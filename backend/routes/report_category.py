"""Raporlar için ÜRÜN TİPİ KATEGORİSİ — tek kural (satış, stok değer, hız, kategori raporları).

Ürünün birincil kategorisi (category_name) boş ya da bir kampanya kategorisi (EN YENİLER,
KOLEKSİYONLAR, İNDİRİM…) olabiliyor; oysa panelde ALT KATEGORİ (Body, Pantolon…) seçili.
Kural (benzer-ürünler özelliğiyle aynı):
  1) Panelde seçilen kategoriler (categories; yoksa category_ids / category_id) içinden
     promo/sanal kökler ve alt ağaçları atılır, kalanlardan EN SPESİFİK (en derin) seçilir.
  2) Hiç gerçek kategori yoksa ürün ADINDAN tip çıkarılır (Elbise, Şort…).
  3) O da yoksa promo olmayan birincil kategori adı; hiçbiri yoksa "(Kategorisiz)".
"""
import time

from .deps import db

_CACHE = {"t": 0.0, "fn": None}


async def load_category_resolver():
    """Rapor başına bir kez çağrılır (60 sn önbellek); dönen fonksiyon ürün → kategori adı."""
    if _CACHE["fn"] and time.time() - _CACHE["t"] < 60:
        return _CACHE["fn"]
    from .products import _PROMO_ROOT_NAMES, _TYPE_KEYWORDS, _cnorm
    cats = await db.categories.find({}, {"_id": 0, "id": 1, "name": 1, "parent_id": 1}).to_list(5000)
    by_id = {str(c.get("id")): c for c in cats if c.get("id") is not None}
    depth, promo = {}, {}
    for cid in by_id:
        cur, d, root, g = by_id[cid], 0, by_id[cid], 0
        while cur and cur.get("parent_id") and g < 25:
            nxt = by_id.get(str(cur.get("parent_id")))
            if not nxt:
                break
            cur, d, g = nxt, d + 1, g + 1
            root = cur
        depth[cid] = d
        promo[cid] = (_cnorm(by_id[cid].get("name")) in _PROMO_ROOT_NAMES
                      or _cnorm((root or {}).get("name")) in _PROMO_ROOT_NAMES)
    name_to_cat = {}
    for c in cats:
        name_to_cat.setdefault(_cnorm(c.get("name")), c)
    has_child = {str(c.get("parent_id")) for c in cats if c.get("parent_id") not in (None, "")}

    def _from_name(p):
        pn = _cnorm(p.get("name"))
        for kw, canon in _TYPE_KEYWORDS:
            if kw in pn:
                c = name_to_cat.get(_cnorm(canon))
                return ((c or {}).get("name") or canon).strip()
        return None

    def _pick(ids):
        """(ad, üst_grup_mu) — en spesifik gerçek kategori; alt kategorisi olan bir GRUP
        (ör. 'Üst Giyim') seçildiyse ikinci değer True döner."""
        real = [i for i in ids if i in by_id and not promo.get(i)]
        if not real:
            return None, False
        best = max(real, key=lambda i: depth.get(i, 0))   # eşitlikte seçim sırasındaki ilk
        return ((by_id[best].get("name") or "").strip() or None), (best in has_child)

    def resolve(p: dict) -> str:
        p = p or {}
        sel = [str(x) for x in (p.get("categories") or []) if x not in (None, "")]
        exp = [str(x) for x in (p.get("category_ids") or []) if x not in (None, "")]
        one = [str(p["category_id"])] if p.get("category_id") not in (None, "") else []
        group_name = None
        for ids in (sel, exp, one):
            nm, is_group = _pick(ids)
            if nm and not is_group:
                return nm
            if nm and not group_name:
                group_name = nm          # yalnız üst grup seçili → önce addan tip dene
        nt = _from_name(p)
        if nt:
            return nt
        if group_name:
            return group_name
        cn = (p.get("category_name") or "").strip()
        if cn and _cnorm(cn) not in _PROMO_ROOT_NAMES:
            return cn
        return "(Kategorisiz)"

    _CACHE.update(t=time.time(), fn=resolve)
    return resolve


# Rapor sorgularının projeksiyonuna eklenecek alanlar
CATEGORY_FIELDS = {"category_name": 1, "categories": 1, "category_ids": 1, "category_id": 1, "name": 1}
