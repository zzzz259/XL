"""Restricted, byte-verified dynamic inputs for guarded Linux portrait workers.

No package imports, discovery, downloads or host mutation. The descriptor is
trusted deployment input. Large file maps are discarded after verification.
"""

from dataclasses import dataclass
import hashlib
import json
import mmap
import os
from pathlib import Path
import re

SCHEMA = "mesa-llvmpipe-xvfb-invariant/v1"
SELECTION_FIELDS = ("dri_driver", "glx_vendor", "egl_vendor", "egl_vendor_json")
FILE_FIELDS = (*SELECTION_FIELDS, "tree_manifest")
TREE_FIELDS = ("share_tree", "font_tree")
MASKED = ("/usr/share", "/etc", "/tmp", "/usr/bin")
MAX_FILES = 50000
GROUPS = frozenset(("dotnet", "app_files", "native_files", "xvfb", "xkbcomp", "x_libraries", "runtime_files"))


def _path(value):
    if not isinstance(value, str) or len(value) > 4096:
        raise ValueError("dynamic input path bound")
    path = Path(value)
    if not path.is_absolute() or ".." in path.parts or any(ord(c) < 32 for c in value):
        raise ValueError("absolute dynamic input path required")
    if any(path == Path(root) or Path(root) in path.parents for root in MASKED):
        raise ValueError("dynamic input hidden by namespace overlay")
    return path


def _regular(path):
    if any(p.is_symlink() for p in (path, *path.parents)) or not path.is_file():
        raise ValueError("regular immutable dynamic input required")


def file_ref(value, *, verify=False):
    if type(value) is not dict or set(value) != {"path", "sha256"}:
        raise ValueError("invalid dynamic file reference")
    path = _path(value["path"])
    if not isinstance(value["sha256"], str) or not re.fullmatch("[0-9a-f]{64}", value["sha256"]):
        raise ValueError("invalid dynamic file digest")
    if verify:
        _regular(path)
        digest, count = hashlib.sha256(), 0
        with path.open("rb") as stream:
            while chunk := stream.read(1024**2):
                count += len(chunk)
                if count > 512 * 1024**2:
                    raise ValueError("dynamic input byte bound")
                digest.update(chunk)
        if digest.hexdigest() != value["sha256"]:
            raise ValueError("dynamic input hash mismatch")
    return path


def strict_json(raw):
    if len(raw) > 2 * 1024**2:
        raise ValueError("dynamic JSON bound")

    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate dynamic JSON field")
            result[key] = value
        return result

    return json.loads(
        raw, object_pairs_hook=pairs, parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite JSON"))
    )


def _read(path):
    with path.open("rb") as stream:
        raw = stream.read(2 * 1024**2 + 1)
    if len(raw) > 2 * 1024**2:
        raise ValueError("dynamic JSON/data bound")
    return raw


@dataclass(frozen=True)
class DynamicProfile:
    schema: str
    dri_driver: str
    glx_vendor: str
    egl_vendor: str
    egl_vendor_json: str
    share_tree: str
    font_tree: str
    font_mode: str = "directory"

    def __post_init__(self):
        if (
            self.schema != SCHEMA
            or any(
                type(getattr(self, name)) is not str
                for name in ("schema", "font_mode", *SELECTION_FIELDS, *TREE_FIELDS)
            )
            or self.font_mode not in ("directory", "built-ins")
            or (self.font_mode == "built-ins" and self.font_tree)
        ):
            raise ValueError("compact dynamic profile must contain only immutable strings")

    @property
    def font_argument(self):
        return "built-ins" if self.font_mode == "built-ins" else self.font_tree

    def environment(self, work):
        # Owned fresh directories; a stale HOME cannot introduce ~/.drirc.
        folders = {}
        for name in ("home", "cache", "config", "runtime"):
            path = Path(work) / ("profile-" + name)
            path.mkdir(mode=0o700, exist_ok=False)
            folders[name] = str(path)
        return {
            "LIBGL_ALWAYS_SOFTWARE": "1",
            "GALLIUM_DRIVER": "llvmpipe",
            "MESA_LOADER_DRIVER_OVERRIDE": "swrast",
            "LIBGL_DRIVERS_PATH": str(Path(self.dri_driver).parent),
            "__GLX_VENDOR_LIBRARY_NAME": "mesa",
            "__EGL_VENDOR_LIBRARY_FILENAMES": self.egl_vendor_json,
            "MESA_SHADER_CACHE_DISABLE": "true",
            "LP_NUM_THREADS": "1",
            "DOTNET_SYSTEM_GLOBALIZATION_INVARIANT": "1",
            "HOME": folders["home"],
            "XDG_CACHE_HOME": folders["cache"],
            "XDG_CONFIG_HOME": folders["config"],
            "XDG_RUNTIME_DIR": folders["runtime"],
            "LANG": "C",
            "LC_ALL": "C",
            "TZ": "UTC",
        }


