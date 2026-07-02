import {
  createContext,
  useCallback,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { authApi } from "../services/authService";
import { ApiError, type User } from "../types/auth";
import { getStoredAccessToken, setStoredAccessToken } from "../services/http";

type AuthState = {
  user: User | null;
  loading: boolean;
  login: (email: string, password: string, orgId?: string) => Promise<void>;
  signup: (email: string, password: string, orgName: string) => Promise<void>;
  logout: () => Promise<void>;
};

export const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);

  const bootstrap = useCallback(async () => {
    const token = getStoredAccessToken();
    if (!token) {
      setUser(null);
      setLoading(false);
      return;
    }
    try {
      setUser(await authApi.me());
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        try {
          const refreshed = await authApi.refresh();
          setStoredAccessToken(refreshed.access_token);
          setUser(await authApi.me());
          return;
        } catch {
          setStoredAccessToken(null);
        }
      }
      setUser(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void bootstrap();
  }, [bootstrap]);

  const login = useCallback(async (email: string, password: string, orgId?: string) => {
    const tokens = await authApi.login(email, password, orgId);
    setStoredAccessToken(tokens.access_token);
    setUser(await authApi.me());
  }, []);

  const signup = useCallback(async (email: string, password: string, orgName: string) => {
    const tokens = await authApi.signup(email, password, orgName);
    setStoredAccessToken(tokens.access_token);
    setUser(await authApi.me());
  }, []);

  const logout = useCallback(async () => {
    try {
      await authApi.logout();
    } finally {
      setStoredAccessToken(null);
      setUser(null);
    }
  }, []);

  const value = useMemo(
    () => ({ user, loading, login, signup, logout }),
    [user, loading, login, signup, logout],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}
