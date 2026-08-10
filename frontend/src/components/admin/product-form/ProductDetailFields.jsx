import React, { useState, useMemo } from "react";
import { Input } from "../../ui/input";
import { Textarea } from "../../ui/textarea";
import { Switch } from "../../ui/switch";
import { Label } from "../../ui/label";
import { Badge } from "../../ui/badge";
import { ChevronDown, ChevronRight } from "lucide-react";

/**
 * Ürün kartı içindeki mevcut sekmelere gömülen, gruplu ek alan editörü.
 * `groupLabels` ile yalnızca ilgili gruplar render edilir; değerler
 * formData.ticimax_fields üzerinden okunur/yazılır (kullanıcıya görünür
 * herhangi bir "Ticimax" etiketi yoktur).
 */
export default function ProductDetailFields({ schema = [], groupLabels = [], values = {}, onChange }) {
  const groups = useMemo(
    () => schema.filter((g) => groupLabels.includes(g.label)),
    [schema, groupLabels]
  );
  const [open, setOpen] = useState(() => ({ 0: true }));

  if (!groups.length) return null;
  const set = (key, val) => onChange && onChange(key, val);

  return (
    <div className="space-y-3" data-testid="product-detail-fields">
      {groups.map((group, gi) => {
        const isOpen = !!open[gi];
        const filled = group.fields.filter((f) => {
          const v = values?.[f.key];
          return v !== "" && v !== null && v !== undefined && v !== 0 && v !== "0";
        }).length;
        return (
          <div key={group.label} className="border border-gray-200 rounded-xl overflow-hidden bg-white">
            <button
              type="button"
              data-testid={`detail-group-${group.label}`}
              onClick={() => setOpen((p) => ({ ...p, [gi]: !p[gi] }))}
              className="w-full flex items-center justify-between px-4 py-3 bg-gray-50 hover:bg-gray-100 transition-colors"
            >
              <span className="font-semibold text-xs uppercase tracking-widest text-gray-700">{group.label}</span>
              <div className="flex items-center gap-2">
                {filled > 0 && <Badge variant="secondary" className="text-[10px]">{filled} dolu</Badge>}
                <Badge variant="outline" className="text-[10px]">{group.fields.length}</Badge>
                {isOpen ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
              </div>
            </button>
            {isOpen && (
              <div className="p-4 grid grid-cols-1 md:grid-cols-2 gap-4">
                {group.fields.map((f) => {
                  const val = values?.[f.key];
                  const testid = `detail-field-${f.key}`;
                  if (f.type === "bool") {
                    return (
                      <div key={f.key} className="flex items-center justify-between border rounded-lg px-3 py-2">
                        <Label className="text-xs text-gray-700">{f.label}</Label>
                        <Switch
                          data-testid={testid}
                          checked={String(val) === "1" || val === 1 || val === true}
                          onCheckedChange={(c) => set(f.key, c ? 1 : 0)}
                        />
                      </div>
                    );
                  }
                  if (f.type === "textarea") {
                    return (
                      <div key={f.key} className="md:col-span-2 space-y-1">
                        <Label className="text-xs text-gray-700">{f.label}</Label>
                        <Textarea
                          data-testid={testid}
                          value={val ?? ""}
                          disabled={f.readonly}
                          rows={3}
                          onChange={(e) => set(f.key, e.target.value)}
                        />
                      </div>
                    );
                  }
                  return (
                    <div key={f.key} className="space-y-1">
                      <Label className="text-xs text-gray-700">{f.label}</Label>
                      <Input
                        data-testid={testid}
                        type={f.type === "number" ? "number" : "text"}
                        value={val ?? ""}
                        disabled={f.readonly}
                        onChange={(e) =>
                          set(f.key, f.type === "number" && e.target.value !== "" ? Number(e.target.value) : e.target.value)
                        }
                      />
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
