import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

import { auth as authApi } from "./api";
import { clearAuth, getUser, refreshAccessToken, setAuth, subscribe } from "./auth";
import type { UserOut } from "./types";

interface AuthState {
  user: UserOut | null;
  /** True until the boot refresh settles — render neither app nor login yet. */
  loading: boolean;
  signIn: (email: string, password: string) => Promise<void>;
  signUp: (email: string, password: string, displayName?: string) => Promise<void>;
  signOut: () => Promise<void>;
  update: (patch: { display_name?: string; memory_enabled?: boolean }) => Promise<void>;
}

const Ctx = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<UserOut | null>(getUser());
  const [loading, setLoading] = useState(true);

  // The token module is the source of truth; React mirrors it.
  useEffect(() => subscribe(setUser), []);

  useEffect(() => {
    // One refresh on boot. The access token died with the last tab; the
    // HttpOnly cookie is what proves the session is still good.
    let cancelled = false;
    refreshAccessToken().finally(() => {
      if (!cancelled) setLoading(false);
    });
    return () => {
      cancelled = true;
    };
  }, []);

  const signIn = useCallback(async (email: string, password: string) => {
    const body = await authApi.login(email, password);
    setAuth(body.access_token, body.user);
  }, []);

  const signUp = useCallback(
    async (email: string, password: string, displayName?: string) => {
      const body = await authApi.register(email, password, displayName);
      setAuth(body.access_token, body.user);
    },
    [],
  );

  const signOut = useCallback(async () => {
    try {
      await authApi.logout();
    } finally {
      // Local state clears regardless: a failed network call must not leave
      // someone looking at a signed-in screen they think they left.
      clearAuth();
    }
  }, []);

  const update = useCallback(
    async (patch: { display_name?: string; memory_enabled?: boolean }) => {
      const updated = await authApi.updateMe(patch);
      setUser(updated);
    },
    [],
  );

  const value = useMemo(
    () => ({ user, loading, signIn, signUp, signOut, update }),
    [user, loading, signIn, signUp, signOut, update],
  );

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useAuth(): AuthState {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("useAuth must be used inside AuthProvider");
  return ctx;
}
