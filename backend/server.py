#
# The API.
#
# Serves the builder UI, the agent store, the Copilot, the eval harness, the
# call analyser, and the WebRTC endpoint for browser test calls — one process,
# one port, `make dev`.
#
# Anything that calls a model can take 10-60 seconds, so those endpoints return
# a job id immediately and the UI polls. That's what lets the test suite fill in
# case-by-case as results land instead of blocking on a spinner.
#
#   python server.py     then open http://localhost:7860
#

import asyncio
import hmac
import json
import os
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Optional

import uvicorn
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request, Response, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from loguru import logger
from pydantic import BaseModel

import settings  # noqa: F401  — loads .env with the shell-wins rule

# Always log to a file as well as the console. However the server was started —
# `make run`, a background shell, a double-clicked terminal — the last few runs
# are readable afterwards, which is the difference between debugging a call and
# asking the user to reproduce it.
LOG_DIR = Path(__file__).resolve().parent / "logs"
LOG_DIR.mkdir(exist_ok=True)
logger.add(
    LOG_DIR / "server.log",
    rotation="10 MB",
    retention=5,
    level="DEBUG",
    backtrace=True,
    diagnose=False,          # never write secrets into a log file
    enqueue=True,
)

from agent_builder import lint, store  # noqa: E402
from agent_builder.patch import PatchError, affected_nodes, apply_ops, summarize  # noqa: E402
from agent_builder.schema import DEFAULT_VOICE_ID, AgentConfig  # noqa: E402
from analysis import (  # noqa: E402
    issue_context,
    load_calls,
    load_issues,
    mine_issues,
    set_issue_status,
)
from copilot import (  # noqa: E402
    blast_radius,
    build_context,
    build_outcome,
    decisions_context,
    propose,
    record_acceptance,
    structural_signals,
)
from sim import (  # noqa: E402
    TestCase,
    add_case,
    advance,
    case_from_call,
    delete_case,
    generate_suite,
    load_cases,
    load_last_run,
    run_suite,
    save_cases,
)
import activation  # noqa: E402
import auth  # noqa: E402
import db  # noqa: E402
import tenancy  # noqa: E402
from jobs import JOBS  # noqa: E402
import telephony  # noqa: E402
from voice import (  # noqa: E402
    DEBUG_DIR,
    SESSIONS,
    get_session,
    new_session,
    run_call,
    run_phone_call,
)

FRONTEND_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"


# ------------------------------------------------------------------- jobs ---
# Durable when a database is configured, in-process otherwise. See jobs.py for
# why this stopped being a plain dict.
def _new_job(kind: str, agent_id: Optional[str] = None) -> str:
    return JOBS.new(kind, agent_id)


def _run_job(job_id: str, coro_factory):
    """Run an async job, recording success or failure on the job record."""

    async def runner():
        try:
            JOBS[job_id]["result"] = await coro_factory()
            JOBS[job_id]["status"] = "done"
        except Exception as exc:
            logger.exception(f"job {job_id} failed")
            JOBS[job_id]["status"] = "error"
            JOBS[job_id]["error"] = f"{type(exc).__name__}: {exc}"

    asyncio.create_task(runner())


# ------------------------------------------------------------------- app ----
@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    await webrtc_handler.close()


