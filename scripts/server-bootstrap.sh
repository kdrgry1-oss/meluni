#!/usr/bin/env bash
#
# meluni — sunucu kurulumunu tek komutta yapar.
#
# Kullanım (sunucuda, deploy kullanıcısı olarak — root DEĞİL):
#   scp scripts/server-bootstrap.sh kadir@83.150.214.157:~/
#   ssh kadir@83.150.214.157 "bash ~/server-bootstrap.sh"
#
# Repo private olduğu için curl ile indirilemez; scp ile taşıyın.
# Script tek başına çalışır, repodan hiçbir şey çekmez.
#
# Ne yapar:
#   1. Sistem paketleri (python3, nginx, certbot, ufw)
#   2. Güvenlik duvarı
#   3. Dizin yapısı + shared/.env (anahtarları kendisi üretir)
#   4. Parolasız sudo (yalnız gerekli 3 komut için)
#   5. systemd servisi
#   6. Geçici nginx → Let's Encrypt sertifikası → asıl nginx
#
# Baştan sona tekrar çalıştırılabilir: var olan .env'i EZMEZ, sertifikayı
# yeniden almaz. Yarıda kalırsa aynı komutla devam edebilirsiniz.
#
set -euo pipefail

DOMAIN="meluni.com.tr"
APP_USER="$(id -un)"
APP_DIR="${HOME}/apps/meluni"
SERVICE="meluni"

bold()  { printf '\n\033[1;34m▸ %s\033[0m\n' "$*"; }
ok()    { printf '\033[1;32m  ✓ %s\033[0m\n' "$*"; }
warn()  { printf '\033[1;33m  ! %s\033[0m\n' "$*"; }
die()   { printf '\033[1;31m  ✗ HATA: %s\033[0m\n' "$*" >&2; exit 1; }

[ "$(id -u)" -ne 0 ] || die "Bu script root olarak DEĞİL, deploy kullanıcısı olarak çalışmalı."

# ── 1. Paketler ─────────────────────────────────────────────────────────
bold "1/6  Sistem paketleri"
sudo apt-get update -qq
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
  python3 python3-venv python3-pip nginx curl git ufw certbot python3-certbot-nginx >/dev/null
PYV="$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
ok "python ${PYV}, nginx, certbot kuruldu"
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)' \
  || die "Python ${PYV} çok eski, 3.10+ gerekiyor."

# ── 2. Güvenlik duvarı ──────────────────────────────────────────────────
bold "2/6  Güvenlik duvarı"
sudo ufw allow OpenSSH >/dev/null
sudo ufw allow 'Nginx Full' >/dev/null
sudo ufw --force enable >/dev/null
ok "SSH ve HTTP/HTTPS açık, gerisi kapalı"

# ── 3. Dizinler ve .env ─────────────────────────────────────────────────
bold "3/6  Dizin yapısı ve ortam değişkenleri"
mkdir -p "${APP_DIR}"/{releases,shared,tmp}
ENV_FILE="${APP_DIR}/shared/.env"

if [ -f "${ENV_FILE}" ]; then
  ok ".env zaten var, dokunulmadı"
else
  echo
  echo "  MongoDB bağlantı dizinizi yapıştırın."
  echo "  Biçim: mongodb+srv://kullanici:PAROLA@cluster0.xxxxx.mongodb.net/meluni?retryWrites=true&w=majority"
  printf "  MONGO_URL: "
  read -r MONGO_URL
  [ -n "${MONGO_URL}" ] || die "MONGO_URL boş bırakılamaz."
  case "${MONGO_URL}" in
    mongodb*://*) ;;
    *) die "Bu bir MongoDB bağlantı dizisi değil." ;;
  esac
  case "${MONGO_URL}" in
    *"<db_password>"*|*"<password>"*|*PAROLA*)
      die "Bağlantı dizisinde parola yer tutucusu duruyor — gerçek parolayla değiştirin." ;;
  esac

  # Anahtarları biz üretiyoruz: elle üretilen "rastgele" değerler zayıf oluyor
  # ve her markanın AYRI anahtarı olmak zorunda.
  SECRETS_KEY="$(python3 -c 'import base64,os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())')"
  JWT_KEY="$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')"

  umask 077
  cat > "${ENV_FILE}" <<ENVEOF
