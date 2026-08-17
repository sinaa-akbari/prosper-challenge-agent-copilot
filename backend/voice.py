#
# The live voice runtime — one browser test call against one saved agent.
#
# This is the starter's bot.py, with two changes that matter for the builder UI:
#
#   1. The agent is chosen per call (by id, from the store) instead of being a
#      module-level constant, so "place a test call" always exercises exactly
#      what's on screen.
#   2. The call is observed. Transcript turns and node transitions are recorded
#      into a session object the UI polls, so the graph lights up node-by-node
#      while you talk to it. Watching the agent take the wrong edge in real time
#      is a much faster diagnosis than reading a transcript afterwards.
#

import asyncio
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from loguru import logger
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    ErrorFrame,
    FunctionCallInProgressFrame,
    InterimTranscriptionFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    TranscriptionFrame,
    TTSAudioRawFrame,
    TTSStartedFrame,
    TTSStoppedFrame,
    TTSTextFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.services.elevenlabs.stt import ElevenLabsRealtimeSTTService
from pipecat.services.elevenlabs.tts import ElevenLabsTTSService
from pipecat.transports.base_transport import TransportParams
from pipecat.transports.smallwebrtc.transport import SmallWebRTCTransport
from pipecat.workers.runner import WorkerRunner
from pipecat_flows import FlowManager

from agent_builder import AgentBuilder
from agent_builder.schema import AgentConfig
from llm import is_supported_model

IDLE_TIMEOUT_SECS = 180

# A phone caller who has stopped talking has usually gone. Shorter than the
# WebRTC timeout because every extra second here is billed by two vendors.
PHONE_SESSION_TIMEOUT_SECS = 300
DEBUG_DIR = Path(__file__).resolve().parent / "data" / "calls_debug"


# --------------------------------------------------------------- sessions ---
@dataclass
class LiveCall:
    """Everything the UI needs to render a call in progress."""

    session_id: str
    agent_id: str
    status: str = "connecting"          # connecting | live | ended | error
    started_at: float = field(default_factory=time.time)
    current_node: str = ""
    path: list = field(default_factory=list)
    turns: list = field(default_factory=list)     # {speaker, text, at}
    collected: dict = field(default_factory=dict)
    error: str = ""
    # Non-fatal problems the caller should still be told about — chiefly a voice
    # the account can't use, which otherwise presents as a working, silent call.
    warning: str = ""

    # Where the call came from, and who was on it. Only populated for PSTN.
    channel: str = "webrtc"             # webrtc | twilio
    from_number: str = ""
    to_number: str = ""
    provider_sid: str = ""
    agent_version: Optional[int] = None
    # Set when the flow enters a node with transfer_to; acted on once the agent
    # has finished its handoff sentence, so the caller isn't cut off mid-word.
    pending_transfer: str = ""
    transferred_to: str = ""
    # Set the moment a handover is decided, not when it completes — teardown
    # runs concurrently and must never hang up a call being transferred.
    transfer_started: bool = False
    transfer_failed: str = ""

    # Debug telemetry.
    events: list = field(default_factory=list)   # {at, ms, kind, detail, level}
    audio_bytes: int = 0
    audio_frames: int = 0
    audio_peak: float = 0.0

    def trace(self, kind: str, detail: str = "", level: str = "info") -> None:
        """Append a timestamped event. Cheap, bounded, and always on.

        Tracing only when a flag is set means the one call that misbehaves is
        the one that wasn't traced, so this runs for every call.
        """
        now = time.time()
        self.events.append(
            {
                "at": now,
                "ms": int((now - self.started_at) * 1000),
                "kind": kind,
                "detail": (detail or "")[:500],
                "level": level,
            }
        )
        if len(self.events) > 600:      # a long call shouldn't grow without bound
            del self.events[:200]
        log = getattr(logger, level, logger.info)
        log(f"[{self.session_id}] {kind}: {detail}")

    def add_turn(self, speaker: str, text: str) -> None:
        text = (text or "").strip()
        if not text:
            return
        # TTS emits a sentence at a time; merge consecutive agent fragments so
        # the transcript reads as speech rather than as chunks.
        if self.turns and self.turns[-1]["speaker"] == speaker and speaker == "agent":
            self.turns[-1]["text"] = f"{self.turns[-1]['text']} {text}".strip()
        else:
            self.turns.append({"speaker": speaker, "text": text, "at": time.time()})

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "agent_id": self.agent_id,
            "status": self.status,
            "started_at": self.started_at,
            "current_node": self.current_node,
            "path": self.path,
            "turns": self.turns,
            "collected": self.collected,
            "error": self.error,
            "warning": self.warning,
            "events": self.events,
            "audio": {
                "frames": self.audio_frames,
                "kb": self.audio_bytes // 1024,
                "peak": round(self.audio_peak, 3),
                # The single most useful derived fact: the agent produced words
                # but the caller heard nothing.
                "silent": self.audio_frames > 0 and self.audio_peak == 0.0,
            },
        }

    def outcome(self) -> str:
        """How the call ended, in the vocabulary the issue miner already speaks."""
        if self.transferred_to:
            return "transferred"
        if self.transfer_failed:
            return "transfer_failed"
        if self.error:
            return "error"
        if not self.turns:
            return "no_audio"
        return "completed"

    def save(self) -> None:
        """Persist the finished call.

        Two destinations with different jobs: the debug file keeps the full
        event trace for whoever is staring at a broken call right now, and the
        calls table keeps the transcript so the call becomes evidence — minable
        into an issue, replayable into a test case. Only the second one matters
        a week later.
        """
        try:
            DEBUG_DIR.mkdir(parents=True, exist_ok=True)
            path = DEBUG_DIR / f"{self.session_id}.json"
            path.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")
        except Exception as exc:
            logger.warning(f"could not save call trace: {exc}")

        try:
            import db

            if not db.enabled():
                return
            import repo

            repo.save_call(
                self.agent_id,
                {
                    "id": self.session_id,
                    "source": self.channel,
                    "outcome": self.outcome(),
                    "duration_s": round(time.time() - self.started_at, 1),
                    "turns": [
                        {"speaker": t["speaker"], "text": t["text"]} for t in self.turns
                    ],
                    "path": self.path,
                    "collected": self.collected,
                    "agent_version": self.agent_version,
                    "from_number": self.from_number,
                    "to_number": self.to_number,
                    "provider_sid": self.provider_sid,
                    "metadata": {
                        "error": self.error,
                        "warning": self.warning,
                        "audio_peak": round(self.audio_peak, 3),
                        "transferred_to": self.transferred_to,
                        "transfer_failed": self.transfer_failed,
                    },
                },
            )
        except Exception as exc:
            # A call that happened is worth more than a tidy database; never let
            # a write failure surface as a call failure.
            logger.warning(f"could not persist call {self.session_id}: {exc}")


