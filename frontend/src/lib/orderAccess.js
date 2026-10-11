/**
 * Denetim #27: sipariş numarası sıralı olduğundan sipariş ayrıntısı (içerik, tutar, adres, kargo)
 * yalnız sahiplik kanıtıyla gelir. Siparişi oluşturan tarayıcı, sunucunun verdiği sipariş
 * anahtarını (pay_token) burada saklar ve sipariş sayfalarında X-Order-Token başlığıyla gönderir.
 * Aynı cihazda 14 gün tutulur; en çok son 10 sipariş.
 */
const STORE = "meluni_order_tokens";
const TTL_MS = 14 * 24 * 3600 * 1000;

function _read() {
  try {
    const raw = JSON.parse(window.localStorage.getItem(STORE) || "{}");
    const now = Date.now();
    return Object.fromEntries(Object.entries(raw).filter(([, v]) => v && v.t && now - v.at < TTL_MS));
  } catch {
    return {};
  }
}

export function saveOrderToken(orderNumber, token) {
  if (!orderNumber || !token) return;
  try {
    const all = _read();
    all[String(orderNumber)] = { t: token, at: Date.now() };
    const keep = Object.entries(all).sort((a, b) => b[1].at - a[1].at).slice(0, 10);
    window.localStorage.setItem(STORE, JSON.stringify(Object.fromEntries(keep)));
  } catch {
    /* depolama kapalı — sipariş sayfası doğrulama ister */
  }
}

export function orderAccessHeaders(orderNumber, extra = {}) {
  const tok = (_read()[String(orderNumber)] || {}).t;
  return tok ? { ...extra, "X-Order-Token": tok } : { ...extra };
}
