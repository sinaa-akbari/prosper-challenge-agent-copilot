import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import type { AuthStatus } from "../types";
import { Button, Icon, Spinner, cx } from "./ui";

/**
 * The way in — and the way up, since signing in for the first time is how an
 * account gets created.
 *
 * A phone number rather than an email, because the people who build a voice
 * agent are reachable on a phone by definition, the number is already the thing
 * this product is about, and it means there is no password to reset.
 *
 * The password field is a break-glass for whoever runs the deployment, not a
 * user-facing option. It only appears when text-message sign-in is unavailable,
 * because an OTP flow whose SMS provider is down would otherwise lock everyone
 * out with no way back in.
 */
export function SignIn({
  status,
  onSignedIn,
}: {
  status: AuthStatus;
  onSignedIn: () => void;
}) {
  const [mode, setMode] = useState<"phone" | "password">(
    status.phone ? "phone" : "password"
  );
  const [stage, setStage] = useState<"enter" | "code">("enter");
  const [phone, setPhone] = useState("");
  const [code, setCode] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [note, setNote] = useState("");
  const codeRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (stage === "code") codeRef.current?.focus();
  }, [stage]);

  async function run(fn: () => Promise<void>) {
    setBusy(true);
    setError("");
    try {
      await fn();
    } catch (e: any) {
      setError(e.message || "That didn't work.");
    } finally {
      setBusy(false);
    }
  }

  const requestCode = () =>
    run(async () => {
      await api.requestCode(phone);
      setStage("code");
      setNote("Code sent. It should arrive within a few seconds.");
    });

  const verify = () =>
    run(async () => {
      await api.verifyCode(phone, code);
      onSignedIn();
    });

  const signInWithPassword = () =>
    run(async () => {
      await api.passwordLogin(password);
      onSignedIn();
    });

  return (
    <div className="flex min-h-screen items-center justify-center bg-ink-950 px-6">
      <div className="w-full max-w-[370px]">
        <div className="mb-7 flex items-center gap-2.5">
          <span className="grid size-7 place-items-center rounded-[6px] bg-signal text-[13px] font-bold text-ink-950">
            P
          </span>
          <span className="text-[15px] font-semibold text-mist-100">Agent Composer</span>
        </div>

        <h1 className="text-[19px] font-semibold text-mist-100">
          {mode === "phone" ? "Sign in or create an account" : "Sign in"}
        </h1>
        <p className="mt-1.5 text-[12.5px] leading-relaxed text-mist-400">
          {mode === "phone"
            ? "Enter your mobile number and we'll text you a code. If you haven't been here before, this creates your workspace."
            : "Administrator access for this deployment."}
        </p>

        <div className="mt-6 space-y-3">
          {mode === "phone" && stage === "enter" && (
            <>
              <Field
                label="Mobile number"
                value={phone}
                onChange={setPhone}
                placeholder="+34 600 000 000"
                type="tel"
                onEnter={requestCode}
                autoFocus
              />
              <Button
                variant="primary"
                className="w-full justify-center"
                loading={busy}
                onClick={requestCode}
                disabled={!phone.trim()}
              >
                Send me a code
              </Button>
            </>
          )}

          {mode === "phone" && stage === "code" && (
            <>
              <div className="flex items-center gap-1.5 text-[12px] text-mist-400">
                <span className="font-mono text-mist-300">{phone}</span>
                <button
                  onClick={() => {
                    setStage("enter");
                    setCode("");
                    setNote("");
                  }}
                  className="text-signal-2 underline-offset-2 hover:underline"
                >
                  change
                </button>
              </div>
              <Field
                label="Six-digit code"
                value={code}
                onChange={(v) => setCode(v.replace(/\D/g, "").slice(0, 6))}
                placeholder="000000"
                mono
                inputRef={codeRef}
                onEnter={verify}
              />
              <Button
                variant="primary"
                className="w-full justify-center"
                loading={busy}
                onClick={verify}
                disabled={code.length < 6}
              >
                Sign in
              </Button>
            </>
          )}

          {mode === "password" && (
            <>
              <Field
                label="Password"
                value={password}
                onChange={setPassword}
                type="password"
                onEnter={signInWithPassword}
                autoFocus
              />
              <Button
                variant="primary"
                className="w-full justify-center"
                loading={busy}
                onClick={signInWithPassword}
                disabled={!password}
              >
                Sign in
              </Button>
            </>
          )}

          {note && !error && (
            <div className="text-[11.5px] leading-relaxed text-mist-400">{note}</div>
          )}
          {error && (
            <div className="flex items-start gap-1.5 rounded-md border border-rose-900/50 bg-rose-950/20 px-3 py-2 text-[11.5px] text-rose-300">
              <Icon.Warn className="mt-0.5 size-3 shrink-0" />
              {error}
            </div>
          )}

          {/* Only offered when both actually work. */}
          {status.phone && status.password && (
            <button
              onClick={() => {
                setMode(mode === "phone" ? "password" : "phone");
                setStage("enter");
                setError("");
                setNote("");
              }}
              className="w-full pt-2 text-center text-[10.5px] text-mist-400/70 underline-offset-2 hover:text-mist-300 hover:underline"
            >
              {mode === "phone" ? "Administrator sign-in" : "Back to phone sign-in"}
            </button>
          )}

          {mode === "phone" && stage === "enter" && (
            <p className="pt-1 text-[11px] leading-relaxed text-mist-400/80">
              Standard message rates apply. We use your number to sign you in and
              nothing else.
            </p>
          )}

          {!status.phone && !status.password && (
            <div className="rounded-md border border-amber-900/50 bg-amber-950/20 px-3 py-2 text-[11.5px] leading-relaxed text-amber-300">
              No sign-in method is configured on this deployment. Set
              <span className="font-mono"> TWILIO_VERIFY_SERVICE_SID </span>
              or<span className="font-mono"> AUTH_PASSWORD </span>
              in the server's environment.
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function Field({
  label,
  value,
  onChange,
  placeholder,
  type = "text",
  mono,
  autoFocus,
  inputRef,
  onEnter,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  type?: string;
  mono?: boolean;
  autoFocus?: boolean;
  inputRef?: React.RefObject<HTMLInputElement>;
  onEnter?: () => void;
}) {
  return (
    <label className="block">
      <span className="mb-1.5 block font-mono text-[9.5px] uppercase tracking-[0.1em] text-mist-400">
        {label}
      </span>
      <input
        ref={inputRef}
        autoFocus={autoFocus}
        type={type}
        value={value}
        placeholder={placeholder}
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={(e) => e.key === "Enter" && onEnter?.()}
        className={cx(
          "w-full rounded-[5px] border border-ink-700 bg-ink-900 px-3 py-2 text-[13px] text-mist-100",
          "placeholder:text-mist-400/50 focus:border-signal focus:outline-none",
          mono && "font-mono tracking-[0.3em]"
        )}
      />
    </label>
  );
}
