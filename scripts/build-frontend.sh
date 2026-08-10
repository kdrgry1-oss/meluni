#!/usr/bin/env bash
#
# meluni — frontend derlemesi (yerel makine)
#
# NEDEN AYRI BİR BETİK:
#   CRA, REACT_APP_* değişkenlerini DERLEME ANINDA gömer; çalışma anında
#   değiştirilemez. Elle "CI=true npx craco build" yazıldığında bu değişkenler
#   verilmeyi unutuluyor ve derleme SESSİZCE bozuk çıkıyor:
#     • REACT_APP_BACKEND_URL boş  → panel API'ye ulaşamaz, "Giriş başarısız".
#     • REACT_APP_SITE_TITLE boş   → sekmede ham "%REACT_APP_SITE_TITLE%".
#   Bu iki hata da canlıda yaşandı. Değerler burada varsayılanlı tanımlı ve
#   derleme sonunda GERÇEKTEN gömüldükleri doğrulanıyor.
#
# Kullanım:
#   bash scripts/build-frontend.sh
#   REACT_APP_BACKEND_URL=https://api.baska.com bash scripts/build-frontend.sh
#
# Bu değerler üretilen JS içinde herkese açıktır — gizli bilgi KONMAZ.
#
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT}/frontend"

log() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
die() { printf '\033[1;31mHATA:\033[0m %s\n' "$*" >&2; exit 1; }

export CI="${CI:-true}"                       # uyarılar hata sayılır (Cloudflare ile aynı)
export NODE_OPTIONS="${NODE_OPTIONS:---max-old-space-size=4096}"

export REACT_APP_BACKEND_URL="${REACT_APP_BACKEND_URL:-https://api.meluni.com.tr}"
export REACT_APP_SITE_NAME="${REACT_APP_SITE_NAME:-MELUNİ}"
export REACT_APP_SITE_URL="${REACT_APP_SITE_URL:-https://meluni.com.tr}"
export REACT_APP_SITE_TITLE="${REACT_APP_SITE_TITLE:-MELUNİ | Yeni Sezon Kadın & Erkek Giyim}"
export REACT_APP_SITE_DESC="${REACT_APP_SITE_DESC:-MELUNİ — yeni sezon koleksiyonu. Hızlı kargo, güvenli ödeme, kolay iade.}"
export REACT_APP_CDN_URL="${REACT_APP_CDN_URL:-${REACT_APP_SITE_URL}}"

log "API adresi : ${REACT_APP_BACKEND_URL}"
log "site adı   : ${REACT_APP_SITE_NAME}"

# Bağımlılıklar yoksa kur. Kuruluysa DOKUNMA — sürüm oynatmak derlemeyi bozar.
if [ ! -d node_modules ]; then
  log "node_modules yok, bağımlılıklar kuruluyor"
  if [ -f yarn.lock ] && command -v yarn >/dev/null 2>&1; then
    yarn install --frozen-lockfile
  else
    npm ci
  fi
fi

log "derleniyor (birkaç dakika sürer)"
npx craco build

# ── Derlemenin gerçekten sağlam olduğunu doğrula ────────────────────────
[ -f build/index.html ] || die "build/index.html üretilmedi."

# 1) API adresi JS'e gömüldü mü? Gömülmediyse panel giriş yapamaz.
host="$(printf '%s' "${REACT_APP_BACKEND_URL}" | sed -e 's|^https\?://||' -e 's|/.*$||')"
grep -rqF "${host}" build/static/js/ \
  || die "API adresi (${host}) derlemeye gömülmemiş — paket bozuk, deploy ETMEYİN."

# 2) index.html'de doldurulmamış yer tutucu kaldı mı?
if grep -o '%REACT_APP_[A-Z_]*%' build/index.html 2>/dev/null | sort -u | grep .; then
  die "Yukarıdaki değişkenler tanımsız kaldı; sayfa kaynağında ham yer tutucu görünür."
fi

log "doğrulandı — API adresi gömülü, yer tutucu kalmadı"
log "derleme boyutu: $(du -sh build | cut -f1)"
log "sıradaki adım:"
log "  EXTRA_PATHS=frontend/build DEPLOY_HOST=83.150.214.157 DEPLOY_USER=kadir bash scripts/deploy.sh"
