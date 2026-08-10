"""
Doğan e-Dönüşüm Client - e-Fatura, e-Arşiv, e-İrsaliye
SOAP API integration with zeep
"""
import logging
import os
from zeep import Client, Settings as ZeepSettings
from zeep.transports import Transport
from zeep.cache import InMemoryCache
import threading as _threading
from requests import Session
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# Doğan'ın kabul ettiği XSLT (Doğan örnek faturasından çıkarıldı, base64).
# UBL içinde AdditionalDocumentReference olarak gömülmeli, aksi halde
# Doğan "Gönderilen istek geçersizdir. Belge içerisinde şablon bulanamamıştır." (10013) hatası verir.
_XSLT_TEMPLATE_PATH = os.path.join(os.path.dirname(__file__), "dogan_xslt_template.txt")
try:
    with open(_XSLT_TEMPLATE_PATH, "r", encoding="utf-8") as _f:
        DOGAN_XSLT_B64 = _f.read().strip()
except Exception as _e:  # noqa
    logger.warning(f"Doğan XSLT template not loaded: {_e}")
    DOGAN_XSLT_B64 = ""

# e-Fatura için AYRI görsel şablon: başlık "e-Fatura", e-arşiv altbilgisi
# ("e-Arşiv izni... / İrsaliye yerine geçer.") kaldırılmış sürüm. Aksi halde
# e-fatura belgesi e-arşiv şablonuyla render edilip görselde "E-Arşiv Fatura"
# yazıyordu (veri e-fatura olmasına rağmen). Dosya yoksa e-arşiv şablonuna
# geri düşülür — böylece her zaman geçerli bir XSLT gömülür (Doğan 10013 önlemi).
_XSLT_EFATURA_PATH = os.path.join(os.path.dirname(__file__), "dogan_xslt_efatura_template.txt")
try:
    with open(_XSLT_EFATURA_PATH, "r", encoding="utf-8") as _f:
        DOGAN_XSLT_EFATURA_B64 = _f.read().strip()
except Exception as _e:  # noqa
    logger.warning(f"Doğan e-Fatura XSLT template not loaded: {_e}")
    DOGAN_XSLT_EFATURA_B64 = DOGAN_XSLT_B64


# --- Performans: zeep istemcileri + WSDL surec boyunca onbelleklenir ---
# Her cagrida Client(wsdl) kurmak WSDL'i indirip parse ettigi icin cok yavasti
# (tekli faturada birkac kez, toplu faturada N kez). Asagidaki onbellek WSDL'i
# surec basina BIR kez parse eder; SESSION_ID her SOAP cagrisinda header ile
# gectigi icin istemci paylasimi guvenlidir.
_WSDL_CACHE = InMemoryCache()
_CLIENT_CACHE = {}
_CLIENT_LOCK = _threading.Lock()


def _get_cached_client(wsdl_url, transport, settings):
    c = _CLIENT_CACHE.get(wsdl_url)
    if c is None:
        with _CLIENT_LOCK:
            c = _CLIENT_CACHE.get(wsdl_url)
            if c is None:
                c = Client(wsdl_url, transport=transport, settings=settings)
                _CLIENT_CACHE[wsdl_url] = c
    return c


def _free_shipping_line_xml(idx: int, waived_incl: float, currency: str, kdv_rate: float = 20.0) -> str:
    """Ücretsiz kargo kampanyası İSKONTO satırı. Satır-seviyesi cac:AllowanceCharge ile
    LineExtensionAmount = Fiyat − İskonto = 0 → belge TOPLAM/MATRAH/KDV'sine 0 katkı (GİB-güvenli;
    belge-seviyesi indirimin aksine matrahı bozmaz). Faturada 'Kargo Bedeli X' + 'Kampanya
    İndirimi X' olarak görünür; müşteri kargonun bedava verildiğini iskonto olarak görür."""
    base_excl = round(float(waived_incl or 0) / (1.0 + kdv_rate / 100.0), 2)
    if base_excl <= 0:
        return ""
    return f"""<cac:InvoiceLine>
    <cbc:ID>{idx}</cbc:ID>
    <cbc:InvoicedQuantity unitCode="C62">1</cbc:InvoicedQuantity>
    <cbc:LineExtensionAmount currencyID="{currency}">0.00</cbc:LineExtensionAmount>
    <cac:AllowanceCharge>
      <cbc:ChargeIndicator>false</cbc:ChargeIndicator>
      <cbc:AllowanceChargeReason>Ücretsiz Kargo Kampanyası</cbc:AllowanceChargeReason>
      <cbc:Amount currencyID="{currency}">{base_excl:.2f}</cbc:Amount>
    </cac:AllowanceCharge>
    <cac:TaxTotal>
      <cbc:TaxAmount currencyID="{currency}">0.00</cbc:TaxAmount>
      <cac:TaxSubtotal>
        <cbc:TaxableAmount currencyID="{currency}">0.00</cbc:TaxableAmount>
        <cbc:TaxAmount currencyID="{currency}">0.00</cbc:TaxAmount>
        <cbc:Percent>{kdv_rate:g}</cbc:Percent>
        <cac:TaxCategory>
          <cac:TaxScheme>
            <cbc:Name>GERÇEK USULDE KATMA DEĞER VERGİSİ</cbc:Name>
            <cbc:TaxTypeCode>0015</cbc:TaxTypeCode>
          </cac:TaxScheme>
        </cac:TaxCategory>
      </cac:TaxSubtotal>
    </cac:TaxTotal>
    <cac:Item>
      <cbc:Name>Kargo Bedeli (Ücretsiz Kargo Kampanyası)</cbc:Name>
      <cac:SellersItemIdentification><cbc:ID>KARGO-KAMP</cbc:ID></cac:SellersItemIdentification>
    </cac:Item>
    <cac:Price>
      <cbc:PriceAmount currencyID="{currency}">{base_excl:.4f}</cbc:PriceAmount>
    </cac:Price>
  </cac:InvoiceLine>"""


