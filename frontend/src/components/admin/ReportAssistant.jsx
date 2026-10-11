/**
 * ReportAssistant.jsx — Raporlar sayfalarının "Bana Sor" asistanı.
 *
 * Canlı destek balonu gibi sağ altta durur. Yönetici yazarak ya da SESLİ sorar;
 * arka uç (routes/report_assistant.py) sistemin kendi rapor araçlarını çağırarak
 * cevaplar (önce Gemini, olmazsa ChatGPT). Rakamlar modelden değil rapor
 * hesaplarından gelir; müşteri kişisel verisi modele gitmez.
 */
import { useState, useRef, useEffect, Fragment } from "react";
import axios from "axios";
import { MessageCircle, X, Send, Mic, Square, Loader2, Trash2, FileSpreadsheet } from "lucide-react";

const API = `${process.env.REACT_APP_BACKEND_URL}/api/admin/reports/assistant`;
const STORE_KEY = "report_assistant_chat_v1";
const ORNEKLER = [
  "Bu ay Trendyol'un net satışı ne kadar?",
  "Bu ayı geçen ayla kanal kanal kıyasla",
  "Bu ay en çok iade edilen 10 ürün",
  "Dün hangi ürünlerden kaç adet sattık?",
  "Satışlar en çok hangi saatlerde geliyor?",
  "Bu ayın sipariş listesini Excel olarak ver",
];

const auth = () => ({ headers: { Authorization: `Bearer ${localStorage.getItem("token")}` } });

// Asistanın ürettiği Excel'i yetkili istekle indirir (dosya ucu admin korumalı).
async function indir(f, setErr) {
  try {
    const r = await axios.get(`${API}/file/${f.id}`, { ...auth(), responseType: "blob" });
    const url = URL.createObjectURL(r.data);
    const a = document.createElement("a");
    a.href = url;
    a.download = f.name || "rapor.xlsx";
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 2000);
  } catch (e) {
    setErr(e.response?.status === 410 ? "Dosyanın süresi doldu, yeniden isteyin" : "Dosya indirilemedi");
  }
}

function loadChat() {
  try {
    const v = JSON.parse(sessionStorage.getItem(STORE_KEY) || "[]");
    return Array.isArray(v) ? v : [];
  } catch (e) {
    return [];
  }
}

// ── Küçük markdown: **kalın**, madde işaretleri, tablolar ─────────────────────
function Inline({ text }) {
  const parts = String(text).split(/(\*\*[^*]+\*\*)/g);
  return parts.map((p, i) =>
    p.startsWith("**") && p.endsWith("**")
      ? <strong key={i}>{p.slice(2, -2)}</strong>
      : <Fragment key={i}>{p}</Fragment>);
}

