import { Alert, Button, Card, Checkbox, Group, Modal, NativeSelect, NumberInput, Stack, Text, Textarea, TextInput, Title } from "@mantine/core";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";

import { api } from "@/api/client";
import { useItems } from "@/api/hooks";
import { getApiErrorMessage } from "@/api/errors";
import { ClassifierVerificationNotice } from "@/components/ClassifierVerificationNotice";
import { useAuth } from "@/state/AuthProvider";

import type { CookSession } from "../hooks/cook-session-hooks";
import { type RecipeIngredientInput, useUnitVocabulary } from "../hooks/recipes-hooks";

type Evidence = { name?: string; brand?: string | null; ingredients_text?: string | null; nutrition_basis?: string; nutrition_values?: Record<string, string | number | null>; source?: string | null; structured_evidence?: { stale?: boolean; provenance?: string; facts?: Record<string, unknown> } };
export type ScopedReview = {
  id: string; ingredient_id: string; from_item_id: string; to_item_id: string; binding: string;
  status: string; scope: string | null; ratio: string | null; from_unit: string; unit: string | null;
  snapshot: { from_item?: Evidence; to_item?: Evidence; recipe_name?: string; instructions?: string | null };
  context: { cooking_role?: string | null; preparation?: string | null; restrictions?: string | null };
  missing_evidence: string[]; stale: boolean; warning: string;
  evaluation: { status?: string; role?: string | null; verdict?: string | null; score?: number | null; below_threshold?: boolean; threshold?: number; [key: string]: unknown } | null;
  existing_global: { ratio: string; unit: string; from_unit: string | null } | null;
};
type ReviewList = { reviews: ScopedReview[]; recipe_approvals: ScopedReview[]; attached_ids: string[]; missing_attachment_ids?: string[] };

function ProductFacts({ label, value }: { label: string; value?: Evidence }) {
  return <Card withBorder padding="sm"><Text fw={600}>{label}: {value?.name ?? "Unknown item"}</Text>
    {value?.brand && <Text size="sm">Brand: {value.brand}</Text>}
    <Text size="sm">Composition: {value?.ingredients_text || "Unknown"}</Text>
    <Text size="sm">Nutrition basis: {value?.nutrition_basis ?? "Unknown"}</Text>
    {Object.entries(value?.nutrition_values ?? {}).filter(([, nutrient]) => nutrient != null).map(([key, nutrient]) => <Text key={key} size="sm">{key.replaceAll("_", " ")}: {String(nutrient)}</Text>)}
    <Text size="sm">Source: {value?.source || "Unknown"}; saved facts: {value?.structured_evidence?.provenance ?? "unknown"}.</Text>
    {value?.structured_evidence?.stale ? <Text size="sm">Saved product facts changed and are not reliable context.</Text> :
      Object.entries(value?.structured_evidence?.facts ?? {}).map(([key, fact]) => fact != null && <Text key={key} size="sm">{key.replaceAll("_", " ")}: {String(fact)}</Text>)}
  </Card>;
}

