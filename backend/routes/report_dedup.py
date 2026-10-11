"""Rapor dedup — ticimax_history ÇİFT kayıtlarını rapor toplamlarından HARİÇ tutar.

Arka plan: Ticimax geçmiş aktarımı (`imported_from="ticimax_history"`) bazı Trendyol
siparişlerini, CANLI Trendyol sync'inde de var olan siparişlerle ÇİFT oluşturdu.
Bu ticimax kopyasının `ticimax_siparis_no` alanı `TY-<ticimaxId>_<GERÇEK_TY_NO>`
biçimindedir; son `_` segmenti (GERÇEK_TY_NO) bir CANLI (native) siparişin
`order_number` değeriyle eşleşir. Kopyanın KENDİ `order_number`'ı ise ticimax id'sidir
(native ile çakışmaz). Böyle bir kopya raporda aynı satışı İKİNCİ kez saydırır.

Bu modül SALT-OKUNUR sorgu-zamanı filtresi üretir — HİÇBİR belgeyi silmez/değiştirmez.
Twin'i OLMAYAN gerçek geçmiş kayıtlara (6791 tekil) DOKUNMAZ; yalnız twin'i olan
kopyalar rapor $match'lerinden düşülür. Sonuç kısa süre cache'lenir (geçmiş veri statik;
backfill tamamlandı). Cache, router'lara eklenen `load_dup_dep` bağımlılığıyla her
istekte tazelenir; `dup_nor()` senkron erişimci tazelenmiş cache'i okur.
"""
import time
from typing import List

from .deps import db

_TTL = 1800.0  # 30 dk — geçmiş veri statik; sık taramaya gerek yok
_cache = {"at": -1e9, "nums": None}  # type: ignore


async def load_dup_order_numbers() -> List[str]:
    """Twin'i olan ticimax_history kopyalarının KENDİ order_number'larını döndürür.

    $lookup ile: her ticimax_history belgesi için gömülü GERÇEK_TY_NO
    (ticimax_siparis_no'nun son '_' segmenti) native (ticimax_history OLMAYAN) bir
    order_number ile eşleşiyorsa, o kopya ÇİFT'tir. order_number index'i kullanılır.
    Hata/boşlukta önceki cache (veya boş) döner — rapor asla patlamaz.
    """
    now = time.monotonic()
    c = _cache
    if c["nums"] is not None and (now - c["at"]) < _TTL:
        return c["nums"]
    nums: List[str] = []
    try:
        pipeline = [
            {"$match": {"imported_from": "ticimax_history"}},
            {"$project": {
                "order_number": 1,
                "_emb": {"$arrayElemAt": [
                    {"$split": [{"$ifNull": ["$ticimax_siparis_no", ""]}, "_"]}, -1]},
            }},
            {"$lookup": {
                "from": "orders",
                "let": {"e": "$_emb"},
                "pipeline": [
                    {"$match": {"$expr": {"$and": [
                        {"$eq": ["$order_number", "$$e"]},
                        {"$ne": ["$imported_from", "ticimax_history"]},
                    ]}}},
                    {"$limit": 1},
                    {"$project": {"_id": 1}},
                ],
                "as": "_twin",
            }},
            {"$match": {"_twin.0": {"$exists": True}}},
            {"$project": {"_id": 0, "order_number": 1}},
        ]
        async for r in db.orders.aggregate(pipeline):
            on = r.get("order_number")
            if on is not None:
                nums.append(str(on))
    except Exception:
        return c["nums"] or []
    c["nums"] = nums
    c["at"] = now
    return nums


def dup_filter_state() -> int:
    """Kopya süzgecinin durumu: -1 = hiç yüklenmedi, aksi halde elenen numara sayısı.
    Rapor önbellek anahtarına girer — elemesiz hesaplanan sonuç elemeli isteğe dönmesin."""
    nums = _cache["nums"]
    return -1 if nums is None else len(nums)


