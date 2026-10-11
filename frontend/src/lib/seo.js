/**
 * seo.js — İstemci-taraflı per-sayfa SEO meta yönetimi (Googlebot JS render eder).
 *
 * Edge middleware (functions/_middleware.js) ilk HTML'e meta bassa da basmasa da,
 * bu yardımcı SPA gezinmesinde <title>/<meta description>/<link canonical>/OG'yi
 * gerçek sayfa değerine günceller. Eskiden HER sayfa ana sayfanın meta'sını taşıyordu
 * (canonical=ana sayfa → ürün/kategori indekslenmiyordu).
 *
 * Savunmacı: DOM yoksa / hata olursa hiçbir şey yapmaz. Yönettiği etiketleri
 * data-seo-client="1" ile işaretler.
 */

function _setMeta(selectorAttr, key, content) {
  if (typeof document === "undefined") return;
  const sel = `meta[${selectorAttr}="${key}"]`;
  let el = document.head.querySelector(sel);
  if (!content) {
    // A previous SPA route must not leak its metadata into a page with no fact.
    if (el?.getAttribute("data-seo-client") === "1") el.remove();
    else if (el) el.setAttribute("content", "");
    return;
  }
  if (!el) {
    el = document.createElement("meta");
    el.setAttribute(selectorAttr, key);
    el.setAttribute("data-seo-client", "1");
    document.head.appendChild(el);
  }
  el.setAttribute("content", content);
}

function _setCanonical(href) {
  if (typeof document === "undefined" || !href) return;
  let el = document.head.querySelector('link[rel="canonical"]');
  if (!el) {
    el = document.createElement("link");
    el.setAttribute("rel", "canonical");
    document.head.appendChild(el);
  }
  el.setAttribute("href", href);
}

function _origin() {
  try {
    return (window.location && window.location.origin) || "";
  } catch (_) {
    return "";
  }
}

/**
 * setPageSeo({ title, description, canonical, ogImage, ogType })
 * Verilen alanlarla sayfa meta'sını günceller. canonical mutlak veya path olabilir.
 */
export function setPageSeo(opts = {}) {
  if (typeof document === "undefined") return;
  try {
    const { title, description, canonical, ogImage, ogType, robots } = opts;
    if (title) {
      document.title = title;
      _setMeta("property", "og:title", title);
    }
    _setMeta("name", "description", description || "");
    _setMeta("property", "og:description", description || "");
    let canon = canonical;
    if (canon && !/^https?:\/\//i.test(canon)) canon = _origin() + (canon.startsWith("/") ? "" : "/") + canon;
    if (canon) {
      _setCanonical(canon);
      _setMeta("property", "og:url", canon);
    }
    _setMeta("property", "og:image", ogImage || "");
    if (ogType) _setMeta("property", "og:type", ogType);
    if (robots) _setMeta("name", "robots", robots);
  } catch (_) {
    /* sessiz */
  }
}

/** Apply the same canonical runtime result used by the edge renderer. */
export async function applyRuntimeSeo(path, fallback, { signal } = {}) {
  try {
    const base = process.env.REACT_APP_BACKEND_URL || "";
    const response = await fetch(`${base}/api/seo/page-meta?path=${encodeURIComponent(path)}`, { signal });
    if (!response.ok) throw new Error("runtime SEO unavailable");
    const meta = await response.json();
    if (signal?.aborted) return null;
    if (!meta?.found) throw new Error("runtime SEO missing");
    setPageSeo({
      title: meta.title, description: meta.description, canonical: meta.canonical,
      ogImage: meta.og_image, ogType: meta.og_type, robots: meta.robots,
    });
    _setMeta("name", "twitter:title", meta.title || "");
    _setMeta("name", "twitter:description", meta.description || "");
    _setMeta("name", "twitter:image", meta.og_image || "");
    if (typeof document !== "undefined") {
      // Edge JSON-LD belongs to the initial URL. SPA navigation must replace
      // it, otherwise a second product can inherit the first product schema.
      document.querySelectorAll(
        'script[data-seo="runtime"], script[type="application/ld+json"][data-seo="edge"]'
      ).forEach((el) => el.remove());
      (Array.isArray(meta.jsonld) ? meta.jsonld : []).forEach((value) => {
        const tag = document.createElement("script");
        tag.type = "application/ld+json";
        tag.setAttribute("data-seo", "runtime");
        tag.text = JSON.stringify(value).replace(/</g, "\\u003c");
        document.head.appendChild(tag);
      });
    }
    return meta;
  } catch (_) {
    if (!signal?.aborted && typeof fallback === "function") fallback();
    return null;
  }
}

/** Ürün/kategori sayfasından çıkınca canonical'ı ana sayfaya döndürmek yerine,
 *  gezinmede her sayfa kendi setPageSeo'sunu çağırdığı için reset gerekmez.
 *  Yalnız ana sayfa/statikler için varsayılana döndürmek istenirse kullanılır. */
export function resetPageSeoToHome(defaults = {}) {
  setPageSeo({
    title: defaults.title || "",
    description: defaults.description || "",
    canonical: _origin() + "/",
    ogType: "website",
  });
}

function _clean(s, limit = 160) {
  const t = String(s || "").replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim();
  return t.length > limit ? t.slice(0, limit).trim() + "…" : t;
}

/** Ürün nesnesinden meta üretip uygular. */
export function setProductSeo(product, brand = "") {
  if (!product) return;
  const slug = product.slug || product.id;
  const title = (product.meta_title || `${product.name || ""} | ${brand}`).trim();
  const description = _clean(
    product.meta_description || product.seo_description || product.description || product.name || ""
  );
  let ogImage;
  const imgs = Array.isArray(product.images) ? product.images : [];
  const first = imgs.find((im) => (typeof im === "string" ? im : im && (im.url || im.src || im.image)));
  if (first) ogImage = typeof first === "string" ? first : first.url || first.src || first.image;
  setPageSeo({
    title,
    description,
    canonical: `/urun/${slug}`,
    ogImage,
    ogType: "product",
  });
}

/** Kategori adı/slug'ından meta üretip uygular. */
export function setCategorySeo(name, slug, brand = "", description) {
  if (!name && !slug) return;
  const title = `${name || slug} | ${brand}`;
  const desc = _clean(description || name || slug || "");
  setPageSeo({ title, description: desc, canonical: `/kategori/${slug || ""}`, ogType: "website" });
}
