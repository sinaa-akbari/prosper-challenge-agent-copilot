import type { Finding, RootCause, Verification } from "../types";
import { Badge, Icon, cx } from "./ui";

/**
 * The Copilot's diagnosis, and what happened when it was run.
 *
 * Showing the reasoning isn't decoration. A diff tells you what changed; it
 * can't tell you whether the change was aimed at the right thing, and that is
 * the only judgement a reviewer can actually make faster than the model. So the
 * root cause and the quote that proves it sit above the operations, in that
 * order — and if the cited evidence doesn't support the class, you've found a
 * bad proposal without reading a single op.
 */

const CAUSE_LABEL: Record<RootCause, string> = {
  required_field_blocks_exit: "Required field blocks the exit",
  node_passed_through: "Node passed straight through",
  missing_path: "No path for what the caller wanted",
  edge_description_mismatch: "Edge description didn't match",
  node_overloaded: "Node doing too much",
  conflicting_instructions: "Instructions conflict",
  broken_test: "The test is wrong",
};

// Structural faults read differently from wording ones — the first group can't
// be fixed by changing what a node says, and that distinction is the whole
// point of making the model commit to a class.
const STRUCTURAL: RootCause[] = [
  "required_field_blocks_exit",
  "node_passed_through",
  "missing_path",
  "node_overloaded",
];

export function Findings({ findings }: { findings: Finding[] }) {
  if (!findings?.length) return null;
  return (
    <div className="space-y-2 border-t border-ink-800 px-3 py-2">
      <div className="text-[10px] font-semibold uppercase tracking-[0.1em] text-mist-400">
        Diagnosis
      </div>
      {findings.map((f, i) => {
        const structural = STRUCTURAL.includes(f.root_cause);
        return (
          <div key={i} className="space-y-1">
            <div className="flex flex-wrap items-center gap-1.5">
              <Badge tone={f.root_cause === "broken_test" ? "amber" : structural ? "rose" : "blue"}>
                {CAUSE_LABEL[f.root_cause] ?? f.root_cause}
              </Badge>
              {f.case_name && (
                <span className="min-w-0 truncate text-[11px] text-mist-400">
                  {f.case_name}
                </span>
              )}
            </div>
            {/* The citation. Quoted, because a diagnosis you can't trace to a
                turn in the transcript is a guess wearing a label. */}
            <div className="border-l-2 border-ink-700 pl-2 text-[11px] italic leading-relaxed text-mist-400">
              {f.evidence}
            </div>
            {f.fix && (
              <div className="text-[11.5px] leading-relaxed text-mist-300">{f.fix}</div>
            )}
          </div>
        );
      })}
    </div>
  );
}

export function VerificationStrip({ result }: { result: Verification }) {
  if (result.error) {
    return (
      <div className="border-t border-ink-800 px-3 py-2 text-[11px] text-amber-300/80">
        Couldn't run this proposal automatically ({result.error}). Use Verify first.
      </div>
    );
  }

  const fixed = result.fixed ?? [];
  const broke = result.broke ?? [];
  const still = result.still_failing ?? [];
  const clean = !broke.length && !still.length;

  return (
    <div className="space-y-1 border-t border-ink-800 px-3 py-2">
      <div className="flex items-center gap-1.5 text-[11px]">
        {clean ? (
          <Icon.Check className="size-3 text-emerald-400" />
        ) : (
          <Icon.Warn className="size-3 text-amber-400" />
        )}
        <span className="font-semibold text-mist-300">
          {clean ? "Ran clean before you saw it" : "Ran before you saw it"}
        </span>
        <span className="text-mist-400">
          · {result.passed}/{result.total} of the affected calls
        </span>
      </div>
      {[
        [fixed, "now passing", "text-emerald-300/90"],
        [still, "still failing", "text-rose-300/90"],
        [broke, "broken by this change", "text-rose-300"],
      ].map(([names, label, tone], i) =>
        (names as string[]).length ? (
          <div key={i} className={cx("pl-[18px] text-[11px]", tone as string)}>
            {label as string}: {(names as string[]).join(", ")}
          </div>
        ) : null
      )}
      {(result.retired ?? []).length > 0 && (
        <div className="pl-[18px] text-[11px] text-mist-400">
          not scored (retired): {(result.retired ?? []).join(", ")}
        </div>
      )}
    </div>
  );
}
