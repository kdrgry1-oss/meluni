"""
iys.py — Ticari elektronik ileti İZNİ (İYS) + OTP doğrulama.

Akış (KVKK / İYS uyumu):
  1) Müşteri ödeme adımında "kampanya/fırsat almak istiyorum" kutusunu işaretler.
  2) SMS izni için telefonuna OTP gönderilir (POST /iys/otp/send) → müşteri kodu girer
     (POST /iys/otp/verify). Böylece numaranın gerçekten müşteriye ait olduğu doğrulanır.
  3) Sipariş oluşturulurken izin KAYIT ALTINA alınır (db.iys_consents): alıcı, kanal(lar),
     kaynak (HS_WEB), tarih, IP. Denetim izi (audit trail) yasal zorunluluktur.
  4) İzin NetGSM İYS'ye DİJİTAL olarak bildirilir (best-effort, arka planda).

Çıkış (izin iptali) yolları — müşteriye gösterilecek:
  • https://iys.org.tr üzerinden "Vatandaş Girişi" ile tüm izinlerini görüp iptal edebilir.
  • Her ticari e-postadaki "abonelikten çık" bağlantısı / her SMS'teki "RET" yönergesiyle.
  • Hesabım > Bildirim Tercihleri'nden kapatabilir (işaretsiz → İYS'ye RED bildirilir).
"""
import os
import httpx
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, Request, HTTPException

from .deps import db, logger, generate_id, get_current_user, require_admin, limiter, safe_str, require_permission
from fastapi import Depends
from notification_service import (
    _get_providers_config, SMS_IMPL, _sms_generic, normalize_phone_tr,
)

router = APIRouter(prefix="/iys", tags=["iys-consent"])

_OTP_TTL_MIN = 5
_OTP_MAX_TRY = 5
# GÜVENLİK (denetim 2026-10, #10): yeni kod istemek deneme hakkını SIFIRLAMAZ — hedef numara başına
# 24 saatte toplam en çok bu kadar HATALI deneme (kod başına 5 sınırına ek).
_OTP_DAILY_MAX_FAILS = 15


def _hash_code(code: str) -> str:
    """İYS OTP kodu DB'de düz metin SAKLANMAZ (denetim 2026-10, #10)."""
    import hashlib
    return hashlib.sha256(f"iys:{str(code or '').strip()}".encode("utf-8")).hexdigest()


async def _daily_failed_tries(phone: str) -> int:
    """Numara başına son 24 saatteki HATALI deneme sayısı (başarılı doğrulama denemesi düşülür)."""
    since = (_now() - timedelta(hours=24)).isoformat()
    n = 0
    try:
        async for r in db.otp_verifications.find({"phone": phone, "created_at": {"$gte": since}},
                                                 {"_id": 0, "tries": 1, "verified_at": 1}):
            n += int(r.get("tries") or 0) - (1 if r.get("verified_at") else 0)
    except Exception:
        return 0
    return max(0, n)


def _now():
    return datetime.now(timezone.utc)


def _gen_code() -> str:
    """6 haneli OTP — Math.random/secrets kısıtlaması yok (backend)."""
    import secrets
    return f"{secrets.randbelow(1000000):06d}"


async def _send_sms(to: str, message: str) -> dict:
    """Aktif SMS sağlayıcısıyla tekil SMS gönderir (OTP için)."""
    cfg = await _get_providers_config(db)
    providers = (cfg or {}).get("providers", {}) or {}
    sms_active = (cfg or {}).get("sms_active")
    impl = SMS_IMPL.get(sms_active, _sms_generic)
    prov_cfg = providers.get(sms_active, {}) if sms_active else {}
    try:
        return await impl(prov_cfg, to, message)
    except Exception as e:
        logger.warning(f"[iys] OTP SMS gönderilemedi: {type(e).__name__}")
        return {"success": False, "error": str(e)}


