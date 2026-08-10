/**
 * =============================================================================
 * SitePopup.jsx — Admin > Popup'lar storefront gösterimi
 * =============================================================================
 * /api/storefront/popups (aktif + tarih aralığı) uçtan okur; delay_seconds sonra
 * (ya da exit_intent'te) gösterir. show_once ise oturum/başına bir kez.
 *
 * İki görünüm:
 *   • newsletter=true  → SAĞ ALT köşe bülten kartı (e-posta + KVKK onayı + İYS)
 *                        — "Üyeliğinize Özel %10 İndirim" tarzı, site fontu/renkleri.
 *   • aksi halde       → ekran ortası klasik modal (görsel/içerik/link).
 * =============================================================================
 */
import { useState, useEffect } from "react";
import { Link, useLocation } from "react-router-dom";
import axios from "axios";
import { Tag, Gift, Mail, Lock, Check, X } from "lucide-react";

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;

const CONSENT_TEXT =
  "KVKK Aydınlatma Metni'ni okudum; kampanya ve fırsatlar için ticari elektronik ileti (e-posta) almayı kabul ediyorum.";

const DEFAULT_BENEFITS = [
  "Herhangi bir alışverişinizde %10 indirim",
  "Özel kampanya ve ön satışlara erken erişim",
  "Yeni koleksiyon ve fırsatlardan haberdar olun",
];
const BENEFIT_ICONS = [Tag, Gift, Mail];

export default function SitePopup() {
  const [popup, setPopup] = useState(null);
  const [visible, setVisible] = useState(false);
  const location = useLocation();
  // Popup YALNIZ vitrinde (storefront) gösterilir — admin panelinde ASLA.
  const isAdmin = (location?.pathname || "").startsWith("/admin");

  useEffect(() => {
    if (isAdmin) return;   // admin panelinde popup çekme/gösterme
    let alive = true;
    let timer = null;
    let onExit = null;
    const cleanupExit = () => { if (onExit) { document.removeEventListener("mouseout", onExit); onExit = null; } };
    axios.get(`${API}/storefront/popups`)
      .then((r) => {
        if (!alive) return;
        const list = Array.isArray(r?.data?.items) ? r.data.items : [];
        const p = list.find((x) => x && (x.content || x.image || x.name || x.newsletter));
        if (!p) return;
        if (p.show_once) {
          try { if (localStorage.getItem(`facette_popup_${p.id}`) === "1") return; } catch { /* yoksay */ }
        }
        setPopup(p);
        const show = () => { if (alive) { setVisible(true); cleanupExit(); if (timer) clearTimeout(timer); } };
        const trigger = String(p.trigger || "delay");
        const isTouch = typeof window !== "undefined" && ("ontouchstart" in window || navigator.maxTouchPoints > 0);
        if (trigger === "exit_intent" && !isTouch) {
          onExit = (e) => { if (e.clientY <= 0 && !e.relatedTarget) show(); };
          document.addEventListener("mouseout", onExit);
          timer = setTimeout(show, 45000);
        } else if (trigger === "exit_intent" && isTouch) {
          timer = setTimeout(show, 20000);
        } else {
          const delay = Math.max(0, Number(p.delay_seconds) || 0) * 1000;
          timer = setTimeout(show, delay);
        }
      })
      .catch(() => {});
    return () => { alive = false; if (timer) clearTimeout(timer); cleanupExit(); };
  }, [isAdmin]);

  if (isAdmin || !popup || !visible) return null;

  const close = () => {
    setVisible(false);
    if (popup.show_once) {
      try { localStorage.setItem(`facette_popup_${popup.id}`, "1"); } catch { /* yoksay */ }
    }
  };

  // ── SAĞ ALT bülten kartı (newsletter) ─────────────────────────────────────
  if (popup.newsletter) {
    return <CornerNewsletter popup={popup} onClose={close} />;
  }

  // ── Klasik ORTA modal (görsel/içerik/link) ────────────────────────────────
  return (
    <div className="fixed inset-0 z-[9999] flex items-center justify-center bg-black/50 p-4" onClick={close} data-testid="site-popup">
      <div onClick={(e) => e.stopPropagation()} className="bg-white rounded-xl overflow-hidden max-w-md w-full shadow-2xl relative">
        <button onClick={close} className="absolute right-3 top-3 z-10 w-8 h-8 rounded-full bg-black/40 text-white flex items-center justify-center hover:bg-black/60" aria-label="Kapat">×</button>
        {popup.image && <img src={popup.image} alt={popup.name || "Popup"} className="w-full object-cover" />}
        <div className="p-6">
          {(popup.name || popup.title) && <h3 className="text-xl font-semibold mb-2 tracking-tight">{popup.name || popup.title}</h3>}
          {(popup.subtitle || popup.content) && <div className="text-sm text-gray-600 whitespace-pre-line mb-4">{popup.subtitle || popup.content}</div>}
          {popup.link && <a href={popup.link} onClick={close} className="mt-2 inline-block bg-black text-white px-5 py-2 rounded-lg text-sm hover:bg-gray-800">{popup.button_text || "İncele"}</a>}
        </div>
      </div>
    </div>
  );
}

