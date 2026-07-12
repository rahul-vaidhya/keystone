import { apiFetch } from "./http";
import type { Organization, TokenResponse, User } from "../types/auth";

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

  invite: (email: string, password: string, role: "admin" | "member") =>
    apiFetch<User>("/auth/invite", {
      method: "POST",
      body: JSON.stringify({ email, password, role }),
    }),

  getOrg: () => apiFetch<Organization>("/auth/org"),

  renameOrg: (org_name: string) =>
    apiFetch<Organization>("/auth/org", {
      method: "PATCH",
      body: JSON.stringify({ org_name }),
    }),
};
