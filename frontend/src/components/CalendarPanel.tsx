import { useEffect, useState } from "react";
import { api } from "../api";
import type { CalendarOption, CalendarStatus } from "../types";
import { Badge, Button, Icon, Spinner } from "./ui";

/**
 * Connecting a Google Calendar to the workspace.
 *
 * Consent happens in a popup rather than by navigating away, because the
 * builder often has unsaved graph edits and losing them to an OAuth round trip
 * is a bad trade. The popup posts a message back when it's done; the polling
 * fallback covers the case where the browser blocks that.
 *
 * One connection per workspace, then each agent chooses a calendar inside it.
 * Consent is something a person grants once; which diary an agent books into is
 * an agent setting that changes as often as any other.
 */
export function CalendarPanel() {
  const [status, setStatus] = useState<CalendarStatus | null>(null);
  const [calendars, setCalendars] = useState<CalendarOption[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function refresh() {
    const s = await api.calendarStatus();
    setStatus(s);
    if (s.connected) {
      try {
        setCalendars((await api.calendarList()).calendars);
      } catch (e: any) {
        setError(e.message);
      }
    } else {
      setCalendars([]);
    }
  }

  useEffect(() => {
    refresh().catch(() => setStatus({ connected: false }));
  }, []);

  async function connect() {
    setBusy(true);
    setError("");
    try {
      const { url } = await api.calendarConnect();
      const popup = window.open(url, "google-calendar", "width=520,height=680");

      const done = (ok: boolean) => {
        window.removeEventListener("message", onMessage);
        clearInterval(poll);
        setBusy(false);
        if (ok) refresh();
      };
      const onMessage = (e: MessageEvent) => {
        if (e.data?.source === "composer-calendar") done(!!e.data.ok);
      };
      window.addEventListener("message", onMessage);
      // Popup blockers and cross-origin quirks both eat postMessage, so also
      // watch for the window closing and just re-read the status.
      const poll = setInterval(() => {
        if (!popup || popup.closed) done(true);
      }, 800);
    } catch (e: any) {
      setError(e.message);
      setBusy(false);
    }
  }

  async function disconnect() {
    setBusy(true);
    try {
      await api.calendarDisconnect();
      await refresh();
    } finally {
      setBusy(false);
    }
  }

  if (!status) {
    return (
      <div className="flex items-center gap-2 p-6 text-[12px] text-mist-400">
        <Spinner /> Loading…
      </div>
    );
  }

  return (
    <div className="space-y-4 p-4">
      <div>
        <h2 className="text-[13px] font-semibold text-mist-100">Calendar</h2>
        <p className="mt-1 max-w-[60ch] text-[11.5px] leading-relaxed text-mist-400">
          Connect a Google Calendar and the agent can offer real availability and
          book into it, instead of reading out times that were typed into a prompt.
        </p>
      </div>

      {status.available === false && (
        <div className="rounded-md border border-amber-900/50 bg-amber-950/20 px-3 py-2 text-[11.5px] text-amber-300">
          Google isn't configured on this deployment.
        </div>
      )}

      <div className="space-y-3 rounded-lg border border-ink-800 bg-ink-900 p-4">
        {status.connected ? (
          <>
            <div className="flex flex-wrap items-center gap-2">
              <Icon.Check className="size-4 text-emerald-400" />
              <span className="text-[13px] text-mist-100">{status.email || "Connected"}</span>
              <Badge tone="green">connected</Badge>
            </div>
            {calendars.length > 0 && (
              <div className="space-y-1">
                <div className="font-mono text-[9.5px] uppercase tracking-[0.1em] text-mist-400">
                  Calendars on this account
                </div>
                {calendars.slice(0, 8).map((c) => (
                  <div key={c.id} className="flex items-center gap-2 text-[11.5px]">
                    <span className="text-mist-300">{c.name}</span>
                    {c.primary && <Badge tone="neutral">primary</Badge>}
                    {c.timezone && (
                      <span className="font-mono text-[9.5px] text-mist-400/70">{c.timezone}</span>
                    )}
                  </div>
                ))}
              </div>
            )}
            <Button size="xs" loading={busy} onClick={disconnect}>
              Disconnect
            </Button>
          </>
        ) : (
          <>
            <div className="text-[11.5px] leading-relaxed text-mist-400">
              You'll be asked to grant access to read and create events. Google
              will warn that this app isn't verified — that's expected while it's
              in testing.
            </div>
            <Button
              variant="primary"
              size="xs"
              loading={busy}
              disabled={status.available === false}
              onClick={connect}
            >
              Connect Google Calendar
            </Button>
          </>
        )}
      </div>

      {error && (
        <div className="rounded-md border border-rose-900/50 bg-rose-950/20 px-3 py-2 text-[11.5px] text-rose-300">
          {error}
        </div>
      )}

      <p className="max-w-[60ch] text-[11px] leading-relaxed text-mist-400/80">
        Simulated tests never touch this calendar — they run against an in-memory
        one, so a test run can't create appointments in a real diary.
      </p>
    </div>
  );
}
