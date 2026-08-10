/**
 * Footer.jsx — Admin'in `/api/footer-template` üzerinden tam yönetebildiği footer.
 * İki mod desteklenir:
 *   • mode = "html"        → custom_html alanı doğrudan render edilir
 *   • mode = "structured"  → columns + newsletter + social + copyright alanlarından
 *                            otomatik render edilir
 */
import { Link } from "react-router-dom";
import { sanitizeHtml } from "../lib/sanitizeHtml";
import { useEffect, useState } from "react";
import axios from "axios";
import { Instagram, Facebook, Twitter, ChevronDown, ArrowRight, Check, Lock } from "lucide-react";

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;

const SERIF = { fontFamily: 'Georgia, "Times New Roman", "Playfair Display", serif' };

/**
 * NewsletterBand — footer'ın hemen üstünde "Facette Kulübü seni bekliyor" bandı.
 * Metinler admin footer ayarındaki `newsletter` alanından okunur.
 */
// KVKK / ticari-ileti onay metni — kutucuk etiketiyle AYNI; abone kaydına ve İYS'ye
// bu metin işlenir (ne onayladığının kanıtı).
const CONSENT_TEXT =
  "KVKK Aydınlatma Metni'ni okudum; kampanya ve fırsatlar için ticari elektronik ileti (e-posta) almayı kabul ediyorum.";

function NewsletterBand({ nl }) {
  const [email, setEmail] = useState("");
  const [consent, setConsent] = useState(false);
  const [state, setState] = useState("idle"); // idle | loading | done | error
  const [msg, setMsg] = useState("");

  const title = nl?.title || "Facette Kulübü seni bekliyor";
  const description =
    nl?.description ||
    "Yeni koleksiyonlar, özel kampanyalar ve sana özel fırsatlardan ilk sen haberdar ol.";
  const placeholder = nl?.placeholder || "E-posta adresin";

  const submit = async (e) => {
    e.preventDefault();
    const v = (email || "").trim();
    if (!v || !/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(v)) {
      setState("error");
      setMsg("Lütfen geçerli bir e-posta adresi girin.");
      return;
    }
    if (!consent) {
      setState("error");
      setMsg("Devam etmek için KVKK / ticari ileti onayını işaretlemelisin.");
      return;
    }
    setState("loading");
    try {
      const r = await axios.post(`${API}/newsletter/subscribe`, {
        email: v, source: "footer", consent: true, consent_text: CONSENT_TEXT,
      });
      setState("done");
      setMsg(r?.data?.message || "Aramıza hoş geldin!");
      setEmail("");
      setConsent(false);
    } catch (err) {
      setState("error");
      setMsg(err?.response?.data?.detail || "Bir sorun oluştu, tekrar dene.");
    }
  };

  return (
    <section className="bg-neutral-100 border-t border-neutral-200" data-testid="newsletter-band">
      <div className="container-main py-14 md:py-20">
        <div className="max-w-2xl mx-auto text-center">
          <p className="text-[11px] tracking-[0.35em] uppercase text-neutral-700 mb-4">Facette Kulübü</p>
          <h3 className="text-3xl md:text-5xl font-light tracking-tight text-black leading-none">
            {title}
          </h3>
          <p className="text-sm md:text-base text-neutral-900 mt-4 leading-relaxed">{description}</p>

          {state === "done" ? (
            <div className="mt-8 inline-flex items-center gap-2 text-sm text-neutral-900" data-testid="newsletter-done">
              <span className="w-6 h-6 rounded-full bg-neutral-900 text-white flex items-center justify-center">
                <Check size={13} strokeWidth={2.5} />
              </span>
              {msg}
            </div>
          ) : (
            <form onSubmit={submit} className="mt-8 max-w-md mx-auto" data-testid="newsletter-form">
              <div className="flex items-stretch border-b border-neutral-400 focus-within:border-neutral-900 transition-colors">
                <input
                  type="email"
                  value={email}
                  onChange={(e) => { setEmail(e.target.value); if (state === "error") setState("idle"); }}
                  placeholder={placeholder}
                  className="flex-1 bg-transparent px-1 py-3 text-sm text-neutral-900 placeholder-neutral-400 outline-none"
                  aria-label="E-posta adresi"
                  data-testid="newsletter-email"
                />
                <button
                  type="submit"
                  disabled={state === "loading"}
                  className="px-2 text-neutral-900 hover:opacity-60 disabled:opacity-40 transition-opacity"
                  aria-label="Abone ol"
                  data-testid="newsletter-submit"
                >
                  <ArrowRight size={20} strokeWidth={1.5} />
                </button>
              </div>
              {state === "error" && (
                <p className="text-xs text-red-500 mt-3 text-left" data-testid="newsletter-error">{msg}</p>
              )}
              {/* KVKK / ticari-ileti onayı — ZORUNLU kutucuk (İYS'ye 'ONAY' olarak işlenir) */}
              <label className="flex items-start gap-2 mt-4 text-left text-[12px] text-neutral-700 leading-snug cursor-pointer">
                <input
                  type="checkbox"
                  checked={consent}
                  onChange={(e) => { setConsent(e.target.checked); if (state === "error") setState("idle"); }}
                  className="mt-0.5 w-4 h-4 accent-black flex-shrink-0"
                  aria-label="KVKK ve ticari ileti onayı"
                  data-testid="newsletter-consent"
                />
                <span>
                  <Link to="/sayfa/kvkk" className="underline hover:text-black">KVKK Aydınlatma Metni</Link>'ni okudum;
                  kampanya ve fırsatlar için ticari elektronik ileti (e-posta) almayı kabul ediyorum.
                </span>
              </label>
            </form>
          )}
        </div>
      </div>
    </section>
  );
}

