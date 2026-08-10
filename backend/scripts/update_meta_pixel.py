"""Facette Meta Pixel + CAPI kaydını gerçek Pixel ID ve token ile günceller.
PDF'ten alınan: Pixel ID 1309645730719851 + CAPI access token.
"""
import asyncio
import os
import sys
from datetime import datetime, timezone
from hashlib import sha1

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv

load_dotenv()

PIXEL_ID = "1309645730719851"
# PDF satır kaydırmasındaki boşluklar temizlendi (Meta token'ı boşluk içermez)
TOKEN = (
    "EAAMFOB1wH1oBRI9FIqgoXntSRtaZA8w2rrdXIbNHeeGtiMjIjipbP2DA9CORXnz8EsALZAUBA"
    "V7IERjIP1hMqdkys9yjcsSSWtTCwtrCn3hKp3dRW7tMIHCt0ifdwU59OzZCQDZAeZBRZBkJkFL"
    "w9vvfmLC4V5ZB8ksZBvnK8RiW2oWagvuwl3oBQbRkwn5TILye3gZDZD"
)


def meta_head_snippet(pixel_id: str) -> str:
    return f"""<!-- Meta Pixel -->
<script>
!function(f,b,e,v,n,t,s)
{{if(f.fbq)return;n=f.fbq=function(){{n.callMethod?
n.callMethod.apply(n,arguments):n.queue.push(arguments)}};
if(!f._fbq)f._fbq=n;n.push=n;n.loaded=!0;n.version='2.0';
n.queue=[];t=b.createElement(e);t.async=!0;
t.src=v;s=b.getElementsByTagName(e)[0];
s.parentNode.insertBefore(t,s)}}(window, document,'script',
'https://connect.facebook.net/en_US/fbevents.js');
fbq('init', '{pixel_id}');
fbq('track', 'PageView');
</script>
<noscript><img height="1" width="1" style="display:none"
src="https://www.facebook.com/tr?id={pixel_id}&ev=PageView&noscript=1"/></noscript>"""


async def main():
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]

    now_iso = datetime.now(timezone.utc).isoformat()
    sig = sha1(f"meta-{PIXEL_ID}-default".encode()).hexdigest()[:10]
    vault_key = f"capi_meta_{sig}"

    # Token'ı vault'a şifreli yaz
    from security.crypto import encrypt as vault_encrypt
    enc = vault_encrypt(TOKEN)
    await db.vault_secrets.update_one(
        {"key": vault_key},
        {"$set": {
            "key": vault_key,
            "value_enc": enc,
            "description": "CAPI meta token — Facette Pixel",
            "scope": "capi",
            "updated_at": now_iso,
        }, "$setOnInsert": {"created_at": now_iso}},
        upsert=True,
    )

    existing = await db.marketing_pixels.find_one({"provider": "meta"})
    data = {
        "provider": "meta",
        "name": "Meta Pixel (Facette)",
        "tag_id": PIXEL_ID,
        "head_snippet": meta_head_snippet(PIXEL_ID),
        "body_snippet": "",
        "is_active": True,
        "capi_enabled": True,
        "access_token": "",  # plain saklanmaz
        "vault_key": vault_key,
        "env_token_key": None,
        "test_event_code": None,
        "tenant_id": None,
        "extra": {},
        "updated_at": now_iso,
    }
    if existing:
        await db.marketing_pixels.update_one({"id": existing["id"]}, {"$set": data})
        print(f"Updated meta pixel id={existing['id']}")
    else:
        import uuid
        data["id"] = uuid.uuid4().hex
        data["created_at"] = now_iso
        await db.marketing_pixels.insert_one(data)
        print(f"Inserted meta pixel id={data['id']}")

    # Doğrulama
    px = await db.marketing_pixels.find_one({"provider": "meta"}, {"_id": 0, "tag_id": 1, "capi_enabled": 1, "is_active": 1, "vault_key": 1})
    vs = await db.vault_secrets.find_one({"key": vault_key}, {"_id": 0, "key": 1})
    print("pixel:", px)
    print("vault:", vs)

    # decrypt round-trip test
    from security.crypto import decrypt as vault_decrypt
    dec = vault_decrypt(enc)
    print("decrypt ok:", dec[:12] + "..." + dec[-6:], "len=", len(dec))


if __name__ == "__main__":
    asyncio.run(main())
