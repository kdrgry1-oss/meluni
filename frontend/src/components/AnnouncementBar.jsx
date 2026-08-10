/**
 * =============================================================================
 * AnnouncementBar.jsx — Admin > Duyurular (announcements) storefront barı
 * =============================================================================
 * DENETİM FIX (#35): Admin'de oluşturulan Duyurular için storefront tüketimi yoktu.
 * Bu bileşen /api/storefront/announcements (aktif + tarih aralığı filtreli) uçtan
 * okur ve en üstte ince bir bar olarak gösterir. Kullanıcı kapatabilir (session).
 *
 * Savunmacı: uç hatası / boş liste → hiçbir şey render etmez (mağazayı asla kırmaz).
 * =============================================================================
 */
import { useState, useEffect } from "react";
import axios from "axios";

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;

export default function AnnouncementBar() {
  const [items, setItems] = useState([]);
  const [closed, setClosed] = useState(false);
  const [enabled, setEnabled] = useState(true);  // İşletme Kuralları: storefront.announcement_bar_enabled

  useEffect(() => {
    let alive = true;
    axios
      .get(`${API}/storefront/announcements`)
      .then((r) => {
        if (!alive) return;
        const list = Array.isArray(r?.data?.items) ? r.data.items : [];
        setItems(list.filter((a) => a && (a.content || a.name)));
      })
      .catch(() => {});
    // Duyuru şeridi İşletme Kuralları'ndan kapatılabilir (varsayılan açık)
    axios.get(`${API}/business-rules`)
      .then((r) => { if (alive && r?.data?.["storefront.announcement_bar_enabled"] === false) setEnabled(false); })
      .catch(() => {});
    // Bu oturumda kapatıldıysa gösterme
    try {
      if (sessionStorage.getItem("facette_ann_closed") === "1") setClosed(true);
    } catch { /* sessionStorage yoksa yoksay */ }
    return () => { alive = false; };
  }, []);

  if (!enabled || closed || items.length === 0) return null;

  // İlk aktif duyuruyu göster (birden çoksa sırayla değil, en üsttekini)
  const a = items[0];
  const text = a.content || a.name || "";
  if (!text) return null;

  const bg = a.bg_color || "#111111";
  const fg = a.text_color || "#ffffff";

  const inner = (
    <span className="text-xs md:text-sm font-medium tracking-wide">{text}</span>
  );

  return (
    <div
      className="w-full flex items-center justify-center gap-3 px-4 py-2 relative"
      style={{ backgroundColor: bg, color: fg }}
      data-testid="announcement-bar"
    >
      {a.link ? (
        <a href={a.link} className="hover:underline">{inner}</a>
      ) : (
        inner
      )}
      <button
        onClick={() => {
          setClosed(true);
          try { sessionStorage.setItem("facette_ann_closed", "1"); } catch { /* yoksay */ }
        }}
        className="absolute right-3 top-1/2 -translate-y-1/2 opacity-70 hover:opacity-100 text-sm"
        aria-label="Kapat"
        style={{ color: fg }}
      >
        ×
      </button>
    </div>
  );
}
