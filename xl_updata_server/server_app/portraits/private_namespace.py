"""Private namespace setup shared by guarded package and render workers."""

import os
import shutil
import subprocess
import time
from pathlib import Path

from .executor import BoundaryError
from .source_prepare import _sha
from .source_worker import _path


def _require_private_namespace(value):
    """The mount namespace must belong to this mapped private user namespace."""
    import fcntl
    if (type(value.get("caller_uid")) is not int or value["caller_uid"] <= 0
            or Path("/proc/self/uid_map").read_text().split() != ["0", str(value["caller_uid"]), "1"]):
        raise BoundaryError("private mapped namespace required")
    mount_fd = os.open("/proc/self/ns/mnt", os.O_RDONLY)
    user_fd = None
    try:
        user_fd = fcntl.ioctl(mount_fd, 0xB701)  # NS_GET_USERNS; no mount mutation.
        actual = os.fstat(user_fd)
        expected = os.stat("/proc/self/ns/user")
        if (actual.st_dev, actual.st_ino) != (expected.st_dev, expected.st_ino):
            raise BoundaryError("mount namespace not owned by private user namespace")
    finally:
        if user_fd is not None:
            os.close(user_fd)
        os.close(mount_fd)


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
    _require_private_namespace(value)
    from app.platform.portrait_dynamic_profile import descriptor_files, verify_profile
    for entry in descriptor_files(native):
        source = Path(entry["path"])
        if source.is_relative_to(attempt) or attempt.is_relative_to(source.parent):
            raise BoundaryError("immutable input overlaps private attempt")
    profile = verify_profile(native["dynamic_profile"])
    for source in (Path(profile.share_tree), *([Path(profile.font_tree)] if profile.font_mode == "directory" else [])):
        if source.is_relative_to(attempt) or attempt.is_relative_to(source):
            raise BoundaryError("display resources overlap private attempt")
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
            ["/usr/bin/mount", "-n", *args],
            check=True,
            timeout=min(5, deadline - time.monotonic()),
            **inherited_lease_kwargs(),
        )
        guard()

    ns = attempt / "ns-tmp"
    ns.mkdir(mode=0o1777)
    ns.chmod(0o1777)
    mount("--make-rprivate", "/")
    # Both destinations already exist. Never mkdir or otherwise write a host
    # mount target. All overlay preparation lives under the owned attempt.
    if any(not Path(target).is_dir() or Path(target).is_symlink() for target in ("/etc", "/usr/share")):
        raise BoundaryError("fixed existing namespace resource targets required")
    empty_etc = attempt / "empty-etc"
    empty_etc.mkdir(mode=0o700)
    for source, target in ((profile.share_tree, "/usr/share"), (str(empty_etc), "/etc")):
        mount("--bind", source, target)
        mount("-o", "remount,bind,ro,nodev,nosuid,noexec", target)
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
