import {
  APP_UPDATE_EVENT,
  installAppVersionHeaders,
  installUpdateRequiredInterceptor,
  isAppUpdateRequiredError,
  isOurApiUrl,
  withTimeout,
} from "./appVersionGate";

function fakeAxios() {
  const req = [];
  const resp = [];
  return {
    interceptors: {
      request: { use: (fn) => req.push(fn) - 1, eject: jest.fn() },
      response: { use: (ok, err) => resp.push(err) - 1, eject: jest.fn() },
    },
    _req: req,
    _resp: resp,
  };
}

const API = "https://api.example.test/api";

describe("asgari sürüm başlıkları (ADM-15)", () => {
  test("yalnız kendi API'mize platform/sürüm/hedef başlığı eklenir", async () => {
    const ax = fakeAxios();
    installAppVersionHeaders(ax, { platform: "ios", appTarget: "admin", getVersion: async () => "1.4.0", apiBase: API });
    const ours = await ax._req[0]({ url: `${API}/admin/orders`, headers: {} });
    expect(ours.headers).toEqual({ "X-App-Platform": "ios", "X-App-Target": "admin", "X-App-Version": "1.4.0" });
    const third = await ax._req[0]({ url: "https://cdn.other.test/x.js", headers: {} });
    expect(third.headers).toEqual({});
  });

  test("sürüm okunamazsa X-App-Version gönderilmez (sunucu 'eski' sayar)", async () => {
    const ax = fakeAxios();
    installAppVersionHeaders(ax, { platform: "android", appTarget: "customer", getVersion: async () => "", apiBase: API });
    const cfg = await ax._req[0]({ url: "/api/auth/me", headers: {} });
    expect(cfg.headers["X-App-Platform"]).toBe("android");
    expect(cfg.headers["X-App-Version"]).toBeUndefined();
  });

  test("interceptor API'si olmayan axios örneğinde sessizce atlanır", () => {
    expect(() => installAppVersionHeaders({}, { platform: "ios" })).not.toThrow();
    expect(() => installUpdateRequiredInterceptor(undefined)).not.toThrow();
  });

  test("isOurApiUrl", () => {
    expect(isOurApiUrl(`${API}/x`, API)).toBe(true);
    expect(isOurApiUrl("/api/x", API)).toBe(true);
    expect(isOurApiUrl("https://evil.test/api/x", API)).toBe(false);
  });
});

describe("426 güncelleme gerekli olayı", () => {
  test("426 + app_update_required olay yayar; mağaza bağlantısı yalnız https", async () => {
    const ax = fakeAxios();
    const target = new EventTarget();
    const seen = [];
    target.addEventListener(APP_UPDATE_EVENT, (e) => seen.push(e.detail));
    installUpdateRequiredInterceptor(ax, target);
    const err = { response: { status: 426, data: { code: "app_update_required", min_version: "2.0.0", app: "admin", store_url: "javascript:alert(1)" } } };
    await expect(ax._resp[0](err)).rejects.toBe(err);
    expect(seen).toEqual([{ minVersion: "2.0.0", app: "admin", storeUrl: "" }]);
    const err2 = { response: { status: 426, data: { code: "app_update_required", app: "customer", store_url: "https://apps.example.test/app" } } };
    await expect(ax._resp[0](err2)).rejects.toBe(err2);
    expect(seen[1]).toEqual({ minVersion: "", app: "customer", storeUrl: "https://apps.example.test/app" });
  });

  test("426 dışı hatalar olay yaymaz", async () => {
    const ax = fakeAxios();
    const target = new EventTarget();
    const spy = jest.fn();
    target.addEventListener(APP_UPDATE_EVENT, spy);
    installUpdateRequiredInterceptor(ax, target);
    await expect(ax._resp[0]({ response: { status: 401, data: {} } })).rejects.toBeTruthy();
    await expect(ax._resp[0]({ response: { status: 426, data: { code: "other" } } })).rejects.toBeTruthy();
    expect(spy).not.toHaveBeenCalled();
    expect(isAppUpdateRequiredError({ response: { status: 426, data: {} } })).toBe(true);
  });

  test("withTimeout asılı kalan sürüm okumasında fallback döner", async () => {
    await expect(withTimeout(new Promise(() => {}), 10, "1.0.0")).resolves.toBe("1.0.0");
    await expect(withTimeout(Promise.resolve("2.0.0"), 10, "x")).resolves.toBe("2.0.0");
    await expect(withTimeout(Promise.reject(new Error("x")), 10, "fb")).resolves.toBe("fb");
  });
});
