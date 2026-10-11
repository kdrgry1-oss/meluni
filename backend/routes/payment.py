"""
=============================================================================
payment.py — iyzico Checkout Form (Ödeme Formu) ödeme akışı
=============================================================================

Storefront checkout akışının eksik backend parçası:

  POST /api/payment/initialize  → siparişi iyzico Ödeme Formu'na hazırlar,
                                   paymentPageUrl (hosted) veya
                                   checkoutFormContent (iframe) döndürür.
  POST /api/payment/callback    → iyzico ödeme sonrası token'ı POST eder;
  GET  /api/payment/callback       token ile sonuç sorgulanır (retrieve),
                                   sipariş "ödendi" işaretlenir, kullanıcı
                                   return_url'e (storefront /odeme) yönlendirilir.

Kimlik doğrulama: IYZWSv2 (HMAC-SHA256) — yeni iyzico şeması.
Ayarlar: db.settings (id="iyzico") → api_key, api_secret, mode(live/sandbox).
=============================================================================
"""
import base64
import hashlib
import hmac
import json
import os
import re
import secrets as _secrets
from datetime import datetime, timedelta, timezone

import httpx
from fastapi import APIRouter, HTTPException, Request, Form
from fastapi.responses import RedirectResponse

import uuid as _uuid_pm
from .deps import db, logger, limiter
try:
    from .deps import client_ip_from_request as _client_ip
except Exception:  # pragma: no cover
    def _client_ip(request):
        try:
            return request.client.host if request and request.client else ""
        except Exception:
            return ""

router = APIRouter(prefix="/payment", tags=["Payment"])

# GÜVENLİK (denetim 2026-10 #2, INFRA-03): iyzico'ya bağlanırken oluşan istisnanın HAM metni
# (iç host/proxy/soket ayrıntısı) misafire açık ödeme uçlarında istemciye dönüyordu. Ayrıntı yalnız
# sunucu loguna yazılır; istemci bu genel mesajı alır. Statü/ödeme mantığı DEĞİŞMEZ.
_IYZ_CONN_ERR = "iyzico bağlantı hatası — lütfen birkaç dakika sonra tekrar deneyin"


def _card_bin(card: dict) -> str:
    num = "".join(ch for ch in str((card or {}).get("cardNumber") or "") if ch.isdigit())
    return num[:6] if len(num) >= 6 else ""


async def _card_velocity_guard(request, order_id: str) -> None:
    """A2.6 — Kart-testi (card-testing) hız sınırı. Kart hırsızları çalınmış kart listelerini
    doğrulamak için tek IP'den kısa sürede çok sayıda BAŞARISIZ ödeme dener. Aynı IP'den
    pencere içinde eşik kadar başarısız deneme olduysa 429 ile blokla. Eşik/pencere admin-
    düzenlenebilir (white-label). Başarılı ödeme sayaca girmez."""
    try:
        ip = _client_ip(request) or ""
    except Exception:
        ip = ""
    if not ip:
        return
    try:
        from business_rules import get_rule as _gr
        win_min = int(await _gr(db, "payment.card_velocity_window_min", 15) or 15)
        max_fails = int(await _gr(db, "payment.card_velocity_max_fails", 8) or 8)
    except Exception:
        win_min, max_fails = 15, 8
    if max_fails < 1:
        return
    since_ts = datetime.now(timezone.utc).timestamp() - win_min * 60
    try:
        fails = await db.card_attempts.count_documents(
            {"ip": ip, "status": {"$ne": "success"}, "ts": {"$gt": since_ts}})
    except Exception:
        return
    if fails >= max_fails:
        logger.warning(f"[KART-TESTI] IP hız sınırı aşıldı ip={ip} fails={fails} win={win_min}dk order={order_id}")
        raise HTTPException(status_code=429,
                            detail="Çok fazla başarısız ödeme denemesi yapıldı. Güvenliğiniz için bir süre sonra tekrar deneyin.")


async def _record_card_attempt(request, card: dict, order_id: str, status: str) -> None:
    """Kart denemesini (başarı/başarısızlık) hız-sınırı için kaydeder. PII saklamaz —
    yalnız IP, ilk-6 (BIN), sipariş no, durum, zaman."""
    try:
        ip = _client_ip(request) or ""
    except Exception:
        ip = ""
    now = datetime.now(timezone.utc)
    try:
        await db.card_attempts.insert_one({
            "ip": ip, "bin": _card_bin(card), "order_id": order_id,
            "status": status, "ts": now.timestamp(), "created_at": now.isoformat(),
        })
    except Exception:
        pass


def _safe_return_base(return_url: str, request) -> str:
    """OPEN-REDIRECT koruması: ödeme sonrası yönlendirilecek adres SADECE kendi
    origin'imize (istek host'u) veya markanın kendi alan adlarına izinli. Dış/kötü
    niyetli return_url (ör. https://evil.tld) reddedilip güvenli varsayılana düşülür.

    İzinli alan adları SITE_URL / PAYMENT_ALLOWED_HOSTS env'inden gelir; koda
    marka gömülü değildir.
    """
    default = f"{str(request.base_url).rstrip('/')}/odeme"
    ru = (return_url or "").strip()
    if not ru:
        return default
    # Ayrıştırıcı farkı saldırıları: ters eğik çizgi, kimlik bilgisi (@), boşluk/kontrol karakteri
    # içeren adresler tarayıcıda farklı bir host'a çözülebilir → hiç kabul edilmez.
    if any(c in ru for c in ("\\", "@")) or any(ord(c) < 33 or ord(c) == 127 for c in ru):
        return default
    try:
        from urllib.parse import urlparse
        p = urlparse(ru)
        if p.scheme not in ("http", "https"):
            return default
        host = (p.hostname or "").lower()
        req_host = (urlparse(str(request.base_url)).hostname or "").lower()
        allowed = {req_host}
        _site = (os.environ.get("SITE_URL") or "").strip()
        if _site:
            _sh = (urlparse(_site if "//" in _site else f"https://{_site}").hostname or "").lower()
            if _sh:
                allowed.add(_sh)
                # www. varyantını da kabul et (apex ↔ www yönlendirmesi bozulmasın).
                allowed.add(_sh[4:] if _sh.startswith("www.") else f"www.{_sh}")
        for _h in (os.environ.get("PAYMENT_ALLOWED_HOSTS") or "").split(","):
            _h = _h.strip().lower()
            if _h:
                allowed.add(_h)
        allowed.discard("")
        if host in allowed:
            return ru.rstrip("/")
    except Exception:
        pass
    return default


def _safe_callback_url(callback_url: str, request) -> str:
    """DENETİM (injection-redteam F4): iyzico callbackUrl İSTEMCİDEN geliyordu → saldırgan
    callback_url=https://evil verip 3DS sonrası müşteriyi ödeme token'ıyla kendi adresine
    yönlendirebiliyordu (open redirect + token sızıntısı). Yalnız KENDİ host'umuza izin ver;
    yolu koru. Dış host → reddet."""
    from urllib.parse import urlparse
    ru = (callback_url or "").strip()
    if not ru:
        raise HTTPException(status_code=400, detail="callback_url gerekli")
    if any(c in ru for c in ("\\", "@")) or any(ord(c) < 33 or ord(c) == 127 for c in ru):
        raise HTTPException(status_code=400, detail="Geçersiz callback_url")
    p = urlparse(ru)
    if p.scheme not in ("http", "https"):
        raise HTTPException(status_code=400, detail="Geçersiz callback_url")
    host = (p.hostname or "").lower()
    req_host = (urlparse(str(request.base_url)).hostname or "").lower()
    allowed = {req_host, "meluni.com.tr", "www.meluni.com.tr", "api.meluni.com.tr"}
    allowed.discard("")
    if host not in allowed:
        raise HTTPException(status_code=400, detail="callback_url yalnız kendi alan adımıza izinlidir")
    return ru


INIT_PATH = "/payment/iyzipos/checkoutform/initialize/auth/ecom"
RETRIEVE_PATH = "/payment/iyzipos/checkoutform/auth/ecom/detail"
# Doğrudan kart (kendi formumuz) — iyzico klasik Payment API yolları
THREEDS_INIT_PATH = "/payment/3dsecure/initialize"
THREEDS_AUTH_PATH = "/payment/3dsecure/auth"
NON3DS_PATH = "/payment/auth"
INSTALLMENT_PATH = "/payment/iyzipos/installment"
DEFAULT_TCKN = "11111111111"


async def _get_iyzico_settings() -> dict:
    s = await db.settings.find_one({"id": "iyzico"}, {"_id": 0})
    if not s or not s.get("api_key") or not s.get("api_secret"):
        raise HTTPException(status_code=400, detail="iyzico ödeme bilgileri eksik. Lütfen admin panelinden ayarlayın.")
    mode = s.get("mode", "sandbox")
    base = "https://api.iyzipay.com" if mode == "live" else "https://sandbox-api.iyzipay.com"
    # GÜVENLİK: api_secret at-rest şifreli saklanır; burada tek çözüm noktasıdır
    # (tüm v2 ödeme header'ları + webhook imza kontrolü buradan besler).
    # decrypt legacy düz-metni passthrough eder → mevcut kayıt kırılmaz.
    from security.crypto import decrypt as _dec_secret
    return {"api_key": s["api_key"], "api_secret": _dec_secret(s["api_secret"]) or "", "mode": mode, "base_url": base}


async def _assert_card_payments_enabled() -> None:
    """ÖDEME KILL-SWITCH (denetim 2026-10): YENİ kart ödemesi BAŞLATMA uçları
    (/initialize, /3ds/initialize, /card/pay) için. Ayarlar → Ödeme Yöntemleri'nde kredi kartı
    AÇIKÇA kapatıldıysa (`payment_methods.credit_card is False`) ya da iyzico entegrasyonu
    AÇIKÇA pasifse (`settings.iyzico.is_active is False`) yeni tahsilat başlatılmaz.
    Ayar yok/boş → AÇIK (Checkout.jsx `pm.credit_card !== false` ile aynı varsayılan).

    BİLEREK _get_iyzico_settings'e KONMADI: callback / webhook / reconcile / iade yolları da
    o fonksiyonu kullanır — zaten başlamış (kartı çekilmiş olabilecek) bir ödemenin
    sonuçlandırılması kill-switch ile ASLA engellenmemeli (değişmez 4)."""
    try:
        _main = await db.settings.find_one({"id": "main"}, {"_id": 0, "payment_methods": 1}) or {}
        _iyz = await db.settings.find_one({"id": "iyzico"}, {"_id": 0, "is_active": 1}) or {}
    except Exception:
        return  # ayar okunamadı → bugünkü davranış (engelleme yok)
    if ((_main.get("payment_methods") or {}).get("credit_card")) is False or _iyz.get("is_active") is False:
        raise HTTPException(status_code=400, detail=(
            "Kredi kartı ile ödeme şu anda kullanılamıyor. Lütfen daha sonra tekrar deneyin "
            "veya başka bir ödeme yöntemi seçin."))


def _v2_headers(api_key: str, secret_key: str, path: str, body_str: str) -> dict:
    """iyzico IYZWSv2 HMAC-SHA256 authorization header (randomKey + path + body imzalanır)."""
    random_key = _secrets.token_hex(16)
    to_sign = f"{random_key}{path}{body_str}"
    sig = hmac.new(secret_key.encode("utf-8"), to_sign.encode("utf-8"), hashlib.sha256).hexdigest()
    auth_str = f"apiKey:{api_key}&randomKey:{random_key}&signature:{sig}"
    b64 = base64.b64encode(auth_str.encode("utf-8")).decode("utf-8")
    return {
        "Authorization": f"IYZWSv2 {b64}",
        "x-iyzi-rnd": random_key,
        "Content-Type": "application/json",
    }


def _fmt(v: float) -> str:
    return f"{round(float(v or 0), 2):.2f}"


