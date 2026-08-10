/**
 * AIAssistant.jsx — Akıllı Müşteri Yanıtlayıcı (Iter38)
 * Tabs:
 * - Sohbet: bota direkt yaz, S:/C: çiftleri ya da talimat olarak öğret
 * - Bilgi Bankası: KB list/search/delete
 * - Toplu Eğitim: geçmiş ANSWERED Q&A'dan KB üret
 * - Otomatik Yanıt: bekleyen sorulara batch draft + send
 */
import { useEffect, useState, useRef } from "react";
import axios from "axios";
import { toast } from "sonner";
import {
  Brain, Send, Database, Zap, MessageCircle, Trash2, RefreshCw,
  CheckCircle, AlertTriangle, Sparkles, Search, Bot, ShieldCheck, Key, Save, Lock,
  Tag, Plus, X
} from "lucide-react";

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;
const auth = () => ({ headers: { Authorization: `Bearer ${localStorage.getItem("token")}` } });

const Tab = ({ id, label, icon: Icon, activeId, onClick, badge }) => (
  <button
    data-testid={`ai-tab-${id}`}
    onClick={() => onClick(id)}
    className={`flex items-center gap-2 px-4 py-2 text-sm border-b-2 transition-colors ${
      activeId === id ? "border-black text-black font-semibold" : "border-transparent text-gray-500 hover:text-black"
    }`}
  >
    <Icon className="w-4 h-4" /> {label}
    {badge !== undefined && (
      <span className="ml-1 bg-gray-100 text-xs px-1.5 py-0.5 rounded">{badge}</span>
    )}
  </button>
);

export default function AIAssistant() {
  const [tab, setTab] = useState("chat");
  const [stats, setStats] = useState(null);
  const [trainStatus, setTrainStatus] = useState(null);

  const loadStats = async () => {
    try {
      const [s1, s2] = await Promise.all([
        axios.get(`${API}/ai-assistant/auto-answer-stats`, auth()),
        axios.get(`${API}/ai-assistant/bulk-train-status`, auth()),
      ]);
      setStats(s1.data);
      setTrainStatus(s2.data);
    } catch (e) { /* ignore */ }
  };
  useEffect(() => { loadStats(); }, []);

  return (
    <div data-testid="ai-assistant-page" className="space-y-5 p-6 max-w-[1400px] mx-auto">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="bg-black p-2.5 rounded-lg"><Brain className="w-5 h-5 text-white"/></div>
          <div>
            <h1 className="text-2xl font-light tracking-tight">AI Asistan</h1>
            <p className="text-sm text-gray-500">Sohbet ile eğit • Otomatik yanıt • Bilgi bankası</p>
          </div>
        </div>
        <div className="flex gap-3">
          <div className="text-right text-xs">
            <div className="text-gray-400">Bekleyen Trendyol</div>
            <div className="text-2xl font-light">{stats?.pending_trendyol ?? "—"}</div>
          </div>
          <div className="text-right text-xs">
            <div className="text-gray-400">KB toplam</div>
            <div className="text-2xl font-light">{trainStatus?.kb_total ?? "—"}</div>
          </div>
          <div className="text-right text-xs">
            <div className="text-gray-400">Bugün AI yanıt</div>
            <div className="text-2xl font-light">{stats?.auto_answered_today ?? "—"}</div>
          </div>
        </div>
      </div>

      <div className="border-b border-gray-200 flex gap-3">
        <Tab id="chat" label="Sohbet ile Eğit" icon={MessageCircle} activeId={tab} onClick={setTab} />
        <Tab id="kb" label="Bilgi Bankası" icon={Database} activeId={tab} onClick={setTab} badge={trainStatus?.kb_total} />
        <Tab id="train" label="Toplu Eğitim" icon={Sparkles} activeId={tab} onClick={setTab} />
        <Tab id="auto" label="Otomatik Yanıt" icon={Zap} activeId={tab} onClick={setTab} badge={stats?.pending_trendyol} />
        <Tab id="prodqa" label="Ürüne Özel Yanıt" icon={Tag} activeId={tab} onClick={setTab} />
        <Tab id="key" label="API Anahtarı" icon={Key} activeId={tab} onClick={setTab} />
      </div>

      {tab === "chat" && <ChatTab onUpdate={loadStats} />}
      {tab === "kb" && <KbTab />}
      {tab === "train" && <TrainTab status={trainStatus} onUpdate={loadStats} />}
      {tab === "auto" && <AutoTab stats={stats} onUpdate={loadStats} />}
      {tab === "prodqa" && <ProdQaTab />}
      {tab === "key" && <KeyTab />}
    </div>
  );
}


/* ============================================================
   CHAT TAB — Direct teaching
   ============================================================ */
