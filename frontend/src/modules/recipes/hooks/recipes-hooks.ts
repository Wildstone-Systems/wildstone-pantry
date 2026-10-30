/** Recipes domain hooks.
 *
 * Self-contained recipe hooks module (Phase 5).
 */

import { useDebouncedValue } from "@mantine/hooks";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "@/api/client";

// ============ Types ============

export type Recipe = {
  id: string;
  name: string;
  instructions: string | null;
  servings: number;
  output_item_id: string | null;
  output_quantity: number | null;
  output_unit: string | null;
  created_at: string;
  /** Present on the list endpoint; a recipe with 0 can never be cooked or suggested. */
  ingredient_count?: number;
  // Set when this recipe was imported (Recipes -> Find a Recipe Online, or the
  // agent bulk-import path) — a second import of the same source/id upserts
  // instead of duplicating.
  external_source?: string | null;
  external_id?: string | null;
  external_url?: string | null;
  /** Where to render this recipe's picture from, already resolved by the API:
   *  an uploaded photo wins over an imported one, null means there is none. */
  image_src?: string | null;
  /** True while this row is an in-progress ad hoc cook (ADR 0024) — such rows
   *  only surface via the ad hoc endpoints, never the recipe list. */
  is_draft?: boolean;
  source_text?: string | null;
  import_review?: { stale?: boolean; warning?: string; lines?: Omit<RecipeSourceLine, "raw_line">[]; [key: string]: unknown } | null;
};

export type RecipeIngredientInput = {
  item_id: string;
  quantity_required: number;
  unit: string;
  /** ADR 0017 §4.1: a to-taste/garnish line — out of the score, never auto-deducted. */
  optional?: boolean;
  /** ADR 0025 §5: 'package' means N whole packages, whatever they weigh. */
  quantity_mode?: "amount" | "package" | null;
  yield_basis?: boolean | null;
  // Present on GET (RecipeIngredientRead) but not the id-only PUT primitive.
  ingredient_id?: string;
  item_name?: string;
};

/** One recipe unit the editor may offer (ADR 0017 §3) — served by the backend so
 *  the vocabulary is a single source of truth. */
export type UnitVocabItem = { value: string; label: string; dimension: string };

export function useUnitVocabulary() {
  return useQuery({
    queryKey: ["unitVocab"],
    queryFn: async () => {
      const res = await api.get<{ units: UnitVocabItem[] }>("/api/recipes/unit-vocab");
      return res.data.units;
    },
    staleTime: Infinity, // a fixed, closed set — fetch once per session
  });
}

// ============ External recipe search / import ============

export type RecipeSearchResult = {
  source: string;
  external_id: string;
  name: string;
  image_url: string | null;
  source_label: string;
  category: string | null;
  area: string | null;
  servings: number | null;
  ready_in_minutes: number | null;
};

export type RecipeSearchApiResponse = {
  results: RecipeSearchResult[];
  providers_active: string[];
};

/** Search external recipe providers by name — debounced so typing doesn't fire
 * a request per keystroke, disabled below 2 characters. */
export function useSearchExternalRecipes(query: string) {
  const [debounced] = useDebouncedValue(query.trim(), 300);
  return useQuery({
    queryKey: ["recipeSearchExternal", debounced],
    queryFn: async () => {
      const res = await api.get<RecipeSearchApiResponse>("/api/recipes/search-external", {
        params: { query: debounced },
      });
      return res.data;
    },
    enabled: debounced.length >= 2,
  });
}

export type RecipeImportIngredient = { name: string; quantity: number; unit: string; optional: boolean };
export type RecipeImportCandidate = {
  name: string;
  instructions: string | null;
  servings: number;
  ingredients: RecipeImportIngredient[];
  create_missing_items: boolean;
  /** The provider's picture of the finished dish, carried through the import. */
  image_url?: string | null;
  external_source: { source: string; id: string; url: string | null } | null;
};
export type RecipeImportDetailApiResponse = { recipe: RecipeImportCandidate; image_url: string | null };

