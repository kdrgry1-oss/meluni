import { useState, useEffect, useCallback } from "react";
import axios from "axios";
import { toast } from "sonner";
import { CreditCard, Landmark, Star, Trash2, Plus, Check, X, ExternalLink } from "lucide-react";

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;

const EMPTY = { id: "", bank_name: "", branch: "", iban: "", account_holder: "", is_default: false };

export default function Payments() {
  const [integrations, setIntegrations] = useState([]);
  const [banks, setBanks] = useState([]);
  const [loading, setLoading] = useState(true);
  const [form, setForm] = useState(null); // null = kapalı; {} = ekle/düzenle

  const auth = () => ({ headers: { Authorization: `Bearer ${localStorage.getItem("token")}` } });

  const load = useCallback(async () => {
    try {
      setLoading(true);
      const res = await axios.get(`${API}/settings/payment-overview`, auth());
      setIntegrations(res.data?.integrations || []);
      setBanks(res.data?.bank_accounts || []);
    } catch (e) {
      toast.error("Ödeme ayarları yüklenemedi");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const save = async () => {
    if (!form.bank_name.trim() || !form.iban.trim()) {
      toast.error("Banka adı ve IBAN zorunlu");
      return;
    }
    try {
      const res = await axios.post(`${API}/settings/bank-accounts`, form, auth());
      setBanks(res.data?.bank_accounts || []);
      setForm(null);
      toast.success("Banka hesabı kaydedildi");
    } catch (e) {
      toast.error(e.response?.data?.detail || "Kaydedilemedi");
    }
  };

  const remove = async (id) => {
    if (!(await window.appConfirm?.("Bu banka hesabını silmek istediğinize emin misiniz?") ?? window.confirm("Silinsin mi?"))) return;
    try {
      const res = await axios.delete(`${API}/settings/bank-accounts/${id}`, auth());
      setBanks(res.data?.bank_accounts || []);
      toast.success("Silindi");
    } catch (e) {
      toast.error("Silinemedi");
    }
  };

  const makeDefault = async (id) => {
    try {
      const res = await axios.post(`${API}/settings/bank-accounts/${id}/default`, {}, auth());
      setBanks(res.data?.bank_accounts || []);
    } catch (e) {
      toast.error("Varsayılan yapılamadı");
    }
  };

  return (
    <div data-testid="admin-payments" className="max-w-4xl">
      <div className="mb-6">
        <h1 className="text-2xl font-bold text-gray-900">Ödeme Tipleri</h1>
        <p className="text-sm text-gray-500 mt-1">Ödeme entegrasyonların ve havale/EFT banka hesapların.</p>
      </div>

      {/* Ödeme Entegrasyonları */}
      <div className="mb-8">
        <h2 className="text-sm font-bold text-gray-500 uppercase tracking-wide mb-3">Ödeme Entegrasyonları</h2>
        <div className="grid sm:grid-cols-2 gap-3">
          {integrations.map((it) => (
            <div key={it.key} className="bg-white border rounded-xl p-4 flex items-center justify-between shadow-sm">
              <div className="flex items-center gap-3">
                <div className="w-9 h-9 rounded-lg bg-gray-100 flex items-center justify-center">
                  {it.key === "bank_transfer" ? <Landmark size={18} /> : <CreditCard size={18} />}
                </div>
                <div>
                  <div className="font-semibold text-gray-900 text-sm">{it.name}</div>
                  <div className="text-xs mt-0.5">
                    {it.active ? (
                      <span className="text-green-700 font-medium inline-flex items-center gap-1"><Check size={12} /> Aktif</span>
                    ) : it.configured ? (
                      <span className="text-yellow-700 font-medium">Tanımlı · pasif</span>
                    ) : (
                      <span className="text-gray-400">Tanımlı değil</span>
                    )}
                  </div>
                </div>
              </div>
              {it.key !== "bank_transfer" && (
                <a href={it.settings_path} className="text-xs text-blue-600 hover:text-blue-800 inline-flex items-center gap-1">
                  Ayarlar <ExternalLink size={12} />
                </a>
              )}
            </div>
          ))}
        </div>
      </div>

      {/* Havale/EFT Banka Hesapları */}
      <div>
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-sm font-bold text-gray-500 uppercase tracking-wide">Havale / EFT Banka Hesapları</h2>
          {form === null && (
            <button onClick={() => setForm({ ...EMPTY })}
              data-testid="add-bank-btn"
              className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-gray-900 text-white rounded-lg text-sm font-semibold hover:bg-gray-800">
              <Plus size={15} /> Banka Ekle
            </button>
          )}
        </div>

        {form !== null && (
          <div className="bg-white border rounded-xl p-4 mb-4 shadow-sm">
            <div className="grid sm:grid-cols-2 gap-3">
              <div>
                <label className="block text-[11px] font-bold text-gray-500 uppercase mb-1">Banka Adı *</label>
                <input value={form.bank_name} onChange={(e) => setForm({ ...form, bank_name: e.target.value })}
                  data-testid="bank-name" className="w-full border rounded-lg px-3 py-2 text-sm" placeholder="TÜRKİYE İŞ BANKASI" />
              </div>
              <div>
                <label className="block text-[11px] font-bold text-gray-500 uppercase mb-1">Şube</label>
                <input value={form.branch} onChange={(e) => setForm({ ...form, branch: e.target.value })}
                  className="w-full border rounded-lg px-3 py-2 text-sm" placeholder="CUMHURİYET CADDESİ ESENYURT ŞUBESİ" />
              </div>
              <div className="sm:col-span-2">
                <label className="block text-[11px] font-bold text-gray-500 uppercase mb-1">IBAN *</label>
                <input value={form.iban} onChange={(e) => setForm({ ...form, iban: e.target.value })}
                  data-testid="bank-iban" className="w-full border rounded-lg px-3 py-2 text-sm font-mono" placeholder="TR.. .... .... .... .... .... .." />
              </div>
              <div>
                <label className="block text-[11px] font-bold text-gray-500 uppercase mb-1">Hesap Sahibi</label>
                <input value={form.account_holder} onChange={(e) => setForm({ ...form, account_holder: e.target.value })}
                  className="w-full border rounded-lg px-3 py-2 text-sm" placeholder="FACETTE DIŞ TİC. A.Ş" />
              </div>
              <label className="flex items-center gap-2 text-sm text-gray-700 self-end pb-2 cursor-pointer">
                <input type="checkbox" checked={form.is_default}
                  onChange={(e) => setForm({ ...form, is_default: e.target.checked })} className="rounded" />
                Varsayılan hesap
              </label>
            </div>
            <div className="flex items-center gap-2 mt-3">
              <button onClick={save} data-testid="save-bank-btn"
                className="px-4 py-2 bg-green-600 text-white rounded-lg text-sm font-semibold hover:bg-green-700">Kaydet</button>
              <button onClick={() => setForm(null)}
                className="px-4 py-2 bg-gray-100 text-gray-700 rounded-lg text-sm font-semibold hover:bg-gray-200">Vazgeç</button>
            </div>
          </div>
        )}

        <div className="bg-white border rounded-xl shadow-sm divide-y">
          {loading ? (
            <div className="p-6 text-center text-gray-400 text-sm">Yükleniyor...</div>
          ) : banks.length === 0 ? (
            <div className="p-6 text-center text-gray-400 text-sm">Banka hesabı yok. "Banka Ekle" ile ekleyin.</div>
          ) : banks.map((b) => (
            <div key={b.id} className="p-4 flex items-center justify-between gap-3">
              <div className="min-w-0">
                <div className="flex items-center gap-2">
                  <span className="font-semibold text-gray-900 text-sm">{b.bank_name}</span>
                  {b.is_default && (
                    <span className="inline-flex items-center gap-1 text-[10px] font-bold text-amber-700 bg-amber-100 px-2 py-0.5 rounded-full">
                      <Star size={10} className="fill-amber-500 text-amber-500" /> Varsayılan
                    </span>
                  )}
                </div>
                {b.branch && <div className="text-xs text-gray-500 mt-0.5">{b.branch}</div>}
                <div className="font-mono text-sm text-gray-800 mt-1">{b.iban}</div>
                {b.account_holder && <div className="text-xs text-gray-500 mt-0.5">{b.account_holder}</div>}
              </div>
              <div className="flex items-center gap-1 shrink-0">
                {!b.is_default && (
                  <button onClick={() => makeDefault(b.id)} title="Varsayılan yap"
                    className="p-2 rounded-lg text-gray-500 hover:bg-amber-50 hover:text-amber-700">
                    <Star size={15} />
                  </button>
                )}
                <button onClick={() => setForm({ ...b })} title="Düzenle"
                  className="p-2 rounded-lg text-gray-500 hover:bg-gray-100">
                  <CreditCard size={15} />
                </button>
                <button onClick={() => remove(b.id)} title="Sil"
                  className="p-2 rounded-lg text-red-500 hover:bg-red-50">
                  <Trash2 size={15} />
                </button>
              </div>
            </div>
          ))}
        </div>
        <p className="text-[11px] text-gray-400 mt-2">
          Varsayılan hesap, müşteri havale/EFT seçtiğinde sipariş onayı ve "Siparişiniz Alındı" bildiriminde gösterilecek (Faz 2).
        </p>
      </div>
    </div>
  );
}
