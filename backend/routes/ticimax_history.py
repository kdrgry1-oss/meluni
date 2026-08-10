"""
ticimax_history.py — Ticimax GEÇMİŞ VERİ okuma/kurtarma uçları.

A4 kararıyla tüm Ticimax senkron router'ları kapatıldı (stok/katalog otoritesi
artık kendi sistemimiz). Bu modül YALNIZ geçmişe dönük veri kurtarma içindir:
  - GET  /admin/ticimax-history/orders/probe     — canlı WS şema örneği (admin)
  - POST /admin/ticimax-history/orders/backfill  — geçmiş sipariş aktarımı

Backfill kuralları (kullanıcı şartı — bozma):
  - STOK DÜŞÜMÜ YAPILMAZ, bildirim/mail/push tetiklenmez.
  - Var olan sipariş (order_number / ticimax_order_id) ATLANIR, güncellenmez.
  - Pazaryeri kaynaklı satırlar platform etiketiyle yazılır (raporlar doğru kırılır).
"""
from fastapi import APIRouter, Depends, Body
from datetime import datetime, timezone
from typing import Optional, Dict, List
import asyncio
import sys
import os

_BACKEND_PATH = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_PATH not in sys.path:
    sys.path.insert(0, _BACKEND_PATH)

from .deps import db, logger, require_admin, generate_id

router = APIRouter(prefix="/admin/ticimax-history", tags=["admin-ticimax-history"])


def _deep(o, depth=0):
    """Zeep nesnesini JSON'a çevrilebilir yapıya derinlemesine aç."""
    if depth > 8:
        return str(o)
    if o is None or isinstance(o, (str, int, float, bool)):
        return o
    if isinstance(o, (list, tuple)):
        return [_deep(x, depth + 1) for x in o]
    if hasattr(o, "__values__"):
        try:
            return {k: _deep(v, depth + 1) for k, v in dict(o.__values__).items()}
        except Exception:
            return str(o)
    if isinstance(o, dict):
        return {k: _deep(v, depth + 1) for k, v in o.items()}
    try:
        from datetime import datetime as _dt, date as _d
        if isinstance(o, (_dt, _d)):
            return o.isoformat()
    except Exception:
        pass
    try:
        from decimal import Decimal as _Dec
        if isinstance(o, _Dec):
            return float(o)
    except Exception:
        pass
    return str(o)


# Eski mağaza verileri artık bu domainde (facette.com.tr YENİ siteyi gösteriyor;
# Railway'deki TICIMAX_DOMAIN env'i eski günlerden www.facette.com.tr kalmış olabilir —
# history modülü her çağrıda domaini ZORLA doğru adrese çevirir).
HISTORY_DOMAIN_DEFAULT = "facette.ticimaxeticaret.com"


async def _api_key() -> str:
    s = await db.settings.find_one({"id": "ticimax"}) or {}
    return s.get("api_key") or "AKG0M8DTRSEBAIA898JA6HW22EDIU3"


async def _ensure_domain():
    from ticimax_client import set_domain
    s = await db.settings.find_one({"id": "ticimax"}) or {}
    set_domain(s.get("history_domain") or HISTORY_DOMAIN_DEFAULT)


_SIZE_PAT = None


def _looks_size(v: str) -> bool:
    import re as _re
    global _SIZE_PAT
    if _SIZE_PAT is None:
        _SIZE_PAT = _re.compile(
            r"^(xxs|xs|s|m|l|xl|xxl|3xl|4xl|5xl|xs/s|s/m|m/l|l/xl|std|standart|tek beden|\d{2}(-\d{2})?)$",
            _re.I)
    return bool(_SIZE_PAT.match((v or "").strip()))


