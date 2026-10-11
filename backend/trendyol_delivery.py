"""
Trendyol kargoya veriliş (termin) süresi — otomatik haftalık takvim.

Kullanıcı kuralı (2026-09-30, güncel), TR saati:
  · Pazar 12:00 → Cuma 15:00 : aynı gün kargo (SAME_DAY_SHIPPING), termin 1 gün
  · Cuma 15:00 → Pazar 12:00 : aynı gün kargo KAPALI, termin 2 gün

Trendyol'da termin, ürün güncelleme servisindeki `deliveryOption` alanıyla değişir ve bu
servis ürünün TÜM içerik alanlarını ister. Fiyat/özellik/görsel yanlışlıkla değişmesin diye
ürünün Trendyol'daki KENDİ güncel verisi okunur, yalnız `deliveryOption` değiştirilerek geri
gönderilir. Durum değiştiğinde (ya da uygulanamadıysa) iş kendini tekrarlar.
Ayarlar İşletme Kuralları'ndan: trendyol.delivery_auto (aç/kapa), trendyol.same_day_start_sunday,
trendyol.same_day_end_friday, trendyol.delivery_duration_same_day, trendyol.delivery_duration_other.
"""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import asyncio
import logging

logger = logging.getLogger(__name__)
_TR = ZoneInfo("Europe/Istanbul")
_STATE_ID = "trendyol_delivery_state"


def _hm(v, default):
    try:
        h, m = [int(x) for x in str(v or default).split(":")[:2]]
        return h, m
    except Exception:
        h, m = [int(x) for x in default.split(":")]
        return h, m


def desired_state(now_utc: datetime = None, sun_start: str = "12:00", fri_end: str = "15:00") -> str:
    """'same_day' (Pazar sun_start → Cuma fri_end) | 'other' (Cuma fri_end → Pazar sun_start)."""
    t = (now_utc or datetime.now(timezone.utc)).astimezone(_TR)
    hm = (t.hour, t.minute)
    wd = t.weekday()  # Pzt=0 … Pzr=6
    if wd <= 3:                                   # Pzt–Per tüm gün
        return "same_day"
    if wd == 4:                                   # Cuma: bitiş saatine kadar
        return "same_day" if hm < _hm(fri_end, "15:00") else "other"
    if wd == 6:                                   # Pazar: başlangıç saatinden sonra
        return "same_day" if hm >= _hm(sun_start, "12:00") else "other"
    return "other"                                # Cumartesi


def delivery_option(state: str, dur_same_day: int = 1, dur_other: int = 2) -> dict:
    if state == "same_day":
        d = max(1, int(dur_same_day or 1))
        opt = {"deliveryDuration": d}
        # Trendyol: hızlı teslimat tipi yalnız termin 1 gün iken gönderilebilir.
        if d == 1:
            opt["fastDeliveryType"] = "SAME_DAY_SHIPPING"
        return opt
    return {"deliveryDuration": max(1, int(dur_other or 2))}


async def _rules(db) -> dict:
    from business_rules import get_rule
    return {
        "enabled": (await get_rule(db, "trendyol.delivery_auto", True)) is not False,
        "sun_start": str(await get_rule(db, "trendyol.same_day_start_sunday", "12:00") or "12:00"),
        "fri_end": str(await get_rule(db, "trendyol.same_day_end_friday", "15:00") or "15:00"),
        "dur_same": int(await get_rule(db, "trendyol.delivery_duration_same_day", 1) or 1),
        "dur_other": int(await get_rule(db, "trendyol.delivery_duration_other", 2) or 2),
    }


def _state_and_option(r: dict, now_utc: datetime = None):
    st = desired_state(now_utc, r["sun_start"], r["fri_end"])
    return st, delivery_option(st, r["dur_same"], r["dur_other"])


async def current_option(db):
    """Şu an geçerli deliveryOption (otomatik kapalıysa None) — ürün aktarımı da kullanır."""
    try:
        r = await _rules(db)
        if not r["enabled"]:
            return None
        return _state_and_option(r)[1]
    except Exception:
        return None


