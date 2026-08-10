/**
 * FAQ.jsx — Sıkça Sorulan Sorular (sekmeli + akordeon).
 * Sitenin fontu miras alınır (font-inherit). Sorular/önemli alanlar kalın, cevaplar ince.
 * İçerik kullanıcıdan geldiği için cevaplar HTML olarak (dangerouslySetInnerHTML) render edilir.
 */
import { useState, useEffect } from "react";
import axios from "axios";
import { sanitizeHtml } from "../lib/sanitizeHtml";
import Header from "../components/Header";
import Footer from "../components/Footer";

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;

// Admin "Sayfalar > Sıkça Sorulan Sorular" (slug: sss) içeriği facetteFaq yapısındadır.
// Bunu ayrıştırıp sekmeli akordeon verisine çeviririz → SSS ARTIK PANELDEN DÜZENLENEBİLİR.
// Ayrıştırma başarısızsa aşağıdaki gömülü FAQ_DATA yedeğe düşer.
function parseFaqHtml(html) {
  try {
    if (!html || html.indexOf("facetteFaq") === -1) return null;
    const doc = new DOMParser().parseFromString(html, "text/html");
    const tabs = Array.from(doc.querySelectorAll(".facetteFaqTab"));
    if (!tabs.length) return null;
    const out = [];
    tabs.forEach((tab, ti) => {
      const target = (tab.getAttribute("data-target") || "").replace("#", "");
      const label = (tab.textContent || "").trim();
      const panel = target ? doc.getElementById(target) : null;
      const items = [];
      if (panel) {
        panel.querySelectorAll(".facetteFaqQ").forEach((q) => {
          const qc = q.cloneNode(true);
          qc.querySelectorAll(".facetteFaqIcon").forEach((s) => s.remove());
          let a = q.nextElementSibling;
          while (a && !(a.classList && a.classList.contains("facetteFaqA"))) a = a.nextElementSibling;
          items.push({ q: (qc.textContent || "").trim(), a: a ? a.innerHTML : "" });
        });
      }
      if (items.length) out.push({ id: target || `t${ti}`, label, items });
    });
    return out.length ? out : null;
  } catch { return null; }
}

