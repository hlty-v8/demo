import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { ApiError, NetworkError, api as defaultApi, type PdpApi } from "./api/client";
import { CartSummary } from "./components/CartSummary";
import { OptionPicker } from "./components/OptionPicker";
import { ProductGallery } from "./components/ProductGallery";
import { QuantityStepper } from "./components/QuantityStepper";
import { keyForIntent, type Attempt } from "./domain/idempotency";
import { formatPrice, toCart, type Selection } from "./domain/model";
import {
  clampQuantity,
  isSelectionComplete,
  optionState,
  priceRangeCents,
  selectionFromSearch,
  selectionToSearch,
  skuForSelection,
} from "./domain/variant";
import { useCart } from "./hooks/useCart";
import { useProduct } from "./hooks/useProduct";

export const DEFAULT_PRODUCT_ID = "aurora-trail-runner";

type AddStatus = "idle" | "adding" | "success" | "error";

export interface AppProps {
  /** Injected so tests can drive the page without a network. */
  client?: PdpApi;
  productId?: string;
  /** Overrides window.location.search (deep links arrive as a query string). */
  initialSearch?: string;
}

export function App({ client = defaultApi, productId = DEFAULT_PRODUCT_ID, initialSearch }: AppProps) {
  const { status, product, error, reload, refresh } = useProduct(client, productId);
  const { cart, totalItems, setCart } = useCart(client);

  const search = useMemo(
    () => initialSearch ?? (typeof window === "undefined" ? "" : window.location.search),
    [initialSearch],
  );

  const [selection, setSelection] = useState<Selection>({});
  const [quantity, setQuantity] = useState(1);
  const [addStatus, setAddStatus] = useState<AddStatus>("idle");
  const [feedback, setFeedback] = useState("");

  const attemptRef = useRef<Attempt | null>(null);
  const inFlightRef = useRef(false);
  const initialisedForRef = useRef<string | null>(null);

  // Deep-link the selection once per product. A later background stock refresh
  // must not throw away what the shopper has already chosen.
  useEffect(() => {
    if (!product || initialisedForRef.current === product.id) return;
    initialisedForRef.current = product.id;
    setSelection(selectionFromSearch(product, search));
  }, [product, search]);

  const sku = useMemo(
    () => (product ? skuForSelection(product, selection) : null),
    [product, selection],
  );
  const prices = useMemo(
    () => (product ? priceRangeCents(product, selection) : null),
    [product, selection],
  );

  // The quantity must stay inside the *currently selected* SKU's stock.
  useEffect(() => {
    if (!sku) return;
    setQuantity((current) => clampQuantity(current, sku.availableQty));
  }, [sku]);

  // Keep the URL in step with the selection, so a variant is shareable.
  useEffect(() => {
    if (!product || typeof window === "undefined" || typeof window.history?.replaceState !== "function") {
      return;
    }
    try {
      const query = selectionToSearch(product, selection);
      window.history.replaceState(null, "", query ? `?${query}` : window.location.pathname);
    } catch {
      // Opaque origins (some test environments) refuse history writes.
    }
  }, [product, selection]);

  const submit = useCallback(async () => {
    if (!sku || sku.availableQty <= 0 || inFlightRef.current) return;

    inFlightRef.current = true;
    setAddStatus("adding");
    setFeedback(`Adding ${quantity} × ${sku.id} to your cart…`);

    // Same intent -> same key, so a retry after a timeout cannot add twice.
    const attempt = keyForIntent(attemptRef.current, sku.id, quantity);
    attemptRef.current = attempt;
    const requestedSku = sku.id;
    const requestedQuantity = quantity;

    try {
      const response = await client.addCartItem({
        skuId: requestedSku,
        quantity: requestedQuantity,
        idempotencyKey: attempt.key,
      });
      setCart(toCart(response.cart));
      attemptRef.current = null;
      setAddStatus("success");
      setFeedback(`Added ${requestedQuantity} × ${requestedSku} to your cart.`);
    } catch (caught: unknown) {
      if (ApiError.isApiError(caught) && caught.code === "insufficient_stock") {
        const available =
          typeof caught.details.available === "number" ? caught.details.available : 0;
        setAddStatus("error");
        setFeedback(caught.message);
        setQuantity(clampQuantity(requestedQuantity, available));
        // Pull the authoritative stock so the page recovers in place.
        await refresh();
      } else if (NetworkError.isNetworkError(caught)) {
        setAddStatus("error");
        setFeedback("We could not reach the server. Retrying will not add the item twice.");
      } else if (ApiError.isApiError(caught)) {
        setAddStatus("error");
        setFeedback(caught.message);
      } else {
        setAddStatus("error");
        setFeedback("Something went wrong. Please try again.");
      }
    } finally {
      inFlightRef.current = false;
    }
  }, [client, quantity, refresh, setCart, sku]);

  const currency = product?.currency ?? "USD";
  const priceLabel = sku
    ? formatPrice(sku.priceCents, currency)
    : prices
      ? prices.min === prices.max
        ? formatPrice(prices.min, currency)
        : `${formatPrice(prices.min, currency)} – ${formatPrice(prices.max, currency)}`
      : "—";

  const complete = product ? isSelectionComplete(product, selection) : false;
  const stockLabel = !product
    ? ""
    : !complete || !sku
      ? complete
        ? "This combination is unavailable"
        : "Select a colour and size to see availability"
      : sku.availableQty > 0
        ? `In stock — ${sku.availableQty} available`
        : "Sold out";

  const canAdd = Boolean(sku) && (sku?.availableQty ?? 0) > 0 && addStatus !== "adding";

  return (
    <div className="page">
      <header className="site-header">
        <span className="brand">Aurora</span>
        <span className="cart-count">
          Cart: <strong data-testid="cart-count">{totalItems}</strong>{" "}
          {totalItems === 1 ? "item" : "items"}
        </span>
      </header>

      <main className="page__main">
        {status === "loading" ? (
          <p className="panel" role="status">
            Loading product…
          </p>
        ) : null}

        {status === "error" ? (
          <div className="panel panel--error" role="alert">
            <p>{error}</p>
            <button type="button" className="button" onClick={reload}>
              Try again
            </button>
          </div>
        ) : null}

        {status === "ready" && product ? (
          <article className="pdp">
            <ProductGallery
              imageUrl={sku?.imageUrl ?? null}
              alt={
                sku
                  ? `${product.name}, ${describeSelection(product.options, sku.options)}`
                  : `${product.name} — select a variant to see its image`
              }
            />

            <div className="pdp__details">
              <h1 className="pdp__title">{product.name}</h1>
              <p className="pdp__description">{product.description}</p>

              <p className="pdp__price" data-testid="price">
                {priceLabel}
              </p>
              <p className="pdp__stock" data-testid="stock">
                {stockLabel}
              </p>

              <form
                className="pdp__form"
                onSubmit={(event) => {
                  event.preventDefault();
                  void submit();
                }}
              >
                {product.options.map((dimension) => (
                  <OptionPicker
                    key={dimension.id}
                    dimension={dimension}
                    states={optionState(product, dimension, selection)}
                    selected={selection[dimension.id] ?? null}
                    onSelect={(valueId) =>
                      setSelection((current) => ({ ...current, [dimension.id]: valueId }))
                    }
                  />
                ))}

                <QuantityStepper
                  id="quantity"
                  value={quantity}
                  max={sku?.availableQty ?? 0}
                  disabled={!sku || sku.availableQty === 0}
                  describedBy="stock-line"
                  onChange={setQuantity}
                />
                <span id="stock-line" className="sr-only">
                  {stockLabel}
                </span>

                <button
                  type="submit"
                  className="button button--primary"
                  disabled={!canAdd}
                  aria-busy={addStatus === "adding"}
                >
                  {addStatus === "adding" ? "Adding…" : "Add to cart"}
                </button>
              </form>

              {feedback ? (
                <p
                  className={`feedback feedback--${addStatus}`}
                  role={addStatus === "error" ? "alert" : "status"}
                  aria-live={addStatus === "error" ? "assertive" : "polite"}
                >
                  {feedback}
                </p>
              ) : null}

              <CartSummary cart={cart} currency={currency} />
            </div>
          </article>
        ) : null}
      </main>
    </div>
  );
}

function describeSelection(
  dimensions: { id: string; values: { id: string; label: string }[] }[],
  options: Record<string, string>,
): string {
  return dimensions
    .map((dimension) => {
      const valueId = options[dimension.id];
      return dimension.values.find((value) => value.id === valueId)?.label ?? valueId ?? "";
    })
    .filter(Boolean)
    .join(", ");
}