export function useExternalRecipeDetail() {
  return useMutation({
    mutationFn: async (hit: { source: string; external_id: string }) => {
      const res = await api.get<RecipeImportDetailApiResponse>(
        `/api/recipes/search-external/${hit.source}/${encodeURIComponent(hit.external_id)}`
      );
      return res.data;
    },
  });
}

export type RecipeValidationLine = {
  index: number;
  name: string | null;
  item_id: string | null;
  resolution: string;
  quantity: number;
  unit: string;
  unit_resolution: string;
  suggestion: string | null;
};
export type RecipeValidationResponse = {
  valid: boolean;
  would_upsert: boolean;
  existing_recipe_id: string | null;
  ingredients: RecipeValidationLine[];
  flagged_units: { index: number; item_name: string | null; unit: string; suggestion: string | null }[];
  would_create_items: string[];
  errors: string[];
};

export function useValidateRecipe() {
  return useMutation({
    mutationFn: async (recipe: RecipeImportCandidate) => {
      const res = await api.post<RecipeValidationResponse>("/api/recipes/validate", recipe);
      return res.data;
    },
  });
}

/** The full one-shot create (name-resolved ingredients, external_source
 * dedup) — distinct from useCreateRecipe's bare name/instructions used by the
 * manual "New Recipe" -> set-ingredients-separately flow. */
export function useImportRecipe() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (recipe: RecipeImportCandidate) => {
      const res = await api.post<Recipe & { created: boolean }>("/api/recipes", recipe);
      return res.data;
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["recipes"] });
      await queryClient.invalidateQueries({ queryKey: ["recipeSuggestions"] });
    },
  });
}

/** A line the cook trimmed to what was actually on the shelf (ADR 0025 §7). */
export type CappedIngredient = {
  ingredient_id: string | null;
  item_name: string;
  wanted: string;
  took: string;
  unit: string;
};

export type RecipeCookResult = {
  cook_event_id: string;
  scale_used?: number | null;
  capped_ingredients?: CappedIngredient[];
};

export type RecipeWithIngredients = {
  recipe: Recipe;
  ingredients: RecipeIngredientInput[];
};

export type RecipeSuggestion = {
  recipe_id: string;
  recipe_name: string;
  available_score: number;
  missing_items: string[];
};

export type RecipeSuggestionResponse = {
  location_id: string;
  suggestions: RecipeSuggestion[];
};

export type IngredientMatchSuggestion = {
  canonical_provenance?: "generated" | "confirmed" | "legacy_unknown";
  canonical_version?: number | null;
  canonical_revision?: number;
  item_id: string;
  item_name: string;
  canonical_ingredient: string | null;
  on_hand: string;
};

export type SubstituteAvailable = {
  to_label: string;
  to_canonical: string | null;
  ratio: string;
  outcome: string;
  on_hand: string;
};

export type SubstitutionSuggestion = {
  substitution_id: string;
  to_label: string;
  to_canonical: string | null;
  ratio: string;
  outcome: string;
  on_hand: string;
};

export type RecipeIngredientAvailability = {
  ingredient_id: string;
  item_id: string;
  item_name: string;
  canonical_ingredient: string | null;
  quantity_required: string;
  unit: string;
  /** 'package' lines are counted, not measured — `unit` reads "package" and
   *  on_hand is a count of whole packages (ADR 0025 §5). */
  quantity_mode?: "amount" | "package" | null;
  on_hand: string;
  // ADR 0017: the item's base unit and on-hand rendered in the recipe's own unit
  // for display; null when there's no bridge to convert.
  base_unit: string | null;
  on_hand_recipe_unit: string | null;
  status: "have" | "substitute" | "suggested" | "missing" | "cant_convert" | "optional";
  available_via: SubstituteAvailable | null;
  suggestions: IngredientMatchSuggestion[];
  substitution_suggestions: SubstitutionSuggestion[];
};

export type RecipeAvailabilityResponse = {
  recipe_id: string;
  ingredients: RecipeIngredientAvailability[];
};

