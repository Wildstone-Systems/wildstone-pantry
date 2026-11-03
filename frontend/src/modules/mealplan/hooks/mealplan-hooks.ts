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
