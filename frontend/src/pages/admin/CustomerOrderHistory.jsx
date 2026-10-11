import { useEffect, useState } from "react";
import axios from "axios";
import { History, XCircle, RotateCcw, CheckCircle2 } from "lucide-react";

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;
const auth = () => ({ headers: { Authorization: `Bearer ${localStorage.getItem("token")}` } });

const STATUS_TR = {
  awaiting_payment: "Ödeme Bekleniyor", payment_notified: "Dekont Bildirildi", pending: "Beklemede",
  confirmed: "Onaylandı", processing: "Hazırlanıyor", preparing: "Hazırlanıyor", ready_to_ship: "Kargoya Hazır",
  shipped: "Kargoda", in_transit: "Yolda", out_for_delivery: "Dağıtımda", delivered: "Teslim Edildi",
  cancelled: "İptal", cancel_refunded: "İptal (İade Edildi)", payment_failed: "Ödeme Başarısız", failed: "Başarısız",
  return_requested: "İade Talebi", return_approved: "İade Onaylı", return_in_transit: "İade Kargoda",
  returned: "İade Edildi", refunded: "Para İadesi", partial_refunded: "Kısmi İade",
};

/**
 * CustomerOrderHistory — sipariş detayında AYNI MÜŞTERİNİN diğer siparişleri ve
 * hangilerini iptal/iade ettiği (eski "Sipariş Kaynağı" + "Müşteri Yolculuğu" bloklarının yerine).
 * BACKEND: GET /api/orders/{id}/customer-history
 */
