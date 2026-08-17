"""Provision and re-point the agent's phone number.

    python phone.py countries              # which countries this account can buy in
    python phone.py search GB              # available voice numbers
    python phone.py buy +447700900123      # provision it and wire the webhooks
    python phone.py list                   # what we own
    python phone.py point PNxxxx           # re-point after the tunnel URL changes
    python phone.py status                 # is everything joined up?

`point` is the one you run most: ngrok issues a new hostname every restart, and
a number still aimed at the old one fails with dead air rather than an error.
"""

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import telephony  # noqa: E402
from dotenv import load_dotenv  # noqa: E402

load_dotenv(Path(__file__).parent / ".env", override=True)

AGENT = os.environ.get("TWILIO_AGENT_ID", "northside-scheduling")


async def countries() -> None:
    body = await telephony._request("GET", "/AvailablePhoneNumbers.json?PageSize=100")
    codes = sorted(c["country_code"] for c in body.get("countries", []))
    print(f"{len(codes)} countries available to this account:\n")
    for i in range(0, len(codes), 18):
        print("  " + " ".join(codes[i : i + 18]))
    print(
        "\nA country missing from this list usually needs a Twilio Regulatory "
        "Bundle (local address + ID) approved before its numbers unlock."
    )


async def search(country: str) -> None:
    found = False
    for kind in ("Local", "Mobile", "National", "TollFree"):
        try:
            numbers = await telephony.available_numbers(country, kind)
        except Exception as exc:
            print(f"  {kind:9} unavailable ({str(exc)[:80]})")
            continue
        for n in numbers:
            found = True
            req = n["address_requirements"]
            flag = "" if req in ("none", "") else f"  [needs address: {req}]"
            print(f"  {kind:9} {n['phone_number']:18} {n['locality']}{flag}")
    if not found:
        print(f"  no voice numbers available in {country}")


async def buy(number: str) -> None:
    if not telephony.public_base_url():
        sys.exit("PUBLIC_BASE_URL is not set — buy the number after the tunnel is up.")
    result = await telephony.buy_number(number, AGENT)
    print(f"provisioned {result['phone_number']}  sid={result['sid']}")
    print(f"  voice webhook -> {result.get('voice_url')}")
    print(f"\nAdd to .env:\n  TWILIO_PHONE_NUMBER={result['phone_number']}")


async def show() -> None:
    numbers = await telephony.list_numbers()
    if not numbers:
        print("  (this account owns no numbers)")
    for n in numbers:
        print(f"  {n['phone_number']:18} {n['sid']}")
        print(f"     -> {n['voice_url'] or '(no voice webhook)'}")


async def point(sid: str) -> None:
    result = await telephony.point_number_at(sid, AGENT)
    print(f"{result['phone_number']} now points at:\n  {result.get('voice_url')}")


async def status() -> None:
    base = telephony.public_base_url()
    print(f"account          {telephony.account_sid() or '(not set)'}")
    print(f"agent            {AGENT}")
    print(f"public base url  {base or '(not set — Twilio cannot reach this server)'}")
    print(
        "webhook auth     "
        + (
            "X-Twilio-Signature"
            if os.environ.get("TWILIO_AUTH_TOKEN")
            else "URL secret only (set TWILIO_AUTH_TOKEN to upgrade)"
        )
    )
    if base:
        print(f"voice webhook    {base}/api/twilio/<secret>/voice?agent_id={AGENT}")
        wss = base.replace("https://", "wss://").replace("http://", "ws://")
        print(f"media stream     {wss}/api/twilio/<secret>/media")
    print("\nnumbers:")
    await show()


async def main() -> None:
    if not telephony.configured():
        sys.exit("Twilio is not configured — set TWILIO_ACCOUNT_SID and the API key pair.")
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    arg = sys.argv[2] if len(sys.argv) > 2 else ""
    actions = {
        "countries": lambda: countries(),
        "search": lambda: search(arg.upper() or "GB"),
        "buy": lambda: buy(arg),
        "list": lambda: show(),
        "point": lambda: point(arg),
        "status": lambda: status(),
    }
    if cmd not in actions:
        sys.exit(__doc__)
    if cmd in ("search", "buy", "point") and not arg:
        sys.exit(f"'{cmd}' needs an argument. See --help.")
    await actions[cmd]()


asyncio.run(main())
