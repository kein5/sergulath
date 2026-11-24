"""Capture a desktop window's client area on Windows with PrintWindow (no extra packages).

Used for SteamVR's VR View, which vrmonitor draws from the compositor's mirror texture: what
it shows includes overlays, but not the screens a standalone headset draws on its own side.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass

from . import host

WINDOWS_ONLY = "it reads desktop windows through Win32 (EnumWindows, PrintWindow)"

user32 = ctypes.WinDLL("user32", use_last_error=True) if hasattr(ctypes, "WinDLL") else None
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True) if hasattr(ctypes, "WinDLL") else None
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True) if hasattr(ctypes, "WinDLL") else None

PW_CLIENTONLY = 0x1
PW_RENDERFULLCONTENT = 0x2  # also captures DirectX content (Windows 8.1 and later)
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


@dataclass
class Window:
    hwnd: int
    pid: int
    process: str
    title: str
    visible: bool
    minimized: bool


def full_process_path(pid: int) -> str:
    """The executable path of a process, or "" when it cannot be read."""
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return ""
    try:
        size = wintypes.DWORD(1024)
        buf = ctypes.create_unicode_buffer(size.value)
        if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return buf.value
        return ""
    finally:
        kernel32.CloseHandle(handle)


def _process_name(pid: int) -> str:
    return full_process_path(pid).rsplit("\\", 1)[-1].lower()


def windows(process: str | None = None) -> list[Window]:
    """Top-level windows, optionally only those of one process (by exe name)."""
    host.require_windows("finding desktop windows", WINDOWS_ONLY)
    found: list[Window] = []
    proto = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def visit(hwnd, _):
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        name = _process_name(pid.value)
        if process and name != process.lower():
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        title = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, title, length + 1)
        found.append(Window(int(hwnd), pid.value, name, title.value,
                            bool(user32.IsWindowVisible(hwnd)), bool(user32.IsIconic(hwnd))))
        return True

    user32.EnumWindows(proto(visit), 0)
    return found


class _BitmapInfoHeader(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
                ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]


def capture_client(hwnd: int) -> tuple[int, int, bytes]:
    """The window's client area as (width, height, RGBA bytes). Works while other windows cover it."""
    host.require_windows("capturing a window", WINDOWS_ONLY)
    # Per-monitor DPI awareness, so a scaled display gives the window's real pixels.
    user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    rect = wintypes.RECT()
    if not user32.GetClientRect(hwnd, ctypes.byref(rect)):
        raise OSError(ctypes.get_last_error(), "GetClientRect failed")
    width, height = rect.right - rect.left, rect.bottom - rect.top
    if width <= 0 or height <= 0:
        raise OSError(0, "the window has no client area (minimized?)")
    window_dc = user32.GetDC(hwnd)
    memory_dc = gdi32.CreateCompatibleDC(window_dc)
    bitmap = gdi32.CreateCompatibleBitmap(window_dc, width, height)
    old = gdi32.SelectObject(memory_dc, bitmap)
    try:
        if not user32.PrintWindow(hwnd, memory_dc, PW_CLIENTONLY | PW_RENDERFULLCONTENT):
            raise OSError(ctypes.get_last_error(), "PrintWindow failed")
        header = _BitmapInfoHeader(ctypes.sizeof(_BitmapInfoHeader), width, -height, 1, 32, 0, 0, 0, 0, 0, 0)
        bgra = (ctypes.c_uint8 * (width * height * 4))()
        if not gdi32.GetDIBits(memory_dc, bitmap, 0, height, bgra, ctypes.byref(header), 0):
            raise OSError(ctypes.get_last_error(), "GetDIBits failed")
    finally:
        gdi32.SelectObject(memory_dc, old)
        gdi32.DeleteObject(bitmap)
        gdi32.DeleteDC(memory_dc)
        user32.ReleaseDC(hwnd, window_dc)
    data = bytearray(bgra)
    data[0::4], data[2::4] = data[2::4], data[0::4]  # BGRA -> RGBA
    data[3::4] = b"\xff" * (width * height)  # GDI leaves alpha undefined
    return width, height, bytes(data)


def is_blank(rgba: bytes, width: int = 0, skip_top_rows: int = 0) -> bool:
    """True when every sampled pixel has the same colour (a black, grey or empty capture).

    `skip_top_rows` (with the image's `width`) leaves out a band at the top, such as the VR View's
    own toolbar, which vrmonitor draws over the mirror even when the mirror is empty.
    """
    start = 4 * width * skip_top_rows if width else 0
    body = rgba[start:]
    step = max(1, len(body) // 4 // 50_000)
    return len(set(body[i:i + 3] for i in range(0, len(body), 4 * (97 * step)))) <= 1


# The VR View's toolbar (menu, "dock", "full screen") covers about the top 6% of the window;
# 10% leaves a margin for display scaling.
VR_VIEW_TOOLBAR_SHARE = 0.10


def vr_view_is_blank(rgba: bytes, width: int, height: int) -> bool:
    """Whether the VR View shows nothing below its toolbar: the empty grey it shows while the
    headset is not tracking, or black."""
    return is_blank(rgba, width, int(height * VR_VIEW_TOOLBAR_SHARE + 0.5))
