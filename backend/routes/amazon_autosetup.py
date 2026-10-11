"""
Amazon otomatik kurulum + toplu aktarım (kullanıcı: "Amazon'da tüm kategorileri ve özellik
değerlerini ayarla, aktarılmamış tüm ürünleri aktar").

1) KATEGORİ → productType: eşlemesi olmayan (aktif ürünlü) kategoriler için ürün tipi, kategori
   adından (yoksa kategorideki ürün adlarının çoğunluğundan) tahmin edilir ve Amazon Definitions
   API'de GERÇEKTEN var olduğu (şeması çekilebildiği) doğrulanır. Mevcut (kullanıcı) eşlemeleri
   EZİLMEZ.
2) ÖZELLİK VARSAYILANLARI: YALNIZ bu işle yeni eşlenen kategoriye, mevcut "varsayılan değer"
   (default_mappings) alanı üzerinden, ürün tipinin şemasında ZORUNLU olan ve kesin bilinen alanlar
   (menşei TR, tehlikeli madde yok, yetişkin/kadın…) şemanın kabul ettiği seçenekle yazılır.
   Mevcut aktarım mantığı DEĞİŞMEZ (kullanıcı: "en son doğru aktarmıştı").
3) TOPLU AKTARIM: Amazon kataloğu çekilir, Amazon'da olmayan / eksik bedenli ve sitede stoğu olan
   ürün aileleri arka planda sırayla mevcut aktarım (sync_products_to_amazon) ile gönderilir;
   ilerleme ve Amazon'un hata gerekçeleri panelde görünür.
"""
import asyncio
import logging
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, Body, Depends, HTTPException

from .deps import db, require_admin, require_permission

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/amazon/spapi/auto", tags=["amazon-auto"])

_PUSH_ID = "amazon_bulk_push"
_TASKS: set = set()
_ACTIVE = {"on": False}  # bu süreçte aktarım gerçekten çalışıyor mu (yeniden başlatmadan kalan "running" bayattır)


def _now():
    return datetime.now(timezone.utc).isoformat()


def _norm(s) -> str:
    from .integrations_common import _hb_norm
    return re.sub(r"\s+", " ", _hb_norm(s)).strip()


# ───────────────────────────── 1) productType tahmini ─────────────────────────────
# Türkçe baş-isim → aday Amazon productType'ları (sırayla; ilk DOĞRULANAN seçilir).
# Türkçe ürün adında baş isim SONDA olur ("Volanlı Etekli Elbise" → elbise) → en sağdaki eşleşme.
_PT_HINTS = [
    ("elbise", ["DRESS"]), ("etek", ["SKIRT"]), ("pantolon", ["PANTS"]), ("jean", ["PANTS"]),
    ("kot", ["PANTS"]), ("tayt", ["LEGGINGS", "PANTS"]), ("sort", ["SHORTS"]), ("bermuda", ["SHORTS"]),
    ("gomlek", ["SHIRT"]), ("bluz", ["SHIRT", "BLOUSE"]), ("tisort", ["SHIRT"]), ("t-shirt", ["SHIRT"]),
    ("tshirt", ["SHIRT"]), ("body", ["SHIRT", "BODYSUIT"]), ("bodysuit", ["SHIRT", "BODYSUIT"]),
    ("crop", ["SHIRT"]), ("atlet", ["SHIRT"]), ("top", ["SHIRT"]), ("bustiyer", ["SHIRT"]),
    ("kazak", ["SWEATER"]), ("hirka", ["SWEATER"]), ("triko", ["SWEATER"]), ("suveter", ["SWEATER"]),
    ("sweatshirt", ["SWEATSHIRT"]), ("sweat", ["SWEATSHIRT"]), ("hoodie", ["SWEATSHIRT"]),
    ("blazer", ["BLAZER", "SUIT_JACKET", "COAT"]), ("ceket", ["COAT", "BLAZER"]),
    ("mont", ["COAT"]), ("kaban", ["COAT"]), ("palto", ["COAT"]), ("trenckot", ["COAT"]),
    ("trench", ["COAT"]), ("kap", ["COAT"]), ("pelerin", ["COAT"]), ("kimono", ["ROBE", "COAT"]),
    ("yelek", ["VEST"]), ("tulum", ["ONE_PIECE_OUTFIT", "OVERALLS", "JUMPSUIT"]),
    ("salopet", ["OVERALLS"]), ("takim", ["APPAREL_SET", "SUIT"]),
    ("canta", ["HANDBAG"]), ("kemer", ["BELT"]), ("sal", ["SCARF"]), ("esarp", ["SCARF"]),
    ("fular", ["SCARF"]), ("sapka", ["HAT"]), ("bere", ["HAT"]), ("ayakkabi", ["SHOES"]),
    ("bot", ["BOOT"]), ("cizme", ["BOOT"]), ("sandalet", ["SANDAL"]), ("terlik", ["SANDAL", "SHOES"]),
    ("kolye", ["NECKLACE"]), ("kupe", ["EARRING"]), ("bileklik", ["BRACELET"]), ("yuzuk", ["RING"]),
    ("mayo", ["SWIMWEAR"]), ("bikini", ["SWIMWEAR"]), ("pijama", ["SLEEPWEAR"]),
    ("sutyen", ["BRA"]), ("corap", ["SOCKS"]),
]


