/**
 * Domain model and transport mappers.
 *
 * The UI only ever sees these camelCase, well-typed structures. Mapping at the
 * boundary means the snake_case wire format (and any future change to it) stays
 * confined to src/api.
 */

import type { CartDto, CartItemDto, ProductDto, SkuDto } from "../api/types";

export interface OptionValue {
  id: string;
  label: string;
}

export interface OptionDimension {
  id: string;
  name: string;
  values: OptionValue[];
}

export interface Sku {
  id: string;
  options: Record<string, string>;
  priceCents: number;
  availableQty: number;
  inStock: boolean;
  imageUrl: string;
}

export interface Product {
  id: string;
  name: string;
  description: string;
  currency: string;
  options: OptionDimension[];
  skus: Sku[];
}

/** dimensionId -> valueId, with `null` meaning "not chosen yet". */
export type Selection = Record<string, string | null>;

export interface CartItem {
  skuId: string;
  quantity: number;
  unitPriceCents: number;
  lineTotalCents: number;
  availableQty: number;
  imageUrl: string;
  options: Record<string, string>;
}

export interface Cart {
  id: string;
  items: CartItem[];
  totalItems: number;
  subtotalCents: number;
}

export function toSku(dto: SkuDto): Sku {
  return {
    id: dto.id,
    options: dto.options,
    priceCents: dto.price_cents,
    availableQty: dto.available_qty,
    inStock: dto.in_stock,
    imageUrl: dto.image_url,
  };
}

export function toProduct(dto: ProductDto): Product {
  return {
    id: dto.id,
    name: dto.name,
    description: dto.description,
    currency: dto.currency,
    options: dto.options.map((dimension) => ({
      id: dimension.id,
      name: dimension.name,
      values: dimension.values.map((value) => ({ id: value.id, label: value.label })),
    })),
    skus: dto.skus.map(toSku),
  };
}

function toCartItem(dto: CartItemDto): CartItem {
  return {
    skuId: dto.sku_id,
    quantity: dto.quantity,
    unitPriceCents: dto.unit_price_cents,
    lineTotalCents: dto.line_total_cents,
    availableQty: dto.available_qty,
    imageUrl: dto.image_url,
    options: dto.options,
  };
}

export function toCart(dto: CartDto): Cart {
  return {
    id: dto.id,
    items: dto.items.map(toCartItem),
    totalItems: dto.total_items,
    subtotalCents: dto.subtotal_cents,
  };
}

/**
 * Money is integer cents end to end; the only place it becomes a string is here,
 * at the edge of the UI.
 */
export function formatPrice(cents: number, currency: string): string {
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency,
    minimumFractionDigits: 2,
  }).format(cents / 100);
}
