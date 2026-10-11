/**
 * Influencer CRM & ROI — iki sekme:
 *   1) Influencerlar  → kayıtlı influencer kartları (arama + ekle/düzenle + ROI detay)
 *   2) Ürün Gönderimleri → tüm gönderim geçmişi (kim, ne, ne zaman, paylaşıldı mı) +
 *      "Yeni Gönderim" (influencer seç → ürün seç → stok düş → kargo).
 */
import { useState, useEffect, useCallback, useMemo, useRef, createContext, useContext } from "react";
import { createPortal } from "react-dom";
import axios from "axios";
import { openAdminDocument } from "../../lib/adminDocuments";
import { toast } from "sonner";
import {
  Plus, TrendingUp, CheckCircle, Trash2, X,
  Instagram, DollarSign, Truck, Share2, Search, Pencil, Calendar, Package,
  ClipboardList, ExternalLink, History, Filter, Download,
  ChevronRight, ChevronDown, Barcode, Printer, FileText, StickyNote, PackagePlus,
} from "lucide-react";

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;
const auth = () => ({ headers: { Authorization: `Bearer ${localStorage.getItem("token")}` } });
const money = (n) => `${(Number(n) || 0).toLocaleString("tr-TR", { minimumFractionDigits: 0, maximumFractionDigits: 2 })} TL`;
const fmtDate = (s) => {
  if (!s) return "";
  try {
    const d = new Date(s);
    if (isNaN(d.getTime())) return String(s).slice(0, 10);
    return d.toLocaleDateString("tr-TR", { day: "2-digit", month: "2-digit", year: "numeric" });
  } catch { return String(s).slice(0, 10); }
};

/* İŞ BİRLİĞİ DURUMU (Kadir): influencer adının göründüğü HER yerde 3 renk — soluk; tıklanan dolar.
 *   kırmızı = ürün gönderildi ama paylaşmadı, bir daha çalışılmaz
 *   sarı    = çalışma devam edebilir
 *   yeşil   = iyi etkileşim, sürekli çalışılabilir
 * Tek kaynak: influencer kaydı (rating). Tüm sekmeler aynı haritayı paylaşır; aynı renge tekrar
 * basmak işareti kaldırır. Adla eşleşme yalnız influencer_id olmayan eski kayıtlar için. */
const RATING_OPTS = [
  { k: "red", c: "#dc2626", t: "Kırmızı — ürün gönderildi, paylaşmadı · bir daha çalışılmaz" },
  { k: "yellow", c: "#eab308", t: "Sarı — çalışma devam edebilir" },
  { k: "green", c: "#16a34a", t: "Yeşil — iyi etkileşim · sürekli çalışılabilir" },
];
const _normName = (s) => String(s || "").trim().toLocaleLowerCase("tr");
const RatingCtx = createContext(null);

function RatingProvider({ children }) {
  const [byId, setById] = useState({});
  const [idByName, setIdByName] = useState({});
  const load = useCallback(() => {
    axios.get(`${API}/influencers`, auth()).then(({ data }) => {
      const m = {}, n = {};
      (data.influencers || []).forEach((i) => {
        if (!i.id) return;
        m[i.id] = i.rating || null;
        if (i.name) n[_normName(i.name)] = i.id;
      });
      setById(m); setIdByName(n);
    }).catch(() => {});
  }, []);
  useEffect(() => { load(); }, [load]);
  const value = useMemo(() => ({ byId, idByName, reload: load }), [byId, idByName, load]);
  return <RatingCtx.Provider value={value}>{children}</RatingCtx.Provider>;
}

// SALT GÖSTERİM: yalnız SEÇİLİ rengin noktası (seçim yoksa hiçbir şey). Renk yalnız
// Kayıtlı Influencerlar › Düzenle formundan (RatingPicker) seçilir.
function RatingDots({ id, name, rating, size = 9, className = "" }) {
  const ctx = useContext(RatingCtx);
  let cur = rating || null;
  if (ctx) {
    const iid = id || ctx.idByName[_normName(name)];
    if (iid && iid in ctx.byId) cur = ctx.byId[iid] || null;
  }
  const o = RATING_OPTS.find((x) => x.k === cur);
  if (!o) return null;
  return (
    <span className={`inline-block rounded-full shrink-0 align-middle ${className}`}
      style={{ width: size, height: size, background: o.c }} title={o.t} data-testid="inf-rating-dot" />
  );
}

// Düzenleme formundaki seçici: 3 renk soluk, seçili dolu; aynı renge tekrar basmak kaldırır.
function RatingPicker({ value, onChange }) {
  return (
    <div className="flex items-center gap-2" data-testid="inf-rating-picker">
      {RATING_OPTS.map((o) => {
        const on = value === o.k;
        return (
          <button
            key={o.k}
            type="button"
            title={on ? `${o.t} (kaldırmak için tekrar basın)` : o.t}
            aria-pressed={on}
            onClick={() => onChange(on ? null : o.k)}
            className="rounded-full transition-all hover:scale-110"
            style={{ width: 22, height: 22, background: o.c, opacity: on ? 1 : 0.22,
                     boxShadow: on ? `0 0 0 2px #fff, 0 0 0 4px ${o.c}` : "none" }}
          />
        );
      })}
      <span className="text-xs text-gray-500 ml-1">{(RATING_OPTS.find((o) => o.k === value) || {}).t || "Seçilmedi"}</span>
    </div>
  );
}

/* Kampanya/gönderim aksiyonları — hem detay modalı hem gönderim geçmişi aynı davranışı kullanır. */
function makeCampaignActions(reload) {
  return {
    createCargo: async (cid) => {
      try {
        const r = await axios.post(`${API}/influencer-campaigns/${cid}/cargo`, {}, auth());
        toast.success(`Kargo barkodu: ${r.data.cargo_barcode}`);
        reload();
      } catch (e) { toast.error(e.response?.data?.detail || "Kargo oluşturulamadı"); }
    },
    toggleShared: async (c) => {
      try { await axios.put(`${API}/influencer-campaigns/${c.id}`, { shared: !c.shared }, auth()); reload(); }
      catch { toast.error("Güncellenemedi"); }
    },
    markSent: async (cid) => {
      try {
        await axios.put(`${API}/influencer-campaigns/${cid}`, { sent_at: new Date().toISOString(), status: "shipped" }, auth());
        toast.success("Gönderildi olarak işaretlendi"); reload();
      } catch { toast.error("Güncellenemedi"); }
    },
    saveContentUrl: async (cid, url) => {
      try { await axios.put(`${API}/influencer-campaigns/${cid}`, { content_url: url }, auth()); toast.success("İçerik linki kaydedildi"); reload(); }
      catch { toast.error("Kaydedilemedi"); }
    },
    uncommitStock: async (cid) => {
      if (!window.confirm("Bu gönderimin ürünleri stoğa GERİ yüklensin mi?")) return;
      try { await axios.post(`${API}/influencer-campaigns/${cid}/uncommit-products`, {}, auth()); toast.success("Stok geri yüklendi"); reload(); }
      catch (e) { toast.error(e.response?.data?.detail || "Geri alınamadı"); }
    },
    delCampaign: async (cid) => {
      if (!window.confirm("Gönderim silinsin mi? (Düşülen stok varsa otomatik geri yüklenir)")) return;
      await axios.delete(`${API}/influencer-campaigns/${cid}`, auth()); reload();
    },
  };
}

export default function Influencers() {
  const [tab, setTab] = useState("pr"); // 'pr' | 'influencers' | 'shipments'

  return (
    <div className="p-6 w-full" data-testid="influencers-page">
      <div className="mb-4">
        <h1 className="text-2xl font-bold flex items-center gap-2">
          <Instagram className="text-pink-600" size={24} /> Influencer / İş Birlikleri
        </h1>
        <p className="text-sm text-gray-500 mt-1">
          PR takip, influencer kayıtları, seeding gönderimleri, kargo otomasyonu ve ROI takibi.
        </p>
      </div>

      {/* Sekmeler */}
      <div className="flex gap-2 border-b mb-5">
        <TabBtn active={tab === "pr"} onClick={() => setTab("pr")} icon={<ClipboardList size={15} />} testid="tab-pr">
          Gönderi Takibi
        </TabBtn>
        <TabBtn active={tab === "shipments"} onClick={() => setTab("shipments")} icon={<Package size={15} />} testid="tab-shipments">
          Takvim
        </TabBtn>
        <TabBtn active={tab === "influencers"} onClick={() => setTab("influencers")} icon={<Instagram size={15} />} testid="tab-influencers">
          Kayıtlı Influencerlar
        </TabBtn>
      </div>

      <RatingProvider>
        {tab === "pr" ? <PRTrackTab /> : tab === "influencers" ? <InfluencerListTab /> : <ShipmentsTab />}
      </RatingProvider>
    </div>
  );
}

/* ======================= SEKME 0: PR TAKİP =======================
 * Kadir: haftalık PR listesi — her işlem TEK TEK "sipariş gibi" ayrı kart, alt alta.
 * Tarih filtresi + günlük/haftalık/aylık/yıllık sayaç. TikTok/Insta otomatik linkli.
 * Yan panel: bir influencerla geçmiş (ne gönderdik + PR işlemleri). STOK HAREKETİ YOK. */

// TikTok orijinal glyph (lucide'de marka ikonu yok — inline SVG, currentColor ile renk alır).
function TikTokIcon({ size = 12, className = "" }) {
  return (
    <svg viewBox="0 0 24 24" width={size} height={size} fill="currentColor" className={className} aria-hidden="true">
      <path d="M16.6 5.82A4.28 4.28 0 0 1 15.54 3h-3.09v12.4a2.59 2.59 0 1 1-2.59-2.59c.27 0 .53.04.77.12V9.79a5.7 5.7 0 0 0-.77-.05 5.69 5.69 0 1 0 5.69 5.69V8.9a7.32 7.32 0 0 0 4.3 1.38V7.19a4.28 4.28 0 0 1-3.25-1.37z" />
    </svg>
  );
}

const PR_STATUS = [
  { v: "beklemede", l: "Beklemede", c: "bg-gray-100 text-gray-600" },
  { v: "iletildi", l: "İletildi", c: "bg-blue-50 text-blue-700" },
  { v: "cevap_bekleniyor", l: "Cevap Bekleniyor", c: "bg-amber-50 text-amber-700" },
  { v: "olumlu", l: "Olumlu", c: "bg-green-50 text-green-700" },
  { v: "olumsuz", l: "Olumsuz", c: "bg-red-50 text-red-700" },
  { v: "gonderildi", l: "Gönderildi", c: "bg-indigo-50 text-indigo-700" },
  { v: "yayinlandi", l: "Yayınlandı", c: "bg-emerald-50 text-emerald-700" },
  { v: "iptal", l: "İptal", c: "bg-gray-100 text-gray-400 line-through" },
];
const prStatusMeta = (v) => PR_STATUS.find((s) => s.v === v) || PR_STATUS[0];

