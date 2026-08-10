"""
referrals.py — Referans (arkadaşını getir) + ödül kuponu altyapısı (Bölüm C).

Mevcut KUPON motorunun üstüne kurulur: ödüller, admin'in oluşturduğuyla AYNI şekle sahip
tek-kullanımlık kupon dokümanları olarak yazılır → checkout'taki kupon değerlendirme yolu
(kritik akış) HİÇ değişmez, sadece yeni kupon kayıtları eklenir. E-postalar beyaz-etiket
markasıyla (company.py) gider. Tüm eşik/değerler business_rules'tan (admin-düzenlenebilir).

Ödül tetikleme ödeme akışına DOKUNMAZ: scheduler (tek-lider guard'lı) periyodik olarak
"davet edilenin ilk ödenmiş siparişi var mı" kontrol eder → varsa iki tarafı ödüllendirir
(idempotent). Böylece checkout'a sıfır risk.
"""
import secrets
import string
import uuid
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, HTTPException, Request, Depends

from .deps import db, logger, require_auth

public_router = APIRouter(prefix="/referrals", tags=["referrals"])
admin_router = APIRouter(prefix="/admin/referrals", tags=["admin-referrals"])

_PAID_STATUSES = {"paid", "confirmed", "processing", "preparing", "shipped", "delivered"}
_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # okunması kolay (0/O, 1/I yok)


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _rand(n: int) -> str:
    return "".join(secrets.choice(_ALPHABET) for _ in range(n))


async def _get_rule(key: str, fallback=None):
    try:
        from business_rules import get_rule as _gr
        return await _gr(db, key, fallback)
    except Exception:
        return fallback


async def _unique_coupon_code(prefix: str) -> str:
    for _ in range(12):
        code = f"{prefix}-{_rand(8)}"
        if not await db.coupons.find_one({"code": code}, {"_id": 1}):
            return code
    return f"{prefix}-{_rand(12)}"  # aşırı nadir çakışma — daha uzun


async def issue_reward_coupon(*, email: str, reward_type: str, value: float,
                              title: str, prefix: str, source: str,
                              user_id: str = None, min_cart_total: float = 0.0) -> dict:
    """Tek-kullanımlık ödül kuponu oluşturur (admin kuponuyla aynı şema). Kupon kodunu döndürür.
    Kod alıcının e-postasına gönderilir; usage_limit=1 olduğundan pratikte yalnız alıcı kullanır."""
    value = float(value or 0)
    if value <= 0:
        return {}
    reward_type = "percent" if str(reward_type).lower() == "percent" else "fixed"
    days = int(await _get_rule("reward.coupon_valid_days", 90) or 90)
    now = datetime.now(timezone.utc)
    code = await _unique_coupon_code(prefix)
    doc = {
        "id": str(uuid.uuid4()),
        "code": code,
        "title": title,
        "type": reward_type,                     # percent | fixed  (motorun beklediği ad)
        "value": value,
        "min_cart_total": float(min_cart_total or 0),
        "max_discount": None,
        "categories": [], "products": [],
        "usage_limit": 1,                        # global tek kullanım
        "usage_limit_per_user": 1,
        "start_at": now.isoformat(),
        "end_at": (now + timedelta(days=days)).isoformat(),
        "is_active": True,
        "first_order_only": False,
        "free_shipping": False,
        "auto_apply": False,
        "min_quantity": None, "buy_quantity": None, "free_quantity": 1, "get_discount": 0,
        "priority": 0, "combinable": False, "stack_group": None, "combinable_with": [],
        "payment_methods": [],
        # ödül izleri (admin filtreleme + audit)
        "reward_source": source,                 # referral_referrer | referral_referee | birthday | welcome
        "reward_email": (email or "").strip().lower(),
        "reward_user_id": user_id,
        "created_at": now.isoformat(),
        "created_by": f"system:{source}",
    }
    await db.coupons.insert_one(doc)
    return {"code": code, "type": reward_type, "value": value,
            "end_at": doc["end_at"]}


