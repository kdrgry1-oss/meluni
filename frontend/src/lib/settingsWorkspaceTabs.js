export const SETTINGS_DEFAULT_TAB = "general";

export const SETTINGS_TAB_GROUPS = [
  {
    label: "Mağaza",
    tabs: [
      { key: "general", label: "Genel Ayarlar", description: "Mağaza, iletişim ve bakım ayarları" },
      { key: "business-rules", label: "İşletme Kuralları", description: "Süre, ücret ve operasyon eşikleri" },
    ],
  },
  {
    label: "Sipariş & Finans",
    tabs: [
      { key: "order-statuses", label: "Sipariş Durumları" },
      { key: "einvoice", label: "E-Arşiv / E-Fatura" },
      { key: "currency", label: "Döviz Kurları" },
    ],
  },
  {
    label: "Teslimat",
    tabs: [
      { key: "cargo", label: "Kargo Firmaları" },
      { key: "sender-address", label: "Gönderici / Depo Adresi" },
    ],
  },
  {
    label: "İletişim",
    tabs: [
      { key: "notifications", label: "Bildirim Sağlayıcıları" },
      { key: "notification-templates", label: "Bildirim Şablonları" },
      { key: "email", label: "E-posta" },
      { key: "social-login", label: "Sosyal Giriş" },
    ],
  },
  {
    label: "Görünüm & Panel",
    tabs: [
      { key: "custom-theme", label: "Özel Tema (CSS/JS)" },
      { key: "menu-layout", label: "Menü Düzeni" },
    ],
  },
  {
    label: "Pazarlama & İzleme",
    tabs: [
      { key: "pixels", label: "Pazarlama Pixelleri" },
      { key: "capi", label: "CAPI Logları & Kuyruk" },
    ],
  },
  {
    label: "Ekip & Tedarik",
    tabs: [
      { key: "users-roles", label: "Kullanıcılar & Roller" },
      { key: "vendors", label: "Cariler" },
    ],
  },
];

export const SETTINGS_TAB_KEYS = new Set(
  SETTINGS_TAB_GROUPS.flatMap((group) => group.tabs.map((tab) => tab.key)),
);

export const LEGACY_SETTINGS_TABS = {
  "/admin/ayarlar/isletme-kurallari": "business-rules",
  "/admin/ayarlar/ozel-tema": "custom-theme",
  "/admin/ayarlar/menu-duzeni": "menu-layout",
  "/admin/ayarlar/e-fatura": "einvoice",
  "/admin/ayarlar/kargo": "cargo",
  "/admin/ayarlar/gonderici-adresi": "sender-address",
  "/admin/ayarlar/bildirim": "notifications",
  "/admin/ayarlar/eposta": "email",
  "/admin/ayarlar/bildirim/sablonlar": "notification-templates",
  "/admin/ayarlar/pixel": "pixels",
  "/admin/ayarlar/capi-loglar": "capi",
  "/admin/ayarlar/sosyal-giris": "social-login",
  "/admin/ayarlar/siparis-durumlari": "order-statuses",
  "/admin/doviz": "currency",
  "/admin/kullanicilar": "users-roles",
  "/admin/cariler": "vendors",
};

export function normalizeSettingsTab(search = "") {
  const requested = new URLSearchParams(search).get("tab") || SETTINGS_DEFAULT_TAB;
  return SETTINGS_TAB_KEYS.has(requested) ? requested : SETTINGS_DEFAULT_TAB;
}

export function settingsTabPath(tab) {
  const normalized = SETTINGS_TAB_KEYS.has(tab) ? tab : SETTINGS_DEFAULT_TAB;
  return normalized === SETTINGS_DEFAULT_TAB
    ? "/admin/ayarlar"
    : `/admin/ayarlar?tab=${encodeURIComponent(normalized)}`;
}

export function legacySettingsRedirect(pathname) {
  return settingsTabPath(LEGACY_SETTINGS_TABS[pathname]);
}
