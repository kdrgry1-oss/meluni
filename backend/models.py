from pydantic import BaseModel, Field, ConfigDict
from typing import List, Optional, Dict, Any
from datetime import datetime, timezone
import uuid

# Helper function
def generate_id():
    return str(uuid.uuid4())

def utc_now():
    return datetime.now(timezone.utc)

# User Models
class UserBase(BaseModel):
    email: str
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    phone: Optional[str] = None
    is_admin: bool = False

class UserCreate(UserBase):
    password: str

class User(UserBase):
    model_config = ConfigDict(extra="ignore")
    id: str = Field(default_factory=generate_id)
    password_hash: str = ""
    created_at: datetime = Field(default_factory=utc_now)
    is_active: bool = True

# Category Models
class CategoryBase(BaseModel):
    name: str
    slug: str
    description: Optional[str] = None
    parent_id: Optional[str] = None
    image_url: Optional[str] = None
    is_active: bool = True
    sort_order: int = 0
    trendyol_category_id: Optional[int] = None
    trendyol_attributes: Dict[str, Any] = Field(default_factory=dict)

class CategoryCreate(CategoryBase):
    pass

class Category(CategoryBase):
    model_config = ConfigDict(extra="ignore")
    id: str = Field(default_factory=generate_id)
    created_at: datetime = Field(default_factory=utc_now)

# Product Variant
class ProductVariant(BaseModel):
    id: str = Field(default_factory=generate_id)
    size: Optional[str] = None
    color: Optional[str] = None
    barcode: Optional[str] = None
    sku: Optional[str] = None
    stock: int = 0
    price_adjustment: float = 0
    urun_id: Optional[str] = None  # Ticimax variant ID (URUNID)
    stock_code: Optional[str] = None  # variant stok kodu (varyantın kendi STOKKODU)
    model_config = ConfigDict(extra="allow")

# Product Models - Extended with Ticimax fields
class ProductBase(BaseModel):
    name: str
    slug: str
    description: Optional[str] = None
    short_description: Optional[str] = None
    price: float
    sale_price: Optional[float] = None
    category_id: Optional[str] = None
    category_name: Optional[str] = None
    brand: str = "FACETTE"
    images: List[str] = []
    variants: List[ProductVariant] = []
    is_active: bool = True
    is_featured: bool = False
    is_new: bool = False
    stock: int = 0
    sku: Optional[str] = None
    barcode: Optional[str] = None
    weight: float = 0
    meta_title: Optional[str] = None
    meta_description: Optional[str] = None
    technical_details: Dict[str, str] = {}
    # Ticimax 113-sütun export'unun ham (düzenlenebilir) tüm alanları.
    # Anahtarlar orijinal Ticimax kolon adlarıdır (bkz. product_schema.py).
    ticimax_fields: Dict[str, Any] = {}
    size_chart_images: List[str] = []
    combo_product_ids: List[str] = []
    similar_product_ids: List[str] = []
    # Ticimax Excel fields
    urun_karti_id: Optional[str] = None
    urun_id: Optional[str] = None
    stock_code: Optional[str] = None  # STOKKODU
    variation_code: Optional[str] = None  # VARYASYONKODU
    gtip_code: Optional[str] = None  # GTIPKODU
    unit: Optional[str] = None  # SATISBIRIMI
    keywords: Optional[str] = None  # ANAHTARKELIME
    adwords_description: Optional[str] = None
    adwords_category: Optional[str] = None
    breadcrumb_category: Optional[str] = None
    custom_field_1: Optional[str] = None
    custom_field_2: Optional[str] = None
    custom_field_3: Optional[str] = None
    # Trendyol Markup fields
    use_default_markup: bool = True
    markup_rate: float = 0  # Percentage multiplier (e.g. 20 for +20%)
    custom_field_4: Optional[str] = None
    custom_field_5: Optional[str] = None
    supplier: Optional[str] = None  # TEDARIKCI
    max_installment: Optional[int] = None  # MAKSTAKSITSAYISI
    is_showcase: bool = False  # VITRIN
    is_opportunity: bool = False  # FIRSATURUNU
    is_free_shipping: bool = False  # UCRETSIZKARGO
    consignment_stock: int = 0  # KONSINYESTOKADEDI
    purchase_price: Optional[float] = None  # ALISFIYATI
    market_price: Optional[float] = None  # PIYASAFIYATI
    member_price_1: Optional[float] = None  # UYETIPIFIYAT1 (üye tipi 1 fiyatı)
    vat_rate: float = 20  # KDVORANI
    vat_included: bool = True  # KDVDAHIL
    currency: str = "TRY"  # PARABIRIMI
    cargo_weight: Optional[float] = None  # KARGOAGIRLIGI
    product_weight: Optional[float] = None  # URUNAGIRLIGI
    width: Optional[float] = None  # URUNGENISLIK
    depth: Optional[float] = None  # URUNDERINLIK
    height: Optional[float] = None  # URUNYUKSEKLIK
    min_order_qty: int = 1  # UYEALIMMIN
    max_order_qty: Optional[int] = None  # UYEALIMMAKS
    estimated_delivery: Optional[str] = None  # TAHMINITESLIMSURESI
    marketplace_active: bool = False  # MARKETPLACEAKTIF
    publish_date: Optional[datetime] = None  # YAYINTARIHI
    trendyol_product_id: Optional[str] = None
    trendyol_status: Optional[str] = None