async def _send_reward_email(email: str, subject: str, heading: str, body_html: str, code: str, end_at: str):
    """Beyaz-etiket markalı ödül e-postası (kupon kodu vurgulu)."""
    try:
        _end = ""
        try:
            _end = datetime.fromisoformat(end_at).strftime("%d.%m.%Y")
        except Exception:
            _end = ""
        inner = (
            f'<div style="text-align:center">'
            f'<p style="font-size:15px;line-height:1.7;color:#555">{body_html}</p>'
            f'<div style="margin:22px auto;padding:16px 22px;border:1.5px dashed #1a1a1a;display:inline-block">'
            f'<div style="font-size:11px;letter-spacing:2px;color:#999;text-transform:uppercase">Kupon Kodu</div>'
            f'<div style="font-size:26px;font-weight:700;letter-spacing:4px;margin-top:4px">{code}</div></div>'
            + (f'<p style="font-size:12px;color:#999">Son kullanım: {_end}</p>' if _end else "")
            + '</div>'
        )
        from email_smtp import send_smtp_email
        from email_layout import email_shell
        html = email_shell(title=heading, intro_html=inner, preheader=subject)
        await send_smtp_email(db, email, subject, html, wrap=False)
    except Exception as e:
        logger.warning(f"[referral] ödül maili gönderilemedi ({email[-6:] if email else ''}): {e}")


# ============================ REFERANS KODU / KAYIT ============================

async def get_or_create_referral_code(user: dict) -> str:
    """Kullanıcının referans kodunu döndürür; yoksa üretir ve user'a yazar."""
    if user.get("referral_code"):
        return user["referral_code"]
    for _ in range(12):
        code = _rand(7)
        if not await db.users.find_one({"referral_code": code}, {"_id": 1}):
            await db.users.update_one({"id": user["id"]}, {"$set": {"referral_code": code}})
            return code
    code = _rand(11)
    await db.users.update_one({"id": user["id"]}, {"$set": {"referral_code": code}})
    return code


async def record_referral_on_register(referee_user: dict, referral_code: str) -> None:
    """Kayıt sırasında geçerli bir referans kodu girildiyse bekleyen referans kaydı oluşturur.
    Güvenlik: kendini davet edemez; her davet edilen yalnız bir kez kaydedilir."""
    referral_code = (referral_code or "").strip().upper()
    if not referral_code:
        return
    if not await _get_rule("referral.enabled", True):
        return
    referrer = await db.users.find_one({"referral_code": referral_code},
                                       {"_id": 0, "id": 1, "email": 1, "first_name": 1})
    if not referrer or referrer["id"] == referee_user["id"]:
        return
    # Bu davet edilen için zaten kayıt var mı? (tekilleştirme)
    if await db.referrals.find_one({"referee_user_id": referee_user["id"]}, {"_id": 1}):
        return
    await db.referrals.insert_one({
        "id": str(uuid.uuid4()),
        "referrer_user_id": referrer["id"],
        "referrer_email": (referrer.get("email") or "").lower(),
        "referee_user_id": referee_user["id"],
        "referee_email": (referee_user.get("email") or "").lower(),
        "code": referral_code,
        "status": "pending",              # pending | rewarded
        "created_at": _utcnow(),
    })
    logger.info(f"[referral] kayıt: referrer={referrer['id']} referee={referee_user['id']}")


# ============================ ÖDÜLLENDİRME (scheduler) ============================