function Markdown({ text }) {
  const lines = String(text || "").split("\n");
  const blocks = [];
  let i = 0;
  while (i < lines.length) {
    const l = lines[i];
    if (/^\s*\|.*\|\s*$/.test(l)) {
      const rows = [];
      while (i < lines.length && /^\s*\|.*\|\s*$/.test(lines[i])) {
        if (!/^\s*\|[\s:|-]+\|\s*$/.test(lines[i])) {
          rows.push(lines[i].trim().replace(/^\||\|$/g, "").split("|").map((c) => c.trim()));
        }
        i += 1;
      }
      blocks.push(
        <div key={`t${i}`} className="overflow-x-auto my-1.5">
          <table className="text-[11px] border-collapse min-w-full">
            <tbody>
              {rows.map((r, ri) => (
                <tr key={ri} className={ri === 0 ? "bg-gray-100 font-semibold" : "border-t border-gray-100"}>
                  {r.map((c, ci) => (
                    <td key={ci} className={`px-1.5 py-1 whitespace-nowrap ${ci > 0 ? "text-right tabular-nums" : ""}`}>
                      <Inline text={c} />
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>,
      );
      continue;
    }
    if (/^\s*[-*•]\s+/.test(l)) {
      blocks.push(
        <div key={`l${i}`} className="flex gap-1.5 pl-1">
          <span>•</span><span><Inline text={l.replace(/^\s*[-*•]\s+/, "")} /></span>
        </div>,
      );
    } else if (/^#{1,4}\s+/.test(l)) {
      blocks.push(<div key={`h${i}`} className="font-semibold mt-1"><Inline text={l.replace(/^#{1,4}\s+/, "")} /></div>);
    } else if (l.trim() === "") {
      blocks.push(<div key={`b${i}`} className="h-1.5" />);
    } else {
      blocks.push(<div key={`p${i}`}><Inline text={l} /></div>);
    }
    i += 1;
  }
  return <div className="space-y-0.5">{blocks}</div>;
}

export default function ReportAssistant() {
  const [open, setOpen] = useState(false);
  const [msgs, setMsgs] = useState(loadChat);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [recording, setRecording] = useState(false);
  const [recSec, setRecSec] = useState(0);
  const [err, setErr] = useState("");
  const recRef = useRef(null);
  const chunksRef = useRef([]);
  const timerRef = useRef(null);
  const endRef = useRef(null);
  // Ses kaydı bittiğinde çalışan geri çağırma eski render'ın mesaj listesini görmesin.
  const msgsRef = useRef(msgs);
  msgsRef.current = msgs;

  useEffect(() => {
    try { sessionStorage.setItem(STORE_KEY, JSON.stringify(msgs.slice(-30))); } catch (e) { /* özel pencere */ }
    if (endRef.current) endRef.current.scrollIntoView({ behavior: "smooth" });
  }, [msgs, open]);

  // Bileşen kapanırken kaydı ve sayacı durdur. Ref NESNELERİ efekt gövdesinde yerel
  // değişkene alınır (CI=true: temizlemede doğrudan xRef.current okumak lint hatası).
  useEffect(() => {
    const timers = timerRef;
    const recs = recRef;
    return () => {
      clearInterval(timers.current);
      const r = recs.current;
      if (r && r.state !== "inactive") { try { r.stop(); } catch (e) { /* zaten durdu */ } }
    };
  }, []);

  // 90 saniye sınırı: sayaç state güncelleyicisinin İÇİNDE yan etki yapmasın diye ayrı efekt.
  useEffect(() => {
    if (recording && recSec >= 90 && recRef.current?.state === "recording") recRef.current.stop();
  }, [recording, recSec]);

  const ask = async (text) => {
    const q = String(text || "").trim();
    if (!q || busy) return;
    setErr("");
    const next = [...msgsRef.current, { role: "user", text: q }];
    setMsgs(next);
    setInput("");
    setBusy(true);
    try {
      const res = await axios.post(`${API}/chat`, { messages: next.slice(-12) }, auth());
      setMsgs((m) => [...m, { role: "assistant", text: res.data?.answer || "", provider: res.data?.provider,
                               files: Array.isArray(res.data?.files) ? res.data.files : [] }]);
    } catch (e) {
      const d = e.response?.data?.detail || "Asistan şu an cevap veremedi";
      setMsgs((m) => [...m, { role: "assistant", text: `⚠️ ${d}`, error: true }]);
    } finally {
      setBusy(false);
    }
  };

  const startRec = async () => {
    setErr("");
    if (!navigator.mediaDevices?.getUserMedia || typeof window.MediaRecorder === "undefined") {
      setErr("Bu tarayıcı ses kaydını desteklemiyor");
      return;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const pref = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4", "audio/ogg;codecs=opus"]
        .find((t) => window.MediaRecorder.isTypeSupported?.(t));
      const rec = pref ? new window.MediaRecorder(stream, { mimeType: pref }) : new window.MediaRecorder(stream);
      chunksRef.current = [];
      rec.ondataavailable = (ev) => { if (ev.data && ev.data.size) chunksRef.current.push(ev.data); };
      rec.onstop = async () => {
        stream.getTracks().forEach((t) => t.stop());
        clearInterval(timerRef.current);
        setRecording(false);
        const blob = new Blob(chunksRef.current, { type: rec.mimeType || "audio/webm" });
        if (blob.size < 800) { setErr("Kayıt çok kısa"); return; }
        setBusy(true);
        try {
          const fd = new FormData();
          const ext = (rec.mimeType || "").includes("mp4") ? "m4a" : (rec.mimeType || "").includes("ogg") ? "ogg" : "webm";
          fd.append("audio", blob, `soru.${ext}`);
          const r = await axios.post(`${API}/transcribe`, fd, auth());
          setBusy(false);
          const t = (r.data?.text || "").trim();
          if (t) ask(t); else setErr("Ses anlaşılamadı, tekrar deneyin");
        } catch (e) {
          setBusy(false);
          setErr(e.response?.data?.detail || "Ses yazıya çevrilemedi");
        }
      };
      recRef.current = rec;
      rec.start();
      setRecording(true);
      setRecSec(0);
      timerRef.current = setInterval(() => setRecSec((s) => s + 1), 1000);
    } catch (e) {
      const n = e?.name || "";
      setErr(
        n === "NotAllowedError" || n === "SecurityError"
          ? "Mikrofon izni verilmedi — adres çubuğundaki kilit simgesinden mikrofona izin verip sayfayı yenileyin"
          : n === "NotFoundError" || n === "OverconstrainedError"
            ? "Mikrofon bulunamadı — bir mikrofon bağlı mı?"
            : n === "NotReadableError"
              ? "Mikrofon başka bir uygulama tarafından kullanılıyor"
              : "Ses kaydı başlatılamadı"
      );
    }
  };

  const stopRec = () => {
    const r = recRef.current;
    if (r && r.state === "recording") r.stop();
  };

  const clearChat = () => { setMsgs([]); setErr(""); };

  if (!open) {
    return (
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="fixed bottom-5 right-5 z-50 flex items-center gap-2 rounded-full bg-gray-900 text-white px-4 py-3 shadow-lg hover:bg-black"
        data-testid="report-assistant-open"
      >
        <MessageCircle size={18} />
        <span className="text-sm font-semibold">Bana Sor</span>
      </button>
    );
  }

  return (
    <div className="fixed bottom-0 right-0 sm:bottom-5 sm:right-5 z-50 w-full sm:w-[400px] h-[85vh] sm:h-[600px] bg-white sm:rounded-2xl shadow-2xl border border-gray-200 flex flex-col"
         data-testid="report-assistant">
      <div className="flex items-center justify-between px-4 py-3 border-b bg-gray-900 text-white sm:rounded-t-2xl">
        <div>
          <div className="text-sm font-semibold">Rapor Asistanı</div>
          <div className="text-[10px] opacity-70">Raporlar hakkında yazarak ya da sesli sorun</div>
        </div>
        <div className="flex items-center gap-1">
          {msgs.length > 0 && (
            <button type="button" onClick={clearChat} className="p-1.5 rounded hover:bg-white/10" title="Sohbeti temizle">
              <Trash2 size={15} />
            </button>
          )}
          <button type="button" onClick={() => setOpen(false)} className="p-1.5 rounded hover:bg-white/10" title="Kapat">
            <X size={17} />
          </button>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto px-3 py-3 space-y-2.5 text-sm">
        {msgs.length === 0 && (
          <div className="space-y-2">
            <div className="text-gray-600 text-[13px]">
              Sipariş, ciro, iade, iptal, ürün, kanal, gün/saat, il, stok… ne isterseniz sorun.
              Rakamlar raporlar ekranının hesaplarından gelir.
            </div>
            <div className="flex flex-wrap gap-1.5">
              {ORNEKLER.map((o) => (
                <button key={o} type="button" onClick={() => ask(o)}
                        className="text-[12px] rounded-full border border-gray-200 bg-gray-50 px-3 py-1.5 hover:bg-gray-100 text-left">
                  {o}
                </button>
              ))}
            </div>
          </div>
        )}
        {msgs.map((m, i) => (
          <div key={i} className={`flex ${m.role === "user" ? "justify-end" : "justify-start"}`}>
            <div className={`max-w-[90%] rounded-2xl px-3 py-2 leading-relaxed ${
              m.role === "user" ? "bg-gray-900 text-white rounded-br-sm"
                : m.error ? "bg-red-50 text-red-800 rounded-bl-sm" : "bg-gray-100 text-gray-900 rounded-bl-sm"}`}>
              {m.role === "user" ? <div className="whitespace-pre-wrap">{m.text}</div> : <Markdown text={m.text} />}
              {(m.files || []).map((f) => (
                <button key={f.id} type="button" onClick={() => indir(f, setErr)}
                  data-testid="assistant-excel-download"
                  className="mt-2 flex items-center gap-2 w-full text-left bg-emerald-600 hover:bg-emerald-700 text-white rounded-lg px-3 py-2 text-[12px] font-medium">
                  <FileSpreadsheet size={16} className="shrink-0" />
                  <span className="truncate">Excel'i indir · {f.name}</span>
                </button>
              ))}
              {m.provider && <div className="text-[9px] opacity-50 mt-1">{m.provider}</div>}
            </div>
          </div>
        ))}
        {busy && (
          <div className="flex items-center gap-2 text-gray-500 text-[12px]">
            <Loader2 size={14} className="animate-spin" /> Raporlara bakıyorum…
          </div>
        )}
        <div ref={endRef} />
      </div>

      {err && <div className="px-3 py-1.5 text-[12px] text-red-700 bg-red-50 border-t border-red-100">{err}</div>}

      <form className="border-t p-2.5 flex items-end gap-2"
            onSubmit={(e) => { e.preventDefault(); ask(input); }}>
        {recording ? (
          <div className="flex-1 flex items-center gap-2 rounded-xl border border-red-200 bg-red-50 px-3 py-2.5 text-[13px] text-red-700">
            <span className="h-2 w-2 rounded-full bg-red-600 animate-pulse" />
            Dinliyorum… {recSec}s
          </div>
        ) : (
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); ask(input); } }}
            rows={1}
            placeholder="Sorunuzu yazın…"
            className="flex-1 resize-none rounded-xl border border-gray-200 px-3 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-gray-900/10 max-h-28"
            disabled={busy}
          />
        )}
        {recording ? (
          <button type="button" onClick={stopRec} className="rounded-xl bg-red-600 text-white p-2.5" title="Kaydı bitir ve gönder">
            <Square size={18} />
          </button>
        ) : (
          <button type="button" onClick={startRec} disabled={busy}
                  className="rounded-xl border border-gray-200 p-2.5 text-gray-700 hover:bg-gray-50 disabled:opacity-40" title="Sesli sor">
            <Mic size={18} />
          </button>
        )}
        {!recording && (
          <button type="submit" disabled={busy || !input.trim()}
                  className="rounded-xl bg-gray-900 text-white p-2.5 disabled:opacity-40" title="Gönder">
            <Send size={18} />
          </button>
        )}
      </form>
    </div>
  );
}
