"""
Size Table (Ölçü Tablosu) module.

- Admin creates a size table per product: rows=sizes, cols=measurements (cm).
- System renders a 1200x1800 PNG (PIL) and attaches as the product's last image
  with `is_size_table: true`. Storefront hides these but shows the raw data as
  an HTML table under size selector.
"""
from fastapi import APIRouter, HTTPException, Depends
from io import BytesIO
from datetime import datetime, timezone
import base64
import re
import uuid
import os

from PIL import Image, ImageDraw, ImageFont

from .deps import db, require_admin, logger

router = APIRouter(prefix="/size-tables", tags=["size-tables"])


@router.get("/_diag/state")
async def _size_tables_diag(key: str, q: str = ""):
    """GEÇİCİ TANI (gizli anahtarlı): ölçü tablosu veri kaybını teşhis. Kaç kayıt dolu/boş,
    ne zaman ezilmiş (updated_at günü), kaç tanesi kardeş-senkron (synced_from) ile yazılmış,
    ürünlerde kaç adet render edilmiş 'is_size_table' görsel duruyor (görsel-kurtarma kaynağı)."""
    # Teşhis anahtarı YALNIZ env SIZE_DIAG_KEY'den (gömülü fallback kaldırıldı). Env yoksa uç kapalı.
    import hmac as _hmac
    _sk = (os.environ.get("SIZE_DIAG_KEY") or "").strip()
    if not _sk or not _hmac.compare_digest(str(key or ""), _sk):
        raise HTTPException(status_code=403, detail="forbidden")
    # İSİMLE TEK ÜRÜN DÖKÜMÜ: kullanıcının "boş" dediği ürünü doğrulamak için.
    if q:
        out = []
        async for p in db.products.find(
                {"name": {"$regex": re.escape(q), "$options": "i"}, "is_deleted": {"$ne": True}},
                {"_id": 0, "id": 1, "name": 1, "stock_code": 1}).limit(12):
            own = await db.size_tables.find_one({"product_id": p["id"]}, {"_id": 0})
            inh = None if (own and own.get("sizes")) else await _inherited_size_table(p["id"])
            src = own if (own and own.get("sizes")) else inh
            out.append({
                "id": p["id"], "name": p.get("name", ""), "stock_code": p.get("stock_code", ""),
                "own_row": bool(own), "own_has_data": bool(own and own.get("sizes")),
                "inherited": bool(inh and inh.get("sizes")),
                "resolved_sizes": (src or {}).get("sizes", []),
                "resolved_columns": (src or {}).get("columns", []),
                "has_values": bool((src or {}).get("values")),
                "model_info_keys": list(((own or {}).get("model_info") or {}).keys()),
                "product_size": (own or {}).get("product_size", ""),
            })
        return {"query": q, "matches": out}
    total = await db.size_tables.count_documents({})
    non_empty = await db.size_tables.count_documents({"sizes": {"$exists": True, "$ne": []}})
    empty = await db.size_tables.count_documents({"$or": [{"sizes": {"$exists": False}}, {"sizes": []}]})
    synced = await db.size_tables.count_documents({"synced_from": {"$exists": True, "$ne": ""}})
    with_model = await db.size_tables.count_documents({"model_info": {"$exists": True, "$nin": [{}, None]}})
    # updated_at gün dağılımı (son ezme dalgasını yakala)
    by_day = {}
    async for st in db.size_tables.find({}, {"_id": 0, "updated_at": 1}).limit(20000):
        d = str(st.get("updated_at") or "")[:10]
        by_day[d] = by_day.get(d, 0) + 1
    # Ürünlerde render edilmiş ölçü-tablosu görseli (veri kaybında görselden kurtarma kaynağı)
    img_products = await db.products.count_documents({"images": {"$elemMatch": {"is_size_table": True}}})
    # Boş kayıt örnekleri (ürün adı + ne zaman + synced_from)
    empties = []
    async for st in db.size_tables.find(
            {"$or": [{"sizes": {"$exists": False}}, {"sizes": []}]},
            {"_id": 0, "product_id": 1, "updated_at": 1, "synced_from": 1}).limit(8):
        pr = await db.products.find_one({"id": st.get("product_id")}, {"_id": 0, "name": 1, "stock_code": 1})
        st["_name"] = (pr or {}).get("name", "")
        st["_stock_code"] = (pr or {}).get("stock_code", "")
        empties.append(st)
    # Son güncellenen 12 satırın İÇERİK özeti (bozulma/eksilme var mı görmek için)
    recent = []
    async for st in db.size_tables.find({}, {"_id": 0}).sort("updated_at", -1).limit(12):
        pr = await db.products.find_one({"id": st.get("product_id")}, {"_id": 0, "name": 1})
        _vals = st.get("values") or {}
        recent.append({
            "product_id": st.get("product_id"), "name": (pr or {}).get("name", "")[:34],
            "updated_at": str(st.get("updated_at"))[:19],
            "n_sizes": len(st.get("sizes") or []), "n_cols": len(st.get("columns") or []),
            "n_value_rows": len(_vals), "synced_from": st.get("synced_from", ""),
            "has_model_info": bool(st.get("model_info")), "product_size": (st.get("product_size") or "")[:20],
            "sizes": (st.get("sizes") or [])[:8], "columns": (st.get("columns") or [])[:8],
        })
    # GERÇEK KAYIP ÖLÇÜSÜ: render edilmiş ölçü-tablosu GÖRSELİ olan (kesinlikle tablo girilmiş)
    # ama get_size_table mantığıyla ŞU AN BOŞ dönen (kendi satırı yok/boş VE kardeşte de yok) ürünler.
    had_img_but_empty = 0
    lost_samples = []
    async for p in db.products.find(
            {"images": {"$elemMatch": {"is_size_table": True}}, "is_deleted": {"$ne": True}},
            {"_id": 0, "id": 1, "name": 1, "stock_code": 1}).limit(5000):
        own = await db.size_tables.find_one(
            {"product_id": p["id"], "sizes": {"$exists": True, "$ne": []}}, {"_id": 1})
        if own:
            continue
        inh = await _inherited_size_table(p["id"])
        if inh and inh.get("sizes"):
            continue
        had_img_but_empty += 1
        if len(lost_samples) < 12:
            lost_samples.append({"id": p["id"], "name": (p.get("name") or "")[:34],
                                 "stock_code": p.get("stock_code", "")})
    return {
        "size_tables_total": total, "non_empty": non_empty, "empty": empty,
        "synced_from_count": synced, "with_model_info": with_model,
        "products_with_rendered_size_image": img_products,
        "had_image_but_now_empty": had_img_but_empty,
        "lost_samples": lost_samples,
        "updated_at_by_day": dict(sorted(by_day.items())),
        "empty_samples": empties,
        "recent_rows": recent,
    }


