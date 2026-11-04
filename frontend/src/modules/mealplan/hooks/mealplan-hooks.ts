import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/api/client";

export const MEAL_TYPES = ["breakfast", "lunch", "dinner", "snack"] as const;
export type MealType = (typeof MEAL_TYPES)[number];
export type EntryStatus = "planned" | "cooked" | "skipped";

export type MealPlanEntry = {
  id: string;
  plan_date: string;
  meal_type: MealType;
  position: number;
  kind: "recipe" | "item" | "text";
  meal: string;
  recipe_id: string | null;
  item_id: string | null;
  title: string | null;
  servings: number;
  profile_id: string | null;
  status: EntryStatus;
  cook_event_id: string | null;
  source: "manual" | "agent";
  note: string | null;
  created_by: string | null;
  created_at: string;
  updated_at: string;
};

export type MealPlanWeek = { start: string; days: number; entries: MealPlanEntry[] };

/** Exactly one of recipe_id / item_id / title names the meal. */
export type MealPlanEntryInput = {
  plan_date: string;
  meal_type: MealType;
  recipe_id?: string | null;
  item_id?: string | null;
  title?: string | null;
  servings?: number | null;
  profile_id?: string | null;
  note?: string | null;
};

export type MealPlanEntryPatch = Partial<MealPlanEntryInput> & { position?: number };

const KEY = ["meal-plan"] as const;

/** Parse YYYY-MM-DD as a local calendar day (no timezone drift). */
function parseDay(day: string): Date {
  const [y, m, d] = day.split("-").map(Number);
  return new Date(y, m - 1, d);
}

function formatDay(dt: Date): string {
  return `${dt.getFullYear()}-${String(dt.getMonth() + 1).padStart(2, "0")}-${String(dt.getDate()).padStart(2, "0")}`;
}

export function addDays(day: string, delta: number): string {
  const dt = parseDay(day);
  dt.setDate(dt.getDate() + delta);
  return formatDay(dt);
}

/** The Sunday on or before `day` — planning weeks start on Sunday (#28). */
export function sundayOf(day: string): string {
  const dt = parseDay(day);
  dt.setDate(dt.getDate() - dt.getDay());
  return formatDay(dt);
}

export function todayLocal(): string {
  return formatDay(new Date());
}

export function weekDays(start: string): string[] {
  return Array.from({ length: 7 }, (_, i) => addDays(start, i));
}

export function useMealPlanWeek(start: string) {
  return useQuery({
    queryKey: [...KEY, "week", start],
    queryFn: async () => {
      const res = await api.get<MealPlanWeek>("/api/meal-plan/entries", { params: { start, days: 7 } });
      return res.data;
    },
  });
}

function useInvalidate() {
  const qc = useQueryClient();
  return () => qc.invalidateQueries({ queryKey: KEY });
}

export function useCreateMealPlanEntry() {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: async (input: MealPlanEntryInput) =>
      (await api.post<MealPlanEntry>("/api/meal-plan/entries", input)).data,
    onSuccess: invalidate,
  });
}

export function useUpdateMealPlanEntry() {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: async ({ id, patch }: { id: string; patch: MealPlanEntryPatch }) =>
      (await api.patch<MealPlanEntry>(`/api/meal-plan/entries/${id}`, patch)).data,
    onSuccess: invalidate,
  });
}

export function useDeleteMealPlanEntry() {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: async (id: string) => {
      await api.delete(`/api/meal-plan/entries/${id}`);
    },
    onSuccess: invalidate,
  });
}

export function useSetMealPlanStatus() {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: async ({ id, action }: { id: string; action: "skip" | "reset" }) =>
      (await api.post<MealPlanEntry>(`/api/meal-plan/entries/${id}/${action}`)).data,
    onSuccess: invalidate,
  });
}

// --- recipe completeness (#30) ------------------------------------------------

export type RecipeCompleteness = {
  recipe_id: string;
  recipe_name: string;
  available_score: number;
  have_count: number;
  needed_count: number;
  missing_items: string[];
};

/** A recipe counts as "mostly complete" at or above this share on hand. */
export const MOSTLY_COMPLETE = 0.75;

/** How much of each recipe is on hand, pooled across every location. */
export function useRecipeCompleteness() {
  return useQuery({
    queryKey: [...KEY, "recipe-completeness"],
    queryFn: async () => {
      const res = await api.get<{ suggestions: RecipeCompleteness[] }>("/api/recipes/suggestions");
      return res.data.suggestions;
    },
  });
}

export type RecipeOption = {
  value: string;
  label: string;
  /** Null when the recipe has no required ingredients to score. */
  completeness: RecipeCompleteness | null;
};

/**
 * Every recipe, most complete first, then unscored ones, then A–Z within ties.
 * All recipes stay pickable: the marker guides the choice, it never blocks it.
 */
export function buildRecipeOptions(
  recipes: { id: string; name: string }[],
  completeness: RecipeCompleteness[],
  { mostlyCompleteOnly = false }: { mostlyCompleteOnly?: boolean } = {},
): RecipeOption[] {
  // Nothing counted (only optional lines) means unscored, not "100% on hand".
  const byId = new Map(completeness.filter((c) => c.needed_count > 0).map((c) => [c.recipe_id, c]));
  const score = (o: RecipeOption) => (o.completeness ? o.completeness.available_score : -1);
  return recipes
    .map((r) => ({ value: r.id, label: r.name, completeness: byId.get(r.id) ?? null }))
    .filter((o) => !mostlyCompleteOnly || (o.completeness != null && o.completeness.available_score >= MOSTLY_COMPLETE))
    .sort((a, b) => score(b) - score(a) || a.label.localeCompare(b.label));
}

