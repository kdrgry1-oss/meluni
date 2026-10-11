/**
 * staffDataWipe.js — ADM-15: personel ayrılınca / oturumu iptal edilince cihazdaki
 * personel verisinin TAMAMINI silen tek yardımcı.
 *
 * Ne zaman çağrılır (AuthContext):
 *   - Sunucu 401 + `X-Auth-Code: session_revoked` döndüğünde (hesap pasif/silinmiş,
 *     "tüm cihazlardan çıkış", şifre değişimi, küresel oturum iptali),
 *   - personel oturumu herhangi bir nedenle düştüğünde (süre/boşta kalma),
 *   - personelin açık "Çıkış" / "Tüm cihazlardan çıkış" işleminde.
 *
 * Ne silinir:
 *   - localStorage: token + panel tercihleri/görünüm/filtre önbellekleri (çerez izni HARİÇ),
 *   - sessionStorage: tamamı (rapor asistanı sohbeti, taslaklar, yönlendirme anahtarları),
 *   - Cache Storage (service worker önbellekleri) ve kayıtlı service worker'lar,
 *   - IndexedDB veritabanları (tarayıcı listeleyebiliyorsa),
 *   - Capacitor Preferences (native uygulama) — yalnız rastgele cihaz kimliği (device_id) kalır.
 *
 * Senkron depolama (local/sessionStorage) İLK await'ten önce silinir: sayfa hemen ardından
 * yönlendirilse bile token ve panel verisi cihazda kalmaz. Asenkron kısım süre sınırlıdır.
 */

export const AUTH_CODE_HEADER = "x-auth-code";
export const SESSION_REVOKED_CODE = "session_revoked";

// Kişisel/işletme verisi taşımayan cihaz tercihleri — silinmez (KVKK çerez izni kaydı).
export const KEEP_LOCAL_KEYS = ["meluni_cookie_consent"];
// Capacitor Preferences'ta kalan tek anahtar: rastgele kurulum kimliği (kişisel veri değil).
export const KEEP_PREF_KEYS = ["device_id"];

let _nativePrefs;
function _nativePreferences() {
  if (_nativePrefs !== undefined) return _nativePrefs;
  _nativePrefs = null;
  try {
    // eslint-disable-next-line global-require
    const { Capacitor } = require("@capacitor/core");
    if (Capacitor && Capacitor.isNativePlatform && Capacitor.isNativePlatform()) {
      // eslint-disable-next-line global-require
      _nativePrefs = require("@capacitor/preferences").Preferences || null;
    }
  } catch (e) {
    _nativePrefs = null; // web build / paket yok
  }
  return _nativePrefs;
}

function _safe(fn) {
  try { return fn(); } catch (e) { return undefined; }
}

function defaultEnv() {
  const w = typeof window !== "undefined" ? window : {};
  const n = typeof navigator !== "undefined" ? navigator : {};
  return {
    localStorage: _safe(() => w.localStorage),
    sessionStorage: _safe(() => w.sessionStorage),
    caches: _safe(() => w.caches),
    indexedDB: _safe(() => w.indexedDB),
    serviceWorker: _safe(() => n.serviceWorker),
    preferences: _nativePreferences(),
  };
}

/** axios hatasından makine-okunur oturum kodu (küçük harf) — yoksa "". */
export function authCodeOf(error) {
  const h = error && error.response && error.response.headers;
  if (!h) return "";
  let v;
  try { v = typeof h.get === "function" ? h.get(AUTH_CODE_HEADER) : undefined; } catch (e) { v = undefined; }
  if (v === undefined || v === null) v = h[AUTH_CODE_HEADER] || h["X-Auth-Code"];
  return String(v || "").trim().toLowerCase();
}

export function isSessionRevokedError(error) {
  return Boolean(error && error.response && error.response.status === 401
    && authCodeOf(error) === SESSION_REVOKED_CODE);
}

/**
 * Token bir PERSONEL token'ı mı? (JWT gövdesi imza doğrulamadan okunur — bu bir yetki
 * kararı DEĞİL, yalnız "çıkışta cihazı tam temizle" tetikleyicisidir.)
 */
