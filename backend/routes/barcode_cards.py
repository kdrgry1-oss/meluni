"""
=============================================================================
barcode_cards.py — Ürün ve Sipariş barkod/etiket yazdırma endpoint'leri
=============================================================================

AMAÇ:
  Giyim mağazalarındaki gibi barkodlu ürün kartı ve (ileride) sipariş etiketi
  yazdırma. Her kart üzerinde:
    - Ürün adı + marka
    - Stok kodu (iç tanımlama)
    - GTIN/EAN-13 barkod (makine okuyabilir Code128 / EAN-13 fallback)
    - Beden / renk (varyant)
    - Fiyat (indirimli varsa üstü çizili + indirimli)
    - Kategori
  
  Çıktı HTML olarak döner; frontend yeni pencere açıp window.print() tetikler.
  Tarayıcının "PDF olarak kaydet" seçeneği ile PDF yapılabilir — zebra
  yazıcılara ayrıca PDF sürücüsü üzerinden gönderilebilir.

ENDPOINT'LER:
  - GET  /api/products/{product_id}/barcode-card
         Tek ürün, TÜM varyantları için ayrı kart (2 kart/satır A4).
  - POST /api/products/barcode-cards/bulk  body: {"ids": [id, id, ...]}
         Çoklu ürün, her ürünün tüm varyantları.

KULLANAN FRONTEND:
  /app/frontend/src/pages/admin/Products.jsx
    - handlePrintBarcode  → tek ürün
    - handleBulkPrintBarcodes → seçili ürünler
=============================================================================
"""
from fastapi import APIRouter, HTTPException, Response, Depends, Query
from typing import List
import base64
import io
import re

from .deps import db, get_current_user, require_admin, verify_admin_token

# python-barcode zaten requirements.txt'te (0.16.1)
import barcode
from barcode.writer import ImageWriter, SVGWriter

router = APIRouter(prefix="/products", tags=["Barcode Cards"])


# ---------------------------------------------------------------------------
# Yardımcılar
# ---------------------------------------------------------------------------
def _barcode_png_base64(code: str) -> str:
    """
    Verilen kod için PNG barkod üretir ve base64 string döner (data: URI için).
    EAN-13 (13 hane rakam) denenir; uygun değilse Code128 fallback.
    """
    if not code:
        return ""
    try:
        buf = io.BytesIO()
        clean = "".join(ch for ch in str(code) if ch.isdigit() or ch.isalnum())
        if len(clean) == 13 and clean.isdigit():
            # EAN-13
            ean = barcode.get("ean13", clean[:12], writer=ImageWriter())
            ean.write(buf, options={"write_text": False, "module_height": 10.0})
        else:
            # Code128 (alfanumerik uyumlu)
            c128 = barcode.get("code128", clean or str(code), writer=ImageWriter())
            c128.write(buf, options={"write_text": False, "module_height": 10.0})
        b64 = base64.b64encode(buf.getvalue()).decode("ascii")
        return f"data:image/png;base64,{b64}"
    except Exception:
        return ""


def _barcode_svg_inline(code: str) -> str:
    """EAN-13/Code128 barkodu VEKTOR (SVG) uretir; kaliteli yazicida net cikar.
    SVG olculeri mm bazlidir -> %100 olcekte fiziksel olarak dogru basar."""
    if not code:
        return ""
    try:
        clean = "".join(ch for ch in str(code) if ch.isalnum())
        # Barkod çubukları büyütüldü (etiketi doldursun) — genişlik CSS'te %100'e ölçeklenir.
        opts = {"write_text": False, "module_height": 13.0, "module_width": 0.46, "quiet_zone": 1.0}
        if len(clean) == 13 and clean.isdigit():
            bc = barcode.get("ean13", clean[:12], writer=SVGWriter())
        else:
            bc = barcode.get("code128", clean or str(code), writer=SVGWriter())
        buf = io.BytesIO()
        bc.write(buf, options=opts)
        svg = buf.getvalue().decode("utf-8")
        svg = re.sub(r"<\?xml[^>]*\?>", "", svg)
        svg = re.sub(r"<!DOCTYPE[^>]*>", "", svg, flags=re.S | re.I)
        svg = re.sub(r"<!--.*?-->", "", svg, flags=re.S)
        svg = svg.replace("<svg ", '<svg class="barcode-svg" ', 1)
        return svg.strip()
    except Exception:
        return ""


