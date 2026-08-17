import { useEffect, useState } from "react";
import { api } from "../api";
import type { VersionEntry } from "../types";
import { Badge, Button, Empty, Icon, Spinner, cx } from "./ui";

const sourceTone = { copilot: "teal", manual: "neutral", revert: "amber" } as const;

export function VersionsPanel({
  agentId,
  currentVersion,
  onReverted,
}: {
  agentId: string;
  currentVersion: number;
  onReverted: () => void;
}) {
  const [versions, setVersions] = useState<VersionEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<number | null>(null);
  const [open, setOpen] = useState<number | null>(null);

  async function load() {
    setLoading(true);
    try {
      setVersions((await api.versions(agentId)).versions);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [agentId, currentVersion]);

  async function revert(v: number) {
    setBusy(v);
    try {
      await api.revert(agentId, v);
      onReverted();
      await load();
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="flex h-full flex-col">
      <div className="border-b border-ink-800 px-5 py-3">
        <div className="text-[13px] font-semibold">History</div>
        <div className="text-[11px] text-mist-400">
          Every save is a version. Reverting appends a new one, so nothing is ever lost.
        </div>
      </div>

      <div className="flex-1 overflow-y-auto">
        {loading ? (
          <div className="flex justify-center py-16">
            <Spinner className="size-5 text-mist-400" />
          </div>
        ) : versions.length === 0 ? (
          <Empty icon={<Icon.History className="size-6" />} title="No history yet" />
        ) : (
          <div className="divide-y divide-ink-850">
            {versions.map((v) => {
              const isCurrent = v.version === currentVersion;
              return (
                <div key={v.version} className={cx(open === v.version && "bg-ink-900/50")}>
                  <div className="flex items-center gap-3 px-5 py-2.5">
                    <button
                      onClick={() => setOpen(open === v.version ? null : v.version)}
                      className="flex min-w-0 flex-1 items-center gap-2.5 text-left"
                      disabled={!v.ops.length}
                    >
                      <span
                        className={cx(
                          "shrink-0 font-mono text-[11px]",
                          isCurrent ? "text-signal-2" : "text-mist-400"
                        )}
                      >
                        v{v.version}
                      </span>
                      <span className="truncate text-[12.5px] text-mist-100">{v.label}</span>
                      <Badge tone={sourceTone[v.source as keyof typeof sourceTone] ?? "neutral"}>
                        {v.source}
                      </Badge>
                      {isCurrent && <Badge tone="green">current</Badge>}
                      <span className="ml-auto shrink-0 text-[10.5px] text-mist-400">
                        {v.node_count} nodes
                        {v.ops.length ? ` · ${v.ops.length} ops` : ""}
                      </span>
                    </button>
                    {!isCurrent && (
                      <Button
                        size="xs"
                        variant="subtle"
                        onClick={() => revert(v.version)}
                        loading={busy === v.version}
                      >
                        Revert
                      </Button>
                    )}
                  </div>

                  {open === v.version && v.ops.length > 0 && (
                    <div className="space-y-1 px-5 pb-3 pl-[62px]">
                      {v.ops.map((op: any, i: number) => (
                        <div key={i} className="font-mono text-[10.5px] text-mist-400">
                          {op.op}
                          {op.name ? ` ${op.name}` : ""}
                          {op.node?.name ? ` ${op.node.name}` : ""}
                          {op.edge?.function ? ` ${op.edge.function}` : ""}
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
