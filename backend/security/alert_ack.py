"""
SOC-01 — güvenlik uyarısı İNSAN TEYİDİ, yanıtsız kritik uyarının YÜKSELTİLMESİ ve alarm TATBİKATI.

Hedef (blueprint SOC-01): ≤ 5 dk alarm, ≤ 15 dk insan teyidi — ölçülebilir ve tekrarlanabilir.

1) ONAY (ack)
   • security.alerts.send_alert her UYARI/KRİTİK uyarıyı (kısılmamışsa) `ack_required=True` ile kaydeder;
     e-postanın sonuna alıcıya özel "Gördüm / İnceliyorum" bağlantısı eklenir.
   • Bağlantı: /api/security/alert-ack?t=<uyarı_id>.<alıcı_no>.<son_geçerlilik>.<imza>
       imza = HMAC-SHA256(anahtar, "secack:v1:<id>:<alıcı>:<exp>:<nonce>")[:40]
       anahtar = ALERT_ACK_SECRET (≥32 karakter) varsa o; yoksa JWT_SECRET'tan ALAN-AYRIMLI türetilir
       (fatura linkleriyle aynı desen; JWT anahtarı doğrudan imza anahtarı olarak KULLANILMAZ).
       nonce yalnız sunucuda (alerts.ack_nonce) durur → id bilinse de bağlantı üretilemez.
   • GET yalnız ONAY SAYFASI gösterir (hiçbir şey kaydetmez — e-posta güvenlik tarayıcıları/ön-izleyiciler
     bağlantıyı açtığında sahte "onay" oluşmaz). Kayıt sayfadaki butonun POST'u ile yapılır.
   • Tek kullanımlık: uyarı onaylanınca (bağlantı ya da panel) bağlantı 409 döner. Süresi dolmuş: 410.
     İmza/biçim bozuk ya da uyarı yok: 403/400. Sayfa uyarının içeriğini GÖSTERMEZ.
   • Panel: Güvenlik Paneli → "Onay bekleyen uyarılar" → Onayla (yalnız süper admin, FAIL-CLOSED denetim).
   • Kaydedilen: acked_at, acked_by (e-posta ya da "e-posta bağlantısı"), ack_via, ack_seconds
     (= onay süresi, uyarının oluşturulmasından itibaren).

2) YÜKSELTME (escalation) — zamanlayıcı işi `security_alert_escalation` (5 dk'da bir, YENİ iş)
   • Kritik uyarı İşletme Kuralı `security.ack_escalation_minutes` (varsayılan 15) dakika içinde onaylanmazsa
     BİR KEZ (atomik `escalated_at` talebi) tüm alıcılara "[YANIT YOK]" önekiyle yeniden gönderilir.
   • SMS: kural `security.alert_sms_phones` (boşsa aktif süper adminlerin profil telefonu) numaralarına kısa
     SMS — yalnız bir SMS sağlayıcısı etkinse. TÜM SMS'lerin geçtiği küresel bütçe kapısından
     (notification_service._sms_guard) "normal" öncelikle geçer → yönetici MFA yedek payını KULLANMAZ;
     ayrıca günde en çok ESCALATION_SMS_DAILY_MAX yükseltme SMS'i.

3) TATBİKAT — `security_drills` koleksiyonu
   • Süper admin "Tatbikat başlat" ya da aylık iş (her ayın ilk Pazartesi 10:07 Europe/Istanbul;
     kural `security.monthly_drill_enabled`) GERÇEK hattan "[TATBİKAT]" etiketli KRİTİK bir uyarı gönderir.
   • Onay ≤ DRILL_PASS_MINUTES (15) dk → "geçti"; onaylanmazsa → "kaldı" (+ normal yükseltme akışı).
     Geç gelen onay da ölçülür (late_ack) ama sonuç "kaldı" kalır.
   • Öz-test kontrolü "Alarm tatbikatı": son 35 günde başarılı tatbikat yoksa UYARI.
"""
from __future__ import annotations

import hashlib
import hmac
import html as _html
import logging
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse

from routes.deps import db, generate_id, limiter, require_super_admin

logger = logging.getLogger(__name__)

