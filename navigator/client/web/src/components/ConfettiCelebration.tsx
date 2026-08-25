import Lottie from "lottie-react";
import confettiAnimation from "../assets/lottie/confetti.json";

export function ConfettiCelebration({
  show,
  onDone,
}: {
  show: boolean;
  onDone: () => void;
}) {
  if (!show) return null;

  return (
    <div
      className="pointer-events-none fixed inset-0 z-[100] flex items-center justify-center"
      aria-hidden
    >
      <Lottie
        animationData={confettiAnimation}
        loop={false}
        onComplete={onDone}
        className="h-full w-full max-h-screen"
        style={{ maxWidth: "100vw" }}
      />
    </div>
  );
}
