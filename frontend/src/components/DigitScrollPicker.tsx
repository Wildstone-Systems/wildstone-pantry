import { Stack, Text } from "@mantine/core";
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef } from "react";
import type { KeyboardEvent as ReactKeyboardEvent } from "react";

/** Tablet-sized, like QuantityStepper: `lg` for a primary kiosk control, `sm`
 *  for one of many inline rows. Three rows are visible — the chosen value in
 *  the centre band, its neighbours peeking above and below. */
const SIZES = {
  lg: { row: 40, font: 28, width: 42 },
  sm: { row: 28, font: 20, width: 30 },
} as const;

/** Read out per wheel so a screen reader says "hundreds, 1" and not "digit 1". */
const PLACE_NAMES = ["ones", "tens", "hundreds", "thousands"];

/** Wheel pixels per stop. Left to the browser, one notch of a mouse wheel
 *  (~100px) would fly past three stops on a 28px row; this makes a notch a
 *  stop, while a trackpad flick still runs several. */
const WHEEL_PER_STOP = 40;

/** How finely the fraction wheel divides one whole unit. Shown as decimals
 *  rather than ¼/½/¾: "157 .25" reads the same way it will be logged, and it
 *  stays legible at 20px next to the digits. */
const FRACTIONS = {
  halves: { denominator: 2, labels: [".0", ".5"] },
  quarters: { denominator: 4, labels: [".00", ".25", ".50", ".75"] },
  tenths: { denominator: 10, labels: [".0", ".1", ".2", ".3", ".4", ".5", ".6", ".7", ".8", ".9"] },
} as const;

export type FractionMode = keyof typeof FRACTIONS;

export type DigitScrollPickerProps = {
  value: number | string;
  onChange: (value: number) => void;
  /** Whole-number wheels, most significant first. 3 covers 0–999. */
  digits?: number;
  /** Adds a wheel for the part below 1. Off by default — most callers count
   *  whole things, and a wheel that only ever sits on .00 is a wheel in the way. */
  fraction?: FractionMode;
  min?: number;
  /** Defaults to all-nines for `digits`. A tighter ceiling clamps the value,
   *  so a wheel can refuse to go where it would overshoot. */
  max?: number;
  /** Rendered above the control, matching QuantityStepper's placement. */
  label?: string;
  /** Short name for the wheels ("Amount of yogurt"). */
  ariaLabel?: string;
  size?: keyof typeof SIZES;
  disabled?: boolean;
  /** Fired after a change lands — the scan pages use the stepper's equivalent
   *  to pull focus back to the barcode field. */
  onCommit?: () => void;
};

function clamp(v: number, low: number, high: number): number {
  return Math.min(high, Math.max(low, v));
}

/**
 * A scroll picker with one wheel per place: hundreds, tens, ones, and
 * optionally a fraction.
 *
 * Where it beats +/- buttons: any amount is at most one flick per wheel away.
 * A stepper is fine for "two apples" and hopeless for "157 g of yogurt" — 157
 * taps, or give up and type. Here both cost the same few gestures.
 *
 * Shared on purpose: nothing here knows about meals. Any quantity field with a
 * wide range and a coarse floor — plated weight, a shopping quantity, a recipe
 * yield — can take it, which is why the size, digit count, bounds and fraction
 * step are all props rather than assumptions.
 */
