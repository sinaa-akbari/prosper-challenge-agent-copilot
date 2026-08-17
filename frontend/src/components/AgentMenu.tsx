import { useEffect, useRef, useState } from "react";
import type { AgentSummary } from "../types";
import { Badge, Icon, Spinner, cx } from "./ui";

/**
 * The agent switcher, and everything you can do to an agent.
 *
 * It replaced a native `<select>`, which could hold names and nothing else.
 * Activate, rename and delete all belong next to the name they act on — a
 * delete button in the toolbar acts on "the current agent", which is exactly
 * the kind of ambiguity you don't want on a destructive action.
 *
 * Only one agent is live at a time, deployment-wide, because there is one phone
 * number and a phone number rings one thing. Activating another takes the line
 * from whoever had it, so the button says so.
 */
export function AgentMenu({
  agents,
  agentId,
  activeId,
  busy,
  onSelect,
  onCreate,
  onActivate,
  onDeactivate,
  onRename,
  onDelete,
}: {
  agents: AgentSummary[];
  agentId: string;
  activeId: string;
  busy?: string;
  onSelect: (id: string) => void;
  onCreate: () => void;
  onActivate: (id: string) => void;
  onDeactivate: (id: string) => void;
  onRename: (id: string, name: string) => void;
  onDelete: (id: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [renaming, setRenaming] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [confirming, setConfirming] = useState<string | null>(null);
  const box = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const away = (e: MouseEvent) => {
      if (box.current && !box.current.contains(e.target as any)) close();
    };
    const esc = (e: KeyboardEvent) => e.key === "Escape" && close();
    document.addEventListener("mousedown", away);
    document.addEventListener("keydown", esc);
    return () => {
      document.removeEventListener("mousedown", away);
      document.removeEventListener("keydown", esc);
    };
  }, [open]);

  function close() {
    setOpen(false);
    setRenaming(null);
    setConfirming(null);
  }

  const current = agents.find((a) => a.id === agentId);

  return (
    <div ref={box} className="relative">
      <button
        onClick={() => setOpen((v) => !v)}
        data-testid="agent-menu"
        className="flex h-7 max-w-[340px] items-center gap-1.5 rounded-[4px] border border-transparent px-1.5 text-[13px] font-medium text-mist-100 transition-colors hover:border-ink-700 hover:bg-ink-850"
      >
        <span className="truncate">{current?.name ?? agentId}</span>
        {activeId === agentId && (
          <span className="size-1.5 shrink-0 rounded-full bg-emerald-400" title="Live on the phone" />
        )}
        <Icon.Chevron className={cx("size-3 shrink-0 rotate-90 text-mist-400", open && "-rotate-90")} />
      </button>

      {open && (
        <div className="absolute left-0 top-9 z-50 w-[400px] overflow-hidden rounded-lg border border-ink-700 bg-ink-900 shadow-2xl">
          <div className="max-h-[380px] overflow-y-auto">
            {agents.map((a) => {
              const live = a.id === activeId;
              const working = busy === a.id;
              return (
                <div
                  key={a.id}
                  className={cx(
                    "border-b border-ink-850 px-3 py-2.5 last:border-b-0",
                    a.id === agentId && "bg-ink-850/60"
                  )}
                >
                  {renaming === a.id ? (
                    <div className="flex items-center gap-1.5">
                      <input
                        autoFocus
                        value={draft}
                        onChange={(e) => setDraft(e.target.value)}
                        onKeyDown={(e) => {
                          if (e.key === "Enter" && draft.trim()) {
                            onRename(a.id, draft.trim());
                            setRenaming(null);
                          }
                          if (e.key === "Escape") setRenaming(null);
                        }}
                        className="min-w-0 flex-1 rounded-[4px] border border-signal bg-ink-950 px-2 py-1 text-[12.5px] text-mist-100 outline-none"
                      />
                      <Action
                        onClick={() => {
                          if (draft.trim()) onRename(a.id, draft.trim());
                          setRenaming(null);
                        }}
                      >
                        Save
                      </Action>
                      <Action onClick={() => setRenaming(null)}>Cancel</Action>
                    </div>
                  ) : (
                    <>
                      <div className="flex items-center gap-2">
                        <button
                          onClick={() => {
                            onSelect(a.id);
                            close();
                          }}
                          className="min-w-0 flex-1 truncate text-left text-[12.5px] text-mist-100 hover:text-white"
                        >
                          {a.name}
                        </button>
                        {live ? (
                          <Badge tone="green">
                            <Icon.Check className="size-2.5" /> activated
                          </Badge>
                        ) : working ? (
                          <Spinner className="size-3 text-mist-400" />
                        ) : (
                          <button
                            onClick={() => onActivate(a.id)}
                            title={
                              activeId
                                ? "Put this agent on the phone — the current one stops answering"
                                : "Put this agent on the phone"
                            }
                            className="shrink-0 rounded-[3px] border border-signal/40 px-1.5 py-[3px] font-mono text-[9.5px] uppercase tracking-[0.08em] text-signal-2 hover:bg-signal/12"
                          >
                            Activate
                          </button>
                        )}
                      </div>

                      <div className="mt-1 flex items-center gap-2">
                        <span className="font-mono text-[9.5px] text-mist-400/70">
                          v{a.version} · {a.node_count} node{a.node_count === 1 ? "" : "s"}
                        </span>
                        <span className="ml-auto flex items-center gap-2">
                          {live && (
                            <Action onClick={() => onDeactivate(a.id)}>Deactivate</Action>
                          )}
                          <Action
                            onClick={() => {
                              setRenaming(a.id);
                              setDraft(a.name);
                            }}
                          >
                            Rename
                          </Action>
                          {confirming === a.id ? (
                            <Action
                              danger
                              onClick={() => {
                                onDelete(a.id);
                                close();
                              }}
                            >
                              Really delete?
                            </Action>
                          ) : (
                            <Action danger onClick={() => setConfirming(a.id)}>
                              Delete
                            </Action>
                          )}
                        </span>
                      </div>

                      {/* Deleting what's on the phone is a bigger deal than
                          deleting a draft, so say which one this is. */}
                      {confirming === a.id && live && (
                        <div className="mt-1.5 text-[11px] text-rose-300/90">
                          This agent is answering the phone. Deleting it takes the
                          number offline.
                        </div>
                      )}
                    </>
                  )}
                </div>
              );
            })}
          </div>

          <button
            onClick={() => {
              onCreate();
              close();
            }}
            className="flex w-full items-center gap-1.5 border-t border-ink-700 px-3 py-2.5 text-left text-[12px] text-mist-300 hover:bg-ink-850 hover:text-mist-100"
          >
            <Icon.Plus className="size-3" /> New agent
          </button>
        </div>
      )}
    </div>
  );
}

function Action({
  children,
  onClick,
  danger,
}: {
  children: React.ReactNode;
  onClick: () => void;
  danger?: boolean;
}) {
  return (
    <button
      onClick={onClick}
      className={cx(
        "text-[10.5px] underline-offset-2 hover:underline",
        danger ? "text-rose-300/80 hover:text-rose-300" : "text-mist-400 hover:text-mist-200"
      )}
    >
      {children}
    </button>
  );
}
