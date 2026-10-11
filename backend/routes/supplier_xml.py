"""
supplier_xml.py — Tedarikçi XML beslemesinden ürün ve stok aktarımı.

İki iş yapar:
  • Ürün aktarımı  (günde 1) — beslemede olup bizde olmayan ürünleri açar.
  • Stok senkronu  (günde 4) — mevcut ürünlerin stok/fiyatını günceller.

Tasarım notları:

1) ŞEMA BAĞIMSIZ. Tedarikçi XML'lerinin etiket adları standart değildir
   (UrunAdi / ProductName / Baslik ...). Bu yüzden alan adları KODA GÖMÜLÜ
   DEĞİL: besleme ilk okunduğunda otomatik tanınır, tanınan eşleme ayarlara
   yazılır ve panelden düzeltilebilir. Böylece tedarikçi etiket adını
   değiştirdiğinde kod değil ayar güncellenir.

2) KODLAR BİZE AİT. Tedarikçinin barkodu/stok kodu yalnızca EŞLEME anahtarı
   olarak saklanır; müşteriye ve pazaryerlerine giden barkod/stok/model kodu
   supplier_codes.py tarafından bize özel üretilir ve kalıcıdır.

3) KİMLİK BİLGİSİ REPODA DURMAZ. Besleme adresi (içinde kullanıcı/şifre ile)
   şifreli olarak veritabanında tutulur.
"""
from __future__ import annotations

import asyncio
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException

from .deps import db, logger, generate_id, require_admin
from security.crypto import encrypt, decrypt
import supplier_codes
import product_attributes
import group_ref

router = APIRouter(prefix="/supplier-xml", tags=["supplier-xml"])

SETTINGS_ID = "supplier_xml"


class _SkipProduct(Exception):
    """Ürün iş kuralı gereği aktarılmaz (hata değil, kasıtlı atlama)."""


_MAX_BYTES = 80 * 1024 * 1024  # 80 MB — kaçak büyümeye karşı üst sınır


# ─────────────────────────── alan adı sözlüğü ───────────────────────────
# Aday etiket adları; küçük harfe indirgenip karşılaştırılır. Sıra önemlidir,
# ilk eşleşen kazanır.
CANDIDATES: Dict[str, List[str]] = {
    "name":        ["urunadi", "urunismi", "productname", "baslik", "title", "name", "ad"],
    "description": ["aciklama", "urunaciklama", "detay", "description", "aciklamasi"],
    "brand":       ["marka", "brand"],
    "category":    ["kategori", "kategoriagaci", "kategoriyolu", "category", "categoryname"],
    "barcode":     ["barkod", "barcode", "ean", "gtin", "eankodu"],
    "stock_code":  ["stokkodu", "stockcode", "sku", "urunkodu", "stok_kodu"],
    "model_ref":   ["modelkodu", "model", "urunkartiid", "grupkodu", "anaurunkodu",
                    "productcardid", "modelcode", "urunid", "id"],
    "price":       ["urunbayifiyat", "bayifiyati", "satisfiyati", "indirimlifiyat", "fiyat",
                    "price", "salesprice", "tutar"],
    "list_price":  ["piyasafiyati", "listprice", "eskifiyat", "psf"],
    "currency":    ["parabirimi", "currency", "doviz"],
    "stock":       ["stokadedi", "stok", "miktar", "quantity", "stock", "adet"],
    "vat":         ["kdvorani", "kdv", "vat", "taxrate"],
    "color":       ["renk", "color"],
    "size":        ["beden", "size", "numara", "olcu"],
    "kdv_included": ["kdvdahil", "vatincluded", "kdv_dahil"],
    "gender":      ["cinsiyet", "gender"],
    "fabric":      ["kumas", "fabric"],
    # ÜRÜN İÇERİK BİLGİSİ (materyal bileşeni): "%95 Viskon %5 Elastan" gibi.
    # Trendyol'da "Materyal Bileşeni"/"Ürün İçerik Bilgisi" adıyla isteniyor ve
    # beslemede kumaş alanından AYRI bir alanda gelebiliyor.
    "material":    ["materyal", "icerik", "urunicerik", "kumasbilgisi",
                    "kumasicerigi", "material", "content", "composition"],
    "desi":        ["desi", "kargodesi"],
    "product_url": ["urunurl", "producturl", "link", "url"],
    # RENK GRUBU: tedarikçi aynı modelin her rengini ayrı ürün olarak veriyor;
    # bu alan hepsinde ORTAK. Vitrindeki "diğer renkler" ve pazaryerindeki
    # ortak kart buna dayanır.
    "group_ref":   ["ozelalan2", "grupkodu", "anaurunkodu", "urungrupkodu",
                    "modelgrupkodu"],
}
IMAGE_TAGS = ["resim", "image", "img", "picture", "foto", "gorsel", "resimler", "images"]
VARIANT_CONTAINER = ["urunsecenek", "varyantlar", "variants", "varyasyonlar",
                     "secenekler", "urunsecenekleri", "options"]
VARIANT_ITEM = ["varyant", "variant", "varyasyon", "secenek", "option"]



# ─────────────────────── ürün adı düzenleme ───────────────────────
_DASH_RX = re.compile(r'\s+[–—]\s+|\s+-\s+')
_WS_RX = re.compile(r'\s+')
# Türkçe büyük harf eşlemesi: str.upper() "i"yi "I" yapar, doğrusu "İ".
_TR_UP = {"i": "İ", "ı": "I", "ö": "Ö", "ü": "Ü", "ş": "Ş", "ç": "Ç", "ğ": "Ğ"}
_KEEP_LOWER = {"ve", "ile", "için"}
# Baştaki cinsiyet kelimesi: ayrı alanda (gender) ve kategoride zaten var.
# "Kadı" beslemede geçen bir yazım hatası.
_GENDER_PREFIX = ("kadın", "kadin", "kadı", "kadınlar", "kadinlar", "erkek",
                  "erkekler", "unisex", "çocuk", "cocuk")
NAME_MAX_LEN = 70

# STOK EŞİĞİ — dropshipping güvenliği.
# Tedarikçide stoğu az kalan bir bedeni satışa açmak, sipariş gelince malı
# bulamamak demektir: müşteriye iptal, Trendyol'da satıcı puanı kaybı.
# Eşik VARYANT bazında uygulanır; stoğu bol bedenler ayakta kalır, yalnız
# riskli beden düşer. Gerçek beslemede 4.461 varyant bu kurala takılıyor.
MIN_STOCK_DEFAULT = 5

# TEK BEDEN KALMIŞ ÜRÜN — kalıcı kapatma eşiği.
# Ürünün diğer bedenleri tükenmiş, elde tek beden kalmışsa ve o bedenin stoğu
# da bu değerin altındaysa ürün ölmüş demektir: vitrinde kategori doluymuş gibi
# görünür, müşterilerin çoğu kendi bedenini bulamadan sayfayı terk eder, gelen
# tek tük sipariş de tedarikçide bulunamama riski taşır. Bu ürünler silinir ve
# BİR DAHA AKTARILMAMAK ÜZERE kara listeye alınır (yönetim isteği).
# Kalıcı bir karar: tedarikçi sonradan stok bassa bile ürün geri gelmez —
# geri almak için panelden kara listeden çıkarmak gerekir.
SINGLE_SIZE_MAX_STOCK_DEFAULT = 9

# KÂR KATSAYISI — satış = alış × katsayı (+ KDV).
# Ayar boşsa eskiden 1.0'a düşüyordu: ürünler maliyetine, KDV düşünce
# ZARARINA satışa açılıyordu. Varsayılan iş kuralına çekildi.
MARKUP_DEFAULT = 3.0

# BAYİ FİYATI KDV DAHİL Mİ?
# Beslemedeki KDVDahil alanı boş/yanlış gelebiliyor; boş gelince kod "hariç"
# varsayıp fiyata bir kez daha KDV bindiriyordu — vitrin fiyatı %KDV kadar
# şişiyordu. Tedarikçiye göre değişen bir gerçek, koda gömülmez: ayardan gelir.
# modayakamoz bayi fiyatlarını KDV DAHİL veriyor (yönetim beyanı).
SUPPLIER_VAT_INCLUDED_DEFAULT = True


def _tr_title_word(w: str) -> str:
    if not w:
        return w
    if w.lower() in _KEEP_LOWER:
        return w.lower()
    if w.isupper() and len(w) > 1:
        return w  # T-SHIRT, XL gibi kısaltmalara dokunma
    return _TR_UP.get(w[0], w[0].upper()) + w[1:]


# ── Adı ANLAMINA göre kısaltma ──────────────────────────────────────────
#
# Baştan/sondan karakter kesmek yanlış: "…Dalgıç Pantolon" → "…Dalgıç" gibi
# ürünün ne olduğunu silen sonuçlar çıkıyordu. Türkçe ad öbeği SONA dayalıdır:
#
#     [cinsiyet] [kol] [yaka] [detay] [boy] [kumaş] [TÜR]
#     Kadın      Kısa Kollu  V Yakalı  Dantel Detaylı  Viskon  Bluz
#
# Yani ad zaten anlamlı sırada geliyor; yapılacak iş kesmek değil, hangi
# niteleyicinin bilgi taşıdığına karar verip gerisini atmak:
#
#   1) Yapısal/pazarlama dolgusu atılır  ("belden", "önden", "rahat kalıp").
#   2) Kalanlar öbeklere ayrılıp sınıflandırılır (kol / yaka / detay / desen /
#      boy / bel / kumaş).
#   3) HER SINIFTAN TEK öbek kalır — aynı özelliği iki kez anlatan adlarda
#      BAŞ ADA EN YAKIN olan kazanır (Türkçede baş ada yaklaştıkça niteleyici
#      ürünü daha çok tanımlar).
#   4) Tür (son kelime) her hâlükârda korunur.
#
# Ölçüt örneği (kullanıcı):
#   ham  : Kadın Yüksek Bel Belden Kuşaklı üst Dalgıç Alt Dantel Detaylı Dalgıç Pantolon
#   çıktı: Yüksek Bel Dantel Detaylı Dalgıç Pantolon

# Sınıf işaretçileri: öbeği KAPATAN kelimeler. Öbek, işaretçiden önce biriken
# niteleyicilerle birlikte tek parça sayılır ("Dantel" + "Detaylı").
_CLASS_MARKERS: Dict[str, str] = {}
for _cls, _words in {
    "kol":   ["kollu", "kol", "kolsuz", "askılı", "askili", "kolsuzu"],
    "yaka":  ["yaka", "yakalı", "yakali", "kruvaze", "kapüşonlu", "kapşonlu"],
    "detay": ["detaylı", "detayli", "detay", "cepli", "düğmeli", "fermuarlı",
              "yırtmaçlı", "büzgülü", "kuşaklı", "bağcıklı", "bağlamalı",
              "pileli", "fırfırlı", "nervürlü", "lastikli", "astarlı",
              "dantelli", "likralı", "ribanalı", "şardonlu", "volanlı"],
    "desen": ["baskılı", "desenli", "çizgili", "kareli", "puantiyeli",
              "payetli", "nakışlı", "işlemeli", "renkli"],
    "boy":   ["mini", "midi", "maksi", "uzun", "kısa", "crop", "diz"],
    "bel":   ["bel", "beli", "belli", "paça", "paçalı", "fit", "kalıplı",
              "oversize", "regular", "slim"],
    "kumaş": ["viskon", "krep", "süprem", "triko", "keten", "kadife", "dalgıç",
              "sandy", "modal", "janjan", "aerobin", "bürümcük", "atlas",
              "tül", "dantel", "deri", "kot", "denim", "file", "fitilli",
              "kaşkorse", "örme", "poplin", "gabardin", "scuba", "şifon",
              "saten", "pamuklu", "dokuma", "iplik", "jean", "süet", "polar",
              "gipeli", "bürgü"],
}.items():
    for _w in _words:
        _CLASS_MARKERS[_w] = _cls
