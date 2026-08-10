# 🚀 Facette Production Architecture & Migration Guide

## 1. Mimari Genel Bakış

```
┌─────────────────────────────────────────────────────────────────────┐
│                         CLIENT (Web + Mobile)                       │
│   React SPA  │  Capacitor Android APK  │  Capacitor iOS IPA         │
└──────────────┬──────────────────────────────────────────────────────┘
               │ HTTPS (REACT_APP_BACKEND_URL)
               ▼
┌─────────────────────────────────────────────────────────────────────┐
│                      LOAD BALANCER + CDN                            │
│        (Cloudflare / AWS ALB / NGINX) — TLS, WAF, rate limit        │
└──────────────┬──────────────────────────────────────────────────────┘
               │
               ▼
┌─────────────────────────────────────────────────────────────────────┐
│        FastAPI Backend (Uvicorn + Gunicorn workers)                 │
│  ┌───────────────────────────────────────────────────────────────┐  │
│  │ Middleware Stack (üstten alta çalışır)                        │  │
│  │  • SecurityHeadersMiddleware (CSP, HSTS, XFO, ...)            │  │
│  │  • SlowAPI Rate Limiter (login/register tier'lı)              │  │
│  │  • IntegrationLoggingMiddleware (marketplace çağrıları)       │  │
│  │  • ErrorTrackingMiddleware (5xx → error_logs + alert)         │  │
│  │  • CORS                                                       │  │
│  └───────────────────────────────────────────────────────────────┘  │
│                                                                     │
│  Routers: /api/auth, /api/products, /api/admin/vault, ...           │
│           /api/admin/system/{health,errors,alerts,circuits,cache}   │
└──────────────┬──────────────────────────────────────────────────────┘
               │
   ┌───────────┼─────────────┬───────────────────┐
   ▼           ▼             ▼                   ▼
┌────────┐ ┌────────┐ ┌──────────────┐  ┌──────────────────┐
│MongoDB │ │ Redis  │ │ External APIs│  │ SMTP / Resend    │
│(replica│ │(cache+ │ │(Trendyol,DHL,│  │ (alerts mailing) │
│  set)  │ │ queue) │ │ Iyzico,...)  │  │                  │
└────────┘ └────────┘ └──────────────┘  └──────────────────┘
```

## 2. Güvenlik Katmanları

| Katman | Amaç | Dosya / Konfig |
| --- | --- | --- |
| **TLS / HSTS** | Tüm trafik HTTPS, MITM engeli | `SecurityHeadersMiddleware`, `Strict-Transport-Security` |
| **CSP / XFO / XCTO** | XSS / clickjacking / MIME sniffing engeli | `SecurityHeadersMiddleware` |
| **JWT (HS256, strict)** | Kimlik doğrulama; `alg=none` saldırısına kapalı | `routes/deps.py: _decode_jwt_strict` |
| **Bcrypt (12 round)** | Şifre saklama | `routes/deps.py: hash_password` |
| **Rate Limit** | Brute force engeli (`/auth/login` → 5/dk + IP-block) | `slowapi` + `routes/deps.py: register_failed_login_ip` |
| **NoSQL Injection guard** | `$`/`{}` payload reddi | `routes/deps.py: safe_str / is_safe_email` |
| **Audit Log** | Auth + vault + admin kritik aksiyonlar | `auth_audit_logs` koleksiyonu |
| **Secrets Vault (AES-256-GCM)** | API keyleri DB'de düz metin yok | `security/crypto.py`, `routes/secrets_vault.py` |
| **Field redaction** | Non-superadmin maskelenmiş görür | `security/redactor.py` |
| **Error monitoring + email alerts** | 5xx burst → `kdrgry@gmail.com` | `security/monitoring.py`, `security/alerts.py` |
| **Circuit breaker** | Bozuk upstream'i izole et | `security/circuit_breaker.py` |

### Secrets Vault Kullanımı

1. **Master key**: `/app/backend/.env` içindeki `SECRETS_MASTER_KEY` — 32 byte base64url Fernet key.
   Yenisini üretmek için:
   ```bash
   python -c "from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())"
   ```

2. **Admin UI**: `/admin/secrets-vault` → süper admin değer ekler/günceller. Diğer adminler sadece "set/değil" görür.

3. **Kod tarafında okuma**:
   ```python
   from routes.secrets_vault import get_secret
   trendyol_key = await get_secret("TRENDYOL_API_KEY")
   ```

## 3. Email Alarm Kurulumu

`/app/backend/.env` — alarm bildirimi `ALERT_TO_EMAIL`'e (varsayılan **kdrgry@gmail.com**) gider.

### Seçenek A — Gmail SMTP (önerilen, ücretsiz)
1. <https://myaccount.google.com/apppasswords> üzerinden Google App Password oluştur
2. `.env` doldur:
   ```
   ALERT_SMTP_HOST=smtp.gmail.com
   ALERT_SMTP_PORT=587
   ALERT_SMTP_USER=kdrgry@gmail.com
   ALERT_SMTP_PASSWORD=<16-haneli app password>
   ALERT_SMTP_FROM=kdrgry@gmail.com
   ```
3. Backend'i restart et: `sudo supervisorctl restart backend`
4. `/admin/sistem-sagligi` → "Test Alarmı Gönder" tıkla. Mail gelmelidir.

