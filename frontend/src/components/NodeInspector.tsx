import { useEffect, useMemo, useState } from "react";
import type { AgentConfig, Edge, LintIssue, Node } from "../types";
import {
  AutoTextarea,
  Badge,
  Button,
  Collapse,
  Icon,
  Input,
  Select,
  Toggle,
  cx,
} from "./ui";

interface Props {
  config: AgentConfig;
  nodeName: string;
  lint: LintIssue[];
  onChange: (next: AgentConfig) => void;
  onClose: () => void;
  onSelect: (name: string) => void;
}

const SLOT_TYPES = ["string", "number", "integer", "boolean"] as const;

/* Renaming rewrites every reference, exactly as the server's `rename_node` op does. */
function renameEverywhere(config: AgentConfig, from: string, to: string): AgentConfig {
  return {
    ...config,
    nodes: config.nodes.map((n) => ({
      ...n,
      name: n.name === from ? to : n.name,
      edges: (n.edges ?? []).map((e) => (e.target === from ? { ...e, target: to } : e)),
    })),
    initial_node: config.initial_node === from ? to : config.initial_node,
    global_edges: (config.global_edges ?? []).map((e) =>
      e.target === from ? { ...e, target: to } : e
    ),
  };
}

const slugify = (s: string) =>
  s.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "") || "field";

