import { ActionIcon, Group, NumberInput, Stack, Text } from "@mantine/core";
import type { NumberInputProps } from "@mantine/core";
import { forwardRef } from "react";

/** Tablet-sized. Matches the scan station's check-in controls. */
const SIZES = {
  lg: { control: 48, glyph: 30, input: 64 },
  sm: { control: 34, glyph: 20, input: 56 },
} as const;

export type QuantityStepperProps = {
  value: number | string;
  onChange: (value: number | string) => void;
  /** Rendered above the control. Deliberately NOT the NumberInput's own `label`:
   *  a label on the input adds height to the middle element only, throwing the
   *  number off-centre against the +/- buttons either side. */
  label?: string;
  min?: number;
  max?: number;
  step?: number;
  decimalScale?: number;
  disabled?: boolean;
  /** `lg` (48px) for a primary kiosk control; `sm` for one of many inline rows. */
  size?: keyof typeof SIZES;
  /** Short name for the +/- buttons ("Decrease quantity"). The visible `label`
   *  still names the input itself, so the accessible name matches what's on
   *  screen; this only keeps the buttons from reading out a whole question. */
  ariaLabel?: string;
  /** Width of the number field — widen it for values like "0.125". */
  inputWidth?: number;
  /** Fired after a +/- tap, once the value has been set. The scan pages use it
   *  to pull focus straight back to the barcode field. */
  onStep?: () => void;
  inputProps?: Partial<NumberInputProps>;
};

/**
 * A number field flanked by big +/- buttons (ADR-less UI convention, first used
 * on the scan station): a native spinner's arrows are a ~10px target, unusable
 * on a tablet with wet hands in a kitchen. Extracted from ScanPage so every
 * quantity field in the app nudges the same way.
 */
export const QuantityStepper = forwardRef<HTMLInputElement, QuantityStepperProps>(
  function QuantityStepper(
    {
      value,
      onChange,
      label,
      min = 0,
      max,
      step = 1,
      decimalScale,
      disabled = false,
      size = "lg",
      inputWidth,
      onStep,
      ariaLabel,
      inputProps,
    },
    ref,
  ) {
    const dims = SIZES[size];
    const current = Number(value) || 0;
    // The input takes the VISIBLE label when there is one — an accessible name
    // that doesn't match the on-screen text is its own bug. The buttons take
    // the short name so they don't read out an entire question.
    const inputName = label ?? ariaLabel ?? "quantity";
    const buttonName = ariaLabel ?? label ?? "quantity";

    // toFixed(3) then back through Number: 0.1 + 0.2 is 0.30000000000000004 in
    // binary floating point, and a stepper is exactly where that surfaces.
    const nudge = (delta: number) => {
      const next = Number((current + delta).toFixed(3));
      const clampedLow = min != null ? Math.max(min, next) : next;
      onChange(max != null ? Math.min(max, clampedLow) : clampedLow);
      onStep?.();
    };

    const control = (
      <Group gap={4} wrap="nowrap" align="center">
        <ActionIcon
          size={dims.control}
          variant="light"
          radius="md"
          aria-label={`Decrease ${buttonName}`}
          disabled={disabled || (min != null && current <= min)}
          onClick={() => nudge(-step)}
        >
          <Text fz={dims.glyph} fw={800} lh={1}>
            −
          </Text>
        </ActionIcon>
        <NumberInput
          ref={ref}
          value={value}
          onChange={(val) => {
            // Clearing stays cleared. Snapping to `min` here would make the
            // field impossible to retype: you'd clear it, it would refill with
            // the minimum, and your next keystroke would append to that.
            // Callers coerce ("" -> their own default) when they read it.
            onChange(val === "" ? "" : typeof val === "number" ? val : Number(val) || 0);
          }}
          min={min}
          max={max}
          step={step}
          decimalScale={decimalScale}
          disabled={disabled}
          hideControls
          size="sm"
          // Mobile keyboards: "decimal" gives the number pad WITH a decimal
          // point. Mantine's default is "numeric", which on iOS has no ".", so
          // half a serving becomes impossible to type on a phone.
          inputMode="decimal"
          w={inputWidth ?? dims.input}
          aria-label={inputName}
          styles={{ input: { textAlign: "center", height: dims.control } }}
          {...inputProps}
        />
        <ActionIcon
          size={dims.control}
          variant="light"
          radius="md"
          aria-label={`Increase ${buttonName}`}
          disabled={disabled || (max != null && current >= max)}
          onClick={() => nudge(step)}
        >
          <Text fz={dims.glyph} fw={800} lh={1}>
            +
          </Text>
        </ActionIcon>
      </Group>
    );

    if (!label) return control;

    return (
      <Stack gap={4}>
        <Text size="sm" fw={500}>
          {label}
        </Text>
        {control}
      </Stack>
    );
  },
);
