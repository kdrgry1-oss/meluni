/**
 * =============================================================================
 * Pagination.jsx — Admin Panel Sayfalama Bileşeni
 * =============================================================================
 *
 * AMAÇ:
 *   Admin panel boyunca (Products, Orders, Members, Returns, Reports vb.) tekdüze
 *   bir sayfalama deneyimi sağlamak. Tablo satırları daraltıldığı için bir sayfada
 *   daha çok kayıt gözükmesini ve kullanıcının uzun tabloların sonuna inmeden
 *   de sayfa değiştirebilmesini hedefler.
 *
 * KULLANIM YERLERİ (ne ile bağlantılı?):
 *   - /app/frontend/src/pages/admin/Products.jsx  → Ürün listesi (compact + full)
 *   - /app/frontend/src/pages/admin/Orders.jsx    → Sipariş listesi (compact + full)
 *   - İleride: Members.jsx, Returns.jsx, AbandonedCarts.jsx
 *
 * VARYANTLAR:
 *   - variant="compact": Tablonun ÜZERİNDE, minimal tek satır.
 *       Sadece: "Sayfa 1 / 12 · 240 kayıt" + önceki/sonraki oklar + git kutusu.
 *       Tasarımı bozmamak için filtre barının sağında, nötr gri tonlarda durur.
 *   - variant="full"    : Tablonun ALTINDA, numaralı düğmeler + git kutusu.
 *       Mevcut numaralı pagination davranışının geliştirilmiş halidir (ilk, son,
 *       kısaltma noktaları "..." içerir, çok sayıda sayfada UI bozulmaz).
 *
 * PROPS:
 *   - page        : Mevcut aktif sayfa (1-tabanlı).
 *   - total       : Toplam kayıt sayısı (sayfa sayısını hesaplamak için).
 *   - pageSize    : Sayfa başına kayıt (Products/Orders backend limit=20 ile uyumlu).
 *   - onChange    : (newPage: number) => void  — Parent state'i günceller ve
 *                    genellikle fetch tetikler (useEffect [page] bağımlılığı).
 *   - variant     : "compact" | "full"  (vars. "full").
 *   - className   : Ek kapsayıcı sınıf (hizalama için).
 *
 * NEDEN AYRI BİR DOSYA?
 *   Products.jsx (2370+ satır) ve Orders.jsx (1480+ satır) zaten şişmiş durumda.
 *   Sayfalama mantığını tek bir yerde tutmak → ilerde "Git sayfaya" kutusunun
 *   klavye davranışı veya ikon değişikliği tek noktadan uygulanabilir.
 *
 * ERİŞİLEBİLİRLİK & TEST:
 *   Her interaktif öğenin data-testid'si vardır → testing_agent_v3_fork testleri
 *   ve ileride olası e2e senaryolarında hedeflenebilir.
 * =============================================================================
 */
import { useMemo, useState, useEffect } from "react";
import { ChevronLeft, ChevronRight, ChevronsLeft, ChevronsRight } from "lucide-react";

/**
 * buildPageRange — Görüntülenecek sayfa numaralarını ve "..." ayırıcılarını üretir.
 *   Kural: Her zaman ilk ve son sayfayı göster; aktif sayfanın etrafında siblingCount
 *   kadar komşu göster; aradaki boşluklarda "..." yerleştir.
 *   Örn: current=7, total=12, sibling=1 → [1, '...', 6, 7, 8, '...', 12]
 */
const buildPageRange = (current, totalPages, siblingCount = 1) => {
  const totalNumbers = siblingCount * 2 + 5; // first, last, current, 2 siblings, 2 dots
  if (totalPages <= totalNumbers) {
    return Array.from({ length: totalPages }, (_, i) => i + 1);
  }

  const leftSibling = Math.max(current - siblingCount, 1);
  const rightSibling = Math.min(current + siblingCount, totalPages);
  const showLeftDots = leftSibling > 2;
  const showRightDots = rightSibling < totalPages - 1;

  const range = [];
  range.push(1);
  if (showLeftDots) range.push("...");
  for (let i = leftSibling; i <= rightSibling; i++) {
    if (i !== 1 && i !== totalPages) range.push(i);
  }
  if (showRightDots) range.push("...");
  range.push(totalPages);
  return range;
};

