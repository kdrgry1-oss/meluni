#!/bin/bash
# =============================================================================
# build-ios.sh — Facette iOS Build & Archive Script (Mac üzerinde çalışır)
# =============================================================================
# Usage:
#   bash build-ios.sh           # Sadece sync + Xcode aç
#   bash build-ios.sh archive   # Archive + IPA üret (App Store için)
#
# Önkoşullar:
#   - Mac OS + Xcode 15+
#   - Apple Developer hesabı + Team ID
#   - CocoaPods: sudo gem install cocoapods
# =============================================================================

set -e
cd "$(dirname "$0")"
MODE="${1:-open}"

echo ">>> Step 1/3: React frontend build"
yarn build

echo ">>> Step 2/3: Capacitor sync (CocoaPods install dahil)"
npx cap sync ios

echo ">>> Step 3/3: $MODE"

if [ "$MODE" = "archive" ]; then
  cd ios/App
  TEAM_ID="${APPLE_TEAM_ID:?Set APPLE_TEAM_ID env to your Apple Developer Team ID}"

  xcodebuild -workspace App.xcworkspace \
    -scheme App \
    -configuration Release \
    -archivePath ./build/Facette.xcarchive \
    -destination 'generic/platform=iOS' \
    DEVELOPMENT_TEAM="$TEAM_ID" \
    archive

  xcodebuild -exportArchive \
    -archivePath ./build/Facette.xcarchive \
    -exportPath ./build/ipa \
    -exportOptionsPlist ../ExportOptions.plist

  echo ""
  echo "✅ IPA oluşturuldu: ios/App/build/ipa/Facette.ipa"
  echo "   App Store Connect upload: xcrun altool --upload-app -f Facette.ipa -u YOUR_APPLE_ID -p APP_SPECIFIC_PASSWORD"
else
  echo "Xcode açılıyor — manuel olarak Product > Archive yap"
  npx cap open ios
fi
