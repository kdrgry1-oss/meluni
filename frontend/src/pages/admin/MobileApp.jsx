/**
 * MobileApp.jsx — Mobil Uygulama Yönetimi (Admin)
 * - App version (ios/android) güncelle
 * - Feature flags & branding
 * - Cihaz listesi
 * - Push notification gönder
 */
import { useEffect, useState } from "react";
import axios from "axios";
import { toast } from "sonner";
import {
  Smartphone, Send, Settings, Apple, RefreshCw, Bell, Users
} from "lucide-react";

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;

const auth = () => ({ headers: { Authorization: `Bearer ${localStorage.getItem("token")}` } });

const Tab = ({ id, label, icon: Icon, activeId, onClick }) => (
  <button
    data-testid={`mobile-tab-${id}`}
    onClick={() => onClick(id)}
    className={`flex items-center gap-2 px-4 py-2 text-sm border-b-2 transition-colors ${
      activeId === id ? "border-black text-black font-semibold" : "border-transparent text-gray-500 hover:text-black"
    }`}
  >
    <Icon className="w-4 h-4" /> {label}
  </button>
);

export default function MobileApp() {
  const [tab, setTab] = useState("versions");
  const [loading, setLoading] = useState(false);

  // Versions
  const [versions, setVersions] = useState({
    ios: { min_version: "1.0.0", latest_version: "1.0.0", force_update: false, store_url: "", release_notes: "" },
    android: { min_version: "1.0.0", latest_version: "1.0.0", force_update: false, store_url: "", release_notes: "" },
  });

  // Config
  const [config, setConfig] = useState({ feature_flags: {}, branding: {}, support: {} });

  // Devices
  const [devices, setDevices] = useState({ items: [], total: 0, by_platform: {} });
  const [platformFilter, setPlatformFilter] = useState("");

  // Push
  const [push, setPush] = useState({ target: "all", target_value: "", title: "", body: "", data_json: "{}", image_url: "" });
  const [pushResult, setPushResult] = useState(null);

  const loadAll = async () => {
    setLoading(true);
    try {
      const [v, c, d] = await Promise.all([
        axios.get(`${API}/admin/mobile/versions`, auth()),
        axios.get(`${API}/admin/mobile/config`, auth()),
        axios.get(`${API}/admin/mobile/devices?limit=100${platformFilter ? `&platform=${platformFilter}` : ""}`, auth()),
      ]);
      if (v.data?.versions) setVersions(v.data.versions);
      if (c.data) setConfig({
        feature_flags: c.data.feature_flags || {},
        branding: c.data.branding || {},
        support: c.data.support || {},
      });
      setDevices(d.data || { items: [], total: 0, by_platform: {} });
    } catch (e) {
      toast.error("Yüklenemedi: " + (e.response?.data?.detail || e.message));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { loadAll(); /* eslint-disable-next-line */ }, [platformFilter]);

  const saveVersions = async () => {
    try {
      await axios.post(`${API}/admin/mobile/versions`, { versions }, auth());
      toast.success("Versiyon ayarları kaydedildi");
    } catch (e) {
      toast.error("Hata: " + (e.response?.data?.detail || e.message));
    }
  };

  const saveConfig = async () => {
    try {
      await axios.post(`${API}/admin/mobile/config`, config, auth());
      toast.success("Yapılandırma kaydedildi");
    } catch (e) {
      toast.error("Hata: " + (e.response?.data?.detail || e.message));
    }
  };

  const sendPush = async () => {
    if (!push.title.trim() || !push.body.trim()) {
      toast.error("Başlık ve içerik zorunlu"); return;
    }
    let dataParsed = {};
    try { dataParsed = push.data_json ? JSON.parse(push.data_json) : {}; }
    catch { toast.error("data alanı geçerli JSON olmalı"); return; }
    try {
      const res = await axios.post(`${API}/admin/mobile/push/send`,
        { ...push, data: dataParsed }, auth());
      setPushResult(res.data);
      if (res.data.success) {
        toast.success(res.data.message || `${res.data.sent || res.data.queued} bildirim gönderildi`);
      } else {
        toast.error(res.data.message || "Gönderim başarısız");
      }
    } catch (e) {
      toast.error("Hata: " + (e.response?.data?.detail || e.message));
    }
  };

  const PlatformCard = ({ platform, label, icon: Icon }) => {
    const v = versions[platform] || {};
    const update = (k, val) => setVersions({ ...versions, [platform]: { ...v, [k]: val } });
    return (
      <div className="bg-white border border-gray-200 rounded-lg p-5 space-y-3">
        <div className="flex items-center gap-2 mb-2">
          <Icon className="w-5 h-5" />
          <h3 className="font-semibold">{label}</h3>
        </div>
        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className="text-xs text-gray-600">Min Version</label>
            <input data-testid={`min-${platform}`} value={v.min_version || ""}
                   onChange={(e) => update("min_version", e.target.value)}
                   className="w-full border border-gray-300 rounded px-2 py-1.5 text-sm font-mono" />
          </div>
          <div>
            <label className="text-xs text-gray-600">Latest Version</label>
            <input data-testid={`latest-${platform}`} value={v.latest_version || ""}
                   onChange={(e) => update("latest_version", e.target.value)}
                   className="w-full border border-gray-300 rounded px-2 py-1.5 text-sm font-mono" />
          </div>
        </div>
        <div>
          <label className="text-xs text-gray-600">Store URL</label>
          <input data-testid={`store-${platform}`} value={v.store_url || ""}
                 onChange={(e) => update("store_url", e.target.value)}
                 className="w-full border border-gray-300 rounded px-2 py-1.5 text-sm" />
        </div>
        <div>
          <label className="text-xs text-gray-600">Release Notes</label>
          <textarea value={v.release_notes || ""}
                    onChange={(e) => update("release_notes", e.target.value)}
                    rows={2}
                    className="w-full border border-gray-300 rounded px-2 py-1.5 text-sm" />
        </div>
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" checked={!!v.force_update}
                 onChange={(e) => update("force_update", e.target.checked)} />
          Zorunlu Güncelleme (force update)
        </label>
      </div>
    );
  };

  return (
    <div data-testid="admin-mobile-app" className="space-y-6 p-6 max-w-[1400px] mx-auto">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="bg-black p-2.5 rounded-lg">
            <Smartphone className="w-5 h-5 text-white" />
          </div>
          <div>
            <h1 className="text-2xl font-light tracking-tight">Mobil Uygulama</h1>
            <p className="text-sm text-gray-500">iOS & Android • Versiyon, Feature Flags, Push Bildirim</p>
          </div>
        </div>
        <button onClick={loadAll} disabled={loading}
                className="flex items-center gap-2 px-4 py-1.5 border border-gray-300 rounded text-sm hover:bg-gray-50">
          <RefreshCw className={`w-4 h-4 ${loading ? "animate-spin" : ""}`} /> Yenile
        </button>
      </div>

      <div className="border-b border-gray-200 flex gap-4">
        <Tab id="versions" label="Versiyonlar" icon={Apple} activeId={tab} onClick={setTab} />
        <Tab id="config" label="Yapılandırma" icon={Settings} activeId={tab} onClick={setTab} />
        <Tab id="devices" label={`Cihazlar (${devices.total})`} icon={Users} activeId={tab} onClick={setTab} />
        <Tab id="push" label="Push Bildirim" icon={Bell} activeId={tab} onClick={setTab} />
      </div>

      {tab === "versions" && (
        <>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <PlatformCard platform="ios" label="iOS" icon={Apple} />
            <PlatformCard platform="android" label="Android" icon={Smartphone} />
          </div>
          <div className="text-right">
            <button data-testid="save-versions-btn" onClick={saveVersions}
                    className="px-5 py-2 bg-black text-white rounded text-sm font-medium hover:bg-gray-800">
              Versiyonları Kaydet
            </button>
          </div>
        </>
      )}

      {tab === "config" && (
        <>
          <div className="bg-white border border-gray-200 rounded-lg p-5 space-y-4">
            <h3 className="font-semibold">Feature Flags</h3>
            {[
              ["live_support", "Canlı Destek"],
              ["social_login_apple", "Apple ile Giriş"],
              ["social_login_google", "Google ile Giriş"],
              ["social_login_facebook", "Facebook ile Giriş"],
              ["biometric_login", "Biyometrik Giriş (FaceID/TouchID)"],
              ["instagram_shop", "Instagram Vitrini"],
              ["live_stream_shop", "Canlı Yayın Alışverişi"],
            ].map(([k, lbl]) => (
              <label key={k} className="flex items-center justify-between border-b border-gray-100 pb-2 text-sm">
                <span>{lbl}</span>
                <input type="checkbox"
                       data-testid={`flag-${k}`}
                       checked={!!config.feature_flags[k]}
                       onChange={(e) => setConfig({ ...config, feature_flags: { ...config.feature_flags, [k]: e.target.checked } })} />
              </label>
            ))}
          </div>

          <div className="bg-white border border-gray-200 rounded-lg p-5 space-y-3">
            <h3 className="font-semibold">Destek Kanalları</h3>
            <div className="grid grid-cols-3 gap-3">
              <div>
                <label className="text-xs text-gray-600">WhatsApp</label>
                <input value={config.support.whatsapp || ""}
                       onChange={(e) => setConfig({ ...config, support: { ...config.support, whatsapp: e.target.value } })}
                       placeholder="+90555..."
                       className="w-full border border-gray-300 rounded px-2 py-1.5 text-sm" />
              </div>
              <div>
                <label className="text-xs text-gray-600">Telefon</label>
                <input value={config.support.phone || ""}
                       onChange={(e) => setConfig({ ...config, support: { ...config.support, phone: e.target.value } })}
                       className="w-full border border-gray-300 rounded px-2 py-1.5 text-sm" />
              </div>
              <div>
                <label className="text-xs text-gray-600">E-posta</label>
                <input value={config.support.email || ""}
                       onChange={(e) => setConfig({ ...config, support: { ...config.support, email: e.target.value } })}
                       className="w-full border border-gray-300 rounded px-2 py-1.5 text-sm" />
              </div>
            </div>
          </div>

          <div className="text-right">
            <button data-testid="save-config-btn" onClick={saveConfig}
                    className="px-5 py-2 bg-black text-white rounded text-sm font-medium hover:bg-gray-800">
              Yapılandırmayı Kaydet
            </button>
          </div>
        </>
      )}

      {tab === "devices" && (
        <>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            <div className="bg-white border border-gray-200 rounded-lg p-4">
              <div className="text-xs text-gray-500">Toplam Aktif Cihaz</div>
              <div className="text-3xl font-light mt-1">{devices.total || 0}</div>
            </div>
            <div className="bg-white border border-gray-200 rounded-lg p-4">
              <div className="text-xs text-gray-500">iOS</div>
              <div className="text-3xl font-light mt-1">{devices.by_platform?.ios || 0}</div>
            </div>
            <div className="bg-white border border-gray-200 rounded-lg p-4">
              <div className="text-xs text-gray-500">Android</div>
              <div className="text-3xl font-light mt-1">{devices.by_platform?.android || 0}</div>
            </div>
            <div className="bg-white border border-gray-200 rounded-lg p-4">
              <div className="text-xs text-gray-500">Web</div>
              <div className="text-3xl font-light mt-1">{devices.by_platform?.web || 0}</div>
            </div>
          </div>

          <div className="flex gap-2 items-center">
            <select value={platformFilter} onChange={(e) => setPlatformFilter(e.target.value)}
                    data-testid="device-platform-filter"
                    className="border border-gray-300 rounded px-3 py-1.5 text-sm">
              <option value="">Tüm platformlar</option>
              <option value="ios">iOS</option>
              <option value="android">Android</option>
              <option value="web">Web</option>
            </select>
          </div>

          <div className="bg-white border border-gray-200 rounded-lg overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-gray-50 border-b border-gray-200">
                <tr>
                  <th className="text-left px-3 py-2 text-xs font-medium">Platform</th>
                  <th className="text-left px-3 py-2 text-xs font-medium">Model</th>
                  <th className="text-left px-3 py-2 text-xs font-medium">App / OS</th>
                  <th className="text-left px-3 py-2 text-xs font-medium">User ID</th>
                  <th className="text-left px-3 py-2 text-xs font-medium">Son Görülme</th>
                </tr>
              </thead>
              <tbody>
                {devices.items.length === 0 ? (
                  <tr><td colSpan={5} className="text-center text-gray-400 py-8">Henüz cihaz kaydı yok</td></tr>
                ) : devices.items.map((d, i) => (
                  <tr key={i} className="border-b border-gray-100 hover:bg-gray-50">
                    <td className="px-3 py-1.5">
                      <span className="px-2 py-0.5 rounded text-xs bg-gray-100">{d.platform}</span>
                    </td>
                    <td className="px-3 py-1.5">{d.model || "—"}</td>
                    <td className="px-3 py-1.5 font-mono text-xs">{d.app_version} / {d.os_version}</td>
                    <td className="px-3 py-1.5 font-mono text-xs">{(d.user_id || "").slice(0,8)}...</td>
                    <td className="px-3 py-1.5 font-mono text-xs">{d.last_seen_at?.slice(0,16).replace("T"," ")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}

      {tab === "push" && (
        <>
          <div className="bg-white border border-gray-200 rounded-lg p-5 space-y-4 max-w-3xl">
            <h3 className="font-semibold flex items-center gap-2"><Bell className="w-4 h-4"/> Yeni Bildirim</h3>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="text-xs text-gray-600">Hedef</label>
                <select value={push.target} onChange={(e) => setPush({ ...push, target: e.target.value })}
                        data-testid="push-target"
                        className="w-full border border-gray-300 rounded px-2 py-1.5 text-sm">
                  <option value="all">Tüm aktif cihazlar</option>
                  <option value="platform">Platform (ios/android)</option>
                  <option value="user">Belirli kullanıcı (user_id)</option>
                  <option value="device">Belirli cihaz (device_id)</option>
                </select>
              </div>
              {push.target !== "all" && (
                <div>
                  <label className="text-xs text-gray-600">Hedef Değer</label>
                  <input value={push.target_value} onChange={(e) => setPush({ ...push, target_value: e.target.value })}
                         data-testid="push-target-value"
                         placeholder={push.target === "platform" ? "ios veya android" : "ID"}
                         className="w-full border border-gray-300 rounded px-2 py-1.5 text-sm" />
                </div>
              )}
            </div>
            <div>
              <label className="text-xs text-gray-600">Başlık <span className="text-red-500">*</span></label>
              <input value={push.title} onChange={(e) => setPush({ ...push, title: e.target.value })}
                     data-testid="push-title"
                     maxLength={50}
                     className="w-full border border-gray-300 rounded px-2 py-1.5 text-sm" />
            </div>
            <div>
              <label className="text-xs text-gray-600">İçerik <span className="text-red-500">*</span></label>
              <textarea value={push.body} onChange={(e) => setPush({ ...push, body: e.target.value })}
                        data-testid="push-body"
                        maxLength={150} rows={3}
                        className="w-full border border-gray-300 rounded px-2 py-1.5 text-sm" />
            </div>
            <div>
              <label className="text-xs text-gray-600">Görsel URL (opsiyonel)</label>
              <input value={push.image_url} onChange={(e) => setPush({ ...push, image_url: e.target.value })}
                     className="w-full border border-gray-300 rounded px-2 py-1.5 text-sm" />
            </div>
            <div>
              <label className="text-xs text-gray-600">Data (JSON, opsiyonel) — örn: {`{"deep_link":"facette://order/123"}`}</label>
              <textarea value={push.data_json} onChange={(e) => setPush({ ...push, data_json: e.target.value })}
                        rows={2}
                        className="w-full border border-gray-300 rounded px-2 py-1.5 text-xs font-mono" />
            </div>
            <div className="flex justify-end">
              <button data-testid="send-push-btn" onClick={sendPush}
                      className="px-5 py-2 bg-black text-white rounded text-sm font-medium hover:bg-gray-800 flex items-center gap-2">
                <Send className="w-4 h-4" /> Gönder
              </button>
            </div>
            {pushResult && (
              <div className={`text-sm p-3 rounded border ${pushResult.success ? "bg-green-50 border-green-200" : "bg-red-50 border-red-200"}`}>
                <div>Başarılı: {pushResult.sent || 0} · Başarısız: {pushResult.failed || 0} · Kuyrukta: {pushResult.queued || 0}</div>
                {pushResult.message && <div className="text-xs mt-1">{pushResult.message}</div>}
              </div>
            )}
          </div>
        </>
      )}
    </div>
  );
}
