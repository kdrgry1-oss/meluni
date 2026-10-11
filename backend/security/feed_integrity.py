"""
Feed bütünlüğü (COM15 / FIN-E04 / CHAIN05) — ürün XML feed'i için içe aktarma MANİFESTİ.

Her içe aktarma denemesi için bir manifest yazılır (`feed_import_manifests`):
    sha256 (içerik özeti), item_count, source (sorgu dizesi ATILMIŞ adres — token içerebilir),
    source_url_sha256 (tam adresin özeti), generated_at / sequence (feed veriyorsa), status
    (applied | rejected), reasons, forced, actor, report_id.

Reddetme kuralları (son BAŞARILI uygulanan manifeste göre; admin `force` ile bilerek aşabilir):
  * ürün sayısı %40'tan fazla DÜŞTÜYSE (eksik/kısmi/bozuk feed kataloğu bozmasın),
  * feed'in üretim zamanı (lastBuildDate / pubDate / atom:updated / generated_at) son uygulanandan
    ESKİYSE (bayat ya da tekrar oynatılan feed),
  * feed sıra numarası (sequence) son uygulanandan KÜÇÜKSE ya da AYNI numara FARKLI içerikle
    geldiyse (atlama/tekrar),
  * feed KAYNAĞI (şema/host/yol) son uygulanandan farklıysa (CHAIN11: yeni hedef eski onayı
    miras almaz; adres sorgusundaki token değişimi kaynak değişimi sayılmaz).
Aynı içerik (aynı sha256) yeniden gelirse reddedilmez — işlem idempotenttir, manifest `duplicate`
işaretlenir.

Her deneme için "etkilenen ürünler" raporu (`feed_import_reports`) yazılır: reddedilende hangi
ürünlerin etkilenecekti (eksik kalacak / fiyatı değişecek xml_id'ler) ve bu ürünleri içeren açık
siparişler; uygulananda eklenen / güncellenen / fiyatı incelemeye alınan ürünler ve fiyatı
değişen ürünleri içeren son 24 saatteki açık siparişler (fiyatı eski teklifle verilmiş olabilir).
Kişisel veri yazılmaz (yalnız ürün xml_id ve sipariş numarası).
"""
from __future__ import annotations

import hashlib
import logging
import uuid
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Iterable, Optional
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)

MANIFESTS = "feed_import_manifests"
REPORTS = "feed_import_reports"
DROP_THRESHOLD = 0.40          # son iyi içe aktarmaya göre >%40 düşüş → red
MIN_BASELINE_ITEMS = 10        # çok küçük kataloglarda yüzde kuralı uygulanmaz
REPORT_CAP = 1000
_OPEN_ORDER_STATUSES = ["pending", "awaiting_payment", "payment_notified", "confirmed", "processing",
                        "preparing", "ready_to_ship"]
_NS_G = "{http://base.google.com/ns/1.0}"
_NS_ATOM = "{http://www.w3.org/2005/Atom}"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_ts(raw) -> Optional[str]:
    s = str(raw or "").strip()
    if not s:
        return None
    dt = None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        try:
            dt = parsedate_to_datetime(s)
        except (TypeError, ValueError, IndexError):
            dt = None
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def _first_text(el, tags: Iterable[str]) -> str:
    if el is None:
        return ""
    for t in tags:
        x = el.find(t)
        if x is not None and (x.text or "").strip():
            return (x.text or "").strip()
    return ""


def extract_feed_meta(root) -> dict:
    """Feed'in kendi beyan ettiği üretim zamanı ve sıra numarası (varsa)."""
    channel = root.find("channel") if root is not None else None
    holder = channel if channel is not None else root
    gen_raw = (_first_text(holder, ("lastBuildDate", "pubDate", f"{_NS_ATOM}updated", "updated",
                                    "generated_at", f"{_NS_G}generated_at"))
               or str((root.attrib if root is not None else {}).get("generated_at") or ""))
    seq_raw = (_first_text(holder, ("sequence", f"{_NS_G}sequence", "feed_sequence"))
               or str((root.attrib if root is not None else {}).get("sequence") or ""))
    seq = None
    try:
        seq = int(str(seq_raw).strip()) if str(seq_raw).strip() else None
    except ValueError:
        seq = None
    return {"generated_at": _parse_ts(gen_raw), "sequence": seq}


def safe_source(url: str) -> str:
    """Adresin sorgu/parça kısmını at (feed adresleri erişim token'ı taşıyabilir)."""
    try:
        p = urlsplit(str(url or ""))
        return f"{p.scheme}://{p.hostname or ''}{p.path or ''}"[:300]
    except Exception:
        return ""


def build_manifest(xml_bytes: bytes, root, item_count: int, source_url: str) -> dict:
    meta = extract_feed_meta(root)
    return {
        "id": uuid.uuid4().hex,
        "kind": "xml_feed",
        "sha256": hashlib.sha256(xml_bytes or b"").hexdigest(),
        "size": len(xml_bytes or b""),
        "item_count": int(item_count or 0),
        "source": safe_source(source_url),
        "source_url_sha256": hashlib.sha256(str(source_url or "").encode()).hexdigest()[:32],
        "generated_at": meta["generated_at"],
        "sequence": meta["sequence"],
        "received_at": _now().isoformat(),
    }


async def last_applied(db) -> Optional[dict]:
    rows = await db[MANIFESTS].find({"kind": "xml_feed", "status": "applied"}, {"_id": 0}) \
        .sort("received_at", -1).limit(1).to_list(1)
    return rows[0] if rows else None