function ChatTab({ onUpdate }) {
  const [messages, setMessages] = useState([
    { role: "system",
      text: "Merhaba! Ben Facette AI Asistan. Beni 3 şekilde eğitebilirsin:\n\n" +
            "1. Soru-Cevap çifti — örn: 'S: Kargo kaç günde gelir? C: 2-3 iş günü içinde teslim edilir.'\n" +
            "2. Talimat — örn: 'Müşteriye her zaman çok kibar ol' veya 'XL bedeni 42 numara olarak söyle'\n" +
            "3. Düz soru — bana bir soru sor, cevaplayayım, doğruysa kaydet diyebilirsin.\n\n" +
            "Ne öğretmek istersin?"
    }
  ]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const sessionId = useRef(`admin-chat-${Date.now()}`);
  const endRef = useRef(null);

  const send = async () => {
    if (!input.trim()) return;
    const text = input.trim();
    setInput("");
    setMessages((m) => [...m, { role: "user", text }]);
    setLoading(true);
    try {
      const res = await axios.post(`${API}/ai-assistant/chat`,
        { text, session_id: sessionId.current }, auth());
      const r = res.data;
      let badge = "";
      if (r.kb_added) badge = "KB'ye eklendi ✓";
      else if (r.instruction_saved) badge = "Talimat kaydedildi ✓";
      setMessages((m) => [...m, {
        role: "bot",
        text: r.reply,
        intent: r.intent,
        badge,
        kb_q: r.kb_question,
        kb_a: r.kb_answer,
        instruction: r.instruction,
      }]);
      if (r.kb_added || r.instruction_saved) onUpdate?.();
    } catch (e) {
      toast.error(e.response?.data?.detail || e.message);
    } finally {
      setLoading(false);
      setTimeout(() => endRef.current?.scrollIntoView({ behavior: "smooth" }), 100);
    }
  };

  const quickPrompts = [
    { label: "Kargo süresi", text: "S: Kargo kaç günde gelir? C: Siparişleriniz 2-3 iş günü içinde kargoya verilir, 1-2 gün sonra elinize ulaşır." },
    { label: "Beden tablosu", text: "Talimat: XL bedeni 42-44 numara olarak söyle, S 36-38, M 38-40, L 40-42." },
    { label: "İade politikası", text: "S: İade nasıl yapılır? C: Ürünü orijinal ambalajıyla 14 gün içinde kargo ile gönderebilirsiniz, ücretsiz iade kodumuz: FACETTE." },
  ];

  return (
    <div className="grid grid-cols-1 lg:grid-cols-4 gap-4">
      <div className="lg:col-span-3">
        <div data-testid="ai-chat-window" className="bg-white border border-gray-200 rounded-lg h-[60vh] overflow-y-auto p-4 space-y-3">
          {messages.map((m, i) => (
            <div key={i} className={`flex ${m.role === "user" ? "justify-end" : "justify-start"}`}>
              <div className={`max-w-[85%] rounded-lg px-3 py-2 ${
                m.role === "user" ? "bg-black text-white" :
                m.role === "system" ? "bg-amber-50 border border-amber-200 text-gray-800" :
                "bg-gray-100 text-gray-900"
              }`}>
                {m.role === "bot" && (
                  <div className="flex items-center gap-1 mb-1">
                    <Bot className="w-3 h-3 opacity-60"/>
                    <span className="text-xs opacity-60">{m.intent || "AI"}</span>
                    {m.badge && <span className="ml-2 text-xs bg-green-200 text-green-900 px-2 rounded">{m.badge}</span>}
                  </div>
                )}
                <div className="text-sm whitespace-pre-wrap">{m.text}</div>
                {(m.kb_q && m.kb_a) && (
                  <div className="mt-2 text-xs bg-white/40 rounded p-2 border border-white/30">
                    <div><b>S:</b> {m.kb_q}</div>
                    <div><b>C:</b> {m.kb_a}</div>
                  </div>
                )}
                {m.instruction && (
                  <div className="mt-2 text-xs bg-blue-50 text-blue-900 rounded p-2">
                    <ShieldCheck className="w-3 h-3 inline mr-1"/>{m.instruction}
                  </div>
                )}
              </div>
            </div>
          ))}
          {loading && (
            <div className="flex justify-start">
              <div className="bg-gray-100 rounded-lg px-3 py-2 text-sm">
                <Bot className="w-3 h-3 inline mr-2 animate-pulse"/>Yazıyor...
              </div>
            </div>
          )}
          <div ref={endRef}/>
        </div>

        <div className="mt-3 flex gap-2">
          <input
            data-testid="ai-chat-input"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && !e.shiftKey && (e.preventDefault(), send())}
            placeholder="Bota öğret veya sor... (Enter=gönder)"
            className="flex-1 border border-gray-300 rounded px-3 py-2 text-sm"
            disabled={loading}
          />
          <button data-testid="ai-chat-send" onClick={send} disabled={loading || !input.trim()}
            className="bg-black hover:bg-gray-800 text-white rounded px-4 text-sm flex items-center gap-2 disabled:opacity-50">
            <Send className="w-4 h-4"/> Gönder
          </button>
        </div>
      </div>

      <div className="space-y-2">
        <h3 className="text-sm font-semibold text-gray-600">Hızlı Örnekler</h3>
        {quickPrompts.map((q, i) => (
          <button key={i}
            data-testid={`ai-quick-${i}`}
            onClick={() => setInput(q.text)}
            className="w-full text-left border border-gray-200 rounded p-2 text-xs hover:bg-gray-50">
            <div className="font-semibold">{q.label}</div>
            <div className="text-gray-500 line-clamp-2">{q.text}</div>
          </button>
        ))}
      </div>
    </div>
  );
}


