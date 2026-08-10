import { useState, useEffect, useRef, useCallback } from "react";
import { createPortal } from "react-dom";
import axios from "axios";
import { toast } from "sonner";
import { RefreshCw, Search, Check, X, FileText, Printer, Download, ChevronDown, ChevronUp, AlertCircle } from "lucide-react";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription } from "../../components/ui/dialog";
import RooftrReturns from "./RooftrReturns";

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;

// Yalnızca iade verisi/akışı olan AKTİF platformlar gösterilir.
// Web Sitesi (site iadeleri) en başta, sonra Trendyol. Yeni bir pazaryeri
// entegrasyonu aktifleştiğinde (örn. Hepsiburada claim çekimi) buraya eklenir.
const PLATFORMS = [
  { key: "facette", label: "Web Sitesi", badge: "W", color: "bg-gray-800" },
  { key: "trendyol", label: "Trendyol", badge: "T", color: "bg-orange-500" },
  { key: "hepsiburada", label: "Hepsiburada", badge: "H", color: "bg-amber-600" },
];

const STATUS_TABS = [
  { key: "all", label: "Tüm İadeler" },
  { key: "acik_iade", label: "Açık İade" },
  { key: "aksiyon_bekleyen", label: "Aksiyon Bekleyen" },
  { key: "onaylanan", label: "Onaylanan" },
  { key: "reddedilen", label: "Reddedilen" },
];

function formatCurrency(val) {
  return new Intl.NumberFormat("tr-TR", { style: "currency", currency: "TRY" }).format(val || 0);
}

