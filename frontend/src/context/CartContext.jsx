import { createContext, useContext, useState, useEffect } from "react";

const CartContext = createContext();

export function CartProvider({ children }) {
  const [items, setItems] = useState(() => {
    // Y26: Bozuk/eski bir "cart" değeri (ör. "null") JSON.parse'ta hataya ya da items=null'a
    // yol açıp items.reduce'u patlatarak TÜM mağazayı beyaz ekran yapıyordu. Güvenli parse.
    try {
      const saved = localStorage.getItem("cart");
      const parsed = saved ? JSON.parse(saved) : [];
      return Array.isArray(parsed) ? parsed : [];
    } catch {
      try { localStorage.removeItem("cart"); } catch {}
      return [];
    }
  });
  const [isOpen, setIsOpen] = useState(false);

  useEffect(() => {
    localStorage.setItem("cart", JSON.stringify(items));
  }, [items]);

  // CANLI FİYAT/İNDİRİM TAZELEME — sepet kalemlerinin fiyat/indirimli fiyat/otomatik kampanya
  // alanlarını mevcut ürün verisinden günceller. Böylece:
  //  • Kampanya kaydedilmeden (eski) eklenmiş kalem → indirim artık DOĞRU görünür,
  //  • Admin sonradan fiyat/kampanya değiştirmişse → sepet/kasa güncel indirimli fiyatı gösterir.
  // Sunucu fiyatı zaten otoriter (çekim); bu yalnız GÖRÜNÜMÜ canlıya çeker. Değişiklik yoksa
  // state'e dokunmaz (gereksiz re-render/döngü olmaz).
  const _refreshKey = (items || []).map((it) => `${it.productId}:${it.variantId || ""}`).join(",");
  useEffect(() => {
    const list = items || [];
    if (list.length === 0) return;
    const API = process.env.REACT_APP_BACKEND_URL;
    if (!API) return;
    let cancel = false;
    const ids = [...new Set(list.map((it) => it.productId).filter(Boolean))];
    fetch(`${API}/api/products/cart-pricing`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ product_ids: ids }),
    })
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => {
        if (cancel || !data || !data.items) return;
        setItems((prev) => {
          let changed = false;
          const next = prev.map((it) => {
            const info = data.items[it.productId];
            if (!info) return it;
            const listBase = Number(info.price) || 0;
            const sp = Number(info.sale_price) || 0;
            const saleBase = sp > 0 && sp < listBase ? sp : listBase;
            const nextCamp = Number(info.campaign_discount_percent) || 0;
            let pd = null;
            if (it.variantId) {
              const vinfo = (info.variants || {})[it.variantId];
              pd = vinfo ? (Number(vinfo.price_diff) || 0) : null; // varyant bulunamazsa fiyata dokunma
            } else {
              pd = 0;
            }
            let nextPrice = Number(it.price);
            let nextList = Number(it.listPrice);
            if (pd !== null && listBase > 0) {
              nextPrice = Math.round((saleBase + pd) * 100) / 100;
              nextList = Math.round((listBase + pd) * 100) / 100;
            }
            if (nextPrice === Number(it.price) && nextList === Number(it.listPrice) && nextCamp === Number(it.campaignPct || 0)) {
              return it;
            }
            changed = true;
            return { ...it, price: nextPrice, listPrice: nextList, campaignPct: nextCamp };
          });
          return changed ? next : prev;
        });
      })
      .catch(() => {});
    return () => { cancel = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [_refreshKey]);

  // TERK EDİLEN SEPET TAKİBİ: sepet değişince (debounce 2.5sn) backend'e kaydet
  // (POST /api/cart/track). Daha önce storefront bunu HİÇ çağırmıyordu → cart_sessions
  // boş kalıyor, "Terk Edilen Sepet" paneli hep 0 gösteriyordu. Sipariş sonrası sepet
  // boşalınca (items=[]) kayıt "total 0" olur ve admin listesinde (total>0 filtresi) çıkmaz.
  useEffect(() => {
    if (!items || items.length === 0) return;
    const t = setTimeout(() => {
      try {
        const API = process.env.REACT_APP_BACKEND_URL;
        if (!API) return;
        let sid = localStorage.getItem("cart_session_id");
        if (!sid) {
          sid = (window.crypto?.randomUUID?.() || (String(Date.now()) + Math.random().toString(36).slice(2)));
          localStorage.setItem("cart_session_id", sid);
        }
        const payload = {
          session_id: sid,
          items: items.map((it) => ({
            product_id: it.productId, name: it.name, qty: it.quantity,
            price: it.price, image: it.image,
          })),
          total: items.reduce((s, it) => s + (Number(it.price) || 0) * (it.quantity || 1), 0),
        };
        try {
          const u = JSON.parse(localStorage.getItem("user") || "null");
          if (u && u.email) payload.email = u.email;
          if (u && u.id) payload.user_id = u.id;
          if (u && u.phone) payload.phone = u.phone;
        } catch (_) { /* yoksay */ }
        fetch(`${API}/api/cart/track`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
          keepalive: true,
        }).catch(() => {});
      } catch (_) { /* sessiz */ }
    }, 2500);
    return () => clearTimeout(t);
  }, [items]);

  const addItem = (product, variant = null, quantity = 1) => {
    // Y28: Kimliksiz (uydurma) varyantı varyantsız gibi işle — aksi halde variantId=null ile
    // her ekleme yeni satır oluşturup birleşmiyordu.
    if (variant && !variant.id) variant = null;
    setItems((prev) => {
      const key = variant ? `${product.id}-${variant.id}` : product.id;
      const existing = prev.find((item) =>
        variant ? item.variantId === variant.id : item.productId === product.id && !item.variantId
      );

      if (existing) {
        // Var olan kalemde adet artır + fiyat/indirim/kampanya alanlarını eldeki GÜNCEL ürün
        // verisinden tazele (eski kalemde kampanya eksikse bile artık doğru indirim görünür).
        const _listBase = Number(product.price) || 0;
        const _saleBase = product.sale_price && product.sale_price < _listBase ? product.sale_price : _listBase;
        const _pd = variant?.price_diff || variant?.price_adjustment || 0;
        return prev.map((item) => {
          if (!(variant ? item.variantId === variant.id : item.productId === product.id && !item.variantId)) return item;
          const cap = item.stock || Infinity;   // stok tavanı (eski sepet kalemlerinde stock yoksa sınırsız)
          const patch = _listBase > 0 ? {
            price: Math.round((_saleBase + _pd) * 100) / 100,
            listPrice: Math.round((_listBase + _pd) * 100) / 100,
            campaignPct: Number(product.campaign_discount_percent || 0),
          } : {};
          return { ...item, ...patch, quantity: Math.min(item.quantity + quantity, cap) };
        });
      }

      // Fiyat: para hesabı için sale_price tabanı (kampanya sepet/sunucu tarafında uygulanır).
      // listPrice + campaignPct: sepette "indirim uygulandı" görünümü için (üstü çizili + indirimli).
      const listBase = Number(product.price) || 0;
      const saleBase = product.sale_price && product.sale_price < listBase ? product.sale_price : listBase;
      const priceDiff = variant?.price_diff || variant?.price_adjustment || 0;
      const finalPrice = saleBase + priceDiff;

      return [
        ...prev,
        {
          id: key,
          productId: product.id,
          categoryId: product.category_id || null,
          slug: product.slug || product.id,
          variantId: variant?.id || null,
          name: product.name,
          price: finalPrice,
          listPrice: listBase + priceDiff,
          campaignPct: Number(product.campaign_discount_percent || 0),
          image: product.images?.[0] || "",
          size: variant?.size || null,
          color: variant?.color || null,
          stockCode: variant?.stock_code || product.stock_code || null,
          barcode: variant?.barcode || product.barcode || null,
          stock: (variant ? variant.stock : product.stock) ?? null,   // stok tavanı (oversell engeli)
          quantity: Math.min(quantity, (variant ? variant.stock : product.stock) || Infinity),
        },
      ];
    });
    setIsOpen(true);
  };

  const removeItem = (itemId) => {
    setItems((prev) => prev.filter((item) => item.id !== itemId));
  };

  const updateQuantity = (itemId, quantity) => {
    if (quantity < 1) {
      removeItem(itemId);
      return;
    }
    setItems((prev) =>
      prev.map((item) =>
        item.id === itemId
          ? { ...item, quantity: Math.min(quantity, item.stock || Infinity) }  // stok üstüne çıkma (oversell engeli)
          : item
      )
    );
  };

  const clearCart = () => {
    // Sipariş sonrası (veya elle boşaltma) sunucudaki terk-sepet kaydını SİL → satın alan
    // müşteri "terkedilmiş sepet" sayılıp hatırlatma maili almasın (denetim bulgusu #38).
    try {
      const API = process.env.REACT_APP_BACKEND_URL;
      const sid = localStorage.getItem("cart_session_id");
      if (API && sid) {
        fetch(`${API}/api/cart/mark-ordered`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ session_id: sid }),
          keepalive: true,
        }).catch(() => {});
      }
    } catch (_) { /* sessiz */ }
    setItems([]);
    setIsOpen(false);
  };

  const total = items.reduce((sum, item) => sum + item.price * item.quantity, 0);
  const itemCount = items.reduce((sum, item) => sum + item.quantity, 0);

  return (
    <CartContext.Provider
      value={{
        items,
        isOpen,
        setIsOpen,
        addItem,
        removeItem,
        updateQuantity,
        clearCart,
        total,
        itemCount,
      }}
    >
      {children}
    </CartContext.Provider>
  );
}

export function useCart() {
  return useContext(CartContext);
}
