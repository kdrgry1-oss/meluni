"""Tamper-evident admin audit chain + off-database (R2 archive) copy.

Every chained ``audit_logs`` record carries:
    chain_seq  — monotonically increasing sequence number (1, 2, 3 …)
    prev_hash  — hash of the record with chain_seq - 1 (GENESIS for the first one)
    hash       — sha256(prev_hash + canonical_json(record without "_id"/"hash"))

The chain head (last seq + last hash) lives in ONE settings document
(``settings._id == "audit_chain"``) that is advanced with a compare-and-swap
``find_one_and_update`` — concurrent writers on several workers can never fork the chain.
If the record insert fails after the head moved, the sequence number is pushed to
``gaps`` so verification can tell "write failed" apart from "record deleted".

Off-DB copy: ``export_batch_to_archive`` writes new records as JSONL objects to the
PRIVATE archive bucket (services/r2_storage archive_*), key
``denetim/YYYY/MM/DD/<last_seq>.jsonl``; ``verify_archive_object`` re-checks one object
against itself and against the database.

This module never imports routes.deps (no circular import); callers pass ``db``.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import random
from datetime import datetime, timezone, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

CHAIN_DOC_ID = "audit_chain"
GENESIS = "0" * 64
_CAS_ATTEMPTS = 40
_EXPORT_BATCH = 2000
_INFLIGHT_GRACE_SEC = 60


def canonical_json(record: dict) -> str:
    body = {k: v for k, v in (record or {}).items() if k not in ("_id", "hash")}
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def compute_hash(prev_hash: str, record: dict) -> str:
    return hashlib.sha256(((prev_hash or GENESIS) + canonical_json(record)).encode("utf-8")).hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def _chain_state(db) -> dict:
    doc = await db.settings.find_one({"_id": CHAIN_DOC_ID}) or {}
    doc.pop("_id", None)
    return doc


async def _ensure_chain_doc(db) -> None:
    try:
        await db.settings.update_one(
            {"_id": CHAIN_DOC_ID},
            {"$setOnInsert": {"id": CHAIN_DOC_ID, "seq": 0, "head_hash": GENESIS,
                              "created_at": _now().isoformat()}},
            upsert=True)
    except Exception as e:  # DuplicateKey on a concurrent upsert is fine
        logger.debug("audit chain init: %s", type(e).__name__)


async def append_chained(db, record: dict) -> dict:
    """Append ``record`` to ``audit_logs`` as the next chain element. Raises on failure."""
    for attempt in range(_CAS_ATTEMPTS):
        head = await db.settings.find_one({"_id": CHAIN_DOC_ID}, {"seq": 1, "head_hash": 1})
        if head is None:
            await _ensure_chain_doc(db)
            continue
        seq = int(head.get("seq") or 0)
        prev = str(head.get("head_hash") or GENESIS)
        rec = dict(record)
        rec["chain_seq"] = seq + 1
        rec["prev_hash"] = prev
        rec["hash"] = compute_hash(prev, rec)
        moved = await db.settings.find_one_and_update(
            {"_id": CHAIN_DOC_ID, "seq": seq, "head_hash": prev},
            {"$set": {"seq": seq + 1, "head_hash": rec["hash"], "updated_at": _now().isoformat()}},
            projection={"_id": 1})
        if moved is None:
            await asyncio.sleep(random.uniform(0.002, 0.02) * (1 + attempt // 4))
            continue
        try:
            await db.audit_logs.insert_one(dict(rec))
        except Exception:
            try:
                await db.settings.update_one(
                    {"_id": CHAIN_DOC_ID},
                    {"$push": {"gaps": {"$each": [seq + 1], "$slice": -1000}}})
            except Exception:
                pass
            raise
        return rec
    raise RuntimeError("audit chain contention: head could not be advanced")


# ───────────────────────────── verification ─────────────────────────────

async def verify_chain(db, *, full: bool = False, max_records: int = 500000) -> dict:
    """Walk the chain and report problems. Incremental by default (from the last verified
    checkpoint); ``full=True`` starts at seq 1. Updates the checkpoint only when clean."""
    state = await _chain_state(db)
    head_seq = int(state.get("seq") or 0)
    head_hash = str(state.get("head_hash") or GENESIS)
    gaps = set(int(g) for g in (state.get("gaps") or []) if str(g).isdigit() or isinstance(g, int))
    if full or not state.get("verified_seq"):
        start_seq, prev_hash = 0, GENESIS
    else:
        start_seq, prev_hash = int(state.get("verified_seq") or 0), str(state.get("verified_hash") or GENESIS)
    problems: list = []
    known_gaps: list = []
    expected = start_seq + 1
    checked = 0
    last_seq, last_hash = start_seq, prev_hash
    cur = db.audit_logs.find({"chain_seq": {"$gt": start_seq}}, {"_id": 0}).sort("chain_seq", 1)
    async for rec in cur:
        seq = int(rec.get("chain_seq") or 0)
        if seq < expected:
            problems.append({"seq": seq, "type": "duplicate_seq"})
            continue
        while expected < seq:
            if expected in gaps:
                known_gaps.append(expected)
            else:
                problems.append({"seq": expected, "type": "missing"})
            prev_hash = None
            expected += 1
        if compute_hash(rec.get("prev_hash") or GENESIS, rec) != rec.get("hash"):
            problems.append({"seq": seq, "type": "hash_mismatch"})
        if prev_hash is not None and rec.get("prev_hash") != prev_hash:
            problems.append({"seq": seq, "type": "link_mismatch"})
        prev_hash = rec.get("hash")
        last_seq, last_hash = seq, prev_hash
        expected = seq + 1
        checked += 1
        if checked >= max_records or len(problems) >= 200:
            break
    truncated = checked >= max_records
    if not truncated and len(problems) < 200:
        # Tail: head moved further than the records we saw → missing tail (unless in-flight).
        upd = _parse_iso(state.get("updated_at"))
        inflight = bool(upd and (_now() - upd).total_seconds() < _INFLIGHT_GRACE_SEC)
        for s in range(expected, head_seq + 1):
            if s in gaps:
                known_gaps.append(s)
            elif not (inflight and s >= head_seq - 5):
                problems.append({"seq": s, "type": "missing_tail"})
        if last_seq == head_seq and last_hash and last_hash != head_hash:
            problems.append({"seq": head_seq, "type": "head_mismatch"})
    ok = not problems
    now_iso = _now().isoformat()
    upd_set = {"last_verify_at": now_iso, "last_verify_ok": ok, "last_verify_full": bool(full),
               "last_verify_problems": problems[:20], "last_verify_checked": checked}
    if ok and last_seq > start_seq:
        upd_set.update({"verified_seq": last_seq, "verified_hash": last_hash})
    try:
        await db.settings.update_one({"_id": CHAIN_DOC_ID}, {"$set": upd_set})
    except Exception:
        pass
    return {"ok": ok, "checked": checked, "from_seq": start_seq + 1, "to_seq": last_seq,
            "head_seq": head_seq, "problems": problems[:50], "known_gaps": known_gaps[:50],
            "truncated": truncated, "full": bool(full)}


def _parse_iso(v):
    try:
        dt = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


# ───────────────────────────── off-DB archive ─────────────────────────────

async def export_batch_to_archive(db, *, max_batches: int = 5) -> dict:
    """Write not-yet-exported chained records to the private R2 archive as JSONL.
    Single-runner lock in the chain doc (multi-worker safe). Never raises."""
    try:
        from services import r2_storage as _r2
    except Exception as e:  # pragma: no cover
        return {"ok": False, "error": f"r2 import: {type(e).__name__}"}
    if not _r2.archive_enabled():
        return {"ok": False, "skipped": "archive_disabled"}
    await _ensure_chain_doc(db)
    now = _now()
    lock = await db.settings.find_one_and_update(
        {"_id": CHAIN_DOC_ID, "$or": [{"export_lock_until": {"$exists": False}},
                                      {"export_lock_until": {"$lt": now.isoformat()}}]},
        {"$set": {"export_lock_until": (now + timedelta(minutes=10)).isoformat()}},
        projection={"_id": 0, "exported_seq": 1, "exported_hash": 1, "gaps": 1})
    if lock is None:
        return {"ok": True, "skipped": "locked"}
    exported_seq = int(lock.get("exported_seq") or 0)
    gaps = set(int(g) for g in (lock.get("gaps") or []) if isinstance(g, int))
    written, objects = 0, []
    try:
        for _ in range(max_batches):
            rows = await db.audit_logs.find({"chain_seq": {"$gt": exported_seq}}, {"_id": 0}) \
                .sort("chain_seq", 1).limit(_EXPORT_BATCH).to_list(_EXPORT_BATCH)
            if not rows:
                break
            batch, expected, missing = [], exported_seq + 1, []
            for r in rows:
                seq = int(r.get("chain_seq") or 0)
                if seq > expected:
                    # Gap: in-flight insert (young) → stop here and retry next run;
                    # known/old gap → record it in the archive and continue.
                    young = _parse_iso(r.get("created_at"))
                    young = bool(young and (now - young).total_seconds() < _INFLIGHT_GRACE_SEC)
                    if young and not all(s in gaps for s in range(expected, seq)):
                        break
                    missing.extend(range(expected, seq))
                batch.append(r)
                expected = seq + 1
            if not batch:
                break
            lines = [json.dumps({"missing_seq": m, "known_gap": m in gaps}, sort_keys=True) for m in missing]
            lines += [json.dumps(r, sort_keys=True, ensure_ascii=False, default=str) for r in batch]
            first, last = int(batch[0]["chain_seq"]), int(batch[-1]["chain_seq"])
            key = f"denetim/{now:%Y/%m/%d}/{last:012d}.jsonl"
            data = ("\n".join(lines) + "\n").encode("utf-8")
            await asyncio.to_thread(_r2.archive_put, key, data, "application/x-ndjson")
            exported_seq = last
            entry = {"key": key, "first_seq": first, "last_seq": last, "count": len(batch),
                     "last_hash": batch[-1].get("hash"), "missing": missing[:100],
                     "at": _now().isoformat()}
            await db.settings.update_one(
                {"_id": CHAIN_DOC_ID},
                {"$set": {"exported_seq": last, "exported_hash": batch[-1].get("hash"),
                          "last_export_at": entry["at"], "last_export_key": key},
                 "$push": {"exports": {"$each": [entry], "$slice": -500}}})
            written += len(batch)
            objects.append(key)
            if missing:
                logger.error("[audit-chain] export: missing seq %s", missing[:20])
            if len(rows) < _EXPORT_BATCH:
                break
        return {"ok": True, "written": written, "objects": objects, "exported_seq": exported_seq}
    except Exception as e:
        logger.error("[audit-chain] export failed: %s", type(e).__name__)
        try:
            await db.settings.update_one({"_id": CHAIN_DOC_ID},
                                         {"$set": {"last_export_error": f"{type(e).__name__}",
                                                   "last_export_error_at": _now().isoformat()}})
        except Exception:
            pass
        return {"ok": False, "error": type(e).__name__, "written": written, "objects": objects}
    finally:
        try:
            await db.settings.update_one({"_id": CHAIN_DOC_ID}, {"$unset": {"export_lock_until": ""}})
        except Exception:
            pass


async def verify_archive_object(db, key: str) -> dict:
    """Re-verify one archived JSONL object: per-record hash, in-file linkage, and that the
    database still holds the SAME hash for each seq (detects DB edits/deletions after export)."""
    try:
        from services import r2_storage as _r2
    except Exception as e:  # pragma: no cover
        return {"ok": False, "error": f"r2 import: {type(e).__name__}"}
    if not _r2.archive_enabled():
        return {"ok": False, "error": "archive_disabled"}
    key = str(key or "").strip().lstrip("/")
    if not key.startswith("denetim/") or ".." in key:
        return {"ok": False, "error": "invalid_key"}
    data, _ct = await asyncio.to_thread(_r2.archive_get, key)
    if data is None:
        return {"ok": False, "error": "not_found"}
    problems, prev, checked = [], None, 0
    for line in data.decode("utf-8", "replace").splitlines():
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except Exception:
            problems.append({"type": "bad_line"})
            continue
        if "missing_seq" in rec and "chain_seq" not in rec:
            prev = None
            continue
        seq = rec.get("chain_seq")
        if compute_hash(rec.get("prev_hash") or GENESIS, rec) != rec.get("hash"):
            problems.append({"seq": seq, "type": "archive_hash_mismatch"})
        if prev is not None and rec.get("prev_hash") != prev:
            problems.append({"seq": seq, "type": "archive_link_mismatch"})
        prev = rec.get("hash")
        dbrec = await db.audit_logs.find_one({"chain_seq": seq}, {"_id": 0})
        if not dbrec:
            problems.append({"seq": seq, "type": "db_record_missing"})
        elif (dbrec.get("hash") != rec.get("hash")
              or compute_hash(dbrec.get("prev_hash") or GENESIS, dbrec) != rec.get("hash")):
            problems.append({"seq": seq, "type": "db_differs_from_archive"})
        checked += 1
    return {"ok": not problems, "key": key, "checked": checked, "problems": problems[:50]}


async def chain_status(db) -> dict:
    state = await _chain_state(db)
    try:
        from services import r2_storage as _r2
        arch = bool(_r2.archive_enabled())
    except Exception:
        arch = False
    return {
        "head_seq": int(state.get("seq") or 0),
        "exported_seq": int(state.get("exported_seq") or 0),
        "last_export_at": state.get("last_export_at"),
        "last_export_key": state.get("last_export_key"),
        "last_export_error": state.get("last_export_error"),
        "last_export_error_at": state.get("last_export_error_at"),
        "last_verify_at": state.get("last_verify_at"),
        "last_verify_ok": state.get("last_verify_ok"),
        "last_verify_full": state.get("last_verify_full"),
        "last_verify_problems": state.get("last_verify_problems") or [],
        "verified_seq": int(state.get("verified_seq") or 0),
        "known_gaps": len(state.get("gaps") or []),
        "archive_enabled": arch,
        "recent_exports": list(reversed((state.get("exports") or [])[-10:])),
    }
