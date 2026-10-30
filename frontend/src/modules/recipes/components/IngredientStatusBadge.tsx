import { Badge, Tooltip } from "@mantine/core";

import type { RecipeIngredientAvailability } from "../hooks/recipes-hooks";

/**
 * Shared "do I have this?" pill for a recipe ingredient (ADR 0015 statuses).
 *
 * Extracted from RecipeAvailabilityModal so the recipe view and the modal can't
 * drift into showing the same status with different wording or colour.
 */
export const STATUS_STYLE: Record<
  RecipeIngredientAvailability["status"],
  { color: string; label: string; hint: string }
> = {
  have: { color: "green", label: "in stock", hint: "You have enough of this on hand." },
  substitute: { color: "teal", label: "via sub", hint: "Not on hand, but an approved substitute is." },
  suggested: { color: "yellow", label: "match?", hint: "A similar product is on hand — confirm it's the same thing." },
  missing: { color: "gray", label: "missing", hint: "Not on hand anywhere." },
  // ADR 0017: stock exists but its unit can't bridge to the recipe's unit.
  cant_convert: {
    color: "orange",
    label: "set density",
    hint: "You have some, but its unit can't convert to the recipe's — set a density or package size on the item.",
  },
  optional: { color: "gray", label: "optional", hint: "To-taste line — never counted as a gap." },
};

const FALLBACK = { color: "gray", label: "—", hint: "" };

export function IngredientStatusBadge({
  status,
  onHand,
}: {
  status: RecipeIngredientAvailability["status"] | undefined;
  /** Shown in the tooltip when there is any stock, so "missing" vs "not enough"
   *  is distinguishable without opening the availability modal. */
  onHand?: string | number | null;
}) {
  if (!status) return null;
  const style = STATUS_STYLE[status] ?? FALLBACK;
  const qty = Number(onHand ?? 0);
  const hint =
    Number.isFinite(qty) && qty > 0 && status !== "have"
      ? `${style.hint} On hand: ${qty}.`
      : style.hint;
  return (
    <Tooltip label={hint} multiline w={240} withArrow>
      <Badge color={style.color} variant={status === "have" || status === "substitute" ? "filled" : "light"} size="sm">
        {style.label}
      </Badge>
    </Tooltip>
  );
}
