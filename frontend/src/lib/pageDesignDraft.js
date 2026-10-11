const clone = (value) => JSON.parse(JSON.stringify(value));

export function normalizeBlockOrder(blocks = []) {
  return blocks.map((block, index) => ({ ...block, sort_order: index + 1 }));
}

export function createDraftBlock(type, index = 0) {
  const suffix = `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
  return {
    id: `draft-${suffix}`,
    type,
    title: "",
    images: [],
    links: [],
    settings: type === "rotating_text" ? { texts: [""] } : {},
    sort_order: index + 1,
    is_active: true,
    show_desktop: true,
    show_mobile: true,
    page: "home",
  };
}

export function duplicateDraftBlock(block, index = 0) {
  const copy = createDraftBlock(block.type, index);
  return {
    ...clone(block),
    id: copy.id,
    title: block.title ? `${block.title} (Kopya)` : "Kopya blok",
    sort_order: index + 1,
  };
}

export function buildPageDesignSavePlan(savedBlocks = [], draftBlocks = []) {
  const savedIds = new Set(savedBlocks.map((block) => block.id));
  const draftIds = new Set(draftBlocks.filter((block) => !String(block.id).startsWith("draft-")).map((block) => block.id));
  return {
    deletedIds: savedBlocks.map((block) => block.id).filter((id) => !draftIds.has(id)),
    creates: draftBlocks.filter((block) => String(block.id).startsWith("draft-")),
    updates: draftBlocks.filter((block) => savedIds.has(block.id)),
  };
}

export function samePageDesign(a = [], b = []) {
  return JSON.stringify(a) === JSON.stringify(b);
}

export function clonePageDesign(value) {
  return clone(value);
}
