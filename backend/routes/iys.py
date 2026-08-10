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

from .deps import db, logger, generate_id, get_current_user
from fastapi import Depends
from notification_service import (
    _get_providers_config, SMS_IMPL, _sms_generic, normalize_phone_tr,
)

router = APIRouter(prefix="/iys", tags=["iys-consent"])

_OTP_TTL_MIN = 5
_OTP_MAX_TRY = 5


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
        logger.warning(f"[iys] OTP SMS gönderilemedi: {e}")
        return {"success": False, "error": str(e)}


@router.post("/otp/send")
async def otp_send(payload: dict, request: Request):
    """Telefona OTP gönderir. body: {phone}."""
    phone = normalize_phone_tr(str((payload or {}).get("phone") or ""))
    if len(phone) < 12 or not phone.isdigit():
        raise HTTPException(status_code=400, detail="Geçerli bir telefon numarası giriniz.")
    # 1 dk mükerrer koruması
    recent = await db.otp_verifications.find_one(
        {"phone": phone, "created_at": {"$gte": (_now() - timedelta(minutes=1)).isoformat()}},
        {"_id": 0, "created_at": 1},
    )
    if recent:
        raise HTTPException(status_code=429, detail="Çok sık deneme. Lütfen 1 dakika sonra tekrar deneyin.")
    code = _gen_code()
    rec = {
        "id": generate_id(), "phone": phone, "code": code,
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
async def otp_verify(payload: dict):
    """OTP doğrular. body: {phone, code}. Başarılıysa {verified:true}."""
    phone = normalize_phone_tr(str((payload or {}).get("phone") or ""))
    code = str((payload or {}).get("code") or "").strip()
    rec = await db.otp_verifications.find_one(
        {"phone": phone, "used": False}, {"_id": 0}, sort=[("created_at", -1)]
    )
    if not rec:
        raise HTTPException(status_code=400, detail="Doğrulama kodu bulunamadı, yeniden gönderin.")
    try:
        exp = datetime.fromisoformat(str(rec.get("expires_at")).replace("Z", "+00:00"))
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
    except Exception:
        exp = _now()
    if _now() > exp:
        raise HTTPException(status_code=400, detail="Kodun süresi doldu, yeniden gönderin.")
    if int(rec.get("tries", 0)) >= _OTP_MAX_TRY:
        raise HTTPException(status_code=429, detail="Çok fazla hatalı deneme. Yeni kod isteyin.")
    if code != rec.get("code"):
        await db.otp_verifications.update_one({"id": rec["id"]}, {"$inc": {"tries": 1}})
        raise HTTPException(status_code=400, detail="Kod hatalı.")
    await db.otp_verifications.update_one(
        {"id": rec["id"]}, {"$set": {"used": True, "verified_at": _now().isoformat()}})
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
    prov = (doc.get("providers", {}) or {}).get("netgsm", {}) or {}
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
        lo = body.lower().replace(" ", "")
        # Başarı: HTTP 200 + NetGSM başarı sinyali (code "00"/"0" veya resultstatus "success"/
        # "basarili") ve "failure"/"error"/"hata"/'"code":"40"' gibi hata izi içermez.
        _err = ("failure" in lo or '"error"' in lo or '"code":"40"' in lo
                or '"code":"30"' in lo or '"code":"70"' in lo)
        _succ = ("success" in lo or "basarili" in lo or '"code":"00"' in lo
                 or '"code":"0"' in lo or '"code":00' in lo)
        ok = (code == 200 and _succ and not _err)
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
        {"$set": {"accepts_marketing": bool(email_on or sms_on),
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
    try:
        await _report_to_netgsm_iys(rec)
    except Exception as e:
        logger.warning(f"[iys] rapor spawn hata: {e}")
    return {"recorded": True, "id": rec["id"]}


def _admin_or_403(current_user):
    if not (current_user and current_user.get("is_admin")):
        raise HTTPException(status_code=403, detail="Admin yetkisi gerekli")


@router.get("/diagnostics")
async def iys_diagnostics(order_number: str = "", limit: int = 20,
                          current_user: dict = Depends(get_current_user)):
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
                        "phone": (o.get("shipping_address") or {}).get("phone"),
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
            "email": r.get("email"), "phone": r.get("phone"),
            "reported": r.get("reported"), "report_status_code": r.get("report_status_code"),
            "report_response": (r.get("report_response") or "")[:400],
            "consent_date": r.get("consent_date"), "reported_at": r.get("reported_at"),
        } for r in recs],
    }


@router.post("/retry")
async def iys_retry_report(payload: dict, current_user: dict = Depends(get_current_user)):
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
async def iys_netgsm_probe(payload: dict, current_user: dict = Depends(get_current_user)):
    """TEŞHİS: NetGSM İYS ucunu FARKLI url/appkey ile dener ve NetGSM'in HAM yanıtını (status +
    gövde) döndürür. 404'ün sebebini (yanlış yol mu, appkey/hesap yetkisi mi) tek deploy sonrası
    tarayıcıdan bulmak için. payload: {url?, appkey?, recipient?, type?} — recipient verilmezse
    NetGSM'e GERÇEK add gönderilmez, yalnızca yol/erişim test edilir (recipient boşsa 400 döner)."""
    _admin_or_403(current_user)
    cfg = await _iys_config()
    url = (payload or {}).get("url") or os.environ.get("NETGSM_IYS_URL") or "https://api.netgsm.com.tr/iys/add"
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
