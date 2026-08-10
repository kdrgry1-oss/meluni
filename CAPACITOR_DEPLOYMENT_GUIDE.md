# 📱 Facette Mobile App — Capacitor Wrap Kurulum Rehberi

> **Hedef:** Mevcut React admin panelini Android ve iOS native uygulama olarak App Store + Google Play'e yayınlamak.
>
> **Süre:** Geliştirme 1 hafta + Store onay süreci 1-2 hafta = **toplam 2-3 hafta**.
>
> **Maliyet:** Apple Developer ($99/yıl) + Google Play Console ($25 tek seferlik).

---

## 🎯 Bu Dokümanda Neler Var?

1. ✅ Backend hazırlığı (TAMAMLANDI — Iter35)
2. 🔧 Capacitor projesi kurulumu (yapacağız)
3. 📲 Build & Release adımları
4. 🏪 Store listing rehberi (Apple + Google)

---

## ✅ 1. Backend Hazırlığı — TAMAMLANDI

Mevcut backend tamamen mobile-ready:

```
✅ JWT Auth (Authorization: Bearer header)
✅ CORS whitelist'te capacitor://localhost + ionic://localhost
✅ Mobile endpoints: /api/app/version-check, /api/app/devices/register, /api/app/config
✅ Push notification altyapısı: /api/admin/mobile/push/send
✅ Admin yönetim paneli: /admin/mobil-uygulama
```

**Kullanıcının yapması gereken:**
- `FCM_SERVER_KEY` env'e eklemek (Firebase Console > Cloud Messaging > Server Key)
- `RESEND_API_KEY` env'e eklemek (e-posta için)

---

## 🔧 2. Capacitor Projesi Kurulumu

### A. Hazırlık (PC/Mac'te)

```bash
# Node 20+ + Yarn gerekli
node -v   # v20+
yarn -v   # 1.22+

# iOS için Mac + Xcode 15+ gerekli
xcode-select --install

# Android için Android Studio + JDK 17+
# https://developer.android.com/studio
```

### B. Capacitor Init

```bash
cd /app/frontend

# Capacitor paketleri ekle
yarn add @capacitor/core @capacitor/cli @capacitor/android @capacitor/ios
yarn add @capacitor/push-notifications @capacitor/app @capacitor/network
yarn add @capacitor/preferences @capacitor/splash-screen @capacitor/status-bar

# Init (build çıktısı zaten /app/frontend/build)
npx cap init "Facette" "com.facette.app" --web-dir=build

# Platform ekle
npx cap add android
npx cap add ios
```

### C. capacitor.config.ts (oluştur)

```typescript
// /app/frontend/capacitor.config.ts
import type { CapacitorConfig } from '@capacitor/cli';

const config: CapacitorConfig = {
  appId: 'com.facette.app',
  appName: 'Facette',
  webDir: 'build',
  bundledWebRuntime: false,
  server: {
    // Production'da kaldır - sadece development için
    // url: 'https://self-host-test.preview.emergentagent.com',
    cleartext: false,
    androidScheme: 'https',
  },
  plugins: {
    SplashScreen: {
      launchShowDuration: 2000,
      backgroundColor: '#000000',
      androidSplashResourceName: 'splash',
      androidScaleType: 'CENTER_CROP',
      showSpinner: true,
      spinnerColor: '#ffffff',
      splashFullScreen: true,
      splashImmersive: true,
    },
    PushNotifications: {
      presentationOptions: ['badge', 'sound', 'alert'],
    },
    StatusBar: {
      style: 'DARK',
      backgroundColor: '#000000',
    },
  },
  ios: {
    contentInset: 'always',
    backgroundColor: '#000000',
  },
  android: {
    allowMixedContent: false,
    captureInput: true,
    webContentsDebuggingEnabled: false,
  },
};

export default config;
```

### D. Frontend Native Bridge

`/app/frontend/src/lib/native.js` dosyası oluştur:

