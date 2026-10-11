export const AMAZON_ADMIN_TABS = new Set(["connection", "mapping", "compliance"]);

const TAB_ALIASES = {
  spapi: "connection",
  settings: "connection",
  aktarim: "mapping",
  eslestirme: "mapping",
  dpp: "compliance",
  uyum: "compliance",
};

export function normalizeAmazonAdminTab(search = "", hash = "") {
  const queryTab = new URLSearchParams(search).get("tab") || "";
  const hashTab = String(hash || "").replace(/^#/, "").split(/[?&]/)[0];
  const requested = String(queryTab || hashTab || "connection").toLocaleLowerCase("tr");
  const normalized = TAB_ALIASES[requested] || requested;
  return AMAZON_ADMIN_TABS.has(normalized) ? normalized : "connection";
}

export function amazonAdminTabPath(tab) {
  const normalized = normalizeAmazonAdminTab(`?tab=${tab || ""}`);
  return `/admin/amazon?tab=${normalized}`;
}
