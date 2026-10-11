/**
 * =============================================================================
 * CountdownBar.jsx — Sitenin EN ÜST sticky promo barı
 * =============================================================================
 * Admin > Sayfa Tasarımı > "Geri Sayım Barı" bloğundan tam olarak yönetilebilir.
 *
 * Block JSON ŞEMASI (page_blocks koleksiyonunda):
 * {
 *   type: "countdown_bar",
 *   is_active: true,
 *   settings: {
 *     left_text:  "TÜM ALIŞVERİŞLERDE KARGO BEDAVA",
 *     timer_label:"KALAN SÜRE:",
 *     start_at:   "2026-05-08T00:00:00"   // ISO 8601 (local). Bu tarihe kadar bar GİZLİ.
 *     end_at:     "2026-05-19T23:59:59"   // ISO. Bu tarihe kadar countdown çalışır.
 *     bg_color:   "#000000",   // arka plan
 *     text_color: "#ffffff",   // metin
 *     fallback_text: "500 TL Üzeri Ücretsiz Kargo"   // (opsiyonel) bar pasifken gösterilecek metin
 *   }
 * }
 *
 * KURALLAR:
 *   • is_active=false       → render edilmez (admin taslakta)
 *   • now < start_at        → render edilmez (planlanmış, henüz başlamadı)
 *   • now > end_at          → render edilmez (süre doldu)
 *   • aksi halde            → countdown ile birlikte gösterilir
 *
 * Eğer hiç aktif countdown_bar bloğu yoksa → fallback olarak orijinal
 * "500 TL Üzeri Ücretsiz Kargo" metni statik şekilde gösterilir.
 * =============================================================================
 */
import { useState, useEffect } from "react";
import axios from "axios";

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;

// ── ISO string → Date (local timezone safe) ─────────────────────────────────
const _parseLocal = (s) => {
  if (!s) return null;
  try {
    // Eğer Z/+- yoksa local olarak parse et
    if (typeof s === "string" && !/[zZ]|[+-]\d{2}:?\d{2}$/.test(s)) {
      return new Date(s);
    }
    return new Date(s);
  } catch { return null; }
};

const _diffParts = (target, now) => {
  let ms = target - now;
  if (ms < 0) ms = 0;
  const days = Math.floor(ms / 86400000);
  const hours = Math.floor((ms % 86400000) / 3600000);
  const mins = Math.floor((ms % 3600000) / 60000);
  const secs = Math.floor((ms % 60000) / 1000);
  return { days, hours, mins, secs, total: ms };
};

const _pad = (n) => String(n).padStart(2, "0");

// Çoklu duyuru metni: "|" veya yeni satır ile ayrılan metinleri diziye çevirir.
// Tek metin → tek elemanlı dizi (geriye uyumlu).
const _splitMessages = (str) =>
  String(str || "")
    .split(/\n|\|/)
    .map((x) => x.trim())
    .filter(Boolean);

// Üst bar son görülen halini localStorage'da tutar → tekrar ziyarette/yenilemede
// barı ANINDA (doğru yükseklik + metinle) gösterir; flaş ve layout kayması olmaz.
const _CACHE_KEY = "fct_topbar_block_v1";
const _readBarCache = () => {
  try { const s = localStorage.getItem(_CACHE_KEY); return s ? JSON.parse(s) : null; }
  catch (e) { return null; }
};

