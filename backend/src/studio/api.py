"""HTTP API for the UI. Binds to 127.0.0.1 only.

CSRF guard: every mutating request must carry `X-Studio: 1` (a custom header forces a CORS
preflight, which this server never approves) and, if an Origin is present, it must be the
server's own origin or the Vite dev server.
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from studio import __version__
from studio.agents.leads import ClaudeLead, CodexLead, Lead
from studio.config import Settings
from studio.db import Database
from studio.domain import TaskSpec
from studio.gateway import ChatGateway, OmniRouteGateway
from studio.routing import ModelRouter
from studio.runner import Runner

UI_DIST = Path(__file__).resolve().parents[3] / "frontend" / "dist"


class DecisionBody(BaseModel):
    note: str = ""


def _decode(row: dict[str, Any], *keys: str) -> dict[str, Any]:
    out = dict(row)
    for key in keys:
        if out.get(key):
            out[key] = json.loads(out[key])
    return out


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
        "claude": ClaudeLead(settings.claude_bin),
        "codex": CodexLead(settings.codex_bin),
    }
    runner = Runner(db, gw, router, lead_map, settings.workspaces_dir)
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
            "cooldowns": db.cooldowns(),
        }

    @app.post("/api/tasks", status_code=201)
    def create_task(spec: TaskSpec) -> dict[str, str]:
        repo = Path(spec.repo_path)
        if not repo.is_absolute() or not (repo / ".git").exists():
            raise HTTPException(422, "repo_path must be an absolute path to a git repository")
        run_id = db.create_task_and_run(spec.model_dump_json())
        runner.wake()
        return {"run_id": run_id}

    @app.get("/api/runs")
    def list_runs() -> list[dict[str, Any]]:
        out = []
        for r in db.runs():
            spec = json.loads(r["spec_json"])
            out.append(
                {
                    "id": r["id"],
                    "title": spec["title"],
                    "state": r["state"],
                    "phase": r["phase"],
                    "iteration": r["iteration"],
                    "created_at": r["created_at"],
                    "updated_at": r["updated_at"],
                    "finished_at": r["finished_at"],
                    "decision_reason": r["decision_reason"],
                }
            )
        return out

    @app.get("/api/runs/{run_id}")
    def get_run(run_id: str) -> dict[str, Any]:
        r = db.run(run_id)
        if not r:
            raise HTTPException(404, "run not found")
        run = _decode(r, "spec_json", "plan_json", "feedback_json")
        run["spec"] = run.pop("spec_json")
        run["plan"] = run.pop("plan_json")
        run["feedback"] = run.pop("feedback_json")
        steps = [_decode(s, "output_json") for s in db.steps(run_id)]
        for s in steps:
            s["output"] = s.pop("output_json")
        return {
            "run": run,
            "steps": steps,
            "events": [_decode(e, "data_json") for e in db.events(run_id)],
            "model_calls": db.model_calls(run_id),
            "usage": db.usage(run_id),
        }

    def _decide(action: str, run_id: str, body: DecisionBody) -> dict[str, str]:
        try:
            if action == "cancel":
                runner.cancel(run_id)
            elif action == "resume":
                runner.resume(run_id)
            elif action == "rework":
                runner.rework(run_id, body.note)
            elif action == "abandon":
                runner.abandon(run_id)
        except KeyError as exc:
            raise HTTPException(404, "run not found") from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        return {"ok": action}

    @app.post("/api/runs/{run_id}/{action}")
    def decide(run_id: str, action: str, body: DecisionBody | None = None) -> dict[str, str]:
        if action not in ("cancel", "resume", "rework", "abandon"):
            raise HTTPException(404, "unknown action")
        return _decide(action, run_id, body or DecisionBody())

    if UI_DIST.is_dir():

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str) -> FileResponse:
            target = (UI_DIST / path).resolve()
            if path and target.is_file() and target.is_relative_to(UI_DIST):
                return FileResponse(target)
            return FileResponse(UI_DIST / "index.html")

    return app
