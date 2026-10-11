import { navigationGroups, PAGE_PERMS, canSeeAdminPath, getNavigationFor } from "./adminNav";

function allItems() {
  return navigationGroups.flatMap((group) => group.children || []);
}

test("shows only the advanced email marketing entry under Marketing", () => {
  // "Pazarlama İzinleri (E-posta/SMS)" bir izin/rıza yönetim ekranıdır (gönderim aracı değil) → hariç.
  const entries = allItems().filter((item) =>
    /e-?posta|toplu mail/i.test(String(item.label || "")) && item.path !== "/admin/izinler"
  );

  expect(entries).toHaveLength(1);
  expect(entries[0]).toMatchObject({
    label: "E-posta Pazarlama",
    path: "/admin/eposta-pazarlama",
  });

  const marketing = navigationGroups.find((group) => group.label === "Pazarlama");
  expect(marketing?.children).toContain(entries[0]);
});

test("every internal menu page declares its view permission (except open pages)", () => {
  // Görevler + Eğitim & Yardım tüm personele açık; süper-admin sayfaları ayrıca superOnly ile gizli.
  const open = new Set(["/admin/gorevler", "/admin/egitim"]);
  const items = [...allItems(), ...navigationGroups.filter((g) => g.path)];
  const missing = items
    .filter((item) => item.path && !item.external && !item.superOnly && !open.has(item.path))
    .filter((item) => !PAGE_PERMS[item.path])
    .map((item) => item.path);
  expect(missing).toEqual([]);
});

test("menu hides pages the staff role cannot use", () => {
  expect(canSeeAdminPath("/admin/siparisler", ["orders.view"])).toBe(true);
  expect(canSeeAdminPath("/admin/siparisler", ["products.view"])).toBe(false);
  expect(canSeeAdminPath("/admin/siparisler", ["*"])).toBe(true);
  expect(canSeeAdminPath("/admin/siparisler", undefined)).toBe(true);
  expect(canSeeAdminPath("/admin/gorevler", ["products.view"])).toBe(true);
  const nav = getNavigationFor("iam02-test-user", ["products.view"]);
  const paths = nav.flatMap((g) => (g.children ? g.children.map((c) => c.path) : [g.path]));
  expect(paths).toContain("/admin/urunler");
  expect(paths).not.toContain("/admin/siparisler");
  expect(nav.find((g) => g.key === "ayarlar")).toBeUndefined();
});

test("area-read pages open for any permission in the same area (matches backend any_in_area)", () => {
  expect(canSeeAdminPath("/admin/urunler", ["products.edit"])).toBe(true);
  expect(canSeeAdminPath("/admin/uyeler", ["customers.block"])).toBe(true);
  expect(canSeeAdminPath("/admin/urunler", ["orders.view"])).toBe(false);
  // yeni zorunlu alanlarda (siparişler) görüntüleme izni şart
  expect(canSeeAdminPath("/admin/siparisler", ["orders.invoice"])).toBe(false);
});
