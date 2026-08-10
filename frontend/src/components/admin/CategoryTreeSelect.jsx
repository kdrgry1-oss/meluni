/**
 * CategoryTreeSelect — Aranabilir, ağaç yapılı kategori seçici.
 *
 * Düz kategori listesini (id, name, parent_id) hiyerarşik ağaca dönüştürür
 * (örn. GİYİM > Dış Giyim > Ceket). Üstte arama kutusu; yazılınca eşleşen
 * düğümler + ataları gösterilir ve otomatik açılır.
 *
 * value: seçili kategori id'si ("" => Tüm Kategoriler)
 * onChange(id, name): seçim değişince çağrılır.
 */
import React, { useMemo, useState, useRef, useEffect } from "react";
import { ChevronRight, ChevronDown, Search, X } from "lucide-react";

const norm = (s) =>
  (s || "")
    .toLocaleLowerCase("tr")
    .replace(/ı/g, "i").replace(/ş/g, "s").replace(/ğ/g, "g")
    .replace(/ü/g, "u").replace(/ö/g, "o").replace(/ç/g, "c");

export const CategoryTreeSelect = ({ categories = [], counts = {}, value = "", onChange }) => {
  const [open, setOpen] = useState(false);
  const [term, setTerm] = useState("");
  const [expanded, setExpanded] = useState(() => new Set());
  const ref = useRef(null);

  const byId = useMemo(() => {
    const m = {};
    categories.forEach((c) => { m[c.id] = c; });
    return m;
  }, [categories]);

  const byParent = useMemo(() => {
    const m = {};
    categories.forEach((c) => {
      const p = c.parent_id || "root";
      (m[p] = m[p] || []).push(c);
    });
    Object.values(m).forEach((arr) => arr.sort((a, b) => (a.name || "").localeCompare(b.name || "", "tr")));
    return m;
  }, [categories]);

  // Arama: eşleşen düğümler + atalarını görünür yap, ataları otomatik aç.
  const { visibleIds, autoExpand } = useMemo(() => {
    if (!term.trim()) return { visibleIds: null, autoExpand: null };
    const q = norm(term);
    const vis = new Set();
    const exp = new Set();
    categories.forEach((c) => {
      if (norm(c.name).includes(q)) {
        vis.add(c.id);
        let p = c.parent_id;
        while (p && byId[p]) {
          vis.add(p);
          exp.add(p);
          p = byId[p].parent_id;
        }
      }
    });
    return { visibleIds: vis, autoExpand: exp };
  }, [term, categories, byId]);

  useEffect(() => {
    const handler = (e) => {
      if (ref.current && !ref.current.contains(e.target)) setOpen(false);
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, []);

  const toggle = (id) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });
  };

  const select = (id, name) => {
    onChange && onChange(id, name);
    setOpen(false);
  };

  const selectedName = value && byId[value] ? byId[value].name : "Tüm Kategoriler";

  const renderNode = (cat, depth) => {
    const children = byParent[cat.id] || [];
    const hasChildren = children.length > 0;
    if (visibleIds && !visibleIds.has(cat.id)) return null;
    const isOpen = (autoExpand && autoExpand.has(cat.id)) || expanded.has(cat.id);
    return (
      <div key={cat.id}>
        <div
          className={`flex items-center gap-1 py-1 pr-2 rounded cursor-pointer hover:bg-gray-100 ${value === cat.id ? "bg-black text-white hover:bg-black" : ""}`}
          style={{ paddingLeft: `${8 + depth * 16}px` }}
          data-testid={`cattree-node-${cat.id}`}
        >
          {hasChildren ? (
            <button
              type="button"
              onClick={(e) => { e.stopPropagation(); toggle(cat.id); }}
              className="shrink-0 p-0.5"
              data-testid={`cattree-toggle-${cat.id}`}
            >
              {isOpen ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
            </button>
          ) : (
            <span className="w-[18px] shrink-0" />
          )}
          <span className="text-sm flex-1 truncate" onClick={() => select(cat.id, cat.name)}>
            {cat.name}
          </span>
          {counts[cat.id] != null && (
            <span
              className={`text-[11px] tabular-nums shrink-0 px-1.5 py-0.5 rounded-full ${value === cat.id ? "bg-white/20 text-white" : "bg-gray-100 text-gray-500"}`}
              data-testid={`cattree-count-${cat.id}`}
            >
              {counts[cat.id]}
            </span>
          )}
        </div>
        {hasChildren && isOpen && children.map((ch) => renderNode(ch, depth + 1))}
      </div>
    );
  };

  const roots = byParent["root"] || [];

  return (
    <div className="relative" ref={ref} onKeyDown={(e) => e.stopPropagation()}>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="w-full border border-gray-300 px-2.5 py-1.5 rounded text-sm outline-none focus:ring-1 focus:ring-black flex items-center justify-between bg-white"
        data-testid="pf-category"
      >
        <span className={`truncate ${value ? "text-gray-900" : "text-gray-500"}`}>{selectedName}</span>
        <ChevronDown size={16} className="text-gray-400 shrink-0" />
      </button>

      {open && (
        <div className="absolute z-50 mt-1 w-full bg-white border border-gray-300 rounded-lg shadow-lg" data-testid="cattree-panel">
          <div className="p-2 border-b sticky top-0 bg-white">
            <div className="relative">
              <Search size={14} className="absolute left-2 top-1/2 -translate-y-1/2 text-gray-400" />
              <input
                autoFocus
                type="text"
                value={term}
                onChange={(e) => setTerm(e.target.value)}
                placeholder="Kategori ara..."
                className="w-full pl-7 pr-7 py-1.5 border border-gray-200 rounded text-sm outline-none focus:ring-1 focus:ring-black"
                data-testid="cattree-search"
              />
              {term && (
                <button type="button" onClick={() => setTerm("")} className="absolute right-2 top-1/2 -translate-y-1/2 text-gray-400 hover:text-gray-700">
                  <X size={14} />
                </button>
              )}
            </div>
          </div>
          <div className="max-h-72 overflow-y-auto py-1">
            <div
              className={`py-1.5 px-3 text-sm cursor-pointer hover:bg-gray-100 ${!value ? "bg-black text-white hover:bg-black" : ""}`}
              onClick={() => select("", "")}
              data-testid="cattree-all"
            >
              Tüm Kategoriler
            </div>
            {roots.map((r) => renderNode(r, 0))}
            {visibleIds && visibleIds.size === 0 && (
              <div className="py-3 px-3 text-sm text-gray-400 text-center">Kategori bulunamadı</div>
            )}
          </div>
        </div>
      )}
    </div>
  );
};

export default CategoryTreeSelect;
