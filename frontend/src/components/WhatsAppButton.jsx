/**
 * WhatsAppButton.jsx — Storefront yüzen WhatsApp destek butonu.
 *
 * Admin panelinden (Mağaza Ayarları) açılır ve numara girilir:
 *   storefront.whatsapp_enabled (toggle, varsayılan KAPALI)
 *   storefront.whatsapp_number  (text — uluslararası, ör. 905xxxxxxxxx)
 *   storefront.whatsapp_message (text — hazır mesaj, opsiyonel)
 *
 * Numara girilmeden veya kapalıyken HİÇBİR ŞEY render etmez (ölü buton yok).
 * /admin rotalarında ve native uygulamada gösterilmez.
 */
import { useEffect, useState } from "react";
import { useLocation } from "react-router-dom";
import axios from "axios";

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;

export default function WhatsAppButton() {
  const [cfg, setCfg] = useState(null);
  const loc = useLocation();

  useEffect(() => {
    axios.get(`${API}/business-rules`)
      .then((r) => setCfg(r.data || {}))
      .catch(() => {});
  }, []);

  if (!cfg) return null;
  if (cfg["storefront.whatsapp_enabled"] !== true) return null;
  const raw = String(cfg["storefront.whatsapp_number"] || "").replace(/[^\d]/g, "");
  if (!raw) return null;
  // Admin rotalarında gösterme
  if ((loc.pathname || "").startsWith("/admin")) return null;

  const msg = String(cfg["storefront.whatsapp_message"] || "Merhaba, yardımcı olur musunuz?");
  const href = `https://wa.me/${raw}?text=${encodeURIComponent(msg)}`;

  return (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      aria-label="WhatsApp ile destek"
      data-testid="whatsapp-fab"
      style={{
        position: "fixed", right: "18px", bottom: "18px", zIndex: 9998,
        width: "54px", height: "54px", borderRadius: "50%",
        background: "#25D366", display: "flex", alignItems: "center", justifyContent: "center",
        boxShadow: "0 4px 14px rgba(0,0,0,.25)", transition: "transform .15s ease",
      }}
      onMouseEnter={(e) => { e.currentTarget.style.transform = "scale(1.08)"; }}
      onMouseLeave={(e) => { e.currentTarget.style.transform = "scale(1)"; }}
    >
      {/* WhatsApp logosu (inline SVG — dış istek yok) */}
      <svg width="30" height="30" viewBox="0 0 32 32" fill="#fff" aria-hidden="true">
        <path d="M16.04 3C9.4 3 4 8.4 4 15.04c0 2.12.56 4.19 1.62 6.02L4 29l8.13-1.58a12 12 0 0 0 3.9.66h.01C22.68 28.08 28 22.68 28 16.04 28 8.4 22.68 3 16.04 3zm0 22.06h-.01a9.9 9.9 0 0 1-3.4-.63l-.24-.1-4.83.94.97-4.71-.16-.25a9.86 9.86 0 0 1-1.5-5.22c0-5.46 4.44-9.9 9.9-9.9 2.64 0 5.12 1.03 6.99 2.9a9.82 9.82 0 0 1 2.9 6.99c0 5.46-4.44 9.91-9.9 9.91zm5.43-7.42c-.3-.15-1.76-.87-2.03-.97-.27-.1-.47-.15-.67.15-.2.3-.77.97-.94 1.17-.17.2-.35.22-.65.07-.3-.15-1.26-.46-2.4-1.48-.89-.79-1.49-1.77-1.66-2.07-.17-.3-.02-.46.13-.61.13-.13.3-.35.45-.52.15-.17.2-.3.3-.5.1-.2.05-.37-.02-.52-.07-.15-.67-1.62-.92-2.22-.24-.58-.49-.5-.67-.51l-.57-.01c-.2 0-.52.07-.8.37-.27.3-1.04 1.02-1.04 2.48 0 1.46 1.07 2.88 1.22 3.08.15.2 2.1 3.2 5.08 4.49.71.31 1.26.49 1.69.63.71.22 1.36.19 1.87.12.57-.09 1.76-.72 2.01-1.42.25-.7.25-1.29.17-1.42-.07-.13-.27-.2-.57-.35z"/>
      </svg>
    </a>
  );
}
