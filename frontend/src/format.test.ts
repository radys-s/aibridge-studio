import { describe, expect, it } from "vitest";
import { count, plural } from "./format";

describe("Czech plurals", () => {
  it("picks one / few / many like a native speaker", () => {
    const f = (n: number) => count(n, "záznam", "záznamy", "záznamů");
    expect([0, 1, 2, 4, 5, 11, 21, 22].map(f)).toEqual([
      "0 záznamů",
      "1 záznam",
      "2 záznamy",
      "4 záznamy",
      "5 záznamů",
      "11 záznamů",
      "21 záznamů",
      "22 záznamů",
    ]);
  });

  it("returns only the word when the number is shown elsewhere", () => {
    expect(plural(1, "úkol", "úkoly", "úkolů")).toBe("úkol");
    expect(plural(3, "úkol", "úkoly", "úkolů")).toBe("úkoly");
  });
});
