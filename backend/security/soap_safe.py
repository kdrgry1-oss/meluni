"""SOAP (zeep) istemcileri için güvenli taşıma: WSDL'in içe aktardığı şemalar (xsd=1 …) YALNIZ WSDL ile
AYNI https sunucusundan indirilebilir.

Neden: zeep'in yeni sürümünde `Settings(forbid_external=True)` WSDL'in kendi şema içe aktarmalarını da
(ör. https://connector.doganedonusum.com/AuthenticationWS?xsd=1) yasaklıyor → Doğan girişi / fatura
sorgusu "ExternalReferenceForbidden" ile düşüyordu (2026-10-05). Çözüm: forbid_external KAPALI, ama her
indirme bu taşıyıcıdan geçer ve yalnız izinli sunucu + https kabul edilir (SSRF/başka sunucudan şema
yok). XXE / milyar kahkaha koruması `forbid_dtd=True` + `forbid_entities=True` ile AYNEN sürer.
"""
from __future__ import annotations

from urllib.parse import urlparse

from zeep.exceptions import ExternalReferenceForbidden
from zeep.transports import Transport


def host_of(url: str) -> str:
    return (urlparse(str(url or "")).hostname or "").lower()


class PinnedHostTransport(Transport):
    """Yalnız `allowed_hosts` içindeki https sunucularından WSDL/XSD indirir."""

    def __init__(self, *args, allowed_hosts=(), **kwargs):
        super().__init__(*args, **kwargs)
        self._allowed = {str(h).lower() for h in allowed_hosts if h}

    def load(self, url):
        p = urlparse(str(url))
        if p.scheme != "https" or (p.hostname or "").lower() not in self._allowed:
            raise ExternalReferenceForbidden(url)
        return super().load(url)


def safe_settings(*, huge_tree: bool = False):
    """DTD/varlık YASAK (XXE), dış kaynak denetimi taşıyıcıda (aynı sunucu)."""
    from zeep import Settings
    return Settings(strict=False, xml_huge_tree=huge_tree, forbid_dtd=True,
                    forbid_entities=True, forbid_external=False)
