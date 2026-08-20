/** Spotlight coachmark — dims page, rings [data-coach], shows tip. */

import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { AnimatePresence, motion, type Variants } from "motion/react";
import { X } from "lucide-react";
import { useUi } from "../store";
import { soft } from "../lib/motion";

const PAD = 8;
const AUTO_MS = 25_000;
const DIM = "rgba(15, 23, 42, 0.55)";

type Box = { top: number; left: number; width: number; height: number };

function readBox(el: Element): Box {
  const r = el.getBoundingClientRect();
  return {
    top: Math.max(0, r.top - PAD),
    left: Math.max(0, r.left - PAD),
    width: r.width + PAD * 2,
    height: r.height + PAD * 2,
  };
}

/** Cascade enter/exit — blur + fade (no shrink). */
const shell: Variants = {
  show: { transition: { staggerChildren: 0.03, delayChildren: 0.02 } },
  hidden: { transition: { staggerChildren: 0.025, staggerDirection: -1 } },
};

const fadeBlur: Variants = {
  hidden: {
    opacity: 0,
    filter: "blur(10px)",
    transition: { ...soft, duration: 0.28 },
  },
  show: {
    opacity: 1,
    filter: "blur(0px)",
    transition: soft,
  },
};

const tipBlur: Variants = {
  hidden: {
    opacity: 0,
    filter: "blur(12px)",
    y: 6,
    transition: { ...soft, duration: 0.28 },
  },
  show: {
    opacity: 1,
    filter: "blur(0px)",
    y: 0,
    transition: soft,
  },
};

export function CoachSpotlight() {
  const coach = useUi((s) => s.coach);
  const clearCoach = useUi((s) => s.clearCoach);
  const [box, setBox] = useState<Box | null>(null);
  const [missing, setMissing] = useState(false);

  useEffect(() => {
    if (!coach) {
      setBox(null);
      setMissing(false);
      return;
    }

    let alive = true;
    let tries = 0;
    let poll = 0;

    const locate = () => {
      if (!alive) return;
      const el = document.querySelector(`[data-coach="${coach.target}"]`);
      if (el) {
        setBox(readBox(el));
        setMissing(false);
        el.scrollIntoView({ block: "center", behavior: "smooth" });
        return;
      }
      tries += 1;
      if (tries > 40) {
        setMissing(true);
        setBox(null);
        return;
      }
      poll = window.setTimeout(locate, 50);
    };

    poll = window.setTimeout(locate, 80);

    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") clearCoach();
    };
    const onResize = () => {
      const el = document.querySelector(`[data-coach="${coach.target}"]`);
      if (el) setBox(readBox(el));
    };
    window.addEventListener("keydown", onKey);
    window.addEventListener("resize", onResize);
    window.addEventListener("scroll", onResize, true);

    const auto = window.setTimeout(() => clearCoach(), AUTO_MS);

    return () => {
      alive = false;
      window.clearTimeout(poll);
      window.clearTimeout(auto);
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("resize", onResize);
      window.removeEventListener("scroll", onResize, true);
    };
  }, [coach, clearCoach]);

  const tipTop = box
    ? Math.min(box.top + box.height + 12, window.innerHeight - 150)
    : 96;
  const tipLeft = box
    ? Math.max(16, Math.min(box.left, window.innerWidth - 320))
    : 24;

  const vw = typeof window !== "undefined" ? window.innerWidth : 0;
  const vh = typeof window !== "undefined" ? window.innerHeight : 0;

  return createPortal(
    <AnimatePresence>
      {coach && (
        <motion.div
          key={coach.target}
          className="pointer-events-none fixed inset-0 z-[80]"
          role="dialog"
          aria-label={coach.title}
          variants={shell}
          initial="hidden"
          animate="show"
          exit="hidden"
        >
          {box ? (
            <>
              {/* Four dim panes — hole stays clickable underneath */}
              <motion.button
                type="button"
                aria-label="Dismiss"
                className="pointer-events-auto absolute left-0 right-0 top-0"
                style={{ height: box.top, background: DIM }}
                variants={fadeBlur}
                onClick={() => clearCoach()}
              />
              <motion.button
                type="button"
                aria-label="Dismiss"
                className="pointer-events-auto absolute left-0"
                style={{
                  top: box.top,
                  width: box.left,
                  height: box.height,
                  background: DIM,
                }}
                variants={fadeBlur}
                onClick={() => clearCoach()}
              />
              <motion.button
                type="button"
                aria-label="Dismiss"
                className="pointer-events-auto absolute"
                style={{
                  top: box.top,
                  left: box.left + box.width,
                  width: Math.max(0, vw - box.left - box.width),
                  height: box.height,
                  background: DIM,
                }}
                variants={fadeBlur}
                onClick={() => clearCoach()}
              />
              <motion.button
                type="button"
                aria-label="Dismiss"
                className="pointer-events-auto absolute bottom-0 left-0 right-0"
                style={{
                  top: box.top + box.height,
                  height: Math.max(0, vh - box.top - box.height),
                  background: DIM,
                }}
                variants={fadeBlur}
                onClick={() => clearCoach()}
              />
              <motion.div
                className="pointer-events-none absolute rounded-xl ring-2 ring-[var(--accent)] ring-offset-2 ring-offset-transparent"
                style={{
                  top: box.top,
                  left: box.left,
                  width: box.width,
                  height: box.height,
                }}
                variants={fadeBlur}
              />
            </>
          ) : (
            <motion.button
              type="button"
              className="pointer-events-auto absolute inset-0"
              style={{ background: DIM }}
              aria-label="Dismiss coach"
              variants={fadeBlur}
              onClick={() => clearCoach()}
            />
          )}

          <motion.div
            className="pointer-events-auto absolute z-[81] w-[min(20rem,calc(100vw-2rem))] rounded-xl border bg-[var(--bg)] p-3 shadow-xl"
            style={{
              top: tipTop,
              left: tipLeft,
              borderColor: "var(--line)",
            }}
            variants={tipBlur}
          >
            <div className="mb-1.5 flex items-start justify-between gap-2">
              <p className="text-[0.82rem] font-semibold tracking-tight text-[var(--text)]">
                {coach.title}
              </p>
              <button
                type="button"
                className="rounded-md p-0.5 text-[var(--muted)] hover:bg-black/5 hover:text-[var(--text)] dark:hover:bg-white/10"
                aria-label="Close"
                onClick={() => clearCoach()}
              >
                <X size={14} />
              </button>
            </div>
            <p className="text-[0.76rem] leading-snug text-[var(--muted)]">
              {coach.tip}
            </p>
            {missing && (
              <p className="mt-2 text-[0.72rem] text-amber-700 dark:text-amber-400">
                Target control not found on this page — look for the matching
                section.
              </p>
            )}
            <button
              type="button"
              className="mt-3 rounded-lg bg-[var(--text)] px-2.5 py-1.5 text-[0.74rem] font-medium text-[var(--bg)]"
              onClick={() => clearCoach()}
            >
              Got it
            </button>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>,
    document.body,
  );
}