def dup_nor() -> dict:
    """Senkron erişimci: rapor $match'ine eklenecek hariç-tutma parçası (veya {}).

    `{"$nor": [{imported_from: ticimax_history, order_number: {$in: [...]}}]}` —
    yalnız twin'i olan ticimax kopyalarını düşürür; native twin ve tekil geçmiş kalır.
    Cache `load_dup_dep` bağımlılığıyla tazelenmiş olmalı; boşsa {} döner (no-op).
    """
    nums = _cache["nums"]
    if not nums:
        return {}
    return {"$nor": [{"imported_from": "ticimax_history",
                      "order_number": {"$in": nums}}]}


def merge_match(base: dict) -> dict:
    """Bir $match dict'ine dup hariç-tutmayı (varsa) ekler. base'i mutasyona uğratmaz."""
    nor = dup_nor()
    if not nor:
        return base
    out = dict(base or {})
    out.update(nor)  # top-level $nor; mevcut $or/status/tarih anahtarlarıyla AND'lenir
    return out


def canonical_order_stages() -> list:
    """Mongo aggregation stages that enforce one document per real order.

    Marketplace imports can leave two documents with the same ``order_number``.
    Reports must consistently select the newest terminal/partial-cancel document,
    while orders without an order number remain distinct by their internal id.
    Call this immediately after the report's initial ``$match`` and before any
    ``$unwind`` or metric grouping.
    """

    terminal = [
        "cancelled", "cancel_refunded", "return_requested", "return_approved",
        "return_in_transit", "returned", "refunded", "partial_refunded",
    ]
    return [
        {"$addFields": {
            "_report_order_number": {"$trim": {"input": {"$toString": {"$ifNull": ["$order_number", ""]}}}},
            "_report_terminal": {"$cond": [{"$in": ["$status", terminal]}, 1, 0]},
            "_report_partial_cancel": {"$cond": [{"$gt": [{"$ifNull": ["$partial_cancel_amount", 0]}, 0]}, 1, 0]},
        }},
        {"$addFields": {
            "_report_dedupe_key": {"$cond": [
                {"$ne": ["$_report_order_number", ""]},
                {"$concat": ["order:", "$_report_order_number"]},
                {"$concat": ["id:", {"$toString": {"$ifNull": ["$id", "$_id"]}}]},
            ]},
        }},
        {"$sort": {
            "_report_dedupe_key": 1,
            "_report_terminal": -1,
            "_report_partial_cancel": -1,
            "updated_at": -1,
            "created_at": -1,
        }},
        {"$group": {"_id": "$_report_dedupe_key", "_report_doc": {"$first": "$$ROOT"}}},
        {"$replaceRoot": {"newRoot": "$_report_doc"}},
    ]


def effective_order_date_match(start: str, end: str | None = None) -> dict:
    """Date predicate shared by every order-backed report.

    Marketplace order time is authoritative. ``created_at`` is only the
    fallback for site orders and legacy records where marketplace time is empty.
    """

    bounds = {"$gte": start}
    if end is not None:
        bounds["$lte"] = end
    return {"$or": [
        {"marketplace_order_date": dict(bounds)},
        {"marketplace_order_date": {"$in": [None, ""]}, "created_at": dict(bounds)},
    ]}


def split_confirmed_return(quantity: int, amount: float, returned: int) -> tuple[int, float, int, float]:
    """Split one order line into kept/net and confirmed-return portions."""
    qty = max(0, int(quantity or 0))
    ret = min(qty, max(0, int(returned or 0)))
    kept = qty - ret
    kept_amount = float(amount or 0) * kept / qty if qty else 0.0
    return kept, kept_amount, ret, float(amount or 0) - kept_amount


def allocate_order_total(line_amounts: list[float], order_total: float) -> list[float]:
    """Allocate the authoritative order total across product lines.

    Marketplace history contains more than one item-price representation.  Report
    revenue must therefore close to ``orders.total`` (the same source used by the
    sales breakdown), while preserving each line's relative share.  The last line
    receives the rounding remainder so the returned values add up to the cent.
    """
    amounts = [max(0.0, float(value or 0)) for value in (line_amounts or [])]
    target = max(0.0, round(float(order_total or 0), 2))
    if not amounts:
        return []
    base = sum(amounts)
    if base <= 0:
        # There is no defensible product-level weight.  Keep the full value visible
        # on one line rather than inventing equal unit prices.
        return [target] + [0.0] * (len(amounts) - 1)
    allocated = [round(target * value / base, 2) for value in amounts]
    allocated[-1] = round(allocated[-1] + target - sum(allocated), 2)
    return allocated


