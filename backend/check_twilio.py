"""Dev-only: drive the Twilio media-stream endpoint without a phone number.

The PSTN path is the one piece that can't be exercised from the browser, and
waiting on a provisioned number to find out whether it works is a bad trade. So
this pretends to be Twilio: it speaks the same WebSocket protocol, sends 8kHz
mu-law frames at real time, and checks that intelligible audio comes back.

Caller speech is synthesised through ElevenLabs at ulaw_8000, which is the exact
encoding Twilio uses — so this also exercises the resampling, which is where
telephony audio bugs actually live.

    python check_twilio.py                    # greeting only
    python check_twilio.py --say "I want to book an appointment"
"""

import asyncio
import base64
import json
import os
import ssl
import sys
import time
import uuid
from pathlib import Path

import aiohttp

sys.path.insert(0, str(Path(__file__).parent))
from dotenv import load_dotenv  # noqa: E402

load_dotenv(Path(__file__).parent / ".env", override=True)

BASE = os.environ.get("CHECK_BASE", "wss://localhost:7860")
AGENT = os.environ.get("CHECK_AGENT", "northside-scheduling")
SECRET = os.environ.get("TWILIO_WEBHOOK_SECRET", "")
CHUNK = 160          # 20ms of 8kHz mu-law, the size Twilio actually sends

PROBLEMS = []


def note(ok: bool, msg: str) -> None:
    print(f"  {'ok  ' if ok else 'FAIL'}  {msg}")
    if not ok:
        PROBLEMS.append(msg)


def ulaw_energy(payload: bytes) -> float:
    """Rough loudness of a mu-law buffer, 0..1.

    Decoding properly would need audioop, which 3.13 removed. The sign/magnitude
    layout means the low 7 bits are an inverted magnitude, and that is plenty to
    tell speech from silence.
    """
    if not payload:
        return 0.0
    peak = max((~b) & 0x7F for b in payload)
    return peak / 127.0


async def synth_ulaw(text: str) -> bytes:
    """Caller speech, in the encoding Twilio would deliver it in."""
    key = os.environ.get("ELEVENLABS_API_KEY")
    if not key:
        return b""
    url = (
        "https://api.elevenlabs.io/v1/text-to-speech/EXAVITQu4vr4xnSDxMaL"
        "?output_format=ulaw_8000"
    )
    async with aiohttp.ClientSession() as s:
        async with s.post(
            url,
            headers={"xi-api-key": key, "Content-Type": "application/json"},
            json={"text": text, "model_id": "eleven_turbo_v2_5"},
            timeout=aiohttp.ClientTimeout(total=30),
        ) as r:
            if r.status != 200:
                print(f"  (TTS failed {r.status}: {(await r.text())[:120]})")
                return b""
            return await r.read()


