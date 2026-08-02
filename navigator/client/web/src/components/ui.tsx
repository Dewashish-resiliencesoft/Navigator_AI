import { motion, useMotionTemplate, useMotionValue } from "motion/react";
import type { ReactNode } from "react";
import { cn } from "../lib/cn";
import { cardHover, rise, soft } from "../lib/motion";

/** Bento card. Mouse-tracked mesh light expands under the border on hover. */
export function Card({
  children,
  className,
  span,
  interactive = true,
  onClick,
}: {
  children: ReactNode;
  className?: string;
  span?: string;
  interactive?: boolean;
  onClick?: () => void;
}) {
  const mx = useMotionValue(-200);
  const my = useMotionValue(-200);
  const glare = useMotionTemplate`radial-gradient(340px circle at ${mx}px ${my}px, color-mix(in oklch, var(--accent) 14%, transparent), transparent 70%)`;

  return (
    <motion.section
      variants={rise}
      {...(interactive ? cardHover : {})}
      onClick={onClick}
      onPointerMove={(e) => {
        const r = e.currentTarget.getBoundingClientRect();
        mx.set(e.clientX - r.left);
        my.set(e.clientY - r.top);
      }}
      onPointerLeave={() => {
        mx.set(-200);
        my.set(-200);
      }}
      className={cn(
        "group relative overflow-hidden rounded-xl border p-5",
        "bg-white/60 dark:bg-white/[0.02] backdrop-blur-md",
        "shadow-[0_1px_2px_rgba(0,0,0,0.04)] hover:shadow-[0_12px_40px_-12px_rgba(0,0,0,0.25)]",
        onClick && "cursor-pointer",
        span,
        className,
      )}
      style={{ borderColor: "var(--line)" }}
    >
      <motion.div
        aria-hidden
        className="pointer-events-none absolute inset-0 opacity-0 group-hover:opacity-100"
        style={{ background: glare, transition: "opacity 260ms ease" }}
      />
      <div className="relative">{children}</div>
    </motion.section>
  );
}

export function CardTitle({
  children,
  hint,
  right,
}: {
  children: ReactNode;
  hint?: string;
  right?: ReactNode;
}) {
  return (
    <div className="mb-4 flex items-start justify-between gap-3">
      <div>
        <h2 className="text-[0.7rem] font-semibold uppercase tracking-[0.09em] text-[var(--muted)]">
          {children}
        </h2>
        {hint && (
          <p className="mt-1.5 max-w-prose text-[0.78rem] leading-relaxed text-[var(--muted)]">
            {hint}
          </p>
        )}
      </div>
      {right}
    </div>
  );
}

type ButtonVariant = "primary" | "secondary" | "ghost" | "danger";

const variants: Record<ButtonVariant, string> = {
  primary:
    "bg-[var(--text)] text-[var(--bg)] hover:opacity-90 disabled:opacity-40",
  secondary:
    "border bg-transparent hover:bg-black/[0.04] dark:hover:bg-white/[0.06] disabled:opacity-40",
  ghost:
    "bg-transparent text-[var(--muted)] hover:text-[var(--text)] hover:bg-black/[0.04] dark:hover:bg-white/[0.06] disabled:opacity-30",
  danger:
    "bg-transparent border border-red-500/30 text-red-500 hover:bg-red-500/10 disabled:opacity-40",
};

export function Button({
  children,
  onClick,
  variant = "primary",
  disabled,
  className,
  type = "button",
}: {
  children: ReactNode;
  onClick?: () => void;
  variant?: ButtonVariant;
  disabled?: boolean;
  className?: string;
  type?: "button" | "submit";
}) {
  return (
    <motion.button
      type={type}
      onClick={onClick}
      disabled={disabled}
      whileHover={disabled ? undefined : { y: -1 }}
      whileTap={disabled ? undefined : { scale: 0.98 }}
      transition={soft}
      className={cn(
        "inline-flex items-center justify-center gap-1.5 rounded-lg px-3.5 py-2",
        "text-[0.82rem] font-medium tracking-tight",
        "disabled:cursor-not-allowed",
        "focus-visible:ring-2 focus-visible:ring-[var(--accent)]/50 focus-visible:outline-none",
        variants[variant],
        className,
      )}
      style={{ borderColor: "var(--line)" }}
    >
      {children}
    </motion.button>
  );
}

const fieldBase =
  "w-full rounded-lg border bg-white/50 dark:bg-black/20 px-3 py-2 text-[0.85rem] " +
  "placeholder:text-[var(--muted)]/70 outline-none " +
  "focus:border-[var(--accent)] focus:ring-2 focus:ring-[var(--accent)]/20";

export function Field({
  label,
  children,
}: {
  label: string;
  children: ReactNode;
}) {
  return (
    <label className="mb-3 block">
      <span className="mb-1.5 block text-[0.74rem] font-medium tracking-wide text-[var(--muted)]">
        {label}
      </span>
      {children}
    </label>
  );
}

export function Input({
  value,
  onChange,
  placeholder,
  className,
  type = "text",
  required,
  autoComplete,
  name,
  disabled,
}: {
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  className?: string;
  type?: string;
  required?: boolean;
  autoComplete?: string;
  name?: string;
  disabled?: boolean;
}) {
  return (
    <input
      type={type}
      name={name}
      value={value}
      placeholder={placeholder}
      required={required}
      autoComplete={autoComplete}
      disabled={disabled}
      onChange={(e) => onChange(e.target.value)}
      className={cn(fieldBase, className)}
      style={{ borderColor: "var(--line)" }}
    />
  );
}

