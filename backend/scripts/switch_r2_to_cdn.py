"""R2 public URL'lerini (pub-...r2.dev) Cloudflare özel domainine (cdn.facette.com.tr)
çevirir. Özel domain + Image Transformations (cdn-cgi/image) ile dinamik AVIF/resize
sağlanır. Ürün görselleri için kaynak boyutu en büyüğe (1280), pagedesign için 1920'ye
sabitlenir ki cdn-cgi her zaman aşağı ölçeklensin (keskin kalır).

Kapsam: products.images/thumbnail, page_blocks (iç içe), files.r2_url, frontend dosyaları.

Kullanım:
    python -m scripts.switch_r2_to_cdn --apply
"""
import os
import re
import sys

from dotenv import load_dotenv
from pymongo import MongoClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env"))

OLD_HOST = "pub-287559dafa814b76bdf81ca894c818bf.r2.dev"
NEW_HOST = "cdn.facette.com.tr"
FRONTEND = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "frontend", "src")
FILES = ["pages/Home.jsx", "components/Header.jsx", "pages/admin/PageDesign.jsx", "lib/img.js"]


def convert(u: str) -> str:
    if not isinstance(u, str) or OLD_HOST not in u:
        return u
    u = u.replace(OLD_HOST, NEW_HOST)
    if "/products/" in u:
        u = re.sub(r"-\d+\.webp", "-1280.webp", u)
    elif "/pagedesign/" in u:
        u = re.sub(r"-\d+\.webp", "-1920.webp", u)
    return u


def walk(obj):
    if isinstance(obj, str):
        return convert(obj)
    if isinstance(obj, list):
        return [walk(x) for x in obj]
    if isinstance(obj, dict):
        return {k: walk(v) for k, v in obj.items()}
    return obj


def main(apply: bool):
    client = MongoClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]

    # products
    prod_changed = 0
    for p in db.products.find({"$or": [{"images": {"$elemMatch": {"$regex": OLD_HOST}}},
                                       {"thumbnail": {"$regex": OLD_HOST}}]},
                              {"id": 1, "images": 1, "thumbnail": 1}):
        new_imgs = [convert(u) for u in (p.get("images") or [])]
        upd = {"images": new_imgs}
        if OLD_HOST in str(p.get("thumbnail") or ""):
            upd["thumbnail"] = convert(p["thumbnail"])
        if apply:
            db.products.update_one({"id": p["id"]}, {"$set": upd})
        prod_changed += 1
    print(f"products: {prod_changed}")

    # page_blocks
    import json
    pb_changed = 0
    for doc in db.page_blocks.find({}):
        if OLD_HOST not in json.dumps(doc, default=str):
            continue
        pb_changed += 1
        if apply:
            new = walk({k: v for k, v in doc.items() if k != "_id"})
            db.page_blocks.update_one({"_id": doc["_id"]}, {"$set": new})
    print(f"page_blocks: {pb_changed}")

    # files.r2_url
    f_changed = 0
    for rec in db.files.find({"r2_url": {"$regex": OLD_HOST}}, {"_id": 1, "r2_url": 1}):
        if apply:
            db.files.update_one({"_id": rec["_id"]}, {"$set": {"r2_url": convert(rec["r2_url"])}})
        f_changed += 1
    print(f"files.r2_url: {f_changed}")

    # frontend files
    for rel in FILES:
        path = os.path.join(FRONTEND, rel)
        if not os.path.exists(path):
            continue
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        if OLD_HOST not in content:
            continue
        # convert each occurrence
        def _repl(mo):
            return convert(mo.group(0))
        new_content = re.sub(r"https://" + re.escape(OLD_HOST) + r"[^\"'\s\\)]*", _repl, content)
        print(f"{rel}: {content.count(OLD_HOST)} occurrence(s)")
        if apply:
            with open(path, "w", encoding="utf-8") as f:
                f.write(new_content)

    print("\nMod:", "UYGULANDI" if apply else "DRY-RUN")


if __name__ == "__main__":
    main("--apply" in sys.argv)
