import axios from "axios";

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;

export async function adminDocumentError(error) {
  const status = error?.response?.status;
  let data = error?.response?.data;
  // Axios returns error responses as Blob too when responseType is blob.
  if (data && typeof data.text === "function") {
    try { data = JSON.parse(await data.text()); } catch { data = null; }
  }
  const detail = typeof data?.detail === "string" ? data.detail : "";
  if (status === 401) return "Oturumunuz sona ermiş. Lütfen yeniden giriş yapın.";
  if (status === 403) return detail || "Bu belgeyi görüntülemek için yetkiniz yok.";
  if (status === 404) return detail || "Sipariş veya belge bulunamadı.";
  if (status === 429) return "Çok fazla belge isteği gönderildi. Biraz sonra tekrar deneyin.";
  if (status >= 500) return `Belge hazırlanırken sunucu hatası oluştu (${status}).`;
  if (status) return detail || `Belge alınamadı (HTTP ${status}).`;
  if (error?.isAxiosError || error?.request) return "Belge sunucusuna erişilemedi. Bağlantıyı kontrol edip tekrar deneyin.";
  return error?.message || "Belge alınamadı.";
}

export async function fetchAdminDocument(path) {
  // Accept only an API-relative path; never attach the bearer to external links.
  if (!path.startsWith("/") || path.startsWith("//") || path.includes("\\") || /[?&]token=/i.test(path)) {
    throw new Error("Geçersiz belge adresi");
  }
  const token = localStorage.getItem("token");
  if (!token) throw new Error("Lütfen yeniden giriş yapın");
  try {
    const result = await axios.get(`${API}${path}`, {
      // Cache policy belongs to the RESPONSE (backend sends no-store/private).
      // A request Cache-Control header triggers a CORS preflight rejected by our API.
      headers: { Authorization: `Bearer ${token}` },
      responseType: "blob",
    });
    return result.data;
  } catch (error) {
    throw new Error(await adminDocumentError(error));
  }
}

export async function fetchAdminDocumentTexts(ids, suffix) {
  const results = await Promise.all(ids.map(async (id) => {
    try {
      const blob = await fetchAdminDocument(`/orders/${encodeURIComponent(id)}/${suffix}`);
      const html = await blob.text();
      if (!html.trim()) throw new Error("Sunucu boş belge döndürdü.");
      return { id, html };
    } catch (error) {
      return { id, error: await adminDocumentError(error) };
    }
  }));
  return {
    htmls: results.filter(r => r.html).map(r => r.html),
    failures: results.filter(r => r.error),
  };
}

export async function openAdminDocument(path, features = "", print = false) {
  const popup = window.open("about:blank", "_blank", features);
  if (!popup) throw new Error("Yazdırma penceresi engellendi. Açılır pencerelere izin verin.");
  popup.opener = null;
  try {
    let blob = await fetchAdminDocument(path);
    if (print) {
      // Belge blob: URL'den açılır → sunucu şablonlarının `?print=1` kontrolü (location.search)
      // BOŞ kalır ve popup.onload da belge değişince güvenilir tetiklenmez; yazdırma diyaloğu
      // hiç açılmıyordu. Yazdırma tetikleyicisini belgenin İÇİNE gömüyoruz (tek sefer, guard'lı).
      const type = (blob.type || "").toLowerCase();
      if (!type || type.includes("html")) {
        const html = await blob.text();
        const trigger = "<script>(function(){if(window.__fcPrint)return;window.__fcPrint=1;"
          + "var go=function(){setTimeout(function(){try{window.focus();window.print();}catch(e){}},300);};"
          + "if(document.readyState==='complete')go();else window.addEventListener('load',go);})();</script>";
        const merged = /<\/body>/i.test(html) ? html.replace(/<\/body>/i, trigger + "</body>") : html + trigger;
        blob = new Blob([merged], { type: "text/html;charset=utf-8" });
      } else {
        popup.onload = () => popup.print();
      }
    }
    const url = URL.createObjectURL(blob);
    popup.location.replace(url);
    // Revoke after the document has had time to load, without retaining PII URLs forever.
    setTimeout(() => URL.revokeObjectURL(url), 300000);
  } catch (error) {
    popup.close();
    throw error;
  }
}
