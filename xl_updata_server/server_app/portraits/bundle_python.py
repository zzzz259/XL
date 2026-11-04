"""Bounded Python distribution evidence; no imports from supplied packages."""

import ast
import base64
import csv
import hashlib
import io
import re
from email.parser import Parser
from pathlib import Path


def verify_python_metadata(root, python_version, site_roots=None):
    from .bundle import MAX_FILES, MAX_JSON, BundleError, _read_bounded

    def version(text):
        if not re.fullmatch(r"\d+(?:\.\d+)*", text):
            raise BundleError("unknown package version format")
        parts = tuple(map(int, text.split(".")))
        return parts + (0,) * max(0, 3 - len(parts))

    def satisfies(actual, expression):
        actual = version(actual)
        for clause in expression.strip().strip("()").split(","):
            match = re.fullmatch(
                r"\s*(>=|<=|==|!=|~=|>|<)\s*(\d+(?:\.\d+)*)(\.\*)?\s*", clause
            )
            if match is None:
                raise BundleError("unknown package version constraint")
            op, number, wildcard = match.groups()
            expected = version(number)
            if wildcard:
                if op not in ("==", "!="):
                    raise BundleError("invalid wildcard version constraint")
                equal = (
                    actual[: len(number.split("."))]
                    == expected[: len(number.split("."))]
                )
                okay = equal if op == "==" else not equal
            elif op == "~=":
                prefix = list(map(int, number.split(".")))
                upper = prefix[:-1] if len(prefix) > 2 else prefix[:1]
                upper[-1] += 1
                okay = expected <= actual < version(".".join(map(str, upper)))
            else:
                okay = {
                    ">=": actual >= expected,
                    "<=": actual <= expected,
                    ">": actual > expected,
                    "<": actual < expected,
                    "==": actual == expected,
                    "!=": actual != expected,
                }[op]
            if not okay:
                return False
        return True

    environment = {
        "python_version": ".".join(map(str, python_version[:2])),
        "python_full_version": ".".join(map(str, python_version))
        + (".0" if len(python_version) == 2 else ""),
        "sys_platform": "linux",
        "platform_system": "Linux",
        "platform_machine": "x86_64",
        "implementation_name": "cpython",
        "platform_python_implementation": "CPython",
        "os_name": "posix",
        "extra": "",
    }

    def marker(text):
        def read(node):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                return node.value
            if isinstance(node, ast.Name) and node.id in environment:
                return environment[node.id]
            if isinstance(node, ast.BoolOp):
                values = [read(child) for child in node.values]
                if isinstance(node.op, ast.And):
                    return all(values)
                if isinstance(node.op, ast.Or):
                    return any(values)
            if isinstance(node, ast.Compare) and len(node.ops) == 1:
                left, right = read(node.left), read(node.comparators[0])
                op = node.ops[0]
                if isinstance(op, (ast.Lt, ast.LtE, ast.Gt, ast.GtE)):
                    left, right = version(left), version(right)
                if isinstance(op, ast.Eq):
                    return left == right
                if isinstance(op, ast.NotEq):
                    return left != right
                if isinstance(op, ast.Lt):
                    return left < right
                if isinstance(op, ast.LtE):
                    return left <= right
                if isinstance(op, ast.Gt):
                    return left > right
                if isinstance(op, ast.GtE):
                    return left >= right
                if isinstance(op, ast.In):
                    return left in right
                if isinstance(op, ast.NotIn):
                    return left not in right
            raise BundleError("unknown package environment marker")

        try:
            return bool(read(ast.parse(text, mode="eval").body))
        except (SyntaxError, RecursionError) as exc:
            raise BundleError("invalid package environment marker") from exc

    def tag_ok(tag):
        parts = tag.split("-")
        if len(parts) != 3:
            return False
        interpreters, abi, platforms = parts
        interpreter_ok = False
        for name in interpreters.split("."):
            if name == "py3" or name == f"cp{python_version[0]}{python_version[1]}":
                interpreter_ok = True
            match = re.fullmatch(r"(py|cp)3(\d+)", name)
            if (
                match
                and int(match[2]) <= python_version[1]
                and (match[1] == "py" or abi == "abi3")
            ):
                interpreter_ok = True
        if not interpreter_ok or abi not in (
            "none",
            "abi3",
            f"cp{python_version[0]}{python_version[1]}",
        ):
            return False
        for platform in platforms.split("."):
            if platform == "any" and abi == "none":
                return True
            if platform in (
                "linux_x86_64",
                "manylinux1_x86_64",
                "manylinux2010_x86_64",
                "manylinux2014_x86_64",
            ):
                return True
            match = re.fullmatch(r"manylinux_(\d+)_(\d+)_x86_64", platform)
            if match and (int(match[1]), int(match[2])) <= (2, 28):
                return True
        return False

    distributions = {}
    metadata_paths = (
        sorted(
            {
                path.resolve()
                for directory in site_roots
                for path in directory.glob("*.dist-info/METADATA")
            }
        )
        if site_roots is not None
        else list(root.rglob("*.dist-info/METADATA"))
    )
    if len(metadata_paths) > 1024:
        raise BundleError("Python distribution count bound")
    for path in metadata_paths:
        metadata = Parser().parsestr(_read_bounded(path, MAX_JSON).decode("utf-8"))
        name = re.sub(r"[-_.]+", "-", metadata.get("Name", "").lower())
        release = metadata.get("Version", "")
        if not name or name in distributions:
            raise BundleError("ambiguous Python distribution")
        version(release)
        required = metadata.get("Requires-Python")
        if required and not satisfies(environment["python_full_version"], required):
            raise BundleError("Python package interpreter requirement mismatch")
        wheel = Parser().parsestr(
            _read_bounded(path.parent / "WHEEL", MAX_JSON).decode("utf-8")
        )
        if not any(tag_ok(tag) for tag in wheel.get_all("Tag", [])):
            raise BundleError("Python wheel is not compatible with target")
        record = path.parent / "RECORD"
        rows = list(
            csv.reader(io.StringIO(_read_bounded(record, MAX_JSON).decode("utf-8")))
        )
        if not rows or len(rows) > MAX_FILES:
            raise BundleError("Python RECORD count bound")
        seen = set()
        for row in rows:
            if (
                len(row) != 3
                or "\\" in row[0]
                or not row[0]
                or Path(row[0]).is_absolute()
            ):
                raise BundleError("invalid Python RECORD path")
            actual = (path.parent.parent / row[0]).resolve()
            if (
                actual in seen
                or not actual.is_relative_to(root.resolve())
                or not actual.is_file()
            ):
                raise BundleError("missing or escaping Python package file")
            seen.add(actual)
            if actual == record.resolve() and row[1:] == ["", ""]:
                continue
            if (
                not row[1].startswith("sha256=")
                or not row[2].isdigit()
                or actual.stat().st_size != int(row[2])
            ):
                raise BundleError("Python RECORD identity missing")
            digest = hashlib.sha256()
            with actual.open("rb") as stream:
                while block := stream.read(1024 * 1024):
                    digest.update(block)
            if (
                base64.urlsafe_b64encode(digest.digest()).decode().rstrip("=")
                != row[1][7:]
            ):
                raise BundleError("Python RECORD hash mismatch")
        distributions[name] = (release, metadata)
    if distributions.get("unitypy", ("",))[0] != "1.25.0":
        raise BundleError("UnityPy 1.25.0 distribution required")
    for release, metadata in distributions.values():
        for requirement in metadata.get_all("Requires-Dist", []):
            requirement, separator, condition = requirement.partition(";")
            if separator and not marker(condition.strip()):
                continue
            match = re.fullmatch(r"\s*([A-Za-z0-9_.-]+)\s*(.*)", requirement)
            if match is None or "[" in match[2] or "@" in match[2]:
                raise BundleError("unsupported dependency extra or direct reference")
            name = re.sub(r"[-_.]+", "-", match[1].lower())
            if name not in distributions:
                raise BundleError("Python package dependency missing")
            if match[2].strip() and not satisfies(distributions[name][0], match[2]):
                raise BundleError("Python package dependency version mismatch")