function formatDate(d) {
  if (!d) return "-";
  try { return new Date(d).toLocaleDateString("tr-TR", { day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit" }); }
  catch { return d; }
}

// Gider pusulası takip numarası: 6 haneli, başında sıfırla (085490)
function pad6(n) {
  const v = parseInt(String(n).replace(/\D/g, ""), 10);
  return isNaN(v) ? "" : String(v).padStart(6, "0");
}

// Tutarı sadece tamsayı kısmını TR yazıya çevirir (kuruşsuz): 1862 -> "Binsekizyüzaltmışiki"
function sayiToWords(num) {
  num = Math.abs(Math.floor(Number(num) || 0));
  if (num === 0) return "Sıfır";
  const birler = ["", "Bir", "İki", "Üç", "Dört", "Beş", "Altı", "Yedi", "Sekiz", "Dokuz"];
  const onlar = ["", "On", "Yirmi", "Otuz", "Kırk", "Elli", "Altmış", "Yetmiş", "Seksen", "Doksan"];
  const basamak = ["", "Bin", "Milyon", "Milyar", "Trilyon"];
  const uclu = (n) => {
    let s = "";
    const y = Math.floor(n / 100), o = Math.floor((n % 100) / 10), b = n % 10;
    if (y > 0) s += (y === 1 ? "" : birler[y]) + "Yüz";
    if (o > 0) s += onlar[o];
    if (b > 0) s += birler[b];
    return s;
  };
  const parts = [];
  let i = 0;
  while (num > 0) {
    const grp = num % 1000;
    if (grp > 0) {
      let g = uclu(grp);
      if (i === 1 && grp === 1) g = ""; // "Bin", "BirBin" değil
      parts.unshift(g + basamak[i]);
    }
    num = Math.floor(num / 1000);
    i++;
  }
  const joined = parts.join("");
  return joined.charAt(0) + joined.slice(1).toLocaleLowerCase("tr-TR");
}

// A4 yatay: sayfada aynı pusula 4 kopya (4 sütun), her sütun 74.25mm
const GP_COPIES = 4;            // bir A4 yatay sayfada aynı pusula 4 kopya (4 sütun)
const GP_SLIP_W_MM = 74.25;     // sütun genişliği (297mm / 4)
const GP_SLIP_H_MM = 210;       // sütun yüksekliği (A4 yatay)
const GP_PAGE_W_MM = 297;       // A4 yatay genişlik
const GP_PAGE_H_MM = 210;       // A4 yatay yükseklik

// Trendyol iade durumu — manuel ilerletme seçenekleri (Türkçe etiketli)
const CLAIM_STATUS_OPTS = [
  { value: "Created", label: "Açık İade" },
  { value: "WaitingInAction", label: "Aksiyon Bekleyen" },
  { value: "Accepted", label: "Onaylandı" },
  { value: "Rejected", label: "Reddedildi" },
  { value: "Cancelled", label: "İptal" },
];

// Manuel (sipariş-durumu kaynaklı) satırlar için sipariş iade durumları.
const ORDER_RETURN_STATUS_OPTS = [
  { value: "return_requested", label: "İade Talebi Oluşturuldu" },
  { value: "return_approved", label: "İade Onaylandı" },
  { value: "return_in_transit", label: "İade Kargoda" },
  { value: "returned", label: "İade Tamamlandı" },
  { value: "refunded", label: "İade Bedeli Ödendi" },
  { value: "partial_refunded", label: "Kısmi İade" },
  { value: "return_rejected", label: "İade Reddedildi" },
];

function ActionBadge({ action }) {
  if (action === "approved") return <span className="px-2 py-0.5 bg-green-100 text-green-700 text-xs font-bold rounded-full">Onaylandı</span>;
  if (action === "issued") return <span className="px-2 py-0.5 bg-red-100 text-red-700 text-xs font-bold rounded-full">İtiraz Edildi</span>;
  return <span className="px-2 py-0.5 bg-yellow-100 text-yellow-700 text-xs font-bold rounded-full">Bekliyor</span>;
}

export default function Returns() {
  const [claims, setClaims] = useState([]);
  const [loading, setLoading] = useState(true);
  const [syncing, setSyncing] = useState(false);
  const [page, setPage] = useState(1);
  const [total, setTotal] = useState(0);
  const [stats, setStats] = useState({});
  const [search, setSearch] = useState("");
  const [exporting, setExporting] = useState(false);
  const [debouncedSearch, setDebouncedSearch] = useState("");
  const [typeFilter, setTypeFilter] = useState("");
  const [platform, setPlatform] = useState("facette");
  // Web Sitesi sekmesi doğrudan sipariş bazlı iade akışını (RooftrReturns) gösterir; alt-sekme yok.
  const [statusTab, setStatusTab] = useState("all");
  const [tabCounts, setTabCounts] = useState({});
  // '(N talep)' parantez gösterimi kaldırıldı — talep sayacı state'i de söküldü (CI unused uyarısı)
  const [itemSel, setItemSel] = useState({}); // {claim_id: Set(claim_item_id)} - manuel adet onayı
  const toggleItem = (claimId, itemId, allIds) => {
    setItemSel(prev => {
      const cur = new Set(prev[claimId] || allIds);
      if (cur.has(itemId)) cur.delete(itemId); else cur.add(itemId);
      return { ...prev, [claimId]: cur };
    });
  };
  const [selectedIds, setSelectedIds] = useState(new Set());
  const [expandedId, setExpandedId] = useState(null);
  const [perms, setPerms] = useState([]);
  const [editRows, setEditRows] = useState({}); // onaylanmış iadede kalem kilidi açık mı: {claim_id: true}
  const canEditApproval = () => perms.includes("*") || perms.includes("returns.expense_note");
  const [gpModalOpen, setGpModalOpen] = useState(false);
  const [gpData, setGpData] = useState(null);
  const [gpLoading, setGpLoading] = useState(false);
  const [editGpNo, setEditGpNo] = useState(null); // gider pusulası no inline düzenleme (TY/HB): { key, value }
  const [bulkPrintData, setBulkPrintData] = useState(null);
  // Gider pusulası: takip no (085490'dan), matbu bindirme modu ve hizalama (mm)
  const [gpStart, setGpStart] = useState(() => localStorage.getItem("gp_next_no") || "085490");
  const [gpOverlay, setGpOverlay] = useState(() => localStorage.getItem("gp_overlay") !== "0");
  const [gpOffX, setGpOffX] = useState(() => parseFloat(localStorage.getItem("gp_off_x")) || 0);
  const [gpOffY, setGpOffY] = useState(() => parseFloat(localStorage.getItem("gp_off_y")) || 0);
  const [gpGuides, setGpGuides] = useState(false);
  const autoRefreshRef = useRef(null);

  // A2 - Ret sebebi modal state

  const limit = 20;

  // Aramayı 350ms geciktir (her tuşta istek atıp yarış/janklık yaşamamak için)
  useEffect(() => {
    const t = setTimeout(() => setDebouncedSearch(search), 350);
    return () => clearTimeout(t);
  }, [search]);

  const fetchClaims = useCallback(async () => {
    // Web Sitesi sekmesi kendi verisini gömülü <SiteReturns/> ile customer_returns'ten
    // ceker; bu yuzden Trendyol claims endpoint'ini cagirmaya gerek yok.
    if (platform === "facette") {
      setClaims([]); setTotal(0); setTabCounts({}); setStats({}); setLoading(false);
      return;
    }
    try {
      setLoading(true);
      const token = localStorage.getItem("token");
      // platform = 'trendyol' (pazaryeri). Backend tum claim'leri dondurur (mikro ihracat dahil).
      const params = new URLSearchParams({ page, limit, search: debouncedSearch, platform });
      if (statusTab && statusTab !== "all") params.append("status", statusTab);
      const res = await axios.get(`${API}/integrations/trendyol/claims?${params}`, {
        headers: { Authorization: `Bearer ${token}` }
      });
      setClaims(res.data.claims || []);
      setTotal(res.data.total || 0);
      setStats(res.data.stats || {});
      setTabCounts(res.data.tab_counts || {});
    } catch (err) {
      console.error(err);
    } finally {
      setLoading(false);
    }
  }, [page, debouncedSearch, statusTab, platform]);

  useEffect(() => { fetchClaims(); }, [fetchClaims]);

  // Yetkiler — onay geçmişini düzenleme yalnız muhasebe (returns.expense_note) / admin (*).
  useEffect(() => {
    axios.get(`${API}/admin/me/permissions`, { headers: { Authorization: `Bearer ${localStorage.getItem("token")}` } })
      .then((r) => setPerms(r.data?.permissions || []))
      .catch(() => {});
  }, []);

  // CANLI EŞ ZAMANLILIK: sayfa açılır açılmaz + her 2 dakikada bir pazaryerinden
  // HIZLI senkron (30 günlük pencere) çekilir; sekme sayıları TY/HB paneliyle uyuşur.
  // (Eskiden açılışta senkron YOKTU — sayılar 5 dk'ya kadar bayat kalıyordu.)
  useEffect(() => {
    const syncAndRefresh = async () => {
      if (platform !== "trendyol" && platform !== "hepsiburada") return;
      try {
        const token = localStorage.getItem("token");
        const syncUrl = platform === "hepsiburada"
          ? `${API}/integrations/hepsiburada/claims/sync?days_back=30`
          : `${API}/integrations/trendyol/claims/sync?days_back=30`;
        await axios.get(syncUrl, {
          headers: { Authorization: `Bearer ${token}` }
        });
        await fetchClaims();
      } catch (e) { /* silent */ }
    };
    syncAndRefresh(); // açılışta hemen canlı çek
    autoRefreshRef.current = setInterval(syncAndRefresh, 2 * 60 * 1000);
    return () => clearInterval(autoRefreshRef.current);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [platform]);

  const handleSync = async () => {
    setSyncing(true);
    try {
      const token = localStorage.getItem("token");
      const syncUrl = platform === "hepsiburada"
        ? `${API}/integrations/hepsiburada/claims/sync`
        : `${API}/integrations/trendyol/claims/sync`;
      const res = await axios.get(syncUrl, {
        headers: { Authorization: `Bearer ${token}` }
      });
      toast.success(res.data.message || "Senkronizasyon tamamlandı");
      fetchClaims();
    } catch (err) {
      toast.error(err.response?.data?.detail || "Senkronizasyon hatası");
    } finally {
      setSyncing(false);
    }
  };

  // Bulunulan sekmenin (statusTab) Excel çıktısı — backend openpyxl üretir
  const exportExcel = async () => {
    setExporting(true);
    try {
      const params = new URLSearchParams();
      if (statusTab && statusTab !== "all") params.append("status", statusTab);
      if (debouncedSearch) params.append("search", debouncedSearch);
      params.append("platform", platform);
      const res = await fetch(`${API}/integrations/trendyol/claims/export?${params.toString()}`, {
        headers: { Authorization: `Bearer ${localStorage.getItem("token")}` },
      });
      if (!res.ok) throw new Error("export failed");
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `${platform === "hepsiburada" ? "hepsiburada" : "trendyol"}-iadeleri-${statusTab || "tum"}.xlsx`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
      toast.success("Excel indirildi");
    } catch (e) {
      toast.error("Excel aktarımı başarısız");
    } finally {
      setExporting(false);
    }
  };

  // Gider Pusulası — MUHASEBE Excel'i (görseldeki kolon düzeni: Fatura Tarihi, Seri No,
  // Adı-Soyadı, Kdv Oranı, Tutar (VD), Vergi Hariç Tutar (Y), Kdv (Y)). KDV oranına göre
  // gruplanmış, negatif tutarlarla. Site + Trendyol + Hepsiburada TÜM pusulalar tek dosyada,
  // seçili tarih aralığında. (Sekme-başına ayrı Excel butonları KALDIRILDI — tek kaynak burası.)
  const [gpExporting, setGpExporting] = useState(false);
  const [gpFrom, setGpFrom] = useState("");
  const [gpTo, setGpTo] = useState("");
  const [prFrom, setPrFrom] = useState(""); // koçan no aralığı yazdırma — başlangıç
  const [prTo, setPrTo] = useState("");     // koçan no aralığı yazdırma — bitiş
  const [prBusy, setPrBusy] = useState(false);
  const [gpSource, setGpSource] = useState("all"); // Excel iade kaynağı filtresi
  // Excel'e YALNIZ pusulası kesilmiş (seri no almış) iadeler girsin (kullanıcı isteği).
  // Varsayılan AÇIK: "gider pusulası oluşmayan hiçbir siparişi indirmesin".
  const [gpOnlyCut, setGpOnlyCut] = useState(true);
  // Toplu pusula kesimi (tarih aralığı + kaynak, YALNIZ onaylanan iadeler) — kontrollü partiler
  const [gpBulkCount, setGpBulkCount] = useState(10);
  const [gpBulkBusy, setGpBulkBusy] = useState(false);
  const [gpBulkPreview, setGpBulkPreview] = useState(null); // dry_run sonucu
  // Hazır tarih aralıkları — "son 30 gün" gibi çekimleri tek tıkla, elle yazmadan doldurur.
  // Yerel (TR) tarihi YYYY-MM-DD üretir; type="date" bu formatı bekler.
  const _ymd = (dt) => {
    const y = dt.getFullYear();
    const m = String(dt.getMonth() + 1).padStart(2, "0");
    const d = String(dt.getDate()).padStart(2, "0");
    return `${y}-${m}-${d}`;
  };
  const applyGpPreset = (key) => {
    const now = new Date();
    let from = new Date(now), to = new Date(now);
    if (key === "today") { /* from=to=bugün */ }
    else if (key === "7") { from.setDate(now.getDate() - 6); }
    else if (key === "30") { from.setDate(now.getDate() - 29); }
    else if (key === "month") { from = new Date(now.getFullYear(), now.getMonth(), 1); }
    else if (key === "prevmonth") {
      from = new Date(now.getFullYear(), now.getMonth() - 1, 1);
      to = new Date(now.getFullYear(), now.getMonth(), 0);
    }
    setGpFrom(_ymd(from));
    setGpTo(_ymd(to));
  };
  const exportGiderPusulasi = async () => {
    setGpExporting(true);
    try {
      const params = new URLSearchParams();
      if (gpFrom) params.append("date_from", gpFrom);
      if (gpTo) params.append("date_to", gpTo);
      // İade kaynağı filtresi (kullanıcı isteği): tümü | site | trendyol | hepsiburada
      if (gpSource && gpSource !== "all") params.append("source", gpSource);
      // Yalnız pusulası kesilenler: seri no'suz iade satırları Excel'e girmez
      if (gpOnlyCut) params.append("only_with_gp", "1");
      const qs = params.toString();
      const res = await fetch(`${API}/orders/returns/gider-pusulasi/export${qs ? `?${qs}` : ""}`, {
        headers: { Authorization: `Bearer ${localStorage.getItem("token")}` },
      });
      if (!res.ok) throw new Error("export failed");
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `gider-pusulasi${gpSource && gpSource !== "all" ? `-${gpSource}` : ""}.xlsx`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
      toast.success("Gider pusulası Excel'i indirildi");
    } catch (e) {
      toast.error("Gider pusulası aktarımı başarısız");
    } finally {
      setGpExporting(false);
    }
  };

  // Toplu Gider Pusulası — YALNIZ ONAYLANAN iadeler (site + Trendyol + Hepsiburada ortak
  // havuz, iade talep tarihine göre eskiden yeniye). dry=true: kesmeden önizleme;
  // dry=false: başlangıç no'dan itibaren sıralı koçanla partiyi keser.
  const runGpBulk = async (dry) => {
    if (!gpFrom || !gpTo) { toast.error("Önce yukarıdan tarih aralığı seçin"); return; }
    if (!dry && !window.confirm(
      `${gpBulkCount} adede kadar pusula, ${pad6(gpStart)} numarasından başlayarak kesilecek. Devam?`)) return;
    setGpBulkBusy(true);
    try {
      const token = localStorage.getItem("token");
      const sources = gpSource === "all" ? ["site", "trendyol", "hepsiburada"] : [gpSource];
      const res = await axios.post(`${API}/integrations/trendyol/claims/gp-bulk-range`,
        { date_from: gpFrom, date_to: gpTo, sources, start_no: pad6(gpStart),
          limit: gpBulkCount, dry_run: !!dry },
        { headers: { Authorization: `Bearer ${token}` } });
      if (dry) {
        setGpBulkPreview(res.data);
      } else {
        const d = res.data || {};
        if (d.next_no) { setGpStart(d.next_no); localStorage.setItem("gp_next_no", d.next_no); }
        setGpBulkPreview(null);
        toast.success(`${d.kesilen || 0} pusula kesildi · hata ${d.hata || 0} · kalan aday ${d.kalan_aday || 0}`);
        if (d.hata > 0 && Array.isArray(d.hatalar) && d.hatalar.length) {
          toast.error(`İlk hata: ${d.hatalar[0].siparis || d.hatalar[0].key} — ${d.hatalar[0].hata}`);
        }
        // KESİLEN pusulaları HEMEN yazdır (aynı 4'lü A4 şablonu / matbu form) — Kadir: 'yazdırması lazımdı'.
        if (Array.isArray(d.pusulalar) && d.pusulalar.length) {
          setBulkPrintData(d.pusulalar);
          setTimeout(() => window.print(), 600);
        }
        fetchClaims();
      }
    } catch (e) {
      toast.error(e.response?.data?.detail || "Toplu pusula işlemi başarısız");
    } finally {
      setGpBulkBusy(false);
    }
  };

  const handleApprove = async (claim, explicitIds) => {
    const claimItemIds = (explicitIds && explicitIds.length)
      ? explicitIds
      : (claim.items || []).map(i => i.claim_item_id).filter(Boolean);
    if (!claimItemIds.length) { toast.error("Onaylanacak ürün seçilmedi"); return; }
    if (!await window.appConfirm(`Seçili ${claimItemIds.length} ürünün iadesini onaylamak istediğinize emin misiniz?`)) return;
    try {
      const token = localStorage.getItem("token");
      await axios.post(`${API}/integrations/trendyol/claims/${claim.claim_id}/approve`,
        { claim_item_ids: claimItemIds },
        { headers: { Authorization: `Bearer ${token}` } }
      );
      toast.success("İade onaylandı");
      fetchClaims();
    } catch (err) {
      toast.error(err.response?.data?.detail || "Onay hatası");
    }
  };

  const [statusBusyId, setStatusBusyId] = useState(null);
  // Trendyol iade durumunu MANUEL ilerlet + kilitle (sonraki senkron bu durumu ezmez)
  const changeClaimStatus = async (claimId, newStatus) => {
    setStatusBusyId(claimId);
    try {
      const token = localStorage.getItem("token");
      await axios.post(`${API}/integrations/trendyol/claims/${claimId}/set-status`,
        { status: newStatus }, { headers: { Authorization: `Bearer ${token}` } });
      toast.success("Durum güncellendi · manuel kilitlendi (senkron ezmeyecek)");
      fetchClaims();
    } catch (e) {
      toast.error(e.response?.data?.detail || "Durum güncellenemedi");
    } finally { setStatusBusyId(null); }
  };
  // Manuel kilidi kaldır → durum tekrar Trendyol senkronundan güncellenir
  const unlockClaim = async (claimId) => {
    try {
      const token = localStorage.getItem("token");
      await axios.post(`${API}/integrations/trendyol/claims/${claimId}/unlock`, {},
        { headers: { Authorization: `Bearer ${token}` } });
      toast.success("Kilit kaldırıldı · durum tekrar Trendyol'dan senkronlanacak");
      fetchClaims();
    } catch (e) { toast.error("Kilit kaldırılamadı"); }
  };

  const advanceGpNo = (count) => {
    const base = parseInt(pad6(gpStart) || "0", 10);
    const next = pad6(base + (count || 1));
    setGpStart(next);
    localStorage.setItem("gp_next_no", next);
  };

  const handleGiderPusulasi = async (claim) => {
    // claim_id (eski çağrı) yerine claim objesi bekleriz; geriye uyum:
    const claimId = typeof claim === "string" ? claim : claim.claim_id;
    const _claimObj = typeof claim === "string" ? null : claim;
    setGpLoading(true);
    try {
      const token = localStorage.getItem("token");
      const trackingNo = pad6(gpStart);
      const body = { tracking_no: trackingNo };
      // KISMİ GP: yalnız SEÇİLİ kalemler pusulaya girsin (Trendyol'da sadece M'yi ilerletince
      // pusula tümünü basıyordu). Seçili kalem index'lerini backend'e gönder (item_indexes).
      if (_claimObj && Array.isArray(_claimObj.items)) {
        const allIds = _claimObj.items.map(i => i.claim_item_id).filter(Boolean);
        const selIds = itemSel[claimId] || new Set(allIds);
        const idxs = _claimObj.items
          .map((it, idx) => (selIds.has(it.claim_item_id) ? idx : -1))
          .filter(idx => idx >= 0);
        if (idxs.length && idxs.length < _claimObj.items.length) body.item_indexes = idxs;
      }
      const res = await axios.post(`${API}/integrations/trendyol/claims/${claimId}/gider-pusulasi`,
        body,
        { headers: { Authorization: `Bearer ${token}` } }
      );
      setGpData({ ...res.data.gider_pusulasi, assigned_no: trackingNo });
      setGpModalOpen(true);
      advanceGpNo(1);
      fetchClaims();
    } catch (err) {
      toast.error(err.response?.data?.detail || "Gider pusulası oluşturulamadı");
    } finally {
      setGpLoading(false);
    }
  };

  // ── Manuel (sipariş-durumu kaynaklı) satır aksiyonları ──────────────────
  // Durum: doğrudan sipariş durumunu ilerlet (kaynak sipariştir, Trendyol claim değil).
  const changeManualStatus = async (orderId, newStatus) => {
    if (!orderId) return;
    setStatusBusyId("ord:" + orderId);
    try {
      const token = localStorage.getItem("token");
      await axios.put(`${API}/orders/${orderId}/status?status=${encodeURIComponent(newStatus)}`, {},
        { headers: { Authorization: `Bearer ${token}` } });
      toast.success("Durum güncellendi");
      fetchClaims();
    } catch (err) {
      toast.error(err.response?.data?.detail || "Durum güncellenemedi");
    } finally { setStatusBusyId(null); }
  };
  // Gider pusulası: önce siparişi customer_returns'e köprüle (idempotent), sonra GP üret.
  const handleManualGiderPusulasi = async (claim) => {
    if (!claim.order_id) { toast.error("Sipariş bulunamadı"); return; }
    setGpLoading(true);
    try {
      const token = localStorage.getItem("token");
      const trackingNo = pad6(gpStart);
      const br = await axios.post(`${API}/admin/rooftr/returns/${claim.order_id}/open`, {},
        { headers: { Authorization: `Bearer ${token}` } });
      const returnId = br.data?.return_id;
      if (!returnId) throw new Error("bridge");
      const res = await axios.post(`${API}/orders/returns/${returnId}/gider-pusulasi`,
        { tracking_no: trackingNo }, { headers: { Authorization: `Bearer ${token}` } });
      setGpData({ ...res.data.gider_pusulasi, assigned_no: trackingNo });
      setGpModalOpen(true);
      advanceGpNo(1);
      fetchClaims();
    } catch (err) {
      toast.error(err.response?.data?.detail || "Gider pusulası oluşturulamadı");
    } finally { setGpLoading(false); }
  };

  // Gider pusulası no'yu satırdaki #no'ya tıklayınca inline düzenle (TY/HB + manuel).
  // Web sitesi (RooftrReturns) ile AYNI uç: /orders/returns/vouchers/set-number.
  // Anahtar: TY/HB için claim_id, manuel/site için order_number.
  const saveGpNo = async (claim) => {
    const val = String(editGpNo?.value || "").trim();
    if (!val) { setEditGpNo(null); return; }
    try {
      const token = localStorage.getItem("token");
      const body = claim.claim_id
        ? { claim_id: claim.claim_id, display_number: val }
        : { order_number: claim.order_number, display_number: val };
      await axios.post(`${API}/orders/returns/vouchers/set-number`, body,
        { headers: { Authorization: `Bearer ${token}` } });
      toast.success("Gider pusulası numarası güncellendi");
      setEditGpNo(null);
      fetchClaims();
    } catch (e) {
      toast.error(e.response?.data?.detail || "Numara güncellenemedi");
    }
  };
  const gpRowKey = (claim) => claim.claim_id || claim.order_number || claim.order_id;
  // #no'yu tıklanabilir/inline-düzenlenebilir gösterir (web sitesiyle aynı UX).
  const renderGpNo = (claim) => {
    if (!claim.gider_pusulasi_no) return null;
    const key = gpRowKey(claim);
    if (editGpNo?.key === key) {
      return (
        <span className="inline-flex items-center gap-1">
          <input autoFocus value={editGpNo.value}
            onChange={(e) => setEditGpNo({ key, value: e.target.value })}
            onKeyDown={(e) => { if (e.key === "Enter") saveGpNo(claim); if (e.key === "Escape") setEditGpNo(null); }}
            className="w-20 text-[11px] font-mono border border-purple-300 rounded px-1 py-0.5 focus:outline-none focus:border-purple-500" />
          <button onClick={() => saveGpNo(claim)} className="text-green-600 hover:text-green-700" title="Kaydet"><Check size={14} /></button>
          <button onClick={() => setEditGpNo(null)} className="text-gray-400 hover:text-gray-600" title="Vazgeç"><X size={14} /></button>
        </span>
      );
    }
    return (
      <span onClick={() => setEditGpNo({ key, value: claim.gider_pusulasi_no })}
        className="text-[11px] font-mono font-bold text-purple-700 px-1 cursor-pointer hover:underline"
        title="Gider Pusulası No — düzenlemek için tıkla">
        #{claim.gider_pusulasi_no}
      </span>
    );
  };

  // KOÇAN NO ARALIĞI YAZDIR: kesilmiş pusulaları verilen aralıkta (087312–087608) tekrar bas
  // (Kadir: yazıcıda kağıt sıkıştı, o aralığı baştan değil tam o aralıktan yazdır).
  const printGpRange = async () => {
    const f = String(prFrom || "").trim(), t = String(prTo || "").trim();
    if (!f || !t) { toast.error("Başlangıç ve bitiş koçan no girin"); return; }
    setPrBusy(true);
    try {
      const token = localStorage.getItem("token");
      const res = await axios.get(
        `${API}/orders/returns/gider-pusulasi/print-range?from_no=${encodeURIComponent(pad6(f))}&to_no=${encodeURIComponent(pad6(t))}`,
        { headers: { Authorization: `Bearer ${token}` } });
      const list = res.data?.pusulalar || [];
      if (!list.length) { toast.error(`Bu aralıkta (${pad6(f)}–${pad6(t)}) pusula bulunamadı`); return; }
      setBulkPrintData(list);
      toast.success(`${list.length} pusula yazdırılıyor (${pad6(f)}–${pad6(t)})`);
      setTimeout(() => window.print(), 500);
    } catch (e) {
      toast.error(e.response?.data?.detail || "Aralık yazdırma hatası");
    } finally { setPrBusy(false); }
  };

  // Modaldan tek pusula yazdır: aynı 4'lü A4 mekanizmasını kullanır.
  // ÖNİZLEME (site iadesi) ise: numara YAZDIR anında atanır + kalıcılaşır (Kadir: 'numara
  // yazdırınca atansın'). TY/manuel akışta numara zaten açılışta atandığından finalize yok.
  const printSingleGp = async () => {
    if (!gpData) return;
    let finalGp = gpData;
    if (gpData.preview && gpData._returnId) {
      try {
        const token = localStorage.getItem("token");
        const res = await axios.post(`${API}/orders/returns/${gpData._returnId}/gider-pusulasi`,
          gpData._gpBody || {}, { headers: { Authorization: `Bearer ${token}` } });
        finalGp = { ...res.data.gider_pusulasi, assigned_no: gpData.assigned_no || res.data.gider_pusulasi?.display_number };
        setGpData(finalGp);
        advanceGpNo(1);
        toast.success(`Gider pusulası atandı: ${finalGp.display_number}`);
      } catch (e) {
        toast.error(e.response?.data?.detail || "Numara atanamadı — yazdırma iptal");
        return;
      }
    }
    setBulkPrintData([finalGp]);
    setTimeout(() => { window.print(); }, 300);
  };

  const handleBulkPrint = async () => {
    if (selectedIds.size === 0) { toast.error("Yazdırılacak satır seçin"); return; }
    setGpLoading(true);
    try {
      const token = localStorage.getItem("token");
      const startNo = pad6(gpStart);
      const res = await axios.post(`${API}/integrations/trendyol/claims/bulk-gider-pusulasi`,
        { claim_ids: [...selectedIds], start_no: startNo },
        { headers: { Authorization: `Bearer ${token}` } }
      );
      const list = res.data.gider_pusulalari || [];
      const base = parseInt(startNo || "0", 10);
      const withNo = list.map((gp, i) => ({ ...gp, assigned_no: pad6(base + i) }));
      setBulkPrintData(withNo);
      const nextNo = res.data.next_no || pad6(base + withNo.length);
      setGpStart(nextNo); localStorage.setItem("gp_next_no", nextNo);
      setTimeout(() => window.print(), 500);
      fetchClaims();
    } catch (err) {
      toast.error("Toplu yazdırma hatası");
    } finally {
      setGpLoading(false);
    }
  };

  const toggleSelect = (id) => {
    setSelectedIds(prev => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });
  };

  const toggleSelectAll = () => {
    // e-Fatura siparişler seçilemez — gider pusulası düzenlenemez (katı kural).
    const selectable = claims.filter(c => !c.manual && !c.is_efatura);
    if (selectable.length > 0 && selectedIds.size === selectable.length) {
      setSelectedIds(new Set());
    } else {
      setSelectedIds(new Set(selectable.map(c => c.claim_id)));
    }
  };

  const totalPages = Math.ceil(total / limit);

  return (
    <div data-testid="admin-returns">
      {/* Gider Pusulası Yazdırma Katmanı: A4 dikey, sayfa başına 4 pusula */}
      {bulkPrintData && (
        <GpPrintLayer slips={bulkPrintData} overlay={gpOverlay} offX={gpOffX} offY={gpOffY} guides={gpGuides} />
      )}

      <div className="print:hidden">
        {/* Header */}
        <div className="flex items-center justify-between mb-6">
          <div>
            <h1 className="text-2xl font-bold text-gray-900">İadeler</h1>
            <p className="text-xs text-gray-500 mt-1">Her 5 dakikada otomatik güncellenir</p>
          </div>
          <div className="flex items-center gap-3">
            {selectedIds.size > 0 && (
              <button onClick={handleBulkPrint} disabled={gpLoading}
                data-testid="bulk-print-btn"
                className="flex items-center gap-2 px-4 py-2 bg-purple-600 text-white rounded-lg text-sm font-bold hover:bg-purple-700 transition-colors disabled:opacity-50">
                <Printer size={16} />
                Pusula Kes + Yazdır ({selectedIds.size})
              </button>
            )}
            {/* TEK Excel: tüm gider pusulaları (site + Trendyol + Hepsiburada), seçili tarih
                aralığında, muhasebe formatında (KDV oranına göre gruplu). Sekme-başına ayrı
                Excel butonları KALDIRILDI. */}
            <div className="flex items-end gap-2 flex-wrap">
              <div className="flex flex-col">
                <label className="text-[10px] text-gray-500 uppercase tracking-wider mb-0.5" title="İade ONAY tarihi (muhasebe/admin iadeyi onayladığı tarih) — toplu gider pusulası bu tarihe göre sıralanır">İade Onay Baş.</label>
                <input type="date" value={gpFrom} onChange={(e) => setGpFrom(e.target.value)}
                  className="border border-gray-300 rounded-lg px-2 py-1.5 text-sm" />
              </div>
              <div className="flex flex-col">
                <label className="text-[10px] text-gray-500 uppercase tracking-wider mb-0.5" title="İade ONAY tarihi (muhasebe/admin iadeyi onayladığı tarih) — toplu gider pusulası bu tarihe göre sıralanır">İade Onay Bit.</label>
                <input type="date" value={gpTo} onChange={(e) => setGpTo(e.target.value)}
                  className="border border-gray-300 rounded-lg px-2 py-1.5 text-sm" />
              </div>
              <div className="flex flex-col">
                <label className="text-[10px] text-gray-500 uppercase tracking-wider mb-0.5">İade Kaynağı</label>
                <select value={gpSource} onChange={(e) => setGpSource(e.target.value)}
                  data-testid="gp-source-filter"
                  className="border border-gray-300 rounded-lg px-2 py-1.5 text-sm bg-white">
                  <option value="all">Tümü</option>
                  <option value="site">Web Sitesi</option>
                  <option value="trendyol">Trendyol</option>
                  <option value="hepsiburada">Hepsiburada</option>
                </select>
              </div>
              <label className="flex items-center gap-1.5 text-xs text-gray-600 cursor-pointer pb-2"
                title="Açıkken Excel'e YALNIZ gider pusulası kesilmiş (seri no almış) iadeler girer; pusulası olmayan hiçbir satır inmez">
                <input type="checkbox" checked={gpOnlyCut}
                  onChange={(e) => setGpOnlyCut(e.target.checked)}
                  data-testid="gp-only-cut" />
                Yalnız pusulası kesilenler
              </label>
              <button onClick={exportGiderPusulasi} disabled={gpExporting}
                data-testid="export-gider-pusulasi-btn"
                title="Gider pusulalarını (seçili tarih aralığı + iade kaynağına göre) muhasebe formatında Excel indir"
                className="flex items-center gap-2 px-4 py-2 bg-gray-900 text-white rounded-lg text-sm font-bold hover:bg-black transition-colors disabled:opacity-50">
                <Download size={16} />
                {gpExporting ? "Hazırlanıyor..." : "Gider Pusulası Excel"}
              </button>
              {/* Hazır aralık: elle tarih yazmadan tek tık. "Son 30 gün" İADE ONAY tarihine göre. */}
              <div className="flex items-center gap-1 flex-wrap">
                {[["today", "Bugün"], ["7", "Son 7 gün"], ["30", "Son 30 gün"], ["month", "Bu ay"], ["prevmonth", "Geçen ay"]].map(([k, lbl]) => (
                  <button key={k} type="button" onClick={() => applyGpPreset(k)}
                    className="px-2.5 py-1.5 border border-gray-300 rounded-lg text-xs text-gray-600 hover:bg-gray-100 hover:text-black transition-colors">
                    {lbl}
                  </button>
                ))}
              </div>
            </div>
            {/* Manuel "Güncelle" kaldırıldı — iadeler 5 dk'da bir otomatik güncelleniyor. */}
          </div>
        </div>

        {/* Gider Pusulası Ayarları — ORTAK alan: Web Sitesi + Trendyol aynı no serisini paylaşır */}
        <div className="flex flex-wrap items-end gap-4 mb-4 p-3 bg-purple-50 border border-purple-200 rounded-xl">
          <div>
            <label className="block text-[11px] font-bold text-purple-800 uppercase mb-1">Gider Pusulası Başlangıç No</label>
            <input value={gpStart}
              onChange={(e) => setGpStart(e.target.value)}
              onBlur={(e) => { const v = pad6(e.target.value); setGpStart(v); localStorage.setItem("gp_next_no", v); }}
              data-testid="gp-start-no"
              className="w-32 px-3 py-1.5 border border-purple-300 rounded-lg text-sm font-mono"
              placeholder="085490" />
          </div>
          <label className="flex items-center gap-2 text-sm text-purple-800 cursor-pointer pb-1.5">
            <input type="checkbox" checked={gpOverlay}
              onChange={(e) => { setGpOverlay(e.target.checked); localStorage.setItem("gp_overlay", e.target.checked ? "1" : "0"); }} />
            Matbu forma bindir (yalnız veri)
          </label>
          {gpOverlay && (
            <>
              <div>
                <label className="block text-[11px] font-bold text-purple-800 uppercase mb-1">Yatay (mm)</label>
                <input type="number" step="0.5" value={gpOffX}
                  onChange={(e) => { const v = parseFloat(e.target.value) || 0; setGpOffX(v); localStorage.setItem("gp_off_x", v); }}
                  className="w-20 px-2 py-1.5 border border-purple-300 rounded-lg text-sm" />
              </div>
              <div>
                <label className="block text-[11px] font-bold text-purple-800 uppercase mb-1">Dikey (mm)</label>
                <input type="number" step="0.5" value={gpOffY}
                  onChange={(e) => { const v = parseFloat(e.target.value) || 0; setGpOffY(v); localStorage.setItem("gp_off_y", v); }}
                  className="w-20 px-2 py-1.5 border border-purple-300 rounded-lg text-sm" />
              </div>
              <label className="flex items-center gap-2 text-sm text-purple-800 cursor-pointer pb-1.5">
                <input type="checkbox" checked={gpGuides} onChange={(e) => setGpGuides(e.target.checked)} />
                Hizalama çerçevesi
              </label>
            </>
          )}
          <p className="text-[11px] text-purple-600 ml-auto max-w-xs pb-1">
            A4 yatay, aynı pusula 4 kopya. Numara kağıda basılmaz (matbuda var); takip için satırda/önizlemede görünür. Her iade çıktısı no'yu 1 ilerletir.
          </p>
        </div>

        {/* KOÇAN NO ARALIĞI YAZDIR — kesilmiş pusulaları verilen aralıkta TEKRAR bas
            (yazıcı sıkışması vb. → baştan değil, tam istenen aralıktan yazdır). */}
        <div className="mb-4 p-3 bg-sky-50 border border-sky-200 rounded-xl">
          <div className="flex flex-wrap items-end gap-3">
            <div>
              <p className="text-[11px] font-bold text-sky-800 uppercase mb-1">Koçan No Aralığı Yazdır</p>
              <p className="text-[11px] text-sky-700 max-w-md">
                Kesilmiş gider pusulalarını verilen <b>koçan no aralığında</b> tekrar yazdırır
                (yeni pusula oluşturmaz). Kağıt sıkışması vb. durumda kaldığınız yerden devam edin.
              </p>
            </div>
            <div className="flex flex-col">
              <label className="text-[10px] text-sky-700 uppercase tracking-wider mb-0.5">Başlangıç No</label>
              <input value={prFrom} onChange={(e) => setPrFrom(e.target.value)} placeholder="087312"
                className="border border-sky-300 rounded-lg px-2 py-1.5 text-sm bg-white w-28 font-mono" />
            </div>
            <div className="flex flex-col">
              <label className="text-[10px] text-sky-700 uppercase tracking-wider mb-0.5">Bitiş No</label>
              <input value={prTo} onChange={(e) => setPrTo(e.target.value)} placeholder="087608"
                className="border border-sky-300 rounded-lg px-2 py-1.5 text-sm bg-white w-28 font-mono" />
            </div>
            <button onClick={printGpRange} disabled={prBusy}
              className="px-4 py-2 bg-sky-600 text-white rounded-lg text-sm font-bold hover:bg-sky-700 transition-colors disabled:opacity-50">
              {prBusy ? "..." : "Aralığı Yazdır"}
            </button>
          </div>
        </div>

        {/* Toplu Gider Pusulası — YALNIZ ONAYLANAN iadeler. Yukarıdaki tarih aralığı +
            iade kaynağı filtresini kullanır; başlangıç no yukarıdaki ortak alandan gelir.
            Önizle (kesmeden aday listesi) → Oluştur (kontrollü parti). */}
        <div className="mb-4 p-3 bg-emerald-50 border border-emerald-200 rounded-xl">
          <div className="flex flex-wrap items-end gap-3">
            <div>
              <p className="text-[11px] font-bold text-emerald-800 uppercase mb-1">Toplu Gider Pusulası — Onaylanan İadeler</p>
              <p className="text-[11px] text-emerald-700 max-w-md">
                Yukarıda seçili tarih aralığı ({gpFrom || "—"} → {gpTo || "—"}) ve kaynak
                ({gpSource === "all" ? "site + Trendyol + Hepsiburada" : gpSource}) içindeki
                <b> yalnız iadesi ONAYLANMIŞ ve pusulası henüz olmayan</b> iadeler, İADE ONAY
                tarihine göre sıralanır; {pad6(gpStart)} numarasından itibaren sıralı koçanla kesilir.
              </p>
            </div>
            <div className="flex flex-col">
              <label className="text-[10px] text-emerald-700 uppercase tracking-wider mb-0.5">Parti Adedi</label>
              <select value={gpBulkCount} onChange={(e) => setGpBulkCount(parseInt(e.target.value, 10))}
                data-testid="gp-bulk-count"
                className="border border-emerald-300 rounded-lg px-2 py-1.5 text-sm bg-white">
                {[10, 20, 50, 100, 200, 500, 1000, 2000].map(n => <option key={n} value={n}>{n}</option>)}
              </select>
            </div>
            <button onClick={() => runGpBulk(true)} disabled={gpBulkBusy}
              data-testid="gp-bulk-preview-btn"
              className="px-4 py-2 border border-emerald-400 text-emerald-800 rounded-lg text-sm font-bold hover:bg-emerald-100 transition-colors disabled:opacity-50">
              {gpBulkBusy ? "..." : "Önizle"}
            </button>
            <button onClick={() => runGpBulk(false)} disabled={gpBulkBusy || !gpBulkPreview}
              data-testid="gp-bulk-run-btn"
              title={gpBulkPreview ? "Önizlemedeki partiyi kes" : "Önce Önizle ile aday listesini kontrol edin"}
              className="px-4 py-2 bg-emerald-600 text-white rounded-lg text-sm font-bold hover:bg-emerald-700 transition-colors disabled:opacity-50">
              {gpBulkBusy ? "Kesiliyor..." : `Pusula Kes (${Math.min(gpBulkCount, gpBulkPreview?.bu_partide ?? gpBulkCount)})`}
            </button>
            {gpBulkPreview && (
              <button onClick={() => setGpBulkPreview(null)}
                className="px-3 py-2 text-xs text-emerald-700 hover:text-emerald-900">Önizlemeyi kapat</button>
            )}
          </div>
          {gpBulkPreview && (
            <div className="mt-3 border-t border-emerald-200 pt-2">
              <p className="text-xs font-semibold text-emerald-800 mb-1.5">
                Toplam aday: {gpBulkPreview.toplam_aday} · Bu partide kesilecek: {gpBulkPreview.bu_partide}
                {gpBulkPreview.toplam_aday > gpBulkPreview.bu_partide &&
                  ` (kalan ${gpBulkPreview.toplam_aday - gpBulkPreview.bu_partide} sonraki partilerde)`}
              </p>
              {gpBulkPreview.kaynak_dagilim && (
                <p className="text-[11px] text-gray-600 mb-1.5">
                  Kaynak dağılımı — Site: <b>{gpBulkPreview.kaynak_dagilim.site}</b> · Trendyol: <b>{gpBulkPreview.kaynak_dagilim.trendyol}</b> · Hepsiburada: <b>{gpBulkPreview.kaynak_dagilim.hepsiburada}</b>
                </p>
              )}
              <div className="max-h-56 overflow-y-auto">
                <table className="w-full text-xs">
                  <thead>
                    <tr className="text-left text-emerald-700">
                      <th className="py-1 pr-2">#</th>
                      <th className="py-1 pr-2">Kaynak</th>
                      <th className="py-1 pr-2">Sipariş</th>
                      <th className="py-1 pr-2">Müşteri</th>
                      <th className="py-1 pr-2">İade Tarihi</th>
                      <th className="py-1 pr-2 text-right">Tutar</th>
                      <th className="py-1">Alacağı No</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(gpBulkPreview.adaylar || []).map((a, i) => (
                      <tr key={a.key} className="border-t border-emerald-100">
                        <td className="py-1 pr-2 text-gray-500">{i + 1}</td>
                        <td className="py-1 pr-2 capitalize">{a.kaynak}</td>
                        <td className="py-1 pr-2 font-mono">{a.siparis}</td>
                        <td className="py-1 pr-2">{a.musteri || "—"}</td>
                        <td className="py-1 pr-2">{a.tarih}</td>
                        <td className="py-1 pr-2 text-right">{Number(a.tutar || 0).toLocaleString("tr-TR", { minimumFractionDigits: 2 })} ₺</td>
                        <td className="py-1 font-mono">{pad6(parseInt(pad6(gpStart) || "0", 10) + i)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </div>


        {/* Platform barı — en başta Web Sitesi, sonra pazaryerleri */}
        <div className="flex items-center gap-1 overflow-x-auto border-b mb-4 pb-px">
          {PLATFORMS.map((pf) => (
            <button key={pf.key}
              onClick={() => { setPlatform(pf.key); setStatusTab("all"); setPage(1); setSelectedIds(new Set()); }}
              className={`flex items-center gap-2 whitespace-nowrap px-3 py-2 text-sm border-b-2 -mb-px transition-colors ${platform === pf.key ? "border-orange-500 text-orange-600 font-semibold" : "border-transparent text-gray-500 hover:text-gray-800"}`}>
              <span className={`inline-flex items-center justify-center w-5 h-5 rounded-full text-[10px] font-bold text-white ${pf.color}`}>{pf.badge}</span>
              {pf.label}
            </button>
          ))}
        </div>

        {/* Web Sitesi sekmesi: doğrudan sipariş bazlı iade akışı (alt-sekme yok — üstte zaten "Web Sitesi" yazıyor) */}
        {platform === "facette" && <RooftrReturns embedded gpStart={gpStart}
          onGiderCreated={(gp) => { setGpData(gp); setGpModalOpen(true); /* önizleme: numara YAZDIR'da atanır, burada advance YOK */ }}
          onBulkGider={(gps, nextNo) => { setBulkPrintData(gps); const nn = pad6(nextNo); setGpStart(nn); localStorage.setItem("gp_next_no", nn); setTimeout(() => window.print(), 500); }} />}

        {/* Trendyol (pazaryeri) sekmesi gövdesi */}
        {(platform === "trendyol" || platform === "hepsiburada") && (<>
        {/* Durum sekmeleri */}
        <div className="flex items-center gap-2 overflow-x-auto mb-4">
          {STATUS_TABS.map((t) => (
            <button key={t.key}
              onClick={() => { setStatusTab(t.key); setPage(1); }}
              className={`whitespace-nowrap px-3 py-1.5 rounded-full text-sm border transition-colors ${statusTab === t.key ? "bg-gray-900 text-white border-gray-900" : "bg-white text-gray-600 border-gray-200 hover:border-gray-400"}`}>
              {t.label}
              {/* Rozet yalnız ürün (kalem) adedi — parantezli '(N talep)' gösterimi kullanıcı
                  isteğiyle kaldırıldı (kafa karıştırıyordu). */}
              <span className={`ml-1.5 text-xs ${statusTab === t.key ? "text-gray-300" : "text-gray-400"}`}>
                {tabCounts[t.key] ?? 0}
              </span>
            </button>
          ))}
        </div>


        {/* Stats */}
        <div className="grid grid-cols-2 gap-4 mb-6">
          <div className="bg-white p-4 rounded-xl border shadow-sm">
            <p className="text-xs text-gray-500 font-bold uppercase">Toplam İade</p>
            <p className="text-2xl font-bold text-red-600 mt-1">{stats.total_returns || 0}</p>
          </div>
          <div className="bg-white p-4 rounded-xl border shadow-sm">
            <p className="text-xs text-gray-500 font-bold uppercase">Toplam İade Tutarı</p>
            <p className="text-2xl font-bold text-gray-900 mt-1">{formatCurrency(stats.total_refund)}</p>
          </div>
        </div>

        {/* Filters */}
        <div className="flex items-center gap-3 mb-4">
          <div className="relative flex-1 max-w-xs">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" size={14} />
            <input type="text" placeholder="Sipariş no, müşteri adı..."
              value={search} onChange={(e) => { setSearch(e.target.value); setPage(1); }}
              className="w-full pl-9 pr-4 py-2 bg-white border rounded-lg text-sm" />
          </div>
        </div>

        {/* Table */}
        <div className="bg-white rounded-xl border shadow-sm overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-gray-50 border-b">
              <tr>
                <th className="px-3 py-3 w-10">
                  <input type="checkbox" checked={claims.filter(c => !c.manual && !c.is_efatura).length > 0 && selectedIds.size === claims.filter(c => !c.manual && !c.is_efatura).length}
                    onChange={toggleSelectAll} className="rounded" data-testid="select-all-checkbox" />
                </th>
                <th className="text-left px-3 py-3 text-xs font-bold text-gray-500 uppercase">Sipariş No</th>
                <th className="text-left px-3 py-3 text-xs font-bold text-gray-500 uppercase">Müşteri</th>
                <th className="text-left px-3 py-3 text-xs font-bold text-gray-500 uppercase">Sebep</th>
                <th className="text-left px-3 py-3 text-xs font-bold text-gray-500 uppercase">Ödeme</th>
                <th className="text-left px-3 py-3 text-xs font-bold text-gray-500 uppercase">Kargo</th>
                <th className="text-right px-3 py-3 text-xs font-bold text-gray-500 uppercase">Tutar<span className="block text-[9px] font-normal normal-case text-gray-400">Brüt / İskonto / Net</span></th>
                <th className="text-left px-3 py-3 text-xs font-bold text-gray-500 uppercase">İade Talep Tarihi<span className="block text-[9px] font-normal normal-case text-gray-400">müşterinin iadeyi açtığı tarih</span></th>
                <th className="text-center px-3 py-3 text-xs font-bold text-gray-500 uppercase">Durum</th>
                <th className="text-right px-3 py-3 text-xs font-bold text-gray-500 uppercase">İşlemler</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {loading ? (
                <tr><td colSpan={10} className="text-center py-12 text-gray-400">Yükleniyor...</td></tr>
              ) : claims.length === 0 ? (
                <tr><td colSpan={10} className="text-center py-12 text-gray-400">İade kaydı bulunamadı</td></tr>
              ) : claims.map(claim => {
                // ADET ile çarp: aynı üründen 2 adet iade edildiyse tutar 2× görünmeli (yoksa
                // yarısı — 11361499309'daki "1207.5 yerine 2415 olmalı" hatası buydu).
                const totalGross = (claim.items || []).reduce((s, i) => s + (i.unit_price || 0) * (i.quantity || 1), 0);
                const totalDiscount = (claim.items || []).reduce((s, i) => s + (i.discount_amount || 0) * (i.quantity || 1), 0);
                const _itemsNet = (claim.items || []).reduce((s, i) => s + (i.price || 0) * (i.quantity || 1), 0);
                // İade tutarı: yetkili refund_amount öncelikli (Trendyol/sipariş toplamıyla birebir),
                // yoksa kalemlerden (adet dahil) hesaplanır.
                const totalNet = (claim.refund_amount != null && claim.refund_amount !== "")
                  ? Number(claim.refund_amount) : _itemsNet;
                const isExpanded = expandedId === claim.claim_id;
                // ONAYLANDI mı? (panel butonu VEYA claim durumu VEYA gider pusulası/onay damgası).
                // Böylece dropdown'dan/senkrondan "Accepted" olan iade de ONAYLI sayılır → "Bekliyor"
                // ile karışmaz ve yeşil "İade Onayla" butonu tekrar bastırmaz.
                const isApproved = claim.panel_action === "approved"
                  || claim.claim_status === "Accepted"
                  || !!claim.return_approved_at
                  || !!claim.has_gider_pusulasi;
                const isRejected = claim.panel_action === "issued"
                  || ["Rejected", "Cancelled"].includes(claim.claim_status);
                const badgeAction = isApproved ? "approved" : (isRejected ? "issued" : (claim.panel_action || "pending"));
                const isActioned = !!claim.panel_action || isApproved || isRejected;

                return (
                  <tr key={claim.claim_id} className={`${selectedIds.has(claim.claim_id) ? "bg-blue-50" : "hover:bg-gray-50"} transition-colors`}>
                    <td className="px-3 py-3">
                      {!claim.manual && !claim.is_efatura && (
                        <input type="checkbox" checked={selectedIds.has(claim.claim_id)}
                          onChange={() => toggleSelect(claim.claim_id)} className="rounded"
                          data-testid={`select-claim-${claim.claim_id}`} />
                      )}
                    </td>
                    <td className="px-3 py-3">
                      <button onClick={() => setExpandedId(isExpanded ? null : claim.claim_id)}
                        className="flex items-center gap-1 font-mono text-sm font-bold text-blue-600 hover:text-blue-800">
                        {claim.order_number}
                        {isExpanded ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
                      </button>
                      {claim.is_efatura && (
                        <div className="mt-1 inline-flex items-center gap-1 px-2 py-0.5 rounded bg-red-600 text-white text-[10px] font-bold border border-red-700 animate-pulse"
                             title="Bu sipariş e-Fatura / kurumsal siparişidir. Gider pusulası düzenlenemez — müşteriden iade faturası alınmalıdır.">
                          ⚠️ BU SİPARİŞ E-FATURADIR
                        </div>
                      )}
                      {claim.merged_count > 1 && (
                        <div className="mt-1 inline-flex items-center gap-1 px-1.5 py-0.5 rounded bg-amber-100 text-amber-700 text-[10px] font-semibold border border-amber-200"
                             title={`Bu sipariş Hepsiburada'da ${claim.merged_count} ayrı kalem-claim olarak açılmış; tek iade olarak birleştirildi. İşlemler (onay/gider pusulası) hepsine birden uygulanır.`}>
                          🔗 {claim.merged_count} kalem birleşik
                        </div>
                      )}
                      {Array.isArray(claim.staff_notes) && claim.staff_notes.length > 0 && (
                        <div className="mt-1 inline-flex items-center gap-1 px-1.5 py-0.5 rounded bg-indigo-100 text-indigo-700 text-[10px] font-semibold border border-indigo-200 cursor-help"
                             title={claim.staff_notes.map((n) => `• ${n.text}${n.by ? `  — ${n.by}` : ""}`).join("\n")}>
                          📝 Personel Notu{claim.staff_notes.length > 1 ? ` (${claim.staff_notes.length})` : ""}
                        </div>
                      )}
                      {isExpanded && (
                        <div className="mt-2 p-3 bg-gray-50 rounded-lg text-xs space-y-1">
                          <p className="mb-1">
                            {claim.manual
                              ? <span><strong>Manuel iade</strong> · sipariş durumundan</span>
                              : <span><strong>Claim ID:</strong> {claim.claim_id}</span>}
                            {claim.invoice_number ? ` · Fatura: ${claim.invoice_number}` : ""}
                          </p>
                          {(() => {
                            const allIds = (claim.items || []).map(i => i.claim_item_id).filter(Boolean);
                            const selIds = itemSel[claim.claim_id] || new Set(allIds);
                            // Onaylanmış iadede kalemler KİLİTLİ (yalnız muhasebe/admin "Düzenle" ile açar).
                            const locked = isApproved && !editRows[claim.claim_id];
                            return (
                              <>
                                <div className="rounded-lg border bg-white divide-y">
                                  {(claim.items || []).map((item, ii) => (
                                    <label key={ii} className={`flex items-center gap-2 px-2 py-1.5 ${locked ? "cursor-default" : "cursor-pointer hover:bg-gray-50"}`}>
                                      {!claim.manual && (
                                        <input type="checkbox" className="rounded" disabled={locked}
                                          checked={selIds.has(item.claim_item_id)}
                                          onChange={() => !locked && toggleItem(claim.claim_id, item.claim_item_id, allIds)} />
                                      )}
                                      {isApproved && selIds.has(item.claim_item_id) && (
                                        <span className="text-green-600 text-[10px] font-bold" title="Bu kalem onaylandı">✓</span>
                                      )}
                                      <span className="flex-1">
                                        <span className="font-medium">{item.productName || "-"}</span>
                                        {item.size ? <span className="ml-1.5 text-gray-600 font-semibold whitespace-nowrap">{item.size}</span> : null}
                                        {(item.quantity || 1) > 1 ? <span className="ml-1.5 px-1.5 py-0.5 rounded bg-blue-100 text-blue-700 text-[10px] font-bold">× {item.quantity} adet</span> : null}
                                        {item.barcode ? <span className="ml-2 font-mono text-[10px] text-gray-500">{item.barcode}</span> : null}
                                        {item.reason ? <span className="ml-2 text-[10px] text-gray-400">({item.reason})</span> : null}
                                      </span>
                                      <span className="font-mono">
                                        {(item.quantity || 1) > 1 && <span className="text-[10px] text-gray-400 mr-1">{formatCurrency(item.price)}×{item.quantity}=</span>}
                                        {formatCurrency((item.price || 0) * (item.quantity || 1))}
                                      </span>
                                    </label>
                                  ))}
                                </div>
                                {!claim.manual && (isApproved ? (
                                  <div className="mt-2 flex flex-wrap items-center gap-2">
                                    <span
                                      title="Bu iade zaten onaylanmış — tekrar onaylanamaz"
                                      className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-green-50 text-green-600 border border-green-200 rounded-lg text-xs font-bold cursor-not-allowed opacity-90 select-none">
                                      <Check size={13} /> İade Onaylandı
                                    </span>
                                    <span className="text-[11px] text-gray-500">
                                      Onaylanan: {selIds.size}/{(claim.items || []).length} kalem
                                      {claim.return_approved_at ? ` · ${new Date(claim.return_approved_at).toLocaleDateString("tr-TR")}` : ""}
                                    </span>
                                    {canEditApproval() ? (
                                      editRows[claim.claim_id] ? (
                                        <button onClick={() => setEditRows(s => { const n = { ...s }; delete n[claim.claim_id]; return n; })}
                                          className="px-3 py-1 rounded-lg bg-gray-800 text-white text-xs font-bold hover:bg-black">Kilitle</button>
                                      ) : (
                                        <button onClick={() => setEditRows(s => ({ ...s, [claim.claim_id]: true }))}
                                          className="px-3 py-1 rounded-lg bg-white text-gray-700 border border-gray-300 text-xs font-bold hover:bg-gray-100">✏️ Düzenle</button>
                                      )
                                    ) : (
                                      <span className="text-[10px] text-gray-400 italic" title="Onay geçmişini değiştirme yetkisi yalnız muhasebe ve admin kullanıcılarındadır.">🔒 muhasebe/admin</span>
                                    )}
                                  </div>
                                ) : !isActioned && (
                                  <button onClick={() => handleApprove(claim, Array.from(selIds))}
                                    data-testid={`approve-items-${claim.claim_id}`}
                                    disabled={selIds.size === 0}
                                    className="mt-2 inline-flex items-center gap-1.5 px-3 py-1.5 bg-green-600 text-white rounded-lg text-xs font-bold hover:bg-green-700 disabled:opacity-50">
                                    <Check size={13} /> Seçili {selIds.size} ürünü İade Onayla
                                  </button>
                                ))}
                              </>
                            );
                          })()}
                        </div>
                      )}
                    </td>
                    <td className="px-3 py-3 text-gray-700">{claim.customer_name || "-"}</td>
                    <td className="px-3 py-3 text-xs text-gray-600 max-w-[140px] truncate">{claim.claim_reason || "-"}</td>
                    <td className="px-3 py-3 text-xs">
                      <span className={`px-2 py-0.5 rounded text-[10px] font-bold ${
                        (claim.payment_type || 'credit_card') === 'credit_card' ? 'bg-blue-100 text-blue-700' :
                        claim.payment_type === 'transfer' ? 'bg-purple-100 text-purple-700' : 'bg-gray-100 text-gray-700'
                      }`}>
                        {claim.payment_type === 'transfer' ? 'Havale' : claim.payment_type === 'cod' ? 'Kapıda' : 'Kredi Kartı'}
                      </span>
                    </td>
                    <td className="px-3 py-3 text-xs">
                      {claim.cargo_tracking_number ? (
                        <div className="flex flex-col">
                          <span className="font-medium text-orange-600 text-[11px]">{claim.cargo_provider_name || 'Trendyol'}</span>
                          <span className="font-mono text-[10px] text-gray-500 truncate max-w-[110px]" title={claim.cargo_tracking_number}>{claim.cargo_tracking_number}</span>
                        </div>
                      ) : (
                        <span className="text-gray-400">—</span>
                      )}
                    </td>
                    <td className="px-3 py-3 text-right font-mono whitespace-nowrap">
                      {totalDiscount > 0 && (
                        <div className="text-gray-500 line-through text-xs leading-tight">{formatCurrency(totalGross)}</div>
                      )}
                      {totalDiscount > 0 && (
                        <div className="text-orange-600 text-xs font-bold leading-tight">-{formatCurrency(totalDiscount)}</div>
                      )}
                      <div className="font-bold text-gray-900 leading-tight">{formatCurrency(totalNet)}</div>
                    </td>
                    <td className="px-3 py-3 text-xs text-gray-500">{formatDate(claim.created_date)}</td>
                    <td className="px-3 py-3 text-center">
                      <div className="flex flex-col items-center gap-1">
                        {claim.manual ? (
                          <select
                            value={claim.order_status || ""}
                            onChange={(e) => changeManualStatus(claim.order_id, e.target.value)}
                            disabled={statusBusyId === ("ord:" + claim.order_id)}
                            title="Sipariş durumunu ilerlet (kaynak: sipariş)"
                            className="text-[11px] border border-gray-200 rounded-md px-1.5 py-0.5 text-gray-700 focus:outline-none focus:ring-1 focus:ring-gray-300"
                          >
                            {ORDER_RETURN_STATUS_OPTS.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
                          </select>
                        ) : (<>
                        <ActionBadge action={badgeAction} />
                        <select
                          value={claim.claim_status || ""}
                          onChange={(e) => changeClaimStatus(claim.claim_id, e.target.value)}
                          disabled={statusBusyId === claim.claim_id}
                          title={claim.manual_locked ? "Manuel kilitli — Trendyol senkronu bu durumu ezmez" : "Durumu manuel ilerlet (sonrasında senkron ezmez)"}
                          className={`text-[11px] border rounded-md px-1.5 py-0.5 focus:outline-none focus:ring-1 focus:ring-gray-300 ${claim.manual_locked ? "border-amber-400 bg-amber-50 text-amber-800 font-semibold" : "border-gray-200 text-gray-600"}`}
                        >
                          {claim.claim_status && !CLAIM_STATUS_OPTS.some(o => o.value === claim.claim_status) && (
                            <option value={claim.claim_status}>{claim.bucket_label || claim.claim_status}</option>
                          )}
                          {CLAIM_STATUS_OPTS.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
                        </select>
                        {claim.manual_locked && (
                          <button onClick={() => unlockClaim(claim.claim_id)}
                            title="Manuel kilidi kaldır — durum tekrar Trendyol'dan senkronlanır"
                            className="text-[10px] text-amber-700 hover:underline">🔒 kilidi aç</button>
                        )}
                        </>)}
                      </div>
                    </td>
                    <td className="px-3 py-3">
                      <div className="flex items-center justify-end gap-1">
                        {claim.is_efatura ? (
                          <span
                            title="Bu sipariş e-Fatura / kurumsal siparişidir — gider pusulası düzenlenemez. Müşteriden iade faturası alınmalıdır."
                            className="inline-flex items-center gap-1 px-2 py-1.5 rounded-lg bg-red-50 text-red-600 border border-red-200 text-[10px] font-bold cursor-not-allowed select-none">
                            <FileText size={13} /> e-Fatura
                          </span>
                        ) : claim.manual ? (
                          <>
                            {renderGpNo(claim)}
                            <button onClick={() => handleManualGiderPusulasi(claim)}
                              disabled={gpLoading}
                              className={`p-1.5 rounded-lg transition-colors ${
                                claim.has_gider_pusulasi
                                  ? "bg-purple-100 text-purple-700 hover:bg-purple-200"
                                  : "bg-gray-100 text-gray-600 hover:bg-gray-200"
                              }`} title="Gider Pusulası">
                              <FileText size={14} />
                            </button>
                          </>
                        ) : (<>
                        {renderGpNo(claim)}
                        <button onClick={() => handleGiderPusulasi(claim)}
                          disabled={gpLoading}
                          data-testid={`gp-${claim.claim_id}`}
                          className={`p-1.5 rounded-lg transition-colors ${
                            claim.has_gider_pusulasi
                              ? "bg-purple-100 text-purple-700 hover:bg-purple-200"
                              : "bg-gray-100 text-gray-600 hover:bg-gray-200"
                          }`} title="Gider Pusulası">
                          <FileText size={14} />
                        </button>
                        </>)}
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>

        {/* Pagination */}
        {totalPages > 1 && (
          <div className="flex items-center justify-between mt-4">
            <p className="text-sm text-gray-500">Toplam {total} kayıt, Sayfa {page}/{totalPages}</p>
            <div className="flex gap-2">
              <button disabled={page <= 1} onClick={() => setPage(p => p - 1)}
                className="px-3 py-1 border rounded text-sm disabled:opacity-50">Önceki</button>
              <button disabled={page >= totalPages} onClick={() => setPage(p => p + 1)}
                className="px-3 py-1 border rounded text-sm disabled:opacity-50">Sonraki</button>
            </div>
          </div>
        )}
        </>)}
      </div>

      {/* Gider Pusulası Modal */}
      <Dialog open={gpModalOpen} onOpenChange={setGpModalOpen}>
        <DialogContent className="max-w-3xl max-h-[90vh] overflow-y-auto print:hidden" data-testid="gp-modal">
          <DialogHeader>
            <DialogTitle className="text-lg font-bold flex items-center gap-2">
              <FileText size={20} />
              Gider Pusulası — Takip No {gpData?.assigned_no || gpData?.display_number}
            </DialogTitle>
          </DialogHeader>
          {gpData && (
            <div className="print:hidden">
              {gpData.cargo_campaign_warning && (
                <div className="mb-3 flex items-start gap-2 rounded-lg border-2 border-red-400 bg-red-50 px-3 py-2 text-red-800">
                  <span className="text-lg leading-none">⚠️</span>
                  <span className="text-sm font-bold">{gpData.cargo_campaign_warning}</span>
                </div>
              )}
              {/* Kargo müşteriden kesildiyse gider pusulasında AÇIKÇA belirt (net ödeme kargo kadar düşük). */}
              {gpData.cargo?.mode === "deducted" && Number(gpData.cargo?.amount) > 0 && (
                <div className="mb-3 flex items-start gap-2 rounded-lg border-2 border-amber-400 bg-amber-50 px-3 py-2 text-amber-900">
                  <span className="text-lg leading-none">🚚</span>
                  <span className="text-sm font-semibold">
                    {gpData.cargo_deduction_note
                      || `Kargo bedeli ${fmt2(gpData.cargo.amount)} TL müşteriden kesildi (ücretsiz kargo hakkı iade sonrası kalktı). Net ödeme buna göre ${fmt2(gpData.totals?.net)} TL.`}
                  </span>
                </div>
              )}
              <p className="text-xs text-gray-500 mb-2">
                {gpData.preview
                  ? <>Önizleme — gider pusulası numarası <b className="text-amber-700">YAZDIR'a basınca atanır</b>. Yazdırmadan kapatırsanız numara yanmaz.</>
                  : <>Bu pusula <span className="font-mono font-bold text-purple-700">#{gpData.assigned_no || gpData.display_number}</span> numaralı matbu forma basılacak. Numara kağıda yazılmaz; yalnız veriler basılır.</>}
              </p>
              <div className="border rounded-lg overflow-hidden bg-white mx-auto" style={{ width: `${GP_SLIP_W_MM}mm`, maxWidth: "100%" }}>
                <GiderPusulasiSlip data={gpData} overlay={false} offX={0} offY={0} guides preview />
              </div>
              <div className="flex justify-end gap-3 mt-4">
                <button onClick={() => setGpModalOpen(false)}
                  className="px-4 py-2 bg-gray-100 text-gray-700 rounded-lg text-sm font-medium hover:bg-gray-200">Kapat</button>
                <button onClick={printSingleGp}
                  data-testid="gp-print-btn"
                  className="px-6 py-2 bg-purple-600 text-white rounded-lg text-sm font-bold hover:bg-purple-700">
                  <Printer size={16} className="inline mr-2" />Yazdır
                </button>
              </div>
            </div>
          )}
        </DialogContent>
      </Dialog>


    </div>
  );
}

function fmt2(v) {
  return new Intl.NumberFormat("tr-TR", { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(v || 0);
}

// Tek matbu form (sütun): 74.25mm x 210mm. overlay=true -> yalnız işaretli alan verisi basılır
// (matbu için). overlay=false -> gri referans form + siyah veri (boş kağıt / hizalama testi).
function GiderPusulasiSlip({ data, overlay, offX = 0, offY = 0, guides = false }) {
  if (!data) return null;
  const c = data.customer || {};
  const tot = data.totals || {};
  const items = data.items || [];
  const dt = data.date ? new Date(data.date) : null;
  const dateStr = dt ? dt.toLocaleDateString("tr-TR") : "";
  const timeStr = dt ? dt.toLocaleTimeString("tr-TR", { hour: "2-digit", minute: "2-digit", second: "2-digit" }) : "";
  const net = tot.net || 0;
  const matrah = tot.net_without_vat || 0;
  const kdv = tot.vat_amount || 0;
  // Kargo müşteriden kesildiyse (deducted) AYRI göster: kesilen tutar "indirim"e katlanmıştı;
  // burada ayırıp "Kargo mahsubu" satırında gösteririz (çift saymadan, net değişmez).
  const cargoDeducted = (data.cargo && data.cargo.mode === "deducted") ? (Number(data.cargo.amount) || 0) : 0;
  const indirim = Math.max(0, (tot.discount || 0) - cargoDeducted);
  const words = sayiToWords(Math.round(net));
  const cityLine = [c.district, c.city, c.country || "Türkiye"].filter(Boolean).join("/");

  const wrap = {
    position: "relative", width: GP_SLIP_W_MM + "mm", height: GP_SLIP_H_MM + "mm",
    boxSizing: "border-box", overflow: "hidden", fontFamily: "Arial, sans-serif",
    color: "#000", background: "#fff", borderRight: guides ? "0.3mm dashed #c084fc" : "none",
  };

  // --- GRİ REFERANS (yalnız overlay kapalıyken / önizlemede; KAĞIDA BASILMAZ) ---
  const G = "#9aa0a6";
  const Gt = (x, y, s, txt, extra = {}) => (
    <div style={{ position: "absolute", left: x + "mm", top: y + "mm", fontSize: s + "mm", color: G, whiteSpace: "nowrap", ...extra }}>{txt}</div>
  );
  const reference = !overlay && (
    <div style={{ position: "absolute", inset: 0 }}>
      {Gt(5, 4, 1.7, "No: 3  34307 Küçükçekmece/İST.")}
      {Gt(5, 7.5, 1.7, "Halkalı V.D.: 781 081 6779")}
      {Gt(5, 11, 1.7, "Ticaret Sicil No: 203113-5")}
      {Gt(5, 14.5, 1.7, "iletisim@facette.com.tr")}
      <div style={{ position: "absolute", left: "52mm", top: "4mm", width: "13mm", height: "13mm", border: "0.3mm solid " + G, borderRadius: "50%", textAlign: "center", fontSize: "1.4mm", color: G, lineHeight: "13mm" }}>T.C.</div>
      {Gt(48, 27, 1.9, "İL KODU: 34", { fontWeight: 700 })}
      {Gt(52, 34, 2.0, "SERİ A", { fontWeight: 700 })}
      {Gt(5, 188, 1.6, "Yalnız _______________________")}
      {Gt(5, 193, 1.5, "____ den yukarıda belirtilen Mal/İş")}
      {Gt(5, 196.5, 1.5, "bedelini aldım.")}
      {Gt(5, 202, 1.6, "Adı Soyadı ________________")}
      {Gt(5, 206, 1.6, "Adresi ___________   İMZA")}
      <div style={{ position: "absolute", left: "2.4mm", top: "150mm", transform: "rotate(-90deg)", transformOrigin: "left top", fontSize: "1.7mm", color: "#cc2222" }}>SIRA NO   No {data.assigned_no || ""}</div>
    </div>
  );

  // --- SİYAH VERİ: sıkışık akışkan blok (KAĞIDA BASILAN TEK ALAN) ---
  const row = (label, val, bold) => (
    <div style={{ display: "flex", justifyContent: "space-between", gap: "2mm", fontWeight: bold ? 700 : 400 }}>
      <span>{label}</span><span>{val}</span>
    </div>
  );
  const black = (
    <div style={{ position: "absolute", left: "6mm", top: "49mm", width: "63mm", transform: `translate(${offX}mm, ${offY}mm)`, fontSize: "2.0mm", lineHeight: 1.12, color: "#000" }}>
      <div style={{ fontWeight: 700 }}>{c.name || ""}</div>
      <div style={{ height: "2.4mm" }} />
      {c.address ? <div>{c.address}</div> : null}
      <div>{cityLine}</div>
      <div style={{ height: "1.2mm" }} />
      <div>Sipariş: {data.order_number || ""}</div>
      <div>{dateStr}{timeStr ? "  " + timeStr : ""}</div>
      <div style={{ height: "0.8mm" }} />
      <div>Satış Fatura No: {data.sales_invoice_no || "-"}</div>
      <div>Kargo Firma: {data.cargo_company || "-"}</div>
      <div>Satış Sorumlusu: {data.sales_rep || "-"}</div>
      <div style={{ height: "1mm" }} />
      <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "1.9mm" }}>
        <thead>
          <tr style={{ borderBottom: "0.2mm solid #999" }}>
            <th style={{ textAlign: "left", fontWeight: 700, padding: "0.2mm 0" }}>Açıklama</th>
            <th style={{ textAlign: "right", fontWeight: 700, width: "8mm" }}>Ad.</th>
            <th style={{ textAlign: "right", fontWeight: 700, width: "17mm" }}>Tutar</th>
          </tr>
        </thead>
        <tbody>
          {items.map((it, i) => (
            <tr key={i}>
              <td style={{ padding: "0.3mm 0", wordBreak: "break-word" }}>{it.name || ""}{it.size ? ` — Beden: ${it.size}` : ""}</td>
              <td style={{ textAlign: "right" }}>{it.quantity}</td>
              <td style={{ textAlign: "right" }}>{fmt2(-(it.net_price || 0))}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <div style={{ height: "1.2mm" }} />
      {row("Tutar (V.D.)", fmt2(net), true)}
      {row("Toplam Satır İsk (VD)", fmt2(indirim))}
      {/* Kargo müşteriden kesildiyse her zaman 0 olan 'Dip İsk' satırını kargo mahsubu için kullan
          (satır sayısı sabit → matbu form hizası bozulmaz). */}
      {cargoDeducted > 0
        ? row("Kargo Mahsubu (müşt.)", "−" + fmt2(cargoDeducted))
        : row("Toplam Dip İsk (/D)", fmt2(0))}
      {row("Vergi Matrahı", fmt2(matrah))}
      {row("Kdv", fmt2(kdv))}
      {row("Net Tutar", fmt2(net), true)}
      <div style={{ height: "0.8mm" }} />
      <div>Yalnız: {words} TL</div>
    </div>
  );

  return <div style={wrap}>{reference}{black}</div>;
}

// A4 YATAY: her iade = 1 sayfa, aynı pusula 4 kopya (4 sütun). Ekranda gizli, yazdırmada görünür.
function GpPrintLayer({ slips, overlay, offX, offY, guides }) {
  if (typeof document === "undefined") return null;
  return createPortal(
    <div className="gp-print">
      <style>{`
        .gp-print { display: none; }
        @media print {
          @page { size: A4 landscape; margin: 0; }
          html, body { margin: 0 !important; padding: 0 !important; background: #fff !important; }
          /* Yazdırmada SADECE gider pusulası katmanı; admin arayüzü/başlık/kenar çubuğu/modal gizlenir */
          body > *:not(.gp-print) { display: none !important; }
          .gp-print { display: block !important; background: #fff; }
          .gp-page { width: ${GP_PAGE_W_MM}mm; height: ${GP_PAGE_H_MM}mm; display: flex; flex-direction: row; page-break-after: always; overflow: hidden; background: #fff; }
          .gp-page:last-child { page-break-after: auto; }
          .gp-col { width: ${GP_SLIP_W_MM}mm; height: ${GP_SLIP_H_MM}mm; }
        }
      `}</style>
      {(slips || []).map((gp, pi) => (
        <div className="gp-page" key={pi}>
          {Array.from({ length: GP_COPIES }).map((_, ci) => (
            <div className="gp-col" key={ci}>
              <GiderPusulasiSlip data={gp} overlay={overlay} offX={offX} offY={offY} guides={guides} />
            </div>
          ))}
        </div>
      ))}
    </div>,
    document.body
  );
}
