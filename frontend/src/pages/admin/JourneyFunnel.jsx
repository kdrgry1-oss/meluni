import { useEffect, useState } from "react";
import axios from "axios";
import {
  Compass, Megaphone, Instagram, Search, MousePointerClick, ShoppingBag,
  Star, Globe, ChevronDown, ChevronRight, Store, Radio,
} from "lucide-react";

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;
const auth = () => ({ headers: { Authorization: `Bearer ${localStorage.getItem("token")}` } });

// Kaynak türü → etiket + renk + ikon
const KIND = {
  reklam:     { label: "Reklamdan Geldi",      cls: "bg-violet-100 text-violet-700 ring-violet-200", Icon: Megaphone },
  influencer: { label: "Influencer",           cls: "bg-pink-100 text-pink-700 ring-pink-200",       Icon: Star },
  organik:    { label: "Organik",              cls: "bg-emerald-100 text-emerald-700 ring-emerald-200", Icon: Compass },
  pazaryeri:  { label: "Pazaryeri",            cls: "bg-amber-100 text-amber-700 ring-amber-200",    Icon: Store },
  direct:     { label: "Doğrudan / Bilinmiyor", cls: "bg-gray-100 text-gray-600 ring-gray-200",      Icon: Globe },
};

const chIcon = (ch) => {
  const c = (ch || "").toLowerCase();
  if (c.includes("instagram")) return Instagram;
  if (c.includes("google")) return Search;
  if (c.includes("meta") || c.includes("facebook")) return Radio;
  return Globe;
};

