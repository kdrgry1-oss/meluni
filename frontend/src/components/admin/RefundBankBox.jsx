import { Copy } from "lucide-react";
import { toast } from "sonner";

/* Havale/EFT iadesinde müşterinin girdiği banka bilgisi (IBAN + ad soyad + banka).
   IBAN ve ad soyad AYRI AYRI kopyalanabilir (Kadir isteği: bankaya yapıştırırken tek tık). */
export default function RefundBankBox({ info }) {
  if (!info?.iban) return null;
  const copy = (val, label) => {
    try {
      navigator.clipboard.writeText(String(val || ""));
      toast.success(`${label} kopyalandı`);
    } catch (e) {
      toast.error("Kopyalanamadı");
    }
  };
  const Btn = ({ val, label, testId }) => (
    <button type="button" onClick={(e) => { e.stopPropagation(); copy(val, label); }}
      className="shrink-0 inline-flex items-center gap-0.5 px-1 py-0.5 rounded border border-amber-300 bg-white text-amber-800 hover:bg-amber-100 text-[10px]"
      title={`${label} kopyala`} data-testid={testId}>
      <Copy size={10} /> Kopyala
    </button>
  );
  return (
    <div className="mt-1 text-[11px] leading-snug bg-amber-50 border border-amber-200 rounded px-1.5 py-1 text-amber-900"
         title={`IBAN: ${info.iban}${info.name ? " · " + info.name : ""}${info.bank ? " · " + info.bank : ""}`}>
      <div className="flex items-start gap-1.5">
        <span className="font-mono font-semibold break-all flex-1">{info.iban}</span>
        <Btn val={info.iban} label="IBAN" testId="copy-iban" />
      </div>
      <div className="flex items-start gap-1.5 mt-0.5">
        <span className="flex-1 truncate">{info.name || "—"}{info.bank ? ` · ${info.bank}` : ""}</span>
        {info.name && <Btn val={info.name} label="Ad soyad" testId="copy-iban-name" />}
      </div>
    </div>
  );
}
