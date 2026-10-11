"""
chargebacks.py — Ödeme itirazı (chargeback / dispute) kayıt ve takip (COM11 / FIN-E03).

Kart sahibinin bankası bir tahsilata itiraz ettiğinde (iyzico/banka bildirimi) personel itirazı
siparişe bağlı olarak kaydeder, son savunma tarihini (deadline) ve delil notlarını takip eder,
sonucu (kazanıldı / kaybedildi / kabul edildi) işler.

YETKİ: `returns.refund_pay` (para/uyum işlemi) ya da süper admin ('*').

DEĞİŞMEZ (CLAUDE.md #1): Bu modül siparişin `status` / `payment_status` alanlarına ASLA yazmaz.
Siparişe yalnız ayrı, ödeme mantığının okumadığı alanlar yazılır:
  orders.chargeback_open (bool), orders.chargebacks (özet listesi), orders.chargeback_updated_at.
Kaybedilen itirazın muhasebe/iade etkisi ayrı süreçte (iade/finans ekranı) ele alınır.

Uçlar (prefix /api/admin/chargebacks):
  GET    ""                    — liste + filtre (status, q=sipariş no/banka ref, due_within_days, overdue)
  POST   ""                    — yeni itiraz kaydı (siparişe bağlı)
  GET    /order/{order_id}     — bir siparişin itirazları
  GET    /{chargeback_id}      — ayrıntı (geçmiş dahil)
  PATCH  /{chargeback_id}      — durum / delil notu / son tarih / tutar güncelle

Zamanlanmış hatırlatma: remind_chargeback_deadlines() — son tarihi 3 gün içinde olan (ya da geçmiş)
AÇIK itirazlar için günde bir kez süper adminlere e-posta uyarısı (security.alerts.send_alert).
"""
from __future__ import annotations

import re
from datetime import datetime, timezone, timedelta, date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from .deps import db, logger, generate_id, require_permission, write_audit_log, safe_str

router = APIRouter(prefix="/admin/chargebacks", tags=["admin-chargebacks"])

_PERM = require_permission("returns.refund_pay")
STATUSES = ("open", "won", "lost", "accepted")
STATUS_LABELS = {"open": "Açık", "won": "Kazanıldı", "lost": "Kaybedildi", "accepted": "Kabul edildi"}
_DATE_RX = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_MAX_NOTE = 4000


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_deadline(v) -> str:
    """'YYYY-MM-DD' (ya da ISO tarih-saat) → 'YYYY-MM-DD'. Boş → ''."""
    s = str(v or "").strip()
    if not s:
        return ""
    s10 = s[:10]
    if not _DATE_RX.match(s10):
        raise HTTPException(status_code=400, detail="Son tarih YYYY-AA-GG biçiminde olmalı")
    try:
        date.fromisoformat(s10)
    except ValueError:
        raise HTTPException(status_code=400, detail="Geçersiz son tarih")
    return s10


def _parse_amount(v) -> float:
    try:
        a = round(float(v), 2)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Tutar sayı olmalı")
    if a <= 0:
        raise HTTPException(status_code=400, detail="Tutar sıfırdan büyük olmalı")
    return a


def _summary(cb: dict) -> dict:
    """Siparişe yazılan özet (PII yok)."""
    return {k: cb.get(k) for k in ("id", "amount", "currency", "status", "reason_code", "deadline",
                                   "created_at", "updated_at")}


async def _sync_order(order_id: str) -> None:
    """Siparişteki özet listesi + chargeback_open bayrağını itiraz koleksiyonundan yeniden kurar.
    YALNIZ chargeback_* / chargebacks alanlarına yazar (status/payment_status'a DOKUNMAZ)."""
    rows = await db.chargebacks.find({"order_id": order_id}, {"_id": 0}).sort("created_at", 1).to_list(100)
    await db.orders.update_one({"id": order_id}, {"$set": {
        "chargebacks": [_summary(r) for r in rows],
        "chargeback_open": any(r.get("status") == "open" for r in rows),
        "chargeback_updated_at": _now(),
    }})


