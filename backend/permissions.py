"""
Permission tree and role management.

All module/action permissions the admin panel supports, grouped hierarchically.
Frontend renders this tree as a nested checkbox matrix so admins can assign
fine-grained access per role.
"""

# Each leaf is a permission key. Groups carry children. The `key` on a group is
# treated as a synthetic "all children" shortcut in the UI.
PERMISSION_TREE = [
    {
        "key": "dashboard",
        "label": "Dashboard",
        "children": [
            {"key": "dashboard.view", "label": "Görüntüle"},
        ],
    },
    {
        "key": "products",
        "label": "Ürünler",
        "children": [
            {"key": "products.view", "label": "Listele / Görüntüle"},
            {"key": "products.create", "label": "Yeni Ekle"},
            {"key": "products.edit", "label": "Düzenle"},
            {"key": "products.delete", "label": "Sil"},
            {"key": "products.import_xlsx", "label": "Excel İçe Aktar"},
            {"key": "products.import_technical", "label": "Teknik Özellik Yükle"},
            {"key": "products.attributes", "label": "Özellik Kütüphanesi"},
            {"key": "products.size_table", "label": "Ölçü Tablosu Düzenle"},
            {"key": "products.categories", "label": "Kategori Yönet"},
        ],
    },
    {
        "key": "orders",
        "label": "Siparişler",
        "children": [
            {"key": "orders.view", "label": "Listele / Görüntüle"},
            {"key": "orders.create", "label": "Manuel Sipariş Oluştur"},
            {"key": "orders.update_status", "label": "Durum Değiştir"},
            {"key": "orders.delete", "label": "Sil / İptal Et"},
            {"key": "orders.note", "label": "Not Ekle"},
            {"key": "orders.invoice", "label": "Fatura Kes"},
            {"key": "orders.cargo", "label": "Kargo Etiketi / SMS"},
            {"key": "orders.trendyol_import", "label": "Trendyol'dan İçe Aktar"},
            {"key": "orders.cancelled_tab", "label": "İptal Edilenler Sekmesi"},
        ],
    },
    {
        "key": "returns",
        "label": "İadeler & İptaller",
        "children": [
            {"key": "returns.view", "label": "Listele / Görüntüle"},
            {"key": "returns.approve", "label": "İade Onayla"},
            {"key": "returns.reject", "label": "İade Reddet"},
            {"key": "returns.expense_note", "label": "Gider Pusulası Oluştur"},
            {"key": "returns.iyzico_refund", "label": "Iyzico Kart İadesi"},
            {"key": "returns.cargo_rebook", "label": "Yeni Kargo Barkodu"},
            {"key": "returns.refund_pay", "label": "İade Ödemesi Yap (Bedeli Ödendi)"},
        ],
    },
    {
        "key": "questions",
        "label": "Müşteri Soruları",
        "children": [
            {"key": "questions.view", "label": "Görüntüle"},
            {"key": "questions.answer", "label": "Yanıtla"},
            {"key": "questions.sync", "label": "Senkronize Et"},
        ],
    },
    {
        "key": "customers",
        "label": "Müşteriler",
        "children": [
            {"key": "customers.view", "label": "Görüntüle"},
            {"key": "customers.edit", "label": "Düzenle"},
            {"key": "customers.delete", "label": "Sil"},
        ],
    },
    {
        "key": "manufacturing",
        "label": "İmalat Takip",
        "children": [
            {"key": "manufacturing.view", "label": "Görüntüle"},
            {"key": "manufacturing.create", "label": "Yeni İmalat Kaydı"},
            {"key": "manufacturing.update_stage", "label": "Aşama Güncelle"},
            {"key": "manufacturing.delete", "label": "Sil"},
            {"key": "manufacturing.finance", "label": "Ödeme / Maliyet"},
            {"key": "manufacturing.files", "label": "Dosya Ekle"},
        ],
    },
    {
        "key": "campaigns",
        "label": "Kampanyalar",
        "children": [
            {"key": "campaigns.view", "label": "Görüntüle"},
            {"key": "campaigns.create", "label": "Oluştur"},
            {"key": "campaigns.edit", "label": "Düzenle"},
            {"key": "campaigns.delete", "label": "Sil"},
        ],
    },
    {
        "key": "integrations",
        "label": "Entegrasyonlar",
        "children": [
            {"key": "integrations.view", "label": "Görüntüle"},
            {"key": "integrations.trendyol", "label": "Trendyol Yapılandır"},
            {"key": "integrations.hepsiburada", "label": "Hepsiburada Yapılandır"},
            {"key": "integrations.temu", "label": "Temu Yapılandır"},
            {"key": "integrations.iyzico", "label": "Iyzico Yapılandır"},
            {"key": "integrations.mng", "label": "MNG Kargo"},
            {"key": "integrations.netgsm", "label": "NetGSM SMS"},
            {"key": "integrations.dogan_edonusum", "label": "Doğan e-Dönüşüm"},
            {"key": "integrations.xml_export", "label": "XML Export"},
            {"key": "integrations.sync_actions", "label": "Senkron İşlemleri"},
        ],
    },
    {
        "key": "reports",
        "label": "Raporlar",
        "children": [
            {"key": "reports.sales", "label": "Satış Raporları"},
            {"key": "reports.stock", "label": "Stok Raporları"},
            {"key": "reports.returns", "label": "İade Raporları"},
            {"key": "reports.financial", "label": "Finansal Raporlar"},
            {"key": "reports.export", "label": "Dışa Aktar"},
        ],
    },
    {
        "key": "tasarim",
        "label": "Tasarım / İçerik",
        "children": [
            {"key": "tasarim.view", "label": "Görüntüle"},
            {"key": "tasarim.themes", "label": "Tema Yönetimi"},
            {"key": "tasarim.banners", "label": "Bannerlar & Sliderlar"},
            {"key": "tasarim.popups", "label": "Popuplar & Duyurular"},
            {"key": "tasarim.menu", "label": "Menü Yönetimi"},
            {"key": "tasarim.page_design", "label": "Sayfa Tasarımı"},
            {"key": "tasarim.footer", "label": "Footer Tasarımı"},
            {"key": "tasarim.instagram", "label": "Instagram Akışı"},
            {"key": "tasarim.email", "label": "E-posta Pazarlama"},
            {"key": "tasarim.cms", "label": "Sayfalar (CMS)"},
        ],
    },
    {
        "key": "settings",
        "label": "Ayarlar",
        "children": [
            {"key": "settings.view", "label": "Görüntüle"},
            {"key": "settings.company", "label": "Şirket Bilgileri"},
            {"key": "settings.site", "label": "Site Ayarları"},
            {"key": "settings.emails", "label": "E-posta Şablonları"},
        ],
    },
    {
        "key": "admin",
        "label": "Yönetim",
        "children": [
            {"key": "admin.users", "label": "Kullanıcıları Yönet"},
            {"key": "admin.roles", "label": "Rolleri Yönet"},
            {"key": "admin.logs", "label": "Sistem Logları"},
            {"key": "admin.backup", "label": "Yedekleme"},
        ],
    },
]