def _is_paid(data: dict, strict: bool = False) -> bool:
    """iyzico ödeme/3ds-auth yanıtından gerçek başarı tespiti.

    Not: iyzico'nun 3DS auth (ThreedsPayment) yanıtı çoğu zaman `paymentStatus`
    DÖNDÜRMEZ (null) — sadece status=success + paymentId verir. Bu yüzden
    paymentStatus=="SUCCESS" şartı koşmak başarılı ödemeyi "failed" işaretliyordu.
    Doğru başarı sinyali: API çağrısı başarılı + paymentId var + fraud reddi değil
    + paymentStatus açıkça FAILURE değil.
    """
    if data.get("status") != "success":
        return False
    if not data.get("paymentId"):
        return False
    # Denetim #3: fraudStatus 1 = güvenli (fon garanti). 0 = iyzico FRAUD İNCELEMESİNDE
    # (kabul edildi ama fon garanti DEĞİL, geri çevrilebilir) → HENÜZ 'paid' sayma; sipariş
    # ödeme-bekleniyor + needs_reconciliation kalır, inceleme geçince (fraudStatus 1) reconcile
    # onaylar. -1 = fraud reddi. Alan yoksa varsayılan 1 (klasik başarı — çoğu yanıt böyle).
    if str(data.get("fraudStatus", 1)) in ("-1", "0"):
        return False
    ps = data.get("paymentStatus")
    if strict:
        # Ödeme SORGULAMA yanıtı (/payment/detail — webhook/mutabakat yolu) her zaman
        # paymentStatus taşır; orada yalnız AÇIK "SUCCESS" ödendi sayılır (denetim 2026-10).
        # 3DS auth yanıtı (paymentStatus çoğu zaman null) bu bayrakla ÇAĞRILMAZ → davranışı aynı.
        return str(ps or "").upper() == "SUCCESS"
    return ps in (None, "", "SUCCESS")


def _payment_matches_order(data: dict, order: dict) -> bool:
    """Y1: iyzico yanıtının GERÇEKTEN bu siparişe ve tutara ait olduğunu doğrular.
    `order` en az id, order_number, total alanlarını taşımalı.
    - conversationId (bizim gönderdiğimiz = sipariş id'si) yanıtta eşleşmeli
    - basketId (bizim gönderdiğimiz = order_number/id) varsa eşleşmeli; ikisi de yoksa RED
    - currency TRY olmalı (eksik/farklı → RED)
    - ödenen tutar sipariş toplamıyla uyuşmalı (taksitte vade farkı ile >= olabilir)
    Böylece başka/ucuz bir siparişin paymentId'siyle pahalı siparişi 'ödendi' işaretlemek
    (cross-order paymentId reuse) engellenir."""
    try:
        oid = str(order.get("id") or "")
        conv = str(data.get("conversationId") or "").strip()
        if conv and oid and conv != oid:
            logger.warning(f"[ODEME DOGRULAMA] conversationId uyusmuyor: yanit={conv} siparis={oid}")
            return False
        # SİPARİŞ BAĞI (fail-closed, denetim 2026-10): basketId'yi biz init'te
        # str(order_number or id) olarak gönderiyoruz (_build_initialize_payload); iyzico tüm ödeme
        # yanıtlarında (3DS auth, non-3DS, checkout-form retrieve, /payment/detail) geri döndürür.
        # Varsa siparişle eşleşmeli. conversationId boşsa tek bağ basketId'dir; İKİSİ DE yoksa
        # yanıtın bu siparişe ait olduğu kanıtlanamaz → eşleşme YOK.
        basket = str(data.get("basketId") or "").strip()
        _basket_ok = {str(order.get("order_number") or "").strip(), oid}
        _basket_ok.discard("")
        if basket and basket not in _basket_ok:
            logger.warning(f"[ODEME DOGRULAMA] basketId uyusmuyor: yanit={basket} siparis={oid}")
            return False
        if not conv and not basket:
            logger.warning(f"[ODEME DOGRULAMA] conversationId VE basketId bos → red. siparis={oid}")
            return False
        # PARA BİRİMİ: init payload'ı her zaman TRY gönderir; FARKLI para birimli yanıt tutarı TL
        # sanılarak 'paid' sayılmamalı. Alan HİÇ yoksa reddedilmez (bazı iyzico yanıt türleri
        # alanı taşımayabilir; reddetmek kartı çekilmiş siparişi 'failed' bırakırdı) — tutar ve
        # sipariş eşleşmesi yine zorunlu.
        cur = str(data.get("currency") or "").strip().upper()
        if cur and cur != "TRY":
            logger.warning(f"[ODEME DOGRULAMA] para birimi gecersiz: yanit={cur} beklenen=TRY siparis={oid}")
            return False
        total = round(float(order.get("total") or 0), 2)
        paid = data.get("paidPrice")
        price = data.get("price")
        paid = round(float(paid), 2) if paid not in (None, "") else None
        price = round(float(price), 2) if price not in (None, "") else None
        # ÖNEMLİ: iyzico 'price' = sepet BRÜTÜ (indirim öncesi), 'paidPrice' = TAHSİL EDİLEN
        # (= order.total; taksitte vade farkıyla daha fazla). İndirim (kupon/havale) olan siparişte
        # price != total olur — bu NORMALDİR, reddedilmemeli. Yalnızca gerçek EKSİK-TAHSİLAT
        # (charged < total) reddedilir; böylece "1 TL'ye pahalı ürün" engellenir ama meşru
        # indirimli/tam ödemeler geçer.
        charged = paid if paid is not None else price
        # SAVUNMA (fail-closed): tutar alanı hiç yoksa (paidPrice VE price boş) eşleşmeyi REDDET.
        # Tutarı doğrulanamayan bir yanıt "ödendi" sayılmamalı. Gerçek iyzico başarı yanıtı her
        # zaman paidPrice taşır; bu yalnız derinlemesine-savunma (bozuk/eksik yanıt) içindir.
        if charged is None:
            logger.warning(f"[ODEME DOGRULAMA] tutar alani yok (paidPrice/price bos) → red. siparis_total={total}")
            return False
        if charged + 0.02 < total:
            logger.warning(f"[ODEME DOGRULAMA] eksik tahsilat: tahsil={charged} < siparis_total={total}")
            return False
        return True
    except Exception as _e:
        logger.warning(f"[ODEME DOGRULAMA] hata: {_e}")
        return False


def _format_gsm(phone: str) -> str:
    digits = "".join(ch for ch in str(phone or "") if ch.isdigit())
    if not digits:
        return "+905555555555"
    if digits.startswith("90") and len(digits) == 12:
        return "+" + digits
    if digits.startswith("0") and len(digits) == 11:
        return "+90" + digits[1:]
    if len(digits) == 10:
        return "+90" + digits
    return "+" + digits


def _payment_snapshot(data: dict) -> dict:
    """iyzico retrieve/auth yanıtından siparişe yazılacak ödeme görüntüsü.

    KVKK: SADECE maskeli kart bilgisi saklanır — binNumber (ilk 6) + lastFourDigits
    (son 4). Tam kart numarası ASLA tutulmaz. Banka/Iyzico komisyonu raporlama için
    eklenir.
    """
    d = data or {}
    return {
        "status": d.get("status"),
        "paymentStatus": d.get("paymentStatus"),
        "errorMessage": d.get("errorMessage"),
        "paymentId": d.get("paymentId"),
        "paidPrice": d.get("paidPrice"),
        "currency": d.get("currency"),
        "installment": d.get("installment"),
        "cardType": d.get("cardType"),
        "cardAssociation": d.get("cardAssociation"),
        "cardFamily": d.get("cardFamily"),
        "binNumber": d.get("binNumber"),            # ilk 6 (maskeli)
        "lastFourDigits": d.get("lastFourDigits"),  # son 4 (maskeli)
        "authCode": d.get("authCode"),
        "hostReference": d.get("hostReference"),
        "merchantCommissionRate": d.get("merchantCommissionRate"),
        "iyziCommissionRateAmount": d.get("iyziCommissionRateAmount"),
        "iyziCommissionFee": d.get("iyziCommissionFee"),
        # Kalem başı iyzico tahsilatı: iyzico vade farkını sepetteki TÜM kalemlere (kargo dahil)
        # orantılı dağıtır; iade/gider pusulası kalem tutarları bununla birebir olsun diye saklanır.
        "itemTransactions": [
            {"itemId": (t or {}).get("itemId"), "price": (t or {}).get("price"),
             "paidPrice": (t or {}).get("paidPrice"),
             "paymentTransactionId": (t or {}).get("paymentTransactionId")}
            for t in (d.get("itemTransactions") or []) if isinstance(t, dict)
        ][:100],
    }