export type RecipeParsedIngredient = {
  source_line_id?: string | null;
  raw_line: string;
  name: string;
  quantity: number;
  unit: string;
  item_id?: string | null;
  resolution?: string | null;
  optional?: boolean;
  yield_basis?: boolean | null;
  quantity_mode?: "amount" | "package" | null;
  unit_resolution?: string | null;
  suggestion?: string | null;
};

export type RecipeParseApiResponse = {
  title: string;
  instructions: string;
  ingredients: RecipeParsedIngredient[];
  raw_text: string;
  source?: string;
  source_filename?: string | null;
  source_lines?: RecipeSourceLine[];
  structured_source?: RecipeImportCandidate;
  servings?: number;
};

export type RecipeSourceLine = {
  source_line_id: string;
  raw_line: string;
  line_index: number | null;
  start: number | null;
  end: number | null;
  field_path: string | null;
  role: string;
  warnings: string[];
};
export type RecipeImportSource = {
  kind: "text" | "document" | "structured";
  raw_text?: string;
  structured?: RecipeImportCandidate;
  filename?: string | null;
};
export type RecipeImportReview = {
  version: string;
  binding: string;
  warning: string;
  lines: RecipeSourceLine[];
  status: string;
  provider_requests: number;
  annotations: { source_line_id: string; role: string | null; score: number | null; below_threshold: boolean; threshold: number; status: string }[];
};

// ============ Queries ============

export function useRecipes() {
  return useQuery({
    queryKey: ["recipes"],
    queryFn: async () => {
      const res = await api.get<Recipe[]>("/api/recipes");
      return res.data;
    },
  });
}

/**
 * Recipe suggestions. `null` means "everywhere" — stock pooled across all
 * locations, which is the sane default for a household: the ingredients for one
 * meal are rarely all in the same cupboard.
 */
export function useRecipeSuggestions(locationId: string | null) {
  return useQuery({
    queryKey: ["recipeSuggestions", locationId ?? "all"],
    queryFn: async () => {
      const url = locationId ? `/api/recipes/suggestions/${locationId}` : "/api/recipes/suggestions";
      const res = await api.get<RecipeSuggestionResponse>(url);
      return res.data.suggestions;
    },
  });
}

/**
 * Per-ingredient availability for one recipe (ADR 0015 Phase 2): each ingredient
 * is have / suggested / missing, with tight-subset products offered to confirm.
 * `null` locationId pools across every location. Enabled only when opened.
 */
export function useRecipeAvailability(
  recipeId: string | null,
  locationId: string | null,
  /** Scale have/short to an absolute serving count (ADR 0022 §7). */
  servingsMade?: number | null
) {
  return useQuery({
    queryKey: ["recipeAvailability", recipeId ?? "", locationId ?? "all", servingsMade ?? "asWritten"],
    enabled: Boolean(recipeId),
    queryFn: async () => {
      const params = new URLSearchParams();
      if (locationId) params.set("location_id", locationId);
      if (servingsMade != null) params.set("servings_made", String(servingsMade));
      const query = params.toString();
      const res = await api.get<RecipeAvailabilityResponse>(
        `/api/recipes/${recipeId}/availability${query ? `?${query}` : ""}`
      );
      return res.data.ingredients;
    },
  });
}

/** Confirm an on-hand product IS a recipe ingredient — learns the alias so it
 *  identity-matches everywhere from now on (ADR 0015 §5). */
export type PendingIngredientMatch = {
  recipe_id: string;
  ingredient_id: string;
  item_id: string;
  item_name: string;
  canonical_ingredient: string | null;
  recipe_names: string[];
  candidates: Omit<IngredientMatchSuggestion, "on_hand">[];
};

/** Ingredients that are probably already on the shelf under a different product
 *  name — answered once each, household-wide, rather than per recipe. */
export function usePendingIngredientMatches() {
  return useQuery({
    queryKey: ["ingredientMatches"],
    queryFn: async () =>
      (await api.get<{ matches: PendingIngredientMatch[] }>("/api/recipes/ingredient-matches")).data.matches,
  });
}