SESSIONS: dict[str, LiveCall] = {}


def get_session(session_id: str) -> Optional[LiveCall]:
    return SESSIONS.get(session_id)


def new_session(session_id: str, agent_id: str) -> LiveCall:
    session = LiveCall(session_id=session_id, agent_id=agent_id)
    SESSIONS[session_id] = session
    # Keep memory bounded during a long demo.
    if len(SESSIONS) > 40:
        for old in sorted(SESSIONS.values(), key=lambda s: s.started_at)[:10]:
            SESSIONS.pop(old.session_id, None)
    return session


def _peak_amplitude(audio: bytes, samples: int = 400) -> float:
    """Loudest sample in a PCM16 buffer, estimated by striding across all of it.

    Sampling only the head of the buffer reads the leading silence before speech
    starts and reports a near-zero peak for perfectly good audio — which would
    make the silent-call detector cry wolf on every call.
    """
    count = len(audio) // 2
    if count == 0:
        return 0.0
    stride = max(1, count // samples)
    return (
        max(
            abs(int.from_bytes(audio[i * 2 : i * 2 + 2], "little", signed=True))
            for i in range(0, count, stride)
        )
        / 32768.0
    )


# --------------------------------------------------------------- observer ---
class CallRecorder(FrameProcessor):
    """Passthrough processor that records the call as it flows past.

    It sits at two points in the pipeline — after STT and after TTS — and turns
    the frame stream into two things: the transcript the UI renders, and a
    timestamped trace for debugging. The trace is the useful half when something
    goes wrong, because the interesting failures here are all about *what didn't
    happen*: VAD never fired, STT returned nothing, the LLM answered but TTS
    produced no audio. A transcript can't show an absence; a timeline can.
    """

    def __init__(self, session: LiveCall, role: str):
        super().__init__()
        self._session = session
        self._role = role
        self._tts_started: Optional[float] = None

    async def process_frame(self, frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        try:
            self._observe(frame)
        except Exception as exc:
            logger.warning(f"CallRecorder failed: {exc}")
        await self.push_frame(frame, direction)

    def _observe(self, frame) -> None:
        s = self._session

        # --- errors, from either side ------------------------------------
        if isinstance(frame, ErrorFrame):
            s.warning = str(getattr(frame, "error", frame))[:400]
            s.trace("error", s.warning, level="error")
            return

        if self._role == "caller":
            if isinstance(frame, UserStartedSpeakingFrame):
                s.trace("vad", "caller started speaking")
            elif isinstance(frame, UserStoppedSpeakingFrame):
                s.trace("vad", "caller stopped speaking")
            elif isinstance(frame, InterimTranscriptionFrame):
                s.trace("stt.interim", frame.text)
            elif isinstance(frame, TranscriptionFrame):
                s.add_turn("caller", frame.text)
                s.trace("stt.final", frame.text)
            return

        # --- agent side ---------------------------------------------------
        if isinstance(frame, LLMFullResponseStartFrame):
            s.trace("llm", "response started")
        elif isinstance(frame, LLMFullResponseEndFrame):
            s.trace("llm", "response complete")
        elif isinstance(frame, FunctionCallInProgressFrame):
            s.trace("tool", f"calling {getattr(frame, 'function_name', '?')}")
        elif isinstance(frame, TTSStartedFrame):
            self._tts_started = time.time()
            s.trace("tts", "synthesis started")
        elif isinstance(frame, TTSTextFrame):
            # TTS emits a frame per word. The transcript stitches them together;
            # tracing each one would bury every structural event under a wall of
            # single words.
            s.add_turn("agent", frame.text)
        elif isinstance(frame, TTSAudioRawFrame):
            # The decisive signal. Silence here with text above means TTS
            # accepted the request and returned nothing — the failure mode a
            # transcript cannot show.
            audio = getattr(frame, "audio", b"") or b""
            s.audio_bytes += len(audio)
            if audio:
                s.audio_peak = max(s.audio_peak, _peak_amplitude(audio))
                if s.audio_frames == 0:
                    ttfb = time.time() - (self._tts_started or time.time())
                    s.trace("tts.audio", f"first audio after {ttfb:.2f}s")
                s.audio_frames += 1
        elif isinstance(frame, TTSStoppedFrame):
            s.trace(
                "tts",
                f"synthesis finished · {s.audio_frames} chunks, "
                f"{s.audio_bytes // 1024} KB, peak {s.audio_peak:.3f}",
                level="warning" if s.audio_peak == 0 else "info",
            )
        elif isinstance(frame, BotStartedSpeakingFrame):
            s.trace("speak", "agent started speaking")
        elif isinstance(frame, BotStoppedSpeakingFrame):
            s.trace("speak", "agent stopped speaking")
            # Waiting for silence matters: redirecting the call tears down the
            # media stream immediately, so transferring the moment the node is
            # entered cuts the agent off halfway through "putting you through
            # now". This is the first point where nothing is being said.
            if s.pending_transfer:
                number, s.pending_transfer = s.pending_transfer, ""
                asyncio.create_task(_hand_over(s, number))


async def _hand_over(session: "LiveCall", number: str) -> None:
    """Pass a live PSTN call to a person."""
    import telephony

    session.trace("transfer", f"handing the caller to {number}")
    try:
        await telephony.transfer(session.provider_sid, number)
    except Exception as exc:
        # Undo the optimistic record: the caller is still with us, and the
        # history should say the handover failed rather than claim it happened.
        session.transferred_to = ""
        session.transfer_failed = str(exc)[:200]
        session.trace("error", f"transfer to {number} failed: {exc}", level="error")


# ---------------------------------------------------------- voice preflight ---
_voice_probe_cache: dict[str, str] = {}


async def probe_voice(voice_id: str) -> str:
    """Return "" if the account can synthesise with this voice, else why not.

    ElevenLabs fails this case quietly over the streaming socket: it connects,
    reports a time-to-first-byte, and sends no audio, so the call looks healthy
    and is silent. One cheap HTTP synthesis up front turns that into a message.
    Cached per voice per process, so it costs one request, once.
    """
    if voice_id in _voice_probe_cache:
        return _voice_probe_cache[voice_id]

    key = os.environ.get("ELEVENLABS_API_KEY", "")
    if not key:
        result = "ELEVENLABS_API_KEY is not set, so the agent cannot speak."
        _voice_probe_cache[voice_id] = result
        return result

    result = ""
    try:
        import aiohttp

        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}",
                headers={"xi-api-key": key, "Content-Type": "application/json"},
                json={"text": ".", "model_id": "eleven_turbo_v2_5"},
                timeout=aiohttp.ClientTimeout(total=12),
            ) as resp:
                if resp.status != 200:
                    body = await resp.json(content_type=None)
                    detail = (body or {}).get("detail", {})
                    message = (
                        detail.get("message") if isinstance(detail, dict) else str(detail)
                    ) or f"HTTP {resp.status}"
                    result = f"Voice '{voice_id}' is unavailable: {message}"
    except Exception as exc:
        # A probe failure shouldn't block a call — the real socket may still work.
        logger.warning(f"voice probe failed for {voice_id}: {exc}")

    _voice_probe_cache[voice_id] = result
    return result


