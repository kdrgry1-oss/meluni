import { useState, useEffect } from "react";
import { useParams, Link, useNavigate } from "react-router-dom";
import { sanitizeHtml } from "../lib/sanitizeHtml";
import { X, Bookmark, ChevronUp, ChevronDown, Check, Truck, Star, RotateCcw, CreditCard, Clock, Pencil, ZoomIn, Share2, Link2 } from "lucide-react";
import axios from "axios";
import { toast } from "sonner";
import Header from "../components/Header";
import Footer from "../components/Footer";
import ProductCard from "../components/ProductCard";
import { optimizeImg } from "../lib/img";
import { slugify } from "../lib/slug";
import { priceView } from "../lib/price";
import { isRecommendedSize, recommendLetterSize } from "../lib/sizeRecommend";
import { useCart } from "../context/CartContext";
import { useFavorites } from "../context/FavoritesContext";
import { useAuth } from "../context/AuthContext";
import { useShipping } from "../lib/shipping";
import { trackViewContent, trackAddToCart } from "../utils/pixelEvents";
import { sortLikeSize } from "../utils/sizeSort";

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;

// Tahmini teslimat aralığı (kargoya verilme + iş günü; hafta sonu atlanır)
function estimateDelivery(minDays = 2, maxDays = 4) {
  const addBiz = (base, n) => {
    const r = new Date(base);
    let added = 0;
    while (added < n) {
      r.setDate(r.getDate() + 1);
      const wd = r.getDay();
      if (wd !== 0 && wd !== 6) added++;
    }
    return r;
  };
  const fmt = (d) => d.toLocaleDateString("tr-TR", { day: "numeric", month: "long" });
  const now = new Date();
  return `${fmt(addBiz(now, minDays))} - ${fmt(addBiz(now, maxDays))}`;
}

// "xx saat yy dakika içinde kargoda" kargo aciliyeti + geri sayım.
// KURAL: Kargo YALNIZ iş günlerinde (Cmt/Paz + resmi tatiller hariç). Cutoff 10:30.
//  • İş günü ve 10:30'dan ÖNCE → bugün kargoda + geri sayım ("S saat D dakika içinde kargoda")
//  • İş günü 10:30'dan SONRA (gün dönmese de) → bir sonraki İŞ GÜNÜ kargoda ("yarın kargoda")
//  • Hafta sonu / resmi tatil → sonraki iş günü ("Pazartesi kargoda")
// Geri sayımda yalnız SAAT ve DAKİKA gösterilir; saniye gösterilmez (dakikada bir güncellenir).
const _TR_DAYS = ["Pazar", "Pazartesi", "Salı", "Çarşamba", "Perşembe", "Cuma", "Cumartesi"];
// Resmi tatiller. Sabit ulusal bayramlar MM-DD; her yıl kayan dini bayramlar YYYY-MM-DD
// olarak eklenir (aşağıya ilgili yılın Ramazan/Kurban tarihleri girilebilir).
const TR_PUBLIC_HOLIDAYS = new Set([
  "01-01", // Yılbaşı
  "04-23", // Ulusal Egemenlik ve Çocuk Bayramı
  "05-01", // Emek ve Dayanışma Günü
  "05-19", // Atatürk'ü Anma, Gençlik ve Spor Bayramı
  "07-15", // Demokrasi ve Millî Birlik Günü
  "08-30", // Zafer Bayramı
  "10-29", // Cumhuriyet Bayramı
  // Dini bayramlar (değişken tarihli) — örn: "2026-03-20", "2026-05-27" ...
]);
function _isHolidayDate(d, extraHolidays) {
  const mm = String(d.getMonth() + 1).padStart(2, "0");
  const dd = String(d.getDate()).padStart(2, "0");
  const iso = `${d.getFullYear()}-${mm}-${dd}`;
  if (extraHolidays && extraHolidays.has(iso)) return true;
  return TR_PUBLIC_HOLIDAYS.has(`${mm}-${dd}`) || TR_PUBLIC_HOLIDAYS.has(iso);
}
function _isWorkingDay(d, cfg) {
  const wd = d.getDay(); // 0=Paz..6=Cmt
  const iso = wd === 0 ? 7 : wd; // 1=Pzt..7=Paz
  const workDays = (cfg && cfg.workDays) || [1, 2, 3, 4, 5];
  const holidays = cfg && cfg.holidays;
  const exclude = !cfg || cfg.exclude !== false;
  if (!workDays.includes(iso)) return false;
  if (exclude && _isHolidayDate(d, holidays)) return false;
  return true;
}
// AYAR: kesim saati / çalışma günleri / resmî tatiller İşletme Kuralları'ndan (cfg) gelir;
// cfg verilmezse varsayılan 12:00 + hafta içi + sabit millî tatiller.
// KURAL (kullanıcı isteği): HER ZAMAN canlı geri sayım göster (mesai saatlerine göre).
//  • İş günü ve cutoff'tan (12:00) ÖNCE → "X saat Y dakika içinde sipariş verirsen bugün kargoda"
//    ("bugün kargoda" yeşil). Aynı gün → direkt saat/dakika sayar.
//  • Cutoff geçti / hafta sonu / tatil → sonraki İŞ GÜNÜ cutoff'una kadar geri sayım
//    "1 gün X saat içinde sipariş verirsen en geç yarın kargoda" ("yarın kargoda" yeşil).
function shippingCutoff(cfg) {
  const cutoffHour = cfg && cfg.cutoffHour != null ? cfg.cutoffHour : 12;
  const cutoffMinute = cfg && cfg.cutoffMinute != null ? cfg.cutoffMinute : 0;
  const now = new Date();

  const todayCutoff = new Date(now); todayCutoff.setHours(cutoffHour, cutoffMinute, 0, 0);
  const sameDay = _isWorkingDay(now, cfg) && now.getTime() < todayCutoff.getTime();

  let target, greenLabel, sameDayMode;
  if (sameDay) {
    target = todayCutoff;
    greenLabel = "bugün kargoda";
    sameDayMode = true;
  } else {
    // Sonraki İŞ GÜNÜ (hafta sonu + tatil atlanır) cutoff'u
    const next = new Date(now); next.setHours(0, 0, 0, 0);
    do { next.setDate(next.getDate() + 1); } while (!_isWorkingDay(next, cfg));
    target = new Date(next); target.setHours(cutoffHour, cutoffMinute, 0, 0);
    const tomorrow = new Date(now); tomorrow.setHours(0, 0, 0, 0); tomorrow.setDate(tomorrow.getDate() + 1);
    const isTomorrow = next.toDateString() === tomorrow.toDateString();
    greenLabel = `${isTomorrow ? "yarın" : _TR_DAYS[next.getDay()]} kargoda`;
    sameDayMode = false;
  }

  // Geri sayım (hedef cutoff'a kadar). Gün varsa "G gün S saat", yoksa "S saat D dakika".
  const remMs = Math.max(0, target.getTime() - now.getTime());
  const days = Math.floor(remMs / 86400000);
  const h = Math.floor((remMs % 86400000) / 3600000);
  const m = Math.floor((remMs % 3600000) / 60000);
  let left;
  if (days >= 1) left = `${days} gün ${h} saat`;
  else if (h >= 1) left = `${h} saat ${m} dakika`;
  else left = `${m} dakika`;

  return {
    countdown: true,
    // Aynı gün → "...sipariş verirsen bugün kargoda"; değilse → "...en geç yarın kargoda"
    prefix: sameDayMode
      ? `${left} içinde sipariş verirsen`
      : `${left} içinde sipariş verirsen en geç`,
    green: greenLabel,   // yeşil vurgulanacak kısım
  };
}

// Son gezilen ürünler — localStorage'da küçük anlık görüntü (snapshot) listesi.
const RV_KEY = "facette_recently_viewed";
const readRecentlyViewed = () => {
  try { return JSON.parse(localStorage.getItem(RV_KEY) || "[]"); } catch { return []; }
};
const pushRecentlyViewed = (snap) => {
  if (!snap || !snap.id) return;
  try {
    const list = readRecentlyViewed().filter((x) => x && x.id !== snap.id);
    list.unshift(snap);
    localStorage.setItem(RV_KEY, JSON.stringify(list.slice(0, 12)));
  } catch { /* sessiz */ }
};

