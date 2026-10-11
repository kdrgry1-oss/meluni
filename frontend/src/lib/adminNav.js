/**
 * Admin menü tanımı — tek kaynak.
 * Her grup bir `key` ile tanımlanır; kullanıcı tercihlerinin (sıra/gizleme)
 * referans aldığı sabit anahtar budur. Etiketleri değiştirebilirsiniz, key'leri
 * DEĞİŞTİRMEYİN — aksi takdirde mevcut tercihler bozulur.
 */
import {
  LayoutDashboard, Package, ShoppingCart, Tags, Image, Phone,
  Megaphone, FileText, Settings, Palette, Plug, RotateCcw, Store, GitMerge, XCircle, Trash2,
  Cable, Building2, Sparkles, Shield, Factory, Users, Ruler, MessageSquare, PenTool,
  Truck, CreditCard, AlertTriangle, TrendingUp, Link2, BellRing, CheckSquare, Lock, Brain, Mail, Rss,
  Instagram, GraduationCap,
} from "lucide-react";

// Default sıralama (kullanıcı tercihi yoksa kullanılır):
// Siparişler → Katalog → Raporlar → Üretim → Tasarım → Üyeler → Görevler →
// Pazarlama → SEO → Entegrasyonlar → Ayarlar
export const navigationGroups = [
  {
    key: "siparisler",
    label: "Siparişler",
    icon: ShoppingCart,
    children: [
      { label: "Tüm Siparişler", path: "/admin/siparisler", icon: ShoppingCart },
      { label: "İadeler", path: "/admin/iadeler", icon: RotateCcw },
      { label: "İptaller", path: "/admin/iptaller", icon: XCircle },
      { label: "Havale/EFT Bildirimleri", path: "/admin/havale-bildirimleri", icon: CreditCard },
      { label: "Ödeme Kaydı Bulunmayan", path: "/admin/odeme-bekleyen-siparisler", icon: AlertTriangle },
      { label: "Ödeme İzi (Numaradan)", path: "/admin/odeme-izi", icon: CreditCard },
      { label: "Ödeme İtirazları", path: "/admin/odeme-itirazlari", icon: AlertTriangle },
      { label: "Silinen Siparişler", path: "/admin/silinen-siparisler", icon: Trash2 },
    ],
  },
  {
    key: "katalog",
    label: "Katalog",
    icon: Package,
    children: [
      { label: "Tüm Ürünler", path: "/admin/urunler", icon: Package },
      { label: "Kategoriler", path: "/admin/kategoriler", icon: Tags },
      { label: "Kategori Sıralama", path: "/admin/kategori-siralama", icon: LayoutDashboard },
      { label: "Markalar", path: "/admin/markalar", icon: Store },
      { label: "Etiketler", path: "/admin/etiketler", icon: Tags },
      { label: "Ürün Özellikleri", path: "/admin/urun-ozellikleri", icon: Tags },
      { label: "Varyantlar", path: "/admin/varyantlar", icon: GitMerge },
      { label: "XML Feed'ler", path: "/admin/xml-feedler", icon: Rss },
      { label: "Ölçü Tabloları", path: "/admin/olcu-tablolari", icon: Ruler },
      { label: "Stok & Fiyat Alarm", path: "/admin/stok-alarm", icon: BellRing },
      { label: "Toplu Fiyat/Stok (Excel)", path: "/admin/toplu-fiyat-stok", icon: Package },
      { label: "Stok Uyarıları", path: "/admin/stok-uyarilari", icon: BellRing },
    ],
  },
  {
    key: "raporlar",
    label: "Raporlar",
    icon: TrendingUp,
    children: [
      { label: "Satış Raporları", path: "/admin/raporlar/satis", icon: TrendingUp },
      { label: "Kâr & Stok Değer", path: "/admin/raporlar/kar-stok", icon: TrendingUp },
      { label: "Ürün Raporları", path: "/admin/raporlar/urun", icon: Package },
      { label: "Stok Raporu", path: "/admin/raporlar/stok", icon: Package },
    ],
  },
  {
    key: "uretim",
    label: "Üretim",
    icon: Factory,
    children: [
      { label: "İmalat Takip", path: "/admin/imalat", icon: Factory },
    ],
  },
  {
    key: "tasarim",
    label: "Tasarım",     // ← eski adı "İçerik"
    icon: PenTool,
    children: [
      { label: "Tema Yönetimi", path: "/admin/temalar", icon: Palette },
      { label: "Bannerlar & Sliderlar", path: "/admin/bannerlar", icon: Image },
      { label: "Popuplar", path: "/admin/popuplar", icon: BellRing },
      { label: "Duyurular", path: "/admin/duyurular", icon: BellRing },
      { label: "Menü Yönetimi", path: "/admin/menu-yonetimi", icon: LayoutDashboard },
      { label: "Sayfa Tasarımı", path: "/admin/sayfa-tasarimi", icon: Palette },
      { label: "Footer Tasarımı", path: "/admin/footer-tasarim", icon: Palette },
      { label: "Instagram Akışı", path: "/admin/instagram", icon: Instagram },
      { label: "Sayfalar (CMS)", path: "/admin/sayfalar", icon: FileText },
    ],
  },
  {
    key: "uyeler",
    label: "Üyeler",
    icon: Users,
    children: [
      { label: "Üye Listesi", path: "/admin/uyeler", icon: Users },
      { label: "KVKK Talepleri", path: "/admin/kvkk-talepleri", icon: Users },
      { label: "Müşteri Segmentleri (RFM)", path: "/admin/musteri-segmentleri", icon: Users },
      { label: "Müşteri Soruları", path: "/admin/sorular", icon: MessageSquare },
      { label: "Destek Talepleri", path: "/admin/tickets", icon: MessageSquare },
      { label: "Bloklu Müşteriler", path: "/admin/bloklu-musteriler", icon: Users },
      { label: "Pazarlama İzinleri (E-posta/SMS)", path: "/admin/izinler", icon: Users },
    ],
  },
  {
    key: "gorevler",
    label: "Görevler",
    path: "/admin/gorevler",
    icon: CheckSquare,
  },
  {
    key: "egitim",
    label: "Eğitim & Yardım",
    path: "/admin/egitim",
    icon: GraduationCap,
  },
  {
    key: "pazarlama",
    label: "Pazarlama",
    icon: Megaphone,
    children: [
      { label: "Kampanyalar", path: "/admin/kampanyalar", icon: Megaphone },
      { label: "Kargo/Ödeme Kuralları", path: "/admin/kargo-odeme-kurallari", icon: Truck },
      { label: "Kuponlar", path: "/admin/kuponlar", icon: Tags },
      { label: "Hediye Çekleri", path: "/admin/hediye-cekleri", icon: Tags },
      { label: "E-posta Pazarlama", path: "/admin/eposta-pazarlama", icon: Mail },
      { label: "Ürün Yorumları", path: "/admin/yorumlar", icon: MessageSquare },
      { label: "Terkedilmiş Sepetler", path: "/admin/terkedilmis-sepet", icon: ShoppingCart },
      { label: "Kaynak & Funnel", path: "/admin/kaynak", icon: TrendingUp },
      { label: "Influencer / İş Birlikleri", path: "/admin/influencer", icon: TrendingUp },
    ],
  },
  {
    key: "seo",
    label: "SEO",
    icon: FileText,
    children: [
      { label: "Meta Yönetimi", path: "/admin/seo/meta", icon: FileText },
      { label: "301 Yönlendirmeler", path: "/admin/seo/yonlendirmeler", icon: Link2 },
    ],
  },
  {
    key: "entegrasyonlar",
    label: "Entegrasyonlar",
    icon: Cable,
    children: [
      // Dropshipping'in giriş kapısı: besleme ayarı, ürün aktarımı, stok
      // senkronu. Grubun başında duruyor; adı yalnız "Tedarikçi XML" iken
      // uzun listede gözden kaçıyordu.
      { label: "Tedarikçi XML (Dropshipping)", path: "/admin/tedarikci-xml", icon: Cable },
      { label: "Görsel Üretimi (Yapay Zekâ)", path: "/admin/gorsel-uretimi", icon: Sparkles },
      { label: "Ödeme Tipleri", path: "/admin/odeme-tipleri", icon: CreditCard },
      { label: "Pazaryerleri Hub", path: "/admin/pazaryerleri", icon: Store, superOnly: true },
      { label: "Amazon", path: "/admin/amazon", icon: Store },
      { label: "Detaylı Aktarım & Eşleştirme", path: "/admin/entegrasyonlar", icon: Cable },
      { label: "Entegrasyon Logları", path: "/admin/entegrasyon-loglari", icon: FileText, superOnly: true },
      { label: "Aktarılamayanlar", path: "/admin/aktarilamayanlar", icon: FileText, superOnly: true },
      { label: "Marka Eşleştirme", path: "/admin/marka-eslestir", icon: Store, superOnly: true },
      { label: "Kategori Eşleştirme", path: "/admin/kategori-eslestir", icon: Store, superOnly: true },
      { label: "Otomasyon Durumu", path: "/admin/otomasyon", icon: Cable },
      { label: "Güvenlik Paneli", path: "/admin/guvenlik-paneli", icon: Shield },
      { label: "Acil Anahtarlar", path: "/admin/acil-anahtarlar", icon: Shield, superOnly: true },
      { label: "Kullanıcı İşlem Geçmişi", path: "/admin/islem-gecmisi", icon: FileText },
      { label: "Sistem Sağlığı", path: "/admin/sistem-sagligi", icon: Cable },
      { label: "Secrets Vault", path: "/admin/secrets-vault", icon: Lock },
      { label: "İYS (İzin Yönetim Sistemi)", path: "/admin/iys", icon: Cable },
      { label: "Mobil Uygulama", path: "/admin/mobil-uygulama", icon: BellRing },
      { label: "AI Asistan", path: "/admin/ai-asistan", icon: Brain },
    ],
  },
  {
    key: "ayarlar",
    label: "Ayarlar",
    icon: Settings,
    children: [
      { label: "Genel Ayarlar", path: "/admin/ayarlar", icon: Settings },
      { label: "Webmail'e Git (Zoho)", href: "https://mail.zoho.eu", external: true, icon: Mail },
    ],
  },
];

