/**
 * =============================================================================
 * Products.jsx — Admin Ürün Listesi & Düzenleme Sayfası
 * =============================================================================
 *
 * NE İŞE YARAR?
 *   Facette admin panelinde ürünlerin listelenmesi, filtrelenmesi, oluşturulması,
 *   düzenlenmesi, çoğaltılması, silinmesi ve pazaryerlerine (Trendyol) aktarılması
 *   için kullanılan ana ekran. Ürüne bağlı varyantlar, özellikler (attributes),
 *   görseller, ölçü tablosu ve SEO alanları da bu ekrandaki modal üzerinden
 *   yönetilir.
 *
 * BAĞLANTILI BACKEND UÇLARI:
 *   - GET  /api/products                → Listeleme (search, filters, page)
 *   - POST /api/products                → Yeni ürün
 *   - PUT  /api/products/{id}           → Güncelleme
 *   - DELETE /api/products/{id}         → Silme
 *   - POST /api/products/{id}/duplicate → Kopyalama
 *   - GET  /api/categories              → Kategori listesi (filtre + form)
 *   - GET  /api/attributes              → Varyant/özellik kütüphanesi
 *   - GET  /api/size-tables/{product_id}→ Ölçü tablosu
 *   - POST /api/trendyol/push-product   → Trendyol'a gönderim
 *
 * BAĞLANTILI DİĞER DOSYALAR:
 *   - SizeTablePanel.jsx  → Ürün modalında "Ölçü Tablosu" sekmesinde gömülü açılır.
 *   - SearchableAttribute (aşağıda) → Zorunlu/opsiyonel Trendyol özelliklerini
 *                                      arayarak seçmeyi sağlayan küçük bileşen.
 *   - components/admin/Pagination.jsx  → Üst (compact) ve alt (full) sayfalama.
 *
 * PERFORMANS NOTU:
 *   Bu dosya büyüktür (~2300 satır). İleride modalın sekmelerinin ayrı
 *   componentlere bölünmesi planlanıyor (areas_that_need_refactoring).
 * =============================================================================
 */
import React, { useState, useEffect, useRef } from "react";
import { useSearchParams, useParams, useNavigate } from "react-router-dom";
import { Plus, Search, Edit, Trash2, Eye, EyeOff, Copy, Upload, Image, X, MoreHorizontal, Layers, Filter, ChevronDown, ChevronUp, ChevronLeft, ChevronRight, Store, RefreshCw, Check, Globe, Download, FileSpreadsheet, CheckSquare, Square, Printer, Tag, AlertTriangle } from "lucide-react";
import axios from "axios";
import { toast } from "sonner";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "../../components/ui/dialog";
import {
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
} from "../../components/ui/tabs";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "../../components/ui/dropdown-menu";
import SizeTablePanel from "./SizeTablePanel";
import Pagination from "../../components/admin/Pagination";
import SearchableAttribute from "../../components/admin/product-form/SearchableAttribute";
import SearchableMapSelect from "../../components/admin/SearchableMapSelect";
import SeoTab from "../../components/admin/product-form/SeoTab";
import CombineProductsTab from "../../components/admin/product-form/CombineProductsTab";
import ProductDetailFields from "../../components/admin/product-form/ProductDetailFields";
import ProductFilters from "../../components/admin/ProductFilters";

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;
// API.replace('/api','') HATALIYDI: "https://api.facette.com.tr/api" icinde ilk "/api"
// "https://api"deki //api'dir → "https:/.facette.com.tr/api" (bozuk). Origin'i dogru turet:
// REACT_APP_BACKEND_URL zaten /api'siz taban; yine de sondaki /api'yi guvenle ayikla.
const BACKEND_ORIGIN = String(process.env.REACT_APP_BACKEND_URL || "").replace(/\/+$/, "").replace(/\/api$/, "");
// Gorsel URL normalizasyonu: relatif yollari mutlaklastir + gecmiste kaydedilmis bozuk
// ("https:/.host/api/api/upload/..") URL'leri onar. R2 CDN (https://...) URL'leri aynen gecer.
const fixImg = (u) => {
  if (!u || typeof u !== "string") return u;
  const i = u.indexOf("/upload/files/");
  if (i >= 0 && (u.startsWith("https:/.") || u.startsWith("http:/.") || u.includes("/api/api/"))) {
    return `${BACKEND_ORIGIN}/api${u.slice(i)}`;
  }
  if (u.startsWith("/api/upload") || u.startsWith("/api/files")) return `${BACKEND_ORIGIN}${u}`;
  if (u.startsWith("/upload") || u.startsWith("/files")) return `${BACKEND_ORIGIN}/api${u}`;
  return u;
};

// SearchableAttribute, SeoTab, StockTab artık ayrı dosyalarda:
//   /app/frontend/src/components/admin/product-form/*
// (Products.jsx'i kısaltma refactor'unun 1. adımı).

/**
 * DescriptionEditor — Açıklama alanı için Kaynak / Önizleme toggle'lı editör.
 * - "Kaynak"  : HTML kaynak kodunu textarea içinde düzenle.
 * - "Önizleme": HTML'i canlı olarak render et (read-only).
 * - "Bölünmüş": iki yan yana panel (kaynak + canlı).
 * Trendyol'a aktarımda HTML temizleme backend tarafında yapılır.
 */
function DescriptionEditor({ value, onChange, onGenerate, generating }) {
  const [mode, setMode] = useState("split"); // "source" | "preview" | "split"
  // Önizleme artık DÜZENLENEBİLİR (contentEditable/WYSIWYG). İmleç sıçramasını önlemek için
  // innerHTML'i yalnızca DIŞ değişikliklerde (kaynak textarea düzenlemesi / mod değişimi) yaz;
  // kullanıcı önizlemede yazarken innerHTML === value olduğundan reset edilmez → imleç korunur.
  const previewRef = useRef(null);
  useEffect(() => {
    const el = previewRef.current;
    if (el && el.innerHTML !== (value || "")) {
      el.innerHTML = value || "";
    }
  }, [value, mode]);
  const tabBtn = (m, label) => (
    <button
      type="button"
      onClick={() => setMode(m)}
      data-testid={`desc-mode-${m}`}
      className={`px-3 py-1.5 text-[11px] font-bold uppercase tracking-wider rounded-md transition-colors ${
        mode === m ? "bg-black text-white" : "bg-gray-100 text-gray-600 hover:bg-gray-200"
      }`}
    >
      {label}
    </button>
  );
  const stripHtml = (html) => {
    if (!html) return "";
    const t = document.createElement("div");
    t.innerHTML = html;
    return (t.textContent || t.innerText || "").trim();
  };
  const charsHtml = (value || "").length;
  const charsPlain = stripHtml(value || "").length;
  return (
    <div className="border border-gray-200 rounded-lg overflow-hidden">
      <div className="flex items-center justify-between gap-2 px-3 py-2 bg-gray-50 border-b border-gray-200">
        <div className="flex gap-1 items-center">
          {tabBtn("source", "Kaynak")}
          {tabBtn("preview", "Önizleme")}
          {tabBtn("split", "Bölünmüş")}
          {onGenerate && (
            <button
              type="button"
              onClick={onGenerate}
              disabled={generating}
              data-testid="desc-ai-generate"
              title="Ürün adı, kategori ve özelliklerden yapay zekâ ile açıklama üretir"
              className="ml-2 px-3 py-1.5 text-[11px] font-bold uppercase tracking-wider rounded-md transition-colors bg-violet-600 text-white hover:bg-violet-700 disabled:opacity-50 disabled:cursor-not-allowed inline-flex items-center gap-1.5"
            >
              {generating ? "Üretiliyor…" : "✦ AI ile Oluştur"}
            </button>
          )}
        </div>
        <div className="text-[10px] text-gray-500 font-mono">
          HTML: {charsHtml} kr · Metin: {charsPlain} kr
        </div>
      </div>
      <div className={mode === "split" ? "grid grid-cols-2 gap-0" : ""}>
        {(mode === "source" || mode === "split") && (
          <textarea
            value={value || ""}
            onChange={(e) => onChange(e.target.value)}
            rows={10}
            data-testid="desc-source-textarea"
            className={`w-full px-3 py-2.5 outline-none transition-all text-xs font-mono leading-relaxed ${
              mode === "split" ? "border-r border-gray-200" : ""
            }`}
            placeholder="Ürün açıklaması (HTML destekli). Örn: <p>Pamuklu kumaş…</p>"
          />
        )}
        {(mode === "preview" || mode === "split") && (
          <div
            ref={previewRef}
            data-testid="desc-preview"
            contentEditable
            suppressContentEditableWarning
            onInput={(e) => onChange(e.currentTarget.innerHTML)}
            data-ph="Önizlemede doğrudan yazıp düzenleyebilirsiniz…"
            className="fct-rte w-full px-3 py-2.5 text-sm prose prose-sm max-w-none bg-white min-h-[252px] max-h-[440px] overflow-y-auto outline-none focus:ring-2 focus:ring-violet-200"
          />
        )}
      </div>
      <div className="px-3 py-1.5 bg-amber-50 border-t border-amber-200 text-[10px] text-amber-800">
        <strong>İpucu:</strong> Önizleme alanı da düzenlenebilir — imleci tıklayıp doğrudan yazabilirsiniz
        (değişiklik kaynağa da işlenir). <strong>Not:</strong> Trendyol'a aktarımda HTML etiketleri
        otomatik temizlenip düz metin (paragraflar ve satır sonları korunarak) gönderilir.
      </div>
    </div>
  );
}



const PRODUCTS_VIEW_KEY = "facette_products_view";
const _loadProductsView = () => { try { return JSON.parse(localStorage.getItem(PRODUCTS_VIEW_KEY) || "{}") || {}; } catch (e) { return {}; } };

