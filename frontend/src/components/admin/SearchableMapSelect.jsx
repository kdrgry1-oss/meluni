/**
 * SearchableMapSelect.jsx
 *
 * Pazaryeri eşleştirme ekranlarında "ID yazmak" yerine kullanılır:
 * kullanıcı arama kutusuna yazar, backend'den (options endpoint'inden) gelen
 * öneriler dropdown'da gösterilir; tıklayınca `id + name` döner.
 *
 * Modes:
 *   - flat (default): backend'den filtrelenmiş düz liste gelir
 *   - treeMode=true:  backend'den TÜM ağaç çekilir, kullanıcı expand/collapse
 *                     ile gezinir; arama yapıldığında eşleşen düğümlerin
 *                     tüm parent'ları otomatik açılır (Türkçe + multi-word AND).
 *
 * Props:
 *   - optionsUrl:  "/category-mapping/trendyol/options" gibi
 *   - value:       { id, name }
 *   - onChange:    (val) => void
 *   - placeholder: giriş kutusu placeholder
 *   - hint:        alttaki yardım mesajı
 *   - treeMode:    boolean — Tree View aç
 *   - leafOnly:    boolean (default treeMode=true iken true) — sadece yaprak seçilebilsin
 *   - "data-testid"
 */
import { useEffect, useRef, useState, useLayoutEffect, useMemo } from "react";
import axios from "axios";
import { Search, X, ChevronRight, ChevronDown } from "lucide-react";

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;
const auth = () => ({ headers: { Authorization: `Bearer ${localStorage.getItem("token")}` } });

// Türkçe-uyumlu lowercase
const trLower = (s) => (s || "").toLocaleLowerCase("tr");

// Türkçe-uyumlu çoklu kelime AND match
const tokensOf = (q) => trLower((q || "").trim()).split(/\s+/).filter(Boolean);
const matchTokens = (haystack, tokens) => {
  if (!tokens.length) return true;
  const h = trLower(haystack);
  return tokens.every((t) => h.includes(t));
};

