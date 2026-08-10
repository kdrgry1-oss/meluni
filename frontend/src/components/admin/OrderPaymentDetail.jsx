// =============================================================================
// OrderPaymentDetail.jsx — Sipariş detayında Iyzico ödeme detayı (FAZ 3)
// -----------------------------------------------------------------------------
// Site/web siparişlerinde iyzico yanıtından gelen ödeme görüntüsünü gösterir:
// taksit, MASKELİ kart (ilk6 •••••• son4), kart tipi, auth code, ödeme no ve
// Iyzico komisyonu. Yalnızca iyzico verisi varsa görünür (pazaryeri/havalede yok).
// KVKK: tam kart no asla tutulmaz/gösterilmez — yalnızca maskeli alanlar.
// =============================================================================

function money(v) {
  if (v === "" || v === null || v === undefined) return "";
  const n = Number(v);
  if (!isFinite(n)) return "";
  return n.toLocaleString("tr-TR", { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + " TL";
}

const CARD_TYPE = {
  CREDIT_CARD: "Kredi Kartı",
  DEBIT_CARD: "Banka Kartı",
  PREPAID_CARD: "Ön Ödemeli",
};

function Row({ label, value }) {
  if (value === "" || value === null || value === undefined) return null;
  return (
    <div className="flex justify-between gap-3 text-sm py-0.5">
      <span className="text-gray-500">{label}</span>
      <span className="text-right text-gray-800 break-all">{value}</span>
    </div>
  );
}

export default function OrderPaymentDetail({ order }) {
  if (!order) return null;
  const p = order.iyzico_retrieve_response || null;
  const pid = (p && p.paymentId) || order.iyzico_payment_id || order.payment_id || "";
  if (!p && !pid) return null;
  const d = p || {};

  const inst = Number(d.installment) || 0;
  const taksit = inst > 1 ? `${inst} Taksit` : inst === 1 ? "Tek Çekim" : "";

  const masked =
    d.binNumber || d.lastFourDigits
      ? `${d.binNumber || "••••••"} •••••• ${d.lastFourDigits || "••••"}`
      : "";

  const kartTipi = [CARD_TYPE[d.cardType] || d.cardType, d.cardAssociation, d.cardFamily]
    .filter(Boolean)
    .join(" · ");

  const komisyon = (Number(d.iyziCommissionFee) || 0) + (Number(d.iyziCommissionRateAmount) || 0);

  return (
    <div className="border rounded-lg p-3 bg-emerald-50/40">
      <h3 className="font-medium text-emerald-800 mb-2 text-sm">Ödeme Detayı · Iyzico</h3>
      <Row label="Tutar" value={money(d.paidPrice)} />
      <Row label="Taksit" value={taksit} />
      <Row label="Kart (maskeli)" value={masked} />
      <Row label="Kart Tipi" value={kartTipi} />
      <Row label="Auth Code" value={d.authCode} />
      <Row label="Iyzico Ödeme No" value={pid} />
      <Row label="Iyzico Komisyonu" value={komisyon > 0 ? money(komisyon) : ""} />
      <Row label="Durum" value={d.paymentStatus || d.status} />
    </div>
  );
}
