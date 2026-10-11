/**
 * safeUrl — admin/CMS'ten gelen link hedeflerini güvenli şemalara sınırlar.
 * DENETİM (injection): SitePopup/AnnouncementBar/CookieConsent/Footer gibi yerlerde
 * yönetici-ayarlı href'ler şema kontrolü olmadan render ediliyordu → kötü niyetli/ele
 * geçirilmiş panelden `javascript:` şemasıyla ziyaretçilere stored-XSS. Yalnız http(s),
 * mailto, tel ve site-içi (/...) yollara izin ver; gerisi güvenli "#" olur.
 */
export function safeUrl(u) {
  const s = String(u == null ? "" : u).trim();
  if (!s) return "#";
  // Site-içi göreli yol / anchor
  if (s.startsWith("/") || s.startsWith("#")) return s;
  // Protokol-göreli //host → https'e sabitle
  if (s.startsWith("//")) return "https:" + s;
  const low = s.toLowerCase();
  if (low.startsWith("http://") || low.startsWith("https://") ||
      low.startsWith("mailto:") || low.startsWith("tel:")) {
    return s;
  }
  // Şemasız "example.com/x" gibi değerleri https ile aç
  if (/^[a-z0-9.-]+\.[a-z]{2,}(\/|$|\?|#)/i.test(s)) return "https://" + s;
  // javascript:, data:, vbscript: vb. → engelle
  return "#";
}

export default safeUrl;
