"""Security events → audit chain + alert e-mail to ACTIVE super admins.

Public helpers (safe to call from any route; they never raise):

    await security_event(kind, actor, meta)
        Generic security-relevant event (e.g. bulk personal-data export). Writes a chained
        admin-audit record (action "security.<kind>") and e-mails all active super admins
        (duplicates rate-limited per kind+actor, default 10 min). ``actor`` is the acting
        user dict (current_user) or None for system events; ``meta`` is a small dict WITHOUT
        secrets/PII (it is redacted anyway). Example:
            from security.events import security_event
            await security_event("member_export", current_user, {"rows": 1234, "format": "xlsx"})

    await notify_super_admins(kind, title, body, *, fingerprint, level="warning", meta=None)
        Alert fan-out to the default recipients (rule security.alert_emails → super admins → env → company).

    await note_staff_login(user, ip, ua, method)
        Called after a SUCCESSFUL staff login; alerts on a new IP /24 (IPv6 /48) prefix or a
        new device (user-agent hash). Known list per user, 90 days (collection
        ``staff_known_devices``).

    await alert_break_glass_on_boot()
        Once per process boot: alerts if any security break-glass env is active.

    fire_and_forget(coro)
        Schedules a coroutine in the background (keeps a strong ref, swallows errors) so
        login latency is not affected by SMTP.
"""
from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Optional

logger = logging.getLogger(__name__)

KNOWN_DEVICE_DAYS = 90
_BG_TASKS: set = set()
_BOOT_ALERT_SENT = False

