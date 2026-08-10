import { useState, useEffect, useCallback, Fragment } from "react";
import axios from "axios";
import { toast } from "sonner";
import { RefreshCw, Search, ChevronDown, ChevronUp, CreditCard, Banknote, Truck, Package, Download, CheckCircle, XCircle, FileText, Trash2 } from "lucide-react";
import MultiSelect from "../../components/admin/MultiSelect";

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;
const BACKEND = process.env.REACT_APP_BACKEND_URL;

// TÜM sipariş durumları — order_statuses.py kataloğuyla birebir (iade sayfasında da hepsi seçilebilir)
const STATUS_OPTS = [
  { value: "pending", label: "Onay Bekliyor" },
  { value: "awaiting_payment", label: "Ödeme Bekleniyor (Havale/EFT)" },
  { value: "payment_notified", label: "Ödeme Bildirimi Alındı" },
  { value: "confirmed", label: "Onaylandı" },
  { value: "preparing", label: "Hazırlanıyor" },
  { value: "processing", label: "İşleme Alındı" },
  { value: "ready_to_ship", label: "Kargoya Hazır" },
  { value: "shipped", label: "Kargoya Verildi" },
  { value: "in_transit", label: "Taşınıyor" },
  { value: "out_for_delivery", label: "Dağıtımda" },
  { value: "delivered", label: "Teslim Edildi" },
  { value: "undelivered", label: "Teslim Edilemedi" },
  { value: "return_requested", label: "İade Talebi Alındı" },
  { value: "return_approved", label: "İade Onaylandı" },
  { value: "return_rejected", label: "İade Reddedildi" },
  { value: "return_in_transit", label: "İade Kargoda" },
  { value: "returned", label: "İade Tamamlandı" },
  { value: "partial_refunded", label: "Kısmi İade Yapıldı" },
  { value: "refunded", label: "İade Bedeli Ödendi" },
  { value: "cancelled", label: "İptal Edildi" },
];
const STATUS_LABEL = Object.fromEntries(STATUS_OPTS.map((s) => [s.value, s.label]));
const STATUS_CLS = {}; // durum renkleri kaldırıldı — tüm durumlar nötr görünür

// 6 AŞAMALI operasyonel iade akışı — "Tüm İadeler" yok, "Kısmi İade" ayrı sekme DEĞİL.
// Her hane bir/birkaç order status'üne map'lenir; 5. hane refunded + partial_refunded'ı birlikte gösterir.
// "Kısmi İade Yapıldı" durum açılır menüsünde elle seçilebilir kalır. Sayaçlar status_counts'tan gelir.
const ALL_RETURN_STATUSES = ["return_requested", "return_in_transit", "returned", "return_approved", "refunded", "partial_refunded", "return_rejected"];
const RETURN_TABS = [
  { key: "",                          label: "Tüm İadeler",       statuses: ALL_RETURN_STATUSES },
  { key: "return_requested",          label: "Talep Oluşturulan", statuses: ["return_requested"] },
  { key: "return_approved",           label: "Onaylananlar",      statuses: ["return_approved"] },
  { key: "refunded,partial_refunded", label: "İade Ödemeleri",    statuses: ["refunded", "partial_refunded"] },
  { key: "return_rejected",           label: "Reddedilenler",     statuses: ["return_rejected"] },
];

const PAYMENT_OPTS = [
  { value: "", label: "Tüm Ödeme Tipleri" },
  { value: "bank_transfer", label: "Havale / EFT" },
  { value: "credit_card", label: "Kredi Kartı" },
  { value: "cash_on_delivery", label: "Kapıda Ödeme" },
];
const payIcon = (m) =>
  m === "bank_transfer" ? <Banknote size={13} /> :
  m === "cash_on_delivery" ? <Truck size={13} /> :
  <CreditCard size={13} />;

