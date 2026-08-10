#!/usr/bin/env bash
#
# meluni - SSH ile sunucuya deploy
#
# Hem yerel makineden hem de GitHub Actions icinden ayni sekilde calisir.
# Calisma mantigi: HEAD'den bir tar arsivi uretir, sunucuya kopyalar,
# releases/<sha> altina acar, "current" symlink'ini yeni release'e cevirir
# ve varsa sunucudaki post-deploy hook'unu tetikler.
#
# Kullanim:
#   DEPLOY_HOST=<sunucu-ip> DEPLOY_USER=<kullanici> \
#   SSH_KEY=~/.ssh/meluni_deploy ./scripts/deploy.sh
#
set -euo pipefail

DEPLOY_HOST="${DEPLOY_HOST:?DEPLOY_HOST tanimli degil}"
DEPLOY_USER="${DEPLOY_USER:?DEPLOY_USER tanimli degil}"
DEPLOY_PORT="${DEPLOY_PORT:-22}"
DEPLOY_PATH="${DEPLOY_PATH:-/home/${DEPLOY_USER}/apps/meluni}"
KEEP_RELEASES="${KEEP_RELEASES:-5}"
SSH_KEY="${SSH_KEY:-}"

SSH_OPTS=(-p "${DEPLOY_PORT}" -o StrictHostKeyChecking=yes)
SCP_OPTS=(-P "${DEPLOY_PORT}" -o StrictHostKeyChecking=yes)
if [ -n "${SSH_KEY}" ]; then
  # Anahtar verildiyse (CI durumu) parola sorulmasin, takilip kalmasin.
  SSH_OPTS+=(-i "${SSH_KEY}" -o IdentitiesOnly=yes -o BatchMode=yes)
  SCP_OPTS+=(-i "${SSH_KEY}" -o IdentitiesOnly=yes -o BatchMode=yes)
fi

TARGET="${DEPLOY_USER}@${DEPLOY_HOST}"
REVISION="$(git rev-parse HEAD)"
SHORT_REV="$(git rev-parse --short HEAD)"
RELEASE_ID="$(date -u +%Y%m%d%H%M%S)-${SHORT_REV}"

log() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }

WORKDIR="$(mktemp -d)"
trap 'rm -rf "${WORKDIR}"' EXIT

log "Release paketi hazirlaniyor (${REVISION})"
git archive --format=tar "${REVISION}" -o "${WORKDIR}/release.tar"

# EXTRA_PATHS: git'te izlenmeyen ama pakete girmesi gereken yollar
# (orn. CI'da uretilen frontend/build). Bosluklarla ayrilir.
for extra in ${EXTRA_PATHS:-}; do
  if [ -e "${extra}" ]; then
    log "  + ${extra} ($(du -sh "${extra}" | cut -f1))"
    tar -rf "${WORKDIR}/release.tar" "${extra}"
  else
    log "  ! ${extra} bulunamadi, atlaniyor"
  fi
done

gzip -9 "${WORKDIR}/release.tar"
printf '%s\n' "${REVISION}" > "${WORKDIR}/REVISION"

log "Sunucuya baglaniliyor: ${TARGET}:${DEPLOY_PORT}"
ssh "${SSH_OPTS[@]}" "${TARGET}" "mkdir -p '${DEPLOY_PATH}/releases' '${DEPLOY_PATH}/shared' '${DEPLOY_PATH}/tmp'"

log "Paket kopyalaniyor"
scp "${SCP_OPTS[@]}" "${WORKDIR}/release.tar.gz" "${TARGET}:${DEPLOY_PATH}/tmp/${RELEASE_ID}.tar.gz"

log "Release aciliyor ve etkinlestiriliyor: ${RELEASE_ID}"
ssh "${SSH_OPTS[@]}" "${TARGET}" bash -s -- \
  "${DEPLOY_PATH}" "${RELEASE_ID}" "${REVISION}" "${KEEP_RELEASES}" <<'REMOTE'
set -euo pipefail

DEPLOY_PATH="$1"
RELEASE_ID="$2"
REVISION="$3"
KEEP_RELEASES="$4"

RELEASE_DIR="${DEPLOY_PATH}/releases/${RELEASE_ID}"
ARCHIVE="${DEPLOY_PATH}/tmp/${RELEASE_ID}.tar.gz"

mkdir -p "${RELEASE_DIR}"
tar -xzf "${ARCHIVE}" -C "${RELEASE_DIR}"
rm -f "${ARCHIVE}"
printf '%s\n' "${REVISION}" > "${RELEASE_DIR}/REVISION"

# Ortak (release'ler arasi paylasilan) dosyalari bagla.
# shared/.env sunucuda tutulur, repoya asla girmez.
if [ -f "${DEPLOY_PATH}/shared/.env" ]; then
  ln -sfn "${DEPLOY_PATH}/shared/.env" "${RELEASE_DIR}/.env"
fi
for shared_dir in storage uploads var/log; do
  if [ -d "${DEPLOY_PATH}/shared/${shared_dir}" ]; then
    rm -rf "${RELEASE_DIR:?}/${shared_dir}"
    mkdir -p "$(dirname "${RELEASE_DIR}/${shared_dir}")"
    ln -sfn "${DEPLOY_PATH}/shared/${shared_dir}" "${RELEASE_DIR}/${shared_dir}"
  fi
done

PREVIOUS=""
if [ -L "${DEPLOY_PATH}/current" ]; then
  PREVIOUS="$(readlink -f "${DEPLOY_PATH}/current")"
fi

# Atomik gecis: once gecici symlink, sonra rename.
ln -sfn "${RELEASE_DIR}" "${DEPLOY_PATH}/current.new"
mv -Tf "${DEPLOY_PATH}/current.new" "${DEPLOY_PATH}/current"

# Uygulamaya ozel adimlar (bagimlilik kurulumu, build, servis restart)
# burada calisir. Dosya repodan gelir; yoksa sessizce atlanir.
HOOK="${DEPLOY_PATH}/current/scripts/post-deploy.sh"
if [ -x "${HOOK}" ]; then
  echo "--> post-deploy.sh calistiriliyor"
  if ! (cd "${DEPLOY_PATH}/current" && DEPLOY_PATH="${DEPLOY_PATH}" RELEASE_ID="${RELEASE_ID}" "${HOOK}"); then
    echo "!!! post-deploy basarisiz, onceki release'e donuluyor" >&2
    if [ -n "${PREVIOUS}" ] && [ -d "${PREVIOUS}" ]; then
      ln -sfn "${PREVIOUS}" "${DEPLOY_PATH}/current.new"
      mv -Tf "${DEPLOY_PATH}/current.new" "${DEPLOY_PATH}/current"
      echo "!!! geri alindi: ${PREVIOUS}" >&2
    fi
    rm -rf "${RELEASE_DIR}"
    exit 1
  fi
elif [ -f "${HOOK}" ]; then
  echo "--> post-deploy.sh bulundu ama calistirilabilir degil, atlaniyor"
fi

# Eski release'leri temizle.
cd "${DEPLOY_PATH}/releases"
ls -1dt */ 2>/dev/null | tail -n "+$((KEEP_RELEASES + 1))" | while read -r old; do
  old="${old%/}"
  [ "${old}" = "${RELEASE_ID}" ] && continue
  echo "--> eski release siliniyor: ${old}"
  rm -rf "${DEPLOY_PATH:?}/releases/${old}"
done

echo "--> aktif release: $(readlink -f "${DEPLOY_PATH}/current")"
REMOTE

log "Deploy tamamlandi: ${RELEASE_ID} -> ${TARGET}:${DEPLOY_PATH}/current"