# meluni — üretim ortam değişkenleri. Bu dosya repoya GİRMEZ.
MONGO_URL=${MONGO_URL}
DB_NAME=meluni

SECRETS_MASTER_KEY=${SECRETS_KEY}
JWT_SECRET=${JWT_KEY}

SITE_URL=https://${DOMAIN}
CORS_ORIGINS=https://${DOMAIN},https://www.${DOMAIN}

BRAND_STORE_NAME=meluni
BRAND_CONTACT_EMAIL=info@${DOMAIN}
PRODUCT_CODE_PREFIX=MEL
BARCODE_PREFIX=200

APP_SERVICE=${SERVICE}
HEALTH_URL=http://127.0.0.1:8000/api/health
LOGIN_SMS_MFA=off
AMAZON_ALLOW_RESTRICTED=0
AMAZON_ALLOW_WRITE=0
ENVEOF
  chmod 600 "${ENV_FILE}"
  ok ".env oluşturuldu, anahtarlar üretildi"
  warn "SECRETS_MASTER_KEY'i yedekleyin: kaybederseniz kasadaki tüm"
  warn "entegrasyon şifreleri (iyzico, kargo, pazaryeri) ÇÖZÜLEMEZ."
fi

# ── 4. Parolasız sudo ───────────────────────────────────────────────────
bold "4/6  Deploy yetkileri"
echo "${APP_USER} ALL=(root) NOPASSWD: /bin/systemctl restart ${SERVICE}, /bin/systemctl reload nginx, /usr/sbin/nginx -t" \
  | sudo tee /etc/sudoers.d/meluni-deploy >/dev/null
sudo chmod 440 /etc/sudoers.d/meluni-deploy
sudo visudo -c >/dev/null || die "sudoers dosyası geçersiz."
ok "deploy servisi yeniden başlatabilir (yalnız bu 3 komut)"

# ── 5. systemd ──────────────────────────────────────────────────────────
bold "5/6  systemd servisi"
sudo tee /etc/systemd/system/${SERVICE}.service >/dev/null <<UNITEOF
[Unit]
Description=meluni API (FastAPI/uvicorn)
After=network-online.target
Wants=network-online.target

[Service]
Type=exec
User=${APP_USER}
Group=${APP_USER}
WorkingDirectory=${APP_DIR}/current/backend
EnvironmentFile=${APP_DIR}/shared/.env
ExecStart=${APP_DIR}/shared/venv/bin/uvicorn server:app --host 127.0.0.1 --port 8000 --workers 2 --proxy-headers --forwarded-allow-ips=*
Restart=always
RestartSec=5
KillSignal=SIGINT
TimeoutStopSec=30
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
UNITEOF
sudo systemctl daemon-reload
sudo systemctl enable ${SERVICE} >/dev/null 2>&1
ok "servis tanımlandı (kod gelene kadar başlatılmıyor)"

# ── 6. nginx + sertifika ────────────────────────────────────────────────
bold "6/6  nginx ve TLS sertifikası"

if [ -f "/etc/letsencrypt/live/${DOMAIN}/fullchain.pem" ]; then
  ok "sertifika zaten var, yeniden alınmadı"
else
  # Sertifika almadan önce 80'den erişilebildiğimizi doğrula.
  sudo mkdir -p /var/www/html
  sudo tee /etc/nginx/sites-available/meluni >/dev/null <<TMPEOF
