/**
 * CustomThemeInjector.jsx — Özel Tema (CSS/JS) enjeksiyonu — GÜVENLİ.
 * ---------------------------------------------------------------------------
 * - CSS ve JS YALNIZCA mağaza (storefront) sayfalarında yüklenir.
 * - /admin altında HİÇBİR ŞEY enjekte edilmez → tenant kodu admin panelini,
 *   JWT token'ını veya oturumunu ASLA etkileyemez (en kritik güvenlik sınırı).
 * - JS yalnız admin panelinde 'js_enabled' açıksa sunucudan gelir.
 * - Savunmacı: hata / boş → hiçbir şey yapmaz.
 */
import { useEffect, useRef } from "react";
import { useLocation } from "react-router-dom";
import axios from "axios";

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;

export default function CustomThemeInjector() {
  const location = useLocation();
  const injected = useRef(false);
  const isAdmin = (location.pathname || "").startsWith("/admin");

  useEffect(() => {
    if (isAdmin) {
      // Admin'e girildiyse enjekte edilmiş tema kodunu KALDIR (güvenlik + admin UI bozulmasın).
      document.getElementById("meluni-custom-css")?.remove();
      document.getElementById("meluni-custom-js")?.remove();
      injected.current = false;
      return;
    }
    if (injected.current) return;
    injected.current = true;
    let alive = true;
    axios.get(`${API}/custom-theme`).then((r) => {
      if (!alive) return;
      const css = r.data?.custom_css || "";
      const js = r.data?.custom_js || "";
      if (css && !document.getElementById("meluni-custom-css")) {
        const st = document.createElement("style");
        st.id = "meluni-custom-css";
        st.textContent = css;
        document.head.appendChild(st);
      }
      if (js && !document.getElementById("meluni-custom-js")) {
        try {
          const sc = document.createElement("script");
          sc.id = "meluni-custom-js";
          sc.textContent = js;
          document.body.appendChild(sc);
        } catch (e) { /* tema JS hatası mağazayı kırmasın */ }
      }
    }).catch(() => {});
    return () => { alive = false; };
  }, [isAdmin]);

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