```javascript
// Native bridge — Capacitor ortamında native özellikler aktif olur, web'de no-op
import { Capacitor } from '@capacitor/core';
import { PushNotifications } from '@capacitor/push-notifications';
import { App } from '@capacitor/app';
import { Preferences } from '@capacitor/preferences';
import axios from 'axios';

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;
export const isNative = Capacitor.isNativePlatform();
export const platform = Capacitor.getPlatform(); // 'ios' | 'android' | 'web'

/** Push notification kayıt ve token gönderme */
export async function setupPushNotifications() {
  if (!isNative) return;

  // Permission iste
  let permStatus = await PushNotifications.checkPermissions();
  if (permStatus.receive === 'prompt') {
    permStatus = await PushNotifications.requestPermissions();
  }
  if (permStatus.receive !== 'granted') return;

  await PushNotifications.register();

  // FCM/APNs token alındığında backend'e gönder
  PushNotifications.addListener('registration', async (token) => {
    const userToken = localStorage.getItem('token');
    if (!userToken) return;

    let deviceId = (await Preferences.get({ key: 'device_id' })).value;
    if (!deviceId) {
      deviceId = `${platform}-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`;
      await Preferences.set({ key: 'device_id', value: deviceId });
    }

    await axios.post(`${API}/app/devices/register`, {
      platform: platform,
      device_id: deviceId,
      push_token: token.value,
      app_version: '1.0.0',
      os_version: '',
      model: '',
    }, { headers: { Authorization: `Bearer ${userToken}` } });
  });

  // Bildirim alındığında
  PushNotifications.addListener('pushNotificationReceived', (notification) => {
    console.log('Push received:', notification);
  });

  // Bildirime tıklandığında — deep link
  PushNotifications.addListener('pushNotificationActionPerformed', (action) => {
    const data = action.notification.data;
    if (data?.deep_link) {
      // facette://order/123 → /admin/orders/123
      const path = data.deep_link.replace('facette://', '/');
      window.location.href = path;
    }
  });
}

/** Version check & force update */
export async function checkAppVersion() {
  if (!isNative) return null;
  const appInfo = await App.getInfo();
  const res = await axios.get(`${API}/app/version-check?platform=${platform}&current_version=${appInfo.version}`);
  return res.data; // {force_update_required, update_available, store_url, ...}
}

/** Deep link handler (uygulama dışından açılırsa) */
export function setupDeepLinks() {
  if (!isNative) return;
  App.addListener('appUrlOpen', (event) => {
    // facette://order/123
    const url = event.url.replace('facette://', '/');
    window.location.href = url;
  });
}
```

### E. App.js — Native Setup

```javascript
// /app/frontend/src/App.js içine eklenecek (en üst seviye useEffect)
import { useEffect } from 'react';
import { isNative, setupPushNotifications, setupDeepLinks, checkAppVersion } from './lib/native';

function App() {
  useEffect(() => {
    if (isNative) {
      setupDeepLinks();
      setupPushNotifications();
      checkAppVersion().then((info) => {
        if (info?.force_update_required) {
          alert(`Yeni sürüm gerekli. App Store'a yönlendiriliyorsunuz.`);
          window.location.href = info.store_url;
        }
      });
    }
  }, []);
  // ... mevcut render
}
```

---

## 📲 3. Build & Release

### Android Build (APK + AAB)

```bash
cd /app/frontend
yarn build                    # React build → /app/frontend/build
npx cap sync android          # Native projeye kopyala

# Android Studio aç
npx cap open android

# Studio içinde:
# 1) Build > Generate Signed Bundle/APK > Android App Bundle (.aab)
# 2) Keystore oluştur (ilk kez): /app/keystores/facette.keystore
#    - alias: facette
#    - password: GÜÇLÜ_PAROLA (kaydet!)
# 3) build/outputs/bundle/release/app-release.aab dosyası → Play Console upload
```

### iOS Build (IPA)

```bash
cd /app/frontend
yarn build
npx cap sync ios
npx cap open ios