ACK_LEVELS = ("warning", "critical")
ACK_LINK_TTL_HOURS = 72
ACK_PATH = "/api/security/alert-ack"
ESCALATION_DEFAULT_MINUTES = 15
ESCALATION_MIN_MINUTES, ESCALATION_MAX_MINUTES = 5, 1440
ESCALATION_LOOKBACK_HOURS = 24      # daha eski onaysız uyarılar (ör. ilk deploy öncesi) yükseltilmez
ESCALATION_BATCH = 20
ESCALATION_SMS_DAILY_MAX = 10
ESCALATION_SMS_MAX_PHONES = 3
DRILL_PASS_MINUTES = 15             # SOC-01 hedefi: ≤ 15 dk insan teyidi
DRILL_SUCCESS_MAX_AGE_DAYS = 35
DRILL_KIND = "security_drill"
DRILL_TITLE = "[TATBİKAT] Güvenlik alarm tatbikatı — lütfen 'Gördüm' bağlantısına tıklayın"
DRILL_TZ = "Europe/Istanbul"
_MONTHLY_DOC_ID = "security_drill_monthly"

S_PENDING, S_PASSED, S_FAILED = "bekliyor", "geçti", "kaldı"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso(v) -> Optional[datetime]:
    try:
        dt = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _mask_email(e: str) -> str:
    e = str(e or "")
    if "@" not in e:
        return "***"
    u, d = e.split("@", 1)
    return (u[:1] + "***@" + d) if u else "***@" + d


async def _rule(key: str, default):
    try:
        from business_rules import get_rule
        return await get_rule(db, key, default)
    except Exception:
        return default


def _truthy(v) -> bool:
    if isinstance(v, str):
        return v.strip().lower() not in ("", "0", "false", "off", "no", "hayır", "kapalı")
    return bool(v)


async def escalation_minutes() -> int:
    try:
        v = int(float(await _rule("security.ack_escalation_minutes", ESCALATION_DEFAULT_MINUTES)))
    except Exception:
        v = ESCALATION_DEFAULT_MINUTES
    return max(ESCALATION_MIN_MINUTES, min(ESCALATION_MAX_MINUTES, v))


# ─────────────────────────────── imzalı onay bağlantısı ───────────────────────────────

def _derived_ack_key() -> bytes:
    from routes.deps import JWT_SECRET as _sec
    return hmac.new(str(_sec).encode(), b"meluni:security-alert-ack:v1", hashlib.sha256).digest()


def _ack_key() -> bytes:
    env = (os.environ.get("ALERT_ACK_SECRET") or "").strip()
    if len(env) >= 32:
        return env.encode()
    return _derived_ack_key()


def _ack_verify_keys() -> list:
    """Güncel anahtar + (ALERT_ACK_SECRET sonradan eklendiyse) türetilmiş eski anahtar."""
    keys = [_ack_key()]
    try:
        d = _derived_ack_key()
        if d not in keys:
            keys.append(d)
    except Exception:
        pass
    return keys


def _sig(key: bytes, aid: str, r: int, exp: int, nonce: str) -> str:
    return hmac.new(key, f"secack:v1:{aid}:{int(r)}:{int(exp)}:{nonce}".encode(), hashlib.sha256).hexdigest()[:40]


def make_ack_token(aid: str, r: int, nonce: str, exp: Optional[int] = None) -> str:
    if exp is None:
        exp = int(_now().timestamp()) + ACK_LINK_TTL_HOURS * 3600
    return f"{aid}.{int(r)}.{int(exp)}.{_sig(_ack_key(), aid, r, exp, nonce)}"


def _parse_token(t: str):
    parts = str(t or "").strip().split(".")
    if len(parts) != 4:
        return None
    aid, r, exp, sig = parts
    if not aid or len(aid) > 64 or not r.isdigit() or not exp.isdigit() or len(sig) != 40:
        return None
    return aid, int(r), int(exp), sig


def new_ack_fields(recipients: list) -> dict:
    """send_alert'in uyarı belgesine eklediği onay alanları."""
    return {"ack_required": True, "ack_nonce": secrets.token_hex(16),
            "recipients": [str(x).strip().lower() for x in (recipients or []) if x][:25]}


async def _api_base() -> str:
    """Backend'in herkese açık adresi (beyaz etiket: firma ayarı domains.api_url → PUBLIC_API_URL → BACKEND_URL)."""
    u = ""
    try:
        from tenant_config import get_tenant_config
        cfg = await get_tenant_config(db)
        u = (cfg.get("domains") or {}).get("api_url") or ""
    except Exception:
        u = ""
    u = (u or os.environ.get("PUBLIC_API_URL") or os.environ.get("BACKEND_URL") or "").strip().rstrip("/")
    if u.endswith("/api"):
        u = u[:-4]
    return u