/** Approval changes this cook's plan only. Finish remains a separate action. */
export function ScopedSubstitutionReview({ session, ingredients, onChanged }: {
  session: CookSession; ingredients: RecipeIngredientInput[]; onChanged: () => Promise<unknown>;
}) {
  const { isAdmin } = useAuth();
  const qc = useQueryClient();
  const [opened, setOpened] = useState(false);
  const [ingredientId, setIngredientId] = useState("");
  const [candidateId, setCandidateId] = useState("");
  const [role, setRole] = useState("");
  const [preparation, setPreparation] = useState("");
  const [restrictions, setRestrictions] = useState("");
  const [review, setReview] = useState<ScopedReview | null>(null);
  const [ratio, setRatio] = useState<number | string>("");
  const [unit, setUnit] = useState("");
  const [scope, setScope] = useState("one_use");
  const [confirmed, setConfirmed] = useState(false);
  const [permanentAck, setPermanentAck] = useState(false);
  const [replaceAck, setReplaceAck] = useState(false);
  const [replaceApprovalAck, setReplaceApprovalAck] = useState(false);
  const [reuseConfirm, setReuseConfirm] = useState<Record<string, boolean>>({});
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const generation = useRef(0);
  const items = useItems();
  const vocabulary = useUnitVocabulary();
  const list = useQuery({
    queryKey: ["substitution-reviews", session.id], enabled: opened,
    queryFn: async () => (await api.get<ReviewList>(`/api/cook-sessions/${session.id}/substitution-reviews`)).data,
  });
  // Recipe approvals originate in an earlier cook, so they may only appear in
  // recipe_approvals even while attached here. Use one deduplicated history for
  // active-choice controls and replacement checks.
  const allReviews = [...new Map([...(list.data?.recipe_approvals ?? []), ...(list.data?.reviews ?? [])].map((value) => [value.id, value])).values()];
  const missingAttachmentIds = [...new Set(list.data?.missing_attachment_ids ?? [])];
  const line = ingredients.find((value) => value.ingredient_id === ingredientId);
  const packageMode = line?.quantity_mode === "package";
  const planContext = JSON.stringify({ servings: session.servings_made, ingredients,
    plan: { ...session.plan, substitution_approval_ids: undefined, substitution_contexts: undefined } });
  const packet = JSON.stringify({ session: session.id, ingredientId, candidateId, role, preparation, restrictions, planContext });
  useEffect(() => {
    generation.current += 1;
    setReview(null); setConfirmed(false); setPermanentAck(false); setReplaceAck(false); setReplaceApprovalAck(false); setBusy(false);
    setRatio(""); setUnit(""); setScope("one_use"); setReuseConfirm({}); setMessage("");
  }, [packet]);
  const resetConfirmation = () => { setConfirmed(false); setPermanentAck(false); setReplaceAck(false); };
  const refresh = async () => {
    await Promise.all([qc.invalidateQueries({ queryKey: ["substitution-reviews", session.id] }), onChanged()]);
  };
  const action = async (run: () => Promise<void>) => {
    setBusy(true); setMessage("");
    try { await run(); } catch (error) { setMessage(getApiErrorMessage(error, "Review unavailable. You can review and confirm manually.")); setConfirmed(false); }
    finally { setBusy(false); }
  };
  const prepare = () => action(async () => {
    const current = ++generation.current;
    const result = await api.post<ScopedReview>(`/api/cook-sessions/${session.id}/substitution-reviews`, {
      ingredient_id: ingredientId, substitute_item_id: candidateId,
      context: { cooking_role: role || null, preparation: preparation || null, restrictions: restrictions || null },
    });
    if (current !== generation.current) return;
    setReview(result.data); setConfirmed(false);
  });
  const removeFromCook = (id: string) => action(async () => {
    await api.patch(`/api/cook-sessions/${session.id}`, { plan: {
      ingredient_overrides: session.plan?.ingredient_overrides ?? [],
      scale_to_ingredient_id: session.plan?.scale_to_ingredient_id ?? null,
      allow_short_ingredients: session.plan?.allow_short_ingredients ?? false,
      substitutions: session.plan?.substitutions ?? [],
      substitution_approval_ids: (list.data?.attached_ids ?? session.plan?.substitution_approval_ids ?? []).filter((value) => value !== id),
    } });
    await refresh(); setReview(null); setConfirmed(false); setMessage("Choice removed from this cook. Saved household mappings were not changed.");
  });
  const existing = review?.existing_global;
  const replacementRequired = !!existing && (existing.from_unit !== review?.from_unit || Number(existing.ratio) !== Number(ratio) || existing.unit !== unit);
  const otherAttachment = allReviews.some((value) => value.ingredient_id === ingredientId && value.id !== review?.id && (list.data?.attached_ids ?? []).includes(value.id));
  const attachedForRow = (id: string) => allReviews.some((value) => value.ingredient_id === id && (list.data?.attached_ids ?? []).includes(value.id));
  const ratioValid = ratio !== "" && Number.isFinite(Number(ratio)) && Number(ratio) > 0 && Number(ratio) <= 999999.9999;
  const canApprove = missingAttachmentIds.length === 0 && !!review && !review.stale && review.status === "pending" && ratioValid && !!unit && confirmed &&
    (scope !== "global" || (permanentAck && (!replacementRequired || replaceAck))) && (!otherAttachment || replaceApprovalAck);

  return <>
    <Button type="button" variant="outline" disabled={session.status !== "active"} onClick={() => setOpened(true)}>Review a substitution</Button>
    {session.substitution_review_warning && <Alert color="yellow">{session.substitution_review_warning}</Alert>}
    <Modal opened={opened} onClose={() => { setOpened(false); setConfirmed(false); setReuseConfirm({}); }} title="Review a directional substitution" size="xl">
      <Stack>
        <ClassifierVerificationNotice />
        <Text size="sm">This changes a choice for an active cook. Nothing leaves the pantry until Finish. No dietary or allergy safety is certified.</Text>
        {missingAttachmentIds.length > 0 && <Alert color="yellow">Remove the missing attached choices below before approving or reusing a substitution. You can prepare a new manual review first.</Alert>}
        <NativeSelect label="Recipe ingredient" value={ingredientId} onChange={(event) => setIngredientId(event.currentTarget.value)}
          data={[{ value: "", label: "Choose an ingredient row" }, ...ingredients.filter((value) => value.ingredient_id).map((value, index) => ({ value: value.ingredient_id!, label: `Row ${index + 1}: ${value.item_name ?? value.item_id} · ${value.quantity_required} ${value.unit}` }))]} />
        <NativeSelect label="Proposed substitute" value={candidateId} onChange={(event) => setCandidateId(event.currentTarget.value)}
          data={[{ value: "", label: "Choose a catalog item" }, ...(items.data ?? []).filter((value) => value.id !== line?.item_id).map((value) => ({ value: value.id, label: `${value.name}${value.brand ? ` · ${value.brand}` : ""}` }))]} />
        <TextInput label="Cooking role" description="For example: seasoning, thickener or structure. Leave unknown details blank." maxLength={200} value={role} onChange={(event) => setRole(event.currentTarget.value)} />
        <TextInput label="Preparation and cooking context" description="For example: cold savory sauce, or baked bread." maxLength={200} value={preparation} onChange={(event) => setPreparation(event.currentTarget.value)} />
        <Textarea label="Restriction evidence" description="Describe relevant known restrictions, or explicitly say none known. Missing evidence stays unknown." maxLength={500} value={restrictions} onChange={(event) => setRestrictions(event.currentTarget.value)} />
        {packageMode && <Alert color="yellow">Whole-package substitutions are not supported here. Edit the recipe ingredient if its identity must change, or return to setup to adjust or omit its quantity.</Alert>}
        <Button type="button" onClick={() => void prepare()} disabled={busy || !ingredientId || !candidateId || packageMode}>Prepare manual review</Button>
        {review && <>
          <Title order={4}>{review.snapshot.from_item?.name ?? line?.item_name} → {review.snapshot.to_item?.name ?? "Proposed substitute"}</Title>
          <Text size="sm">{review.snapshot.recipe_name ?? session.recipe_name}: {review.snapshot.instructions ?? "No instructions supplied"}</Text>
          <ProductFacts label="Original" value={review.snapshot.from_item} /><ProductFacts label="Proposed" value={review.snapshot.to_item} />
          <Alert color="yellow">{review.warning}{review.missing_evidence.length > 0 && <Text size="sm">Missing evidence: {review.missing_evidence.join(", ").replaceAll("_", " ")}. Manual confirmation is still your decision.</Text>}</Alert>
          {isAdmin && <Button type="button" variant="subtle" disabled={busy || review.stale || review.status !== "pending"} onClick={() => void action(async () => {
            const current = generation.current; setConfirmed(false);
            const result = await api.post<ScopedReview>(`/api/substitution-reviews/${review.id}/evaluate`, {});
            if (current === generation.current) setReview(result.data);
          })}>Ask classifier for an optional hint</Button>}
          {review.evaluation && <Text size="sm">Classifier: {({ c_1: "plausible in this context", c_2: "possible with adjustments", c_3: "not supported", c_4: "insufficient evidence" } as Record<string, string>)[String(review.evaluation.verdict)] ?? String(review.evaluation.role ?? review.evaluation.status ?? "uncertain").replaceAll("_", " ")}. {review.evaluation.score != null ? `${Math.round(review.evaluation.score * 100)}% model score${review.evaluation.below_threshold ? " (below threshold)" : ""}; ${Math.round((review.evaluation.threshold ?? 0.9) * 100)}% configured threshold. ` : ""}This is not validated accuracy; verify it yourself.</Text>}
          {review.stale && <Alert color="yellow">The recipe, plan or product evidence changed. Prepare a fresh review.</Alert>}
          <NumberInput label="Substitute ratio" description={`Substitute units per 1 ${review.from_unit} of the original. Enter the amount you approve; the classifier supplies no ratio.`}
            value={ratio} min={0} max={999999.9999} decimalScale={4} onChange={(value) => { setRatio(value); resetConfirmation(); }} />
          <NativeSelect label="Substitute unit" value={unit} onChange={(event) => { setUnit(event.currentTarget.value); resetConfirmation(); }}
            data={[{ value: "", label: "Choose a unit" }, ...(vocabulary.data ?? []).map((value) => ({ value: value.value, label: value.label }))]} />
          <Text fw={600}>{ratioValid && unit ? `${ratio} ${unit} substitute per 1 ${review.from_unit} original` : `Enter a ratio and unit for 1 ${review.from_unit} original.`}</Text>
          <NativeSelect label="Approval scope" value={scope} onChange={(event) => { setScope(event.currentTarget.value); resetConfirmation(); }} data={[
            { value: "one_use", label: "This cook only (default)" }, { value: "recipe", label: "Save for this recipe ingredient" }, { value: "global", label: "Permanent household substitution" },
          ]} />
          {scope === "recipe" && <Text size="sm">Saved only for this recipe ingredient and unchanged evidence. Later cooks require explicit reuse.</Text>}
          {scope === "global" && <>
            <Checkbox label="I understand this permanently learns a directional household substitution." checked={permanentAck} onChange={(event) => setPermanentAck(event.currentTarget.checked)} />
            {existing && <Text size="sm">Existing household mapping: {existing.ratio} {existing.unit} per 1 {existing.from_unit ?? "original unit (legacy basis)"}. New choice: {ratio || "?"} {unit || "?"} per 1 {review.from_unit}.</Text>}
            {replacementRequired && <Checkbox label="I explicitly approve replacing the existing household ratio, units or legacy scope shown above." checked={replaceAck} onChange={(event) => setReplaceAck(event.currentTarget.checked)} />}
          </>}
          {otherAttachment && <Checkbox label="Replace the existing approved choice for this ingredient in this cook." checked={replaceApprovalAck} onChange={(event) => setReplaceApprovalAck(event.currentTarget.checked)} />}
          <Checkbox label="I checked the direction, product evidence, missing information, ratio, units and scope and approve this choice." checked={confirmed} onChange={(event) => setConfirmed(event.currentTarget.checked)} />
          <Button type="button" disabled={busy || !canApprove} onClick={() => void action(async () => {
            const result = await api.post<ScopedReview>(`/api/substitution-reviews/${review.id}/approve`, { expected_binding: review.binding, confirmed: true,
              ratio: Number(ratio), unit, scope, permanent_ack: permanentAck, replace_existing_ack: replaceAck, replace_approval_ack: replaceApprovalAck });
            setReview(result.data); setConfirmed(false); await refresh(); setMessage("Choice approved for the stated scope. Finish remains a separate action.");
          })}>Approve substitution</Button>
        </>}
        {allReviews.filter((value) => (list.data?.attached_ids ?? []).includes(value.id)).map((value) => <Card key={value.id} withBorder>
          <Text>Attached: {value.snapshot.from_item?.name} → {value.snapshot.to_item?.name}; {value.ratio} {value.unit} per 1 {value.from_unit}; {value.scope?.replaceAll("_", " ")}.</Text>
          {value.status !== "approved" && <Alert color="yellow">Choice status: {value.status.replaceAll("_", " ")}. Finish is blocked until this choice is removed or freshly reviewed.</Alert>}
          {value.stale && <Alert color="yellow">This choice has stale evidence. Remove it or prepare a fresh review before finishing.</Alert>}
          <Button type="button" variant="subtle" disabled={busy} onClick={() => void removeFromCook(value.id)}>Remove from this cook</Button>
          {value.status === "approved" && value.scope !== "global" && <Button type="button" variant="subtle" disabled={busy} onClick={() => void action(async () => { await api.delete(`/api/substitution-reviews/${value.id}`); await refresh(); setReview(null); })}>{value.scope === "recipe" ? "Revoke saved recipe choice" : "Revoke one-cook choice"}</Button>}
        </Card>)}
        {missingAttachmentIds.map((id) => <Card key={id} withBorder>
          <Text fw={600}>Missing attached choice: {id}</Text>
          <Text size="sm">This cook refers to a substitution whose details are no longer available; its ingredient or review may have been removed.</Text>
          <Alert color="yellow">Finish is blocked until you explicitly remove this missing choice. Its source, ratio and scope cannot be verified.</Alert>
          <Button type="button" variant="outline" disabled={busy} onClick={() => void removeFromCook(id)}>Remove missing choice from this cook</Button>
        </Card>)}
        {(list.data?.recipe_approvals ?? []).filter((value) => !(list.data?.attached_ids ?? []).includes(value.id)).map((value) => <Card key={value.id} withBorder>
          <Text>Saved recipe choice: {value.snapshot.from_item?.name} → {value.snapshot.to_item?.name}; {value.ratio} {value.unit} per 1 {value.from_unit}.</Text>
          {value.stale && <Text>Saved evidence changed; prepare a new review.</Text>}
          {attachedForRow(value.ingredient_id) && <Text>Remove the current choice for this ingredient before reusing another saved choice.</Text>}
          <Checkbox label={`Confirm reuse of ${value.snapshot.to_item?.name ?? "this saved choice"} for this cook`} checked={reuseConfirm[value.id] ?? false} onChange={(event) => { const checked = event.currentTarget.checked; setReuseConfirm((old) => ({ ...old, [value.id]: checked })); }} />
          <Button type="button" disabled={busy || missingAttachmentIds.length > 0 || value.stale || attachedForRow(value.ingredient_id) || !reuseConfirm[value.id]} onClick={() => void action(async () => {
            await api.post(`/api/cook-sessions/${session.id}/substitution-reviews/${value.id}/attach`, { expected_binding: value.binding, confirmed: true, replace_approval_ack: false });
            await refresh(); setReuseConfirm({});
          })}>Use saved choice for this cook</Button>
        </Card>)}
        {list.isError && <Alert color="yellow">Saved choices could not be loaded. You can prepare a new manual review.</Alert>}
        {message && <Alert color="blue">{message}</Alert>}
      </Stack>
    </Modal>
  </>;
}
