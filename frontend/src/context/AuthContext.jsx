import { createContext, useContext, useState, useEffect } from "react";
import axios from "axios";
import { wipeLocalStaffData, isStaffToken } from "../lib/staffDataWipe";
import { isNative, appTarget } from "../lib/native";

const AuthContext = createContext();
const API = `${process.env.REACT_APP_BACKEND_URL}/api`;
// ADM-15: yönetim (personel) native uygulaması — oturum iptalinde cihaz her durumda tam temizlenir.
const ADMIN_APP = isNative && appTarget === "admin";

// Güvenli localStorage — Safari "tüm çerezleri engelle" / partisyonlu depolama / bazı
// in-app tarayıcılarda localStorage erişimi SecurityError FIRLATIR. Guard'sız kullanım
// AuthProvider render'ında throw edip TÜM uygulamayı çökertiyordu ("Sayfa yüklenemedi" /
// ErrorBoundary). CartContext/FavoritesContext zaten guard'lı; Auth eksikti.
const _lsGet = (k) => { try { return localStorage.getItem(k); } catch { return null; } };
const _lsSet = (k, v) => { try { localStorage.setItem(k, v); } catch { /* storage kapalı */ } };
const _lsDel = (k) => { try { localStorage.removeItem(k); } catch { /* storage kapalı */ } };

// Oturum düşünce (401) yönlendirme yapılmayacak kimlik-akışı uçları: giriş, MFA adımı, kayıt,
// sosyal giriş, şifre sıfırlama, davet kabulü… Bu uçlar 401'i "hatalı kod/şifre" anlamında
// döndürür; yönlendirme döngüsü olmasın.
const AUTH_FLOW_RE = /\/api\/auth\/(login|register|mfa\/|google|facebook|apple|forgot-password|verify-email|invite\/|guest-convert|convert-guest-order|change-password)/;
let _sessionRedirecting = false;

