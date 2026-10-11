import { useState, useEffect } from "react";
import axios from "axios";
import { toast } from "sonner";
import { ArrowUp, ArrowDown, Trash2, Plus, Link2, Megaphone, ExternalLink } from "lucide-react";

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;

// Vitrindeki SALE sekmesinin açılır listesi (masaüstünde üzerine gelince, mobilde alt alta).
// Kalem: kampanya (→ /kampanya/:id, kapsamındaki ürünler) veya site içi link (ör. /sale).
// Pasif / süresi dolmuş kampanya vitrinde otomatik gizlenir.
export default function SaleMenuEditor() {
  const [items, setItems] = useState([]);
  const [campaigns, setCampaigns] = useState([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [dirty, setDirty] = useState(false);

  useEffect(() => {
    axios.get(`${API}/admin/sale-menu`)
      .then(({ data }) => { setItems(data.items || []); setCampaigns(data.campaigns || []); })
      .catch(() => toast.error("SALE menüsü yüklenemedi"))
      .finally(() => setLoading(false));
  }, []);

  const update = (i, patch) => { setItems((a) => a.map((x, j) => (j === i ? { ...x, ...patch } : x))); setDirty(true); };
  const move = (i, d) => {
    setItems((a) => {
      const b = [...a];
      const j = i + d;
      if (j < 0 || j >= b.length) return a;
      [b[i], b[j]] = [b[j], b[i]];
      return b;
    });
    setDirty(true);
  };
  const remove = (i) => { setItems((a) => a.filter((_, j) => j !== i)); setDirty(true); };
  const add = (type) => {
    setItems((a) => [...a, type === "campaign"
      ? { label: "", type: "campaign", campaign_id: "", active: true }
      : { label: "", type: "link", link: "/", active: true }]);
    setDirty(true);
  };

  const save = async () => {
    setSaving(true);
    try {
      const { data } = await axios.put(`${API}/admin/sale-menu`, { items });
      setItems(data.items || []);
      setDirty(false);
      toast.success("SALE menüsü kaydedildi (vitrinde 1-5 dk içinde görünür)");
    } catch (e) {
      toast.error(e.response?.data?.detail || "Kaydedilemedi");
    } finally {
      setSaving(false);
    }
  };

  const campById = Object.fromEntries(campaigns.map((c) => [c.id, c]));
  const inputCls = "border px-2.5 py-1.5 rounded text-sm";

  return (
    <div className="mb-8 bg-white border rounded-xl p-4" data-testid="sale-menu-editor">
      <div className="flex flex-wrap items-center justify-between gap-2 mb-1">
        <h2 className="text-sm font-bold uppercase tracking-wider text-gray-700">SALE Menüsü</h2>
        <div className="flex gap-2">
          <button type="button" onClick={() => add("campaign")} className="inline-flex items-center gap-1 text-xs border px-3 py-1.5 rounded hover:bg-gray-50">
            <Plus size={13} /> Kampanya ekle
          </button>
          <button type="button" onClick={() => add("link")} className="inline-flex items-center gap-1 text-xs border px-3 py-1.5 rounded hover:bg-gray-50">
            <Plus size={13} /> Link ekle
          </button>
          <button type="button" onClick={save} disabled={saving || !dirty}
            className="text-xs bg-black text-white px-4 py-1.5 rounded disabled:opacity-40">
            {saving ? "Kaydediliyor…" : "Kaydet"}
          </button>
        </div>
      </div>
      <p className="text-xs text-gray-500 mb-3">
        Sitedeki SALE sekmesinin üzerine gelince (mobilde alt alta) bu sırayla görünür. Kampanya kalemi, o kampanyaya dahil
        ürünlerin listelendiği sayfayı açar; pasif veya süresi dolmuş kampanya otomatik gizlenir.
      </p>

      {loading ? (
        <div className="text-sm text-gray-400 py-4">Yükleniyor…</div>
      ) : items.length === 0 ? (
        <div className="text-sm text-gray-400 py-4">Menü boş — SALE sekmesi düz link olarak çalışır.</div>
      ) : (
        <div className="space-y-2">
          {items.map((it, i) => {
            const camp = it.type === "campaign" ? campById[it.campaign_id] : null;
            const url = it.type === "campaign" ? (it.campaign_id ? `/kampanya/${it.campaign_id}` : "") : it.link;
            return (
              <div key={it.id || `new-${i}`} className={`flex flex-wrap items-center gap-2 border rounded-lg px-3 py-2 ${it.active === false ? "opacity-50" : ""}`}>
                <span className="text-gray-400" title={it.type === "campaign" ? "Kampanya" : "Link"}>
                  {it.type === "campaign" ? <Megaphone size={15} /> : <Link2 size={15} />}
                </span>
                <input
                  value={it.label}
                  onChange={(e) => update(i, { label: e.target.value })}
                  placeholder="Menüde görünecek ad"
                  maxLength={40}
                  className={`${inputCls} w-48`}
                />
                {it.type === "campaign" ? (
                  <select
                    value={it.campaign_id || ""}
                    onChange={(e) => {
                      const c = campById[e.target.value];
                      update(i, { campaign_id: e.target.value, label: it.label || (c?.title || "") });
                    }}
                    className={`${inputCls} min-w-[240px]`}
                  >
                    <option value="">Kampanya seçin…</option>
                    {campaigns.map((c) => (
                      <option key={c.id} value={c.id}>{c.title}{c.live ? "" : " (yayında değil)"}</option>
                    ))}
                  </select>
                ) : (
                  <input
                    value={it.link || ""}
                    onChange={(e) => update(i, { link: e.target.value })}
                    placeholder="/sale"
                    className={`${inputCls} w-56 font-mono`}
                  />
                )}
                {camp && !camp.live && <span className="text-[11px] text-amber-700 bg-amber-50 px-2 py-0.5 rounded">Yayında değil — vitrinde görünmez</span>}
                <label className="flex items-center gap-1 text-xs text-gray-600 ml-auto">
                  <input type="checkbox" checked={it.active !== false} onChange={(e) => update(i, { active: e.target.checked })} />
                  Görünsün
                </label>
                {url && url.startsWith("/") && (
                  <a href={url} target="_blank" rel="noopener noreferrer" className="p-1 text-gray-500 hover:text-black" title="Sayfayı aç">
                    <ExternalLink size={14} />
                  </a>
                )}
                <button type="button" onClick={() => move(i, -1)} disabled={i === 0} className="p-1 text-gray-500 hover:text-black disabled:opacity-30" aria-label="Yukarı"><ArrowUp size={14} /></button>
                <button type="button" onClick={() => move(i, 1)} disabled={i === items.length - 1} className="p-1 text-gray-500 hover:text-black disabled:opacity-30" aria-label="Aşağı"><ArrowDown size={14} /></button>
                <button type="button" onClick={() => remove(i)} className="p-1 text-red-600 hover:bg-red-50 rounded" aria-label="Sil"><Trash2 size={14} /></button>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