class DoganClient:
    def __init__(self, username: str, password: str, is_test: bool = True):
        self.username = username
        # GÜVENLİK: parola DB'de şifreli (v1:...) saklanabilir → burada çöz.
        # decrypt() düz-metni olduğu gibi geçirir, bu yüzden eski kayıtlar da çalışır.
        try:
            from security.crypto import decrypt as _dec_secret
            self.password = _dec_secret(password) if password else password
        except Exception:
            self.password = password
        self.is_test = is_test
        self.session_id = None

        if is_test:
            self.auth_wsdl = "https://efaturatest.doganedonusum.com/AuthenticationWS?wsdl"
            self.efatura_wsdl = "https://efaturatest.doganedonusum.com/EFaturaOIB?wsdl"
            self.earsiv_wsdl = "https://efaturatest.doganedonusum.com:443/EIArchiveWS/EFaturaArchive?wsdl"
            self.eirsaliye_wsdl = "https://efaturatest.doganedonusum.com/EIrsaliyeWS/EIrsaliye?wsdl"
        else:
            # CANLI endpoint = connector.doganedonusum.com (Doğan müşteri yöneticisi tarafından sağlanan resmi URL'ler)
            self.auth_wsdl = "https://connector.doganedonusum.com/AuthenticationWS?wsdl"
            self.efatura_wsdl = "https://connector.doganedonusum.com/EFaturaOIB?wsdl"
            self.earsiv_wsdl = "https://connector.doganedonusum.com/EIArchiveWS/EFaturaArchive?wsdl"
            self.eirsaliye_wsdl = "https://connector.doganedonusum.com/EIrsaliyeWS/EIrsaliye?wsdl"

        session = Session()
        session.verify = True
        self.transport = Transport(session=session, timeout=30, cache=_WSDL_CACHE)
        self.zeep_settings = ZeepSettings(strict=False, xml_huge_tree=True)

    def login(self) -> str:
        """Authenticate and get session ID. Raises on failure."""
        try:
            client = _get_cached_client(self.auth_wsdl, self.transport, self.zeep_settings)
            header = {"SESSION_ID": "", "APPLICATION_NAME": "FACETTE"}
            result = client.service.Login(
                REQUEST_HEADER=header,
                USER_NAME=self.username,
                PASSWORD=self.password
            )
            # Response: LoginResponse{SESSION_ID, ERROR_TYPE}
            err = None
            sid = None
            if hasattr(result, "__values__"):
                vals = dict(result.__values__)
                sid = vals.get("SESSION_ID")
                err = vals.get("ERROR_TYPE")
            else:
                sid = str(result) if result else None

            if err:
                err_dict = dict(err.__values__) if hasattr(err, "__values__") else {"ERROR_SHORT_DES": str(err)}
                code = err_dict.get("ERROR_CODE")
                msg = err_dict.get("ERROR_SHORT_DES") or err_dict.get("ERROR_LONG_DES") or "Login hatası"
                raise Exception(f"Doğan login {code}: {msg}")

            if not sid:
                raise Exception("SESSION_ID alınamadı")

            self.session_id = str(sid)
            logger.info(f"Doğan e-Dönüşüm login successful, session: {self.session_id[:20]}...")
            return self.session_id
        except Exception as e:
            logger.error(f"Doğan e-Dönüşüm login failed: {e}")
            raise

    def logout(self):
        """Close session"""
        if not self.session_id:
            return
        try:
            client = _get_cached_client(self.auth_wsdl, self.transport, self.zeep_settings)
            header = {"SESSION_ID": self.session_id, "APPLICATION_NAME": "FACETTE"}
            client.service.Logout(REQUEST_HEADER=header)
            self.session_id = None
        except Exception as e:
            logger.warning(f"Doğan logout error: {e}")

    def _get_efatura_client(self):
        if not self.session_id:
            self.login()
        return _get_cached_client(self.efatura_wsdl, self.transport, self.zeep_settings)

    def _get_earsiv_client(self):
        if not self.session_id:
            self.login()
        return _get_cached_client(self.earsiv_wsdl, self.transport, self.zeep_settings)

    def _make_header(self, compressed="N"):
        return {
            "SESSION_ID": self.session_id,
            "APPLICATION_NAME": "FACETTE",
            "COMPRESSED": compressed,
        }

    def get_invoice_status(self, uuid: str) -> dict:
        """Get status of an invoice by UUID"""
        try:
            client = self._get_efatura_client()
            result = client.service.GetInvoiceStatus(
                REQUEST_HEADER=self._make_header(),
                INVOICE_SEARCH_KEY={"UUID": uuid}
            )
            return {"success": True, "status": str(result)}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _list_efatura_operations(self) -> list:
        """E-Fatura (EFaturaOIB) WSDL'indeki operasyon adlarını döndürür."""
        try:
            client = self._get_efatura_client()
            names = set()
            for service in client.wsdl.services.values():
                for port in service.ports.values():
                    try:
                        names.update(port.binding._operations.keys())
                    except Exception:
                        pass
            return sorted(names)
        except Exception:
            return []

    def _efatura_op_signature(self, op_name: str) -> str:
        """Bir e-Fatura operasyonunun giriş imzasını (parametre adları/tipleri) döndürür.
        Doğru parametre adlarını (ör. GetInvoiceWithType'ın format alanı) bulmak için."""
        try:
            client = self._get_efatura_client()
            for service in client.wsdl.services.values():
                for port in service.ports.values():
                    ops = getattr(port.binding, "_operations", {})
                    if op_name in ops:
                        try:
                            return str(ops[op_name].input.signature())
                        except Exception as e:
                            return f"(signature err: {e})"
            return "(operation not found)"
        except Exception as e:
            return f"(err: {e})"

    @staticmethod
    def _extract_pdf_bytes(obj) -> bytes:
        """Doğan yanıtından PDF ikili verisini (bytes ya da base64 string) çıkarır.
        Yanıt şeması sürüme göre değiştiği için yapıyı rekürsif tararız: bytes değeri
        doğrudan alınır; '%PDF' ile başlayan / base64 çözülünce '%PDF' veren string
        PDF olarak kabul edilir."""
        import base64 as _b64
        best = b""

        def _walk(v):
            nonlocal best
            if best:
                return
            if isinstance(v, (bytes, bytearray)):
                b = bytes(v)
                if b[:4] == b"%PDF" or len(b) > 1000:
                    best = b
                return
            if isinstance(v, str):
                s = v.strip()
                if len(s) < 100:
                    return
                try:
                    dec = _b64.b64decode(s, validate=False)
                    if dec[:4] == b"%PDF":
                        best = dec
                except Exception:
                    pass
                return
            if isinstance(v, dict):
                for x in v.values():
                    _walk(x)
            elif isinstance(v, (list, tuple)):
                for x in v:
                    _walk(x)

        _walk(obj)
        return best

    @staticmethod
    def _decode_content(v):
        """Doğan CONTENT alanını çözer: bytes / base64 / gzip. Döner: (kind, data_bytes).
        kind: 'pdf' | 'xml' | '' (bilinmiyor)."""
        import base64 as _b64, gzip as _gz
        raw = None
        if isinstance(v, (bytes, bytearray)):
            raw = bytes(v)
        elif isinstance(v, str):
            s = v.strip()
            if len(s) < 40:
                return ("", b"")
            try:
                raw = _b64.b64decode(s, validate=False)
            except Exception:
                raw = s.encode("utf-8", "ignore")
        else:
            return ("", b"")
        if not raw:
            return ("", b"")
        # gzip?
        if raw[:2] == b"\x1f\x8b":
            try:
                raw = _gz.decompress(raw)
            except Exception:
                pass
        head = raw[:512].lstrip()
        if head[:4] == b"%PDF":
            return ("pdf", raw)
        if head[:5] == b"<?xml" or b"<Invoice" in head or b"urn:oasis" in head or head[:1] == b"<":
            return ("xml", raw)
        return ("", raw)

    def get_efatura_pdf(self, uuid: str = "", invoice_id: str = "") -> dict:
        """Gönderilmiş bir e-Faturanın belgesini (PDF ya da resmî imzalı UBL-XML) Doğan'dan çeker.

        e-Fatura'da e-Arşiv gibi hazır WEB_KEY URL dönmez. Doğan e-Fatura servisi faturayı
        GetInvoiceWithType/GetInvoice ile CONTENT (imzalı UBL-XML, gömülü XSLT ile görüntülenebilir)
        olarak döndürür; bazı kurulumlarda PDF de gelebilir. CONTENT base64/gzip olabilir → çözülür.
        Döner: {success, kind:'pdf'|'xml', pdf(bytes), operation, structure} / {success:False, ...}.
        """
        try:
            client = self._get_efatura_client()
            ops = self._list_efatura_operations()
            preferred = ("GetInvoiceWithType", "GetInvoice", "GetInvoiceResponse")
            candidates = [c for c in preferred if c in ops] or \
                         [o for o in ops if "getinvoice" in o.lower()]
            if not candidates:
                return {"success": False, "error": "e-Fatura getir operasyonu WSDL'de yok",
                        "available_operations": ops}

            from zeep.helpers import serialize_object
            structure = {}
            _idkey = {"UUID": uuid} if uuid else {"ID": invoice_id}
            # GetInvoiceWithType: Desteklenen TYPE = PDF/HTML/XML; DIRECTION zorunlu (giden=OUT).
            # Önce PDF iste; olmazsa HTML/XML (yine de görüntülenebilir belge).
            attempts = []
            if "GetInvoiceWithType" in ops:
                for _t in ("PDF", "HTML", "XML"):
                    attempts.append(("GetInvoiceWithType",
                                     {**_idkey, "TYPE": _t, "DIRECTION": "OUT"}))
            for _op in candidates:
                if _op != "GetInvoiceWithType":
                    attempts.append((_op, {**_idkey, "DIRECTION": "OUT"}))

            for op_name, search_key in attempts:
                op = getattr(client.service, op_name)
                _tag = f"{op_name}:{search_key.get('TYPE','')}"
                try:
                    result = op(REQUEST_HEADER=self._make_header(),
                                INVOICE_SEARCH_KEY=search_key, HEADER_ONLY="N")
                except Exception as _e1:
                    try:
                        result = op(REQUEST_HEADER=self._make_header(),
                                    INVOICE_SEARCH_KEY=search_key)
                    except Exception as _e2:
                        structure[_tag] = f"call-err: {_e2}"
                        continue
                ser = serialize_object(result)
                kind, data = self._find_document(ser)
                structure[_tag] = self._structure_preview(ser)
                if data:
                    return {"success": True, "kind": kind, "pdf": data,
                            "operation": _tag, "structure": structure}
            return {"success": False, "error": "Yanitta belge (PDF/XML) bulunamadi",
                    "operation": candidates[0], "structure": structure,
                    "available_operations": ops}
        except Exception as e:
            logger.error(f"get_efatura_pdf error: {e}")
            return {"success": False, "error": str(e)}

    def _find_document(self, obj):
        """Yanıt ağacında ilk çözülebilir belgeyi (pdf/xml) bulur. Döner: (kind, bytes)."""
        found = ("", b"")

        def _walk(v):
            nonlocal found
            if found[1]:
                return
            if isinstance(v, (bytes, bytearray, str)):
                k, d = self._decode_content(v)
                if d and (k in ("pdf", "xml")):
                    found = (k, d)
                return
            if isinstance(v, dict):
                # Öncelik: CONTENT/PDF/DATA/FILE adlı alanlar
                for key in ("CONTENT", "PDF", "DATA", "FILE", "INVOICE_CONTENT", "DOCUMENT"):
                    if key in v:
                        _walk(v[key])
                        if found[1]:
                            return
                for x in v.values():
                    _walk(x)
            elif isinstance(v, (list, tuple)):
                for x in v:
                    _walk(x)

        _walk(obj)
        return found

    @staticmethod
    def _structure_preview(obj, depth=0):
        """Yanıt yapısını (alan adları + tip/uzunluk) teşhis için özetler."""
        if depth > 4:
            return "…"
        if isinstance(obj, dict):
            return {k: DoganClient._structure_preview(v, depth + 1) for k, v in list(obj.items())[:20]}
        if isinstance(obj, (list, tuple)):
            return [DoganClient._structure_preview(x, depth + 1) for x in obj[:3]]
        if isinstance(obj, (bytes, bytearray)):
            return f"<bytes len={len(obj)}>"
        if isinstance(obj, str):
            return f"<str len={len(obj)}: {obj[:40]!r}>" if len(obj) > 40 else obj
        return obj

    def _list_earsiv_operations(self) -> list:
        """E-Arşiv (EFaturaArchive) WSDL'indeki tüm operasyon adlarını döndürür.
        İptal operasyonunu dinamik bulmak ve hata ayıklamak için kullanılır."""
        try:
            client = self._get_earsiv_client()
            names = set()
            for service in client.wsdl.services.values():
                for port in service.ports.values():
                    try:
                        names.update(port.binding._operations.keys())
                    except Exception:
                        pass
            return sorted(names)
        except Exception:
            return []

    def cancel_earsiv_invoice(self, *, invoice_uuid: str = "", invoice_id: str = "",
                               reason: str = "Sipariş iptal edildi") -> dict:
        """E-Arşiv faturayı Doğan üzerinden iptal eder.

        ÖNEMLİ: connector/test domaini geliştirme ortamında ağ izninde olmadığı için
        WSDL'deki iptal operasyonunun adı/şeması BİREBİR doğrulanamadı. Bu yüzden:
          - iptal operasyonunu WSDL'den DİNAMİK buluruz (CancelEArchiveInvoice / CancelInvoice
            / 'cancel' içeren ilk operasyon),
          - en yaygın payload şemasıyla çağırırız,
          - HATA durumunda mevcut operasyon adlarını döndürürüz ki test ortamında
            gerçek ad/şema netleşsin ve buraya birebir yazılabilsin.
        Durum güncellemesini ASLA bloklamaz (çağıran taraf try/except ile sarmalıdır).
        """
        try:
            client = self._get_earsiv_client()
            ops = self._list_earsiv_operations()
            cancel_op = None
            for cand in ("CancelEArchiveInvoice", "CancelInvoice",
                         "CancelArchiveInvoice", "CancelEArsivInvoice", "CancelEArchive"):
                if cand in ops:
                    cancel_op = cand
                    break
            if not cancel_op:
                cancel_op = next((o for o in ops if "cancel" in o.lower()), None)
            if not cancel_op:
                return {"success": False, "error": "İptal operasyonu WSDL'de bulunamadı",
                        "available_operations": ops}

            from datetime import datetime, timezone
            op = getattr(client.service, cancel_op)
            cancel_payload = {
                "CANCEL_REASON": (reason or "Sipariş iptal edildi")[:250],
                "CANCEL_DATE": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            }
            if invoice_uuid:
                cancel_payload["UUID"] = invoice_uuid
            if invoice_id:
                cancel_payload["INVOICE_ID"] = invoice_id

            # En yaygın imza: REQUEST_HEADER + tekil iptal kaydı. Param adı doğrulanamadığı
            # için birkaç olası sarmalayıcı adını sırayla dener.
            last_err = None
            for wrap in ("CANCEL_INVOICE", "CANCEL", "INVOICE", "ARCHIVE_INVOICE_CANCEL"):
                try:
                    result = op(REQUEST_HEADER=self._make_header(), **{wrap: cancel_payload})
                    from zeep.helpers import serialize_object
                    ser = serialize_object(result) or {}
                    err = (ser.get("ERROR_TYPE") or {}) if isinstance(ser, dict) else {}
                    err_code = err.get("ERROR_CODE") if err else None
                    if err_code:
                        return {"success": False, "operation": cancel_op,
                                "code": str(err_code),
                                "message": str(err.get("ERROR_SHORT_DES") or "Bilinmeyen hata"),
                                "raw": str(ser)[:600]}
                    return {"success": True, "operation": cancel_op,
                            "param": wrap, "raw": str(ser)[:600]}
                except TypeError as te:
                    last_err = str(te)
                    continue  # param adı yanlış → sıradaki sarmalayıcıyı dene
            return {"success": False, "operation": cancel_op,
                    "error": f"İptal payload şeması eşleşmedi: {last_err}",
                    "available_operations": ops}
        except Exception as e:
            try:
                ops = self._list_earsiv_operations()
            except Exception:
                ops = []
            return {"success": False, "error": str(e), "available_operations": ops}

    def check_user(self, vkn: str) -> dict:
        """Check if a VKN is registered for e-Fatura. Returns the user list with
        their PK aliases. is_efatura=True if INVOICE document_type alias exists.
        """
        try:
            from zeep.helpers import serialize_object
            client = self._get_efatura_client()
            result = client.service.CheckUser(
                REQUEST_HEADER=self._make_header(),
                USER={"IDENTIFIER": vkn}
            )
            ser = serialize_object(result) or {}
            # Response shape: {USER: [...], ERROR_TYPE: ...}
            user_list = ser.get("USER") or []
            users = []
            for u in user_list:
                if not u or not isinstance(u, dict):
                    continue
                users.append({
                    "identifier": str(u.get("IDENTIFIER") or ""),
                    "alias": str(u.get("ALIAS") or ""),
                    "title": str(u.get("TITLE") or ""),
                    "type": str(u.get("TYPE") or ""),
                    "unit": str(u.get("UNIT") or ""),
                    "document_type": str(u.get("DOCUMENT_TYPE") or ""),
                })
            # e-Fatura mükellefi sayılır: en az 1 INVOICE document_type'a sahip alias varsa
            invoice_users = [u for u in users if u.get("document_type") == "INVOICE" and u.get("alias")]
            return {
                "success": True,
                "users": users,
                "is_efatura": len(invoice_users) > 0,
                "invoice_alias": invoice_users[0]["alias"] if invoice_users else "",
            }
        except Exception as e:
            return {"success": False, "error": str(e), "is_efatura": False}

    def test_connection(self) -> dict:
        """Test connection to Doğan e-Dönüşüm"""
        try:
            session_id = self.login()
            self.logout()
            return {"success": True, "message": "Bağlantı başarılı", "session_id": session_id[:20] + "..."}
        except Exception as e:
            return {"success": False, "message": f"Bağlantı hatası: {str(e)}"}

    # ═════════════════ UBL-TR e-Arşiv Fatura Üretimi ═════════════════════
    @staticmethod
    def build_earsiv_ubl_xml(*,
                              invoice_uuid: str,
                              invoice_number: str,
                              issue_date: str,            # YYYY-MM-DD
                              issue_time: str,            # HH:MM:SS
                              supplier_vkn: str,
                              supplier_name: str,
                              platform_label: str = "",
                              supplier_district: str = "",
                              supplier_city: str = "",
                              supplier_street: str = "",
                              supplier_country: str = "Türkiye",
                              supplier_tax_office: str = "",
                              supplier_phone: str = "",
                              supplier_email: str = "",
                              supplier_website: str = "",
                              customer_vkn_or_tckn: str,  # 11 haneli TCKN veya 10 haneli VKN
                              customer_name: str,
                              customer_district: str = "",
                              customer_city: str = "",
                              customer_street: str = "",
                              customer_country: str = "Türkiye",
                              customer_postal_zone: str = "",
                              customer_phone: str = "",
                              customer_email: str = "",
                              customer_tax_office: str = "",
                              currency: str = "TRY",
                              kdv_rate: float = 20.0,
                              line_items: list = None,    # [{name, qty, unit_price, kdv_rate, sku, note, barcode}]
                              shipping_cost: float = 0.0,
                              discount: float = 0.0,
                              free_shipping_waived: float = 0.0,
                              note: str = "",
                              order_number: str = "",
                              payment_method: str = "",
                              carrier_vkn: str = "6080712084",
                              carrier_name: str = "MNG KARGO YURTİÇİ VE YURTDIŞI TAŞIMACILIK A.Ş.",
                              carrier_city: str = "İstanbul",
                              cargo_tracking: str = "",
                              order_ext_id: str = "",
                              store_name: str = "",
                              payment_amount: float = 0.0,
                              ) -> str:
        """UBL-TR 1.2 e-Arşiv Fatura XML üretici (Doğan e-Dönüşüm CANLI uyumlu).

        Bireysel müşteri (TCKN 11 hane) ve kurumsal (VKN 10 hane) destekler.
        FCT2026000011227.xml örnek dosyasına birebir şema uyumudur:
          • Tam namespace seti (ubltr, ds, xades, qdt, ccts, udt)
          • cac:Signature bloğu (UBL-TR'de zorunlu)
          • cac:AdditionalDocumentReference > SendingType=ELEKTRONIK
          • InvoicedQuantity unitCode="C62" (UBL-TR adet kodu)
          • cac:PaymentMeans, cac:Delivery (opsiyonel ama Doğan örneğinde mevcut)
          • SellersItemIdentification her satırda
        Tüm tutarlar KDV hariç. Satır toplamı + KDV + kargo - indirim = genel toplam.
        """
        from html import escape

        line_items = line_items or []
        is_individual = len(customer_vkn_or_tckn) == 11
        party_id_scheme = "TCKN" if is_individual else "VKN"

        # null/None safety — UBL'de "None" string'i şema hatası verir
        def _s(v):
            if v is None or str(v).lower() == "none":
                return ""
            return str(v).strip()

        supplier_phone = _s(supplier_phone)
        supplier_email = _s(supplier_email)
        supplier_website = _s(supplier_website)
        supplier_street = _s(supplier_street)
        _sup_street_line = f"<cbc:StreetName>{escape(supplier_street)}</cbc:StreetName>" if supplier_street else ""
        supplier_district = _s(supplier_district)
        supplier_city = _s(supplier_city) or "İstanbul"
        supplier_tax_office = _s(supplier_tax_office) or "HALKALI VERGİ DAİRESİ BAŞKANLIĞI"
        customer_phone = _s(customer_phone)
        customer_email = _s(customer_email)
        customer_street = _s(customer_street)
        customer_district = _s(customer_district)
        customer_city = _s(customer_city) or "İstanbul"
        customer_name = _s(customer_name) or "Bireysel Müşteri"
        customer_tax_office = _s(customer_tax_office)
        customer_postal_zone = _s(customer_postal_zone)
        # #5: açık adres (StreetName) ve posta kodu (PostalZone) — örnek faturada var, eksikti
        customer_street_xml = f"<cbc:StreetName>{escape(customer_street)}</cbc:StreetName>" if customer_street else ""
        customer_zone_xml = f"<cbc:PostalZone>{escape(customer_postal_zone)}</cbc:PostalZone>" if customer_postal_zone else ""
        note = _s(note)
        order_number = _s(order_number)
        payment_method = _s(payment_method) or "DIGER"

        # ─── InvoiceLine'lar — UBL-TR unitCode "C62" (Adet) ──────────────
        invoice_lines_xml = []
        line_subtotal = 0.0
        kdv_total = 0.0
        # KDV oranı bazlı gruplandırma (TaxTotal'da multi-subtotal için)
        kdv_groups = {}  # rate → {"taxable": x, "tax": y}

        for idx, it in enumerate(line_items, start=1):
            qty = float(it.get("qty") or 1)
            unit_price = float(it.get("unit_price") or 0)
            li_kdv_rate = float(it.get("kdv_rate") if it.get("kdv_rate") is not None else kdv_rate)
            gross_line = round(qty * unit_price, 2)
            line_amount = round(gross_line / (1.0 + li_kdv_rate / 100.0), 2)
            # O1: KDV'yi gross−net olarak al (e-Fatura builder ile aynı). round(net×oran) çok satırlı
            # faturada ±0.01 sürükleniyor ve PayableAmount tahsil edilen tutarla tutmuyordu.
            line_kdv = round(gross_line - line_amount, 2)
            net_unit_price = round(line_amount / qty, 4) if qty else line_amount
            line_subtotal += line_amount
            kdv_total += line_kdv
            grp = kdv_groups.setdefault(li_kdv_rate, {"taxable": 0.0, "tax": 0.0})
            grp["taxable"] += line_amount
            grp["tax"] += line_kdv

            name = escape((it.get("name") or "Ürün"))[:255]
            sku = escape(_s(it.get("sku") or it.get("product_code") or f"URN{idx:04d}"))
            li_note = escape(_s(it.get("note") or ""))  # barkod ARTIK not'a yazilmaz (stok ad altinda gorunmesin)
            note_xml = f"<cbc:Note>{li_note}</cbc:Note>" if li_note else ""

            invoice_lines_xml.append(f"""<cac:InvoiceLine>
    <cbc:ID>{idx}</cbc:ID>
    {note_xml}
    <cbc:InvoicedQuantity unitCode="C62">{qty:g}</cbc:InvoicedQuantity>
    <cbc:LineExtensionAmount currencyID="{currency}">{line_amount:.2f}</cbc:LineExtensionAmount>
    <cac:TaxTotal>
      <cbc:TaxAmount currencyID="{currency}">{line_kdv:.2f}</cbc:TaxAmount>
      <cac:TaxSubtotal>
        <cbc:TaxableAmount currencyID="{currency}">{line_amount:.2f}</cbc:TaxableAmount>
        <cbc:TaxAmount currencyID="{currency}">{line_kdv:.2f}</cbc:TaxAmount>
        <cbc:Percent>{li_kdv_rate:g}</cbc:Percent>
        <cac:TaxCategory>
          <cac:TaxScheme>
            <cbc:Name>GERÇEK USULDE KATMA DEĞER VERGİSİ</cbc:Name>
            <cbc:TaxTypeCode>0015</cbc:TaxTypeCode>
          </cac:TaxScheme>
        </cac:TaxCategory>
      </cac:TaxSubtotal>
    </cac:TaxTotal>
    <cac:Item>
      <cbc:Name>{name}</cbc:Name>
      <cac:SellersItemIdentification>
        <cbc:ID>{sku}</cbc:ID>
      </cac:SellersItemIdentification>
    </cac:Item>
    <cac:Price>
      <cbc:PriceAmount currencyID="{currency}">{net_unit_price:.4f}</cbc:PriceAmount>
    </cac:Price>
  </cac:InvoiceLine>""")

        # Kargo bedeli — ayrı InvoiceLine olarak eklenir (sample böyle yapıyor)
        if shipping_cost > 0:
            sh_kdv_rate = 20.0
            sh_taxable = round(shipping_cost / (1.0 + sh_kdv_rate / 100.0), 2)
            sh_kdv = round(sh_taxable * sh_kdv_rate / 100.0, 2)
            kdv_total += sh_kdv
            line_subtotal += sh_taxable
            grp = kdv_groups.setdefault(sh_kdv_rate, {"taxable": 0.0, "tax": 0.0})
            grp["taxable"] += sh_taxable
            grp["tax"] += sh_kdv
            next_idx = len(line_items) + 1
            invoice_lines_xml.append(f"""<cac:InvoiceLine>
    <cbc:ID>{next_idx}</cbc:ID>
    <cbc:InvoicedQuantity unitCode="C62">1</cbc:InvoicedQuantity>
    <cbc:LineExtensionAmount currencyID="{currency}">{sh_taxable:.2f}</cbc:LineExtensionAmount>
    <cac:TaxTotal>
      <cbc:TaxAmount currencyID="{currency}">{sh_kdv:.2f}</cbc:TaxAmount>
      <cac:TaxSubtotal>
        <cbc:TaxableAmount currencyID="{currency}">{sh_taxable:.2f}</cbc:TaxableAmount>
        <cbc:TaxAmount currencyID="{currency}">{sh_kdv:.2f}</cbc:TaxAmount>
        <cbc:Percent>{sh_kdv_rate:g}</cbc:Percent>
        <cac:TaxCategory>
          <cac:TaxScheme>
            <cbc:Name>GERÇEK USULDE KATMA DEĞER VERGİSİ</cbc:Name>
            <cbc:TaxTypeCode>0015</cbc:TaxTypeCode>
          </cac:TaxScheme>
        </cac:TaxCategory>
      </cac:TaxSubtotal>
    </cac:TaxTotal>
    <cac:Item>
      <cbc:Name>KARGO</cbc:Name>
      <cac:SellersItemIdentification><cbc:ID>KARGO</cbc:ID></cac:SellersItemIdentification>
    </cac:Item>
    <cac:Price>
      <cbc:PriceAmount currencyID="{currency}">{sh_taxable:.4f}</cbc:PriceAmount>
    </cac:Price>
  </cac:InvoiceLine>""")

        # İndirim — AllowanceCharge bloğu (root seviyesinde, TaxTotal öncesinde)
        # Ücretsiz kargo kampanyası İSKONTO satırı (net 0, GİB-güvenli — toplam/matrah değişmez).
        if free_shipping_waived and float(free_shipping_waived) > 0 and (shipping_cost or 0) <= 0:
            _fsl = _free_shipping_line_xml(len(invoice_lines_xml) + 1, float(free_shipping_waived), currency)
            if _fsl:
                invoice_lines_xml.append(_fsl)
        allowance_charges_xml = []
        if discount > 0:
            line_subtotal -= discount
            allowance_charges_xml.append(f"""<cac:AllowanceCharge>
    <cbc:ChargeIndicator>false</cbc:ChargeIndicator>
    <cbc:AllowanceChargeReason>İndirim</cbc:AllowanceChargeReason>
    <cbc:Amount currencyID="{currency}">{discount:.2f}</cbc:Amount>
  </cac:AllowanceCharge>""")

        tax_inclusive_total = round(line_subtotal + kdv_total, 2)
        payable_amount = tax_inclusive_total

        # KDV TaxSubtotal blokları (multi-rate destekler)
        tax_subtotals_xml = []
        for rate, grp in sorted(kdv_groups.items()):
            tax_subtotals_xml.append(f"""<cac:TaxSubtotal>
      <cbc:TaxableAmount currencyID="{currency}">{grp['taxable']:.2f}</cbc:TaxableAmount>
      <cbc:TaxAmount currencyID="{currency}">{grp['tax']:.2f}</cbc:TaxAmount>
      <cbc:Percent>{rate:g}</cbc:Percent>
      <cac:TaxCategory>
        <cac:TaxScheme>
          <cbc:Name>GERÇEK USULDE KATMA DEĞER VERGİSİ</cbc:Name>
          <cbc:TaxTypeCode>0015</cbc:TaxTypeCode>
        </cac:TaxScheme>
      </cac:TaxCategory>
    </cac:TaxSubtotal>""")

        # ─── AccountingCustomerParty ─────────────────────────────────────
        if is_individual:
            parts = (customer_name or "").strip().split(" ", 1)
            first_name = escape(parts[0])
            last_name = escape(parts[1] if len(parts) > 1 else parts[0])
            customer_name_block = f"""<cac:Person>
        <cbc:FirstName>{first_name}</cbc:FirstName>
        <cbc:FamilyName>{last_name}</cbc:FamilyName>
      </cac:Person>"""
            customer_legal_block = ""
            customer_tax_scheme_block = ""
        else:
            customer_name_block = ""
            customer_legal_block = f"""<cac:PartyName>
        <cbc:Name>{escape(customer_name)}</cbc:Name>
      </cac:PartyName>"""
            customer_tax_scheme_block = f"""<cac:PartyTaxScheme>
        <cac:TaxScheme>
          <cbc:Name>{escape(customer_tax_office or '-')}</cbc:Name>
        </cac:TaxScheme>
      </cac:PartyTaxScheme>"""

        customer_contact_xml = ""
        if customer_phone or customer_email:
            tel_xml = f"<cbc:Telephone>{escape(customer_phone)}</cbc:Telephone>" if customer_phone else ""
            mail_xml = f"<cbc:ElectronicMail>{escape(customer_email)}</cbc:ElectronicMail>" if customer_email else ""
            customer_contact_xml = f"<cac:Contact>{tel_xml}{mail_xml}</cac:Contact>"
        else:
            customer_contact_xml = "<cac:Contact/>"

        # ─── Notlar (sample birden fazla Note kullanıyor) ────────────────
        def _tr_money_words(n):
            n = int(round(float(n or 0)))
            birler = ["", "Bir", "İki", "Üç", "Dört", "Beş", "Altı", "Yedi", "Sekiz", "Dokuz"]
            onlar = ["", "On", "Yirmi", "Otuz", "Kırk", "Elli", "Altmış", "Yetmiş", "Seksen", "Doksan"]
            def _uc(x):
                s = ""; y = x // 100; k = (x % 100) // 10; b = x % 10
                if y: s += ("" if y == 1 else birler[y]) + "Yüz"
                if k: s += onlar[k]
                if b: s += birler[b]
                return s
            if n == 0: return "Sıfır"
            out = ""; mr = n // 10**9; mn = (n % 10**9) // 10**6; bn = (n % 10**6) // 1000; kl = n % 1000
            if mr: out += _uc(mr) + "Milyar"
            if mn: out += _uc(mn) + "Milyon"
            if bn: out += (("" if bn == 1 else _uc(bn)) + "Bin")
            if kl: out += _uc(kl)
            return out

        notes_xml = []
        if order_number:
            notes_xml.append(f"<cbc:Note>{escape(order_number)}</cbc:Note>")
            notes_xml.append(f"<cbc:Note>Siparis No: {escape(order_number)} :Kargo Takip No: {escape(cargo_tracking)} :Sipariş ID: {escape(order_ext_id)}</cbc:Note>")
        _satici_adr = (supplier_street or "").strip()
        if _satici_adr:
            notes_xml.append(f"<cbc:Note>Taraf : Satıcı; {escape(_satici_adr)}</cbc:Note>")
        # Alıcı açık adresi NOT olarak EKLENMEZ: adres zaten cac:PostalAddress'ten
        # (StreetName + "ilçe / il") basılıyor. "Taraf : Alıcı; <adres>" notu eklenince
        # adres faturada İKİ KEZ görünüyordu (site siparişinde tam adres dolu → belirgin;
        # marketplace'te adres maskeli geldiği için orada fark edilmiyordu).
        notes_xml.append(f"<cbc:Note>Yalnız {_tr_money_words(payable_amount)} Lira</cbc:Note>")
        if store_name:
            notes_xml.append(f"<cbc:Note>Mağaza Adı :{escape(store_name)}</cbc:Note>")
        if payment_method or platform_label:
            _lbl = (platform_label or payment_method).strip()
            _amt = payment_amount if payment_amount else payable_amount
            _amt_str = f"{_amt:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
            notes_xml.append(f"<cbc:Note>Ödeme : {escape(_lbl)} {_amt_str} TL</cbc:Note>")
        if note:
            notes_xml.append(f"<cbc:Note>{escape(note)}</cbc:Note>")
        notes_xml.append("<cbc:Note>Bu Satış Internet Üzerinden Yapılmıştır.</cbc:Note>")

        # ─── XML İskeleti ────────────────────────────────────────────────
        supplier_website_xml = (f"<cbc:WebsiteURI>{escape(supplier_website)}</cbc:WebsiteURI>"
                                if supplier_website else "")
        supplier_contact_xml = "<cac:Contact/>"
        if supplier_phone or supplier_email:
            tel_xml = f"<cbc:Telephone>{escape(supplier_phone)}</cbc:Telephone>" if supplier_phone else ""
            mail_xml = f"<cbc:ElectronicMail>{escape(supplier_email)}</cbc:ElectronicMail>" if supplier_email else ""
            supplier_contact_xml = f"<cac:Contact>{tel_xml}{mail_xml}</cac:Contact>"

        import uuid as _uuid
        xslt_ref_id = str(_uuid.uuid4())
        sending_ref_id = str(_uuid.uuid4())

        xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Invoice xmlns="urn:oasis:names:specification:ubl:schema:xsd:Invoice-2" xmlns:ext="urn:oasis:names:specification:ubl:schema:xsd:CommonExtensionComponents-2" xmlns:qdt="urn:oasis:names:specification:ubl:schema:xsd:QualifiedDatatypes-2" xmlns:ccts="urn:un:unece:uncefact:documentation:2" xmlns:xades="http://uri.etsi.org/01903/v1.3.2#" xmlns:ubltr="urn:oasis:names:specification:ubl:schema:xsd:TurkishCustomizationExtensionComponents" xmlns:cac="urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2" xmlns:udt="urn:un:unece:uncefact:data:specification:UnqualifiedDataTypesSchemaModule:2" xmlns:cbc="urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2" xmlns:ds="http://www.w3.org/2000/09/xmldsig#" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:schemaLocation="urn:oasis:names:specification:ubl:schema:xsd:Invoice-2 UBL-Invoice-2.1.xsd">
  <cbc:UBLVersionID>2.1</cbc:UBLVersionID>
  <cbc:CustomizationID>TR1.2</cbc:CustomizationID>
  <cbc:ProfileID>EARSIVFATURA</cbc:ProfileID>
  <cbc:ID>{escape(invoice_number)}</cbc:ID>
  <cbc:CopyIndicator>false</cbc:CopyIndicator>
  <cbc:UUID>{escape(invoice_uuid)}</cbc:UUID>
  <cbc:IssueDate>{issue_date}</cbc:IssueDate>
  <cbc:IssueTime>{issue_time}</cbc:IssueTime>
  <cbc:InvoiceTypeCode>SATIS</cbc:InvoiceTypeCode>
  {''.join(notes_xml)}
  <cbc:DocumentCurrencyCode>{currency}</cbc:DocumentCurrencyCode>
  <cbc:LineCountNumeric>{len(invoice_lines_xml)}</cbc:LineCountNumeric>
  <cac:AdditionalDocumentReference>
    <cbc:ID>{xslt_ref_id}</cbc:ID>
    <cbc:IssueDate>{issue_date}</cbc:IssueDate>
    <cbc:DocumentType>XSLT</cbc:DocumentType>
    <cac:Attachment>
      <cbc:EmbeddedDocumentBinaryObject characterSetCode="UTF-8" encodingCode="Base64" filename="{escape(invoice_number)}.xslt" mimeCode="application/CSTAdata+xml">{DOGAN_XSLT_B64}</cbc:EmbeddedDocumentBinaryObject>
    </cac:Attachment>
  </cac:AdditionalDocumentReference>
  <cac:AdditionalDocumentReference>
    <cbc:ID>{sending_ref_id}</cbc:ID>
    <cbc:IssueDate>{issue_date}</cbc:IssueDate>
    <cbc:DocumentTypeCode>SendingType</cbc:DocumentTypeCode>
    <cbc:DocumentType>ELEKTRONIK</cbc:DocumentType>
  </cac:AdditionalDocumentReference>
  <cac:Signature>
    <cbc:ID schemeID="VKN_TCKN">{escape(supplier_vkn)}</cbc:ID>
    <cac:SignatoryParty>
      <cac:PartyIdentification>
        <cbc:ID schemeID="VKN">{escape(supplier_vkn)}</cbc:ID>
      </cac:PartyIdentification>
      <cac:PartyName>
        <cbc:Name>{escape(supplier_name)}</cbc:Name>
      </cac:PartyName>
      <cac:PostalAddress>
        <cbc:ID schemeID="VKN">{escape(supplier_vkn)}</cbc:ID>
        {_sup_street_line}<cbc:CitySubdivisionName>{escape(supplier_district or 'Küçükçekmece')}</cbc:CitySubdivisionName>
        <cbc:CityName>{escape(supplier_city)}</cbc:CityName>
        <cac:Country>
          <cbc:Name>{escape(supplier_country)}</cbc:Name>
        </cac:Country>
      </cac:PostalAddress>
      <cac:PartyTaxScheme>
        <cac:TaxScheme>
          <cbc:Name>{escape(supplier_tax_office)}</cbc:Name>
        </cac:TaxScheme>
      </cac:PartyTaxScheme>
      <cac:Contact/>
    </cac:SignatoryParty>
    <cac:DigitalSignatureAttachment>
      <cac:ExternalReference>
        <cbc:URI>#Signature_{escape(invoice_number)}</cbc:URI>
      </cac:ExternalReference>
    </cac:DigitalSignatureAttachment>
  </cac:Signature>
  <cac:AccountingSupplierParty>
    <cac:Party>
      {supplier_website_xml}
      <cac:PartyIdentification>
        <cbc:ID schemeID="VKN">{escape(supplier_vkn)}</cbc:ID>
      </cac:PartyIdentification>
      <cac:PartyName>
        <cbc:Name>{escape(supplier_name)}</cbc:Name>
      </cac:PartyName>
      <cac:PostalAddress>
        <cbc:ID schemeID="VKN">{escape(supplier_vkn)}</cbc:ID>
        {_sup_street_line}<cbc:CitySubdivisionName>{escape(supplier_district or 'Küçükçekmece')}</cbc:CitySubdivisionName>
        <cbc:CityName>{escape(supplier_city)}</cbc:CityName>
        <cac:Country>
          <cbc:Name>{escape(supplier_country)}</cbc:Name>
        </cac:Country>
      </cac:PostalAddress>
      <cac:PartyTaxScheme>
        <cac:TaxScheme>
          <cbc:Name>{escape(supplier_tax_office)}</cbc:Name>
        </cac:TaxScheme>
      </cac:PartyTaxScheme>
      {supplier_contact_xml}
    </cac:Party>
  </cac:AccountingSupplierParty>
  <cac:AccountingCustomerParty>
    <cac:Party>
      <cac:PartyIdentification>
        <cbc:ID schemeID="{party_id_scheme}">{escape(customer_vkn_or_tckn)}</cbc:ID>
      </cac:PartyIdentification>
      {customer_legal_block}
      <cac:PostalAddress>
        {customer_street_xml}
        <cbc:CitySubdivisionName>{escape(customer_district or '-')}</cbc:CitySubdivisionName>
        <cbc:CityName>{escape(customer_city)}</cbc:CityName>
        {customer_zone_xml}
        <cac:Country>
          <cbc:Name>{escape(customer_country)}</cbc:Name>
        </cac:Country>
      </cac:PostalAddress>
      {customer_tax_scheme_block}
      {customer_contact_xml}
      {customer_name_block}
    </cac:Party>
  </cac:AccountingCustomerParty>
  <cac:Delivery>
    <cac:CarrierParty>
      <cac:PartyIdentification><cbc:ID schemeID="VKN">{escape((carrier_vkn or '').strip() or '6080712084')}</cbc:ID></cac:PartyIdentification>
      <cac:PartyName>
        <cbc:Name>{escape(carrier_name)}</cbc:Name>
      </cac:PartyName>
      <cac:PostalAddress>
        <cbc:CitySubdivisionName/>
        <cbc:CityName>{escape(carrier_city)}</cbc:CityName>
        <cac:Country>
          <cbc:Name>Türkiye</cbc:Name>
        </cac:Country>
      </cac:PostalAddress>
    </cac:CarrierParty>
    <cac:Despatch>
      <cbc:ActualDespatchDate>{issue_date}</cbc:ActualDespatchDate>
      <cbc:ActualDespatchTime>{issue_time}</cbc:ActualDespatchTime>
    </cac:Despatch>
  </cac:Delivery>
  <cac:PaymentMeans>
    <cbc:PaymentMeansCode>1</cbc:PaymentMeansCode>
    <cbc:PaymentDueDate>{issue_date}</cbc:PaymentDueDate>
    <cbc:InstructionNote>Odeme Tipi : {escape(payment_method)} - Web Adresi : {escape(supplier_website or 'facette.com.tr')}</cbc:InstructionNote>
  </cac:PaymentMeans>
  {''.join(allowance_charges_xml)}
  <cac:TaxTotal>
    <cbc:TaxAmount currencyID="{currency}">{kdv_total:.2f}</cbc:TaxAmount>
    {''.join(tax_subtotals_xml)}
  </cac:TaxTotal>
  <cac:LegalMonetaryTotal>
    <cbc:LineExtensionAmount currencyID="{currency}">{line_subtotal:.2f}</cbc:LineExtensionAmount>
    <cbc:TaxExclusiveAmount currencyID="{currency}">{line_subtotal:.2f}</cbc:TaxExclusiveAmount>
    <cbc:TaxInclusiveAmount currencyID="{currency}">{tax_inclusive_total:.2f}</cbc:TaxInclusiveAmount>
    <cbc:PayableAmount currencyID="{currency}">{payable_amount:.2f}</cbc:PayableAmount>
  </cac:LegalMonetaryTotal>
  {''.join(invoice_lines_xml)}
