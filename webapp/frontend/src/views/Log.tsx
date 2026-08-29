import { useEffect, useState } from "react";
import { fetchEvents, prettyState, type EventLog, type TwinEvent } from "../session";
import { Button, Eyebrow, Fine, Skeleton, Tag } from "../components/Type";

const REFRESH_MS = 15000;
const PAGE_SIZE = 25;

const TYPE_LABELS: Record<string, string> = {
  state_change: "State change",
  mas_escalation_response: "Agent response",
  escalation_resolved_by_simulation: "Resolved by simulation",
  twin_calibration: "Calibration",
};

/** Severities the twin writes, mapped onto the Tag tones the design system has. */
function tone(severity: string): "measured" | "warning" | "critical" | "nominal" {
  if (severity === "critical") return "critical";
  if (severity === "warning") return "warning";
  return "nominal";
}

const num = (v: unknown, digits = 2) =>
  typeof v === "number" ? v.toFixed(digits) : String(v ?? "—");

/**
 * One line saying what actually happened.
 *
 * Each event type carries a different payload, and dumping raw JSON would make
 * the log unreadable at exactly the moment it matters. The per-type summaries
 * below are the point of this view: the twin's reasoning in plain language.
 */
function Summary({ event }: { event: TwinEvent }) {
  const p = event.payload ?? {};

  switch (event.event_type) {
    case "state_change":
      return (
        <span>
          {prettyState(String(p.from ?? "?"))}
          <span className="text-muted mx-2">→</span>
          <strong className="text-ink">{prettyState(String(p.to ?? "?"))}</strong>
        </span>
      );

    case "escalation_resolved_by_simulation": {
      const before = p.initial_confidence;
      const after = p.post_simulation_confidence;
      return (
        <span>
          {prettyState(String(p.from_state ?? "?"))}
          <span className="text-muted mx-2">→</span>
          {prettyState(String(p.to_state ?? "?"))}
          <span className="text-muted"> · confidence </span>
          {num(before)}<span className="text-muted mx-1">→</span>
          <strong className="text-ink">{num(after)}</strong>
        </span>
      );
    }

    case "twin_calibration":
      return (
        <span>
          Vcmax25 <strong className="text-ink">{num(p.vcmax25)}</strong>
          <span className="text-muted">, Ball-Berry m </span>
          <strong className="text-ink">{num(p.bb_slope_m)}</strong>
          <span className="text-muted">
            {" "}· objective {num(p.objective, 4)} over {String(p.n_samples ?? "?")} samples
          </span>
        </span>
      );

    case "mas_escalation_response":
      return (
        <span>
          {prettyState(String(p.from_state ?? "?"))}
          <span className="text-muted mx-2">→</span>
          {prettyState(String(p.to_state ?? "?"))}
        </span>
      );

    default:
      return <span className="text-muted">{JSON.stringify(p).slice(0, 140)}</span>;
  }
}

/** The agent's answer, shown in full because it is the reasoning itself. */
function AgentAnswer({ event }: { event: TwinEvent }) {
  if (event.event_type !== "mas_escalation_response") return null;
  const answer = String(event.payload?.answer ?? "").trim();
  if (!answer) return null;

  // The agent layer answers this when no LLM is configured. Saying so beats
  // rendering it as though it were a diagnosis.
  const unconfigured = answer === "No agent available.";
  return (
    <p className={`text-[0.8125rem] leading-[1.6] mt-2 max-w-[76ch] ${
      unconfigured ? "text-muted italic" : "text-ink-soft"}`}>
      {unconfigured
        ? "No agent available — DT_LLM__API_KEY is unset, so the escalation was recorded without a diagnosis."
        : answer}
    </p>
  );
}

function timeOf(stamp: string): string {
  const d = new Date(stamp);
  return Number.isNaN(d.getTime()) ? stamp : d.toLocaleString();
}

