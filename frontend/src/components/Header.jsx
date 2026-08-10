import { useRef, useState, useEffect } from "react";
import { Link, useNavigate, useLocation } from "react-router-dom";
import { Search, User, ShoppingBag, X, Bookmark } from "lucide-react";
import { useCart } from "../context/CartContext";
import { useAuth } from "../context/AuthContext";
import { useFavorites } from "../context/FavoritesContext";
import CartDrawer from "./CartDrawer";
import CountdownBar from "./CountdownBar";
import { optimizeImg } from "../lib/img";
import { slugify } from "../lib/slug";
import { fetchHeaderMenu, slugFromLink, DEFAULT_MENU_TABS } from "../lib/headerMenu";
import { priceView } from "../lib/price";
import axios from "axios";

// Menü/öneri kartı fiyatı — indirim varsa üstü çizili liste + indirimli (tutarlı görünüm).
function MegaPrice({ p }) {
  const pv = priceView(p);
  if (pv.hasDiscount) {
    return (
      <p className="text-[11px] tabular-nums">
        <span className="text-black/40 line-through mr-1">{pv.list.toFixed(2)}</span>
        <span className="text-red-600">{pv.display.toFixed(2)} TL</span>
      </p>
    );
  }
  return <p className="text-[11px] tabular-nums text-black/65">{pv.display.toFixed(2)} TL</p>;
}

// Arama önizleme/öneri mini kartlarında görsel üzerine indirim oranı rozeti
// (vitrin ProductCard ile aynı — sol üst köşe). priceView tek kaynağından.
function MegaDiscountBadge({ p }) {
  const pv = priceView(p);
  if (!pv.hasDiscount || pv.discountPct <= 0) return null;
  return (
    <div className="absolute top-0 left-0 z-10 bg-[#6b6b64] text-white text-[11px] font-normal px-2 py-1 leading-none">
      %{pv.discountPct}
    </div>
  );
}

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;

// Menü — TEK KAYNAK panel (Admin > Tasarım > Menü Yönetimi → page-blocks/header-menu).
// Sekmeler (ad/sıra/tip/stil) ve kolonlar TAMAMEN panelden gelir; fetch başarısızsa
// DEFAULT_MENU_TABS (lib/headerMenu) kullanılır. Sabit GİYİM/AKSESUAR yapısı kaldırıldı —
// panelde yapılan her değişiklik (sekme adı, yeni sekme, kolon/kategori) siteye birebir yansır.

// Kemer & Şapka mağazada YOK → panelde hâlâ tanımlı olsalar bile aksesuar menüsünden GİZLE
// (panel elle temizlenmese de düşer).
const _AKSESUAR_HIDDEN = ["kemer", "sapka", "şapka"];
function _isHiddenAksesuar(name, slug) {
  const n = String(name || "").toLocaleLowerCase("tr");
  const s = String(slug || "").toLocaleLowerCase("tr");
  return _AKSESUAR_HIDDEN.some((h) => n === h || s === h || n.startsWith(h + " ") || s.startsWith(h + "-"));
}

// Bir mega sekmenin kolonlarını render için normalize eder: başlık/link/slug + item listesi.
function megaColumnsOf(tab) {
  const cols = [];
  for (const col of (tab?.columns || [])) {
    if (!col || !col.title) continue;
    let items = (col.items || [])
      .filter((it) => it && it.name)
      .map((it) => {
        const slug = slugFromLink(it.link) || slugify(it.name);
        return { name: it.name, slug, link: it.link || `/${slug}` };
      });
    if (tab?.id === "aksesuar") items = items.filter((it) => !_isHiddenAksesuar(it.name, it.slug));
    const cslug = slugFromLink(col.link) || slugify(col.title);
    cols.push({ title: col.title, slug: cslug, link: col.link || `/${cslug}`, items });
  }
  return cols;
}

// Menu images for right side
const MENU_IMAGES = {
  giyim: [
    "https://cdn.facette.com.tr/pagedesign/title-65777bd3-0-1920.webp",
    "https://cdn.facette.com.tr/pagedesign/title-7b3e27f9-5-1920.webp"
  ],
  aksesuar: [
    "https://cdn.facette.com.tr/pagedesign/title-7b3e27f9-5-1920.webp"
  ]
};

// Mega menü sağ panel — ürünler yüklenirken iskelet gösterir (eski/yanlış görsel flash'ını önler).
// ── MEGA MENÜ ORTAK AYARLARI ─────────────────────────────────────────────
// Tüm bölümler (GİYİM, AKSESUAR ve İLERİDE eklenecek her yeni bölüm) kolon
// düzenini ve görsel kartını BURADAN alır — bölümler arası fark oluşmaz.
const MEGA_LINK_GRID = "grid gap-x-14 gap-y-1";
const megaCols = (n) => ({ gridTemplateColumns: `repeat(${n}, 180px)` });
const MEGA_IMG_BOX = "w-52 aspect-[2/3] overflow-hidden";
const MEGA_IMG = "w-full h-full object-cover object-top group-hover:scale-[1.04] transition-transform duration-500";

