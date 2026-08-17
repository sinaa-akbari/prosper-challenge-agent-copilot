import dagre from "dagre";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Background,
  BackgroundVariant,
  Controls,
  Handle,
  MarkerType,
  Position,
  ReactFlow,
  ReactFlowProvider,
  useNodesState,
  useReactFlow,
  type Edge as RFEdge,
  type Node as RFNode,
} from "@xyflow/react";
import type { AgentConfig, LintIssue } from "../types";
import { Badge, Icon, cx } from "./ui";

const NODE_W = 236;
const NODE_H = 102;

type DiffKind = "added" | "removed" | "changed";

interface NodeData {
  label: string;
  instructions: string;
  isStart: boolean;
  isEnd: boolean;
  edgeCount: number;
  diff?: DiffKind;
  live?: boolean;
  visited?: boolean;
  hasError?: boolean;
  hasWarning?: boolean;
  selected?: boolean;
  [key: string]: unknown;
}

/* ----------------------------------------------------------- node card --- */
/* Drawn as a module on a patch bay: a coloured spine for its role in the call,
   a mono name plate, the body, and a meta strip. Squared off, not a soft card. */
function FlowNode({ data }: { data: NodeData }) {
  const spine = data.isStart
    ? "bg-signal"
    : data.isEnd
      ? "bg-mist-400/50"
      : data.visited
        ? "bg-signal/45"
        : "bg-ink-600";

  const shell = data.diff
    ? {
        added: "border-emerald-500/70 bg-emerald-500/[0.04]",
        removed: "border-dashed border-rose-500/50 opacity-50",
        changed: "border-amber-500/70 bg-amber-500/[0.04]",
      }[data.diff]
    : data.selected
      ? "border-signal"
      : data.hasError
        ? "border-rose-600/70"
        : "border-ink-700 hover:border-ink-600";

  return (
    <div
      className={cx(
        "group relative flex w-[236px] overflow-hidden rounded-[5px] border bg-ink-900 transition-colors",
        shell,
        data.live && "pulse-live border-signal"
      )}
    >
      <Handle type="target" position={Position.Top} />
      <span className={cx("w-[3px] shrink-0", spine)} />

      <div className="min-w-0 flex-1">
        {/* name plate */}
        <div className="flex items-center gap-1.5 border-b border-ink-800 px-2.5 py-1.5">
          <span className="truncate font-mono text-[12px] font-medium tracking-[-0.01em] text-mist-100">
            {data.label}
          </span>
          <span className="ml-auto flex shrink-0 items-center gap-1">
            {data.hasError ? (
              <Badge tone="rose">err</Badge>
            ) : data.hasWarning ? (
              <Badge tone="amber">chk</Badge>
            ) : null}
            {data.isStart && <Badge tone="teal">start</Badge>}
            {data.isEnd && <Badge tone="neutral">end</Badge>}
          </span>
        </div>

        {/* Body height is fixed rather than clamped: every node is then exactly
            NODE_H tall, which is what dagre was told when it placed them. */}
        <div className="h-[50px] overflow-hidden px-2.5 py-2">
          <p className="line-clamp-2 text-[11px] leading-[1.55] text-mist-400">
            {data.instructions || "No instructions."}
          </p>
        </div>

        {/* meta strip */}
        <div className="flex items-center gap-1.5 border-t border-ink-850 px-2.5 py-1">
          <span
            className={cx(
              "tnum font-mono text-[9.5px] tracking-[0.06em]",
              data.edgeCount === 0 && !data.isEnd ? "text-amber-400" : "text-mist-400/70"
            )}
          >
            {data.edgeCount === 0
              ? data.isEnd
                ? "TERMINAL"
                : "NO EXITS"
              : `${data.edgeCount} EXIT${data.edgeCount === 1 ? "" : "S"}`}
          </span>
          {data.live && (
            <span className="ml-auto flex items-center gap-1">
              <span className="size-1 animate-pulse rounded-full bg-signal" />
              <span className="font-mono text-[9px] tracking-[0.1em] text-signal">LIVE</span>
            </span>
          )}
        </div>
      </div>

      {data.diff && (
        <span className="absolute -top-[9px] left-2">
          <Badge
            tone={data.diff === "added" ? "green" : data.diff === "removed" ? "rose" : "amber"}
          >
            {data.diff}
          </Badge>
        </span>
      )}

      <Handle type="source" position={Position.Bottom} />
    </div>
  );
}

