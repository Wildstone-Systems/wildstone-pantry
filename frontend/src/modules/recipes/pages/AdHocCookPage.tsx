import {
  Alert,
  Badge,
  Button,
  Card,
  Checkbox,
  Group,
  Loader,
  Select,
  Stack,
  Text,
  TextInput,
  Textarea,
  Title,
} from "@mantine/core";
import { useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";

import { getApiErrorMessage } from "@/api/errors";
import { QuantityStepper } from "@/components/QuantityStepper";
import { useItems } from "@/modules/catalog";
import { ROUTES } from "@/routes/paths";
import { useAppState } from "@/state/AppStateProvider";

import type { AdHocFinishResult } from "../hooks/ad-hoc-hooks";
import {
  useAbandonAdHoc,
  useAdHocDrafts,
  useAdHocState,
  useAppendAdHocEntry,
  useFinishAdHoc,
  useStartAdHoc,
} from "../hooks/ad-hoc-hooks";
import { useUnitVocabulary } from "../hooks/recipes-hooks";

/** The server's placeholder for a draft that was never named — Finish requires
 *  a real name before it will save, so don't offer this one back as a default. */
const UNTITLED = "Untitled cook";

/** Trim trailing zeros so "0.50" reads as 0.5. */
function amount(value: string | number): string {
  const x = Number(value);
  return Number.isFinite(x) ? String(Number(x.toFixed(3))) : String(value);
}

/**
 * Ad hoc cooking (ADR 0024): no recipe — start from nothing, record
 * ingredients and steps as you go, end with a deducted pantry and (optionally)
 * a saved recipe. `/cook` starts or resumes; `/cook/:recipeId` is the live
 * capture surface. Everything is committed server-side per entry, so there is
 * no local draft to lose.
 */
export function AdHocCookPage() {
  const { recipeId } = useParams();
  return recipeId ? <CaptureScreen recipeId={recipeId} /> : <StartScreen />;
}

// ---------------------------------------------------------------- start

function StartScreen() {
  const navigate = useNavigate();
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);

  const drafts = useAdHocDrafts();
  const startCook = useStartAdHoc();
  const abandon = useAbandonAdHoc();

  const handleStart = async () => {
    setError(null);
    try {
      const state = await startCook.mutateAsync({ name: name.trim() || null });
      navigate(`/cook/${state.recipe.id}`);
    } catch (err) {
      setError(getApiErrorMessage(err, "Could not start the cook"));
    }
  };

  return (
    <Stack>
      <div>
        <Title order={2}>Cook something</Title>
        <Text c="dimmed" size="sm">
          No recipe? Add ingredients and steps as you go — you can save it as a recipe at the end,
          or just take what you used off the shelf.
        </Text>
      </div>

      {(drafts.data ?? []).length > 0 && (
        <Card withBorder padding="md">
          <Title order={4} mb="sm">
            Pick up where you left off
          </Title>
          <Stack gap="xs">
            {(drafts.data ?? []).map((draft) => (
              <Group key={draft.id} justify="space-between" wrap="nowrap">
                <div style={{ minWidth: 0 }}>
                  <Text size="sm" truncate>
                    {draft.name}
                  </Text>
                  <Text size="xs" c="dimmed">
                    started {new Date(draft.created_at).toLocaleString()}
                  </Text>
                </div>
                <Group gap="xs" wrap="nowrap">
                  <Button size="xs" onClick={() => navigate(`/cook/${draft.id}`)}>
                    Resume
                  </Button>
                  <Button
                    size="xs"
                    variant="subtle"
                    color="red"
                    loading={abandon.isPending}
                    onClick={() => abandon.mutate(draft.id)}
                  >
                    Discard
                  </Button>
                </Group>
              </Group>
            ))}
          </Stack>
        </Card>
      )}

      <Card withBorder padding="md">
        <Stack gap="sm">
          <TextInput
            label="What are you making? (optional)"
            placeholder="e.g. Tuesday fridge stew — you can name it at the end"
            value={name}
            onChange={(e) => setName(e.currentTarget.value)}
            maxLength={200}
          />
          <Group>
            <Button size="md" loading={startCook.isPending} onClick={handleStart}>
              Start cooking
            </Button>
            <Button variant="subtle" color="gray" onClick={() => navigate(ROUTES.recipes)}>
              Back to recipes
            </Button>
          </Group>
          {error && <Alert color="red">{error}</Alert>}
        </Stack>
      </Card>
    </Stack>
  );
}

// ---------------------------------------------------------------- capture

