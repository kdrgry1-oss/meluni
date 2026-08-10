// =============================================================================
// OrderMarketplaceInfo.jsx — Sipariş detayında Pazaryeri & Kaynak bilgileri
// -----------------------------------------------------------------------------
// FAZ 1b: Pazaryeri siparişlerinde SLA / paket durumu / MP kargo kodu / ihracat;
// Site siparişlerinde IP + UTM/reklam attribution kutusu. Sadece ilgili alan
// doluysa görünür. Kaynak; sipariş no önekinden (TY/HB/yoksa Site) belirlenir.
// =============================================================================

function fmtDate(ts) {
  if (!ts) return "";
  try {
    const d = new Date(ts);
    if (isNaN(d.getTime())) return String(ts);
    return d.toLocaleString("tr-TR", {
      day: "2-digit", month: "2-digit", year: "numeric",
      hour: "2-digit", minute: "2-digit",
    });
  } catch {
    return String(ts);
  }
}

function Row({ label, value, danger }) {
  if (!value) return null;
  return (
    <div className="flex justify-between gap-3 text-sm py-0.5">
      <span className="text-gray-500">{label}</span>
      <span className={`text-right break-all ${danger ? "text-red-600 font-medium" : "text-gray-800"}`}>
        {value}
      </span>
    </div>
  );
}

export default function OrderMarketplaceInfo({ order }) {
  if (!order) return null;

  const on = (order.order_number || "").toUpperCase();
  const plat = (order.platform || "").toLowerCase();
  const isTY = on.startsWith("TY") || plat === "trendyol";
  const isHB = on.startsWith("HB") || plat === "hepsiburada";
  const isMarketplace = isTY || isHB;
  const mpName = isTY ? "Trendyol" : isHB ? "Hepsiburada" : "Pazaryeri";

  const att = order.attribution || {};
  const est =
    order.marketplace_estimated_delivery_start || order.marketplace_estimated_delivery_end
      ? `${fmtDate(order.marketplace_estimated_delivery_start)}${
          order.marketplace_estimated_delivery_end
            ? " – " + fmtDate(order.marketplace_estimated_delivery_end)
            : ""
        }`
      : "";

  const showMarketplace =
    isMarketplace &&
    (order.marketplace_status ||
      order.marketplace_agreed_delivery_date ||
      est ||
      order.cargo_sender_number ||
      order.cargo_provider_name ||
      order.is_micro_export);

  const hasUtm = !!(att.source || att.medium || att.campaign || att.content || att.landing_page);
  const showSource = !!(order.customer_ip || hasUtm);

  if (!showMarketplace && !showSource) return null;

  return (
    <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
      {showMarketplace && (
        <div className="border rounded-lg p-3 bg-orange-50/40">
          <h3 className="font-medium text-orange-800 mb-2 text-sm">Pazaryeri Bilgileri · {mpName}</h3>
          <Row label="Paket Durumu" value={order.marketplace_status} />
          <Row label="Kargoya Son / Anlaşılan Teslim" value={fmtDate(order.marketplace_agreed_delivery_date)} danger />
          <Row label="Tahmini Teslim" value={est} />
          <Row label="Kargo Firması" value={order.cargo_provider_name} />
          <Row label="MP Kargo Kodu" value={order.cargo_sender_number} />
          <Row label="İhracat" value={order.is_micro_export ? "Evet (Mikro İhracat)" : ""} />
        </div>
      )}

      {showSource && (
        <div className="border rounded-lg p-3 bg-slate-50">
          <h3 className="font-medium text-slate-700 mb-2 text-sm">Kaynak &amp; Reklam</h3>
          <Row label="IP Adresi" value={order.customer_ip} />
          <Row label="Reklam Kaynağı" value={att.source} />
          <Row label="Mecra" value={att.medium} />
          <Row label="Kampanya" value={att.campaign} />
          <Row label="İçerik" value={att.content} />
          <Row label="Giriş Sayfası" value={att.landing_page} />
        </div>
      )}
    </div>
  );
}