def skip_reason(r: dict) -> str:
    """Ürün neden toplu termin güncellemesine alınamıyor? Boş dize = alınabilir."""
    if r.get("archived") is True:
        return "arşivde"
    cat = r.get("pimCategoryId") or r.get("categoryId")
    for name, val in (("barkod", r.get("barcode")), ("başlık", r.get("title")),
                      ("model kodu", r.get("productMainId")), ("marka", r.get("brandId")), ("kategori", cat),
                      ("açıklama", r.get("description")), ("görsel", r.get("images"))):
        if not val:
            return f"Trendyol yanıtında {name} yok"
    # Özellik listesi BOŞ olabilir (özelliği girilmemiş ürün) — eskiden bu ürünler de atlanıyor ve
    # hiç aynı gün kargo almıyordu (2026-10-05). Boş liste geri göndermek mevcut durumu değiştirmez;
    # yalnız alanın HİÇ gelmemesi (yanıtta yok) atlama nedenidir.
    if r.get("attributes") is None:
        return "Trendyol yanıtında özellik alanı yok"
    return ""


def _item_from_ty(r: dict, opt: dict, cargo_company_id) -> dict | None:
    """Trendyol ürün kaydı → güncelleme kalemi (yalnız deliveryOption farklı)."""
    cat = r.get("pimCategoryId") or r.get("categoryId")
    # GÜVENLİK: içerik alanlarından biri Trendyol yanıtında yoksa ürün GÖNDERİLMEZ — aksi halde
    # güncelleme ürünün açıklamasını/görselini boşaltabilirdi. Arşivdekiler de atlanır.
    if skip_reason(r):
        return None
    attrs = []
    for a in (r.get("attributes") or []):
        aid = a.get("attributeId")
        if not aid:
            continue
        if a.get("attributeValueId"):
            attrs.append({"attributeId": aid, "attributeValueId": a["attributeValueId"]})
        elif a.get("attributeValue") not in (None, ""):
            attrs.append({"attributeId": aid, "customAttributeValue": str(a["attributeValue"])})
    item = {
        "barcode": r["barcode"], "title": r["title"], "productMainId": r["productMainId"],
        "brandId": r["brandId"], "categoryId": cat,
        "stockCode": r.get("stockCode") or r["barcode"],
        "dimensionalWeight": r.get("dimensionalWeight") or 1,
        "description": r["description"],
        "currencyType": r.get("currencyType") or "TRY",
        "vatRate": r.get("vatRate") if r.get("vatRate") is not None else 10,
        "images": [{"url": i.get("url")} for i in (r.get("images") or []) if isinstance(i, dict) and i.get("url")],
        "attributes": attrs,
        "deliveryOption": opt,
    }
    cc = r.get("cargoCompanyId") or cargo_company_id
    if cc:
        item["cargoCompanyId"] = cc
    for k in ("shipmentAddressId", "returningAddressId"):
        if r.get(k):
            item[k] = r[k]
    return item


