"""
iyzico komisyon / hesaba geçiş (settlement) defteri + mutabakatı (COM12) — günlük, SALT-OKUNUR.

AMAÇ: iyzico hesap özetiyle aylık mutabakat elle yapılıyordu. Bu modül iyzico raporlama API'sinden
günlük işlem listesini çekip `payment_ledger` koleksiyonuna (iyzico işlem kimliğiyle İDEMPOTENT)
yazar, her ay için `payment_ledger_months` özetini (brüt / komisyon / net / iade / adet /
uyuşmazlık) yeniden hesaplar ve siparişlerle karşılaştırıp uyuşmazlıkları
`payment_ledger_discrepancies` koleksiyonuna kaydeder (her uyuşmazlık için YALNIZ BİR KEZ uyarı).

KULLANILAN iyzico UÇLARI (resmî SDK'lardaki yollar; imza payment.py `_v2_headers` ile — IYZWSv2):
  * GET  /v2/reporting/payment/transactions?transactionDate=YYYY-MM-DD&page=N   (günlük işlemler)
  * GET  /v2/reporting/payment/details?paymentConversationId=...                (eksik kayıt teyidi)
  * POST /reporting/settlement/payoutcompleted  {"date": "YYYY-MM-DD 00:00:00"} (hesaba geçenler)
  GET isteklerinde imza = HMACSHA256(secret, randomKey + yol(sorgu dizesi HARİÇ) + gövde(boş)).
  Yanıt alan adları sağlayıcıya göre değişebildiği için tüm alanlar birden çok adayla okunur
  (`_first`); bulunamayan alan "bilinmiyor" sayılır ve uyuşmazlık ÜRETMEZ.

DEĞİŞMEZLER (CLAUDE.md §ÖDEME): bu modül siparişe, ödeme durumuna, stoğa, kupona ASLA yazmaz —
yalnız payment_ledger*, settings(payment_ledger_state), integration_logs ve alerts yazılır.
Kimlik bilgisi HER sağlayıcı çağrısından önce yeniden okunur (PRIV11). Kişisel veri saklanmaz
(yalnız beyaz listedeki parasal/kimlik alanları; ham iyzico yanıtı saklanmaz).

Uyuşmazlık türleri:
  ledger_payment_without_paid_order  defterde ödeme var, eşleşen "ödendi" sipariş yok (tam iptal/iade hariç)
  paid_order_without_ledger          dünün ödenmiş kart siparişi defterde yok (iyzico ayrıntısıyla teyit)
  amount_mismatch                    sipariş tutarı ≠ iyzico paidPrice (> 0.01; taksit vade farkı hariç)
  refund_not_recorded_on_order       iyzico'da iade var, siparişte kayıtlı değil
  refund_missing_in_ledger           siparişte iade var, iyzico defterinde yok
  commission_rate_high               tek çekim komisyon oranı İşletme Kuralı üst sınırını aşıyor
  payout_overdue                     beklenen hesaba geçiş tarihi + tolerans geçti, ödeme görünmüyor
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import date as _date, datetime, timedelta, timezone
from typing import Optional
from urllib.parse import urlencode

import httpx

logger = logging.getLogger(__name__)

STATE_ID = "payment_ledger_state"
TX_PATH = "/v2/reporting/payment/transactions"
DETAIL_PATH = "/v2/reporting/payment/details"
PAYOUT_PATH = "/reporting/settlement/payoutcompleted"

# iyzico rapor günleri Türkiye saatiyle (UTC+3; 2016'dan beri yaz saati uygulaması yok).
TR_TZ = timezone(timedelta(hours=3))
_TIMEOUT = 30
_MAX_PAGES = 200            # gün başına sayfa güvenlik sınırı
_MAX_RERUN_DAYS = 31
_MAX_DETAIL_LOOKUPS = 50    # tur başına "defterde yok" teyit çağrısı
_MAX_INDIVIDUAL_ALERTS = 20  # tur başına tekil uyarı; fazlası tek özet uyarıda
_BOUNDARY_GRACE = timedelta(minutes=15)  # gün sınırında iade zaman farkı toleransı
_EPS = 0.01

_CARD_METHODS = ["credit_card", "card", "kredi_karti", "kart", "iyzico", "creditcard"]
_PAID_LIKE = {"paid", "refunded", "partially_refunded"}
_REVERSAL = ("refund", "cancel")
_OUTFLOW = ("refund", "cancel", "chargeback")   # tutarı satıcıdan çıkan hareketler (net'ten düşülür)

KIND_LEVEL = {
    "ledger_payment_without_paid_order": "critical",
    "paid_order_without_ledger": "critical",
    "amount_mismatch": "critical",
    "refund_not_recorded_on_order": "critical",
    "refund_missing_in_ledger": "critical",
    "commission_rate_high": "warning",
    "payout_overdue": "warning",
}
KIND_LABEL = {
    "ledger_payment_without_paid_order": "iyzico'da ödeme var, ödenmiş sipariş yok",
    "paid_order_without_ledger": "Ödenmiş kart siparişi iyzico defterinde yok",
    "amount_mismatch": "Sipariş tutarı ile iyzico tahsilatı uyuşmuyor",
    "refund_not_recorded_on_order": "iyzico'daki iade siparişte kayıtlı değil",
    "refund_missing_in_ledger": "Siparişteki iade iyzico defterinde yok",
    "commission_rate_high": "Komisyon oranı beklenenden yüksek",
    "payout_overdue": "Hesaba geçiş gecikti",
}


# ════════════════════════════════════════ yardımcılar ════════════════════════════════════════
def _f(v) -> Optional[float]:
    try:
        return round(float(v), 2) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _first(d: dict, *keys):
    for k in keys:
        v = d.get(k)
        if v not in (None, ""):
            return v
    return None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _tr_today() -> _date:
    return _now().astimezone(TR_TZ).date()


def _day_bounds_utc(d: str) -> tuple:
    """TR günü → [başlangıç, bitiş) UTC ISO dizeleri (siparişlerdeki isoformat ile karşılaştırılabilir)."""
    y, m, dd = (int(x) for x in d.split("-"))
    start = datetime(y, m, dd, tzinfo=TR_TZ)
    return start.astimezone(timezone.utc).isoformat(), (start + timedelta(days=1)).astimezone(timezone.utc).isoformat()


def _tr_date_of(iso: str) -> str:
    try:
        dt = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(TR_TZ).date().isoformat()
    except Exception:  # noqa: BLE001
        return ""


def _date_only(v) -> Optional[str]:
    """iyzico tarih alanı (\"2026-10-03 14:00:00\" / ISO / epoch ms) → YYYY-MM-DD; okunamazsa None."""
    if v in (None, ""):
        return None
    if isinstance(v, (int, float)) or (isinstance(v, str) and v.isdigit() and len(v) >= 12):
        try:
            return datetime.fromtimestamp(int(v) / 1000, tz=timezone.utc).astimezone(TR_TZ).date().isoformat()
        except Exception:  # noqa: BLE001
            return None
    s = str(v).strip()[:10]
    try:
        return _date.fromisoformat(s).isoformat()
    except ValueError:
        return None


def _truthy_settled(v) -> Optional[bool]:
    if v is None or v == "":
        return None
    s = str(v).strip().lower()
    if s in ("1", "true", "settled", "success", "completed", "paid", "payout_completed", "done"):
        return True
    if s in ("0", "false", "not_settled", "pending", "waiting", "none"):
        return False
    return None


def classify_type(raw) -> str:
    s = str(raw or "").strip().upper()
    if "CHARGEBACK" in s or "DISPUTE" in s:
        return "chargeback"
    if "REFUND" in s or s in ("IADE", "İADE"):
        return "refund"
    if "CANCEL" in s or "VOID" in s or s == "IPTAL":
        return "cancel"
    if "PAYMENT" in s or s in ("AUTH", "SALE", "POSTAUTH", "CAPTURE", "ODEME"):
        return "payment"
    return "other"


def normalize_transaction(row: dict, report_date: str) -> dict:
    """iyzico günlük rapor satırı → defter belgesi (beyaz liste; kişisel veri yok). Saf fonksiyon."""
    raw_type = str(_first(row, "transactionType", "type") or "")
    typ = classify_type(raw_type)
    payment_id = str(_first(row, "paymentId") or "")
    payment_tx_id = str(_first(row, "paymentTxId", "paymentTransactionId") or "")
    tx_time = str(_first(row, "transactionDate", "createdDate", "date") or "")
    tx_id = str(_first(row, "transactionId", "id") or "")
    if not tx_id:
        h = hashlib.sha256("|".join([raw_type, payment_id, payment_tx_id, tx_time,
                                     str(row.get("paidPrice")), str(row.get("price"))]).encode()).hexdigest()
        tx_id = "h" + h[:24]
    price = _f(row.get("price"))
    if typ in _OUTFLOW:
        amt = _f(_first(row, "refundPrice", "paidPrice", "price", "amount"))
        amount = abs(amt) if amt is not None else 0.0
    else:
        amt = _f(_first(row, "paidPrice", "price"))
        amount = amt if amt is not None else 0.0
    fee = _f(row.get("iyziCommissionFee"))
    rate_amt = _f(row.get("iyziCommissionRateAmount"))
    has_comm = fee is not None or rate_amt is not None
    commission = round((fee or 0.0) + (rate_amt or 0.0), 2)
    payout = _f(row.get("merchantPayoutAmount"))
    blockage = _f(_first(row, "blockageRateAmountMerchant", "blockageAmount"))
    try:
        installment = int(_first(row, "installment") or 1)
    except (TypeError, ValueError):
        installment = 1
    if typ in _OUTFLOW:
        net = -abs(payout) if payout is not None else -amount
    else:
        net = payout if payout is not None else round(amount - commission, 2)
    rate_pct = None
    if typ == "payment" and amount > 0:
        if rate_amt is not None:
            rate_pct = round(rate_amt / amount * 100, 3)
        elif has_comm:
            rate_pct = round((commission - (fee or 0.0)) / amount * 100, 3)
    unexplained = None
    if typ == "payment" and payout is not None and has_comm:
        unexplained = round(amount - commission - payout, 2)
    return {
        "id": f"iyzico:{typ}:{tx_id}",
        "provider": "iyzico",
        "tx_id": tx_id,
        "type": typ,
        "raw_type": raw_type[:40],
        "date": report_date,
        "month": report_date[:7],
        "transaction_time": tx_time[:40],
        "payment_id": payment_id,
        "payment_tx_id": payment_tx_id,
        "conversation_id": str(_first(row, "conversationId", "paymentConversationId") or "")[:80],
        "basket_id": str(_first(row, "basketId") or "")[:80],
        "currency": str(_first(row, "currency", "transactionCurrency") or "TRY").upper()[:8],
        "installment": installment,
        "amount": amount,
        "price": price,
        "iyzi_commission_fee": fee,
        "iyzi_commission_rate_amount": rate_amt,
        "commission": commission if has_comm else None,
        "commission_rate_pct": rate_pct,
        "merchant_commission_rate": _f(row.get("merchantCommissionRate")),
        "merchant_commission_amount": _f(row.get("merchantCommissionRateAmount")),
        "blockage_amount": blockage,
        "net_payout": round(net, 2),
        "unexplained": unexplained,
        "expected_payout_date": _date_only(_first(row, "payoutDate", "settlementDate", "expectedPayoutDate",
                                                  "blockageResolvedDate")),
        "report_settled": _truthy_settled(_first(row, "settlementStatus", "payoutStatus", "settled",
                                                 "isSettled", "payoutCompleted")),
    }


# ════════════════════════════════════════ iyzico çağrıları ═══════════════════════════════════
def signed_headers(settings: dict, path: str, body_str: str = "") -> dict:
    """IYZWSv2: imza sorgu dizesi HARİÇ yol üzerinden (resmî SDK'lar `url.split('?')[0]`)."""
    from routes.payment import _v2_headers
    h = _v2_headers(settings["api_key"], settings["api_secret"], path.split("?")[0], body_str)
    h["Accept"] = "application/json"
    return h


async def _settings() -> Optional[dict]:
    from routes.payment import _get_iyzico_settings
    try:
        return await _get_iyzico_settings()
    except Exception:  # noqa: BLE001 — yapılandırılmamış/iptal edilmiş anahtar → çağrı YAPILMAZ
        return None


def _parse(resp) -> tuple:
    if not resp.headers.get("content-type", "").startswith("application/json"):
        return None, f"http_{resp.status_code}_non_json"
    try:
        data = resp.json()
    except Exception:  # noqa: BLE001
        return None, "bad_json"
    if not isinstance(data, dict):
        return None, "bad_json"
    if str(data.get("status") or "").lower() != "success":
        return data, f"provider:{str(data.get('errorCode') or resp.status_code)[:20]}"
    return data, ""


async def _iyz_get(path: str, params: dict) -> tuple:
    settings = await _settings()       # PRIV11: her çağrıdan önce yeniden okunur
    if not settings:
        return None, "credentials_unavailable"
    url = f"{settings['base_url']}{path}?{urlencode(params)}"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as c:
            resp = await c.get(url, headers=signed_headers(settings, path, ""))
    except Exception as e:  # noqa: BLE001
        return None, f"request:{type(e).__name__}"
    return _parse(resp)


async def _iyz_post(path: str, payload: dict) -> tuple:
    settings = await _settings()
    if not settings:
        return None, "credentials_unavailable"
    body_str = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as c:
            resp = await c.post(f"{settings['base_url']}{path}", content=body_str.encode("utf-8"),
                                headers=signed_headers(settings, path, body_str))
    except Exception as e:  # noqa: BLE001
        return None, f"request:{type(e).__name__}"
    return _parse(resp)


async def fetch_transactions(day: str) -> tuple:
    """(satırlar, hata) — tüm sayfalar; herhangi bir sayfa başarısızsa gün 'eksik' sayılır."""
    rows: list = []
    page, total_pages = 1, 1
    while page <= total_pages:
        if page > _MAX_PAGES:
            return rows, "too_many_pages"
        data, err = await _iyz_get(TX_PATH, {"transactionDate": day, "page": page})
        if err:
            return rows, err
        rows.extend(t for t in (data.get("transactions") or []) if isinstance(t, dict))
        try:
            total_pages = int(data.get("totalPageCount") or 1)
        except (TypeError, ValueError):
            total_pages = 1
        page += 1
    return rows, ""


async def fetch_payouts(day: str) -> tuple:
    payload = {"locale": "tr", "conversationId": f"ledger-payout-{day}", "date": f"{day} 00:00:00"}
    data, err = await _iyz_post(PAYOUT_PATH, payload)
    if err:
        return None, err
    return [p for p in (data.get("payoutCompletedTransactions") or []) if isinstance(p, dict)], ""


async def provider_has_payment(order: dict) -> str:
    """'found' / 'not_found' / 'unavailable' — iyzico ödeme ayrıntısı (salt-okunur teyit)."""
    data, err = await _iyz_get(DETAIL_PATH, {"paymentConversationId": str(order.get("id") or "")})
    if err:
        return "not_found" if (data and str(data.get("status") or "") == "failure") else "unavailable"
    pays = data.get("payments")
    if isinstance(pays, list):
        return "found" if pays else "not_found"
    return "found" if data.get("paymentId") else "not_found"


# ════════════════════════════════════════ uyuşmazlık kaydı ═══════════════════════════════════
class _Recorder:
    def __init__(self, db, now: datetime):
        self.db, self.now = db, now
        self.checked: dict = {}
        self.flagged: dict = {}
        self.new: list = []

    def check(self, kind: str, ref: str) -> None:
        self.checked.setdefault(kind, set()).add(ref)

    async def flag(self, kind: str, ref: str, *, month: str, details: dict, order: Optional[dict] = None,
                   payment_id: str = "") -> None:
        self.check(kind, ref)
        self.flagged.setdefault(kind, set()).add(ref)
        did = f"{kind}:{ref}"
        now = self.now.isoformat()
        await self.db.payment_ledger_discrepancies.update_one(
            {"id": did},
            {"$set": {"kind": kind, "ref": ref, "level": KIND_LEVEL.get(kind, "warning"), "month": month,
                      "order_id": (order or {}).get("id"), "order_number": (order or {}).get("order_number"),
                      "payment_id": payment_id, "details": details, "status": "open", "last_seen_at": now},
             "$setOnInsert": {"id": did, "first_seen_at": now},
             "$inc": {"occurrences": 1}},
            upsert=True)
        # Tam olarak BİR KEZ uyarı: atomik talep (eşzamanlı iki tur aynı uyuşmazlığı iki kez bildirmez).
        claimed = await self.db.payment_ledger_discrepancies.find_one_and_update(
            {"id": did, "alerted_at": {"$exists": False}}, {"$set": {"alerted_at": now}},
            projection={"_id": 0, "id": 1})
        if claimed:
            self.new.append({"id": did, "kind": kind, "ref": ref, "month": month, "details": details,
                             "order_number": (order or {}).get("order_number"), "payment_id": payment_id})

    async def resolve_clean(self) -> int:
        n = 0
        for kind, refs in self.checked.items():
            clean = list(refs - self.flagged.get(kind, set()))
            for i in range(0, len(clean), 500):
                r = await self.db.payment_ledger_discrepancies.update_many(
                    {"kind": kind, "status": "open", "ref": {"$in": clean[i:i + 500]}},
                    {"$set": {"status": "resolved", "resolved_at": self.now.isoformat()}})
                n += int(getattr(r, "modified_count", 0) or 0)
        return n

    async def send_alerts(self) -> int:
        if not self.new:
            return 0
        from security.alerts import send_alert
        sent = 0
        for d in self.new[:_MAX_INDIVIDUAL_ALERTS]:
            lvl = KIND_LEVEL.get(d["kind"], "warning")
            body = (f"Tür: {KIND_LABEL.get(d['kind'], d['kind'])}\n"
                    f"Sipariş: {d.get('order_number') or '-'} · iyzico ödeme: {d.get('payment_id') or '-'} · "
                    f"Ay: {d.get('month')}\n"
                    f"Ayrıntı: {json.dumps(d.get('details') or {}, ensure_ascii=False)[:600]}\n\n"
                    "Sipariş ve ödeme durumu otomatik DEĞİŞTİRİLMEDİ; iyzico panelinden doğrulayın. "
                    "Liste: Yönetim → iyzico mutabakat defteri (uyuşmazlıklar).")
            try:
                await send_alert("payment_ledger_discrepancy",
                                 f"iyzico mutabakat defteri: {KIND_LABEL.get(d['kind'], d['kind'])}",
                                 body, level=lvl, fingerprint=f"payment_ledger:{d['id']}",
                                 meta={"discrepancy_id": d["id"], "kind": d["kind"]})
                sent += 1
            except Exception as e:  # noqa: BLE001
                logger.warning("payment ledger alert failed: %s", type(e).__name__)
        rest = self.new[_MAX_INDIVIDUAL_ALERTS:]
        if rest:
            lines = [f"• {KIND_LABEL.get(d['kind'], d['kind'])}: {d.get('order_number') or d['ref']}" for d in rest[:100]]
            try:
                await send_alert("payment_ledger_discrepancy",
                                 f"iyzico mutabakat defteri: {len(rest)} yeni uyuşmazlık daha",
                                 "\n".join(lines), level="critical",
                                 fingerprint=f"payment_ledger:batch:{self.now.isoformat()}",
                                 meta={"count": len(rest)})
                sent += 1
            except Exception as e:  # noqa: BLE001
                logger.warning("payment ledger batch alert failed: %s", type(e).__name__)
        return sent


# ════════════════════════════════════════ mutabakat kontrolleri ══════════════════════════════
def recorded_refunds(order: dict, before_iso: Optional[str] = None) -> tuple:
    """(kesinleşmiş iyzico iade toplamı [before_iso'dan önce], sonucu belirsiz iade toplamı)."""
    done, unknown = 0.0, 0.0
    for r in order.get("refunds") or []:
        if not isinstance(r, dict) or str(r.get("provider") or "iyzico") != "iyzico":
            continue
        v = _f(r.get("net_refund") if r.get("net_refund") not in (None, "") else r.get("amount")) or 0.0
        st = str(r.get("status") or "done")
        if st == "unknown":
            unknown += v
            continue
        if st != "done":
            continue
        ts = str(r.get("refunded_at") or r.get("requested_at") or "")
        if before_iso is not None and ts and ts >= before_iso:
            continue
        done += v
    return round(done, 2), round(unknown, 2)


_ORDER_PROJ = {"_id": 0, "id": 1, "order_number": 1, "total": 1, "payment_status": 1, "status": 1,
               "payment_method": 1, "iyzico_payment_id": 1, "paid_at": 1, "refunds": 1,
               "paid_with_gift_card_only": 1, "iyzico_retrieve_response.paidPrice": 1}


async def _link_orders(db, rows: list) -> dict:
    """payment_id/conversationId/basketId → sipariş. Dönüş: {row_id: order}."""
    pids = sorted({r["payment_id"] for r in rows if r.get("payment_id")})
    convs = sorted({r["conversation_id"] for r in rows if r.get("conversation_id")})
    baskets = sorted({r["basket_id"] for r in rows if r.get("basket_id")})
    ors = []
    if pids:
        ors.append({"iyzico_payment_id": {"$in": pids}})
    if convs:
        ors.append({"id": {"$in": convs}})
    if baskets:
        ors.append({"order_number": {"$in": baskets}})
    by_pid, by_id, by_no = {}, {}, {}
    if ors:
        async for o in db.orders.find({"$or": ors}, _ORDER_PROJ):
            if o.get("iyzico_payment_id"):
                by_pid[str(o["iyzico_payment_id"])] = o
            by_id[str(o.get("id") or "")] = o
            if o.get("order_number"):
                by_no[str(o["order_number"])] = o
    out = {}
    for r in rows:
        o = (by_pid.get(r.get("payment_id") or "\0") or by_id.get(r.get("conversation_id") or "\0")
             or by_no.get(r.get("basket_id") or "\0") or by_id.get(r.get("basket_id") or "\0"))
        if o:
            out[r["id"]] = o
    return out


async def _rule(db, key: str, default):
    try:
        from business_rules import get_rule
        v = await get_rule(db, key, default)
        return float(v) if v not in (None, "") else float(default)
    except Exception:  # noqa: BLE001
        return float(default)


async def reconcile(db, dates: list, ok_dates: set, rec: _Recorder, state: dict, summary: dict) -> None:
    """Pencere günleri için tüm kontroller. Siparişe YAZMAZ (yalnız okur)."""
    rows = await db.payment_ledger.find({"date": {"$in": list(dates)}}, {"_id": 0}).to_list(100000)
    links = await _link_orders(db, rows)
    # Bağlanan siparişi deftere işle (yalnız defter belgesi güncellenir).
    for r in rows:
        o = links.get(r["id"])
        oid, ono = (o or {}).get("id"), (o or {}).get("order_number")
        if r.get("order_id") != oid or r.get("order_number") != ono:
            await db.payment_ledger.update_one({"id": r["id"]}, {"$set": {"order_id": oid, "order_number": ono}})
            r["order_id"], r["order_number"] = oid, ono

    pay_groups: dict = {}
    for r in rows:
        if r["type"] == "payment":
            pay_groups.setdefault(r.get("payment_id") or r["id"], []).append(r)
    all_pids = sorted({r.get("payment_id") for r in rows if r.get("payment_id")})
    reversal_total: dict = {}
    if all_pids:
        async for x in db.payment_ledger.find({"payment_id": {"$in": all_pids}, "type": {"$in": list(_REVERSAL)}},
                                              {"_id": 0, "payment_id": 1, "amount": 1, "date": 1}):
            reversal_total[x["payment_id"]] = round(reversal_total.get(x["payment_id"], 0.0) + (x.get("amount") or 0), 2)

    # 1) defterde ödeme ↔ ödenmiş sipariş, 2) tutar
    for key, grp in pay_groups.items():
        order = next((links[r["id"]] for r in grp if r["id"] in links), None)
        gross = round(sum(r.get("amount") or 0 for r in grp), 2)
        month = grp[0]["month"]
        pid = grp[0].get("payment_id") or ""
        rec.check("ledger_payment_without_paid_order", key)
        rec.check("amount_mismatch", key)
        reversed_amt = reversal_total.get(pid, 0.0) if pid else 0.0
        if not order or str(order.get("payment_status") or "") not in _PAID_LIKE:
            if reversed_amt >= gross - _EPS:
                continue   # iyzico'da tamamen iptal/iade edilmiş ödeme — sipariş ödenmemiş olabilir
            await rec.flag("ledger_payment_without_paid_order", key, month=month, order=order, payment_id=pid,
                           details={"reason": "order_not_found" if not order else "order_not_paid",
                                    "ledger_paid": gross, "date": grp[0]["date"],
                                    "order_payment_status": (order or {}).get("payment_status"),
                                    "order_status": (order or {}).get("status")})
            continue
        snap = _f(((order.get("iyzico_retrieve_response") or {}) or {}).get("paidPrice"))
        total = _f(order.get("total")) or 0.0
        expected = snap if snap is not None else total
        # Kuruş cinsinden TAM SAYI karşılaştırma. Eskiden float `abs(a-b) > 0.01` 1 kuruşluk farkı
        # bazen uyuşmazlık sayıyordu (9031.64 − 9031.63 = 0.0100000000002 > 0.01). Ayrıca iyzico taksitli
        # ödemede vade farkını kalemlere 8 haneyle dağıtır; defter her kalemi 2 haneye yuvarladığından
        # toplam, ödeme-düzeyi paidPrice'tan kalem başına en çok yarım kuruş sapabilir. Tolerans:
        # en az 1 kuruş, çok kalemli ödemede ⌈kalem/2⌉ kuruş. Gerçek uyuşmazlıklar (TL düzeyi) yine işaretlenir.
        _diff_k = abs(int(round(gross * 100)) - int(round(expected * 100)))
        _tol_k = max(1, (len(grp) + 1) // 2)
        if _diff_k > _tol_k:
            inst = max(int(r.get("installment") or 1) for r in grp)
            if snap is None and inst > 1 and gross > total:
                continue   # taksit vade farkı: tahsilat sipariş toplamından fazla olabilir
            await rec.flag("amount_mismatch", key, month=month, order=order, payment_id=pid,
                           details={"order_total": total, "recorded_paid": snap, "ledger_paid": gross,
                                    "installment": inst})

    # 3) komisyon oranı (yalnız tek çekim; sabit işlem ücreti hariç)
    max_rate = await _rule(db, "payment.expected_commission_rate_max", 5)
    if max_rate > 0:
        for r in rows:
            if r["type"] != "payment" or r.get("commission_rate_pct") is None or int(r.get("installment") or 1) > 1:
                continue
            rec.check("commission_rate_high", r["id"])
            if r["commission_rate_pct"] > max_rate + 1e-9:
                await rec.flag("commission_rate_high", r["id"], month=r["month"], order=links.get(r["id"]),
                               payment_id=r.get("payment_id") or "",
                               details={"rate_pct": r["commission_rate_pct"], "max_rate_pct": max_rate,
                                        "amount": r.get("amount"), "commission": r.get("commission")})

    # 4) iadeler (iki yön) — pencere sonu: son başarılı günün bitişi
    win_end = _day_bounds_utc(max(dates))[1]
    win_start = _day_bounds_utc(min(dates))[0]
    loose_end = (datetime.fromisoformat(win_end) - _BOUNDARY_GRACE).isoformat()
    refund_pids = set(r.get("payment_id") for r in rows if r.get("payment_id"))
    ref_orders = {}
    async for o in db.orders.find(
            {"iyzico_payment_id": {"$nin": [None, ""]},
             "$or": [{"refunds.refunded_at": {"$gte": win_start, "$lt": win_end}},
                     {"refunds.requested_at": {"$gte": win_start, "$lt": win_end}}]}, _ORDER_PROJ).limit(5000):
        ref_orders[str(o["iyzico_payment_id"])] = o
        refund_pids.add(str(o["iyzico_payment_id"]))
    for r in rows:   # iade satırı siparişe, aynı paymentId'li (bağlanmış) ödeme satırı üzerinden bağlanır
        o = links.get(r["id"])
        if o and r.get("payment_id"):
            ref_orders.setdefault(r["payment_id"], o)
    for o in links.values():
        if o.get("iyzico_payment_id"):
            ref_orders.setdefault(str(o["iyzico_payment_id"]), o)
    unlinked = sorted(p for p in refund_pids if p not in ref_orders)
    if unlinked:   # pencere dışındaki (eski) ödeme satırı bağlanmış olabilir
        async for x in db.payment_ledger.find({"payment_id": {"$in": unlinked}, "order_id": {"$nin": [None, ""]}},
                                              {"_id": 0, "payment_id": 1, "order_id": 1}):
            o = await db.orders.find_one({"id": x["order_id"]}, _ORDER_PROJ)
            if o:
                ref_orders.setdefault(x["payment_id"], o)
    max_date = max(dates)
    for pid in sorted(refund_pids):
        led = await db.payment_ledger.find({"payment_id": pid, "date": {"$lte": max_date}},
                                           {"_id": 0, "type": 1, "amount": 1, "month": 1}).to_list(1000)
        L = round(sum(x.get("amount") or 0 for x in led if x.get("type") in _REVERSAL), 2)
        has_payment_row = any(x.get("type") == "payment" for x in led)
        order = ref_orders.get(pid)
        month = (led[0]["month"] if led else max_date[:7])
        rec.check("refund_not_recorded_on_order", pid)
        rec.check("refund_missing_in_ledger", pid)
        if not order:
            pay_gross = round(sum(x.get("amount") or 0 for x in led if x.get("type") == "payment"), 2)
            if pay_gross > 0 and L >= pay_gross - _EPS:
                continue   # siparişe hiç bağlanmamış ama iyzico'da tamamen geri alınmış ödeme
            if L > _EPS:
                await rec.flag("refund_not_recorded_on_order", pid, month=month, payment_id=pid,
                               details={"reason": "order_not_found", "ledger_refunds": L})
            continue
        if str(order.get("payment_status") or "") not in _PAID_LIKE:
            continue   # ödenmemiş sipariş: kontrol 1 (tam iptal edilmemişse) zaten işaretler
        rec_all, unknown = recorded_refunds(order)
        rec_loose, _ = recorded_refunds(order, before_iso=loose_end)
        if L > rec_all + _EPS:
            await rec.flag("refund_not_recorded_on_order", pid, month=month, order=order, payment_id=pid,
                           details={"ledger_refunds": L, "recorded_refunds": rec_all,
                                    "unverified_refunds_on_order": unknown})
        elif has_payment_row and L < rec_loose - _EPS:
            await rec.flag("refund_missing_in_ledger", pid, month=month, order=order, payment_id=pid,
                           details={"ledger_refunds": L, "recorded_refunds": rec_loose})

    # 5) dünün (pencerenin) ödenmiş kart siparişleri defterde mi? (yalnız tam çekilmiş günler)
    lookups = 0
    for d in sorted(ok_dates):
        s, e = _day_bounds_utc(d)
        q = {"payment_status": {"$in": sorted(_PAID_LIKE)}, "payment_method": {"$in": _CARD_METHODS},
             "paid_with_gift_card_only": {"$ne": True}, "paid_at": {"$gte": s, "$lt": e}}
        async for o in db.orders.find(q, _ORDER_PROJ).limit(5000):
            oid = str(o.get("id") or "")
            rec.check("paid_order_without_ledger", oid)
            ors = [{"conversation_id": oid}]
            if o.get("iyzico_payment_id"):
                ors.append({"payment_id": str(o["iyzico_payment_id"])})
            if o.get("order_number"):
                ors.append({"basket_id": str(o["order_number"])})
            hit = await db.payment_ledger.find_one({"type": "payment", "$or": ors}, {"_id": 0, "id": 1})
            if hit:
                continue
            provider = "unchecked"
            if lookups < _MAX_DETAIL_LOOKUPS:
                lookups += 1
                provider = await provider_has_payment(o)
            await rec.flag("paid_order_without_ledger", oid, month=d[:7], order=o,
                           payment_id=str(o.get("iyzico_payment_id") or ""),
                           details={"paid_date": d, "order_total": _f(o.get("total")), "provider_detail": provider})
    summary["detail_lookups"] = lookups

    # 6) hesaba geçiş gecikmesi — yalnız settlement bilgisi gerçekten görüldüyse ve ilgili günlerin
    #    hesaba-geçiş listesi eksiksiz çekildiyse (aksi halde yanlış alarm üretmemek için atlanır).
    grace = int(await _rule(db, "payment.ledger_payout_grace_days", 2))
    sdates = set((state.get("settlement_dates") or {}).keys())
    if grace > 0 and state.get("settlement_seen") and sdates:
        cutoff = (_tr_today() - timedelta(days=grace)).isoformat()
        async for r in db.payment_ledger.find(
                {"type": "payment", "settled": {"$ne": True}, "expected_payout_date": {"$nin": [None, ""], "$lt": cutoff}},
                {"_id": 0}).limit(2000):
            exp = _date.fromisoformat(r["expected_payout_date"])
            span = [(exp + timedelta(days=i)).isoformat() for i in range(grace + 1)]
            if not all(x in sdates for x in span):
                continue
            rec.check("payout_overdue", r["id"])
            await rec.flag("payout_overdue", r["id"], month=r["month"], payment_id=r.get("payment_id") or "",
                           order={"id": r.get("order_id"), "order_number": r.get("order_number")},
                           details={"expected_payout_date": r["expected_payout_date"], "grace_days": grace,
                                    "net_payout": r.get("net_payout")})
        # bu arada ödenenler (settled) çözülür
        async for r in db.payment_ledger.find({"type": "payment", "settled": True}, {"_id": 0, "id": 1}).limit(20000):
            rec.check("payout_overdue", r["id"])


# ════════════════════════════════════════ aylık özet ═════════════════════════════════════════
def _zero() -> dict:
    return {"gross": 0.0, "commission": 0.0, "commission_refunded": 0.0, "net": 0.0, "payout_expected": 0.0,
            "refunds": 0.0, "chargebacks": 0.0, "count": 0, "refund_count": 0, "cancel_count": 0,
            "chargeback_count": 0, "unexplained": 0.0, "commission_unknown_count": 0}


def rollup_rows(rows: list) -> dict:
    """Saf fonksiyon: ay satırları → {"TRY": {...}, ...} para birimi bazında toplamlar."""
    by: dict = {}
    for r in rows:
        t = by.setdefault(r.get("currency") or "TRY", _zero())
        amt = float(r.get("amount") or 0)
        if r.get("type") == "payment":
            t["count"] += 1
            t["gross"] += amt
            if r.get("commission") is None:
                t["commission_unknown_count"] += 1
            t["commission"] += float(r.get("commission") or 0)
            t["payout_expected"] += float(r.get("net_payout") or 0)
            t["net"] += float(r.get("net_payout") or 0)
            t["unexplained"] += float(r.get("unexplained") or 0)
        elif r.get("type") in _REVERSAL:
            t["refund_count" if r["type"] == "refund" else "cancel_count"] += 1
            t["refunds"] += amt
            t["commission_refunded"] += float(r.get("commission") or 0)
            t["net"] += float(r.get("net_payout") or 0)
        elif r.get("type") == "chargeback":
            t["chargeback_count"] += 1
            t["chargebacks"] += amt
            t["net"] += float(r.get("net_payout") or 0)
    for t in by.values():
        for k, v in list(t.items()):
            if isinstance(v, float):
                t[k] = round(v, 2)
        t["commission_rate_pct"] = round(t["commission"] / t["gross"] * 100, 3) if t["gross"] else None
    return by


async def rebuild_month(db, month: str) -> dict:
    rows = await db.payment_ledger.find({"month": month}, {"_id": 0}).to_list(200000)
    by = rollup_rows(rows)
    main = by.get("TRY") or _zero()
    disc: dict = {}
    async for d in db.payment_ledger_discrepancies.find({"month": month, "status": "open"}, {"_id": 0, "kind": 1}):
        disc[d["kind"]] = disc.get(d["kind"], 0) + 1
    doc = {"id": month, "month": month, "currency": "TRY",
           **{k: main.get(k) for k in ("gross", "commission", "commission_refunded", "net", "payout_expected",
                                       "refunds", "chargebacks", "count", "refund_count", "cancel_count",
                                       "chargeback_count", "unexplained", "commission_rate_pct",
                                       "commission_unknown_count")},
           "by_currency": by, "discrepancies": sum(disc.values()), "discrepancies_by_kind": disc,
           "rows": len(rows), "updated_at": _now().isoformat()}
    await db.payment_ledger_months.update_one({"id": month}, {"$set": doc}, upsert=True)
    return doc


# ════════════════════════════════════════ ana iş ═════════════════════════════════════════════
def _date_range(date_from: str, date_to: str) -> list:
    a, b = _date.fromisoformat(date_from), _date.fromisoformat(date_to)
    if b < a:
        a, b = b, a
    return [(a + timedelta(days=i)).isoformat() for i in range((b - a).days + 1)]


async def _ingest_day(db, day: str, now_iso: str) -> tuple:
    raw, err = await fetch_transactions(day)
    n = 0
    for row in raw:
        doc = normalize_transaction(row, day)
        settled_flag = doc.pop("report_settled")
        set_fields = {**doc, "updated_at": now_iso}
        on_insert = {"first_ingested_at": now_iso}
        if settled_flag is True:
            set_fields.update(settled=True, settled_source="report")
        else:
            on_insert["settled"] = False
        await db.payment_ledger.update_one({"id": doc["id"]}, {"$set": set_fields, "$setOnInsert": on_insert},
                                           upsert=True)
        n += 1
    return n, err


async def _ingest_payouts(db, day: str, now_iso: str) -> tuple:
    items, err = await fetch_payouts(day)
    if err:
        return None, err
    marked = 0
    for p in items:
        txid = str(_first(p, "paymentTransactionId", "paymentTxId") or "")
        pid = str(_first(p, "paymentId") or "")
        q = {"payment_tx_id": txid} if txid else ({"payment_id": pid} if pid else None)
        if not q:
            continue
        r = await db.payment_ledger.update_many(
            {**q, "type": "payment"},
            {"$set": {"settled": True, "settled_at": day, "settled_source": "payoutcompleted",
                      "payout_amount": _f(p.get("payoutAmount"))}})
        marked += int(getattr(r, "modified_count", 0) or 0)
    return {"items": len(items), "marked": marked}, ""


async def run_payment_ledger(days: int = 2, date_from: Optional[str] = None, date_to: Optional[str] = None,
                             trigger: str = "scheduler") -> dict:
    """ZAMANLAYICI İŞİ (günlük 05:41 UTC) + süper admin yeniden çalıştırma. Varsayılan: önceki 2 TR günü.
    Dönüş: özet (kişisel veri yok). Asla istisna fırlatmaz. Siparişe/ödeme durumuna YAZMAZ."""
    from routes.deps import db
    now = _now()
    now_iso = now.isoformat()
    if date_from and date_to:
        dates = _date_range(date_from, date_to)[:_MAX_RERUN_DAYS]
    else:
        today = _tr_today()
        dates = [(today - timedelta(days=i)).isoformat() for i in range(max(1, int(days or 2)), 0, -1)]
    summary = {"ok": True, "trigger": trigger, "dates": dates, "at": now_iso, "rows": 0, "failed_dates": [],
               "new_discrepancies": 0, "open_discrepancies": 0, "resolved": 0, "alerts": 0,
               "settlement": {}}
    try:
        if not await _settings():
            summary.update(skipped="iyzico_not_configured")
            await _save_state(db, summary, now_iso, {}, {})
            return summary
        state = await db.settings.find_one({"id": STATE_ID}, {"_id": 0}) or {}
        ok_dates, day_info, settle_info = set(), {}, {}
        for d in dates:
            n, err = await _ingest_day(db, d, now_iso)
            summary["rows"] += n
            if err:
                summary["failed_dates"].append({"date": d, "error": err})
            else:
                ok_dates.add(d)
                day_info[d] = {"at": now_iso, "rows": n}
            pres, perr = await _ingest_payouts(db, d, now_iso)
            if perr:
                summary["settlement"][d] = perr
            else:
                settle_info[d] = {"at": now_iso, **pres}
                summary["settlement"][d] = pres
        if any((v or {}).get("items") for v in settle_info.values()):
            state["settlement_seen"] = True
        state.setdefault("settlement_dates", {}).update(settle_info)
        summary["ok"] = not summary["failed_dates"]
        rec = _Recorder(db, now)
        await reconcile(db, dates, ok_dates, rec, state, summary)
        summary["resolved"] = await rec.resolve_clean()
        summary["new_discrepancies"] = len(rec.new)
        summary["alerts"] = await rec.send_alerts()
        months = sorted({d[:7] for d in dates})
        summary["months"] = {m: {k: v for k, v in (await rebuild_month(db, m)).items()
                                 if k in ("gross", "commission", "net", "refunds", "count", "discrepancies")}
                             for m in months}
        summary["open_discrepancies"] = await db.payment_ledger_discrepancies.count_documents({"status": "open"})
        if summary["failed_dates"]:
            try:
                from security.alerts import send_alert
                await send_alert(
                    "payment_ledger_ingest_failed", "iyzico mutabakat defteri: işlem listesi alınamadı",
                    "Şu günler için iyzico günlük işlem raporu eksiksiz alınamadı: "
                    + ", ".join(f"{x['date']} ({x['error']})" for x in summary["failed_dates"])
                    + "\nBu günlerin 'defterde yok' kontrolü yapılmadı; Yönetim → iyzico mutabakat defteri → "
                      "Yeniden çalıştır ile tekrar deneyin.",
                    level="warning", fingerprint=f"payment_ledger_ingest:{','.join(x['date'] for x in summary['failed_dates'])}")
            except Exception:  # noqa: BLE001
                pass
        await _save_state(db, summary, now_iso, day_info, state)
    except Exception as e:  # noqa: BLE001
        summary.update(ok=False, error=type(e).__name__)
        logger.error("[payment-ledger] hata: %s", type(e).__name__)
        try:
            await _save_state(db, summary, now_iso, {}, {})
        except Exception:  # noqa: BLE001
            pass
    return summary


async def _save_state(db, summary: dict, now_iso: str, day_info: dict, state: dict) -> None:
    upd = {"id": STATE_ID, "last": summary, "last_run_at": now_iso}
    if summary.get("ok") and not summary.get("skipped"):
        upd["last_success_at"] = now_iso
    for d, v in (day_info or {}).items():
        upd[f"ingested_dates.{d}"] = v
    if state.get("settlement_seen"):
        upd["settlement_seen"] = True
    # Hesaba-geçiş günleri: son 120 gün tutulur (belge sınırsız büyümesin).
    keep_from = (_tr_today() - timedelta(days=120)).isoformat()
    sd = {k: v for k, v in (state.get("settlement_dates") or {}).items() if k >= keep_from}
    if sd:
        upd["settlement_dates"] = sd
    await db.settings.update_one({"id": STATE_ID}, {"$set": upd}, upsert=True)
    await db.integration_logs.insert_one({
        "service": "iyzico", "event": "payment_ledger_run",
        "status": "skipped" if summary.get("skipped") else ("success" if summary.get("ok") else "error"),
        **{k: v for k, v in summary.items() if k not in ("ok", "months", "settlement")},
        "created_at": _now().isoformat()})


# ════════════════════════════════════════ öz-test ════════════════════════════════════════════
async def selftest_status(db) -> tuple:
    """Güvenlik öz-testi: son BAŞARILI defter çalışması 36 saatten yeni mi?"""
    iyz = await db.settings.find_one({"id": "iyzico"}, {"_id": 0, "api_key": 1, "api_secret": 1}) or {}
    if not iyz.get("api_key") or not iyz.get("api_secret"):
        return "ok", "iyzico yapılandırılmamış — defter işi gerekmiyor"
    st = await db.settings.find_one({"id": STATE_ID}, {"_id": 0, "last_success_at": 1, "last_run_at": 1}) or {}
    last = str(st.get("last_success_at") or "")
    if not last:
        return "warn", "iyzico mutabakat defteri henüz başarıyla çalışmadı (günlük 05:41 UTC)"
    if last < (_now() - timedelta(hours=36)).isoformat():
        return "fail", f"son başarılı iyzico defter çalışması 36 saatten eski ({last[:16]})"
    n = await db.payment_ledger_discrepancies.count_documents({"status": "open"})
    if n:
        return "warn", f"defter güncel ({last[:16]}); açık mutabakat uyuşmazlığı: {n}"
    return "ok", f"defter güncel ({last[:16]}), açık uyuşmazlık yok"


# ════════════════════════════════════════ yönetim uçları (süper admin) ═══════════════════════
from fastapi import APIRouter, Depends, HTTPException  # noqa: E402
from pydantic import BaseModel  # noqa: E402

from routes.deps import require_super_admin  # noqa: E402

router = APIRouter(prefix="/admin/payment-ledger", tags=["payment-ledger"])


def _valid_month(month: Optional[str]) -> str:
    m = (month or "").strip() or _tr_today().isoformat()[:7]
    try:
        datetime.strptime(m, "%Y-%m")
    except ValueError:
        raise HTTPException(status_code=400, detail="Ay biçimi YYYY-AA olmalı")
    return m


@router.get("/summary")
async def ledger_summary(month: Optional[str] = None, current_user: dict = Depends(require_super_admin)):
    """Aylık defter özeti (brüt / komisyon / net / iade / adet / uyuşmazlık) + son çalışma."""
    from routes.deps import db
    if month:
        m = _valid_month(month)
        doc = await db.payment_ledger_months.find_one({"id": m}, {"_id": 0}) or {"id": m, "month": m, "rows": 0}
        months = [doc]
    else:
        months = await db.payment_ledger_months.find({}, {"_id": 0}).sort("id", -1).limit(24).to_list(24)
    st = await db.settings.find_one({"id": STATE_ID}, {"_id": 0, "last": 1, "last_success_at": 1}) or {}
    return {"months": months, "last_run": st.get("last"), "last_success_at": st.get("last_success_at"),
            "open_discrepancies": await db.payment_ledger_discrepancies.count_documents({"status": "open"})}


@router.get("/discrepancies")
async def ledger_discrepancies(status: Optional[str] = "open", kind: Optional[str] = None,
                               month: Optional[str] = None, limit: int = 200,
                               current_user: dict = Depends(require_super_admin)):
    from routes.deps import db
    q: dict = {}
    if status in ("open", "resolved"):
        q["status"] = status
    if kind:
        if kind not in KIND_LEVEL:
            raise HTTPException(status_code=400, detail="Bilinmeyen uyuşmazlık türü")
        q["kind"] = kind
    if month:
        q["month"] = _valid_month(month)
    items = await db.payment_ledger_discrepancies.find(q, {"_id": 0}).sort("last_seen_at", -1) \
        .limit(max(1, min(int(limit or 200), 1000))).to_list(1000)
    return {"items": items, "count": len(items), "kinds": KIND_LABEL}


def _cell(v):
    s = "" if v is None else str(v)
    return ("'" + s) if s[:1] in ("=", "+", "-", "@", "\t", "\r") and not _is_number(s) else s


def _is_number(s: str) -> bool:
    try:
        float(s)
        return True
    except ValueError:
        return False


@router.get("/export.csv")
async def ledger_export_csv(month: Optional[str] = None, current_user: dict = Depends(require_super_admin)):
    """Ayın defter satırları + toplam satırı (CSV). ACİL ANAHTAR freeze_exports açıkken 423."""
    from security_switches import require_switch_off, emit_security_event
    await require_switch_off("freeze_exports")
    import csv
    import io
    from fastapi.responses import Response
    from activity_audit import run_audited
    from routes.deps import db
    m = _valid_month(month)

    async def _build():
        rows = await db.payment_ledger.find({"month": m}, {"_id": 0}).sort([("date", 1), ("id", 1)]).to_list(200000)
        buf = io.StringIO()
        w = csv.writer(buf)
        cols = ["date", "type", "tx_id", "payment_id", "order_number", "currency", "installment", "amount",
                "commission", "commission_rate_pct", "net_payout", "expected_payout_date", "settled"]
        w.writerow(cols)
        for r in rows:
            w.writerow([_cell(r.get(c)) for c in cols])
        t = (rollup_rows(rows).get("TRY") or _zero())
        w.writerow([])
        w.writerow(["TOPLAM (TRY)", "brut", t["gross"], "komisyon", t["commission"], "iade", t["refunds"],
                    "itiraz", t["chargebacks"], "net", t["net"], "adet", t["count"]])
        return buf.getvalue(), len(rows)

    body, n = await run_audited(db, action="payment_ledger.export", entity_type="payment_ledger",
                                entity_id=m, perform=_build, current_user=current_user,
                                source="admin.payment_ledger", metadata={"month": m})
    await emit_security_event("payment_ledger_export", current_user, {"export": f"iyzico-defter-{m}.csv", "rows": n})
    return Response(content=body, media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="iyzico-defter-{m}.csv"',
                             "Cache-Control": "no-store"})


class RerunBody(BaseModel):
    date_from: str
    date_to: str


@router.post("/rerun")
async def ledger_rerun(body: RerunBody, current_user: dict = Depends(require_super_admin)):
    """Tarih aralığı için defteri yeniden çek + mutabakat (en çok 31 gün; denetimli, fail-closed)."""
    from activity_audit import run_audited
    from routes.deps import db
    try:
        a, b = _date.fromisoformat(body.date_from), _date.fromisoformat(body.date_to)
    except ValueError:
        raise HTTPException(status_code=400, detail="Tarih biçimi YYYY-AA-GG olmalı")
    if b < a:
        raise HTTPException(status_code=400, detail="Bitiş tarihi başlangıçtan önce olamaz")
    if (b - a).days + 1 > _MAX_RERUN_DAYS:
        raise HTTPException(status_code=400, detail=f"En çok {_MAX_RERUN_DAYS} gün seçilebilir")
    if b >= _tr_today():
        raise HTTPException(status_code=400, detail="Yalnız tamamlanmış (dünden önceki) günler çekilebilir")

    async def _perform():
        return await run_payment_ledger(date_from=a.isoformat(), date_to=b.isoformat(), trigger="admin_rerun")

    return await run_audited(db, action="payment_ledger.rerun", entity_type="payment_ledger",
                             entity_id=f"{a.isoformat()}..{b.isoformat()}", perform=_perform,
                             current_user=current_user, source="admin.payment_ledger",
                             metadata={"date_from": a.isoformat(), "date_to": b.isoformat()})
