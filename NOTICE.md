# Notices

SERGULATH is under the MIT license (see [LICENSE](LICENSE)).

It does not bundle third-party code. At run time it uses:

| Component | Used by | License |
|---|---|---|
| [pyopenvr](https://github.com/cmbruns/pyopenvr) (`openvr` on PyPI), which ships Valve's `openvr_api.dll` from the [OpenVR SDK](https://github.com/ValveSoftware/openvr) | `watch`, `overlay` | BSD-3-Clause |
| [PyAudioWPatch](https://github.com/s0d3s/PyAudioWPatch), a PortAudio build with WASAPI loopback (optional, `pip install sergulath[audio]`) | `audio` | Apache-2.0 (PortAudio: MIT) |

SteamVR itself is installed and licensed separately; the tools connect to it as a background OpenVR client.
