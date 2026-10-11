import { isMaskedTaxId, maskTaxId, editTaxIdValue } from "./taxId";

describe("taxId helpers", () => {
  test("detects masked values", () => {
    expect(isMaskedTaxId("*********12")).toBe(true);
    expect(isMaskedTaxId("12345678901")).toBe(false);
    expect(isMaskedTaxId("")).toBe(false);
  });
  test("masks plain values and keeps masked ones", () => {
    expect(maskTaxId("12345678901")).toBe("*********01");
    expect(maskTaxId("*********01")).toBe("*********01");
    expect(maskTaxId("")).toBe("");
  });
  test("editing a masked field keeps only newly typed digits", () => {
    expect(editTaxIdValue("*********01", "1234567890")).toBe("1234567890");
    expect(editTaxIdValue("*********01", "*********015")).toBe("5");
    expect(editTaxIdValue("*********01", "*********0")).toBe("");
    expect(editTaxIdValue("12", "12a3")).toBe("123");
  });
});
