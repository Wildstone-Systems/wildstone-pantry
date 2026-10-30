import { Alert, Anchor, Badge, Button, Checkbox, Group, Modal, Stack, Text, Textarea, TextInput } from "@mantine/core";
import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { getApiErrorMessage } from "@/api/errors";
import { ClassifierVerificationNotice } from "@/components/ClassifierVerificationNotice";
import { ROUTES } from "@/routes/paths";
import { useAuth } from "@/state/AuthProvider";
import {
  actOnMatchReview,
  createMatchReview,
  readProductEvidence,
  saveProductEvidence,
  type ProductFacts,
  type ProductEvidence,
  useMatchCandidates,
  useMatchReviewHistory,
  type EvidenceFacts,
  type MatchItemEvidence,
  type MatchReview,
} from "../hooks/ingredient-match-hooks";

const factFields = [
  {
    key: "identity",
    label: "What is the product?",
    limit: 200,
    hint: "Read the label, for example chicken breast. Leave unknown facts blank.",
  },
  {
    key: "base_ingredient",
    label: "Base ingredient (optional)",
    limit: 200,
    hint: "Named base food, if known; no catalog alias is created.",
  },
  { key: "form", label: "Physical form", limit: 200, hint: "For example boneless breast, whole seeds or fine powder." },
  { key: "preparation", label: "Preparation", limit: 200, hint: "For example raw, cooked, frozen, dried or canned." },
  {
    key: "composition",
    label: "Ingredients or composition",
    limit: 1000,
    hint: "Copy known label ingredients; do not guess.",
  },
  {
    key: "recipe_role",
    label: "Recipe role or requirements",
    limit: 500,
    hint: "Relevant recipe instructions or preferences.",
  },
] as const;
type ProductField = (typeof factFields)[number] & { key: keyof ProductFacts };
const productFields = factFields.filter((field): field is ProductField => field.key !== "recipe_role");
function recipeOnlyFacts(facts: EvidenceFacts): EvidenceFacts {
  return Object.prototype.hasOwnProperty.call(facts, "recipe_role") ? { recipe_role: facts.recipe_role } : {};
}
const sourceLabels: Record<string, string> = {
  user_provided: "Provided by you for this review",
  saved_user_provided: "Saved product details (user provided)",
  catalog_name: "Catalog name",
  catalog_ingredients: "Catalog ingredients",
  recipe_context: "Recipe text",
  unknown: "Unknown",
};
const reasonLabels: Record<string, string> = {
  missing_identity_evidence: "Product identity or preparation details are missing.",
  source_changed: "Product or recipe details changed.",
  policy_changed: "Classifier settings or review policy changed.",
  review_expired: "This review expired.",
  chosen_item_changed: "The recipe choice changed in another review.",
  known_composition_conflict: "The product composition conflicts with the recipe requirements.",
  known_recipe_structure_conflict: "The product conflicts with the recipe structure.",
  known_preparation_conflict: "The preparation conflicts with the recipe requirements.",
  model_recipe_not_suitable: "The classifier does not recommend this product for the recipe.",
  manual_choice_despite_known_conflict: "A human chose this item despite a recorded conflict.",
};
const labels: Record<string, Record<string, string>> = {
  identification: {
    same: "Same ingredient and form",
    same_base: "Same base ingredient, meaningful differences",
    different: "Different named ingredient",
    insufficient: "Product identity needs clarification",
  },
  recipe_matching: {
    direct: "Direct recipe match",
    possible: "Possible recipe match",
    not_suitable: "Not recommended for this recipe",
    insufficient: "More information needed",
  },
  adjustment: {
    none: "No adjustment type identified",
    preparation: "Preparation adjustment",
    seasoning: "Seasoning adjustment",
    texture: "Texture adjustment",
    unknown: "Adjustment needs clarification",
  },
};
function ItemEvidence({ item, title }: { item: MatchItemEvidence; title: string }) {
  return (
    <Stack gap={3}>
      <Text fw={600}>
        {title}: {item.name}
      </Text>
      {item.brand && <Text size="sm">Brand: {item.brand}</Text>}
      <Text size="sm">Ingredients: {item.ingredients_text || "Unknown"}</Text>
      <Text size="sm">Product source: {item.source || "Unknown"}</Text>
      <Text size="sm">
        Nutrition evidence:{" "}
        {item.nutrition_estimated || item.nutrition_basis === "estimated"
          ? "Estimated reference"
          : item.nutrition_basis === "label_or_manual"
            ? "Label or manually entered"
            : "Unknown"}
        {item.nutrition_source ? ` · ${item.nutrition_source}` : ""}
      </Text>
      <details>
        <summary>Recorded nutrition values</summary>
        <Text size="xs">Existing catalog values only; matching does not verify or transfer them.</Text>
        {Object.entries(item.nutrition_values ?? {})
          .filter(([, value]) => value !== null)
          .map(([key, value]) => (
            <Text size="sm" key={key}>
              {key.replaceAll("_", " ")}: {value}
            </Text>
          ))}
        {!Object.values(item.nutrition_values ?? {}).some((value) => value !== null) && (
          <Text size="sm">No recorded values.</Text>
        )}
      </details>
    </Stack>
  );
}

