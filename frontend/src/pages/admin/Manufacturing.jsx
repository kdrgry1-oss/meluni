import { useState, useEffect, Fragment } from "react";
import { useNavigate } from "react-router-dom";
import axios from "axios";
import { toast } from "sonner";
import {
  Factory, Plus, ChevronRight, ChevronLeft, Save, Trash2, Edit, X, Package, CheckCircle2,
  Image as ImageIcon,
} from "lucide-react";
import { resolveColor, needsBorder, MULTI_GRADIENT } from "../../lib/colorMap";

// Renk adı → swatch stili ({type:"solid"|"multi"}|null döner; hex DEĞİL — o yüzden burada çözülür)
function _swatchStyle(name) {
  const rc = resolveColor(name || "");
  if (rc?.type === "solid") {
    return { background: rc.value, ...(needsBorder(rc.value) ? { border: "1px solid #d1d5db" } : {}) };
  }
  if (rc?.type === "multi") return { background: MULTI_GRADIENT };
  return { background: "#e5e7eb", border: "1px solid #d1d5db" };  // bilinmeyen renk → gri
}
import {
  Dialog, DialogContent, DialogHeader, DialogTitle,
} from "../../components/ui/dialog";

// Renk/Beden seçim penceresi için hazır listeler (tıkla-seç; özel değer de eklenebilir)
const PRESET_COLORS = ["Siyah", "Beyaz", "Ekru", "Bej", "Taş", "Vizon", "Kahve", "Camel",
  "Lacivert", "Mavi", "Buz Mavi", "Kırmızı", "Bordo", "Yeşil", "Haki", "Mint",
  "Gri", "Antrasit", "Pembe", "Pudra", "Lila", "Mor", "Sarı", "Turuncu"];
const PRESET_SIZES = ["XS", "S", "M", "L", "XL", "XXL", "XS/S", "M/L", "STD",
  "34", "36", "38", "40", "42", "44", "46", "48"];

function _plus21(dateStr) {
  // Tahmini teslim = sipariş tarihi + 21 gün (kullanıcı isteği)
  try {
    const d = new Date(dateStr);
    d.setDate(d.getDate() + 21);
    return d.toISOString().substring(0, 10);
  } catch { return ""; }
}

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;
// Yüklenen görsel URL'i göreli ise (/api/upload/files/...) backend origin'iyle tamamla
const _imgUrl = (u) => (!u ? "" : String(u).startsWith("http") ? u : `${process.env.REACT_APP_BACKEND_URL}${String(u).startsWith("/") ? "" : "/"}${u}`);

const STAGE_COLORS = {
  siparis_dosyasi: "bg-slate-100 text-slate-700",
  kumas_okeyi: "bg-blue-100 text-blue-700",
  anlasma: "bg-slate-100 text-slate-700",
  numune_hazirlaniyor: "bg-amber-100 text-amber-700",
  numune_onaylandi: "bg-green-100 text-green-700",
  kumas_siparisi: "bg-blue-100 text-blue-700",
  kumas_teslim: "bg-indigo-100 text-indigo-700",
  aksesuar: "bg-cyan-100 text-cyan-700",
  kesim: "bg-orange-100 text-orange-700",
  dikim: "bg-rose-100 text-rose-700",
  utu_paketleme: "bg-pink-100 text-pink-700",
  kalite_kontrol: "bg-purple-100 text-purple-700",
  teslim_alindi: "bg-emerald-100 text-emerald-700",
  fatura_kesildi: "bg-gray-800 text-white",
};