@router.post("/otp/send")
@(limiter.limit("3/minute;20/day") if limiter else (lambda f: f))
async def otp_send(payload: dict, request: Request):
    """Telefona OTP gönderir. body: {phone}.
    GÜVENLİK: IP başına hız-sınırı (3/dk, 20/gün) + YALNIZ TR mobil (90 5XX…) + saatlik global
    devre kesici → SMS kredisi boşaltma / SMS bombardımanı engellenir (denetim SEC-1/SEC-6 F2/F4)."""
    phone = normalize_phone_tr(safe_str((payload or {}).get("phone") or "", 20))
    # Yalnız Türkiye mobil numaraları (90 + 10 hane, 905…) — yurtdışı/premium numaralarla drenaj engeli.
    if not (len(phone) == 12 and phone.startswith("905") and phone.isdigit()):
        raise HTTPException(status_code=400, detail="Geçerli bir Türkiye cep telefonu numarası giriniz.")
    # Global devre kesici: son 1 saatte çok fazla OTP → geçici olarak durdur (kredi koruması).
    try:
        _hourly = await db.otp_verifications.count_documents(
            {"created_at": {"$gte": (_now() - timedelta(hours=1)).isoformat()}})
        if _hourly > 500:
            logger.warning(f"[iys][otp] saatlik OTP eşiği aşıldı ({_hourly}) — geçici durduruldu")
            raise HTTPException(status_code=503, detail="Doğrulama servisi geçici olarak yoğun. Kısa süre sonra tekrar deneyin.")
    except HTTPException:
        raise
    except Exception:
        pass
    # 1 dk mükerrer koruması
    recent = await db.otp_verifications.find_one(
        {"phone": phone, "created_at": {"$gte": (_now() - timedelta(minutes=1)).isoformat()}},
        {"_id": 0, "created_at": 1},
    )
    if recent:
        raise HTTPException(status_code=429, detail="Çok sık deneme. Lütfen 1 dakika sonra tekrar deneyin.")
    # Numara başına 24 saatte en fazla 5 kod (yeniden istemek bütçeyi sıfırlamaz; SMS kredisi koruması).
    try:
        _sent24 = await db.otp_verifications.count_documents(
            {"phone": phone, "created_at": {"$gte": (_now() - timedelta(hours=24)).isoformat()}})
    except Exception:
        raise HTTPException(status_code=503, detail="Doğrulama servisi geçici olarak kullanılamıyor.")
    if _sent24 >= 5:
        raise HTTPException(status_code=429, detail="Bu numaraya bugün çok fazla kod gönderildi. Lütfen yarın tekrar deneyin.")
    if await _daily_failed_tries(phone) >= _OTP_DAILY_MAX_FAILS:
        raise HTTPException(status_code=429, detail="Bu numara için çok fazla hatalı deneme yapıldı. Lütfen yarın tekrar deneyin.")
    code = _gen_code()
    # Önceki kullanılmamış kodlar geçersiz — tek aktif kod (deneme bütçesi numara başına günlük sayılır).
    await db.otp_verifications.update_many({"phone": phone, "used": False},
                                           {"$set": {"used": True, "invalidated": True}})
    rec = {
        "id": generate_id(), "phone": phone, "code_hash": _hash_code(code),
        "expires_at": (_now() + timedelta(minutes=_OTP_TTL_MIN)).isoformat(),
        "tries": 0, "used": False, "created_at": _now().isoformat(),
    }
    await db.otp_verifications.insert_one(rec)
    # Marka adı company.py'den gelir (env/panel) — koda gömülmez.
    try:
        import company as _company
        _brand = (await _company.get_company(db)).get("store_name") or ""
    except Exception:
        _brand = ""
    _prefix = f"{_brand} " if _brand else ""
    msg = f"{_prefix}dogrulama kodunuz: {code} . Kod {_OTP_TTL_MIN} dakika gecerlidir."
    res = await _send_sms(phone, msg)
    if not res.get("success"):
        # Kod kaydı kalır; sağlayıcı hatasını müşteriye sade ver.
        return {"success": False, "detail": res.get("error") or "SMS gönderilemedi. Numaranızı kontrol edin."}
    return {"success": True, "ttl_min": _OTP_TTL_MIN}


@router.post("/otp/verify")
@(limiter.limit("10/minute") if limiter else (lambda f: f))
async def otp_verify(payload: dict, request: Request, current_user: dict = Depends(get_current_user)):
    """OTP doğrular. body: {phone, code}. Başarılıysa {verified:true}.
    GÜVENLİK (denetim 2026-10, #10): kod özetle saklanır ve sabit-zamanlı karşılaştırılır; deneme
    hakkı ATOMİK alınır; kod TEK KEZ tüketilir; numara başına günlük hatalı deneme sınırı vardır.
    Giriş yapmış üye KENDİ hesap numarasını doğrularsa hesap telefonu 'doğrulanmış' işaretlenir."""
    import hmac as _hmac
    phone = normalize_phone_tr(safe_str((payload or {}).get("phone") or "", 20))
    code = safe_str((payload or {}).get("code") or "", 12).strip()
    if not phone or not code:
        raise HTTPException(status_code=400, detail="Doğrulama kodu bulunamadı, yeniden gönderin.")
    if await _daily_failed_tries(phone) >= _OTP_DAILY_MAX_FAILS:
        raise HTTPException(status_code=429, detail="Bu numara için çok fazla hatalı deneme yapıldı. Lütfen yarın tekrar deneyin.")
    peek = await db.otp_verifications.find_one(
        {"phone": phone, "used": False}, {"_id": 0}, sort=[("created_at", -1)]
    )
    if not peek:
        raise HTTPException(status_code=400, detail="Doğrulama kodu bulunamadı, yeniden gönderin.")
    try:
        exp = datetime.fromisoformat(str(peek.get("expires_at")).replace("Z", "+00:00"))
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
    except Exception:
        exp = _now()
    if _now() > exp:
        raise HTTPException(status_code=400, detail="Kodun süresi doldu, yeniden gönderin.")
    rec = await db.otp_verifications.find_one_and_update(
        {"id": peek["id"], "used": False, "tries": {"$lt": _OTP_MAX_TRY}},
        {"$inc": {"tries": 1}}, projection={"_id": 0})
    if not rec:
        raise HTTPException(status_code=429, detail="Çok fazla hatalı deneme. Yeni kod isteyin.")
    if rec.get("code_hash"):
        ok = _hmac.compare_digest(_hash_code(code), str(rec.get("code_hash") or ""))
    else:  # geçiş: bu sürümden önce üretilmiş (≤5 dk) düz-metin kayıt
        ok = _hmac.compare_digest(code, str(rec.get("code") or ""))
    if not ok:
        raise HTTPException(status_code=400, detail="Kod hatalı.")
    res = await db.otp_verifications.update_one(
        {"id": rec["id"], "used": False},
        {"$set": {"used": True, "verified_at": _now().isoformat()}, "$unset": {"code": ""}})
    if getattr(res, "modified_count", 0) != 1:
        raise HTTPException(status_code=400, detail="Doğrulama kodu bulunamadı, yeniden gönderin.")
    # Giriş yapmış üye, hesabındaki numaranın sahibi olduğunu kanıtladı → phone_verified=True
    # (profilden değiştirilen numara SMS ile şifre sıfırlamada yeniden kullanılabilir hale gelir).
    try:
        if current_user and current_user.get("id") and current_user.get("phone_verified") is False:
            _acc = normalize_phone_tr(str(current_user.get("phone") or ""))
            if _acc and _acc[-10:] == phone[-10:]:
                # KVKK: telefon şifreli saklanabilir → "numara hâlâ bu mu" koşulu hash ile (yarış güvenli).
                from security.contact_pii import phone_lookup as _ph_lookup
                await db.users.update_one(
                    {"id": current_user["id"], **_ph_lookup("phone", current_user.get("phone"))},
                    {"$set": {"phone_verified": True, "phone_verified_at": _now().isoformat()}})
    except Exception:
        pass
    return {"verified": True, "phone": phone}


