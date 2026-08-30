import { useEffect, useRef, useState } from "react";
import { Advisor } from "../components/Advisor";
import {
  fetchResearcherDashboard, prettyStage, prettyState,
  type ResearcherDashboard as Data, type User,
} from "../session";
import { Button, Display, Eyebrow, Fine, Rule, Tag } from "../components/Type";
import { DashboardShell, DashboardSkeletonBody, ProvenanceNotice, ReadingCard } from "./Readings";
import { WhatIfSimulator } from "./WhatIfSimulator";
import { Explorer } from "./Explorer";
import { Escalations } from "./Escalations";
import { Log } from "./Log";

const REFRESH_MS = 15000;

type TabKey = "overview" | "log";

const TABS: [TabKey, string][] = [
  ["overview", "Overview"],
  ["log", "Log"],
];

/**
 * Tabs, wired for assistive tech as well as the mouse.
 *
 * role=tab/tablist with aria-selected and aria-controls is what makes this
 * announce as a tab set rather than two anonymous buttons, and it is the
 * difference between a screen reader user knowing there is a second view and
 * never finding it.
 */
function Tabs({ tab, onChange }: { tab: TabKey; onChange: (t: TabKey) => void }) {
  return (
    <div role="tablist" aria-label="Dashboard views" className="flex gap-6 border-b border-ink/15 mb-10">
      {TABS.map(([key, label]) => {
        const selected = tab === key;
        return (
          <button
            key={key}
            role="tab"
            id={`tab-${key}`}
            aria-selected={selected}
            aria-controls={`panel-${key}`}
            onClick={() => onChange(key)}
            className={[
              "bg-transparent border-0 cursor-pointer px-0 pb-3 -mb-px",
              "text-[0.6875rem] uppercase tracking-[0.18em] font-medium",
              "border-b-2 transition-colors",
              selected
                ? "border-ink text-ink"
                : "border-transparent text-muted hover:text-ink",
            ].join(" ")}
          >
            {label}
          </button>
        );
      })}
    </div>
  );
}

