import { cleanup, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { renderWithProviders } from "@/test-utils";
import { ClassifierEvaluationPanel } from "./ClassifierEvaluationPanel";

const get = vi.fn();
const post = vi.fn();
const remove = vi.fn();
vi.mock("@/api/client", () => ({ api: { get: (...args: unknown[]) => get(...args), post: (...args: unknown[]) => post(...args), delete: (...args: unknown[]) => remove(...args) } }));
const base = "/api/ai/classifier/benchmark";
const draft = {
  corpus_hash: "corpus-exact", review_hash: "labels-exact", version: "v1", status: "draft",
  adequacy: { groups: 2 }, frozen: false, reviewed_count: 0, case_count: 2, policy: null,
  holdout_exposed: false, limits: { max_cases: 500, max_requests: 500, max_seconds: 1800 },
  label_options: { identity: ["same", "different"] },
  cases: [0, 1].map((id) => ({ id: `case-${id}`, edge_family: "brand ambiguity", leakage_group: `group-${id}`, split: "train", task: "identity", evidence: { subject: `Food ${id}`, candidate_or_context: "Candidate" }, proposed_label: "same", label: "same", reviewed: false })),
};
const result = {
  id: "run-1", status: "completed", engine: "rules", evidence_kind: "contract_fixture",
  corpus_hash: "corpus-exact", review_hash: "labels-exact", policy_hash: null, versions: { rules: "1" },
  split: "train", completed: 1, total: 1, budget: { max_requests: 1, max_seconds: 60, attempted: 1, elapsed_seconds: 0.1 },
  summary: { by_task: { identity: { support: 1, groups: 1, correct: 1, false_positives: 0, misses: 0, abstained: 0, failures: 0, accuracy: 1, coverage: 1, p50_ms: null, p95_ms: null, total_cost_usd: null, unknown_cost_count: 1, input_tokens: null, output_tokens: null, candidate_retrieval_recall: null, review_corrections_saved: null } }, rollout: { identity: { eligible: false, reasons: ["Insufficient groups"] } } },
};
const diagnosticPolicy = { confidence: 0.9, margin: 0.2, min_support: 20, min_groups: 5, max_false_positives: 0, max_p95_ms: 5000, max_failure_rate: 0.05, budget_unit: "requests", max_requests: 140 };
function diagnosticCorpus(frozen = true, saved = false, splitUsed = { train: false, holdout: false }) {
  return { ...draft, case_count: 366, reviewed_count: 14, frozen, freeze_kind: frozen ? "owner_authorized_diagnostic" : null,
    cases: [{ ...draft.cases[0], task: "nutrition_transfer" }, { ...draft.cases[1], task: "density_transfer", split: "holdout" }],
    diagnostic_policy: diagnosticPolicy,
    policy: saved ? { version: "fixed", hash: "diagnostic-policy", tasks: { nutrition_transfer: diagnosticPolicy, density_transfer: diagnosticPolicy } } : null,
    diagnostic: frozen ? { authorization_hash: "owner-snapshot", label_provenance: "ai_proposed_reference", selected_count: 206, selected_reviewed_count: 0, split_counts: { train: 140, holdout: 66 }, split_used: splitUsed } : null,
  };
}
let corpus: typeof draft;
async function open() {
  renderWithProviders(<ClassifierEvaluationPanel />);
  const user = userEvent.setup();
  const expand = screen.queryByRole("button", { name: "Expand" });
  if (expand) await user.click(expand);
  await screen.findByLabelText("Evaluation case");
  return user;
}
beforeEach(() => {
  window.localStorage.clear(); vi.clearAllMocks(); corpus = structuredClone(draft);
  get.mockImplementation(async () => ({ data: corpus }));
  post.mockResolvedValue({ data: result });
  remove.mockReset().mockResolvedValue({ data: { deleted: true } });
});
afterEach(cleanup);

describe("Classifier evaluation", () => {
  it("requires explicit diagnostic authorization and a separate fixed-policy save without confirming human labels", async () => {
    let authorized = false;
    let saved = false;
    get.mockImplementation(async () => ({ data: diagnosticCorpus(authorized, saved) }));
    post.mockImplementation(async (url) => {
      if (url.endsWith("/diagnostic-freeze")) authorized = true;
      if (url.endsWith("/policy")) saved = true;
      return { data: {} };
    });
    const user = await open();
    const authorize = screen.getByRole("button", { name: "Use smaller diagnostic test" });
    expect(authorize).toBeDisabled();
    expect(screen.getByRole("button", { name: "Freeze reviewed labels and split" })).toBeDisabled();
    const permission = screen.getByRole("checkbox", { name: /I authorize a fixed diagnostic snapshot/ });
    expect(permission).not.toBeChecked();
    expect(post).not.toHaveBeenCalled();
    await user.click(permission);
    await user.click(authorize);
    await screen.findByText("14 of 366 cases reviewed · Diagnostic snapshot frozen");
    expect(post).toHaveBeenCalledTimes(1);
    expect(post).toHaveBeenCalledWith(`${base}/corpus/diagnostic-freeze`, { review_hash: "labels-exact", confirm: true });
    expect(screen.getByText(/206 selected cases; 0 selected cases human reviewed/)).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: "Measure Nutrition suitability" })).toBeDisabled();
    await user.click(screen.getByText("Nutrition suitability thresholds"));
    expect(screen.getByLabelText("Nutrition suitability: Minimum confidence (%)")).toBeDisabled();
    expect(screen.getByLabelText("Nutrition suitability: Minimum confidence (%)")).toHaveValue(90);
    expect(screen.getByRole("button", { name: "Save threshold policy" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Start evaluation" })).toBeDisabled();
    expect(screen.getByText("Save the fixed diagnostic policy before starting.")).toBeInTheDocument();
    expect(screen.getByText(/207-request allocation includes one separate connection test outside this benchmark/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Save threshold policy" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Save threshold policy" })).toBeDisabled());
    expect(post).toHaveBeenLastCalledWith(`${base}/policy`, { review_hash: "labels-exact", tasks: { nutrition_transfer: diagnosticPolicy, density_transfer: diagnosticPolicy } });
    expect(screen.getByText("14 of 366 cases reviewed · Diagnostic snapshot frozen")).toBeInTheDocument();
    expect(post.mock.calls.some(([url]) => url.endsWith("/corpus/review") || url.endsWith("/runs"))).toBe(false);
  });
  it("reports the actual human-reviewed subset in a mixed provisional snapshot", async () => {
    const view = diagnosticCorpus();
    get.mockResolvedValue({ data: { ...view, reviewed_count: 34, diagnostic: { ...view.diagnostic!, label_provenance: "mixed_reference", selected_reviewed_count: 3 } } });
    await open();
    expect(screen.getByText("34 of 366 cases reviewed · Diagnostic snapshot frozen")).toBeInTheDocument();
    expect(screen.getByText(/206 selected cases; 3 selected cases human reviewed; 203 AI-proposed references/)).toBeInTheDocument();
    expect(post).not.toHaveBeenCalled();
  });
  it("uses fixed single-use diagnostic budgets and labels draft agreement without rollout approval", async () => {
    const used = { train: false, holdout: false };
    let selectedSplit: "train" | "holdout" = "train";
    get.mockImplementation(async () => ({ data: diagnosticCorpus(true, true, { ...used }) }));
    const runResult = () => ({ ...result, id: `diagnostic-${selectedSplit}`, engine: "classifier", split: selectedSplit,
      freeze_kind: "owner_authorized_diagnostic", label_provenance: "ai_proposed_reference", diagnostic_authorization_hash: "owner-snapshot",
      metric_interpretation: "Agreement with AI-proposed synthetic references; not human-ground-truth accuracy",
      summary: { ...result.summary, by_task: { identity: { ...result.summary.by_task.identity, reliability: [{ lower: 0.8, upper: 1, count: 1, accuracy: 1 }] } } },
    });
    post.mockImplementation(async (url, payload) => {
      if (url.endsWith("/runs")) { selectedSplit = payload.split; used[selectedSplit] = true; return { data: { ...runResult(), status: "ready" } }; }
      return { data: runResult() };
    });
    const user = await open();
    await waitFor(() => expect(screen.getByLabelText("Maximum requests")).toHaveValue(140));
    expect(screen.getByLabelText("Maximum seconds")).toHaveValue(1200);
    expect(screen.getByLabelText("Maximum requests")).toBeDisabled();
    expect(screen.getByLabelText("Evaluation method")).toHaveValue("classifier");
    expect(screen.getByLabelText("Evaluation method")).toBeDisabled();
    await user.click(screen.getByRole("checkbox", { name: /I approve up to/ }));
    await user.click(screen.getByRole("button", { name: "Start evaluation" }));
    await screen.findByText("Run diagnostic-train: completed · 1/1");
    expect(post).toHaveBeenCalledWith(`${base}/runs`, { engine: "classifier", split: "train", max_requests: 140, max_seconds: 1200, budget_confirmed: true }, expect.anything());
    expect(screen.getByText(/draft agreement 100.0%/)).toBeInTheDocument();
    expect(screen.getByText(/Rollout remains blocked/)).toBeInTheDocument();
    await user.click(screen.getByText("identity: confusion counts and draft agreement by confidence"));
    const diagnosticDetails = screen.getByText(/"draft_agreement_by_confidence"/);
    expect(diagnosticDetails).toHaveTextContent('"draft_agreement": 1');
    expect(diagnosticDetails).not.toHaveTextContent('"accuracy"');
    await user.click(screen.getByRole("checkbox", { name: /I approve up to/ }));
    expect(screen.getByRole("button", { name: "Start evaluation" })).toBeDisabled();
    await user.selectOptions(screen.getByLabelText("Evaluation split"), "holdout");
    await waitFor(() => expect(screen.getByLabelText("Maximum requests")).toHaveValue(66));
    expect(screen.getByLabelText("Maximum seconds")).toHaveValue(600);
    expect(screen.getByText(/Held-out diagnostic requires the locked provisional reference snapshot and its fixed policy/)).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: /I approve up to/ })).not.toBeChecked();
    await user.click(screen.getByRole("checkbox", { name: /I approve up to/ }));
    await user.click(screen.getByRole("button", { name: "Start evaluation" }));
    await screen.findByText("Run diagnostic-holdout: completed · 1/1");
    expect(post).toHaveBeenCalledWith(`${base}/runs`, { engine: "classifier", split: "holdout", max_requests: 66, max_seconds: 600, budget_confirmed: true }, expect.anything());
    expect(screen.getByRole("button", { name: "Start evaluation" })).toBeDisabled();
    expect(post.mock.calls.filter(([url]) => url.endsWith("/runs"))).toHaveLength(2);
  });
  it("replaces only an eligible old draft after separate explicit confirmation", async () => {
    let upgraded = false;
    get.mockImplementation(async (url) => {
      if (url.endsWith("/compatibility")) return { data: { upgrade_required: !upgraded, eligible: true, previous_corpus_hash: "previous-exact", corpus_hash: "corpus-exact", detail: "A revised AI draft is available; your old draft has no human reviews or measurements." } };
      if (!upgraded) throw new Error("Old corpus");
      return { data: corpus };
    });
    post.mockImplementation(async () => { upgraded = true; return { data: corpus }; });
    renderWithProviders(<ClassifierEvaluationPanel />);
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Expand" }));
    const button = await screen.findByRole("button", { name: "Use revised AI draft cases" });
    expect(button).toBeDisabled();
    expect(post).not.toHaveBeenCalled();
    expect(screen.queryByText("Could not load evaluation cases")).not.toBeInTheDocument();
    const confirmation = screen.getByRole("checkbox", { name: /Replace the unreviewed draft/ });
    expect(confirmation).not.toBeChecked();
    await user.click(confirmation);
    await user.click(button);
    await screen.findByText("0 of 2 cases reviewed · Draft labels");
    expect(post).toHaveBeenCalledTimes(1);
    expect(post).toHaveBeenCalledWith(`${base}/corpus/upgrade`, { previous_corpus_hash: "previous-exact", confirm: true });
    expect(screen.getByRole("button", { name: "Save threshold policy" })).toBeDisabled();
    expect(screen.queryByRole("button", { name: "Use revised AI draft cases" })).not.toBeInTheDocument();
  });
  it("shows an ineligible corpus explanation without a reset action or automatic writes", async () => {
    get.mockImplementation(async (url) => {
      if (url.endsWith("/compatibility")) return { data: { upgrade_required: true, eligible: false, previous_corpus_hash: "previous-exact", corpus_hash: "corpus-exact", detail: "Existing human reviews or measurements must be retained." } };
      throw new Error("Old corpus");
    });
    renderWithProviders(<ClassifierEvaluationPanel />);
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Expand" }));
    await screen.findByText("Existing human reviews or measurements must be retained.");
    expect(screen.queryByRole("button", { name: "Use revised AI draft cases" })).not.toBeInTheDocument();
    expect(screen.queryByText("Could not load evaluation cases")).not.toBeInTheDocument();
    expect(post).not.toHaveBeenCalled();
  });
  it("lets an incompatible corpus retain, inspect and explicitly delete old diagnostics before offering an unchecked upgrade", async () => {
    let retained = true;
    const oldRun = { ...result, id: "old-diagnostic", corpus_hash: "previous-exact", status: "interrupted" };
    get.mockImplementation(async (url) => {
      if (url.endsWith("/compatibility")) return { data: { upgrade_required: true, eligible: !retained, previous_corpus_hash: "previous-exact", corpus_hash: "corpus-exact", detail: retained ? "Retained diagnostics must be reviewed before draft replacement." : "The unreviewed draft can now be replaced explicitly." } };
      if (url.endsWith("/runs")) return { data: { runs: retained ? [oldRun] : [] } };
      if (url.endsWith("/runs/old-diagnostic")) return { data: oldRun };
      throw Object.assign(new Error("Corpus conflict"), { response: { status: 409 } });
    });
    remove.mockImplementation(async () => { retained = false; return { data: { deleted: true } }; });
    renderWithProviders(<ClassifierEvaluationPanel />);
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Expand" }));
    await screen.findByText("Retained diagnostics must be reviewed before draft replacement.");
    expect(screen.queryByLabelText("Evaluation case")).not.toBeInTheDocument();
    expect(screen.getByText(/Up to 20 runs can be saved/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Load recent saved runs" }));
    await user.click(await screen.findByRole("button", { name: "View saved run old-diagnostic" }));
    await screen.findByText("Run old-diagnostic: interrupted · 1/1");
    await user.click(screen.getByRole("button", { name: "Delete saved run old-diagnostic" }));
    expect(remove).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "Confirm delete saved run" }));
    const upgrade = await screen.findByRole("button", { name: "Use revised AI draft cases" });
    expect(upgrade).toBeDisabled();
    expect(screen.getByRole("checkbox", { name: /Replace the unreviewed draft/ })).not.toBeChecked();
    expect(screen.queryByRole("button", { name: "View saved run old-diagnostic" })).not.toBeInTheDocument();
    expect(screen.queryByText("Run old-diagnostic: interrupted · 1/1")).not.toBeInTheDocument();
    expect(remove).toHaveBeenCalledWith(`${base}/runs/old-diagnostic`);
    expect(get).toHaveBeenLastCalledWith(`${base}/corpus/compatibility`);
    expect(post).not.toHaveBeenCalled();
  });
  it("shows draft gates without creating provider calls or reviewing any case on mount", async () => {
    await open();
    expect(screen.getByText("0 of 2 cases reviewed · Draft labels")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Freeze reviewed labels and split" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Save threshold policy" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Start evaluation" })).toBeDisabled();
    expect(post).not.toHaveBeenCalled();
  });
  it("reviews only the selected case with edited label and exact hashes", async () => {
    const user = await open();
    await user.selectOptions(screen.getByLabelText("Evaluation case"), "1");
    await user.selectOptions(screen.getByLabelText("Reviewed label"), "different");
    await user.click(screen.getByRole("button", { name: "I reviewed this case" }));
    await waitFor(() => expect(post).toHaveBeenCalledWith(`${base}/corpus/review`, { corpus_hash: "corpus-exact", review_hash: "labels-exact", case_id: "case-1", label: "different", confirm: true }));
    expect(post).toHaveBeenCalledTimes(1);
  });
  it("asks the plain classification question and separates grouping metadata from model evidence", async () => {
    get.mockResolvedValue({ data: { ...corpus, cases: [{ ...corpus.cases[0], task: "culinary_identity", leakage_group: "beans" }] } });
    const user = await open();
    expect(screen.getByText("Question: Do these descriptions refer to the same ingredient in the same meaningful form?")).toBeVisible();
    expect(screen.getByRole("button", { name: "I reviewed this case" })).toBeEnabled();
    await user.click(screen.getByText("Technical evidence details"));
    const evidence = screen.getByText(/"subject": "Food 0"/);
    expect(evidence).not.toHaveTextContent("group_id");
    expect(evidence).not.toHaveTextContent("beans");
    await user.click(screen.getByText("How test cases are grouped"));
    expect(screen.getByText(/not a food category or predicted answer, and is not sent to the model/)).toBeVisible();
    expect(post).not.toHaveBeenCalled();
  });
  it("moves between readable cases without confirming labels", async () => {
    const user = await open();
    expect(screen.getByRole("button", { name: "Previous case" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "Next case" }));
    expect(screen.getByText((_content, element) => element?.tagName === "P" && element.textContent === "Original text: Food 1")).toBeVisible();
    expect(screen.getByRole("button", { name: "Next case" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "Previous case" }));
    expect(screen.getByLabelText("Evaluation case")).toHaveValue("0");
    expect(post).not.toHaveBeenCalled();
  });
  it("allows editing an existing policy before held-out exposure", async () => {
    corpus = { ...corpus, frozen: true, reviewed_count: 2 };
    get.mockResolvedValue({ data: { ...corpus, cases: [...corpus.cases, { ...corpus.cases[0], id: "density-extra", task: "density_transfer" }], policy: { version: "v1", hash: "old-policy", tasks: {
      identity: { confidence: 0.97, margin: 0.2, min_support: 20, min_groups: 5, max_false_positives: 0, max_p95_ms: 5000, max_failure_rate: 0.05, budget_unit: "requests", max_requests: 500 },
    } } } });
    const user = await open();
    expect(screen.getByRole("checkbox", { name: "Measure identity" })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: "Measure Density suitability" })).not.toBeChecked();
    await user.click(screen.getByText("identity thresholds"));
    expect(screen.getByLabelText("identity: Minimum confidence (%)")).toBeEnabled();
    expect(screen.getByLabelText("identity: Minimum confidence (%)")).toHaveValue(97);
    expect(screen.getByRole("button", { name: "Save threshold policy" })).toBeEnabled();
    await user.click(screen.getByRole("checkbox", { name: "Measure Density suitability" }));
    await user.click(screen.getByText("Density suitability thresholds"));
    expect(screen.getByLabelText("Density suitability: Minimum confidence (%)")).toHaveValue(90);
    await user.click(screen.getByRole("button", { name: "Save threshold policy" }));
    expect(post).toHaveBeenCalledWith(`${base}/policy`, expect.objectContaining({ review_hash: "labels-exact" }));
    expect(post.mock.calls[0][1].tasks).toMatchObject({ identity: { confidence: 0.97 }, density_transfer: { confidence: 0.9 } });
  });
  it("blocks runs with unsaved deselection and threshold edits until the saved scope refreshes", async () => {
    const limits = { confidence: 0.9, margin: 0.2, min_support: 20, min_groups: 5, max_false_positives: 0, max_p95_ms: 5000, max_failure_rate: 0.05, budget_unit: "requests" as const, max_requests: 500 };
    let savedTasks: Record<string, typeof limits> = { identity: { ...limits }, density_transfer: { ...limits } };
    get.mockImplementation(async () => ({ data: { ...corpus, frozen: true, reviewed_count: 2,
      cases: [corpus.cases[0], { ...corpus.cases[1], task: "density_transfer", split: "holdout" }],
      policy: { version: "v1", hash: "saved-policy", tasks: savedTasks },
    } }));
    post.mockImplementation(async (url, payload) => {
      if (url.endsWith("/policy")) savedTasks = payload.tasks;
      return { data: result };
    });
    const user = await open();
    await user.selectOptions(screen.getByLabelText("Evaluation method"), "classifier");
    await user.click(screen.getByRole("checkbox", { name: /I approve up to/ }));
    expect(screen.getByRole("button", { name: "Start evaluation" })).toBeEnabled();
    await user.click(screen.getByRole("checkbox", { name: "Measure Density suitability" }));
    await user.click(screen.getByRole("checkbox", { name: /I approve up to/ }));
    expect(screen.getByText("Planned selected cases: 1 training · 0 held out")).toBeInTheDocument();
    expect(screen.getByText("Saved run scope: 1 training · 1 held out")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Start evaluation" })).toBeDisabled();
    await user.click(screen.getByText("identity thresholds"));
    await user.clear(screen.getByLabelText("identity: Minimum confidence (%)"));
    await user.type(screen.getByLabelText("identity: Minimum confidence (%)"), "97");
    await user.click(screen.getByRole("button", { name: "Start evaluation" }));
    expect(post).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "Save threshold policy" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Start evaluation" })).toBeEnabled());
    expect(screen.getByText("Saved run scope: 1 training · 0 held out")).toBeInTheDocument();
    expect(savedTasks).toMatchObject({ identity: { confidence: 0.97 } });
    expect(Object.keys(savedTasks)).toEqual(["identity"]);
    expect(post.mock.calls.every(([url]) => !url.endsWith("/runs"))).toBe(true);
  });
  it("defaults to transfer tasks, counts only selected cases and saves only selected task keys", async () => {
    get.mockResolvedValue({ data: { ...corpus, frozen: true, reviewed_count: 3, case_count: 3, cases: [
      { ...corpus.cases[0], id: "nutrition", task: "nutrition_transfer", split: "train" },
      { ...corpus.cases[1], id: "density", task: "density_transfer", split: "holdout" },
      { ...corpus.cases[0], id: "identity", task: "identity", split: "train" },
    ] } });
    const user = await open();
    expect(screen.getByRole("checkbox", { name: "Measure Nutrition suitability" })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: "Measure Density suitability" })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: "Measure identity" })).not.toBeChecked();
    expect(screen.queryByText("identity thresholds")).not.toBeInTheDocument();
    await user.click(screen.getByText("Nutrition suitability thresholds"));
    expect(screen.getByLabelText("Nutrition suitability: Minimum confidence (%)")).toHaveValue(90);
    expect(screen.getByText("Planned selected cases: 1 training · 1 held out")).toBeInTheDocument();
    await user.click(screen.getByRole("checkbox", { name: "Measure Density suitability" }));
    expect(screen.getByText("Planned selected cases: 1 training · 0 held out")).toBeInTheDocument();
    expect(post).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "Save threshold policy" }));
    expect(Object.keys(post.mock.calls[0][1].tasks)).toEqual(["nutrition_transfer"]);
    expect(post.mock.calls[0][1].tasks.nutrition_transfer.confidence).toBe(0.9);
    expect(post.mock.calls[0][1]).toMatchObject({ review_hash: "labels-exact" });
  });
  it("disables policy save when no tasks are selected without approving labels", async () => {
    corpus = { ...corpus, frozen: true };
    const user = await open();
    await user.click(screen.getByRole("checkbox", { name: "Measure identity" }));
    expect(screen.getByRole("button", { name: "Save threshold policy" })).toBeDisabled();
    expect(screen.getByText("Planned selected cases: 0 training · 0 held out")).toBeInTheDocument();
    expect(post).not.toHaveBeenCalled();
  });
  it("freezes only fully reviewed labels and saves policy only by explicit action", async () => {
    corpus = { ...corpus, reviewed_count: 2 };
    const user = await open();
    post.mockImplementation(async (url) => {
      if (url.endsWith("/freeze")) corpus = { ...corpus, frozen: true };
      return { data: corpus };
    });
    await user.click(screen.getByRole("button", { name: "Freeze reviewed labels and split" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Save threshold policy" })).toBeEnabled());
    expect(post).toHaveBeenCalledTimes(1);
    await user.click(screen.getByText("identity thresholds"));
    await user.clear(screen.getByLabelText("identity: Minimum confidence (%)"));
    await user.type(screen.getByLabelText("identity: Minimum confidence (%)"), "97");
    await user.click(screen.getByRole("button", { name: "Save threshold policy" }));
    expect(post).toHaveBeenLastCalledWith(`${base}/policy`, expect.objectContaining({ review_hash: "labels-exact", tasks: expect.objectContaining({ identity: expect.objectContaining({ confidence: 0.97, budget_unit: "requests" }) }) }));
  });
  it("requires fresh bounded approval and labels fixtures and missing metrics honestly", async () => {
    const user = await open();
    await user.click(screen.getByRole("checkbox", { name: /I approve up to/ }));
    await user.clear(screen.getByLabelText("Maximum requests"));
    await user.type(screen.getByLabelText("Maximum requests"), "501");
    expect(screen.getByRole("button", { name: "Start evaluation" })).toBeDisabled();
    await user.clear(screen.getByLabelText("Maximum requests"));
    await user.type(screen.getByLabelText("Maximum requests"), "1");
    await user.click(screen.getByRole("checkbox", { name: /I approve up to/ }));
    await user.click(screen.getByRole("button", { name: "Start evaluation" }));
    await screen.findByText("Contract fixture — not quality evidence");
    expect(post).toHaveBeenCalledWith(`${base}/runs`, { engine: "rules", split: "train", max_requests: 1, max_seconds: 60, budget_confirmed: true }, expect.objectContaining({ signal: expect.any(AbortSignal) }));
    expect(screen.getByText(/Total USD: Unknown/)).toHaveTextContent("review corrections saved Unknown");
    expect(screen.getByText(/Insufficient groups/)).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: /I approve up to/ })).not.toBeChecked();
    expect(post).toHaveBeenCalledTimes(1);
  });
  it("blocks a provider until labels and thresholds are frozen", async () => {
    const user = await open();
    await user.selectOptions(screen.getByLabelText("Evaluation method"), "classifier");
    await user.click(screen.getByRole("checkbox", { name: /I approve up to/ }));
    expect(screen.getByRole("button", { name: "Start evaluation" })).toBeDisabled();
    expect(post).not.toHaveBeenCalled();
  });
  it("separates passed numerical checks from rollout approval", async () => {
    post.mockResolvedValue({ data: { ...result, summary: { ...result.summary, rollout: { identity: { eligible: false, numeric_checks_passed: true, checks: { minimum_support: true, benefit_review: null }, reasons: ["Independent evidence review is required"] } } } } });
    const user = await open();
    await user.click(screen.getByRole("checkbox", { name: /I approve up to/ }));
    await user.click(screen.getByRole("button", { name: "Start evaluation" }));
    await screen.findByText(/Measured checks passed; rollout still requires evidence review/);
    expect(screen.getByText(/benefit review: Unknown/)).toBeInTheDocument();
    expect(screen.getByText(/Rollout remains blocked/)).toBeInTheDocument();
    expect(screen.getByText("Contract fixture — not quality evidence")).toBeInTheDocument();
    expect(screen.queryByText(/Evidence gate passed;/)).not.toBeInTheDocument();
  });
  it("allows an explicit 358-request diagnostic within the server limit", async () => {
    const user = await open();
    await user.clear(screen.getByLabelText("Maximum requests"));
    await user.type(screen.getByLabelText("Maximum requests"), "358");
    await user.click(screen.getByRole("checkbox", { name: /I approve up to/ }));
    await user.click(screen.getByRole("button", { name: "Start evaluation" }));
    await screen.findByText("Contract fixture — not quality evidence");
    expect(post).toHaveBeenCalledWith(`${base}/runs`, expect.objectContaining({ max_requests: 358 }), expect.anything());
  });
  it("blocks even a rules run on draft held-out cases", async () => {
    const user = await open();
    await user.selectOptions(screen.getByLabelText("Evaluation split"), "holdout");
    await user.click(screen.getByRole("checkbox", { name: /I approve up to/ }));
    expect(screen.getByRole("button", { name: "Start evaluation" })).toBeDisabled();
    expect(post).not.toHaveBeenCalled();
  });
  it("requires separate unchecked confirmation to reopen the exact frozen labels", async () => {
    corpus = { ...corpus, frozen: true, reviewed_count: 2, holdout_exposed: true };
    const user = await open();
    expect(screen.getByRole("button", { name: "Correct reviewed labels" })).toBeDisabled();
    const confirmation = screen.getByRole("checkbox", { name: /correcting labels clears/ });
    expect(confirmation).not.toBeChecked();
    expect(screen.getByRole("button", { name: "Save threshold policy" })).toBeDisabled();
    await user.click(confirmation);
    post.mockImplementation(async () => { corpus = { ...corpus, frozen: false, reviewed_count: 0 }; return { data: corpus }; });
    await user.click(screen.getByRole("button", { name: "Correct reviewed labels" }));
    await screen.findByText("0 of 2 cases reviewed · Draft labels");
    expect(post).toHaveBeenCalledWith(`${base}/corpus/reopen`, { review_hash: "labels-exact", confirm: true });
    expect(screen.getByText(/choosing another policy requires a new independent/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save threshold policy" })).toBeDisabled();
    expect(screen.getByRole("checkbox", { name: /I approve up to/ })).not.toBeChecked();
  });
  it("loads a saved interrupted record for viewing without resuming it", async () => {
    const user = await open();
    get.mockImplementation(async (url) => ({ data: url.endsWith("/runs") ? { runs: [{ ...result, status: "interrupted" }] } : { ...result, status: "interrupted" } }));
    await user.click(screen.getByRole("button", { name: "Load recent saved runs" }));
    await user.click(await screen.findByRole("button", { name: "View saved run run-1" }));
    await screen.findByText("Run run-1: interrupted · 1/1");
    expect(post).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Cancel evaluation" })).toBeDisabled();
  });
  it("recovers from the saved-run cap through confirmed deletion and refreshed history", async () => {
    const user = await open();
    let saved = Array.from({ length: 20 }, (_, i) => ({ ...result, id: `old-${i}`, status: "interrupted" }));
    get.mockImplementation(async (url) => ({ data: url.endsWith("/runs") ? { runs: saved } : { ...saved[0] } }));
    post.mockRejectedValueOnce(new Error("Saved run limit reached"));
    await user.click(screen.getByRole("checkbox", { name: /I approve up to/ }));
    await user.click(screen.getByRole("button", { name: "Start evaluation" }));
    await screen.findByText(/Evaluation stopped. Check its saved record/);
    expect(screen.getByText(/Up to 20 runs can be saved/)).toHaveTextContent("delete an old record to make room");
    await user.click(screen.getByRole("button", { name: "Load recent saved runs" }));
    await user.click(await screen.findByRole("button", { name: "View saved run old-0" }));
    await screen.findByText("Run old-0: interrupted · 1/1");
    await user.click(screen.getByRole("button", { name: "Delete saved run old-0" }));
    expect(remove).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "Keep saved run" }));
    expect(remove).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "Delete saved run old-0" }));
    remove.mockImplementation(async () => { saved = saved.slice(1); return { data: { deleted: true } }; });
    await user.click(screen.getByRole("button", { name: "Confirm delete saved run" }));
    await waitFor(() => expect(screen.queryByRole("button", { name: "View saved run old-0" })).not.toBeInTheDocument());
    expect(remove).toHaveBeenCalledTimes(1);
    expect(remove).toHaveBeenLastCalledWith(`${base}/runs/old-0`);
    expect(get).toHaveBeenCalledWith(`${base}/runs`);
    expect(get).toHaveBeenLastCalledWith(`${base}/corpus/compatibility`);
    expect(screen.queryByText("Run old-0: interrupted · 1/1")).not.toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: /^View saved run/ })).toHaveLength(19);
    await user.click(screen.getByRole("checkbox", { name: /I approve up to/ }));
    expect(screen.getByRole("button", { name: "Start evaluation" })).toBeEnabled();
    await user.click(screen.getByRole("button", { name: "Start evaluation" }));
    await screen.findByText("Run run-1: completed · 1/1");
    expect(post.mock.calls.filter(([url]) => url === `${base}/runs`)).toHaveLength(2);
  });
  it("blocks running-record deletion and preserves a record if the server rejects a stale delete", async () => {
    const user = await open();
    get.mockResolvedValue({ data: { runs: [{ ...result, id: "active", status: "running" }, { ...result, id: "stale", status: "ready" }] } });
    await user.click(screen.getByRole("button", { name: "Load recent saved runs" }));
    expect(await screen.findByRole("button", { name: "Delete saved run active" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "Delete saved run stale" }));
    remove.mockRejectedValue(new Error("Became active"));
    await user.click(screen.getByRole("button", { name: "Confirm delete saved run" }));
    await screen.findByText(/Could not delete or refresh saved runs/);
    expect(screen.getByRole("button", { name: "View saved run stale" })).toBeInTheDocument();
    expect(remove).toHaveBeenCalledTimes(1);
    expect(remove).toHaveBeenLastCalledWith(`${base}/runs/stale`);
  });
  it("shows a rejected review without treating it as confirmed", async () => {
    const user = await open();
    post.mockRejectedValue(new Error("stale"));
    await user.click(screen.getByRole("button", { name: "I reviewed this case" }));
    await screen.findByText("Could not save evaluation changes");
    expect(screen.getByText("0 of 2 cases reviewed · Draft labels")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Freeze reviewed labels and split" })).toBeDisabled();
  });
  it("aborts the active step and confirms cancellation on the server without another step", async () => {
    let stepSignal: AbortSignal | undefined;
    post.mockImplementation(async (url, _body, config) => {
      if (url.endsWith("/runs")) return { data: { ...result, status: "ready" } };
      if (url.endsWith("/step")) {
        stepSignal = config.signal;
        return new Promise((_resolve, reject) => { config.signal.addEventListener("abort", () => reject(new Error("cancelled"))); });
      }
      return { data: { ...result, status: "cancelled" } };
    });
    const user = await open();
    await user.click(screen.getByRole("checkbox", { name: /I approve up to/ }));
    await user.click(screen.getByRole("button", { name: "Start evaluation" }));
    await waitFor(() => expect(stepSignal).toBeDefined());
    await user.click(screen.getByRole("button", { name: "Cancel evaluation" }));
    await screen.findByText("Run run-1: cancelled · 1/1");
    expect(stepSignal?.aborted).toBe(true);
    expect(post).toHaveBeenCalledWith(`${base}/runs/run-1/cancel`);
    expect(post.mock.calls.filter(([url]) => url.endsWith("/step"))).toHaveLength(1);
  });
  it("stops an active step when leaving the page and never resumes automatically", async () => {
    const user = await open();
    let signal: AbortSignal | undefined;
    post.mockImplementation(async (url, _body, config) => {
      if (url.endsWith("/runs")) return { data: { ...result, status: "ready" } };
      if (url.endsWith("/step")) {
        signal = config.signal;
        return new Promise((_resolve, reject) => signal?.addEventListener("abort", () => reject(new Error("cancelled"))));
      }
      return { data: { ...result, status: "cancelled" } };
    });
    await user.click(screen.getByRole("checkbox", { name: /I approve up to/ }));
    await user.click(screen.getByRole("button", { name: "Start evaluation" }));
    await waitFor(() => expect(signal).toBeDefined());
    cleanup();
    expect(signal?.aborted).toBe(true);
    expect(post).toHaveBeenCalledWith(`${base}/runs/run-1/cancel`);
    const count = post.mock.calls.length;
    await open();
    expect(post).toHaveBeenCalledTimes(count);
  });
});
