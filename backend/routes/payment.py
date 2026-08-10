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
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, HTTPException, Request, Form
from fastapi.responses import RedirectResponse

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


def _is_paid(data: dict) -> bool:
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
    return ps in (None, "", "SUCCESS")


def _payment_matches_order(data: dict, order: dict) -> bool:
    """Y1: iyzico yanıtının GERÇEKTEN bu siparişe ve tutara ait olduğunu doğrular.
    - conversationId (bizim gönderdiğimiz = sipariş id'si) yanıtta eşleşmeli
    - ödenen tutar sipariş toplamıyla uyuşmalı (taksitte vade farkı ile >= olabilir)
    Böylece başka/ucuz bir siparişin paymentId'siyle pahalı siparişi 'ödendi' işaretlemek
    (cross-order paymentId reuse) engellenir."""
    try:
        oid = str(order.get("id") or "")
        conv = str(data.get("conversationId") or "")
        if conv and oid and conv != oid:
            logger.warning(f"[ODEME DOGRULAMA] conversationId uyusmuyor: yanit={conv} siparis={oid}")
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
    tn = "".join(ch for ch in str(binfo.get("tax_number") or "") if ch.isdigit())
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
    _prods = []
    items_sum = 0.0
    for it in (order.get("items") or []):
        qty = int(it.get("quantity") or 1)
        line = round(float(it.get("price") or 0) * qty, 2)
        items_sum += line
        _prods.append([it, line])

    shipping_cost = float(order.get("shipping_cost") or 0)
    gift_price = float(order.get("gift_wrap_price") or 0)
    cod_fee = float(order.get("cod_fee") or 0)  # create_order İşletme Kuralı'ndan yazar (hardcode değil)

    # Sipariş-seviyesi indirim = tam sepet − ödenen (order.total). Kargoya/ekstralara DEĞİL,
    # yalnız ürün satırlarına dağıtılır.
    _full = round(items_sum + shipping_cost + gift_price + cod_fee, 2)
    _raw_total = round(float(order.get("total") or 0), 2)
    _order_disc = round(max(0.0, _full - _raw_total), 2) if _raw_total > 0.009 else 0.0
    if _order_disc > 0.009 and items_sum > 0.009:
        _ratio = _order_disc / items_sum
        for p in _prods:
            p[1] = round(p[1] * (1 - _ratio), 2)
        # YUVARLAMA MUTABAKATI (iyzico şartı: sepet toplamı = price, kuruşu kuruşuna): ürün
        # satırları toplamı hedefe (ürün brüt − indirim) BİREBİR eşitlenir; fark son ürüne yazılır.
        _prod_target = round(items_sum - _order_disc, 2)
        _cur = round(sum(p[1] for p in _prods), 2)
        _d = round(_prod_target - _cur, 2)
        if _prods and abs(_d) >= 0.01:
            _prods[-1][1] = round(_prods[-1][1] + _d, 2)

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
    paid_price = round(float(order.get("total") or price), 2)
    # paidPrice price'ı geçemez (indirim sadece düşürür)
    if paid_price > price:
        paid_price = price
    if paid_price <= 0:
        paid_price = price

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
async def initialize_payment(request: Request, order_id: str, callback_url: str, return_url: str = ""):
    """Sipariş için iyzico Ödeme Formu başlatır (rate-limited)."""
    _ = request
    order = await db.orders.find_one({"id": order_id}, {"_id": 0})
    if not order:
        raise HTTPException(status_code=404, detail="Sipariş bulunamadı")
    # GÜVENLİK: Zaten ödenmiş/tamamlanmış/iptal siparişte ödeme başlatma — kart-testi
    # ve tekrar-tahsilat yüzeyini kapatır.
    if order.get("payment_status") in ("paid", "refunded") or \
       order.get("status") in ("confirmed", "shipped", "delivered", "cancelled"):
        raise HTTPException(status_code=400, detail="Bu sipariş için ödeme alınamaz")

    settings = await _get_iyzico_settings()
    payload = _build_initialize_payload(order, callback_url)
    body_str = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    headers = _v2_headers(settings["api_key"], settings["api_secret"], INIT_PATH, body_str)

    try:
        async with httpx.AsyncClient(timeout=30) as c:
            resp = await c.post(f"{settings['base_url']}{INIT_PATH}", content=body_str.encode("utf-8"), headers=headers)
        data = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {"status": "failure", "errorMessage": resp.text[:300]}
    except Exception as e:
        logger.error(f"iyzico initialize error: {e}")
        raise HTTPException(status_code=502, detail=f"iyzico bağlantı hatası: {e}")

    if data.get("status") != "success":
        logger.warning(f"iyzico initialize failed order={order_id}: {data.get('errorCode')} {data.get('errorMessage')}")
        return {
            "success": False,
            "error": data.get("errorMessage") or "Ödeme başlatılamadı",
            "errorCode": data.get("errorCode"),
        }

    await db.orders.update_one(
        {"id": order_id},
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
            await record_order_redemptions(order)
        except Exception as _re:
            logger.warning(f"Odeme sonrasi kupon kaydi hatasi order_id={order_id}: {_re}")
        if order.get("order_confirmed_notified"):
            return
        # Önce işaretle (tek mail garantisi), sonra gönder.
        await db.orders.update_one(
            {"id": order_id}, {"$set": {"order_confirmed_notified": True}}
        )
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
            _tl = float(order.get("total") or 0)
            _who = (f"{ship.get('first_name','')} {ship.get('last_name','')}".strip()
                    or ship.get("full_name") or "Müşteri")
            await send_push_to_admins(
                f"🛍️ Yeni Sipariş (ödendi) · {_tl:,.2f} TL".replace(",", "."),
                f"{order.get('order_number','')} · {_who} · Kart",
                {"type": "new_order", "order_id": str(order.get("id") or ""),
                 "order_number": str(order.get("order_number") or "")},
            )
        except Exception as _pe:
            logger.warning(f"admin push (kart ödeme sonrası) atlandı: {_pe}")
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

    paid = _is_paid(data)
    # Y1: tutar/sipariş eşleşmesini doğrula (token ile bulunduğu için conversationId zaten eşleşir,
    # ama tutar doğrulaması cross-order paymentId'ye karşı korur).
    if paid and not _payment_matches_order(data, order):
        logger.warning(f"[ODEME] retrieve dogrulama basarisiz order={order.get('order_number')}")
        paid = False
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
        await db.orders.update_one({"id": order.get("id")}, {"$set": update})
    else:
        update["payment_status"] = "failed"
        await db.orders.update_one(
            {"id": order.get("id"), "payment_status": {"$ne": "paid"}}, {"$set": update})
    logger.info(f"iyzico payment {'PAID' if paid else 'FAILED'} order={order.get('order_number')} pid={data.get('paymentId')}")
    if paid:
        await _notify_paid_order_confirmed(order.get("id"))
        # CAPI purchase (TikTok CompletePayment / Meta Purchase …) — sunucudan,
        # idempotent (tek event), event_id = sipariş no (pixel'le dedup).
        try:
            from .orders import dispatch_purchase_capi
            await dispatch_purchase_capi(order.get("id"), source="iyzico_callback")
        except Exception as _ce:
            logger.warning(f"CAPI purchase (callback) hata: {_ce}")
    return {"ok": paid, "order": order, "return_url": order.get("iyzico_return_url") or ""}


PAYMENT_DETAIL_PATH = "/payment/detail"


async def _finalize_by_payment_id(order_id: str, payment_id: str) -> bool:
    """3DS/kart akışında (hosted token YOK) paymentId ile iyzico'dan ödemeyi retrieve edip
    finalize eder — webhook/reconcile güvenlik ağı; 'çekildi ama kayıt yok/failed' vakalarını
    kapatır. Belirsiz cevapta (non-JSON/exception) siparişe DOKUNMAZ (failed yazmaz)."""
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
            return False   # belirsiz → failed yazma
        data = resp.json()
    except Exception as e:
        logger.warning(f"iyzico payment-detail retrieve hata order={order_id} pid={payment_id}: {e}")
        return False
    return await _mark_order_from_payment(order_id, data)


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
    base = _build_initialize_payload(order, callback_url)
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


async def _mark_order_from_payment(order_id: str, data: dict) -> bool:
    """iyzico ödeme/3ds-auth yanıtına göre siparişi günceller. Döner: paid(bool)."""
    paid = _is_paid(data)
    # Y1: iyzico başarı dese bile tutar/sipariş eşleşmesini doğrula.
    if paid:
        _ord = await db.orders.find_one({"id": order_id}, {"_id": 0, "id": 1, "total": 1, "order_number": 1})
        if not _ord or not _payment_matches_order(data, _ord):
            logger.warning(f"[ODEME] dogrulama basarisiz, PAID iptal edildi order_id={order_id}")
            paid = False
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
        await db.orders.update_one({"id": order_id}, {"$set": update})
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
                from .orders import _stock_delta_for_order, _RESTORE_MOVE_TYPES
                _moves = await _stock_delta_for_order(_o2, -1)
                # B6: auto-cancel'in yazdığı BAYAT restore hareketini SİL. Aksi halde sonraki
                # GERÇEK iptal _restock_order_once guard'ına takılıp (mevcut restore hareketi
                # görülüp) stoğu ASLA iade etmiyordu → paid siparişin envanteri kalıcı kilitli.
                await db.stock_movements.delete_many(
                    {"order_id": order_id, "type": {"$in": _RESTORE_MOVE_TYPES}})
                logger.info(f"[reconcile] restock sonrası tekrar-ödendi → stok yeniden düşüldü order_id={order_id} moves={len(_moves or [])}")
            except Exception as _rse:
                # Düşüm/temizlik başarısızsa claim'i GERİ AL — sonraki denemede tekrar işlensin.
                await db.orders.update_one(
                    {"id": order_id},
                    {"$set": {"_restocked_by_autocancel": True},
                     "$unset": {"_redecremented_after_reconcile": ""}})
                logger.warning(f"[reconcile] tekrar-stok-düşümü hatası order_id={order_id}: {_rse}")
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
            {"id": order_id, "payment_status": {"$ne": "paid"}}, {"$set": update})
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
    callback_url = (payload.get("callback_url") or "").strip()
    return_url = (payload.get("return_url") or "").strip()
    card = payload.get("card") or {}
    installment = int(payload.get("installment") or 1)
    if not order_id:
        raise HTTPException(status_code=400, detail="order_id gerekli")
    order = await db.orders.find_one({"id": order_id}, {"_id": 0})
    if not order:
        raise HTTPException(status_code=404, detail="Sipariş bulunamadı")
    if order.get("payment_status") in ("paid", "refunded") or \
       order.get("status") in ("confirmed", "shipped", "delivered", "cancelled"):
        raise HTTPException(status_code=400, detail="Bu sipariş için ödeme alınamaz")

    await _card_velocity_guard(request, order_id)  # A2.6: kart-testi hız sınırı
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
        raise HTTPException(status_code=502, detail=f"iyzico bağlantı hatası: {e}")

    if data.get("status") != "success":
        logger.warning(f"iyzico 3ds init failed order={order_id}: {data.get('errorCode')} {data.get('errorMessage')}")
        await _record_card_attempt(request, card, order_id, "init_failed")  # A2.6
        return {"success": False, "error": data.get("errorMessage") or "Ödeme başlatılamadı", "errorCode": data.get("errorCode")}

    await db.orders.update_one(
        {"id": order_id},
        {"$set": {
            "iyzico_conversation_id": data.get("conversationId") or order_id,
            "iyzico_return_url": return_url,
            "payment_provider": "iyzico",
            "payment_flow": "3ds",
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }},
    )
    return {"success": True, "threeDSHtmlContent": data.get("threeDSHtmlContent")}


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
                ok = await _mark_order_from_payment(order.get("id"), data)
            else:
                # Fix 3: cevap belirsiz — 'failed' YAZMA; pending bırak + reconcile bayrağı.
                await db.orders.update_one(
                    {"id": order.get("id"), "payment_status": {"$ne": "paid"}},
                    {"$set": {"needs_reconciliation": True,
                              "reconcile_payment_id": str(payment_id),
                              "updated_at": datetime.now(timezone.utc).isoformat()}})
    elif order:
        # Y1: ödenmiş siparişi 'failed'a düşürme — sahte failure callback'i koruması.
        await db.orders.update_one(
            {"id": order.get("id"), "payment_status": {"$ne": "paid"}},
            {"$set": {"payment_status": "failed", "updated_at": datetime.now(timezone.utc).isoformat()}})

    onum = (order or {}).get("order_number") or ""
    sep = "&" if "?" in return_base else "?"
    redirect = f"{return_base}{sep}status={'success' if ok else 'fail'}&order={onum}"
    return RedirectResponse(url=redirect, status_code=303)


