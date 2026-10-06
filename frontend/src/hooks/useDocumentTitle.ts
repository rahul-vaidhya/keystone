import { useEffect } from "react";

const APP_NAME = "Veratas";

/** Sets `document.title` to "<title> · Veratas" (or just "Veratas" when title is
 *  empty/undefined, e.g. while a notebook name is still loading). Restores the bare
 *  app name on unmount so a page without the hook never inherits a stale title. */
export function useDocumentTitle(title?: string | null) {
  useEffect(() => {
    document.title = title ? `${title} · ${APP_NAME}` : APP_NAME;
  }, [title]);
  useEffect(
    () => () => {
      document.title = APP_NAME;
    },
    [],
  );
}
