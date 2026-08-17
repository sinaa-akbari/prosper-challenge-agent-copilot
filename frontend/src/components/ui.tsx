import React from "react";

export const cx = (...parts: (string | false | null | undefined)[]) =>
  parts.filter(Boolean).join(" ");

/* -------------------------------------------------------------- Button --- */
type ButtonProps = React.ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "primary" | "ghost" | "outline" | "danger" | "subtle";
  size?: "xs" | "sm" | "md";
  loading?: boolean;
};

export function Button({
  variant = "outline",
  size = "sm",
  loading,
  className,
  children,
  disabled,
  ...rest
}: ButtonProps) {
  const sizes = {
    xs: "h-[22px] px-2 text-[11px] gap-1",
    sm: "h-7 px-2.5 text-[12px] gap-1.5",
    md: "h-8 px-3.5 text-[12.5px] gap-2",
  }[size];

  const variants = {
    // The one solid fill in the app. Everything else is quieter than this.
    primary:
      "bg-signal text-ink-950 font-semibold hover:brightness-110 border border-signal active:brightness-95",
    outline:
      "bg-ink-850 text-mist-100 border border-ink-700 hover:border-ink-600 hover:bg-ink-800",
    ghost:
      "bg-transparent text-mist-400 border border-transparent hover:bg-ink-850 hover:text-mist-100",
    subtle:
      "bg-ink-800 text-mist-300 border border-transparent hover:bg-ink-700 hover:text-mist-100",
    danger:
      "bg-transparent text-rose-300 border border-rose-900/60 hover:bg-rose-950/50 hover:border-rose-800",
  }[variant];

  return (
    <button
      className={cx(
        "inline-flex select-none items-center justify-center rounded-[4px] transition-all duration-100",
        "whitespace-nowrap disabled:pointer-events-none disabled:opacity-35",
        sizes,
        variants,
        className
      )}
      disabled={disabled || loading}
      {...rest}
    >
      {loading && <Spinner className="size-3" />}
      {children}
    </button>
  );
}

/* ------------------------------------------------------------- Spinner --- */
export function Spinner({ className }: { className?: string }) {
  return (
    <svg className={cx("animate-spin size-3.5", className)} viewBox="0 0 24 24" fill="none">
      <circle cx="12" cy="12" r="9" stroke="currentColor" strokeWidth="3" opacity="0.25" />
      <path
        d="M21 12a9 9 0 0 0-9-9"
        stroke="currentColor"
        strokeWidth="3"
        strokeLinecap="round"
      />
    </svg>
  );
}

/* --------------------------------------------------------------- Badge --- */
/* Badges are flat and squared — a chip, not a pill. Borders only where the
   fill alone wouldn't separate it from the surface. */
const badgeTones = {
  neutral: "bg-ink-800 text-mist-300",
  teal: "bg-signal/12 text-signal-2",
  amber: "bg-amber-500/12 text-amber-300",
  rose: "bg-rose-500/14 text-rose-300",
  violet: "bg-violet-500/14 text-violet-300",
  green: "bg-emerald-500/12 text-emerald-300",
  blue: "bg-sky-500/12 text-sky-300",
};
export type Tone = keyof typeof badgeTones;

export function Badge({
  tone = "neutral",
  children,
  className,
  title,
}: {
  tone?: Tone;
  children: React.ReactNode;
  className?: string;
  title?: string;
}) {
  return (
    <span
      title={title}
      className={cx(
        "tnum inline-flex items-center gap-1 rounded-[3px] px-1.5 py-[3px]",
        "font-mono text-[9.5px] font-medium uppercase leading-none tracking-[0.06em]",
        badgeTones[tone],
        className
      )}
    >
      {children}
    </span>
  );
}

export const severityTone: Record<string, Tone> = {
  critical: "rose",
  high: "amber",
  medium: "blue",
  low: "neutral",
};

/* --------------------------------------------------------------- Panel --- */
export function SectionLabel({
  children,
  right,
}: {
  children: React.ReactNode;
  right?: React.ReactNode;
}) {
  return (
    <div className="flex items-center justify-between px-4 pb-2 pt-4">
      <div className="eyebrow">{children}</div>
      {right}
    </div>
  );
}

