import { useState, useEffect } from "react";
import axios from "axios";

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;

const TYPE_META = {
  status:  { label: "Durum",  cls: "bg-blue-100 text-blue-700" },
  payment: { label: "Ödeme",  cls: "bg-green-100 text-green-700" },
  cargo:   { label: "Kargo",  cls: "bg-indigo-100 text-indigo-700" },
  note:    { label: "Not",    cls: "bg-amber-100 text-amber-700" },
  invoice: { label: "Fatura", cls: "bg-purple-100 text-purple-700" },
  return:  { label: "İade",   cls: "bg-red-100 text-red-700" },
};

function fmt(ts) {
  if (!ts) return "";
  try {
    return new Date(ts).toLocaleString("tr-TR", {
      day: "2-digit", month: "2-digit", year: "numeric",
      hour: "2-digit", minute: "2-digit",
    });
  } catch {
    return ts;
  }
}

/**
 * Sipariş İşlem Geçmişi (Log)
 * ---------------------------
 * order_events kaydını gösteren, kendi içinde kapalı bileşen.
 * orderId değişince /orders/{id}/events ucundan çeker (açılınca lazy-load).
 * Sipariş detay modalının altına tek satırla eklenir.
 */
export default function OrderEventsLog({ orderId }) {
  const [open, setOpen] = useState(false);
  const [events, setEvents] = useState([]);
  const [loading, setLoading] = useState(false);
  const [loaded, setLoaded] = useState(false);

  // orderId değişince durumu sıfırla
  useEffect(() => {
    setEvents([]);
    setLoaded(false);
    setOpen(false);
  }, [orderId]);

  const load = async () => {
    if (!orderId || loading) return;
    setLoading(true);
    try {
      const token = localStorage.getItem("token");
      const res = await axios.get(`${API}/orders/${orderId}/events`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      setEvents(res.data?.events || []);
      setLoaded(true);
    } catch {
      setEvents([]);
      setLoaded(true);
    } finally {
      setLoading(false);
    }
  };

  const toggle = () => {
    const next = !open;
    setOpen(next);
    if (next && !loaded) load();
  };

  return (
    <div className="border rounded-lg">
      <button
        type="button"
        onClick={toggle}
        className="w-full flex items-center justify-between px-4 py-3 text-sm font-medium text-gray-700 hover:bg-gray-50"
      >
        <span>İşlem Geçmişi (Log){loaded ? ` · ${events.length}` : ""}</span>
        <span className="text-gray-400">{open ? "▲" : "▼"}</span>
      </button>

      {open && (
        <div className="px-4 pb-4">
          {loading && (
            <div className="text-sm text-gray-500 py-2">Yükleniyor…</div>
          )}
          {!loading && loaded && events.length === 0 && (
            <div className="text-sm text-gray-400 py-2">Henüz kayıtlı işlem yok.</div>
          )}
          {!loading && events.length > 0 && (
            <ol className="relative border-l border-gray-200 ml-2 space-y-3 py-2">
              {events.map((ev) => {
                const meta = TYPE_META[ev.event_type] || {
                  label: ev.event_type || "Olay",
                  cls: "bg-gray-100 text-gray-600",
                };
                return (
                  <li key={ev.id} className="ml-4 relative">
                    <span className="absolute -left-[21px] w-2.5 h-2.5 bg-gray-300 rounded-full mt-1.5" />
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className={`text-[11px] px-2 py-0.5 rounded-full ${meta.cls}`}>
                        {meta.label}
                      </span>
                      <span className="text-sm text-gray-800">{ev.description}</span>
                    </div>
                    <div className="text-xs text-gray-400 mt-0.5">
                      {fmt(ev.created_at)}{ev.actor ? ` · ${ev.actor}` : ""}
                    </div>
                  </li>
                );
              })}
            </ol>
          )}
        </div>
      )}
    </div>
  );
}
