"""
Ticimax SOAP Web Service Client  –  facette.com.tr
Default WS Key (UyeKodu): AKG0M8DTRSEBAIA898JA6HW22EDIU3 (override via DB settings)

Gerçek WSDL imzaları (doğrulanmış):
  SelectKategori  : UyeKodu, kategoriID=0, dil='tr', parentID
  SelectUrun      : UyeKodu, f:UrunFiltre, s:UrunSayfalama
  SelectUrunCount : UyeKodu, f:UrunFiltre
  SelectVaryasyon : UyeKodu, f:VaryasyonFiltre, s:UrunSayfalama, varyasyonAyar
  SelectUrunResim : UyeKodu, request
  SelectAsortiMiktar: UyeKodu, asortiMiktarId
  SelectSiparis   : UyeKodu, f:WebSiparisFiltre, s:WebSiparisSayfalama
  SelectSiparisUrun: UyeKodu, siparisId, iptalEdilmisUrunler
  SelectUyeler    : UyeKodu, f:UyeFiltre, s:UyeSayfalama
  SelectUyeAdres  : UyeKodu, uyeID
"""
import logging
import warnings
import time
import os
from typing import List, Dict, Optional, Any

warnings.filterwarnings("ignore")
logger = logging.getLogger(__name__)

TICIMAX_DOMAIN  = os.environ.get("TICIMAX_DOMAIN") or "facette.ticimaxeticaret.com"
TICIMAX_API_KEY = os.environ.get("TICIMAX_API_KEY") or "AKG0M8DTRSEBAIA898JA6HW22EDIU3"
RATE_LIMIT_SLEEP = 13   # Ticimax rate limit: 12 sn, biz 13 sn bekliyoruz

URUN_WSDL    = f"https://{TICIMAX_DOMAIN}/Servis/UrunServis.svc?wsdl"
SIPARIS_WSDL = f"https://{TICIMAX_DOMAIN}/Servis/SiparisServis.svc?wsdl"
UYE_WSDL     = f"https://{TICIMAX_DOMAIN}/Servis/UyeServis.svc?wsdl"

_urun_client_cache = None
_siparis_client_cache = None
_uye_client_cache = None


# [ticimax-off 2026-06-22] Ticimax SOAP baglantisi KAPATILDI (canli bag kesildi).
#   Otomatik siparis cron'u (scheduler) zaten kapali; kalan on-demand cagrilari
#   (returns /orders/refresh-dates + manuel order import/repair) da artik Ticimax'a
#   GITMEZ. Zeep client factory'leri RuntimeError firlatir; TUM cagiranlar SOAP'u
#   try/except ile sardigindan 500 olusmaz (sadece "Ticimax kapali" doner / bos kalir).
#   VERI DB'de; canli pull'a gerek yok. Acil yeniden acmak icin: env TICIMAX_LIVE=1.
TICIMAX_LIVE = (os.environ.get("TICIMAX_LIVE", "").strip().lower() in ("1", "true", "yes"))
_TICIMAX_OFF_MSG = "Ticimax SOAP baglantisi kapali (2026-06-22). Acmak icin env TICIMAX_LIVE=1."
# Geçmiş-veri kurtarma (ticimax_history) için KONTROLLÜ runtime anahtarı:
# yalnız admin backfill/probe çağrısı süresince set_live(True) ile açılır,
# finally bloğunda kapatılır. Kalıcı açmak için yine env TICIMAX_LIVE kullanılır.
_RUNTIME_LIVE = False


def set_live(flag: bool = True):
    global _RUNTIME_LIVE, _urun_client_cache, _siparis_client_cache, _uye_client_cache
    _RUNTIME_LIVE = bool(flag)
    if flag:
        _urun_client_cache = None
        _siparis_client_cache = None
        _uye_client_cache = None
    logger.info(f"Ticimax runtime live → {_RUNTIME_LIVE}")


