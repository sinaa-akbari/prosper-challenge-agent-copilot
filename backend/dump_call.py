"""Print everything known about a call — trace, transcript, audio, log lines.

    python dump_call.py              the most recent call
    python dump_call.py call_abc123  a specific one
    python dump_call.py --list       what's available

Reads the saved traces in data/calls_debug/ and the matching slice of
logs/server.log, so it works after the server has been restarted.
"""

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEBUG_DIR = HERE / "data" / "calls_debug"
LOG = HERE / "logs" / "server.log"


def traces() -> list[Path]:
    if not DEBUG_DIR.exists():
        return []
    return sorted(DEBUG_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    listing = "--list" in sys.argv

    files = traces()
    if not files:
        print("No saved call traces yet. Place a test call, then run this again.")
        print(f"(looking in {DEBUG_DIR})")
        return 1

    if listing:
        print(f"{len(files)} saved call(s):\n")
        for f in files[:20]:
            d = json.loads(f.read_text(encoding="utf-8"))
            a = d.get("audio", {})
            print(
                f"  {d['session_id']}  {d.get('status','?'):9} "
                f"turns={len(d.get('turns', [])):<3} "
                f"audio={a.get('frames', 0)} chunks peak={a.get('peak', 0)}"
                f"{'  *** SILENT ***' if a.get('silent') else ''}"
            )
        return 0

    path = next((f for f in files if f.stem == args[0]), None) if args else files[0]
    if path is None:
        print(f"No trace for '{args[0]}'. Try --list.")
        return 1

    d = json.loads(path.read_text(encoding="utf-8"))
    sid = d["session_id"]
    audio = d.get("audio", {})

    print("=" * 78)
    print(f"CALL {sid}   agent={d.get('agent_id')}   status={d.get('status')}")
    print(f"path: {' -> '.join(d.get('path', [])) or '(none)'}")
    print(
        f"audio: {audio.get('frames', 0)} chunks, {audio.get('kb', 0)} KB, "
        f"peak {audio.get('peak', 0)}"
        + ("   *** SILENT — TTS produced no sound ***" if audio.get("silent") else "")
    )
    if d.get("warning"):
        print(f"WARNING: {d['warning']}")
    if d.get("error"):
        print(f"ERROR:   {d['error']}")

    print("\n--- TIMELINE " + "-" * 64)
    for e in d.get("events", []):
        mark = {"error": "!!", "warning": " !"}.get(e.get("level", "info"), "  ")
        print(f"{mark} {e['ms']:>6}ms  {e['kind']:<12} {e['detail']}")

    print("\n--- TRANSCRIPT " + "-" * 62)
    for t in d.get("turns", []):
        print(f"  {t['speaker'].upper():>6}: {t['text']}")
    if d.get("collected"):
        print(f"\n  collected: {d['collected']}")

    if LOG.exists():
        lines = [
            ln
            for ln in LOG.read_text(encoding="utf-8", errors="replace").splitlines()
            if sid in ln or "ERROR" in ln or "elevenlabs" in ln.lower()
        ]
        if lines:
            print("\n--- SERVER LOG (this call, plus errors) " + "-" * 37)
            for ln in lines[-60:]:
                print("  " + ln)

    print("=" * 78)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