// Kullanıcı adını (@x veya x) tam profil linkine çevirir.
const cleanHandle = (h) => String(h || "").trim().replace(/^@+/, "").replace(/\s+/g, "");
const socialUrl = (kind, h) => {
  const u = cleanHandle(h);
  if (!u) return null;
  if (/^https?:\/\//i.test(h)) return h;
  return kind === "tiktok" ? `https://www.tiktok.com/@${u}` : `https://instagram.com/${u}`;
};

function SocialLinks({ instagram, tiktok, size = 12 }) {
  const ig = socialUrl("instagram", instagram);
  const tk = socialUrl("tiktok", tiktok);
  if (!ig && !tk) return null;
  return (
    <span className="flex items-center gap-2">
      {ig && (
        <a href={ig} target="_blank" rel="noreferrer" onClick={(e) => e.stopPropagation()}
           className="inline-flex items-center gap-1 text-pink-600 hover:underline">
          <Instagram size={size} /> {cleanHandle(instagram)} <ExternalLink size={size - 3} />
        </a>
      )}
      {tk && (
        <a href={tk} target="_blank" rel="noreferrer" onClick={(e) => e.stopPropagation()}
           className="inline-flex items-center gap-1 text-gray-800 hover:underline">
          <TikTokIcon size={size} /> {cleanHandle(tiktok)} <ExternalLink size={size - 3} />
        </a>
      )}
    </span>
  );
}

function periodStart(kind) {
  const now = new Date();
  const d = new Date(now);
  if (kind === "today") { d.setHours(0, 0, 0, 0); return d.toISOString().slice(0, 10); }
  if (kind === "week") { const wd = (d.getDay() + 6) % 7; d.setDate(d.getDate() - wd); return d.toISOString().slice(0, 10); }
  if (kind === "month") return new Date(now.getFullYear(), now.getMonth(), 1).toISOString().slice(0, 10);
  if (kind === "year") return new Date(now.getFullYear(), 0, 1).toISOString().slice(0, 10);
  return "";
}

function PRTrackTab() {
  const [entries, setEntries] = useState([]);
  const [summary, setSummary] = useState({});
  const [loading, setLoading] = useState(true);
  const [q, setQ] = useState("");
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [statusF, setStatusF] = useState("");
  const [showForm, setShowForm] = useState(false);
  const [editTarget, setEditTarget] = useState(null);
  const [historyFor, setHistoryFor] = useState(null); // {id, name}
  const [colF, setColF] = useState({});               // sütun filtreleri (istemci taraflı)
  const setF = (k, v) => setColF((p) => ({ ...p, [k]: v }));
  const clearF = () => setColF({});
  const anyColF = Object.values(colF).some(Boolean);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const params = {};
      if (q.trim()) params.q = q.trim();
      if (start) params.start_date = start;
      if (end) params.end_date = `${end}T23:59:59.999999`;
      if (statusF) params.status = statusF;
      const r = await axios.get(`${API}/influencer-pr`, { ...auth(), params });
      setEntries(r.data?.entries || []);
      setSummary(r.data?.summary || {});
    } catch {
      toast.error("PR kayıtları yüklenemedi");
    } finally {
      setLoading(false);
    }
  }, [q, start, end, statusF]);

  useEffect(() => { const t = setTimeout(load, 300); return () => clearTimeout(t); }, [load]);

  const del = async (id) => {
    if (!window.confirm("Bu gönderi kaydı silinsin mi?")) return;
    try { await axios.delete(`${API}/influencer-pr/${id}`, auth()); toast.success("Silindi"); load(); }
    catch { toast.error("Silinemedi"); }
  };

  // Inline düzenleme (Paylaşma Tarihi / Not / İletişim Tarihi) — kısmi PUT, sonra tazele.
  const patchEntry = async (id, patch) => {
    try { await axios.put(`${API}/influencer-pr/${id}`, patch, auth()); load(); }
    catch { toast.error("Kaydedilemedi"); }
  };
  // Kalem-bazlı "Paylaştı" — yalnız o ürün kaleminin shared'ı (diğer kalemler etkilenmez).
  const patchItemShared = async (id, index, shared) => {
    try { await axios.put(`${API}/influencer-pr/${id}/item-shared`, { index, shared }, auth()); load(); }
    catch { toast.error("Kaydedilemedi"); }
  };

  const quick = (kind) => {
    if (kind === "yesterday") {
      const y = new Date(Date.now() - 864e5);
      const s = `${y.getFullYear()}-${String(y.getMonth() + 1).padStart(2, "0")}-${String(y.getDate()).padStart(2, "0")}`;
      setStart(s); setEnd(s); return;
    }
    setStart(periodStart(kind)); setEnd("");
  };

  // Takip taraması — saatlik cron'un yaptığı işi ŞİMDİ çalıştırır; sonucu (bulunan / hata / son hata)
  // toast + üst bilgi satırında gösterir (teşhis için: MNG neden takip no vermiyor görünür olsun).
  const [scanning, setScanning] = useState(false);
  const [trackHealth, setTrackHealth] = useState(null);
  const loadTrackHealth = useCallback(async () => {
    try { const r = await axios.get(`${API}/influencer-pr/tracking-health`, auth()); setTrackHealth(r.data || null); } catch { /* sessiz */ }
  }, []);
  useEffect(() => { loadTrackHealth(); }, [loadTrackHealth]);
  const runTrackingScan = async () => {
    setScanning(true);
    const t = toast.loading("DHL/MNG takip sorgulanıyor…");
    try {
      const r = await axios.post(`${API}/influencer-pr/tracking-scan`, {}, auth());
      const d = r.data || {};
      const msg = `Aday ${d.candidates ?? 0} · sorgulanan ${d.checked ?? 0} · takip no bulunan ${d.found ?? 0} · hata ${d.errors ?? 0}` + (d.last_error ? ` · son hata: ${d.last_error}` : "");
      if (d.status === "inactive") toast.error(d.last_error || "MNG/DHL entegrasyonu aktif değil", { id: t });
      else if ((d.found ?? 0) > 0) toast.success(msg, { id: t });
      else toast(msg, { id: t, duration: 9000 });
      setTrackHealth(d); load();
    } catch (err) { toast.error(err.response?.data?.detail || "Takip taraması çalıştırılamadı", { id: t }); }
    finally { setScanning(false); }
  };

  // Excel'e aktar — ekrandaki AYNI filtreyle (durum/tarih/arama). Auth header gerektiği
  // için blob olarak çekip indiriyoruz (window.open header taşımaz).
  const exportXlsx = async (withImages = true) => {
    try {
      const params = {};
      if (!withImages) params.with_images = false;   // görselsiz: "Görsel" sütunu yok, hızlı
      if (q.trim()) params.q = q.trim();
      if (start) params.start_date = start;
      if (end) params.end_date = `${end}T23:59:59.999999`;
      if (statusF) params.status = statusF;
      const r = await axios.get(`${API}/influencer-pr/export`, { ...auth(), params, responseType: "blob" });
      const url = URL.createObjectURL(r.data);
      const a = document.createElement("a");
      a.href = url; a.download = withImages ? "gonderi-takibi.xlsx" : "gonderi-takibi-gorselsiz.xlsx"; a.click();
      URL.revokeObjectURL(url);
    } catch { toast.error("Excel oluşturulamadı"); }
  };

  // Kamyon: PR'daki ürünleri KARGOYA VER — mevcut influencer kargo akışını kullanır
  // (kampanya oluştur → ürünleri işle [STOK DÜŞER] → MNG barkod + takip). PR kaydına işlenir.
  const shipPR = async (e) => {
    if (!e.influencer_id) return toast.error("Kargo için PR kaydı bir kayıtlı influencer'a bağlı olmalı");
    if (!(e.products && e.products.length)) return toast.error("Önce ürün ekleyin (ürünü ara → beden seç)");
    if (e.cargo_barcode) return toast(`Zaten kargolandı · barkod ${e.cargo_barcode}`);
    if (!window.confirm("Bu ürünler kargoya verilsin mi? STOK DÜŞÜLECEK ve MNG barkodu oluşturulacak.")) return;
    const t = toast.loading("Kargo oluşturuluyor…");
    try {
      // İdempotent backend ucu: kampanya → stok düşümü → MNG barkod + takip; gönderim
      // tarihi/durumu PR kaydına işler. Tekrar tıklamada çift stok düşümü YAPMAZ.
      const r = await axios.post(`${API}/influencer-pr/${e.id}/ship`, {}, auth());
      toast.success(`Kargo oluşturuldu · barkod ${r.data?.cargo_barcode || "—"} · stok düşüldü`, { id: t });
      load();
    } catch (err) {
      toast.error(err.response?.data?.detail || "Kargo oluşturulamadı", { id: t });
    }
  };

  // AYNI KİŞİYE YENİ KARGO (kullanıcı isteği): mevcut kaydı bozmadan yeni gönderi açar,
  // ürünler yeniden seçilir ve STOK TEKRAR DÜŞER (backend /reship → yeni kampanya).
  const [reshipFor, setReshipFor] = useState(null);   // {id, name}

  // İstemci-taraflı sütun filtreleri (yüklü kayıtlar üzerinde) — İş Birliği/Platform/Ürün/Beden/
  // Durum/Paylaştı/Not. Üst arama+tarih+durum sunucudan; bunlar ekrandaki tabloyu daraltır.
  const _plat = (e) => e.platform || (e.instagram ? "İnstagram" : e.tiktok ? "Tiktok" : "");
  const _sharedAny = (e) => (Array.isArray(e.products) && e.products.length)
    ? e.products.some((p) => p.shared) : !!e.shared;
  const anlasmaOpts = useMemo(() =>
    Array.from(new Set(entries.map((e) => e.anlasma_sekli).filter(Boolean)))
      .sort((a, b) => String(a).localeCompare(String(b), "tr")), [entries]);
  const fEntries = useMemo(() => {
    const f = colF;
    const inc = (v, qq) => String(v || "").toLocaleLowerCase("tr").includes(String(qq).toLocaleLowerCase("tr"));
    const prodMatch = (e, qq) => (e.products || []).some((p) => inc(p.name, qq) || inc(p.barcode, qq)) || inc(e.urun, qq);
    const bedenMatch = (e, qq) => (e.products || []).some((p) => inc(p.size, qq)) || inc(e.beden, qq);
    return entries.filter((e) =>
      (!f.name || inc(e.influencer_name, f.name)) &&
      (!f.anlasma || (e.anlasma_sekli || "") === f.anlasma) &&
      (!f.platform || _plat(e) === f.platform) &&
      (!f.urun || prodMatch(e, f.urun)) &&
      (!f.beden || bedenMatch(e, f.beden)) &&
      (!f.durum || (e.status || "") === f.durum) &&
      (!f.paylasti || (f.paylasti === "evet" ? _sharedAny(e) : !_sharedAny(e))) &&
      (!f.not || inc(e.note, f.not))
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [entries, colF]);

  return (
    <div data-testid="pr-track-tab">
      {/* Dönem sayaçları */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-4">
        <Stat icon={<Calendar size={16} />} label="Bugün" value={summary.today ?? 0} color="blue" />
        <Stat icon={<Calendar size={16} />} label="Bu Hafta" value={summary.week ?? 0} color="green" />
        <Stat icon={<Calendar size={16} />} label="Bu Ay" value={summary.month ?? 0} color="blue" />
        <Stat icon={<Calendar size={16} />} label="Bu Yıl" value={summary.year ?? 0} color="green" />
      </div>

      {/* Filtre çubuğu */}
      <div className="flex items-center gap-2 mb-3 flex-wrap">
        <div className="relative flex-1 min-w-[200px] max-w-xs">
          <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" />
          <input value={q} onChange={(e) => setQ(e.target.value)} data-testid="pr-search"
                 placeholder="Influencer, @kullanıcı, not, teklif ara…"
                 className="w-full border rounded-lg pl-9 pr-3 py-2 text-sm focus:outline-none focus:border-black" />
        </div>
        <div className="flex items-center gap-1 text-xs">
          <Filter size={14} className="text-gray-400" />
          <input type="date" value={start} onChange={(e) => setStart(e.target.value)} className="border rounded-lg px-2 py-1.5" data-testid="pr-start" />
          <span className="text-gray-400">–</span>
          <input type="date" value={end} onChange={(e) => setEnd(e.target.value)} className="border rounded-lg px-2 py-1.5" data-testid="pr-end" />
        </div>
        <div className="flex gap-1 text-xs">
          {[["today", "Bugün"], ["yesterday", "Dün"], ["week", "Hafta"], ["month", "Ay"], ["year", "Yıl"]].map(([k, l]) => (
            <button key={k} onClick={() => quick(k)} className="px-2 py-1.5 border rounded-lg hover:bg-gray-50">{l}</button>
          ))}
          {(start || end) && <button onClick={() => { setStart(""); setEnd(""); }} className="px-2 py-1.5 border rounded-lg text-gray-500 hover:bg-gray-50">Temizle</button>}
        </div>
        <select value={statusF} onChange={(e) => setStatusF(e.target.value)} className="border rounded-lg px-2 py-1.5 text-xs" data-testid="pr-status-filter">
          <option value="">Tüm durumlar</option>
          {PR_STATUS.map((s) => <option key={s.v} value={s.v}>{s.l}</option>)}
        </select>
        <button onClick={runTrackingScan} disabled={scanning} data-testid="pr-track-scan-btn"
                title="Takip no'ları DHL/MNG'den ŞİMDİ sorgula (saatlik otomatik taramayla aynı iş) — sonuç ve varsa hata burada gösterilir"
                className="inline-flex items-center gap-2 border px-3 py-2 rounded-lg text-sm hover:bg-gray-50 ml-auto disabled:opacity-50">
          <Truck size={15} /> {scanning ? "Taranıyor…" : "Takip Tara"}
        </button>
        <button onClick={() => exportXlsx(true)} data-testid="pr-export-btn"
                className="inline-flex items-center gap-2 border px-3 py-2 rounded-lg text-sm hover:bg-gray-50">
          <Download size={15} /> Excel'e Aktar
        </button>
        <button onClick={() => exportXlsx(false)} data-testid="pr-export-noimg-btn"
                className="inline-flex items-center gap-2 border px-3 py-2 rounded-lg text-sm hover:bg-gray-50">
          <Download size={15} /> Excel (Görselsiz)
        </button>
        <button onClick={() => { setEditTarget(null); setShowForm(true); }} data-testid="new-pr-btn"
                className="inline-flex items-center gap-2 bg-black text-white px-4 py-2 rounded-lg text-sm hover:bg-gray-800">
          <Plus size={16} /> Yeni PR Kaydı
        </button>
      </div>

      {/* İşlem listesi — alt alta, her biri "sipariş gibi"

          SAYFA BAŞA ATMASI DÜZELTMESİ: barkod çıkarma / irsaliye gibi bir işlemden sonra
          load() çağrılınca loading=true oluyor ve tablo yerini "Yükleniyor..." yazısına
          bırakıyordu. Liste bir anlığına yok olunca sayfa yüksekliği çöküyor, tarayıcı da
          kaydırmayı en başa alıyordu; kullanıcı aynı satırı baştan aramak zorunda kalıyordu.
          Artık elde veri VARKEN tablo ekranda kalıyor (yalnız hafifçe soluyor), böylece
          yükseklik korunuyor ve kaydırma yerinde duruyor. "Yükleniyor..." yalnız ilk
          açılışta (henüz hiç kayıt yokken) gösteriliyor. */}
      {loading && entries.length === 0 ? (
        <div className="text-gray-400 text-sm py-12 text-center">Yükleniyor...</div>
      ) : entries.length === 0 ? (
        <div className="border border-dashed rounded-xl py-16 text-center text-gray-500">
          Kayıt yok. "Yeni PR Kaydı" ile ekleyin.
        </div>
      ) : (
        <div className={`overflow-x-auto border rounded-xl bg-white transition-opacity ${loading ? "opacity-60" : ""}`}
             data-testid="pr-list" aria-busy={loading}>
          {trackHealth && (
            <div className="px-3 py-1.5 text-[11px] text-gray-500 border-b bg-gray-50/60 flex flex-wrap gap-x-4 gap-y-1" data-testid="pr-track-health">
              <span>Otomatik takip taraması (saatlik): <b className={trackHealth.status === "ok" ? "text-emerald-700" : "text-amber-700"}>{trackHealth.status || "bilinmiyor"}</b></span>
              {trackHealth.last_run_at && <span>son çalışma {new Date(trackHealth.last_run_at).toLocaleString("tr-TR")}</span>}
              <span>aday {trackHealth.candidates ?? 0} · sorgulanan {trackHealth.checked ?? 0} · bulunan {trackHealth.found ?? 0} · hata {trackHealth.errors ?? 0}</span>
              {trackHealth.last_error && <span className="text-red-600">son hata: {trackHealth.last_error}</span>}
            </div>
          )}
          <table className="w-full text-sm min-w-[920px]">
            <thead className="bg-gray-50 text-[10px] uppercase tracking-wide text-gray-500 text-left">
              <tr>
                {["Influencer", "İş Birliği", "İletişim", "Ürün", "Beden", "Gönderim Tarihi",
                  "Paylaştı", "İşlemler"].map((h, i) => (
                  <th key={i} className="px-2 py-2 font-semibold whitespace-nowrap">{h}</th>
                ))}
              </tr>
              {/* SÜTUN FİLTRELERİ (istemci) — İş Birliği/Platform/Ürün/Beden/Durum/Paylaştı/Not */}
              <tr className="bg-white border-t text-[11px] normal-case tracking-normal">
                <th className="px-1.5 py-1.5"><FTxt v={colF.name} onCh={(v) => setF("name", v)} ph="Influencer…" /></th>
                <th className="px-1.5 py-1.5"><FSel v={colF.anlasma} onCh={(v) => setF("anlasma", v)} options={anlasmaOpts} /></th>
                <th className="px-1.5 py-1.5"><FSel v={colF.platform} onCh={(v) => setF("platform", v)} options={["İnstagram", "Tiktok"]} /></th>
                <th className="px-1.5 py-1.5"><FTxt v={colF.urun} onCh={(v) => setF("urun", v)} ph="Ürün/barkod…" /></th>
                <th className="px-1.5 py-1.5"><FTxt v={colF.beden} onCh={(v) => setF("beden", v)} ph="Beden…" /></th>
                <th className="px-1.5 py-1.5"></th>
                <th className="px-1.5 py-1.5">
                  <select value={colF.paylasti || ""} onChange={(e) => setF("paylasti", e.target.value)}
                    className="w-full border rounded px-1.5 py-1 text-[11px] font-normal bg-white focus:outline-none focus:border-black">
                    <option value="">Tümü</option>
                    <option value="evet">Paylaşan</option>
                    <option value="hayir">Paylaşmayan</option>
                  </select>
                </th>
                <th className="px-1.5 py-1.5 text-right">
                  {anyColF && <button onClick={clearF} className="text-[11px] text-gray-500 hover:text-black underline whitespace-nowrap">Temizle</button>}
                </th>
              </tr>
            </thead>
            <tbody>
              {fEntries.length === 0 && (
                <tr><td colSpan={8} className="px-3 py-8 text-center text-gray-400 text-sm">
                  Filtrelerle eşleşen kayıt yok. {anyColF && <button onClick={clearF} className="underline hover:text-black">Temizle</button>}
                </td></tr>
              )}
              {fEntries.map((e) => (
                <PRRow key={e.id} e={e}
                       onEdit={() => { setEditTarget(e); setShowForm(true); }}
                       onDelete={() => del(e.id)}
                       onShip={() => shipPR(e)}
                       onReship={() => setReshipFor({ id: e.id, name: e.influencer_name || "influencer" })}
                       onPatch={patchEntry}
                       onItemShared={patchItemShared}
                       onHistory={() => e.influencer_id && setHistoryFor({ id: e.influencer_id, name: e.influencer_name })} />
              ))}
            </tbody>
          </table>
        </div>
      )}

      {showForm && (
        <PRFormModal initial={editTarget} onClose={() => setShowForm(false)}
                     onSaved={() => { setShowForm(false); load(); }} />
      )}
      {reshipFor && (
        <ReshipModal target={reshipFor} onClose={() => setReshipFor(null)}
                     onDone={() => { setReshipFor(null); load(); }} />
      )}
      {historyFor && (
        <HistoryPanel influencerId={historyFor.id} influencerName={historyFor.name}
                      onClose={() => setHistoryFor(null)} />
      )}
    </div>
  );
}

/* AYNI KİŞİYE YENİ KARGO — mevcut gönderiyi bozmadan yeni gönderi açar.
   Ürünler seçilir → backend /reship: yeni PR kaydı + yeni kampanya + STOK TEKRAR DÜŞER + MNG barkodu. */
function ReshipModal({ target, onClose, onDone }) {
  const [picked, setPicked] = useState([]);
  const [note, setNote] = useState("");
  const [fee, setFee] = useState("");
  const [saving, setSaving] = useState(false);
  // Aynı kişiye tekrar gönderirken kayıtlı üst/alt bedeni göster (ilk gönderide
  // beden yanlış gittiyse burada düzeltiliyor — en çok lazım olduğu yer).
  const [infList, setInfList] = useState([]);
  useEffect(() => {
    (async () => {
      try { const r = await axios.get(`${API}/influencers`, auth()); setInfList(r.data?.influencers || []); }
      catch { /* sessiz */ }
    })();
  }, []);
  const [reUst, reAlt] = useInfSizes(infList, target?.influencer_id);
  const send = async () => {
    if (!picked.length) return toast.error("En az bir ürün seçin");
    if (!window.confirm(`${target.name} adlı kişiye YENİ kargo gönderilecek. Seçilen ürünler STOKTAN TEKRAR DÜŞÜLECEK. Onaylıyor musunuz?`)) return;
    setSaving(true);
    const t = toast.loading("Yeni kargo oluşturuluyor…");
    try {
      const r = await axios.post(`${API}/influencer-pr/${target.id}/reship`, {
        products: picked.map((p) => ({ barcode: p.barcode, qty: p.qty || 1, name: p.name, size: p.size })),
        note: note.trim(),
        fee_amount: fee.trim(),
      }, auth());
      toast.success(`Yeni kargo oluşturuldu · barkod ${r.data?.cargo_barcode || "—"} · stok düşüldü`, { id: t });
      onDone();
    } catch (err) {
      toast.error(err.response?.data?.detail || "Yeni kargo oluşturulamadı", { id: t });
    } finally { setSaving(false); }
  };
  return (
    <Modal title={`Yeni Kargo Gönder — ${target.name}`} onClose={onClose}>
      <p className="text-xs text-gray-500 mb-3">
        Aynı influencer'a <b>yeni bir gönderi</b> açılır (mevcut kayıt değişmez). Seçilen ürünler
        stoktan tekrar düşülür ve yeni bir MNG kargo barkodu üretilir.
      </p>
      <Field label="Ürün & Beden (ürünü ara → bedenini seç; birden çok eklenebilir)" full>
        <ProductPicker picked={picked} setPicked={setPicked} sizes={[reUst, reAlt]} />
        <BedenHint ust={reUst} alt={reAlt} />
      </Field>
      <Field label="Anlaşılan Ücret (₺) (opsiyonel)" full>
        <input className="inp" inputMode="decimal" value={fee} onChange={(e) => setFee(e.target.value)}
               placeholder="Bu gönderi için ayrıca ücret ödendiyse girin" data-testid="reship-fee" />
      </Field>
      <Field label="Not (opsiyonel)" full>
        <input className="inp" value={note} onChange={(e) => setNote(e.target.value)}
               placeholder="Ör. ilk gönderide beden büyük geldi" data-testid="reship-note" />
      </Field>
      <div className="flex justify-end gap-2 mt-4">
        <button onClick={onClose} className="px-4 py-2 text-sm border rounded">Vazgeç</button>
        <button onClick={send} disabled={saving || !picked.length} data-testid="reship-submit"
                className="px-4 py-2 text-sm bg-black text-white rounded disabled:opacity-40">
          {saving ? "Gönderiliyor…" : "Kargoyu Oluştur"}
        </button>
      </div>
    </Modal>
  );
}

// Ürün kalemi thumbnail'ı + hover büyük önizleme (yeni npm YOK — createPortal + fixed div).
// Görsel yoksa/kırıksa nötr gri placeholder (kırık görsel gösterilmez).
function PRThumb({ src, name }) {
  const [err, setErr] = useState(false);
  const [pv, setPv] = useState(null); // {top,left}
  const show = !!src && !err;
  const onEnter = (ev) => {
    if (!show) return;
    const r = ev.currentTarget.getBoundingClientRect();
    // Sağda yer yoksa sola aç (kırpılmasın).
    const openLeft = r.right + 220 > window.innerWidth;
    setPv({ top: Math.max(8, Math.min(r.top - 80, window.innerHeight - 224)), left: openLeft ? r.left - 212 : r.right + 8 });
  };
  return (
    <span className="relative shrink-0" onMouseEnter={onEnter} onMouseLeave={() => setPv(null)}>
      {show ? (
        <img src={src} alt={name || ""} loading="lazy" onError={() => setErr(true)}
          className="w-7 h-7 rounded object-cover bg-gray-100 border border-gray-200" data-testid="pr-thumb" />
      ) : (
        <span className="w-7 h-7 rounded bg-gray-100 border border-gray-200 block" aria-hidden data-testid="pr-thumb-empty" />
      )}
      {pv && show && createPortal(
        <div style={{ position: "fixed", top: pv.top, left: pv.left, zIndex: 90 }}
          className="pointer-events-none border border-gray-200 rounded-lg shadow-2xl bg-white p-1" data-testid="pr-thumb-preview">
          <img src={src} alt={name || ""} className="w-[200px] h-[200px] object-cover rounded" />
        </div>, document.body)}
    </span>
  );
}

// Gönderi Takibi satırı (Excel düzeni): görünür sütunlar + çoklu ürün kalemleri +
// detaya-basınca (expand) profil alanları + inline düzenlenebilir Paylaşma Tarihi/Not/İletişim Tarihi.
function PRRow({ e, onEdit, onDelete, onHistory, onShip, onReship, onPatch, onItemShared }) {
  const [open, setOpen] = useState(false);
  const [noteEdit, setNoteEdit] = useState(false);
  const td = "px-2 py-2 align-top";
  const items = Array.isArray(e.products) && e.products.length ? e.products : null;
  const canShip = (items && e.influencer_id);
  const barcoded = !!e.cargo_barcode;
  // GERÇEK DHL takip no'su YALNIZ cargo_gonderi_no'dur (MNG FaturaSiparisListesi'nden çekilen,
  // kargotakip.dhlecommerce.com.tr'de izlenebilir). cargo_barcode = MNG iç barkodu → takip no DEĞİL.
  const trackNo = (e.cargo_gonderi_no || "").trim();
  const trackUrl = e.cargo_tracking_url || (trackNo ? `https://kargotakip.dhlecommerce.com.tr/?takipNo=${trackNo}` : "");
  const platform = e.platform || (e.instagram ? "İnstagram" : e.tiktok ? "Tiktok" : "—");
  const uname = e.handle || e.instagram || e.tiktok || "—";

  // İnline kaydet: dokunulmadıysa PUT etme.
  const saveField = (key, val) => { if ((e[key] || "") !== (val || "")) onPatch(e.id, { [key]: val }); };

  return (
    <>
      <tr className="border-t hover:bg-gray-50/60" data-testid={`pr-row-${e.id}`}>
        {/* Influencer + expand */}
        <td className={`${td} whitespace-nowrap`}>
          <button onClick={() => setOpen((o) => !o)} className="inline-flex items-center gap-1 font-medium text-gray-900 hover:underline" data-testid={`pr-expand-${e.id}`}>
            {open ? <ChevronDown size={14} className="text-gray-400" /> : <ChevronRight size={14} className="text-gray-400" />}
            {e.influencer_name || "—"}
            {/* Renk noktası isimle AYNI hizada (dikey ortalı) — butonun items-center akışında */}
            <RatingDots id={e.influencer_id} name={e.influencer_name} rating={e.influencer_rating} className="ml-2" />
          </button>
        </td>
        {/* İş Birliği Türü (Barter / PR / Ücretli İş Birliği) */}
        <td className={`${td} whitespace-nowrap`}>
          {Number(e.fee_amount) > 0 && (
            <div className="mt-0.5 text-[11px] font-semibold text-emerald-700" title="Bu iş birliği için anlaşılan ücret">
              {money(e.fee_amount)}
            </div>
          )}
          {e.anlasma_sekli
            ? <span className="inline-block text-[11px] font-medium px-2 py-0.5 rounded-full bg-indigo-50 text-indigo-700 border border-indigo-200">{e.anlasma_sekli}</span>
            : <span className="text-gray-300">—</span>}
        </td>
        {/* İletişim (platform) */}
        <td className={`${td} whitespace-nowrap`}>
          <span className="text-gray-900">{platform}</span>
          <div className="mt-0.5"><SocialLinks instagram={e.instagram} tiktok={e.tiktok} /></div>
        </td>
        {/* Ürün (çoklu kalem) — solda thumbnail; ürün adı TEK SATIR ve TAM (kırpma YOK).
            Sütun içeriğe göre genişler; yer için Not + tarih inputları daraltıldı. */}
        <td className={td}>
          {items ? (
            <div className="space-y-1">
              {items.map((p, i) => (
                <div key={i} className="flex items-center gap-1.5 h-7">
                  <PRThumb src={p.image} name={p.name || p.barcode} />
                  <span className="text-gray-900 whitespace-nowrap" title={p.name || p.barcode}>{p.name || p.barcode}</span>
                </div>
              ))}
            </div>
          ) : <span className="whitespace-nowrap" title={e.urun || ""}>{e.urun || "—"}</span>}
        </td>
        {/* Beden (çoklu kalem) — ürün satırlarıyla HİZALI (h-7) */}
        <td className={td}>
          {items ? <div className="space-y-1">{items.map((p, i) => <div key={i} className="h-7 flex items-center text-gray-900">{p.size || "—"}</div>)}</div> : (e.beden || "—")}
        </td>
        {/* Gönderim Tarihi (çoklu kalem) — hizalı */}
        <td className={`${td} whitespace-nowrap text-gray-900`}>
          {items ? <div className="space-y-1">{items.map((p, i) => <div key={i} className="h-7 flex items-center">{p.gonderim_tarihi ? fmtDate(p.gonderim_tarihi) : (e.shipped_at ? fmtDate(e.shipped_at) : "—")}</div>)}</div>
                 : (e.shipped_at ? fmtDate(e.shipped_at) : "—")}
        </td>
        {/* Paylaştı — KALEM BAZLI (her ürün AYRI); Beden/Gönderim Tarihi ile hizalı (h-7) */}
        <td className={td}>
          {items ? (
            <div className="space-y-1">
              {items.map((p, i) => (
                <label key={i} className="h-7 flex items-center gap-1 cursor-pointer">
                  <input type="checkbox" checked={!!p.shared}
                         onChange={(ev) => onItemShared(e.id, i, ev.target.checked)}
                         className="w-4 h-4 accent-black" data-testid={`pr-item-shared-${e.id}-${i}`} />
                  <span className="text-[10px] text-gray-600">{p.shared ? "Evet" : "Hayır"}</span>
                </label>
              ))}
            </div>
          ) : <span className="text-[11px] text-gray-400">—</span>}
        </td>
        {/* İşlemler: Barkod Çıkart / Düzenle / Sil (+ Geçmiş) */}
        <td className={`${td} whitespace-nowrap`}>
          <div className="flex items-center gap-1">
            {canShip && (
              barcoded
                ? (
                  <>
                    {/* Barkodun OLUŞMASI ile etiketin YAZDIRILMASI ayrı: siparişlerdeki
                        cargo_label_printed_at kuralının aynısı. Yazdırılmadıysa SARI. */}
                    <span
                      className={`inline-flex items-center rounded px-1 py-1 ${
                        e.cargo_label_printed_at
                          ? "text-green-700 bg-green-50"
                          : "text-amber-700 bg-amber-50 border border-amber-200"}`}
                      title={e.cargo_label_printed_at
                        ? `Barkod çıkarıldı ve yazdırıldı · ${e.cargo_barcode}`
                          + (Number(e.cargo_label_print_count || 0) > 1
                             ? ` · ${e.cargo_label_print_count} defa` : "")
                        : `Barkod çıkarıldı — HENÜZ YAZDIRILMADI · ${e.cargo_barcode}`}
                      data-testid={`pr-label-state-${e.id}`}
                    ><Barcode size={14} /></span>
                    {/* Kargo takip — Siparişler mantığı: gerçek gönderi_no varsa yeşil kamyon + no (link),
                        yoksa MNG/DHL'den ÇEK butonu (kamyon). */}
                    {trackNo ? (
                      <a href={trackUrl}
                         target="_blank" rel="noreferrer" onClick={(ev) => ev.stopPropagation()}
                         title={`Kargo takip: ${trackNo}${e.cargo_last_status_text ? " · " + e.cargo_last_status_text : ""}`}
                         className="inline-flex items-center gap-1 text-emerald-600 hover:text-emerald-700 border border-emerald-200 bg-emerald-50 rounded px-1.5 py-1"
                         data-testid={`pr-track-link-${e.id}`}>
                        <Truck size={14} /><span className="font-mono text-[10px] font-bold text-gray-700 max-w-[92px] truncate">{trackNo}</span>
                      </a>
                    ) : (
                      <span title={`Takip no otomatik çekilir (her saat tarama).${e.cargo_status_checked_at ? " Son kontrol: " + new Date(e.cargo_status_checked_at).toLocaleString("tr-TR") : " Henüz taranmadı."}${e.cargo_mng_no ? " · MNG sipariş no: " + e.cargo_mng_no + " (takip no değil)" : ""}${e.cargo_track_note ? " · " + e.cargo_track_note : ""}${e.cargo_last_status_text ? " · MNG durumu: " + e.cargo_last_status_text : ""}${e.cargo_track_error ? " · Hata: " + e.cargo_track_error : ""}${e.cargo_track_debug ? " · Teknik: " + e.cargo_track_debug : ""}`}
                        className={`inline-flex flex-wrap items-center gap-1 border rounded px-1.5 py-1 ${e.cargo_track_error ? "text-red-500 border-red-200 bg-red-50" : "text-gray-400 border-gray-200"}`}
                        data-testid={`pr-track-pending-${e.id}`}><Truck size={14} /><span className="text-[10px] font-medium">{e.cargo_track_error ? "takip hatası" : (e.cargo_mng_no ? "MNG'de okutulmadı" : "takip bekleniyor")}</span>
                        {e.cargo_mng_no && (
                          <span className="text-[9px] font-mono text-gray-500 font-normal" title="MNG iç sipariş numarası — kargo takip no değil; takip no MNG paketi okutunca gelir">MNG {e.cargo_mng_no}</span>
                        )}
                        {(e.cargo_last_status_text || e.cargo_track_error || (!e.cargo_mng_no && e.cargo_track_debug)) && (
                          <span className="block w-full basis-full text-[9px] text-gray-400 font-normal normal-case max-w-[170px] truncate" title={e.cargo_track_note || e.cargo_track_debug || ""}>
                            {(e.cargo_last_status_text || e.cargo_track_error || e.cargo_track_debug || "").slice(0, 70)}
                          </span>
                        )}</span>
                    )}
                    <button
                      onClick={() => { openAdminDocument(`/influencer-pr/${e.id}/cargo-label`, "width=420,height=640", true).catch((err) => toast.error(err.message)); }}
                      title={e.cargo_label_printed_at ? "Kargo etiketini tekrar yazdır" : "Kargo etiketini yazdır — henüz yazdırılmadı"}
                      className={`inline-flex items-center rounded px-1.5 py-1 border ${
                        e.cargo_label_printed_at
                          ? "text-gray-600 hover:text-black border-gray-200"
                          : "text-amber-700 border-amber-300 bg-amber-50 hover:bg-amber-100"}`}
                      data-testid={`pr-print-${e.id}`}><Printer size={14} /></button>
                  </>
                )
                : <button onClick={onShip} title="Barkod Çıkart — stok düşer + MNG kargo barkodu oluşur"
                          className="inline-flex items-center text-indigo-600 hover:text-indigo-800 border border-indigo-200 rounded px-1.5 py-1"
                          data-testid={`pr-ship-${e.id}`}><Barcode size={15} /></button>
            )}
            {e.influencer_id && (
              <button onClick={onReship}
                title="Yeni kargo gönder — aynı kişiye yeni gönderi (ürünleri seç; stok tekrar düşer)"
                className="inline-flex items-center text-purple-600 hover:text-purple-800 border border-purple-200 rounded px-1.5 py-1"
                data-testid={`pr-reship-${e.id}`}><PackagePlus size={14} /></button>
            )}
            {(e.products || []).length > 0 && (
              <button
                onClick={() => { openAdminDocument(`/influencer-pr/${e.id}/irsaliye`, "", true).catch((err) => toast.error(err.message)); }}
                title="Sevk irsaliyesi (PDF) — gönderilen ürünler"
                className="inline-flex items-center text-gray-600 hover:text-black border border-gray-200 rounded px-1.5 py-1"
                data-testid={`pr-irsaliye-${e.id}`}><FileText size={14} /></button>
            )}
            {e.influencer_id && (
              <button onClick={onHistory} title="Geçmiş" className="text-gray-400 hover:text-black p-1" data-testid={`pr-history-${e.id}`}><History size={14} /></button>
            )}
            <button onClick={onEdit} title="Düzenle" className="text-gray-400 hover:text-black p-1" data-testid={`pr-edit-${e.id}`}><Pencil size={13} /></button>
            <button onClick={onDelete} title="Sil" className="text-gray-400 hover:text-red-600 p-1" data-testid={`pr-del-${e.id}`}><Trash2 size={13} /></button>
            {/* Not — sütun kaldırıldı; not girilmişse işlemlerin EN SAĞINDA ikon (hover: metin, tık: düzenle) */}
            {noteEdit ? (
              <input type="text" autoFocus defaultValue={e.note || ""}
                onBlur={(ev) => { saveField("note", ev.target.value); setNoteEdit(false); }}
                onKeyDown={(ev) => { if (ev.key === "Enter") ev.currentTarget.blur(); else if (ev.key === "Escape") setNoteEdit(false); }}
                placeholder="Not…"
                className="border rounded px-1.5 py-1 text-xs w-[150px] focus:outline-none focus:border-black" data-testid={`pr-note-${e.id}`} />
            ) : e.note ? (
              <div className="relative group inline-block">
                <button onClick={() => setNoteEdit(true)} title="Not girili — düzenlemek için tıkla" data-testid={`pr-note-icon-${e.id}`}
                  className="inline-flex items-center justify-center w-7 h-7 rounded-full bg-amber-50 text-amber-600 border border-amber-200 hover:bg-amber-100">
                  <StickyNote size={14} />
                </button>
                <div className="pointer-events-none absolute z-30 right-0 bottom-full mb-1.5 hidden group-hover:block
                              w-max max-w-[260px] bg-gray-900 text-white text-[11px] leading-snug rounded-lg px-2.5 py-1.5 shadow-xl whitespace-pre-wrap text-left normal-case">
                  {e.note}
                </div>
              </div>
            ) : null}
          </div>
        </td>
      </tr>
      {open && (
        <tr className="bg-gray-50/70 border-t" data-testid={`pr-detail-${e.id}`}>
          <td colSpan={8} className="px-4 py-3">
            <div className="flex flex-wrap gap-x-8 gap-y-2 text-xs">
              <div><span className="text-gray-400">Kullanıcı Adı: </span><span className="font-medium text-gray-900">{uname}</span></div>
              <div><span className="text-gray-400">Influencer Türü: </span><span className="font-medium text-gray-900">{e.influencer_turu || "—"}</span></div>
              <div><span className="text-gray-400">İş Birliği Türü: </span><span className="font-medium text-gray-900">{e.anlasma_sekli || "—"}</span></div>
              <div><span className="text-gray-400">Anlaşılan Ücret: </span><span className="font-medium text-gray-900">{Number(e.fee_amount) > 0 ? money(e.fee_amount) : "—"}</span></div>
              <div><span className="text-gray-400">Telefon: </span><span className="font-medium text-gray-900">{e.phone || "—"}</span></div>
              <div><span className="text-gray-400">İletişim (kanal): </span><span className="font-medium text-gray-900">{e.contact || "—"}</span></div>
              <div><span className="text-gray-400">Teklif: </span><span className="text-gray-900">{e.offer || "—"}</span></div>
              <div><span className="text-gray-400">Cevap: </span><span className="text-gray-900">{e.response || "—"}</span></div>
              <div><span className="text-gray-400">Follow-up: </span><span className="text-gray-900">{e.follow_up || "—"}</span></div>
              {e.adres && <div className="w-full"><span className="text-gray-400">Adres: </span><span className="text-gray-900">{e.adres}</span></div>}
              {e.cargo_barcode && <div className="w-full flex items-center gap-2"><span><span className="text-gray-400">Kargo barkodu: </span><span className="font-mono text-gray-800">{e.cargo_barcode}</span>{e.cargo_tracking_no ? <span className="text-gray-400"> · Takip: {e.cargo_tracking_no}</span> : null}</span>
                <button onClick={() => { openAdminDocument(`/influencer-pr/${e.id}/cargo-label`, "width=420,height=640", true).catch((err) => toast.error(err.message)); }}
                  className="inline-flex items-center gap-1 text-[11px] text-gray-600 hover:text-black border border-gray-300 rounded px-2 py-0.5" title="Kargo etiketini yazdır">
                  <Printer size={12} /> Etiketi Yazdır
                </button></div>}
            </div>
          </td>
        </tr>
      )}
    </>
  );
}

/* Kayıtlı influencer seçici: arama kutusu + alfabetik (Türkçe) sıralı liste.
   İlayda talebi: "isimleri aramak için arama butonu + alfabetik sıra". Seçili kişi aramada
   gizlense bile listede tutulur (seçim kaybolmasın). */
// Seçili influencer'ın kayıtlı ÜST/ALT bedeni. Form state'ine kopyalanmaz, seçimden
// türetilir → yeni kayıtta da mevcut kaydı düzenlerken de doğru gelir.
function useInfSizes(list, id) {
  const inf = useMemo(() => (list || []).find((i) => i.id === id) || null, [list, id]);
  return [inf?.beden_ust || "", inf?.beden_alt || ""];
}

// Ürün seçicinin altındaki "kayıtlı bedeni" ipucu.
function BedenHint({ ust, alt }) {
  if (!ust && !alt) return null;
  return (
    <p className="text-[11px] text-purple-700 mt-1" data-testid="beden-hint">
      Kayıtlı bedeni: {[ust && `üst ${ust}`, alt && `alt ${alt}`].filter(Boolean).join(" · ")}
      {" "}— eşleşen bedenler ★ ile işaretli.
    </p>
  );
}

const _infLabel = (i) => `${i.name || ""}${i.instagram ? ` (${i.instagram})` : i.handle ? ` (${i.handle})` : ""}`;
const _infSorted = (list) => [...(list || [])].sort((a, b) => String(a.name || "").localeCompare(String(b.name || ""), "tr", { sensitivity: "base" }));
// Tek parça açılır seçici: alana tıkla → liste açılır, EN ÜSTÜNDE arama kutusu.
// Önceki hâlinde arama ayrı bir input, seçim ayrı bir <select> idi; yazdığın adı
// bir de aşağıdaki listeden bulmak gerekiyordu. Artık yazdıkça liste süzülüyor,
// satıra tıklayınca (veya Enter'la) seçiliyor.
function InfluencerPicker({ list, value, onChange, placeholder, testId }) {
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const [hi, setHi] = useState(0);
  const boxRef = useRef(null);
  const searchRef = useRef(null);

  const sorted = useMemo(() => _infSorted(list), [list]);
  const shown = useMemo(() => {
    const t = q.trim().toLocaleLowerCase("tr");
    if (!t) return sorted;
    return sorted.filter((i) =>
      `${i.name || ""} ${i.instagram || ""} ${i.tiktok || ""} ${i.handle || ""}`
        .toLocaleLowerCase("tr").includes(t));
  }, [sorted, q]);
  const selected = useMemo(() => sorted.find((i) => i.id === value) || null, [sorted, value]);

  // Dışarı tıklama / Esc ile kapan
  useEffect(() => {
    if (!open) return undefined;
    const onDoc = (e) => { if (boxRef.current && !boxRef.current.contains(e.target)) setOpen(false); };
    const onKey = (e) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  // Açılınca aramayı temizle ve imleci oraya koy → direkt yazmaya başlanabilsin
  useEffect(() => {
    if (!open) return undefined;
    setQ(""); setHi(0);
    const t = setTimeout(() => searchRef.current?.focus(), 0);
    return () => clearTimeout(t);
  }, [open]);

  const pick = (id) => { onChange(id); setOpen(false); };

  const onSearchKey = (e) => {
    if (e.key === "ArrowDown") { e.preventDefault(); setHi((h) => Math.min(h + 1, shown.length - 1)); }
    else if (e.key === "ArrowUp") { e.preventDefault(); setHi((h) => Math.max(h - 1, 0)); }
    else if (e.key === "Enter") { e.preventDefault(); if (shown[hi]) pick(shown[hi].id); }
  };

  return (
    <div className="relative" ref={boxRef}>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        data-testid={testId}
        className={`inp w-full flex items-center justify-between text-left ${selected ? "" : "text-gray-400"}`}
      >
        <span className="truncate">{selected ? _infLabel(selected) : placeholder}</span>
        {selected && <RatingDots id={selected.id} size={8} className="ml-1" />}
        <ChevronDown className={`w-4 h-4 shrink-0 ml-2 text-gray-400 transition-transform ${open ? "rotate-180" : ""}`} />
      </button>

      {open && (
        <div className="absolute z-30 left-0 right-0 mt-1 bg-white border rounded-lg shadow-lg overflow-hidden"
             data-testid={`${testId}-panel`}>
          <div className="p-2 border-b bg-gray-50 sticky top-0">
            <div className="relative">
              <Search className="w-4 h-4 absolute left-2 top-1/2 -translate-y-1/2 text-gray-400" />
              <input
                ref={searchRef}
                className="inp pl-8"
                value={q}
                onChange={(e) => { setQ(e.target.value); setHi(0); }}
                onKeyDown={onSearchKey}
                placeholder={`Ara: ad / instagram / tiktok (${sorted.length} kayıt)`}
                data-testid={`${testId}-search`}
              />
            </div>
          </div>
          <div className="max-h-64 overflow-y-auto">
            {value && (
              <button type="button" onClick={() => pick("")}
                className="w-full text-left px-3 py-2 text-sm text-gray-500 hover:bg-gray-50 border-b">
                Seçimi temizle
              </button>
            )}
            {shown.map((i, idx) => (
              <button
                key={i.id}
                type="button"
                onMouseEnter={() => setHi(idx)}
                onClick={() => pick(i.id)}
                data-testid={`${testId}-opt`}
                className={`w-full text-left px-3 py-2 text-sm flex items-center justify-between gap-2 ${
                  idx === hi ? "bg-purple-50" : ""} ${i.id === value ? "font-semibold text-purple-700" : ""}`}
              >
                <span className="flex items-center gap-1.5 min-w-0"><RatingDots id={i.id} size={8} /><span className="truncate">{_infLabel(i)}</span></span>
                {i.tiktok && <span className="text-[10px] text-gray-400 shrink-0">{i.tiktok}</span>}
              </button>
            ))}
            {shown.length === 0 && (
              <p className="px-3 py-3 text-sm text-gray-500">Eşleşen kayıt yok.</p>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

function PRFormModal({ initial, onClose, onSaved }) {
  const [infList, setInfList] = useState([]);
  const [products, setProducts] = useState(Array.isArray(initial?.products) ? initial.products : []);
  const [form, setForm] = useState({
    influencer_id: initial?.influencer_id || "",
    influencer_name: initial?.influencer_name || "",
    influencer_type: initial?.influencer_type || "",
    date: (initial?.date || new Date().toISOString()).slice(0, 10),
    contact: initial?.contact || "",
    offer: initial?.offer || "",
    response: initial?.response || "",
    status: initial?.status || "beklemede",
    follow_up: initial?.follow_up || "",
    note: initial?.note || "",
    instagram: initial?.instagram || "",
    tiktok: initial?.tiktok || "",
    urun: initial?.urun || "",
    beden: initial?.beden || "",
    anlasma_sekli: initial?.anlasma_sekli || "",
    fee_amount: initial?.fee_amount ?? "",
    phone: initial?.phone || "",
    adres: initial?.adres || "",
    cargo_gonderi_no: initial?.cargo_gonderi_no || "",
  });
  const [saving, setSaving] = useState(false);
  const set = (k, v) => setForm((f) => ({ ...f, [k]: v }));

  useEffect(() => {
    (async () => {
      try { const r = await axios.get(`${API}/influencers`, auth()); setInfList(r.data?.influencers || []); }
      catch { /* sessiz */ }
    })();
  }, []);

  const [bedenUst, bedenAlt] = useInfSizes(infList, form.influencer_id);

  // PR kaydı YALNIZ kayıtlı influencer seçilerek açılır (serbest-yaz YOK). Seçilince kimlik
  // alanları registry'den dolar ve READ-ONLY olur; kullanıcı yalnız Ürün&Beden + PR alanlarını girer.
  const pickInfluencer = (id) => {
    if (!id) {
      setForm((f) => ({ ...f, influencer_id: "", influencer_name: "", influencer_type: "",
        instagram: "", tiktok: "", phone: "", anlasma_sekli: "", adres: "", fee_amount: "" }));
      return;
    }
    const inf = infList.find((i) => i.id === id);
    const sa = inf?.shipping_address || {};
    const adr = [sa.adres, sa.ilce, sa.il].filter(Boolean).join(", ");
    setForm((f) => ({
      ...f, influencer_id: id,
      influencer_name: inf?.name || "",
      influencer_type: inf?.platform || "",
      instagram: inf?.instagram || "",
      tiktok: inf?.tiktok || "",
      phone: inf?.phone || "",
      anlasma_sekli: inf?.anlasma_sekli || "",
      // Anlaşılan ücret influencer profilinden ön-dolar; bu gönderi için değiştirilebilir.
      fee_amount: (f.fee_amount !== "" && f.fee_amount != null) ? f.fee_amount : (inf?.fee_amount ?? ""),
      adres: adr || "",
    }));
  };

  const save = async () => {
    if (!form.influencer_id) return toast.error("Önce kayıtlı bir influencer seçin (yoksa Kayıtlı Influencerlar'dan ekleyin)");
    setSaving(true);
    try {
      const body = { ...form, products, beden: "", urun: "", date: form.date ? `${form.date}T00:00:00` : new Date().toISOString() };
      if (initial?.id) await axios.put(`${API}/influencer-pr/${initial.id}`, body, auth());
      else await axios.post(`${API}/influencer-pr`, body, auth());
      toast.success("Kaydedildi");
      onSaved();
    } catch (e) {
      toast.error(e.response?.data?.detail || "Kaydedilemedi");
    } finally { setSaving(false); }
  };

  return (
    <Modal title={initial?.id ? "PR Kaydı Düzenle" : "Yeni PR Kaydı"} onClose={onClose}>
      <div className="grid grid-cols-2 gap-3">
        <Field label="Kayıtlı influencer * (yalnız kayıtlı seçilebilir — yoksa Kayıtlı Influencerlar'dan ekleyin)" full>
          <InfluencerPicker list={infList} value={form.influencer_id} onChange={pickInfluencer}
            placeholder="— Kayıtlı influencer seçin —" testId="pr-inf-select" />
          {!form.influencer_id && (
            <p className="text-[11px] text-amber-600 mt-1">Önce kayıtlı bir influencer seçin (yoksa Kayıtlı Influencerlar'dan ekleyin).</p>
          )}
        </Field>
        {/* Kimlik alanları SEÇİMDEN gelir → READ-ONLY (serbest-yaz ile yeni influencer yaratılmaz). */}
        <Field label="Influencer adı"><input className="inp bg-gray-50 text-gray-600" value={form.influencer_name} readOnly disabled placeholder="Seçimden gelir" /></Field>
        <Field label="Influencer Türü"><input className="inp bg-gray-50 text-gray-600" value={form.influencer_type} readOnly disabled placeholder="Seçimden gelir" /></Field>
        <Field label="Instagram (@)"><input className="inp bg-gray-50 text-gray-600" value={form.instagram} readOnly disabled placeholder="Seçimden gelir" /></Field>
        <Field label="TikTok (@)"><input className="inp bg-gray-50 text-gray-600" value={form.tiktok} readOnly disabled placeholder="Seçimden gelir" /></Field>
        <Field label="Telefon"><input className="inp bg-gray-50 text-gray-600" value={form.phone} readOnly disabled placeholder="Seçimden gelir" /></Field>
        <Field label="Anlaşma Şekli"><input className="inp bg-gray-50 text-gray-600" value={form.anlasma_sekli} readOnly disabled placeholder="Seçimden gelir" data-testid="pr-anlasma" /></Field>
        {/* Kayıtlı influencer'ın beden bilgisi — hangi bedeni göndereceğini seçerken görünsün. */}
        <Field label="Üst Beden"><input className="inp bg-gray-50 text-gray-600" value={bedenUst} readOnly disabled placeholder="Seçimden gelir" data-testid="pr-beden-ust" /></Field>
        <Field label="Alt Beden"><input className="inp bg-gray-50 text-gray-600" value={bedenAlt} readOnly disabled placeholder="Seçimden gelir" data-testid="pr-beden-alt" /></Field>
        {/* ANLAŞILAN ÜCRET — ücretli iş birliğinde kaç TL anlaşıldığı. Kargolanınca gönderinin
            maliyetine (ROI "Toplam Maliyet") yazılır. */}
        <Field label="Anlaşılan Ücret (₺)">
          <input className={`inp ${isPaid(form.anlasma_sekli) && !String(form.fee_amount || "").trim() ? "border-amber-400" : ""}`}
            inputMode="decimal" value={form.fee_amount}
            onChange={(e) => set("fee_amount", e.target.value)}
            placeholder={isPaid(form.anlasma_sekli) ? "Ör. 15000" : "Ücretsiz iş birliğinde boş bırakın"}
            data-testid="pr-fee-amount" />
          <p className="text-[10px] text-gray-400 mt-1">
            {isPaid(form.anlasma_sekli)
              ? "Ücretli iş birliği — anlaşılan tutarı girin; ROI maliyetine eklenir."
              : "Profilden gelir; bu gönderi için değiştirebilirsiniz."}
          </p>
        </Field>
        <Field label="Ürün & Beden (ürünü ara → bedenini seç; birden çok eklenebilir)" full>
          <ProductPicker picked={products} setPicked={setProducts} sizes={[bedenUst, bedenAlt]}
            stockNote="Kaydetmek stoğu DÜŞÜRMEZ — stok, listede Barkod Çıkart (kargoya ver) adımında düşülür." />
          <BedenHint ust={bedenUst} alt={bedenAlt} />
        </Field>
        <Field label="Adres" full><input className="inp bg-gray-50 text-gray-600" value={form.adres} readOnly disabled placeholder="Seçimden gelir" /></Field>
        <Field label="Gönderim Durumu">
          <select className="inp" value={form.status} onChange={(e) => set("status", e.target.value)} data-testid="pr-status">
            {PR_STATUS.map((s) => <option key={s.v} value={s.v}>{s.l}</option>)}
          </select>
        </Field>
        <Field label="İletişim (nasıl/kanal)"><input className="inp" value={form.contact} onChange={(e) => set("contact", e.target.value)} placeholder="DM / e-posta / telefon" /></Field>
        <Field label="Teklif"><input className="inp" value={form.offer} onChange={(e) => set("offer", e.target.value)} placeholder="Ne teklif edildi" /></Field>
        <Field label="Cevap"><input className="inp" value={form.response} onChange={(e) => set("response", e.target.value)} placeholder="Ne cevap geldi" /></Field>
        <Field label="Follow-up"><input className="inp" value={form.follow_up} onChange={(e) => set("follow_up", e.target.value)} placeholder="Tekrar iletişim / hatırlatma" /></Field>
        <Field label="Not" full><textarea className="inp h-20" value={form.note} onChange={(e) => set("note", e.target.value)} /></Field>
        {initial?.cargo_barcode && (
          <Field label={`Kargo takip no (elle) — MNG kaydı: ${initial?.cargo_mng_no || initial?.cargo_barcode}`} full>
            <input className="inp font-mono" value={form.cargo_gonderi_no} onChange={(e) => set("cargo_gonderi_no", e.target.value)}
              placeholder="Kurye fişindeki takip no (MNG bizim kayda atamadıysa buraya yaz)" data-testid="pr-manual-tracking" />
          </Field>
        )}
      </div>
      <div className="flex justify-end gap-2 mt-4">
        <button onClick={onClose} className="px-4 py-2 text-sm border rounded-lg">İptal</button>
        <button onClick={save} disabled={saving || !form.influencer_id} data-testid="pr-save" className="px-4 py-2 text-sm bg-black text-white rounded-lg disabled:opacity-50">
          {saving ? "..." : "Kaydet"}
        </button>
      </div>
    </Modal>
  );
}

function HistoryPanel({ influencerId, influencerName, onClose }) {
  const [data, setData] = useState(null);
  useEffect(() => {
    (async () => {
      try { const r = await axios.get(`${API}/influencers/${influencerId}/history`, auth()); setData(r.data); }
      catch { toast.error("Geçmiş yüklenemedi"); }
    })();
  }, [influencerId]);

  return (
    <div className="fixed inset-0 z-[60] flex justify-end" onClick={onClose}>
      <div className="absolute inset-0 bg-black/40" />
      <div className="relative bg-white w-full max-w-md h-full overflow-y-auto p-5 shadow-xl" onClick={(e) => e.stopPropagation()} data-testid="pr-history-panel">
        <div className="flex items-center justify-between mb-3">
          <h2 className="font-bold text-lg flex items-center gap-2"><History size={18} /> {influencerName || "Geçmiş"} <RatingDots id={influencerId} name={influencerName} size={12} /></h2>
          <button onClick={onClose}><X size={20} /></button>
        </div>
        {!data ? (
          <div className="py-10 text-center text-gray-400 text-sm">Yükleniyor...</div>
        ) : (
          <>
            <div className="text-xs mb-3"><SocialLinks instagram={data.influencer?.instagram} tiktok={data.influencer?.tiktok} /></div>

            <h3 className="font-semibold text-sm mb-2 flex items-center gap-1"><Package size={14} /> Gönderdiklerimiz ({(data.campaigns || []).length})</h3>
            <div className="space-y-2 mb-5">
              {(data.campaigns || []).length === 0 && <p className="text-xs text-gray-400">Henüz ürün gönderimi yok.</p>}
              {(data.campaigns || []).map((c) => (
                <div key={c.id} className="border rounded-lg p-2.5 text-xs">
                  <div className="flex justify-between"><span className="font-medium">{c.title || "Gönderim"}</span><span className="text-gray-400">{fmtDate(c.created_at)}</span></div>
                  {(c.products || []).length > 0 && (
                    <div className="text-gray-500 mt-1">{(c.products || []).map((p) => `${p.name || p.barcode}${p.qty ? ` ×${p.qty}` : ""}`).join(", ")}</div>
                  )}
                  {c.status && <Badge>{c.status}</Badge>}
                </div>
              ))}
            </div>

            <h3 className="font-semibold text-sm mb-2 flex items-center gap-1"><ClipboardList size={14} /> PR işlemleri ({(data.pr_entries || []).length})</h3>
            <div className="space-y-2">
              {(data.pr_entries || []).length === 0 && <p className="text-xs text-gray-400">Henüz PR işlemi yok.</p>}
              {(data.pr_entries || []).map((e) => (
                <div key={e.id} className="border rounded-lg p-2.5 text-xs">
                  <div className="flex justify-between items-center">
                    <span className={`px-2 py-0.5 rounded-full ${prStatusMeta(e.status).c}`}>{prStatusMeta(e.status).l}</span>
                    <span className="text-gray-400">{fmtDate(e.date)}</span>
                  </div>
                  {e.offer && <div className="mt-1"><span className="text-gray-400">Teklif:</span> {e.offer}</div>}
                  {e.response && <div><span className="text-gray-400">Cevap:</span> {e.response}</div>}
                  {e.note && <div className="text-gray-500 mt-0.5">{e.note}</div>}
                </div>
              ))}
            </div>
          </>
        )}
      </div>
    </div>
  );
}

function TabBtn({ active, onClick, icon, children, testid }) {
  return (
    <button
      onClick={onClick}
      data-testid={testid}
      className={`inline-flex items-center gap-1.5 px-4 py-2 text-sm font-medium -mb-px border-b-2 transition-colors ${
        active ? "border-black text-black" : "border-transparent text-gray-500 hover:text-gray-800"
      }`}
    >
      {icon} {children}
    </button>
  );
}

/* Sütun filtre/sıralama yardımcıları (Kayıtlı Influencerlar tablosu) */
function SortTh({ k, label, sortK, sortD, onSort, cls = "" }) {
  const active = sortK === k;
  return (
    <th onClick={() => onSort(k)}
      className={`px-3 py-2.5 font-semibold whitespace-nowrap select-none cursor-pointer hover:text-black ${cls}`}>
      <span className="inline-flex items-center gap-1">{label}
        <span className={`text-[9px] ${active ? "text-black" : "text-gray-300"}`}>{active ? (sortD === "asc" ? "▲" : "▼") : "↕"}</span>
      </span>
    </th>
  );
}
function FTxt({ v, onCh, ph }) {
  return <input value={v || ""} onChange={(e) => onCh(e.target.value)} placeholder={ph}
    className="w-full border rounded px-2 py-1 text-[11px] font-normal focus:outline-none focus:border-black" />;
}
function FNum({ v, onCh, ph }) {
  return <input value={v || ""} onChange={(e) => onCh(e.target.value.replace(/[^\d]/g, ""))} placeholder={ph}
    inputMode="numeric" className="w-full border rounded px-2 py-1 text-[11px] font-normal focus:outline-none focus:border-black" />;
}
function FSel({ v, onCh, options }) {
  return (
    <select value={v || ""} onChange={(e) => onCh(e.target.value)}
      className="w-full border rounded px-1.5 py-1 text-[11px] font-normal bg-white focus:outline-none focus:border-black">
      <option value="">Tümü</option>
      {options.map((o) => <option key={o} value={o}>{o}</option>)}
    </select>
  );
}

/* ======================= SEKME 1: KAYITLI INFLUENCERLAR ======================= */
function InfluencerListTab() {
  const ratingCtx = useContext(RatingCtx);
  const [list, setList] = useState([]);
  const [loading, setLoading] = useState(true);
  const [q, setQ] = useState("");
  const [showForm, setShowForm] = useState(false);
  const [editTarget, setEditTarget] = useState(null);
  const [selected, setSelected] = useState(null);
  const [colF, setColF] = useState({});        // sütun filtreleri
  const [sortK, setSortK] = useState("");       // sıralama sütunu
  const [sortD, setSortD] = useState("asc");    // asc | desc

  // Satır türev alanları (kolon değerleri) — filtre/sıralama ile AYNI kaynak.
  const _uname = (i) => i.handle || i.instagram || i.tiktok || "";
  const _turu = (i) => i.influencer_turu || influencerTuru(i.follower_count) || "";
  const _adres = (i) => i.adres || (i.shipping_address && i.shipping_address.adres) || "";

  // Kategorik sütunların seçenekleri (veriden tekilleştirilmiş) — Türü/Platform/İş Birliği/Beden.
  const opts = useMemo(() => {
    const uniq = (fn) => Array.from(new Set(list.map(fn).filter(Boolean)))
      .sort((a, b) => String(a).localeCompare(String(b), "tr"));
    return {
      turu: uniq(_turu),
      anlasma: uniq((i) => i.anlasma_sekli),
      ust: uniq((i) => i.beden_ust),
      alt: uniq((i) => i.beden_alt),
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [list]);

  const setF = (k, v) => setColF((p) => ({ ...p, [k]: v }));
  const clearF = () => { setColF({}); setSortK(""); };
  const anyF = Object.values(colF).some(Boolean) || !!sortK;

  const rows = useMemo(() => {
    const f = colF;
    const inc = (v, q) => String(v || "").toLocaleLowerCase("tr").includes(String(q).toLocaleLowerCase("tr"));
    const fmin = parseInt(f.followers || "", 10);
    let out = list.filter((i) =>
      (!f.name || inc(i.name, f.name)) &&
      (!f.uname || inc(_uname(i), f.uname)) &&
      (!f.turu || _turu(i) === f.turu) &&
      (!(fmin > 0) || Number(i.follower_count || 0) >= fmin) &&
      (!f.coupon || inc(i.coupon_code, f.coupon)) &&
      (!f.phone || inc(i.phone, f.phone)) &&
      (!f.adres || inc(_adres(i), f.adres)) &&
      (!f.anlasma || (i.anlasma_sekli || "") === f.anlasma) &&
      (!f.ust || (i.beden_ust || "") === f.ust) &&
      (!f.alt || (i.beden_alt || "") === f.alt) &&
      (!f.notes || inc(i.notes, f.notes))
    );
    if (sortK) {
      const num = sortK === "followers";
      const val = (i) => (num ? Number(i.follower_count || 0) : ({
        name: i.name, uname: _uname(i), turu: _turu(i), coupon: i.coupon_code,
        phone: i.phone, adres: _adres(i), anlasma: i.anlasma_sekli,
        ust: i.beden_ust, alt: i.beden_alt, notes: i.notes,
      }[sortK] || ""));
      out = [...out].sort((a, b) => num
        ? val(a) - val(b)
        : String(val(a)).localeCompare(String(val(b)), "tr", { numeric: true, sensitivity: "base" }));
      if (sortD === "desc") out.reverse();
    }
    return out;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [list, colF, sortK, sortD]);

  const onSort = (k) => {
    if (sortK === k) setSortD((d) => (d === "asc" ? "desc" : "asc"));
    else { setSortK(k); setSortD("asc"); }
  };

  const load = useCallback(async (search) => {
    setLoading(true);
    try {
      const r = await axios.get(`${API}/influencers`, {
        ...auth(), params: (search || "").trim() ? { q: search.trim() } : {},
      });
      setList(r.data?.influencers || []);
    } catch {
      toast.error("Influencerlar yüklenemedi");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(""); }, [load]);
  useEffect(() => { const t = setTimeout(() => load(q), 350); return () => clearTimeout(t); }, [q, load]);

  const removeInf = async (inf) => {
    if (!window.confirm(`"${inf.name}" kaydı silinsin mi? (Geri alınamaz)`)) return;
    try {
      await axios.delete(`${API}/influencers/${inf.id}`, auth());
      toast.success("Influencer silindi");
      load(q);
    } catch {
      toast.error("Silinemedi");
    }
  };

  const exportRegistry = async () => {
    try {
      const r = await axios.get(`${API}/influencer-registry/export`, { ...auth(), params: q.trim() ? { q: q.trim() } : {}, responseType: "blob" });
      const url = URL.createObjectURL(r.data);
      const a = document.createElement("a");
      a.href = url; a.download = "kayitli-influencerlar.xlsx"; a.click();
      URL.revokeObjectURL(url);
    } catch { toast.error("Excel oluşturulamadı"); }
  };

  return (
    <div>
      <div className="flex items-center justify-between gap-3 mb-4 flex-wrap">
        <div className="relative flex-1 min-w-[240px] max-w-md">
          <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" />
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            data-testid="influencer-search"
            placeholder="İsim, @kullanıcı, Instagram, TikTok, telefon, kupon ara…"
            className="w-full border rounded-lg pl-9 pr-3 py-2 text-sm focus:outline-none focus:border-black"
          />
        </div>
        <div className="flex items-center gap-2">
          <button onClick={exportRegistry} data-testid="registry-export-btn"
            className="inline-flex items-center gap-2 border px-3 py-2 rounded-lg text-sm hover:bg-gray-50">
            <Download size={15} /> Excel'e Aktar
          </button>
          <button
            onClick={() => { setEditTarget(null); setShowForm(true); }}
            data-testid="new-influencer-btn"
            className="inline-flex items-center gap-2 bg-black text-white px-4 py-2 rounded-lg text-sm hover:bg-gray-800"
          >
            <Plus size={16} /> Yeni Influencer
          </button>
        </div>
      </div>

      {/* PR listesiyle aynı düzeltme: yenilenirken liste ekrandan KALKMASIN,
          yoksa yükseklik çöküp sayfa başa atıyor. */}
      {loading && list.length === 0 ? (
        <div className="text-gray-400 text-sm py-12 text-center">Yükleniyor...</div>
      ) : list.length === 0 ? (
        <div className="border border-dashed rounded-xl py-16 text-center text-gray-500">
          {q.trim() ? "Aramayla eşleşen influencer yok." : 'Henüz influencer eklenmedi. "Yeni Influencer" ile başlayın.'}
        </div>
      ) : (
        <div className="border rounded-xl overflow-x-auto bg-white">
          <table className="w-full text-sm min-w-[1100px]">
            <thead>
              <tr className="bg-gray-50 text-gray-600 text-left text-xs uppercase tracking-wide">
                <SortTh k="name" label="İsim Soyisim" sortK={sortK} sortD={sortD} onSort={onSort} />
                <SortTh k="anlasma" label="İş Birliği Türü" sortK={sortK} sortD={sortD} onSort={onSort} />
                <SortTh k="uname" label="Kullanıcı Adı" sortK={sortK} sortD={sortD} onSort={onSort} />
                <SortTh k="turu" label="Influencer Türü" sortK={sortK} sortD={sortD} onSort={onSort} />
                <SortTh k="phone" label="Telefon" sortK={sortK} sortD={sortD} onSort={onSort} />
                <SortTh k="adres" label="Adres" sortK={sortK} sortD={sortD} onSort={onSort} />
                <SortTh k="ust" label="Beden Üst" sortK={sortK} sortD={sortD} onSort={onSort} />
                <SortTh k="alt" label="Beden Alt" sortK={sortK} sortD={sortD} onSort={onSort} />
                <th className="px-3 py-2.5 font-semibold whitespace-nowrap text-right">İşlemler</th>
              </tr>
              {/* FİLTRE SATIRI — kategorik→açılır (İş Birliği: Barter/PR/Ücretli), serbest→arama, Takipçi→min */}
              <tr className="bg-white border-t text-[11px] normal-case tracking-normal">
                <th className="px-2 py-1.5"><FTxt v={colF.name} onCh={(v) => setF("name", v)} ph="İsim…" /></th>
                <th className="px-2 py-1.5"><FSel v={colF.anlasma} onCh={(v) => setF("anlasma", v)} options={opts.anlasma} /></th>
                <th className="px-2 py-1.5"><FTxt v={colF.uname} onCh={(v) => setF("uname", v)} ph="@kullanıcı…" /></th>
                <th className="px-2 py-1.5"><FSel v={colF.turu} onCh={(v) => setF("turu", v)} options={opts.turu} /></th>
                <th className="px-2 py-1.5"><FTxt v={colF.phone} onCh={(v) => setF("phone", v)} ph="Telefon…" /></th>
                <th className="px-2 py-1.5"><FTxt v={colF.adres} onCh={(v) => setF("adres", v)} ph="Adres…" /></th>
                <th className="px-2 py-1.5"><FSel v={colF.ust} onCh={(v) => setF("ust", v)} options={opts.ust} /></th>
                <th className="px-2 py-1.5"><FSel v={colF.alt} onCh={(v) => setF("alt", v)} options={opts.alt} /></th>
                <th className="px-2 py-1.5 text-right">
                  {anyF && <button onClick={clearF} className="text-[11px] text-gray-500 hover:text-black underline whitespace-nowrap">Temizle</button>}
                </th>
              </tr>
            </thead>
            <tbody>
              {rows.length === 0 && (
                <tr><td colSpan={9} className="px-3 py-10 text-center text-gray-400 text-sm">
                  Filtrelerle eşleşen influencer yok. <button onClick={clearF} className="underline hover:text-black">Filtreleri temizle</button>
                </td></tr>
              )}
              {rows.map((inf) => {
                const uname = inf.handle || inf.instagram || inf.tiktok || "—";
                const adres = inf.adres || (inf.shipping_address && inf.shipping_address.adres) || "—";
                return (
                  <tr key={inf.id} data-testid={`influencer-row-${inf.id}`} className="border-t hover:bg-gray-50/60">
                    <td className="px-3 py-2.5 whitespace-nowrap">
                      <span className="inline-flex items-center">
                        <button onClick={() => setSelected(inf.id)} className="font-medium text-gray-900 hover:underline text-left" title="Detay">
                          {inf.name}
                        </button>
                        <RatingDots id={inf.id} className="ml-2" />
                        {inf.is_active === false && <span className="ml-2 text-[10px] bg-gray-100 text-gray-500 px-1.5 py-0.5 rounded">pasif</span>}
                      </span>
                    </td>
                    <td className="px-3 py-2.5 whitespace-nowrap">
                      {inf.anlasma_sekli
                        ? <span className="inline-block text-[11px] font-medium px-2 py-0.5 rounded-full bg-indigo-50 text-indigo-700 border border-indigo-200">{inf.anlasma_sekli}</span>
                        : <span className="text-gray-300">—</span>}
                    </td>
                    <td className="px-3 py-2.5 whitespace-nowrap text-gray-900">{uname}</td>
                    <td className="px-3 py-2.5 whitespace-nowrap text-gray-900">{inf.influencer_turu || influencerTuru(inf.follower_count)}</td>
                    <td className="px-3 py-2.5 whitespace-nowrap text-gray-900">{inf.phone || "—"}</td>
                    <td className="px-3 py-2.5 max-w-[220px] truncate text-gray-900" title={adres}>{adres}</td>
                    <td className="px-3 py-2.5 whitespace-nowrap text-gray-900">{inf.beden_ust || "—"}</td>
                    <td className="px-3 py-2.5 whitespace-nowrap text-gray-900">{inf.beden_alt || "—"}</td>
                    <td className="px-3 py-2.5 whitespace-nowrap text-right">
                      <div className="inline-flex items-center gap-1.5">
                        <button
                          onClick={() => { setEditTarget(inf); setShowForm(true); }}
                          title="Düzenle" data-testid={`influencer-edit-${inf.id}`}
                          className="text-gray-400 hover:text-black p-1"
                        >
                          <Pencil size={14} />
                        </button>
                        <button
                          onClick={() => removeInf(inf)}
                          title="Sil" data-testid={`influencer-del-${inf.id}`}
                          className="text-gray-400 hover:text-red-600 p-1"
                        >
                          <Trash2 size={14} />
                        </button>
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {showForm && (
        <InfluencerFormModal
          initial={editTarget}
          onClose={() => { setShowForm(false); setEditTarget(null); }}
          onSaved={() => { setShowForm(false); setEditTarget(null); load(q); ratingCtx?.reload(); }}
        />
      )}
      {selected && <DetailModal influencerId={selected} onClose={() => { setSelected(null); load(q); }} />}
    </div>
  );
}

/* ======================= SEKME 2: ÜRÜN GÖNDERİMLERİ (GEÇMİŞ) ======================= */
/* ===== GÖNDERİM TAKVİMİ (Ürün Gönderimleri sekmesi) — veri: influencer_pr ===== */
const CAL_AY = ["Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık"];
const CAL_GUN = ["Pzt", "Sal", "Çar", "Per", "Cum", "Cmt", "Paz"];
const _pad2 = (n) => String(n).padStart(2, "0");
const _ymd = (dt) => `${dt.getFullYear()}-${_pad2(dt.getMonth() + 1)}-${_pad2(dt.getDate())}`;
const _startOfWeek = (dt) => { const d = new Date(dt); const wd = (d.getDay() + 6) % 7; d.setDate(d.getDate() - wd); d.setHours(0, 0, 0, 0); return d; };
// Kaydın takvim tarihi: kalem gonderim_tarihi → shipped_at → (eski) date → created_at.
// İletişim tarihi (date) alanı UI'dan kalktı; tarihsiz kayıtlar created_at'e düşer.
const _prEvDate = (e) => {
  const items = Array.isArray(e.products) ? e.products : [];
  const g = items.map((p) => p && p.gonderim_tarihi).find(Boolean);
  return String(g || e.shipped_at || e.date || e.created_at || "").slice(0, 10);
};

function CalEventCard({ e, onOpen }) {
  const st = prStatusMeta(e.status);
  const uname = e.handle || e.instagram || e.tiktok || "";
  const items = Array.isArray(e.products) ? e.products : [];
  return (
    <button onClick={() => onOpen && onOpen(e)} data-testid={`cal-event-${e.id}`}
      className="w-full text-left border rounded-lg p-2 hover:border-black transition-colors bg-white">
      <div className="flex items-center gap-1.5">
        <span className="font-medium text-gray-900 text-xs truncate">{e.influencer_name || "—"}</span>
        <RatingDots id={e.influencer_id} name={e.influencer_name} rating={e.influencer_rating} size={8} />
        {uname && <span className="text-[10px] text-gray-500 truncate">{uname}</span>}
        <span className={`ml-auto text-[9px] px-1.5 py-0.5 rounded-full shrink-0 ${st.c}`}>{st.l}</span>
      </div>
      {items.length > 0 && (
        <div className="flex items-center gap-1 mt-1.5 flex-wrap">
          {items.slice(0, 4).map((p, i) => (
            p.image
              ? <img key={i} src={p.image} alt={p.name || ""} title={`${p.name || ""}${p.size ? " · " + p.size : ""}`} className="w-6 h-6 rounded object-cover border border-gray-200" />
              : <span key={i} className="w-6 h-6 rounded bg-gray-100 border border-gray-200" title={p.name || ""} />
          ))}
          {items.length > 4 && <span className="text-[10px] text-gray-500">+{items.length - 4}</span>}
        </div>
      )}
    </button>
  );
}

function ShipmentCalendar({ entries }) {
  const [view, setView] = useState("aylik"); // gunluk | haftalik | aylik | yillik
  const [anchor, setAnchor] = useState(() => { const d = new Date(); d.setHours(0, 0, 0, 0); return d; });
  const [dayModal, setDayModal] = useState(null); // {key, list}
  const [detail, setDetail] = useState(null);      // tek etkinlik detayı

  // TAKVİME YALNIZ BARKODU OLUŞTURULMUŞ GÖNDERİLER DÜŞER (kullanıcı kuralı).
  // Hazırlanan/planlanan kayıtlar (henüz kargo barkodu üretilmemiş) takvimde GÖRÜNMEZ:
  // takvim "kime ne zaman ne GÖNDERİLDİ" defteridir, barkod üretilmeden gönderi yola
  // çıkmaz. Barkod üretilince backend cargo_barcode + shipped_at + gonderim_tarihi
  // yazar (routes/influencers.py), kayıt o gün takvimde belirir.
  const shipped = useMemo(
    () => (entries || []).filter((e) => String(e?.cargo_barcode || "").trim()),
    [entries]
  );

  const byDay = useMemo(() => {
    const m = {};
    for (const e of shipped) {
      const k = _prEvDate(e);
      if (!k) continue;
      (m[k] = m[k] || []).push(e);
    }
    return m;
  }, [shipped]);
  const byMonth = useMemo(() => {
    const m = {};
    for (const e of shipped) { const k = _prEvDate(e).slice(0, 7); if (k) (m[k] = m[k] || []).push(e); }
    return m;
  }, [shipped]);

  const shift = (dir) => {
    const d = new Date(anchor);
    if (view === "gunluk") d.setDate(d.getDate() + dir);
    else if (view === "haftalik") d.setDate(d.getDate() + dir * 7);
    else if (view === "aylik") d.setMonth(d.getMonth() + dir);
    else d.setFullYear(d.getFullYear() + dir);
    setAnchor(d);
  };
  const today = () => { const d = new Date(); d.setHours(0, 0, 0, 0); setAnchor(d); };

  const title = view === "gunluk" ? `${anchor.getDate()} ${CAL_AY[anchor.getMonth()]} ${anchor.getFullYear()}`
    : view === "haftalik" ? (() => { const s = _startOfWeek(anchor); const e = new Date(s); e.setDate(s.getDate() + 6); return `${s.getDate()} ${CAL_AY[s.getMonth()]} – ${e.getDate()} ${CAL_AY[e.getMonth()]} ${e.getFullYear()}`; })()
    : view === "aylik" ? `${CAL_AY[anchor.getMonth()]} ${anchor.getFullYear()}`
    : `${anchor.getFullYear()}`;

  const todayKey = _ymd(new Date());

  // AY ızgarası hücreleri
  const monthCells = useMemo(() => {
    const first = new Date(anchor.getFullYear(), anchor.getMonth(), 1);
    const lead = (first.getDay() + 6) % 7; // Pzt=0
    const start = new Date(first); start.setDate(first.getDate() - lead);
    return Array.from({ length: 42 }, (_, i) => { const d = new Date(start); d.setDate(start.getDate() + i); return d; });
  }, [anchor]);

  const weekCells = useMemo(() => {
    const s = _startOfWeek(anchor);
    return Array.from({ length: 7 }, (_, i) => { const d = new Date(s); d.setDate(s.getDate() + i); return d; });
  }, [anchor]);

  const openDay = (key) => { const list = byDay[key] || []; setDayModal({ key, list }); };

  return (
    <div data-testid="shipment-calendar">
      {/* Başlık + görünüm toggle + navigasyon */}
      <div className="flex items-center gap-2 mb-4 flex-wrap">
        <div className="inline-flex rounded-lg border overflow-hidden text-xs">
          {[["gunluk", "Günlük"], ["haftalik", "Haftalık"], ["aylik", "Aylık"], ["yillik", "Yıllık"]].map(([k, l]) => (
            <button key={k} onClick={() => setView(k)} data-testid={`cal-view-${k}`}
              className={`px-3 py-1.5 font-medium ${view === k ? "bg-black text-white" : "bg-white text-gray-700 hover:bg-gray-50"}`}>{l}</button>
          ))}
        </div>
        <div className="flex items-center gap-1 ml-auto">
          <button onClick={() => shift(-1)} className="p-1.5 border rounded-lg hover:bg-gray-50" title="Önceki" data-testid="cal-prev"><ChevronRight size={16} className="rotate-180" /></button>
          <div className="text-sm font-semibold text-gray-900 min-w-[150px] text-center">{title}</div>
          <button onClick={() => shift(1)} className="p-1.5 border rounded-lg hover:bg-gray-50" title="Sonraki" data-testid="cal-next"><ChevronRight size={16} /></button>
          <button onClick={today} className="px-3 py-1.5 border rounded-lg text-xs font-medium hover:bg-gray-50" data-testid="cal-today">Bugün</button>
        </div>
      </div>

      {/* AYLIK */}
      {view === "aylik" && (
        <div className="border rounded-xl overflow-hidden">
          <div className="grid grid-cols-7 bg-gray-50 text-[11px] font-semibold text-gray-500">
            {CAL_GUN.map((g) => <div key={g} className="px-2 py-2 text-center">{g}</div>)}
          </div>
          <div className="grid grid-cols-7">
            {monthCells.map((d, i) => {
              const key = _ymd(d);
              const inMonth = d.getMonth() === anchor.getMonth();
              const list = byDay[key] || [];
              return (
                <button key={i} onClick={() => list.length && openDay(key)} data-testid={`cal-day-${key}`}
                  className={`relative min-h-[74px] border-t border-l p-1.5 pt-7 text-left flex flex-col items-start justify-start ${inMonth ? "bg-white" : "bg-gray-50/60"} ${list.length ? "hover:bg-amber-50 cursor-pointer" : "cursor-default"}`}>
                  {/* Gün no — HER hücrede SABİT sol-üst (absolute); içerik/rozet konumunu ETKİLEMEZ.
                      Bugün kırmızı daire aynı 20px kutuda → diğer numaralarla BİREBİR aynı konum. */}
                  <span className={`absolute top-1 left-1 text-[11px] w-5 h-5 inline-flex items-center justify-center rounded-full ${key === todayKey ? "bg-red-600 text-white font-semibold" : inMonth ? "text-gray-900" : "text-gray-400"}`}>{d.getDate()}</span>
                  {/* İşlem-sayısı rozeti — SABİT sağ-üst (absolute) */}
                  {list.length > 0 && (
                    <span className="absolute top-1 right-1 text-[9px] leading-none bg-black text-white rounded-full min-w-[18px] h-[18px] inline-flex items-center justify-center px-1">{list.length}</span>
                  )}
                  {/* İçerik — numaranın ALTINDA (pt-7 sabit boşluk), numarayı İTMEZ */}
                  {/* TÜM isimler gösterilir (kırpma/"+N daha" YOK); satır en yoğun güne göre
                      otomatik aşağı uzar (grid satırı auto-height). */}
                  {list.length > 0 && (
                    <div className="space-y-0.5 w-full">
                      {list.map((e) => <div key={e.id} className="flex items-center gap-1 text-[10px] leading-4 text-gray-900 min-w-0" title={e.influencer_name || ""}><RatingDots id={e.influencer_id} name={e.influencer_name} rating={e.influencer_rating} size={7} /><span className="truncate">{e.influencer_name || "—"}</span></div>)}
                    </div>
                  )}
                </button>
              );
            })}
          </div>
        </div>
      )}

      {/* HAFTALIK */}
      {view === "haftalik" && (
        <div className="grid grid-cols-1 sm:grid-cols-7 gap-2">
          {weekCells.map((d, i) => {
            const key = _ymd(d);
            const list = byDay[key] || [];
            return (
              <div key={i} className="border rounded-lg p-2 min-h-[120px]">
                <div className={`text-[11px] font-semibold mb-2 ${key === todayKey ? "text-red-600" : "text-gray-500"}`}>{CAL_GUN[i]} · {d.getDate()} {CAL_AY[d.getMonth()].slice(0, 3)}</div>
                <div className="space-y-1.5">
                  {list.length === 0 ? <div className="text-[10px] text-gray-300">—</div>
                    : list.map((e) => <CalEventCard key={e.id} e={e} onOpen={setDetail} />)}
                </div>
              </div>
            );
          })}
        </div>
      )}

      {/* GÜNLÜK */}
      {view === "gunluk" && (() => {
        const key = _ymd(anchor);
        const list = byDay[key] || [];
        return (
          <div className="space-y-2">
            {list.length === 0 ? <div className="border border-dashed rounded-xl py-16 text-center text-gray-500">Bu gün gönderim yok.</div>
              : list.map((e) => <CalEventCard key={e.id} e={e} onOpen={setDetail} />)}
          </div>
        );
      })()}

      {/* YILLIK */}
      {view === "yillik" && (
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-3">
          {CAL_AY.map((ay, mi) => {
            const key = `${anchor.getFullYear()}-${_pad2(mi + 1)}`;
            const cnt = (byMonth[key] || []).length;
            const isNowMonth = key === todayKey.slice(0, 7);   // içinde bulunduğumuz ay → kırmızı
            return (
              <button key={mi} onClick={() => { const d = new Date(anchor.getFullYear(), mi, 1); setAnchor(d); setView("aylik"); }}
                data-testid={`cal-month-${mi + 1}`}
                className={`border rounded-lg p-4 text-left hover:border-black transition-colors ${isNowMonth ? "ring-2 ring-red-500 border-red-500" : ""} ${cnt ? "bg-white" : "bg-gray-50/60"}`}>
                <div className={`text-sm font-semibold ${isNowMonth ? "text-red-600" : "text-gray-900"}`}>{ay}</div>
                <div className="text-xs text-gray-500 mt-1">{cnt ? `${cnt} gönderim` : "—"}</div>
              </button>
            );
          })}
        </div>
      )}

      {/* Gün detay modalı */}
      {dayModal && (
        <div className="fixed inset-0 z-[70] flex items-center justify-center p-4" onClick={() => setDayModal(null)}>
          <div className="absolute inset-0 bg-black/40" />
          <div className="relative bg-white rounded-xl w-full max-w-lg max-h-[80vh] flex flex-col shadow-xl" onClick={(e) => e.stopPropagation()} data-testid="cal-day-modal">
            <div className="flex items-center justify-between px-4 py-3 border-b">
              <div className="text-sm font-semibold text-gray-900">{dayModal.key} — {dayModal.list.length} gönderim</div>
              <button onClick={() => setDayModal(null)} className="text-gray-400 hover:text-black"><X size={18} /></button>
            </div>
            <div className="p-3 space-y-2 overflow-y-auto">
              {dayModal.list.map((e) => <CalDetail key={e.id} e={e} />)}
            </div>
          </div>
        </div>
      )}
      {detail && (
        <div className="fixed inset-0 z-[70] flex items-center justify-center p-4" onClick={() => setDetail(null)}>
          <div className="absolute inset-0 bg-black/40" />
          <div className="relative bg-white rounded-xl w-full max-w-md shadow-xl" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between px-4 py-3 border-b">
              <div className="text-sm font-semibold text-gray-900">Gönderim Detayı</div>
              <button onClick={() => setDetail(null)} className="text-gray-400 hover:text-black"><X size={18} /></button>
            </div>
            <div className="p-3"><CalDetail e={detail} /></div>
          </div>
        </div>
      )}
    </div>
  );
}

function CalDetail({ e }) {
  const st = prStatusMeta(e.status);
  const uname = e.handle || e.instagram || e.tiktok || "";
  const items = Array.isArray(e.products) ? e.products : [];
  return (
    <div className="border rounded-lg p-3">
      <div className="flex items-center gap-2">
        <span className="font-medium text-gray-900">{e.influencer_name || "—"}</span>
        <RatingDots id={e.influencer_id} name={e.influencer_name} rating={e.influencer_rating} />
        {uname && <span className="text-xs text-gray-500">{uname}</span>}
        <span className={`ml-auto text-[10px] px-2 py-0.5 rounded-full ${st.c}`}>{st.l}</span>
      </div>
      <div className="text-[11px] text-gray-500 mt-0.5">Tarih: {_prEvDate(e) || "—"}</div>
      <div className="mt-2 space-y-1.5">
        {items.length === 0 ? <div className="text-xs text-gray-400">Ürün yok</div>
          : items.map((p, i) => (
            <div key={i} className="flex items-center gap-2">
              {p.image ? <img src={p.image} alt={p.name || ""} className="w-8 h-8 rounded object-cover border border-gray-200" />
                : <span className="w-8 h-8 rounded bg-gray-100 border border-gray-200" />}
              <span className="text-xs text-gray-900 truncate">{p.name || p.barcode}</span>
              {p.size && <span className="text-[11px] text-gray-600 ml-auto">Beden: {p.size}</span>}
            </div>
          ))}
      </div>
    </div>
  );
}

function ShipmentsTab() {
  const [entries, setEntries] = useState([]);
  const [loading, setLoading] = useState(true);
  const [showCreate, setShowCreate] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const r = await axios.get(`${API}/influencer-pr`, { ...auth() });
      setEntries(r.data?.entries || []);
    } catch {
      toast.error("Gönderimler yüklenemedi");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const exportCalendar = async () => {
    try {
      const r = await axios.get(`${API}/influencer-pr/calendar-export`, { ...auth(), responseType: "blob" });
      const url = URL.createObjectURL(r.data);
      const a = document.createElement("a");
      a.href = url; a.download = "gonderim-takvimi.xlsx"; a.click();
      URL.revokeObjectURL(url);
    } catch { toast.error("Excel oluşturulamadı"); }
  };

  return (
    <div>
      <div className="flex items-center justify-between gap-3 mb-4 flex-wrap">
        <div className="text-sm text-gray-500">Gönderim takvimi — kime ne zaman ne gönderildiği (Gönderi Takibi kayıtları).</div>
        <div className="flex items-center gap-2">
          <button onClick={exportCalendar} data-testid="calendar-export-btn"
            className="inline-flex items-center gap-2 border px-3 py-2 rounded-lg text-sm hover:bg-gray-50">
            <Download size={15} /> Excel'e Aktar
          </button>
          <button
            onClick={() => setShowCreate(true)}
            data-testid="new-shipment-btn"
            className="inline-flex items-center gap-2 bg-black text-white px-4 py-2 rounded-lg text-sm hover:bg-gray-800"
          >
            <Plus size={16} /> Yeni Gönderim
          </button>
        </div>
      </div>

      {loading && entries.length === 0 ? (
        <div className="text-gray-400 text-sm py-12 text-center">Yükleniyor...</div>
      ) : (
        <div className={`transition-opacity ${loading ? "opacity-60" : ""}`} aria-busy={loading}>
          <ShipmentCalendar entries={entries} />
        </div>
      )}

      {showCreate && (
        <CampaignModal
          onClose={() => setShowCreate(false)}
          onCreated={() => { setShowCreate(false); load(); }}
        />
      )}
    </div>
  );
}

/* ======================= INFLUENCER EKLE/DÜZENLE ======================= */
// Excel "Kayıtlı Influencer" seçenekleri (dropdown). Serbest-metin alanlar kişi tarafından dolar.
const ANLASMA_SEKLI = ["Barter", "PR", "Ücretli İş Birliği"];       // İş Birliği Türü
// Ücretli iş birliği mi? (anlaşılan ücret alanı bu türde vurgulanır)
const isPaid = (a) => String(a || "").toLocaleLowerCase("tr").includes("ücret");
const PLATFORM_OPTS = ["İnstagram", "Tiktok"];                       // Platform
const INF_TURU_BASE = ["Mikro", "Makro", "Nano/UGC", "Mid-Tier"];   // Influencer Türü
const BEDEN_OPTS = ["XXS", "XS", "S", "M", "L", "XL"];              // Beden Üst / Alt
const influencerTuru = (fc) => {
  const n = parseInt(String(fc ?? "").replace(/[^\d]/g, ""), 10) || 0;
  return n >= 100000 ? "Makro" : n >= 10000 ? "Micro" : "Nano";
};

// TR 81 il — adresten İl/İlçe best-effort çıkarımı için (MNG kargo barkodu İl/İlçe ister).
const TR_ILLER = ["Adana", "Adıyaman", "Afyonkarahisar", "Ağrı", "Amasya", "Ankara", "Antalya", "Artvin", "Aydın", "Balıkesir", "Bilecik", "Bingöl", "Bitlis", "Bolu", "Burdur", "Bursa", "Çanakkale", "Çankırı", "Çorum", "Denizli", "Diyarbakır", "Edirne", "Elazığ", "Erzincan", "Erzurum", "Eskişehir", "Gaziantep", "Giresun", "Gümüşhane", "Hakkari", "Hatay", "Isparta", "Mersin", "İstanbul", "İzmir", "Kars", "Kastamonu", "Kayseri", "Kırklareli", "Kırşehir", "Kocaeli", "Konya", "Kütahya", "Malatya", "Manisa", "Kahramanmaraş", "Mardin", "Muğla", "Muş", "Nevşehir", "Niğde", "Ordu", "Rize", "Sakarya", "Samsun", "Siirt", "Sinop", "Sivas", "Tekirdağ", "Tokat", "Trabzon", "Tunceli", "Şanlıurfa", "Uşak", "Van", "Yozgat", "Zonguldak", "Aksaray", "Bayburt", "Karaman", "Kırıkkale", "Batman", "Şırnak", "Bartın", "Ardahan", "Iğdır", "Yalova", "Karabük", "Kilis", "Osmaniye", "Düzce"];
const _trNorm = (s) => String(s || "").toLocaleLowerCase("tr")
  .replace(/i̇/g, "i").replace(/ı/g, "i").replace(/ş/g, "s").replace(/ğ/g, "g")
  .replace(/ü/g, "u").replace(/ö/g, "o").replace(/ç/g, "c").trim();

// Adresten İl/İlçe çıkar (best-effort). "…Kadıköy/İstanbul" veya adreste geçen il adı.
function parseIlIlce(adres) {
  const s = String(adres || "").trim();
  if (!s) return { il: "", ilce: "" };
  let il = "", ilce = "";
  const m = s.match(/([A-Za-zÇĞİÖŞÜçğıöşü.\s]+?)\s*\/\s*([A-Za-zÇĞİÖŞÜçğıöşü.\s]+?)\s*$/);
  if (m) {
    const cand = m[2].trim();
    const prov = TR_ILLER.find((p) => _trNorm(p) === _trNorm(cand) || _trNorm(cand).endsWith(_trNorm(p)));
    if (prov) { il = prov; ilce = m[1].trim().split(/\s+/).slice(-1)[0]; }
  }
  if (!il) {
    const ns = _trNorm(s); let best = -1;
    for (const p of TR_ILLER) { const pos = ns.lastIndexOf(_trNorm(p)); if (pos > best) { best = pos; il = p; } }
    if (best < 0) il = "";
  }
  if (il && !ilce) {
    const re = new RegExp("([A-Za-zÇĞİÖŞÜçğıöşü.]+)\\s*[\\/, ]\\s*" + il.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), "i");
    const mm = s.match(re);
    if (mm) ilce = mm[1].trim();
  }
  ilce = String(ilce || "").replace(/[.,]/g, "").trim();
  return { il, ilce };
}

function InfluencerFormModal({ initial, onClose, onSaved }) {
  const isEdit = !!initial;
  const addr = initial?.shipping_address || {};
  const [form, setForm] = useState({
    name: initial?.name || "", platform: initial?.platform || "instagram",
    handle: initial?.handle || "", instagram: initial?.instagram || "", tiktok: initial?.tiktok || "",
    birthday: (initial?.birthday || "").slice(0, 10),
    phone: initial?.phone || "", email: initial?.email || "",
    follower_count: initial?.follower_count || 0,
    coupon_code: initial?.coupon_code || "", aff_id: initial?.aff_id || "",
    commission_rate: initial?.commission_rate || 0,
    anlasma_sekli: initial?.anlasma_sekli || "",
    fee_amount: initial?.fee_amount ?? "",
    influencer_turu: initial?.influencer_turu || "",
    beden_alt: initial?.beden_alt || "", beden_ust: initial?.beden_ust || "",
    notes: initial?.notes || "",
    rating: initial?.rating || null,
    address_full_name: addr.full_name || "", address_phone: addr.phone || "",
    il: addr.il || "", ilce: addr.ilce || "", adres: initial?.adres || addr.adres || "",
  });
  const [saving, setSaving] = useState(false);
  const set = (k, v) => setForm((f) => ({ ...f, [k]: v }));
  // KVKK: telefon / adres şifreli saklanır, liste maskeli verir. Düzenlemede "Göster" gerçek değeri açar
  // (sayaçlı + denetimli). Maskeli değer değiştirilmeden kaydedilirse sunucu mevcut değeri korur.
  const [contactMasked, setContactMasked] = useState(
    isEdit && /[*•]/.test(`${initial?.phone || ""}${initial?.adres || ""}${addr.adres || ""}${addr.phone || ""}`));
  const [revealing, setRevealing] = useState(false);
  const revealContact = async () => {
    setRevealing(true);
    try {
      const r = await axios.get(`${API}/influencers/${initial.id}/contact`, auth());
      const c = r.data || {};
      const sa = c.shipping_address || {};
      setForm((f) => ({
        ...f,
        phone: c.phone || sa.phone || "",
        adres: c.adres || sa.adres || "",
        il: f.il || sa.il || "", ilce: f.ilce || sa.ilce || "",
      }));
      setContactMasked(false);
    } catch (e) {
      toast.error(e.response?.data?.detail || "İletişim bilgisi açılamadı");
    } finally {
      setRevealing(false);
    }
  };
  // Influencer Türü seçenekleri: Excel tabanı + backend'in eklediği özel tipler (tekilleştirilmiş).
  const [turuTypes, setTuruTypes] = useState(INF_TURU_BASE);
  const mergeTuru = (extra) => {
    const seen = new Set();
    return [...INF_TURU_BASE, ...(extra || [])].filter((t) => {
      const k = String(t).toLowerCase();
      if (!t || seen.has(k)) return false;
      seen.add(k);
      return true;
    });
  };
  useEffect(() => {
    axios.get(`${API}/influencer-types`, auth())
      .then((r) => setTuruTypes(mergeTuru(r.data?.types)))
      .catch(() => setTuruTypes(INF_TURU_BASE));
  }, []);
  const addTuruType = async () => {
    const name = window.prompt("Yeni influencer türü:");
    if (!name || !name.trim()) return;
    try {
      const r = await axios.post(`${API}/influencer-types`, { name: name.trim() }, auth());
      setTuruTypes(r.data?.types || turuTypes);
      set("influencer_turu", name.trim());
    } catch { toast.error("Tip eklenemedi"); }
  };

  const save = async () => {
    if (!form.name.trim()) return toast.error("İsim gerekli");
    setSaving(true);
    const body = {
      // handle formdan KALKTI → Instagram (yoksa TikTok) handle olarak kullanılır (ekranlar bozulmasın).
      name: form.name, platform: form.platform, handle: form.handle || form.instagram || form.tiktok || "",
      instagram: form.instagram, tiktok: form.tiktok, birthday: form.birthday || null,
      phone: form.phone, email: form.email,
      follower_count: parseInt(String(form.follower_count).replace(/[^\d]/g, ""), 10) || 0,
      coupon_code: form.coupon_code, aff_id: form.aff_id,
      commission_rate: Number(form.commission_rate) || 0,
      anlasma_sekli: form.anlasma_sekli,
      influencer_turu: form.influencer_turu || influencerTuru(form.follower_count),
      beden_alt: form.beden_alt, beden_ust: form.beden_ust,
      notes: form.notes,
      rating: form.rating || null,   // iş birliği durumu (kırmızı/sarı/yeşil) — yalnız bu formdan
      adres: form.adres,   // Excel "Adres" (serbest metin) — üst düzey alan (liste sütunu)
      shipping_address: {
        // Alıcı Adı alanı KALKTI → her zaman influencer'ın İsim Soyisim'i.
        full_name: form.name, phone: form.phone,   // alıcı telefonu = influencer ana telefonu (ayrı alan yok)
        il: form.il, ilce: form.ilce, adres: form.adres,   // kargo akışı için de yaz (senkron)
      },
    };
    try {
      if (isEdit) {
        await axios.put(`${API}/influencers/${initial.id}`, body, auth());
        toast.success("Influencer güncellendi");
      } else {
        await axios.post(`${API}/influencers`, body, auth());
        toast.success("Influencer eklendi");
      }
      onSaved();
    } catch (e) {
      toast.error(e.response?.data?.detail || "Kaydedilemedi");
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal title={isEdit ? "Influencer Düzenle" : "Yeni Influencer"} onClose={onClose}>
      {contactMasked && (
        <div className="flex items-center justify-between gap-3 mb-3 px-3 py-2 rounded-lg border border-amber-200 bg-amber-50 text-xs text-amber-800">
          <span>Telefon ve adres KVKK gereği maskeli gösteriliyor.</span>
          <button type="button" onClick={revealContact} disabled={revealing} data-testid="inf-contact-reveal"
            className="inline-flex items-center gap-1 px-2 py-0.5 text-xs border rounded bg-white hover:bg-gray-50 disabled:opacity-50">
            {revealing ? "Açılıyor…" : "Göster"}
          </button>
        </div>
      )}
      <div className="grid grid-cols-2 gap-3">
        <div className="col-span-2">
          <Field label="İş birliği durumu">
            <RatingPicker value={form.rating} onChange={(v) => set("rating", v)} />
          </Field>
        </div>
        <Field label="İsim Soyisim *"><input data-testid="inf-name" className="inp" value={form.name} onChange={(e) => set("name", e.target.value)} placeholder="Ad Soyad" /></Field>
        <Field label="Platform">
          <select data-testid="inf-platform" className="inp" value={form.platform} onChange={(e) => set("platform", e.target.value)}>
            <option value="">— Seçin —</option>
            {PLATFORM_OPTS.map((p) => <option key={p} value={p}>{p}</option>)}
          </select>
        </Field>
        <Field label="Instagram (@)"><input data-testid="inf-handle" className="inp" value={form.instagram} onChange={(e) => set("instagram", e.target.value)} placeholder="@kullanici" /></Field>
        <Field label="TikTok (@) (opsiyonel)"><input className="inp" value={form.tiktok} onChange={(e) => set("tiktok", e.target.value)} placeholder="@kullanici" /></Field>
        <Field label="Doğum Günü"><input type="date" className="inp" value={form.birthday} onChange={(e) => set("birthday", e.target.value)} /></Field>
        <Field label="Telefon"><input className="inp" value={form.phone} onChange={(e) => set("phone", e.target.value)} /></Field>
        <Field label="İş Birliği Türü">
          <select className="inp" value={form.anlasma_sekli} onChange={(e) => set("anlasma_sekli", e.target.value)} data-testid="inf-anlasma">
            <option value="">— Seçin —</option>
            {ANLASMA_SEKLI.map((a) => <option key={a} value={a}>{a}</option>)}
          </select>
        </Field>
        {/* Ücretli iş birliğinde anlaşılan standart ücret — PR kaydı açılınca oraya ön-dolar. */}
        <Field label="Anlaşılan Ücret (₺)">
          <input className="inp" inputMode="decimal" value={form.fee_amount}
            onChange={(e) => set("fee_amount", e.target.value)}
            placeholder={isPaid(form.anlasma_sekli) ? "Ör. 15000" : "Ücretli iş birliğinde doldurun"}
            data-testid="inf-fee-amount" />
          <p className="text-[10px] text-gray-400 mt-1">Bu kişiyle standart anlaşma tutarı. Her gönderide ayrıca değiştirilebilir.</p>
        </Field>
        <Field label="Influencer Türü">
          <div className="flex gap-1">
            <select className="inp flex-1" value={form.influencer_turu} onChange={(e) => set("influencer_turu", e.target.value)} data-testid="inf-turu">
              <option value="">— Seçin —</option>
              {turuTypes.map((t) => <option key={t} value={t}>{t}</option>)}
            </select>
            <button type="button" onClick={addTuruType} title="Yeni tip ekle" className="px-3 border rounded-lg hover:bg-gray-50">+</button>
          </div>
        </Field>
        <Field label="Beden Üst">
          <select className="inp" value={form.beden_ust} onChange={(e) => set("beden_ust", e.target.value)} data-testid="inf-beden-ust">
            <option value="">— Seçin —</option>
            {BEDEN_OPTS.map((b) => <option key={b} value={b}>{b}</option>)}
          </select>
        </Field>
        <Field label="Beden Alt">
          <select className="inp" value={form.beden_alt} onChange={(e) => set("beden_alt", e.target.value)} data-testid="inf-beden-alt">
            <option value="">— Seçin —</option>
            {BEDEN_OPTS.map((b) => <option key={b} value={b}>{b}</option>)}
          </select>
        </Field>
        <Field label="Adres" full>
          <textarea data-testid="inf-adres" className="inp h-16" value={form.adres}
            onChange={(e) => {
              const v = e.target.value;
              const g = parseIlIlce(v);   // adresten İl/İlçe çıkar (best-effort, yalnız boş alanları doldur)
              setForm((f) => ({ ...f, adres: v, il: f.il || g.il, ilce: f.ilce || g.ilce }));
            }}
            placeholder="Kargo/teslim adresi (ör. … Mah. … Sok. No:2 Kadıköy/İstanbul)" />
        </Field>
      </div>
      <p className="text-xs font-semibold text-gray-500 mt-4 mb-2">Kargo Detayı (MNG barkodu için İl/İlçe — Adres'ten otomatik dolar, düzenlenebilir; alıcı adı=İsim Soyisim, telefon=Telefon)</p>
      <div className="grid grid-cols-2 gap-3">
        <Field label="İl"><input className="inp" value={form.il} onChange={(e) => set("il", e.target.value)} data-testid="inf-il" /></Field>
        <Field label="İlçe"><input className="inp" value={form.ilce} onChange={(e) => set("ilce", e.target.value)} data-testid="inf-ilce" /></Field>
      </div>
      <Field label="Not" full>
        <textarea data-testid="inf-not" className="inp h-20" value={form.notes} onChange={(e) => set("notes", e.target.value)} placeholder="Bu influencer'a özel notlar…" />
      </Field>
      <div className="flex justify-end gap-2 mt-5">
        <button onClick={onClose} className="px-4 py-2 text-sm border rounded-lg">İptal</button>
        <button onClick={save} disabled={saving} data-testid="inf-save" className="px-4 py-2 text-sm bg-black text-white rounded-lg disabled:opacity-50">
          {saving ? "Kaydediliyor..." : "Kaydet"}
        </button>
      </div>
    </Modal>
  );
}

/* ======================= INFLUENCER DETAY (ROI + o kişinin gönderimleri) ======================= */
function DetailModal({ influencerId, onClose }) {
  const [inf, setInf] = useState(null);
  const [roi, setRoi] = useState(null);
  const [showCampaign, setShowCampaign] = useState(false);

  const load = useCallback(async () => {
    try {
      const [d, r] = await Promise.all([
        axios.get(`${API}/influencers/${influencerId}`, auth()),
        axios.get(`${API}/influencers/${influencerId}/roi`, auth()),
      ]);
      setInf(d.data);
      setRoi(r.data);
    } catch {
      toast.error("Detay yüklenemedi");
    }
  }, [influencerId]);

  useEffect(() => { load(); }, [load]);

  const act = makeCampaignActions(load);

  if (!inf) return <Modal title="Yükleniyor..." onClose={onClose}><div className="py-8 text-center text-gray-400">...</div></Modal>;

  return (
    <Modal title={`${inf.name} · ${inf.platform}`} wide onClose={onClose}>
      {/* Kimlik satırı */}
      <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-gray-600 mb-4">
        <RatingDots id={inf.id} rating={inf.rating} size={12} />
        {inf.instagram && <span className="flex items-center gap-1"><Instagram size={12} /> {inf.instagram}</span>}
        {inf.tiktok && <span className="flex items-center gap-1"><TikTokIcon size={12} /> {inf.tiktok}</span>}
        {inf.phone && <span>☎ {inf.phone}</span>}
        {inf.birthday && <span className="flex items-center gap-1"><Calendar size={12} /> {fmtDate(inf.birthday)}</span>}
        {inf.coupon_code && <span className="text-amber-700">Kupon: {inf.coupon_code}</span>}
      </div>
      {inf.notes && <p className="text-xs bg-stone-50 border rounded-lg p-2 mb-4 whitespace-pre-wrap">{inf.notes}</p>}

      {/* ROI */}
      {roi && (
        <div className="grid grid-cols-2 md:grid-cols-5 gap-3 mb-5" data-testid="inf-roi">
          <Stat icon={<DollarSign size={16} />} label="Toplam Maliyet" value={money(roi.cost.total_cost)} color="red" />
          <Stat icon={<TrendingUp size={16} />} label="Brüt Ciro" value={money(roi.revenue.revenue)} color="green" />
          {/* Üyelik alanındaki "net iadeler çıktıktan sonraki ciro" ile aynı tanım: brüt − onaylı iade tutarı. */}
          <Stat icon={<TrendingUp size={16} />} label="Net Ciro (iadeler düşülmüş)" value={money(roi.revenue.net_revenue ?? roi.revenue.revenue)} color="green" />
          <Stat icon={<DollarSign size={16} />} label="Net Kâr" value={money(roi.net_profit)} color={roi.net_profit >= 0 ? "green" : "red"} />
          <Stat icon={<TrendingUp size={16} />} label="ROAS" value={roi.roas != null ? `${roi.roas}x` : "—"} color="blue" />
        </div>
      )}
      {roi && (
        <p className="text-xs text-gray-500 mb-4">
          {roi.revenue.successful_orders} başarılı sipariş · {roi.cost.campaign_count} gönderim · {roi.cost.shared_count} paylaşım · Komisyon: {money(roi.revenue.commission_due)}
          {Number(roi.revenue.returns_amount) > 0 && <> · İade: {money(roi.revenue.returns_amount)} ({roi.revenue.returns_orders} sipariş)</>}
          {roi.roas_gross != null && roi.roas_gross !== roi.roas && <> · Brüt ROAS: {roi.roas_gross}x</>}
        </p>
      )}

      {/* Gönderimler */}
      <div className="flex items-center justify-between mb-2">
        <h3 className="font-semibold text-sm">Bu influencer'a gönderimler</h3>
        <div className="flex items-center gap-2">
          <button
            onClick={async () => {
              try {
                const r = await axios.get(`${API}/influencer-pr/export`, { ...auth(), params: { influencer_id: influencerId }, responseType: "blob" });
                const url = URL.createObjectURL(r.data);
                const a = document.createElement("a");
                a.href = url; a.download = "influencer-pr-rapor.xlsx"; a.click();
                URL.revokeObjectURL(url);
              } catch { toast.error("Rapor oluşturulamadı"); }
            }}
            className="inline-flex items-center gap-1 text-sm border px-3 py-1.5 rounded-lg hover:bg-gray-50" title="Bu influencer'ın PR işlemlerini Excel indir">
            <Download size={14} /> PR Rapor
          </button>
          <button onClick={() => setShowCampaign(true)} data-testid="new-campaign-btn" className="inline-flex items-center gap-1 text-sm border px-3 py-1.5 rounded-lg hover:bg-gray-50">
            <Plus size={14} /> Yeni Gönderim
          </button>
        </div>
      </div>
      <div className="space-y-2">
        {(inf.campaigns || []).length === 0 && <p className="text-xs text-gray-400 py-3">Henüz gönderim yok. Her ürün gönderimi için ayrı kart açın.</p>}
        {(inf.campaigns || []).map((c) => <CampaignCard key={c.id} c={c} act={act} />)}
      </div>

      {showCampaign && (
        <CampaignModal influencerId={influencerId} onClose={() => setShowCampaign(false)} onCreated={() => { setShowCampaign(false); load(); }} />
      )}
    </Modal>
  );
}

/* ======================= TEK GÖNDERİM KARTI (paylaşılan) ======================= */
function CampaignCard({ c, act, showInfluencer }) {
  return (
    <div className="border rounded-lg p-3" data-testid={`campaign-${c.id}`}>
      <div className="flex items-center justify-between gap-2">
        <div className="min-w-0">
          {showInfluencer && (
            <div className="text-[11px] text-pink-700 font-medium flex items-center gap-1 truncate">
              <Instagram size={11} /> {c.influencer_name || "—"}{c.influencer_handle ? ` · ${c.influencer_handle}` : ""}
              <RatingDots id={c.influencer_id} name={c.influencer_name} size={8} className="ml-1" />
            </div>
          )}
          <span className="font-medium text-sm">{c.title}</span>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          <Badge>{c.status}</Badge>
          <Badge tone="cargo">{c.cargo_status}</Badge>
        </div>
      </div>
      <div className="flex flex-wrap gap-3 mt-2 text-[11px] text-gray-500">
        <span>Ücret: {money(c.fee_paid)}</span>
        <span>Ürün: {money(c.product_cost)}</span>
        <span>Kargo: {money(c.cargo_cost)}</span>
        {c.sent_at && <span className="text-gray-700 font-medium">Gönderim: {fmtDate(c.sent_at)}</span>}
        {c.cargo_barcode && <span className="text-blue-600">Barkod: {c.cargo_barcode}</span>}
        {c.cargo_tracking_no && <span className="text-blue-600">Takip: {c.cargo_tracking_no}</span>}
      </div>
      {c.directives && (
        <p className="text-[11px] text-gray-500 mt-2 bg-stone-50 border rounded p-2 whitespace-pre-wrap">
          <b className="text-gray-600">Direktif:</b> {c.directives}
        </p>
      )}
      {(c.sent_products || []).length > 0 && (
        <div className="flex flex-wrap items-center gap-1.5 mt-2" data-testid={`sent-products-${c.id}`}>
          {(c.sent_products || []).map((p, pi) => (
            <span key={pi} className="text-[10px] bg-stone-100 border rounded px-1.5 py-0.5">
              {p.name}{p.size ? ` (${p.size})` : ""} ×{p.qty || 1}
            </span>
          ))}
          {c.stock_deducted ? (
            <span className="text-[10px] font-semibold bg-emerald-50 text-emerald-700 border border-emerald-200 rounded px-1.5 py-0.5">stok düşüldü ✓</span>
          ) : (
            <span className="text-[10px] bg-amber-50 text-amber-700 border border-amber-200 rounded px-1.5 py-0.5">stok düşülmedi</span>
          )}
        </div>
      )}
      {/* Paylaşıldı tik'i + içerik linki */}
      <div className="flex items-center flex-wrap gap-3 mt-3">
        <label className="flex items-center gap-1.5 text-xs cursor-pointer select-none" data-testid={`shared-toggle-${c.id}`}>
          <input type="checkbox" checked={!!c.shared} onChange={() => act.toggleShared(c)} className="w-4 h-4 accent-green-600" />
          <span className={c.shared ? "text-green-700 font-medium flex items-center gap-1" : "text-gray-600"}>
            {c.shared ? <><CheckCircle size={12} /> Paylaşıldı{c.shared_at ? ` · ${fmtDate(c.shared_at)}` : ""}</> : "Paylaşıldı mı?"}
          </span>
        </label>
        {c.shared && (
          <input
            defaultValue={c.content_url || ""}
            onBlur={(e) => { const v = e.target.value.trim(); if (v !== (c.content_url || "")) act.saveContentUrl(c.id, v); }}
            placeholder="İçerik linki (yapıştır)…"
            className="text-xs border rounded px-2 py-1 flex-1 min-w-[180px]"
          />
        )}
      </div>
      <div className="flex gap-2 mt-3 flex-wrap">
        {!c.sent_at && (
          <button onClick={() => act.markSent(c.id)} className="text-xs inline-flex items-center gap-1 border px-2 py-1 rounded hover:bg-gray-50">
            <Share2 size={12} /> Gönderildi İşaretle
          </button>
        )}
        <button onClick={() => act.createCargo(c.id)} className="text-xs inline-flex items-center gap-1 border px-2 py-1 rounded hover:bg-gray-50">
          <Truck size={12} /> Kargo Oluştur
        </button>
        {c.stock_deducted && (
          <button onClick={() => act.uncommitStock(c.id)} title="Yanlış seçim/vazgeçme: ürünleri stoğa geri yükler"
            className="text-xs inline-flex items-center gap-1 border border-amber-300 text-amber-700 px-2 py-1 rounded hover:bg-amber-50">
            Stok Geri Al
          </button>
        )}
        <button onClick={() => act.delCampaign(c.id)} className="text-xs inline-flex items-center gap-1 border px-2 py-1 rounded text-red-600 hover:bg-red-50 ml-auto">
          <Trash2 size={12} /> Sil
        </button>
      </div>
    </div>
  );
}

/* Gönderilecek ürün seçici: ürün ara → beden/varyant seç → adet → listeye ekle.
   sizes (opsiyonel): influencer'ın kayıtlı üst/alt bedeni — eşleşen beden butonu
   vurgulanır, böylece yanlış beden gönderilmesi zorlaşır. */
/* stockNote: seçili ürünlerin altındaki stok açıklaması. Varsayılan metin, kaydı ANINDA
   stok düşen akışlar içindir (Yeni Ürün Gönderimi → commit-products, Yeni Kargo → reship).
   PR kaydı formu stok DÜŞÜRMEZ; kendi metnini geçer. */
function ProductPicker({ picked, setPicked, sizes, stockNote = "Kaydedince bu ürünler gönderime işlenir ve STOKTAN DÜŞÜLÜR." }) {
  const [q, setQ] = useState("");
  const [results, setResults] = useState([]);
  const [searching, setSearching] = useState(false);
  const wanted = useMemo(
    () => new Set((sizes || []).filter(Boolean).map((s) => String(s).trim().toLocaleUpperCase("tr"))),
    [sizes]
  );
  const isWanted = (sz) => wanted.has(String(sz || "").trim().toLocaleUpperCase("tr"));

  useEffect(() => {
    if (q.trim().length < 2) { setResults([]); return; }
    const t = setTimeout(async () => {
      setSearching(true);
      try {
        const { data } = await axios.get(`${API}/products`, {
          ...auth(), params: { search: q.trim(), limit: 8, admin_view: 1 },
        });
        setResults(data.products || data || []);
      } catch { setResults([]); }
      finally { setSearching(false); }
    }, 350);
    return () => clearTimeout(t);
  }, [q]);

  const addVariant = (p, v) => {
    const bc = v?.barcode || p.barcode;
    if (!bc) { toast.error("Bu varyantın barkodu yok"); return; }
    if (picked.some((x) => x.barcode === bc)) { toast.error("Zaten listede"); return; }
    setPicked([...picked, { barcode: bc, name: p.name, size: v?.size || "", stock: v ? v.stock : p.stock, qty: 1 }]);
    setQ(""); setResults([]);
  };

  return (
    <div>
      <input className="inp" value={q} onChange={(e) => setQ(e.target.value)}
        placeholder="Ürün ara (en az 2 harf)..." data-testid="seeding-product-search" />
      {searching && <p className="text-[11px] text-gray-400 mt-1">Aranıyor…</p>}
      {results.length > 0 && (
        <div className="border rounded-lg mt-1 max-h-52 overflow-y-auto divide-y">
          {results.map((p) => (
            <div key={p.id} className="p-2">
              <p className="text-xs font-medium">{p.name}</p>
              <div className="flex flex-wrap gap-1.5 mt-1">
                {(p.variants || []).length > 0 ? (p.variants || []).map((v) => (
                  <button key={v.barcode || v.id} type="button" onClick={() => addVariant(p, v)}
                    disabled={Number(v.stock) <= 0}
                    title={isWanted(v.size) ? "Influencer'ın kayıtlı bedeni" : undefined}
                    className={`text-[11px] border rounded px-2 py-0.5 hover:bg-gray-50 disabled:opacity-40 disabled:line-through ${
                      isWanted(v.size) ? "border-purple-500 bg-purple-50 text-purple-700 font-semibold" : ""}`}>
                    {isWanted(v.size) && "★ "}{v.size || "STD"} · stok {v.stock ?? "?"}
                  </button>
                )) : (
                  <button type="button" onClick={() => addVariant(p, null)}
                    className="text-[11px] border rounded px-2 py-0.5 hover:bg-gray-50">
                    Ekle · stok {p.stock ?? "?"}
                  </button>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
      {picked.length > 0 && (
        <div className="mt-2 space-y-1.5" data-testid="seeding-picked-list">
          {picked.map((it, i) => (
            <div key={it.barcode} className="flex items-center gap-2 bg-gray-50 border rounded px-2 py-1.5 text-xs">
              <span className="flex-1 truncate">{it.name} {it.size && <b>({it.size})</b>}</span>
              <input type="number" min={1} max={it.stock || 99} value={it.qty}
                onChange={(e) => {
                  const qv = Math.max(1, Math.min(Number(e.target.value) || 1, it.stock || 99));
                  setPicked(picked.map((x, xi) => (xi === i ? { ...x, qty: qv } : x)));
                }}
                className="w-14 border rounded px-1.5 py-0.5 text-center" />
              <button type="button" onClick={() => setPicked(picked.filter((_, xi) => xi !== i))}
                className="text-red-500 hover:text-red-700"><Trash2 size={12} /></button>
            </div>
          ))}
          <p className="text-[10px] text-gray-400">{stockNote}</p>
        </div>
      )}
    </div>
  );
}

/* Yeni gönderim. influencerId verilirse (detaydan) o kişiye; verilmezse (geçmiş sekmesi)
   önce influencer seçtirilir. */
function CampaignModal({ influencerId, onClose, onCreated }) {
  const preset = !!influencerId;
  const [infList, setInfList] = useState([]);
  const [chosenInf, setChosenInf] = useState(influencerId || "");
  const [form, setForm] = useState({ title: "", fee_paid: 0, product_cost: 0, cargo_cost: 0, directives: "" });
  const [picked, setPicked] = useState([]);
  const [saving, setSaving] = useState(false);
  const set = (k, v) => setForm((f) => ({ ...f, [k]: v }));

  // Influencer listesi her durumda yüklenir: preset değilse SEÇİM için, preset ise
  // seçili kişinin kayıtlı ÜST/ALT bedenini gösterebilmek için.
  useEffect(() => {
    (async () => {
      try {
        const r = await axios.get(`${API}/influencers`, auth());
        setInfList(r.data?.influencers || []);
      } catch { /* sessiz */ }
    })();
  }, []);

  const [shipUst, shipAlt] = useInfSizes(infList, influencerId || chosenInf);

  const save = async () => {
    const infId = influencerId || chosenInf;
    if (!infId) return toast.error("Önce influencer seçin");
    if (!form.title.trim()) return toast.error("Başlık gerekli");
    setSaving(true);
    try {
      const r = await axios.post(`${API}/influencers/${infId}/campaigns`, {
        title: form.title, fee_paid: Number(form.fee_paid) || 0,
        product_cost: Number(form.product_cost) || 0, cargo_cost: Number(form.cargo_cost) || 0,
        directives: form.directives,
      }, auth());
      const cid = r.data?.campaign?.id;
      if (cid && picked.length > 0) {
        await axios.post(`${API}/influencer-campaigns/${cid}/commit-products`, {
          products: picked.map((p) => ({ barcode: p.barcode, qty: p.qty })),
          auto_cost: !(Number(form.product_cost) > 0),
        }, auth());
        toast.success(`Gönderim oluşturuldu — ${picked.length} ürün stoktan düşüldü`);
      } else {
        toast.success("Gönderim oluşturuldu");
      }
      onCreated();
    } catch (e) {
      toast.error(e.response?.data?.detail || "Oluşturulamadı");
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal title="Yeni Ürün Gönderimi" onClose={onClose}>
      {!preset && (
        <Field label="Influencer *" full>
          {infList.length === 0 ? (
            <p className="text-xs text-amber-600 py-1">Önce "Kayıtlı Influencerlar" sekmesinden influencer ekleyin.</p>
          ) : (
            <InfluencerPicker list={infList} value={chosenInf} onChange={setChosenInf}
              placeholder="— Influencer seçin —" testId="ship-inf-select" />
          )}
        </Field>
      )}
      <div className="grid grid-cols-2 gap-3 mt-1">
        <Field label="Başlık *" full><input data-testid="camp-title" className="inp" value={form.title} onChange={(e) => set("title", e.target.value)} placeholder="Örn. Temmuz gönderimi" /></Field>
        <Field label="Ödenen Ücret"><input type="number" className="inp" value={form.fee_paid} onChange={(e) => set("fee_paid", e.target.value)} /></Field>
        <Field label="Ürün Maliyeti (boşsa seçilen ürünlerden otomatik)"><input type="number" className="inp" value={form.product_cost} onChange={(e) => set("product_cost", e.target.value)} /></Field>
        <Field label="Kargo Maliyeti"><input type="number" className="inp" value={form.cargo_cost} onChange={(e) => set("cargo_cost", e.target.value)} /></Field>
      </div>
      <Field label="Gönderilecek Ürünler (beden seçin — kaydetmede stoktan düşer)" full>
        <ProductPicker picked={picked} setPicked={setPicked} sizes={[shipUst, shipAlt]} />
        <BedenHint ust={shipUst} alt={shipAlt} />
      </Field>
      <Field label="İçerik Talimatları / Direktif (boşsa 9:16 dikey format standardı otomatik eklenir)" full>
        <textarea className="inp h-24" value={form.directives} onChange={(e) => set("directives", e.target.value)} placeholder="Boş bırakırsanız zorunlu içerik standartları (9:16 dikey format, @meluni mention) otomatik eklenir." />
      </Field>
      <div className="flex justify-end gap-2 mt-4">
        <button onClick={onClose} className="px-4 py-2 text-sm border rounded-lg">İptal</button>
        <button onClick={save} disabled={saving} data-testid="camp-save" className="px-4 py-2 text-sm bg-black text-white rounded-lg disabled:opacity-50">
          {saving ? "..." : "Gönderim Oluştur"}
        </button>
      </div>
    </Modal>
  );
}

/* ---- küçük yardımcı bileşenler ---- */
function Modal({ title, children, onClose, wide }) {
  return (
    <div className="fixed inset-0 bg-black/40 z-50 flex items-start justify-center overflow-y-auto py-10 px-4" onClick={onClose}>
      <div className={`bg-white rounded-xl w-full ${wide ? "max-w-3xl" : "max-w-xl"} p-6`} onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between mb-4">
          <h2 className="font-bold text-lg">{title}</h2>
          <button onClick={onClose}><X size={20} /></button>
        </div>
        {children}
      </div>
    </div>
  );
}
function Field({ label, children, full }) {
  return (
    <div className={full ? "col-span-2" : ""}>
      <label className="block text-xs text-gray-500 mb-1">{label}</label>
      {children}
    </div>
  );
}
function Stat({ icon, label, value, color }) {
  const c = { red: "bg-red-50 text-red-700", green: "bg-green-50 text-green-700", blue: "bg-blue-50 text-blue-700" }[color] || "bg-gray-50 text-gray-700";
  return (
    <div className={`rounded-lg p-3 ${c}`}>
      <div className="flex items-center gap-1 text-[11px] opacity-80">{icon} {label}</div>
      <div className="text-lg font-bold mt-0.5">{value}</div>
    </div>
  );
}
function Badge({ children, tone }) {
  const c = tone === "cargo" ? "bg-blue-50 text-blue-600" : "bg-gray-100 text-gray-600";
  return <span className={`text-[10px] px-2 py-0.5 rounded-full ${c}`}>{children}</span>;
}
