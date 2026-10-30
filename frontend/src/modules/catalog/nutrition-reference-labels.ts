const STATUS_LABELS: Record<string, string> = {
  legacy_unknown: "Confirmation unknown",
  human_selected: "Chosen by you",
  manual: "Entered manually",
  label_exact: "From product label",
  verified: "Reference verified",
  unverified: "Needs review",
  rejected: "Match rejected",
  unavailable: "Verification unavailable",
};

export const nutritionReferenceStatusLabel = (status?: string | null) =>
  STATUS_LABELS[status ?? "legacy_unknown"] ?? "Needs review";

export function nutritionReferenceSourceLabel(source?: string | null) {
  if (!source) return "Unknown source";
  if (source === "usda_fdc") return "USDA FoodData Central";
  if (["openfoodfacts", "open_food_facts"].includes(source)) return "Open Food Facts product label";
  if (source === "manual") return "Your nutrition entry";
  return source;
}
