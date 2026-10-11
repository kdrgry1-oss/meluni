import {
  buildPageDesignSavePlan,
  createDraftBlock,
  duplicateDraftBlock,
  normalizeBlockOrder,
  samePageDesign,
} from "./pageDesignDraft";

describe("page design local draft", () => {
  test("normalizes ordering without mutating source", () => {
    const source = [{ id: "a", sort_order: 9 }, { id: "b", sort_order: 4 }];
    const result = normalizeBlockOrder(source);
    expect(result.map((item) => item.sort_order)).toEqual([1, 2]);
    expect(source[0].sort_order).toBe(9);
  });

  test("new and duplicated blocks use temporary ids", () => {
    const created = createDraftBlock("hero_slider", 2);
    const duplicate = duplicateDraftBlock({ ...created, title: "Hero" }, 3);
    expect(created.id).toMatch(/^draft-/);
    expect(duplicate.id).toMatch(/^draft-/);
    expect(duplicate.id).not.toBe(created.id);
    expect(duplicate.title).toBe("Hero (Kopya)");
  });

  test("builds a single-save create/update/delete plan", () => {
    const saved = [{ id: "a" }, { id: "b" }];
    const draft = [{ id: "a" }, createDraftBlock("text_block")];
    const plan = buildPageDesignSavePlan(saved, draft);
    expect(plan.deletedIds).toEqual(["b"]);
    expect(plan.updates.map((item) => item.id)).toEqual(["a"]);
    expect(plan.creates).toHaveLength(1);
    expect(samePageDesign(saved, saved)).toBe(true);
  });
});
