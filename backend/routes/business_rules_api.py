"""
business_rules_api.py — İşletme Kuralları admin + public uçları.
"""
from fastapi import APIRouter, Depends
from .deps import db, require_admin
import business_rules as BR

admin_router = APIRouter(prefix="/admin/business-rules", tags=["admin-business-rules"])
public_router = APIRouter(prefix="/business-rules", tags=["business-rules"])


@admin_router.get("")
async def get_business_rules(current_user: dict = Depends(require_admin)):
    """Katalog meta + mevcut değerler (gruplanmış) — admin ayar ekranı için."""
    return {"groups": await BR.get_all_for_admin(db)}


@admin_router.put("")
async def put_business_rules(payload: dict, current_user: dict = Depends(require_admin)):
    """{values: {key: value, ...}} veya doğrudan {key: value} kaydeder."""
    patch = payload.get("values") if isinstance(payload.get("values"), dict) else payload
    res = await BR.save_rules(db, patch)
    return {"success": True, **res}


# Storefront'un ihtiyaç duyduğu kurallar (checkout ücretleri, geri sayım, tatiller).
# Public — hassas değil; müşteri tarafı bunları okuyup UI'da kullanır.
_PUBLIC_KEYS = [
    "shipping.cod_fee", "shipping.same_day_cutoff", "shipping.work_days",
    "shipping.exclude_official_holidays",
    "payment.points_redeem_max_pct", "product.gift_wrap_price",
    "product.fit_recommendation_enabled", "return.window_days",
    # Vitrin görünüm anahtarları (storefront okur)
    "product.size_guide_enabled", "product.complete_the_look_enabled",
    "product.shipping_countdown_enabled", "storefront.announcement_bar_enabled",
    "product.social_share_enabled",
    "storefront.whatsapp_enabled", "storefront.whatsapp_number", "storefront.whatsapp_message",
    "product.low_stock_badge_enabled", "product.low_stock_badge_threshold",
    "giftcard.enabled", "loyalty.enabled",
]


@public_router.get("")
async def get_public_rules():
    out = {}
    for k in _PUBLIC_KEYS:
        out[k] = await BR.get_rule(db, k)
    out["official_holidays"] = sorted(BR.TR_OFFICIAL_HOLIDAYS)
    return out