def partial_cancel_net_values(total: float, units: int, cancel_amount: float,
                              cancel_units: int, total_scope: str = "") -> tuple[float, int]:
    """Return the active remainder without subtracting an active-only total twice.

    New Trendyol reconciliation records explicitly mark totals/items that already
    contain only active package lines. Legacy records have no marker and retain the
    historical full-order subtraction behaviour.
    """
    total = max(0.0, float(total or 0))
    units = max(0, int(units or 0))
    if str(total_scope or "").strip().lower() == "active":
        return total, units
    amount = min(total, max(0.0, float(cancel_amount or 0)))
    cancelled = min(max(0, int(cancel_units or 0)), max(0, units - 1))
    return round(total - amount, 2), units - cancelled


def product_quantity_metrics(net: int, cancelled: int, returned: int) -> dict:
    """Kanonik ürün adetleri ve iki açıkça adlandırılmış iade oranı.

    Trendyol İş Analizi: iade / (net + iptal + iade).
    Operasyonel oran: iade / (net + iade), yani iptaller paydaya girmez.
    """
    net = max(0, int(net or 0))
    cancelled = max(0, int(cancelled or 0))
    returned = max(0, int(returned or 0))
    gross = net + cancelled + returned
    sold_excluding_cancels = net + returned
    return {
        "gross_qty": gross,
        "trendyol_return_rate_pct": round(100 * returned / gross, 2) if gross else 0.0,
        "return_rate_excluding_cancels_pct": (
            round(100 * returned / sold_excluding_cancels, 2)
            if sold_excluding_cancels else 0.0
        ),
    }


def kept_gross_revenue(net_revenue: float, gross_revenue: float,
                       discount_amount: float) -> float:
    """Net kalan satışın indirim-öncesi karşılığını aynı ürün indirim oranıyla bul."""
    net_revenue = max(0.0, float(net_revenue or 0))
    gross_revenue = max(0.0, float(gross_revenue or 0))
    paid_before_returns = max(0.0, gross_revenue - float(discount_amount or 0))
    if gross_revenue <= 0 or paid_before_returns <= 0:
        return net_revenue
    return net_revenue * gross_revenue / paid_before_returns


def reconciled_platform_breakdown(product: dict) -> list[dict]:
    """Platform kırılımını ürünün kanonik net adet/ciro toplamına kuruşu kuruşuna eşitle."""
    qty = max(0, int((product or {}).get("qty") or 0))
    revenue = float((product or {}).get("revenue") or 0)
    rows = [dict(row) for row in ((product or {}).get("platform_breakdown") or [])]
    if not rows:
        rows = [{"platform": (product or {}).get("top_platform") or "site",
                 "qty": qty, "revenue": revenue}]
    rows[0]["revenue"] = float(rows[0].get("revenue") or 0) + (
        revenue - sum(float(row.get("revenue") or 0) for row in rows))
    rows[0]["qty"] = int(rows[0].get("qty") or 0) + (
        qty - sum(int(row.get("qty") or 0) for row in rows))
    return rows


