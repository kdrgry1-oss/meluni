/**
 * =============================================================================
 * Orders.jsx — Admin Sipariş Yönetim Sayfası
 * =============================================================================
 *
 * NE İŞE YARAR?
 *   Tüm kanallardan (web site, Trendyol, Hepsiburada, Temu) gelen siparişlerin
 *   listelenmesi, filtrelenmesi, detaylarının görüntülenmesi, durumlarının
 *   güncellenmesi (onay, hazırlık, kargo, teslim, iptal), kargo çıkışı,
 *   fatura yüklenmesi, Trendyol'dan manuel içe aktarım ve toplu işlemlerin
 *   yapıldığı ana admin ekranıdır.
 *
 * BAĞLANTILI BACKEND UÇLARI:
 *   - GET  /api/orders                     → Listeleme (filtre, sayfa)
 *   - GET  /api/orders/{id}                → Detay
 *   - PUT  /api/orders/{id}/status         → Durum güncelleme
 *   - POST /api/orders/{id}/ship           → Kargoya verme
 *   - POST /api/orders/bulk                → Toplu durum değiştirme
 *   - POST /api/trendyol/import            → Trendyol manuel içe aktarım
 *   - GET  /api/orders/{id}/attribution    → Sipariş kaynağı/funnel
 *
 * BAĞLANTILI MODÜLLER:
 *   - /app/frontend/src/lib/attribution.js → UTM toplanan verileri gösterme.
 *   - components/admin/Pagination.jsx      → Üst/alt sayfalama bileşeni.
 *   - Returns.jsx                          → İade başlatma (sipariş detayından).
 *   - Campaigns.jsx                        → İade/iptal'de kampanya oransal
 *                                            hesabı (P1 backlog).
 * =============================================================================
 */
import { useState, useEffect, useRef } from "react";
import { FolderOpen, RefreshCw, Printer, FileText, FileCheck, MessageSquare, Package, Truck, Tag, CheckSquare, Square, Trash2, Filter, Search, ExternalLink } from "lucide-react";
import JourneyFunnel from "./JourneyFunnel";
import axios from "axios";
import OrderEventsLog from "../../components/admin/OrderEventsLog";
import MultiSelect from "../../components/admin/MultiSelect";
import OrderMarketplaceInfo from "../../components/admin/OrderMarketplaceInfo";
import OrderPaymentDetail from "../../components/admin/OrderPaymentDetail";
import { toast } from "sonner";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "../../components/ui/dialog";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "../../components/ui/select";
import Pagination from "../../components/admin/Pagination";

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;

const statusOptions = [
  { value: "pending", label: "Onay Bekliyor", class: "status-pending" },
  { value: "awaiting_payment", label: "Ödeme Bekleniyor (Havale)", class: "status-pending" },
  { value: "payment_notified", label: "Ödeme Bildirimi Alındı", class: "status-pending" },
  { value: "confirmed", label: "Onaylandı", class: "status-confirmed" },
  { value: "preparing", label: "Hazırlanıyor", class: "status-preparing" },
  { value: "processing", label: "İşleme Alındı", class: "status-preparing" },
  { value: "ready_to_ship", label: "Kargoya Hazır", class: "status-preparing" },
  { value: "shipped", label: "Kargoya Verildi", class: "status-shipped" },
  { value: "in_transit", label: "Taşınıyor", class: "status-shipped" },
  { value: "out_for_delivery", label: "Dağıtımda", class: "status-shipped" },
  { value: "delivered", label: "Teslim Edildi", class: "status-delivered" },
  { value: "undelivered", label: "Teslim Edilemedi (Şubede)", class: "status-undelivered" },
  { value: "return_requested", label: "İade Talebi Oluşturuldu", class: "status-undelivered" },
  { value: "return_in_transit", label: "İade Kargoda", class: "status-shipped" },
  { value: "partial_refunded", label: "Kısmi İade", class: "status-undelivered" },
  { value: "returned", label: "İade Tamamlandı", class: "status-cancelled" },
  { value: "refunded", label: "İade Bedeli Ödendi", class: "status-cancelled" },
  { value: "cancel_requested", label: "İptal Talebi Alındı (İade Bekliyor)", class: "status-pending" },
  { value: "cancelled", label: "İptal Edildi", class: "status-cancelled" },
  { value: "cancel_refunded", label: "Ödemeli İptal Onaylandı", class: "status-cancelled" },
  // Ödemesi HİÇ alınamamış (başarısız kart) sipariş — para alınmadığı için İADE GEREKMEZ.
  // "İptal Edildi"den AYRI: ekip bunu "ödeme alınmıştı" sanmasın.
  { value: "payment_failed", label: "Ödeme Alınamadı (para alınmadı)", class: "status-undelivered" },
];

const cargoCompanies = [
  { value: "MNG", label: "DHL E-Commerce" },
  { value: "DHL", label: "DHL" },
  { value: "YURTICI", label: "Yurtiçi Kargo" },
  { value: "ARAS", label: "Aras Kargo" },
  { value: "PTT", label: "PTT Kargo" },
];

const ORDERS_VIEW_KEY = "facette_orders_view";
const _loadOrdersView = () => { try { return JSON.parse(localStorage.getItem(ORDERS_VIEW_KEY) || "{}") || {}; } catch (e) { return {}; } };

