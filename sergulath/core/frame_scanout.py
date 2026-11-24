"""Runs ON a Steam Frame: save what its panel shows (the scanout framebuffer) as PNG.
Started by `sergulath frame shot`, which copies it to a temporary folder there.

Candidate A of the 2026-11-23 frame-shot study. The panel's current framebuffer holds the final
image the headset shows: both eyes side by side, after the lens correction, with everything the
headset draws itself (the outside-the-play-area camera screen included).

Steps, and which need root:
1. Find the plane that scans out to the panel and its current framebuffer (no root).
2. Get a handle to that framebuffer's memory (DRM GETFB2 hands it out only to the DRM master or
   to CAP_SYS_ADMIN: ROOT), and turn it into a dma-buf (PRIME, on the same handle: root).
3. Import the dma-buf through EGL (Qualcomm's compressed layout is decoded by the GPU), read it
   back as RGBA and write a PNG (no root needed for this part, checked on 2026-11-23).

Read-only: nothing is changed on the device; the handle is closed and the PNG goes to
SERGULATH_SAVE_DIR, a temporary folder the caller removes. Without root it stops at step 2 with
exit 3 and says so. Prints JSON lines. Needs python3, libdrm, Mesa's EGL and libOpenGL, all
present on SteamOS for the Frame. See docs/checks/frame-shot.md.
"""
import ctypes
import json
import os
import struct
import sys
import time
import zlib

STARTED = time.monotonic()
SAVE_DIR = os.environ.get("SERGULATH_SAVE_DIR", "")
NAME = os.environ.get("SERGULATH_NAME", "frame-scanout")


def say(**kw):
    print(json.dumps(kw), flush=True)


def write_png(path, w, h, rgba):
    raw = b"".join(b"\x00" + rgba[y * w * 4:(y + 1) * w * 4] for y in range(h))

    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)

    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
                + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))


vp, u32, i32 = ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int


class PlaneRes(ctypes.Structure):
    _fields_ = [("count_planes", u32), ("planes", ctypes.POINTER(u32))]


class Plane(ctypes.Structure):
    _fields_ = [("count_formats", u32), ("formats", ctypes.POINTER(u32)), ("plane_id", u32), ("crtc_id", u32),
                ("fb_id", u32), ("crtc_x", u32), ("crtc_y", u32), ("x", u32), ("y", u32), ("possible_crtcs", u32),
                ("gamma_size", u32)]


class FB2(ctypes.Structure):
    # libdrm's drmModeFB2: the handles come before the pitches and offsets.
    _fields_ = [("fb_id", u32), ("width", u32), ("height", u32), ("pixel_format", u32), ("modifier", ctypes.c_uint64),
                ("flags", u32), ("handles", u32 * 4), ("pitches", u32 * 4), ("offsets", u32 * 4)]


drm = ctypes.CDLL("libdrm.so.2", use_errno=True)
drm.drmModeGetPlaneResources.restype = ctypes.POINTER(PlaneRes)
drm.drmModeGetPlane.restype = ctypes.POINTER(Plane)
drm.drmModeGetPlane.argtypes = [i32, u32]
drm.drmModeGetFB2.restype = ctypes.POINTER(FB2)
drm.drmModeGetFB2.argtypes = [i32, u32]
drm.drmPrimeHandleToFD.argtypes = [i32, u32, u32, ctypes.POINTER(i32)]
drm.drmSetClientCap.argtypes = [i32, ctypes.c_uint64, ctypes.c_uint64]

card = os.open("/dev/dri/card0", os.O_RDWR | os.O_CLOEXEC)
drm.drmSetClientCap(card, 2, 1)  # DRM_CLIENT_CAP_UNIVERSAL_PLANES: list the primary plane too

# 1. The plane on the panel and its framebuffer.
res = drm.drmModeGetPlaneResources(card)
scanout = None
for i in range(res.contents.count_planes):
    p = drm.drmModeGetPlane(card, res.contents.planes[i])
    if p and p.contents.crtc_id and p.contents.fb_id:
        scanout = (p.contents.plane_id, p.contents.crtc_id, p.contents.fb_id)
    drm.drmModeFreePlane(p)
    if scanout:
        break
drm.drmModeFreePlaneResources(res)
if not scanout:
    say(step="plane", ok=False, error="no plane is scanning out (the panel may be off)")
    sys.exit(1)
fbp = drm.drmModeGetFB2(card, scanout[2])
fb = fbp.contents
fourcc = fb.pixel_format.to_bytes(4, "little").decode(errors="replace")
say(step="plane", ok=True, plane=scanout[0], crtc=scanout[1], fb=scanout[2], width=fb.width, height=fb.height,
    format=fourcc, modifier=hex(fb.modifier), root=os.geteuid() == 0)

