import { useEffect, useMemo, useState } from "react";
import { api, awaitJob } from "../api";
import type { Call, PhoneStatus, ProposedTest } from "../types";
import { FlowPath } from "./FlowPath";
import { Transcript } from "./Transcript";
import { Badge, Button, Empty, Icon, Spinner, cx } from "./ui";

/**
 * Every conversation this agent has had.
 *
 * Three things belong together here and used to be scattered: who called, what
 * was said, and where the call went in the graph. Separately each is a hint;
 * together they're a diagnosis — "the 3pm caller from +34… bounced around
 * verify_identity four times and hung up" is a sentence you can act on.
 *
 * It's also where the loop closes. A call that went wrong becomes a regression
 * test in one click, with the persona reconstructed from the real caller rather
 * than imagined by a model.
 */

const SOURCE_TONE: Record<string, "teal" | "violet" | "neutral"> = {
  twilio: "teal",
  webrtc: "violet",
  seed: "neutral",
};

const SOURCE_LABEL: Record<string, string> = {
  twilio: "phone",
  webrtc: "browser",
  seed: "seeded",
};

const OUTCOME_TONE: Record<string, "green" | "amber" | "rose" | "teal" | "neutral"> = {
  completed: "green",
  transferred: "teal",
  hangup: "amber",
  no_audio: "amber",
  transfer_failed: "rose",
  error: "rose",
};

type Filter = "all" | "twilio" | "webrtc" | "seed";

function when(epoch?: number | null): string {
  if (!epoch) return "";
  return new Date(epoch * 1000).toLocaleString(undefined, {
    month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
  });
}

function endingOf(call: Call) {
  if (call.metadata?.transferred_to) return "transferred" as const;
  if (call.outcome === "transfer_failed" || call.outcome === "error") return "error" as const;
  if (call.outcome === "hangup") return "hangup" as const;
  if (call.outcome === "completed" || call.outcome === "transferred")
    return "completed" as const;
  return "unknown" as const;
}

