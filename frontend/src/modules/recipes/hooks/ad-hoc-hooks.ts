/** Ad hoc cooking hooks (ADR 0024).
 *
 * The session is a server-side draft recipe row — every append commits
 * immediately, so unlike the cook-along walkthrough there is no localStorage
 * mirror: closing the tab loses nothing, and another device can resume.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/api/client";

import type { Recipe } from "./recipes-hooks";

// ============ Types ============

export type AdHocIngredientLine = {
  ingredient_id: string;
  item_id: string;
  item_name: string;
  quantity_required: string;
  unit: string;
  optional: boolean;
};

export type AdHocFlaggedUnit = {
  index: number;
  item_name: string | null;
  unit: string;
  suggestion: string | null;
};

/** The draft as the server has it. Steps live in `recipe.instructions` as
 *  sequentially numbered lines — the canonical cook-along shape. */
export type AdHocState = {
  recipe: Recipe;
  ingredients: AdHocIngredientLine[];
  flagged_units: AdHocFlaggedUnit[];
};

export type AdHocIngredientUse = {
  item_id?: string | null;
  name?: string | null;
  quantity: number;
  unit: string;
};

/** One cooking action: ingredient use(s), a step, or both at once — a
 *  dressing's oil, vinegar and mustard go in ONE entry with the one step
 *  that used them. */
export type AdHocEntryPayload = {
  ingredients?: AdHocIngredientUse[];
  step_text?: string | null;
};

export type AdHocFinishPayload = {
  servings_made: number;
  save: boolean;
  name?: string | null;
  location_id?: string | null;
  note?: string | null;
};

export type AdHocFinishResult = {
  recipe_id: string;
  saved: boolean;
  /** False when nothing could be deducted and no location was given — there
   *  was no location to stamp a cook event with. */
  cook_logged: boolean;
  cook_event_id: string | null;
  location_id: string | null;
  deducted_transaction_ids: string[];
  deducted: { item_id: string; item_name: string; quantity: number; unit: string }[];
  /** Lines the ledger couldn't fully cover — the stock that WAS there has been
   *  deducted to zero; this is surfaced drift, not an error (ADR 0024 §3). */
  shortfalls: { item_name: string; short: number; unit: string }[];
  /** Lines with no unit bridge — nothing deducted rather than guessing. */
  unconvertible: { item_name: string; reason: string }[];
};

// ============ Hooks ============

export function useAdHocDrafts() {
  return useQuery({
    queryKey: ["adHocDrafts"],
    queryFn: async () => (await api.get<Recipe[]>("/api/recipes/ad-hoc")).data,
  });
}

export function useStartAdHoc() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (payload: { name?: string | null; entries?: AdHocEntryPayload[] }) =>
      (await api.post<AdHocState>("/api/recipes/ad-hoc", payload)).data,
    onSuccess: async (state) => {
      queryClient.setQueryData(["adHocState", state.recipe.id], state);
      await queryClient.invalidateQueries({ queryKey: ["adHocDrafts"] });
      // Seed entries may have minted catalog items.
      await queryClient.invalidateQueries({ queryKey: ["items"] });
    },
  });
}

/** The draft's current state — resolved through the append responses when
 *  they arrive, fetched fresh on resume. */
export function useAdHocState(recipeId: string | null) {
  return useQuery({
    queryKey: ["adHocState", recipeId],
    queryFn: async (): Promise<AdHocState> => {
      const res = await api.get<{ recipe: Recipe; ingredients: AdHocIngredientLine[] }>(
        `/api/recipes/${recipeId}`,
      );
      return { ...res.data, flagged_units: [] };
    },
    enabled: Boolean(recipeId),
  });
}

export function useAppendAdHocEntry() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ recipeId, entry }: { recipeId: string; entry: AdHocEntryPayload }) =>
      (await api.post<AdHocState>(`/api/recipes/${recipeId}/ad-hoc/entries`, entry)).data,
    onSuccess: async (state, { recipeId }) => {
      // The response IS the new state — no refetch round-trip mid-cook.
      queryClient.setQueryData(["adHocState", recipeId], state);
      await queryClient.invalidateQueries({ queryKey: ["items"] });
    },
  });
}

export function useFinishAdHoc() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ recipeId, payload }: { recipeId: string; payload: AdHocFinishPayload }) =>
      (await api.post<AdHocFinishResult>(`/api/recipes/${recipeId}/ad-hoc/finish`, payload)).data,
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["adHocDrafts"] }),
        queryClient.invalidateQueries({ queryKey: ["recipes"] }),
        queryClient.invalidateQueries({ queryKey: ["recipeSuggestions"] }),
        queryClient.invalidateQueries({ queryKey: ["inventory"] }),
        queryClient.invalidateQueries({ queryKey: ["transactions"] }),
        queryClient.invalidateQueries({ queryKey: ["expiringItems"] }),
      ]);
    },
  });
}

/** Abandon a draft: nothing has been deducted, so deleting it costs nothing
 *  (ADR 0024 §4) — the same DELETE the recipe list uses. */
export function useAbandonAdHoc() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (recipeId: string) => {
      await api.delete(`/api/recipes/${recipeId}`);
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["adHocDrafts"] });
    },
  });
}
