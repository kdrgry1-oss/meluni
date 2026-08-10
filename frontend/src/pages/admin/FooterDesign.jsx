/**
 * FooterDesign.jsx — Admin > Sayfa Tasarımı > Footer Şablonu
 * iki mod destekler: HTML (serbest HTML) veya Structured (sütun bazlı).
 */
import { useEffect, useState } from "react";
import axios from "axios";
import { toast } from "sonner";
import { Save, RotateCcw, Plus, Trash2, Code, Layers, Eye } from "lucide-react";

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;

export default function FooterDesign() {
  const [tpl, setTpl] = useState(null);
  const [busy, setBusy] = useState(false);
  const [showPreview, setShowPreview] = useState(false);
  const token = localStorage.getItem("token");
  const auth = { headers: { Authorization: `Bearer ${token}` } };

  const load = async () => {
    try {
      const r = await axios.get(`${API}/admin/footer-template`, auth);
      setTpl(r.data || {});
    } catch { toast.error("Footer şablonu alınamadı"); }
  };
  useEffect(() => { load(); }, []);

  const save = async () => {
    setBusy(true);
    try {
      await axios.put(`${API}/admin/footer-template`, tpl, auth);
      toast.success("Footer kaydedildi");
    } catch { toast.error("Kaydetme başarısız"); }
    finally { setBusy(false); }
  };

  const resetDefault = async () => {
    if (!await window.appConfirm?.("Footer'ı varsayılana sıfırlamak istiyor musunuz?")) {
      if (!window.confirm("Footer'ı varsayılana sıfırlamak istiyor musunuz?")) return;
    }
    setBusy(true);
    try {
      await axios.post(`${API}/admin/footer-template/reset-default`, {}, auth);
      toast.success("Varsayılana sıfırlandı");
      await load();
    } catch { toast.error("Sıfırlama başarısız"); }
    finally { setBusy(false); }
  };

  if (!tpl) return <div className="p-10 text-center text-gray-400">Yükleniyor...</div>;

  const isHtml = tpl.mode === "html";

  return (
    <div data-testid="footer-design-page">
      <div className="flex items-center justify-between mb-5">
        <div>
          <h1 className="text-2xl font-bold">Footer Tasarımı</h1>
          <p className="text-sm text-gray-500 mt-1">Site alt bilgi alanını HTML veya yapılandırılmış sütun olarak yönetin.</p>
        </div>
        <div className="flex gap-2">
          <button onClick={() => setShowPreview((v) => !v)}
            className="flex items-center gap-1.5 px-3 py-2 border border-gray-200 rounded-lg text-sm">
            <Eye size={14} /> {showPreview ? "Önizlemeyi Kapat" : "Önizleme"}
          </button>
          <button onClick={resetDefault} disabled={busy}
            className="flex items-center gap-1.5 px-3 py-2 border border-gray-200 rounded-lg text-sm hover:bg-gray-50">
            <RotateCcw size={14} /> Varsayılana Sıfırla
          </button>
          <button onClick={save} disabled={busy} data-testid="save-footer-btn"
            className="flex items-center gap-1.5 px-4 py-2 bg-black text-white rounded-lg text-sm hover:bg-gray-800 disabled:opacity-50">
            <Save size={14} /> Kaydet
          </button>
        </div>
      </div>

      {/* Mode picker */}
      <div className="bg-white border rounded-xl p-4 mb-4">
        <p className="text-xs font-semibold uppercase tracking-wider text-gray-600 mb-3">Mod</p>
        <div className="flex gap-3">
          <button onClick={() => setTpl({ ...tpl, mode: "structured" })}
            data-testid="mode-structured-btn"
            className={`flex items-center gap-2 px-4 py-2.5 border rounded-lg text-sm transition-colors ${!isHtml ? "border-black bg-black text-white" : "border-gray-300 hover:border-black"}`}>
            <Layers size={14} /> Yapılandırılmış (Sütunlu)
          </button>
          <button onClick={() => setTpl({ ...tpl, mode: "html" })}
            data-testid="mode-html-btn"
            className={`flex items-center gap-2 px-4 py-2.5 border rounded-lg text-sm transition-colors ${isHtml ? "border-black bg-black text-white" : "border-gray-300 hover:border-black"}`}>
            <Code size={14} /> Serbest HTML
          </button>
        </div>
      </div>

      {isHtml ? (
        <HtmlEditor tpl={tpl} setTpl={setTpl} />
      ) : (
        <StructuredEditor tpl={tpl} setTpl={setTpl} />
      )}

      {/* Live Preview */}
      {showPreview && (
        <div className="mt-6 bg-black rounded-xl overflow-hidden border-2 border-amber-200" data-testid="footer-preview">
          <div className="bg-amber-50 text-amber-900 text-xs font-semibold uppercase tracking-wider px-4 py-2">📺 Canlı Önizleme</div>
          {isHtml
            ? <div className="text-white p-6" dangerouslySetInnerHTML={{ __html: tpl.custom_html || "<p style='color:#888'>(HTML yok)</p>" }} />
            : <StructuredPreview tpl={tpl} />}
        </div>
      )}
    </div>
  );
}