def product_platform_metrics(net_rows: list[dict], event_rows: list[dict]) -> list[dict]:
    """Build scope-safe product metrics for each marketplace/channel.

    ``net_rows`` contains kept sales, while ``event_rows`` contains cancelled and
    returned units/amounts. Keeping both in one row prevents a Trendyol rate from
    accidentally using the all-channel denominator.
    """
    platforms: dict[str, dict] = {}
    for row in net_rows or []:
        key = str(row.get("platform") or "site").strip().lower() or "site"
        dst = platforms.setdefault(key, {"platform": key, "net_qty": 0, "net_revenue": 0.0,
                                         "cancel_qty": 0, "cancel_total": 0.0,
                                         "return_qty": 0, "return_total": 0.0})
        dst["net_qty"] += max(0, int(row.get("qty") or 0))
        dst["net_revenue"] += max(0.0, float(row.get("revenue") or 0))
    for row in event_rows or []:
        key = str(row.get("platform") or "site").strip().lower() or "site"
        dst = platforms.setdefault(key, {"platform": key, "net_qty": 0, "net_revenue": 0.0,
                                         "cancel_qty": 0, "cancel_total": 0.0,
                                         "return_qty": 0, "return_total": 0.0})
        dst["cancel_qty"] += max(0, int(row.get("cancel") or row.get("cancel_qty") or 0))
        dst["cancel_total"] += max(0.0, float(row.get("cancel_total") or 0))
        dst["return_qty"] += max(0, int(row.get("return") or row.get("return_qty") or 0))
        dst["return_total"] += max(0.0, float(row.get("return_total") or 0))
    result = []
    for dst in platforms.values():
        metrics = product_quantity_metrics(
            dst["net_qty"], dst["cancel_qty"], dst["return_qty"])
        dst.update(metrics)
        dst["gross_revenue"] = round(
            dst["net_revenue"] + dst["cancel_total"] + dst["return_total"], 2)
        for key in ("net_revenue", "cancel_total", "return_total"):
            dst[key] = round(dst[key], 2)
        result.append(dst)
    return sorted(result, key=lambda row: (-row["net_revenue"], row["platform"]))


def payment_report_group_key(order: dict) -> str:
    """Return the payment-report bucket without losing marketplace identity.

    Marketplace orders are reported under their marketplace, regardless of a
    legacy/default ``payment_method`` value.  Site orders retain their actual
    payment method.  Some legacy imports put the marketplace only in the
    ``marketplace`` field, so both fields must be inspected.
    """
    marketplaces = {"trendyol", "hepsiburada", "temu", "n11", "amazon"}
    platform = str((order or {}).get("platform") or "").strip().lower()
    marketplace = str((order or {}).get("marketplace") or "").strip().lower()
    if platform in marketplaces:
        return platform
    if marketplace in marketplaces:
        return marketplace
    return str((order or {}).get("payment_method") or "—").strip().lower() or "—"


# Marketplace return state is shared by the operational Returns screen and the
# financial reports.  Keeping this vocabulary here prevents the two surfaces
# from silently assigning the same Trendyol claim item to different buckets.
OPEN_MARKETPLACE_CLAIM_STATUSES = frozenset({"Created", "WaitingInAction", "InAnalysis"})


def marketplace_claim_status_bucket(status: str, has_cargo: bool = False) -> str | None:
    """Map a marketplace claim-item status to the canonical operational bucket."""
    status = str(status or "").strip()
    if status == "Accepted":
        return "onaylanan"
    if status in ("Rejected", "Unresolved"):
        return "reddedilen"
    if status in ("WaitingInAction", "InAnalysis"):
        return "aksiyon_bekleyen"
    if status == "Created":
        return "kargoya_verilen" if has_cargo else "talep_olusturulan"
    if status == "Cancelled":
        return "iptal"
    return None


def dedupe_return_records(records: list[dict]) -> list[dict]:
    """Keep one current document per return id, without merging distinct returns.

    Historical imports can leave multiple versions of the same ``customer_returns``
    record.  A repeated id is one return, while two different ids for the same
    order can be legitimate separate return attempts and must both survive.
    """
    terminal = {"returned", "refunded", "partial_refunded", "return_approved",
                "approved", "completed", "complete"}

    def rank(row: dict):
        status = str((row or {}).get("status") or "").lower()
        return (1 if status in terminal else 0,
                str((row or {}).get("updated_at") or (row or {}).get("created_at") or ""))

    best: dict[str, dict] = {}
    anonymous: list[dict] = []
    for row in records or []:
        rid = str((row or {}).get("id") or (row or {}).get("_id") or "").strip()
        if not rid:
            anonymous.append(row)
            continue
        current = best.get(rid)
        if current is None or rank(row) > rank(current):
            best[rid] = row
    return list(best.values()) + anonymous


