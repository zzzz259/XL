import json
import os
import hashlib
import ssl
import urllib.request
from urllib.error import HTTPError

from .logger import logger

CDN_BASE = "https://elpis.17995cdn.com"
UPDATE_INFO_URL = f"{CDN_BASE}/Android/UpdateInfo/updateinfo.json"
BUNDLES_URL = f"{CDN_BASE}/Android/Bundles"

HEADERS = {
    "User-Agent": "UnityPlayer/2021.3.45f2c1 (UnityWebRequest/1.0, libcurl/8.5.0-DEV)",
    "X-Unity-Version": "2021.3.45f2c1",
}
SSL_CTX = ssl.create_default_context()


def http_get(url):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, context=SSL_CTX, timeout=15) as resp:
        return resp.read()


def http_head(url):
    req = urllib.request.Request(url, headers=HEADERS)
    req.method = "HEAD"
    try:
        with urllib.request.urlopen(req, context=SSL_CTX, timeout=5) as resp:
            return True, int(resp.headers.get("Content-Length", 0))
    except HTTPError as e:
        return False, e.code
    except Exception as e:
        return False, str(e)


def check_update():
    raw = http_get(UPDATE_INFO_URL).decode("utf-8")
    update_info = json.loads(raw)
    versions_url = f"{BUNDLES_URL}/{update_info['file']}"
    raw2 = http_get(versions_url).decode("utf-8")
    version_data = json.loads(raw2)
    return update_info, version_data


def verify_bundle(filepath, expected_hash):
    """验证下载的文件 MD5 是否匹配"""
    if not os.path.exists(filepath):
        logger.warning(f"MD5 校验：文件不存在 {filepath}")
        return False
    actual = hashlib.md5(open(filepath, "rb").read()).hexdigest()
    match = actual.lower() == expected_hash.lower()
    logger.debug(f"MD5 校验 {'通过' if match else '失败'}: {os.path.basename(filepath)}")
    return match
