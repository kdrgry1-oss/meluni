/**
 * Amazon SP-API Entegrasyon ayar sayfası.
 * SigV4/AWS GEREKMEZ — sadece LWA (client_id + client_secret + refresh_token).
 */
import { useState, useEffect, useCallback } from "react";
import axios from "axios";
import { toast } from "sonner";
import AmazonAutoSetup from "../../components/admin/AmazonAutoSetup";
import {
  ShoppingCart, CheckCircle, XCircle, RefreshCw, Save, Zap, Package, ExternalLink, KeyRound, Link2,
} from "lucide-react";

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;
const REDIRECT_URI = `${process.env.REACT_APP_BACKEND_URL}/api/amazon/spapi/oauth/callback`;
const auth = () => ({ headers: { Authorization: `Bearer ${localStorage.getItem("token")}` } });

export default function AmazonSpApi({ embedded = false }) {
  const [status, setStatus] = useState(null);
  const [form, setForm] = useState({
    client_id: "", client_secret: "", refresh_token: "", app_id: "",
    marketplace_id: "A33AVAJ2PDY3EV", region: "eu", markup: 0, selling_partner_id: "",
  });
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [marketplaces, setMarketplaces] = useState(null);
  const [orders, setOrders] = useState(null);
  const [loadingOrders, setLoadingOrders] = useState(false);

  const loadStatus = useCallback(async () => {
    try {
      const r = await axios.get(`${API}/amazon/spapi/status`, auth());
      setStatus(r.data);
      setForm((f) => ({
        ...f,
        client_id: r.data.client_id || f.client_id,
        app_id: r.data.app_id || f.app_id,
        marketplace_id: r.data.marketplace_id || f.marketplace_id,
        region: r.data.region || f.region,
        markup: r.data.markup != null ? r.data.markup : f.markup,
        selling_partner_id: r.data.selling_partner_id || f.selling_partner_id,
      }));
    } catch {
      toast.error("Durum yüklenemedi");
    }
  }, []);

  useEffect(() => { loadStatus(); }, [loadStatus]);

  // OAuth callback dönüş durumu
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const st = params.get("status");
    if (st === "connected") { toast.success("Amazon yetkilendirme başarılı ✓"); loadStatus(); }
    else if (st === "state_mismatch") toast.error("Güvenlik doğrulaması başarısız (state)");
    else if (st === "exchange_failed") toast.error("Token alınamadı — Client Secret'ı kontrol edin");
    else if (st === "no_refresh_token") toast.error("Refresh token alınamadı");
    else if (st === "error") toast.error("Yetkilendirme iptal edildi/başarısız");
    if (st) window.history.replaceState({}, "", "/admin/amazon?tab=connection");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const connectOAuth = async () => {
    try {
      const r = await axios.get(`${API}/amazon/spapi/authorize-url`, auth());
      window.location.href = r.data.url;
    } catch (e) {
      toast.error(e.response?.data?.detail || "Yetkilendirme başlatılamadı");
    }
  };

  const copyRedirect = () => {
    navigator.clipboard?.writeText(REDIRECT_URI);
    toast.success("Redirect URI kopyalandı");
  };

  const set = (k, v) => setForm((f) => ({ ...f, [k]: v }));

  const save = async () => {
    if (!form.client_id.trim()) return toast.error("Client ID gerekli");
    setSaving(true);
    try {
      const payload = { ...form };
      // boş secret/token gönderme (mevcut korunur)
      if (!payload.client_secret) delete payload.client_secret;
      if (!payload.refresh_token) delete payload.refresh_token;
      await axios.post(`${API}/amazon/spapi/config`, payload, auth());
      toast.success("Ayarlar kaydedildi");
      setForm((f) => ({ ...f, client_secret: "", refresh_token: "" }));
      loadStatus();
    } catch (e) {
      toast.error(e.response?.data?.detail || "Kaydedilemedi");
    } finally {
      setSaving(false);
    }
  };

  const test = async () => {
    setTesting(true);
    setMarketplaces(null);
    try {
      const r = await axios.post(`${API}/amazon/spapi/test`, {}, auth());
      if (r.data.success) {
        setMarketplaces(r.data.marketplaces);
        toast.success("Bağlantı başarılı ✓");
      } else {
        toast.error(`Test başarısız (${r.data.status}): ${JSON.stringify(r.data.error)?.slice(0, 160)}`);
      }
    } catch (e) {
      toast.error(e.response?.data?.detail || "Test hatası");
    } finally {
      setTesting(false);
    }
  };

  const loadOrders = async () => {
    setLoadingOrders(true);
    setOrders(null);
    try {
      const r = await axios.get(`${API}/amazon/spapi/orders?days=30`, auth());
      if (r.data.success) {
        setOrders(r.data.orders);
        toast.success(`${r.data.count} sipariş bulundu`);
      } else {
        toast.error(`Sipariş çekilemedi (${r.data.status})`);
      }
    } catch (e) {
      toast.error(e.response?.data?.detail || "Sipariş hatası");
    } finally {
      setLoadingOrders(false);
    }
  };

  // Amazon siparişlerini GERÇEKTEN panele (db.orders) çeker — otomatik cron ile aynı iş.
  // Otomatik senkron her 10 dk çalışır; bu buton anında tetikler + doğrulama için özet döner.
  const [pulling, setPulling] = useState(false);
  const pullOrders = async () => {
    setPulling(true);
    try {
      const r = await axios.post(`${API}/amazon/spapi/orders/pull?days=7`, {}, auth());
      const s = r.data || {};
      toast.success(
        `Panele çekildi — +${s.imported || 0} yeni / ${s.updated || 0} güncellendi` +
        (s.skipped ? ` / ${s.skipped} ertelendi` : "") +
        (s.errors ? ` / ${s.errors} hata` : "")
      );
    } catch (e) {
      toast.error(e.response?.data?.detail || "Panele çekme hatası");
    } finally {
      setPulling(false);
    }
  };

  // TEŞHİS: bir sipariş no ver → Amazon ham SKU/isim + Meluni eşleşme denemesi + panel kaydı.
  const [diagNo, setDiagNo] = useState("");
  const [diag, setDiag] = useState(null);
  const [diagLoading, setDiagLoading] = useState(false);
  const runDiagnose = async () => {
    const on = (diagNo || "").trim();
    if (!on) { toast.error("Sipariş no gir"); return; }
    setDiagLoading(true);
    setDiag(null);
    try {
      const r = await axios.get(`${API}/amazon/spapi/orders/${encodeURIComponent(on)}/diagnose`, auth());
      setDiag(r.data);
    } catch (e) {
      toast.error(e.response?.data?.detail || "Teşhis hatası");
    } finally {
      setDiagLoading(false);
    }
  };

  // ÜRÜN AKTARIM ÖNİZLEME (dry-run): ada göre ürünü bul → Amazon'a gidecek tam payload.
  const [prevQ, setPrevQ] = useState("");
  const [prev, setPrev] = useState(null);
  const [prevLoading, setPrevLoading] = useState(false);
  // Listeleme teşhisi: Amazon'daki CANLI durum (issues, görsel/beden/varyasyon, görsel-erişim testi)
  const [repQ, setRepQ] = useState("");
  const [rep, setRep] = useState(null);
  const [repLoading, setRepLoading] = useState(false);
  // Katalog eşleştirme: Amazon'da (platform dışı) açılmış listeleri bizim ürünlerle eşle, stok/fiyat gönder, rapor
  const [cat, setCat] = useState(null);
  const [catRep, setCatRep] = useState(null);
  const [catBusy, setCatBusy] = useState(false);
  const loadCatStatus = useCallback(async () => {
    try { const r = await axios.get(`${API}/amazon/spapi/catalog/status`, auth()); setCat(r.data || null); } catch { /* sessiz */ }
  }, []);
  useEffect(() => { loadCatStatus(); }, [loadCatStatus]);
  useEffect(() => {
    if (cat?.status !== "running") return undefined;
    const t = setInterval(loadCatStatus, 8000);
    return () => clearInterval(t);
  }, [cat?.status, loadCatStatus]);
  const startCatSync = async () => {
    setCatBusy(true);
    try {
      const r = await axios.post(`${API}/amazon/spapi/catalog/sync`, {}, auth());
      toast(r.data?.already_running ? "Zaten çalışıyor" : "Amazon kataloğu çekiliyor (1–5 dk)…");
      setTimeout(loadCatStatus, 2000);
    } catch (e) { toast.error(e.response?.data?.detail || "Başlatılamadı"); }
    finally { setCatBusy(false); }
  };
  const loadCatReport = async () => {
    setCatBusy(true);
    try { const r = await axios.get(`${API}/amazon/spapi/catalog/report`, auth()); setCatRep(r.data || null); }
    catch (e) { toast.error(e.response?.data?.detail || "Rapor alınamadı"); }
    finally { setCatBusy(false); }
  };
  const downloadCatXlsx = async () => {
    try {
      const r = await axios.get(`${API}/amazon/spapi/catalog/report.xlsx`, { ...auth(), responseType: "blob" });
      const url = URL.createObjectURL(r.data); const a = document.createElement("a");
      a.href = url; a.download = "amazon-katalog-raporu.xlsx"; a.click(); URL.revokeObjectURL(url);
    } catch { toast.error("Excel oluşturulamadı"); }
  };
  const pushMapped = async () => {
    if (!(await window.appConfirm("Amazon'da eşleşen (platform dışı açılmış) tüm SKU'lara bizim stok ve marjlı fiyat gönderilecek. Devam?"))) return;
    setCatBusy(true);
    const t = toast.loading("Stok/fiyat gönderiliyor…");
    try {
      const r = await axios.post(`${API}/amazon/spapi/catalog/push`, {}, auth());
      const d = r.data || {};
      toast[d.dry_run ? "warning" : "success"](d.message || `${d.pushed ?? 0} gönderildi / ${d.failed ?? 0} hata`, { id: t, duration: 9000 });
    } catch (e) { toast.error(e.response?.data?.detail || "Gönderim hatası", { id: t }); }
    finally { setCatBusy(false); }
  };
  const pushMissing = async () => {
    const n = (catRep?.summary?.not_on_amazon ?? 0) + (catRep?.summary?.on_amazon_partial ?? 0);
    if (!(await window.appConfirm(`Amazon'da olmayan / bedeni eksik ${n || ""} ürün (stoğu olanlar) Amazon'a gönderilecek. Devam?`))) return;
    setCatBusy(true);
    try {
      const r = await axios.post(`${API}/amazon/spapi/auto/push-missing`, { apply: true }, auth());
      toast(r.data?.already_running ? "Gönderim zaten çalışıyor — ilerleme üstteki kartta" : "Gönderim başladı — ilerleme üstteki kartta", { duration: 8000 });
      window.dispatchEvent(new Event("amazon-push-started"));
    } catch (e) { toast.error(e.response?.data?.detail || "Gönderim başlatılamadı"); }
    finally { setCatBusy(false); }
  };
  const [cleanLoading, setCleanLoading] = useState(false);
  const runCleanup = async (confirm) => {
    const q = (repQ || "").trim();
    if (!/^\d{8,14}$/.test(q)) { toast.error("Temizlik için ürünün BARKODUNU girin"); return; }
    if (confirm && !(await window.appConfirm("Amazon'daki ESKİ/MÜKERRER çocuk SKU'lar silinecek (ana ürün ve güncel SKU'lar korunur). Devam?"))) return;
    setCleanLoading(true);
    try {
      const r = await axios.post(`${API}/amazon/spapi/listing-cleanup`, { barcode: q, confirm: !!confirm }, auth());
      const d = r.data || {};
      if (confirm) toast.success(`Silinen: ${d.deleted?.length || 0} · hata: ${d.errors?.length || 0}${d.write_enabled === false ? " (yazma kapalı: dry-run)" : ""}`);
      else toast(`Mükerrer SKU: ${d.legacy_found?.length || 0}`);
      setRep((prev) => ({ ...(prev || {}), cleanup: d }));
    } catch (e) { toast.error(e.response?.data?.detail || "Temizlik hatası"); }
    finally { setCleanLoading(false); }
  };
  const runListingReport = async () => {
    const q = (repQ || "").trim();
    if (!q) { toast.error("Barkod, SKU veya ASIN gir"); return; }
    setRepLoading(true); setRep(null);
    try {
      const isAsin = /^B0[A-Z0-9]{8}$/i.test(q);
      const isBarcode = /^\d{8,14}$/.test(q);
      const param = isAsin ? `asin=${encodeURIComponent(q.toUpperCase())}` : (isBarcode ? `barcode=${encodeURIComponent(q)}` : `sku=${encodeURIComponent(q)}`);
      const r = await axios.get(`${API}/amazon/spapi/listing-report?${param}`, auth());
      setRep(r.data);
    } catch (e) {
      toast.error(e.response?.data?.detail || "Teşhis hatası");
    } finally { setRepLoading(false); }
  };
  const runPreview = async () => {
    const q = (prevQ || "").trim();
    if (!q) { toast.error("Ürün adı/barkod gir"); return; }
    setPrevLoading(true);
    setPrev(null);
    try {
      const r = await axios.get(`${API}/amazon/spapi/products/preview?q=${encodeURIComponent(q)}`, auth());
      setPrev(r.data);
      if (r.data?.found === false) toast.error(r.data.error || "Ürün bulunamadı");
    } catch (e) {
      toast.error(e.response?.data?.detail || "Önizleme hatası");
    } finally {
      setPrevLoading(false);
    }
  };

  return (
    <div className={embedded ? "max-w-5xl" : "p-6 max-w-4xl mx-auto"} data-testid="amazon-spapi-page">
      <div className={`flex items-center justify-between ${embedded ? "mb-4" : "mb-5"}`}>
        <div>
          <h1 className={`${embedded ? "text-lg" : "text-2xl"} font-bold flex items-center gap-2`}>
            <ShoppingCart className="text-orange-500" size={24} /> Amazon SP-API Entegrasyonu
          </h1>
          <p className="text-sm text-gray-500 mt-1">
            Amazon Selling Partner API — Türkiye pazaryeri (Marketplace: A33AVAJ2PDY3EV).
            AWS/SigV4 gerekmez, sadece LWA token.
          </p>
        </div>
        {status?.connected ? (
          <span className="inline-flex items-center gap-1 text-green-700 bg-green-50 px-3 py-1.5 rounded-full text-sm" data-testid="amazon-status-connected">
            <CheckCircle size={16} /> Bağlı
          </span>
        ) : (
          <span className="inline-flex items-center gap-1 text-gray-500 bg-gray-100 px-3 py-1.5 rounded-full text-sm" data-testid="amazon-status-disconnected">
            <XCircle size={16} /> Bağlı değil
          </span>
        )}
      </div>

      {/* Rehber */}
      <div className="bg-orange-50 border border-orange-200 rounded-lg p-4 mb-5 text-sm text-gray-700">
        <div className="font-semibold text-orange-900 mb-2 flex items-center gap-1">
          <KeyRound size={14} /> Bağlantı yöntemleri (uygulaman: <b>rooftr</b>)
        </div>
        <p className="font-medium text-orange-900 mb-1">Yöntem 1 — Tek tık OAuth (önerilen):</p>
        <ol className="list-decimal list-inside space-y-1 mb-3">
          <li><b>Developer Central → Develop Apps</b> içinde "rooftr" uygulamanı aç (NOT: "Manage Your Solutions" başka geliştiricilerin uygulamalarını gösterir).</li>
          <li>App ayarlarında <b>App ID (Solution ID, amzn1.sp.solution.xxx)</b>'i kopyala → aşağıdaki "App ID" alanına gir.</li>
          <li>App'in <b>Login with Amazon</b> ayarlarında <b>"Allowed Return URLs"</b> listesine şu adresi ekle:</li>
        </ol>
        <div className="flex items-center gap-2 bg-white border rounded px-2 py-1.5 font-mono text-xs mb-3 break-all">
          <span className="flex-1">{REDIRECT_URI}</span>
          <button onClick={copyRedirect} className="text-orange-700 hover:underline shrink-0">kopyala</button>
        </div>
        <ol className="list-decimal list-inside space-y-1 mb-3" start="4">
          <li>Client ID + Client Secret + App ID'yi aşağıya girip <b>Kaydet</b>.</li>
          <li><b>"Amazon ile Bağlan"</b> butonuna bas → rooftr yetki ekranı açılır → onayla. Refresh token otomatik alınır.</li>
        </ol>
        <p className="font-medium text-orange-900 mb-1">Yöntem 2 — Self-authorization (manuel):</p>
        <ol className="list-decimal list-inside space-y-1">
          <li>Develop Apps → rooftr → <b>Authorize</b> (kendi mağazanı yetkilendir) → çıkan <b>Refresh Token</b>'ı kopyala.</li>
          <li>Aşağıdaki "Refresh Token" alanına yapıştır + Client Secret'ı gir → <b>Kaydet</b> → <b>Bağlantıyı Test Et</b>.</li>
        </ol>
        <a href="https://sellercentral.amazon.com.tr/sellingpartner/developerconsole" target="_blank" rel="noreferrer"
          className="inline-flex items-center gap-1 mt-2 text-orange-800 underline font-medium">
          Developer Central'ı aç <ExternalLink size={12} />
        </a>
      </div>

      {/* Form */}
      <div className="bg-white border rounded-lg p-5 space-y-4">
        <Field label="LWA Client ID *">
          <input className="inp font-mono" value={form.client_id} onChange={(e) => set("client_id", e.target.value)}
            placeholder="amzn1.application-oa2-client.xxxx" data-testid="amazon-client-id" />
        </Field>
        <Field label={`LWA Client Secret ${status?.has_client_secret ? "(kayıtlı — değiştirmek için gir)" : "*"}`}>
          <input type="password" className="inp font-mono" value={form.client_secret} onChange={(e) => set("client_secret", e.target.value)}
            placeholder={status?.has_client_secret ? "•••••• (boş = mevcut korunur)" : "amzn1.oa2-cs.v1.xxxx"} data-testid="amazon-client-secret" />
        </Field>
        <Field label={`Refresh Token ${status?.connected ? "(kayıtlı — değiştirmek için gir)" : "*"}`}>
          <input type="password" className="inp font-mono" value={form.refresh_token} onChange={(e) => set("refresh_token", e.target.value)}
            placeholder={status?.connected ? "•••••• (boş = mevcut korunur)" : "Atza|IwEBI..."} data-testid="amazon-refresh-token" />
        </Field>
        <div className="grid grid-cols-2 gap-3">
          <Field label="Marketplace ID">
            <input className="inp font-mono" value={form.marketplace_id} onChange={(e) => set("marketplace_id", e.target.value)} />
          </Field>
          <Field label="Bölge (Region)">
            <select className="inp" value={form.region} onChange={(e) => set("region", e.target.value)}>
              <option value="eu">EU (Türkiye dahil)</option>
              <option value="na">NA</option>
              <option value="fe">FE</option>
            </select>
          </Field>
          <Field label="Kâr Marjı (%) — fiyat = baz × (1+marj/100)">
            <input type="number" step="0.1" className="inp" value={form.markup}
              onChange={(e) => set("markup", e.target.value)} placeholder="ör. 20" />
          </Field>
        </div>
        <Field label="Merchant Token / Selling Partner ID (ürün listeleme için ŞART)">
          <input className="inp font-mono" value={form.selling_partner_id}
            onChange={(e) => set("selling_partner_id", e.target.value)}
            placeholder="ör. A2XXXXXXXXXXXX — Seller Central → Ayarlar → Hesap Bilgileri → Merchant Token" />
        </Field>
        <Field label="App ID / Solution ID (opsiyonel — OAuth consent linki için)">
          <input className="inp font-mono" value={form.app_id} onChange={(e) => set("app_id", e.target.value)}
            placeholder="amzn1.sp.solution.xxxx" />
        </Field>

        <div className="flex flex-wrap gap-2 pt-2">
          <button onClick={save} disabled={saving} data-testid="amazon-save-btn"
            className="inline-flex items-center gap-2 bg-black text-white px-4 py-2 rounded-lg text-sm hover:bg-gray-800 disabled:opacity-50">
            <Save size={16} /> {saving ? "Kaydediliyor..." : "Kaydet"}
          </button>
          <button onClick={connectOAuth} disabled={!status?.configured || !status?.has_client_secret || !status?.app_id} data-testid="amazon-oauth-btn"
            title={!status?.app_id ? "Önce App ID + Client Secret kaydedin" : ""}
            className="inline-flex items-center gap-2 bg-[#FF9900] text-black font-medium px-4 py-2 rounded-lg text-sm hover:bg-[#e88b00] disabled:opacity-50">
            <Link2 size={16} /> Amazon ile Bağlan
          </button>
          <button onClick={test} disabled={testing || !status?.connected} data-testid="amazon-test-btn"
            className="inline-flex items-center gap-2 bg-orange-600 text-white px-4 py-2 rounded-lg text-sm hover:bg-orange-700 disabled:opacity-50">
            <Zap size={16} /> {testing ? "Test ediliyor..." : "Bağlantıyı Test Et"}
          </button>
          <button onClick={loadOrders} disabled={loadingOrders || !status?.connected} data-testid="amazon-orders-btn"
            className="inline-flex items-center gap-2 border px-4 py-2 rounded-lg text-sm hover:bg-gray-50 disabled:opacity-50">
            <Package size={16} /> {loadingOrders ? "Çekiliyor..." : "Son Siparişler (30g)"}
          </button>
          <button onClick={pullOrders} disabled={pulling || !status?.connected} data-testid="amazon-orders-pull-btn"
            title="Amazon siparişlerini panele (Siparişler) indirir. Otomatik senkron her 10 dk zaten çalışır."
            className="inline-flex items-center gap-2 bg-emerald-600 text-white px-4 py-2 rounded-lg text-sm hover:bg-emerald-700 disabled:opacity-50">
            <Package size={16} /> {pulling ? "Panele çekiliyor..." : "Siparişleri Panele Çek"}
          </button>
          <button onClick={loadStatus} className="inline-flex items-center gap-2 border px-3 py-2 rounded-lg text-sm hover:bg-gray-50">
            <RefreshCw size={14} />
          </button>
        </div>
      </div>

      {/* Sipariş Teşhis */}
      <div className="bg-white border rounded-lg p-4 mt-4" data-testid="amazon-diagnose">
        <h3 className="font-semibold text-sm mb-2">🔍 Sipariş Teşhis (SKU / isim / eşleşme)</h3>
        <div className="flex flex-wrap items-center gap-2">
          <input value={diagNo} onChange={(e) => setDiagNo(e.target.value)}
            placeholder="Amazon sipariş no (ör. 405-8332084-6545141)"
            className="border rounded-lg px-3 py-2 text-sm w-80 max-w-full" />
          <button onClick={runDiagnose} disabled={diagLoading || !status?.connected}
            className="inline-flex items-center gap-2 bg-indigo-600 text-white px-4 py-2 rounded-lg text-sm hover:bg-indigo-700 disabled:opacity-50">
            {diagLoading ? "İnceleniyor..." : "İncele"}
          </button>
        </div>
        {diag && (
          <pre className="mt-3 text-[11px] bg-gray-900 text-green-200 p-3 rounded-lg overflow-auto max-h-[420px]">
{JSON.stringify(diag, null, 2)}
          </pre>
        )}
        <p className="text-[11px] text-gray-400 mt-2">
          match_report'ta <b>matched:false</b> ise Amazon SellerSKU'n Meluni stok kodu/barkoduyla eşleşmiyor →
          bu yüzden resim/stok gelmiyor. Sonucu bana gönder, eşlemeyi ona göre kurayım.
        </p>
      </div>

      <AmazonAutoSetup connected={!!status?.connected} />

      {/* Katalog Eşleştirme & Rapor */}
      <div className="bg-white border rounded-lg p-4 mt-4" data-testid="amazon-catalog-match">
        <h3 className="font-semibold text-sm mb-1">🗂️ Amazon Katalog Eşleştirme & Rapor</h3>
        <p className="text-[11px] text-gray-500 mb-2">
          Amazon'da daha önce (bu platform dışında) açılmış tüm listelemeler çekilir, EAN/barkod ve SKU ile bizim ürünlerimize
          eşlenir; eşleşenlere stok + marjlı fiyat gönderilir (otomatik senkrona da dahil olur). Rapor: hangi ürünümüz Amazon'da var, hangisi yok, hangi bedenler eksik.
        </p>
        <div className="flex flex-wrap items-center gap-2">
          <button onClick={startCatSync} disabled={catBusy || !status?.connected || cat?.status === "running"}
            className="inline-flex items-center gap-2 bg-slate-800 text-white px-4 py-2 rounded-lg text-sm hover:bg-slate-900 disabled:opacity-50">
            {cat?.status === "running" ? "Çekiliyor…" : "1) Amazon kataloğunu çek & eşleştir"}
          </button>
          <button onClick={loadCatReport} disabled={catBusy || !cat?.map_matched && !cat?.map_unmatched}
            className="inline-flex items-center gap-2 border px-4 py-2 rounded-lg text-sm hover:bg-gray-50 disabled:opacity-50">2) Raporu göster</button>
          <button onClick={downloadCatXlsx} disabled={!cat?.map_matched && !cat?.map_unmatched}
            className="inline-flex items-center gap-2 border px-4 py-2 rounded-lg text-sm hover:bg-gray-50 disabled:opacity-50">Excel indir</button>
          <button onClick={pushMapped} disabled={catBusy || !cat?.map_matched}
            className="inline-flex items-center gap-2 bg-emerald-600 text-white px-4 py-2 rounded-lg text-sm hover:bg-emerald-700 disabled:opacity-50">3) Eşleşenlere stok/fiyat gönder</button>
          <button onClick={pushMissing} disabled={catBusy || !status?.connected || !cat?.map_matched}
            data-testid="amazon-push-missing"
            className="inline-flex items-center gap-2 bg-orange-600 text-white px-4 py-2 rounded-lg text-sm hover:bg-orange-700 disabled:opacity-50">4) Eksikleri Amazon'a gönder</button>
        </div>
        {cat && (
          <div className="mt-2 text-[11px] text-gray-600 flex flex-wrap gap-x-4 gap-y-1">
            <span>Durum: <b className={cat.status === "ok" ? "text-emerald-700" : cat.status === "error" ? "text-red-600" : "text-amber-700"}>{cat.status}</b>{cat.step ? ` · ${cat.step}` : ""}</span>
            {cat.finished_at && <span>son çekim {new Date(cat.finished_at).toLocaleString("tr-TR")}</span>}
            {cat.rows != null && <span>Amazon satırı {cat.rows}</span>}
            <span>eşleşen SKU {cat.map_matched ?? 0} · eşleşmeyen {cat.map_unmatched ?? 0}</span>
            {cat.error && <span className="text-red-600">hata: {cat.error}</span>}
          </div>
        )}
        {catRep && (
          <div className="mt-3 text-xs space-y-3">
            <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
              {[["Aktif ürünümüz", catRep.summary.our_active_products], ["Amazon'da tam", catRep.summary.on_amazon_full],
                ["Kısmen (beden eksik)", catRep.summary.on_amazon_partial], ["Amazon'da YOK", catRep.summary.not_on_amazon],
                ["Amazon listeleme", catRep.summary.amazon_rows], ["Eşleşen SKU", catRep.summary.amazon_matched_skus],
                ["Eşleşmeyen SKU", catRep.summary.amazon_unmatched_skus]].map(([l, v]) => (
                <div key={l} className="border rounded p-2"><div className="text-gray-500">{l}</div><div className="text-lg font-bold">{v}</div></div>
              ))}
            </div>
            {[["Amazon'da OLMAYAN ürünler", catRep.not_on_amazon, "text-red-700"], ["Kısmen olan (eksik bedenler)", catRep.partial, "text-amber-700"]].map(([title, rows, cls]) => (
              <div key={title} className="border rounded p-2 max-h-56 overflow-auto">
                <div className={`font-semibold mb-1 ${cls}`}>{title} ({rows.length})</div>
                {rows.length === 0 ? <div className="text-gray-400">Yok</div> : rows.slice(0, 300).map((r, i) => (
                  <div key={i} className="flex gap-2 border-b last:border-0 py-0.5"><span className="font-mono text-gray-500 w-32 shrink-0">{r.stock_code}</span><span className="flex-1">{r.product}</span>{r.missing_sizes?.length ? <span className="text-gray-500">eksik: {r.missing_sizes.join(", ")}</span> : null}</div>
                ))}
              </div>
            ))}
            <div className="border rounded p-2 max-h-56 overflow-auto">
              <div className="font-semibold mb-1 text-gray-700">Amazon'da olup bizde karşılığı bulunamayan SKU'lar ({catRep.amazon_unmatched.length})</div>
              {catRep.amazon_unmatched.slice(0, 300).map((u, i) => (
                <div key={i} className="flex gap-2 border-b last:border-0 py-0.5"><span className="font-mono w-44 shrink-0 truncate">{u.sku}</span><span className="font-mono text-gray-500 w-24 shrink-0">{u.asin}</span><span className="flex-1 truncate">{u.item_name}</span><span className="text-gray-500">EAN {u.amazon_product_id || "-"}</span></div>
              ))}
            </div>
          </div>
        )}
      </div>

      {/* Listeleme Teşhisi — Amazon'daki canlı durum */}
      <div className="bg-white border rounded-lg p-4 mt-4" data-testid="amazon-listing-report">
        <h3 className="font-semibold text-sm mb-1">🩺 Listeleme Teşhisi (Amazon'da ne var, ne düşmüş?)</h3>
        <p className="text-[11px] text-gray-500 mb-2">
          Barkod (ürünün tüm bedenleri/renkleri), SellerSKU ya da ASIN (ör. B0HGWPMF29) gir → Amazon'un her SKU için döndürdüğü
          durum, ASIN, <b>hata/uyarılar</b>, gönderilen görsel sayısı, beden/renk/varyasyon alanları ve görsellerin Amazon botu
          tarafından çekilebilirliği (Cloudflare 403) gösterilir. Salt-okunur.
        </p>
        <div className="flex flex-wrap items-center gap-2">
          <input value={repQ} onChange={(e) => setRepQ(e.target.value)} placeholder="barkod / SKU / ASIN"
            className="border rounded-lg px-3 py-2 text-sm w-72 max-w-full" />
          <button onClick={runListingReport} disabled={repLoading || !status?.connected}
            className="inline-flex items-center gap-2 bg-rose-600 text-white px-4 py-2 rounded-lg text-sm hover:bg-rose-700 disabled:opacity-50">
            {repLoading ? "Sorgulanıyor..." : "Teşhis Et"}
          </button>
        </div>
        {rep && (
          <div className="mt-3 text-xs space-y-2">
            {rep.summary && (
              <div className="flex flex-wrap gap-x-4 gap-y-1">
                <span><b>SKU:</b> {rep.summary.skus_checked}</span>
                <span><b>Amazon'da bulunan:</b> {rep.summary.found_on_amazon}</span>
                <span><b>Hatalı:</b> {rep.summary.with_issues}</span>
                <span><b>Görselli:</b> {rep.summary.with_images}</span>
              </div>
            )}
            {(rep.notes || []).map((n, i) => <div key={i} className="text-amber-800 bg-amber-50 border border-amber-200 rounded px-2 py-1">{n}</div>)}
            {(rep.listings || []).some((l) => l.role === "legacy-duplicate?" && l.found !== false) && (
              <div className="text-red-800 bg-red-50 border border-red-200 rounded px-2 py-2 flex flex-wrap items-center gap-2">
                <span><b>Mükerrer eski SKU'lar bulundu</b> (renk parçasız "stokkodu-beden"): Amazon bedenleri ana ürüne bağlayamıyor (WARNING 8801). Bunlar silinmeli.</span>
                <button onClick={() => runCleanup(false)} disabled={cleanLoading} className="border border-red-300 rounded px-2 py-1 text-[11px] hover:bg-red-100 disabled:opacity-50">Önizle</button>
                <button onClick={() => runCleanup(true)} disabled={cleanLoading} className="bg-red-600 text-white rounded px-2 py-1 text-[11px] hover:bg-red-700 disabled:opacity-50">{cleanLoading ? "..." : "Eski SKU'ları Amazon'dan sil"}</button>
              </div>
            )}
            {rep.cleanup && (
              <pre className="text-[10.5px] bg-gray-900 text-green-200 p-2 rounded overflow-auto max-h-48">{JSON.stringify(rep.cleanup, null, 2)}</pre>
            )}
            {(rep.image_fetch_check || []).length > 0 && (
              <div className="border rounded p-2">
                <div className="font-semibold mb-1">Görsel erişim testi</div>
                {rep.image_fetch_check.map((r, i) => (
                  <div key={i} className="font-mono text-[10.5px] break-all">
                    {r.url} → Amazonbot: <b className={r.amazonbot?.status === 200 ? "text-green-700" : "text-red-600"}>{r.amazonbot?.status || r.amazonbot?.error}</b> · genel: {r.generic?.status || r.generic?.error}
                  </div>
                ))}
              </div>
            )}
            <div className="overflow-x-auto">
              <table className="w-full text-[11px]">
                <thead><tr className="text-left text-gray-500 border-b">
                  <th className="py-1 pr-2">SKU</th><th className="py-1 pr-2">Rol</th><th className="py-1 pr-2">Amazon</th><th className="py-1 pr-2">ASIN</th>
                  <th className="py-1 pr-2">Varyasyon</th><th className="py-1 pr-2">Beden / Renk</th><th className="py-1 pr-2">Görsel</th><th className="py-1">Hata / Uyarı</th>
                </tr></thead>
                <tbody>
                  {(rep.listings || []).map((l, i) => (
                    <tr key={i} className={`border-b align-top ${l.found === false ? "bg-gray-50 text-gray-400" : (l.role === "legacy-duplicate?" ? "bg-red-100" : (l.issues?.length ? "bg-red-50/60" : ""))}`}>
                      <td className="py-1 pr-2 font-mono">{l.sku}</td>
                      <td className="py-1 pr-2">{l.role || "-"}</td>
                      <td className="py-1 pr-2">{l.found === false ? `yok (HTTP ${l.http})` : (l.status?.join ? l.status.join(", ") : String(l.status || ""))}</td>
                      <td className="py-1 pr-2 font-mono">{l.asin || "-"}</td>
                      <td className="py-1 pr-2">{l.parentage || "-"}{l.variation_theme ? ` · ${l.variation_theme}` : ""}{l.parent_sku ? ` · üst: ${l.parent_sku}` : ""}</td>
                      <td className="py-1 pr-2">{l.size || "-"} / {l.color || "-"}</td>
                      <td className="py-1 pr-2">{l.image_count ?? "-"}</td>
                      <td className="py-1">{(l.issues || []).map((it, j) => <div key={j}><b>{it.severity}</b> {it.code}: {it.message}{it.attributes?.length ? ` [${it.attributes.join(", ")}]` : ""}</div>)}{l.error ? <span className="text-gray-500">{l.error}</span> : null}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </div>

      {/* Ürün Aktarım Önizleme (dry-run) */}
      <div className="bg-white border rounded-lg p-4 mt-4" data-testid="amazon-product-preview">
        <h3 className="font-semibold text-sm mb-1">🧪 Ürün Aktarım Önizleme (Amazon'a ne gidecek?)</h3>
        <p className="text-[11px] text-gray-500 mb-2">
          Ürün adı/barkod gir → Amazon'a gidecek <b>tam payload</b> (marjlı fiyat, list_price, görseller,
          tüm attribute'lar + eksik zorunlu alanlar) canlıya <b>yazılmadan</b> gösterilir.
        </p>
        <div className="flex flex-wrap items-center gap-2">
          <input value={prevQ} onChange={(e) => setPrevQ(e.target.value)}
            placeholder="ör. yüksek bel kapri pantolon acı kahve"
            className="border rounded-lg px-3 py-2 text-sm w-96 max-w-full" />
          <button onClick={runPreview} disabled={prevLoading || !status?.connected}
            className="inline-flex items-center gap-2 bg-purple-600 text-white px-4 py-2 rounded-lg text-sm hover:bg-purple-700 disabled:opacity-50">
            {prevLoading ? "Hazırlanıyor..." : "Önizle"}
          </button>
        </div>
        {prev && prev.found !== false && (
          <div className="mt-3 text-xs">
            <div className="flex flex-wrap gap-x-4 gap-y-1 mb-2">
              <span><b>Ürün:</b> {prev.product}</span>
              <span><b>productType:</b> {prev.product_type || <span className="text-red-600">YOK</span>}</span>
              <span><b>Marj:</b> %{prev.markup_pct}</span>
              <span><b>Satış:</b> {prev.our_price} TL</span>
              {prev.list_price > prev.our_price && <span><b>Liste:</b> {prev.list_price} TL</span>}
              <span><b>Görsel:</b> {prev.image_count}</span>
              <span><b>Varyant:</b> {prev.variant_count}</span>
            </div>
            {prev.product_type_warning && <div className="text-red-600 mb-1">⚠️ {prev.product_type_warning}</div>}
            {prev.missing_required?.length > 0 && (
              <div className="text-amber-700 mb-1">
                ⚠️ Eksik zorunlu alan: {prev.missing_required.join(", ")} — kategori Özellik/Değer ekranından doldur.
              </div>
            )}
            {prev.missing_required?.length === 0 && prev.product_type && (
              <div className="text-green-700 mb-1">✓ Zorunlu alanlar tamam — aktarıma hazır.</div>
            )}
            <pre className="text-[11px] bg-gray-900 text-green-200 p-3 rounded-lg overflow-auto max-h-[360px]">
{JSON.stringify(prev.attributes, null, 2)}
            </pre>
          </div>
        )}
      </div>

      {/* Marketplaces sonucu */}
      {marketplaces && (
        <div className="bg-white border rounded-lg p-4 mt-4" data-testid="amazon-marketplaces">
          <h3 className="font-semibold text-sm mb-2 text-green-700 flex items-center gap-1"><CheckCircle size={14} /> Yetkili Pazaryerleri</h3>
          <div className="flex flex-wrap gap-2">
            {marketplaces.map((m) => (
              <span key={m.id} className="text-xs bg-gray-50 border px-2 py-1 rounded">
                {m.name} ({m.country}) · {m.currency}
              </span>
            ))}
          </div>
        </div>
      )}

      {/* Orders sonucu */}
      {orders && (
        <div className="bg-white border rounded-lg p-4 mt-4" data-testid="amazon-orders-result">
          <h3 className="font-semibold text-sm mb-2">Son Siparişler ({orders.length})</h3>
          {orders.length === 0 ? (
            <p className="text-xs text-gray-400">Son 30 günde sipariş yok.</p>
          ) : (
            <div className="space-y-1 text-xs">
              {orders.map((o) => (
                <div key={o.amazon_order_id} className="flex justify-between border-b py-1">
                  <span className="font-mono">{o.amazon_order_id}</span>
                  <span>{o.status}</span>
                  <span>{o.total ? `${o.total} ${o.currency}` : "—"}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function Field({ label, children }) {
  return (
    <div>
      <label className="block text-xs text-gray-600 mb-1">{label}</label>
      {children}
    </div>
  );
}
