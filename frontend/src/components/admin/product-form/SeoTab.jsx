/**
 * =============================================================================
 * SeoTab.jsx — Ürün modalı "SEO" sekmesi
 * =============================================================================
 *
 * AMAÇ:
 *   Google arama görünümünü kontrol eden meta_title ve meta_description
 *   alanlarını düzenlemek. Frontend product detay sayfasında <Helmet> ile
 *   okunur; Trendyol'a gönderimde de bazı kanallar bu bilgileri kullanır.
 *
 * PROPS:
 *   - formData    : Üst formun mevcut değeri.
 *   - setFormData : Üst formun state setter'ı — meta_title/meta_description
 *                    merge edilerek güncellenir.
 *
 * KULLANIM YERİ:
 *   Products.jsx > ürün modalı > "SEO" sekmesi (TabsContent value="seo")
 *
 * NEDEN AYRI?
 *   Tamamen prop-bağımlı, hiçbir parent closure'a ihtiyaç duymuyor → güvenli
 *   biçimde taşınabilir. Products.jsx'i kısaltma refactor'unun ilk adımı.
 * =============================================================================
 */
import { Globe } from "lucide-react";

export default function SeoTab({ formData, setFormData }) {
  return (
    <div className="bg-white p-8 rounded-xl border shadow-sm space-y-6">
      <h3 className="font-semibold text-lg text-gray-900 border-b pb-4 mb-6 flex items-center gap-2">
        <Globe size={20} className="text-purple-500" />
        Google Arama Görünümü (SEO)
      </h3>
      <div className="space-y-6 max-w-2xl">
        <div>
          <label className="block text-xs font-black text-gray-400 uppercase tracking-widest mb-2">
            Meta Başlık
          </label>
          <input
            type="text"
            value={formData.meta_title || ""}
            onChange={(e) => setFormData({ ...formData, meta_title: e.target.value })}
            className="w-full border-gray-200 border-2 px-4 py-3 rounded-xl focus:border-black outline-none font-bold"
            placeholder="Örn: En Şık Gece Elbiseleri | Facette"
            data-testid="product-seo-meta-title"
          />
        </div>
        <div>
          <label className="block text-xs font-black text-gray-400 uppercase tracking-widest mb-2">
            Meta Açıklama
          </label>
          <textarea
            value={formData.meta_description || ""}
            onChange={(e) =>
              setFormData({ ...formData, meta_description: e.target.value })
            }
            rows={4}
            className="w-full border-gray-200 border-2 px-4 py-3 rounded-xl focus:border-black outline-none font-medium text-sm"
            placeholder="Sayfa açıklamasını buraya yazın..."
            data-testid="product-seo-meta-description"
          />
        </div>
      </div>
    </div>
  );
}