def _card_html_for_variant(product: dict, variant: dict) -> str:
    """Tek varyant icin tek barkod ETIKETI (5cm x 4cm). Markasiz, sola yasli.
    Tasarim: urun adi / urun kart no / renk(sol)+beden(sag) / barkod / numara."""
    import html as _html_b
    def _hb(v):  # GÜVENLİK: ürün/varyant alanlarını HTML-escape et (pazaryeri kaynaklı stored-XSS)
        return _html_b.escape(str(v if v is not None else ""))
    name = _hb((product.get("name", "") or "").strip())
    stock_code = variant.get("stock_code") or product.get("stock_code") or ""
    # Barkod etiketinde ürün KART ID'si değil STOK KODU yazılır (kullanıcı isteği).
    card_no = _hb(str(stock_code or "").strip())
    bar_code = variant.get("barcode") or product.get("barcode") or stock_code
    size = _hb((variant.get("size") or "").strip())
    color = _hb((variant.get("color") or "").strip())

    barcode_svg = _barcode_svg_inline(bar_code)  # bar_code yalnız barkod çizimine gider

    # RENK satırı KALDIRILDI (ürün adı zaten rengi içeriyor). Beden, ürün kart no ile
    # aynı satıra sola alındı → yer açıldı; fontlar büyütüldü (kullanıcı isteği).
    return f"""
    <div class="card">
      <div class="name">{name}</div>
      <div class="row">{f'<span class="cardno">{card_no}</span>' if card_no else ''}{f'<span class="size">{size}</span>' if size else ''}</div>
      {barcode_svg}
      <div class="barcode-text">{_hb(bar_code or '')}</div>
    </div>
    """


def _build_html(cards_html: str, title: str = "Barkod Kartlari") -> str:
    """
    Etiket sayfasi: yan yana 2 barkod, her etiket 5cm x 4cm.
    Kesme payi (bos seritler): sol + orta + sag = 0.5cm -> satir genisligi 11.5cm.
    Barkodlar VEKTOR (SVG) -> net baski. Toolbar'dan kopya adedi secilir.
    """
    # GÜVENLİK: title'a ürün adı (pazaryeri kontrollü) girebiliyor → HTML-escape (stored-XSS)
    import html as _html_t
    title = _html_t.escape(str(title if title is not None else ""))
    LABEL_W = "5cm"
    LABEL_H = "4cm"
    CUT     = "0.5cm"
    SHEET_W = "11.5cm"

    css = """
  @import url('https://fonts.googleapis.com/css2?family=Mulish:wght@400;500;600;700;800&display=swap');
  @page { size: auto; margin: 0; }
  * { box-sizing: border-box; }
  html, body { margin: 0; padding: 0; background: #fff; color: #000;
    font-family: "Mulish", Arial, "Helvetica Neue", Helvetica, sans-serif;
    -webkit-font-smoothing: antialiased; text-rendering: optimizeLegibility; }
  .no-print {
    display: flex; justify-content: space-between; align-items: center; gap: 12px;
    margin: 8px; padding: 8px 12px; background: #fff7ed; border: 1px solid #fed7aa;
    border-radius: 8px; font-size: 13px; flex-wrap: wrap;
  }
  .no-print input { width: 64px; padding: 4px 6px; border: 1px solid #d1d5db; border-radius: 6px; }
  .btn { padding: 6px 16px; border-radius: 6px; border: 1px solid #111;
    background: #111; color: #fff; cursor: pointer; font-size: 13px; font-weight: 700; }

  .sheet { width: __SHEET_W__; margin: 0 auto; padding: __CUT__ __CUT__ 0 __CUT__; }
  .grid { display: grid; grid-template-columns: __LABEL_W__ __LABEL_W__; column-gap: __CUT__; row-gap: 0; }
  .card {
    width: __LABEL_W__; height: __LABEL_H__;
    overflow: hidden; padding: 0.12cm 0.16cm;
    display: flex; flex-direction: column; align-items: flex-start; justify-content: flex-start;
    text-align: left; gap: 1px; page-break-inside: avoid;
  }
  /* İçerik 5x4cm etiketi NEREDEYSE TAMAMEN kaplar (kullanıcı isteği):
     yazılar büyütüldü, barkod etiket genişliğine ölçeklenir. */
  .name { font-size: 15px; font-weight: 800; line-height: 1.1; width: 100%;
    display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
  .cardno { font-size: 13px; font-weight: 700; color: #000; line-height: 1.2; }
  /* Beden ürün kart no'nun YANINDA (sola), aralarında boşluk. Renk satırı yok. */
  .row { display: flex; justify-content: flex-start; align-items: baseline; gap: 10px; width: 100%;
    font-weight: 700; margin: 1px 0; }
  .row .size { font-weight: 800; font-size: 18px; }
  .barcode-svg { display: block; width: 100%; height: auto; margin: 7px 0 0 0; }
  .barcode-text { font-family: "Mulish", Arial, sans-serif; font-weight: 800;
    font-size: 18px; letter-spacing: 0.03em; line-height: 1; margin-top: 4px; }
  @media print { .no-print { display: none; } .sheet { margin: 0; } }
"""
    css = (css.replace("__SHEET_W__", SHEET_W).replace("__LABEL_W__", LABEL_W)
              .replace("__LABEL_H__", LABEL_H).replace("__CUT__", CUT))

    script = (
        "<script>(function(){"
        "var grid=document.querySelector('.grid');"
        "var original=grid?grid.innerHTML:'';"
        "window.doPrint=function(){"
        "var n=parseInt((document.getElementById('copies')||{}).value,10)||1;if(n<1)n=1;"
        "var tmp=document.createElement('div');tmp.innerHTML=original;"
        "var cards=Array.prototype.slice.call(tmp.querySelectorAll('.card'));"
        "grid.innerHTML='';"
        "cards.forEach(function(c){for(var i=0;i<n;i++){grid.appendChild(c.cloneNode(true));}});"
        "window.print();"
        "};})();</script>"
    )

    return (
        "<!DOCTYPE html><html lang='tr'><head><meta charset='utf-8'/>"
        "<title>" + title + "</title><style>" + css + "</style></head><body>"
        "<div class='no-print'>"
        "<span><strong>" + title + "</strong> &middot; %100 olcek / 'Gercek boyut' ile yazdirin.</span>"
        "<span style='display:flex;align-items:center;gap:8px'>"
        "<label>Her barkoddan adet: <input id='copies' type='number' min='1' value='1'/></label>"
        "<button class='btn' onclick='doPrint()'>Yazdir</button>"
        "</span></div>"
        "<div class='sheet'><div class='grid'>" + cards_html + "</div></div>"
        + script +
        "</body></html>"
    )


