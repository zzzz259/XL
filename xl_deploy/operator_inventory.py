"""Small parsers used by the read-only server bootstrap inventory."""

from __future__ import annotations

import re
import shlex
import sys

_SYSTEMD_ARG = re.compile(
    r"argv\[\]=(.*?)(?=;\s*(?:argv\[\]=|ignore_errors=|start_time=)|\s*})",
    re.DOTALL,
)


def extract_systemd_config(exec_start: str) -> str | None:
    """Extract --config from systemd's effective ExecStart argv[] fields."""
    arguments: list[str] = []
    for value in _SYSTEMD_ARG.findall(exec_start):
        try:
            arguments.extend(shlex.split(value))
        except ValueError:
            return None
    for index, argument in enumerate(arguments):
        if argument == "--config":
            return arguments[index + 1] if index + 1 < len(arguments) else None
        if argument.startswith("--config="):
            value = argument.partition("=")[2]
            return value or None
    return None


def main() -> int:
    if sys.argv[1:] != ["--extract-systemd-config"]:
        return 2
    value = extract_systemd_config(sys.stdin.read())
    if value is not None:
        print(value)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
