import { act } from "react";
import { createRoot } from "react-dom/client";
import PageDesign from "./PageDesign";

jest.mock("axios", () => ({ get: jest.fn(), post: jest.fn(), put: jest.fn(), delete: jest.fn() }));
jest.mock("sonner", () => ({ toast: { error: jest.fn(), success: jest.fn() } }));
jest.mock("../../components/ui/dialog", () => {
  const React = require("react");
  return {
    Dialog: ({ children }) => React.createElement("div", null, children),
    DialogContent: ({ children }) => React.createElement("div", null, children),
    DialogHeader: ({ children }) => React.createElement("div", null, children),
    DialogTitle: ({ children }) => React.createElement("h2", null, children),
  };
});

const axios = require("axios");

describe("PageDesign workspace", () => {
  let container;
  let root;

  beforeAll(() => { global.IS_REACT_ACT_ENVIRONMENT = true; });
  afterAll(() => { delete global.IS_REACT_ACT_ENVIRONMENT; });

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    axios.get.mockImplementation((url) => Promise.resolve({
      data: url.includes("/categories") ? [] : [
        { id: "hero-1", type: "hero_slider", title: "Ana Slider", images: [], links: [], settings: {}, sort_order: 1, is_active: true, page: "home" },
      ],
    }));
  });

  afterEach(async () => {
    await act(async () => root.unmount());
    container.remove();
    jest.clearAllMocks();
  });

  test("renders library, flow, properties and draft preview without mutating loaded content", async () => {
    await act(async () => root.render(<PageDesign />));
    await act(async () => Promise.resolve());
    expect(container.textContent).toContain("Blok Kütüphanesi");
    expect(container.textContent).toContain("Ana Sayfa Akışı");
    expect(container.textContent).toContain("Blok Özellikleri");
    expect(container.textContent).toContain("Gerçek Zamanlı Önizleme");
    expect(container.textContent).toContain("Ana Slider");
    expect(container.querySelector('[data-testid="unsaved-indicator"]').textContent).toContain("kayıtlı");

    const textBlockButton = [...container.querySelectorAll("button")]
      .find((button) => button.textContent.includes("Yazı Bloğu"));
    await act(async () => textBlockButton.dispatchEvent(new MouseEvent("click", { bubbles: true })));
    expect(container.querySelector('[data-testid="unsaved-indicator"]').textContent).toContain("Kaydedilmemiş");
    expect(container.textContent).toContain("2 blok");
    expect(axios.post).not.toHaveBeenCalled();
    expect(axios.put).not.toHaveBeenCalled();
    expect(axios.delete).not.toHaveBeenCalled();
  });
});
