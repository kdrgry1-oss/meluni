"""
MNG Kargo (DHL eCommerce) SOAP Client
WSDL: https://service.mngkargo.com.tr/musterikargosiparis/musterikargosiparis.asmx?WSDL

Verified WSDL operations:
  - Baglanti_Test()                    : connection ping
  - SiparisGirisiDetayliV3(...)        : create shipment + return barcode
  - KargoTakipByReferans(pKullanici, pSifre, pReferansId): track by reference (sipariş no)
  - TekBarkodGonderiIptali(...)        : cancel a single shipment by barcode
  - MNGGonderiBarkod(req)              : barcode print request

Kimlik bilgileri (GÜVENLİK: koda gömülmez):
  DB settings.id="mng_kargo" veya ortam değişkeni MNG_USERNAME / MNG_PASSWORD
  üzerinden sağlanır. CustomerCode/TaxNumber gizli olmayan şirket bilgileridir.
"""
import logging
import warnings
from typing import Dict, Optional

warnings.filterwarnings("ignore")
logger = logging.getLogger(__name__)

WSDL_URL = "https://service.mngkargo.com.tr/musterikargosiparis/musterikargosiparis.asmx?WSDL"

_client_cache = None

# MNG kargo etiket render motoru Türkçe büyük karakterleri (Ş, İ, Ğ, vb.)
# kaybediyor. ASCII-güvenli bir Türkçe çeviri ile gönderiyoruz; PDF'de
# "Ş→S, İ→I, Ğ→G, ..." görüntülenecek ama veriler eksiksiz korunur.
_TR_MAP = str.maketrans({
    "Ç": "C", "Ğ": "G", "İ": "I", "Ö": "O", "Ş": "S", "Ü": "U",
    "ç": "c", "ğ": "g", "ı": "i", "ö": "o", "ş": "s", "ü": "u",
    "Â": "A", "Î": "I", "Û": "U", "â": "a", "î": "i", "û": "u",
})


def tr_safe(s) -> str:
    """Türkçe karakterleri ASCII karşılığına dönüştürür. None/boş güvenli."""
    if not s:
        return ""
    return str(s).translate(_TR_MAP)


# FaturaSiparisListesi/KargoTakipByReferans yanıtının iç yapısı MNG/DHL'de sürüme göre
# değişebiliyor (_value_1 sarmalı, düz dict, liste, vb.). Bu yüzden ASIL alanları
# (GONDERI_NO gibi) yapıya bağlı KATI bir yoldan okumak yerine, yanıtın HER yerinde
# özyinelemeli arıyoruz. Böylece numara nerede gömülü olursa olsun yakalanır.
_FIELD_ALIASES = {
    # normalize(key) -> kanonik ad   (normalize: lower + '_'/' ' kaldır)
    "gonderino": "gonderi_no",
    "mngsiparisno": "mng_siparis_no",
    "siparisno": "referans_no",       # MNG bunu 'Referans No' olarak gösteriyor (bizim W… no)
    "referansno": "referans_no",
    "kargostatu": "kargo_statu",
    "kargostatuaciklama": "kargo_statu_aciklama",
    "kargotakipurl": "kargo_takip_url",
    "teslimtarihi": "teslim_tarihi",
    "aliciil": "alici_il",
    "cikissubesi": "cikis_subesi",
    "teslimsubesi": "teslim_subesi",
    "faturaserino": "fatura_seri_no",
    "irsaliyeno": "irsaliye_no",
}


def _dhl_takip_url(no) -> str:
    """Takip no'dan DHL eCommerce müşteri takip deep-link'ini kurar.
    Genel https://www.dhlecommerce.com.tr/gonderitakip (no'suz) yerine
    https://kargotakip.dhlecommerce.com.tr/?takipNo=<no> — müşteri direkt veriyi görür."""
    no = str(no or "").strip()
    return f"https://kargotakip.dhlecommerce.com.tr/?takipNo={no}" if no else ""


def _norm_key(k) -> str:
    return str(k).strip().lower().replace("_", "").replace(" ", "")


