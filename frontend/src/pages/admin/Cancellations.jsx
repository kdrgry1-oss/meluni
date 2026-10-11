import { useState, useEffect, useCallback } from "react";
import axios from "axios";
import { XCircle, Search, RefreshCw, X, Trash2 } from "lucide-react";
import RefundBankBox from "../../components/admin/RefundBankBox";
import { optimizeImg } from "../../lib/img";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "../../components/ui/select";

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;

// Platform etiketi — en başta web sitemiz, sonra pazaryerleri
const PLATFORM_LABELS = {
  "": "Web Sitesi",
  meluni: "Web Sitesi",
  trendyol: "Trendyol",
  hepsiburada: "Hepsiburada",
  temu: "Temu",
  n11: "N11",
  amazon: "Amazon",
  amazon_tr: "Amazon TR",
  amazon_de: "Amazon DE",
  aliexpress: "AliExpress",
  etsy: "Etsy",
  ciceksepeti: "Çiçek Sepeti",
  pttavm: "PTT AVM",
};

// Sipariş durumları (backend order_statuses.py kataloğu ile birebir)
const STATUS_OPTIONS = [
  { key: "pending", label: "Onay Bekliyor" },
  { key: "awaiting_payment", label: "Ödeme Bekleniyor (Havale/EFT)" },
  { key: "payment_notified", label: "Ödeme Bildirimi Alındı" },
  { key: "confirmed", label: "Onaylandı" },
  { key: "preparing", label: "Hazırlanıyor" },
  { key: "processing", label: "İşleme Alındı" },
  { key: "ready_to_ship", label: "Kargoya Hazır" },
  { key: "shipped", label: "Kargoya Verildi" },
  { key: "in_transit", label: "Taşınıyor" },
  { key: "out_for_delivery", label: "Dağıtımda" },
  { key: "delivered", label: "Teslim Edildi" },
  { key: "undelivered", label: "Teslim Edilemedi" },
  { key: "return_requested", label: "İade Talebi Oluşturuldu" },
  { key: "return_approved", label: "İade Onaylandı" },
  { key: "return_rejected", label: "İade Reddedildi" },
  { key: "return_in_transit", label: "İade Kargoda" },
  { key: "returned", label: "İade Tamamlandı" },
  { key: "refunded", label: "İade Bedeli Ödendi" },
  { key: "partial_refunded", label: "Kısmi İade Yapıldı" },
  { key: "cancelled", label: "İptal Edildi" },
  { key: "cancel_refunded", label: "Ödemeli İptal Onaylandı" },
];

function platformLabel(p) {
  return PLATFORM_LABELS[(p || "").toLowerCase()] || (p || "Web Sitesi");
}

