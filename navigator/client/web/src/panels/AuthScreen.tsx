import { useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import { Moon, Network, Sun } from "lucide-react";
import { api } from "../lib/api";
import { spring } from "../lib/motion";
import { Button, Field, Input } from "../components/ui";
import { errText } from "../store";

type Mode = "login" | "signup";

export function AuthScreen({
  onAuthed,
  dark,
  toggleTheme,
}: {
  onAuthed: () => void;
  dark: boolean;
  toggleTheme: () => void;
}) {
  const [mode, setMode] = useState<Mode>("login");
  const [company, setCompany] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    setLoading(true);
    try {
      if (mode === "signup") {
        await api.signup(company.trim(), email.trim(), password);
      } else {
        await api.login(email.trim(), password);
      }
      onAuthed();
    } catch (err) {
      setError(errText(err));
    } finally {
      setLoading(false);
    }
  };

  return (
    <div
      className="relative flex min-h-screen items-center justify-center px-4"
      style={{
        background:
          "radial-gradient(1200px 600px at 10% -10%, color-mix(in oklch, var(--accent) 18%, transparent), transparent 55%), var(--bg)",
      }}
    >
      <button
        type="button"
        onClick={toggleTheme}
        aria-label="Toggle theme"
        className="absolute right-5 top-5 rounded-lg border p-2 text-[var(--muted)] hover:text-[var(--text)]"
        style={{ borderColor: "var(--line)" }}
      >
        {dark ? <Sun size={15} /> : <Moon size={15} />}
      </button>

      <motion.div
        initial={{ opacity: 0, y: 14, filter: "blur(8px)" }}
        animate={{ opacity: 1, y: 0, filter: "blur(0px)" }}
        transition={spring}
        className="w-full max-w-[400px] rounded-2xl border p-7 backdrop-blur-md"
        style={{
          borderColor: "var(--line)",
          background: "color-mix(in oklch, var(--panel) 88%, transparent)",
        }}
      >
        <div className="mb-6 flex items-center gap-2.5">
          <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-[var(--text)] text-[var(--bg)]">
            <Network size={16} strokeWidth={2.2} />
          </div>
          <div className="leading-tight">
            <p className="text-[0.95rem] font-semibold tracking-tight">Navigator AI</p>
            <p className="text-[0.72rem] text-[var(--muted)]">Client console</p>
          </div>
        </div>

        <div
          className="mb-5 flex rounded-lg border p-0.5"
          style={{ borderColor: "var(--line)" }}
        >
          {(["login", "signup"] as Mode[]).map((m) => (
            <button
              key={m}
              type="button"
              onClick={() => {
                setMode(m);
                setError("");
              }}
              className="relative flex-1 rounded-md px-3 py-1.5 text-[0.8rem] font-medium"
            >
              {mode === m && (
                <motion.span
                  layoutId="auth-mode"
                  className="absolute inset-0 rounded-md border bg-black/[0.04] dark:bg-white/[0.08]"
                  style={{ borderColor: "var(--line)" }}
                  transition={spring}
                />
              )}
              <span className="relative text-[var(--text)]">
                {m === "login" ? "Log in" : "Sign up"}
              </span>
            </button>
          ))}
        </div>

        <AnimatePresence mode="wait">
          <motion.form
            key={mode}
            initial={{ opacity: 0, y: 6 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -4 }}
            transition={spring}
            onSubmit={submit}
          >
            {mode === "signup" && (
              <Field label="Company name">
                <Input
                  value={company}
                  onChange={setCompany}
                  required
                  name="company"
                  autoComplete="organization"
                  placeholder="Acme Inc."
                />
              </Field>
            )}
            <Field label="Email">
              <Input
                type="email"
                value={email}
                onChange={setEmail}
                required
                name="email"
                autoComplete="email"
                placeholder="you@company.com"
              />
            </Field>
            <Field label="Password">
              <Input
                type="password"
                value={password}
                onChange={setPassword}
                required
                name="password"
                autoComplete={mode === "signup" ? "new-password" : "current-password"}
                placeholder={mode === "signup" ? "At least 8 characters" : "••••••••"}
              />
            </Field>

            {error && (
              <p className="mb-3 text-[0.78rem] text-red-600 dark:text-red-400">{error}</p>
            )}

            <Button type="submit" disabled={loading} className="mt-1 w-full">
              {loading
                ? mode === "signup"
                  ? "Creating account…"
                  : "Signing in…"
                : mode === "signup"
                  ? "Create account"
                  : "Log in"}
            </Button>
          </motion.form>
        </AnimatePresence>

        <p className="mt-5 text-center text-[0.72rem] leading-relaxed text-[var(--muted)]">
          {mode === "login"
            ? "New company? Switch to Sign up to create your tenant."
            : "Creates your product workspace and admin login."}
        </p>
      </motion.div>
    </div>
  );
}
