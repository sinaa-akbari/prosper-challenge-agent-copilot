import { useEffect, useState } from "react";
import { api, awaitJob } from "../api";
import type { Call, Issue } from "../types";
import { Badge, Button, Empty, Icon, Spinner, cx, severityTone } from "./ui";

interface Props {
  agentId: string;
  onFix: (issue: Issue) => void;
}

const categoryLabel: Record<string, string> = {
  missing_capability: "missing capability",
  wrong_routing: "wrong routing",
  data_collection: "data collection",
  dead_end: "dead end",
  tone_or_wording: "wording",
  compliance_risk: "compliance risk",
};

export function IssuesPanel({ agentId, onFix }: Props) {
  const [issues, setIssues] = useState<Issue[]>([]);
  const [calls, setCalls] = useState<Call[]>([]);
  const [loading, setLoading] = useState(true);
  const [analyzing, setAnalyzing] = useState(false);
  const [open, setOpen] = useState<string | null>(null);
  const [showFixed, setShowFixed] = useState(false);
  const [error, setError] = useState("");

  async function load() {
    setLoading(true);
    try {
      const [i, c] = await Promise.all([api.getIssues(agentId), api.getCalls(agentId)]);
      setIssues(i.issues);
      setCalls(c.calls);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [agentId]);

  async function analyze() {
    setError("");
    setAnalyzing(true);
    try {
      const { job_id } = await api.analyzeCalls(agentId);
      const res = await awaitJob<{ issues: Issue[] }>(job_id);
      setIssues(res.issues);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setAnalyzing(false);
    }
  }

  async function setStatus(issue: Issue, status: string) {
    const res = await api.setIssueStatus(agentId, issue.id, status);
    setIssues(res.issues);
  }

  const visible = issues.filter((i) => (showFixed ? true : i.status === "open"));
  const fixedCount = issues.filter((i) => i.status !== "open").length;
  const callById = new Map(calls.map((c) => [c.id, c]));

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center gap-2 border-b border-ink-800 px-5 py-3">
        <div>
          <div className="text-[13px] font-semibold">Production issues</div>
          <div className="text-[11px] text-mist-400">
            {calls.length} recorded calls, read in bulk and clustered by root cause.
          </div>
        </div>
        <div className="ml-auto flex items-center gap-2">
          {fixedCount > 0 && (
            <Button size="xs" variant="ghost" onClick={() => setShowFixed(!showFixed)}>
              {showFixed ? "Hide" : "Show"} resolved ({fixedCount})
            </Button>
          )}
          <Button
            variant={issues.length ? "outline" : "primary"}
            size="sm"
            onClick={analyze}
            loading={analyzing}
          >
            {issues.length ? "Re-analyse calls" : "Analyse calls"}
          </Button>
        </div>
      </div>

      {error && (
        <div className="border-b border-rose-900/40 bg-rose-950/20 px-5 py-2 text-[11.5px] text-rose-300">
          {error}
        </div>
      )}

      <div className="flex-1 overflow-y-auto">
        {loading ? (
          <div className="flex justify-center py-16">
            <Spinner className="size-5 text-mist-400" />
          </div>
        ) : analyzing && !issues.length ? (
          <div className="flex flex-col items-center gap-3 py-20 text-center">
            <Spinner className="size-5 text-signal" />
            <div className="text-[12.5px] text-mist-300">Reading {calls.length} calls…</div>
            <div className="max-w-xs text-[11.5px] text-mist-400">
              Clustering by root cause, so nine calls that fail the same way arrive as one issue.
            </div>
          </div>
        ) : visible.length === 0 ? (
          <Empty
            icon={<Icon.Inbox className="size-6" />}
            title={issues.length ? "Nothing open" : "Calls haven't been analysed yet"}
            hint={
              issues.length
                ? "Every issue found has been resolved or dismissed."
                : `${calls.length} calls are waiting. Analysis reads all of them and reports what's recurring.`
            }
            action={
              !issues.length ? (
                <Button variant="primary" onClick={analyze} loading={analyzing}>
                  Analyse {calls.length} calls
                </Button>
              ) : undefined
            }
          />
        ) : (
          <div className="divide-y divide-ink-850">
            {visible.map((issue) => (
              <IssueRow
                key={issue.id}
                issue={issue}
                expanded={open === issue.id}
                callById={callById}
                onToggle={() => setOpen(open === issue.id ? null : issue.id)}
                onFix={() => onFix(issue)}
                onDismiss={() => setStatus(issue, "dismissed")}
                onReopen={() => setStatus(issue, "open")}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function IssueRow({
  issue,
  expanded,
  callById,
  onToggle,
  onFix,
  onDismiss,
  onReopen,
}: {
  issue: Issue;
  expanded: boolean;
  callById: Map<string, Call>;
  onToggle: () => void;
  onFix: () => void;
  onDismiss: () => void;
  onReopen: () => void;
}) {
  const resolved = issue.status !== "open";

  return (
    <div className={cx(expanded && "bg-ink-900/50", resolved && "opacity-55")}>
      <button onClick={onToggle} className="flex w-full items-start gap-3 px-5 py-3 text-left">
        <Icon.Chevron
          className={cx("mt-1 size-3 shrink-0 text-mist-400 transition-transform", expanded && "rotate-90")}
        />
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-1.5">
            <Badge tone={severityTone[issue.severity]}>{issue.severity}</Badge>
            <span className="text-[12.5px] font-medium text-mist-100">{issue.title}</span>
          </div>
          <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-[10.5px] text-mist-400">
            <span>{categoryLabel[issue.category] ?? issue.category}</span>
            <span>
              {issue.call_count} call{issue.call_count === 1 ? "" : "s"}
            </span>
            {issue.affected_nodes.length > 0 && (
              <span className="font-mono">{issue.affected_nodes.join(" · ")}</span>
            )}
            {issue.status === "fixed" && <Badge tone="green">fixed</Badge>}
            {issue.status === "dismissed" && <Badge tone="neutral">dismissed</Badge>}
          </div>
        </div>
      </button>

      {expanded && (
        <div className="space-y-3 px-5 pb-4 pl-[38px]">
          <p className="text-[12px] leading-relaxed text-mist-300">{issue.description}</p>

          {issue.evidence.length > 0 && (
            <div className="space-y-1.5">
              <div className="text-[10.5px] font-semibold uppercase tracking-wider text-mist-400">
                From the calls
              </div>
              {issue.evidence.map((ev, i) => {
                const call = callById.get(ev.call_id);
                return (
                  <div
                    key={i}
                    className="rounded-md border-l-2 border-ink-600 bg-ink-950/60 py-1.5 pl-3 pr-2"
                  >
                    <div className="text-[11.5px] italic leading-relaxed text-mist-300">
                      “{ev.quote}”
                    </div>
                    <div className="mt-0.5 flex items-center gap-2 text-[10px] text-mist-400">
                      <span className="font-mono">{ev.call_id}</span>
                      {call && (
                        <>
                          <span>·</span>
                          <span>{call.outcome}</span>
                          {call.flagged_by && (
                            <>
                              <span>·</span>
                              <span className="text-amber-400/80">{call.flagged_by}</span>
                            </>
                          )}
                        </>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          )}

          {issue.suggested_fix && (
            <div className="text-[11.5px] leading-relaxed text-mist-400">
              <span className="text-mist-300">Suggested direction: </span>
              {issue.suggested_fix}
            </div>
          )}

          <div className="flex items-center gap-2 pt-1">
            {resolved ? (
              <Button size="xs" onClick={onReopen}>
                Reopen
              </Button>
            ) : (
              <>
                <Button variant="primary" size="xs" onClick={onFix}>
                  <Icon.Sparkle className="size-3" /> Fix with Copilot
                </Button>
                <Button variant="ghost" size="xs" onClick={onDismiss}>
                  Dismiss
                </Button>
              </>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
