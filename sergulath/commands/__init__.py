"""One module per subcommand. Each defines `NAME`, `HELP`, `add_arguments(parser)` and
`run(args) -> int` (an exit code from `sergulath.output`)."""

from . import app, asr, caption, controller, driver, frame, hmd, logs, overlay, profiles, settings, soak, steamvr, suite, upgrade, view, watch

COMMANDS = (steamvr, app, settings, watch, overlay, view, caption, logs, frame, hmd, driver, controller, asr, soak, upgrade, suite, profiles)