</Invoice>"""
        return xml

    @staticmethod
    def build_earsiv_export_ubl_xml(*,
                              invoice_uuid: str,
                              invoice_number: str,
                              issue_date: str,            # YYYY-MM-DD
                              issue_time: str,            # HH:MM:SS
                              supplier_vkn: str,
                              supplier_name: str,
                              platform_label: str = "",
                              supplier_district: str = "",
                              supplier_city: str = "",
                              supplier_street: str = "",
                              supplier_country: str = "Türkiye",
                              supplier_tax_office: str = "",
                              supplier_phone: str = "",
                              supplier_email: str = "",
                              supplier_website: str = "",
                              customer_vkn_or_tckn: str,  # 11 haneli TCKN veya 10 haneli VKN
                              customer_name: str,
                              customer_district: str = "",
                              customer_city: str = "",
                              customer_street: str = "",
                              customer_country: str = "Türkiye",
                              customer_postal_zone: str = "",
                              customer_phone: str = "",
                              customer_email: str = "",
                              customer_tax_office: str = "",
                              currency: str = "TRY",
                              kdv_rate: float = 20.0,
                              line_items: list = None,    # [{name, qty, unit_price, kdv_rate, sku, note, barcode}]
                              shipping_cost: float = 0.0,
                              discount: float = 0.0,
                              free_shipping_waived: float = 0.0,
                              note: str = "",
                              order_number: str = "",
                              payment_method: str = "",
                              carrier_vkn: str = "6080712084",
                              carrier_name: str = "MNG KARGO YURTİÇİ VE YURTDIŞI TAŞIMACILIK A.Ş.",
                              carrier_city: str = "İstanbul",
                              cargo_tracking: str = "",
                              order_ext_id: str = "",
                              store_name: str = "",
                              payment_amount: float = 0.0,
                              ) -> str:
        """UBL-TR 1.2 e-Arşiv MİKRO İHRACAT (İSTİSNA) Fatura XML üretici. KDV %0 / istisna 301.

        Bireysel müşteri (TCKN 11 hane) ve kurumsal (VKN 10 hane) destekler.
        FCT2026000011227.xml örnek dosyasına birebir şema uyumudur:
          • Tam namespace seti (ubltr, ds, xades, qdt, ccts, udt)
          • cac:Signature bloğu (UBL-TR'de zorunlu)
          • cac:AdditionalDocumentReference > SendingType=ELEKTRONIK
          • InvoicedQuantity unitCode="C62" (UBL-TR adet kodu)
          • cac:PaymentMeans, cac:Delivery (opsiyonel ama Doğan örneğinde mevcut)
          • SellersItemIdentification her satırda
        Tüm tutarlar KDV hariç. Satır toplamı + KDV + kargo - indirim = genel toplam.
        """
        from html import escape

        line_items = line_items or []
        is_individual = len(customer_vkn_or_tckn) == 11
        party_id_scheme = "TCKN" if is_individual else "VKN"

        # null/None safety — UBL'de "None" string'i şema hatası verir
        def _s(v):
            if v is None or str(v).lower() == "none":
                return ""
            return str(v).strip()

        supplier_phone = _s(supplier_phone)
        supplier_email = _s(supplier_email)
        supplier_website = _s(supplier_website)
        supplier_street = _s(supplier_street)
        _sup_street_line = f"<cbc:StreetName>{escape(supplier_street)}</cbc:StreetName>" if supplier_street else ""
        supplier_district = _s(supplier_district)
        supplier_city = _s(supplier_city) or "İstanbul"
        supplier_tax_office = _s(supplier_tax_office) or "HALKALI VERGİ DAİRESİ BAŞKANLIĞI"
        customer_phone = _s(customer_phone)
        customer_email = _s(customer_email)
        customer_street = _s(customer_street)
        customer_district = _s(customer_district)
        customer_city = _s(customer_city) or "İstanbul"
        customer_name = _s(customer_name) or "Bireysel Müşteri"
        customer_tax_office = _s(customer_tax_office)
        customer_postal_zone = _s(customer_postal_zone)
        # #5: açık adres (StreetName) ve posta kodu (PostalZone) — örnek faturada var, eksikti
        customer_street_xml = f"<cbc:StreetName>{escape(customer_street)}</cbc:StreetName>" if customer_street else ""
        customer_zone_xml = f"<cbc:PostalZone>{escape(customer_postal_zone)}</cbc:PostalZone>" if customer_postal_zone else ""
        note = _s(note)
        order_number = _s(order_number)
        payment_method = _s(payment_method) or "DIGER"

        # ─── InvoiceLine'lar — UBL-TR unitCode "C62" (Adet) ──────────────
        invoice_lines_xml = []
        line_subtotal = 0.0
        kdv_total = 0.0
        # KDV oranı bazlı gruplandırma (TaxTotal'da multi-subtotal için)
        kdv_groups = {}  # rate → {"taxable": x, "tax": y}

        for idx, it in enumerate(line_items, start=1):
            qty = float(it.get("qty") or 1)
            unit_price = float(it.get("unit_price") or 0)
            line_amount = round(qty * unit_price, 2)
            li_kdv_rate = 0.0  # ihracat istisnasi: KDV %0
            line_kdv = round(line_amount * li_kdv_rate / 100.0, 2)
            line_subtotal += line_amount
            kdv_total += line_kdv
            grp = kdv_groups.setdefault(li_kdv_rate, {"taxable": 0.0, "tax": 0.0})
            grp["taxable"] += line_amount
            grp["tax"] += line_kdv

            name = escape((it.get("name") or "Ürün"))[:255]
            sku = escape(_s(it.get("sku") or it.get("product_code") or f"URN{idx:04d}"))
            li_note = escape(_s(it.get("note") or ""))  # barkod ARTIK not'a yazilmaz (stok ad altinda gorunmesin)
            note_xml = f"<cbc:Note>{li_note}</cbc:Note>" if li_note else ""

            invoice_lines_xml.append(f"""<cac:InvoiceLine>
    <cbc:ID>{idx}</cbc:ID>
    {note_xml}
    <cbc:InvoicedQuantity unitCode="C62">{qty:g}</cbc:InvoicedQuantity>
    <cbc:LineExtensionAmount currencyID="{currency}">{line_amount:.2f}</cbc:LineExtensionAmount>
    <cac:TaxTotal>
      <cbc:TaxAmount currencyID="{currency}">{line_kdv:.2f}</cbc:TaxAmount>
      <cac:TaxSubtotal>
        <cbc:TaxableAmount currencyID="{currency}">{line_amount:.2f}</cbc:TaxableAmount>
        <cbc:TaxAmount currencyID="{currency}">{line_kdv:.2f}</cbc:TaxAmount>
        <cbc:Percent>{li_kdv_rate:g}</cbc:Percent>
        <cac:TaxCategory>
          <cbc:TaxExemptionReasonCode>301</cbc:TaxExemptionReasonCode>
          <cbc:TaxExemptionReason>11/1 - a Mal ihracatı</cbc:TaxExemptionReason>
          <cac:TaxScheme>
            <cbc:Name>GERÇEK USULDE KATMA DEĞER VERGİSİ</cbc:Name>
            <cbc:TaxTypeCode>0015</cbc:TaxTypeCode>
          </cac:TaxScheme>
        </cac:TaxCategory>
      </cac:TaxSubtotal>
    </cac:TaxTotal>
    <cac:Item>
      <cbc:Name>{name}</cbc:Name>
      <cac:SellersItemIdentification>
        <cbc:ID>{sku}</cbc:ID>
      </cac:SellersItemIdentification>
      <cac:OriginCountry>
        <cbc:Name>Türkiye</cbc:Name>
      </cac:OriginCountry>
    </cac:Item>
    <cac:Price>
      <cbc:PriceAmount currencyID="{currency}">{unit_price:.4f}</cbc:PriceAmount>
    </cac:Price>
  </cac:InvoiceLine>""")

        # Kargo bedeli — ayrı InvoiceLine olarak eklenir (sample böyle yapıyor)
        if shipping_cost > 0:
            sh_kdv_rate = 0.0  # ihracat istisnasi
            sh_kdv = round(shipping_cost * sh_kdv_rate / 100.0, 2)
            sh_taxable = round(shipping_cost - sh_kdv, 2)
            kdv_total += sh_kdv
            line_subtotal += sh_taxable
            grp = kdv_groups.setdefault(sh_kdv_rate, {"taxable": 0.0, "tax": 0.0})
            grp["taxable"] += sh_taxable
            grp["tax"] += sh_kdv
            next_idx = len(line_items) + 1
            invoice_lines_xml.append(f"""<cac:InvoiceLine>
    <cbc:ID>{next_idx}</cbc:ID>
    <cbc:InvoicedQuantity unitCode="C62">1</cbc:InvoicedQuantity>
    <cbc:LineExtensionAmount currencyID="{currency}">{sh_taxable:.2f}</cbc:LineExtensionAmount>
    <cac:TaxTotal>
      <cbc:TaxAmount currencyID="{currency}">{sh_kdv:.2f}</cbc:TaxAmount>
      <cac:TaxSubtotal>
        <cbc:TaxableAmount currencyID="{currency}">{sh_taxable:.2f}</cbc:TaxableAmount>
        <cbc:TaxAmount currencyID="{currency}">{sh_kdv:.2f}</cbc:TaxAmount>
        <cbc:Percent>{sh_kdv_rate:g}</cbc:Percent>
        <cac:TaxCategory>
          <cbc:TaxExemptionReasonCode>301</cbc:TaxExemptionReasonCode>
          <cbc:TaxExemptionReason>11/1 - a Mal ihracatı</cbc:TaxExemptionReason>
          <cac:TaxScheme>
            <cbc:Name>GERÇEK USULDE KATMA DEĞER VERGİSİ</cbc:Name>
            <cbc:TaxTypeCode>0015</cbc:TaxTypeCode>
          </cac:TaxScheme>
        </cac:TaxCategory>
      </cac:TaxSubtotal>
    </cac:TaxTotal>
    <cac:Item>
      <cbc:Name>KARGO</cbc:Name>
      <cac:SellersItemIdentification><cbc:ID>KARGO</cbc:ID></cac:SellersItemIdentification>
    </cac:Item>
    <cac:Price>
      <cbc:PriceAmount currencyID="{currency}">{sh_taxable:.4f}</cbc:PriceAmount>
    </cac:Price>
  </cac:InvoiceLine>""")

        # İndirim — AllowanceCharge bloğu (root seviyesinde, TaxTotal öncesinde)
        # Ücretsiz kargo kampanyası İSKONTO satırı (net 0, GİB-güvenli — toplam/matrah değişmez).
        if free_shipping_waived and float(free_shipping_waived) > 0 and (shipping_cost or 0) <= 0:
            _fsl = _free_shipping_line_xml(len(invoice_lines_xml) + 1, float(free_shipping_waived), currency)
            if _fsl:
                invoice_lines_xml.append(_fsl)
        allowance_charges_xml = []
        if discount > 0:
            line_subtotal -= discount
            allowance_charges_xml.append(f"""<cac:AllowanceCharge>
    <cbc:ChargeIndicator>false</cbc:ChargeIndicator>
    <cbc:AllowanceChargeReason>İndirim</cbc:AllowanceChargeReason>
    <cbc:Amount currencyID="{currency}">{discount:.2f}</cbc:Amount>
  </cac:AllowanceCharge>""")

        tax_inclusive_total = round(line_subtotal + kdv_total, 2)
        payable_amount = tax_inclusive_total

        # KDV TaxSubtotal blokları (multi-rate destekler)
        tax_subtotals_xml = []
        for rate, grp in sorted(kdv_groups.items()):
            tax_subtotals_xml.append(f"""<cac:TaxSubtotal>
      <cbc:TaxableAmount currencyID="{currency}">{grp['taxable']:.2f}</cbc:TaxableAmount>
      <cbc:TaxAmount currencyID="{currency}">{grp['tax']:.2f}</cbc:TaxAmount>
      <cbc:Percent>{rate:g}</cbc:Percent>
      <cac:TaxCategory>
        <cbc:TaxExemptionReasonCode>301</cbc:TaxExemptionReasonCode>
        <cbc:TaxExemptionReason>11/1 - a Mal ihracatı</cbc:TaxExemptionReason>
        <cac:TaxScheme>
          <cbc:Name>GERÇEK USULDE KATMA DEĞER VERGİSİ</cbc:Name>
          <cbc:TaxTypeCode>0015</cbc:TaxTypeCode>
        </cac:TaxScheme>
      </cac:TaxCategory>
    </cac:TaxSubtotal>""")

        # ─── AccountingCustomerParty ─────────────────────────────────────
        if is_individual:
            parts = (customer_name or "").strip().split(" ", 1)
            first_name = escape(parts[0])
            last_name = escape(parts[1] if len(parts) > 1 else parts[0])
            customer_name_block = f"""<cac:Person>
        <cbc:FirstName>{first_name}</cbc:FirstName>
        <cbc:FamilyName>{last_name}</cbc:FamilyName>
      </cac:Person>"""
            customer_legal_block = ""
            customer_tax_scheme_block = ""
        else:
            customer_name_block = ""
            customer_legal_block = f"""<cac:PartyName>
        <cbc:Name>{escape(customer_name)}</cbc:Name>
      </cac:PartyName>"""
            customer_tax_scheme_block = f"""<cac:PartyTaxScheme>
        <cac:TaxScheme>
          <cbc:Name>{escape(customer_tax_office or '-')}</cbc:Name>
        </cac:TaxScheme>
      </cac:PartyTaxScheme>"""

        customer_contact_xml = ""
        if customer_phone or customer_email:
            tel_xml = f"<cbc:Telephone>{escape(customer_phone)}</cbc:Telephone>" if customer_phone else ""
            mail_xml = f"<cbc:ElectronicMail>{escape(customer_email)}</cbc:ElectronicMail>" if customer_email else ""
            customer_contact_xml = f"<cac:Contact>{tel_xml}{mail_xml}</cac:Contact>"
        else:
            customer_contact_xml = "<cac:Contact/>"

        # ─── Notlar (sample birden fazla Note kullanıyor) ────────────────
        def _tr_money_words(n):
            n = int(round(float(n or 0)))
            birler = ["", "Bir", "İki", "Üç", "Dört", "Beş", "Altı", "Yedi", "Sekiz", "Dokuz"]
            onlar = ["", "On", "Yirmi", "Otuz", "Kırk", "Elli", "Altmış", "Yetmiş", "Seksen", "Doksan"]
            def _uc(x):
                s = ""; y = x // 100; k = (x % 100) // 10; b = x % 10
                if y: s += ("" if y == 1 else birler[y]) + "Yüz"
                if k: s += onlar[k]
                if b: s += birler[b]
                return s
            if n == 0: return "Sıfır"
            out = ""; mr = n // 10**9; mn = (n % 10**9) // 10**6; bn = (n % 10**6) // 1000; kl = n % 1000
            if mr: out += _uc(mr) + "Milyar"
            if mn: out += _uc(mn) + "Milyon"
            if bn: out += (("" if bn == 1 else _uc(bn)) + "Bin")
            if kl: out += _uc(kl)
            return out

        notes_xml = []
        if order_number:
            notes_xml.append(f"<cbc:Note>{escape(order_number)}</cbc:Note>")
            notes_xml.append(f"<cbc:Note>Siparis No: {escape(order_number)} :Kargo Takip No: {escape(cargo_tracking)} :Sipariş ID: {escape(order_ext_id)}</cbc:Note>")
        _satici_adr = (supplier_street or "").strip()
        if _satici_adr:
            notes_xml.append(f"<cbc:Note>Taraf : Satıcı; {escape(_satici_adr)}</cbc:Note>")
        # Alıcı açık adresi NOT olarak EKLENMEZ: adres zaten cac:PostalAddress'ten
        # (StreetName + "ilçe / il") basılıyor. "Taraf : Alıcı; <adres>" notu eklenince
        # adres faturada İKİ KEZ görünüyordu (site siparişinde tam adres dolu → belirgin;
        # marketplace'te adres maskeli geldiği için orada fark edilmiyordu).
        notes_xml.append(f"<cbc:Note>Yalnız {_tr_money_words(payable_amount)} Lira</cbc:Note>")
        if store_name:
            notes_xml.append(f"<cbc:Note>Mağaza Adı :{escape(store_name)}</cbc:Note>")
        if payment_method or platform_label:
            _lbl = (platform_label or payment_method).strip()
            _amt = payment_amount if payment_amount else payable_amount
            _amt_str = f"{_amt:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
            notes_xml.append(f"<cbc:Note>Ödeme : {escape(_lbl)} {_amt_str} TL</cbc:Note>")
        if note:
            notes_xml.append(f"<cbc:Note>{escape(note)}</cbc:Note>")
        notes_xml.append("<cbc:Note>Bu Satış Internet Üzerinden Yapılmıştır.</cbc:Note>")

        # ─── XML İskeleti ────────────────────────────────────────────────
        supplier_website_xml = (f"<cbc:WebsiteURI>{escape(supplier_website)}</cbc:WebsiteURI>"
                                if supplier_website else "")
        supplier_contact_xml = "<cac:Contact/>"
        if supplier_phone or supplier_email:
            tel_xml = f"<cbc:Telephone>{escape(supplier_phone)}</cbc:Telephone>" if supplier_phone else ""
            mail_xml = f"<cbc:ElectronicMail>{escape(supplier_email)}</cbc:ElectronicMail>" if supplier_email else ""
            supplier_contact_xml = f"<cac:Contact>{tel_xml}{mail_xml}</cac:Contact>"

        import uuid as _uuid
        xslt_ref_id = str(_uuid.uuid4())
        sending_ref_id = str(_uuid.uuid4())

        xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Invoice xmlns="urn:oasis:names:specification:ubl:schema:xsd:Invoice-2" xmlns:ext="urn:oasis:names:specification:ubl:schema:xsd:CommonExtensionComponents-2" xmlns:qdt="urn:oasis:names:specification:ubl:schema:xsd:QualifiedDatatypes-2" xmlns:ccts="urn:un:unece:uncefact:documentation:2" xmlns:xades="http://uri.etsi.org/01903/v1.3.2#" xmlns:ubltr="urn:oasis:names:specification:ubl:schema:xsd:TurkishCustomizationExtensionComponents" xmlns:cac="urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2" xmlns:udt="urn:un:unece:uncefact:data:specification:UnqualifiedDataTypesSchemaModule:2" xmlns:cbc="urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2" xmlns:ds="http://www.w3.org/2000/09/xmldsig#" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:schemaLocation="urn:oasis:names:specification:ubl:schema:xsd:Invoice-2 UBL-Invoice-2.1.xsd">
  <cbc:UBLVersionID>2.1</cbc:UBLVersionID>
  <cbc:CustomizationID>TR1.2</cbc:CustomizationID>
  <cbc:ProfileID>EARSIVFATURA</cbc:ProfileID>
  <cbc:ID>{escape(invoice_number)}</cbc:ID>
  <cbc:CopyIndicator>false</cbc:CopyIndicator>
  <cbc:UUID>{escape(invoice_uuid)}</cbc:UUID>
  <cbc:IssueDate>{issue_date}</cbc:IssueDate>
  <cbc:IssueTime>{issue_time}</cbc:IssueTime>
  <cbc:InvoiceTypeCode>ISTISNA</cbc:InvoiceTypeCode>
  {''.join(notes_xml)}
  <cbc:DocumentCurrencyCode>{currency}</cbc:DocumentCurrencyCode>
  <cbc:LineCountNumeric>{len(invoice_lines_xml)}</cbc:LineCountNumeric>
  <cac:AdditionalDocumentReference>
    <cbc:ID>{xslt_ref_id}</cbc:ID>
    <cbc:IssueDate>{issue_date}</cbc:IssueDate>
    <cbc:DocumentType>XSLT</cbc:DocumentType>
    <cac:Attachment>
      <cbc:EmbeddedDocumentBinaryObject characterSetCode="UTF-8" encodingCode="Base64" filename="{escape(invoice_number)}.xslt" mimeCode="application/CSTAdata+xml">{DOGAN_XSLT_B64}</cbc:EmbeddedDocumentBinaryObject>
    </cac:Attachment>
  </cac:AdditionalDocumentReference>
  <cac:AdditionalDocumentReference>
    <cbc:ID>{sending_ref_id}</cbc:ID>
    <cbc:IssueDate>{issue_date}</cbc:IssueDate>
    <cbc:DocumentTypeCode>SendingType</cbc:DocumentTypeCode>
    <cbc:DocumentType>ELEKTRONIK</cbc:DocumentType>
  </cac:AdditionalDocumentReference>
  <cac:Signature>
    <cbc:ID schemeID="VKN_TCKN">{escape(supplier_vkn)}</cbc:ID>
    <cac:SignatoryParty>
      <cac:PartyIdentification>
        <cbc:ID schemeID="VKN">{escape(supplier_vkn)}</cbc:ID>
      </cac:PartyIdentification>
      <cac:PartyName>
        <cbc:Name>{escape(supplier_name)}</cbc:Name>
      </cac:PartyName>
      <cac:PostalAddress>
        <cbc:ID schemeID="VKN">{escape(supplier_vkn)}</cbc:ID>
        {_sup_street_line}<cbc:CitySubdivisionName>{escape(supplier_district or 'Küçükçekmece')}</cbc:CitySubdivisionName>
        <cbc:CityName>{escape(supplier_city)}</cbc:CityName>
        <cac:Country>
          <cbc:Name>{escape(supplier_country)}</cbc:Name>
        </cac:Country>
      </cac:PostalAddress>
      <cac:PartyTaxScheme>
        <cac:TaxScheme>
          <cbc:Name>{escape(supplier_tax_office)}</cbc:Name>
        </cac:TaxScheme>
      </cac:PartyTaxScheme>
      <cac:Contact/>
    </cac:SignatoryParty>
    <cac:DigitalSignatureAttachment>
      <cac:ExternalReference>
        <cbc:URI>#Signature_{escape(invoice_number)}</cbc:URI>
      </cac:ExternalReference>
    </cac:DigitalSignatureAttachment>
  </cac:Signature>
  <cac:AccountingSupplierParty>
    <cac:Party>
      {supplier_website_xml}
      <cac:PartyIdentification>
        <cbc:ID schemeID="VKN">{escape(supplier_vkn)}</cbc:ID>
      </cac:PartyIdentification>
      <cac:PartyName>
        <cbc:Name>{escape(supplier_name)}</cbc:Name>
      </cac:PartyName>
      <cac:PostalAddress>
        <cbc:ID schemeID="VKN">{escape(supplier_vkn)}</cbc:ID>
        {_sup_street_line}<cbc:CitySubdivisionName>{escape(supplier_district or 'Küçükçekmece')}</cbc:CitySubdivisionName>
        <cbc:CityName>{escape(supplier_city)}</cbc:CityName>
        <cac:Country>
          <cbc:Name>{escape(supplier_country)}</cbc:Name>
        </cac:Country>
      </cac:PostalAddress>
      <cac:PartyTaxScheme>
        <cac:TaxScheme>
          <cbc:Name>{escape(supplier_tax_office)}</cbc:Name>
        </cac:TaxScheme>
      </cac:PartyTaxScheme>
      {supplier_contact_xml}
    </cac:Party>
  </cac:AccountingSupplierParty>
  <cac:AccountingCustomerParty>
    <cac:Party>
      <cac:PartyIdentification>
        <cbc:ID schemeID="{party_id_scheme}">{escape(customer_vkn_or_tckn)}</cbc:ID>
      </cac:PartyIdentification>
      {customer_legal_block}
      <cac:PostalAddress>
        {customer_street_xml}
        <cbc:CitySubdivisionName>{escape(customer_district or '-')}</cbc:CitySubdivisionName>
        <cbc:CityName>{escape(customer_city)}</cbc:CityName>
        {customer_zone_xml}
        <cac:Country>
          <cbc:Name>{escape(customer_country)}</cbc:Name>
        </cac:Country>
      </cac:PostalAddress>
      {customer_tax_scheme_block}
      {customer_contact_xml}
      {customer_name_block}
    </cac:Party>
  </cac:AccountingCustomerParty>
  <cac:Delivery>
    <cac:CarrierParty>
      <cac:PartyIdentification>
        <cbc:ID schemeID="VKN">{escape((carrier_vkn or '').strip() or '6080712084')}</cbc:ID>
      </cac:PartyIdentification>
      <cac:PartyName>
        <cbc:Name>{escape(carrier_name)}</cbc:Name>
      </cac:PartyName>
      <cac:PostalAddress>
        <cbc:CitySubdivisionName/>
        <cbc:CityName>{escape(carrier_city)}</cbc:CityName>
        <cac:Country>
          <cbc:Name>Türkiye</cbc:Name>
        </cac:Country>
      </cac:PostalAddress>
    </cac:CarrierParty>
    <cac:Despatch>
      <cbc:ActualDespatchDate>{issue_date}</cbc:ActualDespatchDate>
      <cbc:ActualDespatchTime>{issue_time}</cbc:ActualDespatchTime>
    </cac:Despatch>
  </cac:Delivery>
  <cac:PaymentMeans>
    <cbc:PaymentMeansCode>1</cbc:PaymentMeansCode>
    <cbc:PaymentDueDate>{issue_date}</cbc:PaymentDueDate>
    <cbc:InstructionNote>Odeme Tipi : {escape(payment_method)} - Web Adresi : {escape(supplier_website or 'facette.com.tr')}</cbc:InstructionNote>
  </cac:PaymentMeans>
  {''.join(allowance_charges_xml)}
  <cac:TaxTotal>
    <cbc:TaxAmount currencyID="{currency}">{kdv_total:.2f}</cbc:TaxAmount>
    {''.join(tax_subtotals_xml)}
  </cac:TaxTotal>
  <cac:LegalMonetaryTotal>
    <cbc:LineExtensionAmount currencyID="{currency}">{line_subtotal:.2f}</cbc:LineExtensionAmount>
    <cbc:TaxExclusiveAmount currencyID="{currency}">{line_subtotal:.2f}</cbc:TaxExclusiveAmount>
    <cbc:TaxInclusiveAmount currencyID="{currency}">{tax_inclusive_total:.2f}</cbc:TaxInclusiveAmount>
    <cbc:PayableAmount currencyID="{currency}">{payable_amount:.2f}</cbc:PayableAmount>
  </cac:LegalMonetaryTotal>
  {''.join(invoice_lines_xml)}
</Invoice>"""
        return xml


    # ═════════════════ UBL-TR e-Fatura (TICARIFATURA) Üretimi ════════════
    @staticmethod
    def build_efatura_ubl_xml(*,
                                invoice_uuid: str,
                                invoice_number: str,
                                issue_date: str,
                                issue_time: str,
                                supplier_vkn: str,
                                supplier_name: str,
                                platform_label: str = "",
                                supplier_district: str = "KÜÇÜKÇEKMECE",
                                supplier_city: str = "İstanbul",
                                supplier_street: str = "",
                                supplier_country: str = "Türkiye",
                                supplier_tax_office: str = "HALKALI VERGİ DAİRESİ BAŞKANLIĞI",
                                supplier_website: str = "facette.com.tr",
                                customer_vkn: str,
                                customer_name: str,
                                customer_street: str = "",
                                customer_district: str = "",
                                customer_city: str = "İstanbul",
                                customer_postal_zone: str = "",
                                customer_country: str = "Türkiye",
                                customer_email: str = "",
                                currency: str = "TRY",
                                kdv_rate: float = 20.0,
                                line_items: list = None,
                                shipping_cost: float = 0.0,
                                discount: float = 0.0,
                              free_shipping_waived: float = 0.0,
                                order_number: str = "",
                                order_date: str = "",
                                profile_id: str = "TICARIFATURA",
                                customer_id_scheme: str = "",
                                customer_tax_office: str = "",
                                customer_first_name: str = "",
                                customer_family_name: str = "",
                                payment_method: str = "",
                                payment_amount: float = 0.0,
                                cargo_tracking: str = "",
                                carrier_name: str = "",
                                carrier_vkn: str = "",
                                carrier_type: str = "Tüzel",
                                store_name: str = "",
                                order_ext_id: str = "",
                                dispatch_date: str = "",
                                invoice_ref: str = "",
                                ) -> str:
        """UBL-TR 1.2 e-Fatura (varsayılan TICARIFATURA) — kurumsal alıcılar için.

        Örnek EFC2026000000049.xml referansı:
          • ProfileID=TICARIFATURA (varsayılan; alıcı 8 gün içinde kabul/red edebilir)
          • cac:OrderReference (sipariş no + tarih)
          • cac:BuyerCustomerParty (alıcı ayrı blok)
          • cac:Delivery>DeliveryAddress (CarrierParty yok)
          • cac:PaymentMeans yok
          • InvoiceLine: BuyersItemIdentification + SellersItemIdentification
          • Customer kesinlikle 10 haneli VKN olmalı
        """
        from html import escape
        import uuid as _uuid

        import re as _re
        line_items = line_items or []
        _cid = str(customer_vkn or "").strip().replace(" ", "")
        if len(_cid) not in (10, 11):
            raise ValueError(f"e-Fatura için 10 (VKN) veya 11 (TCKN) haneli kimlik gerekli, alındı: {customer_vkn} ({len(_cid)} hane)")
        customer_vkn = _cid
        cust_id_scheme = (customer_id_scheme or ("TCKN" if len(_cid) == 11 else "VKN")).upper()
        _ctax = (customer_tax_office or "").strip()
        _cust_tax_xml = (f"<cac:PartyTaxScheme><cac:TaxScheme><cbc:Name>{escape(_ctax)}</cbc:Name></cac:TaxScheme></cac:PartyTaxScheme>"
                         if (cust_id_scheme == "VKN" and _ctax) else "")

        def _s(v):
            if v is None or str(v).lower() == "none":
                return ""
            return str(v).strip()

        def _clean(v):
            return _re.sub(r"\s+", " ", _s(v)).strip()

        def _clean_addr(street, district, city):
            s = _clean(street); d = _clean(district); c = _clean(city)
            if d and c:
                s = _re.sub(r"(?:\s*" + _re.escape(d) + r"\s+" + _re.escape(c) + r")+\s*$", "", s, flags=_re.IGNORECASE).strip()
            return _clean(s)

        supplier_website = _s(supplier_website)
        customer_street = _s(customer_street)
        _sup_street_line = f"<cbc:StreetName>{escape(_s(supplier_street))}</cbc:StreetName>" if _s(supplier_street) else ""
        customer_district = _s(customer_district)
        customer_city = _s(customer_city) or "İstanbul"
        customer_postal_zone = _s(customer_postal_zone) or "34000"
        customer_email = _s(customer_email)
        customer_name = _s(customer_name) or "Müşteri"
        order_number = _s(order_number)
        order_date = _s(order_date) or issue_date
        _cust_street_clean = _clean_addr(customer_street, customer_district, customer_city)
        _has_person = bool(_s(customer_first_name) or _s(customer_family_name))

        # InvoiceLine'lar — C62 unitCode
        invoice_lines_xml = []
        line_subtotal = 0.0
        kdv_total = 0.0
        kdv_groups = {}
        for idx, it in enumerate(line_items, start=1):
            qty = float(it.get("qty") or 1)
            unit_price = float(it.get("unit_price") or 0)
            li_kdv_rate = float(it.get("kdv_rate") if it.get("kdv_rate") is not None else kdv_rate)
            gross_line = round(qty * unit_price, 2)
            line_amount = round(gross_line / (1.0 + li_kdv_rate / 100.0), 2)
            line_kdv = round(gross_line - line_amount, 2)  # KDV = brüt - net → ödenen tutara birebir
            net_unit_price = round(line_amount / qty, 4) if qty else line_amount
            line_subtotal += line_amount
            kdv_total += line_kdv
            grp = kdv_groups.setdefault(li_kdv_rate, {"taxable": 0.0, "tax": 0.0})
            grp["taxable"] += line_amount
            grp["tax"] += line_kdv

            name = escape(_clean(it.get("name") or "Ürün"))[:255]
            _sku_raw = _clean(it.get("sku") or it.get("product_code") or f"URN{idx:04d}")
            sku = escape(_sku_raw)
            buyer_sku = escape(_clean(it.get("buyer_sku") or it.get("sku") or _sku_raw))
            _barcode = _clean(it.get("barcode"))
            li_note = escape(_clean(it.get("note")))   # barkod ARTIK not'a değil, kendi hanesine
            note_xml = f"<cbc:Note>{li_note}</cbc:Note>" if li_note else ""
            # GİB UBL-TR Item şeması 'StandardItemIdentification'ı KABUL ETMEZ → e-Fatura
            # "INVALID XML! cvc-complex-type.2.4a: Expected ... AdditionalItemIdentification"
            # hatası veriyordu. Barkod/GTIN, GİB Item'ında GEÇERLİ olan
            # AdditionalItemIdentification ile verilir (tutar/vergi etkilenmez).
            std_item_xml = (f"""
      <cac:AdditionalItemIdentification>
        <cbc:ID schemeID="GTIN">{escape(_barcode)}</cbc:ID>
      </cac:AdditionalItemIdentification>""" if _barcode else "")
            # NOT: AdditionalItemProperty (Renk/Beden) EKLENMEZ — GİB UBL-TR Item şeması bu
            # pozisyonda kabul etmiyor ("INVALID XML: Expected AdditionalItemIdentification /
            # OriginCountry"). Sütun doldurma denemesi faturayı bozuyordu → geri alındı.
            # Fatura ÇALIŞIR halde; Renk/Beden bilgisi fatura-altı Note'ta zaten var.
            item_props_xml = ""

            invoice_lines_xml.append(f"""<cac:InvoiceLine>
    <cbc:ID>{idx}</cbc:ID>
    {note_xml}
    <cbc:InvoicedQuantity unitCode="C62">{qty:g}</cbc:InvoicedQuantity>
    <cbc:LineExtensionAmount currencyID="{currency}">{line_amount:.2f}</cbc:LineExtensionAmount>
    <cac:TaxTotal>
      <cbc:TaxAmount currencyID="{currency}">{line_kdv:.2f}</cbc:TaxAmount>
      <cac:TaxSubtotal>
        <cbc:TaxableAmount currencyID="{currency}">{line_amount:.2f}</cbc:TaxableAmount>
        <cbc:TaxAmount currencyID="{currency}">{line_kdv:.2f}</cbc:TaxAmount>
        <cbc:Percent>{li_kdv_rate:g}</cbc:Percent>
        <cac:TaxCategory>
          <cac:TaxScheme>
            <cbc:Name>GERÇEK USULDE KATMA DEĞER VERGİSİ</cbc:Name>
            <cbc:TaxTypeCode>0015</cbc:TaxTypeCode>
          </cac:TaxScheme>
        </cac:TaxCategory>
      </cac:TaxSubtotal>
    </cac:TaxTotal>
    <cac:Item>
      <cbc:Name>{name}</cbc:Name>
      <cac:BuyersItemIdentification>
        <cbc:ID>{buyer_sku}</cbc:ID>
      </cac:BuyersItemIdentification>
      <cac:SellersItemIdentification>
        <cbc:ID>{sku}</cbc:ID>
      </cac:SellersItemIdentification>{std_item_xml}{item_props_xml}
    </cac:Item>
    <cac:Price>
      <cbc:PriceAmount currencyID="{currency}">{net_unit_price:.4f}</cbc:PriceAmount>
    </cac:Price>
  </cac:InvoiceLine>""")

        # Kargo ayrı satır olarak (e-Fatura'da da)
        if shipping_cost > 0:
            sh_kdv_rate = 20.0
            sh_taxable = round(shipping_cost / (1.0 + sh_kdv_rate / 100.0), 2)
            sh_kdv = round(shipping_cost - sh_taxable, 2)  # KDV = brüt - net (kargo)
            kdv_total += sh_kdv
            line_subtotal += sh_taxable
            grp = kdv_groups.setdefault(sh_kdv_rate, {"taxable": 0.0, "tax": 0.0})
            grp["taxable"] += sh_taxable
            grp["tax"] += sh_kdv
            invoice_lines_xml.append(f"""<cac:InvoiceLine>
    <cbc:ID>{len(line_items) + 1}</cbc:ID>
    <cbc:InvoicedQuantity unitCode="C62">1</cbc:InvoicedQuantity>
    <cbc:LineExtensionAmount currencyID="{currency}">{sh_taxable:.2f}</cbc:LineExtensionAmount>
    <cac:TaxTotal>
      <cbc:TaxAmount currencyID="{currency}">{sh_kdv:.2f}</cbc:TaxAmount>
      <cac:TaxSubtotal>
        <cbc:TaxableAmount currencyID="{currency}">{sh_taxable:.2f}</cbc:TaxableAmount>
        <cbc:TaxAmount currencyID="{currency}">{sh_kdv:.2f}</cbc:TaxAmount>
        <cbc:Percent>{sh_kdv_rate:g}</cbc:Percent>
        <cac:TaxCategory>
          <cac:TaxScheme>
            <cbc:Name>GERÇEK USULDE KATMA DEĞER VERGİSİ</cbc:Name>
            <cbc:TaxTypeCode>0015</cbc:TaxTypeCode>
          </cac:TaxScheme>
        </cac:TaxCategory>
      </cac:TaxSubtotal>
    </cac:TaxTotal>
    <cac:Item>
      <cbc:Name>KARGO</cbc:Name>
      <cac:SellersItemIdentification><cbc:ID>KARGO</cbc:ID></cac:SellersItemIdentification>
    </cac:Item>
    <cac:Price>
      <cbc:PriceAmount currencyID="{currency}">{sh_taxable:.4f}</cbc:PriceAmount>
    </cac:Price>
  </cac:InvoiceLine>""")

        # Ücretsiz kargo kampanyası İSKONTO satırı (net 0, GİB-güvenli — toplam/matrah değişmez).
        if free_shipping_waived and float(free_shipping_waived) > 0 and (shipping_cost or 0) <= 0:
            _fsl = _free_shipping_line_xml(len(invoice_lines_xml) + 1, float(free_shipping_waived), currency)
            if _fsl:
                invoice_lines_xml.append(_fsl)
        allowance_charges_xml = []
        if discount > 0:
            line_subtotal -= discount
            allowance_charges_xml.append(f"""<cac:AllowanceCharge>
    <cbc:ChargeIndicator>false</cbc:ChargeIndicator>
    <cbc:AllowanceChargeReason>İndirim</cbc:AllowanceChargeReason>
    <cbc:Amount currencyID="{currency}">{discount:.2f}</cbc:Amount>
  </cac:AllowanceCharge>""")

        tax_inclusive_total = round(line_subtotal + kdv_total, 2)

        tax_subtotals_xml = []
        for rate, grp in sorted(kdv_groups.items()):
            tax_subtotals_xml.append(f"""<cac:TaxSubtotal>
      <cbc:TaxableAmount currencyID="{currency}">{grp['taxable']:.2f}</cbc:TaxableAmount>
      <cbc:TaxAmount currencyID="{currency}">{grp['tax']:.2f}</cbc:TaxAmount>
      <cbc:Percent>{rate:g}</cbc:Percent>
      <cac:TaxCategory>
        <cac:TaxScheme>
          <cbc:Name>GERÇEK USULDE KATMA DEĞER VERGİSİ</cbc:Name>
          <cbc:TaxTypeCode>0015</cbc:TaxTypeCode>
        </cac:TaxScheme>
      </cac:TaxCategory>
    </cac:TaxSubtotal>""")

        def _tr_money_words(n):
            n = int(round(float(n or 0)))
            birler = ["", "Bir", "İki", "Üç", "Dört", "Beş", "Altı", "Yedi", "Sekiz", "Dokuz"]
            onlar = ["", "On", "Yirmi", "Otuz", "Kırk", "Elli", "Altmış", "Yetmiş", "Seksen", "Doksan"]
            def _uc(x):
                s = ""; y = x // 100; k = (x % 100) // 10; b = x % 10
                if y: s += ("" if y == 1 else birler[y]) + "Yüz"
                if k: s += onlar[k]
                if b: s += birler[b]
                return s
            if n == 0: return "Sıfır"
            out = ""; mr = n // 10**9; mn = (n % 10**9) // 10**6; bn = (n % 10**6) // 1000; kl = n % 1000
            if mr: out += _uc(mr) + "Milyar"
            if mn: out += _uc(mn) + "Milyon"
            if bn: out += (("" if bn == 1 else _uc(bn)) + "Bin")
            if kl: out += _uc(kl)
            return out

        notes_xml = []
        # Ürün özellikleri (Stok Kodu / Renk / Barkod / Beden) — Doğan şablonu BU header
        # Note'undan parse eder. Gerçek çalışan e-Faturada (FCE...016) barkod Note[0]'daydı,
        # satır notunda DEĞİL. Bu nedenle her kalem için EN BAŞA ekliyoruz; böylece faturadaki
        # Barkod alanı ürünün barkod no'su ile dolar.
        for _bit in (line_items or []):
            _bsku = _clean(_bit.get("sku") or _bit.get("product_code"))
            _bbar = _clean(_bit.get("barcode"))
            _bcol = _clean(_bit.get("color"))
            _bsz = _clean(_bit.get("size"))
            if _bbar or _bcol or _bsz or _bsku:
                notes_xml.append(
                    "<cbc:Note>"
                    + escape(f"Stok Kodu:{_bsku}\n\nRenk:{_bcol}\n\nBarkod:{_bbar}\n\nBeden:{_bsz}")
                    + "</cbc:Note>"
                )
        _ext = _clean(order_ext_id) or _clean(order_number)
        _cargo = _clean(cargo_tracking)
        if _ext:
            notes_xml.append(f"<cbc:Note>{escape(_ext)}</cbc:Note>")
            _l2 = f"Sipariş Numarası :{escape(_ext)}"
            if _cargo:
                _l2 += f" Kargo Takip No:{escape(_cargo)}"
            notes_xml.append(f"<cbc:Note>{_l2}</cbc:Note>")
        if _clean(invoice_ref):
            notes_xml.append(f"<cbc:Note>Fatura Ref:{escape(_clean(invoice_ref))}</cbc:Note>")
        if _clean(store_name):
            notes_xml.append(f"<cbc:Note>Mağaza Adı : {escape(_clean(store_name))}</cbc:Note>")
        notes_xml.append(f"<cbc:Note>Yalnız {_tr_money_words(tax_inclusive_total)} Lira</cbc:Note>")
        if _clean(payment_method) or _clean(platform_label):
            _lbl = (_clean(platform_label) or _clean(payment_method)).strip()
            _pay_amt = payment_amount if (payment_amount and payment_amount > 0) else tax_inclusive_total
            _amt_str = f"{_pay_amt:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
            notes_xml.append(f"<cbc:Note>Ödeme : {escape(_lbl)} {_amt_str} TL</cbc:Note>")
        notes_xml.append("<cbc:Note>Bu Satış İnternet Üzerinden Yapılmıştır</cbc:Note>")
        notes_xml.append(f"<cbc:Note>Ödeme Tarihi: {escape((_clean(order_date) or issue_date)[:10])}</cbc:Note>")
        notes_xml.append("<cbc:Note>Ödeme Şekli: Elektronik</cbc:Note>")
        notes_xml.append(f"<cbc:Note>Web Adresi: {escape(supplier_website)}</cbc:Note>")
        if _clean(dispatch_date):
            notes_xml.append(f"<cbc:Note>Gönderim Tarihi: {escape(_clean(dispatch_date)[:10])}</cbc:Note>")
        if _clean(carrier_name):
            notes_xml.append(f"<cbc:Note>Gönderi Taşıyan Kişi Türü: {escape(_clean(carrier_type) or 'Tüzel')}</cbc:Note>")
            if _clean(carrier_vkn):
                notes_xml.append(f"<cbc:Note>Gönderi Taşıyan Kimlik No: {escape(_clean(carrier_vkn))}</cbc:Note>")
            notes_xml.append(f"<cbc:Note>Gönderi Taşıyan Kişi Adı: {escape(_clean(carrier_name))}</cbc:Note>")
        # Alıcı açık adresi NOT olarak EKLENMEZ (adres cac:PostalAddress'ten basılıyor;
        # not da eklenince faturada iki kez görünüyordu).

        xslt_ref_id = str(_uuid.uuid4())

        _contact_xml = (f"\n      <cac:Contact><cbc:ElectronicMail>{escape(customer_email)}</cbc:ElectronicMail></cac:Contact>"
                        if customer_email else "")
        _partyname_xml = ("" if _has_person else
                          f"\n      <cac:PartyName>\n        <cbc:Name>{escape(customer_name)}</cbc:Name>\n      </cac:PartyName>")
        _person_xml = ((f"\n      <cac:Person>\n        <cbc:FirstName>{escape(_clean(customer_first_name))}</cbc:FirstName>"
                        f"\n        <cbc:FamilyName>{escape(_clean(customer_family_name))}</cbc:FamilyName>\n      </cac:Person>")
                       if _has_person else "")
        _cust_street_xml = escape(_cust_street_clean or '-')

        xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Invoice xmlns="urn:oasis:names:specification:ubl:schema:xsd:Invoice-2" xmlns:ext="urn:oasis:names:specification:ubl:schema:xsd:CommonExtensionComponents-2" xmlns:qdt="urn:oasis:names:specification:ubl:schema:xsd:QualifiedDatatypes-2" xmlns:ccts="urn:un:unece:uncefact:documentation:2" xmlns:xades="http://uri.etsi.org/01903/v1.3.2#" xmlns:ubltr="urn:oasis:names:specification:ubl:schema:xsd:TurkishCustomizationExtensionComponents" xmlns:cac="urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2" xmlns:udt="urn:un:unece:uncefact:data:specification:UnqualifiedDataTypesSchemaModule:2" xmlns:cbc="urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2" xmlns:ds="http://www.w3.org/2000/09/xmldsig#" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:schemaLocation="urn:oasis:names:specification:ubl:schema:xsd:Invoice-2 UBL-Invoice-2.1.xsd">
  <cbc:UBLVersionID>2.1</cbc:UBLVersionID>
  <cbc:CustomizationID>TR1.2</cbc:CustomizationID>
  <cbc:ProfileID>{escape(profile_id)}</cbc:ProfileID>
  <cbc:ID>{escape(invoice_number)}</cbc:ID>
  <cbc:CopyIndicator>false</cbc:CopyIndicator>
  <cbc:UUID>{escape(invoice_uuid)}</cbc:UUID>
  <cbc:IssueDate>{issue_date}</cbc:IssueDate>
  <cbc:IssueTime>{issue_time}</cbc:IssueTime>
  <cbc:InvoiceTypeCode>SATIS</cbc:InvoiceTypeCode>
  {''.join(notes_xml)}
  <cbc:DocumentCurrencyCode>{currency}</cbc:DocumentCurrencyCode>
  <cbc:LineCountNumeric>{len(invoice_lines_xml)}</cbc:LineCountNumeric>
  <cac:OrderReference>
    <cbc:ID>{escape(order_number) or escape(invoice_number)}</cbc:ID>
    <cbc:IssueDate>{order_date}</cbc:IssueDate>
  </cac:OrderReference>
  <cac:AdditionalDocumentReference>
    <cbc:ID>{xslt_ref_id}</cbc:ID>
    <cbc:IssueDate>{issue_date}</cbc:IssueDate>
    <cbc:DocumentType>XSLT</cbc:DocumentType>
    <cac:Attachment>
      <cbc:EmbeddedDocumentBinaryObject characterSetCode="UTF-8" encodingCode="Base64" filename="{escape(invoice_number)}.xslt" mimeCode="application/CSTAdata+xml">{DOGAN_XSLT_EFATURA_B64}</cbc:EmbeddedDocumentBinaryObject>
    </cac:Attachment>
  </cac:AdditionalDocumentReference>
  <cac:Signature>
    <cbc:ID schemeID="VKN_TCKN">{escape(supplier_vkn)}</cbc:ID>
    <cac:SignatoryParty>
      <cac:PartyIdentification>
        <cbc:ID schemeID="VKN">{escape(supplier_vkn)}</cbc:ID>
      </cac:PartyIdentification>
      <cac:PartyName>
        <cbc:Name>{escape(supplier_name)}</cbc:Name>
      </cac:PartyName>
      <cac:PostalAddress>
        <cbc:ID schemeID="VKN">{escape(supplier_vkn)}</cbc:ID>
        {_sup_street_line}<cbc:CitySubdivisionName>{escape(supplier_district)}</cbc:CitySubdivisionName>
        <cbc:CityName>{escape(supplier_city)}</cbc:CityName>
        <cac:Country>
          <cbc:Name>{escape(supplier_country)}</cbc:Name>
        </cac:Country>
      </cac:PostalAddress>
      <cac:PartyTaxScheme>
        <cac:TaxScheme>
          <cbc:Name>{escape(supplier_tax_office)}</cbc:Name>
        </cac:TaxScheme>
      </cac:PartyTaxScheme>
      <cac:Contact/>
    </cac:SignatoryParty>
    <cac:DigitalSignatureAttachment>
      <cac:ExternalReference>
        <cbc:URI>#Signature_{escape(invoice_number)}</cbc:URI>
      </cac:ExternalReference>
    </cac:DigitalSignatureAttachment>
  </cac:Signature>
  <cac:AccountingSupplierParty>
    <cac:Party>
      <cac:PartyIdentification>
        <cbc:ID schemeID="VKN">{escape(supplier_vkn)}</cbc:ID>
      </cac:PartyIdentification>
      <cac:PartyName>
        <cbc:Name>{escape(supplier_name)}</cbc:Name>
      </cac:PartyName>
      <cac:PostalAddress>
        <cbc:ID schemeID="VKN">{escape(supplier_vkn)}</cbc:ID>
        {_sup_street_line}<cbc:CitySubdivisionName>{escape(supplier_district)}</cbc:CitySubdivisionName>
        <cbc:CityName>{escape(supplier_city)}</cbc:CityName>
        <cac:Country>
          <cbc:Name>{escape(supplier_country)}</cbc:Name>
        </cac:Country>
      </cac:PostalAddress>
      <cac:PartyTaxScheme>
        <cac:TaxScheme>
          <cbc:Name>{escape(supplier_tax_office)}</cbc:Name>
        </cac:TaxScheme>
      </cac:PartyTaxScheme>
      <cac:Contact/>
    </cac:Party>
  </cac:AccountingSupplierParty>
  <cac:AccountingCustomerParty>
    <cac:Party>
      <cac:PartyIdentification>
        <cbc:ID schemeID="{cust_id_scheme}">{escape(customer_vkn)}</cbc:ID>
      </cac:PartyIdentification>{_partyname_xml}
      <cac:PostalAddress>
        <cbc:StreetName>{_cust_street_xml}</cbc:StreetName>
        <cbc:CitySubdivisionName>{escape(customer_district or '-')}</cbc:CitySubdivisionName>
        <cbc:CityName>{escape(customer_city)}</cbc:CityName>
        <cbc:PostalZone>{escape(customer_postal_zone)}</cbc:PostalZone>
        <cac:Country>
          <cbc:Name>{escape(customer_country)}</cbc:Name>
        </cac:Country>
      </cac:PostalAddress>{_cust_tax_xml}{_contact_xml}{_person_xml}
    </cac:Party>
  </cac:AccountingCustomerParty>
  <cac:BuyerCustomerParty>
    <cac:Party>
      <cac:PartyIdentification>
        <cbc:ID schemeID="{cust_id_scheme}">{escape(customer_vkn)}</cbc:ID>
      </cac:PartyIdentification>{_partyname_xml}
      <cac:PostalAddress>
        <cbc:ID schemeID="{cust_id_scheme}">{escape(customer_vkn)}</cbc:ID>
        <cbc:StreetName>{_cust_street_xml}</cbc:StreetName>
        <cbc:CitySubdivisionName>{escape(customer_district or '-')}</cbc:CitySubdivisionName>
        <cbc:CityName>{escape(customer_city)}</cbc:CityName>
        <cbc:PostalZone>{escape(customer_postal_zone)}</cbc:PostalZone>
        <cac:Country>
          <cbc:Name>{escape(customer_country)}</cbc:Name>
        </cac:Country>
      </cac:PostalAddress>{_cust_tax_xml}{_contact_xml}{_person_xml}
    </cac:Party>
  </cac:BuyerCustomerParty>
  <cac:Delivery>
    <cac:DeliveryAddress>
      <cbc:CitySubdivisionName>{escape(customer_district or '-')}</cbc:CitySubdivisionName>
      <cbc:CityName>{escape(customer_city)}</cbc:CityName>
      <cbc:PostalZone>{escape(customer_postal_zone)}</cbc:PostalZone>
      <cac:Country>
        <cbc:Name>{escape(customer_country)}</cbc:Name>
      </cac:Country>
    </cac:DeliveryAddress>
  </cac:Delivery>
  {''.join(allowance_charges_xml)}
  <cac:TaxTotal>
    <cbc:TaxAmount currencyID="{currency}">{kdv_total:.2f}</cbc:TaxAmount>
    {''.join(tax_subtotals_xml)}
  </cac:TaxTotal>
  <cac:LegalMonetaryTotal>
    <cbc:LineExtensionAmount currencyID="{currency}">{line_subtotal:.2f}</cbc:LineExtensionAmount>
    <cbc:TaxExclusiveAmount currencyID="{currency}">{line_subtotal:.2f}</cbc:TaxExclusiveAmount>
    <cbc:TaxInclusiveAmount currencyID="{currency}">{tax_inclusive_total:.2f}</cbc:TaxInclusiveAmount>
    <cbc:PayableAmount currencyID="{currency}">{tax_inclusive_total:.2f}</cbc:PayableAmount>
  </cac:LegalMonetaryTotal>
  {''.join(invoice_lines_xml)}
</Invoice>"""
        return xml

    def send_efatura_invoice(self, ubl_xml: str, invoice_uuid: str,
                              invoice_number: str,
                              receiver_vkn: str, receiver_alias: str,
                              sender_alias: str = "",
                              email_to: str = "") -> dict:
        """E-Faturayı Doğan EFaturaOIB.SendInvoice ile gönderir.

        Önemli: receiver_alias e-Fatura ataması (PK alias). check_user'dan dönen
        ilk user'ın alias'ı kullanılmalı (örn: "urn:mail:defaultpk@vkn.tr").
        sender_alias boşsa Doğan default'u kullanır.
        """
        try:
            client = self._get_efatura_client()
            xml_bytes = ubl_xml.encode("utf-8")
            mail_flag = "Y" if email_to else "N"
            mail_list = [email_to] if email_to else []

            invoice_obj = {
                "HEADER": {
                    "SENDER": self.username if not sender_alias else sender_alias,
                    "RECEIVER": receiver_alias,
                    "MAIL_FLAG": mail_flag,
                    "MAIL": mail_list,
                },
                "CONTENT": xml_bytes,
                "ID": invoice_number,
                "UUID": invoice_uuid,
            }

            result = client.service.SendInvoice(
                REQUEST_HEADER=self._make_header(compressed="N"),
                SENDER={"vkn": self.username, "alias": sender_alias or ""},
                RECEIVER={"vkn": receiver_vkn, "alias": receiver_alias},
                INVOICE=[invoice_obj],
            )
            from zeep.helpers import serialize_object
            ser = serialize_object(result) or {}

            err = ser.get("ERROR_TYPE") or {}
            err_code = err.get("ERROR_CODE") if err else None
            err_msg = err.get("ERROR_SHORT_DES") if err else None
            intl_txn_id = (err.get("INTL_TXN_ID") if err else None) or \
                          (ser.get("REQUEST_RETURN") or {}).get("INTL_TXN_ID")
            if err_code:
                return {
                    "success": False,
                    "code": str(err_code),
                    "message": str(err_msg or "Bilinmeyen hata"),
                    "intl_txn_id": str(intl_txn_id or ""),
                    "uuid": invoice_uuid,
                    "raw": str(ser)[:600],
                }

            invoice_id = ser.get("INVOICE_ID") or invoice_number
            # e-Fatura yanıtında Doğan bazı kurulumlarda görüntüleme URL'i / WEB_KEY döndürebilir.
            # Varsa yakala (e-Arşiv'deki web_key gibi kullanılır); yoksa boş kalır ve orders.py
            # einvoice_link_template ile UUID'den link kurar. (Salt-okuma; e-Arşiv'i etkilemez.)
            _rr = ser.get("REQUEST_RETURN") or {}
            _ef_web = (ser.get("WEB_KEY") or ser.get("URL") or ser.get("INVOICE_URL")
                       or _rr.get("WEB_KEY") or _rr.get("URL") or "")
            return {
                "success": True,
                "code": "0",
                "message": "OK",
                "invoice_id": str(invoice_id),
                "web_key": str(_ef_web or ""),
                "intl_txn_id": str(intl_txn_id or ""),
                "uuid": invoice_uuid,
                "receiver_alias": receiver_alias,
                "raw": str(ser)[:300],
            }
        except Exception as e:
            logger.error(f"send_efatura_invoice error: {e}")
            return {"success": False, "code": "", "message": str(e), "raw": ""}

    def send_earsiv_invoice(self, ubl_xml: str, invoice_uuid: str = None,
                              email_to: str = "", archive_note: str = "") -> dict:
        """E-Arşiv faturayı Doğan WriteToArchiveExtended ile senkron olarak gönderir.

        WriteToArchiveExtended **senkron** çalışır ve INVOICE_ID/WEB_KEY'i ya da
        somut hata kodunu (ERROR_CODE/ERROR_SHORT_DES) anında döner. Bu sayede
        UBL parse / şema / şablon hatalarını anında görebiliriz.

        ubl_xml: UBL-TR 1.2 invoice XML (UTF-8 string, içinde XSLT gömülü olmalı).
        invoice_uuid: opsiyonel, sadece log için.
        email_to: dolu ise Doğan PDF'i bu adrese gönderir.
        Returns: {success, code, message, invoice_id, web_key, intl_txn_id, uuid}
        """
        try:
            client = self._get_earsiv_client()
            xml_bytes = ubl_xml.encode("utf-8")

            content_type = client.get_type("ns0:ArchiveInvoiceExtendedContent")
            earsiv_props = {
                "EARSIV_TYPE": "INTERNET",
                "EARSIV_EMAIL_FLAG": "Y" if email_to else "N",
                "EARCHIVE_TEST_FLAG": "Y" if self.is_test else "N",
                "VALIDATION_FLAG": "Y",
            }
            if email_to:
                earsiv_props["EARSIV_EMAIL"] = [email_to]

            content = content_type(INVOICE_PROPERTIES=[{
                "EARSIV_FLAG": "Y",
                "EARSIV_PROPERTIES": earsiv_props,
                "PDF_PROPERTIES": {
                    "EARSIV_PDF_FLAG": "Y",  # PDF üret
                    "PDF_SIGNATURE_FLAG": "Y",
                },
                "ARCHIVE_NOTE": (archive_note or "")[:200],
                "INVOICE_CONTENT": xml_bytes,
            }])

            result = client.service.WriteToArchiveExtended(
                REQUEST_HEADER=self._make_header(compressed="N"),
                ArchiveInvoiceExtendedContent=content,
            )
            from zeep.helpers import serialize_object
            ser = serialize_object(result) or {}

            err = ser.get("ERROR_TYPE") or {}
            err_code = err.get("ERROR_CODE") if err else None
            err_msg = err.get("ERROR_SHORT_DES") if err else None
            intl_txn_id = (err.get("INTL_TXN_ID") if err else None) or \
                          (ser.get("REQUEST_RETURN") or {}).get("INTL_TXN_ID")
            if err_code:
                return {
                    "success": False,
                    "code": str(err_code),
                    "message": str(err_msg or "Bilinmeyen hata"),
                    "intl_txn_id": str(intl_txn_id or ""),
                    "uuid": invoice_uuid or "",
                    "raw": str(ser)[:600],
                }

            invoice_id = ser.get("INVOICE_ID") or ""
            web_key = ser.get("WEB_KEY") or ""
            return {
                "success": True,
                "code": "0",
                "message": "OK",
                "invoice_id": str(invoice_id),
                "web_key": str(web_key),
                "intl_txn_id": str(intl_txn_id or ""),
                "uuid": invoice_uuid or "",
                "raw": str(ser)[:300],
            }
        except Exception as e:
            logger.error(f"send_earsiv_invoice error: {e}")
            return {"success": False, "code": "", "message": str(e), "raw": ""}
