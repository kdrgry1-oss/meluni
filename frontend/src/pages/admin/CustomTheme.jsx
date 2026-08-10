/**
 * CustomTheme.jsx — Özel Tema Kodu (CSS/JS) düzenleyici.
 * Güvenlik: JS yalnız mağazada çalışır, admin panelinde asla. Düzenleme denetlenir.
 */
import { useEffect, useState } from "react";
import axios from "axios";
import { toast } from "sonner";
import { Save, Code, AlertTriangle, ShieldCheck } from "lucide-react";

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;

export default function CustomTheme() {
  const [css, setCss] = useState("");
  const [js, setJs] = useState("");
  const [jsEnabled, setJsEnabled] = useState(false);
  const [meta, setMeta] = useState({});
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const auth = { headers: { Authorization: `Bearer ${localStorage.getItem("token")}` } };

  const load = async () => {
    setLoading(true);
    try {
      const r = await axios.get(`${API}/admin/custom-theme`, auth);
      setCss(r.data?.custom_css || "");
      setJs(r.data?.custom_js || "");
      setJsEnabled(!!r.data?.js_enabled);
      setMeta({ by: r.data?.updated_by, at: r.data?.updated_at });
    } catch { toast.error("Tema kodu yüklenemedi"); }
    finally { setLoading(false); }
  };
  useEffect(() => { load(); /* eslint-disable-next-line */ }, []);

  const save = async () => {
    setSaving(true);
    try {
      const r = await axios.put(`${API}/admin/custom-theme`,
        { custom_css: css, custom_js: js, js_enabled: jsEnabled }, auth);
      toast.success("Tema kodu kaydedildi (mağazada birkaç saniyede yayında)");
      setMeta((m) => ({ ...m, at: r.data?.updated_at }));
    } catch (e) { toast.error(e.response?.data?.detail || "Kaydedilemedi"); }
    finally { setSaving(false); }
  };

  return (
    <div data-testid="custom-theme-page">
      <div className="flex items-center justify-between mb-4 gap-3 flex-wrap">
        <div>
          <h1 className="text-2xl font-bold flex items-center gap-2"><Code size={20} /> Özel Tema (CSS / JS)</h1>
          <p className="text-sm text-gray-500 mt-1">Mağazanın görünümünü kendi CSS/JS kodunuzla özelleştirin.</p>
        </div>
        <button onClick={save} disabled={saving || loading}
          className="flex items-center gap-1 px-4 py-2 bg-black text-white rounded-lg text-sm disabled:opacity-40">
          <Save size={14} /> {saving ? "Kaydediliyor…" : "Kaydet"}
        </button>
      </div>

      {/* Güvenlik bilgisi */}
      <div className="mb-5 rounded-lg border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-800 flex gap-2">
        <ShieldCheck size={18} className="shrink-0 mt-0.5" />
        <div>
          <b>Güvenlik:</b> Buradaki kod <b>yalnızca mağaza (müşteri) tarafında</b> çalışır; yönetim paneline
          asla enjekte edilmez. Böylece özel JS, panel oturumunuza/verilerinize erişemez.
        </div>
      </div>

      {loading ? (
        <div className="text-center py-16 text-gray-400 text-sm">Yükleniyor…</div>
      ) : (
        <div className="grid lg:grid-cols-2 gap-5">
          {/* CSS */}
          <div className="bg-white border rounded-xl shadow-sm overflow-hidden">
            <div className="px-4 py-2.5 border-b bg-gray-50/60 font-semibold text-sm flex items-center justify-between">
              <span>Özel CSS</span>
              <span className="text-xs text-gray-400 font-normal">{css.length.toLocaleString("tr-TR")} karakter</span>
            </div>
            <textarea
              value={css} onChange={(e) => setCss(e.target.value)} spellCheck={false}
              placeholder={"/* Örnek */\n.product-card:hover { transform: translateY(-4px); }\nheader { letter-spacing: .04em; }"}
              className="w-full h-[420px] p-4 font-mono text-[12.5px] leading-relaxed outline-none resize-y bg-white"
            />
          </div>

          {/* JS */}
          <div className="bg-white border rounded-xl shadow-sm overflow-hidden">
            <div className="px-4 py-2.5 border-b bg-gray-50/60 font-semibold text-sm flex items-center justify-between">
              <span>Özel JavaScript</span>
              <label className="flex items-center gap-2 text-xs font-normal cursor-pointer">
                <span className={jsEnabled ? "text-emerald-700 font-semibold" : "text-gray-400"}>{jsEnabled ? "Aktif" : "Kapalı"}</span>
                <button type="button" onClick={() => setJsEnabled((v) => !v)}
                  className={`relative inline-flex h-5 w-9 items-center rounded-full transition ${jsEnabled ? "bg-emerald-600" : "bg-gray-300"}`}>
                  <span className={`inline-block h-3.5 w-3.5 transform rounded-full bg-white transition ${jsEnabled ? "translate-x-5" : "translate-x-1"}`} />
                </button>
              </label>
            </div>
            <div className="px-4 py-2 text-xs text-amber-700 bg-amber-50 border-b border-amber-100 flex gap-1.5">
              <AlertTriangle size={14} className="shrink-0 mt-0.5" />
              JS yalnız <b>Aktif</b> iken mağazada çalışır. Yalnızca güvendiğiniz kodu ekleyin.
            </div>
            <textarea
              value={js} onChange={(e) => setJs(e.target.value)} spellCheck={false}
              placeholder={"// Örnek\ndocument.addEventListener('DOMContentLoaded', () => {\n  console.log('Özel tema JS yüklendi');\n});"}
              className="w-full h-[380px] p-4 font-mono text-[12.5px] leading-relaxed outline-none resize-y bg-white"
            />
          </div>
        </div>
      )}

      {meta.at && (
        <p className="text-xs text-gray-400 mt-4">
          Son değişiklik: {meta.by || "—"} · {meta.at ? new Date(meta.at).toLocaleString("tr-TR") : ""}
        </p>
      )}
    </div>
  );
}