def _find_font(size=28, bold=False):
    # ÖNCE depoya gömülü font (Railway imajında sistem fontu YOK — bitmap fallback'e
    # düşünce yazı minicik ve Türkçe karakterler kutu çıkıyordu).
    _fname = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
    _bundled = os.path.join(os.path.dirname(__file__), "..", "assets", "fonts", _fname)
    candidates = [
        _bundled,
        f"/usr/share/fonts/truetype/dejavu/{_fname}",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    ]
    for c in candidates:
        if os.path.exists(c):
            try:
                return ImageFont.truetype(c, size)
            except Exception:
                continue
    try:
        return ImageFont.load_default(size=size)  # Pillow ≥10.1: ölçeklenebilir varsayılan
    except Exception:
        return ImageFont.load_default()


def _tr_title(s: str) -> str:
    """Ölçü etiketini Türkçe kurala göre Baş Harfleri Büyük yazar (omuz→Omuz,
    göğüs→Göğüs, kol boyu→Kol Boyu). Türkçe i/ı ayrımı korunur."""
    out = []
    for w in str(s or "").split():
        first = {"i": "İ", "ı": "I"}.get(w[0], w[0].upper())
        rest = w[1:].replace("I", "ı").replace("İ", "i").lower()
        out.append(first + rest)
    return " ".join(out)