async def _phone_recently_verified(phone: str) -> bool:
    """create_order çağrısında SMS izni için OTP doğrulaması yapılmış mı (son 30 dk)."""
    p = normalize_phone_tr(str(phone or ""))
    if len(p) < 12:
        return False
    rec = await db.otp_verifications.find_one(
        {"phone": p, "used": True,
         "verified_at": {"$gte": (_now() - timedelta(minutes=30)).isoformat()}},
        {"_id": 0, "id": 1},
    )
    return bool(rec)


async def _iys_config() -> dict:
    """NetGSM İYS kimlik bilgileri — providers.netgsm bloğundan.

    ÖNEMLİ: NetGSM'de İYS için AYRI bir API alt kullanıcısı vardır (İYS modülü yalnızca o
    kullanıcıda aktiftir). Bu yüzden İYS gönderimi, varsa İYS ALT KULLANICISININ kendi
    şifre/appkey'ini kullanır (iys_password / iys_appkey); yoksa SMS bloğundaki değerlere
    düşer. username tüm alt kullanıcılarda ABONE numarasıdır (8503079456) — NetGSM panelindeki
    'Alt kullanıcı ile giriş: kullanıcı adı = abone no' bilgisiyle birebir. Böylece İYS'ye ayrı
    kimlik verilir, SMS OTP'nin şifre/appkey'i BOZULMAZ."""
    doc = await db.settings.find_one({"id": "notification_providers"}, {"_id": 0}) or {}
    from notification_service import decrypt_provider_block  # B-1: sırlar at-rest şifreli
    prov = decrypt_provider_block((doc.get("providers", {}) or {}).get("netgsm", {}) or {})
    return {
        "username": prov.get("username") or os.environ.get("NETGSM_USERCODE", ""),
        # İYS alt kullanıcısının şifre/appkey'i öncelikli; yoksa SMS'inkine düş.
        "password": (prov.get("iys_password") or prov.get("password")
                     or os.environ.get("NETGSM_IYS_PASSWORD") or os.environ.get("NETGSM_PASSWORD", "")),
        "appkey": (prov.get("iys_appkey") or prov.get("appkey")
                   or os.environ.get("NETGSM_IYS_APPKEY") or os.environ.get("NETGSM_APPKEY", "")),
        "iys_code": prov.get("iys_code") or os.environ.get("NETGSM_IYS_CODE", ""),
        "brand_code": (prov.get("iys_brand_code") or prov.get("brand_code")
                       or os.environ.get("NETGSM_IYS_BRAND_CODE", "")),
    }


def _netgsm_iys_ok(code, body: str) -> bool:
    """NetGSM İYS yanıtı başarılı mı? Başarılı yanıt: {"code":"0","error":"false","uid":"…"}.
    ESKİ HATA: gövdede '"error"' ANAHTARI geçtiği için başarılı yanıt da BAŞARISIZ sayılıyordu →
    hiçbir izin 'bildirildi' işaretlenmiyor, aynı kayıtlar 30 dk'da bir TEKRAR gönderiliyor ve
    NetGSM 'Sinir asimi' (103) ile reddediyordu."""
    if code != 200:
        return False
    import json as _json
    try:
        j = _json.loads(body or "")
    except Exception:
        j = None
    if isinstance(j, dict):
        c = str(j.get("code", "")).strip()
        err = str(j.get("error", "")).strip().lower()
        return c in ("0", "00") and err in ("", "false", "0", "none")
    lo = (body or "").lower().replace(" ", "")
    return ("success" in lo or "basarili" in lo) and "failure" not in lo and "hata" not in lo


def _iys_item(ch: str, consent: dict, appkey: str = ""):
    """Tek kanal için NetGSM İYS data öğesi (alıcı yoksa None)."""
    email = (consent.get("email") or "").strip()
    phone = (consent.get("phone") or "").strip()
    if phone and not phone.startswith("+"):
        digits = phone.lstrip("0")
        phone = "+" + (digits if digits.startswith("90") else "90" + digits)
    recipient = phone if ch == "MESAJ" else email
    if not recipient:
        return None
    item = {"type": ch, "source": consent.get("source", "HS_WEB") or "HS_WEB", "recipient": recipient,
            "status": consent.get("status", "ONAY") or "ONAY",
            "consentDate": (consent.get("consent_date") or consent.get("created_at") or _now().isoformat()).replace("T", " ")[:19],
            "recipientType": "BIREYSEL"}
    if appkey:
        item["appkey"] = appkey
    return item


async def _post_iys(cfg: dict, data: list):
    """NetGSM /iys/add'e TEK istekte birden çok kayıt gönderir → (ok, http_kodu, gövde)."""
    import base64 as _b64
    username = (cfg.get("username") or "").strip()
    password = (cfg.get("password") or "").strip()
    brand_code = (cfg.get("brand_code") or cfg.get("iys_code") or "").strip()
    payload = {"header": {"username": username, "password": password, "brandCode": brand_code},
               "body": {"data": data}}
    url = os.environ.get("NETGSM_IYS_URL") or "https://api.netgsm.com.tr/iys/add"
    hdrs = {"Content-Type": "application/json; charset=utf-8",
            "Authorization": "Basic " + _b64.b64encode(f"{username}:{password}".encode()).decode()}
    try:
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.post(url, json=payload, headers=hdrs)
        body = (r.text or "").strip()[:500]
        return _netgsm_iys_ok(r.status_code, body), r.status_code, body
    except Exception as e:
        return False, None, f"exception: {e}"[:500]


