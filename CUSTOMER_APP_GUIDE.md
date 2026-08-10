# Facette Müşteri Mobil Uygulaması — Adım Adım Kurulum & Yayınlama

Bu rehber, **müşteriye görünen tüm storefront'u** (anasayfa, kategoriler, ürün detay, sepet,
iyzico ödemesi, hesabım, siparişler, iade, sipariş takip, arama…) **iOS + Android** native
uygulaması olarak App Store ve Google Play'e çıkarmanı sağlar.

> **Mimari:** Ayrı bir kod yazmıyoruz. Uygulama, mevcut React storefront'un **customer** build'ini
> Capacitor kabuğuyla sarar. Admin native uygulaması (`tr.com.facette.admin`) hiç etkilenmez;
> müşteri app ayrı bir kimliktir (`tr.com.facette.app`).

---

## 0) Elinde olması gerekenler (kontrol listesi)

| Gereken | Durum | Not |
|---|---|---|
| ✅ Apple Developer hesabı | **VAR** | App Store gönderimi için (99$/yıl). |
| ✅ Google Play Console hesabı | **VAR** | Play gönderimi için (25$ tek sefer). |
| ⬜ **Mac + Xcode** | Gerekli | iOS derlemesi SADECE Mac'te olur. Android için gerekmez. |
| ⬜ **Android Studio** | Gerekli | Android derlemesi için (Windows/Mac/Linux). |
| ⬜ **Firebase projesi (FCM)** | Gerekli | Push bildirim için. iOS'ta ayrıca APNs anahtarı. |
| ⬜ **Uygulama ikonu + splash görseli** | Gerekli | 1024×1024 ikon (şeffaf değil), splash logo. |
| ⬜ Node.js 18+ | Gerekli | `node -v` ile kontrol. |

Elinde Mac yoksa: Android'i tek başına çıkarabilirsin; iOS için ya bir Mac (veya
MacinCloud gibi kiralık Mac) ya da CI (Codemagic/EAS gibi) gerekir.

---

## 1) Tek seferlik: native projeleri üret

Kodda müşteri tarafı hazır (bu depoda). Sadece native iskeletleri **kendi makinende** üreteceksin.

```bash
# a) Web build — MÜŞTERİ varyantı (native açılışı storefront'a ayarlar)
cd frontend
npm install            # ilk kez
npm run build:customer

# b) Müşteri Capacitor projesi
cd ../mobile-customer
npm install
npx cap add ios        # sadece Mac'te
npx cap add android

# c) Web'i native'e kopyala
npx cap sync
```

Bu adımdan sonra `mobile-customer/ios` ve `mobile-customer/android` klasörleri oluşur.

> **Önemli:** `frontend`'i **`build:customer`** ile derle (düz `build` değil). Bu bayrak
> (`REACT_APP_APP_TARGET=customer`) native uygulamanın kökte **storefront** açmasını sağlar.
> Admin native app'i derlerken ise `npm run build:admin` kullanılır (o ayrı bir iş).

---

## 2) Uygulama kimliği & sürüm

- **iOS (Xcode):** `mobile-customer/ios/App/App.xcodeproj` → hedef **App** → *General*:
  - Bundle Identifier: `tr.com.facette.app`
  - Display Name: `Facette`
  - Version: `1.0.0`, Build: `1`
  - *Signing & Capabilities* → Team: Apple Developer hesabın; "Automatically manage signing" açık.
  - *Signing & Capabilities* → **+ Capability → Push Notifications** ekle.
  - *+ Capability → Background Modes* → "Remote notifications" işaretle.
- **Android (Android Studio):** `mobile-customer/android` aç.
  - `android/app/build.gradle` → `applicationId "tr.com.facette.app"`, `versionCode 1`, `versionName "1.0.0"`.

Her yeni yüklemede **Build/versionCode'u artır** (Apple ve Google aynı numarayı reddeder).

---

## 3) İkon & Splash

En kolay yol `@capacitor/assets`:

```bash
cd mobile-customer
# assets/ klasörüne koy: icon.png (1024×1024, şeffaf DEĞİL), splash.png (2732×2732 önerilir)
npx @capacitor/assets generate --iconBackgroundColor '#000000' --splashBackgroundColor '#000000'
```

Bu, tüm iOS/Android boyutlarını otomatik üretip yerleştirir. Sonra `npx cap sync`.

---

## 4) Push bildirim (Firebase / FCM) — opsiyonel ama önerilir

Kod tarafı **hazır**: kullanıcı giriş yapınca cihaz `/api/app/devices/register`'e kaydolur
(`frontend/src/lib/native.js`), backend FCM ile gönderir (`backend/routes/admin_mobile.py`).
Eksik olan sadece Firebase yapılandırması:

