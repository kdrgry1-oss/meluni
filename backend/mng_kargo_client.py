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
from datetime import datetime
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
    """Alan adını normalize eder: küçük harf, boşluk/alt çizgi/x0020 (XML boşluk kaçışı) atılır,
    Türkçe karakterler ASCII'ye çevrilir, birleşik işaretler (i̇) kaldırılır → 'gönderi̇ no' = 'gonderino'."""
    import unicodedata as _ud
    t = str(k).strip().replace("_x0020_", "").replace("x0020", "").replace("_", "").replace(" ", "")
    t = t.replace("İ", "i").replace("I", "i").replace("ı", "i").lower()
    t = _ud.normalize("NFKD", t)
    t = "".join(ch for ch in t if not _ud.combining(ch))
    return t


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


def _capped_session(max_bytes: int):
    """T10: SOAP/WSDL yanıtını bayt sınırıyla okuyan requests oturumu (yardımcı yoksa düz oturum)."""
    try:
        from security_fetch import make_capped_session
        return make_capped_session(max_bytes)
    except Exception:
        import requests
        return requests.Session()


def _soap_settings():
    from security.soap_safe import safe_settings
    return safe_settings()


def _soap_transport(**kw):
    """Şema içe aktarmaları YALNIZ MNG WSDL sunucusundan (security/soap_safe.py)."""
    from security.soap_safe import PinnedHostTransport, host_of
    return PinnedHostTransport(allowed_hosts={host_of(WSDL_URL)}, **kw)


def _get_client():
    global _client_cache
    if _client_cache is None:
        from zeep import Client, Settings
        from zeep.transports import Transport
        # GÜVENLİK (T10): huge_tree KAPALI (lxml varsayılan sınırları), DTD/varlık/harici kaynak
        # yasak, yanıt 50 MB ile sınırlı (security_fetch.make_capped_session).
        _client_cache = Client(
            WSDL_URL,
            settings=_soap_settings(),
            transport=_soap_transport(timeout=15, operation_timeout=25,
                                session=_capped_session(50 * 1024 * 1024)),
        )
    return _client_cache


def list_operations() -> list:
    """WSDL'deki TÜM operasyon adları (teşhis: tarih bazlı gönderi listesi var mı?)."""
    try:
        c = _get_client()
        names = set()
        for svc in c.wsdl.services.values():
            for port in svc.ports.values():
                names.update(getattr(port.binding, "_operations", {}).keys())
        return sorted(names)
    except Exception as e:
        return [f"okunamadı: {str(e)[:80]}"]


_client_long_cache = None


def _get_client_long():
    """Uzun süren rapor operasyonları için (MusteriOzelRapor) 90 sn zaman aşımı olan istemci."""
    global _client_long_cache
    if _client_long_cache is None:
        from zeep import Client, Settings
        from zeep.transports import Transport
        # MusteriOzelRapor ÇOK büyük yanıt döndürür (8 günde ~55 bin satır) ama her satır/alan küçük
        # bir XML düğümüdür → huge_tree GEREKMEZ (lxml'in ~10 MB sınırı TEK metin düğümü içindir).
        # Toplam yanıt 256 MB ile sınırlı; DTD/varlık/harici kaynak yasak.
        _client_long_cache = Client(
            WSDL_URL,
            settings=_soap_settings(),
            transport=_soap_transport(timeout=30, operation_timeout=90,
                                session=_capped_session(256 * 1024 * 1024)),
        )
    return _client_long_cache


def _op_param_names(c, op_name: str) -> list:
    """Bir SOAP operasyonunun giriş parametre adları (WSDL'den, çalışma anında)."""
    try:
        op = c.service._binding._operations[op_name]
        body = op.input.body
        elems = getattr(body.type, "elements", None) or []
        return [name for name, _el in elems]
    except Exception:
        try:
            sig = c.service._binding._operations[op_name].input.signature()
            return [part.split(":")[0].strip() for part in str(sig).split(",") if part.strip()]
        except Exception:
            return []


def _flatten_row(row) -> Dict:
    """Bir gönderi satırını {normalize_key: str} sözlüğüne düzleştirir (iç dict'ler dahil)."""
    out: Dict[str, str] = {}

    def walk(o, prefix=""):
        if isinstance(o, dict):
            for k, v in o.items():
                if isinstance(v, (dict, list)):
                    walk(v, prefix)
                elif v not in (None, ""):
                    out.setdefault(_norm_key(k), str(v).strip())
        elif isinstance(o, list):
            for it in o:
                walk(it, prefix)
    walk(row)
    return out


