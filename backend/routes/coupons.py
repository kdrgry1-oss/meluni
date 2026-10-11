"""
Coupons (Kupon) module — code-based discount coupons.

Supports:
- Type: percent or fixed amount (TRY)
- Min cart total, applicable categories/products, usage limits (total + per user)
- Start/end dates, active flag
- Usage tracking via `coupon_redemptions` collection

Storefront: POST /api/coupons/apply {code, cart_total, items:[{product_id, category_id, qty, price}]}
  Returns {valid: bool, discount: float, reason?: str}
Admin:  /api/admin/coupons  CRUD + stats
"""
from fastapi import APIRouter, HTTPException, Depends, Query, Request
from datetime import datetime, timezone, timedelta
from typing import Optional, List
import re
import uuid

from .deps import db, require_admin, require_auth, get_current_user, logger, require_permission, safe_str, limiter


# ── KAMPANYA ZAMAN PENCERESİ (tarih + SAAT) ─────────────────────────────────
# Panel artık yalnız tarih değil SAAT de gönderiyor ("2026-09-22T10:00"). Saat,
# mağaza saat diliminde (İstanbul) girilen DUVAR saatidir; burada UTC'ye çevrilip
# öyle saklanır çünkü geçerlilik kontrolleri (hem Mongo sorgusu hem validate) ISO
# STRING karşılaştırmasıyla yapılıyor — karışık offset saklamak sıralamayı bozardı.
# Mikrosaniye de yazılır ki "şu an" ile aynı biçimde kıyaslansın.
CAMPAIGN_TZ = "Europe/Istanbul"
try:
    from zoneinfo import ZoneInfo as _ZI
except Exception:
    _ZI = None


def _campaign_tz(name=None):
    if not _ZI:
        return timezone.utc
    try:
        return _ZI(str(name or CAMPAIGN_TZ))
    except Exception:
        try:
            return _ZI(CAMPAIGN_TZ)
        except Exception:
            return timezone.utc


def _to_utc_iso(v, tzname=None, end_of_day=False):
    """Panel girdisini UTC ISO'ya çevirir. Kabul edilenler:
      'YYYY-MM-DD'        → gün başı (bitiş alanında gün SONU 23:59:59 — eski
                            kayıtların davranışı korunur, kampanya bir gün erken bitmez)
      'YYYY-MM-DDTHH:MM'  → o yerel saat (bitişte saniye 59 → seçilen dakika DAHİL)
      offset'li tam ISO   → UTC'ye normalize edilir
    Anlaşılamayan değer AYNEN bırakılır (eski kayıt/biçim bozulmasın)."""
    if v in (None, ""):
        return None
    t = str(v).strip().replace(" ", "T")
    z = _campaign_tz(tzname)
    try:
        if len(t) == 10:
            d = datetime.strptime(t, "%Y-%m-%d")
            if end_of_day:
                d = d.replace(hour=23, minute=59, second=59)
            return d.replace(tzinfo=z).astimezone(timezone.utc).isoformat(timespec="microseconds")
        if len(t) >= 16 and t[10] == "T" and ("+" not in t[10:]) and ("Z" not in t[10:]):
            d = datetime.strptime(t[:16], "%Y-%m-%dT%H:%M")
            if end_of_day:
                d = d.replace(second=59)
            return d.replace(tzinfo=z).astimezone(timezone.utc).isoformat(timespec="microseconds")
        return datetime.fromisoformat(t.replace("Z", "+00:00")).astimezone(
            timezone.utc).isoformat(timespec="microseconds")
    except Exception:
        return v


def _window_from_payload(payload: dict, key_at: str, key_date: str, end_of_day=False):
    """start_at/end_at (saatli) ile eski start_date/end_date arasından SAAT İÇEREN
    değeri tercih eder, UTC'ye çevirir. Alan hiç yoksa None (temizleme) döner."""
    a, b = (payload or {}).get(key_at), (payload or {}).get(key_date)
    raw = a if (isinstance(a, str) and "T" in a) else (a if a not in (None, "") else b)
    return _to_utc_iso(raw, (payload or {}).get("tz"), end_of_day=end_of_day)


def _is_personal_coupon(c: dict) -> bool:
    """Kişiye özel ödül kuponu mu? (referral/doğum günü/hoş geldin → reward_email/reward_user_id)."""
    return bool((c or {}).get("reward_email") or (c or {}).get("reward_user_id"))


def _coupon_owner_ok(c: dict, user_id, email: str) -> bool:
    """Kişiye özel kupon YALNIZ sahibi tarafından kullanılabilir (DENETİM SEC-2 F1). Kimlik
    SUNUCUDAN türetilmeli (istemci iddiasına güvenilmez); kişisel değilse herkese açık.
    DENETİM (verify-redteam #2): reward_user_id VARSA yalnız DOĞRULANMIŞ user_id eşleşmesi kabul —
    istemci e-postası (shipping_address.email) taklit edilebildiği için e-posta dalına düşülmez."""
    if not _is_personal_coupon(c):
        return True
    ru = c.get("reward_user_id")
    if ru:
        # Kayıtlı kullanıcıya ait ödül → yalnız o kullanıcı (token'dan) kullanabilir.
        return bool(user_id and str(user_id) == str(ru))
    # Yalnız e-posta ödülü (misafir referansı) → e-posta eşleşmesi (daha zayıf ama user_id yok).
    _em = (email or "").strip().lower()
    re_ = (c.get("reward_email") or "").strip().lower()
    return bool(_em and re_ and _em == re_)


# ── Denetim #19: kimliğe bağlı indirimler DOĞRULANMIŞ hesap niteliğine dayanır ──────────────
_ORDER_EMAIL_FIELDS = ("shipping_address.email", "billing_address.email", "email", "customer_email", "user_email")


def _norm_email(e) -> str:
    """Küçük harf + boşluksuz; yerel kısımdaki '+etiket' atılır (aynı kutu = aynı kişi)."""
    e = str(e or "").strip().lower()
    if "@" not in e:
        return e
    local, _, dom = e.partition("@")
    return local.split("+", 1)[0] + "@" + dom


def _email_match_clauses(email: str) -> list:
    """Siparişte e-postanın tutulduğu TÜM alanlarda (iç içe shipping_address.email dahil), büyük/küçük
    harf ve '+etiket' farkına duyarsız eşleşme koşulları."""
    import re as _re
    ne = _norm_email(email)
    if "@" not in ne:
        return []
    local, _, dom = ne.partition("@")
    rx = {"$regex": "^" + _re.escape(local) + r"(\+[^@]*)?@" + _re.escape(dom) + "$", "$options": "i"}
    return [{f: rx} for f in _ORDER_EMAIL_FIELDS]


async def _account_identity(user_id) -> dict:
    """user_id (SUNUCU tarafından token'dan türetilmiş) → hesabın kayıtlı e-postası + doğrulanmış mı."""
    if not user_id:
        return {}
    try:
        u = await db.users.find_one({"id": str(user_id)}, {"_id": 0, "email": 1, "email_verified": 1}) or {}
    except Exception:
        u = {}
    return {"email": _norm_email(u.get("email")), "email_verified": u.get("email_verified") is True}


admin_router = APIRouter(prefix="/admin/coupons", tags=["admin-coupons"])
public_router = APIRouter(prefix="/coupons", tags=["coupons"])


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


