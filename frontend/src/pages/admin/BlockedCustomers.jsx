/**
 * =============================================================================
 * BlockedCustomers.jsx — Bloklu Müşteri Yönetimi (FAZ 6)
 * =============================================================================
 *   GET    /api/customer-risk/blocked
 *   POST   /api/customer-risk/block
 *   DELETE /api/customer-risk/blocked/{id}
 *
 * Müşteri IP'si / e-postası / user_id ile blok kaydı ekleyip kaldırma.
 * Sebep + opsiyonel süre (expires_at).
 * =============================================================================
 */
import { useEffect, useState } from "react";
import axios from "axios";
import { toast } from "sonner";
import { Ban, Plus, Trash2 } from "lucide-react";

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;

export default function BlockedCustomers() {
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(true);
  const [form, setForm] = useState({ user_id: "", ip: "", email: "", reason: "Yüksek iade oranı", expires_at: "" });
  const [busy, setBusy] = useState(false);

  const token = localStorage.getItem("token");
  const auth = { headers: { Authorization: `Bearer ${token}` } };

  const load = async () => {
    setLoading(true);
    try {
      const r = await axios.get(`${API}/customer-risk/blocked?active_only=true`, auth);
      setItems(r.data?.items || []);
    } finally { setLoading(false); }
  };

  useEffect(() => { load(); /* eslint-disable-next-line */ }, []);

  const addBlock = async () => {
    if (!form.user_id && !form.ip && !form.email) {
      toast.error("En az bir alan girin (user_id / ip / email)");
      return;
    }
    setBusy(true);
    try {
      await axios.post(`${API}/customer-risk/block`, form, auth);
      toast.success("Blok eklendi");
      setForm({ user_id: "", ip: "", email: "", reason: "Yüksek iade oranı", expires_at: "" });
      await load();
    } catch (e) {
      toast.error(e?.response?.data?.detail || "Blok eklenemedi");
    } finally { setBusy(false); }
  };

  const removeBlock = async (id) => {
    if (!await window.appConfirm("Bu kaydı blok listesinden kaldır?")) return;
    try {
      await axios.delete(`${API}/customer-risk/blocked/${id}`, auth);
      toast.success("Blok kaldırıldı");
      await load();
    } catch {
      toast.error("Kaldırılamadı");
    }
  };

  return (
    <div className="max-w-6xl mx-auto p-6 space-y-6" data-testid="blocked-customers-page">
      <div>
        <h1 className="text-2xl font-semibold flex items-center gap-2"><Ban size={20} /> Bloklu Müşteriler</h1>
        <p className="text-sm text-gray-500 mt-1">Yüksek iade oranlı veya şüpheli IP/E-postaları bloklayın.</p>
      </div>

      {/* Form */}
      <section className="bg-white rounded-lg border border-gray-200 p-5">
        <h2 className="font-semibold mb-3 flex items-center gap-2"><Plus size={16} /> Yeni Blok Ekle</h2>
        <div className="grid grid-cols-1 md:grid-cols-5 gap-3">
          <input placeholder="User ID (opsiyonel)" value={form.user_id}
            onChange={(e) => setForm({ ...form, user_id: e.target.value })}
            className="border px-3 py-2 text-sm rounded" data-testid="block-user-id" />
          <input placeholder="IP (opsiyonel)" value={form.ip}
            onChange={(e) => setForm({ ...form, ip: e.target.value })}
            className="border px-3 py-2 text-sm rounded" data-testid="block-ip" />
          <input placeholder="E-posta (opsiyonel)" value={form.email}
            onChange={(e) => setForm({ ...form, email: e.target.value })}
            className="border px-3 py-2 text-sm rounded" data-testid="block-email" />
          <input placeholder="Sebep" value={form.reason}
            onChange={(e) => setForm({ ...form, reason: e.target.value })}
            className="border px-3 py-2 text-sm rounded" data-testid="block-reason" />
          <button onClick={addBlock} disabled={busy}
            className="bg-red-600 text-white px-3 py-2 rounded text-sm disabled:opacity-60" data-testid="block-add-btn">
            {busy ? "Ekleniyor..." : "Blokla"}
          </button>
        </div>
      </section>

      {/* Liste */}
      <section className="bg-white rounded-lg border border-gray-200 overflow-hidden">
        {loading ? (
          <div className="p-6 text-sm text-gray-500">Yükleniyor...</div>
        ) : items.length === 0 ? (
          <div className="p-6 text-sm text-gray-500">Aktif blok kaydı yok.</div>
        ) : (
          <table className="w-full text-sm">
            <thead className="bg-gray-50 text-xs uppercase">
              <tr>
                <th className="text-left p-3">Tarih</th>
                <th className="text-left p-3">User / IP / E-posta</th>
                <th className="text-left p-3">Sebep</th>
                <th className="text-left p-3">Ekleyen</th>
                <th className="text-right p-3">İşlem</th>
              </tr>
            </thead>
            <tbody className="divide-y">
              {items.map((b) => (
                <tr key={b.id}>
                  <td className="p-3 text-xs text-gray-500">{(b.created_at || "").slice(0, 16)}</td>
                  <td className="p-3">
                    {b.user_id && <div className="text-xs"><b>User:</b> {b.user_id}</div>}
                    {b.ip && <div className="text-xs"><b>IP:</b> {b.ip}</div>}
                    {b.email && <div className="text-xs"><b>Mail:</b> {b.email}</div>}
                  </td>
                  <td className="p-3">{b.reason}</td>
                  <td className="p-3 text-xs text-gray-500">{b.blocked_by}</td>
                  <td className="p-3 text-right">
                    <button onClick={() => removeBlock(b.id)}
                      className="text-red-600 hover:bg-red-50 px-2 py-1 rounded inline-flex items-center gap-1"
                      data-testid={`unblock-${b.id}`}>
                      <Trash2 size={14} /> Kaldır
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </div>
  );
}
