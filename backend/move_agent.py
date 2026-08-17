"""Move an agent and everything attached to it into another workspace.

Needed because multi-tenancy arrived after the demo agent did: it stayed in the
default org while every new signup got an empty workspace, so the person who
owns the deployment could call their own number and then not find the call.

Everything moves together — versions, tests, runs, calls, issues, decisions —
because an agent whose history stayed behind is worse than one that never moved.

    python move_agent.py northside-scheduling +34722482770
    python move_agent.py northside-scheduling <org-uuid>
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import db  # noqa: E402

# Children keyed directly by agent_id. agent_versions has no org_id of its own —
# it inherits from the agent — so it isn't in this list.
CHILD_TABLES = ("test_cases", "test_runs", "calls", "issues", "decisions")


def resolve_org(target: str) -> str:
    if target.startswith("+"):
        row = db.one("select org_id::text from users where phone = %s", (target,))
        if not row:
            sys.exit(f"No account with phone {target}.")
        return row["org_id"]
    return target


def main() -> None:
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    agent_id, target = sys.argv[1], sys.argv[2]
    org_id = resolve_org(target)

    agent = db.one("select org_id::text, config->>'name' as name from agents where id = %s", (agent_id,))
    if not agent:
        sys.exit(f"No agent '{agent_id}'.")
    if agent["org_id"] == org_id:
        print(f"'{agent_id}' is already in {org_id}. Nothing to do.")
        return

    print(f"moving '{agent['name']}' ({agent_id})")
    print(f"  from org {agent['org_id']}")
    print(f"  to   org {org_id}\n")

    with db.conn() as c:
        with c.cursor() as cur:
            cur.execute("select 1 from orgs where id = %s", (org_id,))
            if cur.fetchone() is None:
                sys.exit(f"No org {org_id}.")
            for table in CHILD_TABLES:
                cur.execute(
                    f"update {table} set org_id = %s where agent_id = %s", (org_id, agent_id)
                )
                print(f"  {table:12} {cur.rowcount} row(s)")
            cur.execute("update agents set org_id = %s where id = %s", (org_id, agent_id))
            print(f"  {'agents':12} {cur.rowcount} row(s)")

            # The activation record names the owning org; leaving it stale would
            # send the next inbound call's transcript to the previous workspace.
            cur.execute("select value from app_settings where key = 'active.agent'")
            row = cur.fetchone()
            if row and (row[0] or {}).get("agent_id") == agent_id:
                cur.execute(
                    """update app_settings
                          set value = jsonb_set(value, '{org_id}', to_jsonb(%s::text)),
                              updated_at = now()
                        where key = 'active.agent'""",
                    (org_id,),
                )
                print("  activation   re-pointed at the new owner")
        c.commit()

    print("\ndone.")


if __name__ == "__main__":
    main()
