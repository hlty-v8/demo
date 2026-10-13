import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError, NetworkError, type PdpApi } from "../api/client";
import { toProduct, type Product } from "../domain/model";

export type ProductStatus = "loading" | "ready" | "error";

export interface ProductController {
  status: ProductStatus;
  product: Product | null;
  error: string | null;
  /** Retry after a failed load: shows the loading state again. */
  reload: () => void;
  /**
   * Refresh without flashing a loading state - used to pick up newer stock
   * after a conflict, so the page recovers without a full reload.
   */
  refresh: () => Promise<Product | null>;
}

function messageFor(error: unknown): string {
  if (ApiError.isApiError(error)) return error.message;
  if (NetworkError.isNetworkError(error)) return error.message;
  return "Something went wrong loading this product.";
}

/**
 * Loads the product detail.
 *
 * Staleness: every request carries a monotonically increasing id, and a
 * response is only applied when it is still the newest one. Combined with the
 * AbortController in the effect cleanup, a slow first request can never
 * overwrite a newer one.
 */
export function useProduct(client: PdpApi, productId: string): ProductController {
  const [status, setStatus] = useState<ProductStatus>("loading");
  const [product, setProduct] = useState<Product | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);
  const latestRequestId = useRef(0);

  useEffect(() => {
    const controller = new AbortController();
    const requestId = ++latestRequestId.current;

    setStatus("loading");
    setError(null);

    client
      .getProduct(productId, controller.signal)
      .then((dto) => {
        if (requestId !== latestRequestId.current) return; // a newer load won
        setProduct(toProduct(dto));
        setStatus("ready");
      })
      .catch((cause: unknown) => {
        if (controller.signal.aborted || requestId !== latestRequestId.current) return;
        setError(messageFor(cause));
        setStatus("error");
      });

    return () => controller.abort();
  }, [client, productId, attempt]);

  const reload = useCallback(() => setAttempt((value) => value + 1), []);

  const refresh = useCallback(async (): Promise<Product | null> => {
    const requestId = ++latestRequestId.current;
    try {
      const dto = await client.getProduct(productId);
      if (requestId !== latestRequestId.current) return null;
      const fresh = toProduct(dto);
      setProduct(fresh);
      setStatus("ready");
      setError(null);
      return fresh;
    } catch {
      // A failed background refresh must not destroy a usable page.
      return null;
    }
  }, [client, productId]);

  return { status, product, error, reload, refresh };
}
