"""Long-running stability check (soak): process liveness, memory growth and overlay continuity.

Read-only: reads the target process tree's memory through Windows APIs (ctypes, no third-party
dependencies) and optionally samples OpenVR overlays through an existing SteamVR session.
"""

from __future__ import annotations

import csv
import ctypes
import sys
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from . import overlay as overlay_core

MB = 1024 * 1024
CSV_FIELDS = [
    "time", "sample", "elapsed_s", "pid", "alive", "process_count",
    "main_private_mb", "main_working_set_mb", "tree_private_mb", "tree_working_set_mb",
    "overlay_ok", "overlays",
]


@dataclass
class ProcessMemory:
    pid: int
    alive: bool
    process_count: int
    main_private_mb: float
    main_working_set_mb: float
    tree_private_mb: float
    tree_working_set_mb: float


@dataclass
class SoakSample:
    time: str
    sample: int
    elapsed_s: float
    pid: int
    alive: bool
    process_count: int = 0
    main_private_mb: float = 0.0
    main_working_set_mb: float = 0.0
    tree_private_mb: float = 0.0
    tree_working_set_mb: float = 0.0
    overlay_ok: bool = True
    overlays: list[dict] | None = None

    def to_dict(self) -> dict:
        data = asdict(self)
        if data["overlays"] is None:
            del data["overlays"]
        return data

    def row(self) -> list:
        ov = ""
        if self.overlays is not None:
            ov = "; ".join(
                f"{o['key']}:found={o['found']},visible={o['visible']},alpha={o['alpha']:.2f}"
                for o in self.overlays
            )
        return [
            self.time, self.sample, self.elapsed_s, self.pid, self.alive, self.process_count,
            self.main_private_mb, self.main_working_set_mb, self.tree_private_mb, self.tree_working_set_mb,
            self.overlay_ok, ov,
        ]


@dataclass
class MemoryReport:
    ok: bool
    initial_mb: float = 0.0
    baseline_mb: float = 0.0
    final_mb: float = 0.0
    peak_mb: float = 0.0
    growth_mb: float = 0.0
    slope_mb_per_hour: float = 0.0
    main_growth_mb: float = 0.0
    warmup_samples: int = 0
    evaluated_samples: int = 0
    reason: str = ""


@dataclass
class SoakSummary:
    ok: bool
    alive: bool
    memory_ok: bool
    overlay_ok: bool
    pid: int
    samples: int
    elapsed_s: float
    initial_mb: float = 0.0
    baseline_mb: float = 0.0
    final_mb: float = 0.0
    peak_mb: float = 0.0
    growth_mb: float = 0.0
    slope_mb_per_hour: float = 0.0
    main_growth_mb: float = 0.0
    overlay_failures: int = 0
    reason: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def _win_process_tree_pids(root_pid: int) -> list[int]:
    """Return [root_pid, ...descendants] on Windows, or [] if root_pid is not running."""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    TH32CS_SNAPPROCESS = 0x00000002
    INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
    MAX_PATH = 260

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", ctypes.c_uint32),
            ("cntUsage", ctypes.c_uint32),
            ("th32ProcessID", ctypes.c_uint32),
            ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", ctypes.c_uint32),
            ("cntThreads", ctypes.c_uint32),
            ("th32ParentProcessID", ctypes.c_uint32),
            ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", ctypes.c_uint32),
            ("szExeFile", ctypes.c_wchar * MAX_PATH),
        ]

    kernel32.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
    kernel32.CreateToolhelp32Snapshot.argtypes = [ctypes.c_uint32, ctypes.c_uint32]
    kernel32.Process32FirstW.restype = ctypes.c_int
    kernel32.Process32FirstW.argtypes = [ctypes.c_void_p, ctypes.POINTER(PROCESSENTRY32W)]
    kernel32.Process32NextW.restype = ctypes.c_int
    kernel32.Process32NextW.argtypes = [ctypes.c_void_p, ctypes.POINTER(PROCESSENTRY32W)]
    kernel32.CloseHandle.restype = ctypes.c_int
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]

    snap = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if not snap or snap == INVALID_HANDLE_VALUE:
        return []
    children: dict[int, list[int]] = {}
    present: set[int] = set()
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        if kernel32.Process32FirstW(snap, ctypes.byref(entry)):
            while True:
                pid = int(entry.th32ProcessID)
                ppid = int(entry.th32ParentProcessID)
                present.add(pid)
                children.setdefault(ppid, []).append(pid)
                if not kernel32.Process32NextW(snap, ctypes.byref(entry)):
                    break
    finally:
        kernel32.CloseHandle(snap)

    if root_pid not in present:
        return []
    tree = [root_pid]
    seen = {root_pid}
    stack = [root_pid]
    while stack:
        current = stack.pop()
        for child in children.get(current, ()):
            if child not in seen:
                seen.add(child)
                tree.append(child)
                stack.append(child)
    return tree


