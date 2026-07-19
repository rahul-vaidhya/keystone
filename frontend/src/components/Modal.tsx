import { useEffect, useRef, type ReactNode, type RefObject } from "react";

const FOCUSABLE_SELECTOR =
  'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

/**
 * Headless, reusable dialog shell: owns the WAI-ARIA APG modal mechanics
 * (role="dialog"/aria-modal, focus trap, Escape-to-close, backdrop-click-to-close,
 * initial focus, focus restoration) and nothing else. Callers supply the title,
 * body, and action buttons as `children` — this component renders no message text
 * and no buttons of its own.
 */
export function Modal({
  open,
  onClose,
  titleId,
  children,
  initialFocusRef,
}: {
  open: boolean;
  onClose: () => void;
  titleId: string;
  children: ReactNode;
  /** Focus this element on open instead of the first focusable element in the
   * panel — used by confirm dialogs to focus Cancel (the least-destructive
   * action) rather than whatever happens to be first in the DOM. */
  initialFocusRef?: RefObject<HTMLElement | null>;
}) {
  const panelRef = useRef<HTMLDivElement>(null);
  const previouslyFocusedRef = useRef<Element | null>(null);

  useEffect(() => {
    if (!open) return;
    previouslyFocusedRef.current = document.activeElement;

    const explicit = initialFocusRef?.current;
    const first = panelRef.current?.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR)[0];
    (explicit ?? first)?.focus();

    return () => {
      const toRestore = previouslyFocusedRef.current;
      if (toRestore instanceof HTMLElement) {
        toRestore.focus();
      }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  if (!open) return null;

  function handleKeyDown(e: React.KeyboardEvent<HTMLDivElement>) {
    if (e.key === "Escape") {
      onClose();
      return;
    }
    if (e.key !== "Tab") return;

    const focusable = panelRef.current?.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR);
    if (!focusable || focusable.length === 0) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];

    if (e.shiftKey && document.activeElement === first) {
      e.preventDefault();
      last.focus();
    } else if (!e.shiftKey && document.activeElement === last) {
      e.preventDefault();
      first.focus();
    }
  }

  return (
    <div
      className="fixed inset-0 bg-black/40 z-50 flex items-center justify-center p-4"
      onClick={onClose}
    >
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        onClick={(e) => e.stopPropagation()}
        onKeyDown={handleKeyDown}
        className="w-full max-w-md bg-surface border border-border rounded-lg p-6 space-y-4"
      >
        {children}
      </div>
    </div>
  );
}
