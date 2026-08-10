"""
Location endpoints:
  GET /api/locations/countries               - all countries (pycountry)
  GET /api/locations/tr/provinces            - Turkey provinces (81)
  GET /api/locations/tr/districts?province=  - districts of a province
  GET /api/locations/search?q=&type=         - search by name for city/district
"""
from fastapi import APIRouter, Query
from pathlib import Path
import json
import pycountry

router = APIRouter(prefix="/locations", tags=["locations"])

_DATA_DIR = Path(__file__).parent.parent / "data"
_TR_LOCATIONS_FILE = _DATA_DIR / "tr_locations.json"
_tr_cache = None


def _load_tr():
    global _tr_cache
    if _tr_cache is not None:
        return _tr_cache
    try:
        with open(_TR_LOCATIONS_FILE, encoding="utf-8") as f:
            _tr_cache = json.load(f)
    except Exception:
        _tr_cache = []
    return _tr_cache


@router.get("/countries")
async def list_countries():
    """Return all ISO 3166 countries (code, name, native name where available)."""
    out = []
    for c in pycountry.countries:
        name_tr = c.name
        # Official name if available, common name otherwise
        out.append({
            "code": c.alpha_2,
            "code3": c.alpha_3,
            "name": c.name,
            "common_name": getattr(c, "common_name", None) or c.name,
        })
    # Turkey first, then alphabetical
    out.sort(key=lambda x: (0 if x["code"] == "TR" else 1, x["name"]))
    return {"countries": out, "total": len(out)}


@router.get("/tr/provinces")
async def list_tr_provinces():
    data = _load_tr()
    return {"provinces": [{"id": p["id"], "name": p["name"], "district_count": len(p.get("districts", []))} for p in data], "total": len(data)}


@router.get("/tr/districts")
async def list_tr_districts(province: str = Query(..., description="Province name")):
    data = _load_tr()
    found = next((p for p in data if p["name"].lower() == province.lower()), None)
    if not found:
        return {"province": province, "districts": [], "total": 0}
    return {"province": found["name"], "districts": found["districts"], "total": len(found["districts"])}


@router.get("/tr/search")
async def search_tr(q: str = Query(..., min_length=1)):
    """Search province or district by prefix (case-insensitive, Turkish-safe)."""
    data = _load_tr()
    ql = q.lower()
    provinces = [{"name": p["name"], "id": p["id"]} for p in data if ql in p["name"].lower()][:20]
    districts = []
    for p in data:
        for d in p.get("districts", []):
            if ql in d.lower():
                districts.append({"name": d, "province": p["name"]})
                if len(districts) >= 30:
                    break
        if len(districts) >= 30:
            break
    return {"provinces": provinces, "districts": districts}
