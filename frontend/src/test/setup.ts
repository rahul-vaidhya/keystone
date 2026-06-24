import { afterEach } from "vitest";
import { cleanup } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";

// RTL's auto-cleanup only self-registers when it detects global test-framework hooks
// (vitest's `globals: true`); this repo deliberately avoids globals (explicit imports
// per test file instead), so cleanup is wired here once for the whole suite.
afterEach(() => {
  cleanup();
});
