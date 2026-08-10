#!/usr/bin/env bash
#
# meluni - sunucu tarafi ilk kurulum (sadece bir kez calistirilir)
#
# Sunucuda deploy kullanicisi olarak calistirin:
#   bash server-setup.sh
#
# Yaptiklari:
#   - deploy dizin yapisini olusturur
#   - ~/.ssh/authorized_keys'e deploy public key'ini ekler (DEPLOY_PUBKEY verilirse)
#   - shared/.env dosyasini olusturur (yoksa)
#
set -euo pipefail

DEPLOY_PATH="${DEPLOY_PATH:-$HOME/apps/meluni}"
DEPLOY_PUBKEY="${DEPLOY_PUBKEY:-}"

log() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }

log "Dizin yapisi olusturuluyor: ${DEPLOY_PATH}"
mkdir -p \
  "${DEPLOY_PATH}/releases" \
  "${DEPLOY_PATH}/shared" \
  "${DEPLOY_PATH}/tmp"

if [ ! -f "${DEPLOY_PATH}/shared/.env" ]; then
  log "shared/.env olusturuluyor (icini siz dolduracaksiniz)"
  umask 077
  cat > "${DEPLOY_PATH}/shared/.env" <<'ENVEOF'
# meluni - sunucu ortam degiskenleri
# Bu dosya SADECE sunucuda durur, repoya girmez.
APP_ENV=production
ENVEOF
  chmod 600 "${DEPLOY_PATH}/shared/.env"
else
  log "shared/.env zaten var, dokunulmadi"
fi

if [ -n "${DEPLOY_PUBKEY}" ]; then
  log "Deploy public key authorized_keys'e ekleniyor"
  mkdir -p "$HOME/.ssh"
  chmod 700 "$HOME/.ssh"
  touch "$HOME/.ssh/authorized_keys"
  chmod 600 "$HOME/.ssh/authorized_keys"
  if grep -qxF "${DEPLOY_PUBKEY}" "$HOME/.ssh/authorized_keys"; then
    log "Key zaten ekli"
  else
    printf '%s\n' "${DEPLOY_PUBKEY}" >> "$HOME/.ssh/authorized_keys"
    log "Key eklendi"
  fi
else
  log "DEPLOY_PUBKEY verilmedi, authorized_keys'e dokunulmadi"
fi

log "Kurulum tamam."
echo
echo "Sunucunun host anahtarini GitHub secret'i olarak eklemek icin"
echo "kendi makinenizde su ciktiyi kullanin:"
echo "  ssh-keyscan -p 22 -H <SUNUCU_IP>"