# ─────────────────────── COM11 / FIN-E03: açık itirazda iade kilidi ───────────────────────
# Kart sahibinin bankası tahsilata itiraz ettiyse (chargeback AÇIK) para zaten banka sürecinde geri
# dönebilir; aynı siparişe ek iade (iyzico iadesi, iade ödemesi işaretleme, toplu "iade ödendi")
# ÇİFT para çıkışı riskidir → 409. Yalnız SÜPER ADMIN, gerekçe (`chargeback_override_reason`,
# ≥10 karakter) yazarak istisna yapabilir; istisna denetim zincirine + auth denetimine yazılır.
# Kaynak: chargebacks koleksiyonu (otorite) — sipariş üzerindeki chargeback_open yalnız hızlı
# filtre/atomik koşul için kullanılır.
CHARGEBACK_OVERRIDE_FIELD = "chargeback_override_reason"
_OVERRIDE_MIN_LEN = 10


async def open_chargeback_count(order_id: str) -> int:
    oid = safe_str(order_id or "", 64)
    if not oid:
        return 0
    return int(await db.chargebacks.count_documents({"order_id": oid, "status": "open"}))


async def assert_refund_allowed(order_id: str, current_user: Optional[dict], payload: Optional[dict],
                                *, action: str, request: Optional[Request] = None) -> dict:
    """İade/para çıkışı öncesi çağrılır. Açık itiraz yoksa {"override": False}. Varsa süper admin +
    gerekçe ile {"override": True, "reason": ...} (denetimli), aksi halde HTTPException(409/403)."""
    n = await open_chargeback_count(order_id)
    if n <= 0:
        return {"override": False}
    reason = safe_str((payload or {}).get(CHARGEBACK_OVERRIDE_FIELD) or "", 500).strip()
    if not reason:
        raise HTTPException(status_code=409, detail=(
            "Bu siparişte AÇIK bir ödeme itirazı (chargeback) var; itiraz sonuçlanana kadar yeni iade "
            "yapılamaz (çift para çıkışı riski). Süper admin gerekçe yazarak istisna yapabilir."))
    try:
        from .deps import get_effective_permissions
        perms = await get_effective_permissions(current_user or {})
    except Exception:
        perms = set()
    if "*" not in perms:
        raise HTTPException(status_code=403, detail=(
            "Açık itirazlı siparişte iade istisnası yalnız süper admin tarafından yapılabilir."))
    if len(reason) < _OVERRIDE_MIN_LEN:
        raise HTTPException(status_code=400, detail=(
            f"İstisna gerekçesi en az {_OVERRIDE_MIN_LEN} karakter olmalı."))
    actor = (current_user or {}).get("email") or (current_user or {}).get("id") or ""
    # Fail-closed: istisna denetim zincirine yazılamazsa iade YAPILMAZ.
    from activity_audit import record_admin_audit
    await record_admin_audit(db, action="chargebacks.refund_override", entity_type="order",
                             entity_id=safe_str(order_id, 64), current_user=current_user, request=request,
                             source="admin.chargebacks", strict=True,
                             metadata={"action": str(action)[:80], "reason": reason, "open_chargebacks": n})
    await write_audit_log("chargeback_refund_override", user_id=(current_user or {}).get("id"), email=actor,
                          success=True, meta={"order_id": safe_str(order_id, 64), "action": str(action)[:80],
                                              "reason": reason[:200], "open_chargebacks": n})
    logger.warning(f"[chargeback] AÇIK itirazda iade istisnası order={order_id} by={actor} action={action}")
    return {"override": True, "reason": reason}


def _with_days_left(cb: dict) -> dict:
    out = dict(cb)
    dl = cb.get("deadline") or ""
    if dl and cb.get("status") == "open":
        try:
            out["days_left"] = (date.fromisoformat(dl) - datetime.now(timezone.utc).date()).days
        except ValueError:
            out["days_left"] = None
    else:
        out["days_left"] = None
    out["status_label"] = STATUS_LABELS.get(cb.get("status") or "", cb.get("status"))
    return out


async def _find_order(order_id: str = "", order_number: str = "") -> dict:
    oid = safe_str(order_id or "", 64)
    ono = safe_str(order_number or "", 64)
    order = None
    proj = {"_id": 0, "id": 1, "order_number": 1, "total": 1, "currency": 1, "payment_status": 1,
            "payment_method": 1, "platform": 1, "iyzico_payment_id": 1, "created_at": 1}
    if oid:
        order = await db.orders.find_one({"id": oid}, proj)
    if not order and ono:
        order = await db.orders.find_one({"order_number": ono}, proj)
    if not order:
        raise HTTPException(status_code=404, detail="Sipariş bulunamadı")
    return order


