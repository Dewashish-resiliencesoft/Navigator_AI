/** Shared Product Explore explainer + checklist + start/stop — compact by default. */

import { Loader2 } from "lucide-react";
import { Button, Card, CardTitle } from "./ui";
import { StatusChecklist } from "./StatusChecklist";
import { errText, useUi } from "../store";
import {
  productExploreIsLive,
  useProductExploreSession,
} from "../lib/productExploreSession";

const WHY = [
  {
    title: "Why",
    body: "Agent gets real product context without hand-writing every page.",
  },
  {
    title: "Flow",
    body: "Login → crawl product → brain-guided public web → fill bio + notes + map.",
  },
  {
    title: "Output",
    body: "Explore MD, bio gaps, read-only map, merged canonical knowledge.",
  },
  {
    title: "Not for",
    body: "Not the live walkthrough. Manual record still owns End User demos.",
  },
];

export function ProductExplorePanel({
  showOpenKnowledge = false,
  className,
}: {
  showOpenKnowledge?: boolean;
  className?: string;
}) {
  const { ok, err, setTab } = useUi();
  const status = useProductExploreSession((s) => s.status);
  const starting = useProductExploreSession((s) => s.starting);
  const stopping = useProductExploreSession((s) => s.stopping);
  const start = useProductExploreSession((s) => s.start);
  const stop = useProductExploreSession((s) => s.stop);
  const live = productExploreIsLive(status);
  const busy = starting || stopping || live;

  const onStart = async () => {
    try {
      await start();
      ok("Product Explore started — you can keep using the dashboard.");
    } catch (e) {
      err(errText(e));
    }
  };

  const onStop = async () => {
    try {
      await stop();
      ok("Product Explore stopped.");
    } catch (e) {
      err(errText(e));
    }
  };

  return (
    <Card className={className} interactive={false} dataCoach="knowledge-explore">
      <CardTitle
        hint="Login crawl → web enrich → bio + notes + map."
        right={
          live ? (
            <Button variant="danger" onClick={() => void onStop()} disabled={stopping}>
              {stopping ? (
                <>
                  <Loader2 size={14} className="animate-spin" /> Stopping…
                </>
              ) : (
                "Stop"
              )}
            </Button>
          ) : (
            <Button onClick={() => void onStart()} disabled={busy}>
              {starting ? (
                <>
                  <Loader2 size={14} className="animate-spin" /> Starting…
                </>
              ) : (
                "Start Product Explore"
              )}
            </Button>
          )
        }
      >
        Product Explore
      </CardTitle>

      <div className="mb-3 grid grid-cols-2 gap-1.5">
        {WHY.map((row) => (
          <div
            key={row.title}
            className="rounded-lg border px-2.5 py-2"
            style={{ borderColor: "var(--line)" }}
          >
            <p className="text-[0.65rem] font-semibold uppercase tracking-[0.06em] text-[var(--muted)]">
              {row.title}
            </p>
            <p className="mt-0.5 text-[0.72rem] leading-snug text-[var(--text)]">{row.body}</p>
          </div>
        ))}
      </div>

      {live && (
        <p className="mb-2 text-[0.72rem] text-sky-700 dark:text-sky-300" aria-live="polite">
          Running — bottom-right card tracks the live site.
        </p>
      )}

      <StatusChecklist items={status.artifacts ?? []} className="mb-2" />

      {status.error ? (
        <p className="mb-2 text-[0.72rem] text-amber-700 dark:text-amber-400" role="alert">
          {status.error}
        </p>
      ) : null}

      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[0.72rem]">
        {showOpenKnowledge && (
          <button
            type="button"
            className="cursor-pointer font-medium text-[var(--accent)] underline-offset-2 hover:underline"
            onClick={() => setTab("knowledge")}
          >
            Open Knowledge
          </button>
        )}
        <button
          type="button"
          className="cursor-pointer font-medium text-[var(--accent)] underline-offset-2 hover:underline"
          onClick={() => setTab("settings")}
        >
          Product Login
        </button>
      </div>
    </Card>
  );
}
