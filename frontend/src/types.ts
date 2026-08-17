export interface Edge {
  function: string;
  description: string;
  target: string;
  properties?: Record<string, any>;
  required?: string[];
}

export interface Node {
  name: string;
  /** E.164 number to forward the caller to when this node is reached. */
  transfer_to?: string | null;
  task_messages: { role: string; content: string }[];
  role_message?: string;
  edges?: Edge[];
  pre_actions?: any[];
  post_actions?: any[];
  end?: boolean;
}

export interface AgentConfig {
  name: string;
  voice_id: string;
  model: string;
  persona: string;
  initial_node: string;
  nodes: Node[];
  global_edges?: Edge[];
}

export interface AgentRecord {
  id: string;
  config: AgentConfig;
  version: number;
  updated_at: number;
  label: string;
  source: string;
  ops: any[];
}

export interface AgentSummary {
  id: string;
  name: string;
  version: number;
  updated_at: number;
  node_count: number;
  /** Live on the phone. Exactly one agent is, deployment-wide. */
  active?: boolean;
}

export interface LintIssue {
  severity: "error" | "warning";
  message: string;
  node: string;
  function: string;
}

export interface DiffEntry {
  op: string;
  summary: string;
  detail: any;
}

export interface Proposal {
  reply: string;
  ops: any[];
  diff: DiffEntry[];
  affected: Record<string, "added" | "removed" | "changed">;
  config: AgentConfig | null;
  lint: LintIssue[];
  tests: ProposedTest[];
  /** Existing tests the Copilot judged to be wrong, not merely failing. */
  retire_tests?: RetiredTest[];
  /** Its diagnosis: one per failing test, each with cited evidence. */
  findings?: Finding[];
  /** Result of running the proposal before it was shown. */
  verification?: Verification | null;
  /** Problems this change introduced, as opposed to ones it inherited. */
  new_lint?: LintIssue[];
  error: string;
}

export interface RetiredTest {
  case_id: string;
  name: string;
  reason: string;
}

export type RootCause =
  | "required_field_blocks_exit"
  | "node_passed_through"
  | "missing_path"
  | "edge_description_mismatch"
  | "node_overloaded"
  | "conflicting_instructions"
  | "broken_test";

export interface Finding {
  case_id: string;
  case_name?: string;
  root_cause: RootCause;
  evidence: string;
  fix: string;
}

export interface Verification {
  fixed?: string[];
  still_failing?: string[];
  broke?: string[];
  retired?: string[];
  passed?: number;
  total?: number;
  error?: string;
}

export interface ProposedTest {
  name: string;
  persona: Persona;
  assertions: string[];
}

export interface Persona {
  description: string;
  goal: string;
  facts: Record<string, string>;
  style: string;
}

export interface TestCase {
  id: string;
  name: string;
  persona: Persona;
  assertions: string[];
  max_turns: number;
  origin: "manual" | "generated" | "regression";
  source_issue: string;
  source_call: string;
}

export interface AssertionResult {
  assertion: string;
  passed: boolean;
  reason: string;
  evidence: string;
}

export interface SimTurn {
  speaker: "agent" | "caller" | "transition";
  text: string;
  node: string;
  function: string;
  target: string;
  args: Record<string, any>;
}

export interface CaseResult {
  case_id: string;
  name: string;
  origin: string;
  source_issue: string;
  passed: boolean;
  duration_s: number;
  verdict: {
    passed: boolean;
    results: AssertionResult[];
    summary: string;
    error: string;
  };
  simulation: {
    turns?: SimTurn[];
    path?: string[];
    transcript?: string;
    end_reason?: string;
    collected?: Record<string, any>;
  };
}

export interface TestRun {
  agent_id: string;
  started_at: number;
  duration_s: number;
  total: number;
  passed: number;
  failed: number;
  results: CaseResult[];
}

export interface Issue {
  id: string;
  title: string;
  description: string;
  category: string;
  severity: "critical" | "high" | "medium" | "low";
  call_ids: string[];
  call_count: number;
  affected_nodes: string[];
  evidence: { call_id: string; quote: string }[];
  suggested_fix: string;
  status: "open" | "fixed" | "dismissed";
  found_at: number;
}

export interface Call {
  id: string;
  outcome: string;
  duration_s: number;
  flagged_by?: string;
  turns: { speaker: string; text: string }[];
  /** seed | webrtc | twilio — where this call came from. */
  source: string;
  path?: string[];
  collected?: Record<string, string>;
  from_number?: string;
  to_number?: string;
  provider_sid?: string;
  created_at?: number;
  agent_version?: number | null;
  /** Which agent took the call — history spans the whole workspace. */
  agent_id?: string;
  agent_name?: string;
  metadata?: {
    error?: string;
    warning?: string;
    audio_peak?: number;
    /** Set when the call was handed to a person. */
    transferred_to?: string;
    transfer_failed?: string;
  };
}

export interface VersionEntry {
  version: number;
  created_at: number;
  label: string;
  source: string;
  ops: any[];
  node_count: number;
}

export interface LiveCall {
  session_id: string;
  agent_id: string;
  status: "connecting" | "live" | "ended" | "error";
  started_at: number;
  current_node: string;
  path: string[];
  turns: { speaker: string; text: string; at: number }[];
  collected: Record<string, any>;
  error: string;
  /** Non-fatal, e.g. a voice the ElevenLabs plan can't synthesise. */
  warning?: string;
  events?: TraceEvent[];
  audio?: CallAudio;
}

export interface TraceEvent {
  at: number;
  ms: number;
  kind: string;
  detail: string;
  level: "info" | "warning" | "error";
}

export interface CallAudio {
  frames: number;
  kb: number;
  peak: number;
  /** Words were produced but every audio chunk was silent. */
  silent: boolean;
}

export interface Job<T = any> {
  id: string;
  kind: string;
  status: "running" | "done" | "error";
  progress: { done: number; total: number };
  partial?: { case_id: string; name: string; passed: boolean }[];
  /** What the Copilot is doing right now — set as its loop advances. */
  status_text?: string;
  result: T | null;
  error: string;
}

export type ChatMessage = {
  role: "user" | "assistant";
  content: string;
  proposal?: Proposal;
  applied?: boolean;
  issueId?: string;
};

export interface AuthStatus {
  enabled: boolean;
  /** Text-message sign-in is available. */
  phone: boolean;
  /** New numbers may create an account. */
  signup?: boolean;
  /** The break-glass password is set. */
  password: boolean;
  signed_in: boolean;
  user?: string | null;
}

export interface PhoneStatus {
  number: string;
  configured: boolean;
  agent_id: string;
  claimed_by: string;
  /** True when this account holds the claim. */
  mine: boolean;
  /** Another workspace owns the live agent, so calls land there, not here. */
  elsewhere?: boolean;
}

export interface CalendarStatus {
  connected: boolean;
  /** Google credentials are configured on this deployment. */
  available?: boolean;
  email?: string;
  connected_by?: string;
  expires_at?: string | null;
}

export interface CalendarOption {
  id: string;
  name: string;
  primary: boolean;
  timezone: string;
}
