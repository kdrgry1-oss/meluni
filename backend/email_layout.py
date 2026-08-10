"""
email_layout.py — FACETTE markalı tek tip e-posta kabuğu (shell)
=============================================================================
Sitedeki TÜM transactional e-postalar (sipariş bildirimleri, şifre sıfırlama,
iade vs.) bu kabuğu kullanır:

    ┌──────────────────────────┐
    │        FACETTE           │  ← üstte logo
    │        ┌────┐            │
    │        │ ◻ │            │  ← ikon kutusu
    │        └────┘            │
    │      EYEBROW             │
    │      Başlık              │  ← ortada bilgiler
    │      Açıklama metni      │
    │      [  CTA BUTON  ]     │
    │      bilgi kutusu        │
    │  INSTAGRAM · TIKTOK      │  ← altta sosyal + telif
    │  © FACETTE               │
    └──────────────────────────┘

ÖNEMLİ — PLACEHOLDER GÜVENLİĞİ:
  Bu fonksiyon string BİRLEŞTİRME ile çalışır. İçerik argümanlarındaki
  {customer_name}, {order_number} gibi placeholder'lar OLDUĞU GİBİ korunur;
  gönderim anında notification_service.render_template doldurur.
  İçerik argümanları ASLA .format/f-string ile işlenmez.

E-POSTA UYUMU:
  Yalnızca <table> + inline style. <style> bloğu YOK (render_template'in
  regex'ini ve istemci uyumunu bozmamak için), flexbox YOK, inline SVG YOK
  (Gmail uyumu). İkonlar bordered kutu içinde Unicode glyph.
"""
from datetime import datetime, timezone

# ── Marka paleti (ekran tasarımıyla birebir) ───────────────────────────────
_BG     = "#edeae4"   # sayfa arka planı (sıcak açık gri)
_CARD   = "#ffffff"   # mail kartı
_INK    = "#1a1a1a"   # ana metin / siyah
_MUTED  = "#6b6b6b"   # gövde metni
_FAINT  = "#9a9a93"   # eyebrow / footer
_NOTE   = "#f3f1ec"   # bilgi kutusu arka planı

INSTAGRAM_URL = "https://www.instagram.com/facette"
TIKTOK_URL    = "https://www.tiktok.com/@facette"
# E-posta header logosu — mutlak HTTPS PNG (Outlook .webp render etmez).
# facette.com.tr/logo.png frontend/public içinde yayınlanır (Cloudflare CDN).
LOGO_URL      = "https://facette.com.tr/logo.png"

# BEYAZ ETİKET — e-posta markası ayardan gelir (koddan değil). send_smtp_email her gönderim
# öncesi set_brand(company) çağırır; ayar boşsa aşağıdaki FACETTE varsayılanları kullanılır.
_BRAND = {
    "store_name": "FACETTE",
    "logo_url": LOGO_URL,
    "site": "facette.com.tr",
    "instagram": INSTAGRAM_URL,
    "tiktok": TIKTOK_URL,
}


def set_brand(company: dict) -> None:
    """company.py'den gelen firma bilgisini e-posta markasına uygular (yalnız dolu alanlar)."""
    if not isinstance(company, dict):
        return
    if company.get("store_name"):
        _BRAND["store_name"] = str(company["store_name"])
    if company.get("logo_url"):
        _BRAND["logo_url"] = str(company["logo_url"])
    _site = company.get("website") or company.get("site_url") or ""
    if _site:
        _BRAND["site"] = str(_site).replace("https://", "").replace("http://", "").rstrip("/")
    if company.get("instagram"):
        _BRAND["instagram"] = str(company["instagram"])
    if company.get("tiktok"):
        _BRAND["tiktok"] = str(company["tiktok"])

_FONT = "-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif"


