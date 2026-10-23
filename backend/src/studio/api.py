"""HTTP API for the UI. Binds to 127.0.0.1 only.

CSRF guard: every mutating request must carry `X-Studio: 1` (a custom header forces a CORS
preflight, which this server never approves) and, if an Origin is present, it must be the
server's own origin or the Vite dev server.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from studio import __version__
from studio.agents.api_lead import ApiLead
from studio.agents.leads import ClaudeLead, CodexLead, Lead, cli_version
from studio.config import DAILY_REQUEST_CAPS, Settings
from studio.db import Database
from studio.domain import AUTOPILOT, CLI_LEADS, AnyLead, TaskSpec
from studio.gateway import ChatGateway, DirectGateway, OmniRouteGateway, RoutedGateway
from studio.launcher import LaunchError, OmniRouteLauncher
from studio.memory import project_memory
from studio.messages import Language
from studio.preferences import Defaults, Preferences
from studio.probe import UnknownModel, probe_model
from studio.providers import KeyStoreError, load_providers, local_specs, save_key
from studio.routing import ModelRouter
from studio.runner import Runner
from studio.sandbox import Denied
from studio.vault import Vault

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
    "memory-retry",
)


class ActionBody(BaseModel):
    note: str = Field("", max_length=20_000)
    text: str = Field("", max_length=20_000)
    lead: AnyLead | None = None  # takeover: who implements (Claude/Codex only if enabled)
    edits: dict[str, Any] | None = None
    index: int | None = None  # which memory proposal (vault-write / vault-discard)


class AgentBody(BaseModel):
    plan: bool | None = None
    review: bool | None = None
    takeover: bool | None = None


class ProviderBody(BaseModel):
    enabled: bool | None = None
    # A key to save in the OS keyring ("" deletes it). Never stored by Studio itself.
    key: str | None = Field(default=None, max_length=500)


class MemoryPathBody(BaseModel):
    path: str | None = None


class LanguageBody(BaseModel):
    language: Language


class SettingsBody(BaseModel):
    defaults: Defaults


class ProbeBody(BaseModel):
    model: str


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


PER_MODEL_CAPS = ("groq",)


def _providers_today(by_model: dict[str, int]) -> list[dict[str, Any]]:
    """Requests per provider today against its known free daily cap.

    Groq caps each model separately, so the busiest model counts;
    OpenRouter caps the pool.
    """
    out: dict[str, dict[str, Any]] = {}
    for model, n in by_model.items():
        provider = model.split("/", 1)[0]
        row = out.setdefault(provider, {"provider": provider, "calls": 0, "used": 0, "cap": None})
        row["calls"] += n
        row["used"] = max(row["used"], n) if provider in PER_MODEL_CAPS else row["calls"]
        row["cap"] = DAILY_REQUEST_CAPS.get(provider)
    return sorted(out.values(), key=lambda r: -int(r["calls"]))


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
    prefs = Preferences(db)
    if gateway is None and settings.fake_gateway:
        from studio.testing import ScriptedGateway

        gateway = ScriptedGateway()
    providers = load_providers(settings.data_dir, set(prefs.enabled_providers()))
    direct = DirectGateway(providers, settings.gateway_timeout_s)
    omniroute = OmniRouteGateway(
        settings.omniroute_url, settings.omniroute_api_key, settings.gateway_timeout_s
    )
    gw: ChatGateway = gateway or RoutedGateway(omniroute, direct)
    router = ModelRouter(
        db,
        settings.worker_models,
        privacy_floor=settings.privacy_floor,
        allow_paid=settings.allow_paid,
        exposed=gw.models,
        max_wait_s=settings.route_wait_s,
    )

    def refresh_local_models() -> None:
        """Models of local providers (Ollama) join the catalog when they appear."""
        if gateway is None:
            router.extend(local_specs(providers, direct.models(fresh=True) or set()))

    refresh_local_models()

    def memory_path() -> Path | None:
        """The memory folder chosen in Settings wins over config.toml / STUDIO_VAULT."""
        chosen = prefs.memory_path()
        return Path(chosen) if chosen else settings.vault_path

    def current_vault() -> Vault | None:
        path = memory_path()
        if path is None or not path.is_dir():
            return None
        return Vault(path, settings.memory_layout)

    lead_map: dict[str, Lead] = leads or {
        AUTOPILOT: ApiLead(db, gw, router, current_vault),
        "claude": ClaudeLead(),
        "codex": CodexLead(),
    }

    def agent_problems(spec: TaskSpec) -> list[str]:
        """Claude Code / Codex roles a task wants that the user has not switched on."""
        enabled = prefs.agents()
        problems = []
        for name, role in sorted(spec.orchestration.agent_roles(spec.implementer)):
            lead = lead_map.get(name)
            if not enabled[name][role]:
                problems.append(f"{name} is not enabled for {role} (Settings, Agents)")
            elif lead is None or not lead.available().get("installed"):
                problems.append(f"{name} CLI is not installed")
        return problems

    launcher = OmniRouteLauncher(
        settings.omniroute_url,
        settings.data_dir,
        lambda: bool(gw.health().get("reachable")),
    )
    runner = Runner(
        db,
        gw,
        router,
        lead_map,
        settings.workspaces_dir,
        current_vault,
        agent_enabled=lambda name, role: bool(prefs.agents().get(name, {}).get(role)),
    )
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
            "gateway": {
                **gw.health(),
                "launchable": gateway is None and launcher.binary() is not None,
            },
            "leads": {name: lead.available() for name, lead in lead_map.items()},
            "worker_models": router.candidates(),
            "lead_models": router.candidates("lead", "hard"),
            "cooldowns": db.cooldowns(),
            "models": router.catalog(),
            "privacy_floor": router.privacy_floor,
            "api_calls_today": db.api_calls_since(_midnight_utc()),
            "providers_today": _providers_today(db.api_calls_by_model_since(_midnight_utc())),
        }

    @app.post("/api/omniroute/start")
    def start_omniroute() -> dict[str, object]:
        """Run `omniroute serve` on the user's click and wait until it answers."""
        if gateway is not None:
            raise HTTPException(409, "Studio uses a test gateway")
        try:
            result = launcher.start()
        except LaunchError as exc:
            raise HTTPException(409, str(exc)) from None
        gw.models(fresh=True)
        return result

    @app.get("/api/agents")
    def list_agents() -> list[dict[str, Any]]:
        """Claude Code and Codex: installed or not, version, and the roles switched on."""
        enabled = prefs.agents()
        out = []
        for name in CLI_LEADS:
            lead = lead_map.get(name)
            installed = bool(lead and lead.available().get("installed"))
            executable = getattr(lead, "executable", name)
            out.append(
                {
                    "name": name,
                    "installed": installed,
                    "version": cli_version(executable) if installed else None,
                    "roles": enabled[name],
                }
            )
        return out

    @app.put("/api/agents/{name}")
    def set_agent(name: str, body: AgentBody) -> list[dict[str, Any]]:
        if name not in CLI_LEADS:
            raise HTTPException(404, "unknown agent")
        prefs.remember_agent(name, body.model_dump(exclude_none=True))
        return list_agents()

    def _describe_providers() -> list[dict[str, Any]]:
        # A local provider is "active" by default but only usable while it runs.
        reachable = set(direct.reachable_names())
        return [
            {**p, "running": p["name"] in reachable} if p["local"] else p
            for p in providers.describe()
        ]

    @app.get("/api/providers")
    def list_providers() -> list[dict[str, Any]]:
        """Direct providers and whether they are active. Key values are never returned."""
        return _describe_providers()

    @app.put("/api/providers/{name}")
    def set_provider(name: str, body: ProviderBody) -> list[dict[str, Any]]:
        """Turn a keyless provider on or off, or save / delete its key in the OS keyring."""
        provider = providers.providers.get(name)
        if provider is None:
            raise HTTPException(404, "unknown provider")
        if body.key is not None:  # first, so a refused key changes nothing else
            if not provider.api_key_env:
                raise HTTPException(422, "this provider takes no key")
            try:
                save_key(provider.api_key_env, body.key.strip() or None)
            except KeyStoreError as exc:
                raise HTTPException(409, str(exc)) from None
        if body.enabled is not None:
            prefs.remember_enabled_providers(providers.set_enabled(name, body.enabled))
        gw.models(fresh=True)
        refresh_local_models()
        return _describe_providers()

    @app.post("/api/models/probe")
    def probe(body: ProbeBody) -> dict[str, Any]:
        """One tiny tool call to one catalog model, e.g. right after adding its key."""
        try:
            return probe_model(gw, router, body.model)
        except UnknownModel:
            raise HTTPException(404, "model not in the catalog") from None

    # --- preferences --------------------------------------------------------------------

    @app.get("/api/settings")
    def get_settings() -> dict[str, Any]:
        return prefs.snapshot()

    @app.put("/api/settings")
    def put_settings(body: SettingsBody) -> dict[str, Any]:
        prefs.save(body.defaults)
        return prefs.snapshot()

    # --- memory folder ------------------------------------------------------------------

    @app.get("/api/vault")
    def vault_overview() -> dict[str, Any]:
        """Projects and open handoffs Studio may use. Names only; nothing is sent anywhere."""
        lay = settings.memory_layout
        layout = {"projects": lay.projects, "handoffs": lay.handoffs, "include": list(lay.include)}
        vault = current_vault()
        if vault is None:
            path = memory_path()
            return {"available": False, "path": str(path or ""), "layout": layout}
        return {
            "available": True,
            "path": str(vault.root),
            "projects": vault.projects(),
            "handoffs": vault.open_handoffs(),
            "folders": vault.folder_counts(),
            "layout": layout,
        }

    @app.put("/api/vault/path")
    def set_memory_path(body: MemoryPathBody) -> dict[str, Any]:
        """Choose the memory folder (or clear the choice with null). Must be an existing folder."""
        if body.path:
            path = Path(body.path).expanduser()
            if not path.is_absolute() or not path.is_dir():
                raise HTTPException(422, "the memory folder must be an existing absolute folder")
            path = path.resolve()
            if path == Path.home().resolve() or path.parent == path:
                # Its README, notes/ and projects/ would be shared with models.
                raise HTTPException(422, "choose a dedicated folder, not your home or a drive")
            prefs.remember_memory_path(str(path))
        else:
            prefs.remember_memory_path(None)
        return vault_overview()

    @app.put("/api/language")
    def set_language(body: LanguageBody) -> dict[str, str]:
        """Language for the UI and for new event text (old events keep theirs)."""
        prefs.remember_language(body.language)
        return {"language": body.language}

    @app.get("/api/vault/project")
    def vault_project(note: str) -> dict[str, Any]:
        vault = current_vault()
        if vault is None:
            raise HTTPException(404, "the memory folder is not configured or missing")
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
            vault = current_vault()
            if vault is None or not vault.shareable(spec.project_note):
                raise HTTPException(422, "project_note is not a shared note in the vault")
        problems = agent_problems(spec)
        if problems:
            raise HTTPException(422, "; ".join(problems))
        run_id = db.create_task_and_run(spec.model_dump_json())
        db.message(run_id, "user", spec.goal)
        prefs.remember_repo(str(repo))
        if spec.project_note:
            prefs.remember_project(spec.project_note)
        runner.wake()
        return {"run_id": run_id}

    @app.get("/api/overview")
    def overview(days: int = 30) -> dict[str, Any]:
        days = min(max(days, 1), 365)
        return {"days": days, **db.overview(time.time() - days * 86400)}

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
                    "mode": spec.orchestration.primary,
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
                    # No lead given means the task's own takeover agent: gate that one too.
                    lead = body.lead or _takeover_of(run_id)
                    if lead in CLI_LEADS:
                        if not prefs.agents()[lead]["takeover"]:
                            raise HTTPException(422, f"{lead} is not enabled for takeover")
                        if not lead_map[lead].available().get("installed"):
                            raise HTTPException(422, f"{lead} CLI is not installed")
                    runner.takeover(run_id, lead, body.note)
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
                case "memory-retry":
                    runner.memory_retry(run_id)
                case "vault-discard":
                    runner.vault_discard(run_id, -1 if body.index is None else body.index)
        except KeyError as exc:
            raise HTTPException(404, "run not found") from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        return {"ok": action}

    def _takeover_of(run_id: str) -> str:
        run = db.run(run_id)
        if run is None:
            raise KeyError(run_id)
        return TaskSpec.model_validate_json(run["spec_json"]).orchestration.takeover

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
