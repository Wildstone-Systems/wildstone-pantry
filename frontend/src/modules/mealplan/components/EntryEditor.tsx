import {
  Alert,
  Badge,
  Button,
  Group,
  Modal,
  NumberInput,
  SegmentedControl,
  Select,
  Stack,
  Switch,
  Text,
  TextInput,
  Textarea,
  type ComboboxItem,
} from "@mantine/core";
import { useEffect, useMemo, useState } from "react";

import { getApiErrorMessage } from "@/api/errors";
import { useHouseholdProfiles, useItems, useRecipes } from "@/api/hooks";

import {
  MEAL_TYPES,
  MOSTLY_COMPLETE,
  buildRecipeOptions,
  completenessLabel,
  useRecipeCompleteness,
  useCreateMealPlanEntry,
  useDeleteMealPlanEntry,
  useSetMealPlanStatus,
  useUpdateMealPlanEntry,
  type MealPlanEntry,
  type MealType,
  type RecipeOption,
} from "../hooks/mealplan-hooks";

function completenessColor(score: number): string {
  if (score >= 1) return "green";
  if (score >= MOSTLY_COMPLETE) return "teal";
  if (score >= 0.5) return "yellow";
  return "gray";
}

function CompletenessBadge({ option }: { option: RecipeOption }) {
  const label = completenessLabel(option.completeness);
  if (!option.completeness || !label) return null;
  return (
    <Badge size="xs" variant="light" color={completenessColor(option.completeness.available_score)}>
      {label}
    </Badge>
  );
}

type Kind = "recipe" | "item" | "text";

export type EditorTarget =
  | { mode: "add"; plan_date: string; meal_type: MealType }
  | { mode: "edit"; entry: MealPlanEntry };

const MEAL_TYPE_OPTIONS = MEAL_TYPES.map((m) => ({ value: m, label: m[0].toUpperCase() + m.slice(1) }));
const EVERYONE = "__household__";

