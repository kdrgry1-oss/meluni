const CSS_TOKEN_MAP = {
  background: "--theme-bg", surface: "--theme-surface", text: "--theme-text",
  muted: "--theme-muted", accent: "--theme-accent", border: "--theme-border",
  font_heading: "--theme-heading", font_body: "--theme-body", radius: "--theme-radius",
};

export function themeCssVariables(tokens = {}) {
  return Object.fromEntries(Object.entries(CSS_TOKEN_MAP).filter(([key]) => tokens[key]).map(([key, variable]) => [variable, tokens[key]]));
}

export function themeImage(product) {
  if (!product) return "";
  const images = Array.isArray(product.images) ? product.images : [];
  const first = images[0];
  return (typeof first === "string" ? first : first?.url) || product.image || product.thumbnail || "";
}

export function themePrice(product) {
  const value = product?.sale_price ?? product?.price;
  if (value == null || Number.isNaN(Number(value))) return "Fiyat bilgisi yok";
  return `${Number(value).toLocaleString("tr-TR", { maximumFractionDigits: 2 })} ₺`;
}
