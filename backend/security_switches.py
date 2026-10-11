"""
GÜVENLİK ACİL ANAHTARLARI (kill switches) — olay müdahale runbook'unun ön koşulları.

Tek ayar belgesi: db.settings {id: "security_switches", switches: {<ad>: {on, reason, by, at}}}.
Okuma süreç-içi önbellekli (en çok 10 sn) — anahtarı açan personelden en geç 10 sn sonra TÜM
worker'larda etkin olur. Veritabanı okunamazsa son bilinen değer kullanılır (hiç okunamadıysa
kapalı kabul edilir; DB yoksa zaten hiçbir işlem yürümez).

Anahtarlar:
  freeze_refunds          — para iadesi uçları (iade ödemesi, iyzico iadesi, toplu iade, elle "iade
                            ödendi" statüsü, Temu iade komutu) 423 döner. Ortak kapı: require_refunds_allowed().
  freeze_exports          — TÜM toplu veri dışa aktarımları (üye/izin/iade/gider pusulası/ürün/rapor/
                            influencer/CAPI log/üretim planı/kampanya raporu …) 423 döner.
  pause_marketplace_sync  — pazaryeri senkron/push işleri durur (scheduler/entegrasyon `outbound_paused()`
                            ile sorar).
  restore_mode            — yedekten geri dönüş modu: dışarıya giden HER yazma (Doğan e-fatura, pazaryeri
                            fatura linki, kargo etiketi) durur; `outbound_paused()` da True döner.
                            CHAIN08: açıkken TÜM iade uçları da freeze_refunds gibi kapalıdır (423).
                            Kapatılınca (açık → kapalı) "geri yükleme sonrası iade kilidi"
                            (post_restore_refund_hold) AYNI yazımda açılır; restore penceresini kapsayan
                            iyzico mutabakatı başarıyla bitene ya da süper admin gerekçeyle kaldırana
                            kadar iadeler kapalı kalır (bkz. aşağıdaki "Geri yükleme sonrası iade kilidi").
  close_checkout          — yeni sipariş oluşturma 503 döner. BAŞLAMIŞ ödemelerin sonuçlandırılması
                            (3DS callback, mutabakat cron'u) ETKİLENMEZ — CLAUDE.md değişmez 4.

Yönetim uçları (YALNIZ süper-admin, denetim izi + güvenlik alarmı):
  GET  /api/admin/security-switches
  POST /api/admin/security-switches/{name}   body: {on: bool, reason: str}
  POST /api/admin/security-switches/refund-hold/reconcile   (restore penceresi mutabakatını yeniden başlatır)
  POST /api/admin/security-switches/refund-hold/release     body: {reason: str (≥10)}  (elle kaldırma)
"""
from __future__ import annotations

import inspect
import logging
import math
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request

logger = logging.getLogger(__name__)

_DOC_ID = "security_switches"
_CACHE_TTL_SEC = 10.0
_cache: dict = {"at": 0.0, "switches": None, "gen": 0}

