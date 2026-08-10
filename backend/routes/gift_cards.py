"""
Hediye Çeki / Mağaza Kredisi (C2).

- Admin hediye çeki (kind="gift") veya müşteriye bağlı mağaza kredisi (kind="credit")
  oluşturur; bakiye kısmi kullanılabilir, kalan sonraki siparişe devreder.
- Ödemede kod girilir: sunucu bakiyeyi ATOMİK rezerve eder (create_order içinde),
  sipariş toplamı düşer. Tutarın TAMAMI çekle karşılanırsa sipariş sunucu-tarafı
  değer yakalandığı için paid/confirmed açılır (CLAUDE.md'de belgeli ikinci yol).
- Sipariş iptal/otomatik iptal olursa bakiye idempotent şekilde iade edilir
  (orders._restock_order_once içindeki kanca → refund_gift_card_once).

Güvenlik: istemciye asla bakiye yazdırılmaz-güvenilmez; tüm hesap sunucuda.
Kod tahmin edilemez (secrets). Public check ucu rate-limitli.
"""
import secrets
import string
from datetime import datetime, timezone, timedelta
from typing import Optional

from fastapi import APIRouter, HTTPException, Depends, Request

from .deps import db, logger, require_admin, limiter

router = APIRouter(prefix="/gift-cards", tags=["gift-cards"])

_CODE_ALPHABET = string.ascii_uppercase.replace("O", "").replace("I", "") + "23456789"


def _gen_code() -> str:
    part = lambda n: "".join(secrets.choice(_CODE_ALPHABET) for _ in range(n))
    return f"HED-{part(4)}-{part(4)}-{part(4)}"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _find_active_card(code: str) -> Optional[dict]:
    code = (code or "").strip().upper()
    if not code:
        return None
    card = await db.gift_cards.find_one({"code": code}, {"_id": 0})
    if not card or card.get("status") != "active":
        return None
    exp = card.get("expires_at") or ""
    if exp and exp < _now_iso():
        return None
    return card


async def redeem_gift_card_for_order(code: str, order: dict, payable_total: float) -> dict:
    """create_order içinden çağrılır. Bakiyeyi ATOMİK düşer (yarışa dayanıklı).

    Döner: {"ok": bool, "amount": float, "code": str, "error": str}
    - amount = min(bakiye, payable_total); kart ödemesinde kalan 0<x<1 TL olmasın
      diye gerekirse 1 TL pay bırakılır (iyzico minimum tahsilat).
    """
    card = await _find_active_card(code)
    if not card:
        return {"ok": False, "amount": 0.0, "code": code, "error": "Hediye çeki geçersiz veya süresi dolmuş"}

    # Mağaza kredisi (kind=credit) yalnız sahibinin e-postasıyla kullanılabilir.
    if (card.get("kind") or "gift") == "credit":
        owner = (card.get("customer_email") or "").strip().lower()
        buyer = ((order.get("shipping_address") or {}).get("email") or "").strip().lower()
        if owner and owner != buyer:
            return {"ok": False, "amount": 0.0, "code": code,
                    "error": "Bu mağaza kredisi başka bir hesaba tanımlı"}

    for _attempt in range(2):  # yarış olursa güncel bakiyeyle bir kez daha dene
        balance = round(float(card.get("balance") or 0), 2)
        if balance <= 0:
            return {"ok": False, "amount": 0.0, "code": code, "error": "Hediye çeki bakiyesi tükenmiş"}
        use = round(min(balance, max(0.0, payable_total)), 2)
        if use <= 0:
            return {"ok": False, "amount": 0.0, "code": code, "error": "Uygulanacak tutar yok"}
        # Kartla ödenecek kalan 0 ile 1 TL arasında kalmasın (iyzico min. tahsilat).
        _pm = (order.get("payment_method") or "").lower()
        _is_card = _pm in ("credit_card", "card", "kredi_karti", "kart", "iyzico", "creditcard")
        remaining = round(payable_total - use, 2)
        if _is_card and 0 < remaining < 1.0:
            use = round(payable_total - 1.0, 2)
            if use <= 0:
                return {"ok": False, "amount": 0.0, "code": code,
                        "error": "Tutar hediye çekiyle uyumsuz — farklı ödeme yöntemi deneyin"}
        res = await db.gift_cards.update_one(
            {"code": card["code"], "status": "active", "balance": {"$gte": use}},
            {"$inc": {"balance": -use},
             "$push": {"transactions": {
                 "type": "redeem", "amount": use,
                 "order_id": order.get("id"), "order_number": order.get("order_number"),
                 "at": _now_iso()}},
             "$set": {"last_used_at": _now_iso()}},
        )
        if res.modified_count:
            logger.info(f"[gift-card] {card['code']} → {use} TL rezerve (sipariş {order.get('order_number')})")
            return {"ok": True, "amount": use, "code": card["code"], "error": ""}
        card = await _find_active_card(code)  # yarış: bakiye değişti → tazele
        if not card:
            break
    return {"ok": False, "amount": 0.0, "code": code, "error": "Hediye çeki şu an kullanılamadı, tekrar deneyin"}


