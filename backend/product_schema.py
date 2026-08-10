"""
Ticimax 113 sütunluk Ürün export şeması.

Bu modül; ürün kartı arayüzünde tüm Ticimax alanlarının düzenlenebilir
olarak gösterilmesi (gruplu) ve Excel import sırasında değerlerin
normalize edilmesi için tek doğruluk kaynağıdır.

Veriler ürün dökümanında `ticimax_fields` (Dict[str, Any]) altında,
orijinal Ticimax kolon adlarıyla saklanır.
"""
from __future__ import annotations
from typing import Any, List, Dict

# Orijinal Ticimax kolon sırası (113 alan)
ORDERED_COLUMNS: List[str] = [
    "URUNKARTIID", "URUNID", "STOKKODU", "VARYASYONKODU", "BARKOD", "GTIPKODU",
    "URUNADI", "ONYAZI", "ACIKLAMA", "SATISBIRIMI", "ANAHTARKELIME",
    "ADWORDSACIKLAMA", "ADWORDSKATEGORI", "ADWORDSTIP", "OZELALAN1", "OZELALAN2",
    "BREADCRUMBKAT", "OZELALAN3", "OZELALAN4", "OZELALAN5",
    "SEO_SAYFABASLIK", "SEO_ANAHTARKELIME", "SEO_SAYFAACIKLAMA",
    "PUANDEGER", "PUANYUZDE", "MARKA", "TEDARIKCI", "MAKSTAKSITSAYISI",
    "VITRIN", "YENIURUN", "FIRSATURUNU", "FBSTOREGOSTER", "UCRETSIZKARGO",
    "STOKADEDI", "KONSINYESTOKADEDI", "ALISFIYATI", "PIYASAFIYATI",
    "SATISFIYATI", "INDIRIMLIFIYAT", "SURELIINDIRIMOZELLIK",
    "UYETIPIFIYAT1", "UYETIPIFIYAT2", "UYETIPIFIYAT3", "UYETIPIFIYAT4",
    "UYETIPIFIYAT5", "UYETIPIFIYAT6", "UYETIPIFIYAT7", "UYETIPIFIYAT8",
    "UYETIPIFIYAT9", "UYETIPIFIYAT10", "UYETIPIFIYAT11", "UYETIPIFIYAT12",
    "UYETIPIFIYAT13", "UYETIPIFIYAT14", "UYETIPIFIYAT15", "UYETIPIFIYAT16",
    "UYETIPIFIYAT17", "UYETIPIFIYAT18", "UYETIPIFIYAT19", "UYETIPIFIYAT20",
    "KDVORANI", "TEDARIKCIKOMISYONORANI", "KDVDAHIL", "PARABIRIMI",
    "KARGOAGIRLIGI", "KARGOAGIRLIGIYURTDISI", "URUNAGIRLIGI",
    "URUNGENISLIK", "URUNDERINLIK", "URUNYUKSEKLIK", "KARGOUCRETI",
    "EKSISTOKADEDI", "PARABIRIMI_ID", "KARTAKTIF", "URUNAKTIF",
    "UYEALIMMIN", "UYEALIMMAKS", "BAYIALIMMIN", "BAYIALIMMAKS",
    "URUNKAPIDAODEMEYASAKLI", "YEMEKKARTIODEMEYASAKLILISTESI",
    "SEPETUCRETSIZKARGO", "ENTEGRASYONGUNCELLEMEAKTIF", "VARYASYON",
    "TAHMINITESLIMSURESI", "TAHMINITESLIMSURESIGOSTER",
    "TAHMINITESLIMSURESIAYNIGUN", "TAHMINITESLIMSURESITARIH",
    "MARKETPLACEAKTIF",
    "MARKETPLACEGRUP1EKSISTOKADEDI", "MARKETPLACEGRUP1KOMISYONTIP", "MARKETPLACEGRUP1KOMISYONDEGER",
    "MARKETPLACEAKTIF2",
    "MARKETPLACEGRUP2EKSISTOKADEDI", "MARKETPLACEGRUP2KOMISYONTIP", "MARKETPLACEGRUP2KOMISYONDEGER",
    "MARKETPLACEAKTIF3",
    "MARKETPLACEGRUP3EKSISTOKADEDI", "MARKETPLACEGRUP3KOMISYONTIP", "MARKETPLACEGRUP3KOMISYONDEGER",
    "MARKETPLACEAKTIF4",
    "MARKETPLACEGRUP4EKSISTOKADEDI", "MARKETPLACEGRUP4KOMISYONTIP", "MARKETPLACEGRUP4KOMISYONDEGER",
    "MARKETPLACEAKTIF5",
    "MARKETPLACEGRUP5EKSISTOKADEDI", "MARKETPLACEGRUP5KOMISYONTIP", "MARKETPLACEGRUP5KOMISYONDEGER",
    "YAYINTARIHI", "KAPASITE", "PUANKULLANIMIIPTALAKTIF",
    "PAZARYERIAKTIFLISTESI", "EKLEMETARIHI",
]

