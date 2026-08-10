/**
 * =============================================================================
 * StockTab.jsx — Ürün modalı "Stok" sekmesi
 * =============================================================================
 *
 * AMAÇ:
 *   Varyantların stok miktarlarını tek tek elle güncellemeyi sağlar. Üst
 *   kısımda tüm varyantların toplam stok sayısını gösterir. Bu sekme, mevcut
 *   ürünün bedenlerini hızlıca revize etmek için optimize edilmiştir
 *   (Varyantlar sekmesinde daha geniş CRUD yapılır).
 *
 * PROPS:
 *   - formData    : Üst formun mevcut değeri (variants dizisi burada kullanılır).
 *   - setFormData : Variants[index].stock değerini parseInt ile güncellemek için.
 *
 * BAĞLANTILI BACKEND:
 *   - PUT /api/products/{id}     → Form submit edildiğinde tüm variants bir
 *                                   defada güncellenir.
 *   - POST /api/integrations/trendyol/products/{id}/sync-inventory
 *     (sadece Trendyol'da eşi varsa, Products listesindeki "RefreshCw" butonu
 *      ile ayrıca tetiklenir)
 *
 * NOT: Burada değişen stok'lar formData'dadır — Save butonuna basılmadan
 *       veritabanına yazılmaz. Kullanıcıya kaybolma riskini iletmek için
 *       ileride "unsaved changes" rozetı eklenebilir (P2 backlog).
 * =============================================================================
 */
export default function StockTab({ formData, setFormData }) {
  const variants = formData.variants || [];
  const totalStock = variants.reduce((sum, v) => sum + (v.stock || 0), 0);

  return (
    <div className="bg-white p-8 rounded-xl border shadow-sm">
      <div className="flex justify-between items-center mb-6">
        <h3 className="font-bold text-xl text-gray-900 uppercase">
          Hızlı Stok Yönetimi
        </h3>
        <div className="flex items-center gap-4">
          <span className="text-xs font-bold text-gray-400 uppercase">
            Toplam Stok:
          </span>
          <span
            className="px-4 py-1 bg-black text-white rounded-full text-lg font-black"
            data-testid="product-stock-total"
          >
            {totalStock}
          </span>
        </div>
      </div>

      <div className="border rounded-2xl overflow-hidden shadow-sm">
        <table className="w-full text-sm">
          <thead className="bg-gray-50 border-b">
            <tr>
              <th className="text-left px-6 py-4 font-black text-gray-500 uppercase tracking-widest">
                Varyant
              </th>
              <th className="text-left px-6 py-4 font-black text-gray-500 uppercase tracking-widest">
                Stok
              </th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {variants.map((v, idx) => (
              <tr
                key={idx}
                className="hover:bg-orange-50/20 transition-colors"
                data-testid={`product-stock-row-${idx}`}
              >
                <td className="px-6 py-4">
                  <span className="font-bold text-black">
                    {v.size} {v.color && `/ ${v.color}`}
                  </span>
                  <p className="text-[10px] text-gray-400 font-mono mt-1">
                    {v.stock_code || v.barcode}
                  </p>
                </td>
                <td className="px-6 py-4">
                  <input
                    type="number"
                    value={v.stock || 0}
                    onChange={(e) => {
                      const updated = [...variants];
                      updated[idx] = {
                        ...updated[idx],
                        stock: parseInt(e.target.value) || 0,
                      };
                      setFormData({ ...formData, variants: updated });
                    }}
                    className="w-24 text-center border-2 border-gray-100 px-4 py-2 rounded-xl text-lg font-black"
                    data-testid={`product-stock-input-${idx}`}
                  />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
