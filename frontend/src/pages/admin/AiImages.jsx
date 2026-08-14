import { useEffect, useState } from "react";
import { Sparkles, RotateCcw, Save, AlertTriangle, CheckCircle2 } from "lucide-react";

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;
const field =
  "w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-black/10";

const headers = () => ({
  "Content-Type": "application/json",
  Authorization: `Bearer ${localStorage.getItem("token") || ""}`,
});

/**
 * Ürün görseli üretimi — tedarikçi fotoğrafları onlarca bayide aynı olduğu
 * için ürünün kendi fotoğrafını beş açıdan üretir (ön/sol/sağ/arka/detay).
 *
 * Prompt'lar veritabanında tutuluyor; sonuç beğenilmezse metin değişir, kod
 * elden geçmez. Bu ekran o metinlerin düzenlendiği ve tek ürün üzerinde
 * denendiği yer.
 */
export default function AiImages() {
  const [cfg, setCfg] = useState(null);
  const [apiKey, setApiKey] = useState("");
  const [msg, setMsg] = useState(null);
  const [busy, setBusy] = useState("");

  const load = async () => {
    try {
      const r = await fetch(`${API}/ai-images/settings`, { headers: headers() });
      const d = await r.json();
      if (!r.ok) throw new Error(d.detail || "Ayarlar okunamadı");
      setCfg(d);
    } catch (e) {
      setMsg({ type: "error", text: e.message });
      setCfg({ prompts: {}, pose_order: [], pose_labels: {} });
    }
  };

  useEffect(() => {
    load();
  }, []);

  const save = async () => {
    setBusy("save");
    setMsg(null);
    try {
      const body = { prompts: cfg.prompts, model: cfg.model, size: cfg.size };
      // Anahtar yalnız yeni girildiyse gönderilir; boşsa kayıtlı olan korunur.
      if (apiKey.trim()) body.api_key = apiKey.trim();
      const r = await fetch(`${API}/ai-images/settings`, {
        method: "PUT",
        headers: headers(),
        body: JSON.stringify(body),
      });
      const d = await r.json();
      if (!r.ok) throw new Error(d.detail || "Kaydedilemedi");
      setApiKey("");
      setMsg({ type: "ok", text: "Ayarlar kaydedildi." });
      load();
    } catch (e) {
      setMsg({ type: "error", text: e.message });
    } finally {
      setBusy("");
    }
  };

  const reset = async () => {
    setBusy("reset");
    setMsg(null);
    try {
      const r = await fetch(`${API}/ai-images/prompts/reset`, {
        method: "POST",
        headers: headers(),
      });
      const d = await r.json();
      if (!r.ok) throw new Error(d.detail || "Sıfırlanamadı");
      setMsg({ type: "ok", text: "Prompt'lar fabrika ayarına döndü." });
      load();
    } catch (e) {
      setMsg({ type: "error", text: e.message });
    } finally {
      setBusy("");
    }
  };

  if (!cfg) return <div className="p-8 text-sm text-gray-500">Yükleniyor…</div>;

  const poses = cfg.pose_order || [];

  return (
    <div className="p-6 max-w-4xl">
      <div className="flex items-center gap-2 mb-1">
        <Sparkles className="w-5 h-5" />
        <h1 className="text-2xl font-bold">Görsel Üretimi</h1>
      </div>
      <p className="text-sm text-gray-500 mb-6">
        Tedarikçi fotoğrafları onlarca bayide aynı. Bu araç ürünün kendi
        fotoğrafını beş açıdan üretir: ön, sol, sağ, arka, detay.
      </p>

      {msg && (
        <div
          className={`mb-5 rounded-lg px-4 py-3 text-sm ${
            msg.type === "error"
              ? "bg-red-50 text-red-800 border border-red-200"
              : "bg-green-50 text-green-800 border border-green-200"
          }`}
        >
          {msg.text}
        </div>
      )}

      {/* ── Bağlantı ── */}
      <div className="rounded-xl border border-gray-200 p-5 space-y-4">
        <div className="flex items-center gap-2">
          {cfg.configured ? (
            <CheckCircle2 className="w-4 h-4 text-green-600" />
          ) : (
            <AlertTriangle className="w-4 h-4 text-amber-600" />
          )}
          <span className="text-sm font-medium">
            {cfg.configured
              ? "API anahtarı kayıtlı"
              : "API anahtarı girilmemiş — üretim çalışmaz"}
          </span>
        </div>

        <div>
          <label className="block text-sm font-medium mb-1">OpenAI API anahtarı</label>
          <input
            className={field}
            type="password"
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
            placeholder={cfg.configured ? "kayıtlı — değiştirmek için yazın" : "sk-…"}
            autoComplete="off"
          />
          <p className="text-xs text-gray-500 mt-1">
            Şifreli saklanır, bir daha gösterilmez. Boş bırakırsanız mevcut anahtar korunur.
          </p>
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <div>
            <label className="block text-sm font-medium mb-1">Model</label>
            <input
              className={field}
              value={cfg.model || ""}
              onChange={(e) => setCfg({ ...cfg, model: e.target.value })}
            />
          </div>
          <div>
            <label className="block text-sm font-medium mb-1">Boyut</label>
            <input
              className={field}
              value={cfg.size || ""}
              onChange={(e) => setCfg({ ...cfg, size: e.target.value })}
            />
            <p className="text-xs text-gray-500 mt-1">
              Modada dikey kadraj: 1024x1536
            </p>
          </div>
        </div>
      </div>

      {/* ── Prompt'lar ── */}
      <div className="mt-6 rounded-xl border border-gray-200 p-5">
        <h2 className="font-semibold mb-1">Poz metinleri</h2>
        <p className="text-xs text-gray-500 mb-4">
          Sonuç beğenilmezse burayı değiştirip yeniden üretin — kod değişmez.
          Her metin ürünün kimliğini koruma kuralını da içerir; o bölümü silmek
          modelin deseni/rengi "iyileştirip" ürünü başka bir ürüne çevirmesine
          yol açar.
        </p>
        <div className="space-y-4">
          {poses.map((pose) => (
            <div key={pose}>
              <label className="block text-sm font-medium mb-1">
                {(cfg.pose_labels || {})[pose] || pose}
              </label>
              <textarea
                className={`${field} font-mono text-xs leading-relaxed`}
                rows={4}
                value={(cfg.prompts || {})[pose] || ""}
                onChange={(e) =>
                  setCfg({
                    ...cfg,
                    prompts: { ...(cfg.prompts || {}), [pose]: e.target.value },
                  })
                }
              />
            </div>
          ))}
        </div>

        <div className="flex flex-wrap gap-2 pt-4">
          <button
            onClick={save}
            disabled={busy === "save"}
            className="px-4 py-2 rounded-lg bg-black text-white text-sm inline-flex items-center gap-1.5 disabled:opacity-50"
          >
            <Save className="w-4 h-4" />
            {busy === "save" ? "Kaydediliyor…" : "Kaydet"}
          </button>
          <button
            onClick={reset}
            disabled={busy === "reset"}
            className="px-4 py-2 rounded-lg border text-sm inline-flex items-center gap-1.5 disabled:opacity-50"
          >
            <RotateCcw className="w-4 h-4" />
            {busy === "reset" ? "Sıfırlanıyor…" : "Varsayılana dön"}
          </button>
        </div>
      </div>

      {/* Üretim ürünün KENDİ görselleriyle çalışıyor; bu yüzden ürün kartının
          içinden tetikleniyor. Burada yalnız anahtar ve poz metinleri durur. */}
      <div className="mt-6 rounded-lg bg-gray-50 border border-gray-200 px-4 py-3 text-sm text-gray-700">
        Görseller <b>ürün kartının içinden</b> üretilir: Ürünler → ürünü aç →
        <b> Görseller</b> sekmesi → en altta “Görsel Üretimi”. Ürünün kendi
        görselleri referans olarak kendiliğinden gönderilir; orada yalnız yeni
        <b> ortam referansını</b> yüklersiniz.
      </div>

    </div>
  );
}