BOOL_COLS = {
    "VITRIN", "YENIURUN", "FIRSATURUNU", "FBSTOREGOSTER", "UCRETSIZKARGO",
    "KDVDAHIL", "KARTAKTIF", "URUNAKTIF", "URUNKAPIDAODEMEYASAKLI",
    "SEPETUCRETSIZKARGO", "ENTEGRASYONGUNCELLEMEAKTIF",
    "TAHMINITESLIMSURESIGOSTER", "TAHMINITESLIMSURESIAYNIGUN",
    "MARKETPLACEAKTIF", "MARKETPLACEAKTIF2", "MARKETPLACEAKTIF3",
    "MARKETPLACEAKTIF4", "MARKETPLACEAKTIF5", "PUANKULLANIMIIPTALAKTIF",
}

TEXTAREA_COLS = {
    "ONYAZI", "ACIKLAMA", "ADWORDSACIKLAMA", "SEO_SAYFAACIKLAMA",
    "SEO_ANAHTARKELIME", "ANAHTARKELIME",
}

NUMBER_COLS = {
    "URUNKARTIID", "URUNID", "MAKSTAKSITSAYISI", "STOKADEDI", "KONSINYESTOKADEDI",
    "EKSISTOKADEDI", "ALISFIYATI", "PIYASAFIYATI", "SATISFIYATI", "INDIRIMLIFIYAT",
    "KDVORANI", "TEDARIKCIKOMISYONORANI", "KARGOAGIRLIGI", "KARGOAGIRLIGIYURTDISI",
    "URUNAGIRLIGI", "URUNGENISLIK", "URUNDERINLIK", "URUNYUKSEKLIK", "KARGOUCRETI",
    "PARABIRIMI_ID", "UYEALIMMIN", "UYEALIMMAKS", "BAYIALIMMIN", "BAYIALIMMAKS",
    "TAHMINITESLIMSURESI", "KAPASITE", "PUANDEGER", "PUANYUZDE",
} | {f"UYETIPIFIYAT{i}" for i in range(1, 21)} | {
    f"MARKETPLACEGRUP{g}{suf}"
    for g in range(1, 6)
    for suf in ("EKSISTOKADEDI", "KOMISYONTIP", "KOMISYONDEGER")
}

# Salt-okunur kimlik alanları (import kaynağı, elle değiştirilmez)
READONLY_COLS = {"URUNID", "EKLEMETARIHI"}  # URUNKARTIID artik elle duzenlenebilir

