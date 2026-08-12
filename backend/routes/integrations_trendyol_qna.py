"""
=============================================================================
integrations_trendyol_qna.py — Trendyol Q&A + Reviews (extracted)
=============================================================================
Iter37 refactor adımı: integrations.py'den Trendyol Q&A (3 endpoint) ve
Reviews (2 endpoint) kısmı ayrı modüle taşındı (~340 satır).

Helper'lar (`get_trendyol_config`, `get_trendyol_headers`, `log_integration_event`)
hâlâ integrations.py'de — circular import önlemek için lazy/relative import.

Endpoints:
- GET  /api/integrations/trendyol/questions/sync
- GET  /api/integrations/trendyol/questions
- POST /api/integrations/trendyol/questions/{id}/answer
- POST /api/integrations/trendyol/reviews/scrape
- POST /api/integrations/trendyol/reviews/scrape-bulk
=============================================================================
"""
from fastapi import APIRouter, Depends, HTTPException
from datetime import datetime, timezone, timedelta
from typing import List, Optional
import os
import re
import socket
import asyncio
import httpx

from .deps import db, logger, require_admin, generate_id

router = APIRouter(tags=["Integrations - Trendyol Q&A"])


# ==================== TRENDYOL Q&A ====================

@router.get("/trendyol/questions/sync")
async def sync_trendyol_questions(
    days_back: int = 90,
    status: Optional[str] = None,
    current_user: dict = Depends(require_admin)
):
    """Sync questions from Trendyol and store in DB.

    Trendyol QnA Filter API varsayılan olarak son ~14-30 gün döndürür; geçmiş
    soruları çekebilmek için `startDate`/`endDate` (Unix ms) parametreleri
    geçilmelidir. `days_back` (varsayılan 90) bunu kontrol eder.
    """
    from .integrations import get_trendyol_config, get_trendyol_headers

    config = await get_trendyol_config()
    if not config["is_active"]:
        raise HTTPException(status_code=400, detail="Trendyol entegrasyonu yapılandırılmamış")

    supplier_id = config["supplier_id"]
    headers = await get_trendyol_headers()
    if not headers:
        raise HTTPException(status_code=400, detail="Trendyol kimlik bilgileri eksik")

    base_url = "https://apigw.trendyol.com" if config.get("mode") == "live" else "https://stageapigw.trendyol.com"
    synced = 0
    updated = 0
    total_fetched = 0
    page = 0

    end_dt = datetime.now(timezone.utc)
    start_dt = end_dt - timedelta(days=max(1, min(days_back, 365)))
    start_ms = int(start_dt.timestamp() * 1000)
    end_ms = int(end_dt.timestamp() * 1000)

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            url = f"{base_url}/integration/qna/sellers/{supplier_id}/questions/filter"

            while True:
                params = {
                    "size": 50,
                    "page": page,
                    "startDate": start_ms,
                    "endDate": end_ms,
                    "orderByField": "CreatedDate",
                    "orderByDirection": "DESC",
                }
                if status:
                    params["status"] = status
                resp = await client.get(url, headers=headers, params=params)
                resp.raise_for_status()
                data = resp.json()
                questions = data.get("content", [])

                if not questions:
                    break

                for q in questions:
                    q_id = str(q.get("id"))
                    existing = await db.trendyol_questions.find_one({"question_id": q_id})
                    c_date = q.get("creationDate")
                    created_date_iso = ""
                    if c_date:
                        try:
                            created_date_iso = datetime.fromtimestamp(c_date / 1000, tz=timezone.utc).isoformat()
                        except Exception:
                            created_date_iso = str(c_date)

                    doc = {
                        "question_id": q_id,
                        "product_id": str(q.get("productId", "")),
                        "product_name": q.get("productName", ""),
                        "question_text": q.get("text", ""),
                        "customer_name": q.get("userName", "") if q.get("showUserName") else "Gizli Kullanıcı",
                        "status": q.get("status", "WAITING_FOR_ANSWER"),
                        "created_date": created_date_iso,
                        "image_url": q.get("imageUrl", ""),
                        "synced_at": datetime.now(timezone.utc).isoformat(),
                    }
                    # Y9: Filter API cogu zaman answers[] DONDURMEZ. Bos cevabi $set ile yazarsak
                    # sync-answers ile geri doldurulmus ya da panelden verilmis cevaplar SILINIYORDU.
                    # Bu yuzden cevap yalnizca API gercekten bir cevap dondurdugunde guncellenir.
                    _ans = ""
                    if q.get("answers"):
                        _ans = (q.get("answers", [{}])[0] or {}).get("text", "") or ""
                    if existing:
                        if _ans:
                            doc["answer"] = _ans
                        await db.trendyol_questions.update_one({"question_id": q_id}, {"$set": doc})
                        updated += 1
                    else:
                        doc["id"] = generate_id()
                        doc["created_at"] = datetime.now(timezone.utc).isoformat()
                        doc["answer"] = _ans
                        await db.trendyol_questions.insert_one(doc)
                        synced += 1

                total_fetched += len(questions)
                total_pages = data.get("totalPages", 1)
                page += 1

                if page >= total_pages or page > 50:
                    break

        return {
            "success": True,
            "synced": synced,
            "updated": updated,
            "total_fetched": total_fetched,
            "days_back": days_back,
            "date_range": {"start": start_dt.isoformat(), "end": end_dt.isoformat()},
        }
    except httpx.HTTPStatusError as e:
        logger.error(f"Q&A sync error: {e.response.text}")
        raise HTTPException(status_code=e.response.status_code, detail=e.response.text)
    except Exception as e:
        logger.error(f"Q&A sync error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/trendyol/questions")
async def get_trendyol_questions(
    status: Optional[str] = None,
    page: int = 0,
    size: int = 20,
    current_user: dict = Depends(require_admin)
):
    """Get questions from local DB"""
    query = {}
    if status:
        query["status"] = status

    skip = page * size
    questions = await db.trendyol_questions.find(query).sort("created_at", -1).skip(skip).limit(size).to_list(size)
    total = await db.trendyol_questions.count_documents(query)

    for q in questions:
        q.pop("_id", None)

    return {"questions": questions, "total": total, "page": page, "size": size}


