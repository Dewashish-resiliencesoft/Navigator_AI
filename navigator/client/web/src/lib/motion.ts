import type { Transition, Variants } from "motion/react";

/** Tactile base spring. Heavier elements scale mass up, not duration. */
export const spring: Transition = {
  type: "spring",
  mass: 0.5,
  stiffness: 120,
  damping: 14,
};

/** Large containers carry more visual mass than micro-elements. */
export const heavySpring: Transition = {
  type: "spring",
  mass: 0.9,
  stiffness: 90,
  damping: 16,
};

export const rise: Variants = {
  hidden: { opacity: 0, y: 12, filter: "blur(6px)" },
  show: { opacity: 1, y: 0, filter: "blur(0px)", transition: spring },
};

export const stagger = (gap = 0.04): Variants => ({
  hidden: {},
  show: { transition: { staggerChildren: gap, delayChildren: 0.02 } },
});

export const cardHover = {
  whileHover: { y: -3, scale: 1.01, transition: spring },
  whileTap: { scale: 0.995, transition: spring },
};
