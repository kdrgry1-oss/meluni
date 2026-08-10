/**
 * ProvinceDistrictSelect — iki adet select (İl + İlçe).
 * Backend: /api/locations/tr/provinces  &  /api/locations/tr/districts?province=
 *
 * Props:
 *   city, district   -> controlled values
 *   onChange({city, district})
 *   required         -> default true (yıldız etiketler)
 *   className        -> wrapper için
 *   selectClass      -> select stili
 *   labelClass       -> label stili
 *   layout           -> "grid" (default, 2 kolon) | "inline"
 *
 * Not: Checkout / Account / Admin order formlarında ortak kullanım için
 * yazıldı. Ekstra alan eklememek adına yalın tutuldu.
 */
import { useEffect, useState } from "react";
import axios from "axios";

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;

// Basit modül içi cache — sayfa geçişlerinde tekrar fetch atmasın
let _provincesCache = null;
const _districtsCache = new Map();

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
        <select
          value={city || ""}
          onChange={(e) => onChange({ city: e.target.value, district: "" })}
          required={required}
          className={selectClass}
          data-testid={`${testIdPrefix}-city-select`}
        >
          <option value="">Seçiniz...</option>
          {provinces.map((p) => (
            <option key={p.id || p.name} value={p.name}>
              {p.name}
            </option>
          ))}
        </select>
      </div>
      <div className="flex-1">
        <label className={labelClass}>
          {districtLabel} {required && <span className="text-red-500">*</span>}
        </label>
        <select
          value={district || ""}
          onChange={(e) => onChange({ city, district: e.target.value })}
          required={required}
          disabled={!city}
          className={`${selectClass} disabled:bg-gray-50 disabled:cursor-not-allowed`}
          data-testid={`${testIdPrefix}-district-select`}
        >
          <option value="">{city ? "Seçiniz..." : "Önce il seçin"}</option>
          {districts.map((d) => (
            <option key={d.name || d} value={d.name || d}>
              {d.name || d}
            </option>
          ))}
        </select>
      </div>
    </div>
  );
}
