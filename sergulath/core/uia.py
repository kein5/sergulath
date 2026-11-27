"""Press buttons and check boxes in a desktop app through Windows UI Automation, by their accessible names.

A step looks in the app's main window first and then in its other top-level windows (from the
profile's `[app.other_windows]`; ASTAROT 0.7 shows its settings in a window of their own).

Runs Windows PowerShell's built-in UIAutomationClient, so no Python package is needed. Each
step waits for its control (a button, check box, radio button, tab or text field), then uses the
first pattern it offers: Invoke, Toggle, SelectionItem (select; radio buttons and tabs) or
ExpandCollapse (expand only). Nothing here moves the mouse or takes the foreground.
"""

from __future__ import annotations

import base64
import json
import subprocess
from dataclasses import dataclass

from . import host

WINDOWS_ONLY = "it goes through Windows UI Automation (Windows PowerShell's UIAutomationClient)"

_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes
$req = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($env:SERGULATH_UIA)) | ConvertFrom-Json
$A = [Windows.Automation.AutomationElement]
$ofPid = New-Object Windows.Automation.PropertyCondition($A::ProcessIdProperty, [int]$req.pid)
# The app's top-level windows to search, in order: the main window first, then the others (the
# settings window). Looked up again on every try, as a step can open or close one.
function Get-Windows {
  $found = @()
  $tops = @($A::RootElement.FindAll([Windows.Automation.TreeScope]::Children, $ofPid))
  foreach ($w in $req.windows) {
    foreach ($t in $tops) {
      try { if ($t.Current.Name -eq [string]$w.title) { $found += ,@([string]$w.role, $t) } } catch {}
    }
  }
  return ,$found
}
$results = @()
$main = 0
foreach ($pair in (Get-Windows)) { if ($pair[0] -eq 'main') { $main++ } }
if ($main -eq 0) { @{ ok = $false; error = "window not found"; steps = @() } | ConvertTo-Json -Compress -Depth 4; exit }
$buttons = New-Object Windows.Automation.OrCondition(
  (New-Object Windows.Automation.PropertyCondition($A::ControlTypeProperty, [Windows.Automation.ControlType]::Button)),
  (New-Object Windows.Automation.PropertyCondition($A::ControlTypeProperty, [Windows.Automation.ControlType]::CheckBox)),
  (New-Object Windows.Automation.PropertyCondition($A::ControlTypeProperty, [Windows.Automation.ControlType]::RadioButton)),
  (New-Object Windows.Automation.PropertyCondition($A::ControlTypeProperty, [Windows.Automation.ControlType]::TabItem)),
  (New-Object Windows.Automation.PropertyCondition($A::ControlTypeProperty, [Windows.Automation.ControlType]::Edit)))