### Seçenek B — Resend (transactional, daha güvenilir)
1. <https://resend.com/api-keys> üzerinden key al
2. `.env`:
   ```
   RESEND_API_KEY=re_xxxxxxxxxxxx
   RESEND_FROM=alerts@yourdomain.com  # doğrulanmış domain
   ```

> SMTP varsa öncelikle SMTP kullanılır; başarısızsa Resend devreye girer; her ikisi de yoksa alarm sadece DB'ye düşer (in-app gösterim).

## 4. Yüksek Trafik (100K eş zamanlı kullanıcı) için Kontrol Listesi

### Uygulama Katmanı
- [x] **Async stack** (FastAPI + Motor) — bloklayıcı I/O yok
- [x] **Connection pooling** — Mongo & HTTPX default'larıyla yeterli (Mongo: 100 socket/pod)
- [x] **Cache layer** (`/app/backend/cache.py`) — Redis aktifse hit oranı %70+
- [ ] **Worker scaling** — `gunicorn -k uvicorn.workers.UvicornWorker -w (2*CPU+1)`
- [ ] **CDN** — statik asset (resim, JS bundle) için Cloudflare CDN aç

### Veritabanı Katmanı (MongoDB)
- [x] Index'ler tanımlı: `products.slug`, `orders.order_number`, `users.email`, audit, alerts, vault.
- [ ] **Replica Set (3 node)**: read scaling + HA. `MONGO_URL=mongodb://m1,m2,m3/?replicaSet=rs0&readPreference=secondaryPreferred`
- [ ] Slow-query log: `db.setProfilingLevel(1, { slowms: 100 })`
- [ ] Atlas / Mongo M30+ → 100K user için 6+ node cluster önerilir

### Redis (cache + queue)
- Cache: ürün listesi, kategori, popüler aramalar
- Queue: Trendyol sync, push notification, email send (Celery / RQ)
- 100K user için: Redis Sentinel veya Redis Cluster (3 master + 3 replica), 16-32 GB RAM

### Diğer
- **Circuit breaker** marketplace çağrılarında zaten devrede (`security/circuit_breaker.py`)
- **Background jobs**: bozulduğunda kuyruğa düşer, kullanıcı bekletilmez
- **Load balancing**: NGINX/ALB ile pod-bazlı sticky session yok (JWT stateless)

## 5. Sunucu Taşıma Rehberi

### Hazırlık (eski sunucuda)
```bash
# 1. MongoDB dump
mongodump --uri="$MONGO_URL" --out=/backup/$(date +%F)

# 2. .env yedekle (KRİTİK — özellikle SECRETS_MASTER_KEY)
cp /app/backend/.env /backup/backend.env.$(date +%F)

# 3. Yüklü dosyalar (varsa)
tar czf /backup/uploads-$(date +%F).tgz /app/backend/static /app/backend/imports

# 4. Kod (git ile yapın — Save to GitHub butonu)
```

### Yeni sunucuda kurulum
```bash
# 1. Repository klonu
git clone <repo> /app

# 2. Python deps
cd /app/backend && pip install -r requirements.txt

# 3. Node deps
cd /app/frontend && yarn install && yarn build

# 4. .env restore — SECRETS_MASTER_KEY AYNI olmalı, aksi takdirde
#    Vault'taki şifreli değerler decode edilemez!
cp /backup/backend.env.* /app/backend/.env

# 5. MongoDB restore
mongorestore --uri="$MONGO_URL" /backup/<tarih>

# 6. Yüklü dosyaları çıkar
tar xzf /backup/uploads-*.tgz -C /

# 7. Redis (yeni sunucuda)
docker run -d --name redis -p 6379:6379 redis:7-alpine
# .env: REDIS_URL=redis://localhost:6379/0
# (Eski Redis'te kalan cache verisi taşınmaz; ısınması saniyeler sürer)

# 8. Supervisor + nginx ayarla, servisleri başlat
sudo supervisorctl restart all
```

### Master Key Rotasyonu (önerilen yıllık)
```python
# Yeni key üret, .env'e geçici hem eski hem yeni keyi koy
# tüm vault_secrets'ı re-encrypt et:
from security.crypto import rotate_secret  # decrypt(eski) → encrypt(yeni)
```
> Şu an basit implementasyon — multi-key rotation için gelişmiş versiyon eklenecek.

## 6. Monitoring Çıktıları (admin)

- `/admin/sistem-sagligi`
  - Kırmızı kart = "down": Mongo erişilemez VEYA 5dk'da 10+ kritik hata
  - Sarı = "degraded": Mongo > 500ms VEYA 5dk'da 3+ kritik
  - Yeşil = "healthy"
  - "Test Alarmı Gönder" → kdrgry@gmail.com (SMTP / Resend / DB)

- `/admin/secrets-vault` — yalnızca süper admin (admin@facette.com) raw değer görür
- `/admin/guvenlik-paneli` — login audit, IP blocklist, hesap kilidi

## 7. P2/P3 Backlog

- [ ] Email alarmlara HTML template + grafana benzeri dashboard linki
- [ ] Sentry/Glitchtip self-hosted ekleme (opsiyonel)
- [ ] Per-tenant secrets vault (multi-marka)
- [ ] Multi-region replica set + read-only replica routing
- [ ] WebSocket admin notification (anlık alarm pop-up)
