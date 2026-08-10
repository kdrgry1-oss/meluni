import { useState, useEffect, useCallback } from "react";
import axios from "axios";
import { toast } from "sonner";
import { RefreshCw, Search, Package, Truck, FileText, Download, CheckCircle2, XCircle } from "lucide-react";

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;
const HB = `${API}/integrations/hepsiburada`;

// HB talep (iade) statüleri — OMS claim status değerleri
const CLAIM_STATUSES = [
  { key: "", label: "Tümü" },
  { key: "AwaitingAction", label: "İşlem Bekliyor" },
  { key: "InDispute", label: "İtirazda" },
  { key: "Accepted", label: "Kabul Edildi" },
  { key: "Rejected", label: "Reddedildi" },
  { key: "Refunded", label: "İade Edildi" },
  { key: "Cancelled", label: "İptal" },
];

// HB iade ret sebep kodları (0-11). Bilinen uçlar etiketli; kalanlar kod olarak —
// kesin metinler HB satıcı panelinden teyit edilebilir.
const REJECT_REASONS = [
  { code: 0, label: "0 — Kutu boş" },
  { code: 1, label: "1 — Ret sebebi 1" },
  { code: 2, label: "2 — Ret sebebi 2" },
  { code: 3, label: "3 — Ret sebebi 3" },
  { code: 4, label: "4 — Ret sebebi 4" },
  { code: 5, label: "5 — Ret sebebi 5" },
  { code: 6, label: "6 — Ret sebebi 6" },
  { code: 7, label: "7 — Ret sebebi 7" },
  { code: 8, label: "8 — Ret sebebi 8" },
  { code: 9, label: "9 — Ret sebebi 9" },
  { code: 10, label: "10 — Ret sebebi 10" },
  { code: 11, label: "11 — Bazı parçalar eksik" },
];

const authCfg = () => ({ headers: { Authorization: `Bearer ${localStorage.getItem("token")}` } });

// Yanıt şekli OMS'e göre değişebilir; diziyi esnek çıkar.
const asArray = (data) => {
  if (Array.isArray(data)) return data;
  if (!data || typeof data !== "object") return [];
  return data.items || data.packages || data.claims || data.content || data.data || [];
};