async def report_pending_batch(max_batches: int = 20, batch_size: int = 100) -> dict:
    """GÜNLÜK TOPLU İYS BİLDİRİMİ: bildirilmemiş tüm izin/RET kayıtları NetGSM'e toplu gider.
    Alıcı+kanal başına YALNIZ EN SON karar gönderilir (ONAY sonra RET → yalnız RET); aynı
    alıcının eski bekleyen kayıtları 'daha yeni kayıtla bildirildi' olarak kapanır.
    'Sinir asimi' (103) gelirse durur; kalanlar bir sonraki turda gönderilir."""
    import asyncio as _asyncio
    cfg = await _iys_config()
    if not ((cfg.get("username") or "").strip() and (cfg.get("password") or "").strip()
            and (cfg.get("brand_code") or cfg.get("iys_code") or "").strip()):
        return {"error": "NetGSM İYS kimlik/marka eksik", "sent": 0}
    appkey = (cfg.get("appkey") or "").strip()
    q = {"reported": {"$ne": True}, "report_skip": {"$exists": False}}
    recs = await db.iys_consents.find(q, {"_id": 0}).sort("created_at", 1).to_list(20000)
    latest: dict = {}      # (kanal, alıcı) -> (tarih, kayıt id, item)
    for r in recs:
        for ch in (r.get("channels") or []):
            it = _iys_item(ch, r, appkey)
            if not it:
                continue
            key = (ch, it["recipient"].lower())
            dt = r.get("consent_date") or r.get("created_at") or ""
            if key not in latest or dt >= latest[key][0]:
                latest[key] = (dt, r["id"], it)
    # Bildirilmiş (daha yeni) bir karar varsa bekleyen eski kaydı gönderme.
    winners = {}
    for key, (dt, rid, it) in latest.items():
        winners.setdefault(rid, []).append((key, it))
    now = _now().isoformat()
    no_item = [r["id"] for r in recs if not any(_iys_item(ch, r) for ch in (r.get("channels") or []))]
    if no_item:
        await db.iys_consents.update_many({"id": {"$in": no_item}},
                                          {"$set": {"report_skip": "alıcı/kanal yok", "reported_at": now}})
    items = [(rid, it) for rid, lst in winners.items() for _k, it in lst]
    sent_ids, batches, stop, last_body = set(), 0, None, ""
    for i in range(0, len(items), batch_size):
        if batches >= max_batches:
            stop = "tur sınırı"
            break
        chunk = items[i:i + batch_size]
        ok, code, body = await _post_iys(cfg, [it for _rid, it in chunk])
        batches += 1
        last_body = body
        ids = list({rid for rid, _it in chunk})
        if ok:
            uid = ""
            try:
                import json as _json
                uid = str((_json.loads(body) or {}).get("uid") or "")
            except Exception:
                pass
            await db.iys_consents.update_many({"id": {"$in": ids}}, {"$set": {
                "reported": True, "report_status_code": code, "report_response": body,
                "report_uid": uid, "reported_at": _now().isoformat()}})
            sent_ids.update(ids)
        else:
            await db.iys_consents.update_many({"id": {"$in": ids}}, {"$set": {
                "reported": False, "report_status_code": code, "report_response": body,
                "reported_at": _now().isoformat()}})
            stop = "sınır aşımı" if '"103"' in (body or "") else f"hata {code}"
            break
        await _asyncio.sleep(3)
    # Kazanan kaydı başarıyla bildirilen alıcıların ESKİ bekleyen kayıtları kapanır.
    if sent_ids:
        losers = [r["id"] for r in recs if r["id"] not in winners and r["id"] not in no_item]
        if losers:
            await db.iys_consents.update_many({"id": {"$in": losers}, "reported": {"$ne": True}}, {"$set": {
                "reported": True, "report_response": "daha yeni kayıtla bildirildi", "reported_at": now}})
    pending_left = await db.iys_consents.count_documents(q)
    return {"items": len(items), "batches": batches, "sent_records": len(sent_ids),
            "stopped": stop, "pending_left": pending_left, "last_response": (last_body or "")[:200]}