async def ack_instructions(aid: str, r: int, nonce: str, level: str, *, escalation: bool = False) -> str:
    """E-posta sonuna eklenen insan teyidi bloğu (alıcıya özel imzalı bağlantı)."""
    lines = ["", "─" * 44, "İNSAN TEYİDİ"]
    base = await _api_base()
    if base and nonce:
        lines.append(f"Gördüm / İnceliyorum (tek tıkla onay): {base}{ACK_PATH}?t={make_ack_token(aid, r, nonce)}")
    lines.append("Ya da: Yönetim Paneli → Güvenlik Paneli → Onay bekleyen uyarılar → Onayla")
    if level == "critical" and not escalation:
        lines.append(f"Bu kritik uyarı {await escalation_minutes()} dakika içinde onaylanmazsa tüm alıcılara "
                     "'YANIT YOK' olarak yeniden gönderilir.")
    lines.append(f"Uyarı no: {aid}")
    return "\n".join(lines) + "\n"


async def _check_token(t: str):
    """(durum_kodu, mesaj, uyarı, alıcı_no). 200 = onaylanabilir."""
    p = _parse_token(t)
    if not p:
        return 400, "Bağlantı geçersiz.", None, 0
    aid, r, exp, sig = p
    alert = await db.alerts.find_one({"id": aid, "ack_required": True}, {"_id": 0})
    nonce = str((alert or {}).get("ack_nonce") or "")
    if not alert or not nonce or not any(hmac.compare_digest(sig, _sig(k, aid, r, exp, nonce))
                                         for k in _ack_verify_keys()):
        return 403, "Bağlantı geçersiz.", None, 0
    if exp < int(_now().timestamp()):
        return 410, "Bağlantının süresi dolmuş. Uyarıyı Güvenlik Paneli'nden onaylayabilirsiniz.", None, 0
    if alert.get("acked_at"):
        return 409, "Bu uyarı zaten onaylanmış; bağlantı yeniden kullanılamaz.", None, 0
    return 200, "", alert, r


# ─────────────────────────────── onay kaydı ───────────────────────────────

async def record_ack(alert: dict, *, by: str, via: str, recipient: str = "") -> Optional[dict]:
    """Uyarıyı ATOMİK olarak onaylar (ilk onay kazanır). Zaten onaylıysa None."""
    now = _now()
    created = _parse_iso(alert.get("created_at")) or now
    secs = max(0, int((now - created).total_seconds()))
    upd = {"acked_at": now.isoformat(), "acked_by": str(by or "-")[:200], "ack_via": via, "ack_seconds": secs}
    if recipient:
        upd["ack_recipient"] = recipient
    res = await db.alerts.update_one({"id": alert["id"], "ack_required": True, "acked_at": {"$exists": False}},
                                     {"$set": upd})
    if not res.modified_count:
        return None
    drill = await _drill_on_ack(alert, now, secs, upd["acked_by"])
    return {**upd, "drill": drill}


async def _drill_on_ack(alert: dict, now: datetime, secs: int, by: str) -> Optional[dict]:
    did = str((alert.get("meta") or {}).get("drill_id") or "")
    if not did:
        return None
    base = {"acked_at": now.isoformat(), "ack_seconds": secs, "acked_by": by}
    passed = secs <= DRILL_PASS_MINUTES * 60
    r = await db.security_drills.update_one(
        {"id": did, "status": S_PENDING},
        {"$set": {**base, "status": S_PASSED if passed else S_FAILED, "finalized_at": now.isoformat(),
                  "result_reason": (f"{DRILL_PASS_MINUTES} dakika içinde onaylandı" if passed
                                    else f"geç onaylandı ({secs // 60} dk)")}})
    if not r.modified_count:
        # Tatbikat zaten "kaldı" olarak kapandı → geç onay yine ölçülür, sonuç değişmez.
        await db.security_drills.update_one({"id": did, "acked_at": {"$exists": False}},
                                            {"$set": {**base, "late_ack": True}})
    return await db.security_drills.find_one({"id": did}, {"_id": 0})


async def _audit(action: str, entity_id: str, *, actor=None, request=None, source: str = "security",
                 metadata: Optional[dict] = None) -> None:
    try:
        from activity_audit import record_admin_audit
        await record_admin_audit(db, action=action, entity_type="security_alert", entity_id=str(entity_id)[:160],
                                 current_user=actor, request=request, source=source, metadata=metadata or {})
    except Exception as e:  # noqa: BLE001
        logger.warning("alert_ack audit failed %s: %s", action, type(e).__name__)


# ─────────────────────────────── yükseltme (escalation) ───────────────────────────────

def _ascii(s: str) -> str:
    return str(s or "").translate(str.maketrans("çğıöşüÇĞİÖŞÜâîû—–’", "cgiosuCGIOSUaiu--'"))