del _cls, _words, _w

# Yapısal/konumsal dolgu: nereye dikildiğini söyler, ürünün ne olduğunu değil.
_FILLER_WORDS = {
    "belden", "önden", "onden", "arkadan", "yandan", "yanlardan", "yanları",
    "omuzdan", "omuzlardan", "boyundan", "yakadan", "sırttan", "önü", "ön",
    "arka", "arkası", "üstü", "altı", "içi", "dışı", "kısmı", "bölümü", "ucu",
    "üst", "alt", "sırt", "göğüs", "kumaş", "kumaşı", "kesim", "kesimli",
    # pazarlama sıfatları
    "ithal", "rahat", "günlük", "şık", "kaliteli", "mükemmel", "uygun",
    "konforlu", "yeni", "sezon", "sezonun", "trend", "basic", "yazlık",
    "kışlık", "baharlık", "için", "adet", "tam", "doğru", "çok", "premium",
    "-", "–", "—",              # bölünemeyen kısa adlarda tire tek başına kalıyor
}
# Öbeği bütünüyle çöpe atan kelimeler: taşıdıkları niteleyici de pazarlama.
# ("Rahat Kalıp", "Kışlık Model", "Winagain Serisi" → tamamı gider.)
_KILLER_WORDS = {"kalıp", "kalıbı", "model", "modeli", "serisi", "stil",
                 "stili", "kombin", "kombini", "duruş", "konfor", "şıklığı",
                 "şıklığına", "şıklık"}

# Kendinden ÖNCEKİ kelimeye dilbilgisel olarak bağlı işaretçiler. Tek başına
# öbek olamazlar: "Kısa Kollu" bir özelliktir, "Kısa" + "Kollu" iki değil.
_ABSORB_MARKERS = {"detaylı", "detayli", "detay", "kollu", "kol", "yaka",
                   "yakalı", "yakali", "baskılı", "desenli", "işlemeli"}

# Uzunluk taşarsa hangi sınıf önce gider (küçük = önce atılır).
_CLASS_RANK = {"other": 0, "boy": 1, "desen": 2, "yaka": 3, "kol": 4,
               "detay": 5, "bel": 6, "kumaş": 7}


def _clean_word(w: str) -> str:
    return w.lower().strip(".,;:()\"'’“”")


def _stem(w: str) -> str:
    """Kaba Türkçe kök: çekim eki farkını yok sayar ("bel" ≡ "beli")."""
    x = _clean_word(w)
    return x[:-1] if len(x) > 3 and x[-1] in "ıiuüaeo" else x


def _phrases(words: List[str], drop_marketing: bool = True) -> List[tuple]:
    """Kelime dizisini (metin, sınıf) öbeklerine ayırır."""
    out: List[tuple] = []
    buf: List[str] = []
    bekleyen: Optional[str] = None   # kaderi bir sonraki kelimeye bağlı dolgu
    for w in words:
        lw = _clean_word(w)
        if not lw:
            continue
        if lw in _KILLER_WORDS:
            if drop_marketing:
                buf = []        # biriken niteleyici de pazarlamaydı
            bekleyen = None
            continue
        if lw in _FILLER_WORDS:
            # Dolgu kelimesi HEMEN atılmaz, beklemeye alınır. Çünkü yalnız
            # kendinden sonra sınıflı bir niteleyici gelirse gereksizdir
            # ("önden Düğmeli" → "Düğmeli"). Sonrası sınıfsızsa o kelimeyi
            # ayakta tutan tek şey odur: "Sırtı Açık" → "sırtı" atılınca
            # geriye anlamsız "Açık" kalıyordu.
            bekleyen = w
            continue
        cls = _CLASS_MARKERS.get(lw)
        if bekleyen is not None:
            if not cls:
                buf.append(bekleyen)    # sonrası sınıfsız → dolgu aslında taşıyıcı
            bekleyen = None
        if lw in _ABSORB_MARKERS and not buf and out:
            # "Dantel Detaylı", "Kısa Kollu": bu işaretçiler kendinden ÖNCEKİ
            # kelimeye bağlıdır. Ayrı öbek sayılırsa "Dantel" kumaş, "Detaylı"
            # başka bir detay olur; "Kısa" boy, "Kollu" tek başına kalır.
            prev_text, _prev_cls = out.pop()
            out.append((f"{prev_text} {w}", cls))
            continue
        buf.append(w)
        if cls:
            out.append((" ".join(buf), cls))
            buf = []
    if buf:
        out.append((" ".join(buf), "other"))
    return out


def normalize_name(raw: str, max_len: int = NAME_MAX_LEN) -> str:
    """Tedarikçi ürün adını mağaza için ANLAMINA göre kısaltır.

    Beslemedeki ad alanı çoğu üründe pazarlama metni taşıyor; en uzunu 206
    karakter. Gerçek ad tirenin SOLUNDA kalıyor, sağı açıklamaya ait —
    önce oradan bölünür, sonra kalan niteleyiciler elenir. Kısaltılmamış ham
    ad ürünün `supplier_original_name` alanında saklanır (yalnız yönetim).
    """
    s = _WS_RX.sub(" ", (raw or "").strip())
    if not s:
        return ""

    first = s.split(" ", 1)
    if len(first) == 2 and first[0].lower() in _GENDER_PREFIX:
        s = first[1].strip()

    head = _DASH_RX.split(s, 1)[0].strip()
    # Çok kısa bir baş kaldıysa bölme ("Bluz - Beyaz" → "Bluz" olmasın).
    if len(head) >= 20:
        s = head
    if len(s) > max_len and "," in s:
        cand = s.split(",", 1)[0].strip()
        if len(cand) >= 20:
            s = cand

    # Tamamı BÜYÜK yazılmış adlar ("ŞANELKAPAKLI ÇOBAN DİKİŞ …") vitrinde
    # bağırıyor. _tr_title_word kısaltmaları (XL, T-SHIRT) korumak için büyük
    # kelimelere dokunmaz; bu yüzden ad genelinde büyük harf baskınsa önce
    # küçültüyoruz.
    _letters = [c for c in s if c.isalpha()]
    if len(_letters) > 6 and sum(c.isupper() for c in _letters) / len(_letters) > 0.7:
        # Türkçe küçültme: str.lower() "İ"yi birleşik "i̇", "I"yı "i" yapar.
        s = s.replace("İ", "i").replace("I", "ı").lower()

    s = " ".join(_tr_title_word(w) for w in s.split())
    words = s.split()
    if len(words) < 3:
        return s[:max_len].rstrip(" ,-–—")

    # Ürün TÜRÜ (baş ad) asla elenmez ve sona yazılır. Çoğu adda son kelimedir,
    # ama pazarlama metni araya girince olmuyor ("… Pamuklu Kumaş" gibi). Bu
    # yüzden önce TANINAN bir tür aranır, yoksa sondaki ilk anlamlı kelime.
    kind_idx = None
    for k in range(len(words) - 1, -1, -1):
        if _type_of_word(words[k]):
            kind_idx = k
            break
    if kind_idx is None:
        for k in range(len(words) - 1, -1, -1):
            lw = _clean_word(words[k])
            if lw and lw not in _FILLER_WORDS and lw not in _KILLER_WORDS:
                kind_idx = k
                break
    if kind_idx is None:
        kind_idx = len(words) - 1
    kind = words[kind_idx]
    head_from = kind_idx
    # Tür ortada kaldıysa hemen solundaki sınıfsız kelime ona aittir
    # ("Deniz Şortu", "Yağmurluk Takım"); ayırırsak öbek yanlış yere bağlanıyor.
    if kind_idx > 0:
        prev = _clean_word(words[kind_idx - 1])
        if (prev and prev not in _CLASS_MARKERS and prev not in _FILLER_WORDS
                and prev not in _KILLER_WORDS and not _type_of_word(prev)):
            head_from = kind_idx - 1
            kind = f"{words[head_from]} {kind}"
    rest = words[:head_from] + words[kind_idx + 1:]
    parts = _phrases(rest)
    if not parts:
        # Pazarlama temizliği her şeyi yuttu ("Jogger Eşofman Altı Rahat Kalıp"
        # → yalnız "Eşofman"). Ürünü adsız bırakmaktansa daha yumuşak geç.
        parts = _phrases(rest, drop_marketing=False)

    # Bitişik aynı sınıflar tek özelliktir ("Janjan Krep" iki ayrı kumaş değil).
    merged: List[tuple] = []
    for text, cls in parts:
        if merged and merged[-1][1] == cls and cls != "other":
            # Aynı kelimenin çekimli hâli iki kez girmesin: besleme
            # "Yüksek Bel Beli Lastikli" yazıyor, birleştirince "Bel Beli"
            # oluyordu. Kök karşılaştırması Türkçe ekleri tolere eder.
            var = {_stem(x) for x in merged[-1][0].split()}
            ek = [x for x in text.split() if _stem(x) not in var]
            if ek:
                merged[-1] = (f"{merged[-1][0]} {' '.join(ek)}", cls)
        else:
            merged.append((text, cls))

    # Her sınıftan tek öbek: baş ada EN YAKIN olan kazanır.
    #
    # BEL/KALIP İSTİSNASI: bu sınıf ("Yüksek Bel", "Oversize", "Regular Fit")
    # Türkçede adın BAŞINDA durur ve ürünün kalıbını orada tanımlar. Besleme
    # "bel" kelimesini ilerideki bir süslemede tekrar kullanınca ("Yüksek Bel
    # Beli Lastikli … ve Beli Şeritli") son-kazanır kuralı asıl bilgiyi siliyor
    # ve arama için en değerli terim ("Yüksek Bel") kayboluyordu. Bu sınıfta
    # İLK öbek kazanır.
    _ILK_KAZANIR = {"bel"}
    keep_idx = {}
    for i, (_t, cls) in enumerate(merged):
        if cls == "other":
            continue
        if cls in _ILK_KAZANIR and cls in keep_idx:
            continue
        keep_idx[cls] = i
    kept = [(i, t, c) for i, (t, c) in enumerate(merged)
            if c == "other" or keep_idx.get(c) == i]

    # Tekrarı at ("… Dalgıç … Dalgıç"), ama öbeği PARÇALAMA: "Kısa Kollu"daki
    # "Kısa", ilerideki "Kısa Modal"la aynı kelime diye silinirse geriye anlamsız
    # "Kollu" kalır. Bu yüzden yalnız TEK KELİMELİK öbekler kelime tekrarına
    # takılır; çok kelimeli öbek ancak tıpatıp aynıysa elenir.
    kind_words = {_clean_word(w) for w in kind.split()}
    seen_words: set = set(kind_words)
    seen_texts: set = set()
    deduped = []
    for i, t, c in reversed(kept):          # sağdan sola: baş ada yakın olan kalır
        ws = t.split()
        if len(ws) > 1:                     # "Sporcu Atlet … Atlet" → "Sporcu … Atlet"
            ws = [w for w in ws if _clean_word(w) not in kind_words] or ws
        low = " ".join(_clean_word(w) for w in ws)
        if low in seen_texts or (len(ws) == 1 and low in seen_words):
            continue
        seen_texts.add(low)
        seen_words.update(_clean_word(w) for w in ws)
        deduped.append((i, " ".join(ws), c))
    deduped.reverse()

    def _join(items) -> str:
        return " ".join([t for _i, t, _c in items] + [kind]).strip()

    # Hâlâ uzunsa: en az bilgi taşıyan sınıftan başlayarak, en soldakini at.
    while len(_join(deduped)) > max_len and len(deduped) > 1:
        victim = min(range(len(deduped)),
                     key=lambda k: (_CLASS_RANK.get(deduped[k][2], 0), -deduped[k][0]))
        deduped.pop(victim)
    if len(deduped) == 1 and len(_join(deduped)) > max_len:
        # Tek öbek kaldı ve o da uzun: atarsak geriye yalnız tür kalır
        # ("Premium Motorcu Kurye Yağmurluk … Takım" → "Takım"). Öbeği atmak
        # yerine baştan sığdığı kadar kelime alıyoruz — sınıflandırılmamış
        # metinde de ilk kelimeler ürünü tanımlar.
        i, t, c = deduped[0]
        ws = t.split()
        while ws and len(" ".join(ws)) + 1 + len(kind) > max_len:
            ws.pop()
        deduped = [(i, " ".join(ws), c)] if ws else []

    out = _join(deduped)
    return out if out else s[:max_len].rstrip(" ,-–—")