async def _report_to_netgsm_iys(consent: dict):
    """İzin/RED kaydını NetGSM İYS'ye (POST https://api.netgsm.com.tr/iys/add) bildirir.

    NetGSM, markanın İYS entegratörüdür — "NetGSM bizim adımıza yolluyor". İzinler SMS için
    kullanılan AYNI NetGSM kimlik bilgileriyle (usercode/password) NetGSM'in İYS ucuna POST edilir;
    NetGSM İYS'ye iletir. Ayrı resmî İYS API kullanıcısı GEREKMEZ. Marka kodu (brandCode) = İYS
    marka kodu (ör. 754607).

    Body (NetGSM resmî formatı):
      {"header": {"username","password","brandCode","appkey"?},
       "body": {"data": [{"type":"MESAJ|EPOSTA","source","recipient","status","consentDate","recipientType":"BIREYSEL"}]}}
    Kanal başına bir data satırı; en az biri başarılıysa reported=True."""
    cfg = await _iys_config()
    channels = consent.get("channels", []) or []
    if not channels:
        return False
    username = (cfg.get("username") or "").strip()
    password = (cfg.get("password") or "").strip()
    brand_code = (cfg.get("brand_code") or cfg.get("iys_code") or "").strip()
    if not (username and password and brand_code):
        await db.iys_consents.update_one(
            {"id": consent["id"]},
            {"$set": {"reported": False, "report_status_code": None,
                      "report_response": "NetGSM İYS kimlik/marka eksik (username/password/brandCode)",
                      "reported_at": _now().isoformat()}})
        logger.warning("[iys] NetGSM İYS kimlik/marka eksik — bildirim atlandı")
        return False
    email = (consent.get("email") or "").strip()
    phone = (consent.get("phone") or "").strip()
    # NetGSM İYS telefon biçimi: +90XXXXXXXXXX
    if phone and not phone.startswith("+"):
        digits = phone.lstrip("0")
        phone = "+" + (digits if digits.startswith("90") else "90" + digits)
    status = consent.get("status", "ONAY")
    source = consent.get("source", "HS_WEB")
    cd = (consent.get("consent_date") or _now().isoformat()).replace("T", " ")[:19]
    _appkey = (cfg.get("appkey") or "").strip()
    data = []
    for ch in channels:
        recipient = phone if ch == "MESAJ" else email
        if not recipient:
            continue
        item = {
            "type": ch, "source": source, "recipient": recipient,
            "status": status, "consentDate": cd, "recipientType": "BIREYSEL",
        }
        # NetGSM resmî n8n entegrasyonu: appkey (varsa) DATA öğesinde gönderilir.
        if _appkey:
            item["appkey"] = _appkey
        data.append(item)
    if not data:
        return False
    # Header: NetGSM resmî n8n koduyla birebir — {username, password, brandCode}.
    # (appkey header'da DEĞİL; robustluk için varsa data öğesine yukarıda eklendi.)
    header = {"username": username, "password": password, "brandCode": brand_code}
    payload = {"header": header, "body": {"data": data}}
    url = os.environ.get("NETGSM_IYS_URL") or "https://api.netgsm.com.tr/iys/add"
    # KRİTİK: NetGSM resmî n8n entegrasyonu İYS API'sini HTTP Basic Auth ile çağırır
    # (Authorization: Basic base64(user:pass)). Gövdedeki header'a EK olarak bu şart —
    # yoksa NetGSM 'iys modulunuzu aktiflestirin' (code 40) ile reddediyor.
    import base64 as _b64
    _auth = _b64.b64encode(f"{username}:{password}".encode()).decode()
    _hdrs = {"Content-Type": "application/json; charset=utf-8",
             "Authorization": "Basic " + _auth}
    ok, code, body = False, None, ""
    try:
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.post(url, json=payload, headers=_hdrs)
        code = r.status_code
        body = (r.text or "").strip()[:500]
        ok = _netgsm_iys_ok(code, body)
    except Exception as e:
        body = f"exception: {e}"[:500]
    await db.iys_consents.update_one(
        {"id": consent["id"]},
        {"$set": {"reported": ok, "report_status_code": code,
                  "report_response": body, "reported_at": _now().isoformat()}},
    )
    if not ok:
        logger.warning(f"[iys] NetGSM İYS bildirimi başarısız: code={code} body={body[:200]}")
    return ok


@router.post("/consent/update")
async def consent_update(payload: dict, current_user: dict = Depends(get_current_user)):
    """Üye Hesabım > Pazarlama Tercihleri: e-posta/SMS iznini AÇ/KAPA. Kapatınca İYS'ye RED
    (RET), açınca ONAY bildirilir. Denetim izi + dijital bildirim record_consent ile yapılır."""
    if not current_user:
        raise HTTPException(status_code=401, detail="Giriş yapmanız gerekiyor")
    email_on = bool((payload or {}).get("email"))
    sms_on = bool((payload or {}).get("sms"))
    email = current_user.get("email") or ""
    phone = current_user.get("phone") or ""
    if email:
        await record_consent(email, phone, ["EPOSTA"],
                             status="ONAY" if email_on else "RET",
                             source="HS_WEB", user_id=current_user.get("id"))
    if phone:
        await record_consent(email, phone, ["MESAJ"],
                             status="ONAY" if sms_on else "RET",
                             source="HS_WEB", user_id=current_user.get("id"))
    await db.users.update_one(
        {"id": current_user.get("id")},
        # accepts_marketing = profil E-POSTA izni (consents._collect / kampanya kitlesi bunu e-posta
        # ONAY'ı sayar). Eskiden e-posta kapalı + SMS açıkken True yazılıyor ve e-posta RET'ini eziyordu.
        {"$set": {"accepts_marketing": bool(email_on),
                  "marketing_prefs": {"email": email_on, "sms": sms_on},
                  "marketing_consent_at": _now().isoformat()}})
    return {"success": True, "email": email_on, "sms": sms_on}


async def record_consent(recipient_email: str, recipient_phone: str, channels: list,
                         status: str = "ONAY", source: str = "HS_WEB",
                         ip: str = "", order_id: str = "", user_id: str = None) -> dict:
    """İzin/RED kaydını db.iys_consents'e yazar ve NetGSM İYS'ye bildirir (best-effort).
    channels: ["MESAJ","EPOSTA"] alt kümesi. Denetim izi + dijital bildirim tek yerde."""
    channels = [c for c in (channels or []) if c in ("MESAJ", "EPOSTA")]
    if not channels:
        return {"recorded": False, "reason": "kanal yok"}
    rec = {
        "id": generate_id(),
        "email": (recipient_email or "").strip().lower(),
        "phone": normalize_phone_tr(recipient_phone or ""),
        "channels": channels, "status": status, "source": source,
        "ip": ip, "order_id": order_id, "user_id": user_id,
        "consent_date": _now().isoformat(),
        "reported": False, "created_at": _now().isoformat(),
    }
    await db.iys_consents.insert_one({**rec})
    # NetGSM İYS'ye bildirim GÜNLÜK TOPLU yapılır (scheduler → report_pending_batch). Kayıt başına
    # anlık istek NetGSM 'Sinir asimi' (103) limitine takılıyordu.
    return {"recorded": True, "id": rec["id"]}


