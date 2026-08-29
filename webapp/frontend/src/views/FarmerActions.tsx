import { useEffect, useState } from "react";
import { Button, Eyebrow, Fine, Lede, Skeleton, Tag } from "../components/Type";
import {
  fetchInterventions, fetchIrrigation, fetchProjection, fetchStageForecast,
  logIntervention, prettyState,
  type Intervention, type IrrigationAdvice, type Projection, type StageForecast,
} from "../session";

const KINDS = [
  { value: "irrigation", label: "Watered" },
  { value: "fertiliser", label: "Fertiliser" },
  { value: "lime", label: "Lime" },
  { value: "pest_treatment", label: "Pest treatment" },
  { value: "other", label: "Something else" },
];

const OUTCOME_TONE: Record<string, "ok" | "warning" | "critical" | "unknown"> = {
  improved: "ok", unchanged: "warning", worsened: "critical", pending: "unknown",
};

/** How much water, and when. */
function IrrigationCard({ a }: { a: IrrigationAdvice }) {
  return (
    <div className={`bg-paper border border-ink/15 border-l-[3px] p-[clamp(1.25rem,2.4vw,2rem)] ${
      a.should_irrigate ? "border-l-warn" : "border-l-ok"}`}>
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <Eyebrow strong className="mb-0">Water</Eyebrow>
        <Tag tone={a.confidence === "measured" ? "measured" : "nominal"}>
          {a.confidence}
        </Tag>
      </div>

      {a.should_irrigate && a.depth_mm !== null ? (
        <>
          <p className="text-[clamp(2rem,5vw,3rem)] font-light leading-none mt-4">
            {a.depth_mm}<span className="text-base text-muted ml-2">mm</span>
          </p>
          <Lede className="mt-2">{a.best_time}</Lede>
        </>
      ) : (
        <p className="text-[clamp(1.5rem,3.5vw,2.2rem)] font-light leading-tight mt-4">
          No watering needed
        </p>
      )}

      <Fine className="mt-4">{a.reason}</Fine>

      <dl className="grid grid-cols-2 gap-x-6 gap-y-3 mt-5 m-0">
        {a.daily_use_mm !== null && (
          <div>
            <dt className="text-[0.625rem] uppercase tracking-[0.16em] text-muted">Crop uses</dt>
            <dd className="m-0 text-lg font-light">{a.daily_use_mm} mm/day</dd>
          </div>
        )}
        {a.days_of_water_left !== null && (
          <div>
            <dt className="text-[0.625rem] uppercase tracking-[0.16em] text-muted">Water left</dt>
            <dd className="m-0 text-lg font-light">{a.days_of_water_left} days</dd>
          </div>
        )}
      </dl>
    </div>
  );
}

/** Where the crop heads if nothing is done. */
function ProjectionCard({ p }: { p: Projection }) {
  const weak = p.confidence < 0.3;
  return (
    <div className="bg-paper border border-ink/15 p-[clamp(1.25rem,2.4vw,2rem)]">
      <Eyebrow strong>If you do nothing</Eyebrow>
      <Lede>{p.summary}</Lede>

      {p.changes.length > 0 && (
        <ul className="list-none p-0 mt-4 mb-0">
          {p.changes.map((c) => (
            <li key={c} className="text-[0.8125rem] text-ink-soft border-t border-ink/10 py-2">
              {c}
            </li>
          ))}
        </ul>
      )}

      <Fine className="mt-4">
        {weak
          ? "Confidence is low — there is not yet enough history behind this to rely on."
          : `Confidence ${(p.confidence * 100).toFixed(0)}%, from how steady the recent readings have been.`}
      </Fine>
    </div>
  );
}

/** Where the crop is in its life. */
function StageCard({ s }: { s: StageForecast }) {
  const pct = s.gdd_accumulated !== null && s.gdd_to_next !== null
    ? Math.max(0, Math.min(100,
        (s.gdd_accumulated / (s.gdd_accumulated + s.gdd_to_next)) * 100))
    : null;
  return (
    <div className="bg-paper border border-ink/15 p-[clamp(1.25rem,2.4vw,2rem)]">
      <Eyebrow strong>Growth stage</Eyebrow>
      <Lede>{s.summary}</Lede>
      {pct !== null && (
        <div className="mt-5">
          <div className="h-1 bg-ink/10">
            <div className="h-full bg-moss" style={{ width: `${pct}%` }} />
          </div>
          <Fine className="mt-2">
            {s.gdd_accumulated} growing degree days accumulated
            {s.gdd_to_next !== null && `, ${s.gdd_to_next} to the next stage`}.
          </Fine>
        </div>
      )}
    </div>
  );
}

