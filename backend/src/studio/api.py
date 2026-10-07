"""HTTP API for the UI. Binds to 127.0.0.1 only.

CSRF guard: every mutating request must carry `X-Studio: 1` (a custom header forces a CORS
preflight, which this server never approves) and, if an Origin is present, it must be the
server's own origin or the Vite dev server.
"""

from __future__ import annotations

import asyncio
import json
import tempfile
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from studio import __version__
from studio.agents.api_lead import ApiLead
from studio.agents.leads import CallContext, ClaudeLead, CodexLead, Lead, LeadError
from studio.agents.prompts import PROBE_SCHEMA, probe_prompt
from studio.config import Settings
from studio.db import Database
from studio.domain import AUTOPILOT, LEADS, Effort, LeadConfig, TaskSpec
from studio.gateway import ChatGateway, OmniRouteGateway
from studio.memory import project_memory
from studio.preferences import Catalog, Defaults, Preferences
from studio.routing import ModelRouter
from studio.runner import Runner
from studio.sandbox import Denied

UI_DIST = Path(__file__).resolve().parents[3] / "frontend" / "dist"
ACTIONS = (
    "cancel",
    "resume",
    "rework",
    "retry",
    "abandon",
    "reply",
    "approve",
    "takeover",
    "keep-worker",
    "accept",
    "allow-sensitive",
    "external-done",
    "open-terminal",
    "apply",
    "vault-write",
    "vault-discard",
)


class ActionBody(BaseModel):
    note: str = Field("", max_length=20_000)
    text: str = Field("", max_length=20_000)
    lead: Literal["autopilot", "claude", "codex"] | None = None
    edits: dict[str, Any] | None = None
    index: int | None = None  # which memory proposal (vault-write / vault-discard)


class SettingsBody(BaseModel):
    defaults: Defaults | None = None
    catalog: Catalog | None = None


class ProbeBody(BaseModel):
    lead: Literal["autopilot", "claude", "codex"]
    model: str | None = None
    effort: Effort | None = None


def _decode(row: dict[str, Any], *keys: str) -> dict[str, Any]:
    out = dict(row)
    for key in keys:
        if out.get(key):
            out[key] = json.loads(out[key])
    return out


def _rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for r in rows:
        r = _decode(r, "data_json")
        r["data"] = r.pop("data_json")
        out.append(r)
    return out


def _midnight_utc() -> float:
    """OpenRouter's free daily limit resets at 00:00 UTC."""
    now = time.time()
    return now - now % 86_400


