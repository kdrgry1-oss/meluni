/**
 * native.js — Capacitor native bridge stub
 *
 * Capacitor paketleri yüklü değilse (web build), tüm fonksiyonlar no-op
 * davranır. Kullanıcı `yarn add @capacitor/core @capacitor/ios
 * @capacitor/android @capacitor/push-notifications @capacitor/app
 * @capacitor/preferences` çalıştırdığında otomatik aktive olur.
 *
 * Detaylı kurulum: /app/CAPACITOR_DEPLOYMENT_GUIDE.md
 */
import axios from "axios";

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;

// Lazy / safe imports — paketler yüklü değilse undefined dönecek
let Capacitor, PushNotifications, App, Preferences;
try {
  // eslint-disable-next-line global-require
  Capacitor = require("@capacitor/core").Capacitor;
  PushNotifications = require("@capacitor/push-notifications").PushNotifications;
  App = require("@capacitor/app").App;
  Preferences = require("@capacitor/preferences").Preferences;
} catch (e) {
  // Web build — Capacitor henüz kurulmamış. Tüm fonksiyonlar no-op olacak.
}

export const isNative = !!(Capacitor && Capacitor.isNativePlatform && Capacitor.isNativePlatform());
export const platform = Capacitor ? Capacitor.getPlatform() : "web"; // ios | android | web

// HANGİ NATIVE UYGULAMA? build sırasında REACT_APP_APP_TARGET ile belirlenir:
//   "customer" (varsayılan) → native açılış STOREFRONT (Home) — müşteri uygulaması
//   "admin"                 → native açılış /admin — yönetim uygulaması (ayrı appId)
// Web build'de isNative=false olduğundan bu değer yalnız native app'te anlamlıdır.
export const appTarget = (process.env.REACT_APP_APP_TARGET || "customer").toLowerCase();

/* -------------------------------------------------------------------------- */
/*  PUSH NOTIFICATIONS — FCM (Android) + APNs (iOS)                            */
/* -------------------------------------------------------------------------- */
export async function setupPushNotifications() {
  if (!isNative || !PushNotifications) return;

  let perm = await PushNotifications.checkPermissions();
  if (perm.receive === "prompt") {
    perm = await PushNotifications.requestPermissions();
  }
  if (perm.receive !== "granted") return;

  await PushNotifications.register();

  PushNotifications.addListener("registration", async (token) => {
    const userToken = localStorage.getItem("token");
    if (!userToken) return;

    let deviceId = (await Preferences.get({ key: "device_id" })).value;
    if (!deviceId) {
      deviceId = `${platform}-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`;
      await Preferences.set({ key: "device_id", value: deviceId });
    }

    try {
      await axios.post(
        `${API}/app/devices/register`,
        {
          platform,
          device_id: deviceId,
          push_token: token.value,
          app_version: "1.0.0",
        },
        { headers: { Authorization: `Bearer ${userToken}` } }
      );
    } catch (e) {
      console.warn("Device register failed:", e);
    }
  });

  PushNotifications.addListener("pushNotificationActionPerformed", (action) => {
    const data = action.notification.data || {};
    if (data.deep_link) {
      const path = safeDeepLinkPath(data.deep_link);
      if (path) window.location.href = path;
    }
  });
}

/**
 * GÜVENLİK: deep-link / push payload'ından SADECE aynı-origin relatif bir yol üret.
 * `facette://order/5` → `/order/5`. Dış host (evil.com), mutlak URL, javascript:,
 * `//` protokol-relatif veya scheme içeren değerler reddedilir (açık yönlendirme /
 * keyfi navigasyon engeli).
 */
function safeDeepLinkPath(raw) {
  let s = String(raw || "").trim();
  s = s.replace(/^facette:\/\//i, "/");
  // Sadece "/" ile başlayan, "//" olmayan, scheme/backslash içermeyen yollar
  if (!s.startsWith("/")) return null;
  if (s.startsWith("//")) return null;
  if (/[:\\]/.test(s)) return null; // ":" (scheme) veya "\" barındıran değer reddedilir
  if (!/^\/[A-Za-z0-9/_\-?=&.%#]*$/.test(s)) return null;
  return s;
}

/* -------------------------------------------------------------------------- */
/*  VERSION CHECK — force update                                              */
/* -------------------------------------------------------------------------- */
export async function checkAppVersion() {
  if (!isNative || !App) return null;
  const info = await App.getInfo();
  try {
    const res = await axios.get(
      `${API}/app/version-check?platform=${platform}&current_version=${info.version}`
    );
    return res.data;
  } catch {
    return null;
  }
}

/* -------------------------------------------------------------------------- */
/*  DEEP LINKS — facette://order/123                                          */
/* -------------------------------------------------------------------------- */
export function setupDeepLinks() {
  if (!isNative || !App) return;
  App.addListener("appUrlOpen", (event) => {
    const path = safeDeepLinkPath(event.url);
    if (path) window.location.href = path;
  });
}

/* -------------------------------------------------------------------------- */
/*  ANDROID DONANIM GERİ TUŞU                                                  */
/*  Geri gidilebiliyorsa SPA içinde geri; kök/anasayfada uygulamadan çık.     */
/*  (Aksi halde Android'de geri tuşu WebView'i kapatıp app'i öldürüyordu.)    */
/* -------------------------------------------------------------------------- */
export function setupBackButton() {
  if (!isNative || !App || platform !== "android") return;
  App.addListener("backButton", ({ canGoBack }) => {
    const atRoot = window.location.pathname === "/" || window.location.pathname === "";
    if (canGoBack && !atRoot && window.history.length > 1) {
      window.history.back();
    } else {
      // Kök/anasayfada geri → uygulamadan çık (Android standardı).
      try { App.exitApp(); } catch { /* no-op */ }
    }
  });
}

/* -------------------------------------------------------------------------- */
/*  BOOTSTRAP — App.js'den çağrılır                                            */
/* -------------------------------------------------------------------------- */
export async function bootstrapNative() {
  if (!isNative) return { isNative: false };

  setupDeepLinks();
  setupBackButton();
  await setupPushNotifications();
  const versionInfo = await checkAppVersion();

  if (versionInfo?.force_update_required && versionInfo.store_url) {
    // Native blocking alert + redirect
    if (
      window.confirm(
        `Uygulamanın yeni sürümü gerekli (mevcut ${versionInfo.current_version} → ${versionInfo.latest_version}).
Mağazaya yönlendirilmek ister misiniz?`
      )
    ) {
      window.location.href = versionInfo.store_url;
    }
  }

  return { isNative: true, platform, versionInfo };
}
