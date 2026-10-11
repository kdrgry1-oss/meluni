// AdminApp.jsx — Tüm admin paneli burada. App.js bunu React.lazy ile
// ayrı bir chunk olarak yükler; böylece mağaza (storefront) ziyaretçileri
// ~75 admin sayfasını ve ağır kütüphaneleri (recharts, xlsx vb.) İNDİRMEZ.
import { Routes, Route, Navigate } from "react-router-dom";

import AdminLayout from "./pages/admin/AdminLayout";
import AcceptInvite from "./pages/admin/AcceptInvite";
import Chargebacks from "./pages/admin/Chargebacks";
import KvkkRequests from "./pages/admin/KvkkRequests";
import SecuritySwitches from "./pages/admin/SecuritySwitches";
import AdminDashboard from "./pages/admin/Dashboard";
import AdminProducts from "./pages/admin/Products";
import AdminOrders from "./pages/admin/Orders";
import AdminCategories from "./pages/admin/Categories";
import AdminVariants from "./pages/admin/Variants";
import AdminBanners from "./pages/admin/Banners";
import SettingsWorkspace from "./pages/admin/SettingsWorkspace";
import AdminCampaigns from "./pages/admin/Campaigns";
import AdminPages from "./pages/admin/Pages";
import AdminPageDesign from "./pages/admin/PageDesign";
import AdminCategoryOrder from "./pages/admin/CategoryOrder";
import AdminFooterDesign from "./pages/admin/FooterDesign";
import AdminMenu from "./pages/admin/MenuAdmin";
import AdminInstagram from "./pages/admin/Instagram";
import EmailMarketing from "./pages/admin/EmailMarketing";
import AdminIntegrations from "./pages/admin/Integrations";
import Payments from "./pages/admin/Payments";
import AdminLogin from "./pages/admin/AdminLogin";
import AdminReturns from "./pages/admin/Returns";
import AdminCancellations from "./pages/admin/Cancellations";
import DeletedOrders from "./pages/admin/DeletedOrders";
import AttributeImport from "./pages/admin/AttributeImport";
import AdminQuestions from "./pages/admin/Questions";
import TrendyolLogs from "./pages/admin/TrendyolLogs";
import BarcodeIssues from "./pages/admin/BarcodeIssues";
import TrendyolGhostScanner from "./pages/admin/TrendyolGhostScanner";
import ProductAttributes from "./pages/admin/ProductAttributes";
import Manufacturing from "./pages/admin/Manufacturing";
import Members from "./pages/admin/Members";
import Consents from "./pages/admin/Consents";
import Attribution from "./pages/admin/Attribution";
import SizeTablesList from "./pages/admin/SizeTablesList";
import Coupons from "./pages/admin/Coupons";
import GiftCards from "./pages/admin/GiftCards";
import ProductReviews from "./pages/admin/ProductReviews";
import AbandonedCarts from "./pages/admin/AbandonedCarts";
import { SalesReport, ProductsReport, StockReport, MembersReport } from "./pages/admin/Reports";
import { SeoRedirects, SeoMeta } from "./pages/admin/SeoAdmin";
import {
  Brands, ProductTags, Announcements, Popups,
  StockAlerts, HavaleNotifications, Tickets, ShippingPaymentRules,
  ExtraReports,
} from "./pages/admin/CatalogExtras";
import AdminTasks from "./pages/admin/AdminTasks";
import TrainingPanel from "./pages/admin/TrainingPanel";
import BlockedCustomers from "./pages/admin/BlockedCustomers";
import Influencers from "./pages/admin/Influencers";
import AmazonAdmin from "./pages/admin/AmazonAdmin";
import PaymentTrace from "./pages/admin/PaymentTrace";
import ReportsAdvanced from "./pages/admin/ReportsAdvanced";
import ReportsInsights from "./pages/admin/ReportsInsights";
import XmlFeeds from "./pages/admin/XmlFeeds";
import MarketplaceHub from "./pages/admin/MarketplaceHub";
import IntegrationLogs from "./pages/admin/IntegrationLogs";
import FailedTransfers from "./pages/admin/FailedTransfers";
import BrandMapping from "./pages/admin/BrandMapping";
import CategoryMapping from "./pages/admin/CategoryMapping";
import CatalogReadiness from "./pages/admin/CatalogReadiness";
import BulkPriceStock from "./pages/admin/BulkPriceStock";
import StockAlerts2 from "./pages/admin/StockAlerts";
import CustomerSegments from "./pages/admin/CustomerSegments";
import AutomationStatus from "./pages/admin/AutomationStatus";
import SupplierXml from "./pages/admin/SupplierXml";
import AiImages from "./pages/admin/AiImages";
import SecurityDashboard from "./pages/admin/SecurityDashboard";
import SystemHealth from "./pages/admin/SystemHealth";
import SecretsVault from "./pages/admin/SecretsVault";
import IysAdmin from "./pages/admin/IysAdmin";
import ReportsExtended from "./pages/admin/ReportsExtended";
import MobileApp from "./pages/admin/MobileApp";
import AIAssistant from "./pages/admin/AIAssistant";
import Themes from "./pages/admin/Themes";
import RooftrExcelUpload from "./pages/admin/RooftrExcelUpload";
import ActivityHistory from "./pages/admin/ActivityHistory";

