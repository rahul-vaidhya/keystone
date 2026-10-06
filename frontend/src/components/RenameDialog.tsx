import { useEffect, useId, useState, type FormEvent } from "react";
import { Modal } from "./Modal";

/**
 * Small single-field rename dialog on top of the shared Modal shell (the app's
 * DialogContext only offers alert/confirm, no prompt). Used for notebook rename.
 */
export function RenameDialog({
  open,
  title,
  label,
  initialValue,
  pending = false,
  onClose,
  onSubmit,
}: {
  open: boolean;
  title: string;
  label: string;
  initialValue: string;
  pending?: boolean;
  onClose: () => void;
  onSubmit: (value: string) => void;
}) {
  const titleId = useId();
  const inputId = useId();
  const [value, setValue] = useState(initialValue);

  // Reset to the current name every time the dialog opens.
  useEffect(() => {
    if (open) setValue(initialValue);
  }, [open, initialValue]);

  const trimmed = value.trim();
  const canSave = trimmed.length > 0 && trimmed !== initialValue && !pending;

  function handleSubmit(e: FormEvent) {
    e.preventDefault();
    if (canSave) onSubmit(trimmed);
  }

  return (
    <Modal open={open} onClose={onClose} titleId={titleId}>
      <form onSubmit={handleSubmit} className="space-y-4">
        <h2 id={titleId} className="text-base font-semibold">
          {title}
        </h2>
        <label htmlFor={inputId} className="block space-y-1">
          <span className="text-sm text-muted">{label}</span>
          <input
            id={inputId}
            value={value}
            maxLength={200}
            onChange={(e) => setValue(e.target.value)}
            className="w-full bg-bg border border-border rounded-md px-3 py-2 text-sm focus:outline-none focus:border-accent"
          />
        </label>
        <div className="flex justify-end gap-2">
          <button
            type="button"
            onClick={onClose}
            className="text-sm text-muted hover:text-text px-3 py-1.5"
          >
            Cancel
          </button>
          <button
            type="submit"
            disabled={!canSave}
            className="text-sm bg-accent text-white rounded-md px-3 py-1.5 hover:opacity-90 disabled:opacity-50"
          >
            {pending ? "Saving…" : "Save"}
          </button>
        </div>
      </form>
    </Modal>
  );
}