const fmtDate = (s) => {
  if (!s) return "";
  try { return new Date(s).toLocaleString("tr-TR", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" }); }
  catch { return s; }
};
const shortUrl = (u) => (u || "").replace(/^https?:\/\//, "").replace(/^www\./, "").slice(0, 48) || "—";

// Literal accent sınıfları (Tailwind purge güvenli — dinamik `bg-${x}` KULLANMA)
const ACCENT = {
  emerald: "bg-emerald-50 text-emerald-600",
  violet: "bg-violet-50 text-violet-600",
  pink: "bg-pink-50 text-pink-600",
  sky: "bg-sky-50 text-sky-600",
  amber: "bg-amber-50 text-amber-600",
};

// Huni adımı (üstten alta daralan)
function Step({ Icon, title, value, sub, accent = "violet", width = 100, last }) {
  return (
    <div className="flex flex-col items-center w-full">
      <div
        className="relative rounded-xl border px-4 py-3 flex items-center gap-3 bg-white"
        style={{ width: `${width}%`, borderColor: "#eee" }}
      >
        <span className={`w-9 h-9 rounded-lg flex items-center justify-center shrink-0 ${ACCENT[accent] || ACCENT.violet}`}>
          <Icon size={18} />
        </span>
        <div className="min-w-0">
          <p className="text-[10px] uppercase tracking-wider text-gray-400">{title}</p>
          <p className="text-sm font-semibold text-gray-900 truncate">{value || "—"}</p>
          {sub && <p className="text-xs text-gray-500 truncate">{sub}</p>}
        </div>
      </div>
      {!last && <div className="w-px h-4 bg-gray-200" />}
    </div>
  );
}

export default function JourneyFunnel({ orderId }) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState(false);
  const [showTouches, setShowTouches] = useState(false);

  useEffect(() => {
    if (!orderId) return;
    let alive = true;
    setLoading(true); setErr(false);
    axios.get(`${API}/orders/${orderId}/journey`, auth())
      .then((r) => { if (alive) setData(r.data); })
      .catch(() => { if (alive) setErr(true); })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [orderId]);

  if (loading) return <div className="p-4 text-sm text-gray-400">Müşteri yolculuğu yükleniyor…</div>;
  if (err || !data) return <div className="p-4 text-sm text-gray-400">Yolculuk verisi alınamadı.</div>;

  const a = data.attribution || {};
  const k = KIND[data.kind] || KIND.direct;
  const KIcon = k.Icon;
  const touches = data.touches || [];

  return (
    <div className="border rounded-xl overflow-hidden bg-gray-50">
      <div className="flex items-center justify-between px-4 py-3 bg-white border-b">
        <h3 className="font-medium flex items-center gap-2"><MousePointerClick size={16} /> Müşteri Yolculuğu</h3>
        <span className={`inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-xs font-semibold ring-1 ring-inset ${k.cls}`}>
          <KIcon size={13} /> {k.label}
        </span>
      </div>

      <div className="p-4 flex flex-col items-center gap-0">
        {/* 1) Nereden geldi */}
        <Step
          Icon={chIcon(a.channel || a.source)}
          title="Nereden geldi"
          value={(a.channel || a.source || "Doğrudan").toString().replace(/^\w/, (c) => c.toUpperCase())}
          sub={a.referrer ? `Referrer: ${shortUrl(a.referrer)}` : (a.medium ? `Medium: ${a.medium}` : null)}
          accent="emerald" width={100}
        />

        {/* 2) Reklam / Kampanya (varsa) */}
        {(data.is_ad || a.campaign || data.ad_platform) && (
          <Step
            Icon={Megaphone}
            title="Reklam / Kampanya"
            value={a.campaign ? `Kampanya: ${a.campaign}` : (data.ad_platform || "Reklam")}
            sub={[data.ad_platform && !a.campaign ? null : data.ad_platform, a.content && `Kreatif: ${a.content}`, a.term && `Terim: ${a.term}`].filter(Boolean).join(" · ") || null}
            accent="violet" width={90}
          />
        )}

        {/* 3) Influencer (varsa) */}
        {data.influencer && (
          <Step
            Icon={Star}
            title="Influencer"
            value={data.influencer.name || data.influencer.handle || "Influencer"}
            sub={[data.influencer.handle && `@${data.influencer.handle.replace(/^@/, "")}`, data.influencer.platform, data.influencer.followers ? `${Number(data.influencer.followers).toLocaleString("tr-TR")} takipçi` : null, data.influencer.via && `(${data.influencer.via})`].filter(Boolean).join(" · ")}
            accent="pink" width={85}
          />
        )}

        {/* 4) Giriş sayfası / gördüğü içerik */}
        {a.landing_page && (
          <Step
            Icon={Globe}
            title="İlk gördüğü sayfa"
            value={shortUrl(a.landing_page)}
            sub={a.touches_count ? `${a.touches_count} ziyaret · ${a.device || "cihaz ?"}` : (a.device || null)}
            accent="sky" width={78}
          />
        )}

        {/* 5) Kupon (varsa) */}
        {data.coupon_code && (
          <Step Icon={ChevronRight} title="Kupon kullandı" value={data.coupon_code} accent="amber" width={70} />
        )}

        {/* 6) Satın aldı */}
        <div className="w-full flex flex-col items-center" style={{ width: "62%" }}>
          <div className="w-full rounded-xl border border-black/80 bg-black text-white px-4 py-3">
            <p className="text-[10px] uppercase tracking-wider text-white/60 flex items-center gap-1.5"><ShoppingBag size={12} /> Satın aldı</p>
            <div className="mt-2 space-y-2">
              {(data.products || []).map((p, i) => (
                <div key={i} className="flex items-center gap-2.5">
                  {p.image
                    ? <img src={p.image} alt="" className="w-8 h-10 object-cover rounded bg-white/10" />
                    : <span className="w-8 h-10 rounded bg-white/10 flex items-center justify-center"><ShoppingBag size={13} /></span>}
                  <div className="min-w-0">
                    <p className="text-sm truncate">{p.name}</p>
                    <p className="text-[11px] text-white/60">{p.qty} adet{p.barcode ? ` · ${p.barcode}` : ""}</p>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>

      {/* Ziyaret zaman çizelgesi (yolculuk detayı) */}
      {touches.length > 0 && (
        <div className="border-t bg-white">
          <button onClick={() => setShowTouches((v) => !v)}
            className="w-full flex items-center justify-between px-4 py-2.5 text-sm text-gray-600 hover:bg-gray-50">
            <span className="flex items-center gap-2">
              {showTouches ? <ChevronDown size={15} /> : <ChevronRight size={15} />}
              Ziyaret geçmişi ({touches.length})
            </span>
            <span className="text-xs text-gray-400">tıkla → aç/kapat</span>
          </button>
          {showTouches && (
            <ol className="px-4 pb-4 space-y-2">
              {touches.map((t, i) => {
                const TI = chIcon(t.channel || t.source);
                return (
                  <li key={i} className="flex items-start gap-3 text-sm">
                    <span className="mt-0.5 w-6 h-6 rounded-full bg-gray-100 text-gray-500 flex items-center justify-center shrink-0"><TI size={12} /></span>
                    <div className="min-w-0">
                      <p className="text-gray-800">
                        <b>{(t.channel || t.source || "Doğrudan").toString().replace(/^\w/, (c) => c.toUpperCase())}</b>
                        {t.campaign ? ` · ${t.campaign}` : ""}
                        <span className="text-gray-400 text-xs ml-1">{fmtDate(t.ts)}</span>
                      </p>
                      {(t.landing_page || t.referrer) && (
                        <p className="text-xs text-gray-500 truncate">
                          {t.landing_page ? shortUrl(t.landing_page) : ""}{t.referrer ? ` ← ${shortUrl(t.referrer)}` : ""}
                        </p>
                      )}
                    </div>
                  </li>
                );
              })}
            </ol>
          )}
        </div>
      )}
    </div>
  );
}