/*
 * Card skeletons.
 *
 * The three cards fetch independently, so without these the slots sit empty
 * and the cards pop in one at a time, shifting the layout under the reader.
 * Each mirrors its card's real structure - same frame, same block order - so
 * the arrangement is legible before any value arrives and nothing jumps when
 * it does.
 */
const CARD = "bg-paper border border-ink/15 p-[clamp(1.25rem,2.4vw,2rem)]";

function IrrigationSkeleton() {
  return (
    <div className={`${CARD} border-l-[3px] border-l-ink/15`}>
      <div className="flex items-center justify-between gap-3">
        <Skeleton className="h-2 w-16" />
        <Skeleton className="h-3 w-20" />
      </div>
      <Skeleton className="h-11 w-32 mt-4" />
      <Skeleton className="h-3 w-40 mt-3" />
      <Skeleton className="h-2 w-full mt-5" />
      <Skeleton className="h-2 w-4/5 mt-2" />
      <div className="grid grid-cols-2 gap-x-6 mt-6">
        {[0, 1].map((i) => (
          <div key={i}>
            <Skeleton className="h-2 w-20" />
            <Skeleton className="h-5 w-24 mt-2" />
          </div>
        ))}
      </div>
    </div>
  );
}

function ProjectionSkeleton() {
  return (
    <div className={CARD}>
      <Skeleton className="h-2 w-28 mb-4" />
      <Skeleton className="h-3 w-full" />
      <Skeleton className="h-3 w-3/4 mt-2" />
      {[0, 1].map((i) => (
        <div key={i} className="border-t border-ink/10 py-2.5 mt-2">
          <Skeleton className="h-2 w-44" />
        </div>
      ))}
      <Skeleton className="h-2 w-52 mt-4" />
    </div>
  );
}

function StageSkeleton() {
  return (
    <div className={CARD}>
      <Skeleton className="h-2 w-24 mb-4" />
      <Skeleton className="h-3 w-full" />
      <Skeleton className="h-3 w-2/3 mt-2" />
      <Skeleton className="h-1 w-full mt-6" />
      <Skeleton className="h-2 w-40 mt-3" />
    </div>
  );
}

/** loading -> ready -> failed, kept explicit so a failure cannot look like loading. */
type Loadable<T> =
  | { status: "loading" }
  | { status: "ready"; data: T }
  | { status: "failed"; error: string };

/**
 * Renders the right thing for each state.
 *
 * The failure branch matters most: these fetches used to be `.catch(() => {})`,
 * so a card that failed simply never appeared, and an empty column reads as
 * "nothing to say" rather than "this did not load".
 */
function CardSlot<T>({ state, skeleton, title, children }: {
  state: Loadable<T>;
  skeleton: React.ReactNode;
  title: string;
  children: (data: T) => React.ReactNode;
}) {
  if (state.status === "loading") {
    return (
      <div aria-busy="true" aria-live="polite">
        <span className="sr-only">Loading {title}…</span>
        {skeleton}
      </div>
    );
  }
  if (state.status === "failed") {
    return (
      <div className={`${CARD} border-l-[3px] border-l-crit`}>
        <Eyebrow strong>{title}</Eyebrow>
        <Fine>Could not load this card. {state.error}</Fine>
      </div>
    );
  }
  return <>{children(state.data)}</>;
}

