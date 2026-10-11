"""security_fetch.safe_fetch against a REAL local HTTP server (loopback only).

The policy forbids loopback, so tests that need to reach the local server patch `is_public_ip`
for 127.0.0.1 ONLY (per test) and map test host names through a patched resolver.
"""
from __future__ import annotations

import asyncio
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

import security_fetch as sf
from kv_harness import kv_loopback  # noqa: F401


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    seen: list = []

    def log_message(self, *a):  # silence
        pass

    def _send(self, code, body=b"", headers=None, chunked=False):
        self.send_response(code)
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        if chunked:
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            for i in range(0, len(body), 512):
                part = body[i:i + 512]
                self.wfile.write(f"{len(part):x}\r\n".encode() + part + b"\r\n")
            self.wfile.write(b"0\r\n\r\n")
        else:
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    def do_GET(self):
        _Handler.seen.append({"path": self.path, "host": self.headers.get("Host"),
                              "peer": self.client_address[0]})
        p = self.path
        if p == "/ok":
            self._send(200, b"hello-from-local", {"Content-Type": "text/plain"})
        elif p == "/big":
            self._send(200, b"A" * 5000)
        elif p == "/big-chunked":
            self._send(200, b"B" * 5000, chunked=True)
        elif p == "/redir-private-name":
            self._send(302, b"", {"Location": "http://intranet.kv.test/admin"})
        elif p == "/redir-metadata":
            self._send(302, b"", {"Location": "http://169.254.169.254/latest/meta-data/"})
        elif p == "/redir-loop":
            self._send(302, b"", {"Location": "/redir-loop"})
        else:
            self._send(404, b"nf")


@pytest.fixture
def kv_http_server():
    _Handler.seen = []
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    t = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
    t.start()
    yield srv.server_address[1], _Handler.seen
    srv.shutdown()
    srv.server_close()


@pytest.fixture
def kv_dns(monkeypatch):
    """Patched resolver: {hostname: [ip, ...]} (lists are consumed per call when given as callables)."""
    table: dict = {}
    calls: list = []

    def _fake(host, port, *a, **k):
        h = host.decode() if isinstance(host, bytes) else str(host)
        calls.append(h)
        v = table.get(h)
        if callable(v):
            v = v()
        if not v:
            raise socket.gaierror(socket.EAI_NONAME, "unknown host (kv-test)")
        out = []
        for ip in v:
            fam = socket.AF_INET6 if ":" in ip else socket.AF_INET
            sa = (ip, int(port or 0), 0, 0) if fam == socket.AF_INET6 else (ip, int(port or 0))
            out.append((fam, socket.SOCK_STREAM, 6, "", sa))
        return out

    monkeypatch.setattr(socket, "getaddrinfo", _fake)
    return table, calls


@pytest.fixture
def kv_allow_loopback_as_public(monkeypatch):
    real = sf.is_public_ip
    monkeypatch.setattr(sf, "is_public_ip", lambda v: str(v) == "127.0.0.1" or real(v))


def fetch(url, **kw):
    kw.setdefault("max_bytes", 100_000)
    kw.setdefault("timeout", 5)
    kw.setdefault("allowed_ports", None)
    return asyncio.run(sf.safe_fetch(url, **kw))


# ───────────────────────────── policy (no network needed) ─────────────────────────────
@pytest.mark.parametrize("url", [
    "http://127.0.0.1/", "http://127.0.0.1:8080/x", "http://localhost/", "http://[::1]/",
    "http://169.254.169.254/latest/meta-data/", "http://10.0.0.5/", "http://192.168.1.1/",
    "http://172.16.0.1/", "http://100.64.0.1/", "http://0.0.0.0/",
    "http://[::ffff:127.0.0.1]/", "http://[::ffff:10.0.0.1]/", "http://[::ffff:169.254.169.254]/",
    "http://[64:ff9b::a00:1]/", "http://[2002:a00:1::]/", "http://[fe80::1]/",
    "http://metadata.internal/", "file:///etc/passwd", "gopher://example.com/", "http://user:pw@example.com/",
])
def test_unsafe_urls_rejected_without_connecting(url, kv_http_server, kv_loopback):
    _port, seen = kv_http_server
    with pytest.raises(sf.UnsafeURLError):
        fetch(url)
    assert seen == []


def test_default_ports_only(kv_dns):
    table, _ = kv_dns
    table["example.kv.test"] = ["93.184.216.34"]
    with pytest.raises(sf.UnsafeURLError):
        asyncio.run(sf.safe_fetch("http://example.kv.test:8080/", max_bytes=10, timeout=2))