// localStorage anahtarları — kullanıcı bazlı
const orderKey = (uid) => `menuOrder:${uid || "anon"}`;
const hiddenKey = (uid) => `menuHidden:${uid || "anon"}`;

export function loadUserMenuPrefs(userId) {
  try {
    const order = JSON.parse(localStorage.getItem(orderKey(userId)) || "null");
    const hidden = JSON.parse(localStorage.getItem(hiddenKey(userId)) || "[]");
    return { order: Array.isArray(order) ? order : null, hidden: Array.isArray(hidden) ? hidden : [] };
  } catch { return { order: null, hidden: [] }; }
}

export function saveUserMenuPrefs(userId, { order, hidden }) {
  if (Array.isArray(order)) localStorage.setItem(orderKey(userId), JSON.stringify(order));
  if (Array.isArray(hidden)) localStorage.setItem(hiddenKey(userId), JSON.stringify(hidden));
}

export function resetUserMenuPrefs(userId) {
  localStorage.removeItem(orderKey(userId));
  localStorage.removeItem(hiddenKey(userId));
}

/**
 * Kullanıcının görmek istediği sıralı ve filtrelenmiş menü grupları.
 * `order` listesinde olmayan yeni eklenen menü grupları default sırasında sona eklenir.
 */
// Süper-admin'e özel yollar — arka uç zaten kilitli (require_super_admin);
// burada menüden de gizlenir ki kimse 403 alan bir sayfaya tıklamasın.
export const SUPER_ONLY_PATHS = ["/admin/pazaryerleri", "/admin/entegrasyon-loglari",
                                 "/admin/aktarilamayanlar", "/admin/marka-eslestir",
                                 "/admin/kategori-eslestir"];

