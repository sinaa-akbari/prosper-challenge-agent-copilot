import { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "./api";
import { CallPanel } from "./components/CallPanel";
import { AgentMenu } from "./components/AgentMenu";
import { HistoryPanel } from "./components/HistoryPanel";
import { PhonePanel } from "./components/PhonePanel";
import { SignIn } from "./components/SignIn";
import { CopilotPanel } from "./components/CopilotPanel";
import { GraphCanvas } from "./components/GraphCanvas";
import { IssuesPanel } from "./components/IssuesPanel";
import { NodeInspector } from "./components/NodeInspector";
import { TestsPanel } from "./components/TestsPanel";
import { VersionsPanel } from "./components/VersionsPanel";
import { Badge, Button, Icon, Spinner, cx } from "./components/ui";
import type {
  AgentConfig,
  AuthStatus,
  AgentRecord,
  AgentSummary,
  Issue,
  LintIssue,
  Proposal,
  TestRun,
} from "./types";

type Tab = "build" | "tests" | "history" | "phone" | "issues" | "versions";

function SpecRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-baseline gap-2">
      <span className="w-[42px] shrink-0 font-mono text-[9.5px] uppercase tracking-[0.1em] text-mist-400/70">
        {label}
      </span>
      <span className="tnum truncate font-mono text-[10.5px] text-mist-300" title={value}>
        {value}
      </span>
    </div>
  );
}

const TABS: { id: Tab; label: string; icon: (p: any) => JSX.Element }[] = [
  { id: "build", label: "Build", icon: Icon.Graph },
  { id: "tests", label: "Tests", icon: Icon.Flask },
  { id: "history", label: "History", icon: Icon.History },
  { id: "phone", label: "Connect", icon: Icon.Phone },
  { id: "issues", label: "Issues", icon: Icon.Inbox },
  { id: "versions", label: "Versions", icon: Icon.Clock },
];

