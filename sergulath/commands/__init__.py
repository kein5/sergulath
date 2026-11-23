"""One module per subcommand. Each defines `NAME`, `HELP`, `add_arguments(parser)` and
`run(args) -> int` (an exit code from `sergulath.output`)."""

from . import app, asr, audio, caption, controller, driver, frame, hmd, logs, overlay, profiles, settings, steamvr, suite, tts, view, watch

COMMANDS = (steamvr, app, settings, watch, overlay, view, caption, logs, frame, hmd, driver, controller, asr, tts, audio, suite, profiles)