def _hint_candidates(text: str) -> list:
    """Metindeki (ad) en SAĞDAKİ baş-isme göre aday productType listesi."""
    toks = _norm(text).replace("/", " ").split()
    best_pos, best = -1, []
    for i, t in enumerate(toks):
        for kw, pts in _PT_HINTS:
            hit = (t == kw) if len(kw) < 4 else t.startswith(kw)
            if hit and i >= best_pos:
                best_pos, best = i, pts
    return best


# Amazon productType kodlarının Türkçe karşılıkları (panelde kodun yanında gösterilir; Amazon'a
# yine KOD gider — kod tüm Amazon ülke sitelerinde aynıdır).
PT_TR = {
    "DRESS": "Elbise", "SKIRT": "Etek", "PANTS": "Pantolon", "SHIRT": "Gömlek / Bluz / Tişört",
    "BLOUSE": "Bluz", "COAT": "Ceket / Mont / Kaban", "BLAZER": "Blazer Ceket", "SUIT_JACKET": "Takım Ceketi",
    "SUIT": "Takım", "APPAREL_SET": "Giyim Takımı", "SWEATER": "Kazak / Hırka / Triko",
    "SWEATSHIRT": "Sweatshirt", "VEST": "Yelek", "SHORTS": "Şort", "ONE_PIECE_OUTFIT": "Tulum",
    "JUMPSUIT": "Tulum", "OVERALLS": "Salopet", "LEGGINGS": "Tayt", "BODYSUIT": "Body",
    "HANDBAG": "Çanta", "BELT": "Kemer", "SCARF": "Şal / Eşarp", "HAT": "Şapka / Bere",
    "SHOES": "Ayakkabı", "BOOT": "Bot / Çizme", "SANDAL": "Sandalet / Terlik", "NECKLACE": "Kolye",
    "EARRING": "Küpe", "BRACELET": "Bileklik", "RING": "Yüzük", "SWIMWEAR": "Mayo / Bikini",
    "SLEEPWEAR": "Pijama", "BRA": "Sütyen", "SOCKS": "Çorap", "ROBE": "Kimono / Sabahlık",
    "UNDERPANTS": "İç Çamaşırı (Alt)", "TOP": "Üst Giyim",
}


def pt_label(code: str, display: str = "") -> str:
    """Panel etiketi: 'Mont / Kaban (COAT)'. Bilinmeyen kodda Amazon'un adı kullanılır."""
    code = str(code or "").strip()
    tr = PT_TR.get(code.upper())
    if tr:
        return f"{tr} ({code})"
    return f"{display} ({code})" if display and display != code else code


# Panel aramasında Türkçe kelime → Amazon (İngilizce) arama kelimeleri. Amazon productType
# araması Türkçe kelimeyle ("ceket") sonuç döndürmüyor.
_TR_EN = {
    "elbise": ["dress"], "etek": ["skirt"], "pantolon": ["pants", "trousers"], "jean": ["jeans"],
    "kot": ["jeans"], "tayt": ["leggings"], "sort": ["shorts"], "gomlek": ["shirt"], "bluz": ["blouse", "shirt"],
    "tisort": ["t-shirt", "shirt"], "body": ["bodysuit"], "crop": ["top"], "atlet": ["tank top"],
    "kazak": ["sweater"], "hirka": ["cardigan", "sweater"], "triko": ["sweater"], "sweat": ["sweatshirt"],
    "ceket": ["jacket", "coat", "blazer"], "blazer": ["blazer"], "mont": ["coat", "jacket"],
    "kaban": ["coat"], "palto": ["coat"], "trenckot": ["trench coat", "coat"], "yelek": ["vest"],
    "tulum": ["jumpsuit", "overalls"], "salopet": ["overalls"], "takim": ["suit", "set"],
    "canta": ["handbag", "bag"], "kemer": ["belt"], "sal": ["scarf"], "esarp": ["scarf"], "sapka": ["hat"],
    "ayakkabi": ["shoes"], "bot": ["boot"], "cizme": ["boot"], "kolye": ["necklace"], "kupe": ["earring"],
    "mayo": ["swimwear"], "bikini": ["swimwear"], "pijama": ["sleepwear"], "kimono": ["robe", "kimono"],
    "pelerin": ["cape", "coat"], "kap": ["cape", "coat"],
}


async def search_product_types_tr(q: str) -> list:
    """Panel ürün tipi araması: Türkçe kelimeyi İngilizce Amazon kelimelerine çevirip arar;
    adın baş-ismine göre önerilen tipleri (doğrulanmış) en üste koyar."""
    from .amazon_spapi import _amazon_search_product_types
    q = (q or "").strip()
    terms = [q] if q else [""]
    for t in _norm(q).split():
        for kw, en in _TR_EN.items():
            if t == kw or (len(kw) >= 4 and t.startswith(kw)):
                terms += en
    out, seen = [], set()
    for pt in _hint_candidates(q) if q else []:
        if pt not in seen and await _pt_valid(pt):
            seen.add(pt)
            out.append({"name": pt, "displayName": f"{pt_label(pt)} — önerilen"})
    for t in dict.fromkeys(terms):
        try:
            for pt in await _amazon_search_product_types(t):
                if pt.get("name") and pt["name"] not in seen:
                    seen.add(pt["name"])
                    out.append({"name": pt["name"], "displayName": pt_label(pt["name"], pt.get("displayName"))})
        except Exception:
            continue
    return out


_PT_VALID: dict = {}


async def _pt_schema(pt: str) -> dict:
    """Mevcut modülün (önbellekli) şeması: {required, values{attr:[{value,label}]}}."""
    from .amazon_spapi import _amazon_product_type_schema
    try:
        return await _amazon_product_type_schema(pt) or {}
    except Exception:
        return {}


