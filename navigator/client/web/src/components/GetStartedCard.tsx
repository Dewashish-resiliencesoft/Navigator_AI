import { useCallback, useEffect, useState } from "react";
import { Rocket } from "lucide-react";
import {
  hideOnboardingCard,
  isOnboardingCardHidden,
  loadOnboardingProgress,
  type OnboardingItemId,
  type OnboardingProgress,
} from "../lib/onboarding";
import { useProductData } from "../lib/productData";
import { Button } from "./ui";

export function GetStartedCard({
  onContinue,
}: {
  onContinue: (startAt: OnboardingItemId | null) => void;
}) {
  const epoch = useProductData((s) => s.epoch);
  const [progress, setProgress] = useState<OnboardingProgress | null>(null);
  const [hidden, setHidden] = useState(() => isOnboardingCardHidden());

  const refresh = useCallback(async () => {
    try {
      const p = await loadOnboardingProgress();
      setProgress(p);
      if (p.complete) setHidden(true);
    } catch {
      /* ignore */
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh, epoch]);

  if (hidden || !progress || progress.complete) return null;

  const left = progress.items.filter((i) => !i.done);

  return (
    <div
      className="rounded-xl border p-3"
      style={{
        borderColor: "var(--line)",
        background: "color-mix(in oklab, var(--accent) 8%, transparent)",
      }}
    >
      <div className="flex items-start gap-2">
        <Rocket size={14} className="mt-0.5 shrink-0 text-[var(--accent)]" />
        <div className="min-w-0 flex-1">
          <p className="text-[0.78rem] font-semibold tracking-tight">Get started</p>
          <p className="mt-0.5 text-[0.68rem] text-[var(--muted)]">
            Complete setup · {progress.percent}%
          </p>
          <div
            className="mt-2 h-1.5 overflow-hidden rounded-full"
            style={{
              background: "color-mix(in oklab, var(--line) 80%, transparent)",
            }}
          >
            <div
              className="h-full rounded-full bg-[var(--accent)]"
              style={{ width: `${progress.percent}%` }}
            />
          </div>
          {left.length > 0 && (
            <ul className="mt-2 space-y-0.5 text-[0.68rem] text-[var(--muted)]">
              {left.slice(0, 4).map((i) => (
                <li key={i.id}>
                  · {i.label}
                  {i.optional ? " (optional)" : ""}
                </li>
              ))}
              {left.length > 4 && <li>· +{left.length - 4} more</li>}
            </ul>
          )}
          <div className="mt-2.5 flex flex-wrap gap-1.5">
            <Button
              variant="secondary"
              onClick={() => onContinue(left[0]?.id ?? null)}
            >
              Continue setup
            </Button>
            <Button
              variant="ghost"
              onClick={() => {
                hideOnboardingCard();
                setHidden(true);
              }}
            >
              Hide
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
}