/**
 * JumpToPageInput — Kullanıcının sayfa numarası yazıp Enter'a basarak doğrudan
 * o sayfaya gitmesini sağlayan küçük input. Her iki varyantta da kullanılır.
 *
 *   - max prop'u totalPages'e eşittir; input otomatik olarak aralığa sıkıştırılır.
 *   - Enter veya blur anında onChange tetiklenir.
 */
const JumpToPageInput = ({ totalPages, onGo, testIdPrefix = "pagination" }) => {
  const [val, setVal] = useState("");
  const go = () => {
    const n = parseInt(val, 10);
    if (!Number.isNaN(n) && n >= 1 && n <= totalPages) {
      onGo(n);
      setVal("");
    }
  };
  return (
    <div className="flex items-center gap-1 text-xs text-gray-600">
      <span className="hidden sm:inline">Git:</span>
      <input
        type="number"
        min={1}
        max={totalPages}
        value={val}
        onChange={(e) => setVal(e.target.value)}
        onKeyDown={(e) => { if (e.key === "Enter") go(); }}
        onBlur={go}
        placeholder="#"
        className="w-14 border border-gray-200 rounded px-2 py-1 text-center outline-none focus:border-orange-400 focus:ring-1 focus:ring-orange-100"
        data-testid={`${testIdPrefix}-jump-input`}
      />
    </div>
  );
};

