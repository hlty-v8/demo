/**
 * Add-to-cart behaviour: duplicate-submit protection, progress and feedback,
 * client-side idempotency keys, and recovery when the stock on the server moved
 * on since the page was loaded.
 */

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { App } from "../App";
import { ApiError, NetworkError } from "../api/client";
import type { AddCartItemResponseDto } from "../api/types";
import { cartWith, createFakeClient, deferred, productWithStock } from "../test/fixtures";

const EMBER_42 = "?color=ember&size=42"; // EMB-42: $129.00, 6 in stock

function responseFor(skuId: string, quantity: number, unitPriceCents: number) {
  const cart = cartWith(skuId, quantity, unitPriceCents);
  return { cart_id: cart.id, item: cart.items[0]!, cart };
}

async function renderReady(search = EMBER_42) {
  const client = createFakeClient();
  const user = userEvent.setup();
  render(<App client={client} initialSearch={search} />);
  await screen.findByRole("heading", { name: /aurora trail runner/i });
  await waitFor(() =>
    expect(screen.getByRole("button", { name: /add to cart/i })).toBeEnabled(),
  );
  return { client, user, button: screen.getByRole("button", { name: /add to cart/i }) };
}

describe("add to cart", () => {
  it("submits once when the button is clicked twice, and announces the result", async () => {
    const gate = deferred<AddCartItemResponseDto>();
    const client = createFakeClient();
    client.setAddHandler(() => gate.promise);
    const user = userEvent.setup();
    render(<App client={client} initialSearch={EMBER_42} />);
    const button = await screen.findByRole("button", { name: /add to cart/i });
    await waitFor(() => expect(button).toBeEnabled());

    await user.click(button);

    // In flight: the control reports progress and cannot be triggered again.
    expect(button).toHaveTextContent(/adding/i);
    expect(button).toBeDisabled();
    expect(button).toHaveAttribute("aria-busy", "true");
    fireEvent.click(button);

    expect(client.addInputs).toHaveLength(1);
    expect(client.addInputs[0]).toMatchObject({ skuId: "EMB-42", quantity: 1 });

    gate.resolve(responseFor("EMB-42", 1, 12_900));

    expect(await screen.findByText(/added 1 .* EMB-42/i)).toBeInTheDocument();
    expect(screen.getByTestId("cart-count")).toHaveTextContent("1");
    expect(screen.getByRole("button", { name: /add to cart/i })).toBeEnabled();
  });

  it("keeps the same idempotency key when retrying the same intent", async () => {
    const { client, user } = await renderReady();
    client.setAddHandler(() => Promise.reject(new NetworkError("offline")));

    await user.click(screen.getByRole("button", { name: /add to cart/i }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/could not reach the server/i);

    await user.click(screen.getByRole("button", { name: /add to cart/i }));
    await waitFor(() => expect(client.addInputs).toHaveLength(2));

    expect(client.addInputs[0]!.idempotencyKey).toBe(client.addInputs[1]!.idempotencyKey);
    expect(client.addInputs[0]!.idempotencyKey).not.toBe("");
  });

  it("uses a fresh key once the intent changes", async () => {
    const { client, user } = await renderReady();
    const button = screen.getByRole("button", { name: /add to cart/i });

    await user.click(button);
    await screen.findByText(/added 1/i);

    await user.click(screen.getByRole("button", { name: /increase quantity/i }));
    await user.click(screen.getByRole("button", { name: /add to cart/i }));
    await waitFor(() => expect(client.addInputs).toHaveLength(2));

    expect(client.addInputs[1]).toMatchObject({ skuId: "EMB-42", quantity: 2 });
    expect(client.addInputs[1]!.idempotencyKey).not.toBe(client.addInputs[0]!.idempotencyKey);
  });

  it("recovers from an insufficient-stock conflict by refreshing the product", async () => {
    // The second product read (the post-conflict refresh) reports less stock,
    // which is exactly the "server moved on since page load" case.
    const client = createFakeClient({
      productSequence: [productWithStock("MOS-42", 5), productWithStock("MOS-42", 1)],
    });
    client.setAddHandler(() =>
      Promise.reject(
        new ApiError(409, "insufficient_stock", "Only 1 left of MOS-42.", {
          sku_id: "MOS-42",
          requested: 5,
          available: 1,
        }),
      ),
    );

    const user = userEvent.setup();
    render(<App client={client} initialSearch="?color=moss&size=42" />);
    await screen.findByRole("heading", { name: /aurora trail runner/i });
    // The deep-linked variant has to resolve before the stepper is usable.
    await waitFor(() => expect(screen.getByTestId("stock")).toHaveTextContent(/5 available/i));
    const increase = screen.getByRole("button", { name: /increase quantity/i });
    await waitFor(() => expect(increase).toBeEnabled());
    for (let step = 0; step < 4; step += 1) {
      await user.click(increase);
    }
    expect(screen.getByLabelText("Quantity")).toHaveValue(5);

    await user.click(screen.getByRole("button", { name: /add to cart/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/only 1 left of mos-42/i);
    await waitFor(() => expect(screen.getByTestId("stock")).toHaveTextContent(/1 available/i));
    // Clamped to what the server says is left, and the page still works.
    expect(screen.getByLabelText("Quantity")).toHaveValue(1);
    expect(client.productRequests).toBe(2);
    expect(screen.getByRole("button", { name: /add to cart/i })).toBeEnabled();
  });

  it("surfaces a structured validation error from the server", async () => {
    const { client, user } = await renderReady();
    client.setAddHandler(() =>
      Promise.reject(
        new ApiError(422, "validation_error", "Request validation failed.", {
          fields: [{ field: "quantity", message: "must be >= 1" }],
        }),
      ),
    );

    await user.click(screen.getByRole("button", { name: /add to cart/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/request validation failed/i);
    expect(screen.getByTestId("cart-count")).toHaveTextContent("0");
  });
});