def _build_initialize_payload(order: dict, callback_url: str) -> dict:
    ship = order.get("shipping_address") or {}
    bill = order.get("billing_address") or ship
    binfo = order.get("billing_info") or {}

    first = (ship.get("first_name") or "").strip() or "Müşteri"
    last = (ship.get("last_name") or "").strip() or "Meluni"
    contact = f"{first} {last}".strip()
    _raw_email = (ship.get("email") or order.get("email") or "").strip()
    email = _raw_email if re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", _raw_email) else "siparis@meluni.com.tr"
    address = (ship.get("address") or "Adres").strip()
    city = (ship.get("city") or "İstanbul").strip()
    zipcode = str(ship.get("postal_code") or ship.get("zip_code") or "34000")
    ip = order.get("customer_ip") or "85.34.78.112"

    # TCKN: kurumsal faturada 11 hane TCKN varsa onu, yoksa varsayılan placeholder
    # KVKK: TCKN at-rest şifreli (security/pii.py) → gerçek değer yalnız burada çözülür.
    from security.pii import reveal_tax_id as _reveal_tax_id
    tn = "".join(ch for ch in str(_reveal_tax_id(binfo, "tax_number") or "") if ch.isdigit())
    identity = tn if len(tn) == 11 else DEFAULT_TCKN

    bill_first = (bill.get("first_name") or first).strip()
    bill_last = (bill.get("last_name") or last).strip()
    bill_contact = f"{bill_first} {bill_last}".strip() or contact
    bill_address = (bill.get("address") or address).strip()
    bill_city = (bill.get("city") or city).strip()
    bill_zip = str(bill.get("postal_code") or bill.get("zip_code") or zipcode)

    # ----- Sepet kalemleri -----
    # İNDİRİM YALNIZ ÜRÜNLERE: kupon/kampanya indirimini ÜRÜN satırlarına oransal işleriz; kargo/
    # hediye/hizmet TAM kalır. Amaç: iyzico da indirimi kargoya YAYMASIN. Tam fiyat + düşük paidPrice
    # gönderirsek iyzico farkı TÜM kalemlere (kargo dahil) kendi dağıtır → kargo indirimli görünür,
    # KDV matrahı karışır. Bunun yerine ürünleri indirimli gönderip price=paidPrice yaparız →
    # iyzico'nun redistribüte edeceği fark kalmaz; kalemler bizim muhasebemizle birebir olur.
    # MUHASEBE MUTABAKATI (Merve, W11877): iyzico "Ödeme Kırılımları"ndaki KALEM tutarları,
    # panelde gördüğümüz kalem tutarlarıyla AYNI olmalı. Panelde indirim kampanya KAPSAMINA
    # göre kalem kalem dondurulur (item.discount_amount — W11214 kök çözümü): kapsamdaki ürüne
    # %20, kapsam dışına 0. iyzico'ya ise indirim TÜM ürünlere ORANSAL dağıtılarak gidiyordu →
    # genel toplam tutuyor ama kalemler tutmuyordu (kısmi iadede yanlış tutar riski).
    # Artık dondurulmuş kalem indirimi esas alınır; yalnız kalem-dışı (puan, hediye çeki gibi
    # sipariş-seviyesi) kalan fark oransal dağıtılır.
    _prods = []
    items_sum = 0.0
    _frozen_disc = 0.0
    for it in (order.get("items") or []):
        qty = int(it.get("quantity") or 1)
        line = round(float(it.get("price") or 0) * qty, 2)
        items_sum += line
        try:
            _d = round(float(it.get("discount_amount") or 0), 2)
        except Exception:
            _d = 0.0
        _d = max(0.0, min(_d, line))
        _frozen_disc += _d
        _prods.append([it, round(line - _d, 2)])
    _frozen_disc = round(_frozen_disc, 2)
    _gross_sum = round(items_sum, 2)
    items_sum = round(sum(p[1] for p in _prods), 2)   # kalem indirimleri düşülmüş taban

    shipping_cost = float(order.get("shipping_cost") or 0)
    gift_price = float(order.get("gift_wrap_price") or 0)
    cod_fee = float(order.get("cod_fee") or 0)  # create_order İşletme Kuralı'ndan yazar (hardcode değil)

    # Sipariş-seviyesi indirim = tam sepet − ödenen (order.total). Kargoya/ekstralara DEĞİL,
    # yalnız ürün satırlarına dağıtılır.
    _full = round(_gross_sum + shipping_cost + gift_price + cod_fee, 2)
    _raw_total = round(float(order.get("total") or 0), 2)
    _order_disc = round(max(0.0, _full - _raw_total), 2) if _raw_total > 0.009 else 0.0
    # Kalem bazında dondurulmuş indirim zaten düşüldü; geriye kalan (puan kullanımı, hediye
    # çeki, ödeme tipi indirimi gibi SİPARİŞ-seviyesi kalemler) oransal dağıtılır.
    _rest = round(max(0.0, _order_disc - _frozen_disc), 2)
    # ORAN TAVANI + SATIR TABANI (ödeme çökmesi önlemi): puan + hediye çeki, ürün
    # toplamını AŞABİLİR (kargo/hediye paketi/kapıda ödeme bedeli olan siparişlerde).
    # Oran 1'i geçince kalem netleri NEGATİF oluyor ve _fmt(max(0,line)) ile "0.00"
    # basılıyordu → iyzico sıfır fiyatlı sepet kalemini reddeder: ödeme HİÇ alınamaz,
    # üstelik hediye çeki bakiyesi rezerve edilmiş kalır (iade yalnız sipariş iptalinde).
    # Artık ürün satırları kalem başına en az 0,01'de tutulur; ürünlerin karşılayamadığı
    # indirim VIRTUAL satırlardan (kargo → hediye paketi → kapıda ödeme) düşülür, böylece
    # sepet toplamı = price = paidPrice kuruşu kuruşuna korunur.
    _MIN_LINE = 0.01
    if _rest > 0.009 and items_sum > 0.009:
        _ratio = min(1.0, _rest / items_sum)
        for p in _prods:
            p[1] = round(max(_MIN_LINE, p[1] * (1 - _ratio)), 2)
    # YUVARLAMA MUTABAKATI (iyzico şartı: sepet toplamı = price, kuruşu kuruşuna): ürün
    # satırları toplamı hedefe (ürün brüt − toplam indirim) BİREBİR eşitlenir; fark son ürüne yazılır.
    _spill = 0.0
    if _order_disc > 0.009:
        _prod_target = round(_gross_sum - _order_disc, 2)
        _floor = round(_MIN_LINE * len(_prods), 2)
        if _prod_target < _floor:
            _spill = round(_floor - _prod_target, 2)   # ürünlerin yutamadığı indirim
            _prod_target = _floor
        _cur = round(sum(p[1] for p in _prods), 2)
        _d = round(_prod_target - _cur, 2)
        if _prods and abs(_d) >= 0.01:
            if _d > 0:
                _prods[-1][1] = round(_prods[-1][1] + _d, 2)
            else:
                # eksiği sondan başa doğru, her satırı tabanın altına indirmeden düş
                for p in reversed(_prods):
                    if _d >= -0.005:
                        break
                    _take = round(min(-_d, max(0.0, p[1] - _MIN_LINE)), 2)
                    if _take >= 0.01:
                        p[1] = round(p[1] - _take, 2)
                        _d = round(_d + _take, 2)
                if _d < -0.005:
                    _spill = round(_spill + (-_d), 2)
    # Taşan indirimi VIRTUAL satırlardan sırayla düş (negatife inmeden).
    if _spill > 0.005:
        for _nm in ("shipping", "gift", "cod"):
            if _spill <= 0.005:
                break
            _val = {"shipping": shipping_cost, "gift": gift_price, "cod": cod_fee}[_nm]
            _cut = round(min(_spill, max(0.0, _val)), 2)
            if _cut >= 0.01:
                if _nm == "shipping":
                    shipping_cost = round(shipping_cost - _cut, 2)
                elif _nm == "gift":
                    gift_price = round(gift_price - _cut, 2)
                else:
                    cod_fee = round(cod_fee - _cut, 2)
                _spill = round(_spill - _cut, 2)

    basket_items = []
    for it, line in _prods:
        basket_items.append({
            "id": str(it.get("product_id") or it.get("id") or "item"),
            "name": (it.get("name") or "Ürün")[:200],
            "category1": (it.get("category") or it.get("category_name") or "Giyim")[:60],
            "itemType": "PHYSICAL",
            "price": _fmt(max(0.0, line)),
        })

    if shipping_cost > 0:
        basket_items.append({"id": "shipping", "name": "Kargo Ücreti", "category1": "Kargo", "itemType": "VIRTUAL", "price": _fmt(shipping_cost)})
    if gift_price > 0:
        basket_items.append({"id": "gift-wrap", "name": "Hediye Paketi", "category1": "Hediye", "itemType": "VIRTUAL", "price": _fmt(gift_price)})
    if cod_fee > 0:
        basket_items.append({"id": "cod-fee", "name": "Kapıda Ödeme Hizmeti", "category1": "Hizmet", "itemType": "VIRTUAL", "price": _fmt(cod_fee)})

    # price = sepet TOPLAMI (kalemlerle KURUŞU KURUŞUNA — iyzico şartı). İndirim ürün satırlarına
    # işlendiğinden price artık indirimli toplam (≈ order.total); paidPrice de aynı → iyzico'nun
    # yayacağı indirim farkı kalmaz (kargo tam bedeliyle, kendi KDV'siyle durur).
    price = round(sum(float(b["price"]) for b in basket_items), 2)
    # `or price` KULLANMA: total=0 (çek/puan her şeyi kapattı) "yok" sayılıp sepetin TAMAMI
    # çekiliyordu. 0 geçerli bir tutardır — yalnız alan HİÇ yoksa sepet toplamına düşülür.
    _t_raw = order.get("total")
    paid_price = round(float(_t_raw), 2) if _t_raw not in (None, "") else price
    # paidPrice price'ı geçemez (indirim sadece düşürür)
    if paid_price > price:
        paid_price = price
    if paid_price <= 0:
        # DEĞİŞMEZ: tahsil edilecek tutarı OLMAYAN sipariş iyzico'ya GİTMEZ. Eskiden
        # paidPrice=0 görülünce sepet toplamı (price) çekiliyordu → hediye çeki/puan ile
        # tamamen kapanmış siparişte müşteriden borcu olmayan para çekilebilirdi.
        # (create_order bu siparişi zaten paid/confirmed açar; burası ikinci kapı.)
        raise ValueError("Bu siparişte tahsil edilecek tutar yok (hediye çeki/puan ile kapandı).")

    return {
        "locale": "tr",
        "conversationId": str(order.get("id")),
        "price": _fmt(price),
        "paidPrice": _fmt(paid_price),
        "currency": "TRY",
        "basketId": str(order.get("order_number") or order.get("id")),
        "paymentGroup": "PRODUCT",
        "callbackUrl": callback_url,
        "enabledInstallments": [1, 2, 3, 6, 9],
        "buyer": {
            "id": str(order.get("user_id") or order.get("id")),
            "name": first,
            "surname": last,
            "identityNumber": identity,
            "email": email,
            "gsmNumber": _format_gsm(ship.get("phone")),
            "registrationAddress": address,
            "city": city,
            "country": "Turkey",
            "zipCode": zipcode,
            "ip": ip,
        },
        "shippingAddress": {
            "contactName": contact,
            "city": city,
            "country": "Turkey",
            "address": address,
            "zipCode": zipcode,
        },
        "billingAddress": {
            "contactName": bill_contact,
            "city": bill_city,
            "country": "Turkey",
            "address": bill_address,
            "zipCode": bill_zip,
        },
        "basketItems": basket_items,
    }


@router.post("/initialize")
@(limiter.limit("20/minute") if limiter else (lambda f: f))
async def initialize_payment(request: Request, order_id: str, callback_url: str, return_url: str = "",
                             pay_token: str = ""):
    """Sipariş için iyzico Ödeme Formu başlatır (rate-limited)."""
    _ = request
    order = await db.orders.find_one({"id": order_id}, {"_id": 0})
    if not order:
        raise HTTPException(status_code=404, detail="Sipariş bulunamadı")
    _assert_payment_capability(order, pay_token or request.headers.get("x-pay-token"))
    if order.get("coupon_superseded_by"):
        raise HTTPException(status_code=409, detail=_SUPERSEDED_MSG)
    # GÜVENLİK: Zaten ödenmiş/tamamlanmış/iptal siparişte ödeme başlatma — kart-testi
    # ve tekrar-tahsilat yüzeyini kapatır.
    if order.get("payment_status") in ("paid", "refunded") or \
       order.get("status") in ("confirmed", "shipped", "delivered", "cancelled"):
        raise HTTPException(status_code=400, detail="Bu sipariş için ödeme alınamaz")
    _pay_gate_expired(order)
    await _assert_card_payments_enabled()  # kill-switch (yalnız YENİ başlatma)

    callback_url = _safe_callback_url(callback_url, request)  # F4: dış callback reddedilir
    settings = await _get_iyzico_settings()
    try:
        payload = _build_initialize_payload(order, callback_url)
    except ValueError as _zv:
        raise HTTPException(status_code=400, detail=str(_zv))
    body_str = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    headers = _v2_headers(settings["api_key"], settings["api_secret"], INIT_PATH, body_str)

    try:
        async with httpx.AsyncClient(timeout=30) as c:
            resp = await c.post(f"{settings['base_url']}{INIT_PATH}", content=body_str.encode("utf-8"), headers=headers)
        data = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {"status": "failure", "errorMessage": resp.text[:300]}
    except Exception as e:
        logger.error(f"iyzico initialize error: {e}")
        raise HTTPException(status_code=502, detail=_IYZ_CONN_ERR)  # ayrıntı yalnız logda (INFRA-03)

    if data.get("status") != "success":
        logger.warning(f"iyzico initialize failed order={order_id}: {data.get('errorCode')} {data.get('errorMessage')}")
        return {
            "success": False,
            "error": data.get("errorMessage") or "Ödeme başlatılamadı",
            "errorCode": data.get("errorCode"),
        }

    await db.orders.update_one(
        {"id": order_id, "payment_status": {"$nin": list(_PAID_LIKE_STATUSES)}},
        {"$set": {
            "iyzico_token": data.get("token"),
            "iyzico_conversation_id": data.get("conversationId"),
            "iyzico_return_url": return_url,
            "payment_provider": "iyzico",
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }},
    )

    return {
        "success": True,
        "token": data.get("token"),
        "paymentPageUrl": data.get("paymentPageUrl"),
        "checkoutFormContent": data.get("checkoutFormContent"),
    }


async def _notify_paid_order_confirmed(order_id: str) -> None:
    """Ödeme başarıyla geçtikten (paid → confirmed) SONRA 'Siparişiniz Alındı'
    onay bildirimini BİR KEZ gönderir. Kart (iyzico) siparişlerinde onay maili
    artık sipariş oluşturma anında değil, yalnızca ödeme onaylandıktan sonra
    buradan çıkar — böylece başarısız/yarıda kalan ödemede müşteriye yanlış
    "alındı" maili gitmez. Idempotent: order_confirmed_notified bayrağı çift
    maili (yarış / tekrar gelen callback) engeller. Hata olsa bile ödeme akışını
    bozmaz (fire-and-forget mantığı; yalnızca loglar)."""
    try:
        order = await db.orders.find_one({"id": order_id}, {"_id": 0})
        if not order:
            return
        # Y6: Kupon kullanımını ÖDEME ONAYINDAN sonra kaydet (başarısız ödemede kupon yanmaz).
        # İdempotent; ikinci callback'te tekrar yazmaz.
        try:
            from .orders import record_order_redemptions
            # Başarılıysa 'coupon_redemptions_recorded' bayrağı yazılır → kaçırılan yan etki
            # süpürmesi (scheduler.sweep_paid_order_side_effects) yalnız eksik olanları yeniden dener.
            if await record_order_redemptions(order) is not False and not order.get("coupon_redemptions_recorded"):
                await db.orders.update_one({"id": order_id}, {"$set": {"coupon_redemptions_recorded": True}})
        except Exception as _re:
            logger.warning(f"Odeme sonrasi kupon kaydi hatasi order_id={order_id}: {_re}")
        if order.get("order_confirmed_notified"):
            return
        # Önce işaretle (tek mail garantisi), sonra gönder. ATOMİK claim (denetim 2026-10):
        # eşzamanlı iki çağrı (callback + webhook/kurtarma) bayrağı aynı anda unset görüp
        # İKİ mail/push gönderebiliyordu; yalnız bayrağı gerçekten çeviren devam eder.
        _nclaim = await db.orders.update_one(
            {"id": order_id, "order_confirmed_notified": {"$ne": True}},
            {"$set": {"order_confirmed_notified": True}}
        )
        if _nclaim.modified_count != 1:
            return
        import sys, os
        sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
        from notification_service import send_notification
        from .orders import _order_notify_vars
        ship = order.get("shipping_address") or {}
        variables = await _order_notify_vars(order)
        await send_notification(
            db, "order_confirmed",
            to_phone=ship.get("phone") or order.get("phone"),
            to_email=ship.get("email") or order.get("email"),
            variables=variables,
        )
        logger.info(
            f"order_confirmed (ödeme sonrası) gönderildi order={order.get('order_number')}"
        )
        # Admin ANLIK PUSH: kart siparişinde "yeni sipariş" push'u OLUŞTURMADA değil, ödeme
        # ONAYLANINCA buradan gider (yarıda kalan 3DS'te admin'e sahte yeni-sipariş gitmesin).
        try:
            from .push import send_push_to_admins
            # GÜVENLİK (ADM-15): push kilit ekranında görünür / FCM'den geçer → müşteri adı ve tutar
            # YOK; yalnız sipariş no + ürün adedi (ayrıntı panelde, yetkiyle).
            _n_items = 0
            for _it in (order.get("items") or []):
                try:
                    _n_items += max(1, int((_it or {}).get("quantity") or 1))
                except (TypeError, ValueError):
                    _n_items += 1
            await send_push_to_admins(
                "🛍️ Yeni Sipariş (ödendi)",
                f"Yeni sipariş {order.get('order_number','')} · {_n_items} ürün",
                {"type": "new_order", "order_id": str(order.get("id") or ""),
                 "order_number": str(order.get("order_number") or "")},
            )
        except Exception as _pe:
            logger.warning(f"admin push (kart ödeme sonrası) atlandı: {_pe}")
        # ADMIN E-POSTA BİLDİRİMİ: web sitesinden gelen (ödemesi ONAYLANMIŞ) sipariş için
        # firmanın iletişim adresine (beyaz-etiket: company.contact_email → varsayılan
        # info@meluni.com.tr) özet mail. order_confirmed_notified bayrağıyla tek sefer.
        # Yarıda kalan 3DS'te BURAYA gelinmez → sahte "yeni sipariş" maili gitmez.
        try:
            import company as _company
            from notification_service import _email_send
            _ci = await _company.get_company(db)
            _admin_to = str(_ci.get("contact_email") or _ci.get("email") or "info@meluni.com.tr").strip()
            if _admin_to:
                from notification_service import build_admin_order_email_html as _bld
                _html = _bld(order, ship, "Yeni Sipariş", "Ödendi ✓")
                await _email_send(db, _admin_to, f"Yeni Sipariş · {order.get('order_number','')}", _html)
                logger.info(f"admin sipariş maili gönderildi → {_admin_to} order={order.get('order_number')}")
        except Exception as _ae:
            logger.warning(f"admin sipariş maili atlandı: {_ae}")
    except Exception as e:
        logger.warning(
            f"order_confirmed (ödeme sonrası) gönderilemedi order_id={order_id}: {e}"
        )


