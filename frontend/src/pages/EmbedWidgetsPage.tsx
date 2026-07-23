import { useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { embedApi } from "../services/embedService";
import { notebooksApi } from "../services/notebooksService";
import { useDialog } from "../hooks/useDialog";
import { ApiError } from "../types/auth";
import type { Widget } from "../types/embed";

function parseOrigins(raw: string): string[] {
  return raw
    .split("\n")
    .map((line) => line.trim())
    .filter((line) => line.length > 0);
}

function CopyField({ label, value }: { label: string; value: string }) {
  const [copied, setCopied] = useState(false);

  async function handleCopy() {
    await navigator.clipboard.writeText(value);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  }

  return (
    <div className="space-y-1">
      <p className="text-xs text-muted">{label}</p>
      <div className="flex items-start gap-2">
        <pre className="flex-1 bg-bg border border-border rounded-md px-2 py-1.5 text-xs font-mono whitespace-pre-wrap break-all">
          {value}
        </pre>
        <button
          type="button"
          onClick={() => void handleCopy()}
          className="shrink-0 text-xs bg-accent text-white rounded-md px-2 py-1.5 hover:opacity-90"
        >
          {copied ? "Copied" : "Copy"}
        </button>
      </div>
    </div>
  );
}

export function EmbedWidgetsPage() {
  const dialog = useDialog();
  const queryClient = useQueryClient();

  const [name, setName] = useState("");
  const [notebookId, setNotebookId] = useState("");
  const [originsText, setOriginsText] = useState("");
  const [expandedId, setExpandedId] = useState<string | null>(null);

  const widgetsQuery = useQuery({ queryKey: ["embed-widgets"], queryFn: embedApi.list });
  const notebooksQuery = useQuery({ queryKey: ["notebooks"], queryFn: notebooksApi.list });

  const invalidateWidgets = () => queryClient.invalidateQueries({ queryKey: ["embed-widgets"] });

  const createMutation = useMutation({
    mutationFn: () =>
      embedApi.create({
        knowledge_base_id: notebookId,
        name: name.trim(),
        allowed_origins: parseOrigins(originsText),
      }),
    onSuccess: () => {
      invalidateWidgets();
      setName("");
      setNotebookId("");
      setOriginsText("");
    },
    onError: (err) =>
      void dialog.alert(err instanceof ApiError ? err.message : "Failed to create widget"),
  });

  const updateMutation = useMutation({
    mutationFn: ({ id, isActive }: { id: string; isActive: boolean }) =>
      embedApi.update(id, { is_active: isActive }),
    onSuccess: invalidateWidgets,
    onError: (err) =>
      void dialog.alert(err instanceof ApiError ? err.message : "Failed to update widget"),
  });

  const deleteMutation = useMutation({
    mutationFn: (id: string) => embedApi.remove(id),
    onSuccess: invalidateWidgets,
    onError: (err) =>
      void dialog.alert(err instanceof ApiError ? err.message : "Failed to delete widget"),
  });

  function handleCreate(e: FormEvent) {
    e.preventDefault();
    if (!name.trim() || !notebookId) return;
    createMutation.mutate();
  }

  async function handleToggleActive(widget: Widget) {
    if (widget.is_active) {
      const confirmed = await dialog.confirm(
        "Revoke this widget? Every embed using it will stop working immediately. This can be undone later.",
        { confirmLabel: "Revoke", danger: true },
      );
      if (!confirmed) return;
    }
    updateMutation.mutate({ id: widget.id, isActive: !widget.is_active });
  }

  async function handleDelete(widget: Widget) {
    const confirmed = await dialog.confirm(
      `Delete "${widget.name}"? Every embed using it will stop working. This cannot be undone.`,
      { confirmLabel: "Delete", danger: true },
    );
    if (!confirmed) return;
    deleteMutation.mutate(widget.id);
  }

  function notebookName(id: string): string {
    return notebooksQuery.data?.find((nb) => nb.id === id)?.name ?? `Notebook ${id.slice(0, 8)}…`;
  }

  const widgets = widgetsQuery.data ?? [];
  const notebooks = notebooksQuery.data ?? [];

  return (
    <div className="flex-1 min-h-0 overflow-y-auto">
      <main className="p-6 max-w-3xl mx-auto w-full">
        <div className="mb-4">
          <h1 className="text-lg font-semibold">Embed widgets</h1>
          <p className="text-muted text-sm">
            Create a paste-able chat widget for one notebook. Visitors on your website chat
            anonymously with that notebook — grounded and cited, same as inside the app.
          </p>
        </div>

        <form
          onSubmit={handleCreate}
          className="bg-surface border border-border rounded-lg p-4 mb-6 space-y-3"
        >
          <div className="flex flex-wrap gap-3">
            <div className="flex flex-col gap-1 flex-1 min-w-[160px]">
              <label htmlFor="widget-name" className="text-xs text-muted">
                Name
              </label>
              <input
                id="widget-name"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="e.g. Support bot"
                className="bg-bg border border-border rounded-md px-2 py-1.5 text-sm"
              />
            </div>
            <div className="flex flex-col gap-1 flex-1 min-w-[160px]">
              <label htmlFor="widget-notebook" className="text-xs text-muted">
                Notebook
              </label>
              <select
                id="widget-notebook"
                value={notebookId}
                onChange={(e) => setNotebookId(e.target.value)}
                className="bg-bg border border-border rounded-md px-2 py-1.5 text-sm"
              >
                <option value="">Select a notebook…</option>
                {notebooks.map((nb) => (
                  <option key={nb.id} value={nb.id}>
                    {nb.name}
                  </option>
                ))}
              </select>
            </div>
          </div>

          <div className="flex flex-col gap-1">
            <label htmlFor="widget-origins" className="text-xs text-muted">
              Allowed origins (one per line)
            </label>
            <textarea
              id="widget-origins"
              value={originsText}
              onChange={(e) => setOriginsText(e.target.value)}
              rows={3}
              placeholder="https://example.com"
              className="bg-bg border border-border rounded-md px-2 py-1.5 text-sm font-mono"
            />
            <p className="text-xs text-muted">
              e.g. https://example.com — leave empty to allow ANY site (not recommended for
              production).
            </p>
          </div>

          <button
            type="submit"
            disabled={createMutation.isPending || !name.trim() || !notebookId}
            className="text-sm bg-accent text-white rounded-md px-3 py-1.5 hover:opacity-90 disabled:opacity-50"
          >
            Create widget
          </button>
        </form>

        {widgetsQuery.isLoading && <p className="text-muted text-sm">Loading…</p>}
        {widgetsQuery.isError && <p className="text-danger text-sm">Failed to load widgets.</p>}
        {widgetsQuery.data && widgets.length === 0 && (
          <p className="text-muted text-sm">No widgets yet. Create one above.</p>
        )}

        <div className="space-y-4">
          {widgets.map((widget) => {
            const isExpanded = expandedId === widget.id;
            const allowsAny = widget.allowed_origins.length === 0;
            return (
              <div
                key={widget.id}
                className={`bg-surface border border-border rounded-lg p-4 space-y-3 ${
                  !widget.is_active ? "opacity-60" : ""
                }`}
              >
                <div className="flex items-center justify-between gap-2">
                  <div className="min-w-0">
                    <div className="flex items-center gap-2">
                      <h2 className="font-medium truncate">{widget.name}</h2>
                      <span
                        className={`text-xs rounded-sm px-1.5 py-0.5 border ${
                          widget.is_active
                            ? "text-success border-success/40"
                            : "text-danger border-danger/40"
                        }`}
                      >
                        {widget.is_active ? "Active" : "Revoked"}
                      </span>
                    </div>
                    <p className="text-xs text-muted truncate">
                      {notebookName(widget.knowledge_base_id)}
                    </p>
                  </div>
                  <div className="flex items-center gap-2 shrink-0">
                    <button
                      type="button"
                      onClick={() => void handleToggleActive(widget)}
                      className={
                        widget.is_active
                          ? "text-xs text-danger border border-danger/40 rounded-md px-2 py-1 hover:bg-danger/10"
                          : "text-xs bg-accent text-white rounded-md px-2 py-1 hover:opacity-90"
                      }
                    >
                      {widget.is_active ? "Revoke" : "Reactivate"}
                    </button>
                    <button
                      type="button"
                      aria-label={`Delete ${widget.name}`}
                      onClick={() => void handleDelete(widget)}
                      className="text-xs text-muted hover:text-danger"
                    >
                      Delete
                    </button>
                  </div>
                </div>

                <div className="flex flex-wrap gap-1.5 items-center text-xs">
                  {allowsAny ? (
                    <span className="text-warning border border-warning/40 rounded-sm px-1.5 py-0.5">
                      Any site ⚠
                    </span>
                  ) : (
                    widget.allowed_origins.map((origin) => (
                      <span
                        key={origin}
                        className="text-muted border border-border rounded-sm px-1.5 py-0.5"
                      >
                        {origin}
                      </span>
                    ))
                  )}
                  <span className="text-muted">
                    Created {new Date(widget.created_at).toLocaleDateString()}
                  </span>
                </div>

                <button
                  type="button"
                  onClick={() => setExpandedId(isExpanded ? null : widget.id)}
                  className="text-xs text-accent hover:underline"
                >
                  {isExpanded ? "Hide embed code" : "Get embed code"}
                </button>

                {isExpanded && (
                  <div className="space-y-3 pt-1">
                    <CopyField label="Script snippet" value={widget.embed_snippet} />
                    <CopyField label="Iframe URL" value={widget.iframe_url} />
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </main>
    </div>
  );
}