// Bu Routes, App.js'te "/admin/*" altına mount edilir; bu yüzden yollar
// /admin'e göreceli yazılır (örn. "urunler" => /admin/urunler).
export default function AdminApp() {
  return (
    <Routes>
      <Route path="login" element={<AdminLogin />} />
      <Route path="davet" element={<AcceptInvite />} />
      <Route element={<AdminLayout />}>
        <Route index element={<AdminDashboard />} />
        <Route path="urunler" element={<AdminProducts />} />
        <Route path="urunler/:productId" element={<AdminProducts />} />
        <Route path="siparisler" element={<AdminOrders key="orders-all" />} />
        <Route path="odeme-bekleyen-siparisler" element={<AdminOrders key="orders-unpaid" unpaidView />} />
        <Route path="kategoriler" element={<AdminCategories />} />
        <Route path="kategori-siralama" element={<AdminCategoryOrder />} />
        <Route path="varyantlar" element={<AdminVariants />} />
        <Route path="xml-feedler" element={<XmlFeeds />} />
        <Route path="rooftr-excel" element={<RooftrExcelUpload />} />
        <Route path="sorular" element={<AdminQuestions />} />
        <Route path="sayfa-tasarimi" element={<AdminPageDesign />} />
        <Route path="footer-tasarim" element={<AdminFooterDesign />} />
        <Route path="menu-yonetimi" element={<AdminMenu />} />
        <Route path="instagram" element={<AdminInstagram />} />
        <Route path="eposta-pazarlama" element={<EmailMarketing />} />
        <Route path="bannerlar" element={<AdminBanners />} />
        <Route path="temalar" element={<Themes />} />
        <Route path="kampanyalar" element={<AdminCampaigns />} />
        <Route path="entegrasyonlar" element={<AdminIntegrations />} />
        <Route path="odeme-tipleri" element={<Payments />} />
        <Route path="iadeler" element={<AdminReturns />} />
        <Route path="iptaller" element={<AdminCancellations />} />
        <Route path="silinen-siparisler" element={<DeletedOrders />} />
        <Route path="iade-edilenler" element={<Navigate to="/admin/iadeler" replace />} />
        <Route path="sayfalar" element={<AdminPages />} />
        <Route path="ayarlar" element={<SettingsWorkspace />} />
        <Route path="ayarlar/isletme-kurallari" element={<Navigate to="/admin/ayarlar?tab=business-rules" replace />} />
        <Route path="ayarlar/ozel-tema" element={<Navigate to="/admin/ayarlar?tab=custom-theme" replace />} />
        <Route path="egitim" element={<TrainingPanel />} />
        <Route path="ayarlar/menu-duzeni" element={<Navigate to="/admin/ayarlar?tab=menu-layout" replace />} />
        <Route path="ayarlar/e-fatura" element={<Navigate to="/admin/ayarlar?tab=einvoice" replace />} />
        <Route path="ayarlar/kargo" element={<Navigate to="/admin/ayarlar?tab=cargo" replace />} />
        <Route path="ayarlar/gonderici-adresi" element={<Navigate to="/admin/ayarlar?tab=sender-address" replace />} />
        <Route path="ayarlar/bildirim" element={<Navigate to="/admin/ayarlar?tab=notifications" replace />} />
        <Route path="ayarlar/eposta" element={<Navigate to="/admin/ayarlar?tab=email" replace />} />
        <Route path="ayarlar/bildirim/sablonlar" element={<Navigate to="/admin/ayarlar?tab=notification-templates" replace />} />
        <Route path="bloklu-musteriler" element={<BlockedCustomers />} />
        {/* İmalat Planı (Tablo) menüden kaldırıldı — eski yer imleri İmalat Takip'e gider */}
        <Route path="uretim-plani" element={<Navigate to="/admin/imalat" replace />} />
        <Route path="ayarlar/pixel" element={<Navigate to="/admin/ayarlar?tab=pixels" replace />} />
        <Route path="influencer" element={<Influencers />} />
        <Route path="amazon" element={<AmazonAdmin />} />
        <Route path="amazon/sp-api" element={<Navigate to="/admin/amazon?tab=connection" replace />} />
        <Route path="amazon/aktarim" element={<Navigate to="/admin/amazon?tab=mapping" replace />} />
        <Route path="amazon/eslestirme" element={<Navigate to="/admin/amazon?tab=mapping" replace />} />
        <Route path="amazon/dpp" element={<Navigate to="/admin/amazon?tab=compliance" replace />} />
        <Route path="dpp-uyum" element={<Navigate to="/admin/amazon?tab=compliance" replace />} />
        <Route path="ayarlar/capi-loglar" element={<Navigate to="/admin/ayarlar?tab=capi" replace />} />
        <Route path="odeme-izi" element={<PaymentTrace />} />
        <Route path="odeme-itirazlari" element={<Chargebacks />} />
        <Route path="kvkk-talepleri" element={<KvkkRequests />} />
        <Route path="acil-anahtarlar" element={<SecuritySwitches />} />
        <Route path="raporlar/iade-ve-trend" element={<Navigate to="/admin/raporlar/urun" replace />} />
        <Route path="raporlar/konum-kanal" element={<Navigate to="/admin/raporlar/satis" replace />} />
        <Route path="ayarlar/sosyal-giris" element={<Navigate to="/admin/ayarlar?tab=social-login" replace />} />
        <Route path="ayarlar/siparis-durumlari" element={<Navigate to="/admin/ayarlar?tab=order-statuses" replace />} />
        <Route path="pazaryerleri" element={<MarketplaceHub />} />
        <Route path="entegrasyon-loglari" element={<IntegrationLogs />} />
        <Route path="aktarilamayanlar" element={<FailedTransfers />} />
        <Route path="marka-eslestir" element={<BrandMapping />} />
        <Route path="kategori-eslestir" element={<CategoryMapping />} />
        {/* Katalog hazırlık — kategori kategori ilerleme (ölçü tablosu,
            görsel, öznitelik, sezon/kalıp, Trendyol eşleşmesi). */}
        <Route path="katalog-hazirlik" element={<CatalogReadiness />} />
        <Route path="toplu-fiyat-stok" element={<BulkPriceStock />} />
        <Route path="stok-uyarilari" element={<StockAlerts2 />} />
        <Route path="musteri-segmentleri" element={<CustomerSegments />} />
        <Route path="tedarikci-xml" element={<SupplierXml />} />
        <Route path="gorsel-uretimi" element={<AiImages />} />
        <Route path="otomasyon" element={<AutomationStatus />} />
        <Route path="guvenlik-paneli" element={<SecurityDashboard />} />
        <Route path="islem-gecmisi" element={<ActivityHistory />} />
        <Route path="sistem-sagligi" element={<SystemHealth />} />
        <Route path="secrets-vault" element={<SecretsVault />} />
        <Route path="iys" element={<IysAdmin />} />
        <Route path="mobil-uygulama" element={<MobileApp />} />
        <Route path="ai-asistan" element={<AIAssistant />} />
        <Route path="trendyol-eslestir" element={<Navigate to="/admin/kategori-eslestir" replace />} />
        <Route path="trendyol-loglar" element={<TrendyolLogs />} />
        <Route path="barkod-sorunlari" element={<BarcodeIssues />} />
        <Route path="trendyol-hayalet" element={<TrendyolGhostScanner />} />
        <Route path="urun-ozellikleri" element={<ProductAttributes />} />
        <Route path="cariler" element={<Navigate to="/admin/ayarlar?tab=vendors" replace />} />
        <Route path="kullanicilar" element={<Navigate to="/admin/ayarlar?tab=users-roles" replace />} />
        <Route path="imalat" element={<Manufacturing />} />
        <Route path="uyeler" element={<Members />} />
        <Route path="izinler" element={<Consents />} />
        <Route path="kaynak" element={<Attribution />} />
        <Route path="hepsiburada-eslestir" element={<Navigate to="/admin/kategori-eslestir" replace />} />
        <Route path="temu-eslestir" element={<Navigate to="/admin/kategori-eslestir" replace />} />
        <Route path="olcu-tablolari" element={<SizeTablesList />} />
        <Route path="kuponlar" element={<Coupons />} />
        <Route path="hediye-cekleri" element={<GiftCards />} />
        <Route path="yorumlar" element={<ProductReviews />} />
        <Route path="terkedilmis-sepet" element={<AbandonedCarts />} />
        <Route path="raporlar/karar-kurulu" element={<Navigate to="/admin/raporlar/urun" replace />} />
        <Route path="raporlar/satis" element={<SalesReport />} />
        <Route path="raporlar/urun" element={<ProductsReport />} />
        <Route path="raporlar/stok" element={<StockReport />} />
        <Route path="raporlar/uye" element={<MembersReport />} />
        <Route path="seo/meta" element={<SeoMeta />} />
        <Route path="seo/yonlendirmeler" element={<SeoRedirects />} />
        <Route path="markalar" element={<Brands />} />
        <Route path="etiketler" element={<ProductTags />} />
        <Route path="duyurular" element={<Announcements />} />
        <Route path="popuplar" element={<Popups />} />
        <Route path="stok-alarm" element={<StockAlerts />} />
        <Route path="havale-bildirimleri" element={<HavaleNotifications />} />
        <Route path="tickets" element={<Tickets />} />
        <Route path="kargo-odeme-kurallari" element={<ShippingPaymentRules />} />
        <Route path="doviz" element={<Navigate to="/admin/ayarlar?tab=currency" replace />} />
        <Route path="toplu-mail" element={<Navigate to="/admin/eposta-pazarlama" replace />} />
        <Route path="raporlar/gelismis" element={<Navigate to="/admin/raporlar/satis" replace />} />
        <Route path="raporlar/kar-stok" element={<ReportsExtended />} />
        <Route path="gorevler" element={<AdminTasks />} />
      </Route>
    </Routes>
  );
}
