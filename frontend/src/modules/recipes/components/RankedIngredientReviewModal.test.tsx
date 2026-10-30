import { act, cleanup, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useState } from "react";
import { renderWithProviders } from "@/test-utils";
import type { RankedCandidate, RankedIdentityEvidence, RankedReview } from "../hooks/ranked-ingredient-hooks";
import { RankedIngredientReviewModal } from "./RankedIngredientReviewModal";

const post = vi.fn();
const get = vi.fn();
let admin = true;
let actor = "admin";
vi.mock("@/api/client", () => ({
  api: { post: (...args: unknown[]) => post(...args), get: (...args: unknown[]) => get(...args) },
}));
vi.mock("@/state/AuthProvider", () => ({ useAuth: () => ({ isAdmin: admin, account: { id: actor } }) }));
const acknowledgment =
  "I checked the evidence: these are the same ingredient, and I want this permanent household-wide identity change";
const evidence: RankedIdentityEvidence = {
  brand: null,
  ingredients_text: "Chickpeas, water, salt.",
  source: "manual",
  current_facts: {
    identity: "chickpeas",
    base_ingredient: "chickpeas",
    form: "whole beans",
    preparation: "canned",
    composition: null,
  },
  facts_provenance: "saved_user_provided",
  saved_facts_stale: false,
  truncated: false,
  nutrition_basis: "unknown",
  nutrition_source: null,
};
const candidate: RankedCandidate = {
  candidate_item_id: "chickpeas",
  product_name: "Canned chickpeas",
  requested_name: "garbanzo beans",
  canonical_ingredient: "chickpeas",
  canonical_provenance: "generated",
  canonical_version: 2,
  canonical_revision: 4,
  retrieval_reason: "culinary_synonym",
  label: "same_ingredient",
  decision_provenance: "classifier",
  probability: 0.84,
  provider_confidence: 0.8,
  margin: 0.6,
  clears_threshold: false,
  reason_codes: ["same_base_ingredient"],
  can_confirm: true,
  product_evidence: evidence,
};
function fixture(overrides: Partial<RankedReview> = {}): RankedReview {
  return {
    id: "ranked",
    recipe_id: "recipe",
    ingredient_id: "ingredient",
    requested_name: "garbanzo beans",
    requested_canonical_ingredient: "garbanzo beans",
    requested_evidence: { ...evidence, ingredients_text: null, current_facts: {}, facts_provenance: "unknown" },
    revision: 0,
    status: "pending",
    evidence_hash: "a".repeat(64),
    expires_at: "2099-01-01T00:00:00Z",
    confirmation_token: "original-evaluation-token",
    results: [candidate],
    reason_codes: [],
    stale: false,
    ambiguous_tie: false,
    provider_requests: 1,
    max_provider_requests: 3,
    policy: { confidence: 0.9 },
    confirmation_required: true,
    alias_scope: "permanent_household_product_identity",
    ...overrides,
  };
}
let response: RankedReview;
beforeEach(() => {
  admin = true;
  actor = "admin";
  response = fixture();
  get.mockReset();
  post.mockReset();
  post.mockImplementation(async (url: string) => ({
    data: url.endsWith("evaluate")
      ? response
      : fixture({ status: url.endsWith("confirm") ? "confirmed" : "rejected", confirmation_token: null, results: [] }),
  }));
});
afterEach(cleanup);
function setup(props: Partial<Parameters<typeof RankedIngredientReviewModal>[0]> = {}) {
  const onClose = vi.fn();
  const onRecipeReview = vi.fn();
  let update!: (next: typeof props) => void;
  function Host() {
    const [overrides, setOverrides] = useState(props);
    update = setOverrides;
    return (
      <RankedIngredientReviewModal
        recipeId="recipe"
        ingredientId="ingredient"
        onClose={onClose}
        onRecipeReview={onRecipeReview}
        {...overrides}
      />
    );
  }
  const rendered = renderWithProviders(<Host />);
  return {
    user: userEvent.setup(),
    onClose,
    onRecipeReview,
    update: (next: typeof props) => act(() => update(next)),
    ...rendered,
  };
}
async function evaluate() {
  const result = setup();
  await result.user.click(screen.getByRole("button", { name: "Evaluate identity candidates" }));
  await screen.findByText("Requested ingredient: garbanzo beans");
  return result;
}
async function openConfirmation(user: ReturnType<typeof userEvent.setup>, name = "Canned chickpeas") {
  await user.click(screen.getByRole("button", { name: `Select ${name}` }));
  await user.click(screen.getByRole("button", { name: "Review global identity change" }));
  await screen.findByLabelText(acknowledgment);
}

