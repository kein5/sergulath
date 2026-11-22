"""Press buttons in a desktop app through Windows UI Automation, by their accessible names.

Runs Windows PowerShell's built-in UIAutomationClient, so no Python package is needed. Each
step waits for its button, then uses the first pattern the button offers: Invoke, Toggle or
ExpandCollapse (expand only). Nothing here moves the mouse or takes the foreground.
"""

from __future__ import annotations

import base64
import json
import subprocess
from dataclasses import dataclass

_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes
$req = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($env:SERGULATH_UIA)) | ConvertFrom-Json
$A = [Windows.Automation.AutomationElement]
$cond = New-Object Windows.Automation.AndCondition(
  (New-Object Windows.Automation.PropertyCondition($A::ProcessIdProperty, [int]$req.pid)),
  (New-Object Windows.Automation.PropertyCondition($A::NameProperty, [string]$req.window)))
$win = $A::RootElement.FindFirst([Windows.Automation.TreeScope]::Children, $cond)
$results = @()
if (-not $win) { @{ ok = $false; error = "window not found"; steps = @() } | ConvertTo-Json -Compress -Depth 4; exit }
$buttons = New-Object Windows.Automation.PropertyCondition($A::ControlTypeProperty, [Windows.Automation.ControlType]::Button)
function Find-Button($name, $prefix) {
  foreach ($b in $win.FindAll([Windows.Automation.TreeScope]::Descendants, $buttons)) {
    $n = $b.Current.Name
    if (($prefix -and $n.StartsWith($name)) -or (-not $prefix -and $n -eq $name)) { return $b }
  }
  return $null
}
foreach ($s in $req.steps) {
  $deadline = (Get-Date).AddSeconds([double]$s.timeout)
  $el = $null
  do { $el = Find-Button $s.name ([bool]$s.prefix); if (-not $el) { Start-Sleep -Milliseconds 200 } } while (-not $el -and (Get-Date) -lt $deadline)
  if (-not $el) {
    $results += @{ name = $s.name; ok = [bool]$s.optional; done = 'not found' }
    if ($s.optional) { continue } else { break }
  }
  if ($s.check_only) { $results += @{ name = $s.name; ok = $true; done = 'found' }; continue }
  $p = $null
  if ($el.TryGetCurrentPattern([Windows.Automation.InvokePattern]::Pattern, [ref]$p)) { $p.Invoke(); $done = 'invoke' }
  elseif ($el.TryGetCurrentPattern([Windows.Automation.TogglePattern]::Pattern, [ref]$p)) { $p.Toggle(); $done = 'toggle' }
  elseif ($el.TryGetCurrentPattern([Windows.Automation.ExpandCollapsePattern]::Pattern, [ref]$p)) {
    if ($p.Current.ExpandCollapseState -ne 'Expanded') { $p.Expand(); $done = 'expand' } else { $done = 'already expanded' }
  } else { $results += @{ name = $s.name; ok = $false; done = 'no usable pattern' }; break }
  $results += @{ name = $s.name; ok = $true; done = $done }
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
    timeout: float = 5.0
    settle_ms: int = 400

    def to_dict(self) -> dict:
        return self.__dict__.copy()


def press(pid: int, window_title: str, steps: list[Step]) -> dict:
    """Run the steps in the app's top-level window `window_title`; return {ok, steps|error}."""
    request = {"pid": pid, "window": window_title, "steps": [s.to_dict() for s in steps]}
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
