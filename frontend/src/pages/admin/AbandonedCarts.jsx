import { useState, useEffect } from "react";
import axios from "axios";
import { ShoppingCart, Trash2, Clock, Mail, Send, MousePointerClick, Receipt, AlertTriangle, Eye, X } from "lucide-react";

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;
const authHeaders = () => ({ Authorization: `Bearer ${localStorage.getItem("token")}` });

const fmtTL = (n) => `₺${(Number(n) || 0).toLocaleString("tr-TR", { maximumFractionDigits: 2 })}`;
const fmtDT = (s) => (s ? new Date(s).toLocaleString("tr-TR", { dateStyle: "short", timeStyle: "short" }) : "—");
const SKIP_LABELS = {
  candidates: "Aday üye", sent: "Gönderilen", failed: "Başarısız", skip_no_consent: "İzni yok",
  skip_ordered: "Sipariş vermiş", skip_active: "Hâlâ alışverişte", skip_cooldown: "Yakın zamanda mail almış",
  skip_no_items: "Ürünler tükenmiş/pasif", skip_no_user: "Üye bulunamadı",
};

function SentEmails() {
  const [data, setData] = useState(null);
  const [days, setDays] = useState(30);
  const [loading, setLoading] = useState(false);
  const [preview, setPreview] = useState(null);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    axios.get(`${API}/admin/abandoned-carts/emails`, { headers: authHeaders(), params: { days } })
      .then(({ data: d }) => { if (alive) setData(d); })
      .catch(() => { if (alive) setData({ items: [], summary: {} }); })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [days]);

  const openPreview = async (id) => {
    setPreview({ loading: true });
    try {
      const { data: d } = await axios.get(`${API}/admin/abandoned-carts/emails/${id}/preview`, { headers: authHeaders() });
      setPreview(d);
    } catch (e) {
      setPreview({ error: e.response?.data?.detail || "Önizleme açılamadı" });
    }
  };

  const rows = data?.items || [];
  const sm = data?.summary || {};
  const lr = data?.last_run?.stats;
  const rules = data?.rules || {};
  const pct = (a, b) => (b ? `%${Math.round((a / b) * 100)}` : "—");

  return (
    <div className="space-y-4" data-testid="abandoned-emails">
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <div className="bg-gradient-to-br from-slate-800 to-slate-700 text-white rounded-xl p-5">
          <div className="text-xs uppercase opacity-80 flex items-center gap-1"><Send size={12} /> Gönderilen</div>
          <div className="text-3xl font-bold mt-1">{sm.sent || 0}</div>
          {sm.failed ? <div className="text-xs opacity-80 mt-1">{sm.failed} başarısız</div> : null}
        </div>
        <div className="bg-gradient-to-br from-sky-600 to-blue-500 text-white rounded-xl p-5">
          <div className="text-xs uppercase opacity-80 flex items-center gap-1"><MousePointerClick size={12} /> Sepete Dönen</div>
          <div className="text-3xl font-bold mt-1">{sm.clicked || 0}</div>
          <div className="text-xs opacity-80 mt-1">{pct(sm.clicked || 0, sm.sent || 0)} tıklama</div>
        </div>
        <div className="bg-gradient-to-br from-emerald-600 to-green-500 text-white rounded-xl p-5">
          <div className="text-xs uppercase opacity-80 flex items-center gap-1"><Receipt size={12} /> Sipariş Veren</div>
          <div className="text-3xl font-bold mt-1">{sm.ordered || 0}</div>
          <div className="text-xs opacity-80 mt-1">{pct(sm.ordered || 0, sm.sent || 0)} dönüşüm (7 gün)</div>
        </div>
        <div className="bg-gradient-to-br from-amber-500 to-orange-500 text-white rounded-xl p-5">
          <div className="text-xs uppercase opacity-80">Geri Kazanılan Ciro</div>
          <div className="text-3xl font-bold mt-1">{fmtTL(sm.revenue)}</div>
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-3 bg-white p-3 rounded-lg border text-sm">
        <Clock size={15} className="text-gray-500" />
        <span className="text-gray-600">Dönem:</span>
        <select value={days} onChange={(e) => setDays(parseInt(e.target.value, 10))} className="px-3 py-1 border rounded text-sm">
          <option value="7">Son 7 gün</option>
          <option value="30">Son 30 gün</option>
          <option value="90">Son 90 gün</option>
          <option value="365">Son 1 yıl</option>
        </select>
        <span className="text-gray-400">|</span>
        <span className="text-gray-600">
          Otomatik gönderim: <b className={rules.enabled === false ? "text-red-600" : "text-green-700"}>{rules.enabled === false ? "Kapalı" : "Açık"}</b>
          {rules.delay_hours ? <> · {rules.delay_hours} saat sonra</> : null}
          {rules.cooldown_days ? <> · üye başına {rules.cooldown_days} günde 1</> : null}
        </span>
      </div>

      {lr && (
        <div className="bg-white p-3 rounded-lg border text-xs text-gray-600 flex flex-wrap gap-x-4 gap-y-1">
          <span className="font-semibold text-gray-800">Son kontrol: {fmtDT(data.last_run.at)}</span>
          {Object.entries(SKIP_LABELS).map(([k, label]) => (lr[k] ? <span key={k}>{label}: <b>{lr[k]}</b></span> : null))}
          {!lr.candidates && <span>Bekleyen terkedilmiş üye sepeti yok</span>}
        </div>
      )}

      <div className="bg-white rounded-xl border overflow-x-auto">
        <table className="w-full text-sm min-w-[860px]">
          <thead className="bg-gray-50 text-xs uppercase text-gray-500">
            <tr>
              <th className="text-left p-3">Gönderim</th>
              <th className="text-left p-3">Üye</th>
              <th className="text-left p-3">Maildeki Ürünler</th>
              <th className="text-right p-3">Sepet</th>
              <th className="text-center p-3">Sepete Döndü</th>
              <th className="text-left p-3">Sipariş</th>
              <th className="text-right p-3"></th>
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr><td colSpan={7} className="p-8 text-center text-gray-400">Yükleniyor…</td></tr>
            ) : rows.length === 0 ? (
              <tr><td colSpan={7} className="p-8 text-center text-gray-400">Bu dönemde gönderilmiş terkedilmiş sepet e-postası yok.</td></tr>
            ) : rows.map((r) => (
              <tr key={r.id} className="border-t hover:bg-gray-50 align-top">
                <td className="p-3 whitespace-nowrap">
                  <div className="text-xs text-gray-700">{fmtDT(r.created_at)}</div>
                  {r.status === "sent" ? (
                    <span className="inline-block mt-1 text-[11px] px-2 py-0.5 rounded bg-green-50 text-green-700">Gönderildi</span>
                  ) : (
                    <span className="inline-flex items-center gap-1 mt-1 text-[11px] px-2 py-0.5 rounded bg-red-50 text-red-700" title={r.error || ""}>
                      <AlertTriangle size={11} /> Başarısız
                    </span>
                  )}
                </td>
                <td className="p-3">
                  <div className="font-medium">{r.name || "—"}</div>
                  <div className="text-xs text-gray-500">{r.email}</div>
                </td>
                <td className="p-3">
                  <div className="flex items-center gap-1.5">
                    {(r.products || []).slice(0, 4).map((p, i) => (
                      <img key={i} src={p.image} alt={p.name} title={`${p.name}${p.size ? ` · ${p.size}` : ""} · ${fmtTL(p.display)}`}
                        className="w-10 h-12 object-cover rounded border bg-gray-100" loading="lazy" />
                    ))}
                    {(r.products || []).length > 4 && <span className="text-xs text-gray-500">+{r.products.length - 4}</span>}
                  </div>
                  <div className="text-xs text-gray-500 mt-1">{r.items || (r.products || []).length} ürün</div>
                </td>
                <td className="p-3 text-right font-semibold">{r.cart_total ? fmtTL(r.cart_total) : "—"}</td>
                <td className="p-3 text-center text-xs">
                  {r.clicked_at ? <span className="text-sky-700">✓ {fmtDT(r.clicked_at)}</span> : <span className="text-gray-400">—</span>}
                </td>
                <td className="p-3 text-xs">
                  {r.order ? (
                    <>
                      <div className="font-semibold text-emerald-700">{r.order.order_number}</div>
                      <div className="text-gray-500">{fmtTL(r.order.total)} · {fmtDT(r.order.created_at)}</div>
                    </>
                  ) : <span className="text-gray-400">—</span>}
                </td>
                <td className="p-3 text-right">
                  {(r.products || []).length > 0 && (
                    <button type="button" onClick={() => openPreview(r.id)}
                      className="inline-flex items-center gap-1 text-xs text-blue-600 hover:bg-blue-50 px-2 py-1 rounded">
                      <Eye size={12} /> Önizle
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {preview && (
        <div className="fixed inset-0 z-50 bg-black/50 flex items-center justify-center p-4" onClick={() => setPreview(null)}>
          <div className="bg-white rounded-xl w-full max-w-[680px] max-h-[90vh] flex flex-col" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between px-4 py-3 border-b">
              <div className="text-sm"><span className="text-gray-500">Konu:</span> <b>{preview.subject || "…"}</b></div>
              <button type="button" onClick={() => setPreview(null)} className="p-1 hover:bg-gray-100 rounded"><X size={16} /></button>
            </div>
            {preview.loading ? (
              <div className="p-10 text-center text-gray-400 text-sm">Yükleniyor…</div>
            ) : preview.error ? (
              <div className="p-10 text-center text-red-600 text-sm">{preview.error}</div>
            ) : (
              <iframe title="E-posta önizleme" srcDoc={preview.html} sandbox="allow-same-origin" className="w-full flex-1 min-h-[70vh] rounded-b-xl" />
            )}
          </div>
        </div>
      )}
    </div>
  );
}

export default function AbandonedCarts() {
  const [tab, setTab] = useState("carts");
  const [items, setItems] = useState([]);
  const [totalValue, setTotalValue] = useState(0);
  const [hours, setHours] = useState(1);
  const [loading, setLoading] = useState(false);

  const load = async () => {
    setLoading(true);
    try {
      const { data } = await axios.get(`${API}/admin/abandoned-carts`, { headers: authHeaders(), params: { hours } });
      setItems(data.items || []);
      setTotalValue(data.total_value || 0);
    } finally { setLoading(false); }
  };
  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hours]);

  const del = async (sid) => {
    await axios.delete(`${API}/admin/abandoned-carts/${sid}`, { headers: authHeaders() });
    load();
  };

  return (
    <div className="space-y-5" data-testid="abandoned-page">
      <div>
        <h1 className="text-2xl font-bold flex items-center gap-2"><ShoppingCart /> Terkedilmiş Sepetler</h1>
        <p className="text-sm text-gray-500 mt-1">Ziyaretçilerin sepette bıraktığı ama sipariş vermediği ürünler.</p>
      </div>

      <div className="flex gap-1 border-b">
        {[["carts", "Sepetler"], ["emails", "Gönderilen E-postalar"]].map(([k, label]) => (
          <button key={k} type="button" onClick={() => setTab(k)} data-testid={`abandoned-tab-${k}`}
            className={`px-4 py-2 text-sm -mb-px border-b-2 ${tab === k ? "border-black font-semibold" : "border-transparent text-gray-500 hover:text-gray-800"}`}>
            {label}
          </button>
        ))}
      </div>

      {tab === "emails" ? <SentEmails /> : (<>

      <div className="grid grid-cols-3 gap-3">
        <div className="bg-gradient-to-br from-red-500 to-orange-500 text-white rounded-xl p-5">
          <div className="text-xs uppercase opacity-80">Toplam Sepet</div>
          <div className="text-3xl font-bold mt-1">{items.length}</div>
        </div>
        <div className="bg-gradient-to-br from-amber-500 to-yellow-500 text-white rounded-xl p-5">
          <div className="text-xs uppercase opacity-80">Toplam Değer</div>
          <div className="text-3xl font-bold mt-1">₺{totalValue.toLocaleString("tr-TR")}</div>
        </div>
        <div className="bg-gradient-to-br from-slate-800 to-slate-700 text-white rounded-xl p-5">
          <div className="text-xs uppercase opacity-80">Ortalama Sepet</div>
          <div className="text-3xl font-bold mt-1">₺{items.length ? (totalValue / items.length).toLocaleString("tr-TR", { maximumFractionDigits: 0 }) : 0}</div>
        </div>
      </div>

      <div className="flex items-center gap-2 bg-white p-3 rounded-lg border">
        <Clock size={15} className="text-gray-500" />
        <span className="text-sm text-gray-600">Son aktiviteden bu yana en az:</span>
        <select value={hours} onChange={(e) => setHours(parseInt(e.target.value))} className="px-3 py-1 border rounded text-sm">
          <option value="1">1 saat</option>
          <option value="6">6 saat</option>
          <option value="24">1 gün</option>
          <option value="72">3 gün</option>
          <option value="168">1 hafta</option>
        </select>
      </div>

      <div className="bg-white rounded-xl border overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-gray-50 text-xs uppercase text-gray-500">
            <tr>
              <th className="text-left p-3">Kullanıcı / E-posta</th>
              <th className="text-center p-3">Ürün</th>
              <th className="text-right p-3">Tutar</th>
              <th className="text-left p-3">Son Aktivite</th>
              <th className="text-right p-3">İşlem</th>
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr><td colSpan={5} className="p-8 text-center text-gray-400">Yükleniyor…</td></tr>
            ) : items.length === 0 ? (
              <tr><td colSpan={5} className="p-8 text-center text-gray-400">Terkedilmiş sepet yok 🎉</td></tr>
            ) : items.map((s) => (
              <tr key={s.session_id} className="border-t hover:bg-gray-50">
                <td className="p-3">
                  {s.user ? (
                    <>
                      <div className="font-medium">{s.user.first_name} {s.user.last_name}</div>
                      <div className="text-xs text-gray-500">{s.user.email}</div>
                    </>
                  ) : (
                    <>
                      <div className="text-gray-600">{s.email || "Misafir"}</div>
                      {s.phone && <div className="text-xs text-gray-500">{s.phone}</div>}
                    </>
                  )}
                </td>
                <td className="p-3 text-center font-semibold">{s.items?.length || 0}</td>
                <td className="p-3 text-right font-bold text-red-600">₺{(s.total || 0).toLocaleString("tr-TR")}</td>
                <td className="p-3 text-xs text-gray-500">{new Date(s.updated_at).toLocaleString("tr-TR")}</td>
                <td className="p-3 text-right">
                  {s.email && (
                    <a href={`mailto:${s.email}?subject=Sepetinizi%20Unuttunuz%20mu?&body=Merhaba,%20Meluni%20sepetinizde%20%E2%82%BA${s.total}%20tutarinda%20${s.items?.length}%20ürün%20mevcut.%20Dönüş%20yaparak%20%25X%20indirim%20kazanabilirsiniz.`} className="inline-flex items-center gap-1 text-xs text-blue-600 hover:bg-blue-50 px-2 py-1 rounded">
                      <Mail size={12} /> Mail
                    </a>
                  )}
                  <button onClick={() => del(s.session_id)} className="p-1.5 text-red-600 hover:bg-red-50 rounded ml-1"><Trash2 size={14} /></button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="text-xs text-gray-500 bg-blue-50 border border-blue-200 p-4 rounded-xl">
        <strong>Otomatik e-posta:</strong> Sepete ürün ekleyip satın almayan, e-posta ticari ileti izni olan <b>üyelere</b> sepet
        son güncellendikten 12 saat sonra (sipariş vermedilerse) "Seçtiklerin seni bekliyor" e-postası gider. Süre ve tekrar
        aralığı İşletme Kuralları › Pazarlama &amp; Stok bölümünden değiştirilebilir. Gönderimler "Gönderilen E-postalar" sekmesinde.
      </div>
      </>)}
    </div>
  );
}