const nodeTypes = { flow: FlowNode };

/* -------------------------------------------------------------- layout --- */
type Pos = { x: number; y: number };

/** Auto-layout returns a position *map*, never nodes.
 *
 *  Keeping positions separate from node data is the whole trick behind free
 *  mode: node data is rebuilt from the agent config on every edit (a keystroke
 *  in the inspector, a lint result, a live-call highlight), and if positions
 *  were computed in that same pass, every rebuild would yank a dragged node
 *  back to its dagre coordinate. */
function autoLayout(ids: string[], edges: RFEdge[]): Record<string, Pos> {
  const g = new dagre.graphlib.Graph();
  g.setDefaultEdgeLabel(() => ({}));
  g.setGraph({ rankdir: "TB", nodesep: 48, ranksep: 76, marginx: 24, marginy: 24 });

  ids.forEach((id) => g.setNode(id, { width: NODE_W, height: NODE_H }));
  edges.forEach((e) => {
    // Self-loops and back-edges confuse dagre's ranking; skip them for layout only.
    if (e.source !== e.target) g.setEdge(e.source, e.target);
  });
  dagre.layout(g);

  const out: Record<string, Pos> = {};
  ids.forEach((id) => {
    const n = g.node(id);
    if (n) out[id] = { x: n.x - NODE_W / 2, y: n.y - NODE_H / 2 };
  });
  return out;
}

/* ------------------------------------------------------- persisted layout --- */
const FREE_KEY = "composer.freeLayout";
const posKey = (agent: string) => `composer.layout.${agent}`;

function loadPositions(agent: string): Record<string, Pos> {
  try {
    const raw = localStorage.getItem(posKey(agent));
    const parsed = raw ? JSON.parse(raw) : null;
    return parsed && typeof parsed === "object" ? parsed : {};
  } catch {
    return {};
  }
}

function savePositions(agent: string, positions: Record<string, Pos>) {
  try {
    localStorage.setItem(posKey(agent), JSON.stringify(positions));
  } catch {
    /* quota or private mode — layout just won't persist */
  }
}

/* -------------------------------------------------------------- canvas --- */
export interface GraphCanvasProps {
  config: AgentConfig;
  /** Scopes the saved hand-positioned layout. Usually the agent id. */
  layoutKey: string;
  /** When previewing a patch, the config before it — lets deleted nodes show as ghosts. */
  baseConfig?: AgentConfig | null;
  diff?: Record<string, DiffKind>;
  lint?: LintIssue[];
  selected?: string | null;
  liveNode?: string | null;
  visitedPath?: string[];
  onSelect?: (name: string | null) => void;
}