# kind → (title, level, throttle seconds)
_EVENT_META = {
    "member_export": ("Toplu üye verisi dışa aktarıldı", "warning", 600),
    "consent_export": ("Toplu izin/rıza verisi dışa aktarıldı", "warning", 600),
    "returns_export": ("Toplu iade verisi dışa aktarıldı", "warning", 600),
    "customers_export": ("Toplu müşteri verisi dışa aktarıldı", "warning", 600),
    "orders_export": ("Toplu sipariş verisi dışa aktarıldı", "warning", 600),
    "products_export": ("Ürün listesi Excel'e aktarıldı", "info", 600),
    "product_report_export": ("Ürün raporu Excel'e aktarıldı", "info", 600),
    "report_table_export": ("Rapor tablosu Excel'e aktarıldı", "info", 600),
    "report_assistant_export": ("Rapor asistanı dosyası indirildi", "info", 600),
    "trendyol_claims_export": ("Trendyol iade listesi dışa aktarıldı", "warning", 600),
    "influencer_pr_export": ("Influencer PR listesi dışa aktarıldı", "warning", 600),
    "influencer_calendar_export": ("Influencer gönderim takvimi dışa aktarıldı", "warning", 600),
    "influencer_registry_export": ("Influencer kayıtları dışa aktarıldı", "warning", 600),
    "capi_logs_export": ("CAPI olay logları dışa aktarıldı", "info", 600),
    "production_plan_export": ("Üretim planı dışa aktarıldı", "info", 600),
    "amazon_catalog_export": ("Amazon katalog raporu dışa aktarıldı", "info", 600),
    "email_campaign_report_export": ("E-posta kampanya alıcı raporu dışa aktarıldı", "warning", 600),
    "gider_pusulasi_print_export": ("Gider pusulası toplu çıktısı alındı", "warning", 600),
    "vault_reveal": ("Kasa (vault) gizli değeri görüntülendi", "critical", 60),
    "role_grant_star": ("Tüm yetkiler ('*') verildi / süper admin oluşturuldu", "critical", 60),
    "ip_autoban": ("IP otomatik engellendi (brute-force)", "warning", 900),
    "staff_new_device": ("Yönetici girişi: yeni IP / cihaz", "warning", 900),
    "break_glass_active": ("ACİL KAPATMA (break-glass) ortam değişkeni aktif", "critical", 300),
    "audit_chain_broken": ("Denetim zinciri doğrulaması BAŞARISIZ", "critical", 3600),
    "audit_export_failed": ("Denetim kaydı arşive aktarılamadı", "warning", 3600),
    "mfa_recovery_used": ("Yönetici MFA kurtarma kodu kullandı", "critical", 60),
    "mfa_reset_by_admin": ("Yönetici başka bir personelin MFA ayarını sıfırladı", "warning", 60),
    "sessions_revoked_all": ("TÜM oturumlar sonlandırıldı (güvenlik epoch)", "critical", 60),
    "single_admin_exception": ("Tek süper admin istisnası: kritik işlem onaysız uygulandı", "warning", 60),
    "approval_requested": ("İkinci onay bekleyen kritik işlem", "info", 60),
    "approval_decided": ("Kritik işlem onaylandı / reddedildi", "info", 60),
    "staff_invite_accepted": ("Personel daveti kabul edildi", "info", 60),
    # AUTH14: kayıt ucunda hesap-varlığı taraması
    "register_enumeration_blocked": ("Kayıt ucunda toplu hesap taraması: ağ geçici olarak engellendi", "warning", 3600),
    "register_enumeration_wave": ("Kayıt ucunda dağıtık hesap taraması şüphesi (saatlik 'zaten kayıtlı' eşiği aşıldı)", "critical", 3600),
    "pii_bulk_reveal_mfa": ("Doğrulamalı personel toplu kişisel veri açtı (engellenmedi, bilgi)", "warning", 3600),
    "pii_bulk_reveal": ("TOPLU kişisel veri çözme (IBAN/TCKN/VKN/kasa) — personel geçici engellendi", "critical", 60),
    "sms_budget_exceeded": ("Küresel SMS tavanı aşıldı — rutin SMS'ler durduruldu", "critical", 300),
    "security_pipeline_degraded": ("Güvenlik kayıt/uyarı hattında kesinti", "critical", 1800),
    "restore_finalized": ("Geri yükleme kapatıldı: oturumlar, KVKK silmeleri ve personel iptalleri yeniden uygulandı", "critical", 60),
    # CHAIN08: geri yükleme sonrası iade kilidi
    "refund_hold_cleared": ("Geri yükleme sonrası iyzico mutabakatı temiz: iadeler yeniden açıldı", "warning", 60),
    "refund_hold_released": ("Geri yükleme sonrası iade kilidi ELLE kaldırıldı", "critical", 60),
    "refund_hold_reconcile_requested": ("Geri yükleme sonrası iyzico mutabakatı elle başlatıldı", "info", 60),
    "post_restore_reconciliation_failed": ("Geri yükleme sonrası iyzico mutabakatı tamamlanamadı: iadeler kilitli", "critical", 60),
    # CHAIN09: akmakta olan dışa aktarım erişim iptali / dondurma nedeniyle kesildi
    "export_aborted_access_revoked": ("Dışa aktarım yarıda kesildi: personel erişimi iptal edildi / dışa aktarımlar donduruldu", "critical", 60),
    # T19 / ADM-08: vitrin bütünlük izleyicisi (security/storefront_integrity.py). Her sorun için
    # izleyici kendi tarafında "değişiklik başına bir kez" uygular; buradaki kısma yalnız yedek.
    "storefront_unknown_script_host": ("Vitrinde izin listesi DIŞI bir host'tan script yükleniyor", "critical", 60),
    "storefront_csp_weakened": ("Vitrinin CSP başlığı kayboldu ya da zayıfladı", "critical", 60),
    "storefront_csp_source_added": ("Vitrin CSP script izin listesine yeni kaynak eklendi", "warning", 60),
    "storefront_bundle_tampered": ("Vitrin JS paketinin İÇERİĞİ değişti (aynı dosya adı) — kurcalama", "critical", 60),
    "storefront_inline_changed": ("Vitrin index.html satır içi script'leri deploy olmadan değişti", "warning", 60),
    "storefront_rollback": ("Vitrin daha önce görülmüş bir sürüme geri döndü", "warning", 60),
    "storefront_bundle_changed": ("Vitrin paketi değişti (yeni deploy)", "info", 60),
    "storefront_csp_changed": ("Vitrinin CSP başlığı değişti", "info", 60),
    "storefront_watch_failed": ("Vitrin bütünlük izleyicisi vitrine ulaşamıyor", "warning", 3600),
    "storefront_quarantined_served": ("KARANTİNADAKİ vitrin sürümü yeniden sunuluyor", "critical", 60),
    "storefront_quarantine_changed": ("Vitrin karantina listesi değişti", "warning", 60),
    "storefront_quarantine_blocked": ("Karantinadaki kod yeniden yayınlanmak istendi — engellendi", "critical", 60),
    # T19 build kimliği: vitrin derlemesinin gömdüğü git commit'i backend'in deploy commit'iyle
    # eşleşmiyor / yok / beklenmeyen daldan → izinsiz deploy şüphesi.
    "storefront_build_unverified": ("Vitrine DOĞRULANMAMIŞ bir build (deploy) sunuluyor", "critical", 60),
    "storefront_build_verified": ("Vitrin build kimliği doğrulandı (backend deploy commit'i / onay)", "info", 60),
    "storefront_build_approved": ("Vitrin build'i süper admin tarafından elle onaylandı", "warning", 60),
}