def verify_python_frontier(root, search_paths, aliases, manifest):
    """No unpinned lazy import through path hooks, active zip or bytecode."""
    import zipfile

    from .bundle import (
        MAX_JSON,
        BundleError,
        _hash,
        _read_bounded,
        declared_path,
        portable_path,
    )

    archives = {}
    for path in root.rglob("*.pth"):
        for line in _read_bounded(path, MAX_JSON).decode("utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith(("import ", "import\t")):
                raise BundleError("executable Python pth hook forbidden")
            target = declared_path(path.parent / line, aliases)
            if not target.is_relative_to(root) or not target.is_dir():
                raise BundleError("Python pth path escapes verified prefix")
    for path in root.rglob("*._pth"):
        # Isolated _pth overrides the configured PYTHONPATH and venv startup;
        # this deployment contract uses ordinary private venv initialization.
        raise BundleError("isolated Python _pth override is unsupported")
    for value in search_paths:
        path = declared_path(Path(value), aliases)
        if path.is_dir():
            if any(
                (path / name).exists()
                for name in ("sitecustomize.py", "usercustomize.py")
            ):
                raise BundleError("implicit Python customization hook forbidden")
        elif path.is_file():
            if (
                path.suffix != ".zip"
                or str(path) not in manifest
                or path.stat().st_size > 16 * 1024**2
            ):
                raise BundleError("unsupported Python import archive")
            _hash(path, path.stat().st_size, manifest[str(path)])
            with zipfile.ZipFile(path) as stream:
                entries = stream.infolist()
                if (
                    len(entries) > 50000
                    or sum(info.file_size for info in entries) > 512 * 1024**2
                ):
                    raise BundleError("Python archive expansion bound")
                names = set()
                for info in entries:
                    name = portable_path(info.filename.rstrip("/"))
                    if name in names or name.endswith((".pyc", ".pyo")):
                        raise BundleError("ambiguous or compiled Python archive member")
                    names.add(name)
                archives[path] = names
    return archives
