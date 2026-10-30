import { Alert, Badge, Button, Checkbox, Group, NativeSelect, SimpleGrid, Stack, Table, Text, TextInput } from "@mantine/core";
import { useEffect, useRef, useState } from "react";

import { api } from "@/api/client";
import { getApiErrorMessage } from "@/api/errors";
import { CollapsibleCard } from "./CollapsibleCard";

const ROOT = "/api/ai/classifier/benchmark";
type ReviewCase = {
  id: string; edge_family: string; leakage_group: string; split: string; task: string;
  evidence: { subject: unknown; candidate_or_context: unknown }; proposed_label: string;
  label: string; reviewed: boolean;
  critical_negative?: boolean; review_reason?: string;
};
type TaskPolicy = {
  confidence: number; margin: number; min_support: number; min_groups: number;
  max_false_positives: number; max_p95_ms: number; max_failure_rate: number;
  budget_unit: "requests"; max_requests: number;
};
type Corpus = {
  corpus_hash: string; review_hash: string; version: string; status: string;
  adequacy: unknown; cases: ReviewCase[]; frozen: boolean; reviewed_count: number;
  case_count: number; label_options: Record<string, string[]>;
  policy: null | { version: string; hash: string; tasks: Record<string, TaskPolicy> };
  rubrics?: Record<string, { description: string; labels: Record<string, string> }>;
  limits?: { max_cases: number; max_requests: number; max_seconds: number };
  holdout_exposed?: boolean;
  freeze_kind?: "owner_authorized_diagnostic" | "human_reviewed" | null;
  diagnostic_policy?: TaskPolicy;
  diagnostic?: { authorization_hash: string; label_provenance: "ai_proposed_reference" | "mixed_reference"; selected_count: number; selected_reviewed_count: number; split_counts: { train: number; holdout: number }; split_used: { train: boolean; holdout: boolean } } | null;
};
type Compatibility = { upgrade_required: boolean; eligible: boolean; previous_corpus_hash: string | null; corpus_hash: string; detail: string };
type Metrics = {
  support: number; groups: number; correct: number; false_positives: number; misses: number;
  abstained: number; failures: number; coverage: number; accuracy: number;
  p50_ms: number | null; p95_ms: number | null; total_cost_usd: number | null;
  unknown_cost_count: number; input_tokens: number | null; output_tokens: number | null;
  candidate_retrieval_recall: number | null; review_corrections_saved: number | null;
  critical_false_positives?: number; confusion?: Record<string, number>;
  reliability?: { lower: number; upper: number; count: number; accuracy: number | null }[];
  unsupported?: number; unmeasured?: number; request_attempts?: number;
  insufficient_answers?: number; non_insufficient_coverage?: number;
};
type Run = {
  id: string; status: string; engine: string; evidence_kind: string; corpus_hash: string;
  review_hash: string; policy_hash: string | null; versions: Record<string, unknown>; split: string;
  budget: { max_requests: number; max_seconds: number; attempted: number; elapsed_seconds: number };
  completed: number; total: number; detail?: string;
  freeze_kind?: string; diagnostic_authorization_hash?: string; label_provenance?: string; metric_interpretation?: string;
  selected_reviewed_count?: number | null;
  summary: { by_task: Record<string, Metrics>; rollout: Record<string, { eligible: boolean; reasons: string[]; numeric_checks_passed?: boolean | null; checks?: Record<string, boolean | null> }> };
};
const unknown = (value: unknown) => value === null || value === undefined ? "Unknown" : String(value);
const finished = (run: Run) => ["completed", "cancelled", "interrupted"].includes(run.status);
const names: Record<string, string> = { culinary_identity: "Same-food identity", nutrition_transfer: "Nutrition suitability", density_transfer: "Density suitability", receipt_role: "Receipt line purpose", recipe_plausibility: "Recipe ingredient checks", contextual_substitution: "Substitution context", same: "Same food", different: "Different food", substitution: "Possible substitute", insufficient: "Not enough evidence", eligible: "Suitable", unsuitable: "Unsuitable", stock: "Stock item", adjustment: "Price adjustment", return: "Return", ingredient: "Ingredient", instruction: "Instruction", heading: "Heading", compound: "Combined ingredients", review_required: "Needs review" };
const friendly = (value: string) => names[value] ?? value.replaceAll("_", " ");
const taskQuestions: Record<string, string> = {
  culinary_identity: "Do these descriptions refer to the same ingredient in the same meaningful form?",
  nutrition_transfer: "Is this reference food suitable for transferring nutrition to the original item, given its form and preparation? Missing details must not be assumed.",
  density_transfer: "Is this reference suitable for transferring density to the original item, given its physical form and preparation? A label does not authorize a numeric conversion.",
  receipt_role: "Is this a purchased food line, an adjustment or return, or too ambiguous?",
  recipe_plausibility: "Is this an ingredient, an instruction or heading, combined ingredients, or something needing review?",
  contextual_substitution: "Does this specific directional substitution need human review, or is there insufficient context? A label never approves a substitution.",
};
const checkName = (value: string) => value.replaceAll("_", " ");
const percent = (value: number | null | undefined) => value == null ? "Unknown" : `${(value * 100).toFixed(1)}%`;
const policySignature = (tasks: Record<string, TaskPolicy>) => JSON.stringify(Object.entries(tasks).sort(([left], [right]) => left.localeCompare(right)).map(([task, limits]) => [task, Object.entries(limits).sort(([left], [right]) => left.localeCompare(right))]));
const policyFields: { key: keyof Omit<TaskPolicy, "budget_unit" | "max_false_positives">; label: string; min: number; max: number; scale?: number }[] = [
  { key: "confidence", label: "Minimum confidence (%)", min: 0, max: 100, scale: 100 },
  { key: "margin", label: "Minimum lead over next answer (%)", min: 0, max: 100, scale: 100 },
  { key: "min_support", label: "Minimum measured cases", min: 1, max: 500 },
  { key: "min_groups", label: "Minimum related-case groups", min: 2, max: 500 },
  { key: "max_p95_ms", label: "Maximum p95 response time (ms)", min: 1, max: 8000 },
  { key: "max_failure_rate", label: "Maximum failure rate (%)", min: 0, max: 100, scale: 100 },
  { key: "max_requests", label: "Maximum provider requests", min: 1, max: 500 },
];

