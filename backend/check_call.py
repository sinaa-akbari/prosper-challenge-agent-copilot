"""Dev-only: place a real WebRTC call against the running server with aiortc and
report whether the pipeline connects and the agent speaks.

    python check_call.py [agent_id]
"""

import asyncio
import fractions
import sys
import time

import aiohttp
import numpy as np
from aiortc import RTCPeerConnection, RTCSessionDescription
from aiortc.mediastreams import MediaStreamTrack
from av import AudioFrame

# `make cert` makes the server serve https; probe both so the harness works
# either way. verify_ssl=False because the dev cert is self-signed.
import os as _os
import ssl as _ssl
import urllib.request as _urlreq

def _detect_base() -> str:
    override = _os.environ.get("BASE")
    if override:
        return override
    ctx = _ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = _ssl.CERT_NONE
    for candidate in ("https://localhost:7860", "http://localhost:7860"):
        try:
            _urlreq.urlopen(f"{candidate}/api/health", timeout=4, context=ctx).read()
            return candidate
        except Exception:
            continue
    return "http://localhost:7860"

BASE = _detect_base()
AGENT = sys.argv[1] if len(sys.argv) > 1 else "northside-scheduling"
SAMPLE_RATE = 48000
SAMPLES = 960  # 20ms


class SilenceTrack(MediaStreamTrack):
    """A silent mic. Enough to establish the media path; we only need to listen."""

    kind = "audio"

    def __init__(self):
        super().__init__()
        self._pts = 0

    async def recv(self) -> AudioFrame:
        await asyncio.sleep(0.02)
        frame = AudioFrame.from_ndarray(
            np.zeros((1, SAMPLES), dtype=np.int16), format="s16", layout="mono"
        )
        frame.sample_rate = SAMPLE_RATE
        frame.pts = self._pts
        frame.time_base = fractions.Fraction(1, SAMPLE_RATE)
        self._pts += SAMPLES
        return frame


async def main() -> int:
    pc = RTCPeerConnection()
    pc.addTrack(SilenceTrack())
    pc.createDataChannel("pipecat")

    audio_frames = 0
    peak = 0.0          # loudest sample seen, 0..1
    loud_frames = 0     # frames carrying actual speech energy

    @pc.on("track")
    def on_track(track):
        async def drain():
            # Frame count alone proves nothing — a silent stream still ticks at
            # 50 fps. Measure energy so "I can't hear it" can be pinned on the
            # server or the browser, not guessed at.
            nonlocal audio_frames, peak, loud_frames
            while True:
                try:
                    frame = await track.recv()
                    audio_frames += 1
                    samples = frame.to_ndarray().astype(np.float32) / 32768.0
                    if samples.size:
                        frame_peak = float(np.abs(samples).max())
                        peak = max(peak, frame_peak)
                        if frame_peak > 0.02:
                            loud_frames += 1
                except Exception:
                    return

        asyncio.ensure_future(drain())

    await pc.setLocalDescription(await pc.createOffer())

    connector = aiohttp.TCPConnector(ssl=False)  # self-signed dev cert
    async with aiohttp.ClientSession(connector=connector) as session:
        print(f"POST {BASE}/api/agents/{AGENT}/call/offer …")
        try:
            async with session.post(
                f"{BASE}/api/agents/{AGENT}/call/offer",
                json={"sdp": pc.localDescription.sdp, "type": pc.localDescription.type},
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                body = await resp.json()
                if resp.status != 200:
                    print(f"  FAILED {resp.status}: {body}")
                    return 1
        except Exception as exc:
            print(f"  FAILED: {type(exc).__name__}: {exc}")
            return 1

        session_id = body["session_id"]
        print(f"  answer received, session={session_id}")
        await pc.setRemoteDescription(RTCSessionDescription(sdp=body["sdp"], type=body["type"]))

        # Poll the session record the UI polls.
        spoke = False
        deadline = time.time() + 45
        while time.time() < deadline:
            await asyncio.sleep(1.5)
            async with session.get(f"{BASE}/api/calls/live/{session_id}") as r:
                live = await r.json()
            turns = live.get("turns", [])
            print(
                f"  [{live['status']:10}] conn={pc.connectionState:12} "
                f"node={live.get('current_node','-'):16} turns={len(turns)} "
                f"frames={audio_frames} loud={loud_frames} peak={peak:.3f}"
            )
            if live.get("error"):
                print(f"  ERROR: {live['error']}")
                break
            if turns:
                spoke = True
                for t in turns:
                    print(f"    {t['speaker'].upper()}: {t['text']}")
                break

        if not spoke:
            await pc.close()
            print("\nFAILED — no agent speech within the timeout.")
            return 1

        # The transcript records TTSTextFrames, which are emitted *before* the
        # audio is synthesised (measured TTFB is over a second). Closing the
        # connection as soon as text appears measures silence and blames the
        # wrong component — so hold the line and let the speech play out.
        print("  holding the line to measure audio…")
        for _ in range(12):
            await asyncio.sleep(1)
            print(f"    frames={audio_frames} loud={loud_frames} peak={peak:.3f}")
            if loud_frames > 40:
                break
        await pc.close()

        print(
            f"\n  audio: {audio_frames} frames, {loud_frames} carrying speech energy, "
            f"peak amplitude {peak:.3f}"
        )
        if loud_frames > 40:
            print("OK — the agent spoke and real audio is on the wire.")
            print("     If you can't hear it, the fault is browser playback or output routing.")
            return 0
        print("PROBLEM — text was produced but the audio track stayed silent.")
        print("          That points at TTS or the pipeline, not the browser.")
        return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