const DEFAULT_COLUMNS = [
  { title: "Alışveriş", links: [
    { to: "/kategori/en-yeniler", label: "En Yeniler" },
    { to: "/kategori/elbise", label: "Elbise" },
    { to: "/kategori/pantolon", label: "Pantolon" },
    { to: "/kategori/ceket", label: "Ceket" },
    { to: "/kategori/aksesuar", label: "Aksesuar" },
  ]},
  { title: "Yardım", links: [
    { to: "/siparis-takip", label: "Sipariş Takibi" },
    { to: "/sayfa/uyelik-islemleri", label: "Üyelik İşlemleri" },
    { to: "/iade-islemleri", label: "İade Talebi" },
    { to: "/sayfa/iade-kosullari", label: "İade İşlemleri" },
    { to: "/sikca-sorulan-sorular", label: "Sıkça Sorulan Sorular" },
    { to: "/sayfa/iletisim", label: "İletişim" },
  ]},
  { title: "Kurumsal", links: [
    { to: "/sayfa/hakkimizda", label: "Hakkımızda" },
    { to: "/sayfa/mesafeli-satis", label: "Mesafeli Satış Sözleşmesi" },
    { to: "/sayfa/kvkk", label: "KVKK Aydınlatma Metni" },
    { to: "/sayfa/gizlilik", label: "Gizlilik Politikası" },
  ]},
  { title: "İletişim", static: [
    "info@facette.com.tr", "+90 543 330 03 10", "Pazartesi-Cumartesi 09:00 - 18:00",
  ]},
];

function FooterColumn({ col, defaultOpen = false }) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className="border-b border-white/10 md:border-b-0">
      <button
        type="button"
        className="w-full flex items-center justify-between py-4 md:py-0 md:cursor-default md:pointer-events-none"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        data-testid={`footer-col-toggle-${col.title}`}
      >
        <h4 className="text-[10px] tracking-[0.3em] uppercase text-white">{col.title}</h4>
        <ChevronDown size={14} className={`md:hidden transition-transform duration-300 ${open ? "rotate-180" : ""}`} />
      </button>
      <ul
        className={`grid transition-all duration-300 ease-out overflow-hidden md:!grid-rows-[1fr] md:!opacity-100 md:mt-5 ${
          open ? "grid-rows-[1fr] opacity-100 pb-4" : "grid-rows-[0fr] opacity-0"
        }`}
      >
        <li className="min-h-0">
          <ul className="space-y-3">
            {col.links?.map((l) => (
              <li key={l.to}>
                <Link to={l.to} className="text-xs text-white/55 hover:text-white transition-colors duration-300">
                  {l.label}
                </Link>
              </li>
            ))}
            {col.static?.map((s, i) => (
              <li key={i} className="text-xs text-white/55">{s}</li>
            ))}
          </ul>
        </li>
      </ul>
    </div>
  );
}

