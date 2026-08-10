import { useState, useRef, useEffect } from "react";

// Çoklu-seçim dropdown.
// value = ayraçla (joinChar) birleşik string ("credit_card,bank_transfer").
// onChange aynı formatta string döndürür → mevcut filtre/URL-param akışına birebir uyumlu
// (backend tarafı virgülü split edip $in uygular; channel gibi regex alanlar "|" ayracı kullanır).
export default function MultiSelect({
  options = [],
  value = "",
  onChange,
  placeholder = "Seçiniz",
  joinChar = ",",
  className = "",
  title = "",
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef(null);
  const selected = String(value || "")
    .split(joinChar)
    .map((s) => s.trim())
    .filter(Boolean);

  useEffect(() => {
    const onDoc = (e) => {
      if (ref.current && !ref.current.contains(e.target)) setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, []);

  const toggle = (val) => {
    const next = selected.includes(val)
      ? selected.filter((v) => v !== val)
      : [...selected, val];
    onChange(next.join(joinChar));
  };

  const labelFor = (val) => options.find((o) => o.value === val)?.label || val;
  const summary =
    selected.length === 0
      ? placeholder
      : selected.length === 1
      ? labelFor(selected[0])
      : `${selected.length} seçili`;

  return (
    <div className={`relative ${className}`} ref={ref} title={title}>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="border px-3 py-1.5 rounded text-sm bg-white text-left w-full flex items-center justify-between gap-2"
      >
        <span className={selected.length ? "text-black truncate" : "text-gray-500 truncate"}>{summary}</span>
        <span className="text-gray-400 shrink-0 text-xs">▾</span>
      </button>
      {open && (
        <div className="absolute z-30 mt-1 w-full min-w-[12rem] bg-white border border-gray-200 rounded-lg shadow-xl max-h-64 overflow-y-auto">
          {options.map((o) => (
            <label
              key={o.value}
              className="flex items-center gap-2 px-3 py-2 text-sm hover:bg-gray-100 cursor-pointer"
            >
              <input
                type="checkbox"
                className="accent-black shrink-0"
                checked={selected.includes(o.value)}
                onChange={() => toggle(o.value)}
              />
              <span className="truncate">{o.label}</span>
            </label>
          ))}
          <div className="border-t p-2 sticky bottom-0 bg-white flex justify-between">
            <button
              type="button"
              className="text-xs text-gray-500 hover:text-red-600"
              onClick={() => onChange("")}
            >
              Temizle
            </button>
            <button
              type="button"
              className="text-xs px-2 py-0.5 bg-black text-white rounded hover:bg-gray-800"
              onClick={() => setOpen(false)}
            >
              Tamam
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
