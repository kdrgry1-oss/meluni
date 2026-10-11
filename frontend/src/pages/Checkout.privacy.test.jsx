import { act } from "react";
import { createRoot } from "react-dom/client";
import Checkout from "./Checkout";
import axios from "axios";

let mockUser = null;
let mockTotal = 100;
const mockNavigate = jest.fn();
const mockSearch = new URLSearchParams();
const mockCartItems = [{ id: "test", productId: "test", name: "Test Ürün", quantity: 1, price: 100 }];
jest.mock("react-router-dom", () => ({ useNavigate: () => mockNavigate, useSearchParams: () => [mockSearch] }), { virtual: true });
jest.mock("../context/AuthContext", () => ({ useAuth: () => ({ user: mockUser }) }));
jest.mock("../context/CartContext", () => ({ useCart: () => ({ items: mockCartItems, total: mockTotal, clearCart: jest.fn() }) }));
jest.mock("../lib/shipping", () => ({ useShipping: () => ({ shippingFee: 99, freeShippingThreshold: 4000 }) }));
jest.mock("../components/Header", () => () => null);
jest.mock("../components/Footer", () => () => null);
jest.mock("../components/ProvinceDistrictSelect", () => () => null);
jest.mock("../utils/pixelEvents", () => ({ trackInitiateCheckout: jest.fn(), trackPurchase: jest.fn(), trackAddPaymentInfo: jest.fn(), trackAddShippingInfo: jest.fn() }));
jest.mock("../lib/dataLayer", () => ({ collectClickIds: () => ({}) }));
jest.mock("../lib/attribution", () => ({ getSessionId: () => "test-session" }));
jest.mock("sonner", () => ({ toast: { error: jest.fn(), success: jest.fn(), info: jest.fn() } }));
jest.mock("axios", () => ({ get: jest.fn(), post: jest.fn() }));

describe("checkout address isolation", () => {
  let root, container;
  beforeAll(() => { global.IS_REACT_ACT_ENVIRONMENT = true; });
  afterAll(() => { delete global.IS_REACT_ACT_ENVIRONMENT; });
  beforeEach(() => {
    mockUser = null;
    mockTotal = 100;
    Object.assign(mockCartItems[0], {price: 100, listPrice: 100, campaignPct: 0});
    localStorage.clear();
    container = document.createElement("div");
    root = createRoot(container);
    axios.get.mockResolvedValue({ data: {} });
    axios.post.mockResolvedValue({ data: { applied: [], eligible: [], discount: 0 } });
  });
  afterEach(async () => { await act(async () => root.unmount()); jest.clearAllMocks(); });

  test("guest checkout deletes the legacy address and never displays it", async () => {
    localStorage.setItem("meluni_last_address", JSON.stringify({ first_name: "PRIVATE_PREVIOUS_CUSTOMER", address: "PRIVATE_ADDRESS" }));
    await act(async () => root.render(<Checkout />));
    expect(localStorage.getItem("meluni_last_address")).toBeNull();
    expect(container.textContent).not.toContain("PRIVATE_PREVIOUS_CUSTOMER");
    expect(container.textContent).not.toContain("PRIVATE_ADDRESS");
  });

  test.each([[4210.52, false], [4210.53, true]])('rendered checkout includes bank discount at %s boundary', async (total, free) => {
    mockTotal = total;
    axios.post.mockResolvedValue({ data: { applied: [{ code: 'KARGO0', title: 'Ücretsiz Kargo', free_shipping: true, discount: 0 }], total_discount: 0 } });
    await act(async () => root.render(<Checkout />));
    const row = container.querySelector('[data-testid="shipping-cost"]');
    expect(row.textContent.includes('Bedava')).toBe(free);
    expect(row.textContent).toContain('99.00 TL');
    const promos = container.querySelector('[data-testid="applied-promotions"]');
    expect(promos.textContent.includes('Ücretsiz Kargo')).toBe(free);
  });

  test("account switch clears address and ignores the former account's delayed response", async () => {
    let resolveOld;
    mockUser = { id: "old", email: "old@example.test" };
    axios.get.mockImplementation((url) => url.endsWith("/my-addresses")
      ? new Promise((r) => { resolveOld = r; }) : Promise.resolve({ data: {} }));
    await act(async () => root.render(<Checkout />));
    mockUser = { id: "new", email: "new@example.test" };
    axios.get.mockResolvedValue({ data: {} });
    await act(async () => root.render(<Checkout />));
    await act(async () => resolveOld({ data: { addresses: [{ first_name: "PRIVATE_PREVIOUS_CUSTOMER", address: "PRIVATE_ADDRESS" }] } }));
    expect(container.textContent).not.toContain("PRIVATE_PREVIOUS_CUSTOMER");
    expect(container.textContent).not.toContain("PRIVATE_ADDRESS");
    expect(localStorage.getItem("meluni_last_address")).toBeNull();
  });

  test.each([
    [3790, 3790, 758, 303.2, 2691.36],
    // Backend cap/scope can differ from the cached product badge: never recalculate it.
    [3790, 3790, 400, 339, 2997.45],
    // Embedded sale price stays separate from campaign discounts.
    [4000, 3790, 0, 379, 3339.45],
    // Campaign removed: cached 20% badge must not produce a phantom discount.
    [3790, 3790, 0, 0, 3699.5],
  ])('both summaries use authoritative discounts (%s/%s/%s/%s)', async (listPrice, total, launch, welcome, final) => {
    mockTotal = total;
    Object.assign(mockCartItems[0], {price: total, listPrice, campaignPct: 20});
    const applied = [
      {coupon_id: 'launch', code: 'LAUNCH', title: 'Lansman %20', discount: launch},
      {coupon_id: 'welcome', code: 'WELCOME', title: 'Hoş geldin %10', discount: welcome},
    ].filter(p => p.discount > 0);
    axios.post.mockResolvedValue({data: {applied, total_discount: launch + welcome}});
    await act(async () => root.render(<Checkout />));
    const lower = container.querySelector('[data-testid="promotion-totals"]');
    expect(lower.children).toHaveLength(applied.length);
    applied.forEach((p, i) => {
      expect(lower.children[i].textContent).toBe(`${p.title}-${p.discount.toFixed(2)} TL`);
      expect(container.querySelector('[data-testid="applied-promotions"]').textContent)
        .toContain(`${p.title}-${p.discount.toFixed(2)} ₺`);
    });
    expect(container.textContent).toContain(`${final.toFixed(2)} TL`);
    expect(container.textContent).not.toContain('-682.20');
    if (listPrice > total) expect(container.textContent).toContain('Ürün fiyat indirimi-210.00 TL');
  });
});
