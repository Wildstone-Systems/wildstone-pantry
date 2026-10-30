import { ActionIcon, Badge, Group, Select, Text, Tooltip } from "@mantine/core";
import { useEffect } from "react";
import { Trash2 } from "lucide-react";

import { DigitScrollPicker } from "@/components/DigitScrollPicker";

import { useLoggableUnits } from "../hooks/meals-hooks";

export type CartLine = {
  key: string;
  itemId: string;
  itemName: string;
  quantity: number;
  unit: string;
  /** Set once loggable-units resolves, so a line added by scan starts sane. */
  unitSeeded?: boolean;
};

function n(v: string | number | null | undefined): number | null {
  if (v == null) return null;
  const x = Number(v);
  return Number.isNaN(x) ? null : x;
}

/**
 * Grams and millilitres are counted whole — a quarter of a gram is noise, and
 * a fourth wheel that never leaves .00 is a wheel in the way. Everything else
 * (each, cup, tbsp, oz) genuinely comes in halves and quarters: half a
 * sandwich, three quarters of a cup.
 */
export function fractionForUnit(unit: string): "quarters" | undefined {
  return unit === "g" || unit === "ml" ? undefined : "quarters";
}

/** Snap an amount to what the wheels for that unit can show. Anything finer
 *  would be logged but never displayed — the number on screen has to be the
 *  number that gets sent. */
export function snapQuantity(quantity: number | string, unit: string): number {
  const q = Math.max(0, Number(quantity) || 0);
  return fractionForUnit(unit) ? Math.round(q * 4) / 4 : Math.round(q);
}

/**
 * One pending line. Its unit picker offers ONLY units that actually convert for
 * this item — the whole point: you can see the calories before confirming
 * rather than discovering after the fact that nothing could be computed.
 */
export function CartLineRow({
  line,
  onChange,
  onRemove,
}: {
  line: CartLine;
  onChange: (next: CartLine) => void;
  onRemove: () => void;
}) {
  const units = useLoggableUnits(line.itemId);
  const options = (units.data?.units ?? []).map((u) => ({ value: u.unit, label: u.unit }));
  const chosen = units.data?.units.find((u) => u.unit === line.unit);
  const perUnitCalories = n(chosen?.calories ?? null);
  const lineCalories = perUnitCalories == null ? null : perUnitCalories * line.quantity;

  // Adopt the recommended unit the moment we learn it — a line added by scan
  // has no way to know "each" is meaningful for a protein bar but not a soda.
  // In an effect, not in render: calling onChange during render sets state on
  // the parent mid-render, which React rejects.
  const recommended = units.data?.recommended_unit;
  useEffect(() => {
    if (recommended && !line.unitSeeded) {
      onChange({
        ...line,
        unit: recommended,
        unitSeeded: true,
        quantity: snapQuantity(line.quantity, recommended),
      });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [recommended, line.unitSeeded]);

  const noNutrition = units.data && !units.data.has_nutrition;

  return (
    <Group justify="space-between" wrap="nowrap" align="center" gap="xs">
      <div style={{ minWidth: 0, flex: 1 }}>
        <Text size="sm" truncate>
          {line.itemName}
        </Text>
        {noNutrition ? (
          <Tooltip label="No per-100g nutrition on this item — it will log the amount but add 0 calories">
            <Badge size="xs" color="orange" variant="light">
              no nutrition data
            </Badge>
          </Tooltip>
        ) : lineCalories != null ? (
          <Text size="xs" c="dimmed">
            {Math.round(lineCalories)} kcal
          </Text>
        ) : (
          <Text size="xs" c="dimmed">
            —
          </Text>
        )}
      </div>
      <Group gap={4} wrap="nowrap">
        {/* Digit wheels, not +/- buttons: a meal is as likely to be "157 g of
            yogurt" as "1 apple", and 157 taps is not a control. */}
        <DigitScrollPicker
          size="sm"
          ariaLabel={`Amount of ${line.itemName}`}
          min={0}
          fraction={fractionForUnit(line.unit)}
          value={line.quantity}
          onChange={(v) => onChange({ ...line, quantity: v })}
        />
        <Select
          size="xs"
          w={82}
          data={options}
          value={line.unit}
          aria-label={`Unit for ${line.itemName}`}
          disabled={options.length === 0}
          // Switching to grams drops the fraction wheel, so the fraction has to
          // go with it — otherwise "1.25" stays in the line showing "001".
          onChange={(v) =>
            onChange({ ...line, unit: v || line.unit, quantity: snapQuantity(line.quantity, v || line.unit) })
          }
          comboboxProps={{ withinPortal: true }}
        />
        <ActionIcon variant="subtle" color="red" aria-label={`Remove ${line.itemName}`} onClick={onRemove}>
          <Trash2 size={16} />
        </ActionIcon>
      </Group>
    </Group>
  );
}