def create_app(
    settings: Settings,
    *,
    gateway: ChatGateway | None = None,
    leads: dict[str, Lead] | None = None,
    start_runner: bool = True,
) -> FastAPI:
    db = Database(settings.db_path)
    gw = gateway or OmniRouteGateway(
        settings.omniroute_url, settings.omniroute_api_key, settings.gateway_timeout_s
    )
    router = ModelRouter(db, settings.worker_models)
    lead_map: dict[str, Lead] = leads or {
        AUTOPILOT: ApiLead(db, gw, router, lambda: settings.vault()),
        "claude": ClaudeLead(settings.claude_bin),
        "codex": CodexLead(settings.codex_bin),
    }
    prefs = Preferences(db, settings.codex_model)
    runner = Runner(db, gw, router, lead_map, settings.workspaces_dir, settings.vault)
    allowed_origins = {
        f"http://{settings.host}:{settings.port}",
        f"http://localhost:{settings.port}",
        "http://127.0.0.1:5173",
        "http://localhost:5173",
    }

    @asynccontextmanager
    async def lifespan(_: FastAPI):  # type: ignore[no-untyped-def]
        if start_runner:
            runner.start()
        yield
        runner.stop()

    app = FastAPI(title="AI Bridge Studio", version=__version__, lifespan=lifespan)
    app.state.db = db
    app.state.runner = runner

    @app.middleware("http")
    async def csrf_guard(request: Request, call_next):  # type: ignore[no-untyped-def]
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            origin = request.headers.get("origin")
            if request.headers.get("x-studio") != "1" or (origin and origin not in allowed_origins):
                return JSONResponse({"detail": "forbidden"}, status_code=403)
        return await call_next(request)

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {
            "version": __version__,
            "gateway": gw.health(),
            "leads": {name: lead.available() for name, lead in lead_map.items()},
            "worker_models": router.candidates(),
            "lead_models": router.candidates("lead", "hard"),
            "cooldowns": db.cooldowns(),
            "api_calls_today": db.api_calls_since(_midnight_utc()),
        }

    # --- preferences --------------------------------------------------------------------

    @app.get("/api/settings")
    def get_settings() -> dict[str, Any]:
        return prefs.snapshot()

    @app.put("/api/settings")
    def put_settings(body: SettingsBody) -> dict[str, Any]:
        prefs.save(body.defaults, body.catalog)
        return prefs.snapshot()

    @app.post("/api/probe")
    def probe(body: ProbeBody) -> dict[str, Any]:
        """One tiny live call: does this model + effort work with the user's CLI login?"""
        config = LeadConfig(model=body.model or None, effort=body.effort)
        ctx = CallContext(config=config, timeout_s=180, should_stop=lambda: False)
        started = time.monotonic()
        result: dict[str, Any]
        with tempfile.TemporaryDirectory(prefix="studio-probe-") as tmp:
            try:
                lead_map[body.lead].ask(probe_prompt(), PROBE_SCHEMA, Path(tmp), ctx)
                result = {"ok": True, "actual_model": ctx.usage.actual_model}
            except LeadError as exc:
                result = {"ok": False, "kind": exc.kind, "error": str(exc)[:500]}
        result["latency_ms"] = int((time.monotonic() - started) * 1000)
        result["tokens"] = ctx.usage.tokens_in + ctx.usage.tokens_out
        prefs.record_probe(f"{body.lead}:{config.model or 'default'}:{config.effort or ''}", result)
        return result

    # --- shared memory (Obsidian) ------------------------------------------------------

    @app.get("/api/vault")
    def vault_overview() -> dict[str, Any]:
        """Projects and open handoffs Studio may use. Names only; nothing is sent anywhere."""
        vault = settings.vault()
        if vault is None:
            return {"available": False, "path": str(settings.vault_path or "")}
        return {
            "available": True,
            "path": str(vault.root),
            "projects": vault.projects(),
            "handoffs": vault.open_handoffs(),
            "folders": vault.folder_counts(),
        }

    @app.get("/api/vault/project")
    def vault_project(note: str) -> dict[str, Any]:
        vault = settings.vault()
        if vault is None:
            raise HTTPException(404, "the Obsidian vault is not available")
        try:
            notes, refused = project_memory(vault, note)
        except Denied as exc:
            raise HTTPException(403, str(exc)) from exc
        main = vault.read(note)
        return {
            "note": main.path,
            "title": main.title,
            "repo_hint": vault.repo_hint(main),
            "handoffs": [path for path, _ in notes[1:]],
            "refused": refused,
            "preview": main.text[:1500],
        }

    # --- tasks and runs -----------------------------------------------------------------

    @app.post("/api/tasks", status_code=201)
    def create_task(spec: TaskSpec) -> dict[str, str]:
        repo = Path(spec.repo_path)
        if not repo.is_absolute() or not (repo / ".git").exists():
            raise HTTPException(422, "repo_path must be an absolute path to a git repository")
        if spec.project_note:
            vault = settings.vault()
            if vault is None or not vault.shareable(spec.project_note):
                raise HTTPException(422, "project_note is not a shared note in the vault")
        defaults = prefs.defaults().orchestration
        for name in LEADS:  # a lead left on "default" gets the user's preferred model
            config = spec.orchestration.config(name)
            if not config.model:
                config.model = defaults.config(name).model
        run_id = db.create_task_and_run(spec.model_dump_json())
        db.message(run_id, "user", spec.goal)
        prefs.remember_repo(str(repo))
        runner.wake()
        return {"run_id": run_id}

    @app.get("/api/runs")
    def list_runs() -> list[dict[str, Any]]:
        out = []
        for r in db.runs(200):
            spec = TaskSpec.model_validate_json(r["spec_json"])
            out.append(
                {
                    "id": r["id"],
                    "title": spec.title,
                    "repo_path": spec.repo_path,
                    "mode": spec.orchestration.mode,
                    "implementer": r["implementer"] or spec.implementer,
                    "state": r["state"],
                    "phase": r["phase"],
                    "pending": r["pending"],
                    "iteration": r["iteration"],
                    "created_at": r["created_at"],
                    "updated_at": r["updated_at"],
                    "finished_at": r["finished_at"],
                    "decision_reason": r["decision_reason"],
                }
            )
        return out

    def run_payload(run_id: str) -> dict[str, Any]:
        r = db.run(run_id)
        if not r:
            raise HTTPException(404, "run not found")
        run = _decode(r, "plan_json", "feedback_json")
        run["spec"] = TaskSpec.model_validate_json(run.pop("spec_json")).model_dump(mode="json")
        run["plan"] = run.pop("plan_json")
        run["feedback"] = run.pop("feedback_json")
        return run

    @app.get("/api/runs/{run_id}")
    def get_run(run_id: str) -> dict[str, Any]:
        run = run_payload(run_id)
        steps = [_decode(s, "output_json") for s in db.steps(run_id)]
        for s in steps:
            s["output"] = s.pop("output_json")
        return {
            "run": run,
            "steps": steps,
            "events": _rows(db.events(run_id)),
            "messages": _rows(db.messages(run_id)),
            "agent_events": _rows(db.agent_events(run_id)),
            "model_calls": db.model_calls(run_id),
            "usage": {
                "api": db.usage(run_id, "api"),
                "subscription": db.usage(run_id, "subscription"),
                "total": db.usage(run_id),
            },
        }

    @app.get("/api/runs/{run_id}/stream")
    async def stream(
        run_id: str,
        request: Request,
        events: int = 0,
        agent: int = 0,
        messages: int = 0,
        follow: bool = True,
    ) -> StreamingResponse:
        """Server-sent events: new events, agent output, messages and run state changes.

        `follow=false` sends one batch and closes (catch-up without a live connection)."""
        if not db.run(run_id):
            raise HTTPException(404, "run not found")

        async def gen() -> AsyncIterator[str]:
            cursors = {"event": events, "agent": agent, "message": messages}
            last_state: tuple[Any, ...] | None = None
            yield "retry: 2000\n\n"
            while not await request.is_disconnected():
                batch = await asyncio.to_thread(
                    lambda: (
                        _rows(db.events(run_id, cursors["event"])),
                        _rows(db.agent_events(run_id, cursors["agent"], 500)),
                        _rows(db.messages(run_id, cursors["message"])),
                        db.run(run_id),
                    )
                )
                evs, agents, msgs, run = batch
                for kind, rows, key in (("event", evs, "event"), ("agent", agents, "agent")):
                    if rows:
                        cursors[key] = rows[-1]["id"]
                        yield f"event: {kind}\ndata: {json.dumps(rows, ensure_ascii=False)}\n\n"
                if msgs:
                    cursors["message"] = msgs[-1]["id"]
                    yield f"event: message\ndata: {json.dumps(msgs, ensure_ascii=False)}\n\n"
                if run:
                    state = (run["state"], run["phase"], run["iteration"], run["updated_at"])
                    if state != last_state:
                        last_state = state
                        payload = {
                            "state": run["state"],
                            "phase": run["phase"],
                            "iteration": run["iteration"],
                            "pending": run["pending"],
                            "updated_at": run["updated_at"],
                        }
                        yield f"event: run\ndata: {json.dumps(payload)}\n\n"
                if not follow:
                    return
                await asyncio.sleep(0.5)

        return StreamingResponse(
            gen(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    def _decide(action: str, run_id: str, body: ActionBody) -> dict[str, str]:
        try:
            match action:
                case "cancel":
                    runner.cancel(run_id)
                case "resume":
                    runner.resume(run_id)
                case "rework":
                    runner.rework(run_id, body.note)
                case "retry":
                    runner.retry(run_id)
                case "abandon":
                    runner.abandon(run_id)
                case "reply":
                    runner.reply(run_id, body.text or body.note)
                case "approve":
                    runner.approve(run_id, body.edits)
                case "takeover":
                    runner.takeover(run_id, body.lead, body.note)
                case "keep-worker":
                    runner.keep_worker(run_id)
                case "accept":
                    runner.accept(run_id)
                case "allow-sensitive":
                    runner.allow_sensitive(run_id)
                case "open-terminal":
                    runner.open_external_terminal(run_id)
                case "external-done":
                    runner.external_done(run_id, body.note)
                case "apply":
                    runner.apply_to_repo(run_id)
                case "vault-write":
                    runner.vault_write(run_id, -1 if body.index is None else body.index)
                case "vault-discard":
                    runner.vault_discard(run_id, -1 if body.index is None else body.index)
        except KeyError as exc:
            raise HTTPException(404, "run not found") from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        return {"ok": action}

    @app.post("/api/runs/{run_id}/{action}")
    def decide(run_id: str, action: str, body: ActionBody | None = None) -> dict[str, str]:
        if action not in ACTIONS:
            raise HTTPException(404, "unknown action")
        return _decide(action, run_id, body or ActionBody())

    if UI_DIST.is_dir():

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str) -> FileResponse:
            target = (UI_DIST / path).resolve()
            if path and target.is_file() and target.is_relative_to(UI_DIST):
                return FileResponse(target)
            return FileResponse(UI_DIST / "index.html")

    return app
