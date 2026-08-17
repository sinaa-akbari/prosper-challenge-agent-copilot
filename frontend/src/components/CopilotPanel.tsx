import { useEffect, useRef, useState } from "react";
import { api, awaitJob } from "../api";
import type { AgentConfig, ChatMessage, Issue, Proposal, TestRun } from "../types";
import { Findings, VerificationStrip } from "./Findings";
import { Badge, Button, Empty, Icon, Spinner, Textarea, cx } from "./ui";

interface Props {
  agentId: string;
  config: AgentConfig;
  hasFailingTests: boolean;
  /** Set by the Issues tab: "fix this" hands the Copilot the issue plus its evidence. */
  pendingIssue: Issue | null;
  onIssueConsumed: () => void;
  /** Set by the Tests tab: diagnose the whole failing run. */
  pendingDiagnose: boolean;
  onDiagnoseConsumed: () => void;
  onPreview: (p: Proposal | null) => void;
  onApplied: () => void;
}

const DIAGNOSE_PROMPT =
  "Diagnose the failing tests. For each one work out the root cause from the persona, the transcript and the exits that were available at each node — then fix the causes. If two failures share a cause, make one change. If a test itself is wrong, say so and leave the graph alone.";

const STARTERS = [
  "Callers keep asking about insurance and cost. Add a way to handle that anywhere in the call.",
  "Add an emergency path: if a caller mentions chest pain or trouble breathing, stop and tell them to hang up and dial 911.",
  "Split cancel and reschedule into their own flows instead of funnelling everything into booking.",
  "Offer real availability instead of two hardcoded slots, and handle callers who can't make either.",
];