export default function ProductDetail() {
  const { slug } = useParams();
  const navigate = useNavigate();
  const { addItem, isOpen: cartOpen } = useCart();
  const { isFavorite, toggleFavorite } = useFavorites();
  const { freeShippingThreshold } = useShipping();
  const { user } = useAuth();
  // A5: kargo geri sayım kuralları (kesim saati / çalışma günleri / resmî tatiller) İşletme
  // Kuralları'ndan gelir — kodda sabit değil. Her dakika yeniden hesaplanır.
  const [shipCfg, setShipCfg] = useState(null);
  const [, _tickShip] = useState(0);
  useEffect(() => {
    axios.get(`${API}/business-rules`).then((r) => {
      const d = r.data || {};
      const [ch, cm] = String(d["shipping.same_day_cutoff"] || "12:00").split(":").map((x) => parseInt(x, 10));
      setShipCfg({
        cutoffHour: ch, cutoffMinute: cm,
        workDays: Array.isArray(d["shipping.work_days"]) ? d["shipping.work_days"] : [1, 2, 3, 4, 5],
        holidays: new Set(d["official_holidays"] || []),
        exclude: d["shipping.exclude_official_holidays"] !== false,
        // Vitrin görünüm anahtarları (İşletme Kuralları → varsayılan açık)
        showSizeGuide: d["product.size_guide_enabled"] !== false,
        showCompleteLook: d["product.complete_the_look_enabled"] !== false,
        showCountdown: d["product.shipping_countdown_enabled"] !== false,
        showSocialShare: d["product.social_share_enabled"] !== false,
        lowStockBadge: d["product.low_stock_badge_enabled"] !== false,
        lowStockThreshold: Number(d["product.low_stock_badge_threshold"] ?? 5),
      });
    }).catch(() => {});
  }, []);
  useEffect(() => { const t = setInterval(() => _tickShip((n) => n + 1), 60000); return () => clearInterval(t); }, []);

  // İade ve Değişim akordiyonu — merchant'ın 'iade-kosullari' sayfasını DİNAMİK gösterir
  // (sayfa güncellenince akordiyon da güncellenir). Fetch başarısızsa sabit kısa metne düşer.
  const [returnPolicyHtml, setReturnPolicyHtml] = useState("");
  useEffect(() => {
    let alive = true;
    axios.get(`${API}/pages/iade-kosullari`)
      .then((r) => { if (alive) setReturnPolicyHtml(r?.data?.content || ""); })
      .catch(() => { /* sessiz — fallback metin gösterilir */ });
    return () => { alive = false; };
  }, []);
  // Üyenin boy/kilosuna göre önerilen beden (harf) — beden butonunda rozet gösterilir.
  const recLetter = user ? recommendLetterSize(user.height_cm, user.weight_kg) : null;
  const [product, setProduct] = useState(null);
  const [similarProducts, setSimilarProducts] = useState([]);
  const [comboProducts, setComboProducts] = useState([]);
  const [recentItems, setRecentItems] = useState([]); // son gezilenler (önceki sayfalardan)
  // Son gezilen kartların fiyat/kampanyası CANLI tazelenir: localStorage'daki eski
  // anlık görüntüler kampanyayı bilmediğinden indirimli (kırmızı) fiyat görünmüyordu.
  // Sepetle aynı sunucu-otoriter kaynak: POST /products/cart-pricing.
  const _rvIdsKey = recentItems.map((x) => x && x.id).filter(Boolean).join(",");
  useEffect(() => {
    if (!_rvIdsKey) return;
    let cancel = false;
    axios.post(`${API}/products/cart-pricing`, { product_ids: _rvIdsKey.split(",") })
      .then((r) => {
        if (cancel || !r.data?.items) return;
        setRecentItems((prev) => {
          let changed = false;
          const next = prev.map((it) => {
            const info = r.data.items[it.id];
            if (!info) return it;
            const patch = {
              price: info.price, sale_price: info.sale_price,
              campaign_discount_percent: info.campaign_discount_percent,
            };
            if (Number(it.price) === Number(info.price) && Number(it.sale_price || 0) === Number(info.sale_price || 0)
                && Number(it.campaign_discount_percent || 0) === Number(info.campaign_discount_percent || 0)) return it;
            changed = true;
            return { ...it, ...patch };
          });
          return changed ? next : prev;
        });
      })
      .catch(() => {});
    return () => { cancel = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [_rvIdsKey]);
  const [loading, setLoading] = useState(true);
  const [selectedImage, setSelectedImage] = useState(0);
  // Masaüstü büyük görsel büyüteci (hover → imlecin olduğu bölge büyür)
  const [zoom, setZoom] = useState({ on: false, x: 50, y: 50 });
  const [selectedSize, setSelectedSize] = useState(null);
  const [selectedVariant, setSelectedVariant] = useState(null);
  const [quantity, setQuantity] = useState(1);
  // Kargo geri sayımı: dakikada bir yeniden render (saniye gösterilmez, saat+dakika canlı düşer)
  const [, setShipTick] = useState(0);
  useEffect(() => {
    const id = setInterval(() => setShipTick((t) => t + 1), 30000);
    return () => clearInterval(id);
  }, []);
  const [showSizeChart, setShowSizeChart] = useState(false);
  const [showStickyHeader, setShowStickyHeader] = useState(false);
  const [mobileImageIdx, setMobileImageIdx] = useState(0);
  const [expandedSections, setExpandedSections] = useState({
    description: true,   // Ürün Özellikleri varsayılan açık — bilgi gizli accordion'da kalmasın
    shipping: false,
    returns: false
  });
  // Sepete eklendi mikro-etkileşimi: buton kısa süre "Eklendi ✓" gösterir
  const [justAdded, setJustAdded] = useState(false);
  // Size Table (HTML) - fetched via public endpoint. Hooks must live at top level.
  const [sizeTableData, setSizeTableData] = useState(null);
  // "Gelince Haber Ver" — stokta olmayan beden için e-posta toplama
  const [notifyOpen, setNotifyOpen] = useState(false);
  const [notifyEmail, setNotifyEmail] = useState("");
  const [notifySubmitting, setNotifySubmitting] = useState(false);

  // Ürün değerlendirmeleri (yorum + puan). Backend: GET /reviews/product/:id (onaylı),
  // POST /reviews (auth, moderasyon → pending). Admin onayı sonrası listede görünür.
  const [reviews, setReviews] = useState([]);
  const [reviewAvg, setReviewAvg] = useState(0);
  const [reviewTotal, setReviewTotal] = useState(0);
  const [rvRating, setRvRating] = useState(0);
  const [rvTitle, setRvTitle] = useState("");
  const [rvComment, setRvComment] = useState("");
  const [rvSubmitting, setRvSubmitting] = useState(false);
  const rvLoggedIn = !!localStorage.getItem("token");

  useEffect(() => {
    if (!product?.id) return;
    (async () => {
      try {
        const { data } = await axios.get(`${API}/reviews/product/${product.id}`);
        setReviews(data.items || []);
        setReviewAvg(data.average_rating || 0);
        setReviewTotal(data.total || 0);
      } catch { /* yorum yoksa sessiz geç */ }
    })();
  }, [product?.id]);

  const submitReview = async () => {
    if (rvRating < 1) { toast.error("Lütfen 1-5 yıldız seçin"); return; }
    setRvSubmitting(true);
    try {
      await axios.post(`${API}/reviews`,
        { product_id: product.id, rating: rvRating, title: rvTitle, comment: rvComment },
        { headers: { Authorization: `Bearer ${localStorage.getItem("token")}` } });
      toast.success("Yorumunuz alındı, moderasyon sonrası yayınlanacak");
      setRvRating(0); setRvTitle(""); setRvComment("");
    } catch (e) {
      toast.error(e?.response?.data?.detail || "Yorum gönderilemedi");
    } finally {
      setRvSubmitting(false);
    }
  };

  // SEO — JSON-LD yapısal veri (Product + BreadcrumbList). react-helmet yok,
  // bu yüzden <head>'e script'i elle enjekte edip temizliyoruz. Google'ın
  // "rich results" (fiyat, stok, marka) göstermesini sağlar.
  useEffect(() => {
    if (!product) return;
    // Edge SEO middleware (functions/_middleware.js) zaten JSON-LD bastıysa
    // client tarafında tekrar ekleme — Google'da duplicate Product/Breadcrumb olmasın.
    if (typeof document !== "undefined" && document.querySelector('script[data-seo="edge"]')) return;
    const origin = (typeof window !== "undefined" && window.location && window.location.origin) || "https://facette.com.tr";
    const canonical = `${origin}/urun/${product.slug || product.id}`;
    const price = product.sale_price || product.price;
    const inStock = Array.isArray(product.variants) && product.variants.length > 0
      ? product.variants.some((v) => (v.stock || 0) > 0)
      : true;

    const productLd = {
      "@context": "https://schema.org/",
      "@type": "Product",
      name: product.name,
      image: Array.isArray(product.images) && product.images.length ? product.images : undefined,
      description: product.description || product.name,
      sku: product.barcode || product.stock_code || product.id,
      brand: product.brand ? { "@type": "Brand", name: product.brand } : undefined,
      offers: {
        "@type": "Offer",
        url: canonical,
        priceCurrency: "TRY",
        price: price != null ? String(price) : undefined,
        availability: inStock ? "https://schema.org/InStock" : "https://schema.org/OutOfStock",
      },
    };

    const crumbs = [{ name: "Ana Sayfa", item: origin }];
    if (product.category_name) {
      crumbs.push({ name: product.category_name, item: `${origin}/${slugify(product.category_name || product.category_slug || "")}` });
    }
    crumbs.push({ name: product.name, item: canonical });
    const breadcrumbLd = {
      "@context": "https://schema.org/",
      "@type": "BreadcrumbList",
      itemListElement: crumbs.map((c, i) => ({
        "@type": "ListItem",
        position: i + 1,
        name: c.name,
        item: c.item,
      })),
    };

    const tag = document.createElement("script");
    tag.type = "application/ld+json";
    tag.setAttribute("data-seo", "product");
    tag.text = JSON.stringify([productLd, breadcrumbLd]);
    // Önceki kalmışsa temizle, sonra ekle
    document.querySelectorAll('script[data-seo="product"]').forEach((el) => el.remove());
    document.head.appendChild(tag);

    return () => {
      document.querySelectorAll('script[data-seo="product"]').forEach((el) => el.remove());
    };
  }, [product]);

  useEffect(() => {
    window.scrollTo(0, 0);
  }, [slug]);

  // Sticky header on scroll
  useEffect(() => {
    const handleScroll = () => {
      setShowStickyHeader(window.scrollY > 400);
    };
    window.addEventListener('scroll', handleScroll);
    return () => window.removeEventListener('scroll', handleScroll);
  }, []);

  // Fetch HTML size table whenever product loads
  useEffect(() => {
    const pid = product?.id;
    if (!pid) return;
    axios.get(`${API}/size-tables-public/${pid}`)
      .then(res => { if (res.data?.exists) setSizeTableData(res.data); })
      .catch(() => { /* no table */ });
  }, [product?.id]);

  useEffect(() => {
    const controller = new AbortController();
    let cancel = false;
    // Ürünler arası geçişte (A→B) önceki ürünün seçili varyantı/bedeni KALMASIN —
    // aksi halde B'nin bedeni stoksuzken A'nın varyantı sepete B ürünüyle düşüyordu.
    setSelectedVariant(null);
    setSelectedSize("");
    (async () => {
      try {
        const res = await axios.get(`${API}/products/${slug}`, { signal: controller.signal });
        if (cancel) return;   // A→B hızlı geçişte eski ürünün geç dönen verisi yeni sayfayı EZMESİN
        setProduct(res.data);

        // Son gezilenler: önceki listeyi (mevcut ürün hariç) göster, sonra mevcut
        // ürünü snapshot olarak kaydet (bir sonraki sayfada görünsün).
        try {
          const _p = res.data;
          setRecentItems(readRecentlyViewed().filter((x) => x && x.id !== _p.id).slice(0, 10));
          const _rvImg = (() => {
            const im = (_p.images && _p.images[0]) || _p.image || "";
            return (typeof im === "object" && im !== null) ? (im.url || im.src || "") : im;
          })();
          pushRecentlyViewed({
            id: _p.id,
            name: _p.name,
            slug: _p.slug || _p.id,
            image: _rvImg,
            price: _p.price,
            sale_price: _p.sale_price,
            // Kampanya olmadan kaydedilirse kartta indirim görünmüyordu
            campaign_discount_percent: _p.campaign_discount_percent,
          });
        } catch { /* sessiz */ }

        // Canonical URL: ürün id/eski slug ile açıldıysa doğru slug'a yönlendir (SEO + tutarlı URL)
        if (res.data?.slug && res.data.slug !== slug) {
          navigate(`/${res.data.slug}`, { replace: true });
        }

        // Varsayılan/ilk uygun bedeni önce belirle — ViewContent'in Meta content_id'si
        // seçili bedenin Ticimax varyant id'si olsun (katalog eşleşmesi için).
        // ?beden=XL ile gelindiyse (WhatsApp/AI linki) o bedeni ön-seç (stokluysa).
        let _wantSize = "";
        try { _wantSize = (new URLSearchParams(window.location.search).get("beden") || "").trim(); } catch { _wantSize = ""; }
        const _bySize = _wantSize
          ? (res.data?.variants || []).find((v) => String(v.size || "").toUpperCase() === _wantSize.toUpperCase() && (v.stock || 0) > 0)
          : null;
        const defaultVariant =
          _bySize ||
          (res.data?.variants || []).find((v) => (v.stock || 0) > 0) ||
          (res.data?.variants || [])[0] || null;

        // FAZ 9+ — Pixel ViewContent event
        trackViewContent({
          product_id: res.data.id,
          name: res.data.name,
          category: res.data.category_name,
          price: res.data.sale_price || res.data.price,
          variant_id: defaultVariant?.id,
        });

        if (defaultVariant && (defaultVariant.stock || 0) > 0) {
          setSelectedVariant(defaultVariant);
          setSelectedSize(defaultVariant.size);
        }

        // Benzer ürünler — kategori bazlı. (Eski /similar endpoint'i backend'de
        // yok ve her seferinde 404 dönüyordu; doğrudan kategori sorgusu kullanılıyor.)
        try {
          if (res.data?.category_name) {
            const fallbackRes = await axios.get(`${API}/products?category=${encodeURIComponent(res.data.category_name)}&limit=4`);
            setSimilarProducts(fallbackRes.data?.products?.filter(p => p.id !== res.data.id) || []);
          }
        } catch {
          // benzer ürün getirilemezse sessizce geç
        }

        // Fetch combo products ("Stilini tamamla")
        try {
          const comboRes = await axios.get(`${API}/products/${res.data.id}/combine-products`);
          const comboItems = comboRes.data?.items || [];
          // YALNIZ admin'in atadığı kombin gösterilir; otomatik öneri fallback'i KALDIRILDI
          // (kullanıcı isteği: kombin silinince alan boşalsın, kategori-bazlı ürünler dolmasın).
          setComboProducts(comboItems);
        } catch {
          setComboProducts([]);
        }
      } catch (err) {
        if (cancel || axios.isCancel?.(err) || err.name === "CanceledError") return;
        console.error(err);
      } finally {
        if (!cancel) setLoading(false);
      }
    })();
    return () => { cancel = true; controller.abort(); };
  }, [slug]);

  const handleAddToCart = () => {
    if (product.variants?.length > 0 && !selectedVariant) {
      toast.error("Lütfen beden seçiniz");
      return;
    }

    // Varyantsız ürünlerde ürün stoğu yoksa engelle (tükendi)
    if (!(product.variants?.length > 0) && (Number(product.stock) || 0) <= 0) {
      toast.error("Bu ürün tükendi");
      return;
    }
    
    // Check stock for selected variant (string/null/negatif stoğa karşı sağlam)
    const _selStock = Number(selectedVariant?.stock);
    if (selectedVariant && (!Number.isFinite(_selStock) || _selStock < quantity)) {
      toast.error(_selStock > 0 ? `Yetersiz stok! Mevcut stok: ${_selStock}` : "Bu beden tükendi");
      return;
    }
    
    addItem(product, selectedVariant, quantity);
    // FAZ 9+ — Pixel AddToCart event
    trackAddToCart({
      product_id: product.id,
      name: product.name,
      category: product.category_name,
      price: selectedVariant?.price || product.sale_price || product.price,
      quantity,
      size: selectedVariant?.size,
      color: selectedVariant?.color,
      variant_id: selectedVariant?.id,
    });
    toast.success(selectedVariant 
      ? `${product.name} - ${selectedVariant.size} sepete eklendi` 
      : "Ürün sepete eklendi"
    );
    setJustAdded(true);
    setTimeout(() => setJustAdded(false), 1600);
  };

  const handleSizeSelect = (variant) => {
    setSelectedVariant(variant);
    setSelectedSize(variant.size);
    // Beden değişince açık bildirim formunu kapat (stoklu bedene geçilirse gizlenir)
    if (variant.stock > 0) setNotifyOpen(false);
    // Beden değişince ViewContent'i seçili bedenin Meta katalog id'siyle yeniden gönder
    // (Meta content_id = o bedenin Ticimax varyant id'si — her beden için doğru eşleşme).
    if (product) {
      trackViewContent({
        product_id: product.id,
        name: product.name,
        category: product.category_name,
        price: variant.price || product.sale_price || product.price,
        variant_id: variant.id,
      });
    }
  };

  const handleStockNotify = async () => {
    const email = notifyEmail.trim();
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
      toast.error("Lütfen geçerli bir e-posta adresi giriniz");
      return;
    }
    setNotifySubmitting(true);
    try {
      const res = await axios.post(`${API}/stock-notify`, {
        product_id: product.id,
        size: selectedVariant?.size || selectedSize || "",
        email,
      });
      toast.success(res.data?.message || "Talebiniz alındı, stoğa girince haber vereceğiz.");
      setNotifyOpen(false);
      setNotifyEmail("");
    } catch (err) {
      toast.error(err.response?.data?.detail || "Bir hata oluştu, lütfen tekrar deneyin.");
    } finally {
      setNotifySubmitting(false);
    }
  };

  // Y28: Varyantsız ürünlere UYDURMA beden (XS–XL) ve sahte stok (10) üretilmez — bu sahte
  // varyantlar sepete gerçek dışı beden/stok yazıp mükerrer satır ve yanlış sipariş oluşturuyordu.
  // Varyantsız ürün beden seçici göstermez; ekleme varyantsız (tek ürün) olarak yapılır.
  const sizes = product?.variants?.length > 0
    ? sortLikeSize(product.variants, v => v.size)
    : [];

  if (loading) {
    return (
      <div className="min-h-screen">
        <Header />
        <div className="max-w-screen-2xl mx-auto px-4 py-8">
          <div className="grid md:grid-cols-2 gap-8">
            <div className="aspect-[2/3] bg-gray-100 animate-pulse" />
            <div className="space-y-4">
              <div className="h-8 bg-gray-100 w-3/4 animate-pulse" />
              <div className="h-6 bg-gray-100 w-1/3 animate-pulse" />
            </div>
          </div>
        </div>
        <Footer />
      </div>
    );
  }

  if (!product) {
    return (
      <div className="min-h-screen">
        <Header />
        <div className="max-w-screen-2xl mx-auto px-4 py-16 text-center">
          <p className="text-gray-500">Ürün bulunamadı</p>
          <Link to="/" className="btn-primary mt-4 inline-block">Ana Sayfaya Dön</Link>
        </div>
        <Footer />
      </div>
    );
  }

  // İndirim görünümü TEK KAYNAK (lib/price) — vitrin kartlarıyla BİREBİR aynı; ürün detayı
  // ile kartlar arasında "kartta indirimli ama detayda liste fiyatı" tutarsızlığı olmaz.
  const _pv = priceView(product);
  // Sepetle AYNI mantık: bir varyant price_adjustment taşıyıp price_diff taşımıyorsa
  // PDP farksız fiyat gösterip sepete farklı (yüksek) fiyat ekliyordu → gösterilen=çekilen.
  const variantPriceDiff = selectedVariant?.price_diff || selectedVariant?.price_adjustment || 0;
  const hasDiscount = _pv.hasDiscount;
  const listUnit = _pv.list + variantPriceDiff;
  const displayPrice = _pv.display + variantPriceDiff;

  // Remove duplicate images and hide size-table images from customer view
  const allImages = product.images || [];
  const uniqueImages = allImages.length > 1 && allImages[0] === allImages[1] ? allImages.slice(1) : allImages;
  // Ölçü tablosu görseli: tablo VERİSİ girilmemiş ürünlerde modal bu görseli gösterir
  // (aksi hâlde beden tablosuna hiçbir cihazdan erişilemiyordu).
  const sizeTableImg = (() => {
    for (const img of allImages) {
      if (typeof img === "object" && img !== null && img.is_size_table && img.url) return img.url;
    }
    return null;
  })();

  // Ölçü tablosu görselleri {url, is_size_table:true} dict'i olarak işaretli — müşteriden gizle.
  // Kalanları URL string'e normalize et (dict gelse bile <img src> kırılmasın).
  const displayImages = uniqueImages
    .filter((img) => !(typeof img === 'object' && img !== null && img.is_size_table))
    .map((img) => (typeof img === 'object' && img !== null ? (img.url || img.src || img.image || '') : img))
    .filter(Boolean);

  const toggleSection = (section) => {
    setExpandedSections(prev => ({
      ...prev,
      [section]: !prev[section]
    }));
  };

  return (
    <div className="min-h-screen">
      <Header />

      {/* Sticky Product Bar — mobile: bottom, desktop: top.
          Sepet çekmecesi AÇIKKEN gizlenir: yoksa çekmecenin "Ödemeye Geç" butonunu örter. */}
      {showStickyHeader && !cartOpen && (
        <div className="fixed left-0 right-0 z-50 bg-white border-t md:border-t-0 md:border-b shadow-[0_-4px_20px_rgba(0,0,0,0.05)] md:shadow-sm bottom-0 md:top-0 md:bottom-auto pb-[env(safe-area-inset-bottom)]" data-testid="sticky-product-bar">
          <div className="max-w-screen-2xl mx-auto px-3 md:px-4 py-2.5 md:py-2 flex items-center justify-between gap-3">
            <div className="flex items-center gap-2.5 min-w-0 flex-1">
              <img src={optimizeImg(displayImages[0], 150)} alt="" className="w-10 h-12 object-cover bg-stone-100" />
              <div className="min-w-0">
                <p className="text-[12px] md:text-sm font-light line-clamp-1">{product.name}</p>
                <p className="text-[12px] md:text-sm tabular-nums">{displayPrice.toFixed(2).replace('.', ',')} TL</p>
              </div>
            </div>
            <div className="flex items-center gap-2 shrink-0">
              <div className="hidden md:flex items-center gap-1.5">
                {sizes.slice(0, 5).map((v, i) => (
                  <button
                    key={i}
                    onClick={() => handleSizeSelect(v)}
                    className={`w-8 h-8 text-xs border transition-colors ${
                      selectedSize === v.size ? "border-black bg-black text-white" : "border-gray-300 hover:border-black"
                    }`}
                  >
                    {v.size}
                  </button>
                ))}
              </div>
              <button
                onClick={handleAddToCart}
                className="bg-black text-white px-4 md:px-6 py-2.5 md:py-2 text-[11px] md:text-xs uppercase tracking-[0.2em] hover:bg-black/85"
                data-testid="sticky-add-to-cart"
              >
                Sepete Ekle
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Breadcrumb */}
      <div className="max-w-screen-2xl mx-auto px-4 py-3 border-b">
        <nav className="text-[11px]">
          <Link to="/" className="text-gray-500 hover:text-black">Ana Sayfa</Link>
          <span className="mx-2 text-gray-300">/</span>
          {product.category_name && (
            <>
              <Link to={`/${slugify(product.category_name)}`} className="text-gray-500 hover:text-black">
                {product.category_name}
              </Link>
              <span className="mx-2 text-gray-300">/</span>
            </>
          )}
          <span className="text-black">{product.name}</span>
        </nav>
      </div>

      <div className="max-w-screen-2xl mx-auto px-4 py-6">
        <div className="grid lg:grid-cols-12 gap-8 lg:gap-16 items-start">
          {/* Image Gallery — mobile: swipe carousel, desktop: 2-col grid */}
          <div className="lg:col-span-7 space-y-2 min-w-0">
            {/* Mobile: full-width snap carousel with dots */}
            <div className="lg:hidden -mx-4 relative">
              {user?.is_admin && (
                <Link
                  to={`/admin/urunler/${product.id}`}
                  title="Ürünü düzenle (admin)"
                  data-testid="pdp-admin-edit-mobile"
                  className="absolute left-3 top-1/2 -translate-y-1/2 z-20 bg-black text-white rounded-full p-2.5 shadow-lg"
                >
                  <Pencil size={16} />
                </Link>
              )}
              <div
                className="flex overflow-x-auto snap-x snap-mandatory scrollbar-hide"
                onScroll={(e) => {
                  const cw = e.currentTarget.clientWidth;
                  if (!cw) return;
                  const idx = Math.round(e.currentTarget.scrollLeft / cw);
                  setMobileImageIdx(idx);
                }}
              >
                {displayImages.map((img, index) => (
                  <div
                    key={index}
                    className="snap-center shrink-0 w-screen aspect-[2/3] bg-stone-50 relative"
                    style={{ scrollSnapStop: "always" }}
                  >
                    {hasDiscount && index === 0 && (
                      <div className="absolute top-3 left-3 z-10 bg-[#6b6b64] text-white text-xs font-normal px-2.5 py-1.5 leading-none">
                        %{Math.round(((product.price - displayPrice) / product.price) * 100)}
                      </div>
                    )}
                    <img
                      src={optimizeImg(img, 1200)}
                      alt={`${product.name} ${index + 1}`}
                      className="w-full h-full object-cover object-top"
                      loading={index === 0 ? "eager" : "lazy"}
                      fetchPriority={index === 0 ? "high" : "auto"}
                      decoding="async"
                    />
                  </div>
                ))}
              </div>
              {displayImages.length > 1 && (
                <div className="flex items-center justify-center gap-1.5 py-3">
                  {displayImages.map((_, i) => (
                    <span
                      key={i}
                      className={`h-[2px] transition-all ${i === mobileImageIdx ? "w-6 bg-black" : "w-3 bg-black/25"}`}
                    />
                  ))}
                </div>
              )}
            </div>

            {/* Desktop: sol thumbnail şeridi + orta büyük görsel (Simon Miller usulü).
                Küçük resimler 92px tek kolon, ana görsel 480px — 3-4 gün önceki düzene
                geri dönüldü (kullanıcı isteği). */}
            <div className="hidden lg:flex gap-4 justify-center">
              {displayImages.length > 1 && (
                <div className="flex flex-col gap-2 w-[92px] shrink-0">
                  {displayImages.map((img, index) => (
                    <button
                      key={index}
                      onClick={() => setSelectedImage(index)}
                      onMouseEnter={() => setSelectedImage(index)}
                      className={`relative aspect-[2/3] bg-stone-50 overflow-hidden border transition-colors ${
                        index === selectedImage ? "border-black" : "border-transparent hover:border-gray-300"
                      }`}
                      aria-label={`Görsel ${index + 1}`}
                      data-testid={`pdp-thumb-${index}`}
                    >
                      <img
                        src={optimizeImg(img, 200)}
                        alt=""
                        className="w-full h-full object-cover object-top"
                        loading="lazy"
                        decoding="async"
                      />
                    </button>
                  ))}
                </div>
              )}
              <div className="flex-1 min-w-0 lg:max-w-[480px]">
                <div
                  className="relative aspect-[2/3] bg-stone-50 overflow-hidden cursor-zoom-in"
                  onMouseEnter={() => setZoom((z) => ({ ...z, on: true }))}
                  onMouseLeave={() => setZoom({ on: false, x: 50, y: 50 })}
                  onMouseMove={(e) => {
                    const r = e.currentTarget.getBoundingClientRect();
                    const x = ((e.clientX - r.left) / r.width) * 100;
                    const y = ((e.clientY - r.top) / r.height) * 100;
                    setZoom({ on: true, x: Math.max(0, Math.min(100, x)), y: Math.max(0, Math.min(100, y)) });
                  }}
                >
                  {hasDiscount && (
                    <div className="absolute top-3 left-3 z-10 bg-[#6b6b64] text-white text-xs font-normal px-2.5 py-1.5 leading-none">
                      %{Math.round(((product.price - displayPrice) / product.price) * 100)}
                    </div>
                  )}
                  {/* Büyüteç ipucu — hover ile büyür */}
                  <div className="absolute bottom-3 right-3 z-10 bg-black/55 text-white rounded-full p-1.5 pointer-events-none opacity-80">
                    <ZoomIn size={14} />
                  </div>
                  {/* ADMIN düzenle kalemi — yalnızca admin oturumunda, en solda. Ürünü müşteri
                      gözüyle incelerken tıkla → admin ürün düzenleme sayfasına gider. */}
                  {user?.is_admin && (
                    <Link
                      to={`/admin/urunler/${product.id}`}
                      onClick={(e) => e.stopPropagation()}
                      title="Ürünü düzenle (admin)"
                      data-testid="pdp-admin-edit"
                      className="absolute left-3 top-1/2 -translate-y-1/2 z-20 bg-black text-white rounded-full p-2.5 shadow-lg hover:bg-gray-800 transition-colors"
                    >
                      <Pencil size={16} />
                    </Link>
                  )}
                  <img
                    src={optimizeImg(displayImages[selectedImage] || displayImages[0], 1400)}
                    alt={product.name}
                    className="w-full h-full object-cover object-top transition-transform duration-150 ease-out"
                    style={zoom.on ? { transform: "scale(2.3)", transformOrigin: `${zoom.x}% ${zoom.y}%` } : undefined}
                    loading="eager"
                    fetchPriority="high"
                    decoding="async"
                  />
                </div>
              </div>
            </div>
          </div>

          {/* Product Info */}
          <div className="lg:col-span-5 lg:sticky lg:top-24 min-w-0 lg:max-w-[420px] lg:mx-auto w-full">
            <h1 className="text-xl md:text-2xl font-light mb-2">{product.name}</h1>

            {/* Price */}
            <div className="mb-6">
              <div className="flex items-center gap-3">
                {hasDiscount && (
                  <span className="text-base text-gray-400 line-through">{listUnit.toFixed(2).replace('.', ',')} TL</span>
                )}
                <span className={`text-lg ${hasDiscount ? "text-red-600 font-medium" : ""}`}>
                  {displayPrice.toFixed(2).replace('.', ',')} TL
                </span>
              </div>

            {/* Yıldız derecelendirme — fiyatın altında; tıkla → yorumlara git */}
            <button
              type="button"
              onClick={() => document.getElementById("reviews")?.scrollIntoView({ behavior: "smooth" })}
              className="mt-3 flex items-center gap-1.5 group"
              data-testid="pdp-rating-jump"
              aria-label="Değerlendirmeleri gör"
            >
              <span className="flex">
                {[1, 2, 3, 4, 5].map((i) => (
                  <Star key={i} size={14} className={i <= Math.round(reviewAvg || 0) ? "fill-black text-black" : "text-gray-300"} />
                ))}
              </span>
              <span className="text-xs text-gray-500 group-hover:text-black transition-colors underline-offset-2 group-hover:underline">
                {reviewTotal > 0 ? `${(reviewAvg || 0).toFixed(1)} · ${reviewTotal} değerlendirme` : "İlk değerlendirmeyi yap"}
              </span>
            </button>


            </div>


            {/* Color Siblings (diğer renk) — varsa swatch'ler */}
            <ColorSiblings productId={product.id} currentColor={
              product.color
              || product.variants?.find?.((v) => v.color)?.color
              || product.attributes?.find?.((a) => (a.name || "").toLowerCase().includes("color") || (a.name || "").toLowerCase().includes("renk"))?.value
            } />

            {/* Size Selection */}
            <div className="mb-5">
              <div className="flex items-center justify-between mb-2">
                <span className="text-xs">
                  {(() => {
                    // "Beden Seçiniz" yerine KALIBA göre dinamik tavsiye (kullanıcı isteği).
                    // Kaynak: ürün formundaki size_advice VEYA Özellikler'deki "Kalıp" değeri.
                    // DİKKAT: attributes bazı (XML kaynaklı) ürünlerde LİSTE değil SÖZLÜK —
                    // dizi varsayımı sayfayı çökertiyordu; iki format da desteklenir.
                    const _attrs = product.attributes;
                    let _attrFitRaw = "";
                    if (Array.isArray(_attrs)) {
                      _attrFitRaw = _attrs.find(
                        (a) => ((a?.name || a?.type || "")).toLocaleLowerCase("tr").includes("kalıp"))?.value || "";
                    } else if (_attrs && typeof _attrs === "object") {
                      const _k = Object.keys(_attrs).find((k) => k.toLocaleLowerCase("tr").includes("kalıp"));
                      const _v = _k ? _attrs[_k] : "";
                      _attrFitRaw = (typeof _v === "object" ? _v?.value : _v) || "";
                    }
                    const _attrFit = String(_attrFitRaw).toLocaleLowerCase("tr");
                    const _fit = product.size_advice
                      || (_attrFit.includes("oversize") || _attrFit.includes("bol") ? "bol"
                        : _attrFit.includes("slim") || _attrFit.includes("dar") ? "dar"
                        : _attrFit.includes("regular") || _attrFit.includes("normal") ? "normal" : "");
                    if (_fit === "normal") return "Müşteriler kendi bedeninizi almanızı tavsiye ediyor.";
                    if (_fit === "bol") return "Müşteriler bir beden küçük almanızı tavsiye ediyor.";
                    if (_fit === "dar") return "Müşteriler bir beden büyük almanızı tavsiye ediyor.";
                    return "Beden Seçiniz";
                  })()}
                  {selectedVariant && Number(selectedVariant.stock) <= 0 && (
                    <span className="text-red-600 ml-2">Tükendi</span>
                  )}
                </span>
              </div>
              <div className="flex items-end justify-between gap-3">
                <div className="flex flex-wrap gap-2">
                {sizes.map((variant, index) => {
                  const isSelected = selectedSize === variant.size;
                  const isOOS = Number(variant.stock) <= 0;
                  const isRec = recLetter && isRecommendedSize(variant.size, user?.height_cm, user?.weight_kg);
                  return (
                    <button
                      key={index}
                      onClick={() => handleSizeSelect(variant)}
                      data-testid={`size-btn-${variant.size}`}
                      title={isRec ? "Boy/kilonuza göre sizin için öneriliyor" : undefined}
                      className={`relative min-w-[44px] h-9 px-3 border text-xs transition-all ${
                        isSelected
                          ? isOOS
                            ? "border-red-500 bg-red-50 text-red-600 line-through"
                            : "border-black bg-black text-white"
                          : isOOS
                            ? "border-gray-200 text-gray-300 line-through bg-gray-50 hover:border-gray-400"
                            : isRec
                              ? "border-emerald-500 ring-1 ring-emerald-500 hover:border-emerald-600"
                              : "border-gray-300 hover:border-black"
                      }`}
                    >
                      {variant.size}
                      {isRec && !isSelected && (
                        <span className="absolute -top-1.5 -right-1.5 w-2.5 h-2.5 bg-emerald-500 rounded-full border border-white" />
                      )}
                    </button>
                  );
                })}
                </div>
                {/* Beden Tablosu — beden butonlarıyla aynı satırda, alt hizada sağda (İşletme Kuralları ile açılır/kapanır) */}
                {(sizeTableData || sizeTableImg) && (shipCfg?.showSizeGuide !== false) && (
                  <button onClick={() => setShowSizeChart(true)} className="text-xs underline underline-offset-2 hover:no-underline whitespace-nowrap shrink-0" data-testid="show-size-table-btn">
                    Beden Tablosu
                  </button>
                )}
              </div>
              {/* Beden önerisi — üye boy/kilo girdiyse ve önerilen beden üründe varsa */}
              {(() => {
                const match = recLetter && sizes.find((v) => isRecommendedSize(v.size, user?.height_cm, user?.weight_kg));
                if (!match) return null;
                return (
                  <p className="mt-2 text-xs text-emerald-700 flex items-center gap-1.5" data-testid="size-recommendation">
                    <span className="inline-block w-2 h-2 bg-emerald-500 rounded-full" />
                    <span><b>{match.size}</b> bedeni sizin için öneriliyor (boy/kilonuza göre)</span>
                  </p>
                );
              })()}
            </div>

            {/* Quantity input removed by request — sepete her zaman 1 adet eklenir */}

            {/* Stok aciliyet rozeti — seçili beden azaldıysa "Son X ürün!" (İşletme Kuralları) */}
            {(shipCfg?.lowStockBadge !== false) && selectedVariant &&
              Number(selectedVariant.stock) > 0 &&
              Number(selectedVariant.stock) <= (shipCfg?.lowStockThreshold ?? 5) && (
              <p className="mb-3 text-xs font-medium text-red-600 flex items-center gap-1.5" data-testid="pdp-low-stock">
                <span className="inline-block w-1.5 h-1.5 rounded-full bg-red-500 animate-pulse" />
                Son {Number(selectedVariant.stock)} ürün!
              </p>
            )}

            {/* Add to Cart */}
            {(() => {
              const _hasVariants = (product.variants?.length || 0) > 0;
              const _productOOS = !_hasVariants && (Number(product.stock) || 0) <= 0;
              const oosSelected = (selectedVariant && Number(selectedVariant.stock) <= 0) || _productOOS;
              return (
                <div className="mb-6">
                  <div className="flex gap-2">
                    {oosSelected ? (
                      <button
                        onClick={() => setNotifyOpen((v) => !v)}
                        data-testid="notify-toggle-btn"
                        className="flex-1 py-2.5 sm:py-3 text-[11px] sm:text-xs uppercase tracking-normal sm:tracking-wider transition-colors border border-black bg-white text-black hover:bg-black hover:text-white"
                      >
                        Gelince Haber Ver
                      </button>
                    ) : (
                      <button
                        onClick={handleAddToCart}
                        data-testid="add-to-cart-btn"
                        disabled={product.variants?.length > 0 && !selectedVariant}
                        className={`flex-1 py-2.5 sm:py-3 text-[11px] sm:text-xs uppercase tracking-normal sm:tracking-wider transition-colors ${
                          product.variants?.length > 0 && !selectedVariant
                            ? "bg-gray-300 text-gray-500 cursor-not-allowed"
                            : justAdded
                              ? "bg-emerald-600 text-white"
                              : "bg-black text-white hover:bg-gray-900"
                        }`}
                      >
                        {product.variants?.length > 0 && !selectedVariant
                          ? "Beden Seçiniz"
                          : justAdded
                            ? (<span className="inline-flex items-center justify-center gap-1.5"><Check size={15} strokeWidth={2.5} /> Eklendi</span>)
                            : "Sepete Ekle"}
                      </button>
                    )}
                    <button
                      onClick={() => toggleFavorite(product)}
                      data-testid="pdp-favorite-btn"
                      aria-label="Kaydet"
                      title="Kaydet"
                      className={`w-12 h-12 border flex items-center justify-center transition-colors ${
                        isFavorite(product.id) ? "border-black bg-gray-50" : "border-gray-300 hover:border-black"
                      }`}
                    >
                      <Bookmark size={18} strokeWidth={1.5} className={isFavorite(product.id) ? "fill-black text-black" : ""} />
                    </button>
                  </div>

                  {/* Kargo geri sayımı — HER ZAMAN mesai saatlerine göre canlı; "bugün/yarın kargoda" yeşil.
                      Kesim saati (varsayılan 12:00) İşletme Kuralları'ndan; şerit açılır/kapanır. */}
                  {(shipCfg?.showCountdown !== false) && (() => {
                    const c = shippingCutoff(shipCfg);
                    return (
                      <p className="mt-3 text-xs flex items-center gap-1.5" data-testid="pdp-shipping-cutoff">
                        <Clock size={14} strokeWidth={1.7} className="text-emerald-600" />
                        <span className="text-gray-700">
                          {c.prefix} <span className="font-semibold text-emerald-700">{c.green}</span>
                        </span>
                      </p>
                    );
                  })()}

                  {/* Güven banner'ı — ücretsiz iade / hızlı teslimat / taksitli ödeme */}
                  <div className="mt-4 grid grid-cols-3 gap-2 border-y border-black/10 py-3.5" data-testid="pdp-trust-badges">
                    <div className="flex flex-col items-center text-center gap-1.5 px-1">
                      <RotateCcw size={18} strokeWidth={1.4} className="text-black/75" />
                      <span className="text-[10px] leading-tight text-black/65">Ücretsiz<br />İade</span>
                    </div>
                    <div className="flex flex-col items-center text-center gap-1.5 px-1 border-x border-black/10">
                      <Truck size={18} strokeWidth={1.4} className="text-black/75" />
                      <span className="text-[10px] leading-tight text-black/65">Hızlı<br />Teslimat</span>
                    </div>
                    <div className="flex flex-col items-center text-center gap-1.5 px-1">
                      <CreditCard size={18} strokeWidth={1.4} className="text-black/75" />
                      <span className="text-[10px] leading-tight text-black/65">Taksitli<br />Ödeme</span>
                    </div>
                  </div>

                  {/* Sosyal paylaşım — Web Share API + WhatsApp/X/Facebook + linki kopyala */}
                  {(shipCfg?.showSocialShare !== false) && (() => {
                    const shareUrl = typeof window !== "undefined" ? window.location.href : "";
                    const shareTitle = product?.name || "";
                    const enc = encodeURIComponent;
                    const nativeShare = async () => {
                      try {
                        if (navigator.share) { await navigator.share({ title: shareTitle, url: shareUrl }); }
                        else { await navigator.clipboard.writeText(shareUrl); toast.success("Bağlantı kopyalandı"); }
                      } catch { /* kullanıcı iptal etti */ }
                    };
                    const copyLink = async () => {
                      try { await navigator.clipboard.writeText(shareUrl); toast.success("Bağlantı kopyalandı"); }
                      catch { toast.error("Kopyalanamadı"); }
                    };
                    return (
                      <div className="mt-4 flex items-center gap-3 flex-wrap" data-testid="pdp-social-share">
                        <span className="text-[11px] uppercase tracking-wider text-black/50 inline-flex items-center gap-1">
                          <Share2 size={14} strokeWidth={1.5} /> Paylaş
                        </span>
                        <a href={`https://wa.me/?text=${enc(shareTitle + " " + shareUrl)}`} target="_blank" rel="noopener noreferrer"
                          className="text-xs text-black/70 hover:text-black underline underline-offset-2" aria-label="WhatsApp'ta paylaş">WhatsApp</a>
                        <a href={`https://twitter.com/intent/tweet?text=${enc(shareTitle)}&url=${enc(shareUrl)}`} target="_blank" rel="noopener noreferrer"
                          className="text-xs text-black/70 hover:text-black underline underline-offset-2" aria-label="X'te paylaş">X</a>
                        <a href={`https://www.facebook.com/sharer/sharer.php?u=${enc(shareUrl)}`} target="_blank" rel="noopener noreferrer"
                          className="text-xs text-black/70 hover:text-black underline underline-offset-2" aria-label="Facebook'ta paylaş">Facebook</a>
                        <button onClick={copyLink} className="text-xs text-black/70 hover:text-black inline-flex items-center gap-1" aria-label="Bağlantıyı kopyala">
                          <Link2 size={13} /> Kopyala
                        </button>
                        {typeof navigator !== "undefined" && navigator.share && (
                          <button onClick={nativeShare} className="text-xs text-black/70 hover:text-black underline underline-offset-2 md:hidden">Diğer…</button>
                        )}
                      </div>
                    );
                  })()}

                  {/* Stok bildirim formu */}
                  {oosSelected && notifyOpen && (
                    <div className="mt-3 border border-black/10 bg-gray-50 p-3" data-testid="stock-notify-form">
                      <p className="text-[11px] text-black/60 mb-2">
                        <span className="font-medium text-black">{selectedVariant.size}</span> bedeni tükendi. Stoğa girince e-posta ile haber verelim.
                      </p>
                      <div className="flex gap-2">
                        <input
                          type="email"
                          value={notifyEmail}
                          onChange={(e) => setNotifyEmail(e.target.value)}
                          onKeyDown={(e) => { if (e.key === "Enter") handleStockNotify(); }}
                          placeholder="E-posta adresiniz"
                          data-testid="stock-notify-email-input"
                          className="flex-1 h-10 px-3 border border-gray-300 text-xs focus:outline-none focus:border-black"
                        />
                        <button
                          onClick={handleStockNotify}
                          disabled={notifySubmitting}
                          data-testid="stock-notify-submit-btn"
                          className="px-4 h-10 text-xs uppercase tracking-wider bg-black text-white hover:bg-gray-900 disabled:opacity-50 disabled:cursor-not-allowed whitespace-nowrap"
                        >
                          {notifySubmitting ? "Gönderiliyor..." : "Gönder"}
                        </button>
                      </div>
                    </div>
                  )}
                </div>
              );
            })()}

            {/* Stilini Tamamla — küçük resimler (sepete ekle ile açıklama arası) */}
            {comboProducts.length > 0 && (shipCfg?.showCompleteLook !== false) && (
              <div className="mb-6 pb-2" data-testid="product-combo-mini">
                <p className="text-[10px] tracking-[0.25em] uppercase text-black/60 mb-3">Stilini Tamamla</p>
                <div className="flex gap-2 overflow-x-auto scrollbar-hide -mx-4 px-4 lg:mx-0 lg:px-0">
                  {comboProducts.slice(0, 6).map((p) => {
                    const img = (p.images && p.images[0]) || p.image || "/placeholder.jpg";
                    return (
                      <Link
                        key={p.id}
                        to={`/${p.slug || p.id}`}
                        className="shrink-0 w-[64px] group"
                        data-testid={`combo-mini-${p.id}`}
                        title={p.name}
                      >
                        <div className="relative w-16 h-20 bg-stone-100 overflow-hidden">
                          <img src={optimizeImg(img, 200)} alt={p.name} className="w-full h-full object-cover object-top group-hover:scale-[1.05] transition-transform duration-500" loading="lazy" decoding="async" />
                        </div>
                        <p className="text-[9px] tabular-nums mt-1 truncate">
                          {priceView(p).hasDiscount ? (
                            <>
                              <span className="text-black/35 line-through mr-1">{priceView(p).list.toFixed(0)}</span>
                              <span className="text-red-600">{priceView(p).display.toFixed(0)} TL</span>
                            </>
                          ) : (
                            <span className="text-black/50">{priceView(p).display.toFixed(0)} TL</span>
                          )}
                        </p>
                      </Link>
                    );
                  })}
                </div>
              </div>
            )}

            {/* Custom Accordion Details - No slider issues */}
            <div className="border-t">
              {/* Ürün Özellikleri */}
              <div className="border-b">
                <button 
                  onClick={() => toggleSection('description')}
                  className="w-full flex items-center justify-between py-3 text-xs hover:bg-gray-50"
                >
                  <span>Ürün Özellikleri</span>
                  {expandedSections.description ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
                </button>
                {expandedSections.description && (
                  <div className="pb-3">
                    <div className="text-xs text-gray-600 leading-relaxed" dangerouslySetInnerHTML={{ __html: sanitizeHtml(product.description) || "Ürün açıklaması bulunmamaktadır." }} />
                    {/* Ürün ÖZELLİK tablosu (attributes: Materyal, Kumaş Tipi, Kalıp vb.) müşteri
                        tarafında GÖSTERİLMEZ (merchant kararı). Admin ürün formunda görünmeye devam
                        eder — bu yalnız storefront gösterimidir, veri silinmez. */}
                  </div>
                )}
              </div>
              
              {/* Kargo ve Teslimat */}
              <div className="border-b">
                <button 
                  onClick={() => toggleSection('shipping')}
                  className="w-full flex items-center justify-between py-3 text-xs hover:bg-gray-50"
                >
                  <span>Kargo ve Teslimat</span>
                  {expandedSections.shipping ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
                </button>
                {expandedSections.shipping && (
                  <ul className="pb-3 text-xs text-gray-600 space-y-1.5">
                    <li>• {(freeShippingThreshold != null ? Number(freeShippingThreshold) : 4000).toLocaleString("tr-TR")} TL ve üzeri siparişlerde ücretsiz kargo</li>
                    <li>• 1-3 iş günü içinde kargoya verilir</li>
                  </ul>
                )}
              </div>
              
              {/* İade ve Değişim */}
              <div className="border-b">
                <button 
                  onClick={() => toggleSection('returns')}
                  className="w-full flex items-center justify-between py-3 text-xs hover:bg-gray-50"
                >
                  <span>İade ve Değişim</span>
                  {expandedSections.returns ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
                </button>
                {expandedSections.returns && (
                  returnPolicyHtml
                    ? <div className="pb-3 text-xs text-gray-600 leading-relaxed [&_ul]:list-disc [&_ul]:pl-4 [&_li]:mb-0.5 [&_p]:mb-1.5 [&_h1]:font-semibold [&_h1]:text-gray-800 [&_h1]:mt-2 [&_h2]:font-semibold [&_h2]:text-gray-800 [&_h2]:mt-2 [&_h3]:font-semibold [&_h3]:text-gray-800 [&_h3]:mt-2 [&_strong]:font-semibold [&_a]:underline"
                        dangerouslySetInnerHTML={{ __html: sanitizeHtml(returnPolicyHtml) }} />
                    : <p className="pb-3 text-xs text-gray-600">14 gün içinde iade ve değişim hakkınız bulunmaktadır.</p>
                )}
              </div>
            </div>
          </div>
        </div>

        {/* Combo Products — mobile: yatay snap-scroll, desktop: 4-col grid (İşletme Kuralları ile açılır/kapanır) */}
        {comboProducts.length > 0 && (shipCfg?.showCompleteLook !== false) && (
          <section className="mt-12 md:mt-16 pt-8 md:pt-10 border-t border-black/10" data-testid="product-combo-section">
            <h2 className="text-base md:text-xl font-light tracking-tight mb-5 md:mb-8 px-1">Stilini Tamamla</h2>
            {/* Mobile horizontal scroll */}
            <div className="md:hidden -mx-4 px-4 overflow-x-auto snap-x snap-mandatory scrollbar-hide">
              <div className="flex gap-3" style={{ minWidth: "max-content" }}>
                {comboProducts.map((p) => {
                  const img = (p.images && p.images[0]) || p.image || "";
                  const pv = priceView(p);
                  return (
                    <div
                      key={p.id}
                      className="snap-start shrink-0 w-[44vw]"
                      data-testid={`combo-product-${p.id}`}
                    >
                      <Link to={`/${p.slug || p.id}`} className="block relative overflow-hidden bg-stone-100 aspect-[2/3]" aria-label={p.name}>
                        <img src={optimizeImg(img, 700)} alt={p.name} className="w-full h-full object-cover object-top" loading="lazy" decoding="async" />
                        <button
                          type="button"
                          onClick={(e) => { e.preventDefault(); e.stopPropagation(); }}
                          className="absolute top-1.5 right-1.5 w-7 h-7 flex items-center justify-center"
                          aria-label="Favorilere ekle"
                        >
                          <Bookmark size={15} strokeWidth={1.4} className="text-black" />
                        </button>
                      </Link>
                      <div className="mt-2">
                        <Link to={`/${p.slug || p.id}`} className="block text-[12px] font-light text-black/85 line-clamp-1">
                          {p.name}
                        </Link>
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
              {comboProducts.map((p) => {
                const img = (p.images && p.images[0]) || p.image || "";
                const pv = priceView(p);
                return (
                  <div key={p.id} className="group relative">
                    <Link to={`/${p.slug || p.id}`} className="block relative overflow-hidden bg-stone-100 aspect-[2/3]" aria-label={p.name}>
                      <img src={img} alt={p.name} className="w-full h-full object-cover object-top transition-transform duration-700 ease-out group-hover:scale-[1.03]" loading="lazy" />
                      <button
                        type="button"
                        onClick={(e) => { e.preventDefault(); e.stopPropagation(); }}
                        className="absolute top-2 right-2 w-8 h-8 flex items-center justify-center bg-white/0 hover:bg-white/80 transition-colors"
                        aria-label="Favorilere ekle"
                      >
                        <Bookmark size={16} strokeWidth={1.4} className="text-black/80" />
                      </button>
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
          </section>
        )}

        {/* Değerlendirmeler (yorum + puan) */}
        <section id="reviews" className="mt-12 pt-12 border-t scroll-mt-24" data-testid="product-reviews">
          <div className="flex items-center justify-between mb-6">
            <h2 className="text-base font-light">Değerlendirmeler{reviewTotal > 0 ? ` (${reviewTotal})` : ""}</h2>
            {reviewTotal > 0 && (
              <div className="flex items-center gap-1.5 text-sm">
                <div className="flex">
                  {[1, 2, 3, 4, 5].map((i) => (
                    <Star key={i} size={16} className={i <= Math.round(reviewAvg) ? "fill-black text-black" : "text-gray-300"} />
                  ))}
                </div>
                <span className="text-gray-600">{reviewAvg.toFixed(1)} / 5</span>
              </div>
            )}
          </div>

          {reviews.length > 0 ? (
            <div className="space-y-5 mb-10">
              {reviews.map((r) => (
                <div key={r.id} className="border-b border-black/5 pb-5">
                  <div className="flex items-center gap-2 mb-1">
                    <div className="flex">
                      {[1, 2, 3, 4, 5].map((i) => (
                        <Star key={i} size={13} className={i <= r.rating ? "fill-black text-black" : "text-gray-300"} />
                      ))}
                    </div>
                    <span className="text-xs font-medium">{r.user_name || "Müşteri"}</span>
                    {(r.source === "trendyol" || r.verified) && (
                      <span className="text-[9px] tracking-wide uppercase bg-emerald-50 text-emerald-700 border border-emerald-200 rounded px-1.5 py-0.5">
                        Doğrulanmış Alışveriş
                      </span>
                    )}
                    <span className="text-[11px] text-gray-400">
                      {r.created_at ? new Date(r.created_at).toLocaleDateString("tr-TR", { day: "numeric", month: "long", year: "numeric" }) : ""}
                    </span>
                  </div>
                  {r.title && <p className="text-sm font-medium mb-0.5">{r.title}</p>}
                  {r.comment && <p className="text-sm text-gray-600 leading-relaxed">{r.comment}</p>}
                  {r.admin_reply && (
                    <div className="mt-2 ml-3 pl-3 border-l-2 border-black/10">
                      <p className="text-[11px] font-medium text-gray-500 mb-0.5">FACETTE</p>
                      <p className="text-xs text-gray-600">{r.admin_reply}</p>
                    </div>
                  )}
                </div>
              ))}
            </div>
          ) : (
            <p className="text-sm text-gray-400 mb-10">Bu ürün için henüz değerlendirme yok. İlk yorumu siz yapın.</p>
          )}

          {rvLoggedIn ? (
            <div className="max-w-xl">
              <h3 className="text-sm font-medium mb-3">Değerlendirme yaz</h3>
              <div className="flex items-center gap-1 mb-3">
                {[1, 2, 3, 4, 5].map((i) => (
                  <button key={i} type="button" onClick={() => setRvRating(i)} className="p-0.5" aria-label={`${i} yıldız`}>
                    <Star size={24} className={i <= rvRating ? "fill-black text-black" : "text-gray-300"} />
                  </button>
                ))}
              </div>
              <input
                type="text" value={rvTitle} onChange={(e) => setRvTitle(e.target.value)}
                placeholder="Başlık (opsiyonel)" maxLength={120}
                className="w-full border border-black/15 px-3 py-2 text-sm mb-3 focus:outline-none focus:border-black"
              />
              <textarea
                value={rvComment} onChange={(e) => setRvComment(e.target.value)}
                placeholder="Deneyiminizi paylaşın…" rows={4} maxLength={2000}
                className="w-full border border-black/15 px-3 py-2 text-sm mb-3 focus:outline-none focus:border-black resize-none"
              />
              <button
                type="button" onClick={submitReview} disabled={rvSubmitting}
                className="bg-black text-white text-sm px-6 py-2.5 hover:bg-gray-800 transition disabled:opacity-50"
              >
                {rvSubmitting ? "Gönderiliyor…" : "Gönder"}
              </button>
              <p className="text-[11px] text-gray-400 mt-2">Yorumunuz moderasyon sonrası yayınlanır.</p>
            </div>
          ) : (
            <div className="text-sm text-gray-500">
              Değerlendirme yapmak için <Link to="/giris" className="underline hover:text-black">giriş yapın</Link>.
            </div>
          )}
        </section>

        {/* Similar Products */}
        {similarProducts.length > 0 && (
          <section className="mt-12 pt-12 border-t">
            <h2 className="text-base font-light mb-6">Benzer Ürünler</h2>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
              {similarProducts.map((p) => <ProductCard key={p.id} product={p} />)}
            </div>
          </section>
        )}

        {/* Son Gezdiklerin — Benzer Ürünler ile aynı 4'lü grid (tek üründe sayfayı kaplamaz) */}
        {recentItems.length > 0 && (
          <section className="mt-12 pt-12 border-t" data-testid="recently-viewed">
            <h2 className="text-base font-light mb-6">Son Gezdiklerin</h2>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
              {recentItems.slice(0, 4).map((p) => {
                const pv = priceView(p);
                return (
                  <Link key={p.id} to={`/${p.slug || p.id}`} className="group" data-testid={`recent-${p.id}`}>
                    <div className="aspect-[2/3] bg-stone-100 overflow-hidden">
                      <img src={optimizeImg(p.image, 500)} alt={p.name} className="w-full h-full object-cover object-top group-hover:scale-[1.03] transition-transform duration-500" loading="lazy" decoding="async" />
                    </div>
                    <p className="text-[12px] md:text-sm font-light text-black/85 line-clamp-1 mt-2">{p.name}</p>
                    <p className="text-[12px] md:text-sm tabular-nums mt-0.5">
                      {pv.hasDiscount ? (
                        <>
                          <span className="text-black/40 line-through mr-1">{pv.list.toFixed(2).replace('.', ',')}</span>
                          <span className="text-red-600">{pv.display.toFixed(2).replace('.', ',')} TL</span>
                        </>
                      ) : (
                        <span>{pv.display.toFixed(2).replace('.', ',')} TL</span>
                      )}
                    </p>
                  </Link>
                );
              })}
            </div>
          </section>
        )}
      </div>

      {/* Size Chart Modal – HTML table */}
      {showSizeChart && (sizeTableData || sizeTableImg) && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4" onClick={() => setShowSizeChart(false)}>
          <div className="bg-white max-w-3xl w-full max-h-[90vh] overflow-auto" onClick={e => e.stopPropagation()}>
            <div className="sticky top-0 bg-white flex justify-between items-center px-6 py-4 border-b">
              <h3 className="text-base font-semibold uppercase tracking-wider text-gray-800">Beden Kılavuzu</h3>
              <button onClick={() => setShowSizeChart(false)} className="p-1"><X size={18} /></button>
            </div>
            {!sizeTableData && sizeTableImg && (
              <div className="p-4 flex justify-center" data-testid="size-table-image">
                {/* object-contain + max-yükseklik: orantı bozulmadan, gereksiz uzamadan sığar */}
                <img src={optimizeImg(sizeTableImg, 1000)} alt="Beden Tablosu"
                     className="max-w-full max-h-[75vh] w-auto h-auto object-contain" />
              </div>
            )}
            {sizeTableData && (
            /* KOMPAKT: küçük punto + dar boşluk → modal tek ekrana sığar, kaydırma gerekmez. */
            <div className="p-4" data-testid="size-table-html">
              {/* Üst blok: sol ürün görseli + sağda ad & Ürün Özellikleri (örnek düzen) */}
              <div className="flex flex-row gap-4 mb-3">
                {(product.images?.[0] || product.image) && (
                  <img
                    src={optimizeImg(product.images?.[0] || product.image, 500)}
                    alt={product.name}
                    className="w-28 sm:w-32 h-auto object-contain flex-shrink-0 self-start bg-gray-50"
                    loading="lazy"
                  />
                )}
                <div className="min-w-0 flex-1">
                  <h4 className="text-sm font-medium text-gray-800 mb-1.5">{product.name}</h4>
                  {product.description && (() => {
                    // "Yıkama ve Bakım" bölümü SAĞDAKİ boş alana, Ürün Özellikleri ile
                    // aynı hizada — açıklama HTML'i Yıkama başlığından ikiye bölünür.
                    const _html = sanitizeHtml(product.description);
                    const _m = _html.search(/Y[ıi]kama/i);
                    let _left = _html, _right = "";
                    if (_m > 0) {
                      const _before = _html.slice(0, _m);
                      const _cut = Math.max(_before.lastIndexOf("<p"), _before.lastIndexOf("<h"),
                        _before.lastIndexOf("<div"), _before.lastIndexOf("<ul"),
                        _before.lastIndexOf("<strong"), _before.lastIndexOf("<b"));
                      const _idx = _cut > 0 ? _cut : _m;
                      _left = _html.slice(0, _idx);
                      _right = _html.slice(_idx);
                    }
                    const _cls = "text-[11px] text-gray-600 leading-snug [&_ul]:list-disc [&_ul]:pl-4 [&_li]:mb-0 [&_p]:mb-1";
                    return (
                      <div className="sm:grid sm:grid-cols-2 sm:gap-6">
                        <div>
                          <p className="text-[11px] font-semibold text-gray-800 mb-1">Ürün Özellikleri</p>
                          <div className={_cls} dangerouslySetInnerHTML={{ __html: _left }} />
                        </div>
                        {_right && (
                          <div className="mt-2 sm:mt-0">
                            <div className={_cls} dangerouslySetInnerHTML={{ __html: _right }} />
                          </div>
                        )}
                      </div>
                    );
                  })()}
                  {sizeTableData.product_size && (
                    <p className="text-[11px] text-gray-700 mt-2"><span className="font-semibold text-gray-800">Ürün Bedeni:</span> {sizeTableData.product_size}</p>
                  )}
                  {sizeTableData.model_info && Object.keys(sizeTableData.model_info).length > 0 && (
                    <p className="text-[11px] text-gray-700 mt-1">
                      <span className="font-semibold text-gray-800">Manken:</span>{" "}
                      {Object.entries(sizeTableData.model_info).filter(([, v]) => String(v).trim()).map(([k, v]) => `${k} ${v} cm`).join(", ")}
                    </p>
                  )}
                </div>
              </div>
              {/* Ölçü tablosu — TRANSPOZE: satır=ölçü (Göğüs/Bel/Boy), kolon=beden (34/36/38/40) */}
              <div className="overflow-x-auto border border-gray-200">
                <table className="w-full text-[11px] border-collapse">
                  <thead>
                    <tr className="bg-gray-50 border-b border-gray-200">
                      <th className="text-left px-3 py-1.5 font-semibold text-gray-800">Ölçüler</th>
                      {sizeTableData.sizes.map(s => (
                        <th key={s} className="text-center px-3 py-1.5 font-semibold text-gray-800 border-l border-gray-200">{s}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {sizeTableData.columns.map((c, ri) => (
                      <tr key={c} className={`border-b border-gray-100 ${ri % 2 === 0 ? 'bg-white' : 'bg-gray-50/60'}`}>
                        <td className="px-3 py-1.5 text-gray-700">{c}</td>
                        {sizeTableData.sizes.map(s => (
                          <td key={s} className="px-3 py-1.5 text-center text-gray-600 border-l border-gray-100">{sizeTableData.values?.[s]?.[c] || '—'}</td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <p className="text-[10px] text-gray-400 mt-2">Tüm ölçüler cm cinsindendir; ± 1-2 cm tolerans taşıyabilir.</p>
            </div>
            )}
          </div>
        </div>
      )}

      <Footer />
    </div>
  );
}

/**
 * ColorSiblings — Aynı modelin (csv_card_id paylaşan) farklı renk ürünlerini
 * miniatür kare swatch'lerle gösterir. Hover ile ürün adı tooltip, click ile
 * o renk varyantının ürün sayfasına yönlendirir.
 */
// Türkçe renk adı → HEX. Ürün kartında/PDP'de renk kutuları GERÇEK renk gösterir (görsel değil).
const TR_COLOR_HEX = {
  siyah: "#111111", beyaz: "#ffffff", "kırmızı": "#d11f1f", kirmizi: "#d11f1f",
  mavi: "#2454c7", lacivert: "#1a2a5e", "yeşil": "#2e8b45", yesil: "#2e8b45",
  "sarı": "#f2c500", sari: "#f2c500", turuncu: "#ee7c1b", mor: "#7d3cb5",
  pembe: "#e86ea3", gri: "#9a9a9a", kahverengi: "#6b4226", bej: "#d8c3a5",
  ekru: "#e8e2d0", krem: "#efe7d3", bordo: "#6e1423", haki: "#6b6b3a",
  turkuaz: "#1ab6b6", "gümüş": "#c0c0c0", gumus: "#c0c0c0", "altın": "#c9a227", altin: "#c9a227",
  "füme": "#5a5a5a", fume: "#5a5a5a", antrasit: "#383838", vizon: "#9b7e6b",
  taba: "#a9662e", hardal: "#c9a227", indigo: "#33427a", somon: "#f2a68c",
  "fuşya": "#c81f76", fusya: "#c81f76", lila: "#c8a2d6", mint: "#a8e0c0",
  petrol: "#1f5f6e", camel: "#c19a6b", ten: "#e6c8a8", nude: "#e3c2a8",
  "yavruağzı": "#f2b8a2", yavruagzi: "#f2b8a2", "gül kurusu": "#b76e79", gulkurusu: "#b76e79",
  mürdüm: "#5a2a4d", murdum: "#5a2a4d", "açık mavi": "#8fb8e0", "koyu mavi": "#1a2a5e",
};
function colorHexTR(name) {
  if (!name) return null;
  const n = String(name).toLocaleLowerCase("tr").trim();
  if (TR_COLOR_HEX[n]) return TR_COLOR_HEX[n];
  for (const key of Object.keys(TR_COLOR_HEX)) if (n.includes(key)) return TR_COLOR_HEX[key];
  return null;
}
// Tek bir renk kutusu (yuvarlak). Beyaz/açık tonlarda görünürlük için ince kenarlık.
function ColorDot({ color, selected }) {
  const hex = colorHexTR(color) || "#e5e5e5";
  return (
    <span
      className={`inline-block w-8 h-8 rounded-full transition-transform ${selected ? "ring-2 ring-black ring-offset-2" : "ring-1 ring-gray-300 hover:ring-black"}`}
      style={{ backgroundColor: hex }}
      title={color || ""}
    />
  );
}

function ColorSiblings({ productId, currentColor }) {
  const [siblings, setSiblings] = useState([]);
  useEffect(() => {
    if (!productId) return;
    let cancel = false;
    axios.get(`${API}/products/${productId}/color-siblings`)
      .then((r) => { if (!cancel) setSiblings(r.data?.siblings || []); })
      .catch(() => { if (!cancel) setSiblings([]); });
    return () => { cancel = true; };
  }, [productId]);
  if (!siblings.length) return null;
  return (
    <div className="mb-5" data-testid="color-siblings">
      <p className="text-xs uppercase tracking-[0.18em] text-gray-700 mb-2">
        Renk: <span className="text-black font-medium">{currentColor || "—"}</span>
      </p>
      <div className="flex flex-wrap items-center gap-3">
        {/* Mevcut ürün — seçili renk noktası */}
        <ColorDot color={currentColor} selected />
        {siblings.map((s) => (
          <a
            key={s.id}
            href={`/${s.slug || s.id}`}
            title={`${s.color || s.name || ""}`}
            data-testid={`color-sibling-${s.id}`}
            className="inline-flex"
          >
            <ColorDot color={s.color || s.name} />
          </a>
        ))}
      </div>
    </div>
  );
}
