/**
 * ProvinceDistrictSelect — İl + İlçe için ARANABİLİR combobox (yeni npm YOK).
 * Backend: /api/locations/tr/provinces  &  /api/locations/tr/districts?province=
 *
 * Props:
 *   city, district   -> controlled values (isim string)
 *   onChange({city, district})   -> SÖZLEŞME KORUNDU (seçilen il/ilçe isimleri forma yazılır)
 *   required         -> default true (yıldız etiketler)
 *   className        -> wrapper için
 *   selectClass      -> input stili (eski select stiliyle uyumlu)
 *   labelClass       -> label stili
 *   layout           -> "grid" (default, 2 kolon) | "inline"
 *
 * İl listesi ALFABETİK (Türkçe localeCompare). Kullanıcı yazınca filtrelenir (81 il arasında
 * scroll yerine arama). İlçe de aynı şekilde aranabilir (il seçilince o ilin ilçeleri).
 * Combobox: input + filtrelenmiş liste + dışarı-tıkla-kapat + klavye (↑/↓/Enter/Esc).
 * Checkout / Account / Admin order formlarında ortak kullanım; MNG barkod akışı için
 * value/onChange sözleşmesi ve data-testid'ler aynen korundu.
 */
import { useEffect, useRef, useState } from "react";
import axios from "axios";

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;

// Basit modül içi cache — sayfa geçişlerinde tekrar fetch atmasın
let _provincesCache = null;
const _districtsCache = new Map();

// TR-duyarlı arama normalizasyonu: İ/ı küçült + diakritikleri sadeleştir
// ("ist" → "İstanbul" eşleşsin; büyük/küçük ve aksan duyarsız).
const _trNorm = (s) =>
  (s || "")
    .toLocaleLowerCase("tr")
    .replace(/İ/g, "i")
    .replace(/ı/g, "i")
    .normalize("NFKD")
    .replace(/[\u0300-\u036f]/g, "")
    .trim();

