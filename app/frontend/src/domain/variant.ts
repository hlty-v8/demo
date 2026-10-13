/**
 * Pure variant logic. No React, no DOM: everything here is unit-testable and is
 * the single source of truth for what the page shows.
 *
 * The core idea is that a selection is *never* used to look up a SKU by hand in
 * a component. Components render what these functions return, so a selection
 * change cannot leave a stale price, image or stock behind.
 */

import type { OptionDimension, Product, Selection, Sku } from "./model";

export type ValueAvailability = "available" | "out-of-stock" | "impossible";

export interface ValueState {
  valueId: string;
  availability: ValueAvailability;
  /** True when clicking the value is allowed. */
  selectable: boolean;
}

/** Every dimension value, with whether it can be picked right now. */
export function optionState(
  product: Product,
  dimension: OptionDimension,
  selection: Selection,
): ValueState[] {
  return dimension.values.map((value) => {
    const availability = availabilityOf(product, dimension.id, value.id, selection);
    return {
      valueId: value.id,
      availability,
      // "out-of-stock" stays selectable on purpose: the shopper can still see
      // the variant's price and image (and the Sold out message), which is more
      // useful than a dead button. "impossible" has no SKU at all, so it is
      // genuinely unselectable.
      selectable: availability !== "impossible",
    };
  });
}

export function availabilityOf(
  product: Product,
  dimensionId: string,
  valueId: string,
  selection: Selection,
): ValueAvailability {
  const candidates = product.skus.filter(
    (sku) =>
      sku.options[dimensionId] === valueId &&
      matchesSelection(sku, selection, dimensionId),
  );
  if (candidates.length === 0) return "impossible";
  return candidates.some((sku) => sku.availableQty > 0) ? "available" : "out-of-stock";
}

/** Does the SKU satisfy every chosen value, ignoring one dimension? */
export function matchesSelection(
  sku: Sku,
  selection: Selection,
  ignoreDimensionId?: string,
): boolean {
  return Object.entries(selection).every(([dimensionId, valueId]) => {
    if (valueId === null || dimensionId === ignoreDimensionId) return true;
    return sku.options[dimensionId] === valueId;
  });
}

export function isSelectionComplete(product: Product, selection: Selection): boolean {
  return product.options.every((dimension) => {
    const value = selection[dimension.id] ?? null;
    return value !== null && dimension.values.some((candidate) => candidate.id === value);
  });
}

/** The SKU for a complete selection, or null when incomplete/invalid. */
export function skuForSelection(product: Product, selection: Selection): Sku | null {
  if (!isSelectionComplete(product, selection)) return null;
  return product.skus.find((sku) => matchesSelection(sku, selection)) ?? null;
}

/**
 * Price shown for a partial selection: the range still reachable, so the page
 * never displays the price of a variant the shopper has not chosen.
 */
export function priceRangeCents(
  product: Product,
  selection: Selection,
): { min: number; max: number } | null {
  const candidates = product.skus.filter((sku) => matchesSelection(sku, selection));
  if (candidates.length === 0) return null;
  const prices = candidates.map((sku) => sku.priceCents);
  return { min: Math.min(...prices), max: Math.max(...prices) };
}

/** Quantity is always an integer in [1, availableQty]; 0 when nothing is left. */
export function clampQuantity(quantity: number, availableQty: number): number {
  if (!Number.isFinite(quantity) || availableQty <= 0) return 0;
  const whole = Math.trunc(quantity);
  return Math.min(Math.max(1, whole), availableQty);
}

export function emptySelection(product: Product): Selection {
  const selection: Selection = {};
  for (const dimension of product.options) selection[dimension.id] = null;
  return selection;
}

/**
 * Initial selection from the query string, so a variant can be deep-linked
 * (`/?color=sand&size=38`). Unknown values are ignored rather than trusted: the
 * server response stays the authority on what exists.
 */
export function selectionFromSearch(product: Product, search: string): Selection {
  const selection = emptySelection(product);
  const params = new URLSearchParams(search);
  for (const dimension of product.options) {
    const requested = params.get(dimension.id);
    if (requested && dimension.values.some((value) => value.id === requested)) {
      selection[dimension.id] = requested;
    }
  }
  return selection;
}

export function selectionToSearch(product: Product, selection: Selection): string {
  const params = new URLSearchParams();
  for (const dimension of product.options) {
    const value = selection[dimension.id] ?? null;
    if (value !== null) params.set(dimension.id, value);
  }
  return params.toString();
}