async def award_pending_referrals(db_=None) -> int:
    """Davet edilenin ilk ödenmiş siparişi oluştuysa iki tarafı ödüllendirir (idempotent).
    Scheduler'dan çağrılır. Döner: ödüllendirilen referans sayısı."""
    _db = db_ or db
    if not await _get_rule("referral.enabled", True):
        return 0
    r_type = await _get_rule("referral.reward_type", "fixed")
    r_val = float(await _get_rule("referral.referrer_reward_value", 100) or 0)
    e_val = float(await _get_rule("referral.referee_reward_value", 100) or 0)
    min_total = float(await _get_rule("referral.min_order_total", 0) or 0)
    awarded = 0
    async for ref in _db.referrals.find({"status": "pending"}, {"_id": 0}):
        # Davet edilenin ödenmiş (asgari tutarı geçen) ilk siparişi var mı?
        q = {"user_id": ref["referee_user_id"], "status": {"$in": list(_PAID_STATUSES)}}
        order = await _db.orders.find_one(q, {"_id": 0, "total": 1})
        if not order:
            # e-posta ile de dene (bazı siparişler user_id taşımayabilir)
            order = await _db.orders.find_one(
                {"$or": [{"shipping_address.email": ref["referee_email"]},
                         {"email": ref["referee_email"]}],
                 "status": {"$in": list(_PAID_STATUSES)}}, {"_id": 0, "total": 1})
        if not order:
            continue
        if min_total > 0 and float(order.get("total") or 0) < min_total:
            continue
        # Atomik guard: yalnız hâlâ pending ise ödüllendir (çift ödül önlenir).
        claim = await _db.referrals.update_one(
            {"id": ref["id"], "status": "pending"},
            {"$set": {"status": "rewarded", "rewarded_at": _utcnow()}})
        if claim.modified_count == 0:
            continue
        try:
            rc = await issue_reward_coupon(
                email=ref["referrer_email"], reward_type=r_type, value=r_val,
                title="Referans ödülü — arkadaşın alışveriş yaptı!", prefix="REF",
                source="referral_referrer", user_id=ref["referrer_user_id"])
            ec = await issue_reward_coupon(
                email=ref["referee_email"], reward_type=r_type, value=e_val,
                title="Hoş geldin — ilk alışverişine özel", prefix="REF",
                source="referral_referee", user_id=ref["referee_user_id"])
            await _db.referrals.update_one({"id": ref["id"]}, {"$set": {
                "referrer_coupon": rc.get("code"), "referee_coupon": ec.get("code")}})
            if rc.get("code"):
                _v = f"%{int(r_val)}" if r_type == "percent" else f"₺{int(r_val)}"
                await _send_reward_email(
                    ref["referrer_email"], "Referans ödülün hazır 🎁",
                    "Teşekkürler! Ödülün hazır",
                    f"Davet ettiğin arkadaşın ilk alışverişini yaptı. Sana özel <b>{_v} indirim</b> kuponun:",
                    rc["code"], rc.get("end_at", ""))
            if ec.get("code"):
                _v = f"%{int(e_val)}" if r_type == "percent" else f"₺{int(e_val)}"
                await _send_reward_email(
                    ref["referee_email"], "Sana özel indirim 🎁",
                    "Aramıza hoş geldin!",
                    f"Bir sonraki alışverişinde kullanman için <b>{_v} indirim</b> kuponun:",
                    ec["code"], ec.get("end_at", ""))
            awarded += 1
        except Exception as e:
            logger.warning(f"[referral] ödül verilemedi ref={ref['id']}: {e}")
    if awarded:
        logger.info(f"[referral] {awarded} referans ödüllendirildi")
    return awarded


# ============================ DOĞUM GÜNÜ (scheduler) ============================

async def send_birthday_coupons(db_=None) -> int:
    """Bugün doğum günü olan (birth_date kayıtlı) müşterilere doğum günü kuponu gönderir.
    Yıl-başına idempotent (users.birthday_reward_year). Döner: gönderilen sayısı."""
    _db = db_ or db
    if not await _get_rule("birthday.enabled", True):
        return 0
    now = datetime.now(timezone.utc)
    mmdd = now.strftime("%m-%d")
    year = now.year
    b_type = await _get_rule("birthday.reward_type", "fixed")
    b_val = float(await _get_rule("birthday.reward_value", 100) or 0)
    if b_val <= 0:
        return 0
    sent = 0
    async for u in _db.users.find(
            {"birth_date": {"$nin": [None, ""]}, "is_active": {"$ne": False}},
            {"_id": 0, "id": 1, "email": 1, "birth_date": 1, "birthday_reward_year": 1, "first_name": 1}):
        bd = str(u.get("birth_date") or "")
        # birth_date "YYYY-MM-DD" veya "MM-DD" → son 5 hane ay-gün
        if len(bd) < 5 or bd[-5:] != mmdd:
            continue
        if u.get("birthday_reward_year") == year:
            continue  # bu yıl zaten gönderildi
        claim = await _db.users.update_one(
            {"id": u["id"], "birthday_reward_year": {"$ne": year}},
            {"$set": {"birthday_reward_year": year}})
        if claim.modified_count == 0:
            continue
        try:
            c = await issue_reward_coupon(
                email=u.get("email", ""), reward_type=b_type, value=b_val,
                title="Doğum günün kutlu olsun 🎂", prefix="DG",
                source="birthday", user_id=u["id"])
            if c.get("code"):
                _v = f"%{int(b_val)}" if b_type == "percent" else f"₺{int(b_val)}"
                await _send_reward_email(
                    u.get("email", ""), "Doğum günün kutlu olsun! 🎂",
                    "Nice mutlu yıllara!",
                    f"Doğum gününü kutlarız! Sana özel <b>{_v} indirim</b> hediyemiz:",
                    c["code"], c.get("end_at", ""))
                sent += 1
        except Exception as e:
            logger.warning(f"[birthday] kupon gönderilemedi user={u['id']}: {e}")
    if sent:
        logger.info(f"[birthday] {sent} doğum günü kuponu gönderildi")
    return sent