export default function HepsiburadaOrders() {
  const [tab, setTab] = useState("packages");

  // --- Paketler ---
  const [packages, setPackages] = useState([]);
  const [loadingPkg, setLoadingPkg] = useState(false);
  const [orderNo, setOrderNo] = useState("");
  const [orderDetail, setOrderDetail] = useState(null);
  const [lookingUp, setLookingUp] = useState(false);

  // --- İadeler ---
  const [claims, setClaims] = useState([]);
  const [claimStatus, setClaimStatus] = useState("AwaitingAction");
  const [loadingClaims, setLoadingClaims] = useState(false);
  const [rejecting, setRejecting] = useState(null); // claim_number
  const [rejectReason, setRejectReason] = useState(11);
  const [rejectStatement, setRejectStatement] = useState("");

  // --- Listing & Durum (MPOP auth ile canlı çalışır) ---
  const [listings, setListings] = useState([]);
  const [loadingListings, setLoadingListings] = useState(false);
  const [listingSku, setListingSku] = useState("");
  const [uploadKind, setUploadKind] = useState("price");
  const [uploadId, setUploadId] = useState("");
  const [trackingId, setTrackingId] = useState("");

  // Son ham yanıt — creds gelince gerçek şemayı görmek için
  const [rawResult, setRawResult] = useState(null);

  const showRaw = (label, data) => setRawResult({ label, data });

  const loadPackages = useCallback(async () => {
    setLoadingPkg(true);
    try {
      const res = await axios.get(`${HB}/packages?offset=0&limit=100`, authCfg());
      const arr = asArray(res.data?.data);
      setPackages(arr);
      showRaw("packages", res.data?.data);
      if (!arr.length) toast.info("Paket bulunamadı (ya da yanıt şeması farklı — ham yanıta bakın)");
    } catch (err) {
      toast.error(err.response?.data?.detail || "Paketler alınamadı");
    } finally {
      setLoadingPkg(false);
    }
  }, []);

  const lookupOrder = async () => {
    if (!orderNo.trim()) { toast.warning("Sipariş numarası girin"); return; }
    setLookingUp(true);
    setOrderDetail(null);
    try {
      const res = await axios.get(`${HB}/orders/${encodeURIComponent(orderNo.trim())}`, authCfg());
      setOrderDetail(res.data?.data || null);
      showRaw(`order ${orderNo}`, res.data?.data);
    } catch (err) {
      toast.error(err.response?.data?.detail || "Sipariş alınamadı");
    } finally {
      setLookingUp(false);
    }
  };

  const createPackage = async (lineItems) => {
    if (!lineItems.length) { toast.warning("Paketlenecek kalem yok"); return; }
    try {
      const res = await axios.post(`${HB}/packages`, { line_items: lineItems, parcel_quantity: 1 }, authCfg());
      toast.success("Paketleme isteği gönderildi");
      showRaw("create package", res.data?.data);
      loadPackages();
    } catch (err) {
      toast.error(err.response?.data?.detail || "Paketleme başarısız");
    }
  };

  const getLabel = async (pn) => {
    try {
      const res = await axios.get(`${HB}/packages/${encodeURIComponent(pn)}/label?fmt=base64zpl`, authCfg());
      const payload = res.data?.data;
      showRaw(`label ${pn}`, payload);
      // base64zpl/png → indirilebilir dosya
      const raw = typeof payload === "string" ? payload : (payload?.label || payload?.content || "");
      if (raw) {
        const blob = new Blob([raw], { type: "text/plain" });
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url; a.download = `hb-etiket-${pn}.zpl`; a.click();
        URL.revokeObjectURL(url);
        toast.success("Etiket indirildi");
      } else {
        toast.info("Etiket alındı — ham yanıta bakın");
      }
    } catch (err) {
      toast.error(err.response?.data?.detail || "Etiket alınamadı");
    }
  };

  const sendInvoice = async (pn) => {
    const link = window.prompt("Fatura linki (PDF URL):");
    if (!link) return;
    try {
      const res = await axios.put(`${HB}/packages/${encodeURIComponent(pn)}/invoice`, { invoice_link: link }, authCfg());
      toast.success("Fatura iletildi");
      showRaw(`invoice ${pn}`, res.data?.data);
    } catch (err) {
      toast.error(err.response?.data?.detail || "Fatura iletilemedi");
    }
  };

  const changeCargo = async (pn) => {
    const code = window.prompt("Kargo firması kısa adı (örn: Yurtici, MNG, Aras, PTT):");
    if (!code) return;
    try {
      const res = await axios.put(`${HB}/packages/${encodeURIComponent(pn)}/cargo`, { cargo_company_short_name: code }, authCfg());
      toast.success("Kargo firması değiştirildi");
      showRaw(`cargo ${pn}`, res.data?.data);
      loadPackages();
    } catch (err) {
      toast.error(err.response?.data?.detail || "Kargo değiştirilemedi");
    }
  };

  const deliver = async (pn) => {
    if (!window.confirm(`${pn} paketi "teslim edildi" olarak işaretlensin mi?`)) return;
    try {
      const res = await axios.post(`${HB}/packages/${encodeURIComponent(pn)}/deliver`, {}, authCfg());
      toast.success("Teslim bilgisi gönderildi");
      showRaw(`deliver ${pn}`, res.data?.data);
      loadPackages();
    } catch (err) {
      toast.error(err.response?.data?.detail || "Teslim bilgisi gönderilemedi");
    }
  };

  const cancelLine = async (lid) => {
    if (!window.confirm("Bu kalem iptal edilecek (para cezasına tabi olabilir). Devam?")) return;
    try {
      const res = await axios.post(`${HB}/lineitems/${encodeURIComponent(lid)}/cancel`, { reason_id: "83" }, authCfg());
      toast.success("Kalem iptal edildi");
      showRaw(`cancel ${lid}`, res.data?.data);
    } catch (err) {
      toast.error(err.response?.data?.detail || "İptal başarısız");
    }
  };

  const loadClaims = useCallback(async () => {
    setLoadingClaims(true);
    try {
      const q = claimStatus ? `?status=${encodeURIComponent(claimStatus)}&offset=0&limit=100` : `?offset=0&limit=100`;
      const res = await axios.get(`${HB}/claims${q}`, authCfg());
      const arr = asArray(res.data?.data);
      setClaims(arr);
      showRaw("claims", res.data?.data);
      if (!arr.length) toast.info("Talep bulunamadı (ya da yanıt şeması farklı)");
    } catch (err) {
      toast.error(err.response?.data?.detail || "Talepler alınamadı");
    } finally {
      setLoadingClaims(false);
    }
  }, [claimStatus]);

  const acceptClaim = async (no) => {
    if (!window.confirm(`${no} talebi kabul edilsin mi?`)) return;
    try {
      const res = await axios.post(`${HB}/claims/${encodeURIComponent(no)}/accept`, {}, authCfg());
      toast.success("Talep kabul edildi");
      showRaw(`accept ${no}`, res.data?.data);
      loadClaims();
    } catch (err) {
      toast.error(err.response?.data?.detail || "Kabul başarısız");
    }
  };

  const submitReject = async (no) => {
    try {
      const res = await axios.post(`${HB}/claims/${encodeURIComponent(no)}/reject`,
        { reason: Number(rejectReason), merchant_statement: rejectStatement }, authCfg());
      toast.success("Talep reddedildi");
      showRaw(`reject ${no}`, res.data?.data);
      setRejecting(null); setRejectStatement("");
      loadClaims();
    } catch (err) {
      toast.error(err.response?.data?.detail || "Ret başarısız");
    }
  };

  const loadListings = useCallback(async () => {
    setLoadingListings(true);
    try {
      const q = listingSku.trim()
        ? `?offset=0&limit=100&merchant_sku=${encodeURIComponent(listingSku.trim())}`
        : `?offset=0&limit=100`;
      const res = await axios.get(`${HB}/listings${q}`, authCfg());
      const arr = asArray(res.data?.data);
      setListings(arr);
      showRaw("listings", res.data?.data);
      if (!arr.length) toast.info("Listing bulunamadı (ya da yanıt şeması farklı)");
    } catch (err) {
      toast.error(err.response?.data?.detail || "Listing alınamadı");
    } finally {
      setLoadingListings(false);
    }
  }, [listingSku]);

  const toggleListing = async (hbSku, activate) => {
    try {
      const res = await axios.post(`${HB}/listings/${encodeURIComponent(hbSku)}/${activate ? "activate" : "deactivate"}`, {}, authCfg());
      toast.success(activate ? "Satışa açıldı" : "Satışa kapatıldı");
      showRaw(`${activate ? "activate" : "deactivate"} ${hbSku}`, res.data?.data);
      loadListings();
    } catch (err) {
      toast.error(err.response?.data?.detail || "İşlem başarısız");
    }
  };

  const checkUpload = async () => {
    if (!uploadId.trim()) { toast.warning("Yükleme ID girin"); return; }
    try {
      const res = await axios.get(`${HB}/listings/status/${uploadKind}/${encodeURIComponent(uploadId.trim())}`, authCfg());
      showRaw(`${uploadKind} upload ${uploadId}`, res.data?.data);
      toast.success("Durum getirildi — ham yanıta bakın");
    } catch (err) {
      toast.error(err.response?.data?.detail || "Durum alınamadı");
    }
  };

  const checkTracking = async () => {
    if (!trackingId.trim()) { toast.warning("Tracking ID girin"); return; }
    try {
      const res = await axios.get(`${HB}/products/tracking/${encodeURIComponent(trackingId.trim())}`, authCfg());
      showRaw(`tracking ${trackingId}`, res.data?.data);
      toast.success("Takip durumu getirildi — ham yanıta bakın");
    } catch (err) {
      toast.error(err.response?.data?.detail || "Takip alınamadı");
    }
  };

  useEffect(() => {
    if (tab === "packages") loadPackages();
    if (tab === "claims") loadClaims();
    if (tab === "listings") loadListings();
  }, [tab, loadPackages, loadClaims, loadListings]);

  // Sipariş kalemlerini esnek çıkar (şema OMS'e göre değişebilir)
  const orderLines = orderDetail ? asArray(orderDetail.items || orderDetail.lineItems || orderDetail) : [];
  const lineId = (it) => it.id || it.lineItemId || it.orderLineItemId || it.lineItemUniqueId;
  const lineQty = (it) => it.quantity || it.amount || 1;

  return (
    <div className="p-6 max-w-6xl mx-auto">
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-black text-gray-900 flex items-center gap-2">
          <Package className="text-[#FF6000]" size={26} /> Hepsiburada Yönetim
        </h1>
        <button
          onClick={() => (tab === "packages" ? loadPackages() : tab === "claims" ? loadClaims() : loadListings())}
          className="flex items-center gap-2 px-4 py-2 bg-[#FF6000] text-white rounded-lg text-sm font-bold hover:bg-[#e05600]"
        >
          <RefreshCw size={16} /> Yenile
        </button>
      </div>

      <div className="bg-orange-50 border border-orange-200 text-orange-800 text-xs rounded-lg p-3 mb-5">
        <b>Listing &amp; Durum</b> sekmesi mevcut MPOP kimliğiyle <b>canlı çalışır</b> (fiyat/stok yükleme durumu, listing aç/kapat, ürün import takibi).
        <b> Paketler</b> ve <b>İadeler</b> ise OMS üretim kimliği (ceyjewelry merchant) gerektirir; kimlik gelmeden net hata döner.
        Yanıt şeması OMS'e göre değişebildiğinden her işlemin <b>ham yanıtı</b> aşağıda gösterilir.
      </div>

      <div className="flex gap-2 mb-5 border-b">
        {[["packages", "Paketler / Siparişler"], ["claims", "İadeler (Talepler)"], ["listings", "Listing & Durum"]].map(([k, lbl]) => (
          <button
            key={k}
            onClick={() => setTab(k)}
            className={`px-4 py-2 text-sm font-bold border-b-2 -mb-px ${tab === k ? "border-[#FF6000] text-[#FF6000]" : "border-transparent text-gray-500 hover:text-gray-800"}`}
          >
            {lbl}
          </button>
        ))}
      </div>

      {tab === "packages" && (
        <div className="space-y-6">
          {/* Sipariş ara */}
          <div className="bg-white rounded-xl border p-4">
            <div className="flex gap-2">
              <input
                value={orderNo}
                onChange={(e) => setOrderNo(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && lookupOrder()}
                placeholder="Sipariş numarası (örn: HB12345 veya 12345)"
                className="flex-1 border rounded-lg px-3 py-2 text-sm"
              />
              <button onClick={lookupOrder} disabled={lookingUp}
                className="flex items-center gap-2 px-4 py-2 bg-gray-900 text-white rounded-lg text-sm font-bold disabled:opacity-50">
                <Search size={16} /> {lookingUp ? "Aranıyor..." : "Getir"}
              </button>
            </div>
            {orderDetail && (
              <div className="mt-4 border-t pt-4">
                <div className="flex items-center justify-between mb-3">
                  <span className="text-sm font-bold text-gray-700">Sipariş kalemleri</span>
                  <button
                    onClick={() => createPackage(orderLines.map((it) => ({ id: lineId(it), quantity: lineQty(it) })).filter((x) => x.id))}
                    className="flex items-center gap-1 px-3 py-1.5 bg-[#FF6000] text-white rounded-lg text-xs font-bold hover:bg-[#e05600]">
                    <Package size={14} /> Tümünü Paketle
                  </button>
                </div>
                {orderLines.length ? (
                  <div className="space-y-2">
                    {orderLines.map((it, i) => (
                      <div key={lineId(it) || i} className="flex items-center justify-between bg-gray-50 rounded-lg px-3 py-2 text-sm">
                        <span className="truncate">{it.productName || it.name || it.sku || lineId(it) || `Kalem ${i + 1}`} <span className="text-gray-400">× {lineQty(it)}</span></span>
                        <button onClick={() => cancelLine(lineId(it))}
                          className="flex items-center gap-1 text-red-600 hover:bg-red-50 px-2 py-1 rounded text-xs font-bold">
                          <XCircle size={14} /> İptal
                        </button>
                      </div>
                    ))}
                  </div>
                ) : (
                  <p className="text-xs text-gray-400">Kalem ayrıştırılamadı — ham yanıta bakın.</p>
                )}
              </div>
            )}
          </div>

          {/* Paket listesi */}
          <div className="bg-white rounded-xl border">
            <div className="px-4 py-3 border-b text-sm font-bold text-gray-700">
              Paketler {loadingPkg && <span className="text-gray-400 font-normal">(yükleniyor...)</span>}
            </div>
            {packages.length ? (
              <div className="divide-y">
                {packages.map((p, i) => {
                  const pn = p.packageNumber || p.id || p.packageId || p.cargoTrackingNumber || `pkg-${i}`;
                  return (
                    <div key={pn} className="p-4 flex flex-wrap items-center justify-between gap-3">
                      <div className="text-sm">
                        <div className="font-bold text-gray-900">{pn}</div>
                        <div className="text-gray-500 text-xs">{p.status || p.packageStatus || "-"} · {p.cargoCompany || p.cargoCompanyName || ""}</div>
                      </div>
                      <div className="flex flex-wrap gap-2">
                        <button onClick={() => getLabel(pn)} className="flex items-center gap-1 px-2.5 py-1.5 bg-gray-100 hover:bg-gray-200 rounded text-xs font-bold"><Download size={14} /> Etiket</button>
                        <button onClick={() => sendInvoice(pn)} className="flex items-center gap-1 px-2.5 py-1.5 bg-gray-100 hover:bg-gray-200 rounded text-xs font-bold"><FileText size={14} /> Fatura</button>
                        <button onClick={() => changeCargo(pn)} className="flex items-center gap-1 px-2.5 py-1.5 bg-gray-100 hover:bg-gray-200 rounded text-xs font-bold"><Truck size={14} /> Kargo</button>
                        <button onClick={() => deliver(pn)} className="flex items-center gap-1 px-2.5 py-1.5 bg-green-50 text-green-700 hover:bg-green-100 rounded text-xs font-bold"><CheckCircle2 size={14} /> Teslim</button>
                      </div>
                    </div>
                  );
                })}
              </div>
            ) : (
              <div className="p-8 text-center text-sm text-gray-400">{loadingPkg ? "Yükleniyor..." : "Paket yok"}</div>
            )}
          </div>
        </div>
      )}

      {tab === "claims" && (
        <div className="space-y-4">
          <div className="flex items-center gap-2">
            <select value={claimStatus} onChange={(e) => setClaimStatus(e.target.value)} className="border rounded-lg px-3 py-2 text-sm">
              {CLAIM_STATUSES.map((s) => <option key={s.key} value={s.key}>{s.label}</option>)}
            </select>
            <button onClick={loadClaims} disabled={loadingClaims}
              className="flex items-center gap-2 px-4 py-2 bg-gray-900 text-white rounded-lg text-sm font-bold disabled:opacity-50">
              <Search size={16} /> {loadingClaims ? "Yükleniyor..." : "Getir"}
            </button>
          </div>

          <div className="bg-white rounded-xl border">
            {claims.length ? (
              <div className="divide-y">
                {claims.map((c, i) => {
                  const no = c.claimNumber || c.id || c.number || `claim-${i}`;
                  return (
                    <div key={no} className="p-4">
                      <div className="flex flex-wrap items-center justify-between gap-3">
                        <div className="text-sm">
                          <div className="font-bold text-gray-900">{no}</div>
                          <div className="text-gray-500 text-xs">{c.status || "-"} · {c.productName || c.merchantSku || c.sku || ""}</div>
                        </div>
                        <div className="flex gap-2">
                          <button onClick={() => acceptClaim(no)} className="flex items-center gap-1 px-3 py-1.5 bg-green-50 text-green-700 hover:bg-green-100 rounded text-xs font-bold"><CheckCircle2 size={14} /> Kabul</button>
                          <button onClick={() => { setRejecting(rejecting === no ? null : no); setRejectStatement(""); }}
                            className="flex items-center gap-1 px-3 py-1.5 bg-red-50 text-red-700 hover:bg-red-100 rounded text-xs font-bold"><XCircle size={14} /> Reddet</button>
                        </div>
                      </div>
                      {rejecting === no && (
                        <div className="mt-3 bg-red-50 border border-red-200 rounded-lg p-3 space-y-2">
                          <select value={rejectReason} onChange={(e) => setRejectReason(e.target.value)} className="w-full border rounded px-2 py-1.5 text-sm">
                            {REJECT_REASONS.map((r) => <option key={r.code} value={r.code}>{r.label}</option>)}
                          </select>
                          <textarea value={rejectStatement} onChange={(e) => setRejectStatement(e.target.value)}
                            placeholder="Satıcı açıklaması (zorunlu olabilir)" rows={2}
                            className="w-full border rounded px-2 py-1.5 text-sm" />
                          <div className="flex gap-2">
                            <button onClick={() => submitReject(no)} className="px-3 py-1.5 bg-red-600 text-white rounded text-xs font-bold hover:bg-red-700">Reddi Gönder</button>
                            <button onClick={() => setRejecting(null)} className="px-3 py-1.5 bg-gray-100 rounded text-xs font-bold">Vazgeç</button>
                          </div>
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            ) : (
              <div className="p-8 text-center text-sm text-gray-400">{loadingClaims ? "Yükleniyor..." : "Talep yok"}</div>
            )}
          </div>
        </div>
      )}

      {tab === "listings" && (
        <div className="space-y-6">
          {/* Listing listesi */}
          <div className="bg-white rounded-xl border p-4">
            <div className="flex gap-2 mb-3">
              <input value={listingSku} onChange={(e) => setListingSku(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && loadListings()}
                placeholder="Merchant SKU ile filtrele (boş = tümü)"
                className="flex-1 border rounded-lg px-3 py-2 text-sm" />
              <button onClick={loadListings} disabled={loadingListings}
                className="flex items-center gap-2 px-4 py-2 bg-gray-900 text-white rounded-lg text-sm font-bold disabled:opacity-50">
                <Search size={16} /> {loadingListings ? "Yükleniyor..." : "Getir"}
              </button>
            </div>
            {listings.length ? (
              <div className="divide-y border-t">
                {listings.map((l, i) => {
                  const hbSku = l.hepsiburadaSku || l.hbSku || l.HepsiburadaSku || "";
                  const mSku = l.merchantSku || l.MerchantSku || "";
                  return (
                    <div key={hbSku || mSku || i} className="py-3 flex flex-wrap items-center justify-between gap-3 text-sm">
                      <div>
                        <div className="font-bold text-gray-900">{mSku || hbSku || `Kalem ${i + 1}`}</div>
                        <div className="text-gray-500 text-xs">HB SKU: {hbSku || "-"} · Fiyat: {l.price ?? l.Price ?? "-"} · Stok: {l.availableStock ?? l.AvailableStock ?? "-"} · {l.status || l.Status || ""}</div>
                      </div>
                      <div className="flex gap-2">
                        <button onClick={() => toggleListing(hbSku, true)} disabled={!hbSku}
                          className="flex items-center gap-1 px-2.5 py-1.5 bg-green-50 text-green-700 hover:bg-green-100 rounded text-xs font-bold disabled:opacity-40"><CheckCircle2 size={14} /> Aç</button>
                        <button onClick={() => toggleListing(hbSku, false)} disabled={!hbSku}
                          className="flex items-center gap-1 px-2.5 py-1.5 bg-red-50 text-red-700 hover:bg-red-100 rounded text-xs font-bold disabled:opacity-40"><XCircle size={14} /> Kapat</button>
                      </div>
                    </div>
                  );
                })}
              </div>
            ) : (
              <div className="py-6 text-center text-sm text-gray-400">{loadingListings ? "Yükleniyor..." : "Listing yok"}</div>
            )}
          </div>

          {/* Yükleme durumu sorgula */}
          <div className="bg-white rounded-xl border p-4">
            <div className="text-sm font-bold text-gray-700 mb-2">Fiyat/Stok Yükleme Durumu</div>
            <p className="text-xs text-gray-400 mb-3">Kategori/ürün stok-fiyat gönderiminden dönen yükleme ID'siyle sonucu (MinLock/MaxLock vb. reddedilen SKU'lar) sorgulayın.</p>
            <div className="flex flex-wrap gap-2">
              <select value={uploadKind} onChange={(e) => setUploadKind(e.target.value)} className="border rounded-lg px-3 py-2 text-sm">
                <option value="price">price (fiyat)</option>
                <option value="stock">stock (stok)</option>
                <option value="inventory">inventory (birleşik)</option>
              </select>
              <input value={uploadId} onChange={(e) => setUploadId(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && checkUpload()}
                placeholder="Yükleme ID" className="flex-1 border rounded-lg px-3 py-2 text-sm" />
              <button onClick={checkUpload} className="flex items-center gap-2 px-4 py-2 bg-[#FF6000] text-white rounded-lg text-sm font-bold hover:bg-[#e05600]">
                <Search size={16} /> Sorgula
              </button>
            </div>
          </div>

          {/* Ürün import takibi */}
          <div className="bg-white rounded-xl border p-4">
            <div className="text-sm font-bold text-gray-700 mb-2">Ürün Import Takibi</div>
            <p className="text-xs text-gray-400 mb-3">Ürün gönderiminden dönen tracking ID ile import durumunu (eşleşti/reddedildi/oluşturuldu) sorgulayın.</p>
            <div className="flex gap-2">
              <input value={trackingId} onChange={(e) => setTrackingId(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && checkTracking()}
                placeholder="Tracking ID" className="flex-1 border rounded-lg px-3 py-2 text-sm" />
              <button onClick={checkTracking} className="flex items-center gap-2 px-4 py-2 bg-gray-900 text-white rounded-lg text-sm font-bold">
                <Search size={16} /> Sorgula
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Ham yanıt (şema teşhisi) */}
      {rawResult && (
        <div className="mt-6 bg-gray-900 text-gray-100 rounded-xl p-4">
          <div className="text-xs font-bold text-gray-400 mb-2">Son ham yanıt: {rawResult.label}</div>
          <pre className="text-[11px] overflow-auto max-h-72 whitespace-pre-wrap">{JSON.stringify(rawResult.data, null, 2)}</pre>
        </div>
      )}
    </div>
  );
}