@router.get("")
async def list_chargebacks(
    status: str = Query("", description="open|won|lost|accepted (virgülle çoklu)"),
    q: str = Query("", description="sipariş no / banka referansı / sebep kodu"),
    due_within_days: Optional[int] = Query(None, ge=0, le=365),
    overdue: bool = Query(False),
    limit: int = Query(100, ge=1, le=500),
    skip: int = Query(0, ge=0),
    current_user: dict = Depends(_PERM),
):
    flt: dict = {}
    sts = [s.strip() for s in (status or "").split(",") if s.strip() in STATUSES]
    if sts:
        flt["status"] = {"$in": sts}
    qq = safe_str(q or "", 80).strip()
    if qq:
        rx = {"$regex": re.escape(qq), "$options": "i"}
        flt["$or"] = [{"order_number": rx}, {"bank_ref": rx}, {"reason_code": rx}]
    today = datetime.now(timezone.utc).date()
    if overdue:
        flt["status"] = "open"
        flt["deadline"] = {"$lt": today.isoformat(), "$nin": [None, ""]}
    elif due_within_days is not None:
        flt["status"] = "open"
        flt["deadline"] = {"$lte": (today + timedelta(days=int(due_within_days))).isoformat(), "$nin": [None, ""]}
    total = await db.chargebacks.count_documents(flt)
    rows = await db.chargebacks.find(flt, {"_id": 0, "history": 0}) \
        .sort([("status", 1), ("deadline", 1), ("created_at", -1)]).skip(skip).limit(limit).to_list(limit)
    counts = {s: 0 for s in STATUSES}
    async for c in db.chargebacks.aggregate([{"$group": {"_id": "$status", "n": {"$sum": 1}}}]):
        if c.get("_id") in counts:
            counts[c["_id"]] = int(c.get("n") or 0)
    open_amount = 0.0
    async for c in db.chargebacks.aggregate([{"$match": {"status": "open"}},
                                             {"$group": {"_id": None, "s": {"$sum": "$amount"}}}]):
        open_amount = round(float(c.get("s") or 0), 2)
    return {"items": [_with_days_left(r) for r in rows], "total": total, "counts": counts,
            "open_amount": open_amount, "statuses": STATUS_LABELS}


@router.get("/order/{order_id}")
async def list_order_chargebacks(order_id: str, current_user: dict = Depends(_PERM)):
    oid = safe_str(order_id, 64)
    rows = await db.chargebacks.find({"order_id": oid}, {"_id": 0}).sort("created_at", -1).to_list(100)
    return {"items": [_with_days_left(r) for r in rows]}


@router.get("/{chargeback_id}")
async def get_chargeback(chargeback_id: str, current_user: dict = Depends(_PERM)):
    cb = await db.chargebacks.find_one({"id": safe_str(chargeback_id, 64)}, {"_id": 0})
    if not cb:
        raise HTTPException(status_code=404, detail="İtiraz kaydı bulunamadı")
    return {"chargeback": _with_days_left(cb)}