@router.post("/trendyol/questions/{question_id}/answer")
async def answer_trendyol_question(question_id: str, payload: dict, current_user: dict = Depends(require_admin)):
    """Send an answer to a Trendyol customer question"""
    from .integrations import get_trendyol_config, get_trendyol_headers, log_integration_event

    config = await get_trendyol_config()
    if not config["is_active"]:
        raise HTTPException(status_code=400, detail="Trendyol entegrasyonu yapılandırılmamış")

    answer_text = payload.get("answer", "").strip()
    if not answer_text:
        raise HTTPException(status_code=400, detail="Yanit metni bos olamaz")

    supplier_id = config["supplier_id"]
    headers = await get_trendyol_headers()
    base_url = "https://apigw.trendyol.com" if config.get("mode") == "live" else "https://stageapigw.trendyol.com"

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            url = f"{base_url}/integration/qna/sellers/{supplier_id}/questions/{question_id}/answers"
            body = {"text": answer_text}
            resp = await client.post(url, headers=headers, json=body)
            resp.raise_for_status()

        await db.trendyol_questions.update_one(
            {"question_id": question_id},
            {"$set": {"answer": answer_text, "status": "ANSWERED", "answered_at": datetime.now(timezone.utc).isoformat()}}
        )

        await log_integration_event("trendyol", "answer_question", current_user["email"], question_id, "success", "Soru yanitlandi")
        return {"success": True, "message": "Soru basariyla yanitlandi"}
    except httpx.HTTPStatusError as e:
        logger.error(f"Q&A answer error: {e.response.text}")
        raise HTTPException(status_code=e.response.status_code, detail=e.response.text)
    except Exception as e:
        logger.error(f"Q&A answer error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


# ==================== TRENDYOL REVIEWS (public storefront scrape) ====================

@router.post("/trendyol/questions/sync-answers")
async def sync_trendyol_answers(
    payload: Optional[dict] = None,
    current_user: dict = Depends(require_admin),
):
    """ANSWERED status'lu fakat answer alanı boş olan trendyol_questions için
    tek tek /questions/{id} çağrısı atıp answer text'lerini doldur.

    Trendyol filter API'si tasarım gereği `answers[]` döndürmez (performans).
    Bu endpoint detay endpoint'ten çekerek bulk-train için veriyi tamamlar.

    Body: {"max_count": 100, "only_empty_answers": true}
    """
    from .integrations import get_trendyol_config, get_trendyol_headers

    cfg = payload or {}
    max_count = int(cfg.get("max_count") or 100)
    only_empty = bool(cfg.get("only_empty_answers", True))

    config = await get_trendyol_config()
    if not config["is_active"]:
        raise HTTPException(status_code=400, detail="Trendyol entegrasyonu yapılandırılmamış")

    headers = await get_trendyol_headers()
    if not headers:
        raise HTTPException(status_code=400, detail="Trendyol kimlik bilgileri eksik")

    supplier_id = config["supplier_id"]
    base_url = "https://apigw.trendyol.com" if config.get("mode") == "live" else "https://stageapigw.trendyol.com"

    query = {"status": "ANSWERED"}
    if only_empty:
        query["$or"] = [{"answer": ""}, {"answer": {"$exists": False}}, {"answer": None}]

    cur = db.trendyol_questions.find(query, {"_id": 0, "question_id": 1}).limit(max_count)
    targets = await cur.to_list(max_count)

    fetched = 0
    updated = 0
    failed = 0
    errors = []

    async with httpx.AsyncClient(timeout=15) as client:
        for t in targets:
            q_id = t.get("question_id")
            if not q_id:
                continue
            try:
                resp = await client.get(
                    f"{base_url}/integration/qna/sellers/{supplier_id}/questions/{q_id}",
                    headers=headers,
                )
                if resp.status_code != 200:
                    failed += 1
                    if len(errors) < 5:
                        errors.append(f"{q_id}: HTTP {resp.status_code}")
                    continue
                fetched += 1
                data = resp.json()
                # Trendyol detail endpoint single `answer` object döner (filter'daki answers[] DEĞİL)
                ans_obj = data.get("answer") or {}
                # Bazı durumlarda eski format answers[] olabilir — fallback
                if not ans_obj:
                    ans_arr = data.get("answers") or []
                    if ans_arr:
                        ans_obj = ans_arr[0] if isinstance(ans_arr[0], dict) else {}
                ans_text = (ans_obj or {}).get("text", "") if isinstance(ans_obj, dict) else ""
                if ans_text:
                    answered_at_iso = ""
                    cdate = ans_obj.get("creationDate") or ans_obj.get("createdDate")
                    if cdate:
                        try:
                            answered_at_iso = datetime.fromtimestamp(cdate / 1000, tz=timezone.utc).isoformat()
                        except Exception:
                            answered_at_iso = str(cdate)
                    await db.trendyol_questions.update_one(
                        {"question_id": q_id},
                        {"$set": {
                            "answer": ans_text,
                            "answered_at": answered_at_iso or datetime.now(timezone.utc).isoformat(),
                            "answer_synced_at": datetime.now(timezone.utc).isoformat(),
                        }}
                    )
                    updated += 1
            except Exception as e:
                failed += 1
                if len(errors) < 5:
                    errors.append(f"{q_id}: {e}")

    return {
        "success": True,
        "scanned": len(targets),
        "fetched": fetched,
        "updated": updated,
        "failed": failed,
        "errors": errors,
    }


@router.post("/trendyol/reviews/scrape")
async def scrape_trendyol_reviews(
    payload: dict,
    current_user: dict = Depends(require_admin),
):
    """Public Trendyol storefront'tan bir ürünün yorumlarını çeker.

    Body: { "trendyol_url": "...", "product_id": "<local product id>", "min_rating": 4 }
    """
    from .integrations import log_integration_event

    url = (payload or {}).get("trendyol_url", "").strip()
    local_pid = (payload or {}).get("product_id", "").strip()
    min_rating = int((payload or {}).get("min_rating", 4))
    if not url or "trendyol.com" not in url:
        raise HTTPException(status_code=400, detail="Geçerli bir trendyol_url gerekli")

    m = re.search(r"-p-(\d+)", url)
    if not m:
        raise HTTPException(status_code=400, detail="URL'den ürün ID çıkarılamadı")
    content_id = m.group(1)

    api_url = (
        "https://public.trendyol.com/discovery-web-websfxsocialreviewrating-santral/"
        f"api/v1/reviews/{content_id}"
    )
    fetched: List[dict] = []
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            page = 0
            while page < 10:
                params = {"page": page, "size": 30, "order": "DESC", "orderBy": "Score"}
                resp = await client.get(api_url, params=params,
                                        headers={"User-Agent": "Mozilla/5.0",
                                                 "Accept": "application/json"})
                if resp.status_code == 404:
                    break
                resp.raise_for_status()
                data = resp.json()
                reviews = (data.get("result") or {}).get("productReviews", {}).get("content", [])
                if not reviews:
                    break
                fetched.extend(reviews)
                total_pages = (data.get("result") or {}).get("productReviews", {}).get("totalPages", 1)
                page += 1
                if page >= total_pages:
                    break
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code,
                            detail=f"Trendyol public API hatası: {e.response.text[:200]}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Yorum çekme hatası: {e}")

    # Depolama + tarih normalizasyonu tek yerden (robust _store_reviews): gerçek yorum tarihini
    # otomatik-algılar, mevcut yorumların tarihini günceller.
    _res = await _store_reviews(fetched, local_pid, content_id, min_rating)
    inserted = _res["inserted"]
    updated = _res.get("updated", 0)
    skipped_low_rating = _res["skipped_low_rating"]
    skipped_existing = _res.get("skipped_existing", 0)

    if local_pid:
        agg = await db.product_reviews.aggregate([
            {"$match": {"product_id": local_pid, "approved": True}},
            {"$group": {"_id": None, "avg": {"$avg": "$rating"}, "cnt": {"$sum": 1}}}
        ]).to_list(1)
        if agg:
            await db.products.update_one(
                {"id": local_pid},
                {"$set": {
                    "rating": round(agg[0]["avg"], 2),
                    "review_count": agg[0]["cnt"],
                    "reviews_synced_at": datetime.now(timezone.utc).isoformat(),
                }}
            )

    await log_integration_event(
        "trendyol", "review_scrape", "product", local_pid or content_id,
        "success",
        f"Yorum çekildi: contentId={content_id} fetched={len(fetched)} inserted={inserted}",
    )
    return {
        "success": True,
        "content_id": content_id,
        "fetched": len(fetched),
        "inserted": inserted,
        "updated": updated,
        "skipped_low_rating": skipped_low_rating,
        "skipped_existing": skipped_existing,
        "min_rating": min_rating,
    }


@router.post("/trendyol/reviews/scrape-bulk")
async def scrape_trendyol_reviews_bulk(
    payload: dict,
    current_user: dict = Depends(require_admin),
):
    """Birden fazla ürün için toplu yorum çekimi."""
    items = (payload or {}).get("items") or []
    min_rating = int((payload or {}).get("min_rating", 4))
    results = []
    total_inserted = 0
    for it in items[:50]:
        try:
            r = await scrape_trendyol_reviews(
                {"trendyol_url": it.get("trendyol_url", ""),
                 "product_id": it.get("product_id", ""),
                 "min_rating": min_rating},
                current_user=current_user,
            )
            results.append({"product_id": it.get("product_id"), "ok": True, **r})
            total_inserted += r.get("inserted", 0)
        except HTTPException as e:
            results.append({"product_id": it.get("product_id"), "ok": False, "error": e.detail})
        except Exception as e:
            results.append({"product_id": it.get("product_id"), "ok": False, "error": str(e)})
    return {"success": True, "total_inserted": total_inserted, "items": results}


# ============================================================================
# TOPLU YORUM SENKRONU — tüm aktif site ürünleri için Trendyol 4-5★ yorumları
# ============================================================================

_PUBLIC_HOST = "public.trendyol.com"
# _NO_PIN sentinel = hostname'e doğrudan bağlan (pin yok). good=_UNSET → henüz çalışan hedef yok.
_NO_PIN = "__hostname__"
_UNSET = "__unset__"
_pin_state = {"resolved": False, "candidates": [], "good": _UNSET, "ip": None}

# Cloudflare 530/1016 çoğunlukla datacenter IP'sine WAF şüphesiyle döner. Gerçek tarayıcı
# başlıkları (Referer/Origin/Accept-Language/sec-ch-ua) bu şüpheyi büyük ölçüde azaltır.
_BROWSER_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7",
    "Referer": "https://www.trendyol.com/",
    "Origin": "https://www.trendyol.com",
    "sec-ch-ua": '"Chromium";v="122", "Not(A:Brand";v="24", "Google Chrome";v="122"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-site",
}