function MegaProductsPanel({ products, loading, fallback, fallbackLink, onNavigate }) {
  if (loading) {
    return [0, 1, 2].map((i) => (
      <div key={i} className="w-52" data-testid="mega-product-skeleton">
        <div className="w-52 aspect-[2/3] bg-stone-100 animate-pulse" />
        <div className="h-2.5 bg-stone-100 mt-2 w-3/4 animate-pulse" />
        <div className="h-2.5 bg-stone-100 mt-1 w-1/3 animate-pulse" />
      </div>
    ));
  }
  if (products.length > 0) {
    return products.map((p) => (
      <Link
        key={p.id}
        to={`/${p.slug || p.id}`}
        className="block w-52 group"
        onClick={onNavigate}
      >
        <div className={MEGA_IMG_BOX}>
          <img
            src={optimizeImg((p.images && p.images[0]) || p.image || "", 500)}
            alt={p.name}
            className={MEGA_IMG}
            loading="lazy"
            decoding="async"
          />
        </div>
        <p className="text-[11px] mt-2 line-clamp-1 text-black/85">{p.name}</p>
        <MegaPrice p={p} />
      </Link>
    ));
  }
  return fallback.map((img, i) => (
    <Link key={i} to={fallbackLink} className="block w-52 aspect-[2/3] overflow-hidden" onClick={onNavigate}>
      <img src={optimizeImg(img, 400)} alt="" className="w-full h-full object-cover" loading="lazy" decoding="async" />
    </Link>
  ));
}