SWITCHES = {
    "freeze_refunds": {
        "label": "İadeleri dondur",
        "description": "İade ödemesi işaretleme, iyzico kart iadesi ve toplu iade uçları kapanır.",
        "message": "Para iadesi işlemleri güvenlik nedeniyle geçici olarak donduruldu. "
                   "Lütfen süper yöneticiyle iletişime geçin.",
    },
    "freeze_exports": {
        "label": "Dışa aktarımları dondur",
        "description": "TÜM toplu Excel/CSV dışa aktarımları kapanır (üye, pazarlama izni, iade, gider "
                       "pusulası, ürün/rapor, Trendyol iade, influencer, CAPI log, üretim planı, kampanya).",
        "message": "Toplu veri dışa aktarımı güvenlik nedeniyle geçici olarak kapatıldı. "
                   "Lütfen süper yöneticiyle iletişime geçin.",
    },
    "pause_marketplace_sync": {
        "label": "Pazaryeri senkronunu duraklat",
        "description": "Pazaryeri stok/fiyat/sipariş senkronları ve fatura linki gönderimi durur.",
        "message": "Pazaryeri gönderimleri geçici olarak duraklatıldı.",
    },
    "restore_mode": {
        "label": "Geri yükleme modu",
        "description": "Yedekten dönüş sırasında dışarıya giden tüm yazmalar (e-fatura, pazaryeri, "
                       "kargo etiketi) durur.",
        "message": "Sistem geri yükleme modunda: e-fatura, pazaryeri ve kargo gönderimleri geçici "
                   "olarak durduruldu. İşlem tamamlanınca yeniden açılacak.",
    },
    "close_checkout": {
        "label": "Siparişi kapat",
        "description": "Yeni sipariş alınmaz (başlamış ödemelerin sonuçlanması etkilenmez).",
        "message": "Sitemizde kısa süreli bir bakım çalışması yapıyoruz; şu anda yeni sipariş "
                   "alamıyoruz. Lütfen birazdan tekrar deneyin. Sepetiniz kaydedildi.",
    },
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def invalidate_cache() -> None:
    # gen: okuma sürerken yapılan geçersizleştirme, o okumanın ESKİ değeri 10 sn önbelleğe yazmasını önler.
    _cache["at"] = 0.0
    _cache["gen"] = int(_cache.get("gen") or 0) + 1


async def _load_switches() -> dict:
    now = time.monotonic()
    if _cache["switches"] is not None and (now - _cache["at"]) < _CACHE_TTL_SEC:
        return _cache["switches"]
    gen = _cache.get("gen")
    try:
        from routes.deps import db
        doc = await db.settings.find_one({"id": _DOC_ID}, {"_id": 0, "switches": 1}) or {}
        sw = doc.get("switches") if isinstance(doc.get("switches"), dict) else {}
        if _cache.get("gen") == gen:
            _cache["switches"] = sw
            _cache["at"] = now
        return sw
    except Exception as e:  # pragma: no cover — DB hatası: son bilinen değer
        logger.warning(f"[security_switches] okunamadı, son bilinen değer kullanılıyor: {e}")
        return _cache["switches"] or {}


async def switch_on(name: str) -> bool:
    """Anahtar açık mı? (≤10 sn önbellek)"""
    sw = await _load_switches()
    ent = sw.get(name)
    if isinstance(ent, dict):
        return bool(ent.get("on"))
    return bool(ent) if isinstance(ent, bool) else False


async def outbound_paused() -> bool:
    """Scheduler/entegrasyonlar için: pazaryeri senkronu duraklatıldı ya da geri yükleme modu açık."""
    return (await switch_on("pause_marketplace_sync")) or (await switch_on("restore_mode"))


async def restore_mode_on() -> bool:
    return await switch_on("restore_mode")


def switch_message(name: str) -> str:
    return (SWITCHES.get(name) or {}).get("message") or "Bu işlem geçici olarak kapatıldı."


# ─────────────────────────── İade kapısı (CHAIN08) ───────────────────────────
# Para iadesi yapan / "iade ödendi" işaretleyen HER uç require_switch_off("freeze_refunds") ya da
# require_refunds_allowed() çağırır. Kapı üç durumda kapalıdır (423):
#   1. freeze_refunds açık (olay müdahalesi);
#   2. restore_mode açık (yedekten dönen DB, yedekten sonra iyzico'da tamamlanmış iadeyi bilmez);
#   3. geri yükleme sonrası iade kilidi (post_restore_refund_hold) açık — restore_mode kapatıldığında
#      AYNI yazımda açılır; restore penceresini kapsayan iyzico mutabakatı BAŞARIYLA bitince kalkar.
# Not: /iyzico/refund/resolve (belirsiz iadenin elle mutabakatı) para hareketi yapmadığı için açık kalır.
REFUND_HOLD_KEY = "post_restore_refund_hold"
REFUND_RESTORE_MSG = ("Veritabanı geri yükleme sonrası mutabakat tamamlanana kadar iade yapılamaz. "
                      "Sistem şu an geri yükleme modunda; geri yükleme kapatılıp iyzico mutabakatı "
                      "başarıyla bitince iadeler otomatik açılır.")
REFUND_HOLD_MSG = ("Veritabanı geri yükleme sonrası mutabakat tamamlanana kadar iade yapılamaz. "
                   "iyzico mutabakatı başarıyla bitince iadeler otomatik açılır; mutabakat başarısız "
                   "olduysa süper yönetici kontrol edip gerekçe yazarak kilidi kaldırabilir.")


async def refund_block() -> Optional[tuple]:
    """İadeler kapalıysa (kod, mesaj); açıksa None. ≤10 sn önbellekli anahtar okuması."""
    if await switch_on("freeze_refunds"):
        return "freeze_refunds", switch_message("freeze_refunds")
    if await switch_on("restore_mode"):
        return "restore_mode", REFUND_RESTORE_MSG
    if await switch_on(REFUND_HOLD_KEY):
        return REFUND_HOLD_KEY, REFUND_HOLD_MSG
    return None


async def require_refunds_allowed(status_code: int = 423) -> None:
    """TÜM para-iadesi uçlarının ortak kapısı (freeze_refunds + restore_mode + restore sonrası kilit)."""
    blk = await refund_block()
    if blk:
        raise HTTPException(status_code=status_code, detail=blk[1])


async def require_switch_off(name: str, status_code: int = 423) -> None:
    """Anahtar açıksa açık bir mesajla HTTPException fırlatır (423 Locked varsayılan).
    freeze_refunds kapısı TÜM iade uçlarının ortak giriş noktasıdır → restore_mode ve geri yükleme
    sonrası iade kilidi de burada uygulanır (CHAIN08, require_refunds_allowed).
    freeze_exports kapısı TÜM toplu dışa aktarımların ortak giriş noktasıdır → AUTH24 kurtarma-kodu
    kısıtı da burada uygulanır (isteği yapan personel kurtarma koduyla girip yeni faktör bağlamadıysa
    24 saat dışa aktarım yapamaz) ve istek "dışa aktarım" olarak işaretlenir: yanıt gönderilmeden hemen
    önce ve akış sırasında periyodik olarak personel/oturum/anahtar yeniden denetlenir (CHAIN09,
    security/export_guard.py)."""
    if name == "freeze_refunds":
        await require_refunds_allowed(status_code)
        return
    if await switch_on(name):
        raise HTTPException(status_code=status_code, detail=switch_message(name))
    if name == "freeze_exports":
        from routes.deps import assert_current_staff_not_recovery_restricted
        await assert_current_staff_not_recovery_restricted("export")
        try:
            from security.export_guard import register_export
            register_export()
        except Exception as e:  # pragma: no cover — kayıt yapılamazsa dışa aktarım yapılmaz
            logger.error("[export_guard] dışa aktarım kaydı yapılamadı: %s", type(e).__name__)
            raise HTTPException(status_code=503, detail="Dışa aktarım güvenlik denetimi başlatılamadı; "
                                                        "lütfen tekrar deneyin.")


async def emit_security_event(kind: str, actor=None, meta: Optional[dict] = None,
                              title: Optional[str] = None) -> None:
    """security.events.security_event varsa (denetim + alarm e-postası) çağırır; yoksa sessizce geçer.
    Asla istisna fırlatmaz — çağıran işlemi bozmaz."""
    try:
        from security.events import security_event  # type: ignore
    except Exception:
        return
    try:
        _actor = actor
        if isinstance(actor, dict):
            _actor = {"id": actor.get("id"), "email": actor.get("email")}
        try:
            res = security_event(kind, _actor, meta or {}, title=title) if title else \
                security_event(kind, _actor, meta or {})
        except TypeError:
            res = security_event(kind, _actor, meta or {})
        if inspect.isawaitable(res):
            await res
    except Exception as e:  # pragma: no cover
        logger.warning(f"[security_event] {kind} gönderilemedi: {e}")


# ─────────────────────────────── Yönetim uçları ───────────────────────────────
router = APIRouter(prefix="/admin/security-switches", tags=["security-switches"])


from routes.deps import require_super_admin  # noqa: E402 (statik yetki denetimi görebilsin)


def _super_admin_dep():
    return require_super_admin


@router.get("")
async def list_switches(current_user: dict = Depends(require_super_admin)):
    from routes.deps import db
    invalidate_cache()
    doc = await db.settings.find_one({"id": _DOC_ID}, {"_id": 0}) or {}
    sw = doc.get("switches") if isinstance(doc.get("switches"), dict) else {}
    items = []
    for name, meta in SWITCHES.items():
        ent = sw.get(name) if isinstance(sw.get(name), dict) else {}
        items.append({
            "name": name, "label": meta["label"], "description": meta["description"],
            "on": bool(ent.get("on")), "reason": ent.get("reason") or "",
            "by": ent.get("by") or "", "at": ent.get("at") or "",
        })
    hist = list(reversed((doc.get("history") or [])[-30:]))
    hold = sw.get(REFUND_HOLD_KEY) if isinstance(sw.get(REFUND_HOLD_KEY), dict) else {}
    return {"items": items, "history": hist, "cache_ttl_sec": int(_CACHE_TTL_SEC),
            "refund_hold": _public_hold(hold)}


@router.post("/refund-hold/reconcile")
async def refund_hold_reconcile(request: Request, background_tasks: BackgroundTasks,
                                payload: Optional[dict] = None,
                                current_user: dict = Depends(require_super_admin)):
    """Geri yükleme sonrası iade kilidi için restore penceresi iyzico mutabakatını (yeniden) başlatır.
    İsteği bekletmez (arka plan). Başarıyla biterse kilit kendiliğinden kalkar. Para hareketi yapmaz.
    Body (opsiyonel): {days: int} — pencereyi BÜYÜTMEK için (kilidin penceresinden kısa olamaz)."""
    from routes.deps import assert_no_recovery_restriction
    await assert_no_recovery_restriction(current_user, "security.refund_hold_reconcile")
    invalidate_cache()
    if await switch_on("restore_mode"):
        raise HTTPException(status_code=409, detail="Geri yükleme modu hâlâ açık; önce geri yüklemeyi kapatın.")
    hold = await get_refund_hold()
    if not hold.get("on"):
        raise HTTPException(status_code=409, detail="Açık bir geri yükleme sonrası iade kilidi yok.")
    days = None
    try:
        if isinstance(payload, dict) and payload.get("days") not in (None, ""):
            days = max(1, min(int(payload.get("days")), _HOLD_MAX_DAYS))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="days bir tam sayı olmalı")
    how = schedule_post_restore_reconciliation(hold.get("id"), background_tasks, actor=current_user, days=days)
    await emit_security_event("refund_hold_reconcile_requested", current_user,
                              {"hold_id": hold.get("id"), "days": days or "-",
                               "fp": f"{hold.get('id')}:{_now_iso()}"})
    return {"success": True, "scheduled": how, "refund_hold": _public_hold(hold)}


