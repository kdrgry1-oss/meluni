"""
TERK EDİLMİŞ SEPET E-POSTASI — "Seçtiklerin seni bekliyor!"
=============================================================================
Kim alır (HEPSİ şart):
  • ÜYE: sepet kaydı doğrulanmış oturumla (JWT → cart_sessions.user_id) yazılmış olmalı.
    İstemcinin iddia ettiği e-posta KULLANILMAZ; alıcı adresi db.users'tan okunur.
  • E-POSTA TİCARİ İLETİ İZNİ: pazarlama izinleri listesindeki (consents._collect) EN SON
    karar ONAY; kara listede (bounce/şikâyet) değil. RET / abonelikten çıkmış → gönderilmez.
  • Sepet en az `marketing.abandoned_cart_delay_hours` (12 sa) önce güncellenmiş, en fazla
    `marketing.abandoned_cart_max_hours` (48 sa) önce; o saatten sonra üye SİPARİŞ VERMEMİŞ.
  • Aynı üyeye `marketing.abandoned_cart_cooldown_days` (3 gün) içinde ikinci mail gitmez;
    her sepet kaydına en fazla 1 kez gönderilir.

İçerik: ürünler CANLI veriden (güncel fiyat / indirimli fiyat / otomatik kampanya yüzdesi,
görsel, stok). Pasif/silinmiş/stoğu biten ürün maile girmez; hiç ürün kalmazsa mail gitmez.
"SEPETİME DÖN" → paylaşılan-sepet linki (/sepet?paylasim=…&hatirlatma=1): sepet her cihazda
geri yüklenir; aynı cihazda zaten sepette olan ürün tekrar eklenmez.

Kanal: pazarlama sağlayıcısı (Brevo, yedek SES) — ticari ileti işlemsel kanaldan gitmez.
Her mailde abonelikten-çık linki var (İYS/KVKK).
"""
import base64
import html as _html
import secrets
from datetime import datetime, timedelta, timezone

from urllib.parse import quote

_SUBJECT_DEFAULT = "Seçtiklerin seni bekliyor!"
_TITLE_DEFAULT = "SEÇTİKLERİN SENİ BEKLİYOR!"
_SUB1_DEFAULT = "Seçtiğin parçalar hâlâ sepetinde."
_SUB2_DEFAULT = "Look'unu tamamlamak için kaldığın yerden devam et."
_BUTTON_DEFAULT = "SEPETİME DÖN"

_INK = "#111111"
_MUTED = "#6b6b6b"
_FAINT = "#9a9a9a"
_RED = "#d0021b"
_FONT = "-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif"

_MAX_ITEMS = 12          # mail içinde en fazla bu kadar ürün kartı
_SHARE_TTL_DAYS = 30


def _e(s) -> str:
    return _html.escape(str(s or ""), quote=True)


def fmt_tl(v) -> str:
    """1299.9 → '1.299,90 TL' (tr-TR)."""
    try:
        n = float(v or 0)
    except Exception:
        n = 0.0
    s = f"{n:,.2f}"                       # 1,299.90
    return s.replace(",", "X").replace(".", ",").replace("X", ".") + " TL"


def price_view(p: dict, price_diff: float = 0.0) -> dict:
    """Vitrin kartıyla (frontend/src/lib/price.js::priceView) AYNI kural:
    indirimli fiyat (sale_price) varsa o geçerli, kampanya uygulanmaz; yoksa otomatik kampanya
    yüzdesi liste fiyatına uygulanır. Varyant fiyat farkı iki tarafa da eklenir."""
    diff = float(price_diff or 0)
    lst = float(p.get("price") or 0) + diff
    sp_raw = float(p.get("sale_price") or 0)
    sale = sp_raw + diff if (sp_raw > 0 and sp_raw < float(p.get("price") or 0)) else None
    camp = float(p.get("campaign_discount_percent") or 0)
    if sale is not None:
        disp = sale
    elif camp > 0:
        disp = round(lst * (1 - camp / 100.0), 2)
    else:
        disp = lst
    pct = int(round((lst - disp) / lst * 100)) if (lst > 0 and disp < lst - 0.001) else 0
    return {"list": lst, "display": disp, "discount_pct": pct}