export function useConfirmIngredientItem() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (params: {
      ingredientId: string;
      itemId: string;
      expectedCanonical: string | null;
      expectedRevision?: number;
      expectedIngredient: string | null;
      replaceConfirmed: boolean;
    }) => {
      await api.post(`/api/recipes/ingredients/${params.ingredientId}/confirm-item`, {
        item_id: params.itemId,
        expected_canonical_ingredient: params.expectedCanonical,
        expected_canonical_revision: params.expectedRevision,
        expected_ingredient_key: params.expectedIngredient,
        replace_confirmed: params.replaceConfirmed,
      });
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["recipeAvailability"] });
      await queryClient.invalidateQueries({ queryKey: ["recipeSuggestions"] });
      await queryClient.invalidateQueries({ queryKey: ["ingredientMatches"] });
      await queryClient.invalidateQueries({ queryKey: ["items"] });
    },
  });
}

/** Approve a suggested substitution (ADR 0015 §5) — it then counts toward
 *  availability everywhere and is offered automatically. */
export function useApproveSubstitution() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (substitutionId: string) => {
      await api.post(`/api/recipes/substitutions/${substitutionId}/approve`);
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["recipeAvailability"] });
      await queryClient.invalidateQueries({ queryKey: ["recipeSuggestions"] });
    },
  });
}

export function useDeleteRecipe() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (recipeId: string) => {
      await api.delete(`/api/recipes/${recipeId}`);
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["recipes"] });
      await queryClient.invalidateQueries({ queryKey: ["recipeSuggestions"] });
    },
  });
}

// ============ Mutations ============

export function useParseRecipeText() {
  return useMutation({
    mutationFn: async (rawText: string) => {
      const res = await api.post<RecipeParseApiResponse>("/api/recipes/parse-text", { raw_text: rawText });
      return res.data;
    },
  });
}

export function useParseRecipeImage() {
  return useMutation({
    mutationFn: async (file: File) => {
      const form = new FormData();
      form.append("file", file);
      const res = await api.post<RecipeParseApiResponse>("/api/recipes/parse-image", form, {
        headers: { "Content-Type": "multipart/form-data" },
      });
      return res.data;
    },
  });
}

export function useIngestParsedRecipe() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (params: {
      title: string;
      instructions: string | null;
      ingredients: { raw_line: string; name: string; quantity: number; unit: string }[];
      source: string;
      source_filename: string | null;
      source_text: string | null;
      create_missing_items: boolean;
    }) => {
      const res = await api.post<{ recipe_id: string; created_item_ids: string[] }>(
        "/api/recipes/ingest-parsed",
        params
      );
      return res.data;
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["recipes"] });
      await queryClient.invalidateQueries({ queryKey: ["recipeSuggestions"] });
    },
  });
}

export function useCreateRecipe() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (params: { name: string; instructions: string | null }) => {
      const res = await api.post<Recipe>("/api/recipes", params);
      return res.data;
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["recipes"] });
    },
  });
}

export function useSaveRecipeIngredients() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (params: { recipeId: string; ingredients: RecipeIngredientInput[] }) => {
      const { recipeId, ingredients } = params;
      await api.put(`/api/recipes/${recipeId}/ingredients`, ingredients);
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["recipes"] });
      await queryClient.invalidateQueries({ queryKey: ["recipeSuggestions"] });
    },
  });
}

