"""
Beden Önerisi (Iter 43) — Müşteri vücut ölçüleri + marka beden tablosu eşleştirme.

User profile fields (users collection): height_cm, weight_kg, chest_cm, waist_cm, hip_cm
Brand size charts already exist in size_charts collection (Ölçü Tabloları).

Endpoint:
  GET  /api/me/measurements                       — kendi ölçülerimi getir
  POST /api/me/measurements                       — kendi ölçülerimi güncelle
  GET  /api/products/{id}/size-recommendation     — ürün için beden öner (login gerekli)
"""
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .deps import db, get_current_user

router = APIRouter(tags=["size-recommendation"])


class Measurements(BaseModel):
    height_cm: Optional[float] = Field(None, ge=80, le=230)
    weight_kg: Optional[float] = Field(None, ge=20, le=250)
    chest_cm: Optional[float] = Field(None, ge=50, le=180)
    waist_cm: Optional[float] = Field(None, ge=40, le=180)
    hip_cm: Optional[float] = Field(None, ge=50, le=200)


@router.get("/me/measurements")
async def get_my_measurements(user=Depends(get_current_user)):
    if not user:
        raise HTTPException(status_code=401, detail="Giriş gerekli")
    fields = {"height_cm": 1, "weight_kg": 1, "chest_cm": 1, "waist_cm": 1, "hip_cm": 1, "_id": 0}
    u = await db.users.find_one({"id": user["id"]}, fields)
    return u or {}


@router.post("/me/measurements")
async def update_my_measurements(m: Measurements, user=Depends(get_current_user)):
    if not user:
        raise HTTPException(status_code=401, detail="Giriş gerekli")
    upd = {k: v for k, v in m.dict().items() if v is not None}
    await db.users.update_one({"id": user["id"]}, {"$set": upd})
    return {"ok": True, "measurements": upd}


@router.get("/products/{product_id}/size-recommendation")
async def recommend_size(product_id: str, user=Depends(get_current_user)):
    """Müşteri vücut ölçülerine göre ürün için en uygun bedeni önerir.

    Algoritma: Ürünün marka beden tablosu varsa her beden için ölçü farkı
    (chest+waist+hip - kullanıcı ölçüleri)^2 minimize edilir. Ölçü yoksa
    height+weight'ten standart bir öneri yapılır (e.g., BMI bazlı).
    """
    if not user:
        raise HTTPException(status_code=401, detail="Beden önerisi için giriş gerekli")
    p = await db.products.find_one({"id": product_id}, {"_id": 0, "id": 1, "brand": 1, "category": 1, "variants": 1, "size_chart_id": 1})
    if not p:
        raise HTTPException(status_code=404, detail="Ürün bulunamadı")

    u = await db.users.find_one({"id": user["id"]}, {"_id": 0, "height_cm": 1, "weight_kg": 1, "chest_cm": 1, "waist_cm": 1, "hip_cm": 1})
    if not u or not (u.get("height_cm") or u.get("chest_cm")):
        return {"recommended_size": None, "reason": "missing_measurements",
                "message": "Hesabınıza boy/kilo veya göğüs/bel ölçüsü ekleyin"}

    # 1) Brand size chart varsa kullan
    chart = None
    # DENETİM FIX: admin ölçü tablosunu `size_tables` koleksiyonuna (product_id ile) kaydediyor;
    # recommender yalnız `size_charts`+size_chart_id okuduğu için ASLA eşleşmiyor, hep boş dönüyordu.
    # Önce ürünün kendi ölçü tablosunu (size_tables) dene.
    try:
        _st = await db.size_tables.find_one({"product_id": product_id}, {"_id": 0})
        if _st and (_st.get("sizes") or _st.get("rows")):
            chart = {"sizes": _st.get("sizes") or _st.get("rows")}
    except Exception:
        pass
    if not chart and p.get("size_chart_id"):
        chart = await db.size_charts.find_one({"id": p["size_chart_id"]}, {"_id": 0})
    if not chart and p.get("brand"):
        chart = await db.size_charts.find_one(
            {"$or": [{"brand": p["brand"]}, {"brand_name": p["brand"]}],
             "category": p.get("category")}, {"_id": 0})

    user_chest = u.get("chest_cm")
    user_waist = u.get("waist_cm")
    user_hip = u.get("hip_cm")
    user_height = u.get("height_cm")
    user_weight = u.get("weight_kg")

    if chart and chart.get("sizes"):
        best = None
        best_score = float("inf")
        for size_row in chart["sizes"]:
            # Her satırda örn: {size: "M", chest: 92, waist: 76, hip: 100}
            score = 0
            n = 0
            for um, sk in [(user_chest, "chest"), (user_waist, "waist"), (user_hip, "hip")]:
                sv = size_row.get(sk) or size_row.get(sk + "_cm")
                if um is not None and sv is not None:
                    score += (float(um) - float(sv)) ** 2
                    n += 1
            if n == 0:
                continue
            if score < best_score:
                best_score = score
                best = size_row
        if best:
            return {
                "recommended_size": best.get("size") or best.get("label"),
                "reason": "size_chart_match",
                "confidence": max(0, 1 - (best_score / 1000)),  # rough
                "chart_id": chart.get("id"),
            }

    # 2) Fallback: boy+kilo bazlı BMI heuristic (kadın ürünleri için)
    if user_height and user_weight:
        bmi = user_weight / ((user_height / 100) ** 2)
        if bmi < 18.5: size_label = "XS"
        elif bmi < 21: size_label = "S"
        elif bmi < 24: size_label = "M"
        elif bmi < 27: size_label = "L"
        elif bmi < 30: size_label = "XL"
        else: size_label = "XXL"
        return {
            "recommended_size": size_label,
            "reason": "bmi_fallback",
            "bmi": round(bmi, 1),
            "message": "Marka beden tablosu yok — boy/kilo bazlı genel öneri",
        }

    return {"recommended_size": None, "reason": "insufficient_data"}
