#
# Twilio: answering a real phone call.
#
# Three things live here — proving an inbound request is really from Twilio,
# telling Twilio where to send the audio, and buying the number in the first
# place. The Pipecat side (transport + serializer) is in voice.py, because it is
# the same pipeline a browser call uses and belongs next to it.
#
# On authentication: Twilio signs webhooks with the account's *auth token*, and
# we only have an API key pair. Rather than ship an unauthenticated public
# endpoint, the webhook URL carries a secret path segment that Twilio echoes
# back on every request. That is weaker than a signature — it can't detect a
# tampered body and it leaks if the URL does — but it does stop a stranger who
# guesses the hostname from driving the agent and spending the account balance.
# Signature validation switches itself on the moment TWILIO_AUTH_TOKEN is set,
# and that is the version that should run in production.
#

import base64
import hashlib
import hmac
import os
from typing import Optional
from urllib.parse import urlencode

import aiohttp
from loguru import logger

import settings  # noqa: F401  — telephony is imported by CLI tools too

API_ROOT = "https://api.twilio.com/2010-04-01"


def account_sid() -> str:
    return os.environ.get("TWILIO_ACCOUNT_SID", "")


def _auth() -> aiohttp.BasicAuth:
    return aiohttp.BasicAuth(
        os.environ.get("TWILIO_API_KEY_SID", ""),
        os.environ.get("TWILIO_API_KEY_SECRET", ""),
    )


def configured() -> bool:
    return bool(account_sid() and os.environ.get("TWILIO_API_KEY_SID"))


def webhook_secret() -> str:
    return os.environ.get("TWILIO_WEBHOOK_SECRET", "")


def public_base_url() -> str:
    """Where Twilio can reach us. No trailing slash."""
    return os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")


# ------------------------------------------------- the shared number ---
# One number, and it rings whichever agent is currently live. That decision
# lives in activation.py — the number is a consequence of being active, not a
# separate setting to keep in sync.
def shared_number() -> str:
    return os.environ.get("TWILIO_PHONE_NUMBER", "")


def assignment() -> dict:
    import activation

    return activation.active()


# ------------------------------------------------------------------- auth ---
def validate_signature(url: str, form: dict, signature: str) -> bool:
    """Twilio's HMAC-SHA1 scheme: the full URL with sorted params appended."""
    token = os.environ.get("TWILIO_AUTH_TOKEN", "")
    if not token:
        return False
    payload = url + "".join(f"{k}{form[k]}" for k in sorted(form))
    digest = hmac.new(token.encode(), payload.encode("utf-8"), hashlib.sha1).digest()
    return hmac.compare_digest(base64.b64encode(digest).decode(), signature or "")


def authorise(url: str, form: dict, signature: str, path_secret: str) -> Optional[str]:
    """Return None when the request may proceed, or a reason to reject it.

    Prefers the real signature and falls back to the shared path secret, so
    supplying TWILIO_AUTH_TOKEN silently upgrades every deployment without a
    code change.
    """
    if os.environ.get("TWILIO_AUTH_TOKEN"):
        if validate_signature(url, form, signature):
            return None
        return "signature did not validate"

    secret = webhook_secret()
    if not secret:
        return "no TWILIO_AUTH_TOKEN and no TWILIO_WEBHOOK_SECRET — refusing to serve"
    if not hmac.compare_digest(path_secret or "", secret):
        return "bad webhook secret"
    logger.warning(
        "Twilio webhook accepted on the URL secret — set TWILIO_AUTH_TOKEN for "
        "real signature validation."
    )
    return None


# ------------------------------------------------------------------ twiml ---
def stream_twiml(
    agent_id: str,
    greeting: Optional[str] = None,
    from_number: str = "",
    to_number: str = "",
) -> str:
    """TwiML that hands the call's audio to our media-stream endpoint.

    `<Connect><Stream>` is bidirectional, which is what makes a conversation
    possible; `<Start><Stream>` only forks a copy of the caller's audio to you
    and is the classic reason a Twilio voice bot can hear but not speak.
    """
    base = public_base_url().replace("https://", "wss://").replace("http://", "ws://")
    url = f"{base}/api/twilio/{webhook_secret()}/media"
    parts = ['<?xml version="1.0" encoding="UTF-8"?>', "<Response>"]
    if greeting:
        parts.append(f"<Say>{greeting}</Say>")
    parts.append("<Connect>")
    parts.append(f'<Stream url="{url}">')
    parts.append(f'<Parameter name="agent_id" value="{agent_id}" />')
    # Twilio's media-stream `start` message carries the SIDs but not the
    # numbers — those only exist on the voice webhook. Without forwarding them
    # here, every call in the history reads "no number".
    if from_number:
        parts.append(f'<Parameter name="from" value="{from_number}" />')
    if to_number:
        parts.append(f'<Parameter name="to" value="{to_number}" />')
    parts.append("</Stream>")
    parts.append("</Connect>")
    parts.append("</Response>")
    return "".join(parts)