# ─────────────────────── kategori ağacı ───────────────────────
# Beslemede kategori adları düzensiz: erkek ürünleri "Erkek-T-Shirt" gibi
# cinsiyeti ADIN İÇİNDE taşıyor, "Erkek-T-Shirt" ile "Erkek-T-shirt" ayrı
# sayılıyor (599 + 175 ürün), 292 ürünün kategorisi boş. Ham hâliyle
# aktarılırsa menüde 69 dağınık başlık çıkar.
#
# Kurduğumuz yapı:  Kadın > Elbise ,  Erkek > T-Shirt
# Cinsiyet ANA kategori olur, alt kategori addan arındırılır.
_GENDER_ROOTS = {"kadın": "Kadın", "kadin": "Kadın", "erkek": "Erkek",
                 "unisex": "Unisex", "çocuk": "Çocuk", "cocuk": "Çocuk"}
_OTHER_CAT = "Diğer"

# Kategori adı → id önbelleği. Her içe aktarmanın başında sıfırlanır.
_CAT_CACHE: Dict[str, Any] = {"map": None}


# Ürün TÜRÜNÜ addan çıkarma. Beslemenin Kategori alanı 230 üründe ürün adıyla
# çelişiyor ("Düşük Bel Oversize Keten Pantolon" → Kategori: Bluz). Türkçede
# ürün türü adın SON kelimesidir ve tedarikçinin kategori alanından daha
# güvenilir çıktı.
_TYPE_FROM_NAME = {
    "pantolon": "Pantolon", "elbise": "Elbise", "bluz": "Bluz",
    "takım": "Takım", "takim": "Takım", "etek": "Etek",
    "gömlek": "Gömlek", "gomlek": "Gömlek",
    "tişört": "T-Shirt", "tisort": "T-Shirt", "t-shirt": "T-Shirt", "tshirt": "T-Shirt",
    "şort": "Şort", "sort": "Şort", "tulum": "Tulum", "ceket": "Ceket",
    "kazak": "Kazak", "hırka": "Hırka", "hirka": "Hırka",
    "sweatshirt": "Sweatshirt", "sweat": "Sweatshirt", "atlet": "Atlet",
    "body": "Body", "mont": "Mont", "yelek": "Yelek", "kimono": "Kimono",
    "eşofman": "Eşofman", "esofman": "Eşofman", "tunik": "Tunik",
    "trençkot": "Trençkot", "kaban": "Kaban", "bikini": "Bikini", "mayo": "Mayo",
}

# Tedarikçinin kategorisi addan çıkandan DAHA ÖZEL olduğu durumlar: burada
# ada güvenmek bilgi kaybettirir (Abiye zaten bir elbisedir, Jean bir pantolon).
_KEEP_SUPPLIER_CAT = {"abiye", "jean", "kot", "gecelik", "pijama", "sabahlık"}


def _type_of_word(word: str) -> str:
    """Tek kelimeden ürün türü. Türkçe çekim eklerini tolere eder.

    Besleme "Deniz Şortu", "Elbisesi" gibi iyelik ekli yazıyor; düz sözlük
    araması bunları kaçırıp ürünü türsüz bırakıyordu.
    """
    w = _clean_word(word)
    for cut in (0, 1, 2, 3):
        stem = w[:-cut] if cut else w
        if len(stem) >= 3 and stem in _TYPE_FROM_NAME:
            return _TYPE_FROM_NAME[stem]
    return ""


def _type_from_name(name: str) -> str:
    """Ürün adının son kelimesinden türü çıkarır; tanımazsa boş döner."""
    words = (name or "").strip().split()
    return _type_of_word(words[-1]) if words else ""


def normalize_category(raw: str, gender: str, name: str = "") -> tuple[str, str]:
    """(ana_kategori, alt_kategori) döndürür.

    Ürün adı verilirse ve adın son kelimesi tanınan bir tür ise, tedarikçinin
    kategorisiyle çeliştiğinde ADA güvenilir — besleme 230 üründe hatalı
    kategori veriyor. Tedarikçi kategorisi daha özel olduğunda (Abiye, Jean)
    ona dokunulmaz.
    """
    root = _GENDER_ROOTS.get((gender or "").strip().lower(), "")
    sub = _WS_RX.sub(" ", (raw or "").strip())

    # "Erkek-T-Shirt" → kök "Erkek", alt "T-Shirt"
    if "-" in sub:
        head, _, tail = sub.partition("-")
        if head.strip().lower() in _GENDER_ROOTS:
            root = root or _GENDER_ROOTS[head.strip().lower()]
            sub = tail.strip()

    if not root:
        root = "Diğer"
    # Büyük/küçük harf farkı ayrı kategori üretmesin: "T-shirt" → "T-Shirt"
    sub = "-".join(_tr_title_word(part) for part in sub.split("-")) if sub else ""
    sub = " ".join(_tr_title_word(w) for w in sub.split()) if sub else ""

    by_name = _type_from_name(name)
    if by_name and by_name.lower() != (sub or "").lower():
        if (sub or "").lower() not in _KEEP_SUPPLIER_CAT:
            sub = by_name

    return root, (sub or _OTHER_CAT)


def _cat_key(name: str) -> str:
    """Kategori adını karşılaştırma için sadeleştirir.

    "T-Shirt" / "T-shirt" / "Tshirt" / "t shirt" aynı kategoridir; besleme
    bunları farklı yazıyor ve ham hâliyle her biri ayrı kategori üretir.
    """
    s = (name or "").strip().lower()
    s = s.replace("ı", "i").replace("İ", "i")
    return re.sub(r"[^a-z0-9çğöşü]", "", s)


async def _ensure_category(name: str, parent_id: Optional[str]) -> str:
    """Kategoriyi bulur, yoksa oluşturur; id döndürür. (slug için `_category_slug`)

    Eşleşme sırası:
      1) Aynı ad + aynı üst kategori (tam eşleşme).
      2) Yazım farkına dayanıklı ad eşleşmesi — AMA YALNIZ AYNI ÜST KATEGORİ
         İÇİNDE. Bu sınır olmadan "Kadın > Pantolon" ile "Erkek > Pantolon"
         tek kategoriye düşüyordu: hangi cinsiyet önce aktarıldıysa kategori
         orada açılıyor, diğer cinsiyetin ürünleri de oraya giriyordu. Sonuç:
         kadın pantolon listesinde 44 erkek pantolonu. Aynı ad iki cinsiyette
         de geçtiği için (T-Shirt, Gömlek, Ceket, Mont, Bluz…) sorun genel.
      3) Yoksa yeni oluşturulur.
    """
    q = {"name": name, "parent_id": parent_id}
    doc = await db.categories.find_one(q, {"_id": 0, "id": 1})
    if doc:
        return doc["id"]

    # 2) Ad bazlı eşleşme — aynı üst kategori içinde. Tüm kategorileri her
    # üründe taramak 6.475 üründe ağır olur; bir kez okuyup bellekte tutuyoruz.
    key = (parent_id or "", _cat_key(name))
    if _CAT_CACHE["map"] is None:
        _CAT_CACHE["map"] = {}
        async for c in db.categories.find({}, {"_id": 0, "id": 1, "name": 1, "parent_id": 1}):
            _CAT_CACHE["map"].setdefault(
                (c.get("parent_id") or "", _cat_key(c.get("name"))), c["id"])
    hit = _CAT_CACHE["map"].get(key)
    if hit:
        logger.info(f"[supplier_xml] mevcut kategoriyle eslesti: {name}")
        return hit
    from .categories import generate_slug
    from .deps import generate_short_id
    cid = await generate_short_id("categories")
    slug_base = generate_slug(f"{name}")
    # Aynı ad iki cinsiyette de var (Kadın>Ceket, Erkek>Ceket). Çakışmada
    # kimlik eklemek "ceket-a1b2" gibi okunmaz slug üretiyordu; üst kategori
    # adıyla önekliyoruz → "erkek-ceket".
    if await db.categories.find_one({"slug": slug_base}, {"_id": 0, "id": 1}):
        ust = await db.categories.find_one({"id": parent_id}, {"_id": 0, "name": 1}) if parent_id else None
        onek = generate_slug(ust.get("name")) if (ust or {}).get("name") else ""
        slug_base = f"{onek}-{slug_base}" if onek else f"{slug_base}-{cid}"
        if await db.categories.find_one({"slug": slug_base}, {"_id": 0, "id": 1}):
            slug_base = f"{slug_base}-{cid}"
    await db.categories.insert_one({
        "id": cid, "name": name, "slug": slug_base, "description": "",
        "image": "", "image_url": "", "parent_id": parent_id,
        "sort_order": 0, "is_active": True, "source": "supplier_xml",
        "created_at": _now_iso(),
    })
    logger.info(f"[supplier_xml] kategori olusturuldu: {name} (parent={parent_id})")
    if _CAT_CACHE["map"] is not None:
        _CAT_CACHE["map"].setdefault((parent_id or "", _cat_key(name)), cid)
    return cid


async def _category_slug(cat_id: str) -> str:
    """Kategori slug'ı — vitrinde /kategori/<slug> yolu ve slug bazlı süzme için."""
    if not cat_id:
        return ""
    doc = await db.categories.find_one({"id": cat_id}, {"_id": 0, "slug": 1})
    return (doc or {}).get("slug") or ""


def _tag(el: ET.Element) -> str:
    """Namespace'i atılmış, küçük harfli etiket adı."""
    t = el.tag
    if isinstance(t, str) and "}" in t:
        t = t.split("}", 1)[1]
    return str(t).strip().lower()


def _children_map(el: ET.Element) -> Dict[str, ET.Element]:
    return {_tag(c): c for c in el}


def _text(el: Optional[ET.Element]) -> str:
    if el is None:
        return ""
    return (el.text or "").strip()


_ALL_FIELD_TAGS = {c for cands in CANDIDATES.values() for c in cands}


