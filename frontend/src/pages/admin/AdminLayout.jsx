import { useState, useEffect, useRef, useMemo, useCallback } from "react";
import axios from "axios";
import { Outlet, Link, NavLink, useLocation, Navigate } from "react-router-dom";
import { LogOut, Menu, X, ChevronDown } from "lucide-react";
import { useAuth } from "../../context/AuthContext";
import { AppConfirmRoot } from "../../components/admin/AppConfirm";
import FastTooltip from "../../components/admin/FastTooltip";
import { getNavigationFor } from "../../lib/adminNav";
import ReportAssistant from "../../components/admin/ReportAssistant";

function NavItem({ item, closeMobile }) {
  const [open, setOpen] = useState(false);
  const ref = useRef(null);
  const closeTimer = useRef(null);
  const location = useLocation();

  const isChildActive = item.children?.some((c) =>
    c.path === location.pathname || location.pathname.startsWith(c.path + "/")
  );

  useEffect(() => {
    function handleOutside(e) {
      if (ref.current && !ref.current.contains(e.target)) setOpen(false);
    }
    document.addEventListener("mousedown", handleOutside);
    return () => document.removeEventListener("mousedown", handleOutside);
  }, []);

  // Hover-to-open (desktop) — küçük gecikmeyle close ki dropdown'a geçerken kaybolmasın
  const handleEnter = () => {
    if (closeTimer.current) { clearTimeout(closeTimer.current); closeTimer.current = null; }
    if (item.children) setOpen(true);
  };
  const handleLeave = () => {
    if (item.children) {
      closeTimer.current = setTimeout(() => setOpen(false), 140);
    }
  };

  if (!item.children) {
    return (
      <NavLink
        to={item.path}
        end={item.exact}
        onClick={closeMobile}
        data-testid={`nav-${item.key}`}
        className={({ isActive }) =>
          `flex items-center gap-1.5 px-2 py-2 rounded-md text-[13px] font-medium whitespace-nowrap transition-colors ${
            isActive ? "bg-gray-800 text-white" : "text-white hover:bg-gray-800"
          }`
        }
      >
        <item.icon size={15} />
        {item.label}
      </NavLink>
    );
  }

  return (
    <div className="relative" ref={ref} onMouseEnter={handleEnter} onMouseLeave={handleLeave}>
      <button
        onClick={() => setOpen(!open)}
        data-testid={`nav-${item.key}`}
        className={`flex items-center gap-1.5 px-2 py-2 rounded-md text-[13px] font-medium whitespace-nowrap transition-colors outline-none ${
          isChildActive || open ? "bg-gray-800 text-white" : "text-white hover:bg-gray-800"
        }`}
      >
        <item.icon size={15} />
        {item.label}
        <ChevronDown size={12} className={`ml-0.5 transition-transform duration-200 ${open ? "rotate-180" : ""}`} />
      </button>

      {/* Desktop dropdown */}
      {open && (
        <div className="hidden xl:block absolute left-0 mt-1 w-56 bg-gray-900 border border-gray-700 rounded-lg shadow-xl z-[60] overflow-hidden">
          {item.children.map((child) => {
            const isActive = location.pathname === child.path || (child.path && location.pathname.startsWith(child.path + "/"));
            // Dış bağlantı (ör. Zoho Webmail) → yeni sekmede aç.
            if (child.external) {
              return (
                <a
                  key={child.href}
                  href={child.href}
                  target="_blank"
                  rel="noopener noreferrer"
                  onClick={() => { setOpen(false); }}
                  className="flex items-center gap-3 px-4 py-2.5 text-sm transition-colors text-white hover:bg-gray-800"
                >
                  <child.icon size={15} className="text-gray-300" />
                  {child.label}
                </a>
              );
            }
            return (
              <Link
                key={child.path}
                to={child.path}
                onClick={() => { setOpen(false); }}
                className={`flex items-center gap-3 px-4 py-2.5 text-sm transition-colors ${
                  isActive ? "bg-gray-800 text-white" : "text-white hover:bg-gray-800"
                }`}
              >
                <child.icon size={15} className={isActive ? "text-orange-400" : "text-gray-300"} />
                {child.label}
              </Link>
            );
          })}
        </div>
      )}

      {/* Mobile inline expansion */}
      {open && (
        <div className="xl:hidden ml-4 mt-1 space-y-1">
          {item.children.map((child) => {
            const isActive = location.pathname === child.path || (child.path && location.pathname.startsWith(child.path + "/"));
            if (child.external) {
              return (
                <a
                  key={child.href}
                  href={child.href}
                  target="_blank"
                  rel="noopener noreferrer"
                  onClick={() => { setOpen(false); if (closeMobile) closeMobile(); }}
                  className="flex items-center gap-3 px-3 py-2 rounded-md text-sm transition-colors text-white hover:bg-gray-800"
                >
                  <child.icon size={15} />
                  {child.label}
                </a>
              );
            }
            return (
              <Link
                key={child.path}
                to={child.path}
                onClick={() => { setOpen(false); if (closeMobile) closeMobile(); }}
                className={`flex items-center gap-3 px-3 py-2 rounded-md text-sm transition-colors ${
                  isActive ? "bg-gray-800 text-white" : "text-white hover:bg-gray-800"
                }`}
              >
                <child.icon size={15} />
                {child.label}
              </Link>
            );
          })}
        </div>
      )}
    </div>
  );
}