export function CopilotPanel({
  agentId,
  config,
  hasFailingTests,
  pendingIssue,
  onIssueConsumed,
  pendingDiagnose,
  onDiagnoseConsumed,
  onPreview,
  onApplied,
}: Props) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState("");
  const [verify, setVerify] = useState<
    Record<number, { running: boolean; done: number; total: number; run?: TestRun }>
  >({});
  const [applying, setApplying] = useState<number | null>(null);
  const scroller = useRef<HTMLDivElement>(null);

  // Reset the thread when switching agents — history from another graph is noise.
  useEffect(() => {
    setMessages([]);
    setVerify({});
    onPreview(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [agentId]);

  useEffect(() => {
    scroller.current?.scrollTo({ top: scroller.current.scrollHeight, behavior: "smooth" });
  }, [messages, busy, verify]);

  // An issue arriving from the Issues tab sends itself.
  useEffect(() => {
    if (pendingIssue && !busy) {
      send(`Fix this production issue: ${pendingIssue.title}`, pendingIssue);
      onIssueConsumed();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pendingIssue]);

  // Same for "Diagnose failures" from the Tests tab.
  useEffect(() => {
    if (pendingDiagnose && !busy) {
      send(DIAGNOSE_PROMPT, null, true);
      onDiagnoseConsumed();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pendingDiagnose]);

  async function send(text: string, issue?: Issue | null, includeFailures = false) {
    const trimmed = text.trim();
    if (!trimmed || busy) return;

    const history = messages
      .filter((m) => !m.proposal || m.applied)
      .slice(-6)
      .map((m) => ({ role: m.role, content: m.content }));

    setMessages((m) => [...m, { role: "user", content: trimmed, issueId: issue?.id }]);
    setInput("");
    setBusy(true);
    setStatus(
      issue
        ? "Reading the flagged calls…"
        : includeFailures
          ? "Reading the failing transcripts and the graph…"
          : "Thinking…"
    );
    onPreview(null);

    try {
      const { job_id } = await api.askCopilot(agentId, {
        message: trimmed,
        history,
        issue_id: issue?.id,
        include_failures: includeFailures,
      });
      // The loop narrates itself — diagnosing, running the affected calls,
      // re-diagnosing. Watching it iterate is the difference between "it
      // thought for a while" and "it tried something, checked, and adjusted".
      const proposal = await awaitJob<Proposal>(job_id, (job) => {
        if (job.status_text) {
          const { done, total } = job.progress ?? { done: 0, total: 0 };
          setStatus(job.status_text + (total ? `  ${done}/${total}` : ""));
        }
      });
      setMessages((m) => [
        ...m,
        {
          role: "assistant",
          content: proposal.reply,
          proposal,
          issueId: issue?.id,
        },
      ]);
      if (proposal.ops.length) onPreview(proposal);
    } catch (e: any) {
      setMessages((m) => [
        ...m,
        { role: "assistant", content: `That didn't work: ${e.message}` },
      ]);
    } finally {
      setBusy(false);
      setStatus("");
    }
  }

  async function runVerify(index: number, proposal: Proposal) {
    if (!proposal.config) return;
    setVerify((v) => ({ ...v, [index]: { running: true, done: 0, total: 0 } }));
    try {
      // Run the suite the engineer would end up with, not the one they have —
      // otherwise a proposal that retires a broken test always looks like a
      // regression, and the gate blocks the fix it asked for.
      const { job_id } = await api.runTests(agentId, {
        config: proposal.config,
        retire_ids: (proposal.retire_tests ?? []).map((t) => t.case_id),
        extra_cases: proposal.tests ?? [],
      });
      const run = await awaitJob<TestRun>(job_id, (job) =>
        setVerify((v) => ({
          ...v,
          [index]: { running: true, done: job.progress.done, total: job.progress.total },
        }))
      );
      setVerify((v) => ({ ...v, [index]: { running: false, done: run.total, total: run.total, run } }));
    } catch (e: any) {
      setVerify((v) => ({ ...v, [index]: { running: false, done: 0, total: 0 } }));
      setMessages((m) => [...m, { role: "assistant", content: `Test run failed: ${e.message}` }]);
    }
  }

  async function apply(index: number, msg: ChatMessage) {
    const p = msg.proposal!;
    setApplying(index);
    try {
      const res = await api.applyPatch(agentId, {
        ops: p.ops,
        label: msg.issueId ? "Copilot: production fix" : "Copilot edit",
        tests: p.tests ?? [],
        retire_tests: p.retire_tests ?? [],
        issue_id: msg.issueId,
        // Kept as the reason this was accepted, so a later session doesn't
        // undo it without knowing why it was done.
        reply: p.reply,
      });
      setMessages((m) => m.map((x, i) => (i === index ? { ...x, applied: true } : x)));
      onPreview(null);
      onApplied();
      const added = res.tests_added?.length ?? 0;
      const retired = res.tests_retired?.length ?? 0;
      const notes = [
        added && `added ${added} regression test${added === 1 ? "" : "s"}`,
        retired && `retired ${retired} broken test${retired === 1 ? "" : "s"}`,
      ].filter(Boolean);
      if (notes.length) {
        setMessages((m) => [
          ...m,
          { role: "assistant", content: `Applied — ${notes.join(" and ")}.` },
        ]);
      }
    } catch (e: any) {
      setMessages((m) => [...m, { role: "assistant", content: `Couldn't apply: ${e.message}` }]);
    } finally {
      setApplying(null);
    }
  }

  function discard(index: number) {
    setMessages((m) => m.filter((_, i) => i !== index));
    onPreview(null);
  }

  return (
    /* Slightly darker than the editor so it reads as a different surface —
       a console you talk to, not another panel of the same document. */
    <div className="flex h-full flex-col bg-ink-950">
      {/* header */}
      <div className="flex items-center gap-2 border-b border-ink-800 bg-ink-900 px-4 py-3">
        <Icon.Sparkle className="size-3.5 text-signal" />
        <div className="text-[12.5px] font-semibold tracking-[-0.01em]">Copilot</div>
        <div className="ml-auto flex items-center gap-1.5">
          {hasFailingTests && (
            <Button
              size="xs"
              variant="subtle"
              onClick={() => send(DIAGNOSE_PROMPT, null, true)}
              disabled={busy}
            >
              Diagnose failures
            </Button>
          )}
          {messages.length > 0 && (
            <Button
              size="xs"
              variant="ghost"
              onClick={() => {
                setMessages([]);
                setVerify({});
                onPreview(null);
              }}
            >
              Clear
            </Button>
          )}
        </div>
      </div>

      {/* thread */}
      <div ref={scroller} className="flex-1 space-y-3 overflow-y-auto px-4 py-4">
        {messages.length === 0 && !busy && (
          <div className="pt-4">
            <Empty
              icon={<Icon.Sparkle className="size-6" />}
              title="Describe the change you want"
              hint="The Copilot edits the graph as a reviewable diff. Nothing is applied until you accept it."
            />
            <div className="mt-1 space-y-1.5">
              {STARTERS.map((s) => (
                <button
                  key={s}
                  onClick={() => send(s)}
                  className="w-full rounded-md border border-ink-800 bg-ink-850 px-3 py-2 text-left text-[11.5px] leading-relaxed text-mist-300 transition-colors hover:border-ink-600 hover:text-mist-100"
                >
                  {s}
                </button>
              ))}
            </div>
          </div>
        )}

        {messages.map((m, i) =>
          m.role === "user" ? (
            <div key={i} className="fade-up flex justify-end">
              <div className="max-w-[85%] rounded-lg rounded-br-sm bg-ink-700 px-3 py-2 text-[12.5px] leading-relaxed">
                {m.content}
              </div>
            </div>
          ) : (
            <div key={i} className="fade-up space-y-2">
              <div className="whitespace-pre-wrap text-[12.5px] leading-relaxed text-mist-100">
                {m.content}
              </div>

              {m.proposal &&
                (m.proposal.ops.length > 0 ||
                  (m.proposal.retire_tests?.length ?? 0) > 0 ||
                  (m.proposal.findings?.length ?? 0) > 0) && (
                <ProposalCard
                  proposal={m.proposal}
                  applied={!!m.applied}
                  verify={verify[i]}
                  applying={applying === i}
                  onVerify={() => runVerify(i, m.proposal!)}
                  onApply={() => apply(i, m)}
                  onDiscard={() => discard(i)}
                  onPreview={() => onPreview(m.proposal!)}
                />
              )}

              {m.proposal?.error && (
                <div className="rounded-md border border-rose-900/50 bg-rose-950/20 px-3 py-2 text-[11.5px] text-rose-300">
                  {m.proposal.error}
                </div>
              )}
            </div>
          )
        )}

        {busy && (
          <div className="flex items-center gap-2 text-[12px] text-mist-400">
            <Spinner />
            {status}
          </div>
        )}
      </div>

      {/* composer */}
      <div className="border-t border-ink-800 p-3">
        <Textarea
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) send(input);
          }}
          rows={3}
          placeholder="Describe a change, paste client guidelines, or ask why the agent does something…"
          className="text-[12.5px]"
        />
        <div className="mt-2 flex items-center justify-between">
          <span className="text-[10.5px] text-mist-400">⌘↵ to send</span>
          <Button variant="primary" onClick={() => send(input)} disabled={!input.trim() || busy}>
            Send
          </Button>
        </div>
      </div>
    </div>
  );
}

