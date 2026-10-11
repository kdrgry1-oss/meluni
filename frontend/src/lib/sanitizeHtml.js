/**
 * sanitizeHtml.js — Bağımlılıksız, tarayıcı-DOM tabanlı HTML temizleyici.
 * =====================================================================
 * dangerouslySetInnerHTML ile basılan SUNUCU/PAZARYERİ kaynaklı HTML'i (ürün
 * açıklaması, CMS içeriği, footer özel HTML) STORED-XSS'e karşı temizler.
 *
 * Yöntem: içerik önce <template> içine yazılır — template içeriği INERT'tir,
 * script çalışmaz, <img onerror> tetiklenmez. Sonra bir allowlist ile tehlikeli
 * etiketler (script/iframe/object/…), tüm on* olay öznitelikleri ve javascript:
 * URI'leri kaldırılır. Kalan güvenli HTML döndürülür.
 */

const ALLOWED_TAGS = new Set([
  "p", "br", "hr", "span", "div", "b", "strong", "i", "em", "u", "s", "small", "mark",
  "ul", "ol", "li", "a", "h1", "h2", "h3", "h4", "h5", "h6",
  "table", "thead", "tbody", "tfoot", "tr", "td", "th", "caption",
  "img", "blockquote", "pre", "code", "figure", "figcaption", "sub", "sup",
]);

const ALLOWED_ATTR = new Set([
  "href", "src", "alt", "title", "target", "rel", "colspan", "rowspan",
  "width", "height", "class", "align", "dir", "lang",
]);

function _cleanUrl(val) {
  const v = String(val || "").trim();
  // GÜVENLİK: TÜM C0 kontrol karakterleri + boşluk kaldırılır; böylece
  // "java\tscript:", "java\x00script:", "  javascript:" gibi kaçış denemeleri de
  // normalize edilip "javascript:" olarak yakalanır. -  ASCII kaynak,
  // no-control-regex sorunu yok.
  const stripped = Array.from(v).filter((ch) => ch.charCodeAt(0) > 0x20).join("").toLowerCase();
  if (
    stripped.startsWith("javascript:") ||
    stripped.startsWith("vbscript:") ||
    stripped.startsWith("data:text/html") ||
    stripped.startsWith("data:image/svg")
  ) {
    return null;
  }
  if (stripped.startsWith("data:") && !stripped.startsWith("data:image/")) return null;
  return v;
}

export function sanitizeHtml(dirty, options = {}) {
  if (dirty == null) return "";
  const str = String(dirty);
  if (typeof document === "undefined") return ""; // SSR/olmayan DOM: güvenli tarafta boş
  let tpl;
  try {
    tpl = document.createElement("template");
    tpl.innerHTML = str; // INERT — script çalışmaz, olay tetiklenmez
  } catch {
    return "";
  }

  const walk = (node) => {
    const children = Array.from(node.childNodes);
    for (const el of children) {
      if (el.nodeType === 8) { // yorum düğümü
        el.remove();
        continue;
      }
      if (el.nodeType !== 1) continue; // yalnız element düğümlerini işle
      const tag = el.tagName ? el.tagName.toLowerCase() : "";
      if (!ALLOWED_TAGS.has(tag)) {
        el.remove();
        continue;
      }
      for (const attr of Array.from(el.attributes)) {
        const name = (attr.name || "").toLowerCase();
        const value = attr.value || "";
        if (name.startsWith("on")) { el.removeAttribute(attr.name); continue; } // tüm olay handler'ları
        if (name === "style") {
          if (!options.allowStyle) { el.removeAttribute(attr.name); continue; }
          // E-posta editöründe inline stil görsel bütünlük için gerekir. Ağ isteği
          // veya eski CSS yürütme yüzeyleri içeren stillere yine izin verme.
          const normalizedStyle = value.replace(/\s+/g, "").toLowerCase();
          if (
            normalizedStyle.includes("url(") ||
            normalizedStyle.includes("expression(") ||
            normalizedStyle.includes("@import") ||
            normalizedStyle.includes("-moz-binding") ||
            normalizedStyle.includes("behavior:")
          ) {
            el.removeAttribute(attr.name);
          }
          continue;
        }
        if (!ALLOWED_ATTR.has(name)) { el.removeAttribute(attr.name); continue; }
        if (name === "href" || name === "src") {
          const safe = _cleanUrl(value);
          if (safe === null) { el.removeAttribute(attr.name); continue; }
          el.setAttribute(attr.name, safe);
        }
      }
      // target=_blank güvenliği (reverse tabnabbing)
      if (tag === "a" && (el.getAttribute("target") || "").toLowerCase() === "_blank") {
        el.setAttribute("rel", "noopener noreferrer");
      }
      walk(el);
    }
  };

  try {
    walk(tpl.content);
    return tpl.innerHTML;
  } catch {
    return "";
  }
}

export default sanitizeHtml;
