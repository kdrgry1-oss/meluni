/**
 * PaymentTrace.jsx — Numaradan ödeme izi
 * Bir telefon numarasının son N gündeki tüm siparişlerini ve kart ödeme
 * denemelerini (card_attempts) gösterir. "Kartımdan çekildi/hala çekiliyor
 * ama teslim olmadı" vakalarını numaradan saniyede tespit için.
 */
import { useState } from "react";
import axios from "axios";
import { toast } from "sonner";
import { Search, CreditCard } from "lucide-react";

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;

export default function PaymentTrace() {
  const [phone, setPhone] = useState("");
  const [days, setDays] = useState(10);
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const token = localStorage.getItem("token");
  const auth = { headers: { Authorization: `Bearer ${token}` } };

  const run = async () => {
    if (!phone.trim()) return toast.error("Telefon girin");
    setLoading(true);
    try {
      const res = await axios.get(
        `${API}/orders/payment-trace?phone=${encodeURIComponent(phone)}&days=${days}`, auth);
      setData(res.data);
      if (!res.data?.order_count) toast.info("Bu numara için kayıt bulunamadı");
    } catch (e) {
      toast.error("Sorgu başarısız: " + (e?.response?.data?.detail || e.message));
    } finally { setLoading(false); }
  };

  const fmt = (iso) => { try { return new Date(iso).toLocaleString("tr-TR"); } catch { return iso; } };
  const payBadge = (s) => {
    const map = { paid: "bg-emerald-100 text-emerald-700", failed: "bg-red-100 text-red-700",
      awaiting_payment: "bg-amber-100 text-amber-700", refunded: "bg-gray-200 text-gray-700" };
    return <span className={`text-[10px] px-1.5 py-0.5 rounded font-bold ${map[s] || "bg-gray-100 text-gray-600"}`}>{s || "—"}</span>;
  };

  return (
    <div className="p-6 w-full" data-testid="payment-trace-page">
      <h1 className="text-2xl font-semibold flex items-center gap-2 mb-1"><CreditCard size={20} /> Ödeme İzi (Numaradan)</h1>
      <p className="text-sm text-gray-500 mb-4">Bir telefon numarasının son günlerdeki sipariş + kart ödeme denemelerini gösterir. Kayıtlar PII'siz.</p>

      <div className="flex flex-wrap items-end gap-2 mb-5">
        <div>
          <label className="block text-xs text-gray-500 mb-1">Telefon</label>
          <input value={phone} onChange={(e) => setPhone(e.target.value)} placeholder="5388666981"
            onKeyDown={(e) => e.key === "Enter" && run()}
            className="border px-3 py-2 rounded text-sm w-56" data-testid="pt-phone" />
        </div>
        <div>
          <label className="block text-xs text-gray-500 mb-1">Gün</label>
          <select value={days} onChange={(e) => setDays(Number(e.target.value))} className="border px-2 py-2 rounded text-sm">
            <option value={10}>Son 10 gün</option>
            <option value={30}>Son 30 gün</option>
            <option value={90}>Son 90 gün</option>
            <option value={180}>Son 180 gün</option>
          </select>
        </div>
        <button onClick={run} disabled={loading}
          className="inline-flex items-center gap-1 bg-black text-white px-4 py-2 rounded text-sm disabled:opacity-60" data-testid="pt-search">
          <Search size={14} /> {loading ? "Aranıyor…" : "Sorgula"}
        </button>
      </div>

      {data && (
        <div className="space-y-4">
          <div className="flex flex-wrap gap-3 text-sm">
            <div className="border rounded-lg px-3 py-2"><span className="text-gray-500">Sipariş:</span> <b>{data.order_count}</b></div>
            <div className="border rounded-lg px-3 py-2"><span className="text-gray-500">Başarılı 3DS denemesi:</span> <b className="text-emerald-700">{data.summary?.attempts_success ?? 0}</b></div>
            <div className="border rounded-lg px-3 py-2"><span className="text-gray-500">Başarısız deneme:</span> <b className="text-red-600">{data.summary?.attempts_fail ?? 0}</b></div>
          </div>

          {data.order_count === 0 ? (
            <div className="bg-white border rounded-lg p-8 text-center text-gray-400 text-sm">Bu numara için son {data.days} günde kayıt yok.</div>
          ) : (
            <div className="bg-white border rounded-lg overflow-x-auto">
              <table className="w-full text-xs">
                <thead className="bg-gray-50 text-gray-600 uppercase">
                  <tr>
                    <th className="text-left px-3 py-2">Sipariş No</th>
                    <th className="text-left px-3 py-2">Tarih</th>
                    <th className="text-right px-3 py-2">Tutar</th>
                    <th className="text-left px-3 py-2">Yöntem</th>
                    <th className="text-center px-3 py-2">Ödeme</th>
                    <th className="text-left px-3 py-2">Durum</th>
                    <th className="text-center px-3 py-2">Deneme (✓/✗)</th>
                    <th className="text-left px-3 py-2">iyzico paymentId</th>
                    <th className="text-center px-3 py-2">Reconcile</th>
                    <th className="text-left px-3 py-2">Kargo</th>
                  </tr>
                </thead>
                <tbody>
                  {data.orders.map((o, i) => (
                    <tr key={i} className="border-t hover:bg-gray-50">
                      <td className="px-3 py-2 font-mono">{o.order_number}</td>
                      <td className="px-3 py-2 whitespace-nowrap text-gray-600">{fmt(o.created_at)}</td>
                      <td className="px-3 py-2 text-right">{o.total} ₺</td>
                      <td className="px-3 py-2">{o.payment_method}</td>
                      <td className="px-3 py-2 text-center">{payBadge(o.payment_status)}</td>
                      <td className="px-3 py-2">{o.status}</td>
                      <td className="px-3 py-2 text-center">
                        <span className="text-emerald-700 font-bold">{o.attempts_success}</span>
                        {" / "}
                        <span className="text-red-600 font-bold">{o.attempts_fail}</span>
                      </td>
                      <td className="px-3 py-2 font-mono text-gray-400">{o.iyzico_payment_id || "—"}</td>
                      <td className="px-3 py-2 text-center">{o.needs_reconciliation ? <span className="text-amber-700 font-bold">⚠️</span> : "—"}</td>
                      <td className="px-3 py-2">{o.cargo_status || "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          <p className="text-[11px] text-gray-400">
            {data.note} · <b>Yorum:</b> aynı numarada birden çok <b>paid</b> = mükerrer çekim (fazlası iade); <b>failed + iyzico paymentId</b> veya <b>⚠️ reconcile</b> = kart çekilmiş olabilir, kurtarma/iade gerek; <b>awaiting_payment</b> = çekilmedi (provizyon bankada çözülür).
          </p>
        </div>
      )}
    </div>
  );
}
