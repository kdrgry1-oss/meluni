/**
 * appVersionGate.js — ADM-15: asgari uygulama sürümü politikasının istemci tarafı.
 *
 * - Native uygulama (Capacitor iOS/Android) YALNIZ kendi API'mize giden isteklere
 *   `X-App-Platform`, `X-App-Version`, `X-App-Target` başlıklarını ekler. Web istemcisi hiçbir
 *   şey eklemez (sunucu politikası web'i etkilemez; üçüncü taraf isteklere başlık gitmez).
 * - Sunucu 426 + code=app_update_required dönerse `meluni:app-update-required` olayı yayılır;
 *   <AppUpdateRequired /> engelleyici "güncelleme gerekli" ekranını gösterir.
 *
 * Sürüm asgari değeri İşletme Kuralları'ndan gelir (app.min_version_admin / _customer).
 */

export const APP_UPDATE_EVENT = "meluni:app-update-required";
export const APP_UPDATE_CODE = "app_update_required";

export function isOurApiUrl(url, apiBase) {
  const u = String(url || "");
  return Boolean((apiBase && u.startsWith(apiBase)) || u.startsWith("/api/"));
}

export function isAppUpdateRequiredError(error) {
  const r = error && error.response;
  if (!r || r.status !== 426) return false;
  const code = r.data && r.data.code;
  return !code || code === APP_UPDATE_CODE;
}

function _setHeader(cfg, name, value) {
  const h = cfg.headers || {};
  if (typeof h.set === "function") h.set(name, value);
  else h[name] = value;
  cfg.headers = h;
}

/**
 * Native istemcide sürüm başlıklarını ekleyen istek interceptor'ı kurar.
 * getVersion: () => Promise<string> (native ikili sürümü; çözülemezse "" → sunucu "eski" sayar).
 * @returns {Function} kaldırma fonksiyonu
 */
export function installAppVersionHeaders(axiosInstance, { platform, appTarget, getVersion, apiBase }) {
  const reqIc = axiosInstance && axiosInstance.interceptors && axiosInstance.interceptors.request;
  if (!reqIc || typeof reqIc.use !== "function") return () => {};
  const id = reqIc.use(async (cfg) => {
    try {
      if (cfg && isOurApiUrl(cfg.url, apiBase)) {
        const version = String((await getVersion()) || "");
        _setHeader(cfg, "X-App-Platform", String(platform || ""));
        _setHeader(cfg, "X-App-Target", String(appTarget || ""));
        if (version) _setHeader(cfg, "X-App-Version", version);
      }
    } catch (e) { /* başlıksız devam — sunucu sürümsüz native isteği "eski" sayar */ }
    return cfg;
  });
  return () => { try { reqIc.eject(id); } catch (e) { /* yoksay */ } };
}

/** 426 yanıtında APP_UPDATE_EVENT yayan yanıt interceptor'ı. @returns {Function} kaldırma */
export function installUpdateRequiredInterceptor(axiosInstance, target = typeof window !== "undefined" ? window : null) {
  const respIc = axiosInstance && axiosInstance.interceptors && axiosInstance.interceptors.response;
  if (!respIc || typeof respIc.use !== "function") return () => {};
  const id = respIc.use((r) => r, (error) => {
    try {
      if (isAppUpdateRequiredError(error) && target && typeof target.dispatchEvent === "function") {
        const d = (error.response && error.response.data) || {};
        target.dispatchEvent(new CustomEvent(APP_UPDATE_EVENT, {
          detail: {
            minVersion: String(d.min_version || ""),
            app: d.app === "admin" ? "admin" : "customer",
            storeUrl: /^https:\/\//i.test(String(d.store_url || "")) ? String(d.store_url) : "",
          },
        }));
      }
    } catch (e) { /* yoksay */ }
    return Promise.reject(error);
  });
  return () => { try { respIc.eject(id); } catch (e) { /* yoksay */ } };
}

/** Promise'i süre sınırıyla çözer; süre dolarsa fallback döner. */
export function withTimeout(promise, ms, fallback) {
  return new Promise((resolve) => {
    const t = setTimeout(() => resolve(fallback), ms);
    Promise.resolve(promise).then(
      (v) => { clearTimeout(t); resolve(v); },
      () => { clearTimeout(t); resolve(fallback); },
    );
  });
}