export default function Header({ hideMenu = false }) {
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);
  // Üst menü: Admin > Tasarım > Menü Yönetimi'nden (page-blocks/header-menu) beslenir.
  // Sekmeler dahil TÜM yapı panelden gelir; fetch gelene kadar varsayılan kullanılır.
  const [menuTabs, setMenuTabs] = useState(DEFAULT_MENU_TABS);
  useEffect(() => {
    let alive = true;
    fetchHeaderMenu(API).then((tabs) => {
      if (alive && Array.isArray(tabs) && tabs.length) setMenuTabs(tabs);
    }).catch(() => { /* varsayılan kalır */ });
    return () => { alive = false; };
  }, []);
  const visibleTabs = menuTabs.filter((t) => t && t.active !== false && t.label);
  const [searchOpen, setSearchOpen] = useState(false);
  const [searchQuery, setSearchQuery] = useState("");
  const [searchResults, setSearchResults] = useState([]);
  const [searchTotal, setSearchTotal] = useState(0);
  const [popularSearches, setPopularSearches] = useState([]);
  const [suggestedProducts, setSuggestedProducts] = useState([]);
  const [activeMenu, setActiveMenu] = useState(null);
  const [hoveredCategory, setHoveredCategory] = useState(null); // alt kategori slug (ceket, sort, ...)
  const [megaProducts, setMegaProducts] = useState({}); // {slug: [products]}
  
  const { itemCount, setIsOpen } = useCart();
  const { user } = useAuth();
  const { count: favCount } = useFavorites();
  const navigate = useNavigate();
  const location = useLocation();

  const isCheckout = location.pathname.includes('/odeme') || location.pathname.includes('/checkout');

  // Hero-overlay: ana sayfada TAM EKRAN editorial hero varken ve sayfa en üstteyken header
  // ŞEFFAF + logo/ikonlar BEYAZ; aşağı inince beyaz zemin + siyah. Home, hero varsa
  // document.documentElement'e data-hero-overlay="1" bırakır; burada scroll'a göre hesaplanır.
  // Hero-overlay: ana sayfada editorial hero VARKEN ve sayfa TAM EN ÜSTTEYKEN (kaydırılmamış) →
  // ilk ekran: SİYAH duyuru barı YOK, görsel tam ekran, header ŞEFFAF + logo/ikon BEYAZ, görselin
  // üzerinde biraz aşağıda yüzer. KAYDIRMA BAŞLAR BAŞLAMAZ (scrollY>0) → overlay kapanır: beyaz
  // sticky header + siyah duyuru barı gelir. (Hero jest-slider'ı sayfayı kaydırmadığı için slaytlar
  // arası overlay AÇIK kalır; ancak gerçek sayfa kaydırması başlayınca kapanır.)
  const [heroOverlay, setHeroOverlay] = useState(false);
  const [heroPage, setHeroPage] = useState(false);   // ana sayfa + editorial hero var mı (kaydırmadan bağımsız)
  useEffect(() => {
    let rafId = 0;
    const compute = () => {
      if (typeof document === "undefined" || location.pathname !== "/") { setHeroOverlay(false); setHeroPage(false); return false; }
      const hero = document.querySelector('[data-testid="hero-editorial"]');
      if (!hero) { setHeroOverlay(false); setHeroPage(false); return false; }
      setHeroPage(true);
      const scrolled = window.scrollY || window.pageYOffset || document.documentElement.scrollTop || 0;
      setHeroOverlay(scrolled < 6);   // yalnız sayfa en üstünde → floating; kaydırınca → sticky
      return true;                    // hero bulundu
    };
    // Hero, sayfa blokları async yüklendiği için header'dan GEÇ mount olabilir → hero DOM'a gelene
    // kadar (max ~2.5 sn) requestAnimationFrame ile POLL et. Bulununca değerlendirme zaten yapıldı,
    // poll'u sonlandır (sonrasında scroll/resize dinleyicileri yeterli).
    let tries = 0;
    const poll = () => {
      const found = compute();
      if (!found && tries < 150) { tries++; rafId = requestAnimationFrame(poll); }
    };
    poll();
    window.addEventListener("scroll", compute, { passive: true });
    window.addEventListener("resize", compute);
    let mo = null;
    try { mo = new MutationObserver(compute); mo.observe(document.documentElement, { attributes: true, attributeFilter: ["data-hero-overlay"] }); } catch (_) { /* noop */ }
    return () => {
      if (rafId) cancelAnimationFrame(rafId);
      window.removeEventListener("scroll", compute); window.removeEventListener("resize", compute);
      if (mo) mo.disconnect();
    };
  }, [location.pathname]);

  // Mega menü: hoveredCategory veya activeMenu için en çok satan ürünleri lazy fetch (3 ürün).
  // Kategori boş dönerse statik banner yerine genel popüler ürünlere düşülür → sağ panel her zaman dinamik.
  const _activeTab = menuTabs.find((t) => t && t.id === activeMenu) || null;
  const _tabSlug = (t) => (t ? (slugFromLink(t.link) || t.id) : null);
  useEffect(() => {
    const slug = hoveredCategory || _tabSlug(_activeTab);
    if (slug && megaProducts[slug] === undefined) {
      axios.get(`${API}/products?category=${slug}&limit=3&sort=popular`)
        .then(async (r) => {
          let list = r.data?.products || [];
          if (list.length === 0) {
            const r2 = await axios.get(`${API}/products?limit=3&sort=popular`).catch(() => null);
            list = r2?.data?.products || [];
          }
          setMegaProducts((prev) => ({ ...prev, [slug]: list }));
        })
        .catch(() => setMegaProducts((prev) => ({ ...prev, [slug]: [] })));
    }
  }, [hoveredCategory, activeMenu, menuTabs]);

  // Aktif olarak gösterilecek ürün listesi: önce alt kategori hover, yoksa ana kategori (3 ürün)
  const activeMegaSlug = hoveredCategory || _tabSlug(_activeTab);
  const activeMegaProducts = (megaProducts[activeMegaSlug] || []).slice(0, 3);
  // Henüz fetch tamamlanmadıysa (undefined) yükleniyor → iskelet göster, fallback görsel flash etme
  const megaLoading = Boolean(activeMegaSlug) && megaProducts[activeMegaSlug] === undefined;

  // Mega menü kapanma timer'ı — REF tabanlı. Önceki state tabanlı sürümde scheduleClose
  // mevcut timer'ı temizlemeden yenisini kuruyordu: tetikleyiciden panele geçerken sızan
  // eski timer, imleç panelin ÜZERİNDEYKEN menüyü kapatıyordu ("kategoriye tıklayamadan
  // kayboluyor"). Ref + her kurulumda temizlik yarışı bitirir; gecikme 1sn'ye çıkarıldı.
  const closeTimerRef = useRef(null);
  const openMenu = (m) => {
    if (closeTimerRef.current) { clearTimeout(closeTimerRef.current); closeTimerRef.current = null; }
    setActiveMenu(m);
  };
  const scheduleClose = () => {
    if (closeTimerRef.current) clearTimeout(closeTimerRef.current);
    closeTimerRef.current = setTimeout(() => {
      setActiveMenu(null); setHoveredCategory(null); closeTimerRef.current = null;
    }, 180);  // 1sn çok uzundu ("geç kayboluyor"); 180ms panele imleç taşımaya yeter, laggy değil
  };
  const cancelClose = () => {
    if (closeTimerRef.current) { clearTimeout(closeTimerRef.current); closeTimerRef.current = null; }
  };

  useEffect(() => {
    if (searchOpen && popularSearches.length === 0) {
      fetchPopularSearches();
    }
    if (searchOpen && suggestedProducts.length === 0) {
      axios.get(`${API}/products?limit=8&sort=popular`)
        .then((r) => setSuggestedProducts(r.data?.products || []))
        .catch(() => {});
    }
  }, [searchOpen]);

  // Zara davranışı: overlay açıkken arka plan scroll kilidi + ESC ile kapat
  useEffect(() => {
    if (!searchOpen) return;
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const onKey = (e) => {
      if (e.key === "Escape") { setSearchOpen(false); setSearchQuery(""); }
    };
    window.addEventListener("keydown", onKey);
    return () => {
      document.body.style.overflow = prevOverflow;
      window.removeEventListener("keydown", onKey);
    };
  }, [searchOpen]);

  useEffect(() => {
    const q = searchQuery;
    const timer = setTimeout(async () => {
      if (q.length < 1) {
        setSearchResults([]);
        setSearchTotal(0);
        return;
      }
      try {
        // Önizleme 12'ye çıkarıldı (6 çok azdı → adında geçse bile ürünler "çıkmıyor" görünüyordu);
        // toplam sonuç sayısı da alınır ki "Tüm sonuçları gör (N)" ile kalan ürünler belli olsun.
        const res = await axios.get(`${API}/products?search=${encodeURIComponent(q)}&limit=12`);
        setSearchResults(res.data?.products || []);
        setSearchTotal(Number(res.data?.total || 0));
      } catch (err) {
        console.error(err);
      }
    }, q.length >= 1 ? 300 : 0);
    return () => clearTimeout(timer);
  }, [searchQuery]);

  const fetchPopularSearches = async () => {
    try {
      const res = await axios.get(`${API}/search/popular`);
      setPopularSearches(res.data || []);
    } catch (err) {
      setPopularSearches([
        { term: "elbise", count: 150 },
        { term: "bluz", count: 120 },
        { term: "pantolon", count: 100 },
      ]);
    }
  };

  const submitSearch = () => {
    const q = searchQuery.trim();
    if (q) {
      navigate(`/arama?q=${encodeURIComponent(q)}`);
      setSearchOpen(false);
      setSearchQuery("");
    }
  };

  const closeSearch = () => { setSearchOpen(false); setSearchQuery(""); };

  return (
    <>
      {/* Ana-sayfa-hero'da: SİYAH bar + header TEK fixed sarmalayıcıda (aralarında BOŞLUK/BEYAZ ÇİZGİ
          YOK, bitişik). Hero arkada top-0'da → görsel tam ekran. İlk ekran (overlay): bar siyah + header
          şeffaf/beyaz görselin üzerinde. Kaydırınca (overlay off): bar gizlenir, header beyaz sticky.
          Diğer sayfalarda: bar akışta + header sticky (klasik). */}
      <div className={heroPage ? "fixed inset-x-0 top-0 z-40" : ""}>
        {/* Duyuru barı: ana-sayfa-hero'da yalnız EN ÜSTTE (overlay) görünür; diğer sayfalarda hep. */}
        {(heroPage ? heroOverlay : true) && !isCheckout && <CountdownBar />}

        <header
          className={`z-40 transition-colors duration-300 ${
            heroOverlay
              ? "bg-transparent text-white"
              : heroPage
                ? "bg-white/95 backdrop-blur-xl border-b border-black/5 text-black"
                : "sticky top-0 bg-white/95 backdrop-blur-xl border-b border-black/5 text-black"
          }`}
        >
          {/* Açık hero görselinde beyaz logo/ikonların okunması için üstte ince koyu gradient scrim */}
          {heroOverlay && (
            <div className="pointer-events-none absolute inset-x-0 top-0 h-24 bg-gradient-to-b from-black/30 to-transparent" aria-hidden="true" />
          )}
          {/* Overlay'de logo/ikonlar görselin üzerinde biraz AŞAĞIDA başlasın: ekstra üst boşluk */}
          <div className={`relative max-w-[1800px] mx-auto px-3 md:px-8 ${heroOverlay ? "pt-6 md:pt-4" : ""}`}>
          {/* Masaüstünde (lg) logo + menü + butonlar YALNIZ hero-overlay (üst/şeffaf) durumunda
              biraz aşağıda dursun. STICKY (kaydırınca beyaz sabit header) devreye girince pt-6
              KALKAR → içerik dikeyde ortalı kalır (aşağı kaymaz). Mobil/tablet aynı. */}
          <div className={`relative flex items-center h-12 md:h-14 ${heroOverlay ? "lg:pt-6" : ""}`}>
            {/* Left: Navigation Menu */}
            <div className="flex-1 flex items-center gap-2.5">
              {!isCheckout && (
                <>
                  <button
                    className="lg:hidden p-2 -ml-2"
                    onClick={() => setMobileMenuOpen(true)}
                    aria-label="Menü"
                    data-testid="mobile-menu-btn"
                  >
                    {/* Zara tarzı minimal hamburger — iki ince yatay çizgi */}
                    <svg width="22" height="10" viewBox="0 0 22 10" fill="none" aria-hidden="true">
                      <line x1="0" y1="1.5" x2="22" y2="1.5" stroke="currentColor" strokeWidth="1.1" />
                      <line x1="0" y1="8.5" x2="22" y2="8.5" stroke="currentColor" strokeWidth="1.1" />
                    </svg>
                  </button>

                  {/* Mobil logo — Mango tarzı: hamburger'ın hemen yanında, sol hizalı, küçük.
                      Masaüstünde gizli; masaüstü logosu aşağıdaki ortalanmış Link'te. */}
                  <Link to="/" className="lg:hidden flex-shrink-0" data-testid="header-logo-mobile" aria-label="FACETTE">
                    <img src="/logo.webp" alt="FACETTE" className={`h-4 ${heroOverlay ? "brightness-0 invert" : ""}`} />
                  </Link>

                  {/* iPad (lg, ~1024-1280px) sıkışması: sekmeler lg'de daha kompakt (küçük punto,
                      dar tracking, dar gap), xl'de tam boy — logoya binme/overlap engellenir. */}
                  <nav className="hidden lg:flex items-center gap-4 xl:gap-8 min-w-0 pr-3">
                    {/* Sekmeler TAMAMEN panelden (Tasarım > Menü Yönetimi): ad, sıra, tip (link/mega), stil.
                        style=accent → elmas işaretli premium stil; style=sale → kırmızı. */}
                    {visibleTabs.map((tab) => {
                      const isAccent = tab.style === "accent";
                      const isSale = tab.style === "sale";
                      const cls = isAccent
                        ? "group relative text-[11px] xl:text-xs font-bold tracking-[0.16em] xl:tracking-[0.28em] uppercase py-4 leading-none flex items-center gap-1.5 whitespace-nowrap"
                        : `text-[11px] xl:text-xs font-semibold tracking-[0.1em] xl:tracking-[0.2em] uppercase py-4 leading-none flex items-center hover:opacity-60 whitespace-nowrap${isSale ? " text-red-700" : ""}`;
                      const inner = (
                        <>
                          {isAccent && <span className="inline-block w-[5px] h-[5px] rotate-45 bg-current opacity-55 group-hover:opacity-100 transition-opacity duration-300" aria-hidden="true" />}
                          {tab.label}
                          {isAccent && <span className="pointer-events-none absolute left-0 bottom-2.5 h-px w-full bg-current origin-left scale-x-0 group-hover:scale-x-100 transition-transform duration-500 ease-out" aria-hidden="true" />}
                        </>
                      );
                      if (tab.type === "mega") {
                        return (
                          <div
                            key={tab.id}
                            className="relative"
                            onMouseEnter={() => openMenu(tab.id)}
                            onMouseLeave={scheduleClose}
                          >
                            <Link to={tab.link || "#"} className={cls} data-testid={`nav-tab-${tab.id}`}>{inner}</Link>
                          </div>
                        );
                      }
                      return (
                        <Link key={tab.id} to={tab.link || "#"} className={cls} data-testid={`nav-tab-${tab.id}`}>{inner}</Link>
                      );
                    })}
                  </nav>
                </>
              )}
            </div>

            {/* Center: Logo — sadece masaüstünde (mobil logo yukarıda, sol blokta) */}
            <Link to="/" className="hidden lg:block flex-shrink-0 mx-4" data-testid="header-logo">
              <img src="/logo.webp" alt="FACETTE" className={`h-5 xl:h-6 ${heroOverlay ? "brightness-0 invert" : ""}`} />
            </Link>

            {/* Right: Icons (mobile: search icon + account + favorites + cart, Mango tarzı; desktop: full set) */}
            <div className="flex-1 flex items-center justify-end gap-0.5 md:gap-3">
              {!isCheckout && (
                <>
                  <button onClick={() => setSearchOpen(true)} className="inline-flex flex-col items-center md:items-start justify-center p-2 md:p-0 md:py-1.5 md:pr-4 opacity-80 hover:opacity-100 transition-opacity" aria-label="Ara" data-testid="search-btn">
                    <span className="inline-flex items-center gap-1.5">
                      <Search size={17} strokeWidth={1.4} />
                      <span className="hidden md:inline text-[11px] tracking-[0.2em] uppercase leading-none">Ara</span>
                    </span>
                    <span className="hidden md:block mt-1.5 h-px bg-current w-36 md:w-48"></span>
                  </button>
                  {user && (
                    <Link to="/hesabim?tab=favorites" className="inline-flex p-2 hover:opacity-60 relative" aria-label="Kaydedilenler" data-testid="favorites-btn">
                      <Bookmark size={17} strokeWidth={1.4} />
                      {favCount > 0 && (
                        <span className="absolute top-1 right-1 min-w-[14px] h-[14px] px-0.5 bg-black text-white text-[8px] font-light rounded-full flex items-center justify-center">
                          {favCount}
                        </span>
                      )}
                    </Link>
                  )}
                  <Link to={user ? "/hesabim" : "/giris"} className="inline-flex p-2 hover:opacity-60" aria-label="Hesap">
                    <User size={17} strokeWidth={1.4} />
                  </Link>
                  <button onClick={() => setIsOpen(true)} className="p-2 hover:opacity-60 relative" aria-label="Sepet" data-testid="cart-btn">
                    <ShoppingBag size={17} strokeWidth={1.4} />
                    {itemCount > 0 && (
                      <span className="absolute top-1 right-1 min-w-[14px] h-[14px] px-0.5 bg-black text-white text-[8px] font-light rounded-full flex items-center justify-center">
                        {itemCount}
                      </span>
                    )}
                  </button>
                </>
              )}
            </div>
          </div>
        </div>

        {/* Full Width Mega Menu Dropdown — aktif mega sekme panelden gelir (tüm sekmeler için TEK şablon) */}
        {(() => {
          const tab = visibleTabs.find((t) => t.id === activeMenu && t.type === "mega");
          if (!tab) return null;
          const cols = megaColumnsOf(tab);
          if (!cols.length) return null;
          return (
          <div
            className="absolute left-0 right-0 top-full bg-white shadow-lg border-t z-50"
            onMouseEnter={cancelClose}
            onMouseLeave={scheduleClose}
          >
            <div className="max-w-[1800px] mx-auto px-8 py-6">
              <div className="flex gap-12">
                {/* Kolonlar — panele girilen başlık + alt kategoriler (birbirine yakın, genişliğe yayılmaz).
                    Hiçbir kolonda alt kategori yoksa (ör. koleksiyon listesi) başlıklar TEK kolonda ALT ALTA. */}
                {cols.every((c) => !c.items.length) ? (
                <ul className="space-y-1">
                  {cols.map((col) => (
                    <li key={col.title}>
                      <Link
                        to={col.link}
                        className="block py-1 text-sm text-gray-700 hover:text-black transition-colors"
                        onClick={() => setActiveMenu(null)}
                        onMouseEnter={() => setHoveredCategory(col.slug)}
                      >
                        {col.title}
                      </Link>
                    </li>
                  ))}
                </ul>
                ) : (
                <div className={MEGA_LINK_GRID} style={megaCols(cols.length || 3)}>
                  {cols.map((col) => (
                    // Kolon başlığına/alanına gelince o ana kategorinin ürünleri sağda çıksın
                    // (item'sız kolonlar — ör. koleksiyon başlıkları — dahil). Alt ürün hover'ı override eder.
                    <div key={col.title} onMouseEnter={() => setHoveredCategory(col.slug)}>
                      <Link
                        to={col.link}
                        className="block text-xs font-bold tracking-wider mb-3 text-gray-900 hover:underline cursor-pointer"
                        onClick={() => setActiveMenu(null)}
                        onMouseEnter={() => setHoveredCategory(col.slug)}
                      >
                        {col.title}
                      </Link>
                      <ul className="space-y-1">
                        {col.items.map((item) => (
                          <li key={item.slug}>
                            <Link
                              to={item.link}
                              className="block py-1 text-sm text-gray-600 hover:text-black transition-colors"
                              onClick={() => setActiveMenu(null)}
                              onMouseEnter={() => setHoveredCategory(item.slug)}
                            >
                              {item.name}
                            </Link>
                          </li>
                        ))}
                        <li className="pt-1.5">
                          <Link
                            to={col.link}
                            className="text-xs font-medium underline hover:no-underline"
                            onClick={() => setActiveMenu(null)}
                          >
                            Tümünü Gör
                          </Link>
                        </li>
                      </ul>
                    </div>
                  ))}
                </div>
                )}

                {/* Right: Hover edilen kategorinin en çok satan 3 ürünü */}
                <div className="flex-shrink-0 flex gap-3 min-w-[564px] ml-auto">
                  <MegaProductsPanel
                    products={activeMegaProducts}
                    loading={megaLoading}
                    fallback={MENU_IMAGES[tab.id] || MENU_IMAGES.giyim}
                    fallbackLink={tab.link || "/"}
                    onNavigate={() => setActiveMenu(null)}
                  />
                </div>
              </div>
            </div>
          </div>
          );
        })()}

        </header>
      </div>

      {/* Mobile Menu */}
      {!isCheckout && (
        <div className={`mobile-menu ${mobileMenuOpen ? "open" : ""}`}>
          <div className="flex items-center justify-between px-5 h-14 border-b border-black/5">
            <Link to="/" onClick={() => setMobileMenuOpen(false)}>
              <img src="/logo.webp" alt="FACETTE" className="h-5" />
            </Link>
            <button onClick={() => setMobileMenuOpen(false)} className="-mr-2 p-2" aria-label="Kapat">
              <X size={20} strokeWidth={1.4} />
            </button>
          </div>
          <nav className="overflow-y-auto h-[calc(100dvh-56px)] flex flex-col">
            {/* Primary Categories — sekmeler panelden (Tasarım > Menü Yönetimi) */}
            <div className="px-5 pt-6 pb-4">
              {visibleTabs.map((tab, ti) => {
                const isAccent = tab.style === "accent";
                const isSale = tab.style === "sale";
                const borderCls = ti === 0 ? "" : "border-t border-black/5";
                if (tab.type === "mega") {
                  const cols = megaColumnsOf(tab);
                  return (
                    <details key={tab.id} className={`group ${borderCls}`}>
                      <summary className="flex items-center justify-between py-3 cursor-pointer list-none">
                        <span className={`text-sm tracking-[0.15em] uppercase font-light${isSale ? " text-red-700" : ""}`}>{tab.label}</span>
                        <span className="text-base font-thin transition-transform group-open:rotate-45">+</span>
                      </summary>
                      <div className="pb-3 pl-1 space-y-3">
                        {cols.map((col) => (
                          <div key={col.title}>
                            {/* Alt kategorisi olmayan kolon (ör. koleksiyon) normal satır linki olarak ALT ALTA */}
                            <Link
                              to={col.link}
                              className={col.items.length
                                ? "block text-[10px] tracking-[0.25em] uppercase text-black/40 mb-1.5 hover:underline"
                                : "block py-1.5 text-[13px] font-light text-black/75"}
                              onClick={() => setMobileMenuOpen(false)}
                            >
                              {col.title}
                            </Link>
                            {col.items.map((item) => (
                              <Link
                                key={item.slug}
                                to={item.link}
                                className="block py-1.5 text-[13px] font-light text-black/75"
                                onClick={() => setMobileMenuOpen(false)}
                              >
                                {item.name}
                              </Link>
                            ))}
                          </div>
                        ))}
                        <Link
                          to={tab.link || "/"}
                          className="block py-1.5 mt-1 text-[13px] font-medium underline text-black/80"
                          onClick={() => setMobileMenuOpen(false)}
                        >
                          Tümünü Gör
                        </Link>
                      </div>
                    </details>
                  );
                }
                return (
                  <Link
                    key={tab.id}
                    to={tab.link || "/"}
                    className={`flex items-center gap-2 py-3 text-sm uppercase ${borderCls} ${isAccent ? "tracking-[0.18em] font-medium" : "tracking-[0.15em] font-light"}${isSale ? " text-red-700" : ""}`}
                    onClick={() => setMobileMenuOpen(false)}
                  >
                    {isAccent && <span className="inline-block w-[5px] h-[5px] rotate-45 bg-black" aria-hidden="true" />}
                    {tab.label}
                  </Link>
                );
              })}
            </div>

            {/* Bottom: Account + Service — mobil tarayıcı alt çubuğunun ARKASINDA kalmasın diye
                safe-area + ekstra alt boşlukla yukarı çekildi (100dvh ile birlikte). */}
            <div className="mt-auto px-5 pt-6 pb-[calc(2rem+env(safe-area-inset-bottom))] bg-stone-50 border-t border-black/5 space-y-2.5">
              <Link to={user ? "/hesabim" : "/giris"} className="block text-[13px] font-light text-black/85" onClick={() => setMobileMenuOpen(false)}>
                {user ? "Hesabım" : "Giriş Yap / Üye Ol"}
              </Link>
              <Link to="/siparis-takip" className="block text-[13px] font-light text-black/85" onClick={() => setMobileMenuOpen(false)}>
                Sipariş Takibi
              </Link>
              <Link to="/sayfa/iletisim" className="block text-[13px] font-light text-black/85" onClick={() => setMobileMenuOpen(false)}>
                İletişim
              </Link>
              <Link to="/iade-islemleri" className="block text-[13px] font-light text-black/85" onClick={() => setMobileMenuOpen(false)}>
                İade Talebi
              </Link>
            </div>
          </nav>
        </div>
      )}

      {/* Search Overlay */}
      {searchOpen && (
        <div className="fixed inset-x-0 top-0 h-[100dvh] bg-white z-[60] overflow-y-auto overscroll-contain" style={{ animation: "facetteSearchIn .2s ease-out" }}>
          {/* Mobil klavye düzeltmesi: 100dvh (dinamik viewport) — klavye açılınca kap
              küçülür, içerik klavyenin arkasında kalmaz; overscroll-contain ile arka
              sayfa kaymaz. Mobilde üst boşluklar kısaltıldı. */}
          <style>{`@keyframes facetteSearchIn{from{opacity:0}to{opacity:1}}@keyframes facetteSearchUp{from{opacity:0;transform:translateY(-10px)}to{opacity:1;transform:translateY(0)}}`}</style>
          <div className="px-5 md:px-10 pt-4 md:pt-5 pb-40 md:pb-16" style={{ animation: "facetteSearchUp .28s ease-out" }}>
            {/* Üst bar: logo + kapat */}
            <div className="flex items-center justify-between mb-4 md:mb-12">
              <Link to="/" onClick={closeSearch} aria-label="FACETTE"><img src="/logo.webp" alt="FACETTE" className="h-5" /></Link>
              <button onClick={closeSearch} className="inline-flex items-center gap-1.5 text-[11px] tracking-[0.18em] uppercase hover:opacity-60 transition-opacity" aria-label="Kapat">
                Kapat <X size={18} strokeWidth={1.4} />
              </button>
            </div>

            {/* 3 kolon: sol kategori | orta arama | sağ hesap (Zara düzeni) */}
            <div className="grid grid-cols-1 md:grid-cols-[170px_1fr_170px] gap-8 md:gap-12 items-start mb-5 md:mb-16">
              {/* SOL: kategoriler (desktop) */}
              <nav className="hidden md:flex flex-col gap-3.5 text-[11px] tracking-[0.18em] uppercase">
                {visibleTabs.map((t) => (
                  <Link key={t.id} to={t.link || "/"} onClick={closeSearch} className={`hover:opacity-60 transition-opacity${t.style === "sale" ? " text-red-700" : ""}`}>{t.label}</Link>
                ))}
                <Link to="/" onClick={closeSearch} className="hover:opacity-60 transition-opacity">Ana Sayfa</Link>
              </nav>

              {/* ORTA: büyük arama girişi */}
              <form onSubmit={(e) => { e.preventDefault(); submitSearch(); }} className="w-full">
                <div className="relative border-b border-black max-w-2xl mx-auto md:max-w-4xl md:mx-0">
                  <input
                    type="text"
                    value={searchQuery}
                    onChange={(e) => setSearchQuery(e.target.value)}
                    placeholder="Ne arıyorsunuz?"
                    className="w-full text-xl md:text-3xl font-light py-2.5 pr-10 bg-transparent focus:outline-none placeholder:text-gray-300 text-center md:text-left"
                    autoFocus
                  />
                  {searchQuery ? (
                    <button type="button" onClick={() => setSearchQuery("")} className="absolute right-0 top-1/2 -translate-y-1/2 text-gray-400 hover:text-black transition-colors" aria-label="Temizle"><X size={20} strokeWidth={1.3} /></button>
                  ) : (
                    <Search size={22} strokeWidth={1.2} className="absolute right-0 top-1/2 -translate-y-1/2 text-gray-400" />
                  )}
                </div>
              </form>

              {/* SAĞ: hesap / sepet (desktop) */}
              <nav className="hidden md:flex flex-col gap-3.5 text-[11px] tracking-[0.18em] uppercase md:items-end">
                <button onClick={() => { closeSearch(); setIsOpen(true); }} className="hover:opacity-60 transition-opacity">Sepet{itemCount > 0 ? ` (${itemCount})` : ""}</button>
                <Link to={user ? "/hesabim" : "/giris"} onClick={closeSearch} className="hover:opacity-60 transition-opacity">{user ? "Hesabım" : "Giriş Yap"}</Link>
                {user && (
                  <Link to="/hesabim?tab=favorites" onClick={closeSearch} className="hover:opacity-60 transition-opacity">Kaydedilenler{favCount > 0 ? ` (${favCount})` : ""}</Link>
                )}
              </nav>

              {/* Mobil: kategori + hesap linkleri yatay (md'de gizli) */}
              <div className="md:hidden flex flex-wrap justify-center gap-x-5 gap-y-2.5 text-[11px] tracking-[0.15em] uppercase pt-1">
                <Link to="/giyim" onClick={closeSearch}>Giyim</Link>
                <Link to="/aksesuar" onClick={closeSearch}>Aksesuar</Link>
                <Link to="/sale" onClick={closeSearch} className="text-red-700">Sale</Link>
                <Link to={user ? "/hesabim" : "/giris"} onClick={closeSearch}>{user ? "Hesabım" : "Giriş"}</Link>
                {user && <Link to="/hesabim?tab=favorites" onClick={closeSearch}>Kaydedilenler</Link>}
              </div>
            </div>

            {/* ALT: ürünler — boş aramada öneri, yazınca canlı sonuç */}
            {searchQuery.length === 0 ? (
              suggestedProducts.length > 0 && (
                <div>
                  <h3 className="text-[10px] tracking-widest uppercase text-gray-400 mb-5">İlginizi çekebilecek diğer ürünler</h3>
                  <div className="grid grid-cols-2 md:grid-cols-4 gap-x-4 gap-y-8">
                    {suggestedProducts.map((p) => (
                      <button key={p.id} onClick={() => { navigate(`/${p.slug}`); closeSearch(); }} className="text-left group">
                        <div className="relative aspect-[2/3] bg-gray-50 mb-2.5 overflow-hidden">
                          <MegaDiscountBadge p={p} />
                          <img src={optimizeImg(p.images?.[0], 500)} alt={p.name} className="w-full h-full object-cover group-hover:scale-105 transition-transform duration-500" loading="lazy" decoding="async" />
                        </div>
                        <p className="text-xs font-light line-clamp-1 mb-0.5">{p.name}</p>
                        <MegaPrice p={p} />
                      </button>
                    ))}
                  </div>
                </div>
              )
            ) : searchResults.length > 0 ? (
              <div>
                <h3 className="text-[10px] tracking-widest uppercase text-gray-400 mb-5">Ürünler</h3>
                <div className="grid grid-cols-2 md:grid-cols-4 gap-x-4 gap-y-8">
                  {searchResults.map((p) => (
                    <button key={p.id} onClick={() => { navigate(`/${p.slug}`); closeSearch(); }} className="text-left group">
                      <div className="relative aspect-[2/3] bg-gray-50 mb-2.5 overflow-hidden">
                        <MegaDiscountBadge p={p} />
                        <img src={optimizeImg(p.images?.[0], 500)} alt={p.name} className="w-full h-full object-cover group-hover:scale-105 transition-transform duration-500" loading="lazy" decoding="async" />
                      </div>
                      <p className="text-xs font-light line-clamp-1 mb-0.5">{p.name}</p>
                      <MegaPrice p={p} />
                    </button>
                  ))}
                </div>
                <button onClick={() => submitSearch()} className="mt-10 text-[11px] tracking-[0.18em] uppercase border-b border-black pb-1 hover:opacity-60 transition-opacity">
                  {searchTotal > searchResults.length ? `Tüm sonuçları gör (${searchTotal})` : "Tüm sonuçları gör"}
                </button>
              </div>
            ) : (
              <p className="text-sm font-light text-gray-400">"{searchQuery}" için sonuç bulunamadı.</p>
            )}
          </div>
        </div>
      )}

      <CartDrawer />
    </>
  );
}
