import { enforceSingleWhatsAppFab, findWhatsAppFabs, restoreSuppressedWhatsAppFabs } from "./whatsappSingleton";

describe("WhatsApp FAB singleton", () => {
  afterEach(() => { document.body.innerHTML = ""; });

  test("keeps the preferred global FAB and suppresses later theme duplicates", () => {
    document.body.innerHTML = `
      <a data-meluni-contact-fab href="https://wa.me/905000000000" style="position:fixed"></a>
      <a class="whatsapp-float" href="https://api.whatsapp.com/send?phone=905111111111"></a>
      <a href="https://wa.me/?text=share">share link</a>`;
    const preferred = document.querySelector("[data-meluni-contact-fab]");
    const result = enforceSingleWhatsAppFab(document, preferred);
    expect(result.total).toBe(2);
    expect(result.suppressed).toBe(1);
    expect(document.querySelector(".whatsapp-float").style.display).toBe("none");
    expect(document.querySelector("a[href*='?text=share']").style.display).toBe("");
  });

  test("allows one legacy FAB when the managed setting is disabled and restores safely", () => {
    document.body.innerHTML = `
      <a class="whatsapp-button" href="https://wa.me/1"></a>
      <a data-whatsapp-fab href="https://wa.me/2" style="display:flex"></a>`;
    enforceSingleWhatsAppFab(document);
    expect(findWhatsAppFabs(document)).toHaveLength(2);
    expect(document.querySelector("[data-whatsapp-fab]").style.display).toBe("none");
    restoreSuppressedWhatsAppFabs(document);
    expect(document.querySelector("[data-whatsapp-fab]").style.display).toBe("flex");
  });
});