async def _retrieve_and_finalize(token: str) -> dict:
    """token ile iyzico'dan sonucu çeker, siparişi günceller. Döner: {ok, order, return_url}."""
    order = await db.orders.find_one({"iyzico_token": token}, {"_id": 0})
    if not order:
        raise HTTPException(status_code=404, detail="Ödeme token'ı için sipariş bulunamadı")

    settings = await _get_iyzico_settings()
    payload = {"locale": "tr", "conversationId": str(order.get("id")), "token": token}
    body_str = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    headers = _v2_headers(settings["api_key"], settings["api_secret"], RETRIEVE_PATH, body_str)

    async with httpx.AsyncClient(timeout=30) as c:
        resp = await c.post(f"{settings['base_url']}{RETRIEVE_PATH}", content=body_str.encode("utf-8"), headers=headers)
    data = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {"status": "failure"}

    # TEK YETKİLİ YOL (CLAUDE.md değişmez 1; denetim 2026-09-29): eskiden bu eski hosted-form yolu
    # siparişi kendisi, DURUM KONTROLSÜZ paid/confirmed yapıyordu (replay ile kargolanmış/iptal
    # sipariş geri "onaylandı"ya dönebiliyordu). Artık tutar+sipariş eşleşmesi, paymentId replay
    # kalkanı, idempotency ve bildirim/CAPI tek yerden: _mark_order_from_payment.
    paid = await _mark_order_from_payment(order.get("id"), data)
    return {"ok": paid, "order": order, "return_url": order.get("iyzico_return_url") or ""}


PAYMENT_DETAIL_PATH = "/payment/detail"


async def _finalize_by_payment_id(order_id: str, payment_id: str, raise_on_uncertain: bool = False) -> bool:
    """3DS/kart akışında (hosted token YOK) paymentId ile iyzico'dan ödemeyi retrieve edip
    finalize eder — webhook/reconcile güvenlik ağı; 'çekildi ama kayıt yok/failed' vakalarını
    kapatır. Belirsiz cevapta (non-JSON/exception) siparişe DOKUNMAZ (failed yazmaz).
    raise_on_uncertain=True (webhook): belirsizlikte istisna fırlatır → webhook 500 döner,
    iyzico bildirimi YENİDEN dener."""
    if not order_id or not payment_id:
        return False
    settings = await _get_iyzico_settings()
    payload = {"locale": "tr", "conversationId": str(order_id), "paymentId": str(payment_id)}
    body_str = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    headers = _v2_headers(settings["api_key"], settings["api_secret"], PAYMENT_DETAIL_PATH, body_str)
    try:
        async with httpx.AsyncClient(timeout=30) as c:
            resp = await c.post(f"{settings['base_url']}{PAYMENT_DETAIL_PATH}", content=body_str.encode("utf-8"), headers=headers)
        if not resp.headers.get("content-type", "").startswith("application/json"):
            if raise_on_uncertain:
                raise RuntimeError(f"iyzico payment-detail JSON dönmedi (HTTP {resp.status_code})")
            return False   # belirsiz → failed yazma
        data = resp.json()
    except Exception as e:
        logger.warning(f"iyzico payment-detail retrieve hata order={order_id} pid={payment_id}: {e}")
        if raise_on_uncertain:
            raise
        return False
    # SIKI DOĞRULAMA (denetim 2026-10): sorgu yanıtında paymentStatus YOKSA sonuç belirsizdir →
    # siparişe dokunma (ne paid ne failed). Varsa yalnız açık "SUCCESS" ödendi sayılır.
    if isinstance(data, dict) and str(data.get("status") or "") == "success" \
            and not str(data.get("paymentStatus") or "").strip():
        logger.warning(f"iyzico payment-detail paymentStatus boş → belirsiz, siparişe dokunulmadı order={order_id}")
        if raise_on_uncertain:
            raise RuntimeError("iyzico payment-detail paymentStatus boş (belirsiz)")
        return False
    return await _mark_order_from_payment(order_id, data, strict_status=True)


@router.api_route("/callback", methods=["POST", "GET"])
async def payment_callback(request: Request, token: str = Form(default=None)):
    """iyzico ödeme sonrası buraya yönlendirir; sonucu doğrulayıp storefront'a geri yollar."""
    tok = token
    if not tok:
        tok = request.query_params.get("token")
    if not tok:
        try:
            form = await request.form()
            tok = form.get("token")
        except Exception:
            tok = None

    if not tok:
        raise HTTPException(status_code=400, detail="Ödeme token'ı bulunamadı")

    result = await _retrieve_and_finalize(tok)
    order = result["order"]
    return_base = _safe_return_base(result["return_url"], request)
    sep = "&" if "?" in return_base else "?"
    status = "success" if result["ok"] else "fail"
    redirect = f"{return_base}{sep}status={status}&order={order.get('order_number')}"
    # 303: tarayıcı POST'u GET'e çevirip storefront'a gider
    return RedirectResponse(url=redirect, status_code=303)



# =============================================================================
# DOĞRUDAN KART İLE ÖDEME (kendi formumuz) — iyzico Payment API
# iyzico hosted Checkout Form yerine: müşteri kartı sitemizdeki formda girer.
#   POST /payment/3ds/initialize → 3DS başlat, threeDSHtmlContent döner (banka OTP)
#   POST /payment/3ds/callback   → banka/iyzico geri döner, auth ile finalize
#   POST /payment/card/pay       → 3DS'siz doğrudan tahsilat (use3DSecure kapalıysa)
# NOT: Kart verisi YALNIZCA istek gövdesinde taşınır, asla loglanmaz/saklanmaz.
# =============================================================================
async def _installment_total_price(settings: dict, bin_number: str, base_price: float, installment: int):
    """Seçilen taksit için iyzico'dan GERÇEK totalPrice'i (vade farkı dahil) sorgular.

    Taksitli işlemde iyzico'ya gönderilecek `paidPrice` bu tutar olmalıdır; aksi
    halde (paidPrice = tek çekim tutarı + installment>1) banka/iyzico işlemi reddeder.
    Frontend'den gelen tutara GÜVENİLMEZ — değer iyzico'dan doğrulanır.
    Bulunamazsa None döner (çağıran tek çekim tutarına düşer).
    """
    bin6 = "".join(ch for ch in str(bin_number or "") if ch.isdigit())[:8]
    if len(bin6) < 6 or base_price <= 0 or installment <= 1:
        return None
    body = {"locale": "tr", "conversationId": "installment", "binNumber": bin6, "price": _fmt(base_price)}
    body_str = json.dumps(body, separators=(",", ":"), ensure_ascii=False)
    headers = _v2_headers(settings["api_key"], settings["api_secret"], INSTALLMENT_PATH, body_str)
    try:
        async with httpx.AsyncClient(timeout=20) as c:
            resp = await c.post(f"{settings['base_url']}{INSTALLMENT_PATH}", content=body_str.encode("utf-8"), headers=headers)
        data = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
    except Exception as e:
        logger.error(f"iyzico installment paidPrice resolve error: {e}")
        return None
    if data.get("status") != "success":
        return None
    for det in (data.get("installmentDetails") or []):
        for ip in (det.get("installmentPrices") or []):
            try:
                if int(ip.get("installmentNumber") or 0) == int(installment):
                    tp = round(float(ip.get("totalPrice") or 0), 2)
                    return tp if tp > 0 else None
            except Exception:
                pass
    return None


def _build_card_payment_payload(order: dict, card: dict, installment: int,
                                callback_url: str, is_3ds: bool) -> dict:
    """Checkout-form payload'ını doğrudan-kart isteğine uyarlar (paymentCard ekler)."""
    try:
        base = _build_initialize_payload(order, callback_url)
    except ValueError as _zv:
        raise HTTPException(status_code=400, detail=str(_zv))
    base.pop("enabledInstallments", None)
    if not is_3ds:
        base.pop("callbackUrl", None)
    num = "".join(ch for ch in str(card.get("cardNumber") or "") if ch.isdigit())
    exp_m = str(card.get("expireMonth") or "").strip().zfill(2)
    ey = "".join(ch for ch in str(card.get("expireYear") or "") if ch.isdigit())
    exp_y = ("20" + ey) if len(ey) == 2 else ey
    base["paymentChannel"] = "WEB"
    base["installment"] = int(installment or 1)
    base["paymentCard"] = {
        "cardHolderName": (card.get("cardHolderName") or "").strip(),
        "cardNumber": num,
        "expireMonth": exp_m,
        "expireYear": exp_y,
        "cvc": str(card.get("cvc") or "").strip(),
        "registerCard": 0,
    }
    return base



# DENETİM (2026-09-29, K1/O1): süresi dolup otomatik kapatılmış (stok/hediye çeki/puan İADE
# edilmiş) siparişe sonradan ödeme başlatılamaz — aksi halde iade edilmiş çek/puan bakiyesi
# müşteride kalırken sipariş yeniden 'paid' olabiliyordu. Havale/kapıda siparişi de kartla
# ödenemez (havale indirimi karta taşınmasın). Müşteri sepetten yeni sipariş açar.
# Ödemesi alınmış sayılan durumlar: bu siparişe yeni ödeme başlatılmaz / paid geçişi yapılmaz.
_PAID_LIKE_STATUSES = ("paid", "refunded", "partially_refunded")
_EXPIRED_ORDER_STATUSES = {"payment_failed", "cancel_requested", "cancel_refunded", "refunded", "returned"}
_NON_CARD_METHODS = {"bank_transfer", "havale", "eft", "havale_eft", "banka_havale",
                     "cash_on_delivery", "kapida", "kapida_odeme", "cod", "transfer"}


# ── Denetim #4: siparişe özel ödeme yetkisi (capability) ─────────────────────────────────────
# Ödeme başlatma uçları (initialize / 3ds/initialize / card/pay) eskiden yalnız sipariş id'si ile
# çalışıyordu: id'yi ele geçiren biri başkasının bekleyen ödemesinin token'ını değiştirip akışı
# bozabiliyordu. Artık sipariş oluşturulurken yalnız oluşturan istemciye, tahmin edilemez bir
# "pay_token" verilir (DB'de yalnız SHA-256 özeti); ödeme başlatma bu token'ı ister.
# Bu alan ödeme DURUMUNA dokunmaz (değişmez 1 korunur) — yalnız başlatmayı yetkilendirir.
PAY_CAP_ENFORCED = True   # vitrin (cf90db2) pay_token gönderiyor — zorunluluk açık


def _pay_cap_hash(token: str) -> str:
    return hashlib.sha256(("paycap:" + str(token or "")).encode("utf-8")).hexdigest()