app = FastAPI(title="Prosper Agent Composer", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.middleware("http")
async def require_session(request: Request, call_next):
    """Gate the API behind a session, with the machine-to-machine paths exempt.

    Only /api/* is checked. The static frontend is served to anyone, because it
    is a login screen until an API call succeeds, and a 401 on the HTML would
    mean the browser's own credential prompt instead of ours.
    """
    path = request.url.path
    session = auth.read(request.cookies.get(auth.SESSION_COOKIE))

    if session and session.get("org"):
        # Establish the tenant for the whole request, including any background
        # job it spawns — asyncio copies the context into new tasks.
        tenancy.set_context(session["org"], session.get("uid"))
    elif not auth.enabled():
        # Local development has no session to read a workspace from. Following
        # the live agent is self-maintaining: an agent moved between accounts
        # takes local dev with it, instead of leaving the builder looking empty
        # and the data looking lost.
        live = activation.active()
        tenancy.set_context(live.get("org_id") or db.DEFAULT_ORG, None)

    if (
        not auth.enabled()
        or not path.startswith("/api/")
        or path.startswith(auth.OPEN_PREFIXES)
    ):
        return await call_next(request)

    if not session:
        return JSONResponse({"detail": "Sign in to continue."}, status_code=401)
    if not session.get("org"):
        # A session from before workspaces existed, or one whose provisioning
        # failed. Nothing it could read would be correctly scoped.
        return JSONResponse(
            {"detail": "Your session predates workspaces. Sign in again."}, status_code=401
        )
    return await call_next(request)


class PhoneRequest(BaseModel):
    phone: str


class VerifyRequest(BaseModel):
    phone: str
    code: str


class PasswordRequest(BaseModel):
    password: str


def _set_session(
    response: JSONResponse, subject: str, via: str,
    user_id: Optional[str] = None, org_id: Optional[str] = None,
) -> JSONResponse:
    response.set_cookie(
        auth.SESSION_COOKIE,
        auth.issue(subject, via, user_id, org_id),
        max_age=auth.SESSION_TTL,
        httponly=True,          # a token JS can read is a token XSS can steal
        samesite="lax",
        secure=bool(os.environ.get("PUBLIC_BASE_URL", "").startswith("https")),
        path="/",
    )
    return response


@app.get("/api/auth/status")
async def auth_status(request: Request):
    session = auth.read(request.cookies.get(auth.SESSION_COOKIE))
    return {**auth.status(), "signed_in": bool(session), "user": (session or {}).get("sub")}


@app.post("/api/auth/request-code")
async def auth_request_code(body: PhoneRequest, request: Request):
    """Send a sign-in code. Open to anyone — this is how accounts are created."""
    try:
        phone = auth.normalise(body.phone)
    except auth.AuthError as exc:
        raise HTTPException(400, str(exc))

    # uvicorn runs with proxy_headers=True, so request.client.host is already the
    # real caller rather than the proxy. Reading X-Forwarded-For by hand as well
    # produced two different keys for the same visitor and a throttle that
    # counted a fraction of the requests it was meant to.
    caller = request.client.host if request.client else "unknown"

    # Both buckets always increment: `or` short-circuits, so checking them
    # inline meant a tripped IP limit stopped the per-number count entirely.
    ip_hit = auth.throttled(f"ip:{caller}")
    phone_hit = auth.throttled(f"phone:{phone}")
    if ip_hit or phone_hit:
        logger.warning(f"throttled a code request from {caller}")
        raise HTTPException(429, "Too many codes requested. Try again in a few minutes.")

    try:
        await auth.send_code(phone)
    except auth.AuthError as exc:
        raise HTTPException(400, str(exc))
    return {"sent": True}


@app.post("/api/auth/verify")
async def auth_verify(body: VerifyRequest):
    """Check the code, then sign in — creating the account on first use."""
    try:
        phone = auth.normalise(body.phone)
        if not await auth.check_code(phone, body.code):
            raise HTTPException(401, "That code isn't right.")
    except auth.AuthError as exc:
        raise HTTPException(400, str(exc))

    user_id, org_id = await auth.sign_in(phone)
    if not org_id:
        # Without a workspace there is nowhere to put anything they build, and a
        # session with no tenant would read as "no data" rather than as a fault.
        raise HTTPException(503, "Couldn't set up your workspace. Try again shortly.")

    with tenancy.as_org(org_id, user_id):
        _seed_workspace()

    logger.info(f"signed in: {phone[:-4]}****")
    return _set_session(
        JSONResponse({"signed_in": True, "user": phone}), phone, "phone", user_id, org_id
    )


def _seed_workspace() -> None:
    """Give a brand-new account something to open.

    An empty builder is a worse first impression than a one-node scaffold, and
    the scaffold is exactly what the 'New agent' button would have produced.
    """
    try:
        if store.list_agents():
            return
        store.create(json.loads(json.dumps(SCAFFOLD)), label="Created with your workspace")
    except Exception as exc:
        logger.warning(f"could not seed a starter agent: {exc}")


@app.post("/api/auth/password")
async def auth_password(body: PasswordRequest):
    if not auth.password_login(body.password):
        raise HTTPException(401, "Wrong password.")
    logger.info("signed in with the break-glass password")
    return _set_session(
        JSONResponse({"signed_in": True, "user": "operator"}),
        "operator", "password", None, db.DEFAULT_ORG,
    )


@app.post("/api/auth/logout")
async def auth_logout():
    response = JSONResponse({"signed_in": False})
    response.delete_cookie(auth.SESSION_COOKIE, path="/")
    return response


from pipecat.transports.smallwebrtc.request_handler import (  # noqa: E402
    SmallWebRTCPatchRequest,
    SmallWebRTCRequest,
    SmallWebRTCRequestHandler,
)

webrtc_handler = SmallWebRTCRequestHandler()


def _config_or_404(agent_id: str) -> dict:
    try:
        return store.get_config(agent_id)
    except KeyError:
        raise HTTPException(404, f"No agent '{agent_id}'.")


# ----------------------------------------------------------------- agents ---
class CreateAgent(BaseModel):
    name: Optional[str] = None
    config: Optional[dict] = None


class SaveAgent(BaseModel):
    config: dict
    label: str = "Edited"


SCAFFOLD = {
    "name": "Untitled agent",
    "voice_id": DEFAULT_VOICE_ID,
    "model": "gpt-4o",
    "persona": (
        "You are a warm, efficient scheduling assistant for a healthcare clinic. Your "
        "responses are spoken aloud, so avoid emoji, lists, or anything that can't be read "
        "out. Keep replies to one or two short sentences."
    ),
    "initial_node": "greeting",
    "nodes": [
        {
            "name": "greeting",
            "task_messages": [
                {"role": "developer", "content": "Greet the caller and ask how you can help."}
            ],
            "edges": [],
        }
    ],
}


@app.get("/api/agents")
async def list_agents():
    live = activation.active_agent_id()
    return {
        "agents": [{**a, "active": a["id"] == live} for a in store.list_agents()],
        "active_agent_id": live,
    }


class RenameAgent(BaseModel):
    name: str


@app.post("/api/agents/{agent_id}/activate")
async def activate_agent(agent_id: str, request: Request):
    """Put this agent on the phone. Whatever was live stops being live."""
    config = _config_or_404(agent_id)        # 404s unless the agent is yours
    if [i for i in lint(AgentConfig.from_dict(config)) if i.severity == "error"]:
        raise HTTPException(400, "Fix this agent's errors before putting it live.")
    session = auth.read(request.cookies.get(auth.SESSION_COOKIE)) or {}
    previous = activation.active_agent_id()
    record = activation.activate(agent_id, tenancy.org(), str(session.get("sub") or ""))
    return {"active": record, "deactivated": previous if previous != agent_id else ""}


@app.post("/api/agents/{agent_id}/deactivate")
async def deactivate_agent(agent_id: str):
    _config_or_404(agent_id)
    activation.deactivate(agent_id)
    return {"active": activation.active()}


@app.post("/api/agents/{agent_id}/rename")
async def rename_agent(agent_id: str, body: RenameAgent):
    """Renaming is a change to the agent, so it cuts a version like any other.

    The alternative — editing the name in place — would make the history lie
    about what the agent was called when a call came in.
    """
    config = _config_or_404(agent_id)
    name = (body.name or "").strip()
    if not name:
        raise HTTPException(400, "An agent needs a name.")
    if name == config.get("name"):
        return {"agent": store.get(agent_id)}
    record = store.save(
        agent_id, {**config, "name": name},
        label=f"Renamed to '{name}'", source="manual",
    )
    return {"agent": record}


@app.post("/api/agents")
async def create_agent(body: CreateAgent):
    config = body.config or json.loads(json.dumps(SCAFFOLD))
    if body.name:
        config["name"] = body.name
    record = store.create(config)
    return {"agent": record}


@app.get("/api/agents/{agent_id}")
async def get_agent(agent_id: str):
    try:
        record = store.get(agent_id)
    except KeyError:
        raise HTTPException(404, f"No agent '{agent_id}'.")
    issues = lint(AgentConfig.from_dict(record["config"]))
    return {"agent": record, "lint": [i.to_dict() for i in issues]}


@app.put("/api/agents/{agent_id}")
async def save_agent(agent_id: str, body: SaveAgent):
    _config_or_404(agent_id)
    record = store.save(agent_id, body.config, label=body.label, source="manual")
    issues = lint(AgentConfig.from_dict(body.config))
    return {"agent": record, "lint": [i.to_dict() for i in issues]}


@app.delete("/api/agents/{agent_id}")
async def delete_agent(agent_id: str):
    _config_or_404(agent_id)
    # Deleting the live agent must take it off the line too, or the number
    # keeps ringing an agent that no longer exists.
    activation.deactivate(agent_id)
    store.delete(agent_id)
    return {"ok": True}


@app.post("/api/lint")
async def lint_config(body: dict):
    """Validate a config without saving it — used for live editor feedback."""
    try:
        issues = lint(AgentConfig.from_dict(body.get("config", {})))
    except Exception as exc:
        return {"lint": [{"severity": "error", "message": str(exc), "node": "", "function": ""}]}
    return {"lint": [i.to_dict() for i in issues]}


@app.get("/api/agents/{agent_id}/versions")
async def agent_versions(agent_id: str):
    _config_or_404(agent_id)
    return {"versions": store.versions(agent_id)}


@app.post("/api/agents/{agent_id}/revert")
async def revert_agent(agent_id: str, body: dict):
    _config_or_404(agent_id)
    record = store.revert(agent_id, int(body["version"]))
    return {"agent": record}


# ---------------------------------------------------------------- copilot ---
class CopilotAsk(BaseModel):
    message: str
    history: list = []
    issue_id: Optional[str] = None
    include_failures: bool = False


def _verifier(agent_id: str, run: dict, cases: list, job_id: str):
    """Build the callback that runs a candidate proposal before anyone sees it.

    Which cases run is the whole design: the ones that were failing, because
    those are what the proposal claims to fix, plus the passing ones that route
    through a node it edited, because that's where collateral damage lands. The
    rest of the suite is unaffected by definition and isn't worth the wait.
    """
    cases_by_id = {c.id: c for c in cases}
    focus_ids = {
        r["case_id"] for r in run.get("results", []) if not r.get("passed")
    }

    async def verify(candidate: dict, ops: list, retire_ids: set, extra: list):
        neighbour_ids = set(blast_radius(ops, run)) - focus_ids
        selected_ids = (focus_ids | neighbour_ids) - set(retire_ids)

        selected = [cases_by_id[cid] for cid in selected_ids if cid in cases_by_id]
        for i, case in enumerate(extra or []):
            selected.append(
                TestCase.from_dict({**case, "id": case.get("id") or f"tc_candidate_{i}"})
            )
        if not selected:
            return build_outcome(
                AgentConfig.from_dict(candidate),
                {"results": [], "passed": 0, "total": 0},
                {}, focus_ids, neighbour_ids, set(retire_ids),
            )

        JOBS[job_id]["progress"] = {"done": 0, "total": len(selected)}
        candidate_run = await run_suite(
            agent_id,
            AgentConfig.from_dict(candidate),
            cases=selected,
            persist=False,   # a hypothetical run must never become "the last run"
            on_result=lambda _r, done, total: JOBS[job_id].update(
                progress={"done": done, "total": total}
            ),
        )
        return build_outcome(
            AgentConfig.from_dict(candidate),
            candidate_run,
            {cid: c.to_dict() for cid, c in cases_by_id.items()},
            focus_ids,
            neighbour_ids,
            set(retire_ids),
        )

    return verify


@app.post("/api/agents/{agent_id}/copilot")
async def copilot_ask(agent_id: str, body: CopilotAsk):
    config = _config_or_404(agent_id)

    # Attach evidence so "fix this" is a complete instruction on its own.
    context_parts = []
    # Standing decisions come first: they're the constraints everything else is
    # judged against, and without them the Copilot re-opens settled arguments.
    decisions = decisions_context(agent_id)
    if decisions:
        context_parts.append(decisions)
    if body.issue_id:
        issue = next((i for i in load_issues(agent_id) if i["id"] == body.issue_id), None)
        if issue:
            context_parts.append(issue_context(issue))

    run = load_last_run(agent_id) if body.include_failures else None
    cases = load_cases(agent_id)
    signals: dict = {}
    verify = None

    job_id = _new_job("copilot")

    if body.include_failures and run:
        # The full diagnostic brief: personas, per-assertion verdicts, annotated
        # transcripts, and — the part that makes root causes visible — the exits
        # and their required fields at every node the failing calls visited.
        report = build_context(AgentConfig.from_dict(config), run, cases)
        if report:
            context_parts.append(report)
        signals = structural_signals(AgentConfig.from_dict(config), run)
        if any(not r.get("passed") for r in run.get("results", [])):
            verify = _verifier(agent_id, run, cases, job_id)

    context = "\n\n---\n\n".join(context_parts)
    JOBS[job_id]["status_text"] = ""

    def say(text: str) -> None:
        JOBS[job_id]["status_text"] = text

    _run_job(
        job_id,
        lambda: propose(
            config,
            body.message,
            body.history,
            context,
            verify=verify,
            signals=signals,
            on_status=say,
        ),
    )
    return {"job_id": job_id}


class ApplyPatch(BaseModel):
    ops: list = []
    label: str = "Copilot edit"
    tests: list = []
    retire_tests: list = []
    issue_id: Optional[str] = None
    # The Copilot's own explanation, kept as the reason this was accepted.
    reply: str = ""


@app.post("/api/agents/{agent_id}/copilot/apply")
async def copilot_apply(agent_id: str, body: ApplyPatch):
    config = _config_or_404(agent_id)

    # A proposal can be test-only — "this test is wrong" is a decision to accept
    # without touching the graph. Don't cut a version for a no-op edit.
    record = None
    if body.ops:
        try:
            new_config = apply_ops(config, body.ops)
        except PatchError as exc:
            raise HTTPException(400, str(exc))

        errors = [i for i in lint(AgentConfig.from_dict(new_config)) if i.severity == "error"]
        if errors:
            raise HTTPException(400, "; ".join(i.message for i in errors))

        record = store.save(
            agent_id, new_config, label=body.label, source="copilot", ops=body.ops
        )

    added, retired = [], []
    if body.tests or body.retire_tests:
        cases = load_cases(agent_id)

        # Retire first: a fix that replaces a broken test shouldn't leave both.
        drop = {r.get("case_id") for r in body.retire_tests if r.get("case_id")}
        if drop:
            retired = [c.to_dict() for c in cases if c.id in drop]
            cases = [c for c in cases if c.id not in drop]

        # Regression tests ship with the fix — that's what stops the issue coming back.
        # A case that fixes an existing failure doesn't need re-adding: the failing
        # case is already the regression test, and a duplicate reports it twice.
        existing = {c.name.strip().lower() for c in cases}
        for t in body.tests:
            if (t.get("name") or "").strip().lower() in existing:
                continue
            existing.add((t.get("name") or "").strip().lower())
            case = TestCase.from_dict(
                {
                    **t,
                    "id": f"tc_{uuid.uuid4().hex[:8]}",
                    "origin": "regression" if body.issue_id else "generated",
                    "source_issue": body.issue_id or "",
                }
            )
            cases.append(case)
            added.append(case.to_dict())
        save_cases(agent_id, cases)

    if body.issue_id:
        set_issue_status(agent_id, body.issue_id, "fixed")

    # Accepting a proposal is the moment a judgement call becomes a decision. The
    # graph records what changed; only this records why, which is the half the
    # next session needs in order not to undo it.
    record_acceptance(
        agent_id,
        label=body.label,
        reply=body.reply,
        ops=body.ops,
        retired=[
            {"name": r.get("name"), "reason": r.get("reason")} for r in body.retire_tests
        ],
        added=added,
    )

    return {"agent": record, "tests_added": added, "tests_retired": retired}


# ------------------------------------------------------------------ tests ---
@app.get("/api/agents/{agent_id}/tests")
async def get_tests(agent_id: str):
    _config_or_404(agent_id)
    return {
        "cases": [c.to_dict() for c in load_cases(agent_id)],
        "last_run": load_last_run(agent_id),
    }


@app.post("/api/agents/{agent_id}/tests")
async def create_test(agent_id: str, body: dict):
    _config_or_404(agent_id)
    case = add_case(agent_id, TestCase.from_dict({**body, "id": f"tc_{uuid.uuid4().hex[:8]}"}))
    return {"case": case.to_dict()}


@app.delete("/api/agents/{agent_id}/tests/{case_id}")
async def remove_test(agent_id: str, case_id: str):
    delete_case(agent_id, case_id)
    return {"ok": True}


@app.post("/api/agents/{agent_id}/tests/generate")
async def generate_tests(agent_id: str, body: dict):
    config = _config_or_404(agent_id)
    count = int(body.get("count", 6))
    job_id = _new_job("generate_tests")

    async def work():
        cases = await generate_suite(AgentConfig.from_dict(config), count=count)
        existing = load_cases(agent_id)
        save_cases(agent_id, existing + cases)
        return {"cases": [c.to_dict() for c in cases]}

    _run_job(job_id, work)
    return {"job_id": job_id}


class RunTests(BaseModel):
    # A candidate config runs the suite against a Copilot proposal *before* it's
    # applied. Verifying a change before accepting it is the whole point.
    config: Optional[dict] = None
    case_ids: Optional[list] = None
    persist: bool = True
    # A proposal can change the suite as well as the graph — retiring a test it
    # judged wrong, adding one for the path it fixed. Verify has to run the suite
    # the engineer would end up with, or a retire-and-replace proposal can never
    # show as an improvement and the gate blocks the very fix it asked for.
    retire_ids: Optional[list] = None
    extra_cases: Optional[list] = None


@app.post("/api/agents/{agent_id}/tests/run")
async def run_tests(agent_id: str, body: RunTests):
    saved = _config_or_404(agent_id)
    config = body.config or saved
    cases = load_cases(agent_id)
    if body.case_ids:
        cases = [c for c in cases if c.id in body.case_ids]
    if body.retire_ids:
        drop = set(body.retire_ids)
        cases = [c for c in cases if c.id not in drop]
    for i, extra in enumerate(body.extra_cases or []):
        cases.append(TestCase.from_dict({**extra, "id": extra.get("id") or f"tc_proposed_{i}"}))

    job_id = _new_job("run_tests")
    JOBS[job_id]["progress"] = {"done": 0, "total": len(cases)}
    JOBS[job_id]["partial"] = []

    def on_result(result, done, total):
        JOBS[job_id]["progress"] = {"done": done, "total": total}
        JOBS[job_id].append_partial(
            {"case_id": result["case_id"], "name": result["name"], "passed": result["passed"]}
        )

    _run_job(
        job_id,
        lambda: run_suite(
            agent_id,
            AgentConfig.from_dict(config),
            cases=cases,
            # Only a run of the real suite against the real graph is worth
            # storing as "the last run" — anything hypothetical would misreport.
            persist=(
                body.persist
                and body.config is None
                and not body.retire_ids
                and not body.extra_cases
            ),
            on_result=on_result,
        ),
    )
    return {"job_id": job_id}


# --------------------------------------------------------- calls & issues ---
@app.get("/api/agents/{agent_id}/calls")
async def get_calls(agent_id: str):
    return {"calls": load_calls(agent_id)}


@app.get("/api/calls")
async def get_workspace_calls():
    """Every conversation in this workspace, whichever agent answered.

    The per-agent endpoint above is still what issue mining reads — a cluster of
    problems belongs to one graph. History is the human view, and a human wants
    the calls they received, not the calls the currently-selected agent
    received.
    """
    import repo

    if not db.enabled():
        return {"calls": load_calls(_first_agent_id() or "")}
    return {"calls": repo.load_workspace_calls()}


def _first_agent_id() -> Optional[str]:
    agents = store.list_agents()
    return agents[0]["id"] if agents else None


class ReplayCall(BaseModel):
    # Off by default: a proposed case should be reviewed before it starts
    # gating changes, exactly like a Copilot diff.
    save: bool = False


@app.post("/api/agents/{agent_id}/calls/{call_id}/replay")
async def replay_call(agent_id: str, call_id: str, body: ReplayCall):
    """Turn a recorded call into a regression test.

    This is the loop closing: a caller hits a gap, the call becomes a case, the
    case is what the Copilot's fix has to satisfy. Coverage then grows from what
    actually happens on the phone instead of from what a model imagines might.
    """
    config = _config_or_404(agent_id)
    call = next((c for c in load_calls(agent_id) if c.get("id") == call_id), None)
    if call is None:
        raise HTTPException(404, f"No call '{call_id}' for this agent.")

    agent = AgentConfig.from_dict(config)
    summary = "\n".join(
        [f"Nodes: {', '.join(n.name for n in agent.nodes)}", f"Entry: {agent.initial_node}"]
    )

    job_id = _new_job("replay_call", agent_id)

    async def work():
        case = await case_from_call(call, agent_summary=summary)
        if body.save:
            cases = load_cases(agent_id)
            cases.append(TestCase.from_dict(case))
            save_cases(agent_id, cases)
        return {"case": case, "saved": body.save}

    _run_job(job_id, work)
    return {"job_id": job_id}


@app.get("/api/agents/{agent_id}/issues")
async def get_issues(agent_id: str):
    return {"issues": load_issues(agent_id), "call_count": len(load_calls(agent_id))}


@app.post("/api/agents/{agent_id}/issues/analyze")
async def analyze_calls(agent_id: str):
    config = _config_or_404(agent_id)
    job_id = _new_job("analyze")

    async def work():
        issues = await mine_issues(agent_id, config)
        return {"issues": issues}

    _run_job(job_id, work)
    return {"job_id": job_id}


@app.post("/api/agents/{agent_id}/issues/{issue_id}/status")
async def update_issue(agent_id: str, issue_id: str, body: dict):
    return {"issues": set_issue_status(agent_id, issue_id, body.get("status", "open"))}


# ------------------------------------------------------------------- jobs ---
@app.get("/api/jobs/{job_id}")
async def get_job(job_id: str):
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "Unknown job.")
    return job