export function DigitScrollPicker({
  value,
  onChange,
  digits = 3,
  fraction,
  min = 0,
  max,
  label,
  ariaLabel,
  size = "sm",
  disabled = false,
  onCommit,
}: DigitScrollPickerProps) {
  const dims = SIZES[size];
  const ceiling = max ?? 10 ** digits - 1;
  const floor = Math.max(0, min);
  const name = ariaLabel ?? label ?? "amount";

  const fractionSpec = fraction ? FRACTIONS[fraction] : null;
  const denominator = fractionSpec?.denominator ?? 1;

  // Everything is counted in whole steps of 1/denominator. Fractions of a
  // quarter are not representable in binary floating point, so holding the
  // value as 0.25-at-a-time floats would drift the moment it was added up.
  const ticks = clamp(
    Math.round((Number(value) || 0) * denominator),
    Math.round(floor * denominator),
    Math.round(ceiling * denominator)
  );
  const whole = Math.floor(ticks / denominator);
  const fractionIndex = ticks - whole * denominator;

  const emit = useCallback(
    (nextTicks: number) => {
      const settled = clamp(nextTicks, Math.round(floor * denominator), Math.round(ceiling * denominator));
      if (settled === ticks) return;
      onChange(Number((settled / denominator).toFixed(3)));
      onCommit?.();
    },
    [ticks, floor, ceiling, denominator, onChange, onCommit]
  );

  // Most significant wheel first, so they read left-to-right as a number.
  const places = useMemo(() => Array.from({ length: digits }, (_, i) => digits - 1 - i), [digits]);
  const digitAt = (place: number) => Math.floor(whole / 10 ** place) % 10;
  /** Place of the highest non-zero digit — everything above it is a leading zero. */
  const topPlace = whole > 0 ? Math.floor(Math.log10(whole)) : 0;

  const wheelRefs = useRef<(HTMLDivElement | null)[]>([]);
  // Typing "157" across the wheels is still the fastest way in for anyone on a
  // keyboard, so a digit key fills this wheel and hands focus to the next.
  const focusWheel = useCallback((index: number) => {
    wheelRefs.current[index]?.focus();
  }, []);

  const digitStops = useMemo(
    () => Array.from({ length: 10 }, (_, d) => ({ display: String(d), readAs: String(d) })),
    []
  );
  const fractionStops = useMemo(
    () =>
      (fractionSpec?.labels ?? []).map((display) => ({
        display,
        // A bare ".25" is read out as an index into the wheel; "0.25" is the
        // number it actually stands for.
        readAs: `0${display}`,
      })),
    [fractionSpec]
  );

  const picker = (
    <div
      role="group"
      aria-label={name}
      style={{
        position: "relative",
        display: "inline-flex",
        gap: 2,
        padding: "0 4px",
        borderRadius: 10,
        background: "var(--pantry-hover, rgba(0,0,0,0.04))",
        opacity: disabled ? 0.5 : 1,
      }}
    >
      {places.map((place, i) => (
        <Wheel
          key={place}
          innerRef={(el) => (wheelRefs.current[i] = el)}
          stops={digitStops}
          index={digitAt(place)}
          // Leading zeros stay legible but recede, so one apple reads as "1"
          // and not "001".
          muted={place > topPlace}
          label={`${PLACE_NAMES[place] ?? `10^${place}`} of ${name}`}
          row={dims.row}
          width={dims.width}
          font={dims.font}
          disabled={disabled}
          onSelect={(d) => emit(ticks + (d - digitAt(place)) * 10 ** place * denominator)}
          onTyped={() => focusWheel(i + 1)}
        />
      ))}
      {fractionSpec && (
        <Wheel
          innerRef={(el) => (wheelRefs.current[digits] = el)}
          stops={fractionStops}
          index={fractionIndex}
          muted={fractionIndex === 0}
          label={`fraction of ${name}`}
          row={dims.row}
          // Wider and a size down: ".25" is three characters where a digit is one.
          width={dims.width + 12}
          font={dims.font * 0.8}
          disabled={disabled}
          onSelect={(f) => emit(whole * denominator + f)}
          onTyped={() => focusWheel(digits + 1)}
        />
      )}
      {/* The centre band marks what's selected. Above the wheels, out of the
          way of the pointer, so it never eats a scroll gesture. */}
      <div
        aria-hidden
        style={{
          position: "absolute",
          left: 2,
          right: 2,
          top: dims.row,
          height: dims.row,
          pointerEvents: "none",
          borderTop: "1px solid var(--pantry-divider, rgba(0,0,0,0.16))",
          borderBottom: "1px solid var(--pantry-divider, rgba(0,0,0,0.16))",
        }}
      />
    </div>
  );

  if (!label) return picker;

  return (
    <Stack gap={4}>
      <Text size="sm" fw={500}>
        {label}
      </Text>
      {picker}
    </Stack>
  );
}

type Stop = { display: string; readAs: string };

/**
 * One wheel: a list of stops, one of them parked on the centre line. Native
 * overflow scrolling does the physics — momentum on touch, wheel on desktop —
 * and CSS snap points park each stop. Digits and fractions are the same thing
 * to it, which is what keeps the scroll handling in one place.
 */