export function EntryEditor({
  target,
  dayOptions,
  onClose,
}: {
  target: EditorTarget | null;
  dayOptions: { value: string; label: string }[];
  onClose: () => void;
}) {
  const recipes = useRecipes();
  const completeness = useRecipeCompleteness();
  const [mostlyCompleteOnly, setMostlyCompleteOnly] = useState(false);
  const items = useItems();
  const profiles = useHouseholdProfiles();
  const create = useCreateMealPlanEntry();
  const update = useUpdateMealPlanEntry();
  const remove = useDeleteMealPlanEntry();
  const setStatus = useSetMealPlanStatus();

  const [kind, setKind] = useState<Kind>("recipe");
  const [recipeId, setRecipeId] = useState<string | null>(null);
  const [itemId, setItemId] = useState<string | null>(null);
  const [title, setTitle] = useState("");
  const [planDate, setPlanDate] = useState("");
  const [mealType, setMealType] = useState<MealType>("dinner");
  const [servings, setServings] = useState<number | "">("");
  const [forWho, setForWho] = useState<string>(EVERYONE);
  const [note, setNote] = useState("");
  const [error, setError] = useState<string | null>(null);

  // Load the form whenever the modal opens on a new target.
  useEffect(() => {
    setError(null);
    if (!target) return;
    if (target.mode === "add") {
      setKind("recipe");
      setRecipeId(null);
      setItemId(null);
      setTitle("");
      setPlanDate(target.plan_date);
      setMealType(target.meal_type);
      setServings("");
      setForWho(EVERYONE);
      setNote("");
    } else {
      const e = target.entry;
      setKind(e.kind);
      setRecipeId(e.recipe_id);
      setItemId(e.item_id);
      setTitle(e.kind === "text" ? e.meal : "");
      setPlanDate(e.plan_date);
      setMealType(e.meal_type);
      setServings(e.servings);
      setForWho(e.profile_id ?? EVERYONE);
      setNote(e.note ?? "");
    }
  }, [target]);

  const recipeOptions = useMemo(() => {
    const options = buildRecipeOptions(recipes.data ?? [], completeness.data ?? [], { mostlyCompleteOnly });
    // Keep the chosen recipe selectable even when the filter would hide it.
    if (recipeId && !options.some((o) => o.value === recipeId)) {
      const all = buildRecipeOptions(recipes.data ?? [], completeness.data ?? []);
      const chosen = all.find((o) => o.value === recipeId);
      if (chosen) options.unshift(chosen);
    }
    return options;
  }, [recipes.data, completeness.data, mostlyCompleteOnly, recipeId]);
  const optionById = useMemo(() => new Map(recipeOptions.map((o) => [o.value, o])), [recipeOptions]);
  const chosenRecipe = recipeId ? (optionById.get(recipeId) ?? null) : null;
  const itemOptions = useMemo(() => (items.data ?? []).map((i) => ({ value: i.id, label: i.name })), [items.data]);
  const profileOptions = useMemo(
    () => [
      { value: EVERYONE, label: "Whole household" },
      ...(profiles.data ?? []).filter((p) => p.is_active).map((p) => ({ value: p.id, label: p.display_name })),
    ],
    [profiles.data],
  );

  const meal =
    kind === "recipe"
      ? { recipe_id: recipeId }
      : kind === "item"
        ? { item_id: itemId }
        : { title: title.trim() || null };
  const hasMeal = Object.values(meal)[0] != null;
  const busy = create.isPending || update.isPending || remove.isPending || setStatus.isPending;

  async function run(action: () => Promise<unknown>) {
    setError(null);
    try {
      await action();
      onClose();
    } catch (err) {
      setError(getApiErrorMessage(err, "Could not save the meal"));
    }
  }

  function save() {
    const common = {
      plan_date: planDate,
      meal_type: mealType,
      servings: servings === "" ? null : servings,
      profile_id: forWho === EVERYONE ? null : forWho,
      note: note.trim() || null,
    };
    if (target?.mode === "edit") {
      const { servings: s, ...rest } = common;
      // Servings can't be cleared once pinned; leave it out if blank.
      const patch = { ...rest, ...(s == null ? {} : { servings: s }), ...meal };
      return run(() => update.mutateAsync({ id: target.entry.id, patch }));
    }
    return run(() => create.mutateAsync({ ...common, ...meal }));
  }

  const editing = target?.mode === "edit" ? target.entry : null;

  return (
    <Modal opened={target != null} onClose={onClose} title={editing ? "Edit meal" : "Plan a meal"}>
      <Stack gap="sm">
        {error && (
          <Alert color="red" variant="light">
            {error}
          </Alert>
        )}
        <SegmentedControl
          value={kind}
          onChange={(v) => setKind(v as Kind)}
          data={[
            { value: "recipe", label: "Recipe" },
            { value: "item", label: "Item" },
            { value: "text", label: "Other" },
          ]}
        />
        {kind === "recipe" && (
          <Stack gap={6}>
            <Select
              label="Recipe"
              description="Most complete first"
              placeholder="Pick a recipe"
              searchable
              data={recipeOptions.map(({ value, label }) => ({ value, label }))}
              value={recipeId}
              onChange={setRecipeId}
              nothingFoundMessage={mostlyCompleteOnly ? "No mostly-complete recipes" : "No recipes match"}
              renderOption={({ option }: { option: ComboboxItem }) => {
                const full = optionById.get(option.value);
                return (
                  <Group justify="space-between" wrap="nowrap" w="100%" gap="xs">
                    <Text size="sm" truncate>
                      {option.label}
                    </Text>
                    {full && <CompletenessBadge option={full} />}
                  </Group>
                );
              }}
            />
            <Switch
              size="xs"
              label="Mostly complete only"
              checked={mostlyCompleteOnly}
              onChange={(e) => setMostlyCompleteOnly(e.currentTarget.checked)}
            />
            {chosenRecipe?.completeness && chosenRecipe.completeness.needed_count > 0 && (
              <Group gap={6} wrap="wrap">
                <CompletenessBadge option={chosenRecipe} />
                {chosenRecipe.completeness.missing_items.length > 0 && (
                  <Text size="xs" c="dimmed">
                    To buy: {chosenRecipe.completeness.missing_items.join(", ")}
                  </Text>
                )}
              </Group>
            )}
          </Stack>
        )}
        {kind === "item" && (
          <Select
            label="Item"
            placeholder="Pick an item"
            searchable
            data={itemOptions}
            value={itemId}
            onChange={setItemId}
            nothingFoundMessage="No items match"
          />
        )}
        {kind === "text" && (
          <TextInput
            label="What's planned"
            placeholder="Takeout, eating out, leftovers…"
            value={title}
            onChange={(e) => setTitle(e.currentTarget.value)}
            maxLength={200}
          />
        )}
        <Group grow>
          <Select
            label="Day"
            data={dayOptions}
            value={planDate}
            onChange={(v) => v && setPlanDate(v)}
            allowDeselect={false}
          />
          <Select
            label="Meal time"
            data={MEAL_TYPE_OPTIONS}
            value={mealType}
            onChange={(v) => v && setMealType(v as MealType)}
            allowDeselect={false}
          />
        </Group>
        <Group grow>
          <NumberInput
            label="Servings"
            placeholder={kind === "recipe" ? "Recipe default" : "1"}
            min={1}
            max={100}
            allowDecimal={false}
            value={servings}
            onChange={(v) => setServings(typeof v === "number" ? v : "")}
          />
          <Select label="For" data={profileOptions} value={forWho} onChange={(v) => setForWho(v ?? EVERYONE)} />
        </Group>
        <Textarea label="Note" autosize minRows={1} value={note} onChange={(e) => setNote(e.currentTarget.value)} />

        <Group justify="space-between" mt="xs">
          {editing ? (
            <Group gap="xs">
              <Button
                variant="subtle"
                color="red"
                disabled={busy}
                onClick={() => run(() => remove.mutateAsync(editing.id))}
              >
                Remove
              </Button>
              {editing.status === "planned" ? (
                <Button
                  variant="subtle"
                  disabled={busy}
                  onClick={() => run(() => setStatus.mutateAsync({ id: editing.id, action: "skip" }))}
                >
                  Skip
                </Button>
              ) : (
                <Button
                  variant="subtle"
                  disabled={busy}
                  onClick={() => run(() => setStatus.mutateAsync({ id: editing.id, action: "reset" }))}
                >
                  Back to planned
                </Button>
              )}
            </Group>
          ) : (
            <span />
          )}
          <Button onClick={save} disabled={!hasMeal || busy} loading={create.isPending || update.isPending}>
            {editing ? "Save" : "Add to plan"}
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}
