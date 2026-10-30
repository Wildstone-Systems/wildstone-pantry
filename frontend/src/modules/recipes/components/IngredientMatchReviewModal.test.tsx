import { act, cleanup, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { renderWithProviders } from "@/test-utils";
import type { MatchItemEvidence, MatchReview, ProductEvidence } from "../hooks/ingredient-match-hooks";
import { IngredientMatchReviewModal } from "./IngredientMatchReviewModal";
const get = vi.fn();
const post = vi.fn();
const put = vi.fn();
let isAdmin = true;
vi.mock("@/api/client", () => ({
  api: {
    get: (...args: unknown[]) => get(...args),
    post: (...args: unknown[]) => post(...args),
    put: (...args: unknown[]) => put(...args),
  },
}));
vi.mock("@/state/AuthProvider", () => ({ useAuth: () => ({ isAdmin }) }));
const metadata: ProductEvidence = {
  item_id: "product",
  revision: 2,
  base_source_hash: "b".repeat(64),
  saved_base_source_hash: "b".repeat(64),
  facts: { identity: "cumin", form: "whole seeds", preparation: "dried" },
  stale: false,
  updated_by: "admin",
  updated_at: "2026-10-26T00:00:00Z",
  provenance: "user_provided",
};
const product: MatchItemEvidence = {
  item_id: "product",
  name: "Whole cumin seeds",
  brand: null,
  ingredients_text: "cumin",
  source: "manual",
  canonical_ingredient: "cumin",
  canonical_provenance: "generated",
  canonical_version: 2,
  canonical_revision: 0,
  nutrition_basis: "estimated",
  nutrition_estimated: true,
  nutrition_source: "Generic reference",
  nutrition_values: { energy_kcal_100g: "375" },
  source_fingerprint: "source",
  base_source_fingerprint: "b".repeat(64),
  structured_evidence: metadata,
};
const requested = { ...product, item_id: "requested", name: "Ground cumin" };
function fixture(overrides: Partial<MatchReview> = {}): MatchReview {
  return {
    id: "review",
    recipe_id: "recipe",
    ingredient_id: "ingredient",
    candidate_item_id: "product",
    revision: 0,
    scope_revision: 0,
    status: "pending",
    evidence_hash: "a".repeat(64),
    expires_at: "2099-01-01T00:00:00Z",
    packet: {
      product,
      requested,
      recipe_name: "Chili",
      recipe_instructions: "Grind the cumin before adding.",
      recipe_ingredient_line: "ground cumin",
      quantity: "1",
      unit: "tsp",
      facts: {},
      resolved: { identity: "cumin", form: "whole seeds", preparation: "dried" },
      fact_sources: { identity: "catalog_name", form: "catalog_name", preparation: "catalog_name" },
    },
    missing_fields: [],
    reason_codes: [],
    can_confirm: true,
    can_compare: true,
    readiness: "manual_review",
    stale: false,
    prediction_stale: false,
    confirmation_required: true,
    predictions: [],
    policy: {
      confidence: 0.9,
      margin: 0.2,
      enabled: true,
      version: "v1",
      config_revision: "config",
      source_version: "source",
      provider: "ollama",
      model: "clef",
      execution_location: "cloud_direct",
    },
    provider_requests: 0,
    max_provider_requests: 2,
    current_choice: false,
    confirmed_at: null,
    selected_source_item_id: null,
    confirmation_token: "original-token-".repeat(3),
    ...overrides,
  };
}
let initial: MatchReview;
let compared: MatchReview;
let saved: MatchReview[];
beforeEach(() => {
  isAdmin = true;
  initial = fixture();
  saved = [];
  compared = fixture({
    revision: 2,
    confirmation_token: null,
    provider_requests: 2,
    can_compare: false,
    readiness: "possible_match",
    predictions: [
      {
        purpose: "recipe_matching",
        choice: "c_2",
        label: "possible",
        probability: 0.72,
        provider_confidence: 0.4,
        margin: 0.5,
        clears_threshold: false,
      },
      {
        purpose: "adjustment",
        choice: "c_2",
        label: "preparation",
        probability: 0.8,
        provider_confidence: 0.5,
        margin: 0.6,
        clears_threshold: false,
      },
    ],
  });
  get.mockReset();
  post.mockReset();
  put.mockReset();
  put.mockResolvedValue({ data: { ...metadata, revision: 3 } });
  get.mockImplementation(async (url: string) => ({
    data: url.endsWith("match-candidates")
      ? { requested, candidates: [{ item: product, retrieval_reason: "token_overlap" }] }
      : { reviews: saved },
  }));
  post.mockImplementation(async (url: string) => {
    if (url.endsWith("/compare")) return { data: compared };
    if (url.endsWith("/confirm")) {
      const result = fixture({
        status: "confirmed",
        can_confirm: false,
        can_compare: false,
        current_choice: true,
        confirmation_token: null,
      });
      saved = [result];
      return { data: result };
    }
    if (url.endsWith("/reject"))
      return { data: fixture({ status: "rejected", can_confirm: false, can_compare: false }) };
    return { data: initial };
  });
});
afterEach(cleanup);
async function openReview() {
  const user = userEvent.setup();
  renderWithProviders(<IngredientMatchReviewModal recipeId="recipe" ingredientId="ingredient" onClose={vi.fn()} />);
  await user.click(await screen.findByRole("button", { name: "Review Whole cumin seeds" }));
  await screen.findByText("Facts used for this review");
  return user;
}
describe("Recipe-scoped evidence review", () => {
  it("loads without provider calls, preserves the create token through comparison, and requires an explicit low-score choice", async () => {
    const user = await openReview();
    expect(post).toHaveBeenCalledTimes(1);
    expect(screen.getByText("Classifier suggestion — human verification required")).toBeVisible();
    expect(screen.getByText(/A confidence score is not validated accuracy/)).toBeVisible();
    expect(screen.getByRole("button", { name: "Use for this recipe" })).toBeDisabled();
    expect(screen.getAllByText(/Estimated reference/)).toHaveLength(2);
    await user.click(screen.getByRole("button", { name: "Compare this item" }));
    await screen.findByText("Recipe match: Possible recipe match");
    expect(screen.getAllByText("Below threshold — needs your review")).toHaveLength(2);
    expect(screen.getByText(/Adjustment type only/)).toBeVisible();
    expect(screen.getByRole("button", { name: "Compare this item" })).toBeDisabled();
    expect(post).toHaveBeenCalledTimes(2);
    await user.click(screen.getByLabelText("I checked the evidence and choose this item only for this recipe"));
    await user.click(screen.getByRole("button", { name: "Use for this recipe" }));
    await waitFor(() =>
      expect(post).toHaveBeenCalledWith(
        "/api/ingredient-match-reviews/review/confirm",
        {
          revision: 2,
          evidence_hash: initial.evidence_hash,
          confirmation_token: initial.confirmation_token,
          confirm: true,
        },
        expect.anything()
      )
    );
    await screen.findByText(/Current recipe choice: Whole cumin seeds/);
    expect(
      post.mock.calls.some(([url]) => String(url).includes("confirm-item") || String(url).includes("substitutions"))
    ).toBe(false);
  });
  it("blocks unknown identity and lets checked product facts create a new snapshot without inventing data", async () => {
    initial = fixture({
      readiness: "needs_information",
      missing_fields: ["identity", "preparation"],
      next_clarification: {
        field: "identity",
        question: "What is the product?",
        reason_code: "missing_identity_evidence",
      },
      can_confirm: false,
      can_compare: false,
    });
    const user = await openReview();
    expect(screen.getByRole("button", { name: "Compare this item" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Use for this recipe" })).toBeDisabled();
    await user.type(screen.getByLabelText("What is the product?"), "Cumin seeds");
    expect(screen.queryByText("Facts used for this review")).not.toBeInTheDocument();
    initial = fixture();
    await user.click(screen.getByRole("button", { name: "Review these details" }));
    await waitFor(() =>
      expect(post).toHaveBeenLastCalledWith(
        "/api/recipes/recipe/ingredients/ingredient/match-reviews",
        { candidate_item_id: "product", facts: { identity: "Cumin seeds" } },
        expect.anything()
      )
    );
    expect(post.mock.calls.some(([url]) => String(url).endsWith("/compare"))).toBe(false);
  });
  it("keeps manual review available with classifier assistance disabled", async () => {
    initial = fixture({ can_compare: false, policy: { ...fixture().policy, enabled: false } });
    const user = await openReview();
    expect(screen.getByText(/Classifier assistance is disabled/)).toBeVisible();
    expect(screen.getByRole("button", { name: "Compare this item" })).toBeDisabled();
    await user.click(screen.getByLabelText("I checked the evidence and choose this item only for this recipe"));
    await user.click(screen.getByRole("button", { name: "Use for this recipe" }));
    await screen.findByText(/Current recipe choice:/);
    expect(post).toHaveBeenCalledTimes(2);
  });
  it("keeps a negative result visibly negative and records a separate deliberate human choice", async () => {
    initial = fixture({
      readiness: "not_recommended",
      can_compare: false,
      reason_codes: ["model_recipe_not_suitable"],
      predictions: [
        {
          purpose: "recipe_matching",
          choice: "c_3",
          label: "not_suitable",
          probability: 0.95,
          provider_confidence: 0.8,
          margin: 0.8,
          clears_threshold: true,
        },
      ],
    });
    const user = await openReview();
    expect(screen.getByText("Recipe match: Not recommended for this recipe")).toBeVisible();
    expect(screen.getByRole("button", { name: "Record my choice for this recipe" })).toBeDisabled();
    await user.click(screen.getByLabelText("I reviewed the conflict and choose this item myself only for this recipe"));
    expect(screen.getByRole("button", { name: "Record my choice for this recipe" })).toBeEnabled();
    expect(post).toHaveBeenCalledTimes(1);
  });
  it("clears stale suggestions and token on409 and requires a fresh manual snapshot", async () => {
    const user = await openReview();
    post.mockRejectedValueOnce({ response: { status: 409 } });
    await user.click(screen.getByRole("button", { name: "Compare this item" }));
    await screen.findByText(/Details or settings changed, or this review expired/);
    expect(screen.queryByRole("button", { name: "Use for this recipe" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Review these details" }));
    await screen.findByRole("button", { name: "Use for this recipe" });
    expect(post).toHaveBeenCalledTimes(3);
  });
  it("leaves a case unresolved without confirming or global identity writes", async () => {
    const user = await openReview();
    await user.click(screen.getByRole("button", { name: "Leave unresolved" }));
    await screen.findByText("Left unresolved. No recipe item was chosen.");
    expect(post).toHaveBeenLastCalledWith(
      "/api/ingredient-match-reviews/review/reject",
      expect.objectContaining({ confirmation_token: initial.confirmation_token }),
      expect.anything()
    );
  });
  it("shows saved choices after reload without restoring any pending confirmation capability", async () => {
    saved = [fixture({ status: "confirmed", current_choice: true, confirmation_token: null })];
    renderWithProviders(<IngredientMatchReviewModal recipeId="recipe" ingredientId="ingredient" onClose={vi.fn()} />);
    await screen.findByText(/Current recipe choice: Whole cumin seeds/);
    expect(screen.queryByRole("button", { name: "Use for this recipe" })).not.toBeInTheDocument();
    expect(post).not.toHaveBeenCalled();
  });
  it("gives ordinary users an administrator sign-in path without loading protected review APIs", async () => {
    isAdmin = false;
    renderWithProviders(<IngredientMatchReviewModal recipeId="recipe" ingredientId="ingredient" onClose={vi.fn()} />);
    expect(screen.getByRole("link", { name: "Administrator sign-in" })).toHaveAttribute("href", "/admin");
    expect(get).not.toHaveBeenCalled();
    expect(post).not.toHaveBeenCalled();
  });
  it("allows a deliberate manual choice after the provider returns an interrupted comparison", async () => {
    compared = fixture({
      revision: 1,
      status: "interrupted",
      can_compare: false,
      can_confirm: true,
      readiness: "interrupted",
      confirmation_token: null,
      provider_requests: 1,
      reason_codes: ["unavailable"],
    });
    const user = await openReview();
    await user.click(screen.getByRole("button", { name: "Compare this item" }));
    await screen.findByText(/classifier assistance may be unavailable/);
    expect(screen.getByRole("button", { name: "Compare this item" })).toBeDisabled();
    await user.click(screen.getByLabelText("I checked the evidence and choose this item only for this recipe"));
    await user.click(screen.getByRole("button", { name: "Use for this recipe" }));
    await waitFor(() =>
      expect(post).toHaveBeenLastCalledWith(
        "/api/ingredient-match-reviews/review/confirm",
        expect.objectContaining({ revision: 1, confirmation_token: initial.confirmation_token, confirm: true }),
        expect.anything()
      )
    );
  });

  it("discards a cancelled comparison and ignores its late response without resuming", async () => {
    const user = await openReview();
    let finish!: (value: { data: MatchReview }) => void;
    post.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          finish = resolve;
        })
    );
    await user.click(screen.getByRole("button", { name: "Compare this item" }));
    const signal = post.mock.calls[1][2].signal as AbortSignal;
    await user.click(screen.getByRole("button", { name: "Stop comparison" }));
    expect(signal.aborted).toBe(true);
    await act(async () => {
      finish({ data: compared });
    });
    expect(screen.queryByText("Recipe match: Possible recipe match")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Use for this recipe" })).not.toBeInTheDocument();
    expect(post).toHaveBeenCalledTimes(2);
  });
  it("shows the server snapshot product facts if the catalog changed after candidate discovery", async () => {
    initial = fixture({
      packet: {
        ...fixture().packet,
        product: { ...product, name: "Updated cumin product", ingredients_text: "cumin and salt" },
      },
    });
    await openReview();
    expect(screen.getByText("Pantry product: Updated cumin product")).toBeVisible();
    expect(screen.getByText("Ingredients: cumin and salt")).toBeVisible();
    expect(post).toHaveBeenCalledTimes(1);
  });

  it("reuses saved facts across recipes without retyping, global save or automatic comparison", async () => {
    initial = fixture({
      packet: {
        ...fixture().packet,
        fact_sources: {
          identity: "saved_user_provided",
          form: "saved_user_provided",
          preparation: "saved_user_provided",
        },
      },
    });
    await openReview();
    expect(screen.getAllByText(/Saved product details \(user provided\)/)).toHaveLength(3);
    expect(screen.queryByLabelText("Physical form")).not.toBeInTheDocument();
    expect(post).toHaveBeenCalledTimes(1);
    expect(put).not.toHaveBeenCalled();
    cleanup();
    renderWithProviders(
      <IngredientMatchReviewModal recipeId="second-recipe" ingredientId="second-line" onClose={vi.fn()} />
    );
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Review Whole cumin seeds" }));
    await screen.findByText("Facts used for this review");
    expect(post).toHaveBeenLastCalledWith(
      "/api/recipes/second-recipe/ingredients/second-line/match-reviews",
      { candidate_item_id: "product", facts: {} },
      expect.anything()
    );
    expect(put).not.toHaveBeenCalled();
  });
  it("asks only the next server question and keeps an answer private to this review", async () => {
    initial = fixture({
      readiness: "needs_information",
      missing_fields: ["identity", "preparation", "form"],
      can_confirm: false,
      can_compare: false,
      next_clarification: {
        field: "identity",
        question: "Which product is on the label?",
        reason_code: "missing_identity_evidence",
      },
    });
    const user = await openReview();
    expect(screen.getAllByRole("textbox")).toHaveLength(2); // Search and one question.
    expect(screen.queryByLabelText("Preparation")).not.toBeInTheDocument();
    await user.type(screen.getByLabelText("Which product is on the label?"), "Cumin");
    initial = fixture({
      readiness: "needs_information",
      missing_fields: ["preparation", "form"],
      can_confirm: false,
      can_compare: false,
      next_clarification: {
        field: "preparation",
        question: "Is it raw, cooked or dried?",
        reason_code: "missing_identity_evidence",
      },
    });
    await user.click(screen.getByRole("button", { name: "Use answer for this review" }));
    await screen.findByLabelText("Is it raw, cooked or dried?");
    expect(screen.queryByLabelText("Which product is on the label?")).not.toBeInTheDocument();
    expect(post).toHaveBeenLastCalledWith(
      expect.stringContaining("/match-reviews"),
      { candidate_item_id: "product", facts: { identity: "Cumin" } },
      expect.anything()
    );
    expect(put).not.toHaveBeenCalled();
  });
  it("saves reusable product details only after a separate explicit acknowledgment and never saves recipe preferences", async () => {
    const user = await openReview();
    await user.click(screen.getByRole("button", { name: "Recipe-only requirements" }));
    await user.type(screen.getByLabelText("Recipe role or requirements"), "Smoky sauce");
    await user.click(screen.getByRole("button", { name: "Edit all product details" }));
    expect(screen.getByRole("button", { name: "Save product details" })).toBeDisabled();
    expect(put).not.toHaveBeenCalled();
    await user.click(screen.getByLabelText("I checked these product details and want to reuse them across recipes"));
    await user.click(screen.getByRole("button", { name: "Save product details" }));
    await screen.findByText(/Product details saved for reuse across recipes/);
    expect(put).toHaveBeenCalledWith(
      "/api/items/product/structured-evidence",
      {
        confirm: true,
        expected_revision: 2,
        base_source_hash: metadata.base_source_hash,
        facts: {
          identity: "cumin",
          base_ingredient: null,
          form: "whole seeds",
          preparation: "dried",
          composition: null,
        },
      },
      expect.anything()
    );
    expect(put.mock.calls[0][1].facts).not.toHaveProperty("recipe_role");
    expect(post).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("button", { name: "Use for this recipe" })).not.toBeInTheDocument();
  });
  it("keeps an open product draft bound to its original revision after a new review refreshes the candidate", async () => {
    const user = await openReview();
    await user.click(screen.getByRole("button", { name: "Edit all product details" }));
    await user.clear(screen.getByLabelText("Product details: Preparation"));
    await user.type(screen.getByLabelText("Product details: Preparation"), "roasted");
    const concurrent = { ...metadata, revision: 9, base_source_hash: "c".repeat(64), facts: { ...metadata.facts, preparation: "cooked" } };
    initial = fixture({ packet: { ...fixture().packet, product: { ...product, structured_evidence: concurrent } } });
    await user.click(screen.getByRole("button", { name: "Review these details" }));
    await screen.findByText("Facts used for this review");
    put.mockImplementation(async (_url: string, body: { expected_revision: number }) => {
      if (body.expected_revision !== concurrent.revision) throw { response: { status: 409 } };
      return { data: concurrent };
    });
    await user.click(screen.getByLabelText("I checked these product details and want to reuse them across recipes"));
    await user.click(screen.getByRole("button", { name: "Save product details" }));
    await screen.findByText(/The product details save could not be confirmed/);
    expect(put).toHaveBeenCalledWith(
      "/api/items/product/structured-evidence",
      expect.objectContaining({ expected_revision: 2, base_source_hash: metadata.base_source_hash, facts: expect.objectContaining({ preparation: "roasted" }) }),
      expect.anything()
    );
    expect(screen.queryByText(/Product details saved for reuse across recipes/)).not.toBeInTheDocument();
  });
  it.each([
    ["save", "Smoky sauce"],
    ["save", null],
    ["refresh", "Smoky sauce"],
    ["refresh", null],
  ] as const)("preserves recipe-only requirements through product %s, including %s", async (action, role) => {
    const user = await openReview();
    await user.click(screen.getByRole("button", { name: "Recipe-only requirements" }));
    await user.type(screen.getByLabelText("Recipe role or requirements"), "Smoky sauce");
    if (role === null) await user.clear(screen.getByLabelText("Recipe role or requirements"));
    await user.click(screen.getByRole("button", { name: "Change details for this review" }));
    await user.type(screen.getByLabelText("This review: Preparation"), "roasted");
    if (action === "save") {
      await user.click(screen.getByRole("button", { name: "Edit all product details" }));
      await user.click(screen.getByLabelText("I checked these product details and want to reuse them across recipes"));
      await user.click(screen.getByRole("button", { name: "Save product details" }));
      await screen.findByText(/Product details saved for reuse across recipes/);
      expect(put.mock.calls[0][1].facts).not.toHaveProperty("recipe_role");
    } else {
      get.mockImplementation(async (url: string) => ({
        data: url.endsWith("structured-evidence") ? metadata : url.endsWith("match-candidates")
          ? { requested, candidates: [{ item: product, retrieval_reason: "token_overlap" }] } : { reviews: [] },
      }));
      await user.click(screen.getByRole("button", { name: "Refresh product details" }));
      await screen.findByText("Product details refreshed. Check them, then review again.");
    }
    expect(screen.getByLabelText("Recipe role or requirements")).toHaveValue(role ?? "");
    await user.click(screen.getByRole("button", { name: "Review these details" }));
    await waitFor(() => expect(post).toHaveBeenLastCalledWith(
      expect.stringContaining("/match-reviews"),
      { candidate_item_id: "product", facts: { recipe_role: role } },
      expect.anything()
    ));
  });
  it.each([
    ["direct selection", "Smoky sauce"],
    ["direct selection", null],
    ["choose another", "Smoky sauce"],
    ["choose another", null],
  ] as const)("retains only recipe requirements on %s with requirement %s", async (path, role) => {
    const other = { ...product, item_id: "other", name: "Other cumin" };
    get.mockImplementation(async (url: string) => ({
      data: url.endsWith("match-candidates")
        ? { requested, candidates: [product, other].map((item) => ({ item, retrieval_reason: "token_overlap" })) }
        : { reviews: [] },
    }));
    const user = await openReview();
    await user.click(screen.getByRole("button", { name: "Recipe-only requirements" }));
    await user.type(screen.getByLabelText("Recipe role or requirements"), "Smoky sauce");
    if (role === null) await user.clear(screen.getByLabelText("Recipe role or requirements"));
    await user.click(screen.getByRole("button", { name: "Change details for this review" }));
    await user.type(screen.getByLabelText("This review: Preparation"), "roasted");
    if (path === "choose another") {
      await user.click(screen.getByRole("button", { name: "Review these details" }));
      await screen.findByRole("button", { name: "Choose another item" });
      await user.click(screen.getByRole("button", { name: "Choose another item" }));
    }
    initial = fixture({ candidate_item_id: "other", packet: { ...fixture().packet, product: other } });
    await user.click(screen.getByRole("button", { name: "Review Other cumin" }));
    await waitFor(() => expect(post).toHaveBeenLastCalledWith(
      expect.stringContaining("/match-reviews"),
      { candidate_item_id: "other", facts: { recipe_role: role } },
      expect.anything()
    ));
    expect(put).not.toHaveBeenCalled();
  });
  it("shows stale saved facts as previous evidence and never prefills them into current answers", async () => {
    initial = fixture({
      packet: {
        ...fixture().packet,
        product: { ...product, structured_evidence: { ...metadata, stale: true } },
        resolved: {},
        fact_sources: {},
      },
      readiness: "needs_information",
      missing_fields: ["identity"],
      can_confirm: false,
      can_compare: false,
      next_clarification: {
        field: "identity",
        question: "Which product is on the label?",
        reason_code: "missing_identity_evidence",
      },
    });
    const user = await openReview();
    expect(screen.getByText(/Saved product details are out of date/)).toBeVisible();
    expect(screen.getByLabelText("Which product is on the label?")).toHaveValue("");
    await user.click(screen.getByRole("button", { name: "Edit all product details" }));
    expect(screen.getByLabelText("Product details: Preparation")).toHaveValue("");
    expect(screen.getByLabelText("Product details: What is the product?")).toHaveValue("");
    expect(put).not.toHaveBeenCalled();
  });
  it("treats a saved-fact conflict as a fresh-review requirement without retrying the save", async () => {
    const user = await openReview();
    await user.click(screen.getByRole("button", { name: "Edit all product details" }));
    await user.click(screen.getByLabelText("I checked these product details and want to reuse them across recipes"));
    put.mockRejectedValueOnce({ response: { status: 409 } });
    await user.click(screen.getByRole("button", { name: "Save product details" }));
    await screen.findByText(/The product details save could not be confirmed/);
    expect(put).toHaveBeenCalledTimes(1);
    expect(post).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("button", { name: "Use for this recipe" })).not.toBeInTheDocument();
  });
  it("lets a person mark a saved fact unknown for just this review without clearing the product facts", async () => {
    const rawProduct = {
      ...product,
      structured_evidence: { ...metadata, facts: { ...metadata.facts, preparation: "raw" } },
    };
    initial = fixture({
      packet: {
        ...fixture().packet,
        product: rawProduct,
        resolved: { identity: "cumin", form: "whole seeds", preparation: "raw" },
      },
    });
    const user = await openReview();
    await user.click(screen.getByRole("button", { name: "Change details for this review" }));
    expect(screen.getByLabelText("This review: Preparation")).toHaveAttribute("placeholder", "Current review: raw");
    await user.click(screen.getByRole("button", { name: "Mark preparation unknown for this review" }));
    expect(screen.getByLabelText("This review: Preparation")).toHaveAttribute("placeholder", "Unknown for this review");
    initial = fixture({
      packet: {
        ...fixture().packet,
        product: rawProduct,
        facts: { preparation: null },
        resolved: { identity: "cumin", form: "whole seeds", preparation: null },
      },
      readiness: "needs_information",
      missing_fields: ["preparation"],
      next_clarification: {
        field: "preparation",
        question: "How is the product prepared?",
        reason_code: "missing_identity_evidence",
      },
      can_confirm: false,
      can_compare: false,
    });
    await user.click(screen.getByRole("button", { name: "Review these details" }));
    await screen.findByLabelText("How is the product prepared?");
    expect(screen.getByLabelText("This review: Preparation")).toHaveAttribute("placeholder", "Unknown for this review");
    expect(post).toHaveBeenLastCalledWith(
      expect.stringContaining("/match-reviews"),
      { candidate_item_id: "product", facts: { preparation: null } },
      expect.anything()
    );
    expect(screen.getByRole("button", { name: "Compare this item" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Use for this recipe" })).toBeDisabled();
    expect(put).not.toHaveBeenCalled();
  });
  it("aborts a product save on close and ignores its late response", async () => {
    const user = await openReview();
    let finish!: (value: { data: ProductEvidence }) => void;
    put.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          finish = resolve;
        })
    );
    await user.click(screen.getByRole("button", { name: "Edit all product details" }));
    await user.click(screen.getByLabelText("I checked these product details and want to reuse them across recipes"));
    await user.click(screen.getByRole("button", { name: "Save product details" }));
    const signal = put.mock.calls[0][2].signal as AbortSignal;
    await user.click(screen.getByRole("button", { name: "Close product review" }));
    expect(signal.aborted).toBe(true);
    await act(async () => {
      finish({ data: { ...metadata, revision: 3 } });
    });
    expect(screen.queryByText(/Product details saved for reuse across recipes/)).not.toBeInTheDocument();
    expect(put).toHaveBeenCalledTimes(1);
  });
  it("asks the new clarification returned by comparison instead of leaving the person blocked", async () => {
    const user = await openReview();
    compared = fixture({
      revision: 1,
      can_confirm: false,
      can_compare: false,
      readiness: "needs_information",
      next_clarification: {
        field: "identity",
        question: "Please check the product identity on its label",
        reason_code: "missing_identity_evidence",
      },
      predictions: [
        {
          purpose: "identification",
          choice: "c_4",
          label: "insufficient",
          probability: 0.7,
          provider_confidence: 0.4,
          margin: 0.5,
          clears_threshold: false,
        },
      ],
    });
    await user.click(screen.getByRole("button", { name: "Compare this item" }));
    await screen.findByLabelText("Please check the product identity on its label");
    expect(screen.getByRole("button", { name: "Use for this recipe" })).toBeDisabled();
    expect(put).not.toHaveBeenCalled();
  });
  it("does not claim cached candidates were refreshed when the candidate request fails", async () => {
    const user = await openReview();
    get.mockImplementation(async (url: string) => {
      if (url.endsWith("structured-evidence")) return { data: metadata };
      if (url.endsWith("match-candidates")) throw new Error("Candidate refresh unavailable");
      return { data: { reviews: [] } };
    });
    await user.click(screen.getByRole("button", { name: "Refresh product details" }));
    await screen.findByText(/The request did not complete/);
    expect(screen.queryByText("Product details refreshed. Check them, then review again.")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Use for this recipe" })).not.toBeInTheDocument();
    expect(post).toHaveBeenCalledTimes(1);
    expect(put).not.toHaveBeenCalled();
  });
  it("keeps the newer candidate's stale metadata when its source changes after the details read", async () => {
    const user = await openReview();
    get.mockImplementation(async (url: string) => ({
      data: url.endsWith("structured-evidence")
        ? metadata
        : url.endsWith("match-candidates")
          ? {
              requested,
              candidates: [{
                item: {
                  ...product,
                  base_source_fingerprint: "c".repeat(64),
                  source_fingerprint: "changed-source",
                  structured_evidence: { ...metadata, base_source_hash: "c".repeat(64), stale: true },
                },
                retrieval_reason: "token_overlap",
              }],
            }
          : { reviews: [] },
    }));
    await user.click(screen.getByRole("button", { name: "Refresh product details" }));
    await screen.findByText("Product details refreshed. Check them, then review again.");
    expect(screen.getByText(/Saved product details are out of date because the catalog source changed/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Edit all product details" }));
    expect(screen.getByLabelText("Product details: Preparation")).toHaveValue("");
    expect(screen.getByLabelText("Product details: What is the product?")).toHaveValue("");
    expect(put).not.toHaveBeenCalled();
    expect(post).toHaveBeenCalledTimes(1);
  });
  it("refreshes a conflicted product save before accepting a new explicit revision-bound save", async () => {
    const user = await openReview();
    await user.click(screen.getByRole("button", { name: "Edit all product details" }));
    await user.click(screen.getByLabelText("I checked these product details and want to reuse them across recipes"));
    put.mockRejectedValueOnce({ response: { status: 409 } });
    await user.click(screen.getByRole("button", { name: "Save product details" }));
    await screen.findByText(/The product details save could not be confirmed/);
    const refreshed = {
      ...metadata,
      revision: 7,
      base_source_hash: "c".repeat(64),
      facts: { ...metadata.facts, preparation: "roasted" },
    };
    get.mockImplementation(async (url: string) => ({
      data: url.endsWith("structured-evidence")
        ? refreshed
        : url.endsWith("match-candidates")
          ? {
              requested,
              candidates: [{ item: { ...product, structured_evidence: refreshed }, retrieval_reason: "token_overlap" }],
            }
          : { reviews: [] },
    }));
    await user.click(screen.getByRole("button", { name: "Refresh product details" }));
    await screen.findByText("Product details refreshed. Check them, then review again.");
    expect(put).toHaveBeenCalledTimes(1);
    expect(post).toHaveBeenCalledTimes(1);
    await user.click(screen.getByRole("button", { name: "Edit all product details" }));
    expect(screen.getByLabelText("Product details: Preparation")).toHaveValue("roasted");
    await user.click(screen.getByLabelText("I checked these product details and want to reuse them across recipes"));
    await user.click(screen.getByRole("button", { name: "Save product details" }));
    await waitFor(() =>
      expect(put).toHaveBeenLastCalledWith(
        "/api/items/product/structured-evidence",
        expect.objectContaining({ expected_revision: 7, base_source_hash: "c".repeat(64) }),
        expect.anything()
      )
    );
    expect(post).toHaveBeenCalledTimes(1);
  });
});
