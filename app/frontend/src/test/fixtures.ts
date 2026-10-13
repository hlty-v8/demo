/**
 * Test fixtures and a scriptable fake API client.
 *
 * The product mirrors the backend seed data exactly - including the impossible
 * sand/38 combination and the out-of-stock EMB-40 - so the frontend tests
 * exercise the same edge cases the API ships with.
 */

import type { AddCartItemInput, PdpApi } from "../api/client";
import type { AddCartItemResponseDto, CartDto, ProductDto, SkuDto } from "../api/types";

function sku(
  id: string,
  color: string,
  size: string,
  priceCents: number,
  availableQty: number,
): SkuDto {
  return {
    id,
    options: { color, size },
    price_cents: priceCents,
    available_qty: availableQty,
    in_stock: availableQty > 0,
    image_url: `/api/skus/${id}/image`,
  };
}

export const productDto: ProductDto = {
  id: "aurora-trail-runner",
  name: "Aurora Trail Runner",
  description: "A lightweight trail shoe built for long, loose descents.",
  currency: "USD",
  options: [
    {
      id: "color",
      name: "Colour",
      values: [
        { id: "ember", label: "Ember" },
        { id: "moss", label: "Moss" },
        { id: "sand", label: "Sand" },
      ],
    },
    {
      id: "size",
      name: "Size",
      values: [
        { id: "38", label: "EU 38" },
        { id: "40", label: "EU 40" },
        { id: "42", label: "EU 42" },
        { id: "44", label: "EU 44" },
      ],
    },
  ],
  skus: [
    sku("EMB-38", "ember", "38", 12_900, 4),
    sku("EMB-40", "ember", "40", 12_900, 0),
    sku("EMB-42", "ember", "42", 12_900, 6),
    sku("MOS-40", "moss", "40", 12_900, 7),
    sku("MOS-42", "moss", "42", 13_200, 2),
    sku("SAND-42", "sand", "42", 13_500, 5),
    sku("SAND-44", "sand", "44", 13_500, 3),
  ],
};

export const emptyCartDto: CartDto = {
  id: "cart-1",
  items: [],
  total_items: 0,
  subtotal_cents: 0,
};

/** A copy of the product with one SKU's stock changed, for stock-race tests. */
export function productWithStock(skuId: string, availableQty: number): ProductDto {
  return {
    ...productDto,
    skus: productDto.skus.map((entry) =>
      entry.id === skuId
        ? { ...entry, available_qty: availableQty, in_stock: availableQty > 0 }
        : { ...entry },
    ),
  };
}

export function cartWith(skuId: string, quantity: number, unitPriceCents: number): CartDto {
  return {
    id: "cart-1",
    items: [
      {
        sku_id: skuId,
        quantity,
        unit_price_cents: unitPriceCents,
        line_total_cents: unitPriceCents * quantity,
        available_qty: 0,
        image_url: `/api/skus/${skuId}/image`,
        options: {},
      },
    ],
    total_items: quantity,
    subtotal_cents: unitPriceCents * quantity,
  };
}

export interface FakeClient extends PdpApi {
  addInputs: AddCartItemInput[];
  productRequests: number;
  setProduct(next: ProductDto): void;
  failNextProductRequest(error: unknown): void;
  setAddHandler(handler: (input: AddCartItemInput) => Promise<AddCartItemResponseDto>): void;
}

export interface FakeClientOptions {
  product?: ProductDto;
  /**
   * Responses for successive getProduct calls, so a test can express "stock
   * moved on between the page load and the next read". The last entry repeats.
   */
  productSequence?: ProductDto[];
  cart?: CartDto;
  addHandler?: (input: AddCartItemInput) => Promise<AddCartItemResponseDto>;
}

export function createFakeClient(options: FakeClientOptions = {}): FakeClient {
  let product = options.product ?? productDto;
  let cart = options.cart ?? emptyCartDto;
  let nextProductError: unknown = null;
  let productReads = 0;
  let addHandler =
    options.addHandler ??
    ((input: AddCartItemInput): Promise<AddCartItemResponseDto> => {
      cart = cartWith(input.skuId, input.quantity, 12_900);
      return Promise.resolve({ cart_id: cart.id, item: cart.items[0]!, cart });
    });

  const client: FakeClient = {
    addInputs: [],
    productRequests: 0,

    getProduct() {
      client.productRequests += 1;
      if (nextProductError) {
        const error = nextProductError;
        nextProductError = null;
        return Promise.reject(error);
      }
      const sequence = options.productSequence;
      if (sequence && sequence.length > 0) {
        const index = Math.min(productReads, sequence.length - 1);
        productReads += 1;
        product = sequence[index] ?? product;
      }
      return Promise.resolve(product);
    },

    getCart() {
      return Promise.resolve(cart);
    },

    addCartItem(input) {
      client.addInputs.push(input);
      return addHandler(input);
    },

    setProduct(next: ProductDto) {
      product = next;
    },

    failNextProductRequest(error: unknown) {
      nextProductError = error;
    },

    setAddHandler(handler) {
      addHandler = handler;
    },
  };

  return client;
}

export interface Deferred<T> {
  promise: Promise<T>;
  resolve: (value: T) => void;
  reject: (reason: unknown) => void;
}

export function deferred<T>(): Deferred<T> {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}