async def main() -> None:
    say = ""
    if "--say" in sys.argv:
        say = sys.argv[sys.argv.index("--say") + 1]

    if not SECRET:
        sys.exit("TWILIO_WEBHOOK_SECRET is not set — the endpoint would reject us.")

    stream_sid = f"MZ{uuid.uuid4().hex[:30]}"
    call_sid = f"CA{uuid.uuid4().hex[:30]}"
    url = f"{BASE}/api/twilio/{SECRET}/media"
    print(f"connecting to {url}\n")

    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    inbound_audio = bytearray()
    events: list[str] = []
    closed_out = {"yes": False}

    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(url, ssl=ctx, timeout=30) as ws:
            note(True, "media socket accepted the connection")

            await ws.send_str(json.dumps(
                {"event": "connected", "protocol": "Call", "version": "1.0.0"}
            ))
            await ws.send_str(json.dumps({
                "event": "start",
                "sequenceNumber": "1",
                "streamSid": stream_sid,
                "start": {
                    "streamSid": stream_sid,
                    "callSid": call_sid,
                    "accountSid": os.environ.get("TWILIO_ACCOUNT_SID", ""),
                    "tracks": ["inbound"],
                    "mediaFormat": {
                        "encoding": "audio/x-mulaw",
                        "sampleRate": 8000,
                        "channels": 1,
                    },
                    "customParameters": {"agent_id": AGENT, "from": "+34600000000",
                                         "to": "+34900000000"},
                },
            }))

            # The server closing the socket is a normal end state — a terminal
            # node or a transfer tears the stream down — so both pumps stop
            # rather than treating it as a failure.
            closed = {"yes": False}

            async def send(payload: bytes) -> bool:
                try:
                    await ws.send_str(json.dumps({
                        "event": "media",
                        "streamSid": stream_sid,
                        "media": {"payload": base64.b64encode(payload).decode()},
                    }))
                except Exception:
                    closed["yes"] = True
                    closed_out["yes"] = True
                    return False
                await asyncio.sleep(0.02)
                return True

            async def pump_silence(seconds: float) -> None:
                """Twilio streams continuously; silence keeps VAD's clock honest."""
                for _ in range(int(seconds * 50)):
                    if not await send(b"\xff" * CHUNK):
                        return

            async def pump_audio(ulaw: bytes) -> None:
                for i in range(0, len(ulaw), CHUNK):
                    if not await send(ulaw[i:i + CHUNK]):
                        return

            async def reader() -> None:
                async for msg in ws:
                    if msg.type is not aiohttp.WSMsgType.TEXT:
                        continue
                    try:
                        data = json.loads(msg.data)
                    except json.JSONDecodeError:
                        continue
                    kind = data.get("event", "?")
                    if kind not in events:
                        events.append(kind)
                    if kind == "media":
                        inbound_audio.extend(base64.b64decode(data["media"]["payload"]))

            read_task = asyncio.create_task(reader())

            # The agent speaks first; give it room to greet.
            print("  waiting for the greeting…")
            await pump_silence(9)
            greeting_bytes = len(inbound_audio)
            note(greeting_bytes > 0, f"agent sent audio unprompted ({greeting_bytes} bytes of mu-law)")
            note(
                ulaw_energy(bytes(inbound_audio[:8000])) > 0.1,
                f"greeting audio carries signal (peak {ulaw_energy(bytes(inbound_audio[:8000])):.2f})",
            )

            if say:
                print(f"  caller says: {say!r}")
                ulaw = await synth_ulaw(say)
                note(len(ulaw) > 0, f"synthesised caller speech ({len(ulaw)} bytes)")
                if ulaw:
                    before = len(inbound_audio)
                    await pump_audio(ulaw)
                    # Wait for a reply rather than a fixed window. ElevenLabs
                    # realtime STT sometimes takes many seconds to emit a final
                    # transcript, and a fixed wait turns that into a fake
                    # failure of whatever is being tested.
                    before_wait = len(inbound_audio)
                    for _ in range(30):
                        await pump_silence(1)
                        if len(inbound_audio) > before_wait + 2000 or closed["yes"]:
                            break
                    await pump_silence(3)
                    replied = len(inbound_audio) - before
                    note(replied > 0, f"agent replied to speech ({replied} bytes)")

            if not closed["yes"]:
                try:
                    await ws.send_str(json.dumps({"event": "stop", "streamSid": stream_sid}))
                except Exception:
                    pass                  # already torn down, which is fine
            await asyncio.sleep(1.5)
            read_task.cancel()

    note("media" in events, f"received media events back (saw: {', '.join(events) or 'none'})")
    if closed_out["yes"]:
        note(True, "server closed the stream — the call ended or was handed over")

    # The call must survive as evidence, or none of the downstream loop works.
    await asyncio.sleep(2)
    import db

    if db.enabled():
        import repo
        import tenancy

        # Agents can be moved between workspaces, so read the owner rather than
        # assuming the default one — otherwise this reports "not persisted" for
        # a call that saved perfectly well somewhere else.
        owner = db.one("select org_id::text from agents where id = %s", (AGENT,))
        if not owner:
            note(False, f"agent '{AGENT}' does not exist")
            return
        tenancy.set_context(owner["org_id"], None)

        row = next(
            (c for c in repo.load_calls(AGENT, limit=20) if c["provider_sid"] == call_sid),
            None,
        )
        note(row is not None, "call was persisted to Postgres")
        if row:
            note(row["source"] == "twilio", f"tagged as a phone call (source={row['source']})")
            note(len(row["turns"]) > 0, f"transcript captured ({len(row['turns'])} turns)")
            for t in row["turns"][:6]:
                print(f"      {t['speaker']:>6}: {t['text'][:90]}")

    print()
    if PROBLEMS:
        print("PROBLEMS:")
        for p in PROBLEMS:
            print("  -", p)
        sys.exit(1)
    print("Twilio media path OK.")


asyncio.run(main())
