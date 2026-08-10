import { useEffect, useState } from "react";
import { Copy, Check, ExternalLink, AlertTriangle, Truck } from "lucide-react";

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;

/**
 * Sipariş ekranında tedarikçi alım listesi — YALNIZ yönetim görür.
 *
 * Dropshipping'de sipariş gelince ana siteden aynı ürünü almak gerekir; orada
 * aratılacak kod TEDARİKÇİNİN kodudur, bizimki değil. Kodlar tek tıkla
 * kopyalanır, çünkü elle yazarken bir karakter kaçırmak yanlış ürün sipariş
 * ettirir.
 */
export default function DropshipPanel({ orderId }) {
  const [data, setData] = useState(null);
  const [err, setErr] = useState("");
  const [copied, setCopied] = useState("");

  useEffect(() => {
    if (!orderId) return;
    let cancelled = false;
    fetch(`${API}/dropship/orders/${orderId}`, {
      headers: { Authorization: `Bearer ${localStorage.getItem("token") || ""}` },
    })
      .then((r) => (r.ok ? r.json() : r.json().then((d) => Promise.reject(new Error(d.detail)))))
      .then((d) => !cancelled && setData(d))
      .catch((e) => !cancelled && setErr(e.message));
    return () => {
      cancelled = true;
    };
  }, [orderId]);

  const copy = (text, key) => {
    navigator.clipboard?.writeText(text);
    setCopied(key);
    setTimeout(() => setCopied(""), 1500);
  };

  if (err) return <div className="text-sm text-red-600 p-4">Tedarikçi bilgisi alınamadı: {err}</div>;
  if (!data) return <div className="text-sm text-gray-500 p-4">Yükleniyor…</div>;
  if (!data.lines?.length) return null;

  return (
    <div className="rounded-xl border border-amber-300 bg-amber-50/40 p-4 mt-4">
      <div className="flex items-center gap-2 mb-1">
        <Truck className="w-4 h-4" />
        <h3 className="font-semibold">Tedarikçi Alım Listesi</h3>
        <span className="text-[11px] uppercase tracking-wide bg-black text-white px-2 py-0.5 rounded">
          yalnız yönetim
        </span>
      </div>
      <p className="text-xs text-gray-600 mb-3">
        Ana siteden bu kodlarla sipariş verin. Müşteriye görünmez.
      </p>

      {data.warning && (
        <div className="mb-3 flex gap-2 rounded-lg bg-red-50 border border-red-200 px-3 py-2 text-sm text-red-800">
          <AlertTriangle className="w-4 h-4 shrink-0 mt-0.5" />
          {data.warning}
        </div>
      )}

      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-xs text-gray-500 border-b">
              <th className="py-2 pr-3">Ürün</th>
              <th className="py-2 pr-3">Beden / Renk</th>
              <th className="py-2 pr-3">Adet</th>
              <th className="py-2 pr-3">Tedarikçi Kodu</th>
              <th className="py-2 pr-3 text-right">Alış</th>
            </tr>
          </thead>
          <tbody>
            {data.lines.map((l, i) => {
              const code = l.supplier_barcode || l.supplier_stock_code || l.supplier_model_ref;
              return (
                <tr key={i} className={`border-b last:border-0 ${!l.matched ? "bg-red-50/60" : ""}`}>
                  <td className="py-2 pr-3">
                    <div className="font-medium">{l.name}</div>
                    {l.supplier_url && (
                      <a
                        href={l.supplier_url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="text-xs text-blue-600 inline-flex items-center gap-1 hover:underline"
                      >
                        tedarikçi sayfası <ExternalLink className="w-3 h-3" />
                      </a>
                    )}
                  </td>
                  <td className="py-2 pr-3 whitespace-nowrap">
                    {l.size || "—"} / {l.color || "—"}
                  </td>
                  <td className="py-2 pr-3 font-semibold">{l.quantity}</td>
                  <td className="py-2 pr-3">
                    {code ? (
                      <button
                        onClick={() => copy(code, `c${i}`)}
                        className="font-mono text-xs bg-white border rounded px-2 py-1 inline-flex items-center gap-1.5 hover:bg-gray-50"
                        title="Kopyala"
                      >
                        {code}
                        {copied === `c${i}` ? (
                          <Check className="w-3 h-3 text-green-600" />
                        ) : (
                          <Copy className="w-3 h-3 text-gray-400" />
                        )}
                      </button>
                    ) : (
                      <span className="text-red-600 text-xs">kod yok</span>
                    )}
                    {!l.matched && (
                      <div className="text-[11px] text-red-700 mt-1">
                        eşleşmedi ({l.matched_by}) — elle kontrol edin
                      </div>
                    )}
                  </td>
                  <td className="py-2 pr-3 text-right whitespace-nowrap">
                    {l.line_cost.toFixed(2)} ₺
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      <div className="flex flex-wrap gap-x-6 gap-y-1 mt-3 pt-3 border-t text-sm">
        <div>
          Toplam alış: <b>{data.total_cost.toFixed(2)} ₺</b>
        </div>
        <div>
          Satış: <b>{data.revenue.toFixed(2)} ₺</b>
        </div>
        <div className={data.gross_profit >= 0 ? "text-green-700" : "text-red-700"}>
          Brüt kâr: <b>{data.gross_profit.toFixed(2)} ₺</b>
        </div>
      </div>
      {/* Kargo ve komisyon bu hesaba dahil değil — yanıltmamak için yazıyoruz. */}
      <p className="text-[11px] text-gray-500 mt-1">
        Kargo ve ödeme komisyonu dahil değildir.
      </p>
    </div>
  );
}
