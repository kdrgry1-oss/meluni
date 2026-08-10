import { useEffect, useRef, useState } from "react";

/**
 * FastTooltip — Tek sefer monte edilen global tooltip.
 *
 * Sitedeki HER `title` attribute'unu yakalar; native ~1sn gecikme yerine
 * ~180ms içinde şık, koyu bir balon olarak gösterir. Hiçbir butona tek tek
 * dokunmaya gerek yok — mevcut tüm `title`'lar otomatik hızlanır.
 *
 * Çalışma: hover anında elementin `title`'ını okur, native tooltip çıkmasın
 * diye geçici olarak `data-ftip`'e taşır; mouseout/scroll'da geri yükler.
 */
export default function FastTooltip() {
  const [tip, setTip] = useState(null); // { text, x, y, place }
  const timerRef = useRef(null);
  const elRef = useRef(null);

  useEffect(() => {
    const DELAY = 180;

    const restore = (el) => {
      if (!el) return;
      const t = el.getAttribute("data-ftip");
      if (t != null) {
        if (!el.getAttribute("title")) el.setAttribute("title", t);
        el.removeAttribute("data-ftip");
      }
    };

    const cleanup = () => {
      if (timerRef.current) { clearTimeout(timerRef.current); timerRef.current = null; }
      if (elRef.current) { restore(elRef.current); elRef.current = null; }
      setTip(null);
    };

    const show = (el) => {
      const text = el.getAttribute("title");
      if (!text) return;
      el.setAttribute("data-ftip", text);
      el.removeAttribute("title"); // native tooltip'i bastır
      elRef.current = el;
      timerRef.current = setTimeout(() => {
        if (elRef.current !== el || !el.isConnected) return;
        const r = el.getBoundingClientRect();
        const vw = window.innerWidth;
        let x = r.left + r.width / 2;
        x = Math.max(60, Math.min(vw - 60, x)); // kenarlardan taşmayı önle
        const place = r.bottom + 40 > window.innerHeight ? "top" : "bottom";
        const y = place === "bottom" ? r.bottom + 6 : r.top - 6;
        setTip({ text, x, y, place });
      }, DELAY);
    };

    const onOver = (e) => {
      const el = e.target.closest && e.target.closest("[title]");
      if (!el) return;
      if (el === elRef.current) return;
      cleanup();
      show(el);
    };

    const onOut = (e) => {
      if (!elRef.current) return;
      const to = e.relatedTarget;
      if (to && elRef.current.contains && elRef.current.contains(to)) return;
      cleanup();
    };

    document.addEventListener("mouseover", onOver, true);
    document.addEventListener("mouseout", onOut, true);
    window.addEventListener("scroll", cleanup, true);
    window.addEventListener("blur", cleanup);
    return () => {
      document.removeEventListener("mouseover", onOver, true);
      document.removeEventListener("mouseout", onOut, true);
      window.removeEventListener("scroll", cleanup, true);
      window.removeEventListener("blur", cleanup);
      cleanup();
    };
  }, []);

  if (!tip) return null;

  return (
    <div
      style={{
        position: "fixed",
        left: tip.x,
        top: tip.y,
        transform: tip.place === "bottom" ? "translate(-50%, 0)" : "translate(-50%, -100%)",
        zIndex: 99999,
        pointerEvents: "none",
        background: "#111827",
        color: "#fff",
        fontSize: 12,
        lineHeight: 1.35,
        padding: "5px 9px",
        borderRadius: 6,
        maxWidth: 280,
        boxShadow: "0 6px 18px rgba(0,0,0,0.22)",
        whiteSpace: "normal",
        textAlign: "center",
      }}
    >
      {tip.text}
    </div>
  );
}