export function FarmerActions() {
  const [irrigation, setIrrigation] = useState<Loadable<IrrigationAdvice>>({ status: "loading" });
  const [projection, setProjection] = useState<Loadable<Projection>>({ status: "loading" });
  const [stage, setStage] = useState<Loadable<StageForecast>>({ status: "loading" });
  const [log, setLog] = useState<Intervention[]>([]);
  const [kind, setKind] = useState("irrigation");
  const [amount, setAmount] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = () => {
    const msg = (e: unknown) => (e instanceof Error ? e.message : String(e));
    // Each card reports its own outcome. Swallowing these left a failed card
    // indistinguishable from one still loading, forever.
    fetchIrrigation()
      .then((d) => setIrrigation({ status: "ready", data: d }))
      .catch((e) => setIrrigation({ status: "failed", error: msg(e) }));
    fetchProjection(48)
      .then((d) => setProjection({ status: "ready", data: d }))
      .catch((e) => setProjection({ status: "failed", error: msg(e) }));
    fetchStageForecast()
      .then((d) => setStage({ status: "ready", data: d }))
      .catch((e) => setStage({ status: "failed", error: msg(e) }));
    fetchInterventions().then((r) => setLog(r.interventions)).catch(() => {});
  };

  useEffect(() => {
    refresh();
    const id = setInterval(refresh, 20000);
    return () => clearInterval(id);
  }, []);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await logIntervention({
        kind,
        note,
        amount: amount ? Number(amount) : null,
        unit: kind === "irrigation" ? "mm" : kind === "other" ? null : "kg/ha",
      });
      setAmount(""); setNote("");
      refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  const input =
    "w-full font-sans text-[0.9375rem] px-3.5 py-2.5 bg-bone text-ink border border-ink/15 rounded-none focus:outline-none focus:border-moss";
  const label = "block text-[0.625rem] uppercase tracking-[0.18em] text-muted mb-2";

  return (
    <>
      <section className="grid grid-cols-12 gap-[clamp(1rem,2.4vw,2.25rem)] mt-10">
        <div className="col-span-12 lg:col-span-5">
          <CardSlot state={irrigation} skeleton={<IrrigationSkeleton />} title="Water">
            {(a) => <IrrigationCard a={a} />}
          </CardSlot>
        </div>
        <div className="col-span-12 md:col-span-6 lg:col-span-4">
          <CardSlot state={projection} skeleton={<ProjectionSkeleton />} title="If you do nothing">
            {(p) => <ProjectionCard p={p} />}
          </CardSlot>
        </div>
        <div className="col-span-12 md:col-span-6 lg:col-span-3">
          <CardSlot state={stage} skeleton={<StageSkeleton />} title="Growth stage">
            {(sf) => <StageCard s={sf} />}
          </CardSlot>
        </div>
      </section>

      {/* ── Intervention log ─────────────────────────────────────────── */}
      <section className="mt-12">
        <Eyebrow strong className="mb-1.5">What you did</Eyebrow>
        <Fine className="max-w-[58ch] mb-5">
          Record an action and the twin checks whether the readings responded.
          An action nobody verifies is a diary entry, not evidence it helped.
        </Fine>

        <form onSubmit={submit}
              className="grid grid-cols-12 gap-4 items-end bg-paper border border-ink/15 p-[clamp(1.25rem,2.4vw,1.75rem)]">
          <label className="col-span-12 sm:col-span-4">
            <span className={label}>Action</span>
            <select className={input} value={kind} onChange={(e) => setKind(e.target.value)}>
              {KINDS.map((k) => <option key={k.value} value={k.value}>{k.label}</option>)}
            </select>
          </label>
          <label className="col-span-6 sm:col-span-3">
            <span className={label}>
              Amount {kind === "irrigation" ? "(mm)" : kind === "other" ? "" : "(kg/ha)"}
            </span>
            <input className={input} type="number" step="0.1" value={amount}
                   onChange={(e) => setAmount(e.target.value)} placeholder="optional" />
          </label>
          <label className="col-span-12 sm:col-span-3">
            <span className={label}>Note</span>
            <input className={input} value={note} maxLength={400}
                   onChange={(e) => setNote(e.target.value)} placeholder="optional" />
          </label>
          <div className="col-span-6 sm:col-span-2">
            <Button type="submit" variant="solid" full disabled={busy}>
              {busy ? "Saving…" : "Log it"}
            </Button>
          </div>
        </form>

        {error && (
          <div className="border border-ink/15 border-l-[3px] border-l-crit bg-paper px-4 py-3 text-[0.8125rem] text-ink-soft mt-4">
            {error}
          </div>
        )}

        {log.length > 0 && (
          <ul className="list-none p-0 m-0 mt-6">
            {log.map((i) => (
              <li key={i.id} className="border-t border-ink/15 py-4">
                <div className="flex flex-wrap items-center gap-3">
                  <Tag tone={OUTCOME_TONE[i.outcome] ?? "unknown"}>{i.outcome}</Tag>
                  <span className="text-[0.9375rem]">
                    {KINDS.find((k) => k.value === i.kind)?.label ?? i.kind}
                    {i.amount !== null && ` — ${i.amount}${i.unit ? ` ${i.unit}` : ""}`}
                  </span>
                  <span className="text-xs text-muted ml-auto">
                    {new Date(i.logged_at).toLocaleString()}
                  </span>
                </div>
                <Fine className="mt-2">{i.outcome_detail}</Fine>
                {i.note && <Fine className="mt-1 italic">“{i.note}”</Fine>}
                <Fine className="mt-1">
                  Twin was {prettyState(i.state_at_logging)} at{" "}
                  {i.health_at_logging.toFixed(0)} when you logged this.
                </Fine>
              </li>
            ))}
          </ul>
        )}
      </section>
    </>
  );
}