/* ------------------------------------------------------ proposal card --- */
function ProposalCard({
  proposal,
  applied,
  verify,
  applying,
  onVerify,
  onApply,
  onDiscard,
  onPreview,
}: {
  proposal: Proposal;
  applied: boolean;
  verify?: { running: boolean; done: number; total: number; run?: TestRun };
  applying: boolean;
  onVerify: () => void;
  onApply: () => void;
  onDiscard: () => void;
  onPreview: () => void;
}) {
  const [open, setOpen] = useState(true);
  // A warning the change caused reads very differently from one it inherited:
  // the first is a reason to look again, the second is just the state of the
  // graph. Showing them in one undifferentiated list hid the ones that mattered.
  const introduced = new Set((proposal.new_lint ?? []).map((l) => l.message));
  const warnings = proposal.lint.filter((l) => l.severity === "warning");
  const caused = warnings.filter((w) => introduced.has(w.message));
  const inherited = warnings.filter((w) => !introduced.has(w.message));
  const retired = proposal.retire_tests ?? [];
  const findings = proposal.findings ?? [];
  const run = verify?.run;

  return (
    <div
      className={cx(
        "rounded-lg border bg-ink-850",
        applied ? "border-emerald-700/40" : "border-ink-700"
      )}
    >
      <div className="flex items-center gap-2 border-b border-ink-800 px-3 py-2">
        <button
          onClick={() => setOpen(!open)}
          className="flex items-center gap-1.5 text-[11px] font-semibold text-mist-300 hover:text-mist-100"
        >
          <Icon.Chevron className={cx("size-3 transition-transform", open && "rotate-90")} />
          {proposal.ops.length > 0
            ? `${proposal.ops.length} change${proposal.ops.length === 1 ? "" : "s"}`
            : retired.length > 0
              ? `${retired.length} test${retired.length === 1 ? "" : "s"} retired`
              : `${findings.length} finding${findings.length === 1 ? "" : "s"}, no change needed`}
        </button>
        {applied ? (
          <Badge tone="green">
            <Icon.Check className="size-2.5" /> applied
          </Badge>
        ) : (
          proposal.ops.length > 0 && (
            <button
              onClick={onPreview}
              className="text-[10.5px] text-signal-2 underline-offset-2 hover:underline"
            >
              show on graph
            </button>
          )
        )}
        <span className="ml-auto flex items-center gap-1.5">
          {retired.length > 0 && proposal.ops.length > 0 && (
            <Badge tone="rose">−{retired.length}</Badge>
          )}
          {proposal.tests?.length > 0 && (
            <Badge tone="violet">
              +{proposal.tests.length} test{proposal.tests.length === 1 ? "" : "s"}
            </Badge>
          )}
        </span>
      </div>

      {open && (
        <div className="space-y-1 px-3 py-2">
          {proposal.diff.map((d, i) => (
            <div key={i} className="flex items-start gap-2 text-[11.5px] leading-relaxed">
              <span
                className={cx(
                  "mt-1 size-1.5 shrink-0 rounded-full",
                  d.op.startsWith("add")
                    ? "bg-emerald-400"
                    : d.op.startsWith("delete")
                      ? "bg-rose-400"
                      : "bg-amber-400"
                )}
              />
              <span className="text-mist-300">{d.summary}</span>
            </div>
          ))}

          {/* A test the Copilot judged to be wrong rather than merely failing.
              It's a call with consequences, so it's shown with its reasoning and
              accepted or rejected alongside the graph edits. */}
          {retired.length > 0 && (
            <div className="mt-2 space-y-1.5 border-t border-ink-800 pt-2">
              {retired.map((t) => (
                <div key={t.case_id} className="text-[11.5px] leading-relaxed">
                  <div className="flex items-start gap-2">
                    <span className="mt-1 size-1.5 shrink-0 rounded-full bg-rose-400" />
                    <span className="text-mist-300">
                      Retire test{" "}
                      <span className="text-mist-100">&ldquo;{t.name}&rdquo;</span>
                    </span>
                  </div>
                  <div className="pl-[14px] text-[11px] text-mist-400">{t.reason}</div>
                </div>
              ))}
            </div>
          )}

          {warnings.length > 0 && (
            <div className="mt-2 space-y-1 border-t border-ink-800 pt-2">
              {caused.length > 0 && (
                <div className="mb-1 font-mono text-[9.5px] uppercase tracking-[0.1em] text-amber-300/70">
                  Introduced by this change
                </div>
              )}
              {caused.map((w, i) => (
                <div key={`c${i}`} className="flex items-start gap-1.5 text-[11px] text-amber-300">
                  <Icon.Warn className="mt-0.5 size-3 shrink-0" />
                  {w.message}
                </div>
              ))}
              {inherited.length > 0 && caused.length > 0 && (
                <div className="mb-1 mt-2 font-mono text-[9.5px] uppercase tracking-[0.1em] text-mist-400/70">
                  Already present
                </div>
              )}
              {inherited.map((w, i) => (
                <div key={`i${i}`} className="flex items-start gap-1.5 text-[11px] text-mist-400">
                  <Icon.Warn className="mt-0.5 size-3 shrink-0" />
                  {w.message}
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Why, then whether it worked — both above the accept buttons, because
          both are things you'd want to know before pressing Apply. */}
      {open && findings.length > 0 && <Findings findings={findings} />}
      {proposal.verification && <VerificationStrip result={proposal.verification} />}

      {/* verification */}
      {verify && (
        <div className="border-t border-ink-800 px-3 py-2">
          {verify.running ? (
            <div className="flex items-center gap-2 text-[11.5px] text-mist-400">
              <Spinner />
              Simulating {verify.total || "…"} calls against the proposed graph
              {verify.total ? ` — ${verify.done}/${verify.total}` : ""}
            </div>
          ) : run ? (
            <div className="space-y-1.5">
              <div className="flex items-center gap-2 text-[11.5px]">
                {run.failed === 0 ? (
                  <Badge tone="green">
                    <Icon.Check className="size-2.5" /> {run.passed}/{run.total} passing
                  </Badge>
                ) : (
                  <Badge tone="rose">
                    {run.failed} of {run.total} failing
                  </Badge>
                )}
                <span className="text-mist-400">on the proposed graph · {run.duration_s}s</span>
              </div>
              {run.results
                .filter((r) => !r.passed)
                .slice(0, 3)
                .map((r) => (
                  <div key={r.case_id} className="pl-1 text-[11px] text-rose-300/80">
                    ✗ {r.name}
                  </div>
                ))}
            </div>
          ) : null}
        </div>
      )}

      {!applied && (
        <div className="flex items-center gap-2 border-t border-ink-800 px-3 py-2">
          {/* A diagnosis with no change to make has nothing to accept — the
              finding is the whole deliverable. */}
          {(proposal.ops.length > 0 || retired.length > 0) && (
            <Button variant="primary" size="xs" onClick={onApply} loading={applying}>
              Apply
            </Button>
          )}
          {/* Nothing to verify when the graph isn't changing. */}
          {proposal.ops.length > 0 && (
            <Button
              size="xs"
              onClick={onVerify}
              disabled={verify?.running}
              title="Run the test suite against this proposal without applying it"
            >
              Verify first
            </Button>
          )}
          <Button variant="ghost" size="xs" onClick={onDiscard} className="ml-auto">
            Dismiss
          </Button>
        </div>
      )}
    </div>
  );
}
