import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

afterEach(() => {
  cleanup();
  // The page syncs its selection into the URL; reset it so tests stay isolated.
  window.history.replaceState(null, "", "/");
});