/* ========================================================================== */
export function NodeInspector({ config, nodeName, lint, onChange, onClose, onSelect }: Props) {
  const node = config.nodes.find((n) => n.name === nodeName);
  const [renaming, setRenaming] = useState(false);
  const [draftName, setDraftName] = useState(nodeName);
  const [openEdge, setOpenEdge] = useState<number | null>(null);

  useEffect(() => {
    setRenaming(false);
    setDraftName(nodeName);
    setOpenEdge(null);
  }, [nodeName]);

  const issues = useMemo(() => lint.filter((i) => i.node === nodeName), [lint, nodeName]);
  const incoming = useMemo(
    () =>
      config.nodes.flatMap((n) =>
        (n.edges ?? [])
          .filter((e) => e.target === nodeName)
          .map((e) => ({ from: n.name, fn: e.function }))
      ),
    [config, nodeName]
  );

  if (!node) return null;

  const isStart = config.initial_node === nodeName;
  const edges = node.edges ?? [];

  const update = (patch: Partial<Node>) =>
    onChange({
      ...config,
      nodes: config.nodes.map((n) => (n.name === nodeName ? { ...n, ...patch } : n)),
    });

  const setEdges = (next: Edge[]) => update({ edges: next });
  const updateEdge = (i: number, patch: Partial<Edge>) =>
    setEdges(edges.map((e, k) => (k === i ? { ...e, ...patch } : e)));

  const addEdge = () => {
    const target = config.nodes.find((n) => n.name !== nodeName)?.name ?? nodeName;
    let fn = `to_${target}`;
    let n = 2;
    while (edges.some((e) => e.function === fn)) fn = `to_${target}_${n++}`;
    setEdges([
      ...edges,
      { function: fn, description: "", target, properties: {}, required: [] },
    ]);
    setOpenEdge(edges.length);
  };

  const commitRename = () => {
    const clean = slugify(draftName);
    setRenaming(false);
    if (!clean || clean === nodeName) {
      setDraftName(nodeName);
      return;
    }
    if (config.nodes.some((n) => n.name === clean)) {
      setDraftName(nodeName);
      return;
    }
    onChange(renameEverywhere(config, nodeName, clean));
    onSelect(clean);
  };

  const deleteNode = () => {
    if (isStart) return;
    onChange({
      ...config,
      nodes: config.nodes
        .filter((n) => n.name !== nodeName)
        .map((n) => ({ ...n, edges: (n.edges ?? []).filter((e) => e.target !== nodeName) })),
      global_edges: (config.global_edges ?? []).filter((e) => e.target !== nodeName),
    });
    onClose();
  };

  return (
    <aside className="fade-up flex h-full w-[400px] shrink-0 flex-col border-l border-ink-800 bg-ink-900">
      {/* ---------------------------------------------------------- header */}
      <header className="relative shrink-0 border-b border-ink-800 px-4 pb-3 pt-3.5">
        <div
          className={cx(
            "absolute inset-x-0 top-0 h-px",
            isStart
              ? "bg-gradient-to-r from-transparent via-signal/60 to-transparent"
              : node.end
                ? "bg-gradient-to-r from-transparent via-mist-400/30 to-transparent"
                : "bg-transparent"
          )}
        />
        <div className="flex items-center gap-2">
          {renaming ? (
            <Input
              autoFocus
              value={draftName}
              onChange={(e) => setDraftName(e.target.value)}
              onBlur={commitRename}
              onKeyDown={(e) => {
                if (e.key === "Enter") commitRename();
                if (e.key === "Escape") {
                  setDraftName(nodeName);
                  setRenaming(false);
                }
              }}
              className="h-7 font-mono text-[13px]"
            />
          ) : (
            <button
              onClick={() => setRenaming(true)}
              title="Click to rename — every reference updates"
              className="group flex min-w-0 items-center gap-1.5"
            >
              <span className="truncate font-mono text-[14px] font-medium text-mist-100">
                {nodeName}
              </span>
              <span className="text-mist-400 opacity-0 transition-opacity group-hover:opacity-100">
                <Icon.Plus className="size-3 rotate-45" />
              </span>
            </button>
          )}
          <Button variant="ghost" size="xs" onClick={onClose} className="ml-auto shrink-0">
            <Icon.X className="size-3" />
          </Button>
        </div>

        <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
          {isStart && <Badge tone="teal">start of call</Badge>}
          {node.end && <Badge tone="neutral">ends call</Badge>}
          <Badge tone={edges.length ? "violet" : node.end ? "neutral" : "amber"}>
            {edges.length} exit{edges.length === 1 ? "" : "s"}
          </Badge>
          {incoming.length > 0 && (
            <Badge tone="neutral">
              {incoming.length} inbound
            </Badge>
          )}
        </div>
      </header>

      {/* ------------------------------------------------------------ body */}
      <div className="flex-1 space-y-5 overflow-y-auto px-4 py-4">
        {issues.length > 0 && (
          <div className="space-y-1">
            {issues.map((i, k) => (
              <div
                key={k}
                className={cx(
                  "flex items-start gap-1.5 rounded-md border px-2 py-1.5 text-[11px] leading-relaxed",
                  i.severity === "error"
                    ? "border-rose-900/50 bg-rose-950/20 text-rose-300"
                    : "border-amber-900/40 bg-amber-950/15 text-amber-300"
                )}
              >
                <Icon.Warn className="mt-0.5 size-3 shrink-0" />
                {i.message}
              </div>
            ))}
          </div>
        )}

        {/* instructions */}
        <section>
          <SectionHead
            title="Instructions"
            hint="what the agent must accomplish here — not a script to read out"
          />
          <AutoTextarea
            value={(node.task_messages ?? []).map((m) => m.content).join("\n\n")}
            onChange={(e) =>
              update({ task_messages: [{ role: "developer", content: e.target.value }] })
            }
            minRows={3}
            placeholder="Ask the caller for their date of birth and confirm it back to them…"
            className="text-[12.5px]"
          />
        </section>

        {/* behaviour */}
        <section className="space-y-1.5">
          <SectionHead title="Behaviour" />
          <Toggle
            checked={!!node.end}
            onChange={(v) => update({ end: v })}
            label="Ends the call"
            hint="the agent hangs up after this node's last line"
          />
          {!isStart && (
            <Toggle
              checked={false}
              onChange={() => onChange({ ...config, initial_node: nodeName })}
              label="Start the call here"
              hint={`currently starts at ${config.initial_node}`}
            />
          )}
        </section>

        {/* exits */}
        <section>
          <SectionHead
            title={`Exits (${edges.length})`}
            hint="the model routes on these descriptions"
            action={
              !node.end && (
                <Button size="xs" variant="subtle" onClick={addEdge}>
                  <Icon.Plus className="size-3" /> Add exit
                </Button>
              )
            }
          />

          {edges.length === 0 ? (
            <div
              className={cx(
                "rounded-lg border border-dashed px-3 py-4 text-center text-[11.5px] leading-relaxed",
                node.end
                  ? "border-ink-700 text-mist-400"
                  : "border-amber-900/40 bg-amber-950/10 text-amber-300/80"
              )}
            >
              {node.end
                ? "Terminal node — the call ends here."
                : "No exits. The caller reaches this node and gets stuck."}
            </div>
          ) : (
            <div className="space-y-2">
              {edges.map((edge, i) => (
                <EdgeCard
                  key={i}
                  index={i}
                  edge={edge}
                  nodes={config.nodes}
                  open={openEdge === i}
                  onToggle={() => setOpenEdge(openEdge === i ? null : i)}
                  onChange={(patch) => updateEdge(i, patch)}
                  onRemove={() => {
                    setEdges(edges.filter((_, k) => k !== i));
                    setOpenEdge(null);
                  }}
                  onGo={() => onSelect(edge.target)}
                />
              ))}
            </div>
          )}
        </section>

        {/* incoming */}
        {incoming.length > 0 && (
          <section>
            <SectionHead title="Reached from" />
            <div className="flex flex-wrap gap-1.5">
              {incoming.map(({ from, fn }, i) => (
                <button
                  key={i}
                  onClick={() => onSelect(from)}
                  title={`via ${fn}`}
                  className="group flex items-center gap-1.5 rounded-md border border-ink-700 bg-ink-850 px-2 py-1 text-[11px] transition-colors hover:border-ink-600 hover:bg-ink-800"
                >
                  <span className="font-mono text-mist-300 group-hover:text-mist-100">{from}</span>
                  <span className="font-mono text-[9.5px] text-mist-400">{fn}</span>
                </button>
              ))}
            </div>
          </section>
        )}
      </div>

      {/* ---------------------------------------------------------- footer */}
      <footer className="shrink-0 border-t border-ink-800 p-3">
        <Button
          variant="danger"
          size="xs"
          onClick={deleteNode}
          disabled={isStart}
          title={isStart ? "Can't delete the start node" : "Delete this node and any edge into it"}
        >
          <Icon.Trash className="size-3" /> Delete node
        </Button>
      </footer>
    </aside>
  );
}