# ------------------------------------------------------------- test calls ---
@app.post("/api/agents/{agent_id}/call/offer")
async def call_offer(agent_id: str, request: dict, background_tasks: BackgroundTasks):
    """WebRTC offer -> answer. Starts a voice call against this agent."""
    config_dict = _config_or_404(agent_id)
    errors = [i for i in lint(AgentConfig.from_dict(config_dict)) if i.severity == "error"]
    if errors:
        raise HTTPException(400, "Agent has errors: " + "; ".join(i.message for i in errors))

    session_id = request.get("session_id") or f"call_{uuid.uuid4().hex[:10]}"
    session = new_session(session_id, agent_id)
    config = AgentConfig.from_dict(config_dict)
    session.trace("webrtc", f"offer received for agent '{agent_id}'")

    async def on_connection(connection):
        session.trace("webrtc", "peer connection established")
        background_tasks.add_task(run_call, connection, config, session)

    answer = await webrtc_handler.handle_web_request(
        request=SmallWebRTCRequest(
            sdp=request["sdp"],
            type=request["type"],
            pc_id=request.get("pc_id"),
            restart_pc=request.get("restart_pc"),
        ),
        webrtc_connection_callback=on_connection,
    )
    session.trace("webrtc", "answer sent")
    return {**(answer or {}), "session_id": session_id}