function Combobox({ value, options, placeholder, emptyText, disabled, required, onSelect, testId, selectClass }) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [hi, setHi] = useState(0); // klavye ile vurgulanan index
  const rootRef = useRef(null);

  // Dışarı tıklayınca kapat (seçim korunur — input kapalıyken value gösterir)
  useEffect(() => {
    if (!open) return;
    const onDoc = (e) => {
      if (rootRef.current && !rootRef.current.contains(e.target)) {
        setOpen(false);
        setQuery("");
      }
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [open]);

  const q = _trNorm(query);
  const filtered = q ? options.filter((o) => _trNorm(o).includes(q)) : options;

  const choose = (label) => {
    onSelect(label);
    setOpen(false);
    setQuery("");
  };
  const openList = () => {
    if (disabled) return;
    setOpen(true);
    setQuery("");
    setHi(0);
  };
  const onKeyDown = (e) => {
    if (disabled) return;
    if (e.key === "ArrowDown") {
      e.preventDefault();
      if (!open) return openList();
      setHi((i) => Math.min(i + 1, filtered.length - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setHi((i) => Math.max(i - 1, 0));
    } else if (e.key === "Enter") {
      if (open && filtered[hi] != null) {
        e.preventDefault();
        choose(filtered[hi]);
      }
    } else if (e.key === "Escape") {
      setOpen(false);
      setQuery("");
    }
  };

  return (
    <div ref={rootRef} className="relative">
      <input
        type="text"
        role="combobox"
        aria-expanded={open}
        aria-autocomplete="list"
        autoComplete="off"
        disabled={disabled}
        required={required}
        value={open ? query : value || ""}
        placeholder={disabled ? emptyText : placeholder}
        onFocus={openList}
        onClick={openList}
        onChange={(e) => {
          setQuery(e.target.value);
          setOpen(true);
          setHi(0);
        }}
        onKeyDown={onKeyDown}
        className={`${selectClass} disabled:bg-gray-50 disabled:cursor-not-allowed`}
        data-testid={testId}
      />
      {open && !disabled && (
        <ul
          role="listbox"
          className="absolute z-30 left-0 right-0 mt-1 max-h-60 overflow-auto rounded-md border border-gray-200 bg-white shadow-lg text-sm"
        >
          {filtered.length === 0 ? (
            <li className="px-3 py-2 text-gray-400 select-none">Sonuç yok</li>
          ) : (
            filtered.map((label, idx) => (
              <li
                key={label}
                role="option"
                aria-selected={label === value}
                onMouseDown={(e) => {
                  e.preventDefault(); // input blur'undan önce seçimi al
                  choose(label);
                }}
                onMouseEnter={() => setHi(idx)}
                className={`px-3 py-2 cursor-pointer ${idx === hi ? "bg-stone-100" : ""} ${
                  label === value ? "font-semibold" : ""
                }`}
              >
                {label}
              </li>
            ))
          )}
        </ul>
      )}
    </div>
  );
}

export default function ProvinceDistrictSelect({
  city = "",
  district = "",
  onChange,
  required = true,
  className = "",
  selectClass = "w-full border px-3 py-2 text-sm focus:outline-none focus:border-black bg-white",
  labelClass = "block text-sm mb-1",
  layout = "grid",
  cityLabel = "İl",
  districtLabel = "İlçe",
  testIdPrefix = "addr",
}) {
  const [provinces, setProvinces] = useState(_provincesCache || []);
  const [districts, setDistricts] = useState([]);

  useEffect(() => {
    if (_provincesCache) return;
    axios
      .get(`${API}/locations/tr/provinces`)
      .then((r) => {
        _provincesCache = r.data?.provinces || [];
        setProvinces(_provincesCache);
      })
      .catch(() => setProvinces([]));
  }, []);

  useEffect(() => {
    if (!city) {
      setDistricts([]);
      return;
    }
    const cached = _districtsCache.get(city);
    if (cached) {
      setDistricts(cached);
      return;
    }
    axios
      .get(`${API}/locations/tr/districts?province=${encodeURIComponent(city)}`)
      .then((r) => {
        const list = r.data?.districts || [];
        _districtsCache.set(city, list);
        setDistricts(list);
      })
      .catch(() => setDistricts([]));
  }, [city]);

  // ALFABETİK (Türkçe): İ/ı/ş/ğ/ö/ü/ç doğru sıralanır. İsim listeleri combobox'a verilir.
  const provinceNames = provinces
    .map((p) => p.name)
    .filter(Boolean)
    .sort((a, b) => a.localeCompare(b, "tr"));
  const districtNames = districts
    .map((d) => (d && d.name) || d)
    .filter(Boolean)
    .sort((a, b) => a.localeCompare(b, "tr"));

  const wrapper =
    layout === "inline"
      ? `flex gap-3 ${className}`
      : `grid grid-cols-1 md:grid-cols-2 gap-3 ${className}`;

  return (
    <div className={wrapper}>
      <div className="flex-1">
        <label className={labelClass}>
          {cityLabel} {required && <span className="text-red-500">*</span>}
        </label>
        <Combobox
          value={city || ""}
          options={provinceNames}
          placeholder="Seçiniz..."
          emptyText="Seçiniz..."
          required={required}
          onSelect={(name) => onChange({ city: name, district: "" })}
          testId={`${testIdPrefix}-city-select`}
          selectClass={selectClass}
        />
      </div>
      <div className="flex-1">
        <label className={labelClass}>
          {districtLabel} {required && <span className="text-red-500">*</span>}
        </label>
        <Combobox
          value={district || ""}
          options={districtNames}
          placeholder="Seçiniz..."
          emptyText="Önce il seçin"
          disabled={!city}
          required={required}
          onSelect={(name) => onChange({ city, district: name })}
          testId={`${testIdPrefix}-district-select`}
          selectClass={selectClass}
        />
      </div>
    </div>
  );
}
