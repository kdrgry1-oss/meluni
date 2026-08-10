/**
 * =============================================================================
 * SearchableAttribute.jsx — Aranabilir özellik seçici (minimalist, kanal-bilinçli)
 * =============================================================================
 *
 * AMAÇ:
 *   Ürün modalının "Özellikler" sekmesinde, her PAZARYERİNİN kendi zorunlu
 *   tuttuğu özelliklerin kütüphaneden aranarak seçilmesini sağlar. Kütüphanede
 *   değeri olmayan ya da manuel'e açık (allowCustom) alanlarda serbest değer girilir.
 *
 * PROPS:
 *   - attr         : { id, name, values: string[] }
 *   - value        : Mevcut seçili değer.
 *   - onChange     : (v) => void
 *   - isRequired   : Bu KANAL için zorunlu mu? → "ZORUNLU (<KANAL>)" rozeti.
 *   - channelLabel : Rozet etiketi ("TRENDYOL" | "HEPSIBURADA" | "TEMU"). Sabit değil!
 *   - allowCustom  : true ise izinli değer listesi olsa bile manuel değer eklenebilir.
 *
 * TASARIM: minimalist — tek vurgu (kırmızı yalnız zorunlu-boş), gri tonlar,
 *   gradient/animate/pulse/shadow YOK.
 * =============================================================================
 */
import { useState, useEffect, useRef } from "react";
import { Search, Check, ChevronDown, Plus } from "lucide-react";