def _live_ok() -> bool:
    return TICIMAX_LIVE or _RUNTIME_LIVE


def set_domain(domain: str):
    """Runtime'da Ticimax domain'ini değiştir. WSDL URL'lerini yeniden kurar ve
    client cache'lerini sıfırlar ki bir sonraki çağrı yeni domain'e gitsin.

    NOT: Doğru WS domaini artık resolve_ws_domain() ile CANLI olarak yoklanıp
    bulunur (aşağı bak). Bu fonksiyon sadece verilen host'u temizleyip ayarlar.
    """
    global TICIMAX_DOMAIN, URUN_WSDL, SIPARIS_WSDL, UYE_WSDL
    global _urun_client_cache, _siparis_client_cache, _uye_client_cache
    domain = (domain or "").strip().replace("https://", "").replace("http://", "").strip("/")
    # Yol / sorgu parçalarını at — yalnızca host kalsın
    domain = domain.split("/")[0].split("?")[0].strip()
    if not domain or domain == TICIMAX_DOMAIN:
        return
    TICIMAX_DOMAIN = domain
    URUN_WSDL    = f"https://{domain}/Servis/UrunServis.svc?wsdl"
    SIPARIS_WSDL = f"https://{domain}/Servis/SiparisServis.svc?wsdl"
    UYE_WSDL     = f"https://{domain}/Servis/UyeServis.svc?wsdl"
    _urun_client_cache = None
    _siparis_client_cache = None
    _uye_client_cache = None
    logger.info(f"Ticimax domain set → {domain}")
    logger.info(f"Ticimax domain set → {domain}")


def _urun_client():
    if not _live_ok():
        raise RuntimeError(_TICIMAX_OFF_MSG)
    global _urun_client_cache
    if _urun_client_cache is None:
        from zeep import Client, Settings
        from zeep.transports import Transport
        _urun_client_cache = Client(
            URUN_WSDL,
            settings=Settings(strict=False, xml_huge_tree=True),
            transport=Transport(timeout=90, operation_timeout=180),
        )
    return _urun_client_cache


def _siparis_client():
    if not _live_ok():
        raise RuntimeError(_TICIMAX_OFF_MSG)
    global _siparis_client_cache
    if _siparis_client_cache is None:
        from zeep import Client, Settings
        from zeep.transports import Transport
        _siparis_client_cache = Client(
            SIPARIS_WSDL,
            settings=Settings(strict=False, xml_huge_tree=True),
            transport=Transport(timeout=90, operation_timeout=180),
        )
    return _siparis_client_cache


def _uye_client():
    if not _live_ok():
        raise RuntimeError(_TICIMAX_OFF_MSG)
    global _uye_client_cache
    if _uye_client_cache is None:
        from zeep import Client, Settings
        from zeep.transports import Transport
        _uye_client_cache = Client(
            UYE_WSDL,
            settings=Settings(strict=False, xml_huge_tree=True),
            transport=Transport(timeout=90, operation_timeout=180),
        )
    return _uye_client_cache


def _to_dict(obj) -> Any:
    """Recursively convert zeep object → plain dict/list."""
    if obj is None:
        return None
    if isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, list):
        return [_to_dict(i) for i in obj]
    if hasattr(obj, "__values__"):
        return {k: _to_dict(v) for k, v in obj.__values__.items()}
    if hasattr(obj, "__dict__"):
        return {k: _to_dict(v) for k, v in obj.__dict__.items()
                if not k.startswith("_")}
    return obj


def _unwrap_list(result) -> List[Dict]:
    """Pull a list out of whatever zeep returns."""
    data = _to_dict(result)
    if data is None:
        return []
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for v in data.values():
            if isinstance(v, list):
                return v
        return [data]
    return []


# ═══════════════ CATEGORIES ════════════════════════════════════

class TicimaxAuthError(Exception):
    """Raised when WS Yetki Kodu lacks access to a Ticimax service."""
    pass


