import { useEffect, useState } from "react";
import { api, awaitJob } from "../api";
import type { AgentConfig, CaseResult, TestCase, TestRun } from "../types";
import { Transcript } from "./Transcript";
import { Badge, Button, Empty, Icon, Spinner, cx } from "./ui";

interface Props {
  agentId: string;
  config: AgentConfig;
  onRunComplete: (run: TestRun) => void;
  /** Hand the whole failing run to the Copilot for root-cause analysis. */
  onDiagnose: () => void;
}

const originTone = { regression: "violet", generated: "blue", manual: "neutral" } as const;

export function TestsPanel({ agentId, onRunComplete, onDiagnose }: Props) {
  const [cases, setCases] = useState<TestCase[]>([]);
  const [run, setRun] = useState<TestRun | null>(null);
  const [loading, setLoading] = useState(true);
  const [running, setRunning] = useState<{ done: number; total: number } | null>(null);
  const [generating, setGenerating] = useState(false);
  const [open, setOpen] = useState<string | null>(null);
  const [error, setError] = useState("");

  async function load() {
    setLoading(true);
    try {
      const data = await api.getTests(agentId);
      setCases(data.cases);
      setRun(data.last_run);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [agentId]);

  async function runAll() {
    setError("");
    setRunning({ done: 0, total: cases.length });
    try {
      const { job_id } = await api.runTests(agentId, {});
      const result = await awaitJob<TestRun>(job_id, (job) =>
        setRunning({ done: job.progress.done, total: job.progress.total })
      );
      setRun(result);
      onRunComplete(result);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setRunning(null);
    }
  }

  async function generate() {
    setError("");
    setGenerating(true);
    try {
      const { job_id } = await api.generateTests(agentId, 6);
      await awaitJob(job_id);
      await load();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setGenerating(false);
    }
  }

  async function remove(id: string) {
    await api.deleteTest(agentId, id);
    setCases((c) => c.filter((x) => x.id !== id));
  }

  const byCase = new Map((run?.results ?? []).map((r) => [r.case_id, r]));

  return (
    <div className="flex h-full flex-col">
      {/* toolbar */}
      <div className="flex items-center gap-2 border-b border-ink-800 px-5 py-3">
        <div>
          <div className="text-[13px] font-semibold">Test suite</div>
          <div className="text-[11px] text-mist-400">
            Simulated callers, graded on the transcript. No audio, so a full run takes seconds.
          </div>
        </div>
        <div className="ml-auto flex items-center gap-2">
          {run && !running && (
            <Badge tone={run.failed === 0 ? "green" : "rose"}>
              {run.passed}/{run.total} passing · {run.duration_s}s
            </Badge>
          )}
          {run && run.failed > 0 && !running && (
            <Button size="sm" variant="primary" onClick={onDiagnose}>
              <Icon.Sparkle className="size-3.5" /> Diagnose {run.failed} failure
              {run.failed === 1 ? "" : "s"}
            </Button>
          )}
          <Button size="sm" onClick={generate} loading={generating} disabled={!!running}>
            Generate cases
          </Button>
          <Button
            variant="primary"
            size="sm"
            onClick={runAll}
            disabled={!cases.length || !!running}
            loading={!!running}
          >
            {running ? `Running ${running.done}/${running.total}` : "Run all"}
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
        ) : cases.length === 0 ? (
          <Empty
            icon={<Icon.Flask className="size-6" />}
            title="No test cases yet"
            hint="Generate a starter suite from the graph, or let the Copilot write regression tests when it fixes a production issue."
            action={
              <Button variant="primary" onClick={generate} loading={generating}>
                Generate cases from the graph
              </Button>
            }
          />
        ) : (
          <div className="divide-y divide-ink-850">
            {cases.map((c) => (
              <CaseRow
                key={c.id}
                testCase={c}
                result={byCase.get(c.id)}
                running={!!running && !byCase.get(c.id)}
                expanded={open === c.id}
                onToggle={() => setOpen(open === c.id ? null : c.id)}
                onDelete={() => remove(c.id)}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function CaseRow({
  testCase,
  result,
  running,
  expanded,
  onToggle,
  onDelete,
}: {
  testCase: TestCase;
  result?: CaseResult;
  running: boolean;
  expanded: boolean;
  onToggle: () => void;
  onDelete: () => void;
}) {
  const status = result ? (result.passed ? "pass" : "fail") : running ? "running" : "idle";

  return (
    <div className={cx(expanded && "bg-ink-900/50")}>
      <div className="flex items-center gap-3 px-5 py-2.5">
        <button onClick={onToggle} className="flex min-w-0 flex-1 items-center gap-2.5 text-left">
          <StatusDot status={status} />
          <span className="truncate text-[12.5px] text-mist-100">{testCase.name}</span>
          <Badge tone={originTone[testCase.origin] ?? "neutral"}>{testCase.origin}</Badge>
          {result && (
            <span className="ml-auto shrink-0 text-[10.5px] text-mist-400">
              {result.verdict.results.filter((r) => r.passed).length}/
              {result.verdict.results.length} checks · {result.duration_s}s
            </span>
          )}
        </button>
        <Button variant="ghost" size="xs" onClick={onDelete} title="Delete case">
          <Icon.Trash className="size-3" />
        </Button>
      </div>

      {expanded && (
        <div className="space-y-3 px-5 pb-4 pl-[46px]">
          <div className="text-[11.5px] leading-relaxed text-mist-400">
            <span className="text-mist-300">{testCase.persona.description}</span>{" "}
            {testCase.persona.goal}
          </div>

          <div className="space-y-1">
            {testCase.assertions.map((a, i) => {
              const verdict = result?.verdict.results[i];
              return (
                <div key={i} className="flex items-start gap-2 text-[11.5px]">
                  {verdict ? (
                    verdict.passed ? (
                      <Icon.Check className="mt-0.5 size-3 shrink-0 text-emerald-400" />
                    ) : (
                      <Icon.X className="mt-0.5 size-3 shrink-0 text-rose-400" />
                    )
                  ) : (
                    <span className="mt-1.5 size-1.5 shrink-0 rounded-full bg-ink-600" />
                  )}
                  <div className="min-w-0">
                    <div className={verdict && !verdict.passed ? "text-mist-100" : "text-mist-300"}>
                      {a}
                    </div>
                    {verdict && !verdict.passed && (
                      <div className="mt-0.5 text-[11px] text-rose-300/80">{verdict.reason}</div>
                    )}
                  </div>
                </div>
              );
            })}
          </div>

          {result?.verdict.error && (
            <div className="rounded border border-rose-900/50 bg-rose-950/20 px-2 py-1.5 text-[11px] text-rose-300">
              {result.verdict.error}
            </div>
          )}

          {result?.simulation && (
            <details className="group">
              <summary className="flex cursor-pointer list-none items-center gap-2 text-[11px] text-mist-400 hover:text-mist-100">
                <Icon.Chevron className="size-3 transition-transform group-open:rotate-90" />
                <span className="group-open:hidden">Show transcript</span>
                <span className="hidden group-open:inline">Hide transcript</span>
                {result.simulation.path && (
                  <span className="font-mono text-[10px] text-mist-400/70">
                    {result.simulation.path.join(" → ")}
                  </span>
                )}
              </summary>
              <div className="mt-2 max-h-[420px] overflow-auto">
                <Transcript turns={result.simulation.turns ?? []} />
              </div>
              {result.simulation.end_reason && (
                <div className="mt-1.5 font-mono text-[10px] text-mist-400">
                  ended: {result.simulation.end_reason}
                  {result.simulation.end_reason === "hangup" &&
                    " — the simulated caller gave up"}
                  {result.simulation.end_reason === "max_turns" &&
                    " — hit the turn limit, usually a loop"}
                </div>
              )}
            </details>
          )}
        </div>
      )}
    </div>
  );
}

function StatusDot({ status }: { status: "pass" | "fail" | "running" | "idle" }) {
  if (status === "running") return <Spinner className="size-3 shrink-0 text-signal" />;
  return (
    <span
      className={cx(
        "size-2 shrink-0 rounded-full",
        status === "pass" && "bg-emerald-400",
        status === "fail" && "bg-rose-400",
        status === "idle" && "bg-ink-600"
      )}
    />
  );
}
