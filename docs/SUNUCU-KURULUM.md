# Sunucu Kurulumu — adım adım

Hedef: `83.150.214.157` sunucusunda meluni'yi yayına almak.
Sıra önemlidir; her adım bir öncekine dayanır.

Tüm komutlar **sunucunun içinde** çalışır. Önce bağlanın:

```bash
ssh kadir@83.150.214.157
```

Komut satırı `kadir@...` olarak değiştiyse içeridesiniz.

---

## 0. Parolayı değiştirin

Mevcut parola bir sohbet penceresine düz metin yazıldı; sızmış kabul edin.

```bash
passwd
```

---

## 1. Sistem paketleri

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y python3 python3-venv python3-pip nginx curl git ufw
```

Python sürümünü doğrulayın — 3.10 veya üstü olmalı:

```bash
python3 --version
```

---

## 2. Güvenlik duvarı

```bash
sudo ufw allow OpenSSH
sudo ufw allow 'Nginx Full'
sudo ufw enable
sudo ufw status
```

> Mevcut SSH oturumunuzu kapatmadan **yeni bir terminalden** bağlanıp
> kilitlenmediğinizi doğrulayın.

---

## 3. Deploy anahtarı

GitHub Actions sunucuya parolayla değil anahtarla bağlanır.
**Kendi bilgisayarınızda** (sunucuda değil) üretin:

```bash
ssh-keygen -t ed25519 -C "meluni-deploy" -f ~/.ssh/meluni_deploy -N ""
ssh-copy-id -i ~/.ssh/meluni_deploy.pub kadir@83.150.214.157
```

Doğrulayın — parola sorulmadan girebilmelisiniz:

```bash
ssh -i ~/.ssh/meluni_deploy kadir@83.150.214.157 "echo baglanti-ok"
```

`known_hosts` değerini de alın (5. adımda GitHub secret'ı olacak):

```bash
ssh-keyscan -H 83.150.214.157
```

---

## 4. Dizin yapısı ve ortam değişkenleri

Sunucuda:

```bash
mkdir -p ~/apps/meluni/{releases,shared,tmp}
nano ~/apps/meluni/shared/.env
```

İçine yapıştırın (kendi değerlerinizle):

```bash
# ── Veritabanı ────────────────────────────────────────────
MONGO_URL=mongodb+srv://kdrgry_db_user:PAROLA@cluster0.xxxxx.mongodb.net/meluni?retryWrites=true&w=majority
DB_NAME=meluni

# ── Kimlik/şifreleme — meluni'ye ÖZEL üretin ──────────────
SECRETS_MASTER_KEY=<fernet-anahtari>
JWT_SECRET=<rastgele-48-karakter>

# ── Adresler ──────────────────────────────────────────────
SITE_URL=https://meluni.com.tr
CORS_ORIGINS=https://meluni.com.tr,https://www.meluni.com.tr

# ── Marka ─────────────────────────────────────────────────
BRAND_STORE_NAME=meluni
BRAND_CONTACT_EMAIL=info@meluni.com.tr
PRODUCT_CODE_PREFIX=MEL
BARCODE_PREFIX=200

# ── Servis ────────────────────────────────────────────────
APP_SERVICE=meluni
HEALTH_URL=http://127.0.0.1:8000/api/health
LOGIN_SMS_MFA=off
AMAZON_ALLOW_RESTRICTED=0
AMAZON_ALLOW_WRITE=0
```

```bash
chmod 600 ~/apps/meluni/shared/.env
```

Anahtarları **kendi bilgisayarınızda** üretin:

```bash
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
python3 -c "import secrets; print(secrets.token_urlsafe(48))"
```

> Facette'in anahtarlarını kullanmayın. Aynı `SECRETS_MASTER_KEY` paylaşılırsa
> bir markanın kasasını çözen diğerininkini de çözer.

---

## 5. GitHub secret'ları

Repo → Settings → Secrets and variables → **Actions**:

| Secret | Değer |
|---|---|
| `SSH_HOST` | `83.150.214.157` |
| `SSH_USER` | `kadir` |
| `SSH_PRIVATE_KEY` | `cat ~/.ssh/meluni_deploy` çıktısının tamamı |
| `SSH_KNOWN_HOSTS` | 3. adımdaki `ssh-keyscan` çıktısı |
| `REACT_APP_BACKEND_URL` | `https://api.meluni.com.tr` |
| `REACT_APP_SITE_NAME` | `meluni` |
| `REACT_APP_SITE_URL` | `https://meluni.com.tr` |

> `REACT_APP_BACKEND_URL` boşsa iş baştan durur. CRA bu değeri **derleme
> anında** gömer; sunucuda sonradan düzeltilemez.

---

## 6. systemd servisi

Deploy henüz çalışmadığı için dosyayı elle oluşturuyoruz:

```bash
sudo nano /etc/systemd/system/meluni.service
```

Repodaki [`deploy/meluni.service`](../deploy/meluni.service) içeriğini
yapıştırın, `<KULLANICI>` yerine `kadir` yazın. Sonra:

```bash
sudo systemctl daemon-reload
sudo systemctl enable meluni
```