const SearchableAttribute = ({
  attr,
  value,
  onChange,
  isRequired,
  channelLabel = "TRENDYOL",
  allowCustom = false,
}) => {
  const [isOpen, setIsOpen] = useState(false);
  const [searchTerm, setSearchTerm] = useState("");
  const dropdownRef = useRef(null);

  const hasValue = !!value;
  // "Kalıp" alanı: yalnız Regular / Oversize / Slim Fit seçilebilir (kullanıcı isteği) —
  // diğer kütüphane değerleri (Battal, Dar Kesim vb.) listeden çıkarılır. Ürün sayfasındaki
  // dinamik beden tavsiyesi bu üç değeri tanır.
  const _isKalip = (attr.name || "").toLocaleLowerCase("tr").includes("kalıp");
  const _rawValues = _isKalip
    ? (attr.values || []).filter((v) => /regular|oversize|slim/i.test(String(v)))
    : (attr.values || []);
  // #14: Değerler önce alfabetik (Türkçe), sonra sayısal sırada.
  const _isNumVal = (s) => /^\d+([.,]\d+)?$/.test(String(s).trim());
  const opts = [..._rawValues].sort((a, b) => {
    const an = _isNumVal(a), bn = _isNumVal(b);
    if (an && bn) return parseFloat(String(a).replace(",", ".")) - parseFloat(String(b).replace(",", "."));
    if (an !== bn) return an ? 1 : -1; // sayısal değerler en sona
    return String(a).localeCompare(String(b), "tr");
  });
  const filteredValues = opts.filter((v) =>
    v.toLowerCase().includes(searchTerm.toLowerCase())
  );
  const exactExists = opts.some(
    (v) => v.toLowerCase() === searchTerm.toLowerCase()
  );
  // İzinli değer yoksa ya da alan manuel'e açıksa serbest metne izin ver
  const freeText = opts.length === 0;

  useEffect(() => {
    const onOutside = (e) => {
      if (dropdownRef.current && !dropdownRef.current.contains(e.target)) {
        setIsOpen(false);
      }
    };
    document.addEventListener("mousedown", onOutside);
    return () => document.removeEventListener("mousedown", onOutside);
  }, []);

  const reqEmpty = isRequired && !hasValue;

  const Header = () => (
    <div className="flex justify-between items-center">
      <div className="flex items-center gap-1.5">
        <label
          className={`block text-[11px] font-semibold tracking-wide ${
            reqEmpty ? "text-red-600" : "text-gray-600"
          }`}
        >
          {attr.name}
          {isRequired && <span className="text-red-500 ml-0.5">*</span>}
        </label>
        {hasValue && <Check size={12} className="text-emerald-600" strokeWidth={3} />}
      </div>
      {reqEmpty && (
        <span className="text-[10px] font-medium text-red-600 border border-red-200 px-1.5 py-0.5 rounded">
          ZORUNLU ({channelLabel})
        </span>
      )}
    </div>
  );

  const fieldBorder = hasValue
    ? "border-emerald-300"
    : reqEmpty
    ? "border-red-300"
    : "border-gray-200";

  // Serbest metin alanı (izinli değer yok)
  if (freeText) {
    return (
      <div className={`space-y-1.5 ${reqEmpty ? "p-2.5 rounded-lg border border-red-100 bg-red-50/40" : ""}`}>
        <Header />
        <input
          type="text"
          value={value || ""}
          onChange={(e) => onChange(e.target.value)}
          placeholder="Değer yazın…"
          className={`w-full border ${fieldBorder} px-3 py-2 rounded-lg bg-white outline-none transition-colors text-sm focus:border-gray-400`}
        />
      </div>
    );
  }

  // İzinli değer listesi (dropdown) + opsiyonel manuel ekleme
  return (
    <div
      className={`space-y-1.5 relative ${reqEmpty ? "p-2.5 rounded-lg border border-red-100 bg-red-50/40" : ""}`}
      ref={dropdownRef}
    >
      <Header />
      <div
        onClick={() => setIsOpen(!isOpen)}
        className={`w-full border ${fieldBorder} px-3 py-2 rounded-lg bg-white cursor-pointer flex justify-between items-center transition-colors`}
      >
        <div className="flex items-center gap-2 overflow-hidden flex-1">
          <Search size={13} className="text-gray-300 shrink-0" />
          <span className={`text-sm truncate ${hasValue ? "text-gray-900" : "text-gray-400"}`}>
            {value || (allowCustom ? "Seç ya da yaz…" : "Seçiniz…")}
          </span>
        </div>
        <ChevronDown size={13} className={`text-gray-400 transition-transform shrink-0 ${isOpen ? "rotate-180" : ""}`} />
      </div>

      {isOpen && (
        <div className="absolute z-[100] top-full left-0 right-0 mt-1 bg-white border border-gray-200 rounded-lg shadow-lg overflow-hidden">
          <div className="p-2 border-b border-gray-100 flex items-center gap-2">
            <Search size={13} className="text-gray-300" />
            <input
              autoFocus
              className="bg-transparent border-none outline-none text-xs w-full py-0.5"
              placeholder={allowCustom ? "Ara ya da yeni değer yaz…" : "Ara…"}
              value={searchTerm}
              onChange={(e) => setSearchTerm(e.target.value)}
              onClick={(e) => e.stopPropagation()}
            />
          </div>
          <div className="max-h-60 overflow-y-auto">
            {/* allowCustom: listede olmayan değeri ekleme satırı */}
            {allowCustom && searchTerm.trim() && !exactExists && (
              <div
                className="px-3 py-2.5 text-sm flex items-center gap-2 hover:bg-gray-50 cursor-pointer border-b border-gray-50 text-gray-700"
                onClick={() => {
                  onChange(searchTerm.trim());
                  setIsOpen(false);
                  setSearchTerm("");
                }}
              >
                <Plus size={13} className="text-gray-400" />
                <span>"<b>{searchTerm.trim()}</b>" değerini ekle</span>
              </div>
            )}
            {filteredValues.map((v, idx) => (
              <div
                key={idx}
                className="px-3 py-2.5 text-sm hover:bg-gray-50 cursor-pointer border-b last:border-0 border-gray-50 text-gray-700"
                onClick={() => {
                  onChange(v);
                  setIsOpen(false);
                }}
              >
                {v}
              </div>
            ))}
            {filteredValues.length === 0 && !(allowCustom && searchTerm.trim()) && (
              <div className="px-3 py-6 text-center text-xs text-gray-400">Sonuç yok</div>
            )}
          </div>
        </div>
      )}
    </div>
  );
};

export default SearchableAttribute;
