from urllib.error import URLError

import pytest

from app.platform import downloader


def test_check_update_retries_transient_connection_refusal(monkeypatch):
    calls = []

    def fake_http_get(url):
        calls.append(url)
        if len(calls) < 3:
            raise URLError(ConnectionRefusedError(10061, "connection refused"))
        if url == downloader.UPDATE_INFO_URL:
            return b'{"file": "versions.json"}'
        return b'{"data": []}'

    monkeypatch.setattr(downloader, "http_get", fake_http_get)
    monkeypatch.setattr(downloader.time, "sleep", lambda _seconds: None)

    info, versions = downloader.check_update()

    assert info == {"file": "versions.json"}
    assert versions == {"data": []}
    assert calls == [
        downloader.UPDATE_INFO_URL,
        downloader.UPDATE_INFO_URL,
        downloader.UPDATE_INFO_URL,
        f"{downloader.BUNDLES_URL}/versions.json",
    ]


def test_check_update_explains_connection_refusal(monkeypatch):
    def refused_http_get(_url):
        raise URLError(ConnectionRefusedError(10061, "connection refused"))

    monkeypatch.setattr(downloader, "http_get", refused_http_get)
    monkeypatch.setattr(downloader.time, "sleep", lambda _seconds: None)

    with pytest.raises(RuntimeError, match="更新服务器拒绝连接"):
        downloader.check_update()


def test_http_get_uses_direct_connection(monkeypatch):
    captured = []

    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return b"direct-response"

    def direct_open(request, timeout):
        captured.append((request.full_url, timeout))
        return _Response()

    monkeypatch.setattr(downloader.DIRECT_OPENER, "open", direct_open)

    assert downloader.http_get(downloader.UPDATE_INFO_URL) == b"direct-response"
    assert captured == [(downloader.UPDATE_INFO_URL, 15)]