def check_urun_service_access(wscode: str = TICIMAX_API_KEY) -> dict:
    """
    Probe UrunServis with SelectKategori (a small, deterministic call).
    Ticimax returns 'Hatalı Kullanıcı Kodu' as a SOAP fault when the WS
    key has NO access to UrunServis. SelectUrun silently returns empty
    instead — making the failure invisible. This probe surfaces it.
    Returns: {"ok": bool, "error": str|None, "categories_sample": int}
    """
    c = _urun_client()
    try:
        result = c.service.SelectKategori(
            UyeKodu=wscode, kategoriID=0, dil="tr", parentID=0)
        cats = _unwrap_list(result)
        return {"ok": True, "error": None, "categories_sample": len(cats)}
    except Exception as e:
        msg = str(e)
        if "Hatalı Kullanıcı Kodu" in msg or "Hatali Kullanici" in msg:
            return {
                "ok": False,
                "error": "Hatalı Kullanıcı Kodu",
                "detail": "WS Yetki Kodu (UyeKodu) UrunServis'e erişim yetkisine sahip değil. "
                          "Ticimax admin panelinden bu anahtara 'Ürün Servis' yetkisi vermelisiniz "
                          "ya da ürün servisine yetkili farklı bir WS anahtarı edinmelisiniz.",
                "categories_sample": 0,
            }
        return {"ok": False, "error": msg, "detail": msg, "categories_sample": 0}


def get_categories(parent_id: int = 0, wscode: str = TICIMAX_API_KEY) -> List[Dict]:
    """
    SelectKategori(UyeKodu, kategoriID=0, dil='tr', parentID)
    parent_id=0 → kök kategoriler
    parent_id=X → X'in alt kategorileri
    Dönen alanlar: ID, PID, Tanim, Aktif, AltKategoriSayisi, Sira, Url,
                   Icerik, Kod, KategoriMenuGoster, SeoAnahtarKelime,
                   SeoSayfaAciklama, SeoSayfaBaslik, PaylasimAyar
    """
    c = _urun_client()
    result = c.service.SelectKategori(
        UyeKodu=wscode, kategoriID=0, dil="tr", parentID=parent_id)
    cats = _unwrap_list(result)
    logger.info(f"SelectKategori(parentID={parent_id}) → {len(cats)} categories")
    return cats


def get_all_categories(wscode: str = TICIMAX_API_KEY,
                       sleep_between: float = 1.5) -> List[Dict]:
    """
    Tüm kategori hiyerarşisini recursive olarak çeker.
    Kök kategorilerden başlar, AltKategoriSayisi > 0 olanların
    alt kategorilerini de çeker (2 seviye derinlik yeterli).
    sleep_between: rate limit için her API çağrısı arasında bekleme (sn)
    """
    all_cats: List[Dict] = []
    seen_ids = set()

    # Kök kategorileri çek
    roots = get_categories(parent_id=0, wscode=wscode)
    for cat in roots:
        cid = cat.get("ID")
        if cid and cid not in seen_ids:
            all_cats.append(cat)
            seen_ids.add(cid)

    # Alt kategorileri çek
    to_expand = [c for c in roots if (c.get("AltKategoriSayisi") or 0) > 0]
    for parent_cat in to_expand:
        time.sleep(sleep_between)
        try:
            subs = get_categories(parent_id=parent_cat["ID"], wscode=wscode)
            for sub in subs:
                sid = sub.get("ID")
                if sid and sid not in seen_ids:
                    all_cats.append(sub)
                    seen_ids.add(sid)
                # 3. seviye de varsa çek
                if (sub.get("AltKategoriSayisi") or 0) > 0:
                    time.sleep(sleep_between)
                    try:
                        subsubs = get_categories(parent_id=sid, wscode=wscode)
                        for ss in subsubs:
                            ssid = ss.get("ID")
                            if ssid and ssid not in seen_ids:
                                all_cats.append(ss)
                                seen_ids.add(ssid)
                    except Exception as e:
                        logger.error(f"SelectKategori(parentID={sid}): {e}")
        except Exception as e:
            logger.error(f"SelectKategori(parentID={parent_cat['ID']}): {e}")

    logger.info(f"get_all_categories → {len(all_cats)} total categories")
    return all_cats


