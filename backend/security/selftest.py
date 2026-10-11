"""
Canlı güvenlik öz-testi (production self-test).

Amaç: güvenlik önlemlerinin CANLI ortamda gerçekten çalıştığını varsayıma bırakmadan doğrulamak.
Yalnız SAYI / DURUM toplar — kişisel veri (e-posta, ad, telefon, TCKN, IBAN…) rapora ve loga
YAZILMAZ. Sonuç:
  • db.settings {id: "security_selftest"} → son rapor (Güvenlik Paneli okur)
  • integration_logs (service="security", event="selftest")
  • Güvenlik uyarısı e-postası (İşletme Kuralları security.alert_emails alıcılarına) — günde en
    fazla bir kez ya da bir kontrol BAŞARISIZ olduğunda.

Kontroller (her biri ok/warn/fail + kısa açıklama):
  1. Gizli R2 arşivi: erişim + yaz/oku/sil turu
  2. Denetim zinciri: artımlı doğrulama + arşive dışa aktarım
  3. Şifreli sırlar: settings/kullanıcı MFA alanlarındaki "v1:" değerlerinin TAMAMI çözülebiliyor mu
     (SECRETS_MASTER_KEY eklenince eski kayıtlar kırılmadı mı)
  4. Ortam değişkenleri: varlık kontrolü (değerler ASLA okunup raporlanmaz)
  5. Acil anahtarlar: okunabiliyor mu, hangileri açık
  6. Oturum dönemi (security epoch) okunabiliyor mu
  7. KVKK silme kayıtları arşive yazılabiliyor mu
  8. Veri dökümü: düz metin kalan dekont / vergi no / IBAN sayıları, R2'ye taşınmamış görsel sayısı
  9. Personel: aktif süper admin sayısı, MFA'sız aktif personel sayısı
 10. Uyarı e-postası teslimi (alıcı başına sonuç; adresler maskeli)
 …  Vitrin bütünlüğü (T19/ADM-08): 15 dk'lık izleyicinin son sonucu (sunulan index.html, CSP,
    JS paket özetleri, karantina eşleşmesi; panel kodunun karantinadaki bir sürümle aynı olması)
 …  Görsel dönüştürme izolasyonu (ADM-07): dönüşümler ayrı süreçte mi, çökme/süre aşımı sayıları
 …  iyzico mutabakat defteri (COM12): son başarılı defter çalışması < 36 saat, açık uyuşmazlık sayısı
 …  Alarm tatbikatı (SOC-01): son 35 günde 15 dk içinde onaylanmış tatbikat yoksa UYARI
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

_DOC_ID = "security_selftest"
_ENV_KEYS = ("SECRETS_MASTER_KEY", "INVOICE_LINK_SECRET", "KVKK_TOMBSTONE_SECRET", "CF_EDGE_SECRET",
             "RESEND_API_KEY", "R2_ARCHIVE_BUCKET", "JWT_SECRET")


def _mask_email(e: str) -> str:
    e = str(e or "")
    if "@" not in e:
        return "***"
    u, d = e.split("@", 1)
    return (u[:1] + "***@" + d) if u else "***@" + d


def _walk_enc(obj, out: list, depth: int = 0):
    if depth > 6:
        return
    if isinstance(obj, dict):
        for v in obj.values():
            _walk_enc(v, out, depth + 1)
    elif isinstance(obj, list):
        for v in obj[:200]:
            _walk_enc(v, out, depth + 1)
    elif isinstance(obj, str) and obj.startswith("v1:"):
        out.append(obj)


async def _check(name: str, coro):
    try:
        status, detail = await coro
    except Exception as e:  # noqa: BLE001
        status, detail = "fail", f"hata: {type(e).__name__}: {str(e)[:160]}"
    return {"name": name, "status": status, "detail": detail}


async def _c_archive():
    from services import r2_storage as r2
    if not r2.archive_enabled():
        return "fail", "R2_ARCHIVE_BUCKET tanımlı değil ya da genel depo ile aynı"
    h = await asyncio.to_thread(r2.archive_health)
    if not h.get("ok"):
        return "fail", f"depoya erişilemiyor: {str(h.get('error'))[:120]}"
    key = f"_selftest/{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}.txt"
    data = b"meluni-selftest"
    await asyncio.to_thread(r2.archive_put, key, data, "text/plain")
    got, _ = await asyncio.to_thread(r2.archive_get, key)
    try:
        await asyncio.to_thread(lambda: r2._get_client().delete_object(Bucket=r2.archive_bucket(), Key=key))
    except Exception:
        pass
    if got != data:
        return "fail", "yazılan test dosyası geri okunamadı"
    return "ok", f"'{r2.archive_bucket()}' yaz/oku/sil başarılı"


async def _c_audit(db):
    from activity_audit import verify_audit_chain, export_audit_to_archive
    v = await verify_audit_chain(full=False, db=db)
    if not v.get("ok"):
        return "fail", f"zincir bozuk: {len(v.get('problems') or [])} sorun"
    ex = await export_audit_to_archive()
    if ex and not ex.get("ok") and not ex.get("skipped"):
        return "fail", f"arşive aktarım başarısız: {str(ex.get('error'))[:120]}"
    st = await db.settings.find_one({"_id": "audit_chain"}) or await db.settings.find_one({"id": "audit_chain"}) or {}
    return "ok", (f"zincir sağlam (kontrol edilen: {v.get('checked', '?')}), son sıra "
                  f"{st.get('seq', '?')}, arşive aktarılan: {(ex or {}).get('exported', (ex or {}).get('records', 0))}")


async def _c_secrets(db):
    from security.crypto import decrypt
    vals: list = []
    async for d in db.settings.find({}, {"_id": 0}).limit(500):
        _walk_enc(d, vals)
    async for u in db.users.find({"is_admin": True}, {"_id": 0, "mfa_secret_enc": 1, "mfa_phone_enc": 1}).limit(500):
        _walk_enc(u, vals)
    bad = 0
    for v in vals:
        try:
            if decrypt(v) is None:
                bad += 1
        except Exception:
            bad += 1
    if bad:
        return "fail", f"{bad}/{len(vals)} şifreli değer ÇÖZÜLEMİYOR (anahtar değişmiş olabilir)"
    has_master = bool((os.environ.get("SECRETS_MASTER_KEY") or "").strip())
    return ("ok" if has_master else "warn"), (
        f"{len(vals)} şifreli değerin tamamı çözülüyor; "
        + ("SECRETS_MASTER_KEY tanımlı" if has_master else "SECRETS_MASTER_KEY YOK (JWT'den türetiliyor)"))


async def _c_env():
    missing = [k for k in _ENV_KEYS if not (os.environ.get(k) or "").strip()]
    weak = [k for k in ("INVOICE_LINK_SECRET", "KVKK_TOMBSTONE_SECRET")
            if (os.environ.get(k) or "").strip() and len((os.environ.get(k) or "").strip()) < 32]
    if missing or weak:
        return "warn", ("eksik: " + (", ".join(missing) or "-") + ("; 32 karakterden kısa: " + ", ".join(weak) if weak else ""))
    return "ok", "gerekli güvenlik değişkenlerinin tamamı tanımlı"


async def _c_switches():
    import security_switches as ss
    on = []
    for name in ss.SWITCHES:
        if await ss.switch_on(name):
            on.append(name)
    return ("warn" if on else "ok"), ("AÇIK anahtarlar: " + ", ".join(on)) if on else "tüm acil anahtarlar kapalı"


async def _c_epoch():
    from routes.deps import get_security_epoch
    ep, _ = await get_security_epoch()
    return "ok", f"oturum dönemi okunuyor (değer {ep})"


async def _c_tombstone_archive():
    from services import r2_storage as r2
    if not r2.archive_enabled():
        return "fail", "arşiv kapalı — KVKK silme kayıtları yedeklenemez"
    key = "kvkk-tombstones/_selftest.json"
    await asyncio.to_thread(r2.archive_put, key, b"{}", "application/json")
    got, _ = await asyncio.to_thread(r2.archive_get, key)
    return ("ok" if got == b"{}" else "fail"), ("silme kaydı arşivi yazılabiliyor" if got == b"{}" else "silme kaydı arşivi okunamadı")


async def _c_data(db):
    cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    rc_plain = await db.orders.count_documents({"payment_receipt.data_b64": {"$exists": True, "$nin": [None, ""]}})
    rc_old = await db.orders.count_documents({"$or": [{"payment_receipt.data_b64": {"$exists": True, "$nin": [None, ""]}},
                                                       {"payment_receipt.data_enc": {"$exists": True, "$nin": [None, ""]}}],
                                              "payment_receipt.uploaded_at": {"$lt": cutoff}})
    iban_plain = await db.orders.count_documents({"refund_bank_info.iban": {"$exists": True, "$nin": [None, ""]}})
    tax_plain = 0
    for f in ("billing_info.tax_number", "billing_address.tax_number", "billing_info.tckn", "billing_address.tckn",
              "billing_info.tax_no", "billing_address.tax_no"):
        tax_plain += await db.orders.count_documents({f: {"$exists": True, "$nin": [None, ""]}})
    img_db = await db.files.count_documents({"data_b64": {"$exists": True, "$nin": [None, ""]},
                                             "content_type": {"$regex": "^(image|video)/"}, "is_deleted": {"$ne": True}})
    parts = [f"düz metin dekont: {rc_plain}", f"30 günden eski arşivlenmemiş dekont: {rc_old}",
             f"düz metin iade IBAN: {iban_plain}", f"düz metin vergi/TC no alanı: {tax_plain}",
             f"veritabanında kalan görsel/video: {img_db}"]
    bad = rc_old > 0 or iban_plain > 0 or tax_plain > 0
    return ("warn" if bad else "ok"), "; ".join(parts)


async def _c_staff(db):
    q = {"is_admin": True, "is_active": {"$ne": False}}
    supers = await db.users.count_documents({**q, "is_super_admin": True})
    no_mfa = await db.users.count_documents({**q, "mfa_enabled": {"$ne": True},
                                             "$or": [{"is_super_admin": True}, {"role_id": {"$nin": [None, ""]}}]})
    st = "ok" if supers >= 1 else "fail"
    return st, f"aktif süper admin: {supers}; MFA'sı açık olmayan aktif personel: {no_mfa} (girişte SMS/e-posta kodu yine zorunlu)"


# ─────────────────── SOC-01 / SOC-02: güvenlik kayıt/uyarı hattı kesinti izlemesi ───────────────────
# Zamanlayıcıdan bağımsız kontrol: 5 dakikada bir çalıştırılmak üzere (scheduler kaydı:
#   _add(run_security_pipeline_check, "interval", minutes=5, id="security_pipeline_check",
#        max_instances=1, coalesce=True, replace_existing=True)
# ) ve günlük öz-testin bir kontrolü olarak. Alarm koşulları:
#   1. Denetim arşivi gecikmesi: arşive aktarılmamış en eski zincir kaydı > 30 dk (ya da arşiv kapalı);
#   2. Uyarı teslim hatası: son 1 saatte HİÇBİR kanaldan teslim edilemeyen güvenlik uyarısı;
#   3. Denetim zinciri boşluğu/bozulması: artımlı doğrulamada sorun ya da yeni "yazılamadı" boşluğu.
# Sorun varsa pencere başına (30 dk) BİR kritik uyarı (security_pipeline_degraded) + ERROR log.
PIPELINE_EXPORT_LAG_MIN = 30
PIPELINE_ALERT_WINDOW_MIN = 60
_PIPELINE_DOC_ID = "security_pipeline"


def _parse_iso(v):
    try:
        dt = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


async def check_security_pipeline(*, alert: bool = True, db=None) -> dict:
    """Hattı denetler; {ok, problems:[{type, detail}], checked_at} döner. Asla istisna fırlatmaz."""
    if db is None:
        from routes.deps import db as _db
        db = _db
    now = datetime.now(timezone.utc)
    problems: list = []
    # 1) Denetim arşivi gecikmesi
    try:
        from services import r2_storage as r2
        chain = await db.settings.find_one({"_id": "audit_chain"}) or {}
        head = int(chain.get("seq") or 0)
        exported = int(chain.get("exported_seq") or 0)
        if head > exported:
            first = await db.audit_logs.find_one({"chain_seq": {"$gt": exported}}, {"_id": 0, "created_at": 1},
                                                 sort=[("chain_seq", 1)])
            oldest = _parse_iso((first or {}).get("created_at"))
            if not r2.archive_enabled():
                problems.append({"type": "audit_archive_disabled",
                                 "detail": f"gizli arşiv kapalı; {head - exported} kayıt arşive aktarılamıyor"})
            elif oldest and (now - oldest) > timedelta(minutes=PIPELINE_EXPORT_LAG_MIN):
                lag = int((now - oldest).total_seconds() // 60)
                problems.append({"type": "audit_export_lag",
                                 "detail": f"{head - exported} kayıt {lag} dakikadır arşive aktarılmadı "
                                           f"(son hata: {chain.get('last_export_error') or '-'})"})
    except Exception as e:  # noqa: BLE001
        problems.append({"type": "audit_export_check_failed", "detail": type(e).__name__})
    # 2) Uyarı teslim hatası (son 1 saat)
    try:
        since = (now - timedelta(minutes=PIPELINE_ALERT_WINDOW_MIN)).isoformat()
        _q_fail = {"created_at": {"$gte": since},
                   "delivery_error": {"$exists": True, "$nin": [None, ""]},
                   "kind": {"$ne": "security_pipeline_degraded"}}
        n_fail = await db.alerts.count_documents(_q_fail)
        if n_fail:
            # Hangi uyarı, neden? (alıcı adresi/kişisel veri YOK — yalnız tür ve sağlayıcı hata kodu)
            _ex = await db.alerts.find(_q_fail, {"_id": 0, "kind": 1, "delivery_error": 1}).limit(3).to_list(3)
            _why = "; ".join(f"{e.get('kind')}: "
                             + re.sub(r"[^\s@:;,'\"]+@[^\s@:;,'\"]+", "[e-posta]", str(e.get('delivery_error') or ''))[:80]
                             for e in _ex)
            problems.append({"type": "alert_delivery_failed",
                             "detail": f"son 1 saatte {n_fail} güvenlik uyarısı hiçbir kanaldan teslim edilemedi"
                                       + (f" ({_why})" if _why else "")})
    except Exception as e:  # noqa: BLE001
        problems.append({"type": "alert_check_failed", "detail": type(e).__name__})
    # 3) Denetim zinciri: artımlı doğrulama + yeni boşluk
    prev = {}
    try:
        prev = await db.settings.find_one({"id": _PIPELINE_DOC_ID}, {"_id": 0}) or {}
        from security.audit_chain import verify_chain
        v = await verify_chain(db, full=False)
        if not v.get("ok"):
            first = (v.get("problems") or [{}])[0]
            problems.append({"type": "audit_chain_problem",
                             "detail": f"{len(v.get('problems') or [])} sorun (ilk: sıra {first.get('seq')} "
                                       f"{first.get('type')})"})
        chain = await db.settings.find_one({"_id": "audit_chain"}, {"gaps": 1}) or {}
        n_gaps = len(chain.get("gaps") or [])
        if n_gaps > int(prev.get("known_gaps") or 0):
            problems.append({"type": "audit_chain_gap",
                             "detail": f"{n_gaps - int(prev.get('known_gaps') or 0)} yeni denetim kaydı yazılamadı "
                                       "(zincir boşluğu)"})
    except Exception as e:  # noqa: BLE001
        n_gaps = int(prev.get("known_gaps") or 0)
        problems.append({"type": "audit_chain_check_failed", "detail": type(e).__name__})
    res = {"ok": not problems, "problems": problems, "checked_at": now.isoformat()}
    try:
        await db.settings.update_one({"id": _PIPELINE_DOC_ID},
                                     {"$set": {"id": _PIPELINE_DOC_ID, "last": res, "known_gaps": n_gaps,
                                               "checked_at": res["checked_at"]}}, upsert=True)
    except Exception:
        pass
    if problems:
        logger.error("[security_pipeline] kesinti: %s", "; ".join(p["type"] for p in problems))
        if alert:
            try:
                from security.events import security_event
                bucket = int(now.timestamp() // 1800)
                await security_event(
                    "security_pipeline_degraded", None,
                    {"problems": ", ".join(p["type"] for p in problems),
                     **{f"detail_{i + 1}": p["detail"] for i, p in enumerate(problems[:5])},
                     "fp": f"{'|'.join(sorted(p['type'] for p in problems))}:{bucket}"},
                    body=("Güvenlik denetim/uyarı hattında kesinti tespit edildi. Güvenlik Paneli → Denetim Zinciri "
                          "ve Railway loglarını kontrol edin; arşiv (R2) ve e-posta sağlayıcısı erişimini doğrulayın."))
            except Exception:
                pass
    return res


async def run_security_pipeline_check() -> dict:
    """Zamanlayıcı girişi (5 dk)."""
    try:
        return await check_security_pipeline(alert=True)
    except Exception as e:  # noqa: BLE001
        logger.error("[security_pipeline] kontrol çalışmadı: %s", type(e).__name__)
        return {"ok": False, "problems": [{"type": "check_crashed", "detail": type(e).__name__}]}


async def _c_pipeline(db):
    r = await check_security_pipeline(alert=False, db=db)
    if r.get("ok"):
        return "ok", "denetim arşivi güncel, uyarılar teslim ediliyor, zincirde yeni boşluk yok"
    return "fail", "; ".join(p["detail"] for p in r.get("problems") or [])[:400]


# ─────────────── CHAIN08 / CHAIN10: restore sonrası arşiv referans mutabakatı ───────────────
# Veritabanı eski bir yedekten dönerse kayıtlar R2'deki gizli arşiv nesnelerine (dekont, dekont
# sürümü, manuel fatura PDF'i, KVKK mezar taşı) işaret etmeye devam eder; nesne yoksa belge
# açılamaz / silme yeniden uygulanamaz. Her türden örnek alınır (toplam en çok 50) ve nesnenin
# arşivde VAR olduğu doğrulanır. Eksikler raporlanır (anahtar yolu kişisel veri içermez).
ARCHIVE_REF_SAMPLE = 50


async def _sample(coll, match: dict, proj: dict, n: int) -> list:
    try:
        return await coll.aggregate([{"$match": match}, {"$sample": {"size": n}}, {"$project": proj}]).to_list(n)
    except Exception:  # noqa: BLE001 — $sample desteklenmiyorsa ilk n kayıt
        return await coll.find(match, proj).limit(n).to_list(n)


def _archive_has(key: str) -> bool:
    from services import r2_storage as r2
    client = r2._get_client()
    head = getattr(client, "head_object", None)
    if callable(head):
        try:
            head(Bucket=r2.archive_bucket(), Key=key.lstrip("/"))
            return True
        except Exception as e:  # noqa: BLE001
            code = str(getattr(e, "response", {}).get("Error", {}).get("Code", "")) if hasattr(e, "response") else ""
            if code in ("404", "NoSuchKey", "NotFound"):
                return False
    data, _ = r2.archive_get(key)
    return data is not None


async def collect_archive_refs(db, limit: int = ARCHIVE_REF_SAMPLE) -> list:
    """[(tür, anahtar)] — her türden örnek (toplam ≤ limit)."""
    per = max(1, limit // 5)
    refs: list = []
    for o in await _sample(db.orders, {"payment_receipt.archive_key": {"$nin": [None, ""]}},
                           {"_id": 0, "payment_receipt.archive_key": 1}, per):
        refs.append(("dekont", ((o.get("payment_receipt") or {}).get("archive_key"))))
    for v in await _sample(db.payment_receipt_versions, {"receipt.archive_key": {"$nin": [None, ""]}},
                           {"_id": 0, "receipt.archive_key": 1}, per):
        refs.append(("dekont_surumu", ((v.get("receipt") or {}).get("archive_key"))))
    for f in await _sample(db.files, {"archive_key": {"$nin": [None, ""]}}, {"_id": 0, "archive_key": 1}, per):
        refs.append(("dosya", f.get("archive_key")))
    for o in await _sample(db.orders, {"invoice_manual_file.archive_key": {"$nin": [None, ""]}},
                           {"_id": 0, "invoice_manual_file.archive_key": 1}, per):
        refs.append(("manuel_fatura", ((o.get("invoice_manual_file") or {}).get("archive_key"))))
    for t in await _sample(db.deletion_tombstones, {"id": {"$nin": [None, ""]}}, {"_id": 0, "id": 1}, per):
        refs.append(("kvkk_mezar_tasi", f"kvkk-tombstones/{t.get('id')}.json"))
    return [(k, str(v)) for k, v in refs if v][:limit]


async def check_archive_references(db, limit: int = ARCHIVE_REF_SAMPLE) -> dict:
    from services import r2_storage as r2
    if not r2.archive_enabled():
        return {"enabled": False, "checked": 0, "missing": []}
    refs = await collect_archive_refs(db, limit)
    missing = []
    for kind, key in refs:
        try:
            ok = await asyncio.to_thread(_archive_has, key)
        except Exception:  # noqa: BLE001
            ok = False
        if not ok:
            missing.append({"type": kind, "key": key[:160]})
    return {"enabled": True, "checked": len(refs), "missing": missing}


async def _c_archive_refs(db):
    r = await check_archive_references(db)
    if not r["enabled"]:
        return "warn", "gizli arşiv kapalı — arşiv referansları doğrulanamadı"
    if r["missing"]:
        by = {}
        for m in r["missing"]:
            by[m["type"]] = by.get(m["type"], 0) + 1
        try:
            from routes.integrations_common import log_integration_event
            await log_integration_event("security", "archive_reference_missing", "system", "selftest", "error",
                                        f"{len(r['missing'])}/{r['checked']} arşiv referansı R2'de yok",
                                        {"missing": r["missing"][:50]})
        except Exception:
            pass
        return "fail", (f"{len(r['missing'])}/{r['checked']} örnek arşiv referansının nesnesi YOK ("
                        + ", ".join(f"{k}: {v}" for k, v in sorted(by.items())) + ") — restore/arşiv uyuşmazlığı; "
                        "ayrıntı Entegrasyon Logları (archive_reference_missing)")
    return "ok", f"{r['checked']} örnek arşiv referansının nesnesi arşivde mevcut"


async def _c_script_manifest(db):
    from routes.custom_theme import script_manifest_drift
    r = await script_manifest_drift()
    if not r.get("initialized"):
        return "warn", f"script host manifesti henüz oluşmadı (vitrinde {len(r.get('hosts') or [])} host)"
    if r.get("unknown"):
        return "fail", ("vitrinde manifest DIŞI script host'u: " + ", ".join(r["unknown"][:10])
                        + " — API dışı (doğrudan veritabanı) kod değişikliği olabilir")
    return "ok", f"vitrindeki {len(r.get('hosts') or [])} script host'unun tamamı manifestte"


async def _c_audit_outbox(db):
    from security.audit_outbox import outbox_status
    st = await outbox_status(db)
    if st.get("local_dropped_total"):
        return "fail", f"yerel denetim outbox tavanı doldu, {st['local_dropped_total']} kayıt düştü"
    pend, local = st.get("pending_db") or 0, st.get("local_backlog_bytes") or 0
    if pend or local:
        return "warn", f"zincire aktarılmayı bekleyen denetim kaydı: {pend} (yerel {local} bayt)"
    return "ok", "bekleyen denetim outbox kaydı yok"


async def _c_suppression_archive(db):
    st = await db.settings.find_one({"id": "kvkk_suppression_archive"}, {"_id": 0}) or {}
    last = str(st.get("last_at") or "")
    if not last:
        return "warn", "pazarlama ret listesi henüz arşivlenmedi (gece işi)"
    if not st.get("last_ok") or last < (datetime.now(timezone.utc) - timedelta(hours=36)).isoformat():
        return "warn", f"pazarlama ret listesi arşivi güncel değil/doğrulanamadı (son: {last[:16]})"
    return "ok", f"pazarlama ret listesi arşivde ({st.get('last_count', 0)} kayıt, {last[:16]})"


async def _c_integration_freshness(db):
    st = await db.settings.find_one({"id": "integration_freshness_state"}, {"_id": 0}) or {}
    stale = sorted(k for k, v in (st.get("sources") or {}).items() if (v or {}).get("stale"))
    if not st:
        return "warn", "entegrasyon tazelik kontrolü henüz çalışmadı"
    if stale:
        return "warn", "bayat entegrasyon: " + ", ".join(stale)
    return "ok", "izlenen entegrasyonların tamamı zamanında senkronize"


async def _c_storefront_integrity(db):
    from security.storefront_integrity import selftest_status
    return await selftest_status(db)


async def _c_payment_ledger(db):
    from security.payment_ledger import selftest_status
    return await selftest_status(db)


async def _c_image_isolation(db):
    from image_worker import isolation_status
    st = isolation_status()
    if st.get("mode") == "disabled":
        return "warn", "görsel dönüştürme süreç izolasyonu ortam değişkeniyle KAPALI (IMAGE_PROCESS_ISOLATION)"
    if st.get("mode") != "process":
        return "fail", f"görsel dönüştürme süreç izolasyonu kurulamadı, iş parçacığına düşüldü: {st.get('last_error') or '?'}"
    return "ok", (f"dönüşümler ayrı süreçte ({st.get('workers')} işçi; başlatılan süreç {st.get('spawned', 0)}, "
                  f"iş {st.get('jobs', 0)}, çökme {st.get('crashes', 0)}, süre aşımıyla öldürülen "
                  f"{st.get('timeouts_killed', 0)})")


async def _c_alert_drill(db):
    """SOC-01: son 35 günde ≤ 15 dk içinde onaylanmış (geçti) bir alarm tatbikatı var mı."""
    from security.alert_ack import selftest_drill_status
    return await selftest_drill_status(db)


class _AlreadyAlertedToday(Exception):
    """Aynı başarısız kontrol kümesi bugün zaten kritik uyarı/SMS olarak bildirildi."""


async def run_selftest(*, send_email: bool | None = None, actor: dict | None = None) -> dict:
    """Tüm kontrolleri çalıştırır, raporu kaydeder; gerekirse e-posta gönderir. Asla istisna fırlatmaz."""
    from routes.deps import db
    started = datetime.now(timezone.utc)
    checks = []
    for name, coro in (
        ("Gizli R2 arşivi", _c_archive()),
        ("Denetim zinciri ve arşivi", _c_audit(db)),
        ("Şifreli sırlar çözülebiliyor", _c_secrets(db)),
        ("Güvenlik ortam değişkenleri", _c_env()),
        ("Acil anahtarlar", _c_switches()),
        ("Oturum dönemi", _c_epoch()),
        ("KVKK silme kaydı arşivi", _c_tombstone_archive()),
        ("Veri koruma durumu", _c_data(db)),
        ("Personel / süper admin", _c_staff(db)),
        ("Güvenlik kayıt/uyarı hattı", _c_pipeline(db)),
        ("Arşiv referansları (restore mutabakatı)", _c_archive_refs(db)),
        ("Vitrin script host manifesti", _c_script_manifest(db)),
        ("Denetim outbox'ı", _c_audit_outbox(db)),
        ("Pazarlama ret listesi arşivi", _c_suppression_archive(db)),
        ("Entegrasyon tazeliği", _c_integration_freshness(db)),
        ("Vitrin bütünlüğü", _c_storefront_integrity(db)),
        ("Görsel dönüştürme izolasyonu", _c_image_isolation(db)),
        ("iyzico mutabakat defteri", _c_payment_ledger(db)),
        ("Alarm tatbikatı", _c_alert_drill(db)),
    ):
        checks.append(await _check(name, coro))

    n_fail = sum(1 for c in checks if c["status"] == "fail")
    n_warn = sum(1 for c in checks if c["status"] == "warn")
    report = {"id": _DOC_ID, "at": started.isoformat(), "fail": n_fail, "warn": n_warn,
              "checks": checks, "version": (os.environ.get("RAILWAY_GIT_COMMIT_SHA") or "")[:12]}

    # E-posta: başarısızlıkta her zaman; aksi halde günde en fazla bir kez (ya da açıkça istenirse).
    prev = await db.settings.find_one({"id": _DOC_ID}, {"_id": 0, "last_email_at": 1, "last_email_ok": 1}) or {}
    last = str(prev.get("last_email_at") or "")
    # Son e-posta TESLİM EDİLEMEDİYSE (ya da hiç gitmediyse) bir sonraki çalışmada yeniden denenir.
    due = (not last) or prev.get("last_email_ok") is not True or last < (started - timedelta(hours=20)).isoformat()
    do_mail = send_email if send_email is not None else (n_fail > 0 or due)
    delivery = []
    if do_mail:
        try:
            from security.alerts import send_alert
            lines = [f"[{'TAMAM' if c['status'] == 'ok' else ('UYARI' if c['status'] == 'warn' else 'HATA')}] "
                     f"{c['name']}: {c['detail']}" for c in checks]
            _failing = [c["name"] for c in checks if c["status"] == "fail"]
            _title = (f"Güvenlik öz-testi HATA: {', '.join(_failing)[:120]}" if _failing
                      else f"Güvenlik öz-testi: {n_fail} hata, {n_warn} uyarı")
            # Aynı başarısız kontrol kümesi için günde TEK kritik uyarı/SMS (her yayından sonra tekrarlamasın);
            # elle "Şimdi Çalıştır" (send_email=True) her zaman gönderir.
            _fp = (f"security_selftest:fail:{'|'.join(sorted(_failing))}:{started.strftime('%Y-%m-%d')}"
                   if (_failing and send_email is None) else f"security_selftest:{started.isoformat()}")
            if _failing and send_email is None and await db.alerts.find_one({"fingerprint": _fp}, {"_id": 1}):
                raise _AlreadyAlertedToday()
            res = await send_alert(
                "security_selftest",
                _title,
                "Canlı ortamda otomatik güvenlik kontrolü sonucu:\n\n" + "\n".join(lines)
                + "\n\nAyrıntı: Yönetim Paneli → Güvenlik Paneli.",
                level=("critical" if n_fail else ("warning" if n_warn else "info")),
                fingerprint=_fp)
            delivery = [{"to": _mask_email(r.get("to")), "ok": bool(r.get("ok")), "error": r.get("error") or "",
                         "via": ("resend" if r.get("resend") else ("smtp" if r.get("smtp") else
                                 ("magaza_maili" if r.get("store") else "-"))),
                         "warning": r.get("warning") or ""}
                        for r in (res.get("recipients") or [])]
            report["last_email_at"] = started.isoformat()
            report["last_email_ok"] = bool(delivery) and all(d.get("ok") for d in delivery)
        except _AlreadyAlertedToday:
            delivery = [{"to": "-", "ok": True, "error": "", "via": "-", "warning": "aynı hata bugün zaten bildirildi"}]
        except Exception as e:  # noqa: BLE001
            delivery = [{"to": "-", "ok": False, "error": f"{type(e).__name__}"}]
    report["email_delivery"] = delivery
    if do_mail:
        ok_n = sum(1 for d in delivery if d.get("ok"))
        checks.append({"name": "Uyarı e-postası teslimi",
                       "status": "ok" if delivery and ok_n == len(delivery) else "fail",
                       "detail": (f"{ok_n}/{len(delivery)} alıcıya teslim edildi: "
                                  + ", ".join(f"{d['to']}={('ok/' + d['via']) if d['ok'] else d['error'] or 'hata'}"
                                              + (f" (Resend: {d['warning'][:80]})" if d.get('warning') else "")
                                              for d in delivery))
                       if delivery else "alıcı bulunamadı"})
        report["fail"] = sum(1 for c in checks if c["status"] == "fail")
    elif last:
        report["last_email_at"] = last
        report["last_email_ok"] = prev.get("last_email_ok")

    try:
        await db.settings.update_one({"id": _DOC_ID}, {"$set": report}, upsert=True)
    except Exception:
        pass
    try:
        from routes.integrations_common import log_integration_event
        await log_integration_event("security", "selftest", "system", "selftest",
                                    "success" if report["fail"] == 0 else "error",
                                    f"Güvenlik öz-testi: {report['fail']} hata, {n_warn} uyarı — "
                                    + " | ".join(f"{c['name']}: {c['status']}" for c in checks))
    except Exception:
        pass
    logger.warning(f"[selftest] {report['fail']} hata, {n_warn} uyarı")
    return report


async def run_selftest_on_boot():
    """Açılıştan sonra bir kez (zamanlayıcı date işi). Her deploy'da çalışır; e-posta günde bir
    kez ya da hata varsa gider."""
    try:
        await run_selftest()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[selftest] çalışmadı: {e}")


# ─── Yönetim uçları (yalnız süper admin) ────────────────────────────────────────────────────
from fastapi import APIRouter, Depends  # noqa: E402

from routes.deps import require_super_admin  # noqa: E402

router = APIRouter(prefix="/admin/security/selftest", tags=["security-selftest"])


@router.get("")
async def get_selftest(current_user: dict = Depends(require_super_admin)):
    from routes.deps import db
    return await db.settings.find_one({"id": _DOC_ID}, {"_id": 0}) or {"checks": []}


@router.post("/run")
async def post_selftest_run(current_user: dict = Depends(require_super_admin)):
    return await run_selftest(send_email=True, actor=current_user)
