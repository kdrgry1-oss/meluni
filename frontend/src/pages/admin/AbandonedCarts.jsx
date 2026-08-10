import { useState, useEffect } from "react";
import axios from "axios";
import { ShoppingCart, Trash2, Clock, Mail } from "lucide-react";

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;
const authHeaders = () => ({ Authorization: `Bearer ${localStorage.getItem("token")}` });

export default function AbandonedCarts() {
  const [items, setItems] = useState([]);
  const [totalValue, setTotalValue] = useState(0);
  const [hours, setHours] = useState(1);
  const [loading, setLoading] = useState(false);

  const load = async () => {
    setLoading(true);
    try {
      const { data } = await axios.get(`${API}/admin/abandoned-carts`, { headers: authHeaders(), params: { hours } });
      setItems(data.items || []);
      setTotalValue(data.total_value || 0);
    } finally { setLoading(false); }
  };
  useEffect(() => { load(); /* eslint-disable-next-line */ }, [hours]);

  const del = async (sid) => {
    await axios.delete(`${API}/admin/abandoned-carts/${sid}`, { headers: authHeaders() });
    load();
  };

  return (
    <div className="space-y-5" data-testid="abandoned-page">
      <div>
        <h1 className="text-2xl font-bold flex items-center gap-2"><ShoppingCart /> Terkedilmiş Sepetler</h1>
        <p className="text-sm text-gray-500 mt-1">Ziyaretçilerin sepette bıraktığı ama sipariş vermediği ürünler.</p>
      </div>

      <div className="grid grid-cols-3 gap-3">
        <div className="bg-gradient-to-br from-red-500 to-orange-500 text-white rounded-xl p-5">
          <div className="text-xs uppercase opacity-80">Toplam Sepet</div>
          <div className="text-3xl font-bold mt-1">{items.length}</div>
        </div>
        <div className="bg-gradient-to-br from-amber-500 to-yellow-500 text-white rounded-xl p-5">
          <div className="text-xs uppercase opacity-80">Toplam Değer</div>
          <div className="text-3xl font-bold mt-1">₺{totalValue.toLocaleString("tr-TR")}</div>
        </div>
        <div className="bg-gradient-to-br from-slate-800 to-slate-700 text-white rounded-xl p-5">
          <div className="text-xs uppercase opacity-80">Ortalama Sepet</div>
          <div className="text-3xl font-bold mt-1">₺{items.length ? (totalValue / items.length).toLocaleString("tr-TR", { maximumFractionDigits: 0 }) : 0}</div>
        </div>
      </div>

      <div className="flex items-center gap-2 bg-white p-3 rounded-lg border">
        <Clock size={15} className="text-gray-500" />
        <span className="text-sm text-gray-600">Son aktiviteden bu yana en az:</span>
        <select value={hours} onChange={(e) => setHours(parseInt(e.target.value))} className="px-3 py-1 border rounded text-sm">
          <option value="1">1 saat</option>
          <option value="6">6 saat</option>
          <option value="24">1 gün</option>
          <option value="72">3 gün</option>
          <option value="168">1 hafta</option>
        </select>
      </div>

      <div className="bg-white rounded-xl border overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-gray-50 text-xs uppercase text-gray-500">
            <tr>
              <th className="text-left p-3">Kullanıcı / E-posta</th>
              <th className="text-center p-3">Ürün</th>
              <th className="text-right p-3">Tutar</th>
              <th className="text-left p-3">Son Aktivite</th>
              <th className="text-right p-3">İşlem</th>
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr><td colSpan={5} className="p-8 text-center text-gray-400">Yükleniyor…</td></tr>
            ) : items.length === 0 ? (
              <tr><td colSpan={5} className="p-8 text-center text-gray-400">Terkedilmiş sepet yok 🎉</td></tr>
            ) : items.map((s) => (
              <tr key={s.session_id} className="border-t hover:bg-gray-50">
                <td className="p-3">
                  {s.user ? (
                    <>
                      <div className="font-medium">{s.user.first_name} {s.user.last_name}</div>
                      <div className="text-xs text-gray-500">{s.user.email}</div>
                    </>
                  ) : (
                    <>
                      <div className="text-gray-600">{s.email || "Misafir"}</div>
                      {s.phone && <div className="text-xs text-gray-500">{s.phone}</div>}
                    </>
                  )}
                </td>
                <td className="p-3 text-center font-semibold">{s.items?.length || 0}</td>
                <td className="p-3 text-right font-bold text-red-600">₺{(s.total || 0).toLocaleString("tr-TR")}</td>
                <td className="p-3 text-xs text-gray-500">{new Date(s.updated_at).toLocaleString("tr-TR")}</td>
                <td className="p-3 text-right">
                  {s.email && (
                    <a href={`mailto:${s.email}?subject=Sepetinizi%20Unuttunuz%20mu?&body=Merhaba,%20Facette%20sepetinizde%20%E2%82%BA${s.total}%20tutarinda%20${s.items?.length}%20ürün%20mevcut.%20Dönüş%20yaparak%20%25X%20indirim%20kazanabilirsiniz.`} className="inline-flex items-center gap-1 text-xs text-blue-600 hover:bg-blue-50 px-2 py-1 rounded">
                      <Mail size={12} /> Mail
                    </a>
                  )}
                  <button onClick={() => del(s.session_id)} className="p-1.5 text-red-600 hover:bg-red-50 rounded ml-1"><Trash2 size={14} /></button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="text-xs text-gray-500 bg-blue-50 border border-blue-200 p-4 rounded-xl">
        <strong>İpucu:</strong> Terkedilmiş sepetleri geri kazanmak için mail atın veya bir kupon gönderin. Sistem her gün 10:00'da (UTC), 2-48 saat önce aktif olup sipariş vermemiş e-posta sahibi sepetlere otomatik bir hatırlatma maili gönderir (her sepete yalnızca 1 kez).
      </div>
    </div>
  );
}
