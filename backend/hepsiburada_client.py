#!/usr/bin/env python3
"""
hepsiburada_client.py — Hepsiburada Marketplace (MPOP) tam API istemcisi.

Kapsam (resmi dökümana göre):
  • Katalog (MPOP)    : kategoriler, kategori özellikleri, özellik (enum) değerleri
  • Ürün (MPOP)       : ürün oluşturma (import / multipart), tracking, statü sorgulama,
                        statü bazlı ve mağaza bazlı ürün listeleme
  • Listing (LISTING) : fiyat güncelleme, stok güncelleme, güncelleme durumu sorgulama,
                        listing çekme, satışa açma/kapatma (activate/deactivate), silme
  • Sipariş (OMS)     : sipariş listesi/detay, kampanya, paketleme, paket listesi,
                        kargo firması değiştirme, fatura linki, kargo etiketi,
                        kalem iptali, teslim bilgisi
  • İade/Talep (OMS)  : talep listesi, statü bazlı talep, kabul/ret, bekleyen aksiyonlar

Auth :
  • MPOP + Listing    : HTTP Basic (merchant_id:secret_key) + User-Agent (dev_username)
  • OMS + Talep       : AYRI Basic kimlik gerekebilir; olası kombinasyonlar sırayla denenir.

Ortam: test=True  -> *-sit.hepsiburada.com (sandbox)
       test=False -> canlı host

Kütüphane:
  hb = HepsiburadaClient(merchant_id, secret_key, dev_username, test=True)
  cats  = hb.get_categories(leaf=True, active=True, available=True)
  attrs = hb.get_category_attributes(category_id)
  res   = hb.update_stocks([{"merchantSku": "ABC", "availableStock": 5}])

Self-test:
  python3 hepsiburada_client.py
"""
import base64
import json
import uuid
import urllib.request
import urllib.parse
import urllib.error


class HepsiburadaError(Exception):
    pass


def _hb_decode_body(raw, headers=None) -> str:
    """HB yanıt/hata gövdesini güvenle metne çevirir: gzip/deflate ise açar,
    HTTP cevabı binary (WAF/CDN gzip 403 sayfası) olsa bile okunur metin döndürür."""
    if not raw:
        return ""
    if isinstance(raw, str):
        return raw
    try:
        enc = ""
        if headers:
            try:
                enc = (headers.get("Content-Encoding") or headers.get("content-encoding") or "").lower()
            except Exception:
                enc = ""
        if raw[:2] == b"\x1f\x8b" or "gzip" in enc:
            import gzip
            raw = gzip.decompress(raw)
        elif "deflate" in enc:
            import zlib
            try:
                raw = zlib.decompress(raw)
            except Exception:
                raw = zlib.decompress(raw, -zlib.MAX_WBITS)
    except Exception:
        pass
    txt = raw.decode("utf-8", "replace") if isinstance(raw, (bytes, bytearray)) else str(raw)
    # WAF/HTML sayfalarını sadeleştir: etiketleri kaba temizle, boşlukları daralt
    import re as _re
    if "<html" in txt.lower() or "<!doctype" in txt.lower():
        txt = _re.sub(r"<[^>]+>", " ", txt)
    return " ".join(txt.split())