def _resolve_public_ips_via_doh() -> List[str]:
    """public.trendyol.com'un TÜM A kayıtlarını DNS-over-HTTPS ile çözer (birden fazla IP).
    Cloudflare/Google DoH sabit IP — Railway özel DNS'ine bağımlı değil."""
    ips: List[str] = []
    for doh_ip, sni in (("1.1.1.1", "cloudflare-dns.com"), ("8.8.8.8", "dns.google")):
        try:
            with httpx.Client(timeout=10) as c:
                req = c.build_request(
                    "GET", f"https://{doh_ip}/dns-query",
                    params={"name": _PUBLIC_HOST, "type": "A"},
                    headers={"accept": "application/dns-json"},
                )
                req.extensions["sni_hostname"] = sni.encode("ascii")
                resp = c.send(req)
                resp.raise_for_status()
                for ans in resp.json().get("Answer", []):
                    if ans.get("type") == 1 and ans.get("data") and ans["data"] not in ips:
                        ips.append(ans["data"])
            if ips:
                break
        except Exception:
            continue
    return ips


def _build_candidates() -> List[str]:
    """Denenecek bağlantı hedefleri (sıralı): hostname (Railway çözebiliyorsa) → DoH IP'leri →
    çözülebilen Trendyol anycast IP'leri. Idempotent (bir kez çözer, cache'ler)."""
    if _pin_state["resolved"]:
        return _pin_state["candidates"]
    _pin_state["resolved"] = True
    cands: List[str] = []
    try:
        socket.gethostbyname(_PUBLIC_HOST)
        cands.append(_NO_PIN)   # DNS çözülüyor → doğrudan hostname
    except Exception:
        pass
    for ip in _resolve_public_ips_via_doh():
        if ip not in cands:
            cands.append(ip)
    for alt in ("apigw.trendyol.com", "www.trendyol.com", "api.trendyol.com"):
        try:
            ip = socket.gethostbyname(alt)
            if ip not in cands:
                cands.append(ip)
        except Exception:
            continue
    if not cands:
        cands.append(_NO_PIN)   # son çare
    _pin_state["candidates"] = cands
    _pin_state["ip"] = next((c for c in cands if c != _NO_PIN), None)  # teşhis için
    return cands


def _pin_public_trendyol_if_needed():
    """Geriye dönük uyumluluk — adaylar bir kez çözülür."""
    _build_candidates()


async def _one_review_page(client, target: str, content_id: str, page: int):
    """Tek sayfa istek. target=_NO_PIN → hostname; aksi halde IP'ye bağlan, Host+SNI korunur."""
    pin = None if target == _NO_PIN else target
    base = f"https://{pin}" if pin else f"https://{_PUBLIC_HOST}"
    api_url = (f"{base}/discovery-web-websfxsocialreviewrating-santral/"
               f"api/v1/reviews/{content_id}")
    params = {"page": page, "size": 30, "order": "DESC", "orderBy": "Score"}
    headers = dict(_BROWSER_HEADERS)
    if pin:
        headers["Host"] = _PUBLIC_HOST
    req = client.build_request("GET", api_url, params=params, headers=headers)
    if pin:
        req.extensions["sni_hostname"] = _PUBLIC_HOST.encode("ascii")  # TLS SNI + cert = public.trendyol.com
    return await client.send(req)


def _review_worker() -> str:
    """Opsiyonel Cloudflare Worker vekili. Worker, Trendyol yorum JSON'unu aynen döndürür
    (GET {worker}?contentId=X&page=Y). Trendyol'un datacenter-IP 530 engelini ÜCRETSİZ aşar.
    Ayar: db.settings(id=trendyol).review_worker_url VEYA env TRENDYOL_REVIEW_WORKER."""
    return os.environ.get("TRENDYOL_REVIEW_WORKER") or _pin_state.get("worker") or ""


def _review_proxy() -> str:
    """Yorum çekimi için opsiyonel proxy (residential/datacenter). 530 engelini aşar.
    Ayar: env TRENDYOL_REVIEW_PROXY → db.settings(id=trendyol).review_proxy."""
    return os.environ.get("TRENDYOL_REVIEW_PROXY") or _pin_state.get("proxy") or ""


async def _fetch_reviews_for_content_id(content_id: str, min_rating: int, max_pages: int = 10) -> List[dict]:
    """Trendyol public storefront'tan bir contentId'nin yorumlarını çeker (sayfalı).
    ÖNCELİK: (1) Cloudflare Worker vekili → (2) Proxy → (3) doğrudan DoH IP hedefleri.
    (1) ve (2) Trendyol'un Railway/datacenter-IP 530 engelini aşar."""
    # (1) Cloudflare Worker vekili — worker Trendyol JSON'unu aynen döndürür.
    worker = _review_worker()
    if worker:
        fetched: List[dict] = []
        # A2.9: worker JSON'u doğrudan döndürür; redirect izleme kapalı (redirect tabanlı SSRF baypası yok).
        async with httpx.AsyncClient(timeout=30, follow_redirects=False) as client:
            page = 0
            while page < max_pages:
                sep = "&" if "?" in worker else "?"
                u = f"{worker}{sep}contentId={content_id}&page={page}"
                # Trendyol hız sınırı → 502/429/503 gelirse artan bekleme ile 3 kez dene.
                resp = None
                for attempt in range(3):
                    resp = await client.get(u)
                    if resp.status_code == 200 or resp.status_code == 404:
                        break
                    if resp.status_code in (429, 500, 502, 503, 504):
                        await asyncio.sleep(0.9 * (attempt + 1))
                        continue
                    break
                if resp is None or resp.status_code == 404:
                    break
                resp.raise_for_status()
                body = resp.json()
                rv = (body.get("result") or {}).get("productReviews", {}).get("content", [])
                if not rv:
                    break
                fetched.extend(rv)
                total_pages = (body.get("result") or {}).get("productReviews", {}).get("totalPages", 1) or 1
                page += 1
                if page >= total_pages:
                    break
        _pin_state["good"] = "worker"
        return fetched

    # (2) Proxy
    proxy = _review_proxy()
    if proxy:
        # Proxy DNS'i kendi çözer; IP pin/SNI gerekmez, düz hostname yeterli.
        api_url = (f"https://{_PUBLIC_HOST}/discovery-web-websfxsocialreviewrating-santral/"
                   f"api/v1/reviews/{content_id}")
        fetched: List[dict] = []
        async with httpx.AsyncClient(timeout=25, follow_redirects=True, proxy=proxy) as client:
            page = 0
            while page < max_pages:
                resp = await client.get(api_url, params={"page": page, "size": 30, "order": "DESC", "orderBy": "Score"},
                                        headers=_BROWSER_HEADERS)
                if resp.status_code == 404:
                    break
                resp.raise_for_status()
                rv = (resp.json().get("result") or {}).get("productReviews", {}).get("content", [])
                if not rv:
                    break
                fetched.extend(rv)
                total_pages = (resp.json().get("result") or {}).get("productReviews", {}).get("totalPages", 1) or 1
                page += 1
                if page >= total_pages:
                    break
        _pin_state["good"] = "proxy"
        return fetched

    cands = _build_candidates()
    good = _pin_state["good"]
    order = ([good] if good != _UNSET and good in cands else []) + \
            [c for c in cands if c != good]

    last_err = None
    _BAD = {403, 429, 500, 502, 503, 520, 521, 522, 523, 524, 525, 526, 530}
    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
        for target in order:
            try:
                resp = await _one_review_page(client, target, content_id, 0)
                if resp.status_code in _BAD:
                    last_err = f"HTTP {resp.status_code} @ {target}"
                    continue
                if resp.status_code == 404:
                    _pin_state["good"] = target   # bu hedef çalışıyor (ürünün yorumu yok sadece)
                    return []
                resp.raise_for_status()
                _pin_state["good"] = target        # çalışan hedefi sabitle
                data = resp.json()
                fetched: List[dict] = list((data.get("result") or {}).get("productReviews", {}).get("content", []))
                total_pages = (data.get("result") or {}).get("productReviews", {}).get("totalPages", 1) or 1
                page = 1
                while page < min(max_pages, total_pages):
                    r2 = await _one_review_page(client, target, content_id, page)
                    if r2.status_code != 200:
                        break
                    rv2 = (r2.json().get("result") or {}).get("productReviews", {}).get("content", [])
                    if not rv2:
                        break
                    fetched.extend(rv2)
                    page += 1
                return fetched
            except Exception as e:
                last_err = str(e)[:140]
                continue
    # Hiçbir hedef çalışmadı — çağıran (sync_all) hatayı özet errors'a yazar.
    raise Exception(f"tum hedefler basarisiz ({len(order)} denendi): {last_err}")


