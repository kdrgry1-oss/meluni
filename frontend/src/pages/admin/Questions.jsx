import { useState, useEffect } from "react";
import {
  MessageCircle, Check, Clock, Send, RefreshCw, Trash2, Sparkles,
  BookOpenCheck, ShieldAlert, User, Bot, Settings as SettingsIcon,
} from "lucide-react";
import axios from "axios";
import { toast } from "sonner";
import {
  Dialog, DialogContent, DialogHeader, DialogTitle,
} from "../../components/ui/dialog";

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;

const CHANNEL_STYLE = {
  trendyol:    { border: "#F27A1A", bg: "#F27A1A", label: "Trendyol" },
  hepsiburada: { border: "#FF6000", bg: "#FF6000", label: "Hepsiburada" },
  temu:        { border: "#111827", bg: "#111827", label: "Temu" },
  whatsapp:    { border: "#25D366", bg: "#25D366", label: "WhatsApp" },
  instagram:   { border: "#E4405F", bg: "linear-gradient(135deg,#FFDC80,#E4405F,#833AB4)", label: "Instagram" },
  messenger:   { border: "#0084FF", bg: "linear-gradient(135deg,#00C6FF,#0078FF)", label: "Messenger" },
  site:        { border: "#6366F1", bg: "#6366F1", label: "Facette Site" },
};

const ChannelBadge = ({ channel }) => {
  const s = CHANNEL_STYLE[channel] || CHANNEL_STYLE.trendyol;
  return (
    <span
      data-testid={`mp-badge-${channel || "trendyol"}`}
      className="inline-flex items-center gap-1 px-3 py-1 rounded-md text-[11px] font-black uppercase tracking-widest shadow-sm text-white"
      style={{ background: s.bg }}
    >
      {s.label}
    </span>
  );
};