function HtmlEditor({ tpl, setTpl }) {
  return (
    <div className="bg-white border rounded-xl p-4">
      <label className="block text-xs font-semibold uppercase tracking-wider text-gray-600 mb-2">
        Footer HTML İçeriği
      </label>
      <p className="text-xs text-gray-500 mb-3">
        Doğrudan HTML yazın. Tailwind class'ları kullanabilirsiniz; footer arkaplanı siyah, yazı beyaz olacak şekilde sarılır.
      </p>
      <textarea
        value={tpl.custom_html || ""}
        onChange={(e) => setTpl({ ...tpl, custom_html: e.target.value })}
        rows={20}
        className="w-full border border-gray-300 rounded-lg px-3 py-2 text-xs font-mono focus:outline-none focus:border-black"
        placeholder='<div class="container-main py-12 text-white">\n  <h3>Facette</h3>\n  <p>Contact us at info@facette.com.tr</p>\n</div>'
        data-testid="footer-html-textarea"
      />
    </div>
  );
}

function StructuredEditor({ tpl, setTpl }) {
  const columns = tpl.columns || [];
  const setColumns = (cols) => setTpl({ ...tpl, columns: cols });

  const addColumn = () => setColumns([...columns, { title: "Yeni Sütun", links: [] }]);
  const removeColumn = (i) => setColumns(columns.filter((_, k) => k !== i));
  const updateCol = (i, field, val) => {
    const c = [...columns]; c[i] = { ...c[i], [field]: val }; setColumns(c);
  };

  return (
    <div className="space-y-4">
      {/* Columns editor */}
      <div className="bg-white border rounded-xl p-4">
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-sm font-bold uppercase tracking-wider">Sütunlar</h2>
          <button onClick={addColumn} className="flex items-center gap-1 text-xs text-blue-600 font-medium" data-testid="add-column-btn">
            <Plus size={14} /> Sütun Ekle
          </button>
        </div>
        <div className="grid md:grid-cols-3 gap-4">
          {columns.map((col, i) => (
            <div key={i} className="border border-gray-200 rounded-lg p-3 bg-gray-50">
              <div className="flex items-center gap-2 mb-2">
                <input
                  value={col.title || ""}
                  onChange={(e) => updateCol(i, "title", e.target.value)}
                  className="flex-1 border border-gray-300 rounded px-2 py-1 text-sm font-bold"
                  placeholder="Başlık"
                />
                <button onClick={() => removeColumn(i)} className="p-1.5 text-red-600 hover:bg-red-50 rounded">
                  <Trash2 size={13} />
                </button>
              </div>
              {/* Linkler */}
              <div className="space-y-1.5 mb-2">
                {(col.links || []).map((l, li) => (
                  <div key={li} className="flex gap-1">
                    <input value={l.label || ""} onChange={(e) => {
                      const nc = [...columns]; nc[i].links[li] = { ...l, label: e.target.value }; setColumns(nc);
                    }} className="flex-1 border border-gray-200 rounded px-2 py-1 text-xs" placeholder="Etiket" />
                    <input value={l.to || ""} onChange={(e) => {
                      const nc = [...columns]; nc[i].links[li] = { ...l, to: e.target.value }; setColumns(nc);
                    }} className="flex-1 border border-gray-200 rounded px-2 py-1 text-xs font-mono" placeholder="/url" />
                    <button onClick={() => {
                      const nc = [...columns]; nc[i].links = nc[i].links.filter((_, k) => k !== li); setColumns(nc);
                    }} className="px-1 text-red-500"><Trash2 size={11} /></button>
                  </div>
                ))}
                <button onClick={() => {
                  const nc = [...columns]; nc[i].links = [...(nc[i].links || []), { label: "Yeni", to: "/" }]; setColumns(nc);
                }} className="text-[11px] text-blue-600">+ Link Ekle</button>
              </div>
              {/* Static lines */}
              {col.static !== undefined && (
                <div className="space-y-1 border-t pt-2">
                  <p className="text-[10px] uppercase tracking-wider text-gray-500">Statik Satırlar</p>
                  {(col.static || []).map((s, si) => (
                    <div key={si} className="flex gap-1">
                      <input value={s} onChange={(e) => {
                        const nc = [...columns]; nc[i].static[si] = e.target.value; setColumns(nc);
                      }} className="flex-1 border border-gray-200 rounded px-2 py-1 text-xs" />
                      <button onClick={() => {
                        const nc = [...columns]; nc[i].static = nc[i].static.filter((_, k) => k !== si); setColumns(nc);
                      }} className="px-1 text-red-500"><Trash2 size={11} /></button>
                    </div>
                  ))}
                  <button onClick={() => {
                    const nc = [...columns]; nc[i].static = [...(nc[i].static || []), ""]; setColumns(nc);
                  }} className="text-[11px] text-blue-600">+ Satır Ekle</button>
                </div>
              )}
              {!col.static && (
                <button onClick={() => updateCol(i, "static", [""])} className="text-[11px] text-gray-500 mt-2">+ Statik Satır Modu</button>
              )}
            </div>
          ))}
        </div>
      </div>

      {/* Newsletter — "Facette Kulübü" bandı */}
      <div className="bg-white border rounded-xl p-4" data-testid="newsletter-editor">
        <h2 className="text-sm font-bold uppercase tracking-wider mb-1">Bülten Bandı (Facette Kulübü)</h2>
        <p className="text-xs text-gray-500 mb-3">Footer'ın hemen üstünde görünen abonelik bandının metinleri.</p>
        <div className="grid gap-3">
          <div>
            <label className="block text-xs uppercase tracking-wider text-gray-500 mb-1">Başlık</label>
            <input
              value={(tpl.newsletter || {}).title || ""}
              onChange={(e) => setTpl({ ...tpl, newsletter: { ...(tpl.newsletter || {}), title: e.target.value } })}
              className="w-full border border-gray-300 rounded px-3 py-2 text-sm"
              placeholder="Facette Kulübü seni bekliyor" />
          </div>
          <div>
            <label className="block text-xs uppercase tracking-wider text-gray-500 mb-1">Açıklama</label>
            <textarea
              value={(tpl.newsletter || {}).description || ""}
              onChange={(e) => setTpl({ ...tpl, newsletter: { ...(tpl.newsletter || {}), description: e.target.value } })}
              rows={2}
              className="w-full border border-gray-300 rounded px-3 py-2 text-sm"
              placeholder="Yeni koleksiyonlar, özel kampanyalar ve sana özel fırsatlardan ilk sen haberdar ol." />
          </div>
          <div>
            <label className="block text-xs uppercase tracking-wider text-gray-500 mb-1">Giriş kutusu ipucu (placeholder)</label>
            <input
              value={(tpl.newsletter || {}).placeholder || ""}
              onChange={(e) => setTpl({ ...tpl, newsletter: { ...(tpl.newsletter || {}), placeholder: e.target.value } })}
              className="w-full border border-gray-300 rounded px-3 py-2 text-sm"
              placeholder="E-posta adresin" />
          </div>
        </div>
      </div>

      {/* Social */}
      <div className="bg-white border rounded-xl p-4">
        <h2 className="text-sm font-bold uppercase tracking-wider mb-3">Sosyal Medya</h2>
        <div className="grid md:grid-cols-3 gap-3">
          {["instagram", "facebook", "twitter"].map((k) => (
            <div key={k}>
              <label className="block text-xs uppercase tracking-wider text-gray-500 mb-1">{k}</label>
              <input value={(tpl.social || {})[k] || ""} onChange={(e) => setTpl({ ...tpl, social: { ...(tpl.social || {}), [k]: e.target.value } })}
                className="w-full border border-gray-300 rounded px-3 py-2 text-sm" placeholder="https://..." />
            </div>
          ))}
        </div>
      </div>

      {/* Copyright */}
      <div className="bg-white border rounded-xl p-4">
        <label className="block text-xs font-semibold uppercase tracking-wider text-gray-600 mb-2">
          Copyright Metni
        </label>
        <input value={tpl.copyright || ""} onChange={(e) => setTpl({ ...tpl, copyright: e.target.value })}
          className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm" />
      </div>
    </div>
  );
}

function StructuredPreview({ tpl }) {
  return (
    <div className="text-white p-8">
      <div className="grid md:grid-cols-3 gap-8 mb-8">
        {(tpl.columns || []).map((c, i) => (
          <div key={i}>
            <h4 className="text-xs uppercase tracking-[0.3em] mb-3">{c.title}</h4>
            <ul className="space-y-1.5">
              {(c.links || []).map((l, li) => <li key={li} className="text-xs text-white/55">{l.label}</li>)}
              {(c.static || []).map((s, si) => <li key={si} className="text-xs text-white/55">{s}</li>)}
            </ul>
          </div>
        ))}
      </div>
      <p className="text-[10px] uppercase tracking-wider text-white/40 border-t border-white/10 pt-4">{tpl.copyright}</p>
    </div>
  );
}