@pytest.mark.parametrize("ip", ["127.0.0.1", "::ffff:10.1.2.3", "::ffff:192.168.0.10", "169.254.169.254",
                                "fd00::1", "64:ff9b::7f00:1"])
def test_is_public_ip_rejects_internal_and_mapped(ip):
    assert sf.is_public_ip(ip) is False


def test_is_public_ip_accepts_public():
    assert sf.is_public_ip("93.184.216.34") is True
    assert sf.is_public_ip("2606:4700:4700::1111") is True


# ───────────────────────────── DNS-based checks ─────────────────────────────
@pytest.mark.parametrize("answers", [["10.0.0.5"], ["192.168.10.10"], ["169.254.169.254"],
                                     ["93.184.216.34", "10.0.0.5"], ["::ffff:10.0.0.1"], ["127.0.0.1"]])
def test_hostname_resolving_to_private_ip_rejected(kv_dns, kv_http_server, kv_loopback, answers):
    table, calls = kv_dns
    port, seen = kv_http_server
    table["evil.kv.test"] = answers
    with pytest.raises(sf.UnsafeURLError):
        fetch(f"http://evil.kv.test:{port}/ok")
    assert "evil.kv.test" in calls
    assert seen == []  # never connected


def test_redirect_to_private_hostname_rejected(kv_dns, kv_http_server, kv_loopback, kv_allow_loopback_as_public):
    table, calls = kv_dns
    port, seen = kv_http_server
    table["pinned.kv.test"] = ["127.0.0.1"]
    table["intranet.kv.test"] = ["10.20.30.40"]
    with pytest.raises(sf.UnsafeURLError):
        fetch(f"http://pinned.kv.test:{port}/redir-private-name")
    assert [s["path"] for s in seen] == ["/redir-private-name"]
    assert "intranet.kv.test" in calls


def test_redirect_to_metadata_ip_rejected(kv_dns, kv_http_server, kv_loopback, kv_allow_loopback_as_public):
    table, _ = kv_dns
    port, seen = kv_http_server
    table["pinned.kv.test"] = ["127.0.0.1"]
    with pytest.raises(sf.UnsafeURLError):
        fetch(f"http://pinned.kv.test:{port}/redir-metadata")
    assert len(seen) == 1


def test_redirect_loop_capped(kv_dns, kv_http_server, kv_loopback, kv_allow_loopback_as_public):
    table, _ = kv_dns
    port, seen = kv_http_server
    table["pinned.kv.test"] = ["127.0.0.1"]
    with pytest.raises(sf.UnsafeURLError):
        fetch(f"http://pinned.kv.test:{port}/redir-loop", allow_redirects=3)
    assert len(seen) == 4


def test_connection_is_pinned_to_validated_ip_and_host_header_kept(kv_dns, kv_http_server, kv_loopback,
                                                                     kv_allow_loopback_as_public):
    table, calls = kv_dns
    port, seen = kv_http_server
    answers = iter([["127.0.0.1"], ["10.66.66.66"], ["10.66.66.66"]])  # DNS-rebinding after first lookup
    table["pinned.kv.test"] = lambda: next(answers)
    res = fetch(f"http://pinned.kv.test:{port}/ok")
    assert res.status_code == 200 and res.content == b"hello-from-local"
    assert calls.count("pinned.kv.test") == 1          # resolved ONCE: validation == connection
    assert seen[0]["peer"] == "127.0.0.1"              # connected to the validated IP
    assert seen[0]["host"] == f"pinned.kv.test:{port}"  # Host header is the name, not the IP


def test_proxy_env_is_ignored(kv_dns, kv_http_server, kv_loopback, kv_allow_loopback_as_public, monkeypatch):
    table, _ = kv_dns
    port, seen = kv_http_server
    table["pinned.kv.test"] = ["127.0.0.1"]
    monkeypatch.setenv("HTTP_PROXY", "http://10.9.9.9:3128")
    monkeypatch.setenv("http_proxy", "http://10.9.9.9:3128")
    res = fetch(f"http://pinned.kv.test:{port}/ok")
    assert res.status_code == 200 and seen[0]["peer"] == "127.0.0.1"


@pytest.mark.parametrize("path", ["/big", "/big-chunked"])
def test_byte_cap_enforced(kv_dns, kv_http_server, kv_loopback, kv_allow_loopback_as_public, path):
    table, _ = kv_dns
    port, _seen = kv_http_server
    table["pinned.kv.test"] = ["127.0.0.1"]
    with pytest.raises(sf.FetchTooLarge):
        fetch(f"http://pinned.kv.test:{port}{path}", max_bytes=1000)
    ok = fetch(f"http://pinned.kv.test:{port}{path}", max_bytes=5000)
    assert len(ok.content) == 5000
