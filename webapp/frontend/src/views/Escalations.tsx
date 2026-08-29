import { useEffect, useState } from "react";
import { Eyebrow, Fine, Tag } from "../components/Type";
import { fetchEscalations, prettyState, type Escalation } from "../session";

/*
 * Cases the twin referred for human review.
 *
 * The escalation protocol raises these when a state change cannot be resolved
 * by forward simulation alone. They were only ever written to a log file, which
 * meant the one part of the system that explicitly asks for a plant scientist
 * had no way to reach one.
 */
export function Escalations() {
  const [items, setItems] = useState<Escalation[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const load = () =>
      fetchEscalations()
        .then((r) => { setItems(r.escalations); setError(null); })
        .catch((e) => setError(e instanceof Error ? e.message : String(e)));
    load();
    const id = setInterval(load, 20000);
    return () => clearInterval(id);
  }, []);

  return (
    <section>
      <Eyebrow strong className="mb-1.5">Escalations</Eyebrow>
      <Fine className="max-w-[58ch] mb-5">
        State changes the twin could not resolve on its own, referred for review.
      </Fine>

      {error && (
        <div className="border border-ink/15 border-l-[3px] border-l-crit bg-paper px-4 py-3 text-[0.8125rem] text-ink-soft">
          {error}
        </div>
      )}

      {items && items.length === 0 && (
        <div className="border border-ink/15 bg-paper px-4 py-6">
          <Fine>
            Nothing has been escalated. The twin has resolved every state change
            so far without needing a second opinion.
          </Fine>
        </div>
      )}

      {items && items.length > 0 && (
        <ul className="list-none p-0 m-0">
          {items.map((e, i) => {
            const severity = String(e.severity ?? "warning");
            return (
              <li key={i} className="border-t border-ink/15 py-4">
                <div className="flex flex-wrap items-center gap-3">
                  <Tag tone={severity === "critical" ? "critical" : "warning"}>
                    {severity}
                  </Tag>
                  <span className="text-[0.9375rem]">
                    {e.from_state && e.to_state
                      ? `${prettyState(String(e.from_state))} → ${prettyState(String(e.to_state))}`
                      : "State change"}
                  </span>
                  {typeof e.confidence === "number" && (
                    <span className="text-xs text-muted">
                      forward-sim confidence {e.confidence.toFixed(2)}
                    </span>
                  )}
                  {e.at && (
                    <span className="text-xs text-muted ml-auto">
                      {new Date(String(e.at)).toLocaleString()}
                    </span>
                  )}
                </div>
                {e.summary && <Fine className="mt-2">{String(e.summary)}</Fine>}
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
