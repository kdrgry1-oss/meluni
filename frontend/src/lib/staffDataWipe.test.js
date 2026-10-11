import {
  wipeLocalStaffData,
  isStaffToken,
  isSessionRevokedError,
  authCodeOf,
  KEEP_LOCAL_KEYS,
} from "./staffDataWipe";

const b64url = (o) => btoa(JSON.stringify(o)).replace(/=+$/, "").replace(/\+/g, "-").replace(/\//g, "_");
const jwtOf = (payload) => `${b64url({ alg: "HS256", typ: "JWT" })}.${b64url(payload)}.imza`;

function fakeEnv() {
  const cacheNames = ["workbox-precache", "api-cache"];
  const idbNames = ["meluni-offline", "localforage"];
  const prefs = { token: "x", device_id: "ios-1", cached_orders: "[]" };
  const regs = [{ unregister: jest.fn().mockResolvedValue(true) }];
  return {
    localStorage: window.localStorage,
    sessionStorage: window.sessionStorage,
    caches: {
      keys: jest.fn().mockResolvedValue(cacheNames.slice()),
      delete: jest.fn().mockResolvedValue(true),
    },
    indexedDB: {
      databases: jest.fn().mockResolvedValue(idbNames.map((name) => ({ name }))),
      deleteDatabase: jest.fn(),
    },
    serviceWorker: { getRegistrations: jest.fn().mockResolvedValue(regs) },
    preferences: {
      keys: jest.fn(async () => ({ keys: Object.keys(prefs) })),
      remove: jest.fn(async ({ key }) => { delete prefs[key]; }),
      _store: prefs,
    },
    _regs: regs,
  };
}

beforeEach(() => {
  window.localStorage.clear();
  window.sessionStorage.clear();
});

describe("wipeLocalStaffData (ADM-15)", () => {
  test("cihazdaki tüm personel verisini siler, yalnız çerez izni ve device_id kalır", async () => {
    window.localStorage.setItem("token", jwtOf({ typ: "staff" }));
    window.localStorage.setItem("menuOrder:u1", "[]");
    window.localStorage.setItem("admin_orders_view", "{\"page\":3}");
    window.localStorage.setItem("meluni_last_address", "{\"city\":\"X\"}");
    window.localStorage.setItem(KEEP_LOCAL_KEYS[0], "{\"analytics\":true}");
    window.sessionStorage.setItem("report_assistant_msgs", "[\"ciro\"]");
    const env = fakeEnv();

    const report = await wipeLocalStaffData({ env });

    expect(window.localStorage.length).toBe(1);
    expect(window.localStorage.getItem(KEEP_LOCAL_KEYS[0])).not.toBeNull();
    expect(window.localStorage.getItem("token")).toBeNull();
    expect(window.sessionStorage.length).toBe(0);
    expect(env.caches.delete).toHaveBeenCalledWith("workbox-precache");
    expect(env.caches.delete).toHaveBeenCalledWith("api-cache");
    expect(env.indexedDB.deleteDatabase).toHaveBeenCalledWith("meluni-offline");
    expect(env.indexedDB.deleteDatabase).toHaveBeenCalledWith("localforage");
    expect(env._regs[0].unregister).toHaveBeenCalled();
    expect(Object.keys(env.preferences._store)).toEqual(["device_id"]);
    expect(report).toEqual({ local: 4, session: 1, caches: 2, idb: 2, sw: 1, prefs: 2 });
  });

  test("token ve oturum verisi ilk await'ten ÖNCE (senkron) silinir", () => {
    window.localStorage.setItem("token", "t");
    window.sessionStorage.setItem("k", "v");
    const p = wipeLocalStaffData({ env: fakeEnv() });
    expect(window.localStorage.getItem("token")).toBeNull();
    expect(window.sessionStorage.length).toBe(0);
    return p;
  });

  test("depolama hatası ya da asılı kalan önbellek API'si çağıranı kilitlemez", async () => {
    const broken = {
      localStorage: { get length() { throw new Error("SecurityError"); } },
      sessionStorage: { get length() { throw new Error("SecurityError"); }, clear() { throw new Error("x"); } },
      caches: { keys: () => new Promise(() => {}) },
      indexedDB: { databases: () => Promise.reject(new Error("unsupported")) },
      serviceWorker: null,
      preferences: { keys: () => Promise.reject(new Error("bridge down")) },
    };
    const report = await wipeLocalStaffData({ env: broken, timeoutMs: 20 });
    expect(report.local).toBe(0);
    expect(report.caches).toBe(0);
  });

  test("API'si olmayan ortamda (eski tarayıcı / web) sorunsuz çalışır", async () => {
    window.localStorage.setItem("token", "t");
    const report = await wipeLocalStaffData({
      env: { localStorage: window.localStorage, sessionStorage: window.sessionStorage },
    });
    expect(report.local).toBe(1);
    expect(window.localStorage.getItem("token")).toBeNull();
  });
});

describe("oturum iptal kodu ve personel token tespiti", () => {
  test("session_revoked başlığı düz nesne ya da AxiosHeaders biçiminde okunur", () => {
    const plain = { response: { status: 401, headers: { "x-auth-code": "session_revoked" } } };
    const axiosHeaders = { response: { status: 401, headers: { get: (k) => (k === "x-auth-code" ? "SESSION_REVOKED" : undefined) } } };
    expect(isSessionRevokedError(plain)).toBe(true);
    expect(isSessionRevokedError(axiosHeaders)).toBe(true);
    expect(authCodeOf({ response: { headers: {} } })).toBe("");
  });

  test("başka kod / başka statü / başlıksız yanıt iptal sayılmaz", () => {
    expect(isSessionRevokedError({ response: { status: 401, headers: { "x-auth-code": "session_idle" } } })).toBe(false);
    expect(isSessionRevokedError({ response: { status: 403, headers: { "x-auth-code": "session_revoked" } } })).toBe(false);
    expect(isSessionRevokedError({ response: { status: 401, headers: {} } })).toBe(false);
    expect(isSessionRevokedError(new Error("Network Error"))).toBe(false);
    expect(isSessionRevokedError(null)).toBe(false);
  });

  test("personel token'ı typ=staff ya da is_admin talebinden tanınır", () => {
    expect(isStaffToken(jwtOf({ typ: "staff", user_id: "u1" }))).toBe(true);
    expect(isStaffToken(jwtOf({ is_admin: true, user_id: "eski" }))).toBe(true);
    expect(isStaffToken(jwtOf({ typ: "customer", is_admin: false }))).toBe(false);
    expect(isStaffToken("bozuk")).toBe(false);
    expect(isStaffToken("a.%%%.b")).toBe(false);
    expect(isStaffToken(null)).toBe(false);
  });
});
