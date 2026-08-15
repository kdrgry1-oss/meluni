"""
trendyol_category_match.py — Yerel kategorileri Trendyol kategorilerine eşler.

NEDEN: Ürün kartındaki "Trendyol Özellikleri" bölümü şemayı
`/trendyol/categories/{id}/attributes` ile çeker; o id yerel kategori
belgesindeki `trendyol_category_id` alanından gelir. Alan boşken panel hiçbir
şey çekemez — "özellik kütüphanesi boş" görünür, zorunlu alanlar dolmaz ve ürün
pazaryerine gidince reddedilir. 61 kategoriyi elle eşleştirmek yerine adlardan
otomatik eşliyoruz.

YANLIŞ EŞLEŞME PAHALIDIR: ürün yanlış kategoride listelenir, zorunlu alanları
tutmaz. Bu yüzden her eşleşme PUANLANIR; eşiğin altındakiler yazılmaz, "elle
seçilecek" diye raporlanır. Cinsiyet kapısı zorunludur: "Kadın > Şort" asla
"Erkek > Şort" ile eşleşmez.
"""
from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional, Tuple

# Yerel ad → Trendyol'da geçen karşılık(lar). Yalnız adları farklı olanlar;
# aynı yazılanlar zaten puanla bulunuyor.
_ESANLAM: Dict[str, List[str]] = {
    "sweat": ["sweatshirt"],
    "jean": ["jean pantolon", "jean"],
    "crop": ["crop bluz", "crop top", "bluz"],
    "takım": ["alt üst takım", "takım elbise", "eşofman takımı"],
    "ikili takım": ["alt üst takım"],
    "alt üst takım": ["alt üst takım", "eşofman takımı"],
    "pijama takımı": ["pijama takımı", "pijama"],
    "eşofman altı": ["eşofman altı"],
    "polo yaka t-shirt": ["polo yaka t-shirt", "polo t-shirt"],
    "deniz şortu": ["deniz şortu", "mayo şort"],
    "triko": ["kazak", "triko"],
    "bodysuit": ["body", "bodysuit"],
    "sütyen": ["sütyen"],
    "abiye": ["abiye elbise", "abiye"],
    "tunik": ["tunik"],
    "tulum": ["tulum"],
    "pareo": ["pareo", "plaj elbisesi"],
}

# Eşleşmesi anlamsız yerel adlar — uydurma eşleştirme yapılmaz.
_ATLA = {"diğer", "diger", "kampanya", "yeni", "indirim"}

# Aday yolunda geçtiğinde eşleşmeyi zayıflatan bölümler: yerel adda karşılığı
# yoksa yanlış rafa koyar (kostüm, hamile, iç giyim…).
_UZAK_BOLUM = ["kostüm", "hamile", "büyük beden", "tesettür", "aksesuar",
               "iç giyim", "ev giyim", "spor giyim", "meslek"]

_CINSIYET = ["kadın", "erkek", "kız çocuk", "erkek çocuk", "çocuk", "bebek", "unisex"]


def _kucult(s: Any) -> str:
    return str(s or "").replace("I", "ı").replace("İ", "i").lower().strip()


def _sade(s: Any) -> str:
    """Karşılaştırma için ad: küçük harf, noktalama yerine boşluk, tek boşluk."""
    return re.sub(r"\s+", " ", re.sub(r"[^\wçğıöşü]+", " ", _kucult(s))).strip()


def _parcalar(s: str) -> set:
    return {p for p in _sade(s).split(" ") if len(p) > 1}


def duz_kategoriler(agac: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Trendyol kategori ağacını düz listeye açar.

    Her kayıt: {"id", "name", "yol" (üst adlar dahil), "yaprak"}.
    Ürün YALNIZ yaprak kategoriye gönderilebilir; ara düğüme eşleşmek gönderimi
    baştan reddettirir, o yüzden yaprak bilgisi taşınır.
    """
    duz: List[Dict[str, Any]] = []

    def _gez(dugum: Dict[str, Any], ust: List[str]):
        ad = str(dugum.get("name") or "")
        yol = ust + [ad]
        alt = dugum.get("subCategories") or []
        duz.append({"id": dugum.get("id"), "name": ad,
                    "yol": " > ".join(yol), "yaprak": not alt})
        for c in alt:
            if isinstance(c, dict):
                _gez(c, yol)

    for k in agac or []:
        if isinstance(k, dict):
            _gez(k, [])
    return duz


def _cinsiyet(metin: str) -> str:
    m = _sade(metin)
    for c in _CINSIYET:                       # "kız çocuk" önce, "çocuk" sonra
        if c in m:
            return c
    return ""


def _puan(yerel_ad: str, yerel_kok: str, aday: Dict[str, Any]) -> int:
    """0–100. 70 ve üstü otomatik yazılabilir sayılır."""
    if not aday.get("yaprak"):
        return 0                              # ara düğüme ürün gönderilemez
    aday_ad = _sade(aday.get("name"))
    aday_yol = _sade(aday.get("yol"))
    if not aday_ad:
        return 0

    # Cinsiyet kapısı — yanlış cinsiyet felakettir, puanla telafi edilmez.
    yc = _sade(yerel_kok)
    if yc:
        ac = _cinsiyet(aday_yol)
        if not ac or (yc not in ac and ac not in yc):
            return 0

    adaylar_ad = [_sade(x) for x in ([yerel_ad] + _ESANLAM.get(_kucult(yerel_ad), []))]
    if aday_ad in adaylar_ad:
        p = 100 if aday_ad == _sade(yerel_ad) else 92
    else:
        y = _parcalar(yerel_ad) | set().union(
            *[_parcalar(x) for x in _ESANLAM.get(_kucult(yerel_ad), [])] or [set()])
        a = _parcalar(aday_ad)
        if not y or not a:
            return 0
        ortak = y & a
        if not ortak:
            return 0
        p = int(70 * len(ortak) / len(y | a)) + (10 if y <= a or a <= y else 0)

    for bolum in _UZAK_BOLUM:                 # yerel adda yoksa uzak raf
        if bolum in aday_yol and bolum not in _sade(yerel_ad):
            p -= 25
    p -= min(8, max(0, aday_yol.count(">") - 2) * 2)   # çok derin yol: daha özel
    return max(0, min(100, p))


def eslestir(yerel_ad: str, yerel_kok: str,
             adaylar: List[Dict[str, Any]]) -> Tuple[Optional[Dict[str, Any]], int, List[Dict[str, Any]]]:
    """En iyi adayı, puanını ve ilk üç alternatifi döner."""
    if _kucult(yerel_ad) in _ATLA:
        return None, 0, []
    puanli = [(_puan(yerel_ad, yerel_kok, a), a) for a in adaylar]
    puanli = [(p, a) for p, a in puanli if p > 0]
    if not puanli:
        return None, 0, []
    puanli.sort(key=lambda x: (-x[0], len(x[1].get("yol") or "")))
    en_iyi = puanli[0]
    return en_iyi[1], en_iyi[0], [a for _p, a in puanli[1:4]]