/* -------------------------------------------------------------- section --- */
function SectionHead({
  title,
  hint,
  action,
}: {
  title: string;
  hint?: string;
  action?: React.ReactNode;
}) {
  return (
    <div className="mb-2 flex items-start gap-2">
      <div className="min-w-0">
        <div className="text-[10.5px] font-semibold uppercase tracking-[0.09em] text-mist-400">
          {title}
        </div>
        {hint && <div className="mt-0.5 text-[10.5px] leading-tight text-mist-400/70">{hint}</div>}
      </div>
      {action && <div className="ml-auto shrink-0">{action}</div>}
    </div>
  );
}

/* ------------------------------------------------------------ edge card --- */
const ACCENTS = ["bg-signal", "bg-violet-400", "bg-amber-400", "bg-sky-400", "bg-rose-400"];

function EdgeCard({
  index,
  edge,
  nodes,
  open,
  onToggle,
  onChange,
  onRemove,
  onGo,
}: {
  index: number;
  edge: Edge;
  nodes: Node[];
  open: boolean;
  onToggle: () => void;
  onChange: (patch: Partial<Edge>) => void;
  onRemove: () => void;
  onGo: () => void;
}) {
  const props = edge.properties ?? {};
  const slots = Object.keys(props);
  const required = edge.required ?? [];
  const accent = ACCENTS[index % ACCENTS.length];

  const renameSlot = (from: string, to: string) => {
    const clean = slugify(to);
    if (!clean || clean === from || props[clean]) return;
    const next: Record<string, any> = {};
    for (const [k, v] of Object.entries(props)) next[k === from ? clean : k] = v;
    onChange({
      properties: next,
      required: required.map((r) => (r === from ? clean : r)),
    });
  };

  const setSlot = (key: string, patch: any) =>
    onChange({ properties: { ...props, [key]: { ...props[key], ...patch } } });

  const removeSlot = (key: string) => {
    const next = { ...props };
    delete next[key];
    onChange({ properties: next, required: required.filter((r) => r !== key) });
  };

  const addSlot = () => {
    let key = "field";
    let n = 2;
    while (props[key]) key = `field_${n++}`;
    onChange({
      properties: { ...props, [key]: { type: "string", description: "" } },
      required: [...required, key],
    });
  };

  const toggleRequired = (key: string) =>
    onChange({
      required: required.includes(key) ? required.filter((r) => r !== key) : [...required, key],
    });

  return (
    <div
      className={cx(
        "overflow-hidden rounded-lg border transition-colors",
        open ? "border-ink-600 bg-ink-850" : "border-ink-700 bg-ink-850/60 hover:border-ink-600"
      )}
    >
      {/* summary row */}
      <div className="flex items-stretch">
        <span className={cx("w-0.5 shrink-0", accent, open ? "opacity-100" : "opacity-50")} />
        <button onClick={onToggle} className="flex min-w-0 flex-1 items-center gap-1.5 px-2 py-2">
          <Icon.Chevron
            className={cx(
              "size-3 shrink-0 text-mist-400 transition-transform duration-200",
              open && "rotate-90"
            )}
          />
          <span className="truncate font-mono text-[11.5px] text-mist-100">{edge.function}</span>
          <span className="shrink-0 text-mist-400">→</span>
          <span className="truncate font-mono text-[11.5px] text-mist-300">{edge.target}</span>
          {slots.length > 0 && (
            <Badge tone="violet" className="ml-auto shrink-0">
              {slots.length}
            </Badge>
          )}
          {!edge.description?.trim() && (
            <Badge tone="amber" className="ml-auto shrink-0" title="No condition — the model can't route on this">
              !
            </Badge>
          )}
        </button>
      </div>

      <Collapse open={open}>
        <div className="space-y-3 border-t border-ink-800 px-2.5 py-2.5">
          <Row label="Function">
            <Input
              value={edge.function}
              onChange={(e) => onChange({ function: e.target.value })}
              className="h-7 font-mono text-[11.5px]"
            />
          </Row>

          <div>
            <div className="mb-1 text-[11px] font-medium text-mist-300">Take this exit when…</div>
            <AutoTextarea
              value={edge.description}
              onChange={(e) => onChange({ description: e.target.value })}
              minRows={2}
              placeholder="the caller has confirmed the appointment time"
              className="text-[11.5px]"
            />
          </div>

          <Row label="Goes to">
            {/* h-8, not h-7: native selects clip descenders and underscores at
                tighter heights, and every node name here has an underscore. */}
            <Select
              value={edge.target}
              onChange={(e) => onChange({ target: e.target.value })}
              className="h-8 font-mono text-[11.5px]"
            >
              {nodes.map((n) => (
                <option key={n.name} value={n.name}>
                  {n.name}
                </option>
              ))}
            </Select>
          </Row>

          {/* data slots */}
          <div>
            <div className="mb-1.5 flex items-center justify-between">
              <span className="text-[11px] font-medium text-mist-300">
                Collects {slots.length > 0 && <span className="text-mist-400">({slots.length})</span>}
              </span>
              <button
                onClick={addSlot}
                className="flex items-center gap-1 text-[10.5px] text-signal-2 hover:underline"
              >
                <Icon.Plus className="size-2.5" /> field
              </button>
            </div>

            {slots.length === 0 ? (
              <p className="text-[10.5px] leading-relaxed text-mist-400">
                Nothing. Add a field to capture what the caller says as they take this exit.
              </p>
            ) : (
              <div className="space-y-1.5">
                {slots.map((key) => (
                  <div
                    key={key}
                    className="rounded-md border border-ink-700 bg-ink-900 p-1.5 transition-colors focus-within:border-ink-600"
                  >
                    {/* Explicit grid: flex + the inputs' own w-full fight over width. */}
                    <div className="grid grid-cols-[minmax(0,1fr)_94px_auto_auto] items-center gap-1.5">
                      <Input
                        defaultValue={key}
                        onBlur={(e) => renameSlot(key, e.target.value)}
                        onKeyDown={(e) =>
                          e.key === "Enter" && (e.target as HTMLInputElement).blur()
                        }
                        className="h-[26px] min-w-0 px-1.5 font-mono text-[11px]"
                      />
                      <Select
                        value={props[key]?.type ?? "string"}
                        onChange={(e) => setSlot(key, { type: e.target.value })}
                        className="h-[26px] min-w-0 px-1 text-[11px]"
                      >
                        {SLOT_TYPES.map((t) => (
                          <option key={t} value={t}>
                            {t}
                          </option>
                        ))}
                      </Select>
                      <button
                        onClick={() => toggleRequired(key)}
                        title={
                          required.includes(key)
                            ? "Required — the model must fill it to take this exit"
                            : "Optional"
                        }
                        className={cx(
                          "rounded border px-1.5 py-1 text-[9.5px] font-medium transition-colors",
                          required.includes(key)
                            ? "border-amber-500/30 bg-amber-500/10 text-amber-300"
                            : "border-ink-700 text-mist-400 hover:text-mist-300"
                        )}
                      >
                        req
                      </button>
                      <button
                        onClick={() => removeSlot(key)}
                        title="Remove field"
                        className="px-0.5 text-mist-400 transition-colors hover:text-rose-300"
                      >
                        <Icon.X className="size-3" />
                      </button>
                    </div>
                    <Input
                      value={props[key]?.description ?? ""}
                      onChange={(e) => setSlot(key, { description: e.target.value })}
                      placeholder="what this is, in the model's words"
                      className="mt-1 h-6 border-transparent bg-transparent px-1.5 text-[10.5px] hover:border-ink-700"
                    />
                  </div>
                ))}
              </div>
            )}
          </div>

          <div className="flex items-center gap-2 border-t border-ink-800 pt-2">
            <Button size="xs" variant="ghost" onClick={onGo}>
              Open {edge.target}
            </Button>
            <Button size="xs" variant="danger" onClick={onRemove} className="ml-auto">
              Remove exit
            </Button>
          </div>
        </div>
      </Collapse>
    </div>
  );
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-center gap-2">
      <span className="w-[62px] shrink-0 text-[11px] text-mist-400">{label}</span>
      <div className="min-w-0 flex-1">{children}</div>
    </div>
  );
}
