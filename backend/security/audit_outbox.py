"""
Denetim kaydı OUTBOX'ı (CHAIN07 / FIN-E07).

`activity_audit.record_admin_audit` best-effort (kritik OLMAYAN) yolunda hem zincirli hem zincirsiz
yazma başarısız olursa kayıt KAYBOLMAZ:
  1. `audit_outbox` koleksiyonuna bırakılır (audit_logs yazılamıyor ama veritabanı ayaktaysa);
  2. veritabanı da yazılamıyorsa yerel, SINIRLI bir JSONL dosyasına yazılır:
     `backend/data/audit_outbox/outbox-<YYYYMMDD>.jsonl` (env AUDIT_OUTBOX_DIR ile değişebilir;
     toplam tavan AUDIT_OUTBOX_MAX_BYTES, varsayılan 20 MB — tavan dolunca yeni kayıt düşer ve
     sayaç + log tutulur, disk dolmaz).
`flush_audit_outbox()` (zamanlayıcı, 5 dk'da bir) önce yerel dosyaları DB outbox'ına taşır, sonra
outbox'taki kayıtları hash zincirine (security.audit_chain.append_chained) sırayla ekler. Her
aktarılan kayıt `outbox` alanı taşır (ilk deneme zamanı, aktarım zamanı, yol). Kritik (fail-closed)
işlemler bu yolu KULLANMAZ — onlar denetim yazılamazsa hiç yapılmaz (run_audited).
Kayıtlar zaten redakte edilmiştir (activity_audit.redact_value); burada ek kişisel veri yazılmaz.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

OUTBOX = "audit_outbox"
_DEFAULT_DIR = Path(__file__).resolve().parents[1] / "data" / "audit_outbox"
_LOCK = threading.Lock()
_STATS = {"local_written": 0, "local_dropped": 0}


def outbox_dir() -> Path:
    return Path(os.environ.get("AUDIT_OUTBOX_DIR") or _DEFAULT_DIR)


def max_local_bytes() -> int:
    try:
        return int(os.environ.get("AUDIT_OUTBOX_MAX_BYTES") or 20 * 1024 * 1024)
    except ValueError:
        return 20 * 1024 * 1024


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _local_files() -> list:
    d = outbox_dir()
    if not d.is_dir():
        return []
    return sorted(p for p in d.glob("outbox-*.jsonl") if p.is_file())


def local_backlog_bytes() -> int:
    return sum(p.stat().st_size for p in _local_files())


def _write_local(rec: dict) -> bool:
    line = (json.dumps({"record": rec, "stashed_at": _now()}, ensure_ascii=False, default=str) + "\n").encode()
    with _LOCK:
        if local_backlog_bytes() + len(line) > max_local_bytes():
            _STATS["local_dropped"] += 1
            logger.error("audit outbox: yerel tavan dolu — kayıt düştü (toplam düşen=%s)", _STATS["local_dropped"])
            return False
        d = outbox_dir()
        d.mkdir(parents=True, exist_ok=True)
        path = d / f"outbox-{datetime.now(timezone.utc).strftime('%Y%m%d')}.jsonl"
        with open(path, "ab") as f:
            f.write(line)
            f.flush()
            try:
                os.fsync(f.fileno())
            except OSError:
                pass
        _STATS["local_written"] += 1
    return True


async def stash(db, rec: dict) -> str:
    """Yazılamayan denetim kaydını bırakır. Dönüş: "db" | "local" | "" (ikisi de olmadı)."""
    rec = {k: v for k, v in dict(rec or {}).items() if k not in ("_id", "chain_error", "chain_seq", "prev_hash", "hash")}
    try:
        await db[OUTBOX].insert_one({"id": rec.get("id"), "record": rec, "stashed_at": _now(), "via": "db",
                                     "attempts": 0})
        return "db"
    except Exception as e:  # noqa: BLE001
        logger.warning("audit outbox: DB'ye yazılamadı (%s) → yerel dosya", type(e).__name__)
    try:
        return "local" if _write_local(rec) else ""
    except Exception as e:  # noqa: BLE001
        logger.error("audit outbox: yerel dosyaya da yazılamadı: %s", type(e).__name__)
        return ""


async def _drain_local_to_db(db) -> int:
    """Yerel JSONL dosyalarını DB outbox'ına taşır (dosya ancak TAMAMI taşınınca silinir)."""
    moved = 0
    for path in _local_files():
        try:
            lines = path.read_bytes().splitlines()
        except OSError:
            continue
        pending = []
        for raw in lines:
            if not raw.strip():
                continue
            try:
                pending.append(json.loads(raw.decode("utf-8")))
            except Exception:  # noqa: BLE001 — yarım yazılmış satır
                logger.warning("audit outbox: bozuk yerel satır atlandı (%s)", path.name)
        for item in pending:
            rec = item.get("record") or {}
            await db[OUTBOX].update_one(
                {"id": rec.get("id")},
                {"$setOnInsert": {"id": rec.get("id"), "record": rec, "stashed_at": item.get("stashed_at") or _now(),
                                  "via": "local", "attempts": 0}},
                upsert=True)
            moved += 1
        with _LOCK:
            try:
                path.unlink()
            except OSError:
                pass
    return moved


