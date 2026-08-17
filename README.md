# Agent Composer

A builder for voice-AI phone agents, and a Copilot that does the two jobs a deployment team actually spends its time on: turning client guidelines into a working agent, and fixing agents based on what goes wrong in production.

An agent is a graph of nodes (Pipecat Flows), defined as JSON, compiled into a live voice pipeline. Phase 1 is the editor and the test call. Phase 2 is everything that makes iterating on it fast.

**Live at https://212.227.246.190.sslip.io**, answering a real phone number.

> **This is the reference doc** — how to run it, what's in it, how to debug it.
> The design argument and the architectural decisions are in
> [`solution.md`](solution.md). Read that one first.

![The Copilot proposing a fix as a reviewable diff](docs/copilot-diff.png)

*A production issue, fixed: the Copilot proposes a new terminal node and a global edge that fires from anywhere, previewed on the canvas in green and amber. Nothing is applied until you accept it, and "Verify first" runs the test suite against the proposal before you do.*

---

## Quickstart

Requires **Python 3.11+**, [**uv**](https://docs.astral.sh/uv/getting-started/installation/), and **Node 18+**.

```bash
make install     # uv sync + npm install
make seed        # demo agent + 12 mock production calls
make build       # build the UI
make run         # http://localhost:7860
```

Put your keys in `backend/.env` (see `.env.example`):

```
ELEVENLABS_API_KEY=...   # speech in and out
OPENAI_API_KEY=...       # the voice agent, the Copilot, the simulator, and the judge
```

Nothing else is required. Supabase, Twilio and Google are all optional:
`STORE_BACKEND=files` runs the whole thing against a local directory with no
network dependency beyond the model APIs, and `AUTH_DISABLED=1` skips sign-in.
That combination is the recommended way to run this locally.

For UI hot reload, run `make dev` (port 5173) alongside `make run`; the dev server proxies `/api` to the Python app. `make reseed` resets the demo agent to v1 and clears its versions, tests, runs and issues, which is how you re-run the demo from scratch.

> **A note on ElevenLabs voices.** Free ElevenLabs plans cannot use *voice-library* voices via the API, and the failure is silent: the streaming socket connects, reports a time-to-first-byte, and returns no audio. A call that looks completely healthy and plays nothing. The default here is **Sarah** (`EXAVITQu4vr4xnSDxMaL`), a default voice that works on every plan. If you point an agent at a library voice on a free key, the call panel tells you so instead of just going quiet — see `probe_voice()` in `backend/voice.py`.

---

## A five-minute demo

1. **Issues.** Twelve calls, nine clustered issues, ranked. Open the critical one about emergency symptoms — one call, but life-safety, with the transcript quoted.
2. **Fix with Copilot.** It reads the flagged calls and proposes an emergency global edge that fires from any node, a terminal node that directs to 911, and a persona rule that outranks the rest of the flow. Unprompted, it also adds a human-transfer path, because the graph had none. The canvas previews all of it.
3. **Verify first.** The suite runs against the proposal before it's applied. Watch it fill in case by case.
4. **Apply.** New version, regression tests added, issue marked fixed.
5. **Test call.** Say *"I've had chest pain since last night."* The graph jumps to the emergency node and the agent tells you to hang up and dial 911.
6. **History.** Revert to v1 in one click if you don't like it.

The most honest moment is step 3, when a test goes red. The first time I ran it, one of the Copilot's own regression tests failed, and the failure was a badly written assertion rather than a broken agent: it asserted the agent should escalate *before* its greeting, which no correct agent can do. The judge said so precisely, quoting the transcript. A test suite you can argue with beats a green checkmark you can't.

---

## The shape of it

```
   DETECT                FIX                  VERIFY               SHIP              GUARD
   ──────                ───                  ──────               ────              ─────
   Read every call   →   Reviewable       →   Simulate the     →   Versioned     →   The fix ships
   and cluster the       graph diff,          suite against        apply, one-       with the test
   failures, with        not a rewrite        the proposal         click revert      that proves it
   quotes                                     before applying
```

Every arrow is automated; every box is inspectable. The deployment engineer stays the decision-maker — they read the diff, they see the test results, they click apply — but finding the problem, writing the change, and proving it's safe is done for them.

Why it's built this way is [`solution.md`](solution.md).

---

## Phase 1 — the builder

- **Graph canvas** with auto-layout (dagre). Start and terminal nodes, exit counts, live lint markers.
- **Free layout**, on by default: drag any node anywhere and it stays there, through inspector edits, panel resizes, patch previews and reloads, because positions are stored separately from node data and persisted per agent. Switch to **Auto** for the computed layout, or **Tidy** to forget an arrangement. Auto-layout is right for a graph you're reading; hand-placement is right for one you're reasoning about.

  ![Free layout, with one node dragged clear of the computed arrangement](docs/free-layout.png)

- **Node inspector** — instructions, exits, and the data each exit collects, all editable: field name, type, required flag, and the description the model actually reads. Renaming a node rewrites every reference, the same way the Copilot's `rename_node` op does.

  ![The node inspector with an exit expanded, showing the editable data fields](docs/inspector.png)

- **Graph linter**, running on every keystroke: unknown edge targets, duplicate names, required fields that were never declared, unreachable nodes, dead ends. Errors block a test call; warnings don't.
- **Browser test call** — WebRTC, ElevenLabs STT/TTS, your choice of LLM. The graph lights up node by node as you talk, with a live transcript and the data the agent has collected. Watching it take the wrong edge in real time is a much faster diagnosis than reading a transcript afterwards.
- **Chat mode, for when there's no microphone.** The same panel runs the conversation as text against the identical graph: same nodes, same prompts, same transitions, no audio. "No mic connected" shouldn't mean "can't test the agent", and typing a tricky caller turn is often faster than saying it. The node indicator and collected-data chips work the same in both modes.

  ![The tester in chat mode, with the live node highlighted on the graph](docs/test-call.png)

- **Version history** with one-click revert, labelled by what produced each version.
- **Real phone numbers.** An agent can be activated on a Twilio number and answer PSTN calls, not just WebRTC.

### Two changes to the agent format

**`global_edges`** — edges attached to every non-terminal node. Real callers interrupt the script from anywhere: *"wait, do you take my insurance?"*, *"can I speak to a person"*, and the one that matters, *"I'm having chest pain"*. Without them the only fix is copy-pasting the same edge onto twenty nodes, which is also twenty places to forget one. It's the single most common repair the Copilot makes, so it needed to be one operation.

**`transfer_to` on a node** — an E.164 number. Before this, `transfer_to_staff` said *"I'll pass you to a member of the team"* and hung up: a node claiming a capability the product didn't have. It now redirects the live call to `<Dial>`.

Everything else is the format the starter shipped with.

---

## What I mocked and left out

**Mocked, deliberately**

- *Production calls.* Twelve transcripts in `backend/seed.py`, written to be diagnostic rather than decorative: several share a root cause so the analyser has to cluster rather than list, and one is a clean happy path so it has to separate signal from noise. `load_calls()` is the only thing that changes when it reads a real call store.
- *Clinic availability.* `offer_times` hardcodes slots, which is *why* the demo agent has a dead end worth finding. The calendar integration exists (provider interface, Google adapter, OAuth, in-memory fake, 23 checks) but nothing in the graph uses it yet.

**Left out, deliberately**

- *Audio in the eval loop.* The simulator is text-only: no STT errors, no barge-in, no latency, no prosody. Those are real failure modes and this harness is blind to all of them; the browser test call is still how you check the agent *sounds* right. What it does cover is conversation logic — routing, collection, coverage, dead ends. That's where iteration time goes, and it's the half that runs 100× faster without audio.
- *Drag-to-connect edge editing.* Nodes can be dragged, but edges are edited in the inspector, not drawn on the canvas. Wiring boxes by hand is the part of a graph editor that looks best in a screenshot and matters least. The Copilot and the inspector both produce structurally valid graphs, and neither needs a canvas gesture.
- *Multi-language.* The miner surfaced it as issue #9 against a Spanish clinic. Real gap, but it's breadth, and it would have come out of the Copilot's budget.
- *Retention and redaction.* Transcripts hold names, dates of birth and reasons for visit. A real deployment needs a retention policy and redaction before any of that reaches a model prompt. Named rather than done — it's a compliance workstream.
- *Operational monitoring.* No dashboard of calls, latency or error rate, and no alerting. I think this is the biggest single gap for something answering a real line.
- *Streaming Copilot responses.* Patches are structured tool calls, which don't stream usefully. Long operations return a job id the UI polls, which is also what lets the test suite fill in case by case instead of blocking on a spinner.

**Known limits**

- The simulator mirrors Pipecat Flows' semantics (role message + node tasks, edges as tools, transition on call) but is a separate implementation, so the two could drift. Sharing `AgentConfig` and the edge-to-tool mapping keeps them honest; a shared runtime would be better.
- The judge is an LLM. Requiring a transcript quote for every verdict is the guard, and it's a real one, but a suite that's 90% green is a signal, not a proof.
- Determinism is best-effort. The graded path runs at temperature 0 with a pinned seed, but OpenAI's `seed` is not a guarantee and `gpt-5.1` accepts no temperature at all. Repeated runs on an unchanged graph hold steady, which is what the gate needs; it isn't the same as reproducibility.
- The linter catches structural problems. It cannot tell you the agent has no insurance path. That's what call analysis is for, and the split is deliberate.

---

## Layout

| Path | What's in it |
| --- | --- |
| `backend/agent_builder/` | `schema.py` the agent format · `builder.py` compiles it to Pipecat Flows, plus the graph linter · `patch.py` the operations the Copilot emits · `store.py` versioned storage |
| `backend/copilot/` | `prompts.py` format spec and voice-agent design rules · `agent.py` propose → apply → lint → self-correct · `verification.py` blast radius, simulation, scoring, reward-hacking guards · `diagnostics.py` structural signals and evidence rules · `memory.py` decision log replayed into later prompts |
| `backend/sim/` | `engine.py` headless graph runtime · `judge.py` LLM-as-judge with required evidence · `suite.py` test storage, generation, parallel runs · `replay.py` a real call into a test case |
| `backend/analysis/` | `miner.py` calls → clustered, ranked, evidenced issues |
| `backend/integrations/calendar/` | Provider interface, Google adapter, in-memory fake. Built, not yet wired into the graph |
| `backend/telephony.py`, `phone.py` | Twilio media streams, number provisioning, transfer via `<Dial>` |
| `backend/auth.py`, `tenancy.py`, `db.py` | Sign-in, per-account isolation, Supabase Postgres (all optional) |
| `backend/voice.py` | The live WebRTC call, with transcript and node-transition observation |
| `backend/server.py` | The API, the job queue, and the static UI |
| `backend/seed.py` | The demo agent and its mock call history |
| `frontend/src/` | React + React Flow. `CopilotPanel` is the interesting one |

### Models

Everything runs on OpenAI, split into three tiers by what each job demands (see `backend/llm.py`):

| Tier | Model | Job |
| --- | --- | --- |
| `COPILOT_MODEL` | `gpt-5.1` | Writes graph patches, mines call logs, generates test suites. Reasoning-heavy, low volume, and the product's quality rests on it. |
| `JUDGE_MODEL` | `gpt-4.1` | Grades transcripts against assertions. A wrong judge is worse than no judge, so it isn't the cheapest tier. |
| `SIM_MODEL` | `gpt-4.1-mini` | Plays simulated callers. Highest volume, easiest job — this is what keeps a full suite run to seconds. |

The agent under test runs on whatever model **its own config** declares (`gpt-4o` for the demo), so the harness measures the model that actually ships rather than a stand-in.

One implementation note: every request uses `max_completion_tokens`, never `max_tokens`. The gpt-5 family rejects the latter outright and the newer name is accepted by every model here, so one code path covers all of them.

---

## Debugging a call

Every test call is traced, always. A flag you have to remember to turn on is a flag that was off during the one call that misbehaved. Each call records a millisecond timeline (WebRTC handshake, voice preflight, VAD, STT interim and final, LLM, node transitions, TTS time-to-first-audio, peak amplitude) plus the transcript and the collected data.

- **In the UI**: the call panel has a **Trace** toggle, with **Copy** to lift the whole timeline to the clipboard.
- **On disk**: `backend/logs/server.log` (rotating), and one JSON per call in `backend/data/calls_debug/`, which survives a restart.
- **From the CLI**: `python dump_call.py` prints the last call — timeline, transcript, audio stats and the matching log lines. `--list` shows what's available; pass a session id for a specific one.
- **Over HTTP**: `GET /api/calls/recent`, `GET /api/calls/live/{id}`, `GET /api/logs?lines=200&grep=elevenlabs`.

The audio summary is the part worth knowing about: it reports chunk count, KB and **peak amplitude**, and flags `silent` when the agent produced words but every audio chunk was quiet. That one number separates "TTS is broken" from "your speakers are wrong", which is otherwise a long guessing game.

---

## Checks

There's no unit-test framework here. There are executable checks that exercise real behaviour and print what they verified.

```bash
# Backend
uv run --directory backend python check_copilot_loop.py   # verify loop, scoring, reward-hacking guards, coherence, decision memory (model stubbed — free and fast)
uv run --directory backend python check_tenancy.py        # two accounts cannot see each other's agents, calls, tests or issues
uv run --directory backend python check_store.py          # version history stays consistent under four concurrent writes
uv run --directory backend python check_calendar.py       # slot arithmetic, double-booking, idempotency, timezones
uv run --directory backend python check_twilio.py         # media-stream endpoint with real 8kHz μ-law, no phone number needed
uv run --directory backend python check_call.py           # a real WebRTC call via aiortc
uv run --directory backend python smoke.py [sim|copilot|mine]   # each AI piece, from the CLI

# Frontend (real Chrome)
cd frontend
node check-ui.mjs         # render every tab, fail on any console error
node check-copilot.mjs    # Issues -> Fix with Copilot -> diff preview
node check-diagnose.mjs   # a failing run -> diagnosis with evidence
node check-findings.mjs   # structured findings and their citations
node check-retire.mjs     # retiring a test, and that it scores nothing
node check-history.mjs    # version history and revert
node check-agents.mjs     # agent list, create, switch
node check-signin.mjs     # sign-in flow
node check-call.mjs       # browser test call with a fake mic
node check-chat.mjs       # the mic-free chat tester
node check-inspector.mjs  # open a node, expand an exit, edit its fields
node check-drag.mjs       # free layout: drag, re-render, reload, mode switches
```

Start with `check_copilot_loop.py`, where the Copilot's judgement is pinned down, and `check_tenancy.py`, whose failure would show one clinic another clinic's patients.

The `check-*.mjs` scripts drive your installed Chrome through `puppeteer-core` and write screenshots to `frontend/shots/` (the images in this README came from them). They fail on any console error, page error or failed request, which is how I caught UI problems a type-check wouldn't. **They point at a Windows Chrome path by default** — change the `CHROME` constant at the top of each file to `/Applications/Google Chrome.app/Contents/MacOS/Google Chrome` on macOS.
