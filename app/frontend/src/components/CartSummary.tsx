import { formatPrice, type Cart } from "../domain/model";

export interface CartSummaryProps {
  cart: Cart | null;
  currency: string;
}

export function CartSummary({ cart, currency }: CartSummaryProps) {
  const items = cart?.items ?? [];

  return (
    <section className="cart" aria-labelledby="cart-heading">
      <h2 id="cart-heading">Your cart</h2>
      {items.length === 0 ? (
        <p className="cart__empty">Your cart is empty.</p>
      ) : (
        <>
          <ul className="cart__items">
            {items.map((item) => (
              <li key={item.skuId} className="cart__item">
                <span className="cart__item-name">
                  {item.options["color"] ?? item.skuId} / {item.options["size"] ?? ""}
                </span>
                <span className="cart__item-quantity">×{item.quantity}</span>
                <span className="cart__item-total">
                  {formatPrice(item.lineTotalCents, currency)}
                </span>
              </li>
            ))}
          </ul>
          <p className="cart__subtotal">
            Subtotal: <strong>{formatPrice(cart?.subtotalCents ?? 0, currency)}</strong>
          </p>
        </>
      )}
    </section>
  );
}
