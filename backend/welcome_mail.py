"""Hoş geldin indirim kodu e-postası — üye kaydı VE bülten/popup aboneliği ortak kullanır.

Kod, panelde aktif + tarihi geçerli "ilk siparişe özel" kampanyadan DİNAMİK okunur
(koda gömülü değil). coupon_block sunucuda üretilen güvenilir HTML'dir; içine giren
kupon değerleri burada kaçırılır, şablon render'ı bu değişkeni ham basar.
"""
import html as _html
import logging

logger = logging.getLogger(__name__)


async def welcome_coupon_vars(db) -> dict:
    code, disc, block = "", "", ""
    try:
        from shipping_rules import campaign_live
        async for c in db.coupons.find({"is_active": True, "first_order_only": True},
                                       {"_id": 0}).sort("created_at", -1):
            if not (c.get("code") or "").strip() or not campaign_live(c):
                continue
            code = c["code"].strip()
            v = float(c.get("value") or 0)
            if (c.get("type") or "percent") == "percent":
                disc = f"%{int(v)}" if v == int(v) else f"%{v:g}"
            else:
                disc = f"{int(v)} TL" if v == int(v) else f"{v:g} TL"
            break
    except Exception as e:
        logger.warning(f"welcome coupon lookup failed: {e}")
    if code:
        block = (
            '<div style="margin:20px 0 2px;border:1px dashed #c9c4bb;background:#faf8f5;'
            'padding:24px 18px;text-align:center;">'
            '<div style="font-size:11px;letter-spacing:3px;text-transform:uppercase;'
            'color:#8a8579;margin:0 0 8px;">Hoş Geldin Hediyen</div>'
            f'<div style="font-size:15px;color:#3a3631;margin:0 0 14px;">İlk siparişinde '
            f'<b>{_html.escape(disc)} indirim</b></div>'
            '<div style="display:inline-block;border:1px solid #1a1a1a;padding:13px 30px;'
            'font-size:22px;font-weight:700;letter-spacing:5px;color:#1a1a1a;">'
            f'{_html.escape(code)}</div>'
            '<div style="font-size:11px;color:#a39e95;margin:12px 0 0;">'
            'Kodu sepette uygula · ilk siparişe özel</div>'
            '</div>'
        )
    return {"coupon_code": code, "coupon_discount": disc, "coupon_block": block}


async def send_welcome_email(db, email: str, first_name: str = "") -> dict:
    """Hoş geldin e-postasını (kod kutusuyla) gönderir. Best-effort; hata yükseltmez."""
    import os
    try:
        from notification_service import send_notification
        cv = await welcome_coupon_vars(db)
        res = await send_notification(
            db, "welcome", to_email=email,
            variables={
                "customer_name": (first_name or "").strip() or "değerli müşterimiz",
                "site_url": os.environ.get("SITE_URL", "https://meluni.com.tr").rstrip("/"),
                **cv,
            },
            channels=["email"],
        )
        return {"ok": True, "code": cv.get("coupon_code"), "discount": cv.get("coupon_discount"), "res": res}
    except Exception as e:
        logger.warning("welcome email failed (%s)", type(e).__name__)
        return {"ok": False}