def _product_cards_html(product: dict, sizes: list | None = None) -> str:
    """
    Bir ürünün varyantları için kart HTML üretir. Varyant yoksa ürün
    seviyesinde tek kart üretilir (stock_code + barcode).
    `sizes` verilirse yalnız o bedenlerdeki varyantlar basılır
    (büyük/küçük harf ve boşluk duyarsız). Boş/None => tüm bedenler.
    """
    variants = product.get("variants") or []
    if not variants:
        # Varyant yoksa ürün seviyesinde tek kart (ana barkod)
        fake = {
            "stock_code": product.get("stock_code"),
            "barcode": product.get("barcode"),
            "size": "",
            "color": "",
        }
        return _card_html_for_variant(product, fake)
    if sizes:
        want = {str(s or "").strip().upper() for s in sizes if str(s or "").strip()}
        if want:
            variants = [v for v in variants
                        if str(v.get("size") or "").strip().upper() in want]
    return "".join(_card_html_for_variant(product, v) for v in variants)


# ---------------------------------------------------------------------------
# ENDPOINT: Tek ürün kartı
# ---------------------------------------------------------------------------
@router.get("/{product_id}/barcode-card")
async def get_product_barcode_card(
    product_id: str,
    token: str = Query(None),
    sizes: str = Query(None, description="Virgülle ayrık beden filtresi (örn. 'S,M'). Boş = tüm bedenler."),
):
    """
    Tek ürün için yazdırılabilir barkod kartı sayfası döner.
    Ürünün her varyantı için ayrı kart; `sizes` verilirse yalnız o bedenler.

    GÜVENLİK: Bu uç window.open ile ?token= üzerinden açılır. Eskiden
    get_current_user (None dönebilen) çağrılıp SONUÇ HİÇ KONTROL EDİLMİYORDU →
    kimliksiz erişilip ürün barkod/stok kodları sızabiliyordu. Artık fatura/
    kargo etiketi ile aynı şekilde verify_admin_token ile ADMIN doğrulanır.
    """
    await verify_admin_token(token)
    product = await db.products.find_one({"id": product_id}, {"_id": 0})
    if not product:
        raise HTTPException(status_code=404, detail="Ürün bulunamadı")

    _sizes = [s for s in (sizes or "").split(",") if s.strip()] or None
    cards = _product_cards_html(product, sizes=_sizes)
    if not cards:
        raise HTTPException(status_code=404, detail="Seçilen bedenlerde varyant bulunamadı")
    html = _build_html(cards, title=f"{product.get('name', 'Ürün')} — Barkod Kartı")
    return Response(content=html, media_type="text/html; charset=utf-8")


# ---------------------------------------------------------------------------
# ENDPOINT: Toplu ürün kartları
# ---------------------------------------------------------------------------
@router.post("/barcode-cards/bulk")
async def get_bulk_barcode_cards(
    payload: dict,
    current_user: dict = Depends(require_admin),
):
    """
    {"ids": [...], "sizes": ["S","M"]} → Gönderilen ürün id'lerinin hepsi için tek
    yazdırılabilir HTML dokümanı. `sizes` verilirse her üründe yalnız o bedenlerin
    kartları basılır; boşsa tüm varyantlar.
    """
    ids = payload.get("ids") or []
    if not ids:
        raise HTTPException(status_code=400, detail="Ürün seçilmedi")
    _sizes = payload.get("sizes") or None
    if isinstance(_sizes, str):
        _sizes = [s for s in _sizes.split(",") if s.strip()]

    cursor = db.products.find({"id": {"$in": ids}}, {"_id": 0})
    products = await cursor.to_list(length=len(ids))

    if not products:
        raise HTTPException(status_code=404, detail="Ürün bulunamadı")

    cards = "".join(_product_cards_html(p, sizes=_sizes) for p in products)
    if not cards:
        raise HTTPException(status_code=404, detail="Seçilen bedenlerde varyant bulunamadı")
    html = _build_html(cards, title=f"{len(products)} Ürün — Barkod Kartları")
    return Response(content=html, media_type="text/html; charset=utf-8")
