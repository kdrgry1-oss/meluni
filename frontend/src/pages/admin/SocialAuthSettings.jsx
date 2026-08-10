/**
 * SocialAuthSettings.jsx — Apple + Facebook credential yönetimi
 *   GET/POST /api/auth/social/settings (admin)
 */
import { useEffect, useState } from "react";
import axios from "axios";
import { toast } from "sonner";
import { Save, Lock } from "lucide-react";

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;

export default function SocialAuthSettings() {
  const [cfg, setCfg] = useState({
    apple_enabled: false, apple_client_id: "", apple_team_id: "", apple_key_id: "", apple_private_key: "",
    facebook_enabled: false, facebook_app_id: "", facebook_app_secret: "", facebook_redirect_uri: "",
  });
  const [saving, setSaving] = useState(false);
  const token = localStorage.getItem("token");
  const auth = { headers: { Authorization: `Bearer ${token}` } };

  useEffect(() => {
    axios.get(`${API}/auth/social/settings`, auth).then((r) => setCfg({ ...cfg, ...(r.data || {}) }))
      .catch(() => {});
    // eslint-disable-next-line
  }, []);

  const save = async () => {
    setSaving(true);
    try {
      await axios.post(`${API}/auth/social/settings`, cfg, auth);
      toast.success("Sosyal giriş ayarları kaydedildi");
    } catch (e) { toast.error("Kaydedilemedi: " + (e?.response?.data?.detail || e.message)); }
    finally { setSaving(false); }
  };

  const f = (key, label, type = "text", long = false) => (
    <div>
      <label className="block text-xs text-gray-600 mb-1">{label}</label>
      {long ? (
        <textarea rows={5} value={cfg[key] || ""} onChange={(e) => setCfg({ ...cfg, [key]: e.target.value })}
          className="w-full border px-3 py-2 text-xs font-mono rounded" data-testid={`sa-${key}`} />
      ) : (
        <input type={type} value={cfg[key] || ""} onChange={(e) => setCfg({ ...cfg, [key]: e.target.value })}
          className="w-full border px-3 py-2 text-sm rounded" data-testid={`sa-${key}`} />
      )}
    </div>
  );

  return (
    <div className="max-w-4xl mx-auto p-6 space-y-6" data-testid="social-auth-settings">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold flex items-center gap-2"><Lock size={20} /> Sosyal Giriş Ayarları</h1>
          <p className="text-sm text-gray-500 mt-1">Apple Sign-In ve Facebook Login için developer credential'larınızı girin.</p>
        </div>
        <button onClick={save} disabled={saving}
          className="inline-flex items-center gap-1 bg-black text-white px-4 py-2 rounded text-sm disabled:opacity-60"
          data-testid="sa-save">
          <Save size={14} /> {saving ? "Kaydediliyor..." : "Kaydet"}
        </button>
      </div>

      {/* Apple */}
      <section className="bg-white rounded-lg border border-gray-200 p-5 space-y-3">
        <div className="flex items-center justify-between">
          <h2 className="font-semibold">Apple Sign-In</h2>
          <label className="flex items-center gap-2 text-sm cursor-pointer">
            <input type="checkbox" checked={cfg.apple_enabled}
              onChange={(e) => setCfg({ ...cfg, apple_enabled: e.target.checked })}
              data-testid="sa-apple-enabled" />
            Aktif
          </label>
        </div>
        <p className="text-xs text-gray-500">
          Gerekli: Apple Developer hesabı → Services ID, Team ID, Key ID, Private Key (.p8).{" "}
          <a href="https://developer.apple.com/sign-in-with-apple/" target="_blank" rel="noreferrer"
             className="text-blue-600 hover:underline">Dokümantasyon</a>
        </p>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
          {f("apple_client_id", "Services ID (Client ID)")}
          {f("apple_team_id", "Team ID")}
          {f("apple_key_id", "Key ID")}
        </div>
        {f("apple_private_key", "Private Key (.p8 içeriği)", "text", true)}
      </section>

      {/* Facebook */}
      <section className="bg-white rounded-lg border border-gray-200 p-5 space-y-3">
        <div className="flex items-center justify-between">
          <h2 className="font-semibold">Facebook Login</h2>
          <label className="flex items-center gap-2 text-sm cursor-pointer">
            <input type="checkbox" checked={cfg.facebook_enabled}
              onChange={(e) => setCfg({ ...cfg, facebook_enabled: e.target.checked })}
              data-testid="sa-fb-enabled" />
            Aktif
          </label>
        </div>
        <p className="text-xs text-gray-500">
          Gerekli: Facebook Developer Console → App ID, App Secret, OAuth Redirect URI.{" "}
          <a href="https://developers.facebook.com/apps/" target="_blank" rel="noreferrer"
             className="text-blue-600 hover:underline">Developers Portal</a>
        </p>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          {f("facebook_app_id", "App ID")}
          {f("facebook_app_secret", "App Secret", "password")}
        </div>
        {f("facebook_redirect_uri", "Redirect URI (ör. https://facette.com/auth/facebook/callback)")}
      </section>
    </div>
  );
}
