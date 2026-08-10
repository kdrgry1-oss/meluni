import axios from "axios";
import { toast } from "sonner";

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;

/**
 * Sepeti Paylaş — sepet kalemlerini sunucuya yazar, kısa link üretir.
 * Mobilde yerel paylaşım menüsü (navigator.share), yoksa panoya kopyalar.
 * Hataları kendi içinde toast'lar; asla throw etmez.
 */
export async function shareCart(items) {
  if (!items || items.length === 0) {
    toast.error("Sepetiniz boş");
    return;
  }
  try {
    const res = await axios.post(`${API}/shared-carts`, {
      items: items.map((it) => ({
        product_id: it.productId,
        variant_id: it.variantId || null,
        quantity: it.quantity,
      })),
    });
    const url = `${window.location.origin}/sepet?paylasim=${res.data.id}`;
    if (navigator.share) {
      try {
        await navigator.share({ title: "Sepetim", text: "Sepetimdeki ürünlere göz at:", url });
        return;
      } catch (_) { /* kullanıcı iptal etti → kopyalamaya düş */ }
    }
    await navigator.clipboard.writeText(url);
    toast.success("Sepet linki kopyalandı — dilediğin kişiyle paylaşabilirsin");
  } catch (e) {
    toast.error(e?.response?.data?.detail || "Sepet paylaşılamadı");
  }
}
