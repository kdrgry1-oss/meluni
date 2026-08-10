/**
 * =============================================================================
 * pixelEvents.js — Backwards-compatible bridge to new dataLayer.js
 * =============================================================================
 * Eski API (trackViewContent, trackAddToCart, trackInitiateCheckout, trackPurchase)
 * geriye uyumlu olacak şekilde, yeni `lib/dataLayer.js` (GA4 schema + CAPI
 * mirror + event_id dedup) üzerinden çalıştırılır.
 *
 * Bu sayede mevcut sayfalardaki import'ları değiştirmeden sunucu taraflı
 * (Conversions API) gönderim devreye girer.
 * =============================================================================
 */
import {
  trackViewItem,
  trackAddToCart as _trackAddToCart,
  trackBeginCheckout,
  trackPurchase as _trackPurchase,
  trackViewItemList,
  trackAddPaymentInfo as _trackAddPaymentInfo,
  trackRemoveFromCart,
  trackSearch as _trackSearch,
  trackViewCart as _trackViewCart,
  trackAddShippingInfo as _trackAddShippingInfo,
} from "../lib/dataLayer";

/**
 * Sepet satırlarını GA4 item dizisine çevirir. Hem ham CartContext şekli
 * (productId, stockCode) hem de zenginleştirilmiş şekli (product_id, list_price,
 * brand, breadcrumb) tolere eder — çağıran ne verirse versin çalışır.
 */
function mapCartItems(items = [], coupon = "") {
  return (items || []).map((i) => {
    const price = Number(i.price) || 0;
    const listPrice = Number(i.list_price ?? i.price) || price;
    return {
      item_id: String(i.product_id || i.productId || i.id || ""),
      item_name: i.name || i.title || "",
      item_brand: i.brand || "FACETTE",
      item_category: i.category || i.categoryName || "",
      item_variant: `${i.size || ""} ${i.color || ""}`.trim(),
      price,
      list_price: listPrice,
      sale_price: Number(i.sale_price ?? i.price) || price,
      discount: Math.max(0, listPrice - price),
      sku: i.sku || i.stockCode || "",
      size: i.size || "",
      color: i.color || "",
      barcode: i.barcode || "",
      quantity: Number(i.quantity) || 1,
      coupon: coupon || "",
    };
  });
}

/** Ürün detay görüntüleme — eski imza (+ Meta katalog varyant id'si) */
export function trackViewContent({ product_id, name, category, price, brand, color, variant_id }) {
  return trackViewItem({
    product: {
      id: product_id, name, category_name: category,
      brand: brand || "FACETTE", color: color || "",
      sale_price: Number(price) || 0, price: Number(price) || 0,
      // Meta content_ids için: seçili bedenin Ticimax varyant id'si (varsa)
      catalog_id: (variant_id != null && variant_id !== "") ? variant_id : undefined,
    },
  });
}

/** Sepete ekleme — eski imza (+ Meta katalog varyant id'si) */
export function trackAddToCart({ product_id, name, category, price, quantity = 1, size, color, variant_id }) {
  return _trackAddToCart({
    product: {
      id: product_id, name, category_name: category,
      sale_price: Number(price) || 0, price: Number(price) || 0,
    },
    variant: (variant_id || size || color)
      ? { id: variant_id, size, color, price: Number(price) || 0 }
      : null,
    quantity,
  });
}

/** Checkout başladı — eski imza + yeni zengin alanlar */
export function trackInitiateCheckout({ total, items = [], coupon = "", discount = 0, shipping_cost = 0, tax = 0 }) {
  const mapped = items.map((i) => ({
    item_id: String(i.product_id || i.productId || i.id || ""),
    item_name: i.name || i.title || "",
    item_brand: i.brand || "FACETTE",
    item_category: i.category || i.categoryName || "",
    item_variant: `${i.size || ""} ${i.color || ""}`.trim(),
    price: Number(i.price) || 0,
    list_price: Number(i.list_price || i.price) || 0,
    sale_price: Number(i.sale_price || i.price) || 0,
    discount: Math.max(0, Number(i.list_price || i.price) - Number(i.price)),
    sku: i.sku || "", size: i.size || "", color: i.color || "",
    quantity: Number(i.quantity) || 1,
    coupon: coupon || "",
  }));
  const originalTotal = mapped.reduce((s, x) => s + x.list_price * x.quantity, 0);
  return trackBeginCheckout({
    items: mapped, value: Number(total) || 0, coupon, discount,
    original_value: originalTotal, shipping: shipping_cost, tax,
  });
}

/** Satın alma tamamlandı — eski imza + zengin alanlar */
export function trackPurchase({ order_id, total, items = [], coupon = "", currency = "TRY",
                                discount = 0, shipping = 0, tax = 0,
                                shipping_tier = "", payment_type = "", user }) {
  const mapped = items.map((i) => ({
    item_id: String(i.product_id || i.productId || i.id || ""),
    item_name: i.name || i.title || "",
    item_brand: i.brand || "FACETTE",
    item_category: i.category || i.categoryName || "",
    item_variant: `${i.size || ""} ${i.color || ""}`.trim(),
    price: Number(i.price) || 0,
    list_price: Number(i.list_price || i.price) || 0,
    sale_price: Number(i.sale_price || i.price) || 0,
    discount: Math.max(0, Number(i.list_price || i.price) - Number(i.price)),
    sku: i.sku || "", size: i.size || "", color: i.color || "",
    quantity: Number(i.quantity) || 1,
    coupon: coupon || "",
  }));
  const originalTotal = mapped.reduce((s, x) => s + x.list_price * x.quantity, 0);
  return _trackPurchase({
    orderNumber: order_id, value: Number(total) || 0,
    items: mapped, coupon, currency, user,
    discount, original_value: originalTotal,
    shipping, tax, shipping_tier, payment_type,
  });
}

// Re-exports — new API kullanmak isteyenler için
export {
  trackViewItem, trackViewItemList,
  trackRemoveFromCart, trackBeginCheckout,
};
export const trackSearch = _trackSearch;

/** Add Payment Info — eski API ile uyumlu, zengin alanları forward'lar */
export function trackAddPaymentInfo(payload) {
  // payload zaten yeni format'ta gelirse direkt forward
  return _trackAddPaymentInfo(payload);
}

/** Sepet görüntüleme (view_cart) — sepet satırlarını (ham veya zengin) alır. */
export function trackViewCart({ total, items = [], coupon = "" }) {
  return _trackViewCart({
    items: mapCartItems(items, coupon),
    value: Number(total) || 0,
    coupon,
  });
}

/** Teslimat bilgisi girildi (add_shipping_info) — sepet satırlarını alır. */
export function trackAddShippingInfo({ total, items = [], coupon = "", shipping_cost = 0, shipping_tier = "" }) {
  return _trackAddShippingInfo({
    items: mapCartItems(items, coupon),
    value: Number(total) || 0,
    coupon,
    shipping: shipping_cost,
    shipping_tier,
  });
}
