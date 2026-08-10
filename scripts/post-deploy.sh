#!/usr/bin/env bash
#
# meluni — sunucuda, yeni release aktif edildikten HEMEN SONRA çalışır.
#
# Çalışma dizini: <DEPLOY_PATH>/current
# Değişkenler:    DEPLOY_PATH, RELEASE_ID
#
# Sıfırdan farklı dönerse deploy.sh otomatik olarak bir önceki release'e
# döner. Yani buradaki her adım aynı zamanda bir sağlık kontrolüdür.
#
# Bu proje bir MONOREPO:
#   backend/   FastAPI + uvicorn  → systemd servisi
#   frontend/  React (craco)      → derlenmiş dosyalar, nginx servis eder
#
# Frontend CI'da derlenir ve hazır `frontend/build` olarak gelir. Sunucuda
# yeniden derlemiyoruz: 6475 ürünlük bir katalogla CRA derlemesi 1 GB'ın
# üzerinde RAM ister ve küçük VPS'lerde OOM ile ölür.
#
set -euo pipefail

log()  { printf '\033[1;32m[post-deploy]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[post-deploy]\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31m[post-deploy] HATA:\033[0m %s\n' "$*" >&2; exit 1; }

RELEASE_DIR="$(pwd)"
DEPLOY_PATH="${DEPLOY_PATH:-$(dirname "$(dirname "${RELEASE_DIR}")")}"
SHARED_DIR="${DEPLOY_PATH}/shared"
VENV="${SHARED_DIR}/venv"

log "release : ${RELEASE_ID:-bilinmiyor}"
log "dizin   : ${RELEASE_DIR}"

read_env() {
  local key="$1" file="${SHARED_DIR}/.env"
  [ -f "${file}" ] || return 0
  sed -n "s/^[[:space:]]*${key}[[:space:]]*=[[:space:]]*//p" "${file}" \
    | tail -n 1 | sed -e 's/^"//' -e 's/"$//' -e "s/^'//" -e "s/'$//"
}

APP_SERVICE="${APP_SERVICE:-$(read_env APP_SERVICE)}"
APP_SERVICE="${APP_SERVICE:-meluni}"
HEALTH_URL="${HEALTH_URL:-$(read_env HEALTH_URL)}"
HEALTH_URL="${HEALTH_URL:-http://127.0.0.1:8000/api/health}"

need() {
  command -v "$1" >/dev/null 2>&1 || die "'$1' sunucuda kurulu değil."
}

# ── Backend ─────────────────────────────────────────────────────────────
[ -f backend/requirements.txt ] || die "backend/requirements.txt yok — yanlış release mi?"
need python3

if [ ! -x "${VENV}/bin/python" ]; then
  log "sanal ortam oluşturuluyor: ${VENV}"
  python3 -m venv "${VENV}"
fi

log "backend bağımlılıkları kuruluyor"
"${VENV}/bin/pip" install --quiet --upgrade pip wheel
# Sunucuda derleyici olmayabilir; mümkün olduğunca hazır tekerlek kullan.
"${VENV}/bin/pip" install --quiet --prefer-binary -r backend/requirements.txt

# .env'i backend'in yanına bağla — server.py onu kendi dizininden okuyor.
ln -sfn "${SHARED_DIR}/.env" "${RELEASE_DIR}/backend/.env"

# ── Frontend ────────────────────────────────────────────────────────────
# CI derlemesi `frontend/build` olarak gelir. Gelmediyse nginx eski release'i
# servis etmeye devam eder; bunu sessizce geçmek "site güncellenmedi"
# şikayetinin en sık sebebi, o yüzden gürültülü uyarıyoruz.
if [ -d frontend/build ] && [ -n "$(ls -A frontend/build 2>/dev/null)" ]; then
  log "frontend derlemesi bulundu ($(du -sh frontend/build | cut -f1))"
else
  warn "frontend/build YOK — bu release yalnız backend'i günceller."
  warn "CI'da derleme adımı çalıştı mı? (Actions > Deploy > 'Frontend derle')"
fi

# ── Servisi yeniden başlat ──────────────────────────────────────────────
if sudo -n systemctl restart "${APP_SERVICE}" 2>/dev/null; then
  log "systemd servisi yeniden başlatıldı: ${APP_SERVICE}"
else
  die "systemctl restart ${APP_SERVICE} başarısız. Parolasız sudo tanımlı mı?
     sudo visudo -f /etc/sudoers.d/meluni-deploy
     <kullanıcı> ALL=(root) NOPASSWD: /bin/systemctl restart ${APP_SERVICE}"
fi

# nginx yalnız yapılandırma değiştiyse gerekli; reload ucuz, her deployda yap.
if sudo -n nginx -t >/dev/null 2>&1; then
  sudo -n systemctl reload nginx 2>/dev/null || warn "nginx reload edilemedi"
fi

# ── Sağlık kontrolü ─────────────────────────────────────────────────────
need curl
log "sağlık kontrolü: ${HEALTH_URL}"
for attempt in $(seq 1 20); do
  if curl -fsS --max-time 5 "${HEALTH_URL}" >/dev/null 2>&1; then
    log "sağlık kontrolü geçti (${attempt}. deneme)"
    log "tamamlandı"
    exit 0
  fi
  [ "${attempt}" -eq 20 ] && break
  sleep 3
done

# Buraya düştüysek servis ayakta değil. Teşhisi kolaylaştırmak için son
# logları bas — deploy geri alınacağı için bu çıktı tek ipucu olabilir.
warn "sağlık kontrolü 20 denemede geçmedi; son loglar:"
sudo -n journalctl -u "${APP_SERVICE}" -n 40 --no-pager 2>/dev/null || true
die "servis ayağa kalkmadı, release geri alınıyor"
