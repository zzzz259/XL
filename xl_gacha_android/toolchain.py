"""Resolve official SDK layouts and the previously supported offline layout."""

import os
from pathlib import Path


def executable(directory, name):
    return Path(directory) / (name + ".exe" if os.name == "nt" else name)


def sdk_paths(sdk):
    sdk = Path(sdk)
    platforms = [
        sdk / "platforms/android-35/android.jar",
        sdk / "platform/android-35/android.jar",
    ]
    android = next((p for p in platforms if p.is_file()), None)
    candidates = list((sdk / "build-tools").glob("*"))

    def priority(path):
        parts = path.name.split(".")
        return (
            tuple(int(part) for part in parts)
            if all(p.isdigit() for p in parts)
            else (0,)
        )

    tools = next(
        (
            p
            for p in sorted(candidates, key=priority, reverse=True)
            if (p / "lib/d8.jar").is_file() and executable(p, "aapt2").is_file()
        ),
        None,
    )
    if android is None or tools is None:
        raise ValueError("SDK requires API35 android.jar and build-tools with aapt2/D8")
    return android, tools
