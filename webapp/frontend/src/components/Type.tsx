/*
 * Typographic primitives.
 *
 * Each takes `onDark`, which selects the paper scale instead of the ink scale.
 * The first version of this design used bare utility classes at every call
 * site, and the dark panels ended up with ink-coloured body text at 1.85:1 --
 * unreadable -- because fixing it meant remembering every instance. Centralising
 * the choice here means a new section on a dark ground cannot get it wrong.
 */
import type { ReactNode } from "react";
import { Link } from "react-router-dom";

type Base = { children: ReactNode; className?: string; onDark?: boolean };

const cx = (...parts: (string | false | undefined)[]) =>
  parts.filter(Boolean).join(" ");

export function Display({
  children, className, onDark, size = "lg", as: Tag = "h2",
}: Base & { size?: "lg" | "sm"; as?: "h1" | "h2" | "h3" }) {
  return (
    <Tag
      className={cx(
        "font-light uppercase leading-[1.02] tracking-[0.055em] m-0",
        size === "lg" ? "text-display" : "text-display-sm",
        onDark ? "text-paper" : "text-ink",
        className,
      )}
    >
      {children}
    </Tag>
  );
}

export function Eyebrow({ children, className, onDark, strong }: Base & { strong?: boolean }) {
  return (
    <p
      className={cx(
        "text-[0.625rem] uppercase tracking-[0.22em] font-medium mb-4",
        onDark ? (strong ? "text-paper" : "text-paper/70") : strong ? "text-ink" : "text-muted",
        className,
      )}
    >
      {children}
    </p>
  );
}

export function Lede({ children, className, onDark }: Base) {
  return (
    <p className={cx("text-[0.9375rem] max-w-[46ch]",
                     onDark ? "text-paper/85" : "text-ink-soft", className)}>
      {children}
    </p>
  );
}

export function Fine({ children, className, onDark }: Base) {
  return (
    <p className={cx("text-xs leading-[1.55]",
                     onDark ? "text-paper/70" : "text-muted", className)}>
      {children}
    </p>
  );
}

/** Hairline divider. Needs room on both sides or it reads as a border. */
export function Rule({ onDark, className }: { onDark?: boolean; className?: string }) {
  return (
    <hr className={cx("h-px border-0 my-[clamp(2rem,5vw,3.5rem)]",
                      onDark ? "bg-paper/25" : "bg-ink/15", className)} />
  );
}

/** Outlined square button, the only button shape in this design. */
export function Button({
  children, onClick, to, type = "button", variant = "outline", disabled, className, full,
}: {
  children: ReactNode;
  onClick?: () => void;
  /** When set, renders a router Link instead of a button. */
  to?: string;
  type?: "button" | "submit";
  variant?: "outline" | "solid" | "light" | "ghost";
  disabled?: boolean;
  className?: string;
  full?: boolean;
}) {
  const variants = {
    outline: "border-ink text-ink hover:bg-ink hover:text-bone",
    solid:   "border-ink bg-ink text-bone hover:bg-moss hover:border-moss",
    light:   "border-paper/55 text-paper hover:bg-paper hover:text-moss-deep",
    ghost:   "border-ink/15 text-ink-soft hover:bg-ink hover:text-bone hover:border-ink",
  };
  const classes = cx(
    "inline-block font-sans text-[0.6875rem] uppercase tracking-[0.18em] no-underline",
    "px-6 py-3.5 border rounded-none cursor-pointer transition-colors duration-150",
    "disabled:opacity-40 disabled:cursor-not-allowed",
    "max-sm:px-4 max-sm:tracking-[0.14em]",
    variants[variant],
    full && "block w-full text-center",
    className,
  );

  if (to) {
    return <Link to={to} className={classes}>{children}</Link>;
  }

  return (
    <button type={type} onClick={onClick} disabled={disabled} className={classes}>
      {children}
    </button>
  );
}

/** Small status/provenance token. */
export function Tag({ children, tone }: {
  children: ReactNode;
  tone: "measured" | "nominal" | "ok" | "warning" | "critical" | "unknown";
}) {
  const tones = {
    measured: "text-ok", nominal: "text-muted",
    ok: "text-ok", warning: "text-warn", critical: "text-crit", unknown: "text-muted",
  };
  return (
    <span className={cx(
      "inline-block text-[0.5625rem] uppercase tracking-[0.14em]",
      "px-1.5 py-0.5 border border-current rounded-sm leading-[1.4]",
      tones[tone],
    )}>
      {children}
    </span>
  );
}

/*
 * Skeletons.
 *
 * A loading state that shows the shape of what is coming beats a line of text
 * saying it is coming: the page stops being blank immediately, nothing jumps
 * when the data lands, and the eye already knows where to look.
 *
 * `motion-reduce:animate-none` because a pulsing block is exactly the kind of
 * ambient motion people turn off at the OS level.
 */
export function Skeleton({ className }: { className?: string }) {
  return (
    <div
      aria-hidden="true"
      className={cx(
        "bg-ink/8 animate-pulse motion-reduce:animate-none",
        className,
      )}
    />
  );
}

/** Mirrors ReadingCard, so the layout does not shift when values arrive. */
export function ReadingCardSkeleton() {
  return (
    <div className="border-t border-ink/15 pt-3.5">
      <div className="flex justify-between gap-2">
        <Skeleton className="h-2 w-24" />
        <Skeleton className="h-2 w-14" />
      </div>
      <Skeleton className="h-7 w-28 mt-2.5" />
      <Skeleton className="h-2 w-20 mt-2.5" />
    </div>
  );
}