server {
    listen 80;
    listen [::]:80;
    server_name ${DOMAIN} www.${DOMAIN} api.${DOMAIN};
    root /var/www/html;
    location /.well-known/acme-challenge/ { default_type "text/plain"; }
    location / { return 200 "kurulum suruyor\n"; default_type "text/plain"; }
}
TMPEOF
  sudo ln -sf /etc/nginx/sites-available/meluni /etc/nginx/sites-enabled/meluni
  sudo rm -f /etc/nginx/sites-enabled/default
  sudo nginx -t >/dev/null 2>&1 || die "geçici nginx yapılandırması geçersiz."
  sudo systemctl reload nginx
  ok "geçici HTTP yapılandırması aktif"

  # Her alan adı gerçekten BU sunucuya mı geliyor? Gelmiyorsa certbot
  # başarısız olur ve Let's Encrypt hız sınırına takılırız.
  MY_IP="$(curl -fsS --max-time 10 https://api.ipify.org || echo '')"
  CERT_DOMAINS=""
  for d in "${DOMAIN}" "www.${DOMAIN}" "api.${DOMAIN}"; do
    if curl -fsS --max-time 10 "http://${d}/" 2>/dev/null | grep -q "kurulum suruyor"; then
      CERT_DOMAINS="${CERT_DOMAINS} -d ${d}"
      ok "${d} → bu sunucuya ulaşıyor"
    else
      warn "${d} bu sunucuya ULAŞMIYOR — sertifikaya dahil edilmedi"
      warn "  (Cloudflare proxy'si turuncuysa griye alın, ya da DNS'i kontrol edin)"
    fi
  done
  [ -n "${CERT_DOMAINS}" ] || die "Hiçbir alan adı bu sunucuya ulaşmıyor. DNS/proxy ayarını düzeltip tekrar çalıştırın.
     Sunucunun dış IP'si: ${MY_IP:-bilinmiyor}"

  bold "Let's Encrypt sertifikası alınıyor"
  # shellcheck disable=SC2086
  sudo certbot certonly --webroot -w /var/www/html ${CERT_DOMAINS} \
    --agree-tos -m "info@${DOMAIN}" --no-eff-email --non-interactive \
    || die "certbot başarısız. Yukarıdaki çıktıyı okuyun."
  ok "sertifika alındı"
fi

# Asıl yapılandırma. Repo private olduğu için raw.githubusercontent'ten
# indirilemez (404); yapılandırma bu scriptin içine gömülü.
CERT_DIR="/etc/letsencrypt/live/${DOMAIN}"
sudo tee /etc/nginx/sites-available/meluni >/dev/null <<NGINXEOF
# Cloudflare proxy'si arkasında \$remote_addr Cloudflare'in IP'sidir.
# Bu blok olmadan loglar ve IP bazlı kurallar anlamsız olur.
set_real_ip_from 173.245.48.0/20;
set_real_ip_from 103.21.244.0/22;
set_real_ip_from 103.22.200.0/22;
set_real_ip_from 103.31.4.0/22;
set_real_ip_from 141.101.64.0/18;
set_real_ip_from 108.162.192.0/18;
set_real_ip_from 190.93.240.0/20;
set_real_ip_from 188.114.96.0/20;
set_real_ip_from 197.234.240.0/22;
set_real_ip_from 198.41.128.0/17;
set_real_ip_from 162.158.0.0/15;
set_real_ip_from 104.16.0.0/13;
set_real_ip_from 104.24.0.0/14;
set_real_ip_from 172.64.0.0/13;
set_real_ip_from 131.0.72.0/22;
set_real_ip_from 2400:cb00::/32;
set_real_ip_from 2606:4700::/32;
set_real_ip_from 2803:f800::/32;
real_ip_header CF-Connecting-IP;