# ═══════════════ PRODUCTS ══════════════════════════════════════

def get_product_count(aktif: Optional[int] = None,
                      wscode: str = TICIMAX_API_KEY) -> int:
    """
    SelectUrunCount(UyeKodu, f:UrunFiltre)
    aktif=1 → sadece aktif ürünler, aktif=0 → pasif, None → hepsi
    """
    c = _urun_client()
    ff = c.get_type("ns2:UrunFiltre")
    f_kwargs = {}
    if aktif is not None:
        f_kwargs["Aktif"] = aktif
    f = ff(**f_kwargs)
    try:
        result = c.service.SelectUrunCount(UyeKodu=wscode, f=f)
        return int(result or 0)
    except Exception as e:
        logger.error(f"SelectUrunCount: {e}")
        return 0


def get_products(page: int = 1, page_size: int = 50,
                 aktif: Optional[int] = 1,
                 kategori_id: Optional[int] = None,
                 urun_karti_id: Optional[int] = None,
                 wscode: str = TICIMAX_API_KEY) -> List[Dict]:
    """
    SelectUrun(UyeKodu, f:UrunFiltre, s:UrunSayfalama)
    aktif=1 → aktif ürünler (varsayılan), aktif=0 → pasif, None → hepsi
    kategori_id → belirli kategori filtresi
    urun_karti_id → belirli ürün kartı filtresi (tek ürün çekmek için)
    UrunSayfalama: BaslangicIndex (0-based), KayitSayisi, KayitSayisinaGoreGetir
    """
    c = _urun_client()
    ff = c.get_type("ns2:UrunFiltre")
    sf = c.get_type("ns2:UrunSayfalama")

    f_kwargs = {}
    if aktif is not None:
        f_kwargs["Aktif"] = aktif
    if kategori_id is not None:
        f_kwargs["KategoriID"] = kategori_id
    if urun_karti_id is not None:
        # Ticimax UrunFiltre tek-ürün filtresi (alan adı sürüme göre değişebilir,
        # ikisini de set ediyoruz; desteklenmeyen alan strict=False ile yok sayılır).
        f_kwargs["UrunKartiID"] = urun_karti_id
        f_kwargs["UrunID"] = urun_karti_id
    f = ff(**f_kwargs)

    # Y11: BaslangicIndex 0-tabanlı PAGE INDEX'tir (get_orders'ta doğrulandı — aynı sayfalama
    # fabrikası). Önceki `(page-1)*page_size` offset formülü her sayfada page_size kadar SAYFA
    # atlıyordu → katalogun çoğu hiç çekilmiyor ama senkron "başarılı" raporluyordu.
    s = sf(BaslangicIndex=(page - 1), KayitSayisi=page_size, KayitSayisinaGoreGetir=True)

    result = c.service.SelectUrun(UyeKodu=wscode, f=f, s=s)
    prods = _unwrap_list(result)
    logger.info(f"SelectUrun page={page} aktif={aktif} → {len(prods)} products")
    return prods


# ═══════════════ VARIANTS ══════════════════════════════════════

def get_variants(urun_karti_id: int, wscode: str = TICIMAX_API_KEY) -> List[Dict]:
    """SelectVaryasyon(..., f.UrunKartiID=urun_karti_id)"""
    c = _urun_client()
    try:
        vf   = c.get_type("ns2:VaryasyonFiltre")
        sf   = c.get_type("ns2:UrunSayfalama")
        ayar = c.get_type("ns2:SelectVaryasyonAyar")

        f = vf(UrunKartiID=urun_karti_id)
        s = sf(BaslangicIndex=0, KayitSayisi=500, KayitSayisinaGoreGetir=True)
        a = ayar()

        result = c.service.SelectVaryasyon(
            UyeKodu=wscode, f=f, s=s, varyasyonAyar=a)
        return _unwrap_list(result)
    except Exception as e:
        logger.error(f"SelectVaryasyon({urun_karti_id}): {e}")
        return []


