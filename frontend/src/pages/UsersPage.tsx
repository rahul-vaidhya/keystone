import { useState, type FormEvent } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { authApi } from "../services/authService";
import { ApiError } from "../types/auth";
import { useAuth } from "../hooks/useAuth";
import { setStoredAccessToken } from "../services/http";

const ROLE_LABEL: Record<string, string> = {
  owner: "Owner",
  admin: "Admin",
  member: "Member",
};

export function UsersPage() {
  const { user } = useAuth();
  const queryClient = useQueryClient();
  const usersQuery = useQuery({ queryKey: ["users"], queryFn: authApi.listUsers });
  const orgQuery = useQuery({ queryKey: ["org"], queryFn: authApi.getOrg });

  const isAdmin = user?.role === "owner" || user?.role === "admin";

  const [editingOrgName, setEditingOrgName] = useState(false);
  const [orgNameDraft, setOrgNameDraft] = useState("");
  const [renamingOrg, setRenamingOrg] = useState(false);

  const [inviteEmail, setInviteEmail] = useState("");
  const [invitePassword, setInvitePassword] = useState("");
  const [inviteRole, setInviteRole] = useState<"admin" | "member">("member");
  const [inviting, setInviting] = useState(false);

  const [showPasswordForm, setShowPasswordForm] = useState(false);
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [changingPassword, setChangingPassword] = useState(false);

  function startEditingOrgName() {
    setOrgNameDraft(orgQuery.data?.name ?? "");
    setEditingOrgName(true);
  }

  async function handleRenameOrg() {
    const name = orgNameDraft.trim();
    if (!name) return;
    setRenamingOrg(true);
    try {
      await authApi.renameOrg(name);
      await queryClient.invalidateQueries({ queryKey: ["org"] });
      setEditingOrgName(false);
    } catch (err) {
      window.alert(err instanceof ApiError ? err.message : "Failed to rename organization");
    } finally {
      setRenamingOrg(false);
    }
  }

  async function handleInvite(e: FormEvent) {
    e.preventDefault();
    if (!inviteEmail.trim() || !invitePassword) return;
    setInviting(true);
    try {
      await authApi.invite(inviteEmail.trim(), invitePassword, inviteRole);
      await queryClient.invalidateQueries({ queryKey: ["users"] });
      setInviteEmail("");
      setInvitePassword("");
      setInviteRole("member");
    } catch (err) {
      window.alert(err instanceof ApiError ? err.message : "Failed to invite user");
    } finally {
      setInviting(false);
    }
  }

  async function handleRoleChange(userId: string, role: "admin" | "member") {
    try {
      await authApi.changeRole(userId, role);
      await queryClient.invalidateQueries({ queryKey: ["users"] });
    } catch (err) {
      window.alert(err instanceof ApiError ? err.message : "Failed to change role");
    }
  }

  async function handleSetActive(userId: string, nextActive: boolean) {
    if (!nextActive) {
      const confirmed = window.confirm(
        "Remove this member? They will immediately lose access. This can be undone later.",
      );
      if (!confirmed) return;
    }
    try {
      await authApi.setUserActive(userId, nextActive);
      await queryClient.invalidateQueries({ queryKey: ["users"] });
    } catch (err) {
      window.alert(err instanceof ApiError ? err.message : "Failed to update member status");
    }
  }

  async function handleChangePassword(e: FormEvent) {
    e.preventDefault();
    if (!currentPassword || newPassword.length < 8) return;
    setChangingPassword(true);
    try {
      const tokens = await authApi.changePassword(currentPassword, newPassword);
      // The response carries a fresh token pair (password change bumps token_version,
      // invalidating every other session) — persist it now or this tab's own next
      // request would get rejected as stale.
      setStoredAccessToken(tokens.access_token);
      setCurrentPassword("");
      setNewPassword("");
      setShowPasswordForm(false);
      window.alert("Password changed. You've been signed out of any other active sessions.");
    } catch (err) {
      window.alert(err instanceof ApiError ? err.message : "Failed to change password");
    } finally {
      setChangingPassword(false);
    }
  }

  return (
    <div className="flex-1 min-h-0 overflow-y-auto">
      <main className="p-6 max-w-3xl mx-auto w-full">
        <div className="mb-4">
          {editingOrgName ? (
            <div className="flex items-center gap-2">
              <input
                autoFocus
                value={orgNameDraft}
                onChange={(e) => setOrgNameDraft(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") void handleRenameOrg();
                  if (e.key === "Escape") setEditingOrgName(false);
                }}
                className="bg-bg border border-border rounded-md px-2 py-0.5 text-sm"
              />
              <button
                type="button"
                onClick={() => void handleRenameOrg()}
                disabled={renamingOrg}
                className="text-xs bg-accent text-white rounded-md px-2 py-1 hover:opacity-90 disabled:opacity-50"
              >
                Save
              </button>
              <button
                type="button"
                onClick={() => setEditingOrgName(false)}
                className="text-xs text-muted hover:text-fg"
              >
                Cancel
              </button>
            </div>
          ) : (
            <div className="flex items-center gap-2">
              <span className="text-sm text-muted">
                {orgQuery.data?.name ?? (orgQuery.isLoading ? "Loading…" : "")}
              </span>
              {isAdmin && orgQuery.data && (
                <button
                  type="button"
                  aria-label="Rename organization"
                  onClick={startEditingOrgName}
                  className="text-muted hover:text-fg text-xs"
                >
                  Edit
                </button>
              )}
            </div>
          )}
        </div>

        <div className="mb-6">
          {showPasswordForm ? (
            <form
              onSubmit={handleChangePassword}
              className="bg-surface border border-border rounded-lg p-4 flex flex-wrap items-end gap-2"
            >
              <div className="flex flex-col gap-1">
                <label htmlFor="current-password" className="text-xs text-muted">
                  Current password
                </label>
                <input
                  id="current-password"
                  type="password"
                  required
                  value={currentPassword}
                  onChange={(e) => setCurrentPassword(e.target.value)}
                  className="bg-bg border border-border rounded-md px-2 py-1 text-sm"
                />
              </div>
              <div className="flex flex-col gap-1">
                <label htmlFor="new-password" className="text-xs text-muted">
                  New password
                </label>
                <input
                  id="new-password"
                  type="password"
                  required
                  minLength={8}
                  value={newPassword}
                  onChange={(e) => setNewPassword(e.target.value)}
                  className="bg-bg border border-border rounded-md px-2 py-1 text-sm"
                />
              </div>
              <button
                type="submit"
                disabled={changingPassword}
                className="text-sm bg-accent text-white rounded-md px-3 py-1.5 hover:opacity-90 disabled:opacity-50"
              >
                Update password
              </button>
              <button
                type="button"
                onClick={() => {
                  setShowPasswordForm(false);
                  setCurrentPassword("");
                  setNewPassword("");
                }}
                className="text-sm text-muted hover:text-fg px-2 py-1.5"
              >
                Cancel
              </button>
            </form>
          ) : (
            <button
              type="button"
              onClick={() => setShowPasswordForm(true)}
              className="text-xs text-muted hover:text-fg"
            >
              Change your password
            </button>
          )}
        </div>

        <h1 className="text-lg font-semibold mb-4">Team members</h1>

        {isAdmin && (
          <form
            onSubmit={handleInvite}
            className="bg-surface border border-border rounded-lg p-4 mb-6 flex flex-wrap items-end gap-2"
          >
            <div className="flex flex-col gap-1">
              <label htmlFor="invite-email" className="text-xs text-muted">
                Email
              </label>
              <input
                id="invite-email"
                type="email"
                required
                value={inviteEmail}
                onChange={(e) => setInviteEmail(e.target.value)}
                className="bg-bg border border-border rounded-md px-2 py-1 text-sm"
              />
            </div>
            <div className="flex flex-col gap-1">
              <label htmlFor="invite-password" className="text-xs text-muted">
                Initial password
              </label>
              <input
                id="invite-password"
                type="password"
                required
                minLength={8}
                value={invitePassword}
                onChange={(e) => setInvitePassword(e.target.value)}
                className="bg-bg border border-border rounded-md px-2 py-1 text-sm"
              />
            </div>
            <div className="flex flex-col gap-1">
              <label htmlFor="invite-role" className="text-xs text-muted">
                Role
              </label>
              <select
                id="invite-role"
                value={inviteRole}
                onChange={(e) => setInviteRole(e.target.value as "admin" | "member")}
                className="bg-bg border border-border rounded-md px-2 py-1 text-sm"
              >
                <option value="member">Member</option>
                <option value="admin">Admin</option>
              </select>
            </div>
            <button
              type="submit"
              disabled={inviting}
              className="text-sm bg-accent text-white rounded-md px-3 py-1.5 hover:opacity-90 disabled:opacity-50"
            >
              Invite
            </button>
          </form>
        )}

        {usersQuery.isLoading && <p className="text-muted text-sm">Loading…</p>}
        {usersQuery.isError && (
          <p className="text-danger text-sm">Failed to load users.</p>
        )}

        {usersQuery.data && (
          <div className="bg-surface border border-border rounded-lg overflow-hidden">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-muted border-b border-border">
                  <th className="px-4 py-2 font-medium">Email</th>
                  <th className="px-4 py-2 font-medium">Role</th>
                  <th className="px-4 py-2 font-medium" />
                </tr>
              </thead>
              <tbody>
                {usersQuery.data.map((u) => {
                  const isSelf = u.id === user?.id;
                  const isOwner = u.role === "owner";
                  const canEdit = !isSelf && !isOwner;

                  return (
                    <tr
                      key={u.id}
                      className={`border-b border-border last:border-0 ${!u.is_active ? "opacity-50" : ""}`}
                    >
                      <td className="px-4 py-2">
                        {u.email}
                        {!u.is_active && (
                          <span className="ml-2 text-xs bg-danger/10 text-danger rounded-sm px-1.5 py-0.5">
                            Removed
                          </span>
                        )}
                      </td>
                      <td className="px-4 py-2">
                        <span className="text-xs bg-bg border border-border rounded-sm px-2 py-0.5">
                          {ROLE_LABEL[u.role] ?? u.role}
                        </span>
                      </td>
                      <td className="px-4 py-2 text-right">
                        <div className="flex items-center justify-end gap-2">
                          {canEdit && u.is_active && (
                            <select
                              value={u.role}
                              onChange={(e) =>
                                void handleRoleChange(u.id, e.target.value as "admin" | "member")
                              }
                              className="bg-bg border border-border rounded-md px-2 py-1 text-sm"
                            >
                              <option value="admin">Admin</option>
                              <option value="member">Member</option>
                            </select>
                          )}
                          {isAdmin && canEdit ? (
                            <button
                              type="button"
                              onClick={() => void handleSetActive(u.id, !u.is_active)}
                              className={
                                u.is_active
                                  ? "text-xs text-danger border border-danger/40 rounded-md px-2 py-1 hover:bg-danger/10"
                                  : "text-xs bg-accent text-white rounded-md px-2 py-1 hover:opacity-90"
                              }
                            >
                              {u.is_active ? "Remove" : "Reactivate"}
                            </button>
                          ) : (
                            !canEdit && (
                              <span className="text-muted text-xs">
                                {isSelf ? "(you)" : "—"}
                              </span>
                            )
                          )}
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </main>
    </div>
  );
}