def _admin_or_403(current_user):
    # GÜVENLİK (denetim 2026-10): uçlar artık Depends(require_admin) ile korunur (aktif hesap,
    # token_version, personel rolü DB'den doğrulanır); bu kontrol ek savunma olarak kalır.
    if not (current_user and current_user.get("is_admin")):
        raise HTTPException(status_code=403, detail="Admin yetkisi gerekli")


from security.contact_pii import mask_phone as _mask_phone, scrub_contact_text as _scrub_text  # noqa: E402


@router.get("/diagnostics")
async def iys_diagnostics(order_number: str = "", limit: int = 20,
                          current_user: dict = Depends(require_permission("integrations.view", any_in_area=True))):
    """SALT-OKUNUR teşhis: NetGSM İYS kimlik bilgileri dolu mu + son izin kayıtlarının
    BİLDİRİM durumu (reported / HTTP kodu / NetGSM yanıtı). 'İYS'ye düşmedi' sorununun tam
    sebebini gösterir (kimlik eksik mi, NetGSM reddetti mi, kanal yok mu)."""
    _admin_or_403(current_user)
    # NetGSM İYS kimlik durumu — bildirim NetGSM'in /iys/add ucunu kullanır (SMS ile aynı kimlik).
    cfg = await _iys_config()
    present = {
        "netgsm_username": bool(cfg.get("username")),
        "netgsm_password": bool(cfg.get("password")),
        "brand_code": bool(cfg.get("brand_code") or cfg.get("iys_code")),
    }
    q = {}
    ord_info = None
    if order_number:
        o = await db.orders.find_one(
            {"order_number": order_number},
            {"_id": 0, "id": 1, "marketing_consent": 1, "shipping_address": 1})
        if o:
            q["order_id"] = o["id"]
            ord_info = {"order_number": order_number,
                        "marketing_consent": o.get("marketing_consent"),
                        "phone": _mask_phone((o.get("shipping_address") or {}).get("phone")),
                        "email": (o.get("shipping_address") or {}).get("email")}
    recs = await db.iys_consents.find(q, {"_id": 0}).sort("created_at", -1)\
        .limit(max(1, min(int(limit or 20), 100))).to_list(None)
    return {
        "config_present": present,
        "all_credentials_ok": all(present.values()),
        "brand_code": (cfg.get("brand_code") or cfg.get("iys_code") or "(eksik)"),
        "netgsm_url": os.environ.get("NETGSM_IYS_URL", "https://api.netgsm.com.tr/iys/add"),
        "order": ord_info,
        "consent_count": len(recs),
        "consents": [{
            "order_id": r.get("order_id"), "channels": r.get("channels"), "status": r.get("status"),
            "email": r.get("email"), "phone": _mask_phone(r.get("phone")),
            "reported": r.get("reported"), "report_status_code": r.get("report_status_code"),
            # KVKK: NetGSM yanıtı numarayı yankılayabilir → metindeki telefon maskelenir.
            "report_response": _scrub_text((r.get("report_response") or "")[:400]),
            "consent_date": r.get("consent_date"), "reported_at": r.get("reported_at"),
        } for r in recs],
    }


@router.post("/retry")
async def iys_retry_report(payload: dict, current_user: dict = Depends(require_permission("integrations.netgsm"))):
    """Kimlik bilgileri düzeltildikten sonra bildirilmemiş izinleri NetGSM İYS'ye YENİDEN
    bildirir. payload: {order_number?} verilirse o siparişin izinleri; yoksa bildirilmemiş
    (reported=false) son 100 izin denenir."""
    _admin_or_403(current_user)
    onum = str((payload or {}).get("order_number") or "").strip()
    if onum:
        o = await db.orders.find_one({"order_number": onum}, {"_id": 0, "id": 1})
        q = {"order_id": o["id"]} if o else {"order_id": "__none__"}
    else:
        q = {"reported": {"$ne": True}}
    recs = await db.iys_consents.find(q, {"_id": 0}).sort("created_at", -1).limit(100).to_list(None)
    results = []
    for r in recs:
        ok = await _report_to_netgsm_iys(r)
        results.append({"id": r.get("id"), "channels": r.get("channels"), "reported": bool(ok)})
    return {"retried": len(results), "ok": sum(1 for x in results if x["reported"]), "results": results[:50]}


