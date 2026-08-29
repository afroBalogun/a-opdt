import { Navigate, Route, Routes, useLocation } from "react-router-dom";
import "./index.css";
import { useAuth } from "./auth-context";
import { Button, Fine, Skeleton } from "./components/Type";
import { Landing } from "./views/Landing";
import { Auth } from "./views/Auth";
import { ResearcherDashboard } from "./views/ResearcherDashboard";
import { FarmerDashboard } from "./views/FarmerDashboard";

/**
 * Session restore runs before we know which dashboard the user gets, so this
 * cannot mirror a specific layout the way DashboardSkeleton does. It shows the
 * page frame and header instead of a bare line of text, which is enough to stop
 * the window looking broken while the token is verified.
 */
function Loading({ message }: { message: string }) {
  return (
    <div
      className="mx-auto max-w-[1320px] px-[clamp(1.25rem,4vw,4.5rem)]"
      aria-busy="true"
      aria-live="polite"
    >
      <span className="sr-only">{message}</span>
      <div className="flex items-center justify-between gap-4 py-6 border-b border-ink/15">
        <span className="text-xs uppercase tracking-[0.24em] font-medium">A-OPDT</span>
        <Skeleton className="h-2 w-32" />
      </div>
      <div className="py-16">
        <Skeleton className="h-2 w-40" />
        <Skeleton className="h-10 w-[min(22rem,80%)] mt-4" />
      </div>
    </div>
  );
}

/** Send signed-in users straight to their dashboard rather than the front door. */
function PublicOnly({ children }: { children: React.ReactNode }) {
  const { user, restoring } = useAuth();
  if (restoring) return <Loading message="Restoring your session…" />;
  if (user) return <Navigate to="/dashboard" replace />;
  return <>{children}</>;
}

function RequireAuth({ children }: { children: React.ReactNode }) {
  const { user, restoring } = useAuth();
  const location = useLocation();
  if (restoring) return <Loading message="Restoring your session…" />;
  // Remember where they were headed so sign-in can return them there.
  if (!user) return <Navigate to="/signin" replace state={{ from: location }} />;
  return <>{children}</>;
}

function DashboardChrome() {
  const { user, signOut } = useAuth();
  if (!user) return null;
  return (
    <>
      <header className="mx-auto max-w-[1320px] px-[clamp(1.25rem,4vw,4.5rem)]">
        <div className="flex flex-wrap items-center justify-between gap-4 py-6 border-b border-ink/15">
          <span className="text-xs uppercase tracking-[0.24em] font-medium">A-OPDT</span>
          <div className="flex flex-wrap items-center gap-4">
            <Fine>{user.name} · {user.role === "researcher" ? "Researcher" : "Farmer"}</Fine>
            <Button variant="ghost" onClick={signOut}>Sign out</Button>
          </div>
        </div>
      </header>
      {user.role === "researcher"
        ? <ResearcherDashboard user={user} />
        : <FarmerDashboard user={user} />}
    </>
  );
}

export default function App() {
  return (
    <Routes>
      <Route path="/" element={<PublicOnly><Landing /></PublicOnly>} />
      <Route path="/signin" element={<PublicOnly><Auth mode="login" /></PublicOnly>} />
      <Route path="/signup" element={<PublicOnly><Auth mode="signup" /></PublicOnly>} />
      <Route path="/dashboard" element={<RequireAuth><DashboardChrome /></RequireAuth>} />
      {/* Anything unrecognised goes to the front door rather than a blank screen. */}
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
