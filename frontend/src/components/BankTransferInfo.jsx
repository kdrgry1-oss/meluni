import { useState, useEffect } from "react";
import { Link } from "react-router-dom";
import { Copy, Check, Building2, Upload } from "lucide-react";
import { toast } from "sonner";
import axios from "axios";

/**
 * BankTransferInfo — Havale/EFT ile ödenecek siparişlerde müşteriye gösterilen
 * KOPYALANABİLİR banka hesap bilgileri kartı + "Ödeme Bildirimi Yap" butonu.
 *
 * BEYAZ ETİKET: Banka bilgisi KODA GÖMÜLÜ DEĞİL — /settings/public/bank-default
 * (admin > Ödeme Ayarları > varsayılan banka) uçundan okunur. Böylece yeni firma
 * yalnız ayardan kendi IBAN'ını girer; kod değişmez. Ayar boşsa kart gizlenir
 * (yanlış/başka firmanın hesabı ASLA gösterilmez).
 *
 * Props: orderNumber (string)
 */

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;

function CopyRow({ label, value, mono = false, strong = false }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      toast.success(`${label} kopyalandı`);
      setTimeout(() => setCopied(false), 1600);
    } catch (_) {
      toast.error("Kopyalanamadı");
    }
  };
  return (
    <div className="flex items-center justify-between gap-3 py-2.5 border-b border-gray-100 last:border-0">
      <div className="min-w-0">
        <p className="text-[10px] tracking-[0.15em] text-gray-500 uppercase">{label}</p>
        <p className={`text-black break-all ${mono ? "font-mono" : ""} ${strong ? "text-base font-medium tracking-wide" : "text-sm"}`}>
          {value}
        </p>
      </div>
      <button
        type="button"
        onClick={copy}
        aria-label={`${label} kopyala`}
        className="flex-shrink-0 w-9 h-9 border border-gray-300 flex items-center justify-center hover:bg-black hover:text-white hover:border-black transition-colors"
      >
        {copied ? <Check size={15} /> : <Copy size={15} />}
      </button>
    </div>
  );
}

export default function BankTransferInfo({ orderNumber }) {
  const [bank, setBank] = useState(null);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    let alive = true;
    axios
      .get(`${API}/settings/public/bank-default`, { timeout: 8000 })
      .then((r) => { if (alive) setBank(r?.data?.bank || null); })
      .catch(() => {})
      .finally(() => { if (alive) setLoaded(true); });
    return () => { alive = false; };
  }, []);

  // Ayar henüz yüklenmedi → sabit yükseklikli boşluk (layout kaymasın)
  if (!loaded) {
    return <div className="mb-12" style={{ minHeight: 120 }} data-testid="bank-transfer-loading" aria-hidden="true" />;
  }

  // Banka ayarlanmamış → kartı hiç gösterme (yanlış hesap riski yok).
  if (!bank || !bank.iban) return null;

  const bankLine = [bank.bank_name, bank.branch].filter(Boolean).join(" · ");

  return (
    <div className="border border-black/80 mb-12" data-testid="bank-transfer-info">
      <div className="bg-black text-white px-5 sm:px-8 py-4 flex items-center gap-2.5">
        <Building2 size={18} strokeWidth={1.6} />
        <div>
          <p className="text-sm font-medium tracking-wide">Havale / EFT ile Ödeme</p>
          <p className="text-[11px] text-white/70">
            Ödemenizi aşağıdaki hesaba yapın; havale onaylanınca siparişiniz hazırlanır.
          </p>
        </div>
      </div>

      <div className="px-5 sm:px-8 py-4">
        {bank.account_holder && <CopyRow label="Alıcı" value={bank.account_holder} />}
        {bankLine && <CopyRow label="Banka" value={bankLine} />}
        <CopyRow label="IBAN" value={bank.iban} mono strong />
        {orderNumber && (
          <CopyRow label="Açıklama (sipariş no)" value={orderNumber} mono />
        )}

        {/* Ödeme yaptıysanız → dekont yükleme (ödeme bildirimi) sayfası */}
        {orderNumber && (
          <Link
            to={`/odeme-bildirimi/${orderNumber}`}
            className="mt-4 w-full h-11 bg-black text-white text-xs tracking-[0.2em] uppercase flex items-center justify-center gap-2 hover:opacity-90 transition-opacity"
            data-testid="payment-notify-btn"
          >
            <Upload size={15} />
            Ödeme Yaptıysanız Ödeme Bildirimi Yapın
          </Link>
        )}

        <p className="mt-3 text-[11px] text-gray-500 leading-relaxed">
          <strong className="text-gray-700">Önemli:</strong> Havale/EFT açıklamasına
          mutlaka <strong className="text-black">sipariş numaranızı</strong> yazın
          {orderNumber ? ` (${orderNumber})` : ""}. Ödemeniz onaylandığında sipariş
          durumunuz güncellenir ve size bilgi verilir.
        </p>
      </div>
    </div>
  );
}
