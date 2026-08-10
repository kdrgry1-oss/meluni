// Header menüsü — tek kaynak.
// Vitrin Header'ı ve Admin "Menü Yönetimi" sayfası bu varsayılanı paylaşır.
// Panelden kayıt yapıldığında menü artık /api/page-blocks/header-menu'den gelir;
// kayıt yoksa (veya API'ye ulaşılamazsa) aşağıdaki varsayılan kullanılır —
// yani bu dosya, sitenin bugünkü menüsünün birebir kopyasıdır.

export const DEFAULT_MENU_TABS = [
  { id: "yeni", label: "YENİ KOLEKSİYON", type: "link", link: "/en-yeniler", style: "accent", active: true },
  {
    id: "giyim", label: "GİYİM", type: "mega", link: "/giyim", style: "normal", active: true,
    columns: [
      {
        title: "ÜST GİYİM", link: "/ust-giyim",
        items: [
          { name: "Elbise", link: "/elbise" },
          { name: "Bluz", link: "/bluz" },
          { name: "Kazak", link: "/kazak" },
          { name: "Sweatshirt", link: "/sweatshirt" },
          { name: "Takım", link: "/takim" },
          { name: "Tişört", link: "/tisort" },
          { name: "Gömlek", link: "/gomlek" },
        ],
      },
      {
        title: "ALT GİYİM", link: "/alt-giyim",
        items: [
          { name: "Etek", link: "/etek" },
          { name: "Pantolon", link: "/pantolon" },
          { name: "Şort", link: "/sort" },
          { name: "Jean", link: "/jean" },
        ],
      },
      {
        title: "DIŞ GİYİM", link: "/dis-giyim",
        items: [
          { name: "Kaban", link: "/kaban" },
          { name: "Mont", link: "/mont" },
          { name: "Hırka", link: "/hirka" },
          { name: "Trençkot", link: "/trenckot" },
          { name: "Ceket", link: "/ceket" },
        ],
      },
    ],
  },
  {
    id: "aksesuar", label: "AKSESUAR", type: "mega", link: "/aksesuar", style: "normal", active: true,
    columns: [
      {
        title: "AKSESUAR", link: "/aksesuar",
        items: [
          { name: "Çanta", link: "/canta" },
          { name: "Şal", link: "/sal" },
          { name: "Atkı", link: "/atki" },
          { name: "Kemer", link: "/kemer" },
          { name: "Şapka", link: "/sapka" },
        ],
      },
    ],
  },
  { id: "sale", label: "SALE", type: "link", link: "/sale", style: "sale", active: true },
];

// Link'ten ürün-fetch slug'ı türet: "/elbise" → "elbise", "/giyim?kategori=etek" → "etek"
export function slugFromLink(link) {
  const s = String(link || "");
  const m = s.match(/[?&]kategori=([^&#]+)/);
  if (m) return decodeURIComponent(m[1]);
  const seg = s.split("?")[0].split("#")[0].split("/").filter(Boolean).pop();
  return seg || "";
}

// Menü API'den bir kez çekilir, tüm Header mount'ları aynı promise'i paylaşır.
let _menuPromise = null;
export function fetchHeaderMenu(apiBase) {
  if (!_menuPromise) {
    _menuPromise = fetch(`${apiBase}/page-blocks/header-menu`)
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => {
        const tabs = Array.isArray(d?.tabs) ? d.tabs.filter((t) => t && t.label) : [];
        return tabs.length ? tabs : DEFAULT_MENU_TABS;
      })
      .catch(() => DEFAULT_MENU_TABS);
  }
  return _menuPromise;
}