def reject_twiml(message: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f"<Response><Say>{message}</Say><Hangup/></Response>"
    )


# ------------------------------------------------------------------- rest ---
async def _request(method: str, path: str, data: Optional[dict] = None) -> dict:
    url = f"{API_ROOT}/Accounts/{account_sid()}{path}"
    async with aiohttp.ClientSession() as s:
        async with s.request(
            method, url, auth=_auth(),
            data=urlencode(data) if data else None,
            headers={"Content-Type": "application/x-www-form-urlencoded"} if data else None,
            timeout=aiohttp.ClientTimeout(total=30),
        ) as resp:
            body = await resp.json(content_type=None)
            if resp.status >= 400:
                raise RuntimeError(f"Twilio {resp.status}: {body.get('message', body)}")
            return body


async def list_numbers() -> list[dict]:
    body = await _request("GET", "/IncomingPhoneNumbers.json?PageSize=50")
    return [
        {
            "sid": n["sid"],
            "phone_number": n["phone_number"],
            "friendly_name": n.get("friendly_name", ""),
            "voice_url": n.get("voice_url", ""),
        }
        for n in body.get("incoming_phone_numbers", [])
    ]


async def available_numbers(country: str, kind: str = "Local", limit: int = 5) -> list[dict]:
    body = await _request(
        "GET", f"/AvailablePhoneNumbers/{country}/{kind}.json?VoiceEnabled=true&PageSize={limit}"
    )
    return [
        {
            "phone_number": n["phone_number"],
            "locality": n.get("locality", ""),
            "address_requirements": n.get("address_requirements", "none"),
        }
        for n in body.get("available_phone_numbers", [])
    ]


async def buy_number(phone_number: str, agent_id: str) -> dict:
    """Provision a number and point its voice webhook at this deployment."""
    base = public_base_url()
    if not base:
        raise RuntimeError("PUBLIC_BASE_URL is not set — Twilio would have nowhere to call.")
    return await _request(
        "POST",
        "/IncomingPhoneNumbers.json",
        {
            "PhoneNumber": phone_number,
            "VoiceUrl": f"{base}/api/twilio/{webhook_secret()}/voice?agent_id={agent_id}",
            "VoiceMethod": "POST",
            "StatusCallback": f"{base}/api/twilio/{webhook_secret()}/status",
            "StatusCallbackMethod": "POST",
            "FriendlyName": f"Agent Composer — {agent_id}",
        },
    )


async def point_number_at(number_sid: str, agent_id: str) -> dict:
    """Re-point an existing number, e.g. after the tunnel URL changes.

    Which it does on every ngrok restart, so this is the command you actually
    run day to day rather than buy_number.
    """
    base = public_base_url()
    if not base:
        raise RuntimeError("PUBLIC_BASE_URL is not set.")
    return await _request(
        "POST",
        f"/IncomingPhoneNumbers/{number_sid}.json",
        {
            "VoiceUrl": f"{base}/api/twilio/{webhook_secret()}/voice?agent_id={agent_id}",
            "VoiceMethod": "POST",
            "StatusCallback": f"{base}/api/twilio/{webhook_secret()}/status",
            "StatusCallbackMethod": "POST",
        },
    )


async def transfer(call_sid: str, to_number: str, say: str = "") -> None:
    """Hand a live call to a person.

    A call inside `<Connect><Stream>` can't be redirected by the media socket —
    the socket only carries audio. The control channel is the REST API: updating
    the call with new TwiML tears down our stream and runs the new verbs, which
    is how a bot-to-human handoff is actually done on Twilio.

    `callerId` is our own number rather than the caller's. Twilio will only
    present a number the account owns, and passing the original caller's number
    gets the whole dial rejected.
    """
    verbs = []
    if say:
        verbs.append(f"<Say>{say}</Say>")
    caller_id = os.environ.get("TWILIO_PHONE_NUMBER", "")
    attrs = f' callerId="{caller_id}"' if caller_id else ""
    # answerOnBridge keeps the caller hearing ringing rather than silence while
    # the human's phone rings, which is the difference between waiting and
    # assuming the line dropped.
    verbs.append(f'<Dial{attrs} answerOnBridge="true" timeout="25">{to_number}</Dial>')
    twiml = f'<?xml version="1.0" encoding="UTF-8"?><Response>{"".join(verbs)}</Response>'

    await _request("POST", f"/Calls/{call_sid}.json", {"Twiml": twiml})
    logger.info(f"transferred call {call_sid} to {to_number}")


async def hangup(call_sid: str) -> None:
    """End a call from our side. Works with the API key pair, unlike the
    serializer's built-in hang-up, which insists on the account auth token."""
    try:
        await _request("POST", f"/Calls/{call_sid}.json", {"Status": "completed"})
    except Exception as exc:
        logger.warning(f"hangup failed for {call_sid}: {exc}")
