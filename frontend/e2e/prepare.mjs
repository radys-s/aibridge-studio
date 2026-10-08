// Fresh fixture for the end-to-end tests: a git copy of the synthetic repo and a small vault
// whose project note points at it. Everything lives in e2e/.tmp and is rebuilt on each run.
import { execFileSync } from "node:child_process";
import { cpSync, mkdirSync, rmSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
export const TMP = join(here, ".tmp");
export const REPO = join(TMP, "calc");
export const VAULT = join(TMP, "vault");

rmSync(TMP, { recursive: true, force: true });
cpSync(resolve(here, "../../fixtures/synthetic_repo"), REPO, { recursive: true });
const git = (...args) => execFileSync("git", ["-c", "user.name=e2e", "-c", "user.email=e2e@test", ...args], { cwd: REPO });
git("init", "-q", "-b", "main");
git("add", "-A");
git("commit", "-qm", "init");

const notes = {
  "02 Projekty/Calc.md": `# Calc\n\nRepozitář: ${REPO}\n\n## Stav\n- další krok: přidat median()\n`,
  "02 Projekty/Tajné.md": "---\nprivate: true\n---\n# Tajné\nosobní obsah\n",
  "08 Předávání/2026-10-08 claude → codex - Calc median.md": "---\nstatus: open\nto: codex\n---\nCalc: přidat median\n",
};
for (const [rel, text] of Object.entries(notes)) {
  mkdirSync(dirname(join(VAULT, rel)), { recursive: true });
  writeFileSync(join(VAULT, rel), text, "utf8");
}
mkdirSync(join(TMP, "data"), { recursive: true });
console.log(`e2e fixture ready in ${TMP}`);
