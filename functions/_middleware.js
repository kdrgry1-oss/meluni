/**
 * FACETTE — Edge SEO Middleware  (Cloudflare Pages Function)
 * ----------------------------------------------------------------------------
 * AMAÇ:
 *   Ürün (/urun/<slug>) ve kategori (/kategori/<slug>) sayfalarında BOT'a
 *   (Facebook / Instagram / WhatsApp / Twitter scraper'ları + Google) sayfaya
 *   ÖZEL <title>, meta description, canonical, Open Graph / Twitter ve JSON-LD
 *   bas. Bu scraper'lar JS ÇALIŞTIRMAZ; React'in client-side enjekte ettiği
 *   etiketleri göremez → bu yüzden reklam linki paylaşılınca hep ANASAYFA kartı
 *   çıkıyor. Bu fonksiyon etiketleri EDGE'de (HTML servis edilirken) basar.
 *
 * GÜVENLİ: React koduna DOKUNMAZ. Yalnızca mevcut index.html etiketlerini
 *   günceller. API'den veri gelmezse / hata olursa sayfayı OLDUĞU GİBİ geçirir
 *   — hiçbir koşulda sayfayı kırmaz. Anasayfa ve diğer route'lar dokunulmaz
 *   (onların statik OG'si zaten doğru).
 * ----------------------------------------------------------------------------
 */

const CONFIG = {
  API: "https://api.facette.com.tr/api",
  ORIGIN: "https://facette.com.tr",
  SITE_NAME: "FACETTE",
  DEFAULT_IMAGE: "https://facette.com.tr/og-image.jpg",
  PRODUCT_PREFIX: "/urun/",
  CATEGORY_PREFIX: "/kategori/",
};

// Y27: Gerçek mağaza rotaları KÖK seviyededir (/<slug>) — ürün ve kategori aynı seviyede.
// Middleware yalnızca /urun/ ve /kategori/ öneklerini eşliyordu → adres çubuğundan
// kopyalanan kök-slug linki hiç SEO almıyor, paylaşımda jenerik anasayfa kartı çıkıyordu.
// Bu statik/işlevsel kök yolları ürün/kategori denemesinden HARİÇ tutarız.
const RESERVED_ROOTS = new Set([
  "", "sepet", "odeme", "checkout", "cart", "hesap", "account", "giris", "login",
  "kayit", "register", "sifremi-unuttum", "favoriler", "kaydedilenler", "siparis-takip",
  "iletisim", "hakkimizda", "kurumsal", "sss", "kvkk", "gizlilik", "gizlilik-politikasi",
  "iade", "iade-degisim", "kargo", "kargo-takip", "teslimat", "blog", "arama", "search",
  "admin", "odeme-bildirimi", "urun", "kategori", "kategoriler", "urunler", "sepetim",
  "mesafeli-satis", "cerez-politikasi", "sartlar", "sozlesme",
]);

export async function onRequest(context) {
  const { request, next } = context;

  let res;
  try {
    res = await next();
  } catch (e) {
    return new Response("Upstream error", { status: 502 });
  }

  try {
    const ct = res.headers.get("content-type") || "";
    if (!ct.includes("text/html")) return res; // yalnızca HTML; asset'lere dokunma

    const url = new URL(request.url);
    const seo = await buildSeo(url);
    if (!seo) return res; // hedef route değil veya veri yok → statik kal

    return transform(res, seo);
  } catch (e) {
    return res; // her ihtimale karşı: sayfayı dokunmadan geçir
  }
}

