"""One module per subcommand. Each defines `NAME`, `HELP`, `add_arguments(parser)` and
`run(args) -> int` (an exit code from `sergulath.output`)."""

from . import caption, controller, driver, frame, overlay, profiles, steamvr, view, watch

COMMANDS = (steamvr, watch, overlay, view, caption, frame, driver, controller, profiles)
