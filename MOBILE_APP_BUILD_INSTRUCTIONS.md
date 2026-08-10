# 🚀 Facette Mobile App — Mac/PC'nizde Çalıştırma Rehberi

> **Durum:** Capacitor projesi sunucuda %100 hazır. Mac/PC'nize indirip 3 komutla build alabilirsiniz.

---

## 📦 1. ÖNCE GIT'E PUSH ET

Bu pod'da hazırlanan tüm dosyaları kendi GitHub repo'nuza kaydetmek için Emergent panelindeki **"Save to GitHub"** butonunu kullanın.

Sonra Mac/PC'nize klonla:
```bash
git clone https://github.com/<sizin-username>/<repo-name>.git
cd <repo-name>/frontend
yarn install
```

---

## 🤖 2. ANDROID BUILD (Windows / Mac / Linux)

### Önkoşullar
- **Android Studio** (en son sürüm) — https://developer.android.com/studio
- **JDK 17** — `brew install openjdk@17` (Mac) veya https://adoptium.net (Win)
- **Android SDK** — Studio kurulumu otomatik kurar
- ENV: `ANDROID_HOME=/Users/<sen>/Library/Android/sdk` (Mac) ya da `C:\Users\<sen>\AppData\Local\Android\Sdk` (Win)

### Debug APK (test için)
```bash
cd frontend
bash build-android.sh debug
```
Çıktı: `frontend/android/app/build/outputs/apk/debug/app-debug.apk`
Telefona yüklemek için:
```bash
adb install android/app/build/outputs/apk/debug/app-debug.apk
```

### Release AAB (Play Store için)

**A) Önce keystore oluştur (TEK SEFER):**
```bash
cd frontend/android/app
keytool -genkey -v -keystore facette.keystore \
  -alias facette \
  -keyalg RSA \
  -keysize 2048 \
  -validity 10000

# 6+ haneli güçlü password gir
# CN: Facette, Organization: Facette Dış Tic. A.Ş., City: Istanbul, Country: TR
```

⚠️ **`facette.keystore` dosyasını GÜVENLİ YERE YEDEKLE!** Kaybedersen Play Store'a yeni güncelleme yükleyemezsin.

**B) Şifreleri env'e koy + build:**
```bash
export KEYSTORE_PASSWORD="senin-güçlü-paroland"
export KEY_PASSWORD="senin-güçlü-paroland"
cd frontend
bash build-android.sh release
```
Çıktı: `frontend/android/app/build/outputs/bundle/release/app-release.aab`
Bu dosyayı **Google Play Console > Üretim sürümü > Yeni sürüm** ekranına yükle.

---

## 🍎 3. iOS BUILD (Sadece Mac)