def _group_score(items: List[ET.Element]) -> int:
    """Bir kardeş grubunun 'ürün listesi' olma puanı.

    Çocuk etiketlerinden kaç tanesi bildiğimiz ürün alanlarına benziyor?
    Sadece 'en çok tekrar eden düğüm' demek yetmez: tek ürünlü bir beslemede
    varyant düğümleri üründen daha kalabalık olur ve yanlış seviye seçilir.
    Alan benzerliği bu tuzağı çözer (ürün düğümü ad/marka/fiyat taşır,
    varyant düğümü yalnız barkod/renk/beden).
    """
    if not items:
        return 0
    tags: set[str] = set()
    for it in items[:5]:
        tags.update(_children_map(it))
    score = len(tags & _ALL_FIELD_TAGS)
    # Görsel taşıyan düğüm ürün olma ihtimalini artırır.
    if any(any(w in t for w in IMAGE_TAGS) for t in tags):
        score += 1
    # Varyant KAPSAYICISI olan düğüm neredeyse kesin üründür.
    if tags & set(VARIANT_CONTAINER):
        score += 3
    return score


def find_item_elements(root: ET.Element) -> List[ET.Element]:
    """Ürün düğümlerini bulur — etiket adından bağımsız.

    <Urunler><Urun>, <Products><Product>, <root><item> hepsi aynı kodla
    çalışır. Aday kardeş grupları puanlanır, en yüksek puanlı seçilir.
    """
    best: List[ET.Element] = []
    best_score = 0

    # Kapsayıcılarda aşağı iniyoruz; kalabalık gruplar zaten ürün listesidir,
    # onların içine girmiyoruz (büyük beslemede tüm ağacı gezmemek için).
    stack: List[tuple[ET.Element, int]] = [(root, 0)]
    while stack:
        parent, depth = stack.pop()
        if depth > 6:
            continue
        groups: Dict[str, List[ET.Element]] = {}
        for child in parent:
            groups.setdefault(_tag(child), []).append(child)
        for items in groups.values():
            if len(list(items[0])) < 2:
                continue  # yaprak/metin düğümü, ürün olamaz
            score = _group_score(items)
            if score > best_score or (score == best_score and len(items) > len(best)):
                best, best_score = items, score
            if len(items) <= 3:
                stack.append((items[0], depth + 1))  # kapsayıcı olabilir, in
    return best


def _variant_nodes(item: ET.Element) -> List[ET.Element]:
    """Ürün düğümünün varyant çocuklarını döndürür (yoksa boş liste)."""
    cm = _children_map(item)
    cont = next((cm[k] for k in cm if k in VARIANT_CONTAINER), None)
    if cont is None:
        return []
    return [c for c in cont if _tag(c) in VARIANT_ITEM] or list(cont)


def detect_mapping(items: List[ET.Element]) -> Dict[str, str]:
    """İlk birkaç üründen alan eşlemesini çıkarır.

    Ürün SEVİYESİ ve VARYANT SEVİYESİ etiketleri birlikte taranır: stok, renk,
    beden gibi alanlar çoğu beslemede yalnız varyantın içindedir; sadece ürün
    çocuklarına bakılırsa bu alanlar hiç tanınmaz ve stok 0 olarak aktarılır.
    Ürün seviyesindeki etiket önceliklidir, varyant seviyesi boşlukları doldurur.
    """
    product_tags: Dict[str, int] = {}
    variant_tags: Dict[str, int] = {}
    for it in items[:25]:
        for t in _children_map(it):
            product_tags[t] = product_tags.get(t, 0) + 1
        for v in _variant_nodes(it)[:5]:
            for t in _children_map(v):
                variant_tags[t] = variant_tags.get(t, 0) + 1

    mapping: Dict[str, str] = {}
    for field, cands in CANDIDATES.items():
        for c in cands:
            if c in product_tags or c in variant_tags:
                mapping[field] = c
                break
    return mapping


def _collect_images(item: ET.Element) -> List[str]:
    """Ürünün görsel adreslerini toplar (Resimler/Resim, Resim1..N, ImageUrl…)."""
    urls: List[str] = []

    def visit(el: ET.Element, depth: int = 0):
        if depth > 3:
            return
        for child in el:
            t = _tag(child)
            txt = _text(child)
            # Etiket adı "Resim1", "ImageUrl", "GorselBuyuk" gibi çeşitlenir;
            # tam eşitlik yerine içerik araması yapıyoruz.
            if txt.startswith("http") and any(w in t for w in IMAGE_TAGS):
                urls.append(txt)
            if list(child):
                visit(child, depth + 1)

    visit(item)
    # Sırayı koruyarak tekilleştir.
    out: List[str] = []
    for u in urls:
        if u not in out:
            out.append(u)
    return out


def _num(s: Any, default: float = 0.0) -> float:
    """'1.234,56' / '1234.56' / '1,5' biçimlerini sayıya çevirir."""
    txt = str(s or "").strip()
    if not txt:
        return default
    txt = re.sub(r"[^\d,.\-]", "", txt)
    if "," in txt and "." in txt:
        # Hangisi ondalık? En sağdaki ayırıcı ondalıktır.
        txt = txt.replace(".", "") if txt.rfind(",") > txt.rfind(".") else txt.replace(",", "")
        txt = txt.replace(",", ".")
    elif "," in txt:
        txt = txt.replace(",", ".")
    try:
        return float(txt)
    except ValueError:
        return default


def _int(s: Any, default: int = 0) -> int:
    return int(_num(s, default))


def _truthy(s: Any) -> bool:
    return str(s or "").strip().lower() in ("1", "true", "evet", "yes", "var", "dahil")


def sale_price(cost: float, *, vat_rate: float, vat_included: bool, markup: float) -> float:
    """Tedarikçi fiyatından müşteriye gösterilecek fiyatı hesaplar.

    Beslemedeki fiyat BAYİ (maliyet) fiyatıdır ve `KDVDahil=false` ise KDV
    HARİÇtir. Olduğu gibi yayınlanırsa ürünler maliyetine — KDV düşünce
    ZARARINA — satılır. Bu yüzden:

        satış = maliyet × kâr_katsayısı  (+ KDV, fiyata dahil değilse)

    `kâr_katsayısı` ayarlardan gelir (`price_markup`). Tanımlanmazsa 1.0
    kullanılır ve içe aktarma sonucunda UYARI dönülür — sessizce marjsız
    fiyat yazmamak için.
    """
    base = max(0.0, float(cost)) * max(0.0, float(markup))
    if not vat_included:
        base *= 1 + (max(0.0, float(vat_rate)) / 100.0)
    return round(base, 2)


def parse_item(item: ET.Element, mapping: Dict[str, str]) -> Dict[str, Any]:
    """Bir XML ürün düğümünü ara temsile çevirir."""
    cm = _children_map(item)

    def g(field: str) -> str:
        tag = mapping.get(field)
        return _text(cm.get(tag)) if tag else ""

    rec: Dict[str, Any] = {
        "name": g("name"),
        "description": g("description"),
        "brand": g("brand"),
        "category": g("category"),
        "supplier_barcode": g("barcode"),
        "supplier_stock_code": g("stock_code"),
        "price": _num(g("price")),
        "list_price": _num(g("list_price")),
        "currency": g("currency") or "TRY",
        "vat": _num(g("vat"), 10.0),
        "vat_included": _truthy(g("kdv_included")),
        "gender": g("gender"),
        "fabric": g("fabric"),
        "desi": _num(g("desi")),
        "supplier_url": g("product_url"),
        "images": _collect_images(item),
        "variants": [],
    }
    # Model referansı: model alanı yoksa stok kodu, o da yoksa barkod.
    # GRUP KODU DOĞRULANIR: besleme bu alanı bazı üründe eksik yolluyor, değer
    # yalnız önekten ibaret kalıyor ("MLN-"). Kimlik sanılıp yazılınca o değeri
    # taşıyan BÜTÜN ürünler tek renk-kardeşi grubuna düşüyor ve alakasız
    # ürünler birbirinin rengi görünüyor (canlıda yaşandı). Kimlik taşımayan
    # kod BOŞ sayılır; ürün kendi başına kalır.
    rec["material"] = g("material") or ""
    rec["group_ref"] = group_ref.temiz_grup(g("group_ref"))
    rec["model_ref"] = (
        g("model_ref") or rec["supplier_stock_code"] or rec["supplier_barcode"] or rec["name"]
    )

    # Varyantlar
    vcont = next((cm[k] for k in cm if k in VARIANT_CONTAINER), None)
    vnodes: List[ET.Element] = []
    if vcont is not None:
        vnodes = [c for c in vcont if _tag(c) in VARIANT_ITEM] or list(vcont)
    if vnodes:
        for v in vnodes:
            vm = _children_map(v)

            def vg(field: str) -> str:
                tag = mapping.get(field)
                return _text(vm.get(tag)) if tag else ""

            color, size = vg("color"), vg("size")
            rec["variants"].append({
                "supplier_barcode": vg("barcode") or "",
                "supplier_stock_code": vg("stock_code") or "",
                "color": color,
                "size": size,
                "fabric": vg("fabric"),
                "stock": _int(vg("stock")),
                "cost": _num(vg("price")) or rec["price"],
                "vat": _num(vg("vat")) or rec["vat"],
                "vat_included": _truthy(vg("kdv_included")) or rec["vat_included"],
                "ref": vg("barcode") or vg("stock_code") or f"{color}-{size}".strip("-"),
            })
    else:
        # Varyantsız ürün: tek satılabilir birim.
        rec["variants"].append({
            "supplier_barcode": rec["supplier_barcode"],
            "supplier_stock_code": rec["supplier_stock_code"],
            "color": g("color"),
            "size": g("size"),
            "fabric": rec["fabric"],
            "stock": _int(g("stock")),
            "cost": rec["price"],
            "vat": rec["vat"],
            "vat_included": rec["vat_included"],
            "ref": rec["supplier_barcode"] or rec["supplier_stock_code"] or "0",
        })
    return rec


# ─────────────────────────── ayarlar ───────────────────────────
async def get_config() -> Dict[str, Any]:
    doc = await db.settings.find_one({"id": SETTINGS_ID}, {"_id": 0}) or {}
    return doc


async def _feed_url() -> str:
    cfg = await get_config()
    enc = cfg.get("url_enc")
    if not enc:
        raise HTTPException(status_code=400,
                            detail="Tedarikçi XML adresi tanımlı değil. Ayarlardan girin.")
    return decrypt(enc)


async def fetch_feed() -> ET.Element:
    """Beslemeyi indirir ve kökü döndürür."""
    url = await _feed_url()
    # Tedarikçi 34 MB'lık XML'i istek anında üretiyor: ilk bayt gelene kadar
    # dakikalarca sessiz kalabiliyor. Tek bir "180 sn" değeri hem bağlanmayı
    # hem de bu bekleyişi kapsadığı için ReadTimeout ile düşüyorduk.
    timeout = httpx.Timeout(connect=30.0, read=600.0, write=60.0, pool=60.0)
    last_err: Exception | None = None
    raw = b""

    for attempt in range(1, 4):
        try:
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
                async with client.stream("GET", url) as resp:
                    resp.raise_for_status()
                    chunks, total = [], 0
                    async for chunk in resp.aiter_bytes():
                        total += len(chunk)
                        if total > _MAX_BYTES:
                            raise HTTPException(
                                status_code=413,
                                detail=f"Besleme {_MAX_BYTES // 1048576} MB sınırını aştı.")
                        chunks.append(chunk)
            raw = b"".join(chunks)
            logger.info(f"[supplier_xml] besleme indirildi: {len(raw)/1048576:.1f} MB "
                        f"({attempt}. deneme)")
            break
        except HTTPException:
            raise
        except (httpx.ReadTimeout, httpx.ConnectTimeout, httpx.RemoteProtocolError) as e:
            last_err = e
            logger.warning(f"[supplier_xml] indirme {attempt}. denemede basarisiz: "
                           f"{type(e).__name__}")
            if attempt < 3:
                await asyncio.sleep(5 * attempt)

    if not raw:
        raise HTTPException(
            status_code=504,
            detail=f"Besleme 3 denemede indirilemedi ({type(last_err).__name__ if last_err else '?'}). "
                   "Tedarikçi yanıt vermiyor olabilir, birkaç dakika sonra tekrar deneyin.")
    try:
        return ET.fromstring(raw)
    except ET.ParseError as e:
        raise HTTPException(status_code=502, detail=f"XML ayrıştırılamadı: {e}")


