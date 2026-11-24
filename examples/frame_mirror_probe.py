"""Runs ON the Steam Frame (python3 -, over SSH). Read-only proof of candidate B: read the
device compositor's mirror texture through OpenVR's GetMirrorTextureGL from a client of our own.
Changes no setting; writes PNGs only into SERGULATH_SAVE_DIR (a mktemp folder removed afterwards).
Prints one JSON line per step."""
import ctypes
import json
import os
import struct
import sys
import time
import zlib

SAVE_DIR = os.environ.get("SERGULATH_SAVE_DIR", "")
LIB = "/opt/steamvr/bin/linuxarm64/libopenvr_api.so"


def say(**kw):
    print(json.dumps(kw), flush=True)


def write_png(path, w, h, rgba, flip=True):
    rows = [rgba[y * w * 4:(y + 1) * w * 4] for y in range(h)]
    if flip:  # GL rows start at the bottom
        rows.reverse()
    raw = b"".join(b"\x00" + r for r in rows)

    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)

    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
                + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))


# ---- EGL surfaceless + desktop GL -------------------------------------------------------------
egl = ctypes.CDLL("libEGL.so.1")
gl = ctypes.CDLL("libOpenGL.so.0")
egl.eglGetPlatformDisplay.restype = ctypes.c_void_p
egl.eglGetPlatformDisplay.argtypes = [ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p]
egl.eglInitialize.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int)]
egl.eglChooseConfig.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_void_p),
                                ctypes.c_int, ctypes.POINTER(ctypes.c_int)]
egl.eglCreateContext.restype = ctypes.c_void_p
egl.eglCreateContext.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_int)]
egl.eglMakeCurrent.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
egl.eglDestroyContext.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
egl.eglTerminate.argtypes = [ctypes.c_void_p]
gl.glGetString.restype = ctypes.c_char_p

dpy = egl.eglGetPlatformDisplay(0x31DD, None, None)  # EGL_PLATFORM_SURFACELESS_MESA
major, minor = ctypes.c_int(), ctypes.c_int()
if not dpy or not egl.eglInitialize(dpy, ctypes.byref(major), ctypes.byref(minor)):
    say(step="egl", ok=False, error=egl.eglGetError())
    sys.exit(4)
egl.eglBindAPI(0x30A2)  # EGL_OPENGL_API
attrs = (ctypes.c_int * 5)(0x3040, 0x0008, 0x3033, 0x0001, 0x3038)  # renderable GL, pbuffer, none
config, count = ctypes.c_void_p(), ctypes.c_int()
egl.eglChooseConfig(dpy, attrs, ctypes.byref(config), 1, ctypes.byref(count))
ctx = egl.eglCreateContext(dpy, config if count.value else None, None, None)
if not ctx or not egl.eglMakeCurrent(dpy, None, None, ctx):
    say(step="egl", ok=False, error=hex(egl.eglGetError()), configs=count.value)
    sys.exit(4)
say(step="egl", ok=True, egl=f"{major.value}.{minor.value}", renderer=(gl.glGetString(0x1F01) or b"").decode(),
    version=(gl.glGetString(0x1F02) or b"").decode())

# ---- OpenVR -----------------------------------------------------------------------------------
lib = ctypes.CDLL(LIB)
lib.VR_InitInternal2.restype = ctypes.c_void_p
lib.VR_InitInternal2.argtypes = [ctypes.POINTER(ctypes.c_int), ctypes.c_int, ctypes.c_char_p]
lib.VR_GetGenericInterface.restype = ctypes.c_void_p
lib.VR_GetGenericInterface.argtypes = [ctypes.c_char_p, ctypes.POINTER(ctypes.c_int)]


class Stats(ctypes.Structure):
    _fields_ = [(n, ctypes.c_uint32) for n in (
        "pid", "presents", "dropped", "reprojected", "presents_startup", "dropped_startup", "reprojected_startup",
        "loading", "presents_loading", "dropped_loading", "reprojected_loading", "timed_out", "presents_timed_out",
        "dropped_timed_out", "reprojected_timed_out", "submits")] + [
        (n, ctypes.c_double) for n in ("cpu_ms", "gpu_ms", "target_ms", "app_cpu_ms", "app_gpu_ms")] + [
        ("frames_with_depth", ctypes.c_uint32)]


