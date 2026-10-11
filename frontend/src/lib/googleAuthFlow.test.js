import {
  consumeOAuthState,
  createGooglePopupFlow,
  createOAuthState,
  googleErrorMessage,
  isEmbeddedWebView,
  safeInternalPath,
  safeReturnPath,
} from "./googleAuthFlow";

describe("Google auth flow", () => {
  test("accepts only local return paths", () => {
    expect(safeReturnPath("/urun/1?x=1")).toBe("/urun/1?x=1");
    expect(safeReturnPath("https://evil.example")).toBe("/hesabim");
    expect(safeReturnPath("//evil.example")).toBe("/hesabim");
  });

  test("rejects browser-normalised external targets (backslash / control chars)", () => {
    // Tarayıcı "\" → "/", TAB/CR/LF yutulur: hepsi fiilen "//evil.example" olurdu.
    for (const v of ["/\\evil.example", "/\\/evil.example", "/\t/evil.example", "/\n/evil.example",
      "/\r/evil.example", "/admin/../\\evil.example", "\\\\evil.example", "/x\u0000y", "", null, 42]) {
      expect(safeReturnPath(v)).toBe("/hesabim");
    }
    expect(safeReturnPath("/sepet#odeme")).toBe("/sepet#odeme");
    expect(safeReturnPath("/kategori/%C3%A7anta")).toBe("/kategori/%C3%A7anta");
  });

  test("safeInternalPath enforces a canonical path prefix", () => {
    expect(safeInternalPath("/admin/orders?x=1", "/admin", "/admin")).toBe("/admin/orders?x=1");
    expect(safeInternalPath("/admin", "/admin", "/admin")).toBe("/admin");
    expect(safeInternalPath("/admin/../hesabim", "/admin", "/admin")).toBe("/admin");
    expect(safeInternalPath("/administrator", "/admin", "/admin")).toBe("/admin");
    expect(safeInternalPath("/admin/../\\evil.example", "/admin", "/admin")).toBe("/admin");
    // ?next=/admin/..%2F%09/evil.example → URLSearchParams çözünce "/admin/../<TAB>/evil.example"
    expect(safeInternalPath("/admin/../\t/evil.example", "/admin", "/admin")).toBe("/admin");
  });

  test("exchanges popup credential and completes once", async () => {
    const exchange = jest.fn().mockResolvedValue({ token: "app-token", user: { id: "u1" } });
    const success = jest.fn();
    const error = jest.fn();
    const flow = createGooglePopupFlow({ exchangeCredential: exchange, onSuccess: success, onError: error });
    flow.begin();
    await flow.credential({ credential: "google-test-double" });
    await flow.credential({ credential: "duplicate" });
    expect(exchange).toHaveBeenCalledTimes(1);
    expect(success).toHaveBeenCalledWith({ token: "app-token", user: { id: "u1" } });
    expect(error).not.toHaveBeenCalled();
  });

  test("surfaces popup errors without exchanging a credential", () => {
    const exchange = jest.fn();
    const error = jest.fn();
    const flow = createGooglePopupFlow({ exchangeCredential: exchange, onSuccess: jest.fn(), onError: error });
    flow.begin();
    flow.popupError({ type: "popup_failed_to_open" });
    expect(exchange).not.toHaveBeenCalled();
    expect(error).toHaveBeenCalledWith("popup_failed_to_open", expect.anything());
  });

  test("times out a popup that never calls back", () => {
    jest.useFakeTimers();
    const error = jest.fn();
    const flow = createGooglePopupFlow({ exchangeCredential: jest.fn(), onSuccess: jest.fn(), onError: error, timeoutMs: 50 });
    flow.begin();
    jest.advanceTimersByTime(50);
    expect(error).toHaveBeenCalledWith("timeout", undefined);
    jest.useRealTimers();
  });

  test("detects embedded webviews and maps callback errors", () => {
    expect(isEmbeddedWebView({ navigator: { userAgent: "Instagram 300" } })).toBe(true);
    expect(isEmbeddedWebView({ navigator: { userAgent: "Chrome" } })).toBe(false);
    expect(googleErrorMessage("csrf")).toMatch(/güvenlik/);
  });

  test("OAuth state is random, tab-bound and single-use (login CSRF)", () => {
    // jsdom 16 (CRA jest) window.crypto sağlamaz → Node'un rastgele üreteciyle doldur.
    if (!window.crypto || typeof window.crypto.getRandomValues !== "function") {
      const nodeCrypto = require("crypto");
      Object.defineProperty(window, "crypto", {
        configurable: true,
        value: { getRandomValues: (arr) => nodeCrypto.randomFillSync(arr) },
      });
    }
    const mem = {};
    const storage = {
      getItem: (k) => (k in mem ? mem[k] : null),
      setItem: (k, v) => { mem[k] = String(v); },
      removeItem: (k) => { delete mem[k]; },
    };
    const a = createOAuthState("facebook", storage, "fb_oauth_state");
    expect(a).toMatch(/^facebook\.[0-9a-f]{32}$/);
    // saldırganın sabit/eski değeri kabul edilmez
    expect(consumeOAuthState("facebook", storage, "fb_oauth_state")).toBe(false);
    const b = createOAuthState("facebook", storage, "fb_oauth_state");
    expect(b).not.toBe(a);
    expect(consumeOAuthState(b, storage, "fb_oauth_state")).toBe(true);
    // tek kullanımlık
    expect(consumeOAuthState(b, storage, "fb_oauth_state")).toBe(false);
    // bu sekmede başlatılmamış giriş
    expect(consumeOAuthState("facebook.00", storage, "fb_oauth_state")).toBe(false);
  });
});
