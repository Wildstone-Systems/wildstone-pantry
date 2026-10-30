import { Alert, Anchor, Badge, Button, Checkbox, Group, Modal, Stack, Text } from "@mantine/core";
import { useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { getApiErrorMessage } from "@/api/errors";
import { ClassifierVerificationNotice } from "@/components/ClassifierVerificationNotice";
import { ROUTES } from "@/routes/paths";
import { useAuth } from "@/state/AuthProvider";
import {
  confirmRankedIngredient,
  evaluateRankedIngredients,
  rejectRankedIngredients,
  type RankedBinding,
  type RankedCandidate,
  type RankedReview,
  type RankedIdentityEvidence,
} from "../hooks/ranked-ingredient-hooks";

const labels: Record<RankedCandidate["label"], string> = {
  same_ingredient: "Same ingredient",
  possible_substitution: "Possible substitution",
  different_ingredient: "Different ingredient",
  insufficient_evidence: "Insufficient evidence",
};
const readable = (code: string) => code.replaceAll("_", " ");
const percent = (value: number) => `${Math.round(value * 100)}%`;
const provenanceNames: Record<string, string> = { confirmed: "Confirmed by a user", generated: "Suggested from name" };
const provenance = (value: string) => provenanceNames[value] ?? "Unknown";
const evidenceUsable = (evidence?: RankedIdentityEvidence) => Boolean(evidence && !evidence.truncated);
function IdentityEvidence({ title, evidence }: { title: string; evidence?: RankedIdentityEvidence }) {
  return (
    <Stack gap={4}>
      <Text fw={600}>{title}</Text>
      <Text size="sm">
        Brand: {evidence?.brand || "Unknown"}. Recorded source:{" "}
        {evidence?.source ? readable(evidence.source) : "Unknown"}.
      </Text>
      <Text size="sm">Recorded ingredients: {evidence?.ingredients_text || "Unknown"}</Text>
      <Text size="sm">
        Product details:{" "}
        {evidence?.facts_provenance === "saved_user_provided" && !evidence.saved_facts_stale
          ? "Saved by a user"
          : "Unknown; no current saved facts"}
        .
      </Text>
      {(
        [
          ["identity", "Identity"],
          ["base_ingredient", "Base ingredient"],
          ["form", "Form"],
          ["preparation", "Preparation"],
          ["composition", "Composition"],
        ] as const
      ).map(([key, label]) => (
        <Text size="sm" key={key}>
          {label}: {(!evidence?.saved_facts_stale && evidence?.current_facts[key]) || "Unknown"}
        </Text>
      ))}
      {evidence?.saved_facts_stale && (
        <Alert color="orange">
          Saved product details are out of date and were not used. Check the current catalog evidence.
        </Alert>
      )}
      {evidence?.truncated && (
        <Alert color="orange">
          Some evidence was shortened. Review the complete product details before a global identity change.
        </Alert>
      )}
      {!evidence && (
        <Alert color="orange">Product evidence is unavailable. A global identity change is not available.</Alert>
      )}
    </Stack>
  );
}

export function RankedIngredientReviewModal({
  recipeId,
  ingredientId,
  onClose,
  onRecipeReview,
}: {
  recipeId: string;
  ingredientId: string;
  onClose: () => void;
  onRecipeReview: () => void;
}) {
  const { account, isAdmin } = useAuth();
  const cache = useQueryClient();
  const [review, setReview] = useState<RankedReview | null>(null);
  const [binding, setBinding] = useState<RankedBinding | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [confirmation, setConfirmation] = useState(false);
  const [acknowledged, setAcknowledged] = useState(false);
  const [replace, setReplace] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const generation = useRef(0);
  const request = useRef<AbortController | null>(null);
  const clear = useCallback(() => {
    generation.current += 1;
    request.current?.abort();
    request.current = null;
    setBusy(false);
    setReview(null);
    setBinding(null);
    setSelected(null);
    setConfirmation(false);
    setAcknowledged(false);
    setReplace(false);
  }, []);
  useEffect(() => {
    clear();
    setMessage(null);
    return () => {
      generation.current += 1;
      request.current?.abort();
    };
  }, [recipeId, ingredientId, account?.id, isAdmin, clear]);
  function close() {
    clear();
    onClose();
  }
  const row = review?.results.find((candidate) => candidate.candidate_item_id === selected);
  const conflict =
    row?.canonical_provenance === "confirmed" && row.canonical_ingredient !== review?.requested_canonical_ingredient;
  const current = Boolean(review && binding && !review.stale && review.status === "pending");
  const selectedEvidenceUsable = evidenceUsable(review?.requested_evidence) && evidenceUsable(row?.product_evidence);
  const confidence = typeof review?.policy.confidence === "number" ? review.policy.confidence : 0.9;
  function select(candidateId: string) {
    setSelected(candidateId);
    setConfirmation(false);
    setAcknowledged(false);
    setReplace(false);
  }
  async function run(action: "evaluate" | "confirm" | "reject") {
    if (!isAdmin || busy) return;
    if (action !== "evaluate" && (!current || !review || !binding)) return;
    if (
      action === "confirm" &&
      (!row ||
        row.label !== "same_ingredient" ||
        !row.can_confirm ||
        !selectedEvidenceUsable ||
        !acknowledged ||
        (conflict && !replace))
    )
      return;
    const prior = review;
    const capability = binding;
    if (action === "evaluate") clear();
    const version = ++generation.current;
    const controller = new AbortController();
    request.current = controller;
    setBusy(true);
    setMessage(null);
    try {
      const next =
        action === "evaluate"
          ? await evaluateRankedIngredients(recipeId, ingredientId, controller.signal)
          : action === "confirm"
            ? await confirmRankedIngredient(prior!.id, capability!, row!.candidate_item_id, replace, controller.signal)
            : await rejectRankedIngredients(prior!.id, capability!, controller.signal);
      if (generation.current !== version || controller.signal.aborted) return;
      setReview(next);
      setConfirmation(false);
      setAcknowledged(false);
      setReplace(false);
      // Only an explicit evaluation can issue authority. Read/confirmation responses cannot upgrade it.
      setBinding(
        action === "evaluate" && next.confirmation_token && !next.stale
          ? {
              revision: next.revision,
              evidence_hash: next.evidence_hash,
              confirmation_token: next.confirmation_token,
            }
          : null
      );
      if (action === "confirm") {
        setMessage(
          `Confirmed ${row!.product_name} as ${prior!.requested_name} for every recipe in your household. The selected product's ingredient identity has been saved.`
        );
        for (const queryKey of ["recipeAvailability", "recipeSuggestions", "ingredientMatches", "items"]) {
          void cache.invalidateQueries({ queryKey: [queryKey] });
        }
      } else if (action === "reject") setMessage("Review dismissed. No ingredient identity was changed.");
    } catch (error) {
      if (generation.current !== version || controller.signal.aborted) return;
      clear();
      const status = (error as { response?: { status?: number } })?.response?.status;
      setMessage(
        status === 409
          ? "Details, stock or settings changed, or this review expired. Evaluate again before confirming. Manual matching remains available."
          : getApiErrorMessage(
              error,
              "Review could not be completed. Check the saved identity before trying again; manual matching remains available."
            )
      );
    } finally {
      if (generation.current === version) {
        setBusy(false);
        request.current = null;
      }
    }
  }
  return (
    <>
      <Modal
        opened
        onClose={close}
        title="Rank ingredient identity candidates"
        size="lg"
        closeButtonProps={{ "aria-label": "Close ranked review" }}
      >
        <Stack>
          <ClassifierVerificationNotice />
          <Alert color="yellow">
            This review can permanently change one product&apos;s ingredient identity for every recipe in your
            household. Recipe-only choices use a separate review.
          </Alert>
          <Text>
            Evaluation checks up to three candidates with at most three serial classifier requests, eight seconds each,
            without retries. Opening this window makes no classifier requests.
          </Text>
          {!isAdmin ? (
            <Text>
              Sign in as an admin to evaluate candidates.{" "}
              <Anchor component={Link} to={ROUTES.admin}>
                Admin sign-in
              </Anchor>
              . Existing manual matching remains available.
            </Text>
          ) : (
            <>
              <Button disabled={busy} onClick={() => run("evaluate")}>
                Evaluate identity candidates
              </Button>
              {busy && (
                <Button
                  variant="default"
                  onClick={() => {
                    clear();
                    setMessage(
                      "Stopped waiting for this review. No automatic retry will run; check the saved identity if a confirmation was in progress."
                    );
                  }}
                >
                  Stop waiting
                </Button>
              )}
              {message && <Alert>{message}</Alert>}
              {review && review.status !== "confirmed" && review.status !== "rejected" && (
                <>
                  <Text fw={600}>Requested ingredient: {review.requested_name}</Text>
                  <IdentityEvidence title="Requested ingredient evidence" evidence={review.requested_evidence} />
                  <Text size="sm">
                    Admin confidence threshold: {percent(confidence)}. Every identity change requires your confirmation.
                    Provider requests: {review.provider_requests}/{review.max_provider_requests}.
                  </Text>
                  {review.policy.enabled === false && (
                    <Alert color="orange">
                      Classifier assistance is disabled or unconfigured. Deterministic evidence and manual matching
                      remain available.
                    </Alert>
                  )}
                  {typeof review.policy.provider === "string" && typeof review.policy.model === "string" && (
                    <Text size="sm">
                      Classifier: {review.policy.provider} / {review.policy.model}.
                    </Text>
                  )}
                  {review.stale && (
                    <Alert color="orange">This evidence is out of date. Evaluate again before choosing.</Alert>
                  )}
                  {review.ambiguous_tie && (
                    <Alert color="orange">
                      Candidates are tied. There is no automatic winner; compare their evidence.
                    </Alert>
                  )}
                  {review.reason_codes.length > 0 && (
                    <Alert color="orange">
                      Review notes: {review.reason_codes.map(readable).join("; ")}. Manual matching remains available.
                    </Alert>
                  )}
                  {review.results.length === 0 && (
                    <Text>No candidates were returned. Use the existing manual matching workflow.</Text>
                  )}
                  {review.results.map((candidate) => (
                    <Stack
                      key={candidate.candidate_item_id}
                      gap="xs"
                      p="sm"
                      style={{ border: "1px solid var(--mantine-color-default-border)" }}
                    >
                      <Text fw={600}>
                        {candidate.product_name} → {candidate.requested_name}
                      </Text>
                      <Badge color={candidate.label === "same_ingredient" ? "green" : "gray"}>
                        {labels[candidate.label]}
                      </Badge>
                      <Text size="sm">Identity source: {provenance(candidate.canonical_provenance)}.</Text>
                      <IdentityEvidence title="Product evidence" evidence={candidate.product_evidence} />
                      <details>
                        <summary>Technical identity audit</summary>
                        <Text size="sm">
                          Canonical key: {candidate.canonical_ingredient ?? "unassigned"}; version{" "}
                          {candidate.canonical_version ?? "unknown"}; revision {candidate.canonical_revision}.
                        </Text>
                      </details>
                      <Text size="sm">
                        Retrieved by: {readable(candidate.retrieval_reason)}. Decision source:{" "}
                        {readable(candidate.decision_provenance)}.
                      </Text>
                      {candidate.probability !== null && (
                        <Text size="sm">
                          Classifier score: {percent(candidate.probability)}
                          {candidate.margin !== null ? `; margin: ${percent(candidate.margin)}` : ""}.{" "}
                          {candidate.clears_threshold
                            ? "Meets the configured threshold; still needs confirmation."
                            : "Below threshold — needs your review."}
                        </Text>
                      )}
                      {candidate.reason_codes.length > 0 && (
                        <Text size="sm">Evidence notes: {candidate.reason_codes.map(readable).join("; ")}.</Text>
                      )}
                      <Button
                        variant={selected === candidate.candidate_item_id ? "filled" : "light"}
                        disabled={busy || !current}
                        onClick={() => select(candidate.candidate_item_id)}
                      >
                        Select {candidate.product_name}
                      </Button>
                      {candidate.label === "possible_substitution" && (
                        <>
                          <Text>This may suit a particular recipe. It cannot create a global ingredient alias.</Text>
                          <Button
                            variant="default"
                            disabled={busy}
                            onClick={() => {
                              clear();
                              onRecipeReview();
                            }}
                          >
                            Review substitution for this recipe
                          </Button>
                        </>
                      )}
                    </Stack>
                  ))}
                  <Button
                    disabled={
                      busy ||
                      !current ||
                      !row?.can_confirm ||
                      row.label !== "same_ingredient" ||
                      !selectedEvidenceUsable
                    }
                    onClick={() => {
                      setAcknowledged(false);
                      setReplace(false);
                      setConfirmation(true);
                    }}
                  >
                    Review global identity change
                  </Button>
                  <Button variant="default" disabled={busy || !current} onClick={() => run("reject")}>
                    Dismiss ranked review
                  </Button>
                </>
              )}
            </>
          )}
          <Button variant="subtle" onClick={close}>
            Return to manual matching
          </Button>
        </Stack>
      </Modal>
      <Modal
        opened={confirmation && Boolean(row)}
        onClose={() => {
          if (!busy) setConfirmation(false);
        }}
        title="Confirm ranked global identity"
      >
        {row && review && (
          <Stack>
            <Alert color="yellow">
              This permanently links {row.product_name} to {review.requested_name} for every recipe in your household. A
              possible substitution must not be confirmed as the same ingredient.
            </Alert>
            <Text>
              {row.product_name} → {review.requested_name}
            </Text>
            <IdentityEvidence title="Selected product evidence" evidence={row.product_evidence} />
            <IdentityEvidence title="Requested ingredient evidence" evidence={review.requested_evidence} />
            <Checkbox
              label="I checked the evidence: these are the same ingredient, and I want this permanent household-wide identity change"
              checked={acknowledged}
              disabled={busy}
              onChange={(event) => setAcknowledged(event.currentTarget.checked)}
            />
            {conflict && (
              <Checkbox
                label="Replace the previously confirmed identity"
                checked={replace}
                disabled={busy}
                onChange={(event) => setReplace(event.currentTarget.checked)}
              />
            )}
            <Group>
              <Button variant="default" disabled={busy} onClick={() => setConfirmation(false)}>
                Cancel ranked identity change
              </Button>
              <Button
                disabled={busy || !current || !selectedEvidenceUsable || !acknowledged || (conflict && !replace)}
                onClick={() => run("confirm")}
              >
                Confirm selected global identity
              </Button>
            </Group>
          </Stack>
        )}
      </Modal>
    </>
  );
}