async def _pt_valid(pt: str) -> bool:
    """productType Amazon'da gerçekten var mı (LISTING şemasında zorunlu alan listesi dönüyor mu)."""
    if pt not in _PT_VALID:
        ok = bool((await _pt_schema(pt)).get("required"))
        if not ok:
            # Mevcut önbellek başarısız (geçici hata) yanıtı da boş liste olarak saklar → bir kez
            # önbelleği atlayıp tekrar sor; gerçekten yoksa yine boş döner.
            await db.amazon_pt_schema.delete_one({"product_type": pt, "required": []})
            ok = bool((await _pt_schema(pt)).get("required"))
        _PT_VALID[pt] = ok
    return _PT_VALID[pt]


async def guess_product_type(text: str) -> str:
    for pt in _hint_candidates(text):
        if await _pt_valid(pt):
            return pt
    return ""


# ───────────────────────────── 2) kategori varsayılanları ─────────────────────────────
# Yeni eşlenen kategoriye varsayılan olarak yazılabilecek, ürün tipinden bağımsız GERÇEKLER.
# Yalnız şemada ZORUNLU ve değer şemanın izin verdiği bir seçenekse yazılır. Aktarım varsayılanları
# metin olarak gönderdiğinden yalnız metin değerli alanlar (bool/sayı yok).
_FACTS = {
    "country_of_origin": ["TR", "Türkiye", "Turkey"],
    "supplier_declared_dg_hz_regulation": ["not_applicable"],
    "age_range_description": ["Yetişkin", "Adult"],
}
# Aktarımın zaten kendisi gönderdiği alanlar — yalnız şemanın seçeneklerinde o değer YOKSA
# şemadaki karşılığı varsayılan yapılır (ör. department "Kadın" yerine şemadaki "womens").
_BUILTIN = {"department": ("Kadın", ["Kadın", "Kadin", "womens", "women", "female"]),
            "target_gender": ("female", ["female", "Kadın", "women"])}


def _match_enum(pairs, raw):
    nr = _norm(raw)
    for v in pairs:
        if _norm(v.get("label")) == nr or _norm(v.get("value")) == nr:
            return v.get("value")
    return None


_STYLE_NEUTRAL = ["Günlük", "Gunluk", "Casual", "Klasik", "Classic", "Basic", "Modern", "Diğer", "Other"]


async def apparel_defaults(pt: str, category_name: str) -> tuple:
    """Amazon'un koşullu-zorunlu saydığı (PUT 90220) giyim alanları için kategori varsayılanları:
    Su Geçirmezlik Düzeyi, Yaş Aralığı, Ürün Türü Adı, Tarz. Değer şema seçeneğiyse onunla eşlenir;
    seçenekli alanda uygun seçenek yoksa YAZILMAZ (uydurma yok). Döner (defaults, çözülemeyenler)."""
    if str(pt).upper() == "SHIRT":
        return {}, []  # SHIRT için aktarım bu alanları zaten doğrulanmış değerlerle dolduruyor — dokunma
    sch = await _pt_schema(pt)
    vals = sch.get("values") or {}
    known = set(sch.get("required") or []) | set(sch.get("optional") or [])
    tr = (PT_TR.get(str(pt).upper()) or "").split(" / ")[0]
    wants = {
        "water_resistance_level": ["not_water_resistant", "Suya Dayanıklı Değil", "Su Geçirmez Değil"],
        "age_range_description": ["Yetişkin", "Adult"],
        "item_type_name": [category_name, tr],
        "style": _STYLE_NEUTRAL,
        # Amazon PUT 90220 ile istenen diğer düz giyim alanları — yalnız nötr şema seçeneği
        "special_size_type": ["Standart", "Standard", "Normal", "Regular"],
        "fit_type": ["Standart", "Standart Kesim", "Regular", "Regular Fit", "Normal", "Rahat Kesim"],
        "weave_type": ["Düz", "Sade", "Plain", "Dokuma"],
        "batteries_required": ["false"],
    }
    out, miss = {}, []
    for attr, cands in wants.items():
        if (known and attr not in known) or (attr == "batteries_required" and attr not in known):
            continue
        cands = [c for c in cands if c]
        pairs = vals.get(attr) or []
        if not pairs or attr == "batteries_required":
            if cands:
                out[attr] = cands[0]
            continue
        got = None
        for c in cands:
            got = _match_enum(pairs, c)
            if got is not None:
                break
        if got is None and attr == "item_type_name":
            # kategori adını içeren seçenek (ör. "Kadın Ceket")
            nc = _norm(category_name)
            for v in pairs:
                if nc and nc in _norm(v.get("label")):
                    got = v.get("value")
                    break
        if got is not None:
            out[attr] = got
        else:
            miss.append(attr)
    return out, miss


