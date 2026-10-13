import { expect, test } from "@playwright/test";

/**
 * Browser end-to-end tests for the PDP.
 *
 * These run against the production build served by `vite preview`, proxied to a
 * real uvicorn process. Nothing is faked: if the proxy, the API contract, the
 * cookie handling or the variant logic break, these fail.
 */

test.describe("variant PDP against the real API", () => {
  test("selects a variant, adds it to the cart and keeps the cart across a reload", async ({
    page,
  }) => {
    await page.goto("/");

    // Incomplete selection: a price range and a disabled call to action.
    await expect(page.getByTestId("price")).toContainText("$129.00");
    await expect(page.getByTestId("price")).toContainText("$135.00");
    await expect(page.getByTestId("stock")).toContainText(/select a colour and size/i);
    await expect(page.getByRole("button", { name: "Add to cart" })).toBeDisabled();

    // Resolve a variant: price, stock and image update together.
    await page.getByRole("button", { name: "Ember" }).click();
    await page.getByRole("button", { name: "EU 42" }).click();
    await expect(page.getByTestId("price")).toHaveText("$129.00");
    await expect(page.getByTestId("stock")).toContainText("6 available");
    await expect(page.getByRole("img", { name: /ember, eu 42/i })).toHaveAttribute(
      "src",
      "/api/skus/EMB-42/image",
    );

    // Add to cart: real POST, real cookie, real cart in SQLite.
    await page.getByRole("button", { name: "Add to cart" }).click();
    await expect(page.getByRole("status")).toContainText(/added 1/i);
    await expect(page.getByTestId("cart-count")).toHaveText("1");

    // The cart is server state behind a cookie, so it survives a page reload.
    await page.reload();
    await expect(page.getByTestId("cart-count")).toHaveText("1");
    await expect(page.getByRole("heading", { name: "Your cart" })).toBeVisible();
  });

  test("disables impossible combinations and blocks a sold-out variant", async ({ page }) => {
    await page.goto("/");

    // EU 38 exists for Ember only: the other colours must be unselectable.
    await page.getByRole("button", { name: "EU 38" }).click();
    await expect(page.getByRole("button", { name: /moss/i })).toBeDisabled();
    await expect(page.getByRole("button", { name: /sand/i })).toBeDisabled();
    await expect(page.getByRole("button", { name: "Ember" })).toBeEnabled();

    // Ember / EU 40 is seeded with no stock: selectable, but not addable.
    await page.getByRole("button", { name: "Ember" }).click();
    await page.getByRole("button", { name: "EU 40" }).click();
    await expect(page.getByTestId("stock")).toHaveText(/sold out/i);
    await expect(page.getByRole("button", { name: "Add to cart" })).toBeDisabled();
  });

  test("follows a deep link to a variant", async ({ page }) => {
    await page.goto("/?color=sand&size=44");

    await expect(page.getByTestId("price")).toHaveText("$135.00");
    await expect(page.getByTestId("stock")).toContainText("3 available");
    await expect(page.getByRole("button", { name: "Add to cart" })).toBeEnabled();
  });

  test("stays usable at 375px and at 1280px", async ({ page }) => {
    for (const viewport of [
      { width: 375, height: 800 },
      { width: 1280, height: 900 },
    ]) {
      await page.setViewportSize(viewport);
      await page.goto("/");

      await expect(page.getByRole("heading", { name: "Aurora Trail Runner" })).toBeVisible();
      await expect(page.getByRole("button", { name: "Ember" })).toBeVisible();

      // No horizontal overflow: the layout must fit the viewport it is given.
      const overflow = await page.evaluate(
        () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
      );
      expect(overflow, `horizontal overflow at ${viewport.width}px`).toBeLessThanOrEqual(1);

      // Controls stay keyboard reachable at both widths.
      await page.getByRole("button", { name: "Ember" }).focus();
      await expect(page.getByRole("button", { name: "Ember" })).toBeFocused();
    }
  });
});