export default function CountdownBar() {
  const [block, setBlock] = useState(_readBarCache);   // önbellekten anında
  const [now, setNow] = useState(Date.now());
  const [msgIdx, setMsgIdx] = useState(0);
  const [loaded, setLoaded] = useState(() => _readBarCache() !== null); // cache varsa anında hazır

  // Bloğu yükle (sayfa açılışında)
  useEffect(() => {
    let mounted = true;
    axios.get(`${API}/page-blocks?page=home`)
      .then((r) => {
        if (!mounted) return;
        const cb = (r.data || []).find((b) => b.type === "countdown_bar" && b.is_active);
        setBlock(cb || null);
        try {
          if (cb) localStorage.setItem(_CACHE_KEY, JSON.stringify(cb));
          else localStorage.removeItem(_CACHE_KEY);
        } catch (e) {}
      })
      .catch(() => {})
      .finally(() => { if (mounted) setLoaded(true); });
    return () => { mounted = false; };
  }, []);

  // Tick — saniye bazlı güncelleme (sadece aktif countdown varsa)
  useEffect(() => {
    if (!block) return;
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, [block]);

  // ── Geçerli metin alanını ve çoklu mesaj listesini belirle ──────────────────
  const s = block?.settings || {};
  const startAt = _parseLocal(s.start_at);
  const endAt   = _parseLocal(s.end_at);
  const bg      = s.bg_color || "#000000"; // DENETİM FIX: panelde seçilen renk uygulanıyordu değildi
  const fg      = s.text_color || "#ffffff";
  const inWindow =
    (!startAt || now >= startAt.getTime()) &&
    (!endAt   || now <= endAt.getTime());

  let rawText;
  if (!block)          rawText = "";                             // blok yok → metin yok (yedek metin kaldırıldı)
  else if (!inWindow)  rawText = s.fallback_text || "";          // pencere dışı
  else                 rawText = s.left_text || "";              // aktif

  const messages = _splitMessages(rawText);
  const messagesKey = messages.join("||");

  // Çoklu mesaj rotasyonu (4 sn). messagesKey sabit olduğundan saniyelik
  // tick'lerde interval sıfırlanmaz.
  useEffect(() => {
    if (messages.length <= 1) { setMsgIdx(0); return; }
    const t = setInterval(() => setMsgIdx((i) => (i + 1) % messages.length), 4000);
    return () => clearInterval(t);
  }, [messagesKey]); // eslint-disable-line react-hooks/exhaustive-deps

  const currentMsg = messages.length ? messages[msgIdx % messages.length] : "";
  const multi = messages.length > 1;
  const fadeStyle = multi ? { animation: "fctTopbarSlide .6s cubic-bezier(.16,1,.3,1)" } : undefined;
  const fadeKeyframes = multi
    ? <style>{"@keyframes fctTopbarSlide{0%{opacity:0;transform:translateY(110%)}100%{opacity:1;transform:translateY(0)}}"}</style>
    : null;

  // page-blocks henüz dönmedi → nötr, sabit yükseklikli, METİNSİZ bar.
  // Varsayılan "500 TL" metnini flaşlatmadan yüklemeyi bekler (#17 flaş düzeltmesi).
  if (!loaded) {
    return <div className="bg-black" data-testid="topbar-loading" aria-hidden="true" style={{ minHeight: 38 }} />;
  }

  // Hiç blok yok → hiçbir şey gösterme (yedek/statik metin kaldırıldı).
  if (!block) return null;

  if (!inWindow) {
    // Bar pasif (planlanmış ya da süresi dolmuş) → fallback metin (kayan marquee)
    if (!messages.length) return null; // tamamen gizle
    return (
      <div className="py-2 md:py-2.5" style={{ backgroundColor: bg, color: fg }}
           data-testid="topbar-fallback">
        <Marquee items={messages} fg={fg}
          className="text-[13px] md:text-[17px] uppercase fct-topbar-heavy" />
      </div>
    );
  }

  // Aktif: sayaç + (dönüşümlü) sol metin
  const target = endAt ? endAt.getTime() : now;
  const { days, hours, mins, secs } = _diffParts(target, now);
  const timerLbl  = (s.timer_label || "KALAN SÜRE:").trim();

  return (
    <div
      className="text-center py-2 md:py-2.5"
      style={{ backgroundColor: bg, color: fg }}
      data-testid="topbar-countdown"
    >
      {fadeKeyframes}
      <div className="max-w-screen-2xl mx-auto px-3 md:px-6 flex items-center justify-center gap-3 md:gap-5 flex-wrap">
        {currentMsg && (
          <Rotator idx={msgIdx} text={currentMsg} multi={multi}
             className="text-xs md:text-sm font-semibold tracking-wide flex-shrink-0" />
        )}
        <div className="flex items-center gap-2 md:gap-3">
          {timerLbl && (
            <span className="text-[11px] md:text-[13px] tracking-[0.22em] uppercase font-semibold hidden md:inline">
              {timerLbl}
            </span>
          )}
          <CountUnit value={days}  label="GÜN"  fg={fg} bg={bg} />
          <CountUnit value={hours} label="SAAT" fg={fg} bg={bg} />
          <CountUnit value={mins}  label="DK"   fg={fg} bg={bg} />
          <CountUnit value={secs}  label="SN"   fg={fg} bg={bg} />
        </div>
      </div>
    </div>
  );
}

// Üst bar yazı geçişi — suudcollection tarzı: dikey kayan (clipped slide-up) rotasyon.
function Rotator({ idx, text, multi, className }) {
  if (!multi) return <p className={className}>{text}</p>;
  return (
    <p className={`${className} overflow-hidden`} style={{ lineHeight: 1.6 }}>
      <span
        key={idx}
        style={{ display: "inline-block", animation: "fctTopbarSlide .6s cubic-bezier(.22,1,.36,1)", willChange: "transform, opacity" }}
      >
        {text}
      </span>
    </p>
  );
}

// Vatkali tarzı SÜREKLİ kayan üst bar (marquee): mesajlar sağdan sola kesintisiz akar,
// başa dönerken zıplama olmaz (içerik iki kez basılır, track -%50 kaydırılır). Hover'da
// durur; prefers-reduced-motion açıksa animasyon kapalıdır. Hız metin uzunluğuna göre
// ayarlanır → mesaj sayısından bağımsız sabit akış hızı.
function Marquee({ items, className, fg = "#ffffff" }) {
  const list = (items || []).filter(Boolean);
  if (!list.length) return null;
  const totalLen = list.join("   ").length || 20;
  const durSec = Math.min(70, Math.max(16, Math.round(totalLen * 0.55)));
  const Group = ({ ariaHidden }) => (
    <div className="flex items-center shrink-0" aria-hidden={ariaHidden ? "true" : undefined}>
      {list.map((t, i) => (
        <span key={i} className="flex items-center shrink-0">
          <span className={className}>{t}</span>
          <span className="mx-5 md:mx-9 opacity-60 select-none" style={{ color: fg }}>◆</span>
        </span>
      ))}
    </div>
  );
  return (
    <div className="fct-marquee w-full overflow-hidden" data-testid="topbar-marquee">
      <style>{`
        @keyframes fctMarqueeScroll { from { transform: translateX(0); } to { transform: translateX(-50%); } }
        .fct-marquee__track { display:flex; align-items:center; width:max-content; will-change:transform;
          animation: fctMarqueeScroll ${durSec}s linear infinite; }
        .fct-marquee:hover .fct-marquee__track { animation-play-state: paused; }
        @media (prefers-reduced-motion: reduce) { .fct-marquee__track { animation: none; justify-content:center; width:100%; } }
      `}</style>
      <div className="fct-marquee__track">
        <Group />
        <Group ariaHidden />
      </div>
    </div>
  );
}

function CountUnit({ value, label, fg, bg }) {
  // Beyaz tabela + metin renkleri tersine — bar siyah arkaplanlıysa kutular beyaz arka, metin siyah
  return (
    <div className="flex items-center gap-1">
      <span
        className="inline-flex items-center justify-center min-w-[26px] md:min-w-[34px] h-6 md:h-7 px-1 md:px-1.5 text-[11px] md:text-sm font-semibold tabular-nums"
        style={{ backgroundColor: fg, color: bg }}
      >
        {_pad(value)}
      </span>
      <span className="text-[9px] md:text-[11px] tracking-[0.18em] uppercase font-medium">{label}</span>
    </div>
  );
}
