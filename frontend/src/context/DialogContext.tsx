import { createContext, useCallback, useMemo, useRef, useState, type ReactNode } from "react";
import { Modal } from "../components/Modal";

type AlertOptions = { title?: string };
type ConfirmOptions = { title?: string; confirmLabel?: string; danger?: boolean };

type DialogRequest =
  | { kind: "alert"; message: string; title?: string; resolve: () => void }
  | {
      kind: "confirm";
      message: string;
      title?: string;
      confirmLabel?: string;
      danger?: boolean;
      resolve: (result: boolean) => void;
    };

type DialogState = {
  /** Replaces `window.alert` — shows a single "OK" button, resolves on dismissal
   * (OK click, Escape, or backdrop click all resolve it; there's nothing to
   * cancel for a pure info alert). */
  alert: (message: string, opts?: AlertOptions) => Promise<void>;
  /** Replaces `window.confirm` — resolves `true` on confirm click, `false` on
   * Cancel/Escape/backdrop click. Mirrors `window.confirm`'s ergonomics so a
   * call site can `await dialog.confirm(...)` and branch on the boolean. */
  confirm: (message: string, opts?: ConfirmOptions) => Promise<boolean>;
};

export const DialogContext = createContext<DialogState | null>(null);

export function DialogProvider({ children }: { children: ReactNode }) {
  const [request, setRequest] = useState<DialogRequest | null>(null);
  const cancelButtonRef = useRef<HTMLButtonElement>(null);
  const [titleId] = useState(
    () => `dialog-title-${Math.random().toString(36).slice(2)}`,
  );

  const alert = useCallback((message: string, opts?: AlertOptions) => {
    return new Promise<void>((resolve) => {
      setRequest({ kind: "alert", message, title: opts?.title, resolve });
    });
  }, []);

  const confirm = useCallback((message: string, opts?: ConfirmOptions) => {
    return new Promise<boolean>((resolve) => {
      setRequest({
        kind: "confirm",
        message,
        title: opts?.title,
        confirmLabel: opts?.confirmLabel,
        danger: opts?.danger,
        resolve,
      });
    });
  }, []);

  function settle(result: boolean) {
    if (!request) return;
    if (request.kind === "alert") {
      request.resolve();
    } else {
      request.resolve(result);
    }
    setRequest(null);
  }

  const value = useMemo(() => ({ alert, confirm }), [alert, confirm]);

  return (
    <DialogContext.Provider value={value}>
      {children}
      <Modal
        open={request !== null}
        onClose={() => settle(false)}
        titleId={titleId}
        initialFocusRef={request?.kind === "confirm" ? cancelButtonRef : undefined}
      >
        {request && (
          <>
            <h2 id={titleId} className="text-base font-semibold">
              {request.title ?? (request.kind === "confirm" ? "Confirm" : "Notice")}
            </h2>
            <p className="text-sm text-muted whitespace-pre-line">{request.message}</p>
            <div className="flex justify-end gap-2 pt-2">
              {request.kind === "alert" ? (
                <button
                  type="button"
                  onClick={() => settle(false)}
                  className="text-sm bg-accent text-white rounded-md px-3 py-1.5 hover:opacity-90"
                >
                  OK
                </button>
              ) : (
                <>
                  <button
                    type="button"
                    ref={cancelButtonRef}
                    onClick={() => settle(false)}
                    className="text-sm border border-border rounded-md px-3 py-1.5 text-muted hover:text-text hover:bg-surface"
                  >
                    Cancel
                  </button>
                  <button
                    type="button"
                    onClick={() => settle(true)}
                    className={`text-sm rounded-md px-3 py-1.5 text-white hover:opacity-90 ${
                      request.danger ? "bg-danger" : "bg-accent"
                    }`}
                  >
                    {request.confirmLabel ?? "Confirm"}
                  </button>
                </>
              )}
            </div>
          </>
        )}
      </Modal>
    </DialogContext.Provider>
  );
}