export default function AdminQuestions() {
  const [questions, setQuestions] = useState([]);
  const [loading, setLoading] = useState(true);
  const [syncing, setSyncing] = useState(false);
  const [statusFilter, setStatusFilter] = useState("WAITING_FOR_ANSWER");
  const [channelFilter, setChannelFilter] = useState("all");
  const [page, setPage] = useState(0);
  const [totals, setTotals] = useState({});

  const [answerOpen, setAnswerOpen] = useState(false);
  const [selectedQuestion, setSelectedQuestion] = useState(null);
  const [answerText, setAnswerText] = useState("");
  const [sendingAnswer, setSendingAnswer] = useState(false);

  // AI state (per-question draft)
  const [aiLoading, setAiLoading] = useState(false);
  const [aiDraft, setAiDraft] = useState(null); // {draft,confidence,handoff,threshold}

  // AI settings modal
  const [aiSettingsOpen, setAiSettingsOpen] = useState(false);
  const [aiSettings, setAiSettings] = useState(null);

  useEffect(() => { fetchQuestions(); }, [statusFilter, channelFilter, page]);

  const fetchQuestions = async () => {
    setLoading(true);
    try {
      const token = localStorage.getItem("token");
      let url = `${API}/integrations/marketplace/questions?page=${page}&size=30&marketplace=${channelFilter}`;
      if (statusFilter) url += `&status=${statusFilter}`;
      const res = await axios.get(url, { headers: { Authorization: `Bearer ${token}` } });
      setQuestions(res.data?.questions || []);
      setTotals(res.data?.totals || {});
    } catch (err) {
      toast.error("Sorular yüklenemedi");
    } finally {
      setLoading(false);
    }
  };

  const syncChannel = async (ch) => {
    setSyncing(true);
    try {
      const token = localStorage.getItem("token");
      const endpoint = ch === "trendyol"
        ? `${API}/integrations/trendyol/questions/sync`
        : `${API}/integrations/${ch}/questions/sync`;
      await axios.get(endpoint, { headers: { Authorization: `Bearer ${token}` } }).catch(async () => {
        if (ch !== "trendyol") await axios.post(endpoint, {}, { headers: { Authorization: `Bearer ${token}` } });
      });
      toast.success(`${CHANNEL_STYLE[ch].label} senkronize edildi`);
      fetchQuestions();
    } catch (err) {
      toast.error(err.response?.data?.detail || "Senkron başarısız");
    } finally {
      setSyncing(false);
    }
  };

  const openAnswer = (q) => {
    setSelectedQuestion(q);
    setAnswerText(q.answer || "");
    setAiDraft(null);
    setAnswerOpen(true);
  };

  const sendAnswer = async () => {
    if (!answerText.trim()) return;
    setSendingAnswer(true);
    try {
      const token = localStorage.getItem("token");
      const ch = selectedQuestion.marketplace || "trendyol";
      const endpoint = ch === "trendyol"
        ? `${API}/integrations/trendyol/questions/${selectedQuestion.question_id}/answer`
        : `${API}/integrations/${ch}/questions/${selectedQuestion.question_id}/answer`;
      const res = await axios.post(endpoint, { answer: answerText }, { headers: { Authorization: `Bearer ${token}` } });
      // DENETİM FIX: sabit "gönderildi" yanıltıcıydı — Trendyol dışı kanallarda (HB/Temu/WhatsApp/
      // Instagram/Site) cevap müşteriye GİTMİYOR olabilir. Backend'in gerçek mesajını göster;
      // yoksa entegrasyonsuz kanalda "gönderildi" değil "kaydedildi" de.
      const _msg = res?.data?.message
        || (ch === "trendyol" ? "Cevap müşteriye gönderildi" : "Cevap kaydedildi (bu kanalda otomatik gönderim yok)");
      toast.success(_msg);
      setAnswerOpen(false);
      fetchQuestions();
    } catch (err) {
      toast.error(err.response?.data?.detail || "Cevap gönderilemedi");
    } finally {
      setSendingAnswer(false);
    }
  };

  const deleteQuestion = async (q) => {
    if (!await window.appConfirm("Bu soruyu silmek istediğinize emin misiniz?")) return;
    try {
      const token = localStorage.getItem("token");
      const ch = q.marketplace || "trendyol";
      await axios.delete(`${API}/integrations/${ch}/questions/${q.question_id}`, {
        headers: { Authorization: `Bearer ${token}` }
      });
      toast.success("Soru silindi");
      fetchQuestions();
    } catch (err) {
      toast.error(err.response?.data?.detail || "Silinemedi");
    }
  };

  const generateDraft = async () => {
    if (!selectedQuestion) return;
    setAiLoading(true);
    setAiDraft(null);
    try {
      const token = localStorage.getItem("token");
      const ch = selectedQuestion.marketplace || "trendyol";
      const res = await axios.post(`${API}/ai/draft/${ch}/${selectedQuestion.question_id}`, {}, {
        headers: { Authorization: `Bearer ${token}` }
      });
      setAiDraft(res.data);
      if (!answerText) setAnswerText(res.data.draft);
      toast.success("AI taslak hazır");
    } catch (err) {
      toast.error(err.response?.data?.detail || "AI cevap üretemedi");
    } finally {
      setAiLoading(false);
    }
  };

  const trainAI = async () => {
    if (!selectedQuestion || !answerText.trim()) {
      toast.error("Cevap yazmadan eğitemezsiniz");
      return;
    }
    try {
      const token = localStorage.getItem("token");
      await axios.post(`${API}/ai/train-from-question`, {
        question: selectedQuestion.question_text,
        answer: answerText,
        channel: selectedQuestion.marketplace || "trendyol",
        question_id: selectedQuestion.question_id,
      }, { headers: { Authorization: `Bearer ${token}` } });
      toast.success("AI bilgi bankasına eklendi – benzer sorularda kullanılacak");
    } catch (err) {
      toast.error(err.response?.data?.detail || "Eğitme başarısız");
    }
  };

  const openAiSettings = async () => {
    try {
      const token = localStorage.getItem("token");
      const res = await axios.get(`${API}/ai/settings`, { headers: { Authorization: `Bearer ${token}` } });
      setAiSettings(res.data);
      setAiSettingsOpen(true);
    } catch (err) {
      toast.error("Ayarlar alınamadı");
    }
  };

  const saveAiSettings = async () => {
    try {
      const token = localStorage.getItem("token");
      await axios.post(`${API}/ai/settings`, aiSettings, { headers: { Authorization: `Bearer ${token}` } });
      toast.success("AI ayarları kaydedildi");
      setAiSettingsOpen(false);
    } catch (err) {
      toast.error("Kaydedilemedi");
    }
  };

  const getRemainingTime = (isoStr) => {
    if (!isoStr) return null;
    const created = new Date(isoStr);
    const deadline = new Date(created.getTime() + 24 * 60 * 60 * 1000);
    const diff = deadline.getTime() - Date.now();
    if (diff <= 0) return { expired: true, text: "Süre Doldu" };
    const h = Math.floor(diff / 3_600_000);
    const m = Math.floor((diff % 3_600_000) / 60_000);
    return { expired: false, text: `${h}s ${m}d kaldı` };
  };

  const formatDate = (isoStr) => isoStr ? new Date(isoStr).toLocaleString("tr-TR") : "-";

  return (
    <div className="p-6 max-w-7xl mx-auto" data-testid="admin-questions-page">
      <div className="flex justify-between items-start mb-6 flex-wrap gap-3">
        <div>
          <h1 className="text-2xl font-bold flex items-center gap-2">
            <MessageCircle className="text-blue-600" /> Müşteri Mesajları
          </h1>
          <p className="text-sm text-gray-500 mt-1">
            Trendyol · Hepsiburada · Temu · WhatsApp · Instagram · Messenger · Site — tek panelden AI asistan destekli.
          </p>
        </div>
        <div className="flex gap-2 flex-wrap">
          <button onClick={openAiSettings} data-testid="ai-settings-btn"
            className="flex items-center gap-2 px-3 py-2 bg-indigo-50 text-indigo-700 border border-indigo-200 rounded-lg text-xs font-medium hover:bg-indigo-100">
            <SettingsIcon size={14} /> AI Ayarları
          </button>
        </div>
      </div>

      {/* Channel chips */}
      <div className="flex flex-wrap gap-2 mb-5">
        <button
          onClick={() => { setChannelFilter("all"); setPage(0); }}
          data-testid="chip-all"
          className={`px-3 py-1.5 rounded-full text-xs font-bold ${channelFilter === "all" ? "bg-black text-white" : "bg-gray-100 text-gray-700 hover:bg-gray-200"}`}
        >
          Tümü ({Object.values(totals).reduce((a, b) => a + b, 0)})
        </button>
        {Object.keys(CHANNEL_STYLE).map(ch => {
          const active = channelFilter === ch;
          const s = CHANNEL_STYLE[ch];
          return (
            <div key={ch} className="flex items-stretch">
              <button
                onClick={() => { setChannelFilter(ch); setPage(0); }}
                data-testid={`chip-${ch}`}
                className="px-3 py-1.5 rounded-l-full text-xs font-bold text-white transition-transform"
                style={{
                  background: s.bg,
                  opacity: active ? 1 : 0.7,
                  transform: active ? "scale(1.02)" : "none",
                }}
              >
                {s.label} ({totals[ch] || 0})
              </button>
              <button
                onClick={() => syncChannel(ch)}
                disabled={syncing}
                title={`${s.label} senkron`}
                className="px-2 bg-white border-y border-r border-gray-200 rounded-r-full hover:bg-gray-50 disabled:opacity-50"
              >
                <RefreshCw size={12} className={syncing ? "animate-spin text-gray-500" : "text-gray-500"} />
              </button>
            </div>
          );
        })}
      </div>

      {/* Status filter */}
      <div className="flex gap-2 mb-4">
        {[
          { k: "WAITING_FOR_ANSWER", label: "Bekleyenler" },
          { k: "ANSWERED", label: "Cevaplananlar" },
          { k: "", label: "Tümü" },
        ].map(s => (
          <button key={s.k || "all"}
            onClick={() => { setStatusFilter(s.k); setPage(0); }}
            className={`px-3 py-1.5 rounded text-xs font-bold ${statusFilter === s.k ? "bg-black text-white" : "bg-gray-100 text-gray-600 hover:bg-gray-200"}`}
          >
            {s.label}
          </button>
        ))}
      </div>

      {/* List */}
      <div className="bg-white rounded-xl border shadow-sm">
        <div className="divide-y max-h-[70vh] overflow-y-auto">
          {loading ? (
            <div className="p-8 text-center text-gray-500">Yükleniyor...</div>
          ) : questions.length === 0 ? (
            <div className="p-8 text-center text-gray-500">Mesaj bulunamadı</div>
          ) : questions.map(q => {
            const ch = q.marketplace || "trendyol";
            const style = CHANNEL_STYLE[ch] || CHANNEL_STYLE.trendyol;
            const timeObj = q.status === "WAITING_FOR_ANSWER" ? getRemainingTime(q.created_date) : null;
            return (
              <div
                key={`${ch}-${q.question_id}`}
                data-testid={`question-row-${ch}`}
                className="p-4 hover:bg-gray-50 transition-colors relative"
                style={{ borderLeft: `6px solid ${style.border}` }}
              >
                <div className="absolute top-3 right-3">
                  <ChannelBadge channel={ch} />
                </div>

                <div className="flex justify-between items-start mb-2 pr-36">
                  <div className="flex items-center gap-2 flex-wrap">
                    {q.status === "WAITING_FOR_ANSWER" ? (
                      <>
                        <span className="flex items-center gap-1 text-xs font-medium text-orange-600 bg-orange-100 px-2 py-1 rounded-full">
                          <Clock size={12} /> Bekliyor
                        </span>
                        {timeObj && (
                          <span className={`text-xs font-medium px-2 py-1 rounded-full ${timeObj.expired ? "text-red-600 bg-red-100" : "text-blue-600 bg-blue-100"}`}>
                            {timeObj.text}
                          </span>
                        )}
                      </>
                    ) : (
                      <span className="flex items-center gap-1 text-xs font-medium text-green-600 bg-green-100 px-2 py-1 rounded-full">
                        <Check size={12} /> Cevaplandı
                      </span>
                    )}
                    <span className="text-xs text-gray-500">{formatDate(q.created_date)}</span>
                  </div>
                  <div className="flex gap-1 mt-8">
                    <button onClick={() => openAnswer(q)} data-testid={`answer-btn-${ch}-${q.question_id}`}
                      className="flex items-center gap-1 px-3 py-1.5 text-sm font-medium text-blue-600 bg-blue-50 rounded-lg hover:bg-blue-100">
                      {q.status === "WAITING_FOR_ANSWER" ? "Cevapla" : "Görüntüle"}
                    </button>
                    <button onClick={() => deleteQuestion(q)} data-testid={`delete-q-${q.question_id}`}
                      title="Sil"
                      className="flex items-center gap-1 px-2 py-1.5 text-sm font-medium text-red-600 bg-red-50 rounded-lg hover:bg-red-100">
                      <Trash2 size={13} />
                    </button>
                  </div>
                </div>
                <div className="flex items-center gap-3 mb-3">
                  {q.image_url ? (
                    <img src={q.image_url} alt="..." className="w-12 h-16 object-cover rounded border bg-white" />
                  ) : (
                    <div className="w-12 h-16 bg-gray-100 rounded border flex items-center justify-center text-[10px] text-gray-400 text-center">{ch === "whatsapp" || ch === "instagram" || ch === "messenger" ? "DM" : "—"}</div>
                  )}
                  <h3 className="font-medium text-gray-900">{q.product_name || q.subject || "Genel Mesaj"}</h3>
                </div>
                <div className="bg-gray-50 border rounded-lg p-3 text-sm text-gray-700 italic mb-2">
                  <p>{q.question_text}</p>
                </div>
                <p className="text-xs text-gray-500"><User size={10} className="inline" /> {q.customer_name || "—"}</p>
                {q.answer && (
                  <div className="mt-3 pl-4 border-l-2" style={{ borderColor: style.border }}>
                    <p className="text-xs text-gray-500 mb-1">Verilen Cevap:</p>
                    <p className="text-sm text-gray-800">{q.answer}</p>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </div>

      {/* Answer modal */}
      <Dialog open={answerOpen} onOpenChange={setAnswerOpen}>
        <DialogContent className="max-w-2xl">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-3">
              Mesajı Yanıtla
              {selectedQuestion && <ChannelBadge channel={selectedQuestion.marketplace || "trendyol"} />}
            </DialogTitle>
          </DialogHeader>

          {selectedQuestion && (
            <div className="space-y-4 pt-2">
              <div className="bg-gray-50 p-3 rounded-lg border text-sm">
                <p className="font-medium text-gray-900 mb-1">{selectedQuestion.product_name || "Genel"}</p>
                <p className="text-gray-700 italic">"{selectedQuestion.question_text}"</p>
                <p className="text-xs text-gray-500 mt-2">— {selectedQuestion.customer_name || "—"}</p>
              </div>

              <div className="flex flex-wrap gap-2">
                <button onClick={generateDraft} disabled={aiLoading} data-testid="ai-draft-btn"
                  className="flex items-center gap-1 px-3 py-1.5 bg-indigo-600 text-white rounded text-xs font-bold hover:bg-indigo-700 disabled:opacity-50">
                  <Sparkles size={13} /> {aiLoading ? "AI düşünüyor..." : "AI Taslak Üret"}
                </button>
                <button onClick={trainAI} disabled={!answerText.trim()} data-testid="ai-train-btn"
                  className="flex items-center gap-1 px-3 py-1.5 bg-green-600 text-white rounded text-xs font-bold hover:bg-green-700 disabled:opacity-50">
                  <BookOpenCheck size={13} /> Yapay Zekayı Eğit
                </button>
              </div>

              {aiDraft && (
                <div className={`rounded-lg border p-3 text-xs ${aiDraft.handoff ? "bg-amber-50 border-amber-300" : "bg-indigo-50 border-indigo-200"}`}>
                  <div className="flex justify-between items-center mb-2">
                    <span className="flex items-center gap-1 font-bold text-indigo-700">
                      <Bot size={13} /> AI Taslağı
                    </span>
                    <div className="flex items-center gap-2">
                      <span className="font-bold">Güven: {(aiDraft.confidence * 100).toFixed(0)}%</span>
                      {aiDraft.handoff && (
                        <span className="flex items-center gap-1 text-amber-700 font-bold">
                          <ShieldAlert size={12} /> İnsan devralmalı
                        </span>
                      )}
                    </div>
                  </div>
                  <p className="text-gray-800 whitespace-pre-wrap">{aiDraft.draft}</p>
                  <button onClick={() => setAnswerText(aiDraft.draft)} className="mt-2 text-xs text-indigo-700 underline">
                    Bu taslağı cevap alanına kopyala
                  </button>
                </div>
              )}

              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">Cevap</label>
                <textarea
                  className="w-full border rounded-lg p-3 text-sm min-h-[140px] focus:ring-1 focus:outline-none"
                  value={answerText}
                  onChange={(e) => setAnswerText(e.target.value)}
                  disabled={selectedQuestion.status !== "WAITING_FOR_ANSWER"}
                  data-testid="answer-textarea"
                />
              </div>

              {selectedQuestion.status === "WAITING_FOR_ANSWER" && (() => {
                // DENETİM FIX (#42): yalnız Trendyol'da cevap müşteriye otomatik gider. Diğer
                // kanallarda (HB/Temu/WhatsApp/Instagram/Messenger/Site) buton "Yerel Kaydet"
                // olur ve gönderim vaadi verilmez; bir uyarı satırı gösterilir.
                const _ch = selectedQuestion.marketplace || "trendyol";
                const _autoSend = _ch === "trendyol";
                return (
                  <div className="flex flex-col items-end gap-1 pt-2">
                    {!_autoSend && (
                      <span className="text-[11px] text-amber-600">
                        Bu kanalda otomatik gönderim yok — cevap yalnızca panelde kaydedilir.
                      </span>
                    )}
                    <button onClick={sendAnswer} disabled={sendingAnswer || !answerText.trim()} data-testid="send-answer-btn"
                      className="flex items-center gap-2 px-4 py-2 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-700 disabled:opacity-50">
                      {sendingAnswer
                        ? (_autoSend ? "Gönderiliyor..." : "Kaydediliyor...")
                        : <><Send size={14} /> {_autoSend ? "Gönder" : "Yerel Kaydet"}</>}
                    </button>
                  </div>
                );
              })()}
            </div>
          )}
        </DialogContent>
      </Dialog>

      {/* AI Settings Modal */}
      <Dialog open={aiSettingsOpen} onOpenChange={setAiSettingsOpen}>
        <DialogContent className="max-w-2xl" data-testid="ai-settings-modal">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <Bot size={18} className="text-indigo-600" /> AI Chatbot Ayarları
            </DialogTitle>
          </DialogHeader>
          {aiSettings && (
            <div className="space-y-4 pt-2">
              <label className="flex items-center gap-2">
                <input type="checkbox" checked={!!aiSettings.enabled} onChange={e => setAiSettings({ ...aiSettings, enabled: e.target.checked })} />
                <span className="text-sm font-medium">AI Chatbot Aktif</span>
              </label>

              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="block text-xs font-bold text-gray-600 mb-1">Sağlayıcı</label>
                  <select value={aiSettings.provider} onChange={e => setAiSettings({ ...aiSettings, provider: e.target.value })}
                    className="w-full border px-3 py-2 rounded text-sm bg-white">
                    <option value="openai">OpenAI</option>
                    <option value="anthropic">Anthropic (Claude)</option>
                    <option value="gemini">Google Gemini</option>
                  </select>
                </div>
                <div>
                  <label className="block text-xs font-bold text-gray-600 mb-1">Model</label>
                  <input value={aiSettings.model || ""} onChange={e => setAiSettings({ ...aiSettings, model: e.target.value })}
                    className="w-full border px-3 py-2 rounded text-sm" placeholder="claude-sonnet-4-6" />
                </div>
                <div>
                  <label className="block text-xs font-bold text-gray-600 mb-1">Hızlı Model (sınıflandırma)</label>
                  <input value={aiSettings.fast_model || ""} onChange={e => setAiSettings({ ...aiSettings, fast_model: e.target.value })}
                    className="w-full border px-3 py-2 rounded text-sm" placeholder="claude-haiku-4-5" />
                </div>
                <div>
                  <label className="block text-xs font-bold text-gray-600 mb-1">Güven Eşiği (0-1)</label>
                  <input type="number" step="0.05" min="0" max="1" value={aiSettings.confidence_threshold || 0.7}
                    onChange={e => setAiSettings({ ...aiSettings, confidence_threshold: Number(e.target.value) })}
                    className="w-full border px-3 py-2 rounded text-sm" />
                </div>
              </div>

              <div>
                <label className="flex items-center gap-2 mb-2">
                  <input type="checkbox" checked={!!aiSettings.use_emergent_key}
                    onChange={e => setAiSettings({ ...aiSettings, use_emergent_key: e.target.checked })} />
                  <span className="text-sm font-medium">Emergent Universal Key kullan (ESKİ — kapalı tutun, kendi anahtarınızı girin)</span>
                </label>
                {!aiSettings.use_emergent_key && (
                  <>
                  <input type="password" value={aiSettings.custom_api_key === "********" ? "" : (aiSettings.custom_api_key || "")}
                    onChange={e => setAiSettings({ ...aiSettings, custom_api_key: e.target.value })}
                    placeholder="Kendi API anahtarınız (örn. Anthropic sk-ant-...)"
                    className="w-full border px-3 py-2 rounded text-sm" />
                  <p className="text-xs text-gray-500 mt-1">Sağlayıcı: Anthropic (Claude) önerilir. Model: claude-sonnet-4-6 · Hızlı: claude-haiku-4-5. Anahtarı console.anthropic.com'dan alın.</p>
                  </>
                )}
              </div>

              <div>
                <label className="block text-xs font-bold text-gray-600 mb-1">Persona / Konuşma Tonu</label>
                <textarea rows={6} value={aiSettings.persona || ""} onChange={e => setAiSettings({ ...aiSettings, persona: e.target.value })}
                  data-testid="ai-persona-input"
                  className="w-full border px-3 py-2 rounded text-sm font-mono" />
                <p className="text-[10px] text-gray-400 mt-1">AI'nın kimliğini ve konuşma tarzını belirler.</p>
              </div>

              <div>
                <label className="block text-xs font-bold text-gray-600 mb-1">Mağaza Ek Kuralları (WhatsApp/Sosyal)</label>
                <textarea rows={6} value={aiSettings.wa_extra_rules || ""} onChange={e => setAiSettings({ ...aiSettings, wa_extra_rules: e.target.value })}
                  data-testid="ai-wa-extra-rules-input"
                  placeholder={"Örn:\n- Beden sorusunda önce boy/kilo iste, sonra öner.\n- İndirim isteyene kampanyaları hatırlat.\n- Kargo süresi 1-3 iş günü de."}
                  className="w-full border px-3 py-2 rounded text-sm font-mono" />
                <p className="text-[10px] text-gray-400 mt-1">Buraya yazdığınız kurallar WhatsApp/Instagram/Messenger AI yanıtlarına EKLENİR (persona'yı bozmadan kendi ince ayarlarınızı yapın). Güvenlik/ödeme kuralları kodda korunur.</p>
              </div>

              <div>
                <label className="block text-xs font-bold text-gray-600 mb-2">Aktif Kanallar</label>
                <div className="grid grid-cols-3 gap-2">
                  {Object.keys(CHANNEL_STYLE).map(ch => (
                    <label key={ch} className="flex items-center gap-2 text-sm">
                      <input type="checkbox" checked={!!aiSettings.channels?.[ch]}
                        onChange={e => setAiSettings({ ...aiSettings, channels: { ...(aiSettings.channels || {}), [ch]: e.target.checked } })} />
                      {CHANNEL_STYLE[ch].label}
                    </label>
                  ))}
                </div>
              </div>

              <div className="flex justify-end gap-2 pt-4 border-t">
                <button onClick={() => setAiSettingsOpen(false)} className="px-4 py-2 border rounded hover:bg-gray-50 text-sm">İptal</button>
                <button onClick={saveAiSettings} data-testid="save-ai-settings-btn"
                  className="px-4 py-2 bg-indigo-600 text-white rounded hover:bg-indigo-700 text-sm font-bold">
                  Kaydet
                </button>
              </div>
            </div>
          )}
        </DialogContent>
      </Dialog>
    </div>
  );
}
