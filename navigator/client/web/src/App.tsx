import { useEffect, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import { AlertCircle, CheckCircle2, Moon, Sun, X } from "lucide-react";
import { MobileTabs, Sidebar, TABS } from "./components/Sidebar";
import { Overview } from "./panels/Overview";
import { LiveDemo } from "./panels/LiveDemo";
import { Logs } from "./panels/Logs";
import { Flows } from "./panels/Flows";
import { Bio, Knowledge, SiteGraph } from "./panels/Editors";
import { spring } from "./lib/motion";
import { useUi } from "./store";

const PANELS: Record<string, () => React.ReactElement> = {
  overview: Overview,
  demo: LiveDemo,
  logs: Logs,
  flows: Flows,
  graph: SiteGraph,
  knowledge: Knowledge,
  bio: Bio,
};

/** Per-word blur reveal for the greeting. */
function BlurText({ text }: { text: string }) {
  return (
    <span className="inline-flex flex-wrap gap-x-[0.28em]">
      {text.split(" ").map((word, i) => (
        <motion.span
          key={`${word}-${i}`}
          initial={{ opacity: 0, y: 10, filter: "blur(8px)" }}
          animate={{ opacity: 1, y: 0, filter: "blur(0px)" }}
          transition={{ ...spring, delay: i * 0.05 }}
        >
          {word}
        </motion.span>
      ))}
    </span>
  );
}

function Toast() {
  const { toast, clear } = useUi();
  useEffect(() => {
    if (!toast) return;
    const t = setTimeout(clear, 6000);
    return () => clearTimeout(t);
  }, [toast, clear]);

  return (
    <AnimatePresence>
      {toast && (
        <motion.div
          initial={{ opacity: 0, y: 16, filter: "blur(6px)" }}
          animate={{ opacity: 1, y: 0, filter: "blur(0px)" }}
          exit={{ opacity: 0, y: 10, filter: "blur(4px)" }}
          transition={spring}
          className="fixed bottom-5 left-1/2 z-50 flex max-w-[92vw] -translate-x-1/2 items-center gap-2.5 rounded-xl border px-4 py-2.5 backdrop-blur-xl"
          style={{
            borderColor: "var(--line)",
            background: "color-mix(in oklch, var(--panel) 82%, transparent)",
          }}
        >
          {toast.kind === "ok" ? (
            <CheckCircle2 size={15} className="shrink-0 text-emerald-500" />
          ) : (
            <AlertCircle size={15} className="shrink-0 text-red-500" />
          )}
          <span className="text-[0.81rem] leading-snug">{toast.text}</span>
          <button onClick={clear} className="ml-1 text-[var(--muted)] hover:text-[var(--text)]">
            <X size={14} />
          </button>
        </motion.div>
      )}
    </AnimatePresence>
  );
}

function useTheme() {
  const [dark, setDark] = useState(
    () => localStorage.getItem("nav-theme") !== "light",
  );
  useEffect(() => {
    document.documentElement.classList.toggle("dark", dark);
    localStorage.setItem("nav-theme", dark ? "dark" : "light");
  }, [dark]);
  return { dark, toggle: () => setDark((d) => !d) };
}


import { api } from "./lib/api";

function Login({ onLogin }: { onLogin: () => void }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    setLoading(true);
    try {
      await api.login(email, password);
      onLogin();
    } catch (err: any) {
      setError(err.message || "Login failed");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="flex min-h-screen items-center justify-center p-4">
      <div className="w-full max-w-sm rounded-xl border p-6 shadow-xl" style={{ borderColor: "var(--line)", background: "var(--panel)" }}>
        <h2 className="mb-6 text-2xl font-bold">Log In to Navigator</h2>
        <form onSubmit={handleSubmit} className="flex flex-col gap-4">
          <div>
            <label className="mb-1 block text-sm text-[var(--muted)]">Email</label>
            <input type="email" value={email} onChange={e => setEmail(e.target.value)} required className="w-full rounded-md border p-2" style={{ borderColor: "var(--line)", background: "var(--bg)", color: "var(--text)" }} />
          </div>
          <div>
            <label className="mb-1 block text-sm text-[var(--muted)]">Password</label>
            <input type="password" value={password} onChange={e => setPassword(e.target.value)} required className="w-full rounded-md border p-2" style={{ borderColor: "var(--line)", background: "var(--bg)", color: "var(--text)" }} />
          </div>
          {error && <div className="text-sm text-red-500">{error}</div>}
          <button type="submit" disabled={loading} className="mt-2 rounded-md bg-blue-600 py-2 font-medium text-white hover:bg-blue-700 disabled:opacity-50">
            {loading ? "Logging in..." : "Log In"}
          </button>
        </form>
      </div>
    </div>
  );
}


export default function App() {
  const [authed, setAuthed] = useState<boolean | null>(null);
  useEffect(() => { api.checkAuth().then(ok => setAuthed(ok)); }, []);
  if (authed === null) return null;
  if (authed === false) return <Login onLogin={() => setAuthed(true)} />;
  const tab = useUi((s) => s.tab);
  const { dark, toggle } = useTheme();
  const Panel = PANELS[tab] ?? Overview;
  const title = TABS.find((t) => t.id === tab)?.label ?? "Overview";

  return (
    <div className="flex min-h-screen">
      <Sidebar />
      <div className="min-w-0 flex-1">
        <header
          className="sticky top-0 z-30 border-b backdrop-blur-md"
          style={{
            borderColor: "var(--line)",
            background: "color-mix(in oklch, var(--bg) 72%, transparent)",
          }}
        >
          <div className="flex items-center justify-between gap-4 px-5 py-4 md:px-8 md:py-5">
            <div className="min-w-0">
              <h1 className="truncate text-[1.35rem] font-semibold tracking-tighter md:text-[1.6rem]">
                <BlurText text={title} />
              </h1>
              <p className="mt-1 text-[0.79rem] text-[var(--muted)]">
                Configure demos, flows, and knowledge for your product.
              </p>
            </div>
            <button
              onClick={toggle}
              aria-label="Toggle theme"
              className="shrink-0 rounded-lg border p-2 text-[var(--muted)] hover:text-[var(--text)]"
              style={{ borderColor: "var(--line)" }}
            >
              {dark ? <Sun size={15} /> : <Moon size={15} />}
            </button>
          </div>
          <MobileTabs />
        </header>

        <main className="px-5 py-6 md:px-8 md:py-8">
          <AnimatePresence mode="wait">
            <motion.div
              key={tab}
              initial={{ opacity: 0, y: 10 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -6 }}
              transition={spring}
            >
              <Panel />
            </motion.div>
          </AnimatePresence>
        </main>
      </div>
      <Toast />
    </div>
  );
}