export default function Pagination({
  page = 1,
  total = 0,
  pageSize = 20,
  onChange,
  onPageSizeChange,
  pageSizeOptions = [20, 50, 100, 200],
  variant = "full",
  className = "",
}) {
  // totalPages: toplam sayfa sayısı. total=0 iken 0 kalır, hiçbir şey render edilmez.
  const totalPages = useMemo(
    () => (total > 0 ? Math.max(1, Math.ceil(total / pageSize)) : 0),
    [total, pageSize]
  );

  // Aktif sayfa kayıtlarının aralığı: "1-20 / 240" bilgisi için.
  const from = total === 0 ? 0 : (page - 1) * pageSize + 1;
  const to = Math.min(page * pageSize, total);

  // page prop'u aralık dışına çıkarsa (filtre değişince) parent'ı güvenle sıfırla.
  useEffect(() => {
    if (totalPages > 0 && page > totalPages) onChange?.(1);
  }, [totalPages, page, onChange]);

  if (totalPages <= 1) return null;

  const go = (n) => {
    if (n < 1 || n > totalPages || n === page) return;
    onChange?.(n);
  };

  // ------------------------------ COMPACT ------------------------------------
  // Tablonun üst tarafında durur; tasarımı sıkışık tutar, yalnızca prev/next
  // okları + sayfa bilgisi + jump input + sayfa boyutu bulunur.
  if (variant === "compact") {
    return (
      <div
        className={`flex items-center gap-2 text-xs ${className}`}
        data-testid="pagination-compact"
      >
        {onPageSizeChange && (
          <select
            value={pageSize}
            onChange={(e) => onPageSizeChange(parseInt(e.target.value, 10))}
            className="border border-gray-200 rounded px-1.5 py-1 text-xs bg-white outline-none focus:border-orange-400"
            data-testid="pagination-compact-page-size"
            title="Sayfa başına kayıt"
          >
            {pageSizeOptions.map((s) => (
              <option key={s} value={s}>{s}/sayfa</option>
            ))}
          </select>
        )}
        <button
          onClick={() => go(page - 1)}
          disabled={page <= 1}
          className="p-1 rounded border border-gray-200 bg-white hover:bg-gray-50 disabled:opacity-40 disabled:cursor-not-allowed"
          title="Önceki sayfa"
          data-testid="pagination-compact-prev"
        >
          <ChevronLeft size={14} />
        </button>
        <span className="text-gray-500 whitespace-nowrap tabular-nums">
          <span className="font-semibold text-gray-800">{page}</span>
          <span className="mx-1">/</span>
          {totalPages}
          <span className="mx-2 text-gray-300">·</span>
          <span className="text-gray-400">{from}-{to} / {total}</span>
        </span>
        <button
          onClick={() => go(page + 1)}
          disabled={page >= totalPages}
          className="p-1 rounded border border-gray-200 bg-white hover:bg-gray-50 disabled:opacity-40 disabled:cursor-not-allowed"
          title="Sonraki sayfa"
          data-testid="pagination-compact-next"
        >
          <ChevronRight size={14} />
        </button>
        <JumpToPageInput
          totalPages={totalPages}
          onGo={go}
          testIdPrefix="pagination-compact"
        />
      </div>
    );
  }

  // -------------------------------- FULL -------------------------------------
  // Tablonun altında tam numaralı görünüm + jump input.
  const range = buildPageRange(page, totalPages, 1);
  return (
    <div
      className={`flex flex-col sm:flex-row items-center justify-between gap-3 mt-4 ${className}`}
      data-testid="pagination-full"
    >
      <div className="text-xs text-gray-500 tabular-nums flex items-center gap-3">
        <span>
          Toplam <span className="font-semibold text-gray-800">{total}</span> kayıt ·
          Gösterilen <span className="font-semibold text-gray-800">{from}-{to}</span>
        </span>
        {onPageSizeChange && (
          <select
            value={pageSize}
            onChange={(e) => onPageSizeChange(parseInt(e.target.value, 10))}
            className="border border-gray-200 rounded px-1.5 py-1 text-xs bg-white outline-none focus:border-orange-400"
            data-testid="pagination-full-page-size"
            title="Sayfa başına kayıt"
          >
            {pageSizeOptions.map((s) => (
              <option key={s} value={s}>{s}/sayfa</option>
            ))}
          </select>
        )}
      </div>
      <div className="flex items-center gap-1 flex-wrap justify-center">
        <button
          onClick={() => go(1)}
          disabled={page <= 1}
          className="w-8 h-8 rounded border border-gray-200 bg-white hover:bg-gray-50 disabled:opacity-40 flex items-center justify-center"
          title="İlk sayfa"
          data-testid="pagination-first"
        >
          <ChevronsLeft size={14} />
        </button>
        <button
          onClick={() => go(page - 1)}
          disabled={page <= 1}
          className="w-8 h-8 rounded border border-gray-200 bg-white hover:bg-gray-50 disabled:opacity-40 flex items-center justify-center"
          title="Önceki"
          data-testid="pagination-prev"
        >
          <ChevronLeft size={14} />
        </button>
        {range.map((p, idx) =>
          p === "..." ? (
            <span key={`dot-${idx}`} className="px-2 text-gray-400 select-none">…</span>
          ) : (
            <button
              key={p}
              onClick={() => go(p)}
              className={`w-8 h-8 rounded text-sm font-medium transition-colors ${
                page === p
                  ? "bg-black text-white"
                  : "bg-white border border-gray-200 hover:bg-gray-50 text-gray-700"
              }`}
              data-testid={`pagination-page-${p}`}
            >
              {p}
            </button>
          )
        )}
        <button
          onClick={() => go(page + 1)}
          disabled={page >= totalPages}
          className="w-8 h-8 rounded border border-gray-200 bg-white hover:bg-gray-50 disabled:opacity-40 flex items-center justify-center"
          title="Sonraki"
          data-testid="pagination-next"
        >
          <ChevronRight size={14} />
        </button>
        <button
          onClick={() => go(totalPages)}
          disabled={page >= totalPages}
          className="w-8 h-8 rounded border border-gray-200 bg-white hover:bg-gray-50 disabled:opacity-40 flex items-center justify-center"
          title="Son sayfa"
          data-testid="pagination-last"
        >
          <ChevronsRight size={14} />
        </button>
      </div>
      <JumpToPageInput
        totalPages={totalPages}
        onGo={go}
        testIdPrefix="pagination-full"
      />
    </div>
  );
}