export default function Footer() {
  const [tpl, setTpl] = useState(null);

  useEffect(() => {
    let cancel = false;
    axios.get(`${API}/footer-template`)
      .then((r) => { if (!cancel) setTpl(r.data); })
      .catch(() => { if (!cancel) setTpl(null); });
    return () => { cancel = true; };
  }, []);

  const newsletter = tpl?.newsletter;

  // HTML mode — admin tam serbest HTML yazdı
  if (tpl?.mode === "html" && tpl?.custom_html) {
    return (
      <>
        <NewsletterBand nl={newsletter} />
        <footer className="bg-black text-white" data-testid="footer-html">
          <div dangerouslySetInnerHTML={{ __html: sanitizeHtml(tpl.custom_html) }} />
        </footer>
      </>
    );
  }

  // Structured mode — admin sütunları/sosyal/copyright güncelledi
  let columns = tpl?.columns || DEFAULT_COLUMNS;
  // "İade Talebi" linki her zaman görünür olsun (admin sütunları override etse bile)
  try {
    const hasReturn = columns.some((c) => (c.links || []).some((l) => l.to === "/iade-islemleri"));
    if (!hasReturn) {
      columns = columns.map((c) => c.links ? c : c); // shallow copy tetikleyici
      const svc = columns.find((c) => /müşteri|hizmet|iade|customer/i.test(c.title || ""));
      if (svc && svc.links) {
        svc.links = [...svc.links, { to: "/iade-islemleri", label: "İade Talebi" }];
      } else {
        columns = [...columns, { title: "İade İşlemleri", links: [{ to: "/iade-islemleri", label: "İade Talebi" }] }];
      }
    }
  } catch (_) { /* yoksay */ }
  // Sosyal linkler footer ayarından gelir; girilmemişse markanın varsayılan hesaplarına düşer.
  const _socialCfg = tpl?.social || {};
  const social = {
    instagram: _socialCfg.instagram || "https://instagram.com/facette",
    tiktok: _socialCfg.tiktok || "https://www.tiktok.com/@facetteofficial",
    facebook: _socialCfg.facebook || "https://www.facebook.com/faceette",
    twitter: _socialCfg.twitter || "",
  };
  const copyright = tpl?.copyright || `© ${new Date().getFullYear()} Facette – Tüm hakları saklıdır.`;

  return (
    <>
    <NewsletterBand nl={newsletter} />
    <footer className="bg-black text-white" data-testid="footer-structured">
      <div className="container-main pt-10 md:pt-12 pb-8">
        {/* Brand strip — logo/slogan/ikon bloğu; üstte modest boşluk (çift padding kaldırıldı,
            footer dikey olarak kısaldı). 'inspired...' alt hizası ikonlarla eşit (items-end + leading-none) */}
        <div className="md:flex md:items-end md:justify-between mb-10 md:mb-12 pt-2 md:pt-4">
          <div className="max-w-md">
            <Link to="/" className="inline-block mb-5">
              {/* Koyu footer → logo beyaza zorlanır (brightness-0 + invert) */}
              <img src="/logo.webp" alt="FACETTE" className="h-6 md:h-7 w-auto brightness-0 invert" />
            </Link>
            <p className="text-sm text-white/60 leading-none italic">
              inspired by who you are.
            </p>
          </div>
          <div className="flex gap-7 mt-8 md:mt-0">
            {social.instagram && (
              <a href={social.instagram} target="_blank" rel="noreferrer noopener" className="text-white hover:text-white/70 transition-colors" aria-label="Instagram">
                <Instagram size={24} strokeWidth={2} />
              </a>
            )}
            {social.tiktok && (
              <a href={social.tiktok} target="_blank" rel="noreferrer noopener" className="text-white hover:text-white/70 transition-colors" aria-label="TikTok">
                {/* lucide'da TikTok yok → inline SVG */}
                <svg width="24" height="24" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
                  <path d="M16.5 3c.29 2.02 1.45 3.42 3.5 3.6v2.36c-1.18.11-2.21-.27-3.41-1v4.9c0 4.4-4.8 7.18-8.63 4.68-2.45-1.6-2.9-5.02-.85-7.16 1.2-1.27 3.02-1.78 4.89-1.31v2.5c-.4-.12-.86-.16-1.34-.05-1.05.22-1.85 1.14-1.74 2.31.13 1.5 1.9 2.28 3.1 1.34.66-.5.9-1.2.9-2.02V3h3.63z"/>
                </svg>
              </a>
            )}
            {social.facebook && (
              <a href={social.facebook} target="_blank" rel="noreferrer noopener" className="text-white hover:text-white/70 transition-colors" aria-label="Facebook">
                <Facebook size={24} strokeWidth={2} />
              </a>
            )}
            {social.twitter && (
              <a href={social.twitter} target="_blank" rel="noreferrer noopener" className="text-white hover:text-white/70 transition-colors" aria-label="Twitter">
                <Twitter size={24} strokeWidth={2} />
              </a>
            )}
          </div>
        </div>

        {/* Columns */}
        <div className="grid md:grid-cols-4 gap-x-10 md:border-t md:border-white/10 md:pt-12">
          {columns.map((col, i) => (
            <FooterColumn key={col.title || i} col={col} defaultOpen={i === 0} />
          ))}
        </div>

        {/* Bottom — mobilde üst çizgi YOK (son sütunun alt çizgisiyle çift çizgi olmasın); masaüstünde var */}
        <div className="md:border-t md:border-white/10 mt-6 md:mt-12 pt-8 flex flex-col-reverse md:flex-row justify-between items-center gap-6">
          <p className="text-[10px] tracking-[0.2em] uppercase text-white/40">{copyright}</p>
          {/* Ödeme: kilit + iyzico güvenli ödeme etiketi + beyaz kart rozetleri (self-contained) */}
          <div className="flex items-center gap-4 flex-wrap justify-center md:justify-end">
            <span className="flex items-center gap-1.5 text-[10px] tracking-[0.18em] uppercase text-white/55 whitespace-nowrap">
              <Lock size={13} strokeWidth={2} /> iyzico ile güvenli ödeme
            </span>
            <div className="flex items-center gap-2">
              {/* VISA */}
              <span className="h-7 px-2.5 rounded-md border border-white/25 bg-white/[0.06] flex items-center text-white text-[13px] font-extrabold italic tracking-wide" aria-label="Visa">VISA</span>
              {/* Mastercard: iki halka + 'mastercard' */}
              <span className="h-7 pl-1.5 pr-2 rounded-md border border-white/25 bg-white/[0.06] flex items-center gap-1" aria-label="Mastercard">
                <svg width="22" height="14" viewBox="0 0 22 14" aria-hidden="true">
                  <circle cx="8" cy="7" r="6" fill="#fff" />
                  <circle cx="14" cy="7" r="6" fill="#fff" fillOpacity="0.5" />
                </svg>
                <span className="text-white text-[9px] font-semibold lowercase tracking-tight">mastercard</span>
              </span>
              {/* American Express — beyaz 'kutu' logosu */}
              <span className="h-7 px-2 rounded-md border border-white bg-white/[0.06] flex flex-col items-center justify-center leading-[1.05]" aria-label="American Express">
                <span className="text-white text-[7px] font-extrabold tracking-[0.06em]">AMERICAN</span>
                <span className="text-white text-[7px] font-extrabold tracking-[0.06em]">EXPRESS</span>
              </span>
              {/* Troy — beyaz wordmark */}
              <span className="h-7 px-2.5 rounded-md border border-white/25 bg-white/[0.06] flex items-center text-white text-[14px] font-black lowercase tracking-tighter" aria-label="Troy">troy</span>
            </div>
          </div>
        </div>
      </div>
    </footer>
    </>
  );
}
