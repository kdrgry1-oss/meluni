#!/usr/bin/env bash
#
# meluni - sunucuda, yeni release aktif edildikten HEMEN SONRA calisir.
#
# Calisma dizini: <DEPLOY_PATH>/current
# Kullanilabilir degiskenler: DEPLOY_PATH, RELEASE_ID
#
# Bu script sifirdan (non-zero) donerse deploy.sh otomatik olarak
# bir onceki release'e geri doner. Yani buraya yazdiginiz her adim
# aslinda bir saglik kontrolu gorevi de goruyor.
#
# Asagidaki bloklar uygulama yigini belli olunca doldurulacak.
# Simdilik hicbir sey yapmadan basariyla cikiyor.
#
set -euo pipefail

log() { printf '\033[1;32m[post-deploy]\033[0m %s\n' "$*"; }

log "release: ${RELEASE_ID:-bilinmiyor}"
log "dizin:   $(pwd)"

# --- Bagimliliklar -----------------------------------------------------
# Node.js:
#   npm ci --omit=dev
# PHP / Composer:
#   composer install --no-dev --optimize-autoloader --no-interaction
# Python:
#   python3 -m venv "${DEPLOY_PATH}/shared/venv"
#   "${DEPLOY_PATH}/shared/venv/bin/pip" install -r requirements.txt

# --- Build -------------------------------------------------------------
#   npm run build

# --- Veritabani migration ----------------------------------------------
#   php artisan migrate --force
#   npx prisma migrate deploy

# --- Servisi yeniden baslat --------------------------------------------
# systemd (deploy kullanicisina sudoers'ta NOPASSWD yetkisi verilmeli):
#   sudo systemctl restart meluni
# PM2:
#   pm2 reload meluni --update-env
# Docker Compose:
#   docker compose up -d --build

# --- Saglik kontrolu ---------------------------------------------------
#   for i in $(seq 1 10); do
#     curl -fsS http://127.0.0.1:3000/health >/dev/null && break
#     [ "$i" = 10 ] && { echo "saglik kontrolu basarisiz" >&2; exit 1; }
#     sleep 2
#   done

log "tamamlandi"
