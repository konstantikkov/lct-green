import { useEffect, useRef, useState, type ReactNode } from "react";

export function Section({
  title, children, defaultOpen = true, right,
}: {
  title: string;
  children: ReactNode;
  defaultOpen?: boolean;
  right?: ReactNode;
}) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className="border-b border-ink-600">
      <button
        className="flex h-8 w-full items-center gap-1.5 px-2 text-2xs font-semibold uppercase tracking-wider text-ink-300 hover:text-ink-100"
        onClick={() => setOpen((o) => !o)}
      >
        <span className="inline-block w-3 text-ink-400">{open ? "▾" : "▸"}</span>
        <span className="flex-1 text-left">{title}</span>
        {right}
      </button>
      {open && <div className="px-2 pb-2">{children}</div>}
    </div>
  );
}

export function Btn({
  children, onClick, disabled, variant = "default", className = "", title,
  "data-testid": testid,
}: {
  children: ReactNode;
  onClick?: () => void;
  disabled?: boolean;
  variant?: "default" | "primary" | "ghost";
  className?: string;
  title?: string;
  "data-testid"?: string;
}) {
  const base =
    "inline-flex items-center justify-center gap-1.5 border px-2.5 h-7 text-xs rounded-sm transition-colors disabled:opacity-40 disabled:cursor-not-allowed";
  const styles = {
    default: "border-ink-600 bg-ink-800 text-ink-200 hover:bg-ink-700 hover:text-ink-100",
    primary:
      "border-emerald-300 bg-emerald-50 text-emerald-800 hover:bg-emerald-100 " +
      "dark:border-emerald-700 dark:bg-emerald-900/60 dark:text-emerald-200 dark:hover:bg-emerald-800/60",
    ghost: "border-transparent text-ink-300 hover:text-ink-100 hover:bg-ink-800",
  } as const;
  return (
    <button
      className={`${base} ${styles[variant]} ${className}`}
      onClick={onClick}
      disabled={disabled}
      title={title}
      data-testid={testid}
    >
      {children}
    </button>
  );
}

export function Badge({
  children, tone = "grey", title, onClick,
}: {
  children: ReactNode;
  tone?: "grey" | "green" | "amber" | "red" | "blue" | "violet";
  title?: string;
  onClick?: () => void;
}) {
  const tones = {
    grey: "border-ink-500 text-ink-300 bg-ink-800",
    green: "border-emerald-300 text-emerald-800 bg-emerald-50 dark:border-emerald-700 dark:text-emerald-300 dark:bg-emerald-950/70",
    amber: "border-amber-300 text-amber-800 bg-amber-50 dark:border-amber-600 dark:text-amber-300 dark:bg-amber-950/60",
    red: "border-red-300 text-red-800 bg-red-50 dark:border-red-700 dark:text-red-300 dark:bg-red-950/60",
    blue: "border-sky-300 text-sky-800 bg-sky-50 dark:border-sky-700 dark:text-sky-300 dark:bg-sky-950/60",
    violet: "border-violet-300 text-violet-800 bg-violet-50 dark:border-violet-700 dark:text-violet-300 dark:bg-violet-950/60",
  } as const;
  const cls = `inline-flex items-center border px-1 py-px rounded-sm text-2xs leading-4 whitespace-nowrap ${tones[tone]} ${onClick ? "cursor-pointer hover:brightness-125" : ""}`;
  return (
    <span className={cls} title={title} onClick={onClick}>
      {children}
    </span>
  );
}

export function Checkbox({
  checked, onChange, label,
}: {
  checked: boolean;
  onChange: (v: boolean) => void;
  label?: ReactNode;
}) {
  return (
    <label className="flex cursor-pointer select-none items-center gap-1.5 text-xs text-ink-200 hover:text-ink-100">
      <input
        type="checkbox"
        checked={checked}
        onChange={(e) => onChange(e.target.checked)}
        className="h-3 w-3 accent-emerald-600"
      />
      {label}
    </label>
  );
}

export function TextInput({
  value, onChange, placeholder, className = "", type = "text",
}: {
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  className?: string;
  type?: string;
}) {
  return (
    <input
      type={type}
      value={value}
      placeholder={placeholder}
      onChange={(e) => onChange(e.target.value)}
      className={`h-7 w-full rounded-sm border border-ink-600 bg-ink-900 px-2 font-mono text-xs text-ink-100 placeholder-ink-500 outline-none focus:border-ink-400 ${className}`}
    />
  );
}

export function Select({
  value, onChange, options, className = "",
}: {
  value: string;
  onChange: (v: string) => void;
  options: { value: string; label: string }[];
  className?: string;
}) {
  return (
    <select
      value={value}
      onChange={(e) => onChange(e.target.value)}
      className={`h-7 w-full rounded-sm border border-ink-600 bg-ink-900 px-1.5 text-xs text-ink-100 outline-none focus:border-ink-400 ${className}`}
    >
      {options.map((o) => (
        <option key={o.value} value={o.value}>{o.label}</option>
      ))}
    </select>
  );
}

/** Minimal dropdown anchored to its trigger; closes on outside click / Esc. */
export function Dropdown({
  trigger, open, onOpenChange, children,
}: {
  trigger: ReactNode;
  open: boolean;
  onOpenChange: (o: boolean) => void;
  children: ReactNode;
}) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) onOpenChange(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onOpenChange(false);
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open, onOpenChange]);
  return (
    <div className="relative" ref={ref}>
      <span onClick={() => onOpenChange(!open)}>{trigger}</span>
      {open && (
        <div className="absolute right-0 top-full z-50 mt-1 max-h-64 w-52 overflow-y-auto rounded-sm border border-ink-500 bg-ink-850 py-1 shadow-xl shadow-black/15 dark:shadow-black/50">
          {children}
        </div>
      )}
    </div>
  );
}

export function Spinner({ className = "" }: { className?: string }) {
  return (
    <span
      className={`inline-block h-3 w-3 animate-spin rounded-full border border-ink-400 border-t-transparent ${className}`}
    />
  );
}

/** Compact horizontal metric bar: label · bar · percentage. */
export function MetricBar({
  label, value, invert = false, title,
}: {
  label: string;
  /** 0..1 */
  value: number;
  /** true = lower is better (e.g. penalties) — colors flip. */
  invert?: boolean;
  title?: string;
}) {
  const pct = Math.round(Math.max(0, Math.min(1, value)) * 100);
  const good = invert ? pct <= 30 : pct >= 60;
  return (
    <div className="flex items-center gap-2" title={title}>
      <span className="w-24 shrink-0 truncate text-2xs text-ink-400">{label}</span>
      <div className="h-1.5 min-w-0 flex-1 overflow-hidden rounded-full bg-ink-700">
        <div
          className={`h-full rounded-full ${good ? "bg-emerald-500/80" : "bg-amber-500/80"}`}
          style={{ width: `${pct}%` }}
        />
      </div>
      <span className="w-8 shrink-0 text-right font-mono text-2xs text-ink-300">{pct}%</span>
    </div>
  );
}

export function KV({ k, v, mono = true }: { k: string; v: ReactNode; mono?: boolean }) {
  return (
    <div className="flex items-baseline justify-between gap-2 py-0.5">
      <span className="shrink-0 text-2xs text-ink-400">{k}</span>
      <span className={`text-right text-xs text-ink-100 ${mono ? "font-mono" : ""}`}>{v}</span>
    </div>
  );
}
