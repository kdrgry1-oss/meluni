"""
KVKK SELF-SERVICE (BL-034, PRIV02-04) — veri taşınabilirliği + hesap silme + silme mezar taşları.

Müşteri uçları (oturum gerekli):
  GET  /api/kvkk/my-data          → kişisel verilerimin JSON dökümü (24 saatte en çok 3; denetim kaydı)
  POST /api/kvkk/delete-request   → hesabımı sil (mevcut şifre tekrar sorulur)

Admin uçları (yetki: admin.compliance — süper admin geçer):
  GET  /api/admin/kvkk/requests                 → KVKK talepleri listesi
  POST /api/admin/kvkk/requests/{id}/apply      → bekleyen silme talebini uygula (açık sipariş bitince)
  POST /api/admin/kvkk/requests/{id}/close      → talebi kapat (not ile)
  POST /api/admin/kvkk/reapply-tombstones       → (süper admin) mezar taşlarını yeniden uygula (yedekten dönüş sonrası)

SİLMEDE NE OLUR (anonymize_user — üye paneli, admin "Üyeyi sil" ve yeniden-uygulama işi AYNI fonksiyonu kullanır):
  HEMEN SİLİNİR / ANONİMLEŞİR (yasal saklama gerektirmeyen veriler):
    • users: belge, kişisel alan içermeyen bir İSKELETE indirgenir (ad "Silinmiş Üye", e-posta
      deleted-<id>@deleted.invalid, telefon/doğum tarihi/boy-kilo/sosyal bağlantılar/MFA silinir,
      şifre kullanılamaz, is_active=False, token_version+1 → TÜM oturumlar düşer).
    • addresses, favorites, cart_sessions, user_devices (push token'ları), password_reset_otps,
      mfa_sms_codes, stock_notifications (e-posta), newsletter_subscribers (e-posta) → SİLİNİR.
    • reviews: yorum sahibi adı "Silinmiş Üye" olur (yorum metni ürün sayfası içeriği olarak kalır).
    • referrals: davet kayıtlarındaki e-posta alanları boşaltılır.
    • Pazarlama izinleri: users.accepts_marketing=False; İYS'ye EPOSTA/MESAJ için RET kaydı yazılır
      (record_consent — RET kaydı İYS mevzuatı gereği iletişim adresiyle SAKLANIR; ret kanıtıdır).
  SAKLANIR AMA KOPARILIR (yasal saklama — VUK md. 253 [5 yıl], TTK md. 82 [10 yıl], Mesafeli
  Sözleşmeler Yönetmeliği md. 18 [3 yıl]):
    • orders / orders_deleted: sipariş no, tarih, kalemler, tutarlar, ödeme/iyzico kayıtları, fatura
      no, kargo takip, TESLİMAT/FATURA ADI-ADRESİ ve billing_info (firma adı, VKN/TCKN, vergi dairesi)
      KORUNUR — düzenlenmiş faturanın içeriğidir ve vergi denetiminde ibrazı zorunludur.
      İletişim alanları (telefon, e-posta — sipariş, teslimat ve fatura adresi içinde), hediye notu,
      pazarlama atfı (attribution) ve CAPI user_data MASKELENİR/SİLİNİR. user_id takma ad (pseudonym)
      olarak kalır; bağlı kişi belgesi artık kimlik içermez. customer_ip/user_agent sözleşme onayı ve
      ödeme itirazı (chargeback) delili olarak saklama süresince tutulur. Sipariş belgesine
      kvkk_detached=True + kvkk_legal_hold_until (silme anı + 10 yıl) yazılır.
    • customer_returns: iade kalemleri/tutarları ve (şifreli) iade IBAN'ı iade ödemesinin muhasebe
      kanıtı olarak kalır.
    • iyzico/Doğan tarafındaki ödeme ve e-fatura kayıtları ilgili sağlayıcıda kalır (bizim kontrolümüzde değil).
    • auth_audit_logs / audit_logs: güvenlik denetim izi (≥12 ay politika) korunur.
  MEZAR TAŞI (deletion_tombstones): yalnız e-posta/telefonun ALAN-AYRIMLI HMAC'i + user_id + zaman.
    Düz kişisel veri İÇERMEZ. Gizli R2 arşivine de (kvkk-tombstones/<id>.json) yansıtılır →
    veritabanı yedekten dönülse bile reapply_deletion_tombstones() silmeleri yeniden uygular ve
    pazarlama izni geri gelmez (silme tarihinden SONRA verilmiş açık rıza hariç).
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials

from .deps import (db, logger, require_auth, require_permission, require_super_admin, generate_id,
                   verify_password, write_audit_log, client_ip_from_request, is_recent_auth, limiter)
from .deps import security as _bearer

router = APIRouter(prefix="/kvkk", tags=["KVKK"])
admin_router = APIRouter(prefix="/admin/kvkk", tags=["KVKK Admin"])

_EXPORT_LIMIT_PER_DAY = 3
_LEGAL_HOLD_YEARS = 10
_ARCHIVE_PREFIX = "kvkk-tombstones/"
_DELETED_NAME = "Silinmiş"
_DELETED_SURNAME = "Üye"

# Silmeyi ERTELEYEN sipariş durumları: teslimat/iade/para iadesi sürerken iletişim bilgisi silinirse
# kargo, iade ve para iadesi yürütülemez. Bu durumda talep "pending_open_orders" olarak açılır.
_OPEN_STATUSES = [
    "pending", "awaiting_payment", "payment_notified", "confirmed", "processing", "preparing",
    "ready_to_ship", "shipped", "in_transit", "out_for_delivery", "undelivered", "cancel_requested",
    "return_requested", "return_approved", "return_in_transit",
]

# Kullanıcı iskeletinde KALAN alanlar (kişisel veri içermez).
_USER_KEEP_FIELDS = (
    "id", "created_at", "is_admin", "role", "acquisition_source",
    "cached_orders", "cached_spent", "cached_gross_spent", "cached_returns",
    "cached_returns_amount", "cached_last_order", "cached_segment",
)

# Sipariş belgesinde MASKELENEN iletişim alanları (üst düzey) ve adres içi alanlar.
_ORDER_TOP_CONTACT = ("email", "phone", "customer_email", "customer_phone", "user_email",
                      "user_phone", "buyer_email", "buyer_phone", "gift_note")
_ORDER_ADDR_CONTACT = ("phone", "email", "phone_raw", "email_raw")
_ORDER_UNSET = ("attribution", "user_data")

# Kullanıcı dökümünde ASLA verilmeyen (güvenlik) alanlar.
_PROFILE_SECRET_RX = re.compile(r"(password|secret|token|mfa|otp|totp|hash|lock|fail|recovery|backup_code)", re.I)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _now().isoformat()


def _norm_email(e) -> str:
    return str(e or "").strip().lower()


def _norm_phone(p) -> str:
    d = re.sub(r"\D", "", str(p or ""))
    return d[-10:] if len(d) >= 10 else ""


def _mask_email(e: str) -> str:
    e = _norm_email(e)
    if "@" not in e:
        return ""
    local, _, dom = e.partition("@")
    return (local[:2] + "***@" + dom) if len(local) > 2 else ("***@" + dom)


# ─────────────────────────── Mezar taşı HMAC anahtarları ───────────────────────────
# Alan-ayrımlı anahtar (orders._manual_invoice_link_key deseni): kök sır DOĞRUDAN kullanılmaz.
# Sıra: KVKK_TOMBSTONE_SECRET (≥32, önerilen — JWT/anahtar rotasyonundan bağımsız kalır) →
# SECRETS_MASTER_KEY'den türetilmiş → JWT_SECRET'tan türetilmiş. Her mezar taşı TÜM mevcut
# anahtarlarla hash'lenir; eşleştirme de hepsiyle yapılır (rotasyon sonrası da bulunur).
def _tombstone_keys() -> list:
    keys = []
    env = (os.environ.get("KVKK_TOMBSTONE_SECRET") or "").strip()
    if len(env) >= 32:
        keys.append(("env", env.encode()))
    smk = (os.environ.get("SECRETS_MASTER_KEY") or "").strip()
    if smk:
        keys.append(("smk", hmac.new(smk.encode(), b"meluni:kvkk-tombstone:v1", hashlib.sha256).digest()))
    try:
        from .deps import JWT_SECRET as _jwt
        if _jwt:
            keys.append(("jwt", hmac.new(str(_jwt).encode(), b"meluni:kvkk-tombstone:v1", hashlib.sha256).digest()))
    except Exception:
        pass
    return keys


def _hmac_values(kind: str, value: str) -> dict:
    """{key_id: hex} — boş değer için {}."""
    if not value:
        return {}
    msg = f"meluni:kvkk-tombstone:{kind}:v1|{value}".encode()
    return {kid: hmac.new(k, msg, hashlib.sha256).hexdigest() for kid, k in _tombstone_keys()}


def _tombstone_or_query(email: str = "", phone: str = "") -> list:
    ors = []
    for kid, h in _hmac_values("email", _norm_email(email)).items():
        ors.append({f"email_hmacs.{kid}": h})
    for kid, h in _hmac_values("phone", _norm_phone(phone)).items():
        ors.append({f"phone_hmacs.{kid}": h})
    return ors


async def find_deletion_tombstone(email: Optional[str] = None, phone: Optional[str] = None) -> Optional[dict]:
    """Bu e-posta/telefon daha önce KVKK ile silinmiş bir hesaba mı ait? (kayıt / bülten kontrolü)
    Dönüş: en yeni mezar taşı (yalnız hash + zaman içerir) ya da None."""
    ors = _tombstone_or_query(email or "", phone or "")
    if not ors:
        return None
    try:
        return await db.deletion_tombstones.find_one({"$or": ors}, {"_id": 0}, sort=[("deleted_at", -1)])
    except Exception:
        return None


async def note_marketing_reoptin(email: Optional[str] = None, phone: Optional[str] = None,
                                 source: str = "") -> int:
    """Silinmiş bir kişi AÇIKÇA yeniden pazarlama izni verdiğinde çağrılır (bülten aboneliği / izin
    kutusu). Mezar taşına işaret düşer → yeniden-uygulama işi bu yeni izni GERİ ALMAZ."""
    ors = _tombstone_or_query(email or "", phone or "")
    if not ors:
        return 0
    try:
        res = await db.deletion_tombstones.update_many(
            {"$or": ors}, {"$set": {"marketing_reoptin_at": _now_iso(), "marketing_reoptin_source": source[:40]}})
        return int(res.modified_count or 0)
    except Exception:
        return 0


def _archive_mirror(t: dict) -> None:
    """Mezar taşını gizli R2 arşivine yaz (yalnız hash/zaman/takma-ad id). Senkron — to_thread ile çağır."""
    from services import r2_storage as r2
    if not r2.archive_enabled():
        return
    keep = {k: t.get(k) for k in ("id", "user_ids", "email_hmacs", "phone_hmacs", "deleted_at",
                                   "request_id", "source", "status")}
    r2.archive_put(f"{_ARCHIVE_PREFIX}{t['id']}.json", json.dumps(keep, ensure_ascii=False).encode("utf-8"),
                   "application/json")


def _archive_list_and_load() -> list:
    """Arşivdeki TÜM mezar taşlarını oku (yedekten dönüş sonrası DB'de kaybolanları geri getirmek için)."""
    from services import r2_storage as r2
    if not r2.archive_enabled():
        return []
    out = []
    client = r2._get_client()
    token = None
    while True:
        kw = {"Bucket": r2.archive_bucket(), "Prefix": _ARCHIVE_PREFIX, "MaxKeys": 1000}
        if token:
            kw["ContinuationToken"] = token
        resp = client.list_objects_v2(**kw)
        for obj in resp.get("Contents") or []:
            data, _ = r2.archive_get(obj["Key"])
            if not data:
                continue
            try:
                d = json.loads(data.decode("utf-8"))
                if isinstance(d, dict) and d.get("id"):
                    out.append(d)
            except Exception:
                continue
        if resp.get("IsTruncated"):
            token = resp.get("NextContinuationToken")
        else:
            break
    return out


# ─────────────────────────── Sahiplik / açık sipariş ───────────────────────────
def _owner_clauses(user: dict) -> list:
    """customer._owner_or_clauses ile AYNI kural (misafir siparişi yalnız DOĞRULANMIŞ e-postayla)."""
    try:
        from .customer import _owner_or_clauses
        return _owner_or_clauses(user)
    except Exception:
        return [{"user_id": user.get("id")}]


async def open_order_count(user: dict) -> int:
    """Silmeyi erteleyen siparişler: teslimat/iade/para iadesi sürenler + son 45 günde teslim edilmiş
    ama faturası henüz kesilmemiş site siparişleri (fatura e-postası/adres bilgisi gerekebilir)."""
    cut = (_now() - timedelta(days=45)).isoformat()
    q = {"$and": [
        {"$or": _owner_clauses(user)},
        {"$or": [
            {"status": {"$in": _OPEN_STATUSES}},
            {"refund_pending": True},
            {"$and": [{"status": {"$in": ["delivered", "completed"]}},
                      {"invoice_issued": {"$ne": True}},
                      {"invoice_number": {"$in": [None, ""]}},
                      {"platform": {"$in": [None, "", "meluni", "site"]}},
                      {"created_at": {"$gte": cut}}]},
        ]},
    ]}
    try:
        return await db.orders.count_documents(q)
    except Exception:
        return 0


# ─────────────────────────── Anonimleştirme (tek yetkili yol) ───────────────────────────
async def _detach_orders(coll, user: dict, request_id: str, now_iso: str, hold_until: str) -> int:
    n = 0
    q = {"$or": _owner_clauses(user)}
    async for o in coll.find(q, {"_id": 1, "shipping_address": 1, "billing_address": 1,
                                 **{k: 1 for k in _ORDER_TOP_CONTACT}, **{k: 1 for k in _ORDER_UNSET}}):
        s = {"kvkk_detached": True, "kvkk_detached_at": now_iso, "kvkk_request_id": request_id,
             "kvkk_legal_hold_until": hold_until}
        for k in _ORDER_TOP_CONTACT:
            if o.get(k):
                s[k] = ""
        for addr in ("shipping_address", "billing_address"):
            a = o.get(addr)
            if isinstance(a, dict):
                for k in _ORDER_ADDR_CONTACT:
                    if a.get(k):
                        s[f"{addr}.{k}"] = ""
        ops = {"$set": s}
        uns = {k: "" for k in _ORDER_UNSET if k in o}
        if uns:
            ops["$unset"] = uns
        await coll.update_one({"_id": o["_id"]}, ops)
        n += 1
    return n


async def anonymize_user(user: dict, *, source: str, request_id: str, actor: Optional[dict] = None,
                         write_tombstone: bool = True, send_iys_ret: bool = True,
                         newsletter_keep_after: Optional[str] = None) -> dict:
    """KVKK silme — yasal saklama dışındaki verileri siler/anonimleştirir, saklanması zorunlu sipariş/
    fatura kayıtlarını kişiden KOPARIR (yukarıdaki modül açıklaması). İdempotent: tekrar çağrı zararsız.
    `user` TAM kullanıcı belgesi olmalı (e-posta/telefon mezar taşı + İYS RET için gerekir).
    newsletter_keep_after (ISO, yalnız yeniden-uygulama): bu andan SONRA verilmiş bülten rızası SİLİNMEZ."""
    uid = user.get("id")
    if not uid:
        raise ValueError("user.id gerekli")
    if user.get("is_admin") is True or user.get("is_super_admin") is True:
        raise HTTPException(status_code=403, detail="Personel hesapları bu yolla silinemez.")
    # KVKK: üye telefonu at-rest şifreli olabilir — mezar taşı HMAC'i, İYS RET ve sipariş eşleşmesi
    # için gerçek değer bellekte açılır (DB'ye düz yazılmaz).
    if user.get("phone_enc") and not user.get("phone"):
        from security.contact_pii import open_field as _open_contact
        user = dict(user)
        user["phone"] = _open_contact(user, "phone")
    email = _norm_email(user.get("email"))
    if email.endswith("@deleted.invalid"):
        email = ""
    phone_raw = str(user.get("phone") or "")
    phone10 = _norm_phone(phone_raw)
    now_iso = _now_iso()
    hold_until = (_now() + timedelta(days=365 * _LEGAL_HOLD_YEARS)).isoformat()
    summary: dict = {}

    # 1) Mezar taşı ÖNCE (süreç yarıda kesilirse yeniden-uygulama işi tamamlar).
    tomb_id = None
    if write_tombstone:
        tomb_id = generate_id()
        t = {"id": tomb_id, "user_ids": [uid], "email_hmacs": _hmac_values("email", email),
             "phone_hmacs": _hmac_values("phone", phone10), "deleted_at": now_iso,
             "request_id": request_id, "source": source, "status": "in_progress",
             "marketing_reoptin_at": None}
        await db.deletion_tombstones.insert_one(dict(t))
        try:
            await asyncio.to_thread(_archive_mirror, t)
            await db.deletion_tombstones.update_one({"id": tomb_id}, {"$set": {"archived_at": _now_iso()}})
        except Exception as e:
            logger.warning(f"[kvkk] mezar taşı arşive yazılamadı: {type(e).__name__}")

    # 2) İYS RET (düz adres GEREKİR — silmeden önce). RET kaydı yasal ret kanıtı olarak saklanır.
    if send_iys_ret and (email or phone10):
        try:
            from .iys import record_consent
            chans = (["EPOSTA"] if email else []) + (["MESAJ"] if phone10 else [])
            await record_consent(email, phone_raw, chans, status="RET", source="HS_WEB", user_id=uid)
            summary["iys_ret"] = chans
        except Exception as e:
            logger.warning(f"[kvkk] İYS RET yazılamadı: {type(e).__name__}")

    # 3) Saklanması zorunlu kayıtlar: koparılır + iletişim maskelenir.
    summary["orders_detached"] = await _detach_orders(db.orders, user, request_id, now_iso, hold_until)
    try:
        summary["deleted_orders_detached"] = await _detach_orders(db.orders_deleted, user, request_id,
                                                                  now_iso, hold_until)
    except Exception:
        pass

    # 4) Yasal saklama gerektirmeyenler: SİLİNİR.
    async def _del(coll, q, key):
        try:
            r = await coll.delete_many(q)
            summary[key] = int(r.deleted_count or 0)
        except Exception as e:
            logger.warning(f"[kvkk] {key} silinemedi: {type(e).__name__}")

    await _del(db.addresses, {"user_id": uid}, "addresses")
    await _del(db.favorites, {"user_id": uid}, "favorites")
    await _del(db.user_devices, {"user_id": uid}, "devices")
    await _del(db.cart_sessions, {"$or": [{"user_id": uid}] + ([{"email": email}] if email else [])}, "carts")
    await _del(db.password_reset_otps, {"$or": [{"user_id": uid}] + ([{"email": email}] if email else [])},
               "reset_codes")
    await _del(db.mfa_sms_codes, {"user_id": uid}, "mfa_codes")
    if email:
        await _del(db.stock_notifications, {"email": email}, "stock_notifications")
        _nl_q: dict = {"email": email}
        if newsletter_keep_after:
            # Yeniden uygulama (yedekten dönüş): silmeden SONRA verilmiş açık rıza korunur — yalnız
            # silme anından önceki (geri yüklenmiş) abonelikler silinir. Kural reapply döngüsüyle aynı:
            # rıza zamanı = consent_at, yoksa created_at.
            _nl_q["$nor"] = [{"consent_at": {"$gt": newsletter_keep_after}},
                             {"consent_at": {"$in": [None, ""]}, "created_at": {"$gt": newsletter_keep_after}}]
        await _del(db.newsletter_subscribers, _nl_q, "newsletter")
    try:
        r = await db.reviews.update_many({"user_id": uid}, {"$set": {"user_name": f"{_DELETED_NAME} {_DELETED_SURNAME}",
                                                                      "kvkk_anonymized": True}})
        summary["reviews_anonymized"] = int(r.modified_count or 0)
    except Exception:
        pass
    try:
        await db.referrals.update_many({"referrer_user_id": uid}, {"$set": {"referrer_email": ""}})
        await db.referrals.update_many({"referee_user_id": uid}, {"$set": {"referee_email": ""}})
    except Exception:
        pass

    # 5) Kullanıcı belgesi → kişisel veri içermeyen iskelet (bilinmeyen alanlar da gider).
    cur = await db.users.find_one({"id": uid}, {"_id": 0}) or user
    skel = {k: cur.get(k) for k in _USER_KEEP_FIELDS if k in cur}
    skel.update({
        "id": uid, "is_admin": False,
        "email": f"deleted-{uid}@deleted.invalid", "first_name": _DELETED_NAME, "last_name": _DELETED_SURNAME,
        "phone": "", "password": "", "is_active": False, "email_verified": False, "phone_verified": False,
        "accepts_marketing": False, "marketing_prefs": {"email": False, "sms": False},
        "token_version": int(cur.get("token_version", 0) or 0) + 1,
        "kvkk_deleted": True, "kvkk_deleted_at": now_iso, "kvkk_request_id": request_id,
        "kvkk_delete_source": source, "updated_at": now_iso,
    })
    await db.users.replace_one({"id": uid}, skel)
    summary["user_anonymized"] = True

    if tomb_id:
        await db.deletion_tombstones.update_one({"id": tomb_id}, {"$set": {"status": "applied",
                                                                          "applied_at": _now_iso()}})
    await write_audit_log("kvkk_account_deleted", user_id=(actor or {}).get("id") or uid,
                          email=(actor or {}).get("email"), success=True,
                          meta={"member_id": uid, "source": source, "request_id": request_id,
                                "summary": {k: v for k, v in summary.items() if k != "iys_ret"}})
    try:
        from security_switches import emit_security_event
        await emit_security_event("kvkk_deletion", actor or {"id": uid},
                                  {"member_id": uid, "source": source, "request_id": request_id,
                                   "fp": request_id or uid},
                                  title="KVKK hesap silme / anonimleştirme uygulandı")
    except Exception:
        pass
    return summary


# ─────────────────────────── Yedekten dönüş sonrası yeniden uygulama ───────────────────────────
async def reapply_deletion_tombstones() -> dict:
    """GECE / yedekten dönüş sonrası: (1) arşivdeki mezar taşlarını DB'ye geri getir, (2) mezar taşındaki
    user_id'ler için kişisel veri GERİ GELMİŞSE yeniden anonimleştir, koparılmamış siparişleri kopar,
    (3) silme anından ÖNCE verilmiş bülten aboneliklerini sil. Silmeden SONRA verilmiş açık rıza korunur."""
    stats = {"restored_tombstones": 0, "users_reanonymized": 0, "orders_detached": 0, "newsletter_removed": 0}
    try:
        archived = await asyncio.to_thread(_archive_list_and_load)
    except Exception as e:
        archived = []
        logger.warning(f"[kvkk] arşiv mezar taşları okunamadı: {type(e).__name__}")
    for t in archived:
        try:
            res = await db.deletion_tombstones.update_one(
                {"id": t["id"]}, {"$setOnInsert": {**t, "restored_from_archive_at": _now_iso()}}, upsert=True)
            if res.upserted_id is not None:
                stats["restored_tombstones"] += 1
        except Exception:
            continue

    email_hashes: dict = {}   # (kid, hex) -> deleted_at (en geç)
    async for t in db.deletion_tombstones.find({}, {"_id": 0}):
        deleted_at = t.get("deleted_at") or ""
        for kid, h in (t.get("email_hmacs") or {}).items():
            prev = email_hashes.get((kid, h))
            if not prev or deleted_at > prev:
                email_hashes[(kid, h)] = deleted_at
        for uid in t.get("user_ids") or []:
            try:
                u = await db.users.find_one({"id": uid}, {"_id": 0})
                if u and not u.get("kvkk_deleted") and not u.get("is_admin"):
                    # Silmeden SONRA açık rıza verilmişse İYS'ye tekrar RET gönderilmez.
                    em = _norm_email(u.get("email"))
                    fresh = bool(t.get("marketing_reoptin_at"))
                    if not fresh and em:
                        fresh = bool(await db.newsletter_subscribers.find_one(
                            {"email": em, "consent_at": {"$gt": deleted_at}}, {"_id": 1}))
                    await anonymize_user(u, source="reapply", request_id=t.get("request_id") or "",
                                         write_tombstone=False, send_iys_ret=not fresh,
                                         newsletter_keep_after=deleted_at or None)
                    stats["users_reanonymized"] += 1
                elif u:
                    # Kullanıcı iskeleti duruyor ama siparişler (kısmi geri yükleme) geri gelmiş olabilir.
                    hold = (_now() + timedelta(days=365 * _LEGAL_HOLD_YEARS)).isoformat()
                    q = {"user_id": uid, "kvkk_detached": {"$ne": True}}
                    if await db.orders.find_one(q, {"_id": 1}):
                        stats["orders_detached"] += await _detach_orders(
                            db.orders, {"id": uid}, t.get("request_id") or "", _now_iso(), hold)
            except HTTPException:
                continue
            except Exception as e:
                logger.warning(f"[kvkk] reapply user hata: {type(e).__name__}")
        await db.deletion_tombstones.update_one({"id": t["id"]}, {"$set": {"last_reapplied_at": _now_iso()}})

    if email_hashes:
        keys = _tombstone_keys()
        async for s in db.newsletter_subscribers.find({}, {"_id": 1, "email": 1, "consent_at": 1, "created_at": 1}):
            em = _norm_email(s.get("email"))
            if not em:
                continue
            msg = f"meluni:kvkk-tombstone:email:v1|{em}".encode()
            for kid, k in keys:
                d_at = email_hashes.get((kid, hmac.new(k, msg, hashlib.sha256).hexdigest()))
                if d_at is None:
                    continue
                given = str(s.get("consent_at") or s.get("created_at") or "")
                if given and given > d_at:
                    break   # silmeden SONRA verilmiş açık rıza → korunur
                await db.newsletter_subscribers.delete_one({"_id": s["_id"]})
                stats["newsletter_removed"] += 1
                break
    if any(stats.values()):
        logger.info(f"[kvkk] tombstone reapply: {stats}")
    return stats


# ─────────────────────────── Müşteri uçları ───────────────────────────
def _clean(doc: dict, drop=("_id",)) -> dict:
    return {k: v for k, v in (doc or {}).items() if k not in drop}


@router.get("/my-data")
async def kvkk_my_data(request: Request, current_user: dict = Depends(require_auth)):
    """KVKK md. 11 — kişisel verilerimin makinece okunabilir dökümü (JSON). 24 saatte en çok 3."""
    uid = current_user.get("id")
    since = (_now() - timedelta(days=1)).isoformat()
    n_recent = await db.kvkk_requests.count_documents({"user_id": uid, "type": "export",
                                                       "created_at": {"$gte": since}})
    if n_recent >= _EXPORT_LIMIT_PER_DAY:
        raise HTTPException(status_code=429, detail="Veri dökümünü 24 saatte en fazla 3 kez indirebilirsiniz. "
                                                    "Lütfen daha sonra tekrar deneyin.")
    email = _norm_email(current_user.get("email"))
    phone = str(current_user.get("phone") or "")

    profile = {k: v for k, v in current_user.items()
               if k not in ("_id",) and not _PROFILE_SECRET_RX.search(k) and not k.startswith("cached_")}

    # KVKK/güvenlik: T.C. kimlik / vergi no dökümde maskeli (IBAN ile aynı ilke — hesap ele geçirilse
    # bile tam kimlik no dışarı çıkmaz); şifreli alan/hash dökümde yer almaz.
    from security.pii import mask_doc_tax_ids as _pii_mask
    # KVKK md.11: adres defterindeki şifreli telefon/açık adres veri sahibine AÇIK verilir.
    from security.contact_pii import reveal_doc as _contact_reveal
    addresses = [_pii_mask(_contact_reveal(_clean(a), "address"), "address")
                 for a in await db.addresses.find({"user_id": uid}, {"_id": 0}).to_list(100)]

    order_fields = {"_id": 0, "id": 1, "order_number": 1, "created_at": 1, "status": 1, "payment_method": 1,
                    "payment_status": 1, "total": 1, "subtotal": 1, "shipping_cost": 1, "discount": 1,
                    "coupon_code": 1, "items": 1, "shipping_address": 1, "billing_address": 1,
                    "billing_info": 1, "cargo_provider_name": 1, "cargo_tracking_number": 1,
                    "invoice_number": 1, "delivered_at": 1, "notes": 1}
    orders = []
    order_ids = []
    async for o in db.orders.find({"$or": _owner_clauses(current_user)}, order_fields).sort("created_at", -1).limit(1000):
        order_ids.append(o.get("id"))
        o["items"] = [{"name": i.get("product_name") or i.get("name") or "", "size": i.get("size") or "",
                       "color": i.get("color") or "", "quantity": i.get("quantity") or 1,
                       "price": i.get("price") or i.get("unit_price") or 0}
                      for i in (o.get("items") or []) if isinstance(i, dict)]
        o.pop("id", None)
        _pii_mask(o)
        orders.append(o)

    from notification_service import normalize_phone_tr
    phone_n = normalize_phone_tr(phone) if phone else ""
    c_or = [{"user_id": uid}] + ([{"email": email}] if email else []) + ([{"phone": phone_n}] if phone_n else [])
    consents = [{"channels": c.get("channels"), "status": c.get("status"), "source": c.get("source"),
                 "date": c.get("consent_date") or c.get("created_at")}
                async for c in db.iys_consents.find({"$or": c_or}, {"_id": 0}).sort("created_at", 1).limit(500)]
    newsletter = [{"active": s.get("active", True), "consent": s.get("consent"), "source": s.get("source"),
                   "consent_at": s.get("consent_at"), "unsubscribed_at": s.get("unsubscribed_at")}
                  async for s in db.newsletter_subscribers.find({"email": email}, {"_id": 0})] if email else []

    fav_docs = await db.favorites.find({"user_id": uid}, {"_id": 0, "product_id": 1, "created_at": 1}).to_list(500)
    _pids = [f.get("product_id") for f in fav_docs if f.get("product_id")]
    _names = {p["id"]: p.get("name") async for p in db.products.find({"id": {"$in": _pids}}, {"_id": 0, "id": 1, "name": 1})} if _pids else {}
    favorites = [{"product_id": f.get("product_id"), "product_name": _names.get(f.get("product_id"), ""),
                  "added_at": f.get("created_at")} for f in fav_docs]

    reviews = [{"product_id": r.get("product_id"), "rating": r.get("rating"), "title": r.get("title"),
                "comment": r.get("comment"), "status": r.get("status"), "created_at": r.get("created_at")}
               async for r in db.reviews.find({"user_id": uid}, {"_id": 0}).limit(500)]

    from .orders import mask_refund_bank_info
    r_or = [{"user_id": uid}] + ([{"order_id": {"$in": [x for x in order_ids if x]}}] if order_ids else [])
    returns = [{"order_number": r.get("order_number"), "status": r.get("status"), "reason": r.get("reason"),
                "items": r.get("items"), "refund_amount": r.get("refund_amount"), "created_at": r.get("created_at"),
                "refund_bank_info": mask_refund_bank_info(r.get("refund_bank_info"))}
               async for r in db.customer_returns.find({"$or": r_or}, {"_id": 0, "barcode_png_b64": 0}).limit(500)]

    now_iso = _now_iso()
    payload = {
        "generated_at": now_iso,
        "format": "meluni-kvkk-export-v1",
        "note": ("Bu dosya KVKK md. 11 kapsamında hesabınızla ilişkili kişisel verilerin dökümüdür. "
                 "Ödeme kartı bilgileri tarafımızda saklanmaz; iade IBAN'ı ve T.C. kimlik / vergi numarası "
                 "güvenlik nedeniyle maskelidir."),
        "profile": profile,
        "addresses": addresses,
        "orders": orders,
        "marketing_consents": {"iys_records": consents, "newsletter": newsletter,
                               "profile_preference": {"accepts_marketing": bool(current_user.get("accepts_marketing")),
                                                      "marketing_prefs": current_user.get("marketing_prefs") or {}}},
        "favorites": favorites,
        "reviews": reviews,
        "returns": returns,
    }
    await db.kvkk_requests.insert_one({
        "id": generate_id(), "type": "export", "user_id": uid, "email_masked": _mask_email(email),
        "source": "self", "status": "completed", "created_at": now_iso, "completed_at": now_iso,
        "summary": {"orders": len(orders), "addresses": len(addresses), "returns": len(returns)},
    })
    await write_audit_log("kvkk_data_export", user_id=uid, email=email,
                          ip=client_ip_from_request(request), user_agent=request.headers.get("user-agent"),
                          success=True, meta={"orders": len(orders)})
    fname = f"meluni-verilerim-{_now().strftime('%Y%m%d')}.json"
    return JSONResponse(content=json.loads(json.dumps(payload, default=str)),
                        headers={"Content-Disposition": f'attachment; filename="{fname}"',
                                 "Cache-Control": "no-store"})


@router.post("/delete-request")
@(limiter.limit("5/hour") if limiter else (lambda f: f))
async def kvkk_delete_request(request: Request, payload: dict = Body(default={}),
                              credentials: HTTPAuthorizationCredentials = Depends(_bearer),
                              current_user: dict = Depends(require_auth)):
    """Hesabımı sil. Gövde: {password, confirm: true}. Şifre tanımlı olmayan (yalnız sosyal girişli)
    hesapta son 5 dk içinde yapılmış giriş kabul edilir."""
    payload = payload if isinstance(payload, dict) else {}
    uid = current_user.get("id")
    ip = client_ip_from_request(request)
    ua = request.headers.get("user-agent")
    if current_user.get("is_admin") is True or current_user.get("is_super_admin") is True:
        raise HTTPException(status_code=403, detail="Personel hesapları bu ekrandan silinemez.")
    if payload.get("confirm") is not True:
        raise HTTPException(status_code=400, detail="Silme işlemini onaylamanız gerekiyor.")
    full = await db.users.find_one({"id": uid}, {"_id": 0})
    if not full:
        raise HTTPException(status_code=404, detail="Hesap bulunamadı")
    pw = str(payload.get("password") or "")[:200]
    if full.get("password"):
        # ID3-05: şifre denemesi giriş kilidi sayaçlarına tabidir (IP limiti tek başına dağıtık
        # tahmini durdurmaz).
        from .deps import is_account_locked, register_failed_login
        _locked, _retry = await is_account_locked(full.get("email") or "", ip)
        if _locked:
            raise HTTPException(status_code=429, detail="Çok fazla hatalı deneme. Lütfen daha sonra tekrar deneyin.")
        if not pw or not verify_password(pw, full.get("password") or ""):
            await register_failed_login(full.get("email") or "", ip)
            await write_audit_log("kvkk_delete_request", user_id=uid, email=full.get("email"), ip=ip,
                                  user_agent=ua, success=False, meta={"reason": "wrong_password"})
            raise HTTPException(status_code=400, detail="Şifre hatalı.")
    elif not is_recent_auth(credentials):
        raise HTTPException(status_code=400, detail=(
            "Hesabınızda şifre tanımlı değil. Lütfen çıkış yapıp yeniden giriş yaptıktan sonra 5 dakika "
            "içinde tekrar deneyin ya da 'Şifremi Unuttum' ile şifre belirleyin."))

    now_iso = _now_iso()
    rid = generate_id()
    n_open = await open_order_count(full)
    if n_open:
        existing = await db.kvkk_requests.find_one({"user_id": uid, "type": "delete",
                                                    "status": "pending_open_orders"}, {"_id": 0, "id": 1})
        if not existing:
            await db.kvkk_requests.insert_one({
                "id": rid, "type": "delete", "user_id": uid, "email_masked": _mask_email(full.get("email")),
                "source": "self", "status": "pending_open_orders", "open_orders": n_open,
                "created_at": now_iso, "reason": str(payload.get("reason") or "")[:300]})
        await write_audit_log("kvkk_delete_request", user_id=uid, email=full.get("email"), ip=ip,
                              user_agent=ua, success=True, meta={"status": "pending_open_orders", "open": n_open})
        return {"success": True, "status": "pending",
                "message": ("Silme talebiniz alındı. Devam eden siparişiniz veya iadeniz olduğu için hesabınız, "
                            "bu işlemler tamamlandığında silinecektir. Bu sürede hesabınızı kullanmaya devam edebilirsiniz.")}

    await db.kvkk_requests.insert_one({
        "id": rid, "type": "delete", "user_id": uid, "email_masked": _mask_email(full.get("email")),
        "source": "self", "status": "processing", "created_at": now_iso,
        "reason": str(payload.get("reason") or "")[:300]})
    summary = await anonymize_user(full, source="self", request_id=rid)
    await db.kvkk_requests.update_one({"id": rid}, {"$set": {"status": "completed", "completed_at": _now_iso(),
                                                            "summary": summary}})
    await write_audit_log("kvkk_delete_request", user_id=uid, ip=ip, user_agent=ua, success=True,
                          meta={"status": "completed", "request_id": rid})
    return {"success": True, "status": "completed",
            "message": "Hesabınız silindi. Yasal saklama yükümlülüğü olan sipariş ve fatura kayıtları "
                       "kimliğinizden ayrılarak yalnız yasal süre boyunca saklanacaktır."}


# ─────────────────────────── Admin uçları ───────────────────────────
@admin_router.get("/requests")
async def admin_list_requests(status: Optional[str] = None, type: Optional[str] = None,
                              page: int = Query(1, ge=1), limit: int = Query(50, ge=1, le=200),
                              current_user: dict = Depends(require_permission("admin.compliance"))):
    q: dict = {}
    if status:
        q["status"] = status
    if type in ("export", "delete"):
        q["type"] = type
    total = await db.kvkk_requests.count_documents(q)
    items = await db.kvkk_requests.find(q, {"_id": 0}).sort("created_at", -1).skip((page - 1) * limit).to_list(limit)
    counts = {}
    async for r in db.kvkk_requests.aggregate([{"$group": {"_id": {"t": "$type", "s": "$status"}, "n": {"$sum": 1}}}]):
        counts[f"{r['_id'].get('t')}:{r['_id'].get('s')}"] = r["n"]
    n_tomb = await db.deletion_tombstones.count_documents({})
    return {"items": items, "total": total, "page": page, "pages": max(1, (total + limit - 1) // limit),
            "counts": counts, "tombstones": n_tomb}


@admin_router.post("/requests/{rid}/apply")
async def admin_apply_request(rid: str, request: Request, payload: dict = Body(default={}),
                              current_user: dict = Depends(require_permission("admin.compliance"))):
    req = await db.kvkk_requests.find_one({"id": rid}, {"_id": 0})
    if not req or req.get("type") != "delete":
        raise HTTPException(status_code=404, detail="Silme talebi bulunamadı")
    if req.get("status") not in ("pending_open_orders", "processing"):
        raise HTTPException(status_code=409, detail="Bu talep zaten sonuçlanmış.")
    user = await db.users.find_one({"id": req.get("user_id")}, {"_id": 0})
    if not user:
        raise HTTPException(status_code=404, detail="Kullanıcı bulunamadı")
    if user.get("kvkk_deleted"):
        await db.kvkk_requests.update_one({"id": rid}, {"$set": {"status": "completed", "completed_at": _now_iso()}})
        return {"success": True, "already": True}
    force = bool((payload or {}).get("force"))
    n_open = await open_order_count(user)
    if n_open and not force:
        raise HTTPException(status_code=409, detail=f"Üyenin hâlâ {n_open} açık siparişi/iadesi var. "
                                                    "Yine de uygulamak için 'zorla' seçin.")
    # FAIL-CLOSED denetim: geri alınamaz anonimleştirme, denetim kaydı (intent) yazılamazsa YAPILMAZ (503).
    from activity_audit import run_audited
    _out: dict = {}

    async def _perform():
        _out["summary"] = await anonymize_user(user, source="admin_request", request_id=rid, actor=current_user)
    await run_audited(db, action="kvkk.delete_apply", entity_type="user", entity_id=user["id"],
                      perform=_perform, current_user=current_user, request=request, source="admin.kvkk",
                      metadata={"request_id": rid, "forced": bool(n_open)})
    summary = _out.get("summary")
    await db.kvkk_requests.update_one({"id": rid}, {"$set": {
        "status": "completed", "completed_at": _now_iso(), "summary": summary,
        "actor_email": current_user.get("email"), "forced": bool(n_open),
        "note": str((payload or {}).get("note") or "")[:300]}})
    return {"success": True, "summary": summary}


@admin_router.post("/requests/{rid}/close")
async def admin_close_request(rid: str, request: Request, payload: dict = Body(default={}),
                              current_user: dict = Depends(require_permission("admin.compliance"))):
    note = str((payload or {}).get("note") or "").strip()[:300]
    if len(note) < 3:
        raise HTTPException(status_code=400, detail="Kapatma notu zorunlu.")
    res = await db.kvkk_requests.update_one(
        {"id": rid, "status": {"$in": ["pending_open_orders", "processing"]}},
        {"$set": {"status": "closed", "closed_at": _now_iso(), "actor_email": current_user.get("email"),
                  "note": note}})
    if not res.matched_count:
        raise HTTPException(status_code=404, detail="Açık talep bulunamadı")
    try:
        from activity_audit import record_admin_audit
        await record_admin_audit(db, action="kvkk.request_close", entity_type="kvkk_request", entity_id=rid,
                                 current_user=current_user, request=request, source="admin.kvkk",
                                 metadata={"note": note})
    except Exception:
        pass
    return {"success": True}


@admin_router.post("/reapply-tombstones")
async def admin_reapply_tombstones(request: Request, current_user: dict = Depends(require_super_admin)):
    stats = await reapply_deletion_tombstones()
    await write_audit_log("kvkk_tombstone_reapply", user_id=current_user.get("id"),
                          email=current_user.get("email"), ip=client_ip_from_request(request),
                          success=True, meta=stats)
    return {"success": True, **stats}
