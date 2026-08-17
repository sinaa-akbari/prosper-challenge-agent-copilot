import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api";
import type { LiveCall } from "../types";
import { Badge, Button, Icon, Spinner, cx } from "./ui";

interface Props {
  agentId: string;
  onClose: () => void;
  /** Drives node highlighting on the graph, from either mode. */
  onNodeUpdate: (node: string | null, path: string[]) => void;
}

type Mode = "voice" | "chat";
type Speaker = "agent" | "caller" | "transition";
interface PanelTurn {
  speaker: Speaker;
  text: string;
  function?: string;
  target?: string;
}

/* ------------------------------------------------------------ mic access -- */
async function listDevices(kind: MediaDeviceKind): Promise<MediaDeviceInfo[]> {
  if (!navigator.mediaDevices?.enumerateDevices) return [];
  try {
    const all = await navigator.mediaDevices.enumerateDevices();
    return all.filter((d) => d.kind === kind && d.deviceId !== "communications");
  } catch {
    return [];
  }
}

/** Try progressively looser constraints — a specific device may have vanished
 *  since it was enumerated, and a bare `{audio:true}` often still succeeds. */
async function acquireMic(deviceId?: string): Promise<MediaStream> {
  if (!navigator.mediaDevices?.getUserMedia) {
    throw Object.assign(new Error("insecure-context"), { name: "SecurityError" });
  }
  const attempts: MediaStreamConstraints[] = [];
  if (deviceId) attempts.push({ audio: { deviceId: { exact: deviceId } } });
  attempts.push({ audio: { echoCancellation: true, noiseSuppression: true } });
  attempts.push({ audio: true });

  let last: unknown;
  for (const constraints of attempts) {
    try {
      return await navigator.mediaDevices.getUserMedia(constraints);
    } catch (err) {
      last = err;
    }
  }
  throw last;
}

function micError(err: any, micCount: number): string {
  switch (err?.name) {
    case "NotFoundError":
    case "DevicesNotFoundError":
    case "OverconstrainedError":
      return micCount === 0
        ? "No microphone is connected. Plug one in or connect a headset, then hit Refresh — or use Chat, which needs no mic at all."
        : "That microphone isn't available any more. Pick a different one below, or use Chat.";
    case "NotAllowedError":
    case "PermissionDeniedError":
      return "Microphone access is blocked. Allow it from the padlock in the address bar, then try again.";
    case "SecurityError":
      return "This page can't reach a microphone. Open it over http://localhost or https, or use Chat.";
    case "NotReadableError":
    case "TrackStartError":
    case "AbortError":
      return "Another app is holding the microphone. Close it and try again, or use Chat.";
    default:
      return err?.message ? `Couldn't open the microphone: ${err.message}` : "Couldn't open the microphone.";
  }
}

/** ICE gathering has no promise API; resolve on completion or send what we have. */
function iceComplete(pc: RTCPeerConnection, timeoutMs = 2500) {
  if (pc.iceGatheringState === "complete") return Promise.resolve();
  return new Promise<void>((resolve) => {
    const done = () => {
      pc.removeEventListener("icegatheringstatechange", check);
      clearTimeout(timer);
      resolve();
    };
    const check = () => pc.iceGatheringState === "complete" && done();
    const timer = setTimeout(done, timeoutMs);
    pc.addEventListener("icegatheringstatechange", check);
  });
}

/* ------------------------------------------------------------ trace view -- */
const KIND_TONE: Record<string, string> = {
  webrtc: "text-sky-300",
  call: "text-mist-100",
  voice: "text-violet-300",
  vad: "text-mist-400",
  "stt.interim": "text-mist-400",
  "stt.final": "text-emerald-300",
  llm: "text-violet-300",
  tool: "text-amber-300",
  flow: "text-amber-300",
  tts: "text-signal-2",
  "tts.text": "text-signal-2",
  "tts.audio": "text-emerald-300",
  speak: "text-mist-400",
  error: "text-rose-300",
};

