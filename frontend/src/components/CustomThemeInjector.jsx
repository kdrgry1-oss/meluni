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
      document.getElementById("facette-custom-css")?.remove();
      document.getElementById("facette-custom-js")?.remove();
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
      if (css && !document.getElementById("facette-custom-css")) {
        const st = document.createElement("style");
        st.id = "facette-custom-css";
        st.textContent = css;
        document.head.appendChild(st);
      }
      if (js && !document.getElementById("facette-custom-js")) {
        try {
          const sc = document.createElement("script");
          sc.id = "facette-custom-js";
          sc.textContent = js;
          document.body.appendChild(sc);
        } catch (e) { /* tema JS hatası mağazayı kırmasın */ }
      }
    }).catch(() => {});
    return () => { alive = false; };
  }, [isAdmin]);

  return null;
}
