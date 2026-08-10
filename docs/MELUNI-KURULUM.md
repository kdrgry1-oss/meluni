# meluni — Kurulum ve Yayına Alma

Roof platformunun meluni markası için kurulumu. Sıra önemlidir.

---

## 1. Değişkenler — Railway (backend)

Railway > proje > **Variables**. Yıldızlı olanlar **her marka için yeniden üretilir**.

| Değişken | Değer | Not |
|---|---|---|
| `MONGO_URL` * | Atlas bağlantı dizesi | meluni'ye **ayrı** cluster/DB |
| `SECRETS_MASTER_KEY` * | `Fernet.generate_key()` çıktısı | Facette'inkini **kullanmayın** |
| `JWT_SECRET` * | 32+ rastgele karakter | Facette'inkini **kullanmayın** |
| `SITE_URL` | `https://meluni.com.tr` | e-posta/feed linkleri + ödeme allowlist |
| `CORS_ORIGINS` | `https://meluni.com.tr,https://www.meluni.com.tr` | virgülle |
| `BRAND_STORE_NAME` | `meluni` | SMS/e-posta imzası |
| `BRAND_COMPANY_NAME` | resmî unvan | fatura |
| `BRAND_CONTACT_EMAIL` | `info@meluni.com.tr` | |
| `BRAND_LOGO_URL` | logo adresi | e-posta başlığı |
| `PRODUCT_CODE_PREFIX` | `MEL` | ürün kod öneki |
| `BARCODE_PREFIX` | `200` | GS1 firma önekiniz varsa onu yazın |
| `AMAZON_ALLOW_RESTRICTED` / `AMAZON_ALLOW_WRITE` | `0` | ilk faz |
| `LOGIN_SMS_MFA` | `off` | NetGSM aktif olana kadar |

`BRAND_*` değişkenleri yalnız **varsayılandır**; panelden girilen Şirket
Bilgileri her zaman bunları ezer.

Anahtar üretimi:

```bash
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"  # SECRETS_MASTER_KEY
python3 -c "import secrets; print(secrets.token_urlsafe(48))"                                # JWT_SECRET
```

> **Uyarı:** Her marka ayrı `SECRETS_MASTER_KEY` kullanmalı. Aynı anahtar
> paylaşılırsa bir markanın vault'unu çözen diğerininkini de çözer.

---

## 2. Secret'lar — GitHub

Repo > Settings > Secrets and variables > **Actions**.

| Secret | Ne için |
|---|---|
| `RAILWAY_TOKEN` | backend deploy |
| `RAILWAY_SERVICE` | (opsiyonel) hedef servis adı |
| `CLOUDFLARE_API_TOKEN` | Pages deploy — izin: Account > Cloudflare Pages > Edit |
| `CLOUDFLARE_ACCOUNT_ID` | Cloudflare panelinin sağ sütunu |
| `REACT_APP_BACKEND_URL` | `https://api.meluni.com.tr` |
| `REACT_APP_SITE_NAME` / `_SITE_URL` / `_SITE_TITLE` / `_SITE_DESC` | title, meta, OG, JSON-LD |
| `CF_PAGES_PROJECT` | (opsiyonel) Pages proje adı, varsayılan `meluni` |

> `REACT_APP_*` değerleri derlenmiş JavaScript içinde **herkese açıktır**.
> Buraya API anahtarı, parola, token koymayın.

---

## 3. DNS ve SSL

Ayrıntı: [`DNS-CLOUDFLARE.md`](DNS-CLOUDFLARE.md)

| Tip | Name | Content | Proxy |
|---|---|---|---|
| `CNAME` | `@` | Pages projesinin `.pages.dev` adresi | Proxied 🟠 |
| `CNAME` | `www` | `meluni.com.tr` | Proxied 🟠 |
| `CNAME` | `api` | Railway'in verdiği `.up.railway.app` adresi | **DNS only** ⚪ |

**SSL:** Frontend'de Cloudflare Pages sertifikayı kendi üretir. Backend'de
Railway kendi sertifikasını verir — `api` kaydı bu yüzden **gri bulut**
olmalı; turuncuya alırsanız Railway'in sertifikası ile Cloudflare'inki
çakışır ve 526 alırsınız.

Cloudflare > SSL/TLS > **Full (strict)**. `Flexible` bırakırsanız
`ERR_TOO_MANY_REDIRECTS` alırsınız. Edge Certificates altında
`Always Use HTTPS` açık olmalı.

Alan adı Pages'e bağlanır: Pages projesi > Custom domains > `meluni.com.tr`.

---

## 4. Panel ayarları

1. **İşletme Ayarları > Şirket Bilgileri** — hepsini doldurun. Buraya
   girilen değerler e-posta, fatura, footer, GPSR ve e-faturaya akar.