async def fill_all_category_defaults(by: str) -> list:
    """TÜM amazon-tr kategori eşlemelerine yalnız EKSİK (hiç ayarlanmamış) varsayılanları ekler."""
    rows = []
    async for m in db.category_mappings.find({"marketplace": "amazon-tr",
                                              "marketplace_category_id": {"$nin": [None, ""]}}, {"_id": 0}):
        pt = str(m.get("marketplace_category_id") or "").strip()
        cname = m.get("category_name") or ""
        if not cname:
            c = await db.categories.find_one({"id": m.get("category_id")}, {"_id": 0, "name": 1}) or {}
            cname = c.get("name") or ""
        d1 = await fact_defaults(pt)
        d2, miss = await apparel_defaults(pt, cname)
        cur = dict(m.get("default_mappings") or {})
        add = {k: v for k, v in {**d1, **d2}.items() if k not in cur or cur.get(k) in (None, "")}
        if add:
            await db.category_mappings.update_one(
                {"category_id": m.get("category_id"), "marketplace": "amazon-tr"},
                {"$set": {**{f"default_mappings.{k}": v for k, v in add.items()},
                          "defaults_auto_filled_at": _now(), "defaults_auto_filled_by": by}})
        rows.append({"category": cname, "pt": pt, "added": add, "unresolved": miss})
    return rows


async def fact_defaults(pt: str) -> dict:
    sch = await _pt_schema(pt)
    req, vals = set(sch.get("required") or []), sch.get("values") or {}
    out = {}
    for attr, cands in _FACTS.items():
        if attr not in req:
            continue
        pairs = vals.get(attr) or []
        if not pairs:
            out[attr] = cands[0]
            continue
        for c in cands:
            m = _match_enum(pairs, c)
            if m is not None:
                out[attr] = m
                break
    for attr, (sent, cands) in _BUILTIN.items():
        pairs = vals.get(attr) or []
        if attr not in req or not pairs or _match_enum(pairs, sent) is not None:
            continue
        for c in cands:
            m = _match_enum(pairs, c)
            if m is not None:
                out[attr] = m
                break
    return out


# ───────────────────────────── 1) kategori otomatik eşleme uçları ─────────────────────────────
async def _category_plan() -> list:
    cats = {c["id"]: c for c in await db.categories.find({}, {"_id": 0, "id": 1, "name": 1, "parent_id": 1}).to_list(None)}
    maps = {m.get("category_id"): m for m in await db.category_mappings.find(
        {"marketplace": "amazon-tr"}, {"_id": 0}).to_list(None)}
    by_cat = defaultdict(list)
    async for p in db.products.find({"is_active": True, "is_deleted": {"$ne": True}},
                                    {"_id": 0, "name": 1, "category_id": 1, "category": 1}):
        cid = str(p.get("category_id") or p.get("category") or "")
        if cid:
            by_cat[cid].append(p.get("name") or "")
    rows = []
    for cid, names in sorted(by_cat.items(), key=lambda x: -len(x[1])):
        c = cats.get(cid) or {}
        m = maps.get(cid) or {}
        cur = str(m.get("marketplace_category_id") or "").strip()
        row = {"category_id": cid, "category": c.get("name") or cid, "products": len(names),
               "current": cur, "suggested": "", "source": "", "status": ""}
        if cur:
            row["status"] = "eşli"
            rows.append(row)
            continue
        pt = await guess_product_type(c.get("name") or "")
        if pt:
            row.update(suggested=pt, source="kategori adı")
        else:
            votes = Counter()
            for n in names[:60]:
                g = await guess_product_type(n)
                if g:
                    votes[g] += 1
            if votes:
                pt, cnt = votes.most_common(1)[0]
                row.update(suggested=pt, source=f"ürün adları ({cnt}/{len(names)})")
        row["status"] = "eşlenecek" if row["suggested"] else "tespit edilemedi"
        rows.append(row)
    for r in rows:
        code = r["current"] or r["suggested"]
        r["type_label"] = pt_label(code) if code else ""
    return rows


async def apply_category_map(by: str, apply: bool = True) -> dict:
    """Eşlemesi olmayan kategorilere Amazon productType yazar (apply=False → yalnız plan).
    Kullanıcının mevcut eşlemesine DOKUNULMAZ; yeni eşlenene yalnız eksik varsayılanlar eklenir."""
    rows = await _category_plan()
    applied = 0
    if apply:
        now = _now()
        for r in rows:
            if r["status"] != "eşlenecek":
                continue
            fields = {"category_id": r["category_id"], "category_name": r["category"],
                      "marketplace": "amazon-tr", "marketplace_category_id": r["suggested"],
                      "marketplace_category_name": r["suggested"], "status": "matched",
                      "auto_mapped": True, "updated_at": now, "updated_by": f"otomatik ({by})"}
            key = {"category_id": r["category_id"], "marketplace": "amazon-tr"}
            existing = await db.category_mappings.find_one(key, {"_id": 0, "marketplace_category_id": 1})
            if existing is None:
                await db.category_mappings.insert_one(dict(fields))
                ok = True
            else:
                if str(existing.get("marketplace_category_id") or "").strip():
                    continue  # bu arada kullanıcı eşlemiş → dokunma
                res = await db.category_mappings.update_one(
                    {**key, "$or": [{"marketplace_category_id": {"$in": [None, ""]}},
                                    {"marketplace_category_id": {"$exists": False}}]},
                    {"$set": fields})
                ok = bool(res.modified_count)
            if not ok:
                continue
            applied += 1
            r["status"] = "eşlendi"
            dfl = await fact_defaults(r["suggested"])
            if dfl:
                m = await db.category_mappings.find_one(key, {"_id": 0, "default_mappings": 1}) or {}
                cur = dict(m.get("default_mappings") or {})
                add = {k: v for k, v in dfl.items() if k not in cur}
                if add:
                    await db.category_mappings.update_one(key, {"$set": {f"default_mappings.{k}": v for k, v in add.items()}})
                r["defaults"] = add
    return {"success": True, "applied": applied, "rows": rows,
            "summary": {k: sum(1 for r in rows if r["status"] == k)
                        for k in ("eşli", "eşlenecek", "eşlendi", "tespit edilemedi")}}


