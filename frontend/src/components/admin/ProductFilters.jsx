/**
 * ProductFilters — Ticimax tarzı gelişmiş ürün filtreleme paneli (3 kolon).
 *
 * Kullanıcının yüklediği Ticimax ekran görüntüleriyle birebir; ~45 alan.
 * Sunum bileşenidir: state `Products.jsx`'te tutulur, buradan `update(key, value)`
 * ile değiştirilir. Veri henüz yoksa bile alanlar görünür (backend yapısı hazır;
 * senkron aktifleşince otomatik sorgulanır).
 *
 * data-testid kuralı: her alan benzersiz `pf-<key>` test id'sine sahiptir.
 */
import React from "react";
import { RotateCcw, Search } from "lucide-react";
import CategoryTreeSelect from "./CategoryTreeSelect";

const EVET_HAYIR = [
  { v: "", l: "Seçiniz" },
  { v: "1", l: "Evet" },
  { v: "0", l: "Hayır" },
];
const VAR_YOK = [
  { v: "", l: "Seçiniz" },
  { v: "1", l: "Var" },
  { v: "0", l: "Yok" },
];
const VAR_YOK_NE = [
  { v: "", l: "Seçiniz" },
  { v: "__nonempty__", l: "Var" },
  { v: "__empty__", l: "Yok" },
];

const labelCls = "block text-[11px] text-gray-500 font-medium mb-1 uppercase tracking-wide";
const inputCls = "w-full border border-gray-300 px-2.5 py-1.5 rounded text-sm outline-none focus:ring-1 focus:ring-black";

function Text({ label, k, value, update, placeholder }) {
  return (
    <div>
      <label className={labelCls}>{label}</label>
      <input
        type="text"
        value={value || ""}
        placeholder={placeholder || ""}
        onChange={(e) => update(k, e.target.value)}
        className={inputCls}
        data-testid={`pf-${k}`}
      />
    </div>
  );
}

function Sel({ label, k, value, update, options }) {
  return (
    <div>
      <label className={labelCls}>{label}</label>
      <select
        value={value ?? ""}
        onChange={(e) => update(k, e.target.value)}
        className={inputCls}
        data-testid={`pf-${k}`}
      >
        {options.map((o) => (
          <option key={String(o.v)} value={o.v}>{o.l}</option>
        ))}
      </select>
    </div>
  );
}

function Range({ label, kMin, kMax, filters, update }) {
  return (
    <div>
      <label className={labelCls}>{label}</label>
      <div className="flex items-center gap-1.5">
        <input
          type="number"
          value={filters[kMin] || ""}
          placeholder="min"
          onChange={(e) => update(kMin, e.target.value)}
          className={inputCls}
          data-testid={`pf-${kMin}`}
        />
        <span className="text-gray-400 text-xs">ile</span>
        <input
          type="number"
          value={filters[kMax] || ""}
          placeholder="max"
          onChange={(e) => update(kMax, e.target.value)}
          className={inputCls}
          data-testid={`pf-${kMax}`}
        />
      </div>
    </div>
  );
}

function DateRange({ label, kFrom, kTo, filters, update }) {
  return (
    <div>
      <label className={labelCls}>{label}</label>
      <div className="flex items-center gap-1.5">
        <input type="date" value={filters[kFrom] || ""} onChange={(e) => update(kFrom, e.target.value)} className={inputCls} data-testid={`pf-${kFrom}`} />
        <span className="text-gray-400 text-xs">-</span>
        <input type="date" value={filters[kTo] || ""} onChange={(e) => update(kTo, e.target.value)} className={inputCls} data-testid={`pf-${kTo}`} />
      </div>
    </div>
  );
}

