/**
 * ReportsExtended.jsx — Kâr & Stok Değer (yalnız Stok Değer raporları).
 *
 * Bölümler (her biri AYRI Excel indirilebilir, tüm sütunlar iki yönlü sıralanır):
 *   · Özet kartlar
 *   · Kategori Bazlı Stok
 *   · Tarih Bazlı Mal Alımı (İmalat depo sevkiyatları → alış değeri + potansiyel)
 *   · Kategori Bazlı Satış + Aylara Göre Kategori Satışları + hızlanan/yavaşlayan
 *   · Marka Bazlı Stok
 *   · Maliyeti Girilmemiş Ürünler
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import axios from "axios";
import { toast } from "sonner";
import {
  Package, TrendingUp, TrendingDown, AlertTriangle, DollarSign, Wallet, RefreshCw,
  Zap, Download, Truck,
} from "lucide-react";
import ReportScopeBadge from "../../components/ReportScopeBadge";
import { REPORT_MIN_DATE, defaultReportRange, reportPresetRange } from "../../lib/reportFilters";


const API = `${process.env.REACT_APP_BACKEND_URL}/api`;
const auth = () => ({ headers: { Authorization: `Bearer ${localStorage.getItem("token")}` } });

// Sayı biçimi tr-TR: binlik nokta, ondalık virgül, TL 2 ondalık, yüzde "%12,3".
// Değer yoksa (null/undefined/NaN) "—" gösterilir — "bilinmiyor" ile "0" karışmasın.
const _isNum = (v) => v != null && v !== "" && !Number.isNaN(Number(v));
const fmtMoney = (v) => (_isNum(v)
  ? "₺" + Number(v).toLocaleString("tr-TR", { minimumFractionDigits: 2, maximumFractionDigits: 2 })
  : "—");
const fmtPct = (v, digits = 1) => (_isNum(v)
  ? "%" + Number(v).toLocaleString("tr-TR", { minimumFractionDigits: digits, maximumFractionDigits: digits })
  : "—");
const fmtNum = (v) => (_isNum(v) ? Number(v).toLocaleString("tr-TR") : "—");
const fmtDec = (v, digits = 2) => (_isNum(v)
  ? Number(v).toLocaleString("tr-TR", { minimumFractionDigits: digits, maximumFractionDigits: digits })
  : "—");
const fmtDate = (v) => (v ? String(v).substring(0, 10).split("-").reverse().join(".") : "—");
const fmtBy = (type, v) => (type === "money" ? fmtMoney(v) : type === "pct" ? fmtPct(v)
  : type === "num" ? fmtNum(v) : type === "date" ? fmtDate(v) : (v ?? "—"));

const _PRESETS = [
  { key: "thismonth", label: "Bu Ay" },
  { key: "lastmonth", label: "Geçen Ay" },
  { key: "90", label: "Son 3 Ay" },
  { key: "all", label: "Tüm Dönem" },
];
const _rangeOf = (key) => (key === "all" ? defaultReportRange() : reportPresetRange(key));

// Ekrandaki tabloyu (sütun + SIRALI satırlar + toplam) sunucuya gönderip Excel indirir —
// dosya ekranla birebir aynıdır.
async function downloadXlsx(title, cols, rows, footer) {
  const pick = (r) => Object.fromEntries(cols.map((c) => [c.k, c.x ? c.x(r) : r?.[c.k]]));
  try {
    const res = await axios.post(`${API}/admin/reports2/export-xlsx`, {
      title,
      columns: cols.map((c) => ({ k: c.k, l: c.l, type: c.type === "date" ? "text" : (c.type || "text") })),
      rows: rows.map((r) => {
        const o = pick(r);
        cols.forEach((c) => { if (c.type === "date") o[c.k] = fmtDate(o[c.k]); });
        return o;
      }),
      footer: footer || null,
    }, { ...auth(), responseType: "blob" });
    const url = window.URL.createObjectURL(new Blob([res.data]));
    const a = document.createElement("a");
    a.href = url;
    a.download = `${title}_${new Date().toISOString().substring(0, 10)}.xlsx`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    window.URL.revokeObjectURL(url);
  } catch (_) {
    toast.error("Excel indirilemedi");
  }
}

function ExcelBtn({ onClick, testid }) {
  return (
    <button type="button" onClick={onClick} data-testid={testid}
      className="inline-flex items-center gap-1.5 h-8 px-3 rounded-md border border-gray-300 bg-white text-xs font-semibold text-gray-700 hover:border-gray-900 hover:text-gray-900 shrink-0">
      <Download className="w-3.5 h-3.5" /> Excel
    </button>
  );
}

// Ortak tablo: başlık + (opsiyonel) ek bilgi + Excel düğmesi; her sütun başlığa tıklayınca
// büyükten küçüğe ⇄ küçükten büyüğe sıralanır. Bilinmeyen (—) değerler iki yönde de en altta.
function DataTable({ title, extra, cols, rows, footer, initialSort, rowKey, testid, empty = "Veri yok", maxH }) {
  const [sort, setSort] = useState(initialSort || { k: cols[1]?.k, dir: "desc" });
  const toggle = (c) => setSort((cur) => (cur.k === c.k
    ? { k: c.k, dir: cur.dir === "desc" ? "asc" : "desc" }
    : { k: c.k, dir: (c.type || "text") === "text" ? "asc" : "desc" }));
  const sorted = useMemo(() => {
    const col = cols.find((c) => c.k === sort.k) || cols[0];
    const val = (r) => (col.sv ? col.sv(r) : r?.[col.k]);
    return (rows || []).slice().sort((a, b) => {
      const av = val(a); const bv = val(b);
      const an = av == null || av === ""; const bn = bv == null || bv === "";
      if (an && bn) return 0;
      if (an) return 1;
      if (bn) return -1;
      const r = (col.type || "text") === "text" || col.type === "date"
        ? String(av).localeCompare(String(bv), "tr")
        : Number(av) - Number(bv);
      return sort.dir === "asc" ? r : -r;
    });
  }, [rows, cols, sort]);
  const right = (c) => (c.type && c.type !== "text" && c.type !== "date");

  return (
    <section className="bg-white border border-gray-200 rounded-xl overflow-hidden" data-testid={testid}>
      <div className="px-5 py-3 border-b flex flex-wrap items-center gap-x-4 gap-y-2">
        <h3 className="text-base font-medium text-gray-900">{title}</h3>
        {extra}
        <div className="ml-auto">
          <ExcelBtn testid={testid ? `${testid}-excel` : undefined}
            onClick={() => downloadXlsx(title, cols, sorted, footer)} />
        </div>
      </div>
      <div className={`overflow-x-auto ${maxH ? `${maxH} overflow-y-auto` : ""}`}>
        <table className="w-full text-sm">
          <thead className="bg-gray-50 text-xs text-gray-600 sticky top-0">
            <tr>
              <th className="px-3 py-2 text-left w-8">#</th>
              {cols.map((c) => {
                const on = sort.k === c.k;
                return (
                  <th key={c.k} className={`px-3 py-2 whitespace-nowrap ${right(c) ? "text-right" : "text-left"}`}>
                    <button type="button" onClick={() => toggle(c)}
                      className={`inline-flex items-center gap-1 hover:text-gray-900 ${on ? "text-gray-900 font-semibold" : ""}`}>
                      {c.l}
                      <span className={`w-2.5 text-[10px] ${on ? "" : "opacity-30"}`}>{on && sort.dir === "asc" ? "▲" : "▼"}</span>
                    </button>
                  </th>
                );
              })}
            </tr>
          </thead>
          <tbody>
            {sorted.length === 0 ? (
              <tr><td colSpan={cols.length + 1} className="text-center text-gray-500 py-6">{empty}</td></tr>
            ) : sorted.map((r, i) => (
              <tr key={rowKey ? rowKey(r) : i} className="border-t border-gray-100 hover:bg-gray-50">
                <td className="px-3 py-2 text-gray-400 tabular-nums">{i + 1}</td>
                {cols.map((c) => (
                  <td key={c.k} className={`px-3 py-2 ${right(c) ? "text-right tabular-nums" : ""} ${c.cls || ""}`}>
                    {c.render ? c.render(r) : fmtBy(c.type, r?.[c.k])}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
          {footer && sorted.length > 0 && (
            <tfoot className="bg-gray-50 text-sm font-medium border-t">
              <tr>
                <td className="px-3 py-2" />
                {cols.map((c) => (
                  <td key={c.k} className={`px-3 py-2 ${right(c) ? "text-right tabular-nums" : ""}`}>
                    {footer[c.k] == null ? "" : (c.type && c.type !== "text" ? fmtBy(c.type, footer[c.k]) : footer[c.k])}
                  </td>
                ))}
              </tr>
            </tfoot>
          )}
        </table>
      </div>
    </section>
  );
}

function PresetBar({ preset, setPreset, children }) {
  return (
    <div className="flex flex-wrap items-center gap-2">
      {_PRESETS.map((p) => (
        <button key={p.key} type="button" onClick={() => setPreset(p.key)}
          className={`h-8 px-3 rounded-md text-xs font-semibold border whitespace-nowrap ${preset === p.key ? "bg-gray-900 text-white border-gray-900" : "bg-white text-gray-700 border-gray-200 hover:border-gray-400"}`}>
          {p.label}
        </button>
      ))}
      {children}
    </div>
  );
}


export default function ReportsExtended() {
  // Kullanıcı isteği (2026-09-30): sayfada YALNIZ Stok Değer raporları kalır.
  return (
    <div data-testid="reports-extended-page" className="space-y-6">
      <h1 className="text-2xl font-light text-gray-900 flex items-center gap-2 flex-wrap">Stok Değer <ReportScopeBadge kind="stock" /></h1>
      <StockValuation />
    </div>
  );
}

const _CAT_COLS = [
  { k: "name", l: "Kategori", cls: "font-medium text-gray-900" },
  { k: "units", l: "Stok Adedi", type: "num" },
  { k: "pct", l: "Yüzde Pay", type: "pct" },
  { k: "cost", l: "Alış Değeri", type: "money" },
  { k: "sale", l: "Potansiyel Satış Değeri", type: "money" },
  { k: "avgCost", l: "Ort. Alış Fiyatı", type: "money" },
  { k: "avgSale", l: "Ort. Satış Fiyatı", type: "money" },
  { k: "marj", l: "Kâr Marjı", type: "pct" },
];

const _BRAND_COLS = [
  { k: "name", l: "Marka", cls: "font-medium text-gray-900" },
  { k: "units", l: "Stok Adedi", type: "num" },
  { k: "cost", l: "Alış Değeri", type: "money" },
  { k: "sale", l: "Potansiyel Satış Değeri", type: "money" },
];

const _MISSING_COLS = [
  { k: "name", l: "Ürün", render: (m) => (
    <a href={`/admin/urunler/${m.id}`} target="_blank" rel="noopener noreferrer" className="text-blue-700 hover:underline">{m.name}</a>) },
  { k: "stock_code", l: "Stok Kodu", cls: "font-mono text-xs text-gray-600" },
  { k: "units", l: "Stok Adedi", type: "num" },
  { k: "sale_price", l: "Satış Fiyatı", type: "money" },
  { k: "sale_value", l: "Potansiyel Satış Değeri", type: "money" },
];

function StockValuation() {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const r = await axios.get(`${API}/admin/reports2/stock-valuation`, auth());
      setData(r.data);
    } catch (e) { toast.error(e?.response?.data?.detail || "Yüklenemedi"); }
    finally { setLoading(false); }
  }, []);
  useEffect(() => { load(); }, [load]);

  const t = data?.totals || {};
  const cats = useMemo(() => (data?.by_category || []).map((c) => {
    const vu = c.valued_units || 0;
    return {
      ...c,
      pct: t.units ? (c.units / t.units) * 100 : 0,
      cost: vu ? c.cost : null,
      avgCost: vu ? c.cost / vu : null,
      avgSale: c.units ? c.sale / c.units : null,
      marj: c.valued_sale ? ((c.valued_sale - c.cost) / c.valued_sale) * 100 : null,
    };
  }), [data, t.units]);

  if (loading) return <div className="text-gray-500 text-sm">Hesaplanıyor...</div>;
  if (!data) return null;
  const missing = data.missing_cost || [];
  const brands = data.by_brand || [];
  const topCat = cats.slice().sort((a, b) => b.units - a.units)[0];

  const kpiRows = [
    { k: "Toplam Stok Adedi", v: t.units, type: "num" },
    { k: "Alış Değeri", v: t.cost_value, type: "money" },
    { k: "Potansiyel Satış Değeri", v: t.sale_value, type: "money" },
    { k: "Potansiyel Kâr", v: t.potential_profit, type: "money" },
    { k: "Kâr Marjı", v: t.potential_margin_pct, type: "pct" },
    { k: "Maliyetsiz Ürün", v: t.missing_count, type: "num" },
  ];

  return (
    <div className="space-y-8">
      <div>
        <div className="flex items-center justify-between mb-2">
          <h3 className="text-base font-medium text-gray-900">Özet</h3>
          <ExcelBtn testid="kpi-excel" onClick={() => downloadXlsx("Stok Değer Özet",
            [{ k: "k", l: "Gösterge" }, { k: "v", l: "Değer", type: "num" }],
            kpiRows.map((r) => ({ k: r.k, v: r.type === "pct" ? Number(r.v || 0) : r.v })))} />
        </div>
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
          <GradTile testid="kpi-units" grad="from-slate-900 to-slate-700" icon={Package}
            label="Toplam Stok Adedi" value={fmtNum(t.units)} sub={`${fmtNum(cats.length)} kategori`} />
          <GradTile testid="kpi-cost-value" grad="from-blue-600 to-blue-500" icon={DollarSign}
            label="Alış Değeri" value={fmtMoney(t.cost_value)}
            sub={t.missing_count > 0 ? `${fmtNum(t.missing_count)} ürün maliyetsiz` : null} />
          <GradTile testid="kpi-sale-value" grad="from-emerald-600 to-emerald-500" icon={Wallet}
            label="Potansiyel Satış Değeri" value={fmtMoney(t.sale_value)} />
          <GradTile testid="kpi-potential-profit" grad="from-violet-600 to-violet-500" icon={TrendingUp}
            label="Potansiyel Kâr" value={fmtMoney(t.potential_profit)} sub={`Marj ${fmtPct(t.potential_margin_pct)}`} />
        </div>
      </div>

      <DataTable testid="stock-by-category" title="Kategori Bazlı Stok" cols={_CAT_COLS} rows={cats}
        rowKey={(r) => r.name} initialSort={{ k: "units", dir: "desc" }}
        extra={topCat ? (
          <span className="text-xs text-gray-600">En çok stok: <b className="text-gray-900">{topCat.name}</b></span>
        ) : null}
        footer={{
          name: "Toplam", units: t.units, pct: 100, cost: t.cost_value, sale: t.sale_value,
          avgCost: t.valued_units ? t.cost_value / t.valued_units : null,
          avgSale: t.units ? t.sale_value / t.units : null, marj: t.potential_margin_pct,
        }} />

      <Purchases />

      <CategorySales />

      {brands.length > 1 && (
        <DataTable testid="stock-by-brand" title="Marka Bazlı Stok" cols={_BRAND_COLS} rows={brands}
          rowKey={(r) => r.name} initialSort={{ k: "units", dir: "desc" }} />
      )}

      {missing.length > 0 && (
        <DataTable testid="missing-cost" title="Maliyeti Girilmemiş Ürünler" cols={_MISSING_COLS} rows={missing}
          rowKey={(r) => r.id} initialSort={{ k: "sale_value", dir: "desc" }} maxH="max-h-96"
          extra={<span className="inline-flex items-center gap-1 text-xs text-amber-700"><AlertTriangle className="w-3.5 h-3.5" /> {fmtNum(t.missing_count)} ürün</span>} />
      )}
    </div>
  );
}

// ── Tarih Bazlı Mal Alımı (İmalat depo sevkiyatları) ──────────────────────────
const _PUR_COLS = [
  { k: "product", l: "Ürün", cls: "font-medium text-gray-900" },
  { k: "card_id", l: "Kart ID", cls: "font-mono text-xs text-gray-600" },
  { k: "partner", l: "İmalatçı" },
  { k: "first_date", l: "İlk Sevk", type: "date" },
  { k: "last_date", l: "Son Sevk", type: "date" },
  { k: "units", l: "Alınan Adet", type: "num" },
  { k: "unit_cost", l: "Birim Alış", type: "money" },
  { k: "cost", l: "Alış Değeri", type: "money" },
  { k: "sale_price", l: "Güncel Satış Fiyatı", type: "money" },
  { k: "sale", l: "Potansiyel Satış Değeri", type: "money" },
  { k: "profit", l: "Potansiyel Kâr", type: "money" },
  { k: "margin_pct", l: "Kâr Marjı", type: "pct" },
];

function Purchases() {
  const [preset, setPreset] = useState("thismonth");
  const [range, setRange] = useState(() => _rangeOf("thismonth"));
  const [d, setD] = useState(null);
  const [loading, setLoading] = useState(false);
  const seq = useRef(0);

  useEffect(() => {
    if (!range.from || !range.to || range.from > range.to) return;
    const my = ++seq.current;
    setLoading(true);
    axios.get(`${API}/admin/reports2/purchases`, { ...auth(), params: { start_date: range.from, end_date: range.to } })
      .then((r) => { if (my === seq.current) setD(r.data); })
      .catch((e) => { if (my === seq.current) toast.error(e?.response?.data?.detail || "Mal alımı yüklenemedi"); })
      .finally(() => { if (my === seq.current) setLoading(false); });
  }, [range]);

  const pick = (k) => { setPreset(k); setRange(_rangeOf(k)); };
  const tt = d?.totals || {};
  return (
    <section className="space-y-4" data-testid="purchases">
      <div className="flex flex-wrap items-center gap-3">
        <h3 className="text-base font-medium text-gray-900 inline-flex items-center gap-2"><Truck className="w-4 h-4" /> Tarih Bazlı Mal Alımı</h3>
        <PresetBar preset={preset} setPreset={pick}>
          <div className="flex items-center gap-2 h-8 bg-white px-2 border rounded-md">
            <input type="date" min={REPORT_MIN_DATE} value={range.from}
              onChange={(e) => { if (e.target.value) { setPreset(""); setRange((r) => ({ ...r, from: e.target.value })); } }}
              className="text-xs border-0 p-0" />
            <span className="text-gray-400">→</span>
            <input type="date" min={REPORT_MIN_DATE} value={range.to}
              onChange={(e) => { if (e.target.value) { setPreset(""); setRange((r) => ({ ...r, to: e.target.value })); } }}
              className="text-xs border-0 p-0" />
          </div>
        </PresetBar>
        {loading && <span className="text-xs text-gray-500 inline-flex items-center gap-1"><RefreshCw className="w-3 h-3 animate-spin" /> Hesaplanıyor…</span>}
      </div>
      {d && (
        <>
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
            <GradTile grad="from-slate-900 to-slate-700" icon={Package} label="Alınan Adet" value={fmtNum(tt.units)}
              sub={`${fmtNum(tt.rows)} ürün`} />
            <GradTile grad="from-blue-600 to-blue-500" icon={DollarSign} label="Alış Değeri" value={fmtMoney(tt.cost)} />
            <GradTile grad="from-emerald-600 to-emerald-500" icon={Wallet} label="Potansiyel Satış Değeri" value={fmtMoney(tt.sale)}
              sub={tt.unmatched > 0 ? `${fmtNum(tt.unmatched)} ürün eşleşmedi` : null} />
            <GradTile grad="from-violet-600 to-violet-500" icon={TrendingUp} label="Potansiyel Kâr" value={fmtMoney(tt.profit)}
              sub={`Marj ${fmtPct(tt.margin_pct)}`} />
          </div>
          <DataTable testid="purchases-table" title={`Mal Alımı ${fmtDate(d.range?.start)} – ${fmtDate(d.range?.end)}`}
            cols={_PUR_COLS} rows={d.rows || []} initialSort={{ k: "cost", dir: "desc" }}
            rowKey={(r) => `${r.order_no}|${r.product}`} empty="Bu dönemde depo sevkiyatı yok"
            footer={{ product: "Toplam", units: tt.units, cost: tt.cost, sale: tt.sale, profit: tt.profit, margin_pct: tt.margin_pct }} />
        </>
      )}
    </section>
  );
}

// Satış Raporları kartlarıyla aynı görünüm: renkli degrade + beyaz metin.
function GradTile({ label, value, sub, grad, icon: Icon, testid }) {
  return (
    <div data-testid={testid} className={`bg-gradient-to-br ${grad} text-white rounded-xl p-5 min-w-0`}>
      <div className="flex items-center justify-between text-[11px] uppercase tracking-wide opacity-90">
        <span className="truncate">{label}</span>
        {Icon ? <Icon className="w-4 h-4 shrink-0" /> : null}
      </div>
      <div className="text-2xl md:text-[26px] font-semibold tabular-nums mt-2 truncate">{value}</div>
      <div className="text-[11px] opacity-85 mt-1 leading-snug min-h-[15px]">{sub || ""}</div>
    </div>
  );
}

const _CS_SOURCES = [
  { key: "", label: "Tüm Kanallar" }, { key: "site", label: "Site" },
  { key: "trendyol", label: "Trendyol" }, { key: "hepsiburada", label: "Hepsiburada" },
];

const _CS_COLS = [
  { k: "category", l: "Kategori", cls: "font-medium text-gray-900" },
  { k: "qty", l: "Net Satış Adedi", type: "num" },
  { k: "qty_share", l: "Yüzde Pay", type: "pct" },
  { k: "revenue", l: "Net Ciro", type: "money" },
  { k: "revenue_share", l: "Ciro Yüzde Payı", type: "pct" },
  { k: "avg_price", l: "Ort. Satış Fiyatı", type: "money" },
  { k: "avg_cost", l: "Ort. Alış Fiyatı", type: "money" },
  { k: "gross_margin_pct", l: "Brüt Marj", type: "pct" },
];

// Kategori satış performansı + aylık trend (ısı tablosu) + hızlanan/yavaşlayan kategoriler.
function CategorySales() {
  const [preset, setPreset] = useState("thismonth");
  const [source, setSource] = useState("");
  const [metric, setMetric] = useState("qty");
  const [showAll, setShowAll] = useState(false);
  const [d, setD] = useState(null);
  const [loading, setLoading] = useState(false);
  const seq = useRef(0);

  useEffect(() => {
    const my = ++seq.current;
    const range = _rangeOf(preset);
    setLoading(true);
    axios.get(`${API}/admin/reports/category-insights`, {
      ...auth(),
      params: { start_date: range.from, end_date: range.to, source: source || undefined, months: 6, min_date: REPORT_MIN_DATE },
    })
      .then((r) => { if (my === seq.current) setD(r.data); })
      .catch((e) => { if (my === seq.current) toast.error(e?.response?.data?.detail || "Kategori satışları yüklenemedi"); })
      .finally(() => { if (my === seq.current) setLoading(false); });
  }, [preset, source]);

  const rows = d?.period || [];
  const months = d?.months || [];
  const series = (d?.series || []).filter((s) => s.total_qty > 0 || s.total_revenue > 0);
  const shownSeries = showAll ? series : series.slice(0, 15);
  const cellVals = shownSeries.flatMap((s) => (metric === "qty" ? s.qty : s.revenue));
  const cellMax = Math.max(1, ...cellVals);
  const fmtCell = (v) => (metric === "qty" ? fmtNum(v) : (v >= 1000 ? `₺${Math.round(v / 1000).toLocaleString("tr-TR")}B` : fmtMoney(v)));
  const monthlyExcel = () => {
    const cols = [{ k: "category", l: "Kategori" },
      ...months.map((m, i) => ({ k: `m${i}`, l: m.label, type: metric === "qty" ? "num" : "money" })),
      { k: "total", l: "Toplam", type: metric === "qty" ? "num" : "money" }];
    const out = series.map((s) => {
      const vals = metric === "qty" ? s.qty : s.revenue;
      const o = { category: s.category, total: metric === "qty" ? s.total_qty : s.total_revenue };
      vals.forEach((v, i) => { o[`m${i}`] = v; });
      return o;
    });
    const foot = { category: "Ay toplamı" };
    months.forEach((m, i) => { foot[`m${i}`] = metric === "qty" ? m.qty : m.revenue; });
    downloadXlsx(`Aylara Göre Kategori ${metric === "qty" ? "Adet" : "Ciro"}`, cols, out, foot);
  };

  return (
    <section className="space-y-5" data-testid="category-sales">
      <div className="flex flex-wrap items-center gap-3">
        <h3 className="text-base font-medium text-gray-900">Kategori Bazlı Satış</h3>
        <PresetBar preset={preset} setPreset={setPreset}>
          <select value={source} onChange={(e) => setSource(e.target.value)}
            className="h-8 border border-gray-200 rounded-md text-xs px-2 bg-white">
            {_CS_SOURCES.map((s) => <option key={s.key} value={s.key}>{s.label}</option>)}
          </select>
        </PresetBar>
        {loading && <span className="text-xs text-gray-500 inline-flex items-center gap-1"><RefreshCw className="w-3 h-3 animate-spin" /> Hesaplanıyor…</span>}
      </div>

      {d && (
        <>
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
            <GradTile grad="from-indigo-600 to-indigo-500" icon={Zap}
              label="En Çok Satan Kategori (adet)" value={d.top_by_qty?.category || "—"}
              sub={d.top_by_qty ? `${fmtNum(d.top_by_qty.qty)} adet · ${fmtPct(d.top_by_qty.qty_share)}` : null} />
            <GradTile grad="from-rose-600 to-rose-500" icon={TrendingUp}
              label="En Çok Ciro Getiren" value={d.top_by_revenue?.category || "—"}
              sub={d.top_by_revenue ? `${fmtMoney(d.top_by_revenue.revenue)} · ${fmtPct(d.top_by_revenue.revenue_share)}` : null} />
            <GradTile grad="from-slate-900 to-slate-700" icon={Package}
              label="Net Satış Adedi" value={fmtNum(d.totals?.qty)} sub={`${fmtNum(d.totals?.categories)} kategori`} />
            <GradTile grad="from-emerald-600 to-emerald-500" icon={Wallet}
              label="Net Ciro" value={fmtMoney(d.totals?.revenue)} sub="İptal & iade düşülmüş" />
          </div>

          <DataTable testid="category-sales-table" title="Kategori Bazlı Satış Tablosu" cols={_CS_COLS} rows={rows}
            rowKey={(r) => r.category} initialSort={{ k: "qty", dir: "desc" }} empty="Bu dönemde satış yok"
            footer={{ category: "Toplam", qty: d.totals?.qty, qty_share: 100, revenue: d.totals?.revenue, revenue_share: 100 }} />

          {d.compare && (
            <div className="grid md:grid-cols-2 gap-3">
              <TrendList title={`Hızlanan kategoriler · ${d.compare.cur}`} items={d.accelerating} up />
              <TrendList title={`Yavaşlayan kategoriler · ${d.compare.cur}`} items={d.slowing} />
            </div>
          )}

          <div className="bg-white border border-gray-200 rounded-xl overflow-hidden" data-testid="category-monthly">
            <div className="px-5 py-3 border-b flex flex-wrap items-center gap-3">
              <h4 className="text-base font-medium text-gray-900">Aylara Göre Kategori Satışları</h4>
              <div className="ml-auto flex items-center gap-2">
                {[["qty", "Adet"], ["revenue", "Ciro"]].map(([k, l]) => (
                  <button key={k} type="button" onClick={() => setMetric(k)}
                    className={`h-8 px-3 rounded-md text-xs font-semibold border ${metric === k ? "bg-gray-900 text-white border-gray-900" : "bg-white text-gray-700 border-gray-200"}`}>{l}</button>
                ))}
                <ExcelBtn testid="category-monthly-excel" onClick={monthlyExcel} />
              </div>
            </div>
            <div className="overflow-x-auto">
              <table className="w-full text-xs">
                <thead className="bg-gray-50 text-gray-600">
                  <tr>
                    <th className="px-3 py-2 text-left sticky left-0 bg-gray-50">Kategori</th>
                    {months.map((m) => (
                      <th key={m.key} className="px-2 py-2 text-center whitespace-nowrap">
                        {m.label}{m.partial ? <span className="text-gray-400"> (devam)</span> : null}
                      </th>
                    ))}
                    <th className="px-3 py-2 text-right">Toplam</th>
                  </tr>
                </thead>
                <tbody>
                  {shownSeries.map((s) => {
                    const vals = metric === "qty" ? s.qty : s.revenue;
                    return (
                      <tr key={s.category} className="border-t border-gray-100">
                        <td className="px-3 py-1.5 font-medium text-gray-900 sticky left-0 bg-white whitespace-nowrap">{s.category}</td>
                        {vals.map((v, i) => {
                          const k = v / cellMax;
                          return (
                            <td key={i} className="px-1 py-1 text-center">
                              <div title={`${s.category} · ${months[i]?.label}: ${fmtNum(s.qty[i])} adet · ${fmtMoney(s.revenue[i])}`}
                                className={`rounded py-1.5 tabular-nums ${k > 0.55 ? "text-white" : "text-gray-800"}`}
                                style={{ backgroundColor: v > 0 ? `rgba(79, 70, 229, ${0.08 + k * 0.82})` : "transparent" }}>
                                {v > 0 ? fmtCell(v) : "·"}
                              </div>
                            </td>
                          );
                        })}
                        <td className="px-3 py-1.5 text-right tabular-nums font-medium">
                          {metric === "qty" ? fmtNum(s.total_qty) : fmtMoney(s.total_revenue)}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
                <tfoot className="bg-gray-50 border-t font-medium">
                  <tr>
                    <td className="px-3 py-2 sticky left-0 bg-gray-50">Ay toplamı</td>
                    {months.map((m) => (
                      <td key={m.key} className="px-2 py-2 text-center tabular-nums">
                        {metric === "qty" ? fmtNum(m.qty) : fmtCell(m.revenue)}
                      </td>
                    ))}
                    <td />
                  </tr>
                </tfoot>
              </table>
            </div>
            {series.length > 15 && (
              <button type="button" onClick={() => setShowAll((v) => !v)}
                className="w-full text-xs text-gray-600 hover:text-gray-900 py-2 border-t">
                {showAll ? "İlk 15 kategoriyi göster" : `Tüm ${series.length} kategoriyi göster`}
              </button>
            )}
          </div>
        </>
      )}
    </section>
  );
}

function TrendList({ title, items, up }) {
  const Icon = up ? TrendingUp : TrendingDown;
  const excel = () => downloadXlsx(up ? "Hızlanan Kategoriler" : "Yavaşlayan Kategoriler",
    [{ k: "category", l: "Kategori" }, { k: "prev", l: "Önceki Ay Adet", type: "num" },
     { k: "cur", l: "Bu Ay Adet", type: "num" }, { k: "change", l: "Değişim", type: "num" },
     { k: "pct", l: "Değişim %", type: "pct" }], items || []);
  return (
    <div className="bg-white border border-gray-200 rounded-xl overflow-hidden">
      <div className={`px-4 py-2 border-b text-sm font-medium flex items-center gap-2 ${up ? "text-emerald-800 bg-emerald-50" : "text-rose-800 bg-rose-50"}`}>
        <Icon className="w-4 h-4" /> <span className="flex-1">{title}</span>
        <ExcelBtn onClick={excel} />
      </div>
      {(!items || items.length === 0) ? (
        <div className="px-4 py-4 text-xs text-gray-500">Kayda değer değişim yok.</div>
      ) : (
        <ul className="divide-y divide-gray-100">
          {items.map((it) => (
            <li key={it.category} className="px-4 py-2 flex items-center gap-3 text-sm">
              <span className="font-medium text-gray-900 flex-1 truncate">{it.category}</span>
              <span className="tabular-nums text-gray-500 text-xs">{fmtNum(it.prev)} → {fmtNum(it.cur)} adet</span>
              <span className={`tabular-nums text-xs font-semibold w-24 text-right ${up ? "text-emerald-700" : "text-rose-700"}`}>
                {up ? "▲" : "▼"} {it.change > 0 ? "+" : ""}{fmtNum(it.change)}{it.pct != null ? ` (${it.pct > 0 ? "+" : ""}${fmtDec(it.pct, 0)}%)` : ""}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
