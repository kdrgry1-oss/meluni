import { Cable, Link2, ShieldCheck, Store } from "lucide-react";
import { useLocation, useNavigate } from "react-router-dom";

import { amazonAdminTabPath, normalizeAmazonAdminTab } from "../../lib/amazonAdminTabs";
import AmazonSpApi from "./AmazonSpApi";
import CategoryMapping from "./CategoryMapping";
import Compliance from "./Compliance";

const TABS = [
  { key: "connection", label: "SP-API & Bağlantı", icon: Link2 },
  { key: "mapping", label: "Aktarım & Eşleştirme", icon: Cable },
  { key: "compliance", label: "DPP Uyum", icon: ShieldCheck },
];

export default function AmazonAdmin() {
  const location = useLocation();
  const navigate = useNavigate();
  const active = normalizeAmazonAdminTab(location.search, location.hash);

  return (
    <div className="p-6" data-testid="amazon-admin-page">
      <div className="max-w-7xl mx-auto">
        <div className="mb-5">
          <h1 className="text-2xl font-bold flex items-center gap-2">
            <Store className="text-orange-500" size={24} /> Amazon
          </h1>
          <p className="text-sm text-gray-500 mt-1">
            Bağlantı, ürün aktarımı/eşleştirme ve DPP güvenlik kontrollerini tek yerden yönetin.
          </p>
        </div>

        <div className="flex items-center gap-1 border-b border-gray-200 mb-5 overflow-x-auto" role="tablist">
          {TABS.map(({ key, label, icon: Icon }) => (
            <button
              key={key}
              type="button"
              role="tab"
              aria-selected={active === key}
              onClick={() => navigate(amazonAdminTabPath(key))}
              className={`inline-flex items-center gap-2 px-4 py-2.5 whitespace-nowrap border-b-2 text-sm font-semibold transition-colors ${
                active === key
                  ? "border-orange-500 text-orange-700"
                  : "border-transparent text-gray-500 hover:text-gray-900"
              }`}
              data-testid={`amazon-tab-${key}`}
            >
              <Icon size={15} /> {label}
            </button>
          ))}
        </div>

        <div role="tabpanel" data-testid={`amazon-panel-${active}`}>
          {active === "connection" && <AmazonSpApi embedded />}
          {active === "mapping" && <CategoryMapping lockedMarketplace="amazon-tr" embedded />}
          {active === "compliance" && <Compliance embedded />}
        </div>
      </div>
    </div>
  );
}