@router.post("/refund-hold/release")
async def refund_hold_release(payload: dict, request: Request,
                              current_user: dict = Depends(require_super_admin)):
    """Geri yükleme sonrası iade kilidini ELLE kaldırır (yalnız süper admin, ≥10 karakter gerekçe).
    FAIL-CLOSED denetim: kayıt yazılamazsa kilit KALKMAZ (503). Kritik güvenlik uyarısı üretir.
    restore_mode hâlâ açıksa kaldırılamaz (geri yükleme bitmeden mutabakatın anlamı yok).
    Not: acil anahtarların hiçbiri iki-kişi onayı (security/approvals.py) kullanmıyor; bu uç da
    anahtarlarla aynı modeli izler (süper admin + gerekçe + fail-closed denetim + kritik uyarı)."""
    from routes.deps import db, write_audit_log, client_ip_from_request, assert_no_recovery_restriction
    await assert_no_recovery_restriction(current_user, "security.refund_hold_release")
    payload = payload if isinstance(payload, dict) else {}
    reason = str(payload.get("reason") or "").strip()[:500]
    if len(reason) < _HOLD_RELEASE_MIN_REASON:
        raise HTTPException(status_code=400, detail=(
            f"Kilidi kaldırmak için iyzico panelinde neyi kontrol ettiğinizi yazın (en az "
            f"{_HOLD_RELEASE_MIN_REASON} karakter)."))
    invalidate_cache()
    if await switch_on("restore_mode"):
        raise HTTPException(status_code=409, detail="Geri yükleme modu hâlâ açık; önce geri yüklemeyi kapatın.")
    hold = await get_refund_hold()
    if not hold.get("on"):
        raise HTTPException(status_code=409, detail="Açık bir geri yükleme sonrası iade kilidi yok.")
    hid = str(hold.get("id") or "")
    now = _now_iso()

    async def _perform():
        res = await db.settings.update_one(
            {"id": _DOC_ID, f"switches.{REFUND_HOLD_KEY}.id": hid, f"switches.{REFUND_HOLD_KEY}.on": True},
            {"$set": {f"switches.{REFUND_HOLD_KEY}.on": False,
                      f"switches.{REFUND_HOLD_KEY}.status": "released",
                      f"switches.{REFUND_HOLD_KEY}.released_at": now,
                      f"switches.{REFUND_HOLD_KEY}.released_by": current_user.get("email") or "",
                      f"switches.{REFUND_HOLD_KEY}.release_reason": reason,
                      "updated_at": now},
             "$push": {"history": {"$each": [{"name": REFUND_HOLD_KEY, "on": False, "reason": reason,
                                              "by": current_user.get("email") or "", "at": now}],
                                   "$slice": -200}}})
        if not res.modified_count:
            raise HTTPException(status_code=409, detail="Kilit bu arada değişti; sayfayı yenileyip tekrar deneyin.")
    # FAIL-CLOSED denetim: kayıt (intent) yazılamazsa kilit KALDIRILMAZ (503).
    from activity_audit import run_audited
    await run_audited(
        db, action="security.refund_hold_release", entity_type="security_switch", entity_id=REFUND_HOLD_KEY,
        perform=_perform, before={"on": True}, after={"on": False}, current_user=current_user,
        request=request, source="admin.security_switches",
        metadata={"reason": reason, "hold_id": hid, "hold_status": hold.get("status") or "",
                  "window_since": hold.get("window_since") or ""})
    invalidate_cache()
    await write_audit_log(
        "refund_hold_release", user_id=current_user.get("id"), email=current_user.get("email"),
        ip=client_ip_from_request(request), user_agent=request.headers.get("user-agent"),
        success=True, meta={"hold_id": hid, "reason": reason[:200]})
    await emit_security_event(
        "refund_hold_released", current_user,
        {"hold_id": hid, "reason": reason[:200], "last_status": hold.get("status") or "", "fp": hid},
        title="Geri yükleme sonrası iade kilidi ELLE kaldırıldı")
    logger.warning("[security_switches] geri yükleme sonrası iade kilidi elle kaldırıldı (%s)",
                   current_user.get("email"))
    return {"success": True, "released": True, "hold_id": hid}