def _parse_ty_date(val) -> str:
    """Trendyol yorum tarihini ISO'ya normalize eder. epoch ms/sn, ISO string veya boş kabul eder.
    Türkçe metin ('19 Mart 2026') gibi ayrıştırılamayanlar için '' döner."""
    if not val:
        return ""
    if isinstance(val, (int, float)):
        ts = val / 1000.0 if val > 1e12 else float(val)
        try:
            return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
        except Exception:
            return ""
    s = str(val).strip()
    if re.match(r"^\d{4}-\d{2}-\d{2}", s):  # ISO
        return s
    # Türkçe ay adlı metin → ISO'ya çevir
    _months = {"ocak": 1, "şubat": 2, "subat": 2, "mart": 3, "nisan": 4, "mayıs": 5, "mayis": 5,
               "haziran": 6, "temmuz": 7, "ağustos": 8, "agustos": 8, "eylül": 9, "eylul": 9,
               "ekim": 10, "kasım": 11, "kasim": 11, "aralık": 12, "aralik": 12}
    m = re.match(r"(\d{1,2})\s+([A-Za-zçÇğĞıİöÖşŞüÜ]+)\s+(\d{4})", s)
    if m:
        d, mon, y = int(m.group(1)), _months.get(m.group(2).lower()), int(m.group(3))
        if mon:
            try:
                return datetime(y, mon, d, tzinfo=timezone.utc).isoformat()
            except Exception:
                return ""
    return ""


def _looks_like_date(v) -> bool:
    """Değer tarih-benzeri mi? epoch(ms/sn), ISO string veya 'GG Ay YYYY' Türkçe metin."""
    if isinstance(v, bool):
        return False
    if isinstance(v, (int, float)):
        # makul aralık: 2015-01-01 .. 2035-01-01 (sn ve ms olarak)
        return 1.42e9 <= v <= 2.05e9 or 1.42e12 <= v <= 2.05e12
    if isinstance(v, str):
        s = v.strip()
        if re.match(r"^\d{4}-\d{2}-\d{2}", s):  # ISO
            return True
        # 'GG Ay YYYY' Türkçe/İngilizce ay adı
        if re.match(r"^\d{1,2}\s+[A-Za-zçÇğĞıİöÖşŞüÜ]+\s+\d{4}", s):
            return True
    return False


def _review_raw_date(r: dict):
    """Ham yorum objesinden tarih değerini alır. Önce bilinen alan adlarını dener,
    bulamazsa TÜM alanları tarih-benzeri değer için tarar (Trendyol alan adını değiştirse de çalışır)."""
    # 1) Bilinen alan adları (öncelik sırası)
    for k in ("commentDateISOtype", "commentDateISO", "lastModifiedDate", "commentDate", "date",
              "createdDate", "createdAt", "creationDate", "reviewDate", "commentDateText",
              "formattedDate", "commentDateHumanized", "lastModified"):
        v = r.get(k)
        if v and _looks_like_date(v):
            return v
    # 2) Otomatik tarama: adında 'date'/'time'/'tarih' geçen alanları öncele
    best = ""
    for k, v in r.items():
        if not _looks_like_date(v):
            continue
        kl = str(k).lower()
        if any(w in kl for w in ("date", "time", "tarih", "created", "modified")):
            return v          # en olası alan
        if not best:
            best = v          # tarih-benzeri ilk değer (yedek)
    return best


async def _store_reviews(fetched: List[dict], local_pid: Optional[str], content_id: str, min_rating: int) -> dict:
    """Çekilen yorumları product_reviews'a yazar. YENİ yorumlar gerçek tarihiyle eklenir;
    MEVCUT yorumların tarihi/puanı güncellenir (external_id ile eşleşir)."""
    inserted = updated = low_admin_only = skipped_existing = 0
    for r in fetched:
        rating = int(r.get("rate") or 0)
        if rating <= 0:
            continue
        # min_rating artık YAYIN eşiğidir: eşik altı yorumlar da ÇEKİLİR ama approved=False
        # (mağazada GÖRÜNMEZ, ürün puanına KATILMAZ) → admin panelden görebilir. Kullanıcı isteği:
        # 1-2 yıldızlı yorumları da çek, müşteriye gösterme.
        _approved = rating >= min_rating
        if not _approved:
            low_admin_only += 1
        review_id = str(r.get("id") or "")
        if not review_id:
            continue
        raw_date = _review_raw_date(r)
        iso_date = _parse_ty_date(raw_date)
        display_date = iso_date or datetime.now(timezone.utc).isoformat()

        existing = await db.product_reviews.find_one(
            {"source": "trendyol_public", "external_id": review_id}, {"_id": 1, "comment_date": 1}
        )
        if existing:
            # Mevcut yorumun tarihini gerçek tarihe güncelle (8 Temmuz sorununu düzeltir).
            # Görünürlük yıldıza göre eşiğe göre senkron tutulur (düşükse gizle).
            upd = {"rating": rating, "approved": _approved, "admin_only": (not _approved)}
            if iso_date:
                upd["comment_date"] = raw_date
                upd["created_at"] = iso_date
            await db.product_reviews.update_one({"_id": existing["_id"]}, {"$set": upd})
            updated += 1
            continue
        doc = {
            "id": generate_id(),
            "external_id": review_id,
            "source": "trendyol_public",
            "product_id": local_pid or None,
            "trendyol_content_id": content_id,
            "rating": rating,
            "title": r.get("commentTitle") or "",
            "comment": r.get("comment") or "",
            "user_name": r.get("userFullName") or "Trendyol Müşterisi",
            "is_verified": bool(r.get("verifiedPurchase")),
            "is_seller_verified": bool(r.get("sellerVerified")),
            "approved": _approved,               # eşik altı → False (mağazada gizli, admin görür)
            "admin_only": (not _approved),
            "created_at": display_date,          # GERÇEK yorum tarihi (varsa)
            "comment_date": raw_date or "",
            "synced_at": datetime.now(timezone.utc).isoformat(),
        }
        await db.product_reviews.insert_one(doc)
        inserted += 1
    # skipped_low_rating geriye-uyum için 0; low_admin_only = gizli saklanan düşük yıldız sayısı.
    return {"inserted": inserted, "updated": updated, "skipped_low_rating": 0,
            "low_admin_only": low_admin_only, "skipped_existing": skipped_existing}


async def backfill_review_dates():
    """TEK SEFERLİK (bayrak korumalı): eskiden çekilmiş Trendyol yorumlarının created_at'i
    çekim tarihi olarak kalmıştı. Kayıtlı ham tarih (comment_date) parse edilip created_at
    GERÇEK yorum tarihine çekilir — panel + site yorumları Trendyol'daki tarihiyle görünür.
    Ham tarihi hiç kaydedilmemiş çok eski kayıtlar ağ olmadan düzelemez; onlar
    "Tümünü Baştan Çek" senkronunda _store_reviews güncellemesiyle düzelir."""
    flag = await db.settings.find_one({"id": "ty_review_date_backfill"}, {"_id": 0})
    if flag and flag.get("done"):
        return
    fixed = scanned = 0
    cursor = db.product_reviews.find(
        {"source": "trendyol_public", "comment_date": {"$nin": ["", None]}},
        {"_id": 1, "comment_date": 1, "created_at": 1})
    async for r in cursor:
        scanned += 1
        iso = _parse_ty_date(r.get("comment_date"))
        if iso and iso[:10] != str(r.get("created_at") or "")[:10]:
            await db.product_reviews.update_one({"_id": r["_id"]}, {"$set": {"created_at": iso}})
            fixed += 1
    await db.settings.update_one(
        {"id": "ty_review_date_backfill"},
        {"$set": {"done": True, "scanned": scanned, "fixed": fixed,
                  "at": datetime.now(timezone.utc).isoformat()}},
        upsert=True)
    logger.info(f"[ty-review-date-backfill] tarandı={scanned} düzeltildi={fixed}")