async def flush_audit_outbox(db=None, max_items: int = 500) -> dict:
    """ZAMANLAYICI İŞİ: bekleyen denetim kayıtlarını hash zincirine aktarır. Asla istisna fırlatmaz."""
    if db is None:
        from routes.deps import db as _db
        db = _db
    out = {"ok": True, "from_local": 0, "chained": 0, "failed": 0, "pending": 0}
    try:
        out["from_local"] = await _drain_local_to_db(db)
    except Exception as e:  # noqa: BLE001 — Mongo hâlâ yok → yerel dosyalar yerinde kalır
        out.update(ok=False, error=f"local_drain:{type(e).__name__}")
        return out
    try:
        from security.audit_chain import append_chained
        rows = await db[OUTBOX].find({}, {"_id": 0}).sort("stashed_at", 1).to_list(max_items)
        for row in rows:
            rec = dict(row.get("record") or {})
            if not rec.get("id"):
                await db[OUTBOX].delete_one({"id": row.get("id")})
                continue
            # Aynı kayıt (id) zincire zaten girdiyse (önceki turda silme başarısız) tekrar ekleme.
            if await db.audit_logs.find_one({"id": rec["id"], "chain_seq": {"$exists": True}}, {"_id": 1}):
                await db[OUTBOX].delete_one({"id": row.get("id")})
                continue
            rec["outbox"] = {"stashed_at": row.get("stashed_at"), "replayed_at": _now(), "via": row.get("via")}
            try:
                await append_chained(db, rec)
            except Exception as e:  # noqa: BLE001
                out["failed"] += 1
                await db[OUTBOX].update_one({"id": row.get("id")},
                                            {"$inc": {"attempts": 1},
                                             "$set": {"last_error": type(e).__name__, "last_try_at": _now()}})
                continue
            await db[OUTBOX].delete_one({"id": row.get("id")})
            out["chained"] += 1
        out["pending"] = await db[OUTBOX].count_documents({})
    except Exception as e:  # noqa: BLE001
        out.update(ok=False, error=type(e).__name__)
    out["local_backlog_bytes"] = local_backlog_bytes()
    out["local_dropped_total"] = _STATS["local_dropped"]
    if out["chained"] or out["from_local"]:
        logger.warning("audit outbox: %s kayıt zincire aktarıldı (yerelden %s)", out["chained"], out["from_local"])
    return out


async def outbox_status(db) -> dict:
    try:
        pending = await db[OUTBOX].count_documents({})
    except Exception:  # noqa: BLE001
        pending = None
    return {"pending_db": pending, "local_backlog_bytes": local_backlog_bytes(),
            "local_dropped_total": _STATS["local_dropped"]}

