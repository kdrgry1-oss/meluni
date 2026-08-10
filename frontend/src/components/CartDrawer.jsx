import { Link } from "react-router-dom";
import { useEffect, useState } from "react";
import axios from "axios";
import { useShipping } from "../lib/shipping";
import { X, Plus, Minus, ShoppingBag, Sparkles, Share2 } from "lucide-react";
import { useCart } from "../context/CartContext";
import { priceView, cartLineView } from "../lib/price";
import { trackRemoveFromCart } from "../lib/dataLayer";
import { shareCart } from "../lib/shareCart";

// Çekmece öneri fiyatı — indirim varsa üstü çizili liste + indirimli (tutarlı).
function MiniPrice({ p }) {
  const pv = priceView(p);
  if (pv.hasDiscount) {
    return (
      <p className="text-[10px] tabular-nums">
        <span className="text-black/40 line-through mr-1">{pv.list.toFixed(0)}</span>
        <span className="text-red-600">{pv.display.toFixed(0)} TL</span>
      </p>
    );
  }
  return <p className="text-[10px] tabular-nums text-black/70">{pv.display.toFixed(0)} TL</p>;
}

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;

export default function CartDrawer() {
  const { items, isOpen, setIsOpen, removeItem, updateQuantity, addItem, total, itemCount } = useCart();
  const { shippingFee, freeShippingThreshold } = useShipping();
  const freeShippingLimit = freeShippingThreshold || 0;

  const [suggestions, setSuggestions] = useState([]);
  const [bestsellers, setBestsellers] = useState([]);
  // Kampanya/kupon indirimi — Sepet SAYFASI ile AYNI motor (evaluate). Önceden çekmece
  // indirimi hiç hesaplamıyor, ürünleri tam fiyatla gösteriyordu ("sepete ekleyince ilk fiyat").
  const [promoDiscount, setPromoDiscount] = useState(0);

  // ÜCRETSİZ KARGO EŞİĞİ — sunucunun kararıyla AYNI taban: İNDİRİM SONRASI sepet tutarı
  // (orders.create_order: (_subtotal - _server_discount) >= eşik). Eskiden indirimSİZ ara
  // toplam baz alınıyordu; 3490 TL'lik sepette "510 TL daha" deniyor ama 510 TL'lik ürün
  // eklenince indirim de büyüdüğü için net tutar eşiğin altında kalıp kargo yine ücretli
  // çıkıyordu (müşteriye tutmayan söz). Artık bar ödenecek tutarı baz alır.
  const netTotal = Math.max(0, total - promoDiscount);
  const remaining = freeShippingThreshold != null ? Math.max(0, freeShippingThreshold - netTotal) : 0;
  useEffect(() => {
    if (!isOpen || items.length === 0) { setPromoDiscount(0); return; }
    let cancel = false;
    axios.post(`${API}/coupons/evaluate`, {
      cart_total: total,
      items: items.map((it) => ({ product_id: it.productId, category_id: it.categoryId, price: it.price, qty: it.quantity })),
    })
      .then((r) => { if (!cancel) setPromoDiscount(Number(r.data?.total_discount || 0)); })
      .catch(() => { if (!cancel) setPromoDiscount(0); });
    return () => { cancel = true; };
  }, [isOpen, items, total]);

  // Sepet ürünlerine göre kombin önerisi (cart-suggestions API)
  useEffect(() => {
    if (!isOpen || items.length === 0) return;
    // Y30: Sepet kalemleri camelCase `productId` taşır; `id` ise "productId-variantId" bileşiğidir.
    // Önceden `i.product_id || i.id` bileşik id gönderiyor, hiçbir ürünle eşleşmiyordu. Ayrıca
    // endpoint {items:[...]} döner (suggestions değil).
    const productIds = items.map((i) => i.productId || i.product_id).filter(Boolean);
    if (!productIds.length) return;
    axios.post(`${API}/products/cart-suggestions`, { product_ids: productIds, limit: 4 })
      .then((r) => setSuggestions((r.data?.items || r.data?.suggestions || []).slice(0, 4)))
      .catch(() => setSuggestions([]));
  }, [isOpen, items.length]);

  // Bu ay en çok satan ürünler (sort=popular)
  useEffect(() => {
    if (!isOpen) return;
    if (bestsellers.length > 0) return;
    axios.get(`${API}/products?limit=6&sort=popular`)
      .then((r) => setBestsellers((r.data?.products || []).slice(0, 6)))
      .catch(() => setBestsellers([]));
  }, [isOpen]);

  if (!isOpen) return null;

  return (
    <>
      {/* Backdrop */}
      <div
        className="fixed inset-0 bg-black/50 z-40 animate-fade-in"
        onClick={() => setIsOpen(false)}
      />

      {/* Drawer */}
      <div
        className="fixed inset-y-0 right-0 z-50 w-full sm:w-[400px] bg-white shadow-2xl flex flex-col animate-slide-right"
        data-testid="cart-drawer"
      >
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-black/10">
          <h2 className="text-[11px] tracking-[0.3em] uppercase text-black">
            Sepetim ({itemCount})
          </h2>
          <button
            onClick={() => setIsOpen(false)}
            className="p-2 -mr-2 hover:opacity-60 transition-opacity"
            data-testid="close-cart"
            aria-label="Kapat"
          >
            <X size={18} strokeWidth={1.4} />
          </button>
        </div>

        {/* Free Shipping Progress */}
        {remaining > 0 && items.length > 0 && (
          <div className="px-5 py-3 bg-stone-50 border-b border-black/5">
            <p className="text-[11px] text-center mb-2 text-black/70">
              Ücretsiz kargo için <span className="font-semibold text-emerald-600">{remaining.toFixed(2)} TL</span> daha
            </p>
            <div className="h-[2px] bg-black/10 overflow-hidden">
              <div
                className="h-full bg-black transition-all duration-700 ease-out"
                style={{ width: `${Math.min(100, (netTotal / freeShippingLimit) * 100)}%` }}
              />
            </div>
          </div>
        )}

        {/* Items */}
        <div className="flex-1 overflow-y-auto px-5">
          {items.length === 0 ? (
            <div className="flex flex-col items-center justify-center h-full text-center py-16">
              <ShoppingBag size={36} strokeWidth={1.2} className="text-black/30 mb-4" />
              <p className="text-sm text-black/60 mb-6">Sepetiniz boş</p>
              <button
                onClick={() => setIsOpen(false)}
                className="px-6 h-10 border border-black text-xs uppercase tracking-[0.25em] hover:bg-black hover:text-white transition-colors"
              >
                Alışverişe başla
              </button>
            </div>
          ) : (
            <ul className="divide-y divide-black/10">
              {items.map((item) => (
                <li key={item.id} className="flex gap-4 py-5">
                  <Link
                    to={`/${item.slug || item.productId}`}
                    onClick={() => setIsOpen(false)}
                    className="flex-shrink-0"
                  >
                    <img
                      src={item.image}
                      alt={item.name}
                      className="w-20 h-28 object-cover bg-stone-100 hover:opacity-90 transition-opacity"
                    />
                  </Link>
                  <div className="flex-1 min-w-0">
                    <Link
                      to={`/${item.slug || item.productId}`}
                      onClick={() => setIsOpen(false)}
                      className="block hover:underline"
                    >
                      <h3 className="text-sm font-medium leading-tight line-clamp-2">{item.name}</h3>
                    </Link>
                    <div className="mt-1 space-y-0.5 text-[11px] text-black/55">
                      {item.color && <p>Renk: {item.color}</p>}
                      {item.size && <p>Beden: {item.size}</p>}
                    </div>
                    {(() => {
                      const lv = cartLineView(item);
                      return lv.hasDiscount ? (
                        <div className="mt-2 flex items-center gap-2 flex-wrap">
                          <span className="text-[11px] text-black/40 line-through tabular-nums">{lv.listUnit.toFixed(2)} TL</span>
                          <span className="text-sm font-medium text-red-600 tabular-nums">{lv.unit.toFixed(2)} TL</span>
                          <span className="text-[9px] font-semibold text-emerald-700 bg-emerald-50 border border-emerald-200 px-1.5 py-0.5 rounded">
                            %{lv.discountPct} İNDİRİM
                          </span>
                        </div>
                      ) : (
                        <p className="text-sm font-medium mt-2 tabular-nums">{lv.unit.toFixed(2)} TL</p>
                      );
                    })()}

                    <div className="flex items-center justify-between mt-3">
                      <div className="inline-flex items-center border border-black/15">
                        <button
                          onClick={() => updateQuantity(item.id, item.quantity - 1)}
                          className="p-1.5 hover:bg-black/5 transition-colors disabled:opacity-30"
                          disabled={item.quantity <= 1}
                          data-testid={`decrease-${item.id}`}
                          aria-label="Azalt"
                        >
                          <Minus size={12} />
                        </button>
                        <span className="px-3 text-xs tabular-nums">{item.quantity}</span>
                        <button
                          onClick={() => updateQuantity(item.id, item.quantity + 1)}
                          className="p-1.5 hover:bg-black/5 transition-colors"
                          data-testid={`increase-${item.id}`}
                          aria-label="Arttır"
                        >
                          <Plus size={12} />
                        </button>
                      </div>
                      <button
                        onClick={() => {
                          try {
                            trackRemoveFromCart({
                              product: {
                                id: item.productId || item.product_id || item.id,
                                name: item.name,
                                sale_price: item.price,
                                price: item.price,
                              },
                              variant: { size: item.size, color: item.color, price: item.price },
                              quantity: item.quantity || 1,
                            });
                          } catch (_) { /* silent */ }
                          removeItem(item.id);
                        }}
                        className="text-[11px] text-black/55 hover:text-black underline"
                        data-testid={`remove-${item.id}`}
                      >
                        Kaldır
                      </button>
                    </div>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </div>

        {/* Footer */}
        {items.length > 0 && (
          <>
            {/* NOT (Kadir): "Stilini Tamamla" ve "Bu Ay En Çok Satanlar" önerileri çekmeceden
                KALDIRILDI — çok yer kaplayıp sepetin kendisini gölgeliyordu. (Öneriler Sepet
                sayfasında zaten var.) */}

            <div className="border-t border-black/10 px-5 py-4 space-y-3">
            {(() => {
              // Ürün-seviyesi indirim (sale_price + otomatik kampanya) satırlardan hesaplanır →
              // "Ara Toplam / İndirim / Toplam" kalemlerdeki üstü-çizili görünümle birebir tutarlı.
              const listSum = items.reduce((s, it) => s + cartLineView(it).listUnit * it.quantity, 0);
              const effSum = items.reduce((s, it) => s + cartLineView(it).unit * it.quantity, 0);
              const productDisc = Math.max(0, listSum - effSum);
              // Sepet-seviyesi ekstra indirim (kupon/koşullu kampanya) evaluate'ten gelebilir.
              const extraDisc = Math.max(0, promoDiscount - productDisc);
              const grand = Math.max(0, effSum - extraDisc);
              const anyDisc = productDisc + extraDisc;
              return (
                <>
                  <div className="flex justify-between items-baseline">
                    <span className="text-xs tracking-[0.2em] uppercase text-black/60">Ara Toplam</span>
                    <span className="text-base font-medium tabular-nums">{listSum.toFixed(2)} TL</span>
                  </div>
                  {anyDisc > 0.001 && (
                    <div className="flex justify-between items-baseline text-emerald-700" data-testid="drawer-promo-discount">
                      <span className="text-xs tracking-[0.15em] uppercase">İndirim</span>
                      <span className="text-sm font-medium tabular-nums">-{anyDisc.toFixed(2)} TL</span>
                    </div>
                  )}
                  <div className="flex justify-between items-baseline pt-1 border-t border-black/10">
                    <span className="text-xs tracking-[0.2em] uppercase text-black/70">Toplam</span>
                    <span className="text-base font-semibold tabular-nums">{grand.toFixed(2)} TL</span>
                  </div>
                </>
              );
            })()}
            {freeShippingThreshold != null && remaining <= 0 && (
              <p className="text-[11px] text-emerald-700 text-center">
                Ücretsiz kargo kazandınız
              </p>
            )}
            <Link
              to="/odeme"
              className="flex items-center justify-center w-full h-12 bg-black text-white text-xs uppercase tracking-[0.25em] hover:bg-black/85 transition-colors"
              onClick={() => setIsOpen(false)}
              data-testid="go-to-checkout"
            >
              Ödemeye Geç
            </Link>
            {/* "Sepete Git" yerine "Alışverişe Devam Et" — çekmece zaten sepeti gösteriyor;
                buton yalnız çekmeceyi kapatır → kullanıcı en son olduğu sayfada kalır. */}
            <button
              onClick={() => setIsOpen(false)}
              className="flex items-center justify-center w-full h-11 border border-black text-xs uppercase tracking-[0.25em] hover:bg-black hover:text-white transition-colors"
              data-testid="continue-shopping"
            >
              Alışverişe Devam Et
            </button>
            {/* Sepeti Paylaş — link üretip panoya kopyalar / mobil paylaşım menüsü açar */}
            <button
              onClick={() => shareCart(items)}
              className="flex items-center justify-center gap-1.5 w-full pt-1 text-[11px] uppercase tracking-[0.2em] text-black/55 hover:text-black underline underline-offset-4 transition-colors"
              data-testid="drawer-share-cart"
            >
              <Share2 size={12} />
              Sepeti Paylaş
            </button>
            </div>
          </>
        )}
      </div>
    </>
  );
}