async def _recalc_product_rating(local_pid: str) -> None:
    """Bir ürünün onaylı yorumlarından ortalama puan + adet hesaplayıp products dokümanına yazar."""
    agg = await db.product_reviews.aggregate([
        {"$match": {"product_id": local_pid, "approved": True}},
        {"$group": {"_id": None, "avg": {"$avg": "$rating"}, "cnt": {"$sum": 1}}},
    ]).to_list(1)
    if agg:
        await db.products.update_one(
            {"id": local_pid},
            {"$set": {
                "rating": round(agg[0]["avg"], 2),
                "review_count": agg[0]["cnt"],
                "reviews_synced_at": datetime.now(timezone.utc).isoformat(),
            }},
        )


async def _review_sync_state_bump(done=0, matched=0, fetched=0, inserted=0, err=0, last_name=""):
    """İlerleme sayaçları + kalp atışı — panel canlı izler."""
    inc = {}
    if done: inc["done_products"] = done
    if matched: inc["matched"] = matched
    if fetched: inc["fetched"] = fetched
    if inserted: inc["inserted"] = inserted
    if err: inc["errors"] = err
    upd = {"$set": {"heartbeat": datetime.now(timezone.utc).isoformat()}}
    if last_name:
        upd["$set"]["last_product"] = last_name[:80]
    if inc:
        upd["$inc"] = inc
    await db.settings.update_one({"id": "trendyol_review_sync_state"}, upd, upsert=True)


async def sync_all_trendyol_reviews_core(min_rating: int = 4, limit: int = 0, dry_run: bool = False,
                                         only_missing: bool = False) -> dict:
    """
    TÜM aktif site ürünleri için Trendyol public yorumlarını (>= min_rating) çeker.

    Eşleştirme akışı:
      1) Trendyol getProducts (approved) → barcode→contentId haritası kurulur.
      2) Her site ürününün barcode'ları (ana + varyant) bu haritada aranır.
      3) Bulunan her contentId için public yorumlar çekilip product_reviews'a yazılır,
         ürünün rating/review_count alanları yeniden hesaplanır.
    """
    from trendyol_client import TrendyolClient

    # Config: çalışan fiyat script'iyle birebir aynı kaynak (db.settings id=trendyol).
    # NOT: get_trendyol_config() mode varsayılanı "sandbox" — bu stage API'ye düşüp
    # boş ürün listesi döndürüyordu (indexed=0). Burada mode varsayılanı "live".
    settings = await db.settings.find_one({"id": "trendyol"}) or {}
    supplier_id = settings.get("supplier_id") or os.environ.get("TRENDYOL_SUPPLIER_ID", "")
    api_key = settings.get("api_key") or os.environ.get("TRENDYOL_API_KEY", "")
    api_secret = settings.get("api_secret") or os.environ.get("TRENDYOL_API_SECRET", "")
    mode = settings.get("mode") or os.environ.get("TRENDYOL_MODE") or "live"
    if not (supplier_id and api_key and api_secret):
        raise HTTPException(status_code=400, detail="Trendyol kimlik bilgileri eksik")
    # Opsiyonel yorum-proxy'si: Trendyol Cloudflare Railway IP'sini 530 ile engellerse, buradaki
    # proxy üzerinden çekilir. Ayar: db.settings(id=trendyol).review_proxy VEYA env TRENDYOL_REVIEW_PROXY.
    _pin_state["proxy"] = settings.get("review_proxy") or os.environ.get("TRENDYOL_REVIEW_PROXY") or ""
    _pin_state["worker"] = settings.get("review_worker_url") or os.environ.get("TRENDYOL_REVIEW_WORKER") or ""

    # 1) barcode -> contentId haritası (Trendyol ürünleri; approved filtresi yok = en geniş)
    client = TrendyolClient(supplier_id=str(supplier_id), api_key=api_key, api_secret=api_secret, mode=mode)
    _pin_public_trendyol_if_needed()
    bc_to_cid: dict = {}
    debug = {"mode": mode, "ty_total_elements": None, "ty_total_pages": None,
             "ty_first_page_count": 0, "ty_sample_keys": None, "ty_with_contentid": 0,
             "public_pin_ip": _pin_state["ip"]}
    page = 0
    while page < 300:
        try:
            data = await client.get_filtered_products(page=page, size=200)
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"Trendyol ürün listesi hatası: {e}")
        content = (data or {}).get("content", []) or []
        if page == 0:
            debug["ty_total_elements"] = (data or {}).get("totalElements")
            debug["ty_total_pages"] = (data or {}).get("totalPages")
            debug["ty_first_page_count"] = len(content)
            if content:
                debug["ty_sample_keys"] = sorted(list(content[0].keys()))
        if not content:
            break
        for p in content:
            bc = (p.get("barcode") or "").strip()
            cid = p.get("productContentId") or p.get("contentId")
            if not cid:
                m = re.search(r"-p-(\d+)", p.get("productUrl") or "")
                if m:
                    cid = m.group(1)
            if bc and cid:
                bc_to_cid[bc] = str(cid)
                debug["ty_with_contentid"] += 1
        total_pages = (data or {}).get("totalPages", 1) or 1
        page += 1
        if page >= total_pages:
            break

    # 2) aktif site ürünleri
    products = await db.products.find(
        {"is_active": True, "is_deleted": {"$ne": True}},
        {"_id": 0, "id": 1, "name": 1, "barcode": 1, "variants": 1},
    ).to_list(100000)
    if limit and limit > 0:
        products = products[:limit]

    # "Yalnız eksikleri çek": daha önce yorumu BAŞARIYLA çekilmiş ürünler atlanır —
    # tekrar basıldığında yalnız çekilemeyenler (eşleşmeyen/hatalı/yorumsuz) denenir.
    skipped_already = 0
    if only_missing:
        done_ids = set()
        async for r in db.trendyol_review_sync_products.find(
                {"fetched": {"$gt": 0}}, {"_id": 0, "product_id": 1}):
            done_ids.add(r.get("product_id"))
        before = len(products)
        products = [p for p in products if p.get("id") not in done_ids]
        skipped_already = before - len(products)

    # İlerleme durumu — arka plan senkronu panelden canlı izlenir.
    await db.settings.update_one(
        {"id": "trendyol_review_sync_state"},
        {"$set": {"status": "running", "started_at": datetime.now(timezone.utc).isoformat(),
                  "total_products": len(products), "done_products": 0, "matched": 0,
                  "fetched": 0, "inserted": 0, "errors": 0, "dry_run": dry_run,
                  "only_missing": only_missing, "skipped_already": skipped_already,
                  "last_product": "", "error": "",
                  "heartbeat": datetime.now(timezone.utc).isoformat()},
         "$unset": {"finished_at": ""}},
        upsert=True,
    )

    summary = {
        "trendyol_products_indexed": len(bc_to_cid),
        "site_products": len(products),
        "matched_products": 0,
        "unmatched_products": 0,
        "content_ids_scraped": 0,
        "total_fetched": 0,
        "total_inserted": 0,
        "total_updated": 0,
        "skipped_low_rating": 0,
        "low_admin_only": 0,
        "skipped_existing": 0,
        "errors": [],
        "dry_run": dry_run,
        "min_rating": min_rating,
        "_debug": debug,
    }

    # 3) her ürün için contentId'leri bul, yorumları çek
    for p in products:
        pid = p.get("id")
        barcodes = set()
        if (p.get("barcode") or "").strip():
            barcodes.add(p["barcode"].strip())
        for v in (p.get("variants") or []):
            if (v.get("barcode") or "").strip():
                barcodes.add(v["barcode"].strip())
        cids = []
        for bc in barcodes:
            cid = bc_to_cid.get(bc)
            if cid and cid not in cids:
                cids.append(cid)
        _prod_base = {"product_id": pid, "name": p.get("name") or "",
                      "last_sync_at": datetime.now(timezone.utc).isoformat()}
        if not cids:
            summary["unmatched_products"] += 1
            if not dry_run:
                await db.trendyol_review_sync_products.update_one(
                    {"product_id": pid},
                    {"$set": {**_prod_base, "status": "eslesmedi", "fetched": 0, "inserted": 0, "error": ""}},
                    upsert=True)
            await _review_sync_state_bump(done=1, last_name=p.get("name") or "")
            continue
        summary["matched_products"] += 1
        _p_fetched = _p_inserted = 0
        _p_err = ""
        for cid in cids:
            # Trendyol hız sınırını aşmamak için istekler arası kısa bekleme (rate-limit → 502).
            await asyncio.sleep(0.35)
            try:
                fetched = await _fetch_reviews_for_content_id(cid, min_rating)
            except Exception as e:
                summary["errors"].append({"product_id": pid, "content_id": cid, "error": str(e)[:160]})
                _p_err = str(e)[:160]
                continue
            summary["content_ids_scraped"] += 1
            summary["total_fetched"] += len(fetched)
            _p_fetched += len(fetched)
            if dry_run:
                summary["total_inserted"] += sum(1 for r in fetched if int(r.get("rate") or 0) >= min_rating)
                continue
            res = await _store_reviews(fetched, pid, cid, min_rating)
            summary["total_inserted"] += res["inserted"]
            summary["total_updated"] += res.get("updated", 0)
            summary["skipped_low_rating"] += res["skipped_low_rating"]
            summary["low_admin_only"] = summary.get("low_admin_only", 0) + res.get("low_admin_only", 0)
            summary["skipped_existing"] += res["skipped_existing"]
            _p_inserted += res["inserted"]
        if not dry_run:
            await _recalc_product_rating(pid)
            _p_status = "cekildi" if _p_fetched > 0 else ("hata" if _p_err else "yorum_yok")
            await db.trendyol_review_sync_products.update_one(
                {"product_id": pid},
                {"$set": {**_prod_base, "status": _p_status, "fetched": _p_fetched,
                          "inserted": _p_inserted, "error": _p_err}},
                upsert=True)
        await _review_sync_state_bump(done=1, matched=1, fetched=_p_fetched,
                                      inserted=_p_inserted, err=1 if _p_err else 0,
                                      last_name=p.get("name") or "")

    # Teşhis: hangi bağlantı hedefi çalıştı / kaç aday denendi (530 sorunu için).
    debug["public_candidates"] = _pin_state.get("candidates")
    debug["public_good_target"] = None if _pin_state.get("good") in (_UNSET, None) else _pin_state.get("good")
    debug["review_proxy_set"] = bool(_pin_state.get("proxy"))
    debug["review_worker_set"] = bool(_pin_state.get("worker"))
    await db.settings.update_one(
        {"id": "trendyol_review_sync_state"},
        {"$set": {"status": "done", "finished_at": datetime.now(timezone.utc).isoformat(),
                  "heartbeat": datetime.now(timezone.utc).isoformat()}},
    )
    return summary