export default function CustomerOrderHistory({ orderId, onOpenOrder }) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [showAll, setShowAll] = useState(false);

  useEffect(() => {
    let alive = true;
    setLoading(true); setData(null); setShowAll(false);
    axios.get(`${API}/orders/${orderId}/customer-history`, auth())
      .then((r) => { if (alive) setData(r.data); })
      .catch(() => { if (alive) setData({ orders: [], summary: { total: 0 }, error: true }); })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [orderId]);

  const orders = data?.orders || [];
  const sum = data?.summary || {};
  const shown = showAll ? orders : orders.slice(0, 8);
  const fmtDate = (v) => (v ? new Date(v).toLocaleDateString("tr-TR") : "—");
  const fmtMoney = (v) => `${Number(v || 0).toLocaleString("tr-TR", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} ₺`;

  return (
    <div className="p-4 border rounded bg-white" data-testid="customer-order-history">
      <div className="flex items-center justify-between mb-2">
        <h3 className="font-medium flex items-center gap-1.5 text-gray-900"><History size={16} /> Müşterinin Diğer Siparişleri</h3>
        {!loading && orders.length > 0 && (
          <div className="flex items-center gap-2 text-[11px]">
            <span className="px-2 py-0.5 rounded-full bg-emerald-50 text-emerald-700 border border-emerald-200">Tamamlanan {sum.completed ?? 0}</span>
            <span className="px-2 py-0.5 rounded-full bg-red-50 text-red-700 border border-red-200">İptal {sum.cancelled ?? 0}</span>
            <span className="px-2 py-0.5 rounded-full bg-amber-50 text-amber-700 border border-amber-200">İade {sum.returned ?? 0}</span>
            <span className="px-2 py-0.5 rounded-full bg-gray-50 text-gray-700 border border-gray-200" title="İptal/iade hariç net">Net {fmtMoney(sum.net_spent)}</span>
          </div>
        )}
      </div>
      {loading ? (
        <div className="text-xs text-gray-400 py-2">Yükleniyor…</div>
      ) : orders.length === 0 ? (
        <div className="text-xs text-gray-500 py-2">{data?.error ? "Geçmiş yüklenemedi." : "Bu müşterinin başka siparişi yok (ilk sipariş)."}</div>
      ) : (
        <>
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="text-left text-gray-500 border-b">
                  <th className="py-1.5 pr-2 font-medium">Sipariş</th>
                  <th className="py-1.5 pr-2 font-medium">Tarih</th>
                  <th className="py-1.5 pr-2 font-medium">Durum</th>
                  <th className="py-1.5 pr-2 font-medium text-right">Tutar</th>
                  <th className="py-1.5 pr-2 font-medium">Ürün</th>
                  <th className="py-1.5 font-medium">İptal / İade Bilgisi</th>
                </tr>
              </thead>
              <tbody>
                {shown.map((o) => (
                  <tr key={o.id} className={`border-b last:border-0 ${o.cancelled ? "bg-red-50/60" : o.returned ? "bg-amber-50/60" : ""}`}>
                    <td className="py-1.5 pr-2 whitespace-nowrap">
                      {onOpenOrder
                        ? <button type="button" onClick={() => onOpenOrder(o.id)} className="font-mono font-semibold text-blue-700 hover:underline">{o.order_number}</button>
                        : <span className="font-mono font-semibold">{o.order_number}</span>}
                      <div className="text-[10px] text-gray-400">{o.platform}{o.payment_method ? ` · ${o.payment_method}` : ""}</div>
                    </td>
                    <td className="py-1.5 pr-2 whitespace-nowrap text-gray-700">{fmtDate(o.created_at)}</td>
                    <td className="py-1.5 pr-2 whitespace-nowrap">
                      <span className={`inline-flex items-center gap-1 font-medium ${o.cancelled ? "text-red-700" : o.returned ? "text-amber-700" : "text-gray-800"}`}>
                        {o.cancelled ? <XCircle size={12} /> : o.returned ? <RotateCcw size={12} /> : <CheckCircle2 size={12} className="text-emerald-600" />}
                        {STATUS_TR[o.status] || o.status}
                      </span>
                      {o.partial_cancelled && <div className="text-[10px] text-red-600">kısmi iptal (ürün iptali)</div>}
                    </td>
                    <td className="py-1.5 pr-2 whitespace-nowrap text-right font-semibold">{fmtMoney(o.total)}</td>
                    <td className="py-1.5 pr-2 text-gray-700 min-w-[260px] align-top">
                      {/* Tam kalem listesi: ad + beden + renk + adet + birim fiyat (kesilmez, sarar). */}
                      {(o.items || []).length > 0 ? (
                        <ul className="space-y-0.5" data-testid="order-hist-items">
                          {o.items.map((it, i) => (
                            <li key={i} className="leading-snug" title={[it.barcode && `Barkod ${it.barcode}`, it.stock_code && `Stok kodu ${it.stock_code}`].filter(Boolean).join(" · ")}>
                              <span className="font-medium text-gray-900">{it.name || "—"}</span>
                              {(it.size || it.color) && (
                                <span className="ml-1 text-gray-600">
                                  {it.size ? `Beden ${it.size}` : ""}{it.size && it.color ? " · " : ""}{it.color || ""}
                                </span>
                              )}
                              <span className="ml-1 text-gray-500 whitespace-nowrap">× {it.quantity}{it.price ? ` · ${fmtMoney(it.price)}` : ""}</span>
                            </li>
                          ))}
                        </ul>
                      ) : (
                        <span>{o.items_count} ürün{o.item_names?.length ? ` · ${o.item_names.filter(Boolean).join(", ")}` : ""}</span>
                      )}
                    </td>
                    <td className="py-1.5 text-gray-600">
                      {o.cancelled ? (
                        <span>
                          {o.cancel_reason || "İptal edildi"}
                          {o.cancelled_at ? <span className="text-gray-400"> · {fmtDate(o.cancelled_at)}</span> : null}
                          {o.auto_cancelled ? <span className="text-gray-400"> · otomatik</span> : (o.cancel_source ? <span className="text-gray-400"> · {o.cancel_source}</span> : null)}
                        </span>
                      ) : o.returned ? (
                        <span>İade{o.return_status ? ` · ${o.return_status}` : ""}</span>
                      ) : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {orders.length > 8 && (
            <button type="button" onClick={() => setShowAll((v) => !v)} className="mt-2 text-xs text-blue-700 hover:underline">
              {showAll ? "Daha az göster" : `Tümünü göster (${orders.length})`}
            </button>
          )}
        </>
      )}
    </div>
  );
}