class ProductCreate(ProductBase):
    pass

class Product(ProductBase):
    model_config = ConfigDict(extra="ignore")
    id: str = Field(default_factory=generate_id)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    view_count: int = 0

# Cart Item
class CartItem(BaseModel):
    product_id: str
    variant_id: Optional[str] = None
    quantity: int = 1
    price: float
    name: str
    image: Optional[str] = None
    size: Optional[str] = None
    color: Optional[str] = None

# Order Models
class OrderAddress(BaseModel):
    first_name: str
    last_name: str
    phone: str
    email: str
    address: str
    city: str
    district: str
    postal_code: Optional[str] = None

class OrderBase(BaseModel):
    user_id: Optional[str] = None
    items: List[CartItem]
    shipping_address: OrderAddress
    billing_address: Optional[OrderAddress] = None
    subtotal: float
    shipping_cost: float = 0
    discount: float = 0
    total: float
    payment_method: str  # credit_card, bank_transfer, cash_on_delivery
    notes: Optional[str] = None
    platform: str = "facette"  # facette, trendyol, etc.

class OrderCreate(OrderBase):
    pass

class Order(OrderBase):
    model_config = ConfigDict(extra="ignore")
    id: str = Field(default_factory=generate_id)
    # O14: Saniye çözünürlüklü timestamp aynı saniyede iki siparişte ÇAKIŞIYORDU (order_number
    # idempotency/upsert anahtarıdır → iki müşteri çapraz bağlanabilir). Kısa rastgele sonek eklendi.
    order_number: str = Field(default_factory=lambda: f"FC{int(datetime.now().timestamp())}{uuid.uuid4().hex[:5].upper()}")
    status: str = "pending"  # pending, confirmed, preparing, shipped, delivered, cancelled
    payment_status: str = "pending"  # pending, paid, failed, refunded
    cargo_tracking: Optional[str] = None
    cargo_company: Optional[str] = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

# Banner/Slider Models
class BannerBase(BaseModel):
    title: Optional[str] = None
    subtitle: Optional[str] = None
    image_url: str
    video_url: Optional[str] = None
    link_url: Optional[str] = None
    position: str  # hero_slider, single_banner, double_banner, instashop
    sort_order: int = 0
    is_active: bool = True
    device: str = "all"  # all, mobile, desktop

class BannerCreate(BannerBase):
    pass

class Banner(BannerBase):
    model_config = ConfigDict(extra="ignore")
    id: str = Field(default_factory=generate_id)
    created_at: datetime = Field(default_factory=utc_now)

