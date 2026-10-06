import { describe, expect, it } from "vitest";
import { parseUnifiedDiff } from "./diff";

const SAMPLE = [
  "diff --git a/src/app.py b/src/app.py",
  "index 1111111..2222222 100644",
  "--- a/src/app.py",
  "+++ b/src/app.py",
  "@@ -1,3 +1,4 @@",
  " import os",
  "-x = 1",
  "+x = 2",
  "+y = 3",
  " print(x)",
  "diff --git a/tests/test_new.py b/tests/test_new.py",
  "new file mode 100644",
  "index 0000000..3333333",
  "--- /dev/null",
  "+++ b/tests/test_new.py",
  "@@ -0,0 +1,2 @@",
  "+def test_x():",
  "+    assert True",
  "\\ No newline at end of file",
  "diff --git a/old.sql b/old.sql",
  "deleted file mode 100644",
  "--- a/old.sql",
  "+++ /dev/null",
  "@@ -1,2 +0,0 @@",
  "--- a SQL comment",
  "-SELECT 1;",
  "",
].join("\n");

describe("parseUnifiedDiff", () => {
  it("returns empty list for empty input", () => {
    expect(parseUnifiedDiff("")).toEqual([]);
  });

  it("splits files and counts additions/deletions", () => {
    const files = parseUnifiedDiff(SAMPLE);
    expect(files.map((f) => f.path)).toEqual(["src/app.py", "tests/test_new.py", "old.sql"]);
    expect(files.map((f) => f.status)).toEqual(["modified", "added", "deleted"]);
    expect(files[0]).toMatchObject({ additions: 2, deletions: 1 });
    expect(files[1]).toMatchObject({ additions: 2, deletions: 0 });
    // "--- a SQL comment" inside a hunk is a deleted line, not a new file header.
    expect(files[2]).toMatchObject({ additions: 0, deletions: 2 });
  });

  it("tracks line numbers and line types", () => {
    const [first, second] = parseUnifiedDiff(SAMPLE);
    const lines = first.hunks[0].lines;
    expect(lines.map((l) => l.type)).toEqual(["ctx", "del", "add", "add", "ctx"]);
    expect(lines[1]).toMatchObject({ text: "x = 1", oldNo: 2 });
    expect(lines[2]).toMatchObject({ text: "x = 2", newNo: 2 });
    expect(lines[4]).toMatchObject({ oldNo: 3, newNo: 4 });
    expect(second.hunks[0].lines.at(-1)?.type).toBe("meta");
  });
});
