import type { Transition, Variants } from "motion/react";

/** Smooth, lightly damped — avoids jumpy overshoot. */
export const spring: Transition = {
  type: "spring",
  stiffness: 280,
  damping: 28,
  mass: 0.8,
};

export const soft: Transition = {
  type: "tween",
  duration: 0.22,
  ease: [0.22, 1, 0.36, 1],
};

export const heavySpring: Transition = {
  type: "spring",
  stiffness: 220,
  damping: 26,
  mass: 1,
};

export const rise: Variants = {
  hidden: { opacity: 0, y: 8 },
  show: { opacity: 1, y: 0, transition: soft },
};

export const stagger = (gap = 0.03): Variants => ({
  hidden: {},
  show: { transition: { staggerChildren: gap, delayChildren: 0.01 } },
});

export const cardHover = {
  whileHover: { y: -2, transition: soft },
  whileTap: { scale: 0.99, transition: soft },
};