function TraceView({ call }: { call: LiveCall }) {
  const events = call.events ?? [];
  const audio = call.audio;
  const [copied, setCopied] = useState(false);

  const asText = () =>
    [
      `session ${call.session_id}  agent ${call.agent_id}  status ${call.status}`,
      `path: ${(call.path ?? []).join(" -> ")}`,
      audio
        ? `audio: ${audio.frames} chunks, ${audio.kb} KB, peak ${audio.peak}${audio.silent ? "  *** SILENT ***" : ""}`
        : "",
      call.warning ? `warning: ${call.warning}` : "",
      call.error ? `error: ${call.error}` : "",
      "",
      ...events.map((e) => `${String(e.ms).padStart(6)}ms  ${e.kind.padEnd(12)} ${e.detail}`),
    ]
      .filter(Boolean)
      .join("\n");

  return (
    <div className="flex min-h-[200px] flex-1 flex-col overflow-hidden">
      {/* the two numbers that explain most call failures */}
      <div className="flex items-center gap-2 border-b border-ink-800 px-3.5 py-2">
        {audio && (
          <>
            <Badge tone={audio.silent ? "rose" : audio.frames ? "green" : "neutral"}>
              {audio.frames} chunks
            </Badge>
            <Badge tone={audio.peak > 0 ? "green" : "rose"}>peak {audio.peak}</Badge>
            {audio.silent && <span className="text-[10.5px] text-rose-300">silent audio</span>}
          </>
        )}
        <button
          onClick={() => {
            void navigator.clipboard.writeText(asText());
            setCopied(true);
            setTimeout(() => setCopied(false), 1400);
          }}
          className="ml-auto font-mono text-[9.5px] uppercase tracking-[0.1em] text-mist-400 hover:text-mist-100"
        >
          {copied ? "copied" : "copy"}
        </button>
      </div>

      <div className="flex-1 overflow-y-auto px-3.5 py-2">
        {events.length === 0 ? (
          <p className="py-6 text-center text-[11.5px] text-mist-400">No events yet.</p>
        ) : (
          <div className="space-y-[3px]">
            {events.map((e, i) => (
              <div key={i} className="flex gap-2 font-mono text-[10px] leading-[1.5]">
                <span className="tnum w-11 shrink-0 text-right text-mist-400/60">{e.ms}</span>
                <span className={cx("w-[74px] shrink-0", KIND_TONE[e.kind] ?? "text-mist-400")}>
                  {e.kind}
                </span>
                <span
                  className={cx(
                    "min-w-0 break-words",
                    e.level === "error"
                      ? "text-rose-300"
                      : e.level === "warning"
                        ? "text-amber-300"
                        : "text-mist-300"
                  )}
                >
                  {e.detail}
                </span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

/* ==================================================================== ==== */
export function CallPanel({ agentId, onClose, onNodeUpdate }: Props) {
  const [mode, setMode] = useState<Mode>("voice");
  const [mics, setMics] = useState<MediaDeviceInfo[]>([]);
  const [micId, setMicId] = useState<string | undefined>();
  const [outputs, setOutputs] = useState<MediaDeviceInfo[]>([]);
  const [outputId, setOutputId] = useState<string | undefined>();
  // Chrome can refuse to start playback even after a click; say so rather than
  // leaving the caller staring at a silent, apparently-working call.
  const [audioBlocked, setAudioBlocked] = useState(false);
  const [showTrace, setShowTrace] = useState(false);

  const [state, setState] = useState<"idle" | "connecting" | "live" | "ended" | "error">("idle");
  const [error, setError] = useState("");
  const [call, setCall] = useState<LiveCall | null>(null);

  // chat mode
  const [chatTurns, setChatTurns] = useState<PanelTurn[]>([]);
  const [chatHistory, setChatHistory] = useState<any[]>([]);
  const [chatNode, setChatNode] = useState<string | null>(null);
  const [chatCollected, setChatCollected] = useState<Record<string, any>>({});
  const [chatEnded, setChatEnded] = useState(false);
  const [chatBusy, setChatBusy] = useState(false);
  const [input, setInput] = useState("");

  const pcRef = useRef<RTCPeerConnection | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const pollRef = useRef<number | null>(null);
  const scroller = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const refreshMics = useCallback(async () => {
    setMics(await listDevices("audioinput"));
    setOutputs(await listDevices("audiooutput"));
  }, []);

  /** Route playback to a chosen speaker, and make sure it is actually playing. */
  const startPlayback = useCallback(async () => {
    const el = audioRef.current;
    if (!el) return;
    try {
      if (outputId && "setSinkId" in el) await (el as any).setSinkId(outputId);
    } catch {
      /* unsupported browser or stale device id — fall back to the default */
    }
    try {
      await el.play();
      setAudioBlocked(false);
    } catch {
      setAudioBlocked(true);
    }
  }, [outputId]);

  useEffect(() => {
    refreshMics();
    navigator.mediaDevices?.addEventListener?.("devicechange", refreshMics);
    return () => {
      navigator.mediaDevices?.removeEventListener?.("devicechange", refreshMics);
      teardown();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const turns: PanelTurn[] =
    mode === "voice"
      ? (call?.turns ?? []).map((t) => ({ speaker: t.speaker as Speaker, text: t.text }))
      : chatTurns;

  const currentNode = mode === "voice" ? call?.current_node : chatNode;
  const collected = mode === "voice" ? (call?.collected ?? {}) : chatCollected;

  useEffect(() => {
    scroller.current?.scrollTo({ top: scroller.current.scrollHeight, behavior: "smooth" });
  }, [turns.length, chatBusy]);

  useEffect(() => {
    onNodeUpdate(currentNode ?? null, mode === "voice" ? (call?.path ?? []) : []);
  }, [currentNode, call?.path, mode, onNodeUpdate]);

  useEffect(() => {
    if (state === "live") void startPlayback();
  }, [outputId, state, startPlayback]);

  function teardown() {
    if (pollRef.current) window.clearInterval(pollRef.current);
    pollRef.current = null;
    streamRef.current?.getTracks().forEach((t) => t.stop());
    streamRef.current = null;
    pcRef.current?.close();
    pcRef.current = null;
  }

  /* ------------------------------------------------------------- voice -- */
  async function connect() {
    setError("");
    setState("connecting");
    try {
      const stream = await acquireMic(micId);
      streamRef.current = stream;
      // Labels are only populated once permission is granted.
      refreshMics();

      const pc = new RTCPeerConnection({
        iceServers: [{ urls: "stun:stun.l.google.com:19302" }],
      });
      pcRef.current = pc;

      stream.getTracks().forEach((track) => pc.addTrack(track, stream));
      pc.createDataChannel("pipecat");
      pc.ontrack = (e) => {
        if (!audioRef.current) return;
        audioRef.current.srcObject = e.streams[0];
        void startPlayback();
      };
      pc.onconnectionstatechange = () => {
        if (["failed", "closed", "disconnected"].includes(pc.connectionState)) {
          setState((s) => (s === "live" ? "ended" : s));
        }
      };

      await pc.setLocalDescription(await pc.createOffer());
      await iceComplete(pc);

      const res = await fetch(`/api/agents/${agentId}/call/offer`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ sdp: pc.localDescription!.sdp, type: pc.localDescription!.type }),
      });
      if (!res.ok) {
        throw new Error((await res.json().catch(() => ({}))).detail ?? "The server rejected the call");
      }
      const answer = await res.json();
      await pc.setRemoteDescription({ sdp: answer.sdp, type: answer.type });
      setState("live");

      const sessionId: string = answer.session_id;
      pollRef.current = window.setInterval(async () => {
        try {
          const live = await api.liveCall(sessionId);
          setCall(live);
          if (live.status === "ended" || live.status === "error") {
            setState(live.status === "error" ? "error" : "ended");
            if (live.error) setError(live.error);
            if (pollRef.current) window.clearInterval(pollRef.current);
          }
        } catch {
          /* the session record lags the first poll */
        }
      }, 800);
    } catch (e: any) {
      teardown();
      setState("error");
      setError(e?.name ? micError(e, mics.length) : (e?.message ?? "Something went wrong."));
      refreshMics();
    }
  }

  function hangUp() {
    teardown();
    setState("ended");
  }

  /* -------------------------------------------------------------- chat -- */
  const sendChat = useCallback(
    async (message?: string) => {
      if (chatBusy || chatEnded) return;
      setChatBusy(true);
      if (message) setChatTurns((t) => [...t, { speaker: "caller", text: message }]);
      setInput("");
      try {
        const res = await fetch(`/api/agents/${agentId}/chat`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ history: chatHistory, node: chatNode, message }),
        });
        if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail ?? "Chat failed");
        const d = await res.json();
        setChatTurns((t) => [
          ...t,
          ...d.turns.map((x: any) => ({
            speaker: x.speaker as Speaker,
            text: x.text,
            function: x.function,
            target: x.target,
          })),
        ]);
        setChatHistory(d.history);
        setChatNode(d.node);
        setChatCollected((c) => ({ ...c, ...d.collected }));
        setChatEnded(d.ended);
      } catch (e: any) {
        setChatTurns((t) => [...t, { speaker: "agent", text: `[error: ${e.message}]` }]);
      } finally {
        setChatBusy(false);
        setTimeout(() => inputRef.current?.focus(), 50);
      }
    },
    [agentId, chatBusy, chatEnded, chatHistory, chatNode]
  );

  function resetChat() {
    setChatTurns([]);
    setChatHistory([]);
    setChatNode(null);
    setChatCollected({});
    setChatEnded(false);
  }

  // Opening chat mode starts the conversation, since the agent speaks first.
  useEffect(() => {
    if (mode === "chat" && chatTurns.length === 0 && !chatBusy) sendChat();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mode]);

  const voiceLive = state === "live";
  const showTranscript = mode === "chat" || voiceLive || state === "ended";

  return (
    <div
      data-testid="call-panel"
      data-call-state={state}
      data-mode={mode}
      data-node={currentNode ?? ""}
      className="pointer-events-auto flex max-h-[74vh] w-[380px] flex-col overflow-hidden rounded-xl border border-ink-700 bg-ink-900/95 shadow-2xl backdrop-blur"
    >
      <audio ref={audioRef} autoPlay />

      {/* header */}
      <div className="flex items-center gap-2 border-b border-ink-800 px-3.5 py-2.5">
        <Icon.Phone className={cx("size-3.5", voiceLive ? "text-signal" : "text-mist-400")} />
        <span className="text-[12.5px] font-semibold">Test agent</span>
        {voiceLive && (
          <Badge tone="teal">
            <span className="size-1.5 animate-pulse rounded-full bg-signal" /> live
          </Badge>
        )}
        {mode === "chat" && chatEnded && <Badge tone="neutral">call ended</Badge>}
        <div className="ml-auto flex items-center gap-1">
          {mode === "voice" && (call?.events?.length ?? 0) > 0 && (
            <button
              onClick={() => setShowTrace((v) => !v)}
              title="Show the call timeline"
              className={cx(
                "rounded-[3px] px-1.5 py-1 font-mono text-[9.5px] uppercase tracking-[0.1em] transition-colors",
                showTrace
                  ? "bg-signal/15 text-signal-2"
                  : "text-mist-400 hover:bg-ink-850 hover:text-mist-300"
              )}
            >
              Trace
            </button>
          )}
          <Button variant="ghost" size="xs" onClick={onClose}>
            <Icon.X className="size-3" />
          </Button>
        </div>
      </div>

      {/* mode switch */}
      <div className="flex gap-1 border-b border-ink-800 p-1.5">
        {(["voice", "chat"] as Mode[]).map((m) => (
          <button
            key={m}
            onClick={() => {
              if (m === mode) return;
              if (m === "voice") {
                resetChat();
              } else {
                teardown();
                setState("idle");
                setCall(null);
                setError("");
              }
              setMode(m);
            }}
            className={cx(
              "flex-1 rounded-md px-2 py-1.5 text-[11.5px] font-medium transition-colors",
              mode === m
                ? "bg-ink-800 text-mist-100 shadow-sm"
                : "text-mist-400 hover:bg-ink-850 hover:text-mist-300"
            )}
          >
            {m === "voice" ? "Voice" : "Chat"}
            {m === "chat" && <span className="ml-1 text-[10px] text-mist-400">no mic</span>}
          </button>
        ))}
      </div>

      {/* voice: pre-call */}
      {mode === "voice" && (state === "idle" || state === "error") && (
        <div className="space-y-3 p-4">
          {state === "error" ? (
            <div className="rounded-md border border-rose-900/50 bg-rose-950/20 px-3 py-2 text-[11.5px] leading-relaxed text-rose-300">
              {error}
            </div>
          ) : (
            <p className="text-[11.5px] leading-relaxed text-mist-400">
              Talk to the saved version of this agent. The graph highlights each node as the
              call moves through it.
            </p>
          )}

          <div className="space-y-1.5">
            <div className="flex items-center justify-between">
              <span className="text-[10.5px] font-semibold uppercase tracking-wider text-mist-400">
                Microphone
              </span>
              <button
                onClick={refreshMics}
                className="text-[10.5px] text-signal-2 underline-offset-2 hover:underline"
              >
                Refresh
              </button>
            </div>
            {mics.length === 0 ? (
              <div className="rounded-md border border-dashed border-ink-700 px-2.5 py-2 text-[11px] leading-relaxed text-mist-400">
                No microphone detected. Connect one and press Refresh — or switch to{" "}
                <button
                  onClick={() => setMode("chat")}
                  className="text-signal-2 underline underline-offset-2"
                >
                  Chat
                </button>
                , which tests the same graph without audio.
              </div>
            ) : (
              <select
                value={micId ?? ""}
                onChange={(e) => setMicId(e.target.value || undefined)}
                className="w-full cursor-pointer rounded-md border border-ink-700 bg-ink-900 px-2 py-1.5 text-[11.5px] text-mist-100 outline-none focus:border-signal/60"
              >
                <option value="">System default</option>
                {mics.map((d, i) => (
                  <option key={d.deviceId || i} value={d.deviceId}>
                    {d.label || `Microphone ${i + 1}`}
                  </option>
                ))}
              </select>
            )}
          </div>

          {outputs.length > 1 && (
            <div className="space-y-1.5">
              <span className="eyebrow">Speaker</span>
              <select
                value={outputId ?? ""}
                onChange={(e) => setOutputId(e.target.value || undefined)}
                className="w-full cursor-pointer rounded-[4px] border border-ink-700 bg-ink-950 px-2 py-1.5 text-[11.5px] text-mist-100 outline-none focus:border-signal/70"
              >
                <option value="">System default</option>
                {outputs.map((d, i) => (
                  <option key={d.deviceId || i} value={d.deviceId}>
                    {d.label || `Output ${i + 1}`}
                  </option>
                ))}
              </select>
            </div>
          )}

          <Button
            variant="primary"
            size="md"
            onClick={connect}
            className="w-full"
            disabled={mics.length === 0 && state === "error"}
          >
            <Icon.Phone className="size-3.5" /> {state === "error" ? "Try again" : "Start call"}
          </Button>
        </div>
      )}

      {mode === "voice" && state === "connecting" && (
        <div className="flex items-center justify-center gap-2 py-10 text-[12px] text-mist-400">
          <Spinner /> Connecting…
        </div>
      )}

      {/* A silent call that looks healthy is the worst failure mode there is,
          so anything that would cause one gets said out loud. */}
      {mode === "voice" && (call?.warning || audioBlocked) && (
        <div className="space-y-2 border-b border-amber-900/40 bg-amber-950/20 px-3.5 py-2">
          {call?.warning && (
            <div className="flex items-start gap-1.5 text-[11px] leading-relaxed text-amber-200">
              <Icon.Warn className="mt-0.5 size-3 shrink-0" />
              <span>{call.warning}</span>
            </div>
          )}
          {audioBlocked && (
            <div className="flex items-center gap-2">
              <span className="text-[11px] text-amber-200">
                The browser blocked audio playback.
              </span>
              <Button size="xs" variant="primary" onClick={() => void startPlayback()}>
                Enable sound
              </Button>
            </div>
          )}
        </div>
      )}

      {/* debug timeline */}
      {mode === "voice" && showTrace && call && (
        <TraceView call={call} />
      )}

      {/* transcript (shared) */}
      {!showTrace && showTranscript && (
        <>
          {currentNode && (
            <div className="flex items-center gap-2 border-b border-ink-800 px-3.5 py-2 text-[11px]">
              <span className="text-mist-400">at</span>
              <span className="font-mono text-signal-2">{currentNode}</span>
              {mode === "chat" && (
                <button
                  onClick={resetChat}
                  className="ml-auto text-[10.5px] text-mist-400 hover:text-mist-100"
                >
                  Restart
                </button>
              )}
            </div>
          )}

          <div ref={scroller} className="min-h-[140px] flex-1 space-y-2 overflow-y-auto p-3.5">
            {turns.length === 0 && !chatBusy ? (
              <div className="py-6 text-center text-[11.5px] text-mist-400">
                {mode === "voice" ? "Say hello — the agent speaks first." : "Starting…"}
              </div>
            ) : (
              turns.map((t, i) =>
                t.speaker === "transition" ? (
                  <div key={i} className="fade-up flex items-center gap-1.5 py-0.5 pl-1">
                    <span className="h-px flex-1 bg-ink-800" />
                    <span className="font-mono text-[9.5px] text-violet-300/70">
                      {t.function} → {t.target}
                    </span>
                    <span className="h-px flex-1 bg-ink-800" />
                  </div>
                ) : (
                  <div
                    key={i}
                    className={cx(
                      "fade-up flex",
                      t.speaker === "caller" ? "justify-end" : "justify-start"
                    )}
                  >
                    <div
                      className={cx(
                        "max-w-[85%] rounded-lg px-2.5 py-1.5 text-[11.5px] leading-relaxed",
                        t.speaker === "caller"
                          ? "rounded-br-sm bg-ink-700 text-mist-100"
                          : "rounded-bl-sm bg-ink-850 text-mist-300"
                      )}
                    >
                      {t.text}
                    </div>
                  </div>
                )
              )
            )}
            {chatBusy && (
              <div className="flex items-center gap-2 pl-1 text-[11px] text-mist-400">
                <Spinner className="size-3" /> thinking…
              </div>
            )}
          </div>

          {Object.keys(collected).length > 0 && (
            <div className="border-t border-ink-800 px-3.5 py-2">
              <div className="mb-1 text-[10px] font-semibold uppercase tracking-wider text-mist-400">
                Collected
              </div>
              <div className="flex flex-wrap gap-1">
                {Object.entries(collected).map(([k, v]) => (
                  <Badge key={k} tone="violet" title={`${k}: ${v}`}>
                    {k}: {String(v).slice(0, 22)}
                  </Badge>
                ))}
              </div>
            </div>
          )}
        </>
      )}

      {/* footer */}
      {mode === "voice" && (voiceLive || state === "ended") && (
        <div className="border-t border-ink-800 p-3">
          {voiceLive ? (
            <Button variant="danger" size="sm" onClick={hangUp} className="w-full">
              Hang up
            </Button>
          ) : (
            <Button size="sm" onClick={connect} className="w-full">
              Call again
            </Button>
          )}
        </div>
      )}

      {mode === "chat" && (
        <div className="border-t border-ink-800 p-2.5">
          {chatEnded ? (
            <Button size="sm" onClick={resetChat} className="w-full">
              Start over
            </Button>
          ) : (
            <div className="flex gap-1.5">
              <input
                ref={inputRef}
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && input.trim() && sendChat(input.trim())}
                placeholder="Type what the caller says…"
                disabled={chatBusy}
                className="w-full rounded-md border border-ink-700 bg-ink-900 px-2.5 py-1.5 text-[12px] text-mist-100 placeholder:text-mist-400/60 outline-none focus:border-signal/60 disabled:opacity-50"
              />
              <Button
                variant="primary"
                size="sm"
                onClick={() => input.trim() && sendChat(input.trim())}
                disabled={!input.trim() || chatBusy}
              >
                Send
              </Button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
