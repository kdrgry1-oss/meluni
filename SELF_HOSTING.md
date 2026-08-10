# FACETTE — Kendi Sunucusuna Taşıma Rehberi (Self-Hosting)

Bu rehber, projeyi Emergent dışında, **en hızlı + en güvenilir** şekilde kendi
altyapınıza taşımanız için adım adım yönergedir. Görseller zaten **Cloudflare R2 +
`cdn.facette.com.tr`** üzerinden geldiği için o kısım hazırdır, dokunmanıza gerek yok.

---

## 0) Mimari
- **Frontend:** React (build → statik dosyalar)
- **Backend:** FastAPI (Python, `uvicorn server:app`)
- **Veritabanı:** MongoDB
- **Görsel/CDN:** Cloudflare R2 + Image Transformations (HAZIR)

## 1) Önerilen Stack (en kolay + güvenilir)
| Katman | Önerilen | Alternatif |
|---|---|---|
| Veritabanı | **MongoDB Atlas** (Frankfurt/EU, otomatik yedek) | VPS'te kendi MongoDB |
| Backend | **Railway** veya **Render** (GitHub'dan otomatik deploy) | Fly.io / Hetzner VPS |
| Frontend | **Cloudflare Pages** (zaten Cloudflare'desiniz) | Vercel / Netlify |

**Tek sunucu tercih ederseniz:** Hetzner Cloud (EU) / DigitalOcean → Docker Compose ile hepsi tek yerde (Bölüm 7).

---

## 2) Kodu GitHub'a gönder
1. Bu sohbetteki **"Save to Github"** özelliğini kullanın → repo'nuz oluşur.
   - ⚠️ `db_backup/` klasörü kullanıcı verisi (PII) içerir → **private (gizli) repo** kullanın.
2. Repo yapısı: `/backend` (FastAPI), `/frontend` (React), `/db_backup` (DB yedeği).

---

## 3) Veritabanı — MongoDB Atlas
1. https://cloud.mongodb.com → ücretsiz hesap → **Create Cluster** (M0 ücretsiz veya M10).
   - **Region: Frankfurt (eu-central-1)** seçin (TR'ye en düşük gecikme).
2. **Database Access** → bir kullanıcı oluşturun (kullanıcı adı + şifre).
3. **Network Access** → backend sunucunuzun IP'sini (veya geçici olarak `0.0.0.0/0`) ekleyin.
4. **Connect → Drivers** → bağlantı dizesini (URI) kopyalayın:
   `mongodb+srv://<user>:<pass>@cluster0.xxxx.mongodb.net/?retryWrites=true&w=majority`
5. **Yedeği geri yükleyin** (yerel makinenizde MongoDB Database Tools kurulu olmalı):
   ```bash
   mongorestore --uri="mongodb+srv://<user>:<pass>@cluster0.xxxx.mongodb.net" --drop \
     --gzip --archive=db_backup/facette_FINAL_20260606_141900.archive.gz \
     --nsFrom='test_database.*' --nsTo='facette.*'
   ```
   (Hedef DB adı örnek: `facette`. İstediğinizi seçebilirsiniz.)

## 4) Backend — Railway (veya Render)
1. https://railway.app → **New Project → Deploy from GitHub repo** → repo'nuzu seçin.
2. **Root Directory:** `backend`
3. **Start Command:** `uvicorn server:app --host 0.0.0.0 --port $PORT`
4. **Variables (Environment):** Aşağıdaki **Env Değişkenleri Tablosu**'ndaki tüm anahtarları
   ekleyin. Değerleri mevcut `backend/.env` dosyanızdan kopyalayın. Özellikle:
   - `MONGO_URL` = Atlas URI'niz
   - `DB_NAME` = `facette` (Atlas'ta restore ettiğiniz ad)
   - `CORS_ORIGINS` = `https://facette.com.tr` (frontend adresiniz)
   - `PUBLIC_BASE_URL` = backend public adresiniz (örn. `https://api.facette.com.tr`)
   - `R2_*` anahtarları (aynen mevcut .env'den)
5. Deploy bitince Railway size bir URL verir (örn. `https://facette-backend.up.railway.app`).
   İsterseniz Cloudflare'de `api.facette.com.tr` CNAME ile bu adrese yönlendirin.

> Render için: New → Web Service → repo → Root `backend` → Build `pip install -r requirements.txt`
> → Start `uvicorn server:app --host 0.0.0.0 --port $PORT` → aynı env değişkenleri.

## 5) Frontend — Cloudflare Pages
1. Cloudflare → **Workers & Pages → Create → Pages → Connect to Git** → repo'nuzu seçin.
2. **Build settings:**
   - Framework preset: **Create React App**
   - **Root directory:** `frontend`
   - **Build command:** `yarn build`
   - **Build output directory:** `build`
3. **Environment variables:**
   - `REACT_APP_BACKEND_URL` = backend public adresiniz (örn. `https://api.facette.com.tr`)
4. Deploy → Pages size bir adres verir. **Custom domain** olarak `facette.com.tr`'yi bağlayın
   (Cloudflare içinde olduğu için tek tıkla).

## 6) Cloudflare DNS bağlama
- `facette.com.tr` (ve `www`) → Cloudflare Pages projesine (frontend).
- `api.facette.com.tr` → backend (Railway/Render) adresine **CNAME** (proxied).
- `cdn.facette.com.tr` → R2 bucket (ZATEN bağlı, dokunmayın).

---

## 7) Alternatif: Tek VPS (Hetzner/DigitalOcean) + Docker
Tam kontrol isteyenler için. Sunucuda Docker + Docker Compose kurun, şu servisleri çalıştırın:
- `mongo` (resmi imaj, volume ile kalıcı) — veya Atlas kullanın
- `backend` (Python imajı, `uvicorn server:app --host 0.0.0.0 --port 8001`)
- `frontend` (Nginx ile `build/` statik servis)
- Önünde **Caddy** veya **Nginx** (otomatik HTTPS) + Cloudflare proxy.
İstersek hazır bir `docker-compose.yml` + `Dockerfile`'ları sizin için oluşturabilirim.

---

## 8) Env Değişkenleri Tablosu (backend)
Değerleri mevcut `backend/.env`'den kopyalayın. **GİZLİ olanları repo'ya commit etmeyin** —
hosting panelinin "Environment Variables" bölümüne girin.

| Anahtar | Açıklama |
|---|---|
| `MONGO_URL` | MongoDB Atlas/VPS bağlantı dizesi |
| `DB_NAME` | Veritabanı adı (örn. `facette`) |
| `CORS_ORIGINS` | Frontend origin'i (örn. `https://facette.com.tr`) |
| `PUBLIC_BASE_URL` | Backend public adresi |
| `JWT_SECRET` | Oturum token gizli anahtarı (mevcut değeri koruyun) |
| `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_BUCKET`, `R2_ENDPOINT`, `R2_PUBLIC_URL` | Cloudflare R2 (R2_PUBLIC_URL=`https://cdn.facette.com.tr`) |
| `IYZICO_MODE`, `IYZICO_API_KEY`, `IYZICO_SECRET_KEY`, `IYZICO_BASE_URL` | Iyzico ödeme |
| `TRENDYOL_*`, `TICIMAX_*` | Pazaryeri/entegrasyon (kullanıyorsanız) |
| `GIB_*` | e-Fatura/e-Arşiv (kullanıyorsanız) |
| `RESEND_API_KEY`, `RESEND_FROM` | E-posta gönderimi |
| `SECRETS_MASTER_KEY` | Vault şifreleme anahtarı (mevcut değeri koruyun) |
| `ALERT_SMTP_*`, `ALERT_TO_EMAIL` | Uyarı e-postaları (opsiyonel) |
| `EMERGENT_LLM_KEY` | AI özellikleri (Emergent dışında çalışmaz; kendi OpenAI/Gemini anahtarınızla değiştirin) |
| `REDIS_URL`, `CACHE_DEFAULT_TTL` | Cache (opsiyonel; boşsa in-memory) |

**Frontend env:** `REACT_APP_BACKEND_URL` = backend public adresi.

> Not: `EMERGENT_LLM_KEY` yalnızca Emergent içinde çalışır. Kendi sunucunuzda AI özellikleri
> (varsa) için kendi OpenAI/Anthropic/Gemini API anahtarınızı kullanacak şekilde
> uyarlama gerekir — isterseniz yardımcı olurum.

## 9) Veritabanı durumu (bu yedekte)
- **359 ürün** (Excel'e göre senkron, stok 0 olanlar pasif).
- Tüm görseller **`cdn.facette.com.tr`** (AVIF + dinamik resize).
- **Temiz slug'lar:** 227 ürün numarasız (`gri-dugmeli-blazer-ceket-gri`), 132 aynı isimli
  ürün ayırt edici numara taşır. Eski slug'lar `slug_aliases`'ta → eski linkler yönlenir.
- Siparişler (2.732), kullanıcılar (10.486) ve tüm ayarlar dahil.
