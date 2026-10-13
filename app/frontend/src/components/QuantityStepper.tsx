import { clampQuantity } from "../domain/variant";

export interface QuantityStepperProps {
  id: string;
  value: number;
  max: number;
  disabled?: boolean;
  describedBy?: string;
  onChange: (value: number) => void;
}

/**
 * Quantity control constrained to [1, max].
 *
 * The input is type="number" with explicit min/max, but every change is clamped
 * in the domain function as well, because browsers let people type anything into
 * a number field (and paste is not validated by the browser at all).
 */
export function QuantityStepper({
  id,
  value,
  max,
  disabled = false,
  describedBy,
  onChange,
}: QuantityStepperProps) {
  const canDecrease = !disabled && max > 0 && value > 1;
  const canIncrease = !disabled && max > 0 && value < max;

  return (
    <div className="quantity">
      <label htmlFor={id}>Quantity</label>
      <div className="quantity__controls">
        <button
          type="button"
          className="quantity__button"
          onClick={() => onChange(clampQuantity(value - 1, max))}
          disabled={!canDecrease}
          aria-label="Decrease quantity"
        >
          −
        </button>
        <input
          id={id}
          className="quantity__input"
          type="number"
          inputMode="numeric"
          min={max > 0 ? 1 : 0}
          max={max}
          value={value}
          disabled={disabled}
          aria-describedby={describedBy}
          onChange={(event) => onChange(clampQuantity(Number(event.target.value), max))}
          onBlur={(event) => onChange(clampQuantity(Number(event.target.value), max))}
        />
        <button
          type="button"
          className="quantity__button"
          onClick={() => onChange(clampQuantity(value + 1, max))}
          disabled={!canIncrease}
          aria-label="Increase quantity"
        >
          +
        </button>
      </div>
    </div>
  );
}
