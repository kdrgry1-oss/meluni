import { createContext, useContext, useState, useEffect, useCallback } from "react";
import axios from "axios";
import { toast } from "sonner";
import { useAuth } from "./AuthContext";

const FavoritesContext = createContext();
const API = `${process.env.REACT_APP_BACKEND_URL}/api`;
const LS_KEY = "facette_favorites";

const readLocal = () => {
  try {
    const v = JSON.parse(localStorage.getItem(LS_KEY) || "[]");
    return Array.isArray(v) ? v : [];  // bozuk/dizi-olmayan değerde .map çökmesini önle
  } catch {
    return [];
  }
};
const writeLocal = (ids) => localStorage.setItem(LS_KEY, JSON.stringify(ids));

export function FavoritesProvider({ children }) {
  const { user, token } = useAuth();
  const [ids, setIds] = useState(() => new Set(readLocal()));

  const authHeader = useCallback(
    () => ({ headers: { Authorization: `Bearer ${localStorage.getItem("token")}` } }),
    []
  );

  // Login olunca: localStorage favorilerini sunucuya merge et + sunucudan çek
  useEffect(() => {
    if (!token || !user) return;
    let cancelled = false;
    (async () => {
      try {
        const local = readLocal();
        if (local.length) {
          await axios.post(`${API}/favorites/merge`, { product_ids: local }, authHeader());
          writeLocal([]);
        }
        const res = await axios.get(`${API}/favorites/ids`, authHeader());
        if (!cancelled) setIds(new Set(res.data?.product_ids || []));
      } catch {
        /* sessiz */
      }
    })();
    return () => { cancelled = true; };
  }, [token, user, authHeader]);

  const isFavorite = useCallback((id) => ids.has(id), [ids]);

  const toggleFavorite = useCallback(
    async (product) => {
      const id = typeof product === "string" ? product : product?.id;
      if (!id) return;
      const currentlyFav = ids.has(id);
      // Optimistic UI
      setIds((prev) => {
        const next = new Set(prev);
        if (currentlyFav) next.delete(id);
        else next.add(id);
        return next;
      });

      if (token && user) {
        try {
          if (currentlyFav) await axios.delete(`${API}/favorites/${id}`, authHeader());
          else await axios.post(`${API}/favorites/${id}`, {}, authHeader());
        } catch {
          // rollback
          setIds((prev) => {
            const next = new Set(prev);
            if (currentlyFav) next.add(id);
            else next.delete(id);
            return next;
          });
          toast.error("Favori işlemi başarısız");
          return;
        }
      } else {
        const next = currentlyFav
          ? readLocal().filter((x) => x !== id)
          : [...new Set([...readLocal(), id])];
        writeLocal(next);
      }
      toast.success(currentlyFav ? "Favorilerden çıkarıldı" : "Favorilere eklendi");
    },
    [ids, token, user, authHeader]
  );

  return (
    <FavoritesContext.Provider
      value={{ ids, count: ids.size, isFavorite, toggleFavorite }}
    >
      {children}
    </FavoritesContext.Provider>
  );
}

export function useFavorites() {
  return useContext(FavoritesContext);
}
