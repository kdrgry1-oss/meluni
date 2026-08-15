"""
product_attributes.py — Ürün özelliklerini (pazaryeri attribute) üründen çıkarır.

NEDEN: Trendyol her kategoride ürün özelliği ister (kol tipi, yaka tipi, kumaş,
desen, boy, kalıp…). Bazıları ZORUNLUdur; eksikse ürün reddedilir. Tedarikçi
beslemesi bu alanları vermiyor — yalnız kumaş ve cinsiyet geliyor. Ürünler
özelliksiz aktarılınca pazaryerinde ya reddediliyor ya da filtrelerde hiç
çıkmıyor (müşteri "kısa kollu" diye filtreleyince ürün görünmüyor).

KAYNAKLAR — sırayla, en güvenilir önce:
  1. Beslemenin verdiği alanlar (kumaş, cinsiyet)
  2. ÜRÜN ADI — kısaltılmış adlarımız zaten yapı taşıyor:
     "Kısa Kollu Bisiklet Yaka Büzgülü Viskon Elbise"
     → Kol Tipi: Kısa Kollu · Yaka Tipi: Bisiklet Yaka · Kumaş: Viskon
  3. Tedarikçi açıklamasındaki "Kumaş: …" gibi etiketli satırlar
  4. Ölçü tablosundaki manken bilgisi (boy/kilo/ölçü ve giydiği beden)

UYDURMUYORUZ: bir özellik hiçbir kaynakta geçmiyorsa yazılmaz. Yanlış özellik,
eksik özellikten kötüdür — müşteri "pamuklu" diye filtreleyip polyester ürün
alırsa iade ve puan kaybı gelir.

Çıktı biçimi: [{"name": "Kol Tipi", "value": "Kısa Kollu"}, …]
Pazaryeri gönderimi (integrations_trendyol._collect_local_values) bu biçimi
zaten okuyor; yerel ad → Trendyol id çevirisi kategori eşleştirmesinde yapılır.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

# ── Sınıf → özellik adı ────────────────────────────────────────────────
# Anahtarlar supplier_xml._CLASS_MARKERS sınıflarıyla aynı; sözlüğü orada
# büyütünce burası da kazanır.
_SINIF_ADI = {
    "kol": "Kol Tipi",
    "yaka": "Yaka Tipi",
    "kumaş": "Kumaş",
    "desen": "Desen",
    "boy": "Boy",
    "bel": "Bel Tipi",
    "kalıp": "Kalıp",
    "detay": "Detay",
}

# Sınıfların kelimeleri (supplier_xml ile aynı kaynak; ithal edemiyoruz çünkü
# o modül veritabanına bağlı — kopya bilinçli ve testle sabitlendi).
_SINIF_KELIME: Dict[str, List[str]] = {
    "kol": ["kollu", "kolsuz", "askılı", "askili", "kol"],
    "yaka": ["yaka", "yakalı", "yakali", "kruvaze", "kapüşonlu", "kapşonlu"],
    "detay": ["detaylı", "detayli", "cepli", "düğmeli", "fermuarlı", "yırtmaçlı",
              "büzgülü", "kuşaklı", "bağcıklı", "bağlamalı", "pileli", "fırfırlı",
              "nervürlü", "lastikli", "astarlı", "dantelli", "likralı",
              "ribanalı", "şardonlu", "volanlı"],
    "desen": ["baskılı", "desenli", "çizgili", "kareli", "puantiyeli", "payetli",
              "nakışlı", "işlemeli"],
    # "boy" başlığın kendisidir ("Uzun Boy"): sınıf kelimesi sayılmazsa
    # niteleyici olarak sonraki öbeğe sızıyor ve "Boy Keten" gibi kumaş
    # değerleri üretiyordu.
    "boy": ["mini", "midi", "maksi", "uzun", "kısa", "crop", "diz", "boy"],
    "bel": ["bel", "beli", "paça", "paçalı", "pacali"],
    "kalıp": ["oversize", "regular", "slim", "fit", "kalıplı", "bol", "dar"],
    "kumaş": ["viskon", "krep", "süprem", "triko", "keten", "kadife", "dalgıç",
              "sandy", "modal", "janjan", "aerobin", "bürümcük", "atlas", "tül",
              "dantel", "deri", "kot", "denim", "file", "fitilli", "kaşkorse",
              "örme", "poplin", "gabardin", "scuba", "şifon", "saten",
              "pamuklu", "dokuma", "jean", "süet", "polar", "gipeli"],
}
_KELIME_SINIF: Dict[str, str] = {}
for _s, _ks in _SINIF_KELIME.items():
    for _k in _ks:
        _KELIME_SINIF.setdefault(_k, _s)
del _s, _ks, _k

# Öbeği açıklayan ama tek başına özellik olmayan kelimeler; işaretçinin önüne
# katılırlar ("Bisiklet" + "Yaka", "Kısa" + "Kollu").
_ATLA = {"ve", "ile", "için", "kadın", "erkek", "unisex", "çocuk"}

# NİTELEYİCİ + BAŞ. Türkçede bazı işaretçiler tek başına özellik değil, bir
# sonrakini niteler: "Kısa" + "Kollu" → kol tipi (boy değil), "Dantel" +
# "Detaylı" → detay (kumaş değil). Bunlar kalıplı ve sayılıdır; hepsini
# birleştirmek "Bisiklet Yaka Büzgülü"yü tek öbek yapıp yanlış üretirdi.
_NITELEYICI_CIFT = {
    ("boy", "kol"),        # Kısa/Uzun Kollu
    ("kumaş", "detay"),    # Dantel/Payet Detaylı
    ("desen", "detay"),    # Baskı Detaylı
}

def _kucult(s: str) -> str:
    # U+0307 (birleşik üstteki nokta): besleme "HAKİ"yi bazen i+nokta olarak
    # yolluyor; ayıklanmazsa karşılaştırma da görüntü de bozuluyor ("Haki̇").
    return (str(s or "").replace("I", "ı").replace("İ", "i")
            .replace("\u0307", "").lower().strip())


_TR_BUYUK = {"i": "İ", "ı": "I", "ö": "Ö", "ü": "Ü", "ş": "Ş", "ç": "Ç", "ğ": "Ğ"}


def renk_duzelt(renk: str) -> str:
    """Renk adını tek biçime getirir: "SİYAH"/"haki̇" → "Siyah"/"Haki".

    Besleme rengi kâh büyük harf kâh birleşik noktalı yolluyor. Ham hâliyle
    yazınca hem vitrinde çirkin görünüyor hem de pazaryeri değeri ADA GÖRE
    eşleştirdiği için "SİYAH" Trendyol'un "Siyah" değerini tutturamıyor.
    """
    parcalar = []
    for k in str(renk or "").split():
        kucuk = _kucult(k)
        if not kucuk:
            continue
        parcalar.append(_TR_BUYUK.get(kucuk[0], kucuk[0].upper()) + kucuk[1:])
    return " ".join(parcalar)


def addan_ozellikler(ad: str) -> Dict[str, str]:
    """Ürün adından özellik çıkarır: {"Kol Tipi": "Kısa Kollu", …}

    Öbek mantığı: işaretçiden önce biriken niteleyiciler onunla tek parçadır
    ("Bisiklet" + "Yaka"). Bir işaretçi, kendi niteleyicisi YOKSA ve peşinden
    gelen işaretçiyle bilinen bir çift kuruyorsa ("Kısa"+"Kollu"), kendi
    özelliğini üretmez; sonrakinin niteleyicisi olur.
    """
    kelimeler = [k for k in re.split(r"\s+", str(ad or "").strip()) if k]
    temiz = [re.sub(r"[^\wçğıöşüÇĞİÖŞÜ]+$", "", k) for k in kelimeler]
    siniflar = [_KELIME_SINIF.get(_kucult(k)) for k in temiz]

    bulunan: Dict[str, str] = {}
    onceki: List[str] = []
    for i, kelime in enumerate(temiz):
        sinif = siniflar[i]
        if not sinif:
            if _kucult(kelime) not in _ATLA and kelime:
                onceki = (onceki + [kelime])[-2:]
            continue
        sonraki = siniflar[i + 1] if i + 1 < len(siniflar) else None
        if (not onceki and sonraki and sonraki != sinif
                and (sinif, sonraki) in _NITELEYICI_CIFT):
            onceki = [kelime]            # kendi özelliğini üretme, niteleyici ol
            continue
        deger = " ".join([p for p in onceki if _kucult(p) not in _ATLA] + [kelime])
        ad_ozellik = _SINIF_ADI.get(sinif)
        if ad_ozellik and ad_ozellik not in bulunan and deger.strip():
            bulunan[ad_ozellik] = deger.strip()
        onceki = []
    return bulunan


# ── Açıklamadan etiketli özellikler ────────────────────────────────────
# "Kumaş: %100 Viskon", "Kalıp: Regular" gibi satırlar.
_ETIKET_ESLEME = {
    "kumaş": "Kumaş", "kumas": "Kumaş", "materyal": "Kumaş", "malzeme": "Kumaş",
    "kumaş cinsi": "Kumaş", "kumaş tipi": "Kumaş",
    "yaka": "Yaka Tipi", "yaka tipi": "Yaka Tipi",
    "kol": "Kol Tipi", "kol tipi": "Kol Tipi", "kol boyu": "Kol Tipi",
    "desen": "Desen", "desen tipi": "Desen",
    "kalıp": "Kalıp", "kalip": "Kalıp", "kesim": "Kalıp",
    "boy": "Boy", "paça": "Paça Tipi", "paça tipi": "Paça Tipi",
    "astar": "Astar Durumu", "astar durumu": "Astar Durumu",
    "kalınlık": "Kalınlık", "yıkama": "Yıkama Talimatı",
    "ürün tipi": "Ürün Tipi", "sezon": "Sezon",
}
_HTML = re.compile(r"<[^>]+>")


def aciklamadan_ozellikler(aciklama: str) -> Dict[str, str]:
    """Açıklamadaki "Etiket: Değer" satırlarından tanınan özellikleri alır."""
    import html as _html
    ham = _html.unescape(_HTML.sub("\n", str(aciklama or "")))
    bulunan: Dict[str, str] = {}
    for satir in re.split(r"[\n\r•·|]+", ham):
        m = re.match(r"^\s*([^:：]{2,24})\s*[:：]\s*(.{1,60}?)\s*$", satir)
        if not m:
            continue
        etiket = _kucult(m.group(1))
        deger = re.sub(r"\s+", " ", m.group(2)).strip(" .;,")
        ad = _ETIKET_ESLEME.get(etiket)
        if ad and deger and ad not in bulunan:
            bulunan[ad] = deger[:60]
    return bulunan


# ── Manken bilgisi ─────────────────────────────────────────────────────
_MANKEN_SIRA = ["boy", "kilo", "göğüs", "bel", "basen", "kalça"]


def mankenden_ozellikler(size_table: Optional[Dict[str, Any]]) -> Dict[str, str]:
    """Ölçü tablosundaki manken bilgisinden özellik üretir.

    Müşterinin en çok sorduğu şey bu: "manken kaç beden giyiyor?". Trendyol'da
    ayrı bir alan olarak da isteniyor.
    """
    if not isinstance(size_table, dict):
        return {}
    bulunan: Dict[str, str] = {}
    beden = str(size_table.get("product_size") or "").strip()
    if beden:
        bulunan["Mankenin Üzerindeki Beden"] = beden[:30]
    mi = size_table.get("model_info")
    if isinstance(mi, dict):
        parcalar = []
        for anahtar in _MANKEN_SIRA:
            for k, v in mi.items():
                if _kucult(k).startswith(anahtar) and str(v).strip():
                    parcalar.append(f"{str(k).strip().title()} {str(v).strip()}")
                    break
        if parcalar:
            bulunan["Manken Ölçüleri"] = ", ".join(parcalar)[:120]
    return bulunan


# ── Sezon ve kalıp — ürün kartının ZORUNLU temel alanları ──────────────
# Panel (Products.jsx) bu ikisi boşken kaydı reddediyor: `season` rapor sezon
# filtresini, `size_advice` ürün sayfasındaki beden tavsiyesini besler.
# Tedarikçi beslemesi ikisini de vermiyor — üründe zaten duran bilgiden
# (ad, kategori, kumaş, açıklama) çıkarıyoruz. Çıkmıyorsa BOŞ bırakıyoruz;
# yanlış sezon raporu, yanlış kalıp tavsiyesi iade getirir.

# Sezon: giysi türü kumaştan daha belirleyicidir (kadife elbise yazlık olabilir
# ama kaban her hâlükârda kışlıktır), o yüzden önce tür bakılır.
_SEZON_TUR = [
    ("Kış", ["mont", "kaban", "palto", "parka", "kürk", "kazak", "hırka", "süveter",
             "boğazlı", "kapüşonlu sweat", "sweatshirt", "sweat", "eşofman",
             "atkı", "bere", "eldiven", "yelek mont"]),
    ("Yaz", ["şort", "bikini", "mayo", "pareo", "plaj", "sundress", "atlet",
             "bermuda", "kaftan", "şortlu"]),
    ("İlkbahar/Sonbahar", ["trençkot", "blazer", "kaban ceket", "yağmurluk"]),
]
_SEZON_KUMAS = [
    ("Kış", ["triko", "kaşmir", "kaşe", "yün", "polar", "şardonlu", "dalgıç",
             "scuba", "kadife", "boucle", "peluş", "süet", "deri", "fitilli"]),
    ("Yaz", ["keten", "şifon", "tül", "dantel", "bürümcük", "poplin", "süprem",
             "viskon", "file", "lyocell", "gipe", "gipeli"]),
    ("İlkbahar/Sonbahar", ["krep", "sandy", "aerobin", "gabardin", "kot", "denim",
                           "jean", "atlas", "janjan", "saten", "dokuma"]),
    ("Tüm Sezonlar", ["penye", "kaşkorse", "ribana", "likra", "likralı", "jarse",
                      "modal", "örme", "nervürlü", "interlok"]),
]
# Kolsuz/askılı bir üst kata çıkamayan tek işarettir; kumaş da tür de yoksa bakılır.
_SEZON_KOL = [("Yaz", ["kolsuz", "askılı", "askili", "straplez", "ip askılı"])]

_SEZON_KANON = ["İlkbahar/Sonbahar", "Tüm Sezonlar", "Yaz", "Kış"]


def _sezon_normalize(v: str) -> str:
    """Serbest metni panelin 4 kanonik sezon değerine indirger, tanımazsa ''."""
    s = _kucult(v).replace("i̇", "i")
    if not s:
        return ""
    if any(x in s for x in ("tüm sezon", "tum sezon", "4 mevsim", "dört mevsim",
                            "all season", "her mevsim", "mevsimlik")):
        return "Tüm Sezonlar"
    if any(x in s for x in ("kış", "kis", "winter")):
        return "Kış"
    if any(x in s for x in ("yaz", "summer")):
        return "Yaz"
    if any(x in s for x in ("sonbahar", "ilkbahar", "bahar", "fall", "autumn", "spring")):
        return "İlkbahar/Sonbahar"
    return ""


def _urun_metni(product: Dict[str, Any]) -> str:
    """Sezon/kalıp aranacak birleşik metin: ad + kategori + kumaş + açıklama."""
    parcalar = [product.get("name"), product.get("category_name"),
                product.get("category_path"), product.get("fabric")]
    aciklama = _HTML.sub(" ", str(product.get("description") or ""))
    return _kucult(" ".join([str(p or "") for p in parcalar] + [aciklama]))


def sezon_cikar(product: Dict[str, Any],
                ozellikler: Optional[Dict[str, str]] = None) -> str:
    """Ürünün sezonunu 4 kanonik değerden biri olarak döner; çıkmazsa ''."""
    for kaynak in ((ozellikler or {}).get("Sezon"),):
        s = _sezon_normalize(str(kaynak or ""))
        if s:
            return s
    metin = _urun_metni(product)
    ad_kat = _kucult(f"{product.get('name') or ''} {product.get('category_name') or ''} "
                     f"{product.get('category_path') or ''}")
    for sezon, kelimeler in _SEZON_TUR:
        if any(k in ad_kat for k in kelimeler):
            return sezon
    for tablo in (_SEZON_KUMAS, _SEZON_KOL):
        for sezon, kelimeler in tablo:
            if any(k in metin for k in kelimeler):
                return sezon
    return ""


# Kalıp → panelin `size_advice` değerleri (dar/normal/bol).
_KALIP_ISARET = [
    ("bol", ["oversize", "over size", "salaş", "salas", "boyfriend", "bol kesim",
             "bol kalıp", "bol paça", "geniş kesim", "baggy", "wide leg", "bol "]),
    ("dar", ["slim fit", "slim", "skinny", "dar kesim", "dar kalıp", "bodycon",
             "vücuda oturan", "vucuda oturan", "dar paça", "tayt", "body ", "dar "]),
    ("normal", ["regular fit", "regular", "normal kalıp", "standart kesim",
                "normal kesim", "klasik kesim"]),
]
_KALIP_ETIKET = {"dar": "Dar Kalıp", "normal": "Normal Kalıp", "bol": "Bol Kalıp"}


def kalip_cikar(product: Dict[str, Any],
                ozellikler: Optional[Dict[str, str]] = None) -> str:
    """Beden önerisini (dar/normal/bol) çıkarır; işaret yoksa ''.

    Panel bu alanı zorunlu tutuyor ama boş bırakmak, yanlış tavsiye vermekten
    iyidir: "bir beden büyük al" denip normal kalıp çıkan ürün iade gelir.
    """
    adaylar = [str((ozellikler or {}).get("Kalıp") or ""),
               str(product.get("name") or ""),
               _HTML.sub(" ", str(product.get("description") or ""))]
    for ham in adaylar:
        metin = _kucult(ham)
        if not metin:
            continue
        for kalip, kelimeler in _KALIP_ISARET:
            if any(k in metin for k in kelimeler):
                return kalip
    return ""


def temel_alanlar(product: Dict[str, Any],
                  size_table: Optional[Dict[str, Any]] = None) -> Dict[str, str]:
    """Ürün kartının zorunlu temel alanları: {"season": …, "size_advice": …}

    Yalnız ÇIKARILABİLEN alanlar döner; çıkmayan anahtar hiç konmaz ki çağıran
    mevcut (elle girilmiş) değeri boşla ezmesin.
    """
    ozellikler = {a["name"]: a["value"] for a in urun_ozellikleri(product, size_table)}
    sonuc: Dict[str, str] = {}
    sezon = sezon_cikar(product, ozellikler)
    if sezon:
        sonuc["season"] = sezon
    kalip = kalip_cikar(product, ozellikler)
    if kalip:
        sonuc["size_advice"] = kalip
    return sonuc


# Aynı bilgiyi başka adla isteyen pazaryeri alanları. Değeri kopyalarız;
# uydurma değil, ad eşlemesidir.
_ES_ADLAR = {
    "Kumaş": ["Kumaş Tipi"],
    "Kol Tipi": ["Kol Boyu"],
}

# Trendyol'un giyimde ZORUNLU tuttuğu ama beslemede karşılığı olmayan alanlar.
# Ürün adı/açıklaması bu konuda bir şey SÖYLEMİYORSA en makul okuma budur:
# desen sözcüğü hiç geçmiyorsa ürün düzdür, kalıp/boy sözcüğü geçmiyorsa
# standarttır. Bir işaret varsa (desenli, oversize, mini…) zaten yukarıdaki
# çıkarım doldurmuş olur ve buraya hiç gelinmez.
_ZORUNLU_VARSAYILAN = {
    "Desen": "Düz",
    "Kalıp": "Standart",
    "Boy": "Standart",
}


def _zorunlu_tamamla(toplam: Dict[str, str]) -> None:
    for ad, deger in _ZORUNLU_VARSAYILAN.items():
        if not str(toplam.get(ad) or "").strip():
            toplam[ad] = deger


def urun_ozellikleri(product: Dict[str, Any],
                     size_table: Optional[Dict[str, Any]] = None,
                     zorunlu: bool = True) -> List[Dict[str, str]]:
    """Ürünün tüm kaynaklarından özellik listesi üretir.

    Öncelik: beslemenin verdiği alanlar > ürün adı > açıklama > manken.
    Aynı özellik birden çok kaynaktan gelirse İLKİ kalır.

    `zorunlu=True` iken pazaryerinin zorunlu tuttuğu ama üründe işareti
    olmayan alanlara makul varsayılan yazılır (Desen: Düz, Kalıp/Boy:
    Standart) — bu alanlar boş kalınca ürün Trendyol'da reddediliyor.
    """
    toplam: Dict[str, str] = {}

    def _ekle(d: Dict[str, str]):
        for k, v in (d or {}).items():
            if k and str(v).strip():
                toplam.setdefault(k, str(v).strip())

    # 1) Beslemenin doğrudan verdiği alanlar — en güvenilir
    dogrudan = {}
    if str(product.get("fabric") or "").strip():
        dogrudan["Kumaş"] = str(product["fabric"]).strip()
    if str(product.get("gender") or "").strip():
        dogrudan["Cinsiyet"] = str(product["gender"]).strip()
    if str(product.get("brand") or "").strip():
        dogrudan["Marka"] = str(product["brand"]).strip()
    # RENK — Trendyol'da zorunlu ("Renk" ve bazı kategorilerde "Web Color").
    # Ürün düzeyinde renk boşsa varyantlardan ilk dolu renk alınır; her renk
    # zaten AYRI ürün kartı olduğu için bu tek değerdir.
    renk = str(product.get("color") or "").strip()
    if not renk:
        for v in (product.get("variants") or []):
            if isinstance(v, dict) and str(v.get("color") or "").strip():
                renk = str(v["color"]).strip()
                break
    if renk:
        renk = renk_duzelt(renk)
        dogrudan["Renk"] = renk
        dogrudan["Web Color"] = renk
    _ekle(dogrudan)

    _ekle(addan_ozellikler(product.get("name") or ""))
    _ekle(aciklamadan_ozellikler(product.get("description") or ""))
    _ekle(mankenden_ozellikler(size_table))

    # Sezon ve kalıp: hem pazaryeri özniteliği hem ürün kartının zorunlu alanı.
    # Aynı çıkarım iki yerde kullanılsın diye burada da yazılır (temel_alanlar
    # aynı sonucu okur), böylece panelde görünen ile Trendyol'a giden aynı olur.
    sezon = sezon_cikar(product, toplam)
    if sezon:
        toplam.setdefault("Sezon", sezon)
    kalip = kalip_cikar(product, toplam)
    if kalip:
        toplam.setdefault("Kalıp", _KALIP_ETIKET[kalip])

    # Trendyol'un AYNI bilgiyi başka adla istediği alanlar. Değer aynı, ad
    # farklı olduğu için zorunlu alan boş görünüyordu ("Kumaş" doluyken
    # "Kumaş Tipi" kırmızı kalıyordu).
    for kaynak, hedefler in _ES_ADLAR.items():
        if toplam.get(kaynak):
            for hedef in hedefler:
                toplam.setdefault(hedef, toplam[kaynak])

    if zorunlu:
        _zorunlu_tamamla(toplam)

    return [{"name": k, "value": v} for k, v in toplam.items()]