### Önkoşullar
- **macOS Sonoma+** ile **Xcode 15+** (App Store'dan)
- **Apple Developer Account** ($99/yıl)
- **CocoaPods** — `sudo gem install cocoapods`
- **Apple Team ID** — https://developer.apple.com/account → Membership → Team ID (10 karakter)

### Xcode'da Aç + Manuel Archive
```bash
cd frontend
bash build-ios.sh
# Bu komut: yarn build → cap sync → Xcode açar
```

Xcode içinde:
1. **Sol panel** → "App" tıkla
2. **Signing & Capabilities** sekmesi
3. **Team:** Apple Developer hesabınla seçili olmalı
4. **Bundle Identifier:** `com.facette.app` (otomatik)
5. **+ Capability** → **Push Notifications** ekle
6. **+ Capability** → **Background Modes** ekle → "Remote notifications" işaretle
7. Üstte **"Any iOS Device (arm64)"** seç
8. **Product menu → Archive** (~3-5 dakika)
9. Archive ekranı açılır → **Distribute App** → **App Store Connect** → **Upload**

### Komut Satırı Archive (otomasyon için)
```bash
export APPLE_TEAM_ID="ABC123XYZ4"   # Apple Developer Team ID
cd frontend
bash build-ios.sh archive
```
Çıktı: `frontend/ios/App/build/ipa/Facette.ipa`
Upload için:
```bash
xcrun altool --upload-app -f Facette.ipa \
  -u "your-apple-id@example.com" \
  -p "app-specific-password"   # https://appleid.apple.com/account/manage
```

---

## 🎨 4. APP ICON & SPLASH SCREEN

Şu an Capacitor default ikonlar var. Facette logosu hazırlandığında:

**Tek komutla tüm boyutlara üretmek için:**
```bash
cd frontend
yarn add -D @capacitor/assets

# resources/ klasörü oluştur, içine:
#   - icon-only.png        (1024x1024 PNG, transparent değil, siyah arka plan)
#   - icon-foreground.png  (1024x1024 PNG, transparent, sadece logo)
#   - icon-background.png  (1024x1024 PNG, sadece arka plan rengi)
#   - splash.png           (2732x2732 PNG, ortada logo, kalan siyah)
#   - splash-dark.png      (aynı)

npx capacitor-assets generate --iconBackgroundColor '#000000'

# Bu komut tüm Android mipmap-* + iOS AppIcon.appiconset boyutlarını üretir
```

📝 **Tasarım servisi öneri (5K-15K₺):**
- Fiverr Pro (gun_designs, dezigne)
- Behance/Dribbble lokal Türk tasarımcılar
- Spec: 1024x1024 master + 9:16 splash, transparent + filled varyant

---

## 🔔 5. FIREBASE CLOUD MESSAGING (Push için)

### A. Firebase Console Setup
1. https://console.firebase.google.com → "Add project" → "Facette"
2. Sol panel → **Project settings** → **Cloud Messaging** sekmesi
3. **Server key**'i kopyala (legacy) — backend `.env`'e ekle:
   ```
   FCM_SERVER_KEY=AAAAxxxx...
   ```

### B. Android (FCM)
1. Firebase Console → **Add app** → Android
2. Bundle ID: `com.facette.app`
3. **google-services.json** dosyasını indir
4. Dosyayı şuraya koy: `frontend/android/app/google-services.json`
5. Yeni build al — push notif aktif

### C. iOS (APNs)
1. Apple Developer → **Certificates, Identifiers & Profiles** → **Keys**
2. **+** → "Apple Push Notifications service (APNs)" işaretle → Continue → Download
3. Dosya: `AuthKey_XXXXXXX.p8`
4. **Key ID** ve **Team ID**'yi not al
5. Firebase Console → Project settings → Cloud Messaging → **Apple app configuration** → **APNs Authentication Key** ekle

⚠️ **APNs key'i 1 kez indirilebilir, kaybedersen yenisi gerekir.**

---

## ✅ 6. CHECKLIST

```
☐ Save to GitHub (Emergent panel)
☐ Mac/PC'ye git clone
☐ yarn install (frontend/)
☐ Android Studio + JDK 17 kur
☐ ANDROID_HOME env set
☐ App icon + splash tasarla (5-15K₺)
☐ npx capacitor-assets generate çalıştır
☐ Firebase Console projesi oluştur
☐ FCM_SERVER_KEY backend .env'e ekle
☐ google-services.json android/app/'e koy
☐ keystore oluştur + güvenli yedekle
☐ bash build-android.sh release → AAB
☐ Google Play Console listing oluştur (görseller, açıklama)
☐ Internal testing → Closed → Open → Production
☐ APNs key oluştur (Apple Developer)
☐ Firebase Console'a APNs key yükle
☐ Mac'te: bash build-ios.sh
☐ Xcode > Signing > Team seç
☐ Push Notifications + Background Modes capability ekle
☐ Product > Archive > Upload
☐ App Store Connect listing oluştur
☐ TestFlight Internal Testing
☐ Submit for Review (1-7 gün Apple, 1-3 gün Google)
☐ LIVE 🚀
```

---

## 🆘 SORUN YAŞARSAN

| Hata | Çözüm |
|---|---|
| `SDK not found` | `~/.zshrc`'ye: `export ANDROID_HOME=$HOME/Library/Android/sdk` |
| `Java version mismatch` | `java -version` kontrol → 17 olmalı. `brew install openjdk@17` |
| Xcode "No matching profiles" | Xcode > Preferences > Accounts > Download Manual Profiles |
| `pod install` failed | `cd ios/App && pod repo update && pod install` |
| Apple Reject "Web wrapper" | Splash screen + native scroll + offline mode + ekran 5+ saniye native gözüksün |
| Play Reject "Target SDK düşük" | `android/variables.gradle`: `targetSdkVersion = 34` |

---

## 📂 SUNUCUDA HAZIR DOSYALAR

```
/app/frontend/
├── android/                       ← Android native projesi (Capacitor üretti)
│   ├── app/build.gradle           ← Release signing config'li
│   ├── app/src/main/AndroidManifest.xml  ← Permissions + deep link
│   └── app/src/main/res/values/strings.xml
├── ios/                           ← iOS native projesi (Capacitor üretti)
│   ├── App/App/Info.plist         ← Push + deep link + privacy strings
│   └── ExportOptions.plist        ← App Store distribution config
├── capacitor.config.json          ← Capacitor ana config (siyah/beyaz tema)
├── build-android.sh               ← Android build script
├── build-ios.sh                   ← iOS build script
├── src/lib/native.js              ← Push + deep link + version check bridge
└── package.json                   ← Capacitor packages dahil
```

---

*Sorularınız için: tech@facette.com.tr*
*Tahmini ilk store yayını: 2-3 hafta (1 hafta build + 1-2 hafta review)*
