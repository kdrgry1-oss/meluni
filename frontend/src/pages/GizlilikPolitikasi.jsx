import Header from "../components/Header";
import Footer from "../components/Footer";

export default function GizlilikPolitikasi() {
  return (
    <div className="min-h-screen bg-white">
      <Header />
      <main className="max-w-3xl mx-auto px-5 py-16" data-testid="privacy-policy-page">
        <h1 className="text-3xl font-light tracking-wide mb-2">Gizlilik ve Veri Koruma Politikası</h1>
        <p className="text-sm text-gray-400 mb-10">Son güncelleme: 4 Haziran 2026</p>

        <div className="prose prose-sm max-w-none text-gray-700 space-y-6 leading-relaxed">
          <section>
            <h2 className="text-lg font-medium text-gray-900">1. Toplanan Veriler</h2>
            <p>Facette olarak, sipariş ifası ve müşteri hizmetleri amacıyla ad-soyad, iletişim
            (telefon, e-posta), teslimat/fatura adresi ve sipariş bilgilerinizi toplarız.
            Pazaryeri (Amazon, Trendyol vb.) entegrasyonlarında ilgili platformdan gelen
            sipariş ve alıcı bilgileri yalnızca siparişin yerine getirilmesi için işlenir.</p>
          </section>

          <section>
            <h2 className="text-lg font-medium text-gray-900">2. Verilerin İşlenme Amacı</h2>
            <p>Kişisel verileriniz; siparişin oluşturulması, hazırlanması, kargolanması, faturalandırılması,
            iade/iptal süreçleri ve yasal yükümlülüklerin yerine getirilmesi amacıyla işlenir.</p>
          </section>

          <section>
            <h2 className="text-lg font-medium text-gray-900">3. Saklama ve İmha</h2>
            <p>İşlemsel veriler yasal saklama süreleri boyunca tutulur. Pazaryeri kaynaklı kişisel
            tanımlayıcılar (PII), siparişin gönderiminden itibaren <strong>en geç 30 gün</strong>
            içinde otomatik olarak anonimleştirilir/silinir. Manuel imha talepleri de işleme alınır.</p>
          </section>

          <section>
            <h2 className="text-lg font-medium text-gray-900">4. Güvenlik</h2>
            <p>Verileriniz iletimde TLS, durağan halde ise AES şifreleme ile korunur. Hassas kimlik
            bilgileri şifreli bir kasada (vault) saklanır. Erişim, rol bazlı yetkilendirme ve
            çok faktörlü doğrulama (MFA) ile sınırlandırılır; tüm erişimler denetim kayıtlarına işlenir.</p>
          </section>

          <section>
            <h2 className="text-lg font-medium text-gray-900">5. Veri Paylaşımı</h2>
            <p>Kişisel verileriniz, yalnızca siparişin ifası için zorunlu hizmet sağlayıcılarla
            (kargo, ödeme, e-fatura) ve yasal mercilerle paylaşılır. Pazarlama amacıyla
            üçüncü taraflara satılmaz.</p>
          </section>

          <section>
            <h2 className="text-lg font-medium text-gray-900">6. Haklarınız (KVKK)</h2>
            <p>Verilerinize erişim, düzeltme, silme ve işlemeye itiraz haklarına sahipsiniz.
            Talepleriniz için <a href="mailto:kvkk@facette.com.tr" className="underline">kvkk@facette.com.tr</a>
            adresine başvurabilirsiniz.</p>
          </section>

          <section>
            <h2 className="text-lg font-medium text-gray-900">7. İletişim</h2>
            <p>Gizlilik ve güvenlikle ilgili sorular için: <a href="mailto:security@facette.com.tr" className="underline">security@facette.com.tr</a></p>
          </section>
        </div>
      </main>
      <Footer />
    </div>
  );
}
