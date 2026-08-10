/**
 * Attribution tracker — runs once per visit on the storefront.
 *
 * What it does:
 * 1. On first load, captures utm_source/medium/campaign/term/content, gclid, fbclid,
 *    referrer and landing page.
 * 2. Persists a server-assigned `facette_sid` in localStorage.
 * 3. Calls POST /api/attribution/track-visit so sessions accumulate touches over time.
 *
 * Usage: import and invoke once at the top of the app root (e.g. in App.js useEffect).
 * The returned session_id is auto-attached to orders via window.__FACETTE_SID__.
 */
import axios from "axios";

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;
const SID_KEY = "facette_sid";
const UTM_KEY = "facette_attr_first";
const AFF_COOKIE = "facette_aff";
const AFF_TTL_DAYS = 30;

function setCookie(name, value, days) {
  try {
    const d = new Date();
    d.setTime(d.getTime() + days * 24 * 60 * 60 * 1000);
    document.cookie = `${name}=${encodeURIComponent(value)};expires=${d.toUTCString()};path=/;SameSite=Lax`;
  } catch (_) { /* noop */ }
}

function getCookie(name) {
  try {
    const m = document.cookie.match(new RegExp("(?:^|; )" + name + "=([^;]*)"));
    return m ? decodeURIComponent(m[1]) : "";
  } catch (_) { return ""; }
}

/** aff_id (influencer takip) — URL'de varsa 30 günlük çereze yaz, sonra çerezden oku. */
export function captureAffId() {
  try {
    const p = new URLSearchParams(window.location.search);
    const fromUrl = (p.get("aff_id") || p.get("aff") || "").trim();
    if (fromUrl) setCookie(AFF_COOKIE, fromUrl, AFF_TTL_DAYS);
    return fromUrl || getCookie(AFF_COOKIE) || "";
  } catch (_) { return getCookie(AFF_COOKIE) || ""; }
}

export function getAffId() {
  return getCookie(AFF_COOKIE) || "";
}

function readUTM() {
  try {
    const p = new URLSearchParams(window.location.search);
    return {
      utm_source: p.get("utm_source") || "",
      utm_medium: p.get("utm_medium") || "",
      utm_campaign: p.get("utm_campaign") || "",
      utm_term: p.get("utm_term") || "",
      utm_content: p.get("utm_content") || "",
      gclid: p.get("gclid") || "",
      fbclid: p.get("fbclid") || "",
      ttclid: p.get("ttclid") || "",
      aff_id: p.get("aff_id") || p.get("aff") || "",
    };
  } catch (_) { return {}; }
}

// Meta Browser ID / Click ID — attribution session'da da SAKLA ki tarayıcısız (webhook/kurtarma)
// Purchase eventinde de fbp/fbc bulunabilsin (eşleşme kalitesi). _fbc yoksa URL'deki fbclid'den
// Meta'nın beklediği formatta (fb.1.<ts>.<fbclid>) türet — SAHTE üretim değil, gerçek click id.
function readFbIds() {
  try {
    const fbp = getCookie("_fbp") || "";
    let fbc = getCookie("_fbc") || "";
    if (!fbc) {
      const fbclid = new URLSearchParams(window.location.search).get("fbclid") || "";
      if (fbclid) fbc = `fb.1.${Date.now()}.${fbclid}`;
    }
    // TikTok first-party cookie (_ttp) — tarayıcısız (webhook) Purchase'ta da eşleşme için sakla.
    const ttp = getCookie("_ttp") || "";
    return { fbp: (fbp || "").trim(), fbc: (fbc || "").trim(), ttp: (ttp || "").trim() };
  } catch (_) { return { fbp: "", fbc: "", ttp: "" }; }
}

export async function trackVisit() {
  try {
    if (typeof window === "undefined") return;
    const existingSid = localStorage.getItem(SID_KEY) || null;
    const aff_id = captureAffId();
    const utm = readUTM();
    const fbIds = readFbIds();
    const hasNewUtm = !!(utm.utm_source || utm.utm_campaign || utm.gclid || utm.fbclid || utm.ttclid || aff_id);
    const payload = {
      ...utm,
      // Boş göndermeyip mevcut dolu değeri backend'de ezmemek için yalnız doluysa ekle.
      ...(fbIds.fbp ? { fbp: fbIds.fbp } : {}),
      ...(fbIds.fbc ? { fbc: fbIds.fbc } : {}),
      ...(fbIds.ttp ? { ttp: fbIds.ttp } : {}),
      aff_id: aff_id || utm.aff_id || "",
      referrer: document.referrer || "",
      landing_page: window.location.pathname + window.location.search,
      session_id: existingSid,
    };
    const { data } = await axios.post(`${API}/attribution/track-visit`, payload, { timeout: 5000 });
    if (data?.session_id) {
      localStorage.setItem(SID_KEY, data.session_id);
      window.__FACETTE_SID__ = data.session_id;
      if (hasNewUtm) {
        // Persist first-touch for debug/display
        localStorage.setItem(UTM_KEY, JSON.stringify({ ...utm, channel: data.channel, ts: new Date().toISOString() }));
      }
    }
  } catch (_) {
    // Silent – never break storefront over attribution.
  }
}

export function getSessionId() {
  try { return window.__FACETTE_SID__ || localStorage.getItem(SID_KEY) || null; }
  catch (_) { return null; }
}