function Find-Button($name, $prefix) {
  # The first window (in the order asked) that has the control wins: the main window, then the
  # settings window. A window that closes while it is read is skipped.
  foreach ($pair in (Get-Windows)) {
    try {
      foreach ($b in $pair[1].FindAll([Windows.Automation.TreeScope]::Descendants, $buttons)) {
        $n = $b.Current.Name
        if (($prefix -and $n.StartsWith($name)) -or (-not $prefix -and $n -eq $name)) { return ,@($pair[0], $b) }
      }
    } catch {}
  }
  return $null
}
foreach ($s in $req.steps) {
  $deadline = (Get-Date).AddSeconds([double]$s.timeout)
  $hit = $null
  do { $hit = Find-Button $s.name ([bool]$s.prefix); if (-not $hit) { Start-Sleep -Milliseconds 200 } } while (-not $hit -and (Get-Date) -lt $deadline)
  if (-not $hit) {
    $results += @{ name = $s.name; ok = [bool]$s.optional; done = 'not found' }
    if ($s.optional) { continue } else { break }
  }
  $role = $hit[0]; $el = $hit[1]
  if ($s.check_only) { $results += @{ name = $s.name; ok = $true; done = 'found'; window = $role }; continue }
  $p = $null
  if ($s.value -ne $null) {
    if ($el.TryGetCurrentPattern([Windows.Automation.ValuePattern]::Pattern, [ref]$p)) { $p.SetValue([string]$s.value); $results += @{ name = $s.name; ok = $true; done = 'set'; window = $role } }
    else { $results += @{ name = $s.name; ok = $false; done = 'no value pattern'; window = $role }; break }
    Start-Sleep -Milliseconds ([int]$s.settle_ms); continue
  }
  if ($el.TryGetCurrentPattern([Windows.Automation.InvokePattern]::Pattern, [ref]$p)) { $p.Invoke(); $done = 'invoke' }
  elseif ($el.TryGetCurrentPattern([Windows.Automation.TogglePattern]::Pattern, [ref]$p)) { $p.Toggle(); $done = 'toggle' }
  elseif ($el.TryGetCurrentPattern([Windows.Automation.SelectionItemPattern]::Pattern, [ref]$p)) {
    # Radio buttons and tabs: selecting one that is already selected changes nothing.
    if ($p.Current.IsSelected) { $done = 'already selected' } else { $p.Select(); $done = 'select' }
  }
  elseif ($el.TryGetCurrentPattern([Windows.Automation.ExpandCollapsePattern]::Pattern, [ref]$p)) {
    if ($p.Current.ExpandCollapseState -ne 'Expanded') { $p.Expand(); $done = 'expand' } else { $done = 'already expanded' }
  } else { $results += @{ name = $s.name; ok = $false; done = 'no usable pattern'; window = $role }; break }
  $results += @{ name = $s.name; ok = $true; done = $done; window = $role }
  Start-Sleep -Milliseconds ([int]$s.settle_ms)
}
$ok = ($results.Count -eq $req.steps.Count) -and -not ($results | Where-Object { -not $_.ok })
@{ ok = $ok; steps = $results } | ConvertTo-Json -Compress -Depth 4
"""


@dataclass
class Step:
    name: str
    prefix: bool = False  # match names that start with `name`
    optional: bool = False  # a missing button does not stop the run
    check_only: bool = False  # only wait until the button exists
    value: str | None = None  # set this text in a field (Edit) instead of pressing
    timeout: float = 5.0
    settle_ms: int = 400

    def to_dict(self) -> dict:
        return self.__dict__.copy()


def windows_for(profile_app: dict) -> list[dict]:
    """The app's windows to look for controls in, in order: its main window, then the other
    windows the profile names (`other_windows`, such as ASTAROT 0.7's settings window)."""
    found = [{"title": profile_app["main_window_title"], "role": "main"}]
    for role, titles in profile_app.get("other_windows", {}).items():
        for title in [titles] if isinstance(titles, str) else titles:
            found.append({"title": title, "role": role})
    return found


def press(pid: int, windows: str | list[dict], steps: list[Step]) -> dict:
    """Run the steps in the app's top-level windows; return {ok, steps|error}.

    `windows` is a main window's title, or a list from `windows_for`: each step uses the control
    in the first window that has it, and its result says which window ("window": the role).
    The main window has to be there; the others may open and close as the steps go."""
    host.require_windows("pressing the app's buttons", WINDOWS_ONLY)
    if isinstance(windows, str):
        windows = [{"title": windows, "role": "main"}]
    request = {"pid": pid, "windows": windows, "steps": [s.to_dict() for s in steps]}
    env = {"SERGULATH_UIA": base64.b64encode(json.dumps(request, ensure_ascii=False).encode("utf-8")).decode()}
    import os

    completed = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", _SCRIPT],
        capture_output=True, text=True, encoding="utf-8", env={**os.environ, **env},
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    out = completed.stdout.strip().splitlines()
    if completed.returncode != 0 or not out:
        return {"ok": False, "error": (completed.stderr or "no output").strip()[:500]}
    return json.loads(out[-1])
