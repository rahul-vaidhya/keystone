import { apiFetch } from "../lib/api";
import type { TokenResponse, User } from "../models/auth";

export const authApi = {
  signup: (email: string, password: string, org_name: string) =>
    apiFetch<TokenResponse>("/auth/signup", {
      method: "POST",
      body: JSON.stringify({ email, password, org_name }),
    }),

  login: (email: string, password: string, org_id?: string) =>
    apiFetch<TokenResponse>("/auth/login", {
      method: "POST",
      body: JSON.stringify({ email, password, org_id }),
    }),

  refresh: () =>
    apiFetch<TokenResponse>("/auth/refresh", { method: "POST" }),

  logout: () => apiFetch<void>("/auth/logout", { method: "POST" }),

  me: () => apiFetch<User>("/auth/me"),

  listUsers: () => apiFetch<User[]>("/auth/users"),

  changeRole: (userId: string, role: "admin" | "member") =>
    apiFetch<User>(`/auth/users/${userId}/role`, {
      method: "PATCH",
      body: JSON.stringify({ role }),
    }),
};
