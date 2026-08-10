import { createContext, useContext, useState, useEffect } from "react";
import axios from "axios";

const AuthContext = createContext();
const API = `${process.env.REACT_APP_BACKEND_URL}/api`;

// Güvenli localStorage — Safari "tüm çerezleri engelle" / partisyonlu depolama / bazı
// in-app tarayıcılarda localStorage erişimi SecurityError FIRLATIR. Guard'sız kullanım
// AuthProvider render'ında throw edip TÜM uygulamayı çökertiyordu ("Sayfa yüklenemedi" /
// ErrorBoundary). CartContext/FavoritesContext zaten guard'lı; Auth eksikti.
const _lsGet = (k) => { try { return localStorage.getItem(k); } catch { return null; } };
const _lsSet = (k, v) => { try { localStorage.setItem(k, v); } catch { /* storage kapalı */ } };
const _lsDel = (k) => { try { localStorage.removeItem(k); } catch { /* storage kapalı */ } };

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null);
  const [token, setToken] = useState(() => _lsGet("token"));
  const [loading, setLoading] = useState(() => Boolean(_lsGet("token")));

  useEffect(() => {
    if (token) {
      axios.defaults.headers.common["Authorization"] = `Bearer ${token}`;
      fetchUser();
    }
  }, [token]);

  const fetchUser = async () => {
    try {
      const res = await axios.get(`${API}/auth/me`);
      setUser(res.data);
    } catch (err) {
      logout();
    } finally {
      setLoading(false);
    }
  };

  const login = async (email, password) => {
    // O19: Şifreyi URL query yerine istek GÖVDESİNDE gönder (log/geçmiş/Referer sızıntısı olmasın).
    const res = await axios.post(`${API}/auth/login`, { email, password });
    if (res.data?.mfa_required) {
      return { mfaRequired: true, mfaToken: res.data.mfa_token,
               mfaMethod: res.data.mfa_method || "totp", phoneMasked: res.data.phone_masked || "" };
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
  const loginWithToken = (newToken, userData) => {
    _lsSet("token", newToken);
    axios.defaults.headers.common["Authorization"] = `Bearer ${newToken}`;
    setToken(newToken);
    setUser(userData);
  };

  const verifyMfa = async (mfaToken, code) => {
    const res = await axios.post(`${API}/auth/mfa/verify`, { mfa_token: mfaToken, code });
    const { token: newToken, user: userData } = res.data;
    _lsSet("token", newToken);
    axios.defaults.headers.common["Authorization"] = `Bearer ${newToken}`;
    setToken(newToken);
    setUser(userData);
    return userData;
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
    _lsDel("token");
    delete axios.defaults.headers.common["Authorization"];
    setToken(null);
    setUser(null);
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
        verifyMfa,
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
