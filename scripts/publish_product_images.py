#!/usr/bin/env python3
"""One-shot, compare-and-set publication for reviewed Meluni product images."""
from __future__ import annotations
import asyncio, hashlib, json, os, sys, uuid
from datetime import datetime, timezone
from pathlib import Path

# The script is staged outside the application package and executed by file
# path. Make the checked-out backend the import root for its existing modules.
sys.path.insert(0, os.getcwd())
from motor.motor_asyncio import AsyncIOMotorClient
from services import r2_storage as r2
from routes.deps import db, client

EXPECTED_PUBLICATION = "meluni-images-20261110-v1"
EXPECTED_PRODUCTS = {
    "0179e9a2-0510-494c-b5ec-da95cb982ac3",
    "5a57a060-5f72-40ec-95c0-37d29efb5b78",
    "0149d26f-841c-4c99-8e32-16d6ae15173b",
    "88e0630a-61f4-4704-b84c-1bab9ff67e7f",
}
VIEW_ORDER = ("front", "back", "fashion_model", "size_chart")
ROLE_NAMES = {"front":"front", "back":"back", "fashion_model":"model", "size_chart":"size-chart"}


def canon(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def sha256(path: Path) -> str:
    h=hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda:f.read(1024*1024), b""):
            h.update(block)
    return h.hexdigest()