async def load_items() -> tuple[List[Dict[str, Any]], Dict[str, str]]:
    """Beslemeyi indirir, eşlemeyi çözer, ürünleri ara temsile çevirir."""
    root = await fetch_feed()
    nodes = find_item_elements(root)
    if not nodes:
        raise HTTPException(status_code=502, detail="Beslemede ürün düğümü bulunamadı.")

    cfg = await get_config()
    mapping = dict(cfg.get("mapping") or {})
    detected = detect_mapping(nodes)
    # Panelden girilen eşleme kazanır; eksik alanlar otomatik tespitten tamamlanır.
    for k, v in detected.items():
        mapping.setdefault(k, v)
    if not mapping.get("name"):
        raise HTTPException(
            status_code=502,
            detail=f"Ürün adı alanı tanınamadı. Beslemedeki etiketler: "
                   f"{sorted(_children_map(nodes[0]))[:25]}. Ayarlardan eşleme girin.")

    if mapping != (cfg.get("mapping") or {}):
        await db.settings.update_one(
            {"id": SETTINGS_ID},
            {"$set": {"mapping": mapping, "mapping_detected_at": _now_iso()}},
            upsert=True)

    return [parse_item(n, mapping) for n in nodes], mapping


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ──────────────── tek beden kalmış ürünü kapatma ────────────────
class _RetireProduct(Exception):
    """Ürün tek bedene düşmüş ve o beden de bitiyor → kalıcı kapatılır."""


def tek_beden_bitiyor(variants: List[Dict[str, Any]], esik: int) -> tuple:
    """(kapatilsin_mi, kalan_stok, beden) döndürür.

    Kural: stoğu OLAN beden sayısı 1 ve o bedenin stoğu `esik` ya da altında.
    Beden etiketi olmayan (tek ebat / standart) ürünler de tek bedenli sayılır —
    onlar için de "elde son N adet kaldı" durumu aynıdır.

    Not: renk kardeşleri bu sistemde AYRI ürün kartıdır (aynı stok kodunu
    paylaşırlar), dolayısıyla buradaki varyantlar bedendir; bir rengin bitmesi
    diğer rengi kapatmaz.
    """
    stoklu = [v for v in (variants or []) if int(v.get("stock") or 0) > 0]
    if not stoklu:
        return False, 0, ""          # zaten tamamen tükenmiş — bu kuralın işi değil
    bedenler = {str(v.get("size") or "").strip().lower() for v in stoklu}
    if len(bedenler) != 1:
        return False, 0, ""
    kalan = sum(int(v.get("stock") or 0) for v in stoklu)
    if kalan > esik:
        return False, kalan, ""
    return True, kalan, str(stoklu[0].get("size") or "").strip()


async def kapat_ve_engelle(product: Dict[str, Any], sebep: str,
                           kalan: int = 0, beden: str = "") -> str:
    """Ürünü vitrinden kaldırır ve beslemeye kalıcı olarak kapatır.

    products.py::block_product ile AYNI sözleşme: ürün KALICI SİLİNMEZ
    (`is_deleted`), çünkü geçmiş siparişler ürüne referans veriyor olabilir;
    kaydı yok etmek sipariş detayını bozar. Kara listeye `supplier_key`
    yazılır — aktarım o anahtarı görünce ürünü hiç oluşturmaz.

    Döner: "engellendi" | "silindi" (anahtarsız ürün) | "" (bir şey yapılmadı)
    """
    pid = product.get("id")
    if not pid:
        return ""
    _now = _now_iso()
    await db.products.update_one({"id": pid}, {"$set": {
        "is_active": False,
        "is_deleted": True,
        "manual_deactivated": True,        # besleme geri açmasın
        "manual_deactivated_at": _now,
        "blocked_from_feed": True,
        "deactivated_reason": sebep,
        "retired_single_size": {"kalan": int(kalan), "beden": beden, "at": _now},
        "updated_at": _now,
    }})
    # Vitrinden kalkan ürün pazaryerinde de satılamaz. Bu ürünler tek bedeni
    # tükendiği için emekliye ayrılıyor; Trendyol'daki stoğu duruyorsa sipariş
    # gelir ve tedarikçide mal bulunmaz.
    try:
        from .trendyol_stock_guard import urunu_pazaryerinde_kapat
        await urunu_pazaryerinde_kapat(pid)
    except Exception:
        pass
    key = product.get("supplier_key")
    if not key:
        return "silindi"
    await db.supplier_blocklist.update_one(
        {"supplier_key": key},
        {"$set": {"supplier_key": key, "product_id": pid,
                  "name": product.get("name") or "",
                  "supplier": product.get("supplier") or "",
                  "blocked_by": "sistem: tek beden eşiği",
                  "reason": "tek_beden_esik",
                  "created_at": _now}},
        upsert=True)
    return "engellendi"


# ─────────────────────────── aktarım ───────────────────────────
async def _build_product_doc(rec: Dict[str, Any], supplier: str, markup: float = 1.0,
                             min_stock: int = MIN_STOCK_DEFAULT,
                             tek_beden_esik: int = SINGLE_SIZE_MAX_STOCK_DEFAULT,
                             kdv_dahil: bool = SUPPLIER_VAT_INCLUDED_DEFAULT) -> Dict[str, Any]:
    """Ara temsili ürün dökümanına çevirir; kodları BİZ üretiriz."""
    model_code = await supplier_codes.get_or_create_model_code(
        db, supplier, rec["model_ref"], rec.get("group_ref") or "")

    variants, total_stock = [], 0
    for v in rec["variants"]:
        # Eşiğin altındaki beden HİÇ açılmaz. Kod üretmeden önce eliyoruz;
        # aksi halde satılmayacak varyantlara barkod harcanır.
        if int(v.get("stock") or 0) <= min_stock:
            continue
        codes = await supplier_codes.get_or_create_variant_codes(
            db, supplier, rec["model_ref"], v.get("ref") or "0",
            rec.get("group_ref") or "")
        total_stock += int(v.get("stock") or 0)
        _cost = float(v.get("cost") or rec["price"] or 0)
        variants.append({
            "id": generate_id(),
            "barcode": codes["barcode"],            # BİZİM barkodumuz
            "stock_code": codes["stock_code"],      # BİZİM stok kodumuz
            "color": v.get("color") or "",
            "size": v.get("size") or "",
            "fabric": v.get("fabric") or "",
            "stock": int(v.get("stock") or 0),
            # cost_price = tedarikçiye ödediğimiz; price = müşteriye gösterilen.
            "cost_price": _cost,
            "purchase_price": _cost,
            "price": sale_price(_cost,
                                vat_rate=float(v.get("vat") or rec["vat"] or 0),
                                vat_included=kdv_dahil or bool(v.get("vat_included")),
                                markup=markup),
            # Tedarikçi kodları yalnız eşleme için saklanır, müşteriye gösterilmez.
            "supplier_barcode": v.get("supplier_barcode") or "",
            "supplier_stock_code": v.get("supplier_stock_code") or "",
        })

    # Tüm bedenleri eşiğin altındaysa ürünü hiç açmıyoruz.
    if not variants:
        raise _SkipProduct(f"tüm bedenler stok <= {min_stock}")

    # Tek bedene düşmüş ve o beden de bitiyorsa ürün KALICI kapatılır. Bu,
    # _SkipProduct'tan farklı: o geçici (stok gelirse ürün geri açılır),
    # bu kalıcı (kara listeye yazılır, besleme bir daha getirmez).
    _kapat, _kalan, _beden = tek_beden_bitiyor(variants, tek_beden_esik)
    if _kapat:
        raise _RetireProduct(
            f"tek beden ({_beden or 'tek ebat'}) kaldı, stok {_kalan} <= {tek_beden_esik}")

    primary = variants[0] if variants else {}

    _cat_root, _cat_sub = normalize_category(rec.get("category"), rec.get("gender"),
                                             rec.get("name") or "")
    _root_id = await _ensure_category(_cat_root, None)
    _cat_id = await _ensure_category(_cat_sub, _root_id)
    # ÜST KATEGORİ LİSTELEME: products.py üst kategoriyi `category_ids` içinde
    # arıyor (ata zinciri orada tutulur). Bu alan yazılmadığı için "Kadın" ya da
    # "Erkek" ana kategorisine girildiğinde vitrin BOŞ dönüyordu; yalnız alt
    # kategoriler çalışıyordu (onlar ada göre de eşleşiyor).
    _cat_ids = [i for i in (_root_id, _cat_id) if i]
    _cat_slug = await _category_slug(_cat_id)

    # PAZARYERİ ÖZNİTELİKLERİ + ürün kartının zorunlu temel alanları.
    # Besleme bunları vermiyor (yalnız kumaş/cinsiyet geliyor); ürün adı,
    # kategori ve açıklamadan çıkarıyoruz. Özniteliksiz ürün Trendyol'da ya
    # reddediliyor ya da filtrelerde hiç çıkmıyor; sezon/kalıp boşken panel
    # kaydı reddediyor. Çıkarılamayan alan BOŞ kalır (uydurma yok).
    _ad = normalize_name(rec["name"])
    _oz_kaynak = {
        "name": _ad,
        "description": rec.get("description") or "",
        "fabric": rec.get("fabric") or "",
        "material": rec.get("material") or "",
        "gender": rec.get("gender") or "",
        "brand": rec.get("brand") or "",
        "category_name": _cat_sub,
        "category_path": f"{_cat_root} > {_cat_sub}",
    }
    _ozellikler = product_attributes.urun_ozellikleri(_oz_kaynak)
    _temel = product_attributes.temel_alanlar(_oz_kaynak)

    return {
        "id": generate_id(),
        "name": _ad,
        "attributes": _ozellikler,
        "season": _temel.get("season", ""),
        "size_advice": _temel.get("size_advice", ""),
        "supplier_original_name": rec["name"],
        "description": rec.get("description") or "",
        "brand": rec.get("brand") or "",
        # Kategori ağacı: cinsiyet ANA, beslemedeki kategori ALT.
        "category_id": _cat_id,
        "category_ids": _cat_ids,      # atalar dahil — üst kategori listelemesi buna bakar
        "category_slug": _cat_slug,
        "category_name": _cat_sub,
        "category_root": _cat_root,
        "category_path": f"{_cat_root} > {_cat_sub}",
        "supplier_category_raw": rec.get("category") or "",
        "barcode": primary.get("barcode", ""),
        # STOK KODU KART DÜZEYİNDEDİR: aynı modelin bütün renkleri AYNI stok
        # kodunu taşır, barkod ise her varyantta benzersizdir. Eskiden ürüne
        # ilk varyantın kodu ("MEL-M006783-01") yazılıyordu; her renk farklı
        # kod alıyor, panelden stok koduyla gönderince altı rengin yalnız biri
        # gidiyordu. Varyantların kendi kodları (…-01, …-02) DEĞİŞMEZ.
        "stock_code": model_code or primary.get("stock_code", ""),
        "model_code": model_code,
        # Renk kardeşleri: products.py get_color_siblings bu alanda eşleşenleri
        # "diğer renkler" sayıyor. Grup kodu yazılmadığı için vitrinde hiçbir
        # ürünün alternatif rengi görünmüyordu.
        "urun_karti_id": rec.get("group_ref") or "",
        "supplier_group_ref": rec.get("group_ref") or "",
        # Ürün seviyesinde renk — swatch etiketi ve pazaryeri renk alanı.
        "color": (primary.get("color") or "") if primary else "",
        # Panel "purchase_price" alanını okuyor, kendi hesabımız "cost_price"
        # kullanıyor. İkisini de yazıyoruz; ikisi de müşteriye kapalı alanlar
        # listesinde (_PRODUCT_INTERNAL_FIELDS).
        "cost_price": float(rec.get("price") or 0),
        "purchase_price": float(rec.get("price") or 0),
        "price": (primary.get("price") if primary else 0) or sale_price(
            float(rec.get("price") or 0), vat_rate=float(rec.get("vat") or 0),
            vat_included=kdv_dahil or bool(rec.get("vat_included")), markup=markup),
        "list_price": float(rec.get("list_price") or 0) or None,
        "currency": rec.get("currency") or "TRY",
        "vat_rate": float(rec.get("vat") or 10),
        "stock": total_stock,
        "gender": rec.get("gender") or "",
        "material": rec.get("material") or "",
        "desi": rec.get("desi") or 0,
        "supplier_url": rec.get("supplier_url") or "",
        "images": rec.get("images") or [],
        "variants": variants,
        "is_active": True,
        "source": "supplier_xml",
        "supplier": supplier,
        "supplier_model_ref": rec["model_ref"],
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
    }