export function isStaffToken(token) {
  try {
    const part = String(token || "").split(".")[1];
    if (!part) return false;
    const b64 = part.replace(/-/g, "+").replace(/_/g, "/");
    const padded = b64 + "===".slice((b64.length + 3) % 4);
    const p = JSON.parse(atob(padded));
    return Boolean(p && (p.typ === "staff" || p.is_admin === true));
  } catch (e) {
    return false;
  }
}

function _withTimeout(promise, ms) {
  return new Promise((resolve) => {
    const t = setTimeout(resolve, ms);
    Promise.resolve(promise).then(() => { clearTimeout(t); resolve(); }, () => { clearTimeout(t); resolve(); });
  });
}

/**
 * Cihazdaki personel verisini siler. Hiçbir zaman hata fırlatmaz.
 * @param {object} [opts]
 * @param {object} [opts.env]           test için depolama nesneleri (varsayılan: tarayıcı/native)
 * @param {string[]} [opts.keepLocalKeys] ek olarak korunacak localStorage anahtarları
 * @param {number} [opts.timeoutMs=2000] asenkron temizlik için üst süre
 * @returns {Promise<{local:number, session:number, caches:number, idb:number, sw:number, prefs:number}>}
 */
export async function wipeLocalStaffData(opts = {}) {
  const env = opts.env || defaultEnv();
  const keep = new Set([...KEEP_LOCAL_KEYS, ...(opts.keepLocalKeys || [])]);
  const report = { local: 0, session: 0, caches: 0, idb: 0, sw: 0, prefs: 0 };

  // 1) localStorage — SENKRON (ilk await'ten önce)
  _safe(() => {
    const ls = env.localStorage;
    if (!ls) return;
    const keys = [];
    for (let i = 0; i < ls.length; i += 1) keys.push(ls.key(i));
    keys.forEach((k) => {
      if (k === null || k === undefined || keep.has(k)) return;
      _safe(() => { ls.removeItem(k); report.local += 1; });
    });
  });

  // 2) sessionStorage — SENKRON, tamamı
  _safe(() => {
    const ss = env.sessionStorage;
    if (!ss) return;
    report.session = Number(ss.length) || 0;
    ss.clear();
  });

  const tasks = [];

  // 3) Cache Storage
  tasks.push((async () => {
    const c = env.caches;
    if (!c || typeof c.keys !== "function") return;
    const names = await c.keys();
    await Promise.all((names || []).map(async (name) => {
      try { if (await c.delete(name)) report.caches += 1; } catch (e) { /* yoksay */ }
    }));
  })());

  // 4) IndexedDB
  tasks.push((async () => {
    const idb = env.indexedDB;
    if (!idb || typeof idb.databases !== "function") return;
    const dbs = await idb.databases();
    (dbs || []).forEach((d) => {
      if (d && d.name) _safe(() => { idb.deleteDatabase(d.name); report.idb += 1; });
    });
  })());

  // 5) Service worker kayıtları
  tasks.push((async () => {
    const sw = env.serviceWorker;
    if (!sw || typeof sw.getRegistrations !== "function") return;
    const regs = await sw.getRegistrations();
    await Promise.all((regs || []).map(async (r) => {
      try { if (await r.unregister()) report.sw += 1; } catch (e) { /* yoksay */ }
    }));
  })());

  // 6) Capacitor Preferences (native)
  tasks.push((async () => {
    const P = env.preferences;
    if (!P || typeof P.keys !== "function") return;
    const res = await P.keys();
    const keys = (res && res.keys) || [];
    for (const k of keys) {
      if (KEEP_PREF_KEYS.includes(k)) continue;
      try { await P.remove({ key: k }); report.prefs += 1; } catch (e) { /* yoksay */ }
    }
  })());

  const guarded = tasks.map((t) => t.catch(() => undefined));
  await _withTimeout(Promise.all(guarded), Number(opts.timeoutMs) > 0 ? Number(opts.timeoutMs) : 2000);
  return report;
}
