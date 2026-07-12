import { apiFetch } from "./http";
import type { AccessRole } from "../types/accessRoles";

export const accessRolesApi = {
  listRoles: () => apiFetch<AccessRole[]>("/access-roles"),

  createRole: (name: string) =>
    apiFetch<AccessRole>("/access-roles", { method: "POST", body: JSON.stringify({ name }) }),

  deleteRole: (roleId: string) =>
    apiFetch<void>(`/access-roles/${roleId}`, { method: "DELETE" }),

  grantTag: (roleId: string, tagId: string) =>
    apiFetch<void>(`/access-roles/${roleId}/tags/${tagId}`, { method: "POST" }),

  revokeTag: (roleId: string, tagId: string) =>
    apiFetch<void>(`/access-roles/${roleId}/tags/${tagId}`, { method: "DELETE" }),

  assignUser: (roleId: string, userId: string) =>
    apiFetch<void>(`/access-roles/${roleId}/users/${userId}`, { method: "POST" }),

  removeUser: (roleId: string, userId: string) =>
    apiFetch<void>(`/access-roles/${roleId}/users/${userId}`, { method: "DELETE" }),
};