# --------------------------------------------------------------- telephony ---
# The secret is a path segment rather than a query parameter because Twilio
# echoes the full URL back in the signature payload, and because a path is
# harder to drop accidentally when re-pointing a number.
@app.post("/api/twilio/{secret}/voice")
async def twilio_voice(secret: str, request: Request):
    """Twilio's inbound-call webhook. Answers with TwiML that opens the stream."""
    form = dict(await request.form())
    reason = telephony.authorise(
        str(request.url), form, request.headers.get("X-Twilio-Signature", ""), secret
    )
    if reason:
        logger.warning(f"rejected Twilio webhook: {reason}")
        raise HTTPException(403, "Not authorised.")

    # Who answers is a runtime claim, not a URL parameter: the number gets
    # re-pointed from the UI, and a webhook baked with an agent id would keep
    # calling whoever set it up first.
    claim = telephony.assignment()
    agent_id = (
        claim.get("agent_id")
        or request.query_params.get("agent_id")
        or os.environ.get("TWILIO_AGENT_ID", "")
    )
    # An inbound call carries no session, so the tenant comes from whoever owns
    # the agent the number points at.
    org_id = auth.org_for_agent(agent_id) if agent_id else None
    if not agent_id or not org_id:
        return Response(
            telephony.reject_twiml("Sorry, this number is not configured. Goodbye."),
            media_type="application/xml",
        )

    with tenancy.as_org(org_id):
        if not store.exists(agent_id):
            return Response(
                telephony.reject_twiml("Sorry, this number is not configured. Goodbye."),
                media_type="application/xml",
            )
        # A graph with lint errors would fail mid-call, which for a real caller
        # means dead air. Better to say so and hang up.
        config = AgentConfig.from_dict(store.get_config(agent_id))
        if [i for i in lint(config) if i.severity == "error"]:
            return Response(
                telephony.reject_twiml("Sorry, this service is temporarily unavailable."),
                media_type="application/xml",
            )

    logger.info(f"inbound call {form.get('CallSid')} from {form.get('From')} -> {agent_id}")
    return Response(
        telephony.stream_twiml(
            agent_id,
            from_number=str(form.get("From", "")),
            to_number=str(form.get("To", "")),
        ),
        media_type="application/xml",
    )


