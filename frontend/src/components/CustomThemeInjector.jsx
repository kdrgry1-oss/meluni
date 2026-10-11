/**
 * CustomThemeInjector.jsx — Özel Tema (CSS/JS) enjeksiyonu — GÜVENLİ.
 * ---------------------------------------------------------------------------
 * - CSS ve JS YALNIZCA mağaza (storefront) sayfalarında yüklenir.
 * - /admin altında HİÇBİR ŞEY enjekte edilmez → tenant kodu admin panelini,
 *   JWT token'ını veya oturumunu ASLA etkileyemez (en kritik güvenlik sınırı).
 * - Özel JS ödeme / hesap / giriş-şifre / sipariş-sonucu sayfalarında ÇALIŞTIRILMAZ
 *   (kart, parola ve kişisel veri girilen ekranlar). CSS bu sayfalarda da uygulanır.
 * - SPA'da bir kez çalışmış JS kaldırılamaz: JS bu belgede çalıştıktan sonra hassas
 *   bir sayfaya (veya /admin'e) geçilirse sayfa temiz bir belgeyle yeniden yüklenir.
 * - JS yalnız admin panelinde 'js_enabled' açıksa sunucudan gelir (varsayılan KAPALI).
 * - Savunmacı: hata / boş → hiçbir şey yapmaz.
 */
import { useEffect, useRef } from "react";
import { useLocation } from "react-router-dom";
import axios from "axios";

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;

// Özel JS'in ÇALIŞMAYACAĞI vitrin yolları (tam eşleşme veya alt yol).
const JS_BLOCKED_PATHS = [
  "/odeme", "/odeme-bildirimi", "/hesabim", "/giris", "/sifremi-unuttum", "/sifre-sifirla",
  "/order-success", "/siparis-tamamlandi", "/iade", "/iade-islemleri",
];

const isJsBlockedPath = (p) => JS_BLOCKED_PATHS.some((x) => p === x || p.startsWith(`${x}/`));

const jsHasRun = () => {
  try { return window.__MELUNI_CUSTOM_JS_RAN__ === true; } catch (_) { return false; }
};

export default function CustomThemeInjector() {
  const location = useLocation();
  const themeReq = useRef(null);
  const path = location.pathname || "";
  const isAdmin = path.startsWith("/admin");
  const jsBlocked = isAdmin || isJsBlockedPath(path);

  useEffect(() => {
    if (jsBlocked && jsHasRun()) {
      // Özel JS bu belgede zaten çalıştı; hassas sayfaya temiz belgeyle gir.
      window.location.reload();
      return undefined;
    }
    if (isAdmin) {
      // Admin'e girildiyse enjekte edilmiş tema kodunu KALDIR (güvenlik + admin UI bozulmasın).
      document.getElementById("meluni-custom-css")?.remove();
      document.getElementById("meluni-custom-js")?.remove();
      themeReq.current = null; // vitrine dönüşte güncel temayı yeniden çek
      return undefined;
    }
    let alive = true;
    if (!themeReq.current) {
      themeReq.current = axios.get(`${API}/custom-theme`).then((r) => r.data || {}).catch(() => ({}));
    }
    themeReq.current.then((data) => {
      if (!alive) return;
      const css = data.custom_css || "";
      const js = data.custom_js || "";
      if (css && !document.getElementById("meluni-custom-css")) {
        const st = document.createElement("style");
        st.id = "meluni-custom-css";
        st.textContent = css;
        document.head.appendChild(st);
      }
      if (js && !jsBlocked && !jsHasRun() && !document.getElementById("meluni-custom-js")) {
        try {
          window.__MELUNI_CUSTOM_JS_RAN__ = true;
          const sc = document.createElement("script");
          sc.id = "meluni-custom-js";
          sc.textContent = js;
          document.body.appendChild(sc);
        } catch (_) { /* tema JS hatası mağazayı kırmasın */ }
      }
    });
    return () => { alive = false; };
  }, [isAdmin, jsBlocked]);

  // ---- AKTİF TEMANIN RENK/TİPOGRAFİSİ ----
  // Tema seçimi bugüne dek yalnız /tema önizlemesini etkiliyordu; gerçek mağaza
  // (/, /kategori, /urun …) temadan habersizdi, yani "tema değiştirmek" vitrini
  // değiştirmiyordu. Aktif temanın belirteçleri artık mağaza kökünde CSS
  // değişkenine yazılıyor ve gövde/başlık/ayraç bunları kullanıyor.
  // Admin'e ASLA uygulanmaz (aynı güvenlik sınırı).
  useEffect(() => {
    const ID = "meluni-tema-belirtec";
    if (isAdmin) {
      document.getElementById(ID)?.remove();
      document.getElementById("meluni-tema-font")?.remove();
      return undefined;
    }
    let alive = true;
    axios.get(`${API}/storefront/themes/active`).then((r) => {
      if (!alive) return;
      const s = r.data?.settings || {};
      const t = s.tokens || {};
      if (!Object.keys(t).length) return;   // belirteci olmayan tema → dokunma

      if (s.font_url && !document.getElementById("meluni-tema-font")) {
        const link = document.createElement("link");
        link.id = "meluni-tema-font";
        link.rel = "stylesheet";
        link.href = s.font_url;
        document.head.appendChild(link);
      }

      const satir = (ad, deger) => (deger ? `  --t-${ad}: ${deger};\n` : "");
      const kok =
        ":root {\n" +
        satir("ink", t.ink) + satir("paper", t.paper) + satir("line", t.line) +
        satir("muted", t.muted) + satir("accent", t.accent) +
        satir("sans", t.sans) + satir("display", t.display) +
        satir("wordmark", t.wordmark) + satir("radius", t.radius) +
        satir("wm-track", t.wordmarkTrack) +
        // Tailwind'in köşe yarıçapı zaten --radius'a bağlı (tailwind.config.js);
        // temanın yarıçapı buraya yazılınca rounded-* sınıfları da temaya uyar.
        (t.radius ? `  --radius: ${t.radius};\n` : "") +
        "}\n";
      // KAPSAM DAR TUTULDU: gövde zemini/metni, başlık yazı tipi ve ayraç rengi.
      // Tailwind sınıflarını topluca ezmek (bg-white, text-black…) düzeni bozar;
      // tema kimliği zaten büyük ölçüde tipografi ve zemin/mürekkep ile kurulur.
      const kurallar = `
body { background: var(--t-paper, #fff); color: var(--t-ink, #111); font-family: var(--t-sans, inherit); }
h1, h2, h3, h4, h5, h6 { font-family: var(--t-display, var(--t-sans, inherit)); }
hr { border-color: var(--t-line, #e5e7eb); }
`;
      const st = document.getElementById(ID) || document.createElement("style");
      st.id = ID;
      st.textContent = kok + kurallar;
      if (!st.parentNode) document.head.appendChild(st);
    }).catch(() => {});
    return () => { alive = false; };
  }, [isAdmin]);

  return null;
}
