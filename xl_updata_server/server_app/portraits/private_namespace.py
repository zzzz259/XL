"""Private namespace setup shared by guarded package and render workers."""

import shutil
import subprocess
import time

from .executor import BoundaryError
from .source_prepare import _sha
from .source_worker import _path


def copy_namespace_tools(tools, shadow):
    if (
        type(tools) is not dict
        or set(tools) != {"/usr/bin/mount", "/usr/bin/sh"}
        or any(_sha(p) != sha for p, sha in tools.items())
    ):
        raise BoundaryError("namespace operational identity mismatch")
    for executable in ("sh", "mount"):
        source = "/usr/bin/" + executable
        shutil.copyfile(source, shadow / executable)
        (shadow / executable).chmod(0o755)
        if _sha(shadow / executable) != tools[source]:
            raise BoundaryError("namespace copy mismatch")


def prepare_namespace(value, native, attempt, guard, deadline):
    tools = value["namespace_tools"]
    if (
        type(tools) is not dict
        or set(tools) != {"/usr/bin/mount", "/usr/bin/sh"}
        or any(_sha(p) != sha for p, sha in tools.items())
    ):
        raise BoundaryError("namespace operational identity mismatch")
    python = _path(value["python"])
    if _sha(python) != value["python_sha256"]:
        raise BoundaryError("namespace Python identity mismatch")
    from app.platform.resource_lease import inherited_lease_kwargs

    def mount(*args):
        guard()
        subprocess.run(
            ["/usr/bin/mount", *args],
            check=True,
            timeout=min(5, deadline - time.monotonic()),
            **inherited_lease_kwargs(),
        )
        guard()

    ns = attempt / "ns-tmp"
    ns.mkdir(mode=0o1777)
    ns.chmod(0o1777)
    mount("--make-rprivate", "/")
    mount("--bind", str(ns), "/tmp")
    shadow = ns / "bin"
    shadow.mkdir(mode=0o700)
    copy_namespace_tools(tools, shadow)
    resolved = python.resolve()
    shutil.copyfile(resolved, shadow / resolved.name)
    (shadow / resolved.name).chmod(0o755)
    if _sha(shadow / resolved.name) != value["python_sha256"]:
        raise BoundaryError("namespace Python copy mismatch")
    if resolved.name != "python3":
        (shadow / "python3").symlink_to(resolved.name)
    xkb = native["xkbcomp"]
    shutil.copyfile(xkb["path"], shadow / "xkbcomp")
    (shadow / "xkbcomp").chmod(0o755)
    if _sha(shadow / "xkbcomp") != xkb["sha256"]:
        raise BoundaryError("namespace xkbcomp identity mismatch")
    mount("--bind", str(shadow), "/usr/bin")
