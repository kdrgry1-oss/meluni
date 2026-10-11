export const GOOGLE_POPUP_TIMEOUT_MS = 25000;

/**
 * safeInternalPath — giriş sonrası dönüş adresini (`?redirect=`, `?next=`) yalnız AYNI ORIGIN'deki
 * site-içi bir yola sınırlar; değilse `fallback` döner.
 *
 * GÜVENLİK (açık yönlendirme, 2026-10): tarayıcı yolda "\" karakterini "/" sayar ve TAB/CR/LF'yi
 * yutar → "/\evil.com" ve "/<TAB>/evil.com" fiilen "//evil.com" olur. React Router `navigate`
 * bu değerle pushState çağırınca SecurityError alır ve `window.location.assign` ile DIŞ siteye
 * gider (admin oturumu açıkken `/admin/login?next=/admin/../%5Cevil.com` anında yönlendiriyordu).
 * Bu yüzden ters bölü + kontrol karakterleri reddedilir, değer URL olarak çözülüp origin'i ve
 * (verildiyse) kanonik yolun `requiredPrefix` altında kaldığı doğrulanır.
 */
export function safeInternalPath(value, fallback, requiredPrefix = "") {
  if (typeof value !== "string" || !value.startsWith("/") || value.startsWith("//")) {
    return fallback;
  }
  const hasCtl = Array.from(value).some((ch) => {
    const c = ch.charCodeAt(0);
    return c < 0x20 || c === 0x7f;
  });
  if (hasCtl || value.includes("\\")) return fallback;
  try {
    const origin = (typeof window !== "undefined" && window.location && window.location.origin
      && window.location.origin !== "null") ? window.location.origin : "http://localhost";
    const u = new URL(value, origin);
    if (u.origin !== origin) return fallback;
    if (requiredPrefix && u.pathname !== requiredPrefix && !u.pathname.startsWith(`${requiredPrefix}/`)) {
      return fallback;
    }
  } catch {
    return fallback;
  }
  return value;
}

export function safeReturnPath(value, fallback = "/hesabim") {
  return safeInternalPath(value, fallback);
}

/**
 * OAuth `state` (giriş-CSRF koruması) — Facebook yönlendirmeli girişte `state` eskiden SABİT
 * "facebook" idi: saldırgan KENDİ hesabına ait kodla `/giris?code=…&state=facebook` bağlantısı
 * gönderip kurbanı saldırganın hesabına sokabiliyordu (girilen adres/telefon saldırgana kalır).
 * Artık her girişte rastgele değer üretilir, sekmenin sessionStorage'ına yazılır ve dönüşte
 * TEK SEFERLİK olarak karşılaştırılır.
 */
export function createOAuthState(prefix, storage, key) {
  let rnd = "";
  try {
    const a = new Uint8Array(16);
    window.crypto.getRandomValues(a);
    rnd = Array.from(a, (b) => b.toString(16).padStart(2, "0")).join("");
  } catch {
    return "";
  }
  const state = `${prefix}.${rnd}`;
  try { storage.setItem(key, state); } catch { return ""; }
  return state;
}

export function consumeOAuthState(received, storage, key) {
  let expected = "";
  try { expected = storage.getItem(key) || ""; storage.removeItem(key); } catch { expected = ""; }
  return Boolean(expected) && typeof received === "string" && received === expected;
}

export function isEmbeddedWebView(win = typeof window !== "undefined" ? window : undefined) {
  if (!win) return false;
  const ua = win.navigator?.userAgent || "";
  return Boolean(
    win.ReactNativeWebView ||
    win.webkit?.messageHandlers ||
    /; wv\)|\bwv\b|FBAN|FBAV|Instagram|Line\//i.test(ua)
  );
}

export function googleErrorMessage(code) {
  const messages = {
    csrf: "Google giriş doğrulaması güvenlik kontrolünden geçemedi.",
    inactive: "Bu hesap devre dışı.",
    verification: "Google hesabı doğrulanamadı.",
    configuration: "Google ile giriş henüz yapılandırılmamış.",
    access_denied: "Google ile giriş iptal edildi.",
    popup_failed_to_open: "Google penceresi açılamadı.",
    popup_closed: "Google penceresi tamamlanmadan kapatıldı.",
    timeout: "Google yanıtı zamanında alınamadı.",
    invalid_response: "Google'dan geçerli bir yanıt alınamadı.",
    admin_account: "Yönetici hesapları Google ile giriş yapamaz. Lütfen e-posta ve şifrenizle giriş yapın.",
    account_mismatch: "Bu e-posta adresi farklı bir Google hesabına bağlı. Lütfen e-posta ve şifrenizle giriş yapın.",
  };
  return messages[code] || "Google ile giriş tamamlanamadı.";
}

// GIS callback yaşam döngüsünü DOM'dan ayırır; popup/credential/error/timeout yolları
// gerçek Google hesabı olmadan deterministik olarak test edilebilir.
export function createGooglePopupFlow({
  exchangeCredential,
  onSuccess,
  onError,
  setTimer = setTimeout,
  clearTimer = clearTimeout,
  timeoutMs = GOOGLE_POPUP_TIMEOUT_MS,
}) {
  let timer = null;
  let settled = false;

  const clear = () => {
    if (timer !== null) clearTimer(timer);
    timer = null;
  };
  const fail = (code, error) => {
    if (settled) return;
    settled = true;
    clear();
    onError(code, error);
  };

  return {
    begin() {
      settled = false;
      clear();
      timer = setTimer(() => fail("timeout"), timeoutMs);
    },
    async credential(response) {
      if (settled) return;
      const credential = response?.credential;
      if (!credential) return fail("invalid_response");
      clear();
      try {
        const result = await exchangeCredential(credential);
        if (!result?.token) return fail("invalid_response");
        if (settled) return;
        settled = true;
        onSuccess(result);
      } catch (error) {
        fail("verification", error);
      }
    },
    popupError(event) {
      const type = event?.type === "popup_failed_to_open" ? event.type : "popup_closed";
      fail(type, event);
    },
    dispose() {
      settled = true;
      clear();
    },
  };
}