def _largest_row_list(obj) -> list:
    """Serialize edilmiş yanıtta en çok dict içeren listeyi (gönderi satırları) bulur."""
    best: list = []

    def walk(o):
        nonlocal best
        if isinstance(o, list):
            dicts = [x for x in o if isinstance(x, dict)]
            if len(dicts) > len(best):
                best = dicts
            for it in o:
                walk(it)
        elif isinstance(o, dict):
            for v in o.values():
                walk(v)
    walk(obj)
    return best


def list_shipments_by_date(*, username: str, password: str, start, end, dates=None, customer_codes=None,
                           preferred_rapor: str = "", discover_rapor=None) -> Dict:
    """KargoBilgileriByTarih / FaturaSiparisListesiByTarih → gönderi listesi.
    Amaç: kuryenin bizim referansımız yerine kendi (RE-…) referansıyla açtığı paketleri alıcı
    adına göre bulmak. Alan adları WSDL sürümüne göre değiştiği için parametreler ve satır
    alanları çalışma anında sezgisel eşlenir.
      • İki tarih parametresi alan operasyon → tek çağrı (start..end)
      • TEK tarih parametresi alan operasyon (FaturaSiparisListesiByTarih: pSiparisTarih) →
        `dates` listesindeki her gün için ayrı çağrı (günlük sorgu limitini korumak için az gün).
    Dönüş: {ok, method, rows:[{tracking,name,ref,date,mng_no,status,phone,raw_keys}], diag:{op:{params,error}}}"""
    c = _get_client()
    from zeep.helpers import serialize_object
    fmts = ["%d.%m.%Y", "%Y-%m-%d"]
    diag: Dict[str, Dict] = {}
    all_rows: list = []
    used_method = ""

    def _parse_rows(ser) -> list:
        rows_raw = _largest_row_list(ser)
        rows = []
        for rr in rows_raw:
            f = _flatten_row(rr)

            def pick(*preds):
                for k, v in f.items():
                    if all(p in k for p in preds):
                        return v
                return ""
            import re as _re
            _trk = pick("gonderino") or pick("gonderi", "no") or pick("takipno") or pick("barkod")
            _mng_ref = ""
            if _trk and not _re.fullmatch(r"\d{10,14}", _trk.strip()):
                # "RE - 329844" gibi MNG gönderi referansı → takip no DEĞİL; ayrı sakla
                _mng_ref, _trk = _trk.strip(), ""
            rows.append({"tracking": _trk, "mng_ref": _mng_ref,
                         "sender_no": pick("gonmusterino") or pick("gondericimusterino") or pick("gonderici", "no"),
                         "sender_name": pick("gonmusteriadi") or pick("gondericimusteriadi") or pick("gondericiadi") or pick("gonderici", "ad"),
                         "name": pick("aliciadi") or pick("almusteriadi") or pick("alici", "ad") or pick("alici", "unvan") or pick("alicimusteri") or pick("alici"),
                         "ref": f.get("siparisno") or f.get("referansno") or pick("referans") or pick("siparisno") or pick("rfanlasmano"),
                         "date": pick("gondericikis", "tarih") or pick("gonderitarihi") or pick("kargostatu", "tarih") or pick("siparistarihi") or pick("tarih"),
                         "mng_no": pick("mngsiparisno") or pick("mng", "no"),
                         "status": pick("statu", "aciklama") or pick("statu") or pick("durum"),
                         "phone": pick("telcep") or pick("alici", "tel") or pick("tel"),
                         "raw_keys": sorted(f.keys())[:40]})
        return rows

    for op_name in ("KargoBilgileriByTarih", "MusteriOzelRapor", "FaturaSiparisListesiByTarih"):
        names = _op_param_names(c, op_name)
        d = diag.setdefault(op_name, {"params": names, "error": "", "calls": 0, "rows": 0})
        if not names:
            d["error"] = "parametreler okunamadı"
            continue
        date_slots = [n for n in names if "tarih" in n.lower() or "date" in n.lower()]
        base = {}
        cust_slot = ""
        rapor_slot = ""
        for n in names:
            ln = n.lower()
            if n in date_slots:
                continue
            if "musteri" in ln and "no" in ln:
                cust_slot = n
                base[n] = ""
            elif "kullanici" in ln or "user" in ln:
                base[n] = username
            elif "sifre" in ln or "pass" in ln:
                base[n] = password
            elif "raportype" in ln or "rapor" in ln:
                base[n] = "1"
                rapor_slot = n
            elif "altfirma" in ln:
                base[n] = "0"
            else:
                base[n] = ""
        # pMusteriNo isteyen operasyon: müşteri kodu adayları (ayarlardaki kod, kullanıcı adı)
        cust_candidates = [x for x in (customer_codes or []) if x] or [username]
        if not cust_slot:
            cust_candidates = [""]
        # Rapor tipi / alt firma bayrağı bilinmiyor → boş sonuçta kombinasyonlar sırayla denenir
        alt_slot = next((n for n in names if "altfirma" in n.lower()), "")
        if rapor_slot and "raporno" in rapor_slot.lower():
            # MusteriOzelRapor: rapor no keşfi (1 = şube listesi çıktı; gönderi listesi hangisi?)
            # 1 = şube listesi, 15 = sipariş listesi (takip no yok), 2-6/8/9/11 tanımsız; 7/10/12/13 zaman aşımı
            # (büyük rapor → gönderi listesi olabilir) → uzun zaman aşımı + kısa aralıkla önce onlar denenir.
            # preferred (7: gönderi listesi, yalnız RE/RG referansı) + bu turda keşfedilecek 1-2 yeni rapor no
            rapor_variants = ([preferred_rapor] if preferred_rapor else ["7"]) + [str(x) for x in (discover_rapor or []) if str(x) != preferred_rapor]
        else:
            rapor_variants = ["1", "2"] if rapor_slot else [""]     # KargoBilgileriByTarih: hep boş döndü → az dene
        alt_variants = ["0"] if alt_slot else [""]
        cust_candidates = [(cc, rv, av) for cc in dict.fromkeys(cust_candidates) for av in alt_variants for rv in rapor_variants]
        # Çağrı planı: iki tarih → (start,end); tek tarih → her gün ayrı
        if len(date_slots) >= 2:
            ds = sorted(date_slots, key=lambda n: (0 if any(k in n.lower() for k in ("bas", "ilk", "start", "from")) else
                                                    (2 if any(k in n.lower() for k in ("bit", "son", "end", "to")) else 1)))
            # Rapor keşfi sırasında (rapor no bilinmiyor) kısa aralık: 8 gün; bilinince tam aralık
            _is_rapor = "raporno" in (rapor_slot or "").lower()
            # MusteriOzelRapor çok büyük (8 günde 55 bin satır) → her zaman kısa pencere (bekleyen günler)
            _st = start if not _is_rapor else max(start, end - __import__("datetime").timedelta(days=(10 if preferred_rapor else 8)))
            plans = [{ds[0]: _st, ds[-1]: end}]
        elif len(date_slots) == 1:
            _dl = list(dates or [start])
            # Parametre denemesi ilk planla yapılır → ilk plan GEÇMİŞ bir gün olsun (yarın boş döner)
            _today0 = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
            _past = [dt for dt in _dl if dt.replace(tzinfo=None) < _today0]
            _dl = (_past[:1] + [dt for dt in _dl if dt not in _past[:1]]) if _past else _dl
            plans = [{date_slots[0]: dt} for dt in _dl]
        else:
            plans = [{}]
        got_any = False
        for cust, rv, av in cust_candidates:
          if cust_slot:
              base[cust_slot] = cust
          if got_any and rapor_slot and "raporno" in rapor_slot.lower() and d.get("rapor_no_tracking"):
              break   # takip no'lu rapor bulundu → daha fazla deneme
          if rapor_slot:
              base[rapor_slot] = rv
          if alt_slot:
              base[alt_slot] = av
          if cust_slot or rapor_slot:
              d.setdefault("cust_tried", []).append(f"{str(cust)[:4]}…/rapor={rv}/alt={av}")
          if got_any and not (rapor_slot and "raporno" in rapor_slot.lower()):
              break
          _variant_tag = f"rapor={rv}/alt={av}"
          for fmt in (fmts[:1] if cust_slot else fmts):
              errs = []
              rows_here = []
              for plan in (plans[:1] if cust_slot else plans):   # parametre denemesinde tek gün
                  kwargs = dict(base)
                  for k, dt in plan.items():
                      kwargs[k] = dt.strftime(fmt)
                  try:
                      d["calls"] += 1
                      _cl = _get_client_long() if op_name == "MusteriOzelRapor" else c
                      r = getattr(_cl.service, op_name)(**kwargs)
                      ser = serialize_object(r)
                      rows = _parse_rows(ser)
                      if rows:
                          rows_here.extend(rows)
                      else:
                          errs.append(str(ser)[:140])
                  except Exception as e:
                      errs.append(str(e)[:120])
              if rows_here:
                  _keys = rows_here[0].get("raw_keys") or []
                  _has_trk = any(rows_here[i].get("tracking") for i in range(min(200, len(rows_here))))
                  d.setdefault("by_variant", {})
                  d["by_variant"][_variant_tag] = f"{len(rows_here)} satır, takipNo={'VAR' if _has_trk else 'yok'}, alanlar: " + ",".join(_keys[:24])
                  # PII'siz istatistik: gönderici çeşitliliği, 'meluni' geçen satır, tarih aralığı, takip no'lu satır
                  try:
                      import unicodedata as _ud2
                      def _nm(x):
                          x = str(x or "").replace("ı", "i").replace("İ", "i").lower()
                          return "".join(ch for ch in _ud2.normalize("NFKD", x) if not _ud2.combining(ch))
                      _senders = {}
                      for r in rows_here:
                          sn = _nm(r.get("sender_name"))
                          _senders[sn] = _senders.get(sn, 0) + 1
                      _top = sorted(_senders.items(), key=lambda x: -x[1])[:3]
                      _dates = sorted({str(r.get("date") or "")[:10] for r in rows_here if r.get("date")})
                      d.setdefault("rapor_stats", {})[rv] = {
                          "rows": len(rows_here), "senders_distinct": len(_senders),
                          "senders_top": [f"{k[:3]}***({len(k)}) x{v}" for k, v in _top],
                          "rows_meluni": sum(v for k, v in _senders.items() if "meluni" in k),
                          "rows_with_tracking": sum(1 for r in rows_here if r.get("tracking")),
                          "rows_with_name": sum(1 for r in rows_here if r.get("name")),
                          "date_min": _dates[0] if _dates else "", "date_max": _dates[-1] if _dates else "",
                          "keys": _keys[:60]}
                  except Exception as _se:
                      d.setdefault("rapor_stats", {})[rv] = {"error": str(_se)[:80]}
                  d.setdefault("rapor_keys", {})[rv] = {"rows": len(rows_here), "has_tracking": _has_trk, "keys": _keys[:60]}
                  if _has_trk:
                      d["rapor_no_tracking"] = rv
                  elif rv != (preferred_rapor or "7"):
                      # Keşif turunda gönderi/takip alanı olmayan rapor → satırları kullanma
                      rows_here = []
                      errs = errs or ["takip no alanı yok"]
              if errs:
                  d.setdefault("by_variant", {})
                  if len(d["by_variant"]) < 40:
                      d["by_variant"].setdefault(_variant_tag, errs[0][:90])
              if rows_here:
                  d["rapor_no"] = rv
                  # Kimlik denemesi (2 gün) başarılıysa kalan günleri de aynı kimlikle çek
                  if cust_slot and len(plans) > 1:
                      for plan in plans[1:]:
                          kwargs = dict(base)
                          for k, dt in plan.items():
                              kwargs[k] = dt.strftime(fmt)
                          try:
                              d["calls"] += 1
                              rows_here.extend(_parse_rows(serialize_object(getattr(c.service, op_name)(**kwargs))))
                          except Exception as e:
                              errs.append(str(e)[:120])
                  all_rows.extend(rows_here)
                  d["rows"] += len(rows_here)
                  d["fmt"] = fmt
                  got_any = True
                  if errs:
                      d["error"] = errs[0][:200]
                  break
              d["error"] = (errs[0] if errs else "satır yok")[:200]
              d.setdefault("errors_seen", [])
              if errs and errs[0][:100] not in d["errors_seen"]:
                  d["errors_seen"].append(errs[0][:100])
              # Kimlik hatası formatla ilgili değil → diğer formatı deneme
              if any("KULLANICI" in x.upper() or "SIFRE" in x.upper() for x in errs):
                  break
        if got_any and not used_method:
            used_method = op_name
    for extra_op in ("KargoBilgileriByReferans", "GonderiTeslimatProblemleri", "PaletSiparisListesi"):
        if extra_op not in diag:
            diag[extra_op] = {"params": _op_param_names(c, extra_op), "calls": 0, "rows": 0, "error": "(çağrılmadı)"}
    # Aynı takip no birden çok operasyondan gelirse tekilleştir
    seen, uniq = set(), []
    for r in all_rows:
        key = r.get("tracking") or (r.get("ref") + "|" + r.get("name"))
        if key in seen:
            continue
        seen.add(key)
        uniq.append(r)
    return {"ok": bool(uniq), "method": used_method, "rows": uniq, "diag": diag,
            "error": "; ".join(f"{k}: {v.get('error')}" for k, v in diag.items() if v.get("error"))[:500]}