const FAQ_DATA = [
  {
    id: "uyelik",
    label: "ÜYELİK",
    items: [
      { q: "Sipariş vermek için üye olmam gerekiyor mu?", a: 'Facette web sitesinden sipariş verebilmek için birkaç dakikanızı alacak bir üyelik işlemi gerçekleştirebilirsiniz.<br/><br/>Üyelik gerçekleştirmek istemiyorsanız <b>"Üyeliksiz Devam Et"</b> seçeneği ile sipariş oluşturabilirsiniz.' },
      { q: "Neden üye olmalıyım?", a: "Sipariş verebilmeniz ve vermiş olduğunuz siparişlerin durumu hakkında bilgi edinmek, kampanyalardan haberdar olabilmek, size özel sunulan kampanya ve avantajlardan faydalanabilmek için üye olmanız gerekmektedir." },
      { q: "Üyelik bilgilerimi nasıl güncelleyebilirim?", a: 'Kullanıcı adınız ve şifreniz ile üye girişi yaptıktan sonra <b>"Hesabım"</b> bölümünden üyelik bilgilerinizi güncelleyebilirsiniz.' },
      { q: "Şifremi unuttum, ne yapmalıyım?", a: 'Şifrenizi unuttuğunuzda üye girişi bölümünde bulunan <b>"Şifremi Unuttum"</b> linkine tıklamanız ve karşınıza gelen talimatlara uymanız yeterli olacaktır.' },
    ],
  },
  {
    id: "online",
    label: "ONLINE ALIŞVERİŞ",
    items: [
      { q: "Siparişimin onaylandığını nasıl anlayabilirim?", a: "Siparişiniz alındığında başarıyla gerçekleştiğine dair, kayıtlı e-posta adresinize sipariş bilgileriniz gönderilmektedir." },
      { q: "Siparişim onaylandıktan sonra değişiklik yapabilir miyim?", a: "Ne yazık ki siparişiniz onaylandıktan sonra siparişinizin bir kısmı ya da tamamı ile ilgili değişiklik yapamamaktayız.<br/><br/>Ancak siparişinizi iptal edip, yeni sipariş verebilirsiniz." },
    ],
  },
  {
    id: "odeme",
    label: "ÖDEME",
    items: [
      { q: "Aldığım ürünlerin fiyatlarına KDV dâhil midir?", a: "Facette sitesi üzerinden satılan her ürünün fiyatına KDV dahildir." },
      { q: "Havale yoluyla ödeme yapabiliyor muyum?", a: "Havale/EFT ile ödeme yapabilirsiniz." },
      { q: "Taksit seçeneğiniz var mı?", a: "ATM/Debit Kart veya kredi kartı bilgilerini eksiksiz doldurduktan sonra, bankanızın sunduğu taksit seçeneklerini ödeme ekranında görüntüleyebilir, istediğiniz taksit tutarını seçerek taksitli ödemenizi gerçekleştirebilirsiniz." },
      { q: "Kredi kartı detaylarım güvende midir?", a: 'Facette.com.tr, veri iletiminde 128 bit şifreleme ile iletilen bilgilerin güvenliğini sağlayan SSL sertifikası kullanmaktadır. Kredi kartı bilgileriniz yalnızca sipariş işlemi sırasında kullanılır ve veri tabanında kayıtlı olarak tutulmaz.<br/><br/>Sitemizde gerçekleşen tüm alışveriş işlemleri 3Dsecure özelliği zorunlu halde gerçekleşmektedir.<br/><br/>Hesabınıza izinsiz erişimi engellemek için şifrenizi ve hesap detaylarınızı lütfen başkalarıyla paylaşmayınız.<br/><br/><b>Sorun yaşarsanız:</b> Ödeme işlemlerinde yaşayacağınız olası sorunlarda kart numaranızın tamamını paylaşmayınız. Sadece sipariş numaranızı iletmeniz yeterlidir ya da bankanızın müşteri hizmetlerinden bilgi alabilirsiniz.' },
    ],
  },
  {
    id: "kargo",
    label: "KARGO VE TESLİMAT",
    items: [
      { q: "Hangi kargo firması ile çalışıyorsunuz?", a: "İnternet mağazamızdan oluşturduğunuz siparişleriniz, size DHL Kargo güvencesiyle ulaştırılmaktadır." },
      { q: "Yurt dışına veya KKTC'ye sipariş gönderimi yapılıyor mu?", a: "Yurt dışına gönderiler için lütfen WhatsApp hattımızdan bizlere ulaşınız." },
      { q: "Resmi tatillerde teslimat yapılıyor mu?", a: "Resmi tatillerde DHL Kargo teslimat yapmamaktadır." },
      { q: "DHL Kargo haricinde başka kargo şirketi ile gönderim yapabiliyor musunuz?", a: "DHL Kargo ile çalışmaktayız; başka kargo şirketi ile gönderim yapılmamaktadır." },
      { q: "Sipariş teslimatında adreste yoksam ne oluyor?", a: "Siparişiniz adresinizde bulunmadığınız için ulaştırılamadıysa DHL Kargo belirtilen adrese ikinci bir uğrama yapmamaktadır. Bu durumda kargonuzun bulunduğu şubeyi ziyaret ederek kargonuzu teslim almanız gerekmektedir." },
      { q: "Paket hasarlı geldi, teslim almalı mıyım?", a: "Hasarlı gelen paketleri teslim almayıp, kargo görevlisine paketin hasarlı olduğuna dair tutanak tutturmanız gerekmektedir. Daha sonra siparişinizle ilgili müşteri hizmetlerimizle iletişime geçebilirsiniz." },
    ],
  },
  {
    id: "iade",
    label: "İADE - DEĞİŞİM",
    items: [
      { q: "Ürünlerde iade süresi ne kadar?", a: 'İade işleminizi başlatmadan önce, ad-soyad bilgilerinizi belirterek <b>+90 (543) 330 03 10</b> WhatsApp destek hattımız üzerinden yazılı olarak iade kodunuzu talep etmeniz gerekmektedir.<br/><br/>Facette.com.tr web sitemizden aldığınız ürünlerin <b>kullanılmamış</b> ve <b>deforme olmamış</b> olması şartıyla iade süresi fatura tarihinden itibaren <b>14 gündür</b>.<br/><br/>Hijyen koşulları gereği iç giyim, küpe ve kozmetik ürünlerinde iade yapılmamaktadır.' },
      { q: "İade işleminde kargo ücreti ödeyecek miyim?", a: "1) Satın alınmış ürün ayıplı/defolu/yanlış şekilde ulaştıysa, anlaşmalı kargo firmamızı kullanmanız halinde kargo ücretini biz karşılıyoruz. Farklı kargo firması ile gönderimlerde kargo ücretini karşılamamaktayız.<br/><br/>2) Cayma hakkı kapsamında iade etmek isterseniz, iade koşulları sağlandığında anlaşmalı kargomuz ile göndermeniz durumunda kargo ücretini biz karşılıyoruz." },
      { q: "Siparişim ayıplı/defolu/yanlış gönderilmişse ne yapmalıyım?", a: "Tüm ürünler kargoya teslim edilmeden önce hasar kontrolünden geçmektedir. Nadiren ayıplı/defolu/yanlış ürün ulaştıysa süreç şu şekilde ilerler:<br/><br/>Ürünü, fatura üzerinde bulunan adresimize anlaşmalı kargo firmamız <b>DHL Kargo</b> aracılığı ile <b>Müşteri Adı: Facette Dış Ticaret A.Ş.</b> bilgilerini belirterek göndermeniz gerekmektedir." },
      { q: "Kargo paketinin dışı deforme olmuş ve ürünler zarar görmüşse ne yapmalıyım?", a: 'Kargo tesliminde ürünü almadan önce dış pakette hasar kontrolü yapın ve hasar varsa mutlaka <b>"Durum Tespit Tutanağı"</b> hazırlatın.<br/><br/>Teslim sonrası fark edilen hasarda ilgili kargo şubesi ile hemen iletişime geçerek tutanak düzenlenmesini talep edin. Yardım alamıyorsanız en kısa sürede <b>+90 543 330 03 10</b> numaramızdan bizi bilgilendirin.<br/><br/>Hasar tespit tutanağı ile ürünü adresimize gönderdiğinizde değişim ya da iade işlemleriniz hızla tamamlanır.' },
      { q: "Ürün iadesi yaptığımda ödediğim tutar bana nasıl iade edilecek?", a: "İade paketiniz ekibimiz tarafından kontrol edildikten sonra iade durumunuzu web sitemiz üzerinden takip edebilirsiniz.<br/><br/>• Kredi kartı: Tek çekim/ taksitli işlemler, işlem tipine uygun şekilde <b>7 iş günü</b> içinde kartınıza iade edilir.<br/>• Debit kart: Tek çekim işlem <b>7 iş günü</b> içinde banka hesabınıza iade edilir.<br/>• Sanal kart: Tek çekim işlem <b>7 iş günü</b> içinde kartınıza iade edilir.<br/>• Karşı ödemeli gönderimler, iade edilecek bakiyeden düşülür." },
      { q: "Ürün iadesi hesabıma neden geçmedi?", a: "İade sürecinde yaşanabilecek gecikmeler için WhatsApp destek hattımız <b>+90 543 330 03 10</b> ile iletişime geçebilirsiniz.<br/><br/>* Tüm iade ve değişim işlemlerinde fatura ibrazı zorunludur.<br/>* İade gönderimlerinde anlaşmalı kargo firmamız ile gönderim yapmanız gerekmektedir; diğer kargo firmaları ile gönderilen kargoların ücretleri firmamız tarafından karşılanmamaktadır." },
    ],
  },
];

