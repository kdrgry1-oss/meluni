import { TRAINING_CONTENT_VERSION, TRAINING_UPDATES, mergeTrainingUpdates } from "./trainingUpdates";

describe("versioned training updates", () => {
  test("preserves custom content while inserting a missing system section", () => {
    const custom = [{ key: "custom", title: "Firma Notları", items: [{ title: "Özel", what: "Korunmalı" }] }];
    const result = mergeTrainingUpdates(custom);
    expect(result[0]).toEqual(custom[0]);
    expect(result.some((section) => section.key === "platform-rehberi-v2")).toBe(true);
    expect(custom).toHaveLength(1);
  });

  test("keeps an edited item with the same stable key and upserts only missing items", () => {
    const source = TRAINING_UPDATES[0];
    const custom = [{ ...source, items: [{ ...source.items[0], title: "Firma tarafından düzenlendi" }] }];
    const result = mergeTrainingUpdates(custom);
    const section = result.find((item) => item.key === source.key);
    expect(section.items[0].title).toBe("Firma tarafından düzenlendi");
    expect(section.items).toHaveLength(source.items.length);
    expect(mergeTrainingUpdates(result)).toEqual(result);
  });

  test("publishes an explicit content version", () => {
    expect(TRAINING_CONTENT_VERSION).toMatch(/^\d{4}\.\d{2}\.\d{2}\.\d+$/);
  });
});

