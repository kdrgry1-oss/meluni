import { useEffect, useState } from "react";
import { Cable, Download, Eye, RefreshCw, AlertTriangle, CheckCircle2 } from "lucide-react";

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;
const field =
  "w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-black/10";

const token = () => localStorage.getItem("token") || "";
const headers = () => ({
  "Content-Type": "application/json",
  Authorization: `Bearer ${token()}`,
});

/** Tedarikçi XML — besleme ayarı, önizleme, içe aktarma ve stok senkronu. */
export default function SupplierXml() {
  const [cfg, setCfg] = useState({
    url: "",
    supplier_name: "modayakamoz",
    price_markup: 2.0,
    min_stock: 5,
    enabled: false,
    url_masked: "",
    last_import: null,
    last_sync: null,
    mapping: {},
  });
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState("");
  const [preview, setPreview] = useState(null);
  const [msg, setMsg] = useState(null);

  const load = async () => {
    try {
      const r = await fetch(`${API}/supplier-xml/config`, { headers: headers() });
      // await'i setCfg'nin ok fonksiyonu İÇİNDE kullanamayız: o fonksiyon
      // async değil. Önce çözüp sonra state'e yazıyoruz.
      if (r.ok) {
        const d = await r.json();
        setCfg((c) => ({ ...c, ...d }));
      }
    } catch (e) {
      setMsg({ type: "error", text: `Ayar okunamadı: ${e.message}` });
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
  }, []);

  const save = async () => {
    setBusy("save");
    setMsg(null);
    try {
      const body = {
        supplier_name: cfg.supplier_name,
        price_markup: Number(cfg.price_markup),
        min_stock: Number(cfg.min_stock),
        enabled: !!cfg.enabled,
      };
      // Adres yalnız yeni girildiyse gönderilir; boşsa mevcut (şifreli) adres korunur.
      if (cfg.url && cfg.url.trim()) body.url = cfg.url.trim();

      const r = await fetch(`${API}/supplier-xml/config`, {
        method: "PUT",
        headers: headers(),
        body: JSON.stringify(body),
      });
      const d = await r.json();
      if (!r.ok) throw new Error(d.detail || "Kaydedilemedi");
      setMsg({ type: "ok", text: "Ayarlar kaydedildi." });
      setCfg((c) => ({ ...c, url: "" }));
      load();
    } catch (e) {
      setMsg({ type: "error", text: e.message });
    } finally {
      setBusy("");
    }
  };

  const run = async (path, label) => {
    setBusy(path);
    setMsg(null);
    setPreview(null);
    try {
      const r = await fetch(`${API}/supplier-xml/${path}`, {
        method: "POST",
        headers: headers(),
      });
      const d = await r.json();
      if (!r.ok) throw new Error(d.detail || `${label} başarısız`);
      if (path === "preview") setPreview(d);
      else {
        setMsg({
          type: d.warning ? "warn" : "ok",
          text: d.warning
            ? `${label} tamam ama dikkat: ${d.warning}`
            : `${label} tamam — ${JSON.stringify(d)}`,
        });
        load();
      }
    } catch (e) {
      setMsg({ type: "error", text: e.message });
    } finally {
      setBusy("");
    }
  };

  if (loading) return <div className="p-8 text-sm text-gray-500">Yükleniyor…</div>;

  const markupLow = Number(cfg.price_markup) <= 1;

  return (
    <div className="p-6 max-w-4xl">
      <div className="flex items-center gap-2 mb-1">
        <Cable className="w-5 h-5" />
        <h1 className="text-2xl font-bold">Tedarikçi XML</h1>
      </div>
      <p className="text-sm text-gray-500 mb-6">
        Beslemeden ürün ve stok aktarımı. Stok günde 4 kez, yeni ürünler günde 1 kez
        otomatik güncellenir.
      </p>

      {msg && (
        <div
          className={`mb-5 rounded-lg px-4 py-3 text-sm ${
            msg.type === "error"
              ? "bg-red-50 text-red-800 border border-red-200"
              : msg.type === "warn"
              ? "bg-amber-50 text-amber-900 border border-amber-200"
              : "bg-green-50 text-green-800 border border-green-200"
          }`}
        >
          {msg.text}
        </div>
      )}

      <div className="space-y-5 rounded-xl border border-gray-200 p-5">
        <div>
          <label className="block text-sm font-medium mb-1">Besleme adresi</label>
          <input
            className={field}
            type="password"
            value={cfg.url}
            onChange={(e) => setCfg({ ...cfg, url: e.target.value })}
            placeholder={cfg.url_masked || "https://…/xml/… (kullanıcı adı ve şifre içerir)"}
            autoComplete="off"
          />
          <p className="text-xs text-gray-500 mt-1">
            Şifreli saklanır, bir daha gösterilmez. Boş bırakırsanız mevcut adres korunur.
            {cfg.url_masked ? ` Kayıtlı: ${cfg.url_masked}` : ""}
          </p>
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <div>
            <label className="block text-sm font-medium mb-1">Tedarikçi adı</label>
            <input
              className={field}
              value={cfg.supplier_name}
              onChange={(e) => setCfg({ ...cfg, supplier_name: e.target.value })}
            />
          </div>
          <div>
            <label className="block text-sm font-medium mb-1">Asgari stok eşiği</label>
            <input
              className={field}
              type="number"
              min="0"
              value={cfg.min_stock}
              onChange={(e) => setCfg({ ...cfg, min_stock: e.target.value })}
            />
            <p className="text-xs text-gray-500 mt-1">
              Tedarikçi stoğu bu sayıya <b>eşit veya altındaysa</b> o beden hiç
              açılmaz. Sipariş gelip malı bulamamayı önler.
            </p>
          </div>
          <div>
            <label className="block text-sm font-medium mb-1">Kâr katsayısı</label>
            <input
              className={field}
              type="number"
              step="0.05"
              min="1"
              value={cfg.price_markup}
              onChange={(e) => setCfg({ ...cfg, price_markup: e.target.value })}
            />
          </div>
        </div>

        {/* Beslemedeki fiyat BAYİ fiyatı ve KDV hariç. Katsayısız yayınlamak
            ürünleri maliyetine, KDV düşünce zararına satmak demektir. */}
        <div
          className={`rounded-lg px-4 py-3 text-sm flex gap-2 ${
            markupLow
              ? "bg-red-50 text-red-800 border border-red-200"
              : "bg-gray-50 text-gray-700 border border-gray-200"
          }`}
        >
          {markupLow ? (
            <AlertTriangle className="w-4 h-4 shrink-0 mt-0.5" />
          ) : (
            <CheckCircle2 className="w-4 h-4 shrink-0 mt-0.5" />
          )}
          <div>
            Beslemedeki fiyat <b>bayi (maliyet)</b> fiyatıdır ve <b>KDV hariçtir</b>. Satış
            fiyatı: <code>maliyet × katsayı + KDV</code>.
            {markupLow ? (
              <> Katsayı 1.0 ile ürünler <b>maliyetine</b> satışa açılır — kâr kalmaz.</>
            ) : (
              <>
                {" "}
                Örnek: 434,93 TL maliyet, %10 KDV, katsayı {Number(cfg.price_markup) || 2} →{" "}
                <b>
                  {(434.93 * (Number(cfg.price_markup) || 2) * 1.1).toFixed(2)} TL
                </b>
              </>
            )}
          </div>
        </div>

        <label className="flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={!!cfg.enabled}
            onChange={(e) => setCfg({ ...cfg, enabled: e.target.checked })}
          />
          Otomatik güncellemeler etkin (kapalıyken zamanlanmış işler beslemeyi indirmez)
        </label>

        <div className="flex flex-wrap gap-2 pt-1">
          <button
            onClick={save}
            disabled={busy === "save"}
            className="px-4 py-2 rounded-lg bg-black text-white text-sm disabled:opacity-50"
          >
            {busy === "save" ? "Kaydediliyor…" : "Kaydet"}
          </button>
          <button
            onClick={() => run("preview", "Önizleme")}
            disabled={busy === "preview"}
            className="px-4 py-2 rounded-lg border text-sm inline-flex items-center gap-1.5 disabled:opacity-50"
          >
            <Eye className="w-4 h-4" />
            {busy === "preview" ? "Okunuyor…" : "Önizle"}
          </button>
          <button
            onClick={() => run("import", "İçe aktarma")}
            disabled={busy === "import"}
            className="px-4 py-2 rounded-lg bg-blue-600 text-white text-sm inline-flex items-center gap-1.5 disabled:opacity-50"
          >
            <Download className="w-4 h-4" />
            {busy === "import" ? "Aktarılıyor… (birkaç dakika)" : "Ürünleri İçe Aktar"}
          </button>
          <button
            onClick={() => run("sync-stock", "Stok senkronu")}
            disabled={busy === "sync-stock"}
            className="px-4 py-2 rounded-lg border text-sm inline-flex items-center gap-1.5 disabled:opacity-50"
          >
            <RefreshCw className="w-4 h-4" />
            {busy === "sync-stock" ? "Eşitleniyor…" : "Stokları Eşitle"}
          </button>
        </div>

        {(cfg.last_import || cfg.last_sync) && (
          <div className="text-xs text-gray-500 pt-1">
            {cfg.last_import && <div>Son içe aktarma: {cfg.last_import}</div>}
            {cfg.last_sync && <div>Son stok senkronu: {cfg.last_sync}</div>}
          </div>
        )}
      </div>

      {preview && (
        <div className="mt-6 rounded-xl border border-gray-200 p-5">
          <h2 className="font-semibold mb-3">Önizleme</h2>
          <div className="text-sm mb-3">
            Beslemede <b>{preview.total ?? "?"}</b> ürün bulundu.
          </div>

          {preview.detected_mapping && (
            <>
              <div className="text-sm font-medium mb-1">Tanınan alanlar</div>
              <div className="grid grid-cols-2 sm:grid-cols-3 gap-x-4 gap-y-1 text-xs mb-4">
                {Object.entries(preview.detected_mapping).map(([k, v]) => (
                  <div key={k}>
                    <span className="text-gray-500">{k}</span> → <b>{v}</b>
                  </div>
                ))}
              </div>
            </>
          )}

          {/* Ham örnek: eşleme yanlışsa burada hemen görülür. */}
          <details>
            <summary className="text-sm cursor-pointer text-gray-600">
              Örnek ürünler (ham)
            </summary>
            <pre className="mt-2 text-xs bg-gray-50 border rounded-lg p-3 overflow-x-auto max-h-96">
              {JSON.stringify(preview.sample ?? preview.items ?? preview, null, 2)}
            </pre>
          </details>
        </div>
      )}
    </div>
  );
}
