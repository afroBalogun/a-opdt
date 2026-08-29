import { useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { LeafField } from "../components/Botanical";
import { Button, Display, Eyebrow, Fine } from "../components/Type";
import { login, register, type Role } from "../session";
import { useAuth } from "../auth-context";

type Props = { mode: "signup" | "login" };

const FIELD = "w-full font-sans text-[0.9375rem] px-3.5 py-2.5 bg-bone text-ink " +
              "border border-ink/15 rounded-none focus:outline-none focus:border-moss";
const LABEL = "block text-[0.625rem] uppercase tracking-[0.18em] text-muted mb-2";

const SEG_BASE =
  "font-sans text-[0.6875rem] uppercase tracking-[0.16em] px-2 py-3 cursor-pointer " +
  "border text-center no-underline";
const SEG_ON = "bg-ink text-bone border-ink";
const SEG_OFF = "bg-transparent text-muted border-ink/15";

/** Sign-in and create-account are distinct URLs, so the toggle is two links. */
function ModeToggle({ mode }: { mode: "signup" | "login" }) {
  return (
    <div role="group" aria-label="Account action"
         className="grid grid-cols-2 max-[420px]:grid-cols-1 mb-6">
      <Link to="/signin" aria-current={mode === "login" ? "page" : undefined}
            className={[SEG_BASE, mode === "login" ? SEG_ON : SEG_OFF].join(" ")}>
        Sign in
      </Link>
      <Link to="/signup" aria-current={mode === "signup" ? "page" : undefined}
            className={[SEG_BASE, "max-[420px]:border-t-0 min-[421px]:border-l-0",
                        mode === "signup" ? SEG_ON : SEG_OFF].join(" ")}>
        Create account
      </Link>
    </div>
  );
}

function Segmented({ options, value, onChange, label }: {
  options: { value: string; label: string }[];
  value: string;
  onChange: (v: string) => void;
  label: string;
}) {
  return (
    <div role="group" aria-label={label} className="grid grid-cols-2 max-[420px]:grid-cols-1">
      {options.map((o, i) => (
        <button
          key={o.value}
          type="button"
          aria-pressed={value === o.value}
          onClick={() => onChange(o.value)}
          className={[
            "font-sans text-[0.6875rem] uppercase tracking-[0.16em] px-2 py-3 cursor-pointer border",
            i > 0 && "max-[420px]:border-t-0 min-[421px]:border-l-0",
            value === o.value
              ? "bg-ink text-bone border-ink"
              : "bg-transparent text-muted border-ink/15",
          ].filter(Boolean).join(" ")}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

export function Auth({ mode }: Props) {
  const navigate = useNavigate();
  const location = useLocation();
  const { setUser } = useAuth();
  // Return the user to whatever they were trying to reach, if anything.
  const from = (location.state as { from?: { pathname: string } } | null)?.from?.pathname;

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [name, setName] = useState("");
  const [role, setRole] = useState<Role>("farmer");
  const [plot, setPlot] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const signup = mode === "signup";

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const user = signup
        ? await register({ email, password, name, role, plot_name: plot || undefined })
        : await login(email, password);
      setUser(user);
      navigate(from ?? "/dashboard", { replace: true });
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="grid min-h-screen lg:grid-cols-[1.1fr_1fr]">
      <aside className="relative overflow-hidden bg-moss-deep min-h-[34vh] lg:min-h-0">
        <LeafField className="absolute inset-0 w-full h-full" tone="dark" />
        <div className="relative z-10 h-full flex flex-col justify-end p-[clamp(2rem,5vw,4rem)]">
          <Eyebrow onDark>Agentic Open Plant Digital Twin</Eyebrow>
          <Display onDark size="sm">A living model<br />of the crop</Display>
        </div>
      </aside>

      <main className="w-full max-w-[30rem] self-center p-[clamp(2rem,5vw,4.5rem)]">
        <Link to="/"
              className="inline-block text-xs uppercase tracking-[0.24em] font-medium mb-12 text-ink no-underline">
          ← A-OPDT
        </Link>

        <Eyebrow>{signup ? "Create account" : "Welcome back"}</Eyebrow>
        <Display size="sm" as="h1" className="mb-8">
          {signup ? "Open a dashboard" : "Sign in"}
        </Display>

        <ModeToggle mode={mode} />

        <form onSubmit={submit}>
          {signup && (
            <>
              <label className="block mb-5">
                <span className={LABEL}>Full name</span>
                <input className={FIELD} value={name} required autoComplete="name"
                       onChange={(e) => setName(e.target.value)} />
              </label>

              <div className="block mb-5">
                <span className={LABEL}>I am a</span>
                <Segmented
                  label="Account role"
                  value={role}
                  onChange={(v) => setRole(v as Role)}
                  options={[{ value: "farmer", label: "Farmer" },
                            { value: "researcher", label: "Researcher" }]}
                />
                <Fine className="mt-2.5">
                  {role === "farmer"
                    ? "A plain reading of how the crop is doing, and the action worth taking today."
                    : "Every modelled parameter against its stage bands, stress categories, and the what-if simulator."}
                </Fine>
              </div>

              {role === "farmer" && (
                <label className="block mb-5">
                  <span className={LABEL}>Plot name (optional)</span>
                  <input className={FIELD} value={plot} placeholder="North Plot"
                         onChange={(e) => setPlot(e.target.value)} />
                </label>
              )}
            </>
          )}

          <label className="block mb-5">
            <span className={LABEL}>Email</span>
            <input className={FIELD} type="email" value={email} required autoComplete="email"
                   onChange={(e) => setEmail(e.target.value)} />
          </label>

          <label className="block mb-5">
            <span className={LABEL}>Password</span>
            <input className={FIELD} type="password" value={password} required
                   minLength={signup ? 8 : undefined}
                   autoComplete={signup ? "new-password" : "current-password"}
                   onChange={(e) => setPassword(e.target.value)} />
            {signup && <Fine className="mt-1.5">At least 8 characters.</Fine>}
          </label>

          {error && (
            <div className="border border-ink/15 border-l-[3px] border-l-crit bg-paper px-4 py-3 text-[0.8125rem] text-ink-soft mb-5">
              {error}
            </div>
          )}

          <Button type="submit" variant="solid" full disabled={busy}>
            {busy ? "Working…" : signup ? "Create account" : "Sign in"}
          </Button>
        </form>
      </main>
    </div>
  );
}
