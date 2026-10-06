import { useId, useState } from "react";
import { Modal } from "./Modal";

/**
 * Bespoke replacement for FolderTree's old native-prompt-based cascade/reflow
 * chooser. Two clearly labeled, visually distinct action areas instead of a bare
 * text box: a non-destructive "move contents up" option (no confirmation needed
 * — nothing is deleted) and a destructive "delete folder and subfolders" option that
 * keeps a typed-confirmation safety check, but as a validated text field gating a
 * disabled/enabled button rather than the only way to make a choice at all.
 *
 * Copy must match backend semantics: cascade deletes the folder subtree, but
 * documents are NEVER deleted — `documents.folder_id` is ON DELETE SET NULL, so
 * every document anywhere in the subtree lands in Repository root.
 */
export function FolderDeleteDialog({
  open,
  folderName,
  onClose,
  onChoose,
}: {
  open: boolean;
  folderName: string;
  onClose: () => void;
  onChoose: (mode: "cascade" | "reflow") => void;
}) {
  const titleId = useId();
  const [confirmText, setConfirmText] = useState("");
  const canCascade = confirmText === folderName;

  function handleClose() {
    setConfirmText("");
    onClose();
  }

  function handleChoose(mode: "cascade" | "reflow") {
    setConfirmText("");
    onChoose(mode);
  }

  return (
    <Modal open={open} onClose={handleClose} titleId={titleId}>
      <h2 id={titleId} className="text-base font-semibold">
        Delete &quot;{folderName}&quot;?
      </h2>
      <p className="text-sm text-muted">
        This folder has folders or documents inside it. Choose what happens to them:
      </p>

      <button
        type="button"
        onClick={() => handleChoose("reflow")}
        className="w-full text-left border border-border rounded-md p-3 hover:border-accent transition"
      >
        <span className="block text-sm font-medium">Move contents up a level</span>
        <span className="block text-xs text-muted mt-1">
          Its subfolders and documents move into the parent folder (or Repository root);
          only this now-empty folder is deleted.
        </span>
      </button>

      <div className="border border-danger/40 rounded-md p-3 space-y-2">
        <p className="text-sm font-medium text-danger">Delete folder and subfolders</p>
        <p className="text-xs text-muted">
          Permanently deletes this folder and all of its subfolders. Documents are not
          deleted — any documents inside are moved to Repository root. This cannot be undone.
        </p>
        <label className="block space-y-1">
          <span className="text-xs text-muted">Type &quot;{folderName}&quot; to confirm</span>
          <input
            value={confirmText}
            onChange={(e) => setConfirmText(e.target.value)}
            className="w-full bg-bg border border-border rounded-md px-2 py-1 text-sm"
          />
        </label>
        <button
          type="button"
          disabled={!canCascade}
          onClick={() => handleChoose("cascade")}
          className="text-sm bg-danger text-white rounded-md px-3 py-1.5 hover:opacity-90 disabled:opacity-50"
        >
          Delete folder and subfolders
        </button>
      </div>

      <div className="flex justify-end pt-2">
        <button
          type="button"
          onClick={handleClose}
          className="text-sm text-muted hover:text-text px-2 py-1.5"
        >
          Cancel
        </button>
      </div>
    </Modal>
  );
}
