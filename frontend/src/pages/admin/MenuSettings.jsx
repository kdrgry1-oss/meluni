/**
 * MenuSettings.jsx — kullanıcının kendi admin menüsünü düzenlediği sayfa.
 * • Grup sıralaması: yukarı/aşağı butonları
 * • Gizleme: göz aç/kapa
 * • Sıfırla: varsayılana dön
 * Tercihler localStorage'da saklanır (anahtar: menuOrder:{userId}, menuHidden:{userId}).
 */
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";
import { ArrowUp, ArrowDown, Eye, EyeOff, RotateCcw, Save, LayoutDashboard } from "lucide-react";
import {
  navigationGroups, loadUserMenuPrefs, saveUserMenuPrefs, resetUserMenuPrefs,
} from "../../lib/adminNav";
import { useAuth } from "../../context/AuthContext";

export default function MenuSettings() {
  const { user } = useAuth();
  const navigate = useNavigate();
  const userId = user?.id || user?.email;
  const [items, setItems] = useState([]);  // [{key,label,icon,hidden}]
  const [dirty, setDirty] = useState(false);

  useEffect(() => {
    const { order, hidden } = loadUserMenuPrefs(userId);
    const hiddenSet = new Set(hidden || []);
    const byKey = Object.fromEntries(navigationGroups.map((g) => [g.key, g]));
    let arr;
    if (order && order.length) {
      arr = [];
      const seen = new Set();
      for (const k of order) {
        if (byKey[k]) { arr.push(byKey[k]); seen.add(k); }
      }
      for (const g of navigationGroups) if (!seen.has(g.key)) arr.push(g);
    } else {
      arr = [...navigationGroups];
    }
    setItems(arr.map((g) => ({ ...g, hidden: hiddenSet.has(g.key) })));
  }, [userId]);

  const move = (idx, dir) => {
    const newIdx = idx + dir;
    if (newIdx < 0 || newIdx >= items.length) return;
    const next = [...items];
    [next[idx], next[newIdx]] = [next[newIdx], next[idx]];
    setItems(next);
    setDirty(true);
  };

  const toggleHidden = (idx) => {
    const next = [...items];
    next[idx] = { ...next[idx], hidden: !next[idx].hidden };
    setItems(next);
    setDirty(true);
  };

  const save = () => {
    const order = items.map((i) => i.key);
    const hidden = items.filter((i) => i.hidden).map((i) => i.key);
    saveUserMenuPrefs(userId, { order, hidden });
    // Sunucuya da yaz — tüm cihazlarda kalıcı (gizlenen sekme bir daha geri gelmez)
    try {
      const t = localStorage.getItem("token");
      fetch(`${process.env.REACT_APP_BACKEND_URL}/api/settings/admin-menu-prefs`, {
        method: "PUT",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${t}` },
        body: JSON.stringify({ order, hidden }),
      }).catch(() => {});
    } catch { /* sessiz */ }
    setDirty(false);
    toast.success("Menü düzeniniz kaydedildi. Sayfa yenileniyor...");
    setTimeout(() => window.location.reload(), 800);
  };

  const reset = () => {
    if (!window.confirm("Varsayılana sıfırlansın mı?")) return;
    resetUserMenuPrefs(userId);
    toast.success("Sıfırlandı. Sayfa yenileniyor...");
    setTimeout(() => window.location.reload(), 600);
  };

  return (
    <div data-testid="menu-settings-page" className="max-w-3xl mx-auto space-y-6">
      <div>
        <h1 className="text-2xl font-light text-gray-900 flex items-center gap-2">
          <LayoutDashboard className="w-6 h-6" /> Menü Düzeni
        </h1>
        <p className="text-sm text-gray-500 mt-1">
          Üstteki menü sıralamasını sürükleyip değiştirin (▲ ▼ ile) ve gerekmeyenleri 👁️ ile gizleyin.
          Bu ayar yalnızca <strong>{user?.email}</strong> hesabını etkiler.
        </p>
      </div>

      <div className="bg-white border border-gray-200 rounded-lg overflow-hidden shadow-sm">
        <div className="grid grid-cols-12 px-4 py-2 bg-gray-50 text-xs font-semibold text-gray-600 border-b">
          <div className="col-span-1">Sıra</div>
          <div className="col-span-7">Menü Grubu</div>
          <div className="col-span-2 text-center">Görünür</div>
          <div className="col-span-2 text-center">İşlemler</div>
        </div>
        <ul data-testid="menu-list">
          {items.map((it, idx) => {
            const Icon = it.icon;
            return (
              <li key={it.key} className={`grid grid-cols-12 items-center px-4 py-3 border-b last:border-b-0 ${it.hidden ? "opacity-50" : ""}`}>
                <div className="col-span-1 text-sm text-gray-500 font-mono">{idx + 1}</div>
                <div className="col-span-7 flex items-center gap-3">
                  {Icon && <Icon className="w-4 h-4 text-gray-500" />}
                  <span className="text-sm font-medium text-gray-900">{it.label}</span>
                  <span className="text-xs text-gray-400 font-mono">{it.key}</span>
                </div>
                <div className="col-span-2 text-center">
                  <button
                    onClick={() => toggleHidden(idx)}
                    data-testid={`menu-toggle-${it.key}`}
                    className={`inline-flex items-center gap-1 px-2 py-1 rounded text-xs font-medium ${it.hidden ? "bg-red-50 text-red-700 hover:bg-red-100" : "bg-emerald-50 text-emerald-700 hover:bg-emerald-100"}`}
                  >
                    {it.hidden ? <><EyeOff className="w-3.5 h-3.5" />Gizli</> : <><Eye className="w-3.5 h-3.5" />Açık</>}
                  </button>
                </div>
                <div className="col-span-2 flex items-center justify-center gap-1">
                  <button
                    onClick={() => move(idx, -1)}
                    disabled={idx === 0}
                    data-testid={`menu-up-${it.key}`}
                    className="p-1.5 rounded hover:bg-gray-100 disabled:opacity-30 disabled:cursor-not-allowed"
                    title="Yukarı taşı"
                  >
                    <ArrowUp className="w-4 h-4" />
                  </button>
                  <button
                    onClick={() => move(idx, +1)}
                    disabled={idx === items.length - 1}
                    data-testid={`menu-down-${it.key}`}
                    className="p-1.5 rounded hover:bg-gray-100 disabled:opacity-30 disabled:cursor-not-allowed"
                    title="Aşağı taşı"
                  >
                    <ArrowDown className="w-4 h-4" />
                  </button>
                </div>
              </li>
            );
          })}
        </ul>
      </div>

      <div className="flex items-center gap-3">
        <button
          onClick={save}
          disabled={!dirty}
          data-testid="menu-save-btn"
          className="flex items-center gap-2 px-4 py-2 bg-gray-900 text-white rounded-md text-sm hover:bg-gray-800 disabled:opacity-50 disabled:cursor-not-allowed"
        >
          <Save className="w-4 h-4" /> Kaydet & Uygula
        </button>
        <button
          onClick={reset}
          data-testid="menu-reset-btn"
          className="flex items-center gap-2 px-4 py-2 border border-gray-300 text-gray-700 rounded-md text-sm hover:bg-gray-50"
        >
          <RotateCcw className="w-4 h-4" /> Varsayılana Sıfırla
        </button>
        {dirty && <span className="text-xs text-amber-600">⚠ Değişikliklerinizi kaydetmediniz</span>}
      </div>

      <div className="text-xs text-gray-500 bg-blue-50 border border-blue-200 rounded p-3">
        💡 <strong>İpucu:</strong> Logoya tıklayınca her zaman Dashboard'a dönersiniz. "Görevler" ve diğer tek seviyeli menü öğelerini de buradan gizleyebilirsiniz.
      </div>
    </div>
  );
}