def resolve_mng_ref(*, username: str, password: str, mng_ref: str, customer_codes=None) -> Dict:
    """MNG gönderi referansından ("RE - 329844") 12 haneli kargo takip/barkod no'sunu bulmaya çalışır.
    Denenen: KargoBilgileriByReferans(pGonderiNo=…), KargoTakipByReferans(pReferansId=…), FaturaSiparisListesi(pSiparisNo=…).
    Dönüş: {ok, tracking, method, tried:[…], keys:[…]}"""
    import re as _re
    c = _get_client()
    from zeep.helpers import serialize_object
    raw = str(mng_ref or "").strip()
    digits = _re.sub(r"\D", "", raw)
    variants = list(dict.fromkeys([raw, raw.replace(" ", ""), raw.replace(" - ", "-"), digits]))
    tried = []
    keys_seen = []

    def _find_tracking(ser) -> str:
        f = _flatten_row(ser if isinstance(ser, (dict, list)) else {"v": ser})
        for k, v in f.items():
            if v and _re.fullmatch(r"\d{10,14}", str(v).strip()) and any(t in k for t in ("barkod", "takip", "gonderino", "kargono", "gonderi")):
                return str(v).strip()
        # alan adı uymasa da 12 haneli tek değer varsa onu al
        cands = [str(v).strip() for v in f.values() if v and _re.fullmatch(r"\d{12}", str(v).strip())]
        return cands[0] if len(set(cands)) == 1 else ""

    for op_name in ("KargoBilgileriByReferans", "KargoTakipByReferans", "FaturaSiparisListesi"):
        names = _op_param_names(c, op_name)
        if not names:
            continue
        for v in variants:
            if not v:
                continue
            for cust in ([x for x in (customer_codes or []) if x] or [username]):
                kwargs = {}
                for n in names:
                    ln = n.lower()
                    if "musteri" in ln and "no" in ln:
                        kwargs[n] = cust
                    elif "kullanici" in ln or "user" in ln:
                        kwargs[n] = username
                    elif "sifre" in ln or "pass" in ln:
                        kwargs[n] = password
                    elif "gonderino" in ln or "referans" in ln or ("siparis" in ln and "no" in ln):
                        kwargs[n] = v
                    elif "rapor" in ln:
                        kwargs[n] = "1"
                    else:
                        kwargs[n] = ""
                try:
                    r = getattr(c.service, op_name)(**kwargs)
                    ser = serialize_object(r)
                    trk = _find_tracking(ser)
                    fk = sorted(_flatten_row(ser if isinstance(ser, (dict, list)) else {}).keys())[:25]
                    if fk and fk not in keys_seen:
                        keys_seen.append(fk)
                    tried.append(f"{op_name}({v[:14]}): {'OK ' + trk if trk else str(ser)[:80]}")
                    if trk:
                        return {"ok": True, "tracking": trk, "method": op_name, "variant": v, "tried": tried, "keys": keys_seen}
                except Exception as e:
                    tried.append(f"{op_name}({v[:14]}): {str(e)[:80]}")
                if "musteri" not in "".join(names).lower():
                    break   # müşteri no parametresi yoksa tek deneme yeter
    return {"ok": False, "tracking": "", "method": "", "tried": tried[:12], "keys": keys_seen[:3]}


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
    platform_adi: str = "MELUNİ",
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
            pChIcerik=tr_safe(icerik)[:200],  # DENETİM A-1: MNG pChIcerik limiti 200 (E022)
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