@admin_router.get("")
async def list_coupons(
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    search: Optional[str] = None,
    active_only: bool = False,
    current_user: dict = Depends(require_permission("campaigns.view", any_in_area=True)),
):
    # Kuponlar sayfası yalnız KOD BAZLI kuponları listeler; otomatik kampanyalar (auto_apply /
    # AUTO- kodlu) Pazarlama → Kampanyalar'da yönetilir (kullanıcı isteği: ikisi karışmasın).
    q: dict = {"auto_apply": {"$ne": True}, "code": {"$not": {"$regex": "^AUTO-"}}}
    if search:
        # GÜVENLİK (INJ-01): ham kullanıcı girdisi $regex'e gidiyordu → "(a+)+$" ile Mongo'da ReDoS /
        # "." "*" ile joker eşleşme. Diğer admin aramaları gibi re.escape (düz metin araması).
        _rs = re.escape(str(search).strip()[:100])
        q["$or"] = [{"code": {"$regex": _rs, "$options": "i"}}, {"title": {"$regex": _rs, "$options": "i"}}]
    if active_only:
        q["is_active"] = True
    total = await db.coupons.count_documents(q)
    skip = (page - 1) * limit
    items = await db.coupons.find(q, {"_id": 0}).sort("created_at", -1).skip(skip).limit(limit).to_list(limit)
    # Annotate redemption counts
    for c in items:
        c["redeemed_count"] = await db.coupon_redemptions.count_documents({"coupon_id": c["id"]})
    return {"items": items, "total": total, "page": page, "pages": (total + limit - 1) // limit}


@admin_router.post("")
async def create_coupon(payload: dict, current_user: dict = Depends(require_permission("campaigns.create"))):
    code = (payload.get("code") or "").strip().upper()
    if not code:
        raise HTTPException(status_code=400, detail="Kupon kodu gerekli")
    if await db.coupons.find_one({"code": code}):
        raise HTTPException(status_code=409, detail="Bu kupon kodu zaten kullanılıyor")
    doc = {
        "id": str(uuid.uuid4()),
        "code": code,
        "title": payload.get("title", ""),
        "type": payload.get("type", "percent"),  # percent | fixed | nth_discount | bundle
        "value": float(payload.get("value", 0) or 0),
        # C4 — bundle: 'products' listesindeki ürünlerin hepsi sepetteyse tek paket fiyatı
        "bundle_price": float(payload.get("bundle_price", 0) or 0) or None,
        "min_cart_total": float(payload.get("min_cart_total", 0) or 0),
        "max_discount": float(payload.get("max_discount", 0) or 0) or None,  # cap for percent
        "categories": payload.get("categories", []),
        "products": payload.get("products", []),
        "usage_limit": int(payload.get("usage_limit", 0) or 0) or None,
        "usage_limit_per_user": int(payload.get("usage_limit_per_user", 0) or 0) or None,
        "start_at": _window_from_payload(payload, "start_at", "start_date"),
        "end_at": _window_from_payload(payload, "end_at", "end_date", end_of_day=True),
        "is_active": bool(payload.get("is_active", True)),
        "first_order_only": bool(payload.get("first_order_only", False)),
        "free_shipping": bool(payload.get("free_shipping", False)),
        "auto_apply": bool(payload.get("auto_apply", False)),
        # İndirimli fiyatı (sale_price) olan ürünlere bu kampanya uygulansın mı? Varsayılan True
        # = uygulanmaz (indirimli fiyat geçerli). False → indirimli ürünlere de kampanya uygulanır.
        "skip_discounted": bool(payload.get("skip_discounted", True)),
        # --- nth_discount ("X al Y öde") alanları (önceden create'te kaydedilmiyordu) ---
        "min_quantity": int(payload.get("min_quantity", 0) or 0) or None,
        "buy_quantity": int(payload.get("buy_quantity", 0) or 0) or None,
        "free_quantity": int(payload.get("free_quantity", 1) or 1),
        "get_discount": float(payload.get("get_discount", 0) or 0),
        # Vitrin ibaresi (ör. "3 AL 2 ÖDE"); boşsa kurgudan otomatik üretilir.
        "badge_text": str(payload.get("badge_text") or "").strip()[:40],
        # --- Madde 4 motor alanları ---
        "priority": int(payload.get("priority", 0) or 0),
        # BİRLEŞME KURALI (kullanıcı isteği): yeni kupon VARSAYILAN olarak TÜM kampanyalarla
        # birleşir; admin yalnız ENGELLENECEK kampanyaları seçer (not_combinable_with).
        "combinable": bool(payload.get("combinable", True)),
        "stack_group": (payload.get("stack_group") or "").strip() or None,
        "combinable_with": payload.get("combinable_with") or [],  # eski izin listesi (artık kullanılmıyor)
        "not_combinable_with": [str(x) for x in (payload.get("not_combinable_with") or []) if x],
        "payment_methods": payload.get("payment_methods") or [],  # bos=tum yontemler; dolu=sadece secililer
        "created_at": _utcnow(),
        "created_by": current_user.get("email", ""),
    }
    await db.coupons.insert_one(doc)
    doc.pop("_id", None)
    invalidate_codes_cache()  # bulanık çözümleyici yeni kodu hemen görsün
    return {"success": True, "coupon": doc}


@admin_router.put("/{cid}")
async def update_coupon(cid: str, payload: dict, current_user: dict = Depends(require_permission("campaigns.edit"))):
    allowed = (
        "title", "type", "value", "min_cart_total", "max_discount",
        "categories", "products", "usage_limit", "usage_limit_per_user",
        "start_at", "end_at", "is_active", "first_order_only", "free_shipping", "auto_apply",
        "min_quantity", "buy_quantity", "free_quantity", "get_discount", "bundle_price",
        "priority", "combinable", "stack_group", "combinable_with", "not_combinable_with", "payment_methods",
        "skip_discounted", "badge_text",
    )
    update = {k: v for k, v in payload.items() if k in allowed}
    if "not_combinable_with" in update:
        update["not_combinable_with"] = [str(x) for x in (update.get("not_combinable_with") or []) if x]
    # Tarih+saat → UTC (sorgu/doğrulama ISO string karşılaştırması yapıyor).
    if ("start_at" in payload) or ("start_date" in payload):
        update["start_at"] = _window_from_payload(payload, "start_at", "start_date")
    if ("end_at" in payload) or ("end_date" in payload):
        update["end_at"] = _window_from_payload(payload, "end_at", "end_date", end_of_day=True)
    update["updated_at"] = _utcnow()
    res = await db.coupons.update_one({"id": cid}, {"$set": update})
    if res.matched_count == 0:
        raise HTTPException(status_code=404, detail="Kupon bulunamadı")
    invalidate_codes_cache()  # aktiflik/kod değişimi çözümleyiciye hemen yansısın
    return {"success": True}


@admin_router.delete("/{cid}")
async def delete_coupon(cid: str, current_user: dict = Depends(require_permission("campaigns.delete"))):
    res = await db.coupons.delete_one({"id": cid})
    if res.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Kupon bulunamadı")
    invalidate_codes_cache()
    return {"success": True}


@admin_router.get("/{cid}/redemptions")
async def coupon_redemptions(cid: str, current_user: dict = Depends(require_permission("campaigns.view", any_in_area=True))):
    rows = await db.coupon_redemptions.find({"coupon_id": cid}, {"_id": 0}).sort("redeemed_at", -1).to_list(500)
    return {"items": rows, "total": len(rows)}


@admin_router.get("/exceptions")
async def list_coupon_exceptions(current_user: dict = Depends(require_permission("campaigns.view", any_in_area=True))):
    """İlk-sipariş/kişi-başı-limit istisnası tanınan müşteri e-postaları."""
    doc = await db.settings.find_one({"id": "coupon_first_order_exceptions"}, {"_id": 0, "emails": 1})
    return {"emails": (doc or {}).get("emails") or []}


@admin_router.post("/exceptions")
async def edit_coupon_exceptions(payload: dict, current_user: dict = Depends(require_permission("campaigns.edit"))):
    """İstisna listesine e-posta ekle/çıkar. payload: {email, action: 'add'|'remove'}.
    İstisna: bu e-posta first_order_only + usage_limit_per_user kontrollerinden muaf olur."""
    email = str((payload or {}).get("email") or "").strip().lower()
    action = str((payload or {}).get("action") or "add").lower()
    if not email or "@" not in email:
        raise HTTPException(status_code=400, detail="Geçerli e-posta gerekli")
    op = {"$addToSet": {"emails": email}} if action != "remove" else {"$pull": {"emails": email}}
    if action != "remove":
        op["$setOnInsert"] = {"id": "coupon_first_order_exceptions"}
    await db.settings.update_one({"id": "coupon_first_order_exceptions"}, op, upsert=True)
    _EXEMPT_CACHE["at"] = 0.0  # cache'i hemen tazele
    doc = await db.settings.find_one({"id": "coupon_first_order_exceptions"}, {"_id": 0, "emails": 1})
    return {"success": True, "emails": (doc or {}).get("emails") or []}


async def migrate_coupon_stacking_default():
    """TEK SEFERLİK migrasyon (kullanıcı kararı): kupon/kampanya birleşmesi VARSAYILAN AÇIK.
      - combinable=False olan tüm kayıtlar → True (unutulan/seçilmeyen kuponlar artık birleşir).
      - Eski 'combinable_with' İZİN listesi olan kayıtlar: bilinçli seçim korunur → izin listesinde
        OLMAYAN mevcut kayıtlar 'not_combinable_with' ENGEL listesine yazılır.
    Sonuç settings.id='migrations'.coupon_stacking_default_v1 altında saklanır (etkilenen kodlar)."""
    try:
        _flag = "coupon_stacking_default_v1"
        _m = await db.settings.find_one({"id": "migrations"}, {"_id": 0, _flag: 1}) or {}
        if _m.get(_flag):
            return
        allc = await db.coupons.find({}, {"_id": 0, "id": 1, "code": 1, "combinable": 1, "combinable_with": 1,
                                          "not_combinable_with": 1}).to_list(2000)
        all_ids = {str(c.get("id")) for c in allc if c.get("id")}
        flipped, converted = [], []
        for c in allc:
            upd = {}
            if c.get("combinable") is False:
                upd["combinable"] = True
                flipped.append(c.get("code") or c.get("id"))
            allow = [str(x) for x in (c.get("combinable_with") or []) if x]
            if allow and not c.get("not_combinable_with"):
                deny = sorted(all_ids - set(allow) - {str(c.get("id"))})
                upd["not_combinable_with"] = deny
                upd["combinable_with_legacy"] = allow
                upd["combinable_with"] = []
                converted.append(c.get("code") or c.get("id"))
            if upd:
                upd["updated_at"] = _utcnow()
                await db.coupons.update_one({"id": c.get("id")}, {"$set": upd})
        await db.settings.update_one(
            {"id": "migrations"},
            {"$set": {_flag: True, f"{_flag}_at": _utcnow(),
                      f"{_flag}_flipped": flipped, f"{_flag}_converted": converted},
             "$setOnInsert": {"id": "migrations"}},
            upsert=True,
        )
        invalidate_codes_cache()
        logger.info(f"[kupon] birleşme varsayılanı migrasyonu: combinable→True {len(flipped)} kayıt "
                    f"{flipped}; izin→engel listesi {len(converted)} kayıt {converted}")
    except Exception as e:
        logger.warning(f"[kupon] migrate_coupon_stacking_default hata: {e}")


@admin_router.get("/stacking-migration")
async def coupon_stacking_migration_status(current_user: dict = Depends(require_permission("campaigns.view", any_in_area=True))):
    """Birleşme varsayılanı migrasyonunun sonucu (hangi kuponlar birleşir yapıldı / hangileri engel listesine çevrildi)."""
    _flag = "coupon_stacking_default_v1"
    m = await db.settings.find_one({"id": "migrations"}, {"_id": 0}) or {}
    return {"applied": bool(m.get(_flag)), "at": m.get(f"{_flag}_at"),
            "combinable_made_true": m.get(f"{_flag}_flipped") or [],
            "allow_list_converted_to_block_list": m.get(f"{_flag}_converted") or []}


async def migrate_welcome_coupon_sale_policy():
    """TEK SEFERLİK migrasyon (işletme sahibi kararı, canlı denetim sonrası): hoş geldin kuponları
    (HOSGELDIN10, HOSGELDIN) İNDİRİMLİ (sale_price) ürünlerde de uygulansın → skip_discounted=False.

    Kök neden: katalogun %14'ü indirimli; skip_discounted=True olduğu için indirimli ürünlü sepette
    kupon 0 veriyor ve "kupon çalışmıyor" şikâyetleri tekrarlıyordu (W11692 kanıtı).
    İdempotent + işaretli: settings.id='migrations'.welcome_coupon_sale_ok_v1 yazıldıktan sonra bir
    daha ÇALIŞMAZ — admin ileride kampanya formundan anahtarı değiştirirse restart geri çevirmez."""
    try:
        _flag = "welcome_coupon_sale_ok_v1"
        _m = await db.settings.find_one({"id": "migrations"}, {"_id": 0, _flag: 1}) or {}
        if _m.get(_flag):
            return
        res = await db.coupons.update_many(
            {"code": {"$in": ["HOSGELDIN10", "HOSGELDIN"]}},
            {"$set": {"skip_discounted": False, "updated_at": _utcnow()}},
        )
        await db.settings.update_one(
            {"id": "migrations"},
            {"$set": {_flag: True, f"{_flag}_at": _utcnow(), f"{_flag}_matched": res.matched_count},
             "$setOnInsert": {"id": "migrations"}},
            upsert=True,
        )
        invalidate_codes_cache()
        logger.info(f"[migration] {_flag}: hoş geldin kuponlarında skip_discounted=False "
                    f"(eşleşen {res.matched_count}, güncellenen {res.modified_count})")
    except Exception as _e:
        logger.warning(f"[migration] welcome_coupon_sale_ok_v1 hata: {_e}")


async def seed_coupon_exceptions():
    """Compatibility entry point: maintain indexes, never seed another tenant's customers."""
    await ensure_coupon_redemption_unique_index()


async def _log_coupon_attempt(entered_raw, entered_resolved, entered_id, rejected, applied,
                              email, user_id, cart_total, items, payment_method):
    """TEŞHİS GÜNLÜĞÜ: müşteri bir kod yazdı ama UYGULANMADI → ne yazdı, neye çözüldü, neden
    reddedildi (ya da istiflemede düştü) kaydedilir. Admin /admin/coupons/attempts ile listeler;
    "müşteri kodu geçersiz diyor" şikâyetinde müşteriye sormadan gerçek neden görülür.
    Yalnız BAŞARISIZ denemeler; aynı kimlik+kod+neden 10 dk içinde tekrar yazılmaz (spam yok)."""
    try:
        if entered_id and any(a["c"]["id"] == entered_id for a in applied):
            return  # uygulandı → kayıt yok
        if not entered_id:
            reason = "Kod sistemde bulunamadı (çözümlenemedi)"
        elif rejected:
            reason = "; ".join(str(r.get("reason") or "") for r in rejected)[:300]
        else:
            reason = "Kampanya çakışması (istifleme): başka kampanya öncelikli, kod sessizce uygulanmadı"
        _em = (email or "").strip().lower()
        _key = {"entered": str(entered_raw or "")[:60], "reason": reason[:300],
                "who": (str(user_id or "") or _em or "misafir")}
        _since = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
        if await db.coupon_attempts.find_one({**_key, "at": {"$gte": _since}}, {"_id": 1}):
            return
        await db.coupon_attempts.insert_one({
            **_key, "resolved": entered_resolved or "", "coupon_id": entered_id or "",
            "email": _em, "user_id": str(user_id or ""), "payment_method": payment_method or "",
            "cart_total": float(cart_total or 0),
            "items": [{"product_id": str(it.get("product_id") or ""), "price": it.get("price"),
                       "qty": it.get("qty"), "sale": bool(it.get("_has_manual_sale"))} for it in (items or [])][:20],
            "at": datetime.now(timezone.utc).isoformat(),
        })
    except Exception as _e:
        logger.warning(f"[coupon_attempts] kayıt hatası: {_e}")


@admin_router.get("/attempts")
async def list_coupon_attempts(limit: int = 50, code: str = "", email: str = "",
                               current_user: dict = Depends(require_permission("campaigns.view", any_in_area=True))):
    """Son BAŞARISIZ kupon denemeleri (müşteri ne yazdı → neye çözüldü → neden uygulanmadı).
    Filtre: code (yazılan/çözülen), email. En yeni önce."""
    limit = max(1, min(int(limit or 50), 500))
    q = {}
    if code:
        _c = safe_str(code, 60)
        q["$or"] = [{"entered": {"$regex": re.escape(_c), "$options": "i"}},
                    {"resolved": {"$regex": re.escape(_c), "$options": "i"}}]
    if email:
        q["email"] = safe_str(email, 120).strip().lower()
    rows = await db.coupon_attempts.find(q, {"_id": 0}).sort("at", -1).to_list(limit)
    # Neden dağılımı (hızlı özet)
    _agg = {}
    for r in rows:
        _agg[r.get("reason") or "?"] = _agg.get(r.get("reason") or "?", 0) + 1
    return {"total": len(rows), "by_reason": sorted(_agg.items(), key=lambda x: -x[1]), "items": rows}


@admin_router.get("/diagnose")
async def diagnose_coupon_for_user(code: str, email: str = "",
                                   current_user: dict = Depends(require_permission("campaigns.view", any_in_area=True))):
    """TEŞHİS (admin): Bir müşterinin (email) belirli bir kupon KODUNU neden kullanamadığını
    GİRİŞ YAPMIŞ gibi simüle eder — user_id + email ile _evaluate_single sonucunu ve destekleyici
    sayıları döndürür."""
    code = (code or "").strip().upper()
    c = await db.coupons.find_one({"code": code}, {"_id": 0})
    if not c:
        return {"found": False, "reason": "Kupon kodu bulunamadı"}
    em = (email or "").strip().lower()
    u = await db.users.find_one({"email": em}, {"_id": 0, "id": 1, "email": 1, "created_at": 1}) if em else None
    uid = (u or {}).get("id")
    # Önceki (ödenmiş/tamamlanmış) sipariş sayısı (first_order kriteriyle aynı)
    _ors = []
    if uid: _ors.append({"user_id": uid})
    if em: _ors.append({"customer_email": em})
    prior_paid = 0
    prior_all = 0
    statuses = {}
    if _ors:
        prior_paid = await db.orders.count_documents({"$and": [{"$or": _ors},
            {"$or": [{"payment_status": "paid"},
                     {"status": {"$in": ["confirmed","processing","preparing","shipped","delivered","undelivered","returned","refunded","partial_refunded","cancel_requested"]}}]}]})
        async for _o in db.orders.find({"$or": _ors}, {"_id": 0, "status": 1, "payment_status": 1}):
            prior_all += 1
            k = f"{_o.get('status')}/{_o.get('payment_status')}"
            statuses[k] = statuses.get(k, 0) + 1
    # bu kullanıcının bu kuponu redemption sayısı
    _rors = []
    if uid: _rors.append({"user_id": uid})
    if em: _rors.extend([{"customer_email": em}, {"email": em}])
    redemptions = await db.coupon_redemptions.count_documents(
        {"coupon_id": c["id"], **({"$or": _rors} if _rors else {})}) if _rors else 0
    total_used = await db.coupon_redemptions.count_documents({"coupon_id": c["id"]})
    # GERÇEK değerlendirme (giriş yapmış gibi)
    ev = await _evaluate_single(c, 1000.0, [], uid, em, "")
    return {
        "found": True, "code": code,
        "user_found": bool(u), "user_id": uid, "user_created_at": (u or {}).get("created_at"),
        "coupon": {"first_order_only": c.get("first_order_only"),
                   "usage_limit": c.get("usage_limit"),
                   "usage_limit_per_user": c.get("usage_limit_per_user"),
                   "is_active": c.get("is_active"),
                   "start_at": c.get("start_at"), "end_at": c.get("end_at"),
                   "min_cart_total": c.get("min_cart_total"),
                   "payment_methods": c.get("payment_methods"),
                   "reward_email": c.get("reward_email"), "reward_user_id": c.get("reward_user_id"),
                   "auto_apply": c.get("auto_apply")},
        "prior_paid_orders": prior_paid, "prior_all_orders": prior_all,
        "prior_status_breakdown": statuses,
        "this_user_redemptions": redemptions, "coupon_total_redemptions": total_used,
        "simulated_logged_in_result": ev,
    }


# ---------------- Public: apply a coupon at checkout ----------------

@public_router.post("/available")
async def available_coupons(payload: dict, current_user: dict = Depends(get_current_user)):
    """Trendyol Go benzeri: bu sepet için kullanıcının kullanabileceği aktif kuponları döner.
    Hesaplanmış discount değerlerini de içerir (tıklanınca sepete direkt uygulanır).
    Payload: {cart_total, items}
    GÜVENLİK (DENETİM SEC-2 F1): kimlik yalnız JWT'den (istemci user_id/email'ine güvenilmez);
    kişiye özel ödül kuponlarının KODU başkasına SIZDIRILMAZ (harvest engellendi)."""
    cart_total = float(payload.get("cart_total") or 0)
    # user_id yalnız doğrulanmış token'dan; auth e-postası owner kontrolü için.
    user_id = (current_user or {}).get("id")
    _auth_email = ((current_user or {}).get("email") or "").strip().lower()
    items = payload.get("items") or []
    items = await _enrich_items_category_ids(items)
    now_iso = datetime.now(timezone.utc).isoformat()

    q: dict = {"is_active": True}
    # Zaman penceresi (tarih verilmişse)
    q["$and"] = [
        {"$or": [{"start_at": None}, {"start_at": {"$lte": now_iso}}, {"start_at": {"$exists": False}}]},
        {"$or": [{"end_at": None}, {"end_at": {"$gte": now_iso}}, {"end_at": {"$exists": False}}]},
    ]
    coupons = await db.coupons.find(q, {"_id": 0}).sort("value", -1).to_list(length=50)

    out = []
    for c in coupons:
        # SEC-2 F1: kişiye özel ödül kuponunun KODUNU yalnız doğrulanmış SAHİBİNE göster
        # (anonim/başkası bu listeden REF-/DG-/HG- kodlarını toplayamaz).
        if _is_personal_coupon(c) and not _coupon_owner_ok(c, user_id, _auth_email):
            continue
        # Min tutar
        min_total = float(c.get("min_cart_total") or 0)
        if min_total and cart_total < min_total:
            continue
        # Kullanici eslesmesi: user_id VEYA email (misafir->uye gecisinde de yakalansin)
        em = (payload.get("email") or payload.get("customer_email") or "").strip().lower()
        _ors = []
        if user_id:
            _ors.append({"user_id": user_id})
        if em:
            _ors.append({"customer_email": em})
        # İlk siparişe özel (hoşgeldin): user_id VEYA email ile GEÇERLİ önceki sipariş varsa GİZLE.
        # C1 fix: BAŞARISIZ/ödenmemiş (failed/expired) siparişleri "önceki sipariş" SAYMA — aksi
        # halde 3DS reddi/yarım kalan deneme hoşgeldin kuponunu yakıyordu (elle-giriş yolu #368
        # zaten böyle süzüyordu; iki yol artık tutarlı).
        if c.get("first_order_only") and _ors:
            # "önceki sipariş" YALNIZ gerçekten ödenmiş/tamamlanmış (awaiting_payment/pending
            # yarıda kalan denemeler hoşgeldin kuponunu YAKMAZ) — _evaluate_single ile birebir.
            prior = await db.orders.count_documents({
                "$and": [
                    {"$or": _ors},
                    {"$or": [
                        {"payment_status": "paid"},
                        {"status": {"$in": ["confirmed", "processing", "preparing", "shipped",
                                            "delivered", "undelivered", "returned", "refunded",
                                            "partial_refunded", "cancel_requested"]}},
                    ]},
                ],
            })
            if prior > 0:
                continue
        # Kullanım limiti (toplam)
        # Sayım SİPARİŞ-BAZLI tekilleştirilir (_coupon_used_count): aynı siparişin yinelenen
        # redemption satırı (eşzamanlı kayıt yarışı) kullanımı iki kez saymasın. Bu liste
        # yalnız GÖSTERİM içindir; in-flight sayımı eskisi gibi yok (count_inflight=False).
        if c.get("usage_limit"):
            used = await _coupon_used_count(c["id"], c.get("code", ""), count_inflight=False)
            if used >= c["usage_limit"]:
                continue
        # Kullanıcı başına limit: user_id VEYA email ile kullanim sayilir (hoşgeldin 1 kez -> GIZLE)
        if c.get("usage_limit_per_user") and _ors:
            used_by = await _coupon_used_count(c["id"], c.get("code", ""), restrict_ors=_ors,
                                               count_inflight=False)
            if used_by >= c["usage_limit_per_user"]:
                continue
        # Kategori/ürün filtresi varsa eligible_total hesapla
        allowed_cats = set(c.get("categories") or [])
        allowed_pids = set(c.get("products") or [])
        if allowed_cats or allowed_pids:
            eligible_total = 0.0
            for it in items:
                if _item_in_scope(it, allowed_cats, allowed_pids):
                    eligible_total += float(it.get("price", 0)) * int(it.get("qty", 0) or 0)
            base = eligible_total
            if base <= 0:
                continue
        else:
            base = cart_total
        # Discount hesapla
        discount = 0.0
        if c.get("type") == "percent":
            discount = base * (c.get("value", 0) / 100.0)
            if c.get("max_discount"):
                discount = min(discount, c["max_discount"])
        else:
            discount = min(c.get("value", 0), base)
        discount = round(discount, 2)
        if discount <= 0 and not c.get("free_shipping"):
            continue
        out.append({
            "id": c["id"], "code": c["code"], "title": c.get("title", ""),
            "type": c.get("type"), "value": c.get("value"),
            "min_cart_total": min_total, "discount": discount,
            "free_shipping": bool(c.get("free_shipping")),
            "end_at": c.get("end_at"),
        })
    # En iyi indirimi yukarı koy
    out.sort(key=lambda x: x["discount"], reverse=True)
    return {"items": out, "total": len(out)}


def _norm_pm(pm) -> str:
    """Odeme yontemi anahtarini kanonik 3 degerden birine indirger.
    bank_transfer | credit_card | cash_on_delivery. Bos/bilinmeyen -> ''."""
    s = (str(pm or "")).strip().lower()
    if not s:
        return ""
    if s in ("bank_transfer", "havale", "eft", "havale_eft", "banka_havale", "wire"):
        return "bank_transfer"
    if s in ("cash_on_delivery", "kapida", "kapıda", "cod", "kapida_odeme"):
        return "cash_on_delivery"
    if s in ("credit_card", "card", "kredi_karti", "kredi_kart", "banka_kredi_karti", "iyzico"):
        return "credit_card"
    return s  # taninmayan ama dolu deger: oldugu gibi (esitlik yine de calisir)


def _item_category_set(it: dict) -> set:
    """Bir sepet kaleminin ait oldugu TUM kategori id'leri (coklu uyelik dahil,
    'En Yeniler' gibi ikincil kategoriler dahil). 'category_ids' enrich edilmisse
    (bkz. _enrich_items_category_ids) onu kullanir; yoksa eski tekil 'category_id'ye
    duser (geriye donuk uyumluluk)."""
    # KRİTİK: kategori id'leri str'e normalize edilir. Kampanya.categories int, ürün
    # category_ids string ("7022") olabiliyor; normalize edilmezse kesişim BOŞ kalır →
    # kapsamlı kampanya sepette hiç uygulanmaz (rozet gösterir ama indirim düşmez). Rozet
    # mantığı (_campaign_pct_for_product) da str normalize ediyor — motor onunla TUTARLI olsun.
    cids = it.get("category_ids")
    if cids:
        return {str(x) for x in cids if x is not None}
    single = it.get("category_id")
    return {str(single)} if single not in (None, "") else set()


def _item_in_scope(it: dict, allowed_cats: set, allowed_pids: set, excluded_pids: set = None) -> bool:
    pid = it.get("product_id")
    # HARİÇ TUTMA (kullanıcı isteği): kategori kapsamında olsa BİLE bu ürün kampanyaya girmez.
    # Hariç tutma HER ZAMAN kazanır (kapsamdan önce değerlendirilir).
    if excluded_pids and pid and str(pid) in excluded_pids:
        return False
    if pid and str(pid) in allowed_pids:
        return True
    if allowed_cats and _item_category_set(it) & allowed_cats:
        return True
    return False


async def _resolve_excluded_pids(raw) -> list:
    """Admin'in yazdığı 'hariç tutulacak ürünler' metnini (alt alta VEYA virgülle ayrılmış;
    ürün kart id'si / stok kodu / ürün id'si / ürün adı) gerçek ürün id listesine çevirir.
    Bir kart id / stok kodu verilirse o modelin TÜM renk kardeşleri (aynı gruptaki ürünler)
    hariç tutulur. Ad eşleşmesi önce birebir (küçük/büyük duyarsız), yoksa 'içeren' ile aranır."""
    if not raw:
        return []
    if isinstance(raw, list):
        tokens = [str(t).strip() for t in raw if str(t).strip()]
    else:
        tokens = [t.strip() for t in re.split(r"[\n,;]+", str(raw)) if t.strip()]
    if not tokens:
        return []
    pids = set()
    for tok in tokens:
        matched = False
        # 1) Kimlik alanları: ürün id / kart id / stok kodu / ürün-kartı id (birebir)
        cur = db.products.find(
            {"$or": [{"id": tok}, {"csv_card_id": tok}, {"stock_code": tok},
                     {"urun_karti_id": tok}, {"barcode": tok}]},
            {"_id": 0, "id": 1})
        async for p in cur:
            if p.get("id"):
                pids.add(p["id"]); matched = True
        if matched:
            continue
        # 2) Ad: birebir (küçük/büyük duyarsız)
        rx_exact = {"$regex": f"^{re.escape(tok)}$", "$options": "i"}
        cur = db.products.find({"name": rx_exact}, {"_id": 0, "id": 1})
        async for p in cur:
            if p.get("id"):
                pids.add(p["id"]); matched = True
        if matched:
            continue
        # 3) Ad: içeren (son çare)
        rx_has = {"$regex": re.escape(tok), "$options": "i"}
        async for p in db.products.find({"name": rx_has}, {"_id": 0, "id": 1}).limit(200):
            if p.get("id"):
                pids.add(p["id"])
    return sorted(pids)


async def _enrich_items_category_ids(items: list) -> list:
    """Sepet kalemlerine urunun TAM kategori uyelik listesini (category_ids, atalar
    dahil) ekler. KOK SEBEP DUZELTMESI: kampanya kapsam eslestirmesi onceden sadece
    urunun tekil birincil 'category_id' alanina bakiyordu. 'En Yeniler' gibi kategoriler
    ise urunun birincil kategorisi DEGIL, ayri bir 'category_ids' dizisiyle (coklu
    uyelik) temsil ediliyor -> o kategoriye ozel kampanyalar hicbir zaman eslesmiyordu.
    Bu fonksiyon her sepet kalemini gercek urun dokumanindaki category_ids ile zenginlestirir."""
    pids = list({it.get("product_id") for it in items if it.get("product_id")})
    if not pids:
        return items
    cat_map = {}
    async for p in db.products.find({"id": {"$in": pids}},
                                    {"_id": 0, "id": 1, "category_ids": 1, "category_id": 1,
                                     "price": 1, "sale_price": 1}):
        cids = set(p.get("category_ids") or [])
        if p.get("category_id"):
            cids.add(p["category_id"])
        # KURAL (kullanıcı): ürün kartında İNDİRİMLİ FİYAT (sale_price) girili ise, o ürüne
        # KAMPANYA UYGULANMAZ (indirimli fiyat geçerli). Ürünü kampanya tabanından dışlamak
        # için işaretle; rozet (products._apply_campaign_badge) ve vitrin (priceView) de aynı
        # kuralı uygular → rozet ⊆ motor korunur.
        _lp = float(p.get("price") or 0)
        _sp = float(p.get("sale_price") or 0)
        cat_map[p["id"]] = {"cids": list(cids), "manual_sale": bool(_sp > 0 and _sp < _lp)}
    out = []
    for it in items:
        pid = it.get("product_id")
        if pid and pid in cat_map:
            out.append({**it, "category_ids": cat_map[pid]["cids"],
                        "_has_manual_sale": cat_map[pid]["manual_sale"]})
        else:
            out.append(it)
    return out


def _compute_discount(c: dict, cart_total: float, items: list) -> float:
    """Saf indirim matematigi (dogrulama YOK). Kapsam(scope) + tip(nth/percent/fixed).
    items fiyatlari olceklenmis verilirse (stacking) sonuc kalan tabana gore otomatik cikar."""
    # str normalize (bkz. _item_category_set) — int/str kategori-id uyuşmazlığı kapsamı
    # boşa düşürüp indirimi 0 yapıyordu.
    allowed_cats = {str(x) for x in (c.get("categories") or []) if x is not None}
    allowed_pids = {str(x) for x in (c.get("products") or []) if x is not None}
    # HARİÇ TUTMA (kullanıcı isteği): kategori kapsamında olsa dahi bu ürünler kampanyaya girmez.
    excluded_pids = {str(x) for x in (c.get("excluded_products") or []) if x is not None}
    # KAMPANYA-BAŞINA ANAHTAR: skip_discounted (varsayılan True) → ürün kartında indirimli fiyat
    # (sale_price) girili kalemler kampanya tabanına GİRMEZ (indirimli fiyat geçerli). Admin
    # kampanya formundan kapatırsa (False) indirimli kalemler de kampanya tabanına dahil olur.
    # _has_manual_sale bayrağı _enrich_items_category_ids'ten gelir. (Rozet ⊆ motor: aynı
    # anahtar products._campaign_pct_for_product'ta da uygulanır.)
    skip_disc = c.get("skip_discounted", True)
    def _excl_sale(it):  # bu kalem indirimli-fiyatlı olduğu için dışlanmalı mı?
        return skip_disc and bool(it.get("_has_manual_sale"))
    if allowed_cats or allowed_pids:
        base = 0.0
        for it in items:
            if _item_in_scope(it, allowed_cats, allowed_pids, excluded_pids) and not _excl_sale(it):
                base += float(it.get("price", 0)) * int(it.get("qty", 0) or 0)
    else:
        base = cart_total
        # Kapsamsız (tüm sepet) kampanyada da indirimli-fiyatlı VE hariç-tutulan kalemleri tabandan düş.
        for it in items:
            _ex = excluded_pids and str(it.get("product_id")) in excluded_pids
            if _excl_sale(it) or _ex:
                base -= float(it.get("price", 0)) * int(it.get("qty", 0) or 0)
        base = max(0.0, base)
    ctype = c.get("type")
    discount = 0.0
    if ctype == "nth_discount":
        bq = int(c.get("buy_quantity") or 2)
        fq = int(c.get("free_quantity") or 1)
        gd = float(c.get("get_discount") or 0)
        units = []
        for it in items:
            _ex = excluded_pids and str(it.get("product_id")) in excluded_pids
            inscope = (not _ex) and ((not allowed_cats and not allowed_pids)
                                     or _item_in_scope(it, allowed_cats, allowed_pids, excluded_pids))
            if inscope and not _excl_sale(it):  # indirimli-fiyatlı kalem (anahtar açıksa) kampanyaya girmez
                for _ in range(int(it.get("qty", 0) or 0)):
                    units.append(float(it.get("price", 0) or 0))
        units.sort()  # en ucuz basta
        groups = (len(units) // bq) if bq else 0
        n_disc = groups * fq
        for k in range(min(n_disc, len(units))):
            discount += units[k] * (gd / 100.0)
    elif ctype == "bundle":
        # C4 — Ürün paketi (tek fiyat): tanımlı ürünlerin HEPSİ sepetteyse, her birinden
        # 1'er adedin toplam fiyatı yerine bundle_price uygulanır → indirim = taban - paket fiyatı.
        # Ürünlerden biri eksikse indirim 0 (paket koşulu sağlanmadı).
        bundle_pids = {str(x) for x in (c.get("products") or []) if x is not None}
        if bundle_pids:
            in_cart = {str(it.get("product_id")) for it in items if int(it.get("qty", 0) or 0) > 0}
            if bundle_pids.issubset(in_cart):
                base_b = 0.0
                for pid in bundle_pids:
                    prices = [float(it.get("price", 0) or 0) for it in items
                              if str(it.get("product_id")) == pid and float(it.get("price", 0) or 0) > 0]
                    base_b += min(prices) if prices else 0.0
                bp = float(c.get("bundle_price") or 0)
                if bp > 0 and base_b > bp:
                    discount = base_b - bp
    elif ctype == "percent":
        discount = base * (c.get("value", 0) / 100.0)
        if c.get("max_discount"):
            discount = min(discount, c["max_discount"])
    else:  # fixed
        discount = min(c.get("value", 0), base)
    return round(discount, 2)


async def _coupon_used_count(coupon_id: str, coupon_code: str = "", restrict_ors: list = None,
                             count_inflight: bool = True, inflight_active_only: bool = False) -> int:
    """A1.2: Kupon kullanım sayısı = kesin redemption ∪ HENÜZ ÖDENMEMİŞ (in-flight) siparişler.
    Sadece redemption saymak, 'çok pending sipariş oluştur sonra hepsini öde' baypasına açıktı;
    ödemesi tamamlanmamış ama kuponu uygulamış siparişleri de sayarak limit atomik-benzeri korunur.
    restrict_ors verilirse (per-user) hem redemption hem sipariş bu koşulla filtrelenir.

    count_inflight=False (OTOMATİK kampanyalar için): in-flight sayılmaz. Auto-apply kampanya
    HER uygun siparişe otomatik biner; terk edilmiş/ödenmemiş sepetler de 'kullanım' sayılırsa
    kampanya kısa sürede sahte-dolar ve indirim SESSİZCE durur (rozet gösterilir ama uygulanmaz).
    Bu vektör yalnız kullanıcı-GİRDİĞİ kıt kuponlarda (tek-kullanım) anlamlı → orada açık kalır.
    In-flight sayımı ayrıca yalnız SON 48 SAAT ile sınırlanır; eski terk sepetler limiti
    kalıcı tüketmesin (aksi halde meşru kupon zamanla 'tükenmiş' görünür)."""
    order_ids = set()
    # 1) Kesin redemption'lar (ödenmiş)
    rq = {"coupon_id": coupon_id}
    if restrict_ors:
        rq["$or"] = restrict_ors
    async for r in db.coupon_redemptions.find(rq, {"_id": 0, "order_id": 1}):
        if r.get("order_id"):
            order_ids.add(str(r["order_id"]))
    # 2) In-flight (ödenmemiş, iptal/başarısız olmayan) — YALNIZ istendiğinde ve SON 48 saat.
    if count_inflight:
        _since = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
        coup_or = [{"applied_promotions.coupon_id": coupon_id}]
        if coupon_code:
            coup_or.append({"coupon_code": {"$regex": f"^{re.escape(str(coupon_code))}$", "$options": "i"}})
        oq = {
            "$and": [
                {"$or": coup_or},
                {"status": {"$nin": ["cancelled", "cancel_refunded", "returned", "refunded", "return_approved", "returned_partial"]}},
                {"payment_status": {"$nin": ["paid", "failed", "expired", "refunded"]}},
                {"created_at": {"$gte": _since}},
            ]
        }
        if restrict_ors:
            oq["$and"].append({"$or": restrict_ors})
        if inflight_active_only:
            # Denetim #21 (kişi-başı rezervasyon): yalnız ÖDEMESİ ŞU AN SÜREN (başlatma kilidi aktif)
            # ve başka siparişle geçersiz kılınmamış sipariş hakkı tutar. Ödemesi başlamamış/yarıda
            # kalmış eski deneme hakkı TUTMAZ (müşteri yeniden deneyebilir) — yeni sipariş onu
            # supersede_prior_unpaid_coupon_orders ile ÖDENEMEZ yapar (aynı hak iki kez ödenemez).
            _now_ms = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
            oq["$and"].append({"coupon_superseded_by": {"$exists": False}})
            oq["$and"].append({"payment_init_lock_until": {"$gt": _now_ms}})
        async for o in db.orders.find(oq, {"_id": 0, "id": 1}).limit(1000):
            if o.get("id"):
                order_ids.add(str(o["id"]))
    return len(order_ids)


_REDEMPTION_UNIQUE_INDEX = "uniq_coupon_order"


async def ensure_coupon_redemption_unique_index() -> bool:
    """coupon_redemptions üzerinde (coupon_id, order_id) UNIQUE indeksini garanti eder.
    Index yoksa ÖNCE mevcut yinelenen satırlar temizlenir (her (coupon_id, order_id) için EN ESKİ
    satır kalır — kupon kullanım sayısı sipariş-bazlı olduğundan sayım değişmez), sonra index kurulur.
    Kısmi filtre: yalnız ikisi de string olan satırlar (eski/eksik alanlı satırlar çakışmaz).
    Index zaten varsa tek bir index_information() okumasıyla döner. Döner: index var mı."""
    try:
        info = await db.coupon_redemptions.index_information()
    except Exception:
        info = {}
    for _name, _spec in (info or {}).items():
        _keys = [k for k, _ in (_spec.get("key") or [])]
        if _spec.get("unique") and _keys == ["coupon_id", "order_id"]:
            return True
    removed = 0
    async for g in db.coupon_redemptions.aggregate([
        {"$match": {"coupon_id": {"$type": "string"}, "order_id": {"$type": "string"}}},
        {"$sort": {"_id": 1}},
        {"$group": {"_id": {"c": "$coupon_id", "o": "$order_id"}, "ids": {"$push": "$_id"}, "n": {"$sum": 1}}},
        {"$match": {"n": {"$gt": 1}}},
    ]):
        _dups = list(g.get("ids") or [])[1:]
        if _dups:
            res = await db.coupon_redemptions.delete_many({"_id": {"$in": _dups}})
            removed += int(getattr(res, "deleted_count", 0) or 0)
    if removed:
        logger.warning(f"[coupon] coupon_redemptions: {removed} yinelenen kullanım satırı temizlendi (unique index öncesi)")
    await db.coupon_redemptions.create_index(
        [("coupon_id", 1), ("order_id", 1)], unique=True, name=_REDEMPTION_UNIQUE_INDEX,
        partialFilterExpression={"coupon_id": {"$type": "string"}, "order_id": {"$type": "string"}})
    return True


async def record_coupon_redemption_once(coupon_id: str, order_id: str, doc: dict) -> bool:
    """İDEMPOTENT kupon kullanım kaydı (denetim 2026-10): (coupon_id, order_id) anahtarıyla
    upsert — find_one→insert_one deseninde eşzamanlı iki çağrı aynı sipariş için İKİ satır
    yazabiliyordu. Döner: True = yeni satır yazıldı, False = zaten vardı.
    Tam garanti: (coupon_id, order_id) UNIQUE index — ensure_coupon_redemption_unique_index() ile
    başlangıçta (seed_coupon_exceptions) ve burada garanti edilir (yinelenenler önce temizlenir);
    yarışı kaybeden çağrı DuplicateKey alır ve False döner.
    ZAMANLAMA DEĞİŞMEZ (değişmez 5): bu yardımcı yalnız çağrıldığı yerde yazar; kart siparişinde
    çağrı ödeme onayından SONRA (_notify_paid_order_confirmed → record_order_redemptions) yapılır."""
    if not coupon_id or not order_id:
        return False
    # UNIQUE index (coupon_id, order_id) garanti — upsert MongoDB'de index olmadan atomik değildir
    # (callback + webhook + sweep eşzamanlı → çift satır). Index varsa tek okuma; yoksa kurulur.
    try:
        await ensure_coupon_redemption_unique_index()
    except Exception as _ie:
        logger.warning(f"[coupon] redemption unique index doğrulanamadı: {_ie}")
    _doc = {k: v for k, v in (doc or {}).items() if k not in ("_id", "coupon_id", "order_id")}
    try:
        res = await db.coupon_redemptions.update_one(
            {"coupon_id": coupon_id, "order_id": order_id},
            {"$setOnInsert": {"coupon_id": coupon_id, "order_id": order_id, **_doc}},
            upsert=True)
        return bool(res.upserted_id)
    except Exception as e:
        # Unique index varken yarışı kaybeden çağrı DuplicateKey alır → zaten kayıtlı demektir.
        if "E11000" in str(e) or "duplicate key" in str(e).lower():
            return False
        raise


# ═══════════════════════════════════════════════════════════════════════════════════════════
# COM03 — KUPON KULLANIM LİMİTİ ATOMİK REZERVASYONU
# Sorun: usage_limit kontrolü (_coupon_used_count) SAYIM tabanlıydı → son kullanım hakkı aynı
# anda gelen N checkout'ta N kez geçebiliyordu. Çözüm: kupon başına sayaç belgesi
#   coupon_usage_counters {coupon_id, reserved, used, total}   (total = reserved + used)
# ve sipariş başına tutma kaydı
#   coupon_usage_holds    {coupon_id, order_id, state: reserved|used|released}
# create_order insert'ten ÖNCE `total < usage_limit` koşullu $inc ile hak AYIRIR (tek belge
# üzerinde atomik → limit aşılamaz). Redemption (coupon_redemptions) ZAMANLAMASI DEĞİŞMEZ
# (değişmez 5): kart siparişinde yine ödeme onayından SONRA yazılır; o anda tutma 'used' olur.
# İptal / otomatik iptal / ödeme süresi dolumu (release_order_redemptions kancası) ve sipariş
# açılamazsa (stok yetersiz, insert hatası) tutma 'released' olur ve hak geri döner.
# Kapsam: kullanıcının GİRDİĞİ (auto_apply olmayan) ve usage_limit'i olan kuponlar — değerlendirme
# (_evaluate_single) de yalnız bunlarda ödenmemiş siparişleri sayar; otomatik kampanyalar terk
# edilmiş sepetle sahte-dolmasın diye bilinçli olarak sayım tabanlı kalır. Kişi-başı limit,
# işletme kararıyla (canlı olay HOSGELDIN10) yalnız ÖDENMİŞ kullanımı sayar → rezervasyon yok.
# Geçiş: sayaç yoksa ilk kullanımda mevcut redemption'lar (used) + son 48 saatin ödenmemiş
# siparişleri (reserved) tutma kaydı olarak yazılıp sayaç bunlardan kurulur (tembel backfill).
# ═══════════════════════════════════════════════════════════════════════════════════════════
_USAGE_IDX_DONE = {"ok": False}
_INFLIGHT_EXCLUDED_STATUSES = ["cancelled", "cancel_refunded", "returned", "refunded", "return_approved",
                               "returned_partial", "payment_failed"]


def coupon_needs_reservation(c: Optional[dict]) -> bool:
    try:
        return bool(c and int(c.get("usage_limit") or 0) > 0 and not c.get("auto_apply"))
    except Exception:
        return False


async def _ensure_usage_indexes() -> None:
    if _USAGE_IDX_DONE["ok"]:
        return
    try:
        await db.coupon_usage_holds.create_index([("coupon_id", 1), ("order_id", 1)], unique=True,
                                                 name="uniq_coupon_hold")
        await db.coupon_usage_holds.create_index([("order_id", 1)], name="hold_order")
        await db.coupon_usage_counters.create_index([("coupon_id", 1)], unique=True, name="uniq_coupon_counter")
        _USAGE_IDX_DONE["ok"] = True
    except Exception as e:
        logger.warning(f"[coupon-usage] index kurulamadı: {type(e).__name__}: {e}")


async def _upsert_hold(cid: str, oid: str, state: str, now: str, **extra) -> None:
    try:
        await db.coupon_usage_holds.update_one(
            {"coupon_id": cid, "order_id": oid},
            {"$setOnInsert": {"coupon_id": cid, "order_id": oid, "state": state, "created_at": now, **extra}},
            upsert=True)
    except Exception as e:
        if "E11000" not in str(e) and "duplicate key" not in str(e).lower():
            raise


async def ensure_coupon_usage_counter(coupon: dict) -> None:
    """Sayaç yoksa mevcut kullanım + ödenmemiş siparişlerden kurar (idempotent, yarışa dayanıklı:
    tutma kayıtları $setOnInsert, sayaç $setOnInsert — ilk kuran kazanır, sonuç aynıdır)."""
    cid = str(coupon.get("id") or "")
    if not cid:
        return
    await _ensure_usage_indexes()
    if await db.coupon_usage_counters.find_one({"coupon_id": cid}, {"_id": 1}):
        return
    now = datetime.now(timezone.utc).isoformat()
    for r in await db.coupon_redemptions.find({"coupon_id": cid}, {"_id": 0, "order_id": 1}).to_list(100000):
        if r.get("order_id"):
            await _upsert_hold(cid, str(r["order_id"]), "used", now, backfill=True)
    _since = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
    coup_or = [{"applied_promotions.coupon_id": cid}]
    if coupon.get("code"):
        coup_or.append({"coupon_code": {"$regex": f"^{re.escape(str(coupon['code']))}$", "$options": "i"}})
    oq = {"$and": [
        {"$or": coup_or},
        {"status": {"$nin": _INFLIGHT_EXCLUDED_STATUSES}},
        {"payment_status": {"$nin": ["paid", "failed", "expired", "refunded"]}},
        {"created_at": {"$gte": _since}},
    ]}
    for o in await db.orders.find(oq, {"_id": 0, "id": 1}).limit(5000).to_list(5000):
        if o.get("id"):
            await _upsert_hold(cid, str(o["id"]), "reserved", now, backfill=True)
    reserved = await db.coupon_usage_holds.count_documents({"coupon_id": cid, "state": "reserved"})
    used = await db.coupon_usage_holds.count_documents({"coupon_id": cid, "state": "used"})
    try:
        await db.coupon_usage_counters.update_one(
            {"coupon_id": cid},
            {"$setOnInsert": {"coupon_id": cid, "reserved": reserved, "used": used,
                              "total": reserved + used, "created_at": now, "backfilled": True}},
            upsert=True)
    except Exception as e:
        if "E11000" not in str(e) and "duplicate key" not in str(e).lower():
            raise


async def reserve_coupon_usage(coupon: dict, order_id: str) -> bool:
    """Bu sipariş için kuponun BİR kullanım hakkını atomik ayırır. Döner: True = ayrıldı
    (ya da kupon rezervasyon gerektirmiyor), False = limit dolu."""
    if not coupon_needs_reservation(coupon):
        return True
    cid, oid = str(coupon["id"]), str(order_id)
    limit = int(coupon.get("usage_limit") or 0)
    await ensure_coupon_usage_counter(coupon)
    now = datetime.now(timezone.utc).isoformat()
    res = await db.coupon_usage_counters.update_one(
        {"coupon_id": cid, "total": {"$lt": limit}},
        {"$inc": {"reserved": 1, "total": 1}, "$set": {"updated_at": now}})
    if getattr(res, "modified_count", 0) != 1:
        return False
    try:
        await db.coupon_usage_holds.insert_one({"coupon_id": cid, "order_id": oid, "state": "reserved",
                                                "created_at": now})
    except Exception:
        # Tutma yazılamadı (aynı sipariş için zaten var / DB hatası) → ayrılan hakkı geri ver.
        await db.coupon_usage_counters.update_one({"coupon_id": cid, "reserved": {"$gt": 0}},
                                                  {"$inc": {"reserved": -1, "total": -1}})
        raise
    return True


async def reserve_order_coupon_usage(order: dict) -> dict:
    """Siparişin uyguladığı (rezervasyon gerektiren) tüm kuponlar için hak ayırır. Biri bile dolu
    ise bu siparişin ayırdıkları geri verilir. Döner: {ok, reserved:[coupon_id], code?}."""
    oid = str((order or {}).get("id") or "")
    ids = []
    for a in (order or {}).get("applied_promotions") or []:
        cid = (a or {}).get("coupon_id")
        if cid and cid not in ("edu_discount", "member_discount") and cid not in ids:
            ids.append(str(cid))
    if not oid or not ids:
        return {"ok": True, "reserved": []}
    coupons = {c["id"]: c async for c in db.coupons.find({"id": {"$in": ids}}, {"_id": 0})}
    taken = []
    for cid in ids:
        c = coupons.get(cid)
        if not coupon_needs_reservation(c):
            continue
        try:
            ok = await reserve_coupon_usage(c, oid)
        except Exception:
            await release_coupon_usage(oid)
            raise
        if not ok:
            await release_coupon_usage(oid)
            return {"ok": False, "reserved": [], "code": c.get("code") or ""}
        taken.append(cid)
    return {"ok": True, "reserved": taken}


async def mark_coupon_usage_used(coupon_id: str, order_id: str) -> None:
    """Redemption yazıldığında (ödeme onayı / havale-kapıda oluşturma) tutmayı 'used' yapar.
    Rezervasyonu olmayan (eski / geç ödeme ile iptalden dönen) sipariş de sayaca 'used' olarak
    eklenir — ödenmiş sipariş reddedilemez, ama sayım doğru kalır. İdempotent."""
    cid, oid = str(coupon_id or ""), str(order_id or "")
    if not cid or not oid:
        return
    c = await db.coupons.find_one({"id": cid}, {"_id": 0, "id": 1, "code": 1, "usage_limit": 1, "auto_apply": 1})
    if not coupon_needs_reservation(c):
        return
    await _ensure_usage_indexes()
    now = datetime.now(timezone.utc).isoformat()
    h = await db.coupon_usage_holds.find_one_and_update(
        {"coupon_id": cid, "order_id": oid, "state": "reserved"},
        {"$set": {"state": "used", "used_at": now}})
    if h:
        await db.coupon_usage_counters.update_one(
            {"coupon_id": cid, "reserved": {"$gt": 0}},
            {"$inc": {"reserved": -1, "used": 1}, "$set": {"updated_at": now}})
        return
    cur = await db.coupon_usage_holds.find_one({"coupon_id": cid, "order_id": oid}, {"_id": 0, "state": 1})
    if cur and cur.get("state") == "used":
        return
    counted = False
    if cur:   # released → yeniden kullanıldı (iptal sonrası geç ödeme)
        r = await db.coupon_usage_holds.update_one({"coupon_id": cid, "order_id": oid, "state": "released"},
                                                   {"$set": {"state": "used", "used_at": now}})
        counted = getattr(r, "modified_count", 0) == 1
    else:
        try:
            await db.coupon_usage_holds.insert_one({"coupon_id": cid, "order_id": oid, "state": "used",
                                                    "created_at": now, "used_at": now, "unreserved": True})
            counted = True
        except Exception as e:
            if "E11000" not in str(e) and "duplicate key" not in str(e).lower():
                raise
    if counted:
        await db.coupon_usage_counters.update_one({"coupon_id": cid},
                                                  {"$inc": {"used": 1, "total": 1}, "$set": {"updated_at": now}})


async def release_coupon_usage(order_id: str) -> int:
    """Siparişin TÜM kupon tutmalarını serbest bırakır (iptal / ödeme süresi dolumu / sipariş
    açılamadı). Her tutma bir kez düşülür (koşullu durum geçişi). Döner: bırakılan tutma sayısı."""
    oid = str(order_id or "")
    if not oid:
        return 0
    n = 0
    now = datetime.now(timezone.utc).isoformat()
    rows = await db.coupon_usage_holds.find({"order_id": oid, "state": {"$in": ["reserved", "used"]}},
                                            {"_id": 0, "coupon_id": 1, "state": 1}).to_list(50)
    for h in rows:
        st = h.get("state")
        r = await db.coupon_usage_holds.update_one(
            {"coupon_id": h["coupon_id"], "order_id": oid, "state": st},
            {"$set": {"state": "released", "released_at": now, "released_from": st}})
        if getattr(r, "modified_count", 0) == 1:
            await db.coupon_usage_counters.update_one(
                {"coupon_id": h["coupon_id"], st: {"$gt": 0}},
                {"$inc": {st: -1, "total": -1}, "$set": {"updated_at": now}})
            n += 1
    return n


import time as _time
_EXEMPT_CACHE = {"emails": set(), "at": 0.0}


async def _get_first_order_exempt_emails() -> set:
    """İlk-sipariş + kişi-başı-limit kontrolünden MUAF müşteri e-postaları (istisna listesi).
    Admin, settings.id='coupon_first_order_exceptions'.emails üzerinden yönetir. 60 sn cache."""
    now = _time.time()
    if now - _EXEMPT_CACHE["at"] < 60:
        return _EXEMPT_CACHE["emails"]
    try:
        doc = await db.settings.find_one({"id": "coupon_first_order_exceptions"}, {"_id": 0, "emails": 1})
        emails = {str(e).strip().lower() for e in ((doc or {}).get("emails") or []) if str(e).strip()}
    except Exception:
        emails = _EXEMPT_CACHE["emails"]
    _EXEMPT_CACHE["emails"] = emails
    _EXEMPT_CACHE["at"] = now
    return emails


async def _evaluate_single(c: dict, cart_total: float, items: list,
                           user_id=None, email: str = "", payment_method: str = "") -> dict:
    """Tek kuponu dogrular + indirimini hesaplar. apply_coupon VE motor ayni cekirdegi kullanir."""
    if not c.get("is_active"):
        return {"valid": False, "reason": "Kupon pasif", "discount": 0}
    # İSTİSNA: admin bazı müşterileri ilk-sipariş/kişi-başı-limit kontrolünden muaf tutabilir
    # (ör. destek talebiyle hoş geldin kuponunu tekrar kullanma izni). E-posta muaf listesindeyse
    # o iki kısıt atlanır; diğer tüm kontroller (pasif/tarih/min-tutar/ödeme yöntemi) geçerli kalır.
    # Denetim #19: muafiyet yalnız GİRİŞ YAPMIŞ hesabın kayıtlı e-postasına uygulanır (misafir kasaya
    # muaf bir e-posta yazarak ilk-sipariş/kişi-başı kısıtlarını atlayamaz).
    _acct = await _account_identity(user_id)
    _exempt_list = await _get_first_order_exempt_emails()
    _exempt = bool(_acct.get("email")) and (_acct["email"] in {_norm_email(x) for x in _exempt_list})
    # DENETİM SEC-2 F1: kişiye özel ödül kuponu (referral/doğum günü/hoş geldin) YALNIZ sahibine.
    # create_order buraya SUNUCU-türetilmiş user_id/email geçirir → başkasının kuponu yakılamaz.
    if not _coupon_owner_ok(c, user_id, email):
        return {"valid": False, "reason": "Bu kupon size ait değil", "discount": 0}
    # Odeme yontemi filtresi: kampanyanin payment_methods listesi doluysa, secili yontem listede olmali.
    # ÖNEMLİ (önizleme): Sepet/vitrin değerlendirmesinde henüz ödeme yöntemi SEÇİLMEMİŞ olur
    # (payment_method boş). Bu durumda kampanyayı reddetmek, ödeme-kısıtlı kampanyalı ürünü
    # sepette "indirimsiz" gösteriyordu (rozet 10% var ama sepet tam fiyat). Bu yüzden yöntem
    # BOŞken kısıt UYGULANMAZ — indirim iyimser gösterilir; müşteri bir yöntem seçince (ve sipariş
    # oluşturmada payment_method HER ZAMAN dolu geldiğinden) kısıt orada gerçekten uygulanır →
    # çekilen tutar doğru kalır.
    _pms = c.get("payment_methods") or []
    if _pms and payment_method and _norm_pm(payment_method) not in [_norm_pm(x) for x in _pms]:
        return {"valid": False, "reason": "Bu kampanya bu ödeme yönteminde geçerli değil", "discount": 0}
    now = datetime.now(timezone.utc)
    if c.get("start_at") and c["start_at"] > now.isoformat():
        return {"valid": False, "reason": "Kupon henüz başlamadı", "discount": 0}
    # L1: end_at yalnızca tarih (YYYY-MM-DD) olarak kaydedilmişse, o günün SONUNA kadar geçerli
    # sayılır. Aksi halde ham string karşılaştırması ("2026-07-10" < "2026-07-10T08:..") kuponu
    # bitiş gününün başında öldürüyor, kullanıcı bir gün erken kaybediyordu.
    _end = c.get("end_at")
    if _end:
        if len(str(_end)) == 10 and "T" not in str(_end):
            _end = f"{_end}T23:59:59+00:00"
        if _end < now.isoformat():
            return {"valid": False, "reason": "Kupon süresi dolmuş", "discount": 0}
    if c.get("min_cart_total", 0) and cart_total < c["min_cart_total"]:
        return {"valid": False, "reason": f"Minimum sepet tutarı ₺{c['min_cart_total']:.2f}", "discount": 0}
    if c.get("usage_limit"):
        # A1.2: redemption + in-flight (ödenmemiş) siparişler birlikte sayılır (baypas kapatıldı).
        # OTOMATİK kampanyada in-flight SAYILMAZ (terk edilmiş sepetler kampanyayı sahte-doldurup
        # rozet-var/indirim-yok durumuna sokuyordu) — kıt/kullanıcı-girdiği kuponlarda açık kalır.
        _auto = bool(c.get("auto_apply"))
        used = await _coupon_used_count(c["id"], c.get("code", ""), count_inflight=not _auto)
        if used >= c["usage_limit"]:
            return {"valid": False, "reason": "Kupon kullanım limiti dolmuş", "discount": 0}
    if c.get("usage_limit_per_user") and not _exempt:
        _ors = []
        if user_id:
            _ors.append({"user_id": user_id})
        # Denetim #19: hesap e-postası + kasa e-postası, siparişin TÜM e-posta alanlarında (iç içe dahil),
        # '+etiket'/harf farkına duyarsız aranır.
        for _e in {_acct.get("email") or "", _norm_email(email)}:
            if _e:
                _ors.extend(_email_match_clauses(_e))
        if _ors:
            # DENETİM (canlı olay, HOSGELDIN10 "çalışmıyor"): kişi-başı limit, müşterinin kendi
            # ÖDENMEMİŞ (awaiting_payment — 3DS yarıda kalan/terk edilen) siparişlerini de "kullanım"
            # sayıyordu → ilk denemesi başarısız olan müşteri, sipariş otomatik iptal edilene kadar
            # (3 saate dek) "kullanım hakkınız kalmadı" ile KİLİTLENİYORDU ve tekrar deneyemiyordu.
            # first_order_only ile TUTARLI kural: yalnız GERÇEKTEN ödenmiş sipariş (redemption)
            # müşterinin kişisel hakkını tüketir (redemption zaten ödeme onayından SONRA yazılır —
            # değişmez #5). Aynı kişinin çift-kullanım riski, her siparişin ayrı ayrı gerçek kartla
            # ödenmesini gerektirdiğinden sınırlıdır; buna karşılık meşru yeniden-deneme engeli satış
            # kaybettiriyordu. Global usage_limit (kıt kupon) için in-flight koruması AYNEN kalır.
            used_by_user = await _coupon_used_count(c["id"], c.get("code", ""), restrict_ors=_ors,
                                                    count_inflight=True, inflight_active_only=True)
            if used_by_user >= c["usage_limit_per_user"]:
                return {"valid": False, "reason": "Bu kupon için kullanım hakkınız kalmadı", "discount": 0}
    if c.get("first_order_only") and not _exempt:
        # Denetim #19: "ilk sipariş" bir HESAP niteliğidir — misafirin her seferinde yeni (doğrulanmamış)
        # e-posta yazarak tekrar tekrar ilk-sipariş indirimi alması engellenir. Önceki sipariş; hesap
        # kimliği + hesap e-postası + kasa e-postası ile siparişin tüm e-posta alanlarında aranır.
        if not user_id:
            return {"valid": False, "reason": "İlk siparişe özel kupon için giriş yapın", "discount": 0}
        ors = [{"user_id": user_id}]
        for _e in {_acct.get("email") or "", _norm_email(email)}:
            if _e:
                ors.extend(_email_match_clauses(_e))
        # DENETİM (canlı olay): "önceki sipariş" YALNIZ GERÇEKTEN ödenmiş/tamamlanmış sipariştir.
        # Eskiden awaiting_payment/pending (3DS yarıda kalan, henüz ödenmemiş) siparişler de
        # sayılıyordu → müşteri kartı çekilmeden ilk-sipariş kuponu hakkını KAYBEDİYORDU
        # (HOSGELDIN10 "kullanamıyorum" kök nedeni). Yalnız payment_status=paid VEYA fulfillment
        # statüsündeki siparişler önceki sipariş sayılır.
        prior = await db.orders.count_documents({
            "$and": [
                {"$or": ors},
                {"$or": [
                    {"payment_status": "paid"},
                    {"status": {"$in": ["confirmed", "processing", "preparing", "shipped",
                                        "delivered", "undelivered", "returned", "refunded",
                                        "partial_refunded", "cancel_requested"]}},
                ]},
            ],
        })
        if prior > 0:
            return {"valid": False, "reason": "Kupon sadece ilk siparişe özeldir", "discount": 0}
    if c.get("min_quantity"):
        total_qty = sum(int(it.get("qty", 0) or 0) for it in items)
        if total_qty < int(c["min_quantity"]):
            return {"valid": False, "reason": f"En az {int(c['min_quantity'])} ürün gerekli", "discount": 0}
    discount = _compute_discount(c, cart_total, items)
    return {
        "valid": True, "coupon_id": c["id"], "code": c["code"],
        "title": c.get("title", ""), "type": c.get("type"), "value": c.get("value"),
        "discount": discount, "free_shipping": bool(c.get("free_shipping")),
    }


@public_router.post("/apply")
@(limiter.limit("30/minute") if limiter else (lambda f: f))   # kupon kodu keşfi (brute force) engeli
async def apply_coupon(payload: dict, request: Request, current_user: dict = Depends(get_current_user)):
    if not (payload.get("code") or "").strip():
        return {"valid": False, "reason": "Kupon kodu boş", "discount": 0}
    # BULANIK ÇÖZÜMLEME: her yazım varyantı sistemdeki gerçek kupona eşlenir.
    c, _canon = await resolve_coupon_code(payload.get("code") or "")
    if not c:
        return {"valid": False, "reason": "Kupon bulunamadı", "discount": 0}
    code = c.get("code") or _canon
    cart_total = float(payload.get("cart_total") or 0)
    items = payload.get("items") or []
    # CANLI DENETİM: /apply kalemleri 'quantity' ile gelirken indirim motoru 'qty' okuyor → adet 0
    # sayılıp indirimli-ürün (sale_price) dışlaması ve adet-bazlı kurallar ATLANIYORDU. Sonuç:
    # /apply "geçerli ₺138" derken checkout (/evaluate, 'qty' gönderir) aynı sepeti REDDEDİYORDU —
    # müşteri "kupon uygulandı" görüp ödemede kaybediyordu. Aynı motor, aynı alan: qty'yi doldur.
    items = [{**it, "qty": int(it.get("qty", it.get("quantity", 1)) or 1)} for it in items if isinstance(it, dict)]
    items = await _enrich_items_category_ids(items)
    # SEC-2 F1: kimlik doğrulanmışsa token'dan (istemci user_id'sine güvenilmez); misafirde e-posta.
    # Denetim #19: user_id YALNIZ doğrulanmış oturumdan (istemcinin gönderdiği user_id'ye güvenilmez).
    _uid = (current_user or {}).get("id")
    email = ((current_user or {}).get("email")
             or payload.get("email") or payload.get("customer_email") or "")
    return await _evaluate_single(c, cart_total, items, _uid, email,
                                  payload.get("payment_method") or "")


@public_router.post("/redeem")
async def redeem_coupon(payload: dict, current_user: dict = Depends(require_auth)):
    """DEVRE DIŞI (DENETİM SEC-2 F2): Kupon kullanımı YALNIZ ödeme onayından sonra sunucu
    tarafında record_order_redemptions() ile yazılır. Bu HTTP ucu ana akışta kullanılmıyordu
    ve istemciden gelen coupon_id/order_id/user_id ile herhangi bir kuponun kullanım limiti /
    bir kurbanın kişi-başı kotası ŞİŞİRİLEBİLİYORDU. Artık HİÇBİR ŞEY yazmaz."""
    return {"recorded": False, "message": "Bu uç devre dışı; kupon kullanımı ödeme onayında kaydedilir."}


# ==================== KAMPANYALAR (Campaigns) ====================
# Admin "Kampanyalar" sayfası /api/campaigns çağırır. Kampanya = kupon olduğundan
# bu uçlar db.coupons üzerine eşlenir (tip: percentage<->percent, free_shipping bayrağı).
campaigns_router = APIRouter(prefix="/campaigns", tags=["campaigns"])


def _coupon_to_campaign(c: dict) -> dict:
    t = c.get("type", "percent")
    if c.get("free_shipping"):
        ctype = "free_shipping"
    elif t == "percent":
        ctype = "percentage"
    else:
        ctype = t
    return {
        "id": c.get("id"),
        "name": c.get("title") or c.get("code") or "",
        "code": c.get("code", ""),
        "type": ctype,
        "value": c.get("value", 0),
        "min_order_amount": c.get("min_cart_total", 0),
        "usage_limit": c.get("usage_limit") or 0,
        "is_active": c.get("is_active", True),
        "start_date": c.get("start_at"),
        "end_date": c.get("end_at"),
        "redeemed_count": c.get("redeemed_count", 0),
        "auto_apply": c.get("auto_apply", False),
        "first_order_only": c.get("first_order_only", False),
        "usage_limit_per_user": c.get("usage_limit_per_user") or 0,
        "min_quantity": c.get("min_quantity") or 0,
        "buy_quantity": c.get("buy_quantity") or 0,
        "free_quantity": c.get("free_quantity") or 1,
        "get_discount": c.get("get_discount") or 0,
        "max_discount": c.get("max_discount") or 0,
        "priority": c.get("priority", 0),
        "combinable": bool(c.get("combinable", True)),
        "stack_group": c.get("stack_group") or "",
        "categories": c.get("categories") or [],
        "products": c.get("products") or [],
        "excluded_products": c.get("excluded_products") or [],
        "excluded_products_raw": c.get("excluded_products_raw") or "",
        "combinable_with": c.get("combinable_with") or [],
        "not_combinable_with": c.get("not_combinable_with") or [],
        "title": c.get("title") or "",
        "payment_methods": c.get("payment_methods") or [],
        "skip_discounted": bool(c.get("skip_discounted", True)),
        "badge_text": c.get("badge_text") or "",
    }


def _campaign_to_coupon_fields(payload: dict) -> dict:
    ctype = payload.get("type") or "percentage"
    if ctype == "fixed":
        type_db = "fixed"
    elif ctype == "nth_discount":
        type_db = "nth_discount"
    elif ctype == "bundle":
        type_db = "bundle"  # C4 — ürün paketi (tek fiyat)
    else:
        type_db = "percent"  # "percentage" ve "free_shipping" yuzde tabanli calisir
    return {
        "title": payload.get("name") or payload.get("title") or "",
        "type": type_db,
        "value": float(payload.get("value", 0) or 0),
        "min_cart_total": float(payload.get("min_order_amount", payload.get("min_cart_total", 0)) or 0),
        "usage_limit": int(payload.get("usage_limit", 0) or 0) or None,
        # --- Kural alanlari (onceden tasinmiyordu; "ilk uyelik herkese" bug'inin koku) ---
        "usage_limit_per_user": int(payload.get("usage_limit_per_user", 0) or 0) or None,
        "first_order_only": bool(payload.get("first_order_only", False)),
        "min_quantity": int(payload.get("min_quantity", 0) or 0) or None,
        "categories": payload.get("categories") or [],
        "products": payload.get("products") or [],
        "max_discount": (float(payload["max_discount"]) if payload.get("max_discount") else None),
        # --- "X al Y ode / N. urune %Z" icin ---
        "buy_quantity": int(payload.get("buy_quantity", 0) or 0) or None,
        "free_quantity": int(payload.get("free_quantity", 1) or 1),
        "get_discount": float(payload.get("get_discount", 0) or 0),
        "bundle_price": float(payload.get("bundle_price", 0) or 0) or None,  # C4 — paket fiyatı
        "start_at": _window_from_payload(payload, "start_at", "start_date"),
        "end_at": _window_from_payload(payload, "end_at", "end_date", end_of_day=True),
        "is_active": bool(payload.get("is_active", True)),
        "free_shipping": ctype == "free_shipping",
        "auto_apply": bool(payload.get("auto_apply", False)),
        # --- Madde 4 motor alanları ---
        "priority": int(payload.get("priority", 0) or 0),
        "combinable": bool(payload.get("combinable", True)),
        "stack_group": (payload.get("stack_group") or "").strip() or None,
        "combinable_with": payload.get("combinable_with") or [],
        "not_combinable_with": [str(x) for x in (payload.get("not_combinable_with") or []) if x],
        "payment_methods": payload.get("payment_methods") or [],
        # İndirimli fiyatı (sale_price) olan ürünlere kampanya uygulansın mı? Varsayılan True = uygulama.
        "skip_discounted": bool(payload.get("skip_discounted", True)),
        # Vitrin ibaresi (X al Y öde); boşsa kurgudan otomatik üretilir.
        "badge_text": str(payload.get("badge_text") or "").strip()[:40],
    }


async def _excluded_fields_from_payload(payload: dict):
    """Payload'daki 'excluded_products_raw' (admin'in yazdığı metin) alanını çözer.
    Alan HİÇ yoksa None döner (kısmi güncellemede mevcut hariç-listeyi ezme). Boş string
    verilirse hariç-liste temizlenir. Döndürülen dict doğrudan coupon dokümanına $set edilir."""
    if "excluded_products_raw" not in payload and "excluded_products" not in payload:
        return None
    raw = payload.get("excluded_products_raw")
    if raw is None:  # sadece çözülmüş liste gelmişse onu da kabul et
        raw = payload.get("excluded_products")
    pids = await _resolve_excluded_pids(raw)
    return {"excluded_products": pids,
            "excluded_products_raw": (raw if isinstance(raw, str) else "")}


@campaigns_router.get("")
async def list_campaigns(include_coupons: int = Query(0), current_user: dict = Depends(require_permission("campaigns.view", any_in_area=True))):
    """Kampanya listesi — frontend düz dizi bekler (res.data).
    Varsayılan: yalnız OTOMATİK kampanyalar (auto_apply / AUTO- kodlu). Kod bazlı kuponlar
    Kuponlar sayfasındadır (kullanıcı isteği: kupon kodları kampanyalara düşmesin).
    include_coupons=1 → hepsi (engelleme seçicileri için)."""
    q = {} if include_coupons else {"$or": [{"auto_apply": True}, {"code": {"$regex": "^AUTO-"}}]}
    items = await db.coupons.find(q, {"_id": 0}).sort("created_at", -1).to_list(500)
    out = []
    for c in items:
        c["redeemed_count"] = await db.coupon_redemptions.count_documents({"coupon_id": c.get("id")})
        out.append(_coupon_to_campaign(c))
    return out


@campaigns_router.post("")
async def create_campaign(payload: dict, current_user: dict = Depends(require_permission("campaigns.create"))):
    code = (payload.get("code") or "").strip().upper()
    if not code and payload.get("auto_apply"):
        code = "AUTO-" + uuid.uuid4().hex[:6].upper()
    if not code:
        raise HTTPException(status_code=400, detail="Kampanya kodu gerekli")
    if await db.coupons.find_one({"code": code}):
        raise HTTPException(status_code=409, detail="Bu kod zaten kullanılıyor")
    doc = {
        "id": str(uuid.uuid4()),
        "code": code,
        "categories": [],
        "products": [],
        "max_discount": None,
        "usage_limit_per_user": None,
        "first_order_only": False,
        "created_at": _utcnow(),
        "created_by": current_user.get("email", ""),
    }
    doc.update(_campaign_to_coupon_fields(payload))
    _exf = await _excluded_fields_from_payload(payload)
    if _exf is not None:
        doc.update(_exf)
    await db.coupons.insert_one(doc)
    doc.pop("_id", None)
    return {"success": True, "campaign": _coupon_to_campaign(doc)}


@campaigns_router.put("/{cid}")
async def update_campaign(cid: str, payload: dict, current_user: dict = Depends(require_permission("campaigns.manage"))):
    update = _campaign_to_coupon_fields(payload)
    _exf = await _excluded_fields_from_payload(payload)
    if _exf is not None:
        update.update(_exf)
    update["updated_at"] = _utcnow()
    res = await db.coupons.update_one({"id": cid}, {"$set": update})
    if res.matched_count == 0:
        raise HTTPException(status_code=404, detail="Kampanya bulunamadı")
    return {"success": True}


@campaigns_router.delete("/{cid}")
async def delete_campaign(cid: str, current_user: dict = Depends(require_permission("campaigns.delete"))):
    res = await db.coupons.delete_one({"id": cid})
    if res.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Kampanya bulunamadı")
    return {"success": True}


# ============================================================================
# Madde 4 — Kampanya Motoru (P3)
# Kilitli kurallar: priority sirali; combinable=False MUNHASIR; combinable=True
# stack_group basina bir, KALAN tabana ARDISIK (once %20 sonra %10); GLOBAL TAVAN.
# ============================================================================

async def _promo_cap_pct() -> float:
    s = await db.settings.find_one({"id": "main"}, {"_id": 0, "promo_max_discount_pct": 1}) or {}
    try:
        v = float(s.get("promo_max_discount_pct", 70) or 70)
    except Exception:
        v = 70.0
    return v if v > 0 else 70.0


def fold_code(s) -> str:
    """Kupon kodunu HARF-DUYARSIZ + TÜRKÇE-I DUYARSIZ tek forma indirger.
    Kullanıcı 'HOSGELDİN' (noktalı İ), 'hosgeldin', 'hosgeldın' (noktasız ı) yazsa da
    hepsi kayıtlı 'HOSGELDIN' ile eşleşsin (kullanıcı isteği). İ/ı → ASCII I, sonra upper."""
    folded = (s or "").strip().translate(str.maketrans({
        "İ": "I", "ı": "I", "Ş": "S", "ş": "S", "Ğ": "G", "ğ": "G",
        "Ü": "U", "ü": "U", "Ö": "O", "ö": "O", "Ç": "C", "ç": "C",
    })).upper()
    compact = re.sub(r"[\s_\-]+", "", folded)
    if compact in {"HOSGELDIN", "HOSGELDIN10"}:
        return "HOSGELDIN10"
    return folded


# ── BULANIK KUPON ÇÖZÜMLEYİCİ ───────────────────────────────────────────────────
# Müşteri kupon alanına ne yazarsa yazsın (HOŞGELDİN10 / hoş geldin %10 / HOSGELDIN-10 /
# HOSGELDIN1O (harf O) / HOSGELDN10 (harf eksik) …) sistemdeki hangi kuponu kastettiğini
# algılar. Sabit bir HOSGELDIN listesi DEĞİL, DB'deki gerçek kuponlara karşı normalize edip
# eşler. Yanlış kupona ATLAMAMAK için katı sıra + eşikler (aşağıda).
_CODE_ALNUM = re.compile(r"[^A-Z0-9]+")
_TR_FOLD = str.maketrans({
    "İ": "I", "ı": "I", "Ş": "S", "ş": "S", "Ğ": "G", "ğ": "G",
    "Ü": "U", "ü": "U", "Ö": "O", "ö": "O", "Ç": "C", "ç": "C",
})
_CONFUSABLE = str.maketrans({"O": "0", "I": "1", "L": "1"})


def _norm_code(s) -> str:
    """Karşılaştırma formu: Türkçe harf katla → BÜYÜK → yalnız A-Z0-9 (boşluk, %, -, _, nokta
    atılır). 'hoş geldin %10' → 'HOSGELDIN10'; 'HAVALE%5' → 'HAVALE5'."""
    return _CODE_ALNUM.sub("", (s or "").strip().translate(_TR_FOLD).upper())


def _norm_code2(s) -> str:
    """İkinci geçiş: sık karıştırılan karakterler eşitlenir (harf O ↔ sıfır, I/L ↔ bir).
    İKİ tarafa da uygulandığından karşılaştırma tutarlıdır ('HOSGELDIN1O' ≡ 'HOSGELDIN10')."""
    return _norm_code(s).translate(_CONFUSABLE)


_CODES_CACHE = {"rows": [], "at": 0.0}


async def _all_coupon_codes() -> list:
    """Tüm kuponların (aktif+pasif) hafif listesi, 60 sn önbellek — evaluate her sepet
    değişiminde çağrıldığından DB yorulmasın. Kupon yazımında invalidate_codes_cache()."""
    now = _time.time()
    if now - _CODES_CACHE["at"] < 60 and _CODES_CACHE["rows"]:
        return _CODES_CACHE["rows"]
    rows = []
    async for c in db.coupons.find({}, {"_id": 0, "code": 1, "id": 1, "is_active": 1,
                                        "reward_user_id": 1, "reward_email": 1}):
        if c.get("code"):
            rows.append(c)
    _CODES_CACHE["rows"], _CODES_CACHE["at"] = rows, now
    return rows


def invalidate_codes_cache():
    _CODES_CACHE["at"] = 0.0


async def resolve_coupon_code(entered: str):
    """Müşterinin yazdığı kodu sistemdeki kupona çözümler → (coupon_doc | None, canonical_code).

    Sıra (güvenlik: yanlış kupona ATLAMAMAK için):
      1) fold_code (mevcut hoş-geldin takma adı HOSGELDIN→HOSGELDIN10 korunur) + normalize.
      2) TAM eşleşme — TÜM kuponlarda (pasif dahil): pasif kupon 'Kupon pasif' desin, başka
         bir kupona sıçramasın.
      3) Karışan-karakter eşleşmesi (O↔0, I/L↔1) — tüm kuponlarda.
      4) BULANIK — YALNIZ aktif + kişiye özel OLMAYAN + takma ad OLMAYAN kuponlar; girilen ≥5 kr:
         a) önek/kapsama: biri diğerinin başlangıcı, kısa olan uzunun ≥%70'i (HOSGELDIN1 ~ HOSGELDIN10)
         b) benzerlik (difflib) ≥ 0.80 → yazım hatası (HOSGELDN10, HOSGELDIM10)
         Her iki adımda en iyi aday BENZERSİZ olmalı (ikinciyle fark ≥ 0.05); belirsizlikte
         tahmin ETMEZ → None (müşteri 'geçersiz' görür, yanlış indirim uygulanmaz).
    Kişiye özel ödül kuponları (reward_user_id/email) asla bulanık eşlenmez (yalnız tam)."""
    raw = fold_code(entered or "")
    n1 = _norm_code(raw)
    if not n1:
        return None, ""
    rows = await _all_coupon_codes()

    async def _full(c):
        return await db.coupons.find_one({"id": c["id"]}, {"_id": 0}), c["code"]

    # 2) tam
    for c in rows:
        if _norm_code(c["code"]) == n1:
            return await _full(c)
    # 3) karışan karakter
    n2 = _norm_code2(raw)
    for c in rows:
        if _norm_code2(c["code"]) == n2:
            return await _full(c)
    # 4) bulanık
    if len(n1) < 5:
        return None, raw
    pool = [c for c in rows
            if c.get("is_active") and not c.get("reward_user_id") and not c.get("reward_email")
            and fold_code(c["code"]) == c["code"]]   # takma ad olan kod (HOSGELDIN→HOSGELDIN10) havuz dışı
    # a) önek / kapsama
    pre = []
    for c in pool:
        cn = _norm_code(c["code"])
        if not cn:
            continue
        short, long_ = (n1, cn) if len(n1) <= len(cn) else (cn, n1)
        if len(short) >= 5 and long_.startswith(short) and len(short) / len(long_) >= 0.7:
            pre.append((len(short) / len(long_), c))
    if pre:
        pre.sort(key=lambda x: x[0], reverse=True)
        if len(pre) == 1 or pre[0][0] - pre[1][0] >= 0.05:
            return await _full(pre[0][1])
    # b) benzerlik
    import difflib
    scored = []
    for c in pool:
        cn = _norm_code(c["code"])
        if not cn:
            continue
        r = max(difflib.SequenceMatcher(None, n1, cn).ratio(),
                difflib.SequenceMatcher(None, n2, _norm_code2(c["code"])).ratio())
        scored.append((r, c))
    scored.sort(key=lambda x: x[0], reverse=True)
    if scored and scored[0][0] >= 0.80 and (len(scored) == 1 or scored[0][0] - scored[1][0] >= 0.05):
        return await _full(scored[0][1])
    return None, raw


def _allocate_discount(c: dict, items: list, d: float) -> list:
    """Bir kampanyanın indirimini (d) KALEMLERE dağıtır — istifleme için kalem-başı kalan
    fiyat takibi. X-al-Y-öde (nth_discount): indirim, _compute_discount'ın seçtiği EN UCUZ
    birimlere yazılır (bedava ürün gerçekten o kalemdir). Diğer tipler: kapsamdaki kalemlere
    tutarlarıyla orantılı. Dönüş: items ile hizalı kalem-toplam indirim listesi (Σ = d)."""
    n = len(items)
    out = [0.0] * n
    if d <= 0 or n == 0:
        return out
    allowed_cats = {str(x) for x in (c.get("categories") or []) if x is not None}
    allowed_pids = {str(x) for x in (c.get("products") or []) if x is not None}
    excluded_pids = {str(x) for x in (c.get("excluded_products") or []) if x is not None}
    skip_disc = c.get("skip_discounted", True)
    ctype = c.get("type")

    def _ok(it):
        if skip_disc and it.get("_has_manual_sale"):
            return False
        if excluded_pids and str(it.get("product_id")) in excluded_pids:
            return False
        if ctype == "bundle":
            return str(it.get("product_id")) in allowed_pids
        if allowed_cats or allowed_pids:
            return _item_in_scope(it, allowed_cats, allowed_pids, excluded_pids)
        return True

    idx = [i for i, it in enumerate(items) if _ok(it)]
    if ctype == "nth_discount" and idx:
        bq = int(c.get("buy_quantity") or 2)
        fq = int(c.get("free_quantity") or 1)
        gd = float(c.get("get_discount") or 0)
        units = []
        for i in idx:
            for _ in range(int(items[i].get("qty", 0) or 0)):
                units.append((float(items[i].get("price", 0) or 0), i))
        units.sort(key=lambda u: u[0])
        n_disc = ((len(units) // bq) if bq else 0) * fq
        for k in range(min(n_disc, len(units))):
            out[units[k][1]] += units[k][0] * (gd / 100.0)
    else:
        base = sum(float(items[i].get("price", 0) or 0) * int(items[i].get("qty", 0) or 0) for i in idx)
        if base <= 0:
            idx = list(range(n))
            base = sum(float(it.get("price", 0) or 0) * int(it.get("qty", 0) or 0) for it in items)
        if base > 0:
            for i in idx:
                out[i] = d * float(items[i].get("price", 0) or 0) * int(items[i].get("qty", 0) or 0) / base
    # Σ = d (kuruş). X-al-Y-öde'de birim tutarları AYNEN yazılır (yeniden ölçekleme bedava
    # birimde 0,0003 TL gibi artık bırakıp onu sonraki kampanyada "canlı" tutuyordu).
    tot = sum(out)
    if ctype != "nth_discount" and tot > 0 and abs(tot - d) > 1e-9:
        f = d / tot
        out = [x * f for x in out]
    for i, it in enumerate(items):
        line = float(it.get("price", 0) or 0) * int(it.get("qty", 0) or 0)
        out[i] = max(0.0, min(out[i], line))
    return out


async def evaluate_cart_promotions(cart_total: float, items: list,
                                   user_id=None, email: str = "", entered_code: str = "",
                                   payment_method: str = "", excluded_ids=None) -> dict:
    """Otomatik kampanyalar + (varsa) girilen kodu birlikte degerlendirir. Saf orkestrasyon.
    excluded_ids: musterinin X ile kaldirdigi kampanya id'leri -> uygulanmaz (ama eligible'da
    yine gorunur ki geri eklenebilsin). Boylece motor 'en yuksegi zorla' DEGIL, musteri secer."""
    entered = fold_code(entered_code)
    # KOK SEBEP DUZELTMESI: items'i gercek urun kategori uyelikleriyle zenginlestir
    # ('En Yeniler' gibi ikincil kategoriye ozel kampanyalar bunsuz hic eslesmiyordu).
    items = await _enrich_items_category_ids(items)

    # 1) Adaylar: aktif auto_apply kampanyalar + girilen kod
    candidates = {}
    # OTO-UYGULAMA VARSAYILAN AÇIK: admin panelindeki "Otomatik uygula (kod gerekmez)"
    # onay kutusu işaretlendiğinde kampanya doğrudan uygulanır. Ayar dokümanında
    # promo_auto_apply_enabled alanı yoksa (varsayılan durum) açık kabul edilir;
    # acil durumda kapatmak istenirse settings.promo_auto_apply_enabled = False yazılabilir.
    _auto_on = True
    try:
        _s = await db.settings.find_one({"id": "main"},
                                        {"_id": 0, "promo_auto_apply_enabled": 1}) or {}
        if "promo_auto_apply_enabled" in _s:
            _auto_on = bool(_s.get("promo_auto_apply_enabled"))
    except Exception:
        _auto_on = True
    if _auto_on:
        async for c in db.coupons.find({"is_active": True, "auto_apply": True}, {"_id": 0}):
            candidates[c["id"]] = c
    entered_id = None
    entered_resolved = ""
    if entered:
        # BULANIK ÇÖZÜMLEME: müşterinin yazdığı her varyant (Türkçe harf, boşluk, %, tire,
        # O↔0, eksik/yanlış harf) sistemdeki gerçek kupona eşlenir (resolve_coupon_code).
        ec, entered_resolved = await resolve_coupon_code(entered_code)
        if ec:
            candidates[ec["id"]] = ec
            entered_id = ec["id"]
            entered_resolved = ec.get("code") or entered_resolved

    # 2) Tekil degerlendirme
    valid, rejected = [], []
    for cid, c in candidates.items():
        ev = await _evaluate_single(c, cart_total, items, user_id, email, payment_method)
        if ev.get("valid") and (ev.get("discount", 0) > 0 or ev.get("free_shipping")):
            valid.append({
                "c": c, "discount": ev["discount"], "free_shipping": ev.get("free_shipping", False),
                "priority": int(c.get("priority", 0) or 0),
                "combinable": bool(c.get("combinable", True)),
                "stack_group": c.get("stack_group") or "",
                "combinable_with": c.get("combinable_with") or [],
                "not_combinable_with": [str(x) for x in (c.get("not_combinable_with") or [])],
                "is_entered": cid == entered_id,
            })
        elif cid == entered_id:
            # Girilen kod GEÇERLİ ama bu sepette indirim 0 çıktıysa (kategori/ürün kısıtı dışı
            # sepet) eskiden belirsiz "Uygulanamadı" dönüyordu → müşteri "kod bozuk" sanıyordu.
            # CANLI DENETİM (kök neden — "HOSGELDIN10 çalışmıyor"): kupon GEÇERLİ ama sepetteki
            # ürünler ürün kartında İNDİRİMLİ (sale_price) olduğu için kampanya tabanından
            # dışlanıyor (skip_discounted) → indirim 0. Müşteriye NEDENİNİ söyle; aksi halde
            # "geçerli değil" mesajı kupon bozuk sanılıyor. Kupon indirimli ürünlerde de geçsin
            # isteniyorsa admin kampanya formundan "İndirimli ürünlere uygulansın" açılır.
            _all_sale = bool(items) and all(bool(it.get("_has_manual_sale")) for it in items)
            if ev.get("valid") and not ev.get("reason"):
                if c.get("skip_discounted", True) and _all_sale:
                    _why = ("Sepetinizdeki ürünler zaten indirimli olduğu için bu kupon uygulanamıyor "
                            "(kupon indirimsiz ürünlerde geçerlidir).")
                elif c.get("skip_discounted", True) and any(bool(it.get("_has_manual_sale")) for it in items):
                    _why = ("Bu kupon yalnızca sepetinizdeki indirimsiz ürünlere uygulanır; "
                            "indirimli ürünler kapsam dışıdır.")
                else:
                    _why = "Bu kupon sepetinizdeki ürünlerde geçerli değil"
            else:
                _why = ev.get("reason") or "Uygulanamadı"
            # code = çözülen KANONİK kod, entered = müşterinin yazdığı (katlanmış) — frontend
            # ikisiyle de eşleyebilsin (yazım hatalı girişte reddi yine göstersin).
            rejected.append({"code": entered_resolved or entered, "entered": entered, "reason": _why})

    # 3) SEÇİM önceliği: GİRİLEN KOD ÖNCE -> priority -> discount.
    # CANLI OLAY (havale/EFT siparişlerinde "kod geçersiz"): müşterinin yazdığı kod, daha yüksek
    # öncelikli/indirimli bir OTOMATİK kampanyayla (ödeme-yöntemi kampanyası, üye indirimi…)
    # birleşemeyince istiflemede SESSİZCE düşüyordu — ne applied'da ne rejected'da → müşteri
    # "geçersiz" görüyordu. Motorun ilkesi "en yükseği zorla" değil "müşteri seçer": yazılan kod
    # müşterinin açık tercihidir → önce o uygulanır, onunla birleşemeyen otomatik kampanya düşer.
    # (Birleşebilenler — ör. Lansman %20 + HOSGELDIN10 — yine birlikte uygulanır.)
    valid.sort(key=lambda x: (x["is_entered"], x["priority"], x["discount"]), reverse=True)

    # 4) Stack uygula (IKILI/pairwise combinable kapisi + stack_group + kalan tabana ardisik)
    def _pair_ok(a, b):
        # BİRLEŞME KURALI (kullanıcı isteği — "yeni kupon otomatik tüm kampanyalarla çalışsın,
        # engelleyeceklerimi kuponun içinden seçeyim"):
        #   1) İkisi de birleştirilebilir olmalı (combinable=False → münhasır).
        #   2) Taraflardan biri diğerini ENGEL listesinde (not_combinable_with) tutuyorsa → birleşmez.
        #   3) Aksi hâlde → birleşir. (Eski 'combinable_with' izin listesi migrasyonla engel
        #      listesine çevrildi; artık okunmaz.)
        if not (a["combinable"] and b["combinable"]):
            return False
        an = a.get("not_combinable_with") or []
        bn = b.get("not_combinable_with") or []
        if str(b["c"]["id"]) in an or str(a["c"]["id"]) in bn:
            return False
        return True

    selected = []
    used_groups = set()
    _excluded = set(excluded_ids or [])
    for cand in valid:
        if cand["c"]["id"] in _excluded:
            continue  # musteri bu kampanyayi X ile kaldirdi
        if selected:
            if not cand["combinable"]:
                continue
            if not all(_pair_ok(cand, a) for a in selected):
                continue
            if cand["stack_group"] and cand["stack_group"] in used_groups:
                continue
        selected.append(cand)
        if cand["stack_group"]:
            used_groups.add(cand["stack_group"])

    # Hesap sırası seçim önceliğinden BAĞIMSIZDIR. Girilen kupon birleşmeyen bir
    # otomatik kampanyaya yenilmez; ancak birleşen ürün kampanyası önce hesaplanır.
    # Firma/kod/yüzde sabiti yok: otomatik kampanya -> ilk sipariş/girilen kupon
    # -> yalnız kargo kampanyası. Her aşamada paneldeki priority korunur.
    # Eşit öncelikte "X al Y öde" (nth_discount) ÖNCE: bedava birim tam bedava kalır ve
    # sonuç sıra/eşitlik bozucuya bağlı olmaz (yüzde önce çalışınca "en ucuz" birim değişebiliyordu).
    selected.sort(key=lambda a: (
        2 if a["free_shipping"] and a["discount"] <= 0 else
        1 if a["is_entered"] or a["c"].get("first_order_only") or not a["c"].get("auto_apply") else 0,
        -a["priority"], 0 if a["c"].get("type") == "nth_discount" else 1,
        -a["discount"],
        # İki X-al-Y-öde eşit tutardaysa id'ye değil, daha cömert orana göre sırala
        # (5 al 3 öde, 3 al 2 öde'den önce) → sonuç kampanya kimliğine bağlı olmaz.
        -((float(a["c"].get("free_quantity") or 0) * float(a["c"].get("get_discount") or 0)
           / max(1.0, float(a["c"].get("buy_quantity") or 1))) if a["c"].get("type") == "nth_discount" else 0.0),
        str(a["c"]["id"]),
    ))
    applied = []
    running = cart_total
    # KALEM-BAŞI KALAN FİYAT: sonraki kampanya, önceki kampanyaların O KALEMDEN düştüğü
    # tutardan sonra kalan fiyat üzerinden hesaplanır. Eskiden tüm sepete tek bir oran
    # (kalan/sepet) uygulanıyordu → "3 AL 2 ÖDE" ile "Lansman %20" birlikteyken bedava ürün
    # tam bedava olmuyor (~60 TL ücretli kalıyordu) ya da %20, bedava ürünün payıyla
    # küçülüyordu. Sepet-geneli kampanyada sonuç eskisiyle aynıdır.
    # BİRİM düzeyinde takip: adetli kalem (4 × aynı ürün) birimlere açılır; X-al-Y-öde'nin
    # bedava yaptığı birim TAM 0'a iner, diğerleri dokunulmaz. Tamamen bedava olmuş birim
    # sonraki kampanyalarda sayılmaz (ikinci kez bedava/indirim almaz).
    _units = []   # [kalem_index, kalan_birim_fiyat]
    for i, it in enumerate(items):
        for _ in range(max(0, int(it.get("qty", 0) or 0))):
            _units.append([i, float(it.get("price", 0) or 0)])
    _base_total = sum(u[1] for u in _units)
    _scale0 = (cart_total / _base_total) if (_base_total > 0 and abs(_base_total - cart_total) > 0.009) else 1.0
    for cand in selected:
        _live = [k for k, u in enumerate(_units) if u[1] > 0.005]   # ≤ yarım kuruş = tükenmiş
        scaled_items = [{**items[_units[k][0]], "price": _units[k][1] * _scale0, "qty": 1} for k in _live]
        d = _compute_discount(cand["c"], running, scaled_items)
        fs = cand["free_shipping"]
        # 4000-TL KAPISI (muhasebe sızıntısı): bedava-kargo kampanyası, İNDİRİM SONRASI kalan
        # tutar (running) kampanyanın min_cart_total'ını KARŞILAMIYORSA uygulanmaz. KARGO0 gibi
        # kampanyalar eskiden min'i indirim ÖNCESİ sepete göre geçtiğinden, indirimlerle 4000
        # altına düşen sepetler bedava kargo alıyordu ("4000 altı ama kargo bedava" faturaları).
        # free_shipping kampanyaları hesaplanan indirimi 0 olduğundan sıralamada en sona düşer →
        # bu noktada `running` tüm indirimler düşülmüş NET tutardır; eşiği net tutara uygularız.
        if fs:
            _fs_min = float(cand["c"].get("min_cart_total") or 0)
            if _fs_min and round(running, 2) < _fs_min:
                continue
        if d <= 0 and not fs:
            continue
        applied.append({**cand, "applied_discount": round(d, 2)})
        running = round(running - d, 2)
        if d > 0:
            try:
                _al = _allocate_discount(cand["c"], scaled_items, d)
                for j, k in enumerate(_live):
                    if _scale0 > 0:
                        _units[k][1] = max(0.0, _units[k][1] - _al[j] / _scale0)
            except Exception:
                # Dağıtım yapılamazsa eski davranış: sepete orantılı küçült.
                _f = (running / (running + d)) if (running + d) > 0 else 0.0
                for u in _units:
                    u[1] *= _f

    # GÜVENLİK AĞI — ASLA SESSİZ DÜŞME: girilen kod geçerli (valid) olduğu hâlde istiflemede
    # uygulanamadıysa (müşteri X ile kaldırmadıysa) nedenini rejected'a yaz; frontend gösterir.
    if entered_id and entered_id not in _excluded \
            and any(v["c"]["id"] == entered_id for v in valid) \
            and not any(a["c"]["id"] == entered_id for a in applied) \
            and not any(str(r.get("code")) == str(entered_resolved or entered) for r in rejected):
        _blk = [a["c"].get("title") or a["c"].get("code") or "" for a in applied]
        rejected.append({"code": entered_resolved or entered, "entered": entered,
                         "reason": ("Bu kod şu kampanyayla birlikte kullanılamıyor: " + ", ".join(_blk)
                                    + " — o kampanyayı × ile kaldırırsanız kodunuz uygulanır.") if _blk
                         else "Bu kod bu sepette uygulanamadı."})

    total = round(sum(a["applied_discount"] for a in applied), 2)

    # 5) Global tavan
    cap_pct = await _promo_cap_pct()
    cap_amount = round(cart_total * cap_pct / 100.0, 2)
    capped = False
    if total > cap_amount and total > 0:
        factor = cap_amount / total
        for a in applied:
            a["applied_discount"] = round(a["applied_discount"] * factor, 2)
        total = round(sum(a["applied_discount"] for a in applied), 2)
        capped = True

    # ── OTOMATİK ÜYE / ÖĞRENCİ İNDİRİMLERİ ───────────────────────────────────────
    # İkisi de AYNI tabandan hesaplanır: (sepet − kampanya/kupon indirimi). Birbirlerini
    # küçültmezler (üye %5 + öğrenci %5 = tabanın %10'u). Müşteri bunları "×" ile kaldırmışsa
    # (excluded_ids) sunucu da uygulamaz — aksi halde ekrandaki tutar ile sunucunun tutarı
    # ayrışıp sipariş "tutar uyuşmazlığı" ile reddediliyordu.
    _excl_syn = {str(x) for x in (excluded_ids or [])}
    _base_extra = max(0.0, round(cart_total - total, 2))

    # ── ÜYE GRUBU İNDİRİMİ (users.group_id → member_groups.discount_percent) ──────
    # HATA DÜZELTMESİ: bu indirim kasada GÖSTERİLİYOR ama sipariş kaydında hesaplanmıyordu.
    # Sonuç: indirimli gruba bağlı üye, onay-tutarı denetimine takılıp (409) hiç sipariş
    # VEREMİYORDU. Artık motorun içinde — önizleme ile sipariş tek kaynaktan gelir.
    member_pct, member_amount, member_group = 0.0, 0.0, ""
    try:
        if user_id and "member_discount" not in _excl_syn:
            _u = await db.users.find_one({"id": str(user_id)}, {"_id": 0, "group_id": 1}) or {}
            _gid = _u.get("group_id")
            if _gid:
                _grp = await db.member_groups.find_one(
                    {"id": _gid}, {"_id": 0, "name": 1, "discount_percent": 1}) or {}
                member_pct = max(0.0, min(float(_grp.get("discount_percent") or 0), 90.0))
                member_group = str(_grp.get("name") or "")
                if member_pct > 0:
                    # Emniyet: iki otomatik indirim TOPLAMI tabanı geçemez (negatif tutar olmaz).
                    member_amount = min(round(_base_extra * member_pct / 100.0, 2), _base_extra)
    except Exception as _me:
        logger.warning(f"[uye-indirimi] hesaplanamadi: {_me}")
        member_pct, member_amount, member_group = 0.0, 0.0, ""
    if member_amount > 0:
        applied.append({
            "c": {"id": "member_discount", "code": "",
                  "title": f"Üye İndirimi{(' (' + member_group + ')') if member_group else ''} (%{member_pct:g})",
                  "type": "percent"},
            "applied_discount": member_amount, "free_shipping": False,
            "priority": 998, "combinable": True, "stack_group": "member", "combinable_with": [],
        })
        total = round(total + member_amount, 2)

    # ── ÖĞRENCİ İNDİRİMİ (.edu.tr) ────────────────────────────────────────────────
    # Kullanıcı isteği: .edu.tr uzantılı e-postalara TÜM siparişlerde ek yüzde indirim.
    # Motorun İÇİNDE hesaplanır → kasa önizlemesi ile sipariş toplamı TEK KAYNAKTAN gelir
    # (onaylanan = çekilen). Kampanya tavanından SONRA, ayrı bir kalem olarak eklenir;
    # kupon/kampanya ile birlikte uygulanabilir. Ayar: settings.main.edu_discount_pct
    # (0 veya boş → kapalı). E-posta sunucuya gelen sipariş/kasa e-postasıdır.
    edu_pct, edu_amount = 0.0, 0.0
    try:
        # Denetim #19: öğrenci indirimi kasaya yazılan (doğrulanmamış) e-postaya DEĞİL, giriş yapmış
        # hesabın DOĞRULANMIŞ .edu.tr e-postasına uygulanır.
        _acct_edu = await _account_identity(user_id)
        _em = _acct_edu.get("email") if _acct_edu.get("email_verified") else ""
        if _em and _em.endswith(".edu.tr") and "edu_discount" not in _excl_syn:
            _es = await db.settings.find_one({"id": "main"},
                                             {"_id": 0, "edu_discount_pct": 1}) or {}
            _raw = _es.get("edu_discount_pct")
            edu_pct = 5.0 if _raw in (None, "") else float(_raw or 0)
            edu_pct = max(0.0, min(edu_pct, 90.0))
            if edu_pct > 0:
                # Kampanya indirimi düşüldükten SONRAKİ tutar üzerinden (üye indirimiyle aynı taban)
                edu_amount = min(round(_base_extra * edu_pct / 100.0, 2),
                                 max(0.0, round(_base_extra - member_amount, 2)))
    except Exception as _ee:
        logger.warning(f"[edu-indirim] hesaplanamadi: {_ee}")
        edu_pct, edu_amount = 0.0, 0.0
    if edu_amount > 0:
        applied.append({
            "c": {"id": "edu_discount", "code": "", "title": f"Öğrenci İndirimi (%{edu_pct:g})",
                  "type": "percent"},
            "applied_discount": edu_amount, "free_shipping": False,
            "priority": 999, "combinable": True, "stack_group": "edu", "combinable_with": [],
        })
        total = round(total + edu_amount, 2)

    # TEŞHİS GÜNLÜĞÜ: müşteri kod yazdı ama uygulanmadı → kaydet (admin /admin/coupons/attempts)
    if entered:
        await _log_coupon_attempt(entered_code, entered_resolved, entered_id, rejected, applied,
                                  email, user_id, cart_total, items, payment_method)

    return {
        "applied": [{
            "coupon_id": a["c"]["id"], "code": a["c"].get("code", ""),
            "title": a["c"].get("title", ""), "type": a["c"].get("type"),
            "discount": a["applied_discount"], "free_shipping": a["free_shipping"],
            "priority": a["priority"], "combinable": a["combinable"], "stack_group": a["stack_group"],
            "combinable_with": a.get("combinable_with") or [],
        } for a in applied],
        "total_discount": total,
        # Öğrenci / üye indirimi ayrıca bildirilir (kasa ekranı ayrı satır gösterebilsin).
        "edu_discount": edu_amount,
        "edu_discount_pct": edu_pct if edu_amount > 0 else 0,
        "member_discount": member_amount,
        "member_discount_pct": member_pct if member_amount > 0 else 0,
        "member_group_name": member_group if member_amount > 0 else "",
        "free_shipping": any(a["free_shipping"] for a in applied),
        "capped": capped,
        "cap_pct": cap_pct,
        "rejected": rejected,
        # Girilen kodun çözüldüğü KANONİK kupon kodu (bulanık eşleşmede yazılandan farklı olabilir).
        # Frontend applied/rejected eşlemesini ve siparişe yazılacak kodu buna göre yapar.
        "entered_resolved": entered_resolved,
        # eligible: bu sepette GECERLI tum kampanyalar (uygulanmis olsun olmasin) — musteri
        # X ile kaldirip baskasini secebilsin diye. discount = tek basina (standalone) deger.
        "eligible": [{
            "coupon_id": v["c"]["id"], "code": v["c"].get("code", ""),
            "title": v["c"].get("title", ""), "type": v["c"].get("type"),
            "discount": round(v["discount"], 2), "free_shipping": v["free_shipping"],
            "combinable": v["combinable"], "combinable_with": v.get("combinable_with") or [],
        } for v in valid],
    }


@public_router.post("/evaluate")
@(limiter.limit("60/minute") if limiter else (lambda f: f))
async def evaluate_promotions_endpoint(payload: dict, request: Request,
                                      current_user: dict = Depends(get_current_user)):
    """Checkout/sepet motoru ucu. Onizleme ve siparis-kaydi AYNI motoru cagirir."""
    # user_id İSTEMCİDEN alınmaz (başka üyenin kişiye özel kampanya/kupon durumu yoklanmasın) — JWT'den.
    return await evaluate_cart_promotions(
        cart_total=float(payload.get("cart_total") or 0),
        items=payload.get("items") or [],
        user_id=(current_user or {}).get("id"),
        email=payload.get("email") or payload.get("customer_email") or "",
        entered_code=payload.get("code") or "",
        payment_method=payload.get("payment_method") or "",
        excluded_ids=payload.get("excluded_ids") or [],
    )


async def supersede_prior_unpaid_coupon_orders(order: dict) -> int:
    """Denetim #21: kişi-başı sınırlı kupon/kampanyayla yeni (ödemesi bekleyen) sipariş açılınca, AYNI
    kişinin aynı kuponu taşıyan DAHA ÖNCEKİ ödenmemiş ve ödemesi şu an sürmeyen siparişleri
    "geçersiz kılındı" işaretlenir → ödeme başlatılamaz (payment.py). Böylece müşteri yarıda kalan
    denemeyi yenileyebilir ama aynı hakla iki sipariş ÖDEYEMEZ. Siparişin statüsüne/stoğuna
    DOKUNMAZ: ödenmeyen sipariş 3 saatlik auto_cancel_unpaid_card_orders ile iptal + stok iadesi
    alır (değişmez 4)."""
    cids = [str(a.get("coupon_id")) for a in (order.get("applied_promotions") or [])
            if a.get("coupon_id") and float(a.get("discount") or 0) > 0]
    if not cids:
        return 0
    limited = [c["id"] async for c in db.coupons.find(
        {"id": {"$in": cids}, "usage_limit_per_user": {"$gt": 0}}, {"_id": 0, "id": 1})]
    if not limited:
        return 0
    ors = []
    if order.get("user_id"):
        ors.append({"user_id": order["user_id"]})
    ors.extend(_email_match_clauses((order.get("shipping_address") or {}).get("email") or order.get("email") or ""))
    if not ors:
        return 0
    now_ms = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
    res = await db.orders.update_many(
        {"$and": [
            {"id": {"$ne": order.get("id")}},
            {"created_at": {"$lte": order.get("created_at") or now_ms}},
            {"applied_promotions.coupon_id": {"$in": limited}},
            {"payment_status": {"$nin": ["paid", "refunded", "partially_refunded"]}},
            {"status": {"$in": ["awaiting_payment", "pending", "payment_failed", "failed"]}},
            {"coupon_superseded_by": {"$exists": False}},
            {"$or": [{"payment_init_lock_until": None}, {"payment_init_lock_until": {"$lte": now_ms}}]},
            {"$or": ors},
        ]},
        {"$set": {"coupon_superseded_by": order.get("id"), "coupon_superseded_at": now_ms}})
    return int(getattr(res, "modified_count", 0) or 0)