@router.post("/{name}")
async def toggle_switch(name: str, payload: dict, request: Request, background_tasks: BackgroundTasks,
                        current_user: dict = Depends(require_super_admin)):
    from routes.deps import db, write_audit_log, client_ip_from_request
    if name not in SWITCHES:
        raise HTTPException(status_code=404, detail="Bilinmeyen anahtar")
    payload = payload if isinstance(payload, dict) else {}
    if not isinstance(payload.get("on"), bool):
        raise HTTPException(status_code=400, detail="'on' (true/false) zorunlu")
    on = payload["on"]
    # AUTH24: kurtarma koduyla girişten sonra (yeni faktör bağlanana kadar, ≤24 sa) acil anahtarlar değişmez.
    from routes.deps import assert_no_recovery_restriction
    await assert_no_recovery_restriction(current_user, "security.switch_toggle")
    reason = str(payload.get("reason") or "").strip()[:300]
    if on and len(reason) < 5:
        raise HTTPException(status_code=400, detail="Anahtarı açmak için kısa bir gerekçe yazın (en az 5 karakter).")
    now = _now_iso()
    before = await db.settings.find_one({"id": _DOC_ID}, {"_id": 0, "switches": 1}) or {}
    _before_sw = before.get("switches") if isinstance(before.get("switches"), dict) else {}
    prev_on = bool((_before_sw.get(name) or {}).get("on"))
    prev_at = str((_before_sw.get(name) or {}).get("at") or "")
    entry = {"on": on, "reason": reason, "by": current_user.get("email") or "", "at": now}
    _set = {f"switches.{name}": entry, "updated_at": now}
    _hist = [{"name": name, **entry}]
    hold = None
    if name == "restore_mode" and prev_on and not on:
        # CHAIN08: geri yükleme kapanırken iade kilidi AYNI yazımda açılır → hiçbir worker'ın
        # "restore_mode kapalı ama kilit yok" durumunu görüp iade yapabileceği bir an olmaz.
        hold = await _build_refund_hold(current_user, restore_on_at=prev_at,
                                        previous=_before_sw.get(REFUND_HOLD_KEY), now_iso=now)
        _set[f"switches.{REFUND_HOLD_KEY}"] = hold
        _hist.append({"name": REFUND_HOLD_KEY, "on": True, "reason": hold["reason"],
                      "by": entry["by"], "at": now})

    async def _perform():
        await db.settings.update_one(
            {"id": _DOC_ID},
            {"$set": _set,
             "$push": {"history": {"$each": _hist, "$slice": -200}}},
            upsert=True,
        )
    # FAIL-CLOSED denetim: kayıt (intent) yazılamazsa anahtar DEĞİŞTİRİLMEZ (503).
    from activity_audit import run_audited
    await run_audited(
        db, action="security.switch_toggle", entity_type="security_switch", entity_id=name,
        perform=_perform, before={"on": prev_on}, after={"on": on}, current_user=current_user,
        request=request, source="admin.security_switches", metadata={"reason": reason})
    invalidate_cache()
    await write_audit_log(
        "security_switch_toggle", user_id=current_user.get("id"), email=current_user.get("email"),
        ip=client_ip_from_request(request), user_agent=request.headers.get("user-agent"),
        success=True, meta={"switch": name, "on": on, "previous": prev_on, "reason": reason})
    await emit_security_event(
        "kill_switch_toggled", current_user,
        {"switch": name, "on": on, "previous": prev_on, "reason": reason, "fp": f"{name}:{on}:{now}"},
        title=f"Acil anahtar değiştirildi: {SWITCHES[name]['label']} → {'AÇIK' if on else 'KAPALI'}")
    logger.warning(f"[security_switches] {name} -> {'AÇIK' if on else 'KAPALI'} ({current_user.get('email')})")
    out = {"success": True, "name": name, "on": on, "at": now}
    if name == "restore_mode" and prev_on and not on:
        # ADM-11/CHAIN12: geri yükleme bitti → iptalleri otomatik yeniden uygula.
        out["restore_finalize"] = await finalize_restore(current_user, request, restore_on_at=prev_at,
                                                         background_tasks=background_tasks, hold=hold)
        # Oturum dönemi arttı → çağıran süper admin için TAZE token (paneli açık kalsın; diğer herkes çıkar).
        try:
            from routes.deps import create_token, token_auth_time
            _hdr = request.headers.get("authorization") or ""
            _raw = _hdr.split(" ", 1)[1].strip() if _hdr.lower().startswith("bearer ") else ""
            out["token"] = create_token(current_user["id"], True,
                                        token_version=int(current_user.get("token_version") or 0),
                                        auth_time=token_auth_time(_raw) or None)
        except Exception:
            pass
    return out