@router.post("/trendyol/reviews/sync-all")
async def sync_all_trendyol_reviews(
    payload: dict,
    current_user: dict = Depends(require_admin),
):
    """
    TÜM aktif site ürünleri için Trendyol 4-5 yıldız yorumlarını toplu çeker.

    Body: { "min_rating": 4, "limit": 0, "dry_run": false }
      - min_rating: alt yıldız sınırı (varsayılan 4 → 4 ve 5 yıldız)
      - limit: yalnızca ilk N ürün (0 = hepsi)
      - dry_run: true → yalnızca sayım, DB'ye yazmaz
    """
    from .integrations import log_integration_event

    payload = payload or {}
    if payload.get("min_rating") is not None:
        min_rating = int(payload.get("min_rating"))
    else:
        _s = await db.settings.find_one({"id": "trendyol"}, {"_id": 0, "review_min_rating": 1}) or {}
        min_rating = int(_s.get("review_min_rating") or 3)   # ayar yoksa 3 yıldız ve üzeri
    min_rating = max(1, min(5, min_rating))
    limit = int(payload.get("limit", 0) or 0)
    dry_run = bool(payload.get("dry_run", False))
    # Varsayılan: yalnız daha önce yorumu çekilemeyen ürünler denenir (tekrar basınca
    # baştan hepsini taramaz). "Tümünü baştan" için only_missing:false gönderilir.
    only_missing = bool(payload.get("only_missing", True))

    # Zaten çalışan bir senkron varsa ikinciyi başlatma (10 dk kalp atışı toleransı).
    st = await db.settings.find_one({"id": "trendyol_review_sync_state"}, {"_id": 0}) or {}
    if st.get("status") == "running":
        try:
            _hb = datetime.fromisoformat(str(st.get("heartbeat")))
            if (datetime.now(timezone.utc) - _hb).total_seconds() < 600:
                raise HTTPException(status_code=409, detail="Senkron zaten çalışıyor — ilerlemeyi listeden izleyin")
        except HTTPException:
            raise
        except Exception:
            pass  # bozuk/eski kalp atışı → yeni senkrona izin ver

    # ARKA PLANDA çalıştır: tüm katalog taraması dakikalar sürer, HTTP isteği
    # Cloudflare/Railway zaman aşımına takılıp "senkron başarısız" görünüyordu.
    async def _runner():
        try:
            summary = await sync_all_trendyol_reviews_core(
                min_rating=min_rating, limit=limit, dry_run=dry_run, only_missing=only_missing)
            try:
                await log_integration_event(
                    "trendyol", "review_sync_all", "bulk", "all", "success",
                    f"matched={summary['matched_products']} inserted={summary['total_inserted']} dry_run={dry_run}",
                )
            except Exception:
                pass
        except Exception as e:
            await db.settings.update_one(
                {"id": "trendyol_review_sync_state"},
                {"$set": {"status": "error", "error": str(e)[:300],
                          "finished_at": datetime.now(timezone.utc).isoformat(),
                          "heartbeat": datetime.now(timezone.utc).isoformat()}},
                upsert=True)

    asyncio.create_task(_runner())
    return {"success": True, "started": True,
            "message": "Senkron arka planda başladı — ilerleme aşağıdaki listede canlı görünür"}


@router.get("/trendyol/reviews/sync-status")
async def trendyol_review_sync_status(current_user: dict = Depends(require_admin)):
    """Arka plan yorum senkronunun canlı durumu + ürün bazında çekildi/çekilemedi listesi."""
    st = await db.settings.find_one({"id": "trendyol_review_sync_state"}, {"_id": 0}) or {}
    rows = await db.trendyol_review_sync_products.find({}, {"_id": 0}) \
        .sort([("status", 1), ("last_sync_at", -1)]).to_list(3000)
    counts: dict = {}
    for r in rows:
        k = r.get("status") or "?"
        counts[k] = counts.get(k, 0) + 1
    return {"state": st, "counts": counts, "products": rows}


@router.get("/trendyol/reviews/list")
async def trendyol_reviews_list(
    approved: Optional[bool] = None,
    ratings: Optional[str] = None,
    product_id: Optional[str] = None,
    max_rating: Optional[int] = None,
    min_rating: Optional[int] = None,
    limit: int = 300,
    current_user: dict = Depends(require_admin),
):
    """Tek tek Trendyol yorumlarını (admin görünümü) döndürür. `ratings=1,2` → sadece o
    yıldızlar (tek tek seçim). product_id → tek ürün. approved=false → mağazada gizli. PII yok."""
    q = {"source": "trendyol_public"}
    if product_id:
        q["product_id"] = product_id
    if approved is not None:
        q["approved"] = approved
    _stars = []
    for x in str(ratings or "").split(","):
        x = x.strip()
        if x.isdigit() and 1 <= int(x) <= 5:
            _stars.append(int(x))
    if _stars:
        q["rating"] = {"$in": sorted(set(_stars))}
    else:
        rq = {}
        if max_rating is not None:
            rq["$lte"] = int(max_rating)
        if min_rating is not None:
            rq["$gte"] = int(min_rating)
        if rq:
            q["rating"] = rq
    rows = await db.product_reviews.find(
        q, {"_id": 0, "id": 1, "product_id": 1, "rating": 1, "title": 1, "comment": 1,
            "user_name": 1, "created_at": 1, "comment_date": 1, "approved": 1, "is_verified": 1},
    ).sort("created_at", -1).limit(min(max(1, limit), 2000)).to_list(None)
    pids = list({r.get("product_id") for r in rows if r.get("product_id")})
    names = {}
    if pids:
        async for p in db.products.find({"id": {"$in": pids}}, {"_id": 0, "id": 1, "name": 1}):
            names[p["id"]] = p.get("name")
    for r in rows:
        r["product_name"] = names.get(r.get("product_id")) or "—"
    return {"items": rows, "count": len(rows)}


