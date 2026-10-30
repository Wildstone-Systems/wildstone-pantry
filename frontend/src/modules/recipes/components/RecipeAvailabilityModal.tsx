import { Anchor, Badge, Button, Group, Modal, Stack, Text, TextInput } from "@mantine/core";
import { Check, Link2 } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";

import { getApiErrorMessage } from "@/api/errors";
import { useItems } from "@/modules/catalog";
import { ROUTES } from "@/routes/paths";
import { useAppState } from "@/state/AppStateProvider";
import { IngredientMatchReviewModal } from "./IngredientMatchReviewModal";
import { RankedIngredientReviewModal } from "./RankedIngredientReviewModal";
import { IdentityConfirmationModal, type IdentityChoice } from "./IdentityConfirmationModal";

import type { Recipe } from "../hooks/recipes-hooks";
import {
  type RecipeIngredientAvailability,
  useApproveSubstitution,
  useConfirmIngredientItem,
  useRecipeAvailability,
} from "../hooks/recipes-hooks";

const STATUS_STYLE: Record<RecipeIngredientAvailability["status"], { color: string; label: string }> = {
  have: { color: "green", label: "in stock" },
  substitute: { color: "teal", label: "via sub" },
  suggested: { color: "yellow", label: "match?" },
  missing: { color: "gray", label: "missing" },
  // ADR 0017: stock exists but its unit can't bridge to the recipe's; and
  // optional / to-taste lines that never count as a gap.
  cant_convert: { color: "orange", label: "set density" },
  optional: { color: "gray", label: "optional" },
};
const FALLBACK_STATUS = { color: "gray", label: "—" };

/** Trim a converted quantity to something readable: whole numbers past 100, else
 *  ~2 significant decimals (0.7975… → 0.8). */
function fmtQty(n: number): string {
  if (!Number.isFinite(n)) return "0";
  if (Math.abs(n) >= 100) return String(Math.round(n));
  return String(Math.round(n * 100) / 100);
}

/** Per-ingredient availability (ADR 0015 Phase 2). Auto-suggests tight-subset
 *  products to confirm, and — since a real pantry rarely matches by name alone —
 *  lets you manually match any ingredient to an item you have. Either way the
 *  confirmation learns the alias, so the ingredient counts everywhere afterward. */
export function RecipeAvailabilityModal({
  recipe,
  locationId,
  onClose,
}: {
  recipe: Recipe | null;
  locationId: string | null;
  onClose: () => void;
}) {
  const { setStatus } = useAppState();
  const availability = useRecipeAvailability(recipe?.id ?? null, locationId);
  const confirm = useConfirmIngredientItem();
  const approve = useApproveSubstitution();
  const itemsQuery = useItems();
  const rows = availability.data ?? [];

  const onApprove = async (substitutionId: string, label: string) => {
    try {
      await approve.mutateAsync(substitutionId);
      setStatus(`Substitution approved — using ${label}`);
    } catch (error) {
      setStatus(getApiErrorMessage(error, "Could not approve that substitution"));
    }
  };

  // Which ingredient's manual "match to an item I have" search is open, + its query.
  const [matchFor, setMatchFor] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [choice, setChoice] = useState<IdentityChoice | null>(null);
  const [reviewIngredient, setReviewIngredient] = useState<string | null>(null);
  const [rankedIngredient, setRankedIngredient] = useState<string | null>(null);
  useEffect(() => {
    setChoice(null);
    setReviewIngredient(null);
    setRankedIngredient(null);
    setMatchFor(null);
    setQuery("");
  }, [recipe?.id]);

  const items = itemsQuery.data ?? [];

  const requestConfirmation = (ingredientId: string, itemId: string, name: string) => {
    const ing = rows.find((row) => row.ingredient_id === ingredientId);
    const item = items.find((row) => row.id === itemId);
    const suggestion = ing?.suggestions.find((row) => row.item_id === itemId);
    const identity = item ?? suggestion;
    if (!ing || !identity) return;
    setChoice({
      ingredientId,
      itemId,
      itemName: item?.name ?? suggestion?.item_name ?? name,
      currentKey: identity.canonical_ingredient ?? null,
      targetKey: ing.canonical_ingredient,
      provenance: identity.canonical_provenance,
      version: identity.canonical_version,
      revision: identity.canonical_revision,
    });
  };
  const onConfirm = async (selected: IdentityChoice, replaceConfirmed: boolean) => {
    try {
      await confirm.mutateAsync({
        ingredientId: selected.ingredientId,
        itemId: selected.itemId,
        expectedCanonical: selected.currentKey,
        expectedRevision: selected.revision,
        expectedIngredient: selected.targetKey,
        replaceConfirmed,
      });
      setChoice(null);
      setStatus(`Matched — ${selected.itemName} now counts as in stock`);
      setMatchFor(null);
      setQuery("");
    } catch (error) {
      setStatus(getApiErrorMessage(error, "Could not confirm that match"));
    }
  };

  return (
    <>
      <Modal
        opened={recipe !== null}
        onClose={onClose}
        title={recipe ? `Ingredients — ${recipe.name}` : "Ingredients"}
        size="lg"
      >
        {availability.isLoading ? (
          <Text size="sm" c="dimmed">
            Checking your stock…
          </Text>
        ) : rows.length === 0 ? (
          <Text size="sm" c="dimmed">
            This recipe has no ingredients yet.
          </Text>
        ) : (
          <Stack gap="xs">
            {rows.map((ing) => (
              <IngredientRow
                key={ing.ingredient_id}
                ing={ing}
                items={items}
                matchOpen={matchFor === ing.ingredient_id}
                query={matchFor === ing.ingredient_id ? query : ""}
                confirmingItemId={confirm.isPending ? confirm.variables?.itemId : undefined}
                approvingId={approve.isPending ? approve.variables : undefined}
                onToggleMatch={() => {
                  setMatchFor((cur) => (cur === ing.ingredient_id ? null : ing.ingredient_id));
                  setQuery("");
                }}
                onQuery={setQuery}
                onConfirm={requestConfirmation}
                onApprove={onApprove}
                onReview={() => setReviewIngredient(ing.ingredient_id)}
                onRank={() => setRankedIngredient(ing.ingredient_id)}
              />
            ))}
          </Stack>
        )}
      </Modal>
      {rankedIngredient && recipe && (
        <RankedIngredientReviewModal
          recipeId={recipe.id}
          ingredientId={rankedIngredient}
          onClose={() => setRankedIngredient(null)}
          onRecipeReview={() => {
            setReviewIngredient(rankedIngredient);
            setRankedIngredient(null);
          }}
        />
      )}
      {reviewIngredient && recipe && (
        <IngredientMatchReviewModal
          recipeId={recipe.id}
          ingredientId={reviewIngredient}
          onClose={() => setReviewIngredient(null)}
        />
      )}
      <IdentityConfirmationModal
        choice={choice}
        busy={confirm.isPending}
        onClose={() => setChoice(null)}
        onConfirm={onConfirm}
      />
    </>
  );
}

