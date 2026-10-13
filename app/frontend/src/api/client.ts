/**
 * HTTP client.
 *
 * Two failure kinds are distinguished, because the UI reacts differently:
 *
 *  - `ApiError`     - the server answered with the documented error envelope
 *                     (validation, insufficient stock, ...). `code` is stable.
 *  - `NetworkError` - the request never produced a usable response (offline,
 *                     aborted, malformed body). Always retryable.
 *
 * The client never invents prices or stock: it returns what the server said.
 */

import type {
  AddCartItemResponseDto,
  ApiErrorBodyDto,
  CartDto,
  ProductDto,
} from "./types";

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly details: Record<string, unknown>;
  readonly requestId: string;

  constructor(
    status: number,
    code: string,
    message: string,
    details: Record<string, unknown> = {},
    requestId = "",
  ) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.details = details;
    this.requestId = requestId;
  }

  static isApiError(error: unknown): error is ApiError {
    return error instanceof ApiError;
  }
}

export class NetworkError extends Error {
  readonly code = "network_error";

  constructor(message = "The server could not be reached.", options?: { cause?: unknown }) {
    super(message, options);
    this.name = "NetworkError";
  }

  static isNetworkError(error: unknown): error is NetworkError {
    return error instanceof NetworkError;
  }
}

function isApiErrorBody(value: unknown): value is ApiErrorBodyDto {
  if (typeof value !== "object" || value === null) return false;
  const candidate = (value as { error?: unknown }).error;
  return typeof candidate === "object" && candidate !== null;
}

async function readBody(response: Response): Promise<unknown> {
  const text = await response.text();
  if (!text) return null;
  try {
    return JSON.parse(text) as unknown;
  } catch {
    return null;
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  let response: Response;
  try {
    response = await fetch(path, {
      credentials: "same-origin",
      ...init,
      headers: { Accept: "application/json", ...(init.headers ?? {}) },
    });
  } catch (cause) {
    // fetch rejects only for transport-level failures (including aborts).
    throw new NetworkError("The server could not be reached.", { cause });
  }

  const body = await readBody(response);

  if (!response.ok) {
    if (isApiErrorBody(body)) {
      const { code, message, details, request_id } = body.error;
      throw new ApiError(
        response.status,
        code,
        message,
        details ?? {},
        request_id ?? response.headers.get("X-Request-Id") ?? "",
      );
    }
    throw new NetworkError(`Unexpected ${response.status} response from the server.`);
  }

  if (body === null) {
    throw new NetworkError("The server returned an empty response.");
  }
  return body as T;
}

export interface AddCartItemInput {
  skuId: string;
  quantity: number;
  /** Stable per intent, so a retry cannot add the item twice. */
  idempotencyKey: string;
}

export interface PdpApi {
  getProduct(productId: string, signal?: AbortSignal): Promise<ProductDto>;
  getCart(signal?: AbortSignal): Promise<CartDto>;
  addCartItem(input: AddCartItemInput, signal?: AbortSignal): Promise<AddCartItemResponseDto>;
}

export const api: PdpApi = {
  getProduct(productId, signal) {
    return request<ProductDto>(`/api/products/${encodeURIComponent(productId)}`, {
      signal: signal ?? null,
    });
  },

  getCart(signal) {
    return request<CartDto>("/api/cart", { signal: signal ?? null });
  },

  addCartItem({ skuId, quantity, idempotencyKey }, signal) {
    return request<AddCartItemResponseDto>("/api/cart/items", {
      method: "POST",
      signal: signal ?? null,
      headers: {
        "Content-Type": "application/json",
        "Idempotency-Key": idempotencyKey,
      },
      body: JSON.stringify({ sku_id: skuId, quantity }),
    });
  },
};