# ─────────────────────────── Geri yükleme kapanışı (ADM-11 / CHAIN12) ───────────────────────────
# Yedekten dönen veritabanı, yedek anından SONRA yapılmış iptalleri bilmez: pasifleştirilmiş / silinmiş
# personel yeniden aktif, eski oturumlar geçerli, KVKK silmeleri geri gelmiş olabilir. restore_mode
# KAPATILDIĞINDA (açık → kapalı) otomatik olarak:
#   1. küresel oturum dönemi (security epoch) artırılır → HERKES yeniden giriş yapar;
#   2. KVKK mezar taşları gizli arşivden yeniden uygulanır (routes.kvkk.reapply_deletion_tombstones) ve
#      pazarlama retleri yeniden uygulanır (routes.email_marketing.reapply_marketing_suppressions);
#   3. personel iptalleri yeniden uygulanır: gizli R2 denetim arşivinde (denetim/*.jsonl), geri yüklenen
#      veritabanının EN SON denetim kaydından SONRA tamamlanmış rbac.user_update (is_active→False) /
#      rbac.user_delete işlemleri bulunur; kişi başına EN SON işlem pasifleştirme/silme olup DB'de hâlâ aktif
#      olan personel pasifleştirilir (token_version artar). Sonradan yeniden aktifleştirilmiş kişiye dokunulmaz.
# Her adım denetime yazılır (actor: anahtarı kapatan süper admin) ve süper adminlere kritik uyarı gider.
_STAFF_REVOKE_ACTIONS = ("rbac.user_update", "rbac.user_delete")


def _archive_audit_records() -> list:
    """Gizli arşivdeki TÜM denetim JSONL kayıtları (yalnız rbac personel işlemleri). Senkron."""
    import json as _json
    from services import r2_storage as r2
    if not r2.archive_enabled():
        return []
    out, token = [], None
    client = r2._get_client()
    while True:
        kw = {"Bucket": r2.archive_bucket(), "Prefix": "denetim/", "MaxKeys": 1000}
        if token:
            kw["ContinuationToken"] = token
        resp = client.list_objects_v2(**kw)
        for obj in resp.get("Contents") or []:
            data, _ = r2.archive_get(obj["Key"])
            if not data:
                continue
            for line in data.decode("utf-8", "replace").splitlines():
                if '"rbac.user_' not in line:
                    continue
                try:
                    rec = _json.loads(line)
                except Exception:
                    continue
                if isinstance(rec, dict) and rec.get("action") in _STAFF_REVOKE_ACTIONS:
                    out.append(rec)
        if resp.get("IsTruncated"):
            token = resp.get("NextContinuationToken")
        else:
            break
    return out


async def _restored_db_cutoff(db, restore_on_at: str) -> str:
    """Geri yüklenen veritabanının en son denetim kaydının zamanı (restore_mode açılmadan önceki)."""
    q = {"created_at": {"$lt": restore_on_at}} if restore_on_at else {}
    last = await db.audit_logs.find(q, {"_id": 0, "created_at": 1}).sort("created_at", -1).limit(1).to_list(1)
    return str((last[0] if last else {}).get("created_at") or "")


async def reapply_staff_revocations(actor: Optional[dict], request=None, *, restore_on_at: str = "") -> dict:
    import asyncio as _aio
    from routes.deps import db
    from activity_audit import run_audited
    stats = {"archive_records": 0, "considered_users": 0, "redeactivated": [], "already_inactive": 0,
             "cutoff": ""}
    recs = await _aio.to_thread(_archive_audit_records)
    stats["archive_records"] = len(recs)
    cutoff = await _restored_db_cutoff(db, restore_on_at)
    stats["cutoff"] = cutoff
    done_intents = {str((r.get("metadata") or {}).get("intent_id") or "") for r in recs if r.get("phase") == "done"}
    intents = [r for r in recs if r.get("phase") == "intent" and str(r.get("id") or "") in done_intents
               and str(r.get("created_at") or "") > cutoff
               and (not restore_on_at or str(r.get("created_at") or "") < restore_on_at)]
    intents.sort(key=lambda r: (str(r.get("created_at") or ""), int(r.get("chain_seq") or 0)))
    state: dict = {}
    for r in intents:
        uid = str((r.get("entity") or {}).get("id") or "")
        if not uid:
            continue
        if r.get("action") == "rbac.user_delete":
            state[uid] = "deleted"
            continue
        act = ((r.get("diff") or {}).get("is_active") or {})
        if "new" in act:
            state[uid] = "active" if act.get("new") is not False else "deactivated"
    stats["considered_users"] = len(state)
    for uid, st in state.items():
        if st not in ("deactivated", "deleted"):
            continue
        u = await db.users.find_one({"id": uid}, {"_id": 0, "id": 1, "is_active": 1, "is_admin": 1})
        if not u:
            continue
        if u.get("is_active") is False:
            stats["already_inactive"] += 1
            continue

        async def _perform(_uid=uid):
            await db.users.update_one({"id": _uid}, {"$set": {"is_active": False,
                                                              "deactivated_by_restore_finalize_at": _now_iso()},
                                                     "$inc": {"token_version": 1}})
        await run_audited(db, action="rbac.restore_reapply_deactivation", entity_type="panel_user", entity_id=uid,
                          perform=_perform, before={"is_active": True}, after={"is_active": False},
                          current_user=actor, request=request, source="security.restore_finalize",
                          metadata={"archived_state": st, "cutoff": cutoff})
        stats["redeactivated"].append(uid)
    return stats


