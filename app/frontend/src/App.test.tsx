/**
 * Page-level tests: variant resolution, the states the PDP must render, and
 * recovery from a failed load. No network: the API client is injected.
 */

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { App } from "./App";
import { ApiError } from "./api/client";
import { createFakeClient, type FakeClient } from "./test/fixtures";

async function renderProduct(search = "") {
  const client = createFakeClient();
  const user = userEvent.setup();
  render(<App client={client} initialSearch={search} />);
  await screen.findByRole("heading", { name: /aurora trail runner/i });
  return { client, user };
}

function option(name: RegExp) {
  return screen.getByRole("button", { name });
}

describe("App", () => {
  it("shows a price range, asks for a selection and blocks adding until complete", async () => {
    await renderProduct();

    expect(screen.getByTestId("price")).toHaveTextContent(/129\.00/);
    expect(screen.getByTestId("price")).toHaveTextContent(/135\.00/);
    expect(screen.getByTestId("stock")).toHaveTextContent(/select a colour and size/i);
    expect(screen.getByRole("button", { name: /add to cart/i })).toBeDisabled();
  });

  it("resolves the chosen variant and updates price, image and stock together", async () => {
    const { user } = await renderProduct();

    await user.click(option(/ember/i));
    await user.click(option(/eu 42/i));

    expect(screen.getByTestId("price")).toHaveTextContent("$129.00");
    expect(screen.getByTestId("stock")).toHaveTextContent(/6 available/i);
    const image = screen.getByRole("img", { name: /ember, eu 42/i });
    expect(image).toHaveAttribute("src", "/api/skus/EMB-42/image");
    expect(screen.getByRole("button", { name: /add to cart/i })).toBeEnabled();
  });

  it("never shows a stale variant when the selection changes", async () => {
    const { user } = await renderProduct("?color=ember&size=42");
    expect(screen.getByTestId("price")).toHaveTextContent("$129.00");

    await user.click(option(/moss/i));

    expect(screen.getByTestId("price")).toHaveTextContent("$132.00");
    expect(screen.getByTestId("stock")).toHaveTextContent(/2 available/i);
    expect(screen.getByRole("img", { name: /moss, eu 42/i })).toHaveAttribute(
      "src",
      "/api/skus/MOS-42/image",
    );
  });

  it("disables impossible combinations instead of offering them", async () => {
    const { user } = await renderProduct();

    await user.click(option(/eu 38/i));

    expect(option(/ember/i)).toBeEnabled();
    expect(option(/moss/i)).toBeDisabled();
    expect(option(/sand/i)).toBeDisabled();
  });

  it("reports a sold-out variant and blocks adding it", async () => {
    const { user } = await renderProduct();

    await user.click(option(/ember/i));
    await user.click(option(/eu 40/i));

    expect(screen.getByTestId("stock")).toHaveTextContent(/sold out/i);
    expect(option(/ember/i)).toHaveTextContent(/sold out/i);
    expect(screen.getByRole("button", { name: /add to cart/i })).toBeDisabled();
  });

  it("keeps the quantity valid when the variant changes", async () => {
    const { user } = await renderProduct("?color=ember&size=42");
    // Wait for the deep-linked variant to resolve: until it does, the quantity
    // controls are disabled and clicks would be swallowed.
    const increase = screen.getByRole("button", { name: /increase quantity/i });
    await waitFor(() => expect(increase).toBeEnabled());

    for (let step = 0; step < 5; step += 1) {
      await user.click(increase);
    }
    // The stepper buttons carry "Increase/Decrease quantity" labels, so match
    // the number input's own label exactly.
    expect(screen.getByLabelText("Quantity")).toHaveValue(6);

    // MOS-42 only has 2 left, so the quantity must come down with the variant.
    await user.click(option(/moss/i));
    expect(screen.getByLabelText("Quantity")).toHaveValue(2);
  });

  it("handles a deep link to an impossible combination", async () => {
    await renderProduct("?color=sand&size=38");

    expect(screen.getByTestId("stock")).toHaveTextContent(/combination is unavailable/i);
    expect(screen.getByTestId("price")).toHaveTextContent("—");
    expect(screen.getByRole("button", { name: /add to cart/i })).toBeDisabled();
  });

  it("lets the shopper retry after a failed load, without a page reload", async () => {
    const client: FakeClient = createFakeClient();
    client.failNextProductRequest(
      new ApiError(500, "internal_error", "Unexpected server error. Please retry."),
    );
    const user = userEvent.setup();
    render(<App client={client} initialSearch="" />);

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(/unexpected server error/i);

    await user.click(screen.getByRole("button", { name: /try again/i }));

    expect(await screen.findByRole("heading", { name: /aurora trail runner/i })).toBeInTheDocument();
    expect(client.productRequests).toBe(2);
  });
});