async def refund_gift_card_once(order: dict) -> float:
    """Sipariş iptalinde bakiye iadesi — İDEMPOTENT (sipariş bayrağı atomik kilit)."""
    gc = order.get("gift_card") or {}
    amount = round(float(gc.get("amount") or 0), 2)
    code = (gc.get("code") or "").strip().upper()
    if amount <= 0 or not code:
        return 0.0
    lock = await db.orders.update_one(
        {"id": order.get("id"), "gift_card.refunded": {"$ne": True}},
        {"$set": {"gift_card.refunded": True, "gift_card.refunded_at": _now_iso()}},
    )
    if not lock.modified_count:
        return 0.0  # zaten iade edilmiş
    await db.gift_cards.update_one(
        {"code": code},
        {"$inc": {"balance": amount},
         "$push": {"transactions": {
             "type": "refund", "amount": amount,
             "order_id": order.get("id"), "order_number": order.get("order_number"),
             "at": _now_iso()}}},
    )
    logger.info(f"[gift-card] {code} ← {amount} TL iade (sipariş {order.get('order_number')} iptal)")
    return amount


# ── Public: kod doğrulama (ödeme sayfası) ────────────────────────────────────
@router.post("/check")
@(limiter.limit("15/minute") if limiter else (lambda f: f))
async def check_gift_card(request: Request, payload: dict):
    code = (payload or {}).get("code") or ""
    card = await _find_active_card(code)
    if not card:
        return {"valid": False, "error": "Hediye çeki geçersiz veya süresi dolmuş"}
    if (card.get("kind") or "gift") == "credit":
        owner = (card.get("customer_email") or "").strip().lower()
        buyer = ((payload or {}).get("email") or "").strip().lower()
        if owner and owner != buyer:
            return {"valid": False, "error": "Bu mağaza kredisi başka bir hesaba tanımlı"}
    return {"valid": True, "balance": round(float(card.get("balance") or 0), 2),
            "kind": card.get("kind") or "gift"}


# ── Admin ────────────────────────────────────────────────────────────────────
admin_router = APIRouter(prefix="/admin/gift-cards", tags=["gift-cards-admin"])


@admin_router.post("")
async def create_gift_card(payload: dict, current_user: dict = Depends(require_admin)):
    amount = round(float((payload or {}).get("amount") or 0), 2)
    if amount <= 0 or amount > 100000:
        raise HTTPException(status_code=400, detail="Tutar 0'dan büyük olmalı")
    kind = (payload.get("kind") or "gift").lower()
    if kind not in ("gift", "credit"):
        kind = "gift"
    email = (payload.get("customer_email") or "").strip().lower()
    if kind == "credit" and not email:
        raise HTTPException(status_code=400, detail="Mağaza kredisi için müşteri e-postası zorunlu")
    expires_days = int(payload.get("expires_days") or 0)
    code = _gen_code()
    while await db.gift_cards.find_one({"code": code}, {"_id": 1}):
        code = _gen_code()
    doc = {
        "id": secrets.token_hex(8),
        "code": code,
        "kind": kind,
        "initial_amount": amount,
        "balance": amount,
        "currency": "TRY",
        "status": "active",
        "customer_email": email,
        "note": str(payload.get("note") or "")[:300],
        "created_by": current_user.get("email", ""),
        "created_at": _now_iso(),
        "expires_at": (datetime.now(timezone.utc) + timedelta(days=expires_days)).isoformat() if expires_days > 0 else "",
        "transactions": [],
    }
    await db.gift_cards.insert_one({**doc})
    return doc


@admin_router.get("")
async def list_gift_cards(q: str = "", status: str = "", limit: int = 200,
                          current_user: dict = Depends(require_admin)):
    query: dict = {}
    if q:
        import re as _re
        rx = {"$regex": _re.escape(q.strip()), "$options": "i"}
        query["$or"] = [{"code": rx}, {"customer_email": rx}, {"note": rx}]
    if status:
        query["status"] = status
    rows = await db.gift_cards.find(query, {"_id": 0}).sort("created_at", -1).to_list(max(1, min(limit, 1000)))
    return {"items": rows, "count": len(rows)}


@admin_router.put("/{card_id}")
async def update_gift_card(card_id: str, payload: dict, current_user: dict = Depends(require_admin)):
    upd: dict = {}
    if "status" in (payload or {}):
        st = str(payload["status"]).lower()
        if st not in ("active", "disabled"):
            raise HTTPException(status_code=400, detail="Durum active/disabled olabilir")
        upd["status"] = st
    if "note" in (payload or {}):
        upd["note"] = str(payload.get("note") or "")[:300]
    if payload.get("add_balance") is not None:
        delta = round(float(payload["add_balance"]), 2)
        if delta:
            await db.gift_cards.update_one(
                {"id": card_id},
                {"$inc": {"balance": delta},
                 "$push": {"transactions": {"type": "adjust", "amount": delta,
                                            "by": current_user.get("email", ""), "at": _now_iso()}}})
    if upd:
        await db.gift_cards.update_one({"id": card_id}, {"$set": upd})
    card = await db.gift_cards.find_one({"id": card_id}, {"_id": 0})
    if not card:
        raise HTTPException(status_code=404, detail="Hediye çeki bulunamadı")
    return card