function Canvas({
  config,
  layoutKey,
  baseConfig,
  diff,
  lint = [],
  selected,
  liveNode,
  visitedPath = [],
  onSelect,
}: GraphCanvasProps) {
  const { baseNodes, edges } = useMemo(() => {
    const errorNodes = new Set(
      lint.filter((i) => i.severity === "error" && i.node).map((i) => i.node)
    );
    const warnNodes = new Set(
      lint.filter((i) => i.severity === "warning" && i.node).map((i) => i.node)
    );
    const visited = new Set(visitedPath);

    const present = new Map(config.nodes.map((n) => [n.name, n]));
    // Nodes the patch deletes still render, greyed out, so the diff is legible.
    const ghosts = (baseConfig?.nodes ?? []).filter((n) => !present.has(n.name));

    const rfNodes: RFNode[] = [
      ...config.nodes.map((n) => ({
        id: n.name,
        type: "flow",
        position: { x: 0, y: 0 },
        data: {
          label: n.name,
          instructions: (n.task_messages ?? []).map((m) => m.content).join(" "),
          isStart: n.name === config.initial_node,
          isEnd: !!n.end,
          edgeCount: (n.edges ?? []).length,
          diff: diff?.[n.name],
          live: liveNode === n.name,
          visited: visited.has(n.name),
          hasError: errorNodes.has(n.name),
          hasWarning: warnNodes.has(n.name),
          selected: selected === n.name,
        } satisfies NodeData,
      })),
      ...ghosts.map((n) => ({
        id: n.name,
        type: "flow",
        position: { x: 0, y: 0 },
        data: {
          label: n.name,
          instructions: (n.task_messages ?? []).map((m) => m.content).join(" "),
          isStart: false,
          isEnd: !!n.end,
          edgeCount: (n.edges ?? []).length,
          diff: "removed" as DiffKind,
        } satisfies NodeData,
      })),
    ];

    const ids = new Set(rfNodes.map((n) => n.id));
    const rfEdges: RFEdge[] = [];
    const source = [...config.nodes, ...ghosts];
    source.forEach((n) => {
      (n.edges ?? []).forEach((e) => {
        if (!ids.has(e.target)) return;
        const touched = diff?.[n.name] || diff?.[e.target];
        rfEdges.push({
          id: `${n.name}--${e.function}--${e.target}`,
          source: n.name,
          target: e.target,
          label: e.function,
          labelBgPadding: [5, 2] as [number, number],
          labelBgBorderRadius: 3,
          animated: liveNode === n.name,
          style: touched ? { stroke: "#f59e0b", strokeWidth: 1.8 } : undefined,
          markerEnd: { type: MarkerType.ArrowClosed, width: 14, height: 14, color: "#4b5666" },
        });
      });
    });

    return { baseNodes: rfNodes, edges: rfEdges };
  }, [config, baseConfig, diff, lint, selected, liveNode, visitedPath]);

  const autoPositions = useMemo(
    () => autoLayout(baseNodes.map((n) => n.id), edges),
    [baseNodes, edges]
  );

  /* ------------------------------------------------------ free layout -- */
  const [free, setFree] = useState(() => localStorage.getItem(FREE_KEY) !== "0");
  useEffect(() => localStorage.setItem(FREE_KEY, free ? "1" : "0"), [free]);

  // React Flow owns the node array so it can apply its own drag, selection and
  // measurement changes; we only re-seed it when the agent's data changes.
  const [nodes, setNodes, onNodesChange] = useNodesState<RFNode>([]);

  const savedRef = useRef<Record<string, Pos>>({});
  // Bumping this tells the sync effect to discard on-screen positions and
  // re-seed from storage (agent switch) or from auto-layout (Tidy).
  const [epoch, setEpoch] = useState(0);
  const seenEpoch = useRef(-1);
  const seenFree = useRef(free);

  useEffect(() => {
    savedRef.current = loadPositions(layoutKey);
    setEpoch((e) => e + 1);
  }, [layoutKey]);

  useEffect(() => {
    // Reseed on an agent switch, a Tidy, *or* a mode flip. The mode flip matters:
    // after a trip through auto layout the on-screen positions are the computed
    // ones, so without this, switching back to free would "restore" the auto
    // layout and quietly discard the arrangement the user made.
    const reseed = seenEpoch.current !== epoch || seenFree.current !== free;
    seenEpoch.current = epoch;
    seenFree.current = free;

    setNodes((prev) => {
      const onScreen = reseed
        ? new Map<string, Pos>()
        : new Map(prev.map((n) => [n.id, n.position]));

      return baseNodes.map((n) => ({
        ...n,
        // Precedence: where it already is → where you last dragged it → dagre.
        position:
          (free ? (onScreen.get(n.id) ?? savedRef.current[n.id]) : undefined) ??
          autoPositions[n.id] ?? { x: 0, y: 0 },
        draggable: free,
      }));
    });
  }, [baseNodes, autoPositions, free, epoch, setNodes]);

  const onNodeClick = useCallback(
    (_: unknown, node: RFNode) => onSelect?.(node.id),
    [onSelect]
  );

  // Keep the whole graph in frame when a side panel opens or the patch preview
  // adds nodes. Without this the canvas just gets clipped.
  const wrapper = useRef<HTMLDivElement>(null);
  const rf = useReactFlow();
  const refit = useCallback(
    () => rf.fitView({ padding: 0.2, maxZoom: 1.1, duration: 220 }),
    [rf]
  );

  const persist = useCallback(() => {
    const next: Record<string, Pos> = {};
    rf.getNodes().forEach((n) => (next[n.id] = n.position));
    savedRef.current = next;
    savePositions(layoutKey, next);
  }, [rf, layoutKey]);

  const tidy = useCallback(() => {
    savedRef.current = {};
    savePositions(layoutKey, {});
    setEpoch((e) => e + 1);
    window.setTimeout(refit, 80);
  }, [layoutKey, refit]);

  useEffect(() => {
    const el = wrapper.current;
    if (!el) return;
    let timer: number;
    const observer = new ResizeObserver(() => {
      window.clearTimeout(timer);
      timer = window.setTimeout(refit, 90);
    });
    observer.observe(el);
    return () => {
      observer.disconnect();
      window.clearTimeout(timer);
    };
  }, [refit]);

  const nodeCount = baseNodes.length;
  useEffect(() => {
    const timer = window.setTimeout(refit, 60);
    return () => window.clearTimeout(timer);
  }, [nodeCount, refit]);

  const arranged = Object.keys(savedRef.current).length > 0;
  const globals = config.global_edges ?? [];

  return (
    <div ref={wrapper} className="relative size-full">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        onNodesChange={onNodesChange}
        onNodeClick={onNodeClick}
        onNodeDragStop={persist}
        onPaneClick={() => onSelect?.(null)}
        fitView
        fitViewOptions={{ padding: 0.22, maxZoom: 1.1 }}
        minZoom={0.2}
        maxZoom={1.6}
        proOptions={{ hideAttribution: true }}
        nodesDraggable={free}
        nodesConnectable={false}
        elevateNodesOnSelect
        selectNodesOnDrag={false}
      >
        <Background
          variant={BackgroundVariant.Dots}
          gap={22}
          size={1}
          color={free ? "#2a2520" : "#1e1b18"}
        />
        <Controls showInteractive={false} position="bottom-right" />
      </ReactFlow>

      {/* layout mode */}
      <div className="absolute left-4 top-4 flex items-center gap-1 rounded-[5px] border border-ink-700 bg-ink-900/94 p-1 backdrop-blur-sm">
        <button
          onClick={() => setFree((v) => !v)}
          title={
            free
              ? "Free layout: drag nodes anywhere. Click to switch back to auto-layout."
              : "Auto layout: positions are computed. Click to arrange nodes yourself."
          }
          className={cx(
            "flex items-center gap-1.5 rounded-[3px] px-2 py-1 font-mono text-[9.5px] uppercase tracking-[0.1em] transition-colors",
            free
              ? "bg-signal/15 text-signal-2"
              : "text-mist-400 hover:bg-ink-850 hover:text-mist-300"
          )}
        >
          <Icon.Move className="size-3" />
          {free ? "Free" : "Auto"}
        </button>
        {free && (
          <>
            <span className="h-3.5 w-px bg-ink-700" />
            <button
              onClick={tidy}
              disabled={!arranged}
              title={
                arranged
                  ? "Re-run auto layout and forget your arrangement"
                  : "Nothing to tidy — nodes are still auto-placed"
              }
              className="rounded-[3px] px-2 py-1 font-mono text-[9.5px] uppercase tracking-[0.1em] text-mist-400 transition-colors hover:bg-ink-850 hover:text-mist-300 disabled:pointer-events-none disabled:opacity-35"
            >
              Tidy
            </button>
          </>
        )}
      </div>

      {globals.length > 0 && (
        <div className="pointer-events-none absolute right-4 top-4 w-[248px] overflow-hidden rounded-[5px] border border-ink-700 bg-ink-900/94 backdrop-blur-sm">
          <div className="flex items-center gap-1.5 border-b border-ink-800 bg-violet-500/[0.06] px-2.5 py-1.5">
            <span className="size-1 rounded-full bg-violet-400" />
            <span className="font-mono text-[9.5px] uppercase tracking-[0.12em] text-violet-300">
              Any node
            </span>
            <span className="tnum ml-auto font-mono text-[9.5px] text-mist-400">
              {globals.length}
            </span>
          </div>
          <div className="divide-y divide-ink-850">
            {globals.map((e) => (
              <div key={e.function} className="flex items-center gap-1.5 px-2.5 py-1.5 text-[10.5px]">
                <span className="truncate font-mono text-violet-300">{e.function}</span>
                <span className="shrink-0 text-mist-400/60">→</span>
                <span className="truncate font-mono text-mist-400">{e.target}</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

export function GraphCanvas(props: GraphCanvasProps) {
  return (
    <ReactFlowProvider>
      <Canvas {...props} />
    </ReactFlowProvider>
  );
}
