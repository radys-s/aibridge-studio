// Minimal parser for unified git diff output.

export type DiffLineType = "add" | "del" | "ctx" | "meta";

export interface DiffLine {
  type: DiffLineType;
  text: string;
  oldNo?: number;
  newNo?: number;
}

export interface DiffHunk {
  header: string;
  lines: DiffLine[];
}

export interface DiffFile {
  oldPath: string;
  newPath: string;
  /** Display path (new path unless the file was deleted). */
  path: string;
  status: "modified" | "added" | "deleted" | "renamed";
  binary: boolean;
  additions: number;
  deletions: number;
  hunks: DiffHunk[];
}

const HUNK_RE = /^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/;

function stripPrefix(p: string): string {
  const trimmed = p.trim().replace(/^"(.*)"$/, "$1");
  if (trimmed === "/dev/null") return trimmed;
  return trimmed.replace(/^[ab]\//, "");
}

export function parseUnifiedDiff(diff: string): DiffFile[] {
  const files: DiffFile[] = [];
  if (!diff) return files;
  const lines = diff.replace(/\r\n/g, "\n").split("\n");
  let file: DiffFile | null = null;
  let hunk: DiffHunk | null = null;
  let oldNo = 0;
  let newNo = 0;
  let gitHeaders = false;

  const startFile = (oldPath: string, newPath: string): DiffFile => {
    const f: DiffFile = {
      oldPath,
      newPath,
      path: newPath || oldPath,
      status: "modified",
      binary: false,
      additions: 0,
      deletions: 0,
      hunks: [],
    };
    files.push(f);
    hunk = null;
    return f;
  };

  for (const line of lines) {
    if (line.startsWith("diff --git ")) {
      gitHeaders = true;
      const m = /^diff --git (?:"?a\/(.+?)"?) (?:"?b\/(.+?)"?)$/.exec(line);
      file = startFile(m ? m[1] : "", m ? m[2] : "");
      continue;
    }
    // A bare diff without "diff --git" headers starts each file at "--- ".
    if (!gitHeaders && line.startsWith("--- ") && (!file || hunk)) {
      file = startFile("", "");
    }
    if (!file) continue;

    if (!hunk) {
      if (line.startsWith("--- ")) {
        const p = stripPrefix(line.slice(4));
        if (p === "/dev/null") file.status = "added";
        else file.oldPath = p;
        continue;
      }
      if (line.startsWith("+++ ")) {
        const p = stripPrefix(line.slice(4));
        if (p === "/dev/null") file.status = "deleted";
        else file.newPath = p;
        file.path = file.status === "deleted" ? file.oldPath : file.newPath;
        continue;
      }
      if (line.startsWith("new file mode")) file.status = "added";
      else if (line.startsWith("deleted file mode")) {
        file.status = "deleted";
        file.path = file.oldPath;
      } else if (line.startsWith("rename from ")) {
        file.status = "renamed";
        file.oldPath = line.slice("rename from ".length);
      } else if (line.startsWith("rename to ")) {
        file.status = "renamed";
        file.newPath = line.slice("rename to ".length);
        file.path = file.newPath;
      } else if (line.startsWith("Binary files ") || line === "GIT binary patch") {
        file.binary = true;
      }
    }

    const hm = HUNK_RE.exec(line);
    if (hm) {
      hunk = { header: line, lines: [] };
      file.hunks.push(hunk);
      oldNo = Number(hm[1]);
      newNo = Number(hm[2]);
      continue;
    }
    if (!hunk) continue;
    const h: DiffHunk = hunk;

    if (line.startsWith("+")) {
      h.lines.push({ type: "add", text: line.slice(1), newNo: newNo++ });
      file.additions++;
    } else if (line.startsWith("-")) {
      h.lines.push({ type: "del", text: line.slice(1), oldNo: oldNo++ });
      file.deletions++;
    } else if (line.startsWith(" ")) {
      h.lines.push({ type: "ctx", text: line.slice(1), oldNo: oldNo++, newNo: newNo++ });
    } else if (line.startsWith("\\")) {
      h.lines.push({ type: "meta", text: line });
    } else if (line === "") {
      // Trailing newline at end of diff text, or an empty context line some tools emit.
      continue;
    }
  }
  return files;
}
