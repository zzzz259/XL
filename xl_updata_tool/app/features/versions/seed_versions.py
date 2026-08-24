"""内置版本种子注册，不依赖 Qt。"""

import os
import ssl
import urllib.request

from app.platform import database as db
from app.platform.bundle_parser import extract_manifest_hashes
from app.platform.diagnostics import logger


def seed_bundled_versions(version_manager, bundles_dir: str) -> None:
    """注册内置版本及其 Bundle 清单；已有版本不会重复写入。"""
    existing = {row[0] for row in db.get_all_versions()}
    logger.info(f"写入种子版本：已存在 {len(existing)} 个版本")
    context = ssl.create_default_context()
    seed_dir = os.path.join(bundles_dir, "seeds")
    headers = {
        "User-Agent": "UnityPlayer/2021.3.45f2c1 (UnityWebRequest/1.0, libcurl/8.5.0-DEV)",
        "X-Unity-Version": "2021.3.45f2c1",
    }

    def download_catalog(name, file_hash):
        path = os.path.join(seed_dir, f"SEED_{name}_{file_hash[:12]}.json")
        if not os.path.exists(path):
            os.makedirs(seed_dir, exist_ok=True)
            request = urllib.request.Request(
                f"https://elpis.17995cdn.com/Android/Bundles/{name.lower()}_{file_hash}.json",
                headers=headers,
            )
            data = urllib.request.urlopen(request, context=context, timeout=15).read()
            with open(path, "wb") as handle:
                handle.write(data)
        return path

    def seed(timestamp, info, versions_data, notes, categories, is_current=False):
        if timestamp in existing:
            logger.debug(f"种子版本 {timestamp} 已存在，跳过")
            return
        version_manager.register_version(timestamp, info, versions_data, is_current=is_current)
        db.add_notes(timestamp, notes)
        asset_hashes = set()
        for name, file_hash in categories:
            try:
                asset_hashes |= extract_manifest_hashes(download_catalog(name, file_hash))
            except Exception as exc:
                logger.warning(f"读取种子清单失败 {name}/{file_hash}: {exc}")
        if asset_hashes:
            db.save_sub_bundles(timestamp, list(asset_hashes))
        logger.info(f"种子版本 {timestamp}：注册完成，{len(asset_hashes)} 个 bundle")

    seed(
        134091181056097516,
        {"timestamp": 134091181056097516, "file": "versions_vA137.D342.O142.V6_134091181056097516.json", "hash": "", "size": 0, "downloadURL": "https://elpis.17995cdn.com/Android/Bundles", "playerURL": "https://xl.haoplay.com.cn/dl", "latestVersion": "1.0", "minVersion": "1.0"},
        {"timestamp": 134091181056097516, "data": [{"name": "Arts", "hash": "80d80e718768bdd7b35f5b9406d624ed", "size": 781708, "ver": 137}, {"name": "Data", "hash": "48002d6e908b3fce2c4b61d8c34b9393", "size": 158685, "ver": 342}, {"name": "Other", "hash": "fdf0f3b9cad9498a25f542c4c9ce0f8b", "size": 129635, "ver": 142}, {"name": "Video", "hash": "a7fd12e3f78b95bcfb6c63be49247b5f", "size": 2738, "ver": 6}]},
        "APK内置版本, 2025年12月",
        [("Arts", "80d80e718768bdd7b35f5b9406d624ed"), ("Data", "48002d6e908b3fce2c4b61d8c34b9393"), ("Other", "fdf0f3b9cad9498a25f542c4c9ce0f8b")],
    )
    seed(
        134239138473475084,
        {"timestamp": 134239138473475084, "file": "versions_vA145.D378.O152.V6_134239138473475084.json", "hash": "", "size": 0, "downloadURL": "https://elpis.17995cdn.com/Android/Bundles", "playerURL": "https://xl.haoplay.com.cn/dl", "latestVersion": "1.1", "minVersion": "1.1"},
        {"timestamp": 134239138473475084, "data": [{"name": "Arts", "hash": "a1c72aae7f79b72bc763e63803362d01", "size": 836280, "ver": 145}, {"name": "Data", "hash": "7bbfa67db42e024cb7124dddb8b91d2b", "size": 180385, "ver": 378}, {"name": "Other", "hash": "d3632702f53de4ba0457b5e518aaf0f3", "size": 139791, "ver": 152}, {"name": "Video", "hash": "01fe1717b1979689c37f26f6312ea23b", "size": 2738, "ver": 6}]},
        "完整版本, 2026年5月26日",
        [("Arts", "a1c72aae7f79b72bc763e63803362d01"), ("Data", "7bbfa67db42e024cb7124dddb8b91d2b"), ("Other", "d3632702f53de4ba0457b5e518aaf0f3")],
    )
    seed(
        134272123703055311,
        {"timestamp": 134272123703055311, "file": "versions_vA152.D386.O156.V6_134272123703055311.json", "hash": "bb7d22dab4acab771f336ce53f6af261", "size": 367, "downloadURL": "https://elpis.17995cdn.com/Android/Bundles", "playerURL": "https://xl.haoplay.com.cn/dl", "latestVersion": "1.2", "minVersion": "1.2"},
        {"timestamp": 134272123703055311, "data": [{"name": "Arts", "hash": "30ed8244781b44cacc3c5d1ea10a976e", "size": 844382, "ver": 152}, {"name": "Data", "hash": "5d2c794364dfe22100547764f60689fe", "size": 185983, "ver": 386}, {"name": "Other", "hash": "dd50845a464e1eda86b027103d2cce43", "size": 146765, "ver": 156}, {"name": "Video", "hash": "ac0fb4b1842c88408eee708a5f394a8b", "size": 2744, "ver": 6}]},
        "在线版本, 2026年6月29日",
        [("Arts", "30ed8244781b44cacc3c5d1ea10a976e"), ("Data", "5d2c794364dfe22100547764f60689fe"), ("Other", "dd50845a464e1eda86b027103d2cce43")],
    )
