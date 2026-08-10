"""
FACETTE sabit öznitelik varsayılanları — TÜM pazaryerleri (Trendyol / Hepsiburada / Temu) için ORTAK.

Tasarım:
- Pazaryerine BAĞIMSIZ. Değerler İSİMLE tutulur (value_id değil); her pazaryeri push
  çekirdeği bu değeri kendi value_id'sine ADA GÖRE çözer (TR ↔ Türkiye gibi).
- Push'a GAP-FILL olarak eklenir: ürün/varyant/kategori o özelliği ZATEN taşıyorsa
  DOKUNULMAZ; yalnız boş kalan özelliğe FACETTE sabiti yazılır.
- Eşleşme yoksa (kategoride o özellik yoksa / değer enum'a uymuyorsa) mevcut akış
  AYNEN korunur, sessizce atlanır → hiçbir push'u bozmaz.
- Hiçbir pazaryeri modülüne import bağımlılığı YOKTUR (yalnız bu modül dışa verir).
"""

import re as _re

# Üretici / İthalatçı (GPSR / "Ürün Denetim Bilgileri") tek kaynak.
FACETTE_COMPANY = {
    "company_name": "FACETTE DIŞ TİCARET A.Ş.",
    "email": "info@facette.com.tr",
    "address": "İkitelli O.S.B. İmsan San. Sit. D BLOK NO:3",
}

# Düz sabitler (ada göre). Üretici/İthalatçı GPSR alanları aşağıdaki resolver ile ayrı ele alınır.
FACETTE_FIXED_ATTR_DEFAULTS = {
    "Menşei": "Türkiye",
    "Cinsiyet": "Kadın",
    "Yaş Grubu": "Yetişkin",
    "Ortam": "Casual/Günlük",
    "Koleksiyon": "Casual/Günlük",
    "Ek Özellik": "Yok",
    "Kutu Durumu": "Kutu Yok",
    "Persona": "Fashion Forward",
    "Performans": "Cool & Comfort",
    "Sürdürülebilirlik Detayı": "Hayır",
}


def _norm(s) -> str:
    """Türkçe-duyarsız normalize (eşleştirme için). İ/ı/ş/ğ/ü/ö/ç + birleşik nokta."""
    s = (s or "").casefold()
    for a, b in (("ı", "i"), ("İ", "i"), ("ş", "s"), ("ğ", "g"),
                 ("ü", "u"), ("ö", "o"), ("ç", "c"), ("\u0307", "")):
        s = s.replace(a, b)
    return " ".join(s.split())


_FIXED_NORM = {_norm(k): v for k, v in FACETTE_FIXED_ATTR_DEFAULTS.items()}


def facette_company_value(attr_name):
    """Üretici/İthalatçı (GPSR) bir özellik adını FACETTE şirket bilgisine eşler.
    'Üretici Adı', 'Birincil/İkincil/Üçüncül İthalatçı Adı' → firma adı;
    '...Mail...' → e-posta; '...Adres...' → adres. Değilse None (akış bozulmaz).
    """
    field = company_field_for_attr(attr_name)
    return FACETTE_COMPANY[field] if field else None


def company_field_for_attr(attr_name):
    """GPSR üretici/ithalatçı özellik adının hangi şirket alanına denk geldiğini döndürür:
    'company_name' | 'email' | 'address' | None.
    İSİM-EŞLEME TEK KAYNAK — hem push gap-fill (facette_company_value) hem kategori
    şirket-doldurma (category_mapping._resolve_company_value) bunu kullanır.
    """
    if not attr_name:
        return None
    nm = _norm(attr_name)  # İthalatçı → "ithalatci" (combining-dot temizlenir)
    if not _re.search(r"uretici|ithalatc|imalatc", nm):
        return None
    if "mail" in nm or "posta" in nm or "email" in nm:
        return "email"
    if "adres" in nm:
        return "address"
    if _re.search(r"\bad[i]\b|\bism", nm) or "unvan" in nm or "firma" in nm \
            or nm in ("uretici", "ithalatci"):
        return "company_name"
    return None  # tanımadığımız alt-alan (telefon/vergi no…) → dokunma


def facette_fixed_value_for(attr_name):
    """Pazaryeri özellik ADINA FACETTE sabit varsayılanını döndürür; eşleşmezse None.
    Push çekirdekleri yalnız BOŞ kalan özellik için çağırır (gap-fill)."""
    if not attr_name:
        return None
    cv = facette_company_value(attr_name)
    if cv:
        return cv
    return _FIXED_NORM.get(_norm(attr_name))
