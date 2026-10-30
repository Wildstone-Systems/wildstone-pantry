import { Alert, Badge, Button, Group, Modal, Stack, Text } from "@mantine/core";
import { useState } from "react";

import { getApiErrorMessage } from "@/api/errors";
import { useAppState } from "@/state/AppStateProvider";

import { useConfirmIngredientItem, usePendingIngredientMatches } from "../hooks/recipes-hooks";
import { IdentityConfirmationModal, type IdentityChoice } from "./IdentityConfirmationModal";
import { RankedIngredientReviewModal } from "./RankedIngredientReviewModal";
import { IngredientMatchReviewModal } from "./IngredientMatchReviewModal";

/**
 * "These might already be in your pantry" — the household-wide reconciliation
 * queue (ADR 0015 §5).
 *
 * A recipe's generic "coconut oil" and the "Organic Virgin Coconut Oil" on the
 * shelf are the same thing, but nothing can prove that automatically without
 * guessing, so PantryOS asks. Confirming rewrites the PRODUCT's identity key,
 * which resolves every recipe using that ingredient at once — so this is worth
 * answering once rather than being re-prompted inside each recipe.
 */
export function IngredientMatchBanner() {
  const { setStatus } = useAppState();
  const matches = usePendingIngredientMatches();
  const confirm = useConfirmIngredientItem();
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [choice, setChoice] = useState<IdentityChoice | null>(null);
  const [ranked, setRanked] = useState<{ recipe: string; ingredient: string } | null>(null);
  const [recipeReview, setRecipeReview] = useState<{ recipe: string; ingredient: string } | null>(null);

  const pending = matches.data ?? [];
  if (pending.length === 0) return null;

  const accept = async (selected: IdentityChoice, replaceConfirmed: boolean) => {
    setBusy(selected.ingredientId);
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
      setStatus(`Linked ${selected.itemName} — every recipe using it now sees your stock`);
    } catch (err) {
      setStatus(getApiErrorMessage(err, "Could not link that"));
    } finally {
      setBusy(null);
    }
  };

  return (
    <>
      <Alert color="yellow" variant="light">
        <Group justify="space-between" wrap="wrap" gap="sm">
          <Text size="sm">
            {pending.length} ingredient{pending.length === 1 ? "" : "s"} may already be in your pantry under a different
            name.
          </Text>
          <Button size="compact-sm" variant="light" onClick={() => setOpen(true)}>
            Review
          </Button>
        </Group>
      </Alert>

      <Modal opened={open} onClose={() => setOpen(false)} title="Already in your pantry?" size="lg">
        <Stack>
          <Text size="sm" c="dimmed">
            Confirming links the product to the ingredient permanently, so every recipe using it counts your stock from
            now on.
          </Text>
          {pending.map((m) => (
            <Stack key={m.ingredient_id} gap={6}>
              <Group gap="xs" wrap="wrap">
                <Text size="sm" fw={600}>
                  {m.item_name}
                </Text>
                <Text size="xs" c="dimmed">
                  used in {m.recipe_names.slice(0, 3).join(", ")}
                  {m.recipe_names.length > 3 ? ` +${m.recipe_names.length - 3} more` : ""}
                </Text>
              </Group>
              {m.recipe_id && (
                <Button
                  size="compact-xs"
                  variant="light"
                  onClick={() => setRanked({ recipe: m.recipe_id, ingredient: m.ingredient_id })}
                >
                  Rank identity candidates for {m.item_name}
                </Button>
              )}
              {m.candidates.map((c) => (
                <Group key={c.item_id} justify="space-between" wrap="nowrap" gap="sm">
                  <Group gap={6} wrap="nowrap" style={{ minWidth: 0 }}>
                    <Badge size="xs" variant="light" color="gray">
                      on hand
                    </Badge>
                    <Text size="sm" truncate>
                      {c.item_name}
                    </Text>
                  </Group>
                  <Button
                    size="compact-xs"
                    loading={busy === m.ingredient_id}
                    onClick={() =>
                      setChoice({
                        ingredientId: m.ingredient_id,
                        itemId: c.item_id,
                        itemName: c.item_name,
                        currentKey: c.canonical_ingredient,
                        targetKey: m.canonical_ingredient,
                        provenance: c.canonical_provenance,
                        version: c.canonical_version,
                        revision: c.canonical_revision,
                      })
                    }
                  >
                    Same thing
                  </Button>
                </Group>
              ))}
            </Stack>
          ))}
        </Stack>
      </Modal>
      {ranked && (
        <RankedIngredientReviewModal
          recipeId={ranked.recipe}
          ingredientId={ranked.ingredient}
          onClose={() => setRanked(null)}
          onRecipeReview={() => {
            setRecipeReview(ranked);
            setRanked(null);
          }}
        />
      )}
      {recipeReview && (
        <IngredientMatchReviewModal
          recipeId={recipeReview.recipe}
          ingredientId={recipeReview.ingredient}
          onClose={() => setRecipeReview(null)}
        />
      )}
      <IdentityConfirmationModal
        choice={choice}
        busy={busy !== null}
        onClose={() => setChoice(null)}
        onConfirm={accept}
      />
    </>
  );
}