async def finalize_restore(actor: Optional[dict], request=None, *, restore_on_at: str = "",
                           background_tasks=None, hold: Optional[dict] = None) -> dict:
    """restore_mode kapatılınca çağrılır (bkz. yukarıdaki not). Hata olsa bile diğer adımlar sürer;
    sonuç denetime ve uyarıya yazılır. İLK adım (CHAIN08): geri yükleme sonrası iade kilidinin açık
    olduğu doğrulanır (toggle_switch aynı yazımda açar; doğrudan çağrıda burada açılır) ve restore
    penceresini kapsayan iyzico mutabakatı arka planda başlatılır (istek bekletilmez)."""
    from routes.deps import bump_security_epoch
    res: dict = {}
    try:
        h = hold if (isinstance(hold, dict) and hold.get("on")) else \
            await ensure_refund_hold(actor, restore_on_at=restore_on_at)
        how = schedule_post_restore_reconciliation(h.get("id"), background_tasks)
        res["refund_hold"] = {**_public_hold(h), "reconciliation": how}
    except Exception as e:
        logger.error("[restore_finalize] iade kilidi/mutabakat başlatılamadı: %s", type(e).__name__)
        res["refund_hold_error"] = type(e).__name__
    try:
        res["epoch"] = await bump_security_epoch()
    except Exception as e:
        res["epoch_error"] = type(e).__name__
    try:
        from routes.kvkk import reapply_deletion_tombstones
        res["kvkk"] = await reapply_deletion_tombstones()
    except Exception as e:
        res["kvkk_error"] = type(e).__name__
    try:
        # BL-033: pazarlama retleri (abonelik iptali / bounce / şikâyet / İYS RET) arşivden yeniden uygulanır.
        from routes.email_marketing import reapply_marketing_suppressions
        res["marketing"] = await reapply_marketing_suppressions()
        logger.warning("[restore_finalize] pazarlama retleri yeniden uygulandı: %s",
                       {k: v for k, v in (res["marketing"] or {}).items() if k != "archive_key"})
    except Exception as e:
        res["marketing_error"] = type(e).__name__
    try:
        st = await reapply_staff_revocations(actor, request, restore_on_at=restore_on_at)
        res["staff"] = {**st, "redeactivated": len(st.get("redeactivated") or []),
                        "redeactivated_ids": list(st.get("redeactivated") or [])[:50]}
    except Exception as e:
        logger.error("[restore_finalize] personel iptalleri yeniden uygulanamadı: %s", type(e).__name__)
        res["staff_error"] = type(e).__name__
    # Denetim kaydı (security.restore_finalized) + kritik uyarı tek çağrıda (security_event).
    await emit_security_event(
        "restore_finalized", actor,
        {"epoch": res.get("epoch"), "staff_redeactivated": (res.get("staff") or {}).get("redeactivated"),
         "kvkk": str(res.get("kvkk") or res.get("kvkk_error") or "")[:200],
         "marketing": str({k: v for k, v in (res.get("marketing") or {}).items() if k != "archive_key"}
                          or res.get("marketing_error") or "")[:200],
         "refund_hold": ("açık — restore penceresi iyzico mutabakatı bekleniyor"
                         if (res.get("refund_hold") or {}).get("on") else str(res.get("refund_hold_error") or "-")),
         "errors": ", ".join(k for k in res if k.endswith("_error")) or "-", "fp": str(res.get("epoch"))})
    return res


# ─────────────────────── Geri yükleme sonrası iade kilidi (CHAIN08) ───────────────────────
# Eski bir yedekten dönüşte, yedekten SONRA iyzico'da tamamlanmış bir iade yerel kayıtta görünmez;
# bu durumda aynı iade ikinci kez yapılabilir (çift para çıkışı). Bu yüzden restore_mode kapanınca
# iadeler OTOMATİK kilitli kalır ve restore penceresini kapsayan iyzico mutabakatı
# (security.reconcile_iyzico.run_iyzico_reconciliation) çalıştırılır:
#   * mutabakat TAM ve TEMİZ biterse (hata yok, uyuşmazlık yok, doğrulanamayan sipariş yok, iade bilgisi
#     her siparişte kontrol edilebildi, sonuç kesilmedi) kilit FAIL-CLOSED denetimle kalkar;
#   * aksi halde kilit KALIR, durum "failed" olur ve kritik güvenlik uyarısı gider. Süper admin iyzico
#     panelinden kontrol edip yeniden çalıştırabilir (/refund-hold/reconcile) ya da gerekçe yazarak elle
#     kaldırabilir (/refund-hold/release).
# Pencere: geri yüklenen veritabanının son denetim kaydı (yedek anı) ile restore_mode'un açıldığı anın
# erkeni − güvenlik payı − iade-uygunluk geri bakışı (yedekten önce ödenip yedekten sonra iade edilen
# siparişler de taransın; mutabakat siparişleri ödeme/oluşturma tarihine göre seçer). En az 7 gün.
# Kilit, anahtar belgesinde sahte bir "anahtar" olarak durur (switches.post_restore_refund_hold) →
# switch_on() ile aynı ≤10 sn önbellekten okunur; genel /{name} ucu onu değiştiremez (404).
_HOLD_MIN_DAYS = 7
_HOLD_MARGIN_DAYS = 2
_HOLD_REFUND_LOOKBACK_DAYS = 30
_HOLD_MAX_DAYS = 400
_HOLD_RECON_LIMIT = 20000
_HOLD_RELEASE_MIN_REASON = 10
_SYSTEM_RECON_SOURCE = "security.post_restore_reconciliation"