Henüz **başlatmayın** — kod sunucuda değil.

### Parolasız sudo

Deploy servisi kendisi yeniden başlatır; bunun için yetki gerekir:

```bash
sudo visudo -f /etc/sudoers.d/meluni-deploy
```

Tek satır:

```
kadir ALL=(root) NOPASSWD: /bin/systemctl restart meluni, /bin/systemctl reload nginx, /usr/sbin/nginx -t, /bin/journalctl -u meluni *
```

```bash
sudo chmod 440 /etc/sudoers.d/meluni-deploy
sudo visudo -c
```

---

## 7. Origin sertifikası

Cloudflare → **SSL/TLS → Origin Server → Create Certificate**.
Hostname: `meluni.com.tr, *.meluni.com.tr`. İki metin çıkar.

```bash
sudo mkdir -p /etc/ssl/cloudflare
sudo nano /etc/ssl/cloudflare/meluni.pem     # Origin Certificate
sudo nano /etc/ssl/cloudflare/meluni.key     # Private Key
sudo chmod 600 /etc/ssl/cloudflare/meluni.key
```

> Bu sertifika **yalnız Cloudflare üzerinden** gelen isteklerde geçerlidir.
> Tarayıcıdan doğrudan IP ile girerseniz geçersiz görünür — normaldir.

---

## 8. nginx

```bash
sudo nano /etc/nginx/sites-available/meluni
```

Repodaki [`deploy/nginx.meluni.conf`](../deploy/nginx.meluni.conf) içeriğini
yapıştırın, `<KULLANICI>` yerine `kadir` yazın.

```bash
sudo ln -sf /etc/nginx/sites-available/meluni /etc/nginx/sites-enabled/meluni
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t && sudo systemctl reload nginx
```

`nginx -t` hata verirse **devam etmeyin** — hata metnini okuyun.

---

## 9. İlk deploy

GitHub → **Actions** → **Deploy** → **Run workflow**.

İş şunları yapar: frontend'i derler → paketi sunucuya atar →
`releases/<tarih>-<sha>` altına açar → `current` symlink'ini çevirir →
bağımlılıkları kurar → servisi başlatır → `/api/health`'i 20 kez dener.

Sağlık kontrolü geçmezse **otomatik olarak önceki release'e döner** ve
`journalctl` çıktısını Actions loguna basar.

İlk deployda `current` yoktur, geri dönülecek yer olmadığı için hata
doğrudan loga düşer.

---

## 10. Cloudflare DNS

| Tip | Name | Content | Proxy |
|---|---|---|---|
| A | `@` | `83.150.214.157` | 🟠 Proxied |
| A | `www` | `83.150.214.157` | 🟠 Proxied |
| A | `api` | `83.150.214.157` | 🟠 Proxied |

**SSL/TLS → Overview → `Full (strict)`**
**SSL/TLS → Edge Certificates → `Always Use HTTPS`** açık.

> `Flexible` bırakmayın: nginx zaten HTTP'yi HTTPS'e yönlendiriyor,
> Cloudflare de HTTP ile gelirse sonsuz döngü olur
> (`ERR_TOO_MANY_REDIRECTS`).

---

## 11. Doğrulama

Kendi bilgisayarınızdan:

```bash
curl -sS -o /dev/null -w "%{http_code}\n" https://meluni.com.tr
curl -sS https://api.meluni.com.tr/api/health
curl -sSI http://meluni.com.tr | grep -i location        # 301 → https
curl -sSI https://www.meluni.com.tr | grep -i location   # 301 → köksüz
```

Sunucuda:

```bash
sudo systemctl status meluni
sudo journalctl -u meluni -n 50 --no-pager
```

---

## 12. Panel ve ilk veri

`https://meluni.com.tr/admin`

İlk admin parolası sunucuda şu dosyadadır (log'a parola yazılmaz):

```bash
cat ~/apps/meluni/current/backend/data/.admin_initial_password
```

Sonra sırasıyla:

1. **İşletme Ayarları → Şirket Bilgileri** — eksiksiz doldurun
2. **Tedarikçi XML** — `price_markup` girmeyi unutmayın
   (besleme bayi fiyatı verir, KDV hariç → katsayısız satarsanız zarar edersiniz)
3. `/api/supplier-xml/preview` ile eşlemeyi doğrulayın, sonra `import`

---

## Sorun giderme

| Belirti | Sebep |
|---|---|
| `502 Bad Gateway` | Backend ayakta değil → `journalctl -u meluni -n 50` |
| `ERR_TOO_MANY_REDIRECTS` | Cloudflare SSL modu `Flexible` → `Full (strict)` yapın |
| `526 Invalid SSL certificate` | Origin sertifikası yok/yanlış yolda (7. adım) |
| `ServerSelectionTimeoutError` | Atlas IP listesinde `83.150.214.157/32` yok |
| Site eski hâlinde | `frontend/build` pakete girmemiş → Actions'ta "Frontend derle" adımına bakın |
| Sayfa yenileyince 404 | nginx `try_files ... /index.html` satırı eksik |
| Deploy "systemctl restart başarısız" | Parolasız sudo tanımlı değil (6. adım) |