LABELS: Dict[str, str] = {
    "URUNKARTIID": "Ürün Kart ID", "URUNID": "Varyant Ürün ID",
    "STOKKODU": "Stok Kodu", "VARYASYONKODU": "Varyasyon Kodu",
    "BARKOD": "Barkod", "GTIPKODU": "GTİP Kodu", "URUNADI": "Ürün Adı",
    "ONYAZI": "Ön Yazı", "ACIKLAMA": "Açıklama", "SATISBIRIMI": "Satış Birimi",
    "ANAHTARKELIME": "Anahtar Kelime", "ADWORDSACIKLAMA": "Adwords Açıklama",
    "ADWORDSKATEGORI": "Adwords Kategori", "ADWORDSTIP": "Adwords Tip",
    "OZELALAN1": "Özel Alan 1", "OZELALAN2": "Özel Alan 2",
    "OZELALAN3": "Özel Alan 3", "OZELALAN4": "Özel Alan 4", "OZELALAN5": "Özel Alan 5",
    "BREADCRUMBKAT": "Breadcrumb Kategori",
    "SEO_SAYFABASLIK": "SEO Sayfa Başlığı", "SEO_ANAHTARKELIME": "SEO Anahtar Kelime",
    "SEO_SAYFAACIKLAMA": "SEO Sayfa Açıklaması",
    "PUANDEGER": "Puan Değeri", "PUANYUZDE": "Puan Yüzdesi",
    "MARKA": "Marka", "TEDARIKCI": "Tedarikçi", "MAKSTAKSITSAYISI": "Maks. Taksit Sayısı",
    "VITRIN": "Vitrin", "YENIURUN": "Yeni Ürün", "FIRSATURUNU": "Fırsat Ürünü",
    "FBSTOREGOSTER": "Facebook Mağaza Göster", "UCRETSIZKARGO": "Ücretsiz Kargo",
    "STOKADEDI": "Stok Adedi", "KONSINYESTOKADEDI": "Konsinye Stok Adedi",
    "ALISFIYATI": "Alış Fiyatı", "PIYASAFIYATI": "Piyasa Fiyatı",
    "SATISFIYATI": "Satış Fiyatı", "INDIRIMLIFIYAT": "İndirimli Fiyat",
    "SURELIINDIRIMOZELLIK": "Süreli İndirim Özelliği",
    "KDVORANI": "KDV Oranı (%)", "TEDARIKCIKOMISYONORANI": "Tedarikçi Komisyon Oranı",
    "KDVDAHIL": "KDV Dahil", "PARABIRIMI": "Para Birimi", "PARABIRIMI_ID": "Para Birimi ID",
    "KARGOAGIRLIGI": "Kargo Ağırlığı (kg)", "KARGOAGIRLIGIYURTDISI": "Kargo Ağırlığı Yurtdışı",
    "URUNAGIRLIGI": "Ürün Ağırlığı", "URUNGENISLIK": "Ürün Genişlik",
    "URUNDERINLIK": "Ürün Derinlik", "URUNYUKSEKLIK": "Ürün Yükseklik",
    "KARGOUCRETI": "Kargo Ücreti", "EKSISTOKADEDI": "Eksi Stok Adedi",
    "KARTAKTIF": "Kart Aktif", "URUNAKTIF": "Ürün Aktif",
    "UYEALIMMIN": "Üye Alım Min", "UYEALIMMAKS": "Üye Alım Maks",
    "BAYIALIMMIN": "Bayi Alım Min", "BAYIALIMMAKS": "Bayi Alım Maks",
    "URUNKAPIDAODEMEYASAKLI": "Kapıda Ödeme Yasaklı",
    "YEMEKKARTIODEMEYASAKLILISTESI": "Yemek Kartı Ödeme Yasaklı Listesi",
    "SEPETUCRETSIZKARGO": "Sepet Ücretsiz Kargo",
    "ENTEGRASYONGUNCELLEMEAKTIF": "Entegrasyon Güncelleme Aktif",
    "VARYASYON": "Varyasyon", "TAHMINITESLIMSURESI": "Tahmini Teslim Süresi (gün)",
    "TAHMINITESLIMSURESIGOSTER": "Teslim Süresi Göster",
    "TAHMINITESLIMSURESIAYNIGUN": "Aynı Gün Teslim",
    "TAHMINITESLIMSURESITARIH": "Tahmini Teslim Tarihi",
    "MARKETPLACEAKTIF": "Pazaryeri Aktif (Grup 1)",
    "MARKETPLACEAKTIF2": "Pazaryeri Aktif (Grup 2)",
    "MARKETPLACEAKTIF3": "Pazaryeri Aktif (Grup 3)",
    "MARKETPLACEAKTIF4": "Pazaryeri Aktif (Grup 4)",
    "MARKETPLACEAKTIF5": "Pazaryeri Aktif (Grup 5)",
    "YAYINTARIHI": "Yayın Tarihi", "KAPASITE": "Kapasite",
    "PUANKULLANIMIIPTALAKTIF": "Puan Kullanımı İptal Aktif",
    "PAZARYERIAKTIFLISTESI": "Pazaryeri Aktif Listesi", "EKLEMETARIHI": "Eklenme Tarihi",
}
for _g in range(1, 6):
    LABELS[f"MARKETPLACEGRUP{_g}EKSISTOKADEDI"] = f"Pazaryeri Grup {_g} Eksi Stok"
    LABELS[f"MARKETPLACEGRUP{_g}KOMISYONTIP"] = f"Pazaryeri Grup {_g} Komisyon Tip"
    LABELS[f"MARKETPLACEGRUP{_g}KOMISYONDEGER"] = f"Pazaryeri Grup {_g} Komisyon Değer"