@router.post("/map-categories")
async def auto_map_categories(payload: dict = Body(default={}), current_user: dict = Depends(require_permission("integrations.amazon"))):
    return await apply_category_map(current_user.get("email") or "admin", bool((payload or {}).get("apply")))


async def run_full_auto_setup(by: str = "sistem") -> dict:
    """Kategori eşleme (uygula) + aktarılmamışları toplu aktarım — panel butonu olmadan (tek seferlik iş)."""
    res = await apply_category_map(by, True)
    try:
        dres = await fill_all_category_defaults(by)
        from .integrations_common import log_integration_event
        added = [f"{r['category']}: {', '.join(r['added'])}" for r in dres if r["added"]]
        unres = [f"{r['category']}: {', '.join(r['unresolved'])}" for r in dres if r["unresolved"]]
        await log_integration_event(
            "amazon", "category_defaults", "bulk", str(len(added)), "success",
            f"Kategori varsayılanları: {len(added)} kategoriye eklendi ({'; '.join(added)[:700]})"
            + (f" · seçenek bulunamadı: {'; '.join(unres)[:300]}" if unres else ""))
    except Exception as e:
        logger.warning(f"[amazon-auto] varsayılan doldurma: {e}")
    try:
        from .integrations_common import log_integration_event
        mapped = [f"{r['category']}→{r['suggested']}" for r in res["rows"] if r["status"] == "eşlendi"]
        unk = [r["category"] for r in res["rows"] if r["status"] == "tespit edilemedi"]
        await log_integration_event(
            "amazon", "category_auto_map", "bulk", str(res["applied"]), "success",
            f"Kategori eşleme: {res['applied']} yeni ({', '.join(mapped)[:600]}) · zaten eşli {res['summary'].get('eşli', 0)}"
            + (f" · tespit edilemedi: {', '.join(unk)[:300]}" if unk else ""))
    except Exception:
        pass
    # Tek seferlik iş tek süreçte çalışır; önceki süreçten kalan "running" durumu bayattır.
    await db.settings.update_one({"id": _PUSH_ID}, {"$set": {
        "id": _PUSH_ID, "status": "running", "step": "başlıyor", "started_at": _now(), "finished_at": None,
        "total": 0, "done": 0, "pushed": 0, "failed": 0, "reasons": [], "fails": [], "error": "", "by": by}},
        upsert=True)
    await _run_push({"email": by, "is_admin": True, "role": "admin"}, True)  # güncel katalogla
    return res


# ───────────────────────────── 3) toplu aktarım ─────────────────────────────
async def _targets() -> dict:
    """Amazon'da olmayan / eksik bedenli, sitede stoğu olan ürünler → aile (stok kodu) grupları."""
    from .amazon_catalog import _build_report
    from .integrations_common import _resolve_stock_code
    rep = await _build_report()
    ids = [r["product_id"] for r in (rep.get("not_on_amazon") or []) + (rep.get("partial") or [])]
    # Kullanıcı (2026-10-01): "Amazon'da satışa açılmamış ama panelde aktif satışta olan TÜM ürünler" —
    # Amazon'da kaydı olup durumu Aktif OLMAYAN (Inactive/Incomplete…) eşleşen ürünler de gönderilir.
    async for m in db.amazon_sku_map.find({"matched": True, "product_id": {"$nin": [None, ""]}},
                                          {"_id": 0, "product_id": 1, "amazon_status": 1}):
        if str(m.get("amazon_status") or "").strip().lower() not in ("active", "aktif"):
            ids.append(m["product_id"])
    ids = list(dict.fromkeys(ids))
    prods = await db.products.find({"id": {"$in": ids}}, {"_id": 0}).to_list(None)
    fam, zero = defaultdict(list), []
    for p in prods:
        stock = sum(int((v or {}).get("stock") or 0) for v in (p.get("variants") or []))
        if stock <= 0:
            zero.append(p.get("name"))
            continue
        key = _resolve_stock_code(p) or f"id:{p.get('id')}"
        fam[key].append({"id": p.get("id"), "name": p.get("name"),
                         "category_id": str(p.get("category_id") or p.get("category") or "")})
    return {"families": fam, "zero_stock": zero, "catalog": rep.get("catalog_status") or {},
            "summary": rep.get("summary") or {}}


async def _st(**kw):
    kw["updated_at"] = _now()
    await db.settings.update_one({"id": _PUSH_ID}, {"$set": kw}, upsert=True)


async def _run_push(user: dict, refresh_catalog: bool):
    if _ACTIVE["on"]:
        return
    _ACTIVE["on"] = True
    try:
        await _run_push_inner(user, refresh_catalog)
    finally:
        _ACTIVE["on"] = False