# ------------------------------------------------------------------- LLMs ---
def build_llm(model: str):
    """Build the voice pipeline's LLM service for an agent's declared model."""
    from pipecat.services.openai.llm import OpenAILLMService

    if not is_supported_model(model):
        raise RuntimeError(
            f"Agent declares model '{model}', which isn't an OpenAI model. "
            "This project runs on OpenAI only — use a gpt-* or o-series id."
        )
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        raise RuntimeError(f"Agent uses '{model}' but OPENAI_API_KEY is not set.")
    return OpenAILLMService(
        api_key=key, settings=OpenAILLMService.Settings(model=model)
    )


# ------------------------------------------------------------------- call ---
async def _drive(transport, config: AgentConfig, session: LiveCall, channel: str) -> None:
    """Run one call over an already-built transport.

    Everything above the transport — STT, the LLM, TTS, the flow manager and the
    recorder — is identical whether the audio arrives over WebRTC from a browser
    or over a WebSocket from Twilio. Keeping that shared isn't tidiness: it's the
    only way a call placed from the test panel is evidence about what happens on
    the phone. The moment the two paths diverge, the builder stops predicting
    production.
    """
    session.current_node = config.initial_node
    session.path = [config.initial_node]
    session.trace(
        "call",
        f"starting '{config.name}' · model={config.model} voice={config.voice_id}",
    )

    # Catch an unusable voice before the caller sits through a silent call.
    session.warning = await probe_voice(config.voice_id)
    session.trace(
        "voice",
        session.warning or f"voice {config.voice_id} verified",
        level="error" if session.warning else "info",
    )

    nodes_by_name = {n.name: n for n in config.nodes}

    def on_transition(function: str, target: str, args: dict) -> None:
        session.current_node = target
        session.path.append(target)
        session.collected.update(args or {})
        session.trace("flow", f"{function} -> {target} {args or ''}")

        # Reaching a transfer node means the agent is done and a person takes
        # over. Only a real phone call can be handed to a phone, so this is a
        # no-op in the browser tester and in simulation — which is worth knowing
        # when a test says the transfer "worked".
        node = nodes_by_name.get(target)
        if node is not None and node.transfer_to and session.provider_sid:
            session.pending_transfer = node.transfer_to
            # Both set synchronously. The REST call happens in a background task
            # and the stream tears down the moment Twilio accepts the redirect,
            # so anything recorded after the await can lose the race with save()
            # — and a transferred call filed as "completed" is a lie in the
            # history the whole feature exists to produce.
            session.transfer_started = True
            session.transferred_to = node.transfer_to

    builder = AgentBuilder(config, on_transition=on_transition)

    stt = ElevenLabsRealtimeSTTService(api_key=os.environ["ELEVENLABS_API_KEY"])
    tts = ElevenLabsTTSService(
        api_key=os.environ["ELEVENLABS_API_KEY"],
        settings=ElevenLabsTTSService.Settings(voice=config.voice_id),
    )
    llm = build_llm(config.model)

    context = LLMContext()
    context_aggregator = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(vad_analyzer=SileroVADAnalyzer()),
    )

    stages = [
        transport.input(),
        stt,
        CallRecorder(session, "caller"),
        context_aggregator.user(),
        llm,
        tts,
        CallRecorder(session, "agent"),
        transport.output(),
        context_aggregator.assistant(),
    ]
    if os.environ.get("NO_CALL_RECORDER") == "1":
        stages = [s for s in stages if not isinstance(s, CallRecorder)]
        logger.warning("CallRecorder disabled — no live transcript for this call")

    pipeline = Pipeline(stages)

    worker = PipelineWorker(
        pipeline,
        params=PipelineParams(enable_metrics=True, enable_usage_metrics=True),
        idle_timeout_secs=IDLE_TIMEOUT_SECS,
    )

    flow_manager = FlowManager(
        llm=llm,
        context_aggregator=context_aggregator,
        worker=worker,
        transport=transport,
    )

    @transport.event_handler("on_client_connected")
    async def on_client_connected(_transport, _client):
        session.status = "live"
        session.trace(channel, "connected — initialising flow")
        await flow_manager.initialize(builder.build_initial_node())
        session.trace("flow", f"entered {config.initial_node}")

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(_transport, _client):
        session.trace(channel, "disconnected")
        session.status = "ended"
        await worker.cancel()

    runner = WorkerRunner(handle_sigint=False)
    await runner.add_workers(worker)
    await runner.run()


