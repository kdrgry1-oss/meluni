// AdminApp.jsx — Tüm admin paneli burada. App.js bunu React.lazy ile
// ayrı bir chunk olarak yükler; böylece mağaza (storefront) ziyaretçileri
// ~75 admin sayfasını ve ağır kütüphaneleri (recharts, xlsx vb.) İNDİRMEZ.
import { Routes, Route, Navigate } from "react-router-dom";

import AdminLayout from "./pages/admin/AdminLayout";
import AdminDashboard from "./pages/admin/Dashboard";
import AdminProducts from "./pages/admin/Products";
import AdminOrders from "./pages/admin/Orders";
import AdminCategories from "./pages/admin/Categories";
import AdminVariants from "./pages/admin/Variants";
import AdminBanners from "./pages/admin/Banners";
import AdminSettings from "./pages/admin/Settings";
import AdminCampaigns from "./pages/admin/Campaigns";
import AdminPages from "./pages/admin/Pages";
import AdminPageDesign from "./pages/admin/PageDesign";
import AdminFooterDesign from "./pages/admin/FooterDesign";
import AdminMenu from "./pages/admin/MenuAdmin";
import AdminInstagram from "./pages/admin/Instagram";
import EmailMarketing from "./pages/admin/EmailMarketing";
import AdminIntegrations from "./pages/admin/Integrations";
import Payments from "./pages/admin/Payments";
import OrderStatusSettings from "./pages/admin/OrderStatusSettings";
import SenderAddress from "./pages/admin/SenderAddress";
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
import Vendors from "./pages/admin/Vendors";
import AdminUsersRoles from "./pages/admin/UsersRoles";
import Manufacturing from "./pages/admin/Manufacturing";
import Members from "./pages/admin/Members";
import Attribution from "./pages/admin/Attribution";
import SizeTablesList from "./pages/admin/SizeTablesList";
import Coupons from "./pages/admin/Coupons";
import GiftCards from "./pages/admin/GiftCards";
import ProductReviews from "./pages/admin/ProductReviews";
import AbandonedCarts from "./pages/admin/AbandonedCarts";
import { SalesReport, ProductsReport, StockReport, MembersReport } from "./pages/admin/Reports";
import { SeoRedirects, SeoMeta } from "./pages/admin/SeoAdmin";
import {
  Brands, ProductTags, MemberGroups, Announcements, Popups,
  StockAlerts, HavaleNotifications, Tickets, ShippingPaymentRules,
  CurrencyRates, BulkMail, ExtraReports,
} from "./pages/admin/CatalogExtras";
import AdminTasks from "./pages/admin/AdminTasks";
import BusinessRules from "./pages/admin/BusinessRules";
import CustomTheme from "./pages/admin/CustomTheme";
import TrainingPanel from "./pages/admin/TrainingPanel";
import EInvoiceSettings from "./pages/admin/EInvoiceSettings";
import CargoSettings from "./pages/admin/CargoSettings";
import NotificationSettings from "./pages/admin/NotificationSettings";
import EmailSettings from "./pages/admin/EmailSettings";
import NotificationTemplates from "./pages/admin/NotificationTemplates";
import BlockedCustomers from "./pages/admin/BlockedCustomers";
import MarketingPixels from "./pages/admin/MarketingPixels";
import Influencers from "./pages/admin/Influencers";
import AmazonSpApi from "./pages/admin/AmazonSpApi";
import Compliance from "./pages/admin/Compliance";
import CapiLogs from "./pages/admin/CapiLogs";
import ReportsAdvanced from "./pages/admin/ReportsAdvanced";
import ProfitabilityAnalysis from "./pages/admin/ProfitabilityAnalysis";
import ReportsInsights from "./pages/admin/ReportsInsights";
import XmlFeeds from "./pages/admin/XmlFeeds";
import SocialAuthSettings from "./pages/admin/SocialAuthSettings";
import MarketplaceHub from "./pages/admin/MarketplaceHub";
import IntegrationLogs from "./pages/admin/IntegrationLogs";
import FailedTransfers from "./pages/admin/FailedTransfers";
import BrandMapping from "./pages/admin/BrandMapping";
import CategoryMapping from "./pages/admin/CategoryMapping";
import BulkPriceStock from "./pages/admin/BulkPriceStock";
import StockAlerts2 from "./pages/admin/StockAlerts";
import CustomerSegments from "./pages/admin/CustomerSegments";
import AutomationStatus from "./pages/admin/AutomationStatus";
import SecurityDashboard from "./pages/admin/SecurityDashboard";
import SystemHealth from "./pages/admin/SystemHealth";
import SecretsVault from "./pages/admin/SecretsVault";
import IysAdmin from "./pages/admin/IysAdmin";
import MenuSettings from "./pages/admin/MenuSettings";
import ReportsExtended from "./pages/admin/ReportsExtended";
import MobileApp from "./pages/admin/MobileApp";
import AIAssistant from "./pages/admin/AIAssistant";
import FooterDesign from "./pages/admin/FooterDesign";
import MarketplaceProfit from "./pages/admin/MarketplaceProfit";
import Themes from "./pages/admin/Themes";
import RooftrExcelUpload from "./pages/admin/RooftrExcelUpload";

