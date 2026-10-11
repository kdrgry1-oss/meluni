import { act } from "react";
import { createRoot } from "react-dom/client";
import { AuthProvider, useAuth } from "./AuthContext";
import axios from "axios";

jest.mock("axios", () => ({ get: jest.fn(), post: jest.fn(), defaults: { headers: { common: {} } } }));

describe("session privacy", () => {
  let root, container, auth;
  const Probe = () => { auth = useAuth(); return <span>{auth.user?.email || "guest"}</span>; };
  beforeAll(() => { global.IS_REACT_ACT_ENVIRONMENT = true; });
  afterAll(() => { delete global.IS_REACT_ACT_ENVIRONMENT; });
  beforeEach(() => {
    localStorage.clear();
    container = document.createElement("div");
    root = createRoot(container);
  });
  afterEach(async () => {
    await act(async () => root.unmount());
    jest.clearAllMocks();
    localStorage.clear();
  });
  test("logout removes unscoped address, bearer token and user", async () => {
    localStorage.setItem("token", "test-token");
    localStorage.setItem("meluni_last_address", '{"address":"private test address"}');
    axios.get.mockResolvedValue({ data: { id: "a", email: "a@example.test" } });
    await act(async () => root.render(<AuthProvider><Probe /></AuthProvider>));
    expect(container.textContent).toBe("a@example.test");
    await act(async () => auth.logout());
    expect(container.textContent).toBe("guest");
    expect(localStorage.getItem("meluni_last_address")).toBeNull();
    expect(localStorage.getItem("token")).toBeNull();
    expect(axios.defaults.headers.common.Authorization).toBeUndefined();
  });
  test("a late user response cannot restore the previous session after logout", async () => {
    let resolve;
    localStorage.setItem("token", "old-token");
    axios.get.mockImplementation(() => new Promise((r) => { resolve = r; }));
    await act(async () => root.render(<AuthProvider><Probe /></AuthProvider>));
    await act(async () => auth.logout());
    await act(async () => resolve({ data: { id: "old", email: "old@example.test" } }));
    expect(container.textContent).toBe("guest");
    expect(auth.loading).toBe(false);
  });
});
