/*
 * A line chart drawn as plain SVG.
 *
 * A charting library would bring several hundred kilobytes and its own visual
 * language, neither of which this design wants. The chart needs one series, the
 * warning and critical bands behind it, and nothing else.
 */
import type { Band } from "../session";

type Props = {
  points: { t: string; v: number }[];
  band?: Band;
  unit?: string;
  height?: number;
};

const W = 720;   // viewBox width; the SVG scales to its container

export function Chart({ points, band, unit = "", height = 200 }: Props) {
  if (points.length < 2) {
    return (
      <div className="border border-ink/15 bg-paper px-4 py-8 text-center">
        <p className="text-xs text-muted">
          {points.length === 0
            ? "No readings in this window yet."
            : "Only one reading so far — a line needs at least two."}
        </p>
      </div>
    );
  }

  const values = points.map((p) => p.v);
  const limits = [
    band?.crit_low, band?.crit_high, band?.warn_low, band?.warn_high, band?.nominal,
  ].filter((n): n is number => typeof n === "number");

  // Include the bands in the extent so a threshold never sits off-canvas.
  let lo = Math.min(...values, ...limits);
  let hi = Math.max(...values, ...limits);
  if (hi === lo) { hi += 1; lo -= 1; }
  const pad = (hi - lo) * 0.12;
  lo -= pad; hi += pad;

  const x = (i: number) => (i / (points.length - 1)) * W;
  const y = (v: number) => height - ((v - lo) / (hi - lo)) * height;

  const path = points.map((p, i) => `${i === 0 ? "M" : "L"}${x(i).toFixed(1)},${y(p.v).toFixed(1)}`).join(" ");
  const area = `${path} L${W},${height} L0,${height} Z`;

  const zone = (from: number | null | undefined, to: number | null | undefined) => {
    if (typeof from !== "number" || typeof to !== "number") return null;
    const top = Math.min(y(from), y(to));
    const h = Math.abs(y(from) - y(to));
    return { top, h };
  };
  const ok = zone(band?.warn_low ?? lo, band?.warn_high ?? hi);

  const first = new Date(points[0].t);
  const last = new Date(points[points.length - 1].t);
  const fmtTime = (d: Date) =>
    d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });

  return (
    <figure className="m-0">
      <svg viewBox={`0 0 ${W} ${height}`} className="w-full block" role="img"
           aria-label={`Time series, ${points.length} readings`}
           preserveAspectRatio="none">
        {/* Healthy band behind the trace. */}
        {ok && <rect x="0" y={ok.top} width={W} height={ok.h}
                     className="fill-ok/8" />}

        {/* Critical thresholds as dashed rules. */}
        {[band?.crit_low, band?.crit_high].map((v, i) =>
          typeof v === "number" ? (
            <line key={i} x1="0" x2={W} y1={y(v)} y2={y(v)}
                  className="stroke-crit/45" strokeWidth="1" strokeDasharray="4 4" />
          ) : null,
        )}
        {[band?.warn_low, band?.warn_high].map((v, i) =>
          typeof v === "number" ? (
            <line key={i} x1="0" x2={W} y1={y(v)} y2={y(v)}
                  className="stroke-warn/40" strokeWidth="1" strokeDasharray="2 5" />
          ) : null,
        )}

        <path d={area} className="fill-moss/8" />
        <path d={path} className="stroke-moss fill-none" strokeWidth="1.6"
              vectorEffect="non-scaling-stroke" strokeLinejoin="round" />
        <circle cx={x(points.length - 1)} cy={y(values[values.length - 1])} r="3"
                className="fill-moss" />
      </svg>

      <figcaption className="flex justify-between mt-2 text-[0.625rem] uppercase tracking-[0.14em] text-muted">
        <span>{fmtTime(first)}</span>
        <span>
          {values[values.length - 1].toFixed(3)} {unit} · {points.length} readings
        </span>
        <span>{fmtTime(last)}</span>
      </figcaption>
    </figure>
  );
}