function Wheel({
  stops,
  index,
  muted,
  label,
  row,
  width,
  font,
  disabled,
  onSelect,
  onTyped,
  innerRef,
}: {
  stops: Stop[];
  index: number;
  /** Shown, but not part of the number as read (a leading zero, an empty fraction). */
  muted: boolean;
  label: string;
  row: number;
  width: number;
  font: number;
  disabled: boolean;
  onSelect: (index: number) => void;
  onTyped: () => void;
  innerRef: (el: HTMLDivElement | null) => void;
}) {
  const ref = useRef<HTMLDivElement | null>(null);
  const settleTimer = useRef<number | undefined>(undefined);
  const scrolling = useRef(false);
  const last = stops.length - 1;

  // Park the wheel on the current stop whenever it changes from the outside
  // (or on first paint). Skipped mid-gesture: yanking scrollTop while a finger
  // is still moving kills the momentum scroll.
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el || scrolling.current) return;
    const target = index * row;
    if (Math.abs(el.scrollTop - target) > 1) el.scrollTop = target;
  }, [index, row]);

  useEffect(() => () => window.clearTimeout(settleTimer.current), []);

  // Mouse wheel is handled here rather than left to the scroll container, one
  // stop per notch. Registered by hand because React's onWheel is passive, and
  // this one has to preventDefault to take the gesture over from native
  // scrolling. Touch is untouched: a finger drag emits no wheel events.
  const wheelAcc = useRef(0);
  useEffect(() => {
    const el = ref.current;
    if (!el || disabled) return;
    const onWheel = (event: WheelEvent) => {
      event.preventDefault();
      // deltaMode 1 is lines, not pixels (Firefox reports it).
      const delta = event.deltaMode === 1 ? event.deltaY * 16 : event.deltaY;
      if (delta * wheelAcc.current < 0) wheelAcc.current = 0; // reversed mid-spin
      let steps: number;
      if (Math.abs(delta) >= WHEEL_PER_STOP) {
        // One event this large is a mouse notch — worth exactly one stop,
        // whatever size that mouse reports (53, 100 and 120 are all in the wild).
        steps = Math.sign(delta);
        wheelAcc.current = 0;
      } else {
        // Trackpad: a stream of small deltas, added up.
        wheelAcc.current += delta;
        steps = Math.trunc(wheelAcc.current / WHEEL_PER_STOP);
        wheelAcc.current -= steps * WHEEL_PER_STOP;
      }
      if (!steps) return;
      // Down the list is up in value, same as dragging a finger upward.
      const next = clamp(index + steps, 0, last);
      if (next !== index) onSelect(next);
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, [index, last, disabled, onSelect]);

  const handleScroll = () => {
    const el = ref.current;
    if (!el) return;
    scrolling.current = true;
    // Report the stop once the wheel comes to rest, not on every pixel: each
    // report re-renders the cart and rewrites its saved copy.
    window.clearTimeout(settleTimer.current);
    settleTimer.current = window.setTimeout(() => {
      scrolling.current = false;
      const landed = clamp(Math.round(el.scrollTop / row), 0, last);
      if (landed !== index) onSelect(landed);
    }, 110);
  };

  const handleKeyDown = (event: ReactKeyboardEvent<HTMLDivElement>) => {
    if (disabled) return;
    // ARIA's spinbutton keys: Up raises the value even though the list runs
    // low-at-the-top, which is how a native picker behaves too.
    if (event.key === "ArrowUp") {
      event.preventDefault();
      if (index < last) onSelect(index + 1);
    } else if (event.key === "ArrowDown") {
      event.preventDefault();
      if (index > 0) onSelect(index - 1);
    } else if (event.key === "Home") {
      event.preventDefault();
      onSelect(0);
    } else if (event.key === "End") {
      event.preventDefault();
      onSelect(last);
    } else if (/^\d$/.test(event.key) && Number(event.key) <= last) {
      event.preventDefault();
      onSelect(Number(event.key));
      onTyped();
    }
  };

  return (
    <div
      className="pantry-digit-column"
      ref={(el) => {
        ref.current = el;
        innerRef(el);
      }}
      role="spinbutton"
      tabIndex={disabled ? -1 : 0}
      aria-label={label}
      aria-valuenow={index}
      aria-valuemin={0}
      aria-valuemax={last}
      aria-valuetext={stops[index]?.readAs}
      aria-disabled={disabled || undefined}
      onScroll={disabled ? undefined : handleScroll}
      onKeyDown={handleKeyDown}
      style={{
        // Three rows visible, and border-box so the padding does NOT grow the
        // scrollport: it belongs to the scrollable content, where it is the gap
        // that lets the first and last stop reach the centre line. Content-box
        // here would make the window five rows tall and put the top out of reach.
        height: row * 3,
        width,
        paddingTop: row,
        paddingBottom: row,
        boxSizing: "border-box",
      }}
    >
      {stops.map((stop, i) => (
        <div
          key={stop.display}
          onClick={disabled ? undefined : () => i !== index && onSelect(i)}
          style={{
            height: row,
            scrollSnapAlign: "center",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            cursor: disabled ? "default" : "pointer",
            fontSize: i === index ? font : font * 0.8,
            fontWeight: i === index && !muted ? 700 : 500,
            fontVariantNumeric: "tabular-nums",
            lineHeight: 1,
            color: i === index && !muted ? "var(--pantry-text)" : "var(--pantry-text-muted)",
            opacity: i === index ? (muted ? 0.75 : 1) : 0.55,
            transition: "font-size 120ms, opacity 120ms",
            userSelect: "none",
          }}
        >
          {stop.display}
        </div>
      ))}
    </div>
  );
}
