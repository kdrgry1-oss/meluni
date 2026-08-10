/**
 * =============================================================================
 * SeoManager.jsx — SEO yönlendirme (301/302) + meta override tüketimi
 * =============================================================================
 * DENETİM FIX (#5 + #6): Admin'de tanımlanan 301/302 yönlendirmeler ve per-path
 * meta override'ları (title/description/og_image/noindex) storefront tarafından
 * HİÇ okunmuyordu. Bu bileşen her rota değişiminde:
 *   1) GET /api/seo/resolve-redirect?path=... — found ise hedefe yönlendirir
 *      (301 → replace, 302 → assign). Yalnız admin'de tanımlı from_path'ler
 *      döndüğü için geçerli sayfalar ETKİLENMEZ.
 *   2) GET /api/seo/meta?path=... — found ise <title>/<meta> etiketlerini
 *      react-helmet gerektirmeden doğrudan DOM'a yazar (noindex dahil).
 *
 * Not: SPA client-side; gerçek 301 için public/_redirects (edge) birincildir.
 * Bu, edge kapsamayan dinamik yönlendirmeler için tamamlayıcı fallback'tir.
 * Savunmacı: uç hatası → hiçbir şey yapmaz (mağazayı asla kırmaz).
 * =============================================================================
 */
import { useEffect } from "react";
import { useLocation } from "react-router-dom";
import axios from "axios";

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;

function setMetaTag(attr, key, content) {
  if (typeof document === "undefined") return;
  let el = document.head.querySelector(`meta[${attr}="${key}"]`);
  if (!content) {
    if (el && el.getAttribute("data-seo-managed") === "1") el.remove();
    return;
  }
  if (!el) {
    el = document.createElement("meta");
    el.setAttribute(attr, key);
    el.setAttribute("data-seo-managed", "1");
    document.head.appendChild(el);
  }
  el.setAttribute("content", content);
}

export default function SeoManager() {
  const location = useLocation();

  useEffect(() => {
    const path = location.pathname || "/";
    let cancel = false;

    // 1) Yönlendirme çözümle
    axios
      .get(`${API}/seo/resolve-redirect`, { params: { path } })
      .then((r) => {
        if (cancel || !r?.data?.found || !r.data.to) return;
        const to = r.data.to;
        // Kendine yönlendirme döngüsünü engelle
        if (to === path) return;
        if (String(r.data.status_code) === "302") {
          window.location.assign(to);
        } else {
          window.location.replace(to);
        }
      })
      .catch(() => {});

    // 2) Meta override uygula
    axios
      .get(`${API}/seo/meta`, { params: { path } })
      .then((r) => {
        if (cancel) return;
        const m = r?.data?.found ? r.data.meta : null;
        if (!m) {
          // Bu path için override yok → yönetilen robots etiketini temizle
          setMetaTag("name", "robots", "");
          return;
        }
        if (m.title) document.title = m.title;
        setMetaTag("name", "description", m.description || "");
        setMetaTag("property", "og:image", m.og_image || "");
        setMetaTag("name", "robots", m.noindex ? "noindex,nofollow" : "");
      })
      .catch(() => {});

    return () => { cancel = true; };
  }, [location.pathname]);

  return null;
}
