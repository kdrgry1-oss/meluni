import { useState, useRef, useMemo } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Bookmark, ShoppingBag } from "lucide-react";
import { useCart } from "../context/CartContext";
import { useFavorites } from "../context/FavoritesContext";
import { optimizeImg } from "../lib/img";
import { resolveColor, needsBorder, MULTI_GRADIENT } from "../lib/colorMap";
import { sortLikeSize } from "../utils/sizeSort";
import { priceView } from "../lib/price";
import { trackSelectItem } from "../lib/dataLayer";
import { toast } from "sonner";

export default function ProductCard({ product, listId = "", listName = "", index }) {
  const [currentImageIndex, setCurrentImageIndex] = useState(0);
  const [activeSib, setActiveSib] = useState(null); // hover edilen renk kardeşi
  const { addItem } = useCart();
  const { isFavorite, toggleFavorite } = useFavorites();
  const navigate = useNavigate();
  const isFav = isFavorite(product.id);
  const imageContainerRef = useRef(null);

  // Remove duplicate first image
  const allImages = product.images || [];
  const images = allImages.length > 1 && allImages[0] === allImages[1]
    ? allImages.slice(1)
    : allImages;

  const hasMultipleImages = images.length > 1;
  // İndirim görünümü TEK KAYNAK'tan (lib/price) — vitrin/arama/menü/kombin ile birebir tutarlı.
  const pv = priceView(product);
  const displayPrice = pv.display;
  const hasDiscount = pv.hasDiscount;

  // Renk kardeşleri (aynı modelin diğer renkleri) — backend `color_siblings` döndürür.
  const siblings = Array.isArray(product.color_siblings) ? product.color_siblings : [];
  const hasColors = siblings.length > 1;
  const activeId = activeSib?.id || product.id;
  const targetSlug = activeSib?.slug || product.slug || product.id;

  // Bedenler (hover'da görsel altında) — varyantlardan benzersiz beden + stok.
  const sizeList = useMemo(() => {
    const vs = product.variants || [];
    const map = new Map();
    for (const v of vs) {
      const s = (v.size || "").toString().trim();
      if (!s) continue;
      if (!map.has(s)) map.set(s, { size: s, variant: v, stock: Number(v.stock) || 0 });
      else map.get(s).stock += Number(v.stock) || 0;
    }
    const arr = [...map.values()];
    try { return sortLikeSize(arr, (x) => x.size); } catch { return arr; }
  }, [product.variants]);

  // Efektif stok: varyant varsa varyant stokları toplamı, yoksa ürün stoğu.
  const variants = product.variants || [];
  const variantStock = variants.reduce((s, v) => s + (Number(v.stock) || 0), 0);
  const effectiveStock = variants.length > 0 ? variantStock : (Number(product.stock) || 0);
  const isSoldOut = effectiveStock <= 0;

  // Katmanlı galeri: hover/kaydırmada src DEĞİŞTİRİLMEZ (yüklenene kadar beyaz alan +
  // alt yazısı [ürün adı] görünüyordu). Taban görsel hep altta kalır; diğer kareler ilk
  // etkileşimde üstte şeffaf katman olarak yüklenir, opaklıkla geçiş yapılır.
  const [galleryReady, setGalleryReady] = useState(false);

  const handleQuickAdd = (e) => {
    e.preventDefault();
    e.stopPropagation();
    if (isSoldOut) {
      toast.error("Bu ürün tükendi");
      return;
    }
    // KRİTİK: Bedenli üründe beden SEÇMEDEN sepete ekleme yapma. Aksi halde siparişe bedensiz
    // kalem düşüyor ve hangi bedenin gönderileceği bilinemiyordu (ör. W10322 bermuda şort).
    // Bedeni olan üründe "+" butonu, beden seçimi için ürün sayfasına yönlendirir.
    if (sizeList.length > 0) {
      navigate(`/${targetSlug}`);
      return;
    }
    addItem(product);
    toast.success("Ürün sepete eklendi");
  };

  // Hover'da bedene tıklayınca o beden varyantını sepete ekle.
  const handleSizeAdd = (e, s) => {
    e.preventDefault();
    e.stopPropagation();
    if (!s || s.stock <= 0) return;
    addItem(product, s.variant);
    toast.success(`Sepete eklendi · Beden ${s.size}`);
  };

  const handleFavorite = (e) => {
    e.preventDefault();
    e.stopPropagation();
    toggleFavorite(product);
  };

  const handleSelect = () => {
    try {
      trackSelectItem({ product, listId, listName, index });
    } catch (_) { /* silent */ }
  };

  // Görseller yatay kaydırma: fare/parmak sağa gittikçe sonraki görsel (Mango usulü).
  // Ortak mantık — hem masaüstü mouse hem mobil touch bunu kullanır.
  const updateImageFromX = (clientX) => {
    if (activeSib || !hasMultipleImages || !imageContainerRef.current) return;
    const rect = imageContainerRef.current.getBoundingClientRect();
    const x = clientX - rect.left;
    const width = rect.width;
    const segmentWidth = width / images.length;
    const newIndex = Math.min(Math.max(Math.floor(x / segmentWidth), 0), images.length - 1);
    setCurrentImageIndex((prev) => (newIndex !== prev ? newIndex : prev));
  };

  const handleMouseMove = (e) => {
    updateImageFromX(e.clientX);
  };

  const handleMouseLeave = () => {
    setCurrentImageIndex(0);
  };

  // Mobil dokunmatik kaydırma — yatay parmak hareketi resmi değiştirir (mouse hover'ın
  // dokunmatik karşılığı); dikey hareket baskınsa sayfa scroll'una karışmaz.
  const touchRef = useRef({ x: 0, y: 0, swiping: false });

  const handleTouchStart = (e) => {
    if (activeSib || !hasMultipleImages) return;
    setGalleryReady(true);
    const t = e.touches[0];
    touchRef.current = { x: t.clientX, y: t.clientY, swiping: false };
  };

  const handleTouchMove = (e) => {
    if (activeSib || !hasMultipleImages) return;
    const t = e.touches[0];
    const dx = t.clientX - touchRef.current.x;
    const dy = t.clientY - touchRef.current.y;
    if (touchRef.current.swiping || (Math.abs(dx) > 8 && Math.abs(dx) > Math.abs(dy))) {
      touchRef.current.swiping = true;
      e.preventDefault(); // yatay galeri kaydırması — sayfanın dikey scroll'unu engelle
      updateImageFromX(t.clientX);
    }
  };

  const handleTouchEnd = () => {
    touchRef.current.swiping = false;
  };

  return (
    <div className="product-card group" data-testid={`product-card-${product.id}`}>
      {/* Image (Link) */}
      <Link to={`/${targetSlug}`} onClick={handleSelect} aria-label={product.name}>
        <div
          ref={imageContainerRef}
          className="relative aspect-[2/3] bg-white overflow-hidden"
          onMouseEnter={() => { if (hasMultipleImages) setGalleryReady(true); }}
          onMouseMove={handleMouseMove}
          onMouseLeave={handleMouseLeave}
          onTouchStart={handleTouchStart}
          onTouchMove={handleTouchMove}
          onTouchEnd={handleTouchEnd}
        >
          {/* Taban görsel — her zaman altta ve dolu; üst katman yüklenene kadar bu görünür */}
          <img
            src={optimizeImg(images[0] || "/placeholder.jpg", 700)}
            alt={product.name}
            className="w-full h-full object-cover object-top"
            loading="lazy"
            decoding="async"
          />
          {/* Galeri kareleri — ilk etkileşimde topluca yüklenir (ön-yükleme), opaklıkla geçilir.
              alt="" olduğundan yüklenirken ASLA yazı/beyaz kutu görünmez. */}
          {galleryReady && !activeSib && images.slice(1).map((im, i) => (
            <img
              key={im}
              src={optimizeImg(im, 700)}
              alt=""
              aria-hidden="true"
              className={`absolute inset-0 w-full h-full object-cover object-top transition-opacity duration-150 ${currentImageIndex === i + 1 ? "opacity-100" : "opacity-0"}`}
              decoding="async"
            />
          ))}
          {/* Renk kardeşi önizlemesi — o da katman: yüklenene kadar taban görünür */}
          {activeSib?.image && (
            <img
              src={optimizeImg(activeSib.image, 700)}
              alt=""
              aria-hidden="true"
              className="absolute inset-0 w-full h-full object-cover object-top"
              decoding="async"
            />
          )}

          {/* Tükendi rozeti — görselin TAM ORTASINDA (sol üst köşede değil) */}
          {isSoldOut && (
            <div className="absolute inset-0 z-10 flex items-center justify-center pointer-events-none">
              <span className="bg-black/80 text-white text-[11px] uppercase tracking-[0.15em] px-3 py-1.5">
                Tükendi
              </span>
            </div>
          )}

          {/* İndirim oranı rozeti — tam sol üst köşeye yapışık */}
          {hasDiscount && (
            <div
              className="absolute top-0 left-0 z-10 bg-[#6b6b64] text-white text-xs font-normal px-2.5 py-1.5 leading-none"
              data-testid={`discount-badge-${product.id}`}
            >
              %{Math.round(((product.price - displayPrice) / product.price) * 100)}
            </div>
          )}

          {/* Favorite Button */}
          <button
            onClick={handleFavorite}
            className="absolute top-3 right-3 z-10"
            data-testid={`favorite-${product.id}`}
            aria-label={isFav ? "Kaydedilenlerden çıkar" : "Kaydet"}
            title={isFav ? "Kaydedilenlerden çıkar" : "Kaydet"}
          >
            <Bookmark
              size={20}
              strokeWidth={1.5}
              className={`transition-colors ${isFav ? "fill-black text-black" : "text-gray-400 hover:text-black"}`}
            />
          </button>

          {/* Bedenler — hover'da görselin en altında, Mango usulü (ortalı, ferah aralık) */}
          {sizeList.length > 0 && (
            <>
              {/* Bedenler — Mango birebir: alta sabit yarı saydam açık-gri dikdörtgen bar, bedenler ortalı (DOM'dan: rgba(250,250,250,0.898), gap 8px, 13px/600, #131313) */}
              <div
                className="absolute inset-x-0 bottom-0 z-10 hidden md:flex items-center justify-center gap-2 px-4 py-3.5 opacity-0 pointer-events-none group-hover:opacity-100 group-hover:pointer-events-auto transition-opacity duration-200"
                style={{ backgroundColor: "rgba(250,250,250,0.898)" }}
              >
                {sizeList.map((s) => (
                  <button
                    key={s.size}
                    onClick={(e) => handleSizeAdd(e, s)}
                    disabled={s.stock <= 0}
                    className={`text-[13px] font-semibold leading-none px-2 py-2 transition-opacity ${
                      s.stock <= 0
                        ? "text-[#131313]/30 line-through cursor-not-allowed"
                        : "text-[#131313] hover:opacity-60"
                    }`}
                    title={s.stock <= 0 ? `${s.size} · Tükendi` : `${s.size} · Sepete ekle`}
                  >
                    {s.size}
                  </button>
                ))}
              </div>
            </>
          )}

          {/* Görsel göstergeleri — hover'da bedenlere yer açmak için gizlenir */}
          {hasMultipleImages && !activeSib && (
            <div className="absolute bottom-3 left-1/2 -translate-x-1/2 flex gap-1.5 z-0 md:group-hover:opacity-0 transition-opacity">
              {images.map((_, i) => (
                <div
                  key={i}
                  className={`transition-all ${
                    i === currentImageIndex
                      ? "w-5 h-1.5 bg-black rounded-full"
                      : "w-1.5 h-1.5 bg-gray-300 rounded-full"
                  }`}
                />
              ))}
            </div>
          )}
        </div>
      </Link>

      {/* Product Info */}
      <div className="mt-3">
        <Link to={`/${targetSlug}`} onClick={handleSelect}>
          <div className="flex items-start justify-between gap-2">
            <h3 className="text-sm leading-snug flex-1 line-clamp-2">{product.name}</h3>
            <button
              onClick={handleQuickAdd}
              disabled={isSoldOut}
              className={`flex-shrink-0 p-1 md:mr-2 transition-opacity ${isSoldOut ? "opacity-30 cursor-not-allowed" : "hover:opacity-60"}`}
              data-testid={`quick-add-${product.id}`}
              aria-label={isSoldOut ? "Tükendi" : "Sepete ekle"}
              title={isSoldOut ? "Tükendi" : "Sepete Ekle"}
            >
              <ShoppingBag size={18} strokeWidth={1.5} />
            </button>
          </div>

          {/* Price */}
          <div className="mt-1.5 flex items-center gap-2">
            {hasDiscount ? (
              <>
                <span className="text-xs text-gray-400 line-through">{product.price.toFixed(2).replace('.', ',')} TL</span>
                <span className="text-sm text-red-600 font-medium">{displayPrice.toFixed(2).replace('.', ',')} TL</span>
              </>
            ) : (
              <span className="text-sm">{(displayPrice || 0).toFixed(2).replace('.', ',')} TL</span>
            )}
          </div>
        </Link>

        {/* Renk kutucukları (swatch) — aynı modelin diğer renkleri */}
        {hasColors && (
          <div
            className="mt-2 flex flex-wrap items-center gap-1.5"
            onMouseLeave={() => setActiveSib(null)}
            data-testid={`color-swatches-${product.id}`}
          >
            {siblings.slice(0, 6).map((sib) => {
              const isActive = sib.id === activeId;
              const isSelf = sib.id === product.id;
              const col = resolveColor(sib.color);
              let bgStyle, fallback = false;
              if (col?.type === "solid") bgStyle = { backgroundColor: col.value };
              else if (col?.type === "multi") bgStyle = { background: MULTI_GRADIENT };
              else fallback = true; // renk adı çözülemedi → görsele düş (varsa)
              const lightBorder = col?.type === "solid" && needsBorder(col.value);
              return (
                <Link
                  key={sib.id}
                  to={`/${sib.slug || sib.id}`}
                  onMouseEnter={() => setActiveSib(isSelf ? null : sib)}
                  onClick={(e) => { if (isSelf) e.preventDefault(); }}
                  className={`w-5 h-5 overflow-hidden flex-shrink-0 transition-all ${
                    isActive
                      ? "ring-1 ring-offset-1 ring-black border border-black"
                      : lightBorder
                        ? "border border-gray-300 hover:border-black"
                        : "border border-transparent hover:ring-1 hover:ring-offset-1 hover:ring-black"
                  }`}
                  title={sib.color || ""}
                  aria-label={sib.color || "Renk"}
                >
                  {fallback
                    ? <span className="block w-full h-full bg-gradient-to-br from-gray-200 to-gray-400" />
                    : <span className="block w-full h-full" style={bgStyle} />}
                </Link>
              );
            })}
            {siblings.length > 6 && (
              <span className="text-[11px] text-gray-400">+{siblings.length - 6}</span>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
