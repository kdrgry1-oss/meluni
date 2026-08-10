import { BrowserRouter, Routes, Route, Navigate, useParams, useLocation, useNavigationType } from "react-router-dom";
import { useEffect, lazy, Suspense } from "react";
import { Toaster } from "sonner";
import { CartProvider } from "./context/CartContext";
import { AuthProvider } from "./context/AuthContext";
import { FavoritesProvider } from "./context/FavoritesContext";
import { bootstrapNative, isNative, appTarget } from "./lib/native";
import ErrorBoundary from "./components/ErrorBoundary";

// Storefront — sadece ana sayfa (LCP) eager; gerisi route'a girilince yüklenir.
// Bu, ilk açılışta indirilen JS'i ciddi şekilde küçültür (mobil TBT/LCP/FCP iyileşir).
import Home from "./pages/Home";
const GizlilikPolitikasi = lazy(() => import("./pages/GizlilikPolitikasi"));
const Category = lazy(() => import("./pages/Category"));
const ProductDetail = lazy(() => import("./pages/ProductDetail"));
const Cart = lazy(() => import("./pages/Cart"));
const Checkout = lazy(() => import("./pages/Checkout"));
const Search = lazy(() => import("./pages/Search"));
const StaticPage = lazy(() => import("./pages/StaticPage"));
const FAQ = lazy(() => import("./pages/FAQ"));
const Account = lazy(() => import("./pages/Account"));
const Login = lazy(() => import("./pages/Login"));
const ForgotPassword = lazy(() => import("./pages/ForgotPassword"));
const ResetPassword = lazy(() => import("./pages/ResetPassword"));
const TrackOrder = lazy(() => import("./pages/TrackOrder"));
const OrderSuccess = lazy(() => import("./pages/OrderSuccess"));
const PaymentNotification = lazy(() => import("./pages/PaymentNotification"));
const ReturnRequest = lazy(() => import("./pages/ReturnRequest"));
const GuestReturn = lazy(() => import("./pages/GuestReturn"));
const MiuMiuTheme = lazy(() => import("./pages/storefront/MiuMiuTheme"));

import MarketingPixelsInjector from "./components/MarketingPixelsInjector";
import SlugRouter from "./components/SlugRouter";
import MaintenanceGate from "./components/MaintenanceGate";
import CookieConsent from "./components/CookieConsent";
// DENETİM FIX (#35): admin Duyuru/Popup'larını storefront'ta gösteren bileşenler
const AnnouncementBar = lazy(() => import("./components/AnnouncementBar"));
const SitePopup = lazy(() => import("./components/SitePopup"));
// DENETİM FIX (#5/#6): 301/302 yönlendirme + per-path meta override tüketimi
const SeoManager = lazy(() => import("./components/SeoManager"));
// Özel Tema (CSS/JS) — yalnız storefront'ta enjekte eder (admin'de asla)
const CustomThemeInjector = lazy(() => import("./components/CustomThemeInjector"));
const WhatsAppButton = lazy(() => import("./components/WhatsAppButton"));
import { trackVisit } from "./lib/attribution";

import "./App.css";

// Rota değişiminde sayfayı anında en üste al — 2./3. sayfaya geçişte veya yeni
// sayfa açıldığında footer'ın önce görünüp sonra yukarı zıplaması engellenir.
function ScrollToTop() {
  const { pathname } = useLocation();
  const navType = useNavigationType();  // "POP" = geri/ileri → scroll'a dokunma, tarayıcı pozisyonu restore etsin
  useEffect(() => {
    // Yeni sayfa (PUSH/REPLACE) → en üste al. Geri/ileri (POP) → kullanıcının
    // kaldığı yeri koru (liste → ürün → geri: liste eski scroll'unda açılır).
    if (navType !== "POP") window.scrollTo({ top: 0, behavior: "auto" });
  }, [pathname, navType]);
  return null;
}

// Admin paneli (~75 sayfa + ağır kütüphaneler) AYRI bir chunk olarak,
// sadece /admin'e girilince yüklenir. Storefront ziyaretçileri bunu indirmez.
const AdminApp = lazy(() => import("./AdminApp"));

