"""
security_fetch.py — SSRF-güvenli dış URL indirme + boyut-sınırlı SOAP oturumu (T10/T11, CHAIN11).

NEDEN: Admin panelinde SAKLANAN URL'ler (ürün görselleri, influencer ürün görselleri, beden
tablosu kaynak görseli, Amazon görsel testi …) sunucudan indiriliyordu. `follow_redirects=True`
ve IP denetimi olmadan yapılan bu istekler; iç ağa (10/8, 172.16/12, 192.168/16), loopback'e,
bulut metadata uç noktasına (169.254.169.254), CGNAT'a (100.64/10) ya da yönlendirme zinciriyle
bunlara ulaşmak için kullanılabilirdi (SSRF). Ayrıca yanıt boyutu sınırsızdı (bellek tüketimi).

safe_fetch(url, *, max_bytes, timeout, allow_redirects=3):
  • Yalnız http/https; kullanıcı:parola içeren URL ve 80/443 dışı port reddedilir.
  • Host DNS ile ÇÖZÜLÜR; dönen adreslerden BİRİ bile global/public değilse istek YAPILMAZ
    (ipaddress.is_global False → özel, loopback, link-local/metadata 169.254.x, CGNAT 100.64/10,
    multicast, reserved, unspecified; IPv4-mapped / NAT64 / 6to4 IPv6 içindeki IPv4 de denetlenir,
    Teredo reddedilir).
  • BAĞLANTI PİNLİ: doğrulama ve TCP bağlantısı AYNI çözümleme sonucunu kullanır — httpcore'un
    ağ katmanı (network backend) değiştirilir; bağlantı doğrulanan IP'ye açılır, TLS SNI + sertifika
    doğrulaması ise orijinal HOST adı için yapılır. Böylece "doğrula → yeniden çöz" arasındaki
    DNS-rebinding penceresi KAPALIDIR (her yeni bağlantı, redirect hop'ları dahil, aynı yoldan geçer).
  • Yönlendirmeler ELLE izlenir (en çok allow_redirects hop); her hop'ta şema/port/host yeniden
    denetlenir ve yeni bağlantı yine pinli backend'den geçer.
  • Gövde akış hâlinde okunur; max_bytes aşılınca bağlantı kesilir (FetchTooLarge). Sınır,
    sıkıştırma çözüldükten SONRAKİ bayt sayısına uygulanır (gzip bombasına karşı).
  • Ortam proxy'leri (HTTP(S)_PROXY) KULLANILMAZ (trust_env=False) — aksi halde denetim proxy'nin
    adresine uygulanırdı.
  • Toplam süre `timeout` ile sınırlıdır (asyncio.wait_for).

KALAN RİSK (belgelendi): Pinleme httpx'in iç `_pool` alanının değiştirilmesine dayanır (httpx
0.23–0.28'de kararlı). İleride bu alan kaybolursa _PinnedTransport KURULMAZ ve safe_fetch hata
verir (sessizce pinsiz çalışmaz — fail-closed).

make_capped_session(max_bytes) — zeep/requests için: yanıt gövdesini akış hâlinde okuyup
max_bytes'ı aşan SOAP/WSDL yanıtını reddeden requests.Session (T10: XML boyut sınırı).
"""
from __future__ import annotations

import asyncio
import ipaddress
import socket
from dataclasses import dataclass, field
from typing import Iterable, Optional
from urllib.parse import urlsplit

__all__ = [
    "UnsafeURLError", "FetchTooLarge", "FetchResult", "is_public_ip", "check_url_shape",
    "safe_fetch", "make_capped_session", "ResponseTooLarge",
]

_REDIRECT_CODES = (301, 302, 303, 307, 308)
_DEFAULT_PORTS = (80, 443)
_NAT64_WKP = ipaddress.ip_network("64:ff9b::/96")
_NAT64_LOCAL = ipaddress.ip_network("64:ff9b:1::/48")


class UnsafeURLError(ValueError):
    """URL güvenlik politikasını ihlal ediyor (iç ağ IP'si, şema, port, çok fazla yönlendirme…)."""


class FetchTooLarge(ValueError):
    """Yanıt gövdesi izin verilen bayt sınırını aştı."""


class ResponseTooLarge(IOError):
    """(requests/zeep) SOAP/WSDL yanıtı izin verilen bayt sınırını aştı."""


@dataclass
class FetchResult:
    status_code: int
    content: bytes
    headers: dict = field(default_factory=dict)
    url: str = ""
    redirects: int = 0

    @property
    def content_type(self) -> str:
        return str(self.headers.get("content-type") or "")

    @property
    def ok(self) -> bool:
        return 200 <= self.status_code < 300