def jpeg_url(api_base: str, src: str, w: int = 560) -> str:
    """Ürün görselleri CDN'de WebP; Outlook vb. WebP göstermez → mevcut JPEG vekil ucu
    (/api/upload/jpeg/<b64>.jpg, izinli host listesi + SSRF korumalı). Vekil yoksa kaynak."""
    if not src or not api_base:
        return src or ""
    tok = base64.urlsafe_b64encode(src.encode("utf-8")).decode().rstrip("=")
    return f"{api_base}/api/upload/jpeg/{tok}.jpg?w={int(w)}"


# ─────────────────────────────── HTML ───────────────────────────────────────
def _card(it: dict) -> str:
    """Tek ürün kartı: görsel (+ sol üstte % rozeti), ad, beden/adet, üstü çizili + kırmızı fiyat."""
    badge = ""
    if it.get("discount_pct"):
        # Sıfır-yükseklikli kapsayıcı: rozet görselin ÜSTÜNE biner (Gmail/Apple Mail). Desteklemeyen
        # istemcide (Outlook) rozet görselin hemen üstünde görünür — bozulmaz.
        badge = (
            '<div style="height:0;max-height:0;overflow:visible;line-height:0;">'
            '<span style="position:relative;z-index:2;display:inline-block;margin:10px 0 0 10px;background:' + _RED + ';color:#ffffff;'
            'font-size:12px;line-height:12px;font-weight:700;padding:6px 8px;letter-spacing:0.5px;'
            'font-family:' + _FONT + ';">%' + str(int(it["discount_pct"])) + '</span></div>'
        )
    meta = []
    if it.get("size"):
        meta.append("Beden: " + _e(it["size"]))
    if (it.get("qty") or 1) > 1:
        meta.append(f'{int(it["qty"])} adet')
    meta_html = ""
    if meta:
        meta_html = ('<div style="font-size:12px;color:' + _FAINT + ';margin:0 0 6px;">'
                     + " · ".join(meta) + '</div>')
    if it.get("discount_pct"):
        price_html = (
            '<span style="font-size:13px;color:' + _FAINT + ';text-decoration:line-through;">'
            + _e(fmt_tl(it["list"])) + '</span>&nbsp;&nbsp;'
            '<span style="font-size:15px;font-weight:700;color:' + _RED + ';">'
            + _e(fmt_tl(it["display"])) + '</span>'
        )
    else:
        price_html = ('<span style="font-size:15px;font-weight:700;color:' + _INK + ';">'
                      + _e(fmt_tl(it["display"])) + '</span>')
    img = ""
    if it.get("image"):
        img = ('<img src="' + _e(it["image"]) + '" alt="' + _e(it.get("name")) + '" width="252" '
               'style="display:block;width:100%;max-width:252px;height:auto;border:0;outline:none;'
               'text-decoration:none;background:#f4f4f4;" />')
    href = _e(it.get("url") or "#")
    return (
        '<div style="line-height:0;">' + badge
        + '<a href="' + href + '" style="text-decoration:none;display:block;">' + img + '</a></div>'
        '<div style="padding:12px 2px 0;text-align:left;font-family:' + _FONT + ';">'
        '<a href="' + href + '" style="text-decoration:none;color:' + _INK + ';">'
        '<div style="font-size:13px;line-height:1.45;color:' + _INK + ';margin:0 0 4px;">'
        + _e(it.get("name")) + '</div></a>'
        + meta_html + price_html + '</div>'
    )


def _grid(items: list) -> str:
    """2 sütun; tek sayıda üründe son kart ORTALANIR (şablondaki gibi)."""
    rows = []
    for i in range(0, len(items), 2):
        pair = items[i:i + 2]
        if len(pair) == 2:
            rows.append(
                '<tr>'
                '<td width="50%" valign="top" style="width:50%;padding:0 8px 28px 0;">' + _card(pair[0]) + '</td>'
                '<td width="50%" valign="top" style="width:50%;padding:0 0 28px 8px;">' + _card(pair[1]) + '</td>'
                '</tr>'
            )
        else:
            rows.append(
                '<tr><td colspan="2" align="center" style="padding:0 0 28px;">'
                '<table role="presentation" width="50%" cellpadding="0" cellspacing="0" border="0" '
                'style="width:50%;"><tr><td valign="top" style="padding:0 4px;">' + _card(pair[0]) + '</td></tr></table>'
                '</td></tr>'
            )
    return ('<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
            'style="width:100%;">' + "".join(rows) + '</table>')


