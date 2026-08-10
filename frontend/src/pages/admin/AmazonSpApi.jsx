/**
 * Amazon SP-API Entegrasyon ayar sayfası.
 * SigV4/AWS GEREKMEZ — sadece LWA (client_id + client_secret + refresh_token).
 */
import { useState, useEffect, useCallback } from "react";
import axios from "axios";
import { toast } from "sonner";
import {
  ShoppingCart, CheckCircle, XCircle, RefreshCw, Save, Zap, Package, ExternalLink, KeyRound, Link2,
} from "lucide-react";

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;
const REDIRECT_URI = `${process.env.REACT_APP_BACKEND_URL}/api/amazon/spapi/oauth/callback`;
const auth = () => ({ headers: { Authorization: `Bearer ${localStorage.getItem("token")}` } });

export default function AmazonSpApi() {
  const [status, setStatus] = useState(null);
  const [form, setForm] = useState({
    client_id: "", client_secret: "", refresh_token: "", app_id: "",
    marketplace_id: "A33AVAJ2PDY3EV", region: "eu",
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
    if (st) window.history.replaceState({}, "", "/admin/amazon");
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

  return (
    <div className="p-6 max-w-4xl mx-auto" data-testid="amazon-spapi-page">
      <div className="flex items-center justify-between mb-5">
        <div>
          <h1 className="text-2xl font-bold flex items-center gap-2">
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
        </div>
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
          <button onClick={loadStatus} className="inline-flex items-center gap-2 border px-3 py-2 rounded-lg text-sm hover:bg-gray-50">
            <RefreshCw size={14} />
          </button>
        </div>
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