const _reqAuthHeader = (cfg) => {
  const h = cfg?.headers;
  if (!h) return "";
  try {
    if (typeof h.get === "function") return String(h.get("Authorization") || "");
  } catch { /* yoksay */ }
  return String(h.Authorization || h.authorization || "");
};

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null);
  const [token, setToken] = useState(() => _lsGet("token"));
  const [loading, setLoading] = useState(() => Boolean(_lsGet("token")));

  // OTURUM DÜŞTÜ (401) → token temizlenir ve giriş sayfasına yönlendirilir (/admin altında
  // /admin/login, vitrinde /giris), geri dönüş için `next` korunur. Yalnız BİZİM API'mize,
  // GEÇERLİ token'la yapılmış isteklerde; kimlik-akışı uçlarında ve giriş sayfasındayken yapılmaz.
  useEffect(() => {
    // Testlerde/eksik axios örneklerinde interceptor API'si olmayabilir → sessizce atla.
    const respIc = axios?.interceptors?.response;
    if (!respIc || typeof respIc.use !== "function") return undefined;
    const id = respIc.use(
      (r) => r,
      async (error) => {
        try {
          const cfg = error?.config || {};
          const stored = _lsGet("token");
          const url = String(cfg.url || "");
          const ours = url.startsWith(API) || url.startsWith("/api/");
          const sessionDropped = error?.response?.status === 401 && stored && ours
              && !AUTH_FLOW_RE.test(url) && _reqAuthHeader(cfg) === `Bearer ${stored}`;
          // ADM-15: düşen oturum bir PERSONEL oturumuysa (ya da yönetim uygulamasıysa) cihazdaki
          // önbellek tamamen silinir. Açılış kontrolü (_skipAuthRedirect) dahil; yönlendirmeden ÖNCE
          // tamamlanır. Müşteri oturumu düşünce sepet/favoriler korunur (müşteri verisi kalıcıdır).
          if (sessionDropped && (isStaffToken(stored) || ADMIN_APP)) {
            await wipeLocalStaffData();
          }
          if (sessionDropped && !cfg._skipAuthRedirect) {
            _lsDel("token");
            delete axios.defaults.headers.common["Authorization"];
            setToken(null);
            setUser(null);
            const { pathname, search } = window.location;
            const isAdminPath = pathname.startsWith("/admin");
            const loginPath = isAdminPath ? "/admin/login" : "/giris";
            if (!_sessionRedirecting && pathname !== loginPath && !pathname.startsWith("/admin/davet")) {
              _sessionRedirecting = true;
              const next = encodeURIComponent(pathname + search);
              window.location.replace(isAdminPath
                ? `/admin/login?next=${next}`
                : `/giris?next=${next}&redirect=${next}`);
            }
          }
        } catch { /* yoksay */ }
        return Promise.reject(error);
      }
    );
    return () => { try { respIc.eject(id); } catch { /* yoksay */ } };
  }, []);

  useEffect(() => {
    let active = true;
    if (token) {
      axios.defaults.headers.common["Authorization"] = `Bearer ${token}`;
      // Açılış kontrolü: süresi dolmuş token sessizce temizlenir (vitrinde yönlendirme YOK).
      axios.get(`${API}/auth/me`, { _skipAuthRedirect: true })
        .then((res) => { if (active) setUser(res.data); })
        // 426 (uygulama güncellemesi gerekli, ADM-15) oturum hatası değildir → çıkış yapılmaz;
        // engelleyici güncelleme ekranı gösterilir.
        .catch((err) => { if (active && err?.response?.status !== 426) logout(); })
        .finally(() => { if (active) setLoading(false); });
    }
    return () => { active = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token]);

  const login = async (email, password) => {
    // O19: Şifreyi URL query yerine istek GÖVDESİNDE gönder (log/geçmiş/Referer sızıntısı olmasın).
    const res = await axios.post(`${API}/auth/login`, { email, password });
    if (res.data?.mfa_required) {
      return { mfaRequired: true, mfaToken: res.data.mfa_token,
               mfaMethod: res.data.mfa_method || "totp",
               // Kanal: kişinin varsayılanı (sms|email). Giriş ekranı diğerini alternatif sunar.
               mfaChannel: res.data.mfa_channel || res.data.mfa_method || "totp",
               phoneMasked: res.data.phone_masked || "",
               emailMasked: res.data.email_masked || "",
               // Admin kanal politikası açıklaması (ör. sıfırlama kanalı bu girişte kullanılamaz).
               mfaNotice: res.data.mfa_notice || "" };
    }
    const { token: newToken, user: userData } = res.data;
    _lsSet("token", newToken);
    axios.defaults.headers.common["Authorization"] = `Bearer ${newToken}`;
    setToken(newToken);
    setUser(userData);
    // ZORUNLU MFA: kurulmamışsa frontend kurulum ekranına yönlendirir (mfa_setup_required).
    return { ...userData, mfaSetupRequired: !!res.data?.mfa_setup_required };
  };

  // Sosyal giriş (Google) sonrası: backend'den dönen JWT token + user'ı tam olarak yerleştir.
  // localStorage + axios Authorization header + context state hepsi senkron olmalı.
  // Panel etkin kullanımdayken sessiz oturum yenileme (POST /auth/staff/refresh) — yalnız token değişir.
  const replaceToken = (newToken) => {
    if (!newToken) return;
    _lsSet("token", newToken);
    axios.defaults.headers.common["Authorization"] = `Bearer ${newToken}`;
    setToken(newToken);
  };

  const loginWithToken = (newToken, userData) => {
    _lsSet("token", newToken);
    axios.defaults.headers.common["Authorization"] = `Bearer ${newToken}`;
    setToken(newToken);
    setUser(userData);
  };

  // opts.recovery=true → `code` bir MFA KURTARMA KODU'dur (tek kullanımlık).
  const verifyMfa = async (mfaToken, code, opts = {}) => {
    const body = opts.recovery
      ? { mfa_token: mfaToken, recovery_code: code }
      : { mfa_token: mfaToken, code };
    const res = await axios.post(`${API}/auth/mfa/verify`, body);
    const { token: newToken, user: userData } = res.data;
    _lsSet("token", newToken);
    axios.defaults.headers.common["Authorization"] = `Bearer ${newToken}`;
    setToken(newToken);
    setUser(userData);
    return { ...userData, recoveryCodesRemaining: res.data?.recovery_codes_remaining ?? null };
  };

  // Diğer tüm cihazlardaki oturumları kapatır; bu cihaz TAZE token ile devam eder.
  const logoutAll = async () => {
    const wasStaff = isStaffToken(_lsGet("token")) || Boolean(user?.is_admin) || ADMIN_APP;
    const res = await axios.post(`${API}/auth/logout-all`, {});
    const newToken = res.data?.token;
    // ADM-15: diğer cihazlar bir sonraki istekte `session_revoked` alıp kendini temizler; personel
    // bu cihazda da önbelleği sıfırlar (taze token aşağıda yeniden yazılır).
    if (wasStaff) await wipeLocalStaffData();
    if (newToken) {
      _lsSet("token", newToken);
      axios.defaults.headers.common["Authorization"] = `Bearer ${newToken}`;
      setToken(newToken);
    }
    return res.data;
  };

  const register = async (data) => {
    // O19: Kimlik bilgilerini gövdede gönder (şifre query'de sızmasın).
    // Referans kodu: parametre yoksa URL'deki ?ref= otomatik alınır (davet linki akışı).
    let refCode = data.referral_code || "";
    if (!refCode && typeof window !== "undefined") {
      try { refCode = new URLSearchParams(window.location.search).get("ref") || ""; } catch { /* yok */ }
    }
    const res = await axios.post(`${API}/auth/register`, {
      email: data.email || "",
      password: data.password || "",
      first_name: data.first_name || "",
      last_name: data.last_name || "",
      phone: data.phone || "",
      height_cm: data.height_cm || null,
      weight_kg: data.weight_kg || null,
      birth_date: data.birth_date || "",
      referral_code: refCode,
    });
    const { token: newToken, user: userData } = res.data;
    _lsSet("token", newToken);
    axios.defaults.headers.common["Authorization"] = `Bearer ${newToken}`;
    setToken(newToken);
    setUser(userData);
    return userData;
  };

  const logout = () => {
    // ADM-15: personel çıkışında (açık çıkış, boşta-kalma çıkışı, açılışta düşen oturum) cihazdaki
    // tüm personel verisi silinir. Müşteri çıkışı eskisi gibi (sepet/favoriler korunur).
    const wasStaff = isStaffToken(_lsGet("token")) || Boolean(user?.is_admin) || ADMIN_APP;
    if (wasStaff) wipeLocalStaffData();
    _lsDel("token");
    _lsDel("meluni_last_address");
    delete axios.defaults.headers.common["Authorization"];
    setToken(null);
    setUser(null);
    setLoading(false);
  };

  return (
    <AuthContext.Provider
      value={{
        user,
        setUser,
        token,
        loading,
        isAdmin: user?.is_admin || false,
        login,
        loginWithToken,
        replaceToken,
        verifyMfa,
        logoutAll,
        register,
        logout,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  return useContext(AuthContext);
}
