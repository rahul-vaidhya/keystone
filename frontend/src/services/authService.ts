import { apiFetch } from "./http";
import type { InviteOut, Organization, TokenResponse, User } from "../types/auth";

export const authApi = {
  signup: (email: string, password: string, org_name: string, name?: string) =>
    apiFetch<TokenResponse>("/auth/signup", {
      method: "POST",
      body: JSON.stringify({ email, password, org_name, name: name || undefined }),
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

  setUserActive: (userId: string, is_active: boolean) =>
    apiFetch<User>(`/auth/users/${userId}/status`, {
      method: "PATCH",
      body: JSON.stringify({ is_active }),
    }),

  changePassword: (current_password: string, new_password: string) =>
    apiFetch<TokenResponse>("/auth/me/password", {
      method: "POST",
      body: JSON.stringify({ current_password, new_password }),
    }),

  invite: (email: string, role: "admin" | "member") =>
    apiFetch<InviteOut>("/auth/invite", {
      method: "POST",
      body: JSON.stringify({ email, role }),
    }),

  acceptInvite: (orgId: string, token: string, password: string) =>
    apiFetch<TokenResponse>("/auth/accept-invite", {
      method: "POST",
      body: JSON.stringify({ org_id: orgId, token, password }),
    }),

  getOrg: () => apiFetch<Organization>("/auth/org"),

  renameOrg: (org_name: string) =>
    apiFetch<Organization>("/auth/org", {
      method: "PATCH",
      body: JSON.stringify({ org_name }),
    }),
};