export function isSuperAdmin(perms) {
  return Array.isArray(perms) && perms.includes("*");
}

// Sayfa (menü) görüntüleme izinleri — IAM-02 en az yetki. Arka uçta her panel ucu, onu çağıran
// sayfanın buradaki iznini (okuma) ya da o izne sahip rollere tek seferlik geçişle verilen yazma
// iznini ister (backend/permissions.py → PERMISSION_UPGRADE_GRANTS). Değer: izinlerden HERHANGİ
// BİRİ yeterli. Listede olmayan sayfa (Görevler, Eğitim & Yardım) tüm personele açıktır.
// backend/tests/security_e2e/test_iam02_least_privilege.py bu tabloyu okuyup her sayfanın
// çağırdığı her ucun bu izinle kullanılabildiğini doğrular — biçimi ("yol": ["izin", ...]) koruyun.
const REPORT_PERMS = ["reports.sales", "reports.stock", "reports.returns", "reports.financial", "reports.export"];
export const PAGE_PERMS = {
  "/admin/tedarikci-xml": ["products.view"],
  "/admin/gorsel-uretimi": ["products.view"],
  "/admin/secrets-vault": ["settings.view"],
  "/admin": ["dashboard.view"],
  "/admin/siparisler": ["orders.view"],
  "/admin/odeme-bekleyen-siparisler": ["orders.view"],
  "/admin/iptaller": ["orders.view"],
  "/admin/silinen-siparisler": ["orders.view"],
  "/admin/odeme-izi": ["orders.view"],
  "/admin/havale-bildirimleri": ["orders.view"],
  "/admin/iadeler": ["returns.view"],
  "/admin/odeme-itirazlari": ["returns.refund_pay"],
  "/admin/urunler": ["products.view"],
  "/admin/kategoriler": ["products.view"],
  "/admin/kategori-siralama": ["products.view"],
  "/admin/markalar": ["products.view"],
  "/admin/etiketler": ["products.view"],
  "/admin/urun-ozellikleri": ["products.view"],
  "/admin/varyantlar": ["products.view"],
  "/admin/xml-feedler": ["products.view"],
  "/admin/olcu-tablolari": ["products.view"],
  "/admin/stok-alarm": ["products.view"],
  "/admin/toplu-fiyat-stok": ["products.view"],
  "/admin/stok-uyarilari": ["products.view"],
  "/admin/rooftr-excel": ["products.view"],
  "/admin/barkod-sorunlari": ["products.view"],
  "/admin/raporlar/satis": REPORT_PERMS,
  "/admin/raporlar/kar-stok": REPORT_PERMS,
  "/admin/raporlar/urun": REPORT_PERMS,
  "/admin/raporlar/stok": REPORT_PERMS,
  "/admin/raporlar/uye": REPORT_PERMS,
  "/admin/imalat": ["manufacturing.view"],
  "/admin/temalar": ["tasarim.view"],
  "/admin/bannerlar": ["tasarim.view"],
  "/admin/popuplar": ["tasarim.view"],
  "/admin/duyurular": ["tasarim.view"],
  "/admin/menu-yonetimi": ["tasarim.view"],
  "/admin/sayfa-tasarimi": ["tasarim.view"],
  "/admin/footer-tasarim": ["tasarim.view"],
  "/admin/instagram": ["tasarim.view"],
  "/admin/sayfalar": ["tasarim.view"],
  "/admin/seo/meta": ["tasarim.view"],
  "/admin/seo/yonlendirmeler": ["tasarim.view"],
  "/admin/uyeler": ["customers.view"],
  "/admin/kvkk-talepleri": ["admin.compliance"],
  "/admin/musteri-segmentleri": ["customers.view"],
  "/admin/sorular": ["questions.view"],
  "/admin/tickets": ["customers.view"],
  "/admin/bloklu-musteriler": ["customers.view"],
  "/admin/izinler": ["customers.view"],
  "/admin/kampanyalar": ["campaigns.view"],
  "/admin/kargo-odeme-kurallari": ["campaigns.view"],
  "/admin/kuponlar": ["campaigns.view"],
  "/admin/hediye-cekleri": ["campaigns.view"],
  "/admin/eposta-pazarlama": ["tasarim.email"],
  "/admin/yorumlar": ["campaigns.view"],
  "/admin/terkedilmis-sepet": ["campaigns.view"],
  "/admin/kaynak": ["campaigns.view"],
  "/admin/influencer": ["campaigns.view"],
  "/admin/odeme-tipleri": ["settings.view"],
  "/admin/amazon": ["integrations.view"],
  "/admin/entegrasyonlar": ["integrations.view"],
  "/admin/otomasyon": ["integrations.view"],
  "/admin/trendyol-loglar": ["integrations.view"],
  "/admin/trendyol-hayalet": ["integrations.view"],
  "/admin/iys": ["integrations.view"],
  "/admin/mobil-uygulama": ["integrations.view"],
  "/admin/ai-asistan": ["questions.view"],
  "/admin/guvenlik-paneli": ["admin.logs"],
  "/admin/sistem-sagligi": ["admin.logs"],
  "/admin/islem-gecmisi": ["audit.read"],
  "/admin/ayarlar": ["settings.view"],
};