# ─────────────────────────────── IP / URL politikası ──────────────────────────────────────

def is_public_ip(value: str) -> bool:
    """Adres internette yönlendirilebilir (global) bir unicast adres mi?"""
    try:
        ip = ipaddress.ip_address(str(value).split("%", 1)[0])
    except ValueError:
        return False
    if ip.version == 6:
        if ip.ipv4_mapped is not None:
            return is_public_ip(str(ip.ipv4_mapped))
        if ip in _NAT64_LOCAL:
            return False
        if ip in _NAT64_WKP:  # 64:ff9b::a.b.c.d → gömülü IPv4'ü denetle (NAT64 ile iç ağa erişim)
            return is_public_ip(str(ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)))
        if ip.sixtofour is not None:
            return is_public_ip(str(ip.sixtofour))
        if ip.teredo is not None:
            return False
    if not ip.is_global:
        return False
    if (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast
            or ip.is_reserved or ip.is_unspecified):
        return False
    return True


def check_url_shape(url: str, *, allowed_schemes: Iterable[str] = ("http", "https"),
                    allowed_ports: Optional[Iterable[int]] = _DEFAULT_PORTS) -> tuple[str, str, int]:
    """DNS'siz biçim denetimi → (scheme, host, port). İhlalde UnsafeURLError."""
    try:
        p = urlsplit(str(url or "").strip())
    except ValueError:
        raise UnsafeURLError("Geçersiz URL")
    scheme = (p.scheme or "").lower()
    if scheme not in set(allowed_schemes):
        raise UnsafeURLError("İzin verilmeyen URL şeması")
    if p.username or p.password:
        raise UnsafeURLError("Kimlik bilgisi içeren URL reddedildi")
    host = (p.hostname or "").strip().rstrip(".")
    if not host:
        raise UnsafeURLError("URL'de host yok")
    try:
        port = p.port or (443 if scheme == "https" else 80)
    except ValueError:
        raise UnsafeURLError("Geçersiz port")
    if allowed_ports is not None and port not in set(allowed_ports):
        raise UnsafeURLError("İzin verilmeyen port")
    low = host.lower()
    if low == "localhost" or low.endswith(".localhost") or low.endswith(".internal") or low.endswith(".local"):
        raise UnsafeURLError("İç ağ host adı reddedildi")
    # IP literal ise DNS'e gitmeden denetle
    try:
        ipaddress.ip_address(host.split("%", 1)[0])
        is_literal = True
    except ValueError:
        is_literal = False
    if is_literal and not is_public_ip(host):
        raise UnsafeURLError("İç ağ/özel IP adresine erişim engellendi (SSRF)")
    return scheme, host, port


async def _resolve_public(host: str, port: int) -> list[str]:
    """Host'u çözer; TÜM adresler public olmalı (biri bile değilse reddedilir)."""
    loop = asyncio.get_running_loop()
    try:
        infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError, OSError):
        raise UnsafeURLError("Host çözümlenemedi")
    ips: list[str] = []
    for info in infos:
        ip = str(info[4][0])
        if not is_public_ip(ip):
            raise UnsafeURLError("İç ağ/özel IP adresine erişim engellendi (SSRF)")
        if ip not in ips:
            ips.append(ip)
    if not ips:
        raise UnsafeURLError("Host çözümlenemedi")
    return ips


# ─────────────────────────────── pinli httpx taşıması ─────────────────────────────────────

def _build_pinned_transport(max_connections: int = 10):
    import ssl
    import httpx
    import httpcore

    class _PinnedBackend(httpcore.AsyncNetworkBackend):
        """connect_tcp: host'u BİR KEZ çöz → public mi denetle → doğrulanan IP'ye bağlan.
        TLS (start_tls) httpcore tarafından orijinal host adıyla (SNI + sertifika) yapılır."""

        def __init__(self):
            self._inner = httpcore.AnyIOBackend()

        async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
            ips = await _resolve_public(str(host), int(port))
            last_exc: Exception | None = None
            for ip in ips:
                try:
                    return await self._inner.connect_tcp(ip, port, timeout=timeout,
                                                         local_address=local_address,
                                                         socket_options=socket_options)
                except Exception as e:  # sıradaki doğrulanmış adresi dene
                    last_exc = e
            raise httpcore.ConnectError(str(last_exc or "bağlantı kurulamadı"))

        async def connect_unix_socket(self, path, timeout=None, socket_options=None):
            raise UnsafeURLError("Unix soket bağlantısı yasak")

        async def sleep(self, seconds):
            await self._inner.sleep(seconds)

    try:
        import certifi
        ssl_ctx = ssl.create_default_context(cafile=certifi.where())
    except Exception:
        ssl_ctx = ssl.create_default_context()

    class _PinnedTransport(httpx.AsyncHTTPTransport):
        def __init__(self):
            super().__init__(verify=ssl_ctx, trust_env=False, http2=False, retries=0)
            if not hasattr(self, "_pool"):
                raise RuntimeError("httpx iç yapısı değişmiş — pinli taşıma kurulamadı (fail-closed)")
            self._pool = httpcore.AsyncConnectionPool(
                ssl_context=ssl_ctx, max_connections=max_connections,
                max_keepalive_connections=max_connections, keepalive_expiry=5.0,
                http1=True, http2=False, retries=0, network_backend=_PinnedBackend())

    return _PinnedTransport()


