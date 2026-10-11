import { stockActorDetails, stockSyncDetails, stockSyncLabel } from "./stockHistoryView";

describe("product stock history labels", () => {
  test("distinguishes legacy, local and marketplace results", () => {
    expect(stockSyncLabel({ status: "not_recorded" })).toBe("Geçmişte kaydedilmedi");
    expect(stockSyncLabel({ status: "not_applicable" })).toBe("Yerel işlem");
    expect(stockSyncLabel({ status: "submitted" })).toBe("Gönderildi");
    expect(stockSyncLabel({ status: "failed" })).toBe("Başarısız");
  });

  test("shows non-secret correlation and sync context", () => {
    expect(stockActorDetails({
      actor: { login_method: "google" },
      context: { session_hash: "123456789abc", ip: "127.0.0.1" },
    })).toBe("google · 123456789abc · 127.0.0.1");
    expect(stockSyncDetails({ platform: "trendyol", batch_id: "b-1", message: "accepted" }))
      .toBe("trendyol · b-1 · accepted");
  });
});