export default function AdminOrders({ unpaidView = false }) {
  const [orders, setOrders] = useState([]);
  const [riskMap, setRiskMap] = useState({});  // FAZ 6 — müşteri risk skorları
  const [loading, setLoading] = useState(true);
  const [page, setPage] = useState(() => _loadOrdersView().page || 1);
  const [pageSize, setPageSize] = useState(() => _loadOrdersView().pageSize || 20);
  const [total, setTotal] = useState(0);
  // Varsayılan görünümde gizlenen iade/iptal sayıları — ilgili sayfalara linkle gösterilir
  const [hiddenSummary, setHiddenSummary] = useState(null);
  // --- Gelişmiş Filtreler (kullanıcı talebiyle geri eklendi) ---
  const [advancedFiltersOpen, setAdvancedFiltersOpen] = useState(false);
  // Sayfa yenilenince TÜM arama/filtre alanları sıfırlanır (kalıcılık YOK) —
  // kullanıcı talebi: arama o an uygulanır, yenilemede temiz liste gelir.
  const [filters, setFilters] = useState({
    search: "", phone: "", email: "", order_number: "",
    cargo_tracking: "", invoice_number: "", coupon_code: "",
    start_date: "", end_date: "",
    payment_method: "", payment_status: "", platform: "", channel: "",
    status: "",
    influencer: "", is_corporate: ""
  });
  const [searchTick, setSearchTick] = useState(0);
  const applyFilters = () => { setPage(1); setSearchTick((t) => t + 1); };
  const onFilterKey = (e) => { if (e.key === "Enter") { e.preventDefault(); applyFilters(); } };
  // Genel arama "debounce": kullanıcı yazmayı ~350ms bırakınca arama OTOMATİK
  // uygulanır — "Ara" butonuna basmaya gerek kalmaz. İlk render'da tetiklenmez
  // (mount'ta fetchOrders zaten çalışıyor; çift istek atılmasın).
  const _searchInit = useRef(true);
  useEffect(() => {
    if (_searchInit.current) { _searchInit.current = false; return; }
    const _t = setTimeout(() => { setPage(1); setSearchTick((t) => t + 1); }, 350);
    return () => clearTimeout(_t);
  }, [filters.search]);
  const _ordReqSeq = useRef(0); // yarış koruması: yalnız EN SON isteğin yanıtı uygulanır
  const [activeStatusKeys, setActiveStatusKeys] = useState([]); // Ayarlar > Siparis Durumlari "Gorunur"
  const [selectedOrder, setSelectedOrder] = useState(null);
  const [detailOpen, setDetailOpen] = useState(false);
  const [selectedOrders, setSelectedOrders] = useState([]);
  const [bulkDeleting, setBulkDeleting] = useState(false);
  const [editMode, setEditMode] = useState(false);
  const [editData, setEditData] = useState(null);
  const [bulkAction, setBulkAction] = useState("");
  const [selectedCargo, setSelectedCargo] = useState("MNG");
  const [shipModalOpen, setShipModalOpen] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [cargoTrackingNumbers, setCargoTrackingNumbers] = useState({});

  const [shipOrderId, setShipOrderId] = useState(null);
  const [trackingNumber, setTrackingNumber] = useState("");

  // Trendyol Invoice Upload State
  const [invoicingId, setInvoicingId] = useState(null); // fatura kesimi uçuşta olan sipariş (çift tıklama koruması)

  // FAZ 1 B3 - Order Note Modal
  const [noteModalOpen, setNoteModalOpen] = useState(false);
  const [noteTargetOrder, setNoteTargetOrder] = useState(null);
  const [noteText, setNoteText] = useState("");
  const [giftModalOrder, setGiftModalOrder] = useState(null); // 🎁 ayrı modal — notlarla karışmasın
  const [savingNote, setSavingNote] = useState(false);

  // Manuel (kısmi) iade pop-up — sipariş panelinde iade durumu seçilince açılır
  const [returnModal, setReturnModal] = useState({ open: false, order: null, status: "returned", selected: {}, reason: "", busy: false });

  const openNoteModal = (order) => {
    setNoteTargetOrder(order);
    setNoteText("");
    setNoteModalOpen(true);
  };

  const saveNote = async () => {
    if (!noteTargetOrder || !noteText.trim()) return;
    setSavingNote(true);
    try {
      const token = localStorage.getItem('token');
      await axios.post(`${API}/orders/${noteTargetOrder.id}/note`,
        { note: noteText.trim() },
        { headers: { Authorization: `Bearer ${token}` } }
      );
      toast.success("Not eklendi");
      setNoteModalOpen(false);
      setNoteText("");
      fetchOrders();
    } catch (err) {
      toast.error(err.response?.data?.detail || "Not eklenemedi");
    } finally {
      setSavingNote(false);
    }
  };

  useEffect(() => {
    fetchOrders();
  }, [page, pageSize, unpaidView, searchTick]);

  // Görünüm kalıcılığı: yenilemede YALNIZ sayfa + sayfa boyutu korunur.
  // NOT: arama ve gelişmiş filtreler BİLİNÇLİ olarak saklanmaz → yenilemede sıfırlanır.
  useEffect(() => {
    try { localStorage.setItem(ORDERS_VIEW_KEY, JSON.stringify({ page, pageSize })); } catch (e) {}
  }, [page, pageSize]);

  /**
   * fetchOrders — Siparişleri arka uçtan çeker.
   *
   * TETİKLEYİCİ: useEffect([page, pageSize, unpaidView]) ile otomatik.
   *              Üst ve alt Pagination componentleri `onChange={setPage}` ile
   *              bu state'i günceller.
   * BACKEND    : GET /api/orders?page=&limit=20&status=&...
   * BESLER     : orders listesi + total (Pagination sayfa hesabı için).
   */
  const fetchOrders = async () => {
    const _seq = ++_ordReqSeq.current; // bu çağrının sıra numarası
    setLoading(true);
    try {
      const token = localStorage.getItem('token');
      // Liste varsayilan olarak kapali statuler haric; aktif gelismis filtre varsa tum siparislerde arar.
      let url = `${API}/orders?page=${page}&limit=${pageSize}&payment_view=${unpaidView ? "unpaid" : "valid"}`;
      const anyFilter = Object.values(filters).some((v) => v);
      if (!anyFilter) url += `&hide_closed=1`;
      Object.keys(filters).forEach((key) => { if (filters[key]) url += `&${key}=${encodeURIComponent(filters[key])}`; });
      const res = await axios.get(url, { headers: { Authorization: `Bearer ${token}` } });
      if (_seq !== _ordReqSeq.current) return; // eski yanıt — daha yeni bir istek yolda, yok say
      const list = res.data?.orders || [];
      setOrders(list);
      setTotal(res.data?.total || 0);
      setHiddenSummary(res.data?.hidden_summary || null);
      setLoading(false);   // liste geldi → spinner'ı hemen kaldır; risk skorları arka planda yüklenir

      // FAZ 6 — Yüksek iade oranlı müşterileri ARKA PLANDA çek (sayfa yüklemesini bloklamaz)
      const uids = [...new Set(list.map((o) => o.user_id).filter(Boolean))].slice(0, 100);
      const mails = [...new Set(list.map((o) => o.shipping_address?.email).filter(Boolean))].slice(0, 100);
      if (uids.length || mails.length) {
        axios.get(`${API}/customer-risk/bulk`, {
          params: { user_ids: uids.join(","), emails: mails.join(",") },
          headers: { Authorization: `Bearer ${token}` },
        }).then((r) => setRiskMap(r.data?.risks || {})).catch(() => { /* risk skoru opsiyonel */ });
      }
    } catch (err) {
      if (_seq !== _ordReqSeq.current) return; // eski isteğin hatası — spinner'a dokunma
      console.error(err);
      setLoading(false);
    }
  };

  /**
   * handleStatusChange — Tek bir siparişin durumunu değiştirir (pending→confirmed vs).
   *   Backend tarafında durum akışı doğrulanır (örn: delivered'dan pending'e dönüş
   *   engellenir). Güncelleme sonrası liste ve (açıksa) detay modal tazelenir.
   */
  const handleStatusChange = async (orderId, newStatus) => {
    // İade durumları → kalem seçimli pop-up (kısmi iade akışı); diğer durumlar direkt değişir
    if (newStatus === "returned" || newStatus === "refunded") {
      const ord = orders.find((o) => o.id === orderId) || (selectedOrder?.id === orderId ? selectedOrder : null);
      if (ord) { openReturnModal(ord, newStatus); return; }
    }
    try {
      const token = localStorage.getItem('token');
      await axios.put(`${API}/orders/${orderId}/status?status=${newStatus}`, {}, {
        headers: { Authorization: `Bearer ${token}` }
      });
      toast.success("Sipariş durumu güncellendi");
      fetchOrders();
      if (selectedOrder?.id === orderId) {
        setSelectedOrder({ ...selectedOrder, status: newStatus });
      }
    } catch (err) {
      toast.error("Güncelleme başarısız");
    }
  };

  // ── Manuel kısmi iade ───────────────────────────────────────────────
  const openReturnModal = (order, status) => {
    const items = order.items || [];
    const sel = {};
    items.forEach((_, i) => { sel[i] = true; }); // varsayılan: tüm kalemler seçili (tam iade)
    setReturnModal({ open: true, order, status, selected: sel, reason: "", busy: false });
  };
  const toggleReturnItem = (i) => {
    setReturnModal((m) => ({ ...m, selected: { ...m.selected, [i]: !m.selected[i] } }));
  };
  const submitReturn = async () => {
    const m = returnModal;
    if (!m.order) return;
    const idx = Object.keys(m.selected).filter((k) => m.selected[k]).map((k) => parseInt(k, 10));
    if (!idx.length) { toast.error("En az bir kalem seçin"); return; }
    setReturnModal((s) => ({ ...s, busy: true }));
    try {
      const token = localStorage.getItem('token');
      const res = await axios.post(`${API}/orders/${m.order.id}/admin-return`,
        { item_indexes: idx, reason: m.reason, target_status: m.status },
        { headers: { Authorization: `Bearer ${token}` } });
      toast.success(res.data?.is_full
        ? "Sipariş tamamen iade alındı"
        : `Kısmi iade: ${res.data?.items_count} kalem (sipariş açık kaldı)`);
      setReturnModal({ open: false, order: null, status: "returned", selected: {}, reason: "", busy: false });
      fetchOrders();
    } catch (err) {
      toast.error(err.response?.data?.detail || "İade işlenemedi");
      setReturnModal((s) => ({ ...s, busy: false }));
    }
  };

  /**
   * handleGenerateInvoice — Seçili sipariş için e-Arşiv fatura oluşturur.
   *   BACKEND: POST /api/orders/{id}/create-invoice?invoice_type=e-arsiv
   *   Başarılı olursa `invoice_number` dönerek satırda görünür; fatura
   *   oluşturulmuş siparişler tabloda opaklık azaltılarak pasifleştirilir.
   */
  const handleGenerateInvoice = async (orderId) => {
    if (invoicingId) return;            // başka bir fatura işlemi sürüyor → çift tıklamayı yut
    setInvoicingId(orderId);
    try {
      const token = localStorage.getItem('token');
      const res = await axios.post(`${API}/orders/${orderId}/create-invoice?invoice_type=e-arsiv`, {}, {
        headers: { Authorization: `Bearer ${token}` }
      });
      if (res.data.success) {
        toast.success(`Fatura oluşturuldu: ${res.data.invoice_number}`);
        fetchOrders();
      } else {
        toast.error(res.data.message || "Fatura oluşturulamadı");
      }
    } catch (err) {
      toast.error(err.response?.data?.detail || "Fatura oluşturulamadı");
    } finally {
      setInvoicingId(null);
    }
  };

  const handleResetInvoice = async (orderId) => {
    if (!window.confirm("Bu siparişin fatura kaydı panelden silinecek ve yeniden kesilebilir hale gelecek.\n(Doğan'daki gerçek fatura iptal edilmez.)\n\nDevam edilsin mi?")) return;
    try {
      const token = localStorage.getItem('token');
      const res = await axios.post(`${API}/orders/${orderId}/reset-invoice`, {}, {
        headers: { Authorization: `Bearer ${token}` }
      });
      if (res.data.success) {
        toast.success(res.data.message || "Fatura kaydı sıfırlandı");
        setSelectedOrder(null);
        fetchOrders();
      } else {
        toast.error(res.data.message || "Sıfırlanamadı");
      }
    } catch (err) {
      toast.error(err.response?.data?.detail || "Sıfırlanamadı");
    }
  };

  const [deletingId, setDeletingId] = useState("");
  const handleDeleteOrder = async (orderId) => {
    const label = selectedOrder?.order_number ? `\n\nSipariş: ${selectedOrder.order_number}` : "";
    if (!window.confirm(`Bu sipariş silinecek.${label}\n\nSilinen siparişler "Silinen Siparişler" sayfasına taşınır ve oradan geri alınabilir.\n\nDevam edilsin mi?`)) return;
    setDeletingId(orderId);
    try {
      const token = localStorage.getItem("token");
      await axios.delete(`${API}/orders/${orderId}`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      toast.success("Sipariş silindi — Silinen Siparişler sayfasına taşındı");
      setSelectedOrder(null);
      fetchOrders();
    } catch (err) {
      toast.error(err.response?.data?.detail || "Silinemedi");
    } finally {
      setDeletingId("");
    }
  };

  // eslint-disable-next-line no-unused-vars -- per-row buton kaldırıldı (Toplu Fatura Yazdır kullanılır); detay/ileride kullanılabilir
  const handlePrintInvoice = async (orderId) => {
    const token = localStorage.getItem('token');
    window.open(`${API}/orders/${orderId}/invoice/print?token=${token}`, '_blank');
  };

  const handleGenerateCargoBarcode = async (orderId, company = selectedCargo) => {
    try {
      const token = localStorage.getItem('token');
      const res = await axios.post(`${API}/orders/${orderId}/cargo-barcode?cargo_company=${company}`, {}, {
        headers: { Authorization: `Bearer ${token}` }
      });
      toast.success(`Kargo barkodu: ${res.data.tracking_number}`);
      fetchOrders();
    } catch (err) {
      toast.error("Kargo barkodu oluşturulamadı");
    }
  };

  const openShipModal = (orderId) => {
    setShipOrderId(orderId);
    setTrackingNumber("");
    setShipModalOpen(true);
  };



  /**
   * handleShipOrder — Siparişi "Kargoda" durumuna alıp kargo takip numarasını
   *   kaydeder. Seçilen kargo firması `selectedCargo` state'indendir. Başarı
   *   sonrası liste tazelenir; müşteriye SMS göndermek için ayrı
   *   handleSendShippingSMS kullanılır (canlı API gerektirir).
   */
  const handleShipOrder = async () => {
    if (!trackingNumber.trim()) {
      toast.error("Lütfen takip numarası giriniz");
      return;
    }
    try {
      const token = localStorage.getItem('token');
      const res = await axios.post(
        `${API}/orders/${shipOrderId}/ship?cargo_company=${selectedCargo}&tracking_number=${trackingNumber}`,
        {},
        { headers: { Authorization: `Bearer ${token}` } }
      );
      toast.success(res.data.message || "Sipariş kargoya verildi");
      setShipModalOpen(false);
      fetchOrders();
    } catch (err) {
      toast.error("Kargo işlemi başarısız");
    }
  };

  const handleCreateMngShipment = async (orderId) => {
    try {
      const token = localStorage.getItem('token');
      const res = await axios.post(
        `${API}/orders/${orderId}/create-mng-shipment`,
        {},
        { headers: { Authorization: `Bearer ${token}` } }
      );
      toast.success(res.data.message || `DHL E-Commerce kargo barkodu: ${res.data.tracking_number}`);
      fetchOrders();
    } catch (err) {
      toast.error(err.response?.data?.detail || "DHL E-Commerce oluşturulamadı");
    }
  };


  const handlePrintLabel = (orderId) => {
    const token = localStorage.getItem('token');
    const labelUrl = `${API}/orders/${orderId}/cargo-label?token=${token}`;
    const printWindow = window.open(labelUrl, '_blank', 'width=400,height=600');
    if (printWindow) {
      printWindow.onload = () => {
        setTimeout(() => {
          printWindow.print();
        }, 500);
      };
    }
    // Etiket çekimi backend'de "yazdırıldı" damgası bırakır → listeyi tazele (kamyon sarı→yeşil).
    setTimeout(() => fetchOrders(), 1500);
  };

  const handleTrendyolPrintLabel = async (cargoTrackingNumber) => {
    try {
      const token = localStorage.getItem('token');
      const res = await axios.get(`${API}/integrations/trendyol/orders/label/${cargoTrackingNumber}`, {
        headers: { Authorization: `Bearer ${token}` },
        responseType: 'blob'
      });
      const url = window.URL.createObjectURL(new Blob([res.data], { type: 'application/pdf' }));
      window.open(url, '_blank');
    } catch (err) {
      toast.error("Trendyol kargo etiketi alınamadı");
    }
  };


  const handleSendConfirmationSMS = async (orderId) => {
    try {
      const token = localStorage.getItem('token');
      const res = await axios.post(
        `${API}/orders/${orderId}/send-confirmation-sms`,
        {},
        { headers: { Authorization: `Bearer ${token}` } }
      );
      if (res.data.success) {
        toast.success("Sipariş onay SMS'i gönderildi");
      } else {
        toast.error(res.data.error || "SMS gönderilemedi");
      }
    } catch (err) {
      toast.error("SMS gönderilemedi: " + (err.response?.data?.detail || err.message));
    }
  };

  const handleSendShippingSMS = async (orderId) => {
    try {
      const token = localStorage.getItem('token');
      const res = await axios.post(
        `${API}/orders/${orderId}/send-shipping-sms`,
        {},
        { headers: { Authorization: `Bearer ${token}` } }
      );
      if (res.data.success) {
        toast.success("Kargo SMS'i gönderildi");
      } else {
        toast.error(res.data.error || "SMS gönderilemedi");
      }
    } catch (err) {
      toast.error("SMS gönderilemedi: " + (err.response?.data?.detail || err.message));
    }
  };

  const handleBulkDelete = async () => {
    if (selectedOrders.length === 0) {
      toast.error("Lütfen en az bir sipariş seçin");
      return;
    }
    if (!await window.appConfirm(`${selectedOrders.length} sipariş silinecek.\n\nSilinen siparişler "Silinen Siparişler" sayfasına taşınır ve oradan geri alınabilir.\n\nDevam edilsin mi?`)) return;
    setBulkDeleting(true);
    try {
      const token = localStorage.getItem("token");
      const results = await Promise.allSettled(
        selectedOrders.map((id) =>
          axios.delete(`${API}/orders/${id}`, { headers: { Authorization: `Bearer ${token}` } })
        )
      );
      const ok = results.filter((r) => r.status === "fulfilled").length;
      const fail = results.length - ok;
      if (ok) toast.success(`${ok} sipariş silindi — Silinen Siparişler sayfasına taşındı`);
      if (fail) toast.error(`${fail} sipariş silinemedi`);
      setSelectedOrders([]);
      fetchOrders();
    } catch (err) {
      toast.error("Toplu silme başarısız: " + (err.response?.data?.detail || err.message));
    } finally {
      setBulkDeleting(false);
    }
  };

  const handleBulkCargoBarcode = async () => {
    if (selectedOrders.length === 0) {
      toast.error("Lütfen sipariş seçiniz");
      return;
    }
    const t = toast.loading("Toplu kargo barkodu oluşturuluyor...");
    try {
      const token = localStorage.getItem('token');
      const res = await axios.post(`${API}/orders/bulk-cargo-barcode?cargo_company=${selectedCargo}`,
        selectedOrders,
        { headers: { Authorization: `Bearer ${token}` } }
      );
      toast.dismiss(t);
      const ok = res.data.success_count || 0;
      const err = res.data.error_count || 0;
      if (ok > 0 && err === 0) {
        toast.success(`${ok} sipariş için kargo barkodu oluşturuldu`);
      } else if (ok > 0 && err > 0) {
        toast.success(`${ok} oluşturuldu, ${err} başarısız`);
      } else {
        const firstErr = res.data.errors?.[0]?.error;
        toast.error(firstErr ? `Oluşturulamadı: ${firstErr}` : "Hiçbir barkod oluşturulamadı");
      }
      setSelectedOrders([]);
      fetchOrders();
    } catch (err) {
      toast.dismiss(t);
      toast.error(err.response?.data?.detail || "Toplu barkod oluşturulamadı");
    }
  };

  /**
   * handleBulkPrintCargoLabels — Seçili siparişlerin kargo barkodu ETİKETLERİNİ tek
   *   yazdırılabilir pencerede birleştirir. Her etiket /cargo-label'dan HTML olarak
   *   alınır; ilk etiketin head'i (Google Fonts + stil + @page 100x120) bir kez
   *   kullanılır, her etiketin body'si ayrı sayfa (page-break) olarak eklenir. Her
   *   etiketteki tekil fit-script'i çıkarılıp tüm '.barcode'ları sığdıran tek script konur.
   */
  const handleBulkPrintCargoLabels = async () => {
    if (selectedOrders.length === 0) {
      toast.error("Lütfen sipariş seçiniz");
      return;
    }
    const token = localStorage.getItem('token');
    toast.loading("Kargo etiketleri hazırlanıyor...", { id: "bulklbl" });
    try {
      const htmls = await Promise.all(
        selectedOrders.map(async (id) => {
          try {
            const r = await fetch(`${API}/orders/${id}/cargo-label?token=${token}`);
            if (!r.ok) return "";
            return await r.text();
          } catch {
            return "";
          }
        })
      );
      const valid = htmls.filter(Boolean);
      toast.dismiss("bulklbl");
      if (!valid.length) {
        toast.error("Yazdırılacak etiket bulunamadı");
        return;
      }
      // İlk etiketin <head> içeriği (Google Fonts link + stil + @page) bir kez kullanılır.
      const headInner = (valid[0].match(/<head[^>]*>([\s\S]*?)<\/head>/i)?.[1] || "")
        .replace(/<title[\s\S]*?<\/title>/i, "");
      // Her etiketin body'si; içindeki tekil <script> çıkarılır.
      const bodies = valid.map((html) => {
        const m = html.match(/<body[^>]*>([\s\S]*?)<\/body>/i);
        return (m ? m[1] : html).replace(/<script[\s\S]*?<\/script>/gi, "");
      });
      const doc =
        `<!doctype html><html lang="tr"><head>` + headInner +
        `<style>` +
        `html,body{height:auto!important;width:auto!important;background:#e5e7eb}` +
        `.label{page-break-after:always;margin:4mm auto;background:#fff;box-shadow:0 1px 4px rgba(0,0,0,.2)}` +
        `.print-bar{position:sticky;top:0;z-index:9;background:#ecfdf5;border-bottom:1px solid #a7f3d0;padding:8px 14px;display:flex;justify-content:space-between;align-items:center;font-family:system-ui,Arial,sans-serif}` +
        `.print-bar button{padding:6px 16px;background:#059669;color:#fff;border:0;border-radius:6px;cursor:pointer;font-weight:700}` +
        `@media print{.print-bar{display:none}.label{margin:0;box-shadow:none}html,body{background:#fff}}` +
        `</style></head><body>` +
        `<div class="print-bar"><strong>${bodies.length} Kargo Etiketi</strong><button onclick="window.print()">Tümünü Yazdır</button></div>` +
        bodies.join("") +
        `<script>` +
        `(async function(){` +
        `if(document.fonts&&document.fonts.ready){try{await document.fonts.ready}catch(e){}}` +
        `await new Promise(r=>requestAnimationFrame(()=>r()));` +
        `document.querySelectorAll('.barcode').forEach(function(el){` +
        `var main=el.closest('.main');var maxW=(main?main.clientWidth:340)-12;` +
        `var size=36;el.style.fontSize=size+'pt';var safety=24;` +
        `while(el.scrollWidth>maxW&&size>18&&safety-->0){size-=1;el.style.fontSize=size+'pt';}` +
        `});` +
        `})();` +
        `</script></body></html>`;
      const w = window.open('', '_blank');
      if (!w) {
        toast.error("Açılır pencere engellendi — tarayıcı pop-up iznini açın");
        return;
      }
      w.document.write(doc);
      w.document.close();
      // Etiket çekimleri backend'de "yazdırıldı" damgası bıraktı → kamyonlar sarı→yeşil.
      fetchOrders();
    } catch (e) {
      toast.dismiss("bulklbl");
      toast.error("Etiket yazdırma başarısız");
    }
  };

  /**
   * handleBulkGenerateInvoice — Seçili siparişlerin HEPSİ için e-Arşiv fatura
   *   oluşturur. Sıralı çağrı yapar; hata olursa tek tek raporlar.
   *   BACKEND: POST /api/orders/{id}/create-invoice?invoice_type=e-arsiv
   */
  const handleBulkGenerateInvoice = async () => {
    if (selectedOrders.length === 0) {
      toast.error("Lütfen sipariş seçiniz");
      return;
    }
    if (!await window.appConfirm(`${selectedOrders.length} sipariş için fatura oluşturulacak (otomatik: VKN'liyse e-Fatura, değilse e-Arşiv). Devam?`)) return;
    // Toplu barkodla tutarlı: işlem sürerken "oluşturuluyor" bildirimi (aynı id ile sonra başarı/hata ile değişir).
    const _invTid = toast.loading("Toplu fatura oluşturuluyor...");
    try {
      const token = localStorage.getItem('token');
      const res = await axios.post(
        `${API}/orders/bulk-create-invoice?invoice_type=auto`,
        selectedOrders,
        { headers: { Authorization: `Bearer ${token}` } }
      );
      const ok = res.data?.success_count || 0;
      const fail = res.data?.error_count || 0;
      toast.success(`${ok} fatura oluşturuldu${fail > 0 ? `, ${fail} başarısız` : ""}`, { id: _invTid });
      if (fail > 0 && res.data?.errors?.length) {
        console.warn("Bulk invoice errors:", res.data.errors);
        const havaleBlocked = res.data.errors.filter(
          (e) => (e.error || "").includes("Havale onaylanmadığı")
        );
        if (havaleBlocked.length) {
          toast.error(
            `${havaleBlocked.length} sipariş havale onaylanmadığı için faturalanamadı. Önce ödemelerini 'Ödendi' işaretleyin.`,
            { duration: 6000 }
          );
        }
      }
      setSelectedOrders([]);
      fetchOrders();
    } catch (err) {
      toast.error(err.response?.data?.detail || "Toplu fatura başarısız", { id: _invTid });
    }
  };

  /**
   * handleBulkPrintInvoices — Seçili siparişlerin faturalarını TEK bir
   *   yazdırılabilir pencerede birleştirir. Her fatura HTML'i fetch ile
   *   alınır; cross-origin iframe X-Frame-Options(DENY) ile engellendiği
   *   için IFRAME KULLANILMAZ — body içerikleri tek sayfaya gömülür.
   *   Pop-up engellenirse kullanıcı uyarılır.
   */
  const handleBulkPrintInvoices = async () => {
    if (selectedOrders.length === 0) {
      toast.error("Lütfen sipariş seçiniz");
      return;
    }
    const token = localStorage.getItem('token');
    toast.loading("Faturalar hazırlanıyor...", { id: "bulkinv" });
    try {
      const htmls = await Promise.all(
        selectedOrders.map(async (id) => {
          try {
            const r = await fetch(`${API}/orders/${id}/invoice/print?token=${token}`);
            if (!r.ok) return "";
            return await r.text();
          } catch {
            return "";
          }
        })
      );
      const sections = htmls
        .filter(Boolean)
        .map((html) => {
          const m = html.match(/<body[^>]*>([\s\S]*?)<\/body>/i);
          return `<div class="page">${m ? m[1] : html}</div>`;
        });
      toast.dismiss("bulkinv");
      if (!sections.length) {
        toast.error("Yazdırılacak fatura bulunamadı");
        return;
      }
      const doc = `<!doctype html><html lang="tr"><head><meta charset="utf-8"/>` +
        `<title>${sections.length} Fatura</title><style>` +
        `@page{margin:12mm} body{font-family:system-ui,Arial,sans-serif;margin:0}` +
        `.bar{position:sticky;top:0;background:#fff7ed;border-bottom:1px solid #fed7aa;padding:8px 14px;display:flex;justify-content:space-between;align-items:center}` +
        `.bar button{padding:6px 14px;background:#111;color:#fff;border:0;border-radius:6px;cursor:pointer;font-weight:600}` +
        `.page{page-break-after:always;padding:4px}` +
        `@media print{.bar{display:none}.page{padding:0}}` +
        `</style></head><body>` +
        `<div class="bar"><strong>${sections.length} Fatura</strong><button onclick="window.print()">Tümünü Yazdır</button></div>` +
        sections.join("") +
        `</body></html>`;
      const w = window.open('', '_blank');
      if (!w) {
        toast.error("Açılır pencere engellendi — tarayıcı pop-up iznini açın");
        return;
      }
      w.document.write(doc);
      w.document.close();
    } catch (e) {
      toast.dismiss("bulkinv");
      toast.error("Fatura yazdırma başarısız");
    }
  };

  /**
   * handleBulkStatusChange — Seçili siparişlerin TOPLU durum değişimi.
   *   Üst checkbox ile tümünü seçip alt seçici ile "Hazırlanıyor" / "Kargoda"
   *   gibi toplu güncelleme yapılır. Backend tek tek gezinir, hata olursa
   *   bireysel olarak raporlar; frontend sonunda listeyi yeniler.
   */
  const handleBulkStatusChange = async (status) => {
    if (selectedOrders.length === 0) {
      toast.error("Lütfen sipariş seçiniz");
      return;
    }
    try {
      const token = localStorage.getItem('token');
      await axios.post(`${API}/orders/bulk-status?status=${status}`,
        selectedOrders, 
        { headers: { Authorization: `Bearer ${token}` } }
      );
      toast.success(`${selectedOrders.length} sipariş güncellendi`);
      setSelectedOrders([]);
      fetchOrders();
    } catch (err) {
      toast.error("Toplu güncelleme başarısız");
    }
  };

    const toggleSelectOrder = (orderId) => {
    setSelectedOrders(prev => 
      prev.includes(orderId) 
        ? prev.filter(id => id !== orderId)
        : [...prev, orderId]
    );
  };

  const toggleSelectAll = () => {
    if (selectedOrders.length === orders.length) {
      setSelectedOrders([]);
    } else {
      setSelectedOrders(orders.map(o => o.id));
    }
  };

  const openDetail = (order) => {
    setSelectedOrder(order);
    setEditMode(false);
    setDetailOpen(true);
    // Detayı zenginleştirilmiş haliyle çek (kalem Marka/KDV + Pazaryeri alanları).
    // Modal anında açılır; veri gelince birleştirilir.
    (async () => {
      try {
        const token = localStorage.getItem('token');
        const res = await axios.get(`${API}/orders/${order.id}`, {
          headers: { Authorization: `Bearer ${token}` },
        });
        if (res.data) {
          setSelectedOrder(prev => (prev && prev.id === order.id ? { ...prev, ...res.data } : prev));
        }
      } catch {
        /* detay zenginleştirme başarısızsa satır verisi kullanılmaya devam eder */
      }
    })();
  };

  // ---- Sipariş detayı düzenleme ----
  const _itemsKeyOf = (o) => (o?.lines?.length > 0 ? "lines" : "items");
  const _nameKeyOf = (it) =>
    it.productName !== undefined ? "productName" : (it.product_name !== undefined ? "product_name" : "name");

  const startEdit = () => {
    const o = selectedOrder;
    if (!o) return;
    const itemsKey = _itemsKeyOf(o);
    setEditData({
      itemsKey,
      shipping_address: { ...(o.shipping_address || {}) },
      billing_info: {
        is_corporate: !!(o.billing_info?.is_corporate || o.billing_address?.is_corporate),
        company_name: o.billing_info?.company_name || o.billing_address?.company_name || "",
        tax_number: o.billing_info?.tax_number || o.billing_address?.tax_number || o.billing_address?.tax_no || o.billing_address?.vkn || "",
        tax_office: o.billing_info?.tax_office || o.billing_address?.tax_office || "",
        e_invoice_user: !!o.billing_info?.e_invoice_user,
      },
      items: JSON.parse(JSON.stringify(o[itemsKey] || [])),
      subtotal: o.subtotal ?? 0,
      shipping_cost: o.shipping_cost ?? 0,
      discount: o.discount ?? 0,
      total: o.total ?? 0,
    });
    setEditMode(true);
  };

  const setSA = (k, v) => setEditData((d) => ({ ...d, shipping_address: { ...d.shipping_address, [k]: v } }));
  const setBI = (k, v) => setEditData((d) => ({ ...d, billing_info: { ...(d.billing_info || {}), [k]: v } }));
  const setField = (k, v) => setEditData((d) => ({ ...d, [k]: v }));
  const setItem = (idx, k, v) => setEditData((d) => {
    const items = [...d.items];
    items[idx] = { ...items[idx], [k]: v };
    return { ...d, items };
  });

  const saveEdit = async () => {
    if (!editData || !selectedOrder) return;
    const num = (x) => (x === "" || x === null || x === undefined ? undefined : Number(x));
    try {
      const items = editData.items.map((it) => {
        const o = { ...it };
        if (it.quantity !== undefined) o.quantity = Number(it.quantity) || 1;
        ["unit_price", "discount_amount", "price", "amount"].forEach((f) => {
          if (it[f] !== undefined) { const n = num(it[f]); if (n !== undefined) o[f] = n; }
        });
        return o;
      });
      const _bi = editData.billing_info || {};
      const _tn = String(_bi.tax_number || "").trim();
      const billing_info = {
        is_corporate: !!_bi.is_corporate || _tn.length === 10 || !!String(_bi.company_name || "").trim(),
        company_name: String(_bi.company_name || "").trim(),
        tax_number: _tn,
        tax_office: String(_bi.tax_office || "").trim(),
        e_invoice_user: !!_bi.e_invoice_user,
      };
      const payload = {
        shipping_address: editData.shipping_address,
        billing_info,
        // Fatura kesimi öncelikle billing_address'i okur → kurumsal alanları oraya da yansıt
        billing_address: {
          ...(selectedOrder.billing_address || {}),
          company_name: billing_info.company_name,
          tax_number: billing_info.tax_number,
          tax_office: billing_info.tax_office,
          is_corporate: billing_info.is_corporate,
        },
        [editData.itemsKey]: items,
        subtotal: Number(editData.subtotal) || 0,
        shipping_cost: Number(editData.shipping_cost) || 0,
        discount: Number(editData.discount) || 0,
        total: Number(editData.total) || 0,
      };
      const token = localStorage.getItem("token");
      await axios.put(`${API}/orders/${selectedOrder.id}`, payload, { headers: { Authorization: `Bearer ${token}` } });
      toast.success("Sipariş güncellendi");
      setSelectedOrder({ ...selectedOrder, ...payload });
      setEditMode(false);
      fetchOrders();
    } catch (e) {
      toast.error(e.response?.data?.detail || "Kaydedilemedi");
    }
  };

  const formatDate = (dateStr) => {
    return new Date(dateStr).toLocaleDateString('tr-TR', {
      day: '2-digit',
      month: '2-digit',
      year: 'numeric',
      hour: '2-digit',
      minute: '2-digit'
    });
  };

  const viewReceipt = async (orderId) => {
    try {
      const token = localStorage.getItem('token');
      const res = await axios.get(`${API}/orders/${orderId}/payment-receipt`, {
        headers: { Authorization: `Bearer ${token}` }, responseType: 'blob'
      });
      const url = URL.createObjectURL(res.data);
      window.open(url, '_blank');
      setTimeout(() => URL.revokeObjectURL(url), 60000);
    } catch (e) {
      toast.error('Dekont görüntülenemedi');
    }
  };

  const approvePayment = async (orderId) => {
    const ok = window.appConfirm ? await window.appConfirm('Ödemeyi onaylayıp siparişi "Onaylandı" durumuna almak istiyor musunuz?') : window.confirm('Ödemeyi onayla?');
    if (!ok) return;
    try {
      const token = localStorage.getItem('token');
      await axios.put(`${API}/orders/${orderId}/mark-paid`, {}, { headers: { Authorization: `Bearer ${token}` } });
      toast.success('Ödeme onaylandı, sipariş "Onaylandı" durumuna alındı');
      fetchOrders();
      if (selectedOrder?.id === orderId) setSelectedOrder({ ...selectedOrder, payment_status: 'paid', status: 'confirmed' });
    } catch (e) {
      toast.error('Onaylanamadı');
    }
  };

  const [reminderSending, setReminderSending] = useState(false);
  const sendPaymentReminder = async (orderId) => {
    const ok = window.appConfirm ? await window.appConfirm('Müşteriye ödeme hatırlatma SMS\'i (ve e-postası) gönderilsin mi?') : window.confirm('Hatırlatma gönderilsin mi?');
    if (!ok) return;
    setReminderSending(true);
    try {
      const token = localStorage.getItem('token');
      const r = await axios.post(`${API}/orders/${orderId}/send-payment-reminder`, {}, { headers: { Authorization: `Bearer ${token}` } });
      if (r.data?.success) {
        toast.success(`Ödeme hatırlatması gönderildi${r.data.sms ? ' · SMS' : ''}${r.data.email ? ' · E-posta' : ''}`);
        const now = new Date().toISOString();
        if (selectedOrder?.id === orderId) setSelectedOrder({ ...selectedOrder, payment_reminder_last_at: now, payment_reminder_count: (selectedOrder.payment_reminder_count || 0) + 1 });
      } else {
        toast.error(r.data?.message || 'Hatırlatma gönderilemedi');
      }
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Hatırlatma gönderilemedi');
    } finally {
      setReminderSending(false);
    }
  };

  useEffect(() => {
    (async () => {
      try {
        const token = localStorage.getItem('token');
        const r = await axios.get(`${API}/settings/order-statuses`, { headers: { Authorization: `Bearer ${token}` } });
        const act = (r.data?.statuses || []).filter(s => s.active).map(s => s.key);
        if (act.length) setActiveStatusKeys(act);
      } catch (e) { /* tum liste fallback */ }
    })();
  }, []);

  // "Gorunur" secili durumlar (bos ise tum katalog) — durum DEGISTIRME acilir menulerinde kullanilir
  const visibleStatusOptions = activeStatusKeys.length
    ? statusOptions.filter(s => activeStatusKeys.includes(s.value))
    : statusOptions;

  const getStatusInfo = (status) => {
    return statusOptions.find(s => s.value === status) || statusOptions[0];
  };

  return (
    <div data-testid="admin-orders">
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-bold">{unpaidView ? "Ödeme Kaydı Bulunmayan Siparişler" : "Siparişler"}</h1>
        <button
          onClick={() => setAdvancedFiltersOpen(!advancedFiltersOpen)}
          className={`flex items-center gap-2 px-4 py-2 border rounded hover:bg-gray-50 text-sm ${advancedFiltersOpen ? 'bg-gray-100 border-gray-300' : ''}`}
        >
          <Filter size={16} /> Gelişmiş Filtreler
        </button>
      </div>

      {/* Gelişmiş Filtreler Paneli */}
      {advancedFiltersOpen && (
        <div className="bg-white border rounded-lg p-4 mb-6 shadow-sm">
          <div className="grid grid-cols-2 lg:grid-cols-4 xl:grid-cols-5 gap-3">
            <input type="text" placeholder="Genel Arama (ad, no, fatura, kargo...)" className="border px-3 py-1.5 rounded text-sm" value={filters.search} onChange={e => setFilters({...filters, search: e.target.value})} onKeyDown={onFilterKey} />
            <input type="text" placeholder="Sipariş No" className="border px-3 py-1.5 rounded text-sm" value={filters.order_number} onChange={e => setFilters({...filters, order_number: e.target.value})} onKeyDown={onFilterKey} />
            <input type="text" placeholder="Fatura No" className="border px-3 py-1.5 rounded text-sm" value={filters.invoice_number} onChange={e => setFilters({...filters, invoice_number: e.target.value})} onKeyDown={onFilterKey} />
            <input type="text" placeholder="Kargo Takip No" className="border px-3 py-1.5 rounded text-sm" value={filters.cargo_tracking} onChange={e => setFilters({...filters, cargo_tracking: e.target.value})} onKeyDown={onFilterKey} />
            <input type="text" placeholder="Telefon" className="border px-3 py-1.5 rounded text-sm" value={filters.phone} onChange={e => setFilters({...filters, phone: e.target.value})} onKeyDown={onFilterKey} />
            <input type="text" placeholder="E-posta" className="border px-3 py-1.5 rounded text-sm" value={filters.email} onChange={e => setFilters({...filters, email: e.target.value})} onKeyDown={onFilterKey} />
            <input type="text" placeholder="Kupon Kodu" className="border px-3 py-1.5 rounded text-sm" value={filters.coupon_code} onChange={e => setFilters({...filters, coupon_code: e.target.value})} onKeyDown={onFilterKey} />
            <MultiSelect className="w-44" placeholder="Tüm Platformlar" value={filters.platform} onChange={(v) => setFilters({ ...filters, platform: v })}
              options={[
                { value: "facette", label: "Web Sitesi (Facette)" },
                { value: "trendyol", label: "Trendyol" },
                { value: "hepsiburada", label: "Hepsiburada" },
                { value: "amazon", label: "Amazon" },
                { value: "temu", label: "Temu" },
                { value: "n11", label: "N11" },
                { value: "ciceksepeti", label: "Çiçeksepeti" },
                { value: "pttavm", label: "PttAVM" },
              ]} />
            <MultiSelect className="w-44" placeholder="Tüm Kaynaklar" title="Geliş kaynağı (reklam/organik/sosyal/influencer)" value={filters.channel} onChange={(v) => setFilters({ ...filters, channel: v })}
              options={[
                { value: "organic", label: "Organik" },
                { value: "ads|paid", label: "Reklam (Tümü)" },
                { value: "instagram", label: "Instagram" },
                { value: "google", label: "Google" },
                { value: "tiktok", label: "TikTok" },
                { value: "facebook", label: "Facebook" },
                { value: "influencer", label: "Influencer" },
                { value: "email", label: "E-posta" },
                { value: "referral", label: "Referans" },
                { value: "direct", label: "Doğrudan" },
              ]} />
            <MultiSelect className="w-44" placeholder="Tüm Ödeme Tipleri" value={filters.payment_method} onChange={(v) => setFilters({ ...filters, payment_method: v })}
              options={[
                { value: "credit_card", label: "Kredi Kartı" },
                { value: "bank_transfer", label: "Havale/EFT" },
                { value: "cash_on_delivery", label: "Kapıda Ödeme" },
              ]} />
            <MultiSelect className="w-44" placeholder="Tüm Ödeme Durumları" value={filters.payment_status} onChange={(v) => setFilters({ ...filters, payment_status: v })}
              options={[
                { value: "paid", label: "Ödendi" },
                { value: "pending", label: "Bekliyor" },
                { value: "failed", label: "Başarısız" },
                { value: "refunded", label: "İade Edildi" },
              ]} />
            <MultiSelect className="w-44" placeholder="Tüm Sipariş Durumları" value={filters.status} onChange={(v) => setFilters({ ...filters, status: v })}
              options={statusOptions.map(s => ({ value: s.value, label: s.label }))} />
            <label className="flex items-center gap-2 text-sm border px-3 py-1.5 rounded cursor-pointer">
              <input type="checkbox" className="accent-black" checked={filters.influencer === "1"} onChange={e => setFilters({...filters, influencer: e.target.checked ? "1" : ""})} />
              Influencer ile gelen
            </label>
            <label className="flex items-center gap-2 text-sm border px-3 py-1.5 rounded cursor-pointer">
              <input type="checkbox" className="accent-black" checked={filters.is_corporate === "1"} onChange={e => setFilters({...filters, is_corporate: e.target.checked ? "1" : ""})} />
              Kurumsal fatura
            </label>
            <div className="flex gap-2 items-center text-sm text-gray-500 xl:col-span-2">
              <span className="shrink-0">Tarih:</span>
              <input type="date" title="Başlangıç Tarihi" className="border px-2 py-1.5 rounded flex-1" value={filters.start_date} onChange={e => setFilters({...filters, start_date: e.target.value})} />
              <span>-</span>
              <input type="date" title="Bitiş Tarihi" className="border px-2 py-1.5 rounded flex-1" value={filters.end_date} onChange={e => setFilters({...filters, end_date: e.target.value})} />
            </div>
            <div className="flex gap-2 xl:col-span-2">
              <button onClick={applyFilters} type="button" className="w-1/2 bg-black text-white px-3 py-1.5 rounded text-sm hover:bg-gray-800 flex justify-center items-center gap-1">
                <Search size={14} /> Ara
              </button>
              <button
                onClick={() => {
                  setFilters({ search: "", phone: "", email: "", order_number: "", cargo_tracking: "", invoice_number: "", coupon_code: "", start_date: "", end_date: "", payment_method: "", payment_status: "", platform: "", channel: "", status: "", influencer: "", is_corporate: "" });
                  applyFilters();
                }}
                className="w-1/2 px-3 py-1.5 border hover:border-gray-400 rounded text-sm bg-gray-50 hover:bg-white transition-colors"
                type="button"
              >
                Sıfırla
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Bulk Actions Bar */}
      {selectedOrders.length > 0 && (
        <div className="bg-blue-50 border border-blue-200 rounded-lg p-4 mb-4 flex items-center justify-between flex-wrap gap-3">
          <span className="text-sm font-medium">{selectedOrders.length} sipariş seçildi</span>
          <div className="flex items-center gap-2 flex-wrap">
            <button 
              onClick={handleBulkCargoBarcode}
              className="flex items-center gap-1 px-3 py-1.5 bg-green-600 text-white text-sm rounded hover:bg-green-700"
              data-testid="bulk-cargo-barcode-btn"
            >
              <Package size={16} />
              Toplu Barkod Oluştur
            </button>
            <button
              onClick={handleBulkPrintCargoLabels}
              className="flex items-center gap-1 px-3 py-1.5 bg-emerald-700 text-white text-sm rounded hover:bg-emerald-800"
              data-testid="bulk-print-cargo-label-btn"
              title="Seçili siparişlerin kargo barkodu etiketlerini tek sayfada yazdır"
            >
              <Printer size={16} />
              Kargo Barkodu Yazdır
            </button>
            {/* Yeni: Toplu fatura oluştur + yazdır */}
            <button
              onClick={handleBulkGenerateInvoice}
              className="flex items-center gap-1 px-3 py-1.5 bg-amber-600 text-white text-sm rounded hover:bg-amber-700"
              data-testid="bulk-generate-invoice-btn"
              title="Seçili siparişlere e-Arşiv fatura oluştur"
            >
              <FileText size={16} />
              Toplu Fatura Kes
            </button>
            <button
              onClick={handleBulkPrintInvoices}
              className="flex items-center gap-1 px-3 py-1.5 bg-slate-700 text-white text-sm rounded hover:bg-slate-800"
              data-testid="bulk-print-invoices-btn"
              title="Seçili siparişlerin faturalarını tek sayfada yazdır"
            >
              <Printer size={16} />
              Toplu Fatura Yazdır
            </button>
            <select
              onChange={(e) => {
                if (e.target.value) {
                  handleBulkStatusChange(e.target.value);
                  e.target.value = "";
                }
              }}
              className="border px-2 py-1 rounded text-sm"
              defaultValue=""
            >
              <option value="" disabled>Toplu Durum Güncelle</option>
              {statusOptions.map(s => (
                <option key={s.value} value={s.value}>{s.label}</option>
              ))}
            </select>
            <button
              onClick={handleBulkDelete}
              disabled={bulkDeleting}
              className="flex items-center gap-1 px-3 py-1.5 bg-red-600 text-white text-sm rounded hover:bg-red-700 disabled:opacity-50"
              data-testid="bulk-delete-btn"
              title="Seçili siparişleri sil (Silinen Siparişler'e taşınır, geri alınabilir)"
            >
              <Trash2 size={16} />
              {bulkDeleting ? "Siliniyor..." : "Seçilenleri Sil"}
            </button>
          </div>
        </div>
      )}

      {/* =================================================================
          SİPARİŞLER TABLOSU
          -----------------------------------------------------------------
          - .admin-table-compact ile satır yüksekliği azaltıldı → bir
            sayfada daha çok sipariş görünür.
          - ÜST (compact) Pagination: özet kartlarının hemen altında,
            minimal tek satır. Filtre barı ve tabloyu birbirinden ayıran
            ince bir navigasyon katmanı olarak çalışır.
          - ALT (full) Pagination: tam numaralı + git kutusu.
          ================================================================= */}

      {/* Gizlenen iade/iptal özeti — kullanıcı 'Gizlenenleri göster' açmak zorunda kalmaz;
          bu siparişler otomatik olarak İadeler/İptaller sayfalarındadır, buradan tek tıkla gider. */}
      {hiddenSummary && (hiddenSummary.iade > 0 || hiddenSummary.iptal > 0) && (
        <div className="mb-2 text-xs text-gray-600 bg-amber-50 border border-amber-200 rounded-lg px-3 py-2 flex items-center gap-2 flex-wrap" data-testid="orders-hidden-summary">
          <span className="font-medium text-amber-900">Bu filtrede ayrıca:</span>
          {hiddenSummary.iade > 0 && (
            <a href="/admin/iadeler" className="text-blue-700 hover:underline font-semibold">
              {hiddenSummary.iade} iade → İadeler sayfasında
            </a>
          )}
          {hiddenSummary.iade > 0 && hiddenSummary.iptal > 0 && <span className="text-gray-400">·</span>}
          {hiddenSummary.iptal > 0 && (
            <a href="/admin/iptaller" className="text-blue-700 hover:underline font-semibold">
              {hiddenSummary.iptal} iptal → İptaller sayfasında
            </a>
          )}
        </div>
      )}

      {/* Üst (compact) pagination */}
      {total > 0 && (
        <div className="flex justify-end mb-2" data-testid="orders-top-pagination">
          <Pagination
            page={page}
            total={total}
            pageSize={pageSize}
            onChange={setPage}
            onPageSizeChange={(s) => { setPageSize(s); setPage(1); }}
            variant="compact"
          />
        </div>
      )}

      {/* Orders Table */}
      <div className="bg-white rounded-lg shadow-sm overflow-x-auto">
        <table className="admin-table admin-table-compact">
          <thead>
            <tr>
              <th className="w-10">
                <button onClick={toggleSelectAll} className="p-1">
                  {selectedOrders.length === orders.length ? (
                    <CheckSquare size={18} />
                  ) : (
                    <Square size={18} />
                  )}
                </button>
              </th>
              <th>Sipariş No</th>
              <th>Müşteri</th>
              <th>Ürünler</th>
              <th>Tutar</th>
              <th>Ödeme Tipi</th>
              <th>Platform</th>
              <th>Durum</th>
              <th>Kargo</th>
              <th>Tarih</th>
              <th>İşlemler</th>
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr>
                <td colSpan={10} className="text-center py-8">Yükleniyor...</td>
              </tr>
            ) : orders.length === 0 ? (
              <tr>
                <td colSpan={10} className="text-center py-8 text-gray-500">Sipariş bulunamadı</td>
              </tr>
            ) : (
              orders.map((order) => {
                const statusInfo = getStatusInfo(order.status);
                // FAZ 1 B2/B3/B4 visual logic
                const pmLower = (order.payment_method || '').toLowerCase();
                const isHavale = ['transfer', 'havale', 'bank_transfer', 'eft'].includes(pmLower);
                // Kullanıcı onaylandı/ilerisi ise ödeme teyit edilmiş sayılır → kırmızı vurgu kalkar
                const paymentConfirmed = order.payment_status === 'paid' ||
                  ['confirmed', 'processing', 'shipped', 'delivered', 'undelivered'].includes(order.status);
                const isUnpaidHavale = isHavale && !paymentConfirmed && order.status !== 'cancelled';
                const isUnpaidPending = !paymentConfirmed && !isHavale && order.status === 'pending';
                // İPTAL TALEBİ ALINDI: müşteri ödemesi alınmış siparişi iptal etti → PARA İADESİ BEKLİYOR.
                // Havale-kırmızıdan farklı olsun diye MOR/menekşe vurgu. Personel iadeyi yapıp iptale çeker.
                const isCancelRequested = order.status === 'cancel_requested';
                const isInvoiceIssued = !!order.invoice_issued;
                // row-unpaid-* marker sınıfları: hover'da .admin-table tr:hover td kuralı
                // kırmızı/sarı vurguyu griyle eziyordu; index.css'te bu marker'lar hover'da
                // rengi KORUR (karışıklık olmasın diye kırmızı şerit hover'da kaybolmaz).
                const rowClass = isCancelRequested
                  ? 'bg-purple-50 border-l-4 border-purple-500 row-unpaid-havale'
                  : isUnpaidHavale
                  ? 'bg-red-50 border-l-4 border-red-400 row-unpaid-havale'
                  : isUnpaidPending
                  ? 'bg-yellow-50 row-unpaid-pending'
                  : '';
                return (
                  <tr key={order.id} className={rowClass} data-testid={`order-row-${order.id}`}>
                    <td>
                      <button onClick={() => toggleSelectOrder(order.id)} className="p-1">
                        {selectedOrders.includes(order.id) ? (
                          <CheckSquare size={18} className="text-blue-600" />
                        ) : (
                          <Square size={18} />
                        )}
                      </button>
                    </td>
                    <td className="font-medium">{order.order_number}{order.has_partial_return ? <span className="ml-1.5 inline-block px-1.5 py-0.5 rounded text-[10px] font-semibold bg-amber-100 text-amber-700 align-middle" title="Bu siparişte bir veya daha fazla kalem iade edildi">kısmi iade</span> : null}{order.partial_cancelled ? <span className="relative inline-flex ml-1.5 align-middle" title="Bu siparişte iptal edilen ürün var"><span className="animate-ping absolute inline-flex h-2.5 w-2.5 rounded-full bg-red-400 opacity-75"></span><span className="relative inline-flex rounded-full h-2.5 w-2.5 bg-red-500"></span></span> : null}</td>
                    <td>
                      <div>
                        {(() => {
                          // FAZ 6 — risk rozeti
                          const key = order.user_id || order.shipping_address?.email;
                          const risk = key ? riskMap[key] : null;
                          const high = risk?.risk_level === "high";
                          const medium = risk?.risk_level === "medium";
                          return (
                            <>
                              <p className={`font-medium flex items-center gap-1.5 ${high ? "text-red-600" : ""}`}>
                                {order.shipping_address?.first_name} {order.shipping_address?.last_name}
                                {high && (
                                  <span title={`İade oranı: %${risk.return_rate_pct} (${risk.returned}/${risk.total_orders})`}
                                    className="inline-flex items-center gap-0.5 bg-red-100 text-red-700 text-[10px] px-1.5 py-0.5 rounded-full font-semibold"
                                    data-testid="risk-badge-high">
                                    ⚠ %{risk.return_rate_pct}
                                  </span>
                                )}
                                {medium && !high && (
                                  <span title={`İade oranı: %${risk.return_rate_pct}`}
                                    className="inline-flex items-center gap-0.5 bg-yellow-100 text-yellow-700 text-[10px] px-1.5 py-0.5 rounded-full font-semibold"
                                    data-testid="risk-badge-medium">
                                    %{risk.return_rate_pct}
                                  </span>
                                )}
                              </p>
                              <p className="text-xs text-gray-500">{order.shipping_address?.phone}</p>
                            </>
                          );
                        })()}
                      </div>
                    </td>
                    <td>
                      <div className="flex flex-col gap-0.5">
                        {/* Trendyol stores items in 'lines', web orders in 'items' */}
                        {(order.lines?.length > 0 ? order.lines : order.items)?.slice(0, 2).map((item, i) => {
                          const qty = item.quantity || 1;
                          const img = item.image || item.product_image || item.imageUrl;
                          return (
                          <div key={i} className="flex items-center gap-1 text-xs">
                            <div className="relative w-6 h-6 shrink-0">
                              {img ? (
                                <img src={img} alt="" className="w-6 h-6 object-cover bg-gray-100 rounded" />
                              ) : (
                                <div className="w-6 h-6 bg-gray-100 rounded flex items-center justify-center text-[8px] text-gray-400">—</div>
                              )}
                              {qty > 1 && (
                                <span className="absolute -top-1 -left-1 min-w-[14px] h-[14px] px-0.5 bg-black text-white text-[9px] leading-[14px] text-center rounded-full font-bold">{qty}</span>
                              )}
                            </div>
                            <span className="truncate max-w-[120px]">{item.productName || item.product_name || item.name || 'Ürün'}</span>
                          </div>
                          );
                        })}
                        {((order.lines?.length > 0 ? order.lines : order.items)?.length || 0) > 2 && (
                          <span className="text-xs text-gray-500">+{(order.lines?.length || order.items?.length || 0) - 2} daha</span>
                        )}
                        {!order.lines?.length && !order.items?.length && (
                          <span className="text-xs text-gray-400">—</span>
                        )}
                      </div>
                    </td>
                    <td>
                      {/* Gider pusulası düzeni: Brüt(liste, üstü çizili gri) · İskonto(bordo) · Net(kalın) */}
                      <div className="flex flex-col gap-0.5 tabular-nums" data-testid={`amount-${order.id}`}>
                        {(order.subtotal != null && ((order.discount_amount || order.discount || 0) > 0)) && (
                          <>
                            <span className="text-xs text-gray-400 line-through">{order.subtotal?.toFixed(2)} TL</span>
                            <span className="text-xs font-medium text-[#8b1e3f]">İskonto -{(order.discount_amount || order.discount || 0).toFixed(2)} TL</span>
                          </>
                        )}
                        {/* Brüt−İskonto ile Net arasındaki farkı açıklayan ek kalemler (kullanıcı isteği):
                            kargo / hediye paketi / puan görünmeyince tutar 'yanlış' sanılıyordu. */}
                        {Number(order.shipping_cost) > 0 && (
                          <span className="text-xs text-gray-500">Kargo +{Number(order.shipping_cost).toFixed(2)} TL</span>
                        )}
                        {order.gift_wrap && Number(order.gift_wrap_price) > 0 && (
                          <span className="text-xs text-gray-500">🎁 Paket +{Number(order.gift_wrap_price).toFixed(2)} TL</span>
                        )}
                        {Number(order.points_used) > 0 && (
                          <span className="text-xs text-gray-500">Puan -{Number(order.points_used).toFixed(2)} TL</span>
                        )}
                        <span className="text-sm font-semibold text-gray-900">{order.total?.toFixed(2)} TL</span>
                        {(() => {
                          // Taksitli siparişte müşteriden GERÇEKTE çekilen tutar (vade farkı dahil):
                          // panel peşin `total` gösteriyordu; çekileni de belirt (fatura bu tutarı yansıtır).
                          const iyz = order.iyzico_retrieve_response || {};
                          const charged = Number(iyz.paidPrice) || 0;
                          const inst = parseInt(iyz.installment || order.installment || 1, 10) || 1;
                          const vf = charged > 0 ? Math.round((charged - (order.total || 0)) * 100) / 100 : 0;
                          if (inst > 1 && vf > 0.01) return (
                            <span className="text-[11px] text-amber-600" title={`Müşteri ${inst} taksit seçti; iyzico peşin tutarın üzerine ${vf.toFixed(2)} TL vade farkı ekledi. Fatura bu çekilen tutarı yansıtır.`}>
                              💳 Çekilen: {charged.toFixed(2)} TL ({inst} taksit · vade farkı +{vf.toFixed(2)})
                            </span>
                          );
                          return null;
                        })()}
                        {isUnpaidHavale && (
                          <span className="text-xs text-gray-500">Ödeme bekliyor</span>
                        )}
                      </div>
                    </td>
                    <td>
                      {(() => {
                        const pm = (order.payment_method || '').toLowerCase();
                        const paid = order.payment_status === 'paid' ||
                          ['confirmed', 'processing', 'shipped', 'delivered', 'undelivered'].includes(order.status);
                        let label = '';
                        if (pm === 'transfer' || pm === 'havale' || pm === 'bank_transfer' || pm === 'eft') label = 'Havale/EFT';
                        else if (pm === 'credit_card' || pm === 'card' || pm === 'iyzico' || pm === 'cc') label = 'Kredi Kartı';
                        else if (pm === 'cod' || pm === 'kapida') label = 'Kapıda';
                        else if (order.platform === 'trendyol' || order.platform === 'hepsiburada' || order.platform === 'temu' || pm === 'marketplace') label = 'Marketplace';
                        if (!label) return <span className="text-sm text-gray-400">—</span>;
                        const isHavale = (pm === 'transfer' || pm === 'havale' || pm === 'bank_transfer' || pm === 'eft');
                        return (
                          <div className="flex flex-col text-sm text-gray-900">
                            <span>{label}</span>
                            {isHavale && <span className="text-xs text-gray-500">{paid ? 'Ödendi' : 'Beklemede'}</span>}
                          </div>
                        );
                      })()}
                    </td>
                    <td>
                      {(() => {
                        const p = (order.platform || '').toLowerCase();
                        const map = {
                          trendyol: { label: 'Trendyol', bg: 'bg-[#F27A1A]' },
                          hepsiburada: { label: 'Hepsiburada', bg: 'bg-[#FF6000]' },
                          temu: { label: 'Temu', bg: 'bg-[#FB7701]' },
                        };
                        const m = map[p] || { label: 'Web', bg: 'bg-gray-800' };
                        return <span className={`inline-block px-2 py-0.5 ${m.bg} text-white text-[10px] uppercase font-bold tracking-wider rounded`}>{m.label}</span>;
                      })()}
                    </td>
                    <td>
                      <Select
                        value={order.status}
                        onValueChange={(value) => handleStatusChange(order.id, value)}
                      >
                        <SelectTrigger className="w-32 h-8 text-xs">
                          <SelectValue>
                            <span className="text-gray-700 font-medium">{statusInfo.label}</span>
                          </SelectValue>
                        </SelectTrigger>
                        <SelectContent>
                          {visibleStatusOptions.map((opt) => (
                            <SelectItem key={opt.value} value={opt.value}>{opt.label}</SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    </td>
                    <td>
                      {(() => {
                        const c = order.cargo || {};
                        const barcodeNo = order.cargo_barcode_number || c.mng_siparis_no || ""; // bizim barkodumuz — takip no DEĞİL
                        // Gerçek kargo takip no: kargo firması gönderi numarası (NZ / GONDERI_NO) — barkod hariç
                        let trk = order.cargo_gonderi_no || c.mng_nz_barkod || c.mng_nz_gonderi_no || c.mng_gonderi_no || "";
                        if (!trk) {
                          const ctn = order.cargo_tracking_number || c.tracking_number || "";
                          if (ctn && ctn !== barcodeNo) trk = ctn; // eski kayıt: scheduler gerçek no yazmışsa
                        }
                        const url = order.cargo_tracking_url || order.cargo_tracking_link || c.tracking_link || "";
                        const lastText = order.cargo_last_status_text || order.cargo_status_text || "";
                        // Gerçek takip no var → kargo firması yakaladı: yeşil kamyon + no
                        if (trk) {
                          const body = (
                            <span className="inline-flex flex-col gap-0.5 leading-tight">
                              <span className="inline-flex items-center gap-1.5 text-emerald-600">
                                <Truck size={16} />
                                <span className="font-mono text-xs font-bold text-gray-700 break-all">{trk}</span>
                              </span>
                              {lastText && <span className="text-[10px] text-gray-500">{lastText}</span>}
                            </span>
                          );
                          return url
                            ? <a href={url} target="_blank" rel="noopener noreferrer" title={`Kargo takip: ${trk}`} className="hover:underline">{body}</a>
                            : <span title={lastText || "Kargoda"}>{body}</span>;
                        }
                        // Sadece barkod oluşturulmuş — kargo firması takip no'yu henüz üretmedi (kargoda değil)
                        if (barcodeNo) {
                          // Barkod var ama etiket henüz YAZDIRILMADI → kamyon SARI (aksiyon gerek).
                          // Etiket yazdırıldıysa (cargo_label_printed_at) → YEŞİL, takip no beklenir.
                          const printed = !!order.cargo_label_printed_at;
                          return printed ? (
                            <span className="inline-flex items-center gap-1.5" title={`Barkod yazdırıldı: ${barcodeNo} — kargo firması takip no'yu henüz üretmedi`}>
                              <Truck size={16} className="text-emerald-500" />
                              <span className="text-[10px] text-gray-400">takip bekleniyor</span>
                            </span>
                          ) : (
                            <span className="inline-flex items-center gap-1.5" title={`Barkod hazır: ${barcodeNo} — etiket HENÜZ YAZDIRILMADI`}>
                              <Truck size={16} className="text-amber-500" />
                              <span className="text-[10px] text-amber-600 font-medium">yazdırılmadı</span>
                            </span>
                          );
                        }
                        return <span className="text-gray-300">—</span>;
                      })()}
                    </td>
                    <td className="text-sm text-gray-500">{formatDate(order.created_at)}</td>
                    <td>
                      {/* Ticimax benzeri işlem butonları - daha belirgin ve ayrık */}
                      <div className="flex items-center gap-1 flex-wrap">
                        {/* 1. Detay - Ticimax: mavi klasör */}
                        <button
                          onClick={() => openDetail(order)}
                          title="Sipariş Detayı"
                          className="tci-btn tci-btn-blue"
                        >
                          <FolderOpen size={15} />
                        </button>
                        {/* 2. (kaldırıldı) Kargoya Ver → üst bar "Toplu Barkod Oluştur" + sipariş detayından yapılır */}
                        {/* 3. (kaldırıldı) Fatura Yazdır → üst bar "Toplu Fatura Yazdır" kullanılır */}
                        {/* 4. E-Arşiv Fatura Oluştur - kesilince saydam+pasif (Fatura Sıfırla'ya kadar) */}
                        {(() => {
                          const invoiced = isInvoiceIssued || !!order.invoice_number || !!order.invoice?.invoice_number;
                          const busy = invoicingId === order.id;
                          const invNo = order.invoice_number || order.invoice?.invoice_number || "";
                          return (
                            <button
                              onClick={() => handleGenerateInvoice(order.id)}
                              disabled={busy || invoiced}
                              title={invoiced
                                ? `Fatura kesildi${invNo ? `: ${invNo}` : ""} — yeniden basmak için Fatura Sıfırla`
                                : "E-Arşiv Fatura Oluştur"}
                              className={`tci-btn ${invoiced ? "tci-btn-green-active opacity-50 cursor-not-allowed" : "tci-btn-green"} ${busy ? "opacity-50 cursor-not-allowed" : ""}`}
                            >
                              <FileText size={15} />
                            </button>
                          );
                        })()}
                        {/* 5. (kaldırıldı) Kargo Etiketi → sipariş detayından / üst bardan yapılır */}
                        {/* 5b. (kaldırıldı) Kargo Durum Yenile — kargo verisi artık otomatik
                            çekildiği için manuel yenile butonuna gerek kalmadı. */}
                        {/* 6. (kaldırıldı) SMS → sipariş detayından gönderilir */}
                        {/* Not butonu — admin notu + müşteri sipariş notu göstergesi (en sağda) */}
                        {(() => {
                          const adminCount = order.admin_notes?.length || 0;
                          const custNote = (order.notes || "").trim();
                          const hasNote = adminCount > 0 || !!custNote;
                          return (
                            <button
                              onClick={() => openNoteModal(order)}
                              title={[
                                custNote ? `Müşteri notu: ${custNote}` : "",
                                adminCount ? `${adminCount} admin notu` : "",
                              ].filter(Boolean).join("\n") || "Not Ekle"}
                              data-testid={`note-btn-${order.id}`}
                              className={`tci-btn ${hasNote ? 'tci-btn-yellow-active' : 'tci-btn-gray'}`}
                            >
                              <FileCheck size={15} />
                              {adminCount > 0 ? (
                                <span className="absolute -top-1 -right-1 bg-red-500 text-white text-[9px] w-4 h-4 rounded-full flex items-center justify-center">{adminCount}</span>
                              ) : custNote ? (
                                <span className="absolute -top-1 -right-1 bg-amber-500 w-2.5 h-2.5 rounded-full border border-white" title="Müşteri notu var" />
                              ) : null}
                            </button>
                          );
                        })()}
                        {/* Hediye Paketi butonu — "ücretli" YALNIZ gerçek ek ücret (gift_wrap_price>0)
                            varsa. Ücret ödenmeden gelen hediye NOTU artık "ücretli" gösterilmez;
                            not ayrı belirteçle (pembe nokta) işaretlenir. */}
                        {(() => {
                          const giftNote = (order.gift_note || "").trim();
                          const giftPrice = Number(order.gift_wrap_price || 0);
                          const giftPaid = giftPrice > 0;                 // gerçekten ÜCRETLİ paket
                          const giftFree = !giftPaid && !!order.gift_wrap; // paket seçili ama ücretsiz
                          const hasAny = giftPaid || giftFree || !!giftNote;
                          const title = giftPaid
                            ? (giftNote ? `Hediye paketi (ücretli: ${giftPrice.toFixed(2)} TL) · Not: ${giftNote}` : `Hediye paketi (ücretli: ${giftPrice.toFixed(2)} TL)`)
                            : giftFree
                              ? (giftNote ? `Hediye paketi (ücretsiz) · Not: ${giftNote}` : "Hediye paketi (ücretsiz)")
                              : (giftNote ? `Hediye notu: ${giftNote}` : "Hediye paketi/notu yok");
                          return (
                            <button
                              onClick={() => setGiftModalOrder(order)}
                              title={title}
                              data-testid={`gift-btn-${order.id}`}
                              className={`tci-btn ${giftPaid ? 'tci-btn-pink-active' : 'tci-btn-gray'}`}
                            >
                              <span className={`text-[15px] leading-none ${giftPaid ? '' : 'grayscale opacity-50'}`}>🎁</span>
                              {/* Sadece hediye NOTU (ücretsiz) → pembe nokta belirteci */}
                              {giftNote && !giftPaid && (
                                <span className="absolute -top-1 -right-1 bg-pink-600 w-2.5 h-2.5 rounded-full border border-white" title="Hediye notu var" />
                              )}
                            </button>
                          );
                        })()}
                      </div>
                    </td>
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>

      {/* Pagination (alt — full numaralı). */}
      <Pagination
        page={page}
        total={total}
        pageSize={pageSize}
        onChange={setPage}
        onPageSizeChange={(s) => { setPageSize(s); setPage(1); }}
        variant="full"
      />

      {/* Order Detail Modal */}
      <Dialog open={detailOpen} onOpenChange={setDetailOpen}>
        <DialogContent className="max-w-3xl max-h-[90vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle className="flex items-center justify-between gap-2">
              <span>Sipariş Detayı - {selectedOrder?.order_number}</span>
              {selectedOrder && (
                <span className="flex items-center gap-2 mr-6">
                  {editMode ? (
                    <>
                      <button onClick={saveEdit} className="px-3 py-1.5 bg-gray-900 text-white text-xs rounded hover:bg-gray-800">Kaydet</button>
                      <button onClick={() => setEditMode(false)} className="px-3 py-1.5 border text-xs rounded hover:bg-gray-50">İptal</button>
                    </>
                  ) : (
                    <button onClick={startEdit} className="px-3 py-1.5 border text-xs rounded hover:bg-gray-50">Düzenle</button>
                  )}
                </span>
              )}
            </DialogTitle>
          </DialogHeader>
          
          {selectedOrder && (
            <div className="space-y-6">
              {selectedOrder.partial_cancelled && (
                <div className="flex items-center gap-2 p-3 bg-red-50 border border-red-200 rounded text-sm text-red-700">
                  <span className="relative inline-flex">
                    <span className="animate-ping absolute inline-flex h-2.5 w-2.5 rounded-full bg-red-400 opacity-75"></span>
                    <span className="relative inline-flex rounded-full h-2.5 w-2.5 bg-red-500"></span>
                  </span>
                  <span className="font-semibold">Bu siparişte iptal edilen ürün var</span>
                  <span className="text-red-500">— müşteri kargoya verilmeden önce bir/birkaç kalemi iptal etti. Kalan kalemleri kontrol edin.</span>
                </div>
              )}
              {/* Action Buttons */}
              <div className="flex gap-2 flex-wrap">
                {!(selectedOrder.invoice_issued || selectedOrder.invoice_number || selectedOrder.invoice?.invoice_number) && (
                  <button 
                    onClick={() => handleGenerateInvoice(selectedOrder.id)}
                    disabled={invoicingId === selectedOrder.id}
                    className="flex items-center gap-2 px-4 py-2 bg-blue-600 text-white text-sm rounded hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed"
                  >
                    <FileText size={16} />
                    {invoicingId === selectedOrder.id ? "Kesiliyor..." : "Fatura Kes"}
                  </button>
                )}
                {(selectedOrder.invoice_issued || selectedOrder.invoice_number || selectedOrder.invoice?.invoice_number) && (
                  <button
                    onClick={() => handleResetInvoice(selectedOrder.id)}
                    className="flex items-center gap-2 px-4 py-2 bg-red-600 text-white text-sm rounded hover:bg-red-700"
                  >
                    <Trash2 size={16} />
                    Faturayı Sıfırla
                  </button>
                )}
                {/* Kargo oluşturma (DHL E-Commerce / Manuel Kargo) ve 'Onay SMS' butonları
                    kaldırıldı — istek üzerine. Kargo takip no VARSA etiket + kargo SMS kalır. */}
                {(selectedOrder.cargo?.tracking_number || selectedOrder.cargo_tracking_number) && (
                  <>
                    {selectedOrder.platform === 'trendyol' && selectedOrder.cargo_tracking_number ? (
                      <button
                        onClick={() => handleTrendyolPrintLabel(selectedOrder.cargo_tracking_number)}
                        className="flex items-center gap-2 px-4 py-2 bg-orange-600 text-white text-sm rounded hover:bg-orange-700"
                      >
                        <Tag size={16} />
                        Trendyol Etiketi Yazdır
                      </button>
                    ) : (
                      <button
                        onClick={() => handlePrintLabel(selectedOrder.id)}
                        className="flex items-center gap-2 px-4 py-2 bg-purple-600 text-white text-sm rounded hover:bg-purple-700"
                      >
                        <Tag size={16} />
                        Etiket Yazdır
                      </button>
                    )}
                    <button
                      onClick={() => handleSendShippingSMS(selectedOrder.id)}
                      className="flex items-center gap-2 px-4 py-2 bg-orange-600 text-white text-sm rounded hover:bg-orange-700"
                    >
                      <MessageSquare size={16} />
                      Kargo SMS
                    </button>
                  </>
                )}
                <button 
                  onClick={() => window.print()}
                  className="flex items-center gap-2 px-4 py-2 border text-sm rounded hover:bg-gray-50"
                >
                  <Printer size={16} />
                  Yazdır
                </button>
                <button
                  onClick={() => handleDeleteOrder(selectedOrder.id)}
                  disabled={deletingId === selectedOrder.id}
                  className="flex items-center gap-2 px-4 py-2 bg-red-700 text-white text-sm rounded hover:bg-red-800 disabled:opacity-50 ml-auto"
                >
                  <Trash2 size={16} />
                  {deletingId === selectedOrder.id ? "Siliniyor..." : "Siparişi Sil"}
                </button>
              </div>

              {/* Kargo takip — no + link (web sitesi siparişlerinde ilk okutmada poll'dan dolar) */}
              {(selectedOrder.cargo_tracking_url || selectedOrder.cargo_tracking_link || selectedOrder.cargo_tracking_number || selectedOrder.cargo?.tracking_number || selectedOrder.cargo_gonderi_no) && (() => {
                const trackNo = selectedOrder.cargo_tracking_number || selectedOrder.cargo?.tracking_number || selectedOrder.cargo_gonderi_no || "";
                const trackUrl = selectedOrder.cargo_tracking_url || selectedOrder.cargo_tracking_link || "";
                return (
                  <div className="mt-3 p-3 border rounded-lg bg-gray-50 text-sm">
                    <div className="font-medium text-gray-700 mb-1">Kargo Takip</div>
                    {trackNo && (
                      <div className="flex items-center gap-2 flex-wrap">
                        <span className="text-gray-500">Takip No:</span>
                        <span className="font-mono">{trackNo}</span>
                        <button onClick={() => { navigator.clipboard?.writeText(trackNo); toast.success("Takip no kopyalandı"); }} className="text-xs text-blue-600 hover:underline">kopyala</button>
                      </div>
                    )}
                    {trackUrl ? (
                      <div className="flex items-center gap-2 mt-1 flex-wrap">
                        <span className="text-gray-500">Takip Linki:</span>
                        <a href={trackUrl} target="_blank" rel="noreferrer" className="text-blue-600 hover:underline break-all">{trackUrl}</a>
                        <button onClick={() => { navigator.clipboard?.writeText(trackUrl); toast.success("Takip linki kopyalandı"); }} className="text-xs text-blue-600 hover:underline whitespace-nowrap">kopyala</button>
                      </div>
                    ) : (
                      <div className="text-xs text-gray-400 mt-1">Takip linki, kargo ilk okutulduğunda otomatik oluşur.</div>
                    )}
                  </div>
                );
              })()}

              {/* Fatura — kesilmişse PDF'i otomatik göster (Trendyol'a yükleme zaten otomatik yapılır) */}
              {(selectedOrder.invoice_issued || selectedOrder.invoice_number || selectedOrder.invoice?.invoice_number || selectedOrder.invoice_pdf_url || selectedOrder.invoice_link) && (() => {
                const invNo = selectedOrder.invoice?.invoice_number || selectedOrder.invoice_number;
                const rawUrl = selectedOrder.invoice_pdf_url || selectedOrder.invoice_link || "";
                const tok = localStorage.getItem('token');
                const pdfSrc = (typeof rawUrl === 'string' && rawUrl.startsWith('http'))
                  ? rawUrl
                  : `${API}/orders/${selectedOrder.id}/invoice/print?token=${tok}`;
                return (
                  <div className="border border-green-200 bg-green-50 rounded p-4">
                    <div className="flex items-center justify-between mb-3 gap-3">
                      <div>
                        <h3 className="font-medium text-green-800">Fatura</h3>
                        {invNo && <p className="text-sm text-gray-700">No: <span className="font-medium">{invNo}</span></p>}
                      </div>
                      <a href={pdfSrc} target="_blank" rel="noreferrer" className="text-sm text-blue-600 hover:underline shrink-0">Yeni sekmede aç ↗</a>
                    </div>
                    <iframe src={pdfSrc} title="Fatura" className="w-full rounded border bg-white" style={{ height: 520 }} />
                    {/* #20: Faturada kullanılan MATRAH bilgileri (birim fiyat · iskonto · KDV) */}
                    {(() => {
                      const items = selectedOrder.items || selectedOrder.lines || [];
                      const sub = Number(selectedOrder.subtotal) || items.reduce((a, it) => a + (Number(it.unit_price ?? it.price ?? 0)) * (Number(it.quantity ?? it.qty ?? 1)), 0);
                      const disc = Number(selectedOrder.discount || selectedOrder.discount_total || 0) + Number(selectedOrder.payment_discount || 0);
                      const dr = sub > 0 ? Math.min(1, Math.max(0, disc / sub)) : 0;
                      const fmt = (n) => (Number(n) || 0).toLocaleString('tr-TR', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
                      let tI = 0, tN = 0, tK = 0;
                      const rows = items.map((it, i) => {
                        const q = Number(it.quantity ?? it.qty ?? 1);
                        const unit = Number(it.unit_price ?? it.price ?? it.list_price ?? 0);
                        const gross = unit * q, net = gross * (1 - dr), isk = gross - net;
                        const rate = Number(it.vat_rate ?? selectedOrder.vat_rate ?? 10);
                        const kdv = net * rate / (100 + rate);
                        tI += isk; tN += net; tK += kdv;
                        return { i, name: it.name || it.product_name || 'Ürün', unit, q, isk, net, rate, kdv };
                      });
                      if (!rows.length) return null;
                      return (
                        <div className="mt-3 overflow-x-auto">
                          <div className="text-xs font-semibold text-green-900 mb-1">Fatura Matrahı (birim fiyat · iskonto · KDV)</div>
                          <table className="w-full text-xs border bg-white">
                            <thead className="bg-green-100 text-green-900">
                              <tr><th className="px-2 py-1 text-left">Ürün</th><th className="px-2 py-1 text-right">Birim</th><th className="px-2 py-1 text-right">Adet</th><th className="px-2 py-1 text-right">İskonto</th><th className="px-2 py-1 text-right">Net</th><th className="px-2 py-1 text-right">KDV%</th><th className="px-2 py-1 text-right">KDV</th></tr>
                            </thead>
                            <tbody>
                              {rows.map(r => (<tr key={r.i} className="border-t"><td className="px-2 py-1">{r.name}</td><td className="px-2 py-1 text-right">{fmt(r.unit)}</td><td className="px-2 py-1 text-right">{r.q}</td><td className="px-2 py-1 text-right text-amber-700">-{fmt(r.isk)}</td><td className="px-2 py-1 text-right">{fmt(r.net)}</td><td className="px-2 py-1 text-right">%{r.rate}</td><td className="px-2 py-1 text-right">{fmt(r.kdv)}</td></tr>))}
                            </tbody>
                            <tfoot className="bg-green-50 font-semibold">
                              <tr className="border-t"><td className="px-2 py-1" colSpan={3}>Toplam</td><td className="px-2 py-1 text-right text-amber-700">-{fmt(tI)}</td><td className="px-2 py-1 text-right">{fmt(tN)}</td><td className="px-2 py-1 text-right">Matrah {fmt(tN - tK)}</td><td className="px-2 py-1 text-right">{fmt(tK)}</td></tr>
                            </tfoot>
                          </table>
                          {Number(selectedOrder.shipping_cost) > 0 && <div className="text-[11px] text-gray-500 mt-1">+ Kargo: {fmt(selectedOrder.shipping_cost)} (KDV %20, ayrı matrah)</div>}
                        </div>
                      );
                    })()}
                  </div>
                );
              })()}

              {/* Kargo Bilgileri */}
              {selectedOrder.cargo && (
                <div className="p-4 bg-blue-50 border border-blue-200 rounded">
                  <h3 className="font-medium text-blue-800 mb-2">Kargo Bilgileri</h3>
                  <p className="text-sm">Firma: <span className="font-medium">{selectedOrder.cargo.company}</span></p>
                  <p className="text-sm">Takip No: <span className="font-medium">{selectedOrder.cargo.tracking_number}</span></p>
                </div>
              )}

                            {/* Status */}
              <div className="flex items-center justify-between p-4 bg-gray-50 rounded">
                <div>
                  <p className="text-sm text-gray-500">Durum</p>
                  <span className={getStatusInfo(selectedOrder.status).class}>
                    {getStatusInfo(selectedOrder.status).label}
                  </span>
                </div>
                <Select
                  value={selectedOrder.status}
                  onValueChange={(value) => handleStatusChange(selectedOrder.id, value)}
                >
                  <SelectTrigger className="w-40">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {visibleStatusOptions.map((opt) => (
                      <SelectItem key={opt.value} value={opt.value}>{opt.label}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>

              {/* Havale: Dekont & Ödeme Onayı */}
              {((selectedOrder.payment_method && ["bank_transfer","havale","eft","havale_eft","banka_havale"].includes(String(selectedOrder.payment_method).toLowerCase())) || selectedOrder.payment_receipt) && (
                <div className="p-4 bg-amber-50 border border-amber-200 rounded">
                  <div className="flex items-center justify-between gap-3 flex-wrap">
                    <div>
                      <p className="text-sm text-gray-600">Ödeme: Havale/EFT —
                        <span className={`ml-1 font-semibold ${selectedOrder.payment_status==='paid'?'text-green-700':'text-amber-700'}`}>
                          {selectedOrder.payment_status==='paid' ? 'Ödendi' : 'Ödeme bekleniyor'}
                        </span>
                      </p>
                      {selectedOrder.payment_notified_at && (
                        <p className="text-xs text-gray-500 mt-1">Dekont yüklendi: {new Date(selectedOrder.payment_notified_at).toLocaleString('tr-TR')}</p>
                      )}
                      {selectedOrder.payment_receipt?.note && (
                        <p className="text-xs text-gray-500 mt-0.5">Not: {selectedOrder.payment_receipt.note}</p>
                      )}
                      {selectedOrder.payment_status !== 'paid' && selectedOrder.payment_reminder_last_at && (
                        <p className="text-xs text-blue-600 mt-1">
                          Son hatırlatma: {new Date(selectedOrder.payment_reminder_last_at).toLocaleString('tr-TR')}
                          {selectedOrder.payment_reminder_count ? ` (${selectedOrder.payment_reminder_count} kez)` : ''}
                        </p>
                      )}
                    </div>
                    <div className="flex items-center gap-2">
                      {selectedOrder.payment_receipt && (
                        <button onClick={() => viewReceipt(selectedOrder.id)} className="px-3 py-2 bg-white border rounded text-sm font-medium hover:bg-gray-50">Dekontu Görüntüle</button>
                      )}
                      {selectedOrder.payment_status !== 'paid' && (
                        <button onClick={() => sendPaymentReminder(selectedOrder.id)} disabled={reminderSending} className="px-3 py-2 bg-blue-600 text-white rounded text-sm font-semibold hover:bg-blue-700 disabled:opacity-50">
                          {reminderSending ? 'Gönderiliyor…' : '🔔 Ödeme Hatırlatma SMS\'i Gönder'}
                        </button>
                      )}
                      {selectedOrder.payment_status !== 'paid' && (
                        <button onClick={() => approvePayment(selectedOrder.id)} className="px-3 py-2 bg-green-600 text-white rounded text-sm font-semibold hover:bg-green-700">Ödemeyi Onayla</button>
                      )}
                    </div>
                  </div>
                </div>
              )}

              {/* Customer Info */}
              <div className="grid md:grid-cols-2 gap-4">
                <div className="p-4 border rounded">
                  <h3 className="font-medium mb-3">{editMode ? "Müşteri Bilgileri" : "Teslimat Adresi"}</h3>
                  {editMode ? (
                    <div className="space-y-2">
                      <div className="grid grid-cols-2 gap-2">
                        <input className="border rounded px-2 py-1 text-sm w-full" placeholder="Ad" value={editData.shipping_address.first_name || ""} onChange={(e) => setSA("first_name", e.target.value)} />
                        <input className="border rounded px-2 py-1 text-sm w-full" placeholder="Soyad" value={editData.shipping_address.last_name || ""} onChange={(e) => setSA("last_name", e.target.value)} />
                      </div>
                      <input className="border rounded px-2 py-1 text-sm w-full" placeholder="E-posta" value={editData.shipping_address.email || ""} onChange={(e) => setSA("email", e.target.value)} />
                      <input className="border rounded px-2 py-1 text-sm w-full" placeholder="Telefon" value={editData.shipping_address.phone || ""} onChange={(e) => setSA("phone", e.target.value)} />
                    </div>
                  ) : (() => {
                    const sa = selectedOrder.shipping_address || {};
                    const Row = ({ l, v, mono }) => v ? (
                      <div className="flex gap-2 text-sm">
                        <span className="text-gray-500 w-28 shrink-0 font-medium">{l}:</span>
                        <span className={mono ? "font-mono" : ""}>{v}</span>
                      </div>
                    ) : null;
                    const addr = [sa.address, [sa.district, sa.city].filter(Boolean).join(" / ")].filter(Boolean).join(" — ");
                    return (
                      <div className="space-y-1.5">
                        <Row l="Ad-Soyad" v={[sa.first_name, sa.last_name].filter(Boolean).join(" ")} />
                        <Row l="Adres" v={addr} />
                        <Row l="Telefon" v={sa.phone} />
                        <Row l="E-Posta" v={sa.email} />
                      </div>
                    );
                  })()}
                </div>
                <div className="p-4 border rounded">
                  <h3 className="font-medium mb-3">{editMode ? "Teslimat Adresi" : "Fatura Adresi"}</h3>
                  {editMode ? (
                    <div className="space-y-2">
                      <textarea rows={2} className="border rounded px-2 py-1 text-sm w-full resize-none" placeholder="Açık adres" value={editData.shipping_address.address || ""} onChange={(e) => setSA("address", e.target.value)} />
                      <div className="grid grid-cols-2 gap-2">
                        <input className="border rounded px-2 py-1 text-sm w-full" placeholder="İlçe" value={editData.shipping_address.district || ""} onChange={(e) => setSA("district", e.target.value)} />
                        <input className="border rounded px-2 py-1 text-sm w-full" placeholder="İl" value={editData.shipping_address.city || ""} onChange={(e) => setSA("city", e.target.value)} />
                      </div>
                    </div>
                  ) : (() => {
                    // Trendyol "Fatura Bilgileri" düzeni: Ad-Soyad/Ünvan, Adres, E-Fatura Mükellefi
                    // + (doluysa) VKN/Vergi Dairesi ve Fatura No — teslimatla YAN YANA tek kutu.
                    const sa = selectedOrder.shipping_address || {};
                    const bi = selectedOrder.billing_info || {};
                    const ba = selectedOrder.billing_address || {};
                    const company = bi.company_name || ba.company_name || "";
                    const taxOffice = bi.tax_office || ba.tax_office || "";
                    const taxNumber = bi.tax_number || ba.tax_number || ba.tax_no || ba.vkn || "";
                    const isCorp = bi.is_corporate || ba.is_corporate || !!(company || taxNumber || taxOffice);
                    const invNo = selectedOrder.invoice?.invoice_number || selectedOrder.invoice_number || "";
                    const eInv = bi.e_invoice_user === true ? "Evet" : (isCorp && String(taxNumber).length === 10 ? "Kesimde sorgulanır" : "Hayır");
                    const bAddr = [ba.address, [ba.district, ba.city].filter(Boolean).join(" / ")].filter(Boolean).join(" — ")
                      || [sa.address, [sa.district, sa.city].filter(Boolean).join(" / ")].filter(Boolean).join(" — ");
                    const bName = company || [ba.first_name || sa.first_name, ba.last_name || sa.last_name].filter(Boolean).join(" ");
                    const Row = ({ l, v, mono }) => v ? (
                      <div className="flex gap-2 text-sm">
                        <span className="text-gray-500 w-36 shrink-0 font-medium">{l}:</span>
                        <span className={mono ? "font-mono" : ""}>{v}</span>
                      </div>
                    ) : null;
                    return (
                      <div className="space-y-1.5">
                        <Row l={isCorp ? "Ünvan" : "Ad-Soyad"} v={bName} />
                        <Row l="Adres" v={bAddr} />
                        <Row l="E-Fatura Mükellefi" v={eInv} />
                        <Row l="VKN / TCKN" v={taxNumber} mono />
                        <Row l="Vergi Dairesi" v={taxOffice} />
                        <Row l="Fatura No" v={invNo || "Kesilmedi"} mono />
                      </div>
                    );
                  })()}
                </div>
              </div>

              {/* Kurumsal Fatura Bilgileri */}
              {editMode ? (
                <div className="p-4 border rounded bg-amber-50 border-amber-200">
                  <div className="flex items-center justify-between mb-2">
                    <h3 className="font-medium flex items-center gap-1 text-amber-900">🏢 Kurumsal Fatura Bilgileri</h3>
                    <label className="flex items-center gap-1 text-xs text-amber-900 select-none cursor-pointer">
                      <input type="checkbox" checked={!!editData.billing_info?.is_corporate} onChange={(e) => setBI("is_corporate", e.target.checked)} />
                      Kurumsal fatura talebi
                    </label>
                  </div>
                  <div className="grid grid-cols-1 md:grid-cols-3 gap-2">
                    <input className="border rounded px-2 py-1 text-sm w-full" placeholder="Ünvan (firma adı)" value={editData.billing_info?.company_name || ""} onChange={(e) => setBI("company_name", e.target.value)} />
                    <input className="border rounded px-2 py-1 text-sm w-full" placeholder="VKN (10) / TCKN (11)" inputMode="numeric" value={editData.billing_info?.tax_number || ""} onChange={(e) => setBI("tax_number", e.target.value.replace(/\D/g, ""))} />
                    <input className="border rounded px-2 py-1 text-sm w-full" placeholder="Vergi Dairesi" value={editData.billing_info?.tax_office || ""} onChange={(e) => setBI("tax_office", e.target.value)} />
                  </div>
                  <p className="text-[11px] text-amber-800 mt-1">VKN 10 hane ise kesimde Doğan'a e-Fatura mükellefiyeti sorulur; mükellef değilse e-Arşiv kesilir. (Bu siparişte VKN'yi girip Kaydet → sonra Fatura Kes.)</p>
                </div>
              ) : null}
              {/* Fatura bilgileri Müşteri Bilgileri kutusuna taşındı (kullanıcı isteği: 2 kutu yeterli) */}

              {/* Son fatura hatası (kesim başarısızsa) */}
              {!editMode && selectedOrder.invoice_last_error && !selectedOrder.invoice_issued && (
                <div className="p-3 border rounded bg-red-50 border-red-200 text-sm text-red-800">
                  <span className="font-medium">Son fatura hatası:</span> {selectedOrder.invoice_last_error}
                  {selectedOrder.invoice_last_error_at && (
                    <span className="text-xs text-red-500"> ({formatDate(selectedOrder.invoice_last_error_at)})</span>
                  )}
                </div>
              )}

              {/* Attribution / Order Source */}
              {selectedOrder.attribution && (
                <div className="p-4 border rounded bg-gradient-to-r from-indigo-50 to-purple-50 border-indigo-200">
                  <h3 className="font-medium mb-2 flex items-center gap-1 text-indigo-900">
                    📡 Sipariş Kaynağı
                  </h3>
                  <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-sm">
                    <div>
                      <div className="text-xs text-gray-500 uppercase">Kanal</div>
                      <div className="font-semibold text-indigo-900">{selectedOrder.attribution.channel || "direct"}</div>
                    </div>
                    <div>
                      <div className="text-xs text-gray-500 uppercase">Kaynak (utm_source)</div>
                      <div className="font-medium">{selectedOrder.attribution.source || "—"}</div>
                    </div>
                    <div>
                      <div className="text-xs text-gray-500 uppercase">Medium</div>
                      <div className="font-medium">{selectedOrder.attribution.medium || "—"}</div>
                    </div>
                    <div>
                      <div className="text-xs text-gray-500 uppercase">Kampanya</div>
                      <div className="font-medium">{selectedOrder.attribution.campaign || "—"}</div>
                    </div>
                    {selectedOrder.attribution.referrer && (
                      <div className="col-span-2 md:col-span-4 text-xs text-gray-500 break-all">
                        Referrer: <span className="text-gray-700">{selectedOrder.attribution.referrer}</span>
                      </div>
                    )}
                    {selectedOrder.attribution.landing_page && (
                      <div className="col-span-2 md:col-span-4 text-xs text-gray-500 break-all">
                        İniş Sayfası: <span className="text-gray-700">{selectedOrder.attribution.landing_page}</span>
                      </div>
                    )}
                    {selectedOrder.attribution.device && (
                      <div className="text-xs text-gray-500">Cihaz: <span className="text-gray-700 font-medium">{selectedOrder.attribution.device}</span></div>
                    )}
                  </div>
                </div>
              )}

              {/* Items */}
              <div className="border rounded">
                <h3 className="font-medium p-4 border-b">Sipariş Kalemleri</h3>
                <div className="divide-y">
                  {(editMode ? editData.items : (selectedOrder.lines?.length > 0 ? selectedOrder.lines : selectedOrder.items))?.map((item, i) => {
                    // Ürüne tıklayınca aç: slug varsa storefront ürün sayfası, yoksa isim/barkodla arama (yeni sekme).
                    const _pname = item.productName || item.product_name || item.name || "Ürün";
                    const _pslug = item.slug || item.product_slug;
                    const _phref = _pslug
                      ? `/${_pslug}`
                      : (_pname && _pname !== "Ürün"
                          ? `/arama?q=${encodeURIComponent(_pname)}`
                          : (item.barcode ? `/arama?q=${encodeURIComponent(item.barcode)}` : null));
                    return (
                    <div key={i} className="flex items-start justify-between p-4 gap-4">
                      <div className="flex items-start gap-4 flex-1 min-w-0">
                        {item.image && (
                          _phref
                            ? <a href={_phref} target="_blank" rel="noopener noreferrer" className="shrink-0" title="Ürünü aç"><img src={item.image} alt="" className="w-16 h-20 object-cover bg-gray-100 rounded shrink-0 hover:opacity-80 transition-opacity" /></a>
                            : <img src={item.image} alt="" className="w-16 h-20 object-cover bg-gray-100 rounded shrink-0" />
                        )}
                        {editMode ? (
                          <div className="flex-1 space-y-1 min-w-0">
                            <input className="border rounded px-2 py-1 text-sm w-full font-medium" placeholder="Ürün adı" value={item.productName ?? item.product_name ?? item.name ?? ""} onChange={(e) => setItem(i, _nameKeyOf(item), e.target.value)} />
                            <div className="grid grid-cols-2 gap-1">
                              <input className="border rounded px-2 py-1 text-xs w-full" placeholder="Beden" value={item.size ?? ""} onChange={(e) => setItem(i, "size", e.target.value)} />
                              <input type="number" min="1" className="border rounded px-2 py-1 text-xs w-full" placeholder="Adet" value={item.quantity ?? 1} onChange={(e) => setItem(i, "quantity", e.target.value)} />
                            </div>
                          </div>
                        ) : (
                          <div>
                            {_phref ? (
                              <a href={_phref} target="_blank" rel="noopener noreferrer" className="font-medium text-gray-900 hover:text-blue-600 hover:underline inline-flex items-center gap-1" title="Ürünü aç">
                                {_pname} <ExternalLink size={12} className="opacity-60" />
                              </a>
                            ) : (
                              <p className="font-medium">{_pname}</p>
                            )}
                            {item.size && <p className="text-sm text-gray-500">Beden: {item.size}</p>}
                            <p className="text-sm text-gray-500">Adet: {item.quantity}</p>
                            {item.brand && <p className="text-sm text-gray-500">Marka: {item.brand}</p>}
                            {(item.vat_rate !== undefined && item.vat_rate !== null && item.vat_rate !== "") && (
                              <p className="text-sm text-gray-500">KDV: %{item.vat_rate}</p>
                            )}
                            {item.barcode && <p className="text-xs text-gray-400">Barkod: {item.barcode}</p>}
                          </div>
                        )}
                      </div>
                      <div className="text-right text-sm min-w-[130px]">
                        {editMode ? (
                          <div className="space-y-1">
                            <input type="number" step="0.01" className="border rounded px-2 py-1 text-xs w-full text-right" placeholder="Liste birim" value={item.unit_price ?? ""} onChange={(e) => setItem(i, "unit_price", e.target.value)} />
                            <input type="number" step="0.01" className="border rounded px-2 py-1 text-xs w-full text-right" placeholder="İskonto" value={item.discount_amount ?? ""} onChange={(e) => setItem(i, "discount_amount", e.target.value)} />
                            <input type="number" step="0.01" className="border rounded px-2 py-1 text-xs w-full text-right" placeholder="Fiyat" value={item.price ?? ""} onChange={(e) => setItem(i, "price", e.target.value)} />
                          </div>
                        ) : (
                          <>
                            {item.unit_price > 0 && typeof item.unit_price !== 'undefined' && (
                              <p className="text-gray-400 line-through text-xs">{item.unit_price.toFixed(2)} TL</p>
                            )}
                            {item.discount_amount > 0 && (
                              <p className="text-[#8b1e3f] text-xs font-medium">İndirim: -{item.discount_amount.toFixed(2)} TL</p>
                            )}
                            <p className="font-semibold text-gray-900">
                              {((item.price || item.amount) * (item.quantity === 1 ? 1 : (item.price ? item.quantity : 1))).toFixed(2)} TL
                            </p>
                          </>
                        )}
                      </div>
                    </div>
                    );
                  })}
                </div>
              </div>

              {/* İADE DÖKÜMÜ — hangi kalem iade edildi / müşteride kaldı (ayrı tutarlarla).
                  Backend get_order.return_breakdown'dan gelir; yalnız iade olan siparişlerde. */}
              {!editMode && selectedOrder?.return_breakdown && (
                <div className="border rounded border-amber-300 bg-amber-50/40">
                  <h3 className="font-medium p-4 border-b border-amber-200 flex items-center gap-2">
                    <span>İade Dökümü</span>
                    {selectedOrder.return_breakdown.is_partial && (
                      <span className="text-xs bg-amber-200 text-amber-900 px-2 py-0.5 rounded-full font-medium">Kısmi İade</span>
                    )}
                  </h3>
                  <div className="grid grid-cols-1 md:grid-cols-2 divide-y md:divide-y-0 md:divide-x divide-amber-200">
                    {/* İade edilenler */}
                    <div className="p-4">
                      <div className="flex items-center justify-between mb-2">
                        <p className="text-sm font-semibold text-[#8b1e3f]">İade Edilen</p>
                        <p className="text-sm font-semibold text-[#8b1e3f]">{Number(selectedOrder.return_breakdown.returned_total || 0).toFixed(2)} TL</p>
                      </div>
                      {(selectedOrder.return_breakdown.returned_items || []).length === 0 ? (
                        <p className="text-xs text-gray-400">—</p>
                      ) : (
                        <div className="space-y-2">
                          {selectedOrder.return_breakdown.returned_items.map((it, i) => (
                            <div key={i} className="flex items-start gap-2">
                              {it.image && <img src={it.image} alt="" className="w-10 h-12 object-cover bg-gray-100 rounded shrink-0" />}
                              <div className="flex-1 min-w-0">
                                <p className="text-sm font-medium truncate">{it.name}</p>
                                <p className="text-xs text-gray-500">
                                  {it.size ? `Beden: ${it.size} · ` : ""}{it.quantity} adet
                                </p>
                              </div>
                              <p className="text-sm font-medium text-[#8b1e3f] whitespace-nowrap">{Number(it.amount || 0).toFixed(2)} TL</p>
                            </div>
                          ))}
                        </div>
                      )}
                      {selectedOrder.return_breakdown.refund_amount != null && (
                        <div className="mt-3 pt-2 border-t border-amber-200 flex justify-between text-sm">
                          <span className="text-gray-600">Müşteriye iade edilen</span>
                          <span className="font-semibold">{Number(selectedOrder.return_breakdown.refund_amount).toFixed(2)} TL</span>
                        </div>
                      )}
                    </div>
                    {/* Müşteride kalanlar */}
                    <div className="p-4">
                      <div className="flex items-center justify-between mb-2">
                        <p className="text-sm font-semibold text-green-700">Müşteride Kalan</p>
                        <p className="text-sm font-semibold text-green-700">{Number(selectedOrder.return_breakdown.kept_total || 0).toFixed(2)} TL</p>
                      </div>
                      {(selectedOrder.return_breakdown.kept_items || []).length === 0 ? (
                        <p className="text-xs text-gray-400">Tüm sipariş iade edildi</p>
                      ) : (
                        <div className="space-y-2">
                          {selectedOrder.return_breakdown.kept_items.map((it, i) => (
                            <div key={i} className="flex items-start gap-2">
                              {it.image && <img src={it.image} alt="" className="w-10 h-12 object-cover bg-gray-100 rounded shrink-0" />}
                              <div className="flex-1 min-w-0">
                                <p className="text-sm font-medium truncate">{it.name}</p>
                                <p className="text-xs text-gray-500">
                                  {it.size ? `Beden: ${it.size} · ` : ""}{it.quantity} adet
                                </p>
                              </div>
                              <p className="text-sm font-medium text-green-700 whitespace-nowrap">{Number(it.amount || 0).toFixed(2)} TL</p>
                            </div>
                          ))}
                        </div>
                      )}
                    </div>
                  </div>
                </div>
              )}

              {/* Müşteri Yolculuğu / Attribution Hunisi — düzenleme modunda gizle */}
              {!editMode && selectedOrder?.id && (
                <JourneyFunnel orderId={selectedOrder.id} />
              )}

              {/* Totals */}
              <div className="p-4 bg-gray-50 rounded space-y-2">
                {editMode ? (
                  <>
                    <div className="flex justify-between items-center text-sm gap-2"><span>Ara Toplam</span><input type="number" step="0.01" className="border rounded px-2 py-1 text-sm w-32 text-right" value={editData.subtotal} onChange={(e) => setField("subtotal", e.target.value)} /></div>
                    <div className="flex justify-between items-center text-sm gap-2"><span>Kargo</span><input type="number" step="0.01" className="border rounded px-2 py-1 text-sm w-32 text-right" value={editData.shipping_cost} onChange={(e) => setField("shipping_cost", e.target.value)} /></div>
                    <div className="flex justify-between items-center text-sm gap-2"><span>İndirim</span><input type="number" step="0.01" className="border rounded px-2 py-1 text-sm w-32 text-right" value={editData.discount} onChange={(e) => setField("discount", e.target.value)} /></div>
                    <div className="flex justify-between items-center font-medium text-lg pt-2 border-t gap-2"><span>Toplam</span><input type="number" step="0.01" className="border rounded px-2 py-1 text-base w-32 text-right font-medium" value={editData.total} onChange={(e) => setField("total", e.target.value)} /></div>
                  </>
                ) : (
                  <>
                    <div className="flex justify-between text-sm">
                      <span>Ara Toplam</span>
                      <span>{selectedOrder.subtotal?.toFixed(2)} TL</span>
                    </div>
                    {/* İndirimler AYRI AYRI — kampanya / kupon / havale (hangi indirim uygulandı görünsün) */}
                    {Array.isArray(selectedOrder.discount_breakdown) && selectedOrder.discount_breakdown.length > 0 ? (
                      selectedOrder.discount_breakdown.map((d, i) => (
                        <div key={i} className="flex justify-between text-sm text-green-600">
                          <span>{d.label}{d.code ? ` · ${d.code}` : ""}</span>
                          <span>-{Number(d.amount || 0).toFixed(2)} TL</span>
                        </div>
                      ))
                    ) : (
                      selectedOrder.discount > 0 && (
                        <div className="flex justify-between text-sm text-green-600">
                          <span>İndirim{selectedOrder.coupon_code ? ` · ${selectedOrder.coupon_code}` : ""}</span>
                          <span>-{selectedOrder.discount?.toFixed(2)} TL</span>
                        </div>
                      )
                    )}
                    <div className="flex justify-between text-sm">
                      <span>Kargo</span>
                      <span>{Number(selectedOrder.shipping_cost || 0) === 0 ? "Ücretsiz" : `${selectedOrder.shipping_cost?.toFixed(2)} TL`}</span>
                    </div>
                    {/* Hediye paketi / puan da dökümde görünsün — listede var, detayda yoktu */}
                    {selectedOrder.gift_wrap && Number(selectedOrder.gift_wrap_price) > 0 && (
                      <div className="flex justify-between text-sm">
                        <span>🎁 Hediye Paketi</span>
                        <span>{Number(selectedOrder.gift_wrap_price).toFixed(2)} TL</span>
                      </div>
                    )}
                    {Number(selectedOrder.points_used) > 0 && (
                      <div className="flex justify-between text-sm text-green-600">
                        <span>Puan Kullanımı</span>
                        <span>-{Number(selectedOrder.points_used).toFixed(2)} TL</span>
                      </div>
                    )}
                    <div className="flex justify-between font-medium text-lg pt-2 border-t">
                      <span>Toplam{(() => {
                        const iyz = selectedOrder.iyzico_retrieve_response || {};
                        const inst = parseInt(iyz.installment || selectedOrder.installment || 1, 10) || 1;
                        return inst > 1 ? <span className="text-xs font-normal text-gray-400"> (peşin)</span> : null;
                      })()}</span>
                      <span>{selectedOrder.total?.toFixed(2)} TL</span>
                    </div>
                    {(() => {
                      // Taksitli: müşteriden çekilen (vade farkı dahil) tutar — fatura bunu yansıtır.
                      const iyz = selectedOrder.iyzico_retrieve_response || {};
                      const charged = Number(iyz.paidPrice) || 0;
                      const inst = parseInt(iyz.installment || selectedOrder.installment || 1, 10) || 1;
                      const vf = charged > 0 ? Math.round((charged - (selectedOrder.total || 0)) * 100) / 100 : 0;
                      if (inst > 1 && vf > 0.01) return (
                        <div className="mt-1 rounded-md bg-amber-50 border border-amber-200 px-3 py-2 text-sm">
                          <div className="flex justify-between text-amber-700"><span>Taksit vade farkı ({inst} taksit)</span><span>+{vf.toFixed(2)} TL</span></div>
                          <div className="flex justify-between font-semibold text-amber-900 mt-0.5"><span>💳 Müşteriden çekilen (fatura tutarı)</span><span>{charged.toFixed(2)} TL</span></div>
                        </div>
                      );
                      return null;
                    })()}
                  </>
                )}
              </div>

              <OrderMarketplaceInfo order={selectedOrder} />
              <OrderPaymentDetail order={selectedOrder} />
              <OrderEventsLog orderId={selectedOrder.id} />
            </div>
          )}
        </DialogContent>
      </Dialog>

      {/* Ship Order Modal */}
      <Dialog open={shipModalOpen} onOpenChange={setShipModalOpen}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>Siparişi Kargoya Ver</DialogTitle>
          </DialogHeader>
          <div className="space-y-4">
            <div>
              <label className="block text-sm font-medium mb-1">Kargo Firması</label>
              <select
                value={selectedCargo}
                onChange={(e) => setSelectedCargo(e.target.value)}
                className="w-full border px-3 py-2 rounded"
              >
                {cargoCompanies.map(c => (
                  <option key={c.value} value={c.value}>{c.label}</option>
                ))}
              </select>
            </div>
            <div>
              <label className="block text-sm font-medium mb-1">Takip Numarası</label>
              <input
                type="text"
                value={trackingNumber}
                onChange={(e) => setTrackingNumber(e.target.value)}
                placeholder="Kargo takip numarasını girin"
                className="w-full border px-3 py-2 rounded"
              />
            </div>
            <div className="flex gap-2 justify-end">
              <button
                onClick={() => setShipModalOpen(false)}
                className="px-4 py-2 border rounded hover:bg-gray-50"
              >
                İptal
              </button>
              <button
                onClick={handleShipOrder}
                className="px-4 py-2 bg-green-600 text-white rounded hover:bg-green-700"
              >
                Kargoya Ver
              </button>
            </div>
          </div>
        </DialogContent>
      </Dialog>

      {/* FAZ 1 B3 - Admin Note Modal (Havale takip için) */}
      <Dialog open={noteModalOpen} onOpenChange={(o) => { setNoteModalOpen(o); if (!o) setNoteTargetOrder(null); }}>
        <DialogContent data-testid="order-note-modal">
          <DialogHeader>
            <DialogTitle>Sipariş Notu {noteTargetOrder?.order_number ? `- ${noteTargetOrder.order_number}` : ""}</DialogTitle>
          </DialogHeader>
          <div className="space-y-4 pt-2">
            {/* MÜŞTERİ NOTU — müşterinin checkout'ta girdiği not (salt okunur).
                NOT: Hediye paketi notu buraya AKTARILMAZ; o kendi 🎁 alanında gösterilir. */}
            {noteTargetOrder?.notes && noteTargetOrder.notes.trim() ? (
              <div className="bg-blue-50 border-l-4 border-blue-400 p-3 rounded">
                <p className="text-xs font-bold text-blue-700 uppercase tracking-wider mb-1">Müşteri Notu</p>
                <p className="text-sm text-gray-800 whitespace-pre-wrap">{noteTargetOrder.notes}</p>
              </div>
            ) : (
              <div className="bg-gray-50 border-l-4 border-gray-200 p-3 rounded">
                <p className="text-xs font-bold text-gray-400 uppercase tracking-wider">Müşteri Notu</p>
                <p className="text-sm text-gray-400">Müşteri not girmemiş.</p>
              </div>
            )}
            {/* PERSONEL NOTLARI — kim, ne zaman girdiği görünür */}
            {noteTargetOrder?.admin_notes?.length > 0 && (
              <div className="space-y-2 max-h-[240px] overflow-y-auto">
                <p className="text-xs font-bold text-gray-500 uppercase tracking-wider">Personel Notları</p>
                {noteTargetOrder.admin_notes.map(n => (
                  <div key={n.id || n.at} className="bg-yellow-50 border-l-4 border-yellow-400 p-2 rounded text-sm">
                    <p className="text-gray-800">{n.text}</p>
                    <p className="text-[10px] text-gray-500 mt-1">{n.by || "Personel"} · {new Date(n.at).toLocaleString('tr-TR')}</p>
                  </div>
                ))}
              </div>
            )}
            <div>
              <label className="block text-sm font-medium mb-1">Personel Notu Ekle</label>
              <textarea
                value={noteText}
                onChange={(e) => setNoteText(e.target.value)}
                placeholder="Örn: Müşteri 12:00'de havale attığını bildirdi, 14:00 sonrası kontrol et..."
                className="w-full border rounded-lg p-3 text-sm min-h-[120px]"
                data-testid="note-textarea"
              />
            </div>
            <div className="flex justify-end gap-2 pt-2 border-t">
              <button onClick={() => setNoteModalOpen(false)} className="px-4 py-2 border rounded hover:bg-gray-50 text-sm">İptal</button>
              <button
                onClick={saveNote}
                disabled={savingNote || !noteText.trim()}
                data-testid="save-note-btn"
                className="px-4 py-2 bg-yellow-500 text-white rounded hover:bg-yellow-600 disabled:opacity-50 text-sm font-bold"
              >
                {savingNote ? "Kaydediliyor..." : "Not Ekle"}
              </button>
            </div>
          </div>
        </DialogContent>
      </Dialog>

      {/* 🎁 Hediye Paketi modal'ı — notlardan AYRI. Ücretli paket + hediye notu burada. */}
      <Dialog open={!!giftModalOrder} onOpenChange={(o) => { if (!o) setGiftModalOrder(null); }}>
        <DialogContent data-testid="order-gift-modal">
          <DialogHeader>
            <DialogTitle>🎁 Hediye Paketi {giftModalOrder?.order_number ? `- ${giftModalOrder.order_number}` : ""}</DialogTitle>
          </DialogHeader>
          <div className="space-y-3 pt-2">
            {Number(giftModalOrder?.gift_wrap_price || 0) > 0 ? (
              <div className="bg-pink-50 border-l-4 border-pink-500 p-3 rounded">
                <p className="text-sm font-semibold text-pink-700">Hediye paketi alındı (ücretli)</p>
                <p className="text-xs text-pink-600 mt-0.5">Ücret: {Number(giftModalOrder.gift_wrap_price).toFixed(2)} TL</p>
              </div>
            ) : giftModalOrder?.gift_wrap ? (
              <div className="bg-amber-50 border-l-4 border-amber-400 p-3 rounded">
                <p className="text-sm font-semibold text-amber-700">Hediye paketi seçili (ücretsiz)</p>
                <p className="text-xs text-amber-600 mt-0.5">Ek ücret alınmadı.</p>
              </div>
            ) : (
              <div className="bg-gray-50 border-l-4 border-gray-200 p-3 rounded">
                <p className="text-sm text-gray-500">Bu siparişte ücretli hediye paketi yok{giftModalOrder?.gift_note?.trim() ? " — yalnızca hediye notu var" : ""}.</p>
              </div>
            )}
            {giftModalOrder?.gift_note?.trim() && (
              <div className="border rounded p-3">
                <p className="text-xs font-bold text-gray-500 uppercase tracking-wider mb-1">Hediye Notu (müşteri)</p>
                <p className="text-sm text-gray-800 whitespace-pre-wrap">{giftModalOrder.gift_note}</p>
              </div>
            )}
          </div>
          <div className="flex justify-end pt-3 border-t mt-3">
            <button onClick={() => setGiftModalOrder(null)} className="px-4 py-2 border rounded hover:bg-gray-50 text-sm">Kapat</button>
          </div>
        </DialogContent>
      </Dialog>

      {/* Manuel (kısmi) iade pop-up — kalem seç → seçilmeyenler siparişte kalır */}
      <Dialog open={returnModal.open} onOpenChange={(o) => !o && setReturnModal((m) => ({ ...m, open: false }))}>
        <DialogContent className="max-w-lg max-h-[90vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle>
              {returnModal.status === "refunded" ? "İade Bedeli Ödendi" : "İade Al"}
              {returnModal.order?.order_number ? ` — ${returnModal.order.order_number}` : ""}
            </DialogTitle>
          </DialogHeader>
          <div className="space-y-3">
            <p className="text-sm text-gray-600">
              İade edilecek kalemleri seçin. <b>Seçilmeyenler siparişte kalır</b> (sipariş açık).
              Tüm kalemler seçilirse sipariş tamamen iade alanına taşınır.
            </p>
            <div className="border rounded-lg divide-y">
              {(returnModal.order?.items || []).map((it, i) => {
                const checked = !!returnModal.selected[i];
                return (
                  <label key={i} className={`flex items-center gap-3 px-3 py-2 cursor-pointer ${checked ? "bg-orange-50" : ""}`}>
                    <input type="checkbox" checked={checked} onChange={() => toggleReturnItem(i)} className="w-4 h-4 accent-orange-600" />
                    <span className="flex-1 text-sm">
                      <span className="font-medium">{it.name || it.product_name || "Ürün"}</span>
                      {(it.size || it.color) ? <span className="text-gray-500"> · {[it.size, it.color].filter(Boolean).join(" / ")}</span> : null}
                      <span className="text-gray-500"> · {it.quantity || 1} adet</span>
                    </span>
                    <span className="text-sm font-semibold text-gray-700">
                      {((Number(it.price || it.unit_price || 0)) * (Number(it.quantity) || 1)).toLocaleString("tr-TR")} ₺
                    </span>
                  </label>
                );
              })}
            </div>
            <textarea value={returnModal.reason} onChange={(e) => setReturnModal((m) => ({ ...m, reason: e.target.value }))}
              placeholder="İade nedeni (opsiyonel)" rows={2}
              className="w-full border rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-orange-300" />
            <div className="flex justify-between items-center pt-1">
              <span className="text-xs text-gray-500">
                {Object.values(returnModal.selected).filter(Boolean).length} / {(returnModal.order?.items || []).length} kalem seçili
              </span>
              <div className="flex gap-2">
                <button onClick={() => setReturnModal((m) => ({ ...m, open: false }))}
                  className="px-4 py-2 text-sm rounded-lg border border-gray-300 hover:bg-gray-50">Vazgeç</button>
                <button onClick={submitReturn} disabled={returnModal.busy}
                  className="px-4 py-2 text-sm rounded-lg bg-orange-600 text-white font-medium hover:bg-orange-700 disabled:opacity-50">
                  {returnModal.busy ? "İşleniyor..." : "İadeyi Onayla"}
                </button>
              </div>
            </div>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