@app.post("/api/twilio/{secret}/status")
async def twilio_status(secret: str, request: Request):
    """Call-progress callbacks. Used to close out the record with real duration."""
    form = dict(await request.form())
    if telephony.authorise(
        str(request.url), form, request.headers.get("X-Twilio-Signature", ""), secret
    ):
        raise HTTPException(403, "Not authorised.")
    logger.info(f"call {form.get('CallSid')} -> {form.get('CallStatus')}")
    return Response("", media_type="application/xml")


@app.websocket("/api/twilio/{secret}/media")
async def twilio_media(websocket: WebSocket, secret: str):
    """The bidirectional media stream — this is where the call actually happens.

    Twilio sends a `connected` frame and then a `start` frame carrying the SIDs
    and any <Parameter> values from the TwiML; the pipeline can't be built until
    the second one arrives, because the serializer needs the stream SID to
    address audio back at the right call.
    """
    if not hmac.compare_digest(secret, telephony.webhook_secret() or "\x00"):
        await websocket.close(code=1008)
        return
    await websocket.accept()

    try:
        start = None
        for _ in range(5):
            message = json.loads(await websocket.receive_text())
            if message.get("event") == "start":
                start = message
                break
        if start is None:
            await websocket.close(code=1008)
            return
    except Exception:
        return

    info = start.get("start", {})
    params = info.get("customParameters", {}) or {}
    agent_id = (
        params.get("agent_id")
        or telephony.assignment().get("agent_id")
        or os.environ.get("TWILIO_AGENT_ID", "")
    )
    stream_sid, call_sid = info.get("streamSid", ""), info.get("callSid", "")

    org_id = auth.org_for_agent(agent_id) if agent_id else None
    if not agent_id or not org_id:
        logger.warning(f"media stream for unknown agent '{agent_id}'")
        await websocket.close(code=1008)
        return

    # The whole call runs as the owning workspace, so the transcript is written
    # where its owner will find it rather than into whichever tenant happened to
    # be last.
    with tenancy.as_org(org_id):
        if not store.exists(agent_id):
            await websocket.close(code=1008)
            return
        record = store.get(agent_id)
        session = new_session(f"call_{call_sid[-10:] or uuid.uuid4().hex[:10]}", agent_id)
        session.channel = "twilio"
        session.provider_sid = call_sid
        session.from_number = params.get("from") or info.get("from", "")
        session.to_number = params.get("to") or info.get("to", "")
        session.agent_version = record.get("version")

        try:
            await run_phone_call(
                websocket, AgentConfig.from_dict(record["config"]), session, stream_sid, call_sid
            )
        finally:
            session.save()


