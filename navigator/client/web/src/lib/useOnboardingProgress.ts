import { useCallback, useEffect, useState } from "react";
import {
  loadOnboardingProgress,
  type OnboardingProgress,
} from "./onboarding";
import { useProductData } from "./productData";

/** Shared checklist progress for sidebar card + onboarding modal. */
export function useOnboardingProgress() {
  const epoch = useProductData((s) => s.epoch);
  const [progress, setProgress] = useState<OnboardingProgress | null>(null);

  const refresh = useCallback(async () => {
    try {
      const p = await loadOnboardingProgress();
      setProgress(p);
      return p;
    } catch {
      return null;
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh, epoch]);

  return { progress, refresh };
}