async def import_products(*, only_new: bool = True, limit: int = 0) -> Dict[str, Any]:
    """Beslemedeki ürünleri sisteme aktarır.

    only_new=True  → sadece bizde olmayanları açar (günlük iş).
    only_new=False → mevcutların bilgilerini de tazeler.
    """
    cfg = await get_config()
    supplier = cfg.get("supplier_name") or "modayakamoz"
    markup = float(cfg.get("price_markup") or MARKUP_DEFAULT)
    min_stock = int(cfg.get("min_stock", MIN_STOCK_DEFAULT))
    tek_beden_esik = int(cfg.get("single_size_max_stock", SINGLE_SIZE_MAX_STOCK_DEFAULT))
    kdv_dahil = bool(cfg.get("supplier_vat_included", SUPPLIER_VAT_INCLUDED_DEFAULT))
    low_stock = deactivated = deleted = retired = 0
    _CAT_CACHE["map"] = None  # taze oku: panelden kategori eklenmiş olabilir
    records, _ = await load_items()
    await _set_status(phase=f"{len(records)} ürün işleniyor", total=len(records))
    if limit:
        records = records[:limit]

    created = updated = skipped = failed = blocked = 0

    # KARA LİSTE — yönetimin vitrinden sildiği ürünler. Bu anahtarlar bir daha
    # ASLA aktarılmaz; aksi hâlde silinen ürün bir sonraki beslemede geri gelir
    # ve müşteriye tekrar görünür. Bir kez okunup bellekte tutulur.
    blocklist = set()
    async for b in db.supplier_blocklist.find({}, {"_id": 0, "supplier_key": 1}):
        if b.get("supplier_key"):
            blocklist.add(b["supplier_key"])
    if blocklist:
        logger.info(f"[supplier_xml] kara listede {len(blocklist)} urun — atlanacak")

    for rec in records:
        if not rec.get("name"):
            skipped += 1
            continue
        existing = None
        try:
            key = supplier_codes.supplier_key(supplier, rec["model_ref"])
            if key in blocklist:
                blocked += 1
                continue
            existing = await db.products.find_one(
                {"supplier_key": key},
                {"_id": 0, "id": 1, "name": 1, "supplier": 1, "supplier_key": 1,
                 "manual_deactivated": 1, "images_custom": 1})

            doc = await _build_product_doc(rec, supplier, markup, min_stock,
                                           tek_beden_esik, kdv_dahil)
            doc["supplier_key"] = key

            if existing:
                if only_new:
                    skipped += 1
                    continue
                doc.pop("id", None)
                doc.pop("created_at", None)
                # ELLE PASİFE ALINMIŞ ürünü besleme GERİ AÇMAZ. _build_product_doc
                # her ürüne is_active=True yazıyor; bu satır olmadan yönetimin
                # vitrinden pasife aldığı ürün bir sonraki aktarımda tekrar
                # satışa çıkıyordu.
                if existing.get("manual_deactivated"):
                    doc.pop("is_active", None)
                # KENDİ GÖRSELLERİMİZ KORUNUR. Aktarım her ürüne beslemenin
                # görsellerini yazıyordu; yönetim yapay zekâ ile üretip
                # kaydettiği görseller bir sonraki aktarımda siliniyor, ürün
                # yine tedarikçinin düşük çözünürlüklü fotoğraflarına dönüyordu.
                feed_images = doc.pop("images", None)
                # ELLE GİRİLEN ÖZNİTELİK/SEZON/KALIP KORUNUR. Bunları besleme
                # değil biz türetiyoruz; yönetim panelden düzeltince tazeleme
                # turu üstüne yazmamalı. Yalnız BOŞ olanı dolduruyoruz.
                if existing.get("attributes"):
                    doc.pop("attributes", None)
                for _alan in ("season", "size_advice"):
                    if str(existing.get(_alan) or "").strip() or not doc.get(_alan):
                        doc.pop(_alan, None)
                await db.products.update_one({"id": existing["id"]}, {"$set": doc})
                # Kilidi yazma anında denetle: okuma sırasında başlayan bir
                # görsel yüklemesini XML turu eski bilgiyle ezmemeli.
                if feed_images is not None and not existing.get("images_custom"):
                    await db.products.update_one(
                        {"id": existing["id"], "images_custom": {"$ne": True}},
                        {"$set": {"images": feed_images}})
                updated += 1
            else:
                from .products import slug_with_card_id
                doc["slug"] = await slug_with_card_id(doc.get("name") or rec["name"])
                await db.products.insert_one(doc)
                created += 1
        except _RetireProduct as e:
            # Tek bedene düşmüş, o beden de bitiyor → kalıcı kapatma.
            # Ürün bizde yoksa hiç açmıyoruz ama anahtarı yine kara listeye
            # yazıyoruz: aksi hâlde her aktarımda yeniden değerlendirilir ve
            # tedarikçi bir gün 10 adet basarsa ölü ürün vitrine geri döner.
            retired += 1
            if existing:
                await kapat_ve_engelle(existing, str(e))
            else:
                await db.supplier_blocklist.update_one(
                    {"supplier_key": key},
                    {"$set": {"supplier_key": key, "product_id": "",
                              "name": rec.get("name") or "", "supplier": supplier,
                              "blocked_by": "sistem: tek beden eşiği",
                              "reason": "tek_beden_esik", "created_at": _now_iso()}},
                    upsert=True)
        except _SkipProduct:
            # İş kuralı gereği atlandı — HATA DEĞİL, ayrı sayılır.
            low_stock += 1
            if existing:
                # Daha önce açılmıştı, artık eşiğin altında.
                # Hiç sipariş edilmemişse SİL — katalogda ölü kayıt bırakmaz.
                # Sipariş edilmişse yalnız pasife al: geçmiş siparişler ürüne
                # referans veriyor, silinirse sipariş detayı bozulur.
                has_order = await db.orders.find_one(
                    {"items.product_id": existing["id"]}, {"_id": 1})
                if has_order:
                    await db.products.update_one({"id": existing["id"]}, {"$set": {
                        "is_active": False, "status": "passive",
                        "deactivated_reason": f"tedarikçi stoğu <= {min_stock}",
                        "deactivated_at": _now_iso(),
                    }})
                    deactivated += 1
                else:
                    await db.products.delete_one({"id": existing["id"]})
                    deleted += 1
        except Exception as e:
            failed += 1
            logger.error(f"[supplier_xml] ürün aktarılamadı ({rec.get('name')!r}): {e}")

    result = {"created": created, "updated": updated, "skipped": skipped,
              "failed": failed, "total": len(records),
              "price_markup": markup, "min_stock": min_stock, "blocked": blocked,
              "low_stock_skipped": low_stock, "deactivated": deactivated,
              "deleted": deleted,
              "single_size_max_stock": tek_beden_esik, "retired_single_size": retired,
              "at": _now_iso()}
    if markup <= 1.0:
        # Besleme BAYİ fiyatı verir; katsayısız yayınlamak maliyetine satmaktır.
        result["warning"] = ("price_markup ayarlanmamis (1.0). Urunler bayi fiyatina "
                             "satisa acildi, kar marji YOK. Ayarlardan price_markup girin.")
        logger.warning("[supplier_xml] price_markup=1.0 — urunler marjsiz fiyatlandi")
    await db.settings.update_one({"id": SETTINGS_ID},
                                 {"$set": {"last_import": result}}, upsert=True)
    logger.info(f"[supplier_xml] ürün aktarımı: {result}")
    return result


# ──────────────── varyant eşleme (stok senkronu) ────────────────
# Burada yalnız BİZİM ürettiğimiz stok koduna bakılıyordu. Renk kardeşleri için
# kod üretimi grup koduna geçirilince (658179a) aynı varyant için ARTIK BAŞKA
# bir stok kodu üretilir oldu; daha önce aktarılmış ürünlerin kayıtlı kodu
# tutmadı ve besleme her varyantı "beslemede yok" sayıp stoğu SIFIRLADI.
# Katalog tek turda 0 stoğa düştü.
#
# Tedarikçinin barkodu/stok kodu bizim kod üretimimizden BAĞIMSIZ ve beslemede
# değişmiyor — eşleme artık onlarla başlıyor. Bizim kodumuz yedek; beden+renk
# en son çare (aynı üründe iki varyantın bedeni+rengi aynı olamaz).

def _t(x) -> str:
    return str(x or "").strip().lower()


def besleme_haritasi(rec_variants: List[Dict[str, Any]],
                     kodlar: List[str]) -> Dict[str, Dict[str, Any]]:
    """Besleme varyantlarını tüm eşleme anahtarlarıyla indeksler.

    YALNIZ VARYANTA ÖZEL ANAHTAR İNDEKSLENİR. Tedarikçinin stok kodu ÜRÜN
    düzeyinde: aynı modelin bütün bedenleri aynı kodu taşıyor ("MLN-56189"),
    beden yalnız barkodda ayrışıyor ("MLN-56189-STANDART"). Böyle bir anahtar
    indekslenince `setdefault` İLK bedeni tutuyor ve o üründeki HER beden ilk
    bedenin stoğunu alıyordu: olmayan beden stoklu görünüyor, olan beden
    tükenmiş görünüyordu. Bir anahtar birden çok varyanta denk geliyorsa
    haritaya hiç girmez; eşleşme bir alt anahtara düşer.
    """
    sayac: Dict[str, int] = {}
    aday: List[tuple] = []
    for i, v in enumerate(rec_variants or []):
        kod = kodlar[i] if i < len(kodlar) else ""
        anahtarlar = (f"sb:{_t(v.get('supplier_barcode'))}",
                      f"ss:{_t(v.get('supplier_stock_code'))}",
                      f"ref:{_t(v.get('ref'))}",
                      f"kod:{_t(kod)}",
                      f"bs:{_t(v.get('size'))}|{_t(v.get('color'))}")
        for anahtar in anahtarlar:
            if not anahtar.split(":", 1)[1].strip("|"):
                continue                      # boş alanla eşleşme yapma
            sayac[anahtar] = sayac.get(anahtar, 0) + 1
            aday.append((anahtar, v))

    harita: Dict[str, Dict[str, Any]] = {}
    for anahtar, v in aday:
        if sayac.get(anahtar, 0) == 1:        # ayırt edici değilse KULLANMA
            harita.setdefault(anahtar, v)
    return harita


