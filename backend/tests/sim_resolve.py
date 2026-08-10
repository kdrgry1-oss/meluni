"""Gerçek veriyle Trendyol attribute resolve simülasyonu — Boy/Cep/Paket teşhisi."""
import asyncio, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from motor.motor_asyncio import AsyncIOMotorClient
from routes.integrations import (
    _normalize_attr_key, _norm_val, _resolve_value_id, _bridge_trendyol_attr_synonyms,
)


async def build_meta(db, mp_cat_id):
    cache = await db.trendyol_category_attributes.find_one(
        {"category_id": int(mp_cat_id)}, {"_id": 0})
    meta = {}
    for a in (cache or {}).get("attributes", []) or []:
        aid = a.get("id") or a.get("attribute", {}).get("id")
        if aid is None:
            continue
        valid = {str(v.get("id")) for v in (a.get("attributeValues") or []) if v.get("id") is not None}
        vname = {}
        for v in (a.get("attributeValues") or []):
            if v.get("id") is None or not v.get("name"):
                continue
            vname[_norm_val(v["name"])] = str(v["id"])
        meta[int(aid)] = {
            "allow_custom": bool(a.get("allowCustom") or a.get("attribute", {}).get("allowCustom")),
            "required": bool(a.get("required")),
            "valid_value_ids": valid,
            "value_name_to_id": vname,
            "name": a.get("name") or a.get("attribute", {}).get("name") or "",
        }
    return meta


def collect_local_values(product, variant):
    out = {}
    def _put(nm, vv):
        if not nm or vv in (None, ""):
            return
        out.setdefault(str(nm).lower().strip(), str(vv))
    def _walk(attrs):
        if isinstance(attrs, dict):
            items = sorted(attrs.items(), key=lambda kv: 1 if str(kv[0]).lower().startswith("ticimax_") else 0)
            for k, v in items:
                if isinstance(v, dict):
                    _put(v.get("label") or v.get("name") or k, v.get("value") or v.get("attribute_value"))
                elif v is not None:
                    _put(k, v)
        elif isinstance(attrs, list):
            for a in attrs:
                if isinstance(a, dict):
                    _put(a.get("label") or a.get("name") or a.get("type") or a.get("attribute_name"),
                         a.get("value") or a.get("attribute_value"))
    _walk(product.get("attributes"))
    if variant:
        _walk(variant.get("attributes"))
        if variant.get("color"):
            _put("Renk", variant["color"]); _put("Web Color", variant["color"])
        if variant.get("size"):
            _put("Beden", variant["size"])
    _bridge_trendyol_attr_synonyms(out)
    return out


def resolve(meta, product, variant, category):
    item_attrs = []
    processed = set()
    attr_mappings = category.get("attribute_mappings", []) or []
    val_mappings = category.get("value_mappings", {}) or {}
    default_mappings = category.get("default_mappings", {}) or {}
    local_vals = collect_local_values(product, variant)
    log = []

    def _push(ty_id, value_id=None, custom=None):
        am = meta.get(ty_id) or {}
        amn = (am.get("name") or "").lower()
        if any(p in amn for p in ["analiz testi", "test raporu", "sertifika dosya", "dosya linki"]):
            return False
        if value_id is not None:
            vid = str(value_id)
            if am.get("valid_value_ids") and vid not in am["valid_value_ids"]:
                if am.get("allow_custom") and custom:
                    item_attrs.append((ty_id, am.get("name"), "custom", custom)); processed.add(ty_id); return True
                log.append(f"DROP {ty_id} {am.get('name')}: value_id {vid} not in cache, no custom")
                return False
            item_attrs.append((ty_id, am.get("name"), "valueId", vid)); processed.add(ty_id); return True
        if custom is not None:
            if not am.get("allow_custom"):
                log.append(f"DROP {ty_id} {am.get('name')}: custom '{custom}' but not allowCustom")
                return False
            item_attrs.append((ty_id, am.get("name"), "custom", custom)); processed.add(ty_id); return True
        return False

    for mapping in attr_mappings:
        ty_id = mapping.get("mp_attr_id") or mapping.get("trendyol_attr_id")
        if not ty_id: continue
        try: ty_id = int(ty_id)
        except: continue
        if ty_id in processed: continue
        lname = str(mapping.get("local_attr") or "").strip(); lkey = lname.lower(); lval = None
        if variant:
            if lkey in ["renk", "color", "web color"]: lval = variant.get("color")
            elif lkey in ["beden", "size"]: lval = variant.get("size") or local_vals.get(lkey)
        if not lval: lval = local_vals.get(lkey)
        if not lval and lname: lval = local_vals.get(lname.lower())
        if lval:
            sid = str(ty_id)
            mv = val_mappings.get(f"{sid}|{lval}")
            if not mv and sid in val_mappings and isinstance(val_mappings[sid], dict):
                mv = val_mappings[sid].get(str(lval))
            if mv:
                if str(mv).isdigit():
                    if _push(ty_id, value_id=mv, custom=lval): continue
                else:
                    if _push(ty_id, custom=mv): continue
            nm = (meta.get(ty_id) or {}).get("value_name_to_id") or {}
            avid = _resolve_value_id(nm, lval)
            if avid and _push(ty_id, value_id=avid, custom=lval): continue
            if _push(ty_id, custom=lval): continue
        sid = str(ty_id)
        if sid in default_mappings and default_mappings[sid]:
            dv = default_mappings[sid]
            if str(dv).isdigit(): _push(ty_id, value_id=dv, custom=lval)
            else: _push(ty_id, custom=dv)
    for ts, dv in default_mappings.items():
        if not dv: continue
        try: ty_id = int(ts)
        except: continue
        if ty_id not in processed:
            if str(dv).isdigit(): _push(ty_id, value_id=dv)
            else: _push(ty_id, custom=dv)
    return item_attrs, log, local_vals


async def main():
    c = AsyncIOMotorClient(os.environ["MONGO_URL"]); db = c[os.environ["DB_NAME"]]
    for cat_id, label in [("6846", "Ceket(Boy/Cep)"), ("4294", "Takım(Paket)")]:
        cm = await db.category_mappings.find_one({"category_id": cat_id, "marketplace": "trendyol"}, {"_id": 0})
        mp = cm["marketplace_category_id"]
        meta = await build_meta(db, mp)
        p = await db.products.find_one({"category_id": cat_id}, {"_id": 0})
        vs = p.get("variants") or [None]
        attrs, log, lv = resolve(meta, p, vs[0], cm)
        print(f"\n===== {label} cat={cat_id} mp={mp} product={p.get('name')} =====")
        print("Boy in local_vals:", lv.get("boy"), "| Cep:", lv.get("cep"))
        for a in attrs:
            if a[1] in ("Boy", "Cep", "Paket İçeriği", "Yaş Grubu"):
                print("  SENT:", a)
        print("  DROPS:", log)

asyncio.run(main())
