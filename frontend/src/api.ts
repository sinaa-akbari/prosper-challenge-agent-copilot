import type {
  AgentConfig,
  AuthStatus,
  AgentRecord,
  AgentSummary,
  Call,
  Issue,
  Job,
  CalendarOption,
  CalendarStatus,
  LintIssue,
  PhoneStatus,
  LiveCall,
  Proposal,
  TestCase,
  TestRun,
  VersionEntry,
} from "./types";

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api${path}`, {
    headers: { "Content-Type": "application/json" },
    // The session lives in an HttpOnly cookie, so every call has to carry it.
    credentials: "same-origin",
    ...init,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      detail = (await res.json()).detail ?? detail;
    } catch {
      /* keep statusText */
    }
    throw new Error(detail);
  }
  return res.json();
}

const post = <T,>(path: string, body?: unknown) =>
  req<T>(path, { method: "POST", body: JSON.stringify(body ?? {}) });

/**
 * Model calls take 10-60s, so the server hands back a job id. Poll it, forwarding
 * progress so the UI can fill in results as they land rather than freezing.
 */
export async function awaitJob<T>(
  jobId: string,
  onProgress?: (job: Job<T>) => void,
  intervalMs = 700
): Promise<T> {
  for (;;) {
    const job = await req<Job<T>>(`/jobs/${jobId}`);
    onProgress?.(job);
    if (job.status === "done") return job.result as T;
    if (job.status === "error") throw new Error(job.error);
    await new Promise((r) => setTimeout(r, intervalMs));
  }
}

export const api = {
  // auth
  authStatus: () => req<AuthStatus>("/auth/status"),
  requestCode: (phone: string) => post<{ sent: boolean }>("/auth/request-code", { phone }),
  verifyCode: (phone: string, code: string) =>
    post<{ signed_in: boolean; user: string }>("/auth/verify", { phone, code }),
  passwordLogin: (password: string) =>
    post<{ signed_in: boolean; user: string }>("/auth/password", { password }),
  logout: () => post<{ signed_in: boolean }>("/auth/logout", {}),

  health: () => req<{ ok: boolean; keys: Record<string, boolean> }>("/health"),

  // agents
  listAgents: () =>
    req<{ agents: AgentSummary[]; active_agent_id: string }>("/agents"),
  activateAgent: (id: string) =>
    post<{ active: any; deactivated: string }>(`/agents/${id}/activate`, {}),
  deactivateAgent: (id: string) => post<{ active: any }>(`/agents/${id}/deactivate`, {}),
  renameAgent: (id: string, name: string) =>
    post<{ agent: AgentRecord }>(`/agents/${id}/rename`, { name }),
  createAgent: (body: { name?: string; config?: AgentConfig }) =>
    post<{ agent: AgentRecord }>("/agents", body),
  getAgent: (id: string) =>
    req<{ agent: AgentRecord; lint: LintIssue[] }>(`/agents/${id}`),
  saveAgent: (id: string, config: AgentConfig, label = "Edited") =>
    req<{ agent: AgentRecord; lint: LintIssue[] }>(`/agents/${id}`, {
      method: "PUT",
      body: JSON.stringify({ config, label }),
    }),
  deleteAgent: (id: string) => req(`/agents/${id}`, { method: "DELETE" }),
  lint: (config: AgentConfig) => post<{ lint: LintIssue[] }>("/lint", { config }),
  versions: (id: string) => req<{ versions: VersionEntry[] }>(`/agents/${id}/versions`),
  revert: (id: string, version: number) =>
    post<{ agent: AgentRecord }>(`/agents/${id}/revert`, { version }),

  // copilot
  askCopilot: (
    id: string,
    body: { message: string; history: any[]; issue_id?: string; include_failures?: boolean }
  ) => post<{ job_id: string }>(`/agents/${id}/copilot`, body),
  applyPatch: (
    id: string,
    body: {
      ops: any[];
      label?: string;
      tests?: any[];
      retire_tests?: any[];
      issue_id?: string;
      reply?: string;
    }
  ) =>
    post<{
      agent: AgentRecord | null;
      tests_added: TestCase[];
      tests_retired: TestCase[];
    }>(`/agents/${id}/copilot/apply`, body),

  // tests
  getTests: (id: string) =>
    req<{ cases: TestCase[]; last_run: TestRun | null }>(`/agents/${id}/tests`),
  addTest: (id: string, body: any) => post<{ case: TestCase }>(`/agents/${id}/tests`, body),
  deleteTest: (id: string, caseId: string) =>
    req(`/agents/${id}/tests/${caseId}`, { method: "DELETE" }),
  generateTests: (id: string, count = 6) =>
    post<{ job_id: string }>(`/agents/${id}/tests/generate`, { count }),
  runTests: (
    id: string,
    body: {
      config?: AgentConfig | null;
      case_ids?: string[];
      retire_ids?: string[];
      extra_cases?: any[];
    } = {}
  ) =>
    post<{ job_id: string }>(`/agents/${id}/tests/run`, body),

  // calendar
  calendarStatus: () => req<CalendarStatus>("/calendar"),
  calendarConnect: () => post<{ url: string }>("/calendar/connect", {}),
  calendarList: () => req<{ calendars: CalendarOption[] }>("/calendar/calendars"),
  calendarDisconnect: () => post<{ connected: boolean }>("/calendar/disconnect", {}),

  // phone
  phoneStatus: () => req<PhoneStatus>("/phone"),
  claimPhone: (agentId: string) => post<any>("/phone/claim", { agent_id: agentId }),
  releasePhone: () => post<{ released: boolean }>("/phone/release", {}),

  // calls + issues
  getCalls: (id: string) => req<{ calls: Call[] }>(`/agents/${id}/calls`),
  /** Every call in the workspace, whichever agent took it. */
  getWorkspaceCalls: () => req<{ calls: Call[] }>("/calls"),
  replayCall: (id: string, callId: string, save = false) =>
    post<{ job_id: string }>(`/agents/${id}/calls/${callId}/replay`, { save }),
  twilioNumbers: () =>
    req<{
      configured: boolean;
      numbers: { sid: string; phone_number: string; voice_url: string }[];
      public_base_url: string;
      signature_validation?: boolean;
      error?: string;
    }>("/twilio/numbers"),
  getIssues: (id: string) =>
    req<{ issues: Issue[]; call_count: number }>(`/agents/${id}/issues`),
  analyzeCalls: (id: string) => post<{ job_id: string }>(`/agents/${id}/issues/analyze`),
  setIssueStatus: (id: string, issueId: string, status: string) =>
    post<{ issues: Issue[] }>(`/agents/${id}/issues/${issueId}/status`, { status }),

  // live test call
  liveCall: (sessionId: string) => req<LiveCall>(`/calls/live/${sessionId}`),
};

export type { Proposal };