async def main(payload_path: Path, assets_root: Path, status_path: Path):
    payload=json.loads(payload_path.read_text())
    publication_id=payload.get("publication_id")
    products=payload.get("products") or []
    ids={p.get("product_id") for p in products}
    if publication_id != EXPECTED_PUBLICATION or ids != EXPECTED_PRODUCTS or len(products)!=4:
        raise RuntimeError("publication package scope mismatch")
    if payload.get("publish_authorized_by_user") is not True:
        raise RuntimeError("explicit publication authorization marker missing")

    current_by_id={}
    already={}
    skipped={}
    for p in products:
        pid=p["product_id"]
        if set((p.get("assets") or {}).keys()) != set(VIEW_ORDER):
            raise RuntimeError(f"asset views mismatch for {pid}")
        for view in VIEW_ORDER:
            a=p["assets"][view]
            if a.get("sample") != p.get("sample") or "_pass" not in a.get("qa_status","") and "_verified" not in a.get("qa_status",""):
                raise RuntimeError(f"QA marker missing for {pid} {view}")
            f=assets_root / a["file"]
            if not f.is_file() or sha256(f) != a.get("sha256"):
                raise RuntimeError(f"asset missing or checksum mismatch for {pid} {view}")
        doc=await db.products.find_one({"id":pid},{"_id":0,"id":1,"name":1,"is_active":1,"active":1,"stock":1,"images":1,"images_custom":1,"ai_images_publication_id":1,"ai_images_published_assets":1})
        if not doc:
            skipped[pid]="product_not_found"
            continue
        if doc.get("ai_images_publication_id")==publication_id:
            urls=doc.get("ai_images_published_assets") or {}
            if set(urls.keys()) != set(VIEW_ORDER) or not all(urls.values()):
                raise RuntimeError(f"published marker incomplete for {pid}")
            already[pid]=urls
            current_by_id[pid]=doc
            continue
        active=doc.get("is_active",doc.get("active"))
        try: stock=float(doc.get("stock") or 0)
        except (TypeError,ValueError): stock=0
        if active is not True or stock <= 0:
            skipped[pid]="inactive_or_out_of_stock"
            continue
        if doc.get("images_custom") is True:
            skipped[pid]="custom_gallery_preserved"
            continue
        if canon(doc.get("name")) != canon(p.get("name")):
            skipped[pid]="product_name_changed"
            continue
        if canon(doc.get("images") or []) != canon(p.get("expected_original_images") or []):
            skipped[pid]="original_gallery_changed"
            continue
        current_by_id[pid]=doc

    if not current_by_id:
        raise RuntimeError("no eligible product remains after live catalog preflight")

    if not r2.is_enabled():
        raise RuntimeError("public R2 storage is not configured on the production server")
    health=r2.health_check()
    if not health.get("ok"):
        raise RuntimeError("public R2 health check failed")
    public_base=(os.environ.get("R2_PUBLIC_URL") or "").strip()
    if not public_base.startswith("https://"):
        raise RuntimeError("R2 public URL is not HTTPS")

    staged={}
    now=datetime.now(timezone.utc).isoformat()
    for p in products:
        pid=p["product_id"]
        if pid in skipped:
            continue
        if pid in already:
            staged[pid]=already[pid]
            continue
        staged[pid]={}
        for view in VIEW_ORDER:
            a=p["assets"][view]; f=assets_root/a["file"]; digest=a["sha256"]
            filename=f"{pid}-{ROLE_NAMES[view]}-{digest[:12]}.jpg"
            key=f"ai-products/{filename}"
            data=f.read_bytes()
            url=r2.put_object(key,data,"image/jpeg")
            staged[pid][view]=url
            staged[pid][f"_record_{view}"]={"id":str(uuid.uuid5(uuid.NAMESPACE_URL,f"meluni:{publication_id}:{pid}:{view}")),"storage_path":filename,"r2_key":key,"r2_url":url,"original_filename":f.name,"content_type":"image/jpeg","size":len(data),"source":"ai_generated","product_id":pid,"pose":ROLE_NAMES[view],"is_deleted":False,"created_at":now}

    pending=[p for p in products if p["product_id"] not in already and p["product_id"] not in skipped]
    if pending:
        async with await client.start_session() as session:
            async with session.start_transaction():
                for p in pending:
                    pid=p["product_id"]; doc=current_by_id[pid]; urls=staged[pid]
                    old=list(doc.get("images") or [])
                    new_images=[urls["front"],urls["back"],urls["fashion_model"]]+old+[
                        {"id":str(uuid.uuid5(uuid.NAMESPACE_URL,f"meluni:{publication_id}:{pid}:size_chart_gallery")),"url":urls["size_chart"],"is_size_table":True,"alt":"Ölçü Tablosu","created_at":now}
                    ]
                    filt={"id":pid,"images":doc.get("images") or [],"images_custom":{"$ne":True},"stock":{"$gt":0}}
                    if doc.get("is_active") is not None: filt["is_active"]=True
                    else: filt["active"]=True
                    result=await db.products.update_one(filt,{"$set":{"images":new_images,"images_custom":True,"images_custom_at":now,"ai_images_generated_at":now,"ai_images_publication_id":publication_id,"ai_images_published_assets":{v:urls[v] for v in VIEW_ORDER}}},session=session)
                    if result.modified_count != 1:
                        raise RuntimeError(f"compare-and-set failed for {pid}; no gallery update committed")
                    for view in VIEW_ORDER:
                        record=urls[f"_record_{view}"]
                        await db.files.update_one({"r2_key":record["r2_key"]},{"$setOnInsert":record},upsert=True,session=session)
                    job_id=f"{publication_id}:{pid}"
                    await db.ai_image_jobs.update_one({"id":job_id},{"$setOnInsert":{"id":job_id,"product_id":pid,"poses":list(VIEW_ORDER),"generated":{v:urls[v] for v in VIEW_ORDER},"errors":{},"model":"reviewed-product-image-draft","size":"1200x1800","publication_id":publication_id,"created_at":now}},upsert=True,session=session)

    results=[]
    for p in products:
        pid=p["product_id"]
        if pid in skipped:
            continue
        doc=await db.products.find_one({"id":pid},{"_id":0,"id":1,"is_active":1,"stock":1,"images_custom":1,"ai_images_publication_id":1,"ai_images_published_assets":1})
        if not doc or doc.get("ai_images_publication_id")!=publication_id or doc.get("images_custom") is not True:
            raise RuntimeError(f"post-write verification failed for {pid}")
        urls=doc.get("ai_images_published_assets") or {}
        if set(urls.keys()) != set(VIEW_ORDER):
            raise RuntimeError(f"post-write URL check failed for {pid}")
        results.append({"product_id":pid,"sample":p["sample"],"name":p["name"],"active":doc.get("is_active"),"stock":doc.get("stock"),"publication_id":publication_id,"urls":urls,"gallery_image_count":None})
    status={"status":"published_partial_and_verified" if skipped else "published_and_verified","publication_id":publication_id,"published_at_utc":now,"catalog_poll_at_staging":payload.get("catalog_poll_at"),"products":results,"skipped":[{"product_id":p["product_id"],"sample":p["sample"],"reason":skipped[p["product_id"]]} for p in products if p["product_id"] in skipped],"notes":"R2 object checks passed, gallery updates used transactional compare-and-set, original images preserved, generated size chart last."}
    status_path.write_text(json.dumps(status,ensure_ascii=False,indent=2)+"\n")
    print(json.dumps({"status":status["status"],"publication_id":publication_id,"products":len(results),"skipped":len(skipped),"asset_count":len(results)*4},ensure_ascii=False))

if __name__=="__main__":
    if len(sys.argv)!=4: raise SystemExit("usage: publish_product_images.py payload.json assets_root status.json")
    try:
        asyncio.run(main(Path(sys.argv[1]),Path(sys.argv[2]),Path(sys.argv[3])))
    except Exception as e:
        print(json.dumps({"status":"failed","reason":str(e)},ensure_ascii=False),file=sys.stderr)
        raise
    finally:
        try: client.close()
        except Exception: pass
