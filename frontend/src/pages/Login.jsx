import { useState, useEffect, useRef } from "react";
import { useNavigate, useLocation } from "react-router-dom";
import { toast } from "sonner";
import Header from "../components/Header";
import Footer from "../components/Footer";
import { useAuth } from "../context/AuthContext";
import axios from "axios";
import {
  consumeOAuthState,
  createGooglePopupFlow,
  createOAuthState,
  googleErrorMessage,
  isEmbeddedWebView,
  safeReturnPath,
} from "../lib/googleAuthFlow";

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;
const BUILD_GOOGLE_CLIENT_ID = process.env.REACT_APP_GOOGLE_CLIENT_ID_V2 || process.env.REACT_APP_GOOGLE_CLIENT_ID || "";
const GOOGLE_FLOW_VERSION = "2026-09-08-popup-fallback-1";
const FB_STATE_KEY = "fb_oauth_state";

export default function Login() {
  const navigate = useNavigate();
  const location = useLocation();
  const _redirectTo = safeReturnPath(new URLSearchParams(location.search).get("redirect"));
  const { login, register, user, loginWithToken } = useAuth();
  const [isRegister, setIsRegister] = useState(false);
  const [loading, setLoading] = useState(false);
  // FAZ 3+ — Hangi sosyal sağlayıcılar aktif?
  const [socialProviders, setSocialProviders] = useState({
    apple: false,
    facebook: false,
    google: Boolean(BUILD_GOOGLE_CLIENT_ID),
    google_client_id: BUILD_GOOGLE_CLIENT_ID,
  });
  const [googleStatus, setGoogleStatus] = useState({ phase: "idle", message: "" });
  const embeddedGoogle = isEmbeddedWebView();
  useEffect(() => {
    fetch(`${API}/auth/social/providers`).then((r) => r.ok && r.json())
      .then((d) => d && setSocialProviders(d)).catch(() => {});
  }, []);
  const [formData, setFormData] = useState({
    email: "",
    password: "",
    first_name: "",
    last_name: "",
    phone: "",
  });

  const googleBtnRef = useRef(null);
  const googleFlowRef = useRef(null);

  // Tam-sayfa Google dönüşü: URL'deki kısa ömürlü kodu backend'de bir kez JWT'ye çevir.
  useEffect(() => {
    const q = new URLSearchParams(location.search);
    const code = q.get("google_code");
    const error = q.get("google_error");
    if (!code && !error) return;
    const returnTo = safeReturnPath(q.get("redirect") || (() => {
      try { return sessionStorage.getItem("google_redirect_to"); } catch { return ""; }
    })());
    window.history.replaceState({}, "", `/giris?redirect=${encodeURIComponent(returnTo)}`);
    if (error) {
      const message = googleErrorMessage(error);
      setGoogleStatus({ phase: "error", message });
      toast.error(message);
      return;
    }
    (async () => {
      setLoading(true);
      setGoogleStatus({ phase: "exchanging", message: "Google oturumu doğrulanıyor…" });
      try {
        const res = await axios.post(`${API}/auth/google/exchange`, { code });
        if (res.data?.token) {
          loginWithToken(res.data.token, res.data.user);
          toast.success("Google ile giriş başarılı!");
          try { sessionStorage.removeItem("google_redirect_to"); } catch {}
          navigate(returnTo, { replace: true });
        } else {
          throw new Error("missing app token");
        }
      } catch (err) {
        const message = err.response?.data?.detail || googleErrorMessage("verification");
        setGoogleStatus({ phase: "error", message });
        toast.error(message);
      } finally { setLoading(false); }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // E-posta doğrulama dönüşü: /api/auth/verify-email (onay butonu, POST) → /giris?email_verified=1|0.
  // Doğrulama hesabın açık oturumlarını kapatır (güvenlik) → kullanıcıdan yeniden giriş istenir.
  useEffect(() => {
    const v = new URLSearchParams(location.search).get("email_verified");
    if (v === null) return;
    window.history.replaceState({}, "", "/giris");
    if (v === "1") toast.success("E-posta adresin doğrulandı. Güvenliğin için lütfen yeniden giriş yap.");
    else toast.error("Doğrulama bağlantısı geçersiz ya da süresi dolmuş.");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ---- Facebook ile giriş (sunucu-taraflı OAuth code akışı) ----
  const FB_REDIRECT = typeof window !== "undefined" ? `${window.location.origin}/giris` : "";
  const handleFacebookLogin = () => {
    const appId = socialProviders.facebook_app_id;
    if (!appId) { toast.error("Facebook girişi yapılandırılmamış"); return; }
    try { sessionStorage.setItem("fb_redirect_to", _redirectTo); } catch {}
    // Giriş-CSRF: tahmin edilemez, sekmeye bağlı tek kullanımlık state (dönüşte doğrulanır).
    let fbState = "";
    try { fbState = createOAuthState("facebook", window.sessionStorage, FB_STATE_KEY); } catch { fbState = ""; }
    if (!fbState) { toast.error("Facebook girişi bu tarayıcıda başlatılamadı (depolama kapalı)"); return; }
    const url = `https://www.facebook.com/v20.0/dialog/oauth?client_id=${encodeURIComponent(appId)}`
      + `&redirect_uri=${encodeURIComponent(FB_REDIRECT)}`
      + `&scope=${encodeURIComponent("email,public_profile")}`
      + `&response_type=code&state=${encodeURIComponent(fbState)}`;
    window.location.href = url;
  };
  // FB dönüşü: /giris?code=...&state=facebook → kodu backend'e verip giriş yap.
  useEffect(() => {
    const q = new URLSearchParams(location.search);
    const fbState = q.get("state") || "";
    if (!(fbState === "facebook" || fbState.startsWith("facebook.")) || !q.get("code")) return;
    const code = q.get("code");
    window.history.replaceState({}, "", "/giris"); // kod tekrar kullanılmasın
    // GÜVENLİK (giriş-CSRF): yalnız BU sekmede başlatılmış girişin state'i kabul edilir; dışarıdan
    // gelen (saldırganın kendi kodunu taşıyan) bağlantı kurbanı saldırganın hesabına sokamaz.
    let stateOk = false;
    try { stateOk = consumeOAuthState(fbState, window.sessionStorage, FB_STATE_KEY); } catch { stateOk = false; }
    if (!stateOk) {
      toast.error("Facebook giriş doğrulaması güvenlik kontrolünden geçemedi. Lütfen yeniden deneyin.");
      return;
    }
    (async () => {
      setLoading(true);
      try {
        const res = await axios.post(`${API}/auth/facebook`, { code, redirect_uri: FB_REDIRECT });
        if (res.data?.token) {
          loginWithToken(res.data.token, res.data.user);
          toast.success("Facebook ile giriş başarılı!");
          let to = "/hesabim";
          try { to = sessionStorage.getItem("fb_redirect_to") || to; } catch {}
          navigate(safeReturnPath(to));
        }
      } catch (err) {
        toast.error(err.response?.data?.detail || "Facebook ile giriş başarısız");
      } finally { setLoading(false); }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Apple Sign In JS SDK — sağlayıcı aktif + Services ID girildiyse yükle & init et.
  useEffect(() => {
    if (!socialProviders.apple || !socialProviders.apple_client_id) return;
    const initApple = () => {
      if (!window.AppleID?.auth) return;
      try {
        window.AppleID.auth.init({
          clientId: socialProviders.apple_client_id,   // Services ID (ör. com.meluni.web)
          scope: "name email",
          redirectURI: `${window.location.origin}/giris`,  // Apple'da Return URL olarak kayıtlı olmalı
          usePopup: true,
        });
      } catch { /* init hatası sessiz */ }
    };
    if (window.AppleID?.auth) { initApple(); return; }
    let s = document.getElementById("appleid-script");
    if (!s) {
      s = document.createElement("script");
      s.id = "appleid-script";
      s.src = "https://appleid.cdn-apple.com/appleauth/static/jsapi/appleid/1/en_US/appleid.auth.js";
      s.async = true;
      document.body.appendChild(s);
    }
    s.addEventListener("load", initApple);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [socialProviders.apple, socialProviders.apple_client_id]);

  const handleAppleLogin = async () => {
    if (!window.AppleID?.auth) { toast.error("Apple girişi henüz yüklenmedi, tekrar deneyin"); return; }
    setLoading(true);
    try {
      const resp = await window.AppleID.auth.signIn();
      const idToken = resp?.authorization?.id_token;
      if (!idToken) throw new Error("identity_token yok");
      // Ad-soyad Apple'dan YALNIZ ilk girişte gelir (sonraki girişlerde boş) — backend zaten
      // hesabı Apple'ın imzaladığı 'sub' ile eşler, isim yalnız görünürlük içindir.
      let userName = "";
      const nm = resp?.user?.name;
      if (nm) userName = `${nm.firstName || ""} ${nm.lastName || ""}`.trim();
      const res = await axios.post(`${API}/auth/apple`, { identity_token: idToken, user_name: userName });
      if (res.data?.token) {
        loginWithToken(res.data.token, res.data.user);
        toast.success("Apple ile giriş başarılı!");
        navigate(_redirectTo);
      }
    } catch (err) {
      const code = err?.error || "";
      if (code === "popup_closed_by_user" || code === "user_cancelled_authorize" || code === "user_trigger_new_signin_flow") {
        // kullanıcı vazgeçti → sessiz
      } else {
        toast.error(err.response?.data?.detail || "Apple ile giriş başarısız");
      }
    } finally { setLoading(false); }
  };

  useEffect(() => {
    const googleClientId = socialProviders.google_client_id || BUILD_GOOGLE_CLIENT_ID;
    if (!socialProviders.google || !googleClientId) return;
    if (embeddedGoogle) {
      setGoogleStatus({
        phase: "embedded",
        message: "Bu uygulama içi tarayıcı Google penceresini desteklemeyebilir.",
      });
      return;
    }
    let disposed = false;
    const flow = createGooglePopupFlow({
      exchangeCredential: async (credential) => {
        const res = await axios.post(`${API}/auth/google`, { credential });
        return res.data;
      },
      onSuccess: (result) => {
        if (disposed) return;
        loginWithToken(result.token, result.user);
        try { sessionStorage.removeItem("google_redirect_to"); } catch {}
        setLoading(false);
        setGoogleStatus({ phase: "success", message: "Giriş başarılı, yönlendiriliyorsunuz…" });
        navigate(_redirectTo, { replace: true });
      },
      onError: (code, error) => {
        if (disposed) return;
        const message = error?.response?.data?.detail || googleErrorMessage(code);
        setLoading(false);
        setGoogleStatus({ phase: "error", message });
      },
    });
    googleFlowRef.current = flow;
    const init = () => {
      if (disposed || !window.google?.accounts?.id) return;
      window.google.accounts.id.initialize({
        client_id: googleClientId,
        callback: (response) => flow.credential(response),
        error_callback: (event) => flow.popupError(event),
        ux_mode: "popup",
        button_auto_select: false,
        auto_select: false,
        itp_support: true,
      });
      if (googleBtnRef.current) {
        window.google.accounts.id.renderButton(googleBtnRef.current, {
          theme: "outline",
          size: "large",
          type: "icon",       // kompakt ikon buton → Facebook ile yan yana sığar
          shape: "square",
          locale: "tr",
          click_listener: () => {
            try { sessionStorage.setItem("google_redirect_to", _redirectTo); } catch {}
            setLoading(true);
            setGoogleStatus({ phase: "popup", message: "Google hesap penceresi bekleniyor…" });
            flow.begin();
          },
        });
        setGoogleStatus((current) => current.phase === "idle" ? { phase: "ready", message: "" } : current);
      }
    };
    if (window.google?.accounts?.id) {
      init();
    } else {
      let s = document.getElementById("gsi-script");
      if (!s) {
        s = document.createElement("script");
        s.id = "gsi-script";
        s.src = "https://accounts.google.com/gsi/client";
        s.async = true;
        s.defer = true;
        document.body.appendChild(s);
      }
      s.addEventListener("load", init);
      s.addEventListener("error", () => {
        if (!disposed) setGoogleStatus({ phase: "error", message: "Google giriş bileşeni yüklenemedi." });
      }, { once: true });
    }
    return () => {
      disposed = true;
      flow.dispose();
      googleFlowRef.current = null;
    };
  }, [socialProviders.google, socialProviders.google_client_id, embeddedGoogle, _redirectTo, loginWithToken, navigate]);

  const handleGoogleRedirectFallback = () => {
    try { sessionStorage.setItem("google_redirect_to", _redirectTo); } catch {}
    setGoogleStatus({ phase: "redirect", message: "Google'ın güvenli giriş sayfasına yönlendiriliyorsunuz…" });
    const url = `${API}/auth/google/login?return_to=${encodeURIComponent(_redirectTo)}`;
    window.location.assign(url);
  };

  useEffect(() => {
    if (user) navigate(_redirectTo, { replace: true });
  }, [user, navigate, _redirectTo]);

  if (user) return <div className="sf-page min-h-screen flex items-center justify-center text-sm">Yönlendiriliyorsunuz…</div>;

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (isRegister && (!formData.first_name.trim() || !formData.last_name.trim())) {
      toast.error("Ad ve soyad zorunludur");
      return;
    }
    setLoading(true);

    try {
      if (isRegister) {
        await register({ ...formData, first_name: formData.first_name.trim(), last_name: formData.last_name.trim() });
        toast.success("Kayıt başarılı!");
      } else {
        await login(formData.email, formData.password);
        toast.success("Giriş başarılı!");
      }
      navigate(_redirectTo);
    } catch (err) {
      toast.error(err.response?.data?.detail || "İşlem başarısız");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="sf-page min-h-screen">
      <Header />

      <div className="max-w-screen-2xl mx-auto px-4 py-16">
        <div className="max-w-sm mx-auto">
          <h1 className="text-xl font-medium text-center mb-8">
            {isRegister ? "Üye Ol" : "Giriş Yap"}
          </h1>

          {/* Sosyal giriş — ikon butonlar yan yana (Google + Facebook + Apple), eşit ölçü */}
          <div className="flex items-center justify-center gap-3 mb-6">
            {/* Google — resmi GIS ikon butonu (kırpma yok; kendi boyutunda render olur) */}
            {!embeddedGoogle && socialProviders.google && (
              <div ref={googleBtnRef} data-testid="google-login-btn" data-flow-version={GOOGLE_FLOW_VERSION} className="flex items-center justify-center" />
            )}
            {/* Facebook */}
            {socialProviders.facebook && (
              <button type="button" onClick={handleFacebookLogin} disabled={loading}
                title="Facebook ile giriş" aria-label="Facebook ile giriş"
                className="w-10 h-10 flex items-center justify-center rounded border border-[#1877F2] bg-[#1877F2] text-white hover:bg-[#0f66d6] transition-colors disabled:opacity-60"
                data-testid="facebook-login-btn">
                <svg className="w-5 h-5" viewBox="0 0 24 24" fill="currentColor">
                  <path d="M24 12.073c0-6.627-5.373-12-12-12S0 5.446 0 12.073C0 18.062 4.388 23.027 10.125 23.927v-8.385H7.078v-3.47h3.047V9.43c0-3.007 1.792-4.669 4.533-4.669 1.312 0 2.686.235 2.686.235v2.953H15.83c-1.491 0-1.956.925-1.956 1.874v2.25h3.328l-.532 3.47h-2.796v8.385C19.612 23.027 24 18.062 24 12.073z"/>
                </svg>
              </button>
            )}
            {/* Apple */}
            {socialProviders.apple && (
              <button type="button"
                onClick={handleAppleLogin}
                disabled={loading} title="Apple ile giriş" aria-label="Apple ile giriş"
                className="w-10 h-10 flex items-center justify-center rounded border border-black bg-black text-white hover:bg-gray-900 transition-colors disabled:opacity-60"
                data-testid="apple-login-btn">
                <svg className="w-5 h-5" viewBox="0 0 24 24" fill="currentColor">
                  <path d="M17.05 12.54c.02 2.8 2.43 3.73 2.46 3.74-.02.07-.38 1.29-1.24 2.55-.75 1.1-1.53 2.19-2.76 2.22-1.21.02-1.59-.71-2.97-.71-1.38 0-1.8.69-2.94.73-1.18.04-2.08-1.18-2.83-2.28C5.2 16.53 4 12.93 5.61 10.54c.8-1.19 2.23-1.94 3.77-1.96 1.15-.02 2.23.78 2.93.78.7 0 2.02-.96 3.41-.82.58.02 2.21.23 3.26 1.77-.08.05-1.94 1.13-1.93 3.37zM14.51 7.34c.63-.76 1.05-1.82.94-2.88-.9.04-2 .6-2.65 1.36-.58.67-1.09 1.75-.95 2.78 1.01.08 2.03-.51 2.66-1.26z"/>
                </svg>
              </button>
            )}
          </div>

          {socialProviders.google && ["error", "embedded"].includes(googleStatus.phase) && (
            <div className="mb-6 border border-amber-200 bg-amber-50 px-3 py-3 text-center" role="alert" data-testid="google-login-status">
              <p className="text-xs text-amber-900 mb-2">{googleStatus.message}</p>
              <button
                type="button"
                onClick={handleGoogleRedirectFallback}
                disabled={googleStatus.phase === "redirect"}
                className="text-xs underline font-medium disabled:opacity-60"
                data-testid="google-redirect-fallback"
              >
                Tam sayfa Google girişiyle devam et
              </button>
            </div>
          )}

          {["popup", "exchanging", "redirect", "success"].includes(googleStatus.phase) && googleStatus.message && (
            <p className="mb-6 text-center text-xs text-gray-600" role="status">{googleStatus.message}</p>
          )}

          {!isRegister && <div className="flex items-center gap-4 mb-6">
            <div className="flex-1 h-px bg-gray-200" />
            <span className="text-xs text-gray-500">veya</span>
            <div className="flex-1 h-px bg-gray-200" />
          </div>}

          <form onSubmit={handleSubmit} className="space-y-4">
            {isRegister && (
              <>
                <div>
                  <label htmlFor="register-first-name" className="block text-xs mb-1">Ad *</label>
                  <input
                    type="text"
                    id="register-first-name" name="given-name" autoComplete="given-name" required maxLength={100} pattern=".*\S.*"
                    value={formData.first_name}
                    onChange={(e) => setFormData({ ...formData, first_name: e.target.value })}
                    className="w-full border px-3 py-2.5 text-sm focus:outline-none focus:border-black"
                  />
                </div>
                <div>
                  <label htmlFor="register-last-name" className="block text-xs mb-1">Soyad *</label>
                  <input
                    type="text"
                    id="register-last-name" name="family-name" autoComplete="family-name" required maxLength={100} pattern=".*\S.*"
                    value={formData.last_name}
                    onChange={(e) => setFormData({ ...formData, last_name: e.target.value })}
                    className="w-full border px-3 py-2.5 text-sm focus:outline-none focus:border-black"
                  />
                </div>
                {/* Boy & kilo — size en uygun bedeni önermek için (opsiyonel) */}
                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <label className="block text-xs mb-1">Boy (cm)</label>
                    <input
                      type="number" min="100" max="230" inputMode="numeric"
                      value={formData.height_cm || ""}
                      onChange={(e) => setFormData({ ...formData, height_cm: e.target.value })}
                      placeholder="örn. 168"
                      className="w-full border px-3 py-2.5 text-sm focus:outline-none focus:border-black"
                      data-testid="height-input"
                    />
                  </div>
                  <div>
                    <label className="block text-xs mb-1">Kilo (kg)</label>
                    <input
                      type="number" min="30" max="250" inputMode="numeric"
                      value={formData.weight_kg || ""}
                      onChange={(e) => setFormData({ ...formData, weight_kg: e.target.value })}
                      placeholder="örn. 60"
                      className="w-full border px-3 py-2.5 text-sm focus:outline-none focus:border-black"
                      data-testid="weight-input"
                    />
                  </div>
                </div>
                <p className="text-[11px] text-gray-400 -mt-1">Boy/kilo ile ürün sayfasında size en uygun bedeni öneririz. (opsiyonel)</p>
              </>
            )}
            
            <div>
              <label className="block text-xs mb-1">E-posta *</label>
              <input
                type="email"
                value={formData.email}
                onChange={(e) => setFormData({ ...formData, email: e.target.value })}
                required
                className="w-full border px-3 py-2.5 text-sm focus:outline-none focus:border-black"
                data-testid="email-input"
              />
            </div>
            
            <div>
              <label className="block text-xs mb-1">Şifre *</label>
              <input
                type="password"
                value={formData.password}
                onChange={(e) => setFormData({ ...formData, password: e.target.value })}
                required
                minLength={6}
                className="w-full border px-3 py-2.5 text-sm focus:outline-none focus:border-black"
                data-testid="password-input"
              />
            </div>

            {!isRegister && (
              <div className="text-right -mt-2 mb-1">
                <button
                  type="button"
                  onClick={() => navigate("/sifremi-unuttum")}
                  className="text-xs text-gray-500 hover:text-black underline"
                >
                  Şifremi unuttum?
                </button>
              </div>
            )}

            <button
              type="submit"
              disabled={loading}
              className="w-full bg-black text-white py-3 text-xs uppercase tracking-wider hover:bg-gray-900 disabled:opacity-50"
              data-testid="submit-btn"
            >
              {loading ? "İşleniyor..." : isRegister ? "Üye Ol" : "Giriş Yap"}
            </button>
          </form>

          <div className="text-center mt-6">
            <button
              onClick={() => setIsRegister(!isRegister)}
              className="text-xs underline"
            >
              {isRegister ? "Zaten üye misiniz? Giriş yapın" : "Hesabınız yok mu? Üye olun"}
            </button>
          </div>
        </div>
      </div>

      <Footer />
    </div>
  );
}
