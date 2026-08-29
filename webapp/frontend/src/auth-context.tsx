/*
 * Who is signed in, shared across the route tree.
 *
 * The stored token is validated against the API on first load rather than
 * trusted: it may have expired, or been issued under a previous signing key,
 * in which case the correct behaviour is to sign the user out rather than
 * render a dashboard that will fail every request.
 */
import { createContext, useContext, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import { clearToken, getToken, me, type User } from "./session";

type AuthState = {
  user: User | null;
  /** True until the stored token has been checked, so routes do not redirect early. */
  restoring: boolean;
  setUser: (u: User | null) => void;
  signOut: () => void;
};

const Ctx = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [restoring, setRestoring] = useState(true);

  useEffect(() => {
    if (!getToken()) { setRestoring(false); return; }
    me()
      .then(setUser)
      .catch(() => clearToken())
      .finally(() => setRestoring(false));
  }, []);

  const value = useMemo<AuthState>(() => ({
    user,
    restoring,
    setUser,
    signOut: () => { clearToken(); setUser(null); },
  }), [user, restoring]);

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useAuth(): AuthState {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("useAuth must be used inside <AuthProvider>");
  return ctx;
}