# ═══════════════ IMAGES ════════════════════════════════════════

def get_product_images(urun_karti_id: int, wscode: str = TICIMAX_API_KEY) -> List[Dict]:
    """SelectUrunResim(..., request.UrunKartiId=urun_karti_id)"""
    c = _urun_client()
    try:
        rt  = c.get_type("ns2:UrunResimListeleRequest")
        req = rt(UrunKartiId=urun_karti_id)
        result = c.service.SelectUrunResim(UyeKodu=wscode, request=req)
        return _unwrap_list(result)
    except Exception as e:
        logger.error(f"SelectUrunResim({urun_karti_id}): {e}")
        return []


# ═══════════════ ASORTI STOCK ══════════════════════════════════

def get_assorted_stock(asorti_grup_id: int, wscode: str = TICIMAX_API_KEY) -> List[Dict]:
    """SelectAsortiMiktar(UyeKodu, asortiMiktarId)"""
    c = _urun_client()
    try:
        result = c.service.SelectAsortiMiktar(
            UyeKodu=wscode, asortiMiktarId=asorti_grup_id)
        return _unwrap_list(result)
    except Exception as e:
        logger.error(f"SelectAsortiMiktar({asorti_grup_id}): {e}")
        return []


# ═══════════════ ORDERS ════════════════════════════════════════