function IngredientRow({
  ing,
  items,
  matchOpen,
  query,
  confirmingItemId,
  approvingId,
  onToggleMatch,
  onQuery,
  onConfirm,
  onApprove,
  onReview,
  onRank,
}: {
  ing: RecipeIngredientAvailability;
  items: { id: string; name: string; brand: string | null }[];
  matchOpen: boolean;
  query: string;
  confirmingItemId: string | undefined;
  approvingId: string | undefined;
  onToggleMatch: () => void;
  onQuery: (q: string) => void;
  onConfirm: (ingredientId: string, itemId: string, name: string) => void;
  onApprove: (substitutionId: string, label: string) => void;
  onReview: () => void;
  onRank: () => void;
}) {
  const style = STATUS_STYLE[ing.status] ?? FALLBACK_STATUS;
  // Prefer on-hand shown in the recipe's own unit (ADR 0017 §5), marked "~" since
  // it's a conversion; fall back to the raw base amount when there's no bridge.
  const onHand =
    ing.on_hand_recipe_unit != null
      ? `~${fmtQty(Number(ing.on_hand_recipe_unit))} ${ing.unit}`
      : `${fmtQty(Number(ing.on_hand))}${ing.base_unit ? ` ${ing.base_unit}` : ""}`;
  const isOptional = ing.status === "optional";

  // Manual match candidates: the user's own items, minus the recipe's own
  // (empty) ingredient record, filtered by the search text.
  const candidates = useMemo(() => {
    const q = query.trim().toLowerCase();
    return items
      .filter((it) => it.id !== ing.item_id)
      .filter((it) => (q ? it.name.toLowerCase().includes(q) : true))
      .slice(0, 8);
  }, [items, query, ing.item_id]);

  return (
    <Stack
      gap={6}
      py="xs"
      style={{ borderBottom: "1px solid var(--mantine-color-default-border)", opacity: isOptional ? 0.6 : 1 }}
    >
      <Group justify="space-between" wrap="nowrap">
        <div style={{ minWidth: 0 }}>
          <Text fw={500}>
            {ing.item_name}{" "}
            <Text span c="dimmed" size="sm">
              · need {fmtQty(Number(ing.quantity_required))} {ing.unit}
              {isOptional ? " (to taste)" : ""}
            </Text>
          </Text>
          {Number(ing.on_hand) > 0 && ing.status !== "suggested" ? (
            <Text size="xs" c="dimmed">
              on hand: {onHand}
            </Text>
          ) : null}
        </div>
        <Badge
          color={style.color}
          variant={ing.status === "have" || ing.status === "substitute" ? "filled" : "light"}
          radius="xl"
        >
          {style.label}
        </Badge>
      </Group>

      {/* Can't measure the stock in the recipe's unit — say exactly what to set. */}
      {ing.status === "cant_convert" ? (
        <Text size="xs" c="orange.8" pl="sm">
          Can&apos;t measure <b>{ing.item_name}</b> in {ing.unit}
          {Number(ing.on_hand) > 0 && ing.base_unit ? ` (have ${fmtQty(Number(ing.on_hand))} ${ing.base_unit})` : ""}.
          Set a density or package size on it in{" "}
          <Anchor component={Link} to={ROUTES.catalog}>
            Catalog
          </Anchor>
          .
        </Text>
      ) : null}

      {/* Approved substitute in use. */}
      {ing.available_via ? (
        <Text size="sm" c="dimmed" pl="sm">
          using <b>{ing.available_via.to_label}</b> ({Number(ing.available_via.on_hand)} on hand
          {Number(ing.available_via.ratio) !== 1 ? `, ×${Number(ing.available_via.ratio)}` : ""})
        </Text>
      ) : null}

      {/* Substitution suggestions to approve. */}
      {ing.substitution_suggestions.map((s) => (
        <Group key={s.substitution_id} justify="space-between" wrap="nowrap" pl="sm">
          <Text size="sm" c="dimmed">
            Use <b>{s.to_label}</b> ({Number(s.on_hand)} on hand) for {ing.item_name}?
          </Text>
          <Button
            size="compact-sm"
            variant="light"
            color="teal"
            leftSection={<Check size={14} strokeWidth={3} />}
            loading={approvingId === s.substitution_id}
            onClick={() => onApprove(s.substitution_id, s.to_label)}
            style={{ flexShrink: 0 }}
          >
            Approve
          </Button>
        </Group>
      ))}

      {/* Auto-suggested tight-subset products. */}
      {ing.suggestions.map((s) => (
        <Group key={s.item_id} justify="space-between" wrap="nowrap" pl="sm">
          <Text size="sm" c="dimmed">
            Is your <b>{s.item_name}</b> ({Number(s.on_hand)} on hand) the {ing.item_name} this needs?
          </Text>
          <Button
            size="compact-sm"
            variant="light"
            color="green"
            leftSection={<Check size={14} strokeWidth={3} />}
            loading={confirmingItemId === s.item_id}
            onClick={() => onConfirm(ing.ingredient_id, s.item_id, ing.item_name)}
            style={{ flexShrink: 0 }}
          >
            Use it
          </Button>
        </Group>
      ))}

      <Button size="compact-xs" variant="light" onClick={onReview} style={{ alignSelf: "flex-start" }}>
        Review an item for this recipe
      </Button>
      <Button size="compact-xs" variant="light" onClick={onRank} style={{ alignSelf: "flex-start" }}>
        Rank ingredient identity candidates
      </Button>
      {/* Manual match: works even when the auto-suggester finds nothing. */}
      {ing.status !== "have" && !isOptional ? (
        <Button
          size="compact-xs"
          variant="subtle"
          color="gray"
          leftSection={<Link2 size={13} />}
          onClick={onToggleMatch}
          style={{ alignSelf: "flex-start" }}
        >
          {matchOpen ? "Cancel" : "Match to an item I have…"}
        </Button>
      ) : null}

      {matchOpen ? (
        <Stack gap={4} pl="sm">
          <TextInput
            size="xs"
            placeholder={`Search your items for "${ing.item_name}"`}
            value={query}
            onChange={(e) => onQuery(e.currentTarget.value)}
            aria-label={`Search items to match ${ing.item_name}`}
            data-autofocus
          />
          {candidates.length === 0 ? (
            <Text size="xs" c="dimmed">
              No items match. Add it to your catalog first, then match it here.
            </Text>
          ) : (
            candidates.map((it) => (
              <Group key={it.id} justify="space-between" wrap="nowrap">
                <Text size="sm" lineClamp={1}>
                  {it.name}
                  {it.brand ? (
                    <Text span c="dimmed" size="xs">
                      {" "}
                      · {it.brand}
                    </Text>
                  ) : null}
                </Text>
                <Button
                  size="compact-xs"
                  variant="light"
                  loading={confirmingItemId === it.id}
                  onClick={() => onConfirm(ing.ingredient_id, it.id, ing.item_name)}
                  style={{ flexShrink: 0 }}
                >
                  Use it
                </Button>
              </Group>
            ))
          )}
        </Stack>
      ) : null}
    </Stack>
  );
}
