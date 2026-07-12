import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { accessRolesApi } from "../services/accessRolesService";
import { authApi } from "../services/authService";
import { documentsApi } from "../services/documentsService";
import { ApiError } from "../types/auth";

// An Access Role is deliberately separate from the system role (owner/admin/member,
// managed on UsersPage) — this page controls resource-tag grants, not who can invite
// or change system roles.
export function AccessRolesPage() {
  const queryClient = useQueryClient();
  const [newRoleName, setNewRoleName] = useState("");
  const [newTagName, setNewTagName] = useState("");

  const rolesQuery = useQuery({ queryKey: ["access-roles"], queryFn: accessRolesApi.listRoles });
  const tagsQuery = useQuery({ queryKey: ["tags"], queryFn: documentsApi.listTags });
  const usersQuery = useQuery({ queryKey: ["users"], queryFn: authApi.listUsers });

  const invalidateRoles = () => queryClient.invalidateQueries({ queryKey: ["access-roles"] });

  const createRoleMutation = useMutation({
    mutationFn: (name: string) => accessRolesApi.createRole(name),
    onSuccess: invalidateRoles,
    onError: (err) =>
      window.alert(err instanceof ApiError ? err.message : "Failed to create Access Role"),
  });

  const createTagMutation = useMutation({
    mutationFn: (name: string) => documentsApi.createTag(name),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["tags"] }),
    onError: (err) => window.alert(err instanceof ApiError ? err.message : "Failed to create tag"),
  });

  const deleteRoleMutation = useMutation({
    mutationFn: (roleId: string) => accessRolesApi.deleteRole(roleId),
    onSuccess: invalidateRoles,
  });

  const grantTagMutation = useMutation({
    mutationFn: ({ roleId, tagId }: { roleId: string; tagId: string }) =>
      accessRolesApi.grantTag(roleId, tagId),
    onSuccess: invalidateRoles,
    onError: (err) => window.alert(err instanceof ApiError ? err.message : "Failed to grant tag"),
  });

  const revokeTagMutation = useMutation({
    mutationFn: ({ roleId, tagId }: { roleId: string; tagId: string }) =>
      accessRolesApi.revokeTag(roleId, tagId),
    onSuccess: invalidateRoles,
  });

  const assignUserMutation = useMutation({
    mutationFn: ({ roleId, userId }: { roleId: string; userId: string }) =>
      accessRolesApi.assignUser(roleId, userId),
    onSuccess: invalidateRoles,
    onError: (err) =>
      window.alert(err instanceof ApiError ? err.message : "Failed to assign member"),
  });

  const removeUserMutation = useMutation({
    mutationFn: ({ roleId, userId }: { roleId: string; userId: string }) =>
      accessRolesApi.removeUser(roleId, userId),
    onSuccess: invalidateRoles,
  });

  function handleCreateRole() {
    const name = newRoleName.trim();
    if (!name) return;
    createRoleMutation.mutate(name);
    setNewRoleName("");
  }

  function handleCreateTag() {
    const name = newTagName.trim();
    if (!name) return;
    createTagMutation.mutate(name);
    setNewTagName("");
  }

  const tagName = (tagId: string) => tagsQuery.data?.find((t) => t.id === tagId)?.name ?? tagId;
  const userEmail = (userId: string) =>
    usersQuery.data?.find((u) => u.id === userId)?.email ?? userId;

  return (
    <div className="flex-1 min-h-0 overflow-y-auto">
      <main className="p-6 max-w-3xl mx-auto w-full">
        <div className="flex items-center justify-between mb-4">
          <div>
            <h1 className="text-lg font-semibold">Access Roles</h1>
            <p className="text-muted text-sm">
              Grant a role tags, then assign it to members — anyone holding the role can
              see folders/documents carrying those tags.
            </p>
          </div>
        </div>

        <div className="flex gap-2 mb-6">
          <input
            value={newRoleName}
            placeholder="New Access Role name (e.g. Finance Team)"
            onChange={(e) => setNewRoleName(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && handleCreateRole()}
            className="flex-1 bg-bg border border-border rounded-md px-3 py-1.5 text-sm"
          />
          <button
            type="button"
            onClick={handleCreateRole}
            disabled={createRoleMutation.isPending}
            className="text-sm bg-accent text-white rounded-md px-3 py-1.5 hover:opacity-90 disabled:opacity-50"
          >
            Create
          </button>
        </div>

        <div className="mb-6">
          <p className="text-xs text-muted mb-1">
            Tags (organize documents/folders — grant one to a role below to control access)
          </p>
          <div className="flex flex-wrap gap-1.5 items-center">
            {tagsQuery.data?.map((t) => (
              <span
                key={t.id}
                className="text-xs border border-border rounded-sm px-2 py-0.5 text-muted"
              >
                {t.name}
              </span>
            ))}
            <input
              value={newTagName}
              placeholder="+ new tag"
              onChange={(e) => setNewTagName(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && handleCreateTag()}
              className="bg-bg border border-border rounded-sm px-2 py-0.5 text-xs w-24"
            />
          </div>
        </div>

        {rolesQuery.isLoading && <p className="text-muted text-sm">Loading…</p>}
        {rolesQuery.isError && <p className="text-danger text-sm">Failed to load Access Roles.</p>}
        {rolesQuery.data && rolesQuery.data.length === 0 && (
          <p className="text-muted text-sm">
            No Access Roles yet. Create one above to start granting tag-based access.
          </p>
        )}

        <div className="space-y-4">
          {rolesQuery.data?.map((role) => {
            const grantableTags = (tagsQuery.data ?? []).filter(
              (t) => !role.tag_ids.includes(t.id),
            );
            const assignableUsers = (usersQuery.data ?? []).filter(
              (u) => !role.user_ids.includes(u.id),
            );
            return (
              <div
                key={role.id}
                className="bg-surface border border-border rounded-lg p-4 space-y-3"
              >
                <div className="flex items-center justify-between">
                  <h2 className="font-medium">{role.name}</h2>
                  <button
                    type="button"
                    aria-label={`Delete ${role.name}`}
                    onClick={() => deleteRoleMutation.mutate(role.id)}
                    className="text-muted hover:text-danger text-sm"
                  >
                    Delete
                  </button>
                </div>

                <div>
                  <p className="text-xs text-muted mb-1">Granted tags</p>
                  <div className="flex flex-wrap gap-1.5 items-center">
                    {role.tag_ids.map((tagId) => (
                      <span
                        key={tagId}
                        className="inline-flex items-center gap-1 text-xs border border-border rounded-sm px-2 py-0.5"
                      >
                        {tagName(tagId)}
                        <button
                          type="button"
                          aria-label={`Revoke ${tagName(tagId)} from ${role.name}`}
                          onClick={() =>
                            revokeTagMutation.mutate({ roleId: role.id, tagId })
                          }
                          className="text-muted hover:text-danger"
                        >
                          ×
                        </button>
                      </span>
                    ))}
                    {grantableTags.length > 0 && (
                      <select
                        aria-label={`Grant a tag to ${role.name}`}
                        value=""
                        onChange={(e) => {
                          if (!e.target.value) return;
                          grantTagMutation.mutate({ roleId: role.id, tagId: e.target.value });
                          e.target.value = "";
                        }}
                        className="bg-bg border border-border rounded-sm text-xs px-1.5 py-0.5"
                      >
                        <option value="">+ grant tag</option>
                        {grantableTags.map((t) => (
                          <option key={t.id} value={t.id}>
                            {t.name}
                          </option>
                        ))}
                      </select>
                    )}
                  </div>
                </div>

                <div>
                  <p className="text-xs text-muted mb-1">Members</p>
                  <div className="flex flex-wrap gap-1.5 items-center">
                    {role.user_ids.map((userId) => (
                      <span
                        key={userId}
                        className="inline-flex items-center gap-1 text-xs border border-border rounded-sm px-2 py-0.5"
                      >
                        {userEmail(userId)}
                        <button
                          type="button"
                          aria-label={`Remove ${userEmail(userId)} from ${role.name}`}
                          onClick={() =>
                            removeUserMutation.mutate({ roleId: role.id, userId })
                          }
                          className="text-muted hover:text-danger"
                        >
                          ×
                        </button>
                      </span>
                    ))}
                    {assignableUsers.length > 0 && (
                      <select
                        aria-label={`Assign a member to ${role.name}`}
                        value=""
                        onChange={(e) => {
                          if (!e.target.value) return;
                          assignUserMutation.mutate({ roleId: role.id, userId: e.target.value });
                          e.target.value = "";
                        }}
                        className="bg-bg border border-border rounded-sm text-xs px-1.5 py-0.5"
                      >
                        <option value="">+ assign member</option>
                        {assignableUsers.map((u) => (
                          <option key={u.id} value={u.id}>
                            {u.email}
                          </option>
                        ))}
                      </select>
                    )}
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      </main>
    </div>
  );
}