export default function Manufacturing() {
  const navigate = useNavigate();
  const [items, setItems] = useState([]);
  const [stages, setStages] = useState([]);
  const [counts, setCounts] = useState({});
  const [loading, setLoading] = useState(true);
  const [stageFilter, setStageFilter] = useState("");
  const [search, setSearch] = useState("");

  const [editing, setEditing] = useState(null);  // null | record
  const [modalOpen, setModalOpen] = useState(false);
  const [saving, setSaving] = useState(false);

  const [form, setForm] = useState(initialForm());
  const [qtyDetail, setQtyDetail] = useState(() => new Set()); // Toplam Adet detay satırı açık kayıtlar
  const toggleQtyDetail = (id) => setQtyDetail(prev => { const n = new Set(prev); n.has(id) ? n.delete(id) : n.add(id); return n; });

  function initialForm() {
    const _today = new Date().toISOString().substring(0, 10);
    return {
      product_name: "",
      supplier_id: "",              // İmalatçı (zorunlu, kayıtlı listeden)
      order_no: "",                 // İmalat Sipariş No (boşsa otomatik IMLT-... atanır)
      order_flags: { new: true, rpt: false }, // Yeni Sipariş / RPT
      stock_code: "",               // ürün ilk burada doğar
      agreement_date: _today,       // Sipariş Tarihi
      expected_delivery_date: _plus21(_today), // otomatik +21 gün
      colors: [],                   // sipariş edilen renkler
      sizes: [],                    // bedenler (matris kolonları)
      size_distribution: {},        // {"Renk|Beden": adet}
      unit_price: 0,
      agreed_total: 0,
      payment_done: false,          // tek tik: ödeme yapıldı mı
      has_lining: false,            // astarlı ürün mü (Astar Okeyi kolonunu açar)
      color_approvals: {},          // {"Renk": {fabric: bool, lining: bool}}
      cutting_start_date: "",       // kesim başlangıç tarihi (kesime geçerken sorulur)
      actual_distribution: {},      // gerçekleşen kesim adedi {"Renk|Beden": n}
      sewing_workshop: "",          // dikim atölyesi adı (dikime geçince girilir)
      sewing_report_images: [],     // imalat (görsel) raporu — yüklenen görsel URL'leri
      qc_result: "",                // kalite kontrol: "" | "gecti" | "kaldi" (kaldi → Re-FRI)
      qc_date: "",                  // kalite kontrol tarihi
      qc_images: [],                // kalite kontrol görselleri
      qc2_result: "",               // 2. kalite kontrol (Re-FRI sonrası)
      qc2_date: "",
      qc2_images: [],
      deliveries: [],               // depo sevkiyatları [{date, items:{"Renk|Beden":n}, note_images:[]}]
      waste_meters: 0,
      notes: "",
      current_stage: "siparis_dosyasi",
    };
  }

  // İmalatçı listesi (kayıtlı) + inline ekleme
  const [suppliers, setSuppliers] = useState([]);
  const [supplierSearch, setSupplierSearch] = useState("");
  const fetchSuppliers = async () => {
    try {
      const token = localStorage.getItem("token");
      const r = await axios.get(`${API}/manufacturing-suppliers`, { headers: { Authorization: `Bearer ${token}` } });
      setSuppliers(r.data.items || r.data || []);
    } catch { /* sessiz */ }
  };
  useEffect(() => { fetchSuppliers(); }, []);
  const addSupplierInline = async () => {
    const name = window.prompt("Yeni imalatçı adı:", supplierSearch.trim());
    if (!name || !name.trim()) return;
    const phone = window.prompt("Telefon (opsiyonel):") || "";
    try {
      const token = localStorage.getItem("token");
      const r = await axios.post(`${API}/manufacturing-suppliers`, { name: name.trim(), phone, type: "atolye" },
        { headers: { Authorization: `Bearer ${token}` } });
      const sup = r.data.supplier || r.data;
      toast.success("İmalatçı eklendi");
      await fetchSuppliers();
      if (sup?.id) setForm((f) => ({ ...f, supplier_id: sup.id }));
      setSupplierSearch("");
    } catch (e) { toast.error(e.response?.data?.detail || "İmalatçı eklenemedi"); }
  };

  useEffect(() => { fetchAll(); }, [stageFilter]);

  const fetchAll = async () => {
    setLoading(true);
    try {
      const token = localStorage.getItem("token");
      const hdr = { headers: { Authorization: `Bearer ${token}` } };
      let url = `${API}/manufacturing`;
      const params = [];
      if (stageFilter) params.push(`stage=${stageFilter}`);
      if (search) params.push(`search=${encodeURIComponent(search)}`);
      if (params.length) url += "?" + params.join("&");
      const [stagesRes, listRes] = await Promise.all([
        axios.get(`${API}/manufacturing/stages`, hdr),
        axios.get(url, hdr),
      ]);
      setStages(stagesRes.data.stages || []);
      setItems(listRes.data.items || []);
      setCounts(listRes.data.counts_by_stage || {});
    } catch (err) {
      toast.error("Veriler yüklenemedi");
    } finally {
      setLoading(false);
    }
  };

  const openCreate = () => {
    setEditing(null);
    setForm(initialForm());
    setModalOpen(true);
  };

  const openEdit = (item) => {
    setEditing(item);
    // Mevcut size_distribution'dan renk/beden eksenlerini çıkar ("Renk|Beden" veya çıplak beden)
    const _dist = item.size_distribution || {};
    const _colors = [...new Set(Object.keys(_dist).map((k) => (k.includes("|") ? k.split("|")[0] : "")).filter(Boolean))];
    const _sizes = [...new Set(Object.keys(_dist).map((k) => (k.includes("|") ? k.split("|")[1] : k)))];
    setForm({
      product_name: item.product_name || "",
      supplier_id: item.supplier_id || "",
      order_no: item.order_no || item.code || "",
      order_flags: item.order_flags || { new: false, rpt: false },
      stock_code: item.stock_code || "",
      colors: item.colors?.length ? item.colors : _colors,
      sizes: _sizes,
      agreement_date: (item.agreement_date || "").substring(0, 10),
      expected_delivery_date: (item.expected_delivery_date || "").substring(0, 10),
      size_distribution: _dist,
      unit_price: item.unit_price || 0,
      agreed_total: item.agreed_total || 0,
      payment_done: !!item.payment_done,
      has_lining: !!item.has_lining,
      color_approvals: item.color_approvals || {},
      cutting_start_date: item.cutting_start_date || "",
      actual_distribution: item.actual_distribution || {},
      sewing_workshop: item.sewing_workshop || "",
      sewing_report_images: item.sewing_report_images || [],
      qc_result: item.qc_result || "",
      qc_date: item.qc_date || (item.stage_dates || {}).kalite_kontrol || "",
      qc_images: item.qc_images || [],
      qc2_result: item.qc2_result || "",
      qc2_date: item.qc2_date || "",
      qc2_images: item.qc2_images || [],
      deliveries: item.deliveries || [],
      waste_meters: item.waste_meters || 0,
      notes: item.notes || "",
      current_stage: item.current_stage || "siparis_dosyasi",
    });
    setModalOpen(true);
  };

  const saveRecord = async (e) => {
    e?.preventDefault?.();
    if (!form.product_name.trim()) { toast.error("Ürün adı gerekli"); return; }
    if (!form.supplier_id) { toast.error("İmalatçı seçimi zorunlu — listeden seçin veya ekleyin"); return; }
    if (!String(form.order_no || "").trim()) { toast.error("İmalat Sipariş No zorunlu"); return; }
    if (!form.agreement_date) { toast.error("Sipariş Tarihi zorunlu"); return; }
    setSaving(true);
    try {
      const token = localStorage.getItem("token");
      const hdr = { headers: { Authorization: `Bearer ${token}` } };
      const payload = { ...form };
      // Clean empty sizes
      payload.size_distribution = Object.fromEntries(
        Object.entries(payload.size_distribution || {}).filter(([, v]) => Number(v) > 0)
          .map(([k, v]) => [k, Number(v)])
      );
      payload.unit_price = Number(payload.unit_price || 0);
      // Gerçekleşen kesim adetleri: yalnız sayı girilen hücreler kaydedilir
      payload.actual_distribution = Object.fromEntries(
        Object.entries(payload.actual_distribution || {})
          .filter(([, v]) => v !== "" && v !== null && v !== undefined)
          .map(([k, v]) => [k, Number(v)])
      );
      // Sevkiyatlar: boş kayıtlar atılır, adetler sayıya çevrilir
      payload.deliveries = (payload.deliveries || []).map(d => ({
        ...d,
        items: Object.fromEntries(
          Object.entries(d.items || {})
            .filter(([, v]) => v !== "" && v != null && Number(v) > 0)
            .map(([k, v]) => [k, Number(v)])
        ),
      })).filter(d => d.date || Object.keys(d.items).length || (d.note_images || []).length);
      // Toplam anlaşma bedeli OTOMATİK: genel toplam adet × birim fiyat (kullanıcı isteği)
      const _qty = Object.values(payload.size_distribution).reduce((a, b) => a + Number(b || 0), 0);
      payload.agreed_total = Number((payload.unit_price * _qty).toFixed(2));
      payload.waste_meters = 0;
      if (editing) {
        await axios.put(`${API}/manufacturing/${editing.id}`, payload, hdr);
        toast.success("Kayıt güncellendi");
      } else {
        await axios.post(`${API}/manufacturing`, payload, hdr);
        toast.success("İmalat kaydı oluşturuldu");
      }
      setModalOpen(false);
      fetchAll();
    } catch (err) {
      toast.error(err.response?.data?.detail || "Kaydedilemedi");
    } finally {
      setSaving(false);
    }
  };

  // Depo teslimatındaki kaydı ÜRÜN KARTINA taşı: Ürünler sayfasındaki "Yeni Ürün" formu
  // üretim bilgileriyle (ad, stok kodu, sezon, alış fiyatı, renk×beden=stok) önden dolu açılır.
  const openProductFromMfg = (item) => {
    const dist = Object.keys(item.actual_distribution || {}).length
      ? item.actual_distribution : (item.size_distribution || {});
    // Renk×beden yapısı üretimden gelir ama STOK OTOMATİK ÇEKİLMEZ (kullanıcı kararı):
    // stok girişi ürün kartında elle yapılır — depo sayımı/sevkiyat farkları otomatik yazılmasın.
    const variants = Object.entries(dist)
      .filter(([, q]) => Number(q) > 0)
      .map(([k]) => {
        const [color, size] = k.includes("|") ? k.split("|") : ["", k];
        return { size, color, stock: 0 };
      });
    const sc = (item.stock_code || "").toUpperCase();
    const season = sc.startsWith("FCFW") ? "Kış" : sc.startsWith("FCSS") ? "Yaz" : "";
    sessionStorage.setItem("mfg_product_prefill", JSON.stringify({
      mfg_record_id: item.id,
      name: item.product_name || "",
      stock_code: item.stock_code || "",
      season,
      // Alış fiyatı KDV DAHİL taşınır (birim fiyat KDV hariç girilir, %10 KDV eklenir)
      purchase_price: Number((Number(item.unit_price || 0) * 1.10).toFixed(2)),
      manufacturer: item.partner_name || "FACETTE",
      variants,
    }));
    navigate("/admin/urunler?newFromMfg=1");
  };

  // Her aşama geçişinde kullanıcı o aşamanın TARİHİNİ girer (kullanıcı isteği)
  const _STAGE_DATE_LABELS = {
    kumas_okeyi: "Kumaş okeyi tarihi",
    kesim: "Kesim başlangıç tarihi",
    dikim: "Dikiş başlangıç tarihi",
    kalite_kontrol: "Kalite kontrol tarihi",
    teslim_alindi: "Depo teslim tarihi",
  };
  // İlerletme artık tarayıcı prompt'u yerine tasarıma uygun modal ile (kullanıcı isteği)
  const [advanceModal, setAdvanceModal] = useState(null); // {item, stage, date, note}
  const advanceStage = (item, newStage) => {
    if (!newStage) return;
    // Yön tespiti: seçilen aşama mevcut aşamadan ÖNCE ise "geri alma"dır.
    const curIdx = stages.findIndex((s) => s.key === item.current_stage);
    const newIdx = stages.findIndex((s) => s.key === newStage);
    const back = curIdx > -1 && newIdx > -1 && newIdx < curIdx;
    // Veri KORUMA: geri alırken o aşamada zaten girilmiş tarihi öne al (üzerine yazıp silmesin).
    const existingDate =
      (item.stage_dates || {})[newStage] ||
      (newStage === "kesim" ? item.cutting_start_date || "" : "") ||
      "";
    setAdvanceModal({
      item, stage: newStage, back,
      date: _STAGE_DATE_LABELS[newStage]
        ? existingDate || (back ? "" : new Date().toISOString().substring(0, 10))
        : "",
      note: "",
      workshop: newStage === "dikim" ? (item.sewing_workshop || "") : "",
    });
  };
  const confirmAdvance = async () => {
    const m = advanceModal;
    if (!m) return;
    try {
      const token = localStorage.getItem("token");
      await axios.post(`${API}/manufacturing/${m.item.id}/advance`,
        { stage: m.stage, note: m.note || "", ...(m.date ? { stage_date: m.date } : {}),
          ...(m.stage === "dikim" && m.workshop?.trim() ? { sewing_workshop: m.workshop.trim() } : {}) },
        { headers: { Authorization: `Bearer ${token}` } }
      );
      toast.success(m.back ? "Aşama geri alındı — girilen veriler korundu" : "Aşama güncellendi");
      if (!m.back && m.stage === "teslim_alindi") toast.success("Stok otomatik güncellendi");
      setAdvanceModal(null);
      fetchAll();
    } catch (err) {
      toast.error(err.response?.data?.detail || "Aşama değiştirilemedi");
    }
  };

  // Görsel yükleyici (dikim raporu + kalite kontrol + irsaliye): dosya seç → /upload/image → URL listesi
  const [reportUploading, setReportUploading] = useState("");  // "" | alan adı (hangi bölüm yüklüyor)
  const _uploadImages = async (fileList) => {
    const files = Array.from(fileList || []);
    if (!files.length) return [];
    const token = localStorage.getItem("token");
    const urls = [];
    for (const f of files) {
      const fd = new FormData();
      fd.append("file", f);
      const r = await axios.post(`${API}/upload/image`, fd, { headers: { Authorization: `Bearer ${token}` } });
      if (r.data?.url) urls.push(r.data.url);
    }
    return urls;
  };
  const uploadReportImages = async (fileList, field = "sewing_report_images") => {
    setReportUploading(field);
    try {
      const urls = await _uploadImages(fileList);
      if (urls.length) {
        setForm((prev) => ({ ...prev, [field]: [...(prev[field] || []), ...urls] }));
        toast.success(`${urls.length} görsel yüklendi — Kaydet'e basmayı unutmayın`);
      }
    } catch (e) {
      toast.error(e.response?.data?.detail || "Görsel yüklenemedi");
    } finally {
      setReportUploading("");
    }
  };
  // Sevkiyat kartına irsaliye görseli yükle (di: deliveries dizisindeki sıra)
  const uploadDeliveryImages = async (di, fileList) => {
    setReportUploading(`delivery_${di}`);
    try {
      const urls = await _uploadImages(fileList);
      if (urls.length) {
        setForm((prev) => ({
          ...prev,
          deliveries: (prev.deliveries || []).map((d, j) => j === di
            ? { ...d, note_images: [...(d.note_images || []), ...urls] } : d),
        }));
        toast.success(`${urls.length} irsaliye görseli yüklendi — Kaydet'i unutmayın`);
      }
    } catch (e) {
      toast.error(e.response?.data?.detail || "Görsel yüklenemedi");
    } finally {
      setReportUploading("");
    }
  };
  const setDeliveryField = (di, patch) => setForm((prev) => ({
    ...prev,
    deliveries: (prev.deliveries || []).map((d, j) => (j === di ? { ...d, ...patch } : d)),
  }));
  const setDeliveryItem = (di, key, val) => setForm((prev) => ({
    ...prev,
    deliveries: (prev.deliveries || []).map((d, j) => (j === di
      ? { ...d, items: { ...(d.items || {}), [key]: val } } : d)),
  }));

  const deleteRecord = async (item) => {
    if (!await window.appConfirm(`"${item.code}" kaydını silmek istiyor musunuz?`)) return;
    try {
      const token = localStorage.getItem("token");
      await axios.delete(`${API}/manufacturing/${item.id}`, {
        headers: { Authorization: `Bearer ${token}` }
      });
      toast.success("Silindi");
      fetchAll();
    } catch (err) {
      toast.error("Silinemedi");
    }
  };

  const stageLabel = (key) => stages.find(s => s.key === key)?.label || key;
  const nextStage = (current) => {
    const idx = stages.findIndex(s => s.key === current);
    return idx >= 0 && idx < stages.length - 1 ? stages[idx + 1].key : null;
  };
  const prevStage = (current) => {
    const idx = stages.findIndex(s => s.key === current);
    return idx > 0 ? stages[idx - 1].key : null;
  };

  // ── Renk × Beden kombinasyon matrisi ──────────────────────────────────
  const matrixKey = (color, size) => (color ? `${color}|${size}` : size);
  const cellVal = (color, size) => Number(form.size_distribution?.[matrixKey(color, size)] || 0);
  const setCell = (color, size, val) => setForm(f => ({
    ...f,
    size_distribution: { ...f.size_distribution, [matrixKey(color, size)]: Number(val || 0) },
  }));
  // Renk/Beden seçim penceresi (prompt yerine tablo — kullanıcı isteği)
  const [pickerOpen, setPickerOpen] = useState(false);
  const [pickColors, setPickColors] = useState([]);
  const [pickSizes, setPickSizes] = useState([]);
  const [customColor, setCustomColor] = useState("");
  const [customSize, setCustomSize] = useState("");
  const openPicker = () => {
    setPickColors([...form.colors]);
    setPickSizes([...form.sizes]);
    setCustomColor(""); setCustomSize("");
    setPickerOpen(true);
  };
  const togglePick = (list, setList, val) =>
    setList(list.includes(val) ? list.filter(x => x !== val) : [...list, val]);
  const applyPicker = () => {
    setForm(f => {
      // Seçimden çıkarılan renk/bedenlerin matris hücreleri temizlenir
      const dist = Object.fromEntries(Object.entries(f.size_distribution || {}).filter(([k]) => {
        const c = k.includes("|") ? k.split("|")[0] : "";
        const s = k.includes("|") ? k.split("|")[1] : k;
        return (c === "" ? pickColors.length === 0 : pickColors.includes(c)) && pickSizes.includes(s);
      }));
      return { ...f, colors: pickColors, sizes: pickSizes, size_distribution: dist };
    });
    setPickerOpen(false);
  };
  const removeColor = (c) => setForm(f => ({
    ...f, colors: f.colors.filter(x => x !== c),
    size_distribution: Object.fromEntries(Object.entries(f.size_distribution).filter(([k]) => !k.startsWith(c + "|"))),
  }));
  const removeSize = (s) => setForm(f => ({
    ...f, sizes: f.sizes.filter(x => x !== s),
    size_distribution: Object.fromEntries(Object.entries(f.size_distribution).filter(([k]) => (k.includes("|") ? k.split("|")[1] : k) !== s)),
  }));
  // Onay değişiminde zaman damgası da tutulur (ödeme→okey süresi ölçümü için)
  const _stampApproval = (cur, kind) => {
    const on = !cur[kind];
    return { ...cur, [kind]: on, [`${kind}_at`]: on ? new Date().toISOString() : null };
  };

  // Listeden tek tıkla renk onayı değiştir (modal açmadan) — iyimser güncelle + sunucuya yaz
  const toggleRowApproval = async (item, color, kind) => {
    const key = color || "_tek";
    const cur = item.color_approvals?.[key] || {};
    const next = { ...(item.color_approvals || {}), [key]: _stampApproval(cur, kind) };
    setItems(prev => prev.map(x => x.id === item.id ? { ...x, color_approvals: next } : x));
    try {
      const token = localStorage.getItem("token");
      await axios.put(`${API}/manufacturing/${item.id}`, { color_approvals: next },
        { headers: { Authorization: `Bearer ${token}` } });
    } catch { toast.error("Onay kaydedilemedi"); fetchAll(); }
  };

  // Renk bazlı onaylar: kumaş okeyi her renkte; astar okeyi yalnız astarlı üründe
  const approvalOf = (color, kind) => !!(form.color_approvals?.[color || "_tek"]?.[kind]);
  const toggleApproval = (color, kind) => setForm(f => {
    const key = color || "_tek";
    const cur = f.color_approvals?.[key] || {};
    return { ...f, color_approvals: { ...(f.color_approvals || {}), [key]: _stampApproval(cur, kind) } };
  });

  // Gerçekleşen (kesilen) adet — kesim ve sonraki aşamalarda girilir; fire/fazla % gösterilir
  const actualVal = (color, size) => form.actual_distribution?.[matrixKey(color, size)];
  const setActual = (color, size, val) => setForm(f => ({
    ...f,
    actual_distribution: { ...(f.actual_distribution || {}), [matrixKey(color, size)]: val === "" ? "" : Number(val || 0) },
  }));
  const showActuals = !!editing && ["kesim", "dikim", "kalite_kontrol", "teslim_alindi"].includes(form.current_stage);

  const rowTotal = (color) => form.sizes.reduce((s, sz) => s + cellVal(color, sz), 0);
  const grandTotal = (form.colors.length ? form.colors : [""]).reduce((s, c) => s + rowTotal(c), 0);

  return (
    <div className="p-6 max-w-7xl mx-auto" data-testid="manufacturing-page">
      <div className="flex justify-between items-center mb-6">
        <div>
          <h1 className="text-2xl font-bold flex items-center gap-2">
            <Factory className="text-rose-600" /> İmalat Takip
          </h1>
          <p className="text-sm text-gray-500 mt-1">Üretim sürecinin anlaşmadan teslime kadar tüm aşamalarını takip edin.</p>
        </div>
        <button onClick={openCreate} data-testid="create-mfg-btn"
          className="flex items-center gap-2 px-4 py-2 bg-rose-600 text-white rounded-lg text-sm font-bold hover:bg-rose-700">
          <Plus size={16} /> Yeni İmalat Kaydı
        </button>
      </div>

      {/* Aşamalar — kutucuk yerine TIMELINE (kullanıcı isteği); tıklayınca filtreler */}
      <div className="bg-white rounded-xl shadow-sm p-4 mb-6 overflow-x-auto">
        <div className="flex items-start min-w-[640px]">
          {[{ key: "", label: "Tümü", count: Object.values(counts).reduce((a, b) => a + b, 0) },
            ...stages.map(s => ({ key: s.key, label: s.label, count: counts[s.key] || 0 }))].map((n, i) => {
            const active = stageFilter === n.key;
            return (
              <div key={n.key || "all"} className="flex items-start flex-1 min-w-0">
                {i > 0 && <div className="flex-1 h-0.5 bg-gray-200 mt-[19px] min-w-[16px]" />}
                <button
                  onClick={() => setStageFilter(n.key)}
                  data-testid={n.key ? `stage-filter-${n.key}` : "stage-filter-all"}
                  className="flex flex-col items-center gap-1 shrink-0 px-1 group"
                >
                  <span className={`w-10 h-10 rounded-full flex items-center justify-center text-sm font-bold border-2 transition
                    ${active ? "bg-rose-600 text-white border-rose-600 shadow" : "bg-white border-gray-300 text-gray-700 group-hover:border-rose-400"}`}>
                    {n.count}
                  </span>
                  <span className={`text-[11px] leading-tight text-center max-w-[86px] ${active ? "font-bold text-rose-700" : "text-gray-500"}`}>
                    {n.label}
                  </span>
                </button>
              </div>
            );
          })}
        </div>
      </div>

      {/* List */}
      <div className="bg-white rounded-xl border shadow-sm overflow-hidden">
        {loading ? (
          <div className="p-8 text-center text-gray-500">Yükleniyor...</div>
        ) : items.length === 0 ? (
          <div className="p-8 text-center text-gray-500">Kayıt bulunamadı. Sağ üstten yeni kayıt ekleyebilirsiniz.</div>
        ) : (
          <table className="w-full">
            <thead className="border-b bg-gray-50">
              <tr>
                <th className="text-left px-3 py-3 text-xs font-bold text-gray-500 uppercase w-10">#</th>
                <th className="text-left px-3 py-3 text-xs font-bold text-gray-500 uppercase">İmalatçı</th>
                <th className="text-left px-3 py-3 text-xs font-bold text-gray-500 uppercase">İmalat Sipariş No</th>
                <th className="text-left px-3 py-3 text-xs font-bold text-gray-500 uppercase">Ürün</th>
                <th className="text-left px-3 py-3 text-xs font-bold text-gray-500 uppercase">Sipariş Tarihi</th>
                <th className="text-left px-3 py-3 text-xs font-bold text-gray-500 uppercase">Kumaş Okeyi</th>
                <th className="text-right px-3 py-3 text-xs font-bold text-gray-500 uppercase" title="Gerçekleşen (kesilen) toplam adet">Toplam Adet</th>
                <th className="text-left px-3 py-3 text-xs font-bold text-gray-500 uppercase" title="Dikim tarihi, atölye ve görsel imalat raporu">Dikim Başlangıcı</th>
                <th className="text-left px-3 py-3 text-xs font-bold text-gray-500 uppercase" title="Kalite kontrol: Geçti/Kaldı (kaldıysa Re-FRI), tarih ve görseller">Kalite Kontrol</th>
                <th className="text-left px-3 py-3 text-xs font-bold text-gray-500 uppercase" title="Depoya gelen toplam adet (birden çok sevkiyat toplanır) + irsaliye görselleri">Depo Sevkiyat</th>
                <th className="px-3 py-3"></th>
                <th className="text-center px-3 py-3 text-xs font-bold text-gray-500 uppercase">Ürün Aç</th>
              </tr>
            </thead>
            <tbody>
              {items.map((item, idx) => {
                // Kalan gün: tahmini teslim (sipariş tarihi + 21 gün kuralı) − bugün
                // KURAL: 21 gün ÖDEMEDEN itibaren sayılır; ödeme yoksa sipariş tarihi tabanlı tahmin
                const _exp = (item.payment_done && item.payment_done_at ? _plus21(item.payment_done_at)
                  : item.expected_delivery_date || (item.agreement_date ? _plus21(item.agreement_date) : ""));
                const _days = _exp ? Math.ceil((new Date(String(_exp).substring(0, 10)) - new Date(new Date().toISOString().substring(0, 10))) / 864e5) : null;
                const _delivered = item.current_stage === "teslim_alindi";
                const _rowColors = (item.colors?.length ? item.colors
                  : [...new Set(Object.keys(item.size_distribution || {}).map(k => k.includes("|") ? k.split("|")[0] : ""))].filter(Boolean));
                // Okey tamamlanma (hatırlatıcı nokta için) — zaman damgaları çip tooltip'inde
                const _keys = (_rowColors.length ? _rowColors : [""]).map(c => c || "_tek");
                const _ap = item.color_approvals || {};
                const _okeysDone = _keys.every(k => _ap[k]?.fabric) && (!item.has_lining || _keys.every(k => _ap[k]?.lining));
                // Kırmızı yanıp sönen hatırlatıcı: kumaş okeyinde okeyler tamamsa → kesime geç;
                // kesim ve sonraki ara aşamalarda her zaman (ilerletme dış haberle yapılır)
                const _showDot = (_okeysDone && item.current_stage === "kumas_okeyi")
                  || ["kesim", "dikim", "kalite_kontrol"].includes(item.current_stage);
                return (
                <Fragment key={item.id}>
                <tr className="border-b hover:bg-gray-50" data-testid={`mfg-row-${item.code}`}>
                  <td className="px-3 py-3 text-sm font-bold text-gray-400 tabular-nums">{idx + 1}</td>
                  <td className="px-3 py-3 text-sm font-semibold">{item.partner_name || "—"}</td>
                  <td className="px-3 py-3">
                    <p className="text-sm font-semibold">{item.order_no || item.code}</p>
                    {item.order_flags?.rpt && (
                      <span className="inline-block mt-0.5 text-[9px] font-bold text-blue-700 bg-blue-50 border border-blue-200 rounded px-1.5 py-0.5"
                        title="Tekrar sipariş (repeat)">RPT</span>
                    )}
                  </td>
                  <td className="px-3 py-3">
                    <p className="text-sm font-semibold">{item.product_name}</p>
                    <p className="flex items-center gap-1.5 mt-0.5">
                      {/* Aşama rozeti kaldırıldı — aşama zaten üstteki timeline'da görünüyor */}
                      {item.payment_done
                        ? <span className="text-[9px] font-bold text-emerald-700 bg-emerald-50 border border-emerald-200 rounded px-1 py-0.5">ÖDENDİ</span>
                        : <span className="text-[9px] font-bold text-red-600 bg-red-50 border border-red-200 rounded px-1 py-0.5">ÖDENMEDİ</span>}
                    </p>
                  </td>
                  <td className="px-3 py-3 text-xs">
                    <p className="text-gray-700">{item.agreement_date ? new Date(item.agreement_date).toLocaleDateString('tr-TR') : '—'}</p>
                    {(() => {
                      // Girilen aşama tarihleri (eski kayıtlarda yalnız kesim tarihi olabilir)
                      const sd = { ...(item.cutting_start_date && !(item.stage_dates || {}).kesim ? { kesim: item.cutting_start_date } : {}), ...(item.stage_dates || {}) };
                      return Object.entries(sd).map(([k, v]) => (
                        <p key={k} className="text-[9px] text-orange-600 whitespace-nowrap">
                          {stageLabel(k)}: {new Date(v).toLocaleDateString('tr-TR')}
                        </p>
                      ));
                    })()}
                    {_delivered ? (
                      <p className="text-[10px] font-bold text-emerald-600">Teslim alındı ✓</p>
                    ) : _days == null ? null : _days < 0 ? (
                      <p className="text-[11px] font-bold text-red-700">{Math.abs(_days)} gün GECİKTİ!</p>
                    ) : (
                      <p className="text-[11px] font-bold text-red-600">{_days} gün kaldı</p>
                    )}
                  </td>
                  <td className="px-3 py-3">
                    {/* Ödeme bekliyorsa uyarı; ödendiyse tekrar yazılmaz (ürün sütununda zaten var) */}
                    {!item.payment_done && (
                      <p className="text-[10px] font-bold mb-1 text-amber-600">ÖDEME BEKLİYOR</p>
                    )}
                    {/* Renk bazlı kumaş okeyi — etiket sabit kolonda, çipler kendi kolonunda sarar (simetrik) */}
                    <div className="flex items-start gap-1">
                      <span className="text-[9px] text-black font-bold uppercase w-11 shrink-0 mt-1">Kumaş:</span>
                      <div className="flex flex-wrap gap-1 max-w-[190px]">
                      {(_rowColors.length ? _rowColors : [""]).map(c => {
                        const ok = !!(item.color_approvals?.[c || "_tek"]?.fabric);
                        const at = item.color_approvals?.[c || "_tek"]?.fabric_at;
                        return (
                          // Rengin KENDİSİ küçük kare kutu + yanında onay tiki (oval/dolgu rozet yok)
                          <button key={c || "_tek"} type="button" onClick={() => toggleRowApproval(item, c, "fabric")}
                            title={`${c || "Tek renk"} — kumaş ${ok ? `onaylı${at ? " (" + new Date(at).toLocaleDateString("tr-TR") + ")" : ""}` : "onaysız (onaylamak için tıkla)"}`}
                            className="inline-flex items-center gap-1 px-1 py-0.5 rounded hover:bg-gray-100 transition">
                            <span className="w-4 h-4 rounded-[3px] shrink-0" style={_swatchStyle(c)} />
                            <span className={`w-3.5 h-3.5 rounded-[2px] text-[9px] leading-none flex items-center justify-center border ${ok ? "bg-black text-white border-black" : "bg-white border-gray-300 text-transparent"}`}>✓</span>
                          </button>
                        );
                      })}
                      </div>
                    </div>
                    {item.has_lining && (
                      <div className="flex items-start gap-1 mt-1">
                        <span className="text-[9px] text-black font-bold uppercase w-11 shrink-0 mt-1">Astar:</span>
                        <div className="flex flex-wrap gap-1 max-w-[190px]">
                        {(_rowColors.length ? _rowColors : [""]).map(c => {
                          const ok = !!(item.color_approvals?.[c || "_tek"]?.lining);
                          const at = item.color_approvals?.[c || "_tek"]?.lining_at;
                          return (
                            <button key={c || "_tek"} type="button" onClick={() => toggleRowApproval(item, c, "lining")}
                              title={`${c || "Tek renk"} — astar ${ok ? `onaylı${at ? " (" + new Date(at).toLocaleDateString("tr-TR") + ")" : ""}` : "onaysız (onaylamak için tıkla)"}`}
                              className="inline-flex items-center gap-1 px-1 py-0.5 rounded hover:bg-gray-100 transition">
                              <span className="w-4 h-4 rounded-[3px] shrink-0" style={_swatchStyle(c)} />
                              <span className={`w-3.5 h-3.5 rounded-[2px] text-[9px] leading-none flex items-center justify-center border ${ok ? "bg-black text-white border-black" : "bg-white border-gray-300 text-transparent"}`}>✓</span>
                            </button>
                          );
                        })}
                        </div>
                      </div>
                    )}
                  </td>
                  <td className="px-3 py-3 text-right cursor-pointer hover:bg-gray-100 rounded"
                    onClick={() => toggleQtyDetail(item.id)}
                    title="Detay için tıklayın — renk|beden bazında sipariş → kesilen">
                    {(() => {
                      const _actTot = Object.values(item.actual_distribution || {}).reduce((a, b) => a + Number(b || 0), 0);
                      return (
                        <div>
                          <p className="text-[9px] text-gray-400 whitespace-nowrap">Sipariş: <b className="text-gray-600">{item.total_units || 0}</b></p>
                          {_actTot > 0 ? (
                            <p className="text-sm font-bold tabular-nums whitespace-nowrap text-black">
                              Kesilen: {_actTot}
                            </p>
                          ) : (
                            <p className="text-[10px] text-gray-300">Kesilen: —</p>
                          )}
                          <p className="text-[9px] text-blue-500">{qtyDetail.has(item.id) ? "detayı gizle ▴" : "detay ▾"}</p>
                        </div>
                      );
                    })()}
                  </td>
                  <td className="px-3 py-3 align-top">
                    {(() => {
                      const _dkDate = (item.stage_dates || {}).dikim;
                      const _imgs = item.sewing_report_images || [];
                      if (!_dkDate && !item.sewing_workshop && !_imgs.length)
                        return <span className="text-xs text-gray-300">—</span>;
                      return (
                        <div className="space-y-1">
                          {/* Hizalama sözleşmesi (KK sütunuyla ortak): 1. satır TARİH, 2. satır ROZET, 3. satır GÖRSELLER */}
                          <p className="text-xs text-gray-700 whitespace-nowrap h-4">
                            {_dkDate ? new Date(_dkDate).toLocaleDateString("tr-TR") : ""}
                          </p>
                          <div className="min-h-[22px]">
                            {item.sewing_workshop && (
                              <p className="text-[10px] font-semibold text-purple-700 bg-purple-50 border border-purple-200 rounded px-1.5 py-0.5 inline-block whitespace-nowrap">
                                🏭 {item.sewing_workshop}
                              </p>
                            )}
                          </div>
                          {_imgs.length > 0 && (
                            <a href={_imgUrl(_imgs[0])} target="_blank" rel="noreferrer"
                              title={`${_imgs.length} görsel rapor — açmak için tıklayın (tümü düzenleme ekranında)`}
                              className="inline-flex items-center gap-1 text-gray-500 hover:text-black">
                              <ImageIcon size={16} strokeWidth={1.5} />
                              {_imgs.length > 1 && <span className="text-[10px] font-bold">×{_imgs.length}</span>}
                            </a>
                          )}
                        </div>
                      );
                    })()}
                  </td>
                  <td className="px-3 py-3 align-top">
                    {(() => {
                      const _qcDate = item.qc_date || (item.stage_dates || {}).kalite_kontrol;
                      const _qcImgs = item.qc_images || [];
                      if (!item.qc_result && !_qcDate && !_qcImgs.length
                        && !item.qc2_result && !item.qc2_date && !(item.qc2_images || []).length)
                        return <span className="text-xs text-gray-300">—</span>;
                      return (
                        <div className="space-y-1">
                          {/* Hizalama sözleşmesi (Dikim sütunuyla ortak): 1. satır TARİH, 2. satır ROZET, 3. satır GÖRSELLER */}
                          <p className="text-xs text-gray-700 whitespace-nowrap h-4">
                            {_qcDate ? new Date(_qcDate).toLocaleDateString("tr-TR") : ""}
                          </p>
                          <div className="min-h-[22px]">
                            {item.qc_result === "gecti" && (
                              <p className="text-[10px] font-bold text-emerald-700 bg-emerald-50 border border-emerald-200 rounded px-1.5 py-0.5 inline-block">GEÇTİ ✓</p>
                            )}
                            {item.qc_result === "kaldi" && (
                              <p className="inline-flex items-center gap-1 whitespace-nowrap">
                                <span className="text-[10px] font-bold text-red-700 bg-red-50 border border-red-200 rounded px-1.5 py-0.5">KALDI ✗</span>
                                <span className="text-[10px] font-bold text-white bg-red-600 rounded px-1.5 py-0.5" title="Yeniden kalite kontrol gerekli">Re-FRI</span>
                              </p>
                            )}
                          </div>
                          {_qcImgs.length > 0 && (
                            <a href={_imgUrl(_qcImgs[0])} target="_blank" rel="noreferrer"
                              title={`${_qcImgs.length} kalite kontrol görseli — açmak için tıklayın (tümü düzenleme ekranında)`}
                              className="inline-flex items-center gap-1 text-gray-500 hover:text-black">
                              <ImageIcon size={16} strokeWidth={1.5} />
                              {_qcImgs.length > 1 && <span className="text-[10px] font-bold">×{_qcImgs.length}</span>}
                            </a>
                          )}
                          {/* 2. kalite kontrol (Re-FRI sonrası tekrar) — varsa ayrı satırda */}
                          {(item.qc2_result || item.qc2_date || (item.qc2_images || []).length > 0) && (
                            <div className="pt-1 mt-1 border-t border-gray-100 space-y-1">
                              <p className="text-[9px] font-bold text-gray-400 uppercase">2. Kontrol</p>
                              {item.qc2_date && (
                                <p className="text-xs text-gray-700 whitespace-nowrap">{new Date(item.qc2_date).toLocaleDateString("tr-TR")}</p>
                              )}
                              {item.qc2_result === "gecti" && (
                                <p className="text-[10px] font-bold text-emerald-700 bg-emerald-50 border border-emerald-200 rounded px-1.5 py-0.5 inline-block">GEÇTİ ✓</p>
                              )}
                              {item.qc2_result === "kaldi" && (
                                <p className="inline-flex items-center gap-1 whitespace-nowrap">
                                  <span className="text-[10px] font-bold text-red-700 bg-red-50 border border-red-200 rounded px-1.5 py-0.5">KALDI ✗</span>
                                  <span className="text-[10px] font-bold text-white bg-red-600 rounded px-1.5 py-0.5" title="Yeniden kalite kontrol gerekli">Re-FRI</span>
                                </p>
                              )}
                              {(item.qc2_images || []).length > 0 && (
                                <a href={_imgUrl(item.qc2_images[0])} target="_blank" rel="noreferrer"
                                  title={`${item.qc2_images.length} görsel — açmak için tıklayın`}
                                  className="inline-flex items-center gap-1 text-gray-500 hover:text-black">
                                  <ImageIcon size={16} strokeWidth={1.5} />
                                  {item.qc2_images.length > 1 && <span className="text-[10px] font-bold">×{item.qc2_images.length}</span>}
                                </a>
                              )}
                            </div>
                          )}
                        </div>
                      );
                    })()}
                  </td>
                  <td className="px-3 py-3 align-top">
                    {(() => {
                      const _dels = item.deliveries || [];
                      if (!_dels.length) return <span className="text-xs text-gray-300">—</span>;
                      const _allImgs = _dels.flatMap(d => d.note_images || []);
                      return (
                        <div className="space-y-0.5">
                          {/* Her sevkiyat AYRI satır (toplanmaz): adet + yanında tarih — İmalatçı ile aynı yazım */}
                          {_dels.map((d, i) => {
                            const t = Object.values(d.items || {}).reduce((x, y) => x + Number(y || 0), 0);
                            return (
                              <p key={i} className="text-xs text-gray-700 whitespace-nowrap">
                                {t} adet · {d.date ? new Date(d.date).toLocaleDateString("tr-TR") : "—"}
                              </p>
                            );
                          })}
                          {_allImgs.length > 0 && (
                            <a href={_imgUrl(_allImgs[0])} target="_blank" rel="noreferrer"
                              title={`${_allImgs.length} irsaliye görseli — açmak için tıklayın (tümü düzenleme ekranında)`}
                              className="inline-flex items-center gap-1 text-gray-500 hover:text-black">
                              <ImageIcon size={16} strokeWidth={1.5} />
                              {_allImgs.length > 1 && <span className="text-[10px] font-bold">×{_allImgs.length}</span>}
                            </a>
                          )}
                        </div>
                      );
                    })()}
                  </td>
                  <td className="px-4 py-3 text-right whitespace-nowrap">
                    {/* Kırmızı yanıp sönen hatırlatıcı: bir sonraki aşamaya ilerletme gerekiyor */}
                    {_showDot && (
                      <span className="relative inline-flex h-3 w-3 mr-2 align-middle"
                        title={item.current_stage === "kumas_okeyi"
                          ? "Okeyler tamamlandı — Kesim Başlangıcı'na ilerletin!"
                          : "Bu aşama tamamlandıysa bir sonrakine ilerletin"}>
                        <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-red-400 opacity-75"></span>
                        <span className="relative inline-flex rounded-full h-3 w-3 bg-red-600"></span>
                      </span>
                    )}
                    {prevStage(item.current_stage) && (
                      <button
                        onClick={() => advanceStage(item, prevStage(item.current_stage))}
                        data-testid={`stage-back-${item.id}`}
                        title="Aşamayı bir önceki adıma geri al — girilen veriler (adet, tarih, kalite, atölye, görseller) korunur"
                        className="px-2 py-1 text-xs text-gray-500 hover:bg-gray-100 rounded font-medium"
                      >
                        <ChevronLeft size={13} className="inline" /> Geri Al
                      </button>
                    )}
                    {nextStage(item.current_stage) && (
                      <button
                        onClick={() => advanceStage(item, nextStage(item.current_stage))}
                        data-testid={`advance-${item.id}`}
                        className="px-2 py-1 text-xs text-black hover:bg-gray-100 rounded font-medium"
                      >
                        <ChevronRight size={13} className="inline" /> İlerlet
                      </button>
                    )}
                    <button onClick={() => openEdit(item)} className="px-2 py-1 text-xs text-indigo-600 hover:bg-indigo-50 rounded">
                      <Edit size={13} className="inline" />
                    </button>
                    <button onClick={() => deleteRecord(item)} className="px-2 py-1 text-xs text-red-600 hover:bg-red-50 rounded">
                      <Trash2 size={13} className="inline" />
                    </button>
                  </td>
                  <td className="px-3 py-3 text-center">
                    {/* Ürün Aç HER aşamada kullanılabilir (kullanıcı isteği — dosya açılır açılmaz kart oluşturulabilsin) */}
                    {item.product_created ? (
                      <span className="text-[10px] text-emerald-700 bg-emerald-50 border border-emerald-200 rounded px-1.5 py-0.5">ürün açıldı ✓</span>
                    ) : (
                      <button onClick={() => openProductFromMfg(item)}
                        data-testid={`open-product-${item.id}`}
                        title="Ürün Aç — üretim bilgileriyle (ad, stok kodu, sezon, KDV dahil alış fiyatı, renk×beden yapısı) Yeni Ürün formunu açar"
                        className="w-10 h-10 inline-flex items-center justify-center text-black hover:bg-gray-100 rounded-lg text-3xl font-light leading-none">
                        +
                      </button>
                    )}
                  </td>
                </tr>
                {qtyDetail.has(item.id) && (
                  <tr className="bg-blue-50/40 border-b">
                    <td colSpan={12} className="px-6 py-3">
                      <div className="text-[11px] font-bold text-gray-600 uppercase mb-1.5">Adet Detayı — Sipariş → Kesilen</div>
                      <div className="flex flex-wrap gap-2">
                        {Object.entries(item.size_distribution || {}).map(([k, q]) => {
                          const act = (item.actual_distribution || {})[k];
                          const diff = act != null && q > 0 ? ((Number(act) - q) / q) * 100 : null;
                          return (
                            <span key={k} className="px-2.5 py-1 bg-white border rounded-lg text-xs whitespace-nowrap">
                              <b>{k}</b>: {q} → {act ?? "—"}
                              {diff != null && Math.round(diff) !== 0 && (
                                <b className={diff < 0 ? "text-red-600 ml-1" : "text-emerald-600 ml-1"}>
                                  {diff > 0 ? "+" : ""}{diff.toFixed(1).replace(".0", "")}%
                                </b>
                              )}
                            </span>
                          );
                        })}
                        {Object.keys(item.size_distribution || {}).length === 0 && <span className="text-xs text-gray-400">Dağılım girilmemiş.</span>}
                      </div>
                    </td>
                  </tr>
                )}
                </Fragment>
                );
              })}
            </tbody>
          </table>
        )}
      </div>

      {/* Aşama İlerletme Modalı — tarayıcı prompt yerine tasarıma uygun pencere */}
      <Dialog open={!!advanceModal} onOpenChange={(o) => { if (!o) setAdvanceModal(null); }}>
        <DialogContent className="max-w-md" data-testid="advance-modal">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              {advanceModal?.back
                ? <ChevronLeft size={18} className="text-gray-600" />
                : <ChevronRight size={18} className="text-rose-600" />}
              {advanceModal ? stageLabel(advanceModal.stage) : ""} aşamasına {advanceModal?.back ? "geri al" : "ilerlet"}
            </DialogTitle>
          </DialogHeader>
          {advanceModal && (
            <div className="space-y-4">
              <p className="text-sm text-gray-500">
                <b className="text-gray-800">{advanceModal.item.product_name}</b> kaydı
                "<b>{stageLabel(advanceModal.item.current_stage)}</b>" aşamasından
                "<b className={advanceModal.back ? "text-gray-700" : "text-rose-700"}>{stageLabel(advanceModal.stage)}</b>" aşamasına taşınacak.
              </p>
              {advanceModal.back && (
                <p className="text-xs bg-gray-50 border border-gray-200 rounded-lg px-3 py-2 text-gray-600">
                  Girilen diğer veriler (adet, kalite kontrol, atölye, görseller, tarihler) <b>korunur</b> — yalnızca aşama durumu değişir.
                  {advanceModal.item.current_stage === "teslim_alindi" && (
                    <span className="block mt-1 text-amber-700">
                      ⚠️ Dikkat: Depo teslimatında eklenen <b>stok geri düşülmez</b>. Yanlış stok girdiyse Ürünler sayfasından manuel düzeltin.
                    </span>
                  )}
                </p>
              )}
              {_STAGE_DATE_LABELS[advanceModal.stage] && (
                <div>
                  <label className="block text-xs font-bold text-gray-600 mb-1">{_STAGE_DATE_LABELS[advanceModal.stage]}</label>
                  <input type="date" value={advanceModal.date}
                    onChange={(e) => setAdvanceModal({ ...advanceModal, date: e.target.value })}
                    className="w-full border px-3 py-2 rounded-lg text-sm" data-testid="advance-date" />
                </div>
              )}
              {advanceModal.stage === "dikim" && (
                <div>
                  <label className="block text-xs font-bold text-gray-600 mb-1">Dikim Atölyesi <span className="text-gray-400 font-normal">(opsiyonel — sonradan da girilebilir)</span></label>
                  <input type="text" value={advanceModal.workshop || ""}
                    onChange={(e) => setAdvanceModal({ ...advanceModal, workshop: e.target.value })}
                    placeholder="Ör: Yıldız Tekstil Atölyesi"
                    className="w-full border px-3 py-2 rounded-lg text-sm" data-testid="advance-workshop" />
                </div>
              )}
              <div>
                <label className="block text-xs font-bold text-gray-600 mb-1">Not <span className="text-gray-400 font-normal">(opsiyonel)</span></label>
                <textarea rows={2} value={advanceModal.note}
                  onChange={(e) => setAdvanceModal({ ...advanceModal, note: e.target.value })}
                  placeholder="Ör: kumaş 2 top eksik geldi…"
                  className="w-full border px-3 py-2 rounded-lg text-sm" />
              </div>
              <div className="flex justify-end gap-2 pt-2 border-t">
                <button type="button" onClick={() => setAdvanceModal(null)}
                  className="px-4 py-2 text-sm border rounded-lg hover:bg-gray-50">Vazgeç</button>
                <button type="button" onClick={confirmAdvance} data-testid="advance-confirm"
                  className={`px-5 py-2 text-sm text-white rounded-lg font-semibold inline-flex items-center gap-1.5 ${advanceModal.back ? "bg-gray-700 hover:bg-gray-800" : "bg-rose-600 hover:bg-rose-700"}`}>
                  {advanceModal.back ? <><ChevronLeft size={15} /> Geri Al</> : <><ChevronRight size={15} /> İlerlet</>}
                </button>
              </div>
            </div>
          )}
        </DialogContent>
      </Dialog>

      {/* Create / Edit Modal */}
      <Dialog open={modalOpen} onOpenChange={setModalOpen}>
        <DialogContent className="max-w-4xl max-h-[90vh] overflow-y-auto" data-testid="mfg-modal">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <Factory size={18} className="text-rose-600" />
              {editing ? `İmalat Kaydı: ${editing.code}` : "Yeni İmalat Kaydı"}
            </DialogTitle>
          </DialogHeader>
          <form onSubmit={(e) => { e.preventDefault(); saveRecord(); }} className="space-y-5">
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
              <div>
                <label className="block text-xs font-bold text-gray-600 mb-1">Ürün Adı / Model <span className="text-red-500">*</span></label>
                <input value={form.product_name} onChange={e => setForm({ ...form, product_name: e.target.value })} required
                  data-testid="mfg-product-name" className="w-full border px-3 py-2 rounded text-sm" />
              </div>
              <div>
                <label className="block text-xs font-bold text-gray-600 mb-1">İmalatçı <span className="text-red-500">*</span></label>
                <select value={form.supplier_id} onChange={e => setForm({ ...form, supplier_id: e.target.value })}
                  required data-testid="mfg-supplier-select" className="w-full border px-3 py-2 rounded text-sm">
                  <option value="">— İmalatçı seçin —</option>
                  {suppliers.map(s => <option key={s.id} value={s.id}>{s.name}{s.phone ? ` (${s.phone})` : ""}</option>)}
                </select>
                <button type="button" onClick={addSupplierInline}
                  className="mt-1 text-xs text-rose-600 hover:bg-rose-50 px-2 py-1 rounded" data-testid="mfg-add-supplier">
                  <Plus size={12} className="inline" /> İmalatçı Ekle
                </button>
              </div>
              <div>
                <label className="block text-xs font-bold text-gray-600 mb-1">İmalat Sipariş No <span className="text-red-500">*</span></label>
                <input value={form.order_no} onChange={e => setForm({ ...form, order_no: e.target.value })}
                  required placeholder="Örn: IMLT-2026-0012" className="w-full border px-3 py-2 rounded text-sm" />
                <div className="flex items-center gap-4 mt-2">
                  <label className="inline-flex items-center gap-1.5 text-xs font-semibold">
                    <input type="checkbox" checked={!!form.order_flags?.new}
                      onChange={e => setForm({ ...form, order_flags: { ...form.order_flags, new: e.target.checked } })}
                      className="accent-rose-600" data-testid="mfg-flag-new" /> Yeni Sipariş
                  </label>
                  <label className="inline-flex items-center gap-1.5 text-xs font-semibold">
                    <input type="checkbox" checked={!!form.order_flags?.rpt}
                      onChange={e => setForm({ ...form, order_flags: { ...form.order_flags, rpt: e.target.checked } })}
                      className="accent-rose-600" data-testid="mfg-flag-rpt" /> RPT
                  </label>
                </div>
              </div>
              <div>
                <label className="block text-xs font-bold text-gray-600 mb-1">Sipariş Tarihi <span className="text-red-500">*</span></label>
                <input type="date" value={form.agreement_date}
                  onChange={e => setForm({ ...form, agreement_date: e.target.value, expected_delivery_date: _plus21(e.target.value) })}
                  className="w-full border px-3 py-2 rounded text-sm" data-testid="mfg-order-date" />
              </div>
              <div>
                <label className="block text-xs font-bold text-gray-600 mb-1">Tahmini Teslim Tarihi <span className="text-gray-400 font-normal">(sipariş +21 gün otomatik)</span></label>
                <input type="date" value={form.expected_delivery_date} onChange={e => setForm({ ...form, expected_delivery_date: e.target.value })}
                  className="w-full border px-3 py-2 rounded text-sm" />
              </div>
              <div>
                <label className="block text-xs font-bold text-gray-600 mb-1">Stok Kodu <span className="text-gray-400 font-normal">(FCFW/FCSS seç — otomatik üretilir)</span></label>
                <div className="flex gap-2">
                  <input value={form.stock_code} readOnly placeholder="Üret butonuyla oluşur"
                    className="flex-1 border px-3 py-2 rounded text-sm font-mono bg-gray-50" data-testid="mfg-stock-code" />
                  {/* Ürün kartındaki kuralla birebir aynı: FCFW/FCSS + 6 haneli numara */}
                  <button type="button" onClick={() => setForm({ ...form, stock_code: `FCFW${Math.floor(100000 + Math.random() * 900000)}` })}
                    className="px-3 py-2 bg-orange-100 text-orange-800 rounded-lg text-[10px] font-black tracking-widest uppercase whitespace-nowrap hover:bg-orange-200" data-testid="mfg-gen-fcfw">
                    Üret (FCFW)
                  </button>
                  <button type="button" onClick={() => setForm({ ...form, stock_code: `FCSS${Math.floor(100000 + Math.random() * 900000)}` })}
                    className="px-3 py-2 bg-blue-100 text-blue-800 rounded-lg text-[10px] font-black tracking-widest uppercase whitespace-nowrap hover:bg-blue-200" data-testid="mfg-gen-fcss">
                    Üret (FCSS)
                  </button>
                </div>
              </div>
            </div>

            {/* Renk × Beden kombinasyon matrisi (kullanıcı isteği): satır=renk, kolon=beden,
                hücre=adet; satır sonunda toplam, tablonun altında sağda GENEL TOPLAM. */}
            <div>
              <label className="block text-xs font-bold text-gray-600 mb-2 flex items-center gap-2 justify-between">
                <span className="flex items-center gap-2"><Package size={12} /> Sipariş Edilen Renkler × Bedenler</span>
                <span className="flex items-center gap-2">
                  {/* Astar ön seçeneği: astarlı üründe her renk için Astar Okeyi kolonu açılır */}
                  <button type="button" onClick={() => setForm(f => ({ ...f, has_lining: !f.has_lining }))}
                    data-testid="mfg-has-lining"
                    className={`text-xs px-3 py-1.5 rounded font-semibold border transition ${form.has_lining ? "bg-indigo-600 text-white border-indigo-600" : "bg-white text-gray-500 border-gray-300 hover:border-indigo-400"}`}>
                    {form.has_lining ? "Astarlı Ürün ✓" : "Astarlı Ürün mü?"}
                  </button>
                  <button type="button" onClick={openPicker}
                    className="text-xs bg-rose-600 text-white hover:bg-rose-700 px-3 py-1.5 rounded font-semibold" data-testid="mfg-open-picker">
                    <Plus size={12} className="inline" /> Renk / Beden Seç
                  </button>
                </span>
              </label>
              {form.sizes.length === 0 ? (
                <div className="bg-gray-50 border-2 border-dashed rounded-lg p-4 text-center text-xs text-gray-400">
                  "Renk / Beden Seç" ile hazır tablodan seçim yapın — kombinasyon tablosu burada oluşur.
                </div>
              ) : (
                <div className="overflow-x-auto border border-rose-200 rounded-lg">
                  <table className="w-full text-sm" data-testid="mfg-matrix">
                    <thead className="bg-gray-50 text-xs text-black">
                      <tr>
                        <th className="text-left px-3 py-2">Renk \ Beden</th>
                        {form.sizes.map(s => (
                          <th key={s} className="px-2 py-2 text-center">
                            {s}
                            <button type="button" onClick={() => removeSize(s)} className="ml-1 text-red-400 hover:text-red-600" title="Bedeni kaldır">×</button>
                          </th>
                        ))}
                        <th className="px-3 py-2 text-right">Sipariş Adedi</th>
                        <th className="px-2 py-2 text-center">Kumaş Okeyi</th>
                        {form.has_lining && <th className="px-2 py-2 text-center">Astar Okeyi</th>}
                      </tr>
                    </thead>
                    <tbody>
                      {(form.colors.length ? form.colors : [""]).map(c => (
                        <tr key={c || "_tek"} className="border-t">
                          <td className="px-3 py-1.5 font-semibold whitespace-nowrap">
                            {c || "(Tek renk)"}
                            {c && <button type="button" onClick={() => removeColor(c)} className="ml-1.5 text-red-400 hover:text-red-600" title="Rengi kaldır">×</button>}
                          </td>
                          {form.sizes.map(s => {
                            const _ord = cellVal(c, s);
                            const _act = actualVal(c, s);
                            const _hasAct = showActuals && _act !== undefined && _act !== "" && _ord > 0;
                            const _pct = _hasAct ? ((Number(_act) - _ord) / _ord) * 100 : null;
                            return (
                            <td key={s} className="px-1.5 py-1.5 text-center align-top">
                              <input type="number" min={0} value={_ord || ""}
                                onChange={e => setCell(c, s, e.target.value)}
                                className="w-16 border px-1.5 py-1 rounded text-sm text-center" placeholder="0" />
                              {showActuals && (
                                <div className="mt-0.5">
                                  <input type="number" min={0} value={_act ?? ""}
                                    onChange={e => setActual(c, s, e.target.value)}
                                    title="Gerçekleşen (kesilen) adet"
                                    className="w-16 border border-dashed border-amber-300 bg-amber-50/40 px-1.5 py-0.5 rounded text-xs text-center" placeholder="kesilen" />
                                  {_pct != null && Math.round(_pct) !== 0 && (
                                    <p className={`text-[9px] font-bold mt-0.5 ${_pct < 0 ? "text-red-600" : "text-emerald-600"}`}
                                      title={_pct < 0 ? "Fire: sipariş edilenden az kesildi" : "Fazla: sipariş edilenden çok kesildi"}>
                                      {_pct > 0 ? "+" : ""}{_pct.toFixed(1).replace(".0", "")}%
                                    </p>
                                  )}
                                  {_pct != null && Math.round(_pct) === 0 && (
                                    <p className="text-[9px] text-gray-400 mt-0.5">tam</p>
                                  )}
                                </div>
                              )}
                            </td>
                            );
                          })}
                          <td className="px-3 py-1.5 text-right font-bold tabular-nums">{rowTotal(c)}</td>
                          <td className="px-2 py-1.5 text-center">
                            <button type="button" onClick={() => toggleApproval(c, "fabric")}
                              data-testid={`fabric-ok-${c || "tek"}`}
                              title="Bu rengin kumaşı onaylandı mı?"
                              className={`w-7 h-7 rounded border-2 text-sm font-bold transition ${approvalOf(c, "fabric") ? "bg-emerald-600 text-white border-emerald-600" : "bg-white border-gray-300 hover:border-emerald-400 text-transparent"}`}>
                              ✓
                            </button>
                          </td>
                          {form.has_lining && (
                            <td className="px-2 py-1.5 text-center">
                              <button type="button" onClick={() => toggleApproval(c, "lining")}
                                data-testid={`lining-ok-${c || "tek"}`}
                                title="Bu rengin astarı onaylandı mı?"
                                className={`w-7 h-7 rounded border-2 text-sm font-bold transition ${approvalOf(c, "lining") ? "bg-indigo-600 text-white border-indigo-600" : "bg-white border-gray-300 hover:border-indigo-400 text-transparent"}`}>
                                ✓
                              </button>
                            </td>
                          )}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
              <p className="text-sm text-gray-700 mt-1.5 text-right">
                TOPLAM SİPARİŞ ADEDİ: <b className="text-rose-700" data-testid="mfg-grand-total">{grandTotal}</b> adet
              </p>
            </div>

            {/* Renk/Beden seçim penceresi — hazır tablodan tıkla-seç + özel değer */}
            {pickerOpen && (
              <div className="fixed inset-0 z-[70] bg-black/50 flex items-center justify-center p-4" onClick={() => setPickerOpen(false)}>
                <div className="bg-white rounded-xl shadow-2xl max-w-2xl w-full max-h-[85vh] overflow-y-auto p-5" onClick={e => e.stopPropagation()} data-testid="mfg-picker">
                  <div className="flex items-center justify-between mb-4">
                    <h3 className="font-bold text-sm uppercase tracking-wider">Renk & Beden Seçimi</h3>
                    <button type="button" onClick={() => setPickerOpen(false)} className="text-gray-400 hover:text-black text-lg leading-none">×</button>
                  </div>
                  <p className="text-xs font-bold text-gray-600 mb-2">Renkler <span className="font-normal text-gray-400">({pickColors.length} seçili)</span></p>
                  <div className="flex flex-wrap gap-1.5 mb-3">
                    {[...new Set([...PRESET_COLORS, ...pickColors])].map(c => (
                      <button key={c} type="button" onClick={() => togglePick(pickColors, setPickColors, c)}
                        className={`px-2.5 py-1 rounded-full text-xs border transition-colors ${pickColors.includes(c) ? "bg-rose-600 text-white border-rose-600" : "bg-white text-gray-700 border-gray-300 hover:border-rose-400"}`}>
                        {c}
                      </button>
                    ))}
                  </div>
                  <div className="flex gap-2 mb-5">
                    <input value={customColor} onChange={e => setCustomColor(e.target.value)} placeholder="Özel renk yaz..."
                      className="border rounded px-3 py-1.5 text-xs flex-1"
                      onKeyDown={e => { if (e.key === "Enter") { e.preventDefault(); if (customColor.trim()) { togglePick(pickColors, setPickColors, customColor.trim()); setCustomColor(""); } } }} />
                    <button type="button" onClick={() => { if (customColor.trim()) { togglePick(pickColors, setPickColors, customColor.trim()); setCustomColor(""); } }}
                      className="text-xs border rounded px-3 hover:bg-gray-50">Ekle</button>
                  </div>
                  <p className="text-xs font-bold text-gray-600 mb-2">Bedenler <span className="font-normal text-gray-400">({pickSizes.length} seçili)</span></p>
                  <div className="flex flex-wrap gap-1.5 mb-3">
                    {[...new Set([...PRESET_SIZES, ...pickSizes])].map(s => (
                      <button key={s} type="button" onClick={() => togglePick(pickSizes, setPickSizes, s)}
                        className={`px-2.5 py-1 rounded-full text-xs border transition-colors ${pickSizes.includes(s) ? "bg-rose-600 text-white border-rose-600" : "bg-white text-gray-700 border-gray-300 hover:border-rose-400"}`}>
                        {s}
                      </button>
                    ))}
                  </div>
                  <div className="flex gap-2 mb-5">
                    <input value={customSize} onChange={e => setCustomSize(e.target.value)} placeholder="Özel beden yaz..."
                      className="border rounded px-3 py-1.5 text-xs flex-1"
                      onKeyDown={e => { if (e.key === "Enter") { e.preventDefault(); if (customSize.trim()) { togglePick(pickSizes, setPickSizes, customSize.trim().toUpperCase()); setCustomSize(""); } } }} />
                    <button type="button" onClick={() => { if (customSize.trim()) { togglePick(pickSizes, setPickSizes, customSize.trim().toUpperCase()); setCustomSize(""); } }}
                      className="text-xs border rounded px-3 hover:bg-gray-50">Ekle</button>
                  </div>
                  <div className="flex justify-end gap-2 border-t pt-3">
                    <button type="button" onClick={() => setPickerOpen(false)} className="px-4 py-2 text-sm border rounded-lg">Vazgeç</button>
                    <button type="button" onClick={applyPicker} data-testid="mfg-picker-apply"
                      className="px-4 py-2 text-sm bg-rose-600 text-white rounded-lg hover:bg-rose-700 font-semibold">
                      Uygula ({pickColors.length} renk × {pickSizes.length} beden)
                    </button>
                  </div>
                </div>
              </div>
            )}

            {/* Finansal — üç kolon TAM HİZALI: eşit etiket satırı + eşit yükseklikte kontrol + sabit ipucu alanı */}
            <div className="grid grid-cols-1 md:grid-cols-3 gap-3 items-start">
              <div>
                <label className="block text-xs font-bold text-gray-600 mb-1 h-4">Birim Fiyat (₺, KDV Hariç)</label>
                <input type="number" step="0.01" value={form.unit_price}
                  onChange={e => setForm({ ...form, unit_price: e.target.value })}
                  className="w-full h-[42px] border px-3 rounded text-sm" />
                <p className="mt-1 min-h-[16px]" />
              </div>
              <div>
                <label className="block text-xs font-bold text-gray-600 mb-1 h-4">Toplam Anlaşma Bedeli (₺)</label>
                <div className="w-full h-[42px] border px-3 rounded text-sm bg-gray-50 font-semibold tabular-nums whitespace-nowrap flex items-center" data-testid="mfg-agreed-total">
                  {(Number(form.unit_price || 0) * grandTotal).toLocaleString("tr-TR", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} ₺
                  <span className="text-[11px] text-gray-400 font-normal ml-2">({grandTotal.toLocaleString("tr-TR")} adet × {Number(form.unit_price || 0).toLocaleString("tr-TR", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} ₺)</span>
                </div>
                <p className="mt-1 min-h-[16px]" />
              </div>
              <div>
                <label className="block text-xs font-bold text-gray-600 mb-1 h-4">Ödeme Durumu</label>
                <button type="button"
                  onClick={() => setForm(f => ({ ...f, payment_done: !f.payment_done }))}
                  data-testid="mfg-payment-done"
                  className={`w-full h-[42px] px-3 rounded text-sm font-semibold border-2 transition flex items-center justify-center gap-2
                    ${form.payment_done ? "bg-emerald-600 text-white border-emerald-600" : "bg-white text-gray-600 border-gray-300 hover:border-emerald-400"}`}>
                  <CheckCircle2 size={16} /> {form.payment_done ? "Ödeme Yapıldı ✓" : "Ödeme Yapıldı mı?"}
                </button>
                <p className="mt-1 min-h-[16px]" />
              </div>
            </div>

            {/* Dikim Başlangıcı — atölye adı + görsel imalat raporu (listede sütun altında da görünür) */}
            <div className="border-2 border-purple-100 bg-purple-50/30 rounded-lg p-3">
              <p className="text-xs font-bold text-purple-700 uppercase mb-2">🧵 Dikim Başlangıcı</p>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                <div>
                  <label className="block text-xs font-bold text-gray-600 mb-1">Dikim Atölyesi</label>
                  <input value={form.sewing_workshop}
                    onChange={e => setForm({ ...form, sewing_workshop: e.target.value })}
                    placeholder="Ör: Yıldız Tekstil Atölyesi"
                    data-testid="mfg-sewing-workshop"
                    className="w-full border px-3 py-2 rounded text-sm" />
                  <p className="text-[10px] text-gray-400 mt-1">Dikime ilerletirken de girilebilir; listede Dikim Başlangıcı sütununda görünür.</p>
                </div>
                <div>
                  <label className="block text-xs font-bold text-gray-600 mb-1">Görsel Rapor <span className="text-gray-400 font-normal">(imalat raporu — fotoğraf)</span></label>
                  <label className={`inline-flex items-center gap-1.5 px-3 py-2 border-2 border-dashed rounded text-xs font-semibold cursor-pointer transition ${reportUploading === "sewing_report_images" ? "opacity-50 pointer-events-none" : "border-purple-300 text-purple-700 hover:bg-purple-50"}`}>
                    {reportUploading === "sewing_report_images" ? "Yükleniyor…" : "＋ Görsel Yükle"}
                    <input type="file" accept="image/*" multiple className="hidden" data-testid="mfg-report-upload"
                      onChange={(e) => { uploadReportImages(e.target.files, "sewing_report_images"); e.target.value = ""; }} />
                  </label>
                  {(form.sewing_report_images || []).length > 0 && (
                    <div className="flex flex-wrap gap-2 mt-2">
                      {form.sewing_report_images.map((u, i) => (
                        <div key={i} className="relative group">
                          <a href={_imgUrl(u)} target="_blank" rel="noreferrer">
                            <img src={_imgUrl(u)} alt={`rapor ${i + 1}`} className="w-16 h-16 object-cover rounded border" />
                          </a>
                          <button type="button"
                            onClick={() => setForm(f => ({ ...f, sewing_report_images: f.sewing_report_images.filter((_, j) => j !== i) }))}
                            title="Görseli kaldır"
                            className="absolute -top-1.5 -right-1.5 w-4 h-4 bg-red-600 text-white rounded-full text-[10px] leading-none hidden group-hover:flex items-center justify-center">×</button>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              </div>
            </div>

            {/* Kalite Kontrol — Geçti/Kaldı (kaldıysa Re-FRI), tarih + görseller (listede sütunda görünür) */}
            <div className="border-2 border-amber-100 bg-amber-50/30 rounded-lg p-3">
              <p className="text-xs font-bold text-amber-700 uppercase mb-2">🔍 Kalite Kontrol</p>
              <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
                <div>
                  <label className="block text-xs font-bold text-gray-600 mb-1">Sonuç</label>
                  <div className="flex gap-2">
                    <button type="button" data-testid="qc-pass"
                      onClick={() => setForm(f => ({ ...f, qc_result: f.qc_result === "gecti" ? "" : "gecti" }))}
                      className={`flex-1 px-3 py-2 rounded text-sm font-bold border-2 transition ${form.qc_result === "gecti" ? "bg-emerald-600 text-white border-emerald-600" : "bg-white text-gray-600 border-gray-300 hover:border-emerald-400"}`}>
                      ✓ Geçti
                    </button>
                    <button type="button" data-testid="qc-fail"
                      onClick={() => setForm(f => ({ ...f, qc_result: f.qc_result === "kaldi" ? "" : "kaldi" }))}
                      className={`flex-1 px-3 py-2 rounded text-sm font-bold border-2 transition ${form.qc_result === "kaldi" ? "bg-red-600 text-white border-red-600" : "bg-white text-gray-600 border-gray-300 hover:border-red-400"}`}>
                      ✗ Kaldı
                    </button>
                  </div>
                  {form.qc_result === "kaldi" && (
                    <p className="text-[10px] font-bold text-red-600 mt-1">Re-FRI — yeniden kalite kontrol gerekli; listede Re-FRI rozeti görünür.</p>
                  )}
                </div>
                <div>
                  <label className="block text-xs font-bold text-gray-600 mb-1">Kalite Kontrol Tarihi</label>
                  <input type="date" value={form.qc_date}
                    onChange={e => setForm({ ...form, qc_date: e.target.value })}
                    data-testid="qc-date"
                    className="w-full border px-3 py-2 rounded text-sm" />
                </div>
                <div>
                  <label className="block text-xs font-bold text-gray-600 mb-1">Kalite Kontrol Görselleri</label>
                  <label className={`inline-flex items-center gap-1.5 px-3 py-2 border-2 border-dashed rounded text-xs font-semibold cursor-pointer transition ${reportUploading === "qc_images" ? "opacity-50 pointer-events-none" : "border-amber-300 text-amber-700 hover:bg-amber-50"}`}>
                    {reportUploading === "qc_images" ? "Yükleniyor…" : "＋ Görsel Yükle"}
                    <input type="file" accept="image/*" multiple className="hidden" data-testid="qc-upload"
                      onChange={(e) => { uploadReportImages(e.target.files, "qc_images"); e.target.value = ""; }} />
                  </label>
                  {(form.qc_images || []).length > 0 && (
                    <div className="flex flex-wrap gap-2 mt-2">
                      {form.qc_images.map((u, i) => (
                        <div key={i} className="relative group">
                          <a href={_imgUrl(u)} target="_blank" rel="noreferrer">
                            <img src={_imgUrl(u)} alt={`kk ${i + 1}`} className="w-16 h-16 object-cover rounded border" />
                          </a>
                          <button type="button"
                            onClick={() => setForm(f => ({ ...f, qc_images: f.qc_images.filter((_, j) => j !== i) }))}
                            title="Görseli kaldır"
                            className="absolute -top-1.5 -right-1.5 w-4 h-4 bg-red-600 text-white rounded-full text-[10px] leading-none hidden group-hover:flex items-center justify-center">×</button>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              </div>

              {/* 2. Kalite Kontrol — ilk kontrol KALDI ise (veya veri girildiyse) aynı yapıda ikinci tur */}
              {(form.qc_result === "kaldi" || form.qc2_result || form.qc2_date || (form.qc2_images || []).length > 0) && (
                <div className="mt-3 pt-3 border-t-2 border-red-100">
                  <p className="text-xs font-bold text-red-700 uppercase mb-2">🔁 2. Kalite Kontrol (Re-FRI)</p>
                  <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
                    <div>
                      <label className="block text-xs font-bold text-gray-600 mb-1">Sonuç</label>
                      <div className="flex gap-2">
                        <button type="button" data-testid="qc2-pass"
                          onClick={() => setForm(f => ({ ...f, qc2_result: f.qc2_result === "gecti" ? "" : "gecti" }))}
                          className={`flex-1 px-3 py-2 rounded text-sm font-bold border-2 transition ${form.qc2_result === "gecti" ? "bg-emerald-600 text-white border-emerald-600" : "bg-white text-gray-600 border-gray-300 hover:border-emerald-400"}`}>
                          ✓ Geçti
                        </button>
                        <button type="button" data-testid="qc2-fail"
                          onClick={() => setForm(f => ({ ...f, qc2_result: f.qc2_result === "kaldi" ? "" : "kaldi" }))}
                          className={`flex-1 px-3 py-2 rounded text-sm font-bold border-2 transition ${form.qc2_result === "kaldi" ? "bg-red-600 text-white border-red-600" : "bg-white text-gray-600 border-gray-300 hover:border-red-400"}`}>
                          ✗ Kaldı
                        </button>
                      </div>
                    </div>
                    <div>
                      <label className="block text-xs font-bold text-gray-600 mb-1">2. Kontrol Tarihi</label>
                      <input type="date" value={form.qc2_date}
                        onChange={e => setForm({ ...form, qc2_date: e.target.value })}
                        className="w-full border px-3 py-2 rounded text-sm" />
                    </div>
                    <div>
                      <label className="block text-xs font-bold text-gray-600 mb-1">2. Kontrol Görselleri</label>
                      <label className={`inline-flex items-center gap-1.5 px-3 py-2 border-2 border-dashed rounded text-xs font-semibold cursor-pointer transition ${reportUploading === "qc2_images" ? "opacity-50 pointer-events-none" : "border-red-300 text-red-700 hover:bg-red-50"}`}>
                        {reportUploading === "qc2_images" ? "Yükleniyor…" : "＋ Görsel Yükle"}
                        <input type="file" accept="image/*" multiple className="hidden"
                          onChange={(e) => { uploadReportImages(e.target.files, "qc2_images"); e.target.value = ""; }} />
                      </label>
                      {(form.qc2_images || []).length > 0 && (
                        <div className="flex flex-wrap gap-2 mt-2">
                          {form.qc2_images.map((u, i) => (
                            <div key={i} className="relative group">
                              <a href={_imgUrl(u)} target="_blank" rel="noreferrer">
                                <img src={_imgUrl(u)} alt={`2.kk ${i + 1}`} className="w-16 h-16 object-cover rounded border" />
                              </a>
                              <button type="button"
                                onClick={() => setForm(f => ({ ...f, qc2_images: f.qc2_images.filter((_, j) => j !== i) }))}
                                title="Görseli kaldır"
                                className="absolute -top-1.5 -right-1.5 w-4 h-4 bg-red-600 text-white rounded-full text-[10px] leading-none hidden group-hover:flex items-center justify-center">×</button>
                            </div>
                          ))}
                        </div>
                      )}
                    </div>
                  </div>
                </div>
              )}
            </div>

            {/* Depo Sevkiyatları — tarih + renk/beden adet + irsaliye görselleri; listede Sevk x/y */}
            <div className="border-2 border-sky-100 bg-sky-50/30 rounded-lg p-3">
              <div className="flex items-center justify-between mb-2">
                <p className="text-xs font-bold text-sky-700 uppercase">🚚 Depo Sevkiyatları</p>
                <button type="button" data-testid="add-delivery"
                  onClick={() => setForm(f => ({ ...f, deliveries: [...(f.deliveries || []), { date: new Date().toISOString().substring(0, 10), items: {}, note_images: [] }] }))}
                  className="px-3 py-1.5 text-xs font-bold text-sky-700 border-2 border-dashed border-sky-300 rounded hover:bg-sky-50">
                  ＋ Sevkiyat Ekle
                </button>
              </div>
              {(form.deliveries || []).length === 0 && (
                <p className="text-[11px] text-gray-400">Henüz sevkiyat girilmedi. Her parti geldiğinde "Sevkiyat Ekle" ile tarih, adetler ve irsaliye görselini kaydedin — listede toplam Sevk: x/y olarak görünür.</p>
              )}
              {(form.deliveries || []).map((d, di) => {
                const _dTot = Object.values(d.items || {}).reduce((a, b) => a + Number(b || 0), 0);
                return (
                  <div key={di} className="border bg-white rounded-lg p-2.5 mb-2">
                    <div className="flex items-center gap-3 flex-wrap mb-2">
                      <span className="text-xs font-bold text-gray-500">#{di + 1}</span>
                      <input type="date" value={d.date || ""}
                        onChange={(e) => setDeliveryField(di, { date: e.target.value })}
                        className="border px-2 py-1.5 rounded text-sm" />
                      <span className="text-xs text-gray-500">Toplam: <b className="text-sky-700">{_dTot}</b> adet</span>
                      <button type="button" onClick={() => setForm(f => ({ ...f, deliveries: f.deliveries.filter((_, j) => j !== di) }))}
                        className="ml-auto text-xs text-red-500 hover:bg-red-50 rounded px-2 py-1">Sevkiyatı Sil</button>
                    </div>
                    {/* Renk|Beden bazında gelen adetler — sipariş matrisindeki kalemler */}
                    <div className="grid grid-cols-2 md:grid-cols-4 gap-1.5 mb-2">
                      {Object.keys(form.size_distribution || {}).length === 0 && (
                        <p className="text-[11px] text-gray-400 col-span-full">Önce yukarıda sipariş adet matrisini doldurun.</p>
                      )}
                      {Object.keys(form.size_distribution || {}).map(k => (
                        <label key={k} className="flex items-center gap-1.5 text-[11px] text-gray-600">
                          {/* Kutu, ait olduğu renk|bedenin SOLUNDA — sağdaki komşu etikete aitmiş gibi görünmesin */}
                          <input type="number" min="0" value={d.items?.[k] ?? ""}
                            onChange={(e) => setDeliveryItem(di, k, e.target.value)}
                            className="w-14 border rounded px-1.5 py-1 text-xs text-right shrink-0" placeholder="0" />
                          <span className="flex-1 truncate" title={k}>{k}</span>
                        </label>
                      ))}
                    </div>
                    <div className="flex items-start gap-2 flex-wrap">
                      <label className={`inline-flex items-center gap-1.5 px-3 py-1.5 border-2 border-dashed rounded text-xs font-semibold cursor-pointer transition ${reportUploading === `delivery_${di}` ? "opacity-50 pointer-events-none" : "border-sky-300 text-sky-700 hover:bg-sky-50"}`}>
                        {reportUploading === `delivery_${di}` ? "Yükleniyor…" : "＋ İrsaliye Görseli"}
                        <input type="file" accept="image/*" multiple className="hidden"
                          onChange={(e) => { uploadDeliveryImages(di, e.target.files); e.target.value = ""; }} />
                      </label>
                      {(d.note_images || []).map((u, i) => (
                        <div key={i} className="relative group">
                          <a href={_imgUrl(u)} target="_blank" rel="noreferrer">
                            <img src={_imgUrl(u)} alt={`irsaliye ${i + 1}`} className="w-12 h-12 object-cover rounded border" />
                          </a>
                          <button type="button"
                            onClick={() => setDeliveryField(di, { note_images: (d.note_images || []).filter((_, j) => j !== i) })}
                            title="Görseli kaldır"
                            className="absolute -top-1.5 -right-1.5 w-4 h-4 bg-red-600 text-white rounded-full text-[10px] leading-none hidden group-hover:flex items-center justify-center">×</button>
                        </div>
                      ))}
                    </div>
                  </div>
                );
              })}
            </div>

            <div>
              <label className="block text-xs font-bold text-gray-600 mb-1">Notlar</label>
              <textarea rows={3} value={form.notes} onChange={e => setForm({ ...form, notes: e.target.value })}
                className="w-full border px-3 py-2 rounded text-sm" />
            </div>

            {editing && (
              <div>
                <label className="text-xs font-bold text-gray-600 mb-1 block">Aşama Geçmişi</label>
                <div className="border rounded-lg bg-gray-50 p-3 max-h-40 overflow-y-auto">
                  {(editing.stage_history || []).map((h, i) => (
                    <div key={i} className="text-xs py-1 border-b last:border-0 flex justify-between">
                      <span className={`px-2 py-0.5 rounded ${STAGE_COLORS[h.stage] || 'bg-gray-100'}`}>{h.label}</span>
                      <span className="text-gray-500">
                        {h.by} · {new Date(h.at).toLocaleString('tr-TR')}
                        {h.note && <span className="ml-1">— {h.note}</span>}
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            )}

            <div className="flex justify-end gap-2 pt-4 border-t">
              <button type="button" onClick={() => setModalOpen(false)} className="px-4 py-2 border rounded hover:bg-gray-50 text-sm">İptal</button>
              <button type="button" onClick={saveRecord} disabled={saving} data-testid="save-mfg-btn"
                className="px-4 py-2 bg-rose-600 text-white rounded hover:bg-rose-700 disabled:opacity-50 text-sm font-bold">
                <Save size={14} className="inline mr-1" /> {saving ? "Kaydediliyor..." : "Kaydet"}
              </button>
            </div>
          </form>
        </DialogContent>
      </Dialog>
    </div>
  );
}