/**
 * CornerNewsletter — sağ altta açılan bülten/indirim kartı (ekran görüntüsündeki tasarım,
 * site fontu + siyah/beyaz renkleriyle). Zorunlu KVKK onayı; abone kaydı + İYS backend'de.
 */
function CornerNewsletter({ popup, onClose }) {
  const [email, setEmail] = useState("");
  const [consent, setConsent] = useState(false);
  const [state, setState] = useState("idle"); // idle | loading | done | error
  const [msg, setMsg] = useState("");

  const title = popup.name || popup.title || "Üyeliğinize Özel %10 İndirim";
  const benefits = (() => {
    const lines = String(popup.content || "").split(/\n+/).map((x) => x.trim()).filter(Boolean);
    return lines.length ? lines : DEFAULT_BENEFITS;
  })();
  const buttonText = popup.button_text || "Üye Ol ve Kazan";

  const submit = async (e) => {
    e.preventDefault();
    const v = (email || "").trim();
    if (!v || !/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(v)) { setState("error"); setMsg("Lütfen geçerli bir e-posta adresi girin."); return; }
    if (!consent) { setState("error"); setMsg("Devam etmek için KVKK / ticari ileti onayını işaretleyin."); return; }
    setState("loading");
    try {
      const r = await axios.post(`${API}/newsletter/subscribe`, { email: v, source: "popup", consent: true, consent_text: CONSENT_TEXT });
      setState("done"); setMsg(r?.data?.message || "Aramıza hoş geldin!");
    } catch (err) {
      setState("error"); setMsg(err?.response?.data?.detail || "Bir sorun oluştu, tekrar dene.");
    }
  };

  return (
    <div className="fixed z-[9999] bottom-4 right-4 left-4 md:left-auto md:w-[380px]" data-testid="site-popup-newsletter">
      <style>{`@keyframes fctPopIn{0%{opacity:0;transform:translateY(24px)}100%{opacity:1;transform:translateY(0)}}`}</style>
      <div className="relative bg-white border border-neutral-200 shadow-2xl rounded-lg p-6 md:p-7"
           style={{ animation: "fctPopIn .45s cubic-bezier(.16,1,.3,1)" }}>
        <button onClick={onClose} aria-label="Kapat"
          className="absolute right-3 top-3 text-neutral-400 hover:text-black transition-colors"><X size={20} /></button>

        {state === "done" ? (
          <div className="py-6 text-center">
            <div className="w-12 h-12 rounded-full bg-black text-white flex items-center justify-center mx-auto mb-3">
              <Check size={22} strokeWidth={2.5} />
            </div>
            <p className="text-sm text-neutral-800">{msg}</p>
          </div>
        ) : (
          <>
            <h3 className="text-[26px] leading-[1.1] font-light tracking-tight text-black text-center px-6">{title}</h3>
            <div className="h-px bg-neutral-300/70 mt-5 mb-5" />

            <ul className="space-y-3.5 mb-5">
              {benefits.map((b, i) => {
                const Icon = BENEFIT_ICONS[i % BENEFIT_ICONS.length];
                return (
                  <li key={i} className="flex items-center gap-3">
                    <span className="w-9 h-9 rounded-full bg-neutral-100 flex items-center justify-center flex-shrink-0">
                      <Icon size={16} strokeWidth={1.5} className="text-neutral-700" />
                    </span>
                    <span className="text-[13px] text-neutral-700 leading-snug">{b}</span>
                  </li>
                );
              })}
            </ul>

            <form onSubmit={submit}>
              {/* Alt-çizgi (underline) e-posta alanı — referanstaki css mantığı */}
              <input
                type="email" value={email}
                onChange={(e) => { setEmail(e.target.value); if (state === "error") setState("idle"); }}
                placeholder="E-posta adresiniz"
                className="w-full bg-transparent border-0 border-b border-neutral-300 rounded-none px-0 py-2.5 text-sm text-neutral-900 placeholder-neutral-400 outline-none focus:border-neutral-900 transition-colors"
                aria-label="E-posta adresi"
              />
              <button type="submit" disabled={state === "loading"}
                className="w-full mt-2.5 bg-black text-white py-3.5 text-[13px] tracking-[0.18em] uppercase font-semibold hover:bg-neutral-800 disabled:opacity-50 transition-colors">
                {state === "loading" ? "Gönderiliyor…" : buttonText}
              </button>
              {state === "error" && <p className="text-xs text-red-500 mt-2">{msg}</p>}

              <label className="flex items-start gap-2 mt-4 cursor-pointer">
                <input type="checkbox" checked={consent}
                  onChange={(e) => { setConsent(e.target.checked); if (state === "error") setState("idle"); }}
                  className="mt-0.5 w-3.5 h-3.5 accent-black flex-shrink-0" aria-label="KVKK ve ticari ileti onayı" />
                <span className="text-[11px] text-neutral-500 leading-snug inline-flex items-start gap-1">
                  <Lock size={12} className="mt-0.5 flex-shrink-0" />
                  <span>
                    <Link to="/sayfa/kvkk" onClick={onClose} className="underline hover:text-black">KVKK</Link> kapsamında
                    ticari e-posta almayı kabul ediyorum. Kişisel verileriniz güvenle korunur; dilediğiniz zaman aboneliğinizi iptal edebilirsiniz.
                  </span>
                </span>
              </label>
            </form>
          </>
        )}
      </div>
    </div>
  );
}