export default function FAQ() {
  const [data, setData] = useState(FAQ_DATA);          // panel içeriği gelene kadar gömülü yedek
  const [activeTab, setActiveTab] = useState(FAQ_DATA[0].id);
  const [openKey, setOpenKey] = useState(null); // `${tabId}:${index}` — panel başına tek açık

  // Panelden (Sayfalar > SSS) düzenlenen içeriği çek → ayrıştır → kullan.
  useEffect(() => {
    let alive = true;
    axios.get(`${API}/pages/sss`)
      .then((r) => {
        const parsed = parseFaqHtml(r?.data?.content || "");
        if (alive && parsed && parsed.length) { setData(parsed); setActiveTab(parsed[0].id); }
      })
      .catch(() => { /* yedek gömülü veri kalır */ });
    return () => { alive = false; };
  }, []);

  const panel = data.find((t) => t.id === activeTab) || data[0];

  return (
    <div className="min-h-screen bg-white flex flex-col">
      <Header />
      <main className="flex-1 max-w-[1200px] w-full mx-auto px-3 md:px-4 pt-8 pb-24">
        <h1 className="text-center text-[22px] md:text-[28px] font-bold mb-6 md:mb-8">Sıkça Sorulan Sorular</h1>

        {/* Sekmeler */}
        <div role="tablist" aria-label="SSS Kategorileri" className="flex flex-wrap justify-center gap-2.5 md:gap-3.5">
          {data.map((t) => {
            const active = t.id === activeTab;
            return (
              <button
                key={t.id}
                role="tab"
                aria-selected={active}
                onClick={() => { setActiveTab(t.id); setOpenKey(null); }}
                className={`rounded-md px-3.5 py-3.5 md:px-5 md:py-4 min-w-[140px] md:min-w-[160px] text-xs md:text-sm font-bold tracking-wide transition-all bg-white ${
                  active ? "border border-black shadow-[0_0_0_1px_#111_inset]" : "border border-gray-200 hover:border-gray-400 hover:-translate-y-px"
                }`}
                type="button"
              >
                {t.label}
              </button>
            );
          })}
        </div>

        {/* Panel */}
        <div className="mt-5 md:mt-6">
          <div className="space-y-2.5">
            {panel.items.map((item, i) => {
              const key = `${panel.id}:${i}`;
              const open = openKey === key;
              return (
                <div key={key}>
                  <button
                    type="button"
                    onClick={() => setOpenKey(open ? null : key)}
                    className={`w-full text-left bg-white border border-gray-200 rounded-md px-4 py-4 flex items-center justify-between gap-3 font-semibold text-sm md:text-[15px] ${open ? "rounded-b-none" : ""}`}
                  >
                    <span>{item.q}</span>
                    <span className={`text-xl leading-none transition-transform ${open ? "rotate-90" : ""}`}>›</span>
                  </button>
                  {open && (
                    <div
                      className="bg-white border border-t-0 border-gray-200 rounded-b-md px-4 py-4 text-sm text-gray-700 font-normal leading-relaxed [&_b]:font-semibold [&_b]:text-black"
                      dangerouslySetInnerHTML={{ __html: sanitizeHtml(item.a) }}
                    />
                  )}
                </div>
              );
            })}
          </div>
        </div>
      </main>
      <Footer />
    </div>
  );
}