@router.post("")
async def create_chargeback(payload: dict, request: Request, current_user: dict = Depends(_PERM)):
    p = payload or {}
    order = await _find_order(p.get("order_id") or "", p.get("order_number") or "")
    amount = _parse_amount(p.get("amount"))
    _total = float(order.get("total") or 0)
    if _total > 0 and amount > round(_total, 2) + 0.01:
        raise HTTPException(status_code=400, detail="İtiraz tutarı sipariş toplamını aşamaz")
    status = str(p.get("status") or "open").strip()
    if status not in STATUSES:
        raise HTTPException(status_code=400, detail="Geçersiz durum")
    now = _now()
    actor = (current_user or {}).get("email") or (current_user or {}).get("id") or ""
    cb = {
        "id": generate_id(),
        "order_id": order["id"],
        "order_number": order.get("order_number") or "",
        "amount": amount,
        "currency": safe_str(p.get("currency") or order.get("currency") or "TRY", 8).upper(),
        "reason_code": safe_str(p.get("reason_code") or "", 64).strip(),
        "bank_ref": safe_str(p.get("bank_ref") or "", 120).strip(),
        "provider": safe_str(p.get("provider") or "iyzico", 32).strip(),
        "deadline": _parse_deadline(p.get("deadline")),
        "status": status,
        "evidence_notes": safe_str(p.get("evidence_notes") or "", _MAX_NOTE),
        "payment_ref": str(order.get("iyzico_payment_id") or "")[:64],
        "created_at": now, "updated_at": now,
        "created_by": actor,
        "resolved_at": now if status != "open" else None,
        "history": [{"at": now, "by": actor, "action": "create", "status": status}],
    }
    async def _perform_create():
        await db.chargebacks.insert_one(dict(cb))
        # Sipariş: yalnız itiraz alanları ($push + $set) — status/payment_status DEĞİŞMEZ (değişmez #1).
        await db.orders.update_one({"id": order["id"]}, {
            "$push": {"chargebacks": _summary(cb)},
            "$set": {"chargeback_open": True if status == "open" else bool(
                await db.chargebacks.count_documents({"order_id": order["id"], "status": "open"})),
                "chargeback_updated_at": now},
        })

    # GÜVENLİK (fail-closed denetim): para/uyum kaydı ÖNCE niyet kaydı olarak denetim zincirine yazılır;
    # yazılamazsa itiraz kaydı OLUŞTURULMAZ (503) — activity_audit.run_audited (intent → işlem → done/failed).
    from activity_audit import run_audited
    await run_audited(db, action="chargebacks.create", entity_type="chargeback", entity_id=cb["id"],
                      perform=_perform_create, current_user=current_user, request=request,
                      source="admin.chargebacks",
                      metadata={"order_number": cb["order_number"], "amount": amount, "status": status})
    await write_audit_log("chargeback_create", user_id=(current_user or {}).get("id"), email=actor,
                          meta={"chargeback_id": cb["id"], "order_number": cb["order_number"],
                                "amount": amount, "status": status})
    logger.info(f"[chargeback] kayıt açıldı order={cb['order_number']} tutar={amount} durum={status}")
    cb.pop("_id", None)
    return {"success": True, "chargeback": _with_days_left(cb)}


@router.patch("/{chargeback_id}")
async def update_chargeback(chargeback_id: str, payload: dict, request: Request,
                            current_user: dict = Depends(_PERM)):
    cid = safe_str(chargeback_id, 64)
    cb = await db.chargebacks.find_one({"id": cid}, {"_id": 0})
    if not cb:
        raise HTTPException(status_code=404, detail="İtiraz kaydı bulunamadı")
    p = payload or {}
    upd: dict = {}
    if "status" in p:
        st = str(p.get("status") or "").strip()
        if st not in STATUSES:
            raise HTTPException(status_code=400, detail="Geçersiz durum")
        upd["status"] = st
        upd["resolved_at"] = _now() if st != "open" else None
    if "amount" in p:
        a = _parse_amount(p.get("amount"))
        order = await db.orders.find_one({"id": cb["order_id"]}, {"_id": 0, "total": 1}) or {}
        _total = float(order.get("total") or 0)
        if _total > 0 and a > round(_total, 2) + 0.01:
            raise HTTPException(status_code=400, detail="İtiraz tutarı sipariş toplamını aşamaz")
        upd["amount"] = a
    if "deadline" in p:
        upd["deadline"] = _parse_deadline(p.get("deadline"))
        upd["reminder_sent_on"] = None  # yeni tarih → hatırlatma yeniden değerlendirilsin
    for fld, mx in (("reason_code", 64), ("bank_ref", 120), ("provider", 32)):
        if fld in p:
            upd[fld] = safe_str(p.get(fld) or "", mx).strip()
    if "evidence_notes" in p:
        upd["evidence_notes"] = safe_str(p.get("evidence_notes") or "", _MAX_NOTE)
    if "outcome_notes" in p:
        upd["outcome_notes"] = safe_str(p.get("outcome_notes") or "", _MAX_NOTE)
    if not upd:
        raise HTTPException(status_code=400, detail="Güncellenecek alan yok")
    now = _now()
    actor = (current_user or {}).get("email") or (current_user or {}).get("id") or ""
    upd["updated_at"] = now
    upd["updated_by"] = actor
    _changed = sorted(k for k in upd if k not in ("updated_at", "updated_by", "resolved_at", "reminder_sent_on"))
    async def _perform_update():
        await db.chargebacks.update_one({"id": cid}, {
            "$set": upd,
            "$push": {"history": {"$each": [{"at": now, "by": actor, "action": "update", "fields": _changed,
                                              **({"status": upd["status"]} if "status" in upd else {})}],
                                  "$slice": -50}},
        })
        await _sync_order(cb["order_id"])

    # GÜVENLİK (fail-closed denetim): güncelleme de önce niyet kaydıyla zincire yazılır (yazılamazsa 503).
    from activity_audit import run_audited
    await run_audited(db, action="chargebacks.update", entity_type="chargeback", entity_id=cid,
                      perform=_perform_update, current_user=current_user, request=request,
                      source="admin.chargebacks",
                      before={k: cb.get(k) for k in _changed if not k.endswith("_notes")},
                      after={k: upd.get(k) for k in _changed if not k.endswith("_notes")},
                      metadata={"order_number": cb.get("order_number"), "fields": _changed})
    await write_audit_log("chargeback_update", user_id=(current_user or {}).get("id"), email=actor,
                          meta={"chargeback_id": cid, "order_number": cb.get("order_number"),
                                "fields": _changed, "status": upd.get("status", cb.get("status"))})
    cb2 = await db.chargebacks.find_one({"id": cid}, {"_id": 0})
    return {"success": True, "chargeback": _with_days_left(cb2 or {})}


