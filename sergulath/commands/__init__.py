"""One module per subcommand. Each defines `NAME`, `HELP`, `add_arguments(parser)` and
`run(args) -> int` (an exit code from `sergulath.output`)."""

from . import caption, overlay, profiles, view, watch

COMMANDS = (watch, overlay, view, caption, profiles)