async def apply(db, force: bool = False) -> dict:
    """Gerekiyorsa tüm onaylı Trendyol ürünlerinin terminini günceller. Durum aynı ve son
    uygulama başarılıysa hiçbir şey yapmaz (saatlik kontrol güvenli)."""
    r = await _rules(db)
    if not r["enabled"]:
        return {"skipped": "kapalı"}
    state, opt = _state_and_option(r)
    last = await db.settings.find_one({"id": _STATE_ID}, {"_id": 0}) or {}
    if not force and last.get("state") == state and last.get("ok") and last.get("option") == opt \
            and not (last.get("failed_barcodes") and int(last.get("retry_count") or 0) < 3):
        return {"skipped": "zaten güncel", "state": state}

    from routes.integrations_trendyol import get_trendyol_config
    from trendyol_client import TrendyolClient
    cfg = await get_trendyol_config()
    if not (cfg and cfg.get("api_key") and cfg.get("supplier_id")):
        return {"skipped": "Trendyol ayarı yok"}
    cli = TrendyolClient(supplier_id=cfg["supplier_id"], api_key=cfg["api_key"],
                         api_secret=cfg["api_secret"], mode=cfg.get("mode", "live"))
    cargo = cfg.get("default_cargo_company_id")

    # Durum aynı ve son uygulama başarılıysa YALNIZ önceki turda Trendyol'un reddettiği barkodlar
    # (en çok 3 kez) yeniden denenir — tüm katalog her saat yeniden gönderilmez.
    only = None
    if not force and last.get("state") == state and last.get("option") == opt and last.get("ok") is not False \
            and last.get("failed_barcodes") and int(last.get("retry_count") or 0) < 3:
        only = set(last.get("failed_barcodes") or [])

    items, skipped, skipped_list, page = [], 0, [], 0
    while page < 200:
        res = await cli.get_filtered_products(approved=True, archived=False, page=page, size=100)
        rows = (res or {}).get("content") or []
        for row in rows:
            if only is not None and row.get("barcode") not in only:
                continue
            it = _item_from_ty(row, opt, cargo)
            if it:
                items.append(it)
            else:
                skipped += 1
                if len(skipped_list) < 200:
                    skipped_list.append({"barcode": row.get("barcode"), "reason": skip_reason(row)})
        page += 1
        if not rows or page >= int((res or {}).get("totalPages") or 0):
            break
        await asyncio.sleep(0.3)

    batches, errors = [], []
    for i in range(0, len(items), 100):
        chunk = items[i:i + 100]
        try:
            resp = await cli.update_products(chunk)
            bid = (resp or {}).get("batchRequestId")
            if bid:
                batches.append(bid)
            else:
                errors.append(str(resp)[:300])
        except Exception as e:
            errors.append(str(e)[:300])
        await asyncio.sleep(1.0)

    now = datetime.now(timezone.utc).isoformat()
    doc = {"id": _STATE_ID, "state": state, "option": opt, "applied_at": now,
           "ok": bool(items) and not errors, "items": len(items), "skipped": skipped,
           "skipped_items": skipped_list, "batches": batches, "errors": errors[:10], "checked": False,
           "partial_retry": only is not None,
           "retry_count": (int(last.get("retry_count") or 0) + 1) if only is not None else 0}
    if only is not None:
        doc["ok"] = not errors     # yeniden deneme turu: tam katalog durumu bozulmaz
    await db.settings.update_one({"id": _STATE_ID}, {"$set": doc}, upsert=True)
    try:
        from routes.integrations_common import log_integration_event
        label = (f"Aynı gün kargo, termin {opt['deliveryDuration']} gün" if state == "same_day"
                 else f"Termin {opt['deliveryDuration']} gün")
        await log_integration_event("trendyol", "delivery_option", "bulk", str(len(items)),
                                    "success" if doc["ok"] else "error",
                                    f"Termin: {label} → {len(items)} ürün, {len(batches)} batch"
                                    + (f", atlanan {skipped} ürün (ilk: "
                                       + "; ".join(f"{x['barcode']}: {x['reason']}" for x in skipped_list[:5]) + ")"
                                       if skipped else "")
                                    + (" [yalnız reddedilenler yeniden denendi]" if only is not None else "")
                                    + (f", hata: {errors[0]}" if errors else ""))
    except Exception:
        pass
    logger.warning(f"[trendyol-termin] {state} → {len(items)} ürün, batch={len(batches)}, hata={len(errors)}")
    return {k: doc[k] for k in ("state", "option", "items", "skipped", "batches", "errors", "ok")}


async def check_batches(db) -> dict:
    """Son uygulamanın batch sonuçlarını Trendyol'dan okur; başarısız kalem varsa kaydeder
    ve bir sonraki saatlik kontrolde yeniden denenmesi için ok=False yapar."""
    last = await db.settings.find_one({"id": _STATE_ID}, {"_id": 0}) or {}
    if not last.get("batches") or last.get("checked"):
        return {"skipped": True}
    from routes.integrations_trendyol import get_trendyol_config
    from trendyol_client import TrendyolClient
    cfg = await get_trendyol_config()
    cli = TrendyolClient(supplier_id=cfg["supplier_id"], api_key=cfg["api_key"],
                         api_secret=cfg["api_secret"], mode=cfg.get("mode", "live"))
    failed, reasons, pending, failed_items = 0, {}, False, []
    for bid in last["batches"]:
        try:
            br = await cli.get_batch_request_result(bid)
        except Exception:
            pending = True
            continue
        if str((br or {}).get("status") or "").upper() in ("IN_PROGRESS", "PROCESSING"):
            pending = True
        for it in (br or {}).get("items") or []:
            if str(it.get("status") or "").upper() == "FAILED":
                failed += 1
                bc = str(((it.get("requestItem") or {}).get("product") or it.get("requestItem") or {}).get("barcode")
                         or "")
                fr0 = str((it.get("failureReasons") or [""])[0])[:160]
                if bc and len(failed_items) < 200:
                    failed_items.append({"barcode": bc, "reason": fr0})
                for fr in (it.get("failureReasons") or [])[:1]:
                    reasons[str(fr)[:160]] = reasons.get(str(fr)[:160], 0) + 1
    if pending:
        return {"pending": True}
    upd = {"checked": True, "failed": failed, "failure_reasons": sorted(reasons.items(), key=lambda x: -x[1])[:10],
           "failed_items": failed_items, "failed_barcodes": [f["barcode"] for f in failed_items]}
    await db.settings.update_one({"id": _STATE_ID}, {"$set": upd})
    return upd
