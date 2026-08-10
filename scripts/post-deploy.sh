#!/usr/bin/env bash
#
# meluni - sunucuda, yeni release aktif edildikten HEMEN SONRA calisir.
#
# Calisma dizini: <DEPLOY_PATH>/current
# Kullanilabilir degiskenler: DEPLOY_PATH, RELEASE_ID
#
# Bu script sifirdan farkli (non-zero) donerse deploy.sh otomatik olarak
# bir onceki release'e geri doner. Yani buradaki her adim ayni zamanda
# bir saglik kontroludur.
#
# Uygulama yigini repodaki dosyalardan otomatik anlasilir:
#   package.json      -> Node.js
#   composer.json     -> PHP
#   requirements.txt  -> Python
#   hicbiri yok       -> statik site, sadece dosya kopyalama
#
# Otomatik davranisi ezmek icin shared/.env icine yazabileceginiz degiskenler:
#   APP_STACK=node|php|python|static   (otomatik tespiti devre disi birakir)
#   APP_SERVICE=meluni                 (systemd servis adi)
#   PM2_APP=meluni                     (PM2 uygulama adi)
#   HEALTH_URL=http://127.0.0.1:3000/  (deploy sonrasi saglik kontrolu)
#
set -euo pipefail

log()  { printf '\033[1;32m[post-deploy]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[post-deploy]\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31m[post-deploy] HATA:\033[0m %s\n' "$*" >&2; exit 1; }

RELEASE_DIR="$(pwd)"
SHARED_DIR="${DEPLOY_PATH:-$(dirname "$(dirname "${RELEASE_DIR}")")}/shared"

log "release : ${RELEASE_ID:-bilinmiyor}"
log "dizin   : ${RELEASE_DIR}"

# --- Ortam degiskenlerini oku ------------------------------------------
# .env'i source etmiyoruz; icindeki degerler kabuk tarafindan
# yorumlanmasin diye satir satir okuyoruz.
read_env() {
  local key="$1" file="${SHARED_DIR}/.env"
  [ -f "${file}" ] || return 0
  sed -n "s/^[[:space:]]*${key}[[:space:]]*=[[:space:]]*//p" "${file}" \
    | tail -n 1 | sed -e 's/^"//' -e 's/"$//' -e "s/^'//" -e "s/'$//"
}

APP_STACK="${APP_STACK:-$(read_env APP_STACK)}"
APP_SERVICE="${APP_SERVICE:-$(read_env APP_SERVICE)}"
PM2_APP="${PM2_APP:-$(read_env PM2_APP)}"
HEALTH_URL="${HEALTH_URL:-$(read_env HEALTH_URL)}"

# --- Yigin tespiti ------------------------------------------------------
if [ -z "${APP_STACK}" ]; then
  if   [ -f package.json ];     then APP_STACK=node
  elif [ -f composer.json ];    then APP_STACK=php
  elif [ -f requirements.txt ]; then APP_STACK=python
  else                               APP_STACK=static
  fi
  log "yigin   : ${APP_STACK} (otomatik tespit)"
else
  log "yigin   : ${APP_STACK} (.env ile belirtilmis)"
fi

need() {
  command -v "$1" >/dev/null 2>&1 \
    || die "'$1' sunucuda kurulu degil. Kurun ya da shared/.env icinde APP_STACK=static yapin."
}

# --- Bagimliliklar ve build --------------------------------------------
case "${APP_STACK}" in
  node)
    need node
    need npm
    if [ -f package-lock.json ]; then
      log "npm ci --omit=dev"
      npm ci --omit=dev --no-audit --no-fund
    else
      warn "package-lock.json yok; 'npm install' kullaniliyor (surumler sabit degil)"
      npm install --omit=dev --no-audit --no-fund
    fi
    if node -e 'process.exit(require("./package.json").scripts?.build ? 0 : 1)' 2>/dev/null; then
      log "npm run build"
      npm run build
    fi
    ;;

  php)
    need composer
    log "composer install"
    composer install --no-dev --optimize-autoloader --no-interaction --prefer-dist
    if [ -f artisan ]; then
      log "artisan migrate"
      php artisan migrate --force
      php artisan config:cache
      php artisan route:cache
    fi
    ;;

  python)
    need python3
    VENV="${SHARED_DIR}/venv"
    if [ ! -d "${VENV}" ]; then
      log "sanal ortam olusturuluyor: ${VENV}"
      python3 -m venv "${VENV}"
    fi
    log "pip install -r requirements.txt"
    "${VENV}/bin/pip" install --quiet --upgrade pip
    "${VENV}/bin/pip" install --quiet -r requirements.txt
    ;;

  static)
    log "statik site; kurulacak bagimlilik yok"
    ;;

  *)
    die "bilinmeyen APP_STACK degeri: ${APP_STACK}"
    ;;
esac

# --- Servisi yeniden baslat --------------------------------------------
restarted=false

if [ -n "${APP_SERVICE}" ]; then
  if sudo -n systemctl restart "${APP_SERVICE}" 2>/dev/null; then
    log "systemd servisi yeniden baslatildi: ${APP_SERVICE}"
    restarted=true
  else
    die "systemctl restart ${APP_SERVICE} basarisiz. Parolasiz sudo yetkisi tanimli mi? Bkz. docs/SUNUCU-ENTEGRASYON.md Adim 5"
  fi
elif [ -n "${PM2_APP}" ]; then
  need pm2
  if pm2 describe "${PM2_APP}" >/dev/null 2>&1; then
    log "pm2 reload: ${PM2_APP}"
    pm2 reload "${PM2_APP}" --update-env
  else
    log "pm2 start: ${PM2_APP}"
    pm2 start npm --name "${PM2_APP}" -- start
  fi
  pm2 save >/dev/null 2>&1 || true
  restarted=true
fi

if [ "${restarted}" = false ] && [ "${APP_STACK}" != "static" ]; then
  warn "Servis yeniden baslatilmadi. shared/.env icine APP_SERVICE veya PM2_APP yazin,"
  warn "aksi halde sunucu eski kodu calistirmaya devam eder."
fi

# --- Saglik kontrolu ----------------------------------------------------
if [ -n "${HEALTH_URL}" ]; then
  need curl
  log "saglik kontrolu: ${HEALTH_URL}"
  for attempt in $(seq 1 15); do
    if curl -fsS --max-time 5 "${HEALTH_URL}" >/dev/null 2>&1; then
      log "saglik kontrolu gecti (${attempt}. deneme)"
      break
    fi
    [ "${attempt}" -eq 15 ] && die "saglik kontrolu 15 denemede gecmedi; release geri alinacak"
    sleep 2
  done
fi

log "tamamlandi"
