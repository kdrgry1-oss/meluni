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

import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException

from .deps import db, logger, generate_id, require_admin
from security.crypto import encrypt, decrypt
import supplier_codes

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
    "desi":        ["desi", "kargodesi"],
    "product_url": ["urunurl", "producturl", "link", "url"],
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
_GENDER_PREFIX = ("kadın", "kadin", "kadı", "erkek", "unisex", "çocuk", "cocuk")
NAME_MAX_LEN = 70

# STOK EŞİĞİ — dropshipping güvenliği.
# Tedarikçide stoğu az kalan bir bedeni satışa açmak, sipariş gelince malı
# bulamamak demektir: müşteriye iptal, Trendyol'da satıcı puanı kaybı.
# Eşik VARYANT bazında uygulanır; stoğu bol bedenler ayakta kalır, yalnız
# riskli beden düşer. Gerçek beslemede 4.461 varyant bu kurala takılıyor.
MIN_STOCK_DEFAULT = 5


def _tr_title_word(w: str) -> str:
    if not w:
        return w
    if w.lower() in _KEEP_LOWER:
        return w.lower()
    if w.isupper() and len(w) > 1:
        return w  # T-SHIRT, XL gibi kısaltmalara dokunma
    return _TR_UP.get(w[0], w[0].upper()) + w[1:]