async def run_call(connection, config: AgentConfig, session: LiveCall) -> None:
    """Run one WebRTC call to completion. Exceptions land on the session record."""
    try:
        transport = SmallWebRTCTransport(
            webrtc_connection=connection,
            params=TransportParams(audio_in_enabled=True, audio_out_enabled=True),
        )
        await _drive(transport, config, session, "webrtc")
    except Exception as exc:
        logger.exception(f"[{session.session_id}] call failed")
        session.status = "error"
        session.error = f"{type(exc).__name__}: {exc}"
        session.trace("error", session.error, level="error")
    finally:
        if session.status not in ("error",):
            session.status = "ended"


async def run_phone_call(
    websocket,
    config: AgentConfig,
    session: LiveCall,
    stream_sid: str,
    call_sid: str,
) -> None:
    """Run one inbound PSTN call arriving over a Twilio Media Stream.

    Twilio speaks 8kHz mu-law over a WebSocket. The serializer handles that
    conversion in both directions, so the only real difference from a browser
    call is the sample rate — which matters more than it sounds, because ASR on
    8kHz telephony audio is meaningfully worse than on 16kHz browser audio, and
    the identity-verification flow depends on hearing dates of birth correctly.
    """
    from pipecat.serializers.twilio import TwilioFrameSerializer
    from pipecat.transports.websocket.fastapi import (
        FastAPIWebsocketParams,
        FastAPIWebsocketTransport,
    )

    try:
        # auto_hang_up stays off, and this is not an oversight.
        #
        # Transferring works by replacing the call's TwiML with <Dial>, which
        # tears down the media stream — which ends the pipeline — which makes
        # the serializer hang up the call it thinks is finished. Thirty-four
        # milliseconds after a successful transfer it killed the line. The
        # symptom was "transferring just hangs up", and the cause was a feature
        # that only switched itself on once the auth token existed.
        #
        # Ending a normal call is handled explicitly below instead, where we
        # know whether a handover is in flight.
        serializer = TwilioFrameSerializer(
            stream_sid=stream_sid,
            call_sid=call_sid,
            account_sid=os.environ.get("TWILIO_ACCOUNT_SID"),
            auth_token=os.environ.get("TWILIO_AUTH_TOKEN") or None,
            params=TwilioFrameSerializer.InputParams(auto_hang_up=False),
        )
        transport = FastAPIWebsocketTransport(
            websocket=websocket,
            params=FastAPIWebsocketParams(
                audio_in_enabled=True,
                audio_out_enabled=True,
                add_wav_header=False,
                serializer=serializer,
                session_timeout=PHONE_SESSION_TIMEOUT_SECS,
            ),
        )
        session.trace("twilio", f"media stream {stream_sid} for call {call_sid}")
        await _drive(transport, config, session, "twilio")
    except Exception as exc:
        logger.exception(f"[{session.session_id}] phone call failed")
        session.status = "error"
        session.error = f"{type(exc).__name__}: {exc}"
        session.trace("error", session.error, level="error")
    finally:
        if session.status not in ("error",):
            session.status = "ended"
        # A handed-over call belongs to Twilio's <Dial> now; hanging up here
        # would cut off the person who just answered.
        if session.provider_sid and not session.transfer_started:
            import telephony

            await telephony.hangup(session.provider_sid)
        # A call that produced words but no sound is the failure worth naming.
        if session.audio_frames and session.audio_peak == 0.0:
            session.warning = session.warning or (
                "The agent spoke but every audio chunk was silent — TTS returned "
                "no sound. Check the voice and the ElevenLabs plan."
            )
        session.trace(
            "call",
            f"ended · {len(session.turns)} turns, {session.audio_frames} audio chunks, "
            f"peak {session.audio_peak:.3f}",
        )
        session.save()
