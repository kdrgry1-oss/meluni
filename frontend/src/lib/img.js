// Görsel optimizasyon yardımcısı — LCP/CLS ve ağ ağırlığını düşürmek için.
// - Ticimax (Cloudflare) görsellerinde cdn-cgi/image resize param'ları kullanılır
//   (width + quality + format=auto → otomatik WebP/AVIF).
// - Kendi sunucumuzdaki /api/files veya /api/upload/files görsellerinde ?w=&q=
//   query param'larıyla backend on-the-fly WebP resize devreye girer.

const TCMX = "static.ticimax.cloud";
// Cloudflare R2 özel domaini — Image Transformations (cdn-cgi/image) ile dinamik
// AVIF/WebP + resize sunar (Ticimax ile aynı yöntem).
// Marka-bağımsız: build sırasında REACT_APP_CDN_HOST ile verilir.
// Boş bırakılırsa R2 özel-domain yolu devre dışı kalır ve URL'ler olduğu gibi geçer.
const R2_CDN = (process.env.REACT_APP_CDN_HOST || "").replace(/^https?:\/\//, "").replace(/\/$/, "");
// Eski r2.dev (transform desteklemez) — kalan referanslar için boyut-eşleme fallback.
const R2_SIZES = [400, 800, 1280, 1920];

export function optimizeImg(url, width = 800, quality = 75) {
  // Boş/geçersiz değerlerde src="" uyarısını ve gereksiz isteği önlemek için undefined döndür
  if (!url || typeof url !== "string") return undefined;

  // Cloudflare R2 özel domain.
  // NOT: Image Transformations ücretsiz kotası (ayda 5.000 benzersiz dönüşüm) dolunca
  // cdn-cgi/image ile gelen TÜM görseller "ERROR 9422" verip boş kalıyordu. Origin
  // nesneleri zaten boyutlandırılmış WebP (-1280 ürün / -1920 pagedesign) olduğundan
  // dönüşümü bypass edip origin URL'yi doğrudan döndürüyoruz → kota harcanmaz, ücret yok.
  if (R2_CDN && url.includes(R2_CDN)) {
    // Host'u sabit yazmak yerine yapılandırılan CDN'den kuruyoruz.
    const esc = R2_CDN.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    const m = url.match(new RegExp(`${esc}/cdn-cgi/image/[^/]+/(.*)$`, "i"));
    return m ? `https://${R2_CDN}/${m[1]}` : url;
  }

  // Eski r2.dev URL'leri (transform yok) → en yakın üretilmiş boyutu seç
  if (url.includes("r2.dev")) {
    const m = url.match(/-(\d+)\.webp(\?.*)?$/i);
    if (m) {
      const target = R2_SIZES.find((s) => s >= width) || R2_SIZES[R2_SIZES.length - 1];
      return url.replace(/-\d+\.webp/i, `-${target}.webp`);
    }
    return url;
  }

  // Ticimax Cloudflare CDN
  if (url.includes(TCMX)) {
    // Varsa mevcut cdn-cgi transform'unu soy → orijinal path'i al
    let path = url;
    const m = url.match(/static\.ticimax\.cloud\/cdn-cgi\/image\/[^/]+\/(.*)$/i);
    if (m) {
      path = `https://${TCMX}/${m[1]}`;
    }
    return path.replace(
      `https://${TCMX}/`,
      `https://${TCMX}/cdn-cgi/image/width=${width},quality=${quality},format=auto/`
    );
  }

  // Kendi sunucumuz (MongoDB'den servis edilen yüklenmiş görseller)
  if (url.startsWith("/api/files/") || url.startsWith("/api/upload/files/")) {
    const sep = url.includes("?") ? "&" : "?";
    return `${url}${sep}w=${width}&q=${quality}`;
  }

  return url;
}

// Bir görselin en-boy oranını (CSS aspectRatio için) döndürür: "w / h"
export function aspectFromDims(dims, fallback = "16 / 9") {
  if (Array.isArray(dims) && dims.length === 2 && dims[0] && dims[1]) {
    return `${dims[0]} / ${dims[1]}`;
  }
  return fallback;
}

/**
 * Vitrin/arama/sepet galerisi: yalnız gerçek ürün görselleri (URL string).
 * Beden tablosu nesneleri ({url, is_size_table:true}) ve video kayıtları atılır; nesne ise url'i alınır.
 * (Beden tablosu galeride ilk sıraya düşünce kartlar "[object Object]" ile boş kalıyordu.)
 */
export function imageUrl(image) {
  if (typeof image === 'string') return image;
  if (!image || typeof image !== 'object') return '';
  const url=image.url || image.src || image.image;
  return typeof url === 'string' ? url : '';
}

export function isSizeTableImage(image) {
  if (image && typeof image === 'object' && image.is_size_table) return true;
  // Yalnız sistemin ürettiği, bilinen tablo dosya adları. Ürün adına göre tahmin yok.
  return /-olcu(?:\d+|-elle|-kredisiz|-oto)?-[0-9a-f]{6,}\./i.test(imageUrl(image));
}

export function galleryImages(product) {
  const list = Array.isArray(product?.images) ? product.images : [];
  const out = [];
  for (const im of list) {
    if (!im || isSizeTableImage(im) || (typeof im === 'object' && im.is_video)) continue;
    const url=imageUrl(im);
    if (url && !out.includes(url)) out.push(url);
  }
  if (!out.length && typeof product?.image === 'string' && product.image && !isSizeTableImage(product.image)) out.push(product.image);
  return out;
}

export function firstImage(product) {
  return galleryImages(product)[0] || '';
}
