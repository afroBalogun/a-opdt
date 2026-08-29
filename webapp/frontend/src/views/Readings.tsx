import { Fine, ReadingCardSkeleton, Skeleton, Tag } from "../components/Type";
import type { FieldReading } from "../session";

/** Format a value at a precision suited to its magnitude. */
function fmt(v: number | null): string {
  if (v === null || Number.isNaN(v)) return "—";
  const a = Math.abs(v);
  if (a >= 100) return v.toFixed(0);
  if (a >= 10) return v.toFixed(1);
  if (a >= 1) return v.toFixed(2);
  return v.toFixed(3);
}

/** The band a reading sits in, written out so the number can be judged. */
function bandText(r: FieldReading): string | null {
  const lo = r.warn_low ?? r.crit_low;
  const hi = r.warn_high ?? r.crit_high;
  if (lo != null && hi != null) return `warn outside ${fmt(lo)}–${fmt(hi)}`;
  if (lo != null) return `warn below ${fmt(lo)}`;
  if (hi != null) return `warn above ${fmt(hi)}`;
  return null;
}

export function ReadingCard({ r }: { r: FieldReading }) {
  const band = bandText(r);
  return (
    <div className="border-t border-ink/15 pt-3.5">
      <div className="flex justify-between gap-2 text-[0.625rem] uppercase tracking-[0.16em] text-muted">
        <span>{r.label}</span>
        <Tag tone={r.provenance}>{r.provenance}</Tag>
      </div>
      <div className="text-[clamp(1.35rem,2.4vw,1.9rem)] font-light leading-tight mt-1.5">
        {fmt(r.value)}
        {r.unit && <span className="text-xs text-muted ml-1">{r.unit}</span>}
      </div>
      <div className="text-[0.6875rem] text-muted mt-1.5 flex items-center gap-2 flex-wrap">
        <Tag tone={r.status}>{r.status}</Tag>
        {band && <span>{band}</span>}
      </div>
    </div>
  );
}

/**
 * Standing caveat, shown whenever the twin models more fields than any
 * instrument reports. Without it the stage nominals read as observations.
 */
export function ProvenanceNotice({ measured, total }: { measured: number; total: number }) {
  if (measured === total) return null;
  return (
    <div className="border border-ink/15 border-l-[3px] border-l-warn bg-paper px-4 py-3.5">
      <Fine>
        <strong className="text-ink">{measured} of {total} parameters are measured.</strong>{" "}
        {measured === 0
          ? "No sensor data has reached the twin yet, so every value below is the growth-stage nominal from the sensor profiles — a starting assumption, not an observation of this plant."
          : "The remainder show the growth-stage nominal, marked “nominal”. Those are assumptions, not observations of this plant."}
      </Fine>
    </div>
  );
}

/** Shared page frame for both dashboards. */
export function DashboardShell({ children }: { children: React.ReactNode }) {
  return (
    <div className="mx-auto max-w-[1320px] px-[clamp(1.25rem,4vw,4.5rem)] py-[clamp(2rem,5vw,4rem)]">
      {children}
    </div>
  );
}

/**
 * The dashboard's own shape, shown while the first read is in flight.
 *
 * Deliberately mirrors the real hierarchy - headline, provenance notice, two
 * groups of reading cards - rather than being a generic spinner. The page is
 * legible as *this* page immediately, and nothing moves when the data lands.
 *
 * `aria-busy` with a live region carries the same information to a screen
 * reader, which gets nothing from grey rectangles.
 */
export function DashboardSkeletonBody({ label = "Reading the twin" }: { label?: string }) {
  return (
    <div aria-busy="true" aria-live="polite">
        <span className="sr-only">{label}…</span>

        {/* Headline block: state, health score, growth stage */}
        <div className="grid grid-cols-12 gap-[clamp(1rem,2.4vw,2.25rem)] items-end mb-10">
          <div className="col-span-12 lg:col-span-7">
            <Skeleton className="h-2 w-40" />
            <Skeleton className="h-10 w-[min(22rem,80%)] mt-4" />
          </div>
          <div className="col-span-12 lg:col-span-5 grid grid-cols-2 gap-6">
            {[0, 1].map((i) => (
              <div key={i}>
                <Skeleton className="h-2 w-20" />
                <Skeleton className="h-6 w-16 mt-2" />
              </div>
            ))}
          </div>
        </div>

        <Skeleton className="h-14 w-full" />

        {[0, 1].map((group) => (
          <section key={group} className="mt-12">
            <Skeleton className="h-2 w-32 mb-4" />
            <div className="grid grid-cols-12 gap-[clamp(1rem,2.4vw,2.25rem)]">
              {[0, 1, 2, 3, 4, 5].map((i) => (
                <div key={i} className="col-span-12 sm:col-span-6 lg:col-span-4">
                  <ReadingCardSkeleton />
                </div>
              ))}
            </div>
          </section>
        ))}
    </div>
  );
}

/** The same skeleton inside the page frame, for callers without their own. */
export function DashboardSkeleton({ label }: { label?: string }) {
  return (
    <DashboardShell>
      <DashboardSkeletonBody label={label} />
    </DashboardShell>
  );
}
