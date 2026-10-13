/**
 * Idempotency keys are generated on the client and reused for as long as the
 * *intent* (same SKU, same quantity) is unchanged, so a retry after a timeout
 * cannot add the item twice.
 */

export function newIdempotencyKey(): string {
  const cryptoObject = globalThis.crypto;
  if (cryptoObject && typeof cryptoObject.randomUUID === "function") {
    return cryptoObject.randomUUID();
  }
  // Fallback for environments without crypto.randomUUID (older jsdom).
  return `idem-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}

export interface Attempt {
  skuId: string;
  quantity: number;
  key: string;
}

/**
 * Returns the key for this intent: the existing one when the intent is
 * unchanged, otherwise a fresh key.
 */
export function keyForIntent(previous: Attempt | null, skuId: string, quantity: number): Attempt {
  if (previous && previous.skuId === skuId && previous.quantity === quantity) {
    return previous;
  }
  return { skuId, quantity, key: newIdempotencyKey() };
}