def normalize_name(raw: str, max_len: int = NAME_MAX_LEN) -> str:
    """Tedarikçi ürün adını mağaza için düzenler.

    Beslemedeki ad alanı çoğu üründe pazarlama metni taşıyor; en uzunu 206
    karakter. Kartta taşıyor, SEO'da da zararlı. Gerçek ad tirenin SOLUNDA
    kalıyor, sağı açıklamaya ait — kesme noktası orası.
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

    s = " ".join(_tr_title_word(w) for w in s.split())

    # Ardışık tekrarları at ("Bluz Bluz").
    out = []
    for w in s.split():
        if not out or out[-1].lower() != w.lower():
            out.append(w)
    s = " ".join(out)

    if len(s) > max_len:
        cut = s[:max_len].rsplit(" ", 1)[0].rstrip(" ,-–—")
        s = cut if len(cut) >= 25 else s[:max_len].rstrip(" ,-–—")
    return s



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


def normalize_category(raw: str, gender: str) -> tuple[str, str]:
    """(ana_kategori, alt_kategori) döndürür."""
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
    return root, (sub or _OTHER_CAT)


async def _ensure_category(name: str, parent_id: Optional[str]) -> str:
    """Kategoriyi bulur, yoksa oluşturur; id döndürür. Aynı ad + aynı ebeveyn
    ikinci kez oluşturulmaz (her içe aktarmada kopya birikmesin)."""
    q = {"name": name, "parent_id": parent_id}
    doc = await db.categories.find_one(q, {"_id": 0, "id": 1})
    if doc:
        return doc["id"]
    from .categories import generate_slug
    from .deps import generate_short_id
    cid = await generate_short_id("categories")
    slug_base = generate_slug(f"{name}")
    # Aynı slug başka bir ebeveynde olabilir (Kadın>Ceket, Erkek>Ceket).
    if await db.categories.find_one({"slug": slug_base}, {"_id": 0, "id": 1}):
        slug_base = f"{slug_base}-{cid}"
    await db.categories.insert_one({
        "id": cid, "name": name, "slug": slug_base, "description": "",
        "image": "", "image_url": "", "parent_id": parent_id,
        "sort_order": 0, "is_active": True, "source": "supplier_xml",
        "created_at": _now_iso(),
    })
    logger.info(f"[supplier_xml] kategori olusturuldu: {name} (parent={parent_id})")
    return cid


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
    async with httpx.AsyncClient(timeout=180.0, follow_redirects=True) as client:
        async with client.stream("GET", url) as resp:
            resp.raise_for_status()
            chunks, total = [], 0
            async for chunk in resp.aiter_bytes():
                total += len(chunk)
                if total > _MAX_BYTES:
                    raise HTTPException(status_code=413,
                                        detail=f"Besleme {_MAX_BYTES // 1048576} MB sınırını aştı.")
                chunks.append(chunk)
    raw = b"".join(chunks)
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


# ─────────────────────────── aktarım ───────────────────────────
async def _build_product_doc(rec: Dict[str, Any], supplier: str, markup: float = 1.0,
                             min_stock: int = MIN_STOCK_DEFAULT) -> Dict[str, Any]:
    """Ara temsili ürün dökümanına çevirir; kodları BİZ üretiriz."""
    model_code = await supplier_codes.get_or_create_model_code(db, supplier, rec["model_ref"])

    variants, total_stock = [], 0
    for v in rec["variants"]:
        codes = await supplier_codes.get_or_create_variant_codes(
            db, supplier, rec["model_ref"], v.get("ref") or "0")
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
            "price": sale_price(_cost,
                                vat_rate=float(v.get("vat") or rec["vat"] or 0),
                                vat_included=bool(v.get("vat_included")),
                                markup=markup),
            # Tedarikçi kodları yalnız eşleme için saklanır, müşteriye gösterilmez.
            "supplier_barcode": v.get("supplier_barcode") or "",
            "supplier_stock_code": v.get("supplier_stock_code") or "",
        })

    # Tüm bedenleri eşiğin altındaysa ürünü hiç açmıyoruz.
    if not variants:
        raise _SkipProduct(f"tüm bedenler stok <= {min_stock}")

    primary = variants[0] if variants else {}

    _cat_root, _cat_sub = normalize_category(rec.get("category"), rec.get("gender"))
    _root_id = await _ensure_category(_cat_root, None)
    _cat_id = await _ensure_category(_cat_sub, _root_id)

    return {
        "id": generate_id(),
        "name": normalize_name(rec["name"]),
        "supplier_original_name": rec["name"],
        "description": rec.get("description") or "",
        "brand": rec.get("brand") or "",
        # Kategori ağacı: cinsiyet ANA, beslemedeki kategori ALT.
        "category_id": _cat_id,
        "category_name": _cat_sub,
        "category_root": _cat_root,
        "category_path": f"{_cat_root} > {_cat_sub}",
        "supplier_category_raw": rec.get("category") or "",
        "barcode": primary.get("barcode", ""),
        "stock_code": primary.get("stock_code", ""),
        "model_code": model_code,
        "cost_price": float(rec.get("price") or 0),
        "price": (primary.get("price") if primary else 0) or sale_price(
            float(rec.get("price") or 0), vat_rate=float(rec.get("vat") or 0),
            vat_included=bool(rec.get("vat_included")), markup=markup),
        "list_price": float(rec.get("list_price") or 0) or None,
        "currency": rec.get("currency") or "TRY",
        "vat_rate": float(rec.get("vat") or 10),
        "stock": total_stock,
        "gender": rec.get("gender") or "",
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
    markup = float(cfg.get("price_markup") or 1.0)
    min_stock = int(cfg.get("min_stock", MIN_STOCK_DEFAULT))
    low_stock = deactivated = 0
    records, _ = await load_items()
    if limit:
        records = records[:limit]

    created = updated = skipped = failed = 0
    for rec in records:
        if not rec.get("name"):
            skipped += 1
            continue
        existing = None
        try:
            key = supplier_codes.supplier_key(supplier, rec["model_ref"])
            existing = await db.products.find_one(
                {"supplier_key": key}, {"_id": 0, "id": 1})

            doc = await _build_product_doc(rec, supplier, markup, min_stock)
            doc["supplier_key"] = key

            if existing:
                if only_new:
                    skipped += 1
                    continue
                doc.pop("id", None)
                doc.pop("created_at", None)
                await db.products.update_one({"id": existing["id"]}, {"$set": doc})
                updated += 1
            else:
                await db.products.insert_one(doc)
                created += 1
        except _SkipProduct:
            # İş kuralı gereği atlandı — HATA DEĞİL, ayrı sayılır.
            low_stock += 1
            if existing:
                # Daha önce açılmıştı, artık eşiğin altında: satıştan kaldır.
                # Silmek yerine pasife alıyoruz; geçmiş siparişler ürüne
                # referans veriyor olabilir, silinirse sipariş detayı bozulur.
                await db.products.update_one({"id": existing["id"]}, {"$set": {
                    "is_active": False, "status": "passive",
                    "deactivated_reason": f"tedarikçi stoğu <= {min_stock}",
                    "deactivated_at": _now_iso(),
                }})
                deactivated += 1
        except Exception as e:
            failed += 1
            logger.error(f"[supplier_xml] ürün aktarılamadı ({rec.get('name')!r}): {e}")

    result = {"created": created, "updated": updated, "skipped": skipped,
              "failed": failed, "total": len(records),
              "price_markup": markup, "min_stock": min_stock,
              "low_stock_skipped": low_stock, "deactivated": deactivated,
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


async def sync_stock() -> Dict[str, Any]:
    """Stok ve fiyatı besleme ile eşitler. Ürün açmaz."""
    cfg = await get_config()
    supplier = cfg.get("supplier_name") or "modayakamoz"
    min_stock = int(cfg.get("min_stock", MIN_STOCK_DEFAULT))
    records, _ = await load_items()

    touched = missing = failed = 0
    for rec in records:
        try:
            key = supplier_codes.supplier_key(supplier, rec["model_ref"])
            product = await db.products.find_one(
                {"supplier_key": key}, {"_id": 0, "id": 1, "variants": 1})
            if not product:
                missing += 1
                continue

            # Tedarikçi varyantını BİZİM stok kodumuza eşleyip stok yaz.
            by_code: Dict[str, Dict[str, Any]] = {}
            for v in rec["variants"]:
                codes = await supplier_codes.get_or_create_variant_codes(
                    db, supplier, rec["model_ref"], v.get("ref") or "0")
                by_code[codes["stock_code"]] = v

            new_variants, total = [], 0
            for v in (product.get("variants") or []):
                src = by_code.get(v.get("stock_code"))
                if src is not None:
                    _st = int(src.get("stock") or 0)
                    # Eşiğin altına düşen beden SATILAMAZ hale gelir. Stoğu
                    # olduğu gibi yazsak "son 3 ürün" diye satılır ve sipariş
                    # gelince tedarikçide bulunamaz — iptal ve puan kaybı.
                    v["stock"] = _st if _st > min_stock else 0
                    if src.get("price"):
                        v["price"] = float(src["price"])
                else:
                    # Beslemeden düşen varyant → stoğu sıfırla, silme
                    # (sipariş geçmişi ve pazaryeri eşlemesi bozulmasın).
                    v["stock"] = 0
                total += int(v.get("stock") or 0)
                new_variants.append(v)

            await db.products.update_one(
                {"id": product["id"]},
                {"$set": {"variants": new_variants, "stock": total,
                          "price": float(rec.get("price") or 0) or None,
                          "stock_synced_at": _now_iso()}})
            touched += 1
        except Exception as e:
            failed += 1
            logger.error(f"[supplier_xml] stok eşitlenemedi ({rec.get('name')!r}): {e}")

    result = {"updated": touched, "not_in_catalog": missing, "failed": failed,
              "total": len(records), "at": _now_iso()}
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
        "price_markup": float(cfg.get("price_markup") or 1.0),
        "min_stock": int(cfg.get("min_stock", MIN_STOCK_DEFAULT)),
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
    p = payload or {}
    return await import_products(only_new=bool(p.get("only_new", True)),
                                 limit=int(p.get("limit") or 0))


@router.post("/sync-stock")
async def run_stock_sync(_admin=Depends(require_admin)):
    return await sync_stock()
