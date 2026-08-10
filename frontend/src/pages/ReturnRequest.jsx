import { useState, useEffect } from "react";
import { useParams, Navigate } from "react-router-dom";
import axios from "axios";
import { toast } from "sonner";
import { RotateCcw, CheckCircle2, AlertTriangle } from "lucide-react";

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;
const MS_14D = 14 * 24 * 3600 * 1000;

// İade sebepleri — müşteri ZORUNLU seçer, panelde görünür.
export const RETURN_REASONS = [
  "Beden/kalıp uymadı",
  "Ürünü beğenmedim",
  "Kalite beklentimi karşılamadı",
  "Ürün kusurlu/defolu geldi",
  "Yanlış ürün/beden geldi",
  "Kargo/paket hasarlı geldi",
  "Fikrim değişti",
  "Diğer",
];

export default function ReturnRequest() {
  const { orderNumber } = useParams();
  const token = localStorage.getItem("token");
  const [order, setOrder] = useState(null);
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState({});
  const [reasonCode, setReasonCode] = useState("");
  const [reasonDetail, setReasonDetail] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [ret, setRet] = useState(null); // oluşturulan/var olan iade

  const auth = token ? { headers: { Authorization: `Bearer ${token}` } } : {};

  useEffect(() => {
    if (!token) return;
    (async () => {
      try {
        const o = await axios.get(`${API}/orders/by-number/${orderNumber}`);
        setOrder(o.data);
        // varsa mevcut iade
        try {
          const r = await axios.get(`${API}/orders/${o.data.id}/return`, auth);
          if (r.data?.return) setRet(r.data.return);
        } catch (_) { /* iade yok */ }
      } catch (e) {
        // sipariş yok
      } finally {
        setLoading(false);
      }
    })();
    // eslint-disable-next-line
  }, [orderNumber, token]);

  if (!token) return <Navigate to="/giris" replace />;

  if (loading) return <div className="max-w-xl mx-auto px-4 py-20 text-center text-gray-400">Yükleniyor…</div>;

  if (!order) {
    return (
      <div className="max-w-xl mx-auto px-4 py-20 text-center">
        <h1 className="text-xl font-medium mb-2">Sipariş bulunamadı</h1>
        <p className="text-sm text-gray-500">“{orderNumber}” numaralı sipariş bulunamadı.</p>
      </div>
    );
  }

  const items = order.items || [];
  const deliveredAt = order.delivered_at ? new Date(order.delivered_at) : null;
  const within14 = deliveredAt ? (Date.now() - deliveredAt.getTime()) <= MS_14D : false;
  const deadline = deliveredAt ? new Date(deliveredAt.getTime() + MS_14D) : null;

  const toggle = (i) => setSelected((s) => ({ ...s, [i]: !s[i] }));

  const submit = async () => {
    const idxs = Object.keys(selected).filter((k) => selected[k]).map(Number);
    if (!reasonCode) { toast.error("Lütfen bir iade sebebi seçin"); return; }
    const reason = reasonCode === "Diğer"
      ? (reasonDetail.trim() ? `Diğer: ${reasonDetail.trim()}` : "Diğer")
      : reasonCode;
    try {
      setSubmitting(true);
      const res = await axios.post(
        `${API}/orders/${order.id}/return-request`,
        { items: idxs, reason, reason_code: reasonCode },
        auth
      );
      if (res.data?.return) {
        setRet(res.data.return);
        toast.success("İade talebiniz oluşturuldu");
      }
    } catch (e) {
      toast.error(e.response?.data?.detail || "İade talebi oluşturulamadı");
    } finally {
      setSubmitting(false);
    }
  };

  // --- Oluşturulan / mevcut iade görünümü ---
  if (ret) {
    const vu = ret.valid_until ? new Date(ret.valid_until).toLocaleString("tr-TR") : "";
    const contractNo = ret.contract_no || "490059279";
    const iadeNo = ret.iade_no || "";
    // Süresi dolan kod: barkod gizlenir, müşteri (14 gün penceresi açıksa) yeni kod üretir.
    if (ret.status === "expired") {
      return (
        <div className="max-w-xl mx-auto px-4 py-10 md:py-16">
          <div className="flex items-center gap-2 mb-6">
            <AlertTriangle className="text-amber-500" size={22} />
            <h1 className="text-2xl font-medium tracking-wide">İade Kodunun Süresi Doldu</h1>
          </div>
          <div className="border border-amber-200 bg-amber-50 p-5 space-y-4">
            <p className="text-sm text-amber-900">
              <b className="font-mono">{ret.return_code}</b> kodlu iade kargo kodunun 3 günlük
              geçerlilik süresi doldu (son geçerlilik: <b>{vu}</b>). Bu barkod artık şubede okutulamaz.
            </p>
            <p className="text-sm text-amber-900">
              Teslimattan itibaren <b>14 günlük</b> iade süreniz devam ediyorsa aşağıdan yeni bir kod oluşturabilirsiniz.
            </p>
            <button
              onClick={() => setRet(null)}
              className="w-full bg-black text-white py-3 text-sm tracking-widest uppercase hover:bg-gray-800"
            >
              Yeni İade Kodu Oluştur
            </button>
          </div>
        </div>
      );
    }
    return (
      <div className="max-w-xl mx-auto px-4 py-10 md:py-16">
        <div className="flex items-center gap-2 mb-6">
          <CheckCircle2 className="text-emerald-600" size={22} />
          <h1 className="text-2xl font-medium tracking-wide">İade Talebiniz Hazır</h1>
        </div>
        <div className="border border-gray-200 p-5 space-y-4">
          <div>
            <p className="text-xs uppercase tracking-[0.2em] text-gray-400 mb-1">İade Kargo Kodu</p>
            <p className="text-xl font-mono font-semibold break-all">{ret.return_code}</p>
            <p className="text-xs text-gray-500 mt-1">{ret.cargo_provider_name} · Son geçerlilik: <b>{vu}</b> (3 gün)</p>
          </div>
          {ret.barcode_data_url && (
            <div className="bg-white border border-gray-100 p-4 flex justify-center">
              <img src={ret.barcode_data_url} alt={ret.return_code} className="h-24 object-contain" />
            </div>
          )}

          {/* İade No (sipariş takibi için) */}
          {iadeNo && (
            <div className="flex items-center justify-between border-t border-gray-100 pt-3 text-sm">
              <span className="text-gray-500">İade No</span>
              <span className="font-mono font-medium text-gray-800 break-all">{iadeNo}</span>
            </div>
          )}

          {/* Alıcı (şirket) adresi — gönderi nereye gidiyor */}
          {ret.company_address && (
            <div className="border-t border-gray-100 pt-3">
              <p className="text-xs uppercase tracking-[0.2em] text-gray-400 mb-1">Alıcı Adresi (Bize Gelir)</p>
              <p className="text-sm text-gray-700">{ret.company_address}</p>
            </div>
          )}

          <p className="text-sm text-gray-600">
            Ürünü <b>nereden gönderirseniz gönderin</b> alıcı bizim şirket adresimizdir.
            En yakın <b>DHL / MNG</b> şubesine bu <b>kodu</b> veya <b>barkodu</b> göstererek teslim edebilirsiniz.
            İade bilgisi hesabınızdaki siparişte de görünür.
          </p>

          {/* Anlaşmalı kargo no — alt satır notu (yedek) */}
          <div className="border-t border-gray-100 pt-3 text-xs text-gray-500 space-y-1">
            <p>Anlaşmalı No: <b className="font-mono text-gray-700">{contractNo}</b></p>
            <p>Şube barkodu okutamazsa, gönderiyi <b>FACETTE</b> anlaşmalı müşteri numaramız <b>{contractNo}</b> ile teslim edebilirsiniz.</p>
          </div>
        </div>
        <a href="/hesabim" className="inline-block text-xs uppercase tracking-[0.15em] mt-6 underline underline-offset-4">Hesabıma Dön</a>
      </div>
    );
  }

  // --- İade formu ---
  return (
    <div className="max-w-xl mx-auto px-4 py-10 md:py-16">
      <div className="flex items-center gap-2 mb-1">
        <RotateCcw size={18} className="text-gray-500" />
        <p className="text-xs uppercase tracking-[0.2em] text-gray-400">İade Talebi</p>
      </div>
      <h1 className="text-2xl font-medium tracking-wide mb-6">Sipariş {order.order_number}</h1>

      {!deliveredAt ? (
        <div className="border border-amber-200 bg-amber-50 p-4 flex items-start gap-2">
          <AlertTriangle size={18} className="text-amber-600 mt-0.5" />
          <p className="text-sm text-amber-900">Bu sipariş henüz teslim edilmedi. İade, teslimattan sonra başlatılabilir.</p>
        </div>
      ) : !within14 ? (
        <div className="border border-red-200 bg-red-50 p-4 flex items-start gap-2">
          <AlertTriangle size={18} className="text-red-600 mt-0.5" />
          <p className="text-sm text-red-900">İade süresi (teslimden itibaren 14 gün) dolmuştur.</p>
        </div>
      ) : (
        <>
          <p className="text-xs text-gray-500 mb-4">
            İade etmek istediğiniz ürünleri seçin. Son iade tarihi: <b>{deadline.toLocaleDateString("tr-TR")}</b>
          </p>
          <div className="border border-gray-200 divide-y mb-5">
            {items.map((it, i) => (
              <label key={i} className="flex items-center gap-3 p-3 cursor-pointer hover:bg-gray-50">
                <input type="checkbox" checked={!!selected[i]} onChange={() => toggle(i)} className="rounded" />
                <div className="w-12 h-14 bg-gray-50 border border-gray-100 overflow-hidden shrink-0">
                  {it.image ? <img src={it.image} alt="" className="w-full h-full object-contain" /> : null}
                </div>
                <div className="flex-1 min-w-0 text-sm">
                  <p className="truncate">{it.name || it.product_name || "Ürün"}</p>
                  <p className="text-xs text-gray-500 mt-0.5">
                    {it.size ? `Beden: ${it.size} · ` : ""}{it.color ? `Renk: ${it.color} · ` : ""}Adet: {it.quantity || 1}
                  </p>
                </div>
              </label>
            ))}
          </div>

          {/* İade sebebi — ZORUNLU */}
          <label className="block text-xs uppercase tracking-[0.15em] text-gray-500 mb-1">
            İade Sebebi <span className="text-red-600">*</span>
          </label>
          <select
            value={reasonCode}
            onChange={(e) => setReasonCode(e.target.value)}
            className="w-full border border-gray-200 p-3 text-sm bg-white focus:outline-none focus:border-gray-500 mb-3"
            data-testid="return-reason-select"
          >
            <option value="">— Sebep seçin —</option>
            {RETURN_REASONS.map((r) => <option key={r} value={r}>{r}</option>)}
          </select>
          {reasonCode === "Diğer" && (
            <textarea
              value={reasonDetail}
              onChange={(e) => setReasonDetail(e.target.value)}
              placeholder="Sebebinizi kısaca yazın"
              rows={2}
              className="w-full border border-gray-200 p-3 text-sm resize-none focus:outline-none focus:border-gray-500 mb-4"
            />
          )}

          <button
            onClick={submit}
            disabled={submitting || !reasonCode}
            className="w-full bg-black text-white py-3 text-xs uppercase tracking-[0.2em] hover:bg-gray-800 transition-colors disabled:opacity-40"
          >
            {submitting ? "Oluşturuluyor…" : "İade Talebi Oluştur"}
          </button>
          <p className="text-[11px] text-gray-400 mt-3">
            Hiç ürün seçmezseniz siparişteki tüm ürünler için iade oluşturulur. Onay sonrası 3 gün geçerli bir kargo kodu/barkodu verilir.
          </p>
        </>
      )}
    </div>
  );
}
