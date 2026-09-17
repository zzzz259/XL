import json
import os
import hashlib
import ssl
import time
import urllib.request
from urllib.error import HTTPError
from urllib.error import URLError

from .logger import logger

CDN_BASE = "https://elpis.17995cdn.com"
UPDATE_INFO_URL = f"{CDN_BASE}/Android/UpdateInfo/updateinfo.json"
BUNDLES_URL = f"{CDN_BASE}/Android/Bundles"

HEADERS = {
    "User-Agent": "UnityPlayer/2021.3.45f2c1 (UnityWebRequest/1.0, libcurl/8.5.0-DEV)",
    "X-Unity-Version": "2021.3.45f2c1",
}
SSL_CTX = ssl.create_default_context()
DIRECT_OPENER = urllib.request.build_opener(
    urllib.request.ProxyHandler({}),
    urllib.request.HTTPSHandler(context=SSL_CTX),
)
UPDATE_RETRY_ATTEMPTS = 3
UPDATE_RETRY_DELAYS = (1, 2)


def http_get(url):
    req = urllib.request.Request(url, headers=HEADERS)
    with DIRECT_OPENER.open(req, timeout=15) as resp:
        return resp.read()


def http_head(url):
    req = urllib.request.Request(url, headers=HEADERS)
    req.method = "HEAD"
    try:
        with DIRECT_OPENER.open(req, timeout=5) as resp:
            return True, int(resp.headers.get("Content-Length", 0))
    except HTTPError as e:
        return False, e.code
    except Exception as e:
        return False, str(e)


def _update_http_get(url):
    last_error = None
    for attempt in range(UPDATE_RETRY_ATTEMPTS):
        try:
            return http_get(url)
        except HTTPError:
            raise
        except URLError as error:
            last_error = error
            if attempt >= len(UPDATE_RETRY_DELAYS):
                break
            logger.warning(
                "更新请求失败，将在 %s 秒后重试（%s/%s）：%s",
                UPDATE_RETRY_DELAYS[attempt],
                attempt + 1,
                UPDATE_RETRY_ATTEMPTS,
                error,
            )
            time.sleep(UPDATE_RETRY_DELAYS[attempt])

    reason = getattr(last_error, "reason", last_error)
    if isinstance(reason, ConnectionRefusedError):
        message = "更新检查失败：更新服务器拒绝连接，请检查网络后重试。"
    else:
        message = f"更新检查失败：网络连接异常（{reason}），请检查网络或代理服务后重试。"
    raise RuntimeError(message) from last_error


def check_update():
    raw = _update_http_get(UPDATE_INFO_URL).decode("utf-8")
    update_info = json.loads(raw)
    versions_url = f"{BUNDLES_URL}/{update_info['file']}"
    raw2 = _update_http_get(versions_url).decode("utf-8")
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