def _icon_box(glyph: str) -> str:
    """56x56 ince çerçeveli kutu içinde tek bir glyph (ekran tasarımındaki gibi)."""
    if not glyph:
        return ""
    return (
        '<table role="presentation" cellpadding="0" cellspacing="0" border="0" align="center" '
        'style="margin:0 auto 24px;"><tr>'
        '<td style="width:58px;height:58px;border:1px solid ' + _INK + ';'
        'text-align:center;vertical-align:middle;font-size:24px;line-height:56px;'
        'color:' + _INK + ';">' + glyph + '</td>'
        '</tr></table>'
    )


def email_shell(*, eyebrow="", title="", intro_html="", icon="",
                cta_text="", cta_url="", fallback_url="",
                note_title="", note_html="", body_html="",
                preheader="", year=None, site=None) -> str:
    """Markalı tam-HTML e-posta döndürür. Tüm argümanlar opsiyoneldir; verilmeyen
    bölümler atlanır. İçerik argümanları {placeholder} içerebilir — korunur."""
    yr = str(year or datetime.now(timezone.utc).year)
    if site is None:
        site = _BRAND.get("site") or "facette.com.tr"
    _store = _BRAND.get("store_name") or "FACETTE"
    _logo = _BRAND.get("logo_url") or LOGO_URL
    P = []
    # Sentinel — render_email zaten markalı maili tekrar sarmasın diye.
    P.append("<!--fct-shell-->")

    if preheader:
        P.append('<div style="display:none;max-height:0;overflow:hidden;opacity:0;'
                 'mso-hide:all;">' + preheader + '</div>')

    # Dış sarmal + ortalanmış 600px kart
    P.append(
        '<div style="background:' + _BG + ';margin:0;padding:32px 16px;font-family:' + _FONT + ';">'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">'
        '<tr><td align="center">'
        '<table role="presentation" width="600" cellpadding="0" cellspacing="0" border="0" '
        'style="width:600px;max-width:600px;background:' + _CARD + ';">'
    )

    # Header — gerçek logo görseli (resim engellenirse alt="FACETTE" görünür)
    P.append(
        '<tr><td style="padding:32px 24px 28px;text-align:center;">'
        '<img src="' + _logo + '" alt="' + _store + '" width="150" '
        'style="display:inline-block;width:150px;max-width:62%;height:auto;'
        'border:0;outline:none;text-decoration:none;" />'
        '</td></tr>'
    )

    # Orta blok — ikon + eyebrow + başlık + giriş (ortalı)
    P.append('<tr><td style="padding:6px 44px 4px;text-align:center;">')
    P.append(_icon_box(icon))
    if eyebrow:
        P.append('<div style="font-size:11px;font-weight:500;letter-spacing:3px;'
                 'color:' + _FAINT + ';text-transform:uppercase;margin:0 0 16px;">' + eyebrow + '</div>')
    if title:
        P.append('<h1 style="margin:0 0 18px;font-size:30px;line-height:1.22;'
                 'font-weight:600;color:' + _INK + ';">' + title + '</h1>')
    if intro_html:
        P.append('<div style="font-size:15px;line-height:1.7;color:' + _MUTED + ';margin:0;">' + intro_html + '</div>')
    P.append('</td></tr>')

    # Ek gövde (sipariş tabloları, barkod vs.) — geniş
    if body_html:
        P.append('<tr><td style="padding:10px 44px 4px;">' + body_html + '</td></tr>')

    # CTA butonu
    if cta_text and cta_url:
        P.append(
            '<tr><td style="padding:24px 44px 6px;text-align:center;">'
            '<a href="' + cta_url + '" style="display:inline-block;background:' + _INK + ';'
            'color:#ffffff;text-decoration:none;padding:16px 46px;font-size:13px;font-weight:500;'
            'letter-spacing:2px;text-transform:uppercase;">' + cta_text + '</a>'
            '</td></tr>'
        )

    # Fallback link
    if fallback_url:
        P.append(
            '<tr><td style="padding:20px 44px 4px;text-align:center;">'
            '<div style="font-size:12px;color:' + _FAINT + ';margin:0 0 5px;">'
            'Buton çalışmazsa bu bağlantıyı tarayıcınıza yapıştırın:</div>'
            '<a href="' + fallback_url + '" style="font-size:12px;color:' + _MUTED + ';'
            'word-break:break-all;">' + fallback_url + '</a>'
            '</td></tr>'
        )

    # Bilgi kutusu
    if note_title or note_html:
        nb = ('<tr><td style="padding:24px 44px 6px;">'
              '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
              'style="background:' + _NOTE + ';"><tr><td style="padding:18px 22px;">')
        if note_title:
            nb += '<div style="font-size:13px;font-weight:600;color:' + _INK + ';margin:0 0 6px;">' + note_title + '</div>'
        if note_html:
            nb += '<div style="font-size:13px;line-height:1.65;color:' + _MUTED + ';">' + note_html + '</div>'
        nb += '</td></tr></table></td></tr>'
        P.append(nb)

    # Alt boşluk + kartı kapat
    P.append('<tr><td style="padding:22px;"></td></tr></table>')

    # Footer — sosyal + telif
    P.append(
        '<table role="presentation" width="600" cellpadding="0" cellspacing="0" border="0" '
        'style="width:600px;max-width:600px;"><tr><td style="padding:26px 24px 8px;text-align:center;">'
        '<a href="' + _BRAND.get("instagram", INSTAGRAM_URL) + '" style="font-size:11px;letter-spacing:2px;color:' + _MUTED + ';'
        'text-decoration:none;">INSTAGRAM</a>'
        '<span style="color:' + _FAINT + ';padding:0 12px;">·</span>'
        '<a href="' + _BRAND.get("tiktok", TIKTOK_URL) + '" style="font-size:11px;letter-spacing:2px;color:' + _MUTED + ';'
        'text-decoration:none;">TIKTOK</a>'
        '<div style="font-size:11px;color:' + _FAINT + ';margin:16px 0 4px;letter-spacing:0.3px;">'
        '© ' + yr + ' ' + _store + ' · Tüm hakları saklıdır</div>'
        '<div style="font-size:11px;color:' + _FAINT + ';letter-spacing:0.3px;">'
        'Bu e-posta ' + site + ' hesabınızla ilişkili adrese gönderildi.</div>'
        '</td></tr></table>'
    )

    P.append('</td></tr></table></div>')
    return "".join(P)