export function ResearcherDashboard({ user }: { user: User }) {
  const [tab, setTab] = useState<TabKey>("overview");
  const [data, setData] = useState<Data | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [showSim, setShowSim] = useState(false);
  const simRef = useRef<HTMLDivElement>(null);

  /*
   * Bring the simulator into view when it opens.
   *
   * It renders at the bottom of a long page, so without this the viewport does
   * not move and opening it looks like the button did nothing - the reason to
   * doubt it worked and click again. Only on open: scrolling on close would
   * yank the page around after the content has gone.
   */
  useEffect(() => {
    if (!showSim) return;
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    // One frame, so the panel is in the DOM and has its height before we scroll.
    const id = requestAnimationFrame(() => {
      simRef.current?.scrollIntoView({
        behavior: reduced ? "auto" : "smooth",
        block: "start",
      });
    });
    return () => cancelAnimationFrame(id);
  }, [showSim]);

  useEffect(() => {
    const load = () =>
      fetchResearcherDashboard()
        .then((d) => { setData(d); setError(null); })
        .catch((e) => setError(e instanceof Error ? e.message : String(e)));
    load();
    // The twin publishes on a cycle; track it without hammering the API.
    const id = setInterval(load, REFRESH_MS);
    return () => clearInterval(id);
  }, []);

  const shell = (children: React.ReactNode) => (
    <DashboardShell>
      <Tabs tab={tab} onChange={setTab} />
      <div role="tabpanel" id={`panel-${tab}`} aria-labelledby={`tab-${tab}`}>
        {children}
      </div>
      <Advisor />
    </DashboardShell>
  );

  // The log reads a different store from the dashboard, so it stays reachable
  // even when the twin read fails - which is exactly when you want the log.
  if (tab === "log") return shell(<Log />);

  if (error)
    return shell(
      <div className="border border-ink/15 border-l-[3px] border-l-crit bg-paper px-4 py-3 text-[0.8125rem] text-ink-soft">
        {error}
      </div>,
    );

  if (!data) {
    return (
      <DashboardShell>
        <Tabs tab={tab} onChange={setTab} />
        <DashboardSkeletonBody />
      </DashboardShell>
    );
  }

  return shell(
    <>
      <div className="grid grid-cols-12 gap-[clamp(1rem,2.4vw,2.25rem)] items-end mb-10">
        <div className="col-span-12 lg:col-span-7">
          <Eyebrow>Researcher · {user.name}</Eyebrow>
          <Display size="sm" as="h1">{prettyState(data.plant_state)}</Display>
        </div>
        <div className="col-span-12 lg:col-span-5 grid grid-cols-2 gap-6">
          <div>
            <p className="text-[0.625rem] uppercase tracking-[0.16em] text-muted">Health score</p>
            <p className="text-[clamp(1.35rem,2.4vw,1.9rem)] font-light mt-1.5">
              {data.health_score.toFixed(1)}
            </p>
          </div>
          <div>
            <p className="text-[0.625rem] uppercase tracking-[0.16em] text-muted">Growth stage</p>
            <p className="text-lg font-light mt-1.5">{prettyStage(data.growth_stage)}</p>
          </div>
        </div>
      </div>

      <ProvenanceNotice measured={data.measured_count} total={data.total_count} />

      {Object.keys(data.active_categories).length > 0 && (
        <div className="bg-paper border border-ink/8 p-[clamp(1.25rem,2.4vw,2rem)] mt-6">
          <Eyebrow strong>Active stress categories</Eyebrow>
          <div className="flex flex-wrap gap-2.5">
            {Object.entries(data.active_categories).map(([cat, sev]) => (
              <Tag key={cat} tone={sev as "warning" | "critical"}>
                {cat.replace(/_/g, " ")} · {sev}
              </Tag>
            ))}
          </div>
        </div>
      )}

      {(data.calibrated_vcmax25 !== null || data.calibrated_bb_slope_m !== null) && (
        <div className="bg-paper border border-ink/8 p-[clamp(1.25rem,2.4vw,2rem)] mt-6">
          <Eyebrow strong>Twin calibration (L8)</Eyebrow>
          <Fine>
            {data.calibrated_vcmax25 !== null && <>Vcmax25 = {data.calibrated_vcmax25.toFixed(2)}. </>}
            {data.calibrated_bb_slope_m !== null && <>Ball-Berry slope m = {data.calibrated_bb_slope_m.toFixed(2)}. </>}
            Fitted by the calibration agent against observed behaviour, replacing the literature defaults.
          </Fine>
        </div>
      )}

      <div className="mt-12">
        <Explorer />
      </div>

      <div className="mt-14">
        <Escalations />
      </div>

      {Object.entries(data.groups).map(([group, readings]) => (
        <section key={group} className="mt-12">
          <Eyebrow strong>{group}</Eyebrow>
          <div className="grid grid-cols-12 gap-[clamp(1rem,2.4vw,2.25rem)]">
            {readings.map((r) => (
              <div key={r.field} className="col-span-12 sm:col-span-6 lg:col-span-4">
                <ReadingCard r={r} />
              </div>
            ))}
          </div>
        </section>
      ))}

      <section className="mt-14" ref={simRef}>
        <Rule />
        <div className="flex flex-wrap justify-between items-center gap-4">
          <div>
            <Eyebrow strong className="mb-1.5">What-if simulator</Eyebrow>
            <Fine className="max-w-[52ch]">
              Project the effect of changed conditions using the same physiology
              model and stress rules the live twin runs.
            </Fine>
          </div>
          <Button
            onClick={() => setShowSim((v) => !v)}
            aria-expanded={showSim}
            aria-controls="what-if-panel"
          >
            {showSim ? "Hide" : "Open"}
          </Button>
        </div>
        {showSim && (
          <div id="what-if-panel" className="mt-8">
            <WhatIfSimulator />
          </div>
        )}
      </section>
    </>,
  );
}
