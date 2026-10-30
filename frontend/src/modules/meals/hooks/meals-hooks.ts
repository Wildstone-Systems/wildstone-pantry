import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/api/client";

export type PlateResponse = {
  event_id: string;
  item_id: string;
  item_name: string;
  // Both null on an off-pantry plate (no location_id sent) — nothing was deducted.
  location_id: string | null;
  profile_id: string;
  quantity: string | number;
  unit: string;
  calories: string | number | null;
  protein_g: string | number | null;
  fat_g: string | number | null;
  carbs_g: string | number | null;
  balance: string | number | null;
};

export type CalorieEntry = {
  event_id: string;
  item_name: string;
  quantity: string | number;
  unit: string;
  weight_grams: string | number | null;
  calories: string | number | null;
  protein_g: string | number | null;
  fat_g: string | number | null;
  carbs_g: string | number | null;
  occurred_at: string;
};

export type CalorieRollup = {
  profile_id: string;
  date: string;
  total_calories: string | number;
  total_protein_g: string | number | null;
  total_fat_g: string | number | null;
  total_carbs_g: string | number | null;
  target: string | number | null;
  entries: CalorieEntry[];
};

export type CalorieSummary = {
  date: string;
  profiles: {
    profile_id: string;
    display_name: string;
    total_calories: string | number;
    target: string | number | null;
  }[];
};

export type PlateInput = {
  item_id: string;
  // Omit for an off-pantry plate (something eaten that never came out of stock
  // here) — the calorie/macro snapshot still logs, just deducts nothing.
  location_id?: string | null;
  quantity?: number | null;
  unit?: string;
  weight_grams?: number | null;
};

export function usePlate() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (input: PlateInput) => {
      const res = await api.post<PlateResponse>("/api/consumption/plate", input);
      return res.data;
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["calories"] });
      qc.invalidateQueries({ queryKey: ["inventory"] });
    },
  });
}

export function useDeletePlate() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (eventId: string) => {
      await api.delete(`/api/consumption/plate/${eventId}`);
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["calories"] });
      qc.invalidateQueries({ queryKey: ["inventory"] });
    },
  });
}

export type EditPlateInput = {
  eventId: string;
  quantity?: number | null;
  unit?: string;
  weight_grams?: number | null;
};

export function useEditPlate() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({ eventId, ...body }: EditPlateInput) => {
      const res = await api.patch<PlateResponse>(`/api/consumption/plate/${eventId}`, body);
      return res.data;
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["calories"] });
      qc.invalidateQueries({ queryKey: ["inventory"] });
    },
  });
}

// The server computes "today" in UTC unless told otherwise, which silently
// rolls over hours before/after the household's actual local midnight —
// evening meals would vanish from (or bleed into) the wrong day. Always tell
// it which calendar day and zone we mean.
function localDateAndTz(on?: string | null): { on: string; tz: string } {
  const tz = Intl.DateTimeFormat().resolvedOptions().timeZone;
  return { on: on || todayLocal(), tz };
}

/** Today as YYYY-MM-DD in the BROWSER's zone — never toISOString(), which is
 *  UTC and would show yesterday's log all evening in the Americas. */
export function todayLocal(): string {
  const now = new Date();
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`;
}

/** Shift a YYYY-MM-DD day by N days without tripping over DST or timezones —
 *  constructing from parts keeps it a pure calendar operation. */
export function shiftDay(day: string, delta: number): string {
  const [y, m, d] = day.split("-").map(Number);
  const dt = new Date(y, m - 1, d + delta);
  return `${dt.getFullYear()}-${String(dt.getMonth() + 1).padStart(2, "0")}-${String(dt.getDate()).padStart(2, "0")}`;
}

export function useCalorieRollup(profileId: string | null, on?: string | null) {
  return useQuery({
    queryKey: ["calories", "rollup", profileId, on ?? "today"],
    enabled: Boolean(profileId),
    queryFn: async () => {
      const res = await api.get<CalorieRollup>("/api/consumption/calories", {
        params: { profile_id: profileId, ...localDateAndTz(on) },
      });
      return res.data;
    },
  });
}

export function useCalorieSummary(on?: string | null) {
  return useQuery({
    queryKey: ["calories", "summary", on ?? "today"],
    queryFn: async () => {
      const res = await api.get<CalorieSummary>("/api/consumption/calories/summary", {
        params: localDateAndTz(on),
      });
      return res.data;
    },
  });
}

export type LoggableUnit = {
  unit: string;
  grams: string | number;
  calories: string | number | null;
  protein_g: string | number | null;
  fat_g: string | number | null;
  carbs_g: string | number | null;
};

export type LoggableUnits = {
  item_id: string;
  item_name: string;
  units: LoggableUnit[];
  unresolvable: { unit: string; reason: string }[];
  recommended_unit: string | null;
  has_nutrition: boolean;
};

/** Which units this item can actually be logged in. The picker offers only
 *  these, so a unit that silently fails to convert is never selectable. */
export function useLoggableUnits(itemId: string | null) {
  return useQuery({
    queryKey: ["loggableUnits", itemId],
    enabled: Boolean(itemId),
    staleTime: 5 * 60 * 1000,
    queryFn: async () => (await api.get<LoggableUnits>(`/api/items/${itemId}/loggable-units`)).data,
  });
}

export type BatchEntryInput = {
  item_id: string;
  quantity: number;
  unit: string;
  location_id?: string | null;
  meal_type?: string | null;
};

export function usePlateBatch() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (input: { profile_id?: string | null; entries: BatchEntryInput[] }) => {
      const res = await api.post("/api/consumption/plate-batch", input);
      return res.data as { logged: PlateResponse[]; total_calories: string | number; complete: boolean };
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["calories"] });
      qc.invalidateQueries({ queryKey: ["inventory"] });
    },
  });
}