async def _sms_phones() -> list:
    from notification_service import normalize_phone_tr
    raw = str(await _rule("security.alert_sms_phones", "") or "")
    cands = [x for x in raw.replace(";", ",").replace("\n", ",").split(",") if x.strip()]
    if not cands:
        try:
            async for u in db.users.find({"is_super_admin": True, "is_active": {"$ne": False},
                                          "phone": {"$nin": [None, ""]}}, {"_id": 0, "phone": 1}).limit(10):
                cands.append(str(u.get("phone") or ""))
        except Exception:
            pass
    out = []
    for c in cands:
        n = normalize_phone_tr(c)
        if n and len(n) >= 10 and n not in out:
            out.append(n)
    return out[:ESCALATION_SMS_MAX_PHONES]


async def _escalation_sms(alert: dict, minutes: int, now: datetime) -> dict:
    """Kısa SMS — küresel SMS bütçe kapısından NORMAL öncelikle (MFA yedek payı kullanılmaz)."""
    import notification_service as ns
    phones = await _sms_phones()
    if not phones:
        return {"sent": 0, "attempted": 0, "reason": "no_phone"}
    cfg = await ns._get_providers_config(db)
    active = cfg.get("sms_active")
    if not active:
        return {"sent": 0, "attempted": 0, "reason": "sms_not_configured"}
    day0 = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    used = await db.alerts.count_documents({"escalation.sms_sent": {"$gt": 0}, "escalated_at": {"$gte": day0}})
    if used >= ESCALATION_SMS_DAILY_MAX:
        return {"sent": 0, "attempted": 0, "reason": "escalation_sms_daily_cap"}
    store = ""
    try:
        from security.alerts import _company_cached
        store = str((await _company_cached() or {}).get("store_name") or "").strip()
    except Exception:
        pass
    msg = _ascii(f"{store + ' ' if store else ''}GUVENLIK - YANIT YOK: kritik uyari {minutes} dk onaylanmadi: "
                 f"{str(alert.get('title') or '')[:70]}. E-postadaki onay baglantisi ya da Guvenlik Paneli.")[:300]
    impl = ns.SMS_IMPL.get(active, ns._sms_generic)
    prov = (cfg.get("providers") or {}).get(active, {})
    sent, errors = 0, []
    with ns.sms_priority("normal"):
        for p in phones:
            try:
                res = await impl(prov, p, msg)
            except Exception as e:  # noqa: BLE001
                res = {"success": False, "response": type(e).__name__}
            await ns._log_event(db, event="security_alert_escalation", channel="sms", to=p,
                                status="success" if res.get("success") else "failed",
                                response=str(res.get("response") or "")[:200])
            if res.get("success"):
                sent += 1
            else:
                errors.append(str(res.get("response") or "hata")[:80])
    return {"sent": sent, "attempted": len(phones), "errors": errors}


async def _escalate_one(alert: dict, minutes: int, now: datetime) -> dict:
    from security import alerts as A
    targets = list(alert.get("recipients") or []) or await A.resolve_default_recipients()
    subject = ("[YANIT YOK] " + str(alert.get("subject") or f"[CRITICAL] {alert.get('title') or ''}"))[:200]
    head = (f"YANIT YOK — bu kritik güvenlik uyarısı {minutes} dakikadır onaylanmadı. Lütfen hemen inceleyin.\n\n"
            f"{alert.get('title') or ''}\n\nLevel: critical\nKind: {alert.get('kind')}\n"
            f"İlk gönderim: {alert.get('created_at')}\n\n{alert.get('body') or ''}\n")
    results = []
    for i, t in enumerate(targets):
        text = head + await ack_instructions(alert["id"], i, str(alert.get("ack_nonce") or ""), "critical",
                                             escalation=True)
        results.append(await A._deliver(subject, text, t))
    ok = sum(1 for r in results if r.get("ok"))
    try:
        sms = await _escalation_sms(alert, minutes, now)
    except Exception as e:  # noqa: BLE001
        sms = {"sent": 0, "attempted": 0, "reason": f"error:{type(e).__name__}"}
    esc = {"at": now.isoformat(), "minutes": minutes, "email_ok": ok, "email_total": len(results),
           "sms_sent": int(sms.get("sent") or 0), "sms": sms}
    if not ok:
        esc["delivery_error"] = (";".join(sorted({str(r.get("error") or "") for r in results if r.get("error")}))
                                 or "no_recipients")[:300]
    await db.alerts.update_one({"id": alert["id"]}, {"$set": {"escalation": esc}})
    await _audit("security.alert_escalated", alert["id"], source="security.escalation",
                 metadata={"kind": alert.get("kind"), "minutes": minutes, "email_ok": ok,
                           "email_total": len(results), "sms_sent": esc["sms_sent"],
                           "drill_id": (alert.get("meta") or {}).get("drill_id") or ""})
    logger.error("[guvenlik] kritik uyarı %s dk onaylanmadı → YANIT YOK yükseltmesi (uyarı %s, e-posta %s/%s, sms %s)",
                 minutes, alert["id"], ok, len(results), esc["sms_sent"])
    return esc