def _win_memory_bytes(pid: int) -> tuple[int, int] | None:
    """Return (private_bytes, working_set_bytes) for one running PID on Windows, or None."""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    STILL_ACTIVE = 259

    class PROCESS_MEMORY_COUNTERS_EX(ctypes.Structure):
        _fields_ = [
            ("cb", ctypes.c_uint32),
            ("PageFaultCount", ctypes.c_uint32),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
            ("PrivateUsage", ctypes.c_size_t),
        ]

    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
    kernel32.GetExitCodeProcess.restype = ctypes.c_int
    kernel32.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
    kernel32.K32GetProcessMemoryInfo.restype = ctypes.c_int
    kernel32.K32GetProcessMemoryInfo.argtypes = [ctypes.c_void_p, ctypes.POINTER(PROCESS_MEMORY_COUNTERS_EX), ctypes.c_uint32]
    kernel32.CloseHandle.restype = ctypes.c_int
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]

    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, 0, pid)
    if not handle:
        return None
    try:
        exit_code = ctypes.c_uint32(0)
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)) or exit_code.value != STILL_ACTIVE:
            return None
        counters = PROCESS_MEMORY_COUNTERS_EX()
        counters.cb = ctypes.sizeof(PROCESS_MEMORY_COUNTERS_EX)
        if not kernel32.K32GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
            return None
        return int(counters.PrivateUsage), int(counters.WorkingSetSize)
    finally:
        kernel32.CloseHandle(handle)


def sample_process_tree(root_pid: int) -> ProcessMemory | None:
    """Read memory of `root_pid` and its descendants; None when `root_pid` is no longer alive."""
    if sys.platform != "win32":
        raise RuntimeError("process memory sampling is implemented for Windows")
    pids = _win_process_tree_pids(root_pid)
    if not pids:
        return None
    main = _win_memory_bytes(root_pid)
    if main is None:
        return None
    main_priv, main_ws = main
    tree_priv, tree_ws = main_priv, main_ws
    count = 1
    for child in pids[1:]:
        mem = _win_memory_bytes(child)
        if mem is not None:
            tree_priv += mem[0]
            tree_ws += mem[1]
            count += 1
    return ProcessMemory(
        pid=root_pid,
        alive=True,
        process_count=count,
        main_private_mb=round(main_priv / MB, 2),
        main_working_set_mb=round(main_ws / MB, 2),
        tree_private_mb=round(tree_priv / MB, 2),
        tree_working_set_mb=round(tree_ws / MB, 2),
    )


def linear_slope_mb_per_hour(points: list[tuple[float, float]]) -> float:
    """Ordinary least-squares slope of (elapsed_seconds, memory_mb), returned in MiB/hour."""
    if len(points) < 2:
        return 0.0
    xs = [t / 3600.0 for t, _ in points]
    ys = [m for _, m in points]
    mean_x = sum(xs) / len(xs)
    mean_y = sum(ys) / len(ys)
    denom = sum((x - mean_x) ** 2 for x in xs)
    if denom <= 0.0:
        return 0.0
    numer = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    return round(numer / denom, 2)


def evaluate_memory(
    samples: list[SoakSample],
    warmup_s: float = 120.0,
    max_growth_mb: float = 100.0,
    max_slope_mb_per_hour: float = 120.0,
    min_slope_growth_mb: float = 16.0,
) -> MemoryReport:
    """Check whether process-tree private memory stayed bounded after warmup.

    A slope violation requires at least `min_slope_growth_mb` of net post-warmup growth so a tiny
    1-2 MiB fluctuation over a short run is not extrapolated into a false leak.
    """
    alive_samples = [s for s in samples if s.alive]
    if not alive_samples:
        return MemoryReport(ok=False, reason="no alive samples")
    initial_mb = alive_samples[0].tree_private_mb
    post = [s for s in alive_samples if s.elapsed_s >= warmup_s]
    if not post:
        post = alive_samples
    warmup_count = len(alive_samples) - len(post)
    baseline_mb = post[0].tree_private_mb
    final_mb = post[-1].tree_private_mb
    peak_mb = max(s.tree_private_mb for s in post)
    growth_mb = round(final_mb - baseline_mb, 2)
    main_growth_mb = round(post[-1].main_private_mb - post[0].main_private_mb, 2)
    slope = linear_slope_mb_per_hour([(s.elapsed_s, s.tree_private_mb) for s in post])
    reasons = []
    if growth_mb > max_growth_mb:
        reasons.append(f"memory grew by {growth_mb:.2f} MiB (limit {max_growth_mb:.2f} MiB)")
    if growth_mb >= min_slope_growth_mb and slope > max_slope_mb_per_hour:
        reasons.append(f"memory trend {slope:.2f} MiB/h with +{growth_mb:.2f} MiB growth (limit {max_slope_mb_per_hour:.2f} MiB/h)")
    return MemoryReport(
        ok=not reasons,
        initial_mb=initial_mb,
        baseline_mb=baseline_mb,
        final_mb=final_mb,
        peak_mb=peak_mb,
        growth_mb=growth_mb,
        slope_mb_per_hour=slope,
        main_growth_mb=main_growth_mb,
        warmup_samples=warmup_count,
        evaluated_samples=len(post),
        reason="; ".join(reasons),
    )