async def issue_payment_capability(order_id: str) -> str:
    """Sipariş için yeni ödeme yetkisi üretir; son 5 özet saklanır (idempotent tekrar POST'ta da
    istemci geçerli bir token alır)."""
    token = _secrets.token_urlsafe(32)
    await db.orders.update_one(
        {"id": order_id},
        {"$push": {"pay_cap_hashes": {"$each": [_pay_cap_hash(token)], "$slice": -5}}})
    return token


def _assert_payment_capability(order: dict, token) -> None:
    hashes = [str(h) for h in (order.get("pay_cap_hashes") or []) if h]
    if not hashes:
        # Bu sürümden önce oluşturulmuş sipariş (ödenmezse en geç 3 saatte otomatik iptal edilir).
        return
    tok = str(token or "").strip()
    if tok:
        got = _pay_cap_hash(tok)
        if any(hmac.compare_digest(got, h) for h in hashes):
            return
    if PAY_CAP_ENFORCED:
        raise HTTPException(status_code=403, detail="Ödeme oturumu doğrulanamadı. Sayfayı yenileyip tekrar deneyin.")


def _pay_gate_expired(order: dict) -> None:
    if str(order.get("status") or "") in _EXPIRED_ORDER_STATUSES or \
       str(order.get("payment_status") or "") == "expired" or order.get("_restocked_by_autocancel"):
        raise HTTPException(status_code=400, detail=(
            "Bu siparişin ödeme süresi doldu. Lütfen sepetinizden yeniden sipariş oluşturun."))
    if str(order.get("payment_method") or "").strip().lower() in _NON_CARD_METHODS:
        raise HTTPException(status_code=400, detail="Bu sipariş kartla ödenemez")


# ÖDEME BAŞLATMA KİLİDİ (denetim 2026-10): aynı sipariş için iki sekmede / çift tıkla eşzamanlı
# kart ödemesi başlatılıp İKİ kez çekim yapılabiliyordu. Kısa ömürlü atomik kilit: yalnız kilit
# yoksa veya süresi dolmuşsa alınır. Başarısız başlatmada hemen bırakılır; 3DS başarılı
# başlatıldıysa banka dönüşüne (callback) kadar tutulur, en geç süre dolunca kendiliğinden düşer
# → meşru yeniden deneme hiçbir koşulda bu pencereden uzun engellenmez.
_PAY_INIT_LOCK_SECONDS = 90


_SUPERSEDED_MSG = ("Bu siparişteki indirim hakkınız daha yeni bir siparişinizde kullanıldı. "
                   "Lütfen sepetinizi yenileyip tekrar deneyin.")


def _lock_ts(dt: datetime) -> str:
    # Sabit biçim (UTC, milisaniye) → string karşılaştırması zaman sırasıyla birebir.
    return dt.astimezone(timezone.utc).isoformat(timespec="milliseconds")


async def _acquire_payment_init_lock(order_id: str) -> str:
    now = datetime.now(timezone.utc)
    lock_id = _secrets.token_hex(8)
    got = await db.orders.find_one_and_update(
        {"id": order_id,
         "payment_status": {"$nin": list(_PAID_LIKE_STATUSES)},
         # Denetim #21: kuponu yeni bir siparişle geçersiz kılınmış deneme ödenemez (atomik).
         "coupon_superseded_by": {"$exists": False},
         "$or": [{"payment_init_lock_until": None},
                 {"payment_init_lock_until": {"$lte": _lock_ts(now)}}]},
        {"$set": {"payment_init_lock_until": _lock_ts(now + timedelta(seconds=_PAY_INIT_LOCK_SECONDS)),
                  "payment_init_lock_id": lock_id}},
        projection={"_id": 0, "id": 1},
    )
    if got:
        return lock_id
    cur = await db.orders.find_one({"id": order_id}, {"_id": 0, "payment_status": 1, "coupon_superseded_by": 1})
    if not cur:
        raise HTTPException(status_code=404, detail="Sipariş bulunamadı")
    if str(cur.get("payment_status") or "").lower() in _PAID_LIKE_STATUSES:
        raise HTTPException(status_code=400, detail="Bu siparişin ödemesi zaten alınmış.")
    if cur.get("coupon_superseded_by"):
        raise HTTPException(status_code=409, detail=_SUPERSEDED_MSG)
    logger.warning(f"[ODEME] eşzamanlı ödeme başlatma reddedildi (kilit aktif) order_id={order_id}")
    raise HTTPException(status_code=409, detail=(
        "Bu sipariş için başka bir ödeme işlemi şu anda devam ediyor. Kartınızdan iki kez çekim "
        "yapılmaması için lütfen 1-2 dakika bekleyip sayfayı yenileyerek tekrar deneyin."))


async def _release_payment_init_lock(order_id: str, lock_id: str = None) -> None:
    """Kilidi bırakır. lock_id verilirse yalnız O kilit bırakılır (başka denemenin kilidine dokunmaz)."""
    if not order_id:
        return
    q = {"id": order_id}
    if lock_id:
        q["payment_init_lock_id"] = lock_id
    try:
        await db.orders.update_one(q, {"$unset": {"payment_init_lock_until": "", "payment_init_lock_id": ""}})
    except Exception as _le:
        logger.warning(f"[ODEME] başlatma kilidi bırakılamadı order_id={order_id}: {_le} (süre dolunca düşer)")

async def _record_duplicate_payment(order_id: str, data: dict, existing_pid: str) -> None:
    """ÇİFT TAHSİLAT KAYDI (denetim 2026-10): sipariş ZATEN ödenmişken iyzico'dan DOĞRULANMIŞ
    (_is_paid + _payment_matches_order) ve FARKLI paymentId'li ikinci bir başarılı ödeme gelirse
    (müşteri iki sekmede iki kez ödedi) eskiden sessizce yok sayılıyordu → para müşteriden çekilmiş,
    hiçbir yerde kaydı yok. Artık: siparişe duplicate_payments kalemi + needs_reconciliation +
    needs_duplicate_refund bayrağı + admin notu + entegrasyon logu + admin push. SİPARİŞ
    STATÜSÜNE/ÖDEME DURUMUNA DOKUNMAZ (değişmez 1). İdempotent: aynı paymentId bir kez yazılır."""
    pid = str((data or {}).get("paymentId") or "").strip()
    if not order_id or not pid:
        return
    now_iso = datetime.now(timezone.utc).isoformat()
    entry = {
        "paymentId": pid,
        "amount": (data or {}).get("paidPrice") if (data or {}).get("paidPrice") not in (None, "") else (data or {}).get("price"),
        "currency": (data or {}).get("currency"),
        "installment": (data or {}).get("installment"),
        "original_payment_id": str(existing_pid or ""),
        "status": "needs_refund",
        "at": now_iso,
    }
    _txt = (f"⚠ ÇİFT TAHSİLAT: Sipariş zaten ödenmişken iyzico'da ikinci başarılı ödeme bulundu "
            f"(paymentId={pid}, tutar={entry['amount']} {entry['currency'] or ''}). Bu tutar müşteriye "
            f"İADE edilmelidir (iyzico panelinden kontrol edin).")
    try:
        res = await db.orders.update_one(
            {"id": order_id, "duplicate_payments.paymentId": {"$ne": pid}},
            {"$push": {"duplicate_payments": entry,
                       "admin_notes": {"id": str(_uuid_pm.uuid4()), "text": _txt, "by": "sistem", "at": now_iso}},
             "$set": {"needs_reconciliation": True, "needs_duplicate_refund": True, "updated_at": now_iso}})
    except Exception as _de:
        logger.error(f"[ODEME] ÇİFT TAHSİLAT kaydı YAZILAMADI order_id={order_id} pid={pid}: {_de}")
        return
    if res.modified_count != 1:
        return  # bu paymentId zaten kayıtlı (tekrar gelen sonuç)
    logger.error(f"[ODEME] ÇİFT TAHSİLAT order_id={order_id} yeni_pid={pid} mevcut_pid={existing_pid} "
                 f"tutar={entry['amount']} → iade gerekli")
    try:
        from .integrations_common import log_integration_event
        await log_integration_event(
            "iyzico", "duplicate_payment", "order", order_id, "error",
            f"Çift tahsilat: sipariş zaten ödenmiş (pid={existing_pid}); ikinci ödeme pid={pid} "
            f"tutar={entry['amount']} — iade gerekli",
            {"duplicate_payment": entry})
    except Exception as _le:
        logger.warning(f"[ODEME] çift tahsilat entegrasyon logu yazılamadı: {_le}")
    try:
        from .push import send_push_to_admins
        _on = await db.orders.find_one({"id": order_id}, {"_id": 0, "order_number": 1})
        await send_push_to_admins(
            "⚠ Çift tahsilat — iade gerekli",
            f"{(_on or {}).get('order_number', '')} · ikinci kart ödemesi · {entry['amount']} {entry['currency'] or ''}".strip(),
            {"type": "duplicate_payment", "order_id": str(order_id),
             "order_number": str((_on or {}).get("order_number") or "")},
        )
    except Exception as _pe:
        logger.warning(f"[ODEME] çift tahsilat admin push atlandı: {_pe}")


async def _maybe_record_duplicate(order_id: str, data: dict, verified_paid: bool) -> None:
    """Zaten ödenmiş siparişe gelen DOĞRULANMIŞ ödeme farklı paymentId taşıyorsa çift tahsilat
    olarak kaydeder. Aynı paymentId (callback/webhook tekrarı) → no-op."""
    if not verified_paid:
        return
    pid = str((data or {}).get("paymentId") or "").strip()
    if not pid:
        return
    cur = await db.orders.find_one(
        {"id": order_id}, {"_id": 0, "payment_status": 1, "iyzico_payment_id": 1, "payment_id": 1})
    if not cur or str(cur.get("payment_status") or "").lower() not in _PAID_LIKE_STATUSES:
        return
    existing = str(cur.get("iyzico_payment_id") or cur.get("payment_id") or "").strip()
    if existing == pid:
        return  # aynı ödemenin tekrarı — idempotent
    await _record_duplicate_payment(order_id, data, existing)


async def _redecrement_after_cancel_on_paid(order_id: str) -> bool:
    """Ödeme doğrulanıp sipariş paid'e geçtiğinde (yalnız _mark_order_from_payment'ın KAZANAN
    çağrısından), siparişin TÜKETİLMEMİŞ stok-iade (restore) hareketleri varsa — ör. personel
    ödenmemiş kart siparişini iptal edip stoğu iade etti, banka dönüşü sonra geldi — iade edilen
    miktarı yeniden düşer. update_order_status reaktivasyon mantığıyla aynı kaynak (restore
    hareketleri) kullanılır; düşüm _stock_delta_for_order ile ATOMİK-koşullu (stok yetmezse 0'da
    durur + admin push). İdempotent: _redecremented_after_cancel bayrağı koşullu claim edilir.
    Sipariş manuel incelemeye işaretlenir (paid_after_cancel). Ödeme durumuna DOKUNMAZ."""
    try:
        from .orders import (_RESTORE_MOVE_TYPES, _stock_delta_for_order, _consume_restore_moves,
                             _reverse_stock_moves, _resolve_move_level)
    except Exception as _ie:  # pragma: no cover
        logger.warning(f"[ODEME] restore yardımcıları yüklenemedi: {_ie}")
        return False
    restores = await db.stock_movements.find(
        {"order_id": order_id, "type": {"$in": list(_RESTORE_MOVE_TYPES)}},
        {"_id": 0, "items": 1, "type": 1}).to_list(100)
    if not restores:
        return False
    claim = await db.orders.update_one(
        {"id": order_id, "_redecremented_after_cancel": {"$ne": True}},
        {"$set": {"_redecremented_after_cancel": True}})
    if claim.modified_count != 1:
        return False
    try:
        by_bc, no_bc = {}, []
        for mv in restores:
            for it in (mv.get("items") or []):
                d = int(it.get("delta") or 0)
                if d <= 0:
                    continue
                bc = str(it.get("barcode") or "").strip()
                if bc:
                    by_bc[bc] = by_bc.get(bc, 0) + d
                else:
                    no_bc.append({"barcode": "", "product_id": it.get("product_id"),
                                  "variant_id": it.get("variant_id"),
                                  "level": await _resolve_move_level(it), "delta": d})
        o = await db.orders.find_one({"id": order_id}, {"_id": 0, "order_number": 1, "platform": 1}) or {}
        virt = {"order_number": o.get("order_number", ""), "platform": o.get("platform") or "meluni",
                "items": [{"barcode": bc, "quantity": q} for bc, q in by_bc.items()]}
        moves = await _stock_delta_for_order(virt, -1) if virt["items"] else []
        if no_bc:
            await _reverse_stock_moves(no_bc)   # delta>0 verilir → iade edileni geri DÜŞER
            moves += [{**x, "delta": -x["delta"]} for x in no_bc]
        await _consume_restore_moves(order_id, "paid_after_cancel_redecrement")
        now = datetime.now(timezone.utc).isoformat()
        if moves:
            await db.stock_movements.insert_one({
                "id": str(_uuid_pm.uuid4()), "type": "paid_after_cancel_redecrement",
                "order_id": order_id, "order_number": o.get("order_number", ""),
                "items": moves, "source": "payment_after_cancel", "created_at": now})
        _oversold = any(m.get("oversold") for m in moves)
        _txt = ("⚠ Sipariş iptal edilip stok iade edildikten SONRA banka ödemeyi doğruladı (para çekildi). "
                "Sipariş ödenmiş/onaylı; iade edilen stok yeniden düşüldü"
                + (" — STOK YETMEDİ, 0'a sabitlendi (oversell riski)" if _oversold else "")
                + ". Siparişi gönderin ya da müşteriye iade yapın.")
        await db.orders.update_one({"id": order_id}, {
            "$set": {"needs_manual_review": True, "paid_after_cancel": True,
                     "manual_review_reason": "paid_after_cancel"},
            "$push": {"admin_notes": {"id": str(_uuid_pm.uuid4()), "text": _txt, "by": "sistem", "at": now}}})
        try:
            from .integrations_common import log_integration_event
            await log_integration_event("payment", "paid_after_cancel", "order", o.get("order_number", order_id),
                                        "warning", _txt, {"moves": len(moves), "oversold": _oversold})
        except Exception:
            pass
        logger.error(f"[ODEME] iptal sonrası ödeme doğrulandı → stok yeniden düşüldü, manuel inceleme order_id={order_id}")
        return True
    except Exception as _e:
        await db.orders.update_one({"id": order_id}, {"$unset": {"_redecremented_after_cancel": ""}})
        logger.warning(f"[ODEME] iptal-sonrası stok yeniden düşümü başarısız order_id={order_id}: {_e}")
        return False