function CaptureScreen({ recipeId }: { recipeId: string }) {
  const navigate = useNavigate();
  const { setStatus } = useAppState();

  const state = useAdHocState(recipeId);
  const { data: items } = useItems();
  const { data: unitVocab } = useUnitVocabulary();
  const appendEntry = useAppendAdHocEntry();
  const finishCook = useFinishAdHoc();
  const abandon = useAbandonAdHoc();

  // Entry form. `pending` is the staging row for a multi-ingredient action —
  // "whisk the flour, sugar and salt" collects all three before one entry is
  // posted with the one step that used them.
  const [itemQuery, setItemQuery] = useState("");
  const [selected, setSelected] = useState<{ id: string; name: string } | null>(null);
  const [quantity, setQuantity] = useState<number | string>(1);
  const [unit, setUnit] = useState("each");
  const [pending, setPending] = useState<
    { itemId: string | null; name: string; quantity: number; unit: string }[]
  >([]);
  const [stepText, setStepText] = useState("");
  const [error, setError] = useState<string | null>(null);

  // Finish form
  const [finishing, setFinishing] = useState(false);
  const [servingsMade, setServingsMade] = useState<number | string>(2);
  const [saveRecipe, setSaveRecipe] = useState(true);
  const [finalName, setFinalName] = useState<string | null>(null);
  const [result, setResult] = useState<AdHocFinishResult | null>(null);

  const recipe = state.data?.recipe;
  const ingredients = state.data?.ingredients ?? [];
  const flagged = state.data?.flagged_units ?? [];
  const steps = useMemo(
    () => (recipe?.instructions ?? "").split("\n").filter((line) => line.trim()),
    [recipe?.instructions],
  );

  const matches = useMemo(() => {
    const needle = itemQuery.trim().toLowerCase();
    if (!needle || selected) return [];
    return (items ?? [])
      .filter((i) => i.name.toLowerCase().includes(needle))
      .slice(0, 6)
      .map((i) => ({ id: i.id, name: i.name }));
  }, [items, itemQuery, selected]);

  const ingredientName = selected?.name ?? itemQuery.trim();
  const willCreate =
    !selected &&
    ingredientName.length > 0 &&
    !(items ?? []).some((i) => i.name.toLowerCase() === ingredientName.toLowerCase());
  const canAdd = pending.length > 0 || ingredientName.length > 0 || stepText.trim().length > 0;

  /** The filled-in ingredient fields as a use, or null if empty. */
  const currentUse = () =>
    ingredientName
      ? {
          itemId: selected?.id ?? null,
          name: ingredientName,
          quantity: Number(quantity) || 1,
          unit,
        }
      : null;

  const clearIngredientFields = () => {
    setItemQuery("");
    setSelected(null);
    setQuantity(1);
  };

  const handleStage = () => {
    const use = currentUse();
    if (!use) return;
    setPending((prev) => [...prev, use]);
    clearIngredientFields();
  };

  const unitOptions = useMemo(
    () => (unitVocab ?? []).map((u) => ({ value: u.value, label: u.label })),
    [unitVocab],
  );

  const handleAdd = async () => {
    setError(null);
    // Staged uses plus whatever is still sitting in the fields — nobody should
    // have to tap "add another" for the last ingredient before posting.
    const uses = [...pending, ...(currentUse() ? [currentUse()!] : [])];
    try {
      await appendEntry.mutateAsync({
        recipeId,
        entry: {
          ingredients: uses.map((u) => ({
            item_id: u.itemId,
            name: u.itemId ? null : u.name,
            quantity: u.quantity,
            unit: u.unit,
          })),
          step_text: stepText.trim() || null,
        },
      });
      setPending([]);
      clearIngredientFields();
      setStepText("");
    } catch (err) {
      setError(getApiErrorMessage(err, "Could not add that"));
    }
  };

  const defaultName = recipe && recipe.name !== UNTITLED ? recipe.name : "";

  const handleFinish = async () => {
    setError(null);
    try {
      const outcome = await finishCook.mutateAsync({
        recipeId,
        payload: {
          servings_made: Number(servingsMade) || 1,
          save: saveRecipe,
          name: saveRecipe ? (finalName ?? defaultName)?.trim() || null : null,
        },
      });
      setResult(outcome);
    } catch (err) {
      setError(getApiErrorMessage(err, "Could not finish the cook"));
    }
  };

  const handleAbandon = async () => {
    try {
      await abandon.mutateAsync(recipeId);
      setStatus("Cook discarded — nothing was deducted");
      navigate(ROUTES.recipes);
    } catch (err) {
      setError(getApiErrorMessage(err, "Could not discard the draft"));
    }
  };

  if (state.isLoading) {
    return (
      <Group justify="center" py="xl">
        <Loader />
      </Group>
    );
  }

  if (!recipe) {
    return <Alert color="red">Cook not found — it may have been finished or discarded.</Alert>;
  }

  if (!recipe.is_draft) {
    return (
      <Alert color="blue">
        This cook is already finished.{" "}
        <Button variant="subtle" size="xs" onClick={() => navigate(`/recipes/${recipeId}`)}>
          View the recipe
        </Button>
      </Alert>
    );
  }

  // ------------------------------------------------------------- result

  if (result) {
    return (
      <Stack>
        <Title order={2}>{result.saved ? "Saved and logged" : "Logged"}</Title>
        {result.deducted.length > 0 ? (
          <Card withBorder padding="md">
            <Text size="sm" c="dimmed" mb={6}>
              Taken from your pantry
            </Text>
            <Group gap="xs">
              {result.deducted.map((d, i) => (
                <Badge key={i} variant="light">
                  {amount(d.quantity)} {d.unit} {d.item_name}
                </Badge>
              ))}
            </Group>
          </Card>
        ) : (
          <Alert color="gray">Nothing was in stock to deduct.</Alert>
        )}
        {result.shortfalls.length > 0 && (
          <Alert color="orange" variant="light">
            Your pantry didn't cover everything — the ledger was short{" "}
            {result.shortfalls
              .map((s) => `${amount(s.short)} ${s.unit} ${s.item_name}`)
              .join(", ")}
            . What was there has been used up; the counts are now corrected.
          </Alert>
        )}
        {result.unconvertible.length > 0 && (
          <Alert color="yellow" variant="light">
            Couldn't deduct {result.unconvertible.map((u) => u.item_name).join(", ")} — no way to
            convert the unit. Set a density or package size on the item to fix this for next time.
          </Alert>
        )}
        <Group>
          {result.saved ? (
            <Button size="md" onClick={() => navigate(`/recipes/${result.recipe_id}`)}>
              View the recipe
            </Button>
          ) : null}
          <Button variant="default" size="md" onClick={() => navigate(ROUTES.recipes)}>
            Done
          </Button>
        </Group>
      </Stack>
    );
  }

  // ------------------------------------------------------------- capture

  return (
    <Stack>
      <Group justify="space-between" align="flex-start">
        <div>
          <Title order={2}>{recipe.name}</Title>
          <Text c="dimmed" size="sm">
            Add each ingredient as it goes in the pan — saved as you go.
          </Text>
        </div>
        <Group gap="xs">
          <Button variant="subtle" color="gray" onClick={() => navigate(ROUTES.recipes)}>
            Keep for later
          </Button>
          <Button variant="subtle" color="red" loading={abandon.isPending} onClick={handleAbandon}>
            Discard
          </Button>
        </Group>
      </Group>

      {!finishing && (
        <Card withBorder padding="md">
          <Stack gap="sm">
            <TextInput
              label="Ingredient"
              placeholder="Search your pantry, or type a new name"
              value={selected ? selected.name : itemQuery}
              onChange={(e) => {
                setItemQuery(e.currentTarget.value);
                setSelected(null);
              }}
            />
            {matches.length > 0 && (
              <Group gap="xs">
                {matches.map((m) => (
                  <Button key={m.id} size="xs" variant="light" onClick={() => setSelected(m)}>
                    {m.name}
                  </Button>
                ))}
              </Group>
            )}
            {willCreate && (
              <Text size="xs" c="dimmed">
                "{ingredientName}" isn't in your catalog yet — it'll be added. Use the plain food
                name ("avocado", not "2 avocados, diced").
              </Text>
            )}
            <Group align="flex-end" gap="md" wrap="wrap">
              <QuantityStepper
                label="How much went in?"
                ariaLabel="ingredient amount"
                size="sm"
                min={0}
                step={quantityStep(Number(quantity) || 0)}
                decimalScale={3}
                value={quantity}
                onChange={setQuantity}
              />
              <Select
                label="Unit"
                data={unitOptions}
                value={unit}
                onChange={(v) => setUnit(v ?? "each")}
                allowDeselect={false}
                searchable
                w={130}
              />
              {/* Stage it and keep going — a dressing's oil, vinegar and
                  mustard are one action, so they should land as one entry
                  with the one step that used them. */}
              <Button variant="default" mb={2} disabled={!ingredientName} onClick={handleStage}>
                + Another ingredient
              </Button>
            </Group>
            {pending.length > 0 && (
              <Group gap="xs">
                {pending.map((use, i) => (
                  <Badge
                    key={i}
                    variant="light"
                    size="lg"
                    rightSection={
                      <span
                        role="button"
                        aria-label={`Remove ${use.name}`}
                        style={{ cursor: "pointer" }}
                        onClick={() => setPending((prev) => prev.filter((_, j) => j !== i))}
                      >
                        ×
                      </span>
                    }
                  >
                    {amount(use.quantity)} {use.unit} {use.name}
                  </Badge>
                ))}
              </Group>
            )}
            <Textarea
              label="What did you do? (optional)"
              placeholder="e.g. Diced and sweated in butter until soft"
              value={stepText}
              onChange={(e) => setStepText(e.currentTarget.value)}
              autosize
              minRows={1}
            />
            <Group justify="space-between">
              <Text size="xs" c="dimmed">
                One action per entry — several ingredients can go in together
                ("whisk the dry ingredients"), with or without a step.
              </Text>
              <Button
                size="md"
                disabled={!canAdd}
                loading={appendEntry.isPending}
                onClick={handleAdd}
              >
                Add to cook
              </Button>
            </Group>
            {flagged.length > 0 && (
              <Alert color="yellow" variant="light">
                Unit "{flagged[0].unit}" isn't recognised
                {flagged[0].suggestion ? ` — did you mean "${flagged[0].suggestion}"?` : ""} The
                line is kept, but it can't be deducted until the unit is fixed.
              </Alert>
            )}
            {error && <Alert color="red">{error}</Alert>}
          </Stack>
        </Card>
      )}

      <Card withBorder padding="md">
        <Text size="xs" c="dimmed" mb={6}>
          Used so far
        </Text>
        {ingredients.length === 0 ? (
          <Text size="sm" c="dimmed">
            Nothing yet.
          </Text>
        ) : (
          <Group gap="xs">
            {ingredients.map((line) => (
              <Badge key={line.ingredient_id} variant="light" size="lg">
                {amount(line.quantity_required)} {line.unit} {line.item_name}
              </Badge>
            ))}
          </Group>
        )}
      </Card>

      <Card withBorder padding="md">
        <Text size="xs" c="dimmed" mb={6}>
          Steps so far
        </Text>
        {steps.length === 0 ? (
          <Text size="sm" c="dimmed">
            No steps written down yet — that's fine for a quick cook.
          </Text>
        ) : (
          <Stack gap={4}>
            {steps.map((line, i) => (
              <Text key={i} size="sm">
                {line}
              </Text>
            ))}
          </Stack>
        )}
      </Card>

      {finishing ? (
        <Card withBorder padding="md">
          <Stack gap="sm">
            <Title order={4}>Finish up</Title>
            <QuantityStepper
              label="How many servings did you make?"
              ariaLabel="servings made"
              min={0.25}
              step={1}
              decimalScale={2}
              value={servingsMade}
              onChange={setServingsMade}
            />
            <Checkbox
              label="Save this as a recipe"
              description="Keeps the ingredients and steps so you can cook it again (and see its nutrition)."
              checked={saveRecipe}
              onChange={(e) => setSaveRecipe(e.currentTarget.checked)}
            />
            {saveRecipe && (
              <TextInput
                label="Recipe name"
                placeholder="Give it a name to save it"
                value={finalName ?? defaultName}
                onChange={(e) => setFinalName(e.currentTarget.value)}
                maxLength={200}
              />
            )}
            {!saveRecipe && (
              <Text size="xs" c="dimmed">
                The ingredients still come off your pantry; the recipe itself is thrown away.
              </Text>
            )}
            <Group>
              <Button
                size="md"
                color="green"
                loading={finishCook.isPending}
                disabled={saveRecipe && !((finalName ?? defaultName) || "").trim()}
                onClick={handleFinish}
              >
                Finish &amp; log it
              </Button>
              <Button variant="default" size="md" onClick={() => setFinishing(false)}>
                Keep cooking
              </Button>
            </Group>
            {error && <Alert color="red">{error}</Alert>}
          </Stack>
        </Card>
      ) : (
        <Group justify="flex-end">
          <Button
            size="md"
            color="green"
            disabled={ingredients.length === 0 && steps.length === 0}
            onClick={() => setFinishing(true)}
          >
            Finish cooking
          </Button>
        </Group>
      )}

      <Text size="xs" c="dimmed" ta="center">
        Nothing leaves your pantry until you finish — and a shortfall never blocks you: what you
        record is what counts.
      </Text>
    </Stack>
  );
}

/** A nudge sized to the amount, same convention as cook-along. */
function quantityStep(value: number): number {
  if (value < 1) return 0.25;
  if (value < 20) return 1;
  return 10;
}
