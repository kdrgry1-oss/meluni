/**
 * Admin menü tanımı — tek kaynak.
 * Her grup bir `key` ile tanımlanır; kullanıcı tercihlerinin (sıra/gizleme)
 * referans aldığı sabit anahtar budur. Etiketleri değiştirebilirsiniz, key'leri
 * DEĞİŞTİRMEYİN — aksi takdirde mevcut tercihler bozulur.
 */
import {
  LayoutDashboard, Package, ShoppingCart, Tags, Image, Phone,
  Megaphone, FileText, Settings, Palette, Plug, RotateCcw, Store, GitMerge, XCircle, Trash2,
  Cable, Building2, Shield, Factory, Users, Ruler, MessageSquare, PenTool,
  Truck, CreditCard, AlertTriangle, TrendingUp, Link2, BellRing, CheckSquare, Code, Lock, Brain, Mail, Rss,
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
      { label: "Kârlılık Analizi", path: "/admin/raporlar/karlilik", icon: TrendingUp },
      { label: "Kâr & Stok Değer", path: "/admin/raporlar/kar-stok", icon: TrendingUp },
      { label: "Ürün Raporları", path: "/admin/raporlar/urun", icon: Package },
      { label: "Stok Raporu", path: "/admin/raporlar/stok", icon: Package },
      { label: "Üye Raporu", path: "/admin/raporlar/uye", icon: Users },
      { label: "Pazaryeri Karlılık", path: "/admin/pazaryeri-karlilik", icon: TrendingUp },
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
      { label: "E-posta Pazarlama", path: "/admin/eposta-pazarlama", icon: MessageSquare },
      { label: "Sayfalar (CMS)", path: "/admin/sayfalar", icon: FileText },
    ],
  },
  {
    key: "uyeler",
    label: "Üyeler",
    icon: Users,
    children: [
      { label: "Üye Listesi", path: "/admin/uyeler", icon: Users },
      { label: "Üye Grupları (B2B)", path: "/admin/uye-gruplari", icon: Users },
      { label: "Müşteri Segmentleri (RFM)", path: "/admin/musteri-segmentleri", icon: Users },
      { label: "Müşteri Soruları", path: "/admin/sorular", icon: MessageSquare },
      { label: "Destek Talepleri", path: "/admin/tickets", icon: MessageSquare },
      { label: "Bloklu Müşteriler", path: "/admin/bloklu-musteriler", icon: Users },
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
      { label: "Toplu Mail", path: "/admin/toplu-mail", icon: MessageSquare },
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
      { label: "Ödeme Tipleri", path: "/admin/odeme-tipleri", icon: CreditCard },
      { label: "Pazaryerleri Hub", path: "/admin/pazaryerleri", icon: Store },
      { label: "Amazon SP-API", path: "/admin/amazon", icon: Store },
      { label: "Amazon DPP Uyum", path: "/admin/dpp-uyum", icon: Store },
      { label: "Detaylı Aktarım & Eşleştirme", path: "/admin/entegrasyonlar", icon: Cable },
      { label: "Entegrasyon Logları", path: "/admin/entegrasyon-loglari", icon: FileText },
      { label: "Aktarılamayanlar", path: "/admin/aktarilamayanlar", icon: FileText },
      { label: "Marka Eşleştirme", path: "/admin/marka-eslestir", icon: Store },
      { label: "Kategori Eşleştirme", path: "/admin/kategori-eslestir", icon: Store },
      { label: "Otomasyon Durumu", path: "/admin/otomasyon", icon: Cable },
      { label: "Güvenlik Paneli", path: "/admin/guvenlik-paneli", icon: Shield },
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
      { label: "İşletme Kuralları", path: "/admin/ayarlar/isletme-kurallari", icon: Settings },
      { label: "Özel Tema (CSS/JS)", path: "/admin/ayarlar/ozel-tema", icon: Code },
      { label: "Menü Düzeni", path: "/admin/ayarlar/menu-duzeni", icon: LayoutDashboard },
      { label: "E-Arşiv / E-Fatura", path: "/admin/ayarlar/e-fatura", icon: FileText },
      { label: "Kargo Firması Ayarları", path: "/admin/ayarlar/kargo", icon: Truck },
      { label: "Gönderici / Depo Adresi", path: "/admin/ayarlar/gonderici-adresi", icon: Building2 },
      { label: "Bildirim Ayarları", path: "/admin/ayarlar/bildirim", icon: Settings },
      { label: "E-posta (SMTP)", path: "/admin/ayarlar/eposta", icon: Mail },
      { label: "Webmail'e Git (Zoho)", href: "https://mail.zoho.eu", external: true, icon: Mail },
      { label: "Bildirim Şablonları", path: "/admin/ayarlar/bildirim/sablonlar", icon: FileText },
      { label: "Sipariş Durumları", path: "/admin/ayarlar/siparis-durumlari", icon: ShoppingCart },
      { label: "Pazarlama Pixelleri", path: "/admin/ayarlar/pixel", icon: Code },
      { label: "CAPI Loglar & Kuyruk", path: "/admin/ayarlar/capi-loglar", icon: Code },
      { label: "Sosyal Giriş Ayarları", path: "/admin/ayarlar/sosyal-giris", icon: Lock },
      { label: "Döviz Kurları", path: "/admin/doviz", icon: Settings },
      { label: "Kullanıcılar & Roller", path: "/admin/kullanicilar", icon: Shield },
      { label: "Cariler", path: "/admin/cariler", icon: Building2 },
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
export function getNavigationFor(userId) {
  const { order, hidden } = loadUserMenuPrefs(userId);
  const hiddenSet = new Set(hidden || []);
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
  return ordered.filter((g) => !hiddenSet.has(g.key));
}
