import { useQuery } from "@tanstack/react-query";
import { api } from "@/api/client";

export type EvidenceFacts = {
  identity?: string | null;
  base_ingredient?: string | null;
  form?: string | null;
  preparation?: string | null;
  composition?: string | null;
  recipe_role?: string | null;
};
export type ProductFacts = Omit<EvidenceFacts, "recipe_role">;
export type ProductEvidence = {
  item_id: string;
  revision: number;
  base_source_hash: string;
  saved_base_source_hash: string | null;
  facts: ProductFacts;
  stale: boolean;
  updated_by: string | null;
  updated_at: string | null;
  provenance: "user_provided";
};
export type MatchItemEvidence = {
  item_id: string;
  name: string;
  brand: string | null;
  ingredients_text: string | null;
  source: string;
  canonical_ingredient: string | null;
  canonical_provenance: string;
  canonical_version: number | null;
  canonical_revision: number;
  nutrition_basis: "estimated" | "label_or_manual" | "unknown";
  nutrition_source: string | null;
  nutrition_estimated: boolean;
  nutrition_values: Record<string, string | null>;
  source_fingerprint: string;
  base_source_fingerprint?: string | null;
  structured_evidence?: ProductEvidence | null;
};
export type MatchReview = {
  id: string;
  recipe_id: string;
  ingredient_id: string;
  candidate_item_id: string;
  revision: number;
  scope_revision: number;
  status: "pending" | "evaluating" | "confirmed" | "rejected" | "interrupted";
  evidence_hash: string;
  expires_at: string;
  packet: {
    product: MatchItemEvidence;
    requested: MatchItemEvidence;
    recipe_name: string;
    recipe_instructions: string | null;
    recipe_ingredient_line: string;
    quantity: string;
    unit: string;
    facts: EvidenceFacts;
    resolved: EvidenceFacts;
    fact_sources: Record<
      string,
      "user_provided" | "saved_user_provided" | "catalog_name" | "catalog_ingredients" | "recipe_context" | "unknown"
    >;
  };
  next_clarification?: {
    field: "identity" | "preparation" | "form";
    question: string;
    reason_code: "missing_identity_evidence";
  } | null;
  missing_fields: string[];
  reason_codes: string[];
  can_confirm: boolean;
  can_compare: boolean;
  readiness: "needs_information" | "manual_review" | "possible_match" | "not_recommended" | "confirmed" | "interrupted";
  stale: boolean;
  prediction_stale: boolean;
  confirmation_required: boolean;
  predictions: {
    purpose: "identification" | "recipe_matching" | "adjustment";
    choice: string;
    label: string;
    probability: number;
    provider_confidence: number;
    margin: number;
    clears_threshold: boolean;
  }[];
  policy: {
    confidence: number;
    margin: number;
    enabled: boolean;
    version: string;
    config_revision: string;
    source_version: string;
    provider: string;
    model: string;
    execution_location: string;
  };
  provider_requests: number;
  max_provider_requests: number;
  current_choice: boolean;
  confirmed_at: string | null;
  selected_source_item_id: string | null;
  confirmation_token?: string | null;
};
export type MatchCandidateList = {
  requested: MatchItemEvidence;
  candidates: { item: MatchItemEvidence; retrieval_reason: string }[];
};
export type ReviewBinding = { revision: number; evidence_hash: string; confirmation_token: string };
const scope = (recipe: string, ingredient: string) => `/api/recipes/${recipe}/ingredients/${ingredient}`;
export function useMatchCandidates(
  recipe: string | null,
  ingredient: string | null,
  query: string,
  enabled: boolean,
  actorId?: string
) {
  return useQuery({
    queryKey: ["ingredientMatchCandidates", actorId, recipe, ingredient, query],
    enabled: enabled && Boolean(recipe && ingredient),
    retry: false,
    queryFn: async () =>
      (await api.get<MatchCandidateList>(`${scope(recipe!, ingredient!)}/match-candidates`, { params: { q: query } }))
        .data,
  });
}
export function useMatchReviewHistory(
  recipe: string | null,
  ingredient: string | null,
  enabled: boolean,
  actorId?: string
) {
  return useQuery({
    queryKey: ["ingredientMatchReviews", actorId, recipe, ingredient],
    enabled: enabled && Boolean(recipe && ingredient),
    retry: false,
    queryFn: async () =>
      (await api.get<{ reviews: MatchReview[] }>(`${scope(recipe!, ingredient!)}/match-reviews`)).data.reviews,
  });
}
export async function createMatchReview(
  recipe: string,
  ingredient: string,
  candidate: string,
  facts: EvidenceFacts,
  signal?: AbortSignal
) {
  return (
    await api.post<MatchReview>(
      `${scope(recipe, ingredient)}/match-reviews`,
      { candidate_item_id: candidate, facts },
      { signal }
    )
  ).data;
}
export async function actOnMatchReview(
  id: string,
  action: "compare" | "confirm" | "reject",
  binding: ReviewBinding,
  signal?: AbortSignal
) {
  return (
    await api.post<MatchReview>(
      `/api/ingredient-match-reviews/${id}/${action}`,
      { ...binding, ...(action === "confirm" ? { confirm: true } : {}) },
      { signal }
    )
  ).data;
}

export async function readProductEvidence(itemId: string, signal?: AbortSignal) {
  return (await api.get<ProductEvidence>(`/api/items/${itemId}/structured-evidence`, { signal })).data;
}
export async function saveProductEvidence(
  itemId: string,
  evidence: ProductEvidence,
  facts: ProductFacts,
  signal?: AbortSignal
) {
  return (
    await api.put<ProductEvidence>(
      `/api/items/${itemId}/structured-evidence`,
      { confirm: true, expected_revision: evidence.revision, base_source_hash: evidence.base_source_hash, facts },
      { signal }
    )
  ).data;
}
