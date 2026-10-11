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
            {"key": "products.marketplace_sync", "label": "Ürün / Kategori Pazaryerine Gönder (Trendyol içerik-stok-fiyat)"},
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
            {"key": "orders.edit", "label": "Sipariş Düzenle / Geri Yükle / Havale-Stok İşlemleri"},
            {"key": "orders.ship", "label": "Kargoya Ver / Kargo Barkodu / Takip Yenile"},
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
            {"key": "returns.manage", "label": "İade Kaydı / Pazaryeri İade Taleplerini Yönet"},
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
            {"key": "customers.block", "label": "Müşteri Engelle / Engel Kaldır"},
            {"key": "customers.message", "label": "Müşterilere Toplu E-posta Gönder"},
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
            {"key": "campaigns.push_send", "label": "Mobil Push Bildirim Gönder (toplu)"},
            {"key": "campaigns.manage", "label": "Kampanya Güncelle / Kargo-Ödeme Kuralları / Yorum Moderasyonu"},
            {"key": "campaigns.influencers", "label": "Influencer / İş Birliği İşlemleri"},
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
            {"key": "integrations.amazon", "label": "Amazon Yapılandır / İşlemleri"},
            {"key": "integrations.iyzico", "label": "Iyzico Yapılandır"},
            {"key": "integrations.mng", "label": "MNG Kargo"},
            {"key": "integrations.netgsm", "label": "NetGSM SMS"},
            {"key": "integrations.dogan_edonusum", "label": "Doğan e-Dönüşüm"},
            {"key": "integrations.xml_export", "label": "XML Export"},
            {"key": "integrations.sync_actions", "label": "Senkron İşlemleri"},
            {"key": "integrations.edit", "label": "Diğer Entegrasyon / Kargo Ayarlarını Düzenle"},
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
            {"key": "tasarim.seo", "label": "SEO (Meta / 301 Yönlendirme)"},
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
            {"key": "settings.payment", "label": "Ödeme Yöntemleri / Banka Hesapları (IBAN)"},
            {"key": "settings.edit", "label": "Diğer Ayarları Düzenle (iş kuralları, bildirim, kargo, piksel, cariler…)"},
        ],
    },
    {
        "key": "audit",
        "label": "İşlem Geçmişi",
        "children": [
            {"key": "audit.read", "label": "Genel işlem geçmişini görüntüle"},
        ],
    },
    {
        "key": "admin",
        "label": "Yönetim",
        "children": [
            {"key": "admin.users", "label": "Kullanıcıları Yönet"},
            {"key": "admin.roles", "label": "Rolleri Yönet"},
            {"key": "admin.logs", "label": "Sistem Logları / Güvenlik Paneli / Sistem Sağlığı"},
            {"key": "admin.backup", "label": "Yedekleme"},
            {"key": "admin.compliance", "label": "KVKK / Kişisel Veri Saklama (PII) Ayarları"},
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


# ─────────────── IAM-02: en az yetki geçişi (least privilege) ───────────────
# Önceden YALNIZ require_admin ile korunan (her personelin çağırabildiği) ~720 panel ucu artık
# sayfasının/özelliğinin iznini ister (routes/*: require_permission). Bu değişiklik yürürlükteki
# rollerden erişim GERİ ALMAMALI ("kullanıcının yaptığı değişiklik kalıcıdır"):
#
#   * Uçlar sayfanın görüntüleme izni (okuma) ya da BU DEĞİŞİKLİKTEN ÖNCE HİÇBİR UÇTA ZORUNLU
#     OLMAYAN ("uyuyan") / YENİ izinlerle (yazma) korunur.
#   * Tek seferlik geçiş (başlangıçta, bayrakla bir kez, idempotent, yalnız EKLER, süper admin '*'
#     rolüne dokunmaz) bu uyuyan/yeni izinleri, rol ekranında AYNI ALANDA (ör. "Siparişler")
#     herhangi bir izni olan MEVCUT her role ekler → bugün o alanın ekranlarını kullanan kimse
#     erişim kaybetmez. Hiç izni olmayan alanlar (ör. İmalat sorumlusunun Siparişler'i) kapanır —
#     en az yetkinin asıl kazancı budur. Yeni oluşturulan roller ince taneli kurulabilir.
#   * Önceden başka uçlarda ZORUNLU olan izinler (PRE_ENFORCED_PERMISSIONS) ASLA eklenmez —
#     eklemek, rolün bugün yapamadığı bir işlemi açardı (yetki yükseltme).
PRE_ENFORCED_PERMISSIONS = frozenset({
    "admin.compliance", "audit.read",
    "campaigns.create", "campaigns.edit", "campaigns.push_send", "campaigns.view",
    "customers.block", "customers.delete", "customers.export", "customers.view",
    "integrations.dogan_edonusum", "integrations.iyzico", "integrations.mng", "integrations.trendyol",
    "integrations.view",
    "orders.cargo", "orders.delete", "orders.invoice", "orders.update_status",
    "products.categories", "products.delete", "products.view",
    "reports.export",
    "returns.approve", "returns.cargo_rebook", "returns.expense_note", "returns.iyzico_refund",
    "returns.refund_pay", "returns.reject",
    "settings.emails", "settings.payment", "settings.site",
    "tasarim.email",
})

# IAM-02 ile ilk kez zorunlu olan izinler → (kendi alanı dışında) çapa olarak sayılan EK alanlar.
# Ek alan: o alanın ekranı da aynı ucu çağırır (ör. Entegrasyonlar sayfası da "ürünü Trendyol'a gönder").
_IAM02_NEWLY_ENFORCED = {
    "products.create": (), "products.edit": (), "products.import_xlsx": (), "products.import_technical": (),
    "products.attributes": (), "products.size_table": (),
    "products.marketplace_sync": ("integrations",),
    "orders.view": (), "orders.create": (), "orders.note": (), "orders.edit": (), "orders.ship": (),
    "orders.trendyol_import": ("integrations",),
    "returns.view": (), "returns.manage": (),
    "questions.view": (), "questions.answer": (), "questions.sync": (),
    "customers.edit": (), "customers.message": (),
    "manufacturing.view": (), "manufacturing.create": (), "manufacturing.update_stage": (),
    "manufacturing.delete": (), "manufacturing.files": (),
    "campaigns.delete": (), "campaigns.manage": (), "campaigns.influencers": (),
    "integrations.hepsiburada": (), "integrations.temu": (), "integrations.amazon": (),
    "integrations.netgsm": (), "integrations.sync_actions": (), "integrations.edit": (),
    "integrations.xml_export": ("products",),  # XML Feed'ler Katalog menüsünde
    "tasarim.view": (), "tasarim.themes": (), "tasarim.banners": (), "tasarim.popups": (), "tasarim.menu": (),
    "tasarim.page_design": (), "tasarim.footer": (), "tasarim.instagram": (), "tasarim.cms": (), "tasarim.seo": (),
    "settings.view": (), "settings.company": (), "settings.edit": (),
    "reports.sales": (), "reports.stock": (), "reports.returns": (), "reports.financial": (),
    "admin.logs": (),
}


def _area_keys(area: str) -> tuple:
    for grp in PERMISSION_TREE:
        if grp.get("key") == area:
            return tuple(c["key"] for c in grp.get("children") or [])
    return ()


# izin → bu izni (tek seferlik geçişte) alacak rolün sahip olması gereken çapa izinlerinden BİRİ
# (= izinle aynı alandaki ya da ek alanlardaki herhangi bir izin).
PERMISSION_UPGRADE_GRANTS = {
    perm: tuple(sorted({k for area in (perm.split(".", 1)[0],) + extra for k in _area_keys(area)} - {perm}))
    for perm, extra in _IAM02_NEWLY_ENFORCED.items()
}
assert not set(PERMISSION_UPGRADE_GRANTS) & PRE_ENFORCED_PERMISSIONS  # yetki yükseltme yok

UPGRADE_GRANTS_FLAG = "iam_least_privilege_v1"

# Denetim #3 (en az yetki): para/stok/pazaryeri/kimlik bilgisi etkisi olan izinler HİÇBİR otomatik
# geçişte role eklenmez — yalnız süper admin rol ekranından bilinçli olarak verir.
SENSITIVE_NO_AUTO_GRANT = frozenset({
    "integrations.hepsiburada", "integrations.temu", "integrations.amazon", "integrations.netgsm",
    "integrations.sync_actions", "integrations.edit", "integrations.xml_export",
    "orders.trendyol_import", "products.marketplace_sync", "products.import_xlsx",
    "products.import_technical",
    "settings.edit", "settings.company",
    "manufacturing.delete", "campaigns.delete", "campaigns.manage",
    "customers.message", "reports.financial", "admin.logs",
})
assert SENSITIVE_NO_AUTO_GRANT <= set(PERMISSION_UPGRADE_GRANTS)


def upgrade_grants_for(perms) -> list:
    """Verilen izin listesine (rol) tek seferlik geçişte EKLENECEK izinler (sıralı). '*' → [].
    Yalnız rolün ZATEN izni olduğu alandaki (çapa) izinler eklenir; hassas izinler asla (denetim #3)."""
    have = set(perms or [])
    if "*" in have:
        return []
    return sorted(p for p, anchors in PERMISSION_UPGRADE_GRANTS.items()
                  if p not in have and p not in SENSITIVE_NO_AUTO_GRANT and have.intersection(anchors))


def upgrade_grants_for_existing_role(perms) -> list:
    """Tek seferlik geçişte MEVCUT (DB'deki) role eklenecek izinler. IAM-02 öncesi bu uçlar en az bir izni
    olan HER personele açıktı; bugün çalışan kimse ekran kaybetmesin diye en az bir izni olan her role
    yeni zorunlu izinlerin TAMAMI eklenir (bugünkü erişim birebir korunur, yükseltme yok: önceden zorunlu
    izinler eklenmez). Daraltma süper adminin rol ekranındaki kararıdır ve geçiş bir daha çalışmadığı için
    kalıcıdır. Boş rol (bugün de erişimi yok) ve '*' → []."""
    # Denetim #3: eskiden "en az bir izni olan HER role tüm yeni izinler" ekleniyordu (düşük yetkili
    # rollere pazaryeri iptal/iade gibi yetkiler geçiyordu). Artık yalnız alan-çapalı ve hassas olmayan
    # izinler eklenir; hassas izinler süper adminin açık kararıdır.
    return upgrade_grants_for(perms)


# Sistemle gelen roller de (ilk kez oluşturulurken) geçirilmiş hâlleriyle yazılır — geçişten SONRA
# eklenen varsayılan rol eski (eksik) izinlerle kalmasın.
# Denetim #3: hassas izinler sistem rollerine yalnız bu AÇIK matrisle verilir (görev tanımına uyan).
DEFAULT_ROLE_SENSITIVE_ALLOW = {
    "operations": ("customers.message", "products.import_xlsx", "products.import_technical",
                   "products.marketplace_sync", "integrations.xml_export"),
    "customer_service": ("customers.message",),
}
for _r in DEFAULT_ROLES:
    _extra = [p for p in DEFAULT_ROLE_SENSITIVE_ALLOW.get(_r.get("id"), ()) if p not in _r["permissions"]]
    _r["permissions"] = list(_r["permissions"]) + upgrade_grants_for(_r["permissions"]) + _extra
del _r