# Homepage Block
class HomepageBlock(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str = Field(default_factory=generate_id)
    type: str  # rotating_text, slider, single_banner, double_banner, product_slider, instashop
    title: Optional[str] = None
    content: Dict[str, Any] = {}
    sort_order: int = 0
    is_active: bool = True
    device: str = "all"

# Menu Item
class MenuItem(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str = Field(default_factory=generate_id)
    name: str
    url: str
    parent_id: Optional[str] = None
    image_url: Optional[str] = None
    sort_order: int = 0
    is_active: bool = True

# Settings
class SiteSettings(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str = "main"
    site_name: str = "FACETTE"
    logo_url: str = ""
    favicon_url: str = ""
    free_shipping_limit: float = 500
    rotating_texts: List[str] = ["Yeni Sezon Ürünleri Keşfet", "500 TL Üzeri Ücretsiz Kargo", "Güvenli Alışveriş"]
    contact_email: str = ""
    contact_phone: str = ""
    address: str = ""
    social_links: Dict[str, str] = {}
    payment_methods: Dict[str, bool] = {"credit_card": True, "bank_transfer": True, "cash_on_delivery": True}
    barcode_range_start: str = ""
    barcode_range_end: str = ""

# Campaign
class Campaign(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str = Field(default_factory=generate_id)
    name: str
    type: str  # percentage, fixed, free_shipping, gift
    value: float = 0
    min_order_amount: float = 0
    code: Optional[str] = None
    start_date: datetime
    end_date: datetime
    is_active: bool = True
    usage_limit: int = 0
    used_count: int = 0

# Static Page
class StaticPage(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str = Field(default_factory=generate_id)
    title: str
    slug: str
    content: str
    meta_title: Optional[str] = None
    meta_description: Optional[str] = None
    is_active: bool = True
    created_at: datetime = Field(default_factory=utc_now)

# Integration Settings
class TrendyolSettings(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str = "trendyol"
    supplier_id: str = ""
    api_key: str = ""
    api_secret: str = ""
    is_active: bool = False
    updated_at: datetime = Field(default_factory=utc_now)

# Variant Option Models
class VariantOptionBase(BaseModel):
    type: str  # e.g., 'size', 'color'
    value: str # e.g., 'S', 'M', 'L'
    sort_order: int = 0
    is_active: bool = True

class VariantOptionCreate(VariantOptionBase):
    pass

class VariantOption(VariantOptionBase):
    model_config = ConfigDict(extra="ignore")
    id: str = Field(default_factory=generate_id)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


# =============================================================================
# Influencer CRM Models (Modül 3)
# =============================================================================

DEFAULT_CAMPAIGN_DIRECTIVES = (
    "İçerik Standartları:\n"
    "• Format: 9:16 dikey video (Reels/TikTok/Shorts)\n"
    "• Marka mention: @facette etiketi zorunlu\n"
    "• Min. süre: 15 saniye, ürün net görünmeli\n"
    "• Story'de takip linki (swipe-up) + indirim kuponu paylaşılmalı\n"
    "• Paylaşım sonrası içerik linki sisteme girilmeli"
)


class InfluencerBase(BaseModel):
    name: str
    platform: str = "instagram"            # instagram | tiktok | youtube | x (birincil)
    handle: Optional[str] = None           # @kullaniciadi (geriye uyum — birincil hesap)
    instagram: Optional[str] = None        # Instagram hesabı (@...) — ayrı alan
    tiktok: Optional[str] = None           # TikTok hesabı (@...) — ayrı alan
    birthday: Optional[str] = None         # doğum günü (YYYY-MM-DD)
    phone: Optional[str] = None
    email: Optional[str] = None
    follower_count: int = 0
    coupon_code: Optional[str] = None      # influencer'a özel kupon (fallback attribution)
    aff_id: Optional[str] = None           # takip linki parametresi (aff_id=...)
    commission_rate: float = 0.0           # % komisyon
    # Kargo adresi (seeding gönderileri için)
    shipping_address: Optional[Dict[str, Any]] = None  # {full_name, phone, il, ilce, adres}
    notes: Optional[str] = None
    is_active: bool = True


class Influencer(InfluencerBase):
    model_config = ConfigDict(extra="allow")
    id: str = Field(default_factory=generate_id)
    total_revenue: float = 0.0
    total_orders: int = 0
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class InfluencerCampaignBase(BaseModel):
    influencer_id: str
    title: str
    fee_paid: float = 0.0                   # ödenen ücret
    product_cost: float = 0.0               # gönderilen ürün maliyeti
    cargo_cost: float = 0.0                  # kargo maliyeti
    sent_products: List[Dict[str, Any]] = Field(default_factory=list)  # [{name, barcode, qty}]
    directives: str = DEFAULT_CAMPAIGN_DIRECTIVES
    status: str = "draft"                    # draft | shipped | shared | completed | cancelled
    cargo_status: str = "pending"            # pending | created | shipped | delivered
    cargo_barcode: Optional[str] = None
    cargo_tracking_no: Optional[str] = None
    sent_at: Optional[str] = None            # ürünler ne zaman gönderildi (kargoya verildi)
    shared: bool = False
    shared_at: Optional[str] = None
    content_url: Optional[str] = None
    notes: Optional[str] = None


class InfluencerCampaign(InfluencerCampaignBase):
    model_config = ConfigDict(extra="allow")
    id: str = Field(default_factory=generate_id)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