export function HistoryPanel({
  agentId,
  onTestsChanged,
}: {
  agentId: string;
  onTestsChanged: () => void;
}) {
  // `agentId` is only the default for the agent filter. History deliberately
  // spans the workspace: the agent that answers the phone is the *active* one,
  // chosen independently of whatever is selected in the switcher, so scoping
  // this to the selection showed an empty list right after a real call.
  const [calls, setCalls] = useState<Call[]>([]);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState<Filter>("all");
  const [open, setOpen] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [drafts, setDrafts] = useState<Record<string, ProposedTest & { went_wrong?: string }>>({});
  const [saved, setSaved] = useState<Record<string, boolean>>({});
  const [error, setError] = useState("");
  const [phone, setPhone] = useState<PhoneStatus | null>(null);
  const [byAgent, setByAgent] = useState<string>("all");
  const [refreshing, setRefreshing] = useState(false);

  async function load(quiet = false) {
    if (quiet) setRefreshing(true);
    else setLoading(true);
    try {
      const r = await api.getWorkspaceCalls();
      setCalls(r.calls);
      setPhone(await api.phoneStatus().catch(() => null));
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }

  useEffect(() => {
    load();
    // A call finishes while you're looking at this tab, so coming back to the
    // window is exactly when the list is most likely to be stale.
    const onFocus = () => load(true);
    window.addEventListener("focus", onFocus);
    return () => window.removeEventListener("focus", onFocus);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const counts = useMemo(() => {
    const by: Record<string, number> = { all: calls.length };
    for (const c of calls) by[c.source] = (by[c.source] ?? 0) + 1;
    return by;
  }, [calls]);

  const agentsSeen = useMemo(() => {
    const seen = new Map<string, string>();
    for (const c of calls) if (c.agent_id) seen.set(c.agent_id, c.agent_name ?? c.agent_id);
    return [...seen.entries()];
  }, [calls]);

  const shown = calls
    .filter((c) => filter === "all" || c.source === filter)
    .filter((c) => byAgent === "all" || c.agent_id === byAgent);

  async function makeTest(call: Call, save: boolean) {
    const callId = call.id;
    setBusy(callId);
    setError("");
    try {
      // Replay against the agent that *took* the call, not the one selected in
      // the switcher. History spans the workspace, so those are routinely
      // different, and the endpoint rightly refuses a call the agent never had.
      const owner = call.agent_id || agentId;
      const { job_id } = await api.replayCall(owner, callId, save);
      const res = await awaitJob<{ case: any; saved: boolean }>(job_id);
      setDrafts((d) => ({ ...d, [callId]: res.case }));
      if (res.saved) {
        setSaved((s) => ({ ...s, [callId]: true }));
        onTestsChanged();
      }
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(null);
    }
  }

  if (loading) {
    return (
      <div className="flex items-center gap-2 p-6 text-[12px] text-mist-400">
        <Spinner /> Loading history…
      </div>
    );
  }

  if (!calls.length) {
    return (
      <div className="space-y-3 p-4">
        <Empty
          title="No conversations yet"
          hint="Every call to this workspace is recorded here — who rang, what was said, and the route it took through the graph."
        />
        {phone?.elsewhere && <Elsewhere number={phone.number} />}
      </div>
    );
  }

  return (
    <div className="space-y-3 p-4">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <h2 className="text-[13px] font-semibold text-mist-100">History</h2>
        <span className="text-[11.5px] text-mist-400">
          {shown.length === calls.length
            ? `${calls.length} conversation${calls.length === 1 ? "" : "s"}`
            : `${shown.length} of ${calls.length}`}
        </span>
        {agentsSeen.length > 1 && (
          <select
            value={byAgent}
            onChange={(e) => setByAgent(e.target.value)}
            className="h-6 cursor-pointer rounded-[3px] border border-ink-700 bg-ink-900 px-1.5 text-[11px] text-mist-300 outline-none"
          >
            <option value="all">every agent</option>
            {agentsSeen.map(([id, name]) => (
              <option key={id} value={id}>
                {name}
              </option>
            ))}
          </select>
        )}
        <button
          onClick={() => load(true)}
          className="text-[10.5px] text-mist-400 underline-offset-2 hover:text-mist-200 hover:underline"
        >
          {refreshing ? "refreshing…" : "refresh"}
        </button>
        <div className="ml-auto flex items-center gap-1">
          {(["all", "twilio", "webrtc", "seed"] as Filter[])
            .filter((f) => f === "all" || counts[f])
            .map((f) => (
              <button
                key={f}
                onClick={() => setFilter(f)}
                className={cx(
                  "rounded-[3px] px-2 py-1 font-mono text-[9.5px] uppercase tracking-[0.08em]",
                  filter === f
                    ? "bg-ink-700 text-mist-100"
                    : "text-mist-400 hover:bg-ink-850 hover:text-mist-200"
                )}
              >
                {f === "all" ? "all" : SOURCE_LABEL[f]} {counts[f] ?? 0}
              </button>
            ))}
        </div>
      </div>

      {phone?.elsewhere && <Elsewhere number={phone.number} />}

      {error && (
        <div className="rounded-md border border-rose-900/50 bg-rose-950/20 px-3 py-2 text-[11.5px] text-rose-300">
          {error}
        </div>
      )}

      <div className="divide-y divide-ink-800 overflow-hidden rounded-lg border border-ink-800">
        {shown.map((call) => {
          const isOpen = open === call.id;
          const draft = drafts[call.id];
          const ending = endingOf(call);
          return (
            <div key={call.id} className="bg-ink-900">
              <button
                data-call-row={call.id}
                data-call-source={call.source}
                onClick={() => setOpen(isOpen ? null : call.id)}
                className="flex w-full items-center gap-2 px-3 py-2.5 text-left hover:bg-ink-850"
              >
                <Icon.Chevron
                  className={cx(
                    "size-3 shrink-0 text-mist-400 transition-transform",
                    isOpen && "rotate-90"
                  )}
                />
                {/* Who called, first — it's the thing you scan for. */}
                <span className="w-[130px] shrink-0 font-mono text-[11px] text-mist-100">
                  {call.from_number || <span className="text-mist-400/60">no number</span>}
                </span>
                <Badge tone={SOURCE_TONE[call.source] ?? "neutral"}>
                  {SOURCE_LABEL[call.source] ?? call.source}
                </Badge>
                {agentsSeen.length > 1 && call.agent_name && (
                  <span className="max-w-[150px] shrink-0 truncate text-[10.5px] text-mist-400">
                    {call.agent_name}
                  </span>
                )}
                <Badge tone={OUTCOME_TONE[call.outcome] ?? "neutral"}>{call.outcome}</Badge>
                {call.flagged_by && <Badge tone="amber">flagged</Badge>}
                {saved[call.id] && (
                  <Badge tone="green">
                    <Icon.Check className="size-2.5" /> test added
                  </Badge>
                )}

                {/* A compressed flow, so the route is visible without opening. */}
                <span className="min-w-0 flex-1 truncate font-mono text-[9.5px] text-mist-400/70">
                  {(call.path ?? []).join(" → ")}
                </span>

                <span className="flex shrink-0 items-center gap-2 text-[10.5px] text-mist-400">
                  <span>{call.turns?.length ?? 0} turns</span>
                  <span className="tnum">{Math.round(call.duration_s)}s</span>
                  <span className="tnum">{when(call.created_at)}</span>
                </span>
              </button>

              {isOpen && (
                <div className="space-y-3 border-t border-ink-800 px-3 py-3">
                  {call.flagged_by && (
                    <div className="text-[11.5px] text-amber-300/90">{call.flagged_by}</div>
                  )}

                  <div className="grid gap-3 sm:grid-cols-[1fr_auto]">
                    <div className="min-w-0">
                      <SectionLabel>Flow</SectionLabel>
                      <FlowPath
                        path={call.path ?? []}
                        ending={ending}
                        transferredTo={call.metadata?.transferred_to}
                        collected={call.collected}
                      />
                    </div>
                    <dl className="shrink-0 space-y-1 text-[10.5px]">
                      <Meta label="from">{call.from_number || "—"}</Meta>
                      <Meta label="to">{call.to_number || "—"}</Meta>
                      {call.agent_version != null && (
                        <Meta label="agent">v{call.agent_version}</Meta>
                      )}
                      {call.provider_sid && (
                        <Meta label="sid">{call.provider_sid.slice(0, 14)}…</Meta>
                      )}
                    </dl>
                  </div>

                  <div>
                    <SectionLabel>Conversation</SectionLabel>
                    <Transcript turns={(call.turns ?? []) as any} />
                  </div>

                  <div className="flex items-center gap-2">
                    <Button
                      size="xs"
                      variant="primary"
                      loading={busy === call.id}
                      onClick={() => makeTest(call, false)}
                      title="Reconstruct this caller as a test case, for review before it's added"
                    >
                      Make a test from this call
                    </Button>
                    {draft && !saved[call.id] && (
                      <Button size="xs" onClick={() => makeTest(call, true)}>
                        Add to suite
                      </Button>
                    )}
                    {/* The suite it joins is the one belonging to the agent that
                        took the call, which isn't always the agent on screen. */}
                    {draft && call.agent_id && call.agent_id !== agentId && (
                      <span className="text-[10.5px] text-mist-400">
                        joins <span className="text-mist-300">{call.agent_name}</span>
                      </span>
                    )}
                  </div>

                  {draft && (
                    <div className="space-y-1.5 rounded-md border border-ink-700 bg-ink-850 px-3 py-2">
                      <div className="text-[11.5px] font-semibold text-mist-100">{draft.name}</div>
                      {draft.went_wrong && (
                        <div className="text-[11px] text-rose-300/90">{draft.went_wrong}</div>
                      )}
                      <div className="text-[11px] text-mist-400">{draft.persona?.description}</div>
                      {draft.persona?.facts && Object.keys(draft.persona.facts).length > 0 && (
                        <div className="flex flex-wrap gap-1">
                          {Object.entries(draft.persona.facts).map(([k, v]) => (
                            <span
                              key={k}
                              className="rounded-[3px] bg-ink-800 px-1.5 py-[2px] font-mono text-[9.5px] text-mist-300"
                            >
                              <span className="text-violet-300/80">{k}</span> {String(v)}
                            </span>
                          ))}
                        </div>
                      )}
                      <ul className="space-y-0.5 pt-0.5">
                        {(draft.assertions ?? []).map((a, i) => (
                          <li key={i} className="flex gap-1.5 text-[11px] text-mist-300">
                            <span className="text-mist-400">·</span>
                            {a}
                          </li>
                        ))}
                      </ul>
                    </div>
                  )}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

function Elsewhere({ number }: { number: string }) {
  return (
    <div className="rounded-md border border-amber-900/50 bg-amber-950/20 px-3 py-2 text-[11.5px] leading-relaxed text-amber-300">
      <span className="font-mono">{number}</span> is currently answering as an agent
      in another workspace, so calls to it are recorded there — not here. Activate
      one of your agents on the <strong>Phone</strong> tab to take the line.
    </div>
  );
}

function SectionLabel({ children }: { children: React.ReactNode }) {
  return (
    <div className="mb-1.5 font-mono text-[9.5px] uppercase tracking-[0.1em] text-mist-400">
      {children}
    </div>
  );
}

function Meta({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-baseline gap-2">
      <dt className="w-[42px] font-mono text-[9.5px] uppercase tracking-[0.08em] text-mist-400/70">
        {label}
      </dt>
      <dd className="font-mono text-mist-300">{children}</dd>
    </div>
  );
}