class ClaimNumber(BaseModel):
    agent_id: str


# ------------------------------------------------------------- calendar ---
def _calendar_redirect_uri() -> str:
    base = telephony.public_base_url() or "https://localhost:7860"
    return f"{base}/api/integrations/google/callback"


@app.get("/api/calendar")
async def calendar_status():
    from integrations.calendar import connection

    if not db.enabled():
        return {"connected": False, "available": False}
    state = connection.status()
    state["available"] = bool(os.environ.get("GOOGLE_CLIENT_ID"))
    return state


@app.post("/api/calendar/connect")
async def calendar_connect(request: Request):
    """Begin the Google consent flow.

    The state token is a row in the database rather than a signed blob, because
    it also has to record *which workspace* is connecting — the callback arrives
    with no session of ours attached to it.
    """
    from integrations.calendar.google import authorize_url

    client_id = os.environ.get("GOOGLE_CLIENT_ID", "")
    if not client_id:
        raise HTTPException(400, "Google isn't configured on this deployment.")

    session = auth.read(request.cookies.get(auth.SESSION_COOKIE)) or {}
    state = uuid.uuid4().hex
    db.execute(
        "insert into oauth_states (state, org_id) values (%s, %s)",
        (state, tenancy.org()),
    )
    db.execute("delete from oauth_states where created_at < now() - interval '1 hour'")
    return {"url": authorize_url(client_id, _calendar_redirect_uri(), state)}