async def _run_push_inner(user: dict, refresh_catalog: bool):
    from .amazon_spapi import sync_products_to_amazon
    try:
        if refresh_catalog:
            await _st(step="Amazon kataloğu çekiliyor (1–5 dk)")
            from .amazon_catalog import run_catalog_sync
            await run_catalog_sync()
        await _st(step="aktarılacaklar hesaplanıyor")
        t = await _targets()
        fams = list(t["families"].items())
        await _st(step="gönderiliyor", total=len(fams), done=0, zero_stock=len(t["zero_stock"]))
        pushed = failed = 0
        dry = False
        reasons, fails = Counter(), []
        examples = {}
        for i, (key, members) in enumerate(fams):
            try:
                res = await sync_products_to_amazon(
                    {"product_ids": [m["id"] for m in members], "confirm_large_batch": True}, user)
                dry = dry or bool(res.get("dry_run"))
                pushed += int(res.get("pushed") or 0)
                failed += int(res.get("failed") or 0)
                for r in res.get("results") or []:
                    if r.get("ok") is False or r.get("error"):
                        msgs = [f"{x.get('code') or ''} {x.get('message') or ''}".strip()
                                + (f" [{r.get('product_type')}: {','.join(x.get('attributeNames') or [])}]"
                                   if x.get("attributeNames") else "")
                                for x in (r.get("issues") or []) if (x.get("severity") or "ERROR") == "ERROR"]
                        if not msgs:
                            msgs = [str(r.get("error") or r.get("raw_errors") or "bilinmeyen hata")[:200]]
                        for mm in msgs[:3]:
                            reasons[mm[:220]] += 1
                            examples.setdefault(mm[:220], r.get("sku"))
                        if len(fails) < 400:
                            fails.append({"sku": r.get("sku"), "product": r.get("product"),
                                          "neden": " | ".join(msgs)[:400]})
            except HTTPException as e:
                failed += 1
                reasons[str(e.detail)[:220]] += 1
                fails.append({"sku": key, "product": members[0]["name"], "neden": str(e.detail)[:400]})
            except Exception as e:
                failed += 1
                reasons[str(e)[:220]] += 1
                fails.append({"sku": key, "product": members[0]["name"], "neden": str(e)[:400]})
            if i % 3 == 0 or i == len(fams) - 1:
                await _st(done=i + 1, pushed=pushed, failed=failed, dry_run=dry,
                          reasons=[{"neden": k, "adet": v, "ornek_sku": examples.get(k)} for k, v in reasons.most_common(40)],
                          fails=fails)
            await asyncio.sleep(1.0)
        await _st(status="ok", step="bitti", finished_at=_now(), done=len(fams), pushed=pushed,
                  failed=failed, dry_run=dry,
                  reasons=[{"neden": k, "adet": v, "ornek_sku": examples.get(k)} for k, v in reasons.most_common(40)],
                  fails=fails)
        try:
            from .integrations_common import log_integration_event
            await log_integration_event("amazon", "bulk_listing", "bulk", str(len(fams)),
                                        "success" if not failed else "error",
                                        f"Toplu aktarım: {len(fams)} ürün ailesi, {pushed} SKU gönderildi, {failed} hata"
                                        + (" (DRY-RUN)" if dry else ""))
        except Exception:
            pass
    except Exception as e:
        logger.exception(f"[amazon-bulk] hata: {e}")
        await _st(status="error", step="hata", error=str(e)[:400], finished_at=_now())


@router.post("/push-missing")
async def push_missing(payload: dict = Body(default={}), current_user: dict = Depends(require_permission("integrations.amazon"))):
    """apply=false → ön izleme (son katalog çekimine göre). apply=true → arka planda: katalog
    tazelenir, Amazon'da olmayan/eksik bedenli ve stoğu olan aileler sırayla aktarılır."""
    payload = payload or {}
    st = await db.settings.find_one({"id": _PUSH_ID}, {"_id": 0}) or {}
    if not payload.get("apply"):
        t = await _targets()
        from .amazon_spapi import _resolve_amazon_product_type
        no_pt, rows = [], []
        for key, members in list(t["families"].items()):
            m0 = members[0]
            p0 = await db.products.find_one({"id": m0["id"]}, {"_id": 0}) or {}
            pt = await _resolve_amazon_product_type(p0) or await guess_product_type(p0.get("name") or "")
            rows.append({"aile": key, "urunler": ", ".join(m["name"] for m in members)[:200],
                         "renk": len(members), "product_type": pt or ""})
            if not pt:
                no_pt.append(m0["name"])
        return {"success": True, "preview": True, "families": len(rows), "rows": rows[:500],
                "zero_stock": len(t["zero_stock"]), "no_product_type": no_pt[:200],
                "catalog_finished_at": (t["catalog"] or {}).get("finished_at"),
                "summary": t["summary"], "job": st}
    if _ACTIVE["on"]:
        return {"success": True, "already_running": True, "job": st}
    await db.settings.update_one({"id": _PUSH_ID}, {"$set": {
        "id": _PUSH_ID, "status": "running", "step": "başlıyor", "started_at": _now(), "finished_at": None,
        "total": 0, "done": 0, "pushed": 0, "failed": 0, "reasons": [], "fails": [], "error": "",
        "by": current_user.get("email")}}, upsert=True)
    # Varsayılan: katalog YENİDEN çekilmeden (Amazon rapor kuyruğu dakikalar sürüyor) mevcut
    # eşleşme raporuna göre hemen gönder. refresh_catalog=true ile önce katalog tazelenir.
    try:
        await fill_all_category_defaults(current_user.get("email") or "admin")
    except Exception as e:
        logger.warning(f"[amazon-auto] varsayılan doldurma: {e}")
    task = asyncio.create_task(_run_push(current_user, bool(payload.get("refresh_catalog", False))))
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)
    return {"success": True, "started": True}


@router.get("/push-status")
async def push_status(current_user: dict = Depends(require_permission("integrations.view", any_in_area=True))):
    st = await db.settings.find_one({"id": _PUSH_ID}, {"_id": 0}) or {"status": "idle"}
    if st.get("status") == "running" and not _ACTIVE["on"]:
        st["status"], st["step"] = "error", "yarıda kesildi (sunucu yeniden başladı) — tekrar başlatın"
    return st