def _parse_order(o: dict) -> Optional[dict]:
    """Ticimax WebSiparis (deep-serialize edilmiş) → db.orders dokümanı.
    Canlı şemadan doğrulanan alanlar: SiparisNo, SiparisTarihi, SiparisKaynagi,
    OdenenTutar (KDV dahil), ToplamTutar (KDV hariç), Urunler.WebSiparisUrun[]
    (Barkod/UrunAdi/Adet/Tutar+KdvTutari), TeslimatAdresi, SiparisDurumu."""
    import re as _re
    sip_no = str(o.get("SiparisNo") or "").strip()
    if not sip_no:
        return None
    kaynak = str(o.get("SiparisKaynagi") or "").strip().lower()
    platform = None
    if "trendyol" in kaynak:
        platform = "trendyol"
    elif "hepsiburada" in kaynak:
        platform = "hepsiburada"
    # Trendyol siparişi Ticimax'ta 'TY-<tyNo>_<paket>' — bizde TY numarasıyla durur.
    order_number = sip_no
    m = _re.match(r"^TY-(\d+)_", sip_no)
    if platform == "trendyol" and m:
        order_number = m.group(1)

    durum = str(o.get("SiparisDurumu") or "").lower()
    if "iptal" in durum:
        status = "cancelled"
    elif "iade" in durum:
        status = "returned"
    elif "teslim" in durum:
        status = "delivered"
    elif "kargo" in durum:
        status = "shipped"
    else:
        status = "confirmed"

    items_raw = o.get("Urunler") or {}
    if isinstance(items_raw, dict):
        items_raw = items_raw.get("WebSiparisUrun") or []
    if not isinstance(items_raw, list):
        items_raw = [items_raw] if items_raw else []
    items = []
    for it in items_raw:
        if not isinstance(it, dict):
            continue
        qty = int(float(it.get("Adet") or 1) or 1)
        line_net = float(it.get("Tutar") or 0)          # KDV hariç satır toplamı
        line_kdv = float(it.get("KdvTutari") or 0)
        unit_paid = round((line_net + line_kdv) / max(1, qty), 2)  # KDV dahil birim
        size = color = ""
        eks = it.get("EkSecenekList") or {}
        if isinstance(eks, dict):
            eks = eks.get("WebSiparisUrunEkSecenekOzellik") or []
        for e in (eks if isinstance(eks, list) else []):
            t = str((e or {}).get("Tanim") or "").strip()
            if not t:
                continue
            if _looks_size(t):
                size = size or t
            else:
                color = color or t
        items.append({
            "product_id": None,
            "product_name": it.get("UrunAdi") or "",
            "name": it.get("UrunAdi") or "",
            "barcode": str(it.get("Barkod") or "").strip(),
            "stock_code": it.get("StokKodu") or "",
            "quantity": qty,
            "price": unit_paid,
            "unit_price": unit_paid,
            "vat_rate": float(it.get("KdvOrani") or 10),
            "size": size,
            "color": color,
        })

    ta = o.get("TeslimatAdresi") or {}
    alici = str(ta.get("AliciAdi") or o.get("AdiSoyadi") or "").strip()
    parts = alici.split()
    first_name = " ".join(parts[:-1]) if len(parts) > 1 else alici
    last_name = parts[-1] if len(parts) > 1 else ""

    paid_total = float(o.get("OdenenTutar") or o.get("SiparisToplamTutari") or 0)
    return {
        "order_number": order_number,
        "ticimax_siparis_no": sip_no,
        "ticimax_order_id": int(o.get("ID") or 0),
        **({"platform": platform} if platform else {}),
        "items": items,
        "shipping_address": {
            "first_name": first_name, "last_name": last_name,
            "phone": str(ta.get("AliciTelefon") or "").replace("None", ""),
            "email": str(o.get("Mail") or ""),
            "address": str(ta.get("Adres") or ""),
            "city": str(ta.get("Il") or ""),
            "district": str(ta.get("Ilce") or ""),
        },
        "subtotal": float(o.get("ToplamTutar") or 0) + float(o.get("ToplamKdv") or 0),
        "shipping_cost": float(o.get("KargoTutari") or 0),
        "discount_amount": float(o.get("IndirimTutari") or 0),
        "total": paid_total,
        "payment_method": "marketplace" if platform else "ticimax",
        # Tarihsel kayıt aktarımı: ödeme durumu KAYNAK sistemin verisinden gelir
        # (istemci girdisi değil) — canlı ödeme akışı değişmezlerini etkilemez.
        "payment_status": "paid" if paid_total > 0 else "pending",
        "status": status,
        "cargo_provider_name": o.get("KargoFirmaTanim") or "",
        "cargo_tracking_number": str(o.get("KargoTakipNo") or "").replace("None", ""),
        "invoice_number": str(o.get("FaturaNo") or "").replace("None", ""),
        "created_at": str(o.get("SiparisTarihi") or ""),
        "backfill": True,
        "imported_from": "ticimax_history",
        "imported_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


@router.post("/orders/backfill")
async def ticimax_orders_backfill(payload: dict = Body(...), current_user: dict = Depends(require_admin)):
    """Ticimax'tan geçmiş siparişleri ARKA PLANDA aktarır.
    payload: {"start_date":"YYYY-MM-DD","end_date":"YYYY-MM-DD","dry_run":false,
              "include_marketplace":true}
    KURALLAR: STOK DÜŞÜMÜ YOK, bildirim YOK, var olan sipariş ATLANIR.
    Durum: GET /admin/ticimax-history/orders/backfill/status"""
    start_date = str(payload.get("start_date") or "").strip()
    end_date = str(payload.get("end_date") or "").strip()
    dry = bool(payload.get("dry_run"))
    inc_mp = payload.get("include_marketplace", True)
    if not start_date or not end_date:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail="start_date/end_date zorunlu (YYYY-MM-DD)")
    key = await _api_key()
    await _ensure_domain()
    await db.settings.update_one(
        {"id": "ticimax_order_backfill"},
        {"$set": {"status": "running", "range": f"{start_date}..{end_date}", "dry_run": dry,
                  "started_at": datetime.now(timezone.utc).isoformat()},
         "$unset": {"result": "", "error": ""}},
        upsert=True)

    async def _run():
        from ticimax_client import get_orders as tc_get_orders, set_live
        scanned = imported = skipped = errors = 0
        set_live(True)
        try:
            page = 1
            while page <= 200:
                try:
                    orders = await asyncio.to_thread(
                        tc_get_orders, page, 100, key, start_date, end_date,
                        not inc_mp, False)
                except Exception as e:
                    logger.error(f"[tcx backfill] page {page}: {e}")
                    errors += 1
                    break
                if not orders:
                    break
                for zo in orders:
                    scanned += 1
                    try:
                        doc = _parse_order(_deep(zo))
                        if not doc:
                            continue
                        _ors = [{"order_number": doc["order_number"]},
                                {"order_number": doc["ticimax_siparis_no"]}]
                        if doc.get("ticimax_order_id"):
                            _ors.append({"ticimax_order_id": doc["ticimax_order_id"]})
                        if await db.orders.find_one({"$or": _ors}, {"_id": 1}):
                            skipped += 1
                            continue
                        if dry:
                            imported += 1
                            continue
                        doc["id"] = generate_id()
                        doc["user_id"] = None
                        await db.orders.insert_one(doc)
                        imported += 1
                    except Exception as e:
                        errors += 1
                        logger.error(f"[tcx backfill] order err: {e}")
                await db.settings.update_one(
                    {"id": "ticimax_order_backfill"},
                    {"$set": {"progress": {"page": page, "scanned": scanned,
                                           "imported": imported, "skipped": skipped}}})
                page += 1
                await asyncio.sleep(13)  # Ticimax rate limit (12 sn)
            await db.settings.update_one(
                {"id": "ticimax_order_backfill"},
                {"$set": {"status": "done",
                          "result": {"scanned": scanned, "imported": imported,
                                     "skipped_existing": skipped, "errors": errors},
                          "finished_at": datetime.now(timezone.utc).isoformat()}})
        except Exception as e:
            logger.error(f"[tcx backfill] fatal: {e}")
            await db.settings.update_one(
                {"id": "ticimax_order_backfill"},
                {"$set": {"status": "error", "error": str(e)[:400],
                          "finished_at": datetime.now(timezone.utc).isoformat()}})
        finally:
            set_live(False)

    asyncio.create_task(_run())
    return {"started": True, "range": f"{start_date}..{end_date}", "dry_run": dry}