@app.get("/api/integrations/google/callback")
async def calendar_callback(request: Request):
    """Where Google sends the caller back.

    Deliberately outside the session gate: the browser arrives here straight
    from accounts.google.com, and the proof this is ours is the one-time state
    row, not a cookie.
    """
    from integrations.calendar import connection
    from integrations.calendar.google import exchange_code

    error = request.query_params.get("error")
    if error:
        return _calendar_done(f"Google returned '{error}'.")

    code = request.query_params.get("code", "")
    state = request.query_params.get("state", "")
    row = db.one("select org_id::text from oauth_states where state = %s", (state,))
    if not code or not row:
        return _calendar_done("That authorisation link has expired. Try again.")
    db.execute("delete from oauth_states where state = %s", (state,))

    try:
        tokens = await exchange_code(
            code,
            os.environ.get("GOOGLE_CLIENT_ID", ""),
            os.environ.get("GOOGLE_CLIENT_SECRET", ""),
            _calendar_redirect_uri(),
        )
    except Exception as exc:
        logger.warning(f"calendar exchange failed: {exc}")
        return _calendar_done(str(exc)[:160])

    email = ""
    if tokens.get("id_token"):
        # The id_token is a JWT whose payload carries the address. Google just
        # signed it over TLS, so reading the claim without re-verifying is fine
        # for a label in the UI.
        try:
            import base64 as _b64, json as _json

            payload = tokens["id_token"].split(".")[1]
            payload += "=" * (-len(payload) % 4)
            email = _json.loads(_b64.urlsafe_b64decode(payload)).get("email", "")
        except Exception:
            pass

    with tenancy.as_org(row["org_id"]):
        connection.save_connection(
            row["org_id"],
            tokens.get("access_token", ""),
            tokens.get("refresh_token", ""),
            tokens.get("expires_in", 3600),
            account_email=email,
            scopes=tokens.get("scope", ""),
        )
    return _calendar_done("")


def _calendar_done(error: str) -> Response:
    """A page that closes itself and tells the opener what happened."""
    ok = not error
    body = f"""<!doctype html><meta charset="utf-8">
<title>{'Calendar connected' if ok else 'Calendar not connected'}</title>
<body style="font:14px system-ui;background:#0b0b0c;color:#e7e7ea;
             display:grid;place-items:center;height:100vh;margin:0">
<div style="text-align:center;max-width:30rem;padding:2rem">
  <p style="font-size:1.1rem">{'Calendar connected.' if ok else 'Could not connect the calendar.'}</p>
  <p style="color:#9a9aa2">{error or 'You can close this window.'}</p>
</div>
<script>
  try {{ window.opener && window.opener.postMessage(
      {{ source: "composer-calendar", ok: {str(ok).lower()} }}, "*"); }} catch (e) {{}}
  setTimeout(function () {{ window.close(); }}, {1200 if ok else 6000});
</script>"""
    return Response(body, media_type="text/html")


@app.get("/api/calendar/calendars")
async def calendar_list():
    from integrations.calendar import connection

    try:
        return {"calendars": await connection.provider_for().calendars()}
    except Exception as exc:
        raise HTTPException(400, str(exc)[:200])


@app.post("/api/calendar/disconnect")
async def calendar_disconnect():
    from integrations.calendar import connection

    connection.disconnect()
    return {"connected": False}


@app.get("/api/phone")
async def phone_status(request: Request):
    """The shared number, who currently answers it, and whether that's you."""
    session = auth.read(request.cookies.get(auth.SESSION_COOKIE)) or {}
    claim = activation.active()
    mine = bool(claim.get("org_id")) and claim.get("org_id") == session.get("org")
    return {
        "number": telephony.shared_number(),
        "configured": bool(telephony.shared_number() and telephony.public_base_url()),
        "agent_id": claim.get("agent_id", ""),
        "claimed_by": claim.get("activated_by", ""),
        "mine": mine,
        # Calls land in the workspace that owns the live agent. When that isn't
        # yours, your history stays empty however many times you ring the number
        # — which reads as "history is broken" rather than "someone else has the
        # line", so it has to be said out loud.
        "elsewhere": bool(claim.get("agent_id")) and not mine,
    }


@app.post("/api/phone/claim")
async def phone_claim(body: ClaimNumber, request: Request):
    session = auth.read(request.cookies.get(auth.SESSION_COOKIE)) or {}
    config = _config_or_404(body.agent_id)      # 404s unless the agent is yours

    errors = [i for i in lint(AgentConfig.from_dict(config)) if i.severity == "error"]
    if errors:
        raise HTTPException(400, "Fix the agent's errors before putting it on the phone.")
    if not telephony.shared_number():
        raise HTTPException(400, "No phone number is configured on this deployment.")

    return {
        "assignment": telephony.assign(
            body.agent_id,
            tenancy.org(),
            claimed_by=str(session.get("sub") or ""),
        )
    }


@app.post("/api/phone/release")
async def phone_release():
    claim = telephony.assignment()
    if claim and claim.get("org_id") != tenancy.org():
        raise HTTPException(403, "Another account is using the number.")
    telephony.release()
    return {"released": True}


@app.get("/api/twilio/numbers")
async def twilio_numbers():
    """What the account owns, and whether this deployment can be reached."""
    if not telephony.configured():
        return {"configured": False, "numbers": [], "public_base_url": ""}
    try:
        numbers = await telephony.list_numbers()
    except Exception as exc:
        return {"configured": True, "error": str(exc), "numbers": [], "public_base_url": ""}
    return {
        "configured": True,
        "numbers": numbers,
        "public_base_url": telephony.public_base_url(),
        "signature_validation": bool(os.environ.get("TWILIO_AUTH_TOKEN")),
    }


@app.patch("/api/agents/{agent_id}/call/offer")
async def call_ice(agent_id: str, request: dict):
    from pipecat.transports.smallwebrtc.request_handler import IceCandidate

    await webrtc_handler.handle_patch_request(
        SmallWebRTCPatchRequest(
            pc_id=request["pc_id"],
            candidates=[
                IceCandidate(
                    candidate=c["candidate"],
                    sdp_mid=c.get("sdpMid") or c.get("sdp_mid"),
                    sdp_mline_index=c.get("sdpMLineIndex", c.get("sdp_mline_index", 0)),
                )
                for c in request.get("candidates", [])
            ],
        )
    )
    return {"status": "success"}