export default function AdminProducts() {
  const [searchParams, setSearchParams] = useSearchParams();
  const [products, setProducts] = useState([]);
  const [categories, setCategories] = useState([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState("");                 // arama sayfa yenilenince sıfırlanır (localStorage'dan geri YÜKLENMEZ)
  const [debouncedSearch, setDebouncedSearch] = useState(""); // kullanıcı yazmayı bırakınca tek istek atılır
  const _prodReqSeq = useRef(0);                              // yarış koruması: yalnız EN SON isteğin yanıtı uygulanır
  const [page, setPage] = useState(() => _loadProductsView().page || 1);
  const [pageSize, setPageSize] = useState(() => _loadProductsView().pageSize || 20);
  const [total, setTotal] = useState(0);
  const [modalOpen, setModalOpen] = useState(false);
  const [barcodePushOpen, setBarcodePushOpen] = useState(false);
  const [barcodePushText, setBarcodePushText] = useState("");
  const [barcodePushLoading, setBarcodePushLoading] = useState(false);
  const [validationBlock, setValidationBlock] = useState(null);
  const [vbSelections, setVbSelections] = useState({}); // {`${prodId}|${attrId}|${localVal}`: value_id|"__remove__"|""}
  const [vbSaving, setVbSaving] = useState(false);
  // URL'den ürün ID'si — `/admin/urunler/{productId}` ile gelen direct link
  const { productId: urlProductId } = useParams();
  const navigate = useNavigate();
  // ---------------------------------------------------------------------------
  // Toplu Seçim State'i: siparişler tablosundaki gibi soldaki tiklerle seçilen
  // ürünlerin id listesi. "Seçilenlerin barkod kartını yazdır" ve gelecekte
  // "toplu durum değişikliği / toplu Trendyol push" için kullanılır.
  // ---------------------------------------------------------------------------
  const [selectedProducts, setSelectedProducts] = useState([]);
  const [barcodeSizeModal, setBarcodeSizeModal] = useState(null); // barkod yazdırma beden seçimi
  const toggleSelectProduct = (id) =>
    setSelectedProducts((prev) =>
      prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]
    );
  const toggleSelectAllProducts = () => {
    if (selectedProducts.length === products.length) setSelectedProducts([]);
    else setSelectedProducts(products.map((p) => p.id));
  };
  const [editingProduct, setEditingProduct] = useState(null);
  // Listedeki fiyat hücresinden hızlı düzenleme: {id, price, sale_price}
  const [priceEdit, setPriceEdit] = useState(null);
  const savePriceEdit = async () => {
    if (!priceEdit) return;
    const price = parseFloat(priceEdit.price) || 0;
    const sale_price = priceEdit.sale_price === "" || priceEdit.sale_price === null
      ? null : (parseFloat(priceEdit.sale_price) || null);
    if (price <= 0) { toast.error("Satış fiyatı 0'dan büyük olmalı"); return; }
    if (sale_price && sale_price >= price) { toast.error("İndirimli fiyat, satış fiyatından küçük olmalı"); return; }
    try {
      const token = localStorage.getItem("token");
      await axios.put(`${API}/products/${priceEdit.id}`, { price, sale_price }, { headers: { Authorization: `Bearer ${token}` } });
      setProducts((prev) => prev.map((p) => (p.id === priceEdit.id ? { ...p, price, sale_price } : p)));
      setPriceEdit(null);
      toast.success("Fiyat güncellendi");
    } catch {
      toast.error("Fiyat güncellenemedi");
    }
  };
  // technicalDetails: XML/Ticimax description'dan parse edilen teknik özellikler.
  // Shape: { kumas: {label, value}, kalip: {label, value}, ... } VEYA boş obj
  const [technicalDetails, setTechnicalDetails] = useState({});
  const [uploading, setUploading] = useState(false);
  const [draggedImgIdx, setDraggedImgIdx] = useState(null);
  const [dragOverImgIdx, setDragOverImgIdx] = useState(null);
  const [variantsModalOpen, setVariantsModalOpen] = useState(false);
  const [selectedProductForVariants, setSelectedProductForVariants] = useState(null);
  const [globalTrendyolMarkup, setGlobalTrendyolMarkup] = useState(0);
  const [globalVatRate, setGlobalVatRate] = useState(10);
  const [activeTab, setActiveTab ] = useState("basic");
  const [attributeSearchTerm, setAttributeSearchTerm] = useState("");
  const [showAllAttributes, setShowAllAttributes] = useState(false);
  const [variantSearchTerm, setVariantSearchTerm] = useState("");
  const [categorySearchOpen, setCategorySearchOpen] = useState(false);
  const [categorySearchTerm, setCategorySearchTerm] = useState("");
  const [sizeSearchOpen, setSizeSearchOpen] = useState(false);
  const [sizeSearchTerm, setSizeSearchTerm] = useState("");
  const [colorSearchOpen, setColorSearchOpen] = useState(false);
  const [colorSearchTerm, setColorSearchTerm] = useState("");
  const fileInputRef = useRef(null);
  const imageInputRef = useRef(null);   // Görsel yükleme inputu — Excel import ref'iyle ÇAKIŞMASIN
  const techFileInputRef = useRef(null);
  const [exporting, setExporting] = useState(false);
  const [importing, setImporting] = useState(false);
  const [techImportModalOpen, setTechImportModalOpen] = useState(false);
  const [techImportResults, setTechImportResults] = useState(null);
  const [techImporting, setTechImporting] = useState(false);
  const [toolsMenuOpen, setToolsMenuOpen] = useState(false);
  const toolsMenuRef = useRef(null);
  const [techApplying, setTechApplying] = useState(false);

  const [filters, setFilters] = useState(() => _loadProductsView().appliedFilters || {
    // Durum & kategori
    status: "all",
    category_id: "",
    // Kimlik & metin
    urun_karti_id: "",
    varyasyon_id: "",
    name: "",
    stock_code: "",
    gtip: "",
    barcode: "",
    breadcrumb: "",
    brand: "",
    supplier: "",
    tag: "",
    ozel1: "", ozel2: "", ozel3: "", ozel4: "", ozel5: "",
    seo_title: "", seo_keywords: "", seo_desc: "",
    // Aralıklar
    min_stock: "", max_stock: "",
    min_price: "", max_price: "",
    min_indirimli: "", max_indirimli: "",
    min_alis: "", max_alis: "",
    min_piyasa: "", max_piyasa: "",
    // Tarih
    date_from: "", date_to: "",
    pub_date_from: "", pub_date_to: "",
    // Dropdown / bayraklar (Seçiniz="" | Evet="1" | Hayır="0")
    kdv_dahil: "",
    para_birimi: "",
    kart_aktif: "",
    is_showcase: "",
    is_opportunity: "",
    is_new: "",
    is_free_shipping: "",
    has_image: "",
    has_variants: "",
    multi_barcode: "",
    has_video: "",
    discounted: "",
    sureli_indirim: "",
    yemek_karti: "",
    teslim_goster: "",
    ayni_gun: "",
    entegrasyon: "",
    mp1: "", mp2: "", mp3: "", mp4: "", mp5: "",
    // Teknik detay
    attr_key: "",
    attr_value: "",
  });
  const FILTERS_INITIAL = {
    status: "all", category_id: "", urun_karti_id: "", varyasyon_id: "", name: "",
    stock_code: "", gtip: "", barcode: "", breadcrumb: "", brand: "", supplier: "",
    tag: "", ozel1: "", ozel2: "", ozel3: "", ozel4: "", ozel5: "",
    seo_title: "", seo_keywords: "", seo_desc: "",
    min_stock: "", max_stock: "", min_price: "", max_price: "",
    min_indirimli: "", max_indirimli: "", min_alis: "", max_alis: "",
    min_piyasa: "", max_piyasa: "", date_from: "", date_to: "",
    pub_date_from: "", pub_date_to: "", kdv_dahil: "", para_birimi: "",
    kart_aktif: "", is_showcase: "", is_opportunity: "", is_new: "",
    is_free_shipping: "", has_image: "", has_variants: "", multi_barcode: "",
    has_video: "", discounted: "", sureli_indirim: "", yemek_karti: "",
    teslim_goster: "", ayni_gun: "", entegrasyon: "",
    mp1: "", mp2: "", mp3: "", mp4: "", mp5: "", attr_key: "", attr_value: "",
  };
  const [filterOptions, setFilterOptions] = useState({ brands: [], suppliers: [], currencies: [], attribute_groups: [] });
  // Uygulanan filtreler: liste yalnızca "Listele" / Enter ile bu state'e göre yenilenir.
  const [appliedFilters, setAppliedFilters] = useState(() => _loadProductsView().appliedFilters || FILTERS_INITIAL);
  // Taslak güncelleme: sadece input state'ini değiştirir, listeyi HEMEN yenilemez.
  const updateFilter = (key, value) => { setFilters((f) => ({ ...f, [key]: value })); };
  // Listele: taslak filtreleri uygula ve ilk sayfadan getir.
  const applyFilters = () => { setAppliedFilters({ ...filters }); setPage(1); setShowFilters(false); };
  const clearFilters = () => { setFilters(FILTERS_INITIAL); setAppliedFilters(FILTERS_INITIAL); setPage(1); };
  const [showFilters, setShowFilters] = useState(false);
  // Tablo sıralaması (3 durumlu: yön -> ters -> varsayılan)
  const [sortBy, setSortBy] = useState(() => _loadProductsView().sortBy || { field: null, dir: null });

  const [aiDescLoading, setAiDescLoading] = useState(false);

  const [formData, setFormData] = useState({
    name: "", slug: "", description: "", short_description: "",
    price: 0, sale_price: null, category_name: "", categories: [], brand: "FACETTE",
    images: [], is_active: false, is_featured: false, is_new: false,
    stock: 0, stock_code: "", season: "", barcode: "", sku: "",
    urun_karti_id: "", urun_id: "",
    // Ticimax fields
    variation_code: "", gtip_code: "", unit: "ADET", keywords: "",
    supplier: "", manufacturer: "FACETTE", max_installment: 9, purchase_price: 0, member_price_1: null,
    // FAZ 7 — İmalat modülü entegrasyonu için ek alanlar
    collection: "", color: "",
    vat_rate: 10,
    market_price: 0, vat_included: true, currency: "TRY",
    cargo_weight: 0, product_weight: 0, width: 0, depth: 0, height: 0,
    min_order_qty: 1, max_order_qty: 999, estimated_delivery: "2-3",
    is_free_shipping: false, is_showcase: false,
    meta_title: "", meta_description: "", meta_keywords: "",
    variants: [], newVariant: {},
    attributes: {},
    auto_barcode: false,
    trendyol_attributes: {},
    trendyol_category_id: "",
    trendyol_multiplier: 0,
    hepsiburada_category_id: "",
    hepsiburada_category_name: "",
    temu_category_id: "",
    temu_category_name: "",
    use_default_markup: true,
    markup_rate: 0,
    hepsiburada_attributes: {},
    temu_attributes: {},
    combine_products: [],
    ticimax_fields: {}
  });
  const [ticimaxSchema, setTicimaxSchema] = useState([]);

  const [trendyolAttributesList, setTrendyolAttributesList] = useState([]);
  const [hepsiburadaAttributesList, setHepsiburadaAttributesList] = useState([]);
  const [trendyolCategories, setTrendyolCategories] = useState([]);
  const [globalAttributes, setGlobalAttributes] = useState([]);
  const [globalSizes, setGlobalSizes] = useState([]);
  const [globalColors, setGlobalColors] = useState([]);
  // #3: Satış fiyatı → Üye Tipi 1 otomatik aktarım; üye fiyatı manuel değişince bağımsız olur.
  const [memberPriceManual, setMemberPriceManual] = useState(false);
  // #6: Hızlı varyant — çoklu beden/renk seçimi (kombinasyondan kart üret).
  const [multiSizes, setMultiSizes] = useState([]);
  const [multiColors, setMultiColors] = useState([]);
  const [fetchingAttributes, setFetchingAttributes] = useState(false);
  // HB kategori şeması canlı çekilirken true; modal şema gelmeden 9 sabite düşmesin diye guard.
  const [hbAttrsLoading, setHbAttrsLoading] = useState(false);
  const [attrSearch, setAttrSearch] = useState({});

  useEffect(() => {
    if (modalOpen) {
      const selectedCat = formData.category_name ? categories.find(c => c.name === formData.category_name || c.id === formData.category_name) : null;
      const targetTrendyolCatId = formData.trendyol_category_id || (selectedCat ? selectedCat.trendyol_category_id : null);
      
      if (targetTrendyolCatId) {
        setFetchingAttributes(true);
        const token = localStorage.getItem('token');
        axios.get(`${API}/integrations/trendyol/categories/${targetTrendyolCatId}/attributes?refresh=true`, {
          headers: { Authorization: `Bearer ${token}` }
        })
          .then(res => setTrendyolAttributesList(res.data.attributes || []))
          .catch(err => setTrendyolAttributesList([]))
          .finally(() => setFetchingAttributes(false));
      } else {
        setTrendyolAttributesList([]);
      }
    }
  }, [formData.trendyol_category_id, formData.category_name, modalOpen, categories]);

  // Hepsiburada: urun editorunun HB bolumu icin HB kategori ozelliklerini canli cek.
  // HB kategori id URUNDE durmaz (category_mappings'te durur) — Trendyol gibi kategoriden cozulur.
  // 1) formData.hepsiburada_category_id varsa onunla; 2) yoksa duzenlenen urunun id'siyle
  // /products/{id}/category-attributes endpoint'inden esleme uzerinden cek → 'Zorunlu - Bos' kirmizi dolar.
  useEffect(() => {
    if (!modalOpen) return;
    const token = localStorage.getItem('token');
    const auth = { headers: { Authorization: `Bearer ${token}` } };
    const hbCatId = formData.hepsiburada_category_id;
    // Yerel kategori (id + ad) — KAYDEDİLMEMİŞ üründe HB kategorisini buradan çözeriz
    // (push çekirdeği ile aynı: category_mappings üzerinden). Böylece yeni üründe de
    // HB'nin tüm kategori özellikleri gelir, 9 sabite düşmez.
    const selCat = (categories || []).find(c =>
      c.name === formData.category_name || c.id === formData.category_name || c.id === formData.category_id);
    const locId = formData.category_id || selCat?.id || "";
    const locName = formData.category_name || selCat?.name || "";
    // Once urunun kategori eslemesinden coz (en guvenilir, mapping uzerinden);
    // bos donerse yerel kategoriden, o da bossa formData.hepsiburada_category_id ile dene.
    const byProduct = () => formData.id
      ? axios.get(`${API}/integrations/hepsiburada/products/${formData.id}/category-attributes`, auth).then(r => r.data.attributes || [])
      : Promise.resolve([]);
    const byLocalCat = () => (locId || locName)
      ? axios.get(`${API}/integrations/hepsiburada/category-attributes/by-local`,
          { ...auth, params: { category_id: locId, category_name: locName } }).then(r => r.data.attributes || [])
      : Promise.resolve([]);
    const byCat = () => hbCatId
      ? axios.get(`${API}/integrations/hepsiburada/categories/${hbCatId}/attributes`, auth).then(r => r.data.attributes || [])
      : Promise.resolve([]);
    setHbAttrsLoading(true);
    byProduct()
      .then(list => (list && list.length) ? list : byLocalCat())
      .then(list => (list && list.length) ? list : byCat())
      .then(list => setHepsiburadaAttributesList(list || []))
      .catch(() => setHepsiburadaAttributesList([]))
      .finally(() => setHbAttrsLoading(false));
  }, [formData.hepsiburada_category_id, formData.id, formData.category_id, formData.category_name, modalOpen, categories]);

  // HB OTOMATİK DOLUM (yaklaşım A): Varsayılan özellikler (genel `attributes` + Teknik Detay)
  // HB kategori şemasına normalize ad + değer eşlemesiyle yazılır. Sadece BOŞ HB alanları doldurulur;
  // manuel değer ASLA ezilmez. Temu'daki otomatik dolumun HB karşılığı.
  useEffect(() => {
    if (!modalOpen || !(hepsiburadaAttributesList || []).length) return;
    const _norm = (s) => (s || "").toLocaleLowerCase("tr").replace(/[\s\-_/().]/g, "").trim();
    const defaults = { ...(formData.attributes || {}) };
    Object.values(technicalDetails || {}).forEach(t => { if (t?.label && t?.value) defaults[t.label] = t.value; });
    const defKeys = Object.keys(defaults);
    const next = { ...(formData.hepsiburada_attributes || {}) };
    let changed = false;
    for (const hbAttr of hepsiburadaAttributesList) {
      const hbName = hbAttr.name;
      if (!hbName || next[hbName]) continue;                 // dolu → ezme
      const nb = _norm(hbName);
      const matchKey = defKeys.find(dn => {
        const na = _norm(dn);
        return na && (na === nb || na.includes(nb) || nb.includes(na));
      });
      if (!matchKey) continue;
      const dv = defaults[matchKey];
      if (dv === undefined || dv === null || dv === "") continue;
      const vals = (hbAttr.attributeValues || []).map(v => v.name).filter(Boolean);
      let hbVal = dv;
      if (vals.length) {
        const exact = vals.find(v => _norm(v) === _norm(dv));
        const partial = vals.find(v => _norm(v).includes(_norm(dv)) || _norm(dv).includes(_norm(v)));
        if (exact) hbVal = exact;
        else if (partial) hbVal = partial;
        else if (!hbAttr.allowCustom) continue;              // HB enum'da yok ve serbest değil → atla
      }
      next[hbName] = hbVal;
      changed = true;
    }
    if (changed) setFormData(p => ({ ...p, hepsiburada_attributes: next }));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hepsiburadaAttributesList, formData.attributes, technicalDetails, modalOpen]);

  useEffect(() => {
    if (modalOpen && !editingProduct) {
      axios.get(`${API}/settings`)
        .then(res => {
          if (res.data.default_vat_rate) {
            setFormData(prev => ({ ...prev, vat_rate: res.data.default_vat_rate }));
          }
        })
        .catch(console.error);
    }
  }, [modalOpen, editingProduct]);

  // Direct link: /admin/urunler/{productId} → modal'ı otomatik aç.
  useEffect(() => {
    if (!urlProductId) return;
    if (editingProduct?.id === urlProductId && modalOpen) return;
    openEditModal(urlProductId, { skipNavigate: true });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [urlProductId]);

  // Modal kapanınca URL'i temizle (direct linkten geldiyse listeye dön).
  useEffect(() => {
    if (!modalOpen && urlProductId) {
      navigate("/admin/urunler", { replace: true });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [modalOpen]);

  // Deep-link: /admin/urunler?aktar=barkod → "Barkod ile Trendyol'a Aktar" pop-up'ını otomatik aç.
  // (Pazaryeri Hub'ından hızlı erişim için.)
  useEffect(() => {
    // İMALATTAN ÜRÜN AÇ: İmalat Takip'teki "Ürün Aç" butonu Yeni Ürün formunu
    // üretim bilgileriyle (ad, stok kodu, sezon, alış fiyatı, renk×beden=stok) önden dolu açar.
    if (searchParams.get("newFromMfg") === "1") {
      const raw = sessionStorage.getItem("mfg_product_prefill");
      if (raw) {
        try {
          const p = JSON.parse(raw);
          resetForm();
          setFormData(f => ({
            ...f,
            name: p.name || "",
            stock_code: p.stock_code || "",
            season: p.season || "",
            purchase_price: Number(p.purchase_price || 0),
            manufacturer: p.manufacturer || "FACETTE",
            mfg_record_id: p.mfg_record_id || "",
            is_active: false, // satışa açmadan önce kalan bilgiler girilecek
            variants: (p.variants || []).map((v, i) => ({
              id: `var-mfg-${i}-${v.size}-${v.color}`.replace(/\s+/g, ""),
              size: v.size || "", color: v.color || "",
              stock: Number(v.stock || 0), barcode: "", stock_code: p.stock_code || "",
            })),
          }));
          setModalOpen(true);
          toast.success("Ürün kartı imalat bilgileriyle dolduruldu — kalan alanları tamamlayıp kaydedin");
        } catch { /* sessiz */ }
        sessionStorage.removeItem("mfg_product_prefill");
      }
      searchParams.delete("newFromMfg");
      setSearchParams(searchParams, { replace: true });
      return;
    }
    if (searchParams.get("aktar") === "barkod") {
      setBarcodePushOpen(true);
      const next = new URLSearchParams(searchParams);
      next.delete("aktar");
      setSearchParams(next, { replace: true });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchParams]);

  // Arama "debounce": kullanıcı yazmayı ~350ms bıraktığında TEK istek atılır.
  // Önceden her tuş vuruşu /products çağırıyordu → yarış durumu (eski yanıt yeni
  // yanıtın üstüne yazıp YANLIŞ sonuç gösterebiliyordu) ve gereksiz sunucu yükü.
  useEffect(() => {
    const _t = setTimeout(() => setDebouncedSearch(search), 350);
    return () => clearTimeout(_t);
  }, [search]);

  useEffect(() => {
    fetchProducts();
    fetchCategories();
    fetchTrendyolCategories();
    fetchGlobalTrendyolMarkup();
    fetchGlobalSettings();
  }, [page, pageSize, debouncedSearch, JSON.stringify(appliedFilters), JSON.stringify(sortBy)]);

  // Görünüm kalıcılığı: yenilemede sayfa + boyut + filtreler + sıralama korunur.
  // NOT: arama (search) BİLİNÇLİ olarak saklanmaz → sayfa yenilenince arama sıfırlanır.
  useEffect(() => {
    try { localStorage.setItem(PRODUCTS_VIEW_KEY, JSON.stringify({ page, pageSize, appliedFilters, sortBy })); } catch (e) {}
  }, [page, pageSize, JSON.stringify(appliedFilters), JSON.stringify(sortBy)]);

  // Ürün detay alan şemasını bir kez çek (sekmelere gömülü ek alanlar için)
  useEffect(() => {
    const token = localStorage.getItem('token');
    axios.get(`${API}/products/meta/ticimax-schema`, { headers: { Authorization: `Bearer ${token}` } })
      .then(res => setTicimaxSchema(res.data.groups || []))
      .catch(() => setTicimaxSchema([]));
    // Gelişmiş filtre paneli dropdown verileri (marka/tedarikçi/para birimi/teknik detay)
    axios.get(`${API}/products/meta/filter-options`, { headers: { Authorization: `Bearer ${token}` } })
      .then(res => setFilterOptions(res.data || {}))
      .catch(() => {});
  }, []);

  // Open size-table editor when deep-linked from /admin/olcu-tablolari
  useEffect(() => {
    const stId = searchParams.get("sizeTable");
    const editId = searchParams.get("edit");
    const targetId = stId || editId;
    if (!targetId || products.length === 0) return;
    const p = products.find((x) => x.id === targetId);
    if (p) {
      openEditModal(p);
      if (stId) setActiveTab("sizetable");
      // Clean URL so refresh doesn't reopen endlessly
      const next = new URLSearchParams(searchParams);
      next.delete("sizeTable");
      next.delete("edit");
      setSearchParams(next, { replace: true });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [products, searchParams]);

  // Click outside handler to close dropdowns
  useEffect(() => {
    const handleClickOutside = (event) => {
      // Close size and color dropdowns when clicking outside
      if (!event.target.closest('.size-dropdown-container')) {
        setSizeSearchOpen(false);
      }
      if (!event.target.closest('.color-dropdown-container')) {
        setColorSearchOpen(false);
      }
    };

    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  const fetchTrendyolCategories = async () => {
    try {
      const token = localStorage.getItem('token');
      const res = await axios.get(`${API}/integrations/trendyol/categories`, { headers: { Authorization: `Bearer ${token}` }});
      if (res.data?.categories) {
        setTrendyolCategories(res.data.categories);
      }
    } catch (err) {
      console.error("Trendyol categories fetch failed", err);
    }
  };

  const fetchGlobalTrendyolMarkup = async () => {
    try {
      const token = localStorage.getItem('token');
      const res = await axios.get(`${API}/integrations/trendyol/settings`, {
        headers: { Authorization: `Bearer ${token}` }
      });
      if (res.data && res.data.default_markup !== undefined) {
        setGlobalTrendyolMarkup(res.data.default_markup);
      }
    } catch (err) {
      console.error("Global markup fetch error:", err);
    }
  };

  const fetchGlobalSettings = async () => {
    try {
      const token = localStorage.getItem('token');
      const res = await axios.get(`${API}/settings`, {
        headers: { Authorization: `Bearer ${token}` }
      });
      if (res.data && res.data.default_vat_rate !== undefined) {
        setGlobalVatRate(res.data.default_vat_rate);
      }
    } catch (err) {
      console.error("Global settings fetch error:", err);
    }
  };

  // Liste + Excel için ORTAK sorgu parametreleri (arama + TÜM gelişmiş filtreler).
  // Hem fetchProducts hem handleExport kullanır → Excel ekrandaki filtrenin AYNISINI indirir.
  const buildProductParams = ({ paging = false } = {}) => {
    const p = new URLSearchParams();
    if (paging) { p.set('page', page); p.set('limit', pageSize); }
    if (debouncedSearch) p.set('search', debouncedSearch);
    if (sortBy.field) { p.set('sort', sortBy.field); p.set('order', sortBy.dir); }

    const f = appliedFilters;
    const set = (key, val) => { if (val !== "" && val !== undefined && val !== null) p.set(key, val); };

    // Doğrudan geçen parametreler (backend ile aynı ad)
    [
      'status', 'category_id', 'urun_karti_id', 'varyasyon_id', 'name', 'stock_code',
      'gtip', 'barcode', 'breadcrumb', 'brand', 'supplier', 'tag',
      'min_stock', 'max_stock', 'min_price', 'max_price',
      'date_from', 'date_to', 'pub_date_from', 'pub_date_to',
      'has_image', 'has_variants', 'has_video', 'multi_barcode', 'discounted',
      'is_free_shipping', 'is_showcase', 'is_opportunity', 'is_new',
      'attr_key', 'attr_value',
    ].forEach((k) => set(k, f[k]));

    // ticimax_fields sayısal aralıkları
    const rangeMap = {
      min_indirimli: 'tfmin_INDIRIMLIFIYAT', max_indirimli: 'tfmax_INDIRIMLIFIYAT',
      min_alis: 'tfmin_ALISFIYATI', max_alis: 'tfmax_ALISFIYATI',
      min_piyasa: 'tfmin_PIYASAFIYATI', max_piyasa: 'tfmax_PIYASAFIYATI',
    };
    Object.entries(rangeMap).forEach(([k, param]) => set(param, f[k]));

    // ticimax_fields tekil alanları (tf_)
    const tfMap = {
      kdv_dahil: 'tf_KDVDAHIL', kart_aktif: 'tf_KARTAKTIF', para_birimi: 'tf_PARABIRIMI',
      yemek_karti: 'tf_YEMEKKARTIODEMEYASAKLILISTESI',
      teslim_goster: 'tf_TAHMINITESLIMSURESIGOSTER', ayni_gun: 'tf_TAHMINITESLIMSURESIAYNIGUN',
      sureli_indirim: 'tf_SURELIINDIRIMOZELLIK', entegrasyon: 'tf_ENTEGRASYONGUNCELLEMEAKTIF',
      mp1: 'tf_MARKETPLACEAKTIF', mp2: 'tf_MARKETPLACEAKTIF2', mp3: 'tf_MARKETPLACEAKTIF3',
      mp4: 'tf_MARKETPLACEAKTIF4', mp5: 'tf_MARKETPLACEAKTIF5',
      ozel1: 'tf_OZELALAN1', ozel2: 'tf_OZELALAN2', ozel3: 'tf_OZELALAN3',
      ozel4: 'tf_OZELALAN4', ozel5: 'tf_OZELALAN5',
      seo_title: 'tf_SEO_SAYFABASLIK', seo_keywords: 'tf_SEO_ANAHTARKELIME', seo_desc: 'tf_SEO_SAYFAACIKLAMA',
    };
    Object.entries(tfMap).forEach(([k, param]) => set(param, f[k]));

    return p;
  };

  const handleExport = async () => {
    setExporting(true);
    toast.info("Excel dosyası hazırlanıyor...");
    try {
      const token = localStorage.getItem("token");
      const p = buildProductParams({ paging: false });   // ekrandaki arama + tüm filtreler
      const _qs = p.toString();
      const response = await axios.get(`${API}/products/export/excel${_qs ? `?${_qs}` : ""}`, {
        headers: { Authorization: `Bearer ${token}` },
        responseType: 'blob',
        timeout: 120000,   // büyük katalog export'u uzun sürebilir → erken kopma olmasın
      });
      
      const url = window.URL.createObjectURL(new Blob([response.data]));
      const link = document.createElement('a');
      link.href = url;
      link.setAttribute('download', `urunler_${new Date().toISOString().split('T')[0]}.xlsx`);
      document.body.appendChild(link);
      link.click();
      link.remove();
      toast.success("Excel başarıyla indirildi");
    } catch (err) {
      console.error("Export error:", err);
      toast.error("Dosya indirilemedi");
    } finally {
      setExporting(false);
    }
  };

  // Excel Yükle — SİHİRBAZ: 1) dosya yazılmadan çözümlenir 2) hangi sütun + hangi
  // kategoriler güncellenecek seçilir 3) yalnız seçilenler yazılır 4) bitiş ekranı.
  const [importWizard, setImportWizard] = useState(null); // {file, analysis, selCols:Set, selCats:Set, step, stats, applying}
  const handleImport = async (e) => {
    const file = e.target.files[0];
    if (!file) return;
    setImporting(true);
    const toastId = toast.loading("Excel çözümleniyor (henüz hiçbir şey yazılmadı)...");
    try {
      const token = localStorage.getItem("token");
      const fd = new FormData();
      fd.append("file", file);
      const r = await axios.post(`${API}/products/import/excel/analyze`, fd, {
        headers: { Authorization: `Bearer ${token}`, "Content-Type": "multipart/form-data" },
      });
      toast.success(`Dosya uygun: ${r.data.rows} satır, ${r.data.matched_products} ürün eşleşti`, { id: toastId });
      setImportWizard({
        file,
        analysis: r.data,
        selCols: new Set(r.data.updatable_columns || []),
        selCats: new Set(),   // boş = tüm kategoriler
        step: "select",
        applying: false,
        stats: null,
      });
    } catch (err) {
      toast.error(err.response?.data?.detail || "Dosya çözümlenemedi", { id: toastId });
    } finally {
      setImporting(false);
      if (fileInputRef.current) fileInputRef.current.value = "";
    }
  };
  const applyImportWizard = async () => {
    const w = importWizard;
    if (!w || !w.selCols.size) { toast.error("En az bir sütun seçin"); return; }
    setImportWizard({ ...w, applying: true });
    try {
      const token = localStorage.getItem("token");
      const fd = new FormData();
      fd.append("file", w.file);
      fd.append("columns", [...w.selCols].join(","));
      fd.append("categories", [...w.selCats].join(","));
      const r = await axios.post(`${API}/products/import/excel`, fd, {
        headers: { Authorization: `Bearer ${token}`, "Content-Type": "multipart/form-data" },
        timeout: 300000,
      });
      setImportWizard({ ...w, applying: false, step: "done", stats: r.data?.stats || {} });
      fetchProducts();
    } catch (err) {
      toast.error(err.response?.data?.detail || "Güncelleme başarısız");
      setImportWizard({ ...w, applying: false });
    }
  };

  const handleTechImport = async (e) => {
    const file = e.target.files[0];
    if (!file) return;
    setTechImporting(true);
    const toastId = toast.loading("Excel dosyası analiz ediliyor...");
    try {
      const token = localStorage.getItem("token");
      const fd = new FormData();
      fd.append("file", file);
      const response = await axios.post(`${API}/products/attributes/import-technical-xlsx`, fd, {
        headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'multipart/form-data' }
      });
      if (response.data.success) {
        setTechImportResults(response.data);
        setTechImportModalOpen(true);
        toast.success(`${response.data.matched} ürün eşleştirildi, ${response.data.unmatched} eşleşmedi`, { id: toastId });
      }
    } catch (err) {
      toast.error(err.response?.data?.detail || "Dosya analiz edilemedi", { id: toastId });
    } finally {
      setTechImporting(false);
      if (techFileInputRef.current) techFileInputRef.current.value = "";
    }
  };

  const handleApplyTechImport = async () => {
    if (!techImportResults?.results) return;
    setTechApplying(true);
    const toastId = toast.loading("Özellikler ürünlere uygulanıyor...");
    try {
      const token = localStorage.getItem("token");
      const updates = techImportResults.results
        .filter(r => r.matched_product_id)
        .map(r => ({
          product_id: r.matched_product_id,
          attributes: r.attributes,
          extra_colors: r.extra_colors
        }));
      const response = await axios.post(`${API}/products/attributes/apply-technical-xlsx`, { updates }, {
        headers: { Authorization: `Bearer ${token}` }
      });
      if (response.data.success) {
        toast.success(response.data.message, { id: toastId });
        setTechImportModalOpen(false);
        setTechImportResults(null);
        fetchProducts();
      }
    } catch (err) {
      toast.error(err.response?.data?.detail || "Uygulama başarısız", { id: toastId });
    } finally {
      setTechApplying(false);
    }
  };

  /**
   * fetchProducts — Ürünleri arka uçtan çeker.
   *
   * TETİKLEYİCİLER: useEffect([page, search, filters]) içinde otomatik çağrılır.
   *                 Arama kutusu / filtre değişimlerinde setPage(1) ile baştan
   *                 yüklenir. Pagination componentinden onChange(newPage) geldiğinde
   *                 page state'i güncellenir ve bu fonksiyon yeniden çalışır.
   *
   * BACKEND: GET /api/products (limit=20 ile sabit; Pagination componentinin
   *          pageSize={20} prop'u ile birebir eşleşir).
   *
   * DÖNÜŞ: { products, total } — `total` Pagination için toplam sayfa hesabında
   *        kullanılır; aynı değer hem üst (compact) hem alt (full) pagination'a
   *        beslenir.
   */
  // ── "Toplu İşlemler" menüsü (ürün aramanın altındaki dropdown) handlerleri ──
  // Buton onClick'leri buraya taşındı; menü öğeleri bu fonksiyonları çağırır.
  const handleOtomatikDoldur = async () => {
    if (!window.confirm("TÜM ürünlerin Trendyol/HB/Temu özelliklerini otomatik doldur?\n\nMevcut manuel girilen değerler korunur.")) return;
    const t = toast.loading("Teknik detaylar eşleniyor...");
    try {
      const token = localStorage.getItem('token');
      const res = await axios.post(
        `${API}/integrations/site/teknik-detay/sync?use_cache=true`,
        null,
        { headers: { Authorization: `Bearer ${token}` }, timeout: 120000 }
      );
      toast.dismiss(t);
      toast.success(res.data.message || "Eşleme tamamlandı");
      fetchProducts();
    } catch (e) {
      toast.dismiss(t);
      toast.error(e.response?.data?.detail || "Eşleme başarısız");
    }
  };

  const handleSilinenOzellikKurtar = async () => {
    const token = localStorage.getItem('token');
    const t = toast.loading("Önizleme hazırlanıyor...");
    try {
      const pre = await axios.post(
        `${API}/integrations/site/teknik-detay/recover?apply=false`,
        null, { headers: { Authorization: `Bearer ${token}` }, timeout: 120000 }
      );
      toast.dismiss(t);
      const d = pre.data || {};
      const ok = window.confirm(
        "SİLİNEN TEKNİK DETAY KURTARMA — ÖNİZLEME\n\n" +
        `• Eşleşen ürün: ${d.eslesen_urun}  (kart-ID: ${d.eslesen_kart_id_ile} · barkod: ${d.eslesen_barkod_ile})\n` +
        `• Genel (Trendyol) dolacak özellik: ${d.doldurulacak_ozellik_toplam}\n` +
        `• Hepsiburada'ya dolacak: ${d.hb_dolan_toplam}\n` +
        `• Temu'ya dolacak: ${d.temu_dolan_toplam}\n` +
        `• Hiç eşleşmeyen kart: ${d.eslesmeyen_urun_karti}  ·  Anormal (atlanan): ${d.anormal_atlanmis}\n\n` +
        "Eşleştirme: önce urun_karti_id, tutmazsa BARKOD (varyant-benzersiz, güvenli).\n" +
        "Genel + Hepsiburada + Temu alanlarına, yalnız BOŞ olanlara yazılır; mevcut değerler KORUNUR.\n" +
        "attributes formatına dokunulmaz (Trendyol güvende). Fiyat / KDV / stok / barkoda DOKUNULMAZ.\n\n" +
        "Uygulansın mı?"
      );
      if (!ok) { toast("İptal edildi"); return; }
      const t2 = toast.loading("Kurtarma uygulanıyor...");
      const res = await axios.post(
        `${API}/integrations/site/teknik-detay/recover?apply=true`,
        null, { headers: { Authorization: `Bearer ${token}` }, timeout: 180000 }
      );
      toast.dismiss(t2);
      toast.success(`${res.data.guncellenen_urun} üründe — Genel ${res.data.doldurulacak_ozellik_toplam} · HB ${res.data.hb_dolan_toplam} · Temu ${res.data.temu_dolan_toplam} özellik dolduruldu`);
      fetchProducts();
    } catch (e) {
      toast.dismiss(t);
      toast.error(e.response?.data?.detail || "Kurtarma başarısız");
    }
  };

  const handleEksikAciklamaKurtar = async () => {
    const token = localStorage.getItem('token');
    const t = toast.loading("Açıklama önizlemesi hazırlanıyor...");
    try {
      const pre = await axios.post(
        `${API}/integrations/site/aciklama/recover?apply=false`,
        null, { headers: { Authorization: `Bearer ${token}` }, timeout: 120000 }
      );
      toast.dismiss(t);
      const d = pre.data || {};
      const ok = window.confirm(
        "EKSİK AÇIKLAMA KURTARMA — ÖNİZLEME\n\n" +
        `• Açıklaması doldurulacak ürün: ${d.doldurulacak_urun}  (kart-ID: ${d.eslesen_kart_id_ile} · barkod: ${d.eslesen_barkod_ile})\n` +
        `• Zaten dolu (atlanan): ${d.zaten_dolu}\n` +
        `• Hiç eşleşmeyen kart: ${d.eslesmeyen_urun_karti}  ·  Anormal: ${d.anormal_atlanmis}\n\n` +
        "Eşleştirme: önce urun_karti_id, tutmazsa BARKOD (güvenli).\n" +
        "Yalnız BOŞ açıklama doldurulur, mevcut açıklama KORUNUR.\n" +
        "Fiyat / KDV / stok / başlık / özelliklere DOKUNULMAZ.\n\n" +
        "Uygulansın mı?"
      );
      if (!ok) { toast("İptal edildi"); return; }
      const t2 = toast.loading("Açıklamalar yazılıyor...");
      const res = await axios.post(
        `${API}/integrations/site/aciklama/recover?apply=true`,
        null, { headers: { Authorization: `Bearer ${token}` }, timeout: 180000 }
      );
      toast.dismiss(t2);
      toast.success(`${res.data.guncellenen_urun} ürünün açıklaması dolduruldu`);
      fetchProducts();
    } catch (e) {
      toast.dismiss(t);
      toast.error(e.response?.data?.detail || "Açıklama kurtarma başarısız");
    }
  };

  const handleRenkWebColorDoldur = async () => {
    const token = localStorage.getItem('token');
    const t = toast.loading("Renk/Web Color önizlemesi hazırlanıyor...");
    try {
      const pre = await axios.post(
        `${API}/integrations/site/renk-webcolor/autofill?apply=false`,
        null, { headers: { Authorization: `Bearer ${token}` }, timeout: 120000 }
      );
      toast.dismiss(t);
      const d = pre.data || {};
      const ok = window.confirm(
        "RENK + WEB COLOR DOLDUR — ÖNİZLEME\n\n" +
        `• Taranan ürün: ${d.taranan_urun}\n` +
        `• Rengi bulunan: ${d.renk_bulunan_urun}  ·  Bulunamayan: ${d.renk_bulunamayan}\n` +
        `• Çok renkli (atlanan): ${d.cok_renkli_atlanan}\n` +
        `• Doldurulacak — Renk: ${d.renk_doldurulacak}  ·  Web Color: ${d.webcolor_doldurulacak}\n` +
        `• HB: ${d.hb_dolan_toplam}  ·  Temu: ${d.temu_dolan_toplam}\n\n` +
        "Renk = ürün adının SON kelimesi (renk sözlüğüyle doğrulanır).\n" +
        "Web Color gönderimde pazaryeri değerine (en yakın) çözülür.\n" +
        "Çok renkli kart ATLANIR · Beden YAZILMAZ · yalnız BOŞ alanlar.\n\n" +
        "Uygulansın mı?"
      );
      if (!ok) { toast("İptal edildi"); return; }
      const t2 = toast.loading("Renk + Web Color yazılıyor...");
      const res = await axios.post(
        `${API}/integrations/site/renk-webcolor/autofill?apply=true`,
        null, { headers: { Authorization: `Bearer ${token}` }, timeout: 180000 }
      );
      toast.dismiss(t2);
      toast.success(`${res.data.guncellenen_urun} üründe Renk + Web Color dolduruldu`);
      fetchProducts();
    } catch (e) {
      toast.dismiss(t);
      toast.error(e.response?.data?.detail || "Renk/Web Color doldurma başarısız");
    }
  };

  const handleAIAciklamaUret = async () => {
    const token = localStorage.getItem('token');
    const t = toast.loading("Boş açıklamalar sayılıyor...");
    try {
      const pre = await axios.post(
        `${API}/integrations/site/aciklama/generate?apply=false`,
        null, { headers: { Authorization: `Bearer ${token}` }, timeout: 60000 }
      );
      toast.dismiss(t);
      const total = pre.data?.bos_aciklamali_urun || 0;
      if (total === 0) { toast("Boş açıklamalı ürün yok"); return; }
      const ok = window.confirm(
        "AI AÇIKLAMA ÜRET — ÖNİZLEME\n\n" +
        `• Açıklaması boş ürün: ${total}\n\n` +
        "Ürün Bilgisi AI ile özniteliklerden yazılır.\n" +
        "Kumaş = Materyal, Kalıp = Kalıp özniteliğinden.\n" +
        "Beden/Model ölçüleri BOŞ '___' bırakılır (elle doldurursun).\n" +
        "Yalnız BOŞ açıklamalar doldurulur; mevcutlar KORUNUR.\n\n" +
        "Üretim batch'ler halinde sürer. Başlatılsın mı?"
      );
      if (!ok) { toast("İptal edildi"); return; }
      let done = 0, remaining = total, guard = 0;
      const t2 = toast.loading(`AI açıklama üretiliyor... 0/${total}`);
      while (remaining > 0 && guard < 80) {
        guard++;
        const res = await axios.post(
          `${API}/integrations/site/aciklama/generate?apply=true&limit=10`,
          null, { headers: { Authorization: `Bearer ${token}` }, timeout: 180000 }
        );
        const g = res.data?.uretilen || 0;
        remaining = res.data?.kalan ?? 0;
        done += g;
        toast.loading(`AI açıklama üretiliyor... ${done}/${total}`, { id: t2 });
        if (g === 0) break;
      }
      toast.dismiss(t2);
      toast.success(`${done} ürüne AI açıklama üretildi` + (remaining > 0 ? ` · kalan ${remaining}` : ''));
      fetchProducts();
    } catch (e) {
      toast.dismiss(t);
      toast.error(e.response?.data?.detail || "AI açıklama üretimi başarısız");
    }
  };

  const handleBarkodPush = () => setBarcodePushOpen(true);

  // Toplu İşlemler menüsü: dışarı tıklayınca kapat
  useEffect(() => {
    if (!toolsMenuOpen) return;
    const onDocClick = (e) => {
      if (toolsMenuRef.current && !toolsMenuRef.current.contains(e.target)) setToolsMenuOpen(false);
    };
    document.addEventListener("mousedown", onDocClick);
    return () => document.removeEventListener("mousedown", onDocClick);
  }, [toolsMenuOpen]);

  const fetchProducts = async () => {
    const _seq = ++_prodReqSeq.current;   // bu isteğin sıra no'su
    setLoading(true);
    try {
      const token = localStorage.getItem('token');
      const p = buildProductParams({ paging: true });

      const res = await axios.get(`${API}/products?${p.toString()}&admin_view=1`, { headers: { Authorization: `Bearer ${token}` } });
      if (_seq !== _prodReqSeq.current) return;   // daha yeni bir istek başladı → bu (eski) yanıtı YOKSAY
      setProducts(res.data?.products || []);
      setTotal(res.data?.total || 0);
    } catch (err) {
      if (_seq === _prodReqSeq.current) console.error(err);
    } finally {
      if (_seq === _prodReqSeq.current) setLoading(false);
    }
  };

  /**
   * fetchCategories — Form alanlarını besleyen referans verileri çeker.
   *
   * NEDEN Promise.allSettled?
   *   Kullanıcının raporladığı "Ürün özellikleri çekilmiyor" bug'ının kök
   *   nedeni: Daha önce ardışık await kullanıldığında /api/attributes hata
   *   verdiğinde /api/variants/size ve /color çağrıları hiç yapılmıyordu.
   *   allSettled ile HER endpoint bağımsız çalışır → biri fail olsa bile
   *   diğerleri forma yüklenir.
   *
   * BESLEDİĞİ STATE'LER:
   *   - categories       → Kategori seçici dropdown (sol filtre + form).
   *   - globalAttributes → Ürün modalı "Özellikler" sekmesi (SearchableAttribute).
   *   - globalSizes      → Varyant bedenleri.
   *   - globalColors     → Varyant renkleri.
   */
  const fetchCategories = async () => {
    try {
      const token = localStorage.getItem('token');
      const auth = { headers: { Authorization: `Bearer ${token}` } };
      // Fetch each independently so one failure doesn't block the others
      const results = await Promise.allSettled([
        axios.get(`${API}/categories`),
        axios.get(`${API}/attributes`, auth),
        axios.get(`${API}/variants/size`, auth),
        axios.get(`${API}/variants/color`, auth),
      ]);
      const pick = (r) => (r.status === "fulfilled" ? r.value.data : null);
      setCategories(pick(results[0]) || []);
      setGlobalAttributes((pick(results[1]) || {}).attributes || []);
      const sizes = pick(results[2]) || [];
      const colors = pick(results[3]) || [];
      setGlobalSizes([...sizes].sort((a,b) => (a.sort_order||0) - (b.sort_order||0)));
      setGlobalColors([...colors].sort((a,b) => (a.sort_order||0) - (b.sort_order||0)));
    } catch (err) {
      console.error(err);
    }
  };

  /**
   * uploadImageFiles — Bir FileList/diziyi sırayla yükler (hem "Görsel Yükle" butonu hem
   * sürükle-bırak bu çekirdeği kullanır). Sadece resim (image/*) dosyaları kabul edilir;
   * diğerleri sessizce atlanır (kullanıcı klasöre alakasız dosya bırakırsa patlamaz).
   */
  const uploadImageFiles = async (fileList) => {
    const files = Array.from(fileList || []).filter(
      (f) => f && (f.type ? f.type.startsWith("image/") : /\.(png|jpe?g|webp|gif|avif|bmp|svg)$/i.test(f.name || ""))
    );
    if (!files.length) {
      const had = (fileList && fileList.length) || 0;
      if (had) toast.error("Sadece resim dosyaları yüklenebilir.");
      return;
    }
    setUploading(true);
    const token = localStorage.getItem('token');
    const uploaded = [];
    for (let file of files) {
      try {
        const fd = new FormData();
        fd.append('file', file);
        const res = await axios.post(`${API}/upload/image`, fd, {
          headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'multipart/form-data' },
        });
        if (res.data.url) {
          const fullUrl = res.data.url.startsWith('http') ? res.data.url : `${BACKEND_ORIGIN}${res.data.url}`;
          uploaded.push(fullUrl);
        }
      } catch (err) {
        console.error("Upload error for", file.name, ":", err.response?.data || err.message);
        toast.error(`${file.name} yüklenemedi: ${err.response?.data?.detail || err.message}`);
      }
    }
    // Fonksiyonel güncelleme: sıralama/silme ile yarış (stale state) olmasın.
    if (uploaded.length) setFormData((prev) => ({ ...prev, images: [...(prev.images || []), ...uploaded] }));
    setUploading(false);
  };

  const handleImageUpload = async (e) => {
    await uploadImageFiles(e.target.files);
    if (imageInputRef.current) imageInputRef.current.value = '';
  };

  // OS'tan dosya sürükle-bırak ile yükleme. Tile sıralama drag'i (HTML5 draggable) ile
  // ÇAKIŞMASIN diye yalnızca gerçek DOSYA sürüklemelerinde (dataTransfer "Files") devreye girer.
  const [fileDropActive, setFileDropActive] = useState(false);
  const _isFileDrag = (e) => {
    const t = e?.dataTransfer?.types;
    return !!t && (Array.from(t).includes("Files") || (t.contains && t.contains("Files")));
  };
  const handleGalleryDragOver = (e) => {
    if (!_isFileDrag(e)) return;          // tile sıralaması → dokunma
    e.preventDefault();
    e.dataTransfer.dropEffect = "copy";
    if (!fileDropActive) setFileDropActive(true);
  };
  const handleGalleryDragLeave = (e) => {
    if (!_isFileDrag(e)) return;
    if (!e.currentTarget.contains(e.relatedTarget)) setFileDropActive(false);
  };
  const handleGalleryDrop = (e) => {
    if (!(e.dataTransfer?.files && e.dataTransfer.files.length)) return;  // sıralama drop'u → yok say
    e.preventDefault();
    setFileDropActive(false);
    uploadImageFiles(e.dataTransfer.files);
  };

  /**
   * handleTrendyolSync — Tek bir ürünü Trendyol'a YENİ ürün olarak gönderir.
   *   Zorunlu attributes (SearchableAttribute'ın "ZORUNLU" rozetiyle gösterdiği
   *   alanlar) backend tarafında kontrol edilir; eksikse 400 döner.
   *   BACKEND: POST /api/integrations/trendyol/products/{id}/sync
   */
  const handleTrendyolSync = async (productId) => {
    try {
      const token = localStorage.getItem('token');
      const res = await axios.post(`${API}/integrations/trendyol/products/${productId}/sync`, {}, {
        headers: { Authorization: `Bearer ${token}` }
      });
      if (res.data.success) {
        toast.success(res.data.message || "Trendyol senkronizasyonu başlatıldı");
        fetchProducts();
      }
    } catch (err) {
      console.error("Trendyol sync error:", err);
      toast.error(err.response?.data?.detail || "Trendyol aktarımı başarısız");
    }
  };
  const removeImage = (index) => {
    const newImages = [...formData.images];
    newImages.splice(index, 1);
    setFormData({ ...formData, images: newImages });
  };
  // Görsel sıralama — sürükle-bırak ile diziyi yeniden düzenler. İlk görsel = KAPAK.
  const reorderImages = (from, to) => {
    if (from === null || to === null || from === to) return;
    const newImages = [...formData.images];
    const [moved] = newImages.splice(from, 1);
    newImages.splice(to, 0, moved);
    setFormData({ ...formData, images: newImages });
  };
  // Mobil/dokunmatik için ok ile taşıma (sürükleme zor olabilir)
  const moveImage = (index, dir) => {
    const to = index + dir;
    if (to < 0 || to >= formData.images.length) return;
    reorderImages(index, to);
  };
  // Görsel string ya da {url, is_size_table:true} dict olabilir. Ölçü/pazaryeri görseli
  // işaretlenince müşteriye gizlenir (storefront eler) ama admin galeride durmaya devam eder.
  const imgUrl = (img) => (typeof img === 'object' && img !== null ? (img.url || img.src || img.image || '') : img);
  const isSizeTableImg = (img) => (typeof img === 'object' && img !== null && !!img.is_size_table);
  // Ölçü Görseli alanına taşı: işaretle + dizinin SONUNA al (pazaryerine son görsel gider)
  const markAsSizeImage = (index) => {
    setFormData(prev => {
      const arr = [...prev.images];
      const [it] = arr.splice(index, 1);
      arr.push({ url: imgUrl(it), is_size_table: true });
      return { ...prev, images: arr };
    });
  };
  // Galeriye geri taşı: işareti kaldır (sitede tekrar görünür olur)
  const unmarkSizeImage = (index) => {
    setFormData(prev => {
      const arr = [...prev.images];
      arr[index] = imgUrl(arr[index]);
      return { ...prev, images: arr };
    });
  };

  /**
   * handleSubmit — Ürün kaydetme / güncelleme işlemi.
   *
   * AKIŞ:
   *   1) Form'daki attributes objesini backend'in beklediği diziye çevirir.
   *      Varsayılan olarak "Yaş Grubu: Yetişkin" ve "Menşei: TR" eklenir
   *      (Trendyol için zorunlu minimumlar).
   *   2) Oluşturma modunda, varyantlarda birden fazla renk varsa HER RENK
   *      için AYRI ürün oluşturur → Trendyol aynı renk grubunu tek ürün
   *      olarak kabul eder; bu ayrım orada zorunludur.
   *   3) Tek renkte "Web Color" ve "Renk" özellikleri otomatik set edilir.
   *   4) Edit modunda tüm payload doğrudan PUT edilir.
   *
   * BAĞLANTILAR:
   *   - POST/PUT /api/products
   *   - Başarı sonrası fetchProducts ile liste tazelenir, modal kapanır.
   */
  const handleSubmit = async (e) => {
    e.preventDefault();
    // Sezon zorunlu (kullanıcı isteği) — rapor sezon filtresi bu alandan beslenir
    if (!formData.season) {
      toast.error("Sezon Bilgisi zorunlu — Temel sekmesinde 'Envanter & Kimlik' altından seçin");
      return;
    }
    // Beden Önerisi (Kalıp) zorunlu (kullanıcı isteği) — ürün sayfası kalıp tavsiyesi buradan beslenir
    if (!formData.size_advice) {
      toast.error("Beden Önerisi (Kalıp) zorunlu — Temel sekmesinde Sezon'un altından seçin");
      return;
    }
    // Alış ve Satış fiyatı zorunlu (kullanıcı isteği)
    if (!(Number(formData.purchase_price) > 0)) {
      toast.error("Alış Fiyatı zorunlu — Fiyat sekmesinden girin");
      return;
    }
    if (!(Number(formData.price) > 0)) {
      toast.error("Satış Fiyatı zorunlu — Fiyat sekmesinden girin");
      return;
    }
    try {
      const token = localStorage.getItem('token');
      const headers = { Authorization: `Bearer ${token}` };
      
      // FACETTE SABİT VARSAYILANLAR — her kayıtta boş kalanlar doldurulur (kullanıcı değeri ezilmez).
      const attrObj = {
        "Yaş Grubu": "Yetişkin", "Menşei": "TR", "Cinsiyet": "Kadın",
        "Koleksiyon": "Casual/Günlük", "Ortam": "Casual/Günlük",
        "Ek Özellik": "Yok", "Performans": "Cool & Comfort",
        "Kutu Durumu": "Kutu Yok", "Persona": "Fashion Forward",
        "Sürdürülebilirlik Detayı": "Hayır",
        ...(formData.attributes || {}),
      };
      // #16: "Yaka" formdan kaldırıldı; arka planda lazımsa "Yaka Tipi" değerinden türet.
      if (attrObj["Yaka Tipi"] && !attrObj["Yaka"]) attrObj["Yaka"] = attrObj["Yaka Tipi"];
      const attributesArray = Object.entries(attrObj)
        .filter(([_, v]) => v !== "" && v !== null && v !== undefined)
        .map(([k, v]) => ({ type: k, name: k, value: v }));

      // Teknik detayları (Kumaş, Kalıp, Model Ölçüleri vb.) attribute array'ine ekle
      // — backend'e kayıt için her teknik detayı ayrı {type, name, value} satırı yap
      for (const [slug, item] of Object.entries(technicalDetails || {})) {
        if (item && item.value) {
          const lbl = item.label || slug;
          // ÇAKIŞMA DÜZELTMESİ: technicalDetails state'i kullanıcı tarafından DÜZENLENMEZ
          // (yalnız açılışta üründen yeniden kurulur = kaydın ESKİ hali). Özellikler
          // sekmesinde girilen değer ASILDIR. Eskiden burada technicalDetails koşulsuz
          // ÜZERİNE YAZIYORDU → kullanıcı 'Kalıp/Kumaş/Materyal' gibi teknik-etiketli bir
          // özelliği düzenleyip kaydedince eski değer geri geliyordu. Artık yalnız
          // Özellikler'de o etiket BOŞSA teknik detaydan doldurulur.
          const existing = attributesArray.findIndex(a => (a.type || a.name) === lbl);
          if (existing >= 0) {
            if (!String(attributesArray[existing].value ?? "").trim()) {
              attributesArray[existing] = { type: lbl, name: lbl, value: item.value };
            }
          } else {
            attributesArray.push({ type: lbl, name: lbl, value: item.value });
          }
        }
      }

      const payload = {
        ...formData,
        attributes: attributesArray,
        variants: formData.variants?.map(v => ({
          ...v,
          stock_code: formData.stock_code || v.stock_code,
        })) || []
      };

      // Yeni urun(ler): TEK Urun Kart ID. Renkler ayri urune bolunse bile hepsi AYNI id'yi alir
      // (kart id kendi icinde artmaz). Bos ise sistemdeki en buyuk + 1 alinir.
      if (!editingProduct) {
        let cid = String((formData.ticimax_fields && formData.ticimax_fields.URUNKARTIID) || formData.urun_karti_id || "").trim();
        if (!cid) {
          try {
            const r = await axios.get(`${API}/products/meta/next-card-id`, { headers });
            cid = String(r.data?.card_id || "");
          } catch (e) { /* backend yine de otomatik atar */ }
        }
        if (cid) {
          payload.urun_karti_id = cid;
          payload.ticimax_fields = { ...(payload.ticimax_fields || {}), URUNKARTIID: cid };
        }
      }

      // Get unique colors from variants
      const uniqueColors = [...new Set((payload.variants || []).map(v => v.color).filter(Boolean))];
      
      if (uniqueColors.length > 1 && !editingProduct) {
        // Multi-color: create a separate product per color
        toast.info(`${uniqueColors.length} farklı renk için ayrı ürünler oluşturuluyor...`);

        // Renk-kardeşi gruplama anahtarı (csv_card_id) TÜM renklerde AYNI → "Diğer Renkler"
        // swatch'ında bağlı kalır. Ama her renk LİSTEDE kendi BENZERSİZ Ürün Kart ID'sini gösterir:
        // ilk renk taban kart id'de kalır, sonraki renkler backend'de max+1 ile otomatik artar.
        const groupCardId = String(payload.urun_karti_id || "").trim();
        let _firstColor = true;
        for (const color of uniqueColors) {
          const colorVariants = payload.variants.filter(v => v.color === color);
          // Set Web Color and Renk to this color in attributes
          const colorAttrs = attributesArray
            .filter(a => a.type !== "Web Color" && a.type !== "Renk")
            .concat([
              { type: "Web Color", name: "Web Color", value: color },
              { type: "Renk", name: "Renk", value: color }
            ]);
          
          const colorPayload = {
            ...payload,
            name: `${formData.name} ${color}`,
            slug: generateSlug(`${formData.name} ${color}`) + `-${Date.now()}`,
            attributes: colorAttrs,
            variants: colorVariants,
            csv_card_id: groupCardId || undefined,   // paylaşımlı renk-kardeşi anahtarı
          };
          if (_firstColor) {
            // İlk renk taban Ürün Kart ID'sinde kalır
            if (groupCardId) {
              colorPayload.urun_karti_id = groupCardId;
              colorPayload.ticimax_fields = { ...(colorPayload.ticimax_fields || {}), URUNKARTIID: groupCardId };
            }
          } else {
            // Sonraki renkler BENZERSİZ kart id alsın → urun_karti_id/URUNKARTIID gönderme,
            // backend sistemdeki max + 1'i otomatik atar (insert'ler sıralı olduğu için artar).
            delete colorPayload.urun_karti_id;
            if (colorPayload.ticimax_fields) {
              colorPayload.ticimax_fields = { ...colorPayload.ticimax_fields };
              delete colorPayload.ticimax_fields.URUNKARTIID;
            }
          }
          delete colorPayload.newVariant;
          await axios.post(`${API}/products`, colorPayload, { headers });
          _firstColor = false;
        }
        toast.success(`${uniqueColors.length} ürün oluşturuldu (her renk ayrı kart ID, renkler bağlı)`);
      } else if (uniqueColors.length === 1 && !editingProduct) {
        // Single color: auto-set Web Color and Renk
        const color = uniqueColors[0];
        const colorAttrs = attributesArray
          .filter(a => a.type !== "Web Color" && a.type !== "Renk")
          .concat([
            { type: "Web Color", name: "Web Color", value: color },
            { type: "Renk", name: "Renk", value: color }
          ]);
        
        const singlePayload = {
          ...payload,
          name: formData.name.includes(color) ? formData.name : `${formData.name} ${color}`,
          slug: generateSlug(formData.name.includes(color) ? formData.name : `${formData.name} ${color}`) + `-${Date.now()}`,
          attributes: colorAttrs,
        };
        delete singlePayload.newVariant;
        await axios.post(`${API}/products`, singlePayload, { headers });
        toast.success("Ürün oluşturuldu");
      } else {
        // Edit mode or no variants
        delete payload.newVariant;
        if (editingProduct) {
          await axios.put(`${API}/products/${editingProduct.id}`, payload, { headers });
          toast.success("Ürün güncellendi");
        } else {
          await axios.post(`${API}/products`, payload, { headers });
          toast.success("Ürün oluşturuldu");
        }
      }
      // İmalattan açılan üründe kaydı 'ürün açıldı ✓' olarak işaretle (rozet + tekrar açılmasın)
      if (!editingProduct && formData.mfg_record_id) {
        axios.put(`${API}/manufacturing/${formData.mfg_record_id}`,
          { product_created: true }, { headers }).catch(() => {});
      }
      setModalOpen(false);
      resetForm();
      fetchProducts();
    } catch (err) {
      toast.error(err.response?.data?.detail || "Hata oluştu");
    }
  };

  /**
   * handleDelete — Ürünü kalıcı olarak siler.
   *   Geriye dönük silmeyi önlemek için JS confirm ile onay alır. Silme başarılı
   *   olduğunda liste tazelenir. Trendyol'da da hâlâ varsa oradan ayrıca
   *   kaldırılması gerekir (P2 backlog).
   */
  const handleDelete = async (id) => {
    if (!await window.appConfirm("Ürünü çöp kutusuna taşımak istediğinize emin misiniz?")) return;
    try {
      const token = localStorage.getItem('token');
      await axios.delete(`${API}/products/${id}`, { headers: { Authorization: `Bearer ${token}` } });
      toast.success("Ürün çöp kutusuna taşındı");
      fetchProducts();
    } catch (err) {
      console.error("Silme hatası:", err);
      toast.error(err.response?.data?.detail || "İşlem başarısız. Yetkinizi kontrol edin.");
    }
  };

  // ===== Çöp Kutusu =====
  const [trashOpen, setTrashOpen] = useState(false);
  const [trashItems, setTrashItems] = useState([]);
  const [trashTotal, setTrashTotal] = useState(0);
  const [trashLoading, setTrashLoading] = useState(false);
  const [trashSearch, setTrashSearch] = useState("");

  const fetchTrash = async (q = "") => {
    setTrashLoading(true);
    try {
      const token = localStorage.getItem('token');
      const res = await axios.get(`${API}/products/trash/list`, {
        params: { limit: 500, search: q || undefined },
        headers: { Authorization: `Bearer ${token}` },
      });
      setTrashItems(res.data.products || []);
      setTrashTotal(res.data.total || 0);
    } catch (err) {
      toast.error("Çöp kutusu yüklenemedi");
    } finally {
      setTrashLoading(false);
    }
  };
  const openTrash = () => { setTrashOpen(true); fetchTrash(); };
  const restoreProduct = async (id) => {
    try {
      const token = localStorage.getItem('token');
      await axios.post(`${API}/products/${id}/restore`, {}, { headers: { Authorization: `Bearer ${token}` } });
      toast.success("Ürün geri yüklendi");
      fetchTrash(trashSearch);
      fetchProducts();
    } catch { toast.error("Geri yükleme başarısız"); }
  };
  const permanentDelete = async (id) => {
    if (!await window.appConfirm("Bu ürün KALICI olarak silinecek ve geri alınamayacak. Emin misiniz?")) return;
    try {
      const token = localStorage.getItem('token');
      await axios.delete(`${API}/products/${id}/permanent`, { headers: { Authorization: `Bearer ${token}` } });
      toast.success("Ürün kalıcı olarak silindi");
      fetchTrash(trashSearch);
    } catch { toast.error("Kalıcı silme başarısız"); }
  };

  /**
   * handleTrendyolUpdate — Mevcut Trendyol ürününün STOK/FİYAT bilgisini günceller.
   *   (Yeni ürün göndermek için handleTrendyolSync kullanılır; bu fonksiyon
   *    sadece envanter/price güncellemesi yapar.)
   *   BACKEND: POST /api/integrations/trendyol/products/{id}/sync-inventory
   */
  const handleTrendyolUpdate = async (product) => {
    try {
      const token = localStorage.getItem('token');
      const res = await axios.post(`${API}/integrations/trendyol/products/${product.id}/sync-inventory`, {}, {
        headers: { Authorization: `Bearer ${token}` }
      });
      toast.success(res.data.message || "Trendyol stok/fiyat güncellendi");
    } catch (err) {
      toast.error("Güncelleme başarısız: " + (err.response?.data?.detail || err.message));
    }
  };

  /**
   * handleSplitByColor — Bu ürünün farklı RENK varyantlarını AYRI ürünlere böler.
   *   İlk renk ana üründe kalır; diğer renkler yeni ürün olur (aynı kart id → "Diğer Renkler").
   *   Bedenler her renk ürününün altında varyant olarak kalır.
   *   BACKEND: POST /api/products/{id}/split-by-color
   */
  const handleSplitByColor = async (product) => {
    if (!window.confirm(`"${product.name}" ürününün farklı renkleri AYRI ürünlere bölünecek. Devam edilsin mi?`)) return;
    try {
      const token = localStorage.getItem('token');
      const res = await axios.post(`${API}/products/${product.id}/split-by-color`, {}, {
        headers: { Authorization: `Bearer ${token}` }
      });
      if (res.data?.success) {
        toast.success(res.data.message || "Renkler ayrıldı");
        fetchProducts();
      } else {
        toast.info(res.data?.message || "Ayırma gerekmedi");
      }
    } catch (err) {
      toast.error("Ayırma başarısız: " + (err.response?.data?.detail || err.message));
    }
  };

  /**
   * handlePrintBarcode — TEK ürünün barkod/ürün kartını yeni sekmede açar ve
   *   yazdırma diyalogunu tetikler.
   *   Backend endpoint'i her varyant için ayrı bir barkod kartı döner
   *   (giyim firmalarındaki gibi: ürün adı, stok kodu, GTIN barkod, beden, renk).
   *   BACKEND: GET /api/products/{id}/barcode-card
   */
  const handlePrintBarcode = (productId, sizes = null) => {
    const token = localStorage.getItem('token');
    const q = sizes && sizes.length ? `&sizes=${encodeURIComponent(sizes.join(','))}` : '';
    const url = `${API}/products/${productId}/barcode-card?token=${token}${q}`;
    const w = window.open(url, '_blank', 'width=820,height=1000');
    if (w) { w.focus(); } // otomatik yazdirma yok: kopya adedini secip "Yazdir"a bas
  };

  /**
   * openBarcodeSizePicker — Barkod yazdırmadan önce beden seçimi sunar.
   *   mode:'single' → tek ürün (product objesi), mode:'bulk' → seçili ürünler.
   *   Default: TÜM bedenler işaretli (tek tık "Yazdır" = eski davranış).
   */
  const openBarcodeSizePicker = (mode, product = null) => {
    let sizeList = [];
    if (mode === 'single') {
      sizeList = [...new Set((product?.variants || []).map(v => (v.size || '').trim()).filter(Boolean))];
      if (sizeList.length <= 1) { handlePrintBarcode(product.id); return; } // tek/0 beden → direkt yazdır
    } else {
      const chosen = products.filter(p => selectedProducts.includes(p.id));
      sizeList = [...new Set(chosen.flatMap(p => (p.variants || []).map(v => (v.size || '').trim())).filter(Boolean))];
      if (sizeList.length <= 1) { handleBulkPrintBarcodes(); return; }
    }
    // Bedenleri mantıklı sırala (XS→XXL, sonra sayısal/diğer alfabetik)
    const ORDER = ['XXS','XS','S','M','L','XL','XXL','3XL','4XL'];
    sizeList.sort((a, b) => {
      const ia = ORDER.indexOf(a.toUpperCase()), ib = ORDER.indexOf(b.toUpperCase());
      if (ia !== -1 && ib !== -1) return ia - ib;
      if (ia !== -1) return -1;
      if (ib !== -1) return 1;
      return a.localeCompare(b, 'tr', { numeric: true });
    });
    setBarcodeSizeModal({
      mode,
      productId: product?.id || null,
      sizes: sizeList,
      selected: Object.fromEntries(sizeList.map(s => [s, true])),
    });
  };

  const confirmBarcodeSizePrint = () => {
    if (!barcodeSizeModal) return;
    const picked = barcodeSizeModal.sizes.filter(s => barcodeSizeModal.selected[s]);
    if (picked.length === 0) { toast.error("En az bir beden seçiniz"); return; }
    const all = picked.length === barcodeSizeModal.sizes.length;
    const sizes = all ? null : picked; // hepsi seçiliyse filtre gönderme
    if (barcodeSizeModal.mode === 'single') {
      handlePrintBarcode(barcodeSizeModal.productId, sizes);
    } else {
      handleBulkPrintBarcodes(sizes);
    }
    setBarcodeSizeModal(null);
  };

  /**
   * handleBulkPrintBarcodes — Seçili ürünlerin barkod kartlarını TEK bir
   *   yazdırılabilir sayfada gösterir. A4'e sığacak şekilde 2-4 kart/satır.
   *   BACKEND: POST /api/products/barcode-cards/bulk (body: { ids: [...], sizes?: [...] })
   */
  const handleBulkPrintBarcodes = async (sizes = null) => {
    if (selectedProducts.length === 0) {
      toast.error("Lütfen ürün seçiniz");
      return;
    }
    try {
      const token = localStorage.getItem('token');
      const body = { ids: selectedProducts };
      if (sizes && sizes.length) body.sizes = sizes;
      const res = await axios.post(
        `${API}/products/barcode-cards/bulk`,
        body,
        { headers: { Authorization: `Bearer ${token}` }, responseType: 'text' }
      );
      const w = window.open('', '_blank', 'width=900,height=1100');
      if (w) {
        w.document.write(res.data);
        w.document.close();
        w.focus(); // otomatik yazdirma yok: kopya adedini secip "Yazdir"a bas
      }
    } catch (err) {
      toast.error("Barkod kartları oluşturulamadı: " + (err.response?.data?.detail || err.message));
    }
  };

  /**
   * handleBulkDeleteProducts — Seçili ürünleri toplu sil (onaylı).
   */
  const handleBulkDeleteProducts = async () => {
    if (selectedProducts.length === 0) {
      toast.error("Lütfen ürün seçiniz");
      return;
    }
    const ok = await window.appConfirm({
      title: `${selectedProducts.length} ürün silinsin mi?`,
      description: "Bu işlem geri alınamaz. Ürünler kalıcı olarak silinecek ve varsa pazaryerlerindeki eşleşmeleri de etkilenebilir.",
      confirmText: `Evet, ${selectedProducts.length} Ürünü Sil`,
      cancelText: "Vazgeç",
      variant: "danger",
    });
    if (!ok) return;
    const token = localStorage.getItem('token');
    let success = 0, failed = 0;
    for (const id of selectedProducts) {
      try {
        await axios.delete(`${API}/products/${id}`, { headers: { Authorization: `Bearer ${token}` } });
        success++;
      } catch { failed++; }
    }
    if (success) toast.success(`${success} ürün silindi${failed ? `, ${failed} başarısız` : ""}`);
    else toast.error(`Silme başarısız (${failed})`);
    setSelectedProducts([]);
    fetchProducts();
  };

  /**
   * openEditModal — Seçili ürünü düzenleme modunda modala doldurur.
   *   formData'ya tüm ürün alanlarını + attributes dizisini Object map'e çevirerek
   *   yerleştirir. Ölçü Tablosu sekmesinde SizeTablePanel bileşeni
   *   `product.id`'yi kullanarak kendi verisini çeker.
   */
  const openEditModal = async (productArg, options = {}) => {
    const { skipNavigate = false } = options;
    // DB'den taze çek (enrich/sync sonrası UI cache stale olabilir)
    let product = productArg;
    try {
      const token = localStorage.getItem('token');
      const headers = { Authorization: `Bearer ${token}` };
      const id = typeof productArg === "string" ? productArg : productArg.id;
      const res = await axios.get(`${API}/products/${id}`, { headers });
      if (res.data) product = res.data;
    } catch {
      // fallback: kullan listedeki cached product
    }
    if (!product || !product.id) {
      toast.error("Ürün bulunamadı");
      return;
    }
    if (!skipNavigate) {
      // Direct link için URL'i güncelle (geri butonu çalışsın diye replace değil push)
      navigate(`/admin/urunler/${product.id}`, { replace: false });
    }
    setEditingProduct(product);
    setMemberPriceManual(true); // mevcut ürün: üye fiyatı bağımsız, otomatik ezilmez
    // Parse edilmiş teknik detayları (XML import'dan) ayrı state'e al — Özellikler sekmesinin
    // üstündeki "Teknik Detay" panelinde gösterilecek
    const raw = product.attributes;
    if (raw && typeof raw === "object" && !Array.isArray(raw)) {
      // Dict shape: { kumas: {label, value}, ... }
      const isDictShape = Object.values(raw).every(
        v => v && typeof v === "object" && ("value" in v || "label" in v)
      );
      setTechnicalDetails(isDictShape ? raw : {});
    } else if (Array.isArray(raw)) {
      // Backend savedi: [{ type, name, value }] formatından dict'e geri inşa et
      const dict = {};
      const techLabels = ["Kumaş", "Kumaş Bilgisi", "Kumaş & İçerik Bilgisi", "Kumaş İçeriği",
        "Materyal", "İçerik", "Kalıp", "Beden Ölçüleri", "STD Beden Ölçüleri",
        "Model Ölçüleri", "Yıkama", "Yıkama Talimatı", "Bakım", "Bakım Talimatı",
        "Astar", "Astar Bilgisi", "Ürün Bilgisi", "Ürün Kodu"];
      for (const a of raw) {
        if (techLabels.some(l => (a.type || a.name || "").toLowerCase().includes(l.toLowerCase().slice(0, 6)))) {
          const slug = (a.type || a.name || "").toLowerCase()
            .replace(/[^a-z0-9çğıöşü]/g, "_").replace(/_+/g, "_").replace(/^_|_$/g, "");
          dict[slug] = { label: a.type || a.name, value: a.value };
        }
      }
      setTechnicalDetails(dict);
    } else {
      setTechnicalDetails({});
    }
    setFormData({
      id: product.id,
      category_id: product.category_id,
      trendyol_category_id: product.trendyol_category_id,
      hepsiburada_category_id: product.hepsiburada_category_id,
      name: product.name || "",
      slug: product.slug || "",
      description: product.description || "",
      short_description: product.short_description || "",
      price: product.price || 0,
      sale_price: product.sale_price || null,
      size_advice: product.size_advice || "",
      category_name: product.category_name || "",
      categories: (() => {
        // Yaprak seçim: önce product.categories, yoksa category_id / category_name eşlemesi.
        const leaf = (Array.isArray(product.categories) && product.categories.length)
          ? product.categories.map(String)
          : (product.category_id ? [String(product.category_id)]
             : (categories.find(c => c.name === product.category_name)?.id
                ? [String(categories.find(c => c.name === product.category_name).id)] : []));
        // Ürünün GERÇEK üyeliği category_ids'te (En Yeniler dâhil). category_ids ataları da
        // içerir; bu yüzden seçili yaprakların ATALARINI hariç tutup yalnızca "ekstra"
        // üyelikleri (örn. En Yeniler) ekleriz → form gerçek üyeliği yansıtır, category_name bozulmaz.
        const all = Array.isArray(product.category_ids) ? product.category_ids.map(String) : [];
        if (!all.length) return leaf;
        const byId = new Map(categories.map(c => [String(c.id), c]));
        const anc = new Set();
        for (const lid of leaf) {
          let cur = byId.get(lid), g = 0;
          while (cur && cur.parent_id && g++ < 20) { anc.add(String(cur.parent_id)); cur = byId.get(String(cur.parent_id)); }
        }
        const extras = all.filter(id => !leaf.includes(id) && !anc.has(id) && byId.has(id));
        return [...leaf, ...extras];
      })(),
      brand: product.brand || "FACETTE",
      images: product.images || [],
      is_active: product.is_active ?? true,
      is_featured: product.is_featured ?? false,
      is_new: product.is_new ?? false,
      stock: product.stock || 0,
      stock_code: product.stock_code || "",
      season: product.season || "",
      barcode: product.barcode || "",
      sku: product.sku || "",
      urun_karti_id: product.urun_karti_id || "",
      urun_id: product.urun_id || "",
      variation_code: product.variation_code || "",
      gtip_code: product.gtip_code || "",
      unit: product.unit || "ADET",
      keywords: product.keywords || "",
      supplier: product.supplier || "",
      manufacturer: product.manufacturer || "FACETTE",
      max_installment: product.max_installment || 9,
      purchase_price: product.purchase_price || 0,
      member_price_1: product.member_price_1 ?? null,
      // FAZ 7 — İmalat planı için ek alanlar (geri yükleme)
      collection: product.collection || "",
      color: product.color || "",
      market_price: product.market_price || 0,
      vat_rate: product.vat_rate || 10,
      vat_included: product.vat_included ?? true,
      currency: product.currency || "TRY",
      cargo_weight: product.cargo_weight || 0,
      product_weight: product.product_weight || 0,
      width: product.width || 0,
      depth: product.depth || 0,
      height: product.height || 0,
      min_order_qty: product.min_order_qty || 1,
      max_order_qty: product.max_order_qty || 999,
      estimated_delivery: product.estimated_delivery || "2-3",
      is_free_shipping: product.is_free_shipping ?? false,
      is_showcase: product.is_showcase ?? false,
      meta_title: product.meta_title || "",
      meta_description: product.meta_description || "",
      meta_keywords: product.meta_keywords || "",
      use_default_markup: product.use_default_markup ?? true,
      markup_rate: product.markup_rate || 0,
      trendyol_attributes: product.trendyol_attributes || {},
      hepsiburada_attributes: product.hepsiburada_attributes || {},
      temu_attributes: product.temu_attributes || {},
      variants: product.variants || [],
      combine_products: product.combine_products || [],
      attributes: (() => {
        const base = { "Yaş Grubu": "Yetişkin", "Menşei": "TR" };
        const a = product.attributes;
        if (!a) return base;
        // Array shape (existing): [{type|name, value}, …]
        if (Array.isArray(a)) {
          return a.reduce((acc, curr) => ({ ...acc, [curr.type || curr.name]: curr.value }), base);
        }
        // Object shape (XML import): { slug: {label, value} | string }
        if (typeof a === "object") {
          const out = { ...base };
          for (const [k, v] of Object.entries(a)) {
            const key = (v && typeof v === "object" && v.label) ? v.label : k;
            const val = (v && typeof v === "object") ? v.value : v;
            out[key] = val;
          }
          return out;
        }
        return base;
      })(),
      ticimax_fields: { ...(product.ticimax_fields || {}), URUNKARTIID: ((product.ticimax_fields || {}).URUNKARTIID || product.urun_karti_id || "") },
    });
    setModalOpen(true);
  };

  const resetForm = () => {
    setEditingProduct(null);
    setShowAllAttributes(false);
    setTechnicalDetails({});   // önceki düzenlemeden teknik detay TAŞINMASIN (yeni üründe boş)
    setMemberPriceManual(false); setMultiSizes([]); setMultiColors([]);
    setFormData({
      name: "", slug: "", description: "", short_description: "",
      price: 0, sale_price: null, category_name: "", categories: [], brand: "FACETTE",
      images: [], is_active: false, is_featured: false, is_new: false,
      stock: 0, stock_code: "", season: "", barcode: "", sku: "",
      urun_karti_id: "", urun_id: "",
      variation_code: "", gtip_code: "", unit: "ADET", keywords: "",
      supplier: "", manufacturer: "FACETTE", max_installment: 9, purchase_price: 0, member_price_1: null,
    // FAZ 7 — İmalat modülü entegrasyonu için ek alanlar
    collection: "", color: "",
      market_price: 0, vat_rate: 10, vat_included: true, currency: "TRY",
      cargo_weight: 0, product_weight: 0, width: 0, depth: 0, height: 0,
      min_order_qty: 1, max_order_qty: 999, estimated_delivery: "2-3",
      is_free_shipping: false, is_showcase: false,
      meta_title: "", meta_description: "", meta_keywords: "",
      use_default_markup: true, markup_rate: 0,
      trendyol_attributes: {},
      hepsiburada_attributes: {},
      temu_attributes: {},
      variants: [], newVariant: {},
      combine_products: [],
      attributes: {
        "Yaş Grubu": "Yetişkin",          // #9
        "Menşei": "TR",
        "Cinsiyet": "Kadın",               // #7
        "Koleksiyon": "Casual/Günlük",     // #8
        "Ortam": "Casual/Günlük",          // #8
        "Ek Özellik": "Yok",      // #10
        "Performans": "Cool & Comfort",    // #11
        "Kutu Durumu": "Kutu Yok",         // #12
        "Persona": "Fashion Forward",      // sabit
        "Sürdürülebilirlik Detayı": "Hayır", // sabit (kullanıcı isteği)
      },
      ticimax_fields: {},
    });
  };

  const generateSlug = (name) => {
    return name.toLowerCase()
      .replace(/ğ/g, 'g').replace(/ü/g, 'u').replace(/ş/g, 's')
      .replace(/ı/g, 'i').replace(/ö/g, 'o').replace(/ç/g, 'c')
      .replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');
  };

  // Sıralanabilir sütun başlığı: 1. tık yön, 2. tık ters, 3. tık varsayılan
  const handleSort = (field, firstDir = "asc") => {
    setPage(1);
    setSortBy((prev) => {
      if (prev.field !== field) return { field, dir: firstDir };
      const opposite = firstDir === "asc" ? "desc" : "asc";
      if (prev.dir === firstDir) return { field, dir: opposite };
      return { field: null, dir: null };
    });
  };
  const SortTH = ({ field, label, firstDir = "asc", className = "" }) => {
    const isActive = sortBy.field === field;
    return (
      <th
        onClick={() => handleSort(field, firstDir)}
        data-testid={`sort-${field}`}
        className={`cursor-pointer select-none hover:text-orange-600 ${className}`}
        title="Sıralamak için tıkla"
      >
        <span className="inline-flex items-center gap-1">
          {label}
          {isActive ? (
            sortBy.dir === "asc" ? <ChevronUp size={13} /> : <ChevronDown size={13} />
          ) : (
            <ChevronDown size={13} className="opacity-25" />
          )}
        </span>
      </th>
    );
  };

  // Detay alanlarını (ek ürün bilgileri) ilgili sekmelerin içinde render eder.
  const updateDetailField = (key, val) =>
    setFormData((prev) => ({
      ...prev,
      ticimax_fields: { ...(prev.ticimax_fields || {}), [key]: val },
    }));
  const renderDetailFields = (groupLabels) => (
    <ProductDetailFields
      schema={ticimaxSchema}
      groupLabels={groupLabels}
      values={formData.ticimax_fields || {}}
      onChange={updateDetailField}
    />
  );

  const handleDuplicate = async (product) => {
    try {
      const token = localStorage.getItem('token');
      const headers = { Authorization: `Bearer ${token}` };
      // DENETİM FIX: eski kopya, tüm alanları (varyant barkodları, urun_id, urun_karti_id,
      // csv_card_id) aynen kopyalayıp çakışan barkod/"renk-kardeşi" kopyalar yaratıyordu.
      // Backend'in /duplicate ucu yeni kart id + yeni barkod + temiz urun_id üretir.
      await axios.post(`${API}/products/${product.id}/duplicate`, {}, { headers });
      toast.success("Ürün kopyalandı");
      fetchProducts();
    } catch (err) {
      toast.error(err.response?.data?.detail || "Kopyalama başarısız");
    }
  };

  const setProductActive = async (product, makeActive) => {
    // Mevcut durumla aynıysa hiçbir şey yapma
    if (Boolean(product.is_active) === Boolean(makeActive)) return;
    try {
      const token = localStorage.getItem('token');
      const headers = { Authorization: `Bearer ${token}` };
      await axios.post(
        `${API}/products/${product.id}/toggle-active`,
        null,
        { headers },
      );
      toast.success(makeActive ? "Ürün aktifleştirildi" : "Ürün pasife alındı");
      fetchProducts();
    } catch {
      toast.error("İşlem başarısız");
    }
  };

  const openVariantsModal = (product) => {
    setSelectedProductForVariants(product);
    setVariantsModalOpen(true);
  };

  const handleSaveVariants = async () => {
    try {
      const token = localStorage.getItem('token');
      const headers = { Authorization: `Bearer ${token}` };
      
      await axios.put(`${API}/products/${selectedProductForVariants.id}`, selectedProductForVariants, { headers });
      
      toast.success("Varyantlar başarıyla güncellendi");
      setVariantsModalOpen(false);
      fetchProducts();
    } catch (err) {
      toast.error("Varyantlar güncellenirken hata oluştu");
    }
  };

  return (
    <div data-testid="admin-products">
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-bold">Ürünler ({total}) <span className="text-sm font-normal text-gray-400">aktif + pasif tüm katalog</span></h1>
        <div className="flex items-center gap-3">
          <input
            type="file"
            ref={fileInputRef}
            onChange={handleImport}
            accept=".xlsx, .xls"
            className="hidden"
          />
          <input
            type="file"
            ref={techFileInputRef}
            onChange={handleTechImport}
            accept=".xlsx, .xls"
            className="hidden"
          />

          {/* Excel Yükleme Sihirbazı — çözümleme sonucu + sütun/kategori seçimi + bitiş ekranı */}
          {importWizard && (
            <div className="fixed inset-0 z-50 bg-black/40 flex items-center justify-center p-4" data-testid="import-wizard">
              <div className="bg-white rounded-xl shadow-2xl max-w-2xl w-full max-h-[85vh] overflow-y-auto p-5 text-left font-normal">
                {importWizard.step === "select" ? (
                  <>
                    <h3 className="text-lg font-bold mb-1">Excel Güncelleme — Seçim</h3>
                    <p className="text-xs text-gray-500 mb-3">
                      <b>{importWizard.file?.name}</b> · {importWizard.analysis.rows} satır ·{" "}
                      <b className="text-emerald-700">{importWizard.analysis.matched_products} ürün eşleşti</b>
                      {" "}(barkodla) · Henüz hiçbir şey yazılmadı — yalnız aşağıda seçtikleriniz güncellenecek.
                    </p>

                    <p className="text-xs font-bold text-gray-600 uppercase mb-1.5">1) Güncellenecek sütunlar</p>
                    <div className="flex flex-wrap gap-1.5 mb-4">
                      {(importWizard.analysis.updatable_columns || []).map((c) => {
                        const on = importWizard.selCols.has(c);
                        return (
                          <button key={c} type="button"
                            onClick={() => setImportWizard(w => {
                              const s = new Set(w.selCols); s.has(c) ? s.delete(c) : s.add(c);
                              return { ...w, selCols: s };
                            })}
                            className={`px-2.5 py-1 rounded-full text-xs font-semibold border transition ${on ? "bg-blue-600 text-white border-blue-600" : "bg-white text-gray-500 border-gray-300 hover:border-blue-400"}`}>
                            {c} {on ? "✓" : ""}
                          </button>
                        );
                      })}
                      {(importWizard.analysis.updatable_columns || []).length === 0 && (
                        <p className="text-xs text-red-600">Dosyada güncellenebilir sütun bulunamadı (Stok, Satış Fiyatı, Sezon, Özellik: ... bekleniyor).</p>
                      )}
                    </div>

                    <p className="text-xs font-bold text-gray-600 uppercase mb-1.5">
                      2) Hangi kategoriler güncellensin? <span className="text-gray-400 font-normal normal-case">(hiçbiri seçilmezse TÜMÜ)</span>
                    </p>
                    <div className="flex flex-wrap gap-1.5 mb-4 max-h-36 overflow-y-auto">
                      {(importWizard.analysis.categories || []).map((c) => {
                        const on = importWizard.selCats.has(c);
                        return (
                          <button key={c} type="button"
                            onClick={() => setImportWizard(w => {
                              const s = new Set(w.selCats); s.has(c) ? s.delete(c) : s.add(c);
                              return { ...w, selCats: s };
                            })}
                            className={`px-2.5 py-1 rounded-full text-xs border transition ${on ? "bg-gray-900 text-white border-gray-900" : "bg-white text-gray-500 border-gray-300 hover:border-gray-500"}`}>
                            {c} {on ? "✓" : ""}
                          </button>
                        );
                      })}
                      {(importWizard.analysis.categories || []).length === 0 && (
                        <p className="text-xs text-gray-400">Dosyada Kategori sütunu yok — tüm eşleşen satırlar güncellenir.</p>
                      )}
                    </div>

                    <div className="flex justify-end gap-2 pt-3 border-t">
                      <button type="button" onClick={() => setImportWizard(null)}
                        className="px-4 py-2 text-sm border rounded-lg hover:bg-gray-50">Vazgeç</button>
                      <button type="button" onClick={applyImportWizard} disabled={importWizard.applying}
                        data-testid="import-apply"
                        className="px-5 py-2 text-sm bg-blue-600 text-white rounded-lg hover:bg-blue-700 font-semibold disabled:opacity-50">
                        {importWizard.applying ? "Güncelleniyor…" : `Güncellemeyi Başlat (${importWizard.selCols.size} sütun${importWizard.selCats.size ? ` · ${importWizard.selCats.size} kategori` : " · tüm kategoriler"})`}
                      </button>
                    </div>
                  </>
                ) : (
                  <>
                    <div className="text-center py-4">
                      <div className="text-4xl mb-2">✅</div>
                      <h3 className="text-lg font-bold mb-1">Güncelleme Tamamlandı</h3>
                      <p className="text-3xl font-bold text-emerald-600 my-3">
                        {importWizard.stats?.updated_products ?? importWizard.stats?.updated ?? 0}
                        <span className="text-sm text-gray-500 font-normal ml-1.5">üründe güncelleme yapıldı</span>
                      </p>
                      <div className="text-xs text-gray-500 space-y-0.5">
                        <p>{importWizard.stats?.updated_rows ?? 0} satır işlendi · {importWizard.stats?.skipped ?? 0} satır filtre dışı/değişiklik yok</p>
                        <p>{importWizard.stats?.no_match ?? 0} satır eşleşmedi · {importWizard.stats?.errors ?? 0} hata
                          {importWizard.stats?.duration_sec != null && <> · {importWizard.stats.duration_sec} sn</>}</p>
                      </div>
                    </div>
                    <div className="flex justify-center pt-3 border-t">
                      <button type="button" onClick={() => setImportWizard(null)}
                        className="px-6 py-2 text-sm bg-gray-900 text-white rounded-lg hover:bg-black font-semibold">Kapat</button>
                    </div>
                  </>
                )}
              </div>
            </div>
          )}
          <button
            onClick={handleExport}
            disabled={exporting}
            className="flex items-center gap-2 px-4 py-2 bg-green-600 text-white rounded hover:bg-green-700 transition-all font-medium text-sm shadow-sm disabled:opacity-50"
          >
            {exporting ? <RefreshCw className="animate-spin" size={16} /> : <Download size={16} />}
            Excel İndir
          </button>
          <button
            onClick={() => fileInputRef.current?.click()}
            disabled={importing}
            className="flex items-center gap-2 px-4 py-2 bg-blue-600 text-white rounded hover:bg-blue-700 transition-all font-medium text-sm shadow-sm disabled:opacity-50"
          >
            {importing ? <RefreshCw className="animate-spin" size={16} /> : <Upload size={16} />}
            Excel Yükle
          </button>
          <button
            onClick={() => techFileInputRef.current?.click()}
            disabled={techImporting}
            data-testid="tech-import-btn"
            className="flex items-center gap-2 px-4 py-2 bg-orange-600 text-white rounded hover:bg-orange-700 transition-all font-medium text-sm shadow-sm disabled:opacity-50"
          >
            {techImporting ? <RefreshCw className="animate-spin" size={16} /> : <FileSpreadsheet size={16} />}
            Teknik Detay Yükle
          </button>
          <button
            onClick={openTrash}
            data-testid="open-trash-btn"
            className="flex items-center gap-2 px-4 py-2 bg-gray-100 text-gray-700 rounded hover:bg-gray-200 transition-all font-medium text-sm shadow-sm border border-gray-200"
            title="Silinen ürünleri görüntüle ve geri yükle"
          >
            <Trash2 size={16} />
            Çöp Kutusu
          </button>
          <button 
            onClick={() => { resetForm(); setModalOpen(true); }}
            className="flex items-center gap-2 bg-black text-white px-4 py-2 rounded hover:bg-gray-800 transition-all font-medium text-sm shadow-sm"
          >
            <Plus size={18} />
            Yeni Ürün
          </button>
        </div>
      </div>

      {/* Search & Filter Top Bar */}
      <div className="flex flex-col md:flex-row gap-4 mb-4">
        <div className="relative flex-1">
          <Search size={18} className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" />
          <input
            type="text"
            value={search}
            onChange={(e) => { setSearch(e.target.value); setPage(1); }}
            placeholder="Ürün ara (ad, stok kodu, barkod)..."
            className="w-full pl-10 pr-4 py-2 border rounded focus:ring-1 focus:ring-black outline-none"
          />
        </div>
        <button 
          onClick={() => setShowFilters(!showFilters)}
          className={`flex items-center gap-2 px-4 py-2 border rounded transition-colors ${showFilters ? 'bg-black text-white' : 'bg-white hover:bg-gray-50'}`}
        >
          <Filter size={18} />
          <span>Gelişmiş Filtreleme</span>
          {showFilters ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
        </button>
      </div>

      {/* Toplu İşlemler — ürün aramanın altında dropdown (toolbar'dan taşındı) */}
      <div className="relative mb-4" ref={toolsMenuRef}>
        <button
          onClick={() => setToolsMenuOpen((o) => !o)}
          data-testid="bulk-tools-menu-btn"
          className="flex items-center gap-2 px-4 py-2 border rounded bg-white hover:bg-gray-50 transition-colors font-medium text-sm shadow-sm"
        >
          <Layers size={18} className="text-gray-700" />
          <span>Toplu İşlemler</span>
          <ChevronDown size={16} className={`transition-transform ${toolsMenuOpen ? "rotate-180" : ""}`} />
        </button>
        {toolsMenuOpen && (
          <div className="absolute left-0 mt-2 w-80 bg-white border border-gray-200 rounded-xl shadow-xl py-1.5 z-30">
            <div className="px-3 py-1.5 text-[11px] font-semibold uppercase tracking-wide text-gray-400">
              Ürün Toplu İşlemleri
            </div>
            {[
              { label: "Otomatik Doldur", desc: "Tüm ürünlerin teknik detaylarını eşle", color: "bg-purple-600", on: handleOtomatikDoldur, testid: "ticimax-tekdetay-sync-btn", icon: RefreshCw },
              { label: "Silinen Özellik Kurtar", desc: "Snapshot'tan teknik detayları geri yükle", color: "bg-teal-600", on: handleSilinenOzellikKurtar, testid: "teknik-detay-recover-btn", icon: RefreshCw },
              { label: "Eksik Açıklama Kurtar", desc: "Boş açıklamaları export'tan doldur", color: "bg-cyan-600", on: handleEksikAciklamaKurtar, testid: "aciklama-recover-btn", icon: RefreshCw },
              { label: "Renk + Web Color Doldur", desc: "Ad son kelimesinden renk + web color", color: "bg-fuchsia-600", on: handleRenkWebColorDoldur, testid: "renk-webcolor-autofill-btn", icon: RefreshCw },
              { label: "AI Açıklama Üret", desc: "Boş açıklamalara AI ile üret", color: "bg-violet-600", on: handleAIAciklamaUret, testid: "aciklama-generate-ai-btn", icon: RefreshCw },
              { label: "Barkod ile Trendyol'a Aktar", desc: "Barkod yazıp seçili ürünleri Trendyol'a gönder", color: "bg-orange-500", on: handleBarkodPush, testid: "trendyol-push-barcodes-btn", icon: Store },
            ].map((it) => {
              const Icon = it.icon;
              return (
                <button
                  key={it.testid}
                  data-testid={it.testid}
                  onClick={() => { setToolsMenuOpen(false); it.on(); }}
                  className="w-full flex items-start gap-3 px-3 py-2.5 hover:bg-gray-50 text-left transition-colors"
                >
                  <span className={`mt-0.5 w-7 h-7 shrink-0 rounded-md ${it.color} text-white flex items-center justify-center`}>
                    <Icon size={14} />
                  </span>
                  <span className="min-w-0">
                    <span className="block text-sm font-medium text-gray-900">{it.label}</span>
                    <span className="block text-xs text-gray-500 truncate">{it.desc}</span>
                  </span>
                </button>
              );
            })}
          </div>
        )}
      </div>

      {/* Advanced Filters Panel — Ticimax tarzı 3 kolonlu gelişmiş filtre */}
      {showFilters && (
        <ProductFilters
          filters={filters}
          update={updateFilter}
          onApply={applyFilters}
          onClear={clearFilters}
          categories={categories}
          filterOptions={filterOptions}
        />
      )}

      {/* =================================================================
          ÜRÜN TABLOSU
          -----------------------------------------------------------------
          - .admin-table-compact: Satırları daraltıp bir sayfaya daha çok
            ürün sığdırmak için uygulanıyor (index.css'de tanımlı).
          - ÜST Pagination (compact): Tablo başlığından ÖNCE durur, minimal
            tek satır "Sayfa X / Y" + ok + git kutusu — tasarımı bozmaz.
          - ALT Pagination (full)  : Numaralı düğmeler + ilk/son + git.
          - Her iki pagination da aynı `page/total/onChange` state'ini
            paylaşır → birinden yapılan değişiklik diğerinde yansır.
          ================================================================= */}

      {/* Üst (compact) pagination — listeyi açan kullanıcı aşağı inmeden
          sayfa değiştirebilsin + sayfa boyutunu değiştirebilsin. */}
      {total > 0 && (
        <div className="flex justify-end mb-2" data-testid="products-top-pagination">
          <Pagination
            page={page}
            total={total}
            pageSize={pageSize}
            onChange={setPage}
            onPageSizeChange={(s) => { setPageSize(s); setPage(1); }}
            variant="compact"
          />
        </div>
      )}

      {/* Toplu Seçim Bar'ı — en az bir ürün seçiliyken görünür.
          Buradan seçilen ürünlerin barkod kartları tek dosyada yazdırılır. */}
      {selectedProducts.length > 0 && (
        <div className="bg-orange-50 border border-orange-200 rounded-lg p-3 mb-3 flex items-center justify-between" data-testid="products-bulk-bar">
          <span className="text-sm font-medium text-gray-800">
            <span className="text-orange-600 font-bold">{selectedProducts.length}</span> ürün seçildi
          </span>
          <div className="flex items-center gap-2">
            <button
              onClick={() => openBarcodeSizePicker('bulk')}
              className="flex items-center gap-1 px-3 py-1.5 bg-orange-600 text-white text-sm rounded hover:bg-orange-700"
              data-testid="products-bulk-print-barcode-btn"
            >
              <Printer size={16} />
              Seçili Barkod Kartlarını Yazdır
            </button>
            <button
              onClick={handleBulkDeleteProducts}
              className="flex items-center gap-1 px-3 py-1.5 bg-red-600 text-white text-sm rounded hover:bg-red-700"
              data-testid="products-bulk-delete-btn"
            >
              <Trash2 size={16} />
              Seçili Ürünleri Sil
            </button>
            <button
              onClick={() => setSelectedProducts([])}
              className="px-3 py-1.5 text-sm text-gray-600 hover:bg-white rounded"
            >
              Seçimi Temizle
            </button>
          </div>
        </div>
      )}

      {/* Products Table */}
      <div className="bg-white rounded-lg shadow-sm overflow-x-auto">
        <table className="admin-table admin-table-compact">
          <thead>
            <tr>
              <th className="w-10">
                <button onClick={toggleSelectAllProducts} className="p-1" title="Tümünü seç" data-testid="products-select-all">
                  {selectedProducts.length === products.length && products.length > 0 ? (
                    <CheckSquare size={16} className="text-orange-600" />
                  ) : (
                    <Square size={16} />
                  )}
                </button>
              </th>
              <SortTH field="urun_karti_id" label="Ürün Kart ID" />
              <th>Görsel</th>
              <SortTH field="name" label="Ürün Adı" />
              <SortTH field="stock_code" label="Stok Kodu" />
              <th>Bedenler</th>
              <SortTH field="price" label="Fiyat" />
              <SortTH field="stock" label="Stok" className="text-center" />
              <SortTH field="is_active" label="Durum" firstDir="desc" />
              <th>İşlemler</th>
              <SortTH field="created_at" label="Eklenme Tarihi" firstDir="desc" />
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr><td colSpan={11} className="text-center py-8">Yükleniyor...</td></tr>
            ) : products.length === 0 ? (
              <tr><td colSpan={11} className="text-center py-8 text-gray-500">Ürün bulunamadı</td></tr>
            ) : (
              products.map((product) => (
                <tr key={product.id} data-testid={`product-row-${product.id}`}>
                  <td>
                    <button
                      onClick={() => toggleSelectProduct(product.id)}
                      className="p-1"
                      data-testid={`product-select-${product.id}`}
                    >
                      {selectedProducts.includes(product.id) ? (
                        <CheckSquare size={16} className="text-orange-600" />
                      ) : (
                        <Square size={16} />
                      )}
                    </button>
                  </td>
                  <td className="text-sm font-mono whitespace-nowrap text-gray-700 font-semibold" data-testid={`product-kartid-${product.id}`}>
                    {product.urun_karti_id || '-'}
                  </td>
                  <td>
                    {product.images?.[0] ? (
                      <div className="relative group/img overflow-visible z-0 hover:z-50">
                        <img 
                          src={fixImg(product.images[0])} 
                          alt="" 
                          className="w-10 h-14 object-cover rounded shadow-sm border border-gray-100 transition-all duration-300 group-hover/img:scale-[3.0] group-hover/img:shadow-xl group-hover/img:border-orange-200 cursor-zoom-in" 
                        />
                      </div>
                    ) : (
                      <div className="w-10 h-14 bg-gray-100 flex items-center justify-center rounded border border-gray-50 text-gray-400">
                        <Image size={14} />
                      </div>
                    )}
                  </td>
                  <td>
                    <a href={`/${product.slug || product.id}`} target="_blank" rel="noopener noreferrer" className="font-medium line-clamp-1 text-orange-600 hover:text-orange-800 hover:underline">
                      {product.name}
                    </a>
                    <p className="text-xs text-gray-500">
                      {(() => {
                        // Ürünün BAĞLI OLDUĞU TÜM kategoriler (yalnız birincil değil — kullanıcı isteği)
                        const _ids = [...new Set([...(product.category_ids || []), ...(product.categories || [])].map(String))];
                        const _names = _ids.map((id) => categories.find((c) => String(c.id) === id)?.name).filter(Boolean);
                        return [...new Set([product.category_name, ..._names].filter(Boolean))].join(" · ") || "—";
                      })()}
                    </p>
                  </td>
                  <td className="text-sm font-mono whitespace-nowrap">{product.stock_code || product.sku || '-'}</td>
                  <td>
                    {product.variants?.length > 0 ? (
                      <button 
                        onClick={() => openVariantsModal(product)}
                        className="flex items-center gap-1 text-xs text-orange-600 hover:text-orange-800 hover:underline"
                      >
                        <Layers size={14} />
                        {product.variants.length} Beden
                      </button>
                    ) : (product.sizes?.length > 0 ? (
                      <span className="inline-flex items-center gap-1 text-xs text-gray-700">
                        <Layers size={14} />
                        {product.sizes.join(' · ')}
                      </span>
                    ) : (
                      <span className="text-xs text-gray-400">-</span>
                    ))}
                  </td>
                  <td>
                    {priceEdit?.id === product.id ? (
                      <div className="flex flex-col gap-1" onClick={(e) => e.stopPropagation()}>
                        <input
                          type="number" step="0.01" autoFocus
                          value={priceEdit.price}
                          onChange={(e) => setPriceEdit((pe) => ({ ...pe, price: e.target.value }))}
                          onKeyDown={(e) => { if (e.key === 'Enter') savePriceEdit(); if (e.key === 'Escape') setPriceEdit(null); }}
                          placeholder="Satış Fiyatı"
                          title="Satış Fiyatı"
                          className="w-24 border border-gray-300 rounded px-2 py-1 text-sm focus:border-black outline-none"
                        />
                        <input
                          type="number" step="0.01"
                          value={priceEdit.sale_price ?? ''}
                          onChange={(e) => setPriceEdit((pe) => ({ ...pe, sale_price: e.target.value }))}
                          onKeyDown={(e) => { if (e.key === 'Enter') savePriceEdit(); if (e.key === 'Escape') setPriceEdit(null); }}
                          placeholder="İndirimli (boş=yok)"
                          title="İndirimli Fiyat (boş bırak = indirim yok)"
                          className="w-24 border border-gray-300 rounded px-2 py-1 text-sm text-red-600 focus:border-black outline-none"
                        />
                        <div className="flex gap-1">
                          <button onClick={savePriceEdit} className="px-2 py-0.5 text-xs font-bold bg-black text-white rounded hover:bg-gray-800">✓</button>
                          <button onClick={() => setPriceEdit(null)} className="px-2 py-0.5 text-xs font-bold bg-gray-200 text-gray-600 rounded hover:bg-gray-300">✕</button>
                        </div>
                      </div>
                    ) : (
                      <button
                        onClick={() => setPriceEdit({ id: product.id, price: product.price ?? '', sale_price: product.sale_price ?? '' })}
                        title="Fiyatı düzenle (ürün kartındaki Satış / İndirimli fiyat alanları)"
                        className="text-left hover:bg-orange-50 rounded px-1 -mx-1 cursor-pointer"
                        data-testid={`product-price-edit-${product.id}`}
                      >
                        {product.sale_price ? (
                          <div>
                            <span className="text-red-600">{product.sale_price?.toFixed(2)} TL</span>
                            <span className="text-xs text-gray-400 line-through block">{product.price?.toFixed(2)} TL</span>
                          </div>
                        ) : (
                          <span>{product.price?.toFixed(2)} TL</span>
                        )}
                      </button>
                    )}
                  </td>
                  <td>
                    <div className={`text-sm font-bold ${(product.variants?.length > 0 ? product.variants.reduce((s, v) => s + (v.stock || 0), 0) : product.stock || 0) < 5 ? 'text-red-600' : 'text-gray-700'}`}>
                      {product.variants?.length > 0 ? product.variants.reduce((s, v) => s + (v.stock || 0), 0) : (product.stock || 0)}
                    </div>
                  </td>
                  <td>
                    <div className="flex gap-1 items-center">
                      <button
                        onClick={() => setProductActive(product, true)}
                        title="Aktif yap"
                        data-testid={`product-set-active-${product.id}`}
                        className={`w-6 h-6 text-xs font-bold rounded transition-colors ${product.is_active ? 'bg-green-500 text-white' : 'bg-gray-200 text-gray-500 hover:bg-green-200 hover:text-green-800'}`}
                      >
                        A
                      </button>
                      <button
                        onClick={() => setProductActive(product, false)}
                        title="Pasif yap"
                        data-testid={`product-set-passive-${product.id}`}
                        className={`w-6 h-6 text-xs font-bold rounded transition-colors ${!product.is_active ? 'bg-red-500 text-white' : 'bg-gray-200 text-gray-500 hover:bg-red-200 hover:text-red-800'}`}
                      >
                        P
                      </button>
                    </div>
                  </td>
                  <td>
                    <div className="flex gap-1 items-center">
                        <button
                          onClick={() => openEditModal(product)}
                          className="p-1.5 hover:bg-gray-100 rounded"
                          title="Düzenle"
                          data-testid={`product-edit-modal-${product.id}`}
                        >
                          <Edit size={16} />
                        </button>
                        <button onClick={() => handleDuplicate(product)} className="p-1.5 hover:bg-gray-100 rounded" title="Kopyala">
                          <Copy size={16} />
                        </button>
                        <button
                          onClick={() => handleTrendyolSync(product.id)}
                          className="p-1.5 hover:bg-orange-100 rounded text-orange-600 transition-colors"
                          title="Trendyola Aktar (Yeni Ürün)"
                        >
                          <Store size={16} />
                        </button>
                        <button
                          onClick={() => handleTrendyolUpdate(product)}
                          className="p-1.5 hover:bg-orange-100 rounded text-orange-600 transition-colors"
                          title="Trendyol Stok/Fiyat Güncelle"
                        >
                          <RefreshCw size={16} />
                        </button>
                        <button
                          onClick={() => handleSplitByColor(product)}
                          className="p-1.5 hover:bg-teal-50 rounded text-teal-600 transition-colors"
                          title="Renge Göre Ayır (her renk ayrı ürün)"
                        >
                          <Layers size={16} />
                        </button>
                        <button
                          onClick={() => openBarcodeSizePicker('single', product)}
                          className="p-1.5 hover:bg-purple-50 rounded text-purple-600 transition-colors"
                          title="Barkod Kartı Yazdır"
                          data-testid={`product-print-barcode-${product.id}`}
                        >
                          <Printer size={16} />
                        </button>
                        <button
                          onClick={() => handleDelete(product.id)}
                          className="p-1.5 hover:bg-red-50 rounded text-red-500"
                          title="Sil"
                        >
                          <Trash2 size={16} />
                        </button>
                    </div>
                  </td>
                  <td className="text-xs text-gray-400 whitespace-nowrap">
                    {product.created_at ? new Date(product.created_at).toLocaleDateString('tr-TR', {day: '2-digit', month: '2-digit', year: 'numeric'}) : '-'}
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      {/* Pagination (alt — full numaralı). */}
      <Pagination
        page={page}
        total={total}
        pageSize={pageSize}
        onChange={setPage}
        onPageSizeChange={(s) => { setPageSize(s); setPage(1); }}
        variant="full"
      />

      {/* Product Modal with Tabs */}
      <Dialog open={modalOpen} onOpenChange={(open) => { setModalOpen(open); if(!open) resetForm(); }}>
        <DialogContent className="max-w-5xl max-h-[95vh] overflow-y-auto p-0">
          <div className="flex flex-col h-full bg-slate-50">
            <div className="p-6 bg-white border-b sticky top-0 z-10 flex justify-between items-center">
              <div>
                <h2 className="text-xl font-bold text-gray-900">{editingProduct ? "Ürün Düzenle" : "Yeni Ürün Oluştur"}</h2>
                <p className="text-sm text-gray-500">{formData.name || 'İsimsiz Ürün'}</p>
              </div>
              <div className="flex gap-2">
                <button 
                  type="button"
                  onClick={() => setModalOpen(false)}
                  className="px-4 py-2 text-sm font-medium text-gray-700 bg-white border rounded hover:bg-gray-50"
                >
                  İptal
                </button>
                <button 
                  onClick={handleSubmit}
                  className="px-6 py-2 text-sm font-medium text-white bg-black rounded hover:bg-gray-800 shadow-sm"
                >
                  {editingProduct ? "Değişiklikleri Kaydet" : "Ürünü Oluştur"}
                </button>
              </div>
            </div>

            <Tabs value={activeTab} onValueChange={setActiveTab} className="flex-1 flex flex-col p-6">
              <TabsList className="bg-gray-100/50 p-1 rounded-xl mb-6 flex flex-wrap h-auto gap-1">
                 <TabsTrigger value="basic" className="data-[state=active]:bg-white data-[state=active]:text-black data-[state=active]:shadow-sm px-6 py-2 text-sm font-medium rounded-lg transition-all">Temel</TabsTrigger>
                 <TabsTrigger value="pricing" className="data-[state=active]:bg-white data-[state=active]:text-black data-[state=active]:shadow-sm px-6 py-2 text-sm font-medium rounded-lg transition-all">Fiyat</TabsTrigger>
                 <TabsTrigger value="images" className="data-[state=active]:bg-white data-[state=active]:text-black data-[state=active]:shadow-sm px-6 py-2 text-sm font-medium rounded-lg transition-all">Görseller</TabsTrigger>
                 <TabsTrigger value="variants" className="data-[state=active]:bg-white data-[state=active]:text-black data-[state=active]:shadow-sm px-6 py-2 text-sm font-medium rounded-lg transition-all">Varyantlar</TabsTrigger>
                 <TabsTrigger value="seo" className="data-[state=active]:bg-white data-[state=active]:text-black data-[state=active]:shadow-sm px-6 py-2 text-sm font-medium rounded-lg transition-all">SEO</TabsTrigger>
                 <TabsTrigger value="attributes" className="data-[state=active]:bg-white data-[state=active]:text-black data-[state=active]:shadow-sm px-6 py-2 text-sm font-medium rounded-lg transition-all">Özellikler</TabsTrigger>
                 <TabsTrigger value="sizetable" className="data-[state=active]:bg-white data-[state=active]:text-black data-[state=active]:shadow-sm px-6 py-2 text-sm font-medium rounded-lg transition-all">Ölçü Tablosu</TabsTrigger>
                 <TabsTrigger value="combine" className="data-[state=active]:bg-white data-[state=active]:text-black data-[state=active]:shadow-sm px-6 py-2 text-sm font-medium rounded-lg transition-all">Kombin</TabsTrigger>
                 {/* "Trendyol Ayarları" sekmesi kullanıcı isteğiyle kaldırıldı — Trendyol
                     eşleştirme/aktarım Pazaryeri Hub ve ürün listesi araçlarından yönetiliyor. */}
               </TabsList>

              {/* Basic Info Tab */}
              <TabsContent value="basic" className="space-y-6 m-0 animate-in fade-in slide-in-from-bottom-2 duration-300">
                <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
                  <div className="md:col-span-2 space-y-6">
                    <div className="bg-white p-6 rounded-xl border shadow-sm space-y-4">
                      <h3 className="font-semibold text-gray-900 border-b pb-2 mb-4">Genel Bilgiler</h3>
                      <div className="grid grid-cols-2 gap-4">
                        <div className="col-span-2">
                          <label className="block text-xs font-bold text-gray-500 uppercase mb-1">Ürün Adı *</label>
                          <input
                            type="text"
                            value={formData.name}
                            onChange={(e) => {
                              setFormData({ 
                                ...formData, 
                                name: e.target.value,
                                slug: generateSlug(e.target.value)
                              });
                            }}
                            className="w-full border-gray-200 border px-3 py-2.5 rounded-lg focus:border-black outline-none transition-all"
                            placeholder="Örn: V Yaka Saten Elbise"
                            required
                          />
                        </div>
                        <div>
                          <label className="block text-xs font-bold text-gray-500 uppercase mb-1">Kategoriler (birden fazla seçilebilir)</label>
                          <div className="relative">
                            <div
                              className="w-full border-gray-200 border px-3 py-2.5 rounded-lg focus-within:border-black bg-white flex items-center justify-between cursor-pointer transition-all"
                              onClick={() => setCategorySearchOpen(!categorySearchOpen)}
                            >
                              <span className={(formData.categories?.length) ? "text-black text-sm" : "text-gray-400 text-sm"}>
                                {(formData.categories?.length) ? `${formData.categories.length} kategori seçili` : "Seçiniz"}
                              </span>
                              <ChevronDown size={16} className="text-gray-400" />
                            </div>

                            {(formData.categories?.length > 0) && (
                              <div className="flex flex-wrap gap-1 mt-1.5">
                                {formData.categories.map((cid) => {
                                  const cc = categories.find(x => x.id === cid);
                                  return (
                                    <span key={cid} className="inline-flex items-center gap-1 bg-gray-100 border border-gray-200 rounded px-2 py-0.5 text-xs">
                                      {cc ? (cc.full_name || cc.name) : cid}
                                      <button type="button" className="text-gray-400 hover:text-red-600"
                                        onClick={(e) => { e.stopPropagation(); const next = formData.categories.filter(id => id !== cid); setFormData({ ...formData, categories: next, category_name: next[0] ? (categories.find(x => x.id === next[0])?.name || formData.category_name) : "" }); }}>×</button>
                                    </span>
                                  );
                                })}
                              </div>
                            )}

                            {categorySearchOpen && (
                              <div className="absolute z-10 w-full mt-1 bg-white border border-gray-200 rounded-lg shadow-xl max-h-72 overflow-y-auto">
                                <div className="p-2 sticky top-0 bg-white border-b">
                                  <input
                                    type="text"
                                    placeholder="Kategori ara..."
                                    className="w-full border border-gray-200 rounded px-2 py-1.5 text-sm outline-none focus:border-black bg-gray-50 focus:bg-white transition-colors"
                                    value={categorySearchTerm}
                                    onChange={(e) => setCategorySearchTerm(e.target.value)}
                                    onClick={(e) => e.stopPropagation()}
                                    autoFocus
                                  />
                                </div>
                                <div className="p-1">
                                  {categories.filter(c => {
                                    // Arama: ad + full_name + SLUG + slug_aliases; ayrıca EŞANLAM
                                    // ('sale'↔'indirim') → "sale" yazınca İNDİRİM kategorisi bulunur.
                                    const _SYN = { sale: "indirim", indirim: "sale", "fırsat": "indirim", firsat: "indirim" };
                                    const q = (categorySearchTerm || "").toLowerCase().trim();
                                    if (!q) return true;
                                    const hay = [c.full_name, c.name, c.slug, ...(c.slug_aliases || [])].filter(Boolean).map(x => String(x).toLowerCase()).join(" ");
                                    return hay.includes(q) || (_SYN[q] && hay.includes(_SYN[q]));
                                  }).slice().sort((a, b) => (a.full_name || a.name || "").localeCompare(b.full_name || b.name || "", "tr")).map(c => {
                                    const checked = (formData.categories || []).includes(c.id);
                                    const _fn = c.full_name || c.name || "";
                                    const _parts = _fn.split(" > ");
                                    const _depth = Math.max(0, _parts.length - 1);
                                    const _leaf = _parts[_parts.length - 1] || c.name;
                                    return (
                                      <label
                                        key={c.id}
                                        className="flex items-center gap-2 px-3 py-2 text-sm hover:bg-gray-100 cursor-pointer rounded"
                                        title={c.full_name || c.name}
                                        onClick={(e) => e.stopPropagation()}
                                      >
                                        <input
                                          type="checkbox"
                                          className="accent-black shrink-0"
                                          checked={checked}
                                          onChange={(e) => {
                                            const cur = formData.categories || [];
                                            const next = e.target.checked ? [...cur, c.id] : cur.filter(id => id !== c.id);
                                            setFormData({ ...formData, categories: next, category_name: next[0] ? (categories.find(x => x.id === next[0])?.name || c.name) : "" });
                                          }}
                                        />
                                        <span className="truncate" style={{ paddingLeft: _depth * 14 }}>{_depth > 0 && <span className="text-gray-300 mr-1">›</span>}{_leaf}</span>
                                      </label>
                                    );
                                  })}
                                </div>
                                <div className="p-2 border-t sticky bottom-0 bg-white flex justify-end">
                                  <button type="button" className="text-xs px-3 py-1 bg-black text-white rounded hover:bg-gray-800" onClick={() => { setCategorySearchOpen(false); setCategorySearchTerm(""); }}>Tamam</button>
                                </div>
                              </div>
                            )}
                          </div>
                        </div>
                        <div>
                          <label className="block text-xs font-bold text-gray-500 uppercase mb-1">Marka</label>
                          <input
                            type="text"
                            value={formData.brand}
                            onChange={(e) => setFormData({ ...formData, brand: e.target.value })}
                            className="w-full border-gray-200 border px-3 py-2.5 rounded-lg focus:border-black outline-none transition-all"
                          />
                        </div>
                      </div>
                      <div>
                        <label className="block text-xs font-bold text-gray-500 uppercase mb-1">Açıklama</label>
                        <DescriptionEditor
                          value={formData.description}
                          onChange={(val) => setFormData({ ...formData, description: val })}
                          generating={aiDescLoading}
                          onGenerate={async () => {
                            if (!formData.name?.trim()) { toast.error("Önce ürün adını girin"); return; }
                            if (formData.description?.trim() && !window.confirm("Mevcut açıklamanın üzerine AI ile üretilen yazılsın mı?")) return;
                            setAiDescLoading(true);
                            try {
                              const attrsList = [];
                              const A = formData.attributes || {};
                              Object.entries(A).forEach(([k, v]) => {
                                const val = typeof v === "string" ? v : (v && v.value) ? v.value : (Array.isArray(v) ? v.join(", ") : "");
                                if (k && val) attrsList.push({ name: k, value: String(val) });
                              });
                              if (formData.color) attrsList.push({ name: "Renk", value: formData.color });
                              if (formData.collection) attrsList.push({ name: "Koleksiyon", value: formData.collection });
                              const token = localStorage.getItem("token");
                              const res = await axios.post(`${API}/products/ai-description`, {
                                name: formData.name,
                                category_name: formData.category_name,
                                brand: formData.brand,
                                attributes: attrsList,
                              }, { headers: { Authorization: `Bearer ${token}` } });
                              if (res.data?.description) {
                                setFormData((prev) => ({ ...prev, description: res.data.description }));
                                toast.success("Açıklama AI ile oluşturuldu");
                              }
                            } catch (e) {
                              toast.error(e?.response?.data?.detail || "AI açıklama üretilemedi");
                            } finally {
                              setAiDescLoading(false);
                            }
                          }}
                        />
                      </div>
                    </div>
                  </div>

                  <div className="space-y-6">
                    <div className="bg-white p-6 rounded-xl border shadow-sm space-y-4">
                      <h3 className="font-semibold text-gray-900 border-b pb-2 mb-4">Envanter & Kimlik</h3>
                      <div>
                        <label className="block text-xs font-bold text-gray-500 uppercase mb-1">Stok Kodu (Model Kodu)</label>
                        <div className="flex gap-2">
                          <input
                            type="text"
                            value={formData.stock_code}
                            onChange={(e) => setFormData({ ...formData, stock_code: e.target.value })}
                            className="w-full border-gray-200 border px-3 py-2 rounded-lg bg-gray-50 focus:bg-white focus:border-black outline-none transition-all font-mono text-sm uppercase"
                          />
                        </div>
                        <div className="flex gap-2 mt-2">
                          <button
                            type="button"
                            onClick={() => {
                              const randomNum = Math.floor(100000 + Math.random() * 900000);
                              setFormData({ ...formData, stock_code: `FCFW${randomNum}` });
                            }}
                            className="px-3 py-2 bg-orange-100 text-orange-800 border-none rounded-lg text-[10px] font-black tracking-widest uppercase whitespace-nowrap hover:bg-orange-200 transition-colors"
                          >
                            Üret (FCFW)
                          </button>
                          <button
                            type="button"
                            onClick={() => {
                              const randomNum = Math.floor(100000 + Math.random() * 900000);
                              setFormData({ ...formData, stock_code: `FCSS${randomNum}` });
                            }}
                            className="px-3 py-2 bg-blue-100 text-blue-800 border-none rounded-lg text-[10px] font-black tracking-widest uppercase whitespace-nowrap hover:bg-blue-200 transition-colors"
                          >
                            Üret (FCSS)
                          </button>
                        </div>
                      </div>

                      <div>
                        <label className="block text-xs font-bold text-gray-500 uppercase mb-1">Sezon Bilgisi <span className="text-red-500">*</span></label>
                        <select
                          value={formData.season || ""}
                          onChange={(e) => setFormData({ ...formData, season: e.target.value })}
                          data-testid="product-season-select"
                          className={`w-full border px-3 py-2 rounded-lg bg-gray-50 focus:bg-white outline-none transition-all text-sm ${formData.season ? "border-gray-200 focus:border-black" : "border-red-300"}`}
                        >
                          <option value="">— Sezon seçin (zorunlu) —</option>
                          <option value="İlkbahar/Sonbahar">İlkbahar/Sonbahar</option>
                          <option value="Tüm Sezonlar">Tüm Sezonlar</option>
                          <option value="Yaz">Yaz</option>
                          <option value="Kış">Kış</option>
                        </select>
                        <p className="text-[10px] text-gray-400 mt-1">Ürün raporlarındaki Sezon kolonu ve filtresi bu alandan beslenir.</p>
                      </div>

                      {/* SKU ve Tedarikçi alanları kullanıcı isteğiyle kaldırıldı */}
                      <div>
                        <label className="block text-xs font-bold text-gray-500 uppercase mb-1">Beden Önerisi (Kalıp) <span className="text-red-500">*</span></label>
                        <select
                          value={formData.size_advice || ""}
                          onChange={(e) => setFormData({ ...formData, size_advice: e.target.value })}
                          className={`w-full border px-3 py-2 rounded-lg outline-none transition-all text-sm ${formData.size_advice ? "border-gray-200 focus:border-black" : "border-red-300"}`}
                          title="Ürün sayfasında beden seçiminin yanında öneri yazısı çıkar"
                        >
                          <option value="">— Kalıp seçin (zorunlu) —</option>
                          <option value="dar">Dar / Slim Fit → bir beden büyük öner</option>
                          <option value="normal">Normal / Regular → kendi bedenini al</option>
                          <option value="bol">Bol / Oversize → bir beden küçük öner</option>
                        </select>
                      </div>
                      <div>
                        <label className="block text-xs font-bold text-gray-500 uppercase mb-1">Üretici</label>
                        <input
                          type="text"
                          value={formData.manufacturer}
                          onChange={(e) => setFormData({ ...formData, manufacturer: e.target.value })}
                          placeholder="FACETTE"
                          className="w-full border-gray-200 border px-3 py-2 rounded-lg focus:border-black outline-none transition-all"
                        />
                      </div>

                      {/* Ticimax Senkronizasyon Kimlikleri */}
                      <div className="pt-3 border-t">
                        <div className="text-xs font-bold text-gray-500 uppercase mb-2">Entegrasyon Kodları</div>
                        <div className="grid grid-cols-2 gap-2">
                          {/* Ürün ID (Ana) alanı kullanıcı isteğiyle kaldırıldı (veri durur, formda gösterilmez) */}
                          {/* Kimlik & Kodlar bölümü kaldırıldı — Ürün Kart ID ve GTİP buraya taşındı */}
                          <div>
                            <label className="block text-[10px] text-gray-400 mb-1">Ürün Kart ID</label>
                            <input
                              type="text"
                              value={(formData.ticimax_fields || {}).URUNKARTIID || ""}
                              onChange={(e) => updateDetailField("URUNKARTIID", e.target.value)}
                              placeholder="—"
                              className="w-full border-gray-200 border px-3 py-2 rounded-lg font-mono text-xs bg-gray-50 focus:bg-white focus:border-black outline-none"
                            />
                          </div>
                          <div>
                            <label className="block text-[10px] text-gray-400 mb-1">GTİP Kodu</label>
                            <input
                              type="text"
                              value={(formData.ticimax_fields || {}).GTIPKODU || ""}
                              onChange={(e) => updateDetailField("GTIPKODU", e.target.value)}
                              placeholder="—"
                              className="w-full border-gray-200 border px-3 py-2 rounded-lg font-mono text-xs bg-gray-50 focus:bg-white focus:border-black outline-none"
                            />
                          </div>
                        </div>
                        <p className="text-[10px] text-gray-400 mt-1">Ürün içe aktarımından otomatik yazılır. Varyantların ID'leri her varyantta `urun_id` olarak tutulur.</p>
                      </div>
                    </div>

                    <div className="bg-white p-6 rounded-xl border shadow-sm">
                      <h3 className="font-semibold text-gray-900 border-b pb-2 mb-4">Özellikler</h3>
                      <div className="space-y-3">
                        <label className="flex items-center gap-3 cursor-pointer group">
                          <input
                            type="checkbox"
                            className="w-4 h-4 rounded border-gray-300 text-black focus:ring-black"
                            checked={formData.is_active}
                            onChange={(e) => setFormData({ ...formData, is_active: e.target.checked })}
                          />
                          <span className="text-sm font-medium text-gray-700 group-hover:text-black transition-colors">Mağazada Aktif</span>
                        </label>
                        <label className="flex items-center gap-3 cursor-pointer group">
                          <input
                            type="checkbox"
                            className="w-4 h-4 rounded border-gray-300 text-black focus:ring-black"
                            checked={formData.is_new}
                            onChange={(e) => setFormData({ ...formData, is_new: e.target.checked })}
                          />
                          <span className="text-sm font-medium text-gray-700 group-hover:text-black transition-colors">Yeni Ürün Etiketi</span>
                        </label>
                        <label className="flex items-center gap-3 cursor-pointer group">
                          <input
                            type="checkbox"
                            className="w-4 h-4 rounded border-gray-300 text-orange-600 focus:ring-orange-500"
                            checked={formData.is_opportunity}
                            onChange={(e) => setFormData({ ...formData, is_opportunity: e.target.checked })}
                          />
                          <span className="text-sm font-medium text-gray-700 group-hover:text-black transition-colors">Fırsat Ürünü</span>
                        </label>
                      </div>
                    </div>
                  </div>
                </div>
                {/* "Kimlik & Kodlar / Temel Bilgiler / Tarihler" akordeonu kaldırıldı (kullanıcı isteği).
                    Ürün Kart ID + GTİP Kodu yukarıdaki Entegrasyon Kodları bölümüne taşındı. */}
              </TabsContent>

              {/* Pricing Tab */}
              <TabsContent value="pricing" className="space-y-6 m-0 animate-in fade-in slide-in-from-bottom-2 duration-300">
                <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                  <div className="bg-white p-6 rounded-xl border shadow-sm space-y-4">
                    <h3 className="font-semibold text-gray-900 border-b pb-2 mb-4">Fiyatlandırma</h3>
                    <div className="grid grid-cols-2 gap-4">
                      <div>
                        <label className="block text-xs font-bold text-gray-500 uppercase mb-1">Satış Fiyatı (TL) <span className="text-red-500">*</span></label>
                        <input
                          type="number"
                          value={formData.price || ""}
                          onChange={(e) => {
                            const v = parseFloat(e.target.value) || 0;
                            // #3: Satış fiyatı, üye fiyatı manuel değiştirilmediyse Üye Tipi 1'e de yazılır.
                            setFormData(prev => ({ ...prev, price: v, ...(memberPriceManual ? {} : { member_price_1: v }) }));
                          }}
                          className="w-full border-gray-200 border px-3 py-2 rounded-lg focus:border-black outline-none transition-all font-bold"
                        />
                      </div>
                      <div>
                        <label className="block text-xs font-bold text-gray-500 uppercase mb-1">İndirimli Fiyat (TL)</label>
                        <input
                          type="number"
                          value={formData.sale_price || ""}
                          onChange={(e) => setFormData({ ...formData, sale_price: parseFloat(e.target.value) || null })}
                          className="w-full border-gray-200 border px-3 py-2 rounded-lg focus:border-black outline-none transition-all font-bold text-green-600"
                        />
                        {/* Oran (%) — satış fiyatından girilen yüzde kadar indirim uygular ve
                            sonucu yukarıdaki İndirimli Fiyat alanına yazar. Değer, mevcut
                            satış/indirimli fiyattan otomatik hesaplanarak gösterilir. */}
                        <div className="mt-1.5 flex items-center gap-1.5">
                          <div className="relative">
                            <input
                              type="number"
                              min="0"
                              max="99"
                              placeholder="Oran"
                              value={(Number(formData.price) > 0 && Number(formData.sale_price) > 0)
                                ? Math.round((1 - Number(formData.sale_price) / Number(formData.price)) * 100)
                                : ""}
                              onChange={(e) => {
                                const pct = parseFloat(e.target.value);
                                const base = Number(formData.price) || 0;
                                if (!pct || pct <= 0 || base <= 0) {
                                  setFormData({ ...formData, sale_price: null });
                                  return;
                                }
                                const sp = Math.round(base * (1 - pct / 100) * 100) / 100;
                                setFormData({ ...formData, sale_price: sp });
                              }}
                              title="Satış fiyatından % indirim uygular → indirimli fiyatı otomatik yazar"
                              className="w-16 border-gray-200 border pl-2 pr-5 py-1 rounded-lg text-xs focus:border-black outline-none"
                            />
                            <span className="absolute right-2 top-1/2 -translate-y-1/2 text-xs text-gray-400 pointer-events-none">%</span>
                          </div>
                          <span className="text-[11px] text-gray-400">satış fiyatından indirim</span>
                        </div>
                      </div>
                      {/* Beden Önerisi (Kalıp) Temel sekmesine (Sezon'un altına) taşındı */}
                      <div>
                        <label className="block text-xs font-bold text-gray-500 uppercase mb-1">Alış Fiyatı (TL) <span className="text-red-500">*</span></label>
                        <input
                          type="number"
                          value={formData.purchase_price || ""}
                          onChange={(e) => setFormData({ ...formData, purchase_price: parseFloat(e.target.value) || 0 })}
                          className="w-full border-gray-200 border px-3 py-2 rounded-lg focus:border-black outline-none transition-all"
                          data-testid="product-purchase-price"
                        />
                      </div>
                      <div>
                        <label className="block text-xs font-bold text-gray-500 uppercase mb-1">Üye Tipi 1 Fiyatı (TL)</label>
                        <input
                          type="number"
                          value={formData.member_price_1 ?? ""}
                          onChange={(e) => { setMemberPriceManual(true); setFormData({ ...formData, member_price_1: e.target.value === "" ? null : parseFloat(e.target.value) }); }}
                          placeholder="Üye tipi 1'e özel fiyat"
                          className="w-full border-gray-200 border px-3 py-2 rounded-lg focus:border-black outline-none transition-all font-bold text-purple-600"
                          data-testid="product-member-price-1"
                        />
                        <p className="text-[10px] text-gray-400 mt-1">Üye Tipi 1 fiyatından çekilir. Storefront'ta üye tipi 1 grubuna özel fiyat olarak gösterilir.</p>
                      </div>
                      {/* Koleksiyon alanı kullanıcı isteğiyle kaldırıldı (sezon zorunlu alanı Temel'de) */}
                      <div>
                        <label className="block text-xs font-bold text-gray-500 uppercase mb-1">Renk (Ana)</label>
                        <input
                          type="text"
                          value={formData.color || ""}
                          onChange={(e) => setFormData({ ...formData, color: e.target.value })}
                          placeholder="ör. Siyah / Ekru / Antrasit"
                          className="w-full border-gray-200 border px-3 py-2 rounded-lg focus:border-black outline-none transition-all"
                          data-testid="product-color"
                        />
                      </div>
                      <div>
                        <label className="block text-xs font-bold text-gray-500 uppercase mb-1 text-orange-600">KDV ORANI (%)</label>
                        <input
                          type="number"
                          value={formData.vat_rate || 10}
                          onChange={(e) => setFormData({ ...formData, vat_rate: parseInt(e.target.value) || 0 })}
                          className="w-full border-orange-200 border-2 px-3 py-2 rounded-lg focus:border-orange-500 outline-none transition-all font-bold text-orange-700"
                          placeholder="10"
                        />
                      </div>
                    </div>
                  </div>

                  {/* Trendyol Fiyatlandırma sağ kolona taşındı — sol Fiyatlandırma kartıyla yan yana */}
                  <div className="bg-orange-50 p-6 rounded-xl border border-orange-200 shadow-sm h-fit">
                    <h3 className="font-semibold text-lg text-orange-900 mb-5 flex items-center gap-2">
                      <span className="w-8 h-8 rounded-full bg-orange-500 text-white flex items-center justify-center text-sm font-bold">2</span>
                      Trendyol Fiyatlandırma Ayarları
                    </h3>
                    <div className="space-y-4">
                      <div className="bg-white p-4 rounded-lg border border-orange-100 flex items-start gap-3">
                        <input
                          type="checkbox"
                          id="use_default_markup"
                          checked={formData.use_default_markup}
                          onChange={(e) => setFormData({ ...formData, use_default_markup: e.target.checked })}
                          className="mt-1 rounded border-gray-300 text-orange-600 focus:ring-orange-500"
                        />
                        <label htmlFor="use_default_markup" className="cursor-pointer">
                          <span className="block text-sm font-bold text-orange-900 leading-tight">Global Kâr Oranını Kullan</span>
                          <span className="block text-xs text-orange-600 mt-0.5">Ayarlar sayfasındaki global oranı (%{globalTrendyolMarkup}) baz alır.</span>
                        </label>
                      </div>

                      {!formData.use_default_markup && (
                        <div className="bg-white p-4 rounded-lg border border-orange-100 animate-in slide-in-from-top-2">
                          <label className="block text-xs font-bold text-orange-900 uppercase mb-2">Bu Ürüne Özel Trendyol Fark Oranı (%)</label>
                          <input
                            type="number"
                            value={formData.markup_rate || ""}
                            onChange={(e) => setFormData({ ...formData, markup_rate: parseFloat(e.target.value) || 0 })}
                            placeholder="Örn: 25"
                            className="w-full border-orange-200 border-2 px-4 py-3 rounded-xl focus:border-orange-500 outline-none transition-all text-xl font-bold text-orange-700"
                          />
                        </div>
                      )}

                      <div className="bg-white p-6 rounded-lg border border-orange-100 flex flex-col justify-center">
                        <p className="text-xs font-bold text-gray-500 uppercase mb-4 tracking-widest text-center">Tahmini Trendyol Satış Fiyatı</p>
                        <div className="text-center">
                          <span className="text-4xl font-black text-orange-600">
                            {(((formData.member_price_1 || formData.price) || 0) * (1 + (formData.use_default_markup ? globalTrendyolMarkup : (formData.markup_rate || 0)) / 100)).toFixed(2)}
                          </span>
                          <span className="text-xl font-bold text-orange-400 ml-1">TL</span>
                        </div>
                        <p className="text-[10px] text-gray-400 text-center mt-4">
                          * KDV ve kargo masrafları fiyata dahildir. {formData.use_default_markup ? 'Global' : 'Özel'} markup uygulanmıştır.
                        </p>
                      </div>
                    </div>
                  </div>
                </div>
                {/* "Fiyatlandırma / Üye Tipi Fiyatları" akordeonu kullanıcı isteğiyle kaldırıldı */}
              </TabsContent>

              {/* Attributes Tab */}
              <TabsContent value="attributes" className="space-y-6 m-0 animate-in fade-in slide-in-from-bottom-2 duration-300">
                {(() => {
                  const selectedCat = categories.find(c => c.name === formData.category_name || c.id === formData.category_name);
                  const attrMappings = selectedCat?.attribute_mappings || [];
                  // Türkçe-duyarsız normalize (İ/ı/ş/ğ/ü/ö/ç) + tam-eşleşme gizleme.
                  const _attrNorm = (s) => (s || "").toLowerCase()
                    .replace(/ı/g, "i").replace(/i̇/g, "i").replace(/ş/g, "s").replace(/ğ/g, "g")
                    .replace(/ü/g, "u").replace(/ö/g, "o").replace(/ç/g, "c").trim();
                  // Formda GÖSTERİLMEYECEK özellikler (varyant eksenleri + kullanıcı kaldırma listesi).
                  // NOT: yalnız form görünümünden gizlenir — veri korunur, pazaryeri push'u etkilenmez.
                  // "Alt-Üst Takım" HARİÇ tutuldu (listede kalır).
                  const hiddenAttrNames = [
                    "beden", "renk", "web color", "yaka",
                    "alt siluet", "ust siluet", "silüet", "kesim", "ozellik", "stil",
                    "urun icerik bilgisi", "kumas", "yikama talimati", "materyal analiz testi",
                    "kimyasal analiz testi", "ürün tipi", "ürün detayı",
                  ].map(_attrNorm);
                  const _isHiddenAttr = (nm) => hiddenAttrNames.includes(_attrNorm(nm));

                  const baseList = globalAttributes
                    .filter(a => !_isHiddenAttr(a.name))
                    .filter(a => a.name.toLowerCase().includes(attributeSearchTerm.toLowerCase()));

                  // #7-#12: formData.attributes'ta olup kütüphanede olmayan (varsayılan/seeded)
                  // özellikleri de listeye ekle ki Özellikler sekmesinde dolu görünsünler.
                  {
                    const _seedNames = new Set(baseList.map(a => (a.name || "").toLowerCase()));
                    Object.keys(formData.attributes || {}).forEach(nm => {
                      const low = (nm || "").toLowerCase();
                      if (!nm || _seedNames.has(low) || _isHiddenAttr(nm)) return;
                      if (!low.includes(attributeSearchTerm.toLowerCase())) return;
                      baseList.push({ id: `seed-${nm}`, name: nm, values: [] });
                      _seedNames.add(low);
                    });
                  }

                  // Determine required attrs from Trendyol mapping
                  const getIsRequired = (attr) => {
                    const mapping = attrMappings.find(m => m.local_attr?.toLowerCase() === attr.name.toLowerCase());
                    let tyAttr = null;
                    if (mapping?.trendyol_attr_id) {
                      tyAttr = trendyolAttributesList.find(ta => (ta.attribute?.id || ta.id) === mapping.trendyol_attr_id);
                    }
                    if (!tyAttr) {
                      tyAttr = trendyolAttributesList.find(ta => {
                        const taName = (ta.attribute?.name || ta.name || "").toLowerCase().trim();
                        return taName === (attr.name || "").toLowerCase().trim();
                      });
                    }
                    return !!tyAttr?.required;
                  };

                  // Auto-sync: when Trendyol attribute changes, if value matches an allowed value
                  // in that attribute's value library, auto-apply to HB + Temu maps (only if those
                  // are currently empty for that attr, to respect manual overrides).
                  // Çift yönlü çapraz doldurma: HERHANGİ bir pazaryerinde özellik seçilince,
                  // DİĞER pazaryerlerinin AYNI isimli alanı BOŞSA aynı değer otomatik yazılır.
                  // Manuel her zaman kazanır (dolu alan ASLA ezilmez). Tek yönlü Trendyol→X yerine simetrik.
                  const MP_ATTR_KEY = { trendyol: "attributes", hepsiburada: "hepsiburada_attributes", temu: "temu_attributes" };
                  const setMarketplaceAttr = (srcMp, attr, val) => {
                    const next = { ...formData };
                    const srcKey = MP_ATTR_KEY[srcMp] || "attributes";
                    next[srcKey] = { ...(formData[srcKey] || {}), [attr.name]: val };
                    const valuesLower = (attr.values || []).map(v => (v || "").toLowerCase());
                    const valOk = !val || !attr.values?.length || valuesLower.includes((val || "").toLowerCase());
                    if (valOk) {
                      for (const mp of ["trendyol", "hepsiburada", "temu"]) {
                        if (mp === srcMp) continue;
                        const k = MP_ATTR_KEY[mp];
                        const cur = { ...(next[k] || formData[k] || {}) };
                        if (!cur[attr.name]) { cur[attr.name] = val; next[k] = cur; }
                      }
                    }
                    setFormData(next);
                  };

                  // Renk kardeşlerine özellik kopyala: tekten bölünen renk kartlarının
                  // özelliklerini (Kol Tipi, Yaka Stili, Kumaş... + HB/Temu) birebir eşitler.
                  // RENK/BEDEN'e dokunmaz. Önce bu kart KAYDEDİLMİŞ olmalı (DB'den kopyalanır).
                  const copyAttrsToSiblings = async () => {
                    if (!formData.id) { alert("Önce ürünü kaydet."); return; }
                    if (!window.confirm("Bu rengin özellikleri (Kol Tipi, Yaka Stili, Kumaş vb. — RENK/BEDEN HARİÇ) aynı modelin diğer renk kartlarına kopyalanacak.\n\nÖnce bu kartı KAYDETTİĞİNDEN emin ol (DB'den kopyalanır). Devam edilsin mi?")) return;
                    try {
                      const token = localStorage.getItem('token');
                      const res = await axios.post(`${API}/products/${formData.id}/copy-attributes-to-siblings`, {}, { headers: { Authorization: `Bearer ${token}` } });
                      const u = res.data?.updated ?? 0;
                      alert(u > 0 ? `${u} renk kardeşine kopyalandı. Kartları açıp doğrula.` : (res.data?.detail || "Renk kardeşi bulunamadı."));
                    } catch (e) {
                      alert("Kopyalama başarısız: " + (e?.response?.data?.detail || e.message));
                    }
                  };

                  // 🎯 Ortak SABİT varsayılanlar — HER pazaryeri bölümünde DOLU görünmeli.
                  const FIXED_DEFAULT_ATTRS = {
                    "Menşei": "TR", "Cinsiyet": "Kadın", "Yaş Grubu": "Yetişkin",
                    "Ortam": "Casual/Günlük", "Koleksiyon": "Casual/Günlük",
                    "Ek Özellik": "Yok", "Kutu Durumu": "Kutu Yok",
                    "Persona": "Fashion Forward", "Performans": "Cool & Comfort",
                    "Sürdürülebilirlik Detayı": "Hayır",
                  };
                  // 🏭 GPSR üretici/ithalatçı — backend facette_defaults.company_field_for_attr AYNASI.
                  // TEK otorite backend'dir (push'ta facette_company_value uygular); bu yalnız KARTTA
                  // dolu göstermek içindir — formData'ya YAZMAZ, push'a karışmaz. Üretici/İthalatçı
                  // Adı→firma, ...Mail→e-posta, ...Adres→adres.
                  const FACETTE_COMPANY = {
                    company_name: "FACETTE DIŞ TİCARET A.Ş.",
                    email: "info@facette.com.tr",
                    address: "İkitelli O.S.B. İmsan San. Sit. D BLOK NO:3",
                  };
                  const _companyValFor = (name) => {
                    const nm = _attrNorm(name);
                    if (!/uretici|ithalatc|imalatc/.test(nm)) return "";
                    if (/mail|posta|email/.test(nm)) return FACETTE_COMPANY.email;
                    if (/adres/.test(nm)) return FACETTE_COMPANY.address;
                    if (/\bad[i]?\b|ism|unvan|firma/.test(nm) || nm === "uretici" || nm === "ithalatci")
                      return FACETTE_COMPANY.company_name;
                    return "";
                  };

                  const renderSection = (marketplace, title, accent, logo) => {
                    const mapKey = marketplace === 'trendyol' ? 'attributes'
                                 : marketplace === 'hepsiburada' ? 'hepsiburada_attributes'
                                 : 'temu_attributes';
                    const valuesMap = formData[mapKey] || {};

                    // HB bolumu HB'nin kendi kategori ozelliklerinden beslenir
                    // (Beden/Renk/Cinsiyet + HB enum degerleri). Diger pazaryerleri global listeden.
                    // HB bazı kategorilerde aynı özelliği iki kez döndürür (örn. "Renk" hem
                    // varyant hem normal attribute) → modalde ÇİFT alan çıkıyordu. Normalize
                    // ada göre tekilleştir: değerli/zorunlu olanı tut, diğerini at.
                    const _hbNorm = (s) => (s || "").toLocaleLowerCase("tr").replace(/[\s\-_/().]/g, "").trim();
                    const _hbMap = new Map();
                    (hepsiburadaAttributesList || []).forEach(a => {
                      const nk = _hbNorm(a.name);
                      if (!nk) return;
                      const cand = {
                        id: a.id,
                        name: a.name,
                        values: (a.attributeValues || []).map(v => v.name),
                        required: !!a.required,
                        allowCustom: !!a.allowCustom,
                      };
                      const prev = _hbMap.get(nk);
                      if (!prev) { _hbMap.set(nk, cand); return; }
                      const score = (x) => (x.values.length > 0 ? 2 : 0) + (x.required ? 1 : 0);
                      if (score(cand) > score(prev)) _hbMap.set(nk, cand);
                    });
                    const hbSource = [..._hbMap.values()];
                    // HB şeması boş mu? (kategori eşli değil/seçilmedi VEYA canlı çekim sürüyor).
                    // Boşsa aşağıda Dolu/Zorunlu/Diğer grupları RENDER EDİLMEZ → "Dolu Özellikler (9)"
                    // yanıltıcı flicker'ı engellenir; yerine yükleniyor / eşli değil kutusu gösterilir.
                    const hbSchemaMissing = marketplace === 'hepsiburada' && hbSource.length === 0;

                    // TRENDYOL: kendi kategori özelliklerinin TÜM izin verilen değerlerini
                    // (attributeValues) global kütüphaneyle BİRLEŞTİR ve global'de olmayan
                    // Trendyol özelliklerini de ekle → forma eksiksiz Trendyol listesi gelir.
                    const tyByName = {};
                    (trendyolAttributesList || []).forEach(a => {
                      const nm = (a.attribute?.name || a.name || "").trim();
                      if (!nm) return;
                      tyByName[nm.toLowerCase()] = {
                        id: a.attribute?.id || a.id,
                        name: nm,
                        values: (a.attributeValues || [])
                          .map(v => (typeof v === "string" ? v : (v?.name || "")))
                          .filter(Boolean),
                      };
                    });
                    // KULLANICI KARARI: Trendyol bölümünde 159 özelliklik global kütüphane DEĞİL,
                    // YALNIZ Trendyol API'sinin bu kategori için döndürdüğü özellikler listelenir.
                    // (Ürüne zaten girilmiş dolu değerler şemada olmasa da görünür kalır — veri kaybolmaz.)
                    const tySchemaLoaded = Object.keys(tyByName).length > 0;
                    const tyOnly = (() => {
                      if (!tySchemaLoaded) {
                        // Şema henüz çekilmediyse (kategori eşli değil) yalnız DOLU özellikler gösterilir
                        return baseList.filter(a => (formData.attributes || {})[a.name]);
                      }
                      const out = [];
                      const seen = new Set();
                      Object.values(tyByName).forEach(ty => {
                        // Gizli özellikler (Kimyasal/Materyal Analiz Testi, Ürün Tipi/Detayı,
                        // Silüet...) TY şemasında olsa da formda LİSTELENMEZ (kullanıcı isteği).
                        if (_isHiddenAttr(ty.name)) return;
                        const g = baseList.find(a => (a.name || "").toLowerCase() === ty.name.toLowerCase());
                        const values = g ? Array.from(new Set([...(g.values || []), ...ty.values])) : ty.values;
                        out.push({ id: g?.id || `ty-${ty.id}`, name: ty.name, values });
                        seen.add(ty.name.toLowerCase());
                      });
                      // TY şemasında olmayan ama üründe DOLU olan özellikler görünür kalsın
                      Object.keys(formData.attributes || {}).forEach(nm => {
                        const low = (nm || "").toLowerCase();
                        if (!nm || seen.has(low) || _isHiddenAttr(nm)) return;
                        if (!(formData.attributes || {})[nm]) return;
                        out.push({ id: `cur-${nm}`, name: nm, values: [] });
                        seen.add(low);
                      });
                      return out.filter(a => (a.name || "").toLowerCase().includes(attributeSearchTerm.toLowerCase()));
                    })();

                    const sourceList = marketplace === 'hepsiburada'
                      ? hbSource.filter(a => (a.name || '').toLowerCase().includes(attributeSearchTerm.toLowerCase()))
                      : marketplace === 'trendyol'
                      ? tyOnly
                      : baseList;

                    // 🎯 Değer çözümü: önce pazaryerine-özel harita, yoksa NÖTR formData.attributes,
                    // yoksa sabit varsayılan, yoksa GPSR üretici/ithalatçı sabiti → DOLU görünür.
                    const _effVal = (name) =>
                      valuesMap[name] || (formData.attributes || {})[name] || FIXED_DEFAULT_ATTRS[name]
                      || _companyValFor(name) || "";
                    // Sabit varsayılanları bu bölümün listesinde yoksa DOLU satır olarak ekle
                    // (özellikle HB: kategori seçilmeden liste boş kalıyordu).
                    const _present = new Set((sourceList || []).map(a => (a.name || "").toLocaleLowerCase("tr")));
                    const fixedRows = Object.keys(FIXED_DEFAULT_ATTRS)
                      .filter(nm => !_present.has(nm.toLocaleLowerCase("tr"))
                                 && nm.toLowerCase().includes(attributeSearchTerm.toLowerCase()))
                      .map(nm => ({ id: `fixed-${marketplace}-${nm}`, name: nm, values: [], allowCustom: true }));
                    const sourceListAll = [...(sourceList || []), ...fixedRows];

                    // Mükerrer fix: Teknik Detay panelinde gösterilen etiketler (Materyal, Kalıp, Kumaş...)
                    // pazaryeri özellik bölümlerinde TEKRAR listelenmez — tek kaynak. (HB kendi şema isimlerini
                    // kullandığı için pratikte Trendyol/Temu bölümlerini etkiler.) Değer yine kaydedilir.
                    const _techNames = new Set(
                      Object.values(technicalDetails || {})
                        .map(t => (t?.label || "").toLocaleLowerCase('tr').trim())
                        .filter(Boolean)
                    );
                    const processed = sourceListAll.map(attr => {
                      const isReq = marketplace === 'trendyol' ? getIsRequired(attr)
                                  : marketplace === 'hepsiburada' ? !!attr.required
                                  : false;
                      const hasVal = !!_effVal(attr.name);
                      return { attr, isRequired: isReq, hasValue: hasVal };
                    })
                    // Beden ürün kartından gizlenir: pazaryeri varyant (beden) alanından eşleştiriliyor.
                    .filter(x => (x.attr.name || '').toLocaleLowerCase('tr').trim() !== 'beden')
                    // Mükerrer dedup YALNIZ opsiyonel alanlara: ZORUNLU pazaryeri alanı Teknik Detay
                    // etiketiyle çakışsa bile GİZLENMEZ (gizli+boş = sessiz red riski). Değer tek
                    // kaynaktan (_effVal) dolar; satır görünür kalır → kullanıcı görüp doğrular.
                    // İSTİSNA (Kadir): 'Kalıp' Teknik Detay'da olsa/opsiyonel olsa bile pazaryeri
                    // özellik bölümünde HER ZAMAN görünür (kullanıcı buradan da seçip aktarabilsin).
                    .filter(x => {
                      const _nm = (x.attr.name || '').toLocaleLowerCase('tr').trim();
                      return x.isRequired || _nm === 'kalıp' || !_techNames.has(_nm);
                    });
                    const filledAttrs = processed.filter(a => a.hasValue).sort((a, b) => a.attr.name.localeCompare(b.attr.name));
                    const requiredEmpty = processed.filter(a => a.isRequired && !a.hasValue).sort((a, b) => a.attr.name.localeCompare(b.attr.name));
                    const otherEmpty = processed.filter(a => !a.isRequired && !a.hasValue).sort((a, b) => a.attr.name.localeCompare(b.attr.name));
                    const isSearching = attributeSearchTerm.length > 0;

                    const handleChange = (attr, val) => {
                      setMarketplaceAttr(marketplace, attr, val);
                    };

                    const renderAttr = ({ attr, isRequired }) => (
                      <SearchableAttribute
                        key={`${marketplace}-${attr.id}`}
                        attr={attr}
                        value={_effVal(attr.name)}
                        isRequired={isRequired}
                        channelLabel={logo}
                        allowCustom={marketplace === 'hepsiburada' ? !!attr.allowCustom : marketplace === 'temu'}
                        onChange={(val) => handleChange(attr, val)}
                      />
                    );

                    return (
                      <div
                        data-testid={`attributes-section-${marketplace}`}
                        className={`bg-white p-8 rounded-xl border-2 shadow-sm`}
                        style={{ borderColor: accent.border }}
                      >
                        <div className="flex justify-between items-center mb-6">
                          <div className="flex items-center gap-3 flex-1 mr-4">
                            <span
                              className="inline-flex items-center justify-center text-white text-xs font-black px-3 py-1.5 rounded-md tracking-wider"
                              style={{ background: accent.bg }}
                            >
                              {logo}
                            </span>
                            <div>
                              <h3 className="font-bold text-xl mb-0" style={{ color: accent.text }}>{title}</h3>
                              <p className="text-xs text-gray-500 leading-relaxed max-w-2xl">
                                {marketplace === 'trendyol'
                                  ? "Trendyol için ürün özellikleri. Seçilen değer HB ve Temu'da da otomatik set edilir (boş ise)."
                                  : `${marketplace === 'hepsiburada' ? 'Hepsiburada' : 'Temu'} için ürün özellikleri. Gerekirse Trendyol'dan bağımsız düzenleyin.`}
                              </p>
                            </div>
                          </div>
                          {marketplace === 'trendyol' && (
                            <div className="flex items-center gap-3">
                              <div className="relative">
                                <Search className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" size={14} />
                                <input
                                  type="text"
                                  placeholder="Özellik ara..."
                                  className="pl-9 pr-4 py-2 bg-gray-50 border border-gray-200 rounded-lg text-xs font-bold outline-none focus:border-orange-500 focus:bg-white transition-all w-48"
                                  value={attributeSearchTerm}
                                  onChange={(e) => setAttributeSearchTerm(e.target.value)}
                                />
                              </div>
                            </div>
                          )}
                        </div>

                        {hbSchemaMissing && (
                          hbAttrsLoading ? (
                            <div className="py-16 text-center bg-gray-50 rounded-2xl border-2 border-dashed border-gray-200">
                              <div className="animate-pulse text-sm font-bold uppercase tracking-widest text-gray-400">Hepsiburada kategori özellikleri yükleniyor…</div>
                              <p className="text-xs mt-2 text-gray-400">İlk açılışta canlı çekiliyor; birkaç saniye sürebilir.</p>
                            </div>
                          ) : (
                            <div className="py-12 text-center bg-amber-50 rounded-2xl border-2 border-dashed border-amber-200">
                              <div className="text-sm font-bold text-amber-700">Bu kategori Hepsiburada'ya eşli değil (ya da kategori seçilmedi).</div>
                              <p className="text-xs mt-2 text-amber-600">Kategori Eşleştirme'den HB'ye eşle → Renk, Beden, Kumaş ve tüm HB özellikleri burada görünür.</p>
                            </div>
                          )
                        )}

                        {filledAttrs.length > 0 && !hbSchemaMissing && (
                          <div className="mb-6">
                            <div className="flex items-center gap-2 mb-4">
                              <div className="w-3 h-3 bg-green-500 rounded-full"></div>
                              <h4 className="text-sm font-bold text-green-700">Dolu Özellikler ({filledAttrs.length})</h4>
                            </div>
                            <div className="grid grid-cols-1 md:grid-cols-2 gap-x-8 gap-y-5">
                              {filledAttrs.map(renderAttr)}
                            </div>
                          </div>
                        )}

                        {requiredEmpty.length > 0 && !hbSchemaMissing && (
                          <div className="mb-6">
                            <div className="flex items-center gap-2 mb-4">
                              <div className="w-3 h-3 bg-red-500 rounded-full animate-pulse"></div>
                              <h4 className="text-sm font-bold text-red-700">Zorunlu - Boş ({requiredEmpty.length})</h4>
                            </div>
                            <div className="grid grid-cols-1 md:grid-cols-2 gap-x-8 gap-y-5">
                              {requiredEmpty.map(renderAttr)}
                            </div>
                          </div>
                        )}

                        {otherEmpty.length > 0 && !hbSchemaMissing && (isSearching || showAllAttributes || marketplace !== 'trendyol') && (
                          <div className="mb-6">
                            <div className="flex items-center gap-2 mb-4">
                              <div className="w-3 h-3 bg-gray-300 rounded-full"></div>
                              <h4 className="text-sm font-bold text-gray-500">Diğer Özellikler ({otherEmpty.length})</h4>
                            </div>
                            <div className="grid grid-cols-1 md:grid-cols-2 gap-x-8 gap-y-5">
                              {otherEmpty.map(renderAttr)}
                            </div>
                          </div>
                        )}

                        {otherEmpty.length > 0 && !isSearching && marketplace === 'trendyol' && (
                          <div className="text-center pt-4 border-t border-dashed border-gray-200">
                            <button
                              type="button"
                              onClick={() => setShowAllAttributes(!showAllAttributes)}
                              className="px-6 py-2 text-sm font-bold text-orange-600 bg-orange-50 hover:bg-orange-100 rounded-lg transition-colors"
                              data-testid="toggle-all-attributes-btn"
                            >
                              {showAllAttributes ? `Boş Özellikleri Gizle (${otherEmpty.length})` : `Tüm Özellikleri Göster (+${otherEmpty.length} boş)`}
                            </button>
                          </div>
                        )}

                        {globalAttributes.length === 0 && (
                          <div className="col-span-full py-20 text-center text-gray-400 bg-gray-50 rounded-2xl border-2 border-dashed border-gray-200">
                            <Layers className="mx-auto mb-4 opacity-10" size={64} />
                            <p className="text-sm font-bold uppercase tracking-widest">Henüz özellik kütüphanesi boş.</p>
                          </div>
                        )}
                      </div>
                    );
                  };

                  return (
                    <div className="space-y-6">
                      {formData.id && (
                        <div className="flex items-center justify-between gap-3 bg-amber-50 border border-amber-200 rounded-xl px-5 py-3">
                          <div className="text-xs text-amber-800 leading-relaxed">
                            <span className="font-bold">Renk kardeşleri:</span> Bu kartın özelliklerini (Kol Tipi, Yaka Stili, Kumaş… — renk/beden hariç) aynı modelin diğer renk kartlarına kopyala. Önce bu kartı kaydet.
                          </div>
                          <button
                            type="button"
                            onClick={copyAttrsToSiblings}
                            className="shrink-0 px-4 py-2 text-xs font-bold text-white bg-amber-600 hover:bg-amber-700 rounded-lg transition-colors"
                          >
                            Renk kardeşlerine kopyala
                          </button>
                        </div>
                      )}
                      {/* Teknik Detay paneli KALDIRILDI (Kadir): aşağıdaki "Trendyol/HB/Temu
                          için Özellikler" bölümleriyle mükerrer oluyordu. technicalDetails state'i
                          ve kaydı arka planda korunur; yalnızca bu mükerrer düzenleme paneli gizlendi. */}
                      {renderSection('trendyol', 'Trendyol için Özellikler', { border: '#e5e5e5', bg: '#1a1a1a', text: '#1a1a1a' }, 'TRENDYOL')}
                      {renderSection('hepsiburada', 'Hepsiburada için Özellikler', { border: '#e5e5e5', bg: '#1a1a1a', text: '#1a1a1a' }, 'HEPSIBURADA')}
                      {renderSection('temu', 'Temu için Özellikler', { border: '#e5e5e5', bg: '#1a1a1a', text: '#1a1a1a' }, 'TEMU')}
                    </div>
                  );
                })()}
              </TabsContent>

              {/* Size Table Tab */}
              <TabsContent value="sizetable" className="space-y-6 m-0 animate-in fade-in slide-in-from-bottom-2 duration-300">
                <SizeTablePanel
                  productId={editingProduct?.id}
                  productName={formData.name || editingProduct?.name || ""}
                  variants={formData.variants}
                  onToast={(m, t) => (t === 'err' ? toast.error(m) : toast.success(m))}
                />
              </TabsContent>

              {/* Variants Tab */}
              <TabsContent value="variants" className="space-y-6 m-0 animate-in fade-in slide-in-from-bottom-2 duration-300">
                <div className="bg-white p-6 rounded-xl border shadow-sm space-y-6">
                  <div className="flex justify-between items-center border-b pb-4">
                    <h3 className="font-semibold text-gray-900 flex items-center gap-2">
                      <Layers size={20} className="text-orange-500" />
                      Varyant Yönetimi
                    </h3>
                    <div className="relative">
                        <Search className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" size={14} />
                        <input 
                          type="text"
                          placeholder="Varyant ara (beden/renk/kod)..."
                          className="pl-9 pr-4 py-2 bg-gray-50 border border-gray-200 rounded-lg text-xs font-bold outline-none focus:border-orange-500 focus:bg-white transition-all w-64"
                          value={variantSearchTerm}
                          onChange={(e) => setVariantSearchTerm(e.target.value)}
                        />
                    </div>
                  </div>

                  {/* Existing Variants Table */}
                  {formData.variants?.length > 0 && (
                    <div className="border rounded-xl overflow-hidden shadow-sm">
                      <table className="w-full text-sm">
                        <thead className="bg-gray-50 border-b">
                          <tr>
                            <th className="text-left px-4 py-3 font-bold text-gray-600">Beden / Renk</th>
                            <th className="text-left px-4 py-3 font-bold text-gray-600">Stok Kodu</th>
                            <th className="text-left px-4 py-3 font-bold text-gray-600">Barkod</th>
                            <th className="text-center px-4 py-3 font-bold text-gray-600">Stok</th>
                            <th className="text-center px-4 py-3 font-bold text-gray-600 w-20">Sil</th>
                          </tr>
                        </thead>
                        <tbody className="divide-y divide-gray-100">
                          {formData.variants
                            ?.map((v, originalIdx) => ({ ...v, originalIdx }))
                            ?.filter(v => 
                              !variantSearchTerm || 
                              v.size?.toLowerCase().includes(variantSearchTerm.toLowerCase()) ||
                              v.color?.toLowerCase().includes(variantSearchTerm.toLowerCase()) ||
                              v.stock_code?.toLowerCase().includes(variantSearchTerm.toLowerCase()) ||
                              v.barcode?.toLowerCase().includes(variantSearchTerm.toLowerCase())
                            )
                            ?.map((v) => (
                            <tr key={v.id || v.originalIdx} className="hover:bg-orange-50/30 transition-colors">
                              <td className="px-4 py-3">
                                <div className="flex flex-col">
                                  <span className="font-bold text-gray-900 text-lg">{v.size || "-"}</span>
                                  {v.color && <span className="text-xs text-gray-500">{v.color}</span>}
                                </div>
                              </td>
                              <td className="px-4 py-3">
                                <input
                                  type="text"
                                  value={v.stock_code || ""}
                                  onChange={(e) => {
                                    const updated = [...formData.variants];
                                    updated[v.originalIdx].stock_code = e.target.value;
                                    setFormData({...formData, variants: updated});
                                  }}
                                  className="w-full border-gray-200 border px-2 py-1.5 rounded bg-gray-50 focus:bg-white focus:border-black outline-none font-mono text-xs"
                                  placeholder="Stok kodu..."
                                />
                              </td>
                              <td className="px-4 py-3">
                                <input
                                  type="text"
                                  value={v.barcode || ""}
                                  onChange={(e) => {
                                    const updated = [...formData.variants];
                                    updated[v.originalIdx].barcode = e.target.value;
                                    setFormData({...formData, variants: updated});
                                  }}
                                  className="w-full border-gray-200 border px-2 py-1.5 rounded bg-gray-50 focus:bg-white focus:border-black outline-none font-mono text-xs"
                                  placeholder="Barkod girin..."
                                />
                              </td>
                              <td className="px-4 py-3 text-center">
                                <input
                                  type="number"
                                  value={v.stock || ""}
                                  onChange={(e) => {
                                    const updated = [...formData.variants];
                                    updated[v.originalIdx].stock = parseInt(e.target.value) || 0;
                                    setFormData({...formData, variants: updated});
                                  }}
                                  className={`w-20 border-gray-200 border px-2 py-1.5 rounded text-center font-bold ${v.stock < 5 ? 'text-red-600 bg-red-50 border-red-200' : 'text-gray-900'}`}
                                />
                              </td>
                              <td className="px-4 py-3 text-center">
                                <button
                                  type="button"
                                  onClick={() => {
                                    setFormData({ ...formData, variants: formData.variants.filter((_, i) => i !== v.originalIdx) });
                                  }}
                                  className="text-red-400 hover:text-red-600 p-2 hover:bg-red-50 rounded-full transition-colors"
                                >
                                  <Trash2 size={18} />
                                </button>
                              </td>
                            </tr>
                          ))}
                        </tbody>
                        <tfoot className="bg-gray-50 border-t">
                          <tr>
                            <td colSpan={2} className="px-4 py-3 text-sm font-bold text-gray-500 text-right">Toplam Stok:</td>
                            <td className="px-4 py-3 text-center text-lg font-black text-black">
                              {formData.variants.reduce((sum, v) => sum + (v.stock || 0), 0)}
                            </td>
                            <td></td>
                          </tr>
                        </tfoot>
                      </table>
                    </div>
                  )}

                  {/* Add New Variant Section */}
                  <div className="bg-orange-50 rounded-xl p-6 border border-orange-100">
                    <h4 className="text-sm font-bold text-orange-900 mb-4 uppercase tracking-wider">Hızlı Varyant Ekle</h4>
                    {/* #6: Bedenleri ve renkleri buton buton seç → seçilen kombinasyonlardan kartları tek seferde üret */}
                    <div className="mb-5 bg-white rounded-lg border border-orange-200 p-4">
                      <div className="mb-3">
                        <span className="block text-xs font-bold text-orange-700 mb-2 uppercase">Bedenler (çoklu seç)</span>
                        <div className="flex flex-wrap gap-2">
                          {globalSizes.map(s => {
                            const on = multiSizes.includes(s.value);
                            return (
                              <button key={s.id} type="button"
                                onClick={() => setMultiSizes(p => on ? p.filter(x => x !== s.value) : [...p, s.value])}
                                className={`px-3 py-1.5 rounded-lg text-sm font-bold border-2 transition-all ${on ? 'bg-orange-600 text-white border-orange-600' : 'bg-white text-gray-700 border-gray-200 hover:border-orange-300'}`}>
                                {s.value}
                              </button>
                            );
                          })}
                          {globalSizes.length === 0 && <span className="text-xs text-gray-400 italic">Beden tanımlı değil</span>}
                        </div>
                      </div>
                      <div className="mb-3">
                        <span className="block text-xs font-bold text-orange-700 mb-2 uppercase">Renkler (çoklu seç — boş bırakılırsa renksiz)</span>
                        <div className="flex flex-wrap gap-2">
                          {globalColors.map(c => {
                            const on = multiColors.includes(c.value);
                            return (
                              <button key={c.id} type="button"
                                onClick={() => setMultiColors(p => on ? p.filter(x => x !== c.value) : [...p, c.value])}
                                className={`px-3 py-1.5 rounded-lg text-sm font-bold border-2 transition-all ${on ? 'bg-orange-600 text-white border-orange-600' : 'bg-white text-gray-700 border-gray-200 hover:border-orange-300'}`}>
                                {c.value}
                              </button>
                            );
                          })}
                          {globalColors.length === 0 && <span className="text-xs text-gray-400 italic">Renk tanımlı değil</span>}
                        </div>
                      </div>
                      <button type="button"
                        onClick={() => {
                          if (!multiSizes.length) { toast.error("En az bir beden seçin"); return; }
                          const colors = multiColors.length ? multiColors : [""];
                          const existing = new Set((formData.variants || []).map(v => `${v.size}|${v.color || ""}`));
                          const adds = [];
                          multiSizes.forEach(sz => colors.forEach(cl => {
                            const key = `${sz}|${cl}`;
                            if (existing.has(key)) return;
                            existing.add(key);
                            adds.push({ id: `var-${Date.now()}-${sz}-${cl}`.replace(/\s+/g, ''), size: sz, color: cl, stock: 0, barcode: "", stock_code: formData.stock_code || "" });
                          }));
                          if (!adds.length) { toast.error("Seçili kombinasyonlar zaten ekli"); return; }
                          setFormData(prev => ({ ...prev, variants: [...(prev.variants || []), ...adds] }));
                          setMultiSizes([]); setMultiColors([]);
                          toast.success(`${adds.length} varyant oluşturuldu`);
                        }}
                        className="w-full bg-orange-600 text-white font-bold py-2.5 rounded-lg hover:bg-orange-700 shadow-md shadow-orange-200 transition-all">
                        Seçili Kombinasyonları Oluştur ({multiSizes.length || 0} beden × {multiColors.length || 1} renk)
                      </button>
                      <p className="text-[10px] text-gray-400 mt-2 text-center">Stok ve barkodları oluşan kartlardan düzenleyebilirsin. Tek tek eklemek için aşağıyı kullan.</p>
                    </div>
                    <div className="grid grid-cols-1 md:grid-cols-3 lg:grid-cols-6 gap-4 items-end">
                      <div>
                        <label className="block text-xs font-bold text-orange-700 mb-1 uppercase">Beden *</label>
                        <div className="relative size-dropdown-container">
                          <input 
                            type="text" 
                            placeholder="Beden ara veya seç..." 
                            className="w-full border-orange-200 border-2 px-3 py-2 rounded-lg focus:border-orange-500 outline-none text-sm font-bold bg-white"
                            value={sizeSearchTerm || formData.newVariant?.size || ""}
                            onChange={(e) => { setSizeSearchTerm(e.target.value); setSizeSearchOpen(true); }}
                            onFocus={() => setSizeSearchOpen(true)}
                          />
                          {sizeSearchOpen && (
                            <div className="absolute z-[9999] w-full mt-1 bg-white border-2 border-orange-300 rounded-xl shadow-2xl overscroll-contain" style={{maxHeight: '240px', overflowY: 'auto'}} onWheel={(e) => e.stopPropagation()}>
                              <div className="p-1">
                                {globalSizes
                                  .filter(s => s.value.toLocaleLowerCase("tr").includes((sizeSearchTerm || "").toLocaleLowerCase("tr")))
                                  .map(s => (
                                  <div 
                                    key={s.id}
                                    className="px-3 py-1.5 text-sm hover:bg-orange-100 cursor-pointer rounded-md font-semibold transition-colors"
                                    onClick={() => { 
                                      setFormData({...formData, newVariant: {...(formData.newVariant || {}), size: s.value}}); 
                                      setSizeSearchOpen(false); 
                                      setSizeSearchTerm(""); 
                                    }}
                                  >
                                    {s.value}
                                  </div>
                                ))}
                                {globalSizes.filter(s => s.value.toLocaleLowerCase("tr").includes((sizeSearchTerm || "").toLocaleLowerCase("tr"))).length === 0 && (
                                  <div className="px-4 py-3 text-sm text-gray-400 italic">Sonuç bulunamadı</div>
                                )}
                              </div>
                            </div>
                          )}
                        </div>
                      </div>
                      <div>
                        <label className="block text-xs font-bold text-orange-700 mb-1 uppercase">Renk</label>
                        <div className="relative color-dropdown-container">
                          <input 
                            type="text" 
                            placeholder="Renk ara veya seç..." 
                            className="w-full border-orange-200 border-2 px-3 py-2 rounded-lg focus:border-orange-500 outline-none text-sm font-bold bg-white"
                            value={colorSearchTerm || formData.newVariant?.color || ""}
                            onChange={(e) => { setColorSearchTerm(e.target.value); setColorSearchOpen(true); }}
                            onFocus={() => setColorSearchOpen(true)}
                          />
                          {colorSearchOpen && (
                            <div className="absolute z-[9999] w-full mt-1 bg-white border-2 border-orange-300 rounded-xl shadow-2xl overscroll-contain" style={{maxHeight: '240px', overflowY: 'auto'}} onWheel={(e) => e.stopPropagation()}>
                              <div className="p-1">
                                {globalColors
                                  .filter(c => c.value.toLocaleLowerCase("tr").includes((colorSearchTerm || "").toLocaleLowerCase("tr")))
                                  .map(c => (
                                  <div 
                                    key={c.id}
                                    className="px-3 py-1.5 text-sm hover:bg-orange-100 cursor-pointer rounded-md font-semibold transition-colors"
                                    onClick={() => { 
                                      setFormData({...formData, newVariant: {...(formData.newVariant || {}), color: c.value}}); 
                                      setColorSearchOpen(false); 
                                      setColorSearchTerm(""); 
                                    }}
                                  >
                                    {c.value}
                                  </div>
                                ))}
                                {globalColors.filter(c => c.value.toLocaleLowerCase("tr").includes((colorSearchTerm || "").toLocaleLowerCase("tr"))).length === 0 && (
                                  <div className="px-4 py-3 text-sm text-gray-400 italic">Sonuç bulunamadı</div>
                                )}
                              </div>
                            </div>
                          )}
                        </div>
                      </div>
                      <div>
                        <label className="block text-xs font-bold text-orange-700 mb-1 uppercase">Stok Adedi</label>
                        <input
                          type="number"
                          value={formData.newVariant?.stock || ""}
                          onChange={(e) => setFormData({...formData, newVariant: {...(formData.newVariant || {}), stock: parseInt(e.target.value) || 0}})}
                          className="w-full border-orange-200 border-2 px-3 py-2 rounded-lg focus:border-orange-500 outline-none text-sm font-bold"
                          placeholder="0"
                        />
                      </div>
                      <div className="md:col-span-1">
                        <label className="block text-xs font-bold text-orange-700 mb-1 uppercase">Barkod</label>
                        <div className="flex gap-1">
                          <input
                            type="text"
                            value={formData.newVariant?.barcode || ""}
                            onChange={(e) => setFormData({...formData, newVariant: {...(formData.newVariant || {}), barcode: e.target.value}})}
                            className="w-full border-orange-200 border-2 px-3 py-2 rounded-lg focus:border-orange-500 outline-none text-xs font-mono"
                            placeholder="Otomatik..."
                          />
                        </div>
                      </div>
                      <div className="md:col-span-1">
                        <button
                          type="button"
                          onClick={() => {
                            if (!formData.newVariant?.size) {
                              toast.error("Beden seçimi zorunludur");
                              return;
                            }
                            const newVar = {
                              id: `var-${Date.now()}`,
                              size: formData.newVariant.size,
                              stock: formData.newVariant.stock || 0,
                              barcode: formData.newVariant.barcode || "",
                              stock_code: formData.stock_code || "",
                              color: formData.newVariant.color || ""
                            };
                            setFormData({
                              ...formData,
                              variants: [...(formData.variants || []), newVar],
                              newVariant: {}
                            });
                          }}
                          className="w-full bg-orange-600 text-white font-bold py-2.5 rounded-lg hover:bg-orange-700 shadow-md shadow-orange-200 transition-all flex items-center justify-center gap-2"
                        >
                          <Plus size={18} /> Varyantı Ekle
                        </button>
                      </div>
                    </div>
                  </div>
                </div>
              </TabsContent>

              {/* Trendyol Tab */}
              <TabsContent value="trendyol" className="space-y-6 m-0 animate-in fade-in slide-in-from-bottom-2 duration-300">
                <div className="bg-white p-8 rounded-xl border-t-4 border-t-orange-500 shadow-sm">
                  <div className="flex justify-between items-center mb-8">
                    <div>
                      <h3 className="text-xl font-black text-gray-900 uppercase tracking-tight flex items-center gap-2">
                        <Store className="text-orange-500" size={24} />
                        Trendyol Entegrasyon Ayarları
                      </h3>
                      <p className="text-sm text-gray-500">Bu ürünün Trendyol'da nasıl görüneceğini ve eşleşeceğini ayarlayın.</p>
                    </div>
                    <div className="flex items-center gap-3 bg-orange-50 px-4 py-2 rounded-full">
                      <span className="text-xs font-bold text-orange-700 uppercase">Durum:</span>
                      <span className="flex items-center gap-1.5 text-xs font-bold text-orange-600">
                        <div className="w-2 h-2 rounded-full bg-orange-500 animate-pulse" />
                        Yayına Hazır
                      </span>
                    </div>
                  </div>

                  <div className="grid grid-cols-1 md:grid-cols-2 gap-8">
                    <div className="space-y-6">
                      <div className="space-y-2">
                        <label className="block text-xs font-black text-gray-400 uppercase tracking-widest">Trendyol Kategorisi</label>
                        <SearchableMapSelect
                          optionsUrl={`/category-mapping/trendyol/options`}
                          value={{
                            id: formData.trendyol_category_id || "",
                            name: (trendyolCategories.find(c => String(c.id) === String(formData.trendyol_category_id))?.name) || "",
                          }}
                          onChange={(v) => setFormData({ ...formData, trendyol_category_id: v.id || "" })}
                          placeholder="Kategori ara... (örn: şort, kadın elbise)"
                          treeMode={true}
                          data-testid="product-trendyol-cat-search"
                        />
                      </div>

                      {/* Hepsiburada Category Mapping */}
                      <div className="space-y-2">
                        <label className="block text-xs font-black text-gray-400 uppercase tracking-widest">
                          <span className="inline-block w-2 h-2 bg-[#FF6000] rounded-full mr-1.5"></span>
                          Hepsiburada Kategorisi
                        </label>
                        <input
                          type="text"
                          value={formData.hepsiburada_category_id || ""}
                          onChange={(e) => setFormData({ ...formData, hepsiburada_category_id: e.target.value })}
                          data-testid="hb-category-id"
                          placeholder="Hepsiburada Category ID (örn: 18021982)"
                          className="w-full border-gray-200 border-2 px-4 py-3 rounded-xl focus:border-red-500 outline-none transition-all font-bold text-gray-700 bg-gray-50 focus:bg-white"
                        />
                        <input
                          type="text"
                          value={formData.hepsiburada_category_name || ""}
                          onChange={(e) => setFormData({ ...formData, hepsiburada_category_name: e.target.value })}
                          placeholder="HB Kategori Adı (örn: Giyim > Kadın > Kazak)"
                          className="w-full border-gray-200 border px-4 py-2 rounded-xl focus:border-red-500 outline-none text-xs text-gray-600 bg-gray-50 focus:bg-white"
                        />
                        <p className="text-[10px] text-gray-400">HB Merchant panelinden kategori ID'sini alıp yapıştırın.</p>
                      </div>

                      {/* Temu Category Mapping */}
                      <div className="space-y-2">
                        <label className="block text-xs font-black text-gray-400 uppercase tracking-widest">
                          <span className="inline-block w-2 h-2 bg-black rounded-full mr-1.5"></span>
                          Temu Kategorisi
                        </label>
                        <input
                          type="text"
                          value={formData.temu_category_id || ""}
                          onChange={(e) => setFormData({ ...formData, temu_category_id: e.target.value })}
                          data-testid="temu-category-id"
                          placeholder="Temu Category ID"
                          className="w-full border-gray-200 border-2 px-4 py-3 rounded-xl focus:border-gray-900 outline-none transition-all font-bold text-gray-700 bg-gray-50 focus:bg-white"
                        />
                        <input
                          type="text"
                          value={formData.temu_category_name || ""}
                          onChange={(e) => setFormData({ ...formData, temu_category_name: e.target.value })}
                          placeholder="Temu Kategori Adı"
                          className="w-full border-gray-200 border px-4 py-2 rounded-xl focus:border-gray-900 outline-none text-xs text-gray-600 bg-gray-50 focus:bg-white"
                        />
                      </div>

                      <div className="bg-gray-50 p-6 rounded-2xl border border-gray-100 space-y-4">
                        <h4 className="text-xs font-black text-gray-400 uppercase tracking-widest mb-2">Kategori Bilgisi</h4>
                        <div className="text-sm font-bold text-gray-600 italic">
                          {formData.category_name || "Kategori seçilmemiş"}
                        </div>
                        <p className="text-[10px] text-gray-400 font-medium leading-relaxed">
                          Ürün özellikleri ve Trendyol eşleştirmeleri kategori düzeyinde yönetilmektedir. 
                          Değişiklik yapmak için Kategori Ayarları sayfasını ziyaret edin.
                        </p>
                      </div>
                    </div>

                    <div className="space-y-6">
                      <div className="bg-gray-900 rounded-3xl p-8 text-white shadow-2xl shadow-orange-200 relative overflow-hidden group">
                        <div className="absolute top-0 right-0 w-32 h-32 bg-orange-500 rounded-full blur-[80px] opacity-20 group-hover:opacity-40 transition-opacity" />
                        <div className="relative z-10">
                          <p className="text-[10px] font-black text-orange-400 uppercase tracking-[4px] mb-6">Satış Özeti</p>
                          
                          <div className="space-y-4">
                            <div className="flex justify-between items-baseline border-b border-gray-800 pb-4">
                              <span className="text-gray-400 text-xs font-bold uppercase">Mağaza Fiyatı</span>
                              <span className="text-xl font-bold">{formData.sale_price || formData.price || 0} TL</span>
                            </div>
                            <div className="flex justify-between items-baseline border-b border-gray-800 pb-4">
                              <span className="text-gray-400 text-xs font-bold uppercase">Markup (%{formData.use_default_markup ? globalTrendyolMarkup : formData.markup_rate})</span>
                              <span className="text-green-400 font-bold">
                                +{((((formData.member_price_1 || formData.price) || 0) * (formData.use_default_markup ? globalTrendyolMarkup : formData.markup_rate)) / 100).toFixed(2)} TL
                              </span>
                            </div>
                            <div className="flex justify-between items-center pt-2">
                              <span className="text-white text-sm font-black uppercase tracking-widest">Trendyol Fiyatı</span>
                              <div className="text-right">
                                <span className="text-3xl font-black text-orange-500">
                                  {(((formData.member_price_1 || formData.price) || 0) * (1 + (formData.use_default_markup ? globalTrendyolMarkup : formData.markup_rate) / 100)).toFixed(2)}
                                </span>
                                <span className="text-orange-300 font-bold ml-1">TL</span>
                              </div>
                            </div>
                          </div>
                        </div>
                      </div>

                      <div 
                        className="bg-white p-6 rounded-2xl border-2 border-dashed border-gray-100 flex flex-col items-center justify-center text-center group cursor-pointer hover:border-orange-300 transition-all active:scale-95"
                        onClick={() => editingProduct && handleTrendyolSync(editingProduct.id)}
                      >
                        <div className="w-16 h-16 rounded-full bg-orange-50 flex items-center justify-center mb-4 group-hover:bg-orange-100 transition-colors">
                          <Store className="text-orange-500" size={32} />
                        </div>
                        <h4 className="text-sm font-black text-gray-900 uppercase mb-1">Şimdi Trendyol'a Aktar</h4>
                        <p className="text-[10px] font-bold text-gray-400 uppercase tracking-tighter">Ürünü anlık olarak Trendyol kataloğuna gönderin</p>
                      </div>
                    </div>
                  </div>
                </div>
                {renderDetailFields(["Pazaryeri Entegrasyonu"])}
              </TabsContent>

              {/* Images Tab */}
              <TabsContent value="images" className="space-y-6 m-0 animate-in fade-in slide-in-from-bottom-2 duration-300">
                <div
                  className={`relative bg-white p-8 rounded-xl border shadow-sm transition-all ${fileDropActive ? "ring-2 ring-orange-400 ring-offset-2 bg-orange-50/40" : ""}`}
                  onDragOver={handleGalleryDragOver}
                  onDragLeave={handleGalleryDragLeave}
                  onDrop={handleGalleryDrop}
                >
                  {fileDropActive && (
                    <div className="pointer-events-none absolute inset-0 z-20 flex items-center justify-center rounded-xl bg-orange-50/80 border-2 border-dashed border-orange-400">
                      <div className="flex items-center gap-2 text-orange-700 font-bold uppercase tracking-widest text-sm">
                        <Upload size={18} /> Görselleri buraya bırakın
                      </div>
                    </div>
                  )}
                  <div className="flex justify-between items-center mb-6">
                    <div>
                      <h3 className="font-bold text-gray-900 uppercase tracking-widest text-sm">Ürün Galerisi</h3>
                      <p className="text-xs text-gray-400 mt-1">Dosyaları buraya sürükleyip bırakarak yükleyebilir; yüklü görselleri sürükleyerek sıralayabilirsiniz.</p>
                    </div>
                    <label className="bg-black text-white px-6 py-2 rounded-full text-xs font-bold uppercase tracking-widest cursor-pointer hover:bg-gray-800 transition-all flex items-center gap-2">
                      <Upload size={16} /> Görsel Yükle
                      <input 
                        ref={imageInputRef}
                        type="file" 
                        multiple 
                        accept="image/*" 
                        onChange={handleImageUpload} 
                        className="hidden" 
                      />
                    </label>
                  </div>

                  {uploading && (
                    <div className="mb-6 bg-orange-50 p-4 rounded-lg flex items-center gap-3 animate-pulse">
                      <RefreshCw className="animate-spin text-orange-500" size={20} />
                      <span className="text-sm font-bold text-orange-700 uppercase">Görseller İşleniyor...</span>
                    </div>
                  )}

                  {formData.images.length === 0 && !uploading && (
                    <button
                      type="button"
                      onClick={() => imageInputRef.current?.click()}
                      className="w-full flex flex-col items-center justify-center gap-3 py-14 rounded-2xl border-2 border-dashed border-gray-300 text-gray-400 hover:border-orange-400 hover:text-orange-500 hover:bg-orange-50/30 transition-all"
                    >
                      <Upload size={32} className="opacity-60" />
                      <span className="text-sm font-bold uppercase tracking-widest">Görselleri sürükleyip bırakın</span>
                      <span className="text-xs">ya da seçmek için tıklayın · PNG, JPG, WEBP</span>
                    </button>
                  )}

                  <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-5 gap-6">
                    {formData.images.map((img, idx) => (
                      isSizeTableImg(img) ? null : (
                      <div
                        key={idx}
                        draggable
                        onDragStart={() => setDraggedImgIdx(idx)}
                        onDragOver={(e) => { if (draggedImgIdx === null) return; e.preventDefault(); if (dragOverImgIdx !== idx) setDragOverImgIdx(idx); }}
                        onDragLeave={() => setDragOverImgIdx((cur) => (cur === idx ? null : cur))}
                        onDrop={(e) => { if (e.dataTransfer?.files && e.dataTransfer.files.length) return; e.preventDefault(); reorderImages(draggedImgIdx, idx); setDraggedImgIdx(null); setDragOverImgIdx(null); }}
                        onDragEnd={() => { setDraggedImgIdx(null); setDragOverImgIdx(null); }}
                        className={`relative group aspect-[2/3] rounded-2xl overflow-hidden border-4 border-white shadow-md hover:shadow-xl transition-all cursor-move ${draggedImgIdx === idx ? "opacity-40" : ""} ${dragOverImgIdx === idx && draggedImgIdx !== idx ? "ring-4 ring-orange-400 scale-[1.03]" : ""}`}
                      >
                        <img src={fixImg(imgUrl(img))} draggable={false} className="w-full h-full object-cover pointer-events-none" alt="" />
                        <div className="absolute inset-0 bg-black/40 opacity-0 group-hover:opacity-100 transition-opacity flex items-center justify-center gap-2">
                          <button
                            type="button"
                            onClick={() => moveImage(idx, -1)}
                            disabled={idx === 0}
                            title="Sola al"
                            className="w-9 h-9 rounded-full bg-white text-black flex items-center justify-center hover:bg-gray-100 disabled:opacity-30 disabled:cursor-not-allowed"
                          >
                            <ChevronLeft size={18} />
                          </button>
                          <button
                            type="button"
                            onClick={() => markAsSizeImage(idx)}
                            title="Ölçü Görseli alanına taşı (sitede gizlenir, pazaryerine son görsel gider)"
                            className="w-9 h-9 rounded-full bg-amber-500 text-white flex items-center justify-center hover:bg-amber-600 transition-colors"
                          >
                            <EyeOff size={16} />
                          </button>
                          <button
                            type="button"
                            onClick={() => removeImage(idx)}
                            title="Sil"
                            className="w-9 h-9 rounded-full bg-red-500 text-white flex items-center justify-center hover:bg-red-600 transition-colors"
                          >
                            <Trash2 size={16} />
                          </button>
                          <button
                            type="button"
                            onClick={() => moveImage(idx, 1)}
                            disabled={idx === formData.images.length - 1}
                            title="Sağa al"
                            className="w-9 h-9 rounded-full bg-white text-black flex items-center justify-center hover:bg-gray-100 disabled:opacity-30 disabled:cursor-not-allowed"
                          >
                            <ChevronRight size={18} />
                          </button>
                        </div>
                        {idx === 0 && (
                          <div className="absolute top-2 left-2 px-3 py-1 bg-black text-white text-[10px] font-black uppercase tracking-tighter rounded-full">Kapak</div>
                        )}
                        <div className="absolute bottom-2 right-2 w-6 h-6 rounded-full bg-white/90 text-black flex items-center justify-center text-[10px] font-black">{idx + 1}</div>
                      </div>
                      )
                    ))}
                  </div>

                  {/* ÖLÇÜ GÖRSELİ — sitede ürün galerisinde GÖRÜNMEZ; pazaryerlerine SON görsel olarak gider.
                      Ölçü Tablosu'ndan "Görsel Oluştur" buraya otomatik düşer; galeriden sürükleyerek de taşınır. */}
                  <div
                    className="mt-8 p-5 rounded-2xl border-2 border-dashed border-amber-300 bg-amber-50/40"
                    onDragOver={(e) => { if (draggedImgIdx !== null) e.preventDefault(); }}
                    onDrop={(e) => { if (draggedImgIdx === null) return; e.preventDefault(); markAsSizeImage(draggedImgIdx); setDraggedImgIdx(null); setDragOverImgIdx(null); }}
                    data-testid="size-image-zone"
                  >
                    <h3 className="font-bold text-amber-700 uppercase tracking-widest text-sm">📏 Ölçü Görseli</h3>
                    <p className="text-xs text-amber-700/70 mt-1 mb-4">
                      Sitede ürün görselleri arasında görünmez; pazaryerlerine (Trendyol / Hepsiburada) <b>son görsel</b> olarak gönderilir.
                      Ölçü Tablosu sekmesinden "Görsel Oluştur" deyince buraya otomatik eklenir — galeriden istediğiniz görseli buraya sürükleyebilirsiniz.
                    </p>
                    {formData.images.filter(isSizeTableImg).length === 0 ? (
                      <div className="text-xs text-amber-500/80 italic py-8 text-center border border-dashed border-amber-200 rounded-xl">
                        Henüz ölçü görseli yok — galeriden bir görseli buraya sürükleyin ya da Ölçü Tablosu sekmesinden oluşturun.
                      </div>
                    ) : (
                      <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-5 gap-6">
                        {formData.images.map((img, idx) => (
                          !isSizeTableImg(img) ? null : (
                          <div key={idx} className="relative group aspect-[2/3] rounded-2xl overflow-hidden border-4 border-amber-400 shadow-md">
                            <img src={fixImg(imgUrl(img))} className="w-full h-full object-cover" alt="" />
                            <div className="absolute inset-0 bg-black/40 opacity-0 group-hover:opacity-100 transition-opacity flex items-center justify-center gap-2">
                              <button
                                type="button"
                                onClick={() => unmarkSizeImage(idx)}
                                title="Galeriye geri taşı (sitede tekrar görünür olur)"
                                className="w-9 h-9 rounded-full bg-white text-black flex items-center justify-center hover:bg-gray-100"
                              >
                                <ChevronLeft size={16} />
                              </button>
                              <button
                                type="button"
                                onClick={() => removeImage(idx)}
                                title="Sil"
                                className="w-9 h-9 rounded-full bg-red-500 text-white flex items-center justify-center hover:bg-red-600"
                              >
                                <Trash2 size={16} />
                              </button>
                            </div>
                            <div className="absolute top-2 left-2 px-2 py-1 bg-amber-500 text-white text-[10px] font-black uppercase tracking-tighter rounded-full">Ölçü</div>
                          </div>
                          )
                        ))}
                      </div>
                    )}
                  </div>
                </div>
              </TabsContent>

              {/* SEO Tab */}
              <TabsContent value="seo" className="space-y-6 m-0 animate-in fade-in slide-in-from-bottom-2 duration-300">
                <SeoTab formData={formData} setFormData={setFormData} />
                {renderDetailFields(["SEO & Adwords"])}
              </TabsContent>

              {/* Combine Products Tab — Kombin Ürün Atama */}
              <TabsContent value="combine" className="space-y-4 m-0 animate-in fade-in slide-in-from-bottom-2 duration-300">
                <CombineProductsTab
                  productId={editingProduct?.id}
                  combineIds={formData.combine_products || []}
                  onChange={(ids) => setFormData({ ...formData, combine_products: ids })}
                />
              </TabsContent>

              {/* Stock Tab — hızlı stok güncelleme; tam CRUD için "Varyantlar" sekmesi */}
              {/* Stok sekmesi kullanıcı isteğiyle kaldırıldı — varyant stokları Varyantlar sekmesinde */}
            </Tabs>
          </div>
        </DialogContent>
      </Dialog>

      {/* Variants Modal */}
      <Dialog open={variantsModalOpen} onOpenChange={setVariantsModalOpen}>
        <DialogContent className="max-w-5xl max-h-[90vh] overflow-y-auto">
          <DialogHeader className="flex flex-row items-center justify-between border-b pb-4 mb-4">
            <DialogTitle>
              Beden Varyantları - {selectedProductForVariants?.name}
            </DialogTitle>
            <button
              onClick={handleSaveVariants}
              className="px-6 py-2 bg-black text-white rounded-lg text-sm font-bold hover:bg-gray-800 transition-colors shadow-sm"
            >
              KAYDET
            </button>
          </DialogHeader>
          
          {selectedProductForVariants && (
            <div>
              {/* Summary */}
              <div className="grid grid-cols-3 gap-4 mb-4 p-4 bg-gray-50 rounded-lg">
                <div>
                  <p className="text-xs text-gray-400 font-bold uppercase tracking-wider mb-1">Renk</p>
                  <p className="text-xl font-black text-gray-900">{selectedProductForVariants.variants?.[0]?.color || selectedProductForVariants.color || '-'}</p>
                </div>
                <div>
                  <p className="text-xs text-gray-400 font-bold uppercase tracking-wider mb-1">Toplam Beden</p>
                  <p className="text-xl font-black text-gray-900">{selectedProductForVariants.variants?.length || 0}</p>
                </div>
                <div>
                  <p className="text-xs text-gray-400 font-bold uppercase tracking-wider mb-1">Toplam Stok</p>
                  <p className="text-xl font-black text-gray-900">
                    {selectedProductForVariants.variants?.reduce((sum, v) => sum + (v.stock || 0), 0) || 0}
                  </p>
                </div>
              </div>

              {/* Variants Table */}
              <div className="border border-gray-200 rounded-xl overflow-hidden shadow-sm">
              <table className="w-full text-sm">
                <thead className="bg-gray-50 border-b border-gray-200">
                  <tr>
                    <th className="text-left px-4 py-3 font-bold text-gray-600">Ürün ID</th>
                    <th className="text-left px-4 py-3 font-bold text-gray-600">Beden / Renk</th>
                    <th className="text-left px-4 py-3 font-bold text-gray-600">Stok Kodu</th>
                    <th className="text-left px-4 py-3 font-bold text-gray-600">Barkod</th>
                    <th className="text-center px-4 py-3 font-bold text-gray-600 w-24">Stok</th>
                    <th className="text-right px-4 py-3 font-bold text-gray-600 w-32" title="Ürün kartındaki Satış / İndirimli fiyat alanlarından gelir">Fiyat (TL)</th>
                    <th className="text-center px-4 py-3 font-bold text-gray-600 w-24">Durum</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-100">
                  {selectedProductForVariants.variants?.map((variant, idx) => (
                    <tr key={variant.id || idx} className="hover:bg-gray-50 transition-colors">
                      <td className="px-4 py-3 font-mono text-xs text-gray-500 whitespace-nowrap" data-testid={`variant-urunid-${idx}`}>
                        {variant.urun_id || '-'}
                      </td>
                      <td className="px-4 py-3">
                        <div className="flex flex-col">
                          <span className="font-bold text-gray-900 text-lg">{variant.size || "-"}</span>
                          {variant.color && <span className="text-xs text-gray-500">{variant.color}</span>}
                        </div>
                      </td>
                      <td className="px-4 py-3">
                        <input
                          type="text"
                          value={variant.stock_code || ""}
                          onChange={(e) => {
                           const newVariants = [...selectedProductForVariants.variants];
                           newVariants[idx] = { ...variant, stock_code: e.target.value };
                           setSelectedProductForVariants({ ...selectedProductForVariants, variants: newVariants });
                          }}
                          className="w-full border-gray-200 border px-2 py-1.5 rounded bg-white focus:border-black outline-none font-mono text-xs"
                        />
                      </td>
                      <td className="px-4 py-3">
                        <input
                          type="text"
                          value={variant.barcode || ""}
                          onChange={(e) => {
                           const newVariants = [...selectedProductForVariants.variants];
                           newVariants[idx] = { ...variant, barcode: e.target.value };
                           setSelectedProductForVariants({ ...selectedProductForVariants, variants: newVariants });
                          }}
                          className="w-full border-gray-200 border px-2 py-1.5 rounded bg-white focus:border-black outline-none font-mono text-xs"
                        />
                      </td>
                      <td className="px-4 py-3 text-center">
                        <input
                          type="number"
                          value={variant.stock || 0}
                          onChange={(e) => {
                           const newVariants = [...selectedProductForVariants.variants];
                           newVariants[idx] = { ...variant, stock: parseInt(e.target.value) || 0 };
                           setSelectedProductForVariants({ ...selectedProductForVariants, variants: newVariants });
                          }}
                          className={`w-20 border-gray-200 border px-2 py-1.5 rounded text-center font-bold bg-white focus:border-black outline-none ${variant.stock < 5 ? 'text-red-600 border-red-200' : 'text-gray-900'}`}
                        />
                      </td>
                      <td className="px-4 py-3 text-right" title="Fiyat ürün kartındaki Satış / İndirimli fiyat alanlarından gelir — listeden veya ürün düzenleden değiştirin">
                        <span className="font-bold text-red-600">
                          {Number(selectedProductForVariants.sale_price || selectedProductForVariants.price || 0).toFixed(2)}
                        </span>
                      </td>
                      <td className="px-4 py-3 text-center">
                        <button
                          onClick={() => {
                           const newVariants = [...selectedProductForVariants.variants];
                           newVariants[idx] = { ...variant, is_active: variant.is_active === false ? true : false };
                           setSelectedProductForVariants({ ...selectedProductForVariants, variants: newVariants });
                          }}
                          className={`px-3 py-1.5 text-xs font-bold rounded w-full transition-colors ${variant.is_active !== false ? 'bg-green-100 text-green-800 hover:bg-green-200' : 'bg-gray-200 text-gray-600 hover:bg-gray-300'}`}
                        >
                          {variant.is_active !== false ? 'Aktif' : 'Pasif'}
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              </div>

              {(!selectedProductForVariants.variants || selectedProductForVariants.variants.length === 0) && (
                <p className="text-center text-gray-500 py-8">Bu ürünün beden varyantı bulunmuyor</p>
              )}
            </div>
          )}
        </DialogContent>
      </Dialog>

      {/* Technical Details Import Modal */}
      <Dialog open={techImportModalOpen} onOpenChange={setTechImportModalOpen}>
        <DialogContent className="max-w-5xl max-h-[85vh] overflow-y-auto" data-testid="tech-import-modal">
          <DialogHeader>
            <DialogTitle className="text-xl font-bold">Teknik Detay Eşleştirme Sonuçları</DialogTitle>
          </DialogHeader>
          {techImportResults && (
            <div className="space-y-4">
              <div className="flex gap-4 text-sm">
                <div className="bg-green-50 border border-green-200 px-4 py-2 rounded-lg">
                  <span className="font-bold text-green-700">{techImportResults.matched}</span>
                  <span className="text-green-600 ml-1">Eşleşen</span>
                </div>
                <div className="bg-red-50 border border-red-200 px-4 py-2 rounded-lg">
                  <span className="font-bold text-red-700">{techImportResults.unmatched}</span>
                  <span className="text-red-600 ml-1">Eşleşmeyen</span>
                </div>
                <div className="bg-gray-50 border border-gray-200 px-4 py-2 rounded-lg">
                  <span className="font-bold text-gray-700">{techImportResults.total_excel_products}</span>
                  <span className="text-gray-600 ml-1">Toplam</span>
                </div>
              </div>

              <div className="border rounded-xl overflow-hidden">
                <table className="w-full text-sm">
                  <thead className="bg-gray-50 border-b">
                    <tr>
                      <th className="text-left px-3 py-2 font-bold text-gray-600 w-8">#</th>
                      <th className="text-left px-3 py-2 font-bold text-gray-600">Excel Ürün Adı</th>
                      <th className="text-left px-3 py-2 font-bold text-gray-600">Eşleşen Ürün</th>
                      <th className="text-center px-3 py-2 font-bold text-gray-600 w-16">Skor</th>
                      <th className="text-center px-3 py-2 font-bold text-gray-600 w-20">Özellik</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-gray-100">
                    {techImportResults.results.map((r, idx) => (
                      <tr key={idx} className={r.matched_product_id ? "bg-white" : "bg-red-50"}>
                        <td className="px-3 py-2 text-gray-400">{idx + 1}</td>
                        <td className="px-3 py-2 font-medium">{r.excel_name}</td>
                        <td className="px-3 py-2">
                          {r.matched_product_name ? (
                            <span className="text-green-700 font-medium">{r.matched_product_name}</span>
                          ) : (
                            <span className="text-red-500 italic">Eşleşme bulunamadı</span>
                          )}
                        </td>
                        <td className="px-3 py-2 text-center">
                          <span className={`inline-block px-2 py-0.5 rounded-full text-xs font-bold ${
                            r.match_score >= 80 ? 'bg-green-100 text-green-700' :
                            r.match_score >= 50 ? 'bg-yellow-100 text-yellow-700' :
                            r.match_score > 0 ? 'bg-orange-100 text-orange-700' :
                            'bg-red-100 text-red-700'
                          }`}>
                            %{r.match_score}
                          </span>
                        </td>
                        <td className="px-3 py-2 text-center text-gray-600">{r.attributes.length}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              <div className="flex justify-end gap-3 pt-2">
                <button
                  onClick={() => { setTechImportModalOpen(false); setTechImportResults(null); }}
                  className="px-4 py-2 bg-gray-100 text-gray-700 rounded-lg font-medium text-sm hover:bg-gray-200 transition-colors"
                >
                  İptal
                </button>
                <button
                  onClick={handleApplyTechImport}
                  disabled={techApplying || techImportResults.matched === 0}
                  data-testid="apply-tech-import-btn"
                  className="px-6 py-2 bg-orange-600 text-white rounded-lg font-bold text-sm hover:bg-orange-700 transition-colors disabled:opacity-50 shadow-sm"
                >
                  {techApplying ? "Uygulanıyor..." : `${techImportResults.matched} Ürüne Uygula`}
                </button>
              </div>
            </div>
          )}
        </DialogContent>
      </Dialog>

      {/* Barkod ile Trendyol'a Aktar — pop-up */}
      <Dialog open={barcodePushOpen} onOpenChange={setBarcodePushOpen}>
        <DialogContent className="max-w-2xl" data-testid="barcode-push-dialog">
          <DialogHeader>
            <DialogTitle>Barkod ile Trendyol'a Aktar</DialogTitle>
          </DialogHeader>
          <div className="space-y-4">
            <p className="text-sm text-gray-600">
              Aktarmak istediğiniz ürünlerin <b>barkod</b> veya <b>stok kodlarını</b> her satıra bir tane yazın.
              Boşluk, virgül veya satır sonu ile ayırabilirsiniz.
            </p>
            <textarea
              value={barcodePushText}
              onChange={(e) => setBarcodePushText(e.target.value)}
              rows={10}
              placeholder="8684483528521&#10;FCSS2700005&#10;8684483528522"
              className="w-full border rounded-lg p-3 text-sm font-mono"
              data-testid="barcode-push-textarea"
            />
            <div className="flex justify-end gap-2">
              <button
                onClick={() => setBarcodePushOpen(false)}
                className="px-4 py-2 border rounded hover:bg-gray-100"
              >
                İptal
              </button>
              <button
                disabled={barcodePushLoading || !barcodePushText.trim()}
                data-testid="barcode-push-submit-btn"
                onClick={async () => {
                  // Split by whitespace/comma/newline
                  const codes = barcodePushText
                    .split(/[\s,;\n]+/)
                    .map(s => s.trim())
                    .filter(Boolean);
                  if (!codes.length) return;
                  setBarcodePushLoading(true);
                  const token = localStorage.getItem('token');
                  // 1) ÖNCE DOĞRULA — Trendyol karşılığı olmayan değer/eksik varsa AKTARMA, uyar.
                  const tv = toast.loading(`${codes.length} ürün doğrulanıyor...`);
                  try {
                    const vr = await axios.post(
                      `${API}/integrations/trendyol/products/validate`,
                      { barcodes: codes, stock_codes: codes },
                      { headers: { Authorization: `Bearer ${token}` }, timeout: 120000 }
                    );
                    toast.dismiss(tv);
                    const blocked = (vr.data?.results || []).filter(
                      (r) => !r.is_valid || (r.unmatched_values || []).length || (r.missing_required_attrs || []).length
                    );
                    if (blocked.length) {
                      setBarcodePushLoading(false);
                      setValidationBlock(blocked);
                      return; // AKTARMA — kullanıcı eşleştirmeyi yapsın
                    }
                  } catch (e) {
                    toast.dismiss(tv);
                    toast.error(e.response?.data?.detail || "Doğrulama başarısız");
                    setBarcodePushLoading(false);
                    return;
                  }
                  // 2) Doğrulama temiz → aktar
                  const t = toast.loading(`${codes.length} kod Trendyol'a aktarılıyor...`);
                  try {
                    // Hem barkod hem stok_kodu olarak dene — backend ikisini de kontrol eder
                    const res = await axios.post(
                      `${API}/integrations/trendyol/products/sync`,
                      { barcodes: codes, stock_codes: codes },
                      { headers: { Authorization: `Bearer ${token}` }, timeout: 180000 }
                    );
                    toast.dismiss(t);
                    const data = res.data || {};
                    toast.success(`${data.successful || data.count || 0} ürün gönderildi${data.failed ? `, ${data.failed} hata` : ''}`);
                    setBarcodePushOpen(false);
                    setBarcodePushText("");
                  } catch (e) {
                    toast.dismiss(t);
                    toast.error(e.response?.data?.detail || "Aktarım başarısız");
                  } finally {
                    setBarcodePushLoading(false);
                  }
                }}
                className="flex items-center gap-2 px-4 py-2 bg-orange-600 text-white rounded hover:bg-orange-700 disabled:opacity-50"
              >
                {barcodePushLoading ? <RefreshCw className="animate-spin" size={16} /> : <Store size={16} />}
                Trendyol'a Gönder
              </button>
            </div>
          </div>
        </DialogContent>
      </Dialog>

      {/* Aktarım Engellendi — Trendyol karşılığı olmayan değerler */}
      <Dialog open={!!validationBlock} onOpenChange={(o) => { if (!o) setValidationBlock(null); }}>
        <DialogContent className="max-w-2xl max-h-[85vh] overflow-y-auto" data-testid="validation-block-dialog">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2 text-red-700">
              <AlertTriangle size={20} /> Aktarım Durduruldu — Eşleştirme Gerekli
            </DialogTitle>
          </DialogHeader>
          <p className="text-sm text-gray-600 mb-3">
            Aşağıdaki ürünlerde bazı değerlerin <b>Trendyol karşılığı bulunamadı</b>. Yanlış aktarımı
            önlemek için bu ürünler <b>gönderilmedi</b>. Lütfen ilgili kategoride değer eşleştirmesini
            yapın, ardından tekrar aktarın.
          </p>
          <div className="space-y-3">
            {(validationBlock || []).map((r, i) => (
              <div key={i} className="border border-red-200 bg-red-50/50 rounded-lg p-3" data-testid={`vblock-item-${i}`}>
                <div className="font-semibold text-sm text-gray-900 mb-1">
                  {r.name || r.stock_code} <span className="text-gray-400 font-normal">({r.stock_code})</span>
                </div>
                {(r.errors || []).filter((e) => !e.includes("karşılığı yok")).map((e, j) => (
                  <div key={j} className="text-xs text-red-600">• {e}</div>
                ))}
                {(r.unmatched_values || []).length > 0 && (
                  <div className="mt-2">
                    <div className="text-xs font-medium text-gray-700 mb-1.5">
                      Değer eşleştirme — <span className="text-gray-500">Trendyol'un kabul ettiği değeri seçin</span>:
                    </div>
                    <div className="space-y-2">
                      {r.unmatched_values.map((u, k) => {
                        const key = `${r.id}|${u.mp_attr_id}|${u.local_value}`;
                        const cur = vbSelections[key] !== undefined
                          ? vbSelections[key]
                          : (u.suggested_value?.id || "");
                        const tyVals = u.trendyol_values || [];
                        return (
                          <div key={k} className="flex items-center gap-2 flex-wrap" data-testid={`vblock-${r.stock_code}-${u.mp_attr_id}`}>
                            <span className="px-1.5 py-0.5 bg-white border border-gray-300 rounded text-xs">{u.attr_name}</span>
                            <span className="text-gray-400 text-xs">:</span>
                            <span className="font-semibold text-amber-700 text-xs">{u.local_value}</span>
                            {u.required && <span className="text-[10px] text-red-600 font-bold">(ZORUNLU)</span>}
                            <span className="text-gray-400 text-xs">→</span>
                            <select
                              value={cur}
                              onChange={(e) => setVbSelections((s) => ({ ...s, [key]: e.target.value }))}
                              className="text-xs border border-gray-300 rounded px-2 py-1 bg-white max-w-[220px]"
                              data-testid={`vblock-select-${r.stock_code}-${u.mp_attr_id}`}
                            >
                              <option value="">— Trendyol değeri seçin —</option>
                              {tyVals.map((tv) => (
                                <option key={tv.id} value={tv.id}>
                                  {tv.name}{u.suggested_value?.id === tv.id ? "  (önerilen)" : ""}
                                </option>
                              ))}
                              {u.allow_custom && (
                                <option value={`__custom__${u.local_value}`}>Serbest metin: {u.local_value}</option>
                              )}
                              <option value="__remove__">✕ Bu özelliği üründen kaldır</option>
                            </select>
                            {tyVals.length === 0 && (
                              <span className="text-[10px] text-gray-500">Trendyol bu özellik için değer döndürmedi</span>
                            )}
                          </div>
                        );
                      })}
                    </div>
                  </div>
                )}
                {(r.missing_required_attrs || []).length > 0 && (
                  <div className="text-xs text-red-600 mt-1">
                    Eksik zorunlu özellik: {r.missing_required_attrs.map((m) => m.name).join(", ")}
                  </div>
                )}
              </div>
            ))}
          </div>
          <div className="flex justify-end gap-2 mt-4 pt-3 border-t">
            <button
              onClick={() => setValidationBlock(null)}
              className="px-4 py-2 text-sm text-gray-600 border rounded hover:bg-gray-50"
              data-testid="vblock-close-btn"
            >
              Kapat
            </button>
            <button
              onClick={() => { setValidationBlock(null); navigate("/admin/kategori-eslestir"); }}
              className="flex items-center gap-2 px-4 py-2 text-sm text-gray-700 border rounded hover:bg-gray-50"
              data-testid="vblock-open-mapping-btn"
            >
              <Store size={15} /> Eşleştirme Ekranı
            </button>
            <button
              disabled={vbSaving}
              onClick={async () => {
                // Seçimleri topla: kategori bazında value-mapping merge + üründen kaldır.
                const byCat = {};       // category_id -> [{mp_attr_id, local_value, value_id}]
                const removals = [];    // {product_id, attr_name}
                let picked = 0;
                for (const r of (validationBlock || [])) {
                  for (const u of (r.unmatched_values || [])) {
                    const key = `${r.id}|${u.mp_attr_id}|${u.local_value}`;
                    const sel = vbSelections[key] !== undefined ? vbSelections[key] : (u.suggested_value?.id || "");
                    if (!sel) continue;
                    picked++;
                    if (sel === "__remove__") {
                      removals.push({ product_id: r.id, attr_name: u.attr_name });
                    } else if (String(sel).startsWith("__custom__")) {
                      const cat = r.category_id;
                      (byCat[cat] = byCat[cat] || []).push({
                        mp_attr_id: u.mp_attr_id, local_value: u.local_value,
                        value_id: String(sel).replace("__custom__", ""),
                      });
                    } else {
                      const cat = r.category_id;
                      (byCat[cat] = byCat[cat] || []).push({
                        mp_attr_id: u.mp_attr_id, local_value: u.local_value, value_id: sel,
                      });
                    }
                  }
                }
                if (!picked) { toast.error("Önce en az bir değer için seçim yapın"); return; }
                const token = localStorage.getItem("token");
                const auth = { headers: { Authorization: `Bearer ${token}` } };
                const t = toast.loading("Eşleştirmeler kaydediliyor…");
                try {
                  setVbSaving(true);
                  for (const [cat, mappings] of Object.entries(byCat)) {
                    if (!cat || cat === "undefined" || cat === "null") continue;
                    await axios.post(`${API}/integrations/trendyol/value-mappings/merge`,
                      { category_id: cat, mappings }, auth);
                  }
                  for (const rm of removals) {
                    await axios.post(`${API}/integrations/trendyol/products/${rm.product_id}/remove-attribute`,
                      { attr_name: rm.attr_name }, auth);
                  }
                  toast.dismiss(t);
                  toast.success("Kaydedildi. Şimdi tekrar 'Trendyol'a Gönder' ile aktarabilirsiniz.");
                  setVbSelections({});
                  setValidationBlock(null);
                } catch (e) {
                  toast.dismiss(t);
                  toast.error(e.response?.data?.detail || "Kaydetme başarısız");
                } finally {
                  setVbSaving(false);
                }
              }}
              className="flex items-center gap-2 px-4 py-2 text-sm font-medium text-white bg-black rounded hover:bg-gray-800 disabled:opacity-50"
              data-testid="vblock-save-btn"
            >
              {vbSaving ? <RefreshCw className="animate-spin" size={15} /> : <Store size={15} />}
              Kaydet
            </button>
          </div>
        </DialogContent>
      </Dialog>


      {/* Çöp Kutusu */}
      <Dialog open={trashOpen} onOpenChange={setTrashOpen}>
        <DialogContent className="max-w-4xl max-h-[88vh] overflow-y-auto" data-testid="trash-dialog">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <Trash2 size={18} /> Çöp Kutusu
              <span className="text-sm font-normal text-gray-500" data-testid="trash-total">({trashTotal} ürün)</span>
            </DialogTitle>
          </DialogHeader>
          <div className="relative mb-3">
            <Search size={15} className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" />
            <input
              data-testid="trash-search"
              value={trashSearch}
              onChange={(e) => { setTrashSearch(e.target.value); fetchTrash(e.target.value); }}
              placeholder="Çöp kutusunda ara (ad / stok kodu / kart id)"
              className="w-full pl-9 pr-3 py-2 border rounded-lg text-sm"
            />
          </div>
          {trashLoading ? (
            <div className="py-10 text-center text-gray-400 text-sm">Yükleniyor…</div>
          ) : trashItems.length === 0 ? (
            <div className="py-10 text-center text-gray-400 text-sm" data-testid="trash-empty">Çöp kutusu boş.</div>
          ) : (
            <div className="space-y-2">
              {trashItems.map((p) => (
                <div key={p.id} data-testid={`trash-item-${p.id}`} className="flex items-center gap-3 border rounded-lg px-3 py-2">
                  <div className="w-10 h-12 bg-gray-100 rounded overflow-hidden flex-shrink-0">
                    {p.images?.[0] && <img src={p.images[0]} alt="" className="w-full h-full object-cover" />}
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="text-sm font-medium text-gray-800 truncate">{p.name}</div>
                    <div className="text-xs text-gray-400">Kart: {p.urun_karti_id || "-"} · Stok kodu: {p.stock_code || "-"}</div>
                  </div>
                  <button
                    onClick={() => restoreProduct(p.id)}
                    data-testid={`trash-restore-${p.id}`}
                    className="flex items-center gap-1 px-3 py-1.5 bg-green-600 text-white rounded text-xs font-medium hover:bg-green-700"
                  >
                    <RefreshCw size={14} /> Geri Yükle
                  </button>
                  <button
                    onClick={() => permanentDelete(p.id)}
                    data-testid={`trash-permanent-${p.id}`}
                    className="flex items-center gap-1 px-3 py-1.5 bg-red-600 text-white rounded text-xs font-medium hover:bg-red-700"
                  >
                    <Trash2 size={14} /> Kalıcı Sil
                  </button>
                </div>
              ))}
            </div>
          )}
        </DialogContent>
      </Dialog>

      {/* ── Barkod Yazdırma: Beden Seçimi ─────────────────────────────── */}
      {barcodeSizeModal && (
        <div className="fixed inset-0 z-[80] flex items-center justify-center bg-black/40 p-4"
             onClick={() => setBarcodeSizeModal(null)}>
          <div className="bg-white rounded-lg shadow-xl w-full max-w-sm p-5"
               onClick={(e) => e.stopPropagation()}>
            <h3 className="text-sm font-semibold text-gray-800 mb-1">Barkod Yazdır — Beden Seçimi</h3>
            <p className="text-xs text-gray-500 mb-3">
              {barcodeSizeModal.mode === 'bulk'
                ? `${selectedProducts.length} üründe bulunan bedenler. Yalnız işaretli bedenlerin kartları basılır.`
                : 'Yalnız işaretli bedenlerin kartları basılır.'}
            </p>
            <label className="flex items-center gap-2 py-1.5 border-b border-gray-100 mb-1 cursor-pointer">
              <input
                type="checkbox"
                checked={barcodeSizeModal.sizes.every(s => barcodeSizeModal.selected[s])}
                onChange={(e) => {
                  const all = e.target.checked;
                  setBarcodeSizeModal(m => ({ ...m, selected: Object.fromEntries(m.sizes.map(s => [s, all])) }));
                }}
                className="rounded border-gray-300"
              />
              <span className="text-sm font-medium text-gray-800">Tüm Bedenler</span>
            </label>
            <div className="max-h-52 overflow-y-auto grid grid-cols-3 gap-x-3">
              {barcodeSizeModal.sizes.map(s => (
                <label key={s} className="flex items-center gap-2 py-1.5 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={!!barcodeSizeModal.selected[s]}
                    onChange={(e) => setBarcodeSizeModal(m => ({ ...m, selected: { ...m.selected, [s]: e.target.checked } }))}
                    className="rounded border-gray-300"
                  />
                  <span className="text-sm text-gray-700">{s}</span>
                </label>
              ))}
            </div>
            <div className="flex justify-end gap-2 mt-4">
              <button onClick={() => setBarcodeSizeModal(null)}
                      className="px-3 py-1.5 text-sm text-gray-600 hover:bg-gray-100 rounded">
                Vazgeç
              </button>
              <button onClick={confirmBarcodeSizePrint}
                      className="flex items-center gap-1 px-4 py-1.5 bg-purple-600 text-white text-sm rounded hover:bg-purple-700"
                      data-testid="barcode-size-print-confirm">
                <Printer size={14} /> Yazdır
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

/**
 * TeknikDetayPanel — Ticimax XML "description" alanından parse edilmiş
 * teknik detayları gösterir (Kumaş, Kalıp, Beden Ölçüleri, Model Ölçüleri,
 * Yıkama, Bakım, Astar, Ürün Bilgisi, vs.). Her satır editable; admin
 * elle de yeni alan ekleyebilir.
 *
 * Props:
 *   details: { slug: {label, value} } dict
 *   onChange: (updated dict) => void
 */
function TeknikDetayPanel({ details = {}, onChange }) {
  const [newLabel, setNewLabel] = React.useState("");
  const [newValue, setNewValue] = React.useState("");
  // Önceden bilinen sıralama — yoksa alfabetik
  const ORDER = ["urun_bilgisi", "kumas", "icerik", "materyal", "kalip",
    "beden_olculeri", "model_olculeri", "astar", "renk", "yikama", "bakim", "urun_kodu"];
  const entries = Object.entries(details).sort((a, b) => {
    const ai = ORDER.indexOf(a[0]); const bi = ORDER.indexOf(b[0]);
    if (ai === -1 && bi === -1) return a[0].localeCompare(b[0]);
    if (ai === -1) return 1;
    if (bi === -1) return -1;
    return ai - bi;
  });
  const update = (slug, patch) => onChange({ ...details, [slug]: { ...details[slug], ...patch } });
  const remove = (slug) => {
    const next = { ...details };
    delete next[slug];
    onChange(next);
  };
  const addNew = () => {
    if (!newLabel.trim() || !newValue.trim()) return;
    const slug = newLabel.toLowerCase().trim()
      .replace(/[^a-z0-9çğıöşü]+/g, "_").replace(/^_|_$/g, "");
    if (!slug) return;
    onChange({ ...details, [slug]: { label: newLabel.trim(), value: newValue.trim() } });
    setNewLabel("");
    setNewValue("");
  };
  const isEmpty = entries.length === 0;
  return (
    <div className="rounded-lg border-l-4 border-emerald-600 bg-emerald-50/30 p-5" data-testid="teknik-detay-panel">
      <div className="flex items-center justify-between mb-4">
        <div>
          <h3 className="text-base font-bold text-emerald-800">Teknik Detay</h3>
          <p className="text-xs text-emerald-700/70 mt-0.5">
            Ürün açıklamasından otomatik parse edilen özellikler. Düzenleyebilir veya yeni alan ekleyebilirsiniz.
          </p>
        </div>
        <span className="text-xs font-semibold text-emerald-700 bg-emerald-200/60 px-2 py-1 rounded">
          {entries.length} alan
        </span>
      </div>

      {isEmpty ? (
        <div className="text-sm text-emerald-700/70 italic py-3">
          Bu ürün için açıklamadan herhangi bir teknik detay bulunamadı. Aşağıdan elle ekleyebilirsiniz.
        </div>
      ) : (
        <div className="space-y-2.5">
          {entries.map(([slug, item]) => (
            <div key={slug} className="grid grid-cols-12 gap-2 items-start">
              <input
                value={item.label || slug}
                onChange={(e) => update(slug, { label: e.target.value })}
                placeholder="Etiket"
                className="col-span-3 px-3 py-2 border border-emerald-200 rounded text-xs font-semibold bg-white text-zinc-800"
                data-testid={`tek-label-${slug}`}
              />
              <textarea
                value={item.value || ""}
                onChange={(e) => update(slug, { value: e.target.value })}
                placeholder="Değer"
                rows={Math.min(4, Math.max(1, Math.ceil((item.value || "").length / 80)))}
                className="col-span-8 px-3 py-2 border border-emerald-200 rounded text-sm bg-white text-zinc-700 leading-snug"
                data-testid={`tek-value-${slug}`}
              />
              <button
                type="button"
                onClick={() => remove(slug)}
                className="col-span-1 text-rose-600 hover:bg-rose-50 rounded px-2 py-2 text-sm font-bold"
                title="Sil"
                data-testid={`tek-remove-${slug}`}
              >
                ✕
              </button>
            </div>
          ))}
        </div>
      )}

      <div className="border-t border-emerald-200/60 mt-4 pt-3 grid grid-cols-12 gap-2 items-start">
        <input
          value={newLabel}
          onChange={(e) => setNewLabel(e.target.value)}
          placeholder="Yeni etiket (örn: Boy)"
          className="col-span-3 px-3 py-2 border border-emerald-200 rounded text-xs font-semibold bg-white"
          data-testid="tek-new-label"
        />
        <input
          value={newValue}
          onChange={(e) => setNewValue(e.target.value)}
          placeholder="Değer (örn: Diz altı)"
          onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); addNew(); } }}
          className="col-span-8 px-3 py-2 border border-emerald-200 rounded text-sm bg-white"
          data-testid="tek-new-value"
        />
        <button
          type="button"
          onClick={addNew}
          disabled={!newLabel.trim() || !newValue.trim()}
          className="col-span-1 bg-emerald-600 text-white rounded px-2 py-2 text-sm font-semibold hover:bg-emerald-700 disabled:opacity-40"
          data-testid="tek-new-add"
        >
          +
        </button>
      </div>
    </div>
  );
}