def _deep_find_fields(obj) -> Dict:
    """Serialize edilmiş yanıtın TÜM dict/list düğümlerini gezip her kanonik alan için
    İLK boş-olmayan değeri toplar. Yapı ne olursa olsun GONDERI_NO vb. yakalanır."""
    found: Dict[str, str] = {}

    def walk(o):
        if isinstance(o, dict):
            for k, v in o.items():
                if not isinstance(v, (dict, list)):
                    canon = _FIELD_ALIASES.get(_norm_key(k))
                    if canon and canon not in found and v not in (None, ""):
                        found[canon] = str(v).strip()
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for it in o:
                walk(it)

    try:
        walk(obj)
    except Exception:
        pass
    return found


def _deep_pick_any(obj, norm_keys) -> str:
    """Yanıtın her yerinde, normalize adı norm_keys içinde olan İLK boş-olmayan değeri döndürür."""
    target = set(norm_keys)
    result = {"v": ""}

    def walk(o):
        if result["v"]:
            return
        if isinstance(o, dict):
            for k, v in o.items():
                if not isinstance(v, (dict, list)) and _norm_key(k) in target and v not in (None, ""):
                    result["v"] = str(v).strip()
                    return
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for it in o:
                walk(it)

    try:
        walk(obj)
    except Exception:
        pass
    return result["v"]


def _sanitize_fields(f: Dict, siparis_no: str = "") -> Dict:
    """Gönderi no GERÇEK kargo takip no DEĞİLSE boşalt.

    Kurallar:
      - gönderi no, iç sipariş no (mng_siparis_no) ile AYNI ise → sahte (iç no), boşalt.
      - gönderi no, bizim referansımız (siparis_no / referans_no) ile AYNI ise → boşalt.
    Böylece 'İşlemi Yapılmadı' durumunda MNG'nin iç no'su yanlışlıkla takip no diye yazılmaz.
    """
    g = (f.get("gonderi_no") or "").strip()
    m = (f.get("mng_siparis_no") or "").strip()
    rf = (f.get("referans_no") or "").strip()
    sn = str(siparis_no or "").strip()
    if g and (g == m or (rf and g == rf) or (sn and g == sn)):
        f["gonderi_no"] = ""
    return f


def _get_client():
    global _client_cache
    if _client_cache is None:
        from zeep import Client, Settings
        from zeep.transports import Transport
        _client_cache = Client(
            WSDL_URL,
            settings=Settings(strict=False, xml_huge_tree=True),
            transport=Transport(timeout=15, operation_timeout=25),
        )
    return _client_cache


