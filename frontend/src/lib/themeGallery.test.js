import { themeCssVariables, themeImage, themePrice } from "./themeGallery";

describe("theme gallery data adapters", () => {
  test("maps allow-listed tokens to CSS custom properties", () => {
    expect(themeCssVariables({ background: "#fff", text: "#111", unknown: "bad" })).toEqual(expect.objectContaining({ "--theme-bg": "#fff", "--theme-text": "#111" }));
    expect(themeCssVariables({ unknown: "bad" })["--unknown"]).toBeUndefined();
  });
  test("uses the first real product image and sale price", () => {
    const product = { images: [{ url: "/real.jpg" }], image: "/fallback.jpg", price: 1200, sale_price: 999 };
    expect(themeImage(product)).toBe("/real.jpg");
    expect(themePrice(product)).toContain("999");
  });
  test("reports missing price honestly", () => expect(themePrice({})).toBe("Fiyat bilgisi yok"));
});