def _social(brand: dict) -> str:
    base = (brand.get("site_url") or "").rstrip("/")
    icons = []
    for key, label in (("instagram", "Instagram"), ("tiktok", "TikTok"), ("facebook", "Facebook")):
        url = (brand.get(key) or "").strip()
        if not url:
            continue
        icons.append(
            '<a href="' + _e(url) + '" style="display:inline-block;margin:0 9px;text-decoration:none;">'
            '<img src="' + _e(f"{base}/email/{key}.png") + '" alt="' + label + '" width="22" height="22" '
            'style="display:block;width:22px;height:22px;border:0;" /></a>'
        )
    return "".join(icons)


def build_html(*, brand: dict, items: list, cta_url: str, unsub_url: str, texts: dict | None = None) -> str:
    """Tam HTML e-posta. `brand`: store_name, logo_url, site_url, instagram/tiktok/facebook, tagline.
    `items`: [{name, image, url, list, display, discount_pct, size, qty}]."""
    t = texts or {}
    title = _e(t.get("title") or _TITLE_DEFAULT)
    sub1 = _e(t.get("subtitle1") or _SUB1_DEFAULT)
    sub2 = _e(t.get("subtitle2") or _SUB2_DEFAULT)
    button = _e(t.get("button") or _BUTTON_DEFAULT)
    store = _e(brand.get("store_name") or "")
    logo = (brand.get("logo_url") or "").strip()
    tagline = _e(brand.get("tagline") or "")
    cta = _e(cta_url)
    button_html = (
        '<table role="presentation" cellpadding="0" cellspacing="0" border="0" align="center"><tr>'
        '<td style="background:' + _INK + ';">'
        '<a href="' + cta + '" style="display:inline-block;padding:16px 52px;font-family:' + _FONT + ';'
        'font-size:13px;font-weight:600;letter-spacing:2.5px;color:#ffffff;text-decoration:none;">'
        + button + '</a></td></tr></table>'
    )
    head_logo = (
        '<img src="' + _e(logo) + '" alt="' + store + '" width="150" style="display:inline-block;'
        'width:150px;max-width:60%;height:auto;border:0;" />' if logo else
        '<div style="font-size:26px;letter-spacing:8px;font-weight:600;color:' + _INK + ';">' + store + '</div>'
    )
    preheader = sub1 + " " + sub2
    unsub = ""
    if unsub_url:
        unsub = ('<div style="font-size:11px;line-height:1.6;color:' + _FAINT + ';margin-top:18px;">'
                 'Bu e-postayı ticari ileti izniniz olduğu için aldınız. '
                 '<a href="' + _e(unsub_url) + '" style="color:' + _FAINT + ';text-decoration:underline;">'
                 'Abonelikten çık</a></div>')
    yr = datetime.now(timezone.utc).year
    return (
        '<!doctype html><html lang="tr"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<meta name="x-apple-disable-message-reformatting">'
        '<title>' + title + '</title>'
        '<style>body{margin:0!important;padding:0!important;background:#ffffff;}'
        'table{border-collapse:collapse;}img{border:0;outline:none;text-decoration:none;}</style>'
        '</head><body style="margin:0;padding:0;background:#ffffff;">'
        '<!--fct-shell-->'
        '<div style="display:none;max-height:0;overflow:hidden;opacity:0;mso-hide:all;">' + preheader + '</div>'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background:#ffffff;">'
        '<tr><td align="center" style="padding:0 12px;">'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
        'style="width:100%;max-width:560px;font-family:' + _FONT + ';">'
        # Logo
        '<tr><td align="center" style="padding:40px 0 34px;">' + head_logo + '</td></tr>'
        # Başlık + alt metin
        '<tr><td align="center" style="padding:0 8px;">'
        '<h1 style="margin:0 0 16px;font-size:24px;line-height:1.3;font-weight:700;letter-spacing:1.5px;'
        'color:' + _INK + ';">' + title + '</h1>'
        '<div style="font-size:15px;line-height:1.65;color:' + _MUTED + ';">' + sub1 + '<br>' + sub2 + '</div>'
        '</td></tr>'
        # Buton
        '<tr><td align="center" style="padding:28px 0 38px;">' + button_html + '</td></tr>'
        # Ürünler
        '<tr><td style="padding:0;">' + _grid(items) + '</td></tr>'
        # Alt buton (uzun listede aşağıda da olsun)
        + ('<tr><td align="center" style="padding:6px 0 34px;">' + button_html + '</td></tr>' if len(items) > 2 else '')
        # Footer
        + '<tr><td align="center" style="padding:30px 8px 40px;border-top:1px solid #eeeeee;">'
        '<div style="font-size:18px;letter-spacing:6px;font-weight:600;color:' + _INK + ';">' + store + '</div>'
        + ('<div style="font-size:13px;font-style:italic;color:' + _MUTED + ';margin-top:8px;">' + tagline + '</div>' if tagline else '')
        + '<div style="margin-top:20px;">' + _social(brand) + '</div>'
        '<div style="font-size:11px;color:' + _FAINT + ';margin-top:18px;">© ' + str(yr) + ' ' + store + '</div>'
        + unsub +
        '</td></tr>'
        '</table></td></tr></table></body></html>'
    )


