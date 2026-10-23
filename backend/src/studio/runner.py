"""Control plane: drives a run through setup → draft → approve → plan → work → verify → review.

`run.phase` is the next phase to execute; it only advances after the phase's step has been
recorded as done. `run.state` is what the user sees. A run is picked up only when its state
is `queued`, so after a crash nothing continues until the user resumes it.

`Orchestration` (domain) decides which lead drafts, plans and reviews; this module only
executes that decision. Worker model choice stays with `routing.ModelRouter`.
"""

from __future__ import annotations

import json
import threading
import time
import traceback
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from studio import external, workspace
from studio.agents.leads import CallContext, Lead, LeadError, Mode, validate
from studio.agents.prompts import (
    DRAFT_SCHEMA,
    IMPLEMENT_SCHEMA,
    MEMORY_SCHEMA,
    PLAN_SCHEMA,
    REVIEW_SCHEMA,
    draft_prompt,
    implement_prompt,
    memory_prompt,
    plan_prompt,
    review_prompt,
)
from studio.apply import ApplyError, apply_run
from studio.db import AUTO_APPROVED, Database
from studio.domain import (
    AUTOPILOT,
    CLI_LEADS,
    Draft,
    ImplementReport,
    LeadConfig,
    MemoryProposal,
    Message,
    Plan,
    Review,
    RunState,
    TaskSpec,
)
from studio.gateway import ChatGateway
from studio.memory import build_proposals, fallback_proposal, project_memory
from studio.messages import tr
from studio.review import model_identity, review_passed, review_reason
from studio.routing import ModelRouter
from studio.sandbox import (
    SENSITIVE_OUTPUT,
    Denied,
    Sandbox,
    in_scope,
    safe_text,
    safe_value,
    secret_findings,
    sensitive_files,
)
from studio.vault import Vault
from studio.verify import failure_feedback, verify
from studio.worker.loop import Worker, WorkerStopped

PHASE_STATE = {
    "setup": RunState.PLANNING,
    "draft": RunState.PLANNING,
    "approve": RunState.PLANNING,
    "plan": RunState.PLANNING,
    "work": RunState.WORKING,
    "verify": RunState.VERIFYING,
    "review": RunState.REVIEWING,
}
EXTERNAL_AGENT = "freebuff"
# The Settings role a CLI lead needs for each kind of step (ADR 0007).
AGENT_ROLE_OF_STEP = {"draft": "plan", "plan": "plan", "review": "review", "work": "takeover"}
# Worker outcomes that mean "the cheap path cannot do this", not "it did something partial".
ESCALATE_WORKER = frozenset({"no_model", "gateway_down", "error", "budget"})
# Autopilot uses the same gateway and budget as the worker: it cannot fix these.
SHARED_POOL_FAILURES = frozenset({"gateway_down", "budget"})
WAITING = (RunState.NEEDS_DECISION, RunState.AWAITING_APPROVAL)


class Stopped(Exception):
    pass


