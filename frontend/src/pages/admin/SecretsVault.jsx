/**
 * SecretsVault.jsx — Şifreli Hassas Veri Yönetimi
 * Backend: /api/admin/vault/{secrets, secret, secret/{key}/reveal}
 */
import { useCallback, useEffect, useState } from "react";
import axios from "axios";
import { toast } from "sonner";
import { Eye, EyeOff, KeyRound, Trash2, Plus, ShieldCheck, RefreshCw } from "lucide-react";

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;

export default function SecretsVault() {
  const [items, setItems] = useState([]);
  const [canReveal, setCanReveal] = useState(false);
  const [loading, setLoading] = useState(false);
  const [revealed, setRevealed] = useState({});  // { key: value }
  const [form, setForm] = useState({ key: "", value: "", description: "", scope: "global" });

  const auth = () => ({ headers: { Authorization: `Bearer ${localStorage.getItem("token")}` } });

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const r = await axios.get(`${API}/admin/vault/secrets`, auth());
      setItems(r.data.items || []);
      setCanReveal(!!r.data.can_reveal);
    } catch (e) {
      toast.error("Vault yüklenemedi: " + (e?.response?.data?.detail || e.message));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const upsert = async () => {
    if (!form.key || !form.value) { toast.error("Anahtar ve değer zorunlu"); return; }
    try {
      await axios.post(`${API}/admin/vault/secret`, form, auth());
      toast.success(`${form.key} kaydedildi`);
      setForm({ key: "", value: "", description: "", scope: "global" });
      load();
    } catch (e) {
      toast.error(e?.response?.data?.detail || "Kaydedilemedi");
    }
  };

  const reveal = async (key) => {
    if (revealed[key]) {
      setRevealed((r) => { const n = { ...r }; delete n[key]; return n; });
      return;
    }
    try {
      const r = await axios.get(`${API}/admin/vault/secret/${encodeURIComponent(key)}/reveal`, auth());
      setRevealed((s) => ({ ...s, [key]: r.data.value }));
    } catch (e) {
      toast.error(e?.response?.data?.detail || "Görüntülenemedi");
    }
  };

  const remove = async (key) => {
    if (!window.confirm(`${key} silinsin mi?`)) return;
    try {
      await axios.delete(`${API}/admin/vault/secret/${encodeURIComponent(key)}`, auth());
      toast.success("Silindi");
      load();
    } catch (e) {
      toast.error(e?.response?.data?.detail || "Silinemedi");
    }
  };

  return (
    <div data-testid="secrets-vault-page" className="p-6 space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-light text-gray-900 flex items-center gap-2">
            <ShieldCheck className="w-6 h-6 text-emerald-700" /> Secrets Vault
          </h1>
          <p className="text-sm text-gray-500 mt-1">
            API keyleri, parolalar ve token'lar AES-256-GCM ile şifrelenerek saklanır.
            {!canReveal && " Değerleri görüntüleme yetkiniz yok — sadece süper admin görebilir."}
          </p>
        </div>
        <button data-testid="vault-refresh-btn" onClick={load} disabled={loading} className="px-3 py-1.5 border rounded-md text-sm flex items-center gap-1 hover:bg-gray-50">
          <RefreshCw className={`w-4 h-4 ${loading ? "animate-spin" : ""}`} /> Yenile
        </button>
      </div>

      {canReveal && (
        <div className="bg-white border rounded-lg p-4 space-y-3">
          <h3 className="font-medium flex items-center gap-2"><Plus className="w-4 h-4" /> Yeni / Güncelle</h3>
          <div className="grid grid-cols-1 md:grid-cols-4 gap-2">
            <input data-testid="vault-key-input" value={form.key} onChange={(e) => setForm({ ...form, key: e.target.value.toUpperCase().replace(/\s+/g, "_") })} placeholder="ANAHTAR (örn: TRENDYOL_API_KEY)" className="border rounded px-3 py-2 text-sm font-mono" />
            <input data-testid="vault-value-input" value={form.value} onChange={(e) => setForm({ ...form, value: e.target.value })} placeholder="Değer (şifrelenecek)" type="password" className="border rounded px-3 py-2 text-sm" />
            <input data-testid="vault-scope-input" value={form.scope} onChange={(e) => setForm({ ...form, scope: e.target.value })} placeholder="Kapsam (global / trendyol / iyzico ...)" className="border rounded px-3 py-2 text-sm" />
            <input data-testid="vault-desc-input" value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} placeholder="Açıklama (opsiyonel)" className="border rounded px-3 py-2 text-sm" />
          </div>
          <button data-testid="vault-save-btn" onClick={upsert} className="px-4 py-2 bg-emerald-700 text-white rounded-md text-sm hover:bg-emerald-800">
            Kaydet (Şifrelenecek)
          </button>
        </div>
      )}

      <div className="bg-white border rounded-lg overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-gray-50 text-xs text-gray-600">
            <tr>
              <th className="text-left px-4 py-2">Anahtar</th>
              <th className="text-left px-4 py-2">Değer</th>
              <th className="text-left px-4 py-2">Kapsam</th>
              <th className="text-left px-4 py-2">Açıklama</th>
              <th className="text-left px-4 py-2">Güncelleyen</th>
              <th className="text-left px-4 py-2"></th>
            </tr>
          </thead>
          <tbody data-testid="vault-table">
            {items.length === 0 ? (
              <tr><td colSpan={6} className="text-center text-gray-500 py-6">Henüz kayıt yok. Yukarıdaki formdan ekleyebilirsiniz.</td></tr>
            ) : items.map((it) => (
              <tr key={it.key} className="border-t">
                <td className="px-4 py-2 font-mono text-xs flex items-center gap-2"><KeyRound className="w-3 h-3 text-gray-400" />{it.key}</td>
                <td className="px-4 py-2 font-mono text-xs">
                  {revealed[it.key] ? <span className="text-emerald-700">{revealed[it.key]}</span> : <span className="text-gray-700">{it.masked_value || "(boş)"}</span>}
                </td>
                <td className="px-4 py-2 text-xs">{it.scope}</td>
                <td className="px-4 py-2 text-xs text-gray-600">{it.description}</td>
                <td className="px-4 py-2 text-xs text-gray-500">{it.updated_by}</td>
                <td className="px-4 py-2 text-right whitespace-nowrap">
                  {canReveal && (
                    <>
                      <button data-testid={`vault-reveal-${it.key}`} onClick={() => reveal(it.key)} className="text-blue-700 hover:underline text-xs mr-3 inline-flex items-center gap-1">
                        {revealed[it.key] ? <><EyeOff className="w-3 h-3" />Gizle</> : <><Eye className="w-3 h-3" />Göster</>}
                      </button>
                      <button data-testid={`vault-delete-${it.key}`} onClick={() => remove(it.key)} className="text-red-700 hover:underline text-xs inline-flex items-center gap-1">
                        <Trash2 className="w-3 h-3" />Sil
                      </button>
                    </>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="text-xs text-gray-500 bg-amber-50 border border-amber-200 rounded p-3">
        <strong>🔐 Güvenlik notu:</strong> Bu değerler AES-256-GCM (Fernet) ile <code>SECRETS_MASTER_KEY</code> kullanılarak şifrelenir. Master key sadece sunucu env'inde tutulur ve veritabanında saklanmaz. Görüntüleme her seferinde audit log'a yazılır.
      </div>
    </div>
  );
}