class ChatTurn(BaseModel):
    """One turn of the mic-free tester.

    Same graph, same node semantics, same prompts as a voice call — only the
    audio is missing. Stateless: the client owns the transcript, so there's no
    session to expire and a candidate config can be tested without saving it.
    """

    history: list = []                  # [{role: "user"|"assistant", content}]
    node: Optional[str] = None          # current node; defaults to the entry node
    message: Optional[str] = None       # what the caller just said
    config: Optional[dict] = None       # test an unsaved draft


@app.post("/api/agents/{agent_id}/chat")
async def chat_turn(agent_id: str, body: ChatTurn):
    saved = _config_or_404(agent_id)
    config = AgentConfig.from_dict(body.config or saved)

    history = list(body.history)
    if body.message:
        history.append({"role": "user", "content": body.message})

    step = await advance(config, body.node or config.initial_node, history)
    if step.error:
        raise HTTPException(400, step.error)

    for turn in step.turns:
        if turn.speaker == "agent":
            history.append({"role": "assistant", "content": turn.text})

    return {
        "turns": [t.to_dict() for t in step.turns],
        "node": step.node,
        "collected": step.collected,
        "ended": step.ended,
        "history": history,
    }


@app.get("/api/calls/live/{session_id}")
async def live_call(session_id: str):
    session = get_session(session_id)
    if session:
        return session.to_dict()
    # Fall back to the saved trace so a finished call is still inspectable
    # after a restart.
    saved = DEBUG_DIR / f"{session_id}.json"
    if saved.exists():
        return json.loads(saved.read_text(encoding="utf-8"))
    raise HTTPException(404, "Unknown call session.")


@app.get("/api/calls/recent")
async def recent_calls(limit: int = 15):
    """Every call this process has seen, newest first, plus any saved to disk."""
    seen: dict[str, dict] = {}
    if DEBUG_DIR.exists():
        for f in sorted(DEBUG_DIR.glob("*.json"), reverse=True)[: limit * 2]:
            try:
                d = json.loads(f.read_text(encoding="utf-8"))
                seen[d["session_id"]] = d
            except Exception:
                continue
    for s in SESSIONS.values():          # in-memory wins; it's more current
        seen[s.session_id] = s.to_dict()

    calls = sorted(seen.values(), key=lambda c: c.get("started_at", 0), reverse=True)
    return {
        "calls": [
            {
                "session_id": c["session_id"],
                "agent_id": c.get("agent_id"),
                "status": c.get("status"),
                "started_at": c.get("started_at"),
                "turns": len(c.get("turns", [])),
                "path": c.get("path", []),
                "audio": c.get("audio", {}),
                "warning": c.get("warning", ""),
                "error": c.get("error", ""),
            }
            for c in calls[:limit]
        ]
    }


@app.get("/api/logs")
async def tail_logs(lines: int = 200, grep: str = ""):
    """Tail the server log. Saves a round-trip to the filesystem when debugging."""
    path = LOG_DIR / "server.log"
    if not path.exists():
        return {"lines": [], "note": "No log file yet."}
    try:
        content = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception as exc:
        raise HTTPException(500, f"Could not read the log: {exc}")
    if grep:
        needle = grep.lower()
        content = [ln for ln in content if needle in ln.lower()]
    return {"lines": content[-max(1, min(lines, 2000)) :]}


# ----------------------------------------------------------------- static ---
@app.get("/api/health")
async def health():
    import os

    return {
        "ok": True,
        "keys": {
            "openai": bool(os.environ.get("OPENAI_API_KEY")),
            "elevenlabs": bool(os.environ.get("ELEVENLABS_API_KEY")),
        },
    }


if FRONTEND_DIST.exists():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")

    @app.get("/{full_path:path}")
    async def spa(full_path: str):
        if full_path.startswith("api/"):
            raise HTTPException(404)
        return FileResponse(FRONTEND_DIST / "index.html")

else:

    @app.get("/")
    async def no_ui():
        return JSONResponse(
            {
                "error": "The UI hasn't been built yet.",
                "fix": "cd frontend && npm install && npm run build   (or `npm run dev` for hot reload)",
            },
            status_code=503,
        )


if __name__ == "__main__":
    # Serve TLS when `python make_cert.py` has been run. Browsers hand out the
    # microphone only in a secure context, so a LAN visitor on plain http can't
    # make test calls — https, self-signed or not, is what unlocks them.
    #
    # Behind a reverse proxy this flips over: the proxy terminates a real
    # certificate and talks plain HTTP to us on the loopback, so shipping a
    # self-signed cert there would only give the proxy something to distrust.
    # Deployments set HOST=127.0.0.1 and leave certs/ absent.
    cert, key = Path(__file__).parent / "certs" / "dev.crt", Path(__file__).parent / "certs" / "dev.key"
    tls = cert.exists() and key.exists()

    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "7860"))
    scheme = "https" if tls else "http"
    logger.info(f"Agent Composer on {scheme}://{host}:{port}")
    if not tls and host != "127.0.0.1":
        logger.info("No cert found — run `python make_cert.py` to serve https (needed for mic on the LAN).")

    uvicorn.run(
        app,
        host=host,
        port=port,
        log_level="info",
        # Trust the proxy's forwarded headers, so request.url is the public
        # https:// URL. Twilio's signature is computed over that exact string —
        # validate against http://127.0.0.1 and every webhook is rejected.
        proxy_headers=True,
        forwarded_allow_ips=os.environ.get("FORWARDED_ALLOW_IPS", "127.0.0.1"),
        ssl_certfile=str(cert) if tls else None,
        ssl_keyfile=str(key) if tls else None,
    )