async def escalate_due_alerts() -> dict:
    now = _now()
    minutes = await escalation_minutes()
    due_before = (now - timedelta(minutes=minutes)).isoformat()
    since = (now - timedelta(hours=ESCALATION_LOOKBACK_HOURS)).isoformat()
    rows = await db.alerts.find(
        {"level": "critical", "ack_required": True, "acked_at": {"$exists": False},
         "escalated_at": {"$exists": False}, "created_at": {"$lte": due_before, "$gte": since}},
        {"_id": 0}).sort("created_at", 1).to_list(ESCALATION_BATCH)
    done = []
    for a in rows:
        # Atomik talep: çok worker / üst üste çalışmada bile uyarı başına TEK yükseltme.
        claim = await db.alerts.update_one(
            {"id": a["id"], "acked_at": {"$exists": False}, "escalated_at": {"$exists": False}},
            {"$set": {"escalated_at": now.isoformat()}})
        if not claim.modified_count:
            continue
        try:
            await _escalate_one(a, minutes, now)
            done.append(a["id"])
        except Exception as e:  # noqa: BLE001
            logger.error("[guvenlik] yükseltme başarısız (uyarı %s): %s", a["id"], type(e).__name__)
    finalized = await finalize_overdue_drills(now)
    return {"ok": True, "minutes": minutes, "escalated": done, "drills_finalized": finalized}


async def run_alert_escalation_check() -> dict:
    """Zamanlayıcı girişi (5 dk). Asla istisna fırlatmaz."""
    try:
        return await escalate_due_alerts()
    except Exception as e:  # noqa: BLE001
        logger.error("[guvenlik] uyarı yükseltme kontrolü çalışmadı: %s", type(e).__name__)
        return {"ok": False, "error": type(e).__name__}


# ─────────────────────────────── tatbikat ───────────────────────────────

class DrillBusy(Exception):
    pass


async def finalize_overdue_drills(now: Optional[datetime] = None) -> int:
    now = now or _now()
    cutoff = (now - timedelta(minutes=DRILL_PASS_MINUTES)).isoformat()
    n = 0
    for d in await db.security_drills.find({"status": S_PENDING, "started_at": {"$lte": cutoff}},
                                           {"_id": 0, "id": 1, "alert_id": 1}).to_list(20):
        r = await db.security_drills.update_one(
            {"id": d["id"], "status": S_PENDING},
            {"$set": {"status": S_FAILED, "finalized_at": now.isoformat(),
                      "result_reason": f"{DRILL_PASS_MINUTES} dakika içinde onaylanmadı"}})
        if r.modified_count:
            n += 1
            await _audit("security.drill_failed", d["id"], source="security.drill",
                         metadata={"alert_id": d.get("alert_id") or "", "reason": "no_ack_in_time"})
            logger.error("[guvenlik] alarm tatbikatı KALDI: %s dk içinde onay yok (tatbikat %s)",
                         DRILL_PASS_MINUTES, d["id"])
    return n


