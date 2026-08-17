"""Dev-only: prove that two accounts cannot see each other's work.

This is the check that matters most in the whole project. Everything else being
wrong costs a bad diff; this being wrong shows one clinic another clinic's
patients — names, dates of birth, and reasons for visit, sitting in call
transcripts.

Every table carried org_id from the first migration, but until accounts existed
every write used the same constant, which is a single-tenant app wearing a
multi-tenant schema. These assertions are what stop it quietly going back.
"""

import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import db  # noqa: E402
import tenancy  # noqa: E402
from agent_builder import store  # noqa: E402
from analysis import load_calls, load_issues, save_calls, save_issues  # noqa: E402
from sim import TestCase, load_cases, save_cases  # noqa: E402

PROBLEMS = []


def check(ok, msg):
    print(f"  {'ok  ' if ok else 'FAIL'}  {msg}")
    if not ok:
        PROBLEMS.append(msg)


def graph(name: str) -> dict:
    return {
        "name": name,
        "voice_id": "EXAVITQu4vr4xnSDxMaL",
        "model": "gpt-4o",
        "persona": "test",
        "initial_node": "greeting",
        "nodes": [
            {
                "name": "greeting",
                "task_messages": [{"role": "developer", "content": "hello"}],
                "edges": [],
            }
        ],
    }


def new_org(label: str) -> str:
    row = db.one("insert into orgs (name) values (%s) returning id", (label,))
    return str(row["id"])


def main() -> None:
    if not db.enabled():
        sys.exit("This check needs Postgres — set STORE_BACKEND=postgres.")

    alice_org, bob_org = new_org("check-alice"), new_org("check-bob")
    a_id = f"alice-{uuid.uuid4().hex[:6]}"
    b_id = f"bob-{uuid.uuid4().hex[:6]}"

    print("each account builds in its own workspace")
    with tenancy.as_org(alice_org):
        store.create(graph("Alice clinic"), agent_id=a_id)
        save_cases(a_id, [TestCase.from_dict({"name": "alice case", "persona": {}, "assertions": []})])
        save_calls(a_id, [{"id": "alice_call_1", "turns": [{"speaker": "caller", "text": "my dob is 1 Jan 1970"}]}])
        # Issues too, so "invisible" can't pass merely by being empty.
        save_issues(a_id, [{"id": "alice_issue_1", "title": "Alice only", "severity": "high"}])
    with tenancy.as_org(bob_org):
        store.create(graph("Bob clinic"), agent_id=b_id)

    with tenancy.as_org(alice_org):
        check(len(load_cases(a_id)) == 1, "its own test case is readable")
        check(len(load_calls(a_id)) == 1, "its own call is readable")
        check(len(load_issues(a_id)) == 1, "its own issue is readable")
        mine = [a["id"] for a in store.list_agents()]
        check(a_id in mine, "an account sees its own agent")
        check(b_id not in mine, "and not the other account's")

    print("\nknowing the id is not enough")
    with tenancy.as_org(bob_org):
        check(not store.exists(a_id), "exists() is false for someone else's agent")
        try:
            store.get(a_id)
            check(False, "get() refuses someone else's agent")
        except KeyError:
            check(True, "get() refuses someone else's agent")
        check(store.versions(a_id) == [], "its version history is invisible")
        try:
            store.version_config(a_id, 1)
            check(False, "an individual version is unreadable")
        except KeyError:
            check(True, "an individual version is unreadable")

        # The children are reached through the agent, so the agent check is the
        # gate — but a direct read must not leak either.
        check(load_cases(a_id) == [], "its test cases are invisible")
        check(load_calls(a_id) == [], "its call transcripts are invisible")
        check(load_issues(a_id) == [], "its issues are invisible")

    print("\nand cannot be written to")
    with tenancy.as_org(bob_org):
        store.delete(a_id)
    with tenancy.as_org(alice_org):
        check(store.exists(a_id), "another account's delete does nothing")

    print("\nno tenant, no data")
    tenancy.set_context(None, None)
    try:
        store.list_agents()
        check(False, "reading without a tenant raises rather than defaulting")
    except tenancy.NoTenant:
        check(True, "reading without a tenant raises rather than defaulting")

    # Clean up.
    with tenancy.as_org(alice_org):
        store.delete(a_id)
    with tenancy.as_org(bob_org):
        store.delete(b_id)
    db.execute("delete from orgs where id = any(%s)", ([alice_org, bob_org],))

    print()
    if PROBLEMS:
        print("PROBLEMS:")
        for p in PROBLEMS:
            print("  -", p)
        sys.exit(1)
    print("Tenant isolation OK.")


main()
