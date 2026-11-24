"""Runs ON the Steam Frame, unprivileged. Proves the part of candidate A that needs no root:
a buffer in the scanout's format (XR24) with the scanout's Qualcomm compressed modifier can be
imported through EGL as a dma-buf and read back as plain RGBA. The buffer is our own (GBM on the
render node); nothing of the system is touched. Prints JSON lines."""
import ctypes
import json
import os
import sys

MOD_QCOM_COMPRESSED = 0x0500000000000001
XR24 = 0x34325258  # DRM_FORMAT_XRGB8888
W, H = 256, 128


def say(**kw):
    print(json.dumps(kw), flush=True)


egl = ctypes.CDLL("libEGL.so.1")
gl = ctypes.CDLL("libOpenGL.so.0")
gbm = ctypes.CDLL("libgbm.so.1")
vp, u32, i32 = ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int
egl.eglGetPlatformDisplay.restype = vp
egl.eglGetPlatformDisplay.argtypes = [ctypes.c_uint, vp, vp]
egl.eglInitialize.argtypes = [vp, vp, vp]
egl.eglQueryString.restype = ctypes.c_char_p
egl.eglQueryString.argtypes = [vp, i32]
egl.eglCreateContext.restype = vp
egl.eglCreateContext.argtypes = [vp, vp, vp, vp]
egl.eglMakeCurrent.argtypes = [vp, vp, vp, vp]
egl.eglGetProcAddress.restype = vp
egl.eglGetProcAddress.argtypes = [ctypes.c_char_p]
egl.eglCreateImage.restype = vp
egl.eglCreateImage.argtypes = [vp, vp, ctypes.c_uint, vp, ctypes.POINTER(ctypes.c_ssize_t)]
egl.eglDestroyImage.argtypes = [vp, vp]

dpy = egl.eglGetPlatformDisplay(0x31DD, None, None)
egl.eglInitialize(dpy, None, None)
ext = (egl.eglQueryString(dpy, 0x3055) or b"").decode().split()
say(step="egl_extensions", dma_buf_import="EGL_EXT_image_dma_buf_import" in ext,
    modifiers="EGL_EXT_image_dma_buf_import_modifiers" in ext, surfaceless="EGL_KHR_surfaceless_context" in ext)
egl.eglBindAPI(0x30A2)
ctx = egl.eglCreateContext(dpy, None, None, None)
egl.eglMakeCurrent(dpy, None, None, ctx)

# Is the scanout's modifier importable for XR24?
query = ctypes.CFUNCTYPE(ctypes.c_bool, vp, i32, i32, ctypes.POINTER(ctypes.c_uint64), ctypes.POINTER(ctypes.c_bool),
                         ctypes.POINTER(i32))(egl.eglGetProcAddress(b"eglQueryDmaBufModifiersEXT"))
n = i32(0)
query(dpy, XR24, 0, None, None, ctypes.byref(n))
mods = (ctypes.c_uint64 * max(n.value, 1))()
external = (ctypes.c_bool * max(n.value, 1))()
query(dpy, XR24, n.value, mods, external, ctypes.byref(n))
listed = {hex(mods[i]): bool(external[i]) for i in range(n.value)}
say(step="modifiers_for_XR24", count=n.value, qcom_compressed_listed=hex(MOD_QCOM_COMPRESSED) in listed,
    qcom_compressed_external_only=listed.get(hex(MOD_QCOM_COMPRESSED)), all=listed)

# Our own buffer with that modifier.
gbm.gbm_create_device.restype = vp
gbm.gbm_create_device.argtypes = [i32]
gbm.gbm_bo_create_with_modifiers2.restype = vp
gbm.gbm_bo_create_with_modifiers2.argtypes = [vp, u32, u32, u32, ctypes.POINTER(ctypes.c_uint64), ctypes.c_uint, u32]
gbm.gbm_bo_get_fd.argtypes = [vp]
gbm.gbm_bo_get_modifier.restype = ctypes.c_uint64
gbm.gbm_bo_get_modifier.argtypes = [vp]
gbm.gbm_bo_get_stride.restype = u32
gbm.gbm_bo_get_stride.argtypes = [vp]
gbm.gbm_bo_get_offset.restype = u32
gbm.gbm_bo_get_offset.argtypes = [vp, i32]
gbm.gbm_bo_destroy.argtypes = [vp]
gbm.gbm_device_destroy.argtypes = [vp]
render = os.open("/dev/dri/renderD128", os.O_RDWR | os.O_CLOEXEC)
dev = gbm.gbm_create_device(render)
want = (ctypes.c_uint64 * 1)(MOD_QCOM_COMPRESSED)
bo = gbm.gbm_bo_create_with_modifiers2(dev, W, H, XR24, want, 1, 0x0004)  # GBM_BO_USE_RENDERING
if not bo:
    say(step="gbm_bo", ok=False, errno=ctypes.get_errno())
    sys.exit(1)
