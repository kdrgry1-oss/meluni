import React, { useEffect, useState } from "react";
import { tintEmojis } from "../lib/emojiTint";

// Üst duyuru barı (Sayfa Tasarımı'ndaki "rotating_text" bloğu). Metin/renk/süre
// panelden gelir. Ana sayfa ve Header (diğer tüm sayfalar) aynı bileşeni kullanır.
export default function RotatingText({ block }) {
  const [currentIndex, setCurrentIndex] = useState(0);
  const texts = (block?.settings?.texts || []).filter((t) => (t || "").trim());

  useEffect(() => {
    if (texts.length < 2) return;
    const sec = Math.max(2, Number(block?.settings?.interval) || 4);
    const interval = setInterval(() => {
      setCurrentIndex((prev) => (prev + 1) % texts.length);
    }, sec * 1000);
    return () => clearInterval(interval);
  }, [texts.length, block]);

  if (texts.length === 0) return null;
  const bg = block?.settings?.bg_color || "#ffffff";
  const fg = block?.settings?.text_color || "#374151";

  return (
    <div
      className="text-center py-1"
      style={{ backgroundColor: bg }}
      data-testid="rotating-text"
    >
      <span
        key={currentIndex}
        className="text-[10px] md:text-[12px] uppercase"
        style={{
          color: fg,
          fontWeight: 500,
          letterSpacing: "0.12em",
        }}
      >
        {tintEmojis(texts[currentIndex % texts.length], fg)}
      </span>
    </div>
  );
}
