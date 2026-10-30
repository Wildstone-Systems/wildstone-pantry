import {
  Button,
  Card,
  createTheme,
  Input,
  type MantineColorsTuple,
  Paper,
  rem,
  SegmentedControl,
  Table,
} from "@mantine/core";

// ---------------------------------------------------------------------------
// Organic design system → Mantine theme
//
// Ports the "Organic" tokens from the kiosk redesign handoff (warm terracotta +
// sage on a cream ground) into a Mantine v7 theme. The raw color values also
// live as CSS variables in global.css (`--pantry-*`) so bespoke surfaces (nav,
// console, day-tags) can reference them directly; the Mantine theme below is
// the source of truth for components Mantine already owns.
// ---------------------------------------------------------------------------

// Terracotta accent ramp. Base accent (#c67139) sits at index 6 so it becomes
// the default `primaryColor` shade; 7 = hover, 8 = active. 0–2 map to the
// Organic accent 100/200/300 tints used for tags.
const terracotta: MantineColorsTuple = [
  "#fff2eb", // 0  accent-100
  "#ffe1d0", // 1  accent-200
  "#ffc6a5", // 2  accent-300
  "#f0a877", // 3
  "#e08a54", // 4
  "#d17b45", // 5
  "#c67139", // 6  accent (base)
  "#b2622d", // 7  accent hover
  "#8c491a", // 8  accent active / accent-700
  "#643312", // 9  accent-800
];

// Sage secondary ramp. Base (#7a8a5e) at index 6.
const sage: MantineColorsTuple = [
  "#f0fae1", // 0  sage-100
  "#e1eecc", // 1  sage-200
  "#cfe0b0", // 2
  "#b6cd90", // 3
  "#9db978", // 4
  "#8aa668", // 5
  "#7a8a5e", // 6  sage (base)
  "#728157", // 7  sage-600
  "#566541", // 8
  "#3d472b", // 9  sage-800
];

const HEADING_FONT = "'Caprasimo', Georgia, 'Times New Roman', serif";
const BODY_FONT = "'Figtree', system-ui, -apple-system, BlinkMacSystemFont, 'Helvetica Neue', sans-serif";

export const theme = createTheme({
  primaryColor: "terracotta",
  primaryShade: { light: 6, dark: 6 },
  defaultRadius: "md",
  fontFamily: BODY_FONT,
  fontFamilyMonospace: "ui-monospace, SFMono-Regular, Menlo, monospace",
  colors: {
    terracotta,
    sage,
  },
  radius: {
    xs: rem(6),
    sm: rem(8),
    md: rem(16),
    lg: rem(24),
    xl: rem(28),
  },
  fontSizes: {
    xs: rem(12),
    sm: rem(13),
    md: rem(15),
    lg: rem(17),
    xl: rem(20),
  },
  lineHeights: {
    md: "1.55",
  },
  headings: {
    fontFamily: HEADING_FONT,
    fontWeight: "400",
    sizes: {
      h1: { fontSize: rem(38), fontWeight: "400", lineHeight: "1.1" },
      h2: { fontSize: rem(30), fontWeight: "400", lineHeight: "1.15" },
      h3: { fontSize: rem(20), fontWeight: "400", lineHeight: "1.2" },
      h4: { fontSize: rem(17), fontWeight: "400", lineHeight: "1.25" },
    },
  },
  spacing: {
    xs: rem(8),
    sm: rem(12),
    md: rem(16),
    lg: rem(24),
    xl: rem(32),
  },
  components: {
    // Cards / panels are the warm over-rounded surface.
    Paper: Paper.extend({
      defaultProps: { radius: rem(22) },
    }),
    Card: Card.extend({
      defaultProps: { radius: rem(22) },
      styles: {
        root: { backgroundColor: "var(--pantry-surface)" },
      },
    }),
    // Buttons, inputs and segmented controls are pills (kiosk touch targets).
    Button: Button.extend({
      defaultProps: { radius: 999 },
      styles: {
        root: { fontWeight: 600 },
      },
    }),
    Input: Input.extend({
      defaultProps: { radius: 999 },
      // A fixed minHeight is a kiosk touch-target rule for ordinary inputs —
      // but react-textarea-autosize (Textarea's `autosize` mode) hard-checks
      // `'minHeight' in style` and throws if the key is present AT ALL, even
      // as `undefined`. So this must genuinely omit the key (not just fall
      // back to something falsy) whenever autosize is active.
      //
      // Can't key off `props.autosize` here — Mantine's Textarea destructures
      // and STRIPS that literal prop before forwarding the rest down to this
      // Input (see Textarea.mjs), so it never reaches this styles function.
      // `minRows`/`maxRows` DO survive that forwarding, but only when autosize
      // is genuinely active (Textarea omits them otherwise) — so their
      // presence is the reliable signal here instead.
      styles: (_theme, props) => {
        // Not on InputProps' type — these only exist when the actual
        // rendered component is a Textarea in autosize mode (see the long
        // comment above); read them as the untyped pass-through props they are.
        const { minRows, maxRows } = props as unknown as { minRows?: number; maxRows?: number };
        return {
          input: {
            ...(minRows !== undefined || maxRows !== undefined ? {} : { minHeight: rem(44) }),
            backgroundColor: "var(--pantry-bg)",
            borderColor: "var(--pantry-divider)",
          },
        };
      },
    }),
    SegmentedControl: SegmentedControl.extend({
      defaultProps: { radius: 999, color: "terracotta" },
    }),
    Table: Table.extend({
      defaultProps: { horizontalSpacing: "md", verticalSpacing: "sm" },
    }),
  },
});