for _i in range(1, 21):
    LABELS[f"UYETIPIFIYAT{_i}"] = f"Üye Tipi {_i} Fiyatı"

# Arayüz grupları (sekme içi alt başlıklar)
GROUPS: List[Dict[str, Any]] = [
    {"label": "Kimlik & Kodlar", "keys": [
        "URUNKARTIID", "URUNID", "STOKKODU", "VARYASYONKODU", "BARKOD", "GTIPKODU", "SATISBIRIMI"]},
    {"label": "Temel Bilgiler", "keys": [
        "URUNADI", "MARKA", "TEDARIKCI", "VARYASYON", "ONYAZI", "ACIKLAMA"]},
    {"label": "Fiyatlandırma", "keys": [
        "ALISFIYATI", "PIYASAFIYATI", "SATISFIYATI", "INDIRIMLIFIYAT", "SURELIINDIRIMOZELLIK",
        "KDVORANI", "KDVDAHIL", "PARABIRIMI", "PARABIRIMI_ID", "TEDARIKCIKOMISYONORANI",
        "KARGOUCRETI", "MAKSTAKSITSAYISI"]},
    {"label": "Üye Tipi Fiyatları", "keys": [f"UYETIPIFIYAT{i}" for i in range(1, 21)]},
    {"label": "Stok & Durum", "keys": [
        "STOKADEDI", "KONSINYESTOKADEDI", "EKSISTOKADEDI", "KARTAKTIF", "URUNAKTIF", "KAPASITE",
        "VITRIN", "YENIURUN", "FIRSATURUNU", "FBSTOREGOSTER", "UCRETSIZKARGO", "SEPETUCRETSIZKARGO"]},
    {"label": "SEO & Adwords", "keys": [
        "ANAHTARKELIME", "SEO_SAYFABASLIK", "SEO_ANAHTARKELIME", "SEO_SAYFAACIKLAMA",
        "ADWORDSACIKLAMA", "ADWORDSKATEGORI", "ADWORDSTIP", "BREADCRUMBKAT",
        "OZELALAN1", "OZELALAN2", "OZELALAN3", "OZELALAN4", "OZELALAN5"]},
    {"label": "Puan", "keys": ["PUANDEGER", "PUANYUZDE", "PUANKULLANIMIIPTALAKTIF"]},
    {"label": "Boyut & Kargo", "keys": [
        "KARGOAGIRLIGI", "KARGOAGIRLIGIYURTDISI", "URUNAGIRLIGI",
        "URUNGENISLIK", "URUNDERINLIK", "URUNYUKSEKLIK"]},
    {"label": "Sipariş Limitleri & Ödeme", "keys": [
        "UYEALIMMIN", "UYEALIMMAKS", "BAYIALIMMIN", "BAYIALIMMAKS",
        "URUNKAPIDAODEMEYASAKLI", "YEMEKKARTIODEMEYASAKLILISTESI"]},
    {"label": "Teslimat", "keys": [
        "TAHMINITESLIMSURESI", "TAHMINITESLIMSURESIGOSTER",
        "TAHMINITESLIMSURESIAYNIGUN", "TAHMINITESLIMSURESITARIH"]},
    {"label": "Pazaryeri Entegrasyonu", "keys": [
        "MARKETPLACEAKTIF", "ENTEGRASYONGUNCELLEMEAKTIF", "PAZARYERIAKTIFLISTESI",
        "MARKETPLACEGRUP1EKSISTOKADEDI", "MARKETPLACEGRUP1KOMISYONTIP", "MARKETPLACEGRUP1KOMISYONDEGER",
        "MARKETPLACEAKTIF2", "MARKETPLACEGRUP2EKSISTOKADEDI", "MARKETPLACEGRUP2KOMISYONTIP", "MARKETPLACEGRUP2KOMISYONDEGER",
        "MARKETPLACEAKTIF3", "MARKETPLACEGRUP3EKSISTOKADEDI", "MARKETPLACEGRUP3KOMISYONTIP", "MARKETPLACEGRUP3KOMISYONDEGER",
        "MARKETPLACEAKTIF4", "MARKETPLACEGRUP4EKSISTOKADEDI", "MARKETPLACEGRUP4KOMISYONTIP", "MARKETPLACEGRUP4KOMISYONDEGER",
        "MARKETPLACEAKTIF5", "MARKETPLACEGRUP5EKSISTOKADEDI", "MARKETPLACEGRUP5KOMISYONTIP", "MARKETPLACEGRUP5KOMISYONDEGER"]},
    {"label": "Tarihler", "keys": ["YAYINTARIHI", "EKLEMETARIHI"]},
]