async function buildSeo(url) {
  const path = decodeURIComponent(url.pathname.replace(/\/+$/, "")) || "/";

  // Eski önekli yollar (geriye uyum) — canonical öneki korur.
  if (path.startsWith(CONFIG.PRODUCT_PREFIX)) {
    const slug = path.slice(CONFIG.PRODUCT_PREFIX.length).split("/")[0];
    return slug ? productSeo(slug, CONFIG.ORIGIN + CONFIG.PRODUCT_PREFIX + slug) : null;
  }
  if (path.startsWith(CONFIG.CATEGORY_PREFIX)) {
    const slug = path.slice(CONFIG.CATEGORY_PREFIX.length).split("/")[0];
    return slug ? categorySeo(slug, CONFIG.ORIGIN + CONFIG.CATEGORY_PREFIX + slug) : null;
  }

  // Y27: Kök-seviye tek segmentli yol (/<slug>) — gerçek ürün/kategori URL'leri.
  const segments = path.replace(/^\/+/, "").split("/");
  if (segments.length === 1) {
    const slug = segments[0];
    if (!slug || RESERVED_ROOTS.has(slug.toLowerCase()) || slug.includes(".")) return null;
    const canonical = CONFIG.ORIGIN + "/" + slug;
    // Önce ürün dene; bulunamazsa kategori dene.
    const prod = await productSeo(slug, canonical);
    if (prod) return prod;
    return await categorySeo(slug, canonical);
  }
  return null;
}

async function apiGet(pathname) {
  const r = await fetch(CONFIG.API + pathname, {
    headers: { accept: "application/json" },
    cf: { cacheTtl: 300, cacheEverything: true }, // API yanıtını edge'de 5 dk cache'le
  });
  if (!r.ok) return null;
  return r.json();
}

async function productSeo(slug, canonical) {
  const j = await apiGet("/products/" + encodeURIComponent(slug));
  const p = j && (j.data || j.product || j);
  if (!p || !p.name) return null;

  canonical = canonical || (CONFIG.ORIGIN + CONFIG.PRODUCT_PREFIX + slug);
  const price = p.sale_price && p.sale_price > 0 ? p.sale_price : p.price;
  const image = abs(firstImage(p)) || CONFIG.DEFAULT_IMAGE;
  // Y14 (edge): varyant-farkında stok — varyantlı üründe parent `stock` bayat olabilir.
  const inStock = Array.isArray(p.variants) && p.variants.length > 0
    ? p.variants.some((v) => Number(v.stock || 0) > 0)
    : (p.in_stock !== false && (p.stock == null || Number(p.stock) > 0));

  const productLd = {
    "@context": "https://schema.org/",
    "@type": "Product",
    name: p.name,
    image: imageList(p),
    description: stripHtml(p.description || p.name),
    sku: String(p.barcode || p.stock_code || p.id || slug),
    brand: p.brand ? { "@type": "Brand", name: p.brand } : undefined,
    offers: {
      "@type": "Offer",
      url: canonical,
      priceCurrency: "TRY",
      price: price != null ? String(price) : undefined,
      availability: inStock
        ? "https://schema.org/InStock"
        : "https://schema.org/OutOfStock",
    },
  };

  const crumbs = [{ name: "Ana Sayfa", item: CONFIG.ORIGIN + "/" }];
  if (p.category_name || p.category_slug) {
    crumbs.push({
      name: p.category_name || p.category_slug,
      item: CONFIG.ORIGIN + "/" + (p.category_slug || slugify(p.category_name)),
    });
  }
  crumbs.push({ name: p.name, item: canonical });

  return {
    title: p.name + " | " + CONFIG.SITE_NAME,
    description: clip(stripHtml(p.description || p.name), 155),
    canonical,
    image,
    type: "product",
    price,
    currency: "TRY",
    jsonLd: [productLd, breadcrumb(crumbs)],
  };
}

async function categorySeo(slug, canonical) {
  const j = await apiGet("/categories");
  const arr = Array.isArray(j) ? j : (j && j.categories) || [];
  const cat = arr.find(
    (c) =>
      c &&
      (String(c.slug || "").toLowerCase() === slug.toLowerCase() ||
        slugify(c.name) === slug.toLowerCase())
  );
  // Y27: Kök-slug denemesinde ürün de kategori de değilse SEO üretme (uydurma kategori kartı çıkmasın).
  if (!cat) return null;
  const name = cat.name || titleCase(slug.replace(/-/g, " "));
  canonical = canonical || (CONFIG.ORIGIN + CONFIG.CATEGORY_PREFIX + slug);
  const image = abs(cat.image || cat.image_url) || CONFIG.DEFAULT_IMAGE;

  return {
    title: name + " | " + CONFIG.SITE_NAME,
    description:
      name +
      " kategorisinde yeni sezon FACETTE parçaları. Hızlı kargo, güvenli ödeme, kolay iade.",
    canonical,
    image,
    type: "website",
    jsonLd: [
      breadcrumb([
        { name: "Ana Sayfa", item: CONFIG.ORIGIN + "/" },
        { name, item: canonical },
      ]),
    ],
  };
}