@router.post("/card/pay")
@(limiter.limit("10/minute") if limiter else (lambda f: f))
async def card_pay_non3ds(payload: dict, request: Request):
    """3DS'siz doğrudan tahsilat (use3DSecure kapalıysa). Yönlendirme yok.
    Rate-limited: kart-testi / BIN saldırısı yüzeyini daraltır."""
    _ = request
    order_id = (payload.get("order_id") or "").strip()
    card = payload.get("card") or {}
    installment = int(payload.get("installment") or 1)
    if not order_id:
        raise HTTPException(status_code=400, detail="order_id gerekli")
    order = await db.orders.find_one({"id": order_id}, {"_id": 0})
    if not order:
        raise HTTPException(status_code=404, detail="Sipariş bulunamadı")
    if order.get("payment_status") in ("paid", "refunded") or \
       order.get("status") in ("confirmed", "shipped", "delivered", "cancelled"):
        raise HTTPException(status_code=400, detail="Bu sipariş için ödeme alınamaz")

    await _card_velocity_guard(request, order_id)  # A2.6: kart-testi hız sınırı
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
        await db.orders.update_one(
            {"id": order_id, "payment_status": {"$ne": "paid"}},
            {"$set": {"needs_reconciliation": True,
                      "updated_at": datetime.now(timezone.utc).isoformat()}})
        raise HTTPException(status_code=502, detail=f"iyzico bağlantı hatası: {e}")

    paid = await _mark_order_from_payment(order_id, data)
    await _record_card_attempt(request, card, order_id, "success" if paid else "auth_failed")  # A2.6
    if paid:
        return {"success": True, "order_number": order.get("order_number")}
    return {"success": False, "error": data.get("errorMessage") or "Ödeme başarısız", "errorCode": data.get("errorCode")}



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
    if (order.get("payment_status") or "") != "paid":
        if token:
            try:
                await _retrieve_and_finalize(token)          # hosted Checkout Form akışı
            except Exception as e:
                logger.warning(f"iyzico webhook finalize hata order={order.get('id')}: {e}")
        else:
            # Fix 1: 3DS/kart akışında hosted token YOK → paymentId ile finalize et.
            # Bu, callback'e ulaşamayan (sekme kapandı vb.) çekilmiş siparişleri kurtarır.
            _pid = str(payload.get("iyziPaymentId") or payload.get("paymentId")
                       or payload.get("iyziReferenceCode") or "").strip()
            if _pid:
                try:
                    await _finalize_by_payment_id(order["id"], _pid)
                except Exception as e:
                    logger.warning(f"iyzico webhook paymentId finalize hata order={order.get('id')}: {e}")

    # Paid ise purchase'ı garanti et (daha önce gittiyse no-op)
    fresh = await db.orders.find_one({"id": order["id"]}, {"_id": 0, "payment_status": 1})
    if (fresh or {}).get("payment_status") == "paid":
        try:
            from .orders import dispatch_purchase_capi
            await dispatch_purchase_capi(order["id"], source="iyzico_webhook")
        except Exception as e:
            logger.warning(f"CAPI purchase (webhook) hata: {e}")
    return {"ok": True}