async def safe_fetch(url: str, *, max_bytes: int, timeout: float, allow_redirects: int = 3,
                     headers: Optional[dict] = None, method: str = "GET",
                     allowed_schemes: Iterable[str] = ("http", "https"),
                     allowed_ports: Optional[Iterable[int]] = _DEFAULT_PORTS) -> FetchResult:
    """SSRF-güvenli indirme. Başarısız istekte (4xx/5xx) de FetchResult döner (status_code ile);
    politika ihlalinde UnsafeURLError, boyut aşımında FetchTooLarge, süre aşımında
    asyncio.TimeoutError fırlatır. Gövde 2xx dışı yanıtlarda da (max_bytes sınırıyla) okunur."""
    import httpx

    max_bytes = int(max_bytes)
    if max_bytes <= 0:
        raise ValueError("max_bytes pozitif olmalı")
    method = (method or "GET").upper()
    if method not in ("GET", "HEAD"):
        raise ValueError("safe_fetch yalnız GET/HEAD destekler")

    async def _run() -> FetchResult:
        cur = str(url or "").strip()
        transport = _build_pinned_transport()
        async with httpx.AsyncClient(transport=transport, follow_redirects=False, trust_env=False,
                                     timeout=httpx.Timeout(timeout)) as client:
            hop = 0
            while True:
                check_url_shape(cur, allowed_schemes=allowed_schemes, allowed_ports=allowed_ports)
                async with client.stream(method, cur, headers=headers or {}) as r:
                    loc = r.headers.get("location")
                    if r.status_code in _REDIRECT_CODES and loc:
                        if hop >= int(allow_redirects):
                            raise UnsafeURLError("Çok fazla yönlendirme")
                        hop += 1
                        cur = str(r.url.join(loc))
                        continue
                    try:
                        cl = int(r.headers.get("content-length") or 0)
                    except ValueError:
                        cl = 0
                    if cl > max_bytes:
                        raise FetchTooLarge("Yanıt çok büyük")
                    buf = bytearray()
                    if method != "HEAD":
                        async for chunk in r.aiter_bytes():
                            buf.extend(chunk)
                            if len(buf) > max_bytes:
                                raise FetchTooLarge("Yanıt çok büyük")
                    return FetchResult(status_code=r.status_code, content=bytes(buf),
                                       headers={k.lower(): v for k, v in r.headers.items()},
                                       url=str(r.url), redirects=hop)

    return await asyncio.wait_for(_run(), timeout=float(timeout))


# ─────────────────────────────── zeep / requests boyut sınırı ─────────────────────────────

def make_capped_session(max_bytes: int, *, verify: bool = True):
    """Yanıt gövdesini akış hâlinde okuyup max_bytes'ı aşınca ResponseTooLarge fırlatan
    requests.Session. zeep Transport(session=...) ile kullanılır; zeep yanıtı `response.content`
    ile okuduğundan davranış şeffaftır (gövde önceden okunup _content'e konur)."""
    import requests

    cap = int(max_bytes)

    class _CappedSession(requests.Session):
        def request(self, method, url, *args, **kwargs):  # noqa: D401
            kwargs["stream"] = True
            resp = super().request(method, url, *args, **kwargs)
            try:
                cl = int(resp.headers.get("Content-Length") or 0)
            except ValueError:
                cl = 0
            if cl > cap:
                resp.close()
                raise ResponseTooLarge(f"SOAP yanıtı çok büyük ({cl} bayt > {cap})")
            buf = bytearray()
            try:
                for chunk in resp.iter_content(chunk_size=65536):
                    if chunk:
                        buf.extend(chunk)
                        if len(buf) > cap:
                            raise ResponseTooLarge(f"SOAP yanıtı çok büyük (> {cap} bayt)")
            except ResponseTooLarge:
                resp.close()
                raise
            resp._content = bytes(buf)
            resp._content_consumed = True
            return resp

    s = _CappedSession()
    s.verify = verify
    return s