def _db():
    from routes.deps import db  # lazy: routes.deps imports nothing from here at module load
    return db


def fire_and_forget(coro) -> None:
    try:
        task = asyncio.get_running_loop().create_task(coro)
    except RuntimeError:
        try:
            coro.close()
        except Exception:
            pass
        return
    _BG_TASKS.add(task)

    def _done(t):
        _BG_TASKS.discard(t)
        try:
            t.exception()
        except Exception:
            pass
    task.add_done_callback(_done)


def _h(v: str, n: int = 16) -> str:
    return hashlib.sha256(str(v or "").encode()).hexdigest()[:n]


async def notify_super_admins(kind: str, title: str, body: str = "", *, fingerprint: str,
                              level: str = "warning", meta: Optional[dict] = None) -> int:
    """Alert fan-out via security.alerts.send_alert to the DEFAULT recipients
    (İşletme Kuralı security.alert_emails → active super admins → ALERT_TO_EMAIL → company
    contact e-mail). Each recipient gets its own delivery (recorded per recipient on the
    alerts doc). Throttled per fingerprint. Never raises; returns delivered count."""
    try:
        from security.alerts import send_alert
        res = await send_alert(kind, title, body, level=level, fingerprint=fingerprint, meta=meta or {})
        return sum(1 for r in (res or {}).get("recipients") or [] if r.get("ok"))
    except Exception as e:
        logger.warning("security alert failed kind=%s: %s", kind, type(e).__name__)
        return 0


def _actor_line(actor: Optional[dict]) -> str:
    if not actor:
        return "sistem"
    return str(actor.get("email") or actor.get("id") or "?")