# ─────────────────────────────── VERİ ───────────────────────────────────────
async def _brand(db) -> dict:
    from company import get_company
    c = await get_company(db)
    site = (c.get("site_url") or "").rstrip("/")
    if not site:
        # Firma bilgisinde vitrin adresi boşsa env → API adresinden türet (api.alanadi → alanadi).
        # Aksi halde logo GÖRSELİ yerine düz yazı basılıyordu (kullanıcı: "logo görselini kullan").
        import os as _os
        site = (_os.environ.get("SITE_URL") or _os.environ.get("FRONTEND_PUBLIC_URL") or "").strip().rstrip("/")
        if not site:
            _api = await api_base(db)
            if _api.startswith("https://api."):
                site = "https://" + _api[len("https://api."):]
    try:
        from business_rules import get_rule
        tagline = str(await get_rule(db, "marketing.abandoned_cart_email_tagline", "") or "").strip()
    except Exception:
        tagline = ""
    return {
        "store_name": c.get("store_name") or "",
        "logo_url": c.get("logo_url") or (f"{site}/logo.png" if site else ""),
        "site_url": site,
        "instagram": c.get("instagram") or "", "tiktok": c.get("tiktok") or "",
        "facebook": c.get("facebook") or "", "tagline": tagline,
    }


async def api_base(db) -> str:
    """Backend'in PUBLIC adresi (JPEG vekili + abonelikten-çık linki backend'de çalışır;
    vitrin alan adındaki /api yolu SPA'ya düşer)."""
    import os
    from tenant_config import get_tenant_config
    try:
        cfg = await get_tenant_config(db)
        u = (cfg.get("domains") or {}).get("api_url") or ""
    except Exception:
        u = ""
    u = (u or os.environ.get("PUBLIC_API_URL") or "").strip().rstrip("/")
    if u.endswith("/api"):
        u = u[:-4]
    return u


