import { api } from "@/api/client";
import type { ProductFacts } from "./ingredient-match-hooks";

export type RankedIdentityEvidence = {
  brand: string | null;
  ingredients_text: string | null;
  source: string;
  current_facts: ProductFacts;
  facts_provenance: "saved_user_provided" | "unknown";
  saved_facts_stale: boolean;
  truncated: boolean;
  nutrition_basis: "estimated" | "label_or_manual" | "unknown";
  nutrition_source: string | null;
};

export type RankedCandidate = {
  candidate_item_id: string;
  product_name: string;
  requested_name: string;
  canonical_ingredient: string | null;
  canonical_provenance: string;
  canonical_version: number | null;
  canonical_revision: number;
  retrieval_reason: string;
  label: "same_ingredient" | "possible_substitution" | "different_ingredient" | "insufficient_evidence";
  decision_provenance: "same_item" | "confirmed_identity" | "deterministic_guard" | "classifier" | "abstention";
  probability: number | null;
  provider_confidence: number | null;
  margin: number | null;
  clears_threshold: boolean;
  reason_codes: string[];
  can_confirm: boolean;
  product_evidence: RankedIdentityEvidence;
};
export type RankedReview = {
  id: string;
  recipe_id: string;
  ingredient_id: string;
  requested_name: string;
  requested_canonical_ingredient: string | null;
  requested_evidence: RankedIdentityEvidence;
  revision: number;
  status: "evaluating" | "pending" | "confirmed" | "rejected" | "interrupted";
  evidence_hash: string;
  expires_at: string;
  confirmation_token: string | null;
  results: RankedCandidate[];
  reason_codes: string[];
  stale: boolean;
  ambiguous_tie: boolean;
  provider_requests: number;
  max_provider_requests: 3;
  policy: Record<string, unknown>;
  confirmation_required: true;
  alias_scope: "permanent_household_product_identity";
};
export type RankedBinding = { revision: number; evidence_hash: string; confirmation_token: string };
const endpoint = "/api/recipes/ingredient-matches";
export async function evaluateRankedIngredients(recipeId: string, ingredientId: string, signal: AbortSignal) {
  return (
    await api.post<RankedReview>(
      `${endpoint}/evaluate`,
      { recipe_id: recipeId, ingredient_id: ingredientId },
      { signal }
    )
  ).data;
}
export async function confirmRankedIngredient(
  id: string,
  binding: RankedBinding,
  candidateId: string,
  replaceConfirmed: boolean,
  signal: AbortSignal
) {
  return (
    await api.post<RankedReview>(
      `${endpoint}/reviews/${id}/confirm`,
      {
        ...binding,
        candidate_item_id: candidateId,
        confirm_global_alias: true,
        replace_confirmed: replaceConfirmed,
      },
      { signal }
    )
  ).data;
}
export async function rejectRankedIngredients(id: string, binding: RankedBinding, signal: AbortSignal) {
  return (await api.post<RankedReview>(`${endpoint}/reviews/${id}/reject`, binding, { signal })).data;
}
