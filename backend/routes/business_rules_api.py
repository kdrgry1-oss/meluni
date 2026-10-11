"""
business_rules_api.py — İşletme Kuralları admin + public uçları.
"""
from fastapi import APIRouter, Depends, HTTPException, Request
from .deps import db, require_admin, require_permission
import business_rules as BR

admin_router = APIRouter(prefix="/admin/business-rules", tags=["admin-business-rules"])

# ADM-16: müşteri/ödeme risk kuralları — yalnız süper admin (security.* ile aynı fail-closed denetimli yol).
_RISK_KEYS = frozenset({
    "payment.allow_non3ds_card_pay", "payment.card_velocity_max_fails", "payment.card_velocity_window_min",
    "order.enforce_total_match", "order.block_oversell", "order.max_unpaid_open_orders",
    # ADM-15: asgari uygulama sürümü — düşürülürse eski (açıklı) sürüm yeniden API'ye girer.
    "app.min_version_admin", "app.min_version_customer",
})
public_router = APIRouter(prefix="/business-rules", tags=["business-rules"])


@admin_router.get("")
async def get_business_rules(current_user: dict = Depends(require_permission("settings.view"))):
    """Katalog meta + mevcut değerler (gruplanmış) — admin ayar ekranı için."""
    return {"groups": await BR.get_all_for_admin(db)}


@admin_router.put("")
async def put_business_rules(payload: dict, request: Request, current_user: dict = Depends(require_permission("settings.edit"))):
    """{values: {key: value, ...}} veya doğrudan {key: value} kaydeder."""
    patch = payload.get("values") if isinstance(payload.get("values"), dict) else payload
    patch = dict(patch or {})
    # ADM-15: asgari sürüm biçimi (boş = zorlama yok, ya da 1.2.3 gibi). Geçersiz değer KAYDEDİLMEZ.
    from security.app_version_policy import RULE_KEYS as _APP_VER_KEYS, is_valid_min_version
    for _k in _APP_VER_KEYS:
        if _k in patch:
            if not isinstance(patch[_k], (str, int, float)) or isinstance(patch[_k], bool) \
                    or not is_valid_min_version(patch[_k]):
                raise HTTPException(status_code=400,
                                    detail="Asgari uygulama sürümü 1.2.3 biçiminde olmalı (boş = zorlama yok).")
            patch[_k] = str(patch[_k]).strip()
    # GÜVENLİK (denetim 2026-10): güvenlik kuralları (uyarı alıcıları/gönderen, admin MFA zorunluluğu
    # vb. `security.*`) yalnız SÜPER ADMIN tarafından değiştirilebilir — aksi halde düşük yetkili bir
    # hesap güvenlik uyarılarını kendine yönlendirebilir ya da MFA'yı kapatabilirdi.
    # sms.* (küresel SMS maliyet tavanı, BL-006) da güvenlik kuralıdır: yükseltilirse SMS pompalama
    # koruması zayıflar → yalnız süper admin, fail-closed denetimle.
    # ADM-16: ödeme/sipariş RİSK kuralları (3DS'siz kart ödemesi, kart-testi eşikleri, tutar eşleşmesi,
    # oversell ve açık ödenmemiş sipariş sınırı) da rutin personel tarafından kapatılamaz/gevşetilemez.
    # KVKK: privacy.* (kişisel veri saklama süresi / minimizasyon) da yalnız süper admin.
    _sec_keys = [k for k in patch if str(k).startswith(("security.", "sms.", "privacy.")) or k in _RISK_KEYS]
    if _sec_keys and current_user.get("is_super_admin") is not True:
        raise HTTPException(status_code=403, detail="Güvenlik ve risk kurallarını yalnız süper admin değiştirebilir.")
    # MFA zorunluluğunu kapatma gibi kritik değişiklikler (≥2 süper admin varsa) ikinci onaya gider.
    blocked = None
    try:
        from security.approvals import guard_business_rules_patch
        blocked = await guard_business_rules_patch(patch, current_user, request)
    except HTTPException:
        raise
    except Exception:
        # FAIL-CLOSED: onay kontrolü yapılamadıysa kritik anahtar onaysız KAYDEDİLMEZ.
        raise HTTPException(status_code=503, detail="Güvenlik onay kontrolü yapılamadı; değişiklik kaydedilmedi.")
    _sec_left = [k for k in _sec_keys if k in patch]  # onaya gidenler patch'ten çıkarıldı
    if _sec_left:
        # FAIL-CLOSED denetim: güvenlik kuralı değişikliği, denetim kaydı (intent) yazılamazsa YAPILMAZ (503).
        from activity_audit import run_audited
        _out: dict = {}

        async def _perform():
            _out["res"] = await BR.save_rules(db, patch)
        await run_audited(db, action="business_rules.security_update", entity_type="business_rules",
                          entity_id=",".join(sorted(_sec_left)), perform=_perform, current_user=current_user,
                          request=request, source="admin.business_rules",
                          after={k: patch.get(k) for k in _sec_left})
        res = _out.get("res") or {}
    else:
        res = await BR.save_rules(db, patch)
    if blocked:
        return {**blocked, **res}
    return {"success": True, **res}


# Storefront'un ihtiyaç duyduğu kurallar (checkout ücretleri, geri sayım, tatiller).
# Public — hassas değil; müşteri tarafı bunları okuyup UI'da kullanır.
_PUBLIC_KEYS = [
    "order.havale_cancel_hours",  # müşteriye zaten söylenen süre (banka bilgi maili) — teşhis için açık
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
    # ADM-15: native uygulama açılışta asgari sürümü görebilsin (hassas değil).
    "app.min_version_admin", "app.min_version_customer",
]


@public_router.get("")
async def get_public_rules():
    out = {}
    for k in _PUBLIC_KEYS:
        out[k] = await BR.get_rule(db, k)
    out["official_holidays"] = sorted(BR.TR_OFFICIAL_HOLIDAYS)
    return out
