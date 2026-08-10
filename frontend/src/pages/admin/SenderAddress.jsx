import { useState, useEffect } from "react";
import axios from "axios";
import { toast } from "sonner";
import { Save, Building2 } from "lucide-react";

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;

const FIELDS = [
  { key: "sender_name", label: "Gönderici / Mağaza Adı", ph: "FACETTE DIŞ TİC. A.Ş", full: true },
  { key: "sender_phone", label: "Telefon", ph: "5XX XXX XX XX" },
  { key: "sender_tax_no", label: "Vergi No (opsiyonel)", ph: "7810816779" },
  { key: "sender_city", label: "İl", ph: "İstanbul" },
  { key: "sender_district", label: "İlçe", ph: "Esenyurt" },
  { key: "sender_address", label: "Açık Adres", ph: "Cadde, No, Mahalle…", full: true, area: true },
];

export default function SenderAddress() {
  const [form, setForm] = useState({});
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const auth = () => ({ headers: { Authorization: `Bearer ${localStorage.getItem("token")}` } });

  useEffect(() => {
    (async () => {
      try {
        const res = await axios.get(`${API}/settings/store-info`, auth());
        setForm(res.data || {});
      } catch (e) {
        toast.error("Adres bilgisi yüklenemedi");
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  const save = async () => {
    if (!(form.sender_address || "").trim() || !(form.sender_city || "").trim()) {
      toast.error("İade kargo kodu üretimi için en az İl ve Açık Adres gerekli");
      return;
    }
    try {
      setSaving(true);
      await axios.post(`${API}/settings/store-info`, form, auth());
      toast.success("Gönderici / depo adresi kaydedildi");
    } catch (e) {
      toast.error(e.response?.data?.detail || "Kaydedilemedi");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div data-testid="admin-sender-address" className="max-w-2xl">
      <div className="flex items-start justify-between mb-6 gap-4">
        <div className="flex items-center gap-2">
          <Building2 size={22} className="text-gray-600" />
          <div>
            <h1 className="text-2xl font-bold text-gray-900">Gönderici / Depo Adresi</h1>
            <p className="text-sm text-gray-500 mt-1">Kargo ve <b>iade</b> etiketlerinde kullanılır. İade kargo barkodunun oluşması için zorunlu.</p>
          </div>
        </div>
        <button onClick={save} disabled={saving}
          className="inline-flex items-center gap-2 px-4 py-2 bg-gray-900 text-white rounded-lg text-sm font-semibold hover:bg-gray-800 disabled:opacity-50 shrink-0">
          <Save size={16} /> {saving ? "Kaydediliyor…" : "Kaydet"}
        </button>
      </div>

      {loading ? (
        <div className="p-10 text-center text-gray-400">Yükleniyor…</div>
      ) : (
        <div className="bg-white border rounded-xl p-5 shadow-sm grid sm:grid-cols-2 gap-4">
          {FIELDS.map((f) => (
            <div key={f.key} className={f.full ? "sm:col-span-2" : ""}>
              <label className="block text-[11px] font-bold text-gray-500 uppercase mb-1">{f.label}</label>
              {f.area ? (
                <textarea
                  value={form[f.key] || ""}
                  onChange={(e) => setForm({ ...form, [f.key]: e.target.value })}
                  rows={2} placeholder={f.ph}
                  className="w-full border rounded-lg px-3 py-2 text-sm resize-none"
                />
              ) : (
                <input
                  value={form[f.key] || ""}
                  onChange={(e) => setForm({ ...form, [f.key]: e.target.value })}
                  placeholder={f.ph}
                  className="w-full border rounded-lg px-3 py-2 text-sm"
                />
              )}
            </div>
          ))}
        </div>
      )}
      <p className="text-[11px] text-gray-400 mt-3">
        İade akışında paket bu adrese (depo) yönlendirilir. Eksikse iade talebi yine oluşur ama kargo barkodu “etiket bekliyor” olarak işaretlenir.
      </p>
    </div>
  );
}
