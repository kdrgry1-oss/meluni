"""Denetim #9: Excel görsel indirme DNS-rebinding'e açık değildir — adres safe_fetch'in pinli
taşımasıyla TEK KEZ çözülür ve iç adrese hiç bağlanılmaz; görsel olmayan yanıt işlenmez."""
import asyncio

import security_fetch
import xlsx_images
from security_fetch import FetchResult, UnsafeURLError


def test_private_resolution_never_connected(monkeypatch):
    calls = []

    async def _resolve(host, port):
        calls.append(host)
        raise UnsafeURLError("İç ağ")          # ör. ikinci çözümlemede 169.254.169.254
    monkeypatch.setattr(security_fetch, "_resolve_public", _resolve)
    out = asyncio.run(xlsx_images.fetch_images(["https://rebind.example/a.jpg"]))
    assert out == {}
    assert calls == ["rebind.example"]       # tek çözümleme, pinli bağlantıda


def test_literal_internal_ip_rejected():
    out = asyncio.run(xlsx_images.fetch_images(["http://127.0.0.1/a.png", "http://169.254.169.254/x"]))
    assert out == {}


def test_uses_safe_fetch_and_checks_content_type(monkeypatch):
    seen = []

    async def _fake(url, **kw):
        seen.append((url, kw["max_bytes"], kw["allow_redirects"]))
        ctype = "text/html" if "html" in url else "image/png"
        return FetchResult(status_code=200, content=b"x", headers={"content-type": ctype}, url=url, redirects=0)

    async def _conv(raw, size):
        return b"PNG"
    monkeypatch.setattr(security_fetch, "safe_fetch", _fake)
    monkeypatch.setattr(xlsx_images, "fit_canvas_isolated", _conv)
    out = asyncio.run(xlsx_images.fetch_images(["https://cdn.example/i.png", "https://cdn.example/html"]))
    assert out == {"https://cdn.example/i.png": b"PNG"}
    assert all(m == xlsx_images._MAX_IMAGE_BYTES and r == 3 for _, m, r in seen)