export function Log() {
  const [events, setEvents] = useState<TwinEvent[] | null>(null);
  const [total, setTotal] = useState(0);
  const [hasMore, setHasMore] = useState(false);
  const [available, setAvailable] = useState(true);
  const [detail, setDetail] = useState<string | null>(null);
  const [filter, setFilter] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);
  // True once older pages have been pulled in. Polling stops there - see below.
  const [paged, setPaged] = useState(false);

  const apply = (d: EventLog) => {
    setTotal(d.total);
    setHasMore(d.has_more);
    setAvailable(d.available);
    setDetail(d.detail);
    setError(null);
  };

  /*
   * Poll the first page only, and only while the reader is on it.
   *
   * Refreshing underneath someone who has paged back through history would
   * either discard the pages they pulled in or shift the list under the cursor
   * mid-read. Once they load more, the view becomes a historical one and holds
   * still until they ask for fresh data.
   */
  useEffect(() => {
    if (paged) return;
    let cancelled = false;
    const load = () =>
      fetchEvents(PAGE_SIZE, 0, filter ?? undefined)
        .then((d) => { if (!cancelled) { setEvents(d.events); apply(d); } })
        .catch((e) => { if (!cancelled) setError(e instanceof Error ? e.message : String(e)); });
    load();
    const id = setInterval(load, REFRESH_MS);
    return () => { cancelled = true; clearInterval(id); };
  }, [filter, paged]);

  const loadMore = () => {
    if (!events) return;
    setLoadingMore(true);
    setPaged(true);
    fetchEvents(PAGE_SIZE, events.length, filter ?? undefined)
      .then((d) => {
        setEvents((prev) => [...(prev ?? []), ...d.events]);
        apply(d);
      })
      .catch((e) => setError(e instanceof Error ? e.message : String(e)))
      .finally(() => setLoadingMore(false));
  };

  const selectFilter = (key: string | null) => {
    setEvents(null);
    setPaged(false);
    setFilter(key);
  };

  const filters: [string | null, string][] = [
    [null, "All"],
    ...Object.entries(TYPE_LABELS).map(([k, v]) => [k, v] as [string, string]),
  ];

  return (
    <section>
      <Eyebrow strong>Twin activity log</Eyebrow>
      <Fine className="max-w-[62ch] -mt-2 mb-6">
        What the twin decided, and why. State transitions from the reactive FSM,
        escalations raised and how they were closed, and the calibration agent
        refitting the model against observed behaviour.
      </Fine>

      <div className="flex flex-wrap gap-2 mb-6">
        {filters.map(([key, label]) => (
          <Button
            key={label}
            variant={filter === key ? "solid" : "ghost"}
            onClick={() => selectFilter(key)}
          >
            {label}
          </Button>
        ))}
      </div>

      {error && (
        <div className="border border-ink/15 border-l-[3px] border-l-crit bg-paper px-4 py-3 mb-4">
          <Fine>{error}</Fine>
        </div>
      )}

      {!available && (
        <div className="border border-ink/15 border-l-[3px] border-l-warn bg-paper px-4 py-3 mb-4">
          <Fine>
            <strong className="text-ink">The event store is unreachable.</strong>{" "}
            {detail ?? ""} This is not an empty log — events are still being
            written by the twin, they just cannot be read from here.
          </Fine>
        </div>
      )}

      {events === null ? (
        <div aria-busy="true" aria-live="polite">
          <span className="sr-only">Loading the log…</span>
          {[0, 1, 2, 3, 4, 5].map((i) => (
            <div key={i} className="border-t border-ink/15 py-4">
              <div className="flex justify-between gap-4">
                <Skeleton className="h-2 w-32" />
                <Skeleton className="h-2 w-24" />
              </div>
              <Skeleton className="h-4 w-[min(30rem,70%)] mt-3" />
            </div>
          ))}
        </div>
      ) : events.length === 0 ? (
        <Fine>
          {available
            ? "No events recorded yet. The twin writes here when its state changes, an escalation is raised, or the calibration agent refits the model."
            : ""}
        </Fine>
      ) : (
        <ol className="list-none p-0 m-0">
          {events.map((e, i) => (
            <li key={`${e.timestamp}-${i}`} className="border-t border-ink/15 py-4">
              <div className="flex flex-wrap justify-between items-baseline gap-3">
                <div className="flex items-center gap-2.5">
                  <span className="text-[0.625rem] uppercase tracking-[0.16em] text-muted">
                    {TYPE_LABELS[e.event_type] ?? e.event_type.replace(/_/g, " ")}
                  </span>
                  <Tag tone={tone(e.severity)}>{e.severity}</Tag>
                </div>
                <span className="text-[0.6875rem] text-muted tabular-nums">
                  {timeOf(e.timestamp)}
                </span>
              </div>
              <div className="text-[0.9375rem] mt-1.5 leading-snug">
                <Summary event={e} />
              </div>
              <AgentAnswer event={e} />
            </li>
          ))}
        </ol>
      )}

      {events && events.length > 0 && (
        <div className="border-t border-ink/15 pt-5 mt-1 flex flex-wrap items-center justify-between gap-4">
          <Fine>
            Showing {events.length} of {total}
            {filter ? ` ${TYPE_LABELS[filter]?.toLowerCase() ?? "matching"} events` : " events"}.
            {paged
              ? " Live updates paused while you browse history."
              : " Updating every 15 seconds."}
          </Fine>
          <div className="flex gap-2">
            {paged && (
              <Button variant="ghost" onClick={() => { setEvents(null); setPaged(false); }}>
                Back to latest
              </Button>
            )}
            {hasMore && (
              <Button onClick={loadMore} disabled={loadingMore}>
                {loadingMore ? "Loading…" : "Load older"}
              </Button>
            )}
          </div>
        </div>
      )}
    </section>
  );
}
