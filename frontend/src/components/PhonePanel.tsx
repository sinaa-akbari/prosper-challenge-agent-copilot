import { useEffect, useState } from "react";
import { api } from "../api";
import type { AgentSummary, PhoneStatus } from "../types";
import { CalendarPanel } from "./CalendarPanel";
import { Badge, Button, Icon, Spinner, cx } from "./ui";

/**
 * The shared phone number.
 *
 * One number, many accounts. A number is a per-country regulated purchase, so
 * handing every signup their own is neither instant nor free — until it is, the
 * deployment has a single line and whoever claims it answers it. Modelling that
 * as a visible claim rather than a hidden setting matters: two accounts can't
 * share one phone line, and the honest version of that is showing who has it.
 */
export function PhonePanel({
  agentId,
  agents,
  transferTo,
}: {
  agentId: string;
  agents: AgentSummary[];
  transferTo?: string | null;
}) {
  const [state, setState] = useState<PhoneStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const load = () => api.phoneStatus().then(setState).catch(() => setState(null));
  useEffect(() => {
    load();
  }, []);

  async function act(fn: () => Promise<unknown>) {
    setBusy(true);
    setError("");
    try {
      await fn();
      await load();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  if (!state) {
    return (
      <div className="flex items-center gap-2 p-6 text-[12px] text-mist-400">
        <Spinner /> Loading…
      </div>
    );
  }

  const live = state.agent_id === agentId;
  const takenByOther = !!state.agent_id && !state.mine;
  const owner = agents.find((a) => a.id === state.agent_id);

  return (
    <div className="space-y-4 p-4">
      <div>
        <h2 className="text-[13px] font-semibold text-mist-100">Phone number</h2>
        <p className="mt-1 text-[11.5px] leading-relaxed text-mist-400">
          One number is shared across every account on this deployment. Point it
          at an agent and calls to it are answered by that agent.
        </p>
      </div>

      {!state.configured ? (
        <div className="rounded-md border border-amber-900/50 bg-amber-950/20 px-3 py-2 text-[11.5px] text-amber-300">
          No phone number is configured on this deployment yet.
        </div>
      ) : (
        <div className="space-y-3 rounded-lg border border-ink-800 bg-ink-900 p-4">
          <div className="flex items-center gap-2.5">
            <Icon.Phone className="size-4 text-signal-2" />
            <span className="font-mono text-[15px] text-mist-100">{state.number}</span>
            {live && (
              <Badge tone="green">
                <Icon.Check className="size-2.5" /> answering as this agent
              </Badge>
            )}
            {takenByOther && <Badge tone="amber">in use by another account</Badge>}
          </div>

          {state.agent_id && !live && state.mine && (
            <div className="text-[11.5px] text-mist-400">
              Currently answering as{" "}
              <span className="font-mono text-mist-300">
                {owner?.name ?? state.agent_id}
              </span>
              .
            </div>
          )}
          {takenByOther && (
            <div className="text-[11.5px] leading-relaxed text-mist-400">
              Another account is using it{state.claimed_by ? ` (${state.claimed_by})` : ""}.
              Taking it over will stop their calls being answered.
            </div>
          )}

          <div className="flex items-center gap-2">
            <Button
              variant="primary"
              size="xs"
              loading={busy}
              disabled={live}
              onClick={() => act(() => api.claimPhone(agentId))}
            >
              {live ? "This agent answers it" : takenByOther ? "Take it over" : "Use it for this agent"}
            </Button>
            {state.mine && state.agent_id && (
              <Button size="xs" onClick={() => act(() => api.releasePhone())}>
                Release
              </Button>
            )}
          </div>
        </div>
      )}

      {/* The handoff. Worth stating explicitly, because "transfer to a human"
          is the one node whose behaviour lives outside the graph. */}
      <div className="space-y-2 rounded-lg border border-ink-800 bg-ink-900 p-4">
        <div className="text-[12px] font-semibold text-mist-100">When the agent hands over</div>
        {transferTo ? (
          <div className="flex items-center gap-2 text-[11.5px] text-mist-300">
            <Icon.Check className="size-3 text-emerald-400" />
            Calls are forwarded to{" "}
            <span className="font-mono text-mist-100">{transferTo}</span>
          </div>
        ) : (
          <div className="text-[11.5px] leading-relaxed text-mist-400">
            No node in this agent forwards to a person. Set{" "}
            <span className="font-mono text-mist-300">transfer_to</span> on the node
            that hands the caller over — until then it says it's transferring and
            then hangs up.
          </div>
        )}
        <p className="text-[11px] leading-relaxed text-mist-400/80">
          Forwarding only happens on real phone calls. Browser test calls and the
          simulated suite reach the node but have nowhere to forward to.
        </p>
      </div>

      {error && (
        <div className="rounded-md border border-rose-900/50 bg-rose-950/20 px-3 py-2 text-[11.5px] text-rose-300">
          {error}
        </div>
      )}

      <div className="border-t border-ink-800 pt-1">
        <CalendarPanel />
      </div>
    </div>
  );
}
