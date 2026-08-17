import os
import hashlib
from pathlib import Path

from .logger import logger

UNITYFS_MAGIC = b"UnityFS"


class BundleManager:
    def __init__(self, bundles_dir):
        self.bundles_dir = Path(bundles_dir)
        self.bundles_dir.mkdir(parents=True, exist_ok=True)

    def scan_local(self):
        result = []
        if not self.bundles_dir.exists():
            return result
        for f in sorted(self.bundles_dir.iterdir()):
            if f.is_file() and f.suffix in (".json", ".bundle", ""):
                try:
                    info = self._parse_bundle(str(f))
                    result.append({
                        "path": str(f),
                        "name": f.name,
                        "size": f.stat().st_size,
                        "unity_ver": info.get("unity_version", ""),
                        "bundled_files": info.get("files", 0),
                    })
                except Exception:
                    result.append({
                        "path": str(f),
                        "name": f.name,
                        "size": f.stat().st_size,
                        "unity_ver": "",
                        "bundled_files": 0,
                    })
        logger.info(f"扫描本地 bundle：{len(result)} 个文件")
        return result

    def _parse_bundle(self, path):
        try:
            with open(path, "rb") as f:
                header = f.read(200)
            idx = header.find(UNITYFS_MAGIC)
            if idx < 0:
                logger.debug(f"bundle 无 UnityFS 魔数: {path}")
                return {"unity_version": "", "files": 0}
            ver_start = idx + 8
            ver_end = header.find(b"\x00", ver_start)
            version = ""
            if ver_end > ver_start:
                version = header[ver_start:ver_end].decode("ascii", errors="ignore")
            return {"unity_version": version, "files": 0}
        except Exception as e:
            logger.debug(f"解析 bundle 失败: {path}: {e}")
            return {"unity_version": "", "files": 0}

    def extract_file_list(self, path):
        try:
            with open(path, "rb") as f:
                data = f.read(4096)
            idx = data.find(UNITYFS_MAGIC)
            if idx < 0:
                return []
            # UnityFS format beyond header requires full parsing
            return []
        except Exception:
            return []

    def get_size(self, path):
        try:
            return os.path.getsize(path)
        except OSError:
            return 0

    def verify_hash(self, path, expected_hash):
        if not os.path.exists(path):
            return False
        return hashlib.md5(open(path, "rb").read()).hexdigest().lower() == expected_hash.lower()