def _parse_iso(v) -> Optional[datetime]:
    try:
        d = datetime.fromisoformat(str(v or "").replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _public_hold(h: Optional[dict]) -> dict:
    h = h if isinstance(h, dict) else {}
    keep = ("on", "id", "status", "set_at", "by", "restore_on_at", "backup_cutoff", "window_since",
            "attempts", "last_run_at", "last_problems", "cleared_at", "released_at", "released_by",
            "release_reason")
    out = {k: h.get(k) for k in keep if k in h}
    out["on"] = bool(h.get("on"))
    return out


async def get_refund_hold() -> dict:
    """Kilidin GÜNCEL hâli (önbelleksiz okuma)."""
    from routes.deps import db
    doc = await db.settings.find_one({"id": _DOC_ID}, {"_id": 0, f"switches.{REFUND_HOLD_KEY}": 1}) or {}
    h = (doc.get("switches") or {}).get(REFUND_HOLD_KEY)
    return h if isinstance(h, dict) else {}


async def _build_refund_hold(actor: Optional[dict], *, restore_on_at: str = "",
                             previous: Optional[dict] = None, now_iso: str = "") -> dict:
    """Yeni kilit belgesi (yazmaz). Önceki kilit hâlâ açıksa pencere ikisini birden kapsar."""
    from routes.deps import db
    now = _parse_iso(now_iso) or datetime.now(timezone.utc)
    anchors = [now]
    ron = _parse_iso(restore_on_at)
    if ron:
        anchors.append(ron)
    cutoff = ""
    try:
        cutoff = await _restored_db_cutoff(db, restore_on_at)
    except Exception as e:  # pragma: no cover — pencere yine restore_on_at'tan hesaplanır
        logger.warning("[refund_hold] yedek anı okunamadı: %s", type(e).__name__)
    cdt = _parse_iso(cutoff)
    if cdt:
        anchors.append(cdt)
    since_dt = min(anchors) - timedelta(days=_HOLD_MARGIN_DAYS + _HOLD_REFUND_LOOKBACK_DAYS)
    since_dt = min(since_dt, now - timedelta(days=_HOLD_MIN_DAYS))
    if isinstance(previous, dict) and previous.get("on"):
        pdt = _parse_iso(previous.get("window_since"))
        if pdt and pdt < since_dt:
            since_dt = pdt
    since_dt = max(since_dt, now - timedelta(days=_HOLD_MAX_DAYS))
    return {
        "on": True, "id": uuid.uuid4().hex, "status": "pending", "set_at": now.isoformat(),
        "by": (actor or {}).get("email") or "", "restore_on_at": restore_on_at or "",
        "backup_cutoff": cutoff or "", "window_since": since_dt.isoformat(), "attempts": 0,
        "reason": "Geri yükleme kapatıldı: iyzico mutabakatı tamamlanana kadar iadeler kilitli",
    }


async def ensure_refund_hold(actor: Optional[dict], *, restore_on_at: str = "") -> dict:
    """Kilit açık değilse (ya da başka bir geri yüklemeye aitse) açar; açık kilidi döndürür."""
    from routes.deps import db
    cur = await get_refund_hold()
    if cur.get("on") and str(cur.get("restore_on_at") or "") == str(restore_on_at or ""):
        return cur
    hold = await _build_refund_hold(actor, restore_on_at=restore_on_at, previous=cur)
    await db.settings.update_one(
        {"id": _DOC_ID},
        {"$set": {f"switches.{REFUND_HOLD_KEY}": hold, "updated_at": hold["set_at"]},
         "$push": {"history": {"$each": [{"name": REFUND_HOLD_KEY, "on": True, "reason": hold["reason"],
                                          "by": hold["by"], "at": hold["set_at"]}], "$slice": -200}}},
        upsert=True)
    invalidate_cache()
    return hold


def schedule_post_restore_reconciliation(hold_id: Optional[str], background_tasks=None, *,
                                         actor: Optional[dict] = None, days: Optional[int] = None) -> str:
    """Mutabakatı İSTEĞİ BEKLETMEDEN başlatır: FastAPI BackgroundTasks varsa yanıt gönderildikten sonra,
    yoksa olay döngüsünde arka plan görevi olarak. Süreç bu arada yeniden başlarsa kilit AÇIK kalır
    (fail-closed) — süper admin /refund-hold/reconcile ile yeniden başlatır."""
    if background_tasks is not None:
        background_tasks.add_task(run_post_restore_reconciliation, hold_id, actor=actor, days=days)
        return "background_task"
    from security.events import fire_and_forget
    fire_and_forget(run_post_restore_reconciliation(hold_id, actor=actor, days=days))
    return "task"


async def resume_interrupted_post_restore_reconciliation() -> dict:
    """ZAMANLAYICI İŞİ (30 dk). Kilit açık ama mutabakat hiç başlamamış ("pending") ya da yarıda kalmış
    ("running" + 2 saatten eski — süreç yeniden başladı) ise mutabakatı yeniden başlatır. Başarısız
    ("failed") koşuya dokunmaz: o durum insan incelemesi ister (iyzico'yu tekrar tekrar yormaz)."""
    try:
        hold = await get_refund_hold()
        if not hold.get("on"):
            return {"resumed": False, "reason": "no_hold"}
        status = str(hold.get("status") or "")
        now = datetime.now(timezone.utc)
        ref = _parse_iso(hold.get("last_run_at")) or _parse_iso(hold.get("set_at")) or now
        age_min = (now - ref).total_seconds() / 60.0
        stuck = (status == "pending" and age_min >= 10) or (status == "running" and age_min >= 120)
        if not stuck:
            return {"resumed": False, "reason": status or "?"}
        res = await run_post_restore_reconciliation(str(hold.get("id") or "") or None)
        return {"resumed": True, "result": {k: res.get(k) for k in ("ok", "cleared", "skipped") if k in res}}
    except Exception as e:
        logger.warning("[refund_hold] yeniden başlatma hatası: %s", type(e).__name__)
        return {"resumed": False, "reason": "error"}


def _recon_problems(summary: dict) -> list:
    """Mutabakat özeti kilidi kaldırmaya YETER mi? Boş liste = evet. Fail-closed: emin değilsek hayır."""
    s = summary if isinstance(summary, dict) else {}
    out = []
    if not s.get("ok"):
        out.append(f"hata:{s.get('error') or 'bilinmiyor'}")
    if s.get("skipped"):
        out.append(f"atlandı:{s.get('skipped')}")
    if int(s.get("mismatches") or 0):
        out.append(f"uyuşmazlık:{int(s.get('mismatches'))}")
    if int(s.get("unverified") or 0):
        out.append(f"doğrulanamayan:{int(s.get('unverified'))}")
    if int(s.get("refund_check_unavailable") or 0):
        out.append(f"iade_bilgisi_yok:{int(s.get('refund_check_unavailable'))}")
    if s.get("truncated"):
        out.append("sonuç_kesildi")
    return out


async def run_post_restore_reconciliation(hold_id: Optional[str] = None, *, actor: Optional[dict] = None,
                                          days: Optional[int] = None) -> dict:
    """Kilit penceresi için iyzico mutabakatı; başarılıysa kilidi kaldırır. ASLA istisna fırlatmaz."""
    from routes.deps import db
    try:
        hold = await get_refund_hold()
        if not hold.get("on"):
            return {"ok": True, "skipped": "no_hold"}
        hid = str(hold.get("id") or "")
        if hold_id and hid != str(hold_id):
            return {"ok": True, "skipped": "hold_replaced"}
        now = datetime.now(timezone.utc)
        since = _parse_iso(hold.get("window_since")) or (now - timedelta(days=_HOLD_MIN_DAYS))
        need = max(_HOLD_MIN_DAYS, int(math.ceil((now - since).total_seconds() / 86400.0)))
        use_days = min(_HOLD_MAX_DAYS, max(need, int(days or 0)))
        _k = f"switches.{REFUND_HOLD_KEY}"
        await db.settings.update_one({"id": _DOC_ID, f"{_k}.id": hid},
                                     {"$set": {f"{_k}.status": "running", f"{_k}.last_run_at": now.isoformat()},
                                      "$inc": {f"{_k}.attempts": 1}})
        from security.reconcile_iyzico import run_iyzico_reconciliation
        summary = await run_iyzico_reconciliation(days=use_days, limit=_HOLD_RECON_LIMIT)
        problems = _recon_problems(summary)
        brief = {k: summary.get(k) for k in ("ok", "checked", "matched", "mismatches", "unverified",
                                              "refund_check_unavailable", "truncated", "skipped", "since")
                 if k in summary}
        if not problems:
            done_at = _now_iso()

            async def _perform():
                res = await db.settings.update_one(
                    {"id": _DOC_ID, f"{_k}.id": hid, f"{_k}.on": True},
                    {"$set": {f"{_k}.on": False, f"{_k}.status": "cleared", f"{_k}.cleared_at": done_at,
                              f"{_k}.last_run": brief, f"{_k}.last_problems": [], "updated_at": done_at},
                     "$push": {"history": {"$each": [{"name": REFUND_HOLD_KEY, "on": False,
                                                      "reason": f"iyzico mutabakatı temiz ({use_days} gün)",
                                                      "by": _SYSTEM_RECON_SOURCE, "at": done_at}],
                                           "$slice": -200}}})
                return res.modified_count
            from activity_audit import run_audited
            try:
                n = await run_audited(
                    db, action="security.refund_hold_clear", entity_type="security_switch",
                    entity_id=REFUND_HOLD_KEY, perform=_perform, before={"on": True}, after={"on": False},
                    current_user=actor, source=_SYSTEM_RECON_SOURCE,
                    metadata={"hold_id": hid, "days": use_days, **brief})
            except HTTPException:
                problems = ["denetim_kaydı_yazılamadı"]
            else:
                invalidate_cache()
                if n:
                    await emit_security_event(
                        "refund_hold_cleared", actor,
                        {"hold_id": hid, "days": use_days, "checked": brief.get("checked"), "fp": hid},
                        title="Geri yükleme sonrası iyzico mutabakatı temiz: iadeler yeniden açıldı")
                    return {"ok": True, "cleared": True, "hold_id": hid, "days": use_days, "summary": brief}
                return {"ok": True, "cleared": False, "skipped": "hold_changed", "summary": brief}
        await db.settings.update_one(
            {"id": _DOC_ID, f"{_k}.id": hid, f"{_k}.on": True},
            {"$set": {f"{_k}.status": "failed", f"{_k}.last_run": brief, f"{_k}.last_problems": problems}})
        await emit_security_event(
            "post_restore_reconciliation_failed", actor,
            {"hold_id": hid, "days": use_days, "problems": ", ".join(problems)[:300],
             "checked": brief.get("checked"), "fp": f"{hid}:{int(hold.get('attempts') or 0) + 1}"},
            title="Geri yükleme sonrası iyzico mutabakatı tamamlanamadı: iadeler KİLİTLİ kalıyor")
        logger.error("[refund_hold] mutabakat başarısız, kilit açık kalıyor: %s", problems)
        return {"ok": False, "cleared": False, "hold_id": hid, "days": use_days, "problems": problems,
                "summary": brief}
    except Exception as e:  # noqa: BLE001 — kilit açık kalır (fail-closed)
        logger.error("[refund_hold] mutabakat çalıştırılamadı: %s", type(e).__name__)
        try:
            await db.settings.update_one(
                {"id": _DOC_ID, f"switches.{REFUND_HOLD_KEY}.on": True},
                {"$set": {f"switches.{REFUND_HOLD_KEY}.status": "failed",
                          f"switches.{REFUND_HOLD_KEY}.last_problems": [f"istisna:{type(e).__name__}"]}})
        except Exception:
            pass
        await emit_security_event(
            "post_restore_reconciliation_failed", actor,
            {"hold_id": str(hold_id or ""), "problems": f"istisna:{type(e).__name__}", "fp": _now_iso()},
            title="Geri yükleme sonrası iyzico mutabakatı çalıştırılamadı: iadeler KİLİTLİ kalıyor")
        return {"ok": False, "cleared": False, "problems": [f"istisna:{type(e).__name__}"]}
