import { Link, useSearchParams } from "react-router-dom";
import { useEffect, useState, useRef } from "react";
import { Trash2, Plus, Minus, Share2 } from "lucide-react";
import { toast } from "sonner";
import axios from "axios";
import { shareCart } from "../lib/shareCart";
import { useShipping } from "../lib/shipping";
import Header from "../components/Header";
import Footer from "../components/Footer";
import { useCart } from "../context/CartContext";
import { useAuth } from "../context/AuthContext";
import { optimizeImg } from "../lib/img";
import { priceView, cartLineView } from "../lib/price";
import { trackViewCart } from "../utils/pixelEvents";

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;
const PLACEHOLDER = "/placeholder.jpg";

export default function Cart() {
  const { items, addItem, removeItem, updateQuantity, total, itemCount } = useCart();
  const { user } = useAuth();
  const [searchParams, setSearchParams] = useSearchParams();

  // ── SEPETİ PAYLAŞ ────────────────────────────────────────────────────────
  // Paylaş: sepet kalemleri sunucuya yazılır → kısa link panoya kopyalanır.
  // Alma: /sepet?paylasim=<id> ile gelen ziyaretçinin sepetine kalemler eklenir
  // (fiyat otoritesi sunucu — link açıldığı andaki güncel fiyat geçerli).
  const [sharing, setSharing] = useState(false);
  const shareLoadedRef = useRef(false); // StrictMode çift-effect / tekrar ekleme koruması

  const handleShareCart = async () => {
    if (items.length === 0 || sharing) return;
    setSharing(true);
    try { await shareCart(items); } finally { setSharing(false); }
  };

  useEffect(() => {
    const shareId = searchParams.get("paylasim");
    if (!shareId || shareLoadedRef.current) return;
    // Aynı link ikinci kez açılırsa (yenileme dahil) mükerrer ekleme olmasın
    const seenKey = `shared_cart_loaded_${shareId}`;
    if (sessionStorage.getItem(seenKey)) {
      setSearchParams({}, { replace: true });
      return;
    }
    shareLoadedRef.current = true;
    axios.get(`${API}/shared-carts/${shareId}`)
      .then((res) => {
        const list = res.data?.items || [];
        list.forEach(({ product, variant, quantity }) => {
          if (product?.id) addItem(product, variant || null, quantity || 1);
        });
        if (list.length) toast.success(`Paylaşılan sepetten ${list.length} ürün eklendi`);
        sessionStorage.setItem(seenKey, "1");
      })
      .catch((e) => {
        toast.error(e.response?.data?.detail || "Paylaşılan sepet yüklenemedi");
      })
      .finally(() => setSearchParams({}, { replace: true }));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchParams]);
  const { shippingFee, freeShippingThreshold } = useShipping();
  const freeShippingLimit = freeShippingThreshold || 0;

  // Madde 4 — Kampanya motoru: sepet sayfası da checkout ile AYNI motoru çağırır.
  // Önceden sadece Checkout.jsx çağırıyordu; bu yüzden "sepette otomatik %10" kampanyaları
  // sepet ekranında hiç görünmüyor/uygulanmıyordu (checkout'a geçilince aniden çıkıyordu).
  const [promoDiscount, setPromoDiscount] = useState(0);
  const [appliedPromotions, setAppliedPromotions] = useState([]);

  useEffect(() => {
    if (items.length === 0) { setPromoDiscount(0); setAppliedPromotions([]); return; }
    let cancel = false;
    axios.post(`${API}/coupons/evaluate`, {
      cart_total: total,
      items: items.map((it) => ({ product_id: it.productId, category_id: it.categoryId, price: it.price, qty: it.quantity })),
      user_id: user?.id || null,
      email: user?.email || "",
      code: "",
    }).then((res) => {
      if (cancel) return;
      const d = res.data || {};
      setPromoDiscount(Number(d.total_discount || 0));
      setAppliedPromotions(d.applied || []);
    }).catch(() => {
      if (!cancel) { setPromoDiscount(0); setAppliedPromotions([]); }
    });
    return () => { cancel = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [items, total, user?.id]);

  // ÜCRETSİZ KARGO EŞİĞİ — sunucunun kararıyla AYNI taban: İNDİRİM SONRASI sepet tutarı
  // (orders.create_order: (_subtotal - _server_discount) >= eşik). Eskiden indirimSİZ ara
  // toplam baz alınıyordu → hem "X TL daha" yazısı yanlış çıkıyor hem de sepet ekranı
  // "ücretsiz kargo" gösterip siparişte kargo ücreti ekleniyordu (tutarsızlık).
  const netTotal = Math.max(0, total - promoDiscount);
  const remaining = freeShippingThreshold != null ? Math.max(0, freeShippingThreshold - netTotal) : 0;
  const shippingCost = (freeShippingThreshold != null && netTotal >= freeShippingThreshold) ? 0 : shippingFee;

  // Ürün-seviyesi indirim (sale_price + otomatik kampanya) — satırlarla birebir tutarlı özet.
  const listSum = items.reduce((s, it) => s + cartLineView(it).listUnit * it.quantity, 0);
  const effSum = items.reduce((s, it) => s + cartLineView(it).unit * it.quantity, 0);
  const productDisc = Math.max(0, listSum - effSum);
  const extraDisc = Math.max(0, promoDiscount - productDisc);   // kupon/koşullu kampanya farkı
  const totalDisc = productDisc + extraDisc;
  const grandTotal = Math.max(0, effSum - extraDisc) + shippingCost;

  // Kombin / sale öneriler
  const [suggestions, setSuggestions] = useState([]);
  const [suggestionsLoading, setSuggestionsLoading] = useState(false);
  const [deals, setDeals] = useState([]);

  useEffect(() => {
    if (items.length === 0) { setSuggestions([]); setDeals([]); return; }
    let cancel = false;
    setSuggestionsLoading(true);
    const productIds = items.map((it) => it.productId).filter(Boolean);
    Promise.all([
      axios.post(`${API}/products/cart-suggestions`, { product_ids: productIds, limit: 8 }),
      axios.post(`${API}/products/checkout-deals`, { product_ids: productIds, limit: 6 }),
    ])
      .then(([s, d]) => {
        if (cancel) return;
        setSuggestions(s.data?.items || []);
        setDeals(d.data?.items || []);
      })
      .catch(() => { if (!cancel) { setSuggestions([]); setDeals([]); } })
      .finally(() => { if (!cancel) setSuggestionsLoading(false); });
    return () => { cancel = true; };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [items.length]);

  // GA4: view_cart — sepet sayfası görüntüleme (mount'ta bir kez)
  useEffect(() => {
    if (items.length === 0) return;
    try {
      trackViewCart({ total, items });
    } catch (_) { /* silent */ }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  if (items.length === 0) {
    return (
      <div className="min-h-screen bg-white" data-testid="cart-page">
        <Header />
        <div className="container-main py-24 text-center">
          <p className="text-[10px] tracking-[0.3em] text-black/50 uppercase mb-6">SEPETİM</p>
          <h1 className="text-3xl sm:text-4xl font-light tracking-tight mb-4">Sepetiniz boş</h1>
          <p className="text-sm text-black/60 mb-10 max-w-md mx-auto">
            Henüz sepetinize ürün eklemediniz. Yeni sezon parçaları keşfetmek için alışverişe başlayın.
          </p>
          <Link
            to="/"
            className="inline-flex items-center justify-center h-12 px-10 bg-black text-white text-xs uppercase tracking-[0.25em] hover:bg-black/85 transition-colors"
            data-testid="empty-cart-shop-btn"
          >
            Alışverişe başla
          </Link>
        </div>
        <Footer />
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-white pb-32 md:pb-12" data-testid="cart-page">
      <Header />

      <div className="container-main py-6 md:py-12">
        <div className="mb-8 md:mb-10 flex items-end justify-between gap-4">
          <div>
            <p className="text-[10px] tracking-[0.3em] text-black/50 uppercase mb-2">SEPETİM</p>
            <h1 className="text-2xl sm:text-3xl font-light tracking-tight">
              {itemCount} ürün
            </h1>
          </div>
          {/* Sepeti Paylaş — sepet linki oluşturup panoya kopyalar / paylaşım menüsü açar */}
          <button
            onClick={handleShareCart}
            disabled={sharing}
            data-testid="share-cart-btn"
            className="inline-flex items-center gap-2 h-10 px-4 border border-black/15 text-[11px] uppercase tracking-[0.2em] hover:border-black transition-colors disabled:opacity-50"
            title="Sepetini linkle paylaş"
          >
            <Share2 size={14} />
            {sharing ? "Hazırlanıyor..." : "Sepeti Paylaş"}
          </button>
        </div>

        <div className="grid lg:grid-cols-3 gap-8 lg:gap-12">
          {/* Cart Items */}
          <div className="lg:col-span-2">
            {/* Free Shipping Progress — eşik altı: kalan tutar; eşik üstü: kazandın kutlaması */}
            {freeShippingThreshold != null && items.length > 0 && (
              remaining > 0 ? (
                <div className="mb-8 p-4 bg-stone-50 border border-black/5">
                  <p className="text-xs text-center mb-2 text-black/70">
                    Ücretsiz kargo için <span className="font-medium text-black">{remaining.toFixed(2)} TL</span> daha ekleyin
                  </p>
                  <div className="h-[2px] bg-black/10 overflow-hidden">
                    <div
                      className="h-full bg-black transition-all duration-700 ease-out"
                      style={{ width: `${Math.min(100, (netTotal / freeShippingLimit) * 100)}%` }}
                    />
                  </div>
                </div>
              ) : (
                <div className="mb-8 p-4 bg-emerald-50 border border-emerald-200">
                  <p className="text-xs text-center mb-2 text-emerald-800 font-medium">
                    🎉 Ücretsiz kargo kazandın!
                  </p>
                  <div className="h-[2px] bg-emerald-200 overflow-hidden">
                    <div className="h-full bg-emerald-600 w-full" />
                  </div>
                </div>
              )
            )}

            {/* Items */}
            <div className="divide-y divide-black/10">
              {items.map((item) => (
                <div
                  key={item.id}
                  className="flex gap-4 sm:gap-6 py-6"
                  data-testid={`cart-item-${item.id}`}
                >
                  <Link to={`/${item.slug || item.productId || ""}`} className="shrink-0">
                    <img
                      src={item.image || PLACEHOLDER}
                      alt={item.name}
                      className="w-24 h-32 sm:w-32 sm:h-40 object-cover bg-stone-100"
                    />
                  </Link>
                  <div className="flex-1 min-w-0 flex flex-col">
                    <div className="flex justify-between items-start gap-3">
                      <div className="min-w-0">
                        <Link
                          to={`/${item.slug || item.productId || ""}`}
                          className="block"
                        >
                          <h3 className="text-sm sm:text-base font-medium leading-tight line-clamp-2 hover:underline">
                            {item.name}
                          </h3>
                        </Link>
                        <div className="mt-2 space-y-0.5 text-xs text-black/60">
                          {item.color && <p>Renk: {item.color}</p>}
                          {item.size && <p>Beden: {item.size}</p>}
                        </div>
                      </div>
                      <button
                        onClick={() => removeItem(item.id)}
                        className="text-black/40 hover:text-black transition-colors p-1 -m-1"
                        data-testid={`remove-cart-${item.id}`}
                        aria-label="Ürünü kaldır"
                      >
                        <Trash2 size={16} />
                      </button>
                    </div>

                    <div className="flex items-center justify-between mt-auto pt-4">
                      <div className="inline-flex items-center border border-black/15">
                        <button
                          onClick={() => updateQuantity(item.id, item.quantity - 1)}
                          className="p-2 hover:bg-black/5 transition-colors disabled:opacity-30"
                          disabled={item.quantity <= 1}
                          aria-label="Azalt"
                        >
                          <Minus size={12} />
                        </button>
                        <span className="px-3 sm:px-4 text-xs sm:text-sm tabular-nums">{item.quantity}</span>
                        <button
                          onClick={() => updateQuantity(item.id, item.quantity + 1)}
                          className="p-2 hover:bg-black/5 transition-colors"
                          aria-label="Arttır"
                        >
                          <Plus size={12} />
                        </button>
                      </div>
                      {(() => {
                        const lv = cartLineView(item);
                        return lv.hasDiscount ? (
                          <div className="text-right">
                            <p className="text-[11px] text-black/40 line-through tabular-nums">{(lv.listUnit * item.quantity).toFixed(2)} TL</p>
                            <p className="text-sm sm:text-base font-medium text-red-600 tabular-nums">{(lv.unit * item.quantity).toFixed(2)} TL</p>
                            <span className="text-[9px] font-semibold text-emerald-700">%{lv.discountPct} indirim uygulandı</span>
                          </div>
                        ) : (
                          <p className="text-sm sm:text-base font-medium tabular-nums">
                            {(lv.unit * item.quantity).toFixed(2)} TL
                          </p>
                        );
                      })()}
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </div>

          {/* Summary - desktop only sticky sidebar */}
          <div className="lg:col-span-1">
            <div className="bg-stone-50 p-6 lg:sticky lg:top-32 border border-black/5">
              <h2 className="text-[10px] tracking-[0.3em] uppercase text-black/60 mb-5">Sipariş Özeti</h2>

              <div className="space-y-3 text-sm">
                <div className="flex justify-between">
                  <span className="text-black/60">Ara toplam</span>
                  <span className="tabular-nums">{listSum.toFixed(2)} TL</span>
                </div>
                {totalDisc > 0.001 && (
                  <div className="flex justify-between text-emerald-700" data-testid="cart-promo-discount">
                    <span>
                      İndirim
                      {appliedPromotions.length > 0 && appliedPromotions[0]?.title ? ` (${appliedPromotions[0].title})` : ""}
                    </span>
                    <span className="tabular-nums">-{totalDisc.toFixed(2)} TL</span>
                  </div>
                )}
                <div className="flex justify-between">
                  <span className="text-black/60">Kargo</span>
                  <span className={shippingCost === 0 ? "text-emerald-700" : "tabular-nums"}>
                    {shippingCost === 0 ? "Ücretsiz" : `${shippingCost.toFixed(2)} TL`}
                  </span>
                </div>
                <div className="border-t border-black/10 pt-3 flex justify-between text-base">
                  <span className="font-medium">Toplam</span>
                  <span className="font-medium tabular-nums">{grandTotal.toFixed(2)} TL</span>
                </div>
                {/* Taksit yazısı kaldırıldı (kullanıcı isteği) */}
              </div>

              <Link
                to="/odeme"
                className="hidden md:flex items-center justify-center w-full h-14 mt-6 bg-black text-white text-xs uppercase tracking-[0.25em] hover:bg-black/85 transition-colors"
                data-testid="checkout-btn-desktop"
              >
                Ödemeye Geç
              </Link>

              <Link
                to="/"
                className="block text-center text-xs underline mt-4 text-black/60 hover:text-black transition-colors"
              >
                Alışverişe devam et
              </Link>
            </div>
          </div>
        </div>

        {/* Stilini Tamamla — mobile: yatay snap, desktop: 4-col grid */}
        {(suggestions.length > 0 || suggestionsLoading) && (
          <div className="mt-12 md:mt-16 pt-8 md:pt-12 border-t border-black/10" data-testid="cart-suggestions-block">
            <h2 className="text-base md:text-xl font-light tracking-tight mb-5 md:mb-8 px-1">Stilini Tamamla</h2>
            {/* Mobile snap-scroll */}
            <div className="md:hidden -mx-4 px-4 overflow-x-auto snap-x snap-mandatory scrollbar-hide">
              <div className="flex gap-3" style={{ minWidth: "max-content" }}>
                {suggestions.map((p) => {
                  const img = optimizeImg((p.images && p.images[0]) || p.image, 500) || PLACEHOLDER;
                  const pv = priceView(p);
                  return (
                    <div key={p.id} className="snap-start shrink-0 w-[44vw]" data-testid={`cart-suggestion-${p.id}`}>
                      <Link to={`/${p.slug || p.id}`} className="block relative overflow-hidden bg-stone-100 aspect-[2/3]" aria-label={p.name}>
                        <img src={img} alt={p.name} className="w-full h-full object-cover" loading="lazy" />
                      </Link>
                      <div className="mt-2">
                        <Link to={`/${p.slug || p.id}`} className="block text-[12px] font-light text-black/85 line-clamp-1">{p.name}</Link>
                        <div className="flex items-baseline gap-1.5 mt-0.5">
                          {pv.hasDiscount ? (
                            <>
                              <span className="text-[11px] text-black/40 line-through tabular-nums">{pv.list.toFixed(2)} TL</span>
                              <span className="text-[12px] font-medium text-red-600 tabular-nums">{pv.display.toFixed(2)} TL</span>
                            </>
                          ) : (
                            <span className="text-[12px] font-light tabular-nums">{pv.display.toFixed(2)} TL</span>
                          )}
                        </div>
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
            {/* Desktop grid */}
            <div className="hidden md:grid grid-cols-4 gap-5">
              {suggestions.map((p) => {
                const img = (p.images && p.images[0]) || p.image || "";
                const pv = priceView(p);
                return (
                  <div key={p.id} className="group relative">
                    <Link to={`/${p.slug || p.id}`} className="block relative overflow-hidden bg-stone-100 aspect-[2/3]" aria-label={p.name}>
                      <img src={img} alt={p.name} className="w-full h-full object-cover transition-transform duration-700 ease-out group-hover:scale-[1.03]" loading="lazy" />
                    </Link>
                    <div className="mt-2.5">
                      <Link to={`/${p.slug || p.id}`} className="block text-sm font-light text-black/85 line-clamp-1 hover:underline">{p.name}</Link>
                      <div className="flex items-baseline gap-2 mt-1">
                        {pv.hasDiscount ? (
                          <>
                            <span className="text-sm text-black/40 line-through tabular-nums">{pv.list.toFixed(2)} TL</span>
                            <span className="text-sm font-medium text-red-600 tabular-nums">{pv.display.toFixed(2)} TL</span>
                          </>
                        ) : (
                          <span className="text-sm font-light tabular-nums">{pv.display.toFixed(2)} TL</span>
                        )}
                      </div>
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        )}

        {/* Kasa Önü Fırsatları — indirimdeki ürünler */}
        {deals.length > 0 && (
          <div className="mt-12 md:mt-16 pt-8 md:pt-12 border-t border-black/10" data-testid="checkout-deals-block">
            <div className="mb-6 md:mb-8 flex items-end justify-between gap-4">
              <div>
                <p className="text-[10px] tracking-[0.3em] text-red-700 uppercase mb-2">Sınırlı Süre</p>
                <h2 className="text-xl sm:text-2xl font-light tracking-tight">Kasa önü fırsatları</h2>
              </div>
              <Link to="/sale" className="hidden sm:inline-block text-[11px] tracking-[0.25em] uppercase border-b border-black pb-0.5 hover:opacity-70">
                Tümü
              </Link>
            </div>
            <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3 sm:gap-4">
              {deals.map((p) => {
                const img = (p.images && p.images[0]) || p.image || "";
                const pv = priceView(p);
                const off = pv.discountPct;
                return (
                  <Link
                    key={p.id}
                    to={`/${p.slug || p.id}`}
                    className="group block"
                    data-testid={`checkout-deal-${p.id}`}
                  >
                    <div className="relative aspect-[2/3] bg-stone-100 overflow-hidden mb-2">
                      <img
                        src={img}
                        alt={p.name}
                        className="w-full h-full object-cover transition-transform duration-700 ease-out group-hover:scale-105"
                        loading="lazy"
                      />
                      {off > 0 && (
                        <span className="absolute top-2 left-2 bg-red-700 text-white text-[10px] tracking-[0.15em] px-2 py-1 uppercase">
                          %{off}
                        </span>
                      )}
                    </div>
                    <h3 className="text-[11px] sm:text-xs text-black/85 leading-tight line-clamp-1 group-hover:underline">{p.name}</h3>
                    <div className="flex items-baseline gap-2 mt-0.5">
                      {pv.hasDiscount ? (
                        <>
                          <span className="text-xs font-medium text-red-700 tabular-nums">{pv.display.toFixed(2)} TL</span>
                          <span className="text-[10px] text-black/40 line-through tabular-nums">{pv.list.toFixed(2)} TL</span>
                        </>
                      ) : (
                        <span className="text-xs text-black/85 tabular-nums">{pv.display.toFixed(2)} TL</span>
                      )}
                    </div>
                  </Link>
                );
              })}
            </div>
          </div>
        )}
      </div>

      {/* Sticky bottom mobile CTA */}
      <div
        className="fixed bottom-0 left-0 right-0 z-40 bg-white border-t border-black/10 px-4 pt-3 pb-[calc(env(safe-area-inset-bottom)+12px)] md:hidden shadow-[0_-4px_20px_rgba(0,0,0,0.05)]"
        data-testid="cart-mobile-sticky-cta"
      >
        <div className="flex items-center justify-between mb-2">
          <span className="text-xs text-black/60">Toplam</span>
          <span className="text-sm font-medium tabular-nums">{grandTotal.toFixed(2)} TL</span>
        </div>
        <Link
          to="/odeme"
          className="flex items-center justify-center w-full h-12 bg-black text-white text-xs uppercase tracking-[0.25em] hover:bg-black/85 active:bg-black/85 transition-colors"
          data-testid="checkout-btn"
        >
          Ödemeye Geç
        </Link>
      </div>

      <Footer />
    </div>
  );
}