/** Human labels, frozen policy and explicit budgets precede any provider measurement. */
export function ClassifierEvaluationPanel() {
  const [corpus, setCorpus] = useState<Corpus | null>(null);
  const [compatibility, setCompatibility] = useState<Compatibility | null>(null);
  const [upgradeConfirmed, setUpgradeConfirmed] = useState(false);
  const [diagnosticConfirmed, setDiagnosticConfirmed] = useState(false);
  const [index, setIndex] = useState(0);
  const [label, setLabel] = useState("");
  const [policy, setPolicy] = useState<Record<string, TaskPolicy>>({});
  const [selectedTasks, setSelectedTasks] = useState<string[]>([]);
  const [engine, setEngine] = useState("rules");
  const [split, setSplit] = useState("train");
  const [requests, setRequests] = useState("1");
  const [seconds, setSeconds] = useState("60");
  const [confirmed, setConfirmed] = useState(false);
  const [reopenConfirmed, setReopenConfirmed] = useState(false);
  const [run, setRun] = useState<Run | null>(null);
  const [history, setHistory] = useState<Run[]>([]);
  const [deleteId, setDeleteId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const active = useRef<{ controller: AbortController; id: string | null; cancelled: boolean } | null>(null);
  const mounted = useRef(true);
  const reviewCase = corpus?.cases[index];
  const relatedGroups = corpus ? [...new Set(corpus.cases.map((entry) => entry.leakage_group))].sort() : [];

  async function loadCorpus() {
    const next = (await api.get<Corpus>(`${ROOT}/corpus`)).data;
    if (!mounted.current) return;
    setCorpus(next);
    const tasks = [...new Set(next.cases.map((entry) => entry.task))];
    setPolicy({ ...Object.fromEntries(
      tasks.map((task) => [task, {
        confidence: 0.90, margin: 0.2, min_support: 20, min_groups: 5,
        max_false_positives: 0, max_p95_ms: 5000, max_failure_rate: 0.05,
        budget_unit: "requests", max_requests: next.case_count,
      }]),
    ), ...next.policy?.tasks });
    const transfers = tasks.filter((task) => ["nutrition_transfer", "density_transfer"].includes(task));
    setSelectedTasks(next.policy ? Object.keys(next.policy.tasks) : transfers.length ? transfers : tasks);
    if (next.freeze_kind === "owner_authorized_diagnostic" && next.diagnostic_policy) {
      setPolicy((previous) => ({ ...previous, nutrition_transfer: next.diagnostic_policy!, density_transfer: next.diagnostic_policy! }));
      setSelectedTasks(["nutrition_transfer", "density_transfer"]);
    }
  }
  async function loadCompatibility() {
    const next = (await api.get<Compatibility>(`${ROOT}/corpus/compatibility`)).data;
    if (mounted.current) setCompatibility(next);
    return next;
  }
  useEffect(() => {
    mounted.current = true;
    void Promise.allSettled([loadCorpus(), loadCompatibility()]).then(([cases, compatible]) => {
      if (mounted.current && cases.status === "rejected" && !(compatible.status === "fulfilled" && compatible.value.upgrade_required)) {
        setError(getApiErrorMessage(cases.reason, "Could not load evaluation cases"));
      }
    });
    return () => {
      mounted.current = false;
      const current = active.current;
      if (current) {
        current.cancelled = true;
        current.controller.abort();
        if (current.id) void api.post(`${ROOT}/runs/${current.id}/cancel`).catch(() => undefined);
      }
    };
  }, []); // Loading never starts or resumes a run.
  useEffect(() => { setLabel(reviewCase?.label ?? reviewCase?.proposed_label ?? ""); }, [reviewCase]);
  useEffect(() => {
    if (corpus?.freeze_kind === "owner_authorized_diagnostic") {
      setEngine("classifier"); setRequests(split === "train" ? "140" : "66");
      setSeconds(split === "train" ? "1200" : "600"); setConfirmed(false);
    }
  }, [corpus?.freeze_kind, split]);

  async function change(action: () => Promise<unknown>) {
    setBusy(true); setError(null);
    try { await action(); await loadCorpus(); }
    catch (err) { setError(getApiErrorMessage(err, "Could not save evaluation changes")); }
    finally { setBusy(false); }
  }
  async function start() {
    if (policyDirty || diagnosticUsed) return;
    const current = { controller: new AbortController(), id: null as string | null, cancelled: false };
    active.current = current;
    setBusy(true); setError(null); setConfirmed(false);
    try {
      let next = (await api.post<Run>(`${ROOT}/runs`, {
        engine, split, max_requests: Number(requests), max_seconds: Number(seconds), budget_confirmed: confirmed,
      }, { signal: current.controller.signal })).data;
      current.id = next.id;
      if (mounted.current) setRun(next);
      if (isDiagnostic) await loadCorpus();
      if (current.cancelled) {
        await api.post(`${ROOT}/runs/${next.id}/cancel`);
        return;
      }
      while (!finished(next) && !current.cancelled) {
        next = (await api.post<Run>(`${ROOT}/runs/${next.id}/step`, undefined, { signal: current.controller.signal })).data;
        if (!current.cancelled && mounted.current) setRun(next);
      }
    } catch (err) {
      if (!current.cancelled && mounted.current) setError(getApiErrorMessage(err, "Evaluation stopped. Check its saved record before starting another run."));
    } finally {
      if (!current.cancelled) {
        if (active.current === current) active.current = null;
        if (mounted.current) setBusy(false);
      }
    }
  }
  async function cancel() {
    const current = active.current;
    if (!current) return;
    current.cancelled = true;
    current.controller.abort();
    try {
      if (current.id) {
        const next = (await api.post<Run>(`${ROOT}/runs/${current.id}/cancel`)).data;
        if (mounted.current) setRun(next);
      }
    } catch (err) { if (mounted.current) setError(getApiErrorMessage(err, "Cancellation could not be confirmed. Check the saved run.")); }
    finally {
      if (active.current === current) active.current = null;
      if (mounted.current) setBusy(false);
    }
  }
  async function refreshRuns() {
    setError(null); setBusy(true);
    try { setHistory((await api.get<{ runs: Run[] }>(`${ROOT}/runs`)).data.runs); }
    catch (err) { setError(getApiErrorMessage(err, "Could not load saved runs")); }
    finally { setBusy(false); }
  }
  async function viewRun(id: string) {
    setError(null); setBusy(true);
    try { setRun((await api.get<Run>(`${ROOT}/runs/${id}`)).data); }
    catch (err) { setError(getApiErrorMessage(err, "Could not load saved run")); }
    finally { setBusy(false); }
  }
  async function deleteRun(id: string) {
    setError(null); setBusy(true);
    try {
      await api.delete(`${ROOT}/runs/${id}`);
      setHistory((previous) => previous.filter((saved) => saved.id !== id));
      setRun((previous) => previous?.id === id ? null : previous);
      setDeleteId(null);
      setUpgradeConfirmed(false);
      const refreshed = await Promise.allSettled([
        api.get<{ runs: Run[] }>(`${ROOT}/runs`).then((response) => setHistory(response.data.runs)),
        loadCompatibility(),
      ]);
      for (const result of refreshed) if (result.status === "rejected") throw result.reason;
    } catch (err) { setError(getApiErrorMessage(err, "Could not delete or refresh saved runs. Refresh the list; an active run cannot be deleted.")); }
    finally { setBusy(false); }
  }
  const requestLimit = corpus?.limits?.max_requests ?? 500;
  const caseLimit = corpus?.limits?.max_cases ?? 500;
  const timeLimit = corpus?.limits?.max_seconds ?? 1800;
  const validBudget = Number.isInteger(Number(requests)) && Number(requests) >= 1 && Number(requests) <= requestLimit
    && Number.isInteger(Number(seconds)) && Number(seconds) >= 1 && Number(seconds) <= timeLimit;
  const providerReady = Boolean(corpus?.frozen && corpus.policy);
  const isDiagnostic = corpus?.freeze_kind === "owner_authorized_diagnostic";
  const diagnosticUsed = Boolean(isDiagnostic && corpus?.diagnostic?.split_used[split as "train" | "holdout"]);
  const diagnosticResult = run?.freeze_kind === "owner_authorized_diagnostic" || Boolean(run?.diagnostic_authorization_hash) || ["ai_proposed_reference", "mixed_reference"].includes(run?.label_provenance ?? "");
  const selectedPolicy = Object.fromEntries(selectedTasks.map((task) => [task, policy[task]]));
  const policyDirty = Boolean(corpus?.policy && policySignature(selectedPolicy) !== policySignature(corpus.policy.tasks));
  const support = corpus ? [...new Set(corpus.cases.map((entry) => `${entry.task}|${entry.split}`))].map((key) => {
    const [task, partition] = key.split("|");
    const rows = corpus.cases.filter((entry) => entry.task === task && entry.split === partition);
    return { key, task, partition, count: rows.length, groups: new Set(rows.map((entry) => entry.leakage_group)).size, critical: rows.filter((entry) => entry.critical_negative).length };
  }) : [];
  return (
    <CollapsibleCard id="classifier_evaluation" title="Classifier evaluation" status={<Badge color="orange">Review stays off</Badge>}>
      <Text size="sm">Choose a fully human-reviewed evaluation or explicitly authorize the fixed draft-reference diagnostic below. Save the task policy separately before measuring a provider. This evaluation never enables classifier review.</Text>
      <Alert color="yellow">Proposed labels are AI drafts until a person reviews each case. Test fixtures prove the connection contract, not model quality. Provider runs require saved classifier settings and cloud consent.</Alert>
      {error && <Alert color="red" role="alert">{error}</Alert>}
      {compatibility?.upgrade_required && <Alert color="orange"><Stack gap="xs">
        <Text>{compatibility.detail}</Text>
        {compatibility.eligible && <>
          <Checkbox label="Replace the unreviewed draft proposals with version 2; this does not confirm human labels" checked={upgradeConfirmed} disabled={busy} onChange={(e) => setUpgradeConfirmed(e.currentTarget.checked)} />
          <Button disabled={busy || !upgradeConfirmed} onClick={() => change(async () => {
            await api.post(`${ROOT}/corpus/upgrade`, { previous_corpus_hash: compatibility.previous_corpus_hash, confirm: true });
            setUpgradeConfirmed(false); setConfirmed(false);
            await loadCompatibility();
          })}>Use revised AI draft cases</Button>
        </>}
      </Stack></Alert>}
      {!corpus ? !compatibility?.upgrade_required && <Text>Loading evaluation cases…</Text> : <>
        <Text>{corpus.reviewed_count} of {corpus.case_count} cases reviewed · {isDiagnostic ? "Diagnostic snapshot frozen" : corpus.frozen ? "Labels frozen" : "Draft labels"}</Text>
        {!corpus.frozen && ["nutrition_transfer", "density_transfer"].every((task) => corpus.cases.some((entry) => entry.task === task)) && <Alert color="orange"><Stack gap="xs">
          <Text fw={500}>Optional owner-authorized diagnostic</Text>
          <Text>Use only nutrition and density suitability cases with provisional references: existing human reviews are preserved and the remaining labels stay AI proposals. This does not confirm additional human labels. Results measure draft agreement, not human-ground-truth accuracy. Each split can be started once; cancellation, deletion or expiry does not restore that allowance.</Text>
          <Checkbox label="I authorize a fixed diagnostic snapshot of provisional nutrition and density references, without confirming additional human labels" checked={diagnosticConfirmed} disabled={busy} onChange={(e) => setDiagnosticConfirmed(e.currentTarget.checked)} />
          <Button disabled={busy || !diagnosticConfirmed} onClick={() => change(async () => {
            await api.post(`${ROOT}/corpus/diagnostic-freeze`, { review_hash: corpus.review_hash, confirm: true });
            setDiagnosticConfirmed(false); setConfirmed(false);
          })}>Use smaller diagnostic test</Button>
        </Stack></Alert>}
        {isDiagnostic && <Alert color="orange"><Stack gap="xs">
          <Text fw={500}>Provisional-reference diagnostic — review remains off</Text>
          <Text>{corpus.diagnostic?.selected_count} selected cases; {corpus.diagnostic?.selected_reviewed_count} selected cases human reviewed; {corpus.diagnostic ? corpus.diagnostic.selected_count - corpus.diagnostic.selected_reviewed_count : "Unknown"} AI-proposed references. Existing human confirmations are unchanged. This snapshot measures agreement with provisional references, not human-ground-truth accuracy.</Text>
          <Text>Fixed plan: {corpus.diagnostic?.split_counts.train} training cases, 140 requests, 1200 seconds; {corpus.diagnostic?.split_counts.holdout} held-out cases, 66 requests, 600 seconds. Each split has one permanent use, including cancelled or deleted runs.</Text>
          <Text>The authorized 207-request allocation includes one separate connection test outside this benchmark, plus the 140 training and 66 held-out requests.</Text>
          <Text>Training allowance: {corpus.diagnostic?.split_used.train ? "Used" : "Unused"} · Held-out allowance: {corpus.diagnostic?.split_used.holdout ? "Used" : "Unused"}</Text>
          <details><summary>Diagnostic authorization identifier</summary><Text size="xs" style={{ overflowWrap: "anywhere" }}>{corpus.diagnostic?.authorization_hash}</Text></details>
        </Stack></Alert>}
        <Button variant="subtle" disabled={busy} onClick={() => change(async () => undefined)}>Refresh cases and review status</Button>
        <details><summary>Evidence identifiers</summary><Text size="xs" style={{ overflowWrap: "anywhere" }}>Case set: {corpus.corpus_hash}<br />Labels: {corpus.review_hash}<br />Threshold policy: {corpus.policy?.hash ?? "Not saved"}</Text></details>
        <Text size="sm">Case set checks: {JSON.stringify(corpus.adequacy)}</Text>
        <Table.ScrollContainer minWidth={450}><Table captionSide="top"><Table.Caption>Case coverage before measurement</Table.Caption><Table.Thead><Table.Tr><Table.Th>Task / split</Table.Th><Table.Th>Cases</Table.Th><Table.Th>Related groups</Table.Th><Table.Th>Critical negatives</Table.Th></Table.Tr></Table.Thead><Table.Tbody>{support.map((row) => <Table.Tr key={row.key}><Table.Td>{friendly(row.task)} / {row.partition === "train" ? "Training" : "Held out"}</Table.Td><Table.Td>{row.count}</Table.Td><Table.Td>{row.groups}</Table.Td><Table.Td>{row.critical}</Table.Td></Table.Tr>)}</Table.Tbody></Table></Table.ScrollContainer>
        <NativeSelect label="Evaluation case" value={String(index)} onChange={(e) => setIndex(Number(e.currentTarget.value))}
          data={corpus.cases.map((entry, i) => ({ value: String(i), label: `${i + 1}. ${entry.id}${entry.reviewed ? " — reviewed" : " — draft"}` }))} />
        <Group><Button variant="light" disabled={index === 0} onClick={() => setIndex((current) => current - 1)}>Previous case</Button><Button variant="light" disabled={index >= corpus.cases.length - 1} onClick={() => setIndex((current) => current + 1)}>Next case</Button></Group>
        {reviewCase && <Stack gap="xs">
          <Text fw={500}>{friendly(reviewCase.edge_family)} · {friendly(reviewCase.task)} · {reviewCase.split === "train" ? "Training" : "Held out"}</Text>
          <Text fw={600}>Question: {taskQuestions[reviewCase.task] ?? "Which label best describes the supplied evidence for this task?"}</Text>
          {corpus.rubrics?.[reviewCase.task] && <><Text size="sm">{corpus.rubrics[reviewCase.task].description}</Text>{Object.entries(corpus.rubrics[reviewCase.task].labels).map(([option, definition]) => <Text key={option} size="sm"><strong>{friendly(option)}:</strong> {definition}</Text>)}</>}
          <details><summary>How test cases are grouped</summary><Text size="sm">Related-case group {relatedGroups.indexOf(reviewCase.leakage_group) + 1}. This is an internal grouping identifier, not a food category or predicted answer, and is not sent to the model. Related cases stay together to avoid overlap between training and held-out groups.</Text></details>
          <Text><strong>Original text:</strong> {String(reviewCase.evidence.subject)}</Text>
          <Text><strong>Candidate or context:</strong> {String(reviewCase.evidence.candidate_or_context)}</Text>
          <details><summary>Technical evidence details</summary><Text component="pre" style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{JSON.stringify(reviewCase.evidence, null, 2)}</Text></details>
          <Text>AI proposal: {friendly(reviewCase.proposed_label)} · {reviewCase.reviewed ? "Human reviewed" : "Not human reviewed"}</Text>
          <Text size="sm">Original source: AI proposed. Critical negative: {reviewCase.critical_negative ? "Yes" : "No"}.{reviewCase.review_reason ? ` Review reason: ${reviewCase.review_reason}` : ""}</Text>
          <NativeSelect label="Reviewed label" value={label} onChange={(e) => setLabel(e.currentTarget.value)} disabled={corpus.frozen || busy}
            data={(corpus.label_options?.[reviewCase.task] ?? [reviewCase.proposed_label]).map((option) => ({ value: option, label: friendly(option) }))} />
          <Button disabled={busy || corpus.frozen || !label} onClick={() => change(() => api.post(`${ROOT}/corpus/review`, {
            corpus_hash: corpus.corpus_hash, review_hash: corpus.review_hash, case_id: reviewCase.id, label, confirm: true,
          }))}>I reviewed this case</Button>
        </Stack>}
        <Button disabled={busy || corpus.frozen || corpus.reviewed_count !== corpus.case_count} onClick={() => change(() => api.post(`${ROOT}/corpus/freeze`, { review_hash: corpus.review_hash, confirm: true }))}>Freeze reviewed labels and split</Button>
        {corpus.frozen && !isDiagnostic && <>
          <Checkbox label="I understand correcting labels clears all human review confirmations and the saved policy" checked={reopenConfirmed} disabled={busy} onChange={(e) => setReopenConfirmed(e.currentTarget.checked)} />
          <Button variant="default" disabled={busy || !reopenConfirmed} onClick={() => change(async () => {
            await api.post(`${ROOT}/corpus/reopen`, { review_hash: corpus.review_hash, confirm: true });
            setReopenConfirmed(false); setConfirmed(false);
          })}>Correct reviewed labels</Button>
        </>}
        {corpus.holdout_exposed && <Alert color="orange">Held-out cases have already been measured. Labels can be corrected, but choosing another policy requires a new independent case-set version.</Alert>}
        <Text fw={500}>Task thresholds</Text><Text size="sm">{isDiagnostic ? "This diagnostic uses a fixed task policy. Save it separately before starting; task selection and thresholds cannot be tuned." : "These editable starting proposals have not been validated. Choose separate limits for each task using training results. Up to 20 policy revisions are allowed before held-out measurement locks the policy. False positive allowance is zero."}</Text>
        <Text fw={500}>Tasks to measure</Text>
        <Text size="sm">Task selection limits measurement; it does not confirm draft labels. Save the selected tasks in the policy before measurement. {isDiagnostic ? "The owner-authorized diagnostic keeps draft-reference provenance and does not add human reviews." : "The human-reviewed path requires individual human review of every case before freezing."}</Text>
        {Object.keys(policy).map((task) => <Checkbox key={task} label={`Measure ${friendly(task)}`} checked={selectedTasks.includes(task)} disabled={busy || isDiagnostic || corpus.holdout_exposed} onChange={(e) => {
          const checked = e.currentTarget.checked;
          setSelectedTasks((previous) => checked ? [...previous, task] : previous.filter((entry) => entry !== task));
          setConfirmed(false);
        }} />)}
        <Text>Planned selected cases: {corpus.cases.filter((entry) => selectedTasks.includes(entry.task) && entry.split === "train").length} training · {corpus.cases.filter((entry) => selectedTasks.includes(entry.task) && entry.split === "holdout").length} held out</Text>
        {corpus.policy && <Text>Saved run scope: {corpus.cases.filter((entry) => entry.task in corpus.policy!.tasks && entry.split === "train").length} training · {corpus.cases.filter((entry) => entry.task in corpus.policy!.tasks && entry.split === "holdout").length} held out</Text>}
        {policyDirty && <Alert color="orange">Task selection or thresholds have unsaved changes. Save task selection and thresholds before starting. Planned counts describe the draft; saved run scope remains unchanged until saving succeeds.</Alert>}
        {Object.entries(policy).filter(([task]) => selectedTasks.includes(task)).map(([task, limits]) => <details key={task}><summary>{friendly(task)} thresholds</summary><fieldset disabled={busy || isDiagnostic || !corpus.frozen || corpus.holdout_exposed}><legend>{friendly(task)}</legend><SimpleGrid cols={{ base: 1, sm: 2 }}>{policyFields.map((field) => <TextInput key={field.key} label={`${friendly(task)}: ${field.label}`} type="number" min={field.min} max={field.key === "max_requests" ? requestLimit : ["min_support", "min_groups"].includes(field.key) ? caseLimit : field.max} step={field.scale ? 0.1 : 1} value={limits[field.key] * (field.scale ?? 1)} onChange={(e) => { const value = Number(e.currentTarget.value) / (field.scale ?? 1); setPolicy((previous) => ({ ...previous, [task]: { ...previous[task], [field.key]: value } })); }} />)}</SimpleGrid></fieldset></details>)}
        <Button disabled={busy || !corpus.frozen || corpus.holdout_exposed || selectedTasks.length === 0 || Boolean(isDiagnostic && corpus.policy)} onClick={() => change(() => api.post(`${ROOT}/policy`, { review_hash: corpus.review_hash, tasks: selectedPolicy }))}>Save threshold policy</Button>
        <NativeSelect label="Evaluation method" value={engine} disabled={busy || isDiagnostic} onChange={(e) => { setEngine(e.currentTarget.value); setConfirmed(false); }} data={[
          { value: "rules", label: "Rules diagnostic (no provider calls)" },
          { value: "classifier", label: "Configured classifier" },
          { value: "legacy_judge", label: "Existing food judge" },
        ]} />
        <NativeSelect label="Evaluation split" value={split} disabled={busy} onChange={(e) => { setSplit(e.currentTarget.value); setConfirmed(false); }} data={[{ value: "train", label: "Training cases" }, { value: "holdout", label: "Held-out cases" }]} />
        <Group grow>
          <TextInput label="Maximum requests" type="number" min={1} max={requestLimit} value={requests} disabled={busy || isDiagnostic} onChange={(e) => { setRequests(e.currentTarget.value); setConfirmed(false); }} />
          <TextInput label="Maximum seconds" type="number" min={1} max={timeLimit} value={seconds} disabled={busy || isDiagnostic} onChange={(e) => { setSeconds(e.currentTarget.value); setConfirmed(false); }} />
        </Group>
        <Checkbox label={`I approve up to ${requests} requests over ${seconds} seconds for this run`} checked={confirmed} disabled={busy} onChange={(e) => setConfirmed(e.currentTarget.checked)} />
        {engine !== "rules" && !providerReady && <Text c="orange">{isDiagnostic ? "Save the fixed diagnostic policy before starting." : "Freeze all reviewed labels and save thresholds before a provider run."}</Text>}
        {split === "holdout" && <Text size="sm">{isDiagnostic ? "Held-out diagnostic requires the locked provisional reference snapshot and its fixed policy." : "Held-out evidence requires frozen labels and a policy chosen before the run."} The server checks task support, related-case groups and safety limits.</Text>}
        {diagnosticUsed && <Text c="orange">This diagnostic split allowance has been used. It cannot be repeated or restored by deleting its run.</Text>}
        <Group><Button disabled={busy || diagnosticUsed || policyDirty || !confirmed || !validBudget || ((engine !== "rules" || split === "holdout") && !providerReady)} onClick={start}>Start evaluation</Button>
          <Button variant="default" disabled={!busy || !active.current} onClick={cancel}>Cancel evaluation</Button></Group>
      </>}
      <Button variant="subtle" disabled={busy} onClick={refreshRuns}>Load recent saved runs</Button>
      <Text size="sm">Up to 20 runs can be saved. If that limit is reached, load recent saved runs and delete an old record to make room.</Text>
      {history.map((saved) => <Group key={saved.id}><Text size="sm">{saved.engine} · {saved.split} · {saved.status} · {saved.id}</Text><Button variant="light" disabled={busy} onClick={() => viewRun(saved.id)}>View saved run {saved.id}</Button><Button variant="subtle" color="red" disabled={busy || saved.status === "running"} onClick={() => setDeleteId(saved.id)}>Delete saved run {saved.id}</Button></Group>)}
      {deleteId && <Alert color="orange" title="Delete saved evaluation?">
        <Text>Deleting run {deleteId} permanently removes its saved results.</Text>
        <Group><Button color="red" disabled={busy} onClick={() => deleteRun(deleteId)}>Confirm delete saved run</Button><Button variant="default" disabled={busy} onClick={() => setDeleteId(null)}>Keep saved run</Button></Group>
      </Alert>}
      {run && <Stack gap="xs">
        <Text fw={500}>Run {run.id}: {run.status} · {run.completed}/{run.total}</Text>
        {diagnosticResult && <Alert color="orange">Agreement with provisional synthetic references; not human-ground-truth accuracy. This run includes {unknown(run.selected_reviewed_count)} human-reviewed references and {run.selected_reviewed_count == null ? "Unknown" : run.total - run.selected_reviewed_count} AI-proposed references. This does not confirm additional human labels or approve classifier rollout.</Alert>}
        <Badge color={run.evidence_kind === "live_provider" ? "blue" : "orange"}>{run.evidence_kind === "contract_fixture" ? "Contract fixture — not quality evidence" : run.evidence_kind === "live_provider" ? "Live provider evidence" : `${run.evidence_kind} — diagnostic only`}</Badge>
        <Text size="xs" style={{ overflowWrap: "anywhere" }}>Case set: {run.corpus_hash}<br />Labels: {run.review_hash}<br />Policy: {run.policy_hash ?? "Not saved"}<br />Versions: {JSON.stringify(run.versions)}</Text>
        <Text>Requests attempted: {run.budget.attempted}/{run.budget.max_requests} · Seconds: {run.budget.elapsed_seconds}/{run.budget.max_seconds}</Text>
        {run.detail && <Text>{run.detail}</Text>}
        <Text size="sm">{diagnosticResult ? "Draft agreement counts matches to provisional references" : "Accuracy counts correct answers"} across all attempted cases, including abstentions and failures. Coverage is the share receiving an accepted answer. Median and p95 use nearest-rank timing across all timed attempts, including failures. {diagnosticResult ? "Agreement by confidence includes accepted answers only; this is not calibrated human-ground-truth reliability." : "Confidence reliability includes accepted answers only."}</Text>
        <Table.ScrollContainer minWidth={650}><Table><Table.Thead><Table.Tr><Table.Th>Task</Table.Th><Table.Th>Results</Table.Th><Table.Th>Timing</Table.Th><Table.Th>Cost and usage</Table.Th></Table.Tr></Table.Thead><Table.Tbody>
          {Object.entries(run.summary.by_task).map(([task, metrics]) => <Table.Tr key={task}><Table.Td>{friendly(task)}</Table.Td><Table.Td>
            Support {metrics.support}, groups {metrics.groups}; {diagnosticResult ? "reference matches" : "correct"} {metrics.correct}, {diagnosticResult ? "draft-reference false positives" : "false positives"} {metrics.false_positives} (critical {unknown(metrics.critical_false_positives)}), misses {metrics.misses}, abstentions {metrics.abstained}, failures {metrics.failures}; unsupported {unknown(metrics.unsupported)}, unmeasured {unknown(metrics.unmeasured)}, provider attempts {unknown(metrics.request_attempts)}; accepted “not enough evidence” answers {unknown(metrics.insufficient_answers)}; {diagnosticResult ? "draft agreement" : "accuracy"} {percent(metrics.accuracy)}, accepted-answer coverage {percent(metrics.coverage)}, coverage excluding “not enough evidence” {percent(metrics.non_insufficient_coverage)}
          </Table.Td><Table.Td>Median {unknown(metrics.p50_ms)} ms; p95 {unknown(metrics.p95_ms)} ms</Table.Td><Table.Td>
            Total USD: {unknown(metrics.total_cost_usd)}; unknown-cost attempts {metrics.unknown_cost_count}; input tokens {unknown(metrics.input_tokens)}, output tokens {unknown(metrics.output_tokens)}; retrieval recall {unknown(metrics.candidate_retrieval_recall)}; review corrections saved {unknown(metrics.review_corrections_saved)}
          </Table.Td></Table.Tr>)}
        </Table.Tbody></Table></Table.ScrollContainer>
        {Object.entries(run.summary.by_task).map(([task, metrics]) => <details key={task}><summary>{friendly(task)}: confusion counts and {diagnosticResult ? "draft agreement by confidence" : "confidence reliability"}</summary><Text component="pre" style={{ whiteSpace: "pre-wrap" }}>{JSON.stringify(diagnosticResult ? { confusion: metrics.confusion ?? null, draft_agreement_by_confidence: metrics.reliability?.map(({ accuracy, ...bucket }) => ({ ...bucket, draft_agreement: accuracy })) ?? null } : { confusion: metrics.confusion ?? null, reliability: metrics.reliability ?? null }, null, 2)}</Text></details>)}
        {Object.entries(run.summary.rollout).map(([task, gate]) => <Stack key={task} gap="xs">
          <Text fw={500}>{friendly(task)}: {gate.numeric_checks_passed === true ? "Measured checks passed; rollout still requires evidence review" : gate.numeric_checks_passed === false ? "Measured checks have not passed" : "Measured checks unknown"}</Text>
          {gate.checks && <Text size="sm">{Object.entries(gate.checks).map(([name, passed]) => `${checkName(name)}: ${passed === null ? "Unknown" : passed ? "Passed" : "Not passed"}`).join(" · ")}</Text>}
          <Text>Rollout remains blocked. {gate.reasons.join("; ")}</Text>
        </Stack>)}
      </Stack>}
    </CollapsibleCard>
  );
}