def evaluate(manifest: dict, last: Optional[dict]) -> dict:
    """{'ok': bool, 'reasons': [...], 'duplicate': bool}. Saf fonksiyon (test edilebilir)."""
    reasons: list = []
    if not last:
        return {"ok": True, "reasons": [], "duplicate": False, "baseline": None}
    duplicate = manifest.get("sha256") == last.get("sha256")
    prev_n = int(last.get("item_count") or 0)
    n = int(manifest.get("item_count") or 0)
    if not duplicate and prev_n >= MIN_BASELINE_ITEMS and n < prev_n * (1.0 - DROP_THRESHOLD):
        reasons.append(f"item_count_drop:{prev_n}->{n}")
    g_new, g_old = manifest.get("generated_at"), last.get("generated_at")
    if not duplicate and g_new and g_old and g_new < g_old:
        reasons.append(f"stale_generated_at:{g_new}<{g_old}")
    # CHAIN11: yeni hedef eski onayı miras almaz — feed KAYNAĞI (şema/host/yol; token hariç)
    # değiştiyse ilk içe aktarma süper admin onayı (force) ister.
    if last.get("source") and manifest.get("source") and manifest["source"] != last["source"]:
        reasons.append(f"source_changed:{last['source']}->{manifest['source']}")
    s_new, s_old = manifest.get("sequence"), last.get("sequence")
    if not duplicate and s_new is not None and s_old is not None:
        if s_new < s_old:
            reasons.append(f"sequence_regressed:{s_new}<{s_old}")
        elif s_new == s_old:
            reasons.append(f"sequence_reused_with_different_content:{s_new}")
    return {"ok": not reasons, "reasons": reasons, "duplicate": duplicate,
            "baseline": {"id": last.get("id"), "item_count": prev_n, "generated_at": g_old, "sequence": s_old}}


async def _open_orders_with(db, product_ids: list, since_iso: Optional[str] = None) -> list:
    if not product_ids:
        return []
    q = {"items.product_id": {"$in": product_ids[:REPORT_CAP]}, "status": {"$in": _OPEN_ORDER_STATUSES}}
    if since_iso:
        q["created_at"] = {"$gte": since_iso}
    rows = await db.orders.find(q, {"_id": 0, "order_number": 1, "id": 1}).limit(200).to_list(200)
    return [str(r.get("order_number") or r.get("id") or "") for r in rows]


async def rejection_impact(db, feed_ids: set, feed_prices: dict) -> dict:
    """Reddedilen feed uygulansaydı etkilenecek ürünler (+ bu ürünlerin açık siparişleri)."""
    missing = []
    async for p in db.products.find({"source": "xml_feed", "is_active": {"$ne": False},
                                     "xml_id": {"$nin": list(feed_ids)}},
                                    {"_id": 0, "xml_id": 1, "id": 1}).limit(REPORT_CAP):
        missing.append({"xml_id": p.get("xml_id"), "id": p.get("id")})
    price_changes = []
    ids = [i for i in feed_prices if feed_prices.get(i) is not None]
    for off in range(0, len(ids), 500):
        chunk = ids[off:off + 500]
        async for p in db.products.find({"xml_id": {"$in": chunk}}, {"_id": 0, "xml_id": 1, "id": 1, "price": 1}):
            try:
                old = float(p.get("price") or 0)
            except (TypeError, ValueError):
                old = 0.0
            new = feed_prices.get(p.get("xml_id"))
            if new is not None and abs(float(new) - old) > 0.004:
                price_changes.append({"xml_id": p.get("xml_id"), "id": p.get("id"), "old": old, "new": float(new)})
            if len(price_changes) >= REPORT_CAP:
                break
    pids = [x["id"] for x in missing + price_changes if x.get("id")]
    return {"would_go_missing": missing, "would_change_price": price_changes,
            "open_orders": await _open_orders_with(db, pids)}


async def applied_impact(db, *, imported: list, updated: list, price_review: list, price_skipped: list,
                         price_changed_ids: list) -> dict:
    since = (_now() - timedelta(hours=24)).isoformat()
    return {"imported": imported[:REPORT_CAP], "updated": updated[:REPORT_CAP],
            "price_held_for_review": price_review[:REPORT_CAP], "price_missing": price_skipped[:REPORT_CAP],
            "price_changed": price_changed_ids[:REPORT_CAP],
            "open_orders_with_price_change_24h": await _open_orders_with(db, price_changed_ids, since)}


async def record(db, manifest: dict, *, status: str, verdict: dict, report: dict,
                 forced: bool = False, actor: Optional[dict] = None) -> str:
    """Manifest + etkilenen-ürün raporunu yazar. Hata ana akışı bozmaz; rapor id döner."""
    rid = uuid.uuid4().hex
    now = _now().isoformat()
    try:
        await db[REPORTS].insert_one({"id": rid, "manifest_id": manifest.get("id"), "status": status,
                                      "reasons": list(verdict.get("reasons") or []), "forced": bool(forced),
                                      "created_at": now, **(report or {})})
    except Exception as e:  # noqa: BLE001
        logger.warning("feed report yazılamadı: %s", type(e).__name__)
    doc = dict(manifest)
    doc.update({"status": status, "reasons": list(verdict.get("reasons") or []),
                "duplicate": bool(verdict.get("duplicate")), "baseline": verdict.get("baseline"),
                "forced": bool(forced), "actor_id": str((actor or {}).get("id") or ""),
                "report_id": rid, "decided_at": now})
    try:
        await db[MANIFESTS].insert_one(doc)
    except Exception as e:  # noqa: BLE001
        logger.warning("feed manifest yazılamadı: %s", type(e).__name__)
    return rid
