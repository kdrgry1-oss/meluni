import { useMemo } from "react";
import { sanitizeHtml } from "../lib/sanitizeHtml";
import {
  CalendarDays, Package, Truck, RefreshCw, Headphones, MapPin, Phone,
  Mail, Clock, Building2, FileText, ShieldCheck, HelpCircle, Info,
} from "lucide-react";

/**
 * IconDocPage — CMS içeriğini (title + h3 bölümleri) ikon-kart düzeninde render eder.
 * =====================================================================================
 * KURAL: içerik TAMAMEN CMS'ten gelir (Sayfalar → içerik). Bu bileşen yalnız SUNUM'dur:
 * her <h3> bir bölüm başlığı olur, başlıktaki anahtar kelimeye göre bir çizgi-ikon
 * eşlenir, altındaki metin (p/ul/li/strong/a) olduğu gibi gösterilir. Böylece kullanıcı
 * paneldan metni düzenler; ikonlar/dizilim koda gömülü değil, başlıktan türetilir.
 *
 * Fontlar sitenin kendi fontu (sans) — görseldeki serif başlık DEĞİL.
 */

// Başlık anahtar kelimesi → ikon eşlemesi (Türkçe, küçük harfe indirgenmiş metinde aranır).
const ICON_RULES = [
  [/cayma|iade süre|gün içinde|süre/i, CalendarDays],
  [/koşul|kosul|şart|sart/i, Package],
  [/süreç|surec|kargo|gönder|gonder|teslim/i, Truck],
  [/değişim|degisim|iade & değişim/i, RefreshCw],
  [/müşteri hizmet|musteri hizmet|hizmet|destek|iletişim|iletisim/i, Headphones],
  [/adres|konum/i, MapPin],
  [/telefon|whatsapp|ara[yn]/i, Phone],
  [/e-?posta|eposta|mail|e-mail/i, Mail],
  [/çalışma|calisma|saat|mesai/i, Clock],
  [/kurumsal|ünvan|unvan|iban|vergi|şirket|sirket/i, Building2],
  [/gizlilik|kvkk|güvenlik|guvenlik/i, ShieldCheck],
  [/sık|sik|soru|sss/i, HelpCircle],
  [/fatura|belge|sözleşme|sozlesme/i, FileText],
];

function pickIcon(title) {
  const t = (title || "").toLowerCase();
  for (const [re, Icon] of ICON_RULES) {
    if (re.test(t)) return Icon;
  }
  return Info;
}

// Başlıktaki baştaki emoji / simge / boşlukları temizle (kendi ikonumuzu koyduğumuz için).
function cleanTitle(raw) {
  return (raw || "")
    .replace(/^[^\p{L}\p{N}]+/u, "") // baştaki harf/rakam olmayan her şeyi at (emoji dahil)
    .trim();
}

/**
 * CMS HTML'ini { lead, sections[] } yapısına ayrıştırır.
 * - lead: ilk <h3>'ten önceki .lead veya ilk <p>
 * - sections: her <h3> bir bölüm; başlığa kadar olan sonraki kardeşler gövdedir (<hr> atlanır).
 */
function parseSections(html) {
  if (typeof document === "undefined") return { lead: "", sections: [] };
  let doc;
  try {
    doc = new DOMParser().parseFromString(`<div id="root">${html || ""}</div>`, "text/html");
  } catch {
    return { lead: "", sections: [] };
  }
  const root = doc.getElementById("root");
  if (!root) return { lead: "", sections: [] };

  const nodes = Array.from(root.childNodes).filter(
    (n) => n.nodeType === 1 || (n.nodeType === 3 && n.textContent.trim())
  );

  let lead = "";
  const sections = [];
  let current = null;
  let sawH3 = false;

  for (const n of nodes) {
    const tag = n.nodeType === 1 ? n.tagName.toLowerCase() : "#text";
    if (tag === "h3") {
      sawH3 = true;
      if (current) sections.push(current);
      current = { title: cleanTitle(n.textContent), bodyHtml: "" };
      continue;
    }
    if (tag === "hr") continue; // ayraçları atla (bölümler arası çizgiyi biz koyuyoruz)
    if (!sawH3) {
      // henüz başlık yok → lead (intro) topla
      if (n.nodeType === 1 && (n.classList?.contains("lead") || tag === "p")) {
        lead += n.outerHTML;
      } else if (n.nodeType === 1) {
        lead += n.outerHTML;
      }
      continue;
    }
    if (current) current.bodyHtml += n.nodeType === 1 ? n.outerHTML : n.textContent;
  }
  if (current) sections.push(current);
  return { lead, sections };
}

const BODY_TYPO =
  "[&_p]:text-[15px] [&_p]:leading-[1.75] [&_p]:text-neutral-600 [&_p]:mb-2 " +
  "[&_strong]:font-semibold [&_strong]:text-neutral-900 " +
  "[&_a]:text-black [&_a]:underline [&_a]:underline-offset-2 hover:[&_a]:opacity-60 " +
  "[&_ul]:mt-1 [&_ul]:space-y-2 [&_li]:relative [&_li]:pl-4 [&_li]:text-[15px] [&_li]:leading-[1.7] [&_li]:text-neutral-600 " +
  "[&_li]:before:content-['•'] [&_li]:before:absolute [&_li]:before:left-0 [&_li]:before:text-neutral-400 " +
  "[&_br]:block";

export default function IconDocPage({ page }) {
  const { lead, sections } = useMemo(() => parseSections(page?.content || ""), [page]);

  return (
    <div className="max-w-2xl">
      <h1 className="text-3xl md:text-5xl font-light tracking-tight text-black mb-3">{page?.title}</h1>
      <div className="w-10 h-px bg-black/70 mb-8 md:mb-10" />

      {lead && (
        <div
          className="text-[17px] md:text-lg leading-relaxed text-neutral-800 font-light mb-10 md:mb-14 [&_p]:mb-3"
          dangerouslySetInnerHTML={{ __html: sanitizeHtml(lead) }}
        />
      )}

      <div className="divide-y divide-neutral-200">
        {sections.map((s, i) => {
          const Icon = pickIcon(s.title);
          return (
            <div key={i} className="flex gap-5 md:gap-7 py-8 md:py-9 first:pt-0">
              <div className="flex-shrink-0">
                <div className="w-14 h-14 md:w-16 md:h-16 rounded-full bg-neutral-100 flex items-center justify-center">
                  <Icon size={26} strokeWidth={1.25} className="text-neutral-800" />
                </div>
              </div>
              <div className="flex-1 min-w-0 pt-1">
                <h2 className="text-sm md:text-base font-medium tracking-[0.12em] uppercase text-black mb-3">
                  {s.title}
                </h2>
                <div className={BODY_TYPO} dangerouslySetInnerHTML={{ __html: sanitizeHtml(s.bodyHtml) }} />
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
