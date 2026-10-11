"""T11/CHAIN11 — security_fetch.safe_fetch SSRF politikası + T10 SOAP boyut sınırı testleri.

Ağ erişimi GEREKMEZ: yerel 127.0.0.1 HTTP sunucusu kullanılır; test süresince yalnız loopback
"public" sayılır (monkeypatch), diğer tüm iç ağ adresleri gerçek politikayla reddedilir.
"""
import asyncio
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
httpx = pytest.importorskip("httpx")
import security_fetch as sf  # noqa: E402


@pytest.mark.parametrize("ip", [
    "127.0.0.1", "10.1.2.3", "172.16.0.5", "192.168.1.1", "169.254.169.254", "100.64.0.1",
    "0.0.0.0", "224.0.0.1", "::1", "fe80::1", "fc00::1", "::ffff:127.0.0.1", "::ffff:169.254.169.254",
    "64:ff9b::a9fe:a9fe", "2002:a00:1::1", "2001::1", "198.18.0.1", "240.0.0.1", "not-an-ip",
])
def test_non_public_addresses_rejected(ip):
    assert sf.is_public_ip(ip) is False


@pytest.mark.parametrize("ip", ["8.8.8.8", "1.1.1.1", "2606:4700:4700::1111", "::ffff:8.8.8.8"])
def test_public_addresses_allowed(ip):
    assert sf.is_public_ip(ip) is True


@pytest.mark.parametrize("url", [
    "ftp://example.com/x", "file:///etc/passwd", "http://user:pw@example.com/", "http://localhost/",
    "http://metadata.google.internal/", "http://169.254.169.254/latest/meta-data/", "http://[::1]/",
    "http://example.com:8080/", "gopher://example.com/", "http:///nohost",
])
def test_url_shape_rejections(url):
    with pytest.raises(sf.UnsafeURLError):
        sf.check_url_shape(url)


def test_safe_fetch_blocks_private_literal_without_network():
    with pytest.raises(sf.UnsafeURLError):
        asyncio.run(sf.safe_fetch("http://10.0.0.1/", max_bytes=1000, timeout=5))


# ── yerel sunucu ile uçtan uca ───────────────────────────────────────────────────────────────

class _H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        p = self.path
        if p == "/ok":
            body = b"hello"
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif p == "/big":
            self.send_response(200)
            self.end_headers()  # Content-Length yok → akış sınırı devreye girmeli
            self.wfile.write(b"x" * 50_000)
        elif p == "/to-metadata":
            self.send_response(302)
            self.send_header("Location", "http://169.254.169.254/latest/meta-data/")
            self.end_headers()
        elif p == "/to-ok":
            self.send_response(301)
            self.send_header("Location", "/ok")
            self.end_headers()
        elif p == "/loop":
            self.send_response(302)
            self.send_header("Location", "/loop")
            self.end_headers()
        else:
            self.send_response(404)
            self.end_headers()


@pytest.fixture
def server(monkeypatch):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _H)
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    real = sf.is_public_ip
    monkeypatch.setattr(sf, "is_public_ip", lambda ip: str(ip) == "127.0.0.1" or real(ip))
    resolved = []

    async def fake_resolve(host, port):
        resolved.append(host)
        if host == "pinned.test":
            return ["127.0.0.1"]
        return await _orig_resolve(host, port)

    _orig_resolve = sf._resolve_public
    monkeypatch.setattr(sf, "_resolve_public", fake_resolve)
    yield f"http://pinned.test:{srv.server_address[1]}", resolved
    srv.shutdown()


def _get(url, **kw):
    kw.setdefault("max_bytes", 10_000)
    kw.setdefault("timeout", 10)
    kw.setdefault("allowed_ports", None)
    return asyncio.run(sf.safe_fetch(url, **kw))


def test_fetch_connects_to_pinned_ip(server):
    base, resolved = server
    r = _get(base + "/ok")
    assert r.status_code == 200 and r.content == b"hello"
    assert "pinned.test" in resolved  # bağlantı pinli çözümleyiciden geçti


def test_byte_cap_enforced_while_streaming(server):
    base, _ = server
    with pytest.raises(sf.FetchTooLarge):
        _get(base + "/big", max_bytes=1000)


def test_redirect_to_metadata_blocked(server):
    base, _ = server
    with pytest.raises(sf.UnsafeURLError):
        _get(base + "/to-metadata")


def test_relative_redirect_followed_and_counted(server):
    base, _ = server
    r = _get(base + "/to-ok")
    assert r.content == b"hello" and r.redirects == 1


def test_redirect_limit(server):
    base, _ = server
    with pytest.raises(sf.UnsafeURLError):
        _get(base + "/loop", allow_redirects=3)


def test_default_ports_only(server):
    base, _ = server
    with pytest.raises(sf.UnsafeURLError):
        asyncio.run(sf.safe_fetch(base + "/ok", max_bytes=100, timeout=5))  # varsayılan: yalnız 80/443


def test_capped_session_rejects_large_soap_response(server):
    pytest.importorskip("requests")
    base, _ = server
    url = base.replace("pinned.test", "127.0.0.1")
    s = sf.make_capped_session(1000)
    assert s.get(url + "/ok", timeout=5).content == b"hello"
    with pytest.raises(sf.ResponseTooLarge):
        s.get(url + "/big", timeout=5)