/** Personel bu sayfayı görebilir mi? perms yüklenmediyse (undefined/null) ya da süper adminse evet. */
// Bu alanların ".view" izni IAM-02 öncesinden zorunluydu; o alanların okuma uçları arka uçta
// require_permission(..., any_in_area=True) ile alandaki HERHANGİ bir izinle de açılır → menü de öyle.
const AREA_READ_AREAS = ["products", "customers", "campaigns", "integrations"];

function _grants(perms, p) {
  if (perms.includes(p)) return true;
  const [area, action] = String(p).split(".");
  return action === "view" && AREA_READ_AREAS.includes(area) && perms.some((x) => String(x).startsWith(`${area}.`));
}

export function canSeeAdminPath(path, perms) {
  if (perms === undefined || perms === null || isSuperAdmin(perms)) return true;
  const need = PAGE_PERMS[path];
  if (!need) return true;
  return Array.isArray(perms) && need.some((p) => _grants(perms, p));
}

export function getNavigationFor(userId, perms) {
  const { order, hidden } = loadUserMenuPrefs(userId);
  const hiddenSet = new Set(hidden || []);
  // perms verilmediyse (henüz yüklenmediyse) menü DEĞİŞTİRİLMEZ — yükleme
  // sırasında girdiler bir an kaybolup geri gelmesin.
  const superOk = perms === undefined || perms === null || isSuperAdmin(perms);
  const byKey = Object.fromEntries(navigationGroups.map((g) => [g.key, g]));
  let ordered;
  if (order && order.length) {
    ordered = [];
    const seen = new Set();
    for (const k of order) {
      if (byKey[k]) { ordered.push(byKey[k]); seen.add(k); }
    }
    // Yeni eklenen gruplar listede yoksa sona ekle
    for (const g of navigationGroups) if (!seen.has(g.key)) ordered.push(g);
  } else {
    ordered = navigationGroups;
  }
  const visible = ordered.filter((g) => !hiddenSet.has(g.key));
  if (superOk) return visible;
  // Yetkisi olmayan sayfa menüde gösterilmez (tıklayınca 403 alan sayfa olmasın). Dış bağlantılar
  // (Webmail) ait oldukları grubun görünürlüğünü tek başına taşımaz.
  return visible
    .map((g) => (g.children
      ? { ...g, children: g.children.filter((c) => !c.superOnly && (c.external || canSeeAdminPath(c.path, perms))) }
      : g))
    .filter((g) => (g.children
      ? g.children.some((c) => !c.external)
      : canSeeAdminPath(g.path, perms)));
}
