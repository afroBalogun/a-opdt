import { LeafField, Sprig } from "../components/Botanical";
import { Button, Display, Eyebrow, Fine, Lede, Rule } from "../components/Type";



/*
 * Grid note: the twelve-column rows are placed explicitly rather than
 * auto-flowed. An item whose requested start column is already occupied silently
 * drops to a new row, which is what previously opened a gap under the hero.
 * Every row below sums to twelve or less.
 */
export function Landing() {
  return (
    <>
      <header className="mx-auto max-w-[1320px] px-[clamp(1.25rem,4vw,4.5rem)]">
        <div className="flex flex-wrap items-center justify-between gap-4 py-6 border-b border-ink/15">
          <span className="text-xs uppercase tracking-[0.24em] font-medium">A-OPDT</span>
          <div className="flex flex-wrap items-center gap-3">
            <Button variant="ghost" to="/signin">Sign in</Button>
            <Button to="/signup">Create account</Button>
          </div>
        </div>
      </header>

      {/* ── Opening statement ─────────────────────────────────────────── */}
      <section className="mx-auto max-w-[1320px] px-[clamp(1.25rem,4vw,4.5rem)] py-[clamp(3rem,8vw,7rem)]">
        <div className="grid grid-cols-12 gap-[clamp(1rem,2.4vw,2.25rem)]">
          <div className="col-span-12 lg:col-span-7">
            <Eyebrow>Agentic Open Plant Digital Twin</Eyebrow>
            <Display as="h1" size="lg">A living model<br />of the crop</Display>
          </div>
          <div className="col-span-12 lg:col-span-4 lg:col-start-9 lg:self-end">
            <Lede>
              A-OPDT mirrors a maize crop in software. Sensors report what is
              happening in the field; an eight-layer twin turns those readings
              into an assessment of the plant's condition, and says what it means.
            </Lede>
            <Fine className="mt-5">
              Every value carries its provenance. A measurement and a modelled
              estimate are never shown as the same thing.
            </Fine>
          </div>
        </div>
      </section>

      {/* ── Three movements ───────────────────────────────────────────── */}
      <section className="mx-auto max-w-[1320px] px-[clamp(1.25rem,4vw,4.5rem)] pb-[clamp(3rem,8vw,7rem)]">
        <Rule className="mt-0" />
        <div className="grid grid-cols-12 gap-[clamp(1rem,2.4vw,2.25rem)]">
          {[
            ["01 — Sensing", "Soil, canopy, spectral, fluorescence and volatile signals, gathered on a cadence the plant actually changes on."],
            ["02 — Physiology", "Farquhar, Ball-Berry and Penman-Monteith run over those readings to estimate assimilation, conductance and transpiration."],
            ["03 — Judgement", "An eight-state health machine resolves the stress categories into a single answer, and the reason behind it."],
          ].map(([label, body]) => (
            <div key={label} className="col-span-12 md:col-span-6 lg:col-span-4">
              <Eyebrow strong>{label}</Eyebrow>
              <Lede>{body}</Lede>
            </div>
          ))}
        </div>
      </section>

      {/* ── Full-bleed band ───────────────────────────────────────────── */}
      <section className="relative isolate overflow-hidden bg-moss-deep">
        <LeafField className="absolute inset-0 -z-10 opacity-70" tone="dark" />
        <div className="mx-auto max-w-[1320px] px-[clamp(1.25rem,4vw,4.5rem)] py-[clamp(3.5rem,11vw,9rem)]">
          <Display onDark className="max-w-[16ch]">
            Read the plant,<br />not the average
          </Display>
          <Lede onDark className="mt-8">
            A field is not uniform and a season is not a snapshot. The twin holds
            a continuous picture of one crop, in one place, as it changes.
          </Lede>
          <div className="mt-10">
            <Button variant="light" to="/signup">
              Open a dashboard
            </Button>
          </div>
        </div>
      </section>

      {/* ── Two audiences ─────────────────────────────────────────────── */}
      <section className="mx-auto max-w-[1320px] px-[clamp(1.25rem,4vw,4.5rem)] py-[clamp(3rem,8vw,7rem)]">
        <Display size="sm" className="text-center mb-[clamp(2rem,5vw,3rem)]">
          Two ways to read it
        </Display>
        <div className="grid grid-cols-12 gap-[clamp(1rem,2.4vw,2.25rem)] items-stretch">
          <article className="col-span-12 lg:col-span-5 flex flex-col bg-paper border border-ink/8 p-[clamp(1.25rem,2.4vw,2rem)]">
            <Eyebrow strong>For researchers</Eyebrow>
            <Display size="sm" as="h3" className="mb-4">Every field, with its band</Display>
            <Lede>
              All nineteen modelled parameters, grouped by domain, each against
              its growth-stage thresholds and labelled measured or nominal. Plus
              the active stress categories, the health score, the twin's current
              calibration, and a what-if simulator running the same physiology
              model as the live twin.
            </Lede>
          </article>

          <article className="col-span-12 lg:col-span-5 lg:col-start-7 flex flex-col bg-paper border border-ink/8 p-[clamp(1.25rem,2.4vw,2rem)]">
            <Eyebrow strong>For farmers</Eyebrow>
            <Display size="sm" as="h3" className="mb-4">What to do today</Display>
            <Lede>
              One plain sentence on how the crop is doing, what is causing it,
              and the action worth taking. Below it, only the readings you can
              act on — plus anything that has drifted out of range.
            </Lede>
          </article>
        </div>
      </section>

      {/* ── Ethos ─────────────────────────────────────────────────────── */}
      <section className="mx-auto max-w-[1320px] px-[clamp(1.25rem,4vw,4.5rem)] pb-[clamp(3rem,8vw,7rem)]">
        <Rule className="mt-0" />
        <div className="grid grid-cols-12 gap-[clamp(1rem,2.4vw,2.25rem)] items-end">
          <div className="col-span-12 md:col-span-6 lg:col-span-4">
            <Eyebrow strong>Our ethos</Eyebrow>
            <Fine>
              A digital twin that models more than it measures has to say so.
              Where no instrument reports, A-OPDT shows the stage nominal and
              marks it as such, so a conclusion can always be traced back to
              whether anything actually observed it.
            </Fine>
            <div className="mt-7">
              <Button to="/signup">Get started</Button>
            </div>
          </div>

          {/* Decorative, so it earns its column only on a wide canvas. */}
          <div className="hidden lg:col-span-3 lg:col-start-6 lg:flex justify-center">
            <Sprig className="w-32 h-auto opacity-85" tone="light" />
          </div>

          <div className="col-span-12 md:col-span-6 lg:col-span-4 lg:col-start-9">
            <Display size="sm" as="h3">Grounded in<br />what is measured</Display>
          </div>
        </div>
      </section>

      <footer className="mx-auto max-w-[1320px] px-[clamp(1.25rem,4vw,4.5rem)] pb-12">
        <div className="h-px bg-ink/15" />
        <Fine className="mt-5">A-OPDT — Agentic Open Plant Digital Twin</Fine>
      </footer>
    </>
  );
}