2. **Entegrasyonlar** — iyzico, MNG, Doğan, pazaryerleri, CAPI/Pixel,
   WhatsApp. Hepsi **meluni'ye ait** hesaplar olmalı.
3. **CMS** — KVKK, gizlilik, iade, SSS metinlerini markaya göre düzenleyin.
4. **Bildirim şablonları** — metinlerde marka adını sabit yazmayın,
   `{store_name}` kullanın; doğru marka kendiliğinden gelir.

---

## 5. Girişte SMS doğrulaması

NetGSM aktif olmadığı için giriş SMS'i **otomatik atlanır**: sağlayıcı
yapılandırılmamışken kod üretilip kaydediliyor ama telefona hiç gitmiyordu
ve panele giriş kilitleniyordu. Artık gönderim mümkün değilse adım atlanır
ve giriş normal tamamlanır (audit log'a `login_mfa_skipped_no_sms` düşer).

Kesin kapatma: `LOGIN_SMS_MFA=off`.

NetGSM'i açtığınızda: Bildirim Sağlayıcıları'ndan Netgsm'i seçip kimlik
bilgilerini girin — MFA kendiliğinden yeniden devreye girer. Zorunlu admin
MFA'yı ayrıca istiyorsanız İşletme Kuralı `security.admin_mfa_required`.

---

## 6. Tedarikçi XML beslemesi

**Ayarlar > Tedarikçi XML** (veya API ile):

```bash
curl -X PUT https://api.meluni.com.tr/api/supplier-xml/config \
  -H "Authorization: Bearer <admin-token>" -H "Content-Type: application/json" \
  -d '{"url":"<besleme-adresi>","supplier_name":"modayakamoz","enabled":true}'
```

Adres kullanıcı adı ve şifre içerdiği için **şifrelenerek** saklanır ve
panelde maskeli gösterilir. Repoya yazılmaz.

**Önce önizleyin** — beslemeyi doğru okuduğumuzu kaydetmeden görün:

```bash
curl -X POST https://api.meluni.com.tr/api/supplier-xml/preview -H "Authorization: Bearer <token>"
```

Dönen `detected_mapping` alanlarını kontrol edin. Bir alan yanlış tanınmışsa
`PUT /config` ile `mapping` içinde düzeltin — kod değişikliği gerekmez.

### Otomatik çalışma

| İş | Sıklık | Saat (UTC) |
|---|---|---|
| Stok + fiyat eşitleme | günde 4 | 00:15, 06:15, 12:15, 18:15 |
| Yeni ürün aktarımı | günde 1 | 04:30 |

`enabled: false` iken cron beslemeyi indirmez.

### Ürün kodları bize ait

Tedarikçinin barkodu/stok kodu **yalnızca eşleme anahtarı** olarak saklanır.
Müşteriye ve pazaryerlerine giden kodları biz üretiriz:

| Kod | Biçim | Kapsam |
|---|---|---|
| Model kodu | `MEL-M000123` | ürün (varyantlar paylaşır) |
| Stok kodu | `MEL-M000123-01` | varyant |
| Barkod | `2000001234567` | varyant, geçerli EAN-13 |

Barkod öneki `200-299`, GS1'in **işletme içi kullanıma** ayırdığı aralıktır:
kayıt gerektirmez, gerçek GTIN'lerle çakışmaz. Kendi GS1 firma önekiniz
olduğunda `BARCODE_PREFIX` ile değiştirin.

Eşleme `supplier_code_map` koleksiyonunda kalıcıdır — aynı ürün ikinci kez
aktarıldığında **aynı** kodları alır, yeniden numaralandırma olmaz.

---

## 7. Yayına alma

`main`'e push → GitHub Actions → Railway (backend) + Cloudflare (frontend).
Yol filtreli: yalnız `backend/` değiştiyse backend, yalnız `frontend/`
değiştiyse frontend deploy olur.

> **Not:** Repo boş açıldığı için varsayılan branch şu an
> `claude/meluni-entegrasyon-rzv2le` ve akışlar bu branch'i de dinliyor.
> Settings > Branches'tan varsayılanı `main` yaptığınızda workflow
> dosyalarındaki bu satırı silebilirsiniz.

### Yayın sonrası kontrol

- [ ] `https://meluni.com.tr` açılıyor, sayfa kaynağında `%REACT_APP_` yok
- [ ] `https://api.meluni.com.tr/api/health` yanıt veriyor
- [ ] Panele giriş yapılabiliyor (SMS istemiyor)
- [ ] Sipariş e-postasında marka adı doğru, "Facette" geçmiyor
- [ ] XML önizleme doğru alanları tanıyor
- [ ] Aktarılan üründe barkod `200…` ile başlıyor, stok kodu `MEL-` önekli
