import { useMemo } from "react";
import { parseUnifiedDiff } from "../diff";

const STATUS_LABELS = { modified: "upraven", added: "nový", deleted: "smazán", renamed: "přejmenován" };
const SIGN = { add: "+", del: "-", ctx: " ", meta: "" };

export function DiffView({ diff }: { diff: string }) {
  const files = useMemo(() => parseUnifiedDiff(diff), [diff]);
  if (!diff.trim()) return <p className="muted">Diff je prázdný.</p>;
  if (files.length === 0) return <pre className="output">{diff}</pre>;

  const totalAdd = files.reduce((s, f) => s + f.additions, 0);
  const totalDel = files.reduce((s, f) => s + f.deletions, 0);

  return (
    <div className="diff">
      <p className="small muted">
        {files.length} {files.length === 1 ? "soubor" : files.length < 5 ? "soubory" : "souborů"},{" "}
        <span className="add-count">+{totalAdd}</span> <span className="del-count">−{totalDel}</span>
      </p>
      {files.map((f, i) => (
        <details className="diff-file" key={`${f.path}-${i}`} open={files.length <= 8}>
          <summary>
            <span className="diff-path">
              {f.status === "renamed" ? `${f.oldPath} → ${f.newPath}` : f.path}
            </span>
            <span className="diff-stats">
              <span className="tag">{STATUS_LABELS[f.status]}</span>
              <span className="add-count">+{f.additions}</span>
              <span className="del-count">−{f.deletions}</span>
            </span>
          </summary>
          <div className="diff-body">
            {f.binary && <div className="dl meta">Binární soubor</div>}
            {f.hunks.map((h, hi) => (
              <div key={hi} className="diff-hunk">
                <div className="dl hunk">
                  <span className="ln" />
                  <span className="ln" />
                  <span className="code">{h.header}</span>
                </div>
                {h.lines.map((l, li) => (
                  <div key={li} className={`dl ${l.type}`}>
                    <span className="ln">{l.oldNo ?? ""}</span>
                    <span className="ln">{l.newNo ?? ""}</span>
                    <span className="code">
                      {SIGN[l.type]}
                      {l.text}
                    </span>
                  </div>
                ))}
              </div>
            ))}
          </div>
        </details>
      ))}
    </div>
  );
}