def varyant_bul(v: Dict[str, Any],
                harita: Dict[str, Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Ürün varyantının beslemedeki karşılığı. Güven sırasına göre arar."""
    for anahtar in (f"sb:{_t(v.get('supplier_barcode'))}",
                    f"ss:{_t(v.get('supplier_stock_code'))}",
                    f"kod:{_t(v.get('stock_code'))}",
                    # 'ref' beslemedeki varyantın kendi referansı; burada da
                    # onunla aranmalı. supplier_stock_code ile aranıyordu, yani
                    # bu kol 'ss' ile aynı şeyi tekrar deniyor, farklı bir ad
                    # alanına yanlışlıkla çarpabiliyordu.
                    f"ref:{_t(v.get('ref') or v.get('supplier_barcode'))}",
                    f"bs:{_t(v.get('size'))}|{_t(v.get('color'))}"):
        if not anahtar.split(":", 1)[1].strip("|"):
            continue
        src = harita.get(anahtar)
        if src is not None:
            # Tek XML bedeni kaldığında ürün seviyesindeki stok kodu artık tekil
            # görünür. Eski L/XL kaydına kalan S/M stoğunu taşımaya izin verme.
            # Kimlik eşleşse bile beden/renk çelişkisi gerçek varyant değildir.
            if any(_t(v.get(field)) and _t(src.get(field))
                   and _t(v.get(field)) != _t(src.get(field))
                   for field in ("size", "color")):
                continue
            return src
    return None


async def sync_stock() -> Dict[str, Any]:
    """Stok ve fiyatı besleme ile eşitler. Ürün açmaz."""
    cfg = await get_config()
    supplier = cfg.get("supplier_name") or "modayakamoz"
    min_stock = int(cfg.get("min_stock", MIN_STOCK_DEFAULT))
    tek_beden_esik = int(cfg.get("single_size_max_stock", SINGLE_SIZE_MAX_STOCK_DEFAULT))
    # KÂR KATSAYISI stok senkronunda da gerekli: besleme BAYİ fiyatı veriyor,
    # vitrin fiyatı ondan hesaplanıyor. Ayar boşsa varsayılana düşülür —
    # 1.0'a düşmek ürünleri maliyetine sattırırdı.
    markup = float(cfg.get("price_markup") or MARKUP_DEFAULT)
    kdv_dahil = bool(cfg.get("supplier_vat_included", SUPPLIER_VAT_INCLUDED_DEFAULT))
    records, _ = await load_items()

    # Stok turu yeni barkod üretmez. Kayıtlı yedek eşlemeyi tek okumada alır;
    # her beden için tekrar kod/numara sorgulamak binlerce gereksiz yazma yapıyordu.
    kayitli_kodlar = {d["key"]: d.get("variants") or {}
                      async for d in db.supplier_code_map.find(
                          {"supplier": supplier}, {"_id": 0, "key": 1, "variants": 1})
                      if d.get("key")}

    touched = missing = failed = retired = 0
    eslesmeyen_urun = 0
    eslesme_ornek: List[str] = []
    for rec in records:
        try:
            key = supplier_codes.supplier_key(supplier, rec["model_ref"])
            product = await db.products.find_one(
                {"supplier_key": key},
                {"_id": 0, "id": 1, "name": 1, "supplier": 1, "supplier_key": 1,
                 "variants": 1, "is_deleted": 1})
            if not product:
                missing += 1
                continue

            # EŞLEME — ÖNCE TEDARİKÇİNİN KENDİ KİMLİKLERİ.
            #
            # Burada yalnız BİZİM ürettiğimiz stok koduna bakılıyordu. Renk
            # kardeşleri için kod üretimi grup koduna geçirilince (658179a)
            # aynı varyant için ARTIK BAŞKA bir stok kodu üretilir oldu; daha
            # önce aktarılmış ürünlerin kayıtlı kodu tutmadı ve besleme her
            # varyantı "beslemede yok" sayıp stoğu SIFIRLADI. Katalog tek turda
            # 0 stoğa düştü.
            #
            # Tedarikçinin barkodu/stok kodu bizim kod üretimimizden bağımsız
            # ve beslemede değişmiyor — eşleme artık onlarla başlıyor. Bizim
            # kodumuz yalnız yedek; beden+renk en son çare.
            kodlar = []
            gkey = supplier_codes.supplier_key(supplier, rec.get("group_ref") or rec["model_ref"])
            kayitli = kayitli_kodlar.get(gkey, {})
            for v in rec["variants"]:
                vref = str(v.get("ref") or "0").strip().lower()
                if rec.get("group_ref"):
                    vref = f"{str(rec['model_ref']).strip().lower()}|{vref}"
                vkey = re.sub(r"[.$\x00]", "_", vref) or "0"
                kodlar.append((kayitli.get(vkey) or {}).get("stock_code") or "")
            besleme = besleme_haritasi(rec["variants"], kodlar)

            new_variants, total, eslesen = [], 0, 0
            for v in (product.get("variants") or []):
                src = varyant_bul(v, besleme)
                if src is not None:
                    eslesen += 1
                    # Beslemeye geri döndüyse "kayıp" işareti kalkar.
                    v.pop("feed_missing", None)
                    v.pop("feed_missing_at", None)
                    _st = int(src.get("stock") or 0)
                    # Eşiğin altına düşen beden SATILAMAZ hale gelir. Stoğu
                    # olduğu gibi yazsak "son 3 ürün" diye satılır ve sipariş
                    # gelince tedarikçide bulunamaz — iptal ve puan kaybı.
                    v["stock"] = _st if _st > min_stock else 0
                    # FİYAT: beslemedeki değer BAYİ (maliyet) fiyatıdır.
                    # Olduğu gibi yazmak ürünü maliyetine — KDV düşünce
                    # ZARARINA — satmaktır. Katsayı + KDV uygulanır.
                    _c = float(src.get("cost") or src.get("price") or 0)
                    if _c > 0:
                        v["cost_price"] = _c
                        v["purchase_price"] = _c
                        v["price"] = sale_price(
                            _c,
                            vat_rate=float(src.get("vat") or rec.get("vat") or 0),
                            vat_included=kdv_dahil or bool(src.get("vat_included")),
                            markup=markup)
                else:
                    # Beslemeden düşen varyant → stoğu sıfırla, SİLME.
                    # Silmek cazip ama yanlış: barkod kaybolunca pazaryerindeki
                    # ilanın bizde karşılığı kalmaz ve orada satışta kalır
                    # (öksüz barkod). Kayıt kalsın, satılamaz olsun, panelde
                    # "beslemede yok" diye görünsün.
                    v["stock"] = 0
                    if not v.get("feed_missing"):
                        v["feed_missing_at"] = _now_iso()
                    v["feed_missing"] = True
                total += int(v.get("stock") or 0)
                new_variants.append(v)

            # GÜVENLİK KİLİDİ — toptan sıfırlamaya izin verme.
            # Ürün beslemede DURUYOR ama hiçbir varyantı eşleşmediyse bu bir
            # stok gerçeği değil, bir EŞLEME ARIZASIDIR (kod üretimi değişti,
            # tedarikçi referansları değişti…). Böyle bir turda stoğu sıfırlamak
            # katalogu satılamaz hâle getirir. Dokunmuyoruz, sayıp bildiriyoruz.
            if (product.get("variants") and rec.get("variants")
                    and eslesen == 0):
                eslesmeyen_urun += 1
                if len(eslesme_ornek) < 8:
                    eslesme_ornek.append(product.get("name", "")[:44])
                continue

            # ÜRÜN FİYATI — BURASI KATALOGU MALİYETİNE SATTIRIYORDU.
            # `rec["price"]` beslemedeki BAYİ fiyatıdır; doğrudan `price`
            # alanına yazılıyordu. Yani her stok turunda vitrin fiyatı
            # maliyete iniyor, ürünler KDV düşünce zararına satılıyordu
            # (canlıda görüldü: varyant 1460 TL, ürün 442 TL).
            # Artık aynı hesap: maliyet × katsayı + KDV.
            _urun_maliyet = float(rec.get("price") or 0)
            _yeni_fiyat = sale_price(
                _urun_maliyet, vat_rate=float(rec.get("vat") or 0),
                vat_included=kdv_dahil or bool(rec.get("vat_included")),
                markup=markup) if _urun_maliyet > 0 else None
            _set = {"variants": new_variants, "stock": total,
                    "stock_synced_at": _now_iso()}
            if _yeni_fiyat:
                _set["price"] = _yeni_fiyat
                _set["cost_price"] = _urun_maliyet
                _set["purchase_price"] = _urun_maliyet
            await db.products.update_one({"id": product["id"]}, {"$set": _set})
            touched += 1

            # TEK BEDEN KALDIYSA KALICI KAPAT. Kural asıl BURADA tetiklenir:
            # ürün katalogda dururken bedenleri tek tek tükenir. Stok yazıldıktan
            # SONRA bakıyoruz ki karar en güncel veriyle verilsin.
            #
            # YALNIZ TAM EŞLEŞMEDE: bir kısmı eşleşmemişse kalan bedenler
            # "tükendi" diye görünür ve sağlam ürün kalıcı olarak kapatılırdı.
            # Kapatma geri alınması zor bir karar; şüphede kapatmıyoruz.
            if (not product.get("is_deleted")
                    and eslesen == len(product.get("variants") or [])):
                _kapat, _kalan, _beden = tek_beden_bitiyor(new_variants, tek_beden_esik)
                if _kapat:
                    await kapat_ve_engelle(
                        product,
                        f"tek beden ({_beden or 'tek ebat'}) kaldı, "
                        f"stok {_kalan} <= {tek_beden_esik}", _kalan, _beden)
                    retired += 1
        except Exception as e:
            failed += 1
            logger.error(f"[supplier_xml] stok eşitlenemedi ({rec.get('name')!r}): {e}")

    # Bütün ürünün XML'den düşmesi de eski stok bırakmamalı. İki ayrı tam
    # okuma aynı kaybı doğrulamadan kapatma yapmayız; ciddi küçülen veya
    # hatalı beslemede bu adım atlanır. Kimlikler ve manuel kapatma korunur.
    source_keys = {supplier_codes.supplier_key(supplier, r["model_ref"]) for r in records}
    previous_keys = set(cfg.get("stock_feed_keys") or [])
    absent_zeroed = absent_alias_skipped = 0
    source_barcodes = {v.get("supplier_barcode") for r in records for v in r.get("variants", [])
                       if v.get("supplier_barcode")}
    complete = (bool(source_keys) and bool(previous_keys)
                and len(source_keys) >= len(previous_keys) * 0.9)
    if not failed and complete:
        async for absent in db.products.find(
                {"supplier": supplier, "supplier_key": {"$nin": list(source_keys | previous_keys)}},
                {"_id": 0, "id": 1, "stock": 1, "variants": 1}):
            original = absent.get("variants") or []
            if any(v.get("supplier_barcode") in source_barcodes for v in original):
                absent_alias_skipped += 1
                continue
            if not absent.get("stock") and all(v.get("feed_missing") for v in original):
                continue
            stamp = _now_iso()
            closed = [{**v, "stock": 0, "feed_missing": True,
                       "feed_missing_at": v.get("feed_missing_at") or stamp} for v in original]
            saved = await db.products.update_one(
                {"id": absent["id"], "variants": original},
                {"$set": {"variants": closed, "stock": 0, "stock_synced_at": stamp}})
            absent_zeroed += saved.modified_count
    if not failed and source_keys and (not previous_keys or complete):
        await db.settings.update_one({"id": SETTINGS_ID}, {"$set": {
            "stock_feed_keys": sorted(source_keys), "stock_feed_checked_at": _now_iso()}}, upsert=True)

    result = {"updated": touched, "not_in_catalog": missing, "failed": failed,
              "total": len(records), "absent_products_zeroed": absent_zeroed, "absent_alias_skipped": absent_alias_skipped,
              "retired_single_size": retired,
              "single_size_max_stock": tek_beden_esik,
              "match_failed_products": eslesmeyen_urun,
              "match_failed_samples": eslesme_ornek, "at": _now_iso()}
    if eslesmeyen_urun:
        result["warning"] = (
            f"{eslesmeyen_urun} üründe HİÇBİR varyant beslemeyle eşleşmedi; "
            "stokları SIFIRLANMADI (eşleme arızası olabilir). "
            "scripts/diag_stock_sync.py ile inceleyin.")
        logger.warning(f"[supplier_xml] eslesmeyen urun: {eslesmeyen_urun}")
    await db.settings.update_one({"id": SETTINGS_ID},
                                 {"$set": {"last_stock_sync": result}}, upsert=True)
    logger.info(f"[supplier_xml] stok senkronu: {result}")
    return result


# ─────────────────────────── yönetim uçları ───────────────────────────
@router.get("/config")
async def read_config(_admin=Depends(require_admin)):
    cfg = await get_config()
    url = ""
    if cfg.get("url_enc"):
        try:
            # Şifreyi maskeleyerek göster — panelde ekrana düz parola yazılmasın.
            url = re.sub(r"(sifre|password|pass)=[^&]*", r"\1=****",
                         decrypt(cfg["url_enc"]), flags=re.I)
        except Exception:
            url = ""
    return {
        "url_masked": url,
        "configured": bool(cfg.get("url_enc")),
        "supplier_name": cfg.get("supplier_name") or "modayakamoz",
        "enabled": bool(cfg.get("enabled")),
        "price_markup": float(cfg.get("price_markup") or MARKUP_DEFAULT),
        "min_stock": int(cfg.get("min_stock", MIN_STOCK_DEFAULT)),
        "single_size_max_stock": int(cfg.get("single_size_max_stock",
                                             SINGLE_SIZE_MAX_STOCK_DEFAULT)),
        "supplier_vat_included": bool(cfg.get("supplier_vat_included",
                                              SUPPLIER_VAT_INCLUDED_DEFAULT)),
        "mapping": cfg.get("mapping") or {},
        "last_import": cfg.get("last_import"),
        "last_stock_sync": cfg.get("last_stock_sync"),
    }


@router.put("/config")
async def write_config(payload: dict, _admin=Depends(require_admin)):
    """Besleme adresi ve eşleme ayarları. URL şifreli saklanır."""
    update: Dict[str, Any] = {}
    if payload.get("url"):
        update["url_enc"] = encrypt(str(payload["url"]).strip())
    if "enabled" in payload:
        update["enabled"] = bool(payload["enabled"])
    if payload.get("price_markup") is not None:
        try:
            _m = float(payload["price_markup"])
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="price_markup sayi olmali.")
        if _m < 1.0:
            raise HTTPException(status_code=400,
                                detail="price_markup 1.0'dan kucuk olamaz (zararina satis).")
        update["price_markup"] = _m
    if payload.get("min_stock") is not None:
        try:
            _ms = int(payload["min_stock"])
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="min_stock tam sayı olmalı.")
        if _ms < 0:
            raise HTTPException(status_code=400, detail="min_stock negatif olamaz.")
        update["min_stock"] = _ms
    if payload.get("single_size_max_stock") is not None:
        try:
            _ts = int(payload["single_size_max_stock"])
        except (TypeError, ValueError):
            raise HTTPException(status_code=400,
                                detail="single_size_max_stock tam sayı olmalı.")
        if _ts < 0:
            raise HTTPException(status_code=400,
                                detail="single_size_max_stock negatif olamaz.")
        update["single_size_max_stock"] = _ts
    if "supplier_vat_included" in payload:
        update["supplier_vat_included"] = bool(payload["supplier_vat_included"])
    if payload.get("supplier_name"):
        update["supplier_name"] = str(payload["supplier_name"]).strip()
    if isinstance(payload.get("mapping"), dict):
        update["mapping"] = {k: str(v).strip().lower()
                             for k, v in payload["mapping"].items() if v}
    if not update:
        raise HTTPException(status_code=400, detail="Güncellenecek alan yok.")
    update["updated_at"] = _now_iso()
    await db.settings.update_one({"id": SETTINGS_ID}, {"$set": update}, upsert=True)
    return {"success": True}


@router.post("/preview")
async def preview(_admin=Depends(require_admin)):
    """Beslemeyi indirir, tanınan eşlemeyi ve ilk 3 ürünü döndürür.

    Ayarları kaydetmeden önce 'doğru okuyor muyuz' kontrolü için.
    """
    records, mapping = await load_items()
    return {"detected_mapping": mapping, "product_count": len(records),
            "sample": records[:3]}


@router.post("/import")
async def run_import(payload: dict | None = None, _admin=Depends(require_admin)):
    """İçe aktarmayı ARKA PLANDA başlatır ve hemen döner.

    6.475 ürünlük iş istek-yanıt döngüsüne sığmıyor: besleme indirmesi
    dakikalarca sürebiliyor, üstüne nginx 600 sn'de kesiyor ve istemcinin
    bağlantısı koptuğunda iş yarıda kalmış görünüyordu. Artık iş sunucuda
    bağımsız çalışır; ilerleme /import-status ile sorulur.
    """
    p = payload or {}
    st = (await get_config()).get("import_status") or {}
    # KİLİT KENDİLİĞİNDEN AÇILIR. İş `asyncio.create_task` ile çalışıyor;
    # sunucu yeniden başlarsa (deploy) iş ölüyor ama `running` bayrağı sonsuza
    # kadar açık kalıyor ve içe aktarma bir daha HİÇ çalıştırılamıyor. Canlıda
    # bayrak 10 Ağustos'tan beri açıktı: iki ay boyunca elle içe aktarma 409
    # dönüyordu. Makul bir süreden eski kilit, biten değil ÖLEN bir iştir.
    _BAYAT_SAAT = 3
    if st.get("running"):
        _bayat = False
        try:
            from datetime import datetime, timezone, timedelta
            _bas = datetime.fromisoformat(str(st.get("started_at") or "").replace("Z", "+00:00"))
            if _bas.tzinfo is None:
                _bas = _bas.replace(tzinfo=timezone.utc)
            _bayat = (datetime.now(timezone.utc) - _bas) > timedelta(hours=_BAYAT_SAAT)
        except Exception:
            _bayat = True          # tarih okunamıyorsa kilide güvenilmez
        if _bayat:
            logger.warning("[supplier_xml] bayat içe aktarma kilidi temizlendi "
                           "(başlangıç: %s)", st.get("started_at"))
            await _set_status(running=False, phase="bayat kilit temizlendi",
                              error=None)
        else:
            raise HTTPException(status_code=409,
                                detail=f"Zaten bir içe aktarma sürüyor (başlangıç: {st.get('started_at')}).")

    only_new = bool(p.get("only_new", True))
    limit = int(p.get("limit") or 0)

    async def _job():
        await _set_status(running=True, started_at=_now_iso(), phase="besleme indiriliyor",
                          result=None, error=None)
        try:
            res = await import_products(only_new=only_new, limit=limit)
            await _set_status(running=False, finished_at=_now_iso(), phase="tamamlandı",
                              result=res, error=None)
            logger.info(f"[supplier_xml] ice aktarma bitti: {res}")
        except Exception as e:
            await _set_status(running=False, finished_at=_now_iso(), phase="hata",
                              result=None, error=f"{type(e).__name__}: {e}")
            logger.exception("[supplier_xml] ice aktarma basarisiz")

    asyncio.create_task(_job())
    return {"started": True,
            "message": "İçe aktarma arka planda başladı. Durum: GET /api/supplier-xml/import-status"}


async def _set_status(**fields) -> None:
    await db.settings.update_one(
        {"id": SETTINGS_ID},
        {"$set": {f"import_status.{k}": v for k, v in fields.items()}},
        upsert=True)


@router.get("/import-status")
async def import_status(_admin=Depends(require_admin)):
    """Arka plandaki içe aktarmanın durumu + anlık ürün/kategori sayısı."""
    st = (await get_config()).get("import_status") or {}
    return {
        **st,
        "products": await db.products.count_documents({}),
        "categories": await db.categories.count_documents({}),
    }


@router.post("/sync-stock")
async def run_stock_sync(payload: dict | None = None, _admin=Depends(require_admin)):
    """Stok/fiyat senkronunu ARKA PLANDA başlatır ve hemen döner.

    6.700 kalemlik iş istek-yanıt döngüsüne sığmıyor: canlıda vekil 116.
    saniyede 502 veriyor, istemcinin bağlantısı kopunca coroutine iptal oluyor
    ve senkron YARIDA kalıyor — panelden elle çalıştırmak bu yüzden hiç
    tamamlanmıyordu. Artık iş sunucuda bağımsız çalışır, durum
    /sync-stock-status ile sorulur.

    `bekle: true` ile eski (senkron) davranış korunur — betikler için.
    """
    p = payload or {}
    if p.get("bekle"):
        return await sync_stock()

    st = (await get_config()).get("stock_status") or {}
    if st.get("running"):
        _bayat = False
        try:
            from datetime import datetime, timezone, timedelta
            _bas = datetime.fromisoformat(str(st.get("started_at") or "").replace("Z", "+00:00"))
            if _bas.tzinfo is None:
                _bas = _bas.replace(tzinfo=timezone.utc)
            _bayat = (datetime.now(timezone.utc) - _bas) > timedelta(hours=2)
        except Exception:
            _bayat = True
        if not _bayat:
            raise HTTPException(status_code=409,
                                detail=f"Zaten bir stok senkronu sürüyor (başlangıç: {st.get('started_at')}).")
        logger.warning("[supplier_xml] bayat stok senkronu kilidi temizlendi (%s)",
                       st.get("started_at"))

    async def _job():
        await _set_stock_status(running=True, started_at=_now_iso(),
                                result=None, error=None)
        try:
            res = await sync_stock()
            await _set_stock_status(running=False, finished_at=_now_iso(),
                                    result=res, error=None)
            logger.info(f"[supplier_xml] stok senkronu bitti: {res}")
        except Exception as e:
            await _set_stock_status(running=False, finished_at=_now_iso(),
                                    result=None, error=f"{type(e).__name__}: {e}")
            logger.exception("[supplier_xml] stok senkronu basarisiz")

    asyncio.create_task(_job())
    return {"started": True,
            "message": "Stok senkronu arka planda başladı. Durum: GET /api/supplier-xml/sync-stock-status"}


async def _set_stock_status(**fields) -> None:
    await db.settings.update_one(
        {"id": SETTINGS_ID},
        {"$set": {f"stock_status.{k}": v for k, v in fields.items()}},
        upsert=True)


@router.get("/sync-stock-status")
async def stock_sync_status(_admin=Depends(require_admin)):
    """Arka plandaki stok senkronunun durumu."""
    cfg = await get_config()
    return {**(cfg.get("stock_status") or {}),
            "last_stock_sync": cfg.get("last_stock_sync")}