def get_orders(page: int = 1, page_size: int = 50,
               wscode: str = TICIMAX_API_KEY,
               start_date: Optional[str] = None,
               end_date: Optional[str] = None,
               exclude_marketplace: bool = True,
               only_with_phone: bool = True) -> List[Dict]:
    """SelectSiparis(UyeKodu, f:WebSiparisFiltre, s:WebSiparisSayfalama)
    
    Doğru WSDL alan adları (doğrulandı):
      SiparisTarihiBas, SiparisTarihiSon  → dateTime formatı (YYYY-MM-DDTHH:MM:SS)
      PazaryeriIhracat, IsMarketplace     → marketplace exclusion
      UrunGetir=True                      → satır kalemlerini de getir
      OdemeGetir=True                     → ödeme detaylarını da getir
      KampanyaGetir=True                  → kampanya detaylarını da getir
    
    exclude_marketplace=True ise, IsMarketplace=True olan siparişler ve
    Trendyol/HB/N11/AliExpress kaynaklı siparişler çekilmez (sadece site siparişleri).
    only_with_phone=True ise telefon numarası boş olan siparişler atlanır.
    """
    c = _siparis_client()
    try:
        ff = c.get_type("ns2:WebSiparisFiltre")
        sf = c.get_type("ns2:WebSiparisSayfalama")

        # CRITICAL: Ticimax int filter fields require -1 to mean "no filter".
        # Sending 0 means "filter for value 0" which returns nothing.
        # Therefore default ALL int filters to -1 here.
        fkw = {
            "UrunGetir": True,
            "OdemeGetir": True,
            "KampanyaGetir": True,
            "IptalEdilmisUrunler": False,
            "EntegrasyonAktarildi": -1,
            "SiparisDurumu": -1,
            "OdemeDurumu": -1,
            "OdemeTamamlandi": -1,
            "OdemeTipi": -1,
            "PaketlemeDurumu": -1,
            "PazaryeriIhracat": -1,  # default: hepsi (post-filter ile site only ayrılır)
            "SiparisID": -1,
            "TedarikciID": -1,
            "UyeID": -1,
            "EFaturaURL": -1,
            "KargoEntegrasyonTakipDurumu": -1,
            "KargoFirmaID": -1,
            "TeslimatMagazaID": -1,
        }
        if start_date:
            try:
                from datetime import datetime as _dt
                if "." in start_date:
                    d = _dt.strptime(start_date, "%d.%m.%Y")
                else:
                    d = _dt.strptime(start_date, "%Y-%m-%d")
                fkw["SiparisTarihiBas"] = d.strftime("%Y-%m-%dT00:00:00")
            except Exception:
                fkw["SiparisTarihiBas"] = start_date
        if end_date:
            try:
                from datetime import datetime as _dt
                if "." in end_date:
                    d = _dt.strptime(end_date, "%d.%m.%Y")
                else:
                    d = _dt.strptime(end_date, "%Y-%m-%d")
                fkw["SiparisTarihiSon"] = d.strftime("%Y-%m-%dT23:59:59")
            except Exception:
                fkw["SiparisTarihiSon"] = end_date

        f = ff(**fkw)
        # Ticimax NOT: BaslangicIndex aslında PAGE INDEX (0-based), KayitSayisi sayfa boyutu.
        # Yani 100 ürünlük sayfa için page=2 → BaslangicIndex=1 (offset değil!).
        # Yanlış formül `(page-1)*page_size` her sayfada 100 sayfa atlıyordu.
        s = sf(BaslangicIndex=(page-1), KayitSayisi=page_size, SiralamaDegeri="SiparisTarih", SiralamaYonu="DESC")

        result = c.service.SelectSiparis(UyeKodu=wscode, f=f, s=s)
        orders = _unwrap_list(result)

        # Post-filter: pazaryeri kaynaklı tüm siparişleri kesin ele
        marketplace_keywords = ("trendyol", "hepsiburada", "n11", "aliexpress",
                                "amazon", "ciceksepeti", "pttavm", "temu",
                                "pazarama", "gittigidiyor", "epttavm")
        filtered = []
        for o in orders:
            if not o:
                continue
            is_mp = bool(o.get("IsMarketplace") or o.get("PazaryeriIhracat"))
            kaynak = str(o.get("Kaynak") or "").lower()
            mp_butik = o.get("PazaryeriButikId") or 0
            if exclude_marketplace:
                if is_mp:
                    continue
                if any(kw in kaynak for kw in marketplace_keywords):
                    continue
                if mp_butik and int(mp_butik) > 0:
                    continue
            if only_with_phone:
                phone = (o.get("UyeTelefon") or "")
                # Telefon adres içinde olabilir, daha geniş kontrol için adres alanlarına bakalım
                if not phone:
                    kargo_adresi = o.get("KargoAdresi") or {}
                    if hasattr(kargo_adresi, "__values__"):
                        kargo_adresi = dict(kargo_adresi.__values__)
                    if isinstance(kargo_adresi, dict):
                        phone = (kargo_adresi.get("Telefon") or kargo_adresi.get("CepTelefonu") or "")
                if not phone:
                    fatura = o.get("FaturaAdresi") or {}
                    if hasattr(fatura, "__values__"):
                        fatura = dict(fatura.__values__)
                    if isinstance(fatura, dict):
                        phone = (fatura.get("Telefon") or fatura.get("CepTelefonu") or "")
                phone = str(phone or "").strip()
                if not phone:
                    continue
            filtered.append(o)

        logger.info(f"SelectSiparis page={page} → {len(orders)} raw, {len(filtered)} after filter (exclude_mp={exclude_marketplace}, only_phone={only_with_phone})")
        return filtered
    except Exception as e:
        logger.error(f"SelectSiparis: {e}")
        raise


def get_order_items(siparis_id: int, wscode: str = TICIMAX_API_KEY) -> List[Dict]:
    """SelectSiparisUrun(UyeKodu, siparisId, iptalEdilmisUrunler)"""
    c = _siparis_client()
    try:
        result = c.service.SelectSiparisUrun(
            UyeKodu=wscode, siparisId=siparis_id, iptalEdilmisUrunler=False)
        return _unwrap_list(result)
    except Exception as e:
        logger.error(f"SelectSiparisUrun({siparis_id}): {e}")
        return []


