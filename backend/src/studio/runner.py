"""Control plane: drives a run through setup → plan → work → verify → review.

`run.phase` is the next phase to execute; it only advances after the phase's step has been
recorded as done. `run.state` is what the user sees. A run is picked up only when its state
is `queued`, so after a crash nothing continues until the user resumes it.
"""

from __future__ import annotations

import json
import threading
import time
import traceback
from pathlib import Path
from typing import Any

from studio import external, workspace
from studio.agents.leads import Lead, LeadError, review_prompt
from studio.db import Database
from studio.domain import Plan, Review, RunState, TaskSpec
from studio.gateway import ChatGateway
from studio.routing import ModelRouter
from studio.sandbox import Sandbox, sensitive_files
from studio.verify import failure_feedback, verify
from studio.worker.loop import Worker, WorkerStopped

PHASE_STATE = {
    "setup": RunState.PLANNING,
    "plan": RunState.PLANNING,
    "work": RunState.WORKING,
    "verify": RunState.VERIFYING,
    "review": RunState.REVIEWING,
}


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
        if run and run["state"] in (RunState.QUEUED, RunState.AWAITING_EXTERNAL):
            # Resume re-runs the work phase: the copy is reset and handed off again.
            self.db.update_run(run_id, state=RunState.INTERRUPTED)

    def resume(self, run_id: str) -> None:
        run = self._require(run_id)
        if run["state"] != RunState.INTERRUPTED:
            raise ValueError(f"run is {run['state']}, only interrupted runs can resume")
        if run["phase"] == "work" and run["workspace"] and run["iteration_base"]:
            # The interrupted worker may have left half-written files: start the step clean.
            workspace.reset_to(Path(run["workspace"]), run["iteration_base"])
        self.db.update_run(run_id, state=RunState.QUEUED, cancel_requested=0)
        self.db.event(run_id, "decision", f"Pokračování od fáze {run['phase']}")
        self.wake()

    def rework(self, run_id: str, note: str = "") -> None:
        run = self._require(run_id)
        if run["state"] != RunState.NEEDS_DECISION:
            raise ValueError("rework is possible only when a decision is needed")
        if not run["workspace"]:
            raise ValueError("run has no working copy; abandon it and create a new task")
        feedback = json.loads(run["feedback_json"] or "[]")
        if note.strip():
            feedback.append(f"- Note from the user: {note.strip()}")
        self.db.update_run(
            run_id,
            state=RunState.QUEUED,
            phase="work",
            iteration=run["iteration"] + 1,
            feedback_json=json.dumps(feedback),
            decision_reason=None,
            cancel_requested=0,
        )
        self.db.event(run_id, "decision", "Uživatel vrátil úkol k další opravě")
        self.wake()

    def abandon(self, run_id: str) -> None:
        run = self._require(run_id)
        if run["state"] in (RunState.ACCEPTED, RunState.ABANDONED):
            raise ValueError(f"run is already {run['state']}")
        if run["state"] not in (
            RunState.NEEDS_DECISION,
            RunState.INTERRUPTED,
            RunState.QUEUED,
            RunState.AWAITING_EXTERNAL,
        ):
            raise ValueError("stop the run before abandoning it")
        self.db.update_run(run_id, state=RunState.ABANDONED, finished_at=time.time())
        self.db.event(run_id, "decision", "Uživatel úkol opustil")

    def external_done(self, run_id: str, note: str = "") -> None:
        """The user says the external agent finished. Its work is a claim until verified."""
        run = self._require(run_id)
        if run["state"] != RunState.AWAITING_EXTERNAL:
            raise ValueError(f"run is {run['state']}, not waiting for an external agent")
        spec = TaskSpec.model_validate_json(run["spec_json"])
        root = Path(run["workspace"])
        handoff = self.db.last_step(run_id, "handoff")
        step = self.db.start_step(
            run_id, "work", run["iteration"], f"external:{spec.external_agent}"
        )
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
            "claim": f"{spec.external_agent} run by the user; nothing was verified by Studio yet",
        }
        self.db.finish_step(step, "done", output)
        self.db.record_model_call(
            run_id=run_id,
            step_id=step,
            role="worker",
            requested_model=f"{spec.external_agent}-external",
            actual_model=f"{spec.external_agent}-external",
            provider=f"{spec.external_agent} (own login, ads; usage unknown)",
            outcome="ok",
            latency_ms=int((time.time() - started) * 1000) if started else None,
        )
        self.db.update_run(run_id, state=RunState.QUEUED, phase="verify")
        self.db.event(
            run_id, "work", f"Uživatel ohlásil konec práce {spec.external_agent}; ověřuji"
        )
        self.wake()

    def open_external_terminal(self, run_id: str) -> None:
        """Open a terminal in the working copy. The user runs the agent; Studio does not."""
        run = self._require(run_id)
        if run["state"] != RunState.AWAITING_EXTERNAL:
            raise ValueError(f"run is {run['state']}, not waiting for an external agent")
        spec = TaskSpec.model_validate_json(run["spec_json"])
        try:
            external.open_terminal(Path(run["workspace"]), spec.external_agent)
        except external.TerminalError as exc:
            raise ValueError(str(exc)) from exc
        self.db.event(run_id, "handoff", f"Otevřen terminál pro {spec.external_agent}")

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

    def _needs_decision(self, run_id: str, reason: str) -> str:
        self.db.update_run(run_id, state=RunState.NEEDS_DECISION, decision_reason=reason)
        self.db.event(run_id, "needs_decision", reason)
        return "pause"

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
            run_id, workspace=str(dest), source_commit=head, iteration_base=head, phase="plan"
        )
        self.db.event(run_id, "setup", f"Izolovaná pracovní kopie z commitu {head[:10]}")
        return "next"

    def _phase_plan(self, run: dict[str, Any], spec: TaskSpec) -> str:
        run_id = run["id"]
        if spec.planner == "none":
            self.db.update_run(run_id, phase="work")
            return "next"
        lead = self.leads[spec.planner]
        step = self.db.start_step(run_id, "plan", run["iteration"], lead.name)
        self.db.event(run_id, "plan", f"{lead.name} připravuje plán")
        started = time.monotonic()
        try:
            plan = lead.plan(spec, Path(run["workspace"]), lambda: self._should_stop(run_id))
        except LeadError as exc:
            if exc.kind == "cancelled":
                self.db.finish_step(step, "interrupted", error=str(exc))
                raise Stopped() from exc
            self.db.finish_step(step, "failed", error=str(exc))
            self._record_lead_call(run_id, step, lead.name, "plan", started, exc.kind, str(exc))
            return self._needs_decision(run_id, f"planning by {lead.name} failed: {exc}")
        self._record_lead_call(run_id, step, lead.name, "plan", started, "ok", None)
        self.db.finish_step(step, "done", plan.model_dump())
        self.db.update_run(run_id, plan_json=plan.model_dump_json(), phase="work")
        if plan.recommended_implementer == "strong_agent":
            self.db.event(run_id, "warning", "Lead doporučuje silného agenta; zkouší se worker")
        return "next"

    def _phase_work(self, run: dict[str, Any], spec: TaskSpec) -> str:
        run_id = run["id"]
        root = Path(run["workspace"])
        base = workspace.commit_all(root, f"studio: before iteration {run['iteration']}")
        self.db.update_run(run_id, iteration_base=base)
        plan = Plan.model_validate_json(run["plan_json"]) if run["plan_json"] else None
        feedback = json.loads(run["feedback_json"] or "[]")
        if spec.implementer == "external":
            return self._handoff(run, spec, plan, feedback)
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
            return self._needs_decision(
                run_id, f"worker stopped: {outcome.status} {outcome.detail}".strip()
            )
        self.db.finish_step(step, "done", outcome.as_dict())
        self.db.update_run(run_id, phase="verify")
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
            step, "done", {"agent": spec.external_agent, "brief": str(brief_path), "text": brief}
        )
        self.db.update_run(run_id, state=RunState.AWAITING_EXTERNAL)
        self.db.event(
            run_id,
            "handoff",
            f"Čeká na {spec.external_agent}: spusť ho v pracovní kopii a pak klikni Hotovo",
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
        lead = self.leads[spec.reviewer]
        step = self.db.start_step(run_id, "review", run["iteration"], lead.name)
        self.db.event(run_id, "review", f"{lead.name} nezávisle reviduje diff")
        prompt = review_prompt(spec, verification["diff"], verification, summary)
        started = time.monotonic()
        try:
            review = lead.review(
                spec, Path(run["workspace"]), prompt, lambda: self._should_stop(run_id)
            )
        except LeadError as exc:
            if exc.kind == "cancelled":
                self.db.finish_step(step, "interrupted", error=str(exc))
                raise Stopped() from exc
            self.db.finish_step(step, "failed", error=str(exc))
            self._record_lead_call(run_id, step, lead.name, "review", started, exc.kind, str(exc))
            return self._needs_decision(run_id, f"review by {lead.name} failed: {exc}")
        self._record_lead_call(run_id, step, lead.name, "review", started, "ok", None)
        self.db.finish_step(step, "done", review.model_dump())
        return self._apply_review(run, spec, review)

    def _apply_review(self, run: dict[str, Any], spec: TaskSpec, review: Review) -> str:
        run_id = run["id"]
        blocking = [f for f in review.findings if f.severity in ("blocker", "major")]
        unmet = [c for c in review.criteria if not c.met]
        if review.verdict == "accept" and not blocking and not unmet:
            root = Path(run["workspace"])
            patch = workspace.diff(root, run["source_commit"])
            patch_path = root.parent / f"{run_id}.patch"
            patch_path.write_text(patch, encoding="utf-8", newline="")
            self.db.update_run(run_id, state=RunState.ACCEPTED, finished_at=time.time())
            self.db.event(run_id, "accepted", "Review přijalo změnu", {"patch": str(patch_path)})
            return "pause"
        if review.verdict == "accept":
            self.db.event(run_id, "warning", "Reviewer napsal accept, ale má blokující nálezy")
        feedback = [f"- [{f.severity}] {f.file}: {f.problem} -> {f.fix}" for f in review.findings]
        feedback += [f"- Criterion not met: {c.criterion} ({c.evidence})" for c in unmet]
        if review.verdict == "reject":
            self.db.update_run(run_id, feedback_json=json.dumps(feedback))
            return self._needs_decision(run_id, f"reviewer rejected the approach: {review.summary}")
        return self._next_iteration_or_decide(run, spec, feedback, "review requested changes")

    def _next_iteration_or_decide(
        self, run: dict[str, Any], spec: TaskSpec, feedback: list[str], why: str
    ) -> str:
        run_id = run["id"]
        self.db.update_run(run_id, feedback_json=json.dumps(feedback))
        if run["iteration"] >= spec.budget.max_iterations:
            return self._needs_decision(
                run_id, f"{why}; iteration limit {spec.budget.max_iterations} reached"
            )
        self.db.update_run(run_id, phase="work", iteration=run["iteration"] + 1)
        self.db.event(run_id, "rework", f"{why}; vracím workerovi (iterace {run['iteration'] + 1})")
        return "next"

    def _record_lead_call(
        self,
        run_id: str,
        step: int,
        lead: str,
        role: str,
        started: float,
        outcome: str,
        error: str | None,
    ) -> None:
        self.db.record_model_call(
            run_id=run_id,
            step_id=step,
            role=role,
            requested_model=f"{lead}-cli",
            actual_model=f"{lead}-cli",
            provider=f"{lead} (own login)",
            outcome=outcome,
            error=(error or "")[:500] or None,
            latency_ms=int((time.monotonic() - started) * 1000),
        )


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