async def live_items(db, cart: dict, *, site: str, api: str) -> list:
    """Sepet kalemlerini CANLI ürün verisiyle kurar. Pasif/silinmiş/stoksuz kalem atlanır."""
    from routes.products import _auto_campaigns_for_badges, _apply_campaign_badge
    raw = [x for x in (cart.get("items") or []) if isinstance(x, dict) and x.get("product_id")]
    pids = list({str(x["product_id"])[:64] for x in raw})
    if not pids:
        return []
    prods = {}
    async for p in db.products.find(
            {"id": {"$in": pids}},
            {"_id": 0, "id": 1, "name": 1, "slug": 1, "price": 1, "sale_price": 1, "images": 1,
             "thumbnail": 1, "variants": 1, "is_active": 1, "stock": 1, "category_id": 1,
             "category_ids": 1, "deleted": 1, "is_deleted": 1}):
        prods[p["id"]] = p
    try:
        camps = await _auto_campaigns_for_badges()
    except Exception:
        camps = []
    from routes.email_marketing import _abs_img_url
    out, seen = [], set()
    for x in raw:
        p = prods.get(str(x["product_id"]))
        if not p or p.get("is_active") is False or p.get("deleted") or p.get("is_deleted"):
            continue
        vid = str(x.get("variant_id") or "")[:64] or None
        key = (p["id"], vid)
        if key in seen:
            continue
        seen.add(key)
        variants = [v for v in (p.get("variants") or []) if isinstance(v, dict)]
        var = next((v for v in variants if str(v.get("id") or "") == vid), None) if vid else None
        if var is not None:
            if int(var.get("stock") or 0) <= 0:
                continue
        elif variants:
            if sum(int(v.get("stock") or 0) for v in variants) <= 0:
                continue
        elif p.get("stock") is not None and int(p.get("stock") or 0) <= 0:
            continue
        try:
            _apply_campaign_badge(p, camps)
        except Exception:
            p["campaign_discount_percent"] = 0
        diff = 0.0
        if var is not None:
            try:
                diff = float(var.get("price_diff") or var.get("price_adjustment") or 0)
            except Exception:
                diff = 0.0
        pv = price_view(p, diff)
        if pv["display"] <= 0:
            continue
        img = _abs_img_url((p.get("images") or []) + ([p["thumbnail"]] if p.get("thumbnail") else []), site)
        try:
            qty = max(1, min(int(x.get("qty") or x.get("quantity") or 1), 20))
        except Exception:
            qty = 1
        out.append({
            "product_id": p["id"], "variant_id": vid, "qty": qty,
            "name": p.get("name") or "", "size": (var or {}).get("size") or "",
            "image": jpeg_url(api, img) if img else "",
            "url": f"{site}/{quote(p.get('slug') or '')}" if p.get("slug") else site,
            **pv,
        })
        if len(out) >= _MAX_ITEMS:
            break
    return out


async def _create_share(db, items: list, *, user_id: str) -> str:
    share_id = secrets.token_urlsafe(8)
    now = datetime.now(timezone.utc)
    await db.shared_carts.insert_one({
        "id": share_id,
        "items": [{"product_id": i["product_id"], "variant_id": i.get("variant_id"),
                   "quantity": i.get("qty") or 1} for i in items],
        "source": "abandoned_cart_email", "user_id": user_id,
        "created_at": now.isoformat(),
        "expires_at": (now + timedelta(days=_SHARE_TTL_DAYS)).isoformat(),
    })
    return share_id


async def _texts(db) -> dict:
    from business_rules import get_rule
    out = {}
    for k, rk, d in (("subject", "marketing.abandoned_cart_email_subject", _SUBJECT_DEFAULT),
                     ("title", "marketing.abandoned_cart_email_title", _TITLE_DEFAULT)):
        try:
            out[k] = str(await get_rule(db, rk, d) or d).strip() or d
        except Exception:
            out[k] = d
    return out


async def _send(db, to: str, subject: str, html: str) -> dict:
    """Pazarlama sağlayıcısıyla (Brevo → yedek SES) TEK alıcıya gönderir."""
    from routes.email_marketing import _selected_provider
    from email_brevo import send_test_email as brevo_send
    from email_ses import send_ses_email
    provider, cfg = await _selected_provider()
    if not cfg:
        return {"success": False, "error": "pazarlama e-posta sağlayıcısı yapılandırılmamış", "provider": provider}
    if provider == "brevo":
        r = await brevo_send(cfg, to, subject, html, cfg.get("reply_to") or "", tags=["abandoned-cart"])
    else:
        r = await send_ses_email(cfg, to, subject, html, cfg.get("reply_to") or "")
    r = dict(r or {})
    r["provider"] = provider
    return r