class HepsiburadaClient:
    # MPOP (katalog + ürün)
    SANDBOX = "https://mpop-sit.hepsiburada.com"
    PROD = "https://mpop.hepsiburada.com"
    # Listing (fiyat/stok/listeleme)
    LISTING_SANDBOX = "https://listing-external-sit.hepsiburada.com"
    LISTING_PROD = "https://listing-external.hepsiburada.com"
    # OMS (sipariş + talep)
    OMS_SANDBOX = "https://oms-external-sit.hepsiburada.com"
    OMS_PROD = "https://oms-external.hepsiburada.com"

    # SADECE TEST: test siparişi oluşturma ayrı bir "stub" host'unda yayınlanır (prod yok).
    OMS_STUB_SANDBOX = "https://oms-stub-external-sit.hepsiburada.com"

    def __init__(self, merchant_id, secret_key, dev_username, test=True, timeout=30,
                 oms_username=None, oms_password=None):
        if not (merchant_id and secret_key and dev_username):
            raise ValueError("merchant_id, secret_key ve dev_username zorunlu.")
        self.merchant_id = str(merchant_id).strip()
        self.secret_key = str(secret_key).strip()
        self.dev_username = str(dev_username).strip()
        self.oms_username = (str(oms_username).strip() if oms_username else "")
        self.oms_password = (str(oms_password).strip() if oms_password else "")
        self.test = bool(test)
        self.base = self.SANDBOX if test else self.PROD
        self.listing_base = self.LISTING_SANDBOX if test else self.LISTING_PROD
        self.timeout = timeout
        token = base64.b64encode(f"{self.merchant_id}:{self.secret_key}".encode()).decode()
        self._auth = f"Basic {token}"

    # ====================================================================
    #  Ortak HTTP yardımcıları (MPOP + Listing — standart Basic auth)
    # ====================================================================
    def _request(self, base, method, path, params=None, json_body=None,
                 raw_body=None, content_type=None, return_headers=False, raw_response=False):
        url = base + path
        if params:
            clean = {k: v for k, v in params.items() if v not in (None, "")}
            if clean:
                url += "?" + urllib.parse.urlencode(clean)
        headers = {
            "Authorization": self._auth,
            "User-Agent": self.dev_username,
            "Accept": "application/json",
            "Accept-Encoding": "identity",
        }
        data = None
        if json_body is not None:
            data = json.dumps(json_body, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        elif raw_body is not None:
            data = raw_body if isinstance(raw_body, bytes) else str(raw_body).encode("utf-8")
            if content_type:
                headers["Content-Type"] = content_type
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                body = r.read()
                if raw_response:
                    return (body, dict(r.headers)) if return_headers else body
                text = _hb_decode_body(body, r.headers)
                parsed = json.loads(text) if text.strip() else {}
                return (parsed, dict(r.headers)) if return_headers else parsed
        except urllib.error.HTTPError as e:
            try:
                detail = _hb_decode_body(e.read(), e.headers)[:600]
            except Exception:
                detail = ""
            hint = ""
            if e.code == 403:
                hint = (" | 403: kimlik/ortam uyuşmazlığı (sandbox kimlik + CANLI host ya da tersi) "
                        "veya HB tarafı erişim engeli olabilir. Ortamı /hepsiburada/env-status ile doğrula.")
            raise HepsiburadaError(f"HTTP {e.code} {method} {path} -> {detail}{hint}")
        except urllib.error.URLError as e:
            raise HepsiburadaError(f"Baglanti hatasi {path}: {e}")

    def _get(self, path, params=None, return_headers=False):
        return self._request(self.base, "GET", path, params=params, return_headers=return_headers)

    # ====================================================================
    #  KATEGORİLER (MPOP)
    # ====================================================================
    def get_categories(self, leaf=None, active=None, available=None, page=0, size=2000):
        params = {"page": page, "size": size}
        if leaf is not None:
            params["leaf"] = str(leaf).lower()
        if available is not None:
            params["available"] = str(available).lower()
        if active is not None:
            params["status"] = "ACTIVE" if active else "INACTIVE"
        return self._get("/product/api/categories/get-all-categories", params)

    def iter_all_categories(self, leaf=None, active=None, available=None, size=2000):
        page, out = 0, []
        while True:
            d = self.get_categories(leaf=leaf, active=active, available=available, page=page, size=size)
            rows = d.get("data", []) or []
            out.extend(rows)
            if d.get("last") or not rows:
                break
            page += 1
        return out

    def get_category_attributes(self, category_id):
        """Sadece leaf+available kategorilerde dolu gelir.
        Doner: {'baseAttributes':[...], 'attributes':[...]}"""
        d = self._get(f"/product/api/categories/{category_id}/attributes")
        return d.get("data", {}) or {}

    def get_attribute_values(self, category_id, attribute_id, page=0, limit=1000):
        """type=='enum' ozellikler icin gecerli degerler (paginasyonlu)."""
        aid = urllib.parse.quote(str(attribute_id), safe="")
        path = f"/product/api/categories/{category_id}/attribute/{aid}/values"
        data, headers = self._get(path, {"page": page, "limit": limit}, return_headers=True)
        rows = data.get("data", data) if isinstance(data, dict) else data
        total = (headers.get("Total-Count") or headers.get("total-count")
                 or headers.get("X-Total-Count"))
        return {"data": rows or [],
                "total": int(total) if total and str(total).isdigit() else None,
                "page": page, "limit": limit}

    def iter_attribute_values(self, category_id, attribute_id, limit=1000, max_pages=100):
        """Bir özelliğin TÜM değerlerini çeker. HB sayfa boyutunu (limit'i yok sayıp)
        100'de sınırlayabildiğinden, 'len(chunk) < limit' ile DURMAYIZ; boş sayfa,
        Total-Count'a ulaşma ya da yeni-kayıt-gelmemesi olana dek sayfalamaya devam ederiz."""
        page, out, seen = 0, [], set()
        while page < max_pages:
            res = self.get_attribute_values(category_id, attribute_id, page=page, limit=limit)
            chunk = res.get("data") or []
            if not chunk:
                break
            new = 0
            for v in chunk:
                k = (v.get("id") if isinstance(v, dict) else v)
                if k is None:
                    k = (v.get("value") or v.get("name")) if isinstance(v, dict) else v
                if k in seen:
                    continue
                seen.add(k)
                out.append(v)
                new += 1
            total = res.get("total")
            if total is not None and len(out) >= total:
                break
            if new == 0:  # ilerleme yok (HB aynı sayfayı tekrar döndürüyor) → sonsuz döngüyü önle
                break
            page += 1
        return out

    # ====================================================================
    #  ÜRÜN (MPOP)  —  oluşturma / tracking / statü
    # ====================================================================
    def create_products(self, items):
        """Ürünleri Hepsiburada kataloğuna gönderir (import).
        items: HB ürün nesneleri listesi -> her biri {categoryId, merchant, attributes:{...}}.
        İstek multipart/form-data; 'file' alanında JSON dosyası (integrator.json) gider.
        Doner: {trackingId|tracking_id|...} (içe aktarma takip numarası)."""
        if isinstance(items, dict):
            items = [items]
        payload = json.dumps(items, ensure_ascii=False).encode("utf-8")
        boundary = "----HBFacette" + uuid.uuid4().hex
        body = b""
        body += ("--" + boundary + "\r\n").encode()
        body += b'Content-Disposition: form-data; name="file"; filename="integrator.json"\r\n'
        body += b"Content-Type: application/json\r\n\r\n"
        body += payload + b"\r\n"
        body += ("--" + boundary + "--\r\n").encode()
        return self._request(self.base, "POST", "/product/api/products/import",
                             raw_body=body,
                             content_type="multipart/form-data; boundary=" + boundary)

    def get_product_tracking(self, tracking_id):
        """İçe aktarma (import) takip numarasının durumunu döner."""
        tid = urllib.parse.quote(str(tracking_id), safe="")
        return self._get(f"/product/api/products/status/{tid}")

    # ====================================================================
    #  ÜRÜN GÜNCELLEME (ticket-api) — HB'de ZATEN listelenmiş/oluşturulmuş bir
    #  ürünün ad/açıklama/görsel/video/kategori-özelliklerini değiştirir.
    #  create_products (/product/api/products/import) İLE KARIŞTIRILMAMALI:
    #  o yalnız YENİ ürün GİRİŞİ + ilk eşleştirme içindir ve var olan bir
    #  ürünün özelliklerini güncellemeyi garanti ETMEZ. Var olan ürünü
    #  güncellemenin resmi yolu budur — anahtar merchantSku DEĞİL, hbSku'dur.
    #  Fiyat/stok/KDV/Barkod/Desi bu serviste YOK (ayrı Listing kanalından gider).
    # ====================================================================
    def update_products(self, items, merchant_id=None):
        """Var olan ürünlerin ad/açıklama/görsel/video/özelliklerini günceller.
        items: [{hbSku, productName?, productDescription?, image1..image10?,
                 video?, attributes:{...}}] — hbSku ZORUNLU.
        Boş string ("") gönderilen bir attribute değeri o özelliği SİLER;
        bir alanı hiç göndermemek o alana DOKUNMAZ (HB'nin zenginleştirdiği
        veriyi ezmemek için yalnız değişen alanları göndermek önerilir).
        Döner: {trackingId|...}."""
        if isinstance(items, dict):
            items = [items]
        body_obj = {"merchantId": merchant_id or self.merchant_id, "items": items}
        payload = json.dumps(body_obj, ensure_ascii=False).encode("utf-8")
        boundary = "----HBFacetteTicket" + uuid.uuid4().hex
        body = b""
        body += ("--" + boundary + "\r\n").encode()
        body += b'Content-Disposition: form-data; name="file"; filename="integrator-ticket-upload.json"\r\n'
        body += b"Content-Type: application/json\r\n\r\n"
        body += payload + b"\r\n"
        body += ("--" + boundary + "--\r\n").encode()
        return self._request(self.base, "POST", "/ticket-api/api/integrator/import",
                             raw_body=body,
                             content_type="multipart/form-data; boundary=" + boundary)

    def get_update_ticket_status(self, tracking_id):
        """Ürün güncelleme (ticket) talebinin durumunu sorgular.
        NOT: HB döküman sayfası status endpoint'inin tam path'ini göstermiyordu;
        diğer 'status/{id}' uçlarıyla AYNI desene göre tahmin edilmiştir —
        ilk canlı/sandbox kullanımda doğrulanmalı (yanlışsa _hb_poll_ticket
        sessizce 'UNKNOWN' döner, ana akışı bozmaz)."""
        tid = urllib.parse.quote(str(tracking_id), safe="")
        return self._get(f"/ticket-api/api/integrator/status/{tid}")

    def get_update_ticket_history(self, hb_sku):
        """Bir hbSku için geçmiş güncelleme taleplerini (trackingId, createdAt) döner."""
        sku = urllib.parse.quote(str(hb_sku), safe="")
        path = f"/ticket-api/api/integrator/merchant/{self.merchant_id}/hbSku/{sku}"
        return self._get(path)

    def check_product_status(self, merchant_sku_list):
        """merchantSku listesinin ürün/eşleşme durumlarını sorgular.
        body: [{"merchant": <mid>, "merchantSkuList": [...]}]"""
        skus = [str(s) for s in (merchant_sku_list or []) if s]
        body = [{"merchant": self.merchant_id, "merchantSkuList": skus}]
        return self._request(self.base, "POST", "/product/api/check-product-status", json_body=body)

    def get_products_by_status(self, product_status="WAITING", task_status=False,
                               page=0, size=100, version=1):
        """Statü bazlı ürün bilgisi çekme."""
        params = {"merchantId": self.merchant_id, "productStatus": product_status,
                  "taskStatus": str(task_status).lower(), "page": page, "size": size,
                  "version": version}
        return self._get("/product/api/products/products-by-merchant-and-status", params)

    def get_all_products(self, page=0, size=1000):
        """Mağaza bazlı tüm ürün bilgilerini listeler."""
        return self._get(f"/product/api/products/all-products-of-merchant/{self.merchant_id}",
                         {"page": page, "size": size})

    # ====================================================================
    #  LISTING  —  fiyat / stok / listeleme  (listing-external host)
    # ====================================================================
    def update_prices(self, items):
        """Listing fiyat güncelleme.
        items: [{"merchantSku"|"hepsiburadaSku": ..., "price": <float>}]
        Doner: {"id": <inventoryUploadId>, ...}"""
        path = f"/listings/merchantid/{self.merchant_id}/price-uploads"
        return self._request(self.listing_base, "POST", path, json_body=list(items))

    def update_stocks(self, items):
        """Listing stok güncelleme.
        items: [{"merchantSku"|"hepsiburadaSku": ..., "availableStock": <int>}]
        Doner: {"id": <inventoryUploadId>, ...}"""
        path = f"/listings/merchantid/{self.merchant_id}/stock-uploads"
        return self._request(self.listing_base, "POST", path, json_body=list(items))

    def get_price_upload_status(self, upload_id):
        """Fiyat güncelleme işlem kontrolü (MinLock/MaxLock dahil)."""
        uid = urllib.parse.quote(str(upload_id), safe="")
        path = f"/listings/merchantid/{self.merchant_id}/price-uploads/id/{uid}"
        return self._request(self.listing_base, "GET", path)

    def get_stock_upload_status(self, upload_id):
        """Stok güncelleme işlem kontrolü."""
        uid = urllib.parse.quote(str(upload_id), safe="")
        path = f"/listings/merchantid/{self.merchant_id}/stock-uploads/id/{uid}"
        return self._request(self.listing_base, "GET", path)

    def get_upload_status(self, kind, upload_id):
        """kind: 'price' | 'stock' | 'inventory'"""
        k = {"price": "price-uploads", "stock": "stock-uploads",
             "inventory": "inventory-uploads"}.get(kind, "stock-uploads")
        uid = urllib.parse.quote(str(upload_id), safe="")
        path = f"/listings/merchantid/{self.merchant_id}/{k}/id/{uid}"
        return self._request(self.listing_base, "GET", path)

    def update_listings_xml(self, listings):
        """Birleşik listing güncelleme (fiyat+stok+kargo+termin) — XML body.
        listings: [{HepsiburadaSku, MerchantSku, ProductName, Price, AvailableStock,
                    DispatchTime, MaximumPurchasableQuantity, CargoCompany1..3}]
        Doner: {"id": <inventoryUploadId>, ...}"""
        def esc(v):
            s = "" if v is None else str(v)
            return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
        parts = ['<?xml version="1.0" encoding="utf-8"?>',
                 '<listings xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
                 'xmlns:xsd="http://www.w3.org/2001/XMLSchema">']
        for it in listings:
            parts.append("<listing>")
            for k, v in it.items():
                if v in (None, ""):
                    continue
                parts.append(f"<{k}>{esc(v)}</{k}>")
            parts.append("</listing>")
        parts.append("</listings>")
        xml = "".join(parts)
        path = f"/listings/merchantid/{self.merchant_id}/inventory-uploads"
        return self._request(self.listing_base, "POST", path,
                             raw_body=xml, content_type="application/xml")

    def get_listings(self, offset=0, limit=100, merchant_sku_list=None,
                     hepsiburada_sku_list=None):
        """Satıcı listing bilgilerini çeker."""
        params = {"offset": offset, "limit": limit}
        if merchant_sku_list:
            params["merchantSkuList"] = ",".join(str(s) for s in merchant_sku_list)
        if hepsiburada_sku_list:
            params["hepsiburadaSkuList"] = ",".join(str(s) for s in hepsiburada_sku_list)
        path = f"/listings/merchantid/{self.merchant_id}"
        return self._request(self.listing_base, "GET", path, params=params)

    def get_listing_by_sku(self, merchant_sku):
        """Tek bir merchantSku için listing detayını döner (yoksa None)."""
        res = self.get_listings(offset=0, limit=1, merchant_sku_list=[merchant_sku])
        rows = res.get("listings") if isinstance(res, dict) else None
        if rows:
            return rows[0]
        return None

    def activate_listing(self, hepsiburada_sku):
        """Listingi satışa açar (stok ve fiyat 0'dan farklı olmalı)."""
        sku = urllib.parse.quote(str(hepsiburada_sku), safe="")
        path = f"/listings/merchantid/{self.merchant_id}/sku/{sku}/activate"
        return self._request(self.listing_base, "POST", path)

    def deactivate_listing(self, hepsiburada_sku):
        """Listingi satışa kapatır."""
        sku = urllib.parse.quote(str(hepsiburada_sku), safe="")
        path = f"/listings/merchantid/{self.merchant_id}/sku/{sku}/deactivate"
        return self._request(self.listing_base, "POST", path)

    def delete_listing(self, hepsiburada_sku, merchant_sku):
        """Satışta olmayan bir listingi siler."""
        hb = urllib.parse.quote(str(hepsiburada_sku), safe="")
        ms = urllib.parse.quote(str(merchant_sku), safe="")
        path = f"/listings/merchantid/{self.merchant_id}/sku/{hb}/merchantsku/{ms}"
        return self._request(self.listing_base, "DELETE", path)

    # ====================================================================
    #  OMS (sipariş + talep)  —  ayrı Basic kimlik denemeli
    # ====================================================================
    def _oms_base(self):
        return self.OMS_SANDBOX if self.test else self.OMS_PROD

    def _oms_auth_candidates(self):
        """OMS için denenecek Basic auth kombinasyonları — en olasıdan başlayarak."""
        def b(u, p):
            return "Basic " + base64.b64encode(f"{u}:{p}".encode()).decode()
        cands = []
        if self.oms_username and self.oms_password:
            cands.append(("oms_username:oms_password", b(self.oms_username, self.oms_password)))
        cands += [
            ("merchant_id:secret_key", b(self.merchant_id, self.secret_key)),
            ("dev_username:secret_key", b(self.dev_username, self.secret_key)),
            ("dev_username:merchant_id", b(self.dev_username, self.merchant_id)),
        ]
        return cands

    def _oms_request(self, method, path, params=None, json_body=None, raw_response=False, base=None, read_timeout=None):
        url = (base or self._oms_base()) + path
        if params:
            clean = {k: v for k, v in params.items() if v not in (None, "")}
            if clean:
                url += "?" + urllib.parse.urlencode(clean)
        data = None
        extra = {}
        if json_body is not None:
            data = json.dumps(json_body, ensure_ascii=False).encode("utf-8")
            extra["Content-Type"] = "application/json"
        tried = []
        last_detail = ""
        cands = list(self._oms_auth_candidates())
        ok = getattr(self, "_oms_auth_ok", None)
        if ok:  # daha önce tutan auth'u en başa al (çoklu yavaş deneme turunu önler)
            cands.sort(key=lambda c: 0 if c[1] == ok else 1)
        for label, auth in cands:
            headers = {"Authorization": auth, "User-Agent": self.dev_username,
                       "Accept": "application/json", "Accept-Encoding": "identity"}
            headers.update(extra)
            req = urllib.request.Request(url, data=data, headers=headers, method=method)
            try:
                with urllib.request.urlopen(req, timeout=(read_timeout or min(self.timeout, 20))) as r:
                    self._oms_auth_ok = auth  # başarılı kimliği sakla
                    body = r.read()
                    if raw_response:
                        return body
                    text = _hb_decode_body(body, r.headers)
                    return json.loads(text) if text.strip() else {}
            except urllib.error.HTTPError as e:
                try:
                    detail = _hb_decode_body(e.read(), e.headers)[:300]
                except Exception:
                    detail = ""
                if e.code in (401, 403):
                    tried.append(label)
                    last_detail = detail
                    continue
                raise HepsiburadaError(f"OMS HTTP {e.code} {method} {path} -> {detail}")
            except urllib.error.URLError as e:
                raise HepsiburadaError(f"OMS baglanti hatasi {path}: {e}")
        raise HepsiburadaError(
            f"OMS 401/403 — denenen kimliklerin hepsi reddedildi ({', '.join(tried)}). "
            f"Hepsiburada OMS icin AYRI Entegrasyon Kullanici Adi/Sifre gerekiyor olabilir "
            f"(Merchant panel > Entegrasyon Bilgileri). Son yanit: {last_detail[:120]}"
        )

    def _oms_get(self, path, params=None, read_timeout=None):
        return self._oms_request("GET", path, params=params, read_timeout=read_timeout)

    def create_test_order(self, body=None):
        """SADECE TEST ortamı: oms-stub-external-sit üzerinden test siparişi oluşturur.
        Endpoint: POST /orders/merchantId/{merchantId}. Kural: URL'deki ve LineItems içindeki
        MerchantId bu hesabın merchant_id'si olmalıdır. OrderNumber benzersiz olmalı (boşsa üretilir).
        Auth: OMS ile aynı (Basic merchant_id:secret_key + User-Agent dev_username)."""
        import random
        from datetime import datetime, timezone
        b = dict(body or {})
        if not str(b.get("OrderNumber") or "").strip():
            b["OrderNumber"] = str(random.randint(10**11, 10**12 - 1))
        if not str(b.get("OrderDate") or "").strip():
            b["OrderDate"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
        items = b.get("LineItems") or []
        for it in items:
            if isinstance(it, dict):
                it["MerchantId"] = self.merchant_id  # URL merchantId ile birebir aynı olmak ZORUNDA
        b["LineItems"] = items
        resp = self._oms_request("POST", f"/orders/merchantId/{self.merchant_id}",
                                 json_body=b, base=self.OMS_STUB_SANDBOX)
        if isinstance(resp, dict):
            resp.setdefault("_orderNumber", b["OrderNumber"])
            return resp
        return {"_orderNumber": b["OrderNumber"], "raw": resp}

    # ---------- Sipariş listesi / detay ----------
    @staticmethod
    def _oms_date(v):
        """OMS tarih paramı: HB 'yyyy-MM-dd HH:mm' bekler (küçük harf begindate/enddate).
        ISO/'T'li/saniyeli/datetime girişleri bu formata normalize eder; None→None."""
        if v is None or v == "":
            return None
        from datetime import datetime as _dt
        if isinstance(v, _dt):
            return v.strftime("%Y-%m-%d %H:%M")
        s = str(v).strip().replace("T", " ")
        try:
            return _dt.fromisoformat(s).strftime("%Y-%m-%d %H:%M")
        except Exception:
            return s[:16]  # 'yyyy-MM-dd HH:mm' uzunluğuna kırp — en azından saniyeyi at

    def get_orders(self, begin_date=None, end_date=None, offset=0, limit=100, read_timeout=12):
        """Ödemesi tamamlanmış (Open/Unpacked) sipariş kalemlerini listeler.
        DİKKAT (HB kuralı): begindate/enddate yalnız 24 SAATLİK pencere kabul eder;
        daha genişse enddate ignore edilir. Tarihsiz çağrı tüm açık siparişleri döner.
        read_timeout: gateway 503 eşiğinin (≈20s) ALTINDA kalmak için kısa tutulur."""
        params = {"offset": offset, "limit": limit,
                  "begindate": self._oms_date(begin_date), "enddate": self._oms_date(end_date)}
        return self._oms_get(f"/orders/merchantid/{self.merchant_id}", params, read_timeout=read_timeout)

    def get_cancelled_orders(self, offset=0, limit=50, begin_date=None, end_date=None, read_timeout=12):
        """İptal edilmiş siparişleri listeler (son 1 aylık veri, limit max 50)."""
        params = {"offset": offset, "limit": min(int(limit or 50), 50),
                  "begindate": self._oms_date(begin_date), "enddate": self._oms_date(end_date)}
        return self._oms_get(f"/orders/merchantid/{self.merchant_id}/cancelled", params,
                             read_timeout=read_timeout)

    def get_order_by_number(self, order_number):
        """Tek bir siparişi numarasına göre getirir (özet)."""
        on = urllib.parse.quote(str(order_number), safe="")
        return self._oms_get(f"/orders/merchantid/{self.merchant_id}/ordernumber/{on}")

    def get_order_detail(self, order_number):
        """Sipariş detayını getirir."""
        on = urllib.parse.quote(str(order_number), safe="")
        return self._oms_get(f"/orders/merchantid/{self.merchant_id}/ordernumber/{on}")

    def get_order_campaigns(self, order_number):
        """Sipariş kampanya bilgilerini getirir."""
        on = urllib.parse.quote(str(order_number), safe="")
        return self._oms_get(f"/orders/merchantid/{self.merchant_id}/ordernumber/{on}/campaigns")

    # ---------- Paketleme ----------
    def get_packageable_with(self, line_item_id):
        """Bir kalemin hangi kalemlerle paketlenebileceğini döner."""
        li = urllib.parse.quote(str(line_item_id), safe="")
        return self._oms_get(f"/lineitems/merchantid/{self.merchant_id}/packageablewith/lineitemid/{li}")

    def package_items(self, line_items, parcel_quantity=1, deci=None):
        """Kalemleri paketler.
        line_items: [{"id": <lineItemId>, "quantity": <int>}]
        Doner: paket numarası içeren yanıt."""
        body = {"parcelQuantity": parcel_quantity,
                "lineItemRequests": [{"id": str(li["id"]), "quantity": int(li.get("quantity", 1))}
                                     for li in line_items]}
        if deci is not None:
            body["deci"] = deci
        return self._oms_request("POST", f"/packages/merchantid/{self.merchant_id}", json_body=body)

    def unpack_package(self, package_number):
        """Paketi bozar (unpack)."""
        pn = urllib.parse.quote(str(package_number), safe="")
        return self._oms_request("POST", f"/packages/merchantid/{self.merchant_id}/packagenumber/{pn}/unpack")

    def get_unpacked_items(self, offset=0, limit=100):
        """Paketlenmemiş kalemleri listeler."""
        return self._oms_get(f"/packages/merchantid/{self.merchant_id}/status/unpacked",
                             {"offset": offset, "limit": limit})

    def get_packages(self, offset=0, limit=100, begin_date=None, end_date=None):
        """Paket listesini döner (begindate/enddate: 'yyyy-MM-dd HH:mm')."""
        params = {"offset": offset, "limit": limit,
                  "begindate": self._oms_date(begin_date), "enddate": self._oms_date(end_date)}
        return self._oms_get(f"/packages/merchantid/{self.merchant_id}", params)

    def get_packages_delivered(self, offset=0, limit=100, begin_date=None, end_date=None):
        """Teslim edilen paketler — kargolanıp müşteriye ulaşan siparişler güncel
        paket listesinden düştüğü için geçmiş sipariş backfill'i bu ucu kullanır."""
        params = {"offset": offset, "limit": limit,
                  "begindate": self._oms_date(begin_date), "enddate": self._oms_date(end_date)}
        return self._oms_get(f"/packages/merchantid/{self.merchant_id}/delivered", params)

    def get_packages_shipped(self, offset=0, limit=100, begin_date=None, end_date=None):
        """Kargoya verilen paketler (resmî uç: /packages/merchantid/{mid}/shipped).
        HB dokümanı: limit–offset ya da beginDate–endDate zorunlu, yalnız son 1 aylık veri."""
        params = {"offset": offset, "limit": limit,
                  "begindate": self._oms_date(begin_date), "enddate": self._oms_date(end_date)}
        return self._oms_get(f"/packages/merchantid/{self.merchant_id}/shipped", params)

    def get_packages_by_status(self, status, offset=0, limit=100, begin_date=None, end_date=None):
        """Durum bazlı paket listesi (get_unpacked_items'ın /status/{x} kalıbının
        genellemesi): shipped / intransit / delivered vb. Uç HB tarafında yoksa
        HepsiburadaError fırlar — çağıran taraf probe ile ayıklar."""
        st = urllib.parse.quote(str(status), safe="")
        params = {"offset": offset, "limit": limit,
                  "begindate": self._oms_date(begin_date), "enddate": self._oms_date(end_date)}
        return self._oms_get(f"/packages/merchantid/{self.merchant_id}/status/{st}", params)

    def get_package_cargo(self, package_number):
        """Paketin kargo bilgilerini getirir."""
        pn = urllib.parse.quote(str(package_number), safe="")
        return self._oms_get(f"/packages/merchantid/packagenumber/{pn}")

    # ---------- Kargo firması değiştirme ----------
    def get_package_changeable_cargos(self, package_number):
        pn = urllib.parse.quote(str(package_number), safe="")
        return self._oms_get(f"/packages/merchantid/{self.merchant_id}/packagenumber/{pn}/changablecargocompanies")

    def change_package_cargo(self, package_number, cargo_company_short_name):
        pn = urllib.parse.quote(str(package_number), safe="")
        body = {"CargoCompanyShortName": cargo_company_short_name}
        return self._oms_request("PUT",
                                 f"/packages/merchantid/{self.merchant_id}/packagenumber/{pn}/changecargocompany",
                                 json_body=body)

    # ---------- Fatura / etiket / iptal / teslim ----------
    def send_invoice(self, package_number, invoice_link):
        """Fatura linkini Hepsiburada'ya iletir."""
        pn = urllib.parse.quote(str(package_number), safe="")
        body = {"invoiceLink": invoice_link}
        return self._oms_request("PUT",
                                 f"/packages/merchantid/{self.merchant_id}/packagenumber/{pn}/invoice",
                                 json_body=body)

    def get_cargo_label(self, package_number, fmt="base64zpl"):
        """Hepsiburada kargo etiketi (format: zpl | base64zpl | png)."""
        pn = urllib.parse.quote(str(package_number), safe="")
        path = f"/packages/merchantid/{self.merchant_id}/packagenumber/{pn}/label?format={urllib.parse.quote(fmt)}"
        return self._oms_request("GET", path)

    def cancel_line_item(self, line_item_id, reason_id="83"):
        """Kalem iptali (her iptal para cezasına tabidir). reason_id: HB iptal sebep kodu."""
        li = urllib.parse.quote(str(line_item_id), safe="")
        body = {"reasonId": str(reason_id)}
        return self._oms_request("POST",
                                 f"/lineitems/merchantid/{self.merchant_id}/id/{li}/cancelbymerchant",
                                 json_body=body)

    def send_delivered(self, package_number, received_by=None, received_date=None, digital_codes=None):
        """Teslim edildi bilgisi gönderir."""
        pn = urllib.parse.quote(str(package_number), safe="")
        body = {}
        if received_date:
            body["receivedDate"] = received_date
        if received_by:
            body["receivedBy"] = received_by
        if digital_codes:
            body["digitalCodes"] = digital_codes
        return self._oms_request("POST",
                                 f"/packages/merchantid/{self.merchant_id}/packagenumber/{pn}/deliver",
                                 json_body=body)

    # ====================================================================
    #  İADE / TALEP (OMS claim)
    # ====================================================================
    def get_claims(self, offset=0, limit=100, begin_date=None, end_date=None):
        """Tüm talep (iade) detaylarını listeler (begindate/enddate: 'yyyy-MM-dd HH:mm')."""
        params = {"offset": offset, "limit": limit,
                  "begindate": self._oms_date(begin_date), "enddate": self._oms_date(end_date)}
        return self._oms_get(f"/claims/merchantid/{self.merchant_id}", params)

    def get_claims_by_status(self, status, offset=0, limit=100):
        """Statü bazlı talep listesi.
        status: AwaitingAction | InDispute | Accepted | Rejected | Refunded | Cancelled"""
        st = urllib.parse.quote(str(status), safe="")
        return self._oms_get(f"/claims/merchantid/{self.merchant_id}/status/{st}",
                             {"offset": offset, "limit": limit})

    def accept_claim(self, claim_number):
        """Talebi (iadeyi) kabul eder."""
        cn = urllib.parse.quote(str(claim_number), safe="")
        return self._oms_request("POST", f"/claims/number/{cn}/accept")

    def reject_claim(self, claim_number, reason, merchant_statement=""):
        """Talebi (iadeyi) reddeder. reason: HB ret sebep kodu (int)."""
        cn = urllib.parse.quote(str(claim_number), safe="")
        body = {"reason": reason, "merchantStatement": merchant_statement or ""}
        return self._oms_request("POST", f"/claims/number/{cn}/reject", json_body=body)


# ====================================================================
#  Self-test (manuel) — env'den kimlik okur.
# ====================================================================
if __name__ == "__main__":
    import os
    mid = os.environ.get("HB_MERCHANT_ID", "")
    sk = os.environ.get("HB_SECRET_KEY", "")
    du = os.environ.get("HB_DEV_USERNAME", "")
    if not (mid and sk and du):
        print("HB_MERCHANT_ID / HB_SECRET_KEY / HB_DEV_USERNAME env degiskenleri gerekli.")
        raise SystemExit(1)
    hb = HepsiburadaClient(mid, sk, du, test=True)
    try:
        cats = hb.get_categories(leaf=True, active=True, available=True, size=5)
        print("Kategori örnek:", json.dumps(cats, ensure_ascii=False)[:300])
    except HepsiburadaError as e:
        print("Hata:", e)