def connect(app_type):
    err = ctypes.c_int(0)
    lib.VR_InitInternal2(ctypes.byref(err), app_type, None)
    if err.value:
        return None, err.value
    table = lib.VR_GetGenericInterface(b"FnTable:IVRCompositor_029", ctypes.byref(err))
    return (ctypes.cast(table, ctypes.POINTER(ctypes.c_void_p)) if table else None), err.value


def stats(fns):
    s = Stats()
    ctypes.CFUNCTYPE(None, ctypes.POINTER(Stats), ctypes.c_uint32)(fns[13])(ctypes.byref(s), ctypes.sizeof(Stats))
    return {"presents": s.presents, "dropped": s.dropped, "reprojected": s.reprojected,
            "gpu_ms_sum": round(s.gpu_ms, 1), "cpu_ms_sum": round(s.cpu_ms, 1)}


GL_TEXTURE_2D, GL_RGBA, GL_UNSIGNED_BYTE = 0x0DE1, 0x1908, 0x1401
result = 1
for app_type, name in ((3, "background"), (2, "overlay")):
    fns, err = connect(app_type)
    if not fns:
        say(step="connect", app_type=name, ok=False, error=err)
        lib.VR_ShutdownInternal()
        continue
    get_mirror = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_uint32),
                                  ctypes.POINTER(ctypes.c_void_p))(fns[37])
    release = ctypes.CFUNCTYPE(ctypes.c_bool, ctypes.c_uint32, ctypes.c_void_p)(fns[38])
    lock = ctypes.CFUNCTYPE(None, ctypes.c_void_p)(fns[39])
    unlock = ctypes.CFUNCTYPE(None, ctypes.c_void_p)(fns[40])
    before = stats(fns)
    time.sleep(5)
    baseline = stats(fns)
    say(step="stats_before", app_type=name, first=before, after_5s=baseline)
    got_any = False
    for eye in (0, 1):
        tex, handle = ctypes.c_uint32(0), ctypes.c_void_p(0)
        t0 = time.monotonic()
        e = get_mirror(eye, ctypes.byref(tex), ctypes.byref(handle))
        took = round((time.monotonic() - t0) * 1000, 1)
        if e:
            say(step="mirror", app_type=name, eye=eye, ok=False, compositor_error=e, ms=took)
            continue
        lock(handle)
        gl.glBindTexture(GL_TEXTURE_2D, tex.value)
        w, h = ctypes.c_int(0), ctypes.c_int(0)
        gl.glGetTexLevelParameteriv(GL_TEXTURE_2D, 0, 0x1000, ctypes.byref(w))
        gl.glGetTexLevelParameteriv(GL_TEXTURE_2D, 0, 0x1001, ctypes.byref(h))
        rec = {"step": "mirror", "app_type": name, "eye": eye, "ok": True, "texture": tex.value, "ms": took,
               "width": w.value, "height": h.value, "gl_error": gl.glGetError()}
        if w.value and h.value:
            buf = ctypes.create_string_buffer(w.value * h.value * 4)
            gl.glGetTexImage(GL_TEXTURE_2D, 0, GL_RGBA, GL_UNSIGNED_BYTE, buf)
            rec["gl_error_read"] = gl.glGetError()
            data = buf.raw
            sample = data[0::4 * 97]
            rec["distinct_sampled_values"] = len(set(sample))
            rec["nonzero_px"] = sum(1 for i in range(0, len(data), 4 * 97) if data[i:i + 3] != b"\x00\x00\x00")
            if SAVE_DIR:
                path = os.path.join(SAVE_DIR, f"mirror-eye{eye}.png")
                write_png(path, w.value, h.value, data)
                rec["remote_file"] = path
            got_any = True
        unlock(handle)
        release(tex.value, handle)
        say(**rec)
    time.sleep(5)
    say(step="stats_after", app_type=name, after=stats(fns), note="compare the 5 s windows before and after")
    lib.VR_ShutdownInternal()
    if got_any:
        result = 0
        break
egl.eglMakeCurrent(dpy, None, None, None)
egl.eglDestroyContext(dpy, ctx)
egl.eglTerminate(dpy)
sys.exit(result)