async def consent_decision(db, email: str, latest: dict | None = None, suppressed: set | None = None) -> str:
    """'onay' | 'ret' | '' — pazarlama izinleri ekranıyla AYNI kaynak ve kural."""
    em = (email or "").strip().lower()
    if not em:
        return ""
    if latest is None:
        from routes.consents import _collect
        latest = await _collect()
    if suppressed is not None and em in suppressed:
        return "ret"
    rec = latest.get(("email", em))
    if not rec:
        return ""
    if rec.get("suppressed"):
        return "ret"
    return rec.get("status") or ""


# ─────────────────────────────── İŞ ─────────────────────────────────────────
async def run(db, *, now: datetime | None = None, dry_run: bool = False, limit: int = 200) -> dict:
    """İşi çalıştırır; gerçek çalıştırmanın özetini panele (son çalıştırma) yazar."""
    stats = await _run(db, now=now, dry_run=dry_run, limit=limit)
    if not dry_run:
        try:
            await db.settings.update_one({"id": "abandoned_cart_email_last_run"}, {"$set": {
                "id": "abandoned_cart_email_last_run",
                "at": datetime.now(timezone.utc).isoformat(), "stats": stats}}, upsert=True)
        except Exception:
            pass
    return stats


async def _run(db, *, now: datetime | None = None, dry_run: bool = False, limit: int = 200) -> dict:
    """Zamanlayıcı işi. dry_run=True → hiçbir şey gönderilmez/yazılmaz, yalnız sayım döner
    (tanı için; e-posta/ad gibi kişisel veri DÖNDÜRMEZ)."""
    from business_rules import get_rule
    now = now or datetime.now(timezone.utc)
    stats = {"candidates": 0, "sent": 0, "failed": 0, "skip_no_consent": 0, "skip_ordered": 0,
             "skip_active": 0, "skip_cooldown": 0, "skip_no_items": 0, "skip_no_user": 0,
             "dry_run": dry_run}
    if not bool(await get_rule(db, "marketing.abandoned_cart_email_enabled", True)):
        stats["disabled"] = True
        return stats
    delay_h = max(1, int(await get_rule(db, "marketing.abandoned_cart_delay_hours", 12) or 12))
    max_h = int(await get_rule(db, "marketing.abandoned_cart_max_hours", 48) or 48)
    if max_h <= delay_h:
        max_h = delay_h + 12
    cool_d = max(0, int(await get_rule(db, "marketing.abandoned_cart_cooldown_days", 3) or 0))
    newest = (now - timedelta(hours=delay_h)).isoformat()
    oldest = (now - timedelta(hours=max_h)).isoformat()

    carts = await db.cart_sessions.find(
        {"updated_at": {"$gte": oldest, "$lte": newest}, "total": {"$gt": 0},
         "user_id": {"$nin": [None, ""]}, "abandoned_email_sent": {"$ne": True},
         "abandoned_email_attempts": {"$not": {"$gte": 3}}},
        {"_id": 0}).sort("updated_at", -1).to_list(limit)
    # Üye başına EN GÜNCEL sepet (birden çok cihaz/oturum).
    by_user: dict = {}
    for c in carts:
        by_user.setdefault(c["user_id"], c)
    stats["candidates"] = len(by_user)
    if not by_user:
        return stats

    from routes.consents import _collect
    from routes.email_marketing import _suppressed_set, _unsub_token
    latest = await _collect()
    suppressed = await _suppressed_set()
    brand = await _brand(db)
    site = brand["site_url"]
    api = await api_base(db)
    texts = await _texts(db)

    async def _mark(c, **fields):
        if dry_run:
            return
        await db.cart_sessions.update_one({"session_id": c["session_id"]}, {"$set": fields})

    for uid, cart in by_user.items():
        user = await db.users.find_one({"id": uid}, {"_id": 0, "id": 1, "email": 1, "is_active": 1,
                                                     "last_abandoned_cart_email_at": 1})
        email = ((user or {}).get("email") or "").strip().lower()
        if not user or user.get("is_active") is False or not email:
            stats["skip_no_user"] += 1
            await _mark(cart, abandoned_email_sent=True, abandoned_email_skip="no_user")
            continue
        # Üye bu sepetten SONRA başka bir cihazda/oturumda hâlâ alışveriş yapıyor → henüz terk değil.
        if await db.cart_sessions.find_one({"user_id": uid, "updated_at": {"$gt": newest}}, {"_id": 0, "session_id": 1}):
            stats["skip_active"] += 1
            continue
        if await consent_decision(db, email, latest, suppressed) != "onay":
            stats["skip_no_consent"] += 1
            await _mark(cart, abandoned_email_sent=True, abandoned_email_skip="no_consent")
            continue
        # Sepet güncellemesinden (30 dk pay ile) sonra sipariş verdiyse gönderme.
        # created_at bazı kayıtlarda ISO metin, bazılarında datetime → ikisi de sorgulanır.
        since_dt = datetime.fromisoformat(cart["updated_at"]) - timedelta(minutes=30)
        if await db.orders.find_one({"$and": [
                {"$or": [{"created_at": {"$gte": since_dt.isoformat()}}, {"created_at": {"$gte": since_dt}}]},
                {"$or": [{"user_id": uid}, {"customer_email": email}, {"shipping_address.email": email}]}]},
                {"_id": 0, "id": 1}):
            stats["skip_ordered"] += 1
            await _mark(cart, abandoned_email_sent=True, abandoned_email_skip="ordered")
            continue
        last = user.get("last_abandoned_cart_email_at") or ""
        if cool_d and last and last > (now - timedelta(days=cool_d)).isoformat():
            stats["skip_cooldown"] += 1
            await _mark(cart, abandoned_email_sent=True, abandoned_email_skip="cooldown")
            continue
        items = await live_items(db, cart, site=site, api=api)
        if not items:
            stats["skip_no_items"] += 1
            await _mark(cart, abandoned_email_sent=True, abandoned_email_skip="no_items")
            continue
        if dry_run:
            stats["sent"] += 1   # "gönderilecekti"
            continue
        share_id = await _create_share(db, items, user_id=uid)
        utm = "utm_source=email&utm_medium=abandoned_cart&utm_campaign=sepet_hatirlatma"
        cta = f"{site}/sepet?paylasim={share_id}&hatirlatma=1&{utm}"
        for it in items:
            it["url"] = it["url"] + ("&" if "?" in it["url"] else "?") + utm
        unsub = f"{api}/api/email-marketing/unsubscribe?e={quote(email)}&t={_unsub_token(email)}" if api else ""
        html = build_html(brand=brand, items=items, cta_url=cta, unsub_url=unsub, texts=texts)
        try:
            r = await _send(db, email, texts["subject"], html)
        except Exception as ex:
            r = {"success": False, "error": str(ex)[:200]}
        ts = datetime.now(timezone.utc).isoformat()
        # Panel ("Gönderilen E-postalar") için: alıcı, konu ve maildeki ürünlerin anlık görüntüsü
        # (önizleme bu görüntüden yeniden çizilir; o anki fiyatlar korunur).
        snap = [{k: it.get(k) for k in ("product_id", "name", "image", "url", "list", "display",
                                         "discount_pct", "size", "qty")} for it in items]
        await db.abandoned_cart_emails.insert_one({
            "id": secrets.token_hex(8), "user_id": uid, "session_id": cart["session_id"],
            "email": email, "subject": texts["subject"], "title": texts.get("title"),
            "products": snap,
            "cart_total": round(sum(float(i["display"]) * int(i.get("qty") or 1) for i in items), 2),
            "share_id": share_id, "items": len(items), "provider": r.get("provider"),
            "status": "sent" if r.get("success") else "failed",
            "error": "" if r.get("success") else str(r.get("error") or r.get("response") or "")[:200],
            "created_at": ts,
        })
        if r.get("success"):
            stats["sent"] += 1
            await db.cart_sessions.update_many(
                {"user_id": uid, "updated_at": {"$lte": newest}},
                {"$set": {"abandoned_email_sent": True, "abandoned_email_at": ts}})
            await db.users.update_one({"id": uid}, {"$set": {"last_abandoned_cart_email_at": ts}})
        else:
            stats["failed"] += 1
            await db.cart_sessions.update_one({"session_id": cart["session_id"]},
                                              {"$inc": {"abandoned_email_attempts": 1}})
    return stats