# ── Sipariş gövdesi için küçük yardımcılar (placeholder korunur) ────────────
def info_row(label: str, value_html: str) -> str:
    """Gri kutuda 'ETİKET / değer' bloğu (sipariş no, takip no vb.)."""
    return (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
        'style="background:' + _NOTE + ';margin:0 0 4px;"><tr><td style="padding:16px 20px;">'
        '<div style="font-size:11px;letter-spacing:1.5px;color:' + _FAINT + ';'
        'text-transform:uppercase;margin:0 0 6px;">' + label + '</div>'
        '<div style="font-size:16px;color:' + _INK + ';font-weight:600;">' + value_html + '</div>'
        '</td></tr></table>'
    )


def render_email(subject: str = "", inner_html: str = "") -> str:
    """Herhangi bir e-posta HTML'ini markalı kabuğa sarar (logo + içerik + sosyal footer).
    Zaten markalıysa (email_shell ile üretilmiş) olduğu gibi döner — idempotent.
    Bu sayede send_smtp_email üzerinden giden TÜM mailler istisnasız markalı olur."""
    html = inner_html or ""
    low = html.lower()
    if ("<!--fct-shell-->" in low) or (LOGO_URL.lower() in low):
        return html  # zaten markalı
    # Konudan temiz bir başlık türet ("... — 913BS" / "... — FACETTE" kuyruğunu at)
    title = (subject or "").split(" — ")[0].strip()
    return email_shell(title=title, intro_html=html, preheader=(subject or "").strip())