/** Panel header used at the top of every full-width view. */
export function PanelHeader({
  title,
  subtitle,
  children,
}: {
  title: string;
  subtitle?: string;
  children?: React.ReactNode;
}) {
  return (
    <div className="flex items-start gap-3 border-b border-ink-800 px-5 py-3.5">
      <div className="min-w-0">
        <h2 className="text-[14px] font-semibold leading-none tracking-[-0.01em] text-mist-100">
          {title}
        </h2>
        {subtitle && (
          <p className="mt-1.5 text-[11.5px] leading-snug text-mist-400">{subtitle}</p>
        )}
      </div>
      {children && <div className="ml-auto flex shrink-0 items-center gap-2 pt-0.5">{children}</div>}
    </div>
  );
}

export function Empty({
  title,
  hint,
  action,
  icon,
}: {
  title: string;
  hint?: string;
  action?: React.ReactNode;
  icon?: React.ReactNode;
}) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 px-8 py-14 text-center">
      {icon && <div className="mb-1 text-mist-400 opacity-60">{icon}</div>}
      <div className="text-[13px] font-medium text-mist-300">{title}</div>
      {hint && <div className="max-w-xs text-[12px] leading-relaxed text-mist-400">{hint}</div>}
      {action && <div className="mt-2">{action}</div>}
    </div>
  );
}

/* --------------------------------------------------------------- Field --- */
export function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <label className="block">
      <div className="mb-1 flex items-baseline gap-2">
        <span className="eyebrow">{label}</span>
        {hint && <span className="text-[10.5px] text-mist-400">{hint}</span>}
      </div>
      {children}
    </label>
  );
}

const inputBase =
  "w-full rounded-[4px] border border-ink-700 bg-ink-950 px-2.5 py-1.5 text-[12.5px] text-mist-100 " +
  "placeholder:text-mist-400/55 outline-none transition-colors " +
  "hover:border-ink-600 focus:border-signal/70 focus:bg-ink-900 focus-visible:outline-none";

export function Input(props: React.InputHTMLAttributes<HTMLInputElement>) {
  return <input {...props} className={cx(inputBase, props.className)} />;
}

export function Textarea(props: React.TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return (
    <textarea
      {...props}
      className={cx(inputBase, "resize-y leading-relaxed", props.className)}
    />
  );
}

export function Select(props: React.SelectHTMLAttributes<HTMLSelectElement>) {
  return <select {...props} className={cx(inputBase, "cursor-pointer", props.className)} />;
}

