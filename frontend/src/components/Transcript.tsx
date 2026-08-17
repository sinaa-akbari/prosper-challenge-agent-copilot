import type { SimTurn } from "../types";
import { cx } from "./ui";

/**
 * A simulated call, rendered to be read rather than skimmed.
 *
 * A test transcript is a document you study when something failed, so this is a
 * timeline rather than a chat: a node gutter on the left so you can see where
 * each turn happened, speakers distinguished by colour rather than alignment,
 * and state transitions as full-width rules carrying the data they collected.
 * The transition rows are usually where the answer is.
 */
export function Transcript({ turns }: { turns: SimTurn[] }) {
  if (!turns?.length) {
    return (
      <div className="px-3 py-4 text-center text-[11.5px] text-mist-400">
        This call produced no turns.
      </div>
    );
  }

  let lastNode = "";

  return (
    <div className="overflow-hidden rounded-[5px] border border-ink-800 bg-ink-950">
      {turns.map((t, i) => {
        if (t.speaker === "transition") {
          const args = Object.entries(t.args ?? {});
          return (
            <div key={i} className="flex items-start gap-2 bg-violet-500/[0.05] px-3 py-1.5">
              <span className="w-[104px] shrink-0" />
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-1.5">
                  <span className="font-mono text-[10px] text-violet-300">{t.function}</span>
                  <span className="text-[10px] text-mist-400">→</span>
                  <span className="font-mono text-[10px] text-violet-300">{t.target}</span>
                </div>
                {args.length > 0 && (
                  <div className="mt-1 flex flex-wrap gap-1">
                    {args.map(([k, v]) => (
                      <span
                        key={k}
                        className="rounded-[3px] bg-ink-800 px-1.5 py-[2px] font-mono text-[9.5px] text-mist-300"
                        title={`${k}: ${String(v)}`}
                      >
                        <span className="text-violet-300/80">{k}</span>{" "}
                        {String(v).slice(0, 40)}
                      </span>
                    ))}
                  </div>
                )}
              </div>
            </div>
          );
        }

        // Only label the node when it changes — repeating it every line is noise.
        const showNode = t.node && t.node !== lastNode;
        if (t.node) lastNode = t.node;
        const isAgent = t.speaker === "agent";

        return (
          <div
            key={i}
            className={cx(
              "flex items-start gap-2 px-3 py-1.5",
              i > 0 && "border-t border-ink-900"
            )}
          >
            <span
              className={cx(
                "w-[104px] shrink-0 truncate pt-[1px] font-mono text-[9.5px]",
                showNode ? "text-mist-400/70" : "text-transparent"
              )}
              title={t.node}
            >
              {showNode ? t.node : "·"}
            </span>
            <span
              className={cx(
                "w-[46px] shrink-0 pt-[1px] font-mono text-[9.5px] uppercase tracking-[0.08em]",
                isAgent ? "text-signal-2" : "text-sky-300"
              )}
            >
              {isAgent ? "agent" : "caller"}
            </span>
            <span
              className={cx(
                "min-w-0 flex-1 text-[11.5px] leading-[1.6]",
                isAgent ? "text-mist-300" : "text-mist-100"
              )}
            >
              {t.text}
            </span>
          </div>
        );
      })}
    </div>
  );
}