1. [Firebase Console](https://console.firebase.google.com) → yeni proje (veya mevcut).
2. **Android app** ekle → paket adı `tr.com.facette.app` → `google-services.json` indir →
   `mobile-customer/android/app/google-services.json` içine koy.
3. **iOS app** ekle → bundle `tr.com.facette.app` → `GoogleService-Info.plist` indir →
   Xcode'da `App` hedefine sürükle.
4. iOS için **APNs Auth Key** (.p8) oluştur (Apple Developer → Keys) → Firebase → Project
   Settings → Cloud Messaging → Apple app → APNs key yükle.
5. Backend'e **sunucu anahtarı**: Railway env → `FCM_SERVER_KEY` = Firebase → Project Settings
   → Cloud Messaging → "Server key" (veya yeni sürümde HTTP v1 için servis hesabı — koddaki
   `admin_mobile.py` legacy `key=` başlığı kullanıyor; legacy Server Key'i açman gerekebilir).

> Push'u ilk sürümde atlayabilirsin; uygulama push olmadan da tam çalışır. Sonra ekleyebilirsin.

---

## 5) Ödeme (iyzico 3DS) — önemli not

Ödeme, storefront'un mevcut iyzico 3DS akışıdır ve WebView içinde çalışır. 3DS banka
doğrulaması bir HTML formu/redirect olduğundan Capacitor WebView'de sorunsuz açılır.
**Kontrol et:** ilk test siparişinde 3DS ekranının açılıp döndüğünü ve `/order-success`'e
ulaştığını doğrula. Banka sayfası harici bir şemaya atlarsa (nadiren) `@capacitor/browser`
ile sistem tarayıcısında açman gerekebilir — gerekirse söyle, ekleyeyim.

---

## 6) iOS — derleme & App Store

```bash
cd mobile-customer
npm run ship          # frontend build:customer + cap sync
npx cap open ios      # Xcode açılır
```

Xcode'da:
1. Üstte cihaz olarak **Any iOS Device (arm64)** seç.
2. **Product → Archive**.
3. Archive bitince **Distribute App → App Store Connect → Upload**.
4. [App Store Connect](https://appstoreconnect.apple.com) → **+ Yeni App** → bundle
   `tr.com.facette.app` → adı "Facette".
5. **TestFlight**'ta önce kendin test et (build işlenmesi ~15 dk).
6. App bilgileri: ekran görüntüleri (6.7" ve 5.5" zorunlu), açıklama, kategori (Shopping),
   gizlilik politikası URL'i (facette.com.tr/gizlilik), yaş sınırı.
7. **Submit for Review.**

> **Apple 4.2 (minimum işlevsellik) uyarısı:** Apple, "sadece web sitesini saran" uygulamaları
> reddedebilir. Bunu geçmek için app'in native değeri olmalı — **push bildirimleri**, hızlı
> açılış, native gezinme (donanım geri tuşu, splash) bunu karşılar. Red gelirse: push'u aktif
> et, "mağaza + kampanya bildirimleri + hızlı sipariş takibi" gibi native faydaları review
> notuna yaz.

---

## 7) Android — derleme & Play Store

```bash
cd mobile-customer
npm run ship
npx cap open android   # Android Studio açılır
```

Android Studio'da:
1. **Build → Generate Signed Bundle / APK → Android App Bundle (.aab)**.
2. İlk kez: yeni **keystore** oluştur ve **GÜVENLE SAKLA** (kaybedersen uygulamayı bir daha
   güncelleyemezsin). Şifreleri not al.
3. Çıkan `.aab`'yi [Play Console](https://play.google.com/console) → yeni uygulama → **Internal
   testing** track'ine yükle → kendi hesabınla test et.
4. Store listing: kısa/uzun açıklama, ikon, feature graphic (1024×500), ekran görüntüleri,
   kategori (Shopping), gizlilik politikası, **Data safety** formu (hangi veriyi topluyorsun).
5. Test tamam → **Production**'a yükselt → gönder.

> Play, "Google Play üzerinden dijital ürün" satmıyorsan (sen fiziksel ürün satıyorsun)
> in-app-purchase istemez; iyzico ile fiziksel ödeme serbesttir.

---

## 8) Güncelleme akışı (yeni sürüm çıkarma)

Storefront'ta bir değişiklik yaptığında uygulamayı yenilemek için:

```bash
cd mobile-customer
# iOS/Android version+build numarasını artır (adım 2)
npm run ship
npx cap open ios       # Archive → Upload
npx cap open android   # Signed .aab → Play Console
```

**Zorunlu güncelleme:** Kodda hazır bir mekanizma var — `/api/app/version-check` bir
`force_update_required` dönerse uygulama kullanıcıyı mağazaya yönlendirir
(`frontend/src/lib/native.js` → `checkAppVersion`). Backend tarafında minimum sürümü ayarlayıp
kritik bir güncellemeyi zorunlu kılabilirsin.

---

## 9) Sık karşılaşılan sorunlar

- **Beyaz ekranda kalıyor:** `frontend`'i `build:customer` ile derlemeyi unuttun; `cap sync`
  yeniden çalıştır. `REACT_APP_BACKEND_URL` build sırasında `https://api.facette.com.tr` olmalı.
- **API'ye bağlanmıyor:** cleartext kapalı (https zorunlu) — backend'in canlı https olduğundan emin ol.
- **Geri tuşu app'i kapatıyor (Android):** düzeltildi (`setupBackButton`) — kökte değilsen SPA'da geri gider.
- **Çentik/status bar üst üste biniyor:** config'te `overlaysWebView:false`. Yine olursa
  storefront CSS'ine `env(safe-area-inset-top)` ekleyebiliriz — söyle.
- **Push gelmiyor:** google-services.json / GoogleService-Info.plist yerinde mi, `FCM_SERVER_KEY`
  Railway'de tanımlı mı, kullanıcı giriş yapmış mı (cihaz kaydı token gerektiriyor).

---

## Özet — ne hazır, ne sende

**Bu depoda hazır (kod):** customer build bayrağı, native storefront açılışı, Android geri tuşu,
push/deep-link/version-check altyapısı, ayrı `mobile-customer/` Capacitor projesi + config.

**Sende yapılacak (platform):** `npx cap add ios/android`, ikon/splash, Firebase (push),
Xcode/Android Studio ile derleme + imzalama, App Store & Play Console gönderimi.

Takıldığın her adımda söyle — özellikle 3DS WebView davranışı, safe-area CSS'i veya push
yapılandırmasında ek kod gerekirse hemen eklerim.