async def start_drill(*, actor: Optional[dict] = None, trigger: str = "elle", request=None) -> dict:
    """Gerçek uyarı hattından [TATBİKAT] etiketli kritik uyarı gönderir; security_drills'e kaydeder."""
    now = _now()
    await finalize_overdue_drills(now)
    if await db.security_drills.find_one({"status": S_PENDING}, {"_id": 0, "id": 1}):
        raise DrillBusy()
    did = generate_id()
    minutes = await escalation_minutes()
    who = str((actor or {}).get("email") or "sistem")
    doc = {"id": did, "status": S_PENDING, "trigger": trigger, "started_at": now.isoformat(), "started_by": who,
           "pass_minutes": DRILL_PASS_MINUTES, "escalation_minutes": minutes}
    await db.security_drills.insert_one(dict(doc))
    from security.alerts import send_alert
    body = ("BU BİR TATBİKATTIR — gerçek bir güvenlik olayı YOKTUR.\n\n"
            "Amaç: kritik bir güvenlik uyarısının bir insan tarafından en geç "
            f"{DRILL_PASS_MINUTES} dakika içinde görüldüğünü ölçmek (SOC-01).\n"
            "Yapılacak: aşağıdaki 'Gördüm / İnceliyorum' bağlantısını açıp butona basın ya da "
            "Güvenlik Paneli'nde uyarıyı 'Onayla' ile kapatın.\n"
            f"Başlatan: {who} ({'aylık otomatik tatbikat' if trigger == 'aylık' else 'elle'})")
    res = await send_alert(DRILL_KIND, DRILL_TITLE, body, level="critical", fingerprint=f"{DRILL_KIND}:{did}",
                           meta={"drill_id": did, "is_drill": True})
    rec = res.get("recipients") or []
    ok = sum(1 for r in rec if r.get("ok"))
    upd = {"alert_id": res.get("alert_id"), "delivered_ok": ok, "delivered_total": len(rec)}
    if not ok:
        upd.update({"status": S_FAILED, "finalized_at": _now().isoformat(),
                    "result_reason": "tatbikat uyarısı hiçbir alıcıya teslim edilemedi"})
    await db.security_drills.update_one({"id": did}, {"$set": upd})
    await _audit("security.drill_started", did, actor=actor, request=request, source="security.drill",
                 metadata={"trigger": trigger, "alert_id": upd["alert_id"], "delivered_ok": ok,
                           "delivered_total": len(rec)})
    return await db.security_drills.find_one({"id": did}, {"_id": 0})


async def run_monthly_security_drill() -> dict:
    """Zamanlayıcı girişi: her ayın ilk Pazartesi 10:07 (Europe/Istanbul). Kural kapalıysa çalışmaz;
    ay başına en fazla bir aylık tatbikat (atomik ay anahtarı). Asla istisna fırlatmaz."""
    try:
        if not _truthy(await _rule("security.monthly_drill_enabled", True)):
            logger.warning("[guvenlik] aylık alarm tatbikatı İşletme Kuralı ile kapalı — atlandı")
            return {"ok": True, "skipped": "disabled"}
        from zoneinfo import ZoneInfo
        key = datetime.now(ZoneInfo(DRILL_TZ)).strftime("%Y-%m")
        await db.settings.update_one({"id": _MONTHLY_DOC_ID}, {"$setOnInsert": {"id": _MONTHLY_DOC_ID, "month": ""}},
                                     upsert=True)
        claim = await db.settings.update_one({"id": _MONTHLY_DOC_ID, "month": {"$ne": key}},
                                             {"$set": {"month": key, "at": _now().isoformat()}})
        if not claim.modified_count:
            return {"ok": True, "skipped": "already_this_month"}
        try:
            d = await start_drill(trigger="aylık")
        except DrillBusy:
            return {"ok": True, "skipped": "busy"}
        return {"ok": True, "drill_id": d.get("id"), "status": d.get("status")}
    except Exception as e:  # noqa: BLE001
        logger.error("[guvenlik] aylık alarm tatbikatı çalışmadı: %s", type(e).__name__)
        return {"ok": False, "error": type(e).__name__}


def next_monthly_drill_at(now: Optional[datetime] = None) -> str:
    try:
        from apscheduler.triggers.cron import CronTrigger
        t = CronTrigger(day="1st mon", hour=10, minute=7, timezone=DRILL_TZ)
        nxt = t.get_next_fire_time(None, now or _now())
        return nxt.astimezone(timezone.utc).isoformat() if nxt else ""
    except Exception:
        return ""


async def selftest_drill_status(dbh=None) -> tuple:
    """Öz-test kontrolü 'Alarm tatbikatı' → (durum, açıklama)."""
    dbh = dbh if dbh is not None else db
    since = (_now() - timedelta(days=DRILL_SUCCESS_MAX_AGE_DAYS)).isoformat()
    ok = await dbh.security_drills.find_one({"status": S_PASSED, "started_at": {"$gte": since}}, {"_id": 0},
                                           sort=[("started_at", -1)])
    if ok:
        return "ok", (f"son başarılı tatbikat {str(ok.get('started_at'))[:16]} — onay süresi "
                      f"{int(ok.get('ack_seconds') or 0) // 60} dk {int(ok.get('ack_seconds') or 0) % 60} sn")
    last = await dbh.security_drills.find_one({}, {"_id": 0}, sort=[("started_at", -1)])
    tail = (f"son tatbikat {str(last.get('started_at'))[:16]}: {last.get('status')}"
            if last else "hiç tatbikat yapılmadı")
    return "warn", (f"son {DRILL_SUCCESS_MAX_AGE_DAYS} günde başarılı alarm tatbikatı yok ({tail}) — "
                    "Güvenlik Paneli → Tatbikat başlat")