# 2. Root: a handle to the memory, and a dma-buf from it.
if not fb.handles[0]:
    say(step="handle", ok=False, error="the framebuffer's memory is handed out only to root (CAP_SYS_ADMIN) "
                                       "or the DRM master; run this with sudo")
    sys.exit(3)
prime = i32(-1)
if drm.drmPrimeHandleToFD(card, fb.handles[0], os.O_CLOEXEC, ctypes.byref(prime)):
    say(step="handle", ok=False, error=f"PRIME export failed, errno {ctypes.get_errno()}")
    sys.exit(4)
width, height, pitch, offset, modifier, fmt = fb.width, fb.height, fb.pitches[0], fb.offsets[0], fb.modifier, fb.pixel_format
gem_close = struct.pack("II", fb.handles[0], 0)
drm.drmModeFreeFB2(fbp)
try:
    import fcntl
    fcntl.ioctl(card, 0x40086409, gem_close)  # DRM_IOCTL_GEM_CLOSE: drop our handle; the dma-buf keeps the memory
except OSError:
    pass
say(step="handle", ok=True, ms=round((time.monotonic() - STARTED) * 1000))
read_started = time.monotonic()

# 3. EGL import and read-back (no root needed for this part).
egl = ctypes.CDLL("libEGL.so.1")
gl = ctypes.CDLL("libOpenGL.so.0")
egl.eglGetPlatformDisplay.restype = vp
egl.eglGetPlatformDisplay.argtypes = [ctypes.c_uint, vp, vp]
egl.eglInitialize.argtypes = [vp, vp, vp]
egl.eglCreateContext.restype = vp
egl.eglCreateContext.argtypes = [vp, vp, vp, vp]
egl.eglMakeCurrent.argtypes = [vp, vp, vp, vp]
egl.eglGetProcAddress.restype = vp
egl.eglGetProcAddress.argtypes = [ctypes.c_char_p]
egl.eglCreateImage.restype = vp
egl.eglCreateImage.argtypes = [vp, vp, ctypes.c_uint, vp, ctypes.POINTER(ctypes.c_ssize_t)]
egl.eglDestroyImage.argtypes = [vp, vp]
egl.eglTerminate.argtypes = [vp]
dpy = egl.eglGetPlatformDisplay(0x31DD, None, None)  # surfaceless
egl.eglInitialize(dpy, None, None)
egl.eglBindAPI(0x30A2)
ctx = egl.eglCreateContext(dpy, None, None, None)
egl.eglMakeCurrent(dpy, None, None, ctx)
attrs = (ctypes.c_ssize_t * 17)(0x3057, width, 0x3056, height, 0x3271, fmt, 0x3272, prime.value, 0x3273, offset,
                                0x3274, pitch, 0x3443, modifier & 0xFFFFFFFF, 0x3444, modifier >> 32, 0x3038)
image = egl.eglCreateImage(dpy, None, 0x3270, None, attrs)
if not image:
    say(step="import", ok=False, egl_error=hex(egl.eglGetError()))
    sys.exit(4)
tex = u32(0)
gl.glGenTextures(1, ctypes.byref(tex))
gl.glBindTexture(0x0DE1, tex.value)
ctypes.CFUNCTYPE(None, u32, vp)(egl.eglGetProcAddress(b"glEGLImageTargetTexture2DOES"))(0x0DE1, image)
buf = ctypes.create_string_buffer(width * height * 4)
gl.glGetTexImage(0x0DE1, 0, 0x1908, 0x1401, buf)  # RGBA, unsigned bytes; rows top first for a dma-buf
error = gl.glGetError()
egl.eglDestroyImage(dpy, image)
os.close(prime.value)
data = buf.raw
lit = sum(1 for i in range(0, len(data), 4 * 101) if data[i:i + 3] != b"\x00\x00\x00")
record = {"step": "read", "ok": error == 0, "gl_error": error, "width": width, "height": height,
          "lit_share": round(lit / (len(data) // (4 * 101)), 3),
          "read_ms": round((time.monotonic() - read_started) * 1000)}
if SAVE_DIR and error == 0:
    path = os.path.join(SAVE_DIR, NAME + ".png")
    png_started = time.monotonic()
    write_png(path, width, height, data)
    record["png_ms"] = round((time.monotonic() - png_started) * 1000)
    uid, gid = int(os.environ.get("SUDO_UID", os.getuid())), int(os.environ.get("SUDO_GID", os.getgid()))
    try:
        os.chown(path, uid, gid)  # so the unprivileged user can copy it back and remove it
    except OSError:
        pass
    record["remote_file"] = path
say(**record)
egl.eglTerminate(dpy)
os.close(card)
sys.exit(0 if error == 0 else 4)
