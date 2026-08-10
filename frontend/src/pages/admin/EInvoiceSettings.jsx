/**
 * EInvoiceSettings.jsx — E-Arşiv / E-Fatura Ayarları sayfası.
 *
 * Yöneticinin kullandığı e-fatura entegratörünü seçip credential'larını
 * girmesi için `/admin/ayarlar/e-fatura` rotasında render edilir. Jenerik
 * ProviderSettings componentini "einvoice" modunda çağırır.
 *
 * Desteklenen entegratörler (backend provider_settings.py içinde tanımlı):
 *   Doğan e-Dönüşüm, Nilvera, Uyumsoft, Logo, Mikro, Foriba (EDM),
 *   QNB Finansbank e-Finans, Turkcell e-Şirket, İzibiz, İdea, Kolaysoft.
 *
 * Bağlantılı:
 *   - components/admin/ProviderSettings.jsx (ana UI)
 *   - backend/routes/provider_settings.py (GET/POST /api/provider-settings/einvoice/*)
 */
import ProviderSettings from "../../components/admin/ProviderSettings";

export default function EInvoiceSettings() {
  return (
    <ProviderSettings
      kind="einvoice"
      title="E-Arşiv / E-Fatura Ayarları"
      subtitle="Kullanacağınız fatura entegratörünü seçin ve yalnızca o sağlayıcıya ait bilgileri girin. Seçtiğiniz sağlayıcı sistem genelinde aktif olarak kullanılır."
    />
  );
}