/** "8/10 on hand", or null when there is nothing to count. */
export function completenessLabel(c: RecipeCompleteness | null): string | null {
  if (!c || c.needed_count === 0) return null;
  return `${c.have_count}/${c.needed_count} on hand`;
}

// --- shopping bridge (#31) ------------------------------------------------------

export type ShoppingGapLine = {
  /** canon:<ingredient> or item:<item id> */
  key: string;
  label: string;
  unit: string;
  needed: string | number;
  on_hand: string | number | null;
  /** null when the amount can't be measured: check by hand. */
  to_buy: string | number | null;
  status: "short" | "check";
  meals: string[];
};

export type ExcludedItem = { id: string; key: string; label: string };

export type ShoppingGap = { start: string; end: string; lines: ShoppingGapLine[]; excluded: ExcludedItem[] };

export type ShoppingGapPushResult = { added: string[]; already_on_list: string[] };

export function useShoppingGap(start: string, today: string, enabled: boolean) {
  return useQuery({
    queryKey: [...KEY, "shopping-gap", start, today],
    enabled,
    queryFn: async () =>
      (await api.get<ShoppingGap>("/api/meal-plan/shopping-gap", { params: { start, today, days: 7 } })).data,
  });
}

export function usePushShoppingGap() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (body: { start: string; today: string; keys: string[] }) =>
      (await api.post<ShoppingGapPushResult>("/api/meal-plan/shopping-gap/push", { ...body, days: 7 })).data,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["shopping-list"] }),
  });
}

export function useExcludeItem() {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: async (body: { key: string; label: string }) =>
      (await api.post<ExcludedItem>("/api/meal-plan/excluded-items", body)).data,
    onSuccess: invalidate,
  });
}

export function useUnexcludeItem() {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: async (id: string) => {
      await api.delete(`/api/meal-plan/excluded-items/${id}`);
    },
    onSuccess: invalidate,
  });
}

/** "3 cup", "2 each" — trims trailing zeros from decimal strings. */
export function formatAmount(value: string | number | null, unit: string): string {
  if (value == null) return "?";
  const n = Number(value);
  return `${Number.isNaN(n) ? value : Number(n.toFixed(2))} ${unit}`;
}

// --- cook from plan (#32) -------------------------------------------------------

/** Where "Cook this" on a planned meal goes: the existing cook-along, sized
 *  to the meal's pinned servings, remembering which meal it is for. */
export function cookFromPlanPath(entry: Pick<MealPlanEntry, "id" | "recipe_id" | "servings">): string {
  const params = new URLSearchParams({ servings: String(entry.servings), planEntry: entry.id });
  return `/recipes/${entry.recipe_id}/cook?${params.toString()}`;
}

/** Record which finished cook fulfilled a planned meal. */
export function useMarkMealCooked() {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: async (p: { entryId: string; cookEventId: string }) =>
      (await api.post<MealPlanEntry>(`/api/meal-plan/entries/${p.entryId}/cooked`, { cook_event_id: p.cookEventId }))
        .data,
    onSuccess: invalidate,
  });
}

/** Planned, uncooked meals of this recipe on `day`: what "Was this Sunday's
 *  dinner?" offers after a cook that didn't start from the plan. */
export async function fetchCookCandidates(recipeId: string, day: string): Promise<MealPlanEntry[]> {
  const res = await api.get<MealPlanEntry[]>("/api/meal-plan/cook-candidates", { params: { recipe_id: recipeId, on: day } });
  return res.data;
}

// --- calendar feed (#33) ---------------------------------------------------------

export type CalendarFeedStatus = { enabled: boolean; created_at: string | null };

export function useCalendarFeedStatus(enabled: boolean) {
  return useQuery({
    queryKey: [...KEY, "calendar-feed"],
    enabled,
    queryFn: async () => (await api.get<CalendarFeedStatus>("/api/meal-plan/calendar-feed")).data,
  });
}

/** A new secret link (shown once; the old one stops working). */
export function useCreateCalendarFeed() {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: async () => (await api.post<{ path: string; created_at: string }>("/api/meal-plan/calendar-feed")).data,
    onSuccess: invalidate,
  });
}

export function useTurnOffCalendarFeed() {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: async () => {
      await api.delete("/api/meal-plan/calendar-feed");
    },
    onSuccess: invalidate,
  });
}

// --- nutrition forecast (#34) ----------------------------------------------------

export type ForecastDay = { date: string; calories: number; partial: boolean; unknown_meals: number; meals: number };
export type ForecastPerson = { profile_id: string; display_name: string; target: number | null; days: ForecastDay[] };
export type NutritionForecast = { start: string; days: number; people: ForecastPerson[] };

export function useNutritionForecast(start: string) {
  return useQuery({
    queryKey: [...KEY, "nutrition", start],
    queryFn: async () =>
      (await api.get<NutritionForecast>("/api/meal-plan/nutrition", { params: { start, days: 7 } })).data,
  });
}

/** "1,850 / 2,000 kcal", "~400 kcal +1?" — `~` = some ingredient lacks data,
 *  `+N?` = N planned meals with no calorie data at all. */
export function forecastLabel(day: ForecastDay, target: number | null): string {
  const kcal = `${day.partial ? "~" : ""}${day.calories.toLocaleString()}`;
  const base = target != null ? `${kcal} / ${Math.round(target).toLocaleString()} kcal` : `${kcal} kcal`;
  return day.unknown_meals > 0 ? `${base} +${day.unknown_meals}?` : base;
}
