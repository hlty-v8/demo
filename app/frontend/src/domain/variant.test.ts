/**
 * Domain tests. This is where the variant rules are pinned down, independent of
 * React: which values are pickable, which SKU a selection resolves to, how the
 * quantity is clamped, and what a deep link may set.
 */

import { describe, expect, it } from "vitest";

import { formatPrice, toProduct } from "./model";
import {
  availabilityOf,
  clampQuantity,
  emptySelection,
  isSelectionComplete,
  matchesSelection,
  optionState,
  priceRangeCents,
  selectionFromSearch,
  selectionToSearch,
  skuForSelection,
} from "./variant";
import { productDto } from "../test/fixtures";

const product = toProduct(productDto);
const colorDimension = product.options[0]!;
const sizeDimension = product.options[1]!;

describe("toProduct", () => {
  it("maps the wire format to the domain model", () => {
    expect(product.id).toBe("aurora-trail-runner");
    expect(product.currency).toBe("USD");
    expect(product.skus).toHaveLength(7);
    const emb42 = product.skus.find((sku) => sku.id === "EMB-42")!;
    expect(emb42.priceCents).toBe(12_900);
    expect(emb42.availableQty).toBe(6);
    expect(emb42.imageUrl).toBe("/api/skus/EMB-42/image");
  });
});

describe("availability", () => {
  it("offers every value that appears in some SKU when nothing is selected", () => {
    const selection = emptySelection(product);
    expect(optionState(product, colorDimension, selection).map((s) => s.availability)).toEqual([
      "available",
      "available",
      "available",
    ]);
    expect(optionState(product, sizeDimension, selection).every((s) => s.selectable)).toBe(true);
  });

  it("disables the combinations that have no SKU at all", () => {
    // With size 38 chosen, only Ember exists.
    const selection = { color: null, size: "38" };
    expect(availabilityOf(product, "color", "ember", selection)).toBe("available");
    expect(availabilityOf(product, "color", "moss", selection)).toBe("impossible");
    expect(availabilityOf(product, "color", "sand", selection)).toBe("impossible");

    const states = optionState(product, colorDimension, selection);
    expect(states.find((s) => s.valueId === "moss")?.selectable).toBe(false);
    expect(states.find((s) => s.valueId === "ember")?.selectable).toBe(true);
  });

  it("keeps an out-of-stock variant selectable but marks it", () => {
    // Size 40 in Ember is seeded with zero stock.
    const selection = { color: null, size: "40" };
    expect(availabilityOf(product, "color", "ember", selection)).toBe("out-of-stock");
    expect(availabilityOf(product, "color", "moss", selection)).toBe("available");
    const states = optionState(product, colorDimension, selection);
    expect(states.find((s) => s.valueId === "ember")).toEqual({
      valueId: "ember",
      availability: "out-of-stock",
      selectable: true,
    });
  });
});

describe("sku resolution", () => {
  it("returns null until the selection is complete", () => {
    expect(isSelectionComplete(product, { color: "ember", size: null })).toBe(false);
    expect(skuForSelection(product, { color: "ember", size: null })).toBeNull();
    expect(skuForSelection(product, { color: null, size: null })).toBeNull();
  });

  it("resolves a complete, valid selection to exactly one SKU", () => {
    const sku = skuForSelection(product, { color: "sand", size: "44" });
    expect(sku?.id).toBe("SAND-44");
    expect(sku?.priceCents).toBe(13_500);
    expect(sku?.availableQty).toBe(3);
  });

  it("returns null for a complete but invalid combination", () => {
    expect(isSelectionComplete(product, { color: "sand", size: "38" })).toBe(true);
    expect(skuForSelection(product, { color: "sand", size: "38" })).toBeNull();
  });

  it("matches on the chosen dimensions only", () => {
    const sku = product.skus.find((candidate) => candidate.id === "MOS-42")!;
    expect(matchesSelection(sku, { color: "moss", size: null })).toBe(true);
    expect(matchesSelection(sku, { color: "moss", size: null }, "color")).toBe(true);
    expect(matchesSelection(sku, { color: "ember", size: null })).toBe(false);
  });
});

describe("price range", () => {
  it("covers every reachable SKU while the selection is incomplete", () => {
    expect(priceRangeCents(product, emptySelection(product))).toEqual({
      min: 12_900,
      max: 13_500,
    });
    expect(priceRangeCents(product, { color: null, size: "38" })).toEqual({
      min: 12_900,
      max: 12_900,
    });
  });

  it("is empty for an impossible combination", () => {
    expect(priceRangeCents(product, { color: "sand", size: "38" })).toBeNull();
  });
});

describe("quantity clamping", () => {
  it("keeps the quantity inside [1, stock]", () => {
    expect(clampQuantity(0, 5)).toBe(1);
    expect(clampQuantity(-4, 5)).toBe(1);
    expect(clampQuantity(3, 5)).toBe(3);
    expect(clampQuantity(9, 5)).toBe(5);
    expect(clampQuantity(2.7, 5)).toBe(2);
    expect(clampQuantity(Number.NaN, 5)).toBe(0);
  });

  it("returns 0 when nothing is in stock", () => {
    expect(clampQuantity(3, 0)).toBe(0);
  });
});

describe("deep links", () => {
  it("reads a known selection out of the query string", () => {
    expect(selectionFromSearch(product, "?color=moss&size=42")).toEqual({
      color: "moss",
      size: "42",
    });
  });

  it("ignores unknown or missing values instead of trusting them", () => {
    expect(selectionFromSearch(product, "?color=neon&size=99")).toEqual({
      color: null,
      size: null,
    });
    expect(selectionFromSearch(product, "")).toEqual({ color: null, size: null });
  });

  it("round-trips through the query string", () => {
    expect(selectionToSearch(product, { color: "sand", size: "44" })).toBe("color=sand&size=44");
    expect(selectionToSearch(product, { color: null, size: "44" })).toBe("size=44");
  });
});

describe("formatPrice", () => {
  it("renders integer cents as currency", () => {
    expect(formatPrice(12_900, "USD")).toBe("$129.00");
    expect(formatPrice(0, "USD")).toBe("$0.00");
  });
});