def field_type(col: str) -> str:
    if col in BOOL_COLS:
        return "bool"
    if col in TEXTAREA_COLS:
        return "textarea"
    if col in NUMBER_COLS:
        return "number"
    return "text"


def build_schema() -> List[Dict[str, Any]]:
    """Arayüzün generic render edebileceği gruplu şema."""
    out = []
    for g in GROUPS:
        fields = []
        for k in g["keys"]:
            fields.append({
                "key": k,
                "label": LABELS.get(k, k.title()),
                "type": field_type(k),
                "readonly": k in READONLY_COLS,
            })
        out.append({"label": g["label"], "fields": fields})
    return out


def normalize_value(col: str, raw: Any) -> Any:
    """Excel ham hücre değerini DB/arayüz için normalize et."""
    import math
    if raw is None:
        return "" if col not in BOOL_COLS else 0
    # NaN kontrolü
    try:
        if isinstance(raw, float) and math.isnan(raw):
            return "" if col not in BOOL_COLS else 0
    except Exception:
        pass

    if col in BOOL_COLS:
        s = str(raw).strip().lower()
        return 1 if s in ("1", "1.0", "true", "evet", "yes") else 0

    if col == "BARKOD" or col == "STOKKODU":
        # Barkod/stok kodu büyük sayı; float bilimsel gösterimi engelle
        try:
            return str(int(float(raw)))
        except Exception:
            return str(raw).strip()

    if col in NUMBER_COLS:
        s = str(raw).strip().replace(".", "").replace(",", ".") if isinstance(raw, str) and "," in str(raw) else raw
        try:
            f = float(s)
            return int(f) if f.is_integer() else f
        except Exception:
            return str(raw).strip()

    # text / textarea
    try:
        # int olarak gelen kimlik benzeri değerleri düz string'e çevir
        if isinstance(raw, float) and raw.is_integer():
            return str(int(raw))
    except Exception:
        pass
    return str(raw).strip()


def parse_price(raw: Any) -> float | None:
    """'489,3' / '1.234,56' / 1800 -> float."""
    import math
    if raw is None:
        return None
    try:
        if isinstance(raw, float) and math.isnan(raw):
            return None
    except Exception:
        pass
    if isinstance(raw, (int, float)):
        return float(raw)
    s = str(raw).strip()
    if not s:
        return None
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    try:
        return float(s)
    except Exception:
        return None
