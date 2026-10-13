import { useCallback, useEffect, useState } from "react";

import type { PdpApi } from "../api/client";
import { toCart, type Cart } from "../domain/model";

export interface CartController {
  cart: Cart | null;
  totalItems: number;
  setCart: (cart: Cart) => void;
  refresh: () => Promise<void>;
}

/**
 * Cart state. The server is the authority: the cart returned by a successful
 * add (or by GET /api/cart) replaces the local copy, so totals and stock can
 * never drift away from what the backend holds.
 */
export function useCart(client: PdpApi): CartController {
  const [cart, setCartState] = useState<Cart | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    client
      .getCart(controller.signal)
      .then((dto) => setCartState(toCart(dto)))
      .catch(() => {
        // A missing cart is not fatal: the shopper can still browse and add.
      });
    return () => controller.abort();
  }, [client]);

  const refresh = useCallback(async () => {
    try {
      const dto = await client.getCart();
      setCartState(toCart(dto));
    } catch {
      // Keep the last known cart rather than blanking the page.
    }
  }, [client]);

  const setCart = useCallback((next: Cart) => setCartState(next), []);

  return { cart, totalItems: cart?.totalItems ?? 0, setCart, refresh };
}