@router.get("/progress")
async def push_progress_public(current_user: dict = Depends(require_permission("integrations.view", any_in_area=True))):
    """Toplu aktarım ilerlemesi (sayılar + Amazon hata gerekçeleri).
    GÜVENLİK (denetim #14): eskiden KİMLİKSİZ açıktı ve SKU/stok/fiyat/ASIN/iç kimlik + tanılama
    ayrıntılarını herkese döndürüyordu → artık entegrasyon alanında yetkili personel ister."""
    st = await db.settings.find_one({"id": _PUSH_ID}, {"_id": 0}) or {}
    if st.get("status") == "running" and not _ACTIVE["on"]:
        st["status"], st["step"] = "error", "yarıda kesildi"
    cat = await db.settings.find_one({"id": "amazon_catalog_sync"}, {"_id": 0, "status": 1, "step": 1,
                                                                     "rows": 1, "matched": 1, "unmatched": 1,
                                                                     "finished_at": 1}) or {}
    maps = await db.category_mappings.count_documents({"marketplace": "amazon-tr",
                                                       "marketplace_category_id": {"$nin": [None, ""]}})
    # Eksik denen alanların şema bilgisi (Amazon'un herkese açık ürün tipi tanımı): anahtar + seçenekler.
    schema_info = {}
    try:
        import re as _re
        want = set()
        for r in (st.get("reasons") or [])[:30]:
            m = _re.search(r"\[([A-Z_]+): ([^\]]+)\]", str(r.get("neden") or ""))
            if m:
                for a in m.group(2).split(","):
                    want.add((m.group(1), a.strip()))
        for pt, a in list(want)[:20]:
            sch = await _pt_schema(pt)
            top = a.split(".")[0]
            vals = (sch.get("values") or {}).get(top) or []
            schema_info[f"{pt}:{a}"] = {
                "title": (sch.get("titles") or {}).get(a) or (sch.get("titles") or {}).get(top),
                "required": top in (sch.get("required") or []),
                "values": [f"{v.get('value')}={v.get('label')}" for v in vals[:60]],
                "subkeys": {k: t for k, t in (sch.get("titles") or {}).items() if k.startswith(top + ".")},
                "n_values": len(vals)}
    except Exception as e:
        schema_info["_err"] = str(e)[:200]
    # Güncel katalog özeti (yalnız SAYILAR): Amazon'da tam / kısmen / yok + Amazon durum dağılımı.
    rep_sum, status_counts, not_active_products = {}, {}, 0
    try:
        from .amazon_catalog import _build_report
        rep = await _build_report()
        rep_sum = rep.get("summary") or {}
        pids = set()
        async for m in db.amazon_sku_map.find({"matched": True}, {"_id": 0, "amazon_status": 1, "product_id": 1,
                                                                 "product_active": 1}):
            k = str(m.get("amazon_status") or "?")
            status_counts[k] = status_counts.get(k, 0) + 1
            if m.get("product_active") and k.lower() not in ("active", "aktif"):
                pids.add(m.get("product_id"))
        not_active_products = len(pids)
        # Pasif SKU'ları ayır: bizde stoğu 0 olan beden (Amazon'un normal "stok yok" pasifi) mi,
        # yoksa stoğumuz VAR iken pasif mi (gerçek sorun) — yalnız sayı + Amazon miktarı dağılımı.
        stock_by = {}
        async for p in db.products.find({"is_active": True}, {"_id": 0, "id": 1, "variants.barcode": 1, "variants.stock": 1}):
            for v in p.get("variants") or []:
                stock_by[(p.get("id"), str(v.get("barcode") or ""))] = int(v.get("stock") or 0)
        inact = {"bizde_stok_0": 0, "bizde_stok_var": 0, "bizde_stok_var_amazon_qty_0": 0, "eslesme_yok": 0}
        async for m in db.amazon_sku_map.find({"matched": True, "amazon_status": {"$nin": ["Active", "active"]}},
                                              {"_id": 0, "product_id": 1, "variant_barcode": 1, "amazon_qty": 1}):
            our_qty = stock_by.get((m.get("product_id"), str(m.get("variant_barcode") or "")))
            if our_qty is None:
                inact["eslesme_yok"] += 1
            elif our_qty <= 0:
                inact["bizde_stok_0"] += 1
            else:
                inact["bizde_stok_var"] += 1
                if not m.get("amazon_qty"):
                    inact["bizde_stok_var_amazon_qty_0"] += 1
        status_counts["_inactive_breakdown"] = inact
    except Exception as e:
        rep_sum = {"_err": str(e)[:200]}
    return {
        "report_summary": rep_sum, "amazon_status_counts": status_counts,
        "active_products_not_selling_on_amazon": not_active_products,
        "schema_info": schema_info,
        "status": st.get("status") or "idle", "step": st.get("step"), "started_at": st.get("started_at"),
        "finished_at": st.get("finished_at"), "total": st.get("total"), "done": st.get("done"),
        "pushed": st.get("pushed"), "failed": st.get("failed"), "zero_stock": st.get("zero_stock"),
        "dry_run": st.get("dry_run"), "error": (st.get("error") or "")[:200],
        "reasons": [{"neden": str(r.get("neden") or "")[:180], "adet": r.get("adet")} for r in (st.get("reasons") or [])[:30]],
        "catalog": cat, "amazon_mapped_categories": maps,
        "pasif_teshis": (await db.settings.find_one({"id": "amazon_pasif_teshis"}, {"_id": 0})) or {},
        "repush_teshis": (await db.settings.find_one({"id": "amazon_repush_teshis"}, {"_id": 0})) or {},
    }


