import { useEffect, useState } from "react";
import { Chart } from "../components/Chart";
import { Eyebrow, Fine } from "../components/Type";
import { fetchFieldCatalogue, fetchSeries, type FieldCatalogue, type Series } from "../session";

const WINDOW_LABELS: Record<string, string> = {
  "1h": "1 hour", "6h": "6 hours", "24h": "24 hours", "7d": "7 days",
};

export function Explorer() {
  const [catalogue, setCatalogue] = useState<FieldCatalogue | null>(null);
  const [field, setField] = useState("soil_moisture");
  const [window, setWindow] = useState("6h");
  const [series, setSeries] = useState<Series | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchFieldCatalogue().then(setCatalogue).catch(() => setCatalogue(null));
  }, []);

  useEffect(() => {
    let cancelled = false;
    const load = () =>
      fetchSeries(field, window)
        .then((s) => { if (!cancelled) { setSeries(s); setError(null); } })
        .catch((e) => { if (!cancelled) setError(e instanceof Error ? e.message : String(e)); });
    load();
    const id = setInterval(load, 15000);
    return () => { cancelled = true; clearInterval(id); };
  }, [field, window]);

  const select =
    "font-sans text-[0.8125rem] px-3 py-2 bg-bone text-ink border border-ink/15 rounded-none focus:outline-none focus:border-moss";

  return (
    <section>
      <div className="flex flex-wrap items-end justify-between gap-4 mb-5">
        <div>
          <Eyebrow strong className="mb-1.5">Time series</Eyebrow>
          <Fine className="max-w-[54ch]">
            Any measured field over any window, with the growth-stage warning and
            critical bands drawn behind it.
          </Fine>
        </div>
        <div className="flex flex-wrap gap-2">
          <select className={select} value={field} onChange={(e) => setField(e.target.value)}
                  aria-label="Field">
            {catalogue &&
              Object.entries(catalogue.groups).map(([group, fields]) => (
                <optgroup key={group} label={group}>
                  {fields.map((f) => (
                    <option key={f.field} value={f.field}>{f.label}</option>
                  ))}
                </optgroup>
              ))}
          </select>
          <select className={select} value={window} onChange={(e) => setWindow(e.target.value)}
                  aria-label="Window">
            {(catalogue?.windows ?? ["1h", "6h", "24h", "7d"]).map((w) => (
              <option key={w} value={w}>{WINDOW_LABELS[w] ?? w}</option>
            ))}
          </select>
        </div>
      </div>

      {error ? (
        <div className="border border-ink/15 border-l-[3px] border-l-crit bg-paper px-4 py-3 text-[0.8125rem] text-ink-soft">
          {error}
        </div>
      ) : series ? (
        <>
          <div className="flex items-baseline gap-3 mb-3">
            <h3 className="text-lg font-light">{series.label}</h3>
            {series.unit && <span className="text-xs text-muted">{series.unit}</span>}
          </div>
          <Chart points={series.points} band={series.band} unit={series.unit} />
          <div className="flex flex-wrap gap-x-6 gap-y-1 mt-3">
            <Fine>
              <span className="inline-block w-3 h-px bg-crit align-middle mr-1.5" />
              critical threshold
            </Fine>
            <Fine>
              <span className="inline-block w-3 h-px bg-warn align-middle mr-1.5" />
              warning threshold
            </Fine>
            <Fine>
              <span className="inline-block w-3 h-2 bg-ok/20 align-middle mr-1.5" />
              healthy range
            </Fine>
          </div>
        </>
      ) : (
        <Fine>Loading…</Fine>
      )}
    </section>
  );
}