// Bu Routes, App.js'te "/admin/*" altına mount edilir; bu yüzden yollar
// /admin'e göreceli yazılır (örn. "urunler" => /admin/urunler).
export default function AdminApp() {
  return (
    <Routes>
      <Route path="login" element={<AdminLogin />} />
      <Route element={<AdminLayout />}>
        <Route index element={<AdminDashboard />} />
        <Route path="urunler" element={<AdminProducts />} />
        <Route path="urunler/:productId" element={<AdminProducts />} />
        <Route path="siparisler" element={<AdminOrders key="orders-all" />} />
        <Route path="odeme-bekleyen-siparisler" element={<AdminOrders key="orders-unpaid" unpaidView />} />
        <Route path="kategoriler" element={<AdminCategories />} />
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
        <Route path="ayarlar" element={<AdminSettings />} />
        <Route path="ayarlar/isletme-kurallari" element={<BusinessRules />} />
        <Route path="ayarlar/ozel-tema" element={<CustomTheme />} />
        <Route path="egitim" element={<TrainingPanel />} />
        <Route path="ayarlar/menu-duzeni" element={<MenuSettings />} />
        <Route path="ayarlar/e-fatura" element={<EInvoiceSettings />} />
        <Route path="ayarlar/kargo" element={<CargoSettings />} />
        <Route path="ayarlar/gonderici-adresi" element={<SenderAddress />} />
        <Route path="ayarlar/bildirim" element={<NotificationSettings />} />
        <Route path="ayarlar/eposta" element={<EmailSettings />} />
        <Route path="ayarlar/bildirim/sablonlar" element={<NotificationTemplates />} />
        <Route path="bloklu-musteriler" element={<BlockedCustomers />} />
        {/* İmalat Planı (Tablo) menüden kaldırıldı — eski yer imleri İmalat Takip'e gider */}
        <Route path="uretim-plani" element={<Navigate to="/admin/imalat" replace />} />
        <Route path="ayarlar/pixel" element={<MarketingPixels />} />
        <Route path="influencer" element={<Influencers />} />
        <Route path="amazon" element={<AmazonSpApi />} />
        <Route path="dpp-uyum" element={<Compliance />} />
        <Route path="ayarlar/capi-loglar" element={<CapiLogs />} />
        <Route path="raporlar/iade-ve-trend" element={<Navigate to="/admin/raporlar/urun" replace />} />
        <Route path="raporlar/konum-kanal" element={<Navigate to="/admin/raporlar/satis" replace />} />
        <Route path="ayarlar/sosyal-giris" element={<SocialAuthSettings />} />
        <Route path="ayarlar/siparis-durumlari" element={<OrderStatusSettings />} />
        <Route path="pazaryerleri" element={<MarketplaceHub />} />
        <Route path="entegrasyon-loglari" element={<IntegrationLogs />} />
        <Route path="aktarilamayanlar" element={<FailedTransfers />} />
        <Route path="marka-eslestir" element={<BrandMapping />} />
        <Route path="kategori-eslestir" element={<CategoryMapping />} />
        <Route path="toplu-fiyat-stok" element={<BulkPriceStock />} />
        <Route path="stok-uyarilari" element={<StockAlerts2 />} />
        <Route path="musteri-segmentleri" element={<CustomerSegments />} />
        <Route path="otomasyon" element={<AutomationStatus />} />
        <Route path="guvenlik-paneli" element={<SecurityDashboard />} />
        <Route path="sistem-sagligi" element={<SystemHealth />} />
        <Route path="secrets-vault" element={<SecretsVault />} />
        <Route path="iys" element={<IysAdmin />} />
        <Route path="mobil-uygulama" element={<MobileApp />} />
        <Route path="ai-asistan" element={<AIAssistant />} />
        <Route path="footer-tasarim" element={<FooterDesign />} />
        <Route path="pazaryeri-karlilik" element={<MarketplaceProfit />} />
        <Route path="trendyol-eslestir" element={<Navigate to="/admin/kategori-eslestir" replace />} />
        <Route path="trendyol-loglar" element={<TrendyolLogs />} />
        <Route path="barkod-sorunlari" element={<BarcodeIssues />} />
        <Route path="trendyol-hayalet" element={<TrendyolGhostScanner />} />
        <Route path="urun-ozellikleri" element={<ProductAttributes />} />
        <Route path="cariler" element={<Vendors />} />
        <Route path="kullanicilar" element={<AdminUsersRoles />} />
        <Route path="imalat" element={<Manufacturing />} />
        <Route path="uyeler" element={<Members />} />
        <Route path="kaynak" element={<Attribution />} />
        <Route path="hepsiburada-eslestir" element={<Navigate to="/admin/kategori-eslestir" replace />} />
        <Route path="temu-eslestir" element={<Navigate to="/admin/kategori-eslestir" replace />} />
        <Route path="olcu-tablolari" element={<SizeTablesList />} />
        <Route path="kuponlar" element={<Coupons />} />
        <Route path="hediye-cekleri" element={<GiftCards />} />
        <Route path="yorumlar" element={<ProductReviews />} />
        <Route path="terkedilmis-sepet" element={<AbandonedCarts />} />
        <Route path="raporlar/karlilik" element={<ProfitabilityAnalysis />} />
        <Route path="raporlar/karar-kurulu" element={<Navigate to="/admin/raporlar/urun" replace />} />
        <Route path="raporlar/satis" element={<SalesReport />} />
        <Route path="raporlar/urun" element={<ProductsReport />} />
        <Route path="raporlar/stok" element={<StockReport />} />
        <Route path="raporlar/uye" element={<MembersReport />} />
        <Route path="seo/meta" element={<SeoMeta />} />
        <Route path="seo/yonlendirmeler" element={<SeoRedirects />} />
        <Route path="markalar" element={<Brands />} />
        <Route path="etiketler" element={<ProductTags />} />
        <Route path="uye-gruplari" element={<MemberGroups />} />
        <Route path="duyurular" element={<Announcements />} />
        <Route path="popuplar" element={<Popups />} />
        <Route path="stok-alarm" element={<StockAlerts />} />
        <Route path="havale-bildirimleri" element={<HavaleNotifications />} />
        <Route path="tickets" element={<Tickets />} />
        <Route path="kargo-odeme-kurallari" element={<ShippingPaymentRules />} />
        <Route path="doviz" element={<CurrencyRates />} />
        <Route path="toplu-mail" element={<BulkMail />} />
        <Route path="raporlar/gelismis" element={<Navigate to="/admin/raporlar/satis" replace />} />
        <Route path="raporlar/kar-stok" element={<ReportsExtended />} />
        <Route path="gorevler" element={<AdminTasks />} />
      </Route>
    </Routes>
  );
}