def clean_environment(environment):
    prefixes = (
        "GL",
        "LIBGL",
        "MESA",
        "GALLIUM",
        "EGL",
        "__GL",
        "__EGL",
        "DRI_",
        "LP_",
        "DOTNET",
        "COMPlus",
        "LD_",
        "XKB",
        "XFONT",
        "FONTCONFIG",
        "FREETYPE",
        "FC_",
        "XDG_",
    )
    exact = {
        "DISPLAY",
        "WAYLAND_DISPLAY",
        "HOME",
        "LANG",
        "LANGUAGE",
        "TZ",
        "XENVIRONMENT",
        "XAUTHORITY",
        "XLOCALEDIR",
        "XFILESEARCHPATH",
        "XUSERFILESEARCHPATH",
    }
    return {
        key: value for key, value in environment.items() if key not in exact and not key.startswith((*prefixes, "LC_"))
    }


def descriptor_files(native):
    """Mandatory guarded descriptor shape; yield every actual referenced file."""
    if type(native) is not dict or set(native) != GROUPS | {"dynamic_profile"}:
        raise ValueError("guarded descriptor requires dynamic profile")
    for group in sorted(GROUPS):
        rows = [native[group]] if group in ("dotnet", "xvfb", "xkbcomp") else native[group]
        if type(rows) is not list or len(rows) > MAX_FILES:
            raise ValueError("invalid descriptor group")
        for row in rows:
            file_ref(row)
            yield row
    profile = native["dynamic_profile"]
    _shape(profile)
    yield from (profile[key] for key in FILE_FIELDS)
    if profile["font_mode"] == "built-ins":
        yield profile["builtin_font_provider"]
        yield profile["xvfb"]


def _shape(profile):
    if type(profile) is not dict or profile.get("font_mode") not in ("directory", "built-ins"):
        raise ValueError("unsupported font mode")
    font_fields = {"font_tree"} if profile["font_mode"] == "directory" else {"builtin_font_provider", "xvfb"}
    if set(profile) != {"schema", "font_mode", *FILE_FIELDS, "share_tree", *font_fields}:
        raise ValueError("invalid dynamic profile fields")
    if profile["schema"] != SCHEMA:
        raise ValueError("unsupported dynamic profile")
    for field in FILE_FIELDS:
        file_ref(profile[field])
    _path(profile["share_tree"])
    if profile["font_mode"] == "directory":
        _path(profile["font_tree"])
    else:
        file_ref(profile["builtin_font_provider"])
        file_ref(profile["xvfb"])