# Xcode içinde:
# 1) Apple ID + Team Selection: Signing & Capabilities
# 2) Bundle Identifier: com.facette.app
# 3) Push Notifications + Background Modes capabilities ekle
# 4) Product > Archive > Distribute App > App Store Connect
```

---

## 🏪 4. Store Listing Rehberi

### A. Apple App Store

1. **Apple Developer Account** ($99/yıl): https://developer.apple.com/programs/
2. **App Store Connect** → My Apps → "+" → New App:
   - Bundle ID: `com.facette.app`
   - SKU: `FACETTE001`
   - Primary Language: Türkçe
3. **Listing bilgileri:**
   - **App Name:** Facette
   - **Subtitle:** Premium Kadın Giyim
   - **Description:** "Facette ile en yeni moda trendlerini keşfedin..."
   - **Keywords:** moda, kadın giyim, trendyol, alışveriş
   - **Category:** Shopping
   - **Age Rating:** 4+
4. **Screenshots:** Her cihaz boyutu için 3-10 adet (6.7", 6.5", 5.5", iPad)
   - Tools: https://www.appscreens.com (otomatik render)
5. **App Icon:** 1024x1024 PNG, transparent değil
6. **Privacy Policy URL:** https://facette.com.tr/privacy
7. **TestFlight:** İlk önce internal testing → external → App Review

⚠️ **App Review için kritik notlar:**
- E-ticaret app'leri test hesabı + test kartı bilgisi ister
- KVKK + Privacy nutrition label doldurun
- Push notification'ları opt-in olmalı

### B. Google Play Store

1. **Google Play Console** ($25 tek seferlik): https://play.google.com/console
2. **Create app:**
   - App name: Facette
   - Default language: Türkçe
   - App or game: App
   - Free or paid: Free
3. **Setup:**
   - Privacy Policy URL: https://facette.com.tr/privacy
   - App access: All functionality available
   - Ads: No ads (eğer göstermiyorsanız)
   - Content rating: IARC questionnaire
   - Target audience: 18+
   - News app: No
   - Data safety: KVKK uyumlu form (toplanan veriler, paylaşımlar)
4. **Store listing:**
   - Short description (80 char): "Premium kadın giyim — yeni sezon koleksiyonu, hızlı kargo"
   - Full description (4000 char): 5 paragraf, anahtar kelime zengin
   - Graphics: Icon 512x512, Feature graphic 1024x500
   - Screenshots: Phone (min 2), 7" tablet, 10" tablet
5. **Production release:**
   - Internal testing → Closed testing → Open testing → Production
   - İlk sürüm 1-3 gün incelenir

⚠️ **Play Store kritik:**
- App bundle (.aab) zorunlu (APK değil)
- 64-bit native code zorunlu
- Target SDK 34+ (Android 14)

---

## 📋 5. Checklist (Sırasıyla)

```
[ ] Apple Developer hesabı ($99) → 24-48 saat onay bekleme
[ ] Google Play Console ($25) → anında aktif
[ ] Firebase Console'da Facette projesi oluştur (FCM için)
[ ] FCM_SERVER_KEY backend .env'e ekle
[ ] APNs Key (Apple Developer → Keys) — iOS push için
[ ] Capacitor projesi init + sync (yukarıdaki komutlar)
[ ] Splash screen + icon assets hazırla (1024x1024 PNG)
[ ] Privacy Policy + ToS sayfaları yayınla (facette.com.tr/privacy)
[ ] TestFlight + Internal Testing'de en az 5 kişi test et
[ ] Screenshots üret (her cihaz boyutu için)
[ ] App Store + Play Console submit
[ ] Review süreci (Apple: 1-7 gün, Google: 1-3 gün)
[ ] LIVE 🚀
```

---

## 🆘 Yaygın Sorunlar & Çözümleri

| Sorun | Çözüm |
|---|---|
| Backend HTTPS sertifikası hata veriyor | Mobile'da self-signed kabul edilmez. Production'da geçerli sertifika zorunlu (Cloudflare/Let's Encrypt) |
| Push gelmiyor | FCM server key + APNs key doğru mu? Backend `/api/admin/mobile/devices` cihazı görüyor mu? |
| Apple Review Reject: "Guideline 4.0 - Design" | Web wrapper algılarsa reject. Splash screen + native scroll + status bar düzgün olmalı |
| Apple Review: "Test account gerekli" | Demo creds: `demo@facette.com / demo123` ekleyin (review esnasında kullanılır) |
| Android Build: "minSdkVersion uyumsuz" | `android/variables.gradle` minSdkVersion: 22 yapın |
| iOS Archive: "No matching profiles" | Xcode > Preferences > Accounts > Download Manual Profiles |

---

## 💰 Maliyet Tahmini

| Kalem | Maliyet | Sıklık |
|---|---|---|
| Apple Developer | $99 (~3,500₺) | Yıllık |
| Google Play Console | $25 (~900₺) | Tek seferlik |
| Firebase Cloud Messaging | Ücretsiz | - |
| App icon + screenshot tasarım | 5,000-15,000₺ | Tek seferlik |
| **TOPLAM (1. yıl)** | **~10,000-20,000₺** | - |

---

## 📞 Destek

Sorun yaşarsanız:
- Capacitor docs: https://capacitorjs.com/docs
- Apple Developer Forums: https://developer.apple.com/forums/
- Stack Overflow: tag `capacitor` + `react-native`

---

*Bu rehber Facette Commerce OS v1.0 (Mayıs 2026) için hazırlandı.*
*Sorularınız için: tech@facette.com.tr*