export function useCookRecipe() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (params: {
      recipeId: string;
      /** null = draw ingredients first-expiring-first across every location. */
      locationId: string | null;
      /** BATCH MULTIPLIER — 2 = "two whole recipes". Not a serving count. */
      servings: number;
      leftoverServings: number;
      note?: string | null;
      /** ABSOLUTE servings to make; scales by servings_made / recipe.servings.
       *  When set the server ignores `servings` (ADR 0022 §1). */
      servingsMade?: number | null;
      /** Per-line amounts for THIS cook. `quantity` is the FINAL amount —
       *  scaling is not applied on top of it (ADR 0022 §2). */
      ingredientOverrides?: {
        ingredient_id: string;
        quantity?: number | null;
        unit?: string | null;
        omit?: boolean;
      }[];
      /** What the finished dish WEIGHED. Replaces the recipe's declared yield
       *  rather than scaling it (ADR 0025 §3) — a scale reading is a
       *  measurement, `output_quantity` only a default. */
      outputWeightGrams?: number | null;
      /** How the yield was packed: N containers, optionally of an exact size
       *  with the remainder left loose (ADR 0025 §4). Opt-in per cook — there is
       *  no recipe-level default, so sending nothing means one plain lot. */
      outputPortions?: number | null;
      outputPortionSize?: number | null;
      outputPortionUnit?: string | null;
      /** Size the whole batch from what's on hand for ONE line — "use all the
       *  pork I have" (ADR 0025 §5). Wins over servings/servingsMade. */
      scaleToIngredientId?: string | null;
      /** Let a short line be capped at what's on hand instead of failing the
       *  cook (ADR 0025 §7). Changes the dish, so the response names every line
       *  that was trimmed. */
      allowShortIngredients?: boolean;
    }) => {
      const { recipeId, locationId, servings, leftoverServings, note } = params;
      const res = await api.post<RecipeCookResult>(`/api/recipes/${recipeId}/cook`, {
        location_id: locationId,
        servings,
        leftover_servings: leftoverServings,
        note,
        ...(params.servingsMade != null ? { servings_made: params.servingsMade } : {}),
        ...(params.ingredientOverrides?.length ? { ingredient_overrides: params.ingredientOverrides } : {}),
        // Omitted rather than sent as null: the server distinguishes "not
        // measured" from a value, and no portioning is a different request
        // from a null portion count.
        ...(params.outputWeightGrams != null ? { output_weight_grams: params.outputWeightGrams } : {}),
        ...(params.outputPortions != null ? { output_portions: params.outputPortions } : {}),
        ...(params.outputPortionSize != null ? { output_portion_size: params.outputPortionSize } : {}),
        ...(params.outputPortionUnit ? { output_portion_unit: params.outputPortionUnit } : {}),
        ...(params.scaleToIngredientId ? { scale_to_ingredient_id: params.scaleToIngredientId } : {}),
        ...(params.allowShortIngredients ? { allow_short_ingredients: true } : {}),
      });
      return res.data;
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["inventory"] });
      await queryClient.invalidateQueries({ queryKey: ["transactions"] });
      await queryClient.invalidateQueries({ queryKey: ["consumptionTrends"] });
      await queryClient.invalidateQueries({ queryKey: ["restockSuggestions"] });
      await queryClient.invalidateQueries({ queryKey: ["expiringItems"] });
      await queryClient.invalidateQueries({ queryKey: ["recipeSuggestions"] });
    },
  });
}

export function useUpdateRecipe() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (params: { recipeId: string; name: string; instructions: string | null }) => {
      const { recipeId, name, instructions } = params;
      const res = await api.put<{ id: string }>(`/api/recipes/${recipeId}`, { name, instructions });
      return res.data;
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["recipes"] });
      await queryClient.invalidateQueries({ queryKey: ["recipe", ""] });
    },
  });
}

/** Attach (or replace) a recipe's photo.
 *
 * Sent as multipart rather than a base64 body: the backend re-encodes, shrinks
 * and strips EXIF anyway, so there is nothing to gain from inflating the
 * payload by a third on the way up.
 */
export function useUploadRecipeImage(recipeId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (file: File) => {
      const body = new FormData();
      body.append("file", file);
      const res = await api.post(`/api/recipes/${recipeId}/image`, body);
      return res.data as Recipe;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["recipe", recipeId] });
      queryClient.invalidateQueries({ queryKey: ["recipes"] });
    },
  });
}

export function useDeleteRecipeImage(recipeId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async () => {
      await api.delete(`/api/recipes/${recipeId}/image`);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["recipe", recipeId] });
      queryClient.invalidateQueries({ queryKey: ["recipes"] });
    },
  });
}
