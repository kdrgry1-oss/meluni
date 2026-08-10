#!/bin/bash
# =============================================================================
# build-android.sh — Facette Android APK/AAB Build Script
# =============================================================================
# Usage:
#   bash build-android.sh release    # Production AAB (Play Store için)
#   bash build-android.sh debug      # Debug APK (test için)
#
# Önkoşullar:
#   - Android Studio + Android SDK kurulu
#   - JDK 17 kurulu
#   - ANDROID_HOME env değişkeni set
#   - /app/keystores/facette.keystore mevcut (release için)
# =============================================================================

set -e
cd "$(dirname "$0")"
MODE="${1:-debug}"

echo ">>> Step 1/4: React frontend build"
yarn build

echo ">>> Step 2/4: Capacitor sync"
npx cap sync android

echo ">>> Step 3/4: Gradle build"
cd android

if [ "$MODE" = "release" ]; then
  if [ ! -f "app/facette.keystore" ]; then
    echo "❌ Keystore yok! Önce keystore oluştur:"
    echo "   keytool -genkey -v -keystore app/facette.keystore -alias facette -keyalg RSA -keysize 2048 -validity 10000"
    exit 1
  fi

  # Release keystore.properties
  cat > keystore.properties << EOF
storeFile=facette.keystore
storePassword=${KEYSTORE_PASSWORD:?Set KEYSTORE_PASSWORD env}
keyAlias=facette
keyPassword=${KEY_PASSWORD:?Set KEY_PASSWORD env}
EOF

  ./gradlew bundleRelease
  echo ""
  echo "✅ AAB oluşturuldu: android/app/build/outputs/bundle/release/app-release.aab"
  echo "   Bu dosyayı Google Play Console > Production > New release'e upload et"
else
  ./gradlew assembleDebug
  echo ""
  echo "✅ APK oluşturuldu: android/app/build/outputs/apk/debug/app-debug.apk"
  echo "   Test cihazına: adb install android/app/build/outputs/apk/debug/app-debug.apk"
fi
