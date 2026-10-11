"""
custom_theme.py — Özel Tema Kodu (CSS/JS) — Ticimax benzeri, GÜVENLİ.

GÜVENLİK MODELİ:
  - Özel CSS: mağaza <head>'ine <style> olarak enjekte edilir. Kod çalıştırmaz.
  - Özel JS: SADECE mağaza (storefront) tarafında, sayfa sonunda çalışır. Admin
    panelinde ASLA yüklenmez → enjekte edilen kod admin JWT/oturumuna erişemez
    (en kritik saldırı yüzeyi kapatılır).
  - Düzenleme require_admin ister; her kayıt updated_by + updated_at ile denetlenir.
  - Boyut limiti (kötüye kullanım / DoS'a karşı).
  - JS varsayılan KAPALI; bilinçli açılması gerekir.
  - SÜRÜMLEME: her kayıttan ÖNCE mevcut sürüm `custom_theme_history`'e yazılır (son 20 tutulur);
    her kayıt `audit_logs`'a (kod GÖVDESİ değil, hash+uzunluk) denetim kaydı düşer.
  - Vitrin tarafı (CustomThemeInjector.jsx) özel JS'i ödeme/hesap/giriş sayfalarında çalıştırmaz.
"""
import hashlib
import json
import re
import time
import uuid
from typing import Iterable, Optional
from urllib.parse import urlsplit
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from datetime import datetime, timezone
from .deps import db, require_admin, require_super_admin, logger, limiter, require_permission
from activity_audit import record_admin_audit

admin_router = APIRouter(prefix="/admin/custom-theme", tags=["admin-custom-theme"])
public_router = APIRouter(prefix="/custom-theme", tags=["custom-theme"])

_MAX = 200_000  # ~200 KB üst sınır (css+js her biri)
_HISTORY_KEEP = 20


def _code_summary(doc: dict) -> dict:
    """Denetim kaydı için kodun kendisi yerine özeti (hash + uzunluk + bayrak)."""
    doc = doc or {}
    css = str(doc.get("custom_css") or "")
    js = str(doc.get("custom_js") or "")
    return {
        "css_sha256": hashlib.sha256(css.encode()).hexdigest()[:16] if css else "",
        "css_length": len(css),
        "js_sha256": hashlib.sha256(js.encode()).hexdigest()[:16] if js else "",
        "js_length": len(js),
        "js_enabled": bool(doc.get("js_enabled", False)),
    }


async def _snapshot_previous(prev: dict, replaced_by: str) -> None:
    """Önceki sürümü geçmişe yaz ve yalnız son _HISTORY_KEEP sürümü tut. Best-effort."""
    if not prev:
        return
    try:
        now_iso = datetime.now(timezone.utc).isoformat()
        await db.custom_theme_history.insert_one({
            "id": str(uuid.uuid4()),
            "custom_css": prev.get("custom_css", ""),
            "custom_js": prev.get("custom_js", ""),
            "js_enabled": bool(prev.get("js_enabled", False)),
            "updated_by": prev.get("updated_by", ""),
            "updated_at": prev.get("updated_at", ""),
            "archived_at": now_iso,
            "replaced_by": replaced_by,
        })
        old = await db.custom_theme_history.find({}, {"_id": 0, "id": 1}) \
            .sort("archived_at", -1).skip(_HISTORY_KEEP).to_list(length=1000)
        if old:
            await db.custom_theme_history.delete_many({"id": {"$in": [o["id"] for o in old]}})
    except Exception as e:  # noqa: BLE001 — sürümleme kaydı ana kaydı engellemesin
        logger.warning(f"[custom-theme] geçmiş sürüm yazılamadı: {e}")


@admin_router.get("")
async def get_custom_theme(current_user: dict = Depends(require_permission("settings.view"))):
    doc = await db.settings.find_one({"id": "custom_theme"}, {"_id": 0}) or {}
    return {
        "custom_css": doc.get("custom_css", ""),
        "custom_js": doc.get("custom_js", ""),
        "js_enabled": bool(doc.get("js_enabled", False)),
        "updated_by": doc.get("updated_by", ""),
        "updated_at": doc.get("updated_at", ""),
    }