async def diagnose_inactive_in_stock() -> list:
    """YALNIZ OKUMA: bizde stoğu olup Amazon'da pasif (Inactive) görünen SKU'lar için Amazon Listings
    kaydını (durum + sorunlar) okur ve settings'e yazar. Amazon'a hiçbir şey göndermez."""
    from .amazon_spapi import _spapi_get, _require_seller_id, get_valid_access_token
    seller = await _require_seller_id()
    _, _, mp = await get_valid_access_token()
    stock_by, names = {}, {}
    async for p in db.products.find({"is_active": True}, {"_id": 0, "id": 1, "name": 1, "variants": 1}):
        names[p.get("id")] = p.get("name")
        for v in p.get("variants") or []:
            stock_by[(p.get("id"), str(v.get("barcode") or ""))] = (int(v.get("stock") or 0), v.get("size"))
    rows = []
    async for m in db.amazon_sku_map.find({"matched": True, "amazon_status": {"$nin": ["Active", "active"]}}, {"_id": 0}):
        our = stock_by.get((m.get("product_id"), str(m.get("variant_barcode") or "")))
        if not our or our[0] <= 0:
            continue
        sku = m.get("sku")
        row = {"sku": sku, "urun": names.get(m.get("product_id")), "beden": our[1], "bizde_stok": our[0],
               "amazon_qty": m.get("amazon_qty"), "amazon_fiyat": m.get("amazon_price"), "asin": m.get("asin")}
        try:
            r = await _spapi_get(f"/listings/2021-08-01/items/{seller}/{sku}",
                                 {"marketplaceIds": mp, "includedData": "summaries,issues,offers,fulfillmentAvailability"})
            d = r.get("data") or {}
            s0 = (d.get("summaries") or [{}])[0]
            row["durum"] = s0.get("status")
            row["product_type"] = s0.get("productType")
            row["sorunlar"] = [f"{i.get('code')} {i.get('severity')}: {str(i.get('message'))[:220]}"
                               for i in (d.get("issues") or [])][:6]
            row["teklif_var"] = bool(d.get("offers"))
            fa = (d.get("fulfillmentAvailability") or [{}])
            row["amazon_qty_canli"] = (fa[0] or {}).get("quantity") if fa else None
            if not r.get("ok"):
                row["hata"] = f"HTTP {r.get('status')}"
        except Exception as e:
            row["hata"] = str(e)[:200]
        rows.append(row)
        await asyncio.sleep(0.3)
    await db.settings.update_one({"id": "amazon_pasif_teshis"},
                                 {"$set": {"id": "amazon_pasif_teshis", "at": _now(), "rows": rows}}, upsert=True)
    return rows


async def repush_and_recheck(stock_code: str, wait_s: int = 600) -> dict:
    """Tek ürün ailesini (stok kodu) mevcut aktarımla yeniden gönderir, Amazon'un tam PUT yanıtını
    ve wait_s sonra Listings durumunu (ebeveyn dahil) kaydeder. Kullanıcı: eksik kalanları aktar."""
    from .amazon_spapi import sync_products_to_amazon, _spapi_get, _require_seller_id, get_valid_access_token
    from .integrations_common import _resolve_stock_code
    out = {"stock_code": stock_code, "at": _now()}
    pids = [p["id"] async for p in db.products.find({"is_active": True}, {"_id": 0, "id": 1, "stock_code": 1,
                                                                           "variants": 1, "sku": 1})
            if _resolve_stock_code(p) == stock_code]
    out["product_ids"] = pids
    res = await sync_products_to_amazon({"product_ids": pids, "confirm_large_batch": True}, {"email": "sistem"})
    out["push"] = [{"sku": r.get("sku"), "ok": r.get("ok"), "status": r.get("status"), "http": r.get("http"),
                    "issues": [f"{i.get('code')} {i.get('severity')}: {str(i.get('message'))[:200]}"
                               for i in (r.get("issues") or [])][:5],
                    "err": str(r.get("error") or r.get("raw_errors") or "")[:200]} for r in res.get("results") or []]
    await db.settings.update_one({"id": "amazon_repush_teshis"}, {"$set": {"id": "amazon_repush_teshis", **out}}, upsert=True)
    await asyncio.sleep(wait_s)
    seller = await _require_seller_id()
    _, _, mp = await get_valid_access_token()
    after = []
    for r in out["push"]:
        try:
            g = await _spapi_get(f"/listings/2021-08-01/items/{seller}/{r['sku']}",
                                 {"marketplaceIds": mp, "includedData": "summaries,issues,relationships"})
            d = g.get("data") or {}
            s0 = (d.get("summaries") or [{}])[0]
            after.append({"sku": r["sku"], "http": g.get("status"), "durum": s0.get("status"), "asin": s0.get("asin"),
                          "issues": [f"{i.get('code')} {i.get('severity')}: {str(i.get('message'))[:200]}"
                                     for i in (d.get("issues") or [])][:5],
                          "rel": str(d.get("relationships") or "")[:300]})
        except Exception as e:
            after.append({"sku": r["sku"], "err": str(e)[:200]})
        await asyncio.sleep(0.3)
    await db.settings.update_one({"id": "amazon_repush_teshis"}, {"$set": {"after": after, "after_at": _now()}})
    return out