def dedupe_return_items(items: list[dict]) -> list[dict]:
    """Remove repeated item ids inside one return; id-less lines remain distinct."""
    out: list[dict] = []
    seen: set[str] = set()
    for index, item in enumerate(items or []):
        item = item or {}
        iid = str(item.get("id") or item.get("return_item_id")
                  or item.get("claim_item_id") or "").strip()
        key = f"id:{iid}" if iid else f"row:{index}"
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def claim_items_with_status(claim: dict, statuses: set[str]) -> list[dict]:
    """Return marketplace claim units whose child status is in ``statuses``.

    Trendyol claims may contain a mixture of Accepted, Created and Rejected
    ``claimItems``.  The claim-level status deliberately represents the most
    actionable child status, so it is not a safe source for historical return
    quantities.  Reports must inspect every raw claim item instead.

    Each Trendyol ``claimItem`` is one returned unit.  Barcode is optional here:
    order-level financial reconciliation still has to count a unit whose legacy
    line is missing a barcode (product-level reports may ignore that barcode).
    Legacy/manual rows do not
    always have raw data; for those, normalized items are used only when the
    stored claim itself is accepted.  Returned rows include a stable key so a
    repeated claim item cannot inflate totals.
    """
    raw_items = ((claim or {}).get("raw_data") or {}).get("items") or []
    matched: list[dict] = []
    seen: set[str] = set()
    claim_id = str((claim or {}).get("claim_id") or "").strip()
    normalized_by_id = {
        str((item or {}).get("claim_item_id") or "").strip(): (item or {})
        for item in ((claim or {}).get("items") or [])
        if str((item or {}).get("claim_item_id") or "").strip()
    }

    if raw_items:
        saw_marketplace_child = False
        for line_index, raw_line in enumerate(raw_items):
            raw_line = raw_line or {}
            raw_children = raw_line.get("claimItems") or []
            if raw_children:
                saw_marketplace_child = True
            order_line = raw_line.get("orderLine") or {}
            barcode = str(order_line.get("barcode") or "").strip()
            for item_index, claim_item in enumerate(raw_children):
                claim_item = claim_item or {}
                status = str(((claim_item.get("claimItemStatus") or {}).get("name") or "")).strip()
                if status not in statuses:
                    continue
                item_id = str(claim_item.get("id") or "").strip()
                key = f"{claim_id}:{item_id}" if item_id else f"{claim_id}:raw:{line_index}:{item_index}"
                if key in seen:
                    continue
                seen.add(key)
                normalized = normalized_by_id.get(item_id) or {}
                try:
                    amount = float(normalized.get("price") or normalized.get("unit_price")
                                   or order_line.get("price") or 0)
                except Exception:
                    amount = 0.0
                matched.append({"key": key, "barcode": barcode, "quantity": 1,
                                "amount": max(0.0, amount), "status": status})
        # Trendyol has nested claimItems. HB commonly has a flat raw ``items``
        # array; in that case fall through to the normalized-items parser.
        if saw_marketplace_child:
            return matched

    stored_status = str((claim or {}).get("claim_status") or "").strip()
    if stored_status not in statuses:
        return matched
    for item_index, item in enumerate((claim or {}).get("items") or []):
        item = item or {}
        barcode = str(item.get("barcode") or "").strip()
        if not barcode:
            continue
        item_id = str(item.get("claim_item_id") or "").strip()
        key = f"{claim_id}:{item_id}" if item_id else f"{claim_id}:legacy:{item_index}"
        if key in seen:
            continue
        seen.add(key)
        try:
            unit_amount = float(item.get("price") or item.get("unit_price") or 0)
        except Exception:
            unit_amount = 0.0
        quantity = max(1, int(item.get("quantity") or 1))
        matched.append({
            "key": key,
            "barcode": barcode,
            "quantity": quantity,
            "amount": max(0.0, unit_amount) * quantity,
            "status": stored_status,
        })
    return matched


def accepted_claim_items(claim: dict) -> list[dict]:
    """Return accepted marketplace claim units, item by item."""
    return claim_items_with_status(claim, {"Accepted"})


async def load_dup_dep():
    """Router bağımlılığı — handler'dan ÖNCE cache'i tazeler (salt-okuma)."""
    try:
        await load_dup_order_numbers()
    except Exception:
        pass
    return True
