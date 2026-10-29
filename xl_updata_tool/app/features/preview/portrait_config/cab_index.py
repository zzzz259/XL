"""Local UnityFS directory-only CAB discovery; never decompress asset payloads."""

from pathlib import Path
import hashlib
import json
import os
import tempfile


def scan_headers(root: Path):
    import UnityPy
    from UnityPy.files.BundleFile import BundleFile
    from UnityPy.streams import EndianBinaryReader
    from UnityPy.enums import ArchiveFlags, ArchiveFlagsOld
    from UnityPy.helpers import ArchiveStorageManager
    from .asset_index import canonical_bundle_hash

    UnityPy.set_assetbundle_decrypt_key(b"yunguihaowan1234")

    class Header(BundleFile):
        def read_fs(self, reader):
            reader.read_long()
            compressed, uncompressed, flags = reader.read_u_int(), reader.read_u_int(), reader.read_u_int()
            if self.signature != "UnityFS":
                reader.read_byte()
            version = self.parse_version()
            old = (
                version < (2020,)
                or version[0] == 2020
                and version < (2020, 3, 34)
                or version[0] == 2021
                and version < (2021, 3, 2)
                or version[0] == 2022
                and version < (2022, 1, 1)
            )
            self.dataflags = (ArchiveFlagsOld if old else ArchiveFlags)(flags)
            if self.dataflags & self.dataflags.UsesAssetBundleEncryption:
                self.decryptor = ArchiveStorageManager.ArchiveStorageDecryptor(reader)
            if self.version >= 7 or version[0] == 2019 and version >= (2019, 4, 15):
                reader.align_stream(16)
            start = reader.Position
            if self.dataflags & ArchiveFlags.BlocksInfoAtTheEnd:
                reader.Position = reader.Length - compressed
            data = reader.read_bytes(compressed)
            info = EndianBinaryReader(self.decompress_data(data, uncompressed, self.dataflags), offset=start)
            info.read_bytes(16)
            for _ in range(info.read_int()):
                info.read_u_int()
                info.read_u_int()
                info.read_u_short()
            paths = []
            for _ in range(info.read_int()):
                info.read_long()
                info.read_long()
                info.read_u_int()
                paths.append(info.read_string_to_null())
            self.node_paths = paths
            return (), None

        def read_files(self, reader, nodes):
            pass

    result = {}
    for path in sorted(root.iterdir()):
        if not path.is_file() or path.name.startswith("_"):
            continue
        try:
            with path.open("rb") as stream:
                header = Header(EndianBinaryReader(stream), None, path.name)
            for name in getattr(header, "node_paths", ()):
                cab = canonical_bundle_hash(name)
                if cab:
                    result.setdefault(cab, []).append(path)
        except Exception:
            # Invalid/unknown headers are not proof that any CAB exists.
            continue
    return {cab: tuple(paths) for cab, paths in result.items()}


def load_headers(root: Path, version: int, cache_path: Path | None):
    files = [(p.name, p.stat().st_size, p.stat().st_mtime_ns) for p in sorted(root.iterdir()) if p.is_file()]
    signature = hashlib.sha256(json.dumps([str(root.resolve()), version, files]).encode()).hexdigest()
    if cache_path and cache_path.is_file():
        try:
            data = json.loads(cache_path.read_text(encoding="utf-8"))
            if data["signature"] == signature:
                return {cab: tuple(root / name for name in names) for cab, names in data["headers"].items()}
        except (ValueError, KeyError, OSError):
            pass
    headers = scan_headers(root)
    if cache_path:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            dir=cache_path.parent, prefix=cache_path.name + ".", suffix=".tmp", delete=False
        ) as stream:
            temp = Path(stream.name)
        temp.write_text(
            json.dumps(
                {
                    "signature": signature,
                    "version": version,
                    "headers": {cab: [p.name for p in paths] for cab, paths in headers.items()},
                }
            ),
            encoding="utf-8",
        )
        os.replace(temp, cache_path)
    return headers