function transform(res, seo) {
  const content = (v) => ({
    element(el) {
      if (v != null && v !== "") el.setAttribute("content", String(v));
    },
  });
  const href = (v) => ({
    element(el) {
      if (v) el.setAttribute("href", String(v));
    },
  });

  return new HTMLRewriter()
    .on("title", {
      element(el) {
        el.setInnerContent(seo.title);
      },
    })
    .on('meta[name="description"]', content(seo.description))
    .on('link[rel="canonical"]', href(seo.canonical))
    .on('meta[property="og:title"]', content(seo.title))
    .on('meta[property="og:description"]', content(seo.description))
    .on('meta[property="og:type"]', content(seo.type))
    .on('meta[property="og:url"]', content(seo.canonical))
    .on('meta[property="og:image"]', content(seo.image))
    .on('meta[name="twitter:title"]', content(seo.title))
    .on('meta[name="twitter:description"]', content(seo.description))
    .on('meta[name="twitter:image"]', content(seo.image))
    .on("head", {
      element(el) {
        if (seo.type === "product" && seo.price != null) {
          el.append(
            '<meta property="product:price:amount" content="' +
              attr(seo.price) +
              '"/>',
            { html: true }
          );
          el.append(
            '<meta property="product:price:currency" content="' +
              attr(seo.currency || "TRY") +
              '"/>',
            { html: true }
          );
        }
        if (seo.jsonLd && seo.jsonLd.length) {
          const data = seo.jsonLd.length === 1 ? seo.jsonLd[0] : seo.jsonLd;
          const json = JSON.stringify(data).replace(/</g, "\\u003c");
          el.append(
            '<script type="application/ld+json" data-seo="edge">' +
              json +
              "</script>",
            { html: true }
          );
        }
      },
    })
    .transform(res);
}

/* ---------------- yardımcılar ---------------- */
function breadcrumb(items) {
  return {
    "@context": "https://schema.org/",
    "@type": "BreadcrumbList",
    itemListElement: items.map((it, i) => ({
      "@type": "ListItem",
      position: i + 1,
      name: it.name,
      item: it.item,
    })),
  };
}
function firstImage(p) {
  if (Array.isArray(p.images) && p.images.length) return p.images[0];
  return p.thumbnail || p.image || null;
}
function imageList(p) {
  if (Array.isArray(p.images) && p.images.length)
    return p.images.map(abs).filter(Boolean);
  const one = abs(firstImage(p));
  return one ? [one] : undefined;
}
function abs(src) {
  if (!src || typeof src !== "string") return null;
  if (/^https?:\/\//i.test(src)) return src;
  return CONFIG.ORIGIN + (src.startsWith("/") ? "" : "/") + src;
}
function stripHtml(s) {
  return String(s || "")
    .replace(/<[^>]*>/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}
function clip(s, n) {
  s = String(s || "");
  return s.length > n ? s.slice(0, n - 1).trimEnd() + "…" : s;
}
function attr(s) {
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/"/g, "&quot;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}
function slugify(s) {
  return String(s || "")
    .toLowerCase()
    .replace(/ğ/g, "g")
    .replace(/ü/g, "u")
    .replace(/ş/g, "s")
    .replace(/ı/g, "i")
    .replace(/ö/g, "o")
    .replace(/ç/g, "c")
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
}
function titleCase(s) {
  return String(s || "").replace(/\b\w/g, (c) => c.toUpperCase());
}
