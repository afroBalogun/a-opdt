import { useEffect, useState } from "react";
import {
  fetchFarmerDashboard, prettyStage,
  type FarmerDashboard as Data, type User,
} from "../session";
import { Display, Eyebrow, Fine, Lede } from "../components/Type";
import { Sprig } from "../components/Botanical";
import { DashboardShell, DashboardSkeleton, ProvenanceNotice, ReadingCard } from "./Readings";
import { FarmerActions } from "./FarmerActions";

const REFRESH_MS = 15000;

// Severity is carried as a tone from the API rather than inferred here, so the
// colour and the wording cannot drift apart.
const TONE_BORDER: Record<string, string> = {
  good: "border-l-ok",
  neutral: "border-l-muted",
  bad: "border-l-warn",
  critical: "border-l-crit",
};

export function FarmerDashboard({ user }: { user: User }) {
  const [data, setData] = useState<Data | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const load = () =>
      fetchFarmerDashboard()
        .then((d) => { setData(d); setError(null); })
        .catch((e) => setError(e instanceof Error ? e.message : String(e)));
    load();
    const id = setInterval(load, REFRESH_MS);
    return () => clearInterval(id);
  }, []);

  if (error)
    return (
      <DashboardShell>
        <div className="border border-ink/15 border-l-[3px] border-l-crit bg-paper px-4 py-3 text-[0.8125rem] text-ink-soft">
          {error}
        </div>
      </DashboardShell>
    );

  if (!data) return <DashboardSkeleton label="Checking your crop" />;

  return (
    <DashboardShell>
      <Eyebrow>
        {user.plot_name ? `${user.plot_name} · ` : ""}{prettyStage(data.growth_stage)}
      </Eyebrow>

      <div className={`bg-paper border border-ink/15 border-l-[3px] ${TONE_BORDER[data.tone] ?? "border-l-muted"} p-[clamp(1.25rem,3vw,2rem)]`}>
        <div className="grid grid-cols-12 gap-[clamp(1rem,2.4vw,2.25rem)] items-center">
          <div className="col-span-12 md:col-span-8">
            <Display size="sm" as="h1" className="mb-4">{data.headline}</Display>
            <Lede>{data.detail}</Lede>
            <Eyebrow strong className="mt-7 mb-2">What to do</Eyebrow>
            <p className="text-[0.9375rem] text-ink max-w-[46ch]">{data.action}</p>
          </div>
          <div className="hidden md:col-span-4 md:flex justify-center">
            <Sprig className="w-28 h-auto opacity-70" tone="light" />
          </div>
        </div>
      </div>

      <div className="mt-6">
        <ProvenanceNotice measured={data.measured_count} total={data.total_count} />
      </div>

      <FarmerActions />

      <section className="mt-12">
        <Eyebrow strong>Your readings</Eyebrow>
        <Fine className="mb-6 max-w-[58ch]">
          The measurements worth acting on, plus anything currently outside its
          normal range for this growth stage.
        </Fine>
        <div className="grid grid-cols-12 gap-[clamp(1rem,2.4vw,2.25rem)]">
          {data.highlights.map((r) => (
            <div key={r.field} className="col-span-12 sm:col-span-6 lg:col-span-4">
              <ReadingCard r={r} />
            </div>
          ))}
        </div>
      </section>
    </DashboardShell>
  );
}