# ═══════════════ MEMBERS (ÜYE) ════════════════════════════════
# Ticimax UyeServis: SelectUyeler, SelectUyeAdres, SelectUyeIdByMailOrTel
# Üye fields: ID, Isim, Soyisim, Mail, CepTelefonu, Telefon, IlID, IlceID,
#             Il, Ilce, DogumTarihi, CinsiyetID, UyelikTarihi, SonGirisTarihi,
#             SonGirisIp, ParaPuan, KrediLimiti, MailIzin, SmsIzin,
#             KVKKSozlesmeOnay, UyelikSozlesmeOnay, MusteriKodu,
#             UyeTuru, UyeTuruID, UyelikTipi, UyelikTipiID, UyelikKaynagi,
#             Aktif, Onay, Meslek, OgrenimDurumu


def get_members(page: int = 1, page_size: int = 100,
                wscode: str = TICIMAX_API_KEY,
                only_active: bool = False,
                only_with_phone: bool = False) -> List[Dict]:
    """SelectUyeler(UyeKodu, filtre:UyeFiltre, sayfalama:UyeSayfalama)
    
    NOT: Ticimax int filtre alanları için "-1" = "filtre yok". 0 göndermek "değeri 0 olanları getir"
    anlamına gelir → bu yüzden default int alanları -1'e set ediyoruz.
    
    only_with_phone=True ise CepTelefonu/Telefon boş olan üyeler atlanır.
    """
    c = _uye_client()
    try:
        ff = c.get_type("ns2:UyeFiltre")
        sf = c.get_type("ns2:UyeSayfalama")

        # Default tüm int alanları -1 (filtre yok)
        fkw = {
            "Aktif": -1,
            "AlisverisYapti": -1,
            "Cinsiyet": -1,
            "IlID": -1,
            "IlceID": -1,
            "MailIzin": -1,
            "SmsIzin": -1,
            "UyeID": -1,
        }
        if only_active:
            fkw["Aktif"] = 1
        f = ff(**fkw)
        s = sf(KayitSayisi=page_size, SayfaNo=page,
               SiralamaDegeri="UyelikTarihi", SiralamaYonu="DESC")

        result = c.service.SelectUyeler(UyeKodu=wscode, filtre=f, sayfalama=s)
        members = _unwrap_list(result)

        if only_with_phone:
            filtered = []
            for m in members:
                if not m:
                    continue
                phone = (m.get("CepTelefonu") or m.get("Telefon") or "").strip()
                if not phone:
                    continue
                filtered.append(m)
            members = filtered

        logger.info(f"SelectUyeler page={page} → {len(members)} members")
        return members
    except Exception as e:
        logger.error(f"SelectUyeler: {e}")
        raise


def get_member_addresses(uye_id: int, wscode: str = TICIMAX_API_KEY) -> List[Dict]:
    """SelectUyeAdres(UyeKodu, adresId, uyeId)"""
    c = _uye_client()
    try:
        result = c.service.SelectUyeAdres(UyeKodu=wscode, adresId=0, uyeId=uye_id)
        return _unwrap_list(result)
    except Exception as e:
        logger.error(f"SelectUyeAdres({uye_id}): {e}")
        return []


def find_member_by_phone_or_email(phone_or_email: str,
                                  wscode: str = TICIMAX_API_KEY) -> Optional[int]:
    """SelectUyeIdByMailOrTel(UyeKodu, mailOrTel) → returns UyeID or None"""
    c = _uye_client()
    try:
        result = c.service.SelectUyeIdByMailOrTel(
            UyeKodu=wscode, mailOrTel=phone_or_email)
        if result:
            return int(result)
        return None
    except Exception as e:
        logger.error(f"SelectUyeIdByMailOrTel({phone_or_email}): {e}")
        return None
