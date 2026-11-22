"""Runs ON the headset (a standalone SteamVR device such as a Steam Frame), fed to `python3 -`
over SSH by `sergulath frame overlays`. Read-only: connects to the device's own SteamVR as a
background OpenVR client through libopenvr_api's flat function tables (no packages needed),
prints one JSON line per overlay, and with SAVE_DIR set writes drawn raw-image overlays as PNG.

GetOverlayImageData only returns overlays set from raw pixels; overlays drawn from GPU
textures (the camera view, the headset view, Steam's UI) answer error 24 (InvalidTexture).
"""
import ctypes
import json
import os
import struct
import sys
import zlib

LIB = os.environ.get("SERGULATH_OPENVR_LIB", "/opt/steamvr/bin/linuxarm64/libopenvr_api.so")
KEYS = [k for k in os.environ.get("SERGULATH_KEYS", "").split(",") if k]
SAVE_DIR = os.environ.get("SERGULATH_SAVE_DIR", "")
IVROVERLAY = b"FnTable:IVROverlay_028"
# Slots in the IVROverlay_028 function table (pyopenvr 2.12 order).
FIND, IMAGE, ALPHA, VISIBLE = 0, 7, 17, 45

lib = ctypes.CDLL(LIB)
lib.VR_InitInternal2.restype = ctypes.c_void_p
lib.VR_InitInternal2.argtypes = [ctypes.POINTER(ctypes.c_int), ctypes.c_int, ctypes.c_char_p]
lib.VR_GetGenericInterface.restype = ctypes.c_void_p
lib.VR_GetGenericInterface.argtypes = [ctypes.c_char_p, ctypes.POINTER(ctypes.c_int)]
err = ctypes.c_int(0)
lib.VR_InitInternal2(ctypes.byref(err), 3, None)  # VRApplication_Background: cannot start SteamVR
if err.value:
    print(json.dumps({"init_error": err.value}))
    sys.exit(3)
table = lib.VR_GetGenericInterface(IVROVERLAY, ctypes.byref(err))
if not table:
    print(json.dumps({"interface_error": err.value}))
    sys.exit(4)
fns = ctypes.cast(table, ctypes.POINTER(ctypes.c_void_p))
H = ctypes.c_uint64
find = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_char_p, ctypes.POINTER(H))(fns[FIND])
image = ctypes.CFUNCTYPE(ctypes.c_int, H, ctypes.c_void_p, ctypes.c_uint32,
                         ctypes.POINTER(ctypes.c_uint32), ctypes.POINTER(ctypes.c_uint32))(fns[IMAGE])
alpha_fn = ctypes.CFUNCTYPE(ctypes.c_int, H, ctypes.POINTER(ctypes.c_float))(fns[ALPHA])
visible_fn = ctypes.CFUNCTYPE(ctypes.c_bool, H)(fns[VISIBLE])


def write_png(path, w, h, rgba):
    raw = b"".join(b"\x00" + rgba[y * w * 4:(y + 1) * w * 4] for y in range(h))

    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)

    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
                + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


size = 4096 * 4096 * 4
buf = ctypes.create_string_buffer(size)
for key in KEYS:
    handle = H(0)
    e = find(key.encode(), ctypes.byref(handle))
    if e:
        print(json.dumps({"key": key, "found": False, "error": e}))
        continue
    a = ctypes.c_float(0)
    alpha_fn(handle, ctypes.byref(a))
    w, h = ctypes.c_uint32(0), ctypes.c_uint32(0)
    e = image(handle, buf, size, ctypes.byref(w), ctypes.byref(h))
    rec = {"key": key, "found": True, "visible": bool(visible_fn(handle)), "alpha": round(a.value, 2),
           "image_error": e, "width": w.value, "height": h.value, "drawn_px": 0}
    if not e and w.value:
        data = buf.raw[: w.value * h.value * 4]
        rec["drawn_px"] = len(data[3::4]) - data[3::4].count(0)
        if SAVE_DIR and rec["drawn_px"]:
            path = os.path.join(SAVE_DIR, key + ".png")
            write_png(path, w.value, h.value, data)
            rec["remote_file"] = path
    print(json.dumps(rec), flush=True)
lib.VR_ShutdownInternal()
