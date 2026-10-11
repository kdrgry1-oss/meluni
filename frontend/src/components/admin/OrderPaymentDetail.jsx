import { useState, useEffect } from "react";
import axios from "axios";
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

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;

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
  const [check, setCheck] = useState(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => { setCheck(null); }, [order?.id]);

  // W11805 dersi: müşteri kartla iki kez denemişti; iyzico listesinde YARIDA KALAN deneme
  // siparişle karıştırılıp "para çekilmemiş" sanıldı, ürün iade alındı ve iade elle
  // işaretlendi — oysa tahsilat yapılmıştı. Bu düğme siparişin KENDİ ödeme numarasını
  // iyzico'ya sorar; eşleştirme tahmine kalmaz. Sorgu salt okunur, siparişi değiştirmez.
  const dogrula = async () => {
    setBusy(true);
    try {
      const { data } = await axios.get(`${API}/orders/${order.id}/iyzico-verify`, {
        headers: { Authorization: `Bearer ${localStorage.getItem("token")}` },
      });
      setCheck(data);
    } catch (e) {
      setCheck({ ok: false, mesaj: e.response?.data?.detail || "Sorgulanamadı" });
    } finally { setBusy(false); }
  };

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

      <button type="button" onClick={dogrula} disabled={busy} data-testid="iyzico-verify-btn"
        className="mt-2 w-full text-xs border border-emerald-300 rounded px-2 py-1.5
          text-emerald-800 hover:bg-emerald-100 disabled:opacity-50">
        {busy ? "iyzico'ya soruluyor…" : "iyzico'dan doğrula (canlı)"}
      </button>

      {check && (
        <div className="mt-2 text-xs rounded border p-2 bg-white" data-testid="iyzico-verify-result">
          {check.sorgulandi ? (
            <>
              <div className={check.tahsil_edildi ? "text-emerald-700 font-semibold" : "text-red-700 font-semibold"}>
                {check.tahsil_edildi
                  ? `Tahsil edildi · ${money(check.tahsil_edilen)}`
                  : `iyzico tahsilat bildirmiyor (${check.iyzico_durum || "-"})`}
              </div>
              <div className="text-gray-600 mt-1 space-y-0.5">
                <div>Ödeme No: {check.paymentId}</div>
                {check.auth_code && <div>Auth Code: {check.auth_code}</div>}
                {check.host_reference && <div>Banka Ref: {check.host_reference}</div>}
                <div>Sipariş tutarı: {money(check.siparis_tutari)}</div>
                <div>iyzico iade kaydı: {check.iyzico_iade_kaydi > 0
                  ? `${check.iyzico_iade_kaydi} adet · ${money(check.iade_edilen_tutar)}`
                  : "yok"}</div>
                {check.hata_mesaji && <div className="text-red-600">{check.hata_mesaji}</div>}
              </div>
            </>
          ) : (
            <div className="text-gray-600">{check.mesaj}</div>
          )}
          {check.uyari && (
            <div className="mt-2 rounded bg-amber-50 border border-amber-300 text-amber-900 px-2 py-1.5">
              ⚠ {check.uyari}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