@router.get("/orders/backfill/status")
async def ticimax_orders_backfill_status(current_user: dict = Depends(require_admin)):
    doc = await db.settings.find_one({"id": "ticimax_order_backfill"}, {"_id": 0})
    return doc or {"status": "none"}


@router.get("/wsdl-probe")
async def ticimax_wsdl_probe(current_user: dict = Depends(require_admin)):
    """SiparisServis WSDL'ine Railway'den erişimi ve harici şema referanslarını teşhis eder."""
    import httpx
    import re as _re
    import ticimax_client as _tc
    await _ensure_domain()
    SIPARIS_WSDL = _tc.SIPARIS_WSDL  # set_domain SONRASI güncel değer
    out = {"wsdl_url": SIPARIS_WSDL}
    try:
        async with httpx.AsyncClient(timeout=25.0, follow_redirects=True) as c:
            r = await c.get(SIPARIS_WSDL)
            body = r.text or ""
            out["status"] = r.status_code
            out["final_url"] = str(r.url)
            out["head"] = body[:300]
            refs = sorted(set(_re.findall(r'(?:schemaLocation|location)="([^"]+)"', body)))
            out["external_refs"] = refs[:20]
            ref_status = {}
            for u in refs[:8]:
                if not u.lower().startswith("http"):
                    continue
                try:
                    rr = await c.get(u)
                    ref_status[u] = {"status": rr.status_code, "head": (rr.text or "")[:120]}
                except Exception as e:
                    ref_status[u] = {"error": str(e)[:150]}
            out["ref_status"] = ref_status
    except Exception as e:
        out["error"] = str(e)[:300]
    return out


@router.get("/orders/probe")
async def ticimax_orders_probe(
    start_date: str, end_date: str, page: int = 1,
    include_marketplace: bool = True,
    current_user: dict = Depends(require_admin),
):
    """Ticimax WS'ten örnek sipariş yapısı — backfill alan eşlemesi doğrulaması."""
    from ticimax_client import get_orders as tc_get_orders, set_live
    key = await _api_key()
    await _ensure_domain()
    set_live(True)
    try:
        orders = await asyncio.to_thread(
            tc_get_orders, page, 5, key, start_date, end_date,
            not include_marketplace, False)
    finally:
        set_live(False)
    return {"count": len(orders), "samples": [_deep(o) for o in orders[:2]]}