// Eski /siparis-tamamlandi/:orderNumber linkleri (SPA-içi navigasyon) için client redirect.
// Doğrudan URL/bot istekleri zaten public/_redirects ile gerçek 301 alır; bu, SPA fallback'tir.
function LegacyOrderRedirect() {
  const { orderNumber } = useParams();
  return <Navigate to={`/order-success/${orderNumber}`} replace />;
}

function App() {
  useEffect(() => {
    // UTM/referrer yakalama — render'ı bloklamaması için mount sonrası.
    if (typeof window !== "undefined" && !window.__FACETTE_TRACKED__) {
      window.__FACETTE_TRACKED__ = true;
      trackVisit();
    }
    // Capacitor native bootstrap (web mode'da no-op)
    bootstrapNative();
  }, []);

  return (
    <ErrorBoundary>
    <AuthProvider>
      <CartProvider>
        <FavoritesProvider>
          <BrowserRouter>
            <ScrollToTop />
            <Toaster position="top-center" richColors />
            <MarketingPixelsInjector />
            <CookieConsent />
            {/* Storefront Duyuru barı + Popup + SEO (native uygulamada gösterme).
                Görünüm bozulmasının gerçek nedeni lock-file (yarn.lock) değişikliğiydi;
                düzeltildi. Bu bileşenler güvenli (aktif popup/duyuru/yönlendirme yoksa
                hiçbir şey render etmez), geri açıldı. */}
            {!isNative && (
              <Suspense fallback={null}>
                <SeoManager />
                <CustomThemeInjector />
                <AnnouncementBar />
                <SitePopup />
                <WhatsAppButton />
              </Suspense>
            )}
            <MaintenanceGate>
              <Suspense fallback={<div style={{ padding: 40, textAlign: "center", color: "#888" }}>Yükleniyor…</div>}>
                <Routes>
                {/* Kök route: müşteri app + web → Home (storefront); yalnız admin app'te → /admin.
                    Hedef, build zamanı REACT_APP_APP_TARGET ile belirlenir (native.js appTarget). */}
                <Route path="/" element={isNative && appTarget === "admin" ? <Navigate to="/admin" replace /> : <Home />} />
                <Route path="/kategori/:slug" element={<Category />} />
                <Route path="/sepet" element={<Cart />} />
                <Route path="/odeme" element={<Checkout />} />
                <Route path="/arama" element={<Search />} />
                <Route path="/sayfa/:slug" element={<StaticPage />} />
                <Route path="/gizlilik" element={<GizlilikPolitikasi />} />
                <Route path="/sikca-sorulan-sorular" element={<FAQ />} />
                <Route path="/sss" element={<FAQ />} />
                <Route path="/hesabim" element={<Account />} />
                <Route path="/giris" element={<Login />} />
                <Route path="/sifremi-unuttum" element={<ForgotPassword />} />
                <Route path="/sifre-sifirla" element={<ResetPassword />} />
                <Route path="/siparis-takip" element={<TrackOrder />} />
                <Route path="/siparis-takip/:trackingCode" element={<TrackOrder />} />
                <Route path="/order-success/:orderNumber" element={<OrderSuccess />} />
                <Route path="/siparis-tamamlandi/:orderNumber" element={<LegacyOrderRedirect />} />
                <Route path="/odeme-bildirimi/:orderNumber" element={<PaymentNotification />} />
                <Route path="/iade/:orderNumber" element={<ReturnRequest />} />
                <Route path="/iade-islemleri" element={<GuestReturn />} />

                {/* Tema önizleme */}
                <Route path="/tema/:slug" element={<MiuMiuTheme />} />
                <Route path="/tema" element={<MiuMiuTheme />} />

                {/* Admin — lazy yüklenen ayrı chunk. /admin/login dahil tüm admin
                    rotaları AdminApp içindeki kendi <Routes>'unda tanımlı. */}
                <Route
                  path="/admin/*"
                  element={<AdminApp />}
                />

                {/* Ürün detay — SEO dostu kök URL. Statik rotalar dinamikten önce
                    eşleştiği için /sepet, /giris, /admin vb. her zaman önceliklidir. */}
                <Route path="/urun/:slug" element={<ProductDetail />} />
                <Route path="/:slug" element={<SlugRouter />} />
                </Routes>
              </Suspense>
            </MaintenanceGate>
          </BrowserRouter>
        </FavoritesProvider>
      </CartProvider>
    </AuthProvider>
    </ErrorBoundary>
  );
}

export default App;