const fmtTL = (v) => Number(v || 0).toLocaleString("tr-TR", { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + " TL";
const fmtDate = (s) => {
  if (!s) return "—";
  try { return new Date(s).toLocaleDateString("tr-TR", { day: "2-digit", month: "2-digit", year: "numeric" }); }
  catch { return String(s).slice(0, 10); }
};

export default function RooftrReturns({ embedded = false, gpStart = "085490", onGiderCreated, onBulkGider }) {
  const [rows, setRows] = useState([]);
  // Tarih kolonuna göre sıralama: Sipariş Tarihi / İade Onay-Ret / İade Ödeme.
  // dir "desc" = en yeni üstte. Aynı başlığa tekrar tıklayınca yön değişir.
  const [sort, setSort] = useState({ key: "", dir: "desc" });
  const toggleSort = (key) =>
    setSort((s) => (s.key === key ? { key, dir: s.dir === "desc" ? "asc" : "desc" } : { key, dir: "desc" }));
  const _ts = (v) => { const t = v ? Date.parse(v) : NaN; return Number.isNaN(t) ? null : t; };
  const sortedRows = (() => {
    if (!sort.key) return rows;
    const arr = [...rows];
    arr.sort((a, b) => {
      const ta = _ts(a[sort.key]), tb = _ts(b[sort.key]);
      if (ta === null && tb === null) return 0;
      if (ta === null) return 1;   // tarihi olmayan (—) her zaman en altta
      if (tb === null) return -1;
      return sort.dir === "desc" ? tb - ta : ta - tb;
    });
    return arr;
  })();
  const SortHead = ({ label, k }) => (
    <th className="px-3 py-2.5 font-bold whitespace-nowrap cursor-pointer select-none hover:text-gray-700"
        onClick={() => toggleSort(k)} title="Sıralamak için tıkla">
      <span className="inline-flex items-center gap-1">
        {label}
        {sort.key === k
          ? (sort.dir === "desc" ? <ChevronDown size={13} /> : <ChevronUp size={13} />)
          : <ChevronDown size={12} className="text-gray-300" />}
      </span>
    </th>
  );
  // Client-side sayfalama: tümü yüklenir, sıralama TÜM listeyi kapsar, ekranda sayfa sayfa gösterilir.
  const PER_PAGE = 50;
  const [cpage, setCpage] = useState(1);
  const pageCount = Math.max(1, Math.ceil(sortedRows.length / PER_PAGE));
  const _cpage = Math.min(cpage, pageCount);
  const pageRows = sortedRows.slice((_cpage - 1) * PER_PAGE, _cpage * PER_PAGE);
  // Liste (filtre/arama sonrası yeniden yüklenince) veya sıralama değişince ilk sayfaya dön.
  useEffect(() => { setCpage(1); }, [rows, sort.key, sort.dir]);
  const [loading, setLoading] = useState(true);
  const [pulling, setPulling] = useState(false);
  const [redating, setRedating] = useState(false);
  const [statusFilter, setStatusFilter] = useState("");
  const [paymentFilter, setPaymentFilter] = useState("");
  const [search, setSearch] = useState("");
  const [debounced, setDebounced] = useState("");
  const [statusCounts, setStatusCounts] = useState({});
  const [paymentCounts, setPaymentCounts] = useState({});
  const [totalReturns, setTotalReturns] = useState(0);
  const [busyId, setBusyId] = useState("");
  const [exporting, setExporting] = useState(false);
  const [expandedId, setExpandedId] = useState(null);
  const [perms, setPerms] = useState([]);
  const [wf, setWf] = useState(null); // iade işlem akışı modal'ı
  const [selItems, setSelItems] = useState({}); // açılır detayda tiklenen kalemler: { "orderId::index": true }
  const [editGpNo, setEditGpNo] = useState(null); // gider pusulası no inline düzenleme: { id, value }
  const [cargoSel, setCargoSel] = useState({}); // kargo satırı tiklendi mi: { orderId: true }
  const [editRows, setEditRows] = useState({}); // onaylanmış iadede kalem seçimini düzenleme kilidi açık mı: { orderId: true }
  const [seededRows, setSeededRows] = useState({}); // onay geçmişi kutucukları bir kez önişaretlendi mi (tekrar ezmesin)
  const [freeShipFee, setFreeShipFee] = useState(0); // ücretsiz-kargo mahsup tutarı (ayarlardan)
  const [freeShipThreshold, setFreeShipThreshold] = useState(0); // ücretsiz kargo eşiği (ayarlardan)
  const [selRows, setSelRows] = useState(new Set()); // TOPLU GP: seçili iade satırları (order id)
  const [bulkBusy, setBulkBusy] = useState(false);
  // Tek kaynak: durum listesi Ayarlar → Sipariş Durumları'ndan beslenir (görünürlük + özel durumlar dahil).
  const [statusOpts, setStatusOpts] = useState(STATUS_OPTS);        // dropdown (yalnız "görünür" olanlar)
  const [statusLabelMap, setStatusLabelMap] = useState(STATUS_LABEL); // tüm etiketler (pasif olanlar da)
  const toggleItem = (rid, i) => setSelItems((s) => { const k = `${rid}::${i}`; const n = { ...s }; if (n[k]) delete n[k]; else n[k] = true; return n; });
  const toggleCargo = (rid) => setCargoSel((s) => { const n = { ...s }; if (n[rid]) delete n[rid]; else n[rid] = true; return n; });
  const selCount = (rid) => Object.keys(selItems).filter((k) => k.startsWith(`${rid}::`)).length;

  const auth = () => ({ headers: { Authorization: `Bearer ${localStorage.getItem("token")}` } });
  // Durum kataloğunu tek kaynaktan çek (Ayarlar). Hata olursa hardcoded fallback kalır.
  useEffect(() => {
    (async () => {
      try {
        const r = await axios.get(`${API}/settings/order-statuses`, auth());
        const all = r.data?.statuses || [];
        if (all.length) {
          setStatusLabelMap(Object.fromEntries(all.map((s) => [s.key, s.label])));
          setStatusOpts(all.filter((s) => s.active).map((s) => ({ value: s.key, label: s.label })));
        }
      } catch { /* hardcoded fallback */ }
    })();
  }, []);
  const lbl = (s) => statusLabelMap[s] || STATUS_LABEL[s] || s;

  // Arama debounce (350ms)
  useEffect(() => {
    const t = setTimeout(() => setDebounced(search), 350);
    return () => clearTimeout(t);
  }, [search]);

  const load = useCallback(async () => {
    try {
      setLoading(true);
      const params = new URLSearchParams();
      if (statusFilter) params.append("status", statusFilter);
      if (paymentFilter) params.append("payment", paymentFilter);
      if (debounced) params.append("search", debounced);
      params.append("limit", "10000");   // TÜMÜNÜ çek → client-side sırala + sayfala
      const res = await axios.get(`${API}/admin/rooftr/return-orders?${params}`, auth());
      setRows(res.data.orders || []);
      setFreeShipFee(Number(res.data.free_ship_fee) || 0);
      setFreeShipThreshold(Number(res.data.free_shipping_threshold) || 0);
      setStatusCounts(res.data.status_counts || {});
      setPaymentCounts(res.data.payment_counts || {});
      setTotalReturns(res.data.total_returns || 0);
    } catch (e) {
      console.error(e);
      toast.error(e.response?.data?.detail || "İade siparişleri yüklenemedi");
    } finally {
      setLoading(false);
    }
  }, [statusFilter, paymentFilter, debounced]);

  useEffect(() => { load(); }, [load]);

  // Rooftr'dan siparişleri çek (iade olanlar orders'a düşer, sonra listeyi yenile)
  const pullFromRooftr = async () => {
    setPulling(true);
    toast.info("Rooftr'dan siparişler çekiliyor — bu işlem birkaç dakika sürebilir…");
    try {
      const res = await axios.post(
        `${API}/integrations/rooftr/orders/import?days=365&pages=20&limit=100`,
        {},
        auth()
      );
      if (res.data?.success === false) {
        // Backend gerçek WS hatasını message + error_detail ile döndürür
        toast.error(res.data.message || "Rooftr'dan sipariş çekilemedi.");
      } else {
        toast.success(res.data.message || "Rooftr siparişleri çekildi");
      }
      await load();
    } catch (e) {
      toast.error(e.response?.data?.detail || "Rooftr'dan çekme başarısız. WS yetki kodu / Sipariş Servisi iznini kontrol edin.");
    } finally {
      setPulling(false);
    }
  };

  // Tüm siparişlerin TARİHİ'ni Rooftr'daki gerçek SiparisTarihi'ne çek.
  // Zaman aşımına takılmamak için backend'i parça parça (5 sayfa) döngüyle çağırır.
  const refreshDates = async () => {
    if (redating || pulling) return;
    setRedating(true);
    let page = 1, totalFixed = 0;
    try {
      for (let guard = 0; guard < 60; guard++) {
        const res = await axios.post(
          `${API}/admin/rooftr/orders/refresh-dates?page=${page}&per_pages=5`,
          {},
          auth()
        );
        const d = res.data || {};
        if (d.success === false) {
          toast.error(d.message || "Tarih güncelleme başarısız.", { id: "redate" });
          break;
        }
        totalFixed += d.fixed || 0;
        toast.info(`Tarihler güncelleniyor… ${totalFixed} sipariş düzeltildi`, { id: "redate" });
        if (!d.has_more) {
          toast.success(`Tamamlandı — ${totalFixed} siparişin tarihi gerçek tarihine çekildi.`, { id: "redate" });
          break;
        }
        page = d.next_page || page + 5;
      }
      await load();
    } catch (e) {
      toast.error(e.response?.data?.detail || e.response?.data?.message || "Tarih güncelleme başarısız.", { id: "redate" });
    } finally {
      setRedating(false);
    }
  };

  // İade siparişlerini Excel'e aktar (görseldeki kolon düzeni — backend openpyxl üretir)
  const exportExcel = async () => {
    setExporting(true);
    try {
      const params = new URLSearchParams();
      if (statusFilter) params.append("status", statusFilter);
      if (paymentFilter) params.append("payment", paymentFilter);
      if (debounced) params.append("search", debounced);
      const res = await fetch(`${API}/admin/rooftr/return-orders/export?${params.toString()}`, {
        headers: { Authorization: `Bearer ${localStorage.getItem("token")}` },
      });
      if (!res.ok) throw new Error("export failed");
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = "iade-siparisleri.xlsx";
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

  // Sipariş durumunu değiştir (mevcut endpoint — bildirim de buradan gider)
  const changeStatus = async (row, newStatus) => {
    if (newStatus === row.status) return;
    // DENETİM FIX: 'İade Bedeli Ödendi'/'Kısmi İade' müşteriye "iadeniz ödendi" bildirimi
    // gönderir ama tutar/yöntem/işlem kaydı OLUŞTURMAZ. Yanlışlıkla göndermeyi önlemek için
    // onay iste (gerçek iade ödemesi iyzico/havale ile ayrıca yapılmalı).
    if (newStatus === "refunded" || newStatus === "partial_refunded") {
      const ok = await window.appConfirm(
        `"${lbl(newStatus)}" seçmek müşteriye iade bildirimi gönderir ancak gerçek para iadesini KAYDETMEZ. ` +
        `İade ödemesini (iyzico/havale) ayrıca yaptığınızdan emin misiniz? Devam edilsin mi?`);
      if (!ok) return; // kontrollü select otomatik eski değere döner
    }
    setBusyId(row.id);
    const prev = row.status;
    setRows((rs) => rs.map((r) => (r.id === row.id ? { ...r, status: newStatus } : r)));
    try {
      await axios.put(`${API}/orders/${row.id}/status?status=${encodeURIComponent(newStatus)}`, {}, auth());
      // Yeni durum İADE grubu DIŞINDAYSA (ör. 'İptal Edildi') sipariş artık iade sayfasına
      // ait DEĞİL → satırı listeden düş. Böylece iptal edilen sipariş burada asılı kalmaz,
      // İptaller sayfasında (status=cancelled) görünür. (Aktif filtre eşleşmiyorsa da düşür.)
      const stillHere = ALL_RETURN_STATUSES.includes(newStatus)
        && (!statusFilter || statusFilter.split(",").includes(newStatus));
      if (!stillHere) {
        setRows((rs) => rs.filter((r) => r.id !== row.id));
      }
      toast.success(`Durum güncellendi: ${lbl(newStatus)}`);
    } catch (e) {
      setRows((rs) => rs.map((r) => (r.id === row.id ? { ...r, status: prev } : r)));
      toast.error(e.response?.data?.detail || "Durum güncellenemedi");
    } finally {
      setBusyId("");
    }
  };

  // Yetkiler (RBAC) — butonları yetkiye göre göster
  useEffect(() => {
    axios.get(`${API}/admin/me/permissions`, auth())
      .then((r) => setPerms(r.data?.permissions || []))
      .catch(() => {});
  }, []);
  const can = (k) => perms.includes("*") || perms.includes(k);
  // Onay geçmişini DÜZENLEME yetkisi: yalnız muhasebe (returns.expense_note) veya admin (*).
  const canEditApproval = () => perms.includes("*") || perms.includes("returns.expense_note");

  // ONAY GEÇMİŞİ ÖNİŞARETLEME: onaylanmış bir iade detayı AÇILDIĞINDA, geçmişte
  // hangi kalemler/kargo onaylanmışsa kutucukları o seçimle önişaretle (bir kez).
  // Kullanıcı isteği: "neyi onaylamışız geçmişte görebilelim, bir daha tiklenemesin."
  useEffect(() => {
    if (!expandedId) return;
    const r = rows.find((x) => x.id === expandedId);
    if (!r || !r.return_is_approved || seededRows[expandedId]) return;
    setSelItems((s) => {
      const n = { ...s };
      (r.approved_item_indexes || []).forEach((i) => { n[`${expandedId}::${i}`] = true; });
      return n;
    });
    if (r.approved_cargo_deducted) setCargoSel((s) => ({ ...s, [expandedId]: true }));
    setSeededRows((s) => ({ ...s, [expandedId]: true }));
  }, [expandedId, rows, seededRows]);

  // İade işlem akışı: Rooftr siparişini köprüle (customer_returns üret) → tutar önizleme → modal
  const openWorkflow = async (row, mode = "approve") => {
    try {
      setBusyId(row.id);
      const br = await axios.post(`${API}/admin/rooftr/returns/${row.id}/open`, {}, auth());
      const returnId = br.data?.return_id;
      if (!returnId) throw new Error("bridge");
      // Açılır detayda seçili kalem(ler) varsa iade tutarını onların NET toplamından hesapla.
      // ÖNEMLİ: TÜM kalemler seçiliyse bu TAM İADE'dir → override GÖNDERME; backend ödenen
      // tutarı (kargo DAHİL gerçek genel toplam) baz alır. Override sadece GERÇEK kısmi
      // seçimde (bazı kalemler, bazıları değil) gönderilir — yoksa kargo iki kez düşer.
      const itemCount = (row.items || []).length;
      const selIdx = (row.items || []).map((_, i) => i).filter((i) => selItems[`${row.id}::${i}`]);
      const isPartialSelection = selIdx.length > 0 && selIdx.length < itemCount;
      // İNDİRİM ORANINI UYGULA (indirim / ara toplam) — aksi halde liste fiyatı (indirimsiz)
      // iade edilir; müşteri indirimli ödediği hâlde fazla iade alırdı (ör. 2100 yerine 1890).
      // Panel "İade net tutarı" ile BİREBİR aynı formül: product net × (1 − indirim oranı).
      const _base = Number(row.subtotal) || 0;
      const _dr = (_base > 0 && Number(row.discount) > 0) ? Math.min(1, Number(row.discount) / _base) : 0;
      const selAmount = isPartialSelection ? Math.round(selIdx.reduce(
        (a, i) => a + (Number(row.items[i].qty) || 1) * (Number(row.items[i].price) || 0) * (1 - _dr), 0
      ) * 100) / 100 : 0;
      const returnedNet = selAmount > 0 ? selAmount : null;
      // Kısmi onayda seçilen kalemler backend'e KİMLİKLERİYLE gönderilir (approve kaydına
      // yazılır) ki gider pusulası sonradan kesilse bile onaylanan kalemlere düzenlensin.
      const selIdents = isPartialSelection
        ? selIdx.map((i) => row.items[i]).filter(Boolean)
            .map((it) => ({ barcode: it.barcode || "", name: it.name || "", size: it.size || "", color: it.color || "" }))
        : [];
      // Kargo: liste ekranındaki "Kargoyu müşteriden kes" işaretliyse kargo bedeli iade
      // tutarından mahsup edilir (kusur müşteride); işaretli değilse kargo da tam iade edilir
      // (mağaza üstlenir). Elle "kusur" seçimi yok — bu checkbox tek karar noktasıdır.
      const fault = cargoSel[row.id] ? "customer" : "store";
      let preview = null;
      try {
        const q = `fault=${fault}${returnedNet != null ? `&returned_net=${returnedNet}` : ""}`;
        const pv = await axios.get(`${API}/orders/returns/${returnId}/refund-preview?${q}`, auth());
        preview = pv.data?.breakdown || null;
      } catch { /* önizleme alınamazsa modal yine açılır */ }
      setWf({
        row, returnId, status: br.data?.status || "created", fault,
        preview, returnedNet,
        selIdx: isPartialSelection ? selIdx : [], selIdents,
        finalAmount: preview ? preview.auto_refund : (returnedNet ?? 0),
        edited: false, note: "",
        loading: false, rejectReason: "", reship: false,
        showReject: mode === "reject",
      });
    } catch (e) {
      toast.error(e.response?.data?.detail || "İade işlem akışı açılamadı");
    } finally { setBusyId(""); }
  };
  // #12: Kısmi iadede kargo kararını modal içinde AÇIKÇA değiştir → refund'u yeniden hesapla.
  const changeCargoFault = async (newFault) => {
    if (!wf || wf.loading || wf.fault === newFault) return;
    setWf((m) => ({ ...m, fault: newFault, loading: true }));
    try {
      const q = `fault=${newFault}${wf.returnedNet != null ? `&returned_net=${wf.returnedNet}` : ""}`;
      const pv = await axios.get(`${API}/orders/returns/${wf.returnId}/refund-preview?${q}`, auth());
      const preview = pv.data?.breakdown || null;
      setWf((m) => ({ ...m, fault: newFault, preview,
        finalAmount: preview ? preview.auto_refund : m.finalAmount, edited: false, loading: false }));
    } catch {
      setWf((m) => ({ ...m, loading: false }));
    }
  };

  const wfApprove = async () => {
    if (!wf) return;
    setWf((m) => ({ ...m, loading: true }));
    try {
      const body = { fault: wf.fault, note: wf.note };
      if (wf.returnedNet != null) body.returned_net = wf.returnedNet;
      if (wf.edited) body.refund_amount = Number(wf.finalAmount);
      if (wf.selIdx?.length) {
        body.item_indexes = wf.selIdx;
        body.selected_items = wf.selIdents || [];
      }
      const res = await axios.post(`${API}/orders/returns/${wf.returnId}/approve`, body, auth());
      toast.success(`İade onaylandı · ${fmtTL(res.data?.refund_amount)}`);
      setWf((m) => ({ ...m, status: "approved", loading: false }));
      load();
    } catch (e) { toast.error(e.response?.data?.detail || "Onaylanamadı"); setWf((m) => ({ ...m, loading: false })); }
  };
  const wfReject = async () => {
    if (!wf) return;
    if (!wf.rejectReason.trim()) { toast.error("Ret sebebi zorunludur"); return; }
    setWf((m) => ({ ...m, loading: true }));
    try {
      const res = await axios.post(`${API}/orders/returns/${wf.returnId}/reject`,
        { reason: wf.rejectReason.trim(), reship: !!wf.reship }, auth());
      toast.success(res.data?.reship_code ? `Reddedildi · Geri gönderim: ${res.data.reship_code}` : "İade reddedildi");
      setWf(null); load();
    } catch (e) { toast.error(e.response?.data?.detail || "Reddedilemedi"); setWf((m) => ({ ...m, loading: false })); }
  };
  // wfGider / wfPay kaldırıldı: modaldaki "Gider Pusulası" ve "İade Bedeli Öde"
  // butonları kaldırıldı. Gider pusulası satırdaki belge ikonundan (handleSiteGider),
  // "İade Bedeli Ödendi" işaretlemesi ise Durum açılır menüsünden yapılır.



  // Satır-içi gider pusulası (Trendyol ile ORTAK seri): siparişi köprüle → gider pusulası
  // (tracking_no = ortak başlangıç no gpStart) → parent yazdırma modalını aç + sayacı +1 ilerlet.
  // Onaylanmış iadenin DÜZENLENMİŞ seçimini (kalemler + kargo) muhasebe/admin olarak
  // onayla ve kilitle. Statü/stok/bildirim değişmez; yenileyince yeni seçim korunur.
  const saveApprovalEdit = async (r) => {
    setBusyId(r.id);
    try {
      const br = await axios.post(`${API}/admin/rooftr/returns/${r.id}/open`, {}, auth());
      const returnId = br.data?.return_id;
      if (!returnId) throw new Error("bridge");
      const itemCount = (r.items || []).length;
      const selIdx = (r.items || []).map((_, i) => i).filter((i) => selItems[`${r.id}::${i}`]);
      if (!selIdx.length) { toast.error("En az bir kalem seçin (hesap yalnız seçili kalemlere göre yapılır)."); setBusyId(""); return; }
      const isPartialSelection = selIdx.length > 0 && selIdx.length < itemCount;
      // Panel "İade net tutarı" ile BİREBİR aynı formül: seçili kalem net × (1 − indirim oranı).
      // KDV-dahil kalem fiyatı (r.items[i].price zaten KDV-dahile ölçekli) kullanılır →
      // hesap SADECE tiklenen kalemlere göre olur (Kadir talebi).
      const _base = Number(r.subtotal) || 0;
      const _dr = (_base > 0 && Number(r.discount) > 0) ? Math.min(1, Number(r.discount) / _base) : 0;
      // VADE FARKI burada EKLENMEZ — backend (_compute_refund_breakdown) iade edilen ürün
      // oranında orantılı vade farkını TEK KAYNAKTAN ekler (çift sayım önlenir). Buradan yalnız
      // ürün neti gönderilir; initial-approve akışıyla (satır ~349) birebir aynı formül.
      const selAmount = isPartialSelection ? Math.round(selIdx.reduce(
        (a, i) => a + (Number(r.items[i].qty) || 1) * (Number(r.items[i].price) || 0) * (1 - _dr), 0
      ) * 100) / 100 : null;
      const selIdents = selIdx.map((i) => r.items[i]).filter(Boolean)
        .map((it) => ({ barcode: it.barcode || "", name: it.name || "", size: it.size || "", color: it.color || "" }));
      const body = { item_indexes: selIdx, selected_items: selIdents, include_cargo: !!cargoSel[r.id] };
      if (selAmount != null) body.returned_net = selAmount;
      const res = await axios.post(`${API}/orders/returns/${returnId}/update-approval`, body, auth());
      toast.success(`Düzenleme onaylandı · iade net ${fmtTL(res.data?.refund_amount || 0)}`);
      setEditRows((s) => { const n = { ...s }; delete n[r.id]; return n; });
      // seededRows'u TEMİZLEME: temizlersek load() tamamlanmadan useEffect ESKİ satırlardan
      // yeniden seed edip kendini 'tohumlandı' işaretliyor; yeni (kaydedilen) satırlar gelince
      // bir daha seed etmiyor → kaydedilen seçim yerine ESKİsi görünüyordu (1 tik kalıyordu).
      // Kullanıcının selItems seçimi (yeni tikler) zaten ekranda; olduğu gibi korunur.
      load();
    } catch (e) {
      toast.error(e.response?.data?.detail || "Düzenleme onayı başarısız");
    } finally { setBusyId(""); }
  };

  const handleSiteGider = async (r) => {
    try {
      setBusyId(r.id);
      const br = await axios.post(`${API}/admin/rooftr/returns/${r.id}/open`, {}, auth());
      const returnId = br.data?.return_id;
      if (!returnId) throw new Error("bridge");
      const trackingNo = String(gpStart || "").trim();
      // Kısmi gider pusulası: açılır detayda seçili kalem index'leri (hiç seçim yoksa tüm sipariş)
      const selIdx = Object.keys(selItems)
        .filter((k) => k.startsWith(`${r.id}::`) && selItems[k])
        .map((k) => parseInt(k.split("::")[1], 10))
        .filter((n) => !Number.isNaN(n));
      const includeCargo = !!cargoSel[r.id];
      const body = { tracking_no: trackingNo };
      if (selIdx.length) {
        body.item_indexes = selIdx;
        // Index kaymasına dayanıklı kimlik eşlemesi: customer_returns kaydı (idempotent
        // bridge) ekrandaki listeden farklıysa backend barcode/ad+beden+renk ile eşler.
        body.selected_items = selIdx
          .map((i) => (r.items || [])[i])
          .filter(Boolean)
          .map((it) => ({ barcode: it.barcode || "", name: it.name || "", size: it.size || "", color: it.color || "" }));
      }
      if (includeCargo) body.include_cargo = true;
      // ÖNİZLEME: numara YAKMADAN hesapla + göster. Numara YAZDIR'a basınca atanır (Kadir).
      const res = await axios.post(`${API}/orders/returns/${returnId}/gider-pusulasi?preview=true`, body, auth());
      const gp = res.data?.gider_pusulasi;
      toast.success("Gider pusulası önizleme — numara YAZDIR'a basınca atanacak");
      // Yazdırınca kalıcılaştırmak için return_id + body'yi taşı.
      if (gp && onGiderCreated) onGiderCreated({ ...gp, assigned_no: trackingNo, preview: true, _returnId: returnId, _gpBody: body });
      // load() YAPMA — DB değişmedi (önizleme); numara atanmadı. Yazdırınca güncellenir.
    } catch (e) {
      toast.error(e.response?.data?.detail || "Gider pusulası oluşturulamadı");
    } finally { setBusyId(""); }
  };

  // ── TOPLU GİDER PUSULASI ─────────────────────────────────────────────────
  const _pad6 = (n) => String(n).padStart(6, "0");
  // Bir iade toplu GP'ye uygun mu? Onaylı + e-Fatura DEĞİL + yetki (GP kuralıyla aynı).
  const bulkGpEligible = (r) => !r.is_efatura && can("returns.expense_note") &&
    (r.return_is_approved || ["return_approved", "returned", "refunded", "partial_refunded"].includes(r.status) || r.has_gider_pusulasi);
  const toggleRowSel = (id) => setSelRows((prev) => { const n = new Set(prev); n.has(id) ? n.delete(id) : n.add(id); return n; });
  const toggleSelAll = () => {
    const ids = (pageRows || []).map((r) => r.id);
    setSelRows((prev) => (ids.length && ids.every((id) => prev.has(id))) ? new Set() : new Set(ids));
  };
  const handleBulkGider = async () => {
    const targets = (rows || []).filter((r) => selRows.has(r.id) && bulkGpEligible(r));
    if (!targets.length) { toast.error("Seçili uygun iade yok (e-Fatura/onaysız hariç)."); return; }
    setBulkBusy(true);
    const base = parseInt(String(gpStart || "0").replace(/\D/g, ""), 10) || 0;
    const gps = [];
    try {
      for (let i = 0; i < targets.length; i++) {
        const r = targets[i];
        const trackingNo = _pad6(base + i);
        const br = await axios.post(`${API}/admin/rooftr/returns/${r.id}/open`, {}, auth());
        const returnId = br.data?.return_id;
        if (!returnId) continue;
        // Onaydaki seçim (approved_items) + kargo kararı (approved_cargo_deducted) baz alınır;
        // numara ATANIR (finalize). Kalem seçimi payload'sız → backend onaylı kalemleri kullanır.
        const res = await axios.post(`${API}/orders/returns/${returnId}/gider-pusulasi`,
          { tracking_no: trackingNo, include_cargo: !!r.approved_cargo_deducted }, auth());
        if (res.data?.gider_pusulasi) gps.push({ ...res.data.gider_pusulasi, assigned_no: trackingNo });
      }
      if (!gps.length) { toast.error("Gider pusulası oluşturulamadı."); return; }
      toast.success(`${gps.length} gider pusulası oluşturuldu (${_pad6(base)}–${_pad6(base + gps.length - 1)})`);
      setSelRows(new Set());
      if (onBulkGider) onBulkGider(gps, base + gps.length);
      load();
    } catch (e) {
      toast.error(e.response?.data?.detail || "Toplu gider pusulası hatası");
    } finally { setBulkBusy(false); }
  };

  // ── TOPLU SİL ────────────────────────────────────────────────────────────
  // Seçili iadeleri (siparişleri) sil. DELETE /orders/{id} fiziksel silmeden
  // ÖNCE orders_deleted arşivine taşır → 'Silinen Siparişler'den geri alınabilir.
  const handleBulkDelete = async () => {
    const targets = (rows || []).filter((r) => selRows.has(r.id));
    if (!targets.length) { toast.error("Seçili iade yok."); return; }
    if (!window.confirm(
      `${targets.length} iade siparişi SİLİNECEK.\n\n` +
      `• Siparişler arşive taşınır (orders_deleted) — 'Silinen Siparişler' sayfasından geri alınabilir.\n` +
      `• Stok otomatik geri EKLENMEZ (silme ≠ iptal/iade onayı).\n\nDevam edilsin mi?`
    )) return;
    setBulkBusy(true);
    let ok = 0, fail = 0;
    try {
      for (const r of targets) {
        try { await axios.delete(`${API}/orders/${r.id}`, auth()); ok++; }
        catch (e) { fail++; }
      }
      if (ok) toast.success(`${ok} iade silindi (arşive taşındı)${fail ? ` · ${fail} başarısız` : ""}`);
      else toast.error("Silme başarısız (yetki?).");
      setSelRows(new Set());
      load();
    } finally { setBulkBusy(false); }
  };

  // Gider pusulası numarasını elle değiştir (satırdaki #no'ya tıklayınca inline düzenlenir).
  const saveGpNo = async (r) => {
    const val = String(editGpNo?.value || "").trim();
    if (!val) { setEditGpNo(null); return; }
    try {
      setBusyId(r.id);
      await axios.post(`${API}/orders/returns/vouchers/set-number`,
        { order_number: r.order_number, display_number: val }, auth());
      toast.success("Gider pusulası numarası güncellendi");
      setEditGpNo(null);
      load();
    } catch (e) {
      toast.error(e.response?.data?.detail || "Numara güncellenemedi");
    } finally { setBusyId(""); }
  };

  const wfCanAct = can("returns.approve") || can("returns.reject") || can("returns.expense_note") || can("returns.refund_pay");

  // Bu iade daha önce karara bağlandı mı? (modal'da Onayla/Reddet'i soluklaştırmak için)
  const wfDecided = wf
    ? ((wf.status === "approved" || ["return_approved", "refunded", "partial_refunded"].includes(wf.row.status))
        ? "approved"
        : (wf.row.status === "return_rejected" ? "rejected" : null))
    : null;

  return (
    <div className={embedded ? "" : "p-4"}>
      {/* Üst bar: çek butonu + özet */}
      <div className="flex flex-wrap items-center justify-between gap-3 mb-4">
        <div className="text-sm text-gray-600">
          İade / kısmi iade durumundaki siparişler.
          {totalReturns > 0 && <span className="ml-1 font-medium text-gray-800">Toplam {totalReturns} iade siparişi.</span>}
        </div>
        {/* Sekme-başına ayrı Excel butonları KALDIRILDI. Tek Excel (tüm gider pusulaları,
            tarih aralıklı, muhasebe formatı) İadeler ekranının üst araç çubuğundadır. */}
      </div>

      {/* İade durum sekmeleri — Trendyol sekmeleriyle birebir görsel dil (oval/pill) */}
      <div className="flex flex-wrap gap-2 mb-4 border-b border-gray-200 pb-2">
        {RETURN_TABS.map((t) => {
          const n = (t.statuses || [t.key]).reduce((a, s) => a + (statusCounts[s] || 0), 0);
          const active = statusFilter === t.key;
          return (
            <button
              key={t.key || "all"}
              onClick={() => setStatusFilter(t.key)}
              className={`whitespace-nowrap px-3 py-1.5 rounded-full text-sm border transition-colors ${
                active
                  ? "bg-gray-900 text-white border-gray-900"
                  : "bg-white text-gray-600 border-gray-200 hover:border-gray-400"
              }`}
            >
              {t.label}
              <span className={`ml-1.5 text-xs ${active ? "text-gray-300" : "text-gray-400"}`}>{n}</span>
            </button>
          );
        })}
      </div>

      {/* Filtreler */}
      <div className="flex flex-wrap items-center gap-2 mb-4">
        <div className="relative">
          <Search size={15} className="absolute left-2.5 top-2.5 text-gray-400" />
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Sipariş no / müşteri / telefon…"
            className="pl-8 pr-3 py-2 text-sm border rounded-lg w-64 focus:outline-none focus:ring-1 focus:ring-gray-300"
          />
        </div>
        <MultiSelect className="w-44" placeholder="Tüm Ödeme Tipleri" value={paymentFilter} onChange={setPaymentFilter}
          options={PAYMENT_OPTS.filter((p) => p.value)} />
      </div>

      {/* Liste */}
      {loading ? (
        <div className="text-center py-16 text-gray-400 text-sm">Yükleniyor…</div>
      ) : rows.length === 0 ? (
        <div className="text-center py-16 border rounded-xl text-gray-400">
          <Package className="mx-auto mb-2 opacity-40" size={28} />
          Rooftr iade siparişi bulunamadı.
          <div className="text-xs mt-1">Yukarıdaki "Siparişleri Çek" ile siparişleri içeri aktarın.</div>
        </div>
      ) : (
        <div className="border rounded-xl overflow-hidden">
          {selRows.size > 0 && (
            <div className="flex items-center gap-3 bg-purple-50 border-b border-purple-200 px-3 py-2">
              <span className="text-xs font-semibold text-purple-800">{selRows.size} iade seçili</span>
              <button onClick={handleBulkGider} disabled={bulkBusy}
                className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-purple-600 text-white text-xs font-bold hover:bg-purple-700 disabled:opacity-50">
                <FileText size={14} /> {bulkBusy ? "Oluşturuluyor…" : "Toplu Gider Pusulası Oluştur"}
              </button>
              {can("orders.delete") && (
                <button onClick={handleBulkDelete} disabled={bulkBusy}
                  className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-red-600 text-white text-xs font-bold hover:bg-red-700 disabled:opacity-50">
                  <Trash2 size={14} /> Seçili İadeleri Sil
                </button>
              )}
              <button onClick={() => setSelRows(new Set())}
                className="text-xs text-gray-500 hover:text-gray-800 font-medium">Seçimi temizle</button>
              <span className="text-[10px] text-gray-500">Numaralar {String(gpStart)}'dan sıralı atanır · e-Fatura/onaysız hariç</span>
            </div>
          )}
          <table className="w-full text-sm">
            <thead className="bg-gray-50 border-b text-gray-500 text-left text-xs uppercase">
              <tr>
                <th className="px-2 py-2.5 w-8">
                  <input type="checkbox" title="Sayfadaki tüm iadeleri seç"
                    checked={(pageRows || []).length > 0 && (pageRows || []).every((r) => selRows.has(r.id))}
                    onChange={toggleSelAll} />
                </th>
                <th className="px-3 py-2.5 font-bold">Sipariş No</th>
                <th className="px-3 py-2.5 font-bold">İade No</th>
                <th className="px-3 py-2.5 font-bold">Müşteri</th>
                <th className="px-3 py-2.5 font-bold">Sebep</th>
                <th className="px-3 py-2.5 font-bold">Ödeme</th>
                <th className="px-3 py-2.5 font-bold">Kargo</th>
                <th className="px-3 py-2.5 font-bold text-right whitespace-nowrap">Tutar<span className="block text-[9px] font-normal normal-case text-gray-400">Brüt / İskonto / Net</span></th>
                <SortHead label="Sipariş Tarihi" k="created_at" />
                <SortHead label="İade Onay/Ret" k="return_approved_at" />
                <SortHead label="İade Ödeme" k="refund_paid_at" />
                <th className="px-3 py-2.5 font-bold">Durum</th>
                <th className="px-3 py-2.5 font-bold text-right w-8">İşlemler</th>
              </tr>
            </thead>
            <tbody className="divide-y">
              {pageRows.map((r) => (
                <Fragment key={r.id}>
                  <tr className="hover:bg-gray-50">
                    <td className="px-2 py-2.5 align-top">
                      <input type="checkbox" checked={selRows.has(r.id)}
                        onChange={() => toggleRowSel(r.id)}
                        title={bulkGpEligible(r) ? "Toplu gider pusulası / silme için seç" : "Silme için seç (GP: yalnız onaylı/e-Fatura hariç)"} />
                    </td>
                    <td className="px-3 py-2.5">
                      <div className="font-mono text-sm font-bold text-blue-600">{r.order_number}</div>
                      {r.is_efatura && (
                        <div className="mt-1 inline-flex items-center gap-1 px-2 py-0.5 rounded bg-red-600 text-white text-[10px] font-bold border border-red-700 animate-pulse"
                             title="Bu sipariş e-Fatura / kurumsal siparişidir. Gider pusulası düzenlenemez — müşteriden iade faturası alınmalıdır.">
                          ⚠️ BU SİPARİŞ E-FATURADIR
                        </div>
                      )}
                      {r.item_count > 0 && <div className="text-xs text-gray-400">{r.item_count} ürün</div>}
                      {Array.isArray(r.staff_notes) && r.staff_notes.length > 0 && (
                        <div className="mt-1 inline-flex items-center gap-1 px-1.5 py-0.5 rounded bg-indigo-100 text-indigo-700 text-[10px] font-semibold border border-indigo-200 cursor-help"
                             title={r.staff_notes.map((n) => `• ${n.text}${n.by ? `  — ${n.by}` : ""}`).join("\n")}>
                          📝 Personel Notu{r.staff_notes.length > 1 ? ` (${r.staff_notes.length})` : ""}
                        </div>
                      )}
                    </td>
                    <td className="px-3 py-2.5">
                      {r.iade_no
                        ? <div className="font-mono text-xs font-semibold text-gray-800 truncate max-w-[120px]" title={r.iade_no}>{r.iade_no}</div>
                        : <span className="text-gray-400">—</span>}
                      {r.gonderi_no && <div className="font-mono text-[10px] text-gray-400 truncate max-w-[120px]" title={`Gönderi No: ${r.gonderi_no}`}>Gönderi: {r.gonderi_no}</div>}
                    </td>
                    <td className="px-3 py-2.5">
                      <div className="text-gray-800">{r.customer_name}</div>
                      {r.phone && <div className="text-xs text-gray-400">{r.phone}</div>}
                    </td>
                    <td className="px-3 py-2.5 text-xs text-gray-600 max-w-[140px] truncate" title={r.reason || ""}>{r.reason || "—"}</td>
                    <td className="px-3 py-2.5">
                      <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-md text-xs border ${r.payment_method === "credit_card" ? "bg-blue-100 text-blue-700 border-blue-200" : r.payment_method === "bank_transfer" ? "bg-purple-100 text-purple-700 border-purple-200" : "bg-gray-100 text-gray-700 border-gray-200"}`}>
                        {payIcon(r.payment_method)} {r.payment_label}
                      </span>
                    </td>
                    <td className="px-3 py-2.5 text-xs">
                      {(r.return_cargo_provider || r.cargo_provider_name || r.return_code) ? (
                        <div className="flex flex-col">
                          <span className="font-medium text-orange-600 text-[11px]">{r.return_cargo_provider || r.cargo_provider_name || "Kargo"}</span>
                          {r.return_code && <span className="font-mono text-[10px] text-gray-500 truncate max-w-[110px]" title={r.return_code}>{r.return_code}</span>}
                        </div>
                      ) : <span className="text-gray-400">—</span>}
                    </td>
                    <td className="px-3 py-2.5 text-right font-mono whitespace-nowrap">
                      {/* Net = müşterinin GERÇEKTE ödediği: taksitliyse vade farkı DAHİL (charged_total),
                          değilse Genel toplam. İrem Kılıç 4.977,33 (taksitli); Kübra 1.884; Senem 985,15. */}
                      {Number(r.discount) > 0 && <div className="text-gray-500 line-through text-xs leading-tight">{fmtTL(r.subtotal || r.total)}</div>}
                      {Number(r.discount) > 0 && <div className="text-orange-600 text-xs font-bold leading-tight">-{fmtTL(r.discount)}</div>}
                      <div className="font-bold text-gray-900 leading-tight">{fmtTL(Number(r.charged_total) || Number(r.total))}</div>
                      {Number(r.vade_farki) > 0 && <div className="text-[9px] text-amber-600 leading-tight whitespace-nowrap">taksit · vade +{fmtTL(r.vade_farki)}</div>}
                    </td>
                    <td className="px-3 py-2.5 text-gray-500 whitespace-nowrap">{fmtDate(r.created_at)}</td>
                    <td className="px-3 py-2.5 text-gray-500 whitespace-nowrap">{r.return_approved_at ? fmtDate(r.return_approved_at) : "—"}</td>
                    <td className="px-3 py-2.5 text-gray-500 whitespace-nowrap">{r.refund_paid_at ? fmtDate(r.refund_paid_at) : "—"}</td>
                    <td className="px-3 py-2.5">
                      <select
                        value={r.status}
                        disabled={busyId === r.id}
                        onChange={(e) => changeStatus(r, e.target.value)}
                        className={`text-xs border rounded-md px-2 py-1 font-medium focus:outline-none focus:ring-1 focus:ring-gray-300 ${STATUS_CLS[r.status] || "bg-gray-50 text-gray-700 border-gray-200"}`}
                      >
                        {(statusOpts.some((o) => o.value === r.status) ? statusOpts : [{ value: r.status, label: lbl(r.status) }, ...statusOpts]).map((s) => <option key={s.value} value={s.value}>{s.label}</option>)}
                      </select>
                    </td>
                    <td className="px-3 py-2.5">
                      <div className="flex items-center justify-end gap-1">
                        {r.gider_pusulasi_no && (
                          editGpNo?.id === r.id ? (
                            <span className="inline-flex items-center gap-1">
                              <input autoFocus value={editGpNo.value} disabled={busyId === r.id}
                                onChange={(e) => setEditGpNo({ id: r.id, value: e.target.value })}
                                onKeyDown={(e) => { if (e.key === "Enter") saveGpNo(r); if (e.key === "Escape") setEditGpNo(null); }}
                                className="w-24 text-[11px] font-mono border border-purple-300 rounded px-1 py-0.5 focus:outline-none focus:ring-1 focus:ring-purple-300" />
                              <button onClick={() => saveGpNo(r)} disabled={busyId === r.id} className="text-green-600 hover:text-green-700" title="Kaydet"><CheckCircle size={14} /></button>
                              <button onClick={() => setEditGpNo(null)} className="text-gray-400 hover:text-gray-600" title="Vazgeç"><XCircle size={14} /></button>
                            </span>
                          ) : (
                            <span onClick={() => can("returns.expense_note") && setEditGpNo({ id: r.id, value: r.gider_pusulasi_no })}
                              className={`text-[11px] font-mono font-bold text-purple-700 px-1 ${can("returns.expense_note") ? "cursor-pointer hover:underline" : ""}`}
                              title={can("returns.expense_note") ? "Numarayı değiştirmek için tıkla" : "Gider Pusulası Takip No"}>
                              #{r.gider_pusulasi_no}
                            </span>
                          )
                        )}
                        {/* Gider pusulası YALNIZCA onaylanan iadelerde oluşturulur (talep/kargoda
                            aşamasında gösterilmez). Zaten pusulası olanlarda yeniden yazdırmak için kalır.
                            KATI KURAL: e-Fatura siparişinde gider pusulası düzenlenemez. */}
                        {r.is_efatura ? (
                          <span
                            title="Bu sipariş e-Fatura / kurumsal siparişidir — gider pusulası düzenlenemez. Müşteriden iade faturası alınmalıdır."
                            className="inline-flex items-center gap-1 px-2 py-1.5 rounded-lg bg-red-50 text-red-600 border border-red-200 text-[10px] font-bold cursor-not-allowed select-none">
                            <FileText size={13} /> e-Fatura
                          </span>
                        ) : can("returns.expense_note") &&
                          (r.return_is_approved || ["return_approved", "returned", "refunded", "partial_refunded"].includes(r.status) || r.has_gider_pusulasi) && (
                          <button onClick={() => handleSiteGider(r)} disabled={busyId === r.id}
                            className={`p-1.5 rounded-lg disabled:opacity-50 ${r.has_gider_pusulasi ? "bg-purple-100 text-purple-700 hover:bg-purple-200" : "bg-gray-100 text-gray-600 hover:bg-gray-200"}`} title="Gider Pusulası">
                            <FileText size={14} />
                          </button>
                        )}
                        <button onClick={() => setExpandedId(expandedId === r.id ? null : r.id)} className="text-gray-400 hover:text-gray-700 px-1" title="Detay">
                          {expandedId === r.id ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
                        </button>
                      </div>
                    </td>
                  </tr>
                  {expandedId === r.id && (
                    <tr className="bg-gray-50/60">
                      <td colSpan={13} className="px-4 py-3">
                        {/* Müşteri + sipariş özeti */}
                        <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-x-6 gap-y-1 text-xs text-gray-900 mb-3">
                          <span>Müşteri: <b className="text-gray-900">{r.customer_name}</b></span>
                          {(r.address || r.city || r.district) && (
                            <span className="lg:col-span-3">Adres: <b className="text-gray-900">{[r.address, r.district, r.city].filter(Boolean).join(", ")}</b></span>
                          )}
                          <span>Durum: <b className="text-gray-900">{lbl(r.status)}</b></span>
                          <span>Sipariş tutarı: <b className="text-gray-900">{fmtTL(r.total)}</b></span>
                          <span>Fatura: <b className="text-gray-900">{r.invoice_number || "—"}</b></span>
                          <span>İade Onay Tarihi: <b className="text-gray-900">{r.return_approved_at ? fmtDate(r.return_approved_at) : "—"}</b></span>
                          <span>İade Ödeme Tarihi: <b className="text-gray-900">{r.refund_paid_at ? fmtDate(r.refund_paid_at) : "—"}</b></span>
                          {r.coupon_code && <span>Kupon: <b className="text-gray-900">{r.coupon_code}</b></span>}
                        </div>

                        {/* İade talep edilen ürünler — tiklenebilir (kısmi işlem için kalem seçimi) */}
                        <div className="flex items-center justify-between mb-1">
                          <div className="text-[11px] font-semibold text-gray-700">İade talep edilen ürünler ({r.item_count} adet)</div>
                          {selCount(r.id) > 0 && <div className="text-[11px] font-semibold text-orange-600">{selCount(r.id)} kalem seçili</div>}
                        </div>
                        {(r.items || []).length > 0 ? (
                          <div className="space-y-1">
                            {r.items.map((it, i) => {
                              const sel = !!selItems[`${r.id}::${i}`];
                              // Onaylanmış iadede kutucuklar KİLİTLİ (yalnız muhasebe/admin "Düzenle" ile açar).
                              const locked = r.return_is_approved && !editRows[r.id];
                              return (
                                <label key={i} className={`flex items-center gap-3 text-xs text-gray-900 border rounded-md px-2.5 py-1.5 ${locked ? "cursor-default" : "cursor-pointer"} ${sel ? "bg-orange-50 border-orange-300" : "bg-white"}`}>
                                  <input type="checkbox" checked={sel} disabled={locked}
                                    onChange={() => !locked && toggleItem(r.id, i)} className="shrink-0" />
                                  {sel && r.return_is_approved && <span className="text-green-600 text-[10px] font-bold shrink-0" title="Bu kalem geçmişte onaylanmış">✓ onaylı</span>}
                                  <span className="truncate max-w-[260px]">{it.name || "—"}</span>
                                  <span className="whitespace-nowrap text-gray-500">{it.qty} ad. × {fmtTL(it.price)}</span>
                                  {(() => {
                                    // Sipariş-seviyesi indirim (kupon) kalemlere ORANSAL dağıtılır. TAKSİT
                                    // VADE FARKI da kaleme ORANSAL (net/total) yansıtılır (Kadir: 'kalemlere
                                    // vade farkını oran orantı göster') → gider pusulasıyla birebir.
                                    const g = (Number(it.qty) || 1) * (Number(it.price) || 0);
                                    const dr = (Number(r.subtotal) > 0 && Number(r.discount) > 0)
                                      ? Math.min(1, Number(r.discount) / Number(r.subtotal)) : 0;
                                    const dShare = g * dr;
                                    const netAfterDisc = g - dShare;
                                    // TABAN, backend ile BİREBİR AYNI olmalı (orders._compute_refund_breakdown
                                    // ve gider pusulası): ürünlerin NET toplamı = subtotal − indirim.
                                    // Eskiden r.total kullanılıyordu; kargo order.total'ın içinde olduğundan
                                    // kargolu siparişte pay küçük çıkıp panel ile onay modalı ayrışıyordu.
                                    const vadeBase = (Number(r.subtotal) > 0)
                                      ? Math.max(0, Number(r.subtotal) - (Number(r.discount) || 0))
                                      : Number(r.total) || 0;
                                    const vadeRatio = (Number(r.vade_farki) > 0 && vadeBase > 0)
                                      ? Number(r.vade_farki) / vadeBase : 0;
                                    const vadeShare = netAfterDisc * vadeRatio;
                                    const net = netAfterDisc + vadeShare;
                                    const hasDisc = dr > 0.0001, hasVade = vadeShare > 0.005;
                                    if (!hasDisc && !hasVade) return <span className="font-semibold whitespace-nowrap">{fmtTL(g)}</span>;
                                    return (
                                      <span className="whitespace-nowrap inline-flex items-center gap-2"
                                        title={`Brüt ${fmtTL(g)}${hasDisc ? ` · indirim −${fmtTL(dShare)}` : ""}${hasVade ? ` · vade farkı +${fmtTL(vadeShare)}` : ""} · net ${fmtTL(net)}`}>
                                        {hasDisc && <span className="text-gray-400 line-through">{fmtTL(g)}</span>}
                                        {hasDisc && <span className="text-orange-600">−{fmtTL(dShare)} <span className="text-[10px]">(%{(dr * 100).toFixed(0)})</span></span>}
                                        {hasVade && <span className="text-amber-600">+{fmtTL(vadeShare)} <span className="text-[10px]">vade</span></span>}
                                        {/* Taksitli siparişte kalem tutarı vade farkını İÇERİR — operatör
                                            "bu rakama vade farkı dahil mi?" diye tereddüt etmesin. */}
                                        <span className="font-semibold text-gray-900">{fmtTL(net)}</span>
                                        {hasVade && <span className="text-[10px] text-amber-700">vade farkı dahil</span>}
                                      </span>
                                    );
                                  })()}
                                  {it.barcode && <span className="text-gray-700">#{it.barcode}</span>}
                                  {it.size && <span className="text-gray-700 font-semibold whitespace-nowrap">{it.size}</span>}
                                  {it.color && <span className="text-gray-700">Renk: {it.color}</span>}
                                  <span className="flex-1" />
                                </label>
                              );
                            })}
                          </div>
                        ) : (
                          <div className="text-xs text-gray-400">Ürün kalemi yok.</div>
                        )}

                        {/* Seçili kalemlerin iade tutarı — İYZİCO'YA GİRİLECEK TUTAR.
                            KISMİ (bazı kalemler): KARGO HARİÇ (yalnız ürün neti). TAM (hepsi):
                            kargo DAHİL. Backend hesabıyla (product net × (1−indirim oranı)) birebir. */}
                        {selCount(r.id) > 0 && (() => {
                          // İndirim YALNIZ ürünlere (payda = ara toplam, kargo HARİÇ) — kargoya indirim
                          // uygulanmaz (kargo %20 KDV, ürün %10; matrah karışmasın). Backend ile aynı.
                          const _base = Number(r.subtotal);
                          const dr = (_base > 0 && Number(r.discount) > 0)
                            ? Math.min(1, Number(r.discount) / _base) : 0;
                          // TAKSİT vade farkı payı seçili kaleme ORANSAL eklenir.
                          // TABAN backend (_compute_refund_breakdown _net_base) VE ürün satırıyla BİREBİR:
                          // net = subtotal − indirim (kargo HARİÇ). Eskiden r.total (kargo DAHİL) idi →
                          // kargolu taksitli siparişte İade net tutarı ürün satırından/gerçek iadeden DÜŞÜK
                          // çıkıyordu (panel önizlemesi ile gerçekte iade edilen tutar ayrışması).
                          const _vadeBase = (Number(r.subtotal) > 0)
                            ? Math.max(0, Number(r.subtotal) - (Number(r.discount) || 0))
                            : Number(r.total) || 0;
                          const vadeRatio = (Number(r.vade_farki) > 0 && _vadeBase > 0)
                            ? Number(r.vade_farki) / _vadeBase : 0;
                          let selNet = 0, selN = 0;
                          (r.items || []).forEach((it, i) => {
                            if (!selItems[`${r.id}::${i}`]) return;
                            selN += 1;
                            selNet += ((Number(it.qty) || 1) * (Number(it.price) || 0)) * (1 - dr) * (1 + vadeRatio);
                          });
                          const totalItems = (r.items || []).length;
                          const isFullSel = totalItems > 0 && selN >= totalItems;
                          // ÜRÜN NETİ (kargo HARİÇ): tam iadede charged−kargo (vade farkı korunur),
                          // kısmi iadede seçili ürün netleri. KARGO AYRI gösterilir (Kadir: 'kargoyu
                          // ayrı göster'). Ödenmiş kargo (shipping_cost>0) tam iadede iade edilir; kargo
                          // müşteriden kesiliyse (cargoSel) DÜŞÜLÜR. Kısmi iadede kargo otomatik iade
                          // edilmez (yalnız 'kargoyu müşteriden kes' ile mahsup).
                          const _ship = Number(r.shipping_cost) || 0;
                          const productNet = isFullSel
                            ? Math.max(0, (Number(r.charged_total) || Number(r.total) || selNet) - _ship)
                            : selNet;
                          const _cargoDeduct = !!cargoSel[r.id];
                          const cargoRefund = (isFullSel && _ship > 0 && !_cargoDeduct) ? _ship : 0;
                          const cargoDeducted = _cargoDeduct ? _ship : 0;
                          const total = Math.max(0, productNet + cargoRefund - cargoDeducted);
                          return (
                            <div className="mt-2 text-xs font-semibold text-emerald-800 bg-emerald-50 border border-emerald-200 rounded-md px-3 py-1.5 inline-flex flex-wrap items-center gap-x-3 gap-y-0.5">
                              <span>İade net tutarı: <b>{fmtTL(total)}</b></span>
                              {(cargoRefund > 0 || cargoDeducted > 0) && (
                                <span className="text-[10px] font-normal text-gray-600">
                                  ürün {fmtTL(productNet)}
                                  {cargoRefund > 0 ? ` + kargo ${fmtTL(cargoRefund)}` : ""}
                                  {cargoDeducted > 0 ? ` − kargo ${fmtTL(cargoDeducted)} (müşteriden kesildi)` : ""}
                                </span>
                              )}
                              {Number(r.vade_farki) > 0 && (
                                /* Eski metin "gider pusulasında eklenir" diyordu; oysa vade farkı payı
                                   bu tutarın İÇİNDE. Yanlış anlaşılıp elle tekrar eklenmesin. */
                                <span className="text-[10px] font-normal text-amber-600">taksitli — vade farkı payı bu tutara dahildir</span>
                              )}
                            </div>
                          );
                        })()}

                        {/* Kargo bedeli — "kargoyu müşteriden KES (mahsup)". Kutu YALNIZ
                            ücretsiz-kargo limitini AŞAN (free shipping'den faydalanmış) siparişlerin
                            iadesinde görünür (Kadir isteği): kargo ödenmemiş (shipping_cost=0) VE
                            sipariş tutarı eşiği (ör. 4000 TL) aşmış olmalı. Kargosunu ödemiş / eşiğin
                            altındaki siparişlerde kutu HİÇ gösterilmez. Kısmi iade sonrası KALAN
                            tutar eşiğin altına düşerse KIRMIZI uyarı çıkar. */}
                        {(() => {
                          const threshold = Number(r.free_shipping_threshold) || Number(freeShipThreshold) || 0;
                          // Siparişin verildiği (ücretsiz kargoyu tetikleyen) sepet tutarı: KDV-dahil ara toplam
                          // (yoksa toplam). Ücretsiz kargo checkout'ta sepet tutarına göre uygulanır.
                          const orderAmount = Number(r.subtotal) || Number(r.total) || 0;
                          // KUTUYU GÖSTERME KOŞULU: eşik tanımlı + sipariş tutarı eşiği (4000) aşmış.
                          // NOT: 'paid' (kargo ödenmiş) siparişlerde de gösterilir — 4000+ bir sipariş
                          // kısmi iade sonrası 4000 altına düşerse operatör kargoyu kesip kesmeyeceğine
                          // karar verebilmeli (backend zaten fault=customer'da bu kesintiyi uygular).
                          const qualifiedFreeShip = threshold > 0 && orderAmount >= threshold;
                          if (!qualifiedFreeShip) return null;
                          // Kesilecek standart kargo ücreti (ayarlardan; ödenmiş siparişte de aynı fee).
                          const amt = Number(freeShipFee) || Number(r.shipping_cost) || 0;
                          const sel = !!cargoSel[r.id];
                          const locked = r.return_is_approved && !editRows[r.id];
                          // Kalan (iade sonrası tutulan) net tutar — seçili kalem netleri (indirim + taksit
                          // vade farkı payı DAHİL) → 'İade edilecek' kargo düşülünce doğru çıksın.
                          const _base = Number(r.subtotal);
                          const dr = (_base > 0 && Number(r.discount) > 0) ? Math.min(1, Number(r.discount) / _base) : 0;
                          // Vade farkı payı tabanı backend/ürün satırıyla BİREBİR: subtotal − indirim (kargo HARİÇ).
                          const _vadeBase = (_base > 0) ? Math.max(0, _base - (Number(r.discount) || 0)) : Number(r.total) || 0;
                          const vadeRatio = (Number(r.vade_farki) > 0 && _vadeBase > 0)
                            ? Number(r.vade_farki) / _vadeBase : 0;
                          let retNet = 0, anySel = false;
                          (r.items || []).forEach((it, i) => {
                            if (selItems[`${r.id}::${i}`]) { anySel = true; retNet += ((Number(it.qty) || 1) * (Number(it.price) || 0)) * (1 - dr) * (1 + vadeRatio); }
                          });
                          const orderNet = Number(r.total) || 0;
                          const keptNet = Math.max(0, orderNet - retNet);
                          // Uyarı: kısmi iade (kalan > 0) + kalan tutar eşiğin ALTINA düşmüş.
                          const belowThreshold = anySel && keptNet > 0.01 && keptNet < threshold;
                          return (
                            <div className="mt-1 space-y-1">
                              <label className={`inline-flex items-center gap-2 text-xs border rounded-md px-2.5 py-1.5 ${locked ? "cursor-default" : "cursor-pointer"} ${sel ? "bg-amber-50 border-amber-300 text-gray-900" : "bg-white text-gray-900"}`}>
                                <input type="checkbox" checked={sel} disabled={locked}
                                  onChange={() => !locked && toggleCargo(r.id)} className="shrink-0" />
                                <span className="font-medium whitespace-nowrap">Kargoyu müşteriden kes</span>
                                {amt > 0 && <span className={`font-semibold whitespace-nowrap ${sel ? "text-amber-700" : "text-gray-400"}`}>−{fmtTL(amt)}</span>}
                              </label>
                              {/* Kargo müşteriden kesildiğinde iade edilecek NET tutar: seçili ürün neti − kargo. */}
                              {sel && amt > 0 && anySel && (
                                <div className="block text-xs bg-emerald-50 border border-emerald-300 text-emerald-800 rounded-md px-2.5 py-1.5 font-bold max-w-2xl">
                                  İade edilecek: {fmtTL(Math.max(0, retNet - amt))} <span className="font-normal text-gray-600">({fmtTL(retNet)} − kargo {fmtTL(amt)})</span>
                                </div>
                              )}
                              {belowThreshold && !sel && (
                                <div className="block text-xs bg-red-50 border border-red-300 text-red-700 rounded-md px-2.5 py-1.5 font-semibold max-w-2xl">
                                  ⚠️ İade sonrası kalan tutar {fmtTL(keptNet)} — ücretsiz kargo eşiğinin ({fmtTL(threshold)}) ALTINA düşüyor. Müşteri ücretsiz kargo kampanyasının dışında kalıyor; <b>kargoyu müşteriden kesmeniz gerekebilir</b> (yukarıdaki kutuyu işaretleyin).
                                </div>
                              )}
                            </div>
                          );
                        })()}

                        {/* ONAY GEÇMİŞİ + DÜZENLE — onaylanmış iadelerde geçmiş seçimi göster;
                            değiştirme yalnız muhasebe/admin (returns.expense_note veya *). */}
                        {r.return_is_approved && (
                          <div className="mt-2 flex flex-wrap items-center gap-2 border-t pt-2">
                            <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded bg-green-100 text-green-700 text-[10px] font-bold border border-green-200">
                              ✓ Onaylanmış İade
                            </span>
                            <span className="text-[11px] text-gray-500">
                              {r.approved_full ? "Tüm kalemler onaylandı" : `${(r.approved_item_indexes || []).length} kalem onaylandı`}
                              {r.approved_cargo_deducted ? " · kargo müşteriden kesildi" : ""}
                              {r.approval_by ? ` · ${r.approval_by}` : ""}
                              {r.approval_at ? ` · ${fmtDate(r.approval_at)}` : ""}
                            </span>
                            <span className="flex-1" />
                            {canEditApproval() ? (
                              editRows[r.id] ? (
                                <>
                                  <button onClick={() => saveApprovalEdit(r)} disabled={busyId === r.id}
                                    className="inline-flex items-center gap-1 px-3 py-1 rounded-lg bg-green-600 text-white text-xs font-bold hover:bg-green-700 disabled:opacity-50">
                                    ✔ Düzenlemeyi Onayla
                                  </button>
                                  <button onClick={() => setEditRows((s) => { const n = { ...s }; delete n[r.id]; return n; })}
                                    className="inline-flex items-center gap-1 px-3 py-1 rounded-lg bg-white text-gray-600 border border-gray-300 text-xs font-bold hover:bg-gray-100">
                                    Vazgeç
                                  </button>
                                </>
                              ) : (
                                <button onClick={() => {
                                    // DÜZENLEME: seçimi SIFIRLA → kullanıcı yalnız istediği kalemi
                                    // (ve kargoyu) tiklesin; hesap YALNIZ tiklenenlere göre olur.
                                    // Aksi halde onaylı-tüm seed kutuları dolu kalıp "hepsi seçili"
                                    // sanılıyor, tek kalem seçilemiyordu (Kadir bug).
                                    setSelItems((s) => {
                                      const n = { ...s };
                                      Object.keys(n).forEach((k) => { if (k.startsWith(`${r.id}::`)) delete n[k]; });
                                      return n;
                                    });
                                    setCargoSel((s) => ({ ...s, [r.id]: false }));
                                    setSeededRows((s) => ({ ...s, [r.id]: true })); // yeniden seed etme
                                    setEditRows((s) => ({ ...s, [r.id]: true }));
                                  }}
                                  className="inline-flex items-center gap-1 px-3 py-1 rounded-lg bg-white text-gray-700 border border-gray-300 text-xs font-bold hover:bg-gray-100">
                                  ✏️ Düzenle
                                </button>
                              )
                            ) : (
                              <span className="text-[10px] text-gray-400 italic" title="Onay geçmişini değiştirme yetkisi yalnız muhasebe ve admin kullanıcılarındadır.">🔒 Değiştirme yetkisi: muhasebe/admin</span>
                            )}
                          </div>
                        )}

                        {/* İade kargo süreci: gelen iade barkodu/kodu + reddedilenlerde geri gönderim */}
                        {(r.return_code || r.return_barcode_url || r.cargo_tracking_number || r.reship_code || r.return_cargo_provider) && (
                          <div className="mt-2 text-[11px] text-gray-900 bg-white border rounded-md px-2.5 py-1.5 flex flex-wrap gap-x-5 gap-y-1 items-center">
                            {r.return_cargo_provider && <span>İade kargo: <b className="text-gray-900">{r.return_cargo_provider}</b></span>}
                            {r.return_code && <span>İade kodu: <b className="text-gray-900 font-mono">{r.return_code}</b></span>}
                            {r.return_barcode_url && (
                              <a href={r.return_barcode_url.startsWith("http") ? r.return_barcode_url : `${BACKEND}${r.return_barcode_url}`}
                                target="_blank" rel="noopener noreferrer" className="text-blue-600 hover:text-blue-800 underline">Kargo barkodu</a>
                            )}
                            {r.cargo_tracking_number && <span>Takip no: <b className="text-gray-900 font-mono">{r.cargo_tracking_number}</b></span>}
                            {r.reship_code && <span className="text-blue-700">Geri gönderim: <b className="font-mono">{r.reship_code}</b>{r.reshipped_at ? ` · ${fmtDate(r.reshipped_at)}` : ""}</span>}
                          </div>
                        )}

                        {/* Tutar dökümü */}
                        {(r.subtotal > 0 || r.shipping_cost > 0 || r.discount > 0) && (
                          <div className="mt-2 text-xs text-gray-900 flex flex-wrap gap-x-6 gap-y-1 justify-start">
                            {r.subtotal > 0 && <span>Ara toplam: <b className="text-gray-900">{fmtTL(r.subtotal)}</b></span>}
                            {r.shipping_cost > 0 && <span>Kargo: <b className="text-gray-900">{fmtTL(r.shipping_cost)}</b></span>}
                            {r.discount > 0 && <span>İndirim: <b className="text-gray-900">−{fmtTL(r.discount)}</b></span>}
                            <span>Genel toplam: <b className="text-gray-900">{fmtTL(r.total)}</b></span>
                          </div>
                        )}
                        {/* Taksit farkı: gerçekten tahsil edilen (iyzico paidPrice) genel toplamdan
                            yüksekse — tam iadede ONAY EKRANINDA bu tutar baz alınır, burada da
                            gösteriyoruz ki admin onaya girmeden önce gerçek iade tutarını görsün. */}
                        {r.vade_farki > 0 && (
                          <div className="mt-1 text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded-md px-2.5 py-1.5 inline-flex flex-wrap items-center gap-x-4 gap-y-0.5">
                            <span>Taksit farkı (+{r.installment} taksit): <b>+{fmtTL(r.vade_farki)}</b></span>
                            <span>Gerçek tahsilat — iade onayında baz alınacak tutar: <b>{fmtTL(r.charged_total)}</b></span>
                          </div>
                        )}

                        {/* İade/iptal durumu açıklaması */}
                        {(r.status === "partial_refunded" || r.status === "cancelled") && (
                          <div className="mt-2 text-[11px] text-gray-900 bg-white border rounded-md px-2.5 py-1.5">
                            {r.status === "cancelled" ? "Sipariş iptal edildi."
                              : "Kısmi iade yapıldı. (Kaynak sistem kalem bazında ayrım vermiyor; iade edilen tutar yukarıdaki tutar bilgisine yansır.)"}
                          </div>
                        )}
                        {wfCanAct && (() => {
                          // Bu iade ZATEN onaylandı mı? (durum onay/iade/refund VEYA onay damgası
                          // VEYA gider pusulası kesilmiş). Onaylıysa yeşil "İade Onay" tekrar bastırmaz;
                          // soluk/pasif "İade Onaylandı" gösterilir (karışıklık gitsin).
                          const rowApproved = ["return_approved", "returned", "refunded", "partial_refunded"].includes(r.status)
                            || !!r.return_approved_at || !!r.has_gider_pusulasi || !!r.gider_pusulasi_no;
                          return (
                          <div className="mt-3">
                            <div className="flex flex-wrap gap-2">
                              {can("returns.approve") && (rowApproved ? (
                                <span
                                  title="Bu iade zaten onaylanmış — tekrar onaylanamaz"
                                  className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-emerald-50 text-emerald-600 border border-emerald-200 text-xs font-semibold cursor-not-allowed opacity-70 select-none"
                                >
                                  <CheckCircle size={14} /> İade Onaylandı
                                </span>
                              ) : (
                                <button
                                  onClick={() => openWorkflow(r, "approve")}
                                  disabled={busyId === r.id}
                                  className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-emerald-600 text-white text-xs font-semibold hover:bg-emerald-700 disabled:opacity-60"
                                >
                                  <CheckCircle size={14} /> İade Onay{selCount(r.id) > 0 ? ` (${selCount(r.id)} kalem)` : ""}
                                </button>
                              ))}
                              {can("returns.reject") && (
                                <button
                                  onClick={() => openWorkflow(r, "reject")}
                                  disabled={busyId === r.id}
                                  className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-rose-600 text-white text-xs font-semibold hover:bg-rose-700 disabled:opacity-60"
                                >
                                  <XCircle size={14} /> İade Reddet
                                </button>
                              )}
                            </div>
                          </div>
                          );
                        })()}
                        {r.notes && <div className="mt-2 text-[11px] text-gray-700">Not: {r.notes}</div>}
                      </td>
                    </tr>
                  )}
                </Fragment>
              ))}
            </tbody>
          </table>
          {/* Sayfalama — sıralanmış TÜM liste üzerinden (client-side) */}
          {sortedRows.length > PER_PAGE && (
            <div className="flex items-center justify-between gap-3 px-3 py-3 border-t bg-gray-50 text-sm flex-wrap">
              <div className="text-gray-500">
                {(_cpage - 1) * PER_PAGE + 1}–{Math.min(_cpage * PER_PAGE, sortedRows.length)} / {sortedRows.length} iade
              </div>
              <div className="flex items-center gap-1">
                <button onClick={() => setCpage(1)} disabled={_cpage <= 1}
                  className="px-2.5 py-1 rounded border bg-white disabled:opacity-40 hover:bg-gray-100">« İlk</button>
                <button onClick={() => setCpage((p) => Math.max(1, p - 1))} disabled={_cpage <= 1}
                  className="px-2.5 py-1 rounded border bg-white disabled:opacity-40 hover:bg-gray-100">‹ Önceki</button>
                <span className="px-3 py-1 font-semibold text-gray-700">Sayfa {_cpage} / {pageCount}</span>
                <button onClick={() => setCpage((p) => Math.min(pageCount, p + 1))} disabled={_cpage >= pageCount}
                  className="px-2.5 py-1 rounded border bg-white disabled:opacity-40 hover:bg-gray-100">Sonraki ›</button>
                <button onClick={() => setCpage(pageCount)} disabled={_cpage >= pageCount}
                  className="px-2.5 py-1 rounded border bg-white disabled:opacity-40 hover:bg-gray-100">Son »</button>
              </div>
            </div>
          )}
        </div>
      )}

      {/* İade işlem akışı modal'ı (köprülenmiş customer_returns üzerinden) */}
      {wf && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" onClick={() => !wf.loading && setWf(null)}>
          <div className="bg-white rounded-2xl shadow-xl w-full max-w-lg max-h-[90vh] overflow-auto" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between px-5 py-3 border-b">
              <div>
                <div className="font-semibold text-gray-900">İade İşlemleri</div>
                <div className="text-xs text-gray-500">Sipariş {wf.row.order_number} · {wf.row.customer_name}</div>
              </div>
              <button onClick={() => setWf(null)} disabled={wf.loading} className="text-gray-400 hover:text-gray-700 text-sm">Kapat ✕</button>
            </div>
            <div className="p-5 space-y-4">
              <div className="text-xs text-gray-500">
                Mevcut iade durumu: <b className="text-gray-800">{lbl(wf.row.status)}</b>
              </div>

              {/* #12: KISMİ iadede kargo ücreti kararı — açık son onay (biz mi karşılıyoruz / müşteriye mi). */}
              {wf.returnedNet != null && (
                <div className="border border-amber-200 bg-amber-50 rounded-xl p-3">
                  <div className="text-xs font-semibold text-amber-900 mb-2">Kısmi iade — kargo ücreti kimde kalsın?</div>
                  <div className="flex gap-2">
                    <button type="button" onClick={() => changeCargoFault("store")} disabled={wf.loading}
                      className={`flex-1 px-3 py-2 rounded-lg text-xs font-semibold border transition-colors ${wf.fault === "store" ? "bg-emerald-600 text-white border-emerald-600" : "bg-white text-gray-700 border-gray-300 hover:border-emerald-400"}`}>
                      Kargoyu BİZ karşılıyoruz
                      <span className="block text-[10px] font-normal opacity-80">müşteriden kesme</span>
                    </button>
                    <button type="button" onClick={() => changeCargoFault("customer")} disabled={wf.loading}
                      className={`flex-1 px-3 py-2 rounded-lg text-xs font-semibold border transition-colors ${wf.fault === "customer" ? "bg-amber-600 text-white border-amber-600" : "bg-white text-gray-700 border-gray-300 hover:border-amber-400"}`}>
                      Kargoyu MÜŞTERİYE yansıt
                      <span className="block text-[10px] font-normal opacity-80">iade tutarından kes</span>
                    </button>
                  </div>
                </div>
              )}

              {wf.preview ? (
                <div className="border rounded-xl p-3 text-sm">
                  <div className="text-xs text-gray-500 mb-2">
                    Kargo: {wf.fault === "customer" ? <b className="text-amber-700">müşteriden kesildi</b> : <b className="text-gray-700">mağazadan (tam iade)</b>}
                  </div>
                  <div className="space-y-1 text-xs text-gray-600">
                    {/* Eskiden burada TÜM SİPARİŞİN vade farkı (ör. 2 ürün için +169,98) ayrı satır
                        olarak yazılıyordu; oysa kısmi iadede yalnız SEÇİLİ ürün iade ediliyor →
                        operatör "neden 2 ürünün taksit farkını görüyorum?" diye tereddüt ediyordu.
                        Satır kaldırıldı: zaten aşağıdaki "İade edilen ürün tutarı" bu iadeye DÜŞEN
                        vade farkı payını içeriyor (returned_net = ürün neti + vade farkı payı).
                        Taksitli siparişte etiket "(taksit dahil)" ile açıkça belirtilir. */}
                    <div className="flex justify-between">
                      <span>
                        İade edilen ürün tutarı
                        {wf.preview.vade_farki_refunded > 0 && (
                          <span className="text-emerald-700"> (taksit dahil{wf.preview.installment > 1 ? ` · ${wf.preview.installment} taksit` : ""})</span>
                        )}
                      </span>
                      <b>{fmtTL(wf.preview.returned_net)}</b>
                    </div>
                    {wf.preview.campaign_deduction > 0 && <div className="flex justify-between text-amber-700"><span>Kargo bedeli (müşteriden tahsil)</span><b>− {fmtTL(wf.preview.campaign_deduction)}</b></div>}
                    {wf.preview.return_cargo_fee > 0 && <div className="flex justify-between text-amber-700"><span>İade kargo bedeli</span><b>− {fmtTL(wf.preview.return_cargo_fee)}</b></div>}
                    <div className="flex justify-between pt-1 border-t mt-1 text-gray-900"><span>Otomatik iade tutarı</span><b>{fmtTL(wf.preview.auto_refund)}</b></div>
                  </div>
                  {wf.preview.campaign_note && <div className="text-[11px] text-amber-600 mt-1">{wf.preview.campaign_note}</div>}
                  <div className="flex items-center gap-2 mt-2">
                    <span className="text-xs text-gray-500">İade tutarı:</span>
                    <input type="number" value={wf.finalAmount}
                      onChange={(e) => setWf((m) => ({ ...m, finalAmount: e.target.value, edited: true }))}
                      className="w-32 text-sm border rounded-md px-2 py-1" />
                    <span className="text-xs text-gray-400">TL {wf.edited && "(elle)"}</span>
                  </div>
                </div>
              ) : (
                <div className="text-xs text-gray-400">Tutar önizlemesi alınamadı. Onayda tutarı elle girebilirsin.</div>
              )}

              <div className="flex flex-wrap items-center gap-2">
                {wfDecided ? (
                  <>
                    <button disabled title="Bu iade karara bağlanmış"
                      className={`inline-flex items-center gap-1 px-3 py-1.5 rounded-lg text-xs font-semibold cursor-not-allowed ${wfDecided === "approved" ? "bg-emerald-600 text-white opacity-80" : "bg-gray-200 text-gray-400 opacity-60"}`}>
                      {wfDecided === "approved" && <CheckCircle size={14} />} {wfDecided === "approved" ? "Onaylandı" : "Onayla"}
                    </button>
                    <button disabled title="Bu iade karara bağlanmış"
                      className={`inline-flex items-center gap-1 px-3 py-1.5 rounded-lg text-xs font-semibold cursor-not-allowed ${wfDecided === "rejected" ? "bg-rose-600 text-white opacity-80" : "bg-gray-200 text-gray-400 opacity-60"}`}>
                      {wfDecided === "rejected" && <XCircle size={14} />} {wfDecided === "rejected" ? "Reddedildi" : "Reddet"}
                    </button>
                    <span className="text-[11px] text-gray-500">Bu iade {wfDecided === "approved" ? "onaylanmış" : "reddedilmiş"}; tekrar işlem yapılamaz.</span>
                  </>
                ) : (
                  <>
                    {can("returns.approve") && (
                      <button onClick={wfApprove} disabled={wf.loading}
                        className="inline-flex items-center gap-1 px-3 py-1.5 rounded-lg bg-emerald-600 text-white text-xs font-semibold hover:bg-emerald-700 disabled:opacity-50">Onayla</button>
                    )}
                    {can("returns.reject") && (
                      <button onClick={() => setWf((m) => ({ ...m, showReject: !m.showReject }))} disabled={wf.loading}
                        className="inline-flex items-center gap-1 px-3 py-1.5 rounded-lg bg-rose-600 text-white text-xs font-semibold hover:bg-rose-700 disabled:opacity-50">Reddet</button>
                    )}
                  </>
                )}
              </div>

              {wf.showReject && !wfDecided && (
                <div className="border rounded-xl p-3 bg-rose-50/40">
                  <label className="text-xs text-gray-600">Ret sebebi (müşteriye SMS/mail ile gider)</label>
                  <textarea value={wf.rejectReason} onChange={(e) => setWf((m) => ({ ...m, rejectReason: e.target.value }))}
                    rows={2} className="w-full mt-1 text-sm border rounded-md px-2 py-1" placeholder="Örn. ürün kullanılmış / etiketi yok…" />
                  <label className="flex items-center gap-2 mt-2 text-xs text-gray-600">
                    <input type="checkbox" checked={wf.reship} onChange={(e) => setWf((m) => ({ ...m, reship: e.target.checked }))} />
                    Ürünü müşteriye geri gönder (yeni kargo barkodu üret)
                  </label>
                  <button onClick={wfReject} disabled={wf.loading}
                    className="mt-2 inline-flex items-center gap-1 px-3 py-1.5 rounded-lg bg-rose-600 text-white text-xs font-semibold hover:bg-rose-700 disabled:opacity-50">Reddi Onayla</button>
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
