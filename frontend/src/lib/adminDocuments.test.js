import axios from "axios";
import { adminDocumentError, fetchAdminDocument, fetchAdminDocumentTexts, openAdminDocument } from "./adminDocuments";

jest.mock("axios", () => ({ get: jest.fn() }));

beforeEach(() => { localStorage.setItem("token", "test-session"); });
afterEach(() => { jest.restoreAllMocks(); jest.clearAllMocks(); localStorage.clear(); jest.useRealTimers(); });

test("credentials travel in the header, never in the document URL", async () => {
  const blob = new Blob(["test invoice"], { type: "text/html" });
  axios.get.mockResolvedValue({ data: blob });
  expect(await fetchAdminDocument("/orders/test/invoice/print")).toBe(blob);
  const [url, options] = axios.get.mock.calls[0];
  expect(url).not.toContain("test-session");
  expect(url).not.toContain("token=");
  expect(options.headers.Authorization).toBe("Bearer test-session");
  expect(Object.keys(options.headers)).toEqual(["Authorization"]);
  expect(options.headers).not.toHaveProperty("Cache-Control");
  expect(options.responseType).toBe("blob");
});

test.each(["https://other.test/file", "//other.test/file", "/\\other.test", "/file?token=secret"])(
  "rejects unsafe path %s before sending credentials", async (path) => {
    await expect(fetchAdminDocument(path)).rejects.toThrow();
    expect(axios.get).not.toHaveBeenCalled();
  });

test("requires a current session", async () => {
  localStorage.removeItem("token");
  await expect(fetchAdminDocument("/orders/test/invoice/print")).rejects.toThrow();
  expect(axios.get).not.toHaveBeenCalled();
});

test("a blocked popup does not make a label-printing request", async () => {
  jest.spyOn(window, "open").mockReturnValue(null);
  await expect(openAdminDocument("/orders/test/cargo-label")).rejects.toThrow("engellendi");
  expect(axios.get).not.toHaveBeenCalled();
});

test("request failure closes the blank popup", async () => {
  const popup = { opener: {}, close: jest.fn() };
  jest.spyOn(window, "open").mockReturnValue(popup);
  axios.get.mockRejectedValue(new Error("Forbidden"));
  await expect(openAdminDocument("/orders/test/cargo-label")).rejects.toThrow("Forbidden");
  expect(popup.opener).toBeNull();
  expect(popup.close).toHaveBeenCalled();
});

test.each([
  [401, 'Token süresi dolmuş', 'yeniden giriş'],
  [403, 'Bu işlem için yetkiniz yok (orders.cargo)', 'orders.cargo'],
  [404, 'Sipariş bulunamadı', 'Sipariş bulunamadı'],
  [429, '', 'Çok fazla'],
  [500, 'private traceback', 'sunucu hatası'],
])('decodes blob error %s without hiding it as no barcode', async (status, detail, expected) => {
  axios.get.mockRejectedValue({response: {status, data: {text: async () => JSON.stringify({detail})}}});
  await expect(fetchAdminDocument('/orders/test/cargo-label')).rejects.toThrow(expected);
});

test('network failures and non-JSON errors have actionable messages', async () => {
  expect(await adminDocumentError({isAxiosError: true, request: {}})).toContain('erişilemedi');
  expect(await adminDocumentError({response: {status: 502, data: {text: async () => '<html>private proxy error</html>'}}}))
    .toBe('Belge hazırlanırken sunucu hatası oluştu (502).');
});

test('bulk labels preserve successful documents and report every failure', async () => {
  axios.get.mockImplementation(url => url.endsWith('/ok/cargo-label')
    ? Promise.resolve({data: {text: async () => '<html>label</html>'}})
    : Promise.reject({response: {status: 403, data: {detail: 'Yetkiniz yok (orders.cargo)'}}}));
  const r = await fetchAdminDocumentTexts(['ok', 'denied'], 'cargo-label');
  expect(r.htmls).toEqual(['<html>label</html>']);
  expect(r.failures).toEqual([{id: 'denied', error: 'Yetkiniz yok (orders.cargo)'}]);
});

test('empty documents are not silently counted as printable', async () => {
  axios.get.mockResolvedValue({data: {text: async () => '  '}});
  const r = await fetchAdminDocumentTexts(['empty'], 'cargo-label');
  expect(r.htmls).toEqual([]);
  expect(r.failures[0].error).toContain('boş belge');
});