export default function AdminLayout() {
  const { user, isAdmin, logout, loading, replaceToken } = useAuth();
  const [mobileOpen, setMobileOpen] = useState(false);
  const location = useLocation();
  // Menü tercihleri SUNUCUDAN eşitlenir (cihaz/tarayıcı bağımsız — "Görevler geri
  // geliyor" bumerang sorunu localStorage'ın cihaz bazlı olmasındandı).
  const [navBump, setNavBump] = useState(0);
  // YETKİLER: pazaryeri yönetim sayfaları yalnız süper-admin'e açık (arka uç
  // require_super_admin ile kilitli). Menüden de gizlenir ki kimse 403 alan bir
  // bağlantıya tıklamasın. undefined = henüz yüklenmedi → menü değiştirilmez.
  const [myPerms, setMyPerms] = useState(undefined);
  // Boşta kalma süresi (dk) — İşletme Kuralı panel.auto_logout_minutes (0 = kapalı). Sunucu da
  // aynı süreyi uygular (süre dolunca 401 → giriş sayfası).
  const [idleMinutes, setIdleMinutes] = useState(1440);
  useEffect(() => {
    const t = localStorage.getItem("token");
    if (!t) return;
    fetch(`${process.env.REACT_APP_BACKEND_URL}/api/admin/me/permissions`,
          { headers: { Authorization: `Bearer ${t}` } })
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => {
        setMyPerms(Array.isArray(d?.permissions) ? d.permissions : []);
        const m = Number(d?.auto_logout_minutes);
        if (Number.isFinite(m) && m >= 0) setIdleMinutes(m);
      })
      .catch(() => setMyPerms(undefined));
  }, [user?.id, user?.email]);
  useEffect(() => {
    const uid = user?.id || user?.email;
    if (!uid) return;
    const t = localStorage.getItem("token");
    if (!t) return;
    fetch(`${process.env.REACT_APP_BACKEND_URL}/api/settings/admin-menu-prefs`, { headers: { Authorization: `Bearer ${t}` } })
      .then((r) => (r.ok ? r.json() : null))
      .then((prefs) => {
        if (!prefs) return;
        if (Array.isArray(prefs.hidden)) localStorage.setItem(`menuHidden:${uid}`, JSON.stringify(prefs.hidden));
        if (Array.isArray(prefs.order)) localStorage.setItem(`menuOrder:${uid}`, JSON.stringify(prefs.order));
        setNavBump((x) => x + 1);
      })
      .catch(() => {});
  }, [user?.id, user?.email]);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const navigation = useMemo(() => getNavigationFor(user?.id || user?.email, myPerms),
                             [user?.id, user?.email, navBump, myPerms]);

  // Panel işlemsiz kalınca (İşletme Kuralı; varsayılan 60 dk) otomatik çıkış + filtre sıfırlama.
  // Aktivite (fare/klavye/tık/scroll) olunca süre sıfırlanır. Hook'lar erken return'den ÖNCE.
  // Etkin kullanımda oturum sessizce yenilenir (en çok 5 dk'da bir): form doldururken/gezinirken sunucu
  // boşta-kalma sayacı ve 12 saatlik token süresi yüzünden çıkış olmaz (ilk girişten en çok 7 gün).
  const lastRefreshRef = useRef(Date.now());
  const refreshSession = useCallback(() => {
    if (Date.now() - lastRefreshRef.current < 5 * 60 * 1000) return;
    lastRefreshRef.current = Date.now();
    axios.post(`${process.env.REACT_APP_BACKEND_URL}/api/auth/staff/refresh`, {})
      .then((r) => { if (r?.data?.token) replaceToken(r.data.token); })
      .catch(() => { /* 401 ise genel yakalayıcı çıkışa yönlendirir */ });
  }, [replaceToken]);

  // ÇOK SEKME: son etkinlik localStorage'da ORTAK tutulur. Eskiden her sekmenin kendi sayacı vardı;
  // personel başka sekmede çalışırken boşta duran sekme süresi dolunca logout() çağırıyor ve token
  // tüm sekmelerden siliniyordu ("panelden çok çabuk atılıyorum"). Artık süre dolduğunda ortak son
  // etkinliğe bakılır; herhangi bir sekmede yakın zamanda işlem varsa çıkış yapılmaz, sayaç yeniden kurulur.
  useEffect(() => {
    if (!user || !idleMinutes) return undefined;
    const KEY = "panelLastActivity";
    const windowMs = idleMinutes * 60 * 1000;
    const readShared = () => { try { return Number(localStorage.getItem(KEY)) || 0; } catch (_) { return 0; } };
    let timer;
    let lastWrite = 0;
    const schedule = (ms) => {
      clearTimeout(timer);
      timer = setTimeout(() => {
        const idleFor = Date.now() - Math.max(readShared(), lastWrite);
        if (idleFor < windowMs) { schedule(windowMs - idleFor); return; }
        try {
          // Sayfa filtreleri genelde bileşen state'i; çıkışta sıfırlanır. Kalıcı filtre anahtarlarını temizle.
          Object.keys(localStorage).forEach((k) => { if (/filter|filtre/i.test(k)) localStorage.removeItem(k); });
        } catch (_) { /* yoksay */ }
        logout();
      }, Math.max(1000, ms));
    };
    const reset = () => {
      refreshSession();
      const now = Date.now();
      if (now - lastWrite > 15000) {   // her fare hareketinde yazma — 15 sn'de bir yeter
        lastWrite = now;
        try { localStorage.setItem(KEY, String(now)); } catch (_) { /* storage kapalı */ }
      }
      schedule(windowMs);
    };
    const events = ["mousemove", "keydown", "click", "scroll", "touchstart"];
    events.forEach((e) => window.addEventListener(e, reset, { passive: true }));
    reset();
    return () => { clearTimeout(timer); events.forEach((e) => window.removeEventListener(e, reset)); };
  }, [user, logout, idleMinutes, refreshSession]);

  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-gray-100">
        <p>Yükleniyor...</p>
      </div>
    );
  }

  if (!user || !isAdmin) {
    return <Navigate to="/admin/login" />;
  }

  return (
    <div className="min-h-screen bg-gray-100 flex flex-col" data-testid="admin-layout">
      {/* Top Navigation Bar */}
      <header className="fixed top-0 left-0 right-0 z-50 h-14 bg-gray-900 text-white border-b border-gray-800 flex items-center px-4 gap-4 overflow-visible">
        {/* Logo — Dashboard linki */}
        <Link
          to="/admin"
          data-testid="admin-logo-home"
          className="text-base font-bold tracking-[0.2em] shrink-0 text-white hover:text-gray-300 transition-colors"
        >
          {(process.env.REACT_APP_SITE_NAME || "YÖNETİM").toUpperCase()}
        </Link>

        {/* Desktop Nav — tam yatay menü ancak ~1280px'e sığar; altında (iPad yatay/dikey dahil)
            hamburger kullanılır (dropdown'lar satır-içi açılır, kırpılmaz). */}
        <nav className="hidden xl:flex items-center gap-0.5 flex-1 min-w-0">
          {navigation.map((item) => (
            <NavItem key={item.key} item={item} />
          ))}
        </nav>

        {/* User area */}
        <div className="hidden xl:flex items-center gap-2 ml-auto shrink-0 border-l border-gray-800 pl-3">
          <div className="text-right">
            <p className="text-xs text-white font-medium leading-tight">{user.email}</p>
            <p className="text-xs text-gray-300">Admin</p>
          </div>
          <button
            onClick={logout}
            data-testid="admin-logout-btn"
            className="flex items-center gap-1 text-xs text-white hover:text-red-400 transition-colors px-2 py-1 rounded hover:bg-red-500/10"
          >
            <LogOut size={15} />
            Çıkış
          </button>
        </div>

        {/* Mobile/Tablet menu toggle (iPad dahil <1280) */}
        <button
          className="xl:hidden ml-auto text-white hover:text-gray-300"
          onClick={() => setMobileOpen(!mobileOpen)}
          aria-label={mobileOpen ? "Menüyü kapat" : "Menüyü aç"}
          aria-expanded={mobileOpen}
          data-testid="admin-mobile-toggle"
        >
          {mobileOpen ? <X size={22} /> : <Menu size={22} />}
        </button>
      </header>

      {/* Mobile/Tablet Menu (iPad dahil <1280) */}
      {mobileOpen && (
        <div className="xl:hidden fixed top-14 left-0 right-0 z-40 bg-gray-900 border-b border-gray-800 px-4 py-4 space-y-1 max-h-[80vh] overflow-y-auto shadow-2xl">
          {navigation.map((item) => (
            <NavItem key={item.key} item={item} closeMobile={() => setMobileOpen(false)} />
          ))}
          <div className="pt-4 mt-4 border-t border-gray-800 flex items-center justify-between">
            <div>
              <p className="text-sm text-white">{user.email}</p>
              <p className="text-xs text-gray-300">Admin</p>
            </div>
            <button onClick={logout} className="text-white hover:text-red-400">
              <LogOut size={18} />
            </button>
          </div>
        </div>
      )}

      {/* Page Content */}
      <main className="pt-14 flex-1">
        <div className="p-4 md:p-6 lg:p-8 max-w-full">
          <Outlet />
        </div>
      </main>
      <AppConfirmRoot />
      <FastTooltip />
      {/* "Bana Sor" rapor asistanı — yalnız rapor sayfalarında. */}
      {location.pathname.startsWith("/admin/raporlar") && <ReportAssistant />}
    </div>
  );
}
