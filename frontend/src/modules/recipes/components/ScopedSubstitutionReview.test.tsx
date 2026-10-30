import { cleanup, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { renderWithProviders } from "@/test-utils";
import type { CookSession } from "../hooks/cook-session-hooks";
import { ScopedSubstitutionReview, type ScopedReview } from "./ScopedSubstitutionReview";

const auth = vi.hoisted(() => ({ isAdmin: false }));
vi.mock("@/state/AuthProvider", () => ({ useAuth: () => auth }));
const get = vi.fn();
const post = vi.fn();
const remove = vi.fn();
const patch = vi.fn();
vi.mock("@/api/client", () => ({ api: { get: (...args: unknown[]) => get(...args), post: (...args: unknown[]) => post(...args), delete: (...args: unknown[]) => remove(...args), patch: (...args: unknown[]) => patch(...args) } }));
const onChanged = vi.fn(async () => undefined);
const source = { ingredient_id: "line1", item_id: "original", item_name: "Plain yogurt", quantity_required: 100, unit: "g", quantity_mode: "amount" as const };
const session = { id: "cook1", recipe_id: "recipe1", recipe_name: "Savory sauce", status: "active", servings_made: "2", plan: null } as CookSession;
let review: ScopedReview;
let savedChoices: ScopedReview[] = [];

beforeEach(() => {
  auth.isAdmin = false; onChanged.mockClear(); savedChoices = []; get.mockReset(); post.mockReset(); remove.mockReset(); patch.mockReset(); patch.mockResolvedValue({ data: {} });
  review = { id: "review1", ingredient_id: "line1", from_item_id: "original", to_item_id: "candidate", binding: "bound-current-source",
    status: "pending", scope: null, ratio: null, from_unit: "g", unit: null, stale: false,
    context: {}, snapshot: { from_item: { name: "Plain yogurt", nutrition_basis: "label_or_manual", ingredients_text: "Milk, cultures", structured_evidence: { facts: { flavor: "plain" }, provenance: "user_provided" } }, to_item: { name: "Greek yogurt", brand: "Synthetic", ingredients_text: "Milk, cultures" }, recipe_name: "Savory sauce", instructions: "Stir into cold sauce." },
    missing_evidence: ["restrictions"], warning: "Verify the source and missing evidence yourself.", existing_global: null, evaluation: null };
  get.mockImplementation(async (url: string) => ({ data: url === "/api/items" ? { items: [
    { id: "original", name: "Plain yogurt" }, { id: "candidate", name: "Greek yogurt", brand: "Synthetic" }, { id: "other", name: "Vanilla yogurt" },
  ] } : url.endsWith("unit-vocab") ? { units: [{ value: "g", label: "grams" }, { value: "tsp", label: "teaspoon" }] } : { reviews: [], recipe_approvals: savedChoices, attached_ids: [] } }));
  post.mockImplementation(async (url: string, body: Record<string, unknown>) => ({ data: url.endsWith("/approve") ? { ...review, status: "approved", scope: body.scope, ratio: String(body.ratio), unit: body.unit } : review }));
});
afterEach(cleanup);

async function open(packageMode = false) {
  const user = userEvent.setup();
  renderWithProviders(<ScopedSubstitutionReview session={session} ingredients={[{ ...source, quantity_mode: packageMode ? "package" : "amount" }]} onChanged={onChanged} />);
  await user.click(screen.getByRole("button", { name: "Review a substitution" }));
  await screen.findByLabelText("Recipe ingredient");
  await user.selectOptions(screen.getByLabelText("Recipe ingredient"), "line1");
  await waitFor(() => expect(screen.getByRole("option", { name: "Greek yogurt · Synthetic" })).toBeInTheDocument());
  await user.selectOptions(screen.getByLabelText("Proposed substitute"), "candidate");
  return user;
}

async function prepare() {
  const user = await open();
  await user.click(screen.getByRole("button", { name: "Prepare manual review" }));
  await screen.findByLabelText("Substitute ratio");
  return user;
}

async function amount(user: ReturnType<typeof userEvent.setup>) {
  await user.type(screen.getByLabelText("Substitute ratio"), "2");
  await user.selectOptions(screen.getByLabelText("Substitute unit"), "g");
}

it("keeps inference and cooking separate, defaults to one cook and sends the user's explicit ratio", async () => {
  const user = await prepare();
  expect(post).toHaveBeenCalledTimes(1);
  expect(post).toHaveBeenCalledWith("/api/cook-sessions/cook1/substitution-reviews", expect.objectContaining({ ingredient_id: "line1", substitute_item_id: "candidate" }));
  expect(screen.getByLabelText("Approval scope")).toHaveValue("one_use");
  expect(screen.getByLabelText("Substitute ratio")).toHaveValue("");
  expect(screen.getByRole("button", { name: "Approve substitution" })).toBeDisabled();
  expect(screen.getByText(/Missing evidence: restrictions/)).toBeInTheDocument();
  expect(screen.getByText(/No dietary or allergy safety is certified/)).toBeInTheDocument();
  await amount(user);
  expect(screen.getByText("2 g substitute per 1 g original")).toBeInTheDocument();
  await user.click(screen.getByRole("checkbox", { name: /I checked the direction/ }));
  await user.click(screen.getByRole("button", { name: "Approve substitution" }));
  await screen.findByText(/Choice approved for the stated scope/);
  expect(post).toHaveBeenLastCalledWith("/api/substitution-reviews/review1/approve", expect.objectContaining({ ratio: 2, unit: "g", scope: "one_use", confirmed: true, permanent_ack: false, expected_binding: "bound-current-source" }));
  expect(post.mock.calls.every(([url]) => !String(url).includes("finish") && !String(url).includes("evaluate"))).toBe(true);
  expect(onChanged).toHaveBeenCalledOnce();
});

it("keeps high model scores advisory and displays readable choices and the configured threshold", async () => {
  auth.isAdmin = true;
  const user = await prepare();
  review = { ...review, evaluation: { status: "ok", verdict: "c_2", score: 0.99, threshold: 0.9, below_threshold: false } };
  await user.click(screen.getByRole("button", { name: "Ask classifier for an optional hint" }));
  expect(await screen.findByText(/possible with adjustments.*99% model score.*90% configured threshold/)).toBeInTheDocument();
  expect(screen.getByText(/even above your threshold/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Approve substitution" })).toBeDisabled();
  await amount(user);
  expect(screen.getByRole("button", { name: "Approve substitution" })).toBeDisabled();
});

it("shows old and new global bases and requires separate permanent and replacement acknowledgments", async () => {
  review = { ...review, existing_global: { ratio: "1", unit: "g", from_unit: null } };
  const user = await prepare();
  await amount(user);
  await user.selectOptions(screen.getByLabelText("Approval scope"), "global");
  expect(screen.getByText(/Existing household mapping: 1 g per 1 original unit \(legacy basis\).*New choice: 2 g per 1 g/)).toBeInTheDocument();
  await user.click(screen.getByRole("checkbox", { name: /I checked the direction/ }));
  await user.click(screen.getByRole("checkbox", { name: /I understand this permanently/ }));
  expect(screen.getByRole("button", { name: "Approve substitution" })).toBeDisabled();
  await user.click(screen.getByRole("checkbox", { name: /I explicitly approve replacing/ }));
  await user.click(screen.getByRole("button", { name: "Approve substitution" }));
  await waitFor(() => expect(post).toHaveBeenLastCalledWith("/api/substitution-reviews/review1/approve", expect.objectContaining({ scope: "global", permanent_ack: true, replace_existing_ack: true })));
});

it("invalidates the review, ratio and acknowledgment when candidate or context changes", async () => {
  const user = await prepare();
  await amount(user);
  await user.click(screen.getByRole("checkbox", { name: /I checked the direction/ }));
  await user.selectOptions(screen.getByLabelText("Proposed substitute"), "other");
  expect(screen.queryByRole("button", { name: "Approve substitution" })).not.toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Prepare manual review" }));
  expect(await screen.findByLabelText("Substitute ratio")).toHaveValue("");
  expect(screen.getByRole("checkbox", { name: /I checked the direction/ })).not.toBeChecked();
  await user.type(screen.getByLabelText("Preparation and cooking context"), "Baked bread");
  expect(screen.queryByLabelText("Substitute ratio")).not.toBeInTheDocument();
});

it("ignores an obsolete in-flight review after an ingredient edit", async () => {
  let resolve!: (value: { data: ScopedReview }) => void;
  post.mockImplementationOnce(() => new Promise((done) => { resolve = done; }));
  const user = await open();
  await user.click(screen.getByRole("button", { name: "Prepare manual review" }));
  await user.type(screen.getByLabelText("Cooking role"), "Thickener");
  resolve({ data: review });
  await waitFor(() => expect(screen.getByRole("button", { name: "Prepare manual review" })).toBeEnabled());
  expect(screen.queryByLabelText("Substitute ratio")).not.toBeInTheDocument();
});

it("offers saved recipe choices only for explicit reuse and blocks stale evidence", async () => {
  savedChoices = [{ ...review, id: "saved1", scope: "recipe", status: "approved", ratio: "1.5", unit: "g" }, { ...review, id: "saved-stale", scope: "recipe", status: "approved", stale: true, ratio: "1", unit: "g", snapshot: { ...review.snapshot, to_item: { name: "Old yogurt" } } }];
  const user = await open();
  const buttons = await screen.findAllByRole("button", { name: "Use saved choice for this cook" });
  expect(buttons.every((button) => button.hasAttribute("disabled"))).toBe(true);
  expect(post).not.toHaveBeenCalled();
  await user.click(screen.getByRole("checkbox", { name: "Confirm reuse of Greek yogurt for this cook" }));
  await user.click(buttons[0]);
  await waitFor(() => expect(post).toHaveBeenCalledWith("/api/cook-sessions/cook1/substitution-reviews/saved1/attach", expect.objectContaining({ confirmed: true, expected_binding: "bound-current-source" })));
  expect(buttons[1]).toBeDisabled();
});

it("keeps unsupported whole-package swaps out of the review write path", async () => {
  await open(true);
  expect(screen.getByText(/Whole-package substitutions are not supported/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Prepare manual review" })).toBeDisabled();
  expect(post).not.toHaveBeenCalled();
});

it("keeps disabled and low-confidence hints manually usable with explicit confirmation", async () => {
  auth.isAdmin = true;
  const user = await prepare();
  review = { ...review, evaluation: { status: "disabled", score: null } };
  await user.click(screen.getByRole("button", { name: "Ask classifier for an optional hint" }));
  expect(await screen.findByText(/Classifier: disabled/)).toBeInTheDocument();
  review = { ...review, evaluation: { status: "ok", verdict: "c_4", score: 0.42, below_threshold: true, threshold: 0.9 } };
  await user.click(screen.getByRole("button", { name: "Ask classifier for an optional hint" }));
  expect(await screen.findByText(/insufficient evidence.*42% model score \(below threshold\)/)).toBeInTheDocument();
  await amount(user);
  await user.click(screen.getByRole("checkbox", { name: /I checked the direction/ }));
  await user.click(screen.getByRole("button", { name: "Approve substitution" }));
  expect(await screen.findByText(/Choice approved for the stated scope/)).toBeInTheDocument();
});

it("sends the selected ingredient row even when two rows share one original item", async () => {
  const user = userEvent.setup();
  renderWithProviders(<ScopedSubstitutionReview session={session} ingredients={[source, { ...source, ingredient_id: "line2" }]} onChanged={onChanged} />);
  await user.click(screen.getByRole("button", { name: "Review a substitution" }));
  await user.selectOptions(await screen.findByLabelText("Recipe ingredient"), "line2");
  await screen.findByRole("option", { name: "Greek yogurt · Synthetic" });
  await user.selectOptions(screen.getByLabelText("Proposed substitute"), "candidate");
  await user.click(screen.getByRole("button", { name: "Prepare manual review" }));
  await waitFor(() => expect(post).toHaveBeenCalledWith("/api/cook-sessions/cook1/substitution-reviews", expect.objectContaining({ ingredient_id: "line2" })));
});

it("removes a permanent choice from this cook without deleting its household mapping or dropping legacy plan fields", async () => {
  const approved = { ...review, status: "approved", scope: "global", ratio: "2", unit: "g" };
  const legacy = { original_item_id: "flour", substitute_item_id: "rye", ratio: 1.2, unit: "g" };
  const plan = { ingredient_overrides: [{ ingredient_id: "other-line", quantity: 3, omit: false }], scale_to_ingredient_id: "basis-line", allow_short_ingredients: true, substitutions: [legacy], substitution_approval_ids: [review.id, "another-choice"] };
  const originalGet = get.getMockImplementation()!;
  get.mockImplementation(async (url: string) => url.endsWith("/substitution-reviews") ? { data: { reviews: [approved], recipe_approvals: [], attached_ids: [review.id, "another-choice"] } } : originalGet(url));
  const user = userEvent.setup();
  renderWithProviders(<ScopedSubstitutionReview session={{ ...session, plan }} ingredients={[source]} onChanged={onChanged} />);
  await user.click(screen.getByRole("button", { name: "Review a substitution" }));
  await user.click(await screen.findByRole("button", { name: "Remove from this cook" }));
  await waitFor(() => expect(patch).toHaveBeenCalledWith("/api/cook-sessions/cook1", { plan: { ...plan, substitution_approval_ids: ["another-choice"] } }));
  expect(remove).not.toHaveBeenCalled();
  expect(post).not.toHaveBeenCalled();
  expect(await screen.findByText(/Saved household mappings were not changed/)).toBeInTheDocument();
});

it("keeps a reused recipe choice visible and removable in a later cook", async () => {
  const saved = { ...review, id: "previous-cook-choice", scope: "recipe", status: "approved", ratio: "1.5", unit: "g" };
  let attached = [saved.id];
  const originalGet = get.getMockImplementation()!;
  get.mockImplementation(async (url: string) => url.endsWith("/substitution-reviews") ? { data: { reviews: [], recipe_approvals: [saved], attached_ids: attached } } : originalGet(url));
  patch.mockImplementation(async () => { attached = []; return { data: {} }; });
  const user = userEvent.setup();
  renderWithProviders(<ScopedSubstitutionReview session={{ ...session, id: "later-cook", plan: { substitution_approval_ids: [saved.id] } }} ingredients={[source]} onChanged={onChanged} />);
  await user.click(screen.getByRole("button", { name: "Review a substitution" }));
  expect(await screen.findByText(/Attached: Plain yogurt → Greek yogurt; 1.5 g per 1 g; recipe/)).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Remove from this cook" }));
  await waitFor(() => expect(patch).toHaveBeenCalledWith("/api/cook-sessions/later-cook", expect.objectContaining({ plan: expect.objectContaining({ substitution_approval_ids: [] }) })));
  expect(await screen.findByText(/Saved recipe choice: Plain yogurt → Greek yogurt/)).toBeInTheDocument();
  expect(screen.queryByText(/Attached: Plain yogurt/)).not.toBeInTheDocument();
  expect(remove).not.toHaveBeenCalled();
  expect(post).not.toHaveBeenCalled();
});

it("requires replacement acknowledgment for a reused same-row recipe choice and deduplicates attached history", async () => {
  const saved = { ...review, id: "previous-cook-choice", scope: "recipe", status: "approved", ratio: "1.5", unit: "g" };
  const originalGet = get.getMockImplementation()!;
  get.mockImplementation(async (url: string) => url.endsWith("/substitution-reviews") ? { data: { reviews: [], recipe_approvals: [saved, saved], attached_ids: [saved.id] } } : originalGet(url));
  const user = await prepare();
  expect(screen.getAllByText(/Attached: Plain yogurt → Greek yogurt/)).toHaveLength(1);
  await amount(user);
  await user.click(screen.getByRole("checkbox", { name: /I checked the direction/ }));
  expect(screen.getByRole("button", { name: "Approve substitution" })).toBeDisabled();
  await user.click(screen.getByRole("checkbox", { name: "Replace the existing approved choice for this ingredient in this cook." }));
  await user.click(screen.getByRole("button", { name: "Approve substitution" }));
  await waitFor(() => expect(post).toHaveBeenLastCalledWith("/api/substitution-reviews/review1/approve", expect.objectContaining({ replace_approval_ack: true })));
});

it("keeps an attached revoked recipe choice visible and removable in a later cook", async () => {
  const revoked = { ...review, id: "revoked-recipe-choice", scope: "recipe", status: "revoked", ratio: "1.5", unit: "g" };
  let attached = [revoked.id];
  const originalGet = get.getMockImplementation()!;
  get.mockImplementation(async (url: string) => url.endsWith("/substitution-reviews") ? { data: { reviews: [revoked], recipe_approvals: [], attached_ids: attached } } : originalGet(url));
  patch.mockImplementation(async () => { attached = []; return { data: {} }; });
  const user = userEvent.setup();
  renderWithProviders(<ScopedSubstitutionReview session={{ ...session, id: "later-cook", plan: { substitution_approval_ids: [revoked.id] } }} ingredients={[source]} onChanged={onChanged} />);
  await user.click(screen.getByRole("button", { name: "Review a substitution" }));
  expect(await screen.findByText(/Choice status: revoked.*Finish is blocked/)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Revoke saved recipe choice" })).not.toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Remove from this cook" }));
  await waitFor(() => expect(patch).toHaveBeenCalledWith("/api/cook-sessions/later-cook", expect.objectContaining({ plan: expect.objectContaining({ substitution_approval_ids: [] }) })));
  expect(remove).not.toHaveBeenCalled();
  expect(post).not.toHaveBeenCalled();
});

it("shows a missing attached choice and explicitly removes only its ID while preserving the cook plan", async () => {
  let attached = ["missing-choice", "other-choice"];
  let missing = ["missing-choice"];
  const originalGet = get.getMockImplementation()!;
  get.mockImplementation(async (url: string) => url.endsWith("/substitution-reviews") ? { data: { reviews: [], recipe_approvals: [], attached_ids: attached, missing_attachment_ids: missing } } : originalGet(url));
  const legacy = { original_item_id: "flour", substitute_item_id: "rye", ratio: 1.2, unit: "g" };
  const plan = { ingredient_overrides: [{ ingredient_id: "other-row", quantity: 3, omit: false }], scale_to_ingredient_id: "basis-row", allow_short_ingredients: true, substitutions: [legacy], substitution_approval_ids: attached };
  patch.mockImplementation(async () => { attached = ["other-choice"]; missing = []; return { data: {} }; });
  const user = userEvent.setup();
  renderWithProviders(<ScopedSubstitutionReview session={{ ...session, plan }} ingredients={[source]} onChanged={onChanged} />);
  await user.click(screen.getByRole("button", { name: "Review a substitution" }));
  expect(await screen.findByText("Missing attached choice: missing-choice")).toBeInTheDocument();
  expect(screen.getByText(/its ingredient or review may have been removed/)).toBeInTheDocument();
  expect(screen.getByText(/Finish is blocked until you explicitly remove this missing choice/)).toBeInTheDocument();
  expect(screen.queryByText(/Attached: Plain yogurt/)).not.toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Remove missing choice from this cook" }));
  await waitFor(() => expect(patch).toHaveBeenCalledWith("/api/cook-sessions/cook1", { plan: { ...plan, substitution_approval_ids: ["other-choice"] } }));
  expect(remove).not.toHaveBeenCalled();
  expect(post).not.toHaveBeenCalled();
});

it("blocks approval and saved reuse until the missing attachment is explicitly removed", async () => {
  let missing = ["missing-choice"];
  const saved = { ...review, id: "saved-choice", scope: "recipe", status: "approved", ratio: "1.5", unit: "g" };
  const originalGet = get.getMockImplementation()!;
  get.mockImplementation(async (url: string) => url.endsWith("/substitution-reviews") ? { data: { reviews: [], recipe_approvals: [saved], attached_ids: missing, missing_attachment_ids: missing } } : originalGet(url));
  patch.mockImplementation(async () => { missing = []; return { data: {} }; });
  const user = await prepare();
  await amount(user);
  await user.click(screen.getByRole("checkbox", { name: /I checked the direction/ }));
  await user.click(screen.getByRole("checkbox", { name: "Confirm reuse of Greek yogurt for this cook" }));
  expect(screen.getByRole("button", { name: "Approve substitution" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "Use saved choice for this cook" })).toBeDisabled();
  expect(post).toHaveBeenCalledTimes(1);
  await user.click(screen.getByRole("button", { name: "Remove missing choice from this cook" }));
  await screen.findByText(/Choice removed from this cook/);
  await waitFor(() => expect(screen.queryByText("Missing attached choice: missing-choice")).not.toBeInTheDocument());
  await user.click(screen.getByRole("button", { name: "Prepare manual review" }));
  await screen.findByLabelText("Substitute ratio");
  await user.clear(screen.getByLabelText("Substitute ratio"));
  await amount(user);
  await user.click(screen.getByRole("checkbox", { name: /I checked the direction/ }));
  await user.click(screen.getByRole("button", { name: "Approve substitution" }));
  expect(await screen.findByText(/Choice approved for the stated scope/)).toBeInTheDocument();
  expect(post).toHaveBeenLastCalledWith("/api/substitution-reviews/review1/approve", expect.objectContaining({ scope: "one_use", confirmed: true }));
});