describe("Ranked ingredient identity review", () => {
  it("opens without inference or reads and keeps the manual workflow available", () => {
    setup();
    expect(post).not.toHaveBeenCalled();
    expect(get).not.toHaveBeenCalled();
    expect(screen.getByText(/at most three serial classifier requests/)).toBeVisible();
    expect(screen.getByRole("button", { name: "Return to manual matching" })).toBeEnabled();
  });
  it("requires explicit evaluation, selection and unchecked global acknowledgement even below threshold", async () => {
    const { user } = await evaluate();
    expect(post).toHaveBeenCalledWith(
      "/api/recipes/ingredient-matches/evaluate",
      { recipe_id: "recipe", ingredient_id: "ingredient" },
      expect.anything()
    );
    expect(screen.getByText(/Below threshold — needs your review/)).toBeVisible();
    expect(screen.getByText("Identity source: Suggested from name.")).toBeVisible();
    expect(screen.getByRole("button", { name: "Review global identity change" })).toBeDisabled();
    await openConfirmation(user);
    expect(screen.getByLabelText(acknowledgment)).not.toBeChecked();
    expect(screen.getByRole("button", { name: "Confirm selected global identity" })).toBeDisabled();
    await user.click(screen.getByLabelText(acknowledgment));
    await user.click(screen.getByRole("button", { name: "Confirm selected global identity" }));
    await screen.findByText(/Confirmed Canned chickpeas as garbanzo beans for every recipe in your household/);
    expect(post).toHaveBeenLastCalledWith(
      "/api/recipes/ingredient-matches/reviews/ranked/confirm",
      {
        revision: 0,
        evidence_hash: "a".repeat(64),
        confirmation_token: "original-evaluation-token",
        candidate_item_id: "chickpeas",
        confirm_global_alias: true,
        replace_confirmed: false,
      },
      expect.anything()
    );
    expect(post.mock.calls.some(([url]) => String(url).includes("confirm-item"))).toBe(false);
  });
  it("shows recorded product evidence and unknown facts before selection and in final confirmation", async () => {
    const { user } = await evaluate();
    expect(screen.getByText("Recorded ingredients: Chickpeas, water, salt.")).toBeVisible();
    expect(screen.getByText("Preparation: canned")).toBeVisible();
    expect(screen.getByText("Product details: Saved by a user.")).toBeVisible();
    expect(screen.getByText("Preparation: Unknown")).toBeVisible();
    await openConfirmation(user);
    const dialog = within(screen.getByRole("dialog", { name: "Confirm ranked global identity" }));
    await waitFor(() => expect(dialog.getByText("Canned chickpeas → garbanzo beans")).toBeVisible());
    expect(dialog.getByText("Recorded ingredients: Chickpeas, water, salt.")).toBeVisible();
    expect(dialog.getByText("Preparation: canned")).toBeVisible();
    expect(dialog.getByText("Preparation: Unknown")).toBeVisible();
    expect(dialog.queryByText(/version 2; revision 4/)).not.toBeInTheDocument();
  });
  it.each(["requested", "product"] as const)("blocks alias confirmation for truncated %s evidence", async (side) => {
    response =
      side === "requested"
        ? fixture({ requested_evidence: { ...evidence, truncated: true } })
        : fixture({ results: [{ ...candidate, product_evidence: { ...evidence, truncated: true } }] });
    const { user } = await evaluate();
    expect(screen.getByText(/Some evidence was shortened/)).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Select Canned chickpeas" }));
    expect(screen.getByRole("button", { name: "Review global identity change" })).toBeDisabled();
    expect(post).toHaveBeenCalledTimes(1);
  });
  it("shows excluded historical product facts as unknown while honoring a fresh server-confirmable result", async () => {
    response = fixture({ results: [{ ...candidate, product_evidence: { ...evidence, saved_facts_stale: true } }] });
    const { user } = await evaluate();
    expect(screen.getByText(/Saved product details are out of date and were not used/)).toBeVisible();
    expect(screen.queryByText("Preparation: canned")).not.toBeInTheDocument();
    await openConfirmation(user);
    expect(screen.getByRole("button", { name: "Confirm selected global identity" })).toBeDisabled();
    await user.click(screen.getByLabelText(acknowledgment));
    expect(screen.getByRole("button", { name: "Confirm selected global identity" })).toBeEnabled();
  });
  it("shows a consumed confirmation receipt without stale-pending warnings after the alias changes source", async () => {
    const { user } = await evaluate();
    await openConfirmation(user);
    await user.click(screen.getByLabelText(acknowledgment));
    post.mockResolvedValueOnce({
      data: fixture({ status: "confirmed", stale: true, results: [], confirmation_token: null }),
    });
    await user.click(screen.getByRole("button", { name: "Confirm selected global identity" }));
    await screen.findByText(/Confirmed Canned chickpeas as garbanzo beans for every recipe in your household/);
    expect(screen.queryByText(/This evidence is out of date/)).not.toBeInTheDocument();
    expect(screen.queryByText(/No candidates were returned/)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Review global identity change" })).not.toBeInTheDocument();
    expect(post).toHaveBeenCalledTimes(2);
  });
  it("requires human selection and acknowledgement even for a high score", async () => {
    response = fixture({ results: [{ ...candidate, probability: 0.99, clears_threshold: true }] });
    const { user } = await evaluate();
    expect(screen.getByText(/Meets the configured threshold; still needs confirmation/)).toBeVisible();
    expect(screen.getByText("Classifier suggestion — human verification required")).toBeVisible();
    expect(screen.getByText(/A confidence score is not validated accuracy/)).toBeVisible();
    expect(screen.getByRole("button", { name: "Review global identity change" })).toBeDisabled();
    await openConfirmation(user);
    expect(screen.getByRole("button", { name: "Confirm selected global identity" })).toBeDisabled();
    expect(post).toHaveBeenCalledTimes(1);
  });
  it("does not reuse a stale evaluation token even if the response has a same-ingredient result", async () => {
    response = fixture({ stale: true });
    await evaluate();
    expect(screen.getByText(/This evidence is out of date/)).toBeVisible();
    expect(screen.getByRole("button", { name: "Select Canned chickpeas" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Review global identity change" })).toBeDisabled();
    expect(post).toHaveBeenCalledTimes(1);
  });
  it("requires a separate replacement acknowledgement for an already confirmed identity", async () => {
    response = fixture({ results: [{ ...candidate, canonical_provenance: "confirmed" }] });
    const { user } = await evaluate();
    await openConfirmation(user);
    await user.click(screen.getByLabelText(acknowledgment));
    expect(screen.getByRole("button", { name: "Confirm selected global identity" })).toBeDisabled();
    await user.click(screen.getByLabelText("Replace the previously confirmed identity"));
    await user.click(screen.getByRole("button", { name: "Confirm selected global identity" }));
    await waitFor(() =>
      expect(post).toHaveBeenLastCalledWith(
        expect.stringContaining("/confirm"),
        expect.objectContaining({ replace_confirmed: true }),
        expect.anything()
      )
    );
  });
  it.each(["possible_substitution", "different_ingredient", "insufficient_evidence"] as const)(
    "never offers global confirmation for %s",
    async (label) => {
      response = fixture({ results: [{ ...candidate, label, can_confirm: false }] });
      const { user, onRecipeReview } = await evaluate();
      await user.click(screen.getByRole("button", { name: "Select Canned chickpeas" }));
      expect(screen.getByRole("button", { name: "Review global identity change" })).toBeDisabled();
      if (label === "possible_substitution") {
        await user.click(screen.getByRole("button", { name: "Review substitution for this recipe" }));
        expect(onRecipeReview).toHaveBeenCalledOnce();
      }
      expect(post).toHaveBeenCalledTimes(1);
    }
  );
  it("shows ties without choosing a winner and resets acknowledgements when selection changes", async () => {
    response = fixture({
      ambiguous_tie: true,
      results: [candidate, { ...candidate, candidate_item_id: "other", product_name: "Other chickpeas" }],
    });
    const { user } = await evaluate();
    expect(screen.getByText(/Candidates are tied/)).toBeVisible();
    await openConfirmation(user);
    await user.click(screen.getByLabelText(acknowledgment));
    await user.click(screen.getByRole("button", { name: "Cancel ranked identity change" }));
    await openConfirmation(user, "Other chickpeas");
    expect(screen.getByLabelText(acknowledgment)).not.toBeChecked();
    expect(post).toHaveBeenCalledTimes(1);
  });
  it("dismisses with the original capability and performs no alias confirmation", async () => {
    const { user } = await evaluate();
    await user.click(screen.getByRole("button", { name: "Dismiss ranked review" }));
    await screen.findByText(/Review dismissed. No ingredient identity was changed/);
    expect(post).toHaveBeenLastCalledWith(
      "/api/recipes/ingredient-matches/reviews/ranked/reject",
      {
        revision: 0,
        evidence_hash: "a".repeat(64),
        confirmation_token: "original-evaluation-token",
      },
      expect.anything()
    );
    expect(screen.queryByRole("button", { name: "Select Canned chickpeas" })).not.toBeInTheDocument();
  });
  it("discards stale bindings after409 without automatic reevaluation", async () => {
    const { user } = await evaluate();
    await openConfirmation(user);
    await user.click(screen.getByLabelText(acknowledgment));
    post.mockRejectedValueOnce({ response: { status: 409 } });
    await user.click(screen.getByRole("button", { name: "Confirm selected global identity" }));
    await screen.findByText(/Details, stock or settings changed/);
    expect(post).toHaveBeenCalledTimes(2);
    expect(screen.queryByRole("button", { name: "Select Canned chickpeas" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Return to manual matching" })).toBeEnabled();
  });
  it("shows disabled-provider abstentions and never creates confirmation authority", async () => {
    response = fixture({
      reason_codes: ["review_disabled"],
      results: [
        {
          ...candidate,
          label: "insufficient_evidence",
          decision_provenance: "abstention",
          probability: null,
          can_confirm: false,
        },
      ],
    });
    const { user } = await evaluate();
    expect(screen.getByText(/Review notes: review disabled/)).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Select Canned chickpeas" }));
    expect(screen.getByRole("button", { name: "Review global identity change" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Return to manual matching" })).toBeEnabled();
  });
  it("keeps manual matching available after a provider request fails, with no retry", async () => {
    post.mockRejectedValueOnce(new Error("unavailable"));
    const { user } = setup();
    await user.click(screen.getByRole("button", { name: "Evaluate identity candidates" }));
    await screen.findByText(/Review could not be completed/);
    expect(post).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("button", { name: "Return to manual matching" })).toBeEnabled();
  });
  it.each(["close", "actor", "scope"] as const)(
    "ignores an evaluation's late response after %s changes",
    async (change) => {
      let finish!: (value: { data: RankedReview }) => void;
      post.mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            finish = resolve;
          })
      );
      const { user, update } = setup();
      await user.click(screen.getByRole("button", { name: "Evaluate identity candidates" }));
      const signal = post.mock.calls[0][2].signal as AbortSignal;
      if (change === "close") await user.click(screen.getByRole("button", { name: "Close ranked review" }));
      else {
        if (change === "actor") actor = "another-admin";
        update({ ingredientId: change === "scope" ? "other-ingredient" : "ingredient" });
      }
      expect(signal.aborted).toBe(true);
      await act(async () => finish({ data: fixture() }));
      expect(screen.queryByText("Requested ingredient: garbanzo beans")).not.toBeInTheDocument();
      expect(post).toHaveBeenCalledTimes(1);
    }
  );
  it("requires an admin and starts no evaluation for ordinary users", () => {
    admin = false;
    setup();
    expect(screen.getByText(/Sign in as an admin/)).toBeVisible();
    expect(screen.queryByRole("button", { name: "Evaluate identity candidates" })).not.toBeInTheDocument();
    expect(post).not.toHaveBeenCalled();
  });
});