def sample_overlays(openvr, keys: list[str], min_alpha: float = 0.0) -> tuple[list[dict], bool]:
    """Read each overlay's presence, visibility and alpha (never reads pixels, so works in standby)."""
    states = overlay_core.capture(openvr, keys, read_pixels=False, read_placement=False)
    records = [
        {"key": s.key, "found": s.found, "visible": s.visible, "alpha": round(s.alpha, 3), "error": s.error}
        for s in states
    ]
    ok = all(s.found and s.visible and s.alpha >= min_alpha for s in states)
    return records, ok


class CsvRecord:
    """Appends soak samples to a CSV file, flushing each round so an interrupted run keeps its rows."""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._file = path.open("w", newline="", encoding="utf-8")
        self._writer = csv.writer(self._file)
        self._writer.writerow(CSV_FIELDS)

    def add(self, sample: SoakSample) -> None:
        self._writer.writerow(sample.row())
        self._file.flush()

    def close(self) -> None:
        self._file.close()


def planned_samples(duration_s: float, interval_s: float) -> int:
    """Number of samples spanning `duration_s` at `interval_s` (inclusive of t=0 and t=duration)."""
    if duration_s <= 0:
        return 1
    return max(2, int(duration_s // interval_s) + 1)


def run_soak(
    pid: int,
    duration_s: float = 1800.0,
    interval_s: float = 60.0,
    warmup_s: float = 120.0,
    max_growth_mb: float = 100.0,
    max_slope_mb_per_hour: float = 120.0,
    overlay_keys: list[str] | None = None,
    min_alpha: float = 0.0,
    read_process: Callable[[int], ProcessMemory | None] | None = None,
    read_overlays: Callable[[list[str], float], tuple[list[dict], bool]] | None = None,
    on_sample: Callable[[SoakSample], None] | None = None,
    sleep: Callable[[float], None] | None = None,
    clock: Callable[[], float] | None = None,
) -> tuple[list[SoakSample], SoakSummary]:
    """Run the soak loop until `count` samples finish or the process exits."""
    read_process = read_process or sample_process_tree
    sleep = sleep or time.sleep
    clock = clock or time.monotonic
    count = planned_samples(duration_s, interval_s)
    started = clock()
    samples: list[SoakSample] = []
    alive = True
    overlay_failures = 0

    for n in range(count):
        now_iso = datetime.now().astimezone().isoformat(timespec="seconds")
        elapsed = round(clock() - started, 1)
        mem = read_process(pid)
        if mem is None or not mem.alive:
            alive = False
            sample = SoakSample(time=now_iso, sample=n, elapsed_s=elapsed, pid=pid, alive=False, overlay_ok=False)
            samples.append(sample)
            if on_sample:
                on_sample(sample)
            break

        ov_records: list[dict] | None = None
        ov_ok = True
        if overlay_keys and read_overlays is not None:
            ov_records, ov_ok = read_overlays(overlay_keys, min_alpha)
            if not ov_ok:
                overlay_failures += 1

        sample = SoakSample(
            time=now_iso,
            sample=n,
            elapsed_s=elapsed,
            pid=pid,
            alive=True,
            process_count=mem.process_count,
            main_private_mb=mem.main_private_mb,
            main_working_set_mb=mem.main_working_set_mb,
            tree_private_mb=mem.tree_private_mb,
            tree_working_set_mb=mem.tree_working_set_mb,
            overlay_ok=ov_ok,
            overlays=ov_records,
        )
        samples.append(sample)
        if on_sample:
            on_sample(sample)

        if n < count - 1:
            sleep(interval_s)

    mem_report = evaluate_memory(samples, warmup_s=warmup_s, max_growth_mb=max_growth_mb,
                                 max_slope_mb_per_hour=max_slope_mb_per_hour)
    overlay_ok = overlay_failures == 0
    reasons = []
    if not alive:
        reasons.append(f"process {pid} exited at sample {samples[-1].sample} ({samples[-1].elapsed_s:.1f} s)")
    if not mem_report.ok and mem_report.reason:
        reasons.append(mem_report.reason)
    if not overlay_ok:
        reasons.append(f"caption overlay check failed in {overlay_failures} of {len(samples)} samples")

    summary = SoakSummary(
        ok=alive and mem_report.ok and overlay_ok,
        alive=alive,
        memory_ok=mem_report.ok,
        overlay_ok=overlay_ok,
        pid=pid,
        samples=len(samples),
        elapsed_s=samples[-1].elapsed_s if samples else 0.0,
        initial_mb=mem_report.initial_mb,
        baseline_mb=mem_report.baseline_mb,
        final_mb=mem_report.final_mb,
        peak_mb=mem_report.peak_mb,
        growth_mb=mem_report.growth_mb,
        slope_mb_per_hour=mem_report.slope_mb_per_hour,
        main_growth_mb=mem_report.main_growth_mb,
        overlay_failures=overlay_failures,
        reason="; ".join(reasons),
    )
    return samples, summary