@router.post("/netgsm-probe")
async def iys_netgsm_probe(payload: dict, current_user: dict = Depends(require_permission("integrations.view", any_in_area=True))):
    """TEŞHİS: NetGSM İYS ucunu FARKLI url/appkey ile dener ve NetGSM'in HAM yanıtını (status +
    gövde) döndürür. 404'ün sebebini (yanlış yol mu, appkey/hesap yetkisi mi) tek deploy sonrası
    tarayıcıdan bulmak için. payload: {url?, appkey?, recipient?, type?} — recipient verilmezse
    NetGSM'e GERÇEK add gönderilmez, yalnızca yol/erişim test edilir (recipient boşsa 400 döner)."""
    _admin_or_403(current_user)
    cfg = await _iys_config()
    # DENETİM SEC-2 F7: URL artık İSTEMCİDEN ALINMAZ. Eskiden {url:"https://evil"} verilip
    # NetGSM kullanıcı adı/şifresi (Basic auth) o adrese POST'lanarak SMS sağlayıcı kimliği
    # sızdırılabiliyordu. Yalnız sabit/env NetGSM ucu; ek güvenlik: host beyaz-listesi.
    url = os.environ.get("NETGSM_IYS_URL") or "https://api.netgsm.com.tr/iys/add"
    from urllib.parse import urlparse as _up
    if (_up(url).hostname or "") not in ("api.netgsm.com.tr", "www.netgsm.com.tr", "netgsm.com.tr"):
        raise HTTPException(status_code=400, detail="Geçersiz NetGSM ucu")
    appkey = (payload or {}).get("appkey", cfg.get("appkey") or "")
    recipient = str((payload or {}).get("recipient") or "").strip()
    ch = str((payload or {}).get("type") or "MESAJ")
    username = (cfg.get("username") or "").strip()
    password = (cfg.get("password") or "").strip()
    brand_code = (cfg.get("brand_code") or cfg.get("iys_code") or "").strip()
    header = {"username": username, "password": password, "brandCode": brand_code}
    data = []
    if recipient:
        if ch == "MESAJ" and not recipient.startswith("+"):
            d = recipient.lstrip("0")
            recipient = "+" + (d if d.startswith("90") else "90" + d)
        item = {"type": ch, "source": "HS_WEB", "recipient": recipient,
                "status": "ONAY", "consentDate": _now().isoformat().replace("T", " ")[:19],
                "recipientType": "BIREYSEL"}
        if appkey:
            item["appkey"] = appkey       # NetGSM resmî n8n: appkey data öğesinde
        data.append(item)
    payload_out = {"header": header, "body": {"data": data}}
    import base64 as _b64
    _auth = _b64.b64encode(f"{username}:{password}".encode()).decode()
    _hdrs = {"Content-Type": "application/json; charset=utf-8",
             "Authorization": "Basic " + _auth}
    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as c:
            r = await c.post(url, json=payload_out, headers=_hdrs)
        return {"url": url, "sent_appkey": bool(appkey), "sent_basic_auth": True,
                "http_status": r.status_code,
                "final_url": str(r.url), "content_type": r.headers.get("content-type", ""),
                "body": (r.text or "").strip()[:800], "sent_data_count": len(data)}
    except Exception as e:
        return {"url": url, "error": str(e)[:300]}


# ── İYS'DEN RET ÇEKME (müşteri SMS "RET" / iys.org.tr / İYS uygulamasıyla reddettiyse) ─────────
# Kullanıcı isteği (2026-10-06): "İYS'de ret derse müşteri, İYS'yi tarayıp sitede de güncelleyelim."
# Bizde EN SON kararı ONAY olan alıcılar NetGSM İYS sorgu servisiyle (POST /iys/search — /iys/add ile
# AYNI kimlik, ayrı İYS API hesabı gerekmez) taranır; İYS'de RET görünen alıcı bizde de RET olur
# (kampanya kitlesinden düşer, bülten aboneliği kapanır). Her alıcı en çok GÜNDE BİR sorgulanır;
# tur başına sorgu sınırı + NetGSM 'Sinir asimi' (103) gelirse durma — kalanlar sonraki turda.
_IYS_SEARCH_RECHECK_H = 24


async def _post_iys_search(cfg: dict, ch: str, recipient: str):
    """NetGSM /iys/search → (ok, durum dict|None, ham gövde). durum: {status, consentDate, ...};
    İYS'de kayıt yoksa (code 50) ok=True, durum=None."""
    import base64 as _b64
    import json as _json
    username = (cfg.get("username") or "").strip()
    password = (cfg.get("password") or "").strip()
    brand_code = (cfg.get("brand_code") or cfg.get("iys_code") or "").strip()
    payload = {"header": {"username": username, "password": password, "brandCode": brand_code},
               "body": {"data": [{"type": ch, "recipient": recipient, "recipientType": "BIREYSEL"}]}}
    url = os.environ.get("NETGSM_IYS_SEARCH_URL") or "https://api.netgsm.com.tr/iys/search"
    hdrs = {"Content-Type": "application/json; charset=utf-8",
            "Authorization": "Basic " + _b64.b64encode(f"{username}:{password}".encode()).decode()}
    try:
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.post(url, json=payload, headers=hdrs)
        body = (r.text or "").strip()[:800]
    except Exception as e:
        return False, None, f"exception: {type(e).__name__}"
    if r.status_code != 200:
        return False, None, f"http {r.status_code}: {body[:200]}"
    try:
        j = _json.loads(body)
    except Exception:
        return False, None, body[:200]
    if not isinstance(j, dict):
        return False, None, body[:200]
    code = str(j.get("code", "")).strip()
    if code == "50":                       # İYS'de bu alıcı için kayıt yok
        return True, None, body[:200]
    if code not in ("0", "00"):
        return False, None, body[:200]
    q = j.get("query")
    if isinstance(q, list):
        q = q[0] if q else None
    return True, (q if isinstance(q, dict) else None), body[:200]


def _iys_search_recipient(ch: str, recipient: str) -> str:
    if ch == "MESAJ":
        return (_iys_item("MESAJ", {"phone": recipient}) or {}).get("recipient", "")
    return (recipient or "").strip().lower()


def _iys_date_utc_iso(v: str) -> str:
    """İYS consentDate ('2026-10-05 14:03:00', TR saati) → UTC ISO; çözülemezse ''."""
    from datetime import datetime as _dt, timedelta as _td, timezone as _tz
    s = str(v or "").strip().replace("T", " ")[:19]
    try:
        d = _dt.strptime(s, "%Y-%m-%d %H:%M:%S")
    except Exception:
        return ""
    return (d - _td(hours=3)).replace(tzinfo=_tz.utc).isoformat()


