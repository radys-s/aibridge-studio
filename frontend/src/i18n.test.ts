import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { _setLangForTests, t, tn } from "./i18n";
import { cs } from "./i18n/cs";

const SRC = join(__dirname);
const STR = String.raw`"((?:[^"\\]|\\.)*)"`;

function sources(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) return name === "i18n" ? [] : sources(path);
    return /\.tsx?$/.test(name) && !name.endsWith(".test.ts") ? [path] : [];
  });
}

/** Every message id passed to t() / tn() as a literal. */
function messageIds(): { id: string; plural: boolean }[] {
  const ids: { id: string; plural: boolean }[] = [];
  for (const file of sources(SRC)) {
    const text = readFileSync(file, "utf8");
    for (const m of text.matchAll(new RegExp(String.raw`\bt\(\s*` + STR, "g"))) ids.push({ id: JSON.parse(`"${m[1]}"`), plural: false });
    for (const m of text.matchAll(new RegExp(String.raw`\btn\(\s*[^,]+,\s*` + STR + String.raw`,\s*` + STR, "g")))
      ids.push({ id: JSON.parse(`"${m[2]}"`), plural: true });
  }
  return ids;
}

describe("i18n", () => {
  afterEach(() => _setLangForTests("en"));

  it("has a Czech text for every message in the source", () => {
    const ids = messageIds();
    expect(ids.length).toBeGreaterThan(300);
    const missing = ids.filter((m) => !(m.id in cs)).map((m) => m.id);
    expect(missing).toEqual([]);
    const badPlurals = ids.filter((m) => m.plural && cs[m.id]?.split("|").length !== 3).map((m) => m.id);
    expect(badPlurals).toEqual([]);
  });

  it("keeps placeholders in the Czech text", () => {
    const names = (s: string) => [...s.matchAll(/\{(\w+)\}/g)].map((m) => m[1]).sort();
    for (const [en, czech] of Object.entries(cs)) {
      for (const form of czech.split("|")) expect(names(form), en).toEqual(names(en));
    }
  });

  it("translates and fills placeholders", () => {
    expect(t("{n} days", { n: 7 })).toBe("7 days");
    _setLangForTests("cs");
    expect(t("{n} days", { n: 7 })).toBe("7 dní");
    expect(t("not in the dictionary")).toBe("not in the dictionary");
  });

  it("picks the Czech plural form", () => {
    expect(tn(1, "{n} file", "{n} files")).toBe("1 file");
    expect(tn(3, "{n} file", "{n} files")).toBe("3 files");
    _setLangForTests("cs");
    expect([1, 3, 5, 0].map((n) => tn(n, "{n} file", "{n} files"))).toEqual(["1 soubor", "3 soubory", "5 souborů", "0 souborů"]);
  });
});
