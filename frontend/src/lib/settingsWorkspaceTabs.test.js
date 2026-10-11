import {
  LEGACY_SETTINGS_TABS,
  SETTINGS_TAB_GROUPS,
  legacySettingsRedirect,
  normalizeSettingsTab,
  settingsTabPath,
} from "./settingsWorkspaceTabs";

describe("settings workspace routing", () => {
  test("accepts known tabs and safely falls back", () => {
    expect(normalizeSettingsTab("?tab=cargo")).toBe("cargo");
    expect(normalizeSettingsTab("?tab=unknown")).toBe("general");
    expect(normalizeSettingsTab()).toBe("general");
  });

  test("keeps every legacy settings URL mapped to a visible tab", () => {
    const visible = new Set(SETTINGS_TAB_GROUPS.flatMap((group) => group.tabs.map((tab) => tab.key)));
    expect(Object.values(LEGACY_SETTINGS_TABS).every((tab) => visible.has(tab))).toBe(true);
    expect(legacySettingsRedirect("/admin/ayarlar/kargo")).toBe("/admin/ayarlar?tab=cargo");
    expect(legacySettingsRedirect("/admin/kullanicilar")).toBe("/admin/ayarlar?tab=users-roles");
  });

  test("does not add a query string to the default tab", () => {
    expect(settingsTabPath("general")).toBe("/admin/ayarlar");
  });
});
