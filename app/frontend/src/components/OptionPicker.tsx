import type { OptionDimension } from "../domain/model";
import type { ValueState } from "../domain/variant";

export interface OptionPickerProps {
  dimension: OptionDimension;
  states: ValueState[];
  selected: string | null;
  onSelect: (valueId: string) => void;
}

const AVAILABILITY_TEXT: Record<ValueState["availability"], string> = {
  available: "",
  "out-of-stock": "Sold out",
  impossible: "Not available in this combination",
};

/**
 * One option dimension rendered as a group of toggle buttons.
 *
 * Accessibility notes: a fieldset/legend names the group; each value is a
 * button with `aria-pressed`, so both the keyboard and a screen reader can
 * operate it without custom roles. Impossible combinations are `disabled`
 * (there is no SKU at all); out-of-stock variants stay selectable and are
 * labelled "Sold out" so the shopper can still see their price and image.
 */
export function OptionPicker({ dimension, states, selected, onSelect }: OptionPickerProps) {
  const stateFor = (valueId: string): ValueState =>
    states.find((state) => state.valueId === valueId) ?? {
      valueId,
      availability: "impossible",
      selectable: false,
    };

  return (
    <fieldset className="option-picker">
      <legend>
        {dimension.name}
        {selected === null ? <span className="option-picker__hint"> — choose one</span> : null}
      </legend>
      <div className="option-picker__values">
        {dimension.values.map((value) => {
          const state = stateFor(value.id);
          const isSelected = selected === value.id;
          const label = AVAILABILITY_TEXT[state.availability];
          return (
            <button
              key={value.id}
              type="button"
              className={`option-value option-value--${state.availability}`}
              aria-pressed={isSelected}
              disabled={!state.selectable}
              onClick={() => onSelect(value.id)}
            >
              <span className="option-value__label">{value.label}</span>
              {label ? <span className="option-value__badge">{label}</span> : null}
            </button>
          );
        })}
      </div>
    </fieldset>
  );
}