# ─────────────────────────────── zamanlanmış hatırlatma ───────────────────────────────────

async def remind_chargeback_deadlines(days: int = 3) -> int:
    """Son tarihi `days` gün içinde olan ya da geçmiş AÇIK itirazlar için süper adminlere günde
    BİR kez e-posta uyarısı (security.alerts.send_alert). Uyarıda müşteri bilgisi YOK (sipariş no,
    tutar, son tarih). Döner: uyarılan itiraz sayısı."""
    today = datetime.now(timezone.utc).date()
    today_s = today.isoformat()
    limit_s = (today + timedelta(days=int(days))).isoformat()
    rows = await db.chargebacks.find(
        {"status": "open", "deadline": {"$nin": [None, ""], "$lte": limit_s},
         "reminder_sent_on": {"$ne": today_s}},
        {"_id": 0, "id": 1, "order_number": 1, "amount": 1, "currency": 1, "deadline": 1,
         "reason_code": 1, "bank_ref": 1}).sort("deadline", 1).to_list(200)
    if not rows:
        return 0
    lines = []
    worst = 99
    for r in rows:
        try:
            dl = (date.fromisoformat(r["deadline"]) - today).days
        except Exception:
            dl = 0
        worst = min(worst, dl)
        when = "SÜRESİ GEÇTİ" if dl < 0 else ("BUGÜN" if dl == 0 else f"{dl} gün kaldı")
        lines.append(f"• Sipariş {r.get('order_number') or '-'} · {float(r.get('amount') or 0):.2f} "
                     f"{r.get('currency') or 'TRY'} · son tarih {r['deadline']} ({when})"
                     + (f" · sebep {r.get('reason_code')}" if r.get("reason_code") else ""))
    title = f"Ödeme itirazı son tarihi yaklaşıyor: {len(rows)} açık itiraz"
    body = ("Aşağıdaki ödeme itirazlarının (chargeback) savunma son tarihi 3 gün içinde ya da geçmiş. "
            "Panel → Siparişler → Ödeme İtirazları ekranından delilleri yükleyip sonucu işleyin.\n\n"
            + "\n".join(lines))
    level = "critical" if worst <= 1 else "warning"
    try:
        from security.alerts import send_alert
    except Exception as e:
        logger.warning(f"[chargeback] uyarı modülü yüklenemedi: {e}")
        return 0
    delivered = False
    try:
        # Alıcılar security.alerts'te çözülür: İşletme Kuralı security.alert_emails → aktif süper
        # adminler → ALERT_TO_EMAIL → firma iletişim adresi (beyaz-etiket; kodda adres yok).
        await send_alert("chargeback_deadline", title, body, level=level,
                         fingerprint=f"chargeback_deadline:{today_s}",
                         meta={"count": len(rows)})
        delivered = True
    except Exception as e:
        logger.warning(f"[chargeback] son tarih uyarısı gönderilemedi: {e}")
    if delivered:
        await db.chargebacks.update_many({"id": {"$in": [r["id"] for r in rows]}},
                                         {"$set": {"reminder_sent_on": today_s}})
    logger.info(f"[chargeback] son tarih uyarısı: {len(rows)} itiraz")
    return len(rows)