def flatten_permissions():
    """Return all leaf permission keys as a flat list."""
    out = []
    def walk(nodes):
        for n in nodes:
            children = n.get("children")
            if children:
                walk(children)
            else:
                out.append(n["key"])
    walk(PERMISSION_TREE)
    return out


ALL_PERMISSION_KEYS = flatten_permissions()


# Preset roles shipped with the system
DEFAULT_ROLES = [
    {
        "id": "super_admin",
        "name": "Süper Admin",
        "description": "Tüm yetkilere sahiptir.",
        "permissions": ["*"],
        "is_system": True,
    },
    {
        "id": "operations",
        "name": "Operasyon",
        "description": "Sipariş/İade/Kargo yönetimi.",
        "permissions": [
            "dashboard.view",
            "products.view",
            "orders.view", "orders.update_status", "orders.note", "orders.invoice",
            "orders.cargo", "orders.trendyol_import", "orders.cancelled_tab",
            "returns.view", "returns.approve", "returns.reject", "returns.expense_note",
            "returns.cargo_rebook",
            "questions.view", "questions.answer", "questions.sync",
            "customers.view",
        ],
        "is_system": True,
    },
    {
        "id": "finance",
        "name": "Muhasebe / Finans",
        "description": "Fatura kesme ve finansal iade yetkisi.",
        "permissions": [
            "dashboard.view",
            "orders.view", "orders.invoice",
            "returns.view", "returns.iyzico_refund",
            "reports.financial", "reports.sales", "reports.returns", "reports.export",
            "settings.view", "settings.company",
            "integrations.dogan_edonusum",
        ],
        "is_system": True,
    },
    {
        "id": "manufacturing",
        "name": "İmalat Sorumlusu",
        "description": "İmalat Takip modülü yetkilisi.",
        "permissions": [
            "dashboard.view",
            "manufacturing.view", "manufacturing.create", "manufacturing.update_stage",
            "manufacturing.finance", "manufacturing.files",
            "products.view",
        ],
        "is_system": True,
    },
    {
        "id": "customer_service",
        "name": "Müşteri Hizmetleri",
        "description": "Müşteri soruları ve iade ön onayı.",
        "permissions": [
            "dashboard.view",
            "orders.view", "orders.note",
            "returns.view", "returns.approve", "returns.reject",
            "questions.view", "questions.answer", "questions.sync",
            "customers.view",
        ],
        "is_system": True,
    },
]