export function Textarea({
  value,
  onChange,
  placeholder,
  rows = 4,
  mono,
  className,
}: {
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  rows?: number;
  mono?: boolean;
  className?: string;
}) {
  return (
    <textarea
      value={value}
      rows={rows}
      placeholder={placeholder}
      onChange={(e) => onChange(e.target.value)}
      className={cn(fieldBase, "resize-y", mono && "font-mono text-[0.78rem]", className)}
      style={{ borderColor: "var(--line)" }}
    />
  );
}

export function Select({
  value,
  onChange,
  options,
}: {
  value: string;
  onChange: (v: string) => void;
  options: { value: string; label: string }[];
}) {
  return (
    <select
      value={value}
      onChange={(e) => onChange(e.target.value)}
      className={cn(fieldBase, "appearance-none pr-8")}
      style={{ borderColor: "var(--line)" }}
    >
      {options.map((o) => (
        <option key={o.value} value={o.value}>
          {o.label}
        </option>
      ))}
    </select>
  );
}

const dotTone: Record<string, string> = {
  idle: "bg-[var(--muted)]",
  starting: "bg-amber-400",
  recording: "bg-red-500",
  running: "bg-emerald-400",
  finished: "bg-sky-400",
  failed: "bg-red-500",
};

export function StatusPill({ status, label }: { status: string; label?: string }) {
  const live = status === "running" || status === "starting" || status === "recording";
  const desc = status === "starting" ? "Initializing and opening browser" :
               status === "running" ? "Agent is driving the browser" :
               status === "finished" ? "Demo completed normally" :
               status === "failed" ? "Demo ended with an error" :
               status === "recording" ? "Recording a new flow" : "Idle";
  return (
    <Tooltip content={desc}>
      <div
        className="inline-flex items-center gap-2 rounded-full border px-3 py-1.5 text-[0.76rem] font-medium tracking-tight"
      style={{ borderColor: "var(--line)" }}
    >
      <span className="relative flex h-2 w-2">
        {live && (
          <motion.span
            className={cn("absolute inset-0 rounded-full", dotTone[status])}
            animate={{ scale: [1, 2.4], opacity: [0.6, 0] }}
            transition={{ duration: 1.4, repeat: Infinity, ease: "easeOut" }}
          />
        )}
        <span
          className={cn("relative h-2 w-2 rounded-full", dotTone[status] ?? dotTone.idle)}
        />
      </span>
      {label ?? status}
      </div>
    </Tooltip>
  );
}

/** Kinetic bar loader — three slim bars that stretch and retract in rhythm. */
export function BarLoader({ label }: { label?: string }) {
  return (
    <div className="flex items-center gap-2.5 py-1">
      <div className="flex items-center gap-1">
        {[0, 1, 2].map((i) => (
          <motion.span
            key={i}
            className="h-[3px] rounded-full bg-[var(--accent)]"
            animate={{ width: [8, 22, 8], opacity: [0.35, 1, 0.35] }}
            transition={{
              duration: 1.1,
              repeat: Infinity,
              ease: "easeInOut",
              delay: i * 0.14,
            }}
          />
        ))}
      </div>
      {label && <span className="text-[0.76rem] text-[var(--muted)]">{label}</span>}
    </div>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return (
    <p className="py-2 text-[0.82rem] text-[var(--muted)]">{children}</p>
  );
}

import { useState } from "react";
import { AnimatePresence } from "motion/react";

export function Tooltip({ children, content }: { children: ReactNode, content: string }) {
  const [show, setShow] = useState(false);
  return (
    <span className="relative inline-block" onMouseEnter={() => setShow(true)} onMouseLeave={() => setShow(false)}>
      {children}
      <AnimatePresence>
        {show && (
          <motion.div
            initial={{ opacity: 0, y: 5 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: 5 }}
            transition={{ duration: 0.15 }}
            className="absolute bottom-full left-1/2 z-50 mb-2 w-max -translate-x-1/2 rounded-md bg-[var(--text)] px-2.5 py-1.5 text-[0.72rem] text-[var(--bg)] shadow-md"
          >
            {content}
            <div className="absolute left-1/2 top-full -mt-[1px] h-0 w-0 -translate-x-1/2 border-[5px] border-transparent border-t-[var(--text)]" />
          </motion.div>
        )}
      </AnimatePresence>
    </span>
  );
}

export function ConfirmDialog({
  title,
  message,
  onConfirm,
  onCancel,
  confirmLabel,
  danger,
}: {
  title: string;
  message: string;
  onConfirm: () => void;
  onCancel: () => void;
  confirmLabel?: string;
  danger?: boolean;
}) {
  return (
    <div className="fixed inset-0 z-[100] flex items-center justify-center bg-black/40 p-4 backdrop-blur-sm">
      <motion.div
        initial={{ opacity: 0, scale: 0.95 }}
        animate={{ opacity: 1, scale: 1 }}
        exit={{ opacity: 0, scale: 0.95 }}
        transition={soft}
        className="w-full max-w-[400px] overflow-hidden rounded-xl border bg-[var(--panel)] shadow-xl"
        style={{ borderColor: "var(--line)" }}
      >
        <div className="p-6">
          <h3 className="mb-2 text-[1.1rem] font-semibold tracking-tight">{title}</h3>
          <p className="text-[0.85rem] leading-relaxed text-[var(--muted)]">{message}</p>
        </div>
        <div
          className="flex justify-end gap-2 border-t bg-black/[0.02] p-4 dark:bg-white/[0.02]"
          style={{ borderColor: "var(--line)" }}
        >
          <Button variant="ghost" onClick={onCancel}>
            Cancel
          </Button>
          <Button variant={danger ? "danger" : "primary"} onClick={onConfirm}>
            {confirmLabel ?? "Confirm"}
          </Button>
        </div>
      </motion.div>
    </div>
  );
}