export default function App() {
  // Auth is resolved before anything else loads. Rendering the builder and
  // letting its first API call 401 would flash the whole workspace at someone
  // who isn't allowed to see it.
  const [session, setSession] = useState<AuthStatus | null>(null);
  const [agents, setAgents] = useState<AgentSummary[]>([]);
  const [agentId, setAgentId] = useState<string | null>(null);
  const [record, setRecord] = useState<AgentRecord | null>(null);
  const [draft, setDraft] = useState<AgentConfig | null>(null);
  const [lint, setLint] = useState<LintIssue[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);

  const [tab, setTab] = useState<Tab>("build");
  const [selectedNode, setSelectedNode] = useState<string | null>(null);
  const [proposal, setProposal] = useState<Proposal | null>(null);
  const [pendingIssue, setPendingIssue] = useState<Issue | null>(null);
  const [pendingDiagnose, setPendingDiagnose] = useState(false);
  // Bumped when a call is replayed into a test, so the suite re-reads itself.
  const [testsEpoch, setTestsEpoch] = useState(0);
  const [activeId, setActiveId] = useState("");
  const [agentBusy, setAgentBusy] = useState("");
  const reloadTests = () => setTestsEpoch((e) => e + 1);
  const [lastRun, setLastRun] = useState<TestRun | null>(null);
  const [callOpen, setCallOpen] = useState(false);
  const [activeNode, setActiveNode] = useState<string | null>(null);
  const [visitedPath, setVisitedPath] = useState<string[]>([]);

  const handleNodeUpdate = useCallback((node: string | null, path: string[]) => {
    setActiveNode(node);
    setVisitedPath(path);
  }, []);

  /* ------------------------------------------------------------ loading -- */
  useEffect(() => {
    api
      .authStatus()
      .then(setSession)
      // If the status endpoint itself is unreachable there's nothing useful to
      // gate on; fall through to the builder and let its own errors show.
      .catch(() => setSession({ enabled: false, phone: false, password: false, signed_in: true }));
  }, []);

  useEffect(() => {
    if (!session || (session.enabled && !session.signed_in)) return;
    (async () => {
      const { agents, active_agent_id } = await api.listAgents();
      setAgents(agents);
      setActiveId(active_agent_id || "");
      // Come back to whatever you were last working on. Failing that, prefer a
      // real agent over an empty scaffold someone spun up and abandoned.
      const remembered = localStorage.getItem("composer.agent");
      const pick =
        agents.find((a) => a.id === remembered)?.id ??
        agents.find((a) => a.node_count > 1)?.id ??
        agents[0]?.id ??
        null;
      setAgentId((current) => current ?? pick);
      setLoading(false);
    })();
  }, [session]);

  useEffect(() => {
    if (agentId) localStorage.setItem("composer.agent", agentId);
  }, [agentId]);

  const loadAgent = useCallback(async (id: string) => {
    const [{ agent, lint }, tests] = await Promise.all([api.getAgent(id), api.getTests(id)]);
    setRecord(agent);
    setDraft(agent.config);
    setLint(lint);
    setLastRun(tests.last_run);
  }, []);

  useEffect(() => {
    if (agentId) {
      setSelectedNode(null);
      setProposal(null);
      loadAgent(agentId);
    }
  }, [agentId, loadAgent]);

  /* ------------------------------------------------------------- editing -- */
  const dirty = useMemo(
    () => !!record && !!draft && JSON.stringify(record.config) !== JSON.stringify(draft),
    [record, draft]
  );

  // Re-lint local edits so the graph shows problems before they're saved.
  useEffect(() => {
    if (!draft || !dirty) return;
    const t = setTimeout(async () => {
      try {
        setLint((await api.lint(draft)).lint);
      } catch {
        /* transient */
      }
    }, 350);
    return () => clearTimeout(t);
  }, [draft, dirty]);

  async function save() {
    if (!agentId || !draft) return;
    setSaving(true);
    try {
      const { agent, lint } = await api.saveAgent(agentId, draft);
      setRecord(agent);
      setDraft(agent.config);
      setLint(lint);
      refreshAgentList();
    } finally {
      setSaving(false);
    }
  }

  async function refreshAgentList() {
    const { agents, active_agent_id } = await api.listAgents();
    setAgents(agents);
    setActiveId(active_agent_id || "");
  }

  async function createAgent() {
    const { agent } = await api.createAgent({ name: "Untitled agent" });
    await refreshAgentList();
    setAgentId(agent.id);
    setTab("build");
  }

  async function afterCopilotApply() {
    if (!agentId) return;
    await loadAgent(agentId);
    await refreshAgentList();
  }

  async function removeAgent(id: string) {
    await api.deleteAgent(id);
    const { agents, active_agent_id } = await api.listAgents();
    setAgents(agents);
    setActiveId(active_agent_id || "");
    if (id === agentId) {
      localStorage.removeItem("composer.agent");
      setAgentId(agents[0]?.id ?? null);
    }
  }

  // Only one agent is live at a time, so activating is a swap rather than a
  // toggle — refresh the whole list so the badge moves in one step.
  async function setLive(id: string, live: boolean) {
    setAgentBusy(id);
    try {
      if (live) await api.activateAgent(id);
      else await api.deactivateAgent(id);
      const { agents, active_agent_id } = await api.listAgents();
      setAgents(agents);
      setActiveId(active_agent_id || "");
    } catch (e: any) {
      alert(e.message);
    } finally {
      setAgentBusy("");
    }
  }

  async function renameAgent(id: string, name: string) {
    await api.renameAgent(id, name);
    await refreshAgentList();
    if (id === agentId) await loadAgent(id);
  }

  /* -------------------------------------------------------------- render -- */
  if (!session) {
    return (
      <div className="flex h-full items-center justify-center">
        <Spinner className="size-6 text-mist-400" />
      </div>
    );
  }

  if (session.enabled && !session.signed_in) {
    return <SignIn status={session} onSignedIn={() => window.location.reload()} />;
  }

  if (loading) {
    return (
      <div className="flex h-full items-center justify-center">
        <Spinner className="size-6 text-mist-400" />
      </div>
    );
  }

  if (!agentId || !record || !draft) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-4">
        <div className="text-[15px] font-medium">No agents yet</div>
        <Button variant="primary" onClick={createAgent}>
          Create one
        </Button>
      </div>
    );
  }

  const errors = lint.filter((l) => l.severity === "error");
  const warnings = lint.filter((l) => l.severity === "warning");
  const previewing = !!proposal?.config;
  const shownConfig = previewing ? proposal!.config! : draft;

  return (
    <div className="flex h-full flex-col">
      {/* ------------------------------------------------------- header -- */}
      <header className="flex h-[52px] shrink-0 items-stretch border-b border-ink-800 bg-ink-900">
        {/* brand block, aligned to the rail below it */}
        <div className="flex w-[190px] shrink-0 items-center gap-2.5 border-r border-ink-800 px-4">
          <span className="grid size-[22px] shrink-0 place-items-center rounded-[3px] bg-signal font-mono text-[11px] font-bold text-ink-950">
            P
          </span>
          <span className="text-[12.5px] font-semibold leading-none tracking-[-0.01em]">
            Agent Composer
          </span>
        </div>

        {/* agent identity + status */}
        <div className="flex min-w-0 flex-1 items-center gap-2.5 px-4">
          <AgentMenu
            agents={agents}
            agentId={agentId}
            activeId={activeId}
            busy={agentBusy}
            onSelect={setAgentId}
            onCreate={createAgent}
            onActivate={(id) => setLive(id, true)}
            onDeactivate={(id) => setLive(id, false)}
            onRename={renameAgent}
            onDelete={removeAgent}
          />

          <span className="tnum font-mono text-[10.5px] text-mist-400">v{record.version}</span>

          <span className="h-3.5 w-px bg-ink-700" />

          {errors.length > 0 ? (
            <Badge tone="rose" title={errors.map((e) => e.message).join("\n")}>
              <Icon.Warn className="size-2.5" /> {errors.length} error
              {errors.length === 1 ? "" : "s"}
            </Badge>
          ) : warnings.length > 0 ? (
            <Badge tone="amber" title={warnings.map((w) => w.message).join("\n")}>
              {warnings.length} warning{warnings.length === 1 ? "" : "s"}
            </Badge>
          ) : (
            <Badge tone="green">
              <Icon.Check className="size-2.5" /> valid
            </Badge>
          )}

          {lastRun && (
            <Badge
              tone={lastRun.failed === 0 ? "green" : "rose"}
              title="Result of the last full test run"
            >
              {lastRun.passed}/{lastRun.total} tests
            </Badge>
          )}

          {activeNode && (
            <span className="flex items-center gap-1.5 rounded-[3px] bg-signal/12 px-1.5 py-[3px]">
              <span className="size-1.5 animate-pulse rounded-full bg-signal" />
              <span className="font-mono text-[9.5px] uppercase tracking-[0.1em] text-signal-2">
                on air · {activeNode}
              </span>
            </span>
          )}

          <div className="ml-auto flex shrink-0 items-center gap-2">
            {dirty && (
              <>
                <span className="font-mono text-[10px] uppercase tracking-[0.1em] text-amber-300">
                  unsaved
                </span>
                <Button size="sm" variant="ghost" onClick={() => setDraft(record.config)}>
                  Discard
                </Button>
                <Button size="sm" variant="primary" onClick={save} loading={saving}>
                  Save
                </Button>
              </>
            )}
            <Button
              size="sm"
              variant={callOpen ? "subtle" : "outline"}
              onClick={() => setCallOpen((v) => !v)}
              disabled={errors.length > 0}
              title={
                errors.length
                  ? "Fix the errors first"
                  : "Call this agent, or chat with it if you have no mic"
              }
            >
              <Icon.Phone className="size-3.5" /> Test agent
            </Button>

            {/* Who you are, and the way out. Worth showing plainly on a shared
                deployment — "whose workspace am I looking at?" should never be
                a question you have to answer from the data. */}
            {session.enabled && session.signed_in && (
              <div className="flex items-center gap-2 border-l border-ink-800 pl-3">
                <span className="font-mono text-[10.5px] text-mist-400" title={session.user ?? ""}>
                  {session.user}
                </span>
                <button
                  onClick={async () => {
                    await api.logout();
                    window.location.reload();
                  }}
                  className="text-[10.5px] text-mist-400 underline-offset-2 hover:text-mist-200 hover:underline"
                >
                  Sign out
                </button>
              </div>
            )}
          </div>
        </div>
      </header>

      {/* --------------------------------------------------------- body -- */}
      <div className="flex min-h-0 flex-1">
        {/* rail */}
        <nav className="flex w-[190px] shrink-0 flex-col border-r border-ink-800 bg-ink-900">
          <div className="px-4 pb-1.5 pt-4">
            <span className="eyebrow">Workspace</span>
          </div>
          <div className="px-2">
            {TABS.map(({ id, label, icon: TabIcon }) => {
              const active = tab === id;
              return (
                <button
                  key={id}
                  onClick={() => setTab(id)}
                  className={cx(
                    "group relative flex w-full items-center gap-2.5 rounded-[4px] px-2.5 py-[7px] text-[12.5px] transition-colors",
                    active
                      ? "bg-ink-800 text-mist-100"
                      : "text-mist-400 hover:bg-ink-850 hover:text-mist-300"
                  )}
                >
                  <span
                    className={cx(
                      "absolute left-0 top-1/2 h-3.5 w-[2px] -translate-y-1/2 rounded-r transition-colors",
                      active ? "bg-signal" : "bg-transparent"
                    )}
                  />
                  <TabIcon className={cx("size-4", active ? "text-signal-2" : "")} />
                  {label}
                  {id === "tests" && lastRun && lastRun.failed > 0 && (
                    <span className="tnum ml-auto font-mono text-[9.5px] text-rose-300">
                      {lastRun.failed}
                    </span>
                  )}
                </button>
              );
            })}
          </div>

          {/* spec strip — the facts you keep glancing at while editing */}
          <div className="mt-auto space-y-2 border-t border-ink-800 px-4 py-3">
            <SpecRow label="Model" value={record.config.model} />
            <SpecRow label="Nodes" value={String(draft.nodes.length)} />
            <SpecRow
              label="Global"
              value={String((draft.global_edges ?? []).length)}
            />
            <SpecRow label="Entry" value={draft.initial_node} />
          </div>
        </nav>

        {/* main */}
        <main className="relative flex min-w-0 flex-1">
          {tab === "build" && (
            <>
              <div className="relative min-w-0 flex-1">
                {previewing && (
                  <div className="absolute inset-x-0 top-0 z-10">
                    <div className="flex items-center justify-center gap-2 border-b border-amber-600/30 bg-amber-950/60 py-1.5 backdrop-blur-sm">
                      <Icon.Sparkle className="size-3 text-amber-300" />
                      <span className="font-mono text-[10px] uppercase tracking-[0.12em] text-amber-200">
                        Proposed · not applied
                      </span>
                      <button
                        onClick={() => setProposal(null)}
                        className="ml-1 text-amber-300/60 transition-colors hover:text-amber-200"
                      >
                        <Icon.X className="size-3" />
                      </button>
                    </div>
                  </div>
                )}
                <GraphCanvas
                  config={shownConfig}
                  layoutKey={agentId}
                  baseConfig={previewing ? draft : null}
                  diff={proposal?.affected}
                  lint={lint}
                  selected={selectedNode}
                  liveNode={activeNode}
                  visitedPath={visitedPath}
                  onSelect={setSelectedNode}
                />
                {callOpen && (
                  <div className="pointer-events-none absolute bottom-4 left-4 z-10">
                    <CallPanel
                      agentId={agentId}
                      onClose={() => {
                        setCallOpen(false);
                        handleNodeUpdate(null, []);
                      }}
                      onNodeUpdate={handleNodeUpdate}
                    />
                  </div>
                )}
              </div>

              {selectedNode && !previewing && (
                <NodeInspector
                  config={draft}
                  nodeName={selectedNode}
                  lint={lint}
                  onChange={setDraft}
                  onClose={() => setSelectedNode(null)}
                  onSelect={setSelectedNode}
                />
              )}
            </>
          )}

          {tab === "tests" && (
            <div className="min-w-0 flex-1">
              <TestsPanel
                key={testsEpoch}
                agentId={agentId}
                config={draft}
                onRunComplete={setLastRun}
                onDiagnose={() => {
                  setPendingDiagnose(true);
                  setTab("build");
                }}
              />
            </div>
          )}

          {tab === "history" && (
            <div className="min-w-0 flex-1">
              <HistoryPanel agentId={agentId} onTestsChanged={reloadTests} />
            </div>
          )}

          {tab === "phone" && (
            <div className="min-w-0 flex-1">
              <PhonePanel
                agentId={agentId}
                agents={agents}
                transferTo={
                  shownConfig.nodes.find((n) => n.transfer_to)?.transfer_to ?? null
                }
              />
            </div>
          )}

          {tab === "issues" && (
            <div className="min-w-0 flex-1">
              <IssuesPanel
                agentId={agentId}
                onFix={(issue) => {
                  setPendingIssue(issue);
                  setTab("build");
                }}
              />
            </div>
          )}

          {tab === "versions" && (
            <div className="min-w-0 flex-1">
              <VersionsPanel
                agentId={agentId}
                currentVersion={record.version}
                onReverted={afterCopilotApply}
              />
            </div>
          )}
        </main>

        {/* copilot */}
        <aside className="w-[430px] shrink-0 border-l border-ink-800">
          <CopilotPanel
            agentId={agentId}
            config={draft}
            hasFailingTests={!!lastRun && lastRun.failed > 0}
            pendingIssue={pendingIssue}
            onIssueConsumed={() => setPendingIssue(null)}
            pendingDiagnose={pendingDiagnose}
            onDiagnoseConsumed={() => setPendingDiagnose(false)}
            onPreview={setProposal}
            onApplied={afterCopilotApply}
          />
        </aside>
      </div>
    </div>
  );
}