# ─────────────────────────────── yönetim uçları (yalnız süper admin) ───────────────────────────────

router = APIRouter(prefix="/admin/security", tags=["security-alert-ack"])

_LIST_PROJ = {"_id": 0, "id": 1, "kind": 1, "level": 1, "title": 1, "created_at": 1, "escalated_at": 1,
              "acked_at": 1, "acked_by": 1, "ack_via": 1, "ack_seconds": 1, "meta.drill_id": 1}


def _row(a: dict) -> dict:
    a = dict(a)
    a["is_drill"] = bool((a.pop("meta", None) or {}).get("drill_id"))
    return a


@router.get("/alert-acks")
async def list_alert_acks(current_user: dict = Depends(require_super_admin)):
    since = (_now() - timedelta(days=7)).isoformat()
    base = {"ack_required": True, "level": {"$in": list(ACK_LEVELS)}, "created_at": {"$gte": since}}
    pending = await db.alerts.find({**base, "acked_at": {"$exists": False}}, _LIST_PROJ) \
        .sort("created_at", -1).to_list(50)
    acked = await db.alerts.find({**base, "acked_at": {"$exists": True}}, _LIST_PROJ) \
        .sort("acked_at", -1).to_list(20)
    return {"pending": [_row(a) for a in pending], "recent_acked": [_row(a) for a in acked],
            "pending_critical": sum(1 for a in pending if a.get("level") == "critical"),
            "escalation_minutes": await escalation_minutes()}


@router.post("/alert-acks/{alert_id}/ack")
async def ack_alert_from_panel(alert_id: str, request: Request, current_user: dict = Depends(require_super_admin)):
    alert = await db.alerts.find_one({"id": str(alert_id)[:64], "ack_required": True}, {"_id": 0, "ack_nonce": 0})
    if not alert:
        raise HTTPException(status_code=404, detail="Onay bekleyen uyarı bulunamadı.")
    if alert.get("acked_at"):
        raise HTTPException(status_code=409, detail="Uyarı zaten onaylanmış.")
    from activity_audit import run_audited
    out: dict = {}

    async def _perform():
        out["res"] = await record_ack(alert, by=str(current_user.get("email") or current_user.get("id") or "-"),
                                      via="panel")
        if out["res"] is None:
            raise HTTPException(status_code=409, detail="Uyarı zaten onaylanmış.")
    await run_audited(db, action="security.alert_ack", entity_type="security_alert", entity_id=alert["id"],
                      perform=_perform, current_user=current_user, request=request, source="admin.security",
                      metadata={"via": "panel", "kind": alert.get("kind"), "level": alert.get("level"),
                                "drill_id": (alert.get("meta") or {}).get("drill_id") or ""})
    res = out["res"]
    return {"success": True, "acked_at": res["acked_at"], "ack_seconds": res["ack_seconds"],
            "acked_by": res["acked_by"], "drill": res.get("drill")}


@router.get("/drills")
async def list_drills(current_user: dict = Depends(require_super_admin)):
    await finalize_overdue_drills()
    hist = await db.security_drills.find({}, {"_id": 0}).sort("started_at", -1).to_list(20)
    since = (_now() - timedelta(days=DRILL_SUCCESS_MAX_AGE_DAYS)).isoformat()
    last_ok = await db.security_drills.find_one({"status": S_PASSED}, {"_id": 0}, sort=[("started_at", -1)])
    return {"last": hist[0] if hist else None, "history": hist, "last_success": last_ok,
            "recent_success": bool(last_ok and str(last_ok.get("started_at") or "") >= since),
            "pass_minutes": DRILL_PASS_MINUTES, "escalation_minutes": await escalation_minutes(),
            "monthly_enabled": _truthy(await _rule("security.monthly_drill_enabled", True)),
            "next_monthly_at": next_monthly_drill_at()}


@router.post("/drills/start")
async def post_drill_start(request: Request, current_user: dict = Depends(require_super_admin)):
    await finalize_overdue_drills()
    if await db.security_drills.find_one({"status": S_PENDING}, {"_id": 0, "id": 1}):
        raise HTTPException(status_code=409, detail="Süren bir tatbikat var; onaylanmasını ya da "
                                                    f"{DRILL_PASS_MINUTES} dakikanın dolmasını bekleyin.")
    from activity_audit import run_audited
    out: dict = {}

    async def _perform():
        try:
            out["d"] = await start_drill(actor=current_user, trigger="elle", request=request)
        except DrillBusy:
            raise HTTPException(status_code=409, detail="Süren bir tatbikat var.")
    await run_audited(db, action="security.drill_start", entity_type="security_drill", entity_id="manual",
                      perform=_perform, current_user=current_user, request=request, source="admin.security")
    return out["d"]


