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
    PLAN_SCHEMA,
    REVIEW_SCHEMA,
    draft_prompt,
    implement_prompt,
    merge_prompt,
    plan_prompt,
    review_prompt,
)
from studio.db import Database
from studio.domain import (
    LEADS,
    Draft,
    ImplementReport,
    LeadConfig,
    Message,
    Plan,
    Review,
    RunState,
    TaskSpec,
)
from studio.gateway import ChatGateway
from studio.routing import ModelRouter
from studio.sandbox import Denied, Sandbox, sensitive_files
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
# Worker outcomes that mean "the cheap path cannot do this", not "it did something partial".
ESCALATE_WORKER = frozenset({"no_model", "gateway_down", "error", "budget"})
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
    ) -> None:
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
        self.db.event(run_id, "decision", "Uživatel požádal o přerušení")
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
        if run["phase"] == "work" and run["workspace"] and run["iteration_base"]:
            # The interrupted implementer may have left half-written files: start clean.
            workspace.reset_to(Path(run["workspace"]), run["iteration_base"])
        self.db.update_run(run_id, state=RunState.QUEUED, cancel_requested=0)
        self.db.event(run_id, "decision", f"Pokračování od fáze {run['phase']}")
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
        self.db.event(run_id, "decision", "Uživatel vrátil úkol k další opravě")

    def retry(self, run_id: str) -> None:
        """Run the phase that failed again (e.g. a lead CLI error or a lost login)."""
        run = self._require(run_id)
        if run["state"] != RunState.NEEDS_DECISION:
            raise ValueError("retry is possible only when a decision is needed")
        if run["phase"] == "work" and run["workspace"] and run["iteration_base"]:
            workspace.reset_to(Path(run["workspace"]), run["iteration_base"])
        self._requeue(run_id)
        self.db.event(run_id, "decision", f"Opakuji fázi {run['phase']}")

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
        self.db.event(run_id, "decision", "Odpověď uživatele; lead pokračuje")

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
        self.db.event(run_id, "decision", "Uživatel schválil zadání")

    def takeover(self, run_id: str, lead: str | None = None, note: str = "") -> None:
        """A strong lead implements from the next iteration on (manual escalation)."""
        run = self._require(run_id)
        if run["state"] not in (RunState.NEEDS_DECISION, RunState.INTERRUPTED):
            raise ValueError("takeover is possible only when the run is stopped")
        if not run["workspace"] or run["phase"] in ("setup", "draft", "approve"):
            raise ValueError("the task is not ready for implementation yet")
        spec = TaskSpec.model_validate_json(run["spec_json"])
        name = lead or spec.orchestration.primary
        if name not in LEADS:
            raise ValueError(f"unknown lead {name}")
        feedback = json.loads(run["feedback_json"] or "[]")
        if note.strip():
            feedback.append(f"- Note from the user: {note.strip()}")
            self.db.message(run_id, "user", note.strip())
        if run["iteration_base"]:
            workspace.reset_to(Path(run["workspace"]), run["iteration_base"])
        self._requeue(
            run_id,
            phase="work",
            iteration=run["iteration"] + 1,
            implementer=name,
            escalations=run["escalations"] + 1,
            feedback_json=json.dumps(feedback),
        )
        self.db.event(run_id, "escalation", f"Implementaci přebírá {name} (ručně)")
        self.db.message(run_id, "studio", f"Implementaci přebírá {name}.")

    def keep_worker(self, run_id: str) -> None:
        run = self._require(run_id)
        if (
            run["state"] != RunState.NEEDS_DECISION
            or run["pending"] != "takeover"
            or run["phase"] != "work"
        ):
            raise ValueError("no takeover decision is pending")
        if run["iteration_base"]:
            workspace.reset_to(Path(run["workspace"]), run["iteration_base"])
        self._requeue(run_id, implementer="worker")
        self.db.event(run_id, "decision", "Uživatel ponechal levného workera")

    def accept(self, run_id: str) -> None:
        """The user confirms a change that only a self-review approved (solo mode)."""
        run = self._require(run_id)
        if run["state"] != RunState.NEEDS_DECISION or run["pending"] != "self_review":
            raise ValueError("manual acceptance is only for a self-reviewed change")
        self._accept(run, "Uživatel potvrdil změnu schválenou jen sebe-review")

    def allow_sensitive(self, run_id: str) -> None:
        run = self._require(run_id)
        if run["state"] != RunState.NEEDS_DECISION or run["pending"] != "sensitive":
            raise ValueError("no sensitive-file decision is pending")
        self.db.event(run_id, "decision", "Uživatel potvrdil, že nalezené soubory nejsou tajné")
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
        self.db.event(run_id, "decision", "Uživatel úkol opustil")

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
            actual_model=f"{EXTERNAL_AGENT}-external",
            provider=f"{EXTERNAL_AGENT} (own login, ads; usage unknown)",
            outcome="ok",
            billing="external",
            latency_ms=int((time.time() - started) * 1000) if started else None,
        )
        self.db.update_run(run_id, state=RunState.QUEUED, phase="verify")
        self.db.event(run_id, "work", f"Uživatel ohlásil konec práce {EXTERNAL_AGENT}; ověřuji")
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
        self.db.event(run_id, "handoff", f"Otevřen terminál pro {EXTERNAL_AGENT}")

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
            self.db.event(run_id, "interrupted", "Běh přerušen; lze pokračovat nebo opustit")
        except Exception as exc:  # last resort: never leave a run looking active
            self.db.event(
                run_id, "crash", f"Neočekávaná chyba: {exc}", {"tb": traceback.format_exc()[-3000:]}
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
            if m["author"] in ("user", "claude", "codex", "studio")
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
        lead = self.leads[name]
        step = self.db.start_step(run_id, kind, run["iteration"], actor or name)
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
            result = validate(model, lead.ask(prompt, schema, Path(run["workspace"]), ctx, mode))
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
        self.db.record_model_call(
            run_id=run_id,
            step_id=step,
            role=kind,
            requested_model=f"{name}:{config.model or 'default'}"
            + (f"@{config.effort}" if config.effort else ""),
            actual_model=ctx.usage.actual_model,
            provider=f"{name} CLI (own login)",
            tokens_in=ctx.usage.tokens_in,
            tokens_out=ctx.usage.tokens_out,
            cost_usd=ctx.usage.cost_usd,
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
    ) -> dict[str, T | LeadError]:
        """Ask several leads the same thing in parallel; each gets its own step."""
        if len(names) == 1:
            name = names[0]
            return {name: self._ask_lead(run, spec, name, kind, prompt(name), schema, model)[1]}
        with ThreadPoolExecutor(max_workers=len(names), thread_name_prefix="lead") as pool:
            futures = {
                n: pool.submit(self._ask_lead, run, spec, n, kind, prompt(n), schema, model)
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
        self.db.event(run_id, "setup", f"Izolovaná pracovní kopie z commitu {head[:10]}")
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
        self.db.event(run_id, "draft", f"{name} navrhuje zadání")
        _step, result = self._ask_lead(
            run,
            spec,
            name,
            "draft",
            draft_prompt(spec, self._messages(run_id)),
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
        text = draft_message(result)
        bad = invalid_commands(updated, Path(run["workspace"]))
        if bad:
            text += "\n\nStudio tyto příkazy nespustí: " + "; ".join(bad)
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
            self.db.event(run_id, "decision", "Zadání schváleno automaticky")
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
            "Lead má otázky k zadání" if questions else "Zadání čeká na schválení",
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
        self.db.event(run_id, "plan", " a ".join(planners) + " připravuje plán")
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
        if len(plans) > 1:
            self.db.event(run_id, "plan", f"{orch.primary} slučuje oba plány")
            _step, merged = self._ask_lead(
                run,
                spec,
                orch.primary,
                "plan",
                merge_prompt(spec, plans),
                PLAN_SCHEMA,
                Plan,
                actor=f"{orch.primary}:merge",
            )
            if isinstance(merged, LeadError):
                return self._needs_decision(run_id, f"merging plans failed: {merged}")
            plan = merged
        else:
            plan = next(iter(plans.values()))
        self.db.update_run(run_id, plan_json=plan.model_dump_json(), phase="work")
        if plan.questions:
            self.db.message(
                run_id,
                orch.primary,
                "Před implementací potřebuji odpovědět:\n"
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
            lead = spec.orchestration.primary
            if spec.escalation == "auto":
                self.db.update_run(run["id"], implementer=lead)
                self.db.event(run["id"], "escalation", f"Plán doporučuje silného agenta: {lead}")
                return lead
            if spec.escalation == "ask":
                return "ask"
        return "worker"

    def _phase_work(self, run: dict[str, Any], spec: TaskSpec) -> str:
        run_id = run["id"]
        root = Path(run["workspace"])
        base = workspace.commit_all(root, f"studio: before iteration {run['iteration']}")
        self.db.update_run(run_id, iteration_base=base)
        plan = Plan.model_validate_json(run["plan_json"]) if run["plan_json"] else None
        feedback = json.loads(run["feedback_json"] or "[]")
        who = self._implementer(run, spec, plan)
        if who == "ask":
            return self._needs_decision(
                run_id,
                f"plan recommends a strong agent; hand over to {spec.orchestration.primary}?",
                "takeover",
            )
        if who == "freebuff":
            return self._handoff(run, spec, plan, feedback)
        if who in LEADS:
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
        )
        workspace.commit_all(root, f"studio: {name} iteration {run['iteration']}")
        if isinstance(result, LeadError):
            return self._needs_decision(run_id, f"implementation by {name} failed: {result}")
        self.db.update_run(run_id, phase="verify")
        return "next"

    def _worker_work(
        self, run: dict[str, Any], spec: TaskSpec, plan: Plan | None, feedback: list[str]
    ) -> str:
        run_id = run["id"]
        root = Path(run["workspace"])
        step = self.db.start_step(run_id, "work", run["iteration"], "worker")
        self.db.event(run_id, "work", f"Worker začíná iteraci {run['iteration']}")
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
            if outcome.status in ("turn_limit", "time_limit", "error") and outcome.files_written:
                # Partial work is still worth an independent check rather than a guess.
                self.db.event(run_id, "warning", f"Worker skončil stavem {outcome.status}; ověřuji")
                self.db.update_run(run_id, phase="verify")
                return "next"
            why = f"worker stopped: {outcome.status} {outcome.detail}".strip()
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
        lead = spec.orchestration.primary
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
        workspace.reset_to(Path(run["workspace"]), fresh["iteration_base"])
        self.db.update_run(
            run_id,
            implementer=lead,
            escalations=fresh["escalations"] + 1,
            phase="work",
            iteration=fresh["iteration"] + (0 if same_iteration else 1),
            feedback_json=json.dumps(feedback),
        )
        self.db.event(run_id, "escalation", f"{why}; implementaci přebírá {lead}")
        self.db.message(run_id, "studio", f"Levný worker nestačil ({why}). Přebírá {lead}.")
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
            f"Čeká na {EXTERNAL_AGENT}: spusť ho v pracovní kopii a pak klikni Hotovo",
            {"workspace": str(root), "brief": str(brief_path)},
        )
        return "pause"

    def _phase_verify(self, run: dict[str, Any], spec: TaskSpec) -> str:
        run_id = run["id"]
        step = self.db.start_step(run_id, "verify", run["iteration"], "studio")
        result = verify(spec, Path(run["workspace"]), run["source_commit"])
        result["diff"] = workspace.diff(Path(run["workspace"]), run["source_commit"])
        self.db.finish_step(step, "done" if result["passed"] else "failed", result)
        if result["passed"]:
            self.db.event(run_id, "verify", "Nezávislé ověření prošlo")
            self.db.update_run(run_id, phase="review")
            return "next"
        feedback = failure_feedback(result)
        self.db.event(run_id, "verify", "Nezávislé ověření selhalo", {"feedback": feedback})
        return self._next_iteration_or_decide(
            run, spec, feedback, "independent verification failed"
        )

    def _phase_review(self, run: dict[str, Any], spec: TaskSpec) -> str:
        run_id = run["id"]
        verification = json.loads(self.db.last_step(run_id, "verify")["output_json"])  # type: ignore[index]
        work = self.db.last_step(run_id, "work")
        summary = (
            json.loads(work["output_json"]).get("summary", "")
            if work and work["output_json"]
            else ""
        )
        implementer = (work or {}).get("actor") or "worker"
        impl_name = implementer.removeprefix("lead:")
        reviewers = spec.orchestration.reviewers(impl_name)
        self.db.event(run_id, "review", " a ".join(reviewers) + " nezávisle reviduje diff")
        prompt = review_prompt(spec, verification["diff"], verification, summary, implementer)
        results = self._ask_leads(
            run, spec, list(reviewers), "review", lambda _n: prompt, REVIEW_SCHEMA, Review
        )
        failed = {n: r for n, r in results.items() if isinstance(r, LeadError)}
        if failed:
            # Never accept on fewer reviews than the mode requires.
            return self._needs_decision(
                run_id, "; ".join(f"review by {n} failed: {e}" for n, e in failed.items())
            )
        reviews = {n: r for n, r in results.items() if isinstance(r, Review)}
        return self._apply_reviews(run, spec, reviews, impl_name)

    def _apply_reviews(
        self, run: dict[str, Any], spec: TaskSpec, reviews: dict[str, Review], implementer: str
    ) -> str:
        run_id = run["id"]

        def passes(r: Review) -> bool:
            blocking = [f for f in r.findings if f.severity in ("blocker", "major")]
            return r.verdict == "accept" and not blocking and all(c.met for c in r.criteria)

        for name, r in reviews.items():
            if r.verdict == "accept" and not passes(r):
                self.db.event(run_id, "warning", f"{name} napsal accept, ale má blokující nálezy")
        if all(passes(r) for r in reviews.values()):
            independent = [n for n in reviews if n != implementer]
            if not independent:
                return self._needs_decision(
                    run_id,
                    f"only {implementer} reviewed its own change; confirm acceptance",
                    "self_review",
                )
            self._accept(run, "Review přijalo změnu (" + ", ".join(reviews) + ")")
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

    def _accept(self, run: dict[str, Any], message: str) -> None:
        run_id = run["id"]
        root = Path(run["workspace"])
        patch = workspace.diff(root, run["source_commit"])
        patch_path = root.parent / f"{run_id}.patch"
        patch_path.write_text(patch, encoding="utf-8", newline="")
        self.db.update_run(run_id, state=RunState.ACCEPTED, finished_at=time.time(), pending=None)
        self.db.event(run_id, "accepted", message, {"patch": str(patch_path)})

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
        self.db.event(run_id, "rework", f"{why}; další iterace {iteration + 1}")
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


def draft_message(draft: Draft) -> str:
    lines = [f"Návrh zadání: **{draft.title}**", "", draft.goal, "", "Smí měnit:"]
    lines += [f"- `{p}`" for p in draft.allowed_paths]
    lines += ["", "Kritéria:"] + [f"- {c}" for c in draft.acceptance_criteria]
    lines += ["", "Ověřovací příkazy:"] + [f"- `{c}`" for c in draft.acceptance_commands]
    if draft.notes:
        lines += ["", draft.notes]
    if draft.questions:
        lines += ["", "Otázky:"] + [f"- {q}" for q in draft.questions]
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