class Runner:
    def __init__(
        self,
        db: Database,
        gateway: ChatGateway,
        router: ModelRouter,
        leads: dict[str, Lead],
        workspaces_dir: Path,
        vault: Callable[[], Vault | None] = lambda: None,
        agent_enabled: Callable[[str, str], bool] = lambda _name, _role: True,
    ) -> None:
        self.vault = vault
        # Whether the user still allows a CLI lead in a role (Settings can change mid-run).
        self.agent_enabled = agent_enabled
        self._vault_lock = threading.Lock()  # one proposal read-modify-write at a time
        self.db = db
        self.gateway = gateway
        self.router = router
        self.leads = leads
        self.workspaces_dir = workspaces_dir
        self._wake = threading.Event()
        self._halt = threading.Event()
        self._thread: threading.Thread | None = None

    # --- lifecycle --------------------------------------------------------------------

    def start(self) -> None:
        self.db.recover()
        self._thread = threading.Thread(target=self._loop, name="studio-runner", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._halt.set()
        self._wake.set()

    def wake(self) -> None:
        self._wake.set()

    def _loop(self) -> None:
        while not self._halt.is_set():
            run_id = self.db.next_queued()
            if run_id is None:
                self._wake.wait(timeout=2.0)
                self._wake.clear()
                continue
            self.execute(run_id)

    # --- user decisions ---------------------------------------------------------------

    def cancel(self, run_id: str) -> None:
        self.db.update_run(run_id, cancel_requested=1)
        self.db.event(run_id, "decision", self.db.msg("The user asked to interrupt"))
        run = self.db.run(run_id)
        if run and run["state"] in (
            RunState.QUEUED,
            RunState.AWAITING_EXTERNAL,
            RunState.AWAITING_APPROVAL,
        ):
            # Resume re-runs the current phase: approval and handoff simply wait again.
            self.db.update_run(run_id, state=RunState.INTERRUPTED)

    def resume(self, run_id: str) -> None:
        run = self._require(run_id)
        if run["state"] != RunState.INTERRUPTED:
            raise ValueError(f"run is {run['state']}, only interrupted runs can resume")
        if run["phase"] == "work":
            # The interrupted implementer may have left half-written files: start clean.
            self._reset_attempt(run)
        self.db.update_run(run_id, state=RunState.QUEUED, cancel_requested=0)
        self.db.event(
            run_id, "decision", self.db.msg("Continuing from phase {phase}", phase=run["phase"])
        )
        self.wake()

    def rework(self, run_id: str, note: str = "") -> None:
        run = self._require(run_id)
        if run["state"] != RunState.NEEDS_DECISION:
            raise ValueError("rework is possible only when a decision is needed")
        if not run["workspace"] or run["phase"] in ("setup", "draft", "approve"):
            raise ValueError("the task is not ready for implementation yet; use retry or abandon")
        feedback = json.loads(run["feedback_json"] or "[]")
        if note.strip():
            feedback.append(f"- Note from the user: {note.strip()}")
            self.db.message(run_id, "user", note.strip())
        self._requeue(
            run_id,
            phase="work",
            iteration=run["iteration"] + 1,
            feedback_json=json.dumps(feedback),
        )
        self.db.event(
            run_id, "decision", self.db.msg("The user sent the task back for another fix")
        )

    def retry(self, run_id: str) -> None:
        """Run the phase that failed again (e.g. a lead CLI error or a lost login)."""
        run = self._require(run_id)
        if run["state"] != RunState.NEEDS_DECISION:
            raise ValueError("retry is possible only when a decision is needed")
        if run["phase"] == "work":
            self._reset_attempt(run)
        self._requeue(run_id)
        self.db.event(run_id, "decision", self.db.msg("Retrying phase {phase}", phase=run["phase"]))

    def reply(self, run_id: str, text: str) -> None:
        """A chat message from the user. Answers pending questions by redoing that phase."""
        run = self._require(run_id)
        if not text.strip():
            raise ValueError("empty message")
        self.db.message(run_id, "user", text.strip())
        if run["state"] == RunState.AWAITING_APPROVAL:
            self._requeue(run_id, phase="draft")  # the lead redrafts with the user's answer
        elif run["state"] == RunState.NEEDS_DECISION and run["pending"] in ("draft", "plan"):
            self._requeue(run_id, phase=run["pending"])
        else:
            return
        self.db.event(run_id, "decision", self.db.msg("The user answered; the lead continues"))

    def approve(self, run_id: str, edits: dict[str, Any] | None = None) -> None:
        run = self._require(run_id)
        if run["state"] != RunState.AWAITING_APPROVAL:
            raise ValueError(f"run is {run['state']}, nothing to approve")
        spec = TaskSpec.model_validate_json(run["spec_json"])
        if edits:
            allowed = {
                "title",
                "goal",
                "allowed_paths",
                "acceptance_criteria",
                "acceptance_commands",
            }
            unknown = set(edits) - allowed
            if unknown:
                raise ValueError(f"cannot edit {sorted(unknown)} at approval")
            spec = TaskSpec.model_validate({**spec.model_dump(), **edits})
        if not spec.complete:
            raise ValueError("task is incomplete: " + ", ".join(spec.missing()))
        bad = invalid_commands(spec, Path(run["workspace"]))
        if bad:
            raise ValueError("commands not allowed: " + "; ".join(bad))
        self.db.update_spec(run_id, spec.model_dump_json())
        self._requeue(run_id, phase="plan")
        self.db.event(run_id, "decision", self.db.msg("The user approved the brief"))

    def takeover(self, run_id: str, lead: str | None = None, note: str = "") -> None:
        """A strong lead implements from the next iteration on (manual escalation)."""
        run = self._require(run_id)
        if run["state"] not in (RunState.NEEDS_DECISION, RunState.INTERRUPTED):
            raise ValueError("takeover is possible only when the run is stopped")
        if not run["workspace"] or run["phase"] in ("setup", "draft", "approve"):
            raise ValueError("the task is not ready for implementation yet")
        spec = TaskSpec.model_validate_json(run["spec_json"])
        name = lead or spec.orchestration.takeover
        if name not in (AUTOPILOT, *CLI_LEADS) or name not in self.leads:
            raise ValueError(f"unknown lead {name}")
        if name in CLI_LEADS and name == spec.orchestration.reviewer:
            raise ValueError(f"{name} reviews this task, so it cannot also implement it")
        feedback = json.loads(run["feedback_json"] or "[]")
        if note.strip():
            feedback.append(f"- Note from the user: {note.strip()}")
            self.db.message(run_id, "user", note.strip())
        self._reset_attempt(run)
        self._requeue(
            run_id,
            phase="work",
            iteration=run["iteration"] + 1,
            implementer=name,
            escalations=run["escalations"] + 1,
            feedback_json=json.dumps(feedback),
        )
        self.db.event(
            run_id,
            "escalation",
            self.db.msg("{name} takes over the implementation (by hand)", name=name),
        )
        self.db.message(
            run_id, "studio", self.db.msg("{name} takes over the implementation.", name=name)
        )

    def keep_worker(self, run_id: str) -> None:
        run = self._require(run_id)
        if (
            run["state"] != RunState.NEEDS_DECISION
            or run["pending"] != "takeover"
            or run["phase"] != "work"
        ):
            raise ValueError("no takeover decision is pending")
        self._reset_attempt(run)
        self._requeue(run_id, implementer="worker")
        self.db.event(run_id, "decision", self.db.msg("The user kept the cheap worker"))

    def accept(self, run_id: str) -> None:
        """The user confirms a change that only a self-review approved (solo mode)."""
        run = self._require(run_id)
        if run["state"] != RunState.NEEDS_DECISION or run["pending"] != "self_review":
            raise ValueError("manual acceptance is only for a self-reviewed change")
        spec = TaskSpec.model_validate_json(run["spec_json"])
        review = self.db.last_step(run_id, "review")
        if (
            not review
            or not review["output_json"]
            or not review_passed(json.loads(review["output_json"]), spec.acceptance_criteria)
        ):
            raise ValueError("complete passing criterion assessments are required for acceptance")
        if not self._accept(
            run, self.db.msg("The user confirmed a change approved only by self-review")
        ):
            return
        threading.Thread(
            target=self._propose_memory, args=(run_id,), name="studio-memory", daemon=True
        ).start()

    def vault_write(self, run_id: str, index: int) -> str:
        """Write one approved memory proposal into the vault. The only vault write path."""
        with self._vault_lock:
            return self._vault_write(run_id, index)

    def _vault_write(self, run_id: str, index: int) -> str:
        step, proposals = self._proposals(run_id)
        if not 0 <= index < len(proposals) or proposals[index]["status"] != "proposed":
            raise ValueError("no such open proposal")
        vault = self.vault()
        if vault is None:
            raise ValueError("the memory folder is not configured or missing")
        item = proposals[index]
        try:
            rel = vault.write(item["path"], item["content"], item["kind"])
        except Denied as exc:
            raise ValueError(str(exc)) from exc
        item["status"] = "written"
        self._save_proposals(step, proposals)
        self.db.event(
            run_id,
            "vault",
            self.db.msg("Written to memory: {path}", path=rel),
            {"vault_written": rel},
        )
        return rel

    def apply_to_repo(self, run_id: str) -> dict[str, Any]:
        """Put an accepted change into the source repo as branch studio/<run_id> (on click).

        Never merges, pushes or switches the user's branch; see apply.py.
        """
        run = self._require(run_id)
        if run["state"] != RunState.ACCEPTED:
            raise ValueError("only an accepted change can be applied")
        spec = TaskSpec.model_validate_json(run["spec_json"])
        try:
            applied = apply_run(
                Path(spec.repo_path),
                Path(run["workspace"]),
                run["source_commit"],
                run_id,
                spec.title,
            )
        except ApplyError as exc:
            raise ValueError(str(exc)) from exc
        data = {"branch": applied.branch, "commit": applied.commit, "files": applied.files}
        self.db.event(
            run_id,
            "applied",
            self.db.msg("The change is in branch {branch}", branch=applied.branch),
            data,
        )
        self.db.message(
            run_id,
            "studio",
            self.db.msg(
                "The change is in the repository as branch `{branch}` (commit {commit}). "
                "Your current branch is unchanged; merging is up to you.",
                branch=applied.branch,
                commit=applied.commit[:10],
            ),
        )
        return data

    def vault_discard(self, run_id: str, index: int) -> None:
        with self._vault_lock:
            self._vault_discard(run_id, index)

    def _vault_discard(self, run_id: str, index: int) -> None:
        step, proposals = self._proposals(run_id)
        if not 0 <= index < len(proposals) or proposals[index]["status"] != "proposed":
            raise ValueError("no such open proposal")
        proposals[index]["status"] = "discarded"
        self._save_proposals(step, proposals)
        self.db.event(
            run_id,
            "vault",
            self.db.msg("Proposal discarded: {path}", path=proposals[index]["path"]),
        )

    def _proposals(self, run_id: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        self._require(run_id)
        step = self.db.last_step(run_id, "memory")
        if not step or not step["output_json"]:
            raise ValueError("there are no memory proposals for this run")
        return step, list(json.loads(step["output_json"]).get("proposals") or [])

    def _save_proposals(self, step: dict[str, Any], proposals: list[dict[str, Any]]) -> None:
        output = json.loads(step["output_json"])
        output["proposals"] = proposals
        self.db.finish_step(step["id"], step["status"], output)

    def allow_sensitive(self, run_id: str) -> None:
        run = self._require(run_id)
        if run["state"] != RunState.NEEDS_DECISION or run["pending"] != "sensitive":
            raise ValueError("no sensitive-file decision is pending")
        spec = TaskSpec.model_validate_json(run["spec_json"])
        implementer = run["implementer"] or spec.implementer
        if run["phase"] not in ("setup", "draft") and not (
            run["phase"] == "work" and implementer == "freebuff"
        ):
            raise ValueError("sensitive model evidence cannot be overridden; provide safe evidence")
        work = self.db.last_step(run_id, "work")
        if work and work["error"] == "sensitive":
            raise ValueError("sensitive model output cannot be overridden")
        self.db.event(
            run_id, "decision", self.db.msg("The user confirmed the files found are not secret")
        )
        self._requeue(run_id, sensitive_ok=1)

    def abandon(self, run_id: str) -> None:
        run = self._require(run_id)
        if run["state"] in (RunState.ACCEPTED, RunState.ABANDONED):
            raise ValueError(f"run is already {run['state']}")
        if run["state"] not in (
            RunState.NEEDS_DECISION,
            RunState.INTERRUPTED,
            RunState.QUEUED,
            RunState.AWAITING_EXTERNAL,
            RunState.AWAITING_APPROVAL,
        ):
            raise ValueError("stop the run before abandoning it")
        self.db.update_run(run_id, state=RunState.ABANDONED, finished_at=time.time())
        self.db.event(run_id, "decision", self.db.msg("The user abandoned the task"))

    def external_done(self, run_id: str, note: str = "") -> None:
        """The user says the external agent finished. Its work is a claim until verified."""
        run = self._require(run_id)
        if run["state"] != RunState.AWAITING_EXTERNAL:
            raise ValueError(f"run is {run['state']}, not waiting for an external agent")
        root = Path(run["workspace"])
        handoff = self.db.last_step(run_id, "handoff")
        step = self.db.start_step(run_id, "work", run["iteration"], f"external:{EXTERNAL_AGENT}")
        if not workspace.contains(root, run["iteration_base"]):
            reason = "external agent rewrote git history of the working copy"
            self.db.finish_step(step, "failed", error=reason)
            self._needs_decision(run_id, reason)
            return
        workspace.commit_all(root, f"studio: external iteration {run['iteration']}")
        started = float(handoff["finished_at"]) if handoff and handoff["finished_at"] else None
        output = {
            "status": "external",
            "summary": note.strip(),
            "claim": f"{EXTERNAL_AGENT} run by the user; nothing was verified by Studio yet",
        }
        self.db.finish_step(step, "done", output)
        self.db.record_model_call(
            run_id=run_id,
            step_id=step,
            role="worker",
            requested_model=f"{EXTERNAL_AGENT}-external",
            actual_model=None,  # Studio cannot observe the external tool's actual model.
            provider=f"{EXTERNAL_AGENT} (own login, ads; usage unknown)",
            outcome="ok",
            billing="external",
            latency_ms=int((time.time() - started) * 1000) if started else None,
        )
        self.db.update_run(run_id, state=RunState.QUEUED, phase="verify")
        self.db.event(
            run_id,
            "work",
            self.db.msg("The user reported that {agent} finished; verifying", agent=EXTERNAL_AGENT),
        )
        self.wake()

    def open_external_terminal(self, run_id: str) -> None:
        """Open a terminal in the working copy. The user runs the agent; Studio does not."""
        run = self._require(run_id)
        if run["state"] != RunState.AWAITING_EXTERNAL:
            raise ValueError(f"run is {run['state']}, not waiting for an external agent")
        try:
            external.open_terminal(Path(run["workspace"]), EXTERNAL_AGENT)
        except external.TerminalError as exc:
            raise ValueError(str(exc)) from exc
        self.db.event(
            run_id, "handoff", self.db.msg("Terminal opened for {agent}", agent=EXTERNAL_AGENT)
        )

    def _requeue(self, run_id: str, **fields: Any) -> None:
        self.db.update_run(
            run_id,
            state=RunState.QUEUED,
            decision_reason=None,
            pending=None,
            cancel_requested=0,
            **fields,
        )
        self.wake()

    def _require(self, run_id: str) -> dict[str, Any]:
        run = self.db.run(run_id)
        if not run:
            raise KeyError(run_id)
        return run

    # --- execution --------------------------------------------------------------------

    def _should_stop(self, run_id: str) -> bool:
        if self._halt.is_set():
            return True
        run = self.db.run(run_id)
        return bool(run and run["cancel_requested"])

    def execute(self, run_id: str) -> None:
        """Advance the run until it needs the user, is accepted, or is stopped."""
        try:
            while True:
                run = self._require(run_id)
                if run["state"] not in (RunState.QUEUED, *PHASE_STATE.values()):
                    return
                phase = run["phase"]
                if self._should_stop(run_id):
                    raise Stopped()
                self.db.update_run(run_id, state=PHASE_STATE[phase])
                spec = TaskSpec.model_validate_json(run["spec_json"])
                handler = getattr(self, f"_phase_{phase}")
                if handler(run, spec) == "pause":
                    return
        except Stopped:
            self.db.update_run(run_id, state=RunState.INTERRUPTED)
            self.db.event(
                run_id,
                "interrupted",
                self.db.msg("Run interrupted; it can continue or be abandoned"),
            )
        except Exception as exc:  # last resort: never leave a run looking active
            self.db.event(
                run_id,
                "crash",
                self.db.msg("Unexpected error: {error}", error=exc),
                {"tb": traceback.format_exc()[-3000:]},
            )
            self._needs_decision(run_id, f"internal error: {exc}")

    def _needs_decision(self, run_id: str, reason: str, pending: str | None = None) -> str:
        self.db.update_run(
            run_id, state=RunState.NEEDS_DECISION, decision_reason=reason, pending=pending
        )
        self.db.event(run_id, "needs_decision", reason, {"pending": pending} if pending else None)
        return "pause"

    def _messages(self, run_id: str) -> list[Message]:
        return [
            Message(author=m["author"], text=m["text"])
            for m in self.db.messages(run_id)
            if m["author"] in ("user", AUTOPILOT, "claude", "codex", "studio")
        ]

    # --- lead calls -------------------------------------------------------------------

    def _ask_lead[T: BaseModel](
        self,
        run: dict[str, Any],
        spec: TaskSpec,
        name: str,
        kind: str,
        prompt: str,
        schema: dict[str, Any],
        model: type[T],
        mode: Mode = "read",
        actor: str | None = None,
        exclude_models: frozenset[str] = frozenset(),
        complexity: str = "moderate",
    ) -> tuple[int, T | LeadError]:
        """One lead call as one step. Cancel raises Stopped; other failures are returned.

        CLI leads are recorded here as one subscription call; Autopilot (`records_calls`)
        records each of its gateway calls itself, against the run's API budget.
        """
        run_id = run["id"]
        step = self.db.start_step(run_id, kind, run["iteration"], actor or name)
        if secret_findings(prompt) or SENSITIVE_OUTPUT in prompt:
            error = LeadError(
                "Sensitive context withheld; review requires safe evidence.", "sensitive"
            )
            self.db.finish_step(step, "failed", error=str(error))
            return step, error
        lead = self.leads.get(name)
        if lead is None:
            error = LeadError(f"lead {name} is not configured in this Studio", "not_installed")
            self.db.finish_step(step, "failed", error=str(error))
            return step, error
        role = AGENT_ROLE_OF_STEP.get(kind)
        if name in CLI_LEADS and (role is None or not self.agent_enabled(name, role)):
            error = LeadError(
                f"{name} is switched off for {role or kind} in Settings", "not_installed"
            )
            self.db.finish_step(step, "failed", error=str(error))
            return step, error
        config = spec.orchestration.config(name)
        ctx = CallContext(
            config=config,
            timeout_s=spec.budget.agent_timeout_s,
            should_stop=lambda: self._should_stop(run_id),
            on_event=lambda e: self.db.agent_event(run_id, step, name, e.kind, e.text, e.data),
            run_id=run_id,
            step_id=step,
            role=kind,
            scope=tuple(spec.allowed_paths),
            complexity=complexity,
            exclude_models=exclude_models,
            max_tokens=spec.budget.max_tokens,
            max_cost_usd=spec.budget.max_cost_usd,
        )
        started = time.monotonic()
        result: T | LeadError
        try:
            payload = lead.ask(prompt, schema, Path(run["workspace"]), ctx, mode)
            if secret_findings(json.dumps(payload, ensure_ascii=False)):
                raise LeadError("Sensitive agent output withheld.", "sensitive")
            result = validate(model, payload)
        except LeadError as exc:
            result = exc
        if not getattr(lead, "records_calls", False):
            self._record_lead_call(run_id, step, kind, name, config, ctx, result, started)
        if isinstance(result, LeadError):
            if result.kind == "cancelled":
                self.db.finish_step(step, "interrupted", error=str(result))
                raise Stopped() from result
            self.db.finish_step(step, "failed", error=str(result))
        else:
            self.db.finish_step(step, "done", result.model_dump())
        return step, result

    def _record_lead_call(
        self,
        run_id: str,
        step: int,
        kind: str,
        name: str,
        config: LeadConfig,
        ctx: CallContext,
        result: object,
        started: float,
    ) -> None:
        cli = name in CLI_LEADS
        self.db.record_model_call(
            run_id=run_id,
            step_id=step,
            role=kind,
            requested_model=f"{name}:{config.model or 'default'}"
            + (f"@{config.effort}" if config.effort else "")
            if cli
            else name,
            actual_model=ctx.usage.actual_model,
            provider=f"{name} CLI (own login)" if cli else name,
            tokens_in=ctx.usage.tokens_in,
            tokens_out=ctx.usage.tokens_out,
            # The CLI's figure is what the call would cost on the API; the user pays a
            # subscription, so it must not count against the run's API budget.
            cost_usd=0.0 if cli else ctx.usage.cost_usd,
            billing="subscription",
            outcome="ok" if not isinstance(result, LeadError) else result.kind,
            error=str(result)[:500] if isinstance(result, LeadError) else None,
            latency_ms=int((time.monotonic() - started) * 1000),
        )

    def _ask_leads[T: BaseModel](
        self,
        run: dict[str, Any],
        spec: TaskSpec,
        names: list[str],
        kind: str,
        prompt: Callable[[str], str],
        schema: dict[str, Any],
        model: type[T],
        exclude_models: frozenset[str] = frozenset(),
    ) -> dict[str, T | LeadError]:
        """Ask several leads the same thing in parallel; each gets its own step.

        `exclude_models` only matters to Autopilot (router-backed): the reviewer must not
        be the model that wrote the change.
        """
        if len(names) == 1:
            name = names[0]
            return {
                name: self._ask_lead(
                    run,
                    spec,
                    name,
                    kind,
                    prompt(name),
                    schema,
                    model,
                    exclude_models=exclude_models,
                )[1]
            }
        with ThreadPoolExecutor(max_workers=len(names), thread_name_prefix="lead") as pool:
            futures = {
                n: pool.submit(
                    self._ask_lead,
                    run,
                    spec,
                    n,
                    kind,
                    prompt(n),
                    schema,
                    model,
                    exclude_models=exclude_models,
                )
                for n in names
            }
            results: dict[str, T | LeadError] = {}
            stopped = False
            for n, fut in futures.items():
                try:
                    results[n] = fut.result()[1]
                except Stopped:
                    stopped = True
        if stopped:
            raise Stopped()
        return results

    # --- phases -----------------------------------------------------------------------

    def _phase_setup(self, run: dict[str, Any], spec: TaskSpec) -> str:
        run_id = run["id"]
        step = self.db.start_step(run_id, "setup", run["iteration"], "studio")
        dest = self.workspaces_dir / run_id
        try:
            if dest.exists():  # interrupted during a previous setup: start over
                workspace.remove(dest)
            head = workspace.create(Path(spec.repo_path), dest)
        except workspace.WorkspaceError as exc:
            self.db.finish_step(step, "failed", error=str(exc))
            return self._needs_decision(run_id, f"working copy could not be created: {exc}")
        self.db.finish_step(step, "done", {"workspace": str(dest), "source_commit": head})
        self.db.update_run(
            run_id, workspace=str(dest), source_commit=head, iteration_base=head, phase="draft"
        )
        self.db.event(
            run_id,
            "setup",
            self.db.msg("Isolated working copy from commit {commit}", commit=head[:10]),
        )
        return "next"

    def _phase_draft(self, run: dict[str, Any], spec: TaskSpec) -> str:
        run_id = run["id"]
        if not run["sensitive_ok"]:
            # Lead CLIs read files Studio cannot filter: no secret may sit in the copy.
            hits = sensitive_files(Path(run["workspace"]))
            if hits:
                return self._needs_decision(
                    run_id,
                    "working copy contains files that look like secrets; leads would read "
                    "them: " + "; ".join(hits[:10]),
                    pending="sensitive",
                )
        if spec.complete and not self._drafted(run_id):
            # Structured task: the user wrote scope, criteria and commands already.
            self.db.update_run(run_id, phase="plan")
            return "next"
        name = spec.orchestration.primary
        memory: list[tuple[str, str]] = []
        if spec.project_note:
            vault = self.vault()
            if vault is None:
                return self._needs_decision(
                    run_id, "the memory folder is not configured or missing"
                )
            try:
                memory, refused = project_memory(vault, spec.project_note)
            except (Denied, OSError) as exc:
                return self._needs_decision(run_id, f"project note cannot be shared: {exc}")
            sent = [path for path, _ in memory]
            self.db.event(
                run_id,
                "vault",
                self.db.msg("Memory notes sent to the model: {notes}", notes=", ".join(sent)),
                {"vault": sent, "refused": refused},
            )
        self.db.event(run_id, "draft", self.db.msg("{name} drafts the brief", name=name))
        _step, result = self._ask_lead(
            run,
            spec,
            name,
            "draft",
            draft_prompt(spec, self._messages(run_id), memory),
            DRAFT_SCHEMA,
            Draft,
        )
        if isinstance(result, LeadError):
            return self._needs_decision(run_id, f"drafting by {name} failed: {result}")
        updated = TaskSpec.model_validate(
            {
                **spec.model_dump(),
                "title": result.title or spec.title,
                "goal": result.goal or spec.goal,
                "allowed_paths": result.allowed_paths,
                "acceptance_criteria": result.acceptance_criteria,
                "acceptance_commands": result.acceptance_commands,
            }
        )
        self.db.update_spec(run_id, updated.model_dump_json())
        text = draft_message(result, self.db.language())
        bad = invalid_commands(updated, Path(run["workspace"]))
        if bad:
            text += "\n\n" + self.db.msg(
                "Studio will not run these commands: {commands}", commands="; ".join(bad)
            )
        self.db.message(run_id, name, text, {"draft": result.model_dump(), "invalid": bad})
        self.db.update_run(run_id, phase="approve")
        return "next"

    def _drafted(self, run_id: str) -> bool:
        return self.db.last_step(run_id, "draft") is not None

    def _phase_approve(self, run: dict[str, Any], spec: TaskSpec) -> str:
        run_id = run["id"]
        draft = self.db.last_step(run_id, "draft")
        questions: list[str] = []
        if draft and draft["output_json"]:
            questions = json.loads(draft["output_json"]).get("questions") or []
        bad = invalid_commands(spec, Path(run["workspace"])) if spec.complete else []
        if spec.auto_approve and spec.complete and not questions and not bad:
            self.db.event(run_id, "decision", self.db.msg(AUTO_APPROVED))
            self.db.update_run(run_id, phase="plan")
            return "next"
        self.db.update_run(
            run_id,
            state=RunState.AWAITING_APPROVAL,
            pending="draft" if questions else None,
        )
        self.db.event(
            run_id,
            "approval",
            self.db.msg("The lead has questions about the brief")
            if questions
            else self.db.msg("The brief waits for approval"),
        )
        return "pause"

    def _phase_plan(self, run: dict[str, Any], spec: TaskSpec) -> str:
        run_id = run["id"]
        orch = spec.orchestration
        planners = orch.planners()
        if not planners:
            self.db.update_run(run_id, phase="work")
            return "next"
        messages = self._messages(run_id)
        names = f" {self.db.msg('and')} ".join(planners)
        self.db.event(run_id, "plan", self.db.msg("{names} prepare the plan", names=names))
        results = self._ask_leads(
            run,
            spec,
            list(planners),
            "plan",
            lambda _n: plan_prompt(spec, messages),
            PLAN_SCHEMA,
            Plan,
        )
        failed = {n: r for n, r in results.items() if isinstance(r, LeadError)}
        if failed:
            return self._needs_decision(
                run_id, "; ".join(f"planning by {n} failed: {e}" for n, e in failed.items())
            )
        plans: dict[str, Plan] = {n: r for n, r in results.items() if isinstance(r, Plan)}
        plan = next(iter(plans.values()))
        self.db.update_run(run_id, plan_json=plan.model_dump_json(), phase="work")
        if plan.questions:
            self.db.message(
                run_id,
                orch.primary,
                self.db.msg("Before the implementation I need answers to:")
                + "\n"
                + "\n".join(f"- {q}" for q in plan.questions),
            )
            self.db.update_run(run_id, phase="plan")
            return self._needs_decision(run_id, "lead has questions about the plan", "plan")
        return "next"

    def _implementer(self, run: dict[str, Any], spec: TaskSpec, plan: Plan | None) -> str:
        if run["implementer"]:
            return str(run["implementer"])
        if spec.implementer != "auto":
            return spec.implementer
        if plan and plan.recommended_implementer == "strong_agent":
            lead = spec.orchestration.takeover
            if spec.escalation == "auto":
                self.db.update_run(run["id"], implementer=lead)
                self.db.event(
                    run["id"],
                    "escalation",
                    self.db.msg("The plan recommends a strong agent: {lead}", lead=lead),
                )
                return lead
            if spec.escalation == "ask":
                return "ask"
        return "worker"

    def _reset_attempt(self, run: dict[str, Any]) -> None:
        """Drop the current attempt's half-done work: reset to this iteration's checkpoint.

        If the checkpoint for this iteration was never recorded (Studio stopped after the
        iteration counter moved but before `_phase_work` wrote the new base), no worker has
        run in this iteration yet, so the working copy holds the previous iteration's work
        and must be kept; `_phase_work` will checkpoint it. Resetting to the older base would
        lose it. Older runs without `base_iteration` keep the previous behaviour."""
        if not run["workspace"] or not run["iteration_base"]:
            return
        recorded = run["base_iteration"]
        if recorded is not None and recorded != run["iteration"]:
            self.db.event(
                run["id"],
                "warning",
                self.db.msg("The iteration checkpoint was not written; the work stays"),
            )
            return
        workspace.reset_to(Path(run["workspace"]), run["iteration_base"])

    def _phase_work(self, run: dict[str, Any], spec: TaskSpec) -> str:
        run_id = run["id"]
        root = Path(run["workspace"])
        base = workspace.commit_all(root, f"studio: before iteration {run['iteration']}")
        self.db.update_run(run_id, iteration_base=base, base_iteration=run["iteration"])
        plan = Plan.model_validate_json(run["plan_json"]) if run["plan_json"] else None
        feedback = json.loads(run["feedback_json"] or "[]")
        who = self._implementer(run, spec, plan)
        if who == "ask":
            return self._needs_decision(
                run_id,
                f"plan recommends a strong agent; hand over to {spec.orchestration.takeover}?",
                "takeover",
            )
        if who == "freebuff":
            return self._handoff(run, spec, plan, feedback)
        if who == AUTOPILOT or who in CLI_LEADS:
            return self._lead_work(run, spec, who, plan, feedback)
        return self._worker_work(run, spec, plan, feedback)

    def _lead_work(
        self,
        run: dict[str, Any],
        spec: TaskSpec,
        name: str,
        plan: Plan | None,
        feedback: list[str],
    ) -> str:
        run_id = run["id"]
        root = Path(run["workspace"])
        self.db.event(run_id, "work", f"{name} implementuje iteraci {run['iteration']}")
        _step, result = self._ask_lead(
            run,
            spec,
            name,
            "work",
            implement_prompt(spec, plan, feedback, self._messages(run_id)),
            IMPLEMENT_SCHEMA,
            ImplementReport,
            mode="write",
            actor=f"lead:{name}",
            complexity=plan.complexity if plan else "moderate",
        )
        workspace.commit_all(root, f"studio: {name} iteration {run['iteration']}")
        if isinstance(result, LeadError):
            return self._needs_decision(run_id, f"implementation by {name} failed: {result}")
        # The CLI edits files itself, outside Studio's sandbox: check the scope afterwards.
        outside = [
            f
            for f in workspace.changed_files(root, run["iteration_base"])
            if spec.allowed_paths and not in_scope(f, spec.allowed_paths)
        ]
        if outside:
            return self._needs_decision(
                run_id,
                f"{name} changed files outside the task scope: {', '.join(outside[:10])}",
            )
        self.db.update_run(run_id, phase="verify")
        return "next"

    def _worker_work(
        self, run: dict[str, Any], spec: TaskSpec, plan: Plan | None, feedback: list[str]
    ) -> str:
        run_id = run["id"]
        root = Path(run["workspace"])
        step = self.db.start_step(run_id, "work", run["iteration"], "worker")
        self.db.event(
            run_id, "work", self.db.msg("The worker starts iteration {n}", n=run["iteration"])
        )
        worker = Worker(
            self.db,
            self.gateway,
            self.router,
            Sandbox(root, spec.allowed_paths, spec.budget.command_timeout_s),
            run_id,
            step,
            spec.budget,
            complexity=plan.complexity if plan else "moderate",
            should_stop=lambda: self._should_stop(run_id),
        )
        try:
            outcome = worker.run(spec, plan, feedback)
        except WorkerStopped as exc:
            self.db.finish_step(step, "interrupted", error="cancelled")
            raise Stopped() from exc
        workspace.commit_all(root, f"studio: worker iteration {run['iteration']}")
        if outcome.status != "finished":
            self.db.finish_step(step, "failed", outcome.as_dict(), outcome.status)
            if outcome.status == "sensitive":
                return self._needs_decision(
                    run_id, "Sensitive worker output withheld.", "sensitive"
                )
            if outcome.status in ("turn_limit", "time_limit", "error") and outcome.files_written:
                # Partial work is still worth an independent check rather than a guess.
                self.db.event(
                    run_id,
                    "warning",
                    self.db.msg(
                        "The worker ended with status {status}; verifying", status=outcome.status
                    ),
                )
                self.db.update_run(run_id, phase="verify")
                return "next"
            why = f"worker stopped: {outcome.status} {outcome.detail}".strip()
            if spec.orchestration.takeover == AUTOPILOT and outcome.status in SHARED_POOL_FAILURES:
                return self._needs_decision(run_id, why + " (Autopilot shares this pool)")
            if outcome.status in ESCALATE_WORKER:
                return self._escalate_or_decide(run, spec, why, feedback, same_iteration=True)
            return self._needs_decision(run_id, why)
        self.db.finish_step(step, "done", outcome.as_dict())
        self.db.update_run(run_id, phase="verify")
        return "next"

    def _escalate_or_decide(
        self,
        run: dict[str, Any],
        spec: TaskSpec,
        why: str,
        feedback: list[str],
        same_iteration: bool = False,
    ) -> str:
        """The cheap implementer failed. Hand over to a strong lead if the policy allows."""
        run_id = run["id"]
        fresh = self._require(run_id)
        current = fresh["implementer"] or (
            "worker" if spec.implementer == "auto" else spec.implementer
        )
        lead = spec.orchestration.takeover
        can = (
            spec.implementer in ("auto", "worker")
            and current == "worker"
            and spec.escalation != "off"
            and fresh["escalations"] < spec.budget.max_escalations
        )
        if not can:
            return self._needs_decision(run_id, why)
        if spec.escalation == "ask":
            return self._needs_decision(run_id, f"{why}; hand over to {lead}?", "takeover")
        self._reset_attempt(fresh)
        self.db.update_run(
            run_id,
            implementer=lead,
            escalations=fresh["escalations"] + 1,
            phase="work",
            iteration=fresh["iteration"] + (0 if same_iteration else 1),
            feedback_json=json.dumps(feedback),
        )
        self.db.event(
            run_id,
            "escalation",
            self.db.msg("{why}; {lead} takes over the implementation", why=why, lead=lead),
        )
        self.db.message(
            run_id,
            "studio",
            self.db.msg(
                "The cheap worker was not enough ({why}). {lead} takes over.", why=why, lead=lead
            ),
        )
        return "next"

    def _handoff(
        self, run: dict[str, Any], spec: TaskSpec, plan: Plan | None, feedback: list[str]
    ) -> str:
        """Give the working copy to an agent the user drives by hand, then wait for them."""
        run_id = run["id"]
        root = Path(run["workspace"])
        step = self.db.start_step(run_id, "handoff", run["iteration"], "studio")
        # Studio cannot filter what an external agent reads, so check the whole copy first.
        hits = sensitive_files(root)
        if hits:
            self.db.finish_step(step, "failed", {"sensitive_files": hits}, "sensitive files")
            return self._needs_decision(
                run_id,
                "working copy contains files that must not leave the machine: "
                + "; ".join(hits[:10]),
            )
        brief = external_brief(spec, plan, feedback, root)
        brief_path = self.workspaces_dir / f"{run_id}.brief.md"
        brief_path.write_text(brief, encoding="utf-8")
        self.db.finish_step(
            step, "done", {"agent": EXTERNAL_AGENT, "brief": str(brief_path), "text": brief}
        )
        self.db.update_run(run_id, state=RunState.AWAITING_EXTERNAL)
        self.db.event(
            run_id,
            "handoff",
            self.db.msg(
                "Waiting for {agent}: start it in the working copy, then click Done",
                agent=EXTERNAL_AGENT,
            ),
            {"workspace": str(root), "brief": str(brief_path)},
        )
        return "pause"

    def _phase_verify(self, run: dict[str, Any], spec: TaskSpec) -> str:
        run_id = run["id"]
        step = self.db.start_step(run_id, "verify", run["iteration"], "studio")
        result = verify(spec, Path(run["workspace"]), run["source_commit"])
        raw_diff = workspace.diff(Path(run["workspace"]), run["source_commit"])
        result["diff"] = safe_text(raw_diff)
        result = safe_value(result)
        if SENSITIVE_OUTPUT in json.dumps(result, ensure_ascii=False):
            result["sensitive"] = True
            self.db.finish_step(step, "failed", result)
            return self._needs_decision(
                run_id,
                "Sensitive output or diff withheld; provide safe evidence before review.",
                "sensitive",
            )
        self.db.finish_step(step, "done" if result["passed"] else "failed", result)
        if result["passed"]:
            self.db.event(run_id, "verify", self.db.msg("Independent verification passed"))
            self.db.update_run(run_id, phase="review")
            return "next"
        feedback = failure_feedback(result)
        self.db.event(
            run_id, "verify", self.db.msg("Independent verification failed"), {"feedback": feedback}
        )
        return self._next_iteration_or_decide(
            run, spec, feedback, "independent verification failed"
        )

    def _phase_review(self, run: dict[str, Any], spec: TaskSpec) -> str:
        run_id = run["id"]
        verification = json.loads(self.db.last_step(run_id, "verify")["output_json"])  # type: ignore[index]
        if (
            verification.get("sensitive")
            or secret_findings(json.dumps(verification))
            or SENSITIVE_OUTPUT in json.dumps(verification)
        ):
            return self._needs_decision(run_id, "Sensitive review evidence withheld.", "sensitive")
        work = self.db.last_step(run_id, "work")
        summary = (
            json.loads(work["output_json"]).get("summary", "")
            if work and work["output_json"]
            else ""
        )
        implementer = (work or {}).get("actor") or "worker"
        impl_name = implementer.removeprefix("lead:")
        reviewers: list[str] = list(spec.orchestration.reviewers())
        exclude = self._step_models(run_id, work["id"]) if work else frozenset()
        if exclude:
            # The same model behind another provider (groq/openai/gpt-oss-120b and
            # cerebras/gpt-oss-120b) is not an independent reviewer either; without this the
            # router offers it and the run ends in self_review.
            same = {model_identity(m) for m in exclude} | set(
                self._step_actual_models(run_id, work["id"]) if work else ()
            )
            exclude = exclude | {m.id for m in self.router.models if model_identity(m.id) in same}
        names = f" {self.db.msg('and')} ".join(reviewers)
        self.db.event(
            run_id, "review", self.db.msg("{names} review the diff independently", names=names)
        )
        prompt = review_prompt(spec, verification["diff"], verification, summary, implementer)
        results = self._ask_leads(
            run,
            spec,
            reviewers,
            "review",
            lambda _n: prompt,
            REVIEW_SCHEMA,
            Review,
            exclude_models=exclude,
        )
        dependent: set[str] = set()
        first = results.get(AUTOPILOT)
        if isinstance(first, LeadError) and first.kind == "no_model" and exclude:
            # No other free model is available: review on the same one, the user confirms.
            self.db.event(
                run_id,
                "warning",
                self.db.msg("No other free model is free; review with the same model"),
            )
            step, results[AUTOPILOT] = self._ask_lead(
                run, spec, AUTOPILOT, "review", prompt, REVIEW_SCHEMA, Review
            )
            if self._step_models(run_id, step) & exclude:
                dependent.add(AUTOPILOT)
        failed = {n: r for n, r in results.items() if isinstance(r, LeadError)}
        if failed:
            # Never accept on fewer reviews than the mode requires.
            return self._needs_decision(
                run_id,
                "; ".join(f"review by {n} failed: {e}" for n, e in failed.items()),
                "sensitive" if any(e.kind == "sensitive" for e in failed.values()) else None,
            )
        reviews = {n: r for n, r in results.items() if isinstance(r, Review)}
        for name, review in reviews.items():
            steps = [
                s
                for s in self.db.steps(run_id)
                if s["kind"] == "review" and s["actor"] == name and s["status"] == "done"
            ]
            if steps:
                review_step = steps[-1]
                review_models = self._step_models(run_id, review_step["id"])
                if name in CLI_LEADS:
                    # A CLI lead is its own vendor; independence is a different agent and
                    # different actual models (checked below).
                    distinct = name != impl_name
                elif impl_name in CLI_LEADS:
                    distinct = bool(review_models)
                else:
                    distinct = bool(exclude and review_models and not review_models & exclude)
                independent = distinct and name not in dependent
                actual_work = self._step_actual_models(run_id, work["id"]) if work else frozenset()
                actual_review = self._step_actual_models(run_id, review_step["id"])
                if not actual_work or not actual_review or actual_review & actual_work:
                    independent = False
                    dependent.add(name)
                self.db.finish_step(
                    review_step["id"],
                    "done",
                    {
                        **review.model_dump(),
                        "work_step_id": work["id"] if work else None,
                        "independent": independent,
                        "acceptance_valid": review_passed(
                            review.model_dump(), spec.acceptance_criteria
                        ),
                        "review_reason": review_reason(
                            review.model_dump(), spec.acceptance_criteria
                        ),
                    },
                )
        return self._apply_reviews(run, spec, reviews, impl_name, dependent)

    def _step_models(self, run_id: str, step_id: int) -> frozenset[str]:
        """Router models that answered in one step (Autopilot or worker gateway calls)."""
        return frozenset(
            str(c["requested_model"])
            for c in self.db.model_calls(run_id)
            if c["step_id"] == step_id and c["billing"] == "api" and c["outcome"] == "ok"
        )

    def _step_actual_models(self, run_id: str, step_id: int) -> frozenset[str]:
        values = [
            model_identity(c["actual_model"])
            for c in self.db.model_calls(run_id)
            if c["step_id"] == step_id and c["outcome"] == "ok" and c["billing"] != "external"
        ]
        if not values or any(v is None for v in values):
            return frozenset()
        return frozenset(v for v in values if v is not None)

    def _apply_reviews(
        self,
        run: dict[str, Any],
        spec: TaskSpec,
        reviews: dict[str, Review],
        implementer: str,
        dependent: set[str],
    ) -> str:
        run_id = run["id"]

        def passes(r: Review) -> bool:
            return review_passed(r.model_dump(), spec.acceptance_criteria)

        if not reviews:
            return self._needs_decision(run_id, "Review result is missing.", "review")

        for name, review in reviews.items():
            reason = review_reason(review.model_dump(), spec.acceptance_criteria)
            if reason:
                return self._needs_decision(run_id, f"Review by {name}: {reason}", "review")

        for name, r in reviews.items():
            if r.verdict == "accept" and not passes(r):
                self.db.event(
                    run_id,
                    "warning",
                    self.db.msg("{name} answered accept but has blocking findings", name=name),
                )
        if all(passes(r) for r in reviews.values()):
            independent = [n for n in reviews if n not in dependent]
            if not independent:
                return self._needs_decision(
                    run_id,
                    f"no independent reviewer for {implementer}'s change; confirm acceptance",
                    "self_review",
                )
            if not self._accept(
                run,
                self.db.msg("The review accepted the change ({names})", names=", ".join(reviews)),
            ):
                return "pause"
            self._propose_memory(run_id)
            return "pause"
        tag = len(reviews) > 1
        feedback: list[str] = []
        for name, r in reviews.items():
            prefix = f"[{name}] " if tag else ""
            feedback += [
                f"- {prefix}[{f.severity}] {f.file}: {f.problem} -> {f.fix}" for f in r.findings
            ]
            feedback += [
                f"- {prefix}Criterion not met: {c.criterion} ({c.evidence})"
                for c in r.criteria
                if not c.met
            ]
        rejected = [n for n, r in reviews.items() if r.verdict == "reject"]
        if rejected:
            self.db.update_run(run_id, feedback_json=json.dumps(feedback))
            summaries = "; ".join(f"{n}: {reviews[n].summary}" for n in rejected)
            return self._needs_decision(run_id, f"reviewer rejected the approach: {summaries}")
        return self._next_iteration_or_decide(run, spec, feedback, "review requested changes")

    def _accept(self, run: dict[str, Any], message: str) -> bool:
        run_id = run["id"]
        root = Path(run["workspace"])
        patch = workspace.diff(root, run["source_commit"])
        if secret_findings(patch):
            self._needs_decision(run_id, "Sensitive diff withheld before acceptance.", "sensitive")
            return False
        patch_path = root.parent / f"{run_id}.patch"
        patch_path.write_text(patch, encoding="utf-8", newline="")
        self.db.update_run(run_id, state=RunState.ACCEPTED, finished_at=time.time(), pending=None)
        self.db.event(run_id, "accepted", message, {"patch": str(patch_path)})
        return True

    def memory_retry(self, run_id: str) -> None:
        """Propose the memory update again for an accepted run (on click)."""
        run = self._require(run_id)
        if run["state"] != RunState.ACCEPTED:
            raise ValueError("memory proposals are only for an accepted change")
        spec = TaskSpec.model_validate_json(run["spec_json"])
        if not spec.project_note:
            raise ValueError("this run did not continue a project note")
        if self.vault() is None:
            raise ValueError("the memory folder is not configured or missing")
        threading.Thread(
            target=self._propose_memory, args=(run_id,), name="studio-memory", daemon=True
        ).start()

    def _propose_memory(self, run_id: str) -> None:
        """After acceptance: propose project-note and handoff updates.

        Autopilot writes the proposal (strongest lead, submit-only call). If it fails, Studio
        builds one from facts it checked itself, so an accepted run always ends with a
        proposal to approve. Never blocks or changes the accepted run.
        """
        try:
            run = self._require(run_id)
            spec = TaskSpec.model_validate_json(run["spec_json"])
            vault = self.vault()
            if not spec.project_note or vault is None:
                return
            note = vault.read(spec.project_note)
            review = self.db.last_step(run_id, "review")
            summary = (
                json.loads(review["output_json"]).get("summary", "")
                if review and review["output_json"]
                else ""
            )
            changed = workspace.changed_files(Path(run["workspace"]), run["source_commit"])
            result: MemoryProposal | LeadError = LeadError("Autopilot is not configured")
            step: int | None = None
            if AUTOPILOT in self.leads:
                step, result = self._ask_lead(
                    run,
                    spec,
                    AUTOPILOT,
                    "memory",
                    memory_prompt(spec, (note.path, note.text), "\n".join(changed), summary),
                    MEMORY_SCHEMA,
                    MemoryProposal,
                    mode="submit",
                    complexity="hard",
                )
            author = "Autopilot"
            if isinstance(result, LeadError):
                self.db.event(
                    run_id,
                    "warning",
                    self.db.msg(
                        "Autopilot gave no memory proposal ({error}); "
                        "Studio built it from verified facts",
                        error=result,
                    ),
                )
                verify_step = self.db.last_step(run_id, "verify")
                commands = (
                    [
                        c["command"]
                        for c in json.loads(verify_step["output_json"]).get("commands", [])
                        if c.get("exit_code") == 0
                    ]
                    if verify_step and verify_step["output_json"]
                    else []
                )
                result = fallback_proposal(
                    spec.goal, changed, commands, summary, spec.title, lang=self.db.language()
                )
                author = "studio"
                step = self.db.start_step(run_id, "memory", run["iteration"], "studio")
            assert step is not None  # the Autopilot step, or the fallback step above
            proposals = build_proposals(
                vault, result, note.path, run_id, spec.title, author=author, lang=self.db.language()
            )
            self.db.finish_step(
                step,
                "done",
                {"memory": result.model_dump(), "proposals": proposals, "author": author},
            )
            self.db.event(
                run_id,
                "vault",
                self.db.msg("The memory proposal is ready; it is written only after approval"),
                {"proposals": len(proposals), "author": author},
            )
        except Exception as exc:  # the run is already accepted; this is a convenience
            self.db.event(
                run_id, "warning", self.db.msg("The memory proposal failed: {error}", error=exc)
            )

    def _next_iteration_or_decide(
        self, run: dict[str, Any], spec: TaskSpec, feedback: list[str], why: str
    ) -> str:
        run_id = run["id"]
        self.db.update_run(run_id, feedback_json=json.dumps(feedback))
        fresh = self._require(run_id)
        limit = spec.budget.max_iterations + fresh["escalations"]
        iteration = fresh["iteration"]
        if iteration >= limit:
            return self._escalate_or_decide(
                fresh, spec, f"{why}; iteration limit {limit} reached", feedback
            )
        self.db.update_run(run_id, phase="work", iteration=iteration + 1)
        self.db.event(
            run_id, "rework", self.db.msg("{why}; next iteration {n}", why=why, n=iteration + 1)
        )
        return "next"


def invalid_commands(spec: TaskSpec, root: Path) -> list[str]:
    """Acceptance commands Studio's policy would refuse to run."""
    sandbox = Sandbox(root, spec.allowed_paths or ["*"], spec.budget.command_timeout_s)
    bad: list[str] = []
    for command in spec.acceptance_commands:
        try:
            sandbox.check_command(command)
        except Denied as exc:
            bad.append(f"{command} ({exc})")
    return bad


def draft_message(draft: Draft, lang: str | None = None) -> str:
    lines = [tr(lang, "Brief: **{title}**", title=draft.title), "", draft.goal, ""]
    lines += [tr(lang, "May change:")] + [f"- `{p}`" for p in draft.allowed_paths]
    lines += ["", tr(lang, "Criteria:")] + [f"- {c}" for c in draft.acceptance_criteria]
    lines += ["", tr(lang, "Verification commands:")]
    lines += [f"- `{c}`" for c in draft.acceptance_commands]
    if draft.notes:
        lines += ["", draft.notes]
    if draft.questions:
        lines += ["", tr(lang, "Questions:")] + [f"- {q}" for q in draft.questions]
    return "\n".join(lines)


def external_brief(spec: TaskSpec, plan: Plan | None, feedback: list[str], root: Path) -> str:
    """Prompt the user pastes into the external agent. English: it is read by a model."""
    lines = [
        f"# Task: {spec.title}",
        "",
        spec.goal,
        "",
        "## Scope",
        "Change only files matching these patterns (other changes fail verification):",
        *[f"- `{p}`" for p in spec.allowed_paths],
        "",
        "## Acceptance criteria",
        *[f"- {c}" for c in spec.acceptance_criteria],
        "",
        "## Acceptance commands (Studio runs them itself afterwards)",
        *[f"- `{c}`" for c in spec.acceptance_commands],
    ]
    if plan:
        lines += ["", "## Plan", plan.summary, *[f"{i}. {s}" for i, s in enumerate(plan.steps, 1)]]
    if feedback:
        lines += ["", "## Fix these problems from the previous attempt", *feedback]
    lines += [
        "",
        "## Rules",
        "- Do not commit, push, rebase or reset; leave changes in the working tree.",
        "- Do not read or create credential files.",
        "",
        f"<!-- run in: {root} -->",
    ]
    return "\n".join(lines) + "\n"