server {
    listen 80;
    listen [::]:80;
    server_name ${DOMAIN} www.${DOMAIN} api.${DOMAIN};
    # ACME doğrulaması HTTPS'e yönlendirilmemeli, yoksa yenileme başarısız olur.
    location ^~ /.well-known/acme-challenge/ { root /var/www/html; default_type "text/plain"; }
    location / { return 301 https://\$host\$request_uri; }
}

server {
    listen 443 ssl http2;
    listen [::]:443 ssl http2;
    server_name www.${DOMAIN};
    ssl_certificate     ${CERT_DIR}/fullchain.pem;
    ssl_certificate_key ${CERT_DIR}/privkey.pem;
    return 301 https://${DOMAIN}\$request_uri;
}

server {
    listen 443 ssl http2;
    listen [::]:443 ssl http2;
    server_name ${DOMAIN};
    ssl_certificate     ${CERT_DIR}/fullchain.pem;
    ssl_certificate_key ${CERT_DIR}/privkey.pem;
    ssl_protocols       TLSv1.2 TLSv1.3;

    location ^~ /.well-known/acme-challenge/ { root /var/www/html; default_type "text/plain"; }

    root ${APP_DIR}/current/frontend/build;
    index index.html;
    # current symlink'i her deployda değişir; nginx eski dosyayı tutmasın.
    open_file_cache off;

    access_log /var/log/nginx/meluni.access.log;
    error_log  /var/log/nginx/meluni.error.log;
    client_max_body_size 25m;
    add_header X-Content-Type-Options nosniff always;
    add_header Referrer-Policy strict-origin-when-cross-origin always;

    location ~ /\\. { deny all; }

    # index.html ASLA cache'lenmez: yeni derlemede dosya adları değişir,
    # cache'lenmiş eski index.html silinmiş JS'i ister ve site beyaz ekran olur.
    location = /index.html { add_header Cache-Control "no-store, must-revalidate"; }
    location /static/ { expires 1y; add_header Cache-Control "public, immutable"; access_log off; }

    # React Router: diskte olmayan yollar index.html'e düşer.
    location / { try_files \$uri \$uri/ /index.html; }
}

server {
    listen 443 ssl http2;
    listen [::]:443 ssl http2;
    server_name api.${DOMAIN};
    ssl_certificate     ${CERT_DIR}/fullchain.pem;
    ssl_certificate_key ${CERT_DIR}/privkey.pem;
    ssl_protocols       TLSv1.2 TLSv1.3;

    access_log /var/log/nginx/meluni-api.access.log;
    error_log  /var/log/nginx/meluni-api.error.log;
    client_max_body_size 50m;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Host              \$host;
        proxy_set_header X-Real-IP         \$remote_addr;
        proxy_set_header X-Forwarded-For   \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_set_header Upgrade           \$http_upgrade;
        proxy_set_header Connection        "upgrade";
        # 6475 ürünlük XML içe aktarma 60 sn'yi aşar.
        proxy_read_timeout    600s;
        proxy_connect_timeout 30s;
        proxy_buffering       off;
    }
}
NGINXEOF

if sudo nginx -t >/dev/null 2>&1; then
  sudo systemctl reload nginx
  ok "asıl nginx yapılandırması aktif"
else
  warn "asıl yapılandırma geçersiz, geçici olan bırakıldı. Hata:"
  sudo nginx -t || true
fi

# ── Özet ────────────────────────────────────────────────────────────────
cat <<SUMMARY

  ═══════════════════════════════════════════════════════════════
   SUNUCU HAZIR
  ═══════════════════════════════════════════════════════════════

   Kullanıcı   : ${APP_USER}
   Uygulama    : ${APP_DIR}
   Servis      : ${SERVICE} (kod gelince başlar)
   Sertifika   : /etc/letsencrypt/live/${DOMAIN}/

   SIRADAKİ ADIM — kendi bilgisayarınızda deploy anahtarı üretin:

     ssh-keygen -t ed25519 -C meluni-deploy -f ~/.ssh/meluni_deploy -N ""
     ssh-copy-id -i ~/.ssh/meluni_deploy.pub ${APP_USER}@$(curl -fsS --max-time 5 https://api.ipify.org 2>/dev/null || echo '<sunucu-ip>')
     ssh-keyscan -H $(curl -fsS --max-time 5 https://api.ipify.org 2>/dev/null || echo '<sunucu-ip>')

   Sonra GitHub > Settings > Secrets and variables > Actions:

     SSH_HOST               $(curl -fsS --max-time 5 https://api.ipify.org 2>/dev/null || echo '<sunucu-ip>')
     SSH_USER               ${APP_USER}
     SSH_PRIVATE_KEY        cat ~/.ssh/meluni_deploy   (tamamı)
     SSH_KNOWN_HOSTS        ssh-keyscan çıktısı
     REACT_APP_BACKEND_URL  https://api.${DOMAIN}

   Ardından: Actions > Deploy > Run workflow

  ═══════════════════════════════════════════════════════════════

SUMMARY
