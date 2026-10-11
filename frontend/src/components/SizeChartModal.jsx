import { X } from "lucide-react";
import { optimizeImg } from "../lib/img";

/**
 * SizeChartModal — ürün sayfasındaki "Beden Tablosu" açılır penceresinin paylaşılan sürümü.
 * data: /size-tables-public/{id} yanıtı (exists, sizes[], columns[], values{beden:{ölçü}}, product_size, model_info)
 * img : ölçü tablosu görseli (tablo verisi yoksa)
 */
export default function SizeChartModal({ product, data, img, onClose }) {
  if (!data && !img) return null;
  const image = product?.images?.[0] || product?.image;
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4" onClick={onClose} data-testid="size-chart-modal">
      <div className="bg-white max-w-3xl w-full max-h-[90vh] overflow-auto" onClick={(e) => e.stopPropagation()}>
        <div className="sticky top-0 bg-white flex justify-between items-center px-6 py-4 border-b">
          <h3 className="text-base font-semibold uppercase tracking-wider text-gray-800">Beden Kılavuzu</h3>
          <button type="button" onClick={onClose} className="p-1" aria-label="Kapat"><X size={18} /></button>
        </div>
        {!data && img && (
          <div className="p-4 flex justify-center">
            <img src={optimizeImg(img, 1000)} alt="Beden Tablosu" className="max-w-full max-h-[75vh] w-auto h-auto object-contain" />
          </div>
        )}
        {data && (
          <div className="p-4">
            <div className="flex flex-row gap-4 mb-3">
              {image && <img src={optimizeImg(image, 500)} alt={product?.name || ""} className="w-28 sm:w-32 h-auto object-contain flex-shrink-0 self-start bg-gray-50" loading="lazy" />}
              <div className="min-w-0 flex-1">
                <h4 className="text-sm font-medium text-gray-800 mb-1.5">{product?.name}</h4>
                {data.product_size && (
                  <p className="text-[11px] text-gray-700 mt-2"><span className="font-semibold text-gray-800">Ürün Bedeni:</span> {data.product_size}</p>
                )}
                {data.model_info && Object.keys(data.model_info).length > 0 && (
                  <p className="text-[11px] text-gray-700 mt-1"><span className="font-semibold text-gray-800">Manken ölçüleri:</span> {Object.entries(data.model_info).filter(([, v]) => String(v).trim()).map(([k, v]) => `${k} ${v} cm`).join(", ")}</p>
                )}
              </div>
            </div>
            <div className="overflow-x-auto border border-gray-200">
              <table className="w-full text-[11px] border-collapse">
                <thead>
                  <tr className="bg-gray-50 border-b border-gray-200">
                    <th className="text-left px-3 py-1.5 font-semibold text-gray-800">Ölçüler</th>
                    {(data.sizes || []).map((s) => <th key={s} className="px-3 py-1.5 text-center font-semibold text-gray-800 border-l border-gray-100">{s}</th>)}
                  </tr>
                </thead>
                <tbody>
                  {(data.columns || []).map((c, ri) => (
                    <tr key={c} className={`border-b border-gray-100 ${ri % 2 === 0 ? "bg-white" : "bg-gray-50/60"}`}>
                      <td className="px-3 py-1.5 font-medium text-gray-700">{c}</td>
                      {(data.sizes || []).map((s) => <td key={s} className="px-3 py-1.5 text-center text-gray-600 border-l border-gray-100">{data.values?.[s]?.[c] || "—"}</td>)}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p className="text-[10px] text-gray-400 mt-2">Tüm ölçüler cm cinsindendir; ± 1-2 cm tolerans taşıyabilir.</p>
          </div>
        )}
      </div>
    </div>
  );
}
