"""
Order routes - CRUD, checkout, tracking
"""
from fastapi import APIRouter, HTTPException, Query, Depends, Response, Request, UploadFile, File, Form, Body
from typing import List, Optional
from datetime import datetime, timezone, timedelta
import time
import uuid
import re
import os   # MNG kargo webhook'u MNG_WEBHOOK_SECRET okurken kullanıyor (ithal edilmemişti)

from .deps import db, logger, get_current_user, require_admin, require_permission, generate_id, _search_tr_regex, tr_day_start_utc, tr_day_end_utc, limiter, safe_str, global_rate_key
from .deps import get_effective_permissions
from .deps import security as _deps_security
from activity_audit import record_admin_audit
# KVKK: TCKN/VKN at-rest şifreli (security/pii.py). Düz değer DB'ye yazılmaz; gerçek değer yalnız
# fatura/iyzico/admin-detay okuyucularında reveal_* ile çözülür, listeler maskeli döner.
from security.pii import (
    seal_doc_tax_ids as _pii_seal, reveal_doc_tax_ids as _pii_reveal_doc, reveal_tax_id as _pii_reveal,
    mask_doc_tax_ids as _pii_mask, mask_docs_tax_ids as _pii_mask_many, carry_over_masked as _pii_carry,
    drop_client_sealed_fields as _pii_drop_client, tax_id_search_clauses as _pii_search, scrub_tax_ids as _pii_scrub,
)
# KVKK (sahip talebi): müşteri telefonu / açık adresi personel LİSTELERİNDE maskeli; tam değer yalnız
# tek siparişin "Göster" ucunda (orders.view) + açma sayacı (PRIV07) + denetim kaydı.
from security.contact_pii import (
    mask_order_contact as _contact_mask, mask_orders_contact as _contact_mask_many,
    carry_over_masked_order_contact as _contact_carry, scrub_contact as _contact_scrub,
)

# Kart iadesi kayıtları (order.refunds[].status): 'done' = iyzico onayladı, 'pending' = istek
# sürüyor (rezerve), 'unknown' = sonucu belirsiz (para gitmiş OLABİLİR). Üçü de "iade edilmiş /
# edilmekte" sayılır; 'failed' kayıtlar sayılmaz. İade tavanı hesapları bunu kullanır.
_REFUND_COUNTED_STATUSES = ("done", "pending", "unknown")
from .attribution import resolve_attribution_for_order
from pymongo import ReturnDocument

router = APIRouter(prefix="/orders", tags=["Orders"])


# ═══════════════════════════════════════════════════════════════════════════════════════════
# ALAN ŞİFRELEME (BL-040) — iade IBAN'ı + havale dekontu at-rest şifreli.
#   • refund_bank_info (orders.refund_bank_info, orders.return_request.refund_bank_info,
#     customer_returns.refund_bank_info): IBAN + hesap sahibi adı Fernet ile şifrelenir
#     ({enc:1, iban_enc, name_enc, bank, iban_last4}). Tam değer YALNIZ iade ödemesini yapan
#     yetkili personelin ekranına (İptaller / İadeler listeleri) çözülerek verilir; başka her yerde
#     maskelenir (TR** … 12 34). Eski düz kayıtlar ({iban, name, bank}) okunmaya devam eder.
#   • payment_receipt (dekont): bayt verisi `data_enc` (Fernet, ham bayt) olarak yazılır; eski
#     `data_b64` kayıtları okunmaya devam eder. Okuma TEK yerden: get_receipt_bytes().
# ═══════════════════════════════════════════════════════════════════════════════════════════
# Tam IBAN'ı görebilen yetkiler (para iadesini bankadan yapan personel). '*' (süper admin) geçer.
_IBAN_FULL_PERMS = ("returns.refund_pay", "returns.iyzico_refund", "orders.cancelled_tab")
_RECEIPT_ENC_FORMAT = "fernet-bytes-v1"
# GÜVENLİK (istemci-XSS, 2026-10): dekont için YALNIZ bu raster görsel/PDF türleri kabul edilir ve
# inline sunulur. Eskiden `image/*` olan her tür geçiyordu → müşteri `image/svg+xml` (ya da
# Chrome'un XML belgesi saydığı `image/x+xml`) türünde script'li dosya yükleyebiliyordu; panel
# dekontu BLOB olarak vitrin origin'inde açtığından script admin token'ını okuyabiliyordu.
_RECEIPT_SAFE_TYPES = frozenset({
    "application/pdf", "image/jpeg", "image/jpg", "image/pjpeg", "image/png", "image/x-png",
    "image/gif", "image/webp", "image/heic", "image/heif", "image/bmp", "image/tiff", "image/avif",
})


def _receipt_base_type(ct) -> str:
    """'image/png; charset=x' → 'image/png' (küçük harf, parametresiz)."""
    return str(ct or "").split(";", 1)[0].strip().lower()


def _receipt_looks_like_markup(data: bytes) -> bool:
    """Gerçek görsel/PDF baytları '<' ile başlamaz; SVG/HTML/XML başlar (BOM/boşluk sonrası)."""
    head = (data or b"")[:512].lstrip(b"\xef\xbb\xbf \t\r\n\x0c")
    return head[:1] == b"<"


def _clean_iban(v) -> str:
    return "".join(str(v or "").split()).upper()


def _mask_person_name(n) -> str:
    parts = [p for p in str(n or "").split() if p]
    return " ".join(p[:1] + "***" for p in parts)


def seal_refund_bank_info(info):
    """{iban, name, bank} → şifreli yapı. Zaten şifreliyse dokunmaz. Şifreleme hatası YUTULMAZ
    (IBAN asla sessizce düz metin yazılmaz)."""
    if not isinstance(info, dict) or not info:
        return info
    if info.get("enc"):
        return info
    from security.crypto import encrypt as _enc
    iban = _clean_iban(info.get("iban"))
    name = str(info.get("name") or "").strip()
    return {
        "enc": 1,
        "iban_enc": _enc(iban) if iban else "",
        "name_enc": _enc(name) if name else "",
        "bank": str(info.get("bank") or "").strip()[:80],
        "iban_last4": iban[-4:] if iban else "",
    }


def open_refund_bank_info(info):
    """Şifreli/eski kaydı tam değerle döndürür (YALNIZ yetkili admin yanıtında kullan)."""
    if not isinstance(info, dict) or not info:
        return None
    if not info.get("enc"):
        iban = _clean_iban(info.get("iban"))
        return {"iban": iban, "name": info.get("name") or "", "bank": info.get("bank") or "",
                "iban_last4": iban[-4:] if iban else ""}
    from security.crypto import decrypt as _dec
    iban = _dec(info.get("iban_enc")) or ""
    name = _dec(info.get("name_enc")) or ""
    return {"iban": iban, "name": name, "bank": info.get("bank") or "",
            "iban_last4": info.get("iban_last4") or (iban[-4:] if iban else "")}


def mask_refund_bank_info(info):
    """Maskeli görünüm (tam IBAN/ad yok). Panel kutusu `iban` alanını gösterdiği için maskeli metin
    `iban` anahtarında döner; `masked: True` işaretlidir."""
    if not isinstance(info, dict) or not info:
        return None
    if info.get("enc"):
        last4 = str(info.get("iban_last4") or "")
        name_masked = "***" if info.get("name_enc") else ""
    else:
        iban = _clean_iban(info.get("iban"))
        last4 = iban[-4:] if iban else ""
        name_masked = _mask_person_name(info.get("name"))
    if not last4:
        return None
    return {"iban": f"TR** **** **** **** **** **{last4[:2]} {last4[2:]}", "name": name_masked,
            "bank": info.get("bank") or "", "iban_last4": last4, "masked": True}


async def refund_iban_full_allowed(user: dict) -> bool:
    try:
        perms = await get_effective_permissions(user or {})
    except Exception:
        return False
    return "*" in perms or any(p in perms for p in _IBAN_FULL_PERMS)


async def present_refund_bank_info_in(docs: list, current_user: dict, full=None, request=None) -> list:
    """Listedeki her sipariş/iade belgesinde refund_bank_info'yu (üst düzey + return_request altı)
    yetkiye göre TAM (çözülmüş) ya da MASKELİ hale getirir. Tam değer verildiyse denetim kaydı."""
    if full is None:
        full = await refund_iban_full_allowed(current_user)
    n_full = 0
    refs = []
    for d in docs or []:
        if not isinstance(d, dict):
            continue
        for holder in (d, d.get("return_request") if isinstance(d.get("return_request"), dict) else None):
            if not holder or not holder.get("refund_bank_info"):
                continue
            raw = holder["refund_bank_info"]
            holder["refund_bank_info"] = open_refund_bank_info(raw) if full else mask_refund_bank_info(raw)
            if full and holder["refund_bank_info"]:
                n_full += 1
                refs.append(str(d.get("order_number") or d.get("return_number") or d.get("claim_id")
                                or d.get("id") or "?")[:80])
    if full and n_full:
        # PRIV07: toplu/sıra dışı tam IBAN açma sayacı (eşik aşılırsa 429 yayılır → yanıt dönmez).
        # refs: HANGİ siparişlerin IBAN'ı açıldı — eşik uyarısında ve denetim kaydında listelenir.
        await _note_pii_reveal(current_user, "iban", n_full, refs=refs)
        try:
            from .deps import write_audit_log as _wal
            await _wal("refund_iban_view", user_id=(current_user or {}).get("id"),
                       email=(current_user or {}).get("email"), success=True,
                       meta={"count": n_full, "orders": refs[:100]})
        except Exception:
            pass
    return docs


def _encrypt_receipt_bytes(raw: bytes) -> str:
    from security import crypto as _c
    return _c._fernet.encrypt(raw).decode("ascii")


def get_receipt_bytes(receipt: dict):
    """Dekont baytları (şifreli `data_enc` ya da eski `data_b64`). Yoksa/çözülemezse None.
    Arşivlenmiş (archive_key) dekontlar için None döner — çağıran R2 arşivinden okur.
    scheduler._run_private_archive ve get_payment_receipt bu tek yardımcıyı kullanmalıdır."""
    if not isinstance(receipt, dict):
        return None
    enc = receipt.get("data_enc")
    if enc:
        try:
            from security import crypto as _c
            return _c._fernet_dec.decrypt(str(enc).encode("ascii"))
        except Exception as e:
            logger.error(f"[dekont] şifre çözülemedi: {type(e).__name__}")
            return None
    b64 = receipt.get("data_b64")
    if b64:
        import base64 as _b64
        try:
            return _b64.b64decode(b64)
        except Exception:
            return None
    return None


def seal_receipt(receipt: dict) -> dict:
    """Dekont kaydındaki düz `data_b64`'ü şifreli `data_enc`'e çevirir (metadata korunur)."""
    if not isinstance(receipt, dict) or not receipt.get("data_b64") or receipt.get("data_enc"):
        return receipt
    raw = get_receipt_bytes(receipt)
    if raw is None:
        return receipt
    out = {k: v for k, v in receipt.items() if k != "data_b64"}
    out["data_enc"] = _encrypt_receipt_bytes(raw)
    out["enc_format"] = _RECEIPT_ENC_FORMAT
    return out


def _strip_receipt_payload(doc: dict) -> None:
    """Yanıtlarda dekont baytı taşınmaz (görüntüleme /payment-receipt ucundan, yetkiyle)."""
    r = (doc or {}).get("payment_receipt")
    if isinstance(r, dict):
        r.pop("data_b64", None)
        r.pop("data_enc", None)


async def _guard_restore_mode():
    """Geri yükleme modunda dışarıya giden yazma (e-fatura, kargo etiketi) yapılmaz."""
    try:
        from security_switches import require_switch_off
    except Exception:
        return
    await require_switch_off("restore_mode")


async def _guard_switch(name: str):
    try:
        from security_switches import require_switch_off
    except Exception:
        return
    await require_switch_off(name)


# ═══════════════════════════════════════════════════════════════════════════════════════════
# POLİTİKA REDDİ KAYDI (T01 / PAY-01): müşteri başka birinin siparişine (okuma/iptal/iade/iade-iptal/
# imzalı fatura linki) erişmeye çalışıp reddedilince auth_audit_logs'a "policy_denial" yazılır.
# HAFİF + HIZ SINIRLI: aynı (kullanıcı/IP, eylem) için 60 sn'de en çok 1 kayıt; süreç geneli
# dakikada en çok 120 kayıt (tarama saldırısı denetim koleksiyonunu şişiremez). Yalnız iz kaydı —
# yanıtı/akışı DEĞİŞTİRMEZ; hata yutulur.
# ═══════════════════════════════════════════════════════════════════════════════════════════
_POLICY_DENIAL_SEEN: dict = {}
_POLICY_DENIAL_GLOBAL = {"window": 0, "n": 0}
_POLICY_DENIAL_KEY_TTL = 60.0
_POLICY_DENIAL_GLOBAL_MAX = 120


async def note_policy_denial(current_user: Optional[dict], action: str, *, order: Optional[dict] = None,
                             order_id: str = "", request: Optional[Request] = None,
                             reason: str = "not_owner") -> bool:
    """Döner: kayıt yazıldı mı (hız sınırına takıldıysa False). ASLA hata fırlatmaz."""
    try:
        now = time.time()
        uid = str((current_user or {}).get("id") or "")
        ip = ""
        if request is not None:
            try:
                from .deps import client_ip_from_request
                ip = client_ip_from_request(request) or ""
            except Exception:
                ip = ""
        key = f"{uid or ip or 'anon'}|{action}"
        last = _POLICY_DENIAL_SEEN.get(key)
        if last is not None and now - last < _POLICY_DENIAL_KEY_TTL:
            return False
        win = int(now // 60)
        if _POLICY_DENIAL_GLOBAL["window"] != win:
            _POLICY_DENIAL_GLOBAL.update({"window": win, "n": 0})
        if _POLICY_DENIAL_GLOBAL["n"] >= _POLICY_DENIAL_GLOBAL_MAX:
            return False
        _POLICY_DENIAL_GLOBAL["n"] += 1
        if len(_POLICY_DENIAL_SEEN) > 5000:
            for _k in [k for k, t in _POLICY_DENIAL_SEEN.items() if now - t >= _POLICY_DENIAL_KEY_TTL]:
                _POLICY_DENIAL_SEEN.pop(_k, None)
            if len(_POLICY_DENIAL_SEEN) > 5000:
                _POLICY_DENIAL_SEEN.clear()
        _POLICY_DENIAL_SEEN[key] = now
        from .deps import write_audit_log as _wal
        await _wal("policy_denial", user_id=uid or None, email=(current_user or {}).get("email"),
                   ip=ip or None, success=False,
                   meta={"resource": "order", "action": str(action)[:60], "reason": str(reason)[:40],
                         "order_id": str((order or {}).get("id") or order_id or "")[:64]})
        return True
    except Exception:
        return False


# ═══════════════════════════════════════════════════════════════════════════════════════════
# PRIV07 — tam IBAN / TCKN-VKN açık gösterimi sayacı. security.events.note_pii_reveal (başka iş
# paketinde) toplu/sıra dışı çözmede alarm üretir ve eşik aşılırsa HTTPException(429) fırlatır →
# AYNEN yayılır (yanıt dönmez). Modül/işlev henüz yoksa ya da başka bir hata olursa sessiz geçilir.
# ═══════════════════════════════════════════════════════════════════════════════════════════
async def _note_pii_reveal(actor: Optional[dict], kind: str, n: int = 1, refs=None) -> None:
    if not n or n < 1:
        return
    try:
        from security.events import note_pii_reveal as _npr
    except Exception:
        return
    try:
        res = _npr(actor or {}, kind, n=int(n), refs=refs)
        if hasattr(res, "__await__"):
            await res
    except HTTPException:
        raise
    except Exception as _e:
        logger.warning(f"[pii-reveal] sayaç yazılamadı ({type(_e).__name__})")


def _count_sealed_tax_ids(order: Optional[dict]) -> int:
    """Siparişte açılacak (şifreli) TCKN/VKN değeri sayısı — aynı değer bir kez sayılır."""
    if not isinstance(order, dict):
        return 0
    try:
        from security.pii import _containers_of as _pc
    except Exception:
        return 0
    seen = set()
    try:
        for cont, fields in _pc(order, "order"):
            for f in fields:
                enc = cont.get(f"{f}_enc")
                if enc:
                    seen.add(str(cont.get(f"{f}_hash") or enc))
    except Exception:
        return 0
    return len(seen)


# ═══════════════════════════════════════════════════════════════════════════════════════════
# SİPARİŞ-BAŞI İŞLEM KİLİDİ (COM09 / COM10): kargo etiketi ve iade talebi gibi DIŞ YAN ETKİLİ
# işlemler aynı sipariş için aynı anda iki kez yürümez. Atomik koşullu $set (kilit yok ya da süresi
# dolmuş) → ikinci eşzamanlı istek 409 alır. Kilit süreli (çökmede kendiliğinden düşer).
# Ödeme/statü/stok alanlarına DOKUNMAZ; yalnız `<name>_lock_until` / `<name>_lock_id` yazar.
# ═══════════════════════════════════════════════════════════════════════════════════════════
async def _acquire_order_op_lock(order_id: str, name: str, ttl_s: int = 120, extra_filter: dict = None):
    now = datetime.now(timezone.utc)
    token = uuid.uuid4().hex
    f_until, f_id = f"{name}_lock_until", f"{name}_lock_id"
    q = {"id": order_id, "$or": [{f_until: {"$exists": False}}, {f_until: None},
                                 {f_until: {"$lt": now.isoformat()}}]}
    if extra_filter:
        q = {"$and": [q, extra_filter]}
    res = await db.orders.update_one(q, {"$set": {
        f_until: (now + timedelta(seconds=int(ttl_s))).isoformat(), f_id: token}})
    return token if getattr(res, "modified_count", 0) == 1 else None


async def _release_order_op_lock(order_id: str, name: str, token: Optional[str]) -> None:
    if not token:
        return
    try:
        await db.orders.update_one({"id": order_id, f"{name}_lock_id": token},
                                   {"$unset": {f"{name}_lock_until": "", f"{name}_lock_id": ""}})
    except Exception as _e:
        logger.warning(f"[order-lock] {name} bırakılamadı order={order_id}: {type(_e).__name__}")


# Pazaryeri / dış kanal platformları — bu sistemin ödeme akışından GEÇMEYEN siparişler.
_MARKETPLACE_PLATFORMS = frozenset({
    "trendyol", "hepsiburada", "amazon", "temu", "n11", "ticimax", "ciceksepeti", "pazarama",
    "idefix", "koton", "modanisa", "marketplace",
})


def _is_site_order(order: Optional[dict]) -> bool:
    """Site (vitrin) siparişi mi? create_order platform='meluni' yazar; eski kayıtlar 'site'/'web'/boş
    olabilir. Tek doğruluk kaynağı: platform VE marketplace alanı pazaryeri DEĞİLSE site siparişidir.
    (Ödeme kapıları — değişmez 1/2/3 — buna göre çalışır; 'platform == "site"' karşılaştırması
    'meluni' yazılan gerçek site siparişlerini kaçırıyordu.)"""
    if not isinstance(order, dict):
        return False
    plat = str(order.get("platform") or "").strip().lower()
    mkt = str(order.get("marketplace") or "").strip().lower()
    return plat not in _MARKETPLACE_PLATFORMS and mkt not in _MARKETPLACE_PLATFORMS


def _order_search_or(search: str) -> list:
    """Sipariş genel araması: tek kutu → akla gelen HER alanda eşleşir.
    Türkçe duyarsız + kısmi + telefon-rakam + çok kelimeli ad-soyad."""
    s = (search or "").strip()
    if not s:
        return []
    rx = {"$regex": _search_tr_regex(s), "$options": "i"}
    fields = [
        "order_number", "invoice_number", "cargo_tracking", "id",
        # Pazaryeri kimlikleri: müşteri/panel bunları da arayabilir (TY paket no ≠ order_number).
        "trendyol_package_id", "hepsiburada_order_number", "hb_claim_number",
        "package_number", "shipment_package_id", "marketplace_order_id",
        "cargo_tracking_number", "return_request.return_code",
        "shipping_address.first_name", "shipping_address.last_name",
        # İsim bazen SPLIT değil tek alanda tutulur (full_name/name) ya da üst-düzeyde
        # (customer_name) — bunlar taranmazsa o siparişler aramada HİÇ gelmiyordu.
        "shipping_address.full_name", "shipping_address.name",
        "customer_name", "customer_email", "customer_phone",
        "user_email", "user_phone", "email", "phone",
        "shipping_address.email", "shipping_address.phone",
        "shipping_address.city", "shipping_address.district",
        "shipping_address.address",
        # KVKK: TCKN/VKN artık şifreli — kısmi regex araması YOK; tam eşleşme aşağıda hash ile.
        "billing_info.company_name",
        "coupon_code",
        "items.product_name", "items.name", "items.barcode",
        "items.stock_code", "items.sku",
        "lines.product_name", "lines.name", "lines.barcode",
    ]
    ors = [{f: rx} for f in fields]
    # TCKN/VKN araması: <yol>_hash (HMAC, tüm anahtarlar) + backfill öncesi eski düz değer TAM eşleşmesi.
    ors.extend(_pii_search(s))
    # Pazaryeri paket/no alanları INT saklanabilir → regex tutmaz. Arama tamamen rakamsa
    # bu alanlarda hem int hem string olarak TAM eşleşme ekle (TY paket no vb. bulunur).
    if s.isdigit():
        try:
            _n = int(s)
        except Exception:
            _n = None
        for f in ("trendyol_package_id", "package_number", "shipment_package_id",
                  "marketplace_order_id", "hepsiburada_order_number"):
            ors.append({f: s})
            if _n is not None:
                ors.append({f: _n})
    # Telefon: sadece rakamları al, son 10 haneyi ara (formatten bağımsız)
    digits = re.sub(r"\D", "", s)
    if len(digits) >= 7:
        ors.append({"shipping_address.phone": {"$regex": re.escape(digits[-10:])}})
    # Çok kelimeli tam ad: her kelime ad VEYA soyadda geçsin (sıra önemsiz)
    words = [w for w in s.split() if len(w) >= 2]
    if len(words) >= 2:
        # Ad-soyad her kelimesi, isim TUTULAN HERHANGİ bir alanda geçebilsin (first/last
        # ayrı ya da full_name/name/customer_name tek alan) — sıra önemsiz. Böylece
        # "Ezgi Ceren Uluşan" gibi çok kelimeli aramalar tüm sipariş şemalarında bulunur.
        nf = ["shipping_address.first_name", "shipping_address.last_name",
              "shipping_address.full_name", "shipping_address.name", "customer_name"]
        ors.append({"$and": [
            {"$or": [{f: {"$regex": _search_tr_regex(w), "$options": "i"}} for f in nf]}
            for w in words
        ]})
    return ors


def _platform_display(order) -> str:
    p = (str(order.get("platform") or order.get("marketplace") or "")).strip().lower()
    return {"trendyol": "Trendyol", "hepsiburada": "Hepsiburada"}.get(
        p, "Site" if p in ("", "meluni", "site") else p.title())


async def _log_order_event(order_id: str, event_type: str, description: str,
                           current_user: dict = None, meta: dict = None,
                           order_number: str = None):
    """Sipariş işlem geçmişi (audit log) kaydı → db.order_events.

    Her sipariş aksiyonunda (durum, ödeme, kargo, not, fatura, iade) bir iz
    bırakır. Tasarım gereği HİÇBİR ZAMAN isteği bozmaz; hata olursa sadece
    uyarı loglar. `stock_movements` deseninin birebir kardeşidir.
    """
    try:
        # Sipariş üzerinde bir aksiyon oldu → satış raporu önbelleği anında düşsün,
        # rapor 90 sn beklemeden yeni veriyi göstersin. (Bu fonksiyon tasarım gereği
        # isteği bozmaz; temizlik de aynı try içinde, sessiz.)
        try:
            from .reports import invalidate_report_cache as _inv_rc
            await _inv_rc()
        except Exception:
            pass
        if order_number is None:
            _o = await db.orders.find_one({"id": order_id}, {"_id": 0, "order_number": 1})
            order_number = (_o or {}).get("order_number", "")
        await db.order_events.insert_one({
            "id": str(uuid.uuid4()),
            "order_id": order_id,
            "order_number": order_number or "",
            "event_type": event_type,
            "description": description,
            "actor": (current_user or {}).get("email", "") if current_user else "",
            # KVKK: olay kaydında TCKN/VKN yalnız maskeli (şifreli/hash alanlar atılır); telefon/açık adres
            # anahtarları da maskelenir (adres değişikliği izi → yalnız il/ilçe + son 2 hane).
            "meta": _contact_scrub(_pii_scrub(meta or {}), text=False),
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
    except Exception as _e:  # pragma: no cover - log asla isteği bozmaz
        logger.warning(f"order_event log failed for {order_id}: {_e}")


async def _enrich_items_with_products(items):
    """Sipariş kalemlerini barkodla products'tan zenginleştir: Marka + KDV oranı.

    Stored veriyi DEĞİŞTİRMEZ; sadece okuma anında (detay) ekler. Tek bir $in
    sorgusuyla hem ürün hem varyant barkodunu eşler. Hata olursa items'ı olduğu
    gibi döndürür. (Desi/ağırlık ürün şemasında yok — eklenmez.)
    """
    try:
        if not items:
            return items
        bset = list({str(it.get("barcode") or "").strip() for it in items if (it.get("barcode") or "").strip()})
        if not bset:
            return items
        bmap = {}
        cursor = db.products.find(
            {"$or": [{"barcode": {"$in": bset}}, {"variants.barcode": {"$in": bset}}]},
            {"_id": 0, "barcode": 1, "brand": 1, "vat_rate": 1, "variants.barcode": 1},
        )
        async for p in cursor:
            meta = {"brand": p.get("brand") or "", "vat_rate": p.get("vat_rate")}
            pb = str(p.get("barcode") or "").strip()
            if pb:
                bmap[pb] = meta
            for v in (p.get("variants") or []):
                vb = str(v.get("barcode") or "").strip()
                if vb:
                    bmap[vb] = meta
        for it in items:
            m = bmap.get(str(it.get("barcode") or "").strip())
            if not m:
                continue
            if not it.get("brand") and m.get("brand"):
                it["brand"] = m["brand"]
            if it.get("vat_rate") in (None, "") and m.get("vat_rate") is not None:
                it["vat_rate"] = m["vat_rate"]
        return items
    except Exception as _e:  # pragma: no cover
        logger.warning(f"item enrich failed: {_e}")
        return items


# ---------------------------------------------------------------------------
# Fire-and-forget arka plan görevleri için GÜÇLÜ referans havuzu.
# asyncio.create_task'ın referansı tutulmazsa, event loop yalnızca ZAYIF referans
# tuttuğundan görev bitmeden GC tarafından silinebilir → bildirim "bazen gidiyor
# bazen gitmiyor" olur (dokümante asyncio tuzağı). _spawn referansı tutar; bittiğinde
# done-callback ile havuzdan düşürür. TÜM bildirim/CAPI create_task'ları bunu kullanır.
# ---------------------------------------------------------------------------
_BG_TASKS: set = set()


def _spawn(coro):
    import asyncio as __a
    _t = __a.create_task(coro)
    _BG_TASKS.add(_t)
    _t.add_done_callback(_BG_TASKS.discard)
    return _t


async def _send_admin_new_order_email(order: dict, ship: dict, kind_label: str = "") -> None:
    """Admin'e (firma iletişim adresi → beyaz-etiket varsayılan info@meluni.com.tr) yeni sipariş
    ÖZET maili. OLUŞTURMADA yalnız ödeme-onayı bekleyen ama gerçek aksiyon bekleyen site
    siparişlerinde (havale/EFT, kapıda ödeme) gönderilir. KART siparişi HARİÇ — o, 3DS yarıda
    kalabileceğinden ödeme onaylanınca payment._notify_paid_order_confirmed ile gider (invariant #3).
    Best-effort: hata sipariş akışını ETKİLEMEZ."""
    try:
        import company as _company
        from notification_service import _email_send
        _ci = await _company.get_company(db)
        _to = str(_ci.get("contact_email") or _ci.get("email") or "info@meluni.com.tr").strip()
        if not _to:
            return
        # Gövde ORTAK üreteçten gelir (notification_service) → iki sipariş maili de
        # aynı ve ORTALI. Müşteri-kontrollü alanların kaçırılması da orada yapılır.
        from notification_service import build_admin_order_email_html as _bld
        _html = _bld(order, ship, "Yeni Sipariş", kind_label)
        await _email_send(db, _to, f"Yeni Sipariş · {order.get('order_number','')}", _html)
        logger.info(f"admin yeni-sipariş maili → {_to} order={order.get('order_number')} ({kind_label})")
    except Exception as _e:
        logger.warning(f"admin yeni-sipariş maili atlandı: {_e}")


async def _order_notify_vars(order: dict, **extra) -> dict:
    """Bir sipariş dokümanından TÜM bildirim değişkenlerini (email + SMS şablonlarının
    kullandığı her placeholder) tek elden üretir. Amaç: hiçbir ma/sms'te {order_date},
    {items_html}, {subtotal}, {shipping_*}, {tracking_*} gibi alanlar BOŞ/ham kalmasın.
    Her order-bildirim çağrısı bunu kullanır → değişken↔veri köprüsü her yerde aynı.
    **extra ile event'e özel değerler (return_code, refund_amount, reason...) eklenir/üzerine yazılır.
    """
    import os as _os
    ship = order.get("shipping_address") or {}
    bill = order.get("billing_address") or {}
    # Ad-soyad HER kaynaktan aranır — {customer_name} bazı SMS'lerde boş/"Müşterimiz" kalıyordu
    # (isim shipping değil; top-level, billing veya guest full_name alanındaydı).
    def _fn(d):
        d = d or {}
        return f"{d.get('first_name','')} {d.get('last_name','')}".strip()
    full_name = (_fn(ship) or ship.get("full_name") or ship.get("name")
                 or _fn(order) or order.get("customer_name") or order.get("full_name") or order.get("name")
                 or _fn(bill) or bill.get("full_name") or bill.get("name")
                 or "Müşterimiz")

    # Kalemler tablosu (items_html) — sipariş onay mailindeki ürün listesi
    items_rows = ""
    for it in (order.get("items") or []):
        name = it.get("name") or it.get("product_name") or "Ürün"
        qty = int(it.get("quantity") or 1)
        price = float(it.get("price") or 0)
        size = (it.get("size") or it.get("variant_size") or "")
        color = (it.get("color") or it.get("variant_color") or "")
        img = (it.get("image") or it.get("image_url") or it.get("thumbnail")
               or (it.get("images") or [""])[0] if it.get("images") else
               (it.get("image") or it.get("image_url") or it.get("thumbnail") or ""))
        meta = " · ".join([x for x in [f"Beden: {size}" if size else "",
                                       f"Renk: {color}" if color else "",
                                       f"Adet: {qty}"] if x])
        items_rows += (
            f'<tr><td style="padding:12px 0;border-bottom:1px solid #f0f0f0;width:80px;">'
            f'<img src="{img}" alt="" style="width:64px;height:80px;object-fit:cover;background:#fafafa;"/></td>'
            f'<td style="padding:12px 12px;border-bottom:1px solid #f0f0f0;font-size:13px;color:#111;">{name}'
            f'<div style="color:#999;font-size:11px;margin-top:4px;">{meta}</div></td>'
            f'<td style="padding:12px 0;border-bottom:1px solid #f0f0f0;text-align:right;font-size:13px;color:#111;white-space:nowrap;">{(price*qty):.2f} TL</td></tr>'
        )
    items_html = ('<div style="padding:0 24px 16px;"><table cellpadding="0" cellspacing="0" border="0" '
                  f'style="width:100%;">{items_rows}</table></div>') if items_rows else ""

    # Mağaza (vitrin) adresi: panelde kayıtlı storefront_url → FRONTEND_PUBLIC_URL → SITE_URL.
    # REACT_APP_BACKEND_URL (API alanı) KULLANILMAZ — sipariş/dekont linki API'ye gidip 404 veriyordu.
    _sf = ""
    try:
        from tenant_config import get_tenant_config as _gtc
        _sf = ((await _gtc(db)).get("domains") or {}).get("storefront_url") or ""
    except Exception:
        _sf = ""
    base_url = (_sf or _os.environ.get("FRONTEND_PUBLIC_URL") or _os.environ.get("SITE_URL")
                or _os.environ.get("REACT_APP_BACKEND_URL") or "").rstrip("/")
    onum = order.get("order_number") or order.get("id") or ""
    order_link = f"{base_url}/order-success/{onum}" if base_url else "#"
    order_date = str(order.get("created_at") or "")[:10]

    subtotal = float(order.get("subtotal") or 0)
    shipping_cost = float(order.get("shipping_cost") or 0)
    # E-postadaki "İndirim" satırı havale/EFT ödeme indirimini de içerir — eskiden yalnız kupon +
    # kampanya yazılıyordu; satırlar toplamı tutmuyor, müşteri "%5 uygulanmadı" sanıyordu.
    payment_discount = float(order.get("payment_discount") or 0)
    discount = float(order.get("discount") or order.get("discount_amount") or 0) + payment_discount
    total = float(order.get("total") or 0)

    cargo = order.get("cargo") or {}
    # Takip no: önce çağrının extra ile geçtiği değer (manuel "Kargoya Ver" anında DB
    # güncellense de buraya gelen 'order' belgesi eski/boş olabilir), sonra order/cargo.
    real_tn = (str(extra.get("tracking_number") or "").strip()
               or str(order.get("cargo_tracking_number") or "").strip()
               or str(cargo.get("tracking_number") or "").strip())
    track_link = (order.get("cargo_tracking_link") or cargo.get("tracking_link")
                  or (f"https://kargotakip.dhlecommerce.com.tr/?takipNo={real_tn}" if real_tn
                      else "https://www.dhlecommerce.com.tr/gonderitakip"))
    cargo_provider = (order.get("cargo_provider_name") or cargo.get("provider_name") or "MNG Kargo")

    try:
        from order_statuses import customer_label_for as _clf
        status_label = _clf(order.get("status") or "")
    except Exception:
        status_label = ""

    # Havale/EFT → banka bilgileri (ödeme bekleyen siparişlerde mailde gösterilir)
    bank = {}
    if (order.get("status") == "awaiting_payment"
            or (order.get("payment_method") or "").lower() in
            ("bank_transfer", "havale", "eft", "havale_eft", "banka_havale", "havale/eft")):
        try:
            _pay = await db.settings.find_one({"id": "payment"}, {"_id": 0}) or {}
            _banks = _pay.get("bank_accounts") or []
            bank = next((b for b in _banks if b.get("is_default")), None) or (_banks[0] if _banks else {})
        except Exception:
            bank = {}

    v = {
        "customer_name": full_name,
        "first_name": ship.get("first_name", "") or (full_name.split(" ")[0] if full_name else ""),
        "name": full_name,
        "order_number": onum,
        "order_date": order_date,
        "amount": f"{total:.2f} TL",
        "subtotal": f"{subtotal:.2f}",
        "shipping_cost": f"{shipping_cost:.2f}",
        "discount": f"{discount:.2f}",
        "total": f"{total:.2f}",
        "items_html": items_html,
        "shipping_full_name": full_name,
        "shipping_address": ship.get("address", ""),
        "shipping_city": ship.get("city", ""),
        "shipping_district": ship.get("district", ""),
        "shipping_phone": ship.get("phone", ""),
        "tracking_number": real_tn,
        "tracking_link": track_link,
        "tracking_url": track_link,
        "cargo_provider": cargo_provider,
        "cargo_company": cargo_provider,
        "status_label": status_label,
        "order_link": order_link,
        "site_url": base_url,
        "cart_url": f"{base_url}/sepet",
        "bank_name": (bank or {}).get("bank_name", ""),
        "bank_branch": (bank or {}).get("branch", ""),
        "bank_iban": (bank or {}).get("iban", ""),
        "bank_account_holder": (bank or {}).get("account_holder", ""),
        "payment_url": (f"{base_url}/odeme-bildirimi/{onum}" if base_url else order_link),
    }
    if extra:
        v.update({k: val for k, val in extra.items() if val is not None})
    return v


def _order_vade_farki(order: dict):
    """Taksitli sipariş için vade farkı (KDV DAHİL tutar), gerçek tahsilat ve taksit sayısı.

    Müşteri taksitli ödediğinde iyzico'ya gönderilen/charge edilen `paidPrice` vade farkı
    DAHİL tutardır (bkz. payment.py _installment_total_price). Sipariş `total`'ı ise peşin
    (vade farkı hariç) sepet tutarıdır. Aradaki fark = vade farkı. KDV Kanunu 24/c gereği
    bu fark matraha dahil edilip aynı oranda KDV ile faturalandırılmalıdır.

    Döner: (vade_farki, charged_total, installment). Taksitsiz/farsız ise vade_farki=0.
    """
    iyz = order.get("iyzico_retrieve_response") or {}
    try:
        inst = int(float(iyz.get("installment") or order.get("installment") or 1))
    except Exception:
        inst = 1
    try:
        charged = round(float(iyz.get("paidPrice") or order.get("paid_amount") or 0), 2)
    except Exception:
        charged = 0.0
    base = round(float(order.get("total") or 0), 2)
    vf = round(charged - base, 2) if (charged > 0 and base > 0) else 0.0
    if inst > 1 and vf >= 0.01:
        return vf, charged, inst
    # PEŞİN / 1-TAKSİT → vade farkı YOK. 'charged' olarak BOZUK paid_amount'ı DEĞİL, order.total'ı
    # döndür (Doğa Altaş/Gözde Erdoğan: peşin ama paid_amount=2×total → gider pusulası/iade tutarı
    # 2 katına çıkıyordu). Böylece TÜM tüketiciler (panel, GP, refund) doğru tutarı alır.
    return 0.0, (base if base > 0 else charged), inst


async def _product_vat_map(order: dict) -> dict:
    """Sipariş kalemlerinin ürünlerinden product_id → vat_rate (KDV) haritası.
    Fatura kesiminde, kalemde vat_rate yoksa ürünün KDV oranı kullanılır (DİNAMİK)."""
    pids = list({it.get("product_id") for it in (order.get("items") or []) if it.get("product_id")})
    m = {}
    if pids:
        try:
            async for p in db.products.find({"id": {"$in": pids}}, {"_id": 0, "id": 1, "vat_rate": 1}):
                if p.get("vat_rate") not in (None, ""):
                    m[p["id"]] = float(p["vat_rate"])
        except Exception:
            pass
    return m


def _item_kdv(it: dict, vat_map: dict, default: float = 10.0) -> float:
    """Kalem KDV oranı: önce kalemin vat_rate/kdv_rate'i, sonra ürünün vat_rate'i, en son default.
    Eskiden sabit 10.0'a düşüyordu; artık ürün içindeki gerçek KDV alanına köprüleniyor."""
    for cand in (it.get("vat_rate"), it.get("kdv_rate"), vat_map.get(it.get("product_id"))):
        if cand not in (None, ""):
            try:
                return float(cand)
            except Exception:
                pass
    return float(default)


def _eff_unit_price(prod: dict, variant_id: str = None) -> float:
    """Bir ürün (ve varsa varyant) için SUNUCU-OTORİTER birim fiyat.
    Taban = geçerli indirimli fiyat (0 < sale_price < price ise), yoksa liste fiyatı.
    Varyantın price_adjustment (veya eski alias price_diff) farkı eklenir.
    İstemcinin gönderdiği fiyat ASLA kullanılmaz — sahte fiyatla ödeme (K1) buradan kapanır."""
    try:
        base = float(prod.get("price") or 0)
    except Exception:
        base = 0.0
    sp = prod.get("sale_price")
    try:
        sp = float(sp) if sp not in (None, "") else None
    except Exception:
        sp = None
    if sp is not None and 0 < sp < base:
        base = sp
    adj = 0.0
    if variant_id:
        for v in (prod.get("variants") or []):
            if v.get("id") == variant_id:
                try:
                    adj = float(v.get("price_adjustment") or v.get("price_diff") or 0)
                except Exception:
                    adj = 0.0
                break
    return round(base + adj, 2)


def _server_variant_barcode(prod: dict, variant_id: str = None):
    """GÜVENLİK: Stok düşüm anahtarını (barcode) SUNUCU'dan çöz. variant_id ile
    eşleşen varyantın barkodunu döndürür; eşleşme yoksa None (o zaman mevcut istemci
    değeri korunur → regresyon riski yok). İstemcinin yanlış varyantın stoğunu
    düşürmesini engeller."""
    if not variant_id:
        return None
    for v in (prod.get("variants") or []):
        if v.get("id") == variant_id:
            bc = v.get("barcode") or v.get("sku")
            return bc or None
    return None


async def _issue_pay_token(order_id: str) -> str:
    """Denetim #4: yalnız siparişi oluşturan istemciye verilen ödeme yetkisi (payment.py).
    Üretilemezse boş döner (sipariş akışı ASLA kırılmaz; istemci tekrar POST'ta yenisini alır)."""
    try:
        from .payment import issue_payment_capability
        return await issue_payment_capability(order_id)
    except Exception as _pc_err:
        logger.warning(f"ödeme yetkisi üretilemedi order_id={order_id}: {type(_pc_err).__name__}")
        return ""


async def next_order_number() -> str:
    """Kısa, sıralı site sipariş numarası: W10001, W10002, ...
    Atomik sayaç (db.counters) ile çakışma imkânsız. Sayaç başarısız olursa
    kısa, W ön ekli bir fallback üretir; sipariş oluşturma asla kırılmaz."""
    try:
        doc = await db.counters.find_one_and_update(
            {"_id": "order_seq"},
            {"$inc": {"seq": 1}},
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
        return f"W{10000 + int(doc['seq'])}"
    except Exception as e:
        logger.warning(f"order_seq sayaci basarisiz, fallback kullaniliyor: {e}")
        import secrets
        return f"W{int(time.time()) % 1000000}{secrets.token_hex(1).upper()}"

# Sipariş listesi sayım (count) önbelleği — büyük koleksiyonda count_documents her
# istekte koleksiyonu tarıyor (özellikle valid/hide_closed gibi $nor/$nin filtrelerinde
# pahalı). Aynı filtre için kısa süre cache'leyip sayfalama/yenilemede tekrar taramayı
# önleriz. Filtre imzası skip/limit içermez → tüm sayfalar aynı toplamı paylaşır.
_ORDERS_COUNT_CACHE = {}   # sig -> (count, monotonic_ts)
_ORDERS_COUNT_TTL = 60.0   # sn

@router.get("")
async def get_orders(
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=1000),
    status: Optional[str] = None,
    search: Optional[str] = None,
    phone: Optional[str] = None,
    email: Optional[str] = None,
    order_number: Optional[str] = None,
    cargo_tracking: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    payment_method: Optional[str] = None,
    platform: Optional[str] = None,
    invoice_number: Optional[str] = None,
    payment_status: Optional[str] = None,
    channel: Optional[str] = None,
    source: Optional[str] = None,
    coupon_code: Optional[str] = None,
    influencer: Optional[str] = None,
    is_corporate: Optional[str] = None,
    payment_view: Optional[str] = "all",
    hide_closed: Optional[str] = None,
    show_hidden: Optional[str] = None,   # 1 → HİÇBİR şeyi gizleme (iptal/iade + ödenmemiş junk dahil)
    current_user: dict = Depends(require_permission("orders.view"))
):
    """Get orders with pagination (admin only)"""
    skip = (page - 1) * limit
    query = {}
    _default_hidden = False  # varsayılan iade/iptal gizlemesi uygulandı mı? (hidden_summary için)
    _show_hidden = str(show_hidden).lower() in ("1", "true", "yes")
    # Belirli sipariş araması mı? (Sipariş No / Fatura / Kargo / Telefon / E-posta / Kupon / genel arama)
    # → ödeme başarısız/junk siparişler bile no ile BULUNABİLMELİ; varsayılan gizleme uygulanmaz.
    _specific_search = bool(search or order_number or invoice_number or cargo_tracking
                            or phone or email or coupon_code)

    if status:
        _st = [x.strip() for x in str(status).split(",") if x.strip()]
        query["status"] = _st[0] if len(_st) == 1 else {"$in": _st}
    elif _show_hidden:
        # "Gizlenenleri göster" → HİÇBİR durum gizlenmez (iptal/iade/refund dahil hepsi listede).
        pass
    elif str(hide_closed).lower() in ("0", "false"):
        # Açıkça "kapalı durumları da göster" istendi → durum filtresi uygulanmaz.
        pass
    elif (search or order_number or invoice_number or cargo_tracking
          or phone or email or coupon_code):
        # BELİRLİ SİPARİŞ ARAMASI: genel arama VEYA Sipariş No / Fatura No / Kargo No / Telefon /
        # E-posta / Kupon alanlarından biri girildiyse kullanıcı belirli bir siparişi arıyor →
        # TÜM durumlarda ara (iptal/iade/refund dahil). Varsayılan "kapalı durumları gizle"
        # filtresi UYGULANMAZ; aksi halde iade/iptal olmuş bir sipariş (ör. iade edilmiş sipariş)
        # Sipariş No alanına yazılsa bile bulunamaz. (Önceden yalnız genel arama bunu aşıyordu.)
        pass
    else:
        # VARSAYILAN: ana "Tüm Siparişler" görünümünde iptal/İADE grubu kapalı durumları GİZLE.
        # Böylece iade TALEBİ oluşan sipariş de ana listede KALMAZ, İadeler sayfasına "gider".
        # (return_requested/in_transit/rejected/partial_refunded dahil — hepsi İadeler'de listelenir.)
        query["status"] = {"$nin": [
            "cancelled", "cancel_refunded",  # iptal + "Ödemeli İptal Onaylandı" → ana listede DEĞİL, İptaller sekmesinde
            "returned", "refunded", "return_approved",
            "return_requested", "return_in_transit", "return_rejected", "partial_refunded",
            "payment_failed",  # ödemesi hiç alınamamış (başarısız kart) sipariş → ana listede GÖRÜNMEZ
        ]}
        _default_hidden = True
    if phone:
        # Eskiden yalnız shipping_address.phone alanına ve kullanıcının YAZDIĞI ham metne
        # bakılıyordu: "0532 123 45 67" yazan, DB'deki "+905321234567" ile eşleşmiyor ve filtre
        # BOŞ liste veriyordu (genel arama kutusu ise buluyordu — "filtre bozuk" algısı).
        # Artık genel aramayla aynı: rakamlar ayıklanır, son 10 hane tüm telefon alanlarında aranır.
        _ph_digits = re.sub(r"\D", "", str(phone))[-10:]
        if _ph_digits:
            query.setdefault("$and", []).append({"$or": [
                {f: {"$regex": re.escape(_ph_digits)}} for f in
                ("shipping_address.phone", "billing_address.phone", "customer_phone",
                 "user_phone", "phone")
            ]})
    if email:
        # Aynı gerekçe: e-posta birden çok alanda saklanabiliyor.
        _em = re.escape(str(email).strip())
        if _em:
            query.setdefault("$and", []).append({"$or": [
                {f: {"$regex": _em, "$options": "i"}} for f in
                ("shipping_address.email", "billing_address.email", "customer_email",
                 "user_email", "email")
            ]})
    if order_number:
        # NET FİLTRELEME (kullanıcı bildirimi): eskiden çıpasız regex kullanılıyordu →
        # "W118" yazınca W11800…W11899 hepsi, "1180" yazınca içinde 1180 GEÇEN her sipariş
        # geliyordu; tam numara yazıldığında bile daha uzun numaralar eşleşebiliyordu.
        # Artık: tam sipariş numarası (W'li ya da W'siz, baştaki sıfırlar önemsiz) → YALNIZ o
        # sipariş; kısmi yazımda ise BAŞTAN eşleşme (önek), ortadan eşleşme yok.
        _on = str(order_number).strip()
        _m = re.fullmatch(r"[Ww]?\s*0*(\d{3,})", _on)
        _locked = False
        if _m:
            _cands = [f"W{_m.group(1)}", _m.group(1)]
            try:
                if await db.orders.find_one({"order_number": {"$in": _cands}}, {"_id": 0, "id": 1}):
                    query["order_number"] = {"$in": _cands}   # TAM numara → yalnız o sipariş
                    _locked = True
            except Exception:
                _locked = False
        if not _locked:
            # Kısmi yazım → BAŞTAN eşleşme (W'li/W'siz). Ortadan eşleşme yok.
            _core = _m.group(1) if _m else _on
            query["order_number"] = {"$regex": f"^W?0*{re.escape(_core)}", "$options": "i"}
    if cargo_tracking:
        query["cargo_tracking"] = {"$regex": re.escape(str(cargo_tracking)), "$options": "i"}
    if payment_method:
        _pm = [x.strip() for x in str(payment_method).split(",") if x.strip()]
        if _pm:   # "," gibi boş seçimde filtre HİÇ uygulanmaz (eskiden $in:[] → hep boş liste)
            query["payment_method"] = {"$in": _pm} if len(_pm) > 1 else _pm[0]
    if platform:
        _pf = [x.strip() for x in str(platform).split(",") if x.strip()]
        if _pf:
            # Pazaryeri siparişlerinin bir kısmında kanal 'platform' yerine 'marketplace'
            # alanında duruyor → tek alana bakan filtre onları bulamıyordu.
            query.setdefault("$and", []).append({"$or": [
                {"platform": {"$in": _pf}}, {"marketplace": {"$in": _pf}}]})
    if invoice_number:
        query["invoice_number"] = {"$regex": re.escape(str(invoice_number)), "$options": "i"}
    if payment_status:
        _ps = [x.strip() for x in str(payment_status).split(",") if x.strip()]
        if _ps:
            query["payment_status"] = {"$in": _ps} if len(_ps) > 1 else _ps[0]
    if channel:
        # Çoklu seçim: her parça escape edilip alternation kurulur (ReDoS koruması).
        # DÜZELTME: "Reklam (Tümü)" seçeneğinin değeri 'ads|paid' — yani tek başına bir
        # alternation. Yalnız virgülle bölünüp escape edilince 'ads\|paid' literaline dönüşüyor
        # ve HİÇBİR kayıtla eşleşmiyordu (filtre her zaman boş liste veriyordu). Boru işareti de
        # ayırıcı sayılır.
        _parts = [x.strip() for x in re.split(r"[,|]", str(channel)) if x.strip()]
        _ch = "|".join(re.escape(x) for x in _parts)
        if _ch:
            query["attribution.channel"] = {"$regex": _ch, "$options": "i"}
    if source:
        query["attribution.source"] = {"$regex": re.escape(str(source)), "$options": "i"}
    if coupon_code:
        query["coupon_code"] = {"$regex": re.escape(str(coupon_code)), "$options": "i"}
    if influencer and str(influencer).lower() not in ("0", "false", ""):
        query["influencer_id"] = {"$exists": True, "$ne": None}
    if is_corporate and str(is_corporate).lower() not in ("0", "false", ""):
        query["billing_info.is_corporate"] = True
        
    # Tarih aralığı TR yerel günü kabul edilir → UTC sınırı (bitiş günü tam dahil, TZ kayması yok).
    date_query = {}
    if start_date:
        date_query["$gte"] = tr_day_start_utc(start_date)
    if end_date:
        date_query["$lte"] = tr_day_end_utc(end_date)
    if date_query:
        # created_at KARIŞIK TİPTE: çoğu kayıt ISO metin, eski/migrasyon kayıtları BSON Date.
        # Sınırlar yalnız METİN olarak verilince BSON Date kayıtları HİÇ eşleşmiyor ve tarih
        # filtresinde sessizce kayboluyorlardı. Her iki tip için de sınır uygulanır.
        _dt_q = {}
        for _k, _v in date_query.items():
            try:
                _dt_q[_k] = datetime.fromisoformat(str(_v))
            except Exception:
                pass
        if _dt_q:
            query.setdefault("$and", []).append(
                {"$or": [{"created_at": date_query}, {"created_at": _dt_q}]})
        else:
            query["created_at"] = date_query
    
    if search:
        # Kullanıcı genel arama kutusuna SİPARİŞ NUMARASI yazdıysa (W11805 / 11805) ve o numara
        # gerçekten varsa, geniş alan taraması yerine TAM EŞLEŞMEYE kilitlenilir. Aksi halde
        # numaranın rakamları adreste/telefonda/kalem kodunda geçen başka siparişler de listeye
        # giriyor ve "filtre net değil, birden fazla sipariş çıkıyor" oluyordu.
        _sq = str(search).strip()
        _ms = re.fullmatch(r"[Ww]?\s*0*(\d{3,})", _sq)
        _exact_locked = False
        if _ms:
            _cands = [f"W{_ms.group(1)}", _ms.group(1)]
            try:
                if await db.orders.find_one({"order_number": {"$in": _cands}}, {"_id": 0, "id": 1}):
                    query["order_number"] = {"$in": _cands}
                    _exact_locked = True
            except Exception:
                _exact_locked = False
        _ors = [] if _exact_locked else _order_search_or(search)
        if _ors:
            if "$or" in query:
                # Mevcut $or'u ezme: $and altında birleştir (ikisi de geçerli olmalı)
                query.setdefault("$and", []).append({"$or": query.pop("$or")})
                query["$and"].append({"$or": _ors})
            else:
                query["$or"] = _ors
    
    # Ödeme kaydı bulunmayan (web kart denemesi, hiç ödenmemiş) ayrımı
    # 'Web siparişi' = platform meluni/boş VE marketplace alanı da boş. Eskiden yalnız
    # platform'a bakılıyordu; platform'u yazılmamış ama marketplace=trendyol olan pazaryeri
    # siparişleri "web" sayılıp junk süzgecine takılabiliyordu.
    _web_cond = {"$and": [
        {"$or": [{"platform": "meluni"}, {"platform": {"$in": [None, ""]}},
                 {"platform": {"$exists": False}}]},
        {"$or": [{"marketplace": {"$in": [None, ""]}}, {"marketplace": {"$exists": False}}]},
    ]}
    _settled_pay = ["paid", "completed", "success", "succeeded", "captured"]
    # Havale/EFT ve kapıda ödeme siparişleri ödenmemiş olsa da "junk" (kayıp web kart denemesi)
    # DEĞİLDİR — listede kalmalı. Bu yüzden TÜM alias'lar dahil (sadece kanonik iki string değil;
    # aksi halde 'havale'/'kapida' alias'lı meşru sipariş varsayılan listeden kayboluyordu — denetim #1).
    # NOT: KART (credit_card) burada YOK — ödenmemiş kart siparişi (yarıda kalan 3DS) gizli kalmalı.
    _offline_pm = ["bank_transfer", "havale", "eft", "havale_eft", "banka_havale", "transfer",
                   "cash_on_delivery", "kapida", "kapida_odeme", "cod"]
    # "cancel_requested" (İptal Talebi Alındı) BURADA OLMALI: müşteri o sipariş için AÇIKÇA
    # işlem yapmıştır — yarıda kalmış bir kart denemesi değildir. Eksik olduğunda, ödemesi
    # tamamlanmamış kart siparişinde müşteri iptal isteyince satır "junk" sayılıp ana listeden
    # GİZLENİYORDU; yalnız isimle arayınca (süzgeç atlanır) mor satır görünüyordu (kullanıcı
    # bildirimi, W11886). Personel talebi göremezse sipariş ne iptal edilir ne kapatılır.
    _fulfilled_status = ["confirmed", "processing", "preparing", "shipped", "delivered",
                         "completed", "undelivered", "cancelled", "cancel_requested"]
    _junk_cond = {"$and": [
        _web_cond,
        {"payment_status": {"$nin": _settled_pay}},
        {"payment_method": {"$nin": _offline_pm}},
        {"status": {"$nin": _fulfilled_status}},
    ]}
    if _show_hidden:
        # Gizlenenleri göster: ödeme "junk" (tamamlanmamış web kart denemesi) süzgeci de UYGULANMAZ
        # → daha önce hiçbir görünümde çıkmayan siparişler de listeye gelir.
        pass
    elif payment_view == "unpaid":
        query.setdefault("$and", []).append(_junk_cond)
    elif _specific_search:
        # Belirli sipariş araması → ödeme başarısız/junk dahil BUL (no ile aranan sipariş kaybolmasın).
        pass
    elif status:
        # KULLANICI AÇIKÇA BİR DURUM SEÇTİ → o durumdaki siparişler GİZLENMEZ. Eskiden
        # "Ödeme Alınamadı" (payment_failed) gibi bir durum seçilince junk süzgeci devreye girip
        # DAİMA boş liste dönüyordu: seçenek menüde var ama filtre hiçbir şey getirmiyordu.
        pass
    else:
        # VARSAYILAN ("all") + "valid": ödemesi hiç alınamamış (başarısız/bekleyen web kart
        # denemesi) siparişleri GİZLE. Böylece "başarısız ödeme panele yansımasın" — ekip bunları
        # 'ödeme alındı' sanıp yanlışlıkla PARA İADESİ yapmaz. (Ödeme alanı gerçekten alınanlar
        # payment_status='paid' olduğu için junk süzgecine takılmaz, listede kalır.)
        query.setdefault("$and", []).append({"$nor": [_junk_cond]})

    # PERF: find ve count'u paralel çalıştır (ardışık değil) + count'u kısa süre
    # önbellekle. Önceden find→count ardışıktı ve karmaşık filtrede toplam ~8 sn
    # sürüyordu; soğuk önbellekte paralel + cache çok daha hızlı.
    import asyncio as _aio
    import json as _json
    _sig = _json.dumps(query, sort_keys=True, default=str)
    _now = time.monotonic()
    _cached = _ORDERS_COUNT_CACHE.get(_sig)

    async def _do_find():
        # created_at KARIŞIK TİPTE olabilir: bazı eski/migrasyon kayıtları BSON Date,
        # çoğu ISO string. MongoDB karışık tipte önce TİP'e göre sıralar → tüm Date tipli
        # kayıtlar (ör. Ticimax'tan taşınanlar) string tarihlerin üstüne çıkar ve liste
        # gerçek tarih sırasını kaybeder. Bu yüzden created_at'i $convert ile Date'e
        # normalize edip ona göre sıralarız (EN YENİ EN ÜSTTE), tip ne olursa olsun.
        # TZ TUTARLILIĞI (sıralama = ekrandaki TR saati): Pazaryeri (Trendyol/HB/Temu/N11/Amazon)
        # siparişlerinin created_at'i TR duvar-saatiyle "+00:00" etiketli saklanıyor; site
        # siparişleri ise GERÇEK UTC. Ham UTC'ye göre sıralayınca site siparişi olduğundan eski
        # sanılıp yanlış yere (aşağı) düşüyordu. Sıralamayı ekranla aynı TR çerçevesine getiririz:
        # pazaryeri olduğu gibi, site siparişine +3 saat → liste görünen saate göre "en yeni en üstte".
        _mkt_platforms = ["trendyol", "hepsiburada", "temu", "n11", "amazon"]
        _pipe = [
            {"$match": query},
            {"$addFields": {"_sort_dt": {"$let": {
                "vars": {
                    "d": {"$convert": {"input": "$created_at", "to": "date", "onError": None, "onNull": None}},
                    "mkt": {"$in": [
                        {"$toLower": {"$ifNull": ["$platform", {"$ifNull": ["$marketplace", ""]}]}},
                        _mkt_platforms,
                    ]},
                    # Trendyol API siparişi: marketplace_order_date artık GERÇEK UTC
                    # (ty_date_utc_fix + _ms_to_iso). Site siparişi gibi +3 ile TR'ye
                    # alınır; yoksa yeni Trendyol siparişleri 3 saat eski sanılıp
                    # daha önce gelmiş web siparişlerinin ALTINA düşüyordu.
                    "tyd": {"$cond": [
                        {"$eq": [{"$toLower": {"$ifNull": ["$platform", ""]}}, "trendyol"]},
                        {"$convert": {"input": {"$ifNull": ["$marketplace_order_date", None]},
                                      "to": "date", "onError": None, "onNull": None}},
                        None,
                    ]},
                },
                "in": {"$cond": [
                    {"$ne": ["$$tyd", None]},
                    {"$add": ["$$tyd", 3 * 60 * 60 * 1000]},
                    {"$cond": [
                        {"$or": ["$$mkt", {"$eq": ["$$d", None]}]},
                        "$$d",
                        {"$add": ["$$d", 3 * 60 * 60 * 1000]},
                    ]},
                ]},
            }}}},
            {"$sort": {"_sort_dt": -1, "created_at": -1, "_id": -1}},
            {"$skip": skip},
            {"$limit": limit},
            # Dekont BAYTI listeye taşınmaz (BL-040; görüntüleme /payment-receipt ucundan).
            {"$project": {"_id": 0, "_sort_dt": 0,
                          "payment_receipt.data_b64": 0, "payment_receipt.data_enc": 0}},
        ]
        return await db.orders.aggregate(_pipe, allowDiskUse=True).to_list(limit)

    if _cached and (_now - _cached[1] < _ORDERS_COUNT_TTL):
        orders = await _do_find()
        total = _cached[0]
    else:
        orders, total = await _aio.gather(_do_find(), db.orders.count_documents(query))
        _ORDERS_COUNT_CACHE[_sig] = (total, _now)
        # cache'i sınırla (sınırsız büyümesin)
        if len(_ORDERS_COUNT_CACHE) > 200:
            _oldest = min(_ORDERS_COUNT_CACHE.items(), key=lambda kv: kv[1][1])[0]
            _ORDERS_COUNT_CACHE.pop(_oldest, None)

    # Pazaryeri (Trendyol vb.) kalemlerinde görsel yoksa barkodla yerel üründen eşle.
    # Barkodlar Trendyol ile aynı olduğundan doğrudan eşleşir; mevcut siparişler için
    # re-sync gerekmez (okuma anında zenginleştirilir).
    try:
        _need = set()
        for _o in orders:
            for _it in (_o.get("items") or []) + (_o.get("lines") or []):
                if not _it.get("image"):
                    _bc = str(_it.get("barcode") or "").strip()
                    if _bc:
                        _need.add(_bc)
        if _need:
            _need_list = list(_need)
            _prods = await db.products.find(
                {"$or": [{"barcode": {"$in": _need_list}}, {"variants.barcode": {"$in": _need_list}}]},
                {"_id": 0, "barcode": 1, "image": 1, "images": 1, "thumbnail": 1, "variants": 1},
            ).to_list(1000)

            def _first_img(pr):
                for im in (pr.get("images") or []):
                    if isinstance(im, str) and im:
                        return im
                    if isinstance(im, dict):
                        u = im.get("url") or im.get("src") or im.get("image")
                        if u:
                            return u
                return pr.get("image") or pr.get("thumbnail") or ""

            _img_by_bc = {}
            for _pr in _prods:
                _img = _first_img(_pr)
                if not _img:
                    continue
                _pb = str(_pr.get("barcode") or "").strip()
                if _pb:
                    _img_by_bc.setdefault(_pb, _img)
                for _v in (_pr.get("variants") or []):
                    _vb = str(_v.get("barcode") or "").strip()
                    if _vb:
                        _img_by_bc.setdefault(_vb, _img)

            if _img_by_bc:
                for _o in orders:
                    for _it in (_o.get("items") or []) + (_o.get("lines") or []):
                        if not _it.get("image"):
                            _bc = str(_it.get("barcode") or "").strip()
                            if _bc and _img_by_bc.get(_bc):
                                _it["image"] = _img_by_bc[_bc]
    except Exception as _e:
        logger.warning(f"order image enrich skipped: {_e}")

    # BL-040: iade IBAN'ı — iadeyi ödeyen yetkili personele (İptaller ekranı) tam, diğerlerine maskeli.
    await present_refund_bank_info_in(orders, current_user)
    # KVKK: listede TCKN/VKN yalnız son 2 hane (tam değer yalnız tek sipariş detayında).
    _pii_mask_many(orders)
    # KVKK: listede telefon "+90 *** *** ** 33", açık adres "••••••" (il/ilçe açık). Tam değer:
    # GET /orders/{id}/contact (orders.view, sayaçlı + denetimli).
    _contact_mask_many(orders)

    # Gizlenen sipariş özeti (kullanıcı isteği): kullanıcı 'Gizlenenleri göster'i açmak zorunda
    # kalmasın — aynı filtre aralığında İadeler/İptaller sayfalarına giden sipariş sayıları
    # yanıtla döner, panel bunları ilgili sayfalara linkleyerek gösterir.
    hidden_summary = None
    if _default_hidden:
        try:
            _hsig = "hs:" + _sig
            _hc = _ORDERS_COUNT_CACHE.get(_hsig)
            if _hc and (_now - _hc[1] < _ORDERS_COUNT_TTL):
                hidden_summary = _hc[0]
            else:
                _base = {k: v for k, v in query.items() if k not in ("status", "$and")}
                _iade_q = {**_base, "status": {"$in": [
                    "returned", "refunded", "return_approved", "return_requested",
                    "return_in_transit", "return_rejected", "partial_refunded"]}}
                _iptal_q = {**_base, "status": "cancelled"}
                _iade_n, _iptal_n = await _aio.gather(
                    db.orders.count_documents(_iade_q),
                    db.orders.count_documents(_iptal_q))
                hidden_summary = {"iade": _iade_n, "iptal": _iptal_n}
                _ORDERS_COUNT_CACHE[_hsig] = (hidden_summary, _now)
        except Exception as _he:
            logger.warning(f"hidden_summary hesaplanamadı: {_he}")

    return {
        "orders": orders,
        "total": total,
        "page": page,
        "pages": (total + limit - 1) // limit,
        "hidden_summary": hidden_summary,
    }

_ALLOWED_PAYMENT_METHODS = {
    "credit_card", "card", "kredi_karti", "kart", "iyzico", "creditcard",
    "bank_transfer", "havale", "eft", "havale_eft", "banka_havale",
    "cash_on_delivery", "kapida",
}


def _allowed_payment_method(v) -> str:
    """Site siparişinde yalnız kart / havale-EFT / kapıda ödeme (denetim 2026-09-29): bilinmeyen bir
    yöntem ('marketplace' vb.) siparişi 'Ödeme Bekleniyor' yerine 'pending' açıp admin bildirimi
    tetikliyordu (CLAUDE.md değişmez 2). Bilinmeyen değer → 400."""
    pm = str(v or "credit_card").strip().lower()
    if pm not in _ALLOWED_PAYMENT_METHODS:
        raise HTTPException(status_code=400, detail="Geçersiz ödeme yöntemi")
    return pm


def _order_public_summary(order: dict) -> dict:
    """Kanıtsız (yalnız sipariş numarasıyla) erişimde dönen en küçük görünüm — içerik, tutar,
    adres, tarih, kargo takip bilgisi YOK."""
    return {"order_number": order.get("order_number"), "status": order.get("status"),
            "payment_status": order.get("payment_status"), "payment_method": order.get("payment_method"),
            "payment_notified": bool(order.get("payment_notified")), "verification_required": True}


async def _order_view_authorized(request, order: dict) -> bool:
    """Denetim #27: sipariş görünümü için sahiplik kanıtı.
    1) X-Order-Token: siparişi oluşturan istemciye verilen yetki (payment.issue_payment_capability),
    2) doğrulanmış oturum: siparişin sahibi ya da personel,
    3) X-Order-Contact: siparişteki e-posta ya da telefonun tamamı (sipariş başına deneme kilidiyle)."""
    tok = (request.headers.get("x-order-token") or "").strip()
    if tok and order.get("pay_cap_hashes"):
        import hmac as _hm
        from .payment import _pay_cap_hash
        _h = _pay_cap_hash(tok)
        if any(_hm.compare_digest(_h, str(x)) for x in order.get("pay_cap_hashes") or []):
            return True
    auth = request.headers.get("authorization") or ""
    if auth[:7].lower() == "bearer ":
        try:
            from .deps import _decode_jwt_strict, request_member_ok, optional_admin
            _uid = str(_decode_jwt_strict(auth[7:].strip()).get("user_id") or "")
            if _uid and request_member_ok(request) and str(order.get("user_id") or "") == _uid:
                return True
            if (await optional_admin(request)) is not None:
                return True
        except Exception:
            pass
    contact = (request.headers.get("x-order-contact") or "").strip()
    if contact:
        _em, _ph = (contact, "") if "@" in contact else ("", contact)
        await _guest_verify_or_raise(order, _em, _ph)   # eşleşmezse 403, kilitliyse 429
        return True
    return False


@router.get("/by-number/{order_number}")
@(limiter.limit("30/minute") if limiter else (lambda f: f))
@(limiter.limit("600/minute", key_func=global_rate_key) if limiter else (lambda f: f))
async def get_order_by_number(order_number: str, request: Request):
    """Sipariş numarasıyla siparişi getir (public — ödeme sonrası başarı sayfası için).
    PII maskelenir (telefon/email son kısımları gizlenir, adres detayı sınırlanır)."""
    order = await db.orders.find_one(
        {"order_number": order_number},
        {"_id": 0, "admin_notes": 0, "payment_id": 0, "customer_ip": 0, "user_agent": 0,
         "billing_address": 0, "billing_info": 0, "attribution": 0, "payment_receipt": 0,
         "iyzico_retrieve_response": 0, "iyzico_init_response": 0, "iyzico_response": 0}
    )
    if not order:
        raise HTTPException(status_code=404, detail="Sipariş bulunamadı")
    # Denetim #27: sıralı sipariş numarası TEK BAŞINA içerik/tutar/konum göstermez — sahiplik kanıtı
    # (sahibin oturumu, siparişi açan istemcinin sipariş anahtarı ya da siparişteki e-posta/telefon)
    # yoksa yalnız durum özeti döner.
    if not await _order_view_authorized(request, order):
        return _order_public_summary(order)
    order.pop("user_id", None)
    order.pop("pay_cap_hashes", None)

    def _mask_email(e: str) -> str:
        if not e or "@" not in e:
            return ""
        local, _, domain = e.partition("@")
        return (local[:2] + "***@" + domain) if len(local) > 2 else "***@" + domain

    def _mask_phone(p: str) -> str:
        # Denetim #17: herkese açık uçta telefonun HİÇBİR hanesi gösterilmez — son 2 hane,
        # dekont yüklemedeki "son 4 hane" kanıtını 100 denemeye indiriyordu.
        return "+90 *** *** ** **" if str(p or "").strip() else ""

    def _mask_name(n: str) -> str:
        # Sıra numarası tahmin edilebilir olduğundan (enumeration) ad-soyad da maskelenir.
        n = str(n or "").strip()
        return (n[:1] + "***") if n else ""

    ship = order.get("shipping_address") or {}
    if ship:
        order["shipping_address"] = {
            "first_name": _mask_name(ship.get("first_name", "")),
            "last_name": _mask_name(ship.get("last_name", "")),
            "phone": _mask_phone(ship.get("phone", "")),
            "email": _mask_email(ship.get("email", "")),
            # KVKK: sipariş no ile (sahiplik kanıtı OLMADAN) erişilen uçta açık adres DÖNMEZ — yalnız
            # il/ilçe. Eskiden ilk 40 karakter dönüyordu (çoğu adresin tamamı).
            "address": "",
            "city": ship.get("city", ""),
            "district": ship.get("district", ""),
        }
    # GÜVENLİK: Sipariş no ENUMERATION edilebildiğinden üst-düzey PII de maskelenir/çıkarılır
    # (top-level phone/email/customer_name + kargo takip no sızmasın).
    if order.get("phone"):
        order["phone"] = _mask_phone(order.get("phone", ""))
    if order.get("email"):
        order["email"] = _mask_email(order.get("email", ""))
    for _pk in ("customer_name", "user_email", "user_phone", "cargo_tracking",
                "cargo_tracking_number", "tracking_number", "billing_info", "customer_ip"):
        order.pop(_pk, None)
    # GÜVENLİK (denetim 2026-09-29, HIGH): numara sıralı → herkes tarayabilir. Kara listede olmayan
    # hassas alanlar da sızıyordu: iade IBAN'ı, fatura linki (ad/adres/TCKN), Trendyol TCKN, ödeme
    # token'ı, notlar. Anahtar adına göre temizlenir (sayfaların kullandığı alanlar korunur).
    _SENS = ("iban", "refund_bank", "identity", "tckn", "vkn", "tax_number", "invoice_pdf", "invoice_link",
             "invoice_url", "invoice_provider", "invoice_web_key", "iyzico", "token", "reconcile", "receipt",
             "tracking_link", "gonderi", "barcode", "notes", "gift_note", "admin", "phone_raw", "email_raw",
             # commerce-3: iade/çift-tahsilat kalemlerindeki iyzico ödeme kimlikleri sızmasın.
             "payment_id", "paymentid")
    # GÜVENLİK (commerce-3, HIGH): siparişte kullanılan HEDİYE ÇEKİ KODU hamiline yazılı bir
    # değerdir (kalan bakiye kodu bilen herkesçe harcanır). Sıralı numarayla taranan bu public
    # uçta kod ASLA dönmez; yalnız kullanılan tutar (sayfa gösterimi için) kalır.
    _gc_pub = order.get("gift_card")
    if isinstance(_gc_pub, dict):
        order["gift_card"] = {"amount": _gc_pub.get("amount"), "refunded": bool(_gc_pub.get("refunded"))}
    elif _gc_pub is not None:
        order.pop("gift_card", None)
    def _scrub(obj):
        if isinstance(obj, dict):
            for k in list(obj.keys()):
                kl = str(k).lower()
                if any(t in kl for t in _SENS):
                    obj.pop(k, None)
                else:
                    _scrub(obj[k])
        elif isinstance(obj, list):
            for x in obj:
                _scrub(x)
    _scrub(order)
    # KVKK: kalan üst-düzey / kapsayıcı telefon-adres alanları da (customer_phone, buyer.*, pazaryeri
    # shipmentAddress …) maskelenir — sahiplik kanıtı olmadan tam telefon/adres DÖNMEZ.
    _contact_mask(order)
    # İade uygunluğu delivered_at'e bağlı; kargo senkronu teslimatı raporlamadıysa (MNG status 0'da
    # takılı) müşteri iade açamıyordu → varsayılan-teslim fallback ile delivered_at'i doldur.
    try:
        if not order.get("delivered_at"):
            _eff, _presumed = await _effective_delivered_at(order)
            if _eff:
                order["delivered_at"] = _eff
                order["delivered_presumed"] = bool(_presumed)
    except Exception:
        pass
    return order


@router.get("/deleted")
async def list_deleted_orders(
    page: int = 1,
    limit: int = 50,
    search: str = "",
    current_user: dict = Depends(require_permission("orders.view")),
):
    """Silinen (arşivlenen) siparişler — orders_deleted koleksiyonundan, en yeni üstte."""
    q = {}
    if search:
        # Ana sipariş aramasıyla AYNI kapsam (pazaryeri paket no, fatura, kargo, ad-soyad,
        # telefon, ürün vb.) → silinen bir Trendyol siparişi paket no ile de bulunur.
        _ors = _order_search_or(search)
        if _ors:
            q["$or"] = _ors
    try:
        limit = max(1, min(int(limit), 200))
        page = max(1, int(page))
    except Exception:
        limit, page = 50, 1
    skip = (page - 1) * limit
    total = await db.orders_deleted.count_documents(q)
    items = await db.orders_deleted.find(q, {"_id": 0, "payment_receipt.data_b64": 0, "payment_receipt.data_enc": 0}).sort("deleted_at", -1).skip(skip).limit(limit).to_list(limit)
    await present_refund_bank_info_in(items, current_user, full=False)  # BL-040: maskeli
    _pii_mask_many(items)  # KVKK: TCKN/VKN maskeli
    _contact_mask_many(items)  # KVKK: telefon / açık adres maskeli
    return {"orders": items, "total": total, "page": page, "limit": limit}


@router.get("/{order_id}/iyzico-verify")
async def iyzico_verify_order_payment(order_id: str, current_user: dict = Depends(require_permission("orders.view"))):
    """Siparişin ödemesini iyzico'ya CANLI sorar — SALT OKUNUR, siparişi DEĞİŞTİRMEZ.

    Neden (W11805): müşteri kartla iki kez denemiş; ilk deneme 3DS'te yarıda kalmış (tahsilat
    YOK), ikincisi başarılı olmuş. Personel iyzico'nun işlem listesinde YANLIŞ işlemi (yarıda
    kalanı) siparişle eşleştirip "para çekilmemiş" sanmış, ürün iade alınmış ve iade elle
    'ödendi' işaretlenmiş — oysa 1.899 TL tahsil edilmişti. Bu uç, siparişin KENDİ ödeme
    numarasını iyzico'ya sorar; eşleştirme tahmine kalmaz."""
    o = await db.orders.find_one(
        {"id": order_id},
        {"_id": 0, "id": 1, "order_number": 1, "total": 1, "status": 1, "payment_status": 1,
         "payment_method": 1, "refunds": 1, "manual_return": 1, "iyzico_payment_id": 1,
         "payment_id": 1, "iyzico_retrieve_response": 1})
    if not o:
        raise HTTPException(status_code=404, detail="Sipariş bulunamadı")
    pid = str(o.get("iyzico_payment_id") or o.get("payment_id")
              or (o.get("iyzico_retrieve_response") or {}).get("paymentId") or "").strip()
    if not pid:
        return {"ok": False, "sorgulandi": False,
                "mesaj": "Bu siparişte iyzico ödeme numarası yok (havale/kapıda/pazaryeri olabilir)."}
    try:
        import json as _json
        import httpx as _httpx
        from .payment import (_get_iyzico_settings as _gs, _v2_headers as _vh,
                              PAYMENT_DETAIL_PATH as _pd)
        st = await _gs()
        body = {"locale": "tr", "conversationId": str(order_id), "paymentId": pid}
        bs = _json.dumps(body, separators=(",", ":"), ensure_ascii=False)
        async with _httpx.AsyncClient(timeout=25) as c:
            r = await c.post(f"{st['base_url']}{_pd}", content=bs.encode("utf-8"),
                             headers=_vh(st["api_key"], st["api_secret"], _pd, bs))
        if not r.headers.get("content-type", "").startswith("application/json"):
            return {"ok": False, "sorgulandi": False,
                    "mesaj": f"iyzico beklenmeyen yanıt verdi (HTTP {r.status_code}). Tekrar deneyin."}
        d = r.json()
    except Exception as e:
        logger.warning(f"[iyzico-verify] hata order={order_id}: {e}")
        return {"ok": False, "sorgulandi": False, "mesaj": f"iyzico'ya ulaşılamadı: {str(e)[:120]}"}

    _ps = str(d.get("paymentStatus") or "").upper()
    tahsil = _ps == "SUCCESS" and bool(d.get("paymentId"))
    _iade_kayitlari = [x for x in (o.get("refunds") or [])
                       if str((x or {}).get("status") or "") == "done"]
    _iade_tutari = round(sum(float(x.get("amount") or x.get("net_refund") or 0)
                             for x in _iade_kayitlari), 2)
    _iade_durumlari = ("refunded", "returned", "iade", "iade_edildi")
    uyari = ""
    if tahsil and (str(o.get("status") or "").lower() in _iade_durumlari
                   or o.get("manual_return")) and not _iade_kayitlari:
        uyari = ("Bu siparişte karttan TAHSİLAT VAR ama iyzico üzerinden iade kaydı YOK. "
                 "İade elle işaretlenmişse müşteriye para iadesi yapılmamış olabilir.")
    elif not tahsil and str(o.get("payment_status") or "") == "paid":
        uyari = ("Sipariş 'ödendi' görünüyor ancak iyzico bu ödeme için başarı bildirmiyor. "
                 "Muhasebeye bildirin.")
    return {
        "ok": True, "sorgulandi": True, "tahsil_edildi": tahsil,
        "paymentId": pid,
        "iyzico_durum": d.get("paymentStatus") or d.get("status"),
        "tahsil_edilen": d.get("paidPrice"),
        "siparis_tutari": o.get("total"),
        "auth_code": d.get("authCode"), "host_reference": d.get("hostReference"),
        "taksit": d.get("installment"), "fraud_durumu": d.get("fraudStatus"),
        "hata_mesaji": str(d.get("errorMessage") or "")[:160],
        "iyzico_iade_kaydi": len(_iade_kayitlari), "iade_edilen_tutar": _iade_tutari,
        "uyari": uyari,
    }


@router.get("/payment-trace")
async def payment_trace(
    phone: str = Query(..., description="Müşteri telefonu (herhangi bir biçimde)"),
    days: int = Query(10, ge=1, le=180),
    current_user: dict = Depends(require_permission("orders.view")),
):
    """Bir telefon numarasının son N gündeki TÜM sipariş + kart ödeme denemelerini döndürür.
    'Kartımdan çekildi ama teslim olmadı / hala çekiliyor' vakalarını numaradan tespit için.
    Kayıtlar: orders (payment_status/needs_reconciliation/paymentId) + card_attempts (PII'siz:
    IP/BIN/durum/zaman). Ham kart / tam PII DÖNMEZ."""
    digits = re.sub(r"\D", "", phone or "")
    tail = digits[-10:]
    if len(tail) < 7:
        raise HTTPException(status_code=400, detail="Geçerli bir telefon numarası girin")
    since_iso = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    q = {
        "$or": [
            {"phone": {"$regex": tail}},
            {"shipping_address.phone": {"$regex": tail}},
            {"customer_phone": {"$regex": tail}},
            {"billing_address.phone": {"$regex": tail}},
        ],
        "created_at": {"$gte": since_iso},
    }
    orders = await db.orders.find(
        q,
        {"_id": 0, "id": 1, "order_number": 1, "created_at": 1, "total": 1, "grand_total": 1,
         "payment_method": 1, "payment_status": 1, "status": 1, "needs_reconciliation": 1,
         "iyzico_payment_id": 1, "payment_id": 1, "cargo_status": 1, "tracking_number": 1},
    ).sort("created_at", -1).to_list(300)

    out = []
    total_success = 0
    total_fail = 0
    for o in orders:
        oid = o.get("id")
        attempts = await db.card_attempts.find(
            {"order_id": oid}, {"_id": 0, "status": 1, "bin": 1, "created_at": 1, "ip": 1},
        ).sort("ts", 1).to_list(100)
        succ = sum(1 for a in attempts if a.get("status") == "success")
        fail = len(attempts) - succ
        total_success += succ
        total_fail += fail
        out.append({
            "order_number": o.get("order_number"),
            "created_at": o.get("created_at"),
            "total": o.get("grand_total") or o.get("total"),
            "payment_method": o.get("payment_method"),
            "payment_status": o.get("payment_status"),
            "status": o.get("status"),
            "needs_reconciliation": bool(o.get("needs_reconciliation")),
            "iyzico_payment_id": o.get("iyzico_payment_id") or o.get("payment_id") or "",
            "cargo_status": o.get("cargo_status") or "",
            "tracking_number": o.get("tracking_number") or "",
            "attempts_success": succ,
            "attempts_fail": fail,
            "attempts": attempts,
        })
    return {
        "phone_tail": tail,
        "days": days,
        "order_count": len(out),
        "summary": {"attempts_success": total_success, "attempts_fail": total_fail},
        "orders": out,
        "note": "card_attempts PII'siz (IP/BIN/durum/zaman). Gerçek banka çekimi kesin kaynağı iyzico panelidir; buradaki 'attempts_success' başarılı 3DS denemesini, orders.payment_status='paid' onaylı ödemeyi gösterir.",
    }


@router.get("/admin/havale-sweep")
async def havale_sweep_preview(current_user: dict = Depends(require_permission("orders.view"))):
    """Havale/EFT 72 saat kuralı — ÖNİZLEME: kuralı aşmış, ödenmemiş/onaylanmamış (Ödeme Bildirimi
    dahil) bekleyen havale siparişleri (1 Haziran 2026'dan itibaren) — iptal edilecekler."""
    import business_rules as _BR
    from scheduler import havale_unpaid_query, HAVALE_METHOD_RX
    hrs = int(await _BR.get_rule(db, "order.havale_cancel_hours", 72) or 72)
    cutoff_dt = datetime.now(timezone.utc) - timedelta(hours=hrs)
    proj = {"_id": 0, "id": 1, "order_number": 1, "created_at": 1, "status": 1, "payment_status": 1,
            "payment_method": 1, "total": 1, "shipping_address.first_name": 1, "shipping_address.last_name": 1}
    due = [o async for o in db.orders.find(havale_unpaid_query(cutoff_dt), proj).sort("created_at", 1).limit(500)]
    notified = [o for o in due if o.get("status") == "payment_notified"]
    def _row(o):
        sa = o.get("shipping_address") or {}
        return {"order_number": o.get("order_number"), "created_at": str(o.get("created_at"))[:16],
                "status": o.get("status"), "payment_status": o.get("payment_status"),
                "payment_method": o.get("payment_method"), "total": o.get("total"),
                "customer": f"{sa.get('first_name', '')} {sa.get('last_name', '')}".strip()}
    health = await db.settings.find_one({"id": "havale_sweep_health"}, {"_id": 0}) or {}
    return {"rule_hours": hrs, "cutoff": cutoff_dt.isoformat(), "sweep_health": health,
            "will_cancel": [_row(o) for o in due], "will_cancel_count": len(due),
            "payment_notified_included": [_row(o) for o in notified], "payment_notified_count": len(notified),
            "since": "2026-06-01"}


@router.post("/admin/havale-sweep")
async def havale_sweep_run(current_user: dict = Depends(require_permission("orders.edit"))):
    """Havale/EFT 72 saat kuralını ŞİMDİ uygula (geçmişe dönük): süresi dolmuş ödenmemiş
    havale siparişleri iptal + stok iadesi + müşteriye iptal bildirimi (cron ile aynı iş)."""
    from scheduler import auto_cancel_unpaid_havale_orders
    n = await auto_cancel_unpaid_havale_orders()
    logger.info(f"[havale-sweep] elle çalıştırıldı ({current_user.get('email', '')}): {n} sipariş iptal")
    return {"success": True, "cancelled": n}


@router.get("/{order_id}/customer-history")
async def order_customer_history(order_id: str, current_user: dict = Depends(require_permission("orders.view"))):
    """Sipariş detayı için: AYNI MÜŞTERİNİN diğer siparişleri (üye id / e-posta / telefon eşleşmesi)
    ve hangilerini iptal ettiği/iade ettiği. Panelde eski 'Sipariş Kaynağı' + 'Müşteri Yolculuğu'
    bloklarının yerini alır. En yeni 60 sipariş + özet sayaçlar."""
    import re as _re
    order = await db.orders.find_one({"id": order_id}, {"_id": 0})
    if not order:
        raise HTTPException(status_code=404, detail="Sipariş bulunamadı")
    ship = order.get("shipping_address") or {}
    ors = []
    if order.get("user_id"):
        ors.append({"user_id": order["user_id"]})
    emails = {str(x or "").strip().lower() for x in (
        ship.get("email"), order.get("email"), order.get("customer_email"),
        (order.get("billing_address") or {}).get("email"))}
    emails.discard("")
    for em in emails:
        rx = {"$regex": f"^{_re.escape(em)}$", "$options": "i"}
        ors += [{"shipping_address.email": rx}, {"email": rx}, {"customer_email": rx}]
    phones = set()
    for ph in (ship.get("phone"), order.get("phone"), order.get("customer_phone")):
        d = _re.sub(r"\D", "", str(ph or ""))
        if len(d) >= 10:
            phones.add(d[-10:])
    for d in phones:
        rx = {"$regex": f"{d}$"}
        ors += [{"shipping_address.phone": rx}, {"phone": rx}]
    if not ors:
        return {"orders": [], "summary": {"total": 0}, "match": "none"}
    _cancel_like = {"cancelled", "cancel_refunded"}
    _return_like = {"returned", "refunded", "partial_refunded", "return_requested", "return_approved", "return_in_transit"}
    proj = {"_id": 0, "id": 1, "order_number": 1, "created_at": 1, "status": 1, "payment_status": 1,
            "payment_method": 1, "total": 1, "platform": 1, "marketplace": 1, "items": 1,
            "cancel_reason": 1, "cancelled_at": 1, "auto_cancelled": 1, "cancel_source": 1,
            "cancelled_by": 1, "return_request": 1, "user_id": 1, "partial_cancelled": 1}
    rows = []
    async for o in db.orders.find({"$or": ors, "id": {"$ne": order_id}}, proj).sort("created_at", -1).limit(60):
        st = str(o.get("status") or "")
        rows.append({
            "id": o.get("id"), "order_number": o.get("order_number"), "created_at": o.get("created_at"),
            "status": st, "payment_status": o.get("payment_status"), "payment_method": o.get("payment_method"),
            "total": _round2(o.get("total") or 0), "platform": o.get("platform") or o.get("marketplace") or "site",
            "items_count": sum(int((it or {}).get("quantity") or 1) for it in (o.get("items") or [])),
            "item_names": [str((it or {}).get("name") or (it or {}).get("product_name") or "") for it in (o.get("items") or [])][:4],
            # TAM KALEM LİSTESİ (kullanıcı isteği): ürün adı + beden + renk + adet + birim fiyat + barkod.
            "items": [{
                "name": str((it or {}).get("name") or (it or {}).get("product_name") or ""),
                # Eski/ithal siparişlerde beden farklı anahtarlarda olabilir → hepsine bak.
                "size": str((it or {}).get("size") or (it or {}).get("variant_size") or (it or {}).get("beden")
                            or ((it or {}).get("variant") or {}).get("size") if isinstance((it or {}).get("variant"), dict) else
                            ((it or {}).get("size") or (it or {}).get("variant_size") or (it or {}).get("beden") or "")).strip(),
                "color": str((it or {}).get("color") or (it or {}).get("variant_color") or (it or {}).get("renk") or "").strip(),
                "quantity": int((it or {}).get("quantity") or 1),
                "price": _round2((it or {}).get("price") or (it or {}).get("unit_price") or 0),
                "barcode": str((it or {}).get("barcode") or ""),
                "stock_code": str((it or {}).get("stock_code") or (it or {}).get("sku") or ""),
            } for it in (o.get("items") or []) if isinstance(it, dict)][:40],
            "cancelled": st in _cancel_like,
            "cancel_reason": o.get("cancel_reason") or "",
            "cancelled_at": o.get("cancelled_at") or "",
            "auto_cancelled": bool(o.get("auto_cancelled")),
            "cancel_source": o.get("cancel_source") or ("otomatik" if o.get("auto_cancelled") else ""),
            "partial_cancelled": bool(o.get("partial_cancelled")),
            "returned": st in _return_like,
            "return_status": (o.get("return_request") or {}).get("status") or "",
            "same_member": bool(order.get("user_id")) and o.get("user_id") == order.get("user_id"),
        })
    n_cancel = sum(1 for r in rows if r["cancelled"])
    n_ret = sum(1 for r in rows if r["returned"])
    n_ok = sum(1 for r in rows if not r["cancelled"] and not r["returned"] and r["status"] not in ("awaiting_payment", "payment_failed", "failed", "pending"))
    return {
        "orders": rows,
        "summary": {"total": len(rows), "cancelled": n_cancel, "returned": n_ret, "completed": n_ok,
                    "net_spent": _round2(sum(r["total"] for r in rows if not r["cancelled"] and not r["returned"]
                                             and r["status"] not in ("awaiting_payment", "payment_failed", "failed", "pending")))},
        "match": {"user_id": bool(order.get("user_id")), "emails": sorted(emails), "phones": len(phones)},
    }


@router.get("/{order_id}")
async def get_order(
    order_id: str,
    request: Request,
    current_user: dict = Depends(get_current_user)
):
    """Get single order"""
    # K3: Bu uç TAM (maskesiz) sipariş döndürür — dekont, vergi no, adres, iyzico yanıtı.
    # Anonim erişim YASAK; aksi halde sıralı sipariş numaralarıyla tüm müşteri veritabanı
    # sıyrılabiliyordu. Misafir/başarı sayfası maskeli `/orders/by-number/{n}` ucunu kullanır.
    if not current_user:
        raise HTTPException(status_code=401, detail="Giriş yapmanız gerekiyor")

    order = await db.orders.find_one({"id": order_id}, {"_id": 0})
    if not order:
        raise HTTPException(status_code=404, detail="Sipariş bulunamadı")

    # Admin tümünü görür; normal kullanıcı yalnızca kendi siparişini.
    # ID3-02: is_admin BAYRAĞI tek başına yetmez — liste/olaylar uçlarıyla AYNI yetki (orders.view)
    # ve require_admin'in tüm kontrolleri (gerçek personel, aktif, rol, token_version, boşta kalma)
    # şarttır. Aksi halde orders.view yetkisi olmayan (ör. yalnız ürün rolü) ya da rolü alınmış
    # personel herhangi bir siparişin TAM (maskesiz, vergi no açık) kaydını okuyabiliyordu.
    _staff_view = False
    if current_user.get("is_admin"):
        from .deps import optional_admin, get_effective_permissions
        _staff = await optional_admin(request)
        if _staff:
            _sp = await get_effective_permissions(_staff)
            _staff_view = "*" in _sp or "orders.view" in _sp
    if not _staff_view:
        if order.get("user_id") != current_user.get("id"):
            await note_policy_denial(current_user, "order.read", order=order, request=request)
            raise HTTPException(status_code=403, detail="Bu siparişi görüntüleme yetkiniz yok")
        # GÜVENLİK: sahibine bile personel-iç/pazarlama/ham-gateway alanlarını gösterme
        # (admin_notes = risk/dolandırıcılık notları). Public by-number ile tutarlı.
        for _k in ("admin_notes", "customer_ip", "user_agent", "attribution", "payment_id",
                   "iyzico_retrieve_response", "iyzico_init_response", "iyzico_response"):
            order.pop(_k, None)

    # BL-040: dekont baytı yanıtta taşınmaz; iade IBAN'ı müşteriye/yetkisiz personele maskeli.
    _strip_receipt_payload(order)
    if _staff_view:
        await present_refund_bank_info_in([order], current_user)
        # KVKK: TCKN/VKN — personel sipariş DETAYINDA (fatura kontrolü/düzenleme) tam değer görür;
        # şifreli alan/hash yanıta konmaz.
        # PRIV07: açılan değer sayısı sayaca yazılır (eşik aşımında 429 yayılır → yanıt dönmez).
        await _note_pii_reveal(current_user, "tax_id", _count_sealed_tax_ids(order),
                               refs=[order.get("order_number") or order.get("id")])
        order = _pii_reveal_doc(order)
        # KVKK: personel detayında da telefon/açık adres MASKELİ; "Göster" → GET /orders/{id}/contact.
        _contact_mask(order)
        order["contact_masked"] = True
    else:
        await present_refund_bank_info_in([order], current_user, full=False)
        _pii_mask(order)  # müşteri kendi siparişinde yalnız son 2 hane

    order["items"] = await _enrich_items_with_products(order.get("items") or [])
    # Kısmi/tam iade dökümü: hangi kalem iade edildi / müşteride kaldı (ayrı tutarlarla).
    # Yalnız iade kaydı olan siparişlerde alan eklenir; hata olsa bile detay bozulmasın.
    try:
        _rb = await _order_return_breakdown(order)
        if _rb:
            order["return_breakdown"] = _rb
    except Exception:
        pass
    return order


@router.get("/{order_id}/contact")
async def reveal_order_contact(order_id: str, request: Request,
                               current_user: dict = Depends(require_permission("orders.view"))):
    """Sipariş müşterisinin TAM telefon / adres bilgisi ("Göster"). Yalnız orders.view; her açma
    PRIV07 toplu çözme sayacına işlenir (saatte eşik aşımında uyarı + 1 saat engel) ve denetlenir.
    Kargo etiketi / fatura / pazaryeri senkronu bu uca ihtiyaç duymaz (sunucu tarafında okunur)."""
    order = await db.orders.find_one({"id": order_id}, {"_id": 0, "id": 1, "order_number": 1,
                                                        "shipping_address": 1, "billing_address": 1,
                                                        "invoice_address": 1, "delivery_address": 1,
                                                        "phone": 1, "customer_phone": 1, "user_phone": 1,
                                                        "buyer_phone": 1, "email": 1, "customer_email": 1})
    if not order:
        raise HTTPException(status_code=404, detail="Sipariş bulunamadı")
    from security.contact_pii import note_contact_reveal, order_contact_view, count_order_contact
    await note_contact_reveal(current_user, "order", order_id, count_order_contact(order),
                              request=request, entity_type="order")
    # Yalnız iletişim alanları; şifreli/hash'li alanlar (ör. VKN mühürü) yanıta konmaz.
    view = order_contact_view(order)
    view["id"] = order.get("id")
    view["order_number"] = order.get("order_number")
    return view


@router.get("/{order_id}/journey")
async def order_customer_journey(order_id: str, current_user: dict = Depends(require_permission("orders.view"))):
    """Müşteri yolculuğu / attribution HUNİSİ: müşteri hangi kaynaktan (organik / reklam-kampanya /
    influencer / pazaryeri) gelmiş, hangi kampanya/kreatif, referrer, giriş sayfası, kaç ziyaret sonra
    hangi ürünü satın almış. Sipariş attribution snapshot + oturum touches (ziyaret sırası) + influencer."""
    order = await db.orders.find_one(
        {"id": order_id},
        {"_id": 0, "attribution": 1, "click_ids": 1, "influencer_id": 1, "influencer_via": 1,
         "items": 1, "created_at": 1, "order_number": 1, "platform": 1, "marketplace": 1,
         "coupon_code": 1, "customer_ip": 1},
    )
    if not order:
        raise HTTPException(status_code=404, detail="Sipariş bulunamadı")
    attr = order.get("attribution") or {}
    clicks = {k: v for k, v in (order.get("click_ids") or {}).items() if v}

    # Oturum yolculuğu (ziyaret sırası) — en fazla 20 touch saklanır.
    touches = []
    sid = attr.get("session_id")
    if sid:
        sess = await db.attribution_sessions.find_one(
            {"session_id": sid},
            {"_id": 0, "touches": 1, "first_touch": 1, "last_touch": 1, "visit_count": 1, "device": 1},
        ) or {}
        touches = sess.get("touches") or []

    # Influencer
    influencer = None
    if order.get("influencer_id"):
        inf = await db.influencers.find_one(
            {"id": order["influencer_id"]},
            {"_id": 0, "name": 1, "handle": 1, "platform": 1, "follower_count": 1},
        )
        if inf:
            influencer = {"name": inf.get("name"), "handle": inf.get("handle"),
                          "platform": inf.get("platform"), "followers": inf.get("follower_count"),
                          "via": order.get("influencer_via")}

    # Reklam mı? (gclid/fbc/ttclid tıklama kimliği VEYA ücretli medium)
    _mkt = ["trendyol", "hepsiburada", "temu", "n11", "amazon"]
    is_marketplace = (order.get("platform") in _mkt) or (order.get("marketplace") in _mkt)
    medium = (attr.get("medium") or "").lower()
    is_ad = bool(clicks.get("gclid") or clicks.get("fbc") or clicks.get("ttclid") or clicks.get("fbclid")
                 or medium in ("cpc", "ppc", "paid", "paid_social", "display", "paidsocial"))
    ad_platform = ("Google Ads" if clicks.get("gclid") else
                   "Meta (FB/IG)" if (clicks.get("fbc") or clicks.get("fbclid")) else
                   "TikTok" if clicks.get("ttclid") else
                   (attr.get("source") or "").title() if is_ad else "")

    if is_marketplace:
        kind = "pazaryeri"
    elif influencer:
        kind = "influencer"
    elif is_ad:
        kind = "reklam"
    elif attr.get("channel") and attr.get("channel") not in ("direct", ""):
        kind = "organik"
    else:
        kind = "direct"

    return {
        "order_number": order.get("order_number"),
        "purchased_at": order.get("created_at"),
        "kind": kind,                       # pazaryeri | influencer | reklam | organik | direct
        "is_ad": is_ad,
        "ad_platform": ad_platform,
        "attribution": {
            "channel": attr.get("channel", ""),
            "source": attr.get("source", ""),
            "medium": attr.get("medium", ""),
            "campaign": attr.get("campaign", ""),     # kampanya id/adı (utm_campaign)
            "content": attr.get("content", ""),       # reklam kreatifi (utm_content)
            "term": attr.get("term", ""),
            "referrer": attr.get("referrer", ""),     # nereden geldi (ör. instagram.com)
            "landing_page": attr.get("landing_page", ""),  # ilk gördüğü sayfa/görsel
            "device": attr.get("device", ""),
            "touches_count": attr.get("touches_count", len(touches)),
        },
        "click_ids": clicks,
        "influencer": influencer,
        "coupon_code": order.get("coupon_code", ""),
        "touches": [
            {"ts": t.get("ts"), "channel": t.get("channel"), "source": t.get("utm_source"),
             "medium": t.get("utm_medium"), "campaign": t.get("utm_campaign"),
             "content": t.get("utm_content"), "referrer": t.get("referrer"),
             "landing_page": t.get("landing_page"), "device": t.get("device")}
            for t in touches
        ],
        "products": [
            {"name": it.get("name") or it.get("product_name") or it.get("productName") or "Ürün",
             "image": it.get("image"), "qty": it.get("quantity", 1),
             "product_id": it.get("product_id"), "slug": it.get("slug") or it.get("product_slug"),
             "barcode": it.get("barcode")}
            for it in (order.get("items") or [])
        ],
    }


@router.get("/{order_id}/events")
async def get_order_events(
    order_id: str,
    current_user: dict = Depends(require_permission("orders.view")),
):
    """Sipariş işlem geçmişi (Log) — en yeni en üstte."""
    events = await db.order_events.find(
        {"order_id": order_id}, {"_id": 0}
    ).sort("created_at", -1).to_list(500)
    return {"events": events, "count": len(events)}


def _order_request_fingerprint(order_data: dict, current_user) -> str:
    """İdempotency parmak izi (denetim 2026-10): aynı idempotency_key FARKLI bir sepet/adres/
    ödeme yöntemi/kupon/tutar ile tekrar gelirse eski sipariş SESSİZCE döndürülmemeli (müşteri
    değiştirdiği adresi/yöntemi kaybederdi). Yalnız istek girdisinden hesaplanır; sipariş
    statüsü/ödeme mantığına dokunmaz."""
    import hashlib as _hl
    import json as _js
    od = order_data if isinstance(order_data, dict) else {}
    who = (str((current_user or {}).get("id") or "") if current_user else "") or \
        str(((od.get("shipping_address") or {}) if isinstance(od.get("shipping_address"), dict) else {})
            .get("email") or od.get("email") or "").strip().lower()
    _items = []
    for it in (od.get("items") or []):
        if not isinstance(it, dict):
            continue
        _items.append("|".join(str(it.get(k) or "").strip() for k in
                               ("product_id", "variant_id", "barcode", "size", "color")) +
                      f"|{it.get('quantity') or 1}")
    _sa = od.get("shipping_address") if isinstance(od.get("shipping_address"), dict) else {}
    _addr = "|".join(str(_sa.get(k) or "").strip().lower() for k in
                     ("first_name", "last_name", "phone", "email", "city", "district", "address", "zip_code"))
    try:
        _tot = f"{round(float(od.get('total') or 0), 2):.2f}"
    except Exception:
        _tot = str(od.get("total") or "")
    raw = _js.dumps({
        "who": who, "items": sorted(_items), "addr": _addr,
        "pm": str(od.get("payment_method") or "").strip().lower(),
        "coupon": str(od.get("coupon_code") or "").strip().upper(),
        "gift": str(od.get("gift_card_code") or "").strip().upper(),
        "total": _tot,
    }, sort_keys=True, ensure_ascii=False)
    return _hl.sha256(raw.encode("utf-8")).hexdigest()[:40]


def _idempotency_conflict_response():
    from fastapi.responses import JSONResponse as _JR
    return _JR(status_code=409, content={
        "code": "idempotency_conflict",
        "detail": ("Sepetiniz veya sipariş bilgileriniz önceki denemeden farklı. "
                   "Lütfen siparişi yeniden gönderin."),
    })


@router.post("")
@(limiter.limit("20/minute") if limiter else (lambda f: f))
async def create_order(
    order_data: dict,
    request: Request,
    current_user: dict = Depends(get_current_user)
):
    """Create new order (rate-limited: bot/enumeration + kaynak tüketimi koruması)"""
    # ACİL ANAHTAR (close_checkout): yeni sipariş ALINMAZ (503, dostça mesaj). Hiçbir yan etki
    # (stok, kupon, sipariş kaydı) oluşmadan döner. BAŞLAMIŞ ödemelerin sonuçlandırılması
    # (payment.py callback/non3ds, scheduler mutabakat) bu uçtan geçmez → ETKİLENMEZ (değişmez 4).
    try:
        from security_switches import switch_on as _sw_on, switch_message as _sw_msg
        if await _sw_on("close_checkout"):
            raise HTTPException(status_code=503, detail=_sw_msg("close_checkout"))
    except HTTPException:
        raise
    except Exception:
        pass
    # FAZ 6 — Kullanıcı IP'sini kayda al. cf-connecting-ip (spoof-korumalı) → XFF → peer
    # (ortak helper; CAPI Purchase eşleşmesi + blok kontrolü doğru IP kullansın).
    try:
        from .deps import client_ip_from_request
        client_ip = client_ip_from_request(request) or ""
    except Exception:
        forwarded = request.headers.get("x-forwarded-for", "")
        client_ip = forwarded.split(",")[0].strip() if forwarded else (request.client.host if request.client else "")

    # GİRDİ DOĞRULAMA (2026-10): ürünsüz / adressiz istek sipariş AÇMAZ (eskiden {"items":[]}
    # yalnız kargo bedelli boş bir sipariş oluşturuyordu). Stok/statü mantığından ÖNCE reddedilir.
    _its = order_data.get("items") if isinstance(order_data, dict) else None
    if not isinstance(_its, list) or not _its or len(_its) > 100:
        raise HTTPException(status_code=400, detail="Sepetiniz boş. Lütfen ürün ekleyip tekrar deneyin.")
    for _it in _its:
        if not isinstance(_it, dict) or not str(_it.get("product_id") or "").strip():
            raise HTTPException(status_code=400, detail="Sepette geçersiz bir ürün var. Lütfen sepeti yenileyin.")
        try:
            _q = int(_it.get("quantity") or 1)
        except Exception:
            _q = 0
        if _q < 1 or _q > 100:
            raise HTTPException(status_code=400, detail="Geçersiz ürün adedi.")
    _sa0 = order_data.get("shipping_address")
    if not isinstance(_sa0, dict) or not str(_sa0.get("address") or "").strip() \
            or not str(_sa0.get("phone") or "").strip() or not str(_sa0.get("city") or "").strip():
        raise HTTPException(status_code=400, detail="Teslimat adresi eksik. Lütfen adres, il ve telefon bilgilerini girin.")

    # FAZ 6 — Blok kontrolü: kullanıcı veya IP bloklu mu?
    uid = (current_user.get("id") if current_user else None)
    block_query = []
    if uid:
        block_query.append({"user_id": uid})
    if client_ip:
        block_query.append({"ip": client_ip})
    # DENETİM FIX (#38): e-posta ile bloklama da uygulansın — hem oturumdaki kullanıcının
    # e-postası hem de teslimat/fatura adresindeki e-posta kontrol edilir. Blok kayıtları
    # e-postayı küçük harf sakladığı için karşılaştırma normalize edilir.
    _blk_emails = set()
    if current_user and current_user.get("email"):
        _blk_emails.add(str(current_user["email"]).strip().lower())
    for _addr_key in ("shipping_address", "billing_address", "billing_info"):
        _addr = order_data.get(_addr_key)
        if isinstance(_addr, dict) and _addr.get("email"):
            _blk_emails.add(str(_addr["email"]).strip().lower())
    if order_data.get("email"):
        _blk_emails.add(str(order_data["email"]).strip().lower())
    for _e in _blk_emails:
        if _e:
            block_query.append({"email": _e})
    if block_query:
        # A3: süreli blok kaydında expires_at geçmişse blok UYGULANMAZ (kalıcı bloklar—
        # expires_at yok/boş—aynen sürer). Böylece süresi dolmuş geçici blok müşteriyi
        # sonsuza dek sipariş veremez bırakmaz.
        _now_iso = datetime.now(timezone.utc).isoformat()
        bl = await db.blocked_customers.find_one({"$and": [
            {"$or": block_query},
            {"active": True},
            {"$or": [{"expires_at": {"$exists": False}}, {"expires_at": None},
                     {"expires_at": ""}, {"expires_at": {"$gt": _now_iso}}]},
        ]}, {"_id": 0})
        if bl:
            logger.warning(f"Blocked order attempt: user={uid} ip={client_ip} reason={bl.get('reason')}")
            raise HTTPException(status_code=403, detail="Hesabınız sipariş veremez. Lütfen destek ile iletişime geçin.")

    # BAKIM MODU (denetim 2026-10): bakım modu yalnız vitrinde (MaintenanceGate) uygulanıyordu;
    # doğrudan API / eski mobil istemci sipariş açabiliyordu. Bakım modu AÇIKKEN yalnız
    # panel personeli (MaintenanceGate'teki isAdmin istisnasıyla aynı) sipariş oluşturabilir.
    try:
        _mm = await db.settings.find_one({"id": "main"}, {"_id": 0, "maintenance_mode": 1}) or {}
    except Exception:
        _mm = {}
    if _mm.get("maintenance_mode") is True and not (current_user and current_user.get("is_admin") is True):
        raise HTTPException(status_code=503, detail=(
            "Sitemiz şu anda bakımda; sipariş alınamıyor. Lütfen kısa süre sonra tekrar deneyin."))

    # ÇİFT SİPARİŞ KORUMASI (idempotency): müşteri ödeme onayını göremeyip formu TEKRAR
    # gönderirse (ya da çift tıklarsa) aynı idempotency_key ile YENİ sipariş açılmaz; mevcut
    # (iptal olmayan) sipariş döndürülür → çift sipariş + çift çekim önlenir. İstemci sepet/adres
    # değişince yeni anahtar üretir, dolayısıyla farklı sepet farklı sipariş olur.
    _idem = str(order_data.get("idempotency_key") or "").strip()[:120]
    _idem_fp = _order_request_fingerprint(order_data, current_user) if _idem else None
    if _idem:
        _ex = await db.orders.find_one(
            {"idempotency_key": _idem, "status": {"$ne": "cancelled"}},
            {"_id": 0, "id": 1, "order_number": 1, "payment_status": 1, "idempotency_fingerprint": 1})
        if _ex and _ex.get("idempotency_fingerprint") and _ex.get("idempotency_fingerprint") != _idem_fp:
            logger.info(f"[idempotency] aynı anahtar FARKLI içerik → 409 key={_idem} no={_ex.get('order_number')}")
            return _idempotency_conflict_response()
        if _ex:
            logger.info(f"[idempotency] tekrar POST /orders — mevcut siparis donduruldu "
                        f"key={_idem} no={_ex.get('order_number')}")
            return {
                "order_id": _ex["id"],
                "order_number": _ex["order_number"],
                "payment_status": _ex.get("payment_status"),
                "pay_token": await _issue_pay_token(_ex["id"]),
                "idempotent": True,
                "message": "Mevcut sipariş kullanıldı",
            }

    # STOK KİLİTLEME KORUMASI (2026-09-29): ödeme bekleyen sipariş stoğu düşer ve iptal/iade
    # süresine kadar tutar. Aynı kişi (üye / e-posta / telefon) veya aynı IP çok sayıda ÖDENMEMİŞ
    # sipariş açıp stoğu kilitleyemesin. Eşik İşletme Kuralları'ndan (0 = kapalı). IP eşiği
    # ortak ağlar (mobil operatör/ofis) için 3 katıdır. Statü/ödeme mantığına DOKUNMAZ; yalnız
    # yeni sipariş açılmadan önce reddeder.
    try:
        from business_rules import get_rule as _pend_rule
        _pend_max = int(await _pend_rule(db, "order.max_unpaid_open_orders", 3) or 0)
    except Exception:
        _pend_max = 3
    if _pend_max > 0:
        _since = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
        # GÜVENLİK (commerce-3): müşterinin İPTAL TALEBİ açtığı (cancel_requested) ÖDENMEMİŞ
        # sipariş stoğu hâlâ tutar (personel iptale alana kadar; otomatik süpürge yalnız
        # pending/awaiting_payment'ı kapatır) ama sayılmıyordu → aç-iptal iste-aç döngüsüyle
        # sınırsız stok kilitlenebiliyordu. Ödenmiş siparişin iptal talebi sayılmaz.
        _base = {"$and": [
            {"created_at": {"$gte": _since}},
            {"$or": [{"status": "awaiting_payment"},
                     {"status": "cancel_requested",
                      "payment_status": {"$nin": ["paid", "refunded", "partially_refunded"]}}]},
        ]}
        _who = []
        if uid:
            _who.append({"user_id": uid})
        for _e in _blk_emails:
            if _e:
                _who.append({"shipping_address.email": _e})
        _ph = re.sub(r"\D", "", str(((order_data.get("shipping_address") or {}) if isinstance(order_data.get("shipping_address"), dict) else {}).get("phone") or ""))[-10:]
        if len(_ph) == 10:
            _who.append({"shipping_address.phone": {"$regex": re.escape(_ph) + "$"}})
        _n_who = await db.orders.count_documents({**_base, "$or": _who}) if _who else 0
        _n_ip = await db.orders.count_documents({**_base, "customer_ip": client_ip}) if client_ip else 0
        if _n_who >= _pend_max or _n_ip >= _pend_max * 3:
            logger.warning(f"[stok-kilit] ödenmemiş sipariş sınırı: user={uid} ip={client_ip} "
                           f"kisi={_n_who} ip_say={_n_ip} esik={_pend_max}")
            raise HTTPException(status_code=429, detail=(
                "Ödemesi tamamlanmamış siparişleriniz var. Lütfen önce mevcut siparişinizin ödemesini "
                "tamamlayın ya da bir süre sonra tekrar deneyin. Yardım için bizimle iletişime geçebilirsiniz."))

    order = {
        "id": generate_id(),
        "order_number": await next_order_number(),
        "idempotency_key": _idem or None,
        "idempotency_fingerprint": _idem_fp,
        "user_id": current_user.get("id") if current_user else None,
        # GÜVENLİK: kalem-başı indirim (discount_amount) YALNIZ sunucuda dondurulur
        # (_order_item_discounts). İstemci bu alanı gönderirse dondurma onu AYNEN kabul ediyordu
        # → iade/fatura/iyzico dağılımı manipüle edilebiliyordu (ör. [0,0,690] ile bedava ürünü
        # tutup ücretli olanları tam iade almak). Frontend bu alanı hiç göndermez → atılır.
        "items": [({k: v for k, v in _it.items() if k != "discount_amount"} if isinstance(_it, dict) else _it)
                  for _it in (order_data.get("items") or [])],
        "shipping_address": order_data.get("shipping_address", {}),
        "billing_address": order_data.get("billing_address") or order_data.get("shipping_address", {}),
        # Kurumsal fatura bilgileri (B2B müşteriler için)
        "billing_info": {
            "is_corporate": bool((order_data.get("billing_info") or {}).get("is_corporate", False)),
            "company_name": ((order_data.get("billing_info") or {}).get("company_name") or "").strip(),
            "tax_office": ((order_data.get("billing_info") or {}).get("tax_office") or "").strip(),
            "tax_number": ((order_data.get("billing_info") or {}).get("tax_number") or "").strip(),
            "e_invoice_user": bool((order_data.get("billing_info") or {}).get("e_invoice_user", False)),
        } if order_data.get("billing_info") else {"is_corporate": False},
        "subtotal": float(order_data.get("subtotal", 0)),
        "shipping_cost": float(order_data.get("shipping_cost", 0)),
        "discount": float(order_data.get("discount", 0)),
        "total": float(order_data.get("total", 0)),
        "payment_method": _allowed_payment_method(order_data.get("payment_method", "credit_card")),
        "payment_status": "pending",
        "status": "pending",
        # Müşterinin checkout'ta bıraktığı GENEL sipariş notu (hediye notundan ayrı).
        "notes": (order_data.get("notes") or "").strip()[:500],
        # Site siparişi: platform İSTEMCİDEN alınmaz (pazaryeri siparişleri kendi içe aktarım yolundan gelir).
        "platform": "meluni",
        # FAZ 4 — hediye seçenekleri
        "gift_note": (order_data.get("gift_note") or "")[:500],
        "gift_wrap": bool(order_data.get("gift_wrap", False)),
        "gift_wrap_price": float(order_data.get("gift_wrap_price", 0) or 0),
        "coupon_code": (order_data.get("coupon_code") or "").upper(),
        # COM03: uygulanan kampanyalar YALNIZ sunucu promo motorundan gelir (aşağıda). İstemcinin
        # gönderdiği liste kullanılmaz — motor hata verirse istemci listesindeki kupon id'leri
        # kullanım hakkı ayırıp/yakıp başka müşterinin kuponunu tüketebiliyordu.
        "applied_promotions": [],
        # İYS — ticari ileti izni (ödeme adımında kutu işaretlenirse). SMS izni OTP ile doğrulanır.
        "marketing_consent": {
            "email": bool(((order_data.get("marketing_consent") or {}).get("email"))),
            "sms": bool(((order_data.get("marketing_consent") or {}).get("sms"))),
            "otp_verified": bool(((order_data.get("marketing_consent") or {}).get("otp_verified"))),
            "at": datetime.now(timezone.utc).isoformat(),
        },
        # FAZ 6 — müşteri izleri
        "customer_ip": client_ip,
        "user_agent": request.headers.get("user-agent", "")[:300],
        # Reklam tıklama kimlikleri (ttclid/fbc/gclid …) — CAPI purchase'ta
        # webhook/tarayıcısız akışta atıf için kullanılır.
        "click_ids": _sanitize_click_ids(order_data.get("click_ids")),
        # KVKK: ziyaretçinin çerez bildiriminde 'pazarlama' onayı (tarayıcıdan taşınır). Consent
        # gate açıkken tarayıcısız server Purchase CAPI'si bu değere göre gönderilir/atlanır.
        "ad_tracking_consent": bool(order_data.get("ad_tracking_consent")),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat()
    }

    # Attribution snapshot (source of the sale)
    try:
        sid = order_data.get("attribution_session_id") or order_data.get("session_id")
        inline = order_data.get("attribution") if isinstance(order_data.get("attribution"), dict) else None
        order["attribution"] = await resolve_attribution_for_order(sid, inline)
    except Exception as att_err:
        logger.warning(f"Attribution resolve failed: {att_err}")
        order["attribution"] = {"channel": "direct", "source": "", "medium": "", "campaign": "", "session_id": ""}

    # Influencer linking — aff_id (çerez) önce, kupon fallback override
    try:
        from .influencers import resolve_influencer_for_order
        aff_id = (order.get("attribution") or {}).get("aff_id") or order_data.get("aff_id")
        link = await resolve_influencer_for_order(aff_id, order.get("coupon_code"))
        if link:
            order["influencer_id"] = link["influencer_id"]
            order["influencer_via"] = link["via"]
    except Exception as inf_err:
        logger.warning(f"Influencer link failed: {inf_err}")

    # ÖDEME BEKLEYEN durumu: hem havale/EFT hem de KART siparişi "awaiting_payment" başlar.
    # KÖK NEDEN (tekrar eden): kart siparişi eskiden "pending" açılıyordu → 3DS başlatılıp
    # tamamlanmayan (yarıda kalan) kart siparişi "pending" olarak kalıp panelde NORMAL/onaylanabilir
    # sipariş gibi görünüyordu. Artık ödeme onaylanana (payment callback: paid→confirmed) kadar
    # her ödeme-gerektiren sipariş "Ödeme Bekleniyor"dur; ödeme gelmezse auto-cancel süpürür.
    # Kart için oluşturma anında MÜŞTERİYE MAİL GİTMEZ (aşağıda gating var) — havale mailinden farklı.
    _pm0 = (order.get("payment_method") or "").lower()
    _BANK_METHODS = ("bank_transfer", "havale", "eft", "havale_eft", "banka_havale")
    _CARD_METHODS = ("credit_card", "card", "kredi_karti", "kart", "iyzico", "creditcard")
    if _pm0 in _BANK_METHODS or _pm0 in _CARD_METHODS:
        # Zaten ödenmiş (nadir: anlık non-3DS onaylanmış) gelmedikçe ödeme bekleniyor.
        if (order.get("payment_status") or "").lower() != "paid":
            order["status"] = "awaiting_payment"

    # GÜVENLİK (denetim 2026-10, ödeme kill-switch): admin panelinde (Ayarlar → Ödeme
    # Yöntemleri) kart / havale-EFT AÇIKÇA kapatıldıysa (`False`), eski istemci ya da doğrudan
    # API çağrısıyla gelen o yöntemli sipariş SUNUCUDA da reddedilir. Ayar YOKSA/boşsa yöntem
    # AÇIK sayılır (Checkout.jsx ile aynı: `pm.credit_card !== false`) → bugünkü akış bozulmaz.
    # Statü ataması (yukarıda, değişmez 2) DEĞİŞMEZ; yalnız oluşturma öncesi ret.
    if _pm0 in _BANK_METHODS or _pm0 in _CARD_METHODS:
        _pmcfg = ((await db.settings.find_one({"id": "main"}, {"_id": 0, "payment_methods": 1}) or {})
                  .get("payment_methods") or {})
        if _pm0 in _CARD_METHODS and _pmcfg.get("credit_card") is False:
            raise HTTPException(status_code=400, detail=(
                "Kredi kartı ile ödeme şu anda kullanılamıyor. Lütfen başka bir ödeme yöntemi seçin."))
        if _pm0 in _BANK_METHODS and _pmcfg.get("bank_transfer") is False:
            raise HTTPException(status_code=400, detail=(
                "Havale/EFT ile ödeme şu anda kullanılamıyor. Lütfen başka bir ödeme yöntemi seçin."))

    # Guvenlik: kapida odeme admin tarafindan KAPALIYSA, eski istemci veya dogrudan
    # API cagrisi ile gelen kapida odeme siparislerini sunucu tarafinda da reddet.
    if _pm0 in ("cash_on_delivery", "kapida"):
        _scfg = await db.settings.find_one({"id": "main"}, {"_id": 0, "payment_methods": 1}) or {}
        _cod_on = ((_scfg.get("payment_methods") or {}).get("cash_on_delivery")) is True
        if not _cod_on:
            raise HTTPException(status_code=400, detail="Kapıda ödeme şu anda kullanılamıyor. Lütfen başka bir ödeme yöntemi seçin.")

    # ============ SUNUCU-OTORİTER FİYATLAMA (K1) ============
    # Güvenlik: istemcinin gönderdiği birim fiyat / subtotal / shipping_cost / total ASLA
    # doğrudan kullanılmaz. Her kalemin birim fiyatı üründen (sale_price/price + varyant
    # price_adjustment) sunucuda yeniden hesaplanır; subtotal, indirim (promo motoru), kargo
    # (ayarlardan) ve toplam sunucuda kurulur. Böylece "price:1 ile 1 TL'ye sipariş" imkânsız.
    _items = order.get("items") or []
    _pids = list({it.get("product_id") for it in _items if it.get("product_id")})
    _pmap = {}
    if _pids:
        async for _p in db.products.find({"id": {"$in": _pids}}, {"_id": 0}):
            _pmap[_p["id"]] = _p
    # ÜYELERE ÖZEL: giriş yapmamış (misafir) müşteri üyelere özel kategorideki ürünü sipariş
    # EDEMEZ (id'yi bilerek doğrudan istek atsa bile). Sipariş/stok/ödeme mantığına dokunmaz;
    # yalnız oluşturma öncesi reddeder.
    if not current_user and _pmap:
        from .products import _members_only_cat_ids, product_is_members_only
        _mo_o = await _members_only_cat_ids()
        if _mo_o and any(product_is_members_only(_p, _mo_o) for _p in _pmap.values()):
            raise HTTPException(status_code=403, detail=(
                "Sepetinizde üyelerimize özel bir ürün var. Siparişi tamamlamak için lütfen giriş yapın "
                "veya üye olun."))
    _subtotal = 0.0
    try:
        from business_rules import get_rule as _get_rule_q
        _max_qty_per_item = int(await _get_rule_q(db, "order.max_qty_per_item", 50) or 50)
    except Exception:
        _max_qty_per_item = 50
    if _max_qty_per_item < 1:
        _max_qty_per_item = 50
    # commerce-3: aynı ürün/varyant AYRI kalemlere bölünerek (ör. 2 × 50) kalem başı tavan
    # aşılamasın — tavan ürün+varyant başına TOPLAM adede uygulanır (aşağıda).
    _qty_by_line_key = {}
    for it in _items:
        pid = it.get("product_id")
        prod = _pmap.get(pid) if pid else None
        # ÜrÜn bulunursa fiyatı SUNUCUDAN al (istemci fiyatını ez). Bulunamazsa siparişi TÜMDEN
        # reddetme — aksi halde silinmiş/farklı-id'li tek bir kalem yüzünden müşteri hiç sipariş
        # veremiyordu. Bulunamayan (gerçek olmayan) kalem için istemci fiyatı korunur; loglanır.
        if prod:
            # DENETİM (2026-09-29, Y2): kalem barkodu/varyantı ÜRÜNE BAĞLANIR. Eskiden variant_id
            # eşleşmezse istemcinin gönderdiği barkod korunuyordu → ucuz ürünün fiyatıyla pahalı
            # ürünün (başka ürünün barkodu) stoğu düşülüp sevk edilebiliyordu; sahte variant_id ile
            # varyant fiyat farkı da atlanabiliyordu. Artık: varyant id → bu ürünün barkodu →
            # beden/renk ile BU ürünün varyantı çözülür; çözülemezse kalem reddedilir.
            _vars = [v for v in (prod.get("variants") or []) if isinstance(v, dict)]
            _vid = it.get("variant_id")
            _vm = next((v for v in _vars if _vid and v.get("id") == _vid), None)
            if not _vm and _vars:
                _cb = str(it.get("barcode") or it.get("sku") or "").strip()
                if _cb:
                    _vm = next((v for v in _vars if _cb in (str(v.get("barcode") or "").strip(),
                                                             str(v.get("sku") or "").strip())), None)
                if not _vm and str(it.get("size") or "").strip():
                    _sz = str(it.get("size")).strip().lower()
                    _cl = str(it.get("color") or "").strip().lower()
                    _c = [v for v in _vars if str(v.get("size") or "").strip().lower() == _sz and
                          (not _cl or not v.get("color") or str(v.get("color")).strip().lower() == _cl)]
                    if len(_c) == 1:
                        _vm = _c[0]
                if not _vm:
                    logger.warning(f"[GUVENLIK] varyant çözülemedi pid={pid} vid={_vid} bc={_cb}")
                    raise HTTPException(status_code=400, detail="Sepetteki bir ürünün bedeni bulunamadı. Lütfen sepeti yenileyin.")
                it["variant_id"] = _vm.get("id")
            # SHOP03: panelde PASİF yapılmış (is_active=False) ya da silinmiş varyant, vitrinde
            # gösterilmese bile doğrudan API isteğiyle satın ALINAMAZ (rezervasyonlardan ÖNCE ret).
            if _vm and (_vm.get("is_active") is False or _vm.get("is_deleted")):
                logger.warning(f"[GUVENLIK] pasif varyant siparişi reddedildi pid={pid} vid={_vm.get('id')}")
                raise HTTPException(status_code=409, detail=(
                    f"Üzgünüz, '{prod.get('name') or 'ürün'}' ({_vm.get('size') or 'seçili beden'}) şu an "
                    "satışta değil. Lütfen sepetten çıkarıp tekrar deneyin."))
            it["price"] = _eff_unit_price(prod, it.get("variant_id") if _vm else None)
            if _vm:
                _vbc = _vm.get("barcode") or _vm.get("sku") or ""
                it["barcode"] = _vbc  # boşsa stok düşümü product_id+variant_id ile çözülür
                if _vm.get("size"):
                    it["size"] = _vm.get("size")
            else:
                it["barcode"] = prod.get("barcode") or ""
            it.pop("sku", None)
            if prod.get("name"):
                it["name"] = prod.get("name")
        else:
            # GÜVENLİK (fiyat manipülasyonu): Ürün DB'de yoksa istemci fiyatına GÜVENME.
            # Eskiden istemci fiyatı korunuyordu → saldırgan bilinmeyen product_id'ye
            # NEGATİF fiyat verip toplamı düşürüyor/bedava mal alıyordu. Böyle kalemi REDDET.
            logger.warning(f"[GUVENLIK] gecersiz/bulunamayan urun kalemi reddedildi pid={pid} fiyat={it.get('price')}")
            raise HTTPException(status_code=400, detail="Sepette geçersiz veya artık mevcut olmayan bir ürün var. Lütfen sepeti yenileyin.")
        # Fiyat asla negatif olamaz (savunma)
        try:
            if float(it.get("price", 0) or 0) < 0:
                it["price"] = 0.0
        except Exception:
            it["price"] = 0.0
        # SIFIR FİYAT KORUMASI (denetim 2026-10): sunucu birim fiyatı ≤ 0 ise (ör. XML feed'in
        # fiyatsız ürünü 0 TL yazması) kalem SATILMAZ. Belgelenmiş bir "bedava hediye ürün"
        # mekanizması yok — kampanya bedavaları (X al Y öde vb.) birim fiyatı değil, promo motoru
        # İNDİRİMİ ile sıfırlar; bu kontrol onları etkilemez (indirimden ÖNCEKİ fiyat).
        if float(it.get("price", 0) or 0) <= 0:
            logger.warning(f"[GUVENLIK] sıfır fiyatlı kalem reddedildi pid={pid} ad={it.get('name')}")
            raise HTTPException(status_code=400, detail=(
                f"'{it.get('name') or 'Ürün'}' şu an satışta değil (fiyat bilgisi eksik). "
                "Lütfen sepetten çıkarıp tekrar deneyin."))
        # A2.5: güvenli int parse + kalem başı üst sınır (kötü niyetli dev miktarlı sipariş /
        # tamsayı taşması savunması). Geçersiz miktar → 400. Tavan ayardan (order.max_qty_per_item).
        try:
            qty = int(it.get("quantity", it.get("qty", 1)) or 1)
        except (ValueError, TypeError):
            raise HTTPException(status_code=400, detail="Sepette geçersiz ürün adedi var. Lütfen sepeti yenileyin.")
        if qty < 1:
            qty = 1
        _lk = (str(pid or ""), str(it.get("variant_id") or ""))
        _qty_by_line_key[_lk] = _qty_by_line_key.get(_lk, 0) + qty
        if qty > _max_qty_per_item or _qty_by_line_key[_lk] > _max_qty_per_item:
            raise HTTPException(status_code=400,
                                detail=f"Bir üründen en fazla {_max_qty_per_item} adet sipariş edilebilir. Lütfen adedi düşürün.")
        it["quantity"] = qty
        _subtotal += float(it.get("price", 0) or 0) * qty
    _subtotal = round(_subtotal, 2)
    order["subtotal"] = _subtotal

    # İndirim: promo motoru (SUNUCU fiyatlarıyla); ödeme yöntemi de dikkate alınır.
    _server_discount = 0.0
    _free_shipping = False
    try:
        from .coupons import evaluate_cart_promotions as _eval_promos
        _eng_items = [{
            "product_id": it.get("product_id"),
            # GÜVENLİK (commerce-3): kategori İSTEMCİDEN alınmaz. Motor ürünün category_ids'ini
            # sunucudan zenginleştirir ama kategorisiz üründe kalemin category_id'sine düşüyordu →
            # istemci istediği kategoriye özel kampanyayı (ör. Outlet %50) o ürüne uygulatabiliyordu.
            "category_id": (_pmap.get(it.get("product_id")) or {}).get("category_id"),
            "qty": int(it.get("quantity", it.get("qty", 1)) or 1),
            "price": float(it.get("price", 0) or 0),
        } for it in _items]
        # KRİTİK: Müşterinin kasada "×" ile KALDIRDIĞI kampanyalar (excluded_ids) motora
        # AKTARILMALI. Aktarılmadığında sunucu, ekranda GÖSTERİLMEYEN kampanyaları da üst üste
        # uygulayıp müşterinin onayladığından FARKLI (düşük) tutarı bankadan çekiyordu
        # (3240 onaylandı → 2916,90 çekildi). excluded_ids yalnız indirim AZALTIR; istemci bunu
        # kendi lehine kullanamaz (daha çok indirim aldıramaz), bu yüzden güvenle onurlandırılır.
        _excl = order_data.get("excluded_ids") or []
        if not isinstance(_excl, list):
            _excl = []
        _excl = [str(x) for x in _excl if x]
        order["excluded_promotion_ids"] = _excl
        _ev = await _eval_promos(
            cart_total=_subtotal,
            items=_eng_items,
            user_id=order.get("user_id"),
            email=(order.get("shipping_address") or {}).get("email", ""),
            entered_code=order.get("coupon_code", ""),
            payment_method=order.get("payment_method", ""),
            excluded_ids=_excl,
        )
        _server_discount = round(float(_ev.get("total_discount", 0) or 0), 2)
        _free_shipping = bool(_ev.get("free_shipping"))
        order["applied_promotions"] = _ev.get("applied") or []
        # Bulanık kupon çözümleme: müşteri 'hoş geldin %10' gibi bir varyant yazdıysa siparişe
        # KANONİK kod (HOSGELDIN10) yazılır → record_order_redemptions kuponu birebir bulur.
        _res_code = str(_ev.get("entered_resolved") or "").strip()
        if _res_code and (order.get("coupon_code") or "").strip():
            order["coupon_code"] = _res_code
    except Exception as _promo_err:
        logger.warning(f"Promo değerlendirme hatası (indirim 0 kabul edildi): {_promo_err}")
        _server_discount = 0.0
    if _server_discount > _subtotal:
        _server_discount = _subtotal
    order["discount"] = _server_discount

    # Havale/EFT indirimi: müşteriyi banka havalesine teşvik için, kupon indiriminden SONRAKİ
    # tutar üzerinden ayar-tabanlı yüzde (varsayılan %5). Sunucu-otoriter: istemci değil sunucu
    # hesaplar. Yalnızca havale/eft ödeme yönteminde uygulanır.
    _pm_disc = 0.0
    _pm_lc = (order.get("payment_method") or "").lower()
    if _pm_lc in ("bank_transfer", "havale", "eft", "havale_eft", "banka_havale"):
        try:
            _pmset = await db.settings.find_one(
                {"id": "main"}, {"_id": 0, "bank_transfer_discount_pct": 1}) or {}
            _pm_pct = float(_pmset.get("bank_transfer_discount_pct", 5) or 0)
        except Exception:
            _pm_pct = 5.0
        if _pm_pct > 0:
            _pm_disc = round((_subtotal - _server_discount) * _pm_pct / 100.0, 2)
    order["payment_discount"] = _pm_disc
    order["bank_transfer_discount_pct"] = (_pm_pct if _pm_disc > 0 else 0)

    # Kargo: sunucu ayarından (ücretsiz kargo eşiği VEYA kupon free_shipping). Y25 de burada çözülür.
    try:
        # TEK KAYNAK: storefront (/api/settings) ile BİREBİR aynı çözümleme. Eskiden burada
        # doğrudan settings.shipping_fee (90) okunuyor, storefront ise seçili kargo firmasının
        # ücretini (99) gösteriyordu → müşterinin onayladığı tutar ile çekilen tutar 9 TL
        # farklı çıkıyordu. Eşik de aynı şekilde iki ayrı kaynaktan geliyordu.
        from .settings import resolve_shipping_fee as _res_fee, resolve_free_shipping_threshold as _res_thr
        _sset = await db.settings.find_one(
            {"id": "main"},
            {"_id": 0, "shipping_fee": 1, "free_shipping_threshold": 1,
             "cargo_fees": 1, "default_cargo_company": 1}) or {}
        _ship_fee = float(_res_fee(_sset) or 0)
        _thr = await _res_thr(_sset)
        _thr = float(_thr) if _thr not in (None, "") else None
    except Exception:
        _ship_fee, _thr = 0.0, None
    from shipping_rules import shipping_quote
    _ship_quote = shipping_quote(_subtotal, [_server_discount, _pm_disc], _thr, _ship_fee, _free_shipping)
    _shipping = _ship_quote["cost"]
    order["shipping_cost"] = round(_shipping, 2)
    # #13/#14: Bedava kargo kampanyası (kupon free_shipping VEYA eşik) izini sakla —
    # iade sonrası "kampanya dışı kaldı" uyarısı ve faturada kargo-iskonto kalemi için.
    _fs_applied = _ship_quote["free"]
    order["free_shipping_applied"] = _fs_applied
    order["free_shipping_waived_fee"] = round(_ship_fee, 2) if (_fs_applied and _shipping == 0 and _ship_fee > 0) else 0.0
    if _thr is not None:
        order["free_shipping_threshold"] = _thr

    # Hediye paketi ücreti — SUNUCU-otoriter (K1): istemcinin gönderdiği gift_wrap_price'a
    # GÜVENİLMEZ (gift_wrap_price=0 gönderip bedava paket alınabiliyordu). Yalnız gift_wrap
    # seçiliyse İşletme Kuralı'ndaki ücret uygulanır (frontend Checkout da aynı kuralı okur).
    _gift = 0.0
    if order.get("gift_wrap"):
        try:
            from business_rules import get_rule as _gw_rule
            _gift = max(0.0, float(await _gw_rule(db, "product.gift_wrap_price", 130) or 0))
        except Exception:
            _gift = 0.0
    order["gift_wrap_price"] = round(_gift, 2)

    # Kapıda ödeme hizmet bedeli — İşletme Kuralı'ndan (hardcode DEĞİL), yalnız COD siparişte.
    # Eskiden order.total'a HİÇ eklenmiyordu → her COD siparişinde hizmet bedeli sessizce kayıptı.
    _cod_fee = 0.0
    if _pm0 in ("cash_on_delivery", "kapida", "kapida_odeme", "cod"):
        try:
            from business_rules import get_rule as _cod_rule
            _cod_fee = max(0.0, float(await _cod_rule(db, "shipping.cod_fee", 10) or 0))
        except Exception:
            _cod_fee = 0.0
    order["cod_fee"] = round(_cod_fee, 2)

    # Toplam = subtotal - kupon indirimi - havale indirimi + kargo + hediye paketi + kapıda ödeme.
    order["total"] = round(_subtotal - _server_discount - _pm_disc + _shipping + _gift + _cod_fee, 2)

    # ⭐ PUAN KULLANIMI (C3) — sunucu-otoriter: tavan (ara toplam - indirim) *
    # payment.points_redeem_max_pct; bakiye ATOMİK düşülür (loyalty.spend_points_for_order).
    # İptalde refund_points_once (_restock_order_once kancası) idempotent iade eder.
    # 1 puan = 1 TL. Kart siparişinde kalan 1 TL altına indirilmez.
    if order_data.get("use_points") and order.get("user_id"):
        try:
            from business_rules import get_rule as _ly_rule
            _ly_enabled = await _ly_rule(db, "loyalty.enabled", True) is not False
        except Exception:
            _ly_enabled = True
        if _ly_enabled:
            from .loyalty import spend_points_for_order
            _sp = await spend_points_for_order(
                order.get("user_id"), order, float(order["total"]),
                cap_base=(_subtotal - _server_discount))
            if _sp.get("ok"):
                order["points_used"] = _sp["points"]
                order["points_refunded"] = False
                order["total"] = round(max(0.0, float(order["total"]) - _sp["points"]), 2)
            else:
                logger.info(f"[loyalty] puan kullanılamadı: {_sp.get('error')}")

    # Final eligibility includes points actually spent (never trust requested points).
    # Reconcile the fee BEFORE gift-card reservation and the approved-total check.
    _ship_quote = shipping_quote(
        _subtotal, [_server_discount, _pm_disc, order.get("points_used", 0)],
        _thr, _ship_fee, _free_shipping)
    order["total"] = round(order["total"] + _ship_quote["cost"] - _shipping, 2)
    _shipping = _ship_quote["cost"]
    order["shipping_cost"] = _shipping
    order["shipping_eligibility_basis"] = _ship_quote["basis"]
    order["free_shipping_applied"] = _ship_quote["free"]
    order["free_shipping_waived_fee"] = round(_ship_fee, 2) if _ship_quote["free"] else 0.0
    if not _ship_quote["free"]:
        order["applied_promotions"] = [
            {**p, "free_shipping": False} for p in order.get("applied_promotions", [])
            if not p.get("free_shipping") or float(p.get("discount") or 0) > 0
        ]

    # 💳 HEDİYE ÇEKİ / MAĞAZA KREDİSİ (C2) — tüm indirimlerden SONRA, toplamın üstünden.
    # Sunucu-otoriter: bakiye ATOMİK rezerve edilir (gift_cards.redeem_gift_card_for_order),
    # iptalde _restock_order_once kancası idempotent iade eder. Tutarın TAMAMI çekle
    # karşılanırsa sipariş paid/confirmed açılır — istemci girdisi DEĞİL, sunucunun kendi
    # yakaladığı değer olduğundan "yalnız gerçek ödeme = paid" değişmezinin belgeli 2. yoludur
    # (CLAUDE.md). Kod geçersizse sipariş REDDEDİLİR (müşteri çeksiz sipariş vermiş olmasın).
    _gc_code = str(order_data.get("gift_card_code") or "").strip().upper()
    if _gc_code:
        try:
            from business_rules import get_rule as _gc_rule
            _gc_enabled = await _gc_rule(db, "giftcard.enabled", True) is not False
        except Exception:
            _gc_enabled = True
        if not _gc_enabled:
            if order.get("points_used"):  # Y1: rezerve puanı geri ver
                from .loyalty import refund_points_once as _rpo
                await _rpo(order, unpersisted=True)
            raise HTTPException(status_code=400, detail="Hediye çeki kullanımı şu an kapalı")
        from .gift_cards import redeem_gift_card_for_order
        _gc = await redeem_gift_card_for_order(_gc_code, order, float(order["total"]))
        if not _gc.get("ok"):
            if order.get("points_used"):  # Y1: rezerve puanı geri ver
                from .loyalty import refund_points_once as _rpo
                await _rpo(order, unpersisted=True)
            raise HTTPException(status_code=400, detail=_gc.get("error") or "Hediye çeki uygulanamadı")
        order["gift_card"] = {"code": _gc["code"], "amount": _gc["amount"], "refunded": False}
        order["total"] = round(max(0.0, float(order["total"]) - _gc["amount"]), 2)
        if order["total"] <= 0.009:
            order["total"] = 0.0
            order["payment_status"] = "paid"
            order["status"] = "confirmed"
            order["paid_with_gift_card_only"] = True
            order["paid_at"] = datetime.now(timezone.utc).isoformat()

    # ==================== ONAY-TUTARI DOĞRULAMASI (ödeme standardı) ====================
    # DEĞİŞMEZ: Müşteriden, kasada GÖRÜP ONAYLADIĞI tutardan BAŞKA bir tutar ASLA çekilmez.
    # Sunucu fiyat otoritesidir (istemcinin tutarı KULLANILMAZ), ancak istemcinin gösterdiği
    # tutar bir ONAY KANITI olarak karşılaştırılır. Uyuşmuyorsa sipariş REDDEDİLİR — sessizce
    # farklı tutar çekilmez. Böylece bugünkü olay (ekran 3240,00 → bankaya 2.916,90 gitti)
    # ve gelecekteki HER sapma kaynağı (kampanya seti, puan tavanı, kargo kuralı, üye-grubu/
    # ödeme-tipi indirimi…) sessiz yanlış tahsilat yerine görünür hataya dönüşür.
    # Yön farketmez: eksik tahsilat = gelir kaybı, fazla tahsilat = müşteri mağduriyeti.
    # İstemci tutar göndermiyorsa (eski mobil sürüm) kontrol atlanır — sunucu yine otoriterdir.
    try:
        from business_rules import get_rule as _tm_rule
        _enforce_match = await _tm_rule(db, "order.enforce_total_match", True) is not False
    except Exception:
        _enforce_match = True
    try:
        _client_total = float(order_data.get("total") or 0)
    except Exception:
        _client_total = 0.0
    _server_total = round(float(order["total"] or 0), 2)
    if _client_total > 0 and abs(_client_total - _server_total) > 0.02:
        logger.error(
            "[TUTAR-UYUSMAZLIGI] istemci=%.2f sunucu=%.2f fark=%.2f | subtotal=%.2f indirim=%.2f "
            "havale=%.2f kargo=%.2f hediye_paketi=%.2f kapida=%.2f puan=%.2f hediye_ceki=%.2f "
            "kampanyalar=%s excluded=%s",
            _client_total, _server_total, round(_client_total - _server_total, 2),
            _subtotal, _server_discount, _pm_disc, _shipping, _gift, _cod_fee,
            float(order.get("points_used") or 0), float((order.get("gift_card") or {}).get("amount") or 0),
            [(_a.get("code"), _a.get("discount")) for _a in (order.get("applied_promotions") or [])],
            order.get("excluded_promotion_ids") or [],
        )
        if _enforce_match:
            # Rezerve edilen bakiyeleri (puan / hediye çeki) geri ver — sipariş açılmıyor.
            try:
                # Sipariş HENÜZ yazılmadı → unpersisted=True (aksi halde kilit bulunamayıp iade atlanıyordu)
                if order.get("points_used"):
                    from .loyalty import refund_points_once
                    await refund_points_once(order, unpersisted=True)
                if order.get("gift_card"):
                    from .gift_cards import refund_gift_card_once
                    await refund_gift_card_once(order, unpersisted=True)
            except Exception as _rb_err:
                logger.error(f"[TUTAR-UYUSMAZLIGI] bakiye geri alınamadı: {_rb_err}")
            # Sunucunun hesapladığı tutar mesaja YAZILIR: (a) müşteri neyi onaylayacağını görür,
            # (b) sapma teşhisi log erişimi olmadan da yapılabilir. Bu müşterinin KENDİ sepet
            # tutarıdır — gizli bilgi değildir.
            raise HTTPException(
                status_code=409,
                detail=(f"Sepet tutarı güncellendi: ödenecek tutar {_server_total:.2f} TL "
                        f"(ekranda {_client_total:.2f} TL görünüyordu). Güvenliğiniz için ödeme "
                        f"alınmadı. Lütfen sepetinizi yenileyip tekrar deneyin."),
            )

    # 🧾 İNDİRİM DÖKÜMÜ — müşteri VE admin siparişte her indirimi AYRI AYRI görsün.
    # Her kampanya/kupon ayrı satır (hoşgeldin, otomatik %10, kod…), havale ayrı satır.
    _breakdown = []
    for _ap in (order.get("applied_promotions") or []):
        _amt = round(float(_ap.get("discount", 0) or 0), 2)
        if _amt <= 0:
            continue
        _code = _ap.get("code") or ""
        _breakdown.append({
            "type": "coupon" if _code else "campaign",
            "label": _ap.get("title") or _code or "Kampanya indirimi",
            "code": _code,
            "amount": _amt,
        })
    if _pm_disc > 0:
        _pmpct = order.get("bank_transfer_discount_pct") or 0
        _breakdown.append({
            "type": "payment",
            "label": f"Havale/EFT indirimi" + (f" (%{int(_pmpct)})" if _pmpct else ""),
            "code": "",
            "amount": round(_pm_disc, 2),
        })
    order["discount_breakdown"] = _breakdown
    order["discount_total"] = round(_server_discount + _pm_disc, 2)

    # 🎯 PER-ÜRÜN İNDİRİMİ DONDUR (W11214 kök çözümü): kampanya kapsamı O AN doğru olduğundan
    # hangi üründen ne kadar indirim düşüldüğünü kalem üstüne (item.discount_amount) yaz. İptal/iade/
    # gider-pusulası bu DONMUŞ değeri okur → ürün kategorisi sonradan değişse de sabit kalır, kapsam-
    # dışı ürüne hayali (düz-oransal) indirim yazılmaz. Kapsamdaki her ürün kendi payını, kapsam-dışı 0.
    try:
        _pdisc = await _order_item_discounts(order)
        for _i, _it in enumerate(order.get("items") or []):
            if isinstance(_it, dict) and _i < len(_pdisc):
                _it["discount_amount"] = _pdisc[_i]
    except Exception as _e:
        logger.warning(f"[create_order] per-ürün indirim dondurma atlandı: {_e}")

    # A2.5b — OVERSELL ENGELLE (kullanıcı kararı). Sipariş insert'inden ÖNCE stok koşullu
    # atomik düşülür: bir kalem bile yetmezse düşürülenler geri alınır ve 409 ile reddedilir
    # (karşılanamayacak sipariş HİÇ oluşmaz). Ayar block_oversell=false ise (backorder/ön-sipariş
    # isteyen firma) eski davranışa döner: düşüm insert SONRASI koşulsuz yapılır. Pazaryeri
    # (trendyol/hepsiburada/temu) siparişi dışarıda satıldığından bu kapıya girse de engellenmez.
    _oversell_moves = None
    _plat_lc = str(order.get("platform") or "").lower()
    _is_marketplace = _plat_lc in ("trendyol", "hepsiburada", "temu", "n11", "amazon")

    # DENETİM (2026-09-29, Y1): puan/hediye çeki bu noktada REZERVE edildi. Sipariş aşağıdaki
    # herhangi bir nedenle AÇILMAZSA (pasif ürün, stok yetersiz, eşzamanlı çift istek, insert
    # hatası) rezervasyon geri verilir — eskiden yalnız tutar-uyuşmazlığında veriliyordu ve
    # müşterinin çek bakiyesi/puanı hiçbir siparişe bağlı olmadan kayboluyordu. İdempotent.
    async def _release_reservations(_why: str) -> None:
        try:
            if order.get("points_used"):
                from .loyalty import refund_points_once
                await refund_points_once(order, unpersisted=True)
            if order.get("gift_card"):
                from .gift_cards import refund_gift_card_once
                await refund_gift_card_once(order, unpersisted=True)
        except Exception as _rr_err:
            logger.error(f"[create_order:{_why}] bakiye geri alınamadı: {_rr_err}")
        # COM03: bu siparişe ayrılmış kupon kullanım hakları da geri verilir (idempotent).
        if order.get("coupon_reservations"):
            try:
                from .coupons import release_coupon_usage
                await release_coupon_usage(order["id"])
            except Exception as _cr_err:
                logger.error(f"[create_order:{_why}] kupon hakkı geri verilemedi: {_cr_err}")

    # PASİF / SİLİNMİŞ ürün siparişini ENGELLE (site). Vitrin pasif ürünü gizlese de bayat
    # sepet / direkt istek ile pasif ürün sipariş edilebiliyordu → "ürünü pasif yaptım ama yine
    # sipariş geldi". Ayrıca iptal→restock yarışına karşı ek kalkan: stok 0'a ek olarak ürünü
    # pasif alınca artık KESİN sipariş gelmez.
    if not _is_marketplace:
        for _it in (order.get("items") or []):
            _ipid = _it.get("product_id")
            _ibc = _it.get("barcode") or _it.get("sku") or ""
            _pp = None
            if _ipid:
                _pp = await db.products.find_one({"id": _ipid}, {"_id": 0, "is_active": 1, "is_deleted": 1, "name": 1})
            elif _ibc:
                _pp = await db.products.find_one(
                    {"$or": [{"variants.barcode": _ibc}, {"barcode": _ibc}]},
                    {"_id": 0, "is_active": 1, "is_deleted": 1, "name": 1})
            if _pp and (_pp.get("is_deleted") or _pp.get("is_active") is False):
                _nm = _it.get("name") or _pp.get("name") or "ürün"
                logger.warning(f"[OVERSELL] pasif/silinmiş ürün siparişi reddedildi: {_nm}")
                await _release_reservations("pasif_urun")
                raise HTTPException(status_code=409,
                                    detail=f"Üzgünüz, '{_nm}' şu an satışta değil. Lütfen sepetten çıkarıp tekrar deneyin.")
    # COM03 — KUPON KULLANIM LİMİTİ (atomik): kullanıcının girdiği, usage_limit'li kuponun hakkı
    # insert'ten ÖNCE tek belgede koşullu $inc ile AYRILIR → son hak eşzamanlı checkout'larda bir
    # kez verilir. Redemption kaydının ZAMANLAMASI değişmez (değişmez 5: kartta ödeme onayında).
    # Sipariş açılamazsa _release_reservations hakkı geri verir; iptal/otomatik iptal
    # release_order_redemptions kancasıyla bırakır.
    try:
        from .coupons import reserve_order_coupon_usage as _reserve_cu
        _cu = await _reserve_cu(order)
    except Exception as _cu_err:
        logger.error(f"[create_order] kupon hakkı ayrılamadı: {_cu_err}")
        await _release_reservations("kupon_rezervasyon_hata")
        raise HTTPException(status_code=503, detail=(
            "Kupon kullanım hakkı şu an doğrulanamadı. Lütfen birkaç saniye sonra tekrar deneyin."))
    if not _cu.get("ok"):
        logger.warning(f"[COM03] kupon kullanım limiti dolu — sipariş reddedildi kod={_cu.get('code')}")
        await _release_reservations("kupon_limit_dolu")
        raise HTTPException(status_code=409, detail=(
            f"'{_cu.get('code') or 'Kupon'}' kodunun kullanım limiti doldu. Lütfen sepetinizi yenileyip "
            "kuponsuz tekrar deneyin. Ödeme alınmadı."))
    if _cu.get("reserved"):
        order["coupon_reservations"] = list(_cu["reserved"])
    # KVKK: TCKN/VKN (billing_info/billing_address/shipping_address) düz değer olarak YAZILMAZ —
    # stok düşümünden ÖNCE mühürlenir (şifreleme hatası stok sızdırmasın). İstemcinin gönderdiği
    # *_enc/*_hash alanları atılır (yalnız sunucu üretir).
    _pii_drop_client(order)
    _pii_seal(order)
    # OVERSELL: SİTE siparişinde 0/yetersiz stok HER ZAMAN engellenir — 'order.block_oversell'
    # ayarına BAKILMAZ. (Ayar yanlışlıkla kapatılınca stoğu biten üründen sipariş geçiyordu →
    # para/stok kaybı. Firma kararı: site oversell KESİNLİKLE yasak.) Yalnız pazaryeri
    # (trendyol/hb/temu) siparişi dışarıda satıldığı için bu kapıya girse de engellenmez.
    if not _is_marketplace:
        _dec = await _decrement_stock_atomic(order)
        if not _dec.get("success"):
            _nm = _dec.get("name") or _dec.get("barcode") or "ürün"
            logger.warning(f"[OVERSELL] sipariş reddedildi — stok yetersiz/doğrulanamadı: {_dec.get('barcode')} ({_nm}) reason={_dec.get('reason')}")
            await _release_reservations("stok_yetersiz")
            raise HTTPException(status_code=409,
                                detail=f"Üzgünüz, '{_nm}' için yeterli stok kalmadı. Lütfen sepeti güncelleyip tekrar deneyin.")
        _oversell_moves = _dec.get("movements") or []

    try:
        await db.orders.insert_one(order)
        # Yeni sipariş → satış raporu önbelleğini anında düşür (create_order
        # _log_order_event çağırmadığı için burada ayrıca yapılır). Sessiz: rapor
        # önbelleği yüzünden sipariş akışı ASLA bozulmaz.
        try:
            from .reports import invalidate_report_cache as _inv_rc
            await _inv_rc()
        except Exception:
            pass
    except Exception as _ins_err:
        # Insert başarısız olduysa önceden düşülen stoğu geri al (sızıntı olmasın).
        if _oversell_moves:
            try:
                await _reverse_stock_moves(_oversell_moves)
            except Exception:
                pass
        await _release_reservations("insert_hata")
        # DENETİM (race R1): iki EŞZAMANLI POST /orders aynı idempotency_key ile check-then-insert
        # yarışını geçip ikisi de buraya gelebiliyordu (çift sipariş/çift stok/çift ödeme). Artık
        # orders.idempotency_key ÜZERİNDE partial-unique indeks var → ikinci insert DuplicateKeyError
        # verir; stoğu geri alıp MEVCUT siparişi idempotent döndürürüz (çift sipariş engellenir).
        try:
            from pymongo.errors import DuplicateKeyError as _DupKey
        except Exception:
            _DupKey = None
        if _DupKey is not None and isinstance(_ins_err, _DupKey) and _idem:
            _ex2 = await db.orders.find_one(
                {"idempotency_key": _idem},
                {"_id": 0, "id": 1, "order_number": 1, "payment_status": 1, "idempotency_fingerprint": 1})
            if _ex2 and _ex2.get("idempotency_fingerprint") and _ex2.get("idempotency_fingerprint") != _idem_fp:
                return _idempotency_conflict_response()
            if _ex2:
                logger.info(f"[idempotency-race] eşzamanlı çift POST — mevcut sipariş döndürüldü key={_idem}")
                return {"order_id": _ex2["id"], "order_number": _ex2["order_number"],
                        "payment_status": _ex2.get("payment_status"), "idempotent": True,
                        "pay_token": await _issue_pay_token(_ex2["id"]),
                        "message": "Mevcut sipariş kullanıldı"}
        raise
    logger.info(f"Order created: {order['order_number']}")

    # İYS — ticari ileti izni kaydı + dijital bildirim (arka planda). E-posta izni doğrudan;
    # SMS izni YALNIZCA OTP doğrulaması yapıldıysa geçerli sayılır (numara sahipliği kanıtı).
    try:
        _mc = order.get("marketing_consent") or {}
        _sa = order.get("shipping_address") or {}
        _channels = []
        if _mc.get("email"):
            _channels.append("EPOSTA")
        if _mc.get("sms"):
            # SMS izni SUNUCU-otoriter: telefonun OTP ile doğrulandığını backend teyit eder.
            from .iys import _phone_recently_verified as _otp_ok
            _phone = _sa.get("phone") or order.get("phone") or ""
            _verified = await _otp_ok(_phone)
            order["marketing_consent"]["otp_verified"] = bool(_verified)
            await db.orders.update_one({"id": order["id"]},
                                       {"$set": {"marketing_consent.otp_verified": bool(_verified)}})
            if _verified:
                _channels.append("MESAJ")
        if _channels:
            from .iys import record_consent as _rec_consent
            _spawn(_rec_consent(
                recipient_email=_sa.get("email") or order.get("email") or "",
                recipient_phone=_sa.get("phone") or order.get("phone") or "",
                channels=_channels, status="ONAY", source="HS_WEB",
                ip=order.get("customer_ip", ""), order_id=order["id"],
                user_id=order.get("user_id"),
            ))
            # Üyeyse profildeki pazarlama tercihini de güncelle — YALNIZ işaretlenen kanal.
            # Profil `accepts_marketing` e-posta ONAY'ı sayılır (consents._collect / kampanya kitlesi);
            # eskiden yalnız SMS işaretlense de True yapılıyordu → e-postadan çıkmış üyeye kampanya
            # e-postası yeniden gidiyordu.
            if order.get("user_id"):
                _u_prefs = ((await db.users.find_one({"id": order["user_id"]}, {"_id": 0, "marketing_prefs": 1}))
                            or {}).get("marketing_prefs")
                _u_prefs = dict(_u_prefs) if isinstance(_u_prefs, dict) else {}
                _u_set = {}
                if "EPOSTA" in _channels:
                    _u_prefs["email"] = True
                    _u_set.update({"accepts_marketing": True,
                                   "marketing_consent_at": datetime.now(timezone.utc).isoformat()})
                if "MESAJ" in _channels:
                    _u_prefs["sms"] = True
                _u_set["marketing_prefs"] = _u_prefs
                await db.users.update_one({"id": order["user_id"]}, {"$set": _u_set})
    except Exception as _iys_err:
        logger.warning(f"İYS izin kaydı başarısız (sipariş etkilenmedi): {_iys_err}")

    # Üye adres defteri — giriş yapmış üyenin sipariş adresini "Adreslerim"e otomatik kaydet.
    # Önceden create_order adresi YALNIZCA sipariş belgesine yazıyordu; üyenin /my-addresses
    # (db.addresses) listesi boş kalıyordu. Burada user_id varsa adresi tekilleştirerek ekler
    # (aynı adres+il+ilçe varsa tekrar yazmaz). Hata olsa bile sipariş bozulmaz.
    try:
        import re as _re
        _uid = order.get("user_id")
        _sa = order.get("shipping_address") or {}
        _line = (_sa.get("address") or "").strip()
        if _uid and _line:
            _ci = lambda s: {"$regex": f"^{_re.escape((s or '').strip())}$", "$options": "i"}
            # KVKK: adres defteri şifreli → tekilleştirme address_hash ile (backfill öncesi düz kayıt
            # için eski büyük/küçük harf duyarsız tam eşleşme de denenir).
            from security.contact_pii import address_hashes as _addr_hashes, seal_doc as _contact_seal
            _dup = await db.addresses.find_one({"user_id": _uid, "$or": [
                {"address_hash": {"$in": _addr_hashes(_line, _sa.get("city"), _sa.get("district")) or ["\x00"]}},
                {"address": _ci(_line), "city": _ci(_sa.get("city")), "district": _ci(_sa.get("district"))},
            ]}, {"_id": 1})
            if not _dup:
                _has_any = await db.addresses.find_one({"user_id": _uid}, {"_id": 1})
                _binfo = order.get("billing_info") or {}
                await db.addresses.insert_one(_contact_seal(_pii_seal({  # KVKK: tax_no + telefon + adres mühürlü
                    "id": generate_id(),
                    "user_id": _uid,
                    "title": (_sa.get("title") or "Sipariş Adresi"),
                    "first_name": _sa.get("first_name", ""),
                    "last_name": _sa.get("last_name", ""),
                    "phone": _sa.get("phone", ""),
                    "address": _line,
                    "city": _sa.get("city", ""),
                    "district": _sa.get("district", ""),
                    "postal_code": _sa.get("postal_code", ""),
                    "is_default": (not _has_any),  # üyenin ilk adresi ise varsayılan yap
                    "is_corporate": bool(_binfo.get("is_corporate", False)),
                    "company_name": _binfo.get("company_name", ""),
                    "tax_no": _pii_reveal(_binfo, "tax_number"),
                    "tax_office": _binfo.get("tax_office", ""),
                    "source": "order",
                    "created_at": datetime.now(timezone.utc).isoformat(),
                }, "address"), "address"))
                logger.info(f"[adres defteri] sipariş adresi üyeye kaydedildi user={_uid} order={order.get('order_number')}")
        # Profil telefonu boşsa sipariş telefonundan doldur (üye detay/iletişim için) — şifreli yazılır.
        if _uid and (_sa.get("phone") or "").strip():
            _u = await db.users.find_one({"id": _uid}, {"_id": 0, "phone": 1, "phone_enc": 1,
                                                        "is_admin": 1, "is_super_admin": 1})
            if _u is not None and not (_u.get("phone") or "").strip() and not _u.get("phone_enc"):
                from security.contact_pii import sealed_set_update as _contact_set, is_staff_doc as _is_staff
                _ph_set = {"phone": _sa["phone"]}
                await db.users.update_one({"id": _uid}, {"$set": _ph_set} if _is_staff(_u)
                                          else _contact_set(_ph_set, "user"))
    except Exception as _addr_err:
        logger.warning(f"Üye adres defteri kaydı başarısız (sipariş etkilenmedi): {_addr_err}")

    # Y6 — Promosyon kullanım kaydı ödeme durumuna göre yapılır. Önceden kupon, ödeme
    # BAŞARISIZ olsa bile sipariş oluşturma anında "yakılıyordu"; müşteri tekrar denediğinde
    # indirim düşüyor ve fark sessizce ücretlendiriliyordu. Artık ön-ödemeli (kart) siparişte
    # redemption yalnızca ödeme onaylandıktan sonra (_notify_paid_order_confirmed → record_order_
    # redemptions) kaydedilir. Kapıda ödeme / havale / zaten ödenmiş siparişlerde hemen kaydedilir.
    _pm_now = (order.get("payment_method") or "").lower()
    _commit_now = (
        _pm_now in ("cash_on_delivery", "kapida", "kapida_odeme", "cod",
                    "bank_transfer", "havale", "eft", "havale_eft", "banka_havale")
        or (order.get("payment_status") or "").lower() == "paid"
    )
    if _commit_now:
        await record_order_redemptions(order)
    else:
        # Denetim #21: aynı kişinin aynı kişi-başı-sınırlı kuponu taşıyan önceki ÖDENMEMİŞ denemeleri
        # ödenemez işaretlenir (statü/stok değişmez; auto-cancel süpürür). Sipariş akışını bozmaz.
        try:
            from .coupons import supersede_prior_unpaid_coupon_orders
            await supersede_prior_unpaid_coupon_orders(order)
        except Exception as _sp_err:
            logger.warning(f"[kupon-rezervasyon] önceki denemeler işaretlenemedi: {type(_sp_err).__name__}")

    # NOT: Sunucu-otoriter promosyon yeniden-hesabi artik INSERT'ten ONCE "guvenli kelepce"
    # blogunda yapiliyor (yukari bkz). Bu yuzden eski post-insert [PROMO SHADOW] gozlem blogu
    # kaldirildi — motor siparis basina yalnizca bir kez calisir.


    # FAZ — Sipariş onayı bildirimi (SMS + Email + WhatsApp) — fire-and-forget
    import asyncio as _asyncio
    async def _notify_order_created():
        try:
            import sys, os
            sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
            from notification_service import send_notification

            ship = order.get("shipping_address") or {}
            full_name = (
                f"{ship.get('first_name','')} {ship.get('last_name','')}".strip()
                or ship.get("full_name") or ship.get("name") or "Müşterimiz"
            )
            # Sipariş kalemlerini email için HTML satırlarına çevir
            items = order.get("items") or []
            items_rows = ""
            for it in items[:20]:
                img = it.get("image") or ""
                name = it.get("name") or it.get("product_name") or "Ürün"
                qty = it.get("quantity", 1)
                price = it.get("price", 0)
                size = it.get("size", "")
                color = it.get("color", "")
                meta = " · ".join([f"Beden: {size}" if size else "", f"Renk: {color}" if color else "", f"Adet: {qty}"]).strip(" · ")
                items_rows += (
                    f'<tr><td style="padding:12px 0;border-bottom:1px solid #f0f0f0;width:80px;">'
                    f'<img src="{img}" alt="" style="width:64px;height:80px;object-fit:cover;background:#fafafa;"/></td>'
                    f'<td style="padding:12px 12px;border-bottom:1px solid #f0f0f0;font-size:13px;color:#111;">{name}'
                    f'<div style="color:#999;font-size:11px;margin-top:4px;">{meta}</div></td>'
                    f'<td style="padding:12px 0;border-bottom:1px solid #f0f0f0;text-align:right;font-size:13px;color:#111;white-space:nowrap;">{(price*qty):.2f} TL</td></tr>'
                )
            items_html = (
                '<div style="padding:0 24px 16px;"><table cellpadding="0" cellspacing="0" border="0" style="width:100%;">'
                f'{items_rows}</table></div>'
            ) if items_rows else ""

            base_url = os.environ.get("FRONTEND_PUBLIC_URL") or os.environ.get("REACT_APP_BACKEND_URL") or ""
            order_link = f"{base_url}/order-success/{order['order_number']}" if base_url else "#"
            order_date = order["created_at"][:10]
            _hv_bank = {}
            if order.get("status") == "awaiting_payment":
                _pay = await db.settings.find_one({"id": "payment"}, {"_id": 0}) or {}
                _banks = _pay.get("bank_accounts") or []
                _hv_bank = next((b for b in _banks if b.get("is_default")), None) or (_banks[0] if _banks else {})

            variables = await _order_notify_vars(order)
            _pm_notify = (order.get("payment_method") or "").lower()
            _is_bank_notify = _pm_notify in ("bank_transfer", "havale", "eft", "havale_eft", "banka_havale")
            if order.get("status") == "awaiting_payment" and not _is_bank_notify:
                # KART ödeme bekliyor: oluşturma anında MÜŞTERİYE MAİL GİTMEZ (havale bank-detay
                # maili KART'a gönderilmemeli). Ödeme onaylanınca (payment callback) "confirmed"
                # maili gider. Ödeme gelmezse auto-cancel süpürür, müşteriye yanlış "alındı" gitmez.
                logger.info(f"[order created] kart ödeme bekleniyor — oluşturma maili ertelendi order={order.get('order_number')}")
            elif order.get("status") == "awaiting_payment":
                from order_statuses import get_status_config
                _cfg = await get_status_config(db)
                _nz = (_cfg.get("notify") or {}).get("awaiting_payment") or {}
                _channels = [c for c in ("sms", "email") if _nz.get(c)]
                # Havale/EFT siparişinde "Siparişiniz Alındı · Ödeme Bekleniyor"
                # bildirimi HER ZAMAN gitmeli — config boş bırakılmış olsa bile
                # müşteri en azından e-posta (varsa SMS) ile bilgilendirilir.
                if not _channels:
                    _channels = ["email"]
                    if (ship.get("phone") or order.get("phone")):
                        _channels.append("sms")
                await send_notification(
                    db, "order_awaiting_payment",
                    to_phone=ship.get("phone") or order.get("phone"),
                    to_email=ship.get("email") or order.get("email"),
                    variables=variables,
                    channels=_channels,
                )
                # ADMIN E-POSTA: havale/EFT siparişi OLUŞTURMADA firma adresine (info@...) haber ver
                # (kart HARİÇ — o ödeme onaylanınca gider). Müşteriye banka-detay maili zaten gitti.
                await _send_admin_new_order_email(order, ship, "Havale/EFT bekleniyor")
            else:
                # ÖNEMLİ: Online kart (iyzico) siparişinde "Siparişiniz Alındı" onay
                # maili sipariş OLUŞTURMA anında DEĞİL, ödeme onaylandıktan SONRA
                # (payment callback: paid → confirmed) gönderilir. Aksi halde iyzico
                # ödemesi başarısız/yarıda kalsa bile müşteriye yanlış "alındı" maili
                # gider. Burada yalnızca kapıda ödeme (COD) veya zaten ödenmiş sipariş
                # anında onaylanır; havale zaten yukarıdaki awaiting_payment dalına gider.
                _pm = (order.get("payment_method") or "").lower()
                _cod = _pm in ("cash_on_delivery", "kapida", "kapida_odeme", "cod")
                _already_paid = (order.get("payment_status") or "").lower() == "paid"
                if _cod or _already_paid:
                    await send_notification(
                        db, "order_confirmed",
                        to_phone=ship.get("phone") or order.get("phone"),
                        to_email=ship.get("email") or order.get("email"),
                        variables=variables,
                    )
                    # ADMIN E-POSTA: kapıda ödeme siparişinde OLUŞTURMADA haber ver.
                    if _cod and not _already_paid:
                        await _send_admin_new_order_email(order, ship, "Kapıda ödeme")
                    elif _already_paid and order.get("paid_with_gift_card_only"):
                        # C2 (tamamı hediye çekiyle ödenen sipariş) payment akışından GEÇMEZ →
                        # _notify_paid_order_confirmed hiç çağrılmaz; admin "Yeni Sipariş" maili
                        # burada BİR KEZ gönderilir (atomik bayrak → tekrar/yarışta çift mail yok).
                        _gm = await db.orders.update_one(
                            {"id": order["id"], "admin_new_order_mail_sent": {"$ne": True}},
                            {"$set": {"admin_new_order_mail_sent": True}})
                        if getattr(_gm, "modified_count", 0) == 1:
                            await _send_admin_new_order_email(order, ship, "Hediye çeki ile ödendi")
                else:
                    logger.info(
                        f"[order_confirmed ERTELENDİ] online kart, ödeme onayı bekleniyor "
                        f"order={order.get('order_number')} pm={_pm}"
                    )
        except Exception as e:
            logger.warning(f"order_confirmed notification dispatch failed: {e}")

    _spawn(_notify_order_created())

    # Mobil admin uygulamasına ANLIK PUSH: yeni sipariş bildirimi (best-effort; yoksa sessiz).
    # KART siparişinde push OLUŞTURMADA GÖNDERİLMEZ — 3DS yarıda kalırsa admin'e sahte "yeni
    # sipariş" gitmesin. Kart için push, ödeme onaylanınca (payment: paid→confirmed) çıkar.
    # Havale/EFT/kapıda ödemede oluşturmada push gider (gerçek aksiyon bekleyen sipariş).
    _pm_push = (order.get("payment_method") or "").lower()
    _defer_push = (_pm_push in ("credit_card", "card", "kredi_karti", "kart", "iyzico", "creditcard")
                   and (order.get("payment_status") or "").lower() != "paid")
    async def _notify_admins_push():
        try:
            from .push import send_push_to_admins
            _tl = _round2(order.get("total") or 0)
            _ship = order.get("shipping_address") or {}
            _who = (f"{_ship.get('first_name','')} {_ship.get('last_name','')}".strip()
                    or _ship.get("full_name") or "Müşteri")
            _pf = _platform_display(order)
            await send_push_to_admins(
                f"🛍️ Yeni Sipariş · {_tl:,.2f} TL".replace(",", "."),
                f"{order.get('order_number','')} · {_who} · {_pf}",
                {"type": "new_order", "order_id": str(order.get("id") or ""),
                 "order_number": str(order.get("order_number") or "")},
            )
        except Exception as e:
            logger.warning(f"admin push (new_order) atlandı: {e}")
    if not _defer_push:
        _spawn(_notify_admins_push())
    else:
        logger.info(f"[admin push ERTELENDİ] kart ödeme bekleniyor order={order.get('order_number')}")

    # FAZ 1 - C1: otomatik stok düşümü.
    # A2.5b: oversell-engelli yolda stok zaten insert ÖNCESİ atomik düşüldü (_oversell_moves);
    # burada YALNIZ hareket kaydı yazılır (çift düşüm olmaz). Aksi halde (backorder/pazaryeri
    # veya block_oversell kapalı) eski koşulsuz düşüm uygulanır.
    try:
        if _oversell_moves is not None:
            moves = _oversell_moves
        else:
            moves = await _stock_delta_for_order(order, -1)
        if moves:
            await db.stock_movements.insert_one({
                "id": str(uuid.uuid4()),
                "type": "order_created",
                "order_id": order["id"],
                "order_number": order["order_number"],
                "items": moves,
                "created_at": datetime.now(timezone.utc).isoformat(),
            })
            # FAZ 2: "bu sipariş stoktan düşüldü" damgası siparişin KENDİSİNE yazılır.
            # Böylece ileride iptal/iade ederken cevap ayrı koleksiyona, anahtar
            # eşleşmesine veya tarihe bağlı kalmaz. Damga yazılamazsa akış bozulmaz
            # (defter hareketi yine var, _order_was_deducted onu da okur).
            try:
                await db.orders.update_one({"id": order["id"]},
                                           {"$set": {"stock_deducted": True}})
            except Exception:
                pass
    except Exception as stock_err:
        logger.error(f"Stock decrement on order create failed: {stock_err}")

    # Site satışı stoğu düşürdü → etkilenen ürünlerin güncel stoğunu pazaryerlerine ANINDA bas
    # (Trendyol + HB). Periyodik senkronu beklemeden oversell penceresini kapatır. Yalnız site
    # siparişinde (pazaryeri siparişi zaten dışarıdan geldi, geri push gereksiz). Arka planda,
    # bloklamaz.
    try:
        if not _is_marketplace:
            _push_pids = [it.get("product_id") for it in (order.get("items") or []) if it.get("product_id")]
            if _push_pids:
                _spawn(_push_stock_to_marketplaces(_push_pids))
    except Exception as _push_err:
        logger.warning(f"[stok-push] tetikleme hatası: {_push_err}")

    return {
        "order_id": order["id"],
        "order_number": order["order_number"],
        "pay_token": await _issue_pay_token(order["id"]),
        # C2: tutarın tamamı hediye çekiyle karşılandıysa frontend ödeme adımını atlar.
        "payment_status": order.get("payment_status") or "",
        "total": order.get("total"),
        "gift_card_applied": (order.get("gift_card") or {}).get("amount") or 0,
        "message": "Sipariş oluşturuldu"
    }

@router.put("/{order_id}")
async def update_order(
    order_id: str,
    order_data: dict,
    current_user: dict = Depends(require_permission("orders.edit"))
):
    """Update order (admin only).
    GÜVENLİK: Genel güncelleme finansal/kimlik/iç alanları EZEMEZ (mass-assignment
    koruması). Tutar/ödeme durumu için ayrı, denetimli uçlar var (/status, /mark-paid).
    Böylece ele geçirilmiş veya düşük-yetkili admin token'ı toplamı/ödeme durumunu/
    sahipliği manipüle edemez."""
    existing = await db.orders.find_one({"id": order_id})
    if not existing:
        raise HTTPException(status_code=404, detail="Sipariş bulunamadı")

    _PROTECTED = {
        "id", "_id", "order_number", "user_id", "created_at",
        "total", "total_amount", "subtotal", "discount", "discount_amount",
        "shipping_cost", "payment_status", "is_paid", "paid_at",
        "invoice_number", "invoice_seq", "idempotency_key", "idempotency_fingerprint",
        "iyzico_retrieve_response", "iyzico_init_response", "iyzico_response",
        "iyzico_token", "iyzico_return_url", "payment_receipt",
        "capi_purchase_sent", "coupon_redemptions_recorded",
        # DENETİM (payment redteam F3): mass-assignment savunmasını genişlet — ödeme/iade/çek/puan
        # iç alanları genel uçtan EZİLEMEZ.
        "payment_id", "iyzico_payment_id", "reconcile_payment_id", "needs_reconciliation",
        "gift_card", "points_used", "points_refunded", "paid_with_gift_card_only",
        "loyalty_awarded", "loyalty_points_earned", "gift_card_refunded",
        "_restocked_by_autocancel", "_redecremented_after_reconcile", "stock_restored",
        "_redecremented_after_cancel", "paid_after_cancel",
        # COM03/COM09/COM10: kupon hakkı tutmaları ve sipariş-başı işlem kilitleri genel uçtan EZİLEMEZ.
        "coupon_reservations", "cargo_label_lock_until", "cargo_label_lock_id",
        "cargo_label_attempted_at", "return_req_lock_until", "return_req_lock_id",
        # DENETİM K1: durum makinesi TEK kapıdan geçmeli. `status` bu genel uçtan
        # değiştirilirse update_order_status'taki finans-yetki kapısı + stok iadesi +
        # kupon/çek/puan iadesi + iptal SMS'i ATLANIR (Değişmez 1/5/6 delinir). Statü
        # YALNIZ PUT /orders/{id}/status (update_order_status) üzerinden değişir.
        "status",
        # DENETİM 2026-10 (ödeme sertleştirme): iade kayıtları, çift tahsilat ve kilit/bildirim
        # bayrakları genel uçtan EZİLEMEZ (iade tavanı / çift tahsilat izi atlatılamasın).
        "refunds", "duplicate_payments", "needs_duplicate_refund", "refund_needs_verification",
        "payment_init_lock_until", "payment_init_lock_id", "order_confirmed_notified",
        "invoice_pending", "invoice_manual_file",
        # DENETİM 2026-10: ödeme yöntemi ve kalemler genel uçtan DEĞİŞMEZ (kalem düzenleme
        # stok defterini / iade tavanını / faturayı atlatır; ödeme yöntemi ödeme akışını değiştirir).
        "payment_method", "items", "lines",
    }
    if not isinstance(order_data, dict):
        raise HTTPException(status_code=400, detail="Geçersiz istek gövdesi")
    # COM09: etiket sonrası adres değişikliği için süper admin gerekçesi (alan siparişe YAZILMAZ).
    _addr_override = str(order_data.pop("address_change_override_reason", "") or "").strip()[:300]
    removed = [k for k in list(order_data.keys()) if k in _PROTECTED]
    for k in removed:
        order_data.pop(k, None)
    _perms_uo = await get_effective_permissions(current_user)
    _super_uo = "*" in _perms_uo
    # Fatura bilgisi (VKN/unvan/vergi dairesi) faturayı belirler → yalnız fatura yetkisiyle.
    for _bk in ("billing_address", "billing_info"):
        if _bk in order_data and not (_super_uo or "orders.invoice" in _perms_uo):
            order_data.pop(_bk, None)
            removed.append(_bk)
    # Teslimat adresi (kargo yönlendirmesi) → yalnız sipariş durum yetkisiyle + iz kaydı.
    _addr_change = None
    if "shipping_address" in order_data:
        if not (_super_uo or "orders.update_status" in _perms_uo):
            order_data.pop("shipping_address", None)
            removed.append("shipping_address")
        elif not isinstance(order_data.get("shipping_address"), dict):
            order_data.pop("shipping_address", None)
            removed.append("shipping_address")
        else:
            # KVKK: karşılaştırma mühürleme SONRASI gerçek değerlerle yapılır (aşağıda).
            _addr_change = True
    if removed:
        logger.warning(f"[GUVENLIK] update_order korunan/yetkisiz alan(lar) yok sayildi order={order_id} alanlar={removed} admin={current_user.get('email')}")

    order_data["updated_at"] = datetime.now(timezone.utc).isoformat()

    # KISMİ İPTAL TESPİTİ (site + elle düzenleme): kalemlerden biri çıkarılıp toplam adet
    # DÜŞTÜYSE ve sipariş tamamen iptal DEĞİLSE işaretle → panelde "iptal edilen ürün var"
    # kırmızı noktası. (Pazaryeri kısmi iptalleri kendi senkronunda işaretlenir.)
    if "items" in order_data:
        _old_q = sum(int(it.get("quantity") or 1) for it in (existing.get("items") or []))
        _new_q = sum(int(it.get("quantity") or 1) for it in (order_data.get("items") or []))
        if 0 < _new_q < _old_q and (order_data.get("status") or existing.get("status")) not in ("cancelled", "cancel_refunded"):
            order_data["partial_cancelled"] = True
            order_data["partial_cancel_at"] = datetime.now(timezone.utc).isoformat()

    # KVKK: TCKN/VKN düz yazılmaz. Panel düzenleme formu değeri değiştirmeden (maskeli "*********12")
    # gönderirse mevcut şifreli değer korunur; yeni değer yazıldıysa mühürlenir; boş → temizlenir.
    from security.pii import sealed_set_update as _pii_set_update
    _pii_drop_client(order_data)
    # KVKK: liste/detay telefon ve açık adresi MASKELİ döner. Form maskeli değeri ("+90 *** *** ** 33",
    # "••••••") geri gönderirse gerçek değer korunur — maske ASLA adresin/telefonun üstüne yazılmaz.
    _contact_carry(order_data, existing)
    _upd_doc = _pii_set_update(order_data, "order", existing)
    order_data = _upd_doc["$set"]
    # COM09 / FIN-E06 — ETİKET SONRASI ADRES KİLİDİ: kargo etiketi oluşmuşsa (ya da şu an oluşturuluyorsa)
    # teslimat adresi DEĞİŞTİRİLEMEZ — eski adresli etiket sevke çıkar, yeni adres kayıtta kalırdı.
    # Kilit atomik: güncelleme filtresi etiket kilidinin boş olmasını şart koşar (etiket işçisiyle yarış).
    # Süper admin gerekçe (address_change_override_reason ≥10 karakter) ile istisna yapabilir;
    # o zaman sipariş "etiket adresi eski" işaretlenir ve denetim kaydı yazılır.
    _upd_filter = {"id": order_id}
    _addr_override_used = False
    if _addr_change is True:
        def _sa_view(src):
            # Anlamlı karşılaştırma: boş/eksik alan eşit sayılır, boşluk farkı yok sayılır (panel formu
            # dokunulmamış adresi ek boş anahtarlarla geri gönderebilir → sahte "değişti" + 409 olmasın).
            _v = (_pii_reveal_doc({"shipping_address": src.get("shipping_address")}) or {}).get("shipping_address")
            if not isinstance(_v, dict):
                return _v
            return {k: str(v).strip() for k, v in _v.items()
                    if v not in (None, "") and str(v).strip() != "" and not str(k).endswith(("_enc", "_hash", "_last2"))}
        if _sa_view(order_data) != _sa_view(existing):
            _cargo = existing.get("cargo") or {}
            _has_label = bool(existing.get("cargo_barcode_created") or existing.get("cargo_barcode_number")
                              or existing.get("cargo_tracking_number") or _cargo.get("mng_siparis_no")
                              or _cargo.get("tracking_number"))
            _now_l = datetime.now(timezone.utc).isoformat()
            _lock_busy = bool(existing.get("cargo_label_lock_until")
                              and str(existing.get("cargo_label_lock_until")) > _now_l)
            if _has_label or _lock_busy:
                if not (_super_uo and len(_addr_override) >= 10):
                    raise HTTPException(status_code=409, detail=(
                        "Bu siparişin kargo etiketi oluşturulmuş (ya da şu an oluşturuluyor); teslimat adresi "
                        "değiştirilemez. Önce kargo kaydını iptal ettirin. (Süper admin gerekçe girerek "
                        "istisna yapabilir.)"))
                _addr_override_used = True
                order_data["cargo_address_changed_after_label"] = True
                order_data["cargo_address_changed_after_label_at"] = _now_l
                order_data["cargo_address_changed_after_label_by"] = current_user.get("email", "")
            else:
                _upd_filter = {"$and": [
                    {"id": order_id},
                    {"$or": [{"cargo_label_lock_until": {"$exists": False}}, {"cargo_label_lock_until": None},
                             {"cargo_label_lock_until": {"$lt": _now_l}}]},
                    {"cargo_barcode_created": {"$ne": True}},
                    {"$or": [{"cargo_barcode_number": {"$exists": False}}, {"cargo_barcode_number": {"$in": [None, ""]}}]},
                ]}
    _upd_res = await db.orders.update_one(_upd_filter, _upd_doc)
    if getattr(_upd_res, "matched_count", 1) == 0:
        raise HTTPException(status_code=409, detail=(
            "Kargo etiketi bu arada oluşturuldu; teslimat adresi değiştirilemez. Sayfayı yenileyin."))
    if _addr_override_used:
        await _log_order_event(order_id, "address",
                               "Etiket SONRASI adres değişikliği (süper admin istisnası) — etiket eski adresli!",
                               current_user, {"reason": _addr_override},
                               order_number=existing.get("order_number", ""))
        try:
            await record_admin_audit(db, action="orders.address_change_after_label", entity_type="order",
                                     entity_id=order_id, current_user=current_user, source="admin.orders",
                                     metadata={"reason": _addr_override,
                                               "order_number": existing.get("order_number", "")})
        except Exception as _aa:
            logger.warning(f"[COM09] denetim kaydı yazılamadı: {_aa}")

    def _pii_view(k, src):  # karşılaştırma için gerçek değer; log için maskeli görünüm
        return (_pii_reveal_doc({k: src.get(k)}) or {}).get(k)

    def _pii_logview(k, src):
        v = src.get(k)
        return _pii_mask({k: dict(v)})[k] if isinstance(v, dict) else v
    if _addr_change is True:
        _addr_change = (None if _pii_view("shipping_address", order_data) == _pii_view("shipping_address", existing)
                        else (_pii_logview("shipping_address", existing) or {},
                              _pii_logview("shipping_address", order_data)))
    _billing_changed = [k for k in ("billing_address", "billing_info")
                        if k in order_data and _pii_view(k, order_data) != _pii_view(k, existing)]
    if _addr_change:
        await _log_order_event(order_id, "address", "Teslimat adresi değiştirildi", current_user,
                               {"old_shipping_address": _addr_change[0],
                                "new_shipping_address": _addr_change[1]},
                               order_number=existing.get("order_number", ""))
    if _billing_changed:
        await _log_order_event(order_id, "invoice", "Fatura bilgisi değiştirildi", current_user,
                               {"fields": _billing_changed,
                                **{f"old_{k}": _pii_logview(k, existing) for k in _billing_changed},
                                **{f"new_{k}": _pii_logview(k, order_data) for k in _billing_changed}},
                               order_number=existing.get("order_number", ""))

    return {"message": "Sipariş güncellendi", "ignored_fields": sorted(set(removed))}

def _sanitize_click_ids(raw) -> dict:
    """Storefront'tan sipariş oluştururken gelen reklam tıklama kimliklerini
    (ttclid, fbc, gclid …) beyaz-listeyle süzer. Siparişe kaydedilir; ödeme
    onayı tarayıcısız geldiğinde (iyzico webhook) CAPI purchase event'ine
    buradan eklenir — TikTok/Meta atıf (attribution) kalitesi için şart."""
    if not isinstance(raw, dict):
        return {}
    allowed = ("ttclid", "ttp", "fbp", "fbc", "gclid", "wbraid", "gbraid",
               "epik", "sc_click_id", "sc_cookie1")
    out = {}
    for k in allowed:
        v = raw.get(k)
        if isinstance(v, str) and v.strip():
            out[k] = v.strip()[:500]
    return out


async def dispatch_purchase_capi(order_id: str, source: str = "") -> bool:
    """Sipariş için CAPI 'purchase' event'ini (TikTok CompletePayment, Meta
    Purchase, …) TÜM aktif sağlayıcılara BİR KEZ gönderir.

    event_id = SİPARİŞ NUMARASI — tarayıcı pixel'i (GTM/ttq/fbq) aynı
    event_id'yi kullandığından platformlar iki kaydı tekilleştirir (dedup),
    çift sayım olmaz.

    Idempotent: orders.capi_purchase_sent bayrağı ATOMİK set edilir; ödeme
    callback'i + iyzico webhook'u + admin onayı aynı anda tetiklense bile
    tek event çıkar. Kullanıcı verisi (email/telefon hash'i, IP, UA, ttclid)
    sipariş kaydından okunur — tarayıcı gerekmez."""
    import os as _os_capi
    import company as _company_capi
    _co_capi = await _company_capi.get_company(db)
    _brand_name = _co_capi.get("store_name") or ""
    _site_url = (_co_capi.get("site_url") or _os_capi.environ.get("SITE_URL") or "").rstrip("/")
    try:
        # KVKK: consent gate AÇIKSA ve sipariş 'ad_tracking_consent' taşımıyorsa server Purchase'ı
        # da GÖNDERME (D'nin server-tarafı tamamlanması). Atlanınca capi_purchase_sent İŞARETLENMEZ
        # → izin sonradan tanımlanırsa yeniden tetiklenebilir. Gate KAPALIYKEN (varsayılan) etkisiz.
        try:
            from business_rules import get_rule as _get_rule
            _consent_gate = bool(await _get_rule(db, "marketing.capi_consent_gate", False))
        except Exception:
            _consent_gate = False
        if _consent_gate:
            _co = await db.orders.find_one({"id": order_id}, {"_id": 0, "ad_tracking_consent": 1})
            if _co and _co.get("ad_tracking_consent") is not True:
                logger.info(f"[CAPI] consent gate: order {order_id} reklam izni yok → server Purchase atlandı")
                return False
        prev = await db.orders.find_one_and_update(
            {"id": order_id, "capi_purchase_sent": {"$ne": True}},
            {"$set": {"capi_purchase_sent": True,
                      "capi_purchase_source": source or "",
                      "capi_purchase_at": datetime.now(timezone.utc).isoformat()}},
            projection={"_id": 0},
        )
        if not prev:
            return False  # zaten gönderilmiş (veya sipariş yok) — çift sayım engellendi
        order_doc = prev
        from services.capi.orchestrator import dispatch_event
        from services.capi.hash_utils import build_user_data
        addr = order_doc.get("shipping_address") or {}
        cids = dict(order_doc.get("click_ids") or {})
        # fbp/fbc/ttclid/ttp checkout click_ids'te yoksa (tarayıcısız/webhook Purchase) attribution
        # session'dan doldur — Meta + TikTok eşleşme kalitesi için. Boş değerle mevcudu EZME.
        if not all(cids.get(k) for k in ("fbp", "fbc", "ttclid", "ttp")):
            _sid = order_doc.get("attribution_session_id") or (order_doc.get("attribution") or {}).get("session_id")
            if _sid:
                try:
                    _asess = await db.attribution_sessions.find_one(
                        {"session_id": _sid}, {"_id": 0, "fbp": 1, "fbc": 1, "ttp": 1,
                                               "last_touch": 1, "first_touch": 1})
                    if _asess:
                        for _k in ("fbp", "fbc", "ttp"):
                            if not cids.get(_k) and _asess.get(_k):
                                cids[_k] = _asess[_k]
                        # ttclid touch içinde tutulur (last_touch öncelikli, yoksa first_touch)
                        if not cids.get("ttclid"):
                            _tt = ((_asess.get("last_touch") or {}).get("ttclid")
                                   or (_asess.get("first_touch") or {}).get("ttclid"))
                            if _tt:
                                cids["ttclid"] = _tt
                except Exception:
                    pass
        # external_id: üye → customer/user id; misafir → stabil first-party visitor id
        # (attribution session_id = tarayıcıdaki meluni_sid). Tarayıcı Purchase'ı da AYNI
        # değeri external_id yaptığından browser↔server eşleşir ve external_id coverage yükselir.
        # marketing.capi_guest_external_id flag'i (varsayılan AÇIK) ile misafir fallback'i kapatılabilir.
        _ext_id = order_doc.get("customer_id") or order_doc.get("user_id")
        if not _ext_id:
            try:
                from business_rules import get_rule as _get_rule_ext
                if bool(await _get_rule_ext(db, "marketing.capi_guest_external_id", True)):
                    _ext_id = (order_doc.get("attribution_session_id")
                               or (order_doc.get("attribution") or {}).get("session_id"))
            except Exception:
                pass
        user_data = build_user_data(
            email=addr.get("email") or order_doc.get("email"),
            phone=addr.get("phone") or order_doc.get("phone"),
            first_name=addr.get("first_name") or (addr.get("full_name") or "").split(" ")[0],
            last_name=addr.get("last_name") or " ".join((addr.get("full_name") or "").split(" ")[1:]),
            city=addr.get("city"),
            state=addr.get("district") or addr.get("state"),
            country=addr.get("country") or "TR",
            # KVKK: reklam platformlarına AÇIK ADRES / POSTA KODU gönderilmez (hash'li bile olsa); yalnız
            # platform belgesindeki SHA-256 e-posta/telefon + il/ilçe (hash) eşleşmesi.
            zipcode=None,
            street=None,
            external_id=_ext_id,
            client_ip=order_doc.get("customer_ip"),
            user_agent=order_doc.get("user_agent"),
            ttclid=cids.get("ttclid"), ttp=cids.get("ttp"),
            fbp=cids.get("fbp"), fbc=cids.get("fbc"),
            gclid=cids.get("gclid"), wbraid=cids.get("wbraid"), gbraid=cids.get("gbraid"),
            epik=cids.get("epik"),
            sc_click_id=cids.get("sc_click_id"), sc_cookie1=cids.get("sc_cookie1"),
        )
        items_payload = [{
            "item_id": str(it.get("product_id") or it.get("sku") or ""),
            "item_name": it.get("name") or "",
            "item_brand": it.get("brand") or _brand_name,
            "item_variant": f"{it.get('size','')} {it.get('color','')}".strip(),
            "price": float(it.get("unit_price") or it.get("price") or 0),
            "quantity": int(it.get("quantity") or 1),
        } for it in (order_doc.get("items") or [])]
        order_number = str(order_doc.get("order_number") or order_id)
        await dispatch_event(
            db,
            event_name="purchase",
            event_id=order_number,   # tarayıcı pixel'iyle dedup anahtarı (sipariş no)
            user_data=user_data,
            event_payload={
                "currency": order_doc.get("currency") or "TRY",
                "value": float(order_doc.get("total") or 0),
                "items": items_payload,
                "order_id": order_number,
                "coupon": order_doc.get("coupon_code"),
                "shipping": float(order_doc.get("shipping_cost") or 0),
                "discount": float(order_doc.get("discount") or 0),
                "payment_type": order_doc.get("payment_method") or "",
            },
            event_source_url=_site_url,
        )
        logger.info(f"CAPI purchase gönderildi order={order_number} src={source}")
        return True
    except Exception as e:
        logger.warning(f"CAPI purchase dispatch hata order={order_id} src={source}: {e}")
        return False


@router.put("/{order_id}/status")
async def update_order_status(
    order_id: str,
    status: str = Query(...),
    expected_status: Optional[str] = Query(None, max_length=64),
    current_user: dict = Depends(require_permission("orders.update_status"))
):
    """Update order status (RBAC: orders.update_status)

    COM08 — KARŞILAŞTIR-VE-YAZ: güncelleme, okunan GÜNCEL statü filtreye konarak yapılır; arada
    başka bir işlem (eşzamanlı sevk/iptal/ödeme onayı) statüyü değiştirdiyse 409 döner — iki
    personelin aynı anda "Kargoya verildi" ve "İptal" basması geçersiz birleşim üretmez.
    İstemci ekranda gördüğü statüyü `expected_status` ile gönderirse o da doğrulanır."""
    if not isinstance(expected_status, str):   # doğrudan Python çağrısı (bulk) → Query nesnesi
        expected_status = None
    from order_statuses import get_status_config, valid_keys
    _cfg0 = await get_status_config(db)
    valid_statuses = valid_keys(_cfg0)
    if status not in valid_statuses:
        raise HTTPException(status_code=400, detail=f"Geçersiz durum. Geçerli değerler: {sorted(valid_statuses)}")

    # #16: MOR sipariş (ödemesi ALINMIŞ) İPTALİ yalnız Finans (muhasebe) yetkisiyle —
    # ödenmiş siparişi iptale çekmek para iadesi/muhasebe sonucu doğurur.
    if status == "cancelled":
        _od_pay = await db.orders.find_one({"id": order_id}, {"_id": 0, "payment_status": 1, "status": 1})
        if _od_pay and _od_pay.get("payment_status") == "paid" and _od_pay.get("status") != "cancelled":
            from .deps import get_effective_permissions
            _perms = await get_effective_permissions(current_user)
            if "*" not in _perms and "returns.expense_note" not in _perms:
                raise HTTPException(status_code=403,
                    detail="Ödemesi alınmış (mor) siparişin iptali yalnızca Finans (muhasebe) yetkisine sahip kullanıcı tarafından yapılabilir.")
    # #19: "Ödemeli İptal Onaylandı" durumu yalnızca Finans (muhasebe) tarafından seçilebilir.
    if status == "cancel_refunded":
        from .deps import get_effective_permissions
        _perms = await get_effective_permissions(current_user)
        if "*" not in _perms and "returns.expense_note" not in _perms:
            raise HTTPException(status_code=403,
                detail="'Ödemeli İptal Onaylandı' durumu yalnızca Finans (muhasebe) yetkisine sahip kullanıcı tarafından seçilebilir.")

    # DENETİM FIX: reaktivasyon (iptal/iade → aktif fulfillment) stok re-decrement'i için
    # ÖNCEKİ durumu güncellemeden ÖNCE yakala.
    _prev_doc = await db.orders.find_one({"id": order_id}, {"_id": 0, "status": 1, "chargeback_open": 1})
    if not _prev_doc:
        raise HTTPException(status_code=404, detail="Sipariş bulunamadı")
    _prev_status = (_prev_doc or {}).get("status")
    if expected_status and expected_status != (_prev_status or ""):
        raise HTTPException(status_code=409, detail=(
            f"Sipariş durumu bu arada değişti (şu an: {_prev_status or '-'}). Sayfayı yenileyip tekrar deneyin."))
    # COM11 / FIN-E03: açık ödeme itirazı (chargeback) varken sipariş iade-ödendi statülerine
    # ÇEKİLEMEZ (ikinci para hareketi). Süper admin gerekçeli istisna yalnız iade uçlarında.
    if status in ("refunded", "partial_refunded", "cancel_refunded"):
        # ACİL ANAHTAR (CHAIN08): elle "iade ödendi" işaretlemesi de bir para-iadesi kaydıdır →
        # freeze_refunds / restore_mode / geri yükleme sonrası iade kilidi açıkken 423.
        await _guard_switch("freeze_refunds")
        from .chargebacks import assert_refund_allowed
        await assert_refund_allowed(order_id, current_user, None, action=f"order.status.{status}")

    _now = datetime.now(timezone.utc).isoformat()
    _set = {"status": status, "updated_at": _now}
    if status == "return_approved":
        _set["return_approved_at"] = _now
    if status in ("refunded", "partial_refunded"):
        _set["refund_paid_at"] = _now
    # ELLE (admin) bir İADE durumuna çekilen sipariş: 'manuel öncelik' bayrağı → pazaryeri
    # siparişi olsa bile İadeler sayfasında görünür (aksi halde Trendyol/HB filtrelenip kayboluyordu).
    _RETURN_STS = {"return_requested", "return_approved", "return_rejected",
                   "return_in_transit", "returned", "refunded", "partial_refunded"}
    if status in _RETURN_STS:
        _set["manual_return"] = True
        _set["manual_return_at"] = _now
    else:
        # İade dışı bir duruma dönerse manuel iade bayrağı kalkar (tutarlılık).
        _set["manual_return"] = False

    # "Sipariş Onaylandı" (confirmed) → havale/EFT siparişinde ödeme bildirimini de
    # OTOMATİK onayla: payment_status=paid. Sipariş içine girmeden listeden onaylayınca
    # içerideki ödeme onayı tetiklenir, fatura/kargo kilidi açılır. Kapıda ödeme HARİÇ
    # (teslimde ödenir), kredi kartı zaten 'paid' olduğundan etkilenmez.
    _confirm_paid = False
    _od_pre = None
    if status == "confirmed":
        _od_pre = await db.orders.find_one(
            {"id": order_id},
            {"_id": 0, "payment_method": 1, "payment_status": 1, "order_number": 1,
             "paid_with_gift_card_only": 1, "imported_from": 1, "platform": 1, "marketplace": 1},
        )
        if _od_pre:
            _pm = (_od_pre.get("payment_method") or "").lower()
            if _pm in _HAVALE_PMS and _od_pre.get("payment_status") != "paid":
                _set["payment_status"] = "paid"
                _set["paid_at"] = _now
                _set["paid_by"] = current_user.get("email", "")
                _set["payment_approved_via"] = "status_confirm"
                _confirm_paid = True
            # DEĞİŞMEZ #1 KORUMASI (W11874): KART siparişi, ödeme GERÇEKTEN alınmadan listeden
            # "Onaylandı"ya çekilemez. Kök sebep: kartla başlayıp ödemesi başarısız olan sipariş
            # (payment_status=failed, iyzico kaydı YOK) personel tarafından dropdown'dan onaylanınca
            # panelde ödenmiş gibi görünüyor ve ödeme yöntemi "Kredi Kartı" kalıyordu — oysa müşteri
            # parayı HAVALE ile göndermişti. Havale/EFT ve kapıda ödeme bu kapıdan ETKİLENMEZ.
            elif (_pm in _CARD_PMS
                  and str(_od_pre.get("payment_status") or "").lower() not in _SETTLED_PAY
                  and not _od_pre.get("paid_with_gift_card_only")
                  # Aktarılmış geçmiş (Ticimax) / pazaryeri siparişleri bu sistemin ödeme akışından
                  # geçmedi; onların statüsü serbestçe düzeltilebilir olmalı.
                  and not _od_pre.get("imported_from")
                  and _is_site_order(_od_pre)):
                raise HTTPException(
                    status_code=409,
                    detail=("Bu kart siparişinin ödemesi alınmamış (durum: "
                            f"{_od_pre.get('payment_status') or 'ödeme yok'}) — 'Onaylandı' yapılamaz. "
                            "Müşteri parayı HAVALE/EFT ile gönderdiyse önce siparişi düzenleyip ödeme "
                            "yöntemini Havale/EFT yapın; kartla tahsil edildiyse 'Ödemeyi Onayla' "
                            "(mark-paid) işlemini kullanın — her ikisi de kayda geçer."))

    # COM08: karşılaştır-ve-yaz — okunan statü değişmediyse yaz (eşzamanlı sevk/iptal yarışı).
    _cas = {"id": order_id, "status": _prev_status} if _prev_status is not None else \
        {"id": order_id, "status": {"$in": [None]}}
    result = await db.orders.update_one(_cas, {"$set": _set})

    if result.matched_count == 0:
        if await db.orders.count_documents({"id": order_id}, limit=1):
            raise HTTPException(status_code=409, detail=(
                "Sipariş durumu eşzamanlı başka bir işlemle değişti. Sayfayı yenileyip tekrar deneyin."))
        raise HTTPException(status_code=404, detail="Sipariş bulunamadı")

    # İPTAL TARİHİNİ DONDUR: rapor bu tarihi kullanıyor; yoksa updated_at'e düşüp
    # her senkronda kayıyordu. Koşullu yazılır, mevcut tarihi ezmez.
    if status in _CANCEL_ST_FOR_STAMP:
        await stamp_cancelled_at(order_id=order_id, when=_now, source="admin_status")
    await _log_order_event(order_id, "status", f"Durum güncellendi: {status}", current_user, {"status": status})
    if _confirm_paid:
        await _log_order_event(
            order_id, "payment",
            "Ödeme onaylandı (sipariş onayı ile otomatik · havale/EFT)",
            current_user, {"payment_status": "paid", "via": "status_confirm"},
            order_number=(_od_pre or {}).get("order_number", ""),
        )

    # ---- FATURA İPTALİ (Doğan e-Arşiv) ----------------------------------------
    # Sipariş iptal edildiyse ve Doğan'dan e-Arşiv fatura kesilmişse, faturayı da
    # Doğan üzerinden iptal etmeyi dener. Best-effort: durum güncellemesini ASLA
    # bloklamaz/bozmaz. e-Fatura iptali farklı bir süreç (alıcı onayı / iptal
    # faturası) olduğundan otomatik iptal edilmez, manuel olarak işaretlenir.
    if status == "cancelled":
        try:
            _od = await db.orders.find_one({"id": order_id}, {"_id": 0})
            if (_od and _od.get("invoice_issued") and (_od.get("invoice_provider") == "dogan")
                    and not _od.get("invoice_cancelled")):
                _itype = str(_od.get("invoice_type") or "").lower()
                if _itype in ("e-arsiv", "earsiv", "e-arşiv"):
                    _ds = await db.settings.find_one({"id": "dogan_edonusum"}, {"_id": 0}) or {}
                    if _ds.get("enabled") and _ds.get("username"):
                        from dogan_client import DoganClient as _DC
                        from fastapi.concurrency import run_in_threadpool as _rtp
                        _cli = _DC(username=_ds["username"], password=_ds["password"],
                                   is_test=_ds.get("is_test", True))
                        _cancel = await _rtp(
                            _cli.cancel_earsiv_invoice,
                            invoice_uuid=_od.get("invoice_uuid", "") or "",
                            invoice_id=(_od.get("invoice_number") or _od.get("invoice_dogan_id") or ""),
                            reason=f"Sipariş iptal edildi ({_od.get('order_number') or order_id})",
                            total_amount=_od.get("total"),
                        )
                        try:
                            await _rtp(_cli.logout)
                        except Exception:
                            pass
                        await db.orders.update_one({"id": order_id}, {"$set": {
                            "invoice_cancelled": bool(_cancel.get("success")),
                            "invoice_cancel_result": _cancel,
                            "invoice_cancel_attempted_at": _now,
                        }})
                        await _log_order_event(
                            order_id, "invoice",
                            ("Doğan e-Arşiv fatura iptal edildi"
                             if _cancel.get("success")
                             else f"Doğan fatura iptali başarısız: {_cancel.get('message') or _cancel.get('error') or ''}"),
                            current_user, {"cancel": _cancel})
                else:
                    await db.orders.update_one({"id": order_id}, {"$set": {
                        "invoice_cancel_needs_manual": True,
                        "invoice_cancel_attempted_at": _now,
                    }})
                    await _log_order_event(
                        order_id, "invoice",
                        "e-Fatura iptali manuel yapılmalı (Doğan portalı / iptal faturası).",
                        current_user, {})
        except Exception as _ic:
            logger.error(f"[dogan invoice cancel {order_id}] {_ic}")

    # Bildirim tetikleme: status → event eşlemesi
    # NOT: Provider'lara gidiş yavaş olabilir — fire-and-forget task ile UI yanıtını
    # bloklamadan arka planda tetikliyoruz.
    import asyncio as _asyncio
    async def _dispatch_notif():
        try:
            order_doc = await db.orders.find_one({"id": order_id}, {"_id": 0})
            if not order_doc:
                return
            import sys, os
            sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
            from notification_service import send_notification
            from order_statuses import event_for_cfg, get_status_config, customer_label_for_cfg
            _cfg = await get_status_config(db)
            ev = event_for_cfg(status, _cfg)
            if not ev:
                return
            _nz = (_cfg.get("notify") or {}).get(status) or {}
            _channels = [c for c in ("sms", "email") if _nz.get(c)]
            # İPTAL: Müşteri ödeme yaptıysa iptal edildiğini (ve iade sürecini) MUTLAKA
            # bilmeli. Manuel iptalde iptal SMS'i, Ayarlar toggle'ından BAĞIMSIZ her zaman
            # gider. (Otomatik iptaller bu uçtan geçmez; yalnız admin panelinden manuel
            # durum değişimini kapsar.)
            if status == "cancelled" and "sms" not in _channels:
                _channels.append("sms")
            if not _channels:
                return  # bu durum icin bildirim kapali (Ayarlar > Siparis Durumlari)
            addr = order_doc.get("shipping_address") or {}
            _bank = {}
            if status == "awaiting_payment":
                _pay = await db.settings.find_one({"id": "payment"}, {"_id": 0}) or {}
                _banks = _pay.get("bank_accounts") or []
                _bank = next((b for b in _banks if b.get("is_default")), None) or (_banks[0] if _banks else {})
            _base = os.environ.get("FRONTEND_PUBLIC_URL") or os.environ.get("REACT_APP_BACKEND_URL") or ""
            # Kargo takip linki — gerçek deep-link önceliği (cargo_tracking_link),
            # yoksa eski alanlara düş. {tracking_link} ve {tracking_url} ikisi de bunu alır.
            _track_link = (order_doc.get("cargo_tracking_link")
                           or order_doc.get("cargo_tracking_url")
                           or order_doc.get("tracking_url") or "")
            variables = await _order_notify_vars(order_doc, status_label=customer_label_for_cfg(status, _cfg))
            await send_notification(
                db, ev,
                to_phone=addr.get("phone") or order_doc.get("phone"),
                to_email=addr.get("email") or order_doc.get("email"),
                variables=variables,
                channels=_channels,
            )
        except Exception as _notif_err:
            logger.warning(f"notification dispatch failed for order {order_id}: {_notif_err}")

    _spawn(_dispatch_notif())

    # ---- CAPI Server-Side Tracking Hooks ----
    # Status değişimleri reklam platformlarına da bildirilir.
    async def _dispatch_capi():
        try:
            order_doc = await db.orders.find_one({"id": order_id}, {"_id": 0})
            if not order_doc:
                return
            # Map status → CAPI event_name
            capi_event = None
            value_multiplier = 1.0
            if status == "confirmed" and order_doc.get("payment_status") == "paid":
                # Purchase TEK merkezden gönderilir (idempotent bayrak +
                # event_id = sipariş no → tarayıcı pixel'iyle dedup).
                await dispatch_purchase_capi(order_id, source="admin_status_confirmed")
                return
            elif status == "cancelled" and order_doc.get("payment_status") == "paid":
                capi_event = "refund"
                value_multiplier = -1.0
            if not capi_event:
                return
            # KVKK: consent gate AÇIKSA reklam izni olmayan siparişin iade/iptal olayı da GÖNDERİLMEZ
            # (Purchase yolundaki kapının aynısı; gate kapalıyken davranış değişmez).
            try:
                from business_rules import get_rule as _get_rule_cg
                if bool(await _get_rule_cg(db, "marketing.capi_consent_gate", False)) \
                        and order_doc.get("ad_tracking_consent") is not True:
                    return
            except Exception:
                return
            from services.capi.orchestrator import dispatch_event
            addr = order_doc.get("shipping_address") or {}
            # CAPI event_source_url — marka alan adi (env/panel), sabit degil.
            import os as _os_c2
            import company as _company_c2
            _site_url = ((await _company_c2.get_company(db)).get("site_url")
                         or _os_c2.environ.get("SITE_URL") or "").rstrip("/")
            user_data_kwargs = {
                "email": addr.get("email") or order_doc.get("email"),
                "phone": addr.get("phone") or order_doc.get("phone"),
                "first_name": addr.get("first_name") or (addr.get("full_name") or "").split(" ")[0],
                "last_name": addr.get("last_name") or " ".join((addr.get("full_name") or "").split(" ")[1:]),
                "city": addr.get("city"),
                "state": addr.get("district") or addr.get("state"),
                "country": addr.get("country") or "TR",
                # KVKK: açık adres / posta kodu reklam platformuna gönderilmez.
                "zipcode": None,
                "street": None,
                "date_of_birth": (order_doc.get("customer") or {}).get("date_of_birth"),
                "gender": (order_doc.get("customer") or {}).get("gender"),
                "external_id": order_doc.get("customer_id") or order_doc.get("user_id"),
                # Purchase yolundaki eşleşme alanlarını buraya da geçir (refund/status eventinde
                # IP/UA/fbp/fbc eksikti → EMQ düşüktü). order.click_ids + customer_ip/user_agent.
                "client_ip": order_doc.get("customer_ip"),
                "user_agent": order_doc.get("user_agent"),
                "fbp": (order_doc.get("click_ids") or {}).get("fbp"),
                "fbc": (order_doc.get("click_ids") or {}).get("fbc"),
            }
            from services.capi.hash_utils import build_user_data
            user_data = build_user_data(**user_data_kwargs)
            items_payload = []
            for it in (order_doc.get("items") or []):
                items_payload.append({
                    "item_id": str(it.get("product_id") or it.get("sku") or ""),
                    "item_name": it.get("name") or "",
                    "item_brand": it.get("brand") or "",
                    "item_variant": f"{it.get('size','')} {it.get('color','')}".strip(),
                    "price": float(it.get("unit_price") or it.get("price") or 0),
                    "quantity": int(it.get("quantity") or 1),
                })
            await dispatch_event(
                db,
                event_name=capi_event,
                event_id=f"{order_id}-{capi_event}",   # dedup by order
                user_data=user_data,
                event_payload={
                    "currency": order_doc.get("currency") or "TRY",
                    "value": float(order_doc.get("total") or 0) * value_multiplier,
                    "items": items_payload,
                    "order_id": order_doc.get("order_number") or order_id,
                    "coupon": order_doc.get("coupon_code"),
                },
                event_source_url=_site_url,
            )
        except Exception as _capi_err:
            logger.warning(f"CAPI dispatch failed for order {order_id}: {_capi_err}")

    _spawn(_dispatch_capi())

    # FAZ 1 - C1: Status değişikliğinde stok düzenlemesi
    try:
        order_doc = await db.orders.find_one({"id": order_id}, {"_id": 0})
        prev_status = None  # we don't have a before-value; rely on status flip idempotence
        if status == "cancelled":
            # Y5: Hareket-tipi bağımsız idempotent iade — auto-cancel ya da havale-auto-cancel
            # daha önce iade ettiyse ikinci kez stok EKLENMEZ (önceden yalnızca 'order_cancelled'
            # tipine bakıldığı için çift iade oluyordu).
            await _restock_order_once(order_doc, "order_cancelled")
        else:
            # DENETİM FIX (oversell): iptal/iade grubundan AKTİF fulfillment durumuna geri
            # çekilince stok TEKRAR DÜŞÜLMELİ. Eskiden düşülmediği için iade edilmiş +1 stok
            # üzerinde kalıp aynı ürün ikinci kez satılabiliyordu. İdempotent: yalnız daha önce
            # restock EDİLDİYSE (restore hareketi var) bir kez düş, sonra restore hareketini sil.
            _ACTIVE_FULFILL = {"confirmed", "preparing", "processing", "ready_to_ship",
                               "shipped", "in_transit", "out_for_delivery", "delivered"}
            # B5/B8: iade/iptal sonrası tekrar aktif fulfillment'a çekilince, DAHA ÖNCE İADE
            # EDİLEN miktarı geri düş — tüm siparişi değil, yalnız gerçekten restock edilmiş
            # kalem/adet kadar (kısmi iade simetrisi). Tetik: restore hareketi VARLIĞI (statü
            # grubuna bağlı DEĞİL → payment_failed/expired de kapsanır; eskiden bu statülerde
            # re-decrement hiç tetiklenmeyip stok düşmeden sevk = oversell oluyordu).
            if status in _ACTIVE_FULFILL and order_doc:
                _restore_items = []
                async for _rmv in db.stock_movements.find(
                        {"order_id": order_id, "type": {"$in": list(_RESTORE_MOVE_TYPES)}},
                        {"_id": 0, "items": 1}):
                    for _it in (_rmv.get("items") or []):
                        _d = int(_it.get("delta") or 0)
                        if _d > 0:
                            _lvl = await _resolve_move_level(_it)
                            _restore_items.append({
                                "barcode": _it.get("barcode") or "", "product_id": _it.get("product_id"),
                                "variant_id": _it.get("variant_id"), "level": _lvl, "delta": _d,
                            })
                if _restore_items:
                    # _reverse_stock_moves: back = -delta → delta>0 verildiğinden İADE EDİLENİ geri DÜŞER.
                    await _reverse_stock_moves(_restore_items)
                    # Bayat restore hareketini SİLME → 'tüketildi' işaretle (guard yine görmez ama
                    # stok geçmişi KORUNUR). Öncesinde delete_many ile siliniyordu (log kaybı).
                    await _consume_restore_moves(order_id, "reactivate_decrement")
                    await db.stock_movements.insert_one({
                        "id": str(uuid.uuid4()),
                        "type": "reactivate_decrement",
                        "order_id": order_id,
                        "order_number": order_doc.get("order_number", ""),
                        "items": [{**_ri, "delta": -_ri["delta"]} for _ri in _restore_items],
                        "created_at": datetime.now(timezone.utc).isoformat(),
                    })
    except Exception as stock_err:
        logger.error(f"Stock restore/re-decrement on status change failed: {stock_err}")

    # İptal (normal veya 'İptal Ödemesi Yapıldı'): kupon hakkı serbest bırakılır —
    # müşteri hoş geldin/tek kullanımlık kodunu yeni siparişte tekrar kullanabilir.
    if status in ("cancelled", "cancel_refunded"):
        try:
            await release_order_redemptions(order_id)
        except Exception as _cr_err:
            logger.warning(f"[coupon] statü iptalinde serbest bırakma başarısız ({order_id}): {_cr_err}")

    return {"message": f"Sipariş durumu '{status}' olarak güncellendi"}

@router.put("/{order_id}/mark-paid")
async def mark_order_paid(
    order_id: str,
    current_user: dict = Depends(require_permission("orders.update_status"))
):
    """Havale/EFT ödemesi manuel onaylama.
    payment_status='paid' yapar ve CAPI'ye offline 'purchase' event'i gönderir.
    """
    order_doc = await db.orders.find_one({"id": order_id}, {"_id": 0})
    if not order_doc:
        raise HTTPException(status_code=404, detail="Sipariş bulunamadı")
    # CLAUDE.md değişmez #1 (denetim 2026-09-29): KART siparişi yalnız iyzico onayıyla 'paid' olur;
    # elle işaretlenemez (yarıda kalan 3DS siparişi ödemesiz sevk edilmesin). Parası çekilmiş kart
    # siparişini mutabakat cron'u (15 dk) iyzico'dan doğrulayıp kendisi 'paid' yapar.
    _pm = str(order_doc.get("payment_method") or "").strip().lower()
    if _pm in {"credit_card", "card", "kredi_karti", "kart", "iyzico", "creditcard"}:
        raise HTTPException(status_code=400, detail=(
            "Kart siparişi elle 'ödendi' yapılamaz — yalnız iyzico onayıyla ödenmiş olur. Para çekildiyse "
            "sistem 15 dakika içinde iyzico'dan doğrulayıp siparişi otomatik onaylar."))
    if order_doc.get("payment_status") == "paid":
        return {"message": "Sipariş zaten ödenmiş olarak işaretli.",
                "order_id": order_id, "already_paid": True}

    now_iso = datetime.now(timezone.utc).isoformat()
    _set = {
        "payment_status": "paid",
        "paid_at": now_iso,
        "paid_by": current_user.get("email", ""),
        "updated_at": now_iso,
    }
    _flip_confirmed = order_doc.get("status") in ("awaiting_payment", "payment_notified", "pending")
    if _flip_confirmed:
        _set["status"] = "confirmed"
    await db.orders.update_one({"id": order_id}, {"$set": _set})

    await _log_order_event(order_id, "payment", "Ödeme onaylandı (manuel/havale)", current_user,
                           {"payment_status": "paid", "status_flipped": _flip_confirmed},
                           order_number=order_doc.get("order_number", ""))

    # Havale/EFT ONAYINDA "Ödemenizi Aldık" bildirimi (kullanıcı isteği): personel ödemeyi görüp
    # onaylayınca müşteriye ödemenin alındığı + siparişin onaylandığı SMS'i gider. Kanal seçimi
    # Sipariş Durumları'ndaki 'confirmed' ayarından; şablon = order_payment_approved.
    if _flip_confirmed:
        async def _notify_payment_approved():
            try:
                import sys, os
                sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
                from notification_service import send_notification
                from order_statuses import get_status_config, customer_label_for
                cfg = await get_status_config(db)
                nz = (cfg.get("notify") or {}).get("confirmed") or {}
                ch = [c for c in ("sms", "email") if nz.get(c)]
                if not ch:
                    return
                addr = order_doc.get("shipping_address") or {}
                await send_notification(
                    db, "order_payment_approved",
                    to_phone=addr.get("phone") or order_doc.get("phone"),
                    to_email=addr.get("email") or order_doc.get("email"),
                    variables=await _order_notify_vars(order_doc, status_label=customer_label_for("confirmed")),
                    channels=ch,
                )
            except Exception as e:
                logger.warning(f"payment_approved notif failed: {e}")
        _spawn(_notify_payment_approved())

    # CAPI offline conversion (purchase) — merkezi helper (idempotent,
    # event_id = sipariş no; havale/EFT onayında da tek purchase çıkar)
    async def _capi_offline_purchase():
        await dispatch_purchase_capi(order_id, source="offline_payment_confirm")

    _spawn(_capi_offline_purchase())

    return {"message": "Ödeme onaylandı, CAPI offline conversion tetiklendi.",
            "order_id": order_id, "payment_status": "paid"}


@router.delete("/{order_id}")
async def delete_order(
    order_id: str,
    current_user: dict = Depends(require_permission("orders.delete"))
):
    """Siparişi sil — fiziksel silmeden ÖNCE orders_deleted arşivine taşı. (RBAC: orders.delete)
    Böylece 'Silinen Siparişler' sayfasından görülüp geri alınabilir; ana orders
    sorguları/raporları/dashboard hiç etkilenmez."""
    order = await db.orders.find_one({"id": order_id}, {"_id": 0})
    if not order:
        raise HTTPException(status_code=404, detail="Sipariş bulunamadı")
    order["deleted_at"] = datetime.now(timezone.utc).isoformat()
    order["deleted_by"] = current_user.get("email", "")
    # DENETİM (bizlogic Y3): stoğu DÜŞÜLMÜŞ aktif bir sipariş silinince stok geri eklenmiyordu →
    # kalıcı stok sızıntısı. Aktif fulfillment durumundaysa stoğu geri ekle + arşive işaretle
    # (restore'da tekrar düşülür → simetri). Terminal (iptal/iade) siparişte stok zaten iade edilmiş.
    _ACTIVE_STOCK = {"awaiting_payment", "payment_notified", "pending", "confirmed",
                     "processing", "preparing", "shipped", "undelivered"}
    _returned_on_delete = False
    try:
        if str(order.get("status") or "") in _ACTIVE_STOCK:
            # Guard listesi _RESTORE_MOVE_TYPES'tan DAR idi (manual_increment,
            # backfill_increment, order_returned eksikti) → iade ile geri stoklanmış
            # bir sipariş silinince İKİNCİ kez stok ekleniyordu.
            _already = await db.stock_movements.find_one(
                {"order_id": order_id,
                 "type": {"$in": list(_RESTORE_MOVE_TYPES) + ["order_deleted_restock"]}},
                {"_id": 1})
            # FAZ 2: hiç düşülmemiş siparişi silmek stok YARATMAMALI.
            if not _already and not await _order_was_deducted(order):
                logger.warning(f"[stok] silme: sipariş {order_id} stoktan hiç düşülmemiş "
                               f"— stok eklenmedi")
                await _log_restock_skip(order, "order_deleted_restock", "delete_order")
                _already = True
            if not _already:
                _moves = await _stock_delta_for_order(order, +1)
                await db.stock_movements.insert_one({
                    "id": generate_id(), "type": "order_deleted_restock",
                    "order_id": order_id, "order_number": order.get("order_number", ""),
                    "items": _moves, "source": "delete_order",
                    "created_at": datetime.now(timezone.utc).isoformat()})
                _returned_on_delete = True
    except Exception as _e:
        logger.error(f"[delete restock {order_id}] {_e}")
    order["_stock_returned_on_delete"] = _returned_on_delete
    try:
        await db.orders_deleted.replace_one({"id": order_id}, _pii_seal(order), upsert=True)  # KVKK: düz TCKN kopyalanmaz
    except Exception as _e:
        logger.warning(f"archive deleted order failed {order_id}: {_e}")
    await db.orders.delete_one({"id": order_id})
    await _log_order_event(order_id, "delete", "Sipariş silindi (arşive taşındı)", current_user, {},
                           order_number=order.get("order_number", ""))
    return {"message": "Sipariş silindi", "archived": True}


@router.post("/{order_id}/restore")
async def restore_deleted_order(
    order_id: str,
    current_user: dict = Depends(require_permission("orders.edit"))
):
    """Arşivlenen (silinen) siparişi orders'a geri taşır."""
    arch = await db.orders_deleted.find_one({"id": order_id}, {"_id": 0})
    if not arch:
        raise HTTPException(status_code=404, detail="Silinen sipariş bulunamadı")
    arch.pop("deleted_at", None)
    arch.pop("deleted_by", None)
    # Y3 simetri: silmede stok geri eklendiyse, geri alırken TEKRAR düş (aksi halde restore stok şişirir).
    _re_decrement = bool(arch.pop("_stock_returned_on_delete", False))
    now_iso = datetime.now(timezone.utc).isoformat()
    arch["updated_at"] = now_iso
    arch["restored_at"] = now_iso
    arch["restored_by"] = current_user.get("email", "")
    if _re_decrement:
        try:
            _rd_moves = await _stock_delta_for_order(arch, -1)
            # DEFTER (Faz 1): hareket SİLİNMEZ. Sistemin geri kalanı bayat restore kaydını
            # silmek yerine '..._consumed' diye işaretliyor (bkz. _consume_restore_moves);
            # burada delete_many ile geçmiş yok ediliyordu. Ayrıca yeniden-düşümün kendisi
            # deftere yazılmıyordu → "defter = canlı stok" denkliği bozuluyordu.
            await _consume_restore_moves(order_id, "restore_deleted_order")
            try:
                if _rd_moves:
                    await db.stock_movements.insert_one({
                        "id": str(uuid.uuid4()), "type": "restore_deleted_redecrement",
                        "order_id": order_id,
                        "order_number": arch.get("order_number", ""),
                        "items": _rd_moves, "source": "order_restore",
                        "created_by": current_user.get("email", ""),
                        "created_at": now_iso,
                    })
            except Exception as _mv2:
                logger.warning(f"[restore] hareket kaydı yazılamadı {order_id}: {_mv2}")
        except Exception as _e:
            logger.error(f"[restore re-decrement {order_id}] {_e}")
    await db.orders.replace_one({"id": order_id}, _pii_seal(arch), upsert=True)  # KVKK: düz TCKN yazılmaz
    await db.orders_deleted.delete_one({"id": order_id})
    await _log_order_event(order_id, "status", "Sipariş geri alındı (arşivden)", current_user, {},
                           order_number=arch.get("order_number", ""))
    return {"message": "Sipariş geri alındı", "restored": True}


# ==================== FAZ 1: STOCK AUTO-FLOW + AUTO-CANCEL + NOTES ====================

async def _push_stock_to_marketplaces(product_ids: list) -> None:
    """Site satışı stoğu düşürünce ETKİLENEN ürünlerin GÜNCEL stoğunu Trendyol + Hepsiburada'ya
    ANINDA (hedefli) bas. Periyodik senkronu (~dk) beklemeden pazaryeri oversell penceresini
    saniyelere indirir. Best-effort: konfig yok/hata → sessiz atlanır, sipariş akışını bloklamaz.
    YALNIZ stok gönderilir (fiyata dokunulmaz)."""
    pids = [p for p in dict.fromkeys(product_ids or []) if p]
    if not pids:
        return
    try:
        prods = await db.products.find({"id": {"$in": pids}}, {"_id": 0}).to_list(length=None)
    except Exception:
        return
    if not prods:
        return
    # --- Trendyol ---
    try:
        from routes.integrations import _sync_inventory_to_trendyol, get_trendyol_config
        cfg = await get_trendyol_config()
        if cfg and cfg.get("is_active"):
            await _sync_inventory_to_trendyol(prods)
    except Exception as e:
        logger.warning(f"[stok-push] Trendyol anlık stok push hatası: {e}")
    # --- Hepsiburada (yalnız stok) ---
    try:
        from .category_mapping import _get_hb_client
        from .integrations_hepsiburada import (
            _hb_push_stock_price, _hb_listing_items_from_product,
            _hb_markup, _hb_price_source, _hb_sku_source)
        client, err = await _get_hb_client()
        if client and not err:
            markup = await _hb_markup()
            psrc = await _hb_price_source()
            ssrc = await _hb_sku_source()
            items = []
            for p in prods:
                items.extend(_hb_listing_items_from_product(p, markup, psrc, ssrc) or [])
            if items:
                await _hb_push_stock_price(client, items, do_price=False, do_stock=True)
    except Exception as e:
        logger.warning(f"[stok-push] Hepsiburada anlık stok push hatası: {e}")


async def _reverse_stock_moves(moves: list) -> None:
    """A2.5b geri-alma: verilen düşüm hareketlerini (delta<0) simetrik +qty ile geri ekler.
    Yalnız oversell reddinde / insert hatasında çağrılır (kısmi düşülen stok sızmasın)."""
    now = datetime.now(timezone.utc).isoformat()
    for mv in moves or []:
        bc = mv.get("barcode")
        delta = int(mv.get("delta") or 0)
        pid = mv.get("product_id")
        vid = mv.get("variant_id")
        level = mv.get("level")
        if delta == 0:
            continue
        back = -delta  # düşüm delta<0 → back>0 (geri ekle)
        # Barkodsuz varyant: variant_id ile geri al (barcode boş olabilir).
        if level == "variant_by_id" and vid and pid:
            await db.products.update_one(
                {"id": pid, "variants.id": vid},
                {"$inc": {"variants.$[v].stock": back}, "$set": {"updated_at": now}},
                array_filters=[{"v.id": vid}])
            await db.products.update_one(
                {"id": pid},
                [{"$set": {"stock": {"$sum": {"$map": {
                    "input": {"$ifNull": ["$variants", []]}, "as": "vv",
                    "in": {"$toInt": {"$ifNull": ["$$vv.stock", 0]}}}}}}}])
            continue
        if level == "product":
            if not (pid or bc):
                continue
            await db.products.update_one({"id": pid} if pid else {"barcode": bc},
                                         {"$inc": {"stock": back}, "$set": {"updated_at": now}})
        else:
            if not bc:
                continue
            await db.products.update_one(
                {"variants.barcode": bc},
                {"$inc": {"variants.$[v].stock": back}, "$set": {"updated_at": now}},
                array_filters=[{"v.barcode": bc}])
            if pid:
                await db.products.update_one(
                    {"id": pid},
                    [{"$set": {"stock": {"$sum": {"$map": {
                        "input": {"$ifNull": ["$variants", []]}, "as": "vv",
                        "in": {"$toInt": {"$ifNull": ["$$vv.stock", 0]}}}}}}}])


async def _decrement_stock_atomic(order: dict) -> dict:
    """A2.5b — OVERSELL ENGELLE. Her kalem için stok yeterliyse KOŞULLU atomik $inc ile düşer.
    Bir kalem bile karşılanamıyorsa şimdiye kadar düşürülenleri GERİ ALIR ve
    {"success": False, "barcode", "name"} döndürür → çağıran siparişi 409 ile reddeder
    (karşılanamayacak sipariş hiç oluşmaz). Barkodu çözülemeyen / stok alanı olmayan kalem
    kontrol edilemez → geçirilir (mevcut davranış korunur, movement yazılmaz).
    Başarıda {"success": True, "movements": [...]} döner (stok_movements kaydı için)."""
    items = order.get("items") or order.get("lines") or []
    applied = []  # geri-alma için düşürülen hareketler
    now = datetime.now(timezone.utc).isoformat()
    for it in items:
        barcode = it.get("barcode") or it.get("sku") or ""
        qty = int(it.get("quantity", 1) or 1)
        if qty < 1:
            continue
        if barcode:
            # 1) Varyant düzeyinde KOŞULLU düşüm: yalnız stock>=qty ise eşleşir ve düşer.
            res = await db.products.update_one(
                {"variants": {"$elemMatch": {"barcode": barcode, "stock": {"$gte": qty}}}},
                {"$inc": {"variants.$[v].stock": -qty}, "$set": {"updated_at": now}},
                array_filters=[{"v.barcode": barcode}])
            if res.modified_count > 0:
                prod = await db.products.find_one({"variants.barcode": barcode}, {"_id": 0, "id": 1})
                pid = prod["id"] if prod else None
                if pid:
                    await db.products.update_one(
                        {"id": pid},
                        [{"$set": {"stock": {"$sum": {"$map": {
                            "input": {"$ifNull": ["$variants", []]}, "as": "vv",
                            "in": {"$toInt": {"$ifNull": ["$$vv.stock", 0]}}}}}}}])
                mv = {"barcode": barcode, "delta": -qty, "product_id": pid, "level": "variant"}
                applied.append(mv)
                continue
            # Varyant var ama stok yetmedi mi? (yoksa hiç varyant yok mu?) — ayırt et.
            if await db.products.find_one({"variants.barcode": barcode}, {"_id": 1}):
                await _reverse_stock_moves(applied)  # oversell → geri al + reddet
                return {"success": False, "barcode": barcode, "name": it.get("name", "")}
            # 2) Varyantsız ürün düzeyinde barkod: aynı koşullu düşüm.
            res2 = await db.products.update_one(
                {"barcode": barcode, "stock": {"$gte": qty}},
                {"$inc": {"stock": -qty}, "$set": {"updated_at": now}})
            if res2.modified_count > 0:
                p2 = await db.products.find_one({"barcode": barcode}, {"_id": 0, "id": 1})
                applied.append({"barcode": barcode, "delta": -qty,
                                "product_id": (p2["id"] if p2 else None), "level": "product"})
                continue
            if await db.products.find_one({"barcode": barcode}, {"_id": 1}):
                await _reverse_stock_moves(applied)  # ürün var, stok yetmedi → oversell
                return {"success": False, "barcode": barcode, "name": it.get("name", "")}
        # 3) BARKODSUZ / barkodu eşleşmeyen kalem — product_id + variant_id (veya beden/renk) ile
        #    stok anahtarını çöz. Barkodu boş varyantlar da oversell'e karşı korunur (aksi halde
        #    kalem sessizce geçip stok düşmeden sipariş açılıyordu → stoğu 0 ürüne sipariş).
        pid = it.get("product_id")
        prod = await db.products.find_one({"id": pid}, {"_id": 0}) if pid else None
        if not prod:
            # FAIL-CLOSED: Kalem GERÇEK bir ürünse (product_id VEYA barkod taşıyorsa) ama ürün
            # çözülemiyorsa, stok doğrulanamadığı için siparişi REDDET (oversell sızıntısını kapatır —
            # "stoğu biten üründen sipariş" bug'ının kaynaklarından biri buydu). Yalnız hiçbir kimliği
            # olmayan sanal satır (kargo/hediye paketi vb. — product_id ve barkod YOK) geçilir.
            if pid or barcode:
                await _reverse_stock_moves(applied)
                return {"success": False, "barcode": barcode or "",
                        "name": it.get("name", ""), "reason": "product_unresolved"}
            continue
        variants = prod.get("variants") or []
        vid = it.get("variant_id")
        if variants:
            # Varyantı id ile, yoksa beden(+renk) ile tekil eşle.
            match_v = None
            if vid:
                match_v = next((v for v in variants if v.get("id") == vid), None)
            if not match_v:
                _sz = str(it.get("size") or "").strip().lower()
                _cl = str(it.get("color") or "").strip().lower()
                if _sz:
                    cand = [v for v in variants if str(v.get("size") or "").strip().lower() == _sz]
                    if len(cand) > 1 and _cl:
                        cand = [v for v in cand if str(v.get("color") or "").strip().lower() == _cl] or cand
                    if len(cand) == 1:
                        match_v = cand[0]
            if not match_v:
                # Varyant tekil çözülemedi (barkodsuz/variant_id'siz + aynı bedende birden çok
                # varyant). Normalde yanlış varyantı düşürmemek için geçilirdi → 0 stoklu
                # varyanttan sipariş geçiyordu (OVERSELL). Artık müşterinin verdiği BEDEN(+RENK)
                # ADAYLARINA bakılır: o adaylardan hiçbiri qty'yi karşılamıyorsa (o beden/varyant
                # tükenmiş) REDDEDİLİR → "stoğu 0 olan varyanttan sipariş gelmez". Beden verilmemişse
                # ürünün tüm varyantları aday alınır (hepsi 0 ise reddedilir).
                _sz = str(it.get("size") or "").strip().lower()
                _cl = str(it.get("color") or "").strip().lower()
                _cands = variants
                if _sz:
                    _by_size = [v for v in variants if str(v.get("size") or "").strip().lower() == _sz]
                    _cands = _by_size or variants
                    if _cl:
                        _by_color = [v for v in _cands if str(v.get("color") or "").strip().lower() == _cl]
                        if _by_color:
                            _cands = _by_color
                _cand_max = 0
                for _v in _cands:
                    try:
                        _cand_max = max(_cand_max, int(_v.get("stock") or 0))
                    except Exception:
                        pass
                if _cand_max < qty:
                    await _reverse_stock_moves(applied)
                    return {"success": False, "barcode": "",
                            "name": it.get("name", "") or prod.get("name", "")}
                continue
            _mvid = match_v.get("id")
            res3 = await db.products.update_one(
                {"id": pid, "variants": {"$elemMatch": {"id": _mvid, "stock": {"$gte": qty}}}},
                {"$inc": {"variants.$[v].stock": -qty}, "$set": {"updated_at": now}},
                array_filters=[{"v.id": _mvid}])
            if res3.modified_count > 0:
                await db.products.update_one(
                    {"id": pid},
                    [{"$set": {"stock": {"$sum": {"$map": {
                        "input": {"$ifNull": ["$variants", []]}, "as": "vv",
                        "in": {"$toInt": {"$ifNull": ["$$vv.stock", 0]}}}}}}}])
                applied.append({"barcode": match_v.get("barcode") or "", "variant_id": _mvid,
                                "delta": -qty, "product_id": pid, "level": "variant_by_id"})
                continue
            # Varyant var, stok yetmedi → oversell reddi.
            await _reverse_stock_moves(applied)
            return {"success": False, "barcode": match_v.get("barcode") or "",
                    "name": it.get("name", "") or prod.get("name", "")}
        else:
            # Varyantsız ürün: id ile koşullu düşüm.
            res4 = await db.products.update_one(
                {"id": pid, "stock": {"$gte": qty}},
                {"$inc": {"stock": -qty}, "$set": {"updated_at": now}})
            if res4.modified_count > 0:
                applied.append({"barcode": prod.get("barcode") or "", "delta": -qty,
                                "product_id": pid, "level": "product"})
                continue
            await _reverse_stock_moves(applied)  # ürün var, stok yetmedi → oversell
            return {"success": False, "barcode": prod.get("barcode") or "",
                    "name": it.get("name", "") or prod.get("name", "")}
    return {"success": True, "movements": applied}


async def _stock_delta_for_order(order: dict, delta: int) -> list:
    """Apply stock delta (+1 or -1 per unit) for every order item. Returns movement list."""
    movements = []
    items = order.get("items") or order.get("lines") or []
    for it in items:
        barcode = it.get("barcode") or it.get("sku") or ""
        qty = int(it.get("quantity", 1) or 1)
        if not barcode:
            continue
        # Y4: ATOMİK varyant stok düşümü (arrayFilters $inc — eşzamanlı sipariş kaybı yok).
        # NEGATİF STOK YASAK (kullanıcı kararı): düşüşte önce KOŞULLU dene (stok >= adet);
        # yetmiyorsa 0'a SABİTLE ve uyarı logla/push'la — pazaryeri satışı geri çevrilemez ama
        # stok eksiye sürüklenmez (eksi değer vitrine/rapora yanlış sinyal veriyordu).
        # Aynı barkod hem aktif hem ÇÖP KUTUSUNDAKİ (kopya) üründe olabilir → stok değişimi
        # her zaman silinmemiş ürüne uygulanır; yoksa (yalnız silinmişte varsa) ona.
        prod = await db.products.find_one({"variants.barcode": barcode, "is_deleted": {"$ne": True}}, {"_id": 0, "id": 1, "name": 1}) \
            or await db.products.find_one({"variants.barcode": barcode}, {"_id": 0, "id": 1, "name": 1})
        if prod:
            _now_iso = datetime.now(timezone.utc).isoformat()
            _applied = delta * qty
            if delta < 0:
                _res = await db.products.update_one(
                    {"id": prod["id"], "variants": {"$elemMatch": {"barcode": barcode, "stock": {"$gte": qty}}}},
                    {"$inc": {"variants.$.stock": -qty}, "$set": {"updated_at": _now_iso}},
                )
                if _res.modified_count == 0:
                    # Stok yetersiz (pazaryeri satışı / mutabakat) → 0'a sabitle, negatife inme
                    _doc = await db.products.find_one(
                        {"id": prod["id"], "variants.barcode": barcode}, {"_id": 0, "variants.$": 1})
                    _cur = int(((_doc or {}).get("variants") or [{}])[0].get("stock") or 0)
                    _applied = -max(0, _cur)   # düşülebilen kadar (0'daysa hiç)
                    await db.products.update_one(
                        {"id": prod["id"], "variants.barcode": barcode},
                        {"$set": {"variants.$[v].stock": 0, "updated_at": _now_iso}},
                        array_filters=[{"v.barcode": barcode}],
                    )
                    logger.warning(
                        f"[NEGATİF-STOK ENGELLENDİ] {barcode} ({prod.get('name','')}) stok {_cur} iken "
                        f"{qty} adetlik düşüm geldi (sipariş {order.get('order_number','?')}, "
                        f"platform {order.get('platform','site')}) → 0'a sabitlendi. Pazaryeri stoğu güncel olmayabilir!")
                    try:
                        from .push import send_push_to_admins
                        await send_push_to_admins(
                            "⚠️ Stok 0'dayken satış geldi",
                            f"{prod.get('name','ürün')} ({barcode}) — {order.get('platform','site')} "
                            f"siparişi {order.get('order_number','')}. Stok 0'a sabitlendi; pazaryeri stoklarını kontrol edin.",
                            {"type": "oversold_alert"})
                    except Exception:
                        pass
            else:
                await db.products.update_one(
                    {"id": prod["id"], "variants.barcode": barcode},
                    {"$inc": {"variants.$[v].stock": delta * qty}, "$set": {"updated_at": _now_iso}},
                    array_filters=[{"v.barcode": barcode}],
                )
            # Ana (parent) stok = varyant stoklarının toplamı — tek atomik pipeline update ile.
            await db.products.update_one(
                {"id": prod["id"]},
                [{"$set": {"stock": {"$sum": {"$map": {
                    "input": {"$ifNull": ["$variants", []]},
                    "as": "vv",
                    "in": {"$toInt": {"$ifNull": ["$$vv.stock", 0]}},
                }}}}}],
            )
            _mv = {"barcode": barcode, "delta": _applied, "product_id": prod["id"]}
            if _applied != delta * qty:
                _mv["requested_delta"] = delta * qty
                _mv["oversold"] = True   # stok yetmedi, 0'a sabitlendi
            movements.append(_mv)
        else:
            # Fallback product-level barcode
            p2 = await db.products.find_one({"barcode": barcode}, {"_id": 0, "id": 1, "stock": 1})
            if p2:
                _applied2 = delta * qty
                if delta < 0:
                    _res2 = await db.products.update_one(
                        {"id": p2["id"], "stock": {"$gte": qty}},
                        {"$inc": {"stock": -qty}, "$set": {"updated_at": datetime.now(timezone.utc).isoformat()}})
                    if _res2.modified_count == 0:
                        _cur2 = max(0, int(p2.get("stock") or 0))
                        _applied2 = -_cur2
                        await db.products.update_one(
                            {"id": p2["id"]},
                            {"$set": {"stock": 0, "updated_at": datetime.now(timezone.utc).isoformat()}})
                        logger.warning(f"[NEGATİF-STOK ENGELLENDİ] ürün-düzeyi {barcode} → 0'a sabitlendi (sipariş {order.get('order_number','?')})")
                else:
                    await db.products.update_one(
                        {"id": p2["id"]},
                        {"$inc": {"stock": delta * qty}, "$set": {"updated_at": datetime.now(timezone.utc).isoformat()}})
                movements.append({"barcode": barcode, "delta": _applied2, "product_id": p2["id"]})
    return movements


# Y5: Stok iadesini (restock) idempotent yapan yardımcı. Bir sipariş için birden çok
# iptal yolu (auto-cancel + elle iptal + havale auto-cancel) tetiklenebildiğinden, iade
# hareketi zaten varsa stok TEKRAR eklenmez. Hareket-tipi bağımsız guard.
_RESTORE_MOVE_TYPES = ["order_cancelled", "auto_cancel_expired", "manual_increment",
                       "backfill_increment", "havale_auto_cancel", "order_returned",
                       "return_restock"]  # A1: pazaryeri claim/site iade restock'u da guard'a dahil

# TEK OTORİTER İADE — düşüm hareketi tipleri: iade bu hareketlerin GERÇEK (kayıtlı, cap sonrası,
# variant_id/barcode taşıyan) delta'sını ters çevirir. reactivate_decrement BİLEREK dışarıda:
# order_created zaten tam düşümü temsil eder; onu da saysak çift düşüm sayılırdı.
_DEDUCT_MOVE_TYPES = ["order_created", "order_imported", "manual_decrement", "backfill_decrement"]


async def _consume_restore_moves(order_id: str, reason: str) -> int:
    """Bir siparişin restore (geri-stok) hareketlerini SİLMEK yerine 'tüketildi' işaretler.

    Neden: reaktivasyon/reconcile'de stok yeniden düşülünce, bayat restore hareketi sonraki
    GERÇEK iptalin restock guard'ını (_RESTORE_MOVE_TYPES) bloklamasın diye eskiden
    `delete_many` ile SİLİNİYORDU (orders reactivate + payment reconcile). Bu, panelde ürün
    STOK GEÇMİŞİNİ yok ediyordu ("loglar kayboldu"). Silmek yerine type'ı guard listesinin
    DIŞINA taşıyoruz (type += '_consumed') → tüm guard'lar (hepsi `type $in _RESTORE_MOVE_TYPES`
    tam-eşleşme) bunları ARTIK GÖRMEZ (silinmiş gibi davranır) AMA audit satırı KORUNUR.
    İdempotent: ikinci çağrıda eşleşen kalmaz. Stok miktarına DOKUNMAZ (yalnız log tipi)."""
    now = datetime.now(timezone.utc).isoformat()
    res = await db.stock_movements.update_many(
        {"order_id": order_id, "type": {"$in": list(_RESTORE_MOVE_TYPES)}},
        [{"$set": {
            "type": {"$concat": [{"$ifNull": ["$type", ""]}, "_consumed"]},
            "consumed_at": now,
            "consumed_reason": reason,
        }}],
    )
    return int(getattr(res, "modified_count", 0) or 0)


def _mv_key(it: dict):
    """Hareket kaleminin tekil stok anahtarı. barcode en tutarlı kaydedilen alan olduğundan
     önceliklidir (düşüm ve iade hareketleri arasında eşleşme maksimum olsun); yoksa
    variant_id, yoksa product_id. Böylece kalem-bazlı idempotency düşüm↔iade eşleşir."""
    bc = str(it.get("barcode") or "").strip()
    if bc:
        return ("b", bc)
    vid = str(it.get("variant_id") or "").strip()
    if vid:
        return ("v", vid)
    pid = str(it.get("product_id") or "").strip()
    if pid:
        return ("p", pid)
    return None


async def _resolve_move_level(it: dict) -> str:
    """Bir iade kaleminin _reverse_stock_moves için doğru 'level'ini çöz (kayıtlı değilse).
    Yanlış level parent'ı $sum(variants) ile sıfırlayabildiğinden (varyantsız üründe) kritik."""
    lvl = it.get("level")
    if lvl:
        return lvl
    if it.get("variant_id"):
        return "variant_by_id"
    bc = str(it.get("barcode") or "").strip()
    if bc:
        if await db.products.find_one({"variants.barcode": bc}, {"_id": 1}):
            return "variant"
        return "product"
    return "product"


_CANCEL_ST_FOR_STAMP = ("cancelled", "cancel_refunded")


async def stamp_cancelled_at(order_id: str = "", order_number: str = "",
                             when: str = "", source: str = "") -> bool:
    """İPTAL TARİHİNİ DONDUR — bir kez yazılır, bir daha DEĞİŞMEZ.

    Neden: raporlar iptali "iptalin kesinleştiği ay"a yazıyor ve tarihi
    `cancelled_at` yoksa `updated_at`'ten okuyor. Ama `updated_at` siparişe her
    dokunulduğunda (pazaryeri senkronu 2 dk'da bir) ileri kayıyor → geçen ayın
    iptal rakamı her gün kendiliğinden değişiyordu. Gider pusulasında bu sorun
    yok çünkü kesim tarihi sabit; iptalde de aynı sabitliği kuruyoruz.

    Koşullu yazar (`cancelled_at` YOKSA) → mevcut doğru tarihi ASLA ezmez.
    Döner: bu çağrı damgayı yazdıysa True.
    """
    q = {"cancelled_at": {"$exists": False}}
    if order_id:
        q["id"] = order_id
    elif order_number:
        q["order_number"] = order_number
    else:
        return False
    try:
        res = await db.orders.update_one(q, {"$set": {
            "cancelled_at": when or datetime.now(timezone.utc).isoformat(),
            "cancelled_at_source": source or "live",
        }})
        return bool(res.modified_count)
    except Exception as _e:
        logger.warning(f"[iptal-tarihi] damga yazılamadı ({order_id or order_number}): {_e}")
        return False


async def _order_was_deducted(order: dict) -> bool:
    """Bu sipariş stoktan GERÇEKTEN düşüldü mü? (FAZ 2 — hayalet stok koruması)

    Düşüm kaydı olmayan bir siparişi iptal/iade edince sistem siparişin TAM ADEDİNİ
    stoğa ekliyordu; yani hiç çıkmamış malı depoya geri koyuyordu. Bu, katalogda
    binlerce adetlik hayalet stoğun kaynağıydı.

    Sıra: (1) sipariş belgesindeki damga — yeni siparişlerde kesin cevap,
          (2) defterdeki düşüm hareketi. Pazaryeri içe aktarımı hareketi bazen
              sipariş NUMARASIYLA anahtarlamış olabilir (id yerine), o yüzden üç
              anahtar da denenir — aksi halde gerçekten düşülmüş sipariş yanlışlıkla
              "düşülmemiş" sayılıp iadesi engellenirdi.
    """
    if (order or {}).get("stock_deducted"):
        return True
    oid = str((order or {}).get("id") or "")
    onum = str((order or {}).get("order_number") or "")
    ors = []
    if oid:
        ors.append({"order_id": oid})
    if onum:
        ors.append({"order_id": onum})
        ors.append({"order_number": onum})
    if not ors:
        return False
    try:
        return bool(await db.stock_movements.find_one(
            {"$or": ors, "type": {"$in": _DEDUCT_MOVE_TYPES}}, {"_id": 1}))
    except Exception as _e:
        logger.warning(f"[stok] düşüm kontrolü okunamadı ({oid or onum}): {_e}")
        return True   # okunamadıysa ESKİ davranış (iade et) — veri kaybı riski alma


async def _log_restock_skip(order: dict, move_type: str, source: str = "") -> None:
    """Hayalet iade ENGELLENDİĞİNDE görünür iz bırak (stok DEĞİŞMEZ).

    SİPARİŞ BAŞINA BİR KEZ. Bu izi bırakan yolların bir kısmı cron'dur (Trendyol
    iptal taraması her 5 dk, claim senkronu dakikalar) ve engelleme stoğa hareket
    yazmadığı için guard'lara takılmaz; idempotent olmasaydı aynı sipariş için her
    turda yeni bir kayıt yazılıp defter şişerdi.
    """
    try:
        _oid = (order or {}).get("id")
        _onum = (order or {}).get("order_number", "")
        _q = []
        if _oid:
            _q.append({"order_id": _oid})
        if _onum:
            _q.append({"order_number": _onum})
        if _q and await db.stock_movements.find_one(
                {"$or": _q, "type": "restock_skipped_no_deduction"}, {"_id": 1}):
            return      # bu sipariş için iz zaten var
        # KALEMLER delta=0 ile yazılır. Boş bırakılırsa kayıt ÜRÜN SAYFASINDAKİ stok
        # hareketlerinde GÖRÜNMEZ (o ekran items.product_id / items.barcode ile sorgular)
        # ve "engellendi" bilgisi kimseye ulaşmaz. delta=0 olduğu için stoğa etkisi YOK;
        # engellenen adet ayrı alanda (engellenen_adet) saklanır.
        _items = []
        try:
            for _it in ((order or {}).get("items") or [])[:50]:
                _bc = str((_it or {}).get("barcode") or (_it or {}).get("sku") or "").strip()
                if not _bc:
                    continue
                try:
                    _q2 = max(1, int((_it or {}).get("quantity") or 1))
                except Exception:
                    _q2 = 1
                _p = await db.products.find_one(
                    {"$or": [{"variants.barcode": _bc}, {"barcode": _bc}]},
                    {"_id": 0, "id": 1})
                _items.append({"barcode": _bc, "product_id": (_p or {}).get("id"),
                               "delta": 0, "engellenen_adet": _q2})
        except Exception:
            _items = []
        await db.stock_movements.insert_one({
            "id": str(uuid.uuid4()), "type": "restock_skipped_no_deduction",
            "order_id": (order or {}).get("id"),
            "order_number": (order or {}).get("order_number", ""),
            "items": _items, "source": source or move_type,
            "reason": "siparişin stok düşüm kaydı yok — hayalet iade engellendi",
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
    except Exception:
        pass


async def _restock_authoritative(order: dict, move_type: str, want: dict = None):
    """TEK OTORİTER İADE YOLU (B1/B3/B5/B7/B8).

    Siparişin KAYITLI düşüm hareketlerinden gerçekte düşülen miktarı okur, şimdiye kadar iade
    edileni (_RESTORE_MOVE_TYPES) çıkarır ve KALANI simetrik geri ekler (_reverse_stock_moves —
    level-farkında + parent recompute). Sonuç:
      - oversell'de 0'a sabitlenen kalem TAM qty değil GERÇEK düşülen kadar iade edilir (B1),
      - barkodsuz variant_by_id kalemi de iade edilir (B3),
      - kısmi iade + tam iptal kalem-bazlı idempotent (çift/eksik iade yok) (B7),
      - tetik hareket varlığına bağlı, statü grubuna değil (B5/B8).

    want=None → tüm kalan kalemler (tam iptal). want={key: qty} → yalnız verilenlerden kalanı.
    Döner: iade edilen hareket listesi (delta>0). Düşüm hareketi hiç yoksa None (çağıran eski
    davranışa düşer — geçmiş siparişler bozulmaz)."""
    oid = order.get("id")
    if not oid:
        return None
    deducted = {}
    async for mv in db.stock_movements.find(
            {"order_id": oid, "type": {"$in": _DEDUCT_MOVE_TYPES}}, {"_id": 0, "items": 1, "moves": 1}):
        # DENETİM (stok-sync #1 KRİTİK): pazaryeri düşümleri (order_imported) deltaları "moves"
        # anahtarında yazıyor; buradaki reader yalnız "items"a bakıyordu → pazaryeri düşümleri
        # otoriter restock'a GÖRÜNMEZ olup çağıran tam-adet iadeye düşüyordu (hayalet +stok →
        # Amazon'a push → oversell). İki anahtarı da oku.
        for it in (mv.get("items") or mv.get("moves") or []):
            d = int(it.get("delta") or 0)
            if d >= 0:
                continue
            k = _mv_key(it)
            if not k:
                continue
            slot = deducted.setdefault(k, {"qty": 0, "sample": it})
            slot["qty"] += -d
    if not deducted:
        return None  # ledger yok → eski yola düş
    restored = {}
    async for mv in db.stock_movements.find(
            {"order_id": oid, "type": {"$in": list(_RESTORE_MOVE_TYPES)}}, {"_id": 0, "items": 1}):
        for it in (mv.get("items") or []):
            d = int(it.get("delta") or 0)
            if d <= 0:
                continue
            k = _mv_key(it)
            if not k:
                continue
            restored[k] = restored.get(k, 0) + d
    reversal, recorded = [], []
    for k, slot in deducted.items():
        remaining = slot["qty"] - restored.get(k, 0)
        if remaining <= 0:
            continue
        if want is not None:
            wq = int(want.get(k, 0) or 0)
            if wq <= 0:
                continue
            remaining = min(remaining, wq)
        it = slot["sample"]
        lvl = await _resolve_move_level(it)
        base = {"barcode": it.get("barcode") or "", "product_id": it.get("product_id"),
                "variant_id": it.get("variant_id"), "level": lvl}
        reversal.append({**base, "delta": -remaining})   # _reverse_stock_moves: back=+remaining
        recorded.append({**base, "delta": remaining})
    if reversal:
        await _reverse_stock_moves(reversal)
    return recorded


async def release_order_redemptions(order_or_id) -> int:
    """Sipariş İPTAL edilince kupon kullanım kayıtlarını siler → müşteri kodu (örn. ilk
    üyelik %10 hoş geldin) YENİDEN kullanabilir. İdempotent (delete_many)."""
    oid = order_or_id if isinstance(order_or_id, str) else (order_or_id or {}).get("id")
    if not oid:
        return 0
    # COM03: atomik kullanım hakkı tutmaları (reserved/used) da serbest bırakılır (idempotent).
    try:
        from .coupons import release_coupon_usage
        await release_coupon_usage(oid)
    except Exception as _rcu:
        logger.warning(f"[coupon] kullanım tutması bırakılamadı (sipariş {oid}): {_rcu}")
    res = await db.coupon_redemptions.delete_many({"order_id": oid})
    if res.deleted_count:
        logger.info(f"[coupon] iptal → {res.deleted_count} kupon hakkı serbest bırakıldı (sipariş {oid})")
    return res.deleted_count


async def release_cancelled_redemptions_backfill():
    """TEK SEFERLİK (bayrak korumalı): geçmişte iptal edilmiş siparişlerde yanan kupon
    haklarını geri açar (örn. hoş geldin kodunu kullanıp siparişini iptal eden müşteri
    kodu tekrar kullanamıyordu)."""
    flag = await db.settings.find_one({"id": "coupon_cancel_release_backfill"}, {"_id": 0})
    if flag and flag.get("done"):
        return
    scanned = released = 0
    async for r in db.coupon_redemptions.find({}, {"_id": 1, "order_id": 1}):
        scanned += 1
        o = await db.orders.find_one({"id": r.get("order_id")}, {"_id": 0, "status": 1})
        if o and o.get("status") in ("cancelled", "cancel_refunded"):
            await db.coupon_redemptions.delete_one({"_id": r["_id"]})
            released += 1
    await db.settings.update_one(
        {"id": "coupon_cancel_release_backfill"},
        {"$set": {"done": True, "scanned": scanned, "released": released,
                  "at": datetime.now(timezone.utc).isoformat()}},
        upsert=True)
    logger.info(f"[coupon backfill] tarandı={scanned} serbest bırakıldı={released}")


# Promosyon motorunun ürettiği ve GERÇEK kupon karşılığı olmayan kalemler.
_SYNTHETIC_PROMO_IDS = {"edu_discount", "member_discount"}


async def record_order_redemptions(order: dict) -> bool:
    """Siparişin kuponlarını coupon_redemptions'a yazar (usage_limit / per_user sayımı için).
    İdempotent: aynı (coupon_id, order_id) için ikinci kez yazmaz. FİYATA DOKUNMAZ.
    Y6: Kart siparişlerinde ÖDEME ONAYINDAN sonra çağrılır; başarısız ödemede kupon yanmaz.
    Döner: True = kayıt tamamlandı (ya da kaydedilecek kupon yok); False = hata (yeniden denenmeli)."""
    try:
        # GÜVENLİK: e-postayı lowercase normalize et — kupon per-user/first-order sayımı
        # eval tarafında lowercase sorgulandığından, mixed-case saklarsak limit ATLANABİLİYORDU.
        _email = ((order.get("shipping_address") or {}).get("email", "") or "").strip().lower()
        _redeem_ids = []
        _applied = order.get("applied_promotions") or []
        if _applied:
            for _a in _applied:
                _cid = _a.get("coupon_id")
                # SENTETİK kalemler (gerçek kupon değil, ör. öğrenci indirimi) kullanım
                # sayımına YAZILMAZ — coupon_redemptions yalnız gerçek kuponları taşır.
                if _cid and _cid not in _SYNTHETIC_PROMO_IDS:
                    _redeem_ids.append((_cid, float(_a.get("discount", 0) or 0)))
        elif order.get("coupon_code") and "applied_promotions" not in order:
            # YALNIZ eski (promo motoru öncesi) siparişler: applied_promotions alanı hiç yoksa kod
            # üzerinden kaydedilir. Alan VAR ama boşsa motor kuponu REDDETMİŞTİR (limit dolu, süre
            # bitti…) → indirim uygulanmadı, kullanım hakkı YAKILMAZ (COM03).
            _c = await db.coupons.find_one({"code": order["coupon_code"]}, {"_id": 0, "id": 1})
            if _c:
                _redeem_ids.append((_c["id"], float(order.get("discount", 0) or 0)))
        from .coupons import record_coupon_redemption_once, mark_coupon_usage_used
        for _cid, _disc in _redeem_ids:
            # İdempotent upsert (coupon_id, order_id) — eşzamanlı iki çağrı çift satır yazamaz.
            await record_coupon_redemption_once(_cid, order["id"], {
                "user_id": order.get("user_id"),
                "customer_email": _email,
                "discount": _disc,
                "created_at": datetime.now(timezone.utc).isoformat(),
            })
            # COM03: atomik kullanım tutması reserved → used (zamanlama redemption ile AYNI).
            await mark_coupon_usage_used(_cid, order["id"])
        return True
    except Exception as _redeem_err:
        logger.warning(f"Promosyon kullanım kaydı başarısız (sipariş etkilenmedi): {_redeem_err}")
        return False


async def _restock_return_items_once(rec: dict, order: dict = None) -> int:
    """OTOMATİK site-iade stok geri yükleme — iade edilen kalemleri ADET kadar stoğa ekler.
    KISMİ iadede yalnız iade edilen kalemler; TAM iadede hepsi ("iptal edilen ürün adedi kadar").
    İADE-BAŞINA idempotent (customer_returns.stock_restored atomik guard) → aynı iade iki kez
    (onay + tamamlandı) tetiklense de bir kez uygulanır. Pazaryeri (Trendyol/HB/Temu) siparişi
    HARİÇ — claim yolu restock_claim_once ile ayrıca geri yükler (çift olmasın). Döner: adet."""
    if not rec:
        return 0
    order = order or await db.orders.find_one({"id": rec.get("order_id")}, {"_id": 0})
    if not order:
        return 0
    _plat = str(order.get("platform") or order.get("marketplace") or "").lower()
    if _plat in ("trendyol", "hepsiburada", "temu", "n11", "amazon"):
        return 0
    # Atomik guard: yalnız henüz restock edilmemişse ilerle (yarış/çift-çağrı koruması).
    _claim = await db.customer_returns.update_one(
        {"id": rec.get("id"), "stock_restored": {"$ne": True}},
        {"$set": {"stock_restored": True, "stock_restored_at": datetime.now(timezone.utc).isoformat()}})
    if _claim.modified_count == 0:
        return 0  # başka bir çağrı zaten yaptı
    # Hangi kalemler? KISMİ onayda yalnız onaylananlar (approved_items), yoksa tüm iade kalemleri.
    src = rec.get("approved_items") or rec.get("items") or []
    # Sipariş kalemlerini hem (product_id,beden,renk) hem barkod ile indexle (esnek eşleşme).
    def _k(it):
        return (str(it.get("product_id") or it.get("sku") or ""),
                str(it.get("size") or ""), str(it.get("color") or ""))
    order_by_key, order_by_bc = {}, {}
    for oi in (order.get("items") or []):
        order_by_key.setdefault(_k(oi), oi)
        _obc = str(oi.get("barcode") or oi.get("sku") or "")
        if _obc:
            order_by_bc.setdefault(_obc, oi)
    restock_items, total = [], 0
    for ri in src:
        # barkod: kalemde varsa (approved_items) doğrudan; yoksa siparişten eşle (items).
        bc = str(ri.get("barcode") or "") or str((order_by_key.get(_k(ri)) or {}).get("barcode") or "")
        oi = order_by_bc.get(bc) or order_by_key.get(_k(ri)) or {}
        if not bc:
            bc = str(oi.get("barcode") or oi.get("sku") or "")
        # adet: kalemde varsa (items) o; yoksa (approved_items) siparişteki adet; en az 1.
        qty = int(ri.get("quantity") or oi.get("quantity") or 1)
        if bc and qty > 0:
            restock_items.append({"barcode": bc, "quantity": qty, "name": ri.get("name")})
            total += qty
    if not restock_items:
        # Guard'ı geri al ki eşleşme düzelince tekrar denenebilsin.
        await db.customer_returns.update_one({"id": rec.get("id")}, {"$unset": {"stock_restored": ""}})
        return 0
    # TEK OTORİTER İADE (B7): kalem-bazlı KALAN üzerinden geri ekle — sipariş daha önce (örn.
    # tam iptalle) bu kalemleri iade ettiyse TEKRAR eklemez; oversell-cap'li düşümde gerçek
    # düşülen kadar iade eder. Ledger yoksa eski davranışa düşer.
    want = {}
    for r in restock_items:
        k = _mv_key(r)
        if k:
            want[k] = want.get(k, 0) + int(r.get("quantity") or 0)
    moves = await _restock_authoritative(order, "return_restock", want=want)
    if moves is None:
        moves = await _stock_delta_for_order({"items": restock_items}, +1)
    _restocked = sum(int(m.get("delta") or 0) for m in (moves or []) if int(m.get("delta") or 0) > 0)
    if moves:
        await db.stock_movements.insert_one({
            "id": str(uuid.uuid4()),
            "type": "return_restock",
            "order_id": order.get("id"),
            "order_number": order.get("order_number", ""),
            "return_id": rec.get("id"),
            "items": moves,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "source": "site_return",
        })
    logger.info(f"[iade] otomatik stok geri: {_restocked} adet (talep {total}) order={order.get('order_number')} return={rec.get('id')}")
    return _restocked


async def _restock_order_once(order: dict, move_type: str) -> list:
    """Sipariş kalemlerini stoğa GERİ ekler — ama yalnızca daha önce iade edilmediyse.
    İade hareketi zaten kayıtlıysa hiçbir şey yapmaz (çift iade engellenir)."""
    oid = order.get("id")
    # C2: Sipariş İPTAL yollarında hediye çeki bakiyesi de iade edilir (idempotent —
    # refund_gift_card_once kendi atomik kilidini kullanır). İade (return) yollarında
    # DEĞİL: teslim edilmiş ürün iadesinde para iadesi ayrı süreçtir.
    if move_type in ("order_cancelled", "auto_cancel_expired", "havale_auto_cancel"):
        try:
            from .gift_cards import refund_gift_card_once
            await refund_gift_card_once(order)
        except Exception as _gc_err:
            logger.warning(f"[gift-card] iptal iadesi başarısız (sipariş {oid}): {_gc_err}")
        # C3: kullanılan sadakat puanı da idempotent iade edilir.
        try:
            from .loyalty import refund_points_once
            await refund_points_once(order)
        except Exception as _ly_err:
            logger.warning(f"[loyalty] iptal puan iadesi başarısız (sipariş {oid}): {_ly_err}")
        # Kupon hakkı serbest bırakılır (hoş geldin vb. kod iptal sonrası yeniden kullanılabilir).
        try:
            await release_order_redemptions(order)
        except Exception as _cr_err:
            logger.warning(f"[coupon] iptal serbest bırakma başarısız (sipariş {oid}): {_cr_err}")
    # TEK OTORİTER İADE (B1/B3/B7): kayıtlı düşümün GERÇEK delta'sından KALANI iade et
    # (kısmi iade edilmiş kalemler tekrar iade edilmez; oversell-cap'li kalem gerçek düşülen
    # kadar iade edilir). Kalem-bazlı idempotent.
    moves = await _restock_authoritative(order, move_type, want=None)
    if moves is None:
        # FAZ 2 — HAYALET STOK KORUMASI. Buraya düşmek "siparişin düşüm kaydı yok"
        # demektir. Eskiden siparişin TAM ADEDİ stoğa ekleniyordu: hiç çıkmamış mal
        # depoya geri konuyordu. Artık düşülmemiş siparişe stok EKLENMEZ.
        # NOT: bu kontrol, hediye çeki / sadakat puanı / kupon iade bloğunun ALTINDA
        # durur — o iadeler müşteri parasıdır ve stoktan bağımsız olarak YAPILMALIDIR.
        if not await _order_was_deducted(order):
            logger.warning(f"[stok] hayalet iade engellendi: sipariş {oid} "
                           f"({order.get('order_number','')}) stoktan hiç düşülmemiş "
                           f"— {move_type} stok EKLEMEDİ")
            await _log_restock_skip(order, move_type)
            return []
        # Düşüm var ama otoriter yol eşleştiremedi (ör. hareket sipariş NUMARASIYLA
        # anahtarlanmış) → eski davranış: geniş guard + tam adet.
        if oid and await db.stock_movements.find_one(
                {"order_id": oid, "type": {"$in": _RESTORE_MOVE_TYPES}}, {"_id": 1}):
            return []  # zaten iade edilmiş
        moves = await _stock_delta_for_order(order, +1)
    if not moves:
        return []  # kalan yok (tümü zaten iade edilmiş) — idempotent
    await db.stock_movements.insert_one({
        "id": str(uuid.uuid4()),
        "type": move_type,
        "order_id": oid,
        "order_number": order.get("order_number", ""),
        "items": moves,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    return moves


@router.post("/{order_id}/apply-stock")
async def apply_stock_for_order(
    order_id: str,
    action: str = Query(..., description="'decrement' (new order) or 'increment' (cancel/refund)"),
    current_user: dict = Depends(require_permission("orders.edit"))
):
    """Manually trigger stock adjustment for an order (use for reconciliation)."""
    order = await db.orders.find_one({"id": order_id}, {"_id": 0})
    if not order:
        raise HTTPException(status_code=404, detail="Sipariş bulunamadı")
    delta = -1 if action == "decrement" else 1
    moves = await _stock_delta_for_order(order, delta)
    await db.stock_movements.insert_one({
        "id": str(uuid.uuid4()),
        "type": f"manual_{action}",
        "order_id": order_id,
        "order_number": order.get("order_number", ""),
        "items": moves,
        "created_by": current_user.get("email", ""),
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    return {"success": True, "items_affected": len(moves)}


@router.post("/reconcile-stock")
async def reconcile_stock_backfill(
    platform: str = Query("trendyol"),
    start_date: str = Query(...),
    action: str = Query("decrement"),
    skip_status: str = Query("cancelled,returned,refunded"),
    current_user: dict = Depends(require_permission("orders.edit"))
):
    """İdempotent stok mutabakatı: platform + start_date sonrası siparişlerden HENÜZ
    stok-düşümü yapılmamış olanlara delta uygular. Daha önce düşüm hareketi olan siparişi
    atlar (manual_decrement / order_imported / backfill_decrement). Tekrar çalıştırılabilir.
    Ticimax stok senkronu ile ALAKASI YOKTUR — sadece sipariş bazlı stok düşer/ekler."""
    skips = set(s.strip() for s in (skip_status or "").split(",") if s.strip())
    delta = -1 if action == "decrement" else 1
    # B10: guard'ı action'a göre seç. DÜŞÜMde site siparişleri 'order_created' tipiyle
    # yazıldığından bu tip listede OLMALI — yoksa zaten-düşülmüş site siparişi bulunamayıp
    # İKİNCİ kez düşülüyordu. ARTIŞ tarafında da _RESTORE_MOVE_TYPES ile çift-iade engellenir.
    if delta < 0:
        guard_types = ["order_created", "manual_decrement", "order_imported", "backfill_decrement"]
    else:
        guard_types = _RESTORE_MOVE_TYPES
    q = {"platform": platform, "created_at": {"$gte": start_date}}
    total = 0
    applied = 0
    skipped_status = 0
    skipped_done = 0
    zero_affected = []
    cursor = db.orders.find(q, {"_id": 0, "id": 1, "order_number": 1, "status": 1, "items": 1})
    async for o in cursor:
        total += 1
        if (o.get("status") or "") in skips:
            skipped_status += 1
            continue
        oid = o.get("id")
        already = await db.stock_movements.find_one(
            {"order_id": oid, "type": {"$in": guard_types}}, {"_id": 1}
        )
        if already:
            skipped_done += 1
            continue
        moves = await _stock_delta_for_order(o, delta)
        await db.stock_movements.insert_one({
            "id": str(uuid.uuid4()),
            "type": "backfill_decrement" if delta < 0 else "backfill_increment",
            "order_id": oid,
            "order_number": o.get("order_number", ""),
            "items": moves,
            "created_by": current_user.get("email", ""),
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
        if moves:
            applied += 1
        else:
            zero_affected.append(o.get("order_number", ""))
    return {
        "success": True,
        "total_scanned": total,
        "applied": applied,
        "skipped_status": skipped_status,
        "skipped_already_done": skipped_done,
        "zero_affected_count": len(zero_affected),
        "zero_affected": zero_affected[:50],
    }


@router.post("/auto-cancel-expired")
async def auto_cancel_expired_orders(
    hours: int = Query(48, ge=1),
    current_user: dict = Depends(require_permission("orders.edit"))
):
    """Cancel orders that have been unpaid for more than N hours and restock.

    Y2: Başarısız kart ödemeleri (payment_status='failed') de kapsanır — aksi halde her
        iptal edilen/reddedilen kart denemesi stoğu kalıcı sızdırıyordu.
    Y3: Kapıda ödeme (COD) ve havale siparişleri HARİÇ tutulur — bunlar teslimata/ödemeye
        kadar meşru şekilde 'pending' kalır; havalenin kendi auto-cancel'ı vardır.
    Y5: İade (restock) idempotenttir — sipariş için daha önce iade hareketi varsa tekrar
        stok eklenmez (auto-cancel + elle iptal çift iade sorununu kapatır)."""
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    _cod_bank = ["cash_on_delivery", "kapida", "kapida_odeme", "cod",
                 "bank_transfer", "havale", "eft", "havale_eft", "banka_havale"]
    query = {
        "payment_status": {"$in": ["pending", "failed"]},
        "status": {"$in": ["pending", "awaiting_payment"]},
        "payment_method": {"$nin": _cod_bank},
        "created_at": {"$lt": cutoff},
    }
    cancelled = 0
    async for order in db.orders.find(query, {"_id": 0}):
        moves = await _restock_order_once(order, "auto_cancel_expired")
        # ÖNEMLİ: status="cancelled" DEĞİL "payment_failed" yazılır. Bu siparişlerin parası HİÇ
        # alınmadı; "cancelled" (İptal Edildi) yazılırsa ekip "ödeme alınmıştı, iptal edildi" sanıp
        # yanlışlıkla PARA İADESİ yapıyordu. "payment_failed" = "Ödeme Alınamadı" → iade GEREKMEZ ve
        # ana listede görünmez (varsayılan gizli statü + junk süzgeci).
        await db.orders.update_one(
            {"id": order["id"]},
            {"$set": {
                "status": "payment_failed",
                "payment_status": "expired",
                "cancel_reason": f"Ödeme {hours} saat içinde tamamlanmadı (para HİÇ alınmadı)",
                "cancelled_at": datetime.now(timezone.utc).isoformat(),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }}
        )
        cancelled += 1
    return {"success": True, "cancelled": cancelled, "hours": hours}


async def reclassify_auto_cancelled_unpaid_orders():
    """TELAFİ (otomatik, başlangıçta): Daha önce 'cancelled' (İptal Edildi) olarak işaretlenmiş ama
    parası HİÇ alınmamış (auto_cancelled + payment_status expired/failed, hiç 'paid' olmamış) kart
    siparişlerini 'payment_failed' (Ödeme Alınamadı) statüsüne çeker.

    NEDEN: Bu siparişler 'İptal Edildi' göründüğü için ekip 'ödeme alınmıştı, iptal edildi' sanıp
    yanlışlıkla PARA İADESİ yapıyordu. Gerçekte para alınmadığı için iade GEREKMEZ. Reclassify
    sonrası bu kayıtlar ana listede görünmez ve iade akışına düşmez.

    GÜVENLİK: Yalnız auto_cancelled=True + payment_status∈{expired,failed} + paid_at YOK + hiç
    refund_* alanı YOK olanlar. Gerçekten ödenip iptal/iade edilmiş (payment_status paid/refunded)
    siparişlere DOKUNULMAZ. İdempotent — tekrar çalışması güvenli."""
    try:
        q = {
            "status": "cancelled",
            "auto_cancelled": True,
            "payment_status": {"$in": ["expired", "failed"]},
            "paid_at": {"$in": [None, ""]},
            "refund_paid_at": {"$exists": False},
            "refund_payment": {"$exists": False},
        }
        res = await db.orders.update_many(q, {"$set": {
            "status": "payment_failed",
            "reclassified_payment_failed_at": datetime.now(timezone.utc).isoformat(),
        }})
        return {"reclassified": res.modified_count}
    except Exception as e:
        return {"reclassified": 0, "error": str(e)[:160]}


@router.post("/recover-charged")
async def recover_charged_orders(
    payload: dict = None,
    current_user: dict = Depends(require_permission("orders.edit")),
):
    """Parası çekilip siparişi ONAYLANMAYAN müşterileri kurtarır (admin, tarayıcıdan çağrılabilir).

    Ödeme doğrulaması bir dönem iyzico 'price' (indirim öncesi) ile order.total'ı karşılaştırıp
    indirimli siparişleri yanlışlıkla 'failed' yapıyordu; ama iyzico'nun GERÇEK yanıtı
    (iyzico_retrieve_response) kaydedildiği için gerçekten ödenmiş siparişler tespit edilebilir.

    payload: {days?: int=5, apply?: bool=false}
      - apply=false (varsayılan): YALNIZCA RAPOR — hiçbir şey değişmez.
      - apply=true: uygun siparişleri paid/confirmed yapar + onay bildirimi/kupon/CAPI (idempotent).
    Yalnızca iyzico yanıtı BAŞARILI (paymentId + status=success + FAILURE değil) VE tahsil edilen
    tutar (paidPrice) sipariş toplamına eşit/fazla olanlar kurtarılır; eksik tahsilat ayrı listelenir."""
    payload = payload or {}
    days = int(payload.get("days", 5) or 5)
    apply = bool(payload.get("apply", False))
    notify = bool(payload.get("notify", True))  # False → SMS/e-posta/CAPI GÖNDERME (sessiz kurtar)
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    query = {"payment_status": {"$in": ["failed", "pending"]}, "created_at": {"$gte": cutoff}}

    # Zaten kurtarılmış siparişlerin listesi (rapor için — 18'i geri görebilmek adına).
    already_recovered = []
    async for o in db.orders.find(
        {"recovered_at": {"$exists": True}, "created_at": {"$gte": cutoff}},
        {"_id": 0, "order_number": 1, "total": 1, "shipping_address": 1, "email": 1,
         "recovered_at": 1, "created_at": 1}):
        already_recovered.append({
            "order_number": o.get("order_number"), "total": round(float(o.get("total") or 0), 2),
            "email": (o.get("shipping_address") or {}).get("email") or o.get("email"),
            "created_at": o.get("created_at"), "recovered_at": o.get("recovered_at"),
        })

    recoverable, undercharge = [], []
    async for o in db.orders.find(query, {"_id": 0}):
        snap = o.get("iyzico_retrieve_response") or {}
        if not snap.get("paymentId"):
            continue
        if str(snap.get("status") or "").lower() != "success":
            continue
        _ps = str(snap.get("paymentStatus") or "").upper()
        if _ps and _ps != "SUCCESS":
            continue
        total = round(float(o.get("total") or 0), 2)
        paid = round(float(snap.get("paidPrice") or 0), 2)
        row = {
            "order_number": o.get("order_number"), "id": o.get("id"),
            "total": total, "paid": paid, "paymentId": snap.get("paymentId"),
            "payment_status": o.get("payment_status"), "status": o.get("status"),
            "email": (o.get("shipping_address") or {}).get("email") or o.get("email"),
            "created_at": o.get("created_at"),
        }
        if paid + 0.02 >= total and total > 0:
            recoverable.append((o, row))
        else:
            undercharge.append(row)

    result = {
        "days": days, "apply": apply, "notify": notify,
        "recoverable_count": len(recoverable),
        "undercharge_count": len(undercharge),
        "already_recovered_count": len(already_recovered),
        "recoverable": [r for _o, r in recoverable],
        "undercharge": undercharge,
        "already_recovered": already_recovered,
        "applied": 0,
    }
    if not apply:
        return result

    from .payment import _notify_paid_order_confirmed
    now_iso = datetime.now(timezone.utc).isoformat()
    fixed = 0
    for o, r in recoverable:
        oid = o["id"]
        snap = o.get("iyzico_retrieve_response") or {}
        await db.orders.update_one(
            {"id": oid, "payment_status": {"$ne": "paid"}},
            {"$set": {
                "payment_status": "paid", "status": "confirmed", "paid_at": now_iso,
                "payment_id": snap.get("paymentId"), "iyzico_payment_id": snap.get("paymentId"),
                "recovered_by": current_user.get("email", ""), "recovered_at": now_iso,
                "updated_at": now_iso,
            }},
        )
        # notify=False → SESSİZ kurtarma: SMS/e-posta/CAPI gönderme (eski siparişlerde
        # müşteriyi rahatsız etmemek için). Yalnızca kupon kaydını (idempotent) tutmak istersek
        # bile, notify kapalıyken hiçbir dış bildirim çıkmaz.
        if notify:
            try:
                await _notify_paid_order_confirmed(oid)
            except Exception as _e:
                logger.warning(f"[KURTARMA] bildirim hatasi {r['order_number']}: {_e}")
            try:
                await dispatch_purchase_capi(oid, source="recovery_admin")
            except Exception:
                pass
        fixed += 1
    result["applied"] = fixed
    return result


@router.post("/missing-size")
async def find_missing_size_orders(
    payload: dict = None,
    current_user: dict = Depends(require_permission("orders.view")),
):
    """Bedeni OLAN üründe kalemi BEDENSİZ kalmış siparişleri bulur (tanı — sadece rapor).

    Ürün kartındaki '+' hızlı-ekleme, bedenli üründe beden seçtirmeden ekleyince siparişe
    bedensiz kalem düşüyordu (ör. W10322 bermuda şort). Bu uç, düzeltme öncesi verilmiş bu
    tür siparişleri listeler ki müşterilerine bedenini sorabilesiniz. Hiçbir şeyi DEĞİŞTİRMEZ.

    payload: {days?: int=30}. İptal/expired siparişler hariç tutulur."""
    payload = payload or {}
    days = int(payload.get("days", 30) or 30)
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

    _has_size_cache = {}  # product_id -> bool (üründe beden-varyantı var mı)

    async def _product_has_sizes(pid):
        if pid in _has_size_cache:
            return _has_size_cache[pid]
        has = False
        try:
            p = await db.products.find_one({"id": pid}, {"_id": 0, "variants": 1})
            for v in ((p or {}).get("variants") or []):
                if (v.get("size") or "").strip():
                    has = True
                    break
        except Exception:
            has = False
        _has_size_cache[pid] = has
        return has

    flagged = []
    async for o in db.orders.find(
        {"created_at": {"$gte": cutoff},
         "status": {"$nin": ["cancelled", "returned", "refunded"]}},
        {"_id": 0, "order_number": 1, "items": 1, "shipping_address": 1, "email": 1,
         "created_at": 1, "status": 1}):
        bad = []
        for it in (o.get("items") or []):
            if (it.get("size") or "").strip():
                continue  # bedeni var, sorun yok
            # beden boş — varyant_id/barkod ile beden çıkarılabilir mi?
            if it.get("variant_id") or it.get("barcode"):
                continue  # koddan beden bulunabilir; kritik değil
            pid = it.get("product_id")
            if pid and await _product_has_sizes(pid):
                bad.append({"name": it.get("name"), "product_id": pid, "quantity": it.get("quantity")})
        if bad:
            flagged.append({
                "order_number": o.get("order_number"), "status": o.get("status"),
                "created_at": o.get("created_at"),
                "email": (o.get("shipping_address") or {}).get("email") or o.get("email"),
                "phone": (o.get("shipping_address") or {}).get("phone"),
                "items_missing_size": bad,
            })
    return {"days": days, "count": len(flagged), "orders": flagged}


@router.post("/{order_id}/note")
async def add_order_note(
    order_id: str,
    payload: dict,
    current_user: dict = Depends(require_permission("orders.note"))
):
    """Add/update admin note on order (e.g. havale takip)."""
    note = (payload or {}).get("note", "").strip()
    existing = await db.orders.find_one({"id": order_id}, {"_id": 0, "id": 1})
    if not existing:
        raise HTTPException(status_code=404, detail="Sipariş bulunamadı")
    notes_list = (await db.orders.find_one({"id": order_id}, {"_id": 0, "admin_notes": 1})).get("admin_notes") or []
    notes_list.append({
        "id": str(uuid.uuid4()),
        "text": note,
        "by": current_user.get("email", ""),
        "at": datetime.now(timezone.utc).isoformat(),
    })
    await db.orders.update_one(
        {"id": order_id},
        {"$set": {"admin_notes": notes_list, "updated_at": datetime.now(timezone.utc).isoformat()}}
    )
    await _log_order_event(order_id, "note", "Sipariş notu eklendi", current_user, {"note": note[:200]})
    return {"success": True, "notes": notes_list}


# =============================================================================
# FAZ 5 — Kargo durum güncellemeleri (ship / undeliver / deliver)
# =============================================================================
VALID_CARGO_COMPANIES = {
    "MNG", "DHL", "Yurtici", "Aras", "PTT", "UPS", "HepsiJet", "Trendyol", "Other"
}


@router.post("/{order_id}/ship")
async def ship_order(
    order_id: str,
    cargo_company: str = Query(..., description="Kargo firması kodu"),
    tracking_number: str = Query(..., min_length=3, description="Kargo takip numarası"),
    expected_status: Optional[str] = Query(None, max_length=64),
    current_user: dict = Depends(require_permission("orders.ship"))
):
    """Siparişi kargoya ver: status=shipped + cargo_company + cargo_tracking_number.
    Bildirim hook'u (order_shipped) otomatik tetiklenir.
    COM08: `expected_status` (ekranda görülen statü) verilirse ve güncel statü farklıysa 409.
    """
    if cargo_company not in VALID_CARGO_COMPANIES:
        raise HTTPException(status_code=400, detail=f"Geçersiz kargo firması. Geçerli: {sorted(VALID_CARGO_COMPANIES)}")

    existing = await db.orders.find_one({"id": order_id}, {"_id": 0})
    if not existing:
        raise HTTPException(status_code=404, detail="Sipariş bulunamadı")

    # DENETİM (bizlogic F4): ödeme guard'ı yoktu → ÖDENMEMİŞ (awaiting_payment) veya İPTAL sipariş
    # kargoya verilebiliyordu (CLAUDE.md Değişmez 2/4). Yalnız ödenmiş/onaylı sipariş sevk edilir.
    _ps = str(existing.get("payment_status") or "")
    _st = str(existing.get("status") or "")
    if isinstance(expected_status, str) and expected_status and expected_status != _st:
        raise HTTPException(status_code=409, detail=(
            f"Sipariş durumu bu arada değişti (şu an: {_st or '-'}). Sayfayı yenileyip tekrar deneyin."))
    if _ps != "paid" and _st not in ("confirmed", "processing", "shipped", "delivered", "undelivered"):
        raise HTTPException(status_code=400,
            detail=f"Ödemesi tamamlanmamış/uygun olmayan sipariş kargoya verilemez (durum: {_st or '?'}, ödeme: {_ps or '?'}).")
    # COM08: iptal edilmiş / ödemesi iade edilmiş / ödeme süresi dolmuş sipariş (ödenmiş olsa bile)
    # kargoya VERİLEMEZ — geçersiz birleşim (iptal + sevk) oluşmaz.
    if _st in ("cancelled", "cancel_refunded", "refunded", "returned", "payment_failed"):
        raise HTTPException(status_code=409,
            detail=f"Bu sipariş '{_st}' durumunda; kargoya verilemez.")

    now_iso = datetime.now(timezone.utc).isoformat()
    # COM08: karşılaştır-ve-yaz — okunan statü arada (eşzamanlı iptal vb.) değiştiyse 409.
    _ship_res = await db.orders.update_one(
        {"id": order_id, "status": existing.get("status")},
        {"$set": {
            "status": "shipped",
            "cargo_company": cargo_company,
            "cargo_tracking_number": tracking_number.strip(),
            "shipped_at": now_iso,
            "updated_at": now_iso,
        }},
    )
    if getattr(_ship_res, "matched_count", 1) == 0:
        raise HTTPException(status_code=409, detail=(
            "Sipariş durumu eşzamanlı başka bir işlemle değişti (ör. iptal). Sayfayı yenileyip tekrar deneyin."))

    await _log_order_event(order_id, "cargo", f"Kargoya verildi: {cargo_company} ({tracking_number.strip()})", current_user,
                           {"cargo_company": cargo_company, "tracking_number": tracking_number.strip()},
                           order_number=existing.get("order_number", ""))

    # Bildirim — fire-and-forget
    import asyncio as _asyncio
    async def _notify_ship():
        try:
            import sys, os
            sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
            from notification_service import send_notification
            addr = existing.get("shipping_address") or {}
            await send_notification(
                db, "order_shipped",
                to_phone=addr.get("phone") or existing.get("phone"),
                to_email=addr.get("email") or existing.get("email"),
                variables=await _order_notify_vars(existing, tracking_number=tracking_number.strip(), cargo_company=cargo_company, cargo_provider=cargo_company),
            )
        except Exception as _e:
            logger.warning(f"order_shipped notification failed: {_e}")
    _spawn(_notify_ship())

    return {
        "success": True,
        "message": f"Sipariş {cargo_company} kargosuna verildi ({tracking_number})",
        "tracking_number": tracking_number,
    }


@router.post("/{order_id}/undeliver")
async def mark_order_undelivered(
    order_id: str,
    reason: str = Query("Kargo teslim edilemedi", description="Teslim edilememe nedeni"),
    branch_info: str = Query("", description="Şube bilgisi / nerede bekliyor"),
    current_user: dict = Depends(require_permission("orders.ship"))
):
    """Kargo teslim edilemedi (şubede bekliyor) durumunu işaretler ve müşteriye bildirim atar."""
    existing = await db.orders.find_one({"id": order_id}, {"_id": 0})
    if not existing:
        raise HTTPException(status_code=404, detail="Sipariş bulunamadı")

    now_iso = datetime.now(timezone.utc).isoformat()
    await db.orders.update_one(
        {"id": order_id},
        {"$set": {
            "status": "undelivered",
            "undelivered_reason": reason,
            "undelivered_branch": branch_info,
            "undelivered_at": now_iso,
            "updated_at": now_iso,
        }},
    )

    import asyncio as _asyncio
    async def _notify_undeliver():
        try:
            import sys, os
            sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
            from notification_service import send_notification
            addr = existing.get("shipping_address") or {}
            await send_notification(
                db, "order_undelivered",
                to_phone=addr.get("phone") or existing.get("phone"),
                to_email=addr.get("email") or existing.get("email"),
                variables=await _order_notify_vars(existing, reason=reason, branch_info=branch_info),
            )
        except Exception as _e:
            logger.warning(f"order_undelivered notification failed: {_e}")
    _spawn(_notify_undeliver())

    return {"success": True, "message": "Teslim edilemedi olarak işaretlendi"}


@router.post("/{order_id}/mark-invoiced")
async def mark_order_invoiced(
    order_id: str,
    payload: dict,
    current_user: dict = Depends(require_permission("orders.edit"))
):
    """Mark order as invoiced – locks edits, flips status to 'confirmed' if pending."""
    invoice_number = (payload or {}).get("invoice_number", "")
    existing = await db.orders.find_one({"id": order_id}, {"_id": 0, "status": 1, "payment_method": 1,
                                                         "payment_status": 1, "platform": 1, "marketplace": 1,
                                                         "paid_with_gift_card_only": 1, "imported_from": 1})
    if not existing:
        raise HTTPException(status_code=404, detail="Sipariş bulunamadı")
    update = {
        "invoice_issued": True,
        "invoice_number": invoice_number,
        "invoice_issued_at": datetime.now(timezone.utc).isoformat(),
        "invoice_issued_by": current_user.get("email", ""),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    # DEĞİŞMEZ #1: ödemesi ALINMAMIŞ site KART siparişi fatura işaretiyle 'confirmed' YAPILMAZ
    # (update_order_status'taki kapıyla aynı kural); kapıda ödeme / pazaryeri eski davranışında kalır.
    _unpaid_card = (str(existing.get("payment_method") or "").lower() in _CARD_PMS
                    and str(existing.get("payment_status") or "").lower() not in _SETTLED_PAY
                    and not existing.get("paid_with_gift_card_only") and not existing.get("imported_from")
                    and _is_site_order(existing))
    if existing.get("status") == "pending" and not _unpaid_card:
        update["status"] = "confirmed"
    # BL-035: elle işlenen fatura → inceleme/bekleyen gönderim izi kapanır.
    update["invoice_needs_review"] = False
    update["invoice_in_progress"] = False
    await db.orders.update_one({"id": order_id}, {"$set": update, "$unset": {"invoice_pending": ""}})
    await _log_order_event(order_id, "invoice", f"Fatura işlendi: {invoice_number}".strip(), current_user, {"invoice_number": invoice_number})
    return {"success": True, "invoice_issued": True}



# ---------------------------------------------------------------------------
# E-Fatura oluşturma — aktif e-fatura entegratörünü kullanarak fatura keser.
# Aktif provider `providers_config` kolleksiyonundan okunur. Canlıda her
# provider için ayrı SDK çağrısı olacak; şu an mock başarı döner ama doğru
# provider adı + üretilmiş invoice_number ile kayıt altına alınır.
#
# FRONTEND: Orders.jsx handleGenerateInvoice + handleBulkGenerateInvoice.
# ---------------------------------------------------------------------------
@router.post("/{order_id}/reset-invoice")
async def reset_invoice_for_order(order_id: str, request: Request,
                                  current_user: dict = Depends(require_permission("orders.invoice"))):
    """Sipariş fatura kaydını panelde sıfırlar (yeniden kesilebilsin diye).
    Doğan'daki gerçek faturayı İPTAL ETMEZ; yalnız paneldeki fatura işaretini/numarasını temizler."""
    order = await db.orders.find_one({"id": order_id}, {"_id": 0})
    if not order:
        raise HTTPException(status_code=404, detail="Sipariş bulunamadı")
    prev = order.get("invoice_number") or ""
    # BL-035: bekleyen (Doğan'a gitmiş olabilecek) UUID/numara da temizlenir — bu, yöneticinin
    # Doğan portalında kontrol ettikten sonraki BİLİNÇLİ kararıdır; iz kaybolmasın diye geçmişe yazılır.
    _pend = order.get("invoice_pending") or {}
    _upd = {"$set": {"invoice_issued": False},
            "$unset": {
                "invoice_number": "", "invoice_uuid": "", "invoice_type": "",
                "invoice_provider": "", "invoice_provider_response": "",
                "invoice_intl_txn_id": "", "invoice_dogan_id": "", "invoice_pdf_url": "",
                "invoice_issued_at": "", "invoice_issued_by": "",
                # Başarısız denemelerin izleri de temizlensin (hata + olası takılı kilit) →
                # tam temiz sayfa, yeniden 'Fatura Kes' engelsiz çalışsın.
                "invoice_last_error": "", "invoice_last_error_at": "",
                "invoice_in_progress": "", "invoice_in_progress_at": "",
                "invoice_pending": "", "invoice_needs_review": "",
                "invoice_manual_file": "",
            }}
    if _pend:
        _upd["$push"] = {"invoice_pending_history": {
            **_pend, "cleared_at": datetime.now(timezone.utc).isoformat(),
            "cleared_by": current_user.get("email", "")}}
    await db.orders.update_one({"id": order_id}, _upd)
    # GÜVENLİK (denetim 2026-10): fatura kaydı sıfırlama çift-fatura riski taşır → iz bırak.
    await record_admin_audit(
        db, action="orders.invoice_reset", entity_type="order", entity_id=order_id,
        before={"invoice_number": prev, "invoice_issued": bool(order.get("invoice_issued")),
                "invoice_pending_number": str(_pend.get("number") or "")},
        after={"invoice_number": "", "invoice_issued": False, "invoice_pending_number": ""},
        current_user=current_user, request=request, source="admin.orders")
    _warn = (f" UYARI: bekleyen UUID {_pend.get('uuid')} / no {_pend.get('number')} temizlendi — Doğan'da "
             f"bu UUID ile fatura VARSA yeniden kesim ÇİFT fatura oluşturur." if _pend else "")
    return {"success": True, "message": f"Fatura kaydı sıfırlandı ({prev or 'kayıt/deneme izi'} silindi), yeniden kesebilirsiniz.{_warn}"}


# NOT: 2-segmentli yol (/diagnose/invoice) — tek-segment `GET /{order_id}` (yukarıda kayıtlı)
# statik tek-segment yolu yutacağından burada 2 segment kullanılır; hiçbir literal 2-seg rota
# ("/{order_id}/journey" vb.) "diagnose/invoice" ile eşleşmez → gölgeleme yok.
@router.get("/diagnose/invoice")
async def invoice_diagnose(q: str, current_user: dict = Depends(require_permission("orders.invoice"))):
    """TAHRİBATSIZ teşhis: bir siparişe faturanın neden kesilmediğini, Doğan'a YENİDEN
    göndermeden söyler. `q` = sipariş no (W11663), pazaryeri paket no (Trendyol/HB) veya iç id.
    Siparişte saklı `invoice_last_error` (son Doğan reddi) + tüm ön-engelleri (onay/ödeme/
    entegratör/VKN/kilit) tek yanıtta döndürür."""
    q = safe_str(q, 80)
    if not q:
        raise HTTPException(status_code=400, detail="q (sipariş/paket no) gerekli")
    _qre = {"$regex": f"^{re.escape(q)}$", "$options": "i"}
    order = await db.orders.find_one(
        {"$or": [
            {"id": q}, {"order_number": _qre}, {"trendyol_package_id": _qre},
            {"package_number": _qre}, {"shipment_package_id": _qre},
            {"marketplace_order_id": _qre}, {"hepsiburada_order_number": _qre},
        ]},
        {"_id": 0},
    )
    if not order:
        raise HTTPException(status_code=404, detail=f"Sipariş bulunamadı: {q}")

    # Aktif e-fatura entegratörü durumu
    dogan_settings = await db.settings.find_one({"id": "dogan_edonusum"}, {"_id": 0}) or {}
    dogan_active = bool(dogan_settings.get("enabled") and dogan_settings.get("username"))
    integrator_ok = dogan_active
    if not dogan_active:
        cfg = await db.providers_config.find_one({"kind": "einvoice"}, {"_id": 0}) or {}
        integrator_ok = bool(cfg.get("active_provider") and (cfg.get("providers") or {}).get(cfg.get("active_provider")))

    # VKN/TCKN varlığı (KVKK: şifreli alanlar yalnız bu hesap için çözülür; yanıtta yalnız uzunluk)
    _pv = _pii_reveal_doc(order)
    _bill = dict(_pv.get("billing_address") or {})
    _binfo = _pv.get("billing_info") or {}
    _vkn = (_bill.get("tax_number") or _bill.get("tax_no") or _bill.get("vkn")
            or _binfo.get("tax_number") or str(_pv.get("trendyol_identity_number") or "")).strip().replace(" ", "")

    # Engelleri sırayla değerlendir → ilk bloklayan sebebi "blocker" olarak döndür
    blocker = None
    st = (order.get("status") or "")
    if order.get("invoice_issued"):
        blocker = None  # zaten kesilmiş
    elif st in _PRE_CONFIRM_STATUSES:
        blocker = f"Sipariş ONAYLANMAMIŞ (durum: '{st}'). Önce 'Onaylandı'ya alın."
    elif _havale_invoice_block(order):
        blocker = _havale_invoice_block(order)
    elif not integrator_ok and not dogan_active:
        blocker = "Aktif e-fatura entegratörü yok. Ayarlar > E-Dönüşüm'den Doğan'ı 'Aktif' yapın."
    elif dogan_active and not integrator_ok:
        blocker = "Doğan pasif ve Doğan-dışı entegratör gerçek gönderim yapmıyor (400)."

    # Kilit yaşı (çakışma teşhisi)
    _lock = order.get("invoice_in_progress")
    _lock_at = order.get("invoice_in_progress_at")

    return {
        "found": True,
        "order_id": order.get("id"),
        "order_number": order.get("order_number"),
        "platform": order.get("platform") or order.get("marketplace") or "site",
        "status": st,
        "payment_method": order.get("payment_method"),
        "payment_status": order.get("payment_status"),
        "invoice_issued": bool(order.get("invoice_issued")),
        "invoice_number": order.get("invoice_number") or "",
        "invoice_in_progress": bool(_lock),
        "invoice_in_progress_at": _lock_at,
        # BL-035: Doğan'a gönderilmiş olabilecek bekleyen fatura + inceleme işareti
        "invoice_pending": order.get("invoice_pending") or None,
        "invoice_needs_review": bool(order.get("invoice_needs_review")),
        "has_vkn_tckn": bool(_vkn and len(_vkn) in (10, 11)),
        "vkn_tckn_len": len(_vkn),
        "dogan_active": dogan_active,
        "dogan_is_test": bool(dogan_settings.get("is_test", True)),
        "integrator_ok": integrator_ok,
        # EN ÖNEMLİSİ: son gerçek Doğan reddi (varsa) — asıl sebep genellikle burada
        "invoice_last_error": order.get("invoice_last_error") or "",
        "invoice_last_error_at": order.get("invoice_last_error_at") or "",
        "blocker": blocker,
        "verdict": ("Fatura zaten kesilmiş." if order.get("invoice_issued")
                    else blocker or
                    (f"Ön-engel yok. Son Doğan hatası: {order.get('invoice_last_error')}"
                     if order.get("invoice_last_error")
                     else "Ön-engel yok, kayıtlı hata yok — 'Fatura Kes'e basınca gelen gerçek mesajı (artık 400+detail) iletin.")),
    }


# Havale/EFT siparişlerinde fatura, ödeme (havale) onaylanmadan KESİLMEZ.
_HAVALE_PMS = ("bank_transfer", "havale", "eft", "havale_eft", "banka_havale", "havale/eft")
# Online KART ödeme yöntemleri (modül düzeyi — durum kapısı da kullanır; create_order içindeki
# aynı isimli yerel liste yalnız o fonksiyonu ilgilendirir).
_CARD_PMS = ("credit_card", "card", "kredi_karti", "kart", "iyzico", "creditcard")
_SETTLED_PAY = ("paid", "completed", "success", "succeeded", "captured")


def _havale_invoice_block(order: dict):
    """Havale/EFT siparişi ödemesi onaylanmamışsa fatura engelinin sebebini döndürür;
    onaylıysa (payment_status ödenmiş) None döner."""
    pm = (order.get("payment_method") or "").lower()
    pstat = (order.get("payment_status") or "").lower()
    if pm in _HAVALE_PMS and pstat not in _SETTLED_PAY:
        return ("Havale onaylanmadığı için bu siparişe fatura kesilemedi. "
                "Önce siparişin ödemesini 'Ödendi' olarak işaretleyin (havale onayı), "
                "sonra faturayı kesin.")
    return None


def _dogan_error_detail(kind: str, message) -> str:
    """Doğan gönderim hatasını kullanıcıya anlaşılır şekilde döndürür. GEÇİCİ altyapı hatası
    (503/TR7 'No Available Server'/timeout — Doğan sunucusu o an ayakta değil) ise; bu bizim
    kod/veri hatamız DEĞİL, Doğan tarafıdır ve otomatik retry zaten denendi. Net yönlendirme
    ver. Diğer (iş) hatalarında ham Doğan mesajını göster (VKN/adres/şablon/kontör vb.)."""
    _msg = str(message or "")
    try:
        from dogan_client import _is_transient_dogan_error as _trn
        _is_transient = _trn(_msg)
    except Exception:
        _is_transient = ("no available server" in _msg.lower() or "tr7" in _msg.lower()
                         or "503" in _msg or "timeout" in _msg.lower())
    if _is_transient:
        return (f"Doğan {kind} sunucusu şu anda yanıt vermiyor (geçici — 'TR7 No Available Server' / "
                f"503). Bu bir Doğan altyapı kesintisidir, siparişinizde/ayarınızda sorun yok; sistem "
                f"otomatik birkaç kez denedi. Lütfen birkaç dakika sonra tekrar 'Fatura Kes' deneyin. "
                f"Sürerse Doğan'a canlı {kind} servisinizin (ve sunucu IP izninin) aktif olup "
                f"olmadığını sorun. [ham: {_msg[:180]}]")
    return f"Doğan {kind} hatası: {_msg}"


# ─── BL-035: MÜKERRER (ÇİFT) e-ARŞİV / e-FATURA KORUMASI ─────────────────────────────
# Sorun: Doğan'a gönderim zaman aşımına uğrayıp aslında KABUL edildiyse, sonraki deneme
# "mükerrer/zaten kayıtlı" alıyordu; eski kod bunu numara çakışması sanıp YENİ numara + YENİ
# UUID ile ikinci bir yasal fatura kesiyordu. Kurallar:
#   1) Gönderimden ÖNCE seçilen UUID+numara siparişe `invoice_pending` olarak yazılır; aynı
#      sipariş için her yeniden denemede AYNI UUID/numara kullanılır (yenisi üretilmez).
#   2) Doğan "mükerrer" derse YENİ belge kesilmez: Doğan'da bu UUID sorgulanır; bulunursa
#      siparişe kesilmiş fatura olarak kaydedilir. Doğrulanamazsa `invoice_needs_review=True`
#      ile durulur — ASLA otomatik yeni numara verilmez.
#   3) `invoice_pending` varken yeni gönderimden önce Doğan sorgusu yapılır (bayat kilit dahil).
#   4) Yalnız UUID'mizin Doğan'da OLMADIĞI doğrulanırsa (numara başka belgede) numara ilerler;
#      UUID yine AYNI kalır → Doğan aynı UUID'li ikinci belgeyi zaten reddeder.
_DOGAN_DUP_MARKERS = ("zaten", "mükerrer", "mukerrer", "duplicate", "already", "10009",
                      "kayıtlı", "kayitli", "mevcut", "daha önce gönder", "daha once gonder",
                      "eşleş", "eslesme", "eslesmekte")
# Doğan durum sorgusunda "bu UUID ile belge yok" anlamına gelen ifadeler (yalnız bunlar
# 'bulunamadı' sayılır; başka her hata 'doğrulanamadı' → inceleme gerekir).
_DOGAN_NOT_FOUND_MARKERS = ("bulunam", "bulunmam", "bulunmuyor", "kayıt yok", "kayit yok",
                            "not found", "mevcut değil", "mevcut degil", "yoktur")


def _dogan_is_dup(res) -> bool:
    r = res or {}
    if str(r.get("code") or "") == "10009":
        return True
    return any(t in str(r.get("message", "")).lower() for t in _DOGAN_DUP_MARKERS)


def _dogan_definite_reject(res) -> bool:
    """Doğan isteği İŞLEDİ ve faturayı KESİN reddetti mi (ERROR_CODE dolu, mükerrer değil) ya da
    istek hiç gönderilmedi mi (giriş hatası)? Bu durumda fatura Doğan'da OLUŞMADI → numara geri
    alınabilir, bekleyen UUID silinebilir. Kodsuz hatalar (timeout/bağlantı) BELİRSİZDİR: fatura
    Doğan'a ulaşmış olabilir → bekleyen UUID/numara korunur."""
    r = res or {}
    if _dogan_is_dup(r):
        return False
    if str(r.get("code") or "").strip():
        return True
    _m = str(r.get("message") or "")
    return _m.startswith("Doğan login") or "SESSION_ID alınamadı" in _m


def _dogan_lookup_invoice_sync(cli, kind: str, invoice_uuid: str) -> dict:
    """Doğan'da bu UUID ile giden fatura var mı? (senkron; threadpool'da çağrılır, SALT OKUNUR)
    Döner: {state: 'found'|'not_found'|'unknown', invoice_number, web_key, status, cancelled, detail}
      - found     : belge Doğan'da var (numarası invoice_number)
      - not_found : sorgu başarılı ve bu UUID ile belge YOK
      - unknown   : sorgu başarısız / yanıt beklenmedik → karar verilemez (inceleme gerekir)"""
    out = {"state": "unknown", "invoice_number": "", "web_key": "", "status": "",
           "cancelled": False, "detail": ""}
    _u = str(invoice_uuid or "").strip().lower()
    if not _u:
        out["detail"] = "UUID boş"
        return out

    def _first(obj, keys):
        if isinstance(obj, dict):
            for k in keys:
                v = obj.get(k)
                if v not in (None, "", [], {}) and not isinstance(v, (dict, list)):
                    return str(v)
            for v in obj.values():
                r = _first(v, keys)
                if r:
                    return r
        elif isinstance(obj, (list, tuple)):
            for v in obj:
                r = _first(v, keys)
                if r:
                    return r
        return ""

    def _err(ser):
        e = ser.get("ERROR_TYPE") if isinstance(ser, dict) else None
        if isinstance(e, dict) and e.get("ERROR_CODE"):
            return str(e.get("ERROR_CODE")), str(e.get("ERROR_SHORT_DES") or e.get("ERROR_LONG_DES") or "")
        return "", ""

    try:
        from zeep.helpers import serialize_object
        if kind == "e-arsiv":
            c = cli._get_earsiv_client()
            ser = serialize_object(c.service.GetEArchiveInvoiceStatus(
                REQUEST_HEADER=cli._make_header(), UUID=invoice_uuid)) or {}
            code, msg = _err(ser)
            if code:
                out["detail"] = f"{code}: {msg}"[:200]
                if any(t in msg.lower() for t in _DOGAN_NOT_FOUND_MARKERS):
                    out["state"] = "not_found"
                return out
            invs = [i for i in (ser.get("INVOICE") or []) if i]
            hdrs = [((i or {}).get("HEADER") or {}) for i in invs]
            if not invs or all(not (h.get("INVOICE_ID") or h.get("STATUS") or h.get("UUID")) for h in hdrs):
                out["state"] = "not_found"
                return out
            hdr = hdrs[0]
            _ru = str(hdr.get("UUID") or (invs[0] or {}).get("UUID") or "").strip().lower()
            if _ru and _ru != _u:
                out["detail"] = "Doğan yanıtındaki UUID bizimkiyle eşleşmiyor"
                return out
            _no = str(hdr.get("INVOICE_ID") or (invs[0] or {}).get("INVOICE_ID") or "").strip()
            if not _no:
                out["detail"] = f"durum var ama fatura no yok ({hdr.get('STATUS')} {hdr.get('STATUS_DESC')})"
                # Doğan, bu UUID ile belge YOKKEN hata kodu yerine durum satırı döndürüyor:
                # STATUS=200, STATUS_DESC="FATURA ID BULUNAMADI" (canlı, 2026-10-05). Fatura numarası
                # da yoksa bu KESİN "yok"tur → aynı UUID/numara ile güvenle yeniden gönderilir
                # (Doğan aynı UUID'yi ikinci kez kabul etmez; mükerrer yanıtı ayrıca yakalanıyor).
                _sd = str(hdr.get("STATUS_DESC") or "").lower()
                if any(t in _sd for t in _DOGAN_NOT_FOUND_MARKERS):
                    out["state"] = "not_found"
                return out
            out.update({
                "state": "found", "invoice_number": _no,
                "web_key": _first(ser, ("WEB_KEY",)),
                "status": f"{hdr.get('STATUS') or ''} {hdr.get('STATUS_DESC') or ''}".strip(),
                "cancelled": (str(hdr.get("PROFILE") or "").upper() == "IPTAL"
                              or str(hdr.get("STATUS") or "") == "201"
                              or "İPTAL" in str(hdr.get("STATUS_DESC") or "").upper()),
            })
            return out
        # e-Fatura: giden (OUT) belge, yalnız başlık
        c = cli._get_efatura_client()
        ser = serialize_object(c.service.GetInvoice(
            REQUEST_HEADER=cli._make_header(),
            INVOICE_SEARCH_KEY={"UUID": invoice_uuid, "DIRECTION": "OUT"},
            HEADER_ONLY="Y")) or {}
        code, msg = _err(ser)
        if code:
            out["detail"] = f"{code}: {msg}"[:200]
            if any(t in msg.lower() for t in _DOGAN_NOT_FOUND_MARKERS):
                out["state"] = "not_found"
            return out
        invs = [i for i in (ser.get("INVOICE") or []) if isinstance(i, dict)]
        if not invs:
            out["state"] = "not_found"
            return out
        match = [i for i in invs if str(i.get("UUID") or "").strip().lower() == _u]
        if not match:
            # Sonuç döndü ama hiçbiri bizim UUID değil → filtre uygulanmamış olabilir; karar verilemez.
            out["detail"] = "Doğan sonuç döndürdü ama UUID eşleşmesi yok"
            return out
        _m0 = match[0]
        _no = str(_m0.get("ID") or _first(_m0.get("HEADER") or {}, ("ID", "INVOICE_ID")) or "").strip()
        if not _no:
            out["detail"] = "UUID bulundu ama fatura no okunamadı"
            _h0 = _m0.get("HEADER") or {}
            _sd = str(_h0.get("STATUS_DESCRIPTION") or _h0.get("STATUS_DESC") or "").lower()
            if any(t in _sd for t in _DOGAN_NOT_FOUND_MARKERS):
                out["state"] = "not_found"
            return out
        _h = _m0.get("HEADER") or {}
        out.update({"state": "found", "invoice_number": _no,
                    "status": f"{_h.get('STATUS') or ''} {_h.get('STATUS_DESCRIPTION') or ''}".strip()})
        return out
    except Exception as e:  # noqa: BLE001
        out["detail"] = f"sorgu hatası: {str(e)[:180]}"
        return out


def _dogan_recovered_result(lk: dict, invoice_uuid: str, prev: dict = None) -> dict:
    """Doğan'da bulunan (zaman aşımında kabul edilmiş) faturayı başarı sonucu biçimine çevirir."""
    return {
        "success": True, "code": "0", "already_sent": True, "recovered": True,
        "message": f"Doğan'da mevcut fatura kaydedildi (mükerrer gönderim engellendi) {lk.get('status') or ''}".strip(),
        "invoice_id": lk.get("invoice_number") or "",
        "web_key": lk.get("web_key") or "",
        "intl_txn_id": str((prev or {}).get("intl_txn_id") or ""),
        "uuid": invoice_uuid,
        "previous_message": str((prev or {}).get("message") or "")[:300],
    }


async def _efatura_used_numbers(epref: str, order_id: str) -> set:
    """e-Fatura boşluk-doldurma için DOLU sıra numaraları: kesilmiş faturalar + başka siparişlerde
    BEKLEYEN (Doğan'a gitmiş olabilecek) numaralar (BL-035)."""
    _rx = {"$regex": f"^{re.escape(epref)}"}
    used = set()
    async for _uo in db.orders.find(
        {"$or": [{"invoice_issued": True, "invoice_number": _rx},
                 {"invoice_pending.number": _rx, "id": {"$ne": order_id}}]},
        {"_id": 0, "invoice_number": 1, "invoice_issued": 1, "invoice_pending": 1}):
        for _v in ((_uo.get("invoice_number") if _uo.get("invoice_issued") else ""),
                   (_uo.get("invoice_pending") or {}).get("number")):
            _v = str(_v or "")
            if _v.startswith(epref):
                try:
                    used.add(int(_v[len(epref):]))
                except Exception:
                    pass
    return used


async def _invoice_set_pending(order_id: str, invoice_uuid: str, invoice_number: str,
                               invoice_type: str, prev_pending: dict = None):
    """Gönderimden ÖNCE seçilen UUID/numarayı siparişe yazar (BL-035)."""
    _now = datetime.now(timezone.utc).isoformat()
    _pp = prev_pending or {}
    await db.orders.update_one({"id": order_id}, {"$set": {"invoice_pending": {
        "uuid": invoice_uuid, "number": invoice_number, "type": invoice_type,
        "started_at": (_pp.get("started_at") if _pp.get("uuid") == invoice_uuid else None) or _now,
        "last_attempt_at": _now,
    }}})


async def _invoice_needs_review_exit(order_id: str, kind: str, invoice_uuid: str,
                                     invoice_number: str, reason: str):
    """Mükerrer/belirsiz durumda Doğan'da faturanın varlığı DOĞRULANAMADI → yeni belge KESİLMEZ.
    Sipariş incelemeye işaretlenir, kilit bırakılır (bekleyen UUID/numara korunur) ve 409 döner."""
    _msg = (f"{kind}: Doğan bu sipariş için faturanın zaten var olduğunu bildirdi ya da yanıt "
            f"belirsiz kaldı, ancak Doğan'da UUID {invoice_uuid} (no {invoice_number}) ile fatura "
            f"DOĞRULANAMADI ({str(reason or '')[:200]}). Çift fatura riskine karşı YENİ fatura "
            f"kesilmedi. Doğan portalında bu UUID/numarayı kontrol edin: fatura varsa 'Fatura "
            f"işlendi' ile numarasını kaydedin; yoksa fatura kaydını sıfırlayıp yeniden kesin.")
    await db.orders.update_one({"id": order_id}, {"$set": {
        "invoice_in_progress": False,
        "invoice_needs_review": True,
        "invoice_last_error": _msg,
        "invoice_last_error_at": datetime.now(timezone.utc).isoformat(),
    }})
    logger.error(f"[BL-035] fatura inceleme gerekli order={order_id} uuid={invoice_uuid} "
                 f"no={invoice_number}: {str(reason or '')[:200]}")
    raise HTTPException(status_code=409, detail=_msg)


async def _ensure_payment_reminder_templates():
    """order_payment_reminder SMS+e-posta şablonlarını (yoksa) idempotent tohumlar —
    böylece buton ilk basıldığında boş gitmez; sonra admin Bildirim Şablonları'ndan düzenler."""
    try:
        from .notifications import _DEFAULT_TEMPLATES, _EMAIL_HTML_TEMPLATES
        _now = datetime.now(timezone.utc).isoformat()
        for ch in ("sms", "email"):
            ex = await db.notification_templates.find_one(
                {"event": "order_payment_reminder", "channel": ch}, {"_id": 0})
            if ex:
                continue
            if ch == "sms":
                body, subj = _DEFAULT_TEMPLATES.get(("order_payment_reminder", "sms"), ""), ""
            else:
                rich = _EMAIL_HTML_TEMPLATES.get("order_payment_reminder", {})
                body, subj = rich.get("body", ""), rich.get("subject", "")
            if body:
                await db.notification_templates.insert_one({
                    "event": "order_payment_reminder", "channel": ch, "enabled": True,
                    "subject": subj, "body": body, "created_at": _now,
                })
    except Exception as _e:
        logger.warning(f"[payment_reminder seed] {_e}")


@router.post("/{order_id}/send-payment-reminder")
async def send_payment_reminder(order_id: str, current_user: dict = Depends(require_permission("orders.edit"))):
    """Havale/EFT ödemesi henüz alınmamış siparişe MÜŞTERİYE ödeme hatırlatma SMS'i (+e-posta)
    gönderir. 72 saatlik ödeme penceresinde elle tetiklenir. Şablon: 'order_payment_reminder'
    (Bildirim Şablonları'ndan düzenlenebilir)."""
    order = await db.orders.find_one({"id": order_id}, {"_id": 0})
    if not order:
        raise HTTPException(status_code=404, detail="Sipariş bulunamadı")
    pm = (order.get("payment_method") or "").lower()
    pstat = (order.get("payment_status") or "").lower()
    if pm not in _HAVALE_PMS:
        raise HTTPException(status_code=400, detail="Ödeme hatırlatması yalnızca Havale/EFT siparişlerinde gönderilebilir.")
    if pstat in _SETTLED_PAY:
        raise HTTPException(status_code=400, detail="Bu siparişin ödemesi zaten alınmış — hatırlatma gerekmez.")
    if (order.get("status") or "") in ("cancelled", "cancel_refunded", "returned", "refunded"):
        raise HTTPException(status_code=400, detail="İptal/iade edilmiş siparişe hatırlatma gönderilemez.")

    _addr = order.get("shipping_address") or {}
    _phone = _addr.get("phone") or order.get("phone")
    _email = _addr.get("email") or order.get("email")
    if not _phone and not _email:
        raise HTTPException(status_code=400, detail="Siparişte telefon/e-posta yok — hatırlatma gönderilemedi.")

    await _ensure_payment_reminder_templates()
    import sys as _sys, os as _os
    _sys.path.insert(0, _os.path.dirname(_os.path.dirname(__file__)))
    from notification_service import send_notification
    _vars = await _order_notify_vars(order)
    _res = await send_notification(db, "order_payment_reminder",
                                   to_phone=_phone, to_email=_email,
                                   variables=_vars, channels=["sms", "email"])
    # DÜZELTME: send_notification {"event","results":{"sms":{...},"email":{...}}} döndürür.
    # Önceki kod kanalları üst seviyeden (_res.get("sms")) okuyordu → HER ZAMAN None/False →
    # SMS gerçekten gitse bile "Gönderilemedi" yazıyor ve log sms:false kaydediyordu (mükerrer
    # gönderim riski). Doğru seviye: _res["results"].
    _results = _res.get("results") or {}
    _sms_ok = bool((_results.get("sms") or {}).get("success"))
    _mail_ok = bool((_results.get("email") or {}).get("success"))
    _sms_err = (_results.get("sms") or {}).get("error") or (_results.get("sms") or {}).get("response") or ""
    _now = datetime.now(timezone.utc).isoformat()
    await db.orders.update_one({"id": order_id}, {
        "$set": {"payment_reminder_last_at": _now},
        "$inc": {"payment_reminder_count": 1},
        "$push": {"payment_reminder_log": {"at": _now, "by": current_user.get("email", ""),
                                           "sms": _sms_ok, "email": _mail_ok}},
    })
    return {"success": _sms_ok or _mail_ok, "sms": _sms_ok, "email": _mail_ok,
            "phone_masked": ("***" + str(_phone)[-2:]) if _phone else "",
            "sent_at": _now,
            "message": ("Hatırlatma gönderildi." if (_sms_ok or _mail_ok)
                        else f"Gönderilemedi — {_sms_err or 'SMS/e-posta sağlayıcı veya şablon ayarını kontrol edin.'}")}


# #17: Fatura/kargo barkodu YALNIZ onaylanmış (confirmed) veya sonrası siparişe yapılabilir.
# Ödeme öncesi (pending/awaiting_payment/payment_notified) ya da iptal edilmiş sipariş engellenir.
_PRE_CONFIRM_STATUSES = {"pending", "awaiting_payment", "payment_notified", "cancelled"}
def _ensure_order_confirmed(order: dict, action: str = "işlem"):
    st = (order or {}).get("status") or ""
    if st in _PRE_CONFIRM_STATUSES:
        raise HTTPException(status_code=400,
            detail=f"Bu işlem ({action}) yalnızca ONAYLANMIŞ siparişe yapılabilir. Sipariş durumu: '{st}'. Önce siparişi 'Onaylandı' durumuna alın.")


@router.post("/{order_id}/create-invoice")
async def create_invoice_for_order(
    order_id: str,
    invoice_type: str = "auto",
    current_user: dict = Depends(require_permission("orders.invoice")),
):
    """
    Seçili sipariş için e-Arşiv / e-Fatura keser.

    invoice_type:
      - "auto" (default): Müşterinin VKN/TCKN'sini Doğan'da CheckUser ile sorgular,
        e-Fatura mükellefi ise e-Fatura, değilse veya VKN/TC boşsa e-Arşiv keser.
      - "e-arsiv": Zorla e-Arşiv (bireysel TCKN dolu/boş hepsi)
      - "e-fatura": Zorla e-Fatura (10 haneli VKN şart)
    """
    await _guard_restore_mode()   # ACİL ANAHTAR: geri yükleme modunda Doğan'a fatura gönderilmez → 423
    order = await db.orders.find_one({"id": order_id}, {"_id": 0})
    if not order:
        raise HTTPException(status_code=404, detail="Sipariş bulunamadı")
    _ensure_order_confirmed(order, "fatura kesme")  # #17
    # GÜVENLİK AĞI (HB4153705034): Hepsiburada kalem fiyatları zaten NET'tir; sipariş toplamı
    # kalem toplamından farklıysa fatura bu farkı "iskonto" sayıp EKSİK kesiyordu. Kayıt
    # bozuksa fatura KESİLMEZ, net hata döner (önce sipariş toplamı düzeltilmeli).
    if str(order.get("platform") or "").lower() == "hepsiburada" and not order.get("invoice_issued"):
        try:
            _hb_calc = round(sum(float(i.get("price") or 0) * int(i.get("quantity") or 1)
                                 for i in (order.get("items") or [])), 2)
            _hb_tot = round(float(order.get("total") or 0), 2)
        except Exception:
            _hb_calc = _hb_tot = 0.0
        if _hb_calc > 0 and abs(_hb_calc - _hb_tot) > 0.5:
            raise HTTPException(status_code=409, detail=(
                f"Hepsiburada sipariş toplamı ({_hb_tot:.2f} TL) kalemlerin toplamıyla "
                f"({_hb_calc:.2f} TL) uyuşmuyor — fatura eksik/fazla kesilmesin diye durduruldu. "
                f"Siparişi Hepsiburada'dan yeniden çekin."))
    if order.get("invoice_issued"):
        return {"success": True, "message": "Fatura zaten kesilmiş",
                "invoice_number": order.get("invoice_number", "")}

    # Havale/EFT siparişi onaylanmadan fatura KESİLMEZ — buton basılsa bile engelle.
    _hblk = _havale_invoice_block(order)
    if _hblk:
        raise HTTPException(status_code=400, detail=_hblk)

    # Mikro ihracat: gerçek e-Arşiv İSTİSNA faturası (InvoiceTypeCode=ISTISNA, KDV %0,
    # istisna 301 "11/1-a Mal ihracatı"). ETGB placeholder yerine normal e-Arşiv akışı;
    # build aşamasında export builder seçilir.
    if order.get("is_micro_export"):
        invoice_type = "e-arsiv"
    cfg = await db.providers_config.find_one({"kind": "einvoice"}, {"_id": 0})
    try:
        from routes.provider_settings import decrypt_provider_doc as _dpd
        cfg = _dpd("einvoice", cfg)   # A1.1: sırları çöz (at-rest şifreli)
    except Exception:
        pass
    active = (cfg or {}).get("active_provider")
    providers = (cfg or {}).get("providers") or {}

    # Yeni: Doğan ayarları settings.id=dogan_edonusum altında saklanıyor
    dogan_settings = await db.settings.find_one({"id": "dogan_edonusum"}, {"_id": 0}) or {}
    dogan_active = dogan_settings.get("enabled") and dogan_settings.get("username")

    if not (active and providers.get(active)) and not dogan_active:
        raise HTTPException(
            status_code=400,
            detail="Aktif e-fatura entegratörü yapılandırılmamış. Ayarlar > E-Arşiv / E-Fatura ekranından seçin."
        )

    # Prefix — e-Arşiv: FCT, e-Fatura: FCE (kullanıcı belirleyebilir; default Doğan standardı)
    if dogan_active:
        active = "dogan"
    else:
        pcfg = providers[active]
        # DENETİM FIX (#8): Yalnızca Doğan e-Dönüşüm gerçek gönderim yapıyor. Doğan aktif değilken
        # başka bir entegratör seçiliyse ALTTAKİ gönderim blokları (hepsi `dogan_active` koşullu)
        # ATLANIR → dogan_result None kalır AMA en sonda invoice_issued=True yazılıp SIRA NUMARASI
        # YAKILIYORDU (fatura gerçekte GÖNDERİLMEDEN 'faturalandı' görünüyordu). Bu yüzden Doğan-dışı
        # sağlayıcı canlıya alınana kadar burada reddedilir — numara boşa yakılmaz.
        # 501 DEĞİL: 5xx'i ara katman (Cloudflare/Railway) jenerik hata sayfasıyla değiştirip
        # bu yönlendirme mesajını siler → kullanıcı yalnız "Fatura oluşturulamadı" görür. Yapılandırma
        # hatası 400 ile döndürülür ki mesaj gövdesiyle ekrana ulaşsın.
        raise HTTPException(
            status_code=400,
            detail=(f"Seçili e-fatura entegratörü ('{active}') için gerçek gönderim henüz "
                    "implemente değil. Lütfen Ayarlar > E-Dönüşüm ekranından Doğan e-Dönüşüm'ü "
                    "etkinleştirin (kullanıcı adı/şifre girip 'Aktif' yapın)."),
        )

    # ─── AKILLI HİBRİT MOD ─────────────────────────────────────────────
    # invoice_type="auto" (default): VKN/TCKN dolu ise Doğan CheckUser ile
    # mükellef sorgula → mükellef ise e-Fatura, değilse e-Arşiv. Boşsa e-Arşiv.
    # KVKK: TCKN/VKN at-rest şifreli → fatura (UBL) için GERÇEK değer yalnız bu yerel kopyada çözülür.
    # _pv asla veritabanına yazılmaz; `order` mühürlü haliyle kalır.
    _pv = _pii_reveal_doc(order)
    bill = dict(_pv.get("billing_address") or {})
    ship_addr = order.get("shipping_address") or {}
    # Site (meluni) KURUMSAL fatura talebi bilgileri billing_address'te DEĞİL, billing_info'da
    # tutulur (is_corporate/company_name/tax_office/tax_number). Marketplace'te billing_address dolu gelir.
    # billing_address boşsa billing_info'dan tamamla → kurumsal site siparişinde VKN bulunup CheckUser
    # ile e-Fatura'ya yükseltilebilsin. (Aksi halde VKN boş kalıp yanlışlıkla e-Arşiv kesiliyordu.)
    _binfo = _pv.get("billing_info") or {}
    for _bk in ("tax_number", "tax_office", "company_name"):
        if not bill.get(_bk) and _binfo.get(_bk):
            bill[_bk] = _binfo.get(_bk)
    customer_vkn_raw = (bill.get("tax_number") or bill.get("tax_no") or bill.get("vkn") or "").strip().replace(" ", "")
    if not customer_vkn_raw:
        # Bireysel müşteri: Trendyol TCKN'i identityNumber alanında gelir
        customer_vkn_raw = (str(_pv.get("trendyol_identity_number") or "")).strip().replace(" ", "")
    receiver_alias = ""

    # Alıcı e-fatura mükellefi ise e-arşiv DÜZENLENEMEZ (Doğan 10013). Bu nedenle
    # mikro ihracat DIŞINDAKİ e-arşiv taleplerini de mükellef sorgusundan geçir:
    # mükellef → e-fatura'ya yükselt; değilse / VKN yoksa e-arşiv kalır.
    if invoice_type in ("auto", "e-arsiv") and not order.get("is_micro_export") and dogan_active:
        if customer_vkn_raw and len(customer_vkn_raw) in (10, 11):
            # Doğan'a sor
            try:
                from dogan_client import DoganClient as _DC
                from fastapi.concurrency import run_in_threadpool as _rtp
                _tmp = _DC(
                    username=dogan_settings["username"],
                    password=dogan_settings["password"],
                    is_test=dogan_settings.get("is_test", True),
                )
                chk = await _rtp(_tmp.check_user, customer_vkn_raw)
                # Mükellef ise e-Fatura'ya yükselt — VKN (10) VEYA e-Fatura mükellefi
                # TCKN'li şahıs firması (11). Builder TCKN/VKN şemasını otomatik kurar.
                if chk.get("is_efatura"):
                    invoice_type = "e-fatura"
                    receiver_alias = chk.get("invoice_alias") or ""
                else:
                    invoice_type = "e-arsiv"
            except Exception as _e:
                logger.warning(f"CheckUser fallback to e-arsiv: {_e}")
                invoice_type = "e-arsiv"
        else:
            # VKN/TCKN boş → bireysel e-arşiv
            invoice_type = "e-arsiv"

    if dogan_active:
        prefix = (dogan_settings.get("earchive_prefix") if invoice_type == "e-arsiv"
                  else dogan_settings.get("einvoice_prefix"))
        if not prefix:
            prefix = "FCT" if invoice_type == "e-arsiv" else "FCE"
    else:
        prefix = (pcfg.get("earchive_prefix") if invoice_type == "e-arsiv"
                  else pcfg.get("einvoice_prefix"))
        if not prefix:
            prefix = "FCT" if invoice_type == "e-arsiv" else "FCE"

    # ─── ATOMİK KİLİT (çift tıklama / eşzamanlı istek koruması) ──────────
    # Sayaç artırma + Doğan'a gönderim geri döndürülemez. Aynı sipariş için
    # ikinci eşzamanlı isteği atomik olarak engelle. 2 dk'dan eski kilit
    # "stale" sayılır (önceki deneme çökmüşse otomatik yeniden denenebilir).
    _lock_now = datetime.now(timezone.utc)
    _stale_iso = (_lock_now - timedelta(minutes=2)).isoformat()
    _claim = await db.orders.find_one_and_update(
        {"id": order_id, "invoice_issued": {"$ne": True},
         "$or": [{"invoice_in_progress": {"$ne": True}},
                 {"invoice_in_progress_at": {"$lt": _stale_iso}}]},
        {"$set": {"invoice_in_progress": True,
                  "invoice_in_progress_at": _lock_now.isoformat()}},
    )
    if _claim is None:
        _fresh = await db.orders.find_one({"id": order_id}, {"_id": 0}) or {}
        if _fresh.get("invoice_issued"):
            return {"success": True, "message": "Fatura zaten kesilmiş",
                    "invoice_number": _fresh.get("invoice_number", "")}
        raise HTTPException(status_code=409,
            detail="Bu sipariş için fatura işlemi şu anda sürüyor. Lütfen birkaç saniye bekleyip tekrar deneyin.")

    # ─── BL-035: BEKLEYEN FATURA (önceki denemede Doğan'a gitmiş olabilir) ─────────
    # Önceki deneme zaman aşımı/kesinti ile bittiyse siparişte `invoice_pending` (UUID+no+tip)
    # kalır. Bu durumda (bayat kilit devralınsa bile) YENİ UUID/numara ÜRETİLMEZ: önce Doğan'da
    # bu UUID sorgulanır → varsa kesilmiş fatura olarak kaydedilir; yoksa AYNI UUID/numara ile
    # yeniden gönderilir; doğrulanamazsa inceleme işaretlenip durulur.
    _pending = dict(((_claim or {}).get("invoice_pending")) or {})
    _pnum = str(_pending.get("number") or "")
    _reuse_pending = bool(dogan_active and _pending.get("uuid") and len(_pnum) > 13
                          and _pnum[-13:].isdigit()
                          and _pending.get("type") in ("e-arsiv", "e-fatura"))
    _recovered_result = None
    _used = set()
    if _reuse_pending:
        if _pending["type"] != invoice_type:
            logger.warning(f"[BL-035] order {order_id}: bekleyen fatura tipi {_pending['type']} "
                           f"korunuyor (istenen/çözülen: {invoice_type})")
            invoice_type = _pending["type"]
        invoice_uuid = str(_pending["uuid"])
        invoice_number = _pnum
        prefix = invoice_number[:-13]
        year_str = invoice_number[-13:-9]
        seq = int(invoice_number[-9:])
        seq_key = f"invoice_seq_{prefix}{year_str}"
        from dogan_client import DoganClient as _DCp
        from fastapi.concurrency import run_in_threadpool as _rtp_p
        _pcli = _DCp(
            username=dogan_settings["username"],
            password=dogan_settings["password"],
            is_test=dogan_settings.get("is_test", True),
        )
        _plk = await _rtp_p(_dogan_lookup_invoice_sync, _pcli, invoice_type, invoice_uuid)
        _pkind = "e-Arşiv" if invoice_type == "e-arsiv" else "e-Fatura"
        if _plk.get("state") == "found" and not _plk.get("cancelled"):
            logger.warning(f"[BL-035] order {order_id}: bekleyen {_pkind} Doğan'da MEVCUT "
                           f"({_plk.get('invoice_number')}) → yeni fatura kesilmeden kaydediliyor")
            invoice_number = _plk.get("invoice_number") or invoice_number
            _recovered_result = _dogan_recovered_result(_plk, invoice_uuid)
        elif _plk.get("state") == "found":
            await _invoice_needs_review_exit(order_id, _pkind, invoice_uuid, invoice_number,
                                             f"Doğan'da bu UUID İPTAL durumda ({_plk.get('status')})")
        elif _plk.get("state") != "not_found":
            await _invoice_needs_review_exit(order_id, _pkind, invoice_uuid, invoice_number,
                                             _plk.get("detail") or "Doğan sorgusu sonuçsuz")
        else:
            logger.warning(f"[BL-035] order {order_id}: bekleyen {_pkind} Doğan'da YOK → aynı "
                           f"UUID/numara ({invoice_number}) ile yeniden gönderilecek")
    else:
        # Sıra numarası: atomik sayaç (db.counters) — başarısız denemede bile ilerler,
        # aynı numara BİR DAHA üretilmez (Doğan 10009 "duplicate" önlenir).
        # Taban: panelde en son kesilen no'nun DEVAMI — dogan_edonusum.earchive_start_number
        # (yıl bazlı: earchive_start_year ilgili yıla eşitse uygulanır).
        year_str = datetime.now(timezone.utc).strftime("%Y")
        seq_key = f"invoice_seq_{prefix}{year_str}"
        base_start = 0
        try:
            if invoice_type == "e-arsiv":
                if str((dogan_settings or {}).get("earchive_start_year") or "") == year_str:
                    base_start = int((dogan_settings or {}).get("earchive_start_number") or 0)
            else:
                if str((dogan_settings or {}).get("einvoice_start_year") or "") == year_str:
                    base_start = int((dogan_settings or {}).get("einvoice_start_number") or 0)
        except Exception:
            base_start = 0
        _existing_ctr = await db.counters.find_one({"_id": seq_key})
        if _existing_ctr is None and base_start > 0:
            await db.counters.update_one(
                {"_id": seq_key}, {"$setOnInsert": {"seq": base_start - 1}}, upsert=True
            )
        # O2: Atomik artır-ve-oku. Önceki $inc + ayrı find_one, eşzamanlı iki fatura isteğinde aynı
        # seq'i okuyup AYNI fatura numarasını üretebiliyordu. find_one_and_update tekilliği garanti eder.
        _seq_doc = await db.counters.find_one_and_update(
            {"_id": seq_key}, {"$inc": {"seq": 1}}, upsert=True, return_document=ReturnDocument.AFTER
        ) or {}
        seq = int(_seq_doc.get("seq", 1))
        invoice_number = f"{prefix}{year_str}{seq:09d}"

        # ── E-FATURA "BOŞLUK DOLDURMA" KURALI (SADECE e-Fatura) ─────────────────────
        # Başlangıç no'sundan (einvoice_start_number, örn. 34) başlayıp KULLANILMAMIŞ ilk
        # numarayı seç; kesilmiş (invoice_issued) FCE numaralarını atla. Böylece 034'ten
        # başlar, aradaki boşlukları doldurur, 054/055/058 gibi kesilmişleri atlar, sonra
        # 059+ sıradan devam eder — GİB ardışıklığı korunur, numara boşa gitmez.
        # TABAN = einvoice_start_number (varsa); yoksa en düşük kesilmiş no. Böylece portalda
        # kesilen son no'nun DEVAMINDAN (34) doldurmaya başlar, min(used)'e takılmaz.
        # (e-Arşiv değişmez; atomik sayaç aynen çalışır. Eşzamanlılık: aynı no seçilirse
        #  Doğan 10009 döner, aşağıdaki mükerrer-retry döngüsü bir sonrakine geçer.)
        if invoice_type == "e-fatura":
            try:
                _epref = f"{prefix}{year_str}"
                # BL-035: başka siparişte BEKLEYEN (gönderilmiş olabilecek) numaralar da dolu sayılır.
                _used = await _efatura_used_numbers(_epref, order_id)
                # Taban: ayarlı başlangıç no (34) > 0 ise oradan; değilse en düşük kesilmiş no.
                if base_start > 0:
                    _n = base_start
                elif _used:
                    _n = min(_used)
                else:
                    _n = seq
                while _n in _used:
                    _n += 1
                seq = _n
                invoice_number = f"{prefix}{year_str}{seq:09d}"
            except Exception as _ge:
                logger.warning(f"[e-fatura bosluk-doldur] {_ge}")

        invoice_uuid = generate_id()  # UUID-like

    now = datetime.now(timezone.utc)
    issue_date = now.strftime("%Y-%m-%d")
    issue_time = now.strftime("%H:%M:%S")

    # HATA DÜZELTME (e-Fatura çökmesi): _is_mp ve _fs_invoice_waived e-Fatura dalında da
    # kullanılıyor ama YALNIZ e-Arşiv dalında hesaplanıyordu. invoice_type doğrudan/auto ile
    # "e-fatura" olunca e-Arşiv dalı çalışmaz → UnboundLocalError('_is_mp') → e-Fatura HİÇ
    # kesilemiyordu. Her iki daldan ÖNCE güvenli varsayılan tanımlanır; e-Arşiv dalı (site
    # siparişinde) _fs_invoice_waived'ı gerekiyorsa yeniden hesaplar. Pazaryeri e-Fatura'da
    # zaten kargo kalemi/iskonto olmaz → _fs_invoice_waived=0.0 doğru.
    _is_mp = (str(order.get("platform") or order.get("marketplace") or "").lower()
              in ("trendyol", "hepsiburada", "temu", "n11", "amazon"))
    _fs_invoice_waived = 0.0

    # Gerçek Doğan e-Arşiv kesimi
    dogan_result = None
    # BL-035: bekleyen fatura Doğan'da bulunduysa HİÇBİR şey yeniden gönderilmez.
    if _recovered_result is not None:
        dogan_result = _recovered_result
    if dogan_active and invoice_type == "e-arsiv" and _recovered_result is None:
        from dogan_client import DoganClient
        from fastapi.concurrency import run_in_threadpool

        # Müşteri VKN/TCKN — order'dan al, yoksa default 11111111111 (TCKN bilinmiyor)
        customer_vkn = customer_vkn_raw
        customer_name = (bill.get("company_name") or bill.get("name") or
                         f"{ship_addr.get('first_name','')} {ship_addr.get('last_name','')}".strip() or
                         "Bireysel Müşteri")
        if not customer_vkn:
            # TCKN yoksa Doğan kabul etmez — bireysel için varsayılan TCKN dön (test'te)
            customer_vkn = "11111111111"
        if order.get("is_micro_export"):
            # Mikro ihracat (İSTİSNA): yabancı alıcının TR VKN'i yok →
            # Trendyol gerçek VKN verdiyse (10/11 hane) onu, yoksa 2222222222.
            _mx_vkn = (bill.get("tax_number") or bill.get("tax_no") or "").strip().replace(" ", "")
            customer_vkn = _mx_vkn if len(_mx_vkn) in (10, 11) else "2222222222"

        # Site sipariş kalemlerinde barkod boş olabilir → ürün varyantından tamamla
        # (fatura "Barkod" sütunu için). Marketplace kalemlerinde barkod zaten dolu gelir.
        # NOT: kalemin product_id'si ürünün id'sine DEĞİL kart id'sine (urun_karti_id /
        # csv_card_id) denk gelebilir → ürün bulunamayıp barkod boş kalırdı. Artık üç
        # anahtarla da eşleştiriliyor, ayrıca SKU/beden/tek-varyant fallback'leri var.
        _bc_keys = list({str(it.get("product_id")) for it in (order.get("items") or [])
                         if it.get("product_id") and not it.get("barcode")})
        _bc_pmap = {}
        if _bc_keys:
            async for _p in db.products.find(
                {"$or": [{"id": {"$in": _bc_keys}},
                         {"urun_karti_id": {"$in": _bc_keys}},
                         {"csv_card_id": {"$in": _bc_keys}}]},
                {"_id": 0, "id": 1, "urun_karti_id": 1, "csv_card_id": 1,
                 "variants": 1, "barcode": 1}):
                for _k in (_p.get("id"), _p.get("urun_karti_id"), _p.get("csv_card_id")):
                    if _k:
                        _bc_pmap[str(_k)] = _p

        def _item_barcode(it):
            if it.get("barcode"):
                return it["barcode"]
            _p = _bc_pmap.get(str(it.get("product_id"))) or {}
            _vs = _p.get("variants") or []
            _sz = (it.get("size") or "").strip().lower()
            _cl = (it.get("color") or "").strip().lower()
            _sku = (it.get("sku") or it.get("product_code") or "").strip().lower()
            # 1) SKU eşleşmesi
            if _sku:
                for _v in _vs:
                    if str(_v.get("sku") or "").strip().lower() == _sku and _v.get("barcode"):
                        return _v["barcode"]
            # 2) beden + renk
            for _v in _vs:
                if (str(_v.get("size") or "").strip().lower() == _sz
                        and (not _cl or str(_v.get("color") or "").strip().lower() == _cl)
                        and _v.get("barcode")):
                    return _v["barcode"]
            # 3) sadece beden
            if _sz:
                for _v in _vs:
                    if str(_v.get("size") or "").strip().lower() == _sz and _v.get("barcode"):
                        return _v["barcode"]
            # 4) tek barkodlu varyant → onu kullan
            _withbc = [_v for _v in _vs if _v.get("barcode")]
            if len(_withbc) == 1:
                return _withbc[0]["barcode"]
            # 5) ürün-seviye barkod
            return _p.get("barcode") or ""

        _vat_map = await _product_vat_map(order)
        line_items = []
        for it in (order.get("items") or []):
            _bc = _item_barcode(it)
            line_items.append({
                "name": it.get("product_name") or it.get("name") or "Ürün",
                "qty": int(it.get("quantity") or 1),
                "unit_price": float(it.get("price") or 0),
                "kdv_rate": _item_kdv(it, _vat_map),
                "sku": it.get("sku") or it.get("product_code") or "",
                "barcode": _bc,
                # Doğan şablonu Barkod/Renk/Beden sütunlarını satır notundan parse eder:
                # Renk='Renk:'..';' arası, Beden='Beden:'..':' arası, Barkod=substring-after('Barcode:').
                # Açıklamaya (Item/Name) stok/beden YAZILMAZ; sütunlar bu nottan dolar.
                "note": ((
                    f"Renk:{(it.get('color') or '').strip()};"
                    f"Beden:{(it.get('size') or '').strip()}:"
                    f"Barcode:{_bc}"
                ) if (it.get('color') or it.get('size') or _bc) else ""),
            })


        # #14: Bedava kargo kampanyası → e-Arşiv faturasında BİLGİ NOTU. GİB-GÜVENLİ: monetary
        # tutar/matrah/KDV'ye DOKUNMAZ. (Ayrı "kargo iskonto kalemi" = belge-seviyesi AllowanceCharge
        # GİB LineExtension/matrah doğrulamasını bozduğu için kullanılamıyor — bkz. yukarıdaki not.)
        _fs_waived = float(order.get("free_shipping_waived_fee") or 0)
        _fs_note = ""
        if order.get("free_shipping_applied") and _fs_waived > 0:
            _fs_note = (f"Kargo Kampanyasi: {_fs_waived:.2f} TL tutarindaki kargo bedeli magazamizca "
                        f"karsilanmis olup fatura tutarina yansitilmamistir.")

        # KARGO İSKONTO SATIRI (kullanıcı isteği: 4000 TL+ bedava kargoyu faturada iskonto göster).
        # TÜM SİTE siparişleri için sağlam hesap: kayıtlı waived yoksa; kargo ÖDENMEMİŞ (bedava) +
        # eşik/kupon karşılanmışsa standart kargo ücretini ayardan türet. Pazaryeri siparişlerinde
        # kargo modeli farklı (bizim kampanya değil) → uygulanmaz. İşletme Kuralı ile anında kapatılır.
        # Pazaryeri (Trendyol/HB/Temu) siparişi mi? Faturada kargoya dair HİÇBİR kalem olmaz
        # (ne KARGO satırı ne iskonto) — kargo modeli pazaryerinde farklı.
        _is_mp = (str(order.get("platform") or order.get("marketplace") or "").lower()
                  in ("trendyol", "hepsiburada", "temu", "n11", "amazon"))
        _fs_invoice_waived = 0.0
        try:
            from business_rules import get_rule as _gr_fs
            if (not _is_mp) and await _gr_fs(db, "invoice.free_shipping_as_discount", True):
                _w = _fs_waived
                if _w <= 0 and float(order.get("shipping_cost") or 0) <= 0.001:
                    _mset = await db.settings.find_one(
                        {"id": "main"}, {"_id": 0, "shipping_fee": 1, "free_shipping_threshold": 1}) or {}
                    _fee = float(_mset.get("shipping_fee") or 0)
                    _thr = _mset.get("free_shipping_threshold")
                    _sub = float(order.get("subtotal") or 0)
                    _qual = (bool(order.get("free_shipping_applied")) or bool(order.get("coupon_free_shipping"))
                             or (_thr is not None and _sub >= float(_thr)))
                    if _fee > 0 and _qual:
                        _w = _fee
                _fs_invoice_waived = round(float(_w or 0), 2)
        except Exception as _fse:
            logger.warning(f"[kargo iskonto hesap {order_id}] {_fse}")
            _fs_invoice_waived = 0.0

        # İndirimi ÜRÜN satırlarının birim fiyatına dağıt + hizmet satırlarını ekle.
        # Tek kaynak: _apply_invoice_discounts (hediye çeki/puan iskonto DEĞİL; hediye
        # paketi/kapıda ödeme kendi %20 KDV'li satırında). Böylece her satırın KDV matrahı
        # indirimli tutardan hesaplanır, satır toplamı = tahsilat → GİB/Doğan geçerli.
        _inv_alloc = _apply_invoice_discounts(order, line_items)
        _inv_discount = _inv_alloc["discount"]
        for _xl in _inv_alloc["extra_lines"]:
            line_items.append(dict(_xl))

        # Taşıyan (kargo firması) — siparişin Trendyol'da beyan edilen kargosundan DİNAMİK.
        # Bilinen firmalar için ad+VKN; bilinmeyende Trendyol'un beyan adı, VKN boş (UBL'de atlanır).
        _cpn = (order.get("cargo_provider_name") or "")
        _cpl = _cpn.lower()
        if "trendyol" in _cpl:
            _carrier_name, _carrier_vkn, _carrier_city = "Trendyol Lojistik A.Ş.", "8590921777", "İstanbul"
        elif "mng" in _cpl:
            _carrier_name, _carrier_vkn, _carrier_city = "MNG KARGO YURTİÇİ VE YURTDIŞI TAŞIMACILIK A.Ş.", "6080712084", "İstanbul"
        elif ("yurtiçi" in _cpl) or ("yurtici" in _cpl):
            _carrier_name, _carrier_vkn, _carrier_city = "YURTİÇİ KARGO SERVİSİ A.Ş.", "9860008925", "İstanbul"
        elif "aras" in _cpl:
            _carrier_name, _carrier_vkn, _carrier_city = "ARAS KARGO YURT İÇİ YURT DIŞI TAŞIMACILIK A.Ş.", "0720039666", "İstanbul"
        elif "dhl" in _cpl:
            # Trendyol "DHL eCommerce" yurtiçinde MNG tarafından taşınır
            _carrier_name, _carrier_vkn, _carrier_city = "MNG KARGO YURTİÇİ VE YURTDIŞI TAŞIMACILIK A.Ş.", "6080712084", "İstanbul"
        elif _cpn.strip():
            # Bilinmeyen taşıyıcı: adı koru ama VKN boş kalmasın (Doğan CarrierParty'de VKN zorunlu)
            _carrier_name, _carrier_vkn, _carrier_city = _cpn.replace("Marketplace", "").strip(" -"), "6080712084", "İstanbul"
        else:
            _carrier_name, _carrier_vkn, _carrier_city = "MNG KARGO YURTİÇİ VE YURTDIŞI TAŞIMACILIK A.Ş.", "6080712084", "İstanbul"

        # Taksitli satış → vade farkı satırı (KDV Kanunu 24/c: matraha dahil, malın oranıyla).
        # Satır adı faturada İBARE görevi görür: "Vade Farkı (Taksit xN)".
        _vf, _charged_total, _inst_n = _order_vade_farki(order)
        _vf_note = ""
        if _vf >= 0.01:
            line_items.append({
                "name": f"Vade Farkı (Taksit x{_inst_n})",
                "qty": 1, "unit_price": _vf, "kdv_rate": _goods_kdv_rate(line_items, 10.0),
                "sku": "VADEFARKI", "barcode": "", "note": "",
            })
            _vf_note = (f"Taksitli satış ({_inst_n} taksit). Vade farkı (₺{_vf:.2f}) "
                        f"satış bedeline dahil olup KDV matrahına eklenmiştir.")

        # BEYAZ ETİKET: e-fatura tedarikçi bilgisi öncelik — e-fatura sağlayıcı ayarı →
        # merkezî Firma Bilgileri (company_info) → env varsayılanı.
        try:
            from company import get_company as _get_company
            _co = await _get_company(db)
        except Exception:
            _co = {}
        _earsiv_kwargs = dict(
            invoice_uuid=invoice_uuid,
            invoice_number=invoice_number,
            issue_date=issue_date,
            issue_time=issue_time,
            supplier_vkn=dogan_settings.get("vkn") or _co.get("tax_number") or "",
            supplier_name=dogan_settings.get("supplier_name") or _co.get("company_name") or "",
            supplier_district=dogan_settings.get("supplier_district") or "BEYLIKDÜZÜ",
            supplier_city=dogan_settings.get("supplier_city") or "İstanbul",
            supplier_street=dogan_settings.get("supplier_street") or _co.get("address") or "Kavaklı Mah. Meclis Sok. No:4/9 Referans Beylikdüzü D Blok D:19",
            supplier_tax_office=dogan_settings.get("supplier_tax_office") or _co.get("tax_office") or "BEYLİKDÜZÜ",
            supplier_phone=dogan_settings.get("supplier_phone") or _co.get("phone") or "",
            supplier_email=dogan_settings.get("supplier_email") or _co.get("email") or "",
            supplier_website=dogan_settings.get("supplier_website") or _co.get("website") or "",
            customer_vkn_or_tckn=customer_vkn,
            customer_name=customer_name,
            customer_district=ship_addr.get("district") or "",
            customer_city=ship_addr.get("city") or "",
            customer_street=ship_addr.get("address") or "",
            customer_postal_zone=ship_addr.get("postal_code") or ship_addr.get("zip") or ship_addr.get("postal_zone") or "",
            customer_phone=ship_addr.get("phone") or "",
            customer_email=ship_addr.get("email") or order.get("user_email") or "",
            customer_tax_office=bill.get("tax_office") or "",
            currency="TRY",
            kdv_rate=10.0,
            line_items=line_items,
            shipping_cost=(0.0 if _is_mp else float(order.get("shipping_cost") or 0)),
            free_shipping_waived=_fs_invoice_waived,
            discount=0.0,
            order_number=order.get("order_number") or order_id,
            payment_method=order.get("payment_method") or "DIGER",
            cargo_tracking=str(order.get("cargo_tracking_number") or order.get("cargo_tracking") or order.get("tracking_number") or ""),
            order_ext_id=str(order.get("marketplace_order_id") or order.get("platform_order_id") or order.get("order_number") or ""),
            store_name=order.get("store_name") or order.get("marketplace") or "",
            platform_label=_platform_display(order),
            payment_amount=(_charged_total if _vf >= 0.01 else float(order.get("total") or order.get("total_amount") or order.get("grand_total") or 0)),
            carrier_name=_carrier_name,
            carrier_vkn=_carrier_vkn,
            carrier_city=_carrier_city,
            note=" ".join(x for x in [_vf_note, _fs_note] if x),
        )
        # Mikro ihracat İSTİSNA: faturada alıcı = yabancı alıcı (billing_address), kargocu değil.
        if order.get("is_micro_export"):
            _buyer_name = f"{bill.get('first_name','')} {bill.get('last_name','')}".strip()
            if _buyer_name:
                _earsiv_kwargs["customer_name"] = _buyer_name
            _earsiv_kwargs["customer_district"] = bill.get("district") or ""
            _earsiv_kwargs["customer_city"] = bill.get("city") or ""
            _earsiv_kwargs["customer_street"] = bill.get("address") or ""
            _earsiv_kwargs["customer_postal_zone"] = bill.get("postal_code") or bill.get("zip") or bill.get("postal_zone") or ""
            _earsiv_kwargs["customer_phone"] = bill.get("phone") or ""
            if bill.get("country"):
                _earsiv_kwargs["customer_country"] = bill.get("country")
        _earsiv_builder = (DoganClient.build_earsiv_export_ubl_xml
                           if order.get("is_micro_export")
                           else DoganClient.build_earsiv_ubl_xml)
        ubl_xml = _earsiv_builder(**_earsiv_kwargs)

        dogan_client = DoganClient(
            username=dogan_settings["username"],
            password=dogan_settings["password"],
            is_test=dogan_settings.get("is_test", True),
        )
        # Müşteri e-postasına PDF gönder
        cust_email = (ship_addr.get("email") or order.get("user_email") or "").strip()
        archive_note = f"Sipariş No: {order.get('order_number') or order_id}"
        # BL-035: UUID+numara gönderimden ÖNCE siparişe yazılır (zaman aşımında aynısı yeniden kullanılır).
        await _invoice_set_pending(order_id, invoice_uuid, invoice_number, invoice_type, _pending)
        dogan_result = await run_in_threadpool(
            dogan_client.send_earsiv_invoice,
            ubl_xml, invoice_uuid, cust_email, archive_note,
        )
        # BL-035 mükerrer koruması: Doğan "zaten kayıtlı/mükerrer/10009" derse YENİ UUID ile
        # ikinci fatura KESİLMEZ. Önce Doğan'da bu UUID sorgulanır:
        #   bulundu → kesilmiş fatura olarak kaydet | yok → numara başka belgede (gerçek çakışma):
        #   yalnız NUMARA ilerler, UUID AYNI kalır | doğrulanamadı → inceleme, dur.
        _ex_tries = 0
        while (not dogan_result.get("success")) and _dogan_is_dup(dogan_result):
            _lk = await run_in_threadpool(_dogan_lookup_invoice_sync, dogan_client, "e-arsiv", invoice_uuid)
            if _lk.get("state") == "found" and not _lk.get("cancelled"):
                logger.warning(f"[BL-035] e-Arşiv mükerrer → Doğan'da MEVCUT ({_lk.get('invoice_number')}), "
                               f"kaydediliyor (order {order_id})")
                invoice_number = _lk.get("invoice_number") or invoice_number
                dogan_result = _dogan_recovered_result(_lk, invoice_uuid, dogan_result)
                break
            if _lk.get("state") != "not_found" or _lk.get("cancelled") or _ex_tries >= 5:
                await _invoice_needs_review_exit(
                    order_id, "e-Arşiv", invoice_uuid, invoice_number,
                    (f"Doğan: {dogan_result.get('message')} | sorgu: "
                     f"{'İPTAL ' + str(_lk.get('status')) if _lk.get('cancelled') else (_lk.get('detail') or _lk.get('state'))}"))
            _ex_tries += 1
            _sd = await db.counters.find_one_and_update(
                {"_id": seq_key}, {"$inc": {"seq": 1}}, upsert=True,
                return_document=ReturnDocument.AFTER) or {}
            seq = int(_sd.get("seq", 1))
            invoice_number = f"{prefix}{year_str}{seq:09d}"
            _earsiv_kwargs["invoice_number"] = invoice_number   # UUID DEĞİŞMEZ (BL-035)
            logger.warning(f"e-Arsiv numara cakismasi (UUID Doğan'da yok) -> siradaki: {invoice_number} "
                           f"(deneme {_ex_tries})")
            ubl_xml = _earsiv_builder(**_earsiv_kwargs)
            await _invoice_set_pending(order_id, invoice_uuid, invoice_number, invoice_type, _pending)
            dogan_result = await run_in_threadpool(
                dogan_client.send_earsiv_invoice,
                ubl_xml, invoice_uuid, cust_email, archive_note,
            )

        if not dogan_result.get("success"):
            logger.error(f"DOGAN_RET earsiv: {dogan_result}")
            # NUMARA GERİ AL: yalnız Doğan faturayı KESİN reddettiyse (ERROR_CODE dolu, mükerrer
            # değil) ya da istek hiç gitmediyse → fatura Doğan'da YOK; numara boşa gitmesin,
            # bekleyen UUID silinsin. Kodsuz hata (timeout/bağlantı) BELİRSİZ → fatura Doğan'a
            # ulaşmış olabilir: numara GERİ ALINMAZ, bekleyen UUID/numara KORUNUR (BL-035).
            _definite2 = _dogan_definite_reject(dogan_result)
            if _definite2:
                try:
                    await db.counters.find_one_and_update({"_id": seq_key, "seq": seq},
                                                          {"$inc": {"seq": -1}})
                except Exception:
                    pass
            # Hatayı log'a yaz, mock fallback ile devam etme — gerçek hata bildir
            _err_upd2 = {"$set": {
                "invoice_in_progress": False,
                "invoice_last_error": (f"e-Arşiv: {dogan_result.get('message')}" if _definite2 else
                                       f"e-Arşiv: {dogan_result.get('message')} — Doğan yanıtı belirsiz; "
                                       f"UUID {invoice_uuid} / no {invoice_number} saklandı, yeniden "
                                       f"denemede önce Doğan'da sorgulanır (çift fatura kesilmez)."),
                "invoice_last_error_at": datetime.now(timezone.utc).isoformat(),
            }}
            if _definite2:
                _err_upd2["$unset"] = {"invoice_pending": ""}
            await db.orders.update_one({"id": order_id}, _err_upd2)
            # NOT 502: bir "gateway" durum kodu; Cloudflare/Railway ara katmanı 5xx görünce
            # KENDİ jenerik hata sayfasını koyup JSON gövdeyi (gerçek Doğan mesajını) SİLER →
            # frontend'e `detail` ulaşmaz, kullanıcı yalnız "Fatura oluşturulamadı" görür. Bu bir
            # İŞ hatası (Doğan faturayı reddetti; alıcı/VKN/adres/kontör vb.) → 400 ile döndür:
            # 4xx gövdesiyle birlikte ara katmandan GEÇER, kullanıcı gerçek sebebi okur.
            raise HTTPException(
                status_code=400,
                detail=_dogan_error_detail("e-Arşiv", dogan_result.get("message"))
            )

    # ─── Doğan e-Fatura (TEMELFATURA) kesimi ─────────────────────────
    if dogan_active and invoice_type == "e-fatura" and _recovered_result is None:
        from dogan_client import DoganClient
        from fastapi.concurrency import run_in_threadpool

        customer_vkn = customer_vkn_raw
        if not customer_vkn or len(customer_vkn) not in (10, 11):
            raise HTTPException(
                status_code=400,
                detail="e-Fatura için 10 (VKN) veya 11 (TCKN) haneli kimlik gerekli, müşteride yok."
            )
        _ef_scheme = "TCKN" if len(customer_vkn) == 11 else "VKN"
        customer_name = (bill.get("company_name") or bill.get("name") or
                         f"{ship_addr.get('first_name','')} {ship_addr.get('last_name','')}".strip() or
                         "Müşteri")
        # Birey (TCKN) için Ad/Soyad — Person bloğu builder'da bundan kurulur.
        # Tüzel (VKN) için Person YOK; PartyName = firma unvanı kullanılır.
        _ef_first = ""
        _ef_family = ""
        if _ef_scheme == "TCKN":
            _ef_first = (ship_addr.get("first_name") or "").strip()
            _ef_family = (ship_addr.get("last_name") or "").strip()
            if not (_ef_first or _ef_family):
                _np = (customer_name or "").split()
                _ef_first = " ".join(_np[:-1]) if len(_np) > 1 else (customer_name or "")
                _ef_family = _np[-1] if len(_np) > 1 else ""

        # receiver_alias auto-mode'da çekildi; explicit invoice_type=e-fatura
        # çağrısında alias yoksa CheckUser ile tamamla
        if not receiver_alias:
            try:
                _tmp_cli = DoganClient(
                    username=dogan_settings["username"],
                    password=dogan_settings["password"],
                    is_test=dogan_settings.get("is_test", True),
                )
                chk = await run_in_threadpool(_tmp_cli.check_user, customer_vkn)
                receiver_alias = chk.get("invoice_alias") or ""
            except Exception:
                pass
        if not receiver_alias:
            raise HTTPException(
                status_code=400,
                detail=f"Müşteri ({customer_vkn}) e-Fatura mükellefi değil veya alias bulunamadı."
            )

        _vat_map = await _product_vat_map(order)
        line_items = []
        for it in (order.get("items") or []):
            line_items.append({
                "name": it.get("product_name") or it.get("name") or "Ürün",
                "qty": int(it.get("quantity") or 1),
                "unit_price": float(it.get("price") or 0),
                "kdv_rate": _item_kdv(it, _vat_map),
                "sku": it.get("sku") or it.get("product_code") or "",
                "buyer_sku": it.get("sku") or it.get("product_code") or "",
                "barcode": (it.get("barcode") or "").strip(),
                "color": (it.get("color") or "").strip(),
                "size": (it.get("size") or "").strip(),
                # Barkod/Renk/Beden fatura SATIR SÜTUNLARI → Doğan şablonu satır notundan (cbc:Note)
                # PARSE eder (e-Arşiv'de kanıtlanmış format). e-Fatura'da sütunlar boş çıkıyordu çünkü
                # not boş bırakılıyordu; artık e-Arşiv ile AYNI formatta doldurulur.
                "note": ((
                    f"Renk:{(it.get('color') or '').strip()};"
                    f"Beden:{(it.get('size') or '').strip()}:"
                    f"Barcode:{(it.get('barcode') or '').strip()}"
                ) if (it.get('color') or it.get('size') or (it.get('barcode') or '').strip()) else ""),
            })

        # e-Fatura'da da kupon/kampanya/havale indirimini UYGULA (eskiden discount=0.0
        # geçiliyordu → indirimli/havale ödeyen kurumsal müşteride matrah BRÜT kalıyor,
        # PayableAmount müşterinin ödediğinden fazla düzenleniyordu). e-Arşiv ile TEK
        # KAYNAK: _apply_invoice_discounts — hediye paketi/kapıda ödeme kendi %20'lik
        # satırında, indirim kampanya kapsamına göre dağıtılır, kalan son satıra biner.
        _inv_alloc_ef = _apply_invoice_discounts(order, line_items)
        _inv_disc_ef = _inv_alloc_ef["discount"]
        for _xl in _inv_alloc_ef["extra_lines"]:
            line_items.append(dict(_xl))

        # Taşıyan (kargo firması) — Trendyol'da beyan edilen kargodan DİNAMİK (e-Arşiv ile aynı eşleme)
        _cpn = (order.get("cargo_provider_name") or "")
        _cpl = _cpn.lower()
        if "trendyol" in _cpl:
            _carrier_name, _carrier_vkn = "Trendyol Lojistik A.Ş.", "8590921777"
        elif "mng" in _cpl:
            _carrier_name, _carrier_vkn = "MNG KARGO YURTİÇİ VE YURTDIŞI TAŞIMACILIK A.Ş.", "6080712084"
        elif ("yurtiçi" in _cpl) or ("yurtici" in _cpl):
            _carrier_name, _carrier_vkn = "YURTİÇİ KARGO SERVİSİ A.Ş.", "9860008925"
        elif "aras" in _cpl:
            _carrier_name, _carrier_vkn = "ARAS KARGO YURT İÇİ YURT DIŞI TAŞIMACILIK A.Ş.", "0720039666"
        elif "dhl" in _cpl:
            _carrier_name, _carrier_vkn = "MNG KARGO YURTİÇİ VE YURTDIŞI TAŞIMACILIK A.Ş.", "6080712084"
        elif _cpn.strip():
            _carrier_name, _carrier_vkn = _cpn.replace("Marketplace", "").strip(" -"), ""
        else:
            _carrier_name, _carrier_vkn = "", ""

        # Taksitli satış → vade farkı satırı (KDV Kanunu 24/c). Satır adı = İBARE.
        _vf, _charged_total, _inst_n = _order_vade_farki(order)
        if _vf >= 0.01:
            line_items.append({
                "name": f"Vade Farkı (Taksit x{_inst_n})",
                "qty": 1, "unit_price": _vf, "kdv_rate": _goods_kdv_rate(line_items, 10.0),
                "sku": "VADEFARKI", "barcode": "", "note": "",
            })

        # BEYAZ ETİKET: tedarikçi fallback — e-fatura sağlayıcı ayarı → Firma Bilgileri → varsayılan.
        try:
            from company import get_company as _get_company
            _co = await _get_company(db)
        except Exception:
            _co = {}
        _efatura_kwargs = dict(
            invoice_uuid=invoice_uuid,
            invoice_number=invoice_number,
            issue_date=issue_date,
            issue_time=issue_time,
            supplier_vkn=dogan_settings.get("vkn") or _co.get("tax_number") or "",
            supplier_name=dogan_settings.get("supplier_name") or _co.get("company_name") or "",
            supplier_district=dogan_settings.get("supplier_district") or "BEYLIKDÜZÜ",
            supplier_city=dogan_settings.get("supplier_city") or "İstanbul",
            supplier_street=dogan_settings.get("supplier_street") or _co.get("address") or "Kavaklı Mah. Meclis Sok. No:4/9 Referans Beylikdüzü D Blok D:19",
            supplier_tax_office=dogan_settings.get("supplier_tax_office") or _co.get("tax_office") or "BEYLİKDÜZÜ",
            supplier_website=dogan_settings.get("supplier_website") or _co.get("website") or "",
            customer_vkn=customer_vkn,
            customer_id_scheme=_ef_scheme,
            customer_tax_office=bill.get("tax_office") or "",
            customer_first_name=(_ef_first if _ef_scheme == "TCKN" else ""),
            customer_family_name=(_ef_family if _ef_scheme == "TCKN" else ""),
            customer_name=customer_name,
            customer_street=ship_addr.get("address") or "",
            customer_district=ship_addr.get("district") or "",
            customer_city=ship_addr.get("city") or "İstanbul",
            customer_postal_zone=ship_addr.get("postal_code") or "34000",
            customer_email=ship_addr.get("email") or order.get("user_email") or "",
            currency="TRY",
            kdv_rate=10.0,
            line_items=line_items,
            shipping_cost=(0.0 if _is_mp else float(order.get("shipping_cost") or 0)),
            free_shipping_waived=_fs_invoice_waived,
            discount=0.0,
            order_number=order.get("order_number") or order_id,
            order_date=(order.get("created_at") or now.isoformat())[:10],
            order_ext_id=str(order.get("marketplace_order_id") or order.get("platform_order_id") or order.get("order_number") or ""),
            cargo_tracking=str(order.get("cargo_tracking_number") or order.get("cargo_tracking") or order.get("tracking_number") or ""),
            carrier_name=_carrier_name,
            carrier_vkn=_carrier_vkn,
            carrier_type="Tüzel",
            store_name=order.get("store_name") or order.get("marketplace") or "",
            platform_label=_platform_display(order),
            payment_method=order.get("payment_method") or "",
            payment_amount=(_charged_total if _vf >= 0.01 else float(order.get("total") or order.get("total_amount") or order.get("grand_total") or 0)),
            dispatch_date=str(order.get("shipped_at") or order.get("dispatch_date") or issue_date)[:10],
            # Senaryo: Ticari Fatura (alıcı 8 gün içinde kabul/red edebilir). Ayardan
            # değiştirilebilir (dogan_settings.einvoice_profile); varsayılan TİCARİ.
            profile_id=(dogan_settings.get("einvoice_profile") or "TICARIFATURA"),
        )
        ubl_xml = DoganClient.build_efatura_ubl_xml(**_efatura_kwargs)

        dogan_client = DoganClient(
            username=dogan_settings["username"],
            password=dogan_settings["password"],
            is_test=dogan_settings.get("is_test", True),
        )
        cust_email = (ship_addr.get("email") or order.get("user_email") or "").strip()
        sender_alias = (dogan_settings.get("sender_alias") or "").strip()
        if not sender_alias:
            # GÖNDERİCİ = GÖNDERİCİ BİRİM (GB) etiketi olmalıdır; POSTA KUTUSU (PK) DEĞİL.
            # Doğan'da iki ayrı etiket vardır:
            #   • Posta Etiketi (PK)  = urn:mail:defaultpk@<alan-adi>  → ALICI posta kutusu
            #   • Birim Etiketi (GB)  = urn:mail:defaultgb@meluni.com.tr  → GÖNDERİCİ birim
            # CheckUser invoice_alias PK (defaultpk) döndürür; onu gönderici verince Doğan
            # "Kullanıcının Gönderici ... işlem yetkisi yoktur!" hatası verir. Bu yüzden
            # gönderici birim etiketini (GB) kullanırız. Ticimax+Doğan ayar ekranındaki değer:
            #   E-Fatura Birim Etiketi: urn:mail:defaultgb@meluni.com.tr
            sender_alias = (dogan_settings.get("sender_unit_alias")
                            or "urn:mail:defaultgb@meluni.com.tr")
        logger.info(f"e-Fatura gönderim: receiver_alias={'dolu' if receiver_alias else 'BOŞ'}, sender_alias={'dolu' if sender_alias else 'BOŞ'}")
        # BL-035: UUID+numara gönderimden ÖNCE siparişe yazılır (zaman aşımında aynısı yeniden kullanılır).
        await _invoice_set_pending(order_id, invoice_uuid, invoice_number, invoice_type, _pending)
        dogan_result = await run_in_threadpool(
            dogan_client.send_efatura_invoice,
            ubl_xml, invoice_uuid, invoice_number,
            customer_vkn, receiver_alias, sender_alias, cust_email,
        )
        # KRİTİK (W11242 + BL-035): "UUID/ID daha önce gönderilmiş bir faturayla eşleşmektedir" /
        # mükerrer yanıtı, faturanın Doğan'da ZATEN OLUŞMUŞ olabileceğini gösterir (ilk deneme
        # timeout). YENİ UUID ile ikinci fatura KESİLMEZ. Önce Doğan'da bu UUID sorgulanır:
        #   bulundu → kesilmiş fatura olarak kaydet | yok → numara başka belgede (gerçek çakışma):
        #   yalnız NUMARA ilerler, UUID AYNI kalır | doğrulanamadı → (mesaj BİZİM UUID'imizi ve
        #   bir fatura no'sunu içeriyorsa onu kaydet, aksi halde) inceleme, dur.
        _ef_tries = 0
        while (not dogan_result.get("success")) and _dogan_is_dup(dogan_result):
            _lk = await run_in_threadpool(_dogan_lookup_invoice_sync, dogan_client, "e-fatura", invoice_uuid)
            if _lk.get("state") == "found":
                logger.warning(f"[BL-035] e-Fatura mükerrer → Doğan'da MEVCUT ({_lk.get('invoice_number')}), "
                               f"kaydediliyor (order {order_id})")
                invoice_number = _lk.get("invoice_number") or invoice_number
                dogan_result = _dogan_recovered_result(_lk, invoice_uuid, dogan_result)
                break
            if _lk.get("state") != "not_found":
                import re as _re_inv
                _m_txt = str((dogan_result or {}).get("message", "") or "")
                _mm = _re_inv.search(r'((?:FCE|FCT|FCA|EAR|FAT)\w*\d{6,})', _m_txt)
                if _mm and invoice_uuid.lower() in _m_txt.lower():
                    _exist_no = _mm.group(1)
                    logger.warning(f"e-Fatura zaten kayıtlı ({_exist_no}, mesajda bizim UUID) → başarı "
                                   f"sayıldı, mevcut no kaydedildi (order {order_id})")
                    invoice_number = _exist_no
                    dogan_result = {**(dogan_result or {}), "success": True, "already_sent": True,
                                    "existing_number": _exist_no, "message": _m_txt}
                    break
                await _invoice_needs_review_exit(
                    order_id, "e-Fatura", invoice_uuid, invoice_number,
                    f"Doğan: {dogan_result.get('message')} | sorgu: {_lk.get('detail') or _lk.get('state')}")
            if _ef_tries >= 5:
                await _invoice_needs_review_exit(
                    order_id, "e-Fatura", invoice_uuid, invoice_number,
                    f"5 numara denendi, Doğan hâlâ mükerrer diyor: {dogan_result.get('message')}")
            _ef_tries += 1
            # BOŞLUK-DOLDURMA ile uyumlu: mükerrer no'da atomik sayaca DEĞİL, yerel seq'e +1
            # (bir sonraki boşluğa/serbest no'ya geç). Sayaç ileriye kaçmaz. UUID DEĞİŞMEZ.
            if not _used:
                try:
                    _used = await _efatura_used_numbers(f"{prefix}{year_str}", order_id)
                except Exception:
                    _used = set()
            seq += 1
            while seq in _used:
                seq += 1
            invoice_number = f"{prefix}{year_str}{seq:09d}"
            _efatura_kwargs["invoice_number"] = invoice_number
            logger.warning(f"e-Fatura numara cakismasi (UUID Doğan'da yok) -> siradaki: {invoice_number} "
                           f"(deneme {_ef_tries})")
            ubl_xml = DoganClient.build_efatura_ubl_xml(**_efatura_kwargs)
            await _invoice_set_pending(order_id, invoice_uuid, invoice_number, invoice_type, _pending)
            dogan_result = await run_in_threadpool(
                dogan_client.send_efatura_invoice,
                ubl_xml, invoice_uuid, invoice_number,
                customer_vkn, receiver_alias, sender_alias, cust_email,
            )

        if not dogan_result.get("success"):
            logger.error(f"DOGAN_RET efatura: {dogan_result}")
            # NUMARA GERİ AL: yalnız Doğan faturayı KESİN reddettiyse (ERROR_CODE dolu, mükerrer
            # değil: INVALID XML, validation, red) ya da istek hiç gitmediyse → fatura Doğan'da YOK;
            # bu FCE numarası boşa gitmesin (GİB ardışıklığı), bekleyen UUID silinsin. Compare-and-set:
            # yalnız sayaç hâlâ bizim seq'imizdeyse azalt. Kodsuz hata (timeout/bağlantı) BELİRSİZ →
            # numara geri alınmaz, bekleyen UUID/numara KORUNUR (BL-035).
            _definite = _dogan_definite_reject(dogan_result)
            if _definite:
                try:
                    await db.counters.find_one_and_update({"_id": seq_key, "seq": seq},
                                                          {"$inc": {"seq": -1}})
                except Exception:
                    pass
            _err_upd = {"$set": {
                "invoice_in_progress": False,
                "invoice_last_error": (f"e-Fatura: {dogan_result.get('message')}" if _definite else
                                       f"e-Fatura: {dogan_result.get('message')} — Doğan yanıtı belirsiz; "
                                       f"UUID {invoice_uuid} / no {invoice_number} saklandı, yeniden "
                                       f"denemede önce Doğan'da sorgulanır (çift fatura kesilmez)."),
                "invoice_last_error_at": datetime.now(timezone.utc).isoformat(),
            }}
            if _definite:
                _err_upd["$unset"] = {"invoice_pending": ""}
            await db.orders.update_one({"id": order_id}, _err_upd)
            # 502 DEĞİL (bkz. e-Arşiv notu): 5xx'i ara katman jenerik gateway sayfasıyla
            # değiştirip gerçek mesajı sildiği için iş-hatası 400 ile döndürülür → mesaj ekrana ulaşır.
            raise HTTPException(
                status_code=400,
                detail=_dogan_error_detail("e-Fatura", dogan_result.get("message"))
            )

    await db.orders.update_one(
        {"id": order_id},
        {"$set": {
            "invoice_issued": True,
            "invoice_in_progress": False,
            "invoice_last_error": "",
            "invoice_number": invoice_number,
            "invoice_uuid": invoice_uuid,
            "invoice_type": invoice_type,
            "invoice_provider": active,
            "invoice_provider_response": dogan_result,
            "invoice_intl_txn_id": (dogan_result or {}).get("intl_txn_id", ""),
            "invoice_dogan_id": (dogan_result or {}).get("invoice_id", ""),
            "invoice_pdf_url": (dogan_result or {}).get("web_key", ""),
            "invoice_issued_at": now.isoformat(),
            "invoice_issued_by": current_user.get("email", ""),
            "invoice_needs_review": False,
            # BL-035: Doğan'da zaten var olan (zaman aşımında kabul edilmiş) fatura kaydedildiyse iz bırak.
            "invoice_recovered_from_dogan": bool((dogan_result or {}).get("recovered")
                                                 or (dogan_result or {}).get("already_sent")),
            "updated_at": now.isoformat(),
        },
         "$unset": {"invoice_pending": ""}}
    )

    # Log — integration_logs'a ekle
    try:
        from .marketplace_hub import log_integration_event
        await log_integration_event(
            marketplace=f"einvoice:{active}",
            action="invoice_create",
            status="success",
            direction="outbound",
            ref_id=order_id,
            message=f"{invoice_type} fatura oluşturuldu: {invoice_number} (Doğan: {dogan_result.get('message') if dogan_result else 'mock'})",
        )
    except Exception:
        pass

    # ─── Trendyol: fatura kesilir kesilmez ANINDA Trendyol'a yükle ──────────
    # Tüm fatura tipleri için (e-arşiv, e-fatura, mikro ihracat) — hiçbirini
    # atlamaz. Hata olsa bile fatura kesimi başarılı sayılır (sadece loglanır),
    # böylece toplu fatura akışı kesintisiz devam eder. Bu sayede manuel "linki
    # yapıştır" adımına gerek kalmaz; geç/hiç gitmeme sorunu ortadan kalkar.
    trendyol_upload = None
    try:
        if (order.get("platform") == "trendyol") and order.get("trendyol_package_id"):
            _web = ((dogan_result or {}).get("web_key") or order.get("invoice_pdf_url") or "").strip()
            _tmpl = (dogan_settings.get("earsiv_link_template") or "").strip()
            if _web.startswith("http"):
                _inv_link = _web
            elif _tmpl and _web:
                _inv_link = _tmpl.replace("{web_key}", _web)
            else:
                _inv_link = ""
            # e-FATURA LİNK (e-Arşiv YOLU DEĞİŞMEDEN): e-Arşiv Doğan'dan WEB_KEY (tam URL) döner
            # ve yukarıda kullanılır. e-Fatura'da WEB_KEY DÖNMEZ → link yukarıda boş kalır.
            # e-Fatura görüntüleme linki einvoice_link_template ile UUID/INVOICE_ID üzerinden
            # kurulur (Doğan e-Fatura portal görüntüleme URL şablonu). Şablon boşsa link
            # üretilmez ve aşağıda net bir hata kaydı düşer (yanlış link Trendyol'a GİTMEZ).
            if (not _inv_link) and invoice_type == "e-fatura":
                _eftmpl = (dogan_settings.get("einvoice_link_template") or "").strip()
                if _eftmpl:
                    _ef_uuid = str((dogan_result or {}).get("uuid") or invoice_uuid or "")
                    _ef_iid = str((dogan_result or {}).get("invoice_id") or "")
                    _ef_itxn = str((dogan_result or {}).get("intl_txn_id") or "")
                    _inv_link = (_eftmpl
                                 .replace("{uuid}", _ef_uuid)
                                 .replace("{ettn}", _ef_uuid)
                                 .replace("{invoice_id}", _ef_iid)
                                 .replace("{invoice_number}", invoice_number)
                                 .replace("{intl_txn_id}", _ef_itxn)).strip()
                if not _inv_link:
                    # OTOMATİK: Doğan'dan resmî e-Fatura PDF'ini çekip bizim imzalı public
                    # linkimizle sun (e-Arşiv WEB_KEY karşılığı). Kullanıcıdan hiçbir şey istenmez.
                    _inv_link = _einvoice_pdf_link(order_id)
            if _inv_link:
                from .integrations import upload_invoice_to_trendyol
                try:
                    await upload_invoice_to_trendyol(
                        order.get("order_number"),
                        {"invoice_link": _inv_link, "invoice_number": invoice_number},
                        current_user,
                    )
                    trendyol_upload = {"ok": True, "link": _inv_link}
                    await db.orders.update_one(
                        {"id": order_id},
                        {"$set": {"trendyol_invoice_uploaded": True, "trendyol_invoice_error": ""}},
                    )
                except HTTPException as _he:
                    _err = str(_he.detail)
                    trendyol_upload = {"ok": False, "error": _err}
                    await db.orders.update_one(
                        {"id": order_id},
                        {"$set": {"trendyol_invoice_uploaded": False, "trendyol_invoice_error": _err[:1000]}},
                    )
                    logger.error(f"[trendyol invoice auto-upload] {order.get('order_number')}: {_err}")
            else:
                if invoice_type == "e-fatura":
                    _err = ("e-Fatura icin gorunturleme linki uretilemedi: einvoice_link_template "
                            "ayarli degil. Ayarlar > E-Donusum ekranindan Dogan e-Fatura portal "
                            "goruntuleme URL sablonunu girin (ornek: https://.../view?uuid={uuid}).")
                else:
                    _err = ("Gecerli fatura linki uretilemedi: Dogan web_key bir URL degil ve "
                            "earsiv_link_template ayarli degil (Ayarlar > E-Arsiv / E-Fatura).")
                trendyol_upload = {"ok": False, "error": _err}
                await db.orders.update_one(
                    {"id": order_id},
                    {"$set": {"trendyol_invoice_uploaded": False, "trendyol_invoice_error": _err}},
                )
                logger.warning(f"[trendyol invoice] {order.get('order_number')}: {_err}")
    except Exception as _te:
        trendyol_upload = {"ok": False, "error": str(_te)}
        logger.error(f"[trendyol invoice auto-upload] {order.get('order_number')}: {_te}")

    # ─── Hepsiburada: fatura kesilir kesilmez ANINDA HB'ye yükle ───────────
    # (Trendyol için vardı, HB için YOKTU → HB faturaları kesiliyor ama HB'ye hiç
    # aktarılmıyordu. Artık HB de otomatik; manuel "gönder" adımına gerek kalmaz.)
    hb_upload = None
    try:
        if order.get("platform") == "hepsiburada":
            # Ortak link çözücü — e-Arşiv (http web_key / earsiv şablonu) + e-fatura (self-served).
            # dogan_result.web_key'i order alanı gibi kullanabilmek için order kopyasına enjekte et.
            _oview = dict(order)
            _rwk = ((dogan_result or {}).get("web_key") or order.get("invoice_pdf_url") or "").strip()
            if _rwk:
                _oview["invoice_pdf_url"] = _rwk
            _oview.setdefault("invoice_uuid", (dogan_result or {}).get("uuid") or invoice_uuid or "")
            _oview.setdefault("invoice_dogan_id", (dogan_result or {}).get("invoice_id") or "")
            _oview.setdefault("invoice_number", invoice_number)
            _hlink = _resolve_marketplace_invoice_link(_oview, dogan_settings, order_id, invoice_type, _rwk)
            if _hlink:
                import asyncio as _aio_hb
                from routes.category_mapping import _get_hb_client
                _hcli, _hcerr = await _get_hb_client()
                if _hcerr:
                    hb_upload = {"ok": False, "error": _hcerr}
                    await db.orders.update_one({"id": order_id}, {"$set": {
                        "hepsiburada_invoice_uploaded": False, "hepsiburada_invoice_error": _hcerr[:1000]}})
                else:
                    # ÇOK-PAKETLİ: faturayı siparişin TÜM HB paketlerine yükle (tek pakete yüklenip
                    # diğerleri 'yüklenmemiş' kalmasın).
                    _hres = await _hb_upload_invoice_all_packages(order, _hlink, _hcli)
                    hb_upload = {**_hres, "link": _hlink}
                    if _hres.get("ok"):
                        await db.orders.update_one({"id": order_id}, {"$set": {
                            "hepsiburada_invoice_uploaded": True, "hepsiburada_invoice_error": ""}})
                    else:
                        _herr = _hres.get("error") or "; ".join(
                            f"{p.get('pkg')}:{p.get('error')}" for p in _hres.get("packages", []) if not p.get("ok"))
                        await db.orders.update_one({"id": order_id}, {"$set": {
                            "hepsiburada_invoice_uploaded": False, "hepsiburada_invoice_error": str(_herr)[:1000]}})
                        logger.error(f"[hb invoice auto-upload] {order.get('order_number')}: {_herr}")
            else:
                _herr = "HB fatura linki üretilemedi (e-Arşiv: earsiv_link_template ayarlı mı? / uuid-dogan_id boş)."
                hb_upload = {"ok": False, "error": _herr}
                await db.orders.update_one({"id": order_id}, {"$set": {
                    "hepsiburada_invoice_uploaded": False, "hepsiburada_invoice_error": _herr}})
                logger.warning(f"[hb invoice] {order.get('order_number')}: {_herr}")
    except Exception as _hte:
        hb_upload = {"ok": False, "error": str(_hte)}
        logger.error(f"[hb invoice auto-upload] {order.get('order_number')}: {_hte}")

    return {
        "success": True,
        "message": "Fatura oluşturuldu",
        "invoice_number": invoice_number,
        "invoice_type": invoice_type,
        "provider": active,
        "trendyol_upload": trendyol_upload,
        "hb_upload": hb_upload,
    }


# ---------------------------------------------------------------------------
# MANUEL FATURA YÜKLEME — Doğan/otomatik entegratör kullanılMADAN, dışarıda
# kesilmiş faturayı (PDF/görsel) siparişe ekler. Kullanım: Doğan geçici down
# (503/TR7) veya elle kesim gereken durumlar. Orders.jsx handleUploadManualInvoice.
# ---------------------------------------------------------------------------
_MANUAL_INVOICE_TYPES = {"application/pdf", "image/jpeg", "image/png", "image/webp"}
_MANUAL_INVOICE_EXT = {"application/pdf": "pdf", "image/jpeg": "jpg",
                       "image/png": "png", "image/webp": "webp"}
_MAX_INVOICE_BYTES = 15 * 1024 * 1024  # 15 MB


@router.post("/{order_id}/upload-invoice")
async def upload_manual_invoice(
    order_id: str,
    file: UploadFile = File(...),
    invoice_number: str = Form(""),
    invoice_type: str = Form("manual"),
    current_user: dict = Depends(require_permission("orders.invoice")),
):
    """Manuel kesilmiş faturayı (PDF/görsel) siparişe yükler ve siparişi 'faturalandı'
    işaretler. Otomatik entegratör (Doğan) kullanılMAZ. Ön-engeller create-invoice ile
    AYNI (onaylı sipariş + havale onayı). Ödeme/statü/stok değişmezlerine DOKUNMAZ; yalnız
    fatura kaydını (dosya URL + no) tutar, son fatura hatasını/kilidini temizler."""
    order = await db.orders.find_one({"id": order_id}, {"_id": 0})
    if not order:
        raise HTTPException(status_code=404, detail="Sipariş bulunamadı")
    # create-invoice ile aynı ön-engeller: onaylanmamış / havale-onaysız siparişe fatura eklenmez.
    _ensure_order_confirmed(order, "manuel fatura yükleme")
    _hblk = _havale_invoice_block(order)
    if _hblk:
        raise HTTPException(status_code=400, detail=_hblk)

    ct = (file.content_type or "").split(";")[0].strip().lower()
    if ct not in _MANUAL_INVOICE_TYPES:
        raise HTTPException(status_code=400,
                            detail="Yalnız PDF veya görsel (JPEG/PNG/WebP) yüklenebilir.")
    from security.uploads import read_upload_capped
    data = await read_upload_capped(file, _MAX_INVOICE_BYTES, "Dosya çok büyük (maks 15MB).")
    if not data:
        raise HTTPException(status_code=400, detail="Boş dosya.")
    if len(data) > _MAX_INVOICE_BYTES:
        raise HTTPException(status_code=400, detail="Dosya çok büyük (maks 15MB).")

    _fname = f"{uuid.uuid4()}.{_MANUAL_INVOICE_EXT.get(ct, 'pdf')}"
    _url = ""
    _manual_file = None
    # 0) KVKK: GİZLİ ARŞİV (R2_ARCHIVE_BUCKET) tanımlıysa fatura dosyası YALNIZ oraya yazılır —
    #    herkese açık depoya/CDN'e gitmez. Siparişe süreli imzalı backend linki yazılır (panel ve
    #    pazaryeri linki çalışmaya devam eder); panel ayrıca oturumlu uçtan okuyabilir.
    #    Arşive yazılamazsa herkese açık depoya DÜŞÜLMEZ → net hata (tekrar denenir).
    try:
        from services import r2_storage as _r2a
        _arch_on = _r2a.archive_enabled()
    except Exception:
        _r2a, _arch_on = None, False
    if _arch_on:
        import asyncio as _aio_mi
        _akey = f"faturalar/manuel/{order_id}/{_fname}"
        try:
            await _aio_mi.to_thread(_r2a.archive_put, _akey, data, ct)
        except Exception as _ae:
            logger.error(f"[manuel fatura arşiv {order_id}] {_ae}")
            raise HTTPException(status_code=400,
                                detail="Fatura dosyası gizli arşive yüklenemedi; lütfen tekrar deneyin.")
        _manual_file = {"archive_key": _akey, "content_type": ct, "size": len(data),
                        "uploaded_at": datetime.now(timezone.utc).isoformat()}
        _url = _manual_invoice_signed_link(order_id, _MANUAL_INVOICE_LINK_DAYS_MARKETPLACE)
    # 1) Cloudflare R2 (arşiv YOKSA, eski davranış) → invoices/{order_id}/{fname}
    try:
        from services import r2_storage as _r2
        if (not _url) and _r2.is_enabled():
            _key = f"invoices/{order_id}/{_fname}"
            _url = _r2.put_object(_key, data, ct)
            await db.files.insert_one({
                "id": str(uuid.uuid4()), "storage_path": _fname, "r2_key": _key, "r2_url": _url,
                "original_filename": file.filename, "content_type": ct, "size": len(data),
                "is_deleted": False, "created_at": datetime.now(timezone.utc).isoformat(),
            })
    except Exception as _re:
        logger.error(f"[manuel fatura R2 {order_id}] {_re}")
        _url = ""
    # 2) Fallback: MongoDB (base64) → /api/upload/files/{fname} ile servis
    if not _url:
        import base64 as _b64
        await db.files.insert_one({
            "id": str(uuid.uuid4()), "storage_path": _fname, "original_filename": file.filename,
            "content_type": ct, "size": len(data), "data_b64": _b64.b64encode(data).decode("ascii"),
            "is_deleted": False, "created_at": datetime.now(timezone.utc).isoformat(),
        })
        _url = f"/api/upload/files/{_fname}"

    _now = datetime.now(timezone.utc).isoformat()
    _invno = (safe_str(invoice_number, 40) or "").strip() or \
             f"MANUEL-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
    _itype = ((safe_str(invoice_type, 20) or "").strip().lower()) or "manual"
    _inv_set = {
        "invoice_issued": True,
        "invoice_manual": True,
        "invoice_provider": "manual",
        "invoice_number": _invno,
        "invoice_type": _itype,
        "invoice_pdf_url": _url,
        "invoice_issued_at": _now,
        "invoice_issued_by": current_user.get("email", ""),
        "invoice_last_error": "",
        "invoice_in_progress": False,
        "invoice_needs_review": False,
        "updated_at": _now,
    }
    # BL-035: manuel fatura → bekleyen otomatik gönderim izi kapanır.
    _inv_unset = {"invoice_pending": ""}
    if _manual_file:
        _inv_set["invoice_manual_file"] = _manual_file
    else:
        _inv_unset["invoice_manual_file"] = ""
    await db.orders.update_one({"id": order_id}, {"$set": _inv_set, "$unset": _inv_unset})
    await _log_order_event(order_id, "invoice",
                           f"Manuel fatura yüklendi: {_invno} ({file.filename or _fname})",
                           current_user, {"invoice_number": _invno, "manual": True,
                                          # imzalı link olay günlüğüne YAZILMAZ (süreli erişim anahtarı)
                                          **({"archived": True} if _manual_file else {"url": _url})})
    return {"success": True, "invoice_issued": True, "invoice_number": _invno,
            "invoice_pdf_url": _url, "manual": True,
            "message": f"Manuel fatura yüklendi: {_invno}"}


async def _manual_invoice_file_response(order_id: str) -> Response:
    """Gizli arşivdeki manuel fatura dosyasını backend üzerinden servis eder (önbelleğe alınmaz)."""
    order = await db.orders.find_one({"id": order_id}, {"_id": 0, "invoice_manual_file": 1,
                                                          "invoice_number": 1, "order_number": 1})
    if not order:
        raise HTTPException(status_code=404, detail="Sipariş bulunamadı")
    _mf = order.get("invoice_manual_file") or {}
    _key = _mf.get("archive_key")
    if not _key:
        raise HTTPException(status_code=404, detail="Bu siparişte arşivlenmiş manuel fatura yok")
    from services import r2_storage as _r2a
    if not _r2a.archive_enabled():
        raise HTTPException(status_code=404, detail="Gizli arşiv deposu yapılandırılmamış")
    import asyncio as _aio_mf
    data, _ct = await _aio_mf.to_thread(_r2a.archive_get, _key)
    if data is None:
        raise HTTPException(status_code=404, detail="Fatura dosyası arşivde bulunamadı")
    ct = _mf.get("content_type") if _mf.get("content_type") in _MANUAL_INVOICE_TYPES else (
        _ct if _ct in _MANUAL_INVOICE_TYPES else "application/pdf")
    _base = re.sub(r"[^A-Za-z0-9_.-]", "_", str(order.get("invoice_number") or order.get("order_number") or "fatura"))[:60]
    return Response(content=data, media_type=ct, headers={
        "Content-Disposition": f'inline; filename="{_base}.{_MANUAL_INVOICE_EXT.get(ct, "pdf")}"',
        "Cache-Control": "private, no-store",
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "no-referrer",
    })


@router.get("/{order_id}/manual-invoice-file")
async def get_manual_invoice_file(order_id: str,
                                  current_user: dict = Depends(require_permission("orders.invoice"))):
    """Panel: gizli arşivdeki manuel faturayı oturumlu (orders.invoice yetkili) personele sunar."""
    return await _manual_invoice_file_response(order_id)


@router.get("/{order_id}/manual-invoice-file/signed")
async def get_manual_invoice_file_signed(order_id: str, request: Request, exp: int = 0, sig: str = ""):
    """Pazaryeri / müşteri linki: HMAC imzalı + SÜRELİ (exp=unix saniye) link. Oturum gerektirmez;
    imza sipariş+süreye bağlıdır, süresi dolan veya değiştirilen link 403 döner."""
    import hmac as _hmac
    if not sig or not exp or not _invoice_sig_ok(sig, lambda k: _manual_invoice_sig(order_id, exp, k)):
        await note_policy_denial(None, "order.manual_invoice_link", order_id=order_id, request=request,
                                 reason="bad_signature")
        raise HTTPException(status_code=403, detail="Geçersiz imza")
    if int(exp) < int(time.time()):
        raise HTTPException(status_code=403, detail="Linkin süresi dolmuş")
    return await _manual_invoice_file_response(order_id)


# ---------------------------------------------------------------------------
# FATURA YAZDIR (HTML) — Orders.jsx handleBulkPrintInvoices iframe src olarak
# bu endpoint'i kullanır. Basit bir A4 fatura HTML'i döner; gerçek XSL-UBL
# dönüşümü canlıda provider'dan gelen PDF URL'siyle değiştirilir.
# ---------------------------------------------------------------------------
@router.get("/{order_id}/invoice/print")
async def print_invoice_html(order_id: str, current_user: dict = Depends(require_permission("orders.invoice"))):
    """Basit fatura HTML çıktısı (yazdırılabilir). Bulk print için iframe.
    Yetki Authorization başlığından doğrulanır; URL'de oturum anahtarı kabul edilmez."""
    order = await db.orders.find_one({"id": order_id}, {"_id": 0})
    if not order:
        raise HTTPException(status_code=404, detail="Sipariş bulunamadı")

    addr = order.get("shipping_address") or {}
    items = order.get("items") or []
    total = order.get("total") or order.get("total_amount") or 0

    import html as _html
    def _h(v):  # GÜVENLİK: müşteri/pazaryeri alanlarını HTML-escape et (stored-XSS)
        return _html.escape(str(v if v is not None else ""))
    rows = "".join(
        f"<tr><td>{_h(i.get('product_name') or i.get('name') or '')}</td>"
        f"<td style='text-align:center'>{int(i.get('quantity',1) or 1)}</td>"
        f"<td style='text-align:right'>{(i.get('price') or 0):.2f} ₺</td>"
        f"<td style='text-align:right'>{((i.get('price') or 0)*(i.get('quantity') or 1)):.2f} ₺</td></tr>"
        for i in items
    )

    from fastapi.responses import HTMLResponse
    html = f"""
<!doctype html><html lang="tr"><head><meta charset="utf-8"/>
<title>Fatura — {_h(order.get('invoice_number') or order.get('order_number',''))}</title>
<style>
  @page {{ size: A4; margin: 15mm; }}
  body {{ font-family: -apple-system, Arial, sans-serif; color:#111; margin:0; }}
  .header {{ display:flex; justify-content:space-between; border-bottom:2px solid #111; padding-bottom:10px; margin-bottom:16px; }}
  .brand {{ font-size:20px; font-weight:800; letter-spacing:.1em; }}
  h1 {{ font-size:18px; margin:0 0 8px; }}
  table {{ width:100%; border-collapse:collapse; margin-top:12px; font-size:12px; }}
  th, td {{ padding:6px 8px; border-bottom:1px solid #eee; }}
  th {{ background:#f9fafb; text-align:left; }}
  .totals {{ margin-top:12px; text-align:right; }}
  .grand {{ font-size:16px; font-weight:800; margin-top:4px; }}
  .meta {{ font-size:11px; color:#555; }}
</style></head><body>
<div class="header">
  <div>
    <div class="brand">MELUNİ</div>
    <div class="meta">Meluni E-Ticaret · meluni.com.tr</div>
  </div>
  <div style="text-align:right">
    <h1>{'E-ARŞİV FATURA' if order.get('invoice_type')=='e-arsiv' else 'E-FATURA'}</h1>
    <div class="meta">No: <strong>{_h(order.get('invoice_number') or '-')}</strong></div>
    <div class="meta">Tarih: {_h((order.get('invoice_issued_at') or order.get('created_at') or '')[:10])}</div>
    <div class="meta">Sipariş: {_h(order.get('order_number',''))}</div>
  </div>
</div>
<div style="display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-bottom:12px;">
  <div>
    <div class="meta" style="font-weight:700">Alıcı:</div>
    <div>{_h(addr.get('full_name') or ((addr.get('first_name') or '') + ' ' + (addr.get('last_name') or '')).strip())}</div>
    <div class="meta">{_h(addr.get('address',''))}</div>
    <div class="meta">{_h(addr.get('district',''))} / {_h(addr.get('city',''))}</div>
    <div class="meta">Tel: {_h(addr.get('phone',''))}</div>
  </div>
  <div>
    <div class="meta" style="font-weight:700">Sipariş Kanalı:</div>
    <div>{_h(order.get('channel') or 'web')}</div>
    <div class="meta">Ödeme: {_h(order.get('payment_method','-'))}</div>
  </div>
</div>
<table>
  <thead><tr><th>Ürün</th><th style="text-align:center">Adet</th><th style="text-align:right">Birim</th><th style="text-align:right">Tutar</th></tr></thead>
  <tbody>{rows or '<tr><td colspan=4 style="text-align:center">Kalem yok</td></tr>'}</tbody>
</table>
<div class="totals">
  <div class="grand">Toplam: {float(total):.2f} ₺</div>
</div>
<p class="meta" style="margin-top:24px;border-top:1px solid #eee;padding-top:10px;">
  Bu belge e-Arşiv fatura olup, aktif entegratör
  <strong>{_h(order.get('invoice_provider') or '-')}</strong> üzerinden üretilmiştir.
</p>
</body></html>
"""
    return HTMLResponse(content=html, headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})


# ─── e-Fatura PDF public link (e-Arşiv WEB_KEY karşılığı) ───────────────────────
# e-Arşiv'de Doğan hazır bir WEB_KEY URL'i döner ve Trendyol/HB'ye o gönderilir.
# e-Fatura'da böyle bir URL DÖNMEZ → faturanın resmî PDF'i Doğan'dan çekilip BİZİM
# sunucumuzdan imzalı (order'a özel HMAC) bir link ile sunulur. Böylece e-Fatura da
# e-Arşiv gibi OTOMATİK linke kavuşur; kullanıcıdan hiçbir şey istenmez.
def _einvoice_pdf_sig(order_id: str) -> str:
    """ESKİ (v1) imza — SÜRESİZ ve doğrudan JWT_SECRET ile. YENİ LİNK ÜRETİLMEZ; yalnız
    pazaryerlerinde saklı eski linkler kırılmasın diye DOĞRULAMADA kabul edilir (kullanımı loglanır)."""
    import hmac as _hmac, hashlib as _hl
    from .deps import JWT_SECRET as _sec
    return _hmac.new(str(_sec).encode(), f"einv:{order_id}".encode(), _hl.sha256).hexdigest()[:32]


_EINVOICE_LINK_DAYS = 365  # pazaryeri faturayı geç çekebilir (manuel fatura linkiyle aynı süre)


def _einvoice_pdf_sig_v2(order_id: str, exp: int, key: bytes = None) -> str:
    """YENİ (v2) imza (denetim 2026-10): SÜRELİ + alan-ayrımlı anahtar (_manual_invoice_link_key:
    INVOICE_LINK_SECRET ya da JWT_SECRET'tan türetilmiş; JWT anahtarı doğrudan KULLANILMAZ)."""
    import hmac as _hmac, hashlib as _hl
    return _hmac.new(key or _manual_invoice_link_key(), f"einv:v2:{order_id}:{int(exp)}".encode(),
                     _hl.sha256).hexdigest()[:40]


def _einvoice_pdf_link(order_id: str) -> str:
    import os as _os
    base = (_os.environ.get("PUBLIC_API_URL") or _os.environ.get("BACKEND_URL")
            or "https://api.meluni.com.tr").rstrip("/")
    exp = int(time.time()) + _EINVOICE_LINK_DAYS * 86400
    return f"{base}/api/orders/{order_id}/einvoice-pdf?exp={exp}&sig={_einvoice_pdf_sig_v2(order_id, exp)}"


# ─── MANUEL FATURA DOSYASI — GİZLİ ARŞİV + SÜRELİ İMZALI LİNK (KVKK) ─────────────────────
# Manuel yüklenen fatura dosyaları artık herkese açık R2 deposuna DEĞİL, gizli arşiv deposuna
# (R2_ARCHIVE_BUCKET) yazılır ve yalnız backend üzerinden sunulur:
#   • Panel: GET /orders/{id}/manual-invoice-file  (oturum + orders.invoice yetkisi)
#   • Pazaryeri/dış link: GET /orders/{id}/manual-invoice-file/signed?exp=..&sig=..
#     (HMAC imzalı + SÜRELİ; pazaryeri geç çekebildiği için 365 gün, müşteri e-postası için 30 gün)
# İmza anahtarı: INVOICE_LINK_SECRET (≥32 karakter) varsa o; yoksa JWT_SECRET'tan alan-ayrımlı
# HMAC ile türetilir (JWT anahtarı doğrudan imza anahtarı olarak KULLANILMAZ).
_MANUAL_INVOICE_LINK_DAYS_MARKETPLACE = 365
_MANUAL_INVOICE_LINK_DAYS_CUSTOMER = 30


def _derived_invoice_link_key() -> bytes:
    import hmac as _hmac, hashlib as _hl
    from .deps import JWT_SECRET as _sec
    return _hmac.new(str(_sec).encode(), b"meluni:manual-invoice-link:v1", _hl.sha256).digest()


def _manual_invoice_link_key() -> bytes:
    _env = (os.environ.get("INVOICE_LINK_SECRET") or "").strip()
    if len(_env) >= 32:
        return _env.encode()
    return _derived_invoice_link_key()


def _invoice_link_verify_keys() -> list:
    """İmza DOĞRULAMA anahtarları: güncel anahtar + (INVOICE_LINK_SECRET sonradan eklendiyse)
    önceki JWT-türetilmiş anahtar — env eklenmeden önce pazaryerine/müşteriye gitmiş linkler
    süreleri dolana dek açılmaya devam eder. Yeni linkler HER ZAMAN güncel anahtarla imzalanır."""
    keys = [_manual_invoice_link_key()]
    try:
        _d = _derived_invoice_link_key()
        if _d not in keys:
            keys.append(_d)
    except Exception:
        pass
    return keys


def _manual_invoice_sig(order_id: str, exp: int, key: bytes = None) -> str:
    import hmac as _hmac, hashlib as _hl
    return _hmac.new(key or _manual_invoice_link_key(), f"minv:v1:{order_id}:{int(exp)}".encode(),
                     _hl.sha256).hexdigest()[:40]


def _invoice_sig_ok(sig: str, make) -> bool:
    import hmac as _hmac
    return any(_hmac.compare_digest(str(sig), make(k)) for k in _invoice_link_verify_keys())


def _manual_invoice_signed_link(order_id: str, days: int = _MANUAL_INVOICE_LINK_DAYS_MARKETPLACE) -> str:
    base = (os.environ.get("PUBLIC_API_URL") or os.environ.get("BACKEND_URL")
            or "https://api.meluni.com.tr").rstrip("/")
    exp = int(time.time()) + int(days) * 86400
    return (f"{base}/api/orders/{order_id}/manual-invoice-file/signed"
            f"?exp={exp}&sig={_manual_invoice_sig(order_id, exp)}")


def _order_has_archived_manual_invoice(order: dict) -> bool:
    return bool(((order or {}).get("invoice_manual_file") or {}).get("archive_key"))


def _resolve_marketplace_invoice_link(order: dict, dogan_settings: dict, order_id: str,
                                      invoice_type: str = "", web_key: str = "") -> str:
    """Pazaryerine (Trendyol/Hepsiburada) gönderilecek fatura linkini GÜVENLE çöz — HİÇ boş
    kalmasın. Sıra:
      1) web_key / invoice_pdf_url zaten http URL ise onu kullan (e-Arşiv WEB_KEY URL'i).
      2) bare web_key + earsiv_link_template → şablonla tam URL kur (e-Arşiv).
      3) e-fatura + einvoice_link_template → uuid/id ile şablonla.
      4) Son çare: kendi imzalı self-served PDF linki (order'da uuid/dogan_id varsa; e-fatura'da
         Doğan'dan PDF çeker). Böylece 'link üretilemedi' ile yükleme atlanması ORTADAN KALKAR.
    Not: e-Arşiv'de web_key/şablon yoksa çözülemez → dönen boş string config eksiğine işaret eder
    (earsiv_link_template ayarlanmalı)."""
    # KVKK: gizli arşivdeki manuel fatura → her seferinde TAZE, süreli imzalı backend linki
    # (siparişte saklı eski linkin süresi dolmuş olabilir).
    if _order_has_archived_manual_invoice(order) and not web_key:
        return _manual_invoice_signed_link(order_id or order.get("id"), _MANUAL_INVOICE_LINK_DAYS_MARKETPLACE)
    _it = (invoice_type or order.get("invoice_type") or "").strip()
    wk = (web_key or order.get("invoice_pdf_url") or "").strip()
    if wk.startswith("http"):
        return wk
    _earsiv_tmpl = (dogan_settings.get("earsiv_link_template") or "").strip()
    if wk and _earsiv_tmpl:
        return _earsiv_tmpl.replace("{web_key}", wk)
    if _it == "e-fatura":
        _ef_tmpl = (dogan_settings.get("einvoice_link_template") or "").strip()
        if _ef_tmpl:
            _uuid = str(order.get("invoice_uuid") or "")
            _link = (_ef_tmpl.replace("{uuid}", _uuid).replace("{ettn}", _uuid)
                     .replace("{invoice_id}", str(order.get("invoice_dogan_id") or ""))
                     .replace("{invoice_number}", str(order.get("invoice_number") or ""))
                     .replace("{intl_txn_id}", str(order.get("invoice_intl_txn_id") or ""))).strip()
            if _link:
                return _link
    # Son çare: self-served (e-fatura kesin; e-Arşiv'de uuid/id varsa da denenir)
    if order.get("invoice_uuid") or order.get("invoice_dogan_id"):
        return _einvoice_pdf_link(order_id)
    return ""


async def _resolve_hb_package_number(order: dict, hb_client) -> str:
    """HB fatura API'si PAKET numarası ister (sipariş no DEĞİL). Sırayla: order.hepsiburada_package_number
    → OMS detayından items[].packageNumber → order'daki diğer paket alanları. Bulursa order'a kaydeder."""
    import asyncio as _aio
    _pkg = str(order.get("hepsiburada_package_number") or order.get("package_number")
               or order.get("shipment_package_id") or "").strip()
    if _pkg:
        return _pkg
    _raw = str(order.get("hepsiburada_order_number") or order.get("marketplace_order_id")
               or order.get("order_number") or "").strip()
    if not _raw:
        return ""
    try:
        _det = await _aio.to_thread(hb_client.get_order_detail, _raw)
        for _it in ((_det or {}).get("items") or []):
            if _it.get("packageNumber"):
                _pkg = str(_it["packageNumber"]); break
        if _pkg:
            await db.orders.update_one({"id": order.get("id")},
                                       {"$set": {"hepsiburada_package_number": _pkg}})
    except Exception as _pe:
        logger.warning(f"[hb pkg] paket no cozulemedi {order.get('order_number')}: {_pe}")
    return _pkg


async def _resolve_hb_package_numbers(order: dict, hb_client) -> list:
    """ÇOK-PAKETLİ HB siparişleri: HB faturayı PAKET BAŞINA ister. Bir sipariş birden çok kargo
    paketine bölünmüş olabilir → OMS detayındaki items[].packageNumber'ların TÜMÜ (tekilleştirilmiş)
    + order'da saklı paket no. Böylece faturayı her pakete ayrı yükleriz (tek pakete yüklenip
    diğerleri 'yüklenmemiş' kalmaz)."""
    import asyncio as _aio
    pkgs = []
    for _f in ("hepsiburada_package_number", "package_number", "shipment_package_id"):
        _v = str(order.get(_f) or "").strip()
        if _v and _v not in pkgs:
            pkgs.append(_v)
    _raw = str(order.get("hepsiburada_order_number") or order.get("marketplace_order_id")
               or order.get("order_number") or "").strip()
    if _raw:
        try:
            _det = await _aio.to_thread(hb_client.get_order_detail, _raw)
            for _it in ((_det or {}).get("items") or []):
                _pn = str(_it.get("packageNumber") or "").strip()
                if _pn and _pn not in pkgs:
                    pkgs.append(_pn)
        except Exception as _pe:
            logger.warning(f"[hb pkgs] OMS detay okunamadı {order.get('order_number')}: {_pe}")
    return pkgs


async def _hb_upload_invoice_all_packages(order: dict, link: str, hb_client) -> dict:
    """Faturayı siparişin TÜM HB paketlerine yükler. Döner: {ok, packages:[{pkg,ok,error}], any_fail}.
    409/'zaten mevcut' = başarı (idempotent). Hepsi başarılıysa ok=True."""
    import asyncio as _aio
    pkgs = await _resolve_hb_package_numbers(order, hb_client)
    if not pkgs:
        return {"ok": False, "error": "HB paket no çözülemedi", "packages": []}
    out = []
    all_ok = True
    for _pkg in pkgs:
        try:
            await _aio.to_thread(hb_client.send_invoice, _pkg, link)
            out.append({"pkg": _pkg, "ok": True})
        except Exception as e:
            _err = str(e)
            if any(k in _err.lower() for k in ("already", "zaten mevcut", "409")):
                out.append({"pkg": _pkg, "ok": True, "note": "zaten mevcut"})
            else:
                out.append({"pkg": _pkg, "ok": False, "error": _err[:200]})
                all_ok = False
    return {"ok": all_ok, "packages": out}


@router.post("/repush-invoice-links")
async def repush_invoice_links(payload: dict = None, current_user: dict = Depends(require_permission("orders.edit"))):
    """Faturası kesilmiş ama pazaryerine LİNKİ gitmemiş siparişlerin fatura linkini yeniden gönderir.
    (e-Fatura link düzeltmesinden ÖNCE kesilenler için — özellikle 'web_key url degil' hatası alanlar.)
    payload: {order_id?: str, hours?: int=72, platform?: 'trendyol'|'hepsiburada'}
    Belirli bir order_id verilirse yalnız onu; verilmezse son `hours` içindeki uploaded!=true olanları dener."""
    await _guard_switch("restore_mode")
    await _guard_switch("pause_marketplace_sync")   # pazaryerine fatura linki push'u durdurulmuş olabilir
    payload = payload or {}
    oid = (payload.get("order_id") or "").strip()
    hours = int(payload.get("hours") or 72)
    plat = (payload.get("platform") or "trendyol").strip()

    _uploaded_field = "hepsiburada_invoice_uploaded" if plat in ("hepsiburada", "hb") else "trendyol_invoice_uploaded"
    if oid:
        q = {"id": oid}
    else:
        _since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        q = {"platform": ("hepsiburada" if plat in ("hepsiburada", "hb") else plat), "invoice_issued": True,
             _uploaded_field: {"$ne": True},
             "invoice_issued_at": {"$gte": _since}}
    _dogan_settings = await db.settings.find_one({"id": "dogan_edonusum"}, {"_id": 0}) or {}
    orders = await db.orders.find(q, {"_id": 0}).to_list(500)
    results = []
    for order in orders:
        _order_id = order.get("id")
        _itype = order.get("invoice_type")
        # Ortak çözücü: e-Arşiv (http web_key / earsiv şablonu) + e-fatura (self-served) — boş kalmaz.
        _link = _resolve_marketplace_invoice_link(order, _dogan_settings, _order_id, _itype)
        if not _link:
            _le = "link üretilemedi (e-Arşiv: earsiv_link_template ayarlı mı? / uuid-dogan_id boş)"
            await db.orders.update_one({"id": _order_id}, {"$set": {_uploaded_field.replace("uploaded", "error"): _le}})
            results.append({"order": order.get("order_number"), "ok": False, "error": _le})
            continue
        _plat = order.get("platform")
        try:
            if _plat == "trendyol":
                from .integrations import upload_invoice_to_trendyol
                await upload_invoice_to_trendyol(
                    order.get("order_number"),
                    {"invoice_link": _link, "invoice_number": order.get("invoice_number")},
                    current_user,
                )
                await db.orders.update_one({"id": _order_id},
                                           {"$set": {"trendyol_invoice_uploaded": True, "trendyol_invoice_error": ""}})
                results.append({"order": order.get("order_number"), "ok": True, "link": _link})
            elif _plat == "hepsiburada":
                # HB fatura API'si PAKET numarası ister; ortak çözücü order + OMS detayından bulur.
                import asyncio as _aio_hb
                from routes.category_mapping import _get_hb_client
                _hcli, _hcerr = await _get_hb_client()
                if _hcerr:
                    await db.orders.update_one({"id": _order_id}, {"$set": {
                        "hepsiburada_invoice_uploaded": False, "hepsiburada_invoice_error": _hcerr[:500]}})
                    results.append({"order": order.get("order_number"), "ok": False, "error": _hcerr[:200], "link": _link})
                    continue
                _hres = await _hb_upload_invoice_all_packages(order, _link, _hcli)
                if _hres.get("ok"):
                    await db.orders.update_one({"id": _order_id}, {"$set": {
                        "hepsiburada_invoice_uploaded": True, "hepsiburada_invoice_error": ""}})
                    results.append({"order": order.get("order_number"), "ok": True, "link": _link,
                                    "packages": _hres.get("packages")})
                else:
                    _herr = _hres.get("error") or "; ".join(
                        f"{p.get('pkg')}:{p.get('error')}" for p in _hres.get("packages", []) if not p.get("ok"))
                    await db.orders.update_one({"id": _order_id}, {"$set": {
                        "hepsiburada_invoice_uploaded": False, "hepsiburada_invoice_error": str(_herr)[:500]}})
                    results.append({"order": order.get("order_number"), "ok": False,
                                    "error": str(_herr)[:200], "link": _link, "packages": _hres.get("packages")})
                continue
            else:
                results.append({"order": order.get("order_number"), "ok": False, "error": "yalnız trendyol/hepsiburada destekli"})
                continue
        except Exception as e:
            _err = str(getattr(e, "detail", e))
            _errf = "hepsiburada_invoice_error" if _plat == "hepsiburada" else "trendyol_invoice_error"
            _upf = "hepsiburada_invoice_uploaded" if _plat == "hepsiburada" else "trendyol_invoice_uploaded"
            # 409 / "already exist" → pazaryerinde ZATEN VAR = başarı (idempotent).
            if ("already exist" in _err.lower()) or ("zaten mevcut" in _err.lower()) or ("already" in _err.lower()) or ("409" in _err):
                await db.orders.update_one({"id": _order_id}, {"$set": {_upf: True, _errf: ""}})
                results.append({"order": order.get("order_number"), "ok": True,
                                "link": _link, "note": "pazaryerinde zaten mevcut"})
                continue
            await db.orders.update_one({"id": _order_id}, {"$set": {_errf: _err[:500]}})
            results.append({"order": order.get("order_number"), "ok": False, "error": _err[:200], "link": _link})
    ok_n = sum(1 for r in results if r.get("ok"))
    return {"success": True, "total": len(results), "uploaded": ok_n, "results": results}


async def autoheal_hb_invoices(hours: int = None, limit: int = 500) -> dict:
    """KALICI ÇÖZÜM: faturası kesilmiş ama Hepsiburada'ya YÜKLENMEMİŞ siparişleri yeniden gönderir.
    hours=None → TÜM eski yüklenmemiş HB faturaları (zaman filtresi yok — 'eskileri yükle').
    hours=N → yalnız son N saat (periyodik hafif tarama). İdempotent (409/zaten mevcut = başarı).
    current_user gerektirmez (sistem işi). Faturası kesilmiş sayılma koşulu geniş tutuldu:
    invoice_issued=True VEYA invoice_number/invoice_uuid dolu."""
    q = {"platform": "hepsiburada",
         "hepsiburada_invoice_uploaded": {"$ne": True},
         "$or": [{"invoice_issued": True},
                 {"invoice_number": {"$nin": [None, ""]}},
                 {"invoice_uuid": {"$nin": [None, ""]}}]}
    if hours:
        _since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        q["invoice_issued_at"] = {"$gte": _since}
    orders = await db.orders.find(q, {"_id": 0}).sort("created_at", -1).to_list(limit)
    if not orders:
        return {"checked": 0, "uploaded": 0}
    ds = await db.settings.find_one({"id": "dogan_edonusum"}, {"_id": 0}) or {}
    try:
        from routes.category_mapping import _get_hb_client
        _hcli, _hcerr = await _get_hb_client()
    except Exception as e:
        _hcli, _hcerr = None, str(e)
    if _hcerr or not _hcli:
        return {"checked": len(orders), "uploaded": 0, "error": _hcerr or "HB client yok"}
    import asyncio as _aio
    up = 0
    for order in orders:
        _oid = order.get("id")
        _link = _resolve_marketplace_invoice_link(order, ds, _oid, order.get("invoice_type"))
        if not _link:
            await db.orders.update_one({"id": _oid}, {"$set": {
                "hepsiburada_invoice_error": "link üretilemedi (earsiv_link_template / uuid-dogan_id)"}})
            continue
        _res = await _hb_upload_invoice_all_packages(order, _link, _hcli)
        if _res.get("ok"):
            await db.orders.update_one({"id": _oid}, {"$set": {
                "hepsiburada_invoice_uploaded": True, "hepsiburada_invoice_error": ""}})
            up += 1
        else:
            _emsg = _res.get("error") or "; ".join(
                f"{p.get('pkg')}:{p.get('error')}" for p in _res.get("packages", []) if not p.get("ok"))
            await db.orders.update_one({"id": _oid}, {"$set": {
                "hepsiburada_invoice_error": str(_emsg)[:500]}})
    logger.info(f"[hb invoice autoheal] {up}/{len(orders)} HB faturası yüklendi")
    return {"checked": len(orders), "uploaded": up}


@router.get("/diag/stock-movements")
async def stock_diag(q: str = Query(...), key: str = Query(""), days: int = Query(2)):
    """TEŞHİS (geçici, gizli anahtar korumalı): bir ürünün mevcut stoğu + son N gün stok
    hareketleri (tip/delta/kaynak) + stock_synced_at. Stoğun 0'dan nasıl arttığını gösterir."""
    import os as _os, hmac as _hmac, re as _re
    # Teşhis anahtarı YALNIZ env HB_DIAG_KEY'den (gömülü fallback kaldırıldı). Env yoksa uç kapalı.
    _sk = (_os.environ.get("HB_DIAG_KEY") or "").strip()
    if not _sk or not _hmac.compare_digest(str(key or ""), _sk):
        raise HTTPException(status_code=403, detail="forbidden")
    prod = await db.products.find_one(
        {"$or": [{"id": q}, {"stock_code": q}, {"barcode": q},
                 {"name": {"$regex": _re.escape(q), "$options": "i"}},
                 {"variants.barcode": q}]},
        {"_id": 0, "id": 1, "name": 1, "stock": 1, "stock_code": 1, "platform": 1,
         "variants": 1, "stock_synced_at": 1, "updated_at": 1})
    if not prod:
        return {"found": False, "q": q}
    pid = prod["id"]
    _bcs = [str(v.get("barcode") or "").strip() for v in (prod.get("variants") or []) if v.get("barcode")]
    _since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    _mq = {"created_at": {"$gte": _since},
           "$or": [{"items.product_id": pid}] + ([{"items.barcode": {"$in": _bcs}}] if _bcs else [])}
    moves = await db.stock_movements.find(_mq, {"_id": 0}).sort("created_at", 1).to_list(300)
    # Yalnız bu ürüne ait kalemleri süz + özetle
    out_moves = []
    for m in moves:
        _its = [it for it in (m.get("items") or [])
                if it.get("product_id") == pid or str(it.get("barcode") or "") in _bcs]
        if not _its:
            continue
        out_moves.append({
            "created_at": m.get("created_at"), "type": m.get("type"),
            "source": m.get("source"), "order_number": m.get("order_number"),
            "items": [{"barcode": it.get("barcode"), "delta": it.get("delta"),
                       "oversold": it.get("oversold")} for it in _its],
        })
    return {
        "found": True, "product_id": pid, "name": prod.get("name"),
        "stock_code": prod.get("stock_code"), "platform": prod.get("platform"),
        "current_total_stock": prod.get("stock"),
        "variants": [{"size": v.get("size"), "color": v.get("color"),
                      "barcode": v.get("barcode"), "stock": v.get("stock")}
                     for v in (prod.get("variants") or [])],
        "stock_synced_at": prod.get("stock_synced_at"),  # dolu+güncel ise Ticimax senkronu ezmiş olabilir
        "updated_at": prod.get("updated_at"),
        "movements_last_days": days,
        "movements": out_moves,
    }


@router.get("/hepsiburada/invoice-diag")
async def hb_invoice_diag(request: Request, q: str = Query(...), key: str = Query(""),
                          do_upload: bool = Query(False),
                          credentials=Depends(_deps_security)):
    """TEŞHİS + hedefli yükleme (PII döndürmez).
    q: HB sipariş no / paket no / bizim order_number / id.

    GÜVENLİK (denetim 2026-10): statik anahtar (env HB_DIAG_KEY) yalnız SALT-OKUR teşhisi açar.
    do_upload=true (HB'ye fatura GÖNDERİR — dış sisteme yazma) artık statik anahtarla
    ÇALIŞMAZ: geçerli SÜPER-ADMIN oturumu (Bearer JWT) şart + denetim kaydı yazılır.
    Süper-admin oturumu ile anahtar gerekmez."""
    import os as _os, hmac as _hmac
    _admin = None
    if credentials is not None:
        try:
            _admin = await require_admin(credentials)
            if "*" not in await get_effective_permissions(_admin):
                _admin = None
        except HTTPException:
            _admin = None
    if _admin is None:
        if do_upload:
            raise HTTPException(status_code=403, detail="do_upload yalnızca süper-admin oturumuyla kullanılabilir")
        # Teşhis anahtarı YALNIZ env HB_DIAG_KEY'den (gömülü fallback kaldırıldı). Env yoksa uç kapalı.
        _sk = (_os.environ.get("HB_DIAG_KEY") or "").strip()
        if not _sk or not _hmac.compare_digest(str(key or ""), _sk):
            raise HTTPException(status_code=403, detail="forbidden")
        await record_admin_audit(
            db, action="hepsiburada.invoice_diag_static_key", entity_type="order", entity_id=str(q)[:80],
            current_user=None, request=request, source="diag.static_key", metadata={"do_upload": False})
    order = await db.orders.find_one({"$or": [
        {"hepsiburada_order_number": q}, {"order_number": q},
        {"hepsiburada_package_number": q}, {"marketplace_order_id": q},
        {"package_number": q}, {"id": q}]}, {"_id": 0})
    if not order:
        return {"found": False, "q": q, "hint": "Bu HB siparişi sistemde bulunamadı (henüz çekilmemiş olabilir)."}
    res = {
        "found": True, "order_number": order.get("order_number"),
        "platform": order.get("platform"),
        "invoice_issued": order.get("invoice_issued"),
        "invoice_number": order.get("invoice_number"),
        "invoice_type": order.get("invoice_type"),
        "has_web_key_http": str(order.get("invoice_pdf_url") or "").startswith("http"),
        "has_uuid_or_doganid": bool(order.get("invoice_uuid") or order.get("invoice_dogan_id")),
        "hepsiburada_package_number": order.get("hepsiburada_package_number"),
        "hepsiburada_invoice_uploaded": order.get("hepsiburada_invoice_uploaded"),
        "hepsiburada_invoice_error": str(order.get("hepsiburada_invoice_error") or "")[:300],
    }
    if do_upload and _admin is not None and order.get("platform") == "hepsiburada":
        await record_admin_audit(
            db, action="hepsiburada.invoice_diag_upload", entity_type="order", entity_id=order.get("id") or "",
            current_user=_admin, request=request, source="admin.orders",
            metadata={"order_number": order.get("order_number") or ""})
        ds = await db.settings.find_one({"id": "dogan_edonusum"}, {"_id": 0}) or {}
        from routes.category_mapping import _get_hb_client
        _hcli, _hcerr = await _get_hb_client()
        if _hcerr:
            res["upload"] = {"ok": False, "error": _hcerr[:200]}
        else:
            _link = _resolve_marketplace_invoice_link(order, ds, order["id"], order.get("invoice_type"))
            res["resolved_link"] = bool(_link)
            res["all_packages"] = await _resolve_hb_package_numbers(order, _hcli)
            if not _link:
                res["upload"] = {"ok": False, "error": "fatura linki üretilemedi (fatura kesilmemiş / earsiv_link_template / uuid-dogan_id boş)"}
            else:
                _up = await _hb_upload_invoice_all_packages(order, _link, _hcli)
                res["upload"] = _up
                if _up.get("ok"):
                    await db.orders.update_one({"id": order["id"]}, {"$set": {
                        "hepsiburada_invoice_uploaded": True, "hepsiburada_invoice_error": ""}})
    return res


@router.get("/{order_id}/einvoice-pdf-debug")
async def debug_einvoice_pdf(order_id: str, current_user: dict = Depends(require_permission("orders.view"))):
    """TEŞHİS (admin): Doğan e-Fatura PDF çekimini dener; PDF baytını DÖNDÜRMEZ, yalnızca
    başarı/hatayı, kullanılan operasyonu ve WSDL'deki mevcut operasyon adlarını verir.
    Böylece doğru Doğan metodu ağ erişimi olan ortamda netleşir. PII sızdırmaz."""
    order = await db.orders.find_one({"id": order_id}, {"_id": 0})
    if not order:
        raise HTTPException(status_code=404, detail="Sipariş bulunamadı")
    _uuid = (order.get("invoice_uuid") or "").strip()
    _iid = (order.get("invoice_dogan_id") or "").strip()
    ds = await db.settings.find_one({"id": "dogan_edonusum"}, {"_id": 0}) or {}
    if not (ds.get("enabled") and ds.get("username")):
        return {"ok": False, "error": "Doğan yapılandırılmamış"}
    from dogan_client import DoganClient
    from fastapi.concurrency import run_in_threadpool
    _cli = DoganClient(username=ds["username"], password=ds["password"], is_test=ds.get("is_test", True))
    try:
        ops = await run_in_threadpool(_cli._list_efatura_operations)
    except Exception as e:
        ops = [f"(ops list err: {e})"]
    sigs = {}
    for _op in ("GetInvoiceWithType", "GetInvoice", "LoadInvoice", "GetInvoiceResponse"):
        try:
            sigs[_op] = await run_in_threadpool(_cli._efatura_op_signature, _op)
        except Exception as e:
            sigs[_op] = f"(err: {e})"
    res = await run_in_threadpool(_cli.get_efatura_pdf, _uuid, _iid)
    return {
        "ok": bool(res.get("success")),
        "has_uuid": bool(_uuid), "has_invoice_id": bool(_iid),
        "operation_used": res.get("operation"),
        "kind": res.get("kind"),
        "doc_bytes": len(res.get("pdf") or b"") if res.get("success") else 0,
        "error": res.get("error"),
        "structure": res.get("structure"),
        "signatures": sigs,
    }


@router.get("/{order_id}/einvoice-pdf")
async def serve_einvoice_pdf(order_id: str, request: Request, sig: str = "", exp: str = ""):
    """Sipariş e-Faturasının resmî PDF'ini Doğan'dan çekip servis eder (imzalı public link).
    GÜVENLİK: order'a özel HMAC imzası doğrulanır — kimliksiz PII/fatura sızıntısı yok.
    v2 (exp+sig): süreli, alan-ayrımlı anahtar. v1 (yalnız sig): eski süresiz linkler
    pazaryerlerinde saklı olduğundan geriye uyumluluk için KABUL edilir ve kullanımı loglanır."""
    import hmac as _hmac

    async def _deny():
        await note_policy_denial(None, "order.einvoice_link", order_id=order_id, request=request,
                                 reason="bad_signature")
        raise HTTPException(status_code=403, detail="Geçersiz imza")
    if not sig:
        await _deny()
    if exp:
        try:
            _exp_i = int(exp)
        except Exception:
            await _deny()
        if not _invoice_sig_ok(sig, lambda k: _einvoice_pdf_sig_v2(order_id, _exp_i, k)):
            await _deny()
        if _exp_i < int(time.time()):
            raise HTTPException(status_code=410, detail="Bağlantının süresi dolmuş")
    else:
        if not _hmac.compare_digest(str(sig), _einvoice_pdf_sig(order_id)):
            await _deny()
        logger.warning(f"[einvoice-pdf] ESKİ süresiz (v1) imzalı link kullanıldı order={order_id}")
        try:
            await db.integration_logs.insert_one({
                "id": generate_id(), "service": "einvoice_pdf", "event": "legacy_v1_link_used",
                "order_id": order_id, "created_at": datetime.now(timezone.utc).isoformat()})
        except Exception:
            pass
    order = await db.orders.find_one({"id": order_id}, {"_id": 0})
    if not order:
        raise HTTPException(status_code=404, detail="Sipariş bulunamadı")
    _uuid = (order.get("invoice_uuid") or "").strip()
    _iid = (order.get("invoice_dogan_id") or "").strip()
    if not (_uuid or _iid):
        raise HTTPException(status_code=404, detail="Bu siparişte e-Fatura kaydı yok")

    dogan_settings = await db.settings.find_one({"id": "dogan_edonusum"}, {"_id": 0}) or {}
    if not (dogan_settings.get("enabled") and dogan_settings.get("username")):
        raise HTTPException(status_code=400, detail="Doğan e-Dönüşüm yapılandırılmamış")
    try:
        from dogan_client import DoganClient
        from fastapi.concurrency import run_in_threadpool
        _cli = DoganClient(
            username=dogan_settings["username"],
            password=dogan_settings["password"],
            is_test=dogan_settings.get("is_test", True),
        )
        res = await run_in_threadpool(_cli.get_efatura_pdf, _uuid, _iid)
    except Exception as e:
        logger.error(f"[einvoice-pdf {order_id}] {e}")
        raise HTTPException(status_code=502, detail="e-Fatura PDF alınamadı")  # ayrıntı yalnız logda (INFRA-03)
    if not res.get("success") or not res.get("pdf"):
        logger.warning(f"[einvoice-pdf {order_id}] başarısız: {res.get('error')} "
                       f"struct={res.get('structure')}")
        raise HTTPException(status_code=502,
                            detail=f"e-Fatura belgesi alınamadı: {res.get('error')}")
    fname = (order.get("invoice_number") or order.get("order_number") or "fatura")
    kind = res.get("kind") or "pdf"
    if kind == "xml":
        # Resmî imzalı UBL-XML — gömülü XSLT ile tarayıcıda fatura olarak görüntülenir.
        return Response(
            content=res["pdf"],
            media_type="application/xml; charset=utf-8",
            headers={"Content-Disposition": f'inline; filename="{fname}.xml"'},
        )
    return Response(
        content=res["pdf"],
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{fname}.pdf"'},
    )



# ==================== KARGO BARKOD / MNG SHIPMENT ====================

def _normalize_phone(p: str) -> str:
    if not p:
        return ""
    digits = "".join(ch for ch in str(p) if ch.isdigit())
    # Türkiye: 90XXXXXXXXXX (12) → 5XXXXXXXXX (10) format, MNG cep formatı tercih eder
    if digits.startswith("90") and len(digits) == 12:
        return digits[2:]
    if digits.startswith("0") and len(digits) == 11:
        return digits[1:]
    return digits


async def _get_mng_settings() -> dict:
    """MNG Kargo ayarlarını DB'den çeker; kimlik bilgileri (kullanıcı adı/parola)
    GÜVENLİK gereği artık kaynak koda gömülü DEĞİL — DB ayarı yoksa ortam
    değişkeninden (MNG_USERNAME / MNG_PASSWORD) okunur. Şirket kodu/vergi no
    gizli olmadığından sabit kalır."""
    import os as _os
    s = await db.settings.find_one({"id": "mng_kargo"}, {"_id": 0}) or {}

    # DENETİM FIX (#9): "Kargo Firması Ayarları" formu credential'ları providers_config
    # (kind='cargo', active_provider='mng') altına yazıyor; eski _get_mng_settings ise yalnız
    # settings.mng_kargo'yu okuyordu → formdaki bilgiler HİÇ KULLANILMIYORDU. Artık cargo
    # config birincil kaynak: mng aktifse oradaki username/password/customer_number kullanılır
    # (customer_number ↔ customer_code hizalanır). settings.mng_kargo + env yedek kalır.
    _cargo = {}
    try:
        _cc = await db.providers_config.find_one({"kind": "cargo"}, {"_id": 0}) or {}
        try:
            from routes.provider_settings import decrypt_provider_doc as _dpd
            _cc = _dpd("cargo", _cc)   # A1.1: sırları çöz (at-rest şifreli)
        except Exception:
            pass
        if (_cc.get("active_provider") == "mng"):
            _cargo = ((_cc.get("providers") or {}).get("mng") or {})
    except Exception:
        _cargo = {}

    def _dec(v):
        if not v:
            return v
        try:
            from security.crypto import decrypt as _dec_secret
            return _dec_secret(v)
        except Exception:
            return v

    _username = _cargo.get("username") or s.get("username") or _os.environ.get("MNG_USERNAME", "")
    _pw_raw = _cargo.get("password") or s.get("password") or _os.environ.get("MNG_PASSWORD", "")
    _pw = _dec(_pw_raw)
    _cust = (_cargo.get("customer_number") or _cargo.get("customer_code")
             or s.get("customer_code") or "MELUNİ DIŞ TİC.A.Ş.")
    # env: cargo config 'prod' → canlı; 'test' → test. is_active: cargo dolu ya da settings.
    _cargo_active = bool(_cargo.get("username") and _cargo.get("password"))
    return {
        "username": _username,
        "password": _pw,
        "customer_code": _cust,
        "tax_no": _cargo.get("identity_no") or s.get("tax_no") or "6080712084",
        "env": _cargo.get("env") or s.get("env") or "",
        # DÜZELTME (denetim 2026-10): admin MNG'yi AÇIKÇA kapattıysa (is_active=False) kargo
        # bilgisi dolu olsa bile pasif sayılır (eskiden `_cargo_active or ...` toggle'ı yok sayıyordu).
        # Ayar yoksa eski davranış: kargo bilgisi dolu ya da varsayılan True.
        # Yalnız GÜNCEL sağlayıcı ayarındaki (providers_config) açık kapatma dikkate alınır; eski
        # settings.mng_kargo.is_active alanı panelden artık yazılmadığından bayat olabilir → yok sayılır.
        "is_active": (False if _cargo.get("is_active") is False
                      else (_cargo_active or s.get("is_active", True))),
    }


async def _get_sender_info() -> dict:
    """Mağaza/Gönderici + İADE ALICI (şirket) bilgilerini DB'den çeker (settings.id=store_info).
    store_info boşsa MELUNİ şirket adresine düşer → iade gönderisinin alıcı adresi HER ZAMAN dolu
    olur ve MNG/DHL barkodu gerçekten üretilebilir (boş adres yüzünden barkod üretilememe sorunu çözülür)."""
    store = await db.settings.find_one({"id": "store_info"}, {"_id": 0}) or {}
    return {
        "name": store.get("sender_name") or "MELEK GİRAY",
        "phone": _normalize_phone(store.get("sender_phone") or "5396738540"),
        "address": store.get("sender_address") or "Kavaklı Mah. Meclis Sok. No:4/9 Referans Beylikdüzü D Blok D:19",
        "city": store.get("sender_city") or "İstanbul",
        "district": store.get("sender_district") or "Beylikdüzü",
    }


@router.post("/{order_id}/cargo-barcode")
async def create_cargo_barcode(
    order_id: str,
    cargo_company: str = Query("MNG"),
    current_user: dict = Depends(require_permission("orders.ship"))
):
    """Aktif kargo firmasında barkod / takip numarası oluşturur ve sipariş üzerine yazar.
    Şu an MNG Kargo entegrasyonu canlı; diğer firmalar için manuel takip no input'u gerekir.

    COM09: aynı sipariş için etiket isteği ATOMİK kilitle (cargo_label_lock) tek seferde yürür —
    çift tık / toplu + tekli eşzamanlı istek ikinci MNG kaydı açamaz (ikincisi 409). Kilit sürerken
    teslimat adresi de değiştirilemez (update_order). Sipariş kilit ALINDIKTAN sonra okunur →
    etikete yalnız güncel adres gider."""
    await _guard_restore_mode()   # ACİL ANAHTAR: geri yükleme modunda kargo etiketi oluşturulmaz → 423
    if not isinstance(cargo_company, str):   # doğrudan Python çağrısı (Query nesnesi gelmesin)
        cargo_company = "MNG"
    _tok = await _acquire_order_op_lock(order_id, "cargo_label", ttl_s=300)
    if not _tok:
        if not await db.orders.count_documents({"id": order_id}, limit=1):
            raise HTTPException(status_code=404, detail="Sipariş bulunamadı")
        raise HTTPException(status_code=409, detail=(
            "Bu sipariş için kargo etiketi şu an oluşturuluyor. Birkaç saniye sonra sayfayı yenileyin."))
    try:
        return await _create_cargo_barcode_locked(order_id, cargo_company, current_user)
    finally:
        await _release_order_op_lock(order_id, "cargo_label", _tok)


async def _create_cargo_barcode_locked(order_id: str, cargo_company: str, current_user: dict):
    """create_cargo_barcode gövdesi — YALNIZ cargo_label kilidi tutulurken çağrılır."""
    company = (cargo_company or "MNG").upper()
    order = await db.orders.find_one({"id": order_id}, {"_id": 0})
    if not order:
        raise HTTPException(status_code=404, detail="Sipariş bulunamadı")
    _ensure_order_confirmed(order, "kargo barkodu oluşturma")  # #17 (bulk-cargo-barcode de bu ucu çağırır → o da kapsanır)

    # Havale/EFT siparişi ödeme onaylanmadan kargo barkodu OLUŞTURULMAZ (fatura ile aynı kural).
    _bc_pm = (order.get("payment_method") or "").lower()
    _bc_ps = (order.get("payment_status") or "").lower()
    if _bc_pm in _HAVALE_PMS and _bc_ps not in _SETTLED_PAY:
        raise HTTPException(status_code=400, detail=(
            "Havale onaylanmadığı için bu siparişe kargo barkodu oluşturulamadı. "
            "Önce siparişin ödemesini 'Ödendi' olarak işaretleyin (havale onayı), sonra barkod alın."
        ))

    # Eğer mevcut barkod/takip no varsa direkt dön — yoksa tekrar MNG'ye gidip
    # "duplicate siparis_no" hatası alınır (502 → "barkod oluşmuyor" görünümü).
    # cargo_barcode_number = bizim Self Barkodumuz (her zaman dolu); cargo_tracking_number = gerçek takip no (boş olabilir).
    _existing_tn = (order.get("cargo_tracking_number") or order.get("cargo_barcode_number")
                    or (order.get("cargo") or {}).get("mng_siparis_no")
                    or (order.get("cargo") or {}).get("tracking_number"))
    if _existing_tn:
        return {
            "success": True,
            "tracking_number": _existing_tn,
            "cargo_provider_name": order.get("cargo_provider_name") or company,
            "message": "Sipariş zaten kargo barkoduna sahip",
        }

    if company != "MNG":
        # Diğer firmalar için sahte/placeholder takip no üret (henüz canlı entegrasyon yok)
        import random
        tracking = f"{company}-{int(time.time())}{random.randint(100,999)}"
        await db.orders.update_one(
            {"id": order_id},
            {"$set": {
                "cargo_tracking_number": tracking,
                "cargo_provider_name": company,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }}
        )
        return {
            "success": True,
            "tracking_number": tracking,
            "cargo_provider_name": company,
            "message": f"{company} için manuel takip no atandı (canlı API entegrasyonu yok)",
        }

    # ===== MNG KARGO CANLI =====
    import sys, os
    sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
    from mng_kargo_client import create_shipment as mng_create

    settings = await _get_mng_settings()
    if not settings["is_active"]:
        raise HTTPException(status_code=400, detail="MNG Kargo entegrasyonu pasif. Lütfen Ayarlar > Kargo bölümünden aktif edin.")

    # Sipariş üzerinden teslim adresini al
    ship = order.get("shipping_address") or {}
    full_name = (
        f"{ship.get('first_name','')} {ship.get('last_name','')}".strip()
        or ship.get("name")
        or "Alıcı"
    )
    phone = _normalize_phone(ship.get("phone"))
    if not phone:
        raise HTTPException(status_code=400, detail="Alıcı telefonu eksik. MNG barkodu oluşturulamaz.")
    il = (ship.get("city") or "").strip()
    ilce = (ship.get("district") or "").strip()
    adres = (ship.get("address") or "").strip()
    # DENETİM A-2: ilçe de zorunlu — MNG pChIlce boşsa gönderiyi reddeder/yanlış şubeye
    # yönlendirir (influencer yolu zaten dördünü şart koşuyor, sipariş yolu asimetrikti).
    if not (il and ilce and adres):
        raise HTTPException(status_code=400, detail="Alıcı il, ilçe ve adresi eksik. MNG barkodu oluşturulamaz.")

    # İçerik: ürün isimleri. DENETİM A-1: MNG pChIcerik gerçek limiti 200 karakter (E022);
    # çok ürünlü siparişte 200-250 arası düşerse kargo reddedilir → "barkod oluşmuyor".
    items = order.get("items") or []
    icerik = "; ".join([f"{it.get('quantity',1)}x {it.get('product_name','')}".strip() for it in items])[:200] or "Ürün"
    kiymet = float(order.get("total") or order.get("subtotal") or 0)

    # Sipariş numarası (MNG için unique olmalı, varsa siparişin order_number'ı)
    siparis_no = str(order.get("order_number") or order.get("id") or order_id)

    # Kapıda ödeme?
    payment_method = (order.get("payment_method") or "").lower()
    kapida = 1 if payment_method in ("cash_on_delivery", "kapida") else 0
    odeme_sekli = "U" if kapida else "P"  # P=Peşin (Gönderici Öder), U=Ücretli (Alıcı Öder)

    # Senkron SOAP çağrısı event loop'u BLOKE etmesin diye thread'e taşı (aksi halde
    # MNG WSDL yavaşlığı/erişilemezliği Railway worker'ını kilitleyip 503'e yol açar).
    import asyncio as _aio_cargo

    # FIN-E06 — YEREL KAYIT KAYBI KURTARMA: önceki bir denemede MNG'ye istek GİTTİ (attempt damgası
    # var) ama sonuç siparişe yazılamadıysa (timeout / süreç çöktü), YENİ kayıt açmadan önce MNG'de
    # bu sipariş_no ile kayıt aranır; bulunursa ona BAĞLANILIR (çift gönderi/çift ücret yok).
    # Damga da yoksa (commit tamamen kayıp) MNG zaten aynı sipariş_no'yu E005 ile reddeder →
    # aşağıdaki E005 dalı aynı aramayla kurtarır.
    res = None
    if order.get("cargo_label_attempted_at"):
        try:
            from mng_kargo_client import get_mng_shipment_status as _gss_pre
            _pre = await _aio_cargo.to_thread(
                _gss_pre, username=settings["username"], password=settings["password"], siparis_no=siparis_no)
            if _pre.get("ok") and (str(_pre.get("mng_siparis_no") or "").strip()
                                   or str(_pre.get("gonderi_no") or "").strip()):
                res = {"ok": True,
                       "barkod": str(_pre.get("mng_siparis_no") or "").strip() or siparis_no,
                       "gonderi_no": str(_pre.get("gonderi_no") or "").strip(),
                       "raw": "recovered_from_lookup"}
                logger.info(f"[FIN-E06] MNG'de mevcut kayıt bulundu, bağlanıyor (siparis_no={siparis_no})")
        except Exception as _pre_e:
            logger.warning(f"[FIN-E06] ön sorgu başarısız (siparis_no={siparis_no}): {_pre_e}")
    if res is None:
        # MNG'ye istek GİTMEDEN önce deneme damgası (yanıt kaybolursa sonraki deneme önce sorgular).
        await db.orders.update_one({"id": order_id}, {"$set": {
            "cargo_label_attempted_at": datetime.now(timezone.utc).isoformat()}})
    try:
        res = res or await _aio_cargo.to_thread(
            mng_create,
            username=settings["username"],
            password=settings["password"],
            siparis_no=siparis_no,
            irsaliye_no=str(order.get("invoice_number") or "")[:20],
            kiymet=kiymet,
            icerik=icerik,
            hizmet_sekli="NORMAL",  # NORMAL | ONCELIKLI | GUNICI | AKSAM_TESLIMAT
            teslim_sekli=1,
            al_sms=0,
            gn_sms=1 if phone else 0,
            # MNG format: "Kg:Desi:En:Boy:Yukseklik:;" (her paket ; ile ayrılır)
            # Varsayılan: 1 paket, 1kg, 1 desi, 20x30x15cm
            parca_list="1:1:20:30:15:;",
            alici_ad=full_name,
            odeme_sekli=odeme_sekli,
            adres_farkli="0",
            il=il,
            ilce=ilce,
            adres=adres,
            tel_cep=phone,
            # KVKK/PRIV09 (denetim 2026-10): müşteri e-postası MNG'ye GÖNDERİLMEZ — pChEmail
            # MNG SiparisGirisiDetayliV3'te opsiyoneldir (mng_kargo_client varsayılanı "");
            # teslimat bildirimi için telefon (tel_cep) yeterli. İade gönderileri zaten "" yolluyor.
            email="",
            kapida_odeme=kapida,
            platform_adi="",  # Pazaryeri değil — boş geç (N11/GG/TRND aksi takdirde)
            platform_kodu="",
        )
    except Exception as _mng_exc:
        # MNG bağlantı/SOAP exception'ı: 503 (Railway) yerine okunabilir 502 + gerçek sebep döndür.
        logger.error(f"MNG create_shipment exception (order={order_id}): {_mng_exc}")
        try:
            await db.cargo_logs.insert_one({
                "id": generate_id(), "order_id": order_id, "provider": "MNG",
                "action": "create_shipment", "status": "exception",
                "request_summary": {"siparis_no": siparis_no, "il": il, "ilce": ilce},
                "error": str(_mng_exc)[:1000],
                "created_at": datetime.now(timezone.utc).isoformat(),
            })
        except Exception:
            pass
        raise HTTPException(status_code=502, detail=f"MNG Kargo bağlantı hatası: {str(_mng_exc)[:300]}")

    if not res.get("ok"):
        _hata = str(res.get("hata") or "")
        # E005: MNG'de bu sipariş_no'ya ait kayıt ZATEN VAR (önceki denemede oluştu ama
        # yanıt bize dönmeden timeout/503 olduğu için DB'ye yazılamadı). Bu bir HATA değil —
        # mevcut kaydı FaturaSiparisListesi'nden çekip kurtarıyoruz (yeni kayıt oluşturmadan).
        _recovered = False
        if ("ZATEN VAR" in _hata.upper()) or ("E005" in _hata.upper()):
            # Kayıt MNG'de KESİN var. Gerçek MNG gönderi no'yu çekmeyi dene (FaturaSiparisListesi);
            # ama günlük sorgu limiti ("GUNLUK SORGULAMA SINIRI") veya boş dönse BİLE sipariş_no'yu
            # barkod kabul edip kurtar — etiket barkodu zaten sipariş no'yu kodluyor, gerçek gönderi no
            # sonradan scheduler/backfill ile dolar. Amaç: etiket HEMEN oluşsun.
            _existing_barkod = ""
            _existing_gonderi = ""
            try:
                from mng_kargo_client import get_mng_shipment_status as _gss_recover
                _rec = await _aio_cargo.to_thread(
                    _gss_recover,
                    username=settings["username"], password=settings["password"], siparis_no=siparis_no
                )
                if _rec.get("ok"):
                    _existing_barkod = (_rec.get("mng_siparis_no") or "").strip()
                    _existing_gonderi = (_rec.get("gonderi_no") or "").strip()
                else:
                    logger.warning(f"E005 FaturaSiparisListesi başarısız (siparis_no={siparis_no}): {_rec.get('error')}")
            except Exception as _re:
                logger.warning(f"E005 kurtarma sorgusu hata (siparis_no={siparis_no}): {_re}")
            res = {
                "ok": True,
                "barkod": _existing_barkod or siparis_no,   # gerçek MNG no varsa o; yoksa sipariş no (etiket için yeterli)
                "gonderi_no": _existing_gonderi,
                "raw": "recovered_from_E005",
            }
            _recovered = True
            logger.info(f"E005 kurtarma (siparis_no={siparis_no}): barkod={res['barkod']}, gonderi={_existing_gonderi or '(sonra dolacak)'}")

        if not _recovered:
            # MNG hatasını cargo_logs'a yaz
            await db.cargo_logs.insert_one({
                "id": generate_id(),
                "order_id": order_id,
                "provider": "MNG",
                "action": "create_shipment",
                "status": "error",
                "request_summary": {"siparis_no": siparis_no, "alici_ad": full_name, "il": il, "ilce": ilce},
                "error": res.get("hata"),
                "raw": str(res.get("raw"))[:1000] if res.get("raw") else None,
                "created_at": datetime.now(timezone.utc).isoformat(),
            })
            raise HTTPException(status_code=502, detail=f"MNG Kargo hatası: {res.get('hata') or 'Bilinmeyen hata'}")

    barkod = res["barkod"]  # MNG_SIPARIS_NO (MNG Self Barkod) — gerçek kargo takip kodu
    
    # MNGGonderiBarkod denemesi → NZ-formatlı kargo barkodu (kurumsal hesaplarda anında dolar).
    # Yetki hatası alırsa graceful fallback: MNG_SIPARIS_NO kullanılır.
    nz_barkod = ""
    nz_gonderi_no = ""
    try:
        from mng_kargo_client import get_mng_barcode_immediately
        kapida = (order.get("payment_method") or "").lower() in ("cash_on_delivery", "kapida")
        nz_res = await _aio_cargo.to_thread(
            get_mng_barcode_immediately,
            username=settings["username"], password=settings["password"],
            siparis_no=siparis_no,
            irsaliye_no=str(order.get("invoice_number") or "")[:20],
            urun_bedeli=kiymet,
            kapida_tahsilat=kapida,
        )
        if nz_res.get("ok"):
            nz_barkod = nz_res.get("barkod", "")
            nz_gonderi_no = nz_res.get("gonderi_no", "")
            logger.info(f"MNG NZ barkod alındı: {nz_barkod} (gonderi_no={nz_gonderi_no})")
        else:
            logger.info(f"MNGGonderiBarkod denenemedi/başarısız (graceful fallback): {nz_res.get('hata')}")
    except Exception as nz_err:
        logger.warning(f"MNGGonderiBarkod exception (fallback to siparis_no): {nz_err}")

    # FaturaSiparisListesi'nden ek kargo durumu çek (şube, kargo statu, varsa GONDERI_NO)
    from mng_kargo_client import get_mng_shipment_status
    try:
        status_info = await _aio_cargo.to_thread(
            get_mng_shipment_status,
            username=settings["username"], password=settings["password"], siparis_no=siparis_no
        )
    except Exception as _st_exc:
        logger.warning(f"get_mng_shipment_status exception (graceful fallback): {_st_exc}")
        status_info = {"ok": False}
    gonderi_no_status = (status_info.get("gonderi_no") or "") if status_info.get("ok") else ""
    kargo_takip_url = (status_info.get("kargo_takip_url") or "") if status_info.get("ok") else ""
    kargo_statu = (status_info.get("kargo_statu") or "0") if status_info.get("ok") else "0"
    kargo_statu_aciklama = (status_info.get("kargo_statu_aciklama") or "") if status_info.get("ok") else ""

    # Self Barkod hesapları için: MNG_SIPARIS_NO zaten gerçek kargo takip kodudur
    # NZ-formatlı havuz tahsis edilen kurumsal hesaplarda GONDERI_NO field'ında ayrı bir kod gelir
    # Öncelik: NZ (anında MNGGonderiBarkod) → GONDERI_NO (FaturaSiparisListesi sonradan dolu) → MNG_SIPARIS_NO
    # Gerçek kargo takip no SADECE kargo firması gönderi numarası üretince dolar (NZ barkod / NZ gönderi / GONDERI_NO).
    # MNG Self Barkod (barkod = MNG_SIPARIS_NO) = bizim oluşturduğumuz barkod → kargoya verildiği/kargoda olduğu
    # anlamına GELMEZ, bu yüzden takip no olarak YAZILMAZ. Gerçek no scheduler/backfill ile sonradan yakalanır.
    real_tracking = nz_barkod or nz_gonderi_no or gonderi_no_status or ""
    public_tracking = real_tracking or barkod  # bildirim metni / geriye dönük fallback
    # MNG->DHL devri: kargotakip.dhlecommerce.com.tr/?takipNo={no} dogru deep-link (no ile direkt takip,
    # form/CAPTCHA gerektirmez). Gerçek no yoksa manuel takip sayfasina dusulur.
    track_link = (f"https://kargotakip.dhlecommerce.com.tr/?takipNo={real_tracking}" if real_tracking else "https://www.dhlecommerce.com.tr/gonderitakip")
    update_doc = {
        "cargo_tracking_number": real_tracking,    # SADECE gerçek takip no; yoksa boş (scheduler/backfill doldurur)
        "cargo_barcode_number": barkod,            # MNG Self Barkod — bizim barkodumuz (takip no DEĞİL)
        "cargo_tracking_link": track_link,
        "cargo_provider_name": "MNG Kargo",
        "cargo_provider_code": "MNG",
        "cargo": {
            "provider": "MNG",
            "provider_name": "MNG Kargo",
            "tracking_number": public_tracking,   # etiket/yazdırma butonu için DOLU (gerçek no yoksa barkod). Gerçek takip no order.cargo_tracking_number'da; kargo sütunu barkodu gerçek saymaz (ctn==barcodeNo).
            "tracking_link": track_link,
            "label_format": "10x15cm",
            "mng_siparis_no": barkod,                      # MNG Self Barkod (her zaman dolu)
            "mng_gonderi_no": gonderi_no_status,           # NZ formatlı (FaturaSiparisListesi sonrası)
            "mng_nz_barkod": nz_barkod,                    # NZ formatlı (MNGGonderiBarkod anında, varsa)
            "mng_nz_gonderi_no": nz_gonderi_no,
            "mng_kargo_statu": kargo_statu,
            "mng_kargo_statu_aciklama": kargo_statu_aciklama,
            "cikis_subesi": status_info.get("cikis_subesi"),
            "teslim_subesi": status_info.get("teslim_subesi"),
            "created_at": datetime.now(timezone.utc).isoformat(),
        },
        "cargo_barcode_created": True,
        # BARKOD OLUSTURMA = siparis HAZIRLANIYOR. "Kargoya Verildi" (shipped) DEGIL —
        # o, kargocu/DHL gonderiyi fiilen okuttugunda scheduler (_dhl_cargo_poll_tick) ile
        # gercek takip kodu yakalaninca olur. Burada erken "shipped" YAPMA.
        "status": "preparing" if order.get("status") in ("pending", "confirmed", "processing") else order.get("status"),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    await db.orders.update_one({"id": order_id}, {"$set": update_doc})

    # Bildirim: "Hazirlaniyor" (Ayarlar > Siparis Durumlari'nda acildiysa). "Kargoya Verildi"
    # bildirimi barkod olusturmada GONDERILMEZ — gercek DHL takip kodu yakalaninca scheduler gonderir.
    try:
        if update_doc.get("status") == "preparing":
            from order_statuses import get_status_config
            _cfg = await get_status_config(db)
            _nz = (_cfg.get("notify") or {}).get("preparing") or {}
            _ch = [c for c in ("sms", "email") if _nz.get(c)]
            if _ch:
                from notification_service import send_notification
                ship_addr = order.get("shipping_address") or {}
                await send_notification(
                    db,
                    event="order_preparing",
                    to_phone=ship_addr.get("phone") or order.get("customer_phone"),
                    to_email=ship_addr.get("email") or order.get("customer_email") or order.get("user_email"),
                    variables=await _order_notify_vars(order, order_number=order.get("order_number") or order_id, cargo_provider="MNG Kargo"),
                    channels=_ch,
                )
    except Exception as ne:
        logger.warning(f"Hazirlaniyor bildirimi gönderilemedi (order={order_id}): {ne}")
    await db.cargo_logs.insert_one({
        "id": generate_id(),
        "order_id": order_id,
        "provider": "MNG",
        "action": "create_shipment",
        "status": "success",
        "tracking_number": barkod,
        "request_summary": {"siparis_no": siparis_no, "alici_ad": full_name, "il": il, "ilce": ilce},
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    return {
        "success": True,
        "tracking_number": public_tracking,
        "tracking_link": track_link,
        "mng_siparis_no": barkod,
        "mng_gonderi_no": gonderi_no_status,
        "mng_kargo_statu": kargo_statu,
        "cargo_provider_name": "MNG Kargo",
        "message": f"✅ MNG kargo barkodu oluşturuldu: {public_tracking}",
    }


@router.post("/{order_id}/cargo-refresh")
async def refresh_cargo_tracking(order_id: str, current_user: dict = Depends(require_permission("orders.ship"))):
    """Kargo takip durumunu yeniler — anlaşılır mesajla.

    - Pazaryeri (Trendyol/Hepsiburada) siparişi → takip no pazaryeri senkronundan gelir,
      MNG'de SORGULANMAZ (yanlış 'bulunamadı' hatası önlenir).
    - Site siparişi → MNG FaturaSiparisListesi'nden gönderi no/durum çekilir.
    Hiçbir durumda kuru 'yenilenemedi' dönmez: MNG'de kayıt yok / gönderi no henüz atanmadı /
    yetki-IP whitelist hatası ayrı ayrı, ne yapılacağını söyleyen mesajla döner.
    """
    import sys, os
    sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
    from mng_kargo_client import get_mng_shipment_status

    order = await db.orders.find_one({"id": order_id}, {"_id": 0})
    if not order:
        raise HTTPException(status_code=404, detail="Sipariş bulunamadı")

    cargo = order.get("cargo") or {}
    siparis_no = str(order.get("order_number") or order_id)

    # ── Pazaryeri siparişi: MNG'de aranmaz (takip no pazaryerinden gelir) ──
    _platform = str(order.get("platform") or order.get("marketplace") or "").lower()
    _is_marketplace = (_platform in ("trendyol", "hepsiburada")
                       or (order.get("payment_method") == "marketplace"))
    if _is_marketplace:
        _tn = order.get("cargo_tracking_number") or cargo.get("tracking_number") or ""
        _label = ("Trendyol" if "trendyol" in _platform else
                  "Hepsiburada" if "hepsi" in _platform else "Pazaryeri")
        return {
            "success": True,
            "tracking_number": _tn,
            "message": (
                f"{_label} siparişi — kargo takip no pazaryeri senkronuyla güncellenir, MNG sorgusu yapılmaz. "
                + (f"Mevcut takip: {_tn}" if _tn else "Takip no pazaryeri tarafından atandığında senkronla gelecek.")
            ),
        }

    # ── Site siparişi: MNG'den durum çek (hata yutulmaz, kategorize edilir) ──
    settings = await _get_mng_settings()
    try:
        import asyncio as _aio_mng
        info = await _aio_mng.to_thread(  # SOAP (≤40 sn) → olay döngüsünü kilitlemesin
            get_mng_shipment_status,
            username=settings["username"], password=settings["password"], siparis_no=siparis_no
        )
    except Exception as _e:
        logger.error(f"cargo-refresh MNG exception {siparis_no}: {_e}")
        info = {"ok": False, "error": str(_e)}

    if not info.get("ok"):
        _err = str(info.get("error") or "").lower()
        # MNG'de kayıt yok → genelde MNG kargo barkodu hiç oluşturulmamış (site siparişi elle "Kargoya Verildi")
        if (not _err) or any(t in _err for t in ("bulunamad", "kayıt", "kayit", "not found", "no record")):
            return {
                "success": False,
                "tracking_number": order.get("cargo_tracking_number") or "",
                "message": (
                    f"Bu sipariş ({siparis_no}) MNG'de bulunamadı. Site siparişi MNG ile gönderilmediyse "
                    "önce 📦 (MNG kargo barkodu) butonuyla oluşturun; barkod oluşunca takip no burada görünür."
                ),
            }
        # Yetki / IP whitelist / bağlantı → gerçek sistem hatası (kırmızı uyarı)
        raise HTTPException(
            status_code=502,
            detail=(f"MNG durumu alınamadı: {info.get('error')}. "
                    "Yetki/IP whitelist veya bağlantı sorunu olabilir — MNG paneli > API IP izinlerini kontrol edin."),
        )

    gonderi_no = info.get("gonderi_no") or ""   # GERÇEK kargo takip no (boş olabilir)
    mng_siparis_no = info.get("mng_siparis_no") or cargo.get("mng_siparis_no") or ""
    # ÖNEMLİ: iç sipariş no'yu (mng_siparis_no) ASLA takip no diye yazma. Sadece gerçek gönderi no.
    track_link = (f"https://kargotakip.dhlecommerce.com.tr/?takipNo={gonderi_no}"
                  if gonderi_no else "https://www.dhlecommerce.com.tr/gonderitakip")

    # Durum alanlarını her zaman güncelle; takip no'yu yalnız GERÇEK gönderi no doluysa yaz.
    update = {
        "cargo.mng_siparis_no": mng_siparis_no,
        "cargo.mng_gonderi_no": gonderi_no,
        "cargo.mng_kargo_statu": info.get("kargo_statu"),
        "cargo.mng_kargo_statu_aciklama": info.get("kargo_statu_aciklama"),
        "cargo.cikis_subesi": info.get("cikis_subesi"),
        "cargo.teslim_subesi": info.get("teslim_subesi"),
        "cargo.teslim_tarihi": info.get("teslim_tarihi"),
        "cargo_status_text": info.get("kargo_statu_aciklama") or "",
        "cargo_query_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    if gonderi_no:
        update["cargo_gonderi_no"] = gonderi_no
        update["cargo_tracking_number"] = gonderi_no
        update["cargo_tracking_link"] = track_link
        update["cargo.tracking_number"] = gonderi_no
        update["cargo.tracking_link"] = track_link
    else:
        # Gerçek no yok → daha önce YANLIŞLIKLA yazılmış (iç no = takip no) değeri TEMİZLE,
        # böylece sipariş listesinde yanlış numara yerine "takip bekleniyor" görünür.
        _wrong = (order.get("cargo_tracking_number") or "")
        if _wrong and _wrong == mng_siparis_no:
            update["cargo_tracking_number"] = ""
            update["cargo_gonderi_no"] = ""
            update["cargo.tracking_number"] = ""
    await db.orders.update_one({"id": order_id}, {"$set": update})

    if gonderi_no:
        _msg = f"📦 Gönderi No: {gonderi_no}" + (f" · {info.get('kargo_statu_aciklama')}" if info.get("kargo_statu_aciklama") else "")
    else:
        # Gerçek gönderi no yok = kargo henüz teslim alıp işleme almamış.
        _aci = (info.get("kargo_statu_aciklama") or "").strip()
        _msg = ("Bu sipariş henüz kargoya verilmedi / kargo firması gönderi no atamadı"
                + (f" ({_aci})" if _aci else "")
                + ". Kargo şubede işleme alınınca gönderi no OTOMATİK çekilecek.")
    return {
        "success": True,
        "tracking_number": gonderi_no,   # gerçek no yoksa boş
        "mng_siparis_no": mng_siparis_no,
        "mng_gonderi_no": gonderi_no,
        "kargo_statu": info.get("kargo_statu"),
        "kargo_statu_aciklama": info.get("kargo_statu_aciklama"),
        "tracking_link": track_link,
        "message": _msg,
    }


@router.post("/cargo/backfill-tracking")
async def backfill_cargo_tracking(
    since: str = "",
    force: bool = False,
    all_statuses: bool = False,
    site_only: bool = False,
    current_user: dict = Depends(require_permission("settings.edit")),
):
    """Kargoya verilmiş siparişlerin MNG (DHL eCommerce) takip numaralarını TOPLU çeker.

    - since boş → TÜM ZAMANLAR (tarih sınırı yok). Tarih verilirse (YYYY-MM-DD) o tarihten sonrası.
    - force=False → yalnız takip no'su EKSİK olanları sorgular (verimli); force=True → hepsini yeniden.
    - all_statuses=True → durum filtresi uygulamaz (varsayılan: yalnız kargo durumundaki siparişler).
    - site_only=True → pazaryeri (TY/HB önekli) siparişleri ATLAR; yalnız site siparişlerini sorgular
      (MNG/DHL'de zaten pazaryeri siparişi yok → boşuna sorgu/yanlış 'bulunamadı' önlenir).
    - get_mng_shipment_status (SALT OKUMA) → yeni kargo OLUŞTURMAZ, bildirim göndermez.
    - MNG sorgusu thread'de (event loop bloklanmaz); sıralı → MNG'yi hammer'lamaz.

    ÖNEMLİ: Gerçek takip no'yu MNG 'FaturaSiparisListesi' üretir — MNG kargoyu okutunca dolar
    ve bu sorgu MNG tarafında IP whitelist ister. DHL'in bir 'günlük sorgu limiti' YOKTUR.
    Dönüşteki 'diagnosis' her siparişin GERÇEK nedenini sayar (atanmadı / yetki / hata).
    """
    import asyncio
    from mng_kargo_client import get_mng_shipment_status

    settings = await _get_mng_settings()
    q = {}
    if since:
        q["created_at"] = {"$gte": since}
    if not all_statuses:
        q["status"] = {"$in": [
            "shipped", "in_transit", "out_for_delivery", "delivered",
            "return_requested", "return_approved", "return_in_transit",
            "returned", "undelivered",
        ]}
    cur = db.orders.find(
        q,
        {"_id": 0, "id": 1, "order_number": 1, "cargo_tracking_number": 1, "cargo": 1, "status": 1},
    ).sort("created_at", -1)
    orders = await cur.to_list(length=20000)

    updated, skipped, failed = [], [], []
    # Gerçek neden sayaçları — kullanıcı "limit mi?" diye soruyor; net görünsün.
    diagnosis = {
        "guncellendi": 0,
        "zaten_takip_no_vardi": 0,
        "mngde_kayit_var_takip_no_yok": 0,  # MNG kaydı oluşmuş, GONDERI_NO atanmamış (kargo henüz okutulmadı)
        "mngde_kayit_yok_veya_yetki": 0,    # hiç satır dönmedi → bulunamadı VEYA IP whitelist/yetki eksik
        "sorgu_hatasi": 0,                  # exception / SOAP hatası
        "gunluk_limit": 0,                  # MNG/DHL günlük sorgu limiti mesajı
    }
    limit_hit = False
    site_taranan = 0
    for o in orders:
        on = str(o.get("order_number") or "")
        _is_site = bool(on and (on.startswith("W") or on.startswith("IW")))
        if site_only and not _is_site:
            continue  # SADECE site siparişleri (W / IW); TY/HB ve diğerleri atlanır
        if _is_site:
            site_taranan += 1
        existing = o.get("cargo_tracking_number") or (o.get("cargo") or {}).get("tracking_number")
        _internal_no = (o.get("cargo") or {}).get("mng_siparis_no") or ""
        # Daha önce YANLIŞLIKLA iç sipariş no'su takip no diye yazılmışsa, "zaten var" sayma —
        # yeniden sorgula ki ya gerçek no ile düzelt ya da temizle.
        _existing_wrong = bool(existing) and existing == _internal_no
        if existing and not _existing_wrong and not force:
            skipped.append(on)
            diagnosis["zaten_takip_no_vardi"] += 1
            continue
        siparis_no = on or str(o.get("id"))
        try:
            info = await asyncio.to_thread(
                get_mng_shipment_status,
                username=settings["username"], password=settings["password"], siparis_no=siparis_no,
            )
        except Exception as e:
            failed.append({"no": siparis_no, "reason": "sorgu_hatasi", "err": str(e)[:160]})
            diagnosis["sorgu_hatasi"] += 1
            continue
        if not info.get("ok"):
            _err_txt = (info.get("error") or "")
            if any(k in _err_txt.lower() for k in ("sorgulama sınır", "sorgulama sinir",
                                                   "günlük sorgu", "gunluk sorgu", "sorgu limit",
                                                   "limit aşıl", "limit asil")):
                diagnosis["gunluk_limit"] += 1
                limit_hit = True
                failed.append({"no": siparis_no, "reason": "gunluk_limit", "err": _err_txt[:160]})
                break  # limiti uzatmamak için taramayı durdur
            failed.append({"no": siparis_no, "reason": "sorgu_hatasi", "err": _err_txt[:160]})
            diagnosis["sorgu_hatasi"] += 1
            continue
        gonderi = (info.get("gonderi_no") or "").strip()  # SADECE gerçek GONDERI_NO; iç no'ya düşme
        if not gonderi:
            has_record = bool((info.get("mng_siparis_no") or "").strip())
            reason = "mngde_kayit_var_takip_no_yok" if has_record else "mngde_kayit_yok_veya_yetki"
            diagnosis[reason] += 1
            # Yanlış (iç no) yazılmış takip no varsa TEMİZLE → listede "takip bekleniyor" görünsün.
            if _existing_wrong:
                await db.orders.update_one(
                    {"id": o["id"]},
                    {"$set": {"cargo_tracking_number": "", "cargo_gonderi_no": "",
                              "cargo.tracking_number": "",
                              "cargo_status_text": info.get("kargo_statu_aciklama") or "",
                              "updated_at": datetime.now(timezone.utc).isoformat()}},
                )
            failed.append({
                "no": siparis_no, "reason": reason,
                "kargo_statu": info.get("kargo_statu"),
                "kargo_statu_aciklama": info.get("kargo_statu_aciklama"),
                "mng_siparis_no": info.get("mng_siparis_no"),
            })
            continue
        await db.orders.update_one(
            {"id": o["id"]},
            {"$set": {
                "cargo_tracking_number": gonderi,
                "cargo_tracking_link": f"https://kargotakip.dhlecommerce.com.tr/?takipNo={gonderi}",
                "cargo.tracking_number": gonderi,
                "cargo.tracking_link": f"https://kargotakip.dhlecommerce.com.tr/?takipNo={gonderi}",
                "cargo.mng_gonderi_no": gonderi,
                "cargo.mng_kargo_statu": info.get("kargo_statu"),
                "cargo.mng_kargo_statu_aciklama": info.get("kargo_statu_aciklama"),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }},
        )
        updated.append({"no": siparis_no, "gonderi_no": gonderi})
        diagnosis["guncellendi"] += 1

    # Günlük limite takıldıysak scheduler poll de bugün geri çekilsin diye bayrağı kaydet.
    if limit_hit:
        try:
            await db.settings.update_one(
                {"id": "mng_kargo"},
                {"$set": {"daily_limit_hit_at": datetime.now(timezone.utc).isoformat()}},
                upsert=True,
            )
        except Exception:
            pass

    return {
        "since": since or "(tüm zamanlar)", "force": force, "all_statuses": all_statuses,
        "site_only": site_only, "limit_hit": limit_hit,
        "scanned": len(orders), "site_taranan": site_taranan,
        "updated": len(updated), "skipped": len(skipped), "failed": len(failed),
        "diagnosis": diagnosis,
        "aciklama": (
            ("⚠️ MNG/DHL GÜNLÜK SORGU LİMİTİNE takıldı — tarama durduruldu, bugün için yeterli sorgu yapıldı; "
             "yarın otomatik devam eder. Bu limit DHL/MNG tarafındadır, bizim kodumuzda limit yoktur. "
             if limit_hit else
             "Bu limit (varsa) DHL/MNG tarafındadır; bizim kodumuzda günlük limit yoktur. ")
            + "Takip no MNG 'FaturaSiparisListesi'nden gelir; MNG kargoyu okutunca dolar (IP whitelist gerektirir). "
            "'mngde_kayit_var_takip_no_yok' = MNG kaydı oluşmuş ama numara henüz atanmamış. "
            "'mngde_kayit_yok_veya_yetki' = MNG'den satır dönmedi (bulunamadı VEYA whitelist/yetki eksik)."
        ),
        "updatedList": updated[:200], "failedList": failed[:80],
    }


@router.post("/returns/bulk-mark-refunded-silent")
async def bulk_mark_refunded_silent(payload: dict, request: Request,
                                    current_user: dict = Depends(require_permission("returns.refund_pay"))):
    """Verilen sipariş ID'lerini 'İade Bedeli Ödendi' (refunded) durumuna alır.

    BİLDİRİMSİZ: db.orders doğrudan güncellenir, send_notification çağrılmaz →
    bu siparişler için müşteriye SMS/e-posta GİTMEZ. (Tek seferlik toplu işlem.)

    GÜVENLİK (denetim 2026-10): para/uyum işlemi → `returns.refund_pay` YETKİSİ **VE**
    süper-admin şart; işlemi yapan kayda geçer + denetim kaydı yazılır. Eski
    `all_approved` toplu modu (tüm onaylı iadeleri tek tıkla 'ödendi' yapma) KAPATILDI —
    yalnız açıkça listelenen sipariş id'leri işlenir; yalnız `return_approved` durumundaki
    siparişler güncellenir (başka durumdaki bir sipariş yanlışlıkla 'refunded' olmaz).
    """
    await _guard_switch("freeze_refunds")   # ACİL ANAHTAR: iadeler donduruldu → 423
    _perms = await get_effective_permissions(current_user)
    if "*" not in _perms:
        raise HTTPException(status_code=403, detail="Bu işlem yalnızca süper-admin yetkisi gerektirir")
    payload = payload if isinstance(payload, dict) else {}
    if payload.get("all_approved"):
        raise HTTPException(status_code=400, detail=(
            "Toplu 'tüm onaylı iadeler' modu kapatıldı. İşlenecek sipariş id'lerini order_ids "
            "listesinde açıkça gönderin."))
    order_ids = payload.get("order_ids") or []
    if not isinstance(order_ids, list) or not order_ids:
        raise HTTPException(status_code=400, detail="order_ids (liste) gerekli")
    order_ids = [str(x).strip()[:80] for x in order_ids if str(x or "").strip()]
    order_ids = list(dict.fromkeys(order_ids))
    if not order_ids or len(order_ids) > 500:
        raise HTTPException(status_code=400, detail="order_ids 1-500 arası benzersiz id içermeli")
    now = datetime.now(timezone.utc).isoformat()
    _actor = current_user.get("email", "") or current_user.get("id", "")
    done, missing, skipped = [], [], []
    # COM11 / FIN-E03: AÇIK ödeme itirazlı siparişler toplu "iade ödendi" yapılmaz (atlanır, listede
    # döner). Süper admin gerekçe (chargeback_override_reason) verirse her biri ayrı denetimle geçer.
    from .chargebacks import assert_refund_allowed, CHARGEBACK_OVERRIDE_FIELD
    _cb_open = set(await db.chargebacks.distinct("order_id", {"order_id": {"$in": order_ids}, "status": "open"}))
    _cb_blocked, _cb_overridden = [], set()
    if _cb_open:
        if str(payload.get(CHARGEBACK_OVERRIDE_FIELD) or "").strip():
            for _oid in sorted(_cb_open):
                await assert_refund_allowed(_oid, current_user, payload,
                                            action="returns.bulk_mark_refunded_silent", request=request)
                _cb_overridden.add(_oid)
        else:
            _cb_blocked = sorted(_cb_open)

    async def _apply():
        for oid in order_ids:
            if oid in _cb_blocked:
                continue
            _flt_cb = {} if oid in _cb_overridden else {"chargeback_open": {"$ne": True}}
            res = await db.orders.update_one(
                {"id": oid, "status": "return_approved", **_flt_cb},
                {"$set": {
                    "status": "refunded",
                    "refund_paid_at": now,
                    "return_approved_at": now,
                    "updated_at": now,
                    "refund_silent_bulk": True,
                    "refund_silent_bulk_by": _actor,
                }},
            )
            if res.matched_count:
                done.append(oid)
            elif await db.orders.count_documents({"id": oid}, limit=1):
                skipped.append(oid)  # var ama return_approved değil → dokunulmadı
            else:
                missing.append(oid)

    # PARA/UYUM İŞLEMİ → FAIL-CLOSED denetim: intent yazılamazsa hiçbir sipariş güncellenmez (503).
    from activity_audit import run_audited
    await run_audited(
        db, action="returns.bulk_mark_refunded_silent.apply", entity_type="order", entity_id="bulk",
        perform=_apply, current_user=current_user, request=request, source="admin.orders",
        metadata={"requested": len(order_ids), "order_ids": order_ids[:200]})
    await record_admin_audit(
        db, action="returns.bulk_mark_refunded_silent", entity_type="order", entity_id="bulk",
        current_user=current_user, request=request, source="admin.orders",
        metadata={"requested": len(order_ids), "updated_ids": done[:200],
                  "skipped_ids": skipped[:200], "missing_ids": missing[:200]})
    return {"requested": len(order_ids), "updated": len(done), "updatedIds": done,
            "missingIds": missing, "skippedNotApprovedIds": skipped,
            "blockedChargebackIds": _cb_blocked}


@router.post("/cargo/relink-dhl")
async def relink_cargo_dhl(current_user: dict = Depends(require_permission("orders.ship"))):
    """Eski/yanlis kargo takip linklerini DHL deep-link formatina cevirir:
    kargotakip.dhlecommerce.com.tr/?takipNo={no} (no ile direkt takip, form/CAPTCHA yok).
    Sadece MNG/DHL linki olan siparisleri etkiler (Trendyol/HB kendi linkine dokunulmaz).
    No bos olan kayitlarda manuel takip sayfasina dusulur. Bildirimsiz; MNG API cagirmaz."""
    now = datetime.now(timezone.utc).isoformat()
    DEEP = "https://kargotakip.dhlecommerce.com.tr/?takipNo="
    FALLBACK = "https://www.dhlecommerce.com.tr/gonderitakip"
    rx = {"$regex": "mngkargo|dhlecommerce|gonderitakip|BarkodNo", "$options": "i"}
    q = {"cargo_tracking_link": rx}
    matched = await db.orders.count_documents(q)
    link_expr = {
        "$cond": [
            {"$gt": [{"$strLenCP": {"$ifNull": ["$cargo_tracking_number", ""]}}, 0]},
            {"$concat": [DEEP, "$cargo_tracking_number"]},
            FALLBACK,
        ]
    }
    r = await db.orders.update_many(q, [{"$set": {
        "cargo_tracking_link": link_expr,
        "cargo.tracking_link": link_expr,
        "updated_at": now,
    }}])
    return {"matched": matched, "modified": r.modified_count}


@router.post("/{order_id}/create-mng-shipment")
async def create_mng_shipment(order_id: str, current_user: dict = Depends(require_permission("orders.ship"))):
    """MNG Kargo'ya sipariş gönder ve barkod al (kısayol)."""
    return await create_cargo_barcode(order_id=order_id, cargo_company="MNG", current_user=current_user)


@router.get("/cargo/mng-test")
async def mng_connection_test(siparis_no: str = None, current_user: dict = Depends(require_permission("settings.view"))):
    """TEŞHİS: MNG/DHL SOAP servisine (service.mngkargo.com.tr) sunucudan erişim testi.
    Barkod OLUŞTURMAZ — sadece Baglanti_Test() ping atar. 200 + JSON döner (CORS güvenli).
    ok=true → erişim var; ok=false → error alanında gerçek sebep (timeout/whitelist/DNS).
    """
    import asyncio as _aio_t, time as _t
    from mng_kargo_client import baglanti_test
    t0 = _t.time()
    try:
        res = await _aio_t.to_thread(baglanti_test)
        out = {"ok": bool(res.get("ok")), "result": str(res.get("result"))[:300],
               "error": str(res.get("error"))[:500] if res.get("error") else None,
               "ms": int((_t.time() - t0) * 1000)}
    except Exception as e:
        out = {"ok": False, "error": str(e)[:500], "ms": int((_t.time() - t0) * 1000)}
    # MNG ayar özeti (şifre maskeli) — credential dolu mu kontrolü
    try:
        s = await _get_mng_settings()
        out["settings"] = {
            "username": (s.get("username") or "")[:4] + "***" if s.get("username") else "(boş)",
            "has_password": bool(s.get("password")),
            "customer_code": s.get("customer_code") or "(boş)",
            "enabled": s.get("enabled", True),
        }
    except Exception as se:
        out["settings_error"] = str(se)[:200]
    # siparis_no verilirse FaturaSiparisListesi'nden mevcut kaydı da test et (E005 kurtarma kaynağı)
    if siparis_no:
        try:
            from mng_kargo_client import get_mng_shipment_status as _gss_t
            _s2 = await _get_mng_settings()
            _ss = await _aio_t.to_thread(
                _gss_t, username=_s2["username"], password=_s2["password"], siparis_no=siparis_no
            )
            # Ham yanıtı okunur bir önizlemeye indir (JSON şişmesin, panelde görünür).
            if isinstance(_ss, dict) and "raw" in _ss:
                _ss["raw_preview"] = str(_ss.pop("raw"))[:2000]
            out["shipment_status"] = _ss
        except Exception as _e2:
            out["shipment_status_error"] = str(_e2)[:300]
    return out


@router.get("/cargo/logs")
async def get_cargo_logs_diag(limit: int = 10, current_user: dict = Depends(require_permission("orders.view"))):
    """TEŞHİS: Son kargo işlem logları (create_shipment hataları/exception'ları dahil).
    create-mng-shipment 503/502 verdiğinde gerçek MNG hatası buraya yazılır."""
    try:
        logs = await db.cargo_logs.find({}, {"_id": 0}).sort("created_at", -1).to_list(
            length=min(int(limit or 10), 50))
        return {"ok": True, "count": len(logs), "logs": logs}
    except Exception as e:
        return {"ok": False, "error": str(e)[:300]}


@router.post("/bulk-cargo-barcode")
async def bulk_create_cargo_barcode(
    order_ids: List[str],
    cargo_company: str = Query("MNG"),
    current_user: dict = Depends(require_permission("orders.ship"))
):
    """Birden çok sipariş için topluca kargo barkodu oluştur.

    NOT: Path BİLEREK tek segment ('/bulk-cargo-barcode'). Daha önce '/bulk/cargo-barcode'
    idi ve '/{order_id}/cargo-barcode' route'u daha önce tanımlı olduğu için FastAPI bu
    isteği order_id='bulk' sanıp tekil route'a yönlendiriyor, toplu işlem ÇALIŞMIYORDU.
    """
    await _guard_restore_mode()   # ACİL ANAHTAR: geri yükleme modu → 423
    # DENETİM (cost-redteam #8): tek istekte MNG çağrı sayısını sınırla (burst-DoS/maliyet).
    if order_ids and len(order_ids) > 200:
        raise HTTPException(status_code=400, detail="Tek seferde en fazla 200 sipariş işlenebilir.")
    success = []
    errors = []
    for oid in order_ids or []:
        try:
            r = await create_cargo_barcode(order_id=oid, cargo_company=cargo_company, current_user=current_user)
            success.append({"order_id": oid, "tracking_number": r.get("tracking_number")})
        except HTTPException as he:
            errors.append({"order_id": oid, "error": he.detail})
        except Exception as e:
            errors.append({"order_id": oid, "error": str(e)})
    return {
        "success": True,
        "success_count": len(success),
        "error_count": len(errors),
        "successes": success,
        "errors": errors,
    }


@router.post("/bulk-status")
async def bulk_update_status(
    order_ids: List[str],
    status: str = Query(...),
    # GÜVENLİK (denetim 2026-10): tekli uç require_permission("orders.update_status") ister;
    # toplu uç yalnız require_admin'deydi → yetkisiz personel toplu durum değiştirebiliyordu
    # (BFLA). Aynı yetkiye bağlandı. Finans kapıları (ödenmiş iptal / cancel_refunded)
    # update_order_status İÇİNDE uygulandığından her sipariş için aynen geçerli.
    current_user: dict = Depends(require_permission("orders.update_status"))
):
    """Birden çok siparişin durumunu TOPLUCA güncelle.

    NOT: Path BİLEREK tek segment ('/bulk-status'). Frontend eskiden '/orders/bulk/status'
    POST ediyordu; '/{order_id}/status' PUT route'u bu path'i yutup 405 döndürüyor, "Toplu
    Durum Güncelle" HİÇ çalışmıyordu (bulk-cargo-barcode ile aynı sorun/çözüm).
    """
    if len(order_ids or []) > 500:
        raise HTTPException(status_code=400, detail="Tek seferde en fazla 500 sipariş güncellenebilir")
    success, errors = [], []
    for oid in order_ids or []:
        try:
            await update_order_status(order_id=oid, status=status, expected_status=None,
                                      current_user=current_user)
            success.append(oid)
        except HTTPException as he:
            errors.append({"order_id": oid, "error": he.detail})
        except Exception as e:
            errors.append({"order_id": oid, "error": str(e)})
    return {"success": True, "success_count": len(success),
            "error_count": len(errors), "errors": errors}


@router.post("/bulk-create-invoice")
async def bulk_create_invoice(
    order_ids: List[str],
    invoice_type: str = Query("auto"),
    # DENETİM H1: tekli fatura kesimi require_permission("orders.invoice") ister;
    # toplu uç yalnız require_admin'deydi → izinsiz personel toplu GİB faturası
    # kesebiliyordu (BFLA). Tekli uçla aynı yetkiye bağlandı.
    current_user: dict = Depends(require_permission("orders.invoice"))
):
    """Toplu fatura kesimi — invoice_type=auto ile her sipariş için VKN/TC kontrolü
    yapılır, mükellefse e-Fatura, değilse e-Arşiv kesilir.
    """
    await _guard_restore_mode()   # ACİL ANAHTAR: geri yükleme modu → 423
    success = []
    errors = []
    for oid in order_ids or []:
        try:
            # Daha önce kesilmişse atla
            o = await db.orders.find_one({"id": oid}, {"_id": 0, "invoice_issued": 1, "order_number": 1})
            if not o:
                errors.append({"order_id": oid, "error": "Sipariş bulunamadı"})
                continue
            if o.get("invoice_issued"):
                errors.append({"order_id": oid, "error": "Fatura zaten kesilmiş", "skipped": True})
                continue
            r = await create_invoice_for_order(
                order_id=oid, invoice_type=invoice_type, current_user=current_user
            )
            success.append({
                "order_id": oid,
                "invoice_number": r.get("invoice_number"),
                "invoice_type": r.get("invoice_type"),
                "invoice_pdf_url": r.get("invoice_pdf_url"),
                "trendyol_upload": r.get("trendyol_upload"),
            })
        except HTTPException as he:
            errors.append({"order_id": oid, "error": str(he.detail)})
        except Exception as e:
            errors.append({"order_id": oid, "error": str(e)})
    return {
        "success": True,
        "success_count": len(success),
        "error_count": len(errors),
        "successes": success,
        "errors": errors,
    }


# ═══════════════════ MNG KARGO WEBHOOK ═══════════════════════════════
@router.post("/cargo/mng-webhook")
async def mng_cargo_webhook(payload: dict, request: Request):
    """MNG Kargo'dan gelen kargo durum güncelleme webhook'u.

    MNG Kargo, gönderi durumu değiştikçe önceden tanımlanmış URL'e bu yapıda
    POST atar:
      {"BARKOD": "NZ123", "ISLEM_KODU": "300", "ISLEM_ADI": "Dağıtımda",
       "TARIH": "2026-05-06T12:30:00", "REFERANS_NO": "FC123ABCD"}

    İşlem kodları (MNG standardı):
      - 100: Şubeye girdi
      - 200: Transfere alındı
      - 300: Dağıtıma çıktı
      - 400: Teslim edildi
      - 500: İade

    GÜVENLİK (Y7): Bu uç önceden KİMLİKSİZDİ — sipariş numarasını bilen biri herhangi
    bir siparişi 'teslim edildi' (iade penceresi açar) ya da 'iade' işaretleyebiliyordu.
    Artık paylaşılan bir gizli anahtar zorunludur: X-Webhook-Secret başlığı veya ?key=
    parametresi, MNG'ye tanımlı URL'deki gizli anahtarla eşleşmeli. Anahtar hem env
    (MNG_WEBHOOK_SECRET) hem de settings(mng_kargo).webhook_secret üzerinden okunur."""
    import hmac as _hmac
    import os as _os_mng
    _secret = (_os_mng.environ.get("MNG_WEBHOOK_SECRET") or "").strip()
    if not _secret:
        _mset = await db.settings.find_one({"id": "mng_kargo"}, {"_id": 0, "webhook_secret": 1}) or {}
        _secret = (_mset.get("webhook_secret") or "").strip()
    # A2.7: Gizli anahtar YALNIZ başlık veya URL query'den okunur. Gövdedeki 'secret' alanı
    # kaldırıldı (gövde entegrasyon loglarında saklandığından sır sızıntısı riskiydi). MNG
    # yalnız callback URL yapılandırmasına izin verdiğinden ?key= fallback'i korunur (TLS altında).
    _provided = (request.headers.get("x-webhook-secret")
                 or request.query_params.get("key") or "").strip()
    if not _secret or not _provided or not _hmac.compare_digest(_provided, _secret):
        logger.warning("MNG webhook reddedildi: gizli anahtar eksik/yanlış")
        raise HTTPException(status_code=401, detail="Yetkisiz webhook")

    barkod = (payload.get("BARKOD") or payload.get("barcode") or "").strip()
    islem_kodu = str(payload.get("ISLEM_KODU") or payload.get("status_code") or "")
    islem_adi = (payload.get("ISLEM_ADI") or payload.get("status_text") or "").strip()
    tarih = (payload.get("TARIH") or payload.get("event_time") or
             datetime.now(timezone.utc).isoformat())
    referans_no = (payload.get("REFERANS_NO") or payload.get("reference_no") or "").strip()

    if not barkod and not referans_no:
        raise HTTPException(status_code=400, detail="BARKOD veya REFERANS_NO zorunlu")

    # Siparişi barkod veya referans_no ile bul
    query_or = []
    if barkod:
        query_or.extend([
            {"cargo_tracking_number": barkod},
            {"cargo.mng_nz_barkod": barkod},
            {"cargo.mng_gonderi_no": barkod},
            {"cargo.mng_siparis_no": barkod},
        ])
    if referans_no:
        query_or.append({"order_number": referans_no})

    # DÜZELTME (denetim 2026-10): `status` projeksiyonda YOKTU → aşağıdaki geri-geçiş koruması
    # hep boş durumla çalışıyor, iptal/iade siparişi 'delivered'a çekilebiliyordu.
    order = await db.orders.find_one({"$or": query_or},
                                     {"_id": 0, "id": 1, "order_number": 1, "status": 1, "cargo_status_history": 1})
    if order:
        # A2.7: İDEMPOTENSİ — aynı (barkod, işlem kodu, tarih) olayı tekrar gelirse (MNG retry /
        # replay) yeniden işleme; teslim/iade gibi yan etkiler ikinci kez tetiklenmesin.
        for _h in (order.get("cargo_status_history") or []):
            if (str(_h.get("code") or "") == islem_kodu
                    and str(_h.get("at") or "") == str(tarih)
                    and str(_h.get("barkod") or "") == barkod):
                return {"success": True, "matched": True, "duplicate": True,
                        "order_id": order["id"], "order_number": order.get("order_number")}
    if not order:
        # Sessizce 200 dön — MNG retry yapmasın, sadece logla
        await db.integration_logs.insert_one({
            "id": generate_id(),
            "service": "mng_kargo",
            "event": "webhook_unknown_order",
            "barkod": barkod,
            "referans_no": referans_no,
            "payload": _pii_scrub(payload),  # KVKK: olası alıcı TCKN/VKN alanları maskeli
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
        return {"success": True, "matched": False}

    # Sipariş durumu mapping. DENETİM (bizlogic Y2/F6): "500" (kargo iadesi = teslim edilemeyip
    # geri dönen paket) MÜŞTERİ İADESİ DEĞİL → order.status'u 'returned' YAPMA (stok/return kaydı
    # olmadan sahte iade + ciro düşümü olurdu); yalnız cargo alanlarına yansır.
    status_map = {
        "100": ("preparing", "Şubeye Girdi"),
        "200": ("shipped", "Transfere Alındı"),
        "300": ("shipped", "Dağıtımda"),
        "400": ("delivered", "Teslim Edildi"),
        "500": (None, "Kargo İadesi (şubede)"),
    }
    new_status, _ = status_map.get(islem_kodu, (None, None))

    update_set = {
        "cargo_last_status_code": islem_kodu,
        "cargo_last_status_text": islem_adi,
        "cargo_last_event_at": tarih,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    # DENETİM Y2: terminal/geri-geçiş koruması — geç gelen kargo event'i iptal/iade/teslim
    # edilmiş siparişi EZMESİN, delivered'ı shipped'e geri DÜŞÜRMESİN.
    # DENETİM 2026-10: iptal/iade edilmiş siparişler kargo olayıyla HİÇBİR yöne taşınmaz
    # (400 'teslim edildi' dahil — eskiden iptal siparişi 'delivered' yapabiliyordu).
    # Teslim edilmiş sipariş geri düşmez; aktif akışta yalnız İLERİ geçilir. (Ödeme durumuna
    # hiç dokunulmaz — değişmez 1.)
    _cur_st = str(order.get("status") or "")
    _FROZEN = {"cancelled", "cancel_refunded", "refunded", "partial_refunded", "returned",
               "return_requested", "return_approved", "return_rejected", "return_in_transit",
               "delivered", "completed"}
    _RANK = {"preparing": 1, "shipped": 2, "delivered": 3}
    if new_status:
        _allow = _cur_st not in _FROZEN
        if _allow and _RANK.get(new_status, 0) <= _RANK.get(_cur_st, 0):
            _allow = False
        if _allow:
            update_set["status"] = new_status
            if new_status == "delivered":
                update_set["delivered_at"] = tarih

    history_entry = {
        "code": islem_kodu, "text": islem_adi, "at": tarih,
        "barkod": barkod, "raw": payload,
    }

    # Statü değişecekse filtre MEVCUT statüyü de içerir (okuma ile yazma arasında panelden
    # iptal edilen sipariş ezilmesin). Eşleşmezse statüsüz yalnız kargo alanları yazılır.
    _filt = {"id": order["id"]}
    if "status" in update_set:
        _filt["status"] = order.get("status")
    _res = await db.orders.update_one(
        _filt,
        {
            "$set": update_set,
            "$push": {"cargo_status_history": history_entry},
        },
    )
    if "status" in update_set and not _res.matched_count:
        update_set.pop("status", None)
        update_set.pop("delivered_at", None)
        new_status = None
        await db.orders.update_one(
            {"id": order["id"]},
            {"$set": update_set, "$push": {"cargo_status_history": history_entry}},
        )
    elif "status" not in update_set:
        new_status = None

    # Log'a yaz
    await db.integration_logs.insert_one({
        "id": generate_id(),
        "service": "mng_kargo",
        "event": "webhook_update",
        "order_id": order["id"],
        "order_number": order.get("order_number"),
        "barkod": barkod,
        "code": islem_kodu,
        "text": islem_adi,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })

    return {
        "success": True,
        "matched": True,
        "order_id": order["id"],
        "order_number": order.get("order_number"),
        "new_status": new_status,
    }


def _render_label_barcode_png_b64(code: str, *, max_width_mm: float = 88.0,
                                  height_mm: float = 16.0) -> str:
    """Kargo etiketi barkodunu SUNUCUDA Code128 PNG olarak üretir (base64).

    NEDEN: Etiket eskiden barkodu Google Fonts'tan gelen 'Libre Barcode 39' WEB FONTU ile
    çiziyordu. İki kırılma noktası vardı ve kurye terminali "bir okuyor bir okumuyor" oluyordu:
      1) Font yazdırma anında indirilemezse çubuk YOK — düz yazı basılıyordu.
      2) Code39 seyrek bir simgeleştirme; JS etikete sığdırmak için punto düşürüyordu. 6 karakterli
         sipariş no (W11676) genişte kalırken 11 karakterli influencer referansı (INF07426343)
         ~18pt'ye iniyor, çubuk kalınlığı (X boyutu) lazer okuyucunun altına düşüyordu.
    ÇÖZÜM: Code128 (aynı veride Code39'un ~yarısı genişlik) + sunucuda PNG. Modül genişliği
    etiket genişliğine göre hesaplanır; asla okunamayacak kadar incelmez (taban 0.33mm ≈ 13 mil).
    """
    code = (str(code or "").strip() or "-")[:40]
    try:
        import io as _io, base64 as _b64
        import barcode as _bc
        from barcode.writer import ImageWriter as _IW
        # Code128 modül sayısı: başlangıç(11) + veri(11/karakter) + kontrol(11) + bitiş(13)
        modules = 11 + 11 * len(code) + 11 + 13
        quiet = 10  # her iki yanda 10 modül sessiz alan (standart minimum)
        mw = (max_width_mm / (modules + 2 * quiet))
        mw = max(0.33, min(0.60, round(mw, 3)))   # 0.33mm taban → tarayıcı toleransı içinde
        buf = _io.BytesIO()
        _bc.get("code128", code, writer=_IW()).write(
            buf, options={"module_width": mw, "module_height": height_mm,
                          "quiet_zone": quiet * mw, "write_text": False,
                          "dpi": 300, "background": "white", "foreground": "black"})
        return _b64.b64encode(buf.getvalue()).decode("ascii")
    except Exception as e:
        logger.warning(f"[kargo etiketi] Code128 PNG üretilemedi ({code}): {e}")
        return ""


def _render_cargo_label_html(*, siparis_no, main_barcode, sender_company, sender_phone,
                             sender_addr_line, receiver_name, receiver_phone, receiver_full_addr,
                             cargo_company_display, odeme_turu, kargo_tipi, tracking_line=""):
    """100x120mm yazdırılabilir kargo etiketi (HTML + Code39). Hem SİPARİŞ hem INFLUENCER PR
    gönderileri AYNI şablonu kullanır (tek kaynak). Telefon formatı, HTML-escape (stored-XSS) ve
    logo gömme içeride yapılır → çağıran ham değer geçer."""
    import base64, pathlib, html as _html_c
    def _hc(v):
        return _html_c.escape(str(v if v is not None else ""))
    def _fmt_phone(p):
        if not p:
            return ""
        digits = "".join(c for c in str(p) if c.isdigit())
        if digits.startswith("90") and len(digits) == 12:
            digits = digits[2:]
        elif digits.startswith("0") and len(digits) == 11:
            digits = digits[1:]
        if len(digits) == 10:
            return f"{digits[0:3]} {digits[3:6]} {digits[6:8]} {digits[8:10]}"
        return str(p)
    logo_b64 = ""
    try:
        logo_path = pathlib.Path(__file__).parent.parent / "static" / "brand" / "meluni-logo.png"
        if logo_path.exists():
            logo_b64 = base64.b64encode(logo_path.read_bytes()).decode()
    except Exception:
        logo_b64 = ""
    logo_src = f"data:image/png;base64,{logo_b64}" if logo_b64 else ""
    sender_company = _hc(sender_company)
    sender_phone = _hc(_fmt_phone(sender_phone))
    sender_addr_line = _hc(sender_addr_line)
    receiver_name = _hc(receiver_name)
    receiver_phone_fmt = _hc(_fmt_phone(receiver_phone))
    receiver_full_addr = _hc(receiver_full_addr)
    cargo_company_display = _hc(cargo_company_display)
    odeme_turu = _hc(odeme_turu)
    kargo_tipi = _hc(kargo_tipi)
    siparis_no = _hc(siparis_no)
    _bc_png = _render_label_barcode_png_b64(main_barcode)
    main_barcode = _hc(main_barcode)
    tracking_line = _hc(tracking_line)
    # PNG üretilebildiyse GÖRSEL barkod (okunaklılık garantisi); üretilemezse eski web-font
    # gösterimi yedek olarak kalır (etiket hiçbir koşulda barkodsuz basılmasın).
    if _bc_png:
        _barcode_el = (f'<img class="barcode-img" alt="{main_barcode}" '
                       f'src="data:image/png;base64,{_bc_png}">')
    else:
        _barcode_el = f'<div class="barcode">*{main_barcode}*</div>'
    return f"""<!DOCTYPE html>
<html lang="tr"><head><meta charset="UTF-8"><title>Kargo Etiketi - {siparis_no}</title>
<link href="https://fonts.googleapis.com/css2?family=Mulish:wght@400;500;600;700;800;900&family=Libre+Barcode+39+Extended&display=swap" rel="stylesheet">
<style>
  @page {{ size: 100mm 120mm; margin: 0; }}
  * {{ box-sizing: border-box; -webkit-print-color-adjust: exact; print-color-adjust: exact; }}
  body {{ margin: 0; font-family: 'Mulish','Helvetica Neue',Arial,sans-serif; width: 100mm; height: 120mm; color: #000; background:#fff; }}
  .label {{ width: 100mm; height: 120mm; padding: 2mm; }}
  /* TAŞMA KORUMASI: etiket sabit 120mm. Adres uzayınca içerik büyüyüp barkod bandını
     kutunun DIŞINA itiyordu (sipariş no alt kenardan kesiliyordu). Artık .main taşmayı
     kırpar, bilgi tabloları ESNER (.body-wrap flex:1, min-height:0), barkod bandı ise
     ASLA küçülmez/itilmez (flex:0 0 auto) → barkod + sipariş no her koşulda tam basılır. */
  .main {{ width: 100%; height: 100%; border: 1.8pt solid #000; border-radius: 1.5mm; padding: 2.2mm;
           display:flex; flex-direction:column; overflow: hidden; }}
  .body-wrap {{ flex: 0 1 auto; min-height: 0; overflow: hidden; }}
  .logo-band {{ display:flex; align-items:center; justify-content:center; padding: 0.3mm 0 1.4mm 0; border-bottom: 1.4pt solid #000; flex: 0 0 auto; }}
  .logo-band img {{ height: 6.2mm; width: auto; max-width: 78mm; }}
  .logo-fallback {{ font-family:'Mulish',sans-serif; font-weight: 900; font-size: 17pt; letter-spacing: 4.5pt; }}
  .section-title {{ text-align: center; font-family:'Mulish',sans-serif; font-weight: 800; font-size: 8.6pt; letter-spacing: 0.5pt; padding: 0.9mm 0; border-bottom: 1pt solid #000; background: #f0f0f0; }}
  .info-table {{ border-bottom: 1pt solid #000; }}
  .info-row {{ display:flex; border-bottom: 0.6pt solid #000; min-height: 4.6mm; }}
  .info-row:last-child {{ border-bottom: 0; }}
  .info-row .lbl {{ width: 22mm; padding: 0.8mm 1.5mm; font-family:'Mulish',sans-serif; font-weight: 700; font-size: 7.6pt; border-right: 0.6pt solid #000; display:flex; align-items:center; }}
  .info-row .val {{ flex: 1; padding: 0.8mm 1.5mm; font-family:'Mulish',sans-serif; font-weight: 700; font-size: 8.2pt; display:flex; align-items:center; line-height: 1.22; word-break: break-word; }}
  .info-row .val.addr {{ font-weight: 600; font-size: 7.4pt; line-height: 1.25; }}
  /* flex: 1 0 auto → artan boşluğu BAND alır ve barkodu ortalar (tablo ile alt kenar
     arasında tek yanlı kocaman boşluk kalmaz); shrink 0 olduğu için asla ezilmez. */
  .barcode-band {{ flex: 1 0 auto; padding: 1.2mm 0 0.4mm; text-align:center;
                   display:flex; flex-direction:column; align-items:center; justify-content:center; }}
  .barcode-band > * {{ width: 100%; }}
  .barcode {{ font-family: 'Libre Barcode 39 Extended', 'Libre Barcode 39', monospace; font-size: 30pt; letter-spacing: 0; line-height: 0.95; color:#000; white-space: nowrap; display: block; }}
  /* Sunucuda üretilen Code128 PNG — web fontuna/JS küçültmeye bağımlı DEĞİL (kurye okuyucusu
     için sabit çubuk kalınlığı). image-rendering: pixelated → yazdırmada çubuklar bulanmaz. */
  .barcode-img {{ display:block; margin: 0 auto; max-width: 88mm; width: auto; height: 16mm;
                  image-rendering: pixelated; image-rendering: crisp-edges; }}
  .barcode-num {{ font-size: 9pt; letter-spacing: 1.4pt; font-family: 'Courier New', monospace; font-weight: 800;
                  line-height: 1.05; margin-top: 0.6mm; }}
</style></head><body>
<div class="label">
  <div class="main">
    <div class="logo-band">
      {('<img src="'+logo_src+'" alt="MELUNİ"/>') if logo_src else '<div class="logo-fallback">MELUNİ</div>'}
    </div>
    <div class="body-wrap">
    <div class="section-title">Gönderici Bilgileri</div>
    <div class="info-table">
      <div class="info-row"><span class="lbl">Firma</span><span class="val">{sender_company}</span></div>
      <div class="info-row"><span class="lbl">Telefon</span><span class="val">{sender_phone}</span></div>
      <div class="info-row"><span class="lbl">Adres</span><span class="val addr">{sender_addr_line}</span></div>
    </div>
    <div class="section-title">Alıcı Bilgileri</div>
    <div class="info-table">
      <div class="info-row"><span class="lbl">İsim</span><span class="val">{receiver_name}</span></div>
      <div class="info-row"><span class="lbl">Telefon</span><span class="val">{receiver_phone_fmt}</span></div>
      <div class="info-row"><span class="lbl">Adres</span><span class="val addr">{receiver_full_addr}</span></div>
    </div>
    <div class="section-title">Kargo Bilgileri</div>
    <div class="info-table">
      <div class="info-row"><span class="lbl">Kargo Firması</span><span class="val">{cargo_company_display}</span></div>
      <div class="info-row"><span class="lbl">Ödeme Türü</span><span class="val">{odeme_turu}</span></div>
      <div class="info-row"><span class="lbl">Kargo Tipi</span><span class="val">{kargo_tipi}</span></div>
    </div>
    </div>
    <div class="barcode-band">
      {_barcode_el}
      <div class="barcode-num">{siparis_no}</div>
      {tracking_line}
    </div>
  </div>
</div>
<script>
  async function fitBarcode() {{
    if (document.fonts && document.fonts.ready) {{
      try {{ await document.fonts.ready; }} catch(e) {{}}
    }}
    await new Promise(r => requestAnimationFrame(() => r()));
    const el = document.querySelector('.barcode');
    if (!el) return;   // PNG barkod kullanılıyorsa bu eleman YOK → küçültme çalışmaz
    const main = document.querySelector('.main');
    const maxW = (main ? main.clientWidth : 340) - 12;
    let size = 36;
    el.style.fontSize = size + 'pt';
    let safety = 24;
    while (el.scrollWidth > maxW && size > 18 && safety-- > 0) {{
      size -= 1;
      el.style.fontSize = size + 'pt';
    }}
  }}
  // Çok uzun adreste bilgi tablosu .body-wrap'e sığmayabilir (CSS kırpar → adresin kuyruğu
  // kaybolur). Burada kademeli küçültülerek TAMAMI sığdırılır: önce adres punto, sonra
  // barkod yüksekliği (kurye okuyucusu için 11mm'den aşağı İNİLMEZ), en son diğer satırlar.
  function fitLabel() {{
    const wrap = document.querySelector('.body-wrap');
    if (!wrap) return;
    const over = () => wrap.scrollHeight > wrap.clientHeight + 1;
    let guard = 60;
    let addrPt = 7.4;
    const addrs = document.querySelectorAll('.info-row .val.addr');
    while (over() && addrPt > 5.8 && guard-- > 0) {{
      addrPt -= 0.2;
      addrs.forEach(el => {{ el.style.fontSize = addrPt.toFixed(1) + 'pt'; }});
    }}
    const img = document.querySelector('.barcode-img');
    let bcH = 16;
    while (over() && img && bcH > 11 && guard-- > 0) {{
      bcH -= 0.5;
      img.style.height = bcH + 'mm';
    }}
    let rowPt = 8.2;
    const vals = document.querySelectorAll('.info-row .val:not(.addr), .info-row .lbl');
    while (over() && rowPt > 6.6 && guard-- > 0) {{
      rowPt -= 0.2;
      vals.forEach(el => {{ el.style.fontSize = rowPt.toFixed(1) + 'pt'; }});
    }}
  }}
  window.addEventListener('load', () => {{
    fitBarcode().then(() => {{
      fitLabel();
      if (window.location.search.includes('print=1')) {{
        setTimeout(() => window.print(), 250);
      }}
    }});
  }});
</script>
</body></html>"""


@router.get("/{order_id}/cargo-label")
async def get_cargo_label(order_id: str, current_user: dict = Depends(require_permission("orders.cargo"))):
    """100x150mm yazdırılabilir kargo etiketi (HTML + Code39).
    Tek barkod: kargo takip no varsa onu, yoksa sipariş numarasını kullanır.
    Tasarım: LOGO + ORIGIN ID + FROM/TO/REF + ORDER/ITEM/SHIP DATE/DIMENSIONS/WEIGHT
    + REMARKS + tek barkod sağ altta + sağ kenarda handling icon'ları.
    """
    from fastapi.responses import HTMLResponse
    order = await db.orders.find_one({"id": order_id}, {"_id": 0})
    if not order:
        raise HTTPException(status_code=404, detail="Sipariş bulunamadı")

    # Barkod YAZDIRMA takibi: etiket HTML'i yalnız yazdırma amaçlı çekilir (tekli +
    # toplu yazdırma bu uçtan geçer). İlk çekimde damgala → panelde kamyon SARI
    # (yazdırılmadı) yerine YEŞİL (yazdırıldı) görünür. İlk yazdırma zamanı korunur.
    try:
        await db.orders.update_one(
            {"id": order_id, "cargo_label_printed_at": {"$exists": False}},
            {"$set": {"cargo_label_printed_at": datetime.now(timezone.utc).isoformat()}},
        )
    except Exception:
        pass  # damga başarısız olsa da etiket üretimi engellenmez
    # YAZDIRMA SAYACI: her etiket çekiminde (tekli + toplu) +1. İlk-damga mantığından
    # AYRI update — kaç defa yazdırıldığı panelde görünsün (ödeme/statü/stok'a DOKUNMAZ).
    try:
        await db.orders.update_one({"id": order_id}, {"$inc": {"cargo_label_print_count": 1}})
    except Exception:
        pass

    barkod = order.get("cargo_tracking_number") or ""
    cargo_obj = order.get("cargo") or {}
    mng_siparis_no = cargo_obj.get("mng_siparis_no") or ""
    mng_gonderi_no = cargo_obj.get("mng_gonderi_no") or ""
    mng_nz_barkod = cargo_obj.get("mng_nz_barkod") or ""
    # Tek barkod: NZ > GONDERI_NO > MNG_SIPARIS_NO > tracking > sipariş_no
    real_kargo_takip = mng_nz_barkod or mng_gonderi_no or mng_siparis_no or barkod or ""
    siparis_no = str(order.get("order_number") or order_id)
    # ✅ Barkod ÇUBUKLARI = SİPARİŞ NO (W10047). Depo/operasyon barkodu okutunca siparişi
    # bulabilsin diye. Önceden çubuklar kargo takip no'yu (örn. 170553284) kodluyordu ama
    # alttaki yazı sipariş no idi → okutunca sipariş no yerine takip no çıkıyordu.
    main_barcode = siparis_no
    # Kargo Takip yazısı etiket/barkod altından KALDIRILDI (kullanıcı talebi).
    # Barkod çubukları + altındaki sipariş no zaten yeterli; ayrıca "Kargo Takip: ..." satırı basılmaz.
    tracking_line = ""
    sender = await _get_sender_info()
    mng = await _get_mng_settings()
    sender_company = mng.get("customer_code") or sender["name"] or "MELUNI"
    sender_addr_line = f"{sender['address']}, {sender['district']}/{sender['city']}".strip(" ,/")

    ship = order.get("shipping_address") or {}
    receiver_name = f"{ship.get('first_name','')} {ship.get('last_name','')}".strip() or ship.get("name") or "Alıcı"
    receiver_phone = ship.get("phone") or ""
    receiver_addr = (ship.get("address") or "").strip()
    receiver_district_city = f"{ship.get('district','')} / {ship.get('city','')}".strip(" /")
    receiver_full_addr = ", ".join([x for x in [receiver_addr, receiver_district_city] if x])

    items = order.get("items") or []
    total_qty = sum(int(it.get("quantity") or 1) for it in items)
    # İlk ürünün stock_code/sku/barcode/name'i ITEM NO; çoklu ürün varsa "+N more"
    first_item = items[0] if items else {}
    item_no = (first_item.get("stock_code") or first_item.get("sku")
               or first_item.get("barcode") or first_item.get("name") or "-")
    # Çok uzun ürün ismini kısalt
    item_no = (item_no[:32] + "…") if len(str(item_no)) > 33 else item_no
    if len(items) > 1:
        item_no = f"{item_no} (+{len(items)-1})"

    ship_date = order.get("ship_date") or order.get("created_at") or datetime.now(timezone.utc).isoformat()
    try:
        ship_date_str = datetime.fromisoformat(ship_date.replace("Z", "+00:00")).strftime("%d %b %Y").upper()
    except Exception:
        ship_date_str = datetime.now(timezone.utc).strftime("%d %b %Y").upper()

    # Dimensions / Weight — items'dan toplama
    total_weight = sum(float(it.get("weight") or 0) for it in items)
    if total_weight <= 0:
        total_weight = 0.5 * total_qty  # makul varsayılan
    dimensions = order.get("package_dimensions") or "30x20x15 cm"
    weight_str = f"{total_weight:.1f} KG"

    payment_method = (order.get("payment_method") or "").lower()
    remarks = order.get("cargo_remarks") or order.get("note") or ""
    if not remarks:
        remarks = "Alıcı Ödemeli" if payment_method in ("cash_on_delivery", "kapida") else "Gönderici Ödemeli"

    # Kargo bilgileri (paylaşılan referans şablonuna göre)
    cargo_company_display = "DHL E-Commerce"  # MNG ibaresi kaldırıldı
    payment_method = (order.get("payment_method") or "").lower()
    is_kapida = payment_method in ("cash_on_delivery", "kapida")
    odeme_turu = "Kapıda Ödemeli" if is_kapida else "Peşin Ödemeli"
    kargo_tipi = f"{odeme_turu} Kargo"

    # Telefon formatı: 5435955290 → 543 595 52 90
    html = _render_cargo_label_html(
        siparis_no=str(siparis_no),
        main_barcode=str(main_barcode),
        sender_company=sender_company,
        sender_phone=sender.get("phone", "") or "",
        sender_addr_line=sender_addr_line,
        receiver_name=receiver_name,
        receiver_phone=receiver_phone,
        receiver_full_addr=receiver_full_addr,
        cargo_company_display=cargo_company_display,
        odeme_turu=odeme_turu,
        kargo_tipi=kargo_tipi,
    )
    return HTMLResponse(content=html, headers={"Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})


# ==================== MNG KARGO AYARLARI ====================
# Bu ayarları integrations.py altındaki generic /{marketplace}/settings de yönetebilir,
# ancak özelleştirilmiş alanlar için ayrı endpoint sağlıyoruz.

@router.get("/cargo/mng-settings")
async def get_mng_settings(current_user: dict = Depends(require_permission("settings.view"))):
    """MNG Kargo ayarlarını döndür (şifre maskelenir)."""
    import os as _os
    s = await db.settings.find_one({"id": "mng_kargo"}, {"_id": 0}) or {}
    return {
        "customer_code": s.get("customer_code") or "MELUNİ DIS TIC.A.S.",
        "username": s.get("username") or _os.environ.get("MNG_USERNAME", ""),
        "password": "********" if (s.get("password") or _os.environ.get("MNG_PASSWORD")) else "",
        "tax_no": s.get("tax_no") or "6080712084",
        "is_active": s.get("is_active", True),
        "barkod_cikti_turu": s.get("barkod_cikti_turu") or "Standart",
        "musteri_kodu_goster": s.get("musteri_kodu_goster", False),
    }


@router.post("/cargo/mng-settings")
async def save_mng_settings(payload: dict, request: Request,
                            current_user: dict = Depends(require_permission("integrations.mng"))):
    """MNG Kargo ayarlarını kaydet. GÜVENLİK: 'integrations.mng' yetkisi (süper admin geçer) +
    denetim kaydı (parola ASLA loglanmaz; yalnız 'değişti mi' bilgisi)."""
    payload = payload if isinstance(payload, dict) else {}
    _before = await db.settings.find_one({"id": "mng_kargo"}, {"_id": 0, "password": 0}) or {}
    update = {
        "customer_code": payload.get("customer_code", "MELUNİ DIŞ TİC.A.Ş."),
        "username": payload.get("username", ""),
        "tax_no": payload.get("tax_no", ""),
        "is_active": bool(payload.get("is_active", True)),
        "barkod_cikti_turu": payload.get("barkod_cikti_turu", "Standart"),
        "musteri_kodu_goster": bool(payload.get("musteri_kodu_goster", False)),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    if payload.get("password") and payload.get("password") != "********":
        # GÜVENLİK: MNG parolasını at-rest ŞİFRELE (_get_mng_settings okurken çözülür).
        try:
            from security.crypto import encrypt as _enc_secret
            update["password"] = _enc_secret(payload.get("password"))
        except Exception:
            update["password"] = payload.get("password")
    await db.settings.update_one({"id": "mng_kargo"}, {"$set": update}, upsert=True)
    _keys = ("customer_code", "username", "tax_no", "is_active", "barkod_cikti_turu", "musteri_kodu_goster")
    await record_admin_audit(
        db, action="integrations.mng_settings_save", entity_type="settings", entity_id="mng_kargo",
        before={k: _before.get(k) for k in _keys}, after={k: update.get(k) for k in _keys},
        current_user=current_user, request=request, source="admin.cargo",
        metadata={"password_changed": "password" in update})
    return {"success": True, "message": "MNG Kargo ayarları kaydedildi"}


@router.post("/cargo/mng-test")
async def test_mng_connection(current_user: dict = Depends(require_permission("settings.view"))):
    """MNG Kargo bağlantı testi (Baglanti_Test)."""
    import sys, os
    sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
    from mng_kargo_client import baglanti_test
    import asyncio as _aio_mng
    return await _aio_mng.to_thread(baglanti_test)


# =============================================================================
# Faz 2 — Havale dekont bildirimi (storefront yukleme + admin goruntuleme)
# =============================================================================
@router.post("/by-number/{order_number}/payment-notification")
@(limiter.limit("10/minute") if limiter else (lambda f: f))
@(limiter.limit("60/minute", key_func=global_rate_key) if limiter else (lambda f: f))
async def submit_payment_notification(
    request: Request,
    order_number: str,
    file: UploadFile = File(...),
    note: str = Form(""),
    contact: str = Form(""),
    current_user: dict = Depends(get_current_user),
):
    """Musteri: ilgili siparis icin dekont (PDF/gorsel) yukler (rate-limited).
    Auth gerekmez. GÜVENLİK (denetim 2026-10): sipariş numarası tahmin edilebilir → ek kimlik
    kanıtı ZORUNLU: `contact` = siparişteki e-posta adresinin TAMAMI ya da telefonun SON 4 hanesi.
    Giriş yapmış müşteri KENDİ siparişine (order.user_id) yüklüyorsa oturum kanıt sayılır
    ("Hesabım → Ödeme Bildirimi Yap" akışında ek bilgi istenmez)."""
    _ = request  # rate-limit için
    order = await db.orders.find_one({"order_number": order_number}, {"_id": 0})
    _contact = str(contact or "").strip().lower()
    _owner = bool(order and current_user and current_user.get("id")
                  and str(order.get("user_id") or "") == str(current_user.get("id")))
    if not _contact and not _owner:
        raise HTTPException(status_code=400, detail=(
            "Lütfen siparişteki e-posta adresinizi veya telefon numaranızın son 4 hanesini girin."))
    _id_ok = _owner
    # Denetim #17: sipariş başına deneme kilidi (misafir iade uçlarıyla AYNI sayaç): 1 saatte 5 hatalı
    # doğrulamada dekont yükleme de kilitlenir (kalan haneler denenemez).
    _vkey = str((order or {}).get("id") or "")
    _vnow = datetime.now(timezone.utc)
    _vsince = (_vnow - timedelta(seconds=_GUEST_VERIFY_WINDOW_S)).isoformat()
    _vrec = await db.guest_verify_attempts.find_one({"key": _vkey}, {"_id": 0}) if (order and not _id_ok) else None
    if _vrec and str(_vrec.get("first_at") or "") >= _vsince and int(_vrec.get("fails") or 0) >= _GUEST_VERIFY_MAX_FAILS:
        raise HTTPException(status_code=429, detail=(
            "Çok fazla hatalı deneme. Lütfen 1 saat sonra tekrar deneyin veya bizimle iletişime geçin."))
    if order and not _id_ok:
        _addr_pn = order.get("shipping_address") or {}
        _bill_pn = order.get("billing_address") or {}
        _emails = {str(x or "").strip().lower() for x in (
            _addr_pn.get("email"), _bill_pn.get("email"), order.get("email"), order.get("user_email"))}
        _emails.discard("")
        _phones = {"".join(ch for ch in str(x or "") if ch.isdigit()) for x in (
            _addr_pn.get("phone"), _bill_pn.get("phone"), order.get("phone"))}
        _phones.discard("")
        if "@" in _contact:
            _id_ok = _contact in _emails
        else:
            _digits = "".join(ch for ch in _contact if ch.isdigit())
            if len(_digits) >= 10:   # numaranın tamamı girildiyse son 10 hane karşılaştırılır
                _id_ok = any(len(p) >= 10 and p[-10:] == _digits[-10:] for p in _phones)
            else:
                _id_ok = len(_digits) == 4 and any(len(p) >= 4 and p[-4:] == _digits for p in _phones)
        if not _id_ok:
            if _vrec and str(_vrec.get("first_at") or "") >= _vsince:
                await db.guest_verify_attempts.update_one(
                    {"key": _vkey}, {"$inc": {"fails": 1}, "$set": {"last_at": _vnow.isoformat()}})
            else:
                await db.guest_verify_attempts.update_one(
                    {"key": _vkey}, {"$set": {"fails": 1, "first_at": _vnow.isoformat(),
                                             "last_at": _vnow.isoformat()}}, upsert=True)
    if not order or not _id_ok:
        # Sipariş var/yok ayrımı sızdırılmaz (numara tarama koruması).
        raise HTTPException(status_code=404, detail=(
            "Sipariş bilgileri eşleşmedi. Sipariş numaranızı ve siparişteki e-posta adresinizi "
            "veya telefonunuzun son 4 hanesini kontrol edin."))
    # GÜVENLİK: Sipariş no tahmin edilebilir → dekont yükleme YALNIZCA gerçekten dekont
    # bekleyen (havale/EFT + ödeme bekliyor) siparişlerde. Böylece kart/ödenmiş/tamamlanmış
    # siparişlerin dekontu üzerine yazma (griefing) + durum forge yüzeyi daraltılır.
    _pm = str(order.get("payment_method") or "").lower()
    _is_bank = any(k in _pm for k in ("havale", "eft", "transfer", "banka"))
    # payment_notified: müşteri yanlış/okunaksız dekontu YENİSİYLE değiştirebilir (eskiden 2. yükleme
    # "dekont yüklenemez" ile reddediliyordu; sayfa ise "Yeni dekont yükle" sunuyordu).
    _st = order.get("status")
    _pending = _st in ("awaiting_payment", "pending", "payment_notified") and \
        order.get("payment_status") not in ("paid", "refunded")
    if not (_is_bank and _pending):
        if _st in ("cancelled", "canceled", "payment_failed") or order.get("auto_cancelled"):
            raise HTTPException(status_code=400, detail="Bu sipariş ödeme süresi dolduğu için iptal edilmiş. "
                                                        "Ödeme yaptıysanız lütfen bizimle iletişime geçin.")
        if order.get("payment_status") == "paid" or _st in ("confirmed", "processing", "shipped", "delivered"):
            raise HTTPException(status_code=400, detail="Bu siparişin ödemesi zaten onaylanmış; dekont yüklemenize gerek yok.")
        raise HTTPException(status_code=400, detail="Bu sipariş için dekont yüklenemez (havale/EFT siparişi değil).")
    from security.uploads import read_upload_capped
    data = await read_upload_capped(file, 15 * 1024 * 1024, "Dosya 15MB'tan büyük olamaz")
    ctype = _receipt_base_type(file.content_type)
    if ctype not in _RECEIPT_SAFE_TYPES:
        # Bazı telefon/bilgisayarlar türü boş ya da octet-stream gönderir → uzantıdan tanı.
        _ext = (file.filename or "").rsplit(".", 1)[-1].lower() if "." in (file.filename or "") else ""
        _by_ext = {"pdf": "application/pdf", "jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
                   "heic": "image/heic", "heif": "image/heif", "webp": "image/webp"}
        ctype = _by_ext.get(_ext, "")
        if not ctype:
            raise HTTPException(status_code=400, detail="Sadece PDF veya görsel (JPG, PNG, HEIC) yükleyebilirsiniz")
    # Tür etiketi istemciden gelir (yalan olabilir): SVG/HTML/XML baytı hiçbir türde kabul edilmez.
    if _receipt_looks_like_markup(data):
        raise HTTPException(status_code=400, detail="Sadece PDF veya görsel (JPG, PNG, HEIC) yükleyebilirsiniz")
    now_iso = datetime.now(timezone.utc).isoformat()
    receipt = {
        "filename": (file.filename or "dekont")[:120],
        "content_type": ctype,
        "size": len(data),
        # BL-040: dekont at-rest ŞİFRELİ (Fernet, ham bayt). Okuma: get_receipt_bytes().
        "data_enc": _encrypt_receipt_bytes(data),
        "enc_format": _RECEIPT_ENC_FORMAT,
        "note": (note or "")[:500],
        "uploaded_at": now_iso,
    }
    upd = {
        "payment_receipt": receipt,
        "payment_receipt_count": int(order.get("payment_receipt_count") or (1 if order.get("payment_receipt") else 0)) + 1,
        "payment_notified": True,
        "payment_notified_at": now_iso,
        "updated_at": now_iso,
    }
    if order.get("status") in ("awaiting_payment", "pending"):
        upd["status"] = "payment_notified"
    # ÖNCEKİ DEKONT KAYBOLMASIN (denetim 2026-10): yeni dekont `payment_receipt`'e yazılır
    # (gece arşiv işi + admin paneli bu yapıyı okur — DEĞİŞMEDİ); öncekinin BAYT verisi ayrı
    # koleksiyona (payment_receipt_versions) taşınır (16MB belge sınırı), siparişte yalnız
    # metadata listesi `payment_receipts_history` (en çok 10) tutulur.
    _ops = {"$set": upd}
    _prev_r = order.get("payment_receipt") or {}
    if _prev_r:
        _ver_id = str(uuid.uuid4())
        _hist = {k: _prev_r.get(k) for k in ("filename", "content_type", "size", "note", "uploaded_at",
                                             "archive_key", "archived_at") if _prev_r.get(k) is not None}
        _hist["replaced_at"] = now_iso
        if _prev_r.get("data_b64") or _prev_r.get("data_enc"):
            try:
                # Eski (düz data_b64) dekont da sürüm koleksiyonuna ŞİFRELENEREK taşınır.
                await db.payment_receipt_versions.insert_one({
                    "id": _ver_id, "order_id": order["id"], "order_number": order.get("order_number", ""),
                    "receipt": seal_receipt(_prev_r), "created_at": now_iso})
                _hist["version_id"] = _ver_id
            except Exception as _ve:
                logger.warning(f"[dekont] önceki dekont saklanamadı order={order.get('order_number')}: {_ve}")
        _ops["$push"] = {"payment_receipts_history": {"$each": [_hist], "$slice": -10}}
    await db.orders.update_one({"id": order["id"]}, _ops)
    if _prev_r:
        try:
            _keep = [h.get("version_id") for h in ((await db.orders.find_one(
                {"id": order["id"]}, {"_id": 0, "payment_receipts_history": 1}) or {})
                .get("payment_receipts_history") or []) if h.get("version_id")]
            await db.payment_receipt_versions.delete_many({"order_id": order["id"], "id": {"$nin": _keep}})
        except Exception:
            pass

    # Müşteri bildirimi sipariş başına SAATTE EN ÇOK 1 (tekrar yüklemede SMS/e-posta seli olmasın).
    _cut = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    _claim_n = await db.orders.find_one_and_update(
        {"id": order["id"], "$or": [{"payment_notified_customer_at": {"$exists": False}},
                                    {"payment_notified_customer_at": None},
                                    {"payment_notified_customer_at": {"$lt": _cut}}]},
        {"$set": {"payment_notified_customer_at": now_iso}}, projection={"_id": 1})

    import asyncio as _aio
    async def _notif():
        if not _claim_n:
            return
        try:
            import sys, os
            sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
            from notification_service import send_notification
            from order_statuses import get_status_config
            cfg = await get_status_config(db)
            nz = (cfg.get("notify") or {}).get("payment_notified") or {}
            ch = [c for c in ("sms", "email") if nz.get(c)]
            if not ch:
                return
            addr = order.get("shipping_address") or {}
            await send_notification(
                db, "order_payment_notified",
                to_phone=addr.get("phone") or order.get("phone"),
                to_email=addr.get("email") or order.get("email"),
                variables=await _order_notify_vars(order, order_number=order_number),
                channels=ch,
            )
        except Exception as e:
            logger.warning(f"payment_notified notif failed: {e}")
    _spawn(_notif())
    return {"success": True, "message": "Ödeme bildiriminiz alındı, en kısa sürede kontrol edilecek."}


@router.get("/{order_id}/payment-receipt")
async def get_payment_receipt(order_id: str, current_user: dict = Depends(require_permission("orders.view"))):
    """Admin: yuklenen dekontu goruntule/indir."""
    order = await db.orders.find_one({"id": order_id}, {"_id": 0, "payment_receipt": 1})
    r = (order or {}).get("payment_receipt")
    if not r:
        raise HTTPException(status_code=404, detail="Dekont bulunamadı")
    raw = get_receipt_bytes(r)   # BL-040: şifreli data_enc ya da eski data_b64
    if raw is not None:
        pass
    elif r.get("data_enc"):
        raise HTTPException(status_code=500, detail="Dekont şifresi çözülemedi (anahtar yapılandırmasını kontrol edin)")
    elif r.get("archive_key"):
        # 30 günden eski dekontlar gizli R2 arşivine taşınır (veritabanında tutulmaz).
        import asyncio as _aio_r
        from services import r2_storage as _r2
        if not _r2.archive_enabled():
            raise HTTPException(status_code=503, detail="Dekont arşivde; arşiv deposu yapılandırılmamış")
        raw, _ = await _aio_r.to_thread(_r2.archive_get, r["archive_key"])
        if raw is None:
            raise HTTPException(status_code=404, detail="Dekont arşivde bulunamadı")
        if r.get("archive_enc_format") == _RECEIPT_ENC_FORMAT:
            # Arşive şifreli (Fernet token) yazılmış dekont → çöz.
            try:
                from security import crypto as _c
                raw = _c._fernet_dec.decrypt(raw)
            except Exception:
                raise HTTPException(status_code=500, detail="Arşivdeki dekontun şifresi çözülemedi")
    else:
        raise HTTPException(status_code=404, detail="Dekont bulunamadı")
    _ct = _receipt_base_type(r.get("content_type") or "application/octet-stream")
    # GÜVENLİK: filename header injection'ı temizle
    _fn = str(r.get("filename", "dekont") or "dekont").replace('"', "").replace("\r", "").replace("\n", "")[:100]
    # SVG/HTML veya güvenli olmayan tür → inline RENDER etme (admin tarayıcısında XSS),
    # indir olarak sun + sandbox. Yalnız gerçek raster görsel / PDF inline gösterilir.
    # Kesin izin listesi: "svg içermiyor" kontrolü `image/x+xml` (Chrome'da XML belgesi) türünü
    # kaçırıyordu. Panel dosyayı BLOB olarak açar ve blob, başlıkları (attachment/CSP) değil
    # yalnız Content-Type'ı taşır → güvensiz türde gövde `application/octet-stream` gönderilir.
    _safe_inline = _ct in _RECEIPT_SAFE_TYPES
    if not _safe_inline:
        _ct = "application/octet-stream"
    _disp = "inline" if _safe_inline else "attachment"
    _headers = {
        "Content-Disposition": f'{_disp}; filename="{_fn}"',
        "X-Content-Type-Options": "nosniff",
    }
    if not _safe_inline:
        _headers["Content-Security-Policy"] = "default-src 'none'; sandbox"
    return Response(content=raw, media_type=_ct or "application/octet-stream", headers=_headers)


@router.post("/cargo/poll-now")
async def cargo_poll_now(current_user: dict = Depends(require_permission("settings.edit"))):
    """Admin: DHL/MNG kargo durum taramasini hemen calistir (5 dk'lik job'in manuel tetigi)."""
    try:
        import sys, os
        sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
        from scheduler import _dhl_cargo_poll_tick
        await _dhl_cargo_poll_tick()
        return {"success": True, "message": "DHL/MNG kargo taraması çalıştırıldı."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Tarama hatası: {e}")


@router.get("/cargo/poll-health")
async def cargo_poll_health(current_user: dict = Depends(require_permission("settings.view"))):
    """Admin: DHL/MNG kargo durum taraması sağlık/izleme bilgisi.

    Ayarlar > Kargo sayfasındaki izleme paneli bu veriyi gösterir: son çalışma
    zamanı, sonuç (running/ok/skipped/error), sorgulanan/değişen sipariş sayıları,
    son hata mesajı, ortalama süre ve MNG/DHL ayarlarının aktif olup olmadığı.
    Senkron hiç çalışmadıysa status='unknown' döner (panel bunu uyarı olarak gösterir).
    """
    h = await db.settings.find_one({"id": "dhl_poll_health"}, {"_id": 0}) or {}
    mng = await _get_mng_settings()
    # Günlük limit bayrağı mng_kargo ayar dokümanında saklanır (poll + backfill ortak kullanır).
    _mng_lim = await db.settings.find_one({"id": "mng_kargo"}, {"_id": 0, "daily_limit_hit_at": 1}) or {}
    _lim_at = _mng_lim.get("daily_limit_hit_at") or ""
    _lim_today = False
    if _lim_at:
        try:
            _lim_today = datetime.fromisoformat(_lim_at).date() == datetime.now(timezone.utc).date()
        except Exception:
            _lim_today = False
    hist = list(reversed(h.get("history") or []))[:20]  # yeni → eski
    return {
        "ok": True,
        "interval_min": h.get("interval_min", 5),
        "status": h.get("status") or "unknown",
        "last_run_at": h.get("last_run_at"),
        "last_finish_at": h.get("last_finish_at"),
        "updated_at": h.get("updated_at"),
        "processed": h.get("processed", 0),
        "matched": h.get("matched", 0),
        "last_note": h.get("last_note", ""),
        "shipped": h.get("shipped", 0),
        "delivered": h.get("delivered", 0),
        "updated": h.get("updated", 0),
        "errors": h.get("errors", 0),
        "duration_ms": h.get("duration_ms", 0),
        "last_error": h.get("last_error", ""),
        "skipped_reason": h.get("skipped_reason", ""),
        "daily_limit": bool(h.get("daily_limit")) or _lim_today,
        "daily_limit_hit_at": _lim_at,
        "mng_active": bool(mng.get("is_active")),
        "mng_user_set": bool(mng.get("username")),
        "history": hist,
    }


# =============================================================================
# Faz 4 — Müşteri iadesi (14 gün) + DHL/MNG iade barkodu (3 gün geçerli)
# Veri: db.customer_returns + order.return_request (özet)
# =============================================================================
# Anlaşmalı kargo (MNG/DHL E-Commerce) müşteri/sözleşme numarası.
# Müşteri HER İADEDE bu numarayı yedek olarak görür; per-gönderi barkod tanınmazsa
# en yakın şubeye bu numarayla teslim edebilir (gönderi bizim sözleşmemize işlenir).
RETURN_CONTRACT_NO = "490059279"


def _company_return_address(warehouse: dict) -> str:
    """İade paketinin gideceği ŞİRKET (alıcı) adresini tek satır metne çevirir (müşteriye gösterilir)."""
    w = warehouse or {}
    parts = [w.get("name") or "MELEK GİRAY",
             (w.get("address") or "").strip(),
             "/".join([x for x in [w.get("district"), w.get("city")] if x])]
    return " · ".join([p for p in parts if p])


def _render_return_barcode_png_b64(code: str) -> str:
    """İade kodunu Code128 PNG (base64) barkoda çevirir (e-posta + ekran için)."""
    try:
        import io as _io, base64 as _b64
        import barcode as _bc
        from barcode.writer import ImageWriter as _IW
        c = (code or "").strip() or "IADE"
        buf = _io.BytesIO()
        _bc.get("code128", c, writer=_IW()).write(
            buf, options={"module_height": 12.0, "module_width": 0.3,
                          "font_size": 10, "text_distance": 3.5, "quiet_zone": 2.0}
        )
        return _b64.b64encode(buf.getvalue()).decode("ascii")
    except Exception as e:
        logger.warning(f"return barcode render failed: {e}")
        return ""


def _public_return(rec: dict) -> dict:
    png = rec.get("barcode_png_b64") or ""
    status = rec.get("status")
    # Süresi dolan iade kodu: 'created' + valid_until geçmiş → müşteriye 'expired' görünür,
    # barkod GÖSTERİLMEZ (şubede okutulamaz). 14 gün penceresi hâlâ açıksa müşteri yeni kod üretir.
    expired = False
    if status == "created":
        try:
            vu = datetime.fromisoformat(str(rec.get("valid_until") or "").replace("Z", "+00:00"))
            if vu.tzinfo is None:
                vu = vu.replace(tzinfo=timezone.utc)
            expired = datetime.now(timezone.utc) > vu
        except Exception:
            expired = False
    if expired:
        status, png = "expired", ""
    return {
        "id": rec.get("id"),
        "order_number": rec.get("order_number"),
        "return_code": rec.get("return_code"),
        "iade_no": rec.get("iade_no") or rec.get("mng_ref") or "",
        "gonderi_no": rec.get("gonderi_no") or "",
        "contract_no": rec.get("contract_no") or RETURN_CONTRACT_NO,
        "company_address": rec.get("company_address") or "",
        "status": status,
        "valid_until": rec.get("valid_until"),
        "cargo_provider_name": rec.get("cargo_provider_name"),
        "mng_ok": rec.get("mng_ok", False),
        "items": rec.get("items") or [],
        "reason": rec.get("reason", ""),
        "created_at": rec.get("created_at"),
        "barcode_data_url": (f"data:image/png;base64,{png}" if png else ""),
    }


async def _ensure_return_email_template():
    """İade e-posta şablonunu markalı tasarım (logo · kod · barkod · footer) ile garanti eder."""
    import sys, os
    sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
    from email_layout import email_shell, info_row
    SENTINEL = "<!--meluni-email-v4-->"
    body = SENTINEL + email_shell(
        icon="↩", eyebrow="İADE", title="İade talebin oluşturuldu",
        intro_html=("Merhaba {customer_name}, {order_number} numaralı siparişin için iade talebin oluşturuldu. "
                    "Ürünü en yakın DHL / MNG şubesine aşağıdaki kodu veya barkodu göstererek teslim edebilirsin."),
        body_html=(info_row("İade Kargo Kodu", "{return_code}")
                   + info_row("İade No", "{iade_no}")
                   + info_row("Anlaşmalı No (yedek)", "{contract_no}")
                   + '<div style="text-align:center;margin-top:18px;">{return_barcode_img}</div>'),
        note_title="Bu kod 3 gün geçerlidir.",
        note_html="Son geçerlilik: {valid_until} · Şube barkodu okutamazsa anlaşmalı kargo no'muz ({contract_no}) ile teslim edebilirsin.",
        preheader="İade kargo kodun: {return_code}",
    )
    try:
        tpl = await db.notification_templates.find_one(
            {"event": "order_return_requested", "channel": "email"}, {"_id": 0}
        )
        cur = (tpl or {}).get("body") or ""
        # Eski tasarımı (veya barkodsuz/markasız olanı) yeni markalı tasarıma yükselt;
        # admin manuel düzenlediyse dokunma.
        needs_update = (not tpl) or (SENTINEL not in cur) or ("{return_barcode_img}" not in cur)
        if needs_update and not (tpl or {}).get("manually_edited"):
            await db.notification_templates.update_one(
                {"event": "order_return_requested", "channel": "email"},
                {"$set": {"event": "order_return_requested", "channel": "email", "enabled": True,
                          "subject": "İade talebin oluşturuldu · {order_number}", "body": body,
                          "updated_at": datetime.now(timezone.utc).isoformat()}},
                upsert=True,
            )
    except Exception as e:
        logger.warning(f"ensure return email tpl failed: {e}")


async def _notify_return(order: dict, code: str, valid_until: str, barcode_img: str, iade_no: str = ""):
    try:
        import sys, os
        sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
        from notification_service import send_notification
        from order_statuses import get_status_config
        await _ensure_return_email_template()
        cfg = await get_status_config(db)
        nz = (cfg.get("notify") or {}).get("return_requested") or {}
        ch = [c for c in ("sms", "email") if nz.get(c)]
        if not ch:
            return
        addr = order.get("shipping_address") or {}
        try:
            vu = datetime.fromisoformat(valid_until).strftime("%d.%m.%Y %H:%M")
        except Exception:
            vu = valid_until
        await send_notification(
            db, "order_return_requested",
            to_phone=addr.get("phone") or order.get("phone"),
            to_email=addr.get("email") or order.get("email"),
            variables=await _order_notify_vars(order, return_code=code, iade_no=iade_no or code, contract_no=RETURN_CONTRACT_NO, valid_until=vu, return_barcode_img=barcode_img),
            channels=ch,
        )
    except Exception as e:
        logger.warning(f"return notif failed: {e}")


async def _effective_delivered_at(order: dict):
    """Gerçek teslim tarihi varsa onu döndür. YOKSA fallback: MNG/DHL kargo durum senkronu bazen
    teslimatı HİÇ raporlamıyor (kargo_statu 0 / "İşlem Yapılmadı"da takılı) → sipariş 'preparing'de
    kalıp `delivered_at` boş kalıyor ve müşteri teslim aldığı hâlde İADE AÇAMIYOR. Bu durumda:
    ödenmiş + yeterince eski (kargoya verilme/sipariş tarihinden İşletme-Kuralı eşiği kadar gün geçmiş)
    siparişleri VARSAYILAN TESLİM say → iade başlatılabilsin (admin yine onaylar/reddeder).
    İptal/ödeme-bekleyen/başarısız/zaten-iade durumları hariç. Döner: (iso_str|None, presumed:bool)."""
    real = order.get("delivered_at")
    if real:
        return real, False
    st = str(order.get("status") or "").lower()
    pay = str(order.get("payment_status") or "").lower()
    _blocked = {"awaiting_payment", "pending", "cancelled", "canceled", "failed",
                "returned", "refunded", "partial_refunded", "return_requested"}
    if st in _blocked:
        return None, False
    if pay and pay != "paid":
        return None, False
    try:
        import business_rules as _BR
        _pd = int(await _BR.get_rule(db, "return.presume_delivered_after_days", 5) or 5)
    except Exception:
        _pd = 5
    if _pd < 1:
        return None, False
    anchor = order.get("shipped_at") or order.get("created_at")
    if not anchor:
        return None, False
    try:
        a = datetime.fromisoformat(str(anchor).replace("Z", "+00:00"))
        if a.tzinfo is None:
            a = a.replace(tzinfo=timezone.utc)
    except Exception:
        return None, False
    if datetime.now(timezone.utc) >= (a + timedelta(days=_pd)):
        return (a + timedelta(days=_pd)).isoformat(), True
    return None, False


# MÜKERRER İADE KORUMASI (denetim 2026-10): bir sipariş kalemi, adedinden FAZLA iade
# edilemez. Ürünleri yola çıkmış / teslim alınmış / onaylanmış / bedeli ödenmiş iadeler
# "iade edilmiş adet" sayılır (created = henüz kod üretildi → sayılmaz; reddedilen / iptal /
# süresi dolan sayılmaz). Kalem kimliği: ürün id (yoksa ad) + beden + renk.
_RETURN_COUNTED_STATUSES = ("approved", "in_transit", "received", "returned", "refunded", "partial_refunded")


def _return_item_key(it: dict) -> tuple:
    def _n(v):
        return " ".join(str(v or "").strip().lower().split())
    return (_n(it.get("product_id") or it.get("sku") or it.get("name") or it.get("product_name")),
            _n(it.get("size")), _n(it.get("color")))


def _return_record_items(rec: dict) -> list:
    """İade kaydının GERÇEKTEN iade edilen kalemleri (kısmi onayda approved_items; adet rec.items'tan)."""
    items = rec.get("items") or []
    appr = rec.get("approved_items") or []
    if not appr:
        return items
    try:
        idx = _resolve_return_selection(items, appr, rec.get("approved_item_indexes"))
    except ValueError:
        return items
    return [items[i] for i in idx if 0 <= i < len(items)] or items


async def _already_returned_qty(order_id: str, exclude_return_id: str = None) -> dict:
    out = {}
    q = {"order_id": order_id, "status": {"$in": list(_RETURN_COUNTED_STATUSES)}}
    if exclude_return_id:
        q["id"] = {"$ne": exclude_return_id}
    async for r in db.customer_returns.find(q, {"_id": 0, "items": 1, "approved_items": 1,
                                                "approved_item_indexes": 1}):
        for it in _return_record_items(r):
            k = _return_item_key(it)
            try:
                out[k] = out.get(k, 0) + max(0, int(it.get("quantity") or 1))
            except Exception:
                out[k] = out.get(k, 0) + 1
    return out


def _order_qty_by_key(order: dict) -> dict:
    out = {}
    for it in (order.get("items") or []):
        k = _return_item_key(it)
        try:
            out[k] = out.get(k, 0) + max(0, int(it.get("quantity") or 1))
        except Exception:
            out[k] = out.get(k, 0) + 1
    return out


async def _build_return_for_order(order: dict, payload: dict, actor: dict) -> dict:
    """COM10: aynı sipariş için iade talebi (müşteri RMA + panel manuel iade) ATOMİK sipariş-başı
    kilitle sırayla işlenir. Eşzamanlı ikinci talep 409 alır; kilit alındıktan SONRA sipariş ve
    önceki iadeler yeniden okunur → iade edilebilir adet iki kez tüketilemez, MNG iade kaydı iki
    kez açılmaz."""
    _tok = await _acquire_order_op_lock(order["id"], "return_req", ttl_s=120)
    if not _tok:
        raise HTTPException(status_code=409, detail=(
            "Bu sipariş için başka bir iade işlemi sürüyor. Lütfen birkaç saniye sonra tekrar deneyin."))
    try:
        fresh = await db.orders.find_one({"id": order["id"]}, {"_id": 0}) or order
        return await _build_return_for_order_locked(fresh, payload, actor)
    finally:
        await _release_order_op_lock(order["id"], "return_req", _tok)


async def _build_return_for_order_locked(order: dict, payload: dict, actor: dict) -> dict:
    """İade talebi ÇEKİRDEĞİ — üye ve misafir uçları aynı mantığı kullanır.
    14 gün penceresi (teslimden) + 3 gün kod geçerliliği + DHL/MNG iade kodu/barkod + kayıt + bildirim.
    actor: log için {email,id,...} (üye current_user ya da misafir için sentetik)."""
    # --- 14 gün penceresi (teslim anından itibaren, 1 sn bile geçse engelle) ---
    # delivered_at boşsa: kargo senkronu teslimatı raporlamamış olabilir → varsayılan-teslim fallback.
    delivered_at, _presumed = await _effective_delivered_at(order)
    if not delivered_at:
        raise HTTPException(status_code=400, detail="Sipariş henüz teslim edilmedi; iade başlatılamaz.")
    try:
        d = datetime.fromisoformat(str(delivered_at).replace("Z", "+00:00"))
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
    except Exception:
        raise HTTPException(status_code=400, detail="Teslim tarihi okunamadı.")
    now = datetime.now(timezone.utc)
    if now > (d + timedelta(days=14)):
        raise HTTPException(status_code=400, detail="İade süresi (teslimden itibaren 14 gün) dolmuştur.")

    existing = await db.customer_returns.find_one(
        {"order_id": order["id"], "status": {"$in": ["created", "in_transit"]}}, {"_id": 0}
    )
    if existing:
        _still_valid = True
        if existing.get("status") == "created":
            try:
                _vu = datetime.fromisoformat(str(existing.get("valid_until") or "").replace("Z", "+00:00"))
                if _vu.tzinfo is None:
                    _vu = _vu.replace(tzinfo=timezone.utc)
                _still_valid = now <= _vu
            except Exception:
                _still_valid = True
        if _still_valid:
            return {"success": True, "already": True, "return": _public_return(existing)}
        await db.customer_returns.update_one(
            {"id": existing.get("id")},
            {"$set": {"status": "expired", "expired_at": now.isoformat()}})
        await _log_order_event(order["id"], "return", "İade kodu süresi doldu — yeni kod üretiliyor",
                               actor, {"return_id": existing.get("id"),
                                       "old_return_code": existing.get("return_code")},
                               order_number=order.get("order_number", ""))

    src_items = order.get("items") or []
    sel = payload.get("items")
    chosen = []
    if isinstance(sel, list) and sel:
        # GÜVENLİK (commerce-3, para kaçağı): her sipariş kalemi EN FAZLA BİR KEZ seçilebilir ve
        # index aralık içinde olmalı. Eskiden [1,1] / [1,-1] gibi tekrarlanan ya da negatif
        # (Python'da sondan sayan) index'ler aynı ucuz kalemi iade kaydında sipariş kalem sayısına
        # şişiriyordu → onay/önizleme (_return_covers_whole_order: kalem+adet sayımı) talebi
        # "tüm sipariş" sanıp SİPARİŞİN TAMAMINI iade ediyordu; stok da fazladan geri ekleniyordu.
        _seen_idx = set()
        for idx in sel:
            try:
                _ix = int(idx)
            except Exception:
                continue
            if _ix < 0 or _ix >= len(src_items) or _ix in _seen_idx:
                continue
            _seen_idx.add(_ix)
            chosen.append(src_items[_ix])
    if not chosen:
        chosen = src_items
    items = [{
        "name": it.get("name") or it.get("product_name") or "Ürün",
        "size": it.get("size", ""), "color": it.get("color", ""),
        "quantity": int(it.get("quantity", 1) or 1),
        "price": float(it.get("price") or it.get("unit_price") or 0),
        "product_id": it.get("product_id") or it.get("sku") or "",
    } for it in chosen]
    # MÜKERRER İADE: önceki (yola çıkmış/onaylı/ödenmiş) iadelerde iade edilen adet düşülür;
    # kalan adedi olmayan kalem talebe girmez. Hiç kalan yoksa talep açılmaz.
    _ret_before = await _already_returned_qty(order["id"])
    if _ret_before:
        _left = {k: max(0, v - _ret_before.get(k, 0)) for k, v in _order_qty_by_key(order).items()}
        _kept = []
        for _it in items:
            _k = _return_item_key(_it)
            _q = min(int(_it.get("quantity") or 1), _left.get(_k, 0))
            if _q > 0:
                _left[_k] = _left.get(_k, 0) - _q
                _kept.append({**_it, "quantity": _q})
        if not _kept:
            raise HTTPException(status_code=400, detail=(
                "Bu siparişteki seçilen ürünler için daha önce iade talebi işlendi; iade edilecek ürün kalmadı."))
        items = _kept
    # İade sebebi ZORUNLU — üye ve misafir tüm site iadelerinde. Panelde görünür.
    reason = (payload.get("reason") or "").strip()[:500]
    if not reason:
        raise HTTPException(status_code=400, detail="İade sebebi seçmek zorunludur.")
    reason_code = (payload.get("reason_code") or "").strip()[:60]

    # Havale/EFT iadesinde iade tutarı BANKA HESABINA döner → müşterinin girdiği IBAN + ad soyad
    # kaydedilir (admin iadeyi bu hesaba yapar). Kart ödemesinde saklanmaz (para karta döner).
    _pm = str(order.get("payment_method") or "").lower()
    refund_bank_info = None
    if _pm in ("bank_transfer", "havale", "eft", "bank"):
        _iban = "".join(str(payload.get("refund_iban") or "").split()).upper()[:34]
        # ZORUNLU (kullanıcı kuralı): havale/EFT ile ödenmiş siparişin iadesinde IBAN + ad soyad
        # olmadan talep AÇILAMAZ (form gizlense/atlansa bile sunucu reddeder).
        from routes.customer import valid_tr_iban as _viban
        if not _iban or not _viban(_iban):
            raise HTTPException(status_code=400, detail="Havale/EFT ile ödenen siparişin iadesi için geçerli bir TR IBAN zorunludur (iade bu hesaba yapılır).")
        if not str(payload.get("refund_name") or "").strip():
            raise HTTPException(status_code=400, detail="IBAN sahibinin ad soyadı zorunludur.")
        if True:
            # BL-040: IBAN + hesap sahibi adı at-rest ŞİFRELİ yazılır (iban_last4 görüntüleme için).
            refund_bank_info = seal_refund_bank_info({
                "iban": _iban,
                "name": str(payload.get("refund_name") or "").strip()[:120],
                "bank": str(payload.get("refund_bank") or "").strip()[:80],
            })

    rid = generate_id()
    iade_no = f"IW{order.get('order_number', '')}{rid[:6]}".replace(" ", "")
    icerik = "IADE - " + "; ".join(f"{i['quantity']}x {i['name']}" for i in items)
    warehouse = await _get_sender_info()
    cargo_name = "DHL E-Commerce"
    gonderi_no, mng_ok = await _create_return_shipment(
        iade_no, float(order.get("total") or 0), icerik, warehouse
    )
    return_code = gonderi_no or iade_no

    png_b64 = _render_return_barcode_png_b64(return_code)
    now_iso = now.isoformat()
    valid_until = (now + timedelta(days=3)).isoformat()
    rec = {
        "id": rid, "order_id": order["id"], "order_number": order.get("order_number", ""),
        "user_id": order.get("user_id"), "items": items, "reason": reason, "reason_code": reason_code,
        "return_code": return_code, "iade_no": iade_no, "gonderi_no": gonderi_no,
        "contract_no": RETURN_CONTRACT_NO,
        "company_address": _company_return_address(warehouse),
        "mng_ref": iade_no, "mng_ok": mng_ok, "cargo_provider_name": cargo_name,
        "barcode_png_b64": png_b64, "status": "created",
        "created_at": now_iso, "valid_until": valid_until,
        "guest": bool(not (actor or {}).get("id")),
        "refund_bank_info": refund_bank_info,  # havale iadesinde IBAN+ad soyad; kartta None
    }
    await db.customer_returns.insert_one({**rec})
    await _log_order_event(order["id"], "return", "İade talebi oluşturuldu", actor,
                           {"return_id": rid, "return_code": return_code},
                           order_number=order.get("order_number", ""))
    await db.orders.update_one({"id": order["id"]}, {"$set": {
        "return_request": {
            "return_id": rid, "return_code": return_code,
            "iade_no": iade_no, "gonderi_no": gonderi_no,
            "valid_until": valid_until, "reason": reason, "reason_code": reason_code,
            "created_at": now_iso, "status": "created", "items_count": len(items),
            "refund_bank_info": refund_bank_info,  # havale iadesinde IBAN+ad soyad
            # Talep iptal edilirse sipariş bu statüye geri döner (müşteri iade-iptal için).
            "prev_status": order.get("status") or "delivered",
        },
        "status": "return_requested", "updated_at": now_iso,
    }})

    import os as _os
    base = _os.environ.get("FRONTEND_PUBLIC_URL") or _os.environ.get("REACT_APP_BACKEND_URL") or ""
    barcode_img = (
        f'<div style="margin:14px 0"><img src="{base}/api/orders/returns/{rid}/barcode.png" '
        f'alt="{return_code}" style="height:90px"/></div>' if base else ""
    )
    _spawn(_notify_return(order, return_code, valid_until, barcode_img, iade_no=iade_no))
    return {"success": True, "return": _public_return(rec)}


@router.post("/{order_id}/return-request")
async def create_return_request(order_id: str, payload: dict, request: Request,
                                current_user: dict = Depends(get_current_user)):
    """Müşteri (üye): teslimden itibaren 14 gün içinde iade talebi oluşturur."""
    if not current_user:
        raise HTTPException(status_code=401, detail="Giriş yapmanız gerekiyor")
    order = await db.orders.find_one({"id": order_id}, {"_id": 0})
    if not order:
        order = await db.orders.find_one({"order_number": order_id}, {"_id": 0})
    if not order:
        raise HTTPException(status_code=404, detail="Sipariş bulunamadı")
    # GÜVENLİK (BOLA): Üyeli siparişte user_id eşleşmeli. MİSAFİR siparişte (user_id boş)
    # eskiden kontrol atlanıp HERHANGİ bir üye iade açabiliyordu → artık üyenin kendi
    # e-posta/telefonu siparişteki iletişimle eşleşmeli.
    if order.get("user_id"):
        if current_user.get("id") and order["user_id"] != current_user["id"]:
            await note_policy_denial(current_user, "order.return_request", order=order, request=request)
            raise HTTPException(status_code=403, detail="Bu sipariş size ait değil")
    else:
        if not _member_owns_guest_order(order, current_user):
            await note_policy_denial(current_user, "order.return_request", order=order, request=request)
            raise HTTPException(status_code=403, detail="Bu sipariş size ait değil")
    return await _build_return_for_order(order, payload, current_user)


def _order_is_efatura(order: dict) -> bool:
    """Sipariş e-Fatura / kurumsal mı? (KATI GP yasağının tek doğruluk kaynağı.)
    invoice_type='e-fatura' VEYA billing_info/billing_address kurumsal işaretliyse
    (is_corporate) YA DA VKN/vergi no doluysa e-Fatura kabul edilir."""
    if not order:
        return False
    if str(order.get("invoice_type") or "").strip().lower() in ("e-fatura", "efatura"):
        return True
    for _b in (order.get("billing_info") or {}, order.get("billing_address") or {}):
        if not isinstance(_b, dict):
            continue
        if bool(_b.get("is_corporate")):
            return True
        # VKN (10 hane) dolu → kurumsal e-Fatura mükellefi
        # KVKK: değer şifreli saklanır → uzunluk/rakam kontrolü için çözülür (yanıta konmaz).
        _vkn = str(_pii_reveal(_b, "tax_number") or _pii_reveal(_b, "vkn") or _pii_reveal(_b, "tax_id") or "").strip()
        if len(_vkn) == 10 and _vkn.isdigit():
            return True
    return False


def _order_contact_matches(order: dict, email: str, phone: str) -> bool:
    """Misafir doğrulaması: verilen e-posta VEYA telefon siparişteki ile eşleşiyor mu?
    Siparişteki e-posta/telefon birden çok yerde tutulabilir (kök, shipping/billing)."""
    def _digits(v):
        return "".join(c for c in str(v or "") if c.isdigit())

    ship = order.get("shipping_address") or {}
    bill = order.get("billing_address") or order.get("billing_info") or {}
    emails = {
        str(order.get("email") or "").strip().lower(),
        str(order.get("customer_email") or "").strip().lower(),
        str(ship.get("email") or "").strip().lower(),
        str(bill.get("email") or "").strip().lower(),
    }
    emails.discard("")
    phones = set()
    for v in (order.get("phone"), order.get("customer_phone"),
              ship.get("phone"), bill.get("phone")):
        d = _digits(v)
        if d:
            phones.add(d[-10:])  # son 10 hane (ülke kodu farklarına dayanıklı)

    e = str(email or "").strip().lower()
    p = _digits(phone)[-10:] if phone else ""
    if e and e in emails:
        return True
    if p and p in phones:
        return True
    return False


def _member_owns_guest_order(order: dict, current_user: dict) -> bool:
    """Üye, MİSAFİR siparişin sahibi mi? GÜVENLİK (BL-010): yalnız DOĞRULANMIŞ e-posta
    eşleşmesi kabul edilir. Telefon /users/me ile doğrulamasız yazılabildiği, e-posta da
    kayıtta doğrulanmadığı için eskiden başkasının misafir siparişine iade (IBAN) açılabiliyordu.
    customer.py::_owner_or_clauses ile aynı kural."""
    if current_user.get("email_verified") is not True:
        return False
    return _order_contact_matches(order, current_user.get("email", ""), "")


_GUEST_VERIFY_MAX_FAILS = 5
_GUEST_VERIFY_WINDOW_S = 3600


async def _guest_verify_or_raise(order: dict, email: str, phone: str) -> None:
    """Misafir iade uçlarında iletişim doğrulaması + sipariş başına deneme kilidi (BL-010).
    by-number ucu telefonun bir kısmını maskeli gösterdiğinden kalan haneler denenemesin:
    1 saat içinde 5 hatalı denemede sipariş için doğrulama kilitlenir."""
    key = str(order.get("id") or order.get("order_number") or "")
    now = datetime.now(timezone.utc)
    since = (now - timedelta(seconds=_GUEST_VERIFY_WINDOW_S)).isoformat()
    rec = await db.guest_verify_attempts.find_one({"key": key}, {"_id": 0})
    if rec and str(rec.get("first_at") or "") >= since and int(rec.get("fails") or 0) >= _GUEST_VERIFY_MAX_FAILS:
        raise HTTPException(status_code=429,
                            detail="Çok fazla hatalı deneme. Lütfen 1 saat sonra tekrar deneyin veya bizimle iletişime geçin.")
    if _order_contact_matches(order, email, phone):
        return
    if rec and str(rec.get("first_at") or "") >= since:
        await db.guest_verify_attempts.update_one({"key": key}, {"$inc": {"fails": 1}, "$set": {"last_at": now.isoformat()}})
    else:
        await db.guest_verify_attempts.update_one(
            {"key": key}, {"$set": {"fails": 1, "first_at": now.isoformat(), "last_at": now.isoformat()}}, upsert=True)
    raise HTTPException(status_code=403, detail="Girdiğiniz bilgiler sipariş kayıtları ile eşleşmiyor.")


@router.post("/by-number/{order_number}/return-request")
@(limiter.limit("5/minute") if limiter else (lambda f: f))
@(limiter.limit("60/minute", key_func=global_rate_key) if limiter else (lambda f: f))
async def create_guest_return_request(order_number: str, payload: dict, request: Request):
    """Misafir (üyeliksiz): sipariş no + e-posta/telefon doğrulaması ile iade talebi.
    14 gün penceresi + 3 gün kod geçerliliği kuralları üye uçla aynıdır."""
    order = await db.orders.find_one({"order_number": order_number}, {"_id": 0})
    if not order:
        order = await db.orders.find_one({"id": order_number}, {"_id": 0})
    if not order:
        raise HTTPException(status_code=404, detail="Sipariş bulunamadı")

    email = (payload.get("email") or "").strip()
    phone = (payload.get("phone") or "").strip()
    if not email and not phone:
        raise HTTPException(status_code=400, detail="Doğrulama için e-posta veya telefon giriniz.")
    await _guest_verify_or_raise(order, email, phone)

    actor = {"id": order.get("user_id"), "email": email or order.get("email") or "",
             "name": "Misafir", "guest": True}
    return await _build_return_for_order(order, payload, actor)


async def _cancel_return_for_order(order: dict, actor: dict) -> dict:
    """Müşteri İADE TALEBİNİ İPTAL — YALNIZ hâlâ BEKLEMEDEYSE (talep oluşturuldu ama HENÜZ
    onaylanmadı / kargo geri gelmedi / iade ödemesi YAPILMADI → customer_returns.status='created').
    PARA/STOK/ÖDEME mantığına ASLA DOKUNMAZ (talep aşamasında iade yapılmadı). Yalnız talebi
    'cancelled' yapar ve siparişi talep-öncesi statüsüne (prev_status) döndürür; payment_status
    değişmez. Onaylanmış/işlenmiş/iade-ödemesi yapılmış talep iptal EDİLEMEZ (400)."""
    rr = order.get("return_request") or {}
    rid = rr.get("return_id")
    rec = None
    if rid:
        rec = await db.customer_returns.find_one({"id": rid}, {"_id": 0})
    if not rec:
        rec = await db.customer_returns.find_one(
            {"order_id": order["id"], "status": "created"}, {"_id": 0})
    rec_st = str((rec or {}).get("status") or "")
    rr_st = str(rr.get("status") or "")
    order_st = str(order.get("status") or "")

    # İptal EDİLEBİLİR mi? YALNIZ bekleyen (created) talep.
    _cancelable = (rec_st == "created") or (
        not rec and rr_st in ("created", "return_requested") and order_st == "return_requested")
    if not _cancelable:
        if rec_st == "cancelled" or rr_st == "cancelled":
            raise HTTPException(status_code=400, detail="İade talebi zaten iptal edilmiş.")
        if rec or order_st.startswith("return_") or order_st in ("returned", "refunded"):
            raise HTTPException(status_code=400,
                                detail="Bu iade talebi işlenmeye başlandı/onaylandı — iptal edilemez.")
        raise HTTPException(status_code=400, detail="İptal edilebilecek bekleyen iade talebi yok.")

    now_iso = datetime.now(timezone.utc).isoformat()
    # 1) İade kaydını iptal et (PARA/STOK YOK).
    if rec:
        await db.customer_returns.update_one(
            {"id": rec["id"]},
            {"$set": {"status": "cancelled", "cancelled_at": now_iso,
                      "cancelled_by": (actor or {}).get("email") or "customer"}})
    # 2) Siparişi talep-öncesi statüsüne döndür — payment_status'e DOKUNMA.
    prev = rr.get("prev_status") or "delivered"
    await db.orders.update_one({"id": order["id"]}, {"$set": {
        "return_request.status": "cancelled", "return_request.cancelled_at": now_iso,
        "status": prev, "updated_at": now_iso,
    }})
    await _log_order_event(order["id"], "return", "İade talebi müşteri tarafından iptal edildi",
                           actor, {"return_id": (rec or {}).get("id"),
                                   "return_code": rr.get("return_code")},
                           order_number=order.get("order_number", ""))
    return {"success": True, "status": prev, "message": "İade talebi iptal edildi."}


@router.post("/{order_id}/return-request/cancel")
async def cancel_return_request(order_id: str, request: Request, current_user: dict = Depends(get_current_user)):
    """Üye müşteri: KENDİ siparişinin BEKLEYEN iade talebini iptal eder."""
    if not current_user:
        raise HTTPException(status_code=401, detail="Giriş yapmanız gerekiyor")
    order = await db.orders.find_one({"id": order_id}, {"_id": 0})
    if not order:
        order = await db.orders.find_one({"order_number": order_id}, {"_id": 0})
    if not order:
        raise HTTPException(status_code=404, detail="Sipariş bulunamadı")
    # SAHİPLİK (BOLA): üyeli siparişte user_id, misafir siparişte iletişim eşleşmeli.
    if order.get("user_id"):
        if current_user.get("id") and order["user_id"] != current_user["id"]:
            await note_policy_denial(current_user, "order.return_cancel", order=order, request=request)
            raise HTTPException(status_code=403, detail="Bu sipariş size ait değil")
    else:
        if not _member_owns_guest_order(order, current_user):
            await note_policy_denial(current_user, "order.return_cancel", order=order, request=request)
            raise HTTPException(status_code=403, detail="Bu sipariş size ait değil")
    return await _cancel_return_for_order(order, current_user)


@router.post("/by-number/{order_number}/return-request/cancel")
@(limiter.limit("5/minute") if limiter else (lambda f: f))
@(limiter.limit("60/minute", key_func=global_rate_key) if limiter else (lambda f: f))
async def cancel_guest_return_request(order_number: str, request: Request, payload: dict = Body(default={})):
    """Misafir müşteri: sipariş no + e-posta/telefon doğrulamasıyla bekleyen iade talebini iptal eder."""
    order = await db.orders.find_one({"order_number": order_number}, {"_id": 0})
    if not order:
        order = await db.orders.find_one({"id": order_number}, {"_id": 0})
    if not order:
        raise HTTPException(status_code=404, detail="Sipariş bulunamadı")
    email = ((payload or {}).get("email") or "").strip()
    phone = ((payload or {}).get("phone") or "").strip()
    if not email and not phone:
        raise HTTPException(status_code=400, detail="Doğrulama için e-posta veya telefon giriniz.")
    await _guest_verify_or_raise(order, email, phone)
    actor = {"id": order.get("user_id"), "email": email or order.get("email") or "",
             "name": "Misafir", "guest": True}
    return await _cancel_return_for_order(order, actor)


@router.post("/{order_id}/admin-return")
async def admin_manual_return(order_id: str, payload: dict = Body(default={}),
                              current_user: dict = Depends(require_permission("orders.edit"))):
    """Admin: sipariş panelinden MANUEL (kısmi) iade.
    - payload.item_indexes: iade edilecek kalem index'leri (boşsa TÜM sipariş).
    - Seçili kalemler customer_returns'e yazılır (gider pusulası bu kayıttan üretilir → kısmi olur).
    - TÜM kalemler seçildiyse sipariş target_status'e (returned/refunded) taşınır.
    - KISMİ ise sipariş AÇIK kalır; kalem bazında iade kaydı order.partial_returns'e işlenir
      ve order.has_partial_return=True olur (eski kayıtlarda 'bir kalem iade edildi' görünür)."""
    order = await db.orders.find_one({"id": order_id}, {"_id": 0})
    if not order:
        order = await db.orders.find_one({"order_number": order_id}, {"_id": 0})
    if not order:
        raise HTTPException(status_code=404, detail="Sipariş bulunamadı")
    # COM10: müşteri RMA'sı ile aynı sipariş-başı kilit (eşzamanlı panel + müşteri iadesi adet aşamaz).
    _tok = await _acquire_order_op_lock(order["id"], "return_req", ttl_s=120)
    if not _tok:
        raise HTTPException(status_code=409, detail=(
            "Bu sipariş için başka bir iade işlemi sürüyor. Lütfen birkaç saniye sonra tekrar deneyin."))
    try:
        order = await db.orders.find_one({"id": order["id"]}, {"_id": 0}) or order
        return await _admin_manual_return_locked(order, payload, current_user)
    finally:
        await _release_order_op_lock(order["id"], "return_req", _tok)


async def _admin_manual_return_locked(order: dict, payload: dict, current_user: dict) -> dict:
    src_items = order.get("items") or []
    sel = payload.get("item_indexes")
    target_status = (payload.get("target_status") or "returned").strip()
    if target_status not in ("returned", "refunded", "return_requested", "return_in_transit"):
        target_status = "returned"

    chosen, chosen_idx = [], []
    if isinstance(sel, list) and sel:
        for idx in sel:
            try:
                i = int(idx)
                chosen.append(src_items[i]); chosen_idx.append(i)
            except Exception:
                continue
    if not chosen:
        chosen = list(src_items); chosen_idx = list(range(len(src_items)))

    items = [{
        "name": it.get("name") or it.get("product_name") or "Ürün",
        "size": it.get("size", ""), "color": it.get("color", ""),
        "quantity": int(it.get("quantity", 1) or 1),
        "price": float(it.get("price") or it.get("unit_price") or 0),
        "unit_price": float(it.get("unit_price") or it.get("price") or 0),
        "discount_amount": float(it.get("discount_amount") or it.get("discount") or 0),
        "product_id": it.get("product_id") or it.get("sku") or it.get("barcode") or "",
    } for it in chosen]
    reason = (payload.get("reason") or "").strip()[:500]
    is_full = (len(src_items) > 0 and len(chosen_idx) >= len(src_items))

    # COM10: iade edilebilir adet tavanı — önceki işlenmiş iadeler (onaylı/yolda/teslim/ödenmiş) VE
    # bekleyen müşteri talepleri (created) düşülür; kalan adedi olmayan kalem iade edilemez.
    _cap_before = await _already_returned_qty(order["id"])
    async for _cr in db.customer_returns.find({"order_id": order["id"], "status": "created"},
                                              {"_id": 0, "items": 1, "approved_items": 1,
                                               "approved_item_indexes": 1}):
        for _ci in _return_record_items(_cr):
            _ck = _return_item_key(_ci)
            try:
                _cap_before[_ck] = _cap_before.get(_ck, 0) + max(0, int(_ci.get("quantity") or 1))
            except Exception:
                _cap_before[_ck] = _cap_before.get(_ck, 0) + 1
    if _cap_before:
        _left = {k: max(0, v - _cap_before.get(k, 0)) for k, v in _order_qty_by_key(order).items()}
        _kept, _kept_idx = [], []
        for _it, _ix in zip(items, chosen_idx):
            _k = _return_item_key(_it)
            _q = min(int(_it.get("quantity") or 1), _left.get(_k, 0))
            if _q > 0:
                _left[_k] = _left.get(_k, 0) - _q
                _kept.append({**_it, "quantity": _q})
                _kept_idx.append(_ix)
        if not _kept:
            raise HTTPException(status_code=400, detail=(
                "Seçilen kalemler için iade edilebilir adet kalmadı (daha önce iade edilmiş ya da bekleyen "
                "bir müşteri iade talebi var)."))
        items, chosen_idx = _kept, _kept_idx
        is_full = is_full and all(_left.get(k, 0) == 0 for k in _left)

    rid = generate_id()
    now = datetime.now(timezone.utc); now_iso = now.isoformat()
    rec = {
        "id": rid, "order_id": order["id"], "order_number": order.get("order_number", ""),
        "user_id": order.get("user_id"), "items": items, "reason": reason,
        "return_code": f"MAN{order.get('order_number','')}{rid[:6]}".replace(" ", ""),
        "cargo_provider_name": order.get("cargo_provider_name", ""),
        "status": "returned" if target_status in ("returned", "refunded") else "created",
        "source": "admin_manual", "is_partial": (not is_full), "item_indexes": chosen_idx,
        "created_at": now_iso, "updated_at": now_iso,
    }
    await db.customer_returns.insert_one({**rec})

    if is_full:
        # Tüm kalemler iade → siparişi iade alanına taşı
        await db.orders.update_one({"id": order["id"]}, {"$set": {
            "status": target_status, "updated_at": now_iso,
            "return_request": {"return_id": rid, "status": "returned",
                               "items_count": len(items), "created_at": now_iso, "partial": False},
        }})
    else:
        # Kısmi iade → sipariş AÇIK kalır; kalem bazında iade kaydını işle
        prev = order.get("partial_returns") or []
        prev.append({"return_id": rid, "item_indexes": chosen_idx,
                     "items_count": len(items), "created_at": now_iso, "reason": reason})
        await db.orders.update_one({"id": order["id"]}, {"$set": {
            "has_partial_return": True, "partial_returns": prev, "updated_at": now_iso,
        }})

    await _log_order_event(order["id"], "return",
                           ("Tüm sipariş iade alındı (manuel)" if is_full else f"Kısmi iade (manuel): {len(items)} kalem"),
                           current_user, {"return_id": rid, "item_indexes": chosen_idx, "partial": (not is_full)},
                           order_number=order.get("order_number", ""))
    return {"success": True, "return_id": rid, "partial": (not is_full), "items_count": len(items), "is_full": is_full}


@router.get("/{order_id}/return")
async def get_return_info(order_id: str, request: Request, current_user: dict = Depends(get_current_user)):
    """Müşteri: bir siparişin iade kaydını getir (ekranda barkod/kod göstermek için)."""
    if not current_user:
        raise HTTPException(status_code=401, detail="Giriş yapmanız gerekiyor")
    oid = order_id
    o = await db.orders.find_one(
        {"$or": [{"id": order_id}, {"order_number": order_id}]},
        {"_id": 0, "id": 1, "user_id": 1, "email": 1, "customer_email": 1, "phone": 1,
         "customer_phone": 1, "shipping_address": 1, "billing_address": 1, "billing_info": 1})
    if o:
        oid = o.get("id", order_id)
        if o.get("user_id"):
            # Üyeye ait sipariş → sahiplik user_id ile.
            if current_user.get("id") and o["user_id"] != current_user["id"]:
                await note_policy_denial(current_user, "order.return_info", order=o, request=request)
                raise HTTPException(status_code=403, detail="Bu sipariş size ait değil")
        else:
            # A2.10: Misafir sipariş (user_id yok) → giriş yapan kullanıcının e-posta/telefonu
            # siparişteki iletişimle EŞLEŞMELİ (BOLA/IDOR koruması, create_return_request ile aynı).
            if not _member_owns_guest_order(o, current_user):
                await note_policy_denial(current_user, "order.return_info", order=o, request=request)
                raise HTTPException(status_code=403, detail="Bu sipariş size ait değil")
    else:
        # T01: sipariş kaydı yoksa sahiplik doğrulanamaz → iade kaydı (barkod/kod) da DÖNMEZ.
        raise HTTPException(status_code=404, detail="İade kaydı bulunamadı")
    rec = await db.customer_returns.find_one({"order_id": oid}, {"_id": 0}, sort=[("created_at", -1)])
    if not rec:
        raise HTTPException(status_code=404, detail="İade kaydı bulunamadı")
    return {"return": _public_return(rec)}


@router.get("/returns/{return_id}/barcode.png")
async def return_barcode_png(return_id: str):
    """İade barkodu PNG (e-posta <img> ve ekran için; opak return_id yetki anahtarı)."""
    rec = await db.customer_returns.find_one({"id": return_id}, {"_id": 0, "barcode_png_b64": 1})
    if not rec or not rec.get("barcode_png_b64"):
        raise HTTPException(status_code=404, detail="Barkod bulunamadı")
    import base64 as _b64
    raw = _b64.b64decode(rec["barcode_png_b64"])
    return Response(content=raw, media_type="image/png", headers={"Cache-Control": "public, max-age=3600"})


# --- Admin: site iade taleplerini listele / durum güncelle ---
_RETURN_STATUS_MAP = {
    "created":     (None,                None),
    # approved/rejected bildirimleri ozel endpoint'lerde (sebep/tutar/barkod
    # degiskenleriyle) gonderilir; generic durum-degistirici burada event ATMAZ.
    "approved":    ("return_approved",   None),
    "in_transit":  ("return_in_transit", "order_return_in_transit"),
    "received":    ("returned",          "order_returned"),
    "returned":    ("returned",          "order_returned"),
    "refunded":    ("refunded",          "order_refunded"),
    "partial_refunded": ("partial_refunded", "order_partial_refunded"),
    "rejected":    ("return_rejected",   None),
    "cancelled":   ("delivered",         None),
}


@router.get("/returns/admin/list")
async def list_returns_admin(
    status: str = Query(""),
    limit: int = Query(100, ge=1, le=500),
    current_user: dict = Depends(require_permission("returns.view")),
):
    q = {}
    if status:
        q["status"] = status
    rows = await db.customer_returns.find(q, {"_id": 0, "barcode_png_b64": 0}).sort("created_at", -1).to_list(length=limit)
    # Kalem bedenlerini toplu zenginleştir (barkod → varyant beden) — iade satırında beden görünsün.
    _bcs = list({str(it.get("barcode") or "").strip()
                 for r in rows for it in (r.get("items") or [])
                 if str(it.get("barcode") or "").strip() and not str(it.get("size") or "").strip()})
    _size_map = {}
    if _bcs:
        async for _p in db.products.find({"variants.barcode": {"$in": _bcs}}, {"_id": 0, "variants": 1}):
            for _v in (_p.get("variants") or []):
                _vbc = str(_v.get("barcode") or "").strip()
                if _vbc and _vbc not in _size_map:
                    _size_map[_vbc] = _v.get("size") or _v.get("beden") or ""
    out = []
    for r in rows:
        o = await db.orders.find_one(
            {"id": r.get("order_id")},
            {"_id": 0, "shipping_address": 1, "status": 1,
             "invoice_type": 1, "billing_info": 1, "billing_address": 1}) or {}
        addr = o.get("shipping_address") or {}
        for it in (r.get("items") or []):
            if not str(it.get("size") or "").strip():
                _sz = _size_map.get(str(it.get("barcode") or "").strip())
                if _sz:
                    it["size"] = _sz
        out.append({
            **r,
            "customer_name": addr.get("full_name") or f"{addr.get('first_name','')} {addr.get('last_name','')}".strip(),
            "customer_email": addr.get("email", ""),
            "customer_phone": addr.get("phone", ""),
            "order_status": o.get("status", ""),
            "is_efatura": _order_is_efatura(o),
            "barcode_url": f"/api/orders/returns/{r.get('id')}/barcode.png",
        })
    await present_refund_bank_info_in(out, current_user)  # BL-040: yetkiye göre tam / maskeli
    _contact_mask_many(out)  # KVKK: iade listesinde müşteri telefonu / açık adres maskeli
    return {"returns": out}


# GÜVENLİK (denetim 2026-10): genel iade durum değiştirici yalnız require_admin'deydi →
# her personel iadeyi 'refunded' (bedeli ödendi) yapabiliyordu. Hedef duruma göre yetki:
_RETURN_STATUS_PERMS = {
    "created": "returns.approve",
    "approved": "returns.approve",
    "in_transit": "returns.approve",
    "received": "returns.approve",
    "returned": "returns.approve",
    "refunded": "returns.refund_pay",
    "partial_refunded": "returns.refund_pay",
    "rejected": "returns.reject",
    "cancelled": "returns.reject",
}


@router.post("/returns/{return_id}/status")
async def update_return_status(return_id: str, payload: dict, request: Request,
                               # IAM-02: sayfa izni yeterli — durum başına asıl izin (returns.approve /
                               # refund_pay …) aşağıda gövdede _RETURN_STATUS_PERMS ile zorunlu.
                               current_user: dict = Depends(require_permission("returns.view"))):
    rec = await db.customer_returns.find_one({"id": return_id}, {"_id": 0})
    if not rec:
        raise HTTPException(status_code=404, detail="İade bulunamadı")
    new = (payload.get("status") or "").strip()
    if new not in _RETURN_STATUS_MAP:
        raise HTTPException(status_code=400, detail=f"Geçersiz durum. Geçerli: {list(_RETURN_STATUS_MAP)}")
    _need_perm = _RETURN_STATUS_PERMS.get(new, "returns.approve")
    _perms_rs = await get_effective_permissions(current_user)
    if "*" not in _perms_rs and _need_perm not in _perms_rs:
        raise HTTPException(status_code=403, detail=f"Bu işlem için yetkiniz yok ({_need_perm})")
    now_iso = datetime.now(timezone.utc).isoformat()
    order_status, event = _RETURN_STATUS_MAP[new]
    if order_status in ("refunded", "partial_refunded"):
        # ACİL ANAHTAR (CHAIN08): "iade ödendi" işaretlemesi → freeze_refunds / restore_mode /
        # geri yükleme sonrası iade kilidi açıkken 423.
        await _guard_switch("freeze_refunds")

    async def _apply():
        await db.customer_returns.update_one({"id": return_id}, {"$set": {"status": new, "updated_at": now_iso}})
        if order_status:
            _oset = {"status": order_status, "return_request.status": new, "updated_at": now_iso}
            if order_status == "return_approved":
                _oset["return_approved_at"] = now_iso
            if order_status in ("refunded", "partial_refunded"):
                _oset["refund_paid_at"] = now_iso
            await db.orders.update_one({"id": rec["order_id"]}, {"$set": _oset})
            # OTOMATİK STOK GERİ (site iadesi): iade tamamlanınca/onaylanınca iade edilen kalemleri
            # ADET kadar stoğa ekle (kısmi dahil). İade-başına idempotent. Eskiden site iadesinde stok
            # HİÇ geri gelmiyordu (Trendyol'da GP kesiminde geliyordu — asimetri kapatıldı).
            if order_status in ("returned", "refunded", "partial_refunded", "return_approved"):
                try:
                    await _restock_return_items_once(rec)
                except Exception as _e:
                    logger.warning(f"[iade] otomatik restock hata order={rec.get('order_id')}: {_e}")
        else:
            await db.orders.update_one({"id": rec["order_id"]},
                {"$set": {"return_request.status": new, "updated_at": now_iso}})

    if order_status in ("refunded", "partial_refunded"):
        # PARA ÇIKIŞI (iade ödendi): FAIL-CLOSED denetim — intent yazılamazsa işlem YAPILMAZ (503);
        # intent → uygula → done/failed (tamper-evident audit zinciri).
        from activity_audit import run_audited
        await run_audited(
            db, action="returns.status_refunded", entity_type="customer_return", entity_id=return_id,
            perform=_apply, before={"status": rec.get("status")}, after={"status": new},
            current_user=current_user, request=request, source="admin.returns",
            metadata={"order_id": rec.get("order_id"), "order_number": rec.get("order_number", ""),
                      "return_id": return_id, "new_status": new})
    else:
        await _apply()

    if event and order_status:
        import asyncio as _aio
        async def _n():
            try:
                import sys, os
                sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
                from notification_service import send_notification
                from order_statuses import get_status_config, customer_label_for
                cfg = await get_status_config(db)
                nz = (cfg.get("notify") or {}).get(order_status) or {}
                ch = [c for c in ("sms", "email") if nz.get(c)]
                if not ch:
                    return
                o = await db.orders.find_one({"id": rec["order_id"]}, {"_id": 0})
                addr = (o or {}).get("shipping_address") or {}
                await send_notification(
                    db, event,
                    to_phone=addr.get("phone") or (o or {}).get("phone"),
                    to_email=addr.get("email") or (o or {}).get("email"),
                    variables=await _order_notify_vars(o or {}, order_number=rec.get("order_number", ""), return_code=rec.get("return_code", ""), status_label=customer_label_for(order_status)),
                    channels=ch,
                )
            except Exception as e:
                logger.warning(f"return status notif failed: {e}")
        _spawn(_n())
    return {"success": True, "status": new}


# ============================================================================
# Madde 3 — İade tutarı hesabı + Onay endpoint'i (P3)
# ============================================================================

async def _resolve_shipping_cost(cart_total: float):
    """db.shipping_rules'tan verilen sepet tutarına uygulanacak kargo bedelini döndürür.
    free_shipping kuralı -> 0.0 ; eşleşen kural yoksa None (bilgi yok)."""
    try:
        rules = await db.shipping_rules.find({"is_active": True}, {"_id": 0}).sort("min_cart", -1).to_list(100)
    except Exception:
        return None
    for r in rules:
        mn = r.get("min_cart", 0) or 0
        mx = r.get("max_cart") or None
        if cart_total >= mn and (not mx or cart_total <= mx):
            return 0.0 if r.get("free_shipping") else float(r.get("shipping_cost", 0) or 0)
    return None


def _resolve_return_selection(all_items, sel_items, sel_idx):
    """Kısmi iade kalem seçimini rec.items index listesine çevirir.
    Öncelik: sel_items kalem KİMLİKLERİ [{barcode,name,size,color}] — index kaymasına
    dayanıklı (barcode↔product_id öncelikli, yoksa ad+beden+renk). Geriye uyum: sel_idx
    index listesi. Seçim GÖNDERİLDİYSE ama hiçbir kalem eşleşmediyse ValueError (sessizce
    tam iadeye düşülmez). Seçim hiç gönderilmediyse [] döner (= tam sipariş)."""
    def _inorm(v):
        return " ".join(str(v or "").strip().lower().split())
    _sel = []
    # 1) ÖNCE kimlik (barcode/ad+beden+renk) — index kaymasına dayanıklı.
    if isinstance(sel_items, list) and sel_items:
        for want in sel_items:
            if not isinstance(want, dict):
                continue
            w_bar = _inorm(want.get("barcode") or want.get("product_id"))
            w_key = (_inorm(want.get("name")), _inorm(want.get("size")), _inorm(want.get("color")))
            for i, it in enumerate(all_items):
                if i in _sel:
                    continue
                it_bar = _inorm(it.get("product_id") or it.get("barcode"))
                if w_bar and it_bar and w_bar == it_bar:
                    _sel.append(i)
                    break
                it_key = (_inorm(it.get("name")), _inorm(it.get("size")), _inorm(it.get("color")))
                if w_key[0] and w_key == it_key:
                    _sel.append(i)
                    break
    # 2) Kimlik TUTMADIYSA index'e düş (sel_items ile sel_idx birlikte gönderilir; kimlik
    #    drift'inde SESSİZCE tam iadeye düşmek yerine index seçimini kullan → kısmi korunur).
    if not _sel and isinstance(sel_idx, list) and sel_idx:
        for _i in sel_idx:
            try:
                _i = int(_i)
            except Exception:
                continue
            if 0 <= _i < len(all_items):
                _sel.append(_i)
    # 3) Bir seçim İSTENDİ ama hiçbir şey çözülemediyse hata ver (sessizce tam iadeye düşme).
    if (sel_items or sel_idx) and not _sel:
        raise ValueError("Seçilen kalemler iade kaydında bulunamadı — sayfayı yenileyip tekrar deneyin")
    return sorted(set(_sel))


def _round2(x):
    try:
        return round(float(x or 0) + 1e-9, 2)
    except Exception:
        return 0.0


async def _storefront_free_shipping():
    """Vitrinin kullandığı ücretsiz-kargo EŞİĞİ ve kargo ÜCRETİNİ döndürür.
    /api/settings (settings.py) ile BİREBİR aynı kaynak — böylece iade hesabı
    müşterinin checkout'ta gördüğü kargo mantığıyla tutarlı olur:
      - eşik = aktif 'otomatik' free_shipping kuponlarının en düşük min_cart_total'ı
      - ücret = default kargo firması bedeli (yoksa settings.shipping_fee)
    Değerler panelden değişebilir; sabit yazılmaz. (threshold, fee) döner."""
    s = await db.settings.find_one({"id": "main"}, {"_id": 0}) or {}
    cargo_fees = s.get("cargo_fees") or {}
    default_company = s.get("default_cargo_company") or ""
    fee = None
    if default_company and isinstance(cargo_fees, dict) and cargo_fees.get(default_company) not in (None, ""):
        try:
            fee = float(cargo_fees.get(default_company))
        except Exception:
            fee = None
    if fee is None:
        try:
            fee = float(s.get("shipping_fee"))
        except Exception:
            fee = 0.0
    fee = _round2(fee or 0.0)
    threshold = None
    try:
        from shipping_rules import campaign_live
        async for _c in db.coupons.find(
            {"is_active": True, "free_shipping": True, "auto_apply": True},
            {"_id": 0, "min_cart_total": 1, "start_at": 1, "end_at": 1},
        ):
            if not campaign_live(_c):   # süresi dolmuş kampanya eşiği düşürmez
                continue
            mc = _c.get("min_cart_total")
            if mc in (None, ""):
                continue
            mc = float(mc)
            if threshold is None or mc < threshold:
                threshold = mc
    except Exception:
        threshold = None
    return threshold, fee


def _goods_kdv_rate(line_items: list, default_rate: float = 10.0) -> float:
    """Vade farkı satırının KDV oranı = MALIN oranı (KDVK 24/c: matraha dahil unsur,
    teslimin tabi olduğu oranla vergilendirilir). Eskiden sabit 20.0 yazılıyordu; %10
    KDV'li tekstil siparişinde taksit başına fazla KDV beyan ediliyordu (169,98 TL vade
    farkında 28,33 yerine 15,45 → 12,88 TL fazla). En yüksek tutarlı ÜRÜN satırının
    oranı esas alınır; hizmet satırları (kargo/hediye paketi/kapıda ödeme) sayılmaz.
    """
    _best, _best_amt = None, -1.0
    for li in (line_items or []):
        if str(li.get("sku") or "") in ("VADEFARKI", "HEDIYEPAKETI", "KAPIDAODEME", "KARGO"):
            continue
        try:
            _amt = float(li.get("unit_price") or 0) * int(li.get("qty") or 1)
            _r = float(li.get("kdv_rate"))
        except Exception:
            continue
        if _r > 0 and _amt > _best_amt:
            _best, _best_amt = _r, _amt
    return float(_best if _best is not None else default_rate)


def _apply_invoice_discounts(order: dict, line_items: list) -> dict:
    """FATURA SATIRLARI — TEK KAYNAK (muhasebe mutabakatı: panel = iyzico = fatura).

    Eski formül: indirim = (ürün brüt + kargo) − order.total. order.total ise hem hediye
    paketi/kapıda ödeme bedelini İÇERİYOR hem de hediye çeki/puanı DÜŞÜYOR. Üç sonuç:
      1) HEDİYE PAKETİ ve KAPIDA ÖDEME bedeli faturada hiç yer almıyor, üstelik total'in
         içinde oldukları için "indirim"i küçültüp ürün matrahını şişiriyor/aşındırıyordu.
         Artık %20 KDV'li AYRI hizmet satırı olurlar (ürünün %10'uyla faturalanmazlar).
      2) İndirim ürünlere DÜZ-ORANSAL dağıtılıyordu; panel/iyzico/iade ise kampanya
         KAPSAMINA göre DONMUŞ kalem indirimini kullanıyor → aynı sipariş için fatura
         satırı ile iade satırı farklı çıkıyordu. Artık donmuş kalem indirimi esas.

    DEĞİŞMEZ: fatura satır toplamı (+kargo +vade farkı) = TAHSİL EDİLEN tutar = iyzico
    paidPrice = paneldeki tutar. Hediye çeki ve puan, mağazanın BEDELSİZ verdiği araçlar
    olduğundan iskonto sayılmaya DEVAM eder (avans/ön ödeme sayılsaydı tahsil edilmeyen
    para üzerinden KDV beyan edilirdi). Bu bir vergi tercihidir; mali müşavir "avans"
    isterse tek yer burasıdır: _billable'a + _gc + _pts eklemek yeter.
    Pazaryeri siparişlerinde davranış birebir korunur (indirim total'dan türer; hediye
    çeki/puan/hediye paketi alanları boştur → düzeltmeler 0'dır).

    line_items YERİNDE güncellenir; eklenecek hizmet satırları döndürülür.
    """
    def _f(v):
        try:
            return round(float(v or 0), 2)
        except Exception:
            return 0.0

    _gross = round(sum(_f(li.get("unit_price")) * int(li.get("qty") or 1) for li in line_items), 2)
    _ship = _f(order.get("shipping_cost"))
    _gw = _f(order.get("gift_wrap_price"))
    _cod = _f(order.get("cod_fee"))
    _gc = _f((order.get("gift_card") or {}).get("amount"))
    _pts = _f(order.get("points_used"))

    # Faturalanacak ürün+kargo bedeli: hizmet bedelleri ÇIKARILIR (kendi satırlarında
    # faturalanırlar, ürünün KDV oranıyla değil %20 ile).
    _total = _f(order.get("total"))
    _billable = round((_total if _total > 0.005 else (_gross + _ship)) - _gw - _cod, 2)
    _disc = round((_gross + _ship) - _billable, 2)
    if _disc < 0.01:
        # Yedek: indirim = kupon/kampanya + havale/EFT indirimi.
        _disc = round(_f(order.get("discount")) + _f(order.get("payment_discount")), 2)
    _disc = max(0.0, min(_disc, _gross))

    # Dağıtım: donmuş kalem indirimi (kapsam-farkındalıklı) varsa onu kullan; kalem-dışı
    # kalan (puan/havale payı gibi) oransal yayılır, yuvarlama kalanı SON satıra biner.
    _items = order.get("items") or []
    _line_gross = [round(_f(li.get("unit_price")) * int(li.get("qty") or 1), 2) for li in line_items]
    _frozen = (bool(_items) and len(_items) == len(line_items)
               and all(isinstance(it, dict) and it.get("discount_amount") is not None for it in _items))
    _base = [0.0] * len(line_items)
    if _frozen:
        _base = [max(0.0, min(_line_gross[k], _f(_items[k].get("discount_amount"))))
                 for k in range(len(line_items))]
    _rest = max(0.0, round(_disc - round(sum(_base), 2), 2))
    _alloc = 0.0
    if _disc > 0.005 and _gross > 0.005:
        for _k, li in enumerate(line_items):
            _g = _line_gross[_k]
            if _k < len(line_items) - 1:
                _d = round(_base[_k] + (_rest * _g / _gross), 2)
            else:
                _d = round(_disc - _alloc, 2)          # yuvarlama kalanı son satıra
            _d = max(0.0, min(_d, _g))
            _alloc = round(_alloc + _d, 2)
            _q = int(li.get("qty") or 1) or 1
            li["unit_price"] = round((_g - _d) / _q, 4)

    # Hizmet satırları (kargo HARİÇ — çağıran tarafta ayrıca ekleniyor). KDV %20.
    _extra_lines = []
    if _gw > 0.005:
        _extra_lines.append({"name": "Hediye Paketi", "qty": 1, "unit_price": _gw,
                             "kdv_rate": 20.0, "sku": "HEDIYEPAKETI", "barcode": "", "note": ""})
    if _cod > 0.005:
        _extra_lines.append({"name": "Kapıda Ödeme Hizmet Bedeli", "qty": 1, "unit_price": _cod,
                             "kdv_rate": 20.0, "sku": "KAPIDAODEME", "barcode": "", "note": ""})
    return {"discount": _disc, "allocated": _alloc, "gross": _gross, "billable": _billable,
            "frozen": _frozen, "gift_card": _gc, "points": _pts, "extra_lines": _extra_lines}


async def _order_item_discounts(order: dict) -> list:
    """Sipariş kalemlerine indirimi KAMPANYA KAPSAMINA GÖRE dağıtır (düz-oransal DEĞİL).
    Her applied_promotion'ın indirimi yalnız KAPSAMINDAKİ (kategori/ürün; indirimli-hariç, hariç-liste)
    kalemlere, o kalemlerin brüt payına göre bölüştürülür → kapsam-DIŞI kalem 0 indirim alır.
    W11214 hatası: 'Lansman %20' yalnız kategori 2867'ye uygulanmışken, iade motoru toplam indirimi
    TÜM kalemlere düz-oransal dağıtıp kapsam-dışı ürüne (Kahverengi Etek) hayali indirim yazıyordu.
    Dönüş: order['items'] ile hizalı kalem-başı indirim listesi. applied_promotions yoksa düz-oransal
    (geriye uyum). Kapsam çözülemezse (kupon silinmiş) o promo düz-oransal dağıtılır (güvenli yedek)."""
    items = order.get("items") or []
    n = len(items)
    gross = [_round2((it.get("price") or 0)) * int(it.get("quantity") or 1) for it in items]
    # DONMUŞ per-ürün indirim (checkout'ta item.discount_amount yazıldıysa) → TEK doğru kaynak
    # odur (kategori sonradan değişse de sabit kalır). Yeni siparişler bu daldan geçer.
    if items and all(isinstance(it, dict) and ("discount_amount" in it) for it in items):
        return [_round2((it.get("discount_amount") or 0)) for it in items]
    order_disc = _round2((order.get("discount") or 0) + (order.get("payment_discount") or 0))
    promos = order.get("applied_promotions") or []
    _sub = _round2(order.get("subtotal") or sum(gross))

    def _settle(vals: list, target: float) -> list:
        """Kuruş mutabakatı: yuvarlanmış dağıtım toplamı hedefle BİREBİR eşitlenir; kalan
        EN BÜYÜK paylı kaleme yazılır. Eskiden her kalem ayrı yuvarlanıp kalan hiçbir yere
        yazılmıyordu → Σ item.discount_amount ≠ order.discount (3×33,33 TL / %10'da 9,99 vs
        10,00). Fark iade tarafında fazla iade, iyzico tarafında son kaleme biniyordu; üstelik
        set-item-discounts uyarı eşiği (0,02) 1 kuruşu hiç raporlamıyordu."""
        out = [_round2(v) for v in vals]
        _t = _round2(target)
        _d = _round2(_t - _round2(sum(out)))
        if abs(_d) >= 0.01 and out:
            _k = max(range(len(out)), key=lambda i: (out[i], gross[i]))
            out[_k] = _round2(max(0.0, out[_k] + _d))
        return out

    def _flat():
        dr = min(1.0, order_disc / _sub) if _sub > 0.005 else 0.0
        return _settle([g * dr for g in gross], min(order_disc, _round2(sum(gross) * dr)))

    if order_disc <= 0.005 or n == 0:
        return [0.0] * n
    if not promos:
        return _flat()

    # Kalem kategori üyeliklerini (category_ids, atalar dahil) + indirimli-fiyat bayrağını çöz.
    from .coupons import _item_in_scope
    pids = [str(it.get("product_id") or "") for it in items if it.get("product_id")]
    cat_map = {}
    if pids:
        async for p in db.products.find({"id": {"$in": pids}},
                                        {"_id": 0, "id": 1, "category_ids": 1, "category_id": 1,
                                         "price": 1, "sale_price": 1}):
            cids = set(str(c) for c in (p.get("category_ids") or []) if c)
            if p.get("category_id"):
                cids.add(str(p["category_id"]))
            _lp = float(p.get("price") or 0)
            _sp = float(p.get("sale_price") or 0)
            cat_map[p["id"]] = {"cids": cids, "manual_sale": bool(_sp > 0 and _sp < _lp)}
    enr = []
    for it in items:
        info = cat_map.get(str(it.get("product_id") or ""), {})
        enr.append({**it, "category_ids": list(info.get("cids", set())),
                    "_has_manual_sale": info.get("manual_sale", False)})

    disc = [0.0] * n
    for pr in promos:
        amt = _round2(pr.get("discount") or 0)
        if amt <= 0.005:
            continue
        cid = pr.get("coupon_id")
        cpn = await db.coupons.find_one({"id": cid}, {"_id": 0}) if cid else None
        allowed_cats = set(str(x) for x in ((cpn or {}).get("categories") or []) if x)
        allowed_pids = set(str(x) for x in ((cpn or {}).get("products") or []) if x)
        excluded = set(str(x) for x in ((cpn or {}).get("excluded_products") or []) if x)
        skip_disc = (cpn or {}).get("skip_discounted", True)
        idxs = []
        for i, it in enumerate(enr):
            in_scope = ((not allowed_cats and not allowed_pids)
                        or _item_in_scope(it, allowed_cats, allowed_pids, excluded))
            if in_scope and not (skip_disc and it.get("_has_manual_sale")):
                idxs.append(i)
        base = sum(gross[i] for i in idxs)
        if base <= 0.005:  # kapsam çözülemedi → bu promo'yu düz-oransal (güvenli yedek)
            _tot = sum(gross) or 1.0
            for i in range(n):
                disc[i] += amt * (gross[i] / _tot)
            continue
        for i in idxs:
            disc[i] += amt * (gross[i] / base)
    # Ödeme (havale/EFT) indirimi sepet-geneli teşvik → tüm kalemlere brüt payıyla oransal.
    pm = _round2(order.get("payment_discount") or 0)
    if pm > 0.005:
        _tot = sum(gross) or 1.0
        for i in range(n):
            disc[i] += pm * (gross[i] / _tot)
    return _settle(disc, min(order_disc, _round2(sum(gross))))


@router.post("/{order_id}/set-item-discounts")
async def set_item_discounts(order_id: str, payload: dict,
                             current_user: dict = Depends(require_permission("returns.expense_note"))):
    """ESKİ sipariş düzeltme: kalem-başı indirimi (item.discount_amount) ELLE dondur. Kampanya kapsamı
    sonradan değişen siparişlerde (W11214: Etek kapsam-dışıydı, 0 indirim almıştı ama iade motoru düz-
    oransal 168,42 yazıyordu) doğru per-ürün indirim yazılır. payload: {discounts:[...]} order.items ile
    hizalı (her kalem için indirim tutarı). customer_returns.items barkod/index ile senkronlanır; varsa
    gider pusulası yeni tutarla yeniden hesaplanır. Toplam, siparişin discount+payment_discount'una eşit
    olmalıdır (uyarı verilir, engellenmez)."""
    order = (await db.orders.find_one({"id": order_id}, {"_id": 0})
             or await db.orders.find_one({"order_number": order_id}, {"_id": 0}))
    if not order:
        raise HTTPException(status_code=404, detail="Sipariş bulunamadı")
    ds = payload.get("discounts") or []
    items = order.get("items") or []
    if len(ds) != len(items):
        raise HTTPException(status_code=400, detail=f"discounts uzunluğu ({len(ds)}) kalem sayısıyla ({len(items)}) eşleşmeli.")
    for i, it in enumerate(items):
        if isinstance(it, dict):
            it["discount_amount"] = _round2(ds[i] or 0)
    await db.orders.update_one({"id": order["id"]}, {"$set": {"items": items}})
    _sum = _round2(sum(_round2(x or 0) for x in ds))
    _exp = _round2((order.get("discount") or 0) + (order.get("payment_discount") or 0))
    _warn = "" if abs(_sum - _exp) < 0.02 else f"UYARI: indirim toplamı {_sum} ≠ sipariş indirimi {_exp}"

    # customer_returns senkron: barkod eşleşmesi (yoksa index) ile discount_amount kopyala.
    _by_bc = {}
    for it in items:
        bc = str((it or {}).get("product_id") or "")
        if bc:
            _by_bc[bc] = _round2((it or {}).get("discount_amount") or 0)
    _synced = 0
    async for cr in db.customer_returns.find({"order_id": order["id"]}, {"_id": 0, "id": 1, "items": 1}):
        _cri = cr.get("items") or []
        for j, cit in enumerate(_cri):
            if not isinstance(cit, dict):
                continue
            bc = str(cit.get("product_id") or cit.get("barcode") or "")
            if bc and bc in _by_bc:
                cit["discount_amount"] = _by_bc[bc]
            elif j < len(items) and isinstance(items[j], dict):
                cit["discount_amount"] = _round2(items[j].get("discount_amount") or 0)
        await db.customer_returns.update_one({"id": cr["id"]}, {"$set": {"items": _cri}})
        _synced += 1
        # Gider pusulası varsa yeni per-ürün indirimle yeniden hesapla (idempotent).
        try:
            _gp = await db.gider_pusulasi.find_one({"return_id": cr["id"]}, {"_id": 0, "display_number": 1})
            if _gp:
                await site_return_gider_pusulasi(cr["id"], payload={"tracking_no": _gp.get("display_number") or ""},
                                                 current_user=current_user)
        except Exception:
            pass
    return {"success": True, "order": order.get("order_number"),
            "discounts": [it.get("discount_amount") for it in items],
            "returns_synced": _synced, "warning": _warn}


async def _compute_refund_breakdown(rec: dict, order: dict, fault: str,
                                    return_cargo_fee_override=None, returned_net_override=None):
    """İade tutarını otomatik hesaplar, şeffaf bir döküm döndürür (Karar #2 + #3).

    fault: 'customer' (müşteri kusuru -> kargo bedeli müşteriden tahsil / iadeden düşülür)
           'store'    (mağaza kusuru  -> kargo bedeli mağazadan; HİÇBİR kargo düşülmez)
    returned_net_override: kısmi iadede (panelde seçili kalemler) iade edilen net tutar.
    """
    fault = (fault or "store").lower()
    items = rec.get("items") or []

    # Müşterinin GERÇEKTE ÖDEDİĞİ tutar = baz (taksitliyse vade farkı dahil paidPrice; değilse
    # order.total). ESKİ HATA: ürün BRÜT toplamı (Σ price×qty) baz alınıp kupon indirimi yok
    # sayılıyordu → tam iadede yanlış tutar (örn. 1990−90=1900; doğrusu 1881 / kusur müşteride 1791).
    _vf, _charged, _inst = _order_vade_farki(order)
    base_total = _round2(order.get("total") or 0)
    paid_total = _charged if _charged > base_total + 0.01 else base_total

    if returned_net_override not in (None, ""):
        # KISMİ İADE → panelde seçili kalemlerin net toplamı (ürün tutarı).
        returned_net_products = _round2(returned_net_override)
        orig_cart = _round2(order.get("subtotal") or order.get("total") or 0)
        # ÜCRETSİZ-KARGO EŞİĞİ TABANI (brüt/net karışıklığı düzeltmesi): vitrin eşiği NET
        # sepete bakar (shipping_rules.shipping_quote basis = subtotal − indirim − ödeme
        # indirimi − puan; sipariş anında order.shipping_eligibility_basis'e yazılır).
        # Eskiden BRÜT subtotal ile NET iade tutarı karşılaştırılıyordu → "kalan" daima
        # indirim kadar ŞİŞİK çıkıyor, 99 TL kargo mahsubu yanlış yönde tetikleniyordu:
        #   • kalem kalem TAM iadede bile kalan>0 görünüp 99 TL kesiliyordu (müşteri aleyhine),
        #   • gerçekte eşiğin altına düşen kısmi iadelerde ise kesilmiyordu (mağaza aleyhine).
        _thr_orig = order.get("shipping_eligibility_basis")
        try:
            _thr_orig = _round2(_thr_orig) if _thr_orig not in (None, "") else 0.0
        except Exception:
            _thr_orig = 0.0
        if _thr_orig <= 0.01:
            _thr_orig = _round2(max(0.0, orig_cart
                                    - _round2(order.get("discount") or 0)
                                    - _round2(order.get("payment_discount") or 0)
                                    - _round2(order.get("points_used") or 0)))
        kept_cart = _round2(max(0.0, _thr_orig - returned_net_products))
        is_partial = kept_cart > 0.01
        # VADE FARKI (kullanıcı isteği): müşteri taksitli ödediyse iade edilen ürünlerin
        # vade farkı PAYINI da geri almalı (yalnız ürün tutarı değil). Toplam vade farkını
        # (_vf) iade edilen ürün oranıyla orantılı dağıt → iade tutarına EKLE. Tam iadede
        # zaten paidPrice (vade farkı dahil) baz alındığından burada değil, yalnız kısmide eklenir.
        vade_farki_refunded = 0.0
        if _vf > 0.005:
            # ORANTI TABANI DÜZELTMESİ (kritik): pay ile payda AYNI tabanda olmalı.
            # returned_net_products = seçili kalemlerin İNDİRİMLİ (net) toplamı. Payda eskiden
            # order.subtotal (İNDİRİMSİZ brüt) idi → vade farkı payı olduğundan AZ hesaplanıyor,
            # müşteriye eksik iade çıkıyordu.
            #   W10727: 169,98 × (1935,90 / 5680,00) = 57,93  ✗   (panel 71,52 gösteriyordu)
            #   doğrusu: 169,98 × (1935,90 / 4600,80) = 71,52  ✓
            # Ayrıca payda = TÜM kalemlerin net toplamı olduğundan, sipariş kalem kalem tamamen
            # iade edilirse dağıtılan vade farkı toplamı _vf'ye BİREBİR eşitlenir (kayıp/fazla yok).
            # iyzico İLE BİREBİR (W11676): iyzico vade farkını sepetteki TÜM kalemlere — kargo
            # dahil — orantılı dağıtır (kalem paidPrice = kalem fiyatı × paidPrice/total). Taban bu
            # yüzden order.total (ürün neti + kargo + hizmet bedelleri). Eskiden taban yalnız ürün
            # netiydi → kargonun payı ürünlere biniyor, panel 1.008,72 derken iyzico'daki kalem
            # 1.004,50 kalıyor ve o satırdan panel tutarı iade edilemiyordu. Kargo iadesinde kargonun
            # kendi payı (_shipping_with_vade) ayrıca eklenir; kalem kalem tam iadede toplam yine _vf.
            _net_base = base_total
            if _net_base <= 0.005:      # eski/pazaryeri siparişi: total yoksa ürün netine düş
                _net_base = _round2(max(0.0, _round2(order.get("subtotal") or 0)
                                        - _round2(order.get("discount") or 0)
                                        - _round2(order.get("payment_discount") or 0))) or orig_cart
            if _net_base > 0.005:
                vade_farki_refunded = _round2(_vf * (returned_net_products / _net_base))
        returned_net = _round2(returned_net_products + vade_farki_refunded)
    else:
        # TAM İADE → müşteriye ödediği tutar iade edilir (ürün + ödenmiş kargo AYRI kalemler;
        # kusur müşterideyse kargo düşülür). Kargo AYRI gösterilir (Kadir: 'kargoyu ayrı göster').
        returned_net = paid_total if paid_total > 0 else _round2(
            sum(_round2(it.get("price", 0)) * int(it.get("quantity", 1) or 1) for it in items))
        orig_cart = returned_net
        _thr_orig = orig_cart
        kept_cart = 0.0
        is_partial = False  # tam iadede kalan 0 → kampanya (ücretsiz-kargo) mahsubu uygulanmaz
        # Tam iadede vade farkının TAMAMI zaten returned_net (paidPrice) içinde iade edilir.
        vade_farki_refunded = round(_vf, 2)

    # --- Ücretsiz-kargo iptali (Karar #2): kısmi iade sonrası KALAN sepet, vitrindeki
    #     ücretsiz-kargo eşiğinin altına düşerse — ve SADECE müşteri kusurunda —
    #     vitrindeki kargo ücreti müşteriden tahsil edilir (iadeden düşülür).
    #     Eşik + ücret vitrinle aynı kaynaktan (settings) okunur, sabit değildir. ---
    campaign_deduction = 0.0
    campaign_note = ""
    _thr_base = _thr_orig
    free_threshold, ship_fee = await _storefront_free_shipping()
    if (fault == "customer" and is_partial and free_threshold is not None
            and ship_fee > 0 and _thr_base >= free_threshold and kept_cart < free_threshold):
        campaign_deduction = _round2(ship_fee)
        campaign_note = (
            f"İade sonrası kalan tutar ({kept_cart:.2f} TL) ücretsiz kargo eşiğinin "
            f"({free_threshold:.0f} TL) altına düştü; kargo bedeli ({campaign_deduction:.2f} TL) "
            f"müşteriden tahsil edildi (iadeden düşüldü)."
        )

    # --- İade kargo bedeli (Karar #3 = kusur seçimine bağlı) ---
    if return_cargo_fee_override is not None:
        return_cargo_fee = _round2(return_cargo_fee_override)
    elif fault == "customer":
        sugg = order.get("shipping_cost")
        if not sugg:
            sugg = await _resolve_shipping_cost(orig_cart) or 0
        return_cargo_fee = _round2(sugg)
    else:
        return_cargo_fee = 0.0

    # Çifte kargo tahsilatını önle: ücretsiz-kargo iptali zaten kargo bedelini
    # düşüyorsa, ayrıca iade kargo bedeli EKLEME (ikisi de aynı kargo ücretidir).
    if campaign_deduction > 0 and return_cargo_fee > 0:
        return_cargo_fee = 0.0

    # ── NAKİT TAVANI (para kaçağı düzeltmesi) ────────────────────────────────────
    # Sipariş bedelinin bir kısmı PUAN veya HEDİYE ÇEKİ ile ödendiyse o kısım NAKİT
    # iade edilemez: puan/çek iptalde zaten idempotent biçimde hesaba geri yüklenir
    # (refund_points_once / refund_gift_card_once). Kısmi iade dalında bu dikkate
    # alınmıyordu → kalem tutarının tamamı nakit iade ediliyor, müşteri hem nakdi hem
    # puanı geri alıyordu. Ölçülen sapma: 2.900 TL ürün neti / 2.650 TL tahsilat olan
    # siparişte 2.700 TL nakit iade (50 TL fazla).
    # Yöntem: müşterinin ödediği toplam bedel = nakit (order.total) + nakit-dışı
    # (puan + hediye çeki). İade edilen tutar bu orana göre nakit/nakit-dışı diye
    # ayrılır; nakit kısım iade edilir, nakit-dışı kısım breakdown'da raporlanır.
    _non_cash = 0.0
    try:
        _non_cash = _round2(float(order.get("points_used") or 0)
                            + float((order.get("gift_card") or {}).get("amount") or 0))
    except Exception:
        _non_cash = 0.0
    non_cash_refund = 0.0
    if _non_cash > 0.005 and is_partial:
        _consideration = _round2(base_total + _non_cash)
        if _consideration > 0.005:
            _cash_ratio = max(0.0, min(1.0, base_total / _consideration))
            _before = returned_net
            returned_net = _round2(returned_net * _cash_ratio)
            non_cash_refund = _round2(max(0.0, _before - returned_net))

    auto_refund = _round2(max(0.0, returned_net - campaign_deduction - return_cargo_fee))

    # SON EMNİYET: hiçbir iade, o sipariş için GERÇEKTEN tahsil edilen nakdi aşamaz
    # (önceki kısmi iadeler düşülerek). Aşıyorsa tavana çekilir.
    try:
        # DÜZELTME (denetim 2026-10): yalnız 'done' sayılıyordu → sonucu BEKLENEN ('pending') ya da
        # BELİRSİZ ('unknown' — para gitmiş olabilir) iadeler düşülmüyor, tavan şişiyordu.
        _already = _round2(sum(float((x or {}).get("amount") or (x or {}).get("net_refund") or 0)
                               for x in (order.get("refunds") or [])
                               if str((x or {}).get("status") or "") in _REFUND_COUNTED_STATUSES))
    except Exception:
        _already = 0.0
    _cash_cap = _round2(max(0.0, paid_total - _already))
    if auto_refund > _cash_cap + 0.01:
        auto_refund = _cash_cap

    return {
        "returned_net": returned_net,
        "orig_cart": orig_cart,
        "kept_cart": kept_cart,
        "threshold_basis": _thr_base,
        "is_partial": is_partial,
        "fault": fault,
        "free_shipping_threshold": free_threshold,
        "shipping_fee": ship_fee,
        "campaign_deduction": campaign_deduction,
        "campaign_note": campaign_note,
        "return_cargo_fee": return_cargo_fee,
        "auto_refund": auto_refund,
        "vade_farki": round(_vf, 2),
        "vade_farki_refunded": round(vade_farki_refunded, 2),  # bu iadede geri verilen vade farkı payı
        "installment": int(_inst or 1),
        "returned_products_net": _round2(returned_net_override) if returned_net_override not in (None, "") else returned_net,
        # Puan/hediye çeki ile ödenmiş kısmın iadesi: NAKİT değil, bakiye olarak döner
        # (iptal kancaları idempotent iade eder). Panelde bilgi amaçlı gösterilir.
        "non_cash_refund": non_cash_refund,
        "cash_cap": _cash_cap,
    }


async def _order_return_breakdown(order: dict):
    """Sipariş DETAYI için: hangi kalem İADE EDİLDİ (kaç adet, ne tutar) / hangi kalem
    MÜŞTERİDE KALDI (kaç adet, ne tutar) — ayrı ayrı. Kaynak: customer_returns kayıtları
    (onaylı iade kalemleri approved_items öncelikli, yoksa items). Adetler sipariş
    kalemleriyle barkod → ad+beden+renk sırasıyla eşleştirilir. Hiç iade yoksa None döner
    (get_order bu alanı yalnız iade olan siparişlerde ekler).

    Tutarlar NET birim fiyat (item.price, kalem indirimi düşülmüş) üzerinden hesaplanır;
    vade farkı/kargo dahil edilmez (onlar refund_amount'ta zaten görünür). refund_amount =
    onaylı iadelerde müşteriye geri ödenen toplam (varsa)."""
    oid = order.get("id")
    order_items = order.get("items") or []
    if not oid or not order_items:
        return None

    def _inorm(v):
        return " ".join(str(v or "").strip().lower().split())

    per_item = []
    for it in order_items:
        q = int(it.get("quantity", 1) or 1)
        unit = _round2(it.get("price") or it.get("unit_price") or 0)
        per_item.append({
            "name": it.get("name") or it.get("product_name") or it.get("productName") or "Ürün",
            "size": it.get("size", "") or "", "color": it.get("color", "") or "",
            "barcode": it.get("barcode") or it.get("product_id") or it.get("sku") or "",
            "image": it.get("image") or "",
            "total_qty": q, "returned_qty": 0, "unit_price": unit,
            "_bar": _inorm(it.get("barcode") or it.get("product_id") or it.get("sku")),
            "_key": (_inorm(it.get("name") or it.get("product_name") or it.get("productName")),
                     _inorm(it.get("size")), _inorm(it.get("color"))),
        })

    total_refund_amount = 0.0
    return_ids = []
    found_return = False
    query = {"$or": [{"order_id": oid}]}
    if order.get("order_number"):
        query["$or"].append({"order_number": order.get("order_number")})
    async for rec in db.customer_returns.find(query, {"_id": 0}):
        st = (rec.get("status") or "").lower()
        # İPTAL/RED/SÜRESİ DOLMUŞ iade talepleri gerçekleşmiş iade DEĞİLDİR — sayma.
        # "expired" eklendi: süresi dolup normale döndürülen sipariş (scheduler'daki
        # expire_stale_site_return_requests) teslim edilmiş görünürken detayda hayalet
        # "İade Edilen" kalemleri listeleniyordu → personel ikinci kez para iade edebilirdi.
        if st in ("cancelled", "rejected", "return_rejected", "canceled", "expired"):
            continue
        ret_items = rec.get("approved_items") or rec.get("items") or []
        if not ret_items:
            continue
        found_return = True
        return_ids.append(rec.get("id"))
        try:
            ra = rec.get("refund_amount")
            if ra not in (None, ""):
                total_refund_amount += float(ra)
        except Exception:
            pass
        for ri in ret_items:
            r_bar = _inorm(ri.get("barcode") or ri.get("product_id") or ri.get("sku"))
            r_key = (_inorm(ri.get("name") or ri.get("product_name")),
                     _inorm(ri.get("size")), _inorm(ri.get("color")))
            rq = int(ri.get("quantity", 1) or 1)
            target = None
            for pi in per_item:
                if r_bar and pi["_bar"] and r_bar == pi["_bar"] and pi["returned_qty"] < pi["total_qty"]:
                    target = pi
                    break
            if target is None:
                for pi in per_item:
                    if r_key[0] and r_key == pi["_key"] and pi["returned_qty"] < pi["total_qty"]:
                        target = pi
                        break
            if target is None:
                continue
            target["returned_qty"] = min(target["total_qty"], target["returned_qty"] + rq)

    if not found_return:
        return None

    returned_items, kept_items = [], []
    returned_total = kept_total = 0.0
    for pi in per_item:
        rq = pi["returned_qty"]
        kq = max(0, pi["total_qty"] - rq)
        base = {"name": pi["name"], "size": pi["size"], "color": pi["color"],
                "barcode": pi["barcode"], "image": pi["image"], "unit_price": pi["unit_price"]}
        if rq > 0:
            amt = _round2(rq * pi["unit_price"])
            returned_total += amt
            returned_items.append({**base, "quantity": rq, "amount": amt})
        if kq > 0:
            amt = _round2(kq * pi["unit_price"])
            kept_total += amt
            kept_items.append({**base, "quantity": kq, "amount": amt})

    is_partial = bool(returned_items) and bool(kept_items)
    return {
        "returned_items": returned_items,
        "kept_items": kept_items,
        "returned_total": _round2(returned_total),
        "kept_total": _round2(kept_total),
        "refund_amount": _round2(total_refund_amount) if total_refund_amount > 0.005 else None,
        "is_partial": is_partial,
        "return_ids": return_ids,
    }


@router.get("/returns/{return_id}/refund-preview")
async def refund_preview(return_id: str, fault: str = "store",
                         returned_net: Optional[float] = None,
                         current_user: dict = Depends(require_permission("returns.approve"))):
    """Onaydan ÖNCE iade tutarı dökümünü hesaplar (kaydetmez). Frontend bunu gösterir.
    returned_net: panelde seçili kalemlerin net toplamı (kısmi iade); verilmezse tüm iade."""
    rec = await db.customer_returns.find_one({"id": return_id}, {"_id": 0})
    if not rec:
        raise HTTPException(status_code=404, detail="İade bulunamadı")
    order = await db.orders.find_one({"id": rec.get("order_id")}, {"_id": 0}) or {}
    # Panel tutar göndermediyse: iade talebi siparişin ALT KÜMESİ olabilir (müşteri tek
    # kalem için talep açtıysa). O durumda tam iade dalına düşmesin — onay akışlarıyla
    # AYNI ölçüt ve AYNI hesap kullanılır, böylece önizleme = onaylanacak tutar.
    if returned_net is None:
        _ri = rec.get("items") or []
        if _ri and not _return_covers_whole_order(_ri, order):
            returned_net = _selected_return_net(_ri, list(range(len(_ri))), order)
    bd = await _compute_refund_breakdown(rec, order, fault, returned_net_override=returned_net)
    return {"breakdown": bd}


def _return_covers_whole_order(sel_items: list, order: dict) -> bool:
    """Seçili iade kalemleri siparişin TAMAMINI kapsıyor mu?

    PARA KAÇAĞI DÜZELTMESİ: iade talebi müşteri tarafından KALEM SEÇİLEREK açılabiliyor
    (`create_return` → payload.items index listesi). O zaman customer_returns.items
    siparişin ALT KÜMESİ olur. Onay akışları kısmiliği eskiden yalnız "seçim < KAYITTAKİ
    kalem sayısı" ile ölçüyordu; alt kümenin tamamı seçilince kısmi sayılmıyor,
    _compute_refund_breakdown TAM İADE dalına düşüp müşteriye SİPARİŞİN TAMAMINI
    (order.total / paidPrice) iade ediyordu.
      Örnek: 3.150,90 TL'lik 3 kalemlik siparişten 890 TL'lik TEK kalem iadesi →
             3.150,90 TL iade (2.260,90 TL fazla ödeme).
    Ölçüt kalem SAYISI + toplam ADET'tir; barkod/ad eşleşmesine bilerek bağlanmadı
    (kimlik alanları orders.items ile customer_returns.items arasında farklı adlarla
    saklanıyor — eşleştirme kırılgan olur, TAM iadeyi yanlışlıkla kısmi gösterebilirdi).
    Sipariş kalemleri bilinmiyorsa eski davranış (tam iade) korunur.
    """
    ord_items = (order or {}).get("items") or []
    if not ord_items or not sel_items:
        return True

    def _q(it):
        try:
            return max(1, int((it or {}).get("quantity", 1) or 1))
        except Exception:
            return 1

    if len(sel_items) < len(ord_items):
        return False
    return sum(_q(i) for i in sel_items) >= sum(_q(i) for i in ord_items)


def _selected_return_net(all_items: list, sel_idx: list, order: dict) -> float:
    """Seçili iade kalemlerinin NET (indirim düşülmüş, KDV-dahil) toplamı.

    İki onay akışı da bunu kullanır — hesabın YANLIŞLIKLA tam iadeye düşmesini önleyen
    güvenlik ağı (Kadir: "hesap yalnız tiklediklerime göre"). KDV-dahil faktörü + sipariş
    indirim oranı uygulanır ki tutar panel/gider pusulası ile BİREBİR aynı olsun
    (item.price KDV-hariç 1159.09 saklıysa faktör 1.10 ile 1275'e ölçeklenir).
    """
    if not sel_idx:
        return 0.0
    _o_sub = _round2(order.get("subtotal") or 0)
    _o_disc = _round2((order.get("discount") or 0) + (order.get("payment_discount") or 0))
    _dr = min(1.0, _o_disc / _o_sub) if _o_sub > 0 and _o_disc > 0 else 0.0
    # KDV-dahil faktör (rooftr_returns/GP ile AYNI mantık): KDV-dahil LİSTE (indirim öncesi
    # subtotal) / KDV-hariç LİSTE. İndirim ÇIKARILMAZ (ayrı _dr ile uygulanır).
    _oi = order.get("items") or []
    _all_net_ord = _round2(sum(_round2((i or {}).get("price", 0)) * int((i or {}).get("quantity", 1) or 1)
                               for i in _oi))
    _pf = 1.0
    if _all_net_ord > 0.5:
        try:
            _vf_g, _cg, _ig = _order_vade_farki(order)
        except Exception:
            _vf_g = 0.0
        _tot_h = (_round2(order.get("total") or 0) - _round2(order.get("shipping_cost") or 0)
                  - (round(_vf_g, 2) if _vf_g and _vf_g > 0 else 0.0) + _o_disc)
        for _c in [(_o_sub / _all_net_ord) if _o_sub > 0.5 else None,
                   (_tot_h / _all_net_ord) if _tot_h > 0.5 else None]:
            if _c is not None and 1.06 <= _c <= 1.24:
                _pf = _c
                break
    # DONMUŞ per-ürün indirim (checkout) varsa kapsam-farkındalıklı; yoksa düz-oransal (_dr).
    _froz = all((all_items[i] or {}).get("discount_amount") is not None for i in sel_idx)
    return _round2(sum(
        _pf * ((_round2(all_items[i].get("price", 0)) * int(all_items[i].get("quantity", 1) or 1))
               - (_round2(all_items[i].get("discount_amount") or 0) if _froz
                  else _round2(all_items[i].get("price", 0)) * int(all_items[i].get("quantity", 1) or 1) * _dr))
        for i in sel_idx))


@router.post("/returns/{return_id}/approve")
async def approve_return(return_id: str, payload: dict,
                         current_user: dict = Depends(require_permission("returns.approve"))):
    """İadeyi onaylar: tutarı hesaplar (kusur seçimine göre), kaydeder, durumu 'approved'
    yapar ve müşteriye bildirim gönderir. Tutar elle override edilebilir (loglanır)."""
    rec = await db.customer_returns.find_one({"id": return_id}, {"_id": 0})
    if not rec:
        raise HTTPException(status_code=404, detail="İade bulunamadı")
    if rec.get("status") in ("refunded", "cancelled"):
        raise HTTPException(status_code=400, detail="Bu iade kapanmış; onaylanamaz.")
    order = await db.orders.find_one({"id": rec.get("order_id")}, {"_id": 0}) or {}

    fault = (payload.get("fault") or "store").lower()
    if fault not in ("customer", "store"):
        raise HTTPException(status_code=400, detail="Geçersiz kusur (customer|store).")

    cargo_override = payload.get("return_cargo_fee")
    cargo_override = None if cargo_override in (None, "") else cargo_override
    returned_net_in = payload.get("returned_net")
    returned_net_in = None if returned_net_in in (None, "") else returned_net_in

    # KISMİ ONAY: ekranda seçilen kalemler onay kaydına YAZILIR ki gider pusulası
    # (payload'sız/seçimsiz çağrıldığında bile) tüm siparişe değil onaylanan kalemlere
    # kesilsin. Kimlik (selected_items) öncelikli, index geriye uyum.
    _all_items = rec.get("items") or []
    try:
        _ap_sel = _resolve_return_selection(_all_items, payload.get("selected_items"), payload.get("item_indexes"))
    except ValueError as _e:
        raise HTTPException(status_code=400, detail=str(_e))
    _ap_partial_sel = bool(_ap_sel) and len(_ap_sel) < len(_all_items)
    _sel_idx = _ap_sel or list(range(len(_all_items)))
    # MÜKERRER İADE (denetim 2026-10): bu onay + diğer onaylı/yoldaki/ödenmiş iadeler, sipariş
    # kalem adedini AŞAMAZ (aynı ürün iki ayrı iade kaydıyla iki kez iade edilip ödenmesin).
    if order:
        _ret_other = await _already_returned_qty(rec.get("order_id"), exclude_return_id=return_id)
        if _ret_other:
            _ord_q = _order_qty_by_key(order)
            _want = {}
            for _i in _sel_idx:
                if 0 <= _i < len(_all_items):
                    _k = _return_item_key(_all_items[_i])
                    _want[_k] = _want.get(_k, 0) + max(0, int(_all_items[_i].get("quantity") or 1))
            _over = [(k, q) for k, q in _want.items()
                     if k in _ord_q and q + _ret_other.get(k, 0) > _ord_q[k]]
            if _over:
                _names = ", ".join(sorted({k[0] for k, _ in _over}))[:200]
                raise HTTPException(status_code=400, detail=(
                    f"Bu kalem(ler) bu siparişte daha önce iade edilmiş; sipariş adedini aşan iade "
                    f"onaylanamaz ({_names}). Önceki iade kayıtlarını kontrol edin."))
    # KISMİLİK **SİPARİŞE** GÖRE ÖLÇÜLÜR: iade kaydının kendisi siparişin alt kümesi
    # olabilir (müşteri tek kalem iade talebi açtıysa) → bkz. _return_covers_whole_order.
    _ap_partial = _ap_partial_sel or not _return_covers_whole_order(
        [_all_items[i] for i in _sel_idx if i < len(_all_items)], order)
    # Güvenlik ağı: kısmi ama tutar gönderilmediyse TAM İADE'ye düşme — seçili kalemlerden türet.
    if _ap_partial and returned_net_in is None:
        returned_net_in = _selected_return_net(_all_items, _sel_idx, order)

    bd = await _compute_refund_breakdown(rec, order, fault, return_cargo_fee_override=cargo_override,
                                         returned_net_override=returned_net_in)

    # Elle tutar override (Karar #5: otomatik hesap + elle düzeltme, loglanır)
    final_amount = bd["auto_refund"]
    manual_override = False
    if payload.get("refund_amount") not in (None, ""):
        try:
            final_amount = _round2(payload.get("refund_amount"))
        except Exception:
            raise HTTPException(status_code=400, detail="Geçersiz iade tutarı.")
        manual_override = abs(final_amount - bd["auto_refund"]) > 0.01

    now_iso = datetime.now(timezone.utc).isoformat()
    approval = {
        "by": current_user.get("email") or current_user.get("id"),
        "at": now_iso,
        "fault": fault,
        "auto_refund": bd["auto_refund"],
        "final_refund": final_amount,
        "manual_override": manual_override,
        "note": (payload.get("note") or "")[:500],
    }
    if _ap_partial_sel:
        approval["item_indexes"] = _ap_sel
    _set = {
        "status": "approved", "fault": fault,
        "refund_breakdown": bd, "refund_amount": final_amount,
        "approval": approval, "updated_at": now_iso,
    }
    _upd = {"$set": _set}
    if _ap_partial_sel:
        # Kimlik snapshot'ı da saklanır: GP anında items sırası değişse bile
        # barcode/ad+beden+renk ile eşlenir (index kaymasına dayanıklı).
        _set["approved_item_indexes"] = _ap_sel
        _set["approved_items"] = [{
            "barcode": (_all_items[i].get("barcode") or _all_items[i].get("product_id") or ""),
            "name": _all_items[i].get("name") or "",
            "size": _all_items[i].get("size") or "",
            "color": _all_items[i].get("color") or "",
        } for i in _ap_sel]
    else:
        # Tam onay: eski kısmi onay artıkları kalmasın
        _upd["$unset"] = {"approved_item_indexes": "", "approved_items": ""}
    await db.customer_returns.update_one({"id": return_id}, _upd)
    # Sipariş durumu: iade zaten daha İLERİ bir aşamadaysa (returned/refunded/partial_refunded —
    # ör. sessiz durum düzeltmesiyle 'iade edildi/ödendi' yapılmış, kalem onayı sonradan
    # yapılıyor; W11262) GERİ 'return_approved'a DÜŞÜRME — ödenmiş iade regrese olmasın.
    # Aksi halde (talep/onay aşamasında) return_approved'a al.
    _oset = {"return_request.status": "approved", "updated_at": now_iso}
    if (order.get("status") or "") not in ("returned", "refunded", "partial_refunded"):
        _oset["status"] = "return_approved"
    if not order.get("return_approved_at"):
        _oset["return_approved_at"] = now_iso
    await db.orders.update_one({"id": rec.get("order_id")}, {"$set": _oset})

    # OTOMATİK STOK GERİ: iade ONAYLANDI → onaylanan kalemleri ADET kadar stoğa ekle (idempotent,
    # kısmi dahil). Güncel kaydı (yeni yazılan approved_items dahil) çekip helper'a ver.
    try:
        _fresh = await db.customer_returns.find_one({"id": return_id}, {"_id": 0})
        await _restock_return_items_once(_fresh or rec, order)
    except Exception as _e:
        logger.warning(f"[iade] onay restock hata order={rec.get('order_id')}: {_e}")

    # Bildirim: "İade Onaylandı" + tutar (kanal ayarı panelden yönetilir)
    import asyncio as _aio
    async def _n():
        try:
            import sys, os
            sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
            from notification_service import send_notification
            from order_statuses import get_status_config, customer_label_for
            cfg = await get_status_config(db)
            nz = (cfg.get("notify") or {}).get("return_approved") or {}
            ch = [c for c in ("sms", "email") if nz.get(c)]
            if not ch:
                return
            addr = (order or {}).get("shipping_address") or {}
            await send_notification(
                db, "order_return_approved",
                to_phone=addr.get("phone") or order.get("phone"),
                to_email=addr.get("email") or order.get("email"),
                variables=await _order_notify_vars(order, order_number=rec.get("order_number", ""), return_code=rec.get("return_code", ""), refund_amount=f"{final_amount:.2f}", status_label=customer_label_for("return_approved")),
                channels=ch,
            )
        except Exception as e:
            logger.warning(f"return approve notif failed: {e}")
    _spawn(_n())

    await _log_order_event(rec.get("order_id"), "return", f"İade onaylandı (₺{final_amount})", current_user,
                           {"return_id": return_id, "refund_amount": final_amount, "fault": fault},
                           order_number=rec.get("order_number", ""))
    return {"success": True, "status": "approved", "refund_amount": final_amount,
            "manual_override": manual_override, "breakdown": bd}


@router.post("/returns/{return_id}/update-approval")
async def update_return_approval(return_id: str, payload: dict,
                                 current_user: dict = Depends(require_permission("returns.expense_note"))):
    """ONAYLANMIŞ bir iadenin ONAY SEÇİMİNİ (hangi kalemler + kargo müşteriden kesildi mi)
    muhasebe/admin olarak DÜZENLE ve yeniden kilitle. Kullanıcı isteği: 'düzenle → yeni
    seçimi onayla → kilitle; yenileyince eski haline dönmesin'.

    STATÜ/STOK/BİLDİRİM DEĞİŞMEZ (iade zaten kapanmış olabilir). Yalnız approved_items,
    approved_item_indexes, refund_breakdown, refund_amount ve fault güncellenir → panel
    yenilenince yeni seçim korunur ve gider pusulası bu seçime göre hesaplanır."""
    rec = await db.customer_returns.find_one({"id": return_id}, {"_id": 0})
    if not rec:
        raise HTTPException(status_code=404, detail="İade bulunamadı")
    order = await db.orders.find_one({"id": rec.get("order_id")}, {"_id": 0}) or {}
    # OPERATÖR SİPARİŞTEN DAHA FAZLA ÜRÜN SEÇTİYSE (kök sebep — W11381): customer_returns kaydı
    # siparişin ALT KÜMESİ olabilir (müşteri talebi 1 ürün) ama operatör panelde siparişin 2
    # kalemini seçip onaylıyor. Panel seçimi SİPARİŞ kalemlerine göre indekslidir; kayıt yalnız 1
    # kalemse seçim SIĞMAZ → 2. ürün yok sayılır, onay/pusula tek üründe kalır ("2 seçtim
    # güncellenmiyor"). Bu durumda kaydın kalemlerini SİPARİŞ kalemleriyle GENİŞLETİRİZ ki
    # operatörün seçtiği tüm ürünler onaya ve gider pusulasına girsin. Yalnız seçim mevcut kalem
    # sayısını AŞTIĞINDA tetiklenir → normal (kayıt = sipariş) durumlar etkilenmez.
    _rec_items = rec.get("items") or []
    _ord_items = order.get("items") or []
    def _idx_ok(v):
        try:
            return int(v)
        except Exception:
            return None
    _sent_idx = payload.get("item_indexes") if isinstance(payload.get("item_indexes"), list) else []
    _sent_sel = payload.get("selected_items") if isinstance(payload.get("selected_items"), list) else []
    _needs_expand = bool(_ord_items) and (
        any((_idx_ok(i) is not None and _idx_ok(i) >= len(_rec_items)) for i in _sent_idx)
        or (len(_sent_sel) > len(_rec_items))
    )
    if _needs_expand:
        _expanded = [{
            "name": it.get("product_name") or it.get("name") or "Ürün",
            "size": it.get("size", "") or "", "color": it.get("color", "") or "",
            "quantity": int(it.get("quantity", 1) or 1),
            "price": float(it.get("price") or it.get("unit_price") or 0),
            "unit_price": float(it.get("unit_price") or it.get("price") or 0),
            "product_id": it.get("barcode") or it.get("product_id") or it.get("sku") or "",
        } for it in _ord_items]
        await db.customer_returns.update_one({"id": return_id}, {"$set": {"items": _expanded}})
        rec["items"] = _expanded
    _all_items = rec.get("items") or []
    try:
        _ap_sel = _resolve_return_selection(_all_items, payload.get("selected_items"), payload.get("item_indexes"))
    except ValueError as _e:
        raise HTTPException(status_code=400, detail=str(_e))
    _ap_partial_sel = bool(_ap_sel) and len(_ap_sel) < len(_all_items)
    _sel_idx = _ap_sel or list(range(len(_all_items)))
    # KISMİLİK **SİPARİŞE** GÖRE (iade kaydı siparişin alt kümesi olabilir).
    _ap_partial = _ap_partial_sel or not _return_covers_whole_order(
        [_all_items[i] for i in _sel_idx if i < len(_all_items)], order)
    include_cargo = bool(payload.get("include_cargo"))
    fault = "customer" if include_cargo else "store"
    cargo_override = payload.get("return_cargo_fee")
    cargo_override = None if cargo_override in (None, "") else cargo_override
    returned_net_in = payload.get("returned_net")
    returned_net_in = None if returned_net_in in (None, "") else returned_net_in
    # GÜVENLİK AĞI: kısmi iadede returned_net gönderilmediyse, hesabın YANLIŞLIKLA
    # TAM İADE'ye düşmesini önle — tutarı SEÇİLİ kalemlerin net toplamından türet
    # (Kadir talebi: "hesap yalnız tiklediklerime göre").
    if _ap_partial and returned_net_in is None:
        returned_net_in = _selected_return_net(_all_items, _sel_idx, order)

    bd = await _compute_refund_breakdown(rec, order, fault, return_cargo_fee_override=cargo_override,
                                         returned_net_override=returned_net_in)
    # Kargo müşteriden kesildiyse breakdown'da işaretle (panel önişaretlemesi bunu okur).
    if include_cargo:
        _c = dict(bd.get("cargo") or {}); _c["mode"] = "deducted"; bd["cargo"] = _c
    final_amount = bd.get("auto_refund")
    if payload.get("refund_amount") not in (None, ""):
        try:
            final_amount = _round2(payload.get("refund_amount"))
        except Exception:
            raise HTTPException(status_code=400, detail="Geçersiz iade tutarı.")

    now_iso = datetime.now(timezone.utc).isoformat()
    _set = {"refund_breakdown": bd, "refund_amount": final_amount, "fault": fault,
            "approval_edited_by": current_user.get("email") or current_user.get("id"),
            "approval_edited_at": now_iso, "updated_at": now_iso}
    _upd = {"$set": _set}
    if _ap_partial_sel:
        _set["approved_item_indexes"] = _ap_sel
        _set["approved_items"] = [{
            "barcode": (_all_items[i].get("barcode") or _all_items[i].get("product_id") or ""),
            "name": _all_items[i].get("name") or "",
            "size": _all_items[i].get("size") or "",
            "color": _all_items[i].get("color") or "",
        } for i in _ap_sel]
    else:
        _upd["$unset"] = {"approved_item_indexes": "", "approved_items": ""}
    await db.customer_returns.update_one({"id": return_id}, _upd)

    # GP DİNAMİK GÜNCELLEME: bu iadeye ait gider pusulası ZATEN kesildiyse, yeni seçime
    # (kalem + kargo) göre YENİDEN hesapla (idempotent: numara/koçan KORUNUR). Kullanıcı
    # isteği: "düzenle'de bir değişiklik yapınca gider pusulası da DİNAMİK güncellensin —
    # tüm iadelerde." Böylece onay seçimini değiştirince pusula tutarı otomatik düzelir.
    gp_regenerated = False
    _gp = await db.gider_pusulasi.find_one({"return_id": return_id},
                                           {"_id": 0, "display_number": 1})
    if _gp:
        try:
            # preview=False AÇIKÇA geçilir: doğrudan çağrıda varsayılan Query(False) nesnesi
            # truthy kaldığından pusula "önizleme" sayılıp KAYDEDİLMİYORDU (sessiz no-op).
            await site_return_gider_pusulasi(return_id, payload={
                "include_cargo": include_cargo,
                "tracking_no": _gp.get("display_number") or "",
                "refund_amount": final_amount,  # elle/edited tutarı pusulaya YANSIT (yeni tutara göre kes)
            }, preview=False, current_user=current_user)
            gp_regenerated = True
        except HTTPException:
            # e-Fatura/kurumsal vb. → pusula düzenlenemez; sessiz geç (onay yine güncellendi).
            pass
        except Exception as _e:
            logger.warning(f"[update-approval] GP yeniden hesaplama hatası {return_id}: {_e}")

    return {"success": True, "refund_amount": final_amount, "breakdown": bd,
            "cargo_deducted": bool(include_cargo), "gp_regenerated": gp_regenerated}


# ============================================================================
# Madde 3 — Ret (sebep + bildirim) + Barkod yeniden üretimi (P4)
# ============================================================================

async def _create_return_shipment(ref: str, kiymet: float, icerik: str, recipient: dict):
    """Parametrik MNG/DHL iade gönderisi (best-effort). recipient = ŞİRKET (alıcı) adresi.
    Gönderi MNG'ye 'IW…' sipariş no (iade no) ile kaydedilir → şube barkodu okutunca paket bize gelir.
    Dönüş: (gonderi_no, mng_ok). gonderi_no = MNG'nin ürettiği GERÇEK (taranabilir) gönderi no/barkod;
    üretilemezse "" döner (çağıran taraf iade no'ya / anlaşmalı no'ya düşmeye karar verir)."""
    gonderi_no = ""   # gerçek gönderi no/barkod; MNG üretemezse boş — sahte iç kod ASLA döndürülmez
    mng_ok = False
    # ACİL ANAHTAR (restore_mode): geri yükleme modunda kargo firmasına gönderi YAZILMAZ. MNG hatasıyla
    # AYNI sonuç döner (gonderi_no="", mng_ok=False) → iade kaydı iade no ile açılır, barkod sonra
    # "Yeni Kargo Barkodu" ile üretilir.
    try:
        from security_switches import restore_mode_on as _rm_on
        if await _rm_on():
            logger.warning(f"[restore_mode] iade gönderisi MNG'ye yazılmadı ref={ref}")
            return gonderi_no, mng_ok
    except Exception:
        pass
    try:
        import sys, os, asyncio as _aio
        sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
        from mng_kargo_client import create_shipment as mng_create
        s = await _get_mng_settings()
        if s.get("is_active") and recipient.get("address") and recipient.get("city"):
            res = await _aio.to_thread(
                mng_create,
                username=s["username"], password=s["password"],
                siparis_no=ref, kiymet=float(kiymet or 0),
                icerik=(icerik or "IADE")[:200],
                hizmet_sekli="NORMAL", teslim_sekli=1, al_sms=0, gn_sms=0,
                parca_list="1:1:20:30:15:;",
                alici_ad=recipient.get("name", ""), odeme_sekli="GO", adres_farkli="0",
                il=recipient["city"], ilce=recipient.get("district", ""), adres=recipient["address"],
                tel_cep=recipient.get("phone", ""), email="", kapida_odeme=0,
                platform_adi="", platform_kodu="",
            )
            if res.get("ok") and res.get("barkod"):
                gonderi_no = str(res["barkod"]).strip()
                mng_ok = True
    except Exception as e:
        logger.warning(f"return shipment create failed: {e}")
    return gonderi_no, mng_ok


async def _within_return_window(order: dict) -> bool:
    """Sipariş teslim tarihinden itibaren iade penceresi içinde mi? (barkod yeniden üretimi için)
    Süre admin panelinden (İşletme Kuralları > İade süresi) yönetilir; varsayılan 14 gün."""
    delivered_at = order.get("delivered_at")
    if not delivered_at:
        return False
    try:
        d = datetime.fromisoformat(str(delivered_at).replace("Z", "+00:00"))
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
    except Exception:
        return False
    try:
        import business_rules as _BR
        _days = int(await _BR.get_rule(db, "return.window_days", 14) or 14)
    except Exception:
        _days = 14
    return datetime.now(timezone.utc) <= (d + timedelta(days=_days))


@router.post("/returns/{return_id}/reissue-barcode")
async def reissue_return_barcode(return_id: str,
                                 current_user: dict = Depends(require_permission("returns.cargo_rebook"))):
    """Karar #1: barkod 3 gün geçerli; 14 gün içinde YENİDEN üretilebilir.
    Depoya iade gönderisi için yeni 3 günlük barkod üretir, kayda yazar, müşteriye bildirir."""
    await _guard_restore_mode()   # ACİL ANAHTAR: geri yükleme modu → 423
    rec = await db.customer_returns.find_one({"id": return_id}, {"_id": 0})
    if not rec:
        raise HTTPException(status_code=404, detail="İade bulunamadı")
    if rec.get("status") in ("refunded", "cancelled", "rejected"):
        raise HTTPException(status_code=400, detail="Bu iade kapanmış; yeni barkod üretilemez.")
    order = await db.orders.find_one({"id": rec.get("order_id")}, {"_id": 0}) or {}
    if not await _within_return_window(order):
        raise HTTPException(status_code=400, detail="İade süresi (teslimden 14 gün) dolmuş; barkod üretilemez.")

    items = rec.get("items") or []
    iade_no = rec.get("iade_no") or rec.get("mng_ref") or f"IW{rec.get('order_number','')}{generate_id()[:6]}".replace(" ", "")
    icerik = "IADE - " + "; ".join(f"{i.get('quantity',1)}x {i.get('name','Ürün')}" for i in items)
    warehouse = await _get_sender_info()
    gonderi_no, mng_ok = await _create_return_shipment(iade_no, order.get("total") or 0, icerik, warehouse)
    code = gonderi_no or iade_no
    png_b64 = _render_return_barcode_png_b64(code)
    now = datetime.now(timezone.utc)
    valid_until = (now + timedelta(days=3)).isoformat()
    await db.customer_returns.update_one({"id": return_id}, {"$set": {
        "return_code": code, "iade_no": iade_no, "gonderi_no": gonderi_no,
        "contract_no": RETURN_CONTRACT_NO,
        "company_address": _company_return_address(warehouse),
        "mng_ref": iade_no, "mng_ok": mng_ok, "barcode_png_b64": png_b64,
        "valid_until": valid_until, "updated_at": now.isoformat(),
        "status": "created" if rec.get("status") in (None, "created", "in_transit") else rec.get("status"),
    }})
    await db.orders.update_one({"id": rec.get("order_id")},
        {"$set": {"return_request.return_code": code, "return_request.iade_no": iade_no,
                  "return_request.gonderi_no": gonderi_no, "return_request.valid_until": valid_until,
                  "updated_at": now.isoformat()}})

    # Bildirim: yeni barkod (oluşturma şablonu ile)
    import os as _os, asyncio as _aio
    _base = _os.environ.get("FRONTEND_PUBLIC_URL") or _os.environ.get("REACT_APP_BACKEND_URL") or ""
    barcode_img = (f'<div style="margin:14px 0"><img src="{_base}/api/orders/returns/{return_id}/barcode.png" '
                   f'alt="{code}" style="height:90px"/></div>' if _base else "")
    _spawn(_notify_return(order, code, valid_until, barcode_img, iade_no=iade_no))

    return {"success": True, "return_code": code, "mng_ok": mng_ok, "valid_until": valid_until}


@router.post("/returns/{return_id}/reject")
async def reject_return(return_id: str, payload: dict,
                        current_user: dict = Depends(require_permission("returns.reject"))):
    """İadeyi reddeder: sebep zorunlu, durum 'rejected', müşteriye SEBEP'le bildirim.
    İsteğe bağlı (reship=true): ürünü müşteriye geri göndermek için yeni kargo barkodu üretir."""
    rec = await db.customer_returns.find_one({"id": return_id}, {"_id": 0})
    if not rec:
        raise HTTPException(status_code=404, detail="İade bulunamadı")
    if rec.get("status") in ("refunded", "cancelled"):
        raise HTTPException(status_code=400, detail="Bu iade kapanmış; reddedilemez.")
    reason = (payload.get("reason") or "").strip()
    if not reason:
        raise HTTPException(status_code=400, detail="Ret sebebi zorunludur.")
    order = await db.orders.find_one({"id": rec.get("order_id")}, {"_id": 0}) or {}
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()

    # İsteğe bağlı: ürünü müşteriye geri gönder (yeni barkod, alıcı = müşteri)
    reship_code = ""
    if payload.get("reship"):
        addr = order.get("shipping_address") or {}
        recipient = {
            "name": addr.get("full_name") or f"{addr.get('first_name','')} {addr.get('last_name','')}".strip() or "Müşteri",
            "phone": addr.get("phone", ""), "city": addr.get("city", ""),
            "district": addr.get("district", ""), "address": addr.get("address", ""),
        }
        ref = f"RET{rec.get('order_number','')}{generate_id()[:6]}".replace(" ", "")
        _rgn, _mng = await _create_return_shipment(ref, order.get("total") or 0, "IADE RED - geri gonderim", recipient)
        reship_code = _rgn or ref

    rejection = {
        "by": current_user.get("email") or current_user.get("id"),
        "at": now_iso, "reason": reason, "reship_code": reship_code,
    }
    await db.customer_returns.update_one({"id": return_id}, {"$set": {
        "status": "rejected", "reject_reason": reason, "rejection": rejection,
        "reship_code": reship_code, "updated_at": now_iso,
    }})
    await db.orders.update_one({"id": rec.get("order_id")}, {"$set": {
        "status": "return_rejected", "return_request.status": "rejected", "updated_at": now_iso,
    }})

    # DENETİM (bizlogic Y1b/F2): iade ONAYINDA stok geri ekleniyor; sonradan REDDEDİLİRSE (ürün
    # müşteriye geri gönderiliyor) o +stok HAYALET kalıp oversell yaratıyordu. Restock'u geri al.
    if rec.get("stock_restored"):
        try:
            # DENETİM (race R5): ATOMİK claim — iki eşzamanlı reject çift ters-çevirim yapmasın.
            _claim = await db.customer_returns.update_one(
                {"id": return_id, "stock_restored": True}, {"$unset": {"stock_restored": ""}})
            if _claim.modified_count == 1:
                _mv = await db.stock_movements.find_one(
                    {"return_id": return_id, "type": "return_restock"}, {"_id": 0, "items": 1})
                if _mv and _mv.get("items"):
                    await _reverse_stock_moves(_mv["items"])
                    await db.stock_movements.insert_one({
                        "id": str(uuid.uuid4()), "type": "return_restock_reversed",
                        "order_id": rec.get("order_id"), "order_number": rec.get("order_number", ""),
                        "return_id": return_id, "items": _mv["items"], "source": "return_reject",
                        "created_at": now_iso})
                    logger.info(f"[iade-ret] restock geri alındı return={return_id}")
        except Exception as _e:
            logger.error(f"[iade-ret de-restock {return_id}] {_e}")

    # Bildirim: reddedildi + SEBEP (+ varsa geri-gönderim takip kodu)
    import asyncio as _aio
    async def _n():
        try:
            import sys, os as _os
            sys.path.insert(0, _os.path.dirname(_os.path.dirname(__file__)))
            from notification_service import send_notification
            from order_statuses import get_status_config, customer_label_for
            cfg = await get_status_config(db)
            nz = (cfg.get("notify") or {}).get("return_rejected") or {}
            ch = [c for c in ("sms", "email") if nz.get(c)]
            if not ch:
                return
            addr = (order or {}).get("shipping_address") or {}
            await send_notification(
                db, "order_return_rejected",
                to_phone=addr.get("phone") or order.get("phone"),
                to_email=addr.get("email") or order.get("email"),
                variables=await _order_notify_vars(order, order_number=rec.get("order_number", ""), return_code=rec.get("return_code", ""), reason=reason, reship_code=reship_code, status_label=customer_label_for("return_rejected")),
                channels=ch,
            )
        except Exception as e:
            logger.warning(f"return reject notif failed: {e}")
    _spawn(_n())

    await _log_order_event(rec.get("order_id"), "return", f"İade reddedildi: {reason}", current_user,
                           {"return_id": return_id, "reason": reason},
                           order_number=rec.get("order_number", ""))
    return {"success": True, "status": "rejected", "reason": reason, "reship_code": reship_code}


# ============================================================================
# Madde 3 — Gider Pusulası (site iadeleri) (P5)
# ============================================================================

@router.post("/returns/{return_id}/gider-pusulasi")
async def site_return_gider_pusulasi(return_id: str, payload: Optional[dict] = Body(default=None),
                                     preview: bool = Query(False),
                                     current_user: dict = Depends(require_permission("returns.expense_note"))):
    """Site iadesi için gider pusulası verisi üretir. Trendyol tarafıyla AYNI koleksiyonu
    (db.gider_pusulasi) ve AYNI numara serisini kullanır (numaralar sürekli).

    preview=True: veriyi HESAPLAR ama NUMARA ATAMAZ / KAYDETMEZ (ekranda önizleme). Numara
    yalnız YAZDIR'a basınca (preview=False) atanır ve kalıcılaşır → açıp yazdırmadan kapatınca
    numara yanmaz (Kadir: 'numara yazdırınca atansın')."""
    rec = await db.customer_returns.find_one({"id": return_id}, {"_id": 0})
    if not rec:
        raise HTTPException(status_code=404, detail="İade bulunamadı")
    order = await db.orders.find_one({"id": rec.get("order_id")}, {"_id": 0}) or {}
    # KATI KURAL: e-Fatura / kurumsal siparişe gider pusulası KESİNLİKLE düzenlenemez —
    # iade faturası gerekir (kullanıcı isteği). Hiçbir yol (tekil/toplu) bunu aşamaz.
    if _order_is_efatura(order):
        raise HTTPException(status_code=400, detail="Bu sipariş e-Fatura/kurumsal siparişidir — gider pusulası düzenlenemez. Müşteriden iade faturası alınmalıdır.")
    settings = await db.settings.find_one({"id": "main"}, {"_id": 0}) or {}
    company = settings.get("company_info", {}) if settings else {}

    ship = order.get("shipping_address", {}) or {}
    cust_name = (f"{ship.get('first_name','')} {ship.get('last_name','')}".strip()
                 or ship.get("full_name", "") or "Müşteri")

    # ONARIM: iade köprüsü kalemleri BOŞ ise (mükerrer birleştirme / eski kayıt) sipariş
    # kalemlerinden doldur → GP net'i 0 çıkmaz (Kadir: toplu önizlemede bazı site tutarları 0).
    # (Tekil akışta /open bunu yapıyordu; toplu akış /open'ı atladığından burada da yapılır.)
    if not (rec.get("items") or []):
        _src = order.get("items") or []
        _fix = [{
            "name": it.get("product_name") or it.get("name") or "Ürün",
            "size": it.get("size", "") or "", "color": it.get("color", "") or "",
            "quantity": int(it.get("quantity", 1) or 1),
            "price": float(it.get("price") or it.get("unit_price") or 0),
            "unit_price": float(it.get("unit_price") or it.get("price") or 0),
            "product_id": it.get("barcode") or it.get("product_id") or it.get("sku") or "",
        } for it in _src]
        if _fix:
            rec["items"] = _fix
            await db.customer_returns.update_one({"id": return_id}, {"$set": {"items": _fix}})

    all_items = rec.get("items", []) or []
    # Seçili kalemler (kısmi iade). Öncelik sırası:
    #   1) payload.selected_items: kalem KİMLİKLERİ [{barcode, name, size, color}] — index
    #      kaymasına dayanıklı. barcode↔product_id öncelikli eşleşir.
    #   2) payload.item_indexes: rec.items index'leri (geriye uyum).
    #   3) Payload'da seçim YOKSA → onayda kaydedilen kısmi kalemler (approved_items /
    #      approved_item_indexes). Böylece "İade Onay (N kalem)" sonrası sayfa yenilense
    #      ya da GP sonradan kesilse bile pusula ONAYLANAN kalemlere düzenlenir.
    #   4) O da yoksa => TÜM sipariş (tam iade).
    # GÜVENLİK: seçim GÖNDERİLDİYSE ama hiçbir kalem eşleşmediyse SESSİZCE tam iadeye
    # DÜŞMEYİZ — 400 döneriz.
    try:
        _sel = _resolve_return_selection(all_items,
                                         (payload or {}).get("selected_items"),
                                         (payload or {}).get("item_indexes"))
    except ValueError as _e:
        raise HTTPException(status_code=400, detail=str(_e))
    if not _sel:
        try:
            _sel = _resolve_return_selection(all_items, rec.get("approved_items"),
                                             rec.get("approved_item_indexes"))
        except ValueError:
            _sel = []  # onay kaydı items ile eşleşmiyorsa tam iadeye düş (kullanıcı seçimi değil)
    if _sel:
        _sel = sorted(set(_sel))
        items = [all_items[i] for i in _sel]
    else:
        _sel = list(range(len(all_items)))
        items = list(all_items)
    # TAM İADE mi? Seçim, iade KAYDININ kalemlerine göre DEĞİL, SİPARİŞİN kalemlerine göre
    # ölçülür: customer_returns kaydı siparişin ALT KÜMESİ olabilir (müşteri yalnız 1 ürün iade
    # talep etmiş → kayıtta tek kalem). Kaydın "tümü" seçili olsa bile bu KISMİ iadedir; aksi
    # halde order.total (TÜM sipariş + vade farkı) baz alınıp tek ürüne tüm tutar yazılırdı
    # (W10331 hatası: Nerel'e 3.150,90 yazması). Adet bazında siparişle kıyaslarız.
    _order_items = order.get("items") or []
    _ord_qty = sum(int((i or {}).get("quantity", 1) or 1) for i in _order_items)
    _sel_qty = sum(int((all_items[i] or {}).get("quantity", 1) or 1) for i in _sel if 0 <= i < len(all_items))
    if _ord_qty > 0:
        is_full = _sel_qty >= _ord_qty and len(_sel) >= len(_order_items)
    else:
        is_full = len(all_items) > 0 and len(_sel) >= len(all_items)

    def _q(it):
        return int(it.get("quantity", 1) or 1)

    # KDV-DAHİL TABAN DÜZELTMESİ (Ali Al 398MD4734D): bazı içe-aktarılan (Ticimax) siparişlerde
    # item.price KDV-HARİÇ saklanmış, oysa subtotal/total KDV-DAHİL. Faktör = KDV-DAHİL LİSTE
    # (indirim ÖNCESİ subtotal) / KDV-HARİÇ LİSTE (Σ item.price). İNDİRİM ÇIKARILMAZ: indirim
    # ayrı order-seviyesi kalemdir, item.price'a yansımaz ve aşağıda _disc_ratio ile AYRICA
    # uygulanır. (Eski hata: (subtotal−indirim)/Σ → indirimli siparişte faktör 0.99 çıkıp grossing
    # yapılmıyor, sonra bir de _disc_ratio uygulanıp ÇİFT indirim oluyordu — Senem Birdal 952IF2376A.)
    # Faktör bir KDV oranına yakınsa (1.06–1.24) uygulanır; değilse 1.0 (normal siparişler etkilenmez).
    _all_net = _round2(sum(_round2((i or {}).get("price", 0)) * int((i or {}).get("quantity", 1) or 1)
                          for i in _order_items))
    _sub_h = _round2(order.get("subtotal") or 0)
    _disc_h = _round2((order.get("discount") or 0) + (order.get("payment_discount") or 0))
    _paid_factor = 1.0
    if _all_net > 0.5:
        try:
            _vf_f, _charged_f, _inst_f = _order_vade_farki(order)
        except Exception:
            _vf_f = 0.0
        # total-tabanlı yedek: indirimi GERİ ekle → indirim-öncesi KDV-dahil taban.
        _tot_h = _round2(order.get("total") or 0) - _round2(order.get("shipping_cost") or 0) - (round(_vf_f, 2) if _vf_f and _vf_f > 0 else 0.0) + _disc_h
        _cands = []
        if _sub_h > 0.5:
            _cands.append(_sub_h / _all_net)
        if _tot_h > 0.5:
            _cands.append(_tot_h / _all_net)
        for _f in _cands:
            if 1.06 <= _f <= 1.24:
                _paid_factor = _f
                break

    prod_gross = _round2(sum(_round2(it.get("unit_price", it.get("price", 0))) * _q(it) for it in items) * _paid_factor)
    prod_net = _round2(sum(_round2(it.get("price", 0)) * _q(it) for it in items) * _paid_factor)

    # Kargo bedeli kaynağı — FATURA NE İSE O:
    #   faturada ücret VARSA (shipping_cost>0) → ödenmiş kargodur, iadeye + olarak EKLENİR.
    #   YOKSA (ücretsiz kargo) → faturada kargo YOK; iadede de UYDURMA bir 99 TL çekilmez.
    #   (Operatör yine de kesmek isterse payload.cargo_override ile tutar geçebilir.)
    shipping_cost = _round2(order.get("shipping_cost") or 0)
    paid_shipping = shipping_cost > 0.009
    include_cargo = bool((payload or {}).get("include_cargo"))
    if paid_shipping:
        cargo_amount = shipping_cost
    else:
        try:
            cargo_amount = _round2(float((payload or {}).get("cargo_override") or 0))
        except Exception:
            cargo_amount = 0.0
        # ÜCRETSİZ KARGO + 'kargoyu müşteriden kes': sipariş eşiği aşıp ücretsiz kargodan
        # faydalanmıştı; iade sonrası bu hak kalkıyorsa standart kargo ücreti (ayarlardan, ör. 99)
        # iade tutarından + gider pusulasından düşülür (Kadir: '2300−99'). Override yoksa çöz.
        if cargo_amount <= 0 and include_cargo:
            try:
                _thr_gp, _fee_gp = await _storefront_free_shipping()
                cargo_amount = _round2(_fee_gp or 0)
            except Exception:
                cargo_amount = 0.0

    order_total = _round2(order.get("total") or 0)
    order_sub = _round2(order.get("subtotal") or 0)
    # SİPARİŞ-SEVİYESİ indirim = kupon/kampanya (discount) + havale indirimi (payment_discount).
    # Bunlar item.price'a YANSIMAZ (order.total'da uygulanır); pusula kalemleri müşterinin
    # GERÇEKTE ödediğini göstersin diye bu indirim kalemlere oransal dağıtılır. Aksi halde
    # pusula kalem tutarları faturadan/ödemeden farklı çıkar (Özge Yalçın tutarsızlığı).
    order_disc = _round2((order.get("discount") or 0) + (order.get("payment_discount") or 0))
    # İNDİRİM YALNIZ ÜRÜNLERE dağıtılır (payda = ara toplam, KARGO HARİÇ). MUHASEBE TERCİHİ:
    # kargo (%20 KDV) ile ürünler (%10 KDV) farklı oranlı; indirim kargoya yayılırsa KDV matrahları
    # karışır. Kargoya indirim UYGULANMAZ → kargo tam bedeliyle (kendi %20 KDV'siyle) durur, indirim
    # tamamen ürünlere biner. (iyzico indirimi kargo dahil oranlar → kalem neti ~kuruş farklı çıkar;
    # bizim muhasebemize göre kargoya indirim yansıtmak yanlış olduğundan iyzico'yu baz ALMIYORUZ.)
    _disc_base = order_sub
    _disc_ratio = (order_disc / _disc_base) if _disc_base > 0.009 else 0.0
    if _disc_ratio < 0:
        _disc_ratio = 0.0
    if _disc_ratio > 1:
        _disc_ratio = 1.0

    # DONMUŞ per-ürün indirim (checkout'ta kampanya kapsamına göre yazıldı) varsa onu kullan
    # (kapsam-dışı ürün 0); yoksa sipariş toplam indirimini düz-oransal uygula. W11214 kök çözümü.
    _frozen_disc = bool(all_items) and all(
        isinstance(_i, dict) and (_i.get("discount_amount") is not None) for _i in all_items)

    def _eff_net_unit(it):
        """Kalemin GERÇEK ödenen birim neti. Donmuş per-ürün indirim varsa (kapsam-farkındalıklı) onu,
        yoksa düz-oransal indirimi uygular; ikisi de KDV-dahil faktörüyle ölçeklenir."""
        _lp = _round2(it.get("price", 0))
        if _frozen_disc:
            _q = int(it.get("quantity", 1) or 1) or 1
            _du = _round2(it.get("discount_amount") or 0) / _q
            return _round2((_lp - _du) * _paid_factor)
        return _round2(_lp * _paid_factor * (1 - _disc_ratio))

    cargo_line = None
    cargo_mode = "none"
    vade_line = None
    # Kargo toggle (include_cargo) tek anlam taşır: "kargoyu müşteriden KES (mahsup)".
    # Kusur müşterideyse operatör işaretler → iade tutarından kargo düşülür. Kesilen kargo
    # AYRI satır olarak yazılmaz; fark "indirim" toplamına katlanır ki satırlar net ile tutarlı kalsın.
    deduct_cargo = include_cargo and cargo_amount > 0
    if is_full:
        # TAM İADE → baz, müşterinin GERÇEKTE ÖDEDİĞİ tutar (taksitliyse vade farkı dahil
        # paidPrice; değilse order.total). Kupon indirimi zaten ödenen tutarın içindedir.
        _vf_r, _charged_r, _inst_r = _order_vade_farki(order)
        _paid = _charged_r if (_charged_r > order_total + 0.01) else order_total
        if _paid <= 0:
            _paid = _round2(max(0.0, prod_net - order_disc + (shipping_cost if paid_shipping else 0)))
        # KARGO AYRI KALEM (Kadir: 'kargoyu ayrı göster kargo iade edildiğinde'). Ürün neti kendi
        # satırlarında; ödenmiş kargo iade ediliyorsa AYRI 'Kargo Bedeli' satırı (%20 KDV) eklenir.
        # 'Kargoyu müşteriden kes' işaretliyse kargo iade tutarından DÜŞÜLÜR (satır yazılmaz).
        net_total = _round2(_paid - (cargo_amount if deduct_cargo else 0.0))
        if deduct_cargo:
            total_gross = _round2(prod_gross + (_vf_r if _vf_r >= 0.01 else 0))
            cargo_mode = "deducted"
        else:
            total_gross = _round2(prod_gross + (cargo_amount if paid_shipping else 0) + (_vf_r if _vf_r >= 0.01 else 0))
            if paid_shipping and shipping_cost > 0:
                cargo_line = {"name": "Kargo Bedeli", "net_price": cargo_amount, "qty": 1}
                cargo_mode = "refund"
        total_discount = _round2(max(0.0, total_gross - net_total))
        if _vf_r >= 0.01:
            vade_line = {"name": f"Vade Farkı (Taksit x{_inst_r})", "net_price": _vf_r, "qty": 1}
    else:
        # KISMİ İADE → seçili ürünler; sipariş-seviyesi (kupon) indirimi seçili kalemlere oransal dağıtılır.
        # Donmuş per-ürün indirim varsa: seçili kalemlerin GERÇEK indirim toplamı (kapsam-farkındalıklı,
        # faktörle ölçekli). Yoksa düz-oransal. (W11214: kapsam-dışı Etek → 0 indirim.)
        if _frozen_disc:
            alloc_disc = _round2(sum(_round2(it.get("discount_amount") or 0) for it in items) * _paid_factor)
        else:
            alloc_disc = _round2(order_disc * (prod_net / _disc_base)) if (order_disc > 0 and _disc_base > 0) else 0.0
        base_net = _round2(max(0.0, prod_net - alloc_disc))
        # VADE FARKI (taksit) — kısmi iadede de faturayla örtüşsün diye ORANSAL eklenir: iade edilen
        # ürünlerin faiz-siz ödenen tabana (order.total) oranı kadar vade farkı iade edilir. Tam
        # iadeyle aynı mantık, yalnız iade payına düşen kısmı. (Önceden kısmi iadede HİÇ eklenmiyordu.)
        _vf_r, _charged_r, _inst_r = _order_vade_farki(order)
        alloc_vade = 0.0
        if _vf_r >= 0.01:
            # TABAN, iade hesabıyla (_compute_refund_breakdown) BİREBİR AYNI olmalı: ürünlerin
            # NET toplamı (subtotal − indirim). Eskiden order.total kullanılıyordu; kargo ücreti
            # order.total'ın içinde olduğundan, kargolu siparişte pay küçük çıkıyor ve sipariş
            # kalem kalem tamamen iade edilse bile vade farkının tamamı geri verilmiyordu.
            # Net taban = subtotal − (kupon + havale/EFT indirimi). _compute_refund_breakdown
            # (7398-7400) ile birebir; payment_discount de düşülür (havale+taksit birlikte nadir
            # ama tutarlılık için).
            # iyzico İLE BİREBİR: vade farkı kargo DAHİL tüm kalemlere orantılı (taban order.total);
            # _compute_refund_breakdown ile aynı taban. (W11676: kalem 1.004,50 = 890 × 3.147,82/2.789)
            _vf_base = order_total
            if _vf_base <= 0.009:
                _vf_base = _round2(max(0.0, _round2(order.get("subtotal") or 0)
                                       - _round2(order.get("discount") or 0)
                                       - _round2(order.get("payment_discount") or 0)))
            if _vf_base > 0.009:
                _ret_share = base_net / _vf_base
                if _ret_share > 1:
                    _ret_share = 1.0
                alloc_vade = _round2(_vf_r * _ret_share)
        net_total = _round2(max(0.0, base_net + alloc_vade - (cargo_amount if deduct_cargo else 0.0)))
        total_gross = _round2(prod_gross + (alloc_vade if alloc_vade >= 0.01 else 0.0))
        total_discount = _round2(max(0.0, total_gross - net_total))
        # Vade farkı AYRI SATIR (pusulada "ürün + vade farkı" olarak görünür).
        if alloc_vade >= 0.01:
            vade_line = {"name": f"Vade Farkı (Taksit x{_inst_r})", "net_price": alloc_vade, "qty": 1}
        if deduct_cargo:
            cargo_mode = "deducted"

    # MANUEL/EDİTLENMİŞ TOPLAM (opt-in): caller net iade tutarını AÇIKÇA geçtiyse (update-approval
    # / elle düzenleme), pusula TOPLAMI otomatik hesap yerine BU tutara sabitlenir. Böylece operatör
    # 'İade tutarı'nı elle değiştirince gider pusulası da yeni tutara göre kesilir (kullanıcı talebi:
    # "gider pusulasını güncellediğimiz yeni tutara göre düzenlemiyor"). Ürün satırları aşağıdaki
    # mutabakatla bu hedefe çekilir; kargo/vade satırları korunur. Yalnız açıkça geçilince aktif →
    # diğer çağrılar (preview, toplu, satır-içi) davranışı DEĞİŞMEZ.
    _manual_total = (payload or {}).get("refund_amount")
    try:
        _manual_total = None if _manual_total in (None, "") else _round2(float(_manual_total))
    except Exception:
        _manual_total = None
    if _manual_total is not None and _manual_total >= 0 and abs(_manual_total - net_total) >= 0.01:
        net_total = _manual_total
        if total_gross < net_total:
            total_gross = net_total          # gider pusulası: brüt < net olamaz
        total_discount = _round2(max(0.0, total_gross - net_total))

    vat_rate = settings.get("default_vat_rate", 10) if settings else 10
    vat_amount = round(net_total * vat_rate / (100 + vat_rate), 2)
    net_without_vat = round(net_total - vat_amount, 2)

    # ORTAK numara serisi (Trendyol + site). İDEMPOTENT: bu iade için pusula ZATEN varsa
    # numarayı KORU (yeniden hesaplama yeni numara YAKMAZ) → mevcut pusulaları düzeltebiliriz.
    _existing_gp = await db.gider_pusulasi.find_one(
        {"return_id": return_id}, {"_id": 0, "number": 1, "display_number": 1})
    tracking_no = str((payload or {}).get("tracking_no") or "").strip()
    if _existing_gp and _existing_gp.get("number"):
        gp_number = _existing_gp["number"]
        display_number = tracking_no or _existing_gp.get("display_number") or f"GP-{gp_number:06d}"
    else:
        last_gp = await db.gider_pusulasi.find_one({}, sort=[("number", -1)])
        gp_number = (last_gp.get("number", 0) + 1) if last_gp else 1
        display_number = tracking_no if tracking_no else f"GP-{gp_number:06d}"

    # Kalem satırları GERÇEK ödeneni gösterir: net = item.price × (1 − sipariş indirim oranı).
    # Böylece pusuladaki kalem tutarları TOPLAMI, faturayla/ödemeyle tutarlı olur.
    gp_items = []
    for it in items:
        _list_unit = _round2(it.get("unit_price", it.get("price", 0)))
        _net_unit = _eff_net_unit(it)
        gp_items.append({
            "name": it.get("name", ""),
            "barcode": it.get("product_id", "") or it.get("barcode", ""),
            "size": it.get("size", ""),
            "quantity": int(it.get("quantity", 1) or 1),
            "unit_price": _list_unit,
            "discount": _round2(max(0.0, _list_unit - _net_unit)),
            "net_price": _net_unit,
            "reason": rec.get("reason", ""),
        })

    # 🎯 YUVARLAMA MUTABAKATI (Huriye): Kargo mahsubu YOKKEN pusula, faturayla/ödemeyle KURUŞU
    # KURUŞUNA örtüşmeli. Kalem net'leri tek tek yuvarlandığı için toplam ±birkaç kuruş kayabilir;
    # farkı SON ürün satırına yazarak ürün-satırları toplamını hedefe (net_total − kargo − vade)
    # sabitleriz. Mahsup (deduct_cargo) durumunda tutar zaten faturadan farklıdır → dokunmayız.
    if gp_items and not deduct_cargo:
        _cargo_net = (cargo_line["net_price"] if cargo_line else 0.0)
        _vade_net = (vade_line["net_price"] if vade_line else 0.0)
        _target_prod = _round2(net_total - _cargo_net - _vade_net)
        _cur_prod = _round2(sum(_round2(g["net_price"]) * int(g.get("quantity", 1) or 1) for g in gp_items))
        _diff = _round2(_target_prod - _cur_prod)
        if abs(_diff) >= 0.01:
            _last = gp_items[-1]
            _q = int(_last.get("quantity", 1) or 1)
            _last["net_price"] = _round2(max(0.0, _last["net_price"] + _diff / _q))  # negatif net olamaz
            _last["discount"] = _round2(max(0.0, _last["unit_price"] - _last["net_price"]))

    if cargo_line:
        gp_items.append({
            "name": cargo_line["name"],
            "barcode": "",
            "size": "",
            "quantity": cargo_line.get("qty", 1),
            "unit_price": cargo_line["net_price"],
            "discount": 0,
            "net_price": cargo_line["net_price"],
            "reason": "Kargo",
        })

    if vade_line:
        gp_items.append({
            "name": vade_line["name"],
            "barcode": "",
            "size": "",
            "quantity": vade_line.get("qty", 1),
            "unit_price": vade_line["net_price"],
            "discount": 0,
            "net_price": vade_line["net_price"],
            "reason": "Vade farkı (taksit)",
        })

    # #13: Bedava kargo alınan siparişten KISMİ iade → kargo kampanya koşulu değişti (muhasebe uyarısı).
    _cargo_campaign_warning = ""
    if (not paid_shipping) and (not is_full):
        _fs_thr = order.get("free_shipping_threshold")
        _kept = _round2(order_sub - prod_net)  # iade edilmeyen (kalan) ürünlerin tutarı
        if _fs_thr and _kept < float(_fs_thr):
            _cargo_campaign_warning = (f"KARGO KAMPANYA DIŞI KALDI — sipariş ücretsiz kargo ile alınmıştı; "
                f"kısmi iade sonrası kalan tutar {_kept:.2f} TL, ücretsiz kargo eşiğinin ({float(_fs_thr):.0f} TL) ALTINA düştü.")
        else:
            _cargo_campaign_warning = ("KARGO KAMPANYA DIŞI KALDI — sipariş ücretsiz kargo ile alınmıştı; "
                "kısmi iade sonrası kargo kampanya koşulunu yeniden değerlendirin.")

    # Kargo müşteriden KESİLDİYSE (deduct_cargo) gider pusulasında AÇIKÇA belirt (Kadir isteği:
    # 5000₺ sipariş → 1500₺ iade → kalan 3500₺ eşiğin altında → 1500−99 ödenir). Net ödeme kargo
    # kadar düşürülür; pusulada "Kargo mahsubu" olarak gösterilir (önizleme + matbu).
    _cargo_deduction_note = ""
    if deduct_cargo and cargo_amount > 0:
        _kept_after = _round2(order_sub - prod_net) if not is_full else 0.0
        _cargo_deduction_note = (
            f"Kargo bedeli {cargo_amount:.2f} TL müşteriden kesildi — ücretsiz kargo hakkı "
            f"iade sonrası kalktı"
            + (f" (kalan tutar {_kept_after:.2f} TL, eşiğin altında)" if not is_full else "")
            + f". Net ödeme buna göre {net_total:.2f} TL."
        )

    gider_pusulasi = {
        "number": gp_number,
        "display_number": display_number,
        "return_id": return_id,
        "source": "site",
        "cargo_campaign_warning": _cargo_campaign_warning,
        "order_number": rec.get("order_number", ""),
        "date": datetime.now(timezone.utc).isoformat(),
        "company": company,
        "customer": {
            "name": cust_name,
            "address": ship.get("address", ""),
            "district": ship.get("district", ""),
            "city": ship.get("city", ""),
            "country": ship.get("country", "") or "Türkiye",
        },
        "sales_invoice_no": order.get("invoice_number", ""),
        "cargo_company": rec.get("cargo_provider_name", ""),
        "sales_rep": "",
        "items": gp_items,
        "totals": {
            "gross": total_gross,
            "discount": total_discount,
            "net": net_total,
            "vat_rate": vat_rate,
            "vat_amount": vat_amount,
            "net_without_vat": net_without_vat,
        },
        "cargo": {
            "included": bool(cargo_line),
            "mode": cargo_mode,            # refund | deducted | none
            "amount": cargo_amount,
            "deducted": bool(deduct_cargo),
            "paid_shipping": paid_shipping,
            "is_full": is_full,
        },
        "cargo_deduction_note": _cargo_deduction_note,
        "claim_reason": rec.get("reason", ""),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    # ÖNİZLEME'de KAYDETME/NUMARA YAKMA — yalnız yazdırınca (preview=False) kalıcılaşır.
    if not preview:
        await db.gider_pusulasi.update_one({"return_id": return_id}, {"$set": gider_pusulasi}, upsert=True)
        await db.customer_returns.update_one({"id": return_id},
            {"$set": {"has_gider_pusulasi": True, "gider_pusulasi_no": display_number}})
    gider_pusulasi["preview"] = bool(preview)
    return {"success": True, "gider_pusulasi": gider_pusulasi, "preview": bool(preview)}


@router.get("/returns/gider-pusulasi/export")
async def export_gider_pusulasi_excel(
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    source: Optional[str] = None,   # site | trendyol | hepsiburada | amazon | all(None) → TÜMÜ (varsayılan)
    only_refunded: bool = False,    # False=TÜM iade durumları; True=yalnız iade bedeli ödenmiş
    only_with_gp: bool = False,     # True=YALNIZ pusulası kesilmiş (seri no'lu) satırlar (kullanıcı isteği)
    current_user: dict = Depends(require_permission("returns.expense_note")),
):
    """Gider pusulalarını MUHASEBE formatında Excel'e aktarır (görseldeki kolon düzeni):
    Fatura Tarihi | Seri Numarası | Adı-Soyadı | Kdv Oranı | Tutar (VD) | Vergi Hariç Tutar (Y) | Kdv (Y)

    MANTIK: Her pusula, kalemlerinin KDV ORANINA göre gruplanır → her (pusula × oran) AYRI SATIR
    olur (kargo/vade farkı %20, ürünler %10 veya ürünün gerçek KDV'si). Tutarlar GİDER olduğundan
    NEGATİF yazılır. Tutar (VD)=KDV dahil, Vergi Hariç=net matrah, Kdv=KDV tutarı.
    """
    await _guard_switch("freeze_exports")   # ACİL ANAHTAR: toplu dışa aktarım donduruldu → 423
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment
    from io import BytesIO
    from fastapi.responses import StreamingResponse

    # ── Kaynak süzgeci (varsayılan: TÜMÜ) ──────────────────────────────
    _src = (source or "").lower().strip()
    want_site = _src in ("", "all", "site")
    want_ty = _src in ("", "all", "trendyol")
    want_hb = _src in ("", "all", "hepsiburada")
    want_amz = _src in ("", "all", "amazon")

    # Tarih aralığı → UTC sınırları. Koleksiyonlar farklı tarih alanı/formatı kullandığından
    # (voucher.date, claim.created_date, return.created_at) Python'da parse edip karşılaştırırız.
    def _to_dt(v):
        s = str(v or "").strip()
        if not s:
            return None
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00"))
        except Exception:
            try:
                return datetime.fromisoformat(s[:19])
            except Exception:
                return None
    lo_dt = _to_dt(tr_day_start_utc(date_from)) if date_from else None
    hi_dt = _to_dt(tr_day_end_utc(date_to)) if date_to else None

    def _in_range(v):
        d = _to_dt(v)
        if d is None:
            return not (lo_dt or hi_dt)  # tarih yoksa: süzgeç varsa dışarıda bırak
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        if lo_dt and d < lo_dt:
            return False
        if hi_dt and d > hi_dt:
            return False
        return True

    # ── Tüm iade taleplerini (Trendyol + Hepsiburada — ikisi de trendyol_claims'te,
    #    HB platform="hepsiburada") ve site iadelerini çekeriz. Kesilmiş pusula VARSA onu
    #    (doğru seri no + düzeltilmiş tutar) kullanırız; YOKSA talepten satır sentezleriz.
    #    Böylece pusula kesilmemiş iadeler de Excel'e girer ("658 tümünü çekmiyor" düzeltmesi)
    #    ve ileride eklenen her iade kaynağı otomatik dahil olur.
    all_claims = await db.trendyol_claims.find(
        {}, {"_id": 0, "raw_data": 0}).to_list(None)
    claim_by_id = {str(c.get("claim_id")): c for c in all_claims if c.get("claim_id")}

    def _claim_platform(c):
        _cp = str(c.get("platform") or "").lower()
        return _cp if _cp in ("hepsiburada", "amazon") else "trendyol"

    # ── DURUM SÜZGECİ (kullanıcı talebi): Excel'e SADECE onaylanan/tamamlanan/ödenen iadeler girer.
    # DAHİL: iade onaylandı, iade tamamlandı, (kısmi) iade tamamlandı/ödendi, iade bedeli ödendi,
    #        onay sonrası kargoda/teslim alındı + Trendyol/HB "Accepted".
    # HARİÇ: reddedilenler, iptal edilenler, sadece "iade talebi oluşturuldu" / henüz işlemde olanlar.
    _DENY_STATUS = {
        "return_requested", "created", "pending", "preparing", "shipped",
        "return_rejected", "rejected", "cancelled", "canceled", "expired",
        "error", "exception", "undelivered",
        "waitinginaction", "inanalysis", "unresolved",  # TY/HB: onaylanmamış/işlemde
    }
    def _status_ok(st):
        return str(st or "").strip().lower() not in _DENY_STATUS

    # Site iade durum + TALEP TARİHİ haritaları (voucher kaydında durum/talep tarihi yok →
    # return_id / order_number ile bağla). Talep tarihi = iade talep edilen tarih (created_at).
    ret_status_by_id, ret_status_by_num = {}, {}
    ret_reqdate_by_id, ret_reqdate_by_num = {}, {}
    ret_apprdate_by_id, ret_apprdate_by_num = {}, {}  # İADE ONAY tarihi (approval.at → yoksa created_at)
    async for _r in db.customer_returns.find(
            {}, {"_id": 0, "id": 1, "status": 1, "order_number": 1, "created_at": 1, "date": 1, "approval": 1}):
        _st = _r.get("status") or ""
        _rqd = _r.get("created_at") or _r.get("date") or ""
        _apd = (_r.get("approval") or {}).get("at") or _rqd
        if _r.get("id"):
            ret_status_by_id[str(_r["id"])] = _st
            ret_reqdate_by_id[str(_r["id"])] = _rqd
            ret_apprdate_by_id[str(_r["id"])] = _apd
        if _r.get("order_number"):
            ret_status_by_num.setdefault(str(_r["order_number"]), _st)
            ret_reqdate_by_num.setdefault(str(_r["order_number"]), _rqd)
            ret_apprdate_by_num.setdefault(str(_r["order_number"]), _apd)

    # ── TARİH ESASI: TÜM satırlar İADE TALEP TARİHİ'ne göre süzülür (Trendyol claimDate /
    #    site created_at). Böylece "son 30 gün" export'u iadeler sayfasındaki "Tarih" sütunu
    #    (claim.created_date) ile BİREBİR aynı olur → tutarsızlık biter. Kesilmiş pusulalar da
    #    kendi kesim tarihine göre DEĞİL, bağlı iadenin TALEP tarihine göre süzülür.
    # İADE ONAY TARİHİ çözücü (Kadir: 'iade onay tarihine göre filtrele'): TY/HB → claim
    # return_approved_at (yoksa created_date); site → customer_returns approval.at (yoksa created_at).
    def _appr_date_for(gp):
        cid = str(gp.get("claim_id") or "")
        if cid and claim_by_id.get(cid):
            _c = claim_by_id[cid]
            _d = _c.get("return_approved_at") or _c.get("created_date")
            if _d:
                return _d
        rid = str(gp.get("return_id") or "")
        if rid and ret_apprdate_by_id.get(rid):
            return ret_apprdate_by_id[rid]
        onum = str(gp.get("order_number") or "")
        if onum and ret_apprdate_by_num.get(onum):
            return ret_apprdate_by_num[onum]
        return gp.get("date") or gp.get("created_at")

    # ── 1) Var olan gider pusulaları (kesilmiş) — doğru seri + düzeltilmiş tutar ──
    all_vouchers = await db.gider_pusulasi.find({}, {"_id": 0}).to_list(None)
    seen_claim, seen_return = set(), set()
    records = []
    for gp in all_vouchers:
        # SÜZGEÇ = PUSULANIN KESİLDİĞİ (FATURA) TARİHİ (Kadir isteği): gider pusulası HANGİ AY
        # kesildiyse Excel'de o ayda görünmeli — sipariş/iptal/iade-onay tarihine göre DEĞİL.
        # (Filtre = "Fatura Tarihi" kolonuyla AYNI alan → gp.date/created_at.)
        if not _in_range(gp.get("date") or gp.get("created_at")):
            continue
        # kaynak süzgeci: site pusulası source=site; TY/HB pusulasında claim_id var → platform claim'den
        cid = str(gp.get("claim_id") or "")
        if cid:
            plat = _claim_platform(claim_by_id.get(cid, {}))
            if plat == "hepsiburada" and not want_hb:
                continue
            if plat == "amazon" and not want_amz:
                continue
            if plat == "trendyol" and not want_ty:
                continue
            seen_claim.add(cid)
        else:
            if not want_site:
                continue
            if gp.get("return_id"):
                seen_return.add(str(gp.get("return_id")))
        # durum: claim ise claim_status; site ise return/order durumundan
        if cid:
            gp["_status"] = claim_by_id.get(cid, {}).get("claim_status", "")
        else:
            gp["_status"] = (ret_status_by_id.get(str(gp.get("return_id") or ""))
                             or ret_status_by_num.get(str(gp.get("order_number") or "")) or "")
        records.append(gp)

    # only_refunded (opsiyonel; UI'daki tek buton bunu False geçer): sadece kesilmiş +
    # iade bedeli ödenmiş pusulaları bırak, sentezlenenleri hiç ekleme.
    if only_refunded:
        _rids = list({str(gp.get("return_id")) for gp in records if gp.get("return_id")})
        _onums = list({str(gp.get("order_number")) for gp in records if gp.get("order_number")})
        _paid_status = ["refunded", "partial_refunded"]
        paid_returns, paid_orders = set(), set()
        if _rids:
            async for r in db.customer_returns.find(
                {"id": {"$in": _rids},
                 "$or": [{"status": {"$in": _paid_status}}, {"refund_payment": {"$exists": True}}]},
                {"_id": 0, "id": 1}):
                paid_returns.add(str(r.get("id")))
        if _onums:
            async for o in db.orders.find(
                {"order_number": {"$in": _onums},
                 "$or": [{"status": {"$in": _paid_status}}, {"refund_paid_at": {"$exists": True}}]},
                {"_id": 0, "order_number": 1}):
                paid_orders.add(str(o.get("order_number")))
        records = [gp for gp in records
                   if str(gp.get("return_id")) in paid_returns
                   or str(gp.get("order_number")) in paid_orders]
    else:
        # ── 2) Pusulası kesilmemiş Trendyol/HB iadeleri → satır sentezle ──
        for c in all_claims:
            cid = str(c.get("claim_id") or "")
            if not cid or cid in seen_claim:
                continue
            plat = _claim_platform(c)
            if plat == "hepsiburada" and not want_hb:
                continue
            if plat == "trendyol" and not want_ty:
                continue
            cdate = c.get("return_approved_at") or c.get("created_date") or c.get("created_at") or ""
            if not _in_range(cdate):
                continue
            syn_items = []
            for ci in (c.get("items") or []):
                syn_items.append({
                    "name": ci.get("productName") or ci.get("name") or "",
                    "barcode": str(ci.get("barcode") or "").strip(),
                    "quantity": int(ci.get("quantity", 1) or 1),
                    "net_price": ci.get("price", 0),
                    "reason": ci.get("reason", ""),
                })
            _net = c.get("refund_amount")
            records.append({
                "date": cdate,
                # Koçan/seri no SADECE gerçekten atanmışsa yazılır. Atanmamışsa BOŞ bırakılır —
                # invoice_number / order_number / claim_id gibi BAŞKA bir numara ÇEKİLMEZ
                # (muhasebe "atanmamış pusulaya yanlış no geliyor" düzeltmesi).
                "display_number": (c.get("gider_pusulasi_no") or ""),
                "number": 0,
                "customer": {"name": c.get("customer_name", "")},
                "order_number": c.get("order_number", ""),
                "items": syn_items,
                "totals": {"net": _net if _net not in (None, "") else 0, "vat_rate": 10},
                "_status": c.get("claim_status", ""),
            })

        # ── 3) Pusulası kesilmemiş SİTE iadeleri → satır sentezle ──
        if want_site:
            site_rets = await db.customer_returns.find(
                {"status": {"$ne": "expired"}}, {"_id": 0}).to_list(None)
            site_rets = [r for r in site_rets
                         if str(r.get("id")) not in seen_return and _in_range(
                             (r.get("approval") or {}).get("at") or r.get("created_at") or r.get("date"))]
            # müşteri adını sipariş shipping_address'ten toplu çek
            _oids = list({r.get("order_id") for r in site_rets if r.get("order_id")})
            _oname = {}
            if _oids:
                async for o in db.orders.find(
                    {"id": {"$in": _oids}},
                    {"_id": 0, "id": 1, "shipping_address": 1, "customer_name": 1}):
                    sh = o.get("shipping_address", {}) or {}
                    nm = (f"{sh.get('first_name','')} {sh.get('last_name','')}".strip()
                          or sh.get("full_name", "") or o.get("customer_name", "") or "")
                    _oname[o.get("id")] = nm
            for r in site_rets:
                syn_items = []
                for it in (r.get("items") or []):
                    syn_items.append({
                        "name": it.get("name") or it.get("product_name") or "",
                        "barcode": str(it.get("product_id") or it.get("barcode") or "").strip(),
                        "quantity": int(it.get("quantity", 1) or 1),
                        "net_price": it.get("price", it.get("unit_price", 0)),
                        "reason": it.get("reason", ""),
                    })
                records.append({
                    "date": (r.get("approval") or {}).get("at") or r.get("created_at") or r.get("date") or "",
                    # Site sentez satırı: pusula henüz kesilmemiş → seri no BOŞ kalır
                    # (order_number/return id gibi başka bir numara koçan alanına ÇEKİLMEZ).
                    "display_number": "",
                    "number": 0,
                    "customer": {"name": _oname.get(r.get("order_id"), "") or "Müşteri"},
                    "order_number": r.get("order_number", ""),
                    "items": syn_items,
                    "totals": {"net": 0, "vat_rate": 10},
                    "_status": r.get("status", ""),
                })

    # EN YENİ ÜSTTE: birleşik listeyi tarihe göre azalan sırala.
    records.sort(key=lambda g: str(g.get("date") or g.get("created_at") or ""), reverse=True)

    # Durumu bilinmeyen (voucher return_id/order eşleşmedi) kayıtlar için sipariş durumuna bak.
    _unknown_onums = list({str(g.get("order_number")) for g in records
                           if not g.get("_status") and g.get("order_number")})
    if _unknown_onums:
        _ostat = {}
        async for o in db.orders.find(
                {"order_number": {"$in": _unknown_onums}},
                {"_id": 0, "order_number": 1, "status": 1}):
            _ostat[str(o.get("order_number"))] = o.get("status") or ""
        for g in records:
            if not g.get("_status"):
                g["_status"] = _ostat.get(str(g.get("order_number")), "")

    # SADECE izin verilen durumlar: reddedilen / iptal / sadece-talep / işlemde HARİÇ.
    # İSTİSNA (muhasebe mutabakatı): pusulası FİİLEN KESİLMİŞ kayıt (seri no atanmış)
    # durum ne olursa olsun Excel'e GİRER — fiziki pusula var, listeden düşemez.
    records = [g for g in records
               if (g.get("number") or g.get("display_number")) or _status_ok(g.get("_status"))]
    # only_with_gp: pusulası OLUŞMAMIŞ hiçbir satır Excel'e girmez (kullanıcı isteği).
    if only_with_gp:
        records = [g for g in records if (g.get("number") or g.get("display_number"))]

    # Ürün KDV oranlarını toplu çek (kalem barcode alanı product_id tutar).
    pids = set()
    for gp in records:
        for it in (gp.get("items") or []):
            b = str(it.get("barcode") or "").strip()
            if b:
                pids.add(b)
    vat_by_pid = {}
    if pids:
        async for p in db.products.find({"id": {"$in": list(pids)}}, {"_id": 0, "id": 1, "vat_rate": 1}):
            if p.get("vat_rate") not in (None, ""):
                try:
                    vat_by_pid[p["id"]] = float(p["vat_rate"])
                except Exception:
                    pass

    def _ddmmyyyy(iso: str) -> str:
        s = str(iso or "")[:10]
        if len(s) == 10 and s[4] == "-":
            return f"{s[8:10]}.{s[5:7]}.{s[0:4]}"
        return s

    def _item_rate(it: dict, default_rate: float) -> float:
        """Kalem KDV oranı: kargo/vade %20; ürün → ürünün vat_rate'i, yoksa pusula oranı."""
        reason = (it.get("reason") or "").lower()
        name = (it.get("name") or "").lower()
        if "kargo" in reason or "kargo" in name or "vade" in reason or "vade" in name:
            return 20.0
        b = str(it.get("barcode") or "").strip()
        if b and b in vat_by_pid:
            return vat_by_pid[b]
        return float(default_rate or 10)

    wb = Workbook()
    ws = wb.active
    ws.title = "Gider Pusulası"
    headers = ["Fatura Tarihi", "Seri Numarası", "Adı-Soyadı", "Kdv Oranı",
               "Tutar (VD)", "Vergi Hariç Tutar (Y)", "Kdv (Y)"]
    ws.append(headers)
    hfont = Font(bold=True, color="FFFFFF")
    hfill = PatternFill(start_color="1F2937", end_color="1F2937", fill_type="solid")
    for c in range(1, len(headers) + 1):
        cell = ws.cell(row=1, column=c)
        cell.font = hfont
        cell.fill = hfill
        cell.alignment = Alignment(horizontal="center", vertical="center")

    for gp in records:
        totals = gp.get("totals", {}) or {}
        default_rate = totals.get("vat_rate") or 10
        fatura_tarihi = _ddmmyyyy(gp.get("date") or gp.get("created_at") or "")
        # Seri/koçan no: gerçek atanmış numara varsa onu yaz. Sentez kayıtta number=0 →
        # "000000" DAMGALANMAZ; atanmamış pusula seri no'su BOŞ kalır.
        _num = gp.get("number") or 0
        seri = gp.get("display_number") or (f"{_num:06d}" if _num else "")
        name = (gp.get("customer") or {}).get("name") or ""

        items = gp.get("items") or []
        # KDV oranına göre KDV-DAHİL tutarları grupla (kalem net_price = gerçek ödenen, KDV dahil).
        by_rate = {}
        for it in items:
            rate = _item_rate(it, default_rate)
            qty = int(it.get("quantity", 1) or 1)
            gross_vatincl = _round2(_round2(it.get("net_price", 0)) * qty)
            by_rate[rate] = _round2(by_rate.get(rate, 0.0) + gross_vatincl)

        # Kalem yoksa VEYA kalem tutarları 0 çıktıysa (sentez iade: fiyatsız kalem ama
        # refund_amount var) → pusula/talep net toplamından tek satır.
        if (not by_rate) or _round2(sum(by_rate.values())) <= 0.009:
            net = _round2(totals.get("net") or 0)
            if net > 0.009:
                by_rate = {float(default_rate): net}

        # FATURA MUTABAKATI (VADE FARKI DÜZELTMESİ): pusulanın toplam neti (totals.net) faturayı
        # yansıtır — fatura grand total = paidPrice = ürün + kargo + VADE FARKI (KDV Kanunu 24/c,
        # %20). Kesilen ESKİ pusulalarda vade/kargo satırı items dizisine yazılmamış olabildiğinden
        # kalem toplamı totals.net'in ALTINDA kalır → Excel eksik tutar gösterirdi. Eksik farkı,
        # faturadaki gibi %20 KDV'li (vade farkı/kargo) satır olarak ekleyip pusulayı totals.net'e
        # BİREBİR eşitleriz. Böylece 658 mevcut pusula RE-CUT GEREKMEDEN faturayla örtüşür.
        # Yeni pusulalar (vade satırı zaten items'ta) ve pazaryeri pusulaları (items zaten
        # total_net'e mutabık) için fark 0 → ÇİFT SAYIM OLMAZ.
        _net_target = _round2(totals.get("net") or 0)
        if _net_target > 0.009 and by_rate:
            _short = _round2(_net_target - _round2(sum(by_rate.values())))
            if _short >= 0.01:
                by_rate[20.0] = _round2(by_rate.get(20.0, 0.0) + _short)

        # Her oran grubu → bir satır (yüksek orandan düşüğe, kargo/vade üstte görünür).
        for rate in sorted(by_rate.keys(), reverse=True):
            gross = by_rate[rate]
            if gross <= 0.009:
                continue
            vat = _round2(gross * rate / (100 + rate))
            net_wo = _round2(gross - vat)
            ws.append([
                fatura_tarihi, seri, name, int(rate),
                -gross, -net_wo, -vat,
            ])

    # Sütun genişlikleri + tutar formatları
    widths = [14, 14, 22, 10, 14, 20, 14]
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[ws.cell(row=1, column=i).column_letter].width = w
    for r in range(2, ws.max_row + 1):
        for c in (5, 6, 7):
            ws.cell(row=r, column=c).number_format = "#,##0.00"

    # GÜVENLİK: Excel/CSV formül injection — = + - @ ile başlayan metin hücrelerini kaçışla
    for _ws in wb.worksheets:
        for _row in _ws.iter_rows():
            for _c in _row:
                if isinstance(_c.value, str) and _c.value[:1] in ("=", "+", "-", "@", "\t", "\r"):
                    _c.value = "'" + _c.value
    # KVKK: ad-soyad içeren toplu dışa aktarım → denetim izi + güvenlik olayı (alarm).
    _gp_rows = sum(max(0, (_w.max_row or 1) - 1) for _w in wb.worksheets)
    await record_admin_audit(
        db, action="returns.gider_pusulasi_export", entity_type="export", entity_id="gider_pusulasi",
        current_user=current_user, source="admin.returns",
        metadata={"rows": _gp_rows, "date_from": date_from or "", "date_to": date_to or "",
                  "source": source or "all", "only_refunded": bool(only_refunded),
                  "only_with_gp": bool(only_with_gp)})
    try:
        from security_switches import emit_security_event as _sec_ev
        await _sec_ev("returns_export", current_user, {"export": "returns.gider_pusulasi", "rows": _gp_rows})
    except Exception:
        pass
    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    _fn = "gider-pusulasi"
    if date_from or date_to:
        _fn += f"-{(date_from or '')}_{(date_to or '')}"
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{_fn}.xlsx"'},
    )


@router.post("/returns/vouchers/recompute")
async def recompute_site_vouchers(payload: Optional[dict] = Body(default=None),
                                  current_user: dict = Depends(require_permission("returns.expense_note"))):
    """MEVCUT site gider pusulalarını DÜZELTİLMİŞ tutar mantığıyla (sipariş-seviyesi indirim
    oransal dağıtımı + yuvarlama mutabakatı) yeniden hesaplar. Numara KORUNUR (idempotent).
    Seçim orijinaldeki approved_items'a göre yeniden çözülür (kısmi iadeler korunur)."""
    payload = payload or {}
    limit = int(payload.get("limit", 5000) or 5000)
    recomputed, failed = 0, 0
    errors = []
    async for gp in db.gider_pusulasi.find({"source": "site"}, {"_id": 0, "return_id": 1}).limit(limit):
        rid = gp.get("return_id")
        if not rid:
            continue
        try:
            # Orijinal seçimi koru: payload verilmez → endpoint approved_items/tam iadeye düşer.
            await site_return_gider_pusulasi(rid, payload=None, current_user=current_user)
            recomputed += 1
        except HTTPException as he:
            failed += 1
            errors.append({"return_id": rid, "detail": str(he.detail)})
        except Exception as e:
            failed += 1
            errors.append({"return_id": rid, "detail": str(e)[:120]})
    return {"success": True, "recomputed": recomputed, "failed": failed, "errors": errors[:50]}


@router.post("/returns/vouchers/set-number")
async def set_voucher_number(payload: dict = Body(...),
                             current_user: dict = Depends(require_permission("returns.expense_note"))):
    """Kesilmiş bir gider pusulasının SERİ/TAKİP numarasını elle değiştirir.
    Anahtar: return_id (site) | claim_id (Trendyol/HB) | order_number (site fallback).
    Numara idempotenttir; bağlı sipariş/claim üzerindeki gider_pusulasi_no da güncellenir."""
    return_id = str(payload.get("return_id") or "").strip()
    claim_id = str(payload.get("claim_id") or "").strip()
    order_number = str(payload.get("order_number") or "").strip()
    new_no = str(payload.get("display_number") or payload.get("number") or "").strip()
    if not new_no:
        raise HTTPException(status_code=400, detail="Numara boş olamaz")
    if return_id:
        q = {"return_id": return_id}
    elif claim_id:
        q = {"claim_id": claim_id}
    elif order_number:
        q = {"order_number": order_number}
    else:
        raise HTTPException(status_code=400, detail="return_id / claim_id / order_number gerekli")
    gp = await db.gider_pusulasi.find_one(q, {"_id": 0, "return_id": 1, "claim_id": 1, "order_number": 1})
    if not gp:
        raise HTTPException(status_code=404, detail="Gider pusulası bulunamadı")
    await db.gider_pusulasi.update_one(q, {"$set": {"display_number": new_no}})
    # Bağlı sipariş / claim üzerindeki gider_pusulasi_no'yu da güncelle (panelde tutarlı görünür).
    _rid = gp.get("return_id")
    if _rid:
        _r = await db.customer_returns.find_one({"id": _rid}, {"_id": 0, "order_id": 1})
        if _r and _r.get("order_id"):
            await db.orders.update_one({"id": _r["order_id"]}, {"$set": {"gider_pusulasi_no": new_no}})
    if gp.get("claim_id"):
        await db.trendyol_claims.update_one({"claim_id": gp["claim_id"]}, {"$set": {"gider_pusulasi_no": new_no}})
    if gp.get("order_number"):
        await db.orders.update_one({"order_number": gp["order_number"]}, {"$set": {"gider_pusulasi_no": new_no}})
    return {"success": True, "display_number": new_no}


@router.post("/returns/gider-pusulasi/clear-numbers")
async def clear_gider_pusulasi_numbers(payload: dict = Body(default={}),
                                       current_user: dict = Depends(require_permission("returns.expense_note"))):
    """TÜM eski gider pusulası koçan numaralarını temizler (kullanıcı: 'eski koçan
    numaralarını sil, ben yeni numara atayacağım'). Numara previous_numbers'a taşınır
    (muhasebe izi kaybolmaz); pusula belgesi ve TUTARLAR aynen durur, yalnız numara
    boşalır. Numarası boşalan iadeler toplu kesim (gp-bulk-range) havuzuna yeniden
    girer ve yeni sıralı koçan numarası alır. dry_run=true → yalnız sayım döner."""
    dry = bool((payload or {}).get("dry_run"))
    q = {"$or": [{"display_number": {"$exists": True, "$nin": ["", None]}},
                 {"number": {"$exists": True, "$ne": None}}]}
    total = await db.gider_pusulasi.count_documents(q)
    if dry:
        sample = await db.gider_pusulasi.find(
            q, {"_id": 0, "display_number": 1, "order_number": 1, "claim_id": 1, "return_id": 1}
        ).limit(10).to_list(10)
        return {"dry_run": True, "temizlenecek": total, "ornek": sample}
    cleared = 0
    async for g in db.gider_pusulasi.find(q, {"_id": 1, "display_number": 1}):
        upd = {"$unset": {"number": "", "display_number": ""}}
        old = str(g.get("display_number") or "").strip()
        if old:
            upd["$addToSet"] = {"previous_numbers": old}
        await db.gider_pusulasi.update_one({"_id": g["_id"]}, upd)
        cleared += 1
    # Bağlı kayıtlardaki numara/bayrakları da sıfırla → panel tutarlı, havuz yeniden dolar.
    r1 = await db.trendyol_claims.update_many(
        {"$or": [{"has_gider_pusulasi": True},
                 {"gider_pusulasi_no": {"$exists": True, "$nin": ["", None]}}]},
        {"$unset": {"has_gider_pusulasi": "", "gider_pusulasi_no": ""}})
    r2 = await db.customer_returns.update_many(
        {"$or": [{"has_gider_pusulasi": True},
                 {"gider_pusulasi_no": {"$exists": True, "$nin": ["", None]}}]},
        {"$unset": {"has_gider_pusulasi": "", "gider_pusulasi_no": ""}})
    r3 = await db.orders.update_many(
        {"gider_pusulasi_no": {"$exists": True, "$nin": ["", None]}},
        {"$unset": {"gider_pusulasi_no": ""}})
    return {"success": True, "pusula_temizlenen": cleared,
            "claim_temizlenen": r1.modified_count,
            "site_iade_temizlenen": r2.modified_count,
            "siparis_temizlenen": r3.modified_count}


@router.get("/returns/gider-pusulasi/print-range")
async def gider_pusulasi_print_range(
    from_no: str, to_no: str,
    current_user: dict = Depends(require_permission("returns.expense_note")),
):
    """Verilen KOÇAN NUMARASI ARALIĞINDAKİ (display_number) KESİLMİŞ gider pusulalarını
    yazdırma için döndürür (Kadir: yazıcıda kağıt sıkıştı — 087312–087608 arasını tekrar
    yazdır). Aralık DAHİL; numaraya göre artan sıralı. Yeni pusula OLUŞTURMAZ, mevcutları
    getirir → istenen aralık baştan değil tam o aralıktan basılır."""
    await _guard_switch("freeze_exports")   # ACİL ANAHTAR: toplu belge çıktısı donduruldu → 423
    _f = str(from_no or "").strip()
    _t = str(to_no or "").strip()
    if not _f or not _t:
        raise HTTPException(status_code=400, detail="from_no ve to_no gerekli")
    # 6 haneye pad (kullanıcı 87312 veya 087312 girse de aynı çalışsın); string aralığı 6-haneli
    # sıfır-dolgulu numaralarda leksikografik = sayısal.
    if _f.isdigit():
        _f = _f.zfill(6)
    if _t.isdigit():
        _t = _t.zfill(6)
    if _f > _t:
        _f, _t = _t, _f
    docs = []
    async for gp in db.gider_pusulasi.find(
            {"display_number": {"$gte": _f, "$lte": _t, "$nin": ["", None]}},
            {"_id": 0}).sort("display_number", 1):
        docs.append({**gp, "assigned_no": gp.get("display_number")})
    try:  # Güvenlik olayı: toplu belge çıktısı (denetim izi + süper admin uyarısı; asla istisna fırlatmaz)
        from security_switches import emit_security_event as _sec_emit
        await _sec_emit("gider_pusulasi_print_export", current_user,
                        {"export": "gider_pusulasi.print_range", "rows": len(docs)})
    except Exception:
        pass
    return {"success": True, "count": len(docs), "from_no": _f, "to_no": _t, "pusulalar": docs}


# ============================================================================
# Madde 3 — İade Ödemesi (refunded) — SADECE returns.refund_pay yetkisi (P6)
# ============================================================================

@router.post("/returns/{return_id}/refund-pay")
async def refund_pay_return(return_id: str, request: Request, payload: Optional[dict] = Body(default=None),
                            current_user: dict = Depends(require_permission("returns.refund_pay"))):
    """İade bedelinin ödendiğini işaretler. Sadece 'returns.refund_pay' yetkisi (örn. merve).
    Durumu 'refunded' yapar, kim/ne zaman/yöntem loglanır, müşteriye bildirim gönderir."""
    await _guard_switch("freeze_refunds")   # ACİL ANAHTAR: iadeler donduruldu → 423
    rec = await db.customer_returns.find_one({"id": return_id}, {"_id": 0})
    if not rec:
        raise HTTPException(status_code=404, detail="İade bulunamadı")
    if rec.get("status") == "refunded":
        return {"success": True, "status": "refunded", "already": True}
    if rec.get("status") in ("cancelled", "rejected"):
        raise HTTPException(status_code=400, detail="Bu iade kapanmış; ödeme yapılamaz.")
    order = await db.orders.find_one({"id": rec.get("order_id")}, {"_id": 0}) or {}
    # COM11 / FIN-E03: AÇIK ödeme itirazı varken iade ödemesi işaretlenmez (süper admin gerekçeli istisna).
    from .chargebacks import assert_refund_allowed
    _cb_gate = await assert_refund_allowed(rec.get("order_id"), current_user, payload,
                                           action="returns.refund_pay", request=request)
    now_iso = datetime.now(timezone.utc).isoformat()
    method = (payload or {}).get("method") or ""  # havale | iyzico | nakit ...
    amount = rec.get("refund_amount")
    if (payload or {}).get("amount") not in (None, ""):
        try:
            amount = _round2((payload or {}).get("amount"))
        except Exception:
            raise HTTPException(status_code=400, detail="Geçersiz tutar.")
    # İADE TAVANI (denetim 2026-10): işaretlenen iade ödemesi, siparişte GERÇEKTEN tahsil
    # edilen nakdi (taksitte vade farkı dahil) — o sipariş için zaten kayıtlı iadeler düşülerek —
    # AŞAMAZ. Kayıtlı iadeler: (a) kart iadeleri order.refunds[] status done/pending/unknown,
    # (b) aynı siparişin DİĞER iade kayıtlarında elle işaretlenmiş (havale/nakit…) ödemeler.
    # method='iyzico' ise bu işaret, zaten (a)'da kayıtlı kart iadesinin MUHASEBE kaydıdır →
    # (a) tekrar düşülmez (çift sayım olmasın); iyzico iadesi kendi ucunda tahsilat tavanlıdır.
    try:
        _amt_f = float(amount) if amount not in (None, "") else None
    except Exception:
        raise HTTPException(status_code=400, detail="Geçersiz tutar.")
    if _amt_f is not None and _amt_f < 0:
        raise HTTPException(status_code=400, detail="İade tutarı negatif olamaz.")
    if _amt_f is not None and order:
        _vf_c, _charged_c, _inst_c = _order_vade_farki(order)
        _base_c = _round2(order.get("total") or 0)
        _captured = _charged_c if _charged_c > _base_c + 0.01 else _base_c
        _card_refunded = 0.0
        for _x in (order.get("refunds") or []):
            if str((_x or {}).get("status") or "") in _REFUND_COUNTED_STATUSES:
                try:
                    _card_refunded += float(_x.get("amount") or _x.get("net_refund") or 0)
                except Exception:
                    pass
        _other_manual = 0.0
        async for _r in db.customer_returns.find(
                {"order_id": rec.get("order_id"), "id": {"$ne": return_id},
                 "status": {"$in": ["refunded", "partial_refunded"]}},
                {"_id": 0, "refund_payment": 1, "refund_amount": 1}):
            _rp = _r.get("refund_payment") or {}
            if str(_rp.get("method") or "").lower() == "iyzico":
                continue  # kart iadesi → zaten order.refunds'ta sayıldı
            try:
                _other_manual += float(_rp.get("amount") if _rp.get("amount") not in (None, "")
                                       else (_r.get("refund_amount") or 0))
            except Exception:
                pass
        _already_c = _round2(_other_manual + (0.0 if str(method).lower() == "iyzico" else _card_refunded))
        _limit = _round2(max(0.0, _captured - _already_c))
        if _amt_f > _limit + 0.01:
            raise HTTPException(status_code=400, detail=(
                f"İade tutarı izin verilen tavanı aşıyor: tahsil edilen ₺{_captured:.2f}, "
                f"bu sipariş için kayıtlı iadeler ₺{_already_c:.2f}, en fazla ₺{_limit:.2f} işaretlenebilir "
                f"(istenen ₺{_amt_f:.2f})."))
    payment = {
        "by": current_user.get("email") or current_user.get("id"),
        "at": now_iso, "method": method, "amount": amount,
        "reference": str((payload or {}).get("reference") or "").strip(),
    }
    if _cb_gate.get("override"):
        payment["chargeback_override_reason"] = _cb_gate.get("reason")
    # KISMİ İADE (denetim 2026-10): onay kaydı kısmi ise (refund_breakdown.is_partial) sipariş
    # 'partial_refunded' olur — tam 'refunded' değil (panel 'Kısmi İade Yapıldı' gösterir).
    _is_partial_ret = bool((rec.get("refund_breakdown") or {}).get("is_partial"))
    _ret_status = "partial_refunded" if _is_partial_ret else "refunded"

    async def _apply():
        await db.customer_returns.update_one({"id": return_id}, {"$set": {
            "status": "refunded", "refund_payment": payment,
            "refund_amount": amount if amount is not None else rec.get("refund_amount"),
            "updated_at": now_iso,
        }})
        await db.orders.update_one({"id": rec.get("order_id")}, {"$set": {
            "status": _ret_status, "return_request.status": _ret_status, "updated_at": now_iso,
            "refund_paid_at": now_iso,
        }})

    # PARA ÇIKIŞI → FAIL-CLOSED denetim: intent yazılamazsa iade 'ödendi' işaretlenmez (503).
    from activity_audit import run_audited
    await run_audited(
        db, action="returns.refund_pay", entity_type="customer_return", entity_id=return_id,
        perform=_apply, before={"status": rec.get("status")}, after={"status": "refunded"},
        current_user=current_user, request=request, source="admin.returns",
        metadata={"order_id": rec.get("order_id"), "order_number": rec.get("order_number", ""),
                  "return_id": return_id, "amount": amount, "method": method, "order_status": _ret_status})
    await _log_order_event(rec.get("order_id"), "return",
                           f"İade ödemesi işaretlendi (₺{(_amt_f or 0):.2f}, {method or 'yöntem yok'})",
                           current_user, {"return_id": return_id, "amount": amount, "method": method,
                                          "order_status": _ret_status},
                           order_number=rec.get("order_number", ""))

    import asyncio as _aio
    async def _n():
        try:
            import sys, os as _os
            sys.path.insert(0, _os.path.dirname(_os.path.dirname(__file__)))
            from notification_service import send_notification
            from order_statuses import get_status_config, customer_label_for
            cfg = await get_status_config(db)
            nz = (cfg.get("notify") or {}).get(_ret_status) or {}
            ch = [c for c in ("sms", "email") if nz.get(c)]
            if not ch:
                return
            addr = (order or {}).get("shipping_address") or {}
            await send_notification(
                db, ("order_partial_refunded" if _is_partial_ret else "order_refunded"),
                to_phone=addr.get("phone") or order.get("phone"),
                to_email=addr.get("email") or order.get("email"),
                variables=await _order_notify_vars(order, order_number=rec.get("order_number", ""), return_code=rec.get("return_code", ""), refund_amount=f"{(amount or 0):.2f}", status_label=customer_label_for(_ret_status)),
                channels=ch,
            )
        except Exception as e:
            logger.warning(f"refund-pay notif failed: {e}")
    _spawn(_n())

    return {"success": True, "status": "refunded", "order_status": _ret_status, "amount": amount}


# ============================================================================
# Aşama 6 — Reddedilen iadeyi müşteriye GERİ gönder (reship)
# ============================================================================

@router.post("/returns/{return_id}/reship")
async def reship_return(return_id: str, payload: Optional[dict] = Body(default=None),
                        current_user: dict = Depends(require_permission("returns.cargo_rebook"))):
    """Aşama 6: Reddedilen iadede ürünü müşteriye GERİ gönderir (yeni kargo barkodu, alıcı = müşteri).
    Reddetme anında reship yapılmadıysa ya da tekrar gönderim gerekiyorsa kullanılır."""
    await _guard_restore_mode()   # ACİL ANAHTAR: geri yükleme modu → 423
    rec = await db.customer_returns.find_one({"id": return_id}, {"_id": 0})
    if not rec:
        raise HTTPException(status_code=404, detail="İade bulunamadı")
    if rec.get("status") != "rejected":
        raise HTTPException(status_code=400, detail="Yalnızca reddedilmiş iadeler geri gönderilebilir.")
    order = await db.orders.find_one({"id": rec.get("order_id")}, {"_id": 0}) or {}
    addr = order.get("shipping_address") or {}
    recipient = {
        "name": addr.get("full_name") or f"{addr.get('first_name','')} {addr.get('last_name','')}".strip() or "Müşteri",
        "phone": addr.get("phone", ""), "city": addr.get("city", ""),
        "district": addr.get("district", ""), "address": addr.get("address", ""),
    }
    ref = f"RET{rec.get('order_number','')}{generate_id()[:6]}".replace(" ", "")
    _rgn, _mng = await _create_return_shipment(ref, order.get("total") or 0, "IADE RED - geri gonderim", recipient)
    reship_code = _rgn or ref
    now_iso = datetime.now(timezone.utc).isoformat()
    await db.customer_returns.update_one({"id": return_id}, {"$set": {
        "reship_code": reship_code, "reshipped_at": now_iso,
        "reshipped_by": current_user.get("email") or current_user.get("id"),
        "updated_at": now_iso,
    }})
    await _log_order_event(rec.get("order_id"), "return", f"Reddedilen iade geri gönderildi: {reship_code}", current_user,
                           {"return_id": return_id, "reship_code": reship_code},
                           order_number=rec.get("order_number", ""))
    return {"success": True, "reship_code": reship_code}