async def _apply_iys_ret(ch: str, recipient: str, rec: dict, iys: dict) -> None:
    """İYS'deki RET'i yerel kayda işler (İYS'ye GERİ bildirilmez: reported=True)."""
    now = _now().isoformat()
    doc = {
        "id": generate_id(),
        "email": recipient if ch == "EPOSTA" else (rec.get("email") or ""),
        "phone": recipient if ch == "MESAJ" else "",
        "channels": [ch], "status": "RET", "source": "IYS", "origin": "iys_pull",
        "user_id": rec.get("user_id"),
        # Sıralama için ÖĞRENDİĞİMİZ an (en son karar kazanır); İYS'deki tarih ayrıca saklanır.
        "consent_date": now, "iys_consent_date": str(iys.get("consentDate") or ""),
        "iys_source": str(iys.get("source") or ""),
        "reported": True, "report_response": "İYS'den çekildi (geri bildirilmez)", "created_at": now,
    }
    await db.iys_consents.insert_one(dict(doc))
    if ch == "EPOSTA":
        await db.newsletter_subscribers.update_many(
            {"email": recipient, "active": {"$ne": False}},
            {"$set": {"active": False, "unsubscribed_at": now, "unsubscribed_via": "iys"}})
        async for u in db.users.find({"email": recipient}, {"_id": 0, "id": 1, "marketing_prefs": 1}):
            prefs = u.get("marketing_prefs") if isinstance(u.get("marketing_prefs"), dict) else {}
            await db.users.update_one({"id": u["id"]}, {"$set": {
                "accepts_marketing": False, "marketing_prefs": {**prefs, "email": False},
                "marketing_unsubscribed_at": now}})


async def sync_iys_rets(max_queries: int = 200, pause_s: float = 0.3) -> dict:
    """Bizde ONAY görünen alıcıları İYS'de sorgular; İYS'de RET olanları yerelde RET yapar."""
    import asyncio as _asyncio
    from datetime import timedelta as _td
    from .consents import _collect
    cfg = await _iys_config()
    if not ((cfg.get("username") or "").strip() and (cfg.get("password") or "").strip()
            and (cfg.get("brand_code") or cfg.get("iys_code") or "").strip()):
        return {"error": "NetGSM İYS kimlik/marka eksik", "checked": 0}
    latest = await _collect()
    cands = [(("EPOSTA" if ch == "email" else "MESAJ"), rcp, rec) for (ch, rcp), rec in latest.items()
             if rec.get("status") == "onay" and not rec.get("suppressed")]
    if not cands:
        return {"checked": 0, "ret": 0, "candidates": 0}
    keys = [f"{c}:{r}" for c, r, _ in cands]
    seen = {}
    async for x in db.iys_ret_checks.find({"key": {"$in": keys}}, {"_id": 0, "key": 1, "checked_at": 1}):
        seen[x["key"]] = x.get("checked_at") or ""
    cutoff = (_now() - _td(hours=_IYS_SEARCH_RECHECK_H)).isoformat()
    due = [c for c in cands if seen.get(f"{c[0]}:{c[1]}", "") < cutoff]
    due.sort(key=lambda c: seen.get(f"{c[0]}:{c[1]}", ""))     # hiç bakılmamış → en eski kontrol
    checked = rets = skipped = errors = 0
    stop, last_err = None, ""
    for ch, rcp, rec in due[:max_queries]:
        q_rcp = _iys_search_recipient(ch, rcp)
        if not q_rcp:
            continue
        ok, st, body = await _post_iys_search(cfg, ch, q_rcp)
        if not ok:
            errors += 1
            last_err = body
            if '"103"' in (body or "") or errors >= 3:
                stop = "sınır aşımı" if '"103"' in (body or "") else "ardışık hata"
                break
            continue
        errors = 0
        checked += 1
        iys_status = str((st or {}).get("status") or "").upper()
        applied = False
        if iys_status == "RET":
            # Bizdeki ONAY henüz İYS'ye BİLDİRİLMEMİŞ ve İYS'deki RET'ten YENİYSE müşteri sonradan
            # yeniden izin vermiştir → İYS'deki eski RET uygulanmaz (bizim ONAY'ımız bildirilecek).
            iys_at = _iys_date_utc_iso((st or {}).get("consentDate"))
            if (not rec.get("reported")) and iys_at and (rec.get("at") or "") > iys_at:
                skipped += 1
            else:
                await _apply_iys_ret(ch, rcp, rec, st or {})
                rets += 1
                applied = True
        await db.iys_ret_checks.update_one({"key": f"{ch}:{rcp}"}, {"$set": {
            "key": f"{ch}:{rcp}", "checked_at": _now().isoformat(), "iys_status": iys_status or "YOK",
            "applied_ret": applied}}, upsert=True)
        if pause_s:
            await _asyncio.sleep(pause_s)
    res = {"candidates": len(cands), "due": len(due), "checked": checked, "ret": rets,
           "skipped_newer_local": skipped, "stopped": stop, "last_error": (last_err or "")[:200]}
    await db.settings.update_one({"id": "iys_ret_sync"}, {"$set": {
        "id": "iys_ret_sync", "last_run_at": _now().isoformat(), "last_result": res}}, upsert=True)
    if rets or stop:
        try:
            from .integrations_common import log_integration_event
            await log_integration_event("netgsm", "iys_ret_pull", "consent", str(rets),
                                        "error" if stop else "success",
                                        f"İYS RET taraması: {checked} alıcı sorgulandı, {rets} RET siteye işlendi"
                                        + (f", durdu: {stop} ({(last_err or '')[:120]})" if stop else ""))
        except Exception:
            pass
    return res
