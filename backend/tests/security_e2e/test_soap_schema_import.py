"""Doğan/MNG/Ticimax SOAP: WSDL'in AYNI sunucudan içe aktardığı şema (…?xsd=1) yüklenebilmeli; başka
sunucudan şema ve DTD/varlık (XXE) yasak kalmalı. 2026-10-05 canlı hata: zeep'in yeni sürümünde
forbid_external=True şema içe aktarmasını da engelledi → Doğan girişi/fatura sorgusu
"ExternalReferenceForbidden(url='https://connector.doganedonusum.com/AuthenticationWS?xsd=1')"."""
import pytest
from zeep import Client, Settings
from zeep.exceptions import ExternalReferenceForbidden
from zeep.transports import Transport

from security.soap_safe import PinnedHostTransport, safe_settings

HOST = "connector.doganedonusum.com"
WSDL_URL = f"https://{HOST}/AuthenticationWS?wsdl"
XSD_URL = f"https://{HOST}/AuthenticationWS?xsd=1"

XSD = b"""<?xml version="1.0"?>
<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema" targetNamespace="urn:t" xmlns:tns="urn:t"
  elementFormDefault="qualified">
  <xs:element name="Login"><xs:complexType><xs:sequence>
    <xs:element name="USER_NAME" type="xs:string"/></xs:sequence></xs:complexType></xs:element>
  <xs:element name="LoginResponse"><xs:complexType><xs:sequence>
    <xs:element name="SESSION_ID" type="xs:string"/></xs:sequence></xs:complexType></xs:element>
</xs:schema>"""


def _wsdl(xsd_url):
    return f"""<?xml version="1.0"?>
<definitions xmlns="http://schemas.xmlsoap.org/wsdl/" xmlns:soap="http://schemas.xmlsoap.org/wsdl/soap/"
  xmlns:tns="urn:t" xmlns:xs="http://www.w3.org/2001/XMLSchema" targetNamespace="urn:t">
  <types><xs:schema><xs:import namespace="urn:t" schemaLocation="{xsd_url}"/></xs:schema></types>
  <message name="LoginIn"><part name="parameters" element="tns:Login"/></message>
  <message name="LoginOut"><part name="parameters" element="tns:LoginResponse"/></message>
  <portType name="P"><operation name="Login"><input message="tns:LoginIn"/><output message="tns:LoginOut"/></operation></portType>
  <binding name="B" type="tns:P"><soap:binding transport="http://schemas.xmlsoap.org/soap/http"/>
    <operation name="Login"><soap:operation soapAction=""/><input><soap:body use="literal"/></input>
    <output><soap:body use="literal"/></output></operation></binding>
  <service name="S"><port name="SP" binding="tns:B"><soap:address location="https://{HOST}/AuthenticationWS"/></port></service>
</definitions>""".encode()


@pytest.fixture
def remote(monkeypatch):
    docs = {WSDL_URL: _wsdl(XSD_URL), XSD_URL: XSD,
            "https://evil.example/x.xsd": XSD,
            f"https://{HOST}/evil-wsdl": _wsdl("https://evil.example/x.xsd")}
    calls = []

    def _load(self, url):
        calls.append(url)
        return docs[url]
    monkeypatch.setattr(Transport, "_load_remote_data", _load)
    return calls


def test_old_settings_reproduce_live_error(remote):
    with pytest.raises(ExternalReferenceForbidden) as e:
        Client(WSDL_URL, transport=Transport(),
               settings=Settings(strict=False, forbid_dtd=True, forbid_entities=True, forbid_external=True))
    assert "xsd=1" in str(e.value)


def test_pinned_transport_loads_same_host_schema(remote):
    c = Client(WSDL_URL, transport=PinnedHostTransport(allowed_hosts={HOST}), settings=safe_settings())
    assert "Login" in next(iter(next(iter(c.wsdl.services.values())).ports.values())).binding._operations
    assert XSD_URL in remote


def test_schema_from_other_host_is_forbidden(remote):
    with pytest.raises(ExternalReferenceForbidden):
        Client(f"https://{HOST}/evil-wsdl", transport=PinnedHostTransport(allowed_hosts={HOST}),
               settings=safe_settings())
    assert "https://evil.example/x.xsd" not in remote          # hiç indirilmedi


def test_plain_http_forbidden_and_dtd_entities_still_blocked():
    t = PinnedHostTransport(allowed_hosts={HOST})
    with pytest.raises(ExternalReferenceForbidden):
        t.load(f"http://{HOST}/AuthenticationWS?wsdl")
    s = safe_settings()
    assert s.forbid_dtd is True and s.forbid_entities is True


def test_clients_use_pinned_transport():
    import inspect
    import dogan_client
    import mng_kargo_client
    import ticimax_client
    for mod in (dogan_client, mng_kargo_client, ticimax_client):
        src = inspect.getsource(mod)
        assert "PinnedHostTransport" in src and "forbid_external=True" not in src, mod.__name__
    dc = dogan_client.DoganClient("u", "p", is_test=False)
    assert isinstance(dc.transport, PinnedHostTransport) and dc.transport._allowed == {HOST}