/** Textarea that grows with its content — no inner scrollbar, no fixed rows. */
export function AutoTextarea({
  value,
  minRows = 2,
  maxHeight = 340,
  ...rest
}: React.TextareaHTMLAttributes<HTMLTextAreaElement> & {
  value: string;
  minRows?: number;
  maxHeight?: number;
}) {
  const ref = React.useRef<HTMLTextAreaElement>(null);
  React.useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight + 2, maxHeight)}px`;
    el.style.overflowY = el.scrollHeight + 2 > maxHeight ? "auto" : "hidden";
  }, [value, maxHeight]);

  return (
    <textarea
      ref={ref}
      value={value}
      rows={minRows}
      {...rest}
      className={cx(inputBase, "resize-none leading-relaxed", rest.className)}
    />
  );
}

/** Height-animated container. Uses the grid 0fr→1fr trick so it works with
 *  content of unknown height without measuring anything. */
export function Collapse({ open, children }: { open: boolean; children: React.ReactNode }) {
  return (
    <div
      className={cx(
        "grid transition-[grid-template-rows,opacity] duration-200 ease-out",
        open ? "grid-rows-[1fr] opacity-100" : "grid-rows-[0fr] opacity-0"
      )}
    >
      <div className="overflow-hidden">{children}</div>
    </div>
  );
}

/** Small on/off pill — reads better than a checkbox in a dense inspector. */
export function Toggle({
  checked,
  onChange,
  label,
  hint,
}: {
  checked: boolean;
  onChange: (v: boolean) => void;
  label: string;
  hint?: string;
}) {
  return (
    <button
      type="button"
      onClick={() => onChange(!checked)}
      className={cx(
        "group flex w-full items-center gap-2.5 rounded-md border px-2.5 py-2 text-left transition-colors",
        checked
          ? "border-signal/30 bg-signal/5"
          : "border-ink-700 bg-ink-850 hover:border-ink-600"
      )}
    >
      <span
        className={cx(
          "relative h-4 w-7 shrink-0 rounded-full transition-colors",
          checked ? "bg-signal" : "bg-ink-600"
        )}
      >
        <span
          className={cx(
            "absolute top-0.5 size-3 rounded-full bg-ink-950 transition-all duration-200",
            checked ? "left-3.5" : "left-0.5"
          )}
        />
      </span>
      <span className="min-w-0">
        <span className={cx("block text-[12px]", checked ? "text-mist-100" : "text-mist-300")}>
          {label}
        </span>
        {hint && <span className="block text-[10.5px] leading-tight text-mist-400">{hint}</span>}
      </span>
    </button>
  );
}

/* --------------------------------------------------------------- Icons --- */
export const Icon = {
  Graph: (p: any) => (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" {...p}>
      <rect x="3" y="3" width="7" height="5" rx="1.5" />
      <rect x="14" y="16" width="7" height="5" rx="1.5" />
      <rect x="3" y="16" width="7" height="5" rx="1.5" />
      <path d="M6.5 8v8M10 18.5h4M17.5 16v-4.5H6.5" />
    </svg>
  ),
  Flask: (p: any) => (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" {...p}>
      <path d="M9 3h6M10 3v6L4.5 18a2 2 0 0 0 1.7 3h11.6a2 2 0 0 0 1.7-3L14 9V3" />
      <path d="M7 15h10" />
    </svg>
  ),
  Inbox: (p: any) => (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" {...p}>
      <path d="M3 13h4l2 3h6l2-3h4" />
      <path d="M4.5 5.5h15l2 7.5v5a1.5 1.5 0 0 1-1.5 1.5H4a1.5 1.5 0 0 1-1.5-1.5v-5z" />
    </svg>
  ),
  Clock: (p: any) => (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" {...p}>
      <circle cx="12" cy="12" r="9" />
      <path d="M12 7v5l3 2" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  ),
  History: (p: any) => (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" {...p}>
      <path d="M3.5 12a8.5 8.5 0 1 0 2.6-6.1M3.5 4.5V10h5.5" />
      <path d="M12 7.5V12l3 2" />
    </svg>
  ),
  Phone: (p: any) => (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" {...p}>
      <path d="M6.5 3h3l1.5 4-2 1.5a12 12 0 0 0 5.5 5.5L16 12l4 1.5v3a2 2 0 0 1-2.2 2A16.5 16.5 0 0 1 3.5 5.2 2 2 0 0 1 5.5 3z" />
    </svg>
  ),
  Sparkle: (p: any) => (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" {...p}>
      <path d="M12 3l1.9 5.1L19 10l-5.1 1.9L12 17l-1.9-5.1L5 10l5.1-1.9z" />
      <path d="M18.5 15.5l.8 2.2 2.2.8-2.2.8-.8 2.2-.8-2.2-2.2-.8 2.2-.8z" />
    </svg>
  ),
  Check: (p: any) => (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" {...p}>
      <path d="M4.5 12.5l5 5 10-11" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  ),
  X: (p: any) => (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" {...p}>
      <path d="M6 6l12 12M18 6L6 18" strokeLinecap="round" />
    </svg>
  ),
  Plus: (p: any) => (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" {...p}>
      <path d="M12 5v14M5 12h14" strokeLinecap="round" />
    </svg>
  ),
  Chevron: (p: any) => (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" {...p}>
      <path d="M9 6l6 6-6 6" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  ),
  Warn: (p: any) => (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" {...p}>
      <path d="M12 4.5l9 15.5H3z" strokeLinejoin="round" />
      <path d="M12 10v4M12 17h.01" strokeLinecap="round" />
    </svg>
  ),
  Move: (p: any) => (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" {...p}>
      <path
        d="M12 3v18M3 12h18M12 3l-2.5 2.5M12 3l2.5 2.5M12 21l-2.5-2.5M12 21l2.5-2.5M3 12l2.5-2.5M3 12l2.5 2.5M21 12l-2.5-2.5M21 12l-2.5 2.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  ),
  Trash: (p: any) => (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" {...p}>
      <path d="M4 7h16M9 7V5h6v2M6.5 7l1 13h9l1-13" strokeLinecap="round" />
    </svg>
  ),
};
