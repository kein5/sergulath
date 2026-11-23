"""One module per subcommand. Each defines `NAME`, `HELP`, `add_arguments(parser)` and
`run(args) -> int` (an exit code from `sergulath.output`)."""

from . import caption, controller, driver, frame, hmd, logs, overlay, profiles, settings, steamvr, view, watch

COMMANDS = (steamvr, settings, watch, overlay, view, caption, logs, frame, hmd, driver, controller, profiles)
