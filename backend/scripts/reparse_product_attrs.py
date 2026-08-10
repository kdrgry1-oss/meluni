"""One-off: Re-parse product descriptions to populate attributes (dynamic
parser based on `<strong>Etiket:</strong>` HTML pattern). Touches only
`attributes` and `description_plain` — variants/sizes are not modified.

Run: `python /app/backend/scripts/reparse_product_attrs.py`
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from routes.deps import db  # noqa: E402
from utils.attr_parser import parse_description_attributes  # noqa: E402


async def main():
    prods = await db.products.find(
        {"source": "xml_feed"},
        {"_id": 0, "id": 1, "description": 1, "name": 1},
    ).to_list(None)

    updated = 0
    enriched = 0
    sample = None
    for p in prods:
        attrs, _plain = parse_description_attributes(p.get("description") or "")
        await db.products.update_one(
            {"id": p["id"]},
            {"$set": {"attributes": attrs}},
        )
        updated += 1
        if attrs:
            enriched += 1
            if sample is None:
                sample = {"name": p.get("name"), "attributes": attrs}

    print(f"Updated {updated} products, enriched {enriched} with attributes.")
    if sample:
        import json
        print("\nSample:")
        print(json.dumps(sample, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
