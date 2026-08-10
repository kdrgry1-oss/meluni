import asyncio
from motor.motor_asyncio import AsyncIOMotorClient
import bcrypt
import os
import pathlib

# Auto-detect MongoDB connection (same logic as deps.py)
MONGO_URL = os.environ.get('MONGO_URL')
if not MONGO_URL:
    sock = pathlib.Path('/tmp/mongodb-27017.sock')
    if sock.exists():
        MONGO_URL = 'mongodb://%2Ftmp%2Fmongodb-27017.sock'
    else:
        MONGO_URL = 'mongodb://127.0.0.1:27017'

db_name = os.environ.get('DB_NAME', 'test_database')

async def reset():
    client = AsyncIOMotorClient(MONGO_URL)
    db = client[db_name]
    
    # GÜVENLİK: sabit "admin123" varsayılanı kaldırıldı. ADMIN_RESET_PASSWORD env
    # zorunlu; verilmezse betik hiçbir şey yapmadan durur (zayıf parola dayatılmaz).
    new_password = os.environ.get("ADMIN_RESET_PASSWORD", "").strip()
    if len(new_password) < 10:
        print("HATA: ADMIN_RESET_PASSWORD ortam değişkeni (>=10 karakter) gerekli. İşlem iptal.")
        return
    hashed = bcrypt.hashpw(new_password.encode(), bcrypt.gensalt()).decode()
    
    result = await db.users.update_one(
        {"email": "admin@facette.com"},
        {"$set": {"password": hashed}},
        upsert=False
    )
    
    if result.matched_count == 0:
        # Admin user doesn't exist, create it
        import uuid
        from datetime import datetime, timezone
        await db.users.insert_one({
            "id": str(uuid.uuid4()),
            "email": "admin@facette.com",
            "password": hashed,
            "first_name": "Admin",
            "last_name": "User",
            "is_admin": True,
            "is_active": True,
            "created_at": datetime.now(timezone.utc).isoformat()
        })
        print("Admin user CREATED: admin@facette.com (parola ADMIN_RESET_PASSWORD ile ayarlandı)")
    else:
        print(f"Admin password RESET. Matched: {result.matched_count}, Modified: {result.modified_count}")
        print("Login: admin@facette.com (parola ADMIN_RESET_PASSWORD ile ayarlandı)")

if __name__ == "__main__":
    asyncio.run(reset())