async def security_event(kind: str, actor: Optional[dict] = None, meta: Optional[dict] = None,
                         *, request=None, title: Optional[str] = None, body: str = "",
                         audit: bool = True, alert: bool = True) -> None:
    """Record + alert a security-relevant event. Never raises (see module docstring)."""
    kind = str(kind or "event")[:60]
    m_title, m_level, m_throttle = _EVENT_META.get(kind, (kind, "warning", 600))
    meta = dict(meta or {})
    if audit:
        try:
            from activity_audit import record_admin_audit
            await record_admin_audit(_db(), action=f"security.{kind}", entity_type="security_event",
                                     entity_id=str(meta.get("target") or kind)[:160],
                                     current_user=actor, request=request, source="security",
                                     metadata=meta)
        except Exception as e:
            logger.warning("security_event audit failed kind=%s: %s", kind, type(e).__name__)
    if not alert:
        return
    try:
        lines = [f"Olay: {title or m_title}", f"Yapan: {_actor_line(actor)}",
                 f"Zaman: {datetime.now(timezone.utc).strftime('%d.%m.%Y %H:%M UTC')}"]
        for k, v in list(meta.items())[:15]:
            lines.append(f"{k}: {str(v)[:200]}")
        if body:
            lines += ["", body]
        lines += ["", "Bu işlemi siz ya da ekibiniz yapmadıysa hemen Güvenlik Paneli'nden kontrol edin."]
        # Throttle bucket: same kind + actor within the kind's window → one e-mail.
        bucket = int(datetime.now(timezone.utc).timestamp() // max(60, m_throttle))
        fp = f"secevt:{kind}:{(actor or {}).get('id') or '-'}:{meta.get('fp') or ''}:{bucket}"
        await notify_super_admins(kind, title or m_title, "\n".join(lines), fingerprint=fp,
                                  level=m_level, meta={"kind": kind})
    except Exception as e:
        logger.warning("security_event alert failed kind=%s: %s", kind, type(e).__name__)


# ─────────────────────── new IP / device on staff login ───────────────────────

def ip_prefix(ip: str) -> str:
    try:
        a = ipaddress.ip_address(str(ip or "").strip())
    except Exception:
        return ""
    if a.version == 4:
        return str(ipaddress.ip_network(f"{a}/24", strict=False))
    return str(ipaddress.ip_network(f"{a}/48", strict=False))


_UA_DIGITS = re.compile(r"\d+")


def device_hash(ua: str) -> str:
    """Tarayıcı/işletim sistemi AİLESİ — sürüm numaraları hariç. Chrome ~4 haftada, iOS/Safari her
    güncellemede sürüm değiştirir; tam UA özeti aynı bilgisayarı her güncellemede "yeni cihaz"
    sayıyordu. (UA istemci beyanıdır; sürüm rakamları güvenlik sağlamaz, IP bloğu kontrolü sürer.)"""
    return _h(_UA_DIGITS.sub("", (ua or "").strip().lower()[:400]), 20) if ua else ""


def _legacy_device_hash(ua: str) -> str:
    """Önceki (sürüm-duyarlı) özet — mevcut kayıtlar yeniden "yeni cihaz" uyarısı üretmesin."""
    return _h((ua or "").strip().lower()[:400], 20) if ua else ""


def _short_ua(ua: str) -> str:
    u = str(ua or "")
    return (u[:120] + "…") if len(u) > 120 else (u or "-")


async def note_staff_login(user: dict, ip: str, ua: str, method: str = "") -> None:
    """Remember (prefix, device) for this staff user; alert super admins when either is new.
    First ever recorded login is seeded silently only if this IP already had a successful
    login in the last 30 days (auth_audit_logs) — otherwise it alerts as well."""
    try:
        if not user or not (user.get("is_admin") is True or user.get("is_super_admin") is True):
            return
        try:  # kritik uyarı çağrısı: yetkili panele girdi → tekrar SMS durur
            from security.alert_paging import note_panel_presence
            await note_panel_presence(user)
        except Exception:
            pass
        db = _db()
        uid = str(user.get("id") or "")
        net, dev = ip_prefix(ip), device_hash(ua)
        if not uid or not (net or dev):
            return
        now = datetime.now(timezone.utc)
        since = (now - timedelta(days=KNOWN_DEVICE_DAYS)).isoformat()
        known = await db.staff_known_devices.find(
            {"user_id": uid, "last_seen": {"$gte": since}},
            {"_id": 0, "net": 1, "dev": 1}).to_list(200)
        known_net = any(k.get("net") == net for k in known) if net else True
        _devs = {dev, _legacy_device_hash(ua)}
        known_dev = any(k.get("dev") in _devs for k in known) if dev else True
        alert = not (known_net and known_dev)
        if alert and not known:
            try:
                from routes.deps import _is_known_ip
                if await _is_known_ip(uid, ip):
                    alert = False  # first run after deploy: seed silently from login history
            except Exception:
                pass
        await db.staff_known_devices.update_one(
            {"user_id": uid, "net": net, "dev": dev},
            {"$set": {"last_seen": now.isoformat()},
             "$setOnInsert": {"user_id": uid, "net": net, "dev": dev, "first_seen": now.isoformat()}},
            upsert=True)
        if random_prune():
            await db.staff_known_devices.delete_many({"last_seen": {"$lt": since}})
        if alert:
            what = []
            if not known_net:
                what.append("yeni IP bloğu")
            if not known_dev:
                what.append("yeni cihaz/tarayıcı")
            await security_event(
                "staff_new_device", user,
                {"target": uid, "staff": user.get("email") or uid, "ip_prefix": net or "-",
                 "device": _short_ua(ua), "method": method or "-", "what": ", ".join(what),
                 "fp": f"{net}|{dev}"},
                body="Personel hesabına daha önce görülmeyen bir IP bloğundan / cihazdan giriş yapıldı.")
    except Exception as e:
        logger.warning("note_staff_login failed: %s", type(e).__name__)


def random_prune() -> bool:
    import random as _r
    return _r.random() < 0.02


# ─────────────────────────────── break-glass ───────────────────────────────

_OFF = ("0", "off", "false", "no", "disable")
_ON = ("1", "on", "true", "yes", "enable")


def active_break_glass_flags() -> list:
    flags = []
    if (os.environ.get("ADMIN_MFA_ENFORCE") or "").strip().lower() in _OFF:
        flags.append("ADMIN_MFA_ENFORCE=off")
    if (os.environ.get("ADMIN_MFA_CHANNEL_POLICY") or "").strip().lower() in _OFF:
        flags.append("ADMIN_MFA_CHANNEL_POLICY=off")
    if (os.environ.get("TWO_PERSON_RULE") or "").strip().lower() in _OFF:
        flags.append("TWO_PERSON_RULE=off")
    if (os.environ.get("ALLOW_DIRECT_STAFF_CREATE") or "").strip().lower() in _ON:
        flags.append("ALLOW_DIRECT_STAFF_CREATE=1")
    if (os.environ.get("STAFF_IDLE_TIMEOUT") or "").strip().lower() in _OFF:
        flags.append("STAFF_IDLE_TIMEOUT=off")
    return flags


async def alert_break_glass_on_boot() -> None:
    """Once per process boot (and throttled across workers): alert if a break-glass env is on."""
    global _BOOT_ALERT_SENT
    if _BOOT_ALERT_SENT:
        return
    _BOOT_ALERT_SENT = True
    flags = active_break_glass_flags()
    if not flags:
        return
    logger.warning("[guvenlik] break-glass aktif: %s", ", ".join(flags))
    await security_event(
        "break_glass_active", None, {"flags": ", ".join(flags), "fp": "|".join(flags)},
        body=("Sunucu bu acil durum ayarlarıyla başlatıldı. Kilitlenme giderildiyse Railway ortam "
              "değişkenlerinden kaldırıp yeniden başlatın; aksi halde koruma devre dışı kalır."))



# ─────────────────────── PRIV07: toplu çözme (bulk decryption) alarmı ───────────────────────
# Tam IBAN / TCKN / VKN / kasa değeri her AÇIK gösterildiğinde personel başına sayaç artar
# (db.pii_reveal_counters; 1 saatlik pencere, ATOMİK $inc). Pencerede eşik (env PII_REVEAL_HOURLY_MAX,
# varsayılan 50) AŞILIRSA: kritik güvenlik uyarısı (pencere başına bir kez) + o personelin açık değer
# görüntülemesi 1 SAAT engellenir (429). Sayaç deposu hatasında FAIL-CLOSED (503) — açık değer verilmez.
#
# Kullanım (açık değer DÖNDÜRMEDEN ÖNCE çağır; n = bu yanıtta açılacak kayıt sayısı):
#     from security.events import note_pii_reveal
#     await note_pii_reveal(current_user, "refund_iban", n_full, request=request)
# Engellendiğinde maskeli veriye düşmek isteyen çağıran HTTPException(429/503) yakalayıp maskeleyebilir;
# yalnız soru sormak için: await pii_reveal_allowed(current_user)  (sayaç artırmaz).
PII_REVEAL_WINDOW_SEC = 3600
PII_REVEAL_BLOCK_SEC = 3600


def _pii_reveal_max() -> int:
    try:
        return max(1, int((os.environ.get("PII_REVEAL_HOURLY_MAX") or "50").strip()))
    except Exception:
        return 50


async def pii_reveal_allowed(actor: Optional[dict]) -> bool:
    """Personelin açık değer görüntülemesi şu an engelli mi? (sayaç artırmaz; hata → False)."""
    uid = str((actor or {}).get("id") or "")
    if not uid:
        return False
    try:
        doc = await _db().pii_reveal_counters.find_one({"_id": uid}, {"_id": 0, "blocked_until": 1}) or {}
        bu = doc.get("blocked_until")
        return not (bu and float(bu) > datetime.now(timezone.utc).timestamp())
    except Exception:
        return False


async def _pii_reveal_mfa_exempt(db) -> bool:
    """Bu istek SMS/MFA doğrulamalı personel oturumundan mı (token amr'de "mfa") ve kural muafiyeti açık mı?"""
    try:
        from routes.deps import CURRENT_STAFF_TOKEN
        tok = CURRENT_STAFF_TOKEN.get() or {}
        if "mfa" not in (tok.get("amr") or []):
            return False
        from business_rules import get_rule
        return bool(await get_rule(db, "security.pii_reveal_mfa_exempt", True))
    except Exception:
        return False


_PII_KIND_TR = {"iban": "iade IBAN", "refund_iban": "iade IBAN", "tax_id": "TCKN/VKN",
                "vault_secret": "kasa sırrı"}


def _pii_refs_text(refs: list, limit: int = 80) -> str:
    """Uyarı e-postası için: hangi kayıtların açık değeri görüntülendi (sipariş no / kayıt kimliği).
    Kişisel verinin kendisi YAZILMAZ — yalnız hangi kayıt ve tür."""
    rows, seen = [], set()
    for r in reversed(refs or []):
        if not isinstance(r, dict):
            continue
        k = str(r.get("k") or "")
        key = (k, str(r.get("r") or ""))
        if key in seen or not key[1]:
            continue
        seen.add(key)
        label = _PII_KIND_TR.get(k) or (("iletişim (" + k[8:] + ")") if k.startswith("contact_") else k)
        rows.append(f"• {key[1]} — {label}")
        if len(rows) >= limit:
            break
    if not rows:
        return ""
    return ("Açılan kayıtlar (en yeniden eskiye, tekil; en çok " + str(limit) + "):\n" + "\n".join(rows))


async def note_pii_reveal(actor: Optional[dict], kind: str, n: int = 1, *, request=None, refs=None) -> int:
    """Açık değer görüntülemesini say; eşik aşılırsa 429 fırlatır (bkz. modül notu). Pencere içindeki
    toplam sayıyı döndürür. Personel kimliği yoksa (sistem çağrısı) sayılmaz.
    refs: açılan kayıtların kimlikleri (sipariş no vb.) — eşik uyarısında "hangi veriler açıldı" listesi."""
    from fastapi import HTTPException
    n = int(n or 0)
    uid = str((actor or {}).get("id") or "")
    if n <= 0 or not uid:
        return 0
    now = datetime.now(timezone.utc).timestamp()
    db = _db()
    limit = _pii_reveal_max()
    mfa_session = await _pii_reveal_mfa_exempt(db)
    try:
        cur = await db.pii_reveal_counters.find_one({"_id": uid}, {"_id": 0}) or {}
        if not mfa_session and cur.get("blocked_until") and float(cur["blocked_until"]) > now:
            raise HTTPException(status_code=429,
                                detail="Kısa sürede çok sayıda kişisel veri açıldığı için açık görüntüleme 1 saat "
                                       "kapatıldı. Güvenlik ekibine bilgi verildi.")
        # Pencere yoksa/eskidiyse yeni pencere (atomik; yarışta tek kazanan sıfırlar).
        await db.pii_reveal_counters.update_one(
            {"_id": uid, "$or": [{"window_start": {"$exists": False}},
                                 {"window_start": {"$lt": now - PII_REVEAL_WINDOW_SEC}}]},
            {"$set": {"window_start": now, "count": 0, "alerted": False, "kinds": {}, "refs": []}},
            upsert=False)
        await db.pii_reveal_counters.update_one(
            {"_id": uid}, {"$setOnInsert": {"window_start": now, "count": 0, "alerted": False, "kinds": {},
                                            "refs": []}},
            upsert=True)
        from pymongo import ReturnDocument
        _upd = {"$inc": {"count": n, f"kinds.{str(kind or 'pii')[:40]}": n}, "$set": {"last_at": now}}
        _refs = [{"k": str(kind or "pii")[:40], "r": str(x)[:80], "t": now} for x in (refs or []) if x][:100]
        if _refs:
            _upd["$push"] = {"refs": {"$each": _refs, "$slice": -300}}
        doc = await db.pii_reveal_counters.find_one_and_update(
            {"_id": uid}, _upd, return_document=ReturnDocument.AFTER) or {}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("pii reveal counter unavailable: %s", type(e).__name__)
        raise HTTPException(status_code=503, detail="Güvenlik sayacı şu an kullanılamıyor; açık değer gösterilmedi.")
    total = int(doc.get("count") or 0)
    if total <= limit:
        return total
    if mfa_session:
        # Sahip kararı (2026-10-05): SMS/MFA doğrulamasıyla giren personel ENGELLENMEZ; görüntülemeler
        # sayılır/denetlenir ve pencere başına TEK bilgi uyarısı (e-posta; SMS yok) gider.
        first = await db.pii_reveal_counters.find_one_and_update(
            {"_id": uid, "alerted": {"$ne": True}}, {"$set": {"alerted": True}}, projection={"_id": 1})
        if first:
            await security_event(
                "pii_bulk_reveal_mfa", actor,
                {"target": uid, "staff": (actor or {}).get("email") or uid, "count_1h": total, "limit": limit,
                 "last_kind": kind, "kinds": dict(doc.get("kinds") or {}), "fp": uid},
                request=request,
                body="SMS/MFA doğrulamalı personel oturumu bir saatte eşikten fazla kişisel veri açtı. Oturum "
                     "doğrulamalı olduğu için ENGELLENMEDİ; işlem beklenmiyorsa Güvenlik Paneli → Denetim "
                     "kayıtlarını inceleyin."
                     + (("\n\n" + _pii_refs_text(doc.get("refs"))) if doc.get("refs") else ""))
        return total
    until = now + PII_REVEAL_BLOCK_SEC
    first = await db.pii_reveal_counters.find_one_and_update(
        {"_id": uid, "alerted": {"$ne": True}},
        {"$set": {"alerted": True, "blocked_until": until}}, projection={"_id": 1})
    if not first:
        await db.pii_reveal_counters.update_one({"_id": uid}, {"$max": {"blocked_until": until}})
    else:
        await security_event(
            "pii_bulk_reveal", actor,
            {"target": uid, "staff": (actor or {}).get("email") or uid, "count_1h": total, "limit": limit,
             "last_kind": kind, "kinds": dict(doc.get("kinds") or {}), "blocked_minutes": PII_REVEAL_BLOCK_SEC // 60,
             "fp": uid},
            request=request,
            body="Personel bir saat içinde eşikten fazla tam IBAN / TCKN / VKN / kasa değeri açtı. Açık "
                 "görüntülemesi 1 saat engellendi. Beklenmeyen bir toplu erişimse hesabı Kullanıcılar & Roller "
                 "ekranından pasifleştirin ve Güvenlik Paneli → Denetim kayıtlarını inceleyin."
                 + (("\n\n" + _pii_refs_text(doc.get("refs"))) if doc.get("refs") else ""))
    raise HTTPException(status_code=429,
                        detail="Kısa sürede çok sayıda kişisel veri açıldığı için açık görüntüleme 1 saat "
                               "kapatıldı. Güvenlik ekibine bilgi verildi.")
