"""Unit tests. The SteamVR lease (`sergulath.core.lock`) goes to a throwaway folder, so a test run
never touches, or is refused by, the lease of a real session on this PC."""

import atexit
import os
import shutil
import tempfile

if "SERGULATH_STATE_DIR" not in os.environ:
    _state = tempfile.mkdtemp(prefix="sergulath-test-state-")
    os.environ["SERGULATH_STATE_DIR"] = _state
    atexit.register(shutil.rmtree, _state, True)
