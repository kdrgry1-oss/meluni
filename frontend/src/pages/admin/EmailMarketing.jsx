/**
 * EmailMarketing.jsx — Brevo birincil, AWS SES yedek e-posta pazarlama paneli.
 * ================================================================
 * SES ayarları (Zoho'dan AYRI kanal — işlemsel maile dokunmaz) + rıza vermiş
 * bülten abonelerine toplu kampanya gönderimi + geçmiş. Alıcılar backend'de
 * yalnız active + consent olanlardan seçilir; her maile abonelikten-çık linki eklenir.
 */
import { useState, useEffect, useRef } from "react";
import axios from "axios";
import { toast } from "sonner";
import { Mail, Send, Save, Users, RefreshCw, CheckCircle2, AlertTriangle,
  FileText, Eye, Copy, Trash2, X, Star,
  Bold, Italic, List, Link2, AlignLeft, AlignCenter, AlignRight, Heading, Code, SquarePlus, Image, ImagePlus } from "lucide-react";
import { sanitizeHtml } from "../../lib/sanitizeHtml";

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;
const h = () => ({ Authorization: `Bearer ${localStorage.getItem("token")}` });

export default function EmailMarketing() {
  const [cfg, setCfg] = useState({
    provider: "brevo", fallback_enabled: true,
    brevo_enabled: false, brevo_api_key: "", brevo_from_email: "", brevo_from_name: "", brevo_reply_to: "", brevo_list_id: "", brevo_webhook_secret: "",
    ses_enabled: false, ses_region: "", ses_access_key: "", ses_secret_key: "", ses_from_email: "", ses_from_name: "", ses_reply_to: "", ses_configuration_set: "",
  });
  const [configured, setConfigured] = useState(false);
  const [canEditSettings, setCanEditSettings] = useState(false);
  const [readyProvider, setReadyProvider] = useState("");
  const [aud, setAud] = useState({ total: 0, eligible: 0 });
  // Adres teşhisi: "bu adrese neden mail gitti/gitmedi?"
  const [diagEmail, setDiagEmail] = useState("");
  const [diag, setDiag] = useState(null);
  const [diagBusy, setDiagBusy] = useState(false);
  const runDiag = async () => {
    const em = diagEmail.trim();
    if (!em) return;
    setDiagBusy(true);
    try {
      const r = await axios.get(`${API}/admin/email-marketing/diagnose?email=${encodeURIComponent(em)}`, { headers: h() });
      setDiag(r.data);
    } catch (e) { toast.error(e?.response?.data?.detail || "Sorgulanamadı"); }
    finally { setDiagBusy(false); }
  };
  const [testTo, setTestTo] = useState("");
  const [camp, setCamp] = useState({ subject: "", html: "" });
  const [campTestTo, setCampTestTo] = useState(() => { try { return localStorage.getItem("emailTestTo") || ""; } catch { return ""; } });
  const [campaigns, setCampaigns] = useState([]);
  // Kampanya raporu: kimlere gitti + kimler alışveriş yaptı (UTM link + e-posta eşleşmesi)
  const [rep, setRep] = useState(null);
  const [repBusy, setRepBusy] = useState(false);
  // "Almayanlara gönder": aynı içerik, yalnız bu kampanyayı almamış izinli alıcılara
  const [missBusy, setMissBusy] = useState("");
  const sendMissing = async (c) => {
    setMissBusy(c.id);
    try {
      const m = await axios.get(`${API}/admin/email-marketing/campaigns/${c.id}/missing`, { headers: h() });
      const d = m.data || {};
      if (!d.missing) { toast.info("Bu kampanyayı almamış izinli alıcı yok."); return; }
      if (!window.confirm(`"${c.subject}" içeriği, bu kampanyayı ALMAMIŞ ${d.missing} izinli alıcıya gönderilecek (kitle ${d.audience}, daha önce alan ${d.received} hariç). Onaylıyor musun?`)) return;
      const r = await axios.post(`${API}/admin/email-marketing/campaigns/${c.id}/send-missing`, {}, { headers: h() });
      toast.success(`${r.data?.missing || d.missing} alıcıya gönderim başlatıldı`);
      load();
    } catch (e) { toast.error(e?.response?.data?.detail || "Gönderilemedi"); }
    finally { setMissBusy(""); }
  };
  const openReport = async (c) => {
    setRepBusy(true); setRep(null);
    try { const r = await axios.get(`${API}/admin/email-marketing/campaigns/${c.id}/report?days=14`, { headers: h() }); setRep(r.data); }
    catch (e) { toast.error(e.response?.data?.detail || "Rapor alınamadı"); }
    finally { setRepBusy(false); }
  };
  const downloadReport = async () => {
    if (!rep) return;
    try {
      const r = await axios.get(`${API}/admin/email-marketing/campaigns/${rep.campaign.id}/report.xlsx?days=14`, { headers: h(), responseType: "blob" });
      const url = URL.createObjectURL(r.data); const a = document.createElement("a");
      a.href = url; a.download = "kampanya-raporu.xlsx"; a.click(); URL.revokeObjectURL(url);
    } catch { toast.error("Excel oluşturulamadı"); }
  };
  const [busy, setBusy] = useState("");
  const [templates, setTemplates] = useState([]);
  const [previewHtml, setPreviewHtml] = useState("");   // canlı önizleme (editörün altında)
  const [previewModal, setPreviewModal] = useState(null); // {html, subject} tam ekran modal
  const [editMode, setEditMode] = useState("visual");   // "visual" (WYSIWYG) | "html" (ham)
  // ── Görselli ÜRÜN EKLE seçici ──
  const [pickerCats, setPickerCats] = useState([]);
  const [pickerCat, setPickerCat] = useState("");
  const [pickerProducts, setPickerProducts] = useState([]);
  const [pickerSel, setPickerSel] = useState(new Set());
  const [pickerLoading, setPickerLoading] = useState(false);
  const [pickerOpen, setPickerOpen] = useState(false);

  // Görsel (WYSIWYG) editör — hafif contentEditable + execCommand (yeni npm paketi YOK).
  const visualRef = useRef(null);
  const lastEditorHtml = useRef("");   // editörün DIŞARI yazdığı son html (dış değişimi ayırt et)
  const htmlRef = useRef(null);        // HTML-mod textarea (imleç konumuna görsel eklemek için)
  // ── Serbest GÖRSEL EKLE (herhangi bir dosya → R2/cdn.meluni.com.tr; katalog/ürün DEĞİL) ──
  const imgFileRef = useRef(null);     // gizli file input
  const [imgUploading, setImgUploading] = useState(false);
  const [lastImageUrl, setLastImageUrl] = useState("");   // son yüklenen cdn URL (kopyalanabilir gösterim)

  // camp.html DIŞARIDAN değişince (şablon "Kullan" / HTML modda yazma) görsel alanı senkronla.
  useEffect(() => {
    if (editMode !== "visual") return;
    const el = visualRef.current;
    if (!el) return;
    if (camp.html !== lastEditorHtml.current) {
      const safeHtml = sanitizeHtml(camp.html || "", { allowStyle: true });
      el.innerHTML = safeHtml;
      lastEditorHtml.current = safeHtml;
    }
  }, [camp.html, editMode]);

  const onVisualInput = () => {
    const el = visualRef.current;
    if (!el) return;
    const html = el.innerHTML;
    lastEditorHtml.current = html;
    setCamp((p) => ({ ...p, html }));
  };

  const exec = (cmd, val = null) => {
    const el = visualRef.current;
    if (el) el.focus();
    try { document.execCommand(cmd, false, val); } catch {}
    onVisualInput();
  };
  const addLink = () => {
    const url = window.prompt("Bağlantı (URL) — placeholder bırakabilirsin:", "URUN_LINKI");
    if (url === null) return;
    exec("createLink", url.trim() || "URUN_LINKI");
  };
  const addButton = () => {
    const text = window.prompt("Buton yazısı:", "Koleksiyonu Keşfet");
    if (text === null) return;
    const url = window.prompt("Buton bağlantısı (URL):", "URUN_LINKI") || "URUN_LINKI";
    const btn = `<div style="text-align:center;margin:22px 0;"><a href="${(url || "URUN_LINKI").trim()}" style="display:inline-block;background:#1a1a1a;color:#ffffff;text-decoration:none;padding:15px 44px;font-size:13px;font-weight:500;letter-spacing:1.5px;text-transform:uppercase;border-radius:2px;">${(text || "Buton").trim()}</a></div>`;
    exec("insertHTML", btn + "<p><br></p>");
  };
  const insertPlaceholder = (ph) => { if (ph) exec("insertText", ph); };

  // ── Serbest görsel: dosya seç → mevcut R2 ucuna yükle (POST /api/upload/image, multipart `file`)
  // → dönen cdn.meluni.com.tr URL'iyle e-posta-güvenli <img> ekle. DB'de yalnız URL referansı
  // tutulur (base64/DB-bloat YOK; optimizasyon backend'de WebP). Görsel modda imleç konumuna
  // insertHTML; HTML modda textarea imleç konumuna metin olarak eklenir. Ürün seçiciyle İLGİSİZ.
  const insertImageSnippet = (url) => {
    const snippet = `<img src="${url}" alt="" style="display:block;width:100%;max-width:600px;height:auto;border:0;margin:0 auto;"/>`;
    if (editMode === "visual") {
      exec("insertHTML", snippet + "<p><br></p>");
    } else {
      const ta = htmlRef.current;
      const cur = camp.html || "";
      if (ta && typeof ta.selectionStart === "number") {
        const s = ta.selectionStart, e = ta.selectionEnd;
        const next = cur.slice(0, s) + snippet + cur.slice(e);
        setCamp((p) => ({ ...p, html: next }));
        requestAnimationFrame(() => { try { ta.focus(); ta.selectionStart = ta.selectionEnd = s + snippet.length; } catch { /* noop */ } });
      } else {
        setCamp((p) => ({ ...p, html: cur + snippet }));
      }
    }
  };

  const onPickImageFile = async (ev) => {
    const file = ev.target.files && ev.target.files[0];
    if (ev.target) ev.target.value = "";   // aynı dosya tekrar seçilebilsin
    if (!file) return;
    if (!/^image\//.test(file.type || "")) { toast.error("Lütfen bir görsel dosyası seçin"); return; }
    setImgUploading(true);
    try {
      const fd = new FormData();
      fd.append("file", file);
      const r = await axios.post(`${API}/upload/image`, fd, { headers: { ...h() } });
      const url = r.data?.url || "";
      if (!url) { toast.error("Görsel URL alınamadı"); return; }
      setLastImageUrl(url);
      insertImageSnippet(url);
      toast.success("Görsel yüklendi ve eklendi");
    } catch (e) {
      toast.error(e.response?.data?.detail || "Görsel yüklenemedi");
    } finally { setImgUploading(false); }
  };

  const copyImageUrl = async () => {
    if (!lastImageUrl) return;
    try { await navigator.clipboard.writeText(lastImageUrl); toast.success("URL kopyalandı"); }
    catch { toast.error("Kopyalanamadı — elle seçip kopyalayın"); }
  };

  // ── Canlı önizleme: 600px e-postayı panele SIĞDIR (transform: scale) — yatay kırpma yok ──
  // TİTREME FİXİ: ResizeObserver YALNIZ layout-kaynaklı SÜTUN genişliğini gözler (scale/scrollbar'dan
  // ETKİLENMEZ). Ölçeklenen kutu/iframe GÖZLENMEZ (geri-besleme döngüsü kaynağı buydu). Eşik + rAF ile
  // aynı/çok küçük değişimde state güncellenmez → sabit durur, yalnız pencere/panel genişliği değişince
  // bir kez yeniden ölçeklenir. Yükseklik değişse bile genişlik aynıysa scale SABİT kalır.
  const EMAIL_W = 600;
  const previewColRef = useRef(null);   // önizleme SÜTUNU (layout genişliği)
  const pvScaleRef = useRef(1);
  const [pvScale, setPvScale] = useState(1);
  const [pvH, setPvH] = useState(760);
  const pvHRef = useRef(760);
  useEffect(() => {
    const el = previewColRef.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    let raf = 0;
    const measure = () => {
      const w = el.clientWidth || EMAIL_W;
      // Dikey scrollbar payı (~18px) düşülür → ölçekli e-posta + scrollbar YATAY taşmadan sığar.
      const next = Math.min(1, Math.max(0.2, (w - 18) / EMAIL_W));
      if (Math.abs(next - pvScaleRef.current) > 0.004) {   // eşik: mikro değişimde re-render yok
        pvScaleRef.current = next;
        setPvScale(next);
      }
    };
    const ro = new ResizeObserver(() => {
      cancelAnimationFrame(raf);
      raf = requestAnimationFrame(measure);   // senkron ölçüm yok → "ResizeObserver loop" uyarısı yok
    });
    ro.observe(el);
    measure();
    return () => { cancelAnimationFrame(raf); ro.disconnect(); };
  }, []);
  const onPreviewLoad = (ev) => {
    try {
      const doc = ev.target.contentWindow.document;
      const hh = Math.max(doc.body.scrollHeight, doc.documentElement.scrollHeight, 400) + 8;
      // Yükseklik gerçekten değiştiyse güncelle (genişlik/scale'e DOKUNMAZ → titremez).
      if (Math.abs(hh - pvHRef.current) > 2) {
        pvHRef.current = hh;
        setPvH(hh);
      }
    } catch {}
  };

  const load = async () => {
    try {
      const [s, a, c, t, status] = await Promise.all([
        axios.get(`${API}/admin/email-marketing/settings`, { headers: h() }).catch((e) => {
          if (e.response?.status === 403) return { data: null };
          throw e;
        }),
        axios.get(`${API}/admin/email-marketing/audience`, { headers: h() }),
        axios.get(`${API}/admin/email-marketing/campaigns`, { headers: h() }),
        axios.get(`${API}/admin/email-marketing/templates`, { headers: h() }),
        axios.get(`${API}/admin/email-marketing/provider-status`, { headers: h() }),
      ]);
      setCanEditSettings(!!s.data);
      if (s.data) setCfg((p) => ({ ...p, ...s.data }));
      setReadyProvider(status.data.provider);
      setConfigured(!!status.data.configured);
      setAud(a.data || { total: 0, eligible: 0 });
      setCampaigns(c.data?.campaigns || []);
      setTemplates(t.data?.templates || []);
    } catch (e) { toast.error("Yüklenemedi"); }
  };
  useEffect(() => { load(); /* eslint-disable-next-line */ }, []);

  // Ürün seçici için kategoriler (bir kez).
  useEffect(() => {
    axios.get(`${API}/categories`, { headers: h() })
      .then((r) => setPickerCats((r.data || []).filter((c) => c.is_active !== false)))
      .catch(() => {});
  }, []);

  const loadPickerProducts = async (catId) => {
    setPickerCat(catId);
    setPickerSel(new Set());
    if (!catId) { setPickerProducts([]); return; }
    setPickerLoading(true);
    try {
      const r = await axios.get(`${API}/admin/email-marketing/products`, { headers: h(), params: { category: catId, limit: 48 } });
      setPickerProducts(r.data?.products || []);
    } catch { toast.error("Ürünler yüklenemedi"); setPickerProducts([]); }
    finally { setPickerLoading(false); }
  };

  const togglePick = (id) => setPickerSel((s) => { const n = new Set(s); n.has(id) ? n.delete(id) : n.add(id); return n; });

  // E-posta-güvenli ÜRÜN-KARTI BLOĞU (tablo + inline style, 2 sütun). Görsel: mutlak https.
  const _esc = (s) => String(s || "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  const _fmtTL = (v) => { const n = Number(v); return isFinite(n) ? n.toLocaleString("tr-TR", { minimumFractionDigits: 0, maximumFractionDigits: 2 }) + " TL" : ""; };
  const buildProductCardsHtml = (prods) => {
    const card = (p) => {
      const hasSale = p.sale_price && Number(p.sale_price) > 0 && Number(p.sale_price) < Number(p.price);
      const priceHtml = hasSale
        ? `<span style="color:#1a1a1a;font-weight:600;font-size:14px;">${_fmtTL(p.sale_price)}</span> <span style="color:#9a9a9a;text-decoration:line-through;font-size:12px;">${_fmtTL(p.price)}</span>`
        : `<span style="color:#1a1a1a;font-weight:600;font-size:14px;">${_fmtTL(p.price)}</span>`;
      return `<td width="50%" valign="top" style="padding:8px;box-sizing:border-box;">`
        + `<a href="${_esc(p.url)}" target="_blank" style="text-decoration:none;color:#1a1a1a;display:block;">`
        + `<img src="${_esc(p.image)}" alt="${_esc(p.name)}" width="260" style="display:block;width:100%;max-width:260px;height:auto;border:0;border-radius:2px;margin:0 auto;" />`
        + `<div style="font-size:13px;line-height:1.4;margin:10px 0 4px;color:#1a1a1a;">${_esc(p.name)}</div>`
        + `<div style="margin:0 0 4px;">${priceHtml}</div>`
        + `<div style="font-size:12px;color:#1a1a1a;text-decoration:underline;">İncele</div>`
        + `</a></td>`;
    };
    let rows = "";
    for (let i = 0; i < prods.length; i += 2) {
      const a = card(prods[i]);
      const b = prods[i + 1] ? card(prods[i + 1]) : '<td width="50%" style="padding:8px;"></td>';
      rows += `<tr>${a}${b}</tr>`;
    }
    return `<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin:18px 0;border-collapse:collapse;"><tbody>${rows}</tbody></table>`;
  };

  const insertSelectedProducts = () => {
    const chosen = pickerProducts.filter((p) => pickerSel.has(p.id));
    if (!chosen.length) { toast.error("En az bir ürün seçin"); return; }
    const block = buildProductCardsHtml(chosen);
    if (editMode === "visual" && visualRef.current) {
      visualRef.current.focus();
      try { document.execCommand("insertHTML", false, block + "<p><br></p>"); } catch {}
      onVisualInput();
    } else {
      setCamp((p) => ({ ...p, html: (p.html || "") + "\n" + block + "\n" }));
    }
    setPickerSel(new Set());
    toast.success(`${chosen.length} ürün karta eklendi`);
  };

  // Canlı önizleme — Konu/HTML değişince debounce ile tam markalı görünümü çek.
  useEffect(() => {
    if (!camp.subject.trim() && !camp.html.trim()) { setPreviewHtml(""); return; }
    const t = setTimeout(async () => {
      try {
        const r = await axios.post(`${API}/admin/email-marketing/preview`,
          { subject: camp.subject, html: camp.html }, { headers: h() });
        setPreviewHtml(r.data?.html || "");
      } catch { /* önizleme sessiz geç */ }
    }, 500);
    return () => clearTimeout(t);
  }, [camp.subject, camp.html]);

  const useTemplate = (tpl) => {
    setCamp({ subject: tpl.subject || "", html: tpl.html || "" });
    toast.success(`"${tpl.name}" editöre yüklendi — düzenleyip önizleyebilirsin`);
    try { window.scrollTo({ top: document.getElementById("yeni-kampanya")?.offsetTop || 0, behavior: "smooth" }); } catch {}
  };

  const openPreview = async (tpl) => {
    try {
      const r = await axios.post(`${API}/admin/email-marketing/preview`,
        { subject: tpl.subject, html: tpl.html }, { headers: h() });
      setPreviewModal({ html: r.data?.html || "", subject: tpl.subject });
    } catch { toast.error("Önizleme alınamadı"); }
  };

  const saveAsTemplate = async () => {
    if (!camp.html.trim()) { toast.error("Önce içerik (HTML) girin"); return; }
    const name = window.prompt("Şablon adı:", camp.subject || "Yeni Şablon");
    if (!name || !name.trim()) return;
    const category = window.prompt("Kategori (opsiyonel):", "Genel") || "Genel";
    setBusy("savetpl");
    try {
      await axios.post(`${API}/admin/email-marketing/templates`,
        { name: name.trim(), subject: camp.subject, html: camp.html, category: category.trim() }, { headers: h() });
      toast.success("Şablon kaydedildi");
      load();
    } catch (e) { toast.error(e.response?.data?.detail || "Kaydedilemedi"); }
    finally { setBusy(""); }
  };

  const deleteTemplate = async (tpl) => {
    if (!window.confirm(`"${tpl.name}" şablonu silinsin mi?`)) return;
    try {
      await axios.delete(`${API}/admin/email-marketing/templates/${tpl.id}`, { headers: h() });
      toast.success("Şablon silindi");
      load();
    } catch (e) { toast.error(e.response?.data?.detail || "Silinemedi"); }
  };

  const saveCfg = async () => {
    setBusy("save");
    try {
      await axios.put(`${API}/admin/email-marketing/settings`, cfg, { headers: h() });
      toast.success("E-posta sağlayıcı ayarları kaydedildi");
      load();
    } catch (e) { toast.error(e.response?.data?.detail || "Kaydedilemedi"); }
    finally { setBusy(""); }
  };

  const sendTest = async () => {
    if (!testTo.trim()) { toast.error("Test e-posta adresi girin"); return; }
    setBusy("test");
    try {
      const r = await axios.post(`${API}/admin/email-marketing/test`, { to: testTo.trim() }, { headers: h() });
      toast.success("Test maili gönderildi");
    } catch (e) { toast.error(e.response?.data?.detail || "Gönderilemedi"); }
    finally { setBusy(""); }
  };

  const sendCampaign = async () => {
    if (!camp.subject.trim() || !camp.html.trim()) { toast.error("Konu ve içerik zorunlu"); return; }
    if (!window.confirm(`${aud.eligible} e-posta izinli alıcıya "${camp.subject}" kampanyası gönderilecek. Onaylıyor musun?`)) return;
    setBusy("campaign");
    try {
      const r = await axios.post(`${API}/admin/email-marketing/campaigns`, camp, { headers: h() });
      toast.success(`Kampanya başlatıldı — ${r.data?.eligible} aboneye gönderiliyor (arka planda)`);
      setCamp({ subject: "", html: "" });
      setTimeout(load, 1500);
    } catch (e) { toast.error(e.response?.data?.detail || "Başlatılamadı"); }
    finally { setBusy(""); }
  };

  // Composer'daki güncel içeriği TEST olarak kendine gönder (markalı).
  const sendCampaignTest = async () => {
    if (!camp.subject.trim() || !camp.html.trim()) { toast.error("Önce konu ve içerik girin"); return; }
    const to = (campTestTo || "").trim();
    if (!to) { toast.error("Test alıcısı e-posta adresi girin"); return; }
    try { localStorage.setItem("emailTestTo", to); } catch {}
    setBusy("camptest");
    try {
      await axios.post(`${API}/admin/email-marketing/test`, { to, subject: camp.subject, html: camp.html }, { headers: h() });
      toast.success(`Test e-postası ${to} adresine gönderildi (markalı)`);
    } catch (e) {
      toast.error(e.response?.data?.detail || "Test gönderilemedi");
    } finally { setBusy(""); }
  };

  const field = (label, key, type = "text", ph = "") => (
    <label className="block">
      <span className="text-xs font-medium text-gray-600">{label}</span>
      <input type={type} value={cfg[key] || ""} placeholder={ph}
        onChange={(e) => setCfg((p) => ({ ...p, [key]: e.target.value }))}
        className="mt-1 w-full border rounded-lg px-3 py-2 text-sm" />
    </label>
  );

  const validateProvider = async () => {
    setBusy("validate");
    try {
      const r = await axios.post(`${API}/admin/email-marketing/validate-provider`, {}, { headers: h() });
      toast.success(`${String(r.data?.provider || "Sağlayıcı").toUpperCase()} bağlantısı doğrulandı`);
    } catch (e) { toast.error(e.response?.data?.detail || "Bağlantı doğrulanamadı"); }
    finally { setBusy(""); }
  };

  return (
    <div className="p-4 md:p-6 max-w-4xl mx-auto space-y-6">
      <div className="flex items-center gap-2">
        <Mail size={22} /><h1 className="text-xl font-bold">E-posta Pazarlama</h1>
      </div>

      {/* Kitle */}
      <div className="bg-white border rounded-xl p-4 space-y-3" data-testid="email-audience-card">
        <div className="flex items-center gap-4">
          <Users size={20} className="text-gray-500" />
          <div className="flex-1">
            <p className="text-sm font-semibold">{aud.eligible} e-posta izinli alıcı</p>
            <p className="text-xs text-gray-500">
              Bülten aboneliği {aud.breakdown?.newsletter ?? "–"} · ödeme sayfası / üyelik (İYS) izni {aud.breakdown?.iys ?? "–"} · profil tercihi {aud.breakdown?.profile ?? "–"}
              {aud.suppressed ? ` · kara liste ${aud.suppressed}` : ""}{aud.breakdown?.ret ? ` · vazgeçen ${aud.breakdown.ret}` : ""}
            </p>
          </div>
          <span className={`text-xs px-2 py-1 rounded-full font-semibold ${configured ? "bg-green-100 text-green-700" : "bg-amber-100 text-amber-700"}`}>
            {configured ? `${(readyProvider || "brevo").toUpperCase()} hazır` : "Sağlayıcı ayarı eksik"}
          </span>
        </div>
        <div className="flex flex-wrap items-center gap-2 border-t pt-3">
          <input value={diagEmail} onChange={(e) => setDiagEmail(e.target.value)} onKeyDown={(e) => e.key === "Enter" && runDiag()}
            placeholder="Bu adrese mail gitti mi? e-posta yaz…" className="border rounded-lg px-3 py-1.5 text-sm w-72" data-testid="email-diagnose-input" />
          <button onClick={runDiag} disabled={diagBusy} className="text-xs border rounded-lg px-3 py-1.5 hover:bg-gray-50 disabled:opacity-50" data-testid="email-diagnose-btn">
            {diagBusy ? "Sorgulanıyor…" : "Sorgula"}
          </button>
          {diag && <button onClick={() => setDiag(null)} className="text-xs text-gray-400 hover:text-gray-600">kapat</button>}
        </div>
        {diag && (
          <div className="text-xs space-y-2 bg-gray-50 rounded-lg p-3" data-testid="email-diagnose-result">
            <div className={`font-semibold ${diag.in_audience ? "text-emerald-700" : "text-red-600"}`}>{diag.email}: {diag.reason}</div>
            <div className="grid sm:grid-cols-2 gap-x-6 gap-y-1 text-gray-600">
              <div>Bülten aboneliği: {diag.newsletter ? `${diag.newsletter.consent && diag.newsletter.active !== false ? "AKTİF" : "PASİF"} (${diag.newsletter.source || "footer"})` : "kayıt yok"}</div>
              <div>Profil pazarlama tercihi: {diag.user ? (diag.user.accepts_marketing ? "EVET" : "hayır") : "üye değil"}</div>
              <div>İYS e-posta izni: {diag.iys?.length ? diag.iys.map((x) => `${x.status || "ONAY"} (${x.source || ""} ${String(x.consent_date || x.created_at || "").slice(0, 10)})`).slice(0, 3).join(", ") : "kayıt yok"}</div>
              <div>Kara liste: {diag.suppressed ? `EVET (${diag.suppressed.reason || ""})` : "hayır"}</div>
            </div>
            <div>
              <div className="font-semibold text-gray-700 mt-1">Kampanya gönderimleri (alıcı kaydı olanlar)</div>
              {diag.sends?.length ? diag.sends.map((x, i) => (
                <div key={i}>{String(x.at || "").slice(0, 16).replace("T", " ")} · {x.subject || x.campaign_id} · <b>{x.status}</b>{x.error ? ` · ${x.error}` : ""}</div>
              )) : <div className="text-gray-500">Bu adrese kayıtlı gönderim yok.</div>}
              {diag.older_campaigns_without_log?.length > 0 && (
                <div className="text-gray-500 mt-1">
                  Alıcı kaydı tutulmayan eski kampanyalar ({diag.older_campaigns_without_log.length}): o dönemde kitle YALNIZ bülten aboneleriydi; bu adres bülten abonesi değilse mail gitmemiştir.
                </div>
              )}
            </div>
          </div>
        )}
      </div>

      {/* Brevo + yedek SES ayarları */}
      {canEditSettings ? <div className="bg-white border rounded-xl p-4 space-y-4">
        <div className="flex items-center justify-between">
          <h2 className="font-semibold text-sm">Gönderim Sağlayıcısı</h2>
          <select value={cfg.provider || "brevo"} onChange={(e) => setCfg((p) => ({ ...p, provider: e.target.value }))}
            className="border rounded-lg px-3 py-2 text-sm font-medium">
            <option value="brevo">Brevo (birincil)</option>
            <option value="ses">Amazon SES (yedek / manuel)</option>
          </select>
        </div>
        <div className="rounded-xl border border-blue-200 bg-blue-50/40 p-4 space-y-3">
          <div className="flex items-center justify-between">
            <div>
              <div className="text-sm font-semibold">Brevo Marketing</div>
              <div className="text-[11px] text-gray-500">Kişiler yerel KVKK/İYS rızasına göre Brevo listesiyle eşitlenir; kampanya Brevo Marketing API üzerinden gönderilir.</div>
            </div>
            <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={!!cfg.brevo_enabled} onChange={(e) => setCfg((p) => ({ ...p, brevo_enabled: e.target.checked }))} className="accent-black" /> Aktif</label>
          </div>
          <div className="grid md:grid-cols-2 gap-3">
            {field("Brevo API anahtarı", "brevo_api_key", "password", "xkeysib-...")}
            {field("Brevo kişi listesi ID", "brevo_list_id", "number", "12")}
            {field("Doğrulanmış gönderen adresi", "brevo_from_email", "email", "bulten@meluni.com.tr")}
            {field("Gönderen adı", "brevo_from_name", "text", "Meluni")}
            {field("Yanıt adresi", "brevo_reply_to", "email", "info@meluni.com.tr")}
            {field("Webhook güvenlik anahtarı", "brevo_webhook_secret", "password", "uzun-rastgele-bir-anahtar")}
          </div>
          <p className="text-[11px] text-gray-500">Brevo webhook adresi: <code>/api/email-marketing/brevo-webhook?key=GÜVENLİK_ANAHTARI</code> · unsubscribe, spam ve hard bounce olaylarını seçin.</p>
        </div>
        <details className="rounded-xl border p-4" open={cfg.provider === "ses"}>
          <summary className="cursor-pointer text-sm font-semibold">Amazon SES yedek ayarları</summary>
          <div className="mt-3 space-y-3">
            <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={!!cfg.ses_enabled} onChange={(e) => setCfg((p) => ({ ...p, ses_enabled: e.target.checked }))} className="accent-black" /> SES aktif</label>
            <div className="grid md:grid-cols-2 gap-3">
              {field("AWS Bölgesi", "ses_region", "text", "eu-west-1")}
              {field("Doğrulanmış gönderen", "ses_from_email", "email", "bulten@meluni.com.tr")}
              {field("IAM Access Key ID", "ses_access_key")}
              {field("IAM Secret Access Key", "ses_secret_key", "password", "••••••")}
              {field("Gönderen adı", "ses_from_name", "text", "Meluni")}
              {field("Yanıt adresi", "ses_reply_to", "email", "info@meluni.com.tr")}
              {field("Configuration Set", "ses_configuration_set", "text", "opsiyonel")}
            </div>
          </div>
        </details>
        <label className="flex items-center gap-2 text-sm cursor-pointer">
          <input type="checkbox" checked={!!cfg.fallback_enabled} onChange={(e) => setCfg((p) => ({ ...p, fallback_enabled: e.target.checked }))} className="accent-black" />
          Birincil sağlayıcı yapılandırılmamışsa diğer hazır sağlayıcıyı yedek olarak kullan
        </label>
        <div className="flex flex-wrap items-center gap-3 pt-2">
          <button onClick={saveCfg} disabled={busy === "save"} className="inline-flex items-center gap-2 bg-black text-white px-4 py-2 rounded-lg text-sm font-semibold hover:bg-gray-800 disabled:opacity-50">
            <Save size={15} /> Kaydet
          </button>
          <button onClick={validateProvider} disabled={busy === "validate"} className="inline-flex items-center gap-2 border px-4 py-2 rounded-lg text-sm font-semibold hover:bg-gray-50 disabled:opacity-50">
            <CheckCircle2 size={15} /> Bağlantıyı doğrula
          </button>
          <div className="flex items-center gap-2">
            <input value={testTo} onChange={(e) => setTestTo(e.target.value)} placeholder="test@ornek.com"
              className="border rounded-lg px-3 py-2 text-sm" />
            <button onClick={sendTest} disabled={busy === "test"} className="inline-flex items-center gap-2 border px-4 py-2 rounded-lg text-sm font-semibold hover:bg-gray-50 disabled:opacity-50">
              <Send size={15} /> Test gönder
            </button>
          </div>
        </div>
        <p className="text-[11px] text-gray-400 leading-relaxed">
          İşlemsel e-postalar (sipariş/şifre) Zoho ZeptoMail'den gitmeye devam eder. Brevo pazarlama için birincildir;
          Amazon SES ayarları silinmez ve gerektiğinde yedek olarak seçilebilir.
        </p>
      </div> : <p className="text-sm text-gray-500">Sağlayıcı ayarlarını yönetmek için e-posta ayarları yetkisi gerekir. Kampanya araçlarını aşağıdan kullanabilirsiniz.</p>}

      {/* Hazır Şablonlar */}
      <div className="bg-white border rounded-xl p-4">
        <div className="flex items-center gap-2 mb-3">
          <FileText size={16} className="text-gray-500" />
          <h2 className="font-semibold text-sm">Kampanya Şablonları</h2>
          <span className="text-[11px] text-gray-400">— gönderMEDEN önce önizle & düzenle</span>
        </div>
        {templates.length === 0 ? (
          <p className="text-sm text-gray-400">Şablon yükleniyor…</p>
        ) : (
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3" data-testid="tpl-grid">
            {templates.map((t) => (
              <div key={t.id} className={`border rounded-lg p-3 flex flex-col gap-2 ${t.recommended ? "border-amber-300 bg-amber-50/40" : "border-gray-200"}`} data-testid={`tpl-card-${t.id}`}>
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0">
                    <div className="font-medium text-sm text-gray-900 truncate flex items-center gap-1">
                      {t.recommended && <Star size={13} className="text-amber-500 shrink-0" fill="currentColor" />}
                      {t.name}
                    </div>
                    <div className="text-[11px] text-gray-400">{t.category || "Genel"}{t.builtin ? " · hazır" : ""}</div>
                  </div>
                  {!t.builtin && (
                    <button onClick={() => deleteTemplate(t)} title="Sil" className="text-gray-300 hover:text-red-600 shrink-0"><Trash2 size={14} /></button>
                  )}
                </div>
                <div className="text-[11px] text-gray-500 line-clamp-2">{t.subject}</div>
                <div className="flex gap-2 mt-auto pt-1">
                  <button onClick={() => useTemplate(t)} className="flex-1 inline-flex items-center justify-center gap-1 bg-black text-white rounded-md px-2 py-1.5 text-xs font-medium hover:bg-gray-800" data-testid={`tpl-use-${t.id}`}>
                    <Copy size={12} /> Kullan
                  </button>
                  <button onClick={() => openPreview(t)} className="inline-flex items-center justify-center gap-1 border rounded-md px-2 py-1.5 text-xs hover:bg-gray-50" data-testid={`tpl-preview-${t.id}`}>
                    <Eye size={12} /> Önizle
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}
        <p className="text-[11px] text-gray-400 mt-3 leading-relaxed">
          Placeholder'lar: <code>{"{customer_name}"}</code> (müşteri/abone adı — otomatik dolar), <code>{"{kod}"}</code> (kupon), <code>{"{indirim}"}</code> (oran),
          <code> URUN_LINKI</code> / <code>GORSEL_URL</code> (kendi bağlantın). "Kullan" ile editöre yükle, düzenle, önizle, sonra gönder.
        </p>
      </div>

      {/* Kampanya oluştur + CANLI ÖNİZLEME */}
      <div id="yeni-kampanya" className="bg-white border rounded-xl p-4 space-y-3">
        <div className="flex items-center justify-between">
          <h2 className="font-semibold text-sm">Yeni Kampanya</h2>
          <button onClick={saveAsTemplate} disabled={busy === "savetpl" || !camp.html.trim()}
            className="inline-flex items-center gap-1.5 border rounded-lg px-3 py-1.5 text-xs font-medium hover:bg-gray-50 disabled:opacity-50" data-testid="save-tpl-btn">
            <Save size={13} /> Şablon olarak kaydet
          </button>
        </div>
        <input value={camp.subject} onChange={(e) => setCamp((p) => ({ ...p, subject: e.target.value }))}
          placeholder="Konu (ör. Yeni Sezon Geldi)" className="w-full border rounded-lg px-3 py-2 text-sm" data-testid="camp-subject" />

        {/* Görselli ÜRÜN EKLE seçici */}
        <div className="border rounded-lg">
          <button type="button" onClick={() => setPickerOpen((o) => !o)}
            className="w-full flex items-center gap-2 px-3 py-2 text-sm font-medium text-gray-700 hover:bg-gray-50" data-testid="picker-toggle">
            <Image size={15} className="text-gray-500" /> Görselli Ürün Ekle
            <span className="text-[11px] text-gray-400">— kategori seç, ürünleri işaretle, karta ekle</span>
            <span className="ml-auto text-gray-400">{pickerOpen ? "−" : "+"}</span>
          </button>
          {pickerOpen && (
            <div className="border-t p-3 space-y-3">
              <div className="flex flex-wrap items-center gap-2">
                <select value={pickerCat} onChange={(e) => loadPickerProducts(e.target.value)}
                  className="border rounded-lg px-2.5 py-2 text-sm min-w-[220px]" data-testid="picker-category">
                  <option value="">Kategori seçin…</option>
                  {pickerCats.map((c) => <option key={c.id} value={c.id}>{c.full_name || c.name}</option>)}
                </select>
                <button type="button" onClick={insertSelectedProducts} disabled={pickerSel.size === 0}
                  className="inline-flex items-center gap-1.5 bg-black text-white rounded-lg px-3 py-2 text-sm font-medium hover:bg-gray-800 disabled:opacity-40" data-testid="picker-insert">
                  <SquarePlus size={14} /> Seçilenleri Ekle ({pickerSel.size})
                </button>
              </div>
              {pickerLoading ? (
                <p className="text-xs text-gray-400 py-4 text-center">Ürünler yükleniyor…</p>
              ) : !pickerCat ? (
                <p className="text-xs text-gray-400 py-4 text-center">Ürünleri görmek için bir kategori seçin.</p>
              ) : pickerProducts.length === 0 ? (
                <p className="text-xs text-gray-400 py-4 text-center">Bu kategoride görselli aktif ürün bulunamadı.</p>
              ) : (
                <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-2 max-h-72 overflow-y-auto" data-testid="picker-grid">
                  {pickerProducts.map((p) => {
                    const on = pickerSel.has(p.id);
                    return (
                      <button key={p.id} type="button" onClick={() => togglePick(p.id)}
                        className={`text-left border rounded-lg overflow-hidden hover:border-black transition-colors ${on ? "ring-2 ring-black border-black" : "border-gray-200"}`}
                        data-testid={`picker-item-${p.id}`}>
                        <div className="relative">
                          <img src={p.image} alt={p.name} loading="lazy" className="w-full aspect-[3/4] object-cover bg-gray-50" />
                          {on && <span className="absolute top-1 right-1 bg-black text-white rounded-full w-5 h-5 flex items-center justify-center text-[10px]">✓</span>}
                        </div>
                        <div className="p-1.5">
                          <div className="text-[11px] leading-tight text-gray-800 line-clamp-2">{p.name}</div>
                          <div className="text-[11px] font-semibold mt-0.5">
                            {p.sale_price && p.sale_price < p.price
                              ? <>{Number(p.sale_price).toLocaleString("tr-TR")} TL <span className="text-gray-400 line-through font-normal">{Number(p.price).toLocaleString("tr-TR")}</span></>
                              : <>{Number(p.price || 0).toLocaleString("tr-TR")} TL</>}
                          </div>
                        </div>
                      </button>
                    );
                  })}
                </div>
              )}
            </div>
          )}
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 items-start">
          {/* Editör: Görsel (WYSIWYG) / HTML modu */}
          <div className="space-y-2 min-w-0">
            <div className="flex items-center justify-between flex-wrap gap-2">
              <div className="inline-flex rounded-lg border overflow-hidden text-xs">
                <button onClick={() => setEditMode("visual")} data-testid="mode-visual"
                  className={`px-3 py-1.5 font-medium ${editMode === "visual" ? "bg-black text-white" : "bg-white text-gray-600 hover:bg-gray-50"}`}>Görsel</button>
                <button onClick={() => setEditMode("html")} data-testid="mode-html"
                  className={`px-3 py-1.5 font-medium inline-flex items-center gap-1 ${editMode === "html" ? "bg-black text-white" : "bg-white text-gray-600 hover:bg-gray-50"}`}><Code size={12} /> HTML</button>
              </div>
              <div className="flex items-center gap-2 flex-wrap">
                {/* Serbest GÖRSEL EKLE — herhangi bir dosyayı R2/cdn'e yükler (ürün seçiciyle İLGİSİZ).
                    Hem Görsel hem HTML modunda çalışır. */}
                <input ref={imgFileRef} type="file" accept="image/*" onChange={onPickImageFile} className="hidden" data-testid="email-image-file" />
                <button type="button" onClick={() => imgFileRef.current && imgFileRef.current.click()} disabled={imgUploading}
                  data-testid="email-image-add"
                  className="inline-flex items-center gap-1 border rounded-lg px-2.5 py-1.5 text-xs font-medium hover:bg-gray-50 disabled:opacity-50">
                  {imgUploading ? <RefreshCw size={13} className="animate-spin" /> : <ImagePlus size={14} />}
                  {imgUploading ? "Yükleniyor…" : "Görsel Ekle"}
                </button>
                <select onChange={(e) => { insertPlaceholder(e.target.value); e.target.value = ""; }} defaultValue=""
                  disabled={editMode !== "visual"} className="border rounded-lg px-2 py-1.5 text-xs disabled:opacity-40" title="İmleç konumuna placeholder ekle">
                  <option value="" disabled>+ Placeholder</option>
                  <option value="{customer_name}">{"{customer_name}"} — müşteri/abone adı (otomatik dolar)</option>
                  <option value="{kod}">{"{kod}"} — kupon kodu</option>
                  <option value="{indirim}">{"{indirim}"} — indirim oranı</option>
                  <option value="URUN_LINKI">URUN_LINKI — bağlantı</option>
                  <option value="GORSEL_URL">GORSEL_URL — görsel</option>
                </select>
              </div>
            </div>
            {lastImageUrl && (
              <div className="flex items-center gap-2 rounded-lg border border-emerald-200 bg-emerald-50 px-2.5 py-1.5">
                <span className="text-[11px] text-emerald-700 font-medium shrink-0">Yüklenen görsel (cdn):</span>
                <input readOnly value={lastImageUrl} onFocus={(e) => e.target.select()} data-testid="email-image-url"
                  className="flex-1 min-w-0 bg-white border rounded px-2 py-1 text-[11px] text-gray-700" />
                <button type="button" onClick={copyImageUrl} title="URL'yi kopyala"
                  className="inline-flex items-center gap-1 border rounded px-2 py-1 text-[11px] hover:bg-white shrink-0"><Copy size={12} /> Kopyala</button>
              </div>
            )}

            {editMode === "visual" ? (
              <div className="border rounded-lg overflow-hidden">
                {/* Araç çubuğu */}
                <div className="flex flex-wrap items-center gap-0.5 bg-gray-50 border-b px-1.5 py-1">
                  {[
                    { t: "Kalın", i: <Bold size={14} />, run: () => exec("bold") },
                    { t: "İtalik", i: <Italic size={14} />, run: () => exec("italic") },
                    { t: "Başlık", i: <Heading size={14} />, run: () => exec("formatBlock", "H2") },
                    { t: "Liste", i: <List size={14} />, run: () => exec("insertUnorderedList") },
                    { t: "Link", i: <Link2 size={14} />, run: addLink },
                    { t: "Sola", i: <AlignLeft size={14} />, run: () => exec("justifyLeft") },
                    { t: "Ortala", i: <AlignCenter size={14} />, run: () => exec("justifyCenter") },
                    { t: "Sağa", i: <AlignRight size={14} />, run: () => exec("justifyRight") },
                  ].map((b) => (
                    <button key={b.t} type="button" title={b.t} onMouseDown={(e) => e.preventDefault()} onClick={b.run}
                      className="p-1.5 rounded hover:bg-gray-200 text-gray-700">{b.i}</button>
                  ))}
                  <span className="w-px h-4 bg-gray-300 mx-1" />
                  <button type="button" onMouseDown={(e) => e.preventDefault()} onClick={addButton} title="CTA butonu ekle"
                    className="inline-flex items-center gap-1 px-2 py-1 rounded hover:bg-gray-200 text-gray-700 text-xs"><SquarePlus size={13} /> Buton</button>
                </div>
                {/* Düzenlenebilir gövde (yalnız iç gövde — marka kabuğu gönderimde eklenir) */}
                <div ref={visualRef} contentEditable suppressContentEditableWarning onInput={onVisualInput}
                  data-testid="visual-editor"
                  className="p-4 text-sm text-gray-800 leading-relaxed focus:outline-none overflow-auto bg-white"
                  style={{ minHeight: 360, maxHeight: 460 }} />
                <div className="text-[11px] text-gray-400 px-3 py-1.5 border-t bg-gray-50">
                  Yalnız iç gövdeyi düzenlersiniz; MELUNI başlık/footer gönderimde otomatik eklenir. Placeholder'lar düz metindir.
                </div>
              </div>
            ) : (
              <textarea ref={htmlRef} value={camp.html} onChange={(e) => setCamp((p) => ({ ...p, html: e.target.value }))}
                placeholder="HTML içerik — <p>Merhaba {customer_name}...</p> (marka kabuğu: logo + footer + abonelikten-çık otomatik eklenir)"
                rows={20} className="w-full border rounded-lg px-3 py-2 text-sm font-mono" style={{ minHeight: 400 }} data-testid="camp-html" />
            )}
          </div>

          {/* Canlı önizleme (tam markalı) — 600px e-posta panele ölçeklenir, yatay kırpma yok */}
          <div ref={previewColRef} className="space-y-2 min-w-0">
            <div className="flex items-center gap-2 text-xs text-gray-500">
              <Eye size={13} /> Canlı Önizleme (gönderilecek gerçek görünüm) — %{Math.round(pvScale * 100)}
            </div>
            <div className="border rounded-lg overflow-auto bg-gray-100" style={{ height: 480 }}>
              {previewHtml ? (
                <div style={{ width: EMAIL_W * pvScale, height: pvH * pvScale, margin: "0 auto" }}>
                  <iframe title="onizleme" srcDoc={previewHtml} onLoad={onPreviewLoad} data-testid="live-preview"
                    sandbox="allow-same-origin" referrerPolicy="no-referrer"
                    style={{ width: EMAIL_W, height: pvH, transform: `scale(${pvScale})`, transformOrigin: "top left", border: 0, background: "#fff", display: "block" }} />
                </div>
              ) : (
                <div className="h-full flex items-center justify-center text-xs text-gray-400 text-center px-4">
                  Konu / içerik yazınca ya da bir şablonu "Kullan" deyince markalı önizleme burada görünür.
                </div>
              )}
            </div>
          </div>
        </div>
        <div className="flex flex-wrap items-center justify-end gap-2 pt-1 border-t">
          {/* Test olarak gönder — editördeki güncel içeriği kendine/doğrulanmış adrese */}
          <div className="flex items-center gap-1.5 mr-auto">
            <input type="email" value={campTestTo} onChange={(e) => setCampTestTo(e.target.value)}
              placeholder="test@ornek.com" className="border rounded-lg px-2.5 py-2 text-sm w-52" data-testid="camp-test-to" />
            <button onClick={sendCampaignTest} disabled={busy === "camptest" || !configured}
              className="inline-flex items-center gap-1.5 border rounded-lg px-3 py-2 text-sm font-medium hover:bg-gray-50 disabled:opacity-50" data-testid="camp-test-btn">
              <Send size={14} /> Test olarak gönder
            </button>
          </div>
          <button onClick={sendCampaign} disabled={busy === "campaign" || !configured} className="inline-flex items-center gap-2 bg-black text-white px-5 py-2 rounded-lg text-sm font-bold hover:bg-gray-800 disabled:opacity-50">
            <Send size={15} /> {aud.eligible} alıcıya gönder
          </button>
        </div>
        <p className="text-[11px] text-gray-400 text-right">
          Toplu kampanya, gönderim anında rızalı kitleyi Brevo listesiyle eşitler. Brevo kabulü teslimat değildir; gerçek teslim/açılma sonuçları Brevo raporundan izlenir.
        </p>
      </div>

      {/* Geçmiş */}
      <div className="bg-white border rounded-xl p-4">
        <div className="flex items-center justify-between mb-3">
          <h2 className="font-semibold text-sm">Kampanya Geçmişi</h2>
          <button onClick={load} className="text-gray-400 hover:text-black"><RefreshCw size={16} /></button>
        </div>
        {campaigns.length === 0 ? (
          <p className="text-sm text-gray-400">Henüz kampanya yok.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead><tr className="text-left text-xs text-gray-500 border-b">
                <th className="py-2">Konu</th><th>Sağlayıcı</th><th>Durum</th><th className="text-right">Gönderildi</th><th className="text-right">Hata</th><th className="text-right">Hedef</th><th className="text-right">Tarih</th><th className="text-right">Rapor</th>
              </tr></thead>
              <tbody>
                {campaigns.map((c) => (
                  <tr key={c.id} className="border-b last:border-0">
                    <td className="py-2 pr-2">{c.subject}</td>
                    <td className="uppercase text-xs font-medium text-gray-500">{c.provider || "ses"}</td>
                    <td>
                      <span className={`inline-flex items-center gap-1 text-xs font-semibold ${["sent", "submitted"].includes(c.status) ? "text-green-600" : ["sending", "syncing"].includes(c.status) ? "text-blue-600" : c.status === "failed" ? "text-red-600" : "text-gray-500"}`}>
                        {["sent", "submitted"].includes(c.status) ? <CheckCircle2 size={13} /> : c.status === "failed" ? <AlertTriangle size={13} /> : null}
                        {c.status === "submitted" ? "Brevo'ya iletildi" : c.status === "syncing" ? "Kitle eşitleniyor" : c.status === "needs_review" ? "Sonuç doğrulanmalı" : c.status}
                      </span>
                    </td>
                    <td className="text-right">{c.sent || 0}</td>
                    <td className="text-right text-red-500">
                      {(c.failed || 0) > 0 && c.error_sample ? (
                        <span className="inline-flex items-center gap-1 cursor-help" title={`Hata sebebi: ${c.error_sample}`}>
                          {c.failed}<AlertTriangle size={12} />
                        </span>
                      ) : (c.failed || 0)}
                    </td>
                    <td className="text-right">{c.total || 0}</td>
                    <td className="text-right text-xs text-gray-400">{c.created_at ? new Date(c.created_at).toLocaleString("tr-TR") : ""}</td>
                    <td className="text-right">
                      <div className="flex flex-col items-end gap-1">
                        <button onClick={() => openReport(c)} disabled={repBusy} className="text-xs border rounded px-2 py-1 hover:bg-gray-50 disabled:opacity-50" data-testid={`camp-report-${c.id}`}>Alıcılar & Dönüşüm</button>
                        {!["queued", "sending", "syncing"].includes(c.status) && (
                          <button onClick={() => sendMissing(c)} disabled={missBusy === c.id} className="text-xs border border-gray-900 rounded px-2 py-1 hover:bg-gray-900 hover:text-white disabled:opacity-50" data-testid={`camp-send-missing-${c.id}`}>
                            {missBusy === c.id ? "Hesaplanıyor…" : "Almayanlara gönder"}
                          </button>
                        )}
                        {c.resend_of && <span className="text-[10px] text-gray-400">almayanlara tekrar</span>}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {rep && (
          <div className="mt-4 border rounded-xl p-4 bg-gray-50/60" data-testid="camp-report-panel">
            <div className="flex flex-wrap items-center justify-between gap-2 mb-3">
              <div>
                <div className="font-semibold text-sm">{rep.campaign.subject}</div>
                <div className="text-[11px] text-gray-500">{rep.campaign.created_at ? new Date(rep.campaign.created_at).toLocaleString("tr-TR") : ""} · gönderimden sonraki {rep.window_days} gün içindeki siparişler</div>
              </div>
              <div className="flex gap-2">
                <button onClick={downloadReport} className="text-xs border rounded px-3 py-1.5 hover:bg-white">Excel</button>
                <button onClick={() => setRep(null)} className="text-xs border rounded px-3 py-1.5 hover:bg-white">Kapat</button>
              </div>
            </div>
            {rep.note && <div className="text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded px-2 py-1 mb-3">{rep.note}</div>}
            <div className="grid grid-cols-2 md:grid-cols-4 gap-2 text-xs mb-3">
              {[["Alıcı", rep.summary.recipients], ["Gönderilen", rep.summary.delivered_or_submitted],
                ["Alışveriş yapan alıcı", rep.summary.buyers], ["Dönüşüm", `%${rep.summary.conversion_rate}`],
                ["Sipariş adedi", rep.summary.buyer_orders], ["Ciro", `${Number(rep.summary.buyer_revenue).toLocaleString("tr-TR", { minimumFractionDigits: 2 })} TL`],
                ["Maildeki linkten gelen sipariş", rep.summary.via_link_orders], ["Linkten gelen ciro", `${Number(rep.summary.via_link_revenue).toLocaleString("tr-TR", { minimumFractionDigits: 2 })} TL`]].map(([l, v]) => (
                <div key={l} className="bg-white border rounded p-2"><div className="text-gray-500">{l}</div><div className="text-base font-bold">{v}</div></div>
              ))}
            </div>
            <div className="overflow-auto max-h-80 bg-white border rounded">
              <table className="w-full text-xs">
                <thead className="bg-gray-50 text-gray-500 sticky top-0"><tr><th className="text-left p-2">E-posta</th><th className="text-left p-2">Gönderim</th><th className="text-left p-2">Alışveriş</th><th className="text-left p-2">Sipariş</th></tr></thead>
                <tbody>
                  {rep.recipients.length === 0 ? (
                    <tr><td colSpan={4} className="p-4 text-center text-gray-400">Alıcı kaydı yok</td></tr>
                  ) : rep.recipients.map((r, i) => (
                    <tr key={i} className={`border-t ${r.purchased ? "bg-emerald-50/60" : ""}`}>
                      <td className="p-2 font-mono">{r.email}</td>
                      <td className="p-2">{r.status === "failed" ? <span className="text-red-600" title={r.error}>hata</span> : r.status}</td>
                      <td className="p-2">{r.purchased ? <span className="text-emerald-700 font-semibold">EVET</span> : "—"}</td>
                      <td className="p-2">{r.orders.map((o) => `${o.order_number} (${Number(o.total).toFixed(2)} TL${o.via_link ? " · linkten" : ""})`).join(", ")}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </div>

      {/* Şablon önizleme modalı (tam markalı) */}
      {previewModal && (
        <div className="fixed inset-0 z-[70] flex items-center justify-center p-4" onClick={() => setPreviewModal(null)}>
          <div className="absolute inset-0 bg-black/50" />
          <div className="relative bg-white rounded-xl w-full max-w-2xl h-[80vh] flex flex-col shadow-xl" onClick={(e) => e.stopPropagation()} data-testid="preview-modal">
            <div className="flex items-center justify-between px-4 py-3 border-b">
              <div className="text-sm font-semibold truncate">Önizleme · {previewModal.subject || "(konu yok)"}</div>
              <button onClick={() => setPreviewModal(null)} className="text-gray-400 hover:text-black"><X size={18} /></button>
            </div>
            <iframe title="tpl-onizleme" srcDoc={previewModal.html} sandbox="allow-same-origin" referrerPolicy="no-referrer" className="flex-1 w-full bg-white rounded-b-xl" />
          </div>
        </div>
      )}
    </div>
  );
}