def verify_profile(profile):
    _shape(profile)
    share = _path(profile["share_tree"])
    font = _path(profile["font_tree"]) if profile["font_mode"] == "directory" else None
    if font is not None and (not font.is_relative_to(share / "fonts") or font == share / "fonts"):
        raise ValueError("fonts must occupy explicit subtree")
    if font is None:
        for field in ("builtin_font_provider", "xvfb"):
            path = file_ref(profile[field], verify=True)
            with path.open("rb") as stream, mmap.mmap(stream.fileno(), 0, access=mmap.ACCESS_READ) as raw:
                if raw[:4] != b"\x7fELF" or raw.find(b"built-ins\0") < 0:
                    raise ValueError("actual built-in font binary evidence required")
    if any(p.is_symlink() for p in (share, *share.parents)) or not share.is_dir():
        raise ValueError("real display share tree required")
    manifest_path = file_ref(profile["tree_manifest"], verify=True)
    if manifest_path.is_relative_to(share):
        raise ValueError("dynamic manifest cannot include itself")
    manifest = strict_json(_read(manifest_path))
    if (
        type(manifest) is not dict
        or set(manifest) != {"schema", "files", "aliases"}
        or type(manifest["schema"]) is not int
        or manifest["schema"] != 1
        or type(manifest["files"]) is not list
        or not 0 < len(manifest["files"]) <= MAX_FILES
        or type(manifest["aliases"]) is not list
        or len(manifest["aliases"]) > MAX_FILES
    ):
        raise ValueError("invalid bounded dynamic tree manifest")

    def relative(name):
        if (
            not isinstance(name, str)
            or not 0 < len(name) <= 512
            or not name.isascii()
            or "\\" in name
            or ":" in name
            or any(ord(c) < 32 for c in name)
            or any(part in ("", ".", "..") for part in name.split("/"))
        ):
            raise ValueError("invalid dynamic relative path")
        return share / name

    refs = {}
    for row in manifest["files"]:
        if type(row) is not dict or set(row) != {"path", "sha256"}:
            raise ValueError("invalid tree member")
        row = dict(row, path=str(relative(row["path"])))
        path = file_ref(row, verify=True)
        if path in refs:
            raise ValueError("duplicate dynamic file")
        refs[path] = row["sha256"]
    for key in SELECTION_FIELDS:
        file_ref(profile[key], verify=True)
    expected_names = {"dri_driver": "swrast_dri.so", "glx_vendor": "libGLX_mesa.so.0", "egl_vendor": "libEGL_mesa.so.0"}
    for key, name in expected_names.items():
        path = file_ref(profile[key])
        with path.open("rb") as stream:
            magic = stream.read(4)
        if path.name != name or magic != b"\x7fELF":
            raise ValueError("fixed Mesa ELF selection required")
    driver = file_ref(profile["dri_driver"])
    with os.scandir(driver.parent) as stream:
        if any(Path(entry.path) != driver for entry in stream):
            raise ValueError("DRI search directory must contain only pinned swrast")
    vendor = strict_json(_read(file_ref(profile["egl_vendor_json"])))
    if vendor != {"file_format_version": "1.0.0", "ICD": {"library_path": "libEGL_mesa.so.0"}}:
        raise ValueError("fixed actual EGL vendor selection required")
    aliases = {}
    for row in manifest["aliases"]:
        if type(row) is not dict or set(row) != {"path", "target"}:
            raise ValueError("invalid dynamic alias")
        path, target = relative(row["path"]), relative(row["target"])
        if (
            path in aliases
            or path == share
            or not path.is_relative_to(share)
            or not target.is_relative_to(share)
            or not path.is_symlink()
        ):
            raise ValueError("dynamic alias outside mounted tree")
        link = path.readlink()
        if link.is_absolute() or Path(os.path.abspath(path.parent / link)) != target:
            raise ValueError("dynamic alias actual hop mismatch")
        aliases[path] = target
    for start in aliases:
        seen, current = set(), start
        for _ in range(32):
            if current in seen:
                raise ValueError("dynamic alias cycle")
            seen.add(current)
            if current not in aliases:
                if current.is_symlink() or not current.is_dir():
                    raise ValueError("dynamic alias target must be real directory")
                break
            current = aliases[current]
        else:
            raise ValueError("dynamic alias hop bound")
    observed = set()
    directories = {parent for path in refs for parent in path.parents}
    for count, path in enumerate(share.rglob("*"), 1):
        if count > MAX_FILES:
            raise ValueError("display tree enumeration bound")
        if path.relative_to(share).parts[0] not in ("X11", "fonts"):
            raise ValueError("unsupported display share resource")
        if path.is_symlink():
            if path not in aliases:
                raise ValueError("unlisted display alias")
        elif path.is_file():
            observed.add(path)
        elif path.is_dir() and path not in directories:
            raise ValueError("unlisted empty display directory")
        elif not path.is_dir():
            raise ValueError("unsupported display resource type")
    if observed != set(refs):
        raise ValueError("dynamic tree file membership mismatch")
    for name in (
        "X11/xkb/rules/base",
        "X11/xkb/keycodes/evdev",
        "X11/xkb/symbols/pc",
        "X11/xkb/types/complete",
        "X11/xkb/compat/complete",
        "X11/xkb/geometry/pc",
        "X11/locale/locale.alias",
    ):
        if share / name not in refs:
            raise ValueError("complete fixed X11 resource recipe required")
    if font is not None:
        fonts_dir = font / "fonts.dir"
        if fonts_dir not in refs:
            raise ValueError("pinned fonts.dir required")
        lines = _read(fonts_dir).decode("ascii").splitlines()
        if not lines or not lines[0].isdigit() or not 0 < int(lines[0]) == len(lines) - 1:
            raise ValueError("invalid pinned font directory")
        for line in lines[1:]:
            name, sep, _ = line.partition(" ")
            if not sep or "/" in name or "\\" in name or name in (".", "..") or font / name not in refs:
                raise ValueError("font selection outside pinned tree")
    return DynamicProfile(
        SCHEMA,
        *(str(file_ref(profile[key])) for key in SELECTION_FIELDS),
        str(share),
        str(font) if font is not None else "",
        profile["font_mode"],
    )


def require_invariant(app_files):
    for stem in ("SpineViewerCLI", "PortraitSceneCLI"):
        matches = [e for e in app_files if Path(e.path).name == stem + ".runtimeconfig.json"]
        if len(matches) != 1:
            raise ValueError("paired invariant runtime configuration required")
        options = strict_json(_read(Path(matches[0].path))).get("runtimeOptions", {})
        if options.get("configProperties", {}).get("System.Globalization.Invariant") is not True:
            raise ValueError("managed globalization must be invariant")


def guarded_runtime(native):
    from .portrait_linux_runtime import LinuxManagedRuntime, RuntimeFile

    # Validate before construction; discard the large file/alias maps here.
    for _ in descriptor_files(native):
        pass
    profile = verify_profile(native["dynamic_profile"])
    runtime = LinuxManagedRuntime(
        **{
            key: RuntimeFile(**native[key])
            if key in ("dotnet", "xvfb", "xkbcomp")
            else tuple(RuntimeFile(**row) for row in native[key])
            for key in GROUPS
        },
        dynamic_profile=profile,
    )
    require_invariant(runtime.app_files)
    return runtime