def baglanti_test() -> Dict:
    """Test SOAP service availability."""
    try:
        c = _get_client()
        r = c.service.Baglanti_Test()
        return {"ok": True, "result": str(r)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def get_mng_barcode_immediately(*, username: str, password: str, siparis_no: str,
                                 irsaliye_no: str = "", urun_bedeli: float = 0.0,
                                 kapida_tahsilat: bool = False,
                                 out_barkod_type: str = "C") -> Dict:
    """MNGGonderiBarkod → Anında NZ-formatlı kargo barkodu üretir (sipariş oluşturulduktan sonra).
    
    NOT: Bu endpoint MNG tarafında IP whitelist gerektirir. Whitelist yoksa
    'YETKİ HATASI' döner ve fallback olarak MNG_SIPARIS_NO kullanılmalıdır.
    
    out_barkod_type:
      C = Code39  |  B = Barcode  |  P = Plain (NZ formatlı)
    
    Returns:
      { ok: bool, barkod: str (örn 'NZ197406'), gonderi_no: str, hata: str }
    """
    c = _get_client()
    try:
        req_type = c.get_type("ns0:GonderiBarkodRequest")
        req = req_type(
            WsUserName=username, WsPassword=password,
            ReferansNo=siparis_no, IrsaliyeNo=irsaliye_no,
            OutBarkodType=out_barkod_type,
            FlKapidaTahsilat="1" if kapida_tahsilat else "0",
            UrunBedeli=str(int(round(float(urun_bedeli or 0)))),  # ondaliksiz: E029 onlemi
            ChMesaj="", EkString1="", EkString2="", EkString3="", EkString4="",
            HatadaReferansBarkoduBas=0,
            ParcaBilgi=None,
        )
        r = c.service.MNGGonderiBarkod(req=req)
        from zeep.helpers import serialize_object
        ser = serialize_object(r)
        success = (ser.get("IstekBasarili") == 1)
        if not success:
            return {"ok": False, "barkod": "", "gonderi_no": "",
                    "hata": ser.get("IstekHata") or "MNGGonderiBarkod başarısız",
                    "raw": ser}
        # GonderiBarkods → list of barcodes for each parça
        barkods_obj = ser.get("GonderiBarkods")
        first_barkod = ""
        if barkods_obj:
            # zeep parses ArrayOfGonderiBarkod as {"_value_1": [...]} or list
            items = barkods_obj.get("_value_1", []) if isinstance(barkods_obj, dict) else barkods_obj
            if items and isinstance(items, list):
                first = items[0]
                if isinstance(first, dict):
                    first_barkod = (first.get("Barkod") or first.get("barkod") or "").strip()
                elif hasattr(first, "__values__"):
                    first_barkod = str(dict(first.__values__).get("Barkod") or "").strip()
        return {
            "ok": True,
            "barkod": first_barkod or str(ser.get("MngKargoGonderiNo") or ""),
            "gonderi_no": str(ser.get("MngKargoGonderiNo") or "").strip(),
            "fatura_seri_no": str(ser.get("MngKargoFaturaSeriNo") or "").strip(),
            "raw": ser,
        }
    except Exception as e:
        logger.error(f"MNGGonderiBarkod error for {siparis_no}: {e}")
        return {"ok": False, "barkod": "", "gonderi_no": "", "hata": str(e), "raw": None}


def get_mng_shipment_status(*, username: str, password: str, siparis_no: str) -> Dict:
    """FaturaSiparisListesi → siparis_no'dan TÜM kargo durumunu çek.
    
    Returns:
      {
        "mng_siparis_no": "1757391445",  # MNG iç referans
        "gonderi_no": "NZ197406",         # ASIL KARGO TAKİP KODU (MNG şubesi atadığında)
        "kargo_statu": "0",               # 0=İşlem Yok, 1+=işleniyor
        "kargo_statu_aciklama": "...",
        "kargo_takip_url": "...",
        "teslim_tarihi": "...",
        "alici_il": "...",
      }
    """
    c = _get_client()
    try:
        r = c.service.FaturaSiparisListesi(
            pSiparisNo=siparis_no, pKullaniciAdi=username, pSifre=password
        )
        if r is None:
            return {"ok": False, "error": "Sipariş bulunamadı"}
        from zeep.helpers import serialize_object
        ser = serialize_object(r)
        # Yapıya bağlı KATI yol yerine: yanıtın her yerinde alanları özyinelemeli bul.
        f = _sanitize_fields(_deep_find_fields(ser), siparis_no)
        return {
            "ok": True,
            "method": "FaturaSiparisListesi",
            "mng_siparis_no": f.get("mng_siparis_no", ""),
            "gonderi_no": f.get("gonderi_no", ""),         # ASIL TAKİP NO (örn. 411754264494)
            "referans_no": f.get("referans_no", ""),       # bizim W… sipariş no
            "kargo_statu": f.get("kargo_statu", "0"),
            "kargo_statu_aciklama": f.get("kargo_statu_aciklama", ""),
            "kargo_takip_url": _dhl_takip_url(f.get("gonderi_no", "")) or f.get("kargo_takip_url", ""),
            "teslim_tarihi": f.get("teslim_tarihi", ""),
            "alici_il": f.get("alici_il", ""),
            "cikis_subesi": f.get("cikis_subesi", ""),
            "teslim_subesi": f.get("teslim_subesi", ""),
            "raw": ser,
        }
    except Exception as e:
        _primary = str(e)
        logger.warning(f"MNG FaturaSiparisListesi({siparis_no}) hata, KargoTakipByReferans deneniyor: {e}")
        # FALLBACK: WSDL'de doğrulanmış takip operasyonu KargoTakipByReferans.
        # FaturaSiparisListesi operasyonu serviste yoksa/değiştiyse ya da geçici
        # hata verirse buraya düşer; iki yöntem de başarısızsa İKİSİNİN de gerçek
        # hatası geri döner (teşhis paneli bunu gösterir — sessiz yutma yok).
        try:
            r2 = c.service.KargoTakipByReferans(
                pKullanici=username, pSifre=password, pReferansId=siparis_no
            )
            if r2 is None:
                return {"ok": False,
                        "error": f"Takip kaydı yok (KargoTakipByReferans). İlk yöntem: {_primary[:150]}"}
            from zeep.helpers import serialize_object
            d = serialize_object(r2)
            f = _deep_find_fields(d)
            # GONDERI_NO alias listesi bu operasyonda farklı adlarda olabilir; ek tarama:
            if not f.get("gonderi_no"):
                _extra = _deep_pick_any(d, ("barkod", "takipno", "kargotakipno", "gonderibarkod"))
                if _extra:
                    f["gonderi_no"] = _extra
            f = _sanitize_fields(f, siparis_no)
            return {
                "ok": True,
                "method": "KargoTakipByReferans",
                "mng_siparis_no": f.get("mng_siparis_no", ""),
                "gonderi_no": f.get("gonderi_no", ""),
                "referans_no": f.get("referans_no", ""),
                "kargo_statu": f.get("kargo_statu", "0"),
                "kargo_statu_aciklama": f.get("kargo_statu_aciklama", ""),
                "kargo_takip_url": _dhl_takip_url(f.get("gonderi_no", "")) or f.get("kargo_takip_url", ""),
                "teslim_tarihi": f.get("teslim_tarihi", ""),
                "alici_il": f.get("alici_il", ""),
                "raw": d,
                "primary_error": _primary[:200],
            }
        except Exception as e2:
            logger.error(f"MNG get_shipment_status({siparis_no}) iki yöntem de başarısız: F={e}; K={e2}")
            return {"ok": False,
                    "error": f"FaturaSiparisListesi: {_primary[:140]} || KargoTakipByReferans: {str(e2)[:140]}"}


def get_mng_barcode_by_siparis_no(*, username: str, password: str, siparis_no: str) -> Optional[str]:
    """Geriye uyumluluk: MNG_SIPARIS_NO döndürür."""
    info = get_mng_shipment_status(username=username, password=password, siparis_no=siparis_no)
    return info.get("mng_siparis_no") if info.get("ok") else None


def create_shipment(
    *,
    username: str,
    password: str,
    siparis_no: str,
    irsaliye_no: str = "",
    kiymet: float = 0.0,
    icerik: str = "",
    hizmet_sekli: str = "TS",   # TS=Standart, KP=Kapıda Ödeme, EX=Ekspres
    teslim_sekli: int = 1,       # 1=Adrese Teslim
    al_sms: int = 0,
    gn_sms: int = 1,
    parca_list: str = "1;1;1",   # Adet;En;Boy (basitleştirilmiş)
    alici_mng_no: str = "",
    alici_bayi_no: str = "",
    alici_ad: str,
    odeme_sekli: str = "GO",     # GO=Gönderici Öder, AO=Alıcı Öder
    adres_farkli: str = "0",
    il: str,
    ilce: str,
    adres: str,
    semt: str = "",
    mahalle: str = "",
    meydan_bulvar: str = "",
    cadde: str = "",
    sokak: str = "",
    tel_ev: str = "",
    tel_cep: str,
    tel_is: str = "",
    fax: str = "",
    email: str = "",
    vergi_dairesi: str = "",
    vergi_no: str = "",
    kapida_odeme: int = 0,
    mal_bedeli_odeme: str = "",
    platform_adi: str = "FACETTE",
    platform_kodu: str = "",
) -> Dict:
    """
    SiparisGirisiDetayliV3 ile gönderi oluştur.
    Return: { ok: bool, barkod: str, hata: str, raw: dict }
    """
    c = _get_client()
    try:
        result = c.service.SiparisGirisiDetayliV3(
            pChIrsaliyeNo=tr_safe(irsaliye_no),
            pPrKiymet=str(int(round(float(kiymet or 0)))),  # ondaliksiz: MNG '2410.0'yi '24100' okuyup E029 vermesini onler
            pChBarkod="",
            pChIcerik=tr_safe(icerik)[:250],
            pGonderiHizmetSekli=hizmet_sekli,
            pTeslimSekli=teslim_sekli,
            pFlAlSms=al_sms,
            pFlGnSms=gn_sms,
            pKargoParcaList=parca_list,
            pAliciMusteriMngNo=alici_mng_no,
            pAliciMusteriBayiNo=alici_bayi_no,
            pAliciMusteriAdi=tr_safe(alici_ad)[:100],
            pChSiparisNo=siparis_no,
            pLuOdemeSekli=odeme_sekli,
            pFlAdresFarkli=adres_farkli,
            pChIl=tr_safe(il),
            pChIlce=tr_safe(ilce),
            pChAdres=tr_safe(adres)[:250],
            pChSemt=tr_safe(semt),
            pChMahalle=tr_safe(mahalle),
            pChMeydanBulvar=tr_safe(meydan_bulvar),
            pChCadde=tr_safe(cadde),
            pChSokak=tr_safe(sokak),
            pChTelEv=tel_ev,
            pChTelCep=tel_cep,
            pChTelIs=tel_is,
            pChFax=fax,
            pChEmail=email,
            pChVergiDairesi=tr_safe(vergi_dairesi),
            pChVergiNumarasi=vergi_no,
            pFlKapidaOdeme=kapida_odeme,
            pMalBedeliOdemeSekli=mal_bedeli_odeme,
            pPlatformKisaAdi=tr_safe(platform_adi),
            pPlatformSatisKodu=platform_kodu,
            pKullaniciAdi=username,
            pSifre=password,
        )
        # MNG response: "1" = success, "0" = error veya "0~mesaj" / "1~mesaj"
        result_str = str(result or "").strip()
        logger.info(f"MNG SiparisGirisiDetayliV3 response for {siparis_no}: {result_str[:200]}")
        # V3 sadece status code döner: "1" = success. Gerçek barkod FaturaSiparisListesi ile alınır.
        if result_str == "1":
            # Fetch real barcode (MNG_SIPARIS_NO)
            import time as _t
            barcode = None
            for attempt in range(3):
                _t.sleep(1)
                barcode = get_mng_barcode_by_siparis_no(
                    username=username, password=password, siparis_no=siparis_no
                )
                if barcode:
                    break
            if barcode:
                return {"ok": True, "barkod": barcode, "raw": result_str}
            # Fallback: kayıt başarılı ama barkod henüz hazır değil
            return {"ok": True, "barkod": "", "raw": result_str,
                    "note": "Sipariş MNG'ye kaydedildi ancak barkod birkaç dakika sonra FaturaSiparisListesi ile alınabilir."}
        if "~" in result_str:
            parts = result_str.split("~", 1)
            code = parts[0].strip()
            payload = parts[1].strip() if len(parts) > 1 else ""
            # Y22: Protokol "1"=başarı, "0"=hata (bkz. yukarıdaki not). Önceki kod TERSTİ:
            # "0~mesaj" (HATA) başarı sayılıp hata mesajını barkod olarak yazıyor, "1~mesaj"
            # (BAŞARI) başarısız sanılıp tekrar deneniyor → çift gönderi oluşuyordu.
            if code == "1":
                return {"ok": True, "barkod": "", "raw": result_str,
                        "note": payload or "Sipariş MNG'ye kaydedildi; barkod FaturaSiparisListesi ile alınabilir."}
            return {"ok": False, "barkod": "", "hata": payload, "raw": result_str}
        # 0 veya bilinmeyen → hata
        return {"ok": False, "barkod": "", "hata": result_str or "Boş cevap", "raw": result_str}
    except Exception as e:
        logger.error(f"MNG create_shipment error for {siparis_no}: {e}")
        return {"ok": False, "barkod": "", "hata": str(e), "raw": None}


def cancel_shipment(*, username: str, password: str, siparis_no: str, gonderi_no: str = "") -> Dict:
    """TekBarkodGonderiIptali"""
    c = _get_client()
    try:
        r = c.service.TekBarkodGonderiIptali(
            pKullaniciAdi=username, pSifre=password,
            pChSiparisNo=siparis_no, pNmGonderiNo=gonderi_no
        )
        result_str = str(r or "")
        if result_str.startswith("0"):
            return {"ok": True, "raw": result_str}
        return {"ok": False, "hata": result_str.split("~", 1)[-1] if "~" in result_str else result_str}
    except Exception as e:
        return {"ok": False, "hata": str(e)}


def track_by_reference(*, username: str, password: str, referans_id: str) -> Dict:
    """KargoTakipByReferans → siparis_no ile takip"""
    c = _get_client()
    try:
        r = c.service.KargoTakipByReferans(
            pKullanici=username, pSifre=password, pReferansId=referans_id
        )
        if r is None:
            return {"ok": False, "events": [], "hata": "Bulunamadı"}
        # Normalize to dict
        if hasattr(r, "__values__"):
            r = dict(r.__values__)
        return {"ok": True, "data": r}
    except Exception as e:
        return {"ok": False, "hata": str(e)}