# ─────────────────────────────── herkese açık onay bağlantısı ───────────────────────────────

public_router = APIRouter(prefix="/security", tags=["security-alert-ack-public"])


async def _page(inner: str, status_code: int = 200) -> HTMLResponse:
    """Beyaz etiket: logo GÖRSEL olarak (firma logo_url → site /logo.png). Uyarı içeriği GÖSTERİLMEZ."""
    store, logo = "", ""
    try:
        from company import get_company
        c = await get_company(db)
        store = c.get("store_name") or ""
        logo = (c.get("logo_url") or "").strip()
        if not logo and c.get("site_url"):
            logo = c["site_url"].rstrip("/") + "/logo.png"
    except Exception:
        pass
    logo_html = (f'<img src="{_html.escape(logo, quote=True)}" alt="{_html.escape(store, quote=True)}" '
                 'style="display:block;margin:0 auto 24px;max-height:40px;max-width:180px">') if logo else ""
    page = f"""<!doctype html><html lang="tr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow">
<title>Güvenlik uyarısı onayı</title></head>
<body style="font-family:system-ui,Arial,sans-serif;background:#fafafa;margin:0;padding:0">
<div style="max-width:460px;margin:12vh auto;background:#fff;border:1px solid #eee;border-radius:14px;padding:40px 32px;text-align:center">
{logo_html}{inner}
</div></body></html>"""
    return HTMLResponse(content=page, status_code=status_code,
                        headers={"Cache-Control": "no-store", "X-Robots-Tag": "noindex, nofollow",
                                 "Referrer-Policy": "no-referrer"})


def _p(msg: str) -> str:
    return f'<p style="color:#444;margin:0;line-height:1.6">{_html.escape(msg)}</p>'


@public_router.get("/alert-ack", response_class=HTMLResponse)
@(limiter.limit("30/minute") if limiter else (lambda f: f))
async def alert_ack_page(request: Request, t: str = ""):
    """ONAY SAYFASI — burada hiçbir kayıt DEĞİŞMEZ (bağlantı tarayıcıları sahte onay üretemez)."""
    code, msg, _alert, _r = await _check_token(t)
    if code != 200:
        return await _page(_p(msg), code)
    inner = (_p("Güvenlik uyarısını gördüğünüzü ve incelediğinizi onaylayın. Ayrıntılar yalnız "
                "Güvenlik Paneli'nde görünür.")
             + '<form method="post" style="margin:24px 0 0">'
             f'<input type="hidden" name="t" value="{_html.escape(t, quote=True)}">'
             '<button type="submit" style="display:inline-flex;align-items:center;justify-content:center;'
             'background:#111;color:#fff;border:0;border-radius:8px;padding:12px 28px;font-size:14px;'
             'cursor:pointer">Gördüm / İnceliyorum</button></form>')
    return await _page(inner)


@public_router.post("/alert-ack", response_class=HTMLResponse)
@(limiter.limit("30/minute") if limiter else (lambda f: f))
async def alert_ack_confirm(request: Request, t: str = ""):
    """Onayı KAYDEDER (tek kullanımlık). t sorgu parametresi ya da form alanı."""
    if not t:
        try:
            form = await request.form()
            t = str(form.get("t") or "")
        except Exception:
            t = ""
    code, msg, alert, r = await _check_token(t)
    if code != 200:
        return await _page(_p(msg), code)
    recips = list(alert.get("recipients") or [])
    recipient = _mask_email(recips[r]) if 0 <= r < len(recips) else ""
    res = await record_ack(alert, by="e-posta bağlantısı", via="email_link", recipient=recipient)
    if res is None:
        return await _page(_p("Bu uyarı zaten onaylanmış; bağlantı yeniden kullanılamaz."), 409)
    await _audit("security.alert_ack", alert["id"], request=request, source="security.ack_link",
                 metadata={"via": "email_link", "kind": alert.get("kind"), "level": alert.get("level"),
                           "ack_seconds": res["ack_seconds"], "recipient": recipient,
                           "drill_id": (alert.get("meta") or {}).get("drill_id") or ""})
    return await _page(_p("Teşekkürler — onayınız kaydedildi. Uyarıyı Güvenlik Paneli'nden inceleyebilirsiniz."))