fd = gbm.gbm_bo_get_fd(bo)
mod, stride, offset = gbm.gbm_bo_get_modifier(bo), gbm.gbm_bo_get_stride(bo), gbm.gbm_bo_get_offset(bo, 0)
say(step="gbm_bo", ok=True, modifier=hex(mod), stride=stride, offset=offset)


def import_image():
    a = (ctypes.c_ssize_t * 17)(0x3057, W, 0x3056, H, 0x3271, XR24, 0x3272, fd, 0x3273, offset, 0x3274, stride,
                                0x3443, mod & 0xFFFFFFFF, 0x3444, mod >> 32, 0x3038)
    return egl.eglCreateImage(dpy, None, 0x3270, None, a)  # EGL_LINUX_DMA_BUF_EXT


target_tex = ctypes.CFUNCTYPE(None, u32, vp)(egl.eglGetProcAddress(b"glEGLImageTargetTexture2DOES"))
GL_TEXTURE_2D = 0x0DE1
# 1) Draw a known pattern into the compressed buffer through an FBO.
img_w = import_image()
tex = u32(0)
gl.glGenTextures(1, ctypes.byref(tex))
gl.glBindTexture(GL_TEXTURE_2D, tex.value)
target_tex(GL_TEXTURE_2D, img_w)
fbo = u32(0)
gl.glGenFramebuffers(1, ctypes.byref(fbo))
gl.glBindFramebuffer(0x8D40, fbo.value)
gl.glFramebufferTexture2D(0x8D40, 0x8CE0, GL_TEXTURE_2D, tex.value, 0)
status = gl.glCheckFramebufferStatus(0x8D40)
gl.glEnable(0x0C11)  # scissor: left half red, right half blue
gl.glScissor(0, 0, W // 2, H)
gl.glClearColor(ctypes.c_float(1.0), ctypes.c_float(0.0), ctypes.c_float(0.0), ctypes.c_float(1.0))
gl.glClear(0x4000)
gl.glScissor(W // 2, 0, W // 2, H)
gl.glClearColor(ctypes.c_float(0.0), ctypes.c_float(0.0), ctypes.c_float(1.0), ctypes.c_float(1.0))
gl.glClear(0x4000)
gl.glFinish()
say(step="draw", fbo_complete=status == 0x8CD5, image=bool(img_w), gl_error=gl.glGetError())
# 2) Import the same dma-buf again, as a capture would, and read it back linear.
img_r = import_image()
tex2 = u32(0)
gl.glGenTextures(1, ctypes.byref(tex2))
gl.glBindTexture(GL_TEXTURE_2D, tex2.value)
target_tex(GL_TEXTURE_2D, img_r)
buf = ctypes.create_string_buffer(W * H * 4)
gl.glGetTexImage(GL_TEXTURE_2D, 0, 0x1908, 0x1401, buf)
data = buf.raw
left, right = data[(H // 2 * W + 10) * 4:(H // 2 * W + 10) * 4 + 4], data[(H // 2 * W + W - 10) * 4:(H // 2 * W + W - 10) * 4 + 4]
say(step="read_back", gl_error=gl.glGetError(), left_rgba=list(left), right_rgba=list(right),
    pattern_ok=left[0] > 200 and left[2] < 50 and right[2] > 200 and right[0] < 50)
egl.eglDestroyImage(dpy, img_w)
egl.eglDestroyImage(dpy, img_r)
os.close(fd)
gbm.gbm_bo_destroy(bo)
gbm.gbm_device_destroy(dev)
os.close(render)