/** A human-confirmed planning association, separate from global identity and stock. */
export function IngredientMatchReviewModal({
  recipeId,
  ingredientId,
  onClose,
}: {
  recipeId: string | null;
  ingredientId: string | null;
  onClose: () => void;
}) {
  const { isAdmin, account } = useAuth();
  const opened = Boolean(recipeId && ingredientId);
  const [search, setSearch] = useState("");
  const [query, setQuery] = useState("");
  const [candidate, setCandidate] = useState<MatchItemEvidence | null>(null);
  const [facts, setFacts] = useState<EvidenceFacts>({});
  const [clarification, setClarification] = useState<MatchReview["next_clarification"]>(null);
  const [editDetails, setEditDetails] = useState(false);
  const [editRole, setEditRole] = useState(false);
  const [editReviewFacts, setEditReviewFacts] = useState(false);
  const [productDraft, setProductDraft] = useState<ProductFacts>({});
  const [productDraftBinding, setProductDraftBinding] = useState<{
    itemId: string;
    evidence: ProductEvidence;
  } | null>(null);
  const [saveChecked, setSaveChecked] = useState(false);
  const [review, setReview] = useState<MatchReview | null>(null);
  // Only CREATE issues this capability. Compare/read responses cannot replace it.
  const [token, setToken] = useState<string | null>(null);
  const [checked, setChecked] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const request = useRef<AbortController | null>(null);
  const generation = useRef(0);
  const candidates = useMatchCandidates(recipeId, ingredientId, query, opened && isAdmin, account?.id);
  const history = useMatchReviewHistory(recipeId, ingredientId, opened && isAdmin, account?.id);
  const discard = useCallback(() => {
    generation.current += 1;
    request.current?.abort();
    request.current = null;
    setBusy(false);
    setReview(null);
    setToken(null);
    setChecked(false);
  }, []);
  useEffect(() => {
    discard();
    setCandidate(null);
    setFacts({});
    setClarification(null);
    setEditDetails(false);
    setEditRole(false);
    setEditReviewFacts(false);
    setProductDraft({});
    setProductDraftBinding(null);
    setSaveChecked(false);
    setSearch("");
    setQuery("");
    setMessage(null);
    return () => {
      generation.current += 1;
      request.current?.abort();
    };
  }, [recipeId, ingredientId, isAdmin, account?.id, discard]);
  function close() {
    discard();
    setFacts({});
    onClose();
  }
  function failure(error: unknown) {
    const status = (error as { response?: { status?: number } })?.response?.status;
    if (status === 409) {
      discard();
      setClarification(null);
      setMessage(
        "Details or settings changed, or this review expired. Review the current details again before choosing. No choice was saved."
      );
    } else {
      discard();
      setMessage(
        getApiErrorMessage(
          error,
          "The request did not complete. Manual review is still available; check the saved status before trying again."
        )
      );
    }
  }
  async function create(selected = candidate, suppliedFacts = facts) {
    if (!recipeId || !ingredientId || !selected) return;
    const current = ++generation.current;
    const controller = new AbortController();
    request.current = controller;
    setBusy(true);
    setMessage(null);
    setChecked(false);
    setToken(null);
    setReview(null);
    try {
      const clean = Object.fromEntries(
        Object.entries(suppliedFacts).map(([key, value]) => [key, value?.trim() || null])
      );
      const next = await createMatchReview(recipeId, ingredientId, selected.item_id, clean, controller.signal);
      if (generation.current !== current) return;
      setCandidate(next.packet.product);
      setClarification(next.next_clarification ?? null);
      setReview(next);
      setToken(next.confirmation_token ?? null);
    } catch (error) {
      if (!controller.signal.aborted && generation.current === current) failure(error);
    } finally {
      if (generation.current === current) {
        setBusy(false);
        request.current = null;
      }
    }
  }
  async function act(action: "compare" | "confirm" | "reject") {
    if (!review || !token || (action === "confirm" && (!checked || !review.can_confirm))) return;
    const current = ++generation.current;
    const controller = new AbortController();
    request.current = controller;
    setBusy(true);
    setMessage(null);
    try {
      const next = await actOnMatchReview(
        review.id,
        action,
        { revision: review.revision, evidence_hash: review.evidence_hash, confirmation_token: token },
        controller.signal
      );
      if (generation.current !== current) return;
      setReview(next);
      setClarification(next.next_clarification ?? null);
      setChecked(false);
      if (next.stale || next.prediction_stale) {
        discard();
        setMessage(
          "Details or settings changed. Review the current details again; the old suggestions are no longer usable."
        );
      } else if (action !== "compare") {
        setToken(null);
        setMessage(
          action === "confirm"
            ? "Saved for this recipe's planning only. Stock, product identity and numeric references are unchanged."
            : "Left unresolved. No recipe item was chosen."
        );
        await history.refetch();
      }
    } catch (error) {
      if (!controller.signal.aborted && generation.current === current) failure(error);
    } finally {
      if (generation.current === current) {
        setBusy(false);
        request.current = null;
      }
    }
  }
  function selectCandidate(item: MatchItemEvidence) {
    const retainedFacts = recipeOnlyFacts(facts);
    discard();
    setCandidate(item);
    setFacts(retainedFacts);
    setClarification(null);
    setEditDetails(false);
    setEditRole(false);
    setEditReviewFacts(false);
    setSaveChecked(false);
    setMessage(null);
    void create(item, retainedFacts);
  }
  function openProductEditor() {
    const known = candidate?.structured_evidence;
    setProductDraftBinding(candidate && known ? { itemId: candidate.item_id, evidence: known } : null);
    const resolved = review?.packet.resolved ?? (known && !known.stale ? known.facts : {});
    setProductDraft(
      known?.stale ? {} : Object.fromEntries(productFields.map(({ key }) => [key, resolved[key] ?? null]))
    );
    setSaveChecked(false);
    setEditDetails(true);
  }
  async function refreshProduct() {
    if (!candidate) return;
    const selected = candidate;
    discard();
    const current = ++generation.current;
    const controller = new AbortController();
    request.current = controller;
    setBusy(true);
    setMessage(null);
    try {
      await readProductEvidence(selected.item_id, controller.signal);
      const refreshed = await candidates.refetch({ throwOnError: true });
      if (generation.current !== current || controller.signal.aborted) return;
      const freshItem = refreshed.data?.candidates.find(({ item }) => item.item_id === selected.item_id)?.item;
      setCandidate(freshItem ?? null);
      setFacts(recipeOnlyFacts);
      setClarification(null);
      setEditDetails(false);
      setSaveChecked(false);
      setMessage(
        freshItem
          ? "Product details refreshed. Check them, then review again."
          : "Find and select this product again to review its current details."
      );
    } catch (error) {
      if (generation.current === current && !controller.signal.aborted) failure(error);
    } finally {
      if (generation.current === current) {
        setBusy(false);
        request.current = null;
      }
    }
  }
  async function saveProduct() {
    if (
      !candidate?.structured_evidence ||
      !saveChecked ||
      !productDraftBinding ||
      productDraftBinding.itemId !== candidate.item_id
    ) return;
    const selected = candidate;
    const previous = productDraftBinding.evidence;
    discard();
    const current = ++generation.current;
    const controller = new AbortController();
    request.current = controller;
    setBusy(true);
    setMessage(null);
    setSaveChecked(false);
    const clean = Object.fromEntries(productFields.map(({ key }) => [key, productDraft[key]?.trim() || null]));
    try {
      const evidence = await saveProductEvidence(selected.item_id, previous, clean, controller.signal);
      if (generation.current !== current || controller.signal.aborted) return;
      setCandidate({ ...selected, structured_evidence: evidence });
      setFacts(recipeOnlyFacts);
      setClarification(null);
      setEditDetails(false);
      setMessage(
        "Product details saved for reuse across recipes. Review again before choosing; no comparison was started."
      );
      await history.refetch();
    } catch (error) {
      if (generation.current === current && !controller.signal.aborted) {
        failure(error);
        setEditDetails(false);
        setMessage(
          "The product details save could not be confirmed. Refresh the product details and recheck them before saving again."
        );
      }
    } finally {
      if (generation.current === current) {
        setBusy(false);
        request.current = null;
      }
    }
  }
  const currentChoice = history.data?.find((entry) => entry.current_choice && entry.status === "confirmed");
  const unresolved = review?.readiness === "needs_information";
  const negative =
    review?.readiness === "not_recommended" ||
    review?.predictions.some((entry) => entry.purpose === "recipe_matching" && entry.label === "not_suitable");
  return (
    <Modal
      opened={opened}
      onClose={close}
      title="Review an item for this recipe"
      size="lg"
      closeButtonProps={{ "aria-label": "Close product review" }}
    >
      <Stack>
        <ClassifierVerificationNotice />
        <Alert color="blue">
          This saves a recipe planning association only. It does not count stock as available, change a product's global
          identity, transfer nutrition or density, or choose cooking quantities.
        </Alert>
        {!isAdmin ? (
          <>
            <Text>
              An administrator must sign in to use evidence review. The existing manual recipe workflow remains
              available.
            </Text>
            <Anchor component={Link} to={ROUTES.admin} onClick={close}>
              Administrator sign-in
            </Anchor>
          </>
        ) : (
          <>
            {currentChoice && (
              <Alert color="green">
                Current recipe choice: {currentChoice.packet.product.name}. Human confirmed; planning only.
                {(currentChoice.stale || currentChoice.prediction_stale) &&
                  " Details or settings have changed since this confirmation; the prior human choice is retained."}
              </Alert>
            )}
            {history.isError && (
              <Alert color="orange">
                Saved choices could not be loaded. Refresh before replacing a previous choice.
              </Alert>
            )}
            {message && <Alert color="orange">{message}</Alert>}
            <TextInput
              label="Search catalog items"
              value={search}
              maxLength={200}
              disabled={busy}
              onChange={(e) => setSearch(e.currentTarget.value)}
            />
            <Button variant="default" disabled={busy} onClick={() => setQuery(search.trim())}>
              Find items
            </Button>
            {candidates.isLoading && <Text>Loading candidate items…</Text>}
            {candidates.isError && (
              <Alert color="orange">
                Candidate items could not be loaded. Refresh or use the existing manual recipe workflow.
              </Alert>
            )}
            {candidates.data?.candidates.length === 0 && (
              <Text>No candidate items found. Try another product name.</Text>
            )}
            {candidates.data?.candidates.map(({ item }) => (
              <Button
                key={item.item_id}
                variant={candidate?.item_id === item.item_id ? "light" : "subtle"}
                disabled={busy}
                onClick={() => selectCandidate(item)}
              >
                Review {item.name}
              </Button>
            ))}
            {candidate && (
              <>
                <ItemEvidence item={candidate} title="Pantry product" />
                {candidate.structured_evidence?.stale && (
                  <Alert color="orange">
                    Saved product details are out of date because the catalog source changed. They are not used. Recheck
                    the label before saving or using those details again.
                  </Alert>
                )}
                {candidate.structured_evidence?.stale && (
                  <details>
                    <summary>Previous saved details (not used)</summary>
                    {Object.entries(candidate.structured_evidence.facts)
                      .filter(([, value]) => value)
                      .map(([key, value]) => (
                        <Text key={key}>
                          {key.replaceAll("_", " ")}: {value}
                        </Text>
                      ))}
                  </details>
                )}
                {!review && candidate.structured_evidence && !candidate.structured_evidence.stale && (
                  <Stack gap={2}>
                    <Text fw={500}>Saved product details (user provided)</Text>
                    {Object.entries(candidate.structured_evidence.facts)
                      .filter(([, value]) => value)
                      .map(([key, value]) => (
                        <Text size="sm" key={key}>
                          {key.replaceAll("_", " ")}: {value}
                        </Text>
                      ))}
                  </Stack>
                )}
                {clarification && (
                  <Stack gap={3}>
                    <Text fw={600}>One detail is needed</Text>
                    <Textarea
                      label={clarification.question}
                      description="Use only details you checked. This answer applies to this review unless you separately save product details."
                      maxLength={200}
                      value={facts[clarification.field] ?? ""}
                      disabled={busy}
                      onChange={(event) => {
                        const value = event.currentTarget.value;
                        discard();
                        setFacts((previous) => ({ ...previous, [clarification.field]: value }));
                      }}
                      autosize
                      minRows={1}
                    />
                    <Button disabled={busy || !facts[clarification.field]?.trim()} onClick={() => create()}>
                      Use answer for this review
                    </Button>
                  </Stack>
                )}
                <Button variant="subtle" disabled={busy} onClick={() => setEditReviewFacts(!editReviewFacts)}>
                  Change details for this review
                </Button>
                {editReviewFacts && (
                  <Stack gap="xs">
                    <Text>
                      Only edited fields override the saved facts for this review. Mark a fact unknown if you cannot
                      confirm it. No product details are saved here.
                    </Text>
                    {productFields.map(({ key, label, limit }) => (
                      <Stack key={key} gap={2}>
                        <Textarea
                          label={`This review: ${label}`}
                          placeholder={
                            facts[key] === null
                              ? "Unknown for this review"
                              : review
                                ? `Current review: ${review.packet.resolved[key] || "Unknown"}`
                                : candidate.structured_evidence && !candidate.structured_evidence.stale
                                  ? `Saved product detail: ${candidate.structured_evidence.facts[key] || "Unknown"}`
                                  : "Unknown"
                          }
                          value={facts[key] ?? ""}
                          maxLength={limit}
                          disabled={busy}
                          onChange={(event) => {
                            const value = event.currentTarget.value;
                            discard();
                            setFacts((previous) => ({ ...previous, [key]: value }));
                          }}
                          autosize
                          minRows={1}
                        />
                        <Button
                          size="compact-xs"
                          variant="subtle"
                          disabled={busy}
                          onClick={() => {
                            discard();
                            setFacts((previous) => ({ ...previous, [key]: null }));
                          }}
                        >
                          Mark {label.toLowerCase()} unknown for this review
                        </Button>
                      </Stack>
                    ))}
                  </Stack>
                )}
                <Button variant="subtle" disabled={busy} onClick={() => setEditRole(!editRole)}>
                  Recipe-only requirements
                </Button>
                {editRole && (
                  <Textarea
                    label="Recipe role or requirements"
                    description="Only for this recipe review; never saved as product details."
                    maxLength={500}
                    disabled={busy}
                    value={facts.recipe_role ?? ""}
                    onChange={(event) => {
                      const value = event.currentTarget.value;
                      discard();
                      setFacts((previous) => ({ ...previous, recipe_role: value.trim() ? value : null }));
                    }}
                  />
                )}
                <Button disabled={busy} onClick={() => create()}>
                  Review these details
                </Button>
                <Button variant="default" disabled={busy} onClick={openProductEditor}>
                  Edit all product details
                </Button>
                {editDetails && (
                  <Stack gap="xs">
                    <Text>
                      Optional: save facts about this product for reuse across recipes. This does not create a global
                      ingredient alias or save recipe preferences.
                    </Text>
                    {productFields.map(({ key, label, limit, hint }) => (
                      <Textarea
                        key={key}
                        label={`Product details: ${label}`}
                        description={hint}
                        maxLength={limit}
                        disabled={busy}
                        value={productDraft[key] ?? ""}
                        onChange={(event) => {
                          const value = event.currentTarget.value;
                          discard();
                          setSaveChecked(false);
                          setProductDraft((previous) => ({ ...previous, [key]: value }));
                        }}
                        autosize
                        minRows={1}
                      />
                    ))}
                    {!Object.values(productDraft).some((value) => value?.trim()) &&
                    candidate.structured_evidence?.revision ? (
                      <Alert color="orange">Saving empty details clears the previously saved product facts.</Alert>
                    ) : null}
                    <Checkbox
                      label="I checked these product details and want to reuse them across recipes"
                      disabled={busy}
                      checked={saveChecked}
                      onChange={(event) => setSaveChecked(event.currentTarget.checked)}
                    />
                    <Group>
                      <Button disabled={busy || !saveChecked || !candidate.structured_evidence} onClick={saveProduct}>
                        Save product details
                      </Button>
                      <Button
                        variant="subtle"
                        disabled={busy}
                        onClick={() => {
                          setEditDetails(false);
                          setSaveChecked(false);
                        }}
                      >
                        Cancel product edits
                      </Button>
                    </Group>
                  </Stack>
                )}
                <Button variant="subtle" disabled={busy} onClick={refreshProduct}>
                  Refresh product details
                </Button>
              </>
            )}
            {review && (
              <>
                <Text fw={600}>Recipe: {review.packet.recipe_name}</Text>
                <Text>
                  Requested: {review.packet.quantity} {review.packet.unit} {review.packet.recipe_ingredient_line}
                </Text>
                <ItemEvidence item={review.packet.requested} title="Requested ingredient" />
                <Text size="sm" style={{ whiteSpace: "pre-wrap" }}>
                  Recipe instructions: {review.packet.recipe_instructions || "Not supplied"}
                </Text>
                <Text fw={500}>Facts used for this review</Text>
                {factFields.map(({ key, label }) => (
                  <Text size="sm" key={key}>
                    {label}: {review.packet.resolved[key] || "Unknown"} ·{" "}
                    {sourceLabels[review.packet.fact_sources[key]] ?? "Unknown"}
                  </Text>
                ))}
                {unresolved && (
                  <Alert color="orange">
                    More product details are needed before choosing this item. Check{" "}
                    {review.missing_fields.length
                      ? review.missing_fields.join(", ")
                      : "the product identity and preparation"}
                    , then update the facts and review again. No replacement is recommended.
                  </Alert>
                )}
                {negative && (
                  <Alert color="red">
                    The evidence or classifier reports a recipe conflict. This is not a recommended match. A manual
                    choice, if allowed, requires your deliberate review of that conflict.
                  </Alert>
                )}
                {review.reason_codes.length > 0 && (
                  <Stack gap={2}>
                    {review.reason_codes.map((reason) => (
                      <Text size="sm" key={reason}>
                        {reasonLabels[reason] ??
                          "This review requires manual attention; classifier assistance may be unavailable."}
                      </Text>
                    ))}
                  </Stack>
                )}
                <Text size="sm">
                  Review threshold: {(review.policy.confidence * 100).toFixed(0)}%. Every choice requires your
                  confirmation. A model score is not measured accuracy.
                </Text>
                {!review.policy.enabled && (
                  <Text>
                    Classifier assistance is disabled. You can still review the evidence and choose manually when the
                    required facts are present.
                  </Text>
                )}
                {review.predictions.map((prediction) => (
                  <Stack key={prediction.purpose} gap={2}>
                    <Text fw={500}>
                      {prediction.purpose === "identification"
                        ? "Product identity"
                        : prediction.purpose === "recipe_matching"
                          ? "Recipe match"
                          : "Adjustment type"}
                      : {labels[prediction.purpose]?.[prediction.label] ?? "Unrecognized result; review manually"}
                    </Text>
                    <Badge color={prediction.clears_threshold ? "blue" : "orange"}>
                      {prediction.clears_threshold ? "Needs your confirmation" : "Below threshold — needs your review"}
                    </Badge>
                    <Text size="sm">
                      Model score {(prediction.probability * 100).toFixed(1)}% · lead{" "}
                      {(prediction.margin * 100).toFixed(1)} points
                    </Text>
                    {prediction.purpose === "adjustment" && (
                      <Text size="xs">
                        Adjustment type only; no amount, replacement ratio or complete preparation plan has been
                        established.
                      </Text>
                    )}
                  </Stack>
                ))}
                <Text size="sm">
                  Comparison can use at most {review.max_provider_requests} provider requests, each limited to eight
                  seconds. No automatic retry. Used: {review.provider_requests}.
                </Text>
                <Button variant="light" disabled={busy || !token || !review.can_compare} onClick={() => act("compare")}>
                  Compare this item
                </Button>
                {busy && (
                  <Button
                    variant="default"
                    onClick={() => {
                      discard();
                      setMessage("Comparison stopped. Review the details again before making a choice.");
                    }}
                  >
                    Stop comparison
                  </Button>
                )}
                <Checkbox
                  label={
                    negative
                      ? "I reviewed the conflict and choose this item myself only for this recipe"
                      : "I checked the evidence and choose this item only for this recipe"
                  }
                  checked={checked}
                  disabled={busy || !token || !review.can_confirm}
                  onChange={(e) => setChecked(e.currentTarget.checked)}
                />
                <Group>
                  <Button
                    disabled={
                      busy || !token || !checked || !review.can_confirm || review.stale || review.prediction_stale
                    }
                    onClick={() => act("confirm")}
                  >
                    {negative ? "Record my choice for this recipe" : "Use for this recipe"}
                  </Button>
                  <Button
                    variant="default"
                    disabled={busy}
                    onClick={() => {
                      discard();
                      setCandidate(null);
                      setFacts(recipeOnlyFacts);
                    }}
                  >
                    Choose another item
                  </Button>
                  <Button
                    variant="subtle"
                    disabled={busy || !token || review.status === "confirmed" || review.status === "rejected"}
                    onClick={() => act("reject")}
                  >
                    Leave unresolved
                  </Button>
                </Group>
              </>
            )}
            <Button variant="subtle" disabled={busy} onClick={() => history.refetch()}>
              Refresh saved recipe choice
            </Button>
          </>
        )}
      </Stack>
    </Modal>
  );
}
