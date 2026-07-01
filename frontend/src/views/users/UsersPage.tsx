import { useQuery, useQueryClient } from "@tanstack/react-query";
import { authApi } from "../../controllers/authController";
import { ApiError } from "../../models/auth";
import { useAuth } from "../../lib/auth";

const ROLE_LABEL: Record<string, string> = {
  owner: "Owner",
  admin: "Admin",
  member: "Member",
};

export function UsersPage() {
  const { user } = useAuth();
  const queryClient = useQueryClient();
  const usersQuery = useQuery({ queryKey: ["users"], queryFn: authApi.listUsers });

  async function handleRoleChange(userId: string, role: "admin" | "member") {
    try {
      await authApi.changeRole(userId, role);
      await queryClient.invalidateQueries({ queryKey: ["users"] });
    } catch (err) {
      window.alert(err instanceof ApiError ? err.message : "Failed to change role");
    }
  }

  return (
    <div className="flex-1 min-h-0 overflow-y-auto">
      <main className="p-6 max-w-3xl mx-auto w-full">
        <h1 className="text-lg font-semibold mb-4">Team members</h1>

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
                    <tr key={u.id} className="border-b border-border last:border-0">
                      <td className="px-4 py-2">{u.email}</td>
                      <td className="px-4 py-2">
                        <span className="text-xs bg-bg border border-border rounded-sm px-2 py-0.5">
                          {ROLE_LABEL[u.role] ?? u.role}
                        </span>
                      </td>
                      <td className="px-4 py-2 text-right">
                        {canEdit ? (
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
                        ) : (
                          <span className="text-muted text-xs">
                            {isSelf ? "(you)" : "—"}
                          </span>
                        )}
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
