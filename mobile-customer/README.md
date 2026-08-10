# Facette — Müşteri Mobil Uygulaması (Capacitor)

Bu klasör, **müşteri storefront'unu** (facette.com.tr'nin müşteriye görünen tüm sayfaları)
iOS + Android native uygulamasına saran Capacitor kabuğudur. Web tarafı ayrı bir kod tabanı
değildir — `../frontend` build'inin **customer** varyantından gelir.

- **appId:** `tr.com.facette.app` (admin uygulaması `tr.com.facette.admin`'den AYRI)
- **Açılış:** native uygulamada kök `/` → anasayfa (storefront). Admin'e gitmez.
- **Özellikler:** anasayfa, kategoriler, ürün detay, sepet, ödeme (iyzico 3DS), hesabım,
  siparişlerim, iade, sipariş takip, arama, statik sayfalar — hepsi web storefront'la birebir.

## Hızlı başlangıç (yerelde, ilk kez)

```bash
# 1) Web build (müşteri varyantı — native açılışı storefront'a ayarlar)
cd ../frontend && npm run build:customer

# 2) Bu klasörde Capacitor bağımlılıkları + native projeler
cd ../mobile-customer && npm install
npx cap add ios        # sadece Mac'te
npx cap add android

# 3) Web build'i native projelere kopyala
npx cap sync
```

## Her güncellemede

```bash
npm run ship        # = ../frontend build:customer + cap sync
npx cap open ios      # Xcode'da aç → Archive → App Store
npx cap open android  # Android Studio'da aç → Generate Signed Bundle (.aab) → Play Console
```

Ayrıntılı adım adım (hesaplar, Firebase/FCM push, ikon/splash, imzalama, mağaza gönderimi)
için repo kökündeki **`CUSTOMER_APP_GUIDE.md`** dosyasına bak.