/* ============================================================
   KB TAB — list/delete
   ============================================================ */
function KbTab() {
  const [items, setItems] = useState([]);
  const [search, setSearch] = useState("");
  const [loading, setLoading] = useState(false);
  const [adding, setAdding] = useState({ question: "", answer: "" });

  const load = async () => {
    setLoading(true);
    try {
      const res = await axios.get(`${API}/ai/kb${search ? `?q=${encodeURIComponent(search)}` : ""}`, auth());
      setItems(res.data.items || []);
    } catch (e) {
      toast.error(e.message);
    } finally { setLoading(false); }
  };
  useEffect(() => { load(); /* eslint-disable-next-line */ }, []);

  const remove = async (id) => {
    if (!confirm("Bu KB kaydını silmek istediğinize emin misiniz?")) return;
    try {
      await axios.delete(`${API}/ai/kb/${id}`, auth());
      toast.success("Silindi");
      load();
    } catch (e) { toast.error(e.message); }
  };

  const addEntry = async () => {
    if (!adding.question.trim() || !adding.answer.trim()) {
      toast.error("Soru ve cevap zorunlu"); return;
    }
    try {
      await axios.post(`${API}/ai/kb`, adding, auth());
      toast.success("Eklendi");
      setAdding({ question: "", answer: "" });
      load();
    } catch (e) { toast.error(e.response?.data?.detail || e.message); }
  };

  return (
    <div className="space-y-4">
      <div className="bg-white border border-gray-200 rounded-lg p-4">
        <h3 className="text-sm font-semibold mb-2">Yeni KB Kaydı</h3>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
          <textarea data-testid="kb-add-q" rows={2} placeholder="Soru..."
            value={adding.question}
            onChange={(e) => setAdding({...adding, question: e.target.value})}
            className="border border-gray-300 rounded px-2 py-1.5 text-sm"/>
          <textarea data-testid="kb-add-a" rows={2} placeholder="Cevap..."
            value={adding.answer}
            onChange={(e) => setAdding({...adding, answer: e.target.value})}
            className="border border-gray-300 rounded px-2 py-1.5 text-sm"/>
        </div>
        <div className="text-right mt-2">
          <button data-testid="kb-add-btn" onClick={addEntry}
            className="bg-black text-white px-4 py-1.5 rounded text-sm">Ekle</button>
        </div>
      </div>

      <div className="flex gap-2">
        <div className="relative flex-1">
          <Search className="w-4 h-4 absolute left-2 top-2.5 text-gray-400"/>
          <input data-testid="kb-search" value={search}
            onChange={(e) => setSearch(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && load()}
            placeholder="Bilgi bankasında ara..."
            className="border border-gray-300 rounded pl-8 pr-2 py-1.5 text-sm w-full"/>
        </div>
        <button onClick={load} className="border border-gray-300 rounded px-3 text-sm flex items-center gap-1">
          <RefreshCw className={`w-4 h-4 ${loading ? "animate-spin" : ""}`}/>
        </button>
      </div>

      <div className="bg-white border border-gray-200 rounded-lg overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-gray-50 border-b border-gray-200">
            <tr>
              <th className="text-left px-3 py-2 text-xs font-medium">Soru</th>
              <th className="text-left px-3 py-2 text-xs font-medium">Cevap</th>
              <th className="text-left px-3 py-2 text-xs font-medium">Etiket</th>
              <th className="text-center px-3 py-2 text-xs font-medium">Kullanım</th>
              <th className="px-3 py-2"/>
            </tr>
          </thead>
          <tbody>
            {items.length === 0 ? (
              <tr><td colSpan={5} className="text-center text-gray-400 py-8">Kayıt yok</td></tr>
            ) : items.map((it) => (
              <tr key={it.id} className="border-b border-gray-100 hover:bg-gray-50">
                <td className="px-3 py-1.5 max-w-md truncate">{it.question}</td>
                <td className="px-3 py-1.5 max-w-md truncate text-gray-600">{it.answer}</td>
                <td className="px-3 py-1.5">
                  {(it.tags || []).map((t, i) => (
                    <span key={i} className="px-1.5 py-0.5 bg-gray-100 rounded text-xs mr-1">{t}</span>
                  ))}
                </td>
                <td className="px-3 py-1.5 text-center font-mono">{it.usage_count || 0}</td>
                <td className="px-3 py-1.5 text-right">
                  <button data-testid={`kb-delete-${it.id}`} onClick={() => remove(it.id)}
                    className="text-red-500 hover:text-red-700">
                    <Trash2 className="w-4 h-4"/>
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}


/* ============================================================
   TRAIN TAB — bulk train from history
   ============================================================ */
function TrainTab({ status, onUpdate }) {
  const [cfg, setCfg] = useState({ channel: "trendyol", min_answer_length: 30, skip_existing: true, max_count: 1000 });
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState(null);

  const run = async () => {
    if (!confirm(`Toplu eğitim başlatılsın mı? Kanal: ${cfg.channel}, max ${cfg.max_count} kayıt`)) return;
    setLoading(true);
    try {
      const res = await axios.post(`${API}/ai-assistant/bulk-train`, cfg, auth());
      setResult(res.data);
      toast.success(`${res.data.inserted} kayıt KB'ye eklendi`);
      onUpdate?.();
    } catch (e) { toast.error(e.response?.data?.detail || e.message); }
    finally { setLoading(false); }
  };

  return (
    <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
      <div className="bg-white border border-gray-200 rounded-lg p-5 space-y-3">
        <h3 className="font-semibold flex items-center gap-2"><Sparkles className="w-4 h-4 text-amber-600"/> Geçmişten Otomatik Eğitim</h3>
        <p className="text-sm text-gray-600">
          Geçmişte yanıtlanmış (ANSWERED) müşteri sorularını tarayarak Bilgi Bankası'na otomatik aktar.
          AI cevap üretirken bunları referans alır.
        </p>
        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className="text-xs text-gray-600">Kanal</label>
            <select data-testid="train-channel" value={cfg.channel}
              onChange={(e) => setCfg({...cfg, channel: e.target.value})}
              className="w-full border border-gray-300 rounded px-2 py-1.5 text-sm">
              <option value="trendyol">Trendyol</option>
              <option value="hepsiburada">Hepsiburada</option>
              <option value="temu">Temu</option>
              <option value="all">Tümü</option>
            </select>
          </div>
          <div>
            <label className="text-xs text-gray-600">Min Cevap Uzunluğu</label>
            <input data-testid="train-min-len" type="number" value={cfg.min_answer_length}
              onChange={(e) => setCfg({...cfg, min_answer_length: parseInt(e.target.value, 10) || 30})}
              className="w-full border border-gray-300 rounded px-2 py-1.5 text-sm"/>
          </div>
          <div>
            <label className="text-xs text-gray-600">Maks Kayıt</label>
            <input data-testid="train-max" type="number" value={cfg.max_count}
              onChange={(e) => setCfg({...cfg, max_count: parseInt(e.target.value, 10) || 1000})}
              className="w-full border border-gray-300 rounded px-2 py-1.5 text-sm"/>
          </div>
          <div className="flex items-end">
            <label className="flex items-center gap-2 text-sm">
              <input data-testid="train-skip" type="checkbox" checked={cfg.skip_existing}
                onChange={(e) => setCfg({...cfg, skip_existing: e.target.checked})}/>
              Mevcutları atla
            </label>
          </div>
        </div>
        <button data-testid="train-run-btn" onClick={run} disabled={loading}
          className="w-full bg-black hover:bg-gray-800 text-white rounded px-4 py-2 text-sm font-medium disabled:opacity-50 flex items-center justify-center gap-2">
          {loading ? <RefreshCw className="w-4 h-4 animate-spin"/> : <Sparkles className="w-4 h-4"/>}
          {loading ? "Çalışıyor..." : "Toplu Eğitimi Başlat"}
        </button>

        {result && (
          <div className="bg-green-50 border border-green-200 rounded p-3 text-xs space-y-1">
            <div className="flex items-center gap-2 font-semibold text-green-800">
              <CheckCircle className="w-4 h-4"/> Eğitim Tamamlandı
            </div>
            <div>Tarandı: <b>{result.scanned}</b></div>
            <div>KB'ye eklendi: <b>{result.inserted}</b></div>
            <div>Kısa cevap atlandı: {result.skipped_short}</div>
            <div>Mevcut kayıt atlandı: {result.skipped_existing}</div>
          </div>
        )}
      </div>

      <div className="bg-white border border-gray-200 rounded-lg p-5">
        <h3 className="font-semibold mb-3">Mevcut Durum</h3>
        <div className="space-y-2 text-sm">
          <div className="flex justify-between"><span>KB Toplam</span><b>{status?.kb_total || 0}</b></div>
          <div className="flex justify-between"><span>Sohbetle Eğitilmiş</span><b>{status?.kb_chat_trained || 0}</b></div>
          <div className="flex justify-between"><span>Toplu Eğitilmiş</span><b>{status?.kb_bulk_trained || 0}</b></div>
        </div>
        {status?.last_run && (
          <div className="mt-4 pt-3 border-t text-xs text-gray-500">
            <div>Son çalışma: {status.last_run.ran_at?.slice(0,16).replace("T"," ")}</div>
            <div>Çalıştıran: {status.last_run.ran_by}</div>
            <div>Eklenen: {status.last_run.inserted}</div>
          </div>
        )}
      </div>
    </div>
  );
}


/* ============================================================
   AUTO TAB — auto answer batch
   ============================================================ */
function AutoTab({ stats, onUpdate }) {
  const [cfg, setCfg] = useState({ channel: "trendyol", max_count: 10, min_confidence: 0.85, dry_run: true, send: false });
  const [loading, setLoading] = useState(false);
  const [results, setResults] = useState([]);

  const run = async () => {
    const willSend = !cfg.dry_run && cfg.send;
    if (willSend && !confirm(`Bu işlem confidence ≥ ${cfg.min_confidence} olan cevapları GERÇEKTEN Trendyol'a gönderecek. Devam?`)) return;
    setLoading(true);
    try {
      const res = await axios.post(`${API}/ai-assistant/auto-answer-batch`, cfg, auth());
      setResults(res.data.results || []);
      toast.success(`${res.data.processed} soru işlendi, ${res.data.sent} gönderildi`);
      onUpdate?.();
    } catch (e) { toast.error(e.response?.data?.detail || e.message); }
    finally { setLoading(false); }
  };

  return (
    <div className="space-y-4">
      <div className="bg-white border border-gray-200 rounded-lg p-5">
        <h3 className="font-semibold flex items-center gap-2 mb-3"><Zap className="w-4 h-4 text-yellow-600"/> Otomatik Yanıt Ayarları</h3>
        <div className="grid grid-cols-1 md:grid-cols-4 gap-3">
          <div>
            <label className="text-xs text-gray-600">Kanal</label>
            <select data-testid="auto-channel" value={cfg.channel}
              onChange={(e) => setCfg({...cfg, channel: e.target.value})}
              className="w-full border border-gray-300 rounded px-2 py-1.5 text-sm">
              <option value="trendyol">Trendyol</option>
            </select>
          </div>
          <div>
            <label className="text-xs text-gray-600">Maks Soru</label>
            <input data-testid="auto-max" type="number" min="1" max="50" value={cfg.max_count}
              onChange={(e) => setCfg({...cfg, max_count: parseInt(e.target.value, 10) || 10})}
              className="w-full border border-gray-300 rounded px-2 py-1.5 text-sm"/>
          </div>
          <div>
            <label className="text-xs text-gray-600">Min Güven (0-1)</label>
            <input data-testid="auto-conf" type="number" min="0.5" max="1" step="0.05" value={cfg.min_confidence}
              onChange={(e) => setCfg({...cfg, min_confidence: parseFloat(e.target.value) || 0.85})}
              className="w-full border border-gray-300 rounded px-2 py-1.5 text-sm font-mono"/>
          </div>
          <div className="flex flex-col gap-1">
            <label className="flex items-center gap-2 text-sm">
              <input data-testid="auto-dryrun" type="checkbox" checked={cfg.dry_run}
                onChange={(e) => setCfg({...cfg, dry_run: e.target.checked, send: e.target.checked ? false : cfg.send})}/>
              Sadece Test (gönderme)
            </label>
            <label className="flex items-center gap-2 text-sm">
              <input data-testid="auto-send" type="checkbox" checked={cfg.send}
                disabled={cfg.dry_run}
                onChange={(e) => setCfg({...cfg, send: e.target.checked})}/>
              Gerçekten Gönder
            </label>
          </div>
        </div>
        <button data-testid="auto-run-btn" onClick={run} disabled={loading}
          className="mt-3 w-full bg-black hover:bg-gray-800 text-white rounded px-4 py-2 text-sm font-medium disabled:opacity-50 flex items-center justify-center gap-2">
          {loading ? <RefreshCw className="w-4 h-4 animate-spin"/> : <Zap className="w-4 h-4"/>}
          {loading ? "Çalışıyor..." : (cfg.send && !cfg.dry_run ? "Çalıştır + Gönder" : "Test Çalıştır")}
        </button>
      </div>

      {stats?.last_run && (
        <div className="bg-blue-50 border border-blue-200 rounded p-3 text-xs">
          Son çalışma: {stats.last_run.ran_at?.slice(0,16).replace("T"," ")} •
          İşlenen: {stats.last_run.processed} •
          Gönderilen: <b>{stats.last_run.sent}</b> •
          Kuyruklanan: {stats.last_run.queued}
        </div>
      )}

      {results.length > 0 && (
        <div className="bg-white border border-gray-200 rounded-lg overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-gray-50 border-b border-gray-200">
              <tr>
                <th className="text-left px-3 py-2 text-xs font-medium">Soru</th>
                <th className="text-left px-3 py-2 text-xs font-medium">AI Taslak</th>
                <th className="text-center px-3 py-2 text-xs font-medium">Güven</th>
                <th className="text-center px-3 py-2 text-xs font-medium">Yeterli</th>
                <th className="text-center px-3 py-2 text-xs font-medium">Aksiyon</th>
              </tr>
            </thead>
            <tbody>
              {results.map((r) => (
                <tr key={r.question_id} className="border-b border-gray-100 hover:bg-gray-50">
                  <td className="px-3 py-1.5 max-w-xs truncate">{r.question}</td>
                  <td className="px-3 py-1.5 max-w-md truncate text-gray-600">{r.draft}</td>
                  <td className="px-3 py-1.5 text-center font-mono">
                    <span className={r.confidence >= 0.85 ? "text-green-700" : "text-amber-700"}>
                      {(r.confidence * 100).toFixed(0)}%
                    </span>
                  </td>
                  <td className="px-3 py-1.5 text-center">
                    {r.is_sufficient ? <CheckCircle className="w-4 h-4 text-green-600 inline"/> :
                                       <AlertTriangle className="w-4 h-4 text-amber-600 inline"/>}
                  </td>
                  <td className="px-3 py-1.5 text-center">
                    <span className={`px-2 py-0.5 rounded text-xs ${
                      r.action === "sent" ? "bg-green-100 text-green-800" :
                      r.action === "queued" ? "bg-gray-100 text-gray-700" :
                      "bg-red-100 text-red-800"
                    }`}>
                      {r.action === "sent" ? "GÖNDERİLDİ" :
                       r.action === "queued" ? "KUYRUKTA" : r.action.toUpperCase()}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}


/* ============================================================
   API KEY TAB — LLM token (AES-256 şifreli, asla düz gösterilmez)
   ============================================================ */
const _KEY_DEFAULTS = {
  anthropic: { model: "claude-sonnet-4-6", fast: "claude-haiku-4-5", hint: "console.anthropic.com → API Keys (sk-ant-…)" },
  openai:    { model: "gpt-4o",            fast: "gpt-4o-mini",      hint: "platform.openai.com → API Keys (sk-…)" },
  gemini:    { model: "gemini-2.0-flash",  fast: "gemini-2.0-flash", hint: "aistudio.google.com → API Key" },
};

function KeyTab() {
  const [loaded, setLoaded] = useState(false);
  const [base, setBase] = useState({});
  const [hasKey, setHasKey] = useState(false);
  const [provider, setProvider] = useState("anthropic");
  const [model, setModel] = useState("");
  const [fastModel, setFastModel] = useState("");
  const [keyVal, setKeyVal] = useState("");
  const [show, setShow] = useState(false);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    (async () => {
      try {
        const res = await axios.get(`${API}/ai/settings`, auth());
        const d = res.data || {};
        setBase(d);
        setHasKey(!!d.has_api_key || d.custom_api_key === "********");
        if (d.provider) setProvider(d.provider);
        setModel(d.model || "");
        setFastModel(d.fast_model || "");
      } catch (e) { /* ignore */ }
      finally { setLoaded(true); }
    })();
  }, []);

  const save = async () => {
    if (!keyVal.trim() && !hasKey) { toast.error("Önce API anahtarını girin"); return; }
    setSaving(true);
    try {
      const def = _KEY_DEFAULTS[provider] || _KEY_DEFAULTS.anthropic;
      const payload = {
        ...base,
        provider,
        model: model || def.model,
        fast_model: fastModel || def.fast,
        use_emergent_key: false,
      };
      delete payload.has_api_key;
      if (keyVal.trim()) payload.custom_api_key = keyVal.trim();
      else delete payload.custom_api_key; // maskeyi geri gönderme
      await axios.post(`${API}/ai/settings`, payload, auth());
      if (keyVal.trim()) setHasKey(true);
      setKeyVal("");
      setShow(false);
      toast.success("API anahtarı kaydedildi (şifreli saklandı)");
    } catch (e) {
      toast.error(e.response?.data?.detail || "Kaydedilemedi");
    } finally { setSaving(false); }
  };

  if (!loaded) return <div className="text-sm text-gray-400 p-6">Yükleniyor…</div>;
  const def = _KEY_DEFAULTS[provider] || _KEY_DEFAULTS.anthropic;

  return (
    <div className="space-y-4 max-w-2xl">
      <div className="bg-white border border-gray-200 rounded-lg p-5 space-y-4">
        <div className="flex items-center justify-between">
          <h3 className="font-semibold flex items-center gap-2"><Key className="w-4 h-4 text-violet-600"/> LLM API Anahtarı</h3>
          {hasKey
            ? <span className="text-[11px] font-bold text-green-700 bg-green-100 px-2.5 py-1 rounded-full inline-flex items-center gap-1"><CheckCircle className="w-3 h-3"/> Tanımlı</span>
            : <span className="text-[11px] font-bold text-amber-700 bg-amber-100 px-2.5 py-1 rounded-full">Tanımlı değil</span>}
        </div>

        <p className="text-xs text-gray-500 leading-relaxed flex items-start gap-1.5">
          <Lock className="w-3.5 h-3.5 mt-0.5 shrink-0 text-gray-400"/>
          Anahtar <b>AES-256 ile şifrelenerek</b> saklanır; hiçbir ekranda, API yanıtında veya günlükte düz metin olarak görünmez. Sohbet, otomatik yanıt ve ürün açıklaması üretimi bu tek anahtarı ortak kullanır.
        </p>

        <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
          <div>
            <label className="text-xs font-bold text-gray-600 block mb-1">Sağlayıcı</label>
            <select value={provider} onChange={(e)=>setProvider(e.target.value)}
              className="w-full border border-gray-300 rounded px-2 py-2 text-sm bg-white">
              <option value="anthropic">Anthropic (Claude)</option>
              <option value="openai">OpenAI</option>
              <option value="gemini">Google Gemini</option>
            </select>
          </div>
          <div>
            <label className="text-xs font-bold text-gray-600 block mb-1">Model</label>
            <input value={model} onChange={(e)=>setModel(e.target.value)} placeholder={def.model}
              className="w-full border border-gray-300 rounded px-2 py-2 text-sm"/>
          </div>
          <div>
            <label className="text-xs font-bold text-gray-600 block mb-1">Hızlı Model</label>
            <input value={fastModel} onChange={(e)=>setFastModel(e.target.value)} placeholder={def.fast}
              className="w-full border border-gray-300 rounded px-2 py-2 text-sm"/>
          </div>
        </div>

        <div>
          <label className="text-xs font-bold text-gray-600 block mb-1">API Anahtarı</label>
          <div className="flex gap-2">
            <input type={show ? "text" : "password"} value={keyVal} onChange={(e)=>setKeyVal(e.target.value)}
              placeholder={hasKey ? "•••••••• — değiştirmek için yeni anahtar yapıştırın" : "API anahtarınızı yapıştırın"}
              autoComplete="new-password" data-testid="ai-api-key-input"
              className="flex-1 border border-gray-300 rounded px-3 py-2 text-sm font-mono"/>
            <button type="button" onClick={()=>setShow(s=>!s)}
              className="px-3 py-2 border border-gray-300 rounded text-xs hover:bg-gray-50 shrink-0">
              {show ? "Gizle" : "Göster"}
            </button>
          </div>
          <p className="text-[11px] text-gray-400 mt-1">{def.hint}</p>
        </div>

        <button onClick={save} disabled={saving} data-testid="ai-api-key-save"
          className="w-full bg-black hover:bg-gray-800 text-white rounded px-4 py-2.5 text-sm font-medium disabled:opacity-50 inline-flex items-center justify-center gap-2">
          {saving ? <RefreshCw className="w-4 h-4 animate-spin"/> : <Save className="w-4 h-4"/>}
          {saving ? "Kaydediliyor…" : "Kaydet"}
        </button>
      </div>
    </div>
  );
}

/* ──────────────────────────────────────────────────────────────────────────
   ÜRÜNE ÖZEL YANIT — bir ürün için AI'ın MUTLAKA uyacağı talimat + kalıp cevaplar
   (eşleşme ürün adı bazlı; TY/HB soruları product_name taşır). EN YÜKSEK ÖNCELİK.
   ────────────────────────────────────────────────────────────────────────── */
function ProdQaTab() {
  const [q, setQ] = useState("");
  const [results, setResults] = useState([]);
  const [selected, setSelected] = useState(null); // { id, name }
  const [instructions, setInstructions] = useState("");
  const [rules, setRules] = useState([]); // [{q,a}]
  const [list, setList] = useState([]);
  const [saving, setSaving] = useState(false);
  const [searching, setSearching] = useState(false);

  const loadList = async () => {
    try {
      const res = await axios.get(`${API}/ai/product-qa-rules`, auth());
      setList(res.data?.items || []);
    } catch { /* sessiz */ }
  };
  useEffect(() => { loadList(); }, []);

  const search = async () => {
    if (!q.trim()) { setResults([]); return; }
    setSearching(true);
    try {
      const res = await axios.get(`${API}/products?search=${encodeURIComponent(q.trim())}&limit=8`, auth());
      setResults(res.data?.products || []);
    } catch { toast.error("Ürün araması başarısız"); }
    setSearching(false);
  };

  const pick = async (p) => {
    setSelected({ id: p.id, name: p.name });
    setResults([]); setQ(p.name);
    try {
      const res = await axios.get(`${API}/ai/product-qa-rules/${p.id}`, auth());
      setInstructions(res.data?.instructions || "");
      setRules(res.data?.rules || []);
    } catch { setInstructions(""); setRules([]); }
  };

  const editExisting = (item) => {
    setSelected({ id: item.product_id, name: item.product_name });
    setQ(item.product_name);
    setInstructions(item.instructions || "");
    setRules(item.rules || []);
    setResults([]);
    window.scrollTo({ top: 0, behavior: "smooth" });
  };

  const removeExisting = async (pid) => {
    if (!window.confirm("Bu ürünün özel yanıt kuralı silinsin mi?")) return;
    try { await axios.delete(`${API}/ai/product-qa-rules/${pid}`, auth()); toast.success("Silindi"); loadList(); }
    catch { toast.error("Silinemedi"); }
  };

  const save = async () => {
    if (!selected) { toast.error("Önce bir ürün seçin"); return; }
    setSaving(true);
    try {
      await axios.post(`${API}/ai/product-qa-rules`, {
        product_id: selected.id,
        product_name: selected.name,
        instructions,
        rules: rules.filter((r) => (r.a || "").trim()),
      }, auth());
      toast.success("Ürüne özel yanıt kaydedildi");
      setSelected(null); setInstructions(""); setRules([]); setQ("");
      loadList();
    } catch (e) { toast.error(e.response?.data?.detail || "Kaydedilemedi"); }
    setSaving(false);
  };

  return (
    <div className="space-y-6">
      <div className="bg-amber-50 border border-amber-200 rounded-lg p-3 text-sm text-amber-900">
        Buraya girdiğin kurallar, o ürünle ilgili Trendyol/Hepsiburada sorularında AI'ın <b>en yüksek öncelikle</b> uyacağı yanıtlardır. Genel bilgi + geçmiş soru-cevaplar bunun altında kullanılır.
      </div>

      {/* Ürün seç */}
      <div className="border rounded-lg p-4">
        <label className="block text-xs font-semibold text-gray-600 mb-2">1) Ürün seç</label>
        <div className="flex gap-2">
          <div className="relative flex-1">
            <Search className="w-4 h-4 absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" />
            <input value={q} onChange={(e) => setQ(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && search()}
              placeholder="Ürün adı ara…"
              className="w-full border rounded pl-9 pr-3 py-2 text-sm" />
          </div>
          <button onClick={search} disabled={searching}
            className="px-4 py-2 bg-black text-white rounded text-sm disabled:opacity-50">
            {searching ? "…" : "Ara"}
          </button>
        </div>
        {results.length > 0 && (
          <div className="mt-2 border rounded divide-y max-h-64 overflow-auto">
            {results.map((p) => (
              <button key={p.id} onClick={() => pick(p)}
                className="w-full text-left px-3 py-2 text-sm hover:bg-gray-50 flex items-center gap-3">
                {p.images?.[0] && <img src={p.images[0]} alt="" className="w-8 h-10 object-cover rounded" />}
                <span className="flex-1">{p.name}</span>
                <span className="text-xs text-gray-400">{p.price} TL</span>
              </button>
            ))}
          </div>
        )}
        {selected && <div className="mt-2 text-sm text-green-700 flex items-center gap-1.5"><CheckCircle className="w-4 h-4" /> Seçili: <b>{selected.name}</b></div>}
      </div>

      {selected && (
        <>
          {/* Genel talimat */}
          <div className="border rounded-lg p-4">
            <label className="block text-xs font-semibold text-gray-600 mb-2">2) Bu ürüne özel genel talimat (opsiyonel)</label>
            <textarea value={instructions} onChange={(e) => setInstructions(e.target.value)} rows={3}
              placeholder="Örn: Bu ürün dar kalıptır, bir beden büyük önerilir. Kumaşı %100 pamuktur, ütü gerektirmez."
              className="w-full border rounded px-3 py-2 text-sm resize-none" />
          </div>

          {/* Kalıp soru→cevap */}
          <div className="border rounded-lg p-4">
            <div className="flex items-center justify-between mb-2">
              <label className="text-xs font-semibold text-gray-600">3) Kalıp soru → cevap (opsiyonel)</label>
              <button onClick={() => setRules((r) => [...r, { q: "", a: "" }])}
                className="text-xs flex items-center gap-1 text-black border px-2 py-1 rounded hover:bg-gray-50">
                <Plus className="w-3 h-3" /> Kural ekle
              </button>
            </div>
            <div className="space-y-2">
              {rules.length === 0 && <p className="text-xs text-gray-400">Henüz kalıp yok. "Kural ekle" ile soru kalıbı + sabit cevap tanımla.</p>}
              {rules.map((r, i) => (
                <div key={i} className="grid grid-cols-1 sm:grid-cols-[1fr_1.4fr_auto] gap-2 items-start">
                  <input value={r.q} onChange={(e) => setRules((rs) => rs.map((x, j) => j === i ? { ...x, q: e.target.value } : x))}
                    placeholder="Soru kalıbı (örn: kumaşı ne)" className="border rounded px-2 py-1.5 text-sm" />
                  <input value={r.a} onChange={(e) => setRules((rs) => rs.map((x, j) => j === i ? { ...x, a: e.target.value } : x))}
                    placeholder="Verilecek cevap" className="border rounded px-2 py-1.5 text-sm" />
                  <button onClick={() => setRules((rs) => rs.filter((_, j) => j !== i))}
                    className="p-1.5 text-gray-400 hover:text-red-600"><X className="w-4 h-4" /></button>
                </div>
              ))}
            </div>
          </div>

          <button onClick={save} disabled={saving}
            className="px-5 py-2.5 bg-black text-white rounded text-sm font-semibold flex items-center gap-2 disabled:opacity-50">
            <Save className="w-4 h-4" /> {saving ? "Kaydediliyor…" : "Ürüne Özel Yanıtı Kaydet"}
          </button>
        </>
      )}

      {/* Mevcut kurallar */}
      <div className="border rounded-lg p-4">
        <div className="flex items-center justify-between mb-3">
          <h3 className="text-sm font-semibold">Tanımlı ürün kuralları ({list.length})</h3>
          <button onClick={loadList} className="text-xs text-gray-500 flex items-center gap-1 hover:text-black"><RefreshCw className="w-3 h-3" /> Yenile</button>
        </div>
        {list.length === 0 ? (
          <p className="text-xs text-gray-400">Henüz tanım yok.</p>
        ) : (
          <div className="divide-y">
            {list.map((item) => (
              <div key={item.product_id} className="py-2.5 flex items-start gap-3">
                <div className="flex-1 min-w-0">
                  <div className="text-sm font-medium truncate">{item.product_name}</div>
                  <div className="text-xs text-gray-500 truncate">
                    {item.instructions ? item.instructions.slice(0, 80) : "—"}
                    {item.rules?.length ? ` · ${item.rules.length} kalıp` : ""}
                  </div>
                </div>
                <button onClick={() => editExisting(item)} className="text-xs text-black underline">Düzenle</button>
                <button onClick={() => removeExisting(item.product_id)} className="p-1 text-gray-400 hover:text-red-600"><Trash2 className="w-4 h-4" /></button>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
