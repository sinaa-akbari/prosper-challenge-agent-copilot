#
# Loading configuration, once, with one rule about who wins.
#
# Several modules need the environment before anything else runs, so each of
# them used to call load_dotenv(override=True) on import. That produced a bug
# worth writing down: `AUTH_DISABLED=0 python server.py` looked like it worked —
# server.py captured the shell value and put it back after loading .env — and
# then db.py imported, loaded .env *again*, and quietly restored the file's
# value. The switch appeared to do nothing, with no error anywhere.
#
# The rule:
#
#   * Secrets and endpoints come from .env and override a stale shell, because a
#     rotated key must not lose to an old `export`.
#   * Operational switches — the ones you flip for a single command — come from
#     the shell when it sets them, because that is the entire point of typing
#     them on the command line.
#
# Loading is idempotent, so importing this from anywhere is safe.
#

import os
from pathlib import Path

from dotenv import load_dotenv

ENV_PATH = Path(__file__).resolve().parent / ".env"

# Things you legitimately override per-command. Everything else lives in .env.
OPERATIONAL = (
    "STORE_BACKEND",
    "AUTH_DISABLED",
    "AUTH_PASSWORD",
    "AUTH_ALLOWED_PHONES",
    "HOST",
    "PORT",
    "PUBLIC_BASE_URL",
    "TWILIO_AGENT_ID",
    "NO_CALL_RECORDER",
)

# Captured at first import, which is before any load_dotenv in this process has
# had a chance to overwrite them.
_SHELL = {k: os.environ[k] for k in OPERATIONAL if k in os.environ}
_loaded = False


def load() -> None:
    global _loaded
    load_dotenv(ENV_PATH, override=True)
    # Re-assert every time: a later load_dotenv elsewhere would otherwise undo
    # this, which is exactly the bug that prompted the module.
    os.environ.update(_SHELL)
    _loaded = True


load()
