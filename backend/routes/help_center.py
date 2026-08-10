"""
help_center.py — Eğitim / Yardım Merkezi içeriği (çok-kiracılı / SaaS).

AMAÇ:
  Ticimax'in "destekalanı"na benzer yardım/yönerge alanı. İçerik KODA GÖMÜLÜ
  DEĞİLDİR — admin panelinden düzenlenebilir. Böylece bu sistemi kullanan her
  firma kendi bilgilerini, kendi yönergelerini girebilir; başka bir firma
  Facette'i kullandığında varsayılan metinler yerine kendi içeriğini yazar.

TASARIM:
  - db.settings id="help_content" tek dokümanda { sections: [...] } saklanır.
  - Kayıt yoksa uç `sections: null` döner → frontend paketlenmiş VARSAYILAN
    içeriği (lib/trainingContent.js) gösterir. Admin ilk kez kaydettiğinde bu
    varsayılan bir kopya DB'ye yazılır ve firma üzerinde düzenler.
  - section şeması: { key, title, icon, intro, items: [ {title, path, what,
    where, how:[...], tips:[...]} ] }
"""
from fastapi import APIRouter, Depends, HTTPException
from datetime import datetime, timezone
from .deps import db, require_admin

admin_router = APIRouter(prefix="/admin/help-content", tags=["admin-help-center"])
public_router = APIRouter(prefix="/help-content", tags=["help-center"])

_DOC_ID = "help_content"
_MAX_BYTES = 500_000  # ~500KB güvenlik sınırı (aşırı büyük içerik reddedilir)


def _clean_str(v, limit=4000):
    return (str(v) if v is not None else "").strip()[:limit]


def _sanitize_sections(sections):
    """Gelen içeriği güvenli şekilde normalize eder (beklenen şema dışı alanları atar)."""
    if not isinstance(sections, list):
        raise HTTPException(status_code=400, detail="sections bir liste olmalı")
    out = []
    for s in sections[:60]:  # makul üst sınır
        if not isinstance(s, dict):
            continue
        items = []
        for it in (s.get("items") or [])[:200]:
            if not isinstance(it, dict):
                continue
            how = it.get("how") or []
            tips = it.get("tips") or []
            items.append({
                "title": _clean_str(it.get("title"), 200),
                "path": _clean_str(it.get("path"), 300),
                "what": _clean_str(it.get("what"), 2000),
                "where": _clean_str(it.get("where"), 800),
                "how": [_clean_str(x, 600) for x in how if str(x or "").strip()][:30],
                "tips": [_clean_str(x, 600) for x in tips if str(x or "").strip()][:20],
            })
        out.append({
            "key": _clean_str(s.get("key"), 60) or _clean_str(s.get("title"), 60).lower().replace(" ", "-"),
            "title": _clean_str(s.get("title"), 120),
            "icon": _clean_str(s.get("icon"), 40),
            "intro": _clean_str(s.get("intro"), 600),
            "items": items,
        })
    return out


@admin_router.get("")
async def get_help_content_admin(current_user: dict = Depends(require_admin)):
    """Kayıtlı yardım içeriği (düzenleme için). Kayıt yoksa sections=null döner →
    frontend paketlenmiş varsayılanı gösterir/düzenlemeye başlar."""
    doc = await db.settings.find_one({"id": _DOC_ID}, {"_id": 0}) or {}
    return {
        "sections": doc.get("sections"),
        "is_custom": bool(doc.get("sections") is not None),
        "updated_by": doc.get("updated_by"),
        "updated_at": doc.get("updated_at"),
    }


@admin_router.put("")
async def put_help_content(payload: dict, current_user: dict = Depends(require_admin)):
    """Yardım içeriğini kaydeder. payload: { sections: [...] }.
    Firma kendi yönergelerini buradan yazar; içerik DB'de saklanır."""
    import json as _json
    sections = _sanitize_sections((payload or {}).get("sections"))
    if len(_json.dumps(sections, ensure_ascii=False).encode("utf-8")) > _MAX_BYTES:
        raise HTTPException(status_code=413, detail="İçerik çok büyük (500KB sınırı).")
    await db.settings.update_one(
        {"id": _DOC_ID},
        {"$set": {
            "id": _DOC_ID, "sections": sections,
            "updated_by": current_user.get("email"),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }},
        upsert=True,
    )
    return {"success": True, "section_count": len(sections)}


@admin_router.delete("")
async def reset_help_content(current_user: dict = Depends(require_admin)):
    """Özel içeriği siler → panel yeniden paketlenmiş varsayılana döner."""
    await db.settings.delete_one({"id": _DOC_ID})
    return {"success": True, "message": "Varsayılana döndürüldü"}


@public_router.get("")
async def get_help_content():
    """Panelin okuduğu içerik. Kayıt yoksa sections=null → frontend varsayılanı gösterir."""
    doc = await db.settings.find_one({"id": _DOC_ID}, {"_id": 0}) or {}
    return {"sections": doc.get("sections")}