async def _mark_order_from_payment(order_id: str, data: dict, strict_status: bool = False) -> bool:
    """iyzico ödeme/3ds-auth yanıtına göre siparişi günceller. Döner: paid(bool).

    TEK YETKİLİ 'paid/confirmed' YOLU (CLAUDE.md değişmez 1): _is_paid VE _payment_matches_order
    VE cross-order replay kalkanı geçmeden sipariş ödendi sayılmaz. Geçiş ATOMİK-KOŞULLUDUR
    (payment_status ödenmiş değilse); yalnız geçişi KAZANAN çağrı yan etkileri (kupon kaydı,
    mail/push, CAPI, stok yeniden düşümü) çalıştırır — eşzamanlı ikinci çağrı aynı sonucu
    yan etkisiz döndürür."""
    # İDEMPOTENT (denetim 2026-09-29): sipariş ZATEN ödenmiş/iade edilmişse aynı ödeme sonucunun
    # tekrarı (callback replay) siparişe DOKUNMAZ — eskiden kargolanmış/teslim/iade edilmiş sipariş
    # yeniden "confirmed"a yazılabiliyor ve bildirimler tekrar gidiyordu. (Otomatik-iptal edilip
    # sonradan parası çekildiği anlaşılan siparişin kurtarılması etkilenmez: onun ödeme durumu 'paid' değil.)
    _ord = await db.orders.find_one(
        {"id": order_id},
        {"_id": 0, "id": 1, "total": 1, "order_number": 1, "payment_status": 1})
    paid = _is_paid(data, strict=strict_status)
    # Y1: iyzico başarı dese bile tutar/sipariş eşleşmesini doğrula.
    if paid:
        if not _ord or not _payment_matches_order(data, _ord):
            logger.warning(f"[ODEME] dogrulama basarisiz, PAID iptal edildi order_id={order_id}")
            paid = False
        # Defense-in-depth (payment redteam F4): aynı iyzico paymentId BAŞKA bir ödenmiş
        # siparişe bağlıysa reddet (cross-order paymentId replay — tutar eşleşmesine ek kalkan).
        _pid = data.get("paymentId")
        if paid and _pid:
            _dup = await db.orders.find_one(
                {"iyzico_payment_id": _pid, "payment_status": "paid", "id": {"$ne": order_id}},
                {"_id": 0, "id": 1})
            if _dup:
                logger.warning(f"[ODEME] paymentId {_pid} zaten sipariş {_dup['id']}'e bağlı — PAID iptal (replay) order_id={order_id}")
                paid = False
    if _ord and str(_ord.get("payment_status") or "").lower() in _PAID_LIKE_STATUSES:
        # Zaten ödenmiş: statüye dokunma. Farklı paymentId ile DOĞRULANMIŞ ikinci tahsilat ise kaydet.
        logger.info(f"[ODEME] sipariş zaten {_ord.get('payment_status')} — tekrar gelen ödeme sonucu yok sayıldı order_id={order_id}")
        await _maybe_record_duplicate(order_id, data, paid)
        return str(_ord.get("payment_status")).lower() == "paid"
    update = {
        "iyzico_retrieve_response": _payment_snapshot(data),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    if paid:
        update["payment_status"] = "paid"
        update["payment_id"] = data.get("paymentId")
        update["iyzico_payment_id"] = data.get("paymentId")
        update["paid_at"] = datetime.now(timezone.utc).isoformat()
        update["status"] = "confirmed"
        # ATOMİK GEÇİŞ (denetim 2026-10): eskiden koşulsuz update_one → eşzamanlı iki çağrı (3DS
        # callback + non-3DS / webhook / reconcile) ikisi de 'paid' yazıp bildirim/kupon/CAPI'yi
        # İKİ kez çalıştırabiliyordu. Artık yalnız henüz ödenmemiş sipariş çevrilir.
        _tr = await db.orders.update_one(
            {"id": order_id, "payment_status": {"$nin": list(_PAID_LIKE_STATUSES)}},
            {"$set": update})
        if _tr.modified_count != 1:
            # Yarışı başka çağrı kazandı (veya sipariş yok). Yan etki YOK; sonuç idempotent döner.
            _now = await db.orders.find_one({"id": order_id}, {"_id": 0, "payment_status": 1})
            _ps_now = str((_now or {}).get("payment_status") or "").lower()
            logger.info(f"[ODEME] paid geçişi başka çağrıda tamamlandı (yarış) order_id={order_id} durum={_ps_now or '-'}")
            if _ps_now in _PAID_LIKE_STATUSES:
                await _maybe_record_duplicate(order_id, data, True)
            return _ps_now == "paid"
        # Ödeme onaylandı → sipariş rapora BU AN girer; önbelleği hemen düşür.
        # try/except: rapor önbelleği hiçbir koşulda ödeme akışını etkilemez.
        try:
            from .reports import invalidate_report_cache as _inv_rc
            await _inv_rc()
        except Exception:
            pass
        # Denetim #2: Bu sipariş daha önce auto-cancel ile RESTOCK edilmiş ve şimdi geç reconcile/
        # callback ile GERÇEKTEN ödendiği ortaya çıktıysa (para çekilmiş), stoğu TEKRAR düş —
        # aksi halde create(-1)+restock(+1)+confirm(0) = net 0 kalıp ürün fiilen sevk edilirken
        # stok fazla görünüyordu (oversell). İdempotent: bir kez düşer.
        # B9: ATOMİK claim-then-act — bayrağı ÖNCE koşullu kap. callback_3ds ve reconcile
        # cron'u eşzamanlı çalışabildiğinden, find→act→set sırası ikisinin de bayrağı unset
        # görüp stoğu İKİ kez düşürmesine yol açıyordu. Yalnız claim'i kazanan (modified_count==1)
        # düşümü uygular.
        _claim = await db.orders.update_one(
            {"id": order_id, "_restocked_by_autocancel": True,
             "_redecremented_after_reconcile": {"$ne": True}},
            {"$set": {"_redecremented_after_reconcile": True},
             "$unset": {"_restocked_by_autocancel": ""}})
        if _claim.modified_count == 1:
            try:
                _o2 = await db.orders.find_one({"id": order_id}, {"_id": 0})
                from .orders import _stock_delta_for_order, _consume_restore_moves
                _moves = await _stock_delta_for_order(_o2, -1)
                # B6: auto-cancel'in yazdığı BAYAT restore hareketini guard DIŞINA taşı ('tüketildi').
                # Aksi halde sonraki GERÇEK iptal _restock_order_once guard'ına takılıp (mevcut restore
                # hareketi görülüp) stoğu ASLA iade etmiyordu → paid siparişin envanteri kalıcı kilitli.
                # Öncesinde delete_many ile SİLİNİYORDU (stok geçmişi kaybı); artık kayıt korunur.
                await _consume_restore_moves(order_id, "reconcile_redecrement")
                # DEFTER BOŞLUĞU (Faz 1): bu yeniden-düşüm stoğu gerçekten azaltıyor ama
                # hiçbir hareket yazmıyordu → "defter toplamı = canlı stok" denkliği bozuluyor
                # ve mutabakat raporu bunu sahte sapma sanıyordu. Yalnız KAYIT eklenir; stok
                # mantığı DEĞİŞMEZ. Tip bilerek _DEDUCT_MOVE_TYPES'a konmaz: otoriter iade
                # zaten asıl düşümü (order_created/order_imported) sayar; buraya da sayarsa
                # ileride yapılacak gerçek iptal iki katını geri yükler.
                try:
                    if _moves:
                        await db.stock_movements.insert_one({
                            "id": str(_uuid_pm.uuid4()), "type": "reconcile_redecrement",
                            "order_id": order_id,
                            "order_number": (_o2 or {}).get("order_number", ""),
                            "items": _moves, "source": "payment_reconcile",
                            "created_at": datetime.now(timezone.utc).isoformat(),
                        })
                except Exception as _mv_err:
                    logger.warning(f"[reconcile] hareket kaydı yazılamadı {order_id}: {_mv_err}")
                logger.info(f"[reconcile] restock sonrası tekrar-ödendi → stok yeniden düşüldü order_id={order_id} moves={len(_moves or [])}")
            except Exception as _rse:
                # Düşüm/temizlik başarısızsa claim'i GERİ AL — sonraki denemede tekrar işlensin.
                await db.orders.update_one(
                    {"id": order_id},
                    {"$set": {"_restocked_by_autocancel": True},
                     "$unset": {"_redecremented_after_reconcile": ""}})
                logger.warning(f"[reconcile] tekrar-stok-düşümü hatası order_id={order_id}: {_rse}")
        else:
            # Değişmez 6: PERSONEL (ya da başka bir yol) ödemesi bekleyen kart siparişini iptal edip
            # stoğu iade ettiyse ve doğrulanmış banka dönüşü SONRA geldiyse, ödenmiş sipariş iade
            # edilmiş stoğu tutmuyordu (oversell). Kazanan paid geçişinde tüketilmemiş restore
            # hareketleri varsa iade edilen miktar ATOMİK olarak yeniden düşülür (0'da durur + uyarı).
            await _redecrement_after_cancel_on_paid(order_id)
        # DENETİM (2026-09-29, K1/O8): sipariş kapatılırken hediye çeki / puan müşteriye İADE
        # edilmişse ve ödeme sonradan gerçekleştiyse bakiye müşteride kalmıştır. Ödeme gerçek
        # olduğundan sipariş 'paid' kalır; ekip görsün diye sipariş notu + bayrak yazılır.
        try:
            _o3 = await db.orders.find_one({"id": order_id}, {"_id": 0, "gift_card": 1,
                                                              "points_refunded": 1, "points_used": 1})
            _gc3 = (_o3 or {}).get("gift_card") or {}
            if (isinstance(_gc3, dict) and _gc3.get("refunded")) or (_o3 or {}).get("points_refunded"):
                _txt = ("⚠ Ödeme, sipariş otomatik kapatıldıktan SONRA onaylandı. Kapatılırken hediye çeki/"
                        "puan müşteriye iade edilmişti — bakiyeyi kontrol edip gerekirse düşün.")
                await db.orders.update_one({"id": order_id}, {
                    "$set": {"needs_manual_review": True, "manual_review_reason": "gift_points_refunded_before_paid"},
                    "$push": {"admin_notes": {"id": str(_uuid_pm.uuid4()), "text": _txt, "by": "sistem",
                                              "at": datetime.now(timezone.utc).isoformat()}}})
                logger.error(f"[ODEME] geç onay + iade edilmiş çek/puan → manuel inceleme order_id={order_id}")
        except Exception as _mr:
            logger.warning(f"[ODEME] manuel inceleme işareti yazılamadı {order_id}: {_mr}")
    else:
        # Y1: ZATEN ödenmiş bir siparişi 'failed'a düşürme (replay / sahte failure koruması).
        update["payment_status"] = "failed"
        # KÖK NEDEN #1: iyzico paymentId dönmüşse (kart ÇEKİLMİŞ olabilir; tutar/eşleşme
        # doğrulaması tuttuğu için 'failed' işaretlendi) → paymentId'yi ÜST DÜZEYDE sakla ve
        # needs_reconciliation bayrağı koy. Böylece 15 dk'lık reconcile cron'u (scheduler)
        # iyzico'dan paymentId ile doğrulayıp OTOMATİK kurtarabilir. Aksi halde paymentId
        # yalnız iyzico_retrieve_response içinde kalıyor, cron göremiyor ve sipariş
        # "Ödeme Kaydı Bulunmayan Siparişler"de kalıcı takılıyordu (müşterinin kartı çekilmiş).
        _pid_failed = data.get("paymentId")
        if _pid_failed:
            update["reconcile_payment_id"] = str(_pid_failed)
            update["needs_reconciliation"] = True
        await db.orders.update_one(
            {"id": order_id, "payment_status": {"$nin": list(_PAID_LIKE_STATUSES)}}, {"$set": update})
    logger.info(f"iyzico kart odeme {'PAID' if paid else 'FAILED'} order_id={order_id} pid={data.get('paymentId')}")
    if paid:
        await _notify_paid_order_confirmed(order_id)
        try:
            from .orders import dispatch_purchase_capi
            await dispatch_purchase_capi(order_id, source="iyzico_card")
        except Exception as _ce:
            logger.warning(f"CAPI purchase (kart) hata: {_ce}")
    return paid


@router.post("/3ds/initialize")
@(limiter.limit("20/minute") if limiter else (lambda f: f))
async def initialize_3ds_payment(payload: dict, request: Request):
    """Kendi kart formumuzdan 3D Secure başlatır; threeDSHtmlContent döner."""
    order_id = (payload.get("order_id") or "").strip()
    callback_url = _safe_callback_url((payload.get("callback_url") or "").strip(), request)  # F4
    return_url = (payload.get("return_url") or "").strip()
    card = payload.get("card") or {}
    installment = int(payload.get("installment") or 1)
    if not order_id:
        raise HTTPException(status_code=400, detail="order_id gerekli")
    order = await db.orders.find_one({"id": order_id}, {"_id": 0})
    if not order:
        raise HTTPException(status_code=404, detail="Sipariş bulunamadı")
    _assert_payment_capability(order, payload.get("pay_token") or request.headers.get("x-pay-token"))
    if str(order.get("payment_status") or "").lower() in _PAID_LIKE_STATUSES or \
       order.get("status") in ("confirmed", "shipped", "delivered", "cancelled"):
        raise HTTPException(status_code=400, detail="Bu sipariş için ödeme alınamaz")
    _pay_gate_expired(order)
    await _assert_card_payments_enabled()  # kill-switch (yalnız YENİ başlatma)

    await _card_velocity_guard(request, order_id)  # A2.6: kart-testi hız sınırı
    # Eşzamanlı başlatma kilidi (çift çekim önlemi). 3DS init KART ÇEKMEZ (çekim callback'teki
    # auth'ta) → başarısız/hatalı başlatmada kilit hemen bırakılır; başarılıda callback'e kadar tutulur.
    _lock = await _acquire_payment_init_lock(order_id)
    _keep_lock = False
    try:
        settings = await _get_iyzico_settings()
        body = _build_card_payment_payload(order, card, installment, callback_url, is_3ds=True)
        # Taksit seçildiyse paidPrice'i o taksitin gerçek toplamına (vade farkı dahil) eşitle
        if installment > 1:
            _tp = await _installment_total_price(settings, card.get("cardNumber"), float(body.get("paidPrice") or 0), installment)
            if _tp and _tp > 0:
                body["paidPrice"] = _fmt(_tp)
        body_str = json.dumps(body, separators=(",", ":"), ensure_ascii=False)
        headers = _v2_headers(settings["api_key"], settings["api_secret"], THREEDS_INIT_PATH, body_str)

        try:
            async with httpx.AsyncClient(timeout=30) as c:
                resp = await c.post(f"{settings['base_url']}{THREEDS_INIT_PATH}", content=body_str.encode("utf-8"), headers=headers)
            data = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {"status": "failure", "errorMessage": resp.text[:300]}
        except Exception as e:
            logger.error(f"iyzico 3ds init error: {e}")
            raise HTTPException(status_code=502, detail=_IYZ_CONN_ERR)  # ayrıntı yalnız logda (INFRA-03)

        if data.get("status") != "success":
            logger.warning(f"iyzico 3ds init failed order={order_id}: {data.get('errorCode')} {data.get('errorMessage')}")
            await _record_card_attempt(request, card, order_id, "init_failed")  # A2.6
            return {"success": False, "error": data.get("errorMessage") or "Ödeme başlatılamadı", "errorCode": data.get("errorCode")}
        # KÖK SEBEP (müşteri bildirimi): banka 3DS onayından sonra dönüş adımı tarayıcıda
        # kırılırsa (iOS Safari "adres geçersiz", banka uygulamasına yönlendirme, ağ kopması)
        # callback bize HİÇ ulaşmıyor. O ana kadar paymentId yalnız callback ile geldiğinden
        # sipariş "doğrulanamaz" kalıyor ve 15 dakikalık mutabakat işi onu atlıyordu
        # ("paymentId yok → otomatik doğrulanamaz"). Artık paymentId 3DS BAŞLATMA yanıtından
        # da saklanır → callback gelmese bile mutabakat iyzico'ya sorup gerçekten çekilmişse
        # siparişi kurtarabilir, çekilmemişse durum net görünür.
        _init_pid = str(data.get("paymentId") or "").strip()
        _set = {
            "iyzico_conversation_id": data.get("conversationId") or order_id,
            "iyzico_return_url": return_url,
            "payment_provider": "iyzico",
            "payment_flow": "3ds",
            "threeds_started_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        if _init_pid:
            # Ödeme HENÜZ tamamlanmadı; bu yalnız doğrulama anahtarıdır. 'iyzico_payment_id'
            # (ödendi anlamına gelen alan) YAZILMAZ — yalnız mutabakatın kullandığı alan.
            _set["reconcile_payment_id"] = _init_pid
            # NOT: 'needs_reconciliation' bayrağı BİLEREK yazılmaz — mutabakat sorgusu zaten
            # payment_status=pending siparişleri kapsıyor. Bayrak, ödeme hiç tamamlanmasa bile
            # siparişi 7 gün boyunca 15 dakikada bir iyzico'ya sorulur hâlde bırakırdı.
        await db.orders.update_one({"id": order_id}, {"$set": _set})
        # Müşteri banka OTP ekranında: kilit callback'e (veya süre dolumuna) kadar KORUNUR —
        # bu arada ikinci sekmeden ikinci 3DS başlatılıp iki kez onaylanamaz.
        _keep_lock = True
        return {"success": True, "threeDSHtmlContent": data.get("threeDSHtmlContent")}
    finally:
        if not _keep_lock:
            await _release_payment_init_lock(order_id, _lock)


@router.api_route("/3ds/callback", methods=["POST", "GET"])
async def callback_3ds(request: Request):
    """Banka 3DS sonrası iyzico buraya POST eder; auth ile finalize edip storefront'a döner."""
    try:
        form = dict(await request.form())
    except Exception:
        form = {}
    status = form.get("status") or request.query_params.get("status")
    md = str(form.get("mdStatus") or "")
    payment_id = form.get("paymentId") or request.query_params.get("paymentId")
    conv_data = form.get("conversationData") or ""
    conv_id = form.get("conversationId") or request.query_params.get("conversationId") or ""

    order = await db.orders.find_one({"id": conv_id}, {"_id": 0}) if conv_id else None
    return_base = _safe_return_base((order or {}).get("iyzico_return_url"), request)

    ok = False
    if status == "success" and md == "1" and payment_id:
        settings = await _get_iyzico_settings()
        body = {"locale": "tr", "conversationId": str(conv_id), "paymentId": str(payment_id)}
        if conv_data:
            body["conversationData"] = conv_data
        body_str = json.dumps(body, separators=(",", ":"), ensure_ascii=False)
        headers = _v2_headers(settings["api_key"], settings["api_secret"], THREEDS_AUTH_PATH, body_str)
        # KART BU ADIMDA ÇEKİLİR. Cevabı GÜVENİLİR şekilde okuyabildik mi ayrı tut:
        # transport/parse hatasında (timeout, non-JSON) çekim yapılmış OLABİLİR → siparişi
        # 'failed' yazma (para alınıp kayıt yok/failed olur); reconcile bayrağı bırak.
        _resp_certain = True
        try:
            async with httpx.AsyncClient(timeout=30) as c:
                resp = await c.post(f"{settings['base_url']}{THREEDS_AUTH_PATH}", content=body_str.encode("utf-8"), headers=headers)
            if resp.headers.get("content-type", "").startswith("application/json"):
                data = resp.json()
            else:
                data = {"status": "failure"}
                _resp_certain = False   # iyzico JSON dönmedi → belirsiz
        except Exception as e:
            logger.error(f"iyzico 3ds auth error: {e}")
            data = {"status": "failure", "errorMessage": str(e)}
            _resp_certain = False        # transport hatası → belirsiz
        # Fix 2: callback'te conversationId boş/uyuşmazsa siparişi AUTH yanıtındaki
        # conversationId ile de ara (aksi halde para çekilir ama finalize atlanırdı).
        if order is None:
            _cid2 = str((data or {}).get("conversationId") or conv_id or "").strip()
            if _cid2:
                order = await db.orders.find_one({"id": _cid2}, {"_id": 0})
                if order and not return_base:
                    return_base = _safe_return_base(order.get("iyzico_return_url"), request)
        if order:
            if _resp_certain:
                # Sonuç iyzico'nun KENDİ auth yanıtından (sunucu-sunucu) → doğrulanmış.
                ok = await _mark_order_from_payment(order.get("id"), data)
                # Kesin sonuç alındı: başlatma kilidini bırak (paid → yeni başlatma zaten
                # reddedilir; başarısız → müşteri hemen yeniden deneyebilsin).
                await _release_payment_init_lock(order.get("id"))
            else:
                # Fix 3: cevap belirsiz — 'failed' YAZMA; pending bırak + reconcile bayrağı.
                # Kilit BIRAKILMAZ (çekim olmuş olabilir; süre dolunca kendiliğinden düşer).
                await db.orders.update_one(
                    {"id": order.get("id"), "payment_status": {"$nin": list(_PAID_LIKE_STATUSES)}},
                    {"$set": {"needs_reconciliation": True,
                              "reconcile_payment_id": str(payment_id),
                              "updated_at": datetime.now(timezone.utc).isoformat()}})
    elif order:
        # GÜVENLİK (denetim 2026-10): bu uç İMZASIZ (tarayıcı üzerinden form POST). Eskiden
        # buraya gelen her "başarısız" callback siparişi payment_status='failed' yapıyordu →
        # sipariş id'sini bilen biri sahte failure POST'u ile siparişin ödeme durumunu bozabiliyordu.
        # Artık DOĞRULANMAMIŞ başarısızlık siparişe YAZILMAZ: sipariş awaiting_payment/pending
        # kalır; ödeme hiç tamamlanmazsa 3 saatlik auto_cancel_unpaid_card_orders süpürür
        # (değişmez 4). Gerçek başarısızlık iyzico'nun kendi yanıtıyla (auth/retrieve) yazılır.
        logger.info(f"[3DS CALLBACK] doğrulanmamış başarısızlık — siparişe yazılmadı order_id={order.get('id')} "
                    f"status={str(status)[:20]} mdStatus={md[:4]}")
        # Müşteri hemen yeniden deneyebilsin diye başlatma kilidi bırakılır (yalnız kilit; ödeme
        # durumuna dokunmaz — en kötü ihtimalle eşzamanlı başlatma kalkanı erken düşer, çift
        # tahsilat yine _record_duplicate_payment ile yakalanır).
        await _release_payment_init_lock(order.get("id"))

    onum = (order or {}).get("order_number") or ""
    sep = "&" if "?" in return_base else "?"
    redirect = f"{return_base}{sep}status={'success' if ok else 'fail'}&order={onum}"
    return RedirectResponse(url=redirect, status_code=303)


@router.post("/card/pay")
@(limiter.limit("10/minute") if limiter else (lambda f: f))
async def card_pay_non3ds(payload: dict, request: Request):
    """3DS'siz doğrudan tahsilat (use3DSecure kapalıysa). Yönlendirme yok.
    Rate-limited: kart-testi / BIN saldırısı yüzeyini daraltır.

    GÜVENLİK (denetim 2026-10): vitrin (Checkout.jsx) yalnız 3DS akışını kullanır; bu eski
    3DS'siz uç kart-testi / 3DS atlatma yüzeyidir → VARSAYILAN KAPALI (410). Yalnız İşletme
    Kuralı `payment.allow_non3ds_card_pay` açıkça True ise çalışır."""
    _ = request
    try:
        from business_rules import get_rule as _br_rule
        _allow_non3ds = (await _br_rule(db, "payment.allow_non3ds_card_pay", False)) is True
    except Exception:
        _allow_non3ds = False
    if not _allow_non3ds:
        raise HTTPException(status_code=410, detail=(
            "3D Secure'suz kart ödemesi kapatıldı. Lütfen 3D Secure ile ödeme yapın."))
    await _assert_card_payments_enabled()  # kill-switch
    order_id = (payload.get("order_id") or "").strip()
    card = payload.get("card") or {}
    installment = int(payload.get("installment") or 1)
    if not order_id:
        raise HTTPException(status_code=400, detail="order_id gerekli")
    order = await db.orders.find_one({"id": order_id}, {"_id": 0})
    if not order:
        raise HTTPException(status_code=404, detail="Sipariş bulunamadı")
    _assert_payment_capability(order, payload.get("pay_token") or request.headers.get("x-pay-token"))
    if str(order.get("payment_status") or "").lower() in _PAID_LIKE_STATUSES or \
       order.get("status") in ("confirmed", "shipped", "delivered", "cancelled"):
        raise HTTPException(status_code=400, detail="Bu sipariş için ödeme alınamaz")
    _pay_gate_expired(order)

    await _card_velocity_guard(request, order_id)  # A2.6: kart-testi hız sınırı
    # Eşzamanlı başlatma kilidi (çift çekim önlemi). Kesin sonuçta (paid/başarısız) bırakılır;
    # belirsiz sonuçta (timeout — çekim olmuş olabilir) süre dolana dek tutulur.
    _lock = await _acquire_payment_init_lock(order_id)
    _keep_lock = False
    try:
        settings = await _get_iyzico_settings()
        body = _build_card_payment_payload(order, card, installment, "", is_3ds=False)
        if installment > 1:
            _tp = await _installment_total_price(settings, card.get("cardNumber"), float(body.get("paidPrice") or 0), installment)
            if _tp and _tp > 0:
                body["paidPrice"] = _fmt(_tp)
        body_str = json.dumps(body, separators=(",", ":"), ensure_ascii=False)
        headers = _v2_headers(settings["api_key"], settings["api_secret"], NON3DS_PATH, body_str)

        try:
            async with httpx.AsyncClient(timeout=30) as c:
                resp = await c.post(f"{settings['base_url']}{NON3DS_PATH}", content=body_str.encode("utf-8"), headers=headers)
            data = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {"status": "failure", "errorMessage": resp.text[:300]}
        except Exception as e:
            logger.error(f"iyzico non-3ds error: {e}")
            # Fix 3: çekim yapılmış OLABİLİR ama cevabı okuyamadık → siparişi 'failed' bırakma,
            # reconcile bayrağı koy (cron/webhook iyzico'dan doğrulayıp finalize etsin).
            _keep_lock = True
            await db.orders.update_one(
                {"id": order_id, "payment_status": {"$nin": list(_PAID_LIKE_STATUSES)}},
                {"$set": {"needs_reconciliation": True,
                          "updated_at": datetime.now(timezone.utc).isoformat()}})
            raise HTTPException(status_code=502, detail=_IYZ_CONN_ERR)  # ayrıntı yalnız logda (INFRA-03)

        paid = await _mark_order_from_payment(order_id, data)
        await _record_card_attempt(request, card, order_id, "success" if paid else "auth_failed")  # A2.6
        if paid:
            return {"success": True, "order_number": order.get("order_number")}
        return {"success": False, "error": data.get("errorMessage") or "Ödeme başarısız", "errorCode": data.get("errorCode")}
    finally:
        if not _keep_lock:
            await _release_payment_init_lock(order_id, _lock)



@router.post("/installments")
@(limiter.limit("20/minute") if limiter else (lambda f: f))
async def get_installments(payload: dict, request: Request):
    _ = request  # BIN-oracle sömürüsüne karşı rate-limit
    """Kart BIN'ine göre taksit seçeneklerini iyzico'dan sorgular.
    Kart verisi TAŞINMAZ; yalnızca ilk 6-8 hane (BIN) gönderilir."""
    bin_number = "".join(ch for ch in str(payload.get("bin_number") or "") if ch.isdigit())[:8]
    try:
        price = round(float(payload.get("price") or 0), 2)
    except Exception:
        price = 0.0
    _fallback = {"success": False, "options": [{"number": 1, "totalPrice": price, "installmentPrice": price}]}
    if len(bin_number) < 6 or price <= 0:
        return _fallback

    settings = await _get_iyzico_settings()
    body = {"locale": "tr", "conversationId": "installment", "binNumber": bin_number, "price": _fmt(price)}
    body_str = json.dumps(body, separators=(",", ":"), ensure_ascii=False)
    headers = _v2_headers(settings["api_key"], settings["api_secret"], INSTALLMENT_PATH, body_str)
    try:
        async with httpx.AsyncClient(timeout=20) as c:
            resp = await c.post(f"{settings['base_url']}{INSTALLMENT_PATH}", content=body_str.encode("utf-8"), headers=headers)
        data = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {"status": "failure"}
    except Exception as e:
        logger.error(f"iyzico installment error: {e}")
        return _fallback

    details = data.get("installmentDetails") or []
    if data.get("status") != "success" or not details:
        return _fallback

    det = details[0]
    opts = []
    for ip in det.get("installmentPrices", []):
        try:
            opts.append({
                "number": int(ip.get("installmentNumber") or 1),
                "totalPrice": round(float(ip.get("totalPrice") or price), 2),
                "installmentPrice": round(float(ip.get("installmentPrice") or 0), 2),
            })
        except Exception:
            pass
    opts.sort(key=lambda x: x["number"])
    if not opts:
        return _fallback
    return {
        "success": True,
        "options": opts,
        "cardFamily": det.get("cardFamilyName") or "",
        "bankName": det.get("bankName") or "",
        "force3ds": bool(det.get("force3ds")),
    }


# =============================================================================
# İYZİCO WEBHOOK (İşyeri Bildirimleri) — sunucudan sunucuya ödeme onayı
# Kurulum (iyzico paneli): Ayarlar → Firma Ayarları → İşyeri Bildirimleri →
#   URL: https://api.meluni.com.tr/api/payment/webhook
# Müşteri ödeme sonrası sekmeyi kapatsa bile onay buraya düşer: sipariş
# iyzico'dan retrieve ile DOĞRULANARAK finalize edilir ve CAPI purchase
# (TikTok CompletePayment, Meta Purchase …) sunucudan gönderilir.
# 200 dönülmezse iyzico 15 dk arayla 3 kez daha dener (doğal retry ağı).
# =============================================================================
def _webhook_signature_ok(secret: str, headers, payload: dict) -> bool:
    """X-IYZ-SIGNATURE-V3 doğrulaması. İmza = HMAC-SHA256(secretKey,
    secretKey+iyziEventType+iyziPaymentId+token+paymentConversationId+status).
    Alan adı/birleşim varyasyonlarına karşı iki aday hesaplanır; eksik alanlar
    boş string sayılır. Eşleşmezse istek REDDEDİLİR (sahte çağrı koruması)."""
    sig = ""
    for k, v in headers.items():
        if k.lower() == "x-iyz-signature-v3":
            sig = (v or "").strip()
            break
    if not sig or not secret:
        return False
    evt = str(payload.get("iyziEventType") or "")
    pid = str(payload.get("iyziPaymentId") or payload.get("paymentId") or "")
    tok = str(payload.get("token") or "")
    conv = str(payload.get("paymentConversationId") or payload.get("conversationId") or "")
    status = str(payload.get("status") or payload.get("paymentStatus") or "")
    import base64 as _b64
    candidates_hex = set()
    candidates_b64 = set()
    for msg in (secret + evt + pid + tok + conv + status,   # dokümandaki birleşim
                evt + pid + tok + conv + status):           # secret yalnız key ise
        _d = hmac.new(secret.encode("utf-8"), msg.encode("utf-8"), hashlib.sha256)
        candidates_hex.add(_d.hexdigest().lower())
        # KÖK NEDEN #4: iyzico V3 imzası çoğunlukla BASE64 (ham HMAC byte'ları), hex DEĞİL.
        # Yalnız hex karşılaştırılıyordu → gerçek webhook'lar hep 401 ile reddediliyor, birincil
        # güvenlik ağı ölüydü. base64 adayını da ekle (secret hâlâ zorunlu → güvenlik korunur).
        candidates_b64.add(_b64.b64encode(_d.digest()).decode("ascii"))
    # hex: büyük/küçük harf duyarsız; base64: DUYARLI (lower() base64'ü bozar).
    return (sig.lower() in candidates_hex) or (sig in candidates_b64)


@router.post("/webhook")
async def iyzico_webhook(request: Request):
    """iyzico İşyeri Bildirimleri webhook'u. İmza doğrulanır; SUCCESS ödemede
    sipariş finalize edilir + CAPI purchase tetiklenir. Idempotent: aynı
    bildirim tekrar gelirse (retry) ikinci event ÇIKMAZ (capi_purchase_sent)."""
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}

    settings = await _get_iyzico_settings()
    if not _webhook_signature_ok(settings.get("api_secret") or "", request.headers, payload):
        logger.warning(f"iyzico webhook imza doğrulanamadı; alanlar={sorted(payload.keys())}")
        raise HTTPException(status_code=401, detail="signature")

    # Denetim izi (hassas alan yok — sadece kimlik/durum alanları)
    try:
        await db.iyzico_webhook_logs.insert_one({
            "received_at": datetime.now(timezone.utc).isoformat(),
            "payload": {k: payload.get(k) for k in (
                "iyziEventType", "iyziPaymentId", "paymentId", "token",
                "paymentConversationId", "conversationId", "status", "paymentStatus")},
        })
    except Exception:
        pass

    status = str(payload.get("status") or payload.get("paymentStatus") or "").upper()
    if status != "SUCCESS":
        return {"ok": True, "ignored": status or "no-status"}

    token = str(payload.get("token") or "").strip()
    conv = str(payload.get("paymentConversationId") or payload.get("conversationId") or "").strip()
    order = None
    if token:
        order = await db.orders.find_one({"iyzico_token": token},
                                         {"_id": 0, "id": 1, "payment_status": 1})
    if not order and conv:
        order = await db.orders.find_one({"id": conv},
                                         {"_id": 0, "id": 1, "payment_status": 1, "iyzico_token": 1})
        token = token or str((order or {}).get("iyzico_token") or "")
    if not order:
        return {"ok": True, "ignored": "order-not-found"}

    # Henüz paid değilse webhook verisine körü körüne güvenme — iyzico'dan
    # retrieve ile doğrulayıp finalize et (bildirim + CAPI purchase içeride tetiklenir).
    # DENETİM 2026-10: finalize İSTİSNA/BELİRSİZLİKLE biterse 200 DÖNMEYİZ — 500 dönülür ki
    # iyzico (imzası doğrulanmış) bildirimi yeniden denesin; aksi halde çekilmiş ödeme yalnız
    # 15 dk'lık mutabakat cron'una kalıyordu. Kesin sonuç (paid ya da doğrulanmış başarısız) → 200.
    if (order.get("payment_status") or "") != "paid":
        if token:
            try:
                await _retrieve_and_finalize(token)          # hosted Checkout Form akışı
            except Exception as e:
                logger.warning(f"iyzico webhook finalize hata order={order.get('id')}: {e}")
                raise HTTPException(status_code=500, detail="finalize-retry")
        else:
            # Fix 1: 3DS/kart akışında hosted token YOK → paymentId ile finalize et.
            # Bu, callback'e ulaşamayan (sekme kapandı vb.) çekilmiş siparişleri kurtarır.
            _pid = str(payload.get("iyziPaymentId") or payload.get("paymentId")
                       or payload.get("iyziReferenceCode") or "").strip()
            if _pid:
                try:
                    await _finalize_by_payment_id(order["id"], _pid, raise_on_uncertain=True)
                except Exception as e:
                    logger.warning(f"iyzico webhook paymentId finalize hata order={order.get('id')}: {e}")
                    raise HTTPException(status_code=500, detail="finalize-retry")

    # Paid ise purchase'ı garanti et (daha önce gittiyse no-op)
    fresh = await db.orders.find_one({"id": order["id"]}, {"_id": 0, "payment_status": 1})
    if (fresh or {}).get("payment_status") == "paid":
        try:
            from .orders import dispatch_purchase_capi
            await dispatch_purchase_capi(order["id"], source="iyzico_webhook")
        except Exception as e:
            logger.warning(f"CAPI purchase (webhook) hata: {e}")
    return {"ok": True}