export default function SearchableMapSelect({
  optionsUrl,
  value,
  onChange,
  placeholder = "Ara...",
  hint = "",
  treeMode = false,
  leafOnly,
  ...rest
}) {
  const onlyLeaf = leafOnly ?? treeMode;

  const [q, setQ] = useState(value?.name || "");
  const [options, setOptions] = useState([]);
  const [tree, setTree] = useState(null);
  const [expanded, setExpanded] = useState({}); // { [id]: bool } — kullanıcı override
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [noCache, setNoCache] = useState(false);
  const [openUp, setOpenUp] = useState(false);
  const wrapRef = useRef(null);
  const inputRef = useRef(null);
  const timer = useRef(null);

  useEffect(() => {
    setQ(value?.name || "");
  }, [value?.name]);

  const fetchFlat = async (query) => {
    setLoading(true);
    try {
      const r = await axios.get(
        `${API}${optionsUrl}?q=${encodeURIComponent(query || "")}&limit=300`,
        auth()
      );
      setOptions(r.data?.items || []);
      setNoCache(!!r.data?.hint && (r.data?.items || []).length === 0);
    } catch {
      setOptions([]);
    } finally {
      setLoading(false);
    }
  };

  const fetchTree = async () => {
    setLoading(true);
    try {
      const r = await axios.get(`${API}${optionsUrl}?mode=tree`, auth());
      setTree(r.data?.tree || []);
      if (!(r.data?.tree || []).length) setNoCache(!!r.data?.hint);
    } catch {
      setTree([]);
    } finally {
      setLoading(false);
    }
  };

  // İlk açılışta veri çek
  useEffect(() => {
    if (!open) return;
    if (treeMode && tree === null) fetchTree();
    if (!treeMode && options.length === 0) fetchFlat("");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  // Dışarı tıklayınca kapat
  useEffect(() => {
    function handle(e) {
      if (wrapRef.current && !wrapRef.current.contains(e.target)) setOpen(false);
    }
    document.addEventListener("mousedown", handle);
    return () => document.removeEventListener("mousedown", handle);
  }, []);

  // Aşağıda yer yetiyor mu — yetmiyorsa yukarı açılsın
  useLayoutEffect(() => {
    if (!open || !inputRef.current) return;
    const rect = inputRef.current.getBoundingClientRect();
    const spaceBelow = window.innerHeight - rect.bottom;
    const spaceAbove = rect.top;
    const dropdownHeight = 340;
    if (spaceBelow < dropdownHeight && spaceAbove > spaceBelow) {
      setOpenUp(true);
    } else {
      setOpenUp(false);
    }
    if (spaceBelow < 120 && spaceAbove < 120) {
      inputRef.current.scrollIntoView({ block: "center", behavior: "smooth" });
    }
  }, [open, options.length, tree]);

  const onInput = (v) => {
    setQ(v);
    setOpen(true);
    if (treeMode) return; // tree mode'da filtreleme client-side
    clearTimeout(timer.current);
    timer.current = setTimeout(() => fetchFlat(v), 200);
  };

  const pick = (opt) => {
    onChange?.({ id: String(opt.id ?? ""), name: opt.full_path || opt.name });
    setQ(opt.full_path || opt.name);
    setOpen(false);
  };

  const clear = () => {
    onChange?.({ id: "", name: "" });
    setQ("");
    setOpen(false);
  };

  // Tree filtreleme + otomatik expand olacak parent id'leri hesapla
  const { filteredTree, autoExpand, totalMatches } = useMemo(() => {
    if (!treeMode || !tree) return { filteredTree: null, autoExpand: {}, totalMatches: 0 };
    const tokens = tokensOf(q);
    if (tokens.length === 0) {
      return { filteredTree: tree, autoExpand: {}, totalMatches: 0 };
    }

    const exp = {};
    let matchCount = 0;

    const filterNode = (node, pathSegs) => {
      const name = node?.name || "";
      const fullPath = [...pathSegs, name].join(" > ");
      const selfMatch = matchTokens(fullPath, tokens);
      const subs = node?.subCategories || [];
      const matchedSubs = subs
        .map((s) => filterNode(s, [...pathSegs, name]))
        .filter(Boolean);

      if (selfMatch || matchedSubs.length > 0) {
        if (matchedSubs.length > 0) exp[node.id] = true;
        if (selfMatch && (!subs.length)) matchCount += 1;
        return { ...node, subCategories: matchedSubs };
      }
      return null;
    };

    const filtered = (tree || []).map((n) => filterNode(n, [])).filter(Boolean);
    return { filteredTree: filtered, autoExpand: exp, totalMatches: matchCount };
  }, [tree, q, treeMode]);

  const toggle = (id) => {
    setExpanded((prev) => {
      const curr = id in prev ? prev[id] : !!autoExpand[id];
      return { ...prev, [id]: !curr };
    });
  };

  const isOpen = (id) => (id in expanded ? expanded[id] : !!autoExpand[id]);

  const renderNode = (node, depth, pathSegs) => {
    const subs = node.subCategories || [];
    const hasChildren = subs.length > 0;
    const opened = isOpen(node.id);
    const fullPath = [...pathSegs, node.name].join(" > ");
    const isLeaf = !hasChildren;
    const selectable = isLeaf || !onlyLeaf;
    const tokens = tokensOf(q);

    // Vurgulama: eşleşen ham tokenleri kalın göster (basit substring)
    const renderLabel = () => {
      if (!tokens.length) return node.name;
      const lower = trLower(node.name);
      const hits = [];
      tokens.forEach((t) => {
        let idx = lower.indexOf(t);
        while (idx >= 0) {
          hits.push([idx, idx + t.length]);
          idx = lower.indexOf(t, idx + t.length);
        }
      });
      if (!hits.length) return node.name;
      hits.sort((a, b) => a[0] - b[0]);
      // Merge overlapping
      const merged = [];
      hits.forEach(([s, e]) => {
        if (merged.length && s <= merged[merged.length - 1][1]) {
          merged[merged.length - 1][1] = Math.max(merged[merged.length - 1][1], e);
        } else {
          merged.push([s, e]);
        }
      });
      const out = [];
      let cur = 0;
      merged.forEach(([s, e], i) => {
        if (cur < s) out.push(<span key={`p${i}`}>{node.name.slice(cur, s)}</span>);
        out.push(
          <mark key={`h${i}`} className="bg-yellow-200 text-gray-900 rounded px-0.5">
            {node.name.slice(s, e)}
          </mark>
        );
        cur = e;
      });
      if (cur < node.name.length) out.push(<span key="tail">{node.name.slice(cur)}</span>);
      return out;
    };

    return (
      <div key={node.id}>
        <div
          className={`flex items-center gap-1 border-b border-gray-50 hover:bg-orange-50 ${
            value?.id && String(value.id) === String(node.id) ? "bg-orange-100" : ""
          }`}
          style={{ paddingLeft: 8 + depth * 14, paddingRight: 8 }}
          data-testid={`tree-node-${node.id}`}
        >
          {hasChildren ? (
            <button
              type="button"
              onClick={() => toggle(node.id)}
              className="p-0.5 hover:bg-gray-200 rounded text-gray-500"
              data-testid={`tree-toggle-${node.id}`}
              aria-label={opened ? "Kapat" : "Aç"}
            >
              {opened ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
            </button>
          ) : (
            <span className="inline-block w-4" />
          )}
          <button
            type="button"
            disabled={!selectable}
            onClick={() => {
              if (selectable) {
                pick({ id: node.id, name: node.name, full_path: fullPath });
              } else {
                toggle(node.id);
              }
            }}
            className={`flex-1 text-left py-1.5 text-sm truncate ${
              selectable
                ? isLeaf
                  ? "font-medium text-gray-900 cursor-pointer"
                  : "text-gray-700 cursor-pointer"
                : "text-gray-700 cursor-pointer"
            }`}
            title={fullPath}
          >
            <span>{renderLabel()}</span>
            {isLeaf && (
              <span className="text-[10px] text-gray-400 font-mono ml-2">#{node.id}</span>
            )}
          </button>
        </div>
        {opened && hasChildren && (
          <div>
            {subs.map((s) => renderNode(s, depth + 1, [...pathSegs, node.name]))}
          </div>
        )}
      </div>
    );
  };

  return (
    <div className="relative w-full" ref={wrapRef}>
      <div className="flex items-center gap-1">
        <div className="relative flex-1">
          <Search size={13} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-gray-400" />
          <input
            ref={inputRef}
            value={q}
            onFocus={() => setOpen(true)}
            onChange={(e) => onInput(e.target.value)}
            placeholder={placeholder}
            className="w-full border border-gray-200 rounded-lg pl-7 pr-7 py-1.5 text-sm bg-white"
            data-testid={rest["data-testid"] || "searchable-input"}
          />
          {q && (
            <button
              type="button"
              onClick={clear}
              className="absolute right-2 top-1/2 -translate-y-1/2 text-gray-400 hover:text-black"
              data-testid="searchable-clear"
            >
              <X size={13} />
            </button>
          )}
        </div>
      </div>

      {open && (
        <div
          className={`absolute z-50 left-0 right-0 w-full bg-white border border-gray-200 rounded-lg shadow-lg max-h-80 overflow-y-auto ${
            openUp ? "bottom-full mb-1" : "top-full mt-1"
          }`}
          data-testid="searchable-dropdown"
        >
          {loading ? (
            <div className="px-3 py-3 text-xs text-gray-400">Yükleniyor...</div>
          ) : treeMode ? (
            !filteredTree || filteredTree.length === 0 ? (
              <div className="px-3 py-3 text-xs text-gray-400">
                {noCache
                  ? "Bu pazaryeri için henüz önbellek yok — manuel ID/ad girebilirsiniz."
                  : q
                  ? "Sonuç yok"
                  : "Kategori yok"}
              </div>
            ) : (
              <>
                {q && (
                  <div className="px-3 py-1.5 text-[10px] text-gray-500 bg-gray-50 border-b border-gray-100 sticky top-0">
                    {totalMatches > 0
                      ? `${totalMatches} yaprak kategori bulundu`
                      : "Eşleşen dal görüntüleniyor"}
                  </div>
                )}
                {filteredTree.map((n) => renderNode(n, 0, []))}
              </>
            )
          ) : options.length === 0 ? (
            <div className="px-3 py-3 text-xs text-gray-400">
              {noCache
                ? "Bu pazaryeri için henüz önbellek yok — manuel ID/ad girebilirsiniz."
                : "Sonuç yok"}
            </div>
          ) : (
            options.map((o, i) => (
              <button
                key={`${o.id}-${i}`}
                type="button"
                onClick={() => pick(o)}
                className="w-full text-left px-3 py-2 text-sm hover:bg-orange-50 border-b last:border-b-0 border-gray-100"
                data-testid={`map-option-${i}`}
              >
                <div className="font-medium text-gray-900">{o.full_path || o.name}</div>
                <div className="text-[10px] text-gray-400 font-mono">ID: {o.id}</div>
              </button>
            ))
          )}
        </div>
      )}
      {hint && !open && <p className="text-[11px] text-gray-400 mt-1">{hint}</p>}
      {value?.id && !open && (
        <p className="text-[10px] text-gray-500 mt-0.5 font-mono">Seçili ID: {value.id}</p>
      )}
    </div>
  );
}