# Şikayet kategorileri (anahtar-kelime) — hem AI yedeğinde hem gruplama ucunda kullanılır.
_REVIEW_CATS = [
    ("Kalıp dar/küçük", ["kalıp", "kalip", "dar ", " dar", "küçük", "kucuk", "small", "xs gibi", "beden değil", "beden degil"], 4),
    ("Kumaş kalitesiz", ["kumaş", "kumas", "kalitesiz", "polyester", "ince", "tok durmu", "kalitesi kötü", "kalitesi kotu"], 4),
    ("Dikiş/işçilik", ["dikiş", "dikis", "söküldü", "sokuldu", "açıldı", "acildi", "işçilik", "iscilik", "dikişi attı", "dikisi atti"], 4),
    ("Beden/ölçü uyumsuz", ["beden", "ölçü", "olcu", "numara", "büyük geldi", "buyuk geldi", "geniş", "genis"], 3),
    ("Fiyat/değer", ["fiyat", "para etmez", "pahalı", "pahali", "değmez", "degmez", "hak etmiyor", "israf", "para yazık", "para yazik"], 3),
    ("Kargo/paketleme", ["kargo", "paket", "geç geldi", "gec geldi", "eksik geldi"], 2),
    ("Görselden farklı", ["farklı", "farkli", "resimde", "görselde", "gorselde", "göründüğü", "gorundugu", "beklenti"], 3),
    ("Renk farklı/soluk", ["renk farklı", "renk farkli", "soluk", "soldu", "rengi farklı", "rengi farkli"], 3),
    ("Koku/leke", ["koku", "kokuyor", "leke", "kirli geldi"], 4),
]


@router.get("/trendyol/reviews/grouped")
async def trendyol_reviews_grouped(
    product_id: str,
    ratings: Optional[str] = None,
    current_user: dict = Depends(require_admin),
):
    """Bir ürünün düşük yıldızlı yorumlarını şikayet kategorilerine GRUPLAR ve her grubun
    ALTINDAKİ yorumları döndürür (popup detay için). Bir yorum birden çok gruba girebilir;
    hiçbirine uymayan 'Diğer'e düşer. Küçükten büyüğe sıralı."""
    q = {"source": "trendyol_public", "product_id": product_id}
    _stars = [int(x) for x in str(ratings or "").split(",") if x.strip().isdigit() and 1 <= int(x) <= 5]
    if _stars:
        q["rating"] = {"$in": sorted(set(_stars))}
    else:
        q["rating"] = {"$lte": 2}
    rows = await db.product_reviews.find(
        q, {"_id": 0, "id": 1, "rating": 1, "comment": 1, "title": 1, "created_at": 1,
            "comment_date": 1, "user_name": 1, "approved": 1},
    ).sort("created_at", -1).limit(1500).to_list(None)
    groups = {name: {"reason": name, "severity": sev, "reviews": []} for name, _, sev in _REVIEW_CATS}
    other = {"reason": "Diğer", "severity": 2, "reviews": []}
    for r in rows:
        body = (f"{r.get('title') or ''} {r.get('comment') or ''}").strip().lower()
        matched = False
        for name, kws, _sev in _REVIEW_CATS:
            if any(k in body for k in kws):
                groups[name]["reviews"].append(r)
                matched = True
        if not matched:
            other["reviews"].append(r)
    out = [g for g in groups.values() if g["reviews"]]
    if other["reviews"]:
        out.append(other)
    for g in out:
        g["count"] = len(g["reviews"])
    out.sort(key=lambda x: x["count"])  # küçükten büyüğe (kullanıcı isteği)
    return {"ok": True, "product_id": product_id, "total_reviews": len(rows), "groups": out}


@router.get("/trendyol/reviews/analyze")
async def trendyol_reviews_analyze(
    ratings: Optional[str] = None,
    product_id: Optional[str] = None,
    max_rating: Optional[int] = None,
    limit: int = 250,
    current_user: dict = Depends(require_admin),
):
    """AI ile şikayet-nedeni analizi: seçili düşük yıldızlı yorumları LLM'e verip
    şikayet nedenlerini kategorilere ayırır ve KAÇ yorumda geçtiğini sayar. Küçükten büyüğe
    sıralı döner. ChatGPT/OpenAI (Ayarlar → AI sağlayıcısı) kullanılır."""
    q = {"source": "trendyol_public"}
    if product_id:
        q["product_id"] = product_id
    _stars = [int(x) for x in str(ratings or "").split(",") if x.strip().isdigit() and 1 <= int(x) <= 5]
    if _stars:
        q["rating"] = {"$in": sorted(set(_stars))}
    elif max_rating is not None:
        q["rating"] = {"$lte": int(max_rating)}
    else:
        q["rating"] = {"$lte": 2}
    rows = await db.product_reviews.find(
        q, {"_id": 0, "rating": 1, "comment": 1, "title": 1},
    ).sort("created_at", -1).limit(min(max(1, limit), 400)).to_list(None)
    comments = []
    for r in rows:
        _t = (r.get("title") or "").strip()
        _c = (r.get("comment") or "").strip()
        _body = (f"{_t} {_c}").strip()
        if _body:
            comments.append(f"[{r.get('rating')}★] {_body}")
    if not comments:
        return {"ok": True, "total_reviews": 0, "reasons": [], "message": "Analiz edilecek yorum yok"}

    # KURAL (kullanıcı isteği): kaç yorum varsa O KADARIYLA analiz yap — minimum yok.
    # Sıra: 1) AI (varsa) → 2) AI boş/başarısız veya anahtar yoksa anahtar-kelime yedeği.
    out = []
    method = "ai"
    _provider = ""
    _model = ""
    from .ai_chatbot import get_ai_settings, _api_key_for, llm_chat
    settings = await get_ai_settings()
    api_key = _api_key_for(settings)
    if api_key:
        _provider = settings.get("provider") or "openai"
        _model = settings.get("model") or "gpt-5.4-mini"
        sys_msg = (
            "Sen bir e-ticaret ürün-yorum analistisin. Sana bir mağazanın DÜŞÜK PUANLI müşteri "
            "yorumları verilir. Görevin: şikayet NEDENLERİNİ birbirinden ayrık kategorilere topla "
            "(ör. 'Kalıp dar/küçük', 'Kumaş kalitesiz', 'Dikiş/işçilik kusuru', 'Beden-ölçü uyumsuz', "
            "'Görselden/beklentiden farklı', 'Fiyat/değer', 'Kargo/paketleme', 'Koku/leke', 'Renk farklı'). "
            "Her kategori için KAÇ farklı yorumda geçtiğini SAY. Az yorum olsa bile (1 tane bile) analiz et. "
            "Kısa, Türkçe kategori adları kullan. SADECE ve YALNIZCA şu JSON'u döndür (açıklama yazma): "
            '{"reasons":[{"reason":"...","count":N,"severity":1-5,"example":"o kategoriden kısa bir alıntı"}]}. '
            "severity: şikayetin ciddiyeti (5=iade/ürün kusuru, 1=küçük memnuniyetsizlik)."
        )
        user_text = "YORUMLAR:\n" + "\n".join(comments[:300])
        try:
            txt = await llm_chat(api_key, _provider, _model, sys_msg, user_text, max_tokens=1500)
            import json as _json
            import re as _re2
            _m = _re2.search(r"\{.*\}", (txt or "").strip(), _re2.DOTALL)
            data = _json.loads(_m.group(0)) if _m else {}
            for r in (data.get("reasons") if isinstance(data, dict) else []) or []:
                if not isinstance(r, dict):
                    continue
                try:
                    c = int(r.get("count") or 0)
                except Exception:
                    c = 0
                try:
                    sev = int(r.get("severity") or 0)
                except Exception:
                    sev = 0
                nm = str(r.get("reason") or "").strip()[:80]
                if nm:
                    out.append({"reason": nm, "count": max(0, c), "severity": max(0, min(5, sev)),
                                "example": str(r.get("example") or "").strip()[:220]})
        except Exception as e:
            logger.warning(f"[yorum-analiz] LLM hata: {e}")

    # AI sonuç üretemediyse (anahtar yok / hata / boş) → anahtar-kelime tabanlı YEDEK (her zaman sonuç).
    if not out:
        method = "keyword" if api_key else "keyword_no_ai"
        _CATS = [
            ("Kalıp dar/küçük", ["kalıp", "kalip", "dar ", " dar", "küçük", "kucuk", "small", "xs gibi", "beden değil", "beden degil"], 4),
            ("Kumaş kalitesiz", ["kumaş", "kumas", "kalitesiz", "polyester", "ince", "tok durmu", "kalitesi kötü", "kalitesi kotu"], 4),
            ("Dikiş/işçilik", ["dikiş", "dikis", "söküldü", "sokuldu", "açıldı", "acildi", "işçilik", "iscilik", "dikişi attı", "dikisi atti"], 4),
            ("Beden/ölçü uyumsuz", ["beden", "ölçü", "olcu", "numara", "büyük geldi", "buyuk geldi", "geniş", "genis"], 3),
            ("Fiyat/değer", ["fiyat", "para etmez", "pahalı", "pahali", "değmez", "degmez", "hak etmiyor", "israf", "para yazık", "para yazik"], 3),
            ("Kargo/paketleme", ["kargo", "paket", "geç geldi", "gec geldi", "eksik geldi"], 2),
            ("Görselden farklı", ["farklı", "farkli", "resimde", "görselde", "gorselde", "göründüğü", "gorundugu", "beklenti"], 3),
            ("Renk farklı/soluk", ["renk farklı", "renk farkli", "soluk", "soldu", "rengi farklı", "rengi farkli"], 3),
            ("Koku/leke", ["koku", "kokuyor", "leke", "kirli geldi"], 4),
        ]
        _low = [(_c.lower()) for _c in comments]
        for name, kws, sev in _CATS:
            cnt = 0
            example = ""
            for i, txtc in enumerate(_low):
                if any(k in txtc for k in kws):
                    cnt += 1
                    if not example:
                        example = comments[i][:220]
            if cnt > 0:
                out.append({"reason": name, "count": cnt, "severity": sev, "example": example})

    out.sort(key=lambda x: x["count"])  # küçükten büyüğe (kullanıcı isteği)
    return {"ok": True, "total_reviews": len(comments), "reasons": out,
            "method": method, "provider": _provider, "model": _model,
            "message": "" if out else "Yorumlar bulundu ama neden çıkarılamadı."}