@admin_router.get("/history")
async def list_custom_theme_history(current_user: dict = Depends(require_super_admin)):
    """Son 20 önceki sürüm (yeniden eskiye). Geri dönmek için içerik kopyalanıp kaydedilir."""
    rows = await db.custom_theme_history.find({}, {"_id": 0}) \
        .sort("archived_at", -1).to_list(length=_HISTORY_KEEP)
    return {"items": rows}


@admin_router.put("")
async def save_custom_theme(payload: dict, request: Request, current_user: dict = Depends(require_super_admin)):
    # GÜVENLİK (denetim 2026-09-29): özel JS vitrinde çalışır → yalnız süper-admin kaydedebilir.
    css = str(payload.get("custom_css") or "")
    js = str(payload.get("custom_js") or "")
    if len(css) > _MAX or len(js) > _MAX:
        raise HTTPException(status_code=400, detail=f"Kod çok büyük (azami {_MAX//1000} KB).")
    # GÜVENLİK: </script> ile erken kapanış (HTML kırma) engellenir; enjeksiyon anında da
    # kaçışlanır ama burada da temiz tutmak için basit koruma.
    js = js.replace("</script>", "<\\/script>")
    # ADM-08: karantinaya alınmış (zararlı işaretli) bir sürüm geri yüklenemez (409; liste okunamazsa 503).
    from security.storefront_integrity import assert_code_not_quarantined
    await assert_code_not_quarantined([css, js], source="custom_theme", actor=current_user, request=request)
    doc = {
        "id": "custom_theme",
        "custom_css": css,
        "custom_js": js,
        "js_enabled": bool(payload.get("js_enabled", False)),
        "updated_by": current_user.get("email") or current_user.get("id") or "",
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    prev = await db.settings.find_one({"id": "custom_theme"}, {"_id": 0}) or {}
    _prev_hosts = await storefront_script_hosts()
    await _snapshot_previous(prev, doc["updated_by"])
    await db.settings.update_one({"id": "custom_theme"}, {"$set": doc}, upsert=True)
    await record_admin_audit(
        db, action="custom_theme.update", entity_type="settings", entity_id="custom_theme",
        before=_code_summary(prev), after=_code_summary(doc),
        current_user=current_user, request=request,
    )
    # T19/ADM-08: vitrine ulaşan kod değiştiyse HER değişiklikte güvenlik uyarısı + host manifesti
    _b, _a = _code_summary(prev), _code_summary(doc)
    if _b != _a:
        await note_storefront_script_change(
            source="custom_theme", actor=current_user, request=request, before=_b, after=_a,
            prev_hosts=_prev_hosts)
    return {"success": True, "updated_at": doc["updated_at"]}


@public_router.get("")
async def public_custom_theme():
    """Mağaza tarafı okur. CSS her zaman; JS yalnız js_enabled ise döner."""
    doc = await db.settings.find_one({"id": "custom_theme"}, {"_id": 0}) or {}
    return {
        "custom_css": doc.get("custom_css", ""),
        "custom_js": doc.get("custom_js", "") if doc.get("js_enabled") else "",
        "js_enabled": bool(doc.get("js_enabled", False)),
    }


# ═══════════════════════════ T19 / ADM-08: SCRIPT HOST MANİFESTİ ═══════════════════════════
# Vitrinde çalışan kodun (özel tema JS/CSS + aktif pazarlama pikselleri) yüklediği dış host'lar
# çıkarılır ve `settings.script_host_manifest` içinde tutulur. Her kod değişikliğinde:
#   1) "vitrin kodu değişti" güvenlik olayı (denetim zinciri + süper admin e-postası),
#   2) manifestte OLMAYAN yeni host belirdiyse ayrıca "yeni script host'u" kritik uyarısı;
#      host manifeste eklenir (değişikliği yapan süper adminin onayıyla sayılır; uyarı yine gider).
# Doğrudan veritabanı değişikliği (API'yi atlayan) günlük öz-testte manifestle karşılaştırılır.
SCRIPT_MANIFEST_ID = "script_host_manifest"
# Kod içindeki ad alanı / belge adresleri (script yüklemez) — host sayılmaz.
_NON_LOADING_HOSTS = {"www.w3.org", "w3.org", "schema.org", "ogp.me"}
_HOST_RE = re.compile(r"""(?:https?:|wss?:)?//([a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)+)""",
                      re.I)


def extract_script_hosts(*codes: Optional[str]) -> set:
    """Kod parçalarındaki mutlak/protokol-göreli URL'lerin host'ları (küçük harf)."""
    out = set()
    for code in codes:
        for m in _HOST_RE.finditer(str(code or "")):
            h = m.group(1).lower().rstrip(".")
            if h and h not in _NON_LOADING_HOSTS:
                out.add(h)
    return out


async def storefront_script_hosts() -> set:
    """Şu an vitrine ulaşan kodun host kümesi (özel JS yalnız açıkken; CSS her zaman)."""
    hosts: set = set()
    try:
        ct = await db.settings.find_one({"id": "custom_theme"}, {"_id": 0}) or {}
        hosts |= extract_script_hosts(ct.get("custom_css"))
        if ct.get("js_enabled"):
            hosts |= extract_script_hosts(ct.get("custom_js"))
        async for p in db.marketing_pixels.find({"is_active": True},
                                                {"_id": 0, "head_snippet": 1, "body_snippet": 1}):
            hosts |= extract_script_hosts(p.get("head_snippet"), p.get("body_snippet"))
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[script-manifest] host listesi okunamadı: {type(e).__name__}")
    return hosts


async def script_manifest() -> Optional[dict]:
    return await db.settings.find_one({"id": SCRIPT_MANIFEST_ID}, {"_id": 0})


async def note_storefront_script_change(*, source: str, actor: Optional[dict], request=None,
                                        before: Optional[dict] = None, after: Optional[dict] = None,
                                        prev_hosts: Optional[Iterable[str]] = None,
                                        target: str = "") -> dict:
    """Vitrin kodu değişikliğini bildirir ve host manifestini günceller. Asla istisna fırlatmaz.
    Dönüş: {"new_hosts": [...], "hosts": [...]}."""
    result = {"new_hosts": [], "hosts": []}
    try:
        from security.events import security_event
        current = await storefront_script_hosts()
        result["hosts"] = sorted(current)
        man = await script_manifest()
        if man is None:
            # İlk kurulum: taban = değişiklikten ÖNCEKİ durum (öncekinde olmayan host yine "yeni").
            baseline = set(prev_hosts or [])
            await db.settings.update_one(
                {"id": SCRIPT_MANIFEST_ID},
                {"$setOnInsert": {"id": SCRIPT_MANIFEST_ID, "hosts": sorted(baseline),
                                  "created_at": datetime.now(timezone.utc).isoformat()}},
                upsert=True)
            man = {"hosts": sorted(baseline)}
        new_hosts = sorted(current - set(man.get("hosts") or []))
        result["new_hosts"] = new_hosts
        _fp = str((after or {}).get("js_sha256") or (after or {}).get("head_snippet_sha256") or "") + \
            str((after or {}).get("css_sha256") or (after or {}).get("body_snippet_sha256") or "")
        meta = {"source": source, "target": target or source, "fp": _fp[:40] or uuid.uuid4().hex[:8]}
        for k, v in (after or {}).items():
            if k.endswith("sha256") or k.endswith("length") or k in ("js_enabled", "is_active", "provider"):
                meta[f"yeni_{k}"] = v
        for k, v in (before or {}).items():
            if k.endswith("sha256"):
                meta[f"eski_{k}"] = v
        meta["hosts"] = ", ".join(result["hosts"])[:400]
        await security_event("storefront_script_changed", actor, meta, request=request,
                             title="Vitrinde çalışan kod (özel JS/CSS ya da piksel) değiştirildi",
                             body="Bu değişiklik müşterinin tarayıcısında (ödeme sayfaları dahil) çalışan kodu etkiler.")
        if new_hosts:
            await security_event("script_host_new", actor,
                                 {"source": source, "new_hosts": ", ".join(new_hosts)[:400],
                                  "fp": hashlib.sha256(",".join(new_hosts).encode()).hexdigest()[:16]},
                                 request=request,
                                 title="Vitrin koduna YENİ bir dış script adresi eklendi",
                                 body="Yeni host'u siz eklemediyseniz kodu hemen geri alın (Güvenlik Paneli).")
            await db.settings.update_one(
                {"id": SCRIPT_MANIFEST_ID},
                {"$addToSet": {"hosts": {"$each": new_hosts}},
                 "$set": {"updated_at": datetime.now(timezone.utc).isoformat(),
                          "updated_by": str((actor or {}).get("email") or (actor or {}).get("id") or "")}},
                upsert=True)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[script-manifest] değişiklik bildirilemedi: {type(e).__name__}")
    return result


async def script_manifest_drift() -> dict:
    """Öz-test için: vitrine ulaşan host'lar manifestte mi? {"unknown": [...], "hosts": [...]}"""
    current = await storefront_script_hosts()
    man = await script_manifest()
    if man is None:
        return {"initialized": False, "unknown": [], "hosts": sorted(current)}
    return {"initialized": True, "unknown": sorted(current - set(man.get("hosts") or [])),
            "hosts": sorted(current)}


# ═══════════════════════════ T19: CSP İHLAL RAPORU (report-uri) ═══════════════════════════
# Tarayıcı, Report-Only CSP ihlallerini buraya POST eder (frontend/public/_headers report-uri).
# Herkese açık, kimliksiz: gövde 16 KB ile sınırlı, IP başı oran sınırı + süreç başı küresel
# dakika tavanı. Kişisel veri SAKLANMAZ: IP/UA yazılmaz, belge adresinin yalnız yolu tutulur.
# Kayıt: `csp_violation_stats` — (gün, yönerge, engellenen host, kaynak host) başına sayaç.
CSP_REPORT_MAX_BYTES = 16 * 1024
CSP_REPORT_GLOBAL_PER_MIN = 600
_csp_window = {"minute": 0, "count": 0}


def _csp_host(v) -> str:
    s = str(v or "").strip()
    if not s:
        return ""
    if s in ("inline", "eval", "data", "blob", "self", "wasm-eval", "trusted-types-policy") or ":" not in s:
        return s[:40]
    try:
        p = urlsplit(s)
        return ((p.hostname or p.scheme or "")[:120]).lower()
    except Exception:
        return ""


def _csp_path(v) -> str:
    try:
        return (urlsplit(str(v or "")).path or "/")[:120]
    except Exception:
        return ""


def _csp_scheme(v) -> str:
    try:
        return (urlsplit(str(v or "").strip()).scheme or "")[:30].lower()
    except Exception:
        return ""


def _csp_entries(payload) -> list:
    """`application/csp-report` ({"csp-report": {...}}) ve Reporting API (liste) biçimleri."""
    out = []
    items = payload if isinstance(payload, list) else [payload]
    for it in items[:20]:
        if not isinstance(it, dict):
            continue
        body = it.get("csp-report") if isinstance(it.get("csp-report"), dict) else (
            it.get("body") if isinstance(it.get("body"), dict) else None)
        if body is None:
            continue
        directive = str(body.get("effective-directive") or body.get("effectiveDirective")
                        or body.get("violated-directive") or body.get("violatedDirective") or "").split(" ")[0]
        out.append({
            "directive": directive[:40] or "?",
            "blocked": _csp_host(body.get("blocked-uri") or body.get("blockedURL")),
            "source": _csp_host(body.get("source-file") or body.get("sourceFile")),
            "page": _csp_path(body.get("document-uri") or body.get("documentURL")),
            "disposition": str(body.get("disposition") or "report")[:10],
            "source_scheme": _csp_scheme(body.get("source-file") or body.get("sourceFile")),
        })
    return out


def _csp_global_ok() -> bool:
    m = int(time.time() // 60)
    if _csp_window["minute"] != m:
        _csp_window.update({"minute": m, "count": 0})
    _csp_window["count"] += 1
    return _csp_window["count"] <= CSP_REPORT_GLOBAL_PER_MIN


@public_router.post("/csp-report", status_code=204)
@limiter.limit("60/minute")
async def csp_report(request: Request):
    """Herkese açık CSP ihlal raporu alıcısı (tarayıcı report-uri). Her durumda 204 döner."""
    try:
        cl = int(request.headers.get("content-length") or 0)
    except ValueError:
        cl = 0
    if cl > CSP_REPORT_MAX_BYTES:
        return Response(status_code=413)
    buf = bytearray()
    async for chunk in request.stream():
        buf.extend(chunk)
        if len(buf) > CSP_REPORT_MAX_BYTES:
            return Response(status_code=413)
    if not _csp_global_ok():
        return Response(status_code=204)
    try:
        payload = json.loads(bytes(buf).decode("utf-8") or "null")
    except Exception:
        return Response(status_code=204)
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    now = datetime.now(timezone.utc).isoformat()
    for e in _csp_entries(payload):
        # T19: vitrin artık ZORLAYICI politika + sıkı Report-Only politikası sunar → aynı ihlal iki
        # politikadan farklı anlamla gelir (engellendi vs yalnız ölçüm); sayaçlar ayrı tutulur.
        key = hashlib.sha256(f"{day}|{e['directive']}|{e['blocked']}|{e['source']}|{e['disposition']}"
                             .encode()).hexdigest()[:32]
        try:
            res = await db.csp_violation_stats.update_one(
                {"key": key},
                {"$inc": {"count": 1},
                 "$set": {"last_seen": now, "last_page": e["page"], "disposition": e["disposition"]},
                 "$setOnInsert": {"key": key, "day": day, "directive": e["directive"],
                                  "blocked_host": e["blocked"], "source_host": e["source"], "first_seen": now}},
                upsert=True)
        except Exception as ex:  # noqa: BLE001
            logger.warning(f"[csp-report] yazılamadı: {type(ex).__name__}")
            continue
        # Script yönergesinde, manifeste kayıtlı olmayan bir host İLK KEZ (o gün) görüldüyse uyar.
        # Tarayıcı EKLENTİSİNİN enjekte ettiği script (source-file chrome-extension:// vb.) bizim kodumuz
        # değildir → sayılır ama uyarılmaz. Aynı host zorlayıcı + Report-Only politikadan (ve farklı
        # kaynak dosyalardan) ayrı rapor üretir → e-posta host başına GÜNDE BİR (atomik talep).
        if getattr(res, "upserted_id", None) is not None and e["directive"].startswith("script-src") \
                and e["blocked"] and "." in e["blocked"] \
                and not e.get("source_scheme", "").endswith("-extension"):
            try:
                man = await script_manifest() or {}
                if e["blocked"] not in set(man.get("hosts") or []):
                    akey = hashlib.sha256(f"{day}|{e['blocked']}".encode()).hexdigest()[:32]
                    claim = await db.csp_alert_claims.update_one(
                        {"key": akey}, {"$setOnInsert": {"key": akey, "day": day, "blocked_host": e["blocked"],
                                                         "created_at": now}}, upsert=True)
                    if getattr(claim, "upserted_id", None) is not None:
                        from security.events import security_event
                        await security_event("csp_script_violation", None,
                                             {"directive": e["directive"], "blocked_host": e["blocked"],
                                              "source_host": e["source"], "disposition": e["disposition"],
                                              "page": e["page"], "fp": akey[:16]}, audit=False,
                                             title="Vitrinde CSP dışı script yükleme denemesi raporlandı")
            except Exception:
                pass
    return Response(status_code=204)


@admin_router.get("/csp-reports")
async def list_csp_reports(days: int = 7, current_user: dict = Depends(require_super_admin)):
    """Son N günün toplu CSP ihlal sayaçları (en çok görülen önce)."""
    from datetime import timedelta as _td
    since = (datetime.now(timezone.utc) - _td(days=max(1, min(int(days or 7), 90)))).strftime("%Y-%m-%d")
    rows = await db.csp_violation_stats.find({"day": {"$gte": since}}, {"_id": 0}) \
        .sort("count", -1).to_list(length=500)
    return {"items": rows, "manifest": (await script_manifest() or {}).get("hosts") or []}
