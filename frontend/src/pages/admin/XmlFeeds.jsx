import { useState, useEffect } from "react";
import axios from "axios";
import { toast } from "sonner";
import { Plus, Copy, ExternalLink, Trash2, Rss } from "lucide-react";

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;
const h = () => ({ Authorization: `Bearer ${localStorage.getItem("token")}` });

// Seçilebilir amaçlar — her biri feed'in nasıl üretileceğini belirler.
const TARGETS = [
  {
    value: "google",
    label: "Google Merchant",
    desc: "Ürün-seviyesi feed. Google Merchant Center → Feed'ler → Planlanmış getirme'ye bu linki ekleyin.",
  },
  {
    value: "facebook",
    label: "Facebook / Instagram Kataloğu",
    desc: "Beden-bazlı varyant feed (item_group_id + beden/renk). Meta Commerce Manager → Katalog → Veri Kaynakları → Programlı çekme.",
  },
  {
    value: "generic",
    label: "Genel (diğer pazaryeri/araç)",
    desc: "Standart ürün-seviyesi RSS (g:) feed. RSS/Google formatı kabul eden her araçta kullanılabilir.",
  },
];

const targetMeta = (v) => TARGETS.find((t) => t.value === v) || TARGETS[0];

export default function XmlFeeds() {
  const [feeds, setFeeds] = useState([]);
  const [loading, setLoading] = useState(true);
  const [name, setName] = useState("");
  const [target, setTarget] = useState("google");
  const [inStockOnly, setInStockOnly] = useState(false);
  const [creating, setCreating] = useState(false);

  const load = async () => {
    setLoading(true);
    try {
      const { data } = await axios.get(`${API}/products/feeds`, { headers: h() });
      setFeeds(Array.isArray(data) ? data : []);
    } catch (e) {
      toast.error("Feed'ler yüklenemedi");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
  }, []);

  const create = async () => {
    if (!name.trim()) {
      toast.error("Feed adı girin");
      return;
    }
    setCreating(true);
    try {
      await axios.post(
        `${API}/products/feeds`,
        { name: name.trim(), target, in_stock_only: inStockOnly, enabled: true },
        { headers: h() }
      );
      toast.success("Feed oluşturuldu");
      setName("");
      setTarget("google");
      setInStockOnly(false);
      load();
    } catch (e) {
      toast.error("Oluşturulamadı");
    } finally {
      setCreating(false);
    }
  };

  const patch = async (fid, upd) => {
    try {
      await axios.put(`${API}/products/feeds/${fid}`, upd, { headers: h() });
      load();
    } catch (e) {
      toast.error("Güncellenemedi");
    }
  };

  const remove = async (fid, fname) => {
    if (!window.confirm(`"${fname}" feed'i silinsin mi? Bu linki kullanan dış servisler artık veri çekemez.`)) return;
    try {
      await axios.delete(`${API}/products/feeds/${fid}`, { headers: h() });
      toast.success("Silindi");
      load();
    } catch (e) {
      toast.error("Silinemedi");
    }
  };

  const feedUrl = (slug) => `${API}/products/feed/${slug}.xml`;

  const copy = (url) => {
    try {
      navigator.clipboard.writeText(url);
      toast.success("Kopyalandı");
    } catch (e) {}
  };

  return (
    <div className="p-4 md:p-6 max-w-4xl mx-auto">
      <div className="flex items-center gap-2 mb-1">
        <Rss className="w-5 h-5" />
        <h1 className="text-xl font-bold">XML Feed'ler</h1>
      </div>
      <p className="text-sm text-gray-500 mb-5">
        Ürünlerinizi dış servislere (Google Merchant, Meta Katalog vb.) aktarmak için XML feed oluşturun. Her feed kendi
        kalıcı linkini alır; ilgili panelde "programlı/planlı çekme" olarak bu linki tanımlarsınız.
      </p>

      {/* Yeni feed oluştur */}
      <div className="border rounded-lg p-4 bg-gray-50 mb-6">
        <div className="text-sm font-semibold mb-3">Yeni Feed Oluştur</div>
        <div className="grid md:grid-cols-2 gap-3">
          <div>
            <label className="block text-xs text-gray-600 mb-1">Feed Adı</label>
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="örn. Facebook Kataloğu"
              className="w-full border px-2 py-1.5 rounded text-sm"
            />
          </div>
          <div>
            <label className="block text-xs text-gray-600 mb-1">Amaç</label>
            <select
              value={target}
              onChange={(e) => setTarget(e.target.value)}
              className="w-full border px-2 py-1.5 rounded text-sm bg-white"
            >
              {TARGETS.map((t) => (
                <option key={t.value} value={t.value}>{t.label}</option>
              ))}
            </select>
          </div>
        </div>
        <p className="text-xs text-gray-500 mt-2">{targetMeta(target).desc}</p>
        <div className="flex items-center justify-between mt-3">
          <label className="flex items-center gap-2 text-sm text-gray-700">
            <input type="checkbox" checked={inStockOnly} onChange={(e) => setInStockOnly(e.target.checked)} />
            Sadece stokta olanlar
          </label>
          <button
            type="button"
            onClick={create}
            disabled={creating}
            className="inline-flex items-center gap-1.5 px-4 py-2 bg-black text-white rounded text-sm disabled:opacity-50"
          >
            <Plus className="w-4 h-4" /> Oluştur
          </button>
        </div>
      </div>

      {/* Mevcut feed'ler */}
      {loading ? (
        <div className="text-sm text-gray-500">Yükleniyor…</div>
      ) : feeds.length === 0 ? (
        <div className="text-sm text-gray-500 border rounded-lg p-6 text-center">
          Henüz feed yok. Yukarıdan ilk feed'inizi oluşturun.
        </div>
      ) : (
        <div className="space-y-3">
          {feeds.map((f) => {
            const meta = targetMeta(f.target);
            const url = feedUrl(f.slug);
            return (
              <div key={f.id} className="border rounded-lg p-4">
                <div className="flex items-start justify-between gap-3 flex-wrap">
                  <div>
                    <div className="flex items-center gap-2">
                      <span className="font-semibold">{f.name}</span>
                      <span className="text-[11px] px-2 py-0.5 rounded-full bg-gray-100 border text-gray-700">
                        {meta.label}
                      </span>
                      {f.in_stock_only && (
                        <span className="text-[11px] px-2 py-0.5 rounded-full bg-amber-50 border border-amber-200 text-amber-700">
                          stokta olanlar
                        </span>
                      )}
                      {!f.enabled && (
                        <span className="text-[11px] px-2 py-0.5 rounded-full bg-red-50 border border-red-200 text-red-700">
                          pasif
                        </span>
                      )}
                    </div>
                  </div>
                  <div className="flex items-center gap-2">
                    <label className="flex items-center gap-1.5 text-xs text-gray-600">
                      <input
                        type="checkbox"
                        checked={f.enabled !== false}
                        onChange={(e) => patch(f.id, { enabled: e.target.checked })}
                      />
                      Aktif
                    </label>
                    <button
                      type="button"
                      onClick={() => remove(f.id, f.name)}
                      className="p-1.5 text-red-600 hover:bg-red-50 rounded"
                      title="Sil"
                    >
                      <Trash2 className="w-4 h-4" />
                    </button>
                  </div>
                </div>

                <div className="flex gap-2 mt-3">
                  <input readOnly value={url} className="flex-1 border px-2 py-1.5 rounded text-sm bg-gray-50" />
                  <button
                    type="button"
                    onClick={() => copy(url)}
                    className="inline-flex items-center gap-1 px-3 py-1.5 bg-black text-white rounded text-sm whitespace-nowrap"
                  >
                    <Copy className="w-3.5 h-3.5" /> Kopyala
                  </button>
                  <a
                    href={url}
                    target="_blank"
                    rel="noreferrer"
                    className="inline-flex items-center gap-1 px-3 py-1.5 border rounded text-sm whitespace-nowrap"
                  >
                    <ExternalLink className="w-3.5 h-3.5" /> Aç
                  </a>
                </div>

                <div className="mt-2">
                  <label className="text-xs text-gray-500 mr-2">Amaç:</label>
                  <select
                    value={f.target}
                    onChange={(e) => patch(f.id, { target: e.target.value })}
                    className="border px-2 py-1 rounded text-xs bg-white"
                  >
                    {TARGETS.map((t) => (
                      <option key={t.value} value={t.value}>{t.label}</option>
                    ))}
                  </select>
                  <span className="text-xs text-gray-400 ml-2">{meta.desc}</span>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