export const ProductFilters = ({ filters, update, onApply, onClear, categories = [], filterOptions = {} }) => {
  const { suppliers = [], currencies = [], attribute_groups = [] } = filterOptions;

  const currencyOpts = [{ v: "", l: "Seçiniz" }, ...currencies.map((c) => ({ v: c, l: c }))];
  const attrOpts = [{ v: "", l: "Seçiniz" }, ...attribute_groups.map((a) => ({ v: a.key, l: a.label }))];

  // Enter'a basınca girilen filtreleri uygula (herhangi bir alandan).
  const handleKeyDown = (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      onApply && onApply();
    }
  };

  return (
    <div className="bg-white p-4 rounded-lg shadow-sm border mb-6" data-testid="product-filters-panel" onKeyDown={handleKeyDown}>
      <div className="flex items-center justify-between mb-3 pb-2 border-b">
        <h3 className="text-sm font-semibold text-gray-800">Gelişmiş Filtreleme</h3>
        <div className="flex items-center gap-2">
          <button
            onClick={onClear}
            className="flex items-center gap-1.5 px-3 py-1.5 text-xs text-gray-600 border rounded hover:bg-gray-50"
            data-testid="pf-clear-btn"
          >
            <RotateCcw size={14} /> Filtreleri Temizle
          </button>
          <button
            onClick={onApply}
            className="flex items-center gap-1.5 px-4 py-1.5 text-xs font-medium text-white bg-black rounded hover:bg-gray-800"
            data-testid="pf-apply-btn"
          >
            <Search size={14} /> Listele
          </button>
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-x-6 gap-y-3">
        {/* ---------------- KOLON 1: Kimlik & Kategori ---------------- */}
        <div className="space-y-3">
          <Text label="Ürün Kartı ID" k="urun_karti_id" value={filters.urun_karti_id} update={update} />
          <Text label="Varyasyon ID" k="varyasyon_id" value={filters.varyasyon_id} update={update} />
          <Text label="Ürün Adı" k="name" value={filters.name} update={update} />
          <Text label="Stok Kodu" k="stock_code" value={filters.stock_code} update={update} />
          <Text label="Gtip Kodu" k="gtip" value={filters.gtip} update={update} />
          <Text label="Barkod" k="barcode" value={filters.barcode} update={update} />
          <div>
            <label className={labelCls}>Kategori</label>
            <CategoryTreeSelect
              categories={categories}
              counts={filterOptions.category_counts || {}}
              value={filters.category_id}
              onChange={(id) => update("category_id", id)}
            />
          </div>
          <Text label="Breadcrumb Kategori" k="breadcrumb" value={filters.breadcrumb} update={update} />
          <Text label="Marka" k="brand" value={filters.brand} update={update} />
          <div>
            <label className={labelCls}>Tedarikçi Firma</label>
            <select value={filters.supplier || ""} onChange={(e) => update("supplier", e.target.value)} className={inputCls} data-testid="pf-supplier">
              <option value="">Seçiniz</option>
              {suppliers.map((s) => (<option key={s} value={s}>{s}</option>))}
            </select>
          </div>
          <Text label="Etiket" k="tag" value={filters.tag} update={update} />
          <Text label="Özel Alan 1" k="ozel1" value={filters.ozel1} update={update} />
          <Text label="Özel Alan 2" k="ozel2" value={filters.ozel2} update={update} />
          <Text label="Özel Alan 3" k="ozel3" value={filters.ozel3} update={update} />
          <Text label="Özel Alan 4" k="ozel4" value={filters.ozel4} update={update} />
          <Text label="Özel Alan 5" k="ozel5" value={filters.ozel5} update={update} />
        </div>

        {/* ---------------- KOLON 2: Stok, Fiyat, Tarih, Pazaryeri ---------------- */}
        <div className="space-y-3">
          <Range label="Stok Adedi" kMin="min_stock" kMax="max_stock" filters={filters} update={update} />
          <Range label="Satış Fiyatı" kMin="min_price" kMax="max_price" filters={filters} update={update} />
          <Range label="İndirimli Fiyat" kMin="min_indirimli" kMax="max_indirimli" filters={filters} update={update} />
          <Range label="Alış Fiyatı" kMin="min_alis" kMax="max_alis" filters={filters} update={update} />
          <Range label="Piyasa Fiyatı" kMin="min_piyasa" kMax="max_piyasa" filters={filters} update={update} />
          <Sel label="KDV Dahil" k="kdv_dahil" value={filters.kdv_dahil} update={update} options={EVET_HAYIR} />
          <Sel label="Para Birimi" k="para_birimi" value={filters.para_birimi} update={update} options={currencyOpts} />
          <DateRange label="Tarihe Göre Filtrele (Eklenme)" kFrom="date_from" kTo="date_to" filters={filters} update={update} />
          <DateRange label="Yayın Tarihine Göre Filtrele" kFrom="pub_date_from" kTo="pub_date_to" filters={filters} update={update} />
          <Sel label="Market Place Grup 1" k="mp1" value={filters.mp1} update={update} options={EVET_HAYIR} />
          <Sel label="Market Place Grup 2" k="mp2" value={filters.mp2} update={update} options={EVET_HAYIR} />
          <Sel label="Market Place Grup 3" k="mp3" value={filters.mp3} update={update} options={EVET_HAYIR} />
          <Sel label="Market Place Grup 4" k="mp4" value={filters.mp4} update={update} options={EVET_HAYIR} />
          <Sel label="Market Place Grup 5" k="mp5" value={filters.mp5} update={update} options={EVET_HAYIR} />
          <Sel label="Entegrasyon Güncelleme Aktif" k="entegrasyon" value={filters.entegrasyon} update={update} options={EVET_HAYIR} />
        </div>

        {/* ---------------- KOLON 3: Durum & Özellik Bayrakları ---------------- */}
        <div className="space-y-3">
          <Sel label="Onay (Durum)" k="status" value={filters.status} update={update} options={[
            { v: "all", l: "Tümü (Aktif & Pasif)" },
            { v: "active", l: "Sadece Aktifler" },
            { v: "passive", l: "Sadece Pasifler" },
          ]} />
          <Sel label="Ürün Listede Göster" k="kart_aktif" value={filters.kart_aktif} update={update} options={EVET_HAYIR} />
          <Sel label="Vitrin" k="is_showcase" value={filters.is_showcase} update={update} options={EVET_HAYIR} />
          <Sel label="Fırsat Ürünleri" k="is_opportunity" value={filters.is_opportunity} update={update} options={EVET_HAYIR} />
          <Sel label="Yeni Ürün" k="is_new" value={filters.is_new} update={update} options={EVET_HAYIR} />
          <Sel label="Ücretsiz Kargo" k="is_free_shipping" value={filters.is_free_shipping} update={update} options={EVET_HAYIR} />
          <Sel label="Resimli Ürünler" k="has_image" value={filters.has_image} update={update} options={VAR_YOK} />
          <Sel label="Varyasyonlular" k="has_variants" value={filters.has_variants} update={update} options={VAR_YOK} />
          <Sel label="Çoklu Barkod" k="multi_barcode" value={filters.multi_barcode} update={update} options={VAR_YOK} />
          <Sel label="Video" k="has_video" value={filters.has_video} update={update} options={VAR_YOK} />
          <Sel label="İndirimli Ürünler" k="discounted" value={filters.discounted} update={update} options={VAR_YOK} />
          <Sel label="Süreli İndirime Göre Filtrele" k="sureli_indirim" value={filters.sureli_indirim} update={update} options={VAR_YOK_NE} />
          <Sel label="Yemek Kartı Ödeme Yasaklı Ürün" k="yemek_karti" value={filters.yemek_karti} update={update} options={EVET_HAYIR} />
          <Sel label="Tahmini Teslim Süresi Göster" k="teslim_goster" value={filters.teslim_goster} update={update} options={EVET_HAYIR} />
          <Sel label="Aynı Gün Gönderim" k="ayni_gun" value={filters.ayni_gun} update={update} options={EVET_HAYIR} />
          <Sel label="Teknik Detay Grubu" k="attr_key" value={filters.attr_key} update={update} options={attrOpts} />
          <Text label="Teknik Detay Değer" k="attr_value" value={filters.attr_value} update={update} placeholder="örn. Bol Kalıp" />
          <Text label="SEO Title" k="seo_title" value={filters.seo_title} update={update} />
          <Text label="SEO Keywords" k="seo_keywords" value={filters.seo_keywords} update={update} />
          <Text label="SEO Description" k="seo_desc" value={filters.seo_desc} update={update} />
        </div>
      </div>

      {/* Alt aksiyon çubuğu — uzun panelde aşağıda da Listele/Temizle erişilebilir */}
      <div className="flex items-center justify-end gap-2 mt-4 pt-3 border-t">
        <button
          onClick={onClear}
          className="flex items-center gap-1.5 px-4 py-2 text-sm text-gray-600 border rounded hover:bg-gray-50"
          data-testid="pf-clear-btn-bottom"
        >
          <RotateCcw size={15} /> Filtreleri Temizle
        </button>
        <button
          onClick={onApply}
          className="flex items-center gap-1.5 px-6 py-2 text-sm font-medium text-white bg-black rounded hover:bg-gray-800"
          data-testid="pf-apply-btn-bottom"
        >
          <Search size={15} /> Listele
        </button>
      </div>
    </div>
  );
};

export default ProductFilters;