function money(v) {
  const n = Number(v || 0);
  return n.toLocaleString("tr-TR", { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + " ₺";
}

function custName(o) {
  const a = o.shipping_address || {};
  return `${a.first_name || ""} ${a.last_name || ""}`.trim() || a.full_name || o.customer_name || "—";
}
function invoiceName(o) {
  const b = o.billing_address || {};
  return `${b.first_name || ""} ${b.last_name || ""}`.trim() || b.full_name || "";
}
function itemsOf(o) {
  return o.items || o.lines || [];
}
function itemQty(it) {
  return Number(it.quantity || it.qty || 1);
}
function itemUnit(it) {
  return Number(it.unit_price ?? it.price ?? 0);
}

export default function Cancellations() {
  const [orders, setOrders] = useState([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [search, setSearch] = useState("");
  const [searchInput, setSearchInput] = useState("");
  const [payFilter, setPayFilter] = useState("");  // #21: ödeme tipi filtresi
  const [loading, setLoading] = useState(true);
  const [detail, setDetail] = useState(null);   // detay modalı için sipariş
  // KVKK: liste telefon/açık adresi maskeli verir; "Göster" tam değeri açar (sayaçlı + denetimli).
  const [revealing, setRevealing] = useState(false);
  const revealContact = async (orderId) => {
    setRevealing(true);
    try {
      const token = localStorage.getItem("token");
      const r = await axios.get(`${API}/orders/${orderId}/contact`, { headers: { Authorization: `Bearer ${token}` } });
      const c = r.data || {};
      setDetail((d) => (d && d.id === orderId ? {
        ...d, _revealed: true,
        shipping_address: { ...(d.shipping_address || {}), ...(c.shipping_address || {}) },
        ...(c.phone ? { phone: c.phone } : {}),
      } : d));
    } catch (e) {
      alert("İletişim bilgisi açılamadı: " + (e?.response?.data?.detail || e.message));
    } finally {
      setRevealing(false);
    }
  };
  const [savingId, setSavingId] = useState(null); // durum güncellenirken
  const [deletingId, setDeletingId] = useState(null); // silinirken
  // Tek kaynak: durum listesi Ayarlar → Sipariş Durumları'ndan (görünürlük + özel durumlar dahil).
  const [statusOptions, setStatusOptions] = useState(STATUS_OPTIONS);
  const [statusLabelAll, setStatusLabelAll] = useState(
    Object.fromEntries(STATUS_OPTIONS.map((s) => [s.key, s.label]))
  );
  useEffect(() => {
    (async () => {
      try {
        const token = localStorage.getItem("token");
        const r = await axios.get(`${API}/settings/order-statuses`, { headers: { Authorization: `Bearer ${token}` } });
        const all = r.data?.statuses || [];
        if (all.length) {
          setStatusLabelAll(Object.fromEntries(all.map((s) => [s.key, s.label])));
          setStatusOptions(all.filter((s) => s.active).map((s) => ({ key: s.key, label: s.label })));
        }
      } catch { /* hardcoded fallback */ }
    })();
  }, []);
  // Mevcut değer "görünür" listede yoksa, seçili kalsın diye başa eklenir.
  const optsFor = (cur) =>
    (statusOptions.some((s) => s.key === cur) ? statusOptions : [{ key: cur, label: statusLabelAll[cur] || cur }, ...statusOptions]);
  const pageSize = 20;

  const fetchCancelled = useCallback(async () => {
    setLoading(true);
    try {
      const token = localStorage.getItem("token");
      let url = `${API}/orders?page=${page}&limit=${pageSize}&status=cancelled,cancel_refunded`;
      if (search) url += `&search=${encodeURIComponent(search)}`;
      if (payFilter) url += `&payment_method=${encodeURIComponent(payFilter)}`;  // #21
      const res = await axios.get(url, { headers: { Authorization: `Bearer ${token}` } });
      setOrders(res.data?.orders || []);
      setTotal(res.data?.total || 0);
    } catch (e) {
      setOrders([]);
      setTotal(0);
    } finally {
      setLoading(false);
    }
  }, [page, search, payFilter]);

  useEffect(() => { fetchCancelled(); }, [fetchCancelled]);

  const applySearch = () => { setPage(1); setSearch(searchInput.trim()); };
  const totalPages = Math.max(1, Math.ceil(total / pageSize));

  async function changeStatus(orderId, status) {
    if (!orderId) return;
    setSavingId(orderId);
    try {
      const token = localStorage.getItem("token");
      await axios.put(
        `${API}/orders/${orderId}/status?status=${encodeURIComponent(status)}`,
        null,
        { headers: { Authorization: `Bearer ${token}` } }
      );
      // Açık detay aynı siparişse durumunu güncelle
      setDetail((d) => (d && d.id === orderId ? { ...d, status } : d));
      // "cancelled" dışına alındıysa bu listeden düşecek
      await fetchCancelled();
    } catch (e) {
      alert("Durum güncellenemedi: " + (e?.response?.data?.detail || e.message));
    } finally {
      setSavingId(null);
    }
  }

  async function handleDelete(orderId, orderNumber) {
    if (!orderId) return;
    if (!window.confirm(`Bu sipariş silinecek${orderNumber ? ` (${orderNumber})` : ""}.\n\nSilinen siparişler "Silinen Siparişler" sayfasına taşınır ve oradan geri alınabilir.\n\nDevam edilsin mi?`)) return;
    setDeletingId(orderId);
    try {
      const token = localStorage.getItem("token");
      await axios.delete(`${API}/orders/${orderId}`, { headers: { Authorization: `Bearer ${token}` } });
      setDetail((d) => (d && d.id === orderId ? null : d));
      await fetchCancelled();
    } catch (e) {
      alert("Silinemedi: " + (e?.response?.data?.detail || e.message));
    } finally {
      setDeletingId(null);
    }
  }

  return (
    <div className="p-4 md:p-6">
      <div className="flex items-center gap-2 mb-1">
        <XCircle className="w-5 h-5 text-red-600" />
        <h1 className="text-xl font-semibold">İptaller</h1>
      </div>
      <p className="text-sm text-gray-500 mb-4">
        Kargolanmadan iptal edilen siparişler. Bir satıra tıklayarak müşteri ve ürünleri görebilir,
        sağdaki menüden durumu değiştirebilirsiniz. (İade talepleri ayrı "İadeler" menüsündedir.)
      </p>

      <div className="bg-white border rounded-lg p-4 mb-6 shadow-sm">
        <div className="flex flex-wrap items-center gap-2">
          <div className="relative flex-1 min-w-[220px] max-w-md">
            <Search className="w-4 h-4 absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" />
            <input
              value={searchInput}
              onChange={(e) => setSearchInput(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter") applySearch(); }}
              placeholder="Sipariş no / müşteri ara…"
              className="w-full pl-9 pr-3 py-1.5 border rounded text-sm"
            />
          </div>
          <button onClick={applySearch} className="px-4 py-1.5 bg-gray-900 text-white rounded text-sm">Ara</button>
          <select value={payFilter} onChange={(e) => { setPage(1); setPayFilter(e.target.value); }}
            className="border px-3 py-1.5 rounded text-sm bg-white" title="Ödeme tipine göre filtrele">
            <option value="">Tüm Ödeme Tipleri</option>
            <option value="credit_card">Kredi Kartı</option>
            <option value="bank_transfer">Havale/EFT</option>
            <option value="cash_on_delivery">Kapıda Ödeme</option>
          </select>
          <button onClick={fetchCancelled} className="px-3 py-1.5 border rounded text-sm flex items-center gap-1">
            <RefreshCw className={`w-4 h-4 ${loading ? "animate-spin" : ""}`} /> Yenile
          </button>
          <span className="text-sm text-gray-500 ml-auto">Toplam {total} iptal</span>
        </div>
      </div>

      <div className="bg-white rounded-lg shadow-sm overflow-x-auto">
        <table className="admin-table admin-table-compact">
          <thead>
            <tr>
              <th>Sipariş No</th>
              <th>Müşteri</th>
              <th>Ürünler</th>
              <th>Tutar</th>
              <th>Ödeme Tipi</th>
              <th>Platform</th>
              <th>Durum</th>
              <th>Tarih</th>
              <th>Sebep</th>
              <th>İşlem</th>
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr><td colSpan={10} className="text-center py-8">Yükleniyor...</td></tr>
            ) : orders.length === 0 ? (
              <tr><td colSpan={10} className="text-center py-8 text-gray-500">İptal edilen sipariş bulunamadı</td></tr>
            ) : orders.map((o) => {
              const name = custName(o);
              const invName = invoiceName(o);
              const diffInv = invName && invName !== name;
              const its = itemsOf(o);
              const dt = o.created_at ? new Date(o.created_at).toLocaleString("tr-TR") : "—";
              const phone = (o.shipping_address || {}).phone || o.phone || "";
              const pm = (o.payment_method || "").toLowerCase();
              const pmLabel = { credit_card:"Kredi Kartı", card:"Kredi Kartı", iyzico:"Kredi Kartı", cc:"Kredi Kartı",
                bank_transfer:"Havale/EFT", transfer:"Havale/EFT", havale:"Havale/EFT", eft:"Havale/EFT",
                cash_on_delivery:"Kapıda", cod:"Kapıda", kapida:"Kapıda", marketplace:"Marketplace" }[pm]
                || (["trendyol","hepsiburada","temu","n11","amazon"].includes((o.platform||"").toLowerCase()) ? "Marketplace" : (o.payment_method || "—"));
              const pMap = {
                trendyol: { label: "Trendyol", bg: "bg-[#F27A1A]" },
                hepsiburada: { label: "Hepsiburada", bg: "bg-[#FF6000]" },
                temu: { label: "Temu", bg: "bg-[#FB7701]" },
                amazon: { label: "Amazon", bg: "bg-[#232F3E]" },
                amazon_tr: { label: "Amazon", bg: "bg-[#232F3E]" },
                n11: { label: "n11", bg: "bg-[#EA0029]" },
              };
              const pb = pMap[(o.platform || "").toLowerCase()] || { label: "Web", bg: "bg-gray-800" };
              return (
                <tr
                  key={o.id || o.order_number}
                  className="cursor-pointer"
                  onClick={() => setDetail(o)}
                >
                  <td className="font-medium">{o.order_number || o.id}</td>
                  <td>
                    <p className="font-medium">{diffInv ? invName : name}</p>
                    {diffInv && <p className="text-xs text-gray-500">Teslimat: {name}</p>}
                    {phone && <p className="text-xs text-gray-500">{phone}</p>}
                  </td>
                  <td>
                    <div className="flex flex-col gap-0.5">
                      {its.slice(0, 2).map((item, i) => {
                        const qty = itemQty(item);
                        const img = item.image || item.product_image || item.imageUrl;
                        return (
                          <div key={i} className="flex items-center gap-1 text-xs">
                            <div className="relative w-6 h-6 shrink-0">
                              {img ? (
                                <img src={optimizeImg(img, 48, 48)} alt="" className="w-6 h-6 object-cover bg-gray-100 rounded" onError={(e) => { e.currentTarget.style.visibility = "hidden"; }} />
                              ) : (
                                <div className="w-6 h-6 bg-gray-100 rounded flex items-center justify-center text-[8px] text-gray-400">—</div>
                              )}
                              {qty > 1 && (
                                <span className="absolute -top-2 -left-2 min-w-[20px] h-[20px] px-1 bg-red-600 text-white text-[11px] leading-[20px] text-center rounded-full font-extrabold ring-2 ring-white shadow-md">{qty}</span>
                              )}
                            </div>
                            <span className="truncate max-w-[120px]">{item.name || item.product_name || item.title || "Ürün"}</span>
                          </div>
                        );
                      })}
                      {its.length > 2 && <span className="text-xs text-gray-500">+{its.length - 2} daha</span>}
                      {its.length === 0 && <span className="text-xs text-gray-400">—</span>}
                    </div>
                  </td>
                  <td>
                    <div className="flex flex-col gap-0.5 tabular-nums">
                      {(o.subtotal != null && ((o.discount_amount || o.discount || 0) > 0)) && (
                        <>
                          <span className="text-xs text-gray-400 line-through">{Number(o.subtotal).toFixed(2)} TL</span>
                          <span className="text-xs font-medium text-[#8b1e3f]">İskonto -{Number(o.discount_amount || o.discount || 0).toFixed(2)} TL</span>
                        </>
                      )}
                      {Number(o.shipping_cost) > 0 && (
                        <span className="text-xs text-gray-500">Kargo +{Number(o.shipping_cost).toFixed(2)} TL</span>
                      )}
                      <span className="text-sm font-semibold text-gray-900">{Number(o.total ?? o.total_amount ?? o.grand_total ?? 0).toFixed(2)} TL</span>
                    </div>
                  </td>
                  <td className="text-sm text-gray-900">{pmLabel}</td>
                  <td>
                    <span className={`inline-block px-2 py-0.5 ${pb.bg} text-white text-[10px] uppercase font-bold tracking-wider rounded`}>{pb.label}</span>
                  </td>
                  <td onClick={(e) => e.stopPropagation()}>
                    <Select value={o.status || "cancelled"} onValueChange={(v) => changeStatus(o.id, v)}>
                      <SelectTrigger className="w-32 h-8 text-xs">
                        <SelectValue>
                          <span className="text-gray-700 font-medium">{statusLabelAll[o.status || "cancelled"] || "İptal Edildi"}</span>
                        </SelectValue>
                      </SelectTrigger>
                      <SelectContent>
                        {optsFor(o.status || "cancelled").map((s) => (
                          <SelectItem key={s.key} value={s.key}>{s.label}</SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </td>
                  <td className="text-sm text-gray-500 whitespace-nowrap">{dt}</td>
                  <td className="text-xs text-gray-600 max-w-[200px]">
                    <div className="truncate" title={o.cancel_reason || ""}>{o.cancel_reason || "—"}</div>
                    {/* Havale iptali → para bu hesaba iade edilir (müşteri iptalde girdi) */}
                      <RefundBankBox info={o.refund_bank_info} />
                  </td>
                  <td onClick={(e) => e.stopPropagation()}>
                    <button
                      onClick={() => handleDelete(o.id, o.order_number)}
                      disabled={deletingId === o.id}
                      title="Siparişi sil (arşive taşınır, geri alınabilir)"
                      className="tci-btn tci-btn-red disabled:opacity-50"
                    >
                      <Trash2 size={15} />
                    </button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {totalPages > 1 && (
        <div className="flex items-center justify-center gap-2 mt-4">
          <button disabled={page <= 1} onClick={() => setPage((p) => Math.max(1, p - 1))}
            className="px-3 py-1.5 border rounded-lg text-sm disabled:opacity-40">Önceki</button>
          <span className="text-sm text-gray-600">{page} / {totalPages}</span>
          <button disabled={page >= totalPages} onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
            className="px-3 py-1.5 border rounded-lg text-sm disabled:opacity-40">Sonraki</button>
        </div>
      )}

      {/* ---- Detay Modalı ---- */}
      {detail && (
        <div
          className="fixed inset-0 z-50 bg-black/40 flex items-start md:items-center justify-center p-4 overflow-y-auto"
          onClick={() => setDetail(null)}
        >
          <div
            className="bg-white rounded-xl shadow-xl w-full max-w-2xl my-8"
            onClick={(e) => e.stopPropagation()}
          >
            {/* başlık */}
            <div className="flex items-start justify-between px-5 py-4 border-b">
              <div>
                <div className="text-lg font-semibold">{detail.order_number || detail.id}</div>
                <div className="text-xs text-gray-500 mt-0.5">
                  {platformLabel(detail.platform)} · {detail.created_at ? new Date(detail.created_at).toLocaleString("tr-TR") : "—"}
                </div>
              </div>
              <button onClick={() => setDetail(null)} className="p-1 rounded hover:bg-gray-100">
                <X className="w-5 h-5 text-gray-500" />
              </button>
            </div>

            <div className="px-5 py-4 space-y-5">
              {/* müşteri / adres */}
              <div>
                <div className="flex items-center justify-between gap-2 mb-1">
                  <div className="text-xs font-medium text-gray-500">MÜŞTERİ</div>
                  {!detail._revealed && (
                    <button type="button" onClick={() => revealContact(detail.id)} disabled={revealing}
                      className="inline-flex items-center gap-1 px-2 py-0.5 text-xs border rounded hover:bg-gray-50 disabled:opacity-50"
                      title="Tam telefon ve adres (KVKK: görüntüleme kayıt altına alınır)">
                      {revealing ? "Açılıyor…" : "Göster"}
                    </button>
                  )}
                </div>
                {(() => {
                  const a = detail.shipping_address || {};
                  const addrLine = a.address || a.address_line1 || "";
                  const region = [a.district || a.state, a.city].filter(Boolean).join(" / ");
                  const shipNm = custName(detail);
                  const invNm = invoiceName(detail);
                  const diffNm = invNm && invNm !== shipNm;
                  return (
                    <div className="text-sm text-gray-900 space-y-0.5">
                      <div className="font-medium">{diffNm ? invNm : shipNm}</div>
                      {diffNm && <div className="text-gray-600">Teslimat: {shipNm}</div>}
                      {(a.phone || detail.phone) && <div>{a.phone || detail.phone}</div>}
                      {(a.email || detail.email) && <div className="text-gray-600">{a.email || detail.email}</div>}
                      {addrLine && <div className="text-gray-600">{addrLine}</div>}
                      {region && <div className="text-gray-600">{region}</div>}
                    </div>
                  );
                })()}
              </div>

              {/* ürünler */}
              <div>
                <div className="text-xs font-medium text-gray-500 mb-2">ÜRÜNLER</div>
                <div className="space-y-2">
                  {itemsOf(detail).length === 0 ? (
                    <div className="text-sm text-gray-400">Bu siparişte kalem bilgisi yok.</div>
                  ) : itemsOf(detail).map((it, i) => {
                    const img = it.image ? optimizeImg(it.image, 96, 70) : "";
                    const q = itemQty(it);
                    const u = itemUnit(it);
                    return (
                      <div key={i} className="flex items-center gap-3 border rounded-lg p-2">
                        {img ? (
                          <img
                            src={img}
                            alt=""
                            className="w-12 h-12 rounded object-cover bg-gray-100 shrink-0"
                            onError={(e) => { e.currentTarget.style.visibility = "hidden"; }}
                          />
                        ) : (
                          <div className="w-12 h-12 rounded bg-gray-100 shrink-0" />
                        )}
                        <div className="flex-1 min-w-0">
                          <div className="text-sm text-gray-900 truncate">{it.name || it.product_name || it.title || "Ürün"}</div>
                          <div className="text-xs text-gray-500">
                            {[it.size, it.color].filter(Boolean).join(" / ")}
                            {it.barcode ? ` · ${it.barcode}` : ""}
                          </div>
                        </div>
                        <div className="text-right text-sm shrink-0">
                          <div className="text-gray-500">{q} × {money(u)}</div>
                          <div className="font-medium">{money(q * u)}</div>
                        </div>
                      </div>
                    );
                  })}
                </div>
              </div>

              {/* tutar özeti */}
              <div className="border-t pt-3 text-sm space-y-1">
                {detail.subtotal != null && (
                  <div className="flex justify-between"><span className="text-gray-500">Liste</span><span>{money(detail.subtotal)}</span></div>
                )}
                {((detail.discount_amount || detail.discount || 0) > 0) && (
                  <div className="flex justify-between"><span className="text-gray-500">İskonto</span><span>-{money(detail.discount_amount || detail.discount || 0)}</span></div>
                )}
                <div className="flex justify-between font-semibold text-base">
                  <span>Toplam</span>
                  <span>{money(detail.total ?? detail.total_amount ?? detail.grand_total)}</span>
                </div>
              </div>

              {/* durum değiştir */}
              <div className="border-t pt-3">
                <div className="text-xs font-medium text-gray-500 mb-1">DURUM</div>
                <div className="flex items-center gap-2">
                  <select
                    value={detail.status || "cancelled"}
                    disabled={savingId === detail.id}
                    onChange={(e) => changeStatus(detail.id, e.target.value)}
                    className="border rounded-lg text-sm px-3 py-2 bg-white disabled:opacity-50"
                  >
                    {optsFor(detail.status || "cancelled").map((s) => (
                      <option key={s.key} value={s.key}>{s.label}</option>
                    ))}
                  </select>
                  {savingId === detail.id && <span className="text-sm text-gray-400">Güncelleniyor…</span>}
                </div>
                <p className="text-xs text-gray-400 mt-1">
                  Durumu "İptal Edildi" dışına alırsanız sipariş İptaller listesinden çıkar.
                </p>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