@router.get("/trendyol/reviews/by-product")
async def reviews_by_product(limit: int = 300, current_user: dict = Depends(require_admin)):
    """Hangi ürüne kaç Trendyol yorumu çekildiğini listeler (en çok yorumlu önce)."""
    limit = max(1, min(limit, 2000))
    pipeline = [
        {"$match": {"source": "trendyol_public", "product_id": {"$ne": None}}},
        {"$group": {
            "_id": "$product_id",
            "count": {"$sum": 1},
            "avg": {"$avg": "$rating"},
            "last": {"$max": "$created_at"},
        }},
        {"$sort": {"count": -1}},
        {"$limit": limit},
    ]
    rows = []
    async for r in db.product_reviews.aggregate(pipeline):
        pid = r["_id"]
        p = await db.products.find_one({"id": pid}, {"_id": 0, "name": 1})
        rows.append({
            "product_id": pid,
            "name": (p or {}).get("name") or "—",
            "count": r["count"],
            "avg": round(r.get("avg") or 0, 1),
            "last": r.get("last") or "",
        })
    total = await db.product_reviews.count_documents({"source": "trendyol_public"})
    return {"total_reviews": total, "product_count": len(rows), "products": rows}


async def weekly_trendyol_review_sync():
    """Scheduler: HAFTADA BİR 4-5 yıldız Trendyol yorumlarını otomatik çeker.
    Yalnızca worker/proxy ayarlıysa çalışır (aksi halde 530 alır, boşuna uğraşmaz)."""
    import logging as _logging
    log = _logging.getLogger("trendyol-reviews")
    try:
        s = await db.settings.find_one({"id": "trendyol"}, {"_id": 0}) or {}
        has_relay = bool(s.get("review_worker_url") or s.get("review_proxy")
                         or os.environ.get("TRENDYOL_REVIEW_WORKER") or os.environ.get("TRENDYOL_REVIEW_PROXY"))
        if not has_relay:
            log.info("[trendyol-reviews] haftalik sync atlandi — worker/proxy ayarli degil")
            return
        _mr = int(s.get("review_min_rating") or 3)          # admin ayarı; varsayılan 3 yıldız ve üzeri
        _mr = max(1, min(5, _mr))
        summary = await sync_all_trendyol_reviews_core(min_rating=_mr, limit=0, dry_run=False)
        log.info("[trendyol-reviews] haftalik sync: eslesen=%s eklenen=%s hata=%s",
                 summary.get("matched_products"), summary.get("total_inserted"), len(summary.get("errors") or []))
    except Exception as e:
        log.warning("[trendyol-reviews] haftalik sync hata: %s", e)


@router.get("/trendyol/reviews/fetch-config")
async def get_review_fetch_config(current_user: dict = Depends(require_admin)):
    """Yorum çekme yolu ayarı (Cloudflare Worker URL / proxy) — 530 engelini aşmak için."""
    s = await db.settings.find_one({"id": "trendyol"}, {"_id": 0}) or {}
    return {
        "review_worker_url": s.get("review_worker_url", ""),
        "review_proxy_set": bool(s.get("review_proxy")),
        "review_min_rating": int(s.get("review_min_rating") or 3),   # kaç yıldız ve üzeri çekilsin (varsayılan 3)
    }


def _assert_safe_relay_url(raw: str, field: str, require_https: bool = True) -> None:
    """A2.9 — SSRF savunması (defense-in-depth). Admin'in girdiği vekil/worker adresinin
    özel/loopback/link-local/metadata IP'ye çözülmediğini yazma anında doğrular. Böylece
    ele geçirilmiş veya dikkatsiz bir admin token'ı sunucuyu iç ağa/metadata uçlarına
    (169.254.169.254 vb.) yönlendiremez. Çözümleme başarısızsa (geçici DNS) engellemez."""
    import ipaddress as _ip
    import socket as _sock
    from urllib.parse import urlparse as _urlparse
    if not raw:
        return
    try:
        u = _urlparse(raw)
    except Exception:
        raise HTTPException(status_code=400, detail=f"{field}: geçersiz URL")
    scheme = (u.scheme or "").lower()
    if require_https and scheme not in ("https",):
        raise HTTPException(status_code=400, detail=f"{field}: yalnızca https adres kabul edilir")
    if scheme not in ("http", "https"):
        raise HTTPException(status_code=400, detail=f"{field}: geçersiz şema")
    host = u.hostname or ""
    if not host:
        raise HTTPException(status_code=400, detail=f"{field}: host eksik")
    if host.lower() in ("localhost", "metadata.google.internal") or host.lower().endswith(".internal"):
        raise HTTPException(status_code=400, detail=f"{field}: dahili adres reddedildi")
    try:
        infos = _sock.getaddrinfo(host, None)
    except Exception:
        return  # geçici DNS hatası → admin'i bloklama (best-effort)
    for info in infos:
        addr = info[4][0]
        try:
            ipobj = _ip.ip_address(addr.split("%")[0])
        except ValueError:
            continue
        if (ipobj.is_private or ipobj.is_loopback or ipobj.is_link_local
                or ipobj.is_reserved or ipobj.is_multicast or ipobj.is_unspecified):
            raise HTTPException(status_code=400,
                                detail=f"{field}: özel/dahili IP'ye çözülüyor ({addr}) — reddedildi")


@router.put("/trendyol/reviews/fetch-config")
async def set_review_fetch_config(payload: dict, current_user: dict = Depends(require_admin)):
    """Cloudflare Worker URL veya proxy adresini kaydeder. Trendyol yorumları bunlar üzerinden çekilir."""
    upd = {}
    if "review_worker_url" in (payload or {}):
        _wu = str(payload.get("review_worker_url") or "").strip()
        if _wu:
            _assert_safe_relay_url(_wu, "review_worker_url", require_https=True)
        upd["review_worker_url"] = _wu
    if "review_proxy" in (payload or {}):
        _px = str(payload.get("review_proxy") or "").strip()
        if _px:
            # Proxy http/https olabilir (residential proxy'ler çoğunlukla http CONNECT).
            _assert_safe_relay_url(_px, "review_proxy", require_https=False)
        upd["review_proxy"] = _px
    if "review_min_rating" in (payload or {}):
        try:
            upd["review_min_rating"] = max(1, min(5, int(payload.get("review_min_rating"))))
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="review_min_rating 1-5 arası olmalı")
    if not upd:
        raise HTTPException(status_code=400, detail="review_worker_url, review_proxy veya review_min_rating gerekli")
    await db.settings.update_one({"id": "trendyol"}, {"$set": upd, "$setOnInsert": {"id": "trendyol"}}, upsert=True)
    return {"success": True}