def _clean_label(s: str) -> str:
    """Yaygın yazım hatalarını düzelt + Türkçe baş-harf büyüt (görsel/tablo etiketi)."""
    return _tr_title(re.sub(r"(?i)boyuu", "boyu", str(s or "")))


def _normalize_table_labels(columns, values):
    """Kolon etiketlerini _clean_label'dan geçirir, values sözlüğünün kolon
    anahtarlarını yeni ada taşır. Düzeltme sonrası çakışan kolonlar teke iner
    (dolu değer korunur). İdempotent."""
    rename, new_cols, seen = {}, [], set()
    for c in (columns or []):
        nc = _clean_label(c) or str(c)
        rename[str(c)] = nc
        if nc not in seen:
            seen.add(nc)
            new_cols.append(nc)
    new_values = {}
    for size, m in (values or {}).items():
        nm = {}
        for k, v in (m if isinstance(m, dict) else {}).items():
            nk = rename.get(str(k), str(k))
            if nk not in nm or (str(v).strip() and not str(nm.get(nk, "")).strip()):
                nm[nk] = v
        new_values[size] = nm
    return new_cols, new_values


def render_size_table_image(
    product_name: str,
    sizes: list,          # e.g. ["S", "M", "L", "XL"]
    columns: list,        # e.g. ["Göğüs", "Bel", "Kalça", "Omuz"]
    values: dict,         # {"S": {"Göğüs": "96", "Bel": "80", ...}, ...}
    brand: str = "FACETTE",
    unit: str = "cm",
    product_size: str = "",
    model_info: dict = None,
    product_image: bytes = None,
) -> bytes:
    """SADE ŞABLON (kullanıcının verdiği örnek birebir): beyaz zemin üzerinde
    1) üstte ürünün İLK görseli (ortalı), 2) altında ortalı büyük ürün adı,
    3) altında kutusuz/çizgisiz ferah tablo — 'Bedenler  S  M  L' başlık satırı,
    her ölçü ayrı satırda (etiket solda, değerler beden kolonlarının altında ortalı).
    Manken silüeti, başlık bandı ve marka damgası kaldırıldı."""
    # SEÇİLEN TASARIM: "C — Hairline Çizgili" (kullanıcı seçimi):
    # bold beden başlıkları, gri ölçü etiketleri, satır aralarında kıl inceliğinde çizgiler.
    W, H = 1200, 1800
    img = Image.new("RGB", (W, H), "white")
    draw = ImageDraw.Draw(img)

    INK = (24, 24, 27)
    GRAY = (120, 120, 128)
    LINE = (228, 228, 232)
    font_name = _find_font(52)
    font_head_b = _find_font(32, bold=True)
    font_label = _find_font(29)
    font_cell = _find_font(31)

    rows = [str(c) for c in (columns or [])]
    n_rows = len(rows)

    y = 50
    # 1) Ürün görseli — oran korunarak sığdırılır; tablo satır sayısına göre yükseklik ayarlanır
    if product_image:
        try:
            pim = Image.open(BytesIO(product_image)).convert("RGB")
            _needed_below = 175 + (n_rows + 1) * 84 + 60  # ad + tablo satırları + alt boşluk
            max_h = max(480, H - y - _needed_below)
            max_w = 680
            r = min(max_w / pim.width, max_h / pim.height)
            pim = pim.resize((max(1, int(pim.width * r)), max(1, int(pim.height * r))))
            img.paste(pim, ((W - pim.width) // 2, y))
            y += pim.height + 55
        except Exception:
            y += 10
    else:
        y += 10

    # 2) Ürün adı — ortalı
    name = (product_name or "").strip()[:60]
    tw = draw.textlength(name, font=font_name)
    draw.text(((W - tw) / 2, y), name, fill=INK, font=font_name)
    y += 120

    # 3) Tablo — sol etiket + beden kolonları; her ölçü satırının ÜSTÜNDE ince çizgi
    label_x = 90
    col_area_l, col_area_r = 430, W - 90
    n_sizes = max(1, len(sizes))
    col_w = (col_area_r - col_area_l) / n_sizes

    def _center_x(i, text, font):
        cx0 = col_area_l + i * col_w + col_w / 2
        return cx0 - draw.textlength(text, font=font) / 2

    remaining = H - y - 60
    row_gap = max(76, min(110, int(remaining / max(1, n_rows + 1))))

    draw.text((label_x, y), "Bedenler", fill=INK, font=font_head_b)
    for i, s in enumerate(sizes):
        t = str(s)
        draw.text((_center_x(i, t, font_head_b), y), t, fill=INK, font=font_head_b)
    y += row_gap

    for col in rows:
        draw.line([(label_x, y - 14), (W - 90, y - 14)], fill=LINE, width=1)
        draw.text((label_x, y), _clean_label(col)[:22], fill=GRAY, font=font_label)
        for i, s in enumerate(sizes):
            val = str(values.get(s, {}).get(col, "")).strip() or "-"
            val = val.replace(".", ",")  # ondalıklar virgülle (70,5)
            draw.text((_center_x(i, val, font_cell), y), val, fill=INK, font=font_cell)
        y += row_gap

    out = BytesIO()
    img.save(out, format="JPEG", quality=90, optimize=True)  # 1200x1800 JPEG
    return out.getvalue()


async def _inherited_size_table(product_id: str):
    """Kendi ölçü tablosu olmayan ürün için, AYNI stok kodlu başka bir üründe
    kayıtlı (dolu) ölçü tablosunu döndürür. Stok kodu boşsa / kardeş yoksa None.
    En son güncellenen kardeş tabloyu seçer."""
    prod = await db.products.find_one({"id": product_id}, {"_id": 0, "id": 1, "stock_code": 1})
    if not prod:
        return None
    sc = (prod.get("stock_code") or "").strip()
    if not sc:
        return None
    sibling_ids = []
    async for p in db.products.find({"stock_code": sc, "id": {"$ne": product_id}}, {"_id": 0, "id": 1}):
        if p.get("id"):
            sibling_ids.append(p["id"])
    if not sibling_ids:
        return None
    return await db.size_tables.find_one(
        {"product_id": {"$in": sibling_ids}, "sizes": {"$exists": True, "$ne": []}},
        {"_id": 0},
        sort=[("updated_at", -1)],
    )


@router.get("/{product_id}")
async def get_size_table(product_id: str, current_user: dict = Depends(require_admin)):
    st = await db.size_tables.find_one({"product_id": product_id}, {"_id": 0})
    if not st:
        # Kalıtım: aynı stok kodlu başka üründe tablo varsa otomatik getir (oto-doldurma).
        # exists=True döner ki editör doldursun; admin "Kaydet" deyince bu ürüne kalıcı yazılır.
        inh = await _inherited_size_table(product_id)
        if inh and inh.get("sizes"):
            return {
                "product_id": product_id,
                "sizes": inh.get("sizes") or [],
                "columns": inh.get("columns") or [],
                "values": inh.get("values") or {},
                "exists": True,
                "inherited": True,
                "inherited_from": inh.get("product_id"),
            }
        return {"product_id": product_id, "sizes": [], "columns": [], "values": {}, "exists": False}
    st["exists"] = True
    return st


@router.get("/{product_id}/sibling-model-info")
async def sibling_model_info(product_id: str, current_user: dict = Depends(require_admin)):
    """'Diğer renkten getir' butonu: aynı stok kodlu renk kardeşlerinden DOLU manken (model_info)
    + ürün bedenini döndürür (en son güncellenen). Bu ürüne HİÇBİR ŞEY YAZMAZ — sadece döndürür;
    admin editörde uygulayıp kaydeder. Böylece her rengin kendi mankeni korunur, otomatik ezme olmaz."""
    prod = await db.products.find_one({"id": product_id}, {"_id": 0, "stock_code": 1})
    sc = str((prod or {}).get("stock_code") or "").strip()
    empty = {"found": False, "model_info": {}, "product_size": ""}
    if not sc:
        return empty
    sibling_ids = []
    async for p in db.products.find(
            {"stock_code": sc, "id": {"$ne": product_id}, "is_deleted": {"$ne": True}}, {"_id": 0, "id": 1}):
        if p.get("id"):
            sibling_ids.append(p["id"])
    if not sibling_ids:
        return empty
    st = await db.size_tables.find_one(
        {"product_id": {"$in": sibling_ids}, "model_info": {"$exists": True, "$nin": [None, {}]}},
        {"_id": 0, "model_info": 1, "product_size": 1, "product_id": 1},
        sort=[("updated_at", -1)],
    )
    mi = (st or {}).get("model_info") or {}
    # tümü boş string ise "bulunamadı" say
    if not any(str(v).strip() for v in mi.values()):
        return empty
    return {"found": True, "model_info": mi,
            "product_size": (st or {}).get("product_size") or "", "from": (st or {}).get("product_id")}


@router.post("/maintenance/fix-labels")
async def fix_size_table_labels(current_user: dict = Depends(require_admin)):
    """Tüm ölçü tablolarında kolon etiketlerini düzeltir (boyuu→boyu + Türkçe
    baş-harf büyütme) ve values anahtarlarını yeni ada taşır. İdempotent."""
    fixed = scanned = 0
    async for st in db.size_tables.find({}):
        scanned += 1
        cols = st.get("columns") or []
        new_cols, new_values = _normalize_table_labels(cols, st.get("values") or {})
        if new_cols == [str(c) for c in cols] and new_values == (st.get("values") or {}):
            continue
        await db.size_tables.update_one(
            {"_id": st["_id"]},
            {"$set": {"columns": new_cols, "values": new_values}},
        )
        fixed += 1
    return {"scanned": scanned, "fixed": fixed}


@router.post("/{product_id}")
async def save_size_table(product_id: str, payload: dict, current_user: dict = Depends(require_admin)):
    sizes = payload.get("sizes", [])
    columns = payload.get("columns", [])
    values = payload.get("values", {})
    if not isinstance(sizes, list) or not isinstance(columns, list):
        raise HTTPException(status_code=400, detail="sizes ve columns liste olmalı")

    # Etiketleri kayıtta normalize et (baş harfler büyük + yazım düzeltmesi) —
    # storefront tablosu ve görsel aynı temiz etiketi kullansın.
    columns, values = _normalize_table_labels(columns, values)

    doc = {
        "product_id": product_id,
        "sizes": sizes,
        "columns": columns,
        "values": values,
        # "suud" örneği: Ürün Bedeni + Manken ölçüleri (Boy/Göğüs/Bel/Basen ...)
        "product_size": str(payload.get("product_size") or "").strip(),
        "model_info": payload.get("model_info") if isinstance(payload.get("model_info"), dict) else {},
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "updated_by": current_user.get("email", ""),
    }
    await db.size_tables.update_one({"product_id": product_id}, {"$set": doc}, upsert=True)

    # RENK KARDEŞİ SENKRONU: ÖLÇÜ TABLOSU (beden×ölçü) ürün düzeyindedir → aynı stok kodlu diğer
    # renk kartlarına yansıtılır. ANCAK manken (model_info) + ürün bedeni (product_size) RENK/MANKEN
    # ÖZELDİR (her rengin farklı mankeni olabilir) → kardeşlere KOPYALANMAZ; her renk kendi manken
    # bilgisini korur/manuel girer. (Kullanıcı isteği: aynı manken bilgisinin tüm renklere yansıması
    # bugını giderildi.)
    synced = 0
    try:
        p = await db.products.find_one({"id": product_id}, {"_id": 0, "stock_code": 1})
        sc = str((p or {}).get("stock_code") or "").strip()
        if sc:
            async for s in db.products.find(
                    {"stock_code": sc, "id": {"$ne": product_id}, "is_deleted": {"$ne": True}},
                    {"_id": 0, "id": 1}):
                # YALNIZ paylaşılan ölçü tablosu alanları $set edilir; model_info/product_size
                # kardeşte OLDUĞU GİBİ kalır (dokunulmaz).
                sib_set = {
                    "product_id": s["id"],
                    "sizes": sizes,
                    "columns": columns,
                    "values": values,
                    "synced_from": product_id,
                    "updated_at": doc["updated_at"],
                    "updated_by": doc["updated_by"],
                }
                await db.size_tables.update_one({"product_id": s["id"]}, {"$set": sib_set}, upsert=True)
                synced += 1
    except Exception as e:
        logger.error(f"[ölçü tablosu kardeş senkron {product_id}] {e}")
    return {"success": True, "synced_siblings": synced}


@router.post("/{product_id}/generate-image")
async def generate_size_table_image(product_id: str, current_user: dict = Depends(require_admin)):
    """Render a 1200x1800 PNG, store as base64 in the product's images array
    marked `is_size_table=true`, and return the data URL."""
    st = await db.size_tables.find_one({"product_id": product_id}, {"_id": 0})
    if not st:
        st = await _inherited_size_table(product_id)  # aynı stok kodundan kalıtım
    if not st:
        raise HTTPException(status_code=404, detail="Önce ölçü tablosunu kaydedin")
    product = await db.products.find_one({"id": product_id}, {"_id": 0})
    if not product:
        raise HTTPException(status_code=404, detail="Ürün bulunamadı")

    # Şablonun üst yarısı için ürünün İLK gerçek görselini indir (ölçü tablosu görselleri atlanır)
    _img_bytes = None
    _tries = 0
    for _im in (product.get("images") or []):
        if _tries >= 3 or _img_bytes:
            break
        _u = _im.get("url") if isinstance(_im, dict) else _im
        if isinstance(_im, dict) and _im.get("is_size_table"):
            continue
        if not isinstance(_u, str) or not _u or _u.startswith("data:"):
            continue
        if not _u.startswith("http"):
            _u = "https://api.facette.com.tr" + (_u if _u.startswith("/") else "/" + _u)
        _tries += 1
        try:
            import httpx as _httpx
            async with _httpx.AsyncClient(timeout=25, follow_redirects=True) as _c:
                _r = await _c.get(_u)
            if _r.status_code == 200 and _r.content:
                _img_bytes = _r.content
        except Exception as _e:
            logger.warning(f"size-table: ürün görseli indirilemedi ({_u}): {_e}")

    png = render_size_table_image(
        product_name=product.get("name", ""),
        sizes=st.get("sizes") or [],
        columns=st.get("columns") or [],
        values=st.get("values") or {},
        product_size=st.get("product_size") or "",
        model_info=st.get("model_info") or {},
        product_image=_img_bytes,
    )
    data_url = "data:image/jpeg;base64," + base64.b64encode(png).decode("ascii")

    # Görseli CDN'e (R2) yükle — pazaryerlerine SON görsel olarak gönderilebilmesi için
    # gerçek URL gerekir (data: URL pazaryerine gidemez). R2 kapalıysa data_url'e düşülür.
    stored_url = data_url
    try:
        from services import r2_storage as r2
        if r2.is_enabled():
            _key = f"products/{product_id}/size-table-{uuid.uuid4().hex[:8]}.jpg"
            stored_url = r2.put_object(_key, png, "image/jpeg")
    except Exception as _e:
        logger.warning(f"size-table R2 upload başarısız, base64'e düşüldü: {_e}")

    # Remove any previous size-table images and append fresh one as the last image
    imgs = list(product.get("images") or [])
    imgs = [i for i in imgs if not (isinstance(i, dict) and i.get("is_size_table"))]
    imgs.append({
        "id": str(uuid.uuid4()),
        "url": stored_url,
        "is_size_table": True,
        "alt": "Ölçü Tablosu",
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    await db.products.update_one(
        {"id": product_id},
        {"$set": {"images": imgs, "updated_at": datetime.now(timezone.utc).isoformat()}}
    )
    await db.size_tables.update_one(
        {"product_id": product_id},
        {"$set": {"last_rendered_at": datetime.now(timezone.utc).isoformat()}}
    )
    return {"success": True, "data_url": data_url, "image_bytes": len(png)}


# ---------------------------------------------------------------------------
# TEK SEFERLİK ONARIM: ölçü tablosu görsellerinin kaybolan is_size_table işareti.
# Bir görsel taşıma/dönüştürme işlemi {url, is_size_table:true} dict'lerini düz URL
# string'ine çevirmiş → tablo görselleri müşteri galerisine sızdı ve "Beden Tablosu"
# butonu kayboldu. Tespit (gerçek veriyle doğrulandı): tablo görselleri 1200×1800 ve
# DÖRT kenar şeridi SAF BEYAZ (mean=255) — ürün fotoğrafları (stüdyo fonu) asla değil.
# Yalnız /uploads/ string URL'leri taranır; kapak (index 0) asla işaretlenmez.
# ---------------------------------------------------------------------------

def _is_chart_image_sync(url: str) -> bool:
    import requests as _req
    from PIL import ImageStat
    r = _req.get(url, timeout=25)
    r.raise_for_status()
    im = Image.open(BytesIO(r.content))
    if im.size != (1200, 1800):
        return False
    g = im.convert("L")
    w, h = g.size
    b = 25
    for box in [(0, 0, w, b), (0, h - b, w, h), (0, 0, b, h), (w - b, 0, w, h)]:
        if ImageStat.Stat(g.crop(box)).mean[0] < 254.0:
            return False
    return True


async def repair_size_table_markers():
    """Startup'ta bir kez çalışır (settings bayrağıyla korunur)."""
    import asyncio
    flag = await db.settings.find_one({"id": "size_table_marker_repair"}, {"_id": 0})
    if flag and flag.get("done"):
        return
    scanned = marked = prods = 0
    cursor = db.products.find(
        {"images": {"$elemMatch": {"$regex": "/uploads/"}}},
        {"_id": 0, "id": 1, "images": 1},
    )
    async for p in cursor:
        imgs = p.get("images") or []
        new_imgs, changed = [], False
        for i, im in enumerate(imgs):
            if i > 0 and isinstance(im, str) and "/uploads/" in im:
                scanned += 1
                try:
                    is_chart = await asyncio.to_thread(_is_chart_image_sync, im)
                except Exception:
                    is_chart = False
                if is_chart:
                    new_imgs.append({
                        "id": str(uuid.uuid4()), "url": im, "is_size_table": True,
                        "alt": "Ölçü Tablosu",
                        "created_at": datetime.now(timezone.utc).isoformat(),
                    })
                    changed = True
                    marked += 1
                    continue
            new_imgs.append(im)
        if changed:
            await db.products.update_one({"id": p["id"]}, {"$set": {"images": new_imgs}})
            prods += 1
    await db.settings.update_one(
        {"id": "size_table_marker_repair"},
        {"$set": {"done": True, "scanned": scanned, "marked": marked, "products": prods,
                  "at": datetime.now(timezone.utc).isoformat()}},
        upsert=True,
    )
    logger.info(f"[size-table repair] tarandı={scanned} işaretlendi={marked} ürün={prods}")


# Public endpoint for storefront – no auth
public_router = APIRouter(prefix="/size-tables-public", tags=["size-tables-public"])


@public_router.get("/{product_id}")
async def get_public_size_table(product_id: str):
    """Storefront reads the HTML-renderable data (NOT the image)."""
    own = await db.size_tables.find_one({"product_id": product_id}, {"_id": 0})
    st = own if (own and own.get("sizes")) else await _inherited_size_table(product_id)  # tablo kalıtımı
    if not st or not st.get("sizes"):
        return {"exists": False}
    # model_info + product_size RENK/MANKEN özeldir → YALNIZ ürünün KENDİ tablosundan gösterilir.
    # Tablo başka renkten kalıtımla geldiyse manken bilgisi BOŞ döner (yanlış manken sızmasın).
    _mi = (own.get("model_info") or {}) if own else {}
    _ps = (own.get("product_size") or "") if own else ""
    return {
        "exists": True,
        "sizes": st.get("sizes") or [],
        "columns": st.get("columns") or [],
        "values": st.get("values") or {},
        "product_size": _ps,
        "model_info": _mi,
    }
