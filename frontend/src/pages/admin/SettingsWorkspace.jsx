import { ExternalLink, Settings } from "lucide-react";
import { useLocation, useNavigate } from "react-router-dom";

import AdminSettings from "./Settings";
import BusinessRules from "./BusinessRules";
import CustomTheme from "./CustomTheme";
import MenuSettings from "./MenuSettings";
import EInvoiceSettings from "./EInvoiceSettings";
import CargoSettings from "./CargoSettings";
import SenderAddress from "./SenderAddress";
import NotificationSettings from "./NotificationSettings";
import EmailSettings from "./EmailSettings";
import NotificationTemplates from "./NotificationTemplates";
import MarketingPixels from "./MarketingPixels";
import CapiLogs from "./CapiLogs";
import SocialAuthSettings from "./SocialAuthSettings";
import OrderStatusSettings from "./OrderStatusSettings";
import AdminUsersRoles from "./UsersRoles";
import Vendors from "./Vendors";
import { CurrencyRates } from "./CatalogExtras";
import {
  SETTINGS_TAB_GROUPS,
  normalizeSettingsTab,
  settingsTabPath,
} from "../../lib/settingsWorkspaceTabs";

const TAB_COMPONENTS = {
  general: AdminSettings,
  "business-rules": BusinessRules,
  "order-statuses": OrderStatusSettings,
  einvoice: EInvoiceSettings,
  currency: CurrencyRates,
  cargo: CargoSettings,
  "sender-address": SenderAddress,
  notifications: NotificationSettings,
  "notification-templates": NotificationTemplates,
  email: EmailSettings,
  "social-login": SocialAuthSettings,
  "custom-theme": CustomTheme,
  "menu-layout": MenuSettings,
  pixels: MarketingPixels,
  capi: CapiLogs,
  "users-roles": AdminUsersRoles,
  vendors: Vendors,
};

export default function SettingsWorkspace() {
  const location = useLocation();
  const navigate = useNavigate();
  const activeKey = normalizeSettingsTab(location.search);
  const ActiveComponent = TAB_COMPONENTS[activeKey] || AdminSettings;

  return (
    <div className="min-h-full bg-gray-50" data-testid="settings-workspace">
      <div className="border-b border-gray-200 bg-white px-4 py-4 sm:px-6">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <div className="flex items-center gap-2">
              <Settings size={22} className="text-gray-700" />
              <h1 className="text-2xl font-semibold text-gray-900">Genel Ayarlar</h1>
            </div>
            <p className="mt-1 text-sm text-gray-500">
              Mağaza, operasyon, iletişim ve erişim ayarlarını tek yerden yönetin.
            </p>
          </div>
          <a
            href="https://mail.zoho.eu"
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-2 rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm font-medium text-gray-700 hover:bg-gray-50"
          >
            Webmail’e Git <ExternalLink size={15} />
          </a>
        </div>
      </div>

      <div className="grid gap-5 p-4 lg:grid-cols-[250px_minmax(0,1fr)] lg:p-6">
        <aside className="h-fit rounded-xl border border-gray-200 bg-white p-3 lg:sticky lg:top-4">
          <label htmlFor="settings-tab-select" className="mb-1 block text-xs font-semibold uppercase tracking-wide text-gray-500 lg:hidden">
            Ayar bölümü
          </label>
          <select
            id="settings-tab-select"
            value={activeKey}
            onChange={(event) => navigate(settingsTabPath(event.target.value))}
            className="mb-2 w-full rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm lg:hidden"
          >
            {SETTINGS_TAB_GROUPS.map((group) => (
              <optgroup key={group.label} label={group.label}>
                {group.tabs.map((tab) => <option key={tab.key} value={tab.key}>{tab.label}</option>)}
              </optgroup>
            ))}
          </select>

          <nav aria-label="Ayar bölümleri" className="hidden space-y-4 lg:block">
            {SETTINGS_TAB_GROUPS.map((group) => (
              <div key={group.label}>
                <p className="mb-1 px-2 text-[11px] font-semibold uppercase tracking-wider text-gray-400">{group.label}</p>
                <div className="space-y-0.5">
                  {group.tabs.map((tab) => {
                    const active = tab.key === activeKey;
                    return (
                      <button
                        key={tab.key}
                        type="button"
                        aria-current={active ? "page" : undefined}
                        onClick={() => navigate(settingsTabPath(tab.key))}
                        className={`w-full rounded-lg px-2.5 py-2 text-left text-sm transition-colors ${
                          active ? "bg-gray-900 font-medium text-white" : "text-gray-700 hover:bg-gray-100"
                        }`}
                      >
                        {tab.label}
                        {tab.description && (
                          <span className={`mt-0.5 block text-xs ${active ? "text-gray-300" : "text-gray-400"}`}>
                            {tab.description}
                          </span>
                        )}
                      </button>
                    );
                  })}
                </div>
              </div>
            ))}
          </nav>
        </aside>

        <main className="min-w-0 rounded-xl border border-gray-200 bg-white" data-settings-tab={activeKey}>
          {/* Yalnız aktif sayfayı mount etmek, mevcut sayfaların API/RBAC davranışını aynen korur. */}
          <ActiveComponent key={activeKey} />
        </main>
      </div>
    </div>
  );
}
