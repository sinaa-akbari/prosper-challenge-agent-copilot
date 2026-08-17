import { Icon, cx } from "./ui";

/**
 * The route a call actually took through the graph.
 *
 * A breadcrumb of node names reads as a list; a call is a sequence with an
 * ending, and the ending is usually the interesting part. So the last step is
 * drawn differently depending on how it finished — handed to a person, hung up
 * on, or run out of road — because "greeting → transfer_to_staff" and
 * "greeting → transfer_to_staff, caller gave up" are different calls.
 *
 * Repeated nodes are collapsed with a count. A caller bounced around
 * verify_identity four times is one fact about one node, not four steps.
 */

type Ending = "transferred" | "completed" | "hangup" | "error" | "unknown";

const ENDING: Record<Ending, { label: string; tone: string; dot: string }> = {
  transferred: { label: "handed to a person", tone: "text-signal-2", dot: "bg-signal" },
  completed: { label: "reached the end", tone: "text-emerald-300", dot: "bg-emerald-400" },
  hangup: { label: "caller hung up", tone: "text-amber-300", dot: "bg-amber-400" },
  error: { label: "failed", tone: "text-rose-300", dot: "bg-rose-400" },
  unknown: { label: "", tone: "text-mist-400", dot: "bg-mist-400" },
};

function collapse(path: string[]): { name: string; times: number }[] {
  const out: { name: string; times: number }[] = [];
  for (const name of path) {
    const last = out[out.length - 1];
    if (last && last.name === name) last.times += 1;
    else out.push({ name, times: 1 });
  }
  return out;
}

export function FlowPath({
  path,
  ending = "unknown",
  transferredTo,
  collected,
}: {
  path: string[];
  ending?: Ending;
  transferredTo?: string;
  collected?: Record<string, unknown>;
}) {
  if (!path?.length) {
    return (
      <div className="text-[11px] text-mist-400/70">
        No flow recorded — the call never reached the agent.
      </div>
    );
  }

  const steps = collapse(path);
  const end = ENDING[ending] ?? ENDING.unknown;
  const fields = Object.entries(collected ?? {});

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-x-1 gap-y-1.5">
        {steps.map((step, i) => {
          const last = i === steps.length - 1;
          return (
            <span key={`${step.name}-${i}`} className="flex items-center gap-1">
              <span
                className={cx(
                  "rounded-[3px] border px-1.5 py-[3px] font-mono text-[10px]",
                  last
                    ? cx("border-ink-600 bg-ink-800", end.tone)
                    : "border-ink-700 bg-ink-850 text-mist-300"
                )}
              >
                {step.name}
                {step.times > 1 && (
                  <span className="ml-1 text-mist-400" title={`visited ${step.times} times`}>
                    ×{step.times}
                  </span>
                )}
              </span>
              {!last && <Icon.Chevron className="size-2.5 shrink-0 text-mist-400/50" />}
            </span>
          );
        })}
      </div>

      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[10.5px]">
        {end.label && (
          <span className="flex items-center gap-1.5 text-mist-400">
            <span className={cx("size-1.5 rounded-full", end.dot)} />
            {end.label}
            {transferredTo && (
              <span className="font-mono text-mist-300">→ {transferredTo}</span>
            )}
          </span>
        )}
        <span className="text-mist-400/70">
          {steps.length} step{steps.length === 1 ? "" : "s"}
        </span>
      </div>

      {/* What the agent actually came away with. On a scheduling call this is
          the whole point of the conversation, and it's invisible in a transcript
          once the caller has spelled their surname twice. */}
      {fields.length > 0 && (
        <div className="flex flex-wrap gap-1 pt-0.5">
          {fields.map(([k, v]) => (
            <span
              key={k}
              className="rounded-[3px] bg-ink-800 px-1.5 py-[2px] font-mono text-[9.5px] text-mist-300"
            >
              <span className="text-violet-300/80">{k}</span> {String(v).slice(0, 40)}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}
