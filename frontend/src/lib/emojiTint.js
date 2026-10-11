import React from "react";

// Emoji dizisi (ZWJ / varyasyon seçicili birleşik emojiler dahil) — split için yakalayan grup.
const EMOJI_RE = /(\p{Extended_Pictographic}(?:️|‍\p{Extended_Pictographic})*️?)/u;

// Metindeki emojileri (🐾 vb.) verilen renkte TEK RENK siluet olarak çizer. Emojiler sistem
// emoji fontuyla renkli/gri çiziliyordu; color:transparent + text-shadow ile yazı rengini alır.
export function tintEmojis(text, color) {
  const parts = String(text || "").split(EMOJI_RE);
  if (parts.length === 1) return text;
  return parts.map((part, i) => (i % 2 === 1
    ? React.createElement("span", { key: i, "aria-hidden": "true",
        style: { color: "transparent", textShadow: `0 0 0 ${color}` } }, part)
    : part));
}
