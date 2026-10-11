import { amazonAdminTabPath, normalizeAmazonAdminTab } from "./amazonAdminTabs";

describe("Amazon admin tab routing", () => {
  test("accepts canonical query tabs", () => {
    expect(normalizeAmazonAdminTab("?tab=mapping")).toBe("mapping");
    expect(normalizeAmazonAdminTab("?tab=compliance")).toBe("compliance");
  });

  test("keeps legacy hash and Turkish aliases compatible", () => {
    expect(normalizeAmazonAdminTab("", "#dpp")).toBe("compliance");
    expect(normalizeAmazonAdminTab("?tab=eslestirme")).toBe("mapping");
    expect(normalizeAmazonAdminTab("", "#spapi")).toBe("connection");
  });

  test("falls back safely and builds canonical paths", () => {
    expect(normalizeAmazonAdminTab("?tab=unknown")).toBe("connection");
    expect(amazonAdminTabPath("uyum")).toBe("/admin/amazon?tab=compliance");
  });
});