async def issue_welcome_coupon(user: dict) -> None:
    """Kayıt sonrası hoş geldin kuponu (etkinse). E-posta ile gönderilir."""
    try:
        if not await _get_rule("welcome.enabled", False):
            return
        w_type = await _get_rule("welcome.reward_type", "percent")
        w_val = float(await _get_rule("welcome.reward_value", 10) or 0)
        if w_val <= 0:
            return
        c = await issue_reward_coupon(
            email=user.get("email", ""), reward_type=w_type, value=w_val,
            title="Hoş geldin kuponu", prefix="HG", source="welcome", user_id=user["id"])
        if c.get("code"):
            _v = f"%{int(w_val)}" if w_type == "percent" else f"₺{int(w_val)}"
            await _send_reward_email(
                user.get("email", ""), "Aramıza hoş geldin 🎁",
                "Hoş geldin!",
                f"Üyeliğin için teşekkürler! İlk alışverişinde kullanman için <b>{_v} indirim</b> kuponun:",
                c["code"], c.get("end_at", ""))
    except Exception as e:
        logger.warning(f"[welcome] kupon verilemedi: {e}")


# ============================ MÜŞTERİ UÇLARI ============================

@public_router.get("/my")
async def my_referral(current_user: dict = Depends(require_auth)):
    """Kullanıcının referans kodu + paylaşım linki + istatistikleri."""
    user = await db.users.find_one({"id": current_user["id"]}, {"_id": 0})
    if not user:
        raise HTTPException(status_code=404, detail="Kullanıcı bulunamadı")
    code = await get_or_create_referral_code(user)
    total = await db.referrals.count_documents({"referrer_user_id": user["id"]})
    rewarded = await db.referrals.count_documents({"referrer_user_id": user["id"], "status": "rewarded"})
    enabled = bool(await _get_rule("referral.enabled", True))
    r_type = await _get_rule("referral.reward_type", "fixed")
    e_val = float(await _get_rule("referral.referee_reward_value", 100) or 0)
    try:
        from company import get_site_url
        base = await get_site_url(db)
    except Exception:
        base = "https://facette.com.tr"
    return {
        "enabled": enabled,
        "code": code,
        "share_url": f"{base}/kayit?ref={code}",
        "invited": total,
        "rewarded": rewarded,
        "referee_reward": {"type": r_type, "value": e_val},
    }


@admin_router.get("")
async def admin_referral_stats(current_user: dict = Depends(require_auth)):
    """Admin: referans programı özet + son kayıtlar (require_auth + admin kontrolü)."""
    if not current_user.get("is_admin"):
        raise HTTPException(status_code=403, detail="Yetki yok")
    total = await db.referrals.count_documents({})
    rewarded = await db.referrals.count_documents({"status": "rewarded"})
    pending = await db.referrals.count_documents({"status": "pending"})
    rows = await db.referrals.find({}, {"_id": 0}).sort("created_at", -1).to_list(200)
    return {"total": total, "rewarded": rewarded, "pending": pending, "items": rows}
