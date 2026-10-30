import {
  Alert,
  Button,
  Card,
  Checkbox,
  Group,
  Paper,
  Select,
  Stack,
  Table,
  Text,
  TextInput,
  Textarea,
  Title,
} from "@mantine/core";
import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { Controller, useFieldArray, useForm } from "react-hook-form";
import { useLocation, useNavigate, useParams } from "react-router";

import { api } from "@/api/client";
import { getApiErrorMessage } from "@/api/errors";
import { QuantityStepper } from "@/components/QuantityStepper";
import { ClassifierVerificationNotice } from "@/components/ClassifierVerificationNotice";
import { useAuth } from "@/state/AuthProvider";
import { useItems } from "@/api/hooks";
import { ROUTES } from "@/routes/paths";

import { recipeFormSchema, type RecipeFormValues } from "../forms/recipe-schema";
import { RecipeImportReceipt } from "../components/RecipeImportReceipt";
import { useUnitVocabulary, type RecipeWithIngredients, type RecipeParseApiResponse, type RecipeImportSource, type RecipeImportReview } from "../hooks/recipes-hooks";

/** A recipe parsed from an image/PDF/text, passed via router state for review. */
type ImportedDraft = RecipeParseApiResponse;

// Shown only until the vocabulary loads from the backend (the source of truth).
const FALLBACK_UNITS = [
  { value: "each", label: "each" },
  { value: "g", label: "g" },
  { value: "ml", label: "ml" },
  { value: "cup", label: "cup" },
];

export function EditRecipePage() {
  const { recipeId } = useParams();
  const navigate = useNavigate();
  const location = useLocation();
  const queryClient = useQueryClient();
  const { isAdmin } = useAuth();
  // The /recipes/new route carries no :recipeId, so absence marks a new recipe.
  // Checking only for the literal "new" never matched under that route and sent
  // the page off to fetch /api/recipes/undefined; both shapes are accepted.
  const isNew = !recipeId || recipeId === "new";

  // A recipe parsed from a photo, PDF or pasted text, handed over by the
  // recipes page for review. Nothing is written until the user saves, so a
  // mis-parsed step can simply be deleted first.
  const importedDraft = (location.state as { draft?: ImportedDraft } | null)?.draft ?? null;
  const importSource: RecipeImportSource | null = importedDraft && isNew ? (importedDraft.structured_source
    ? { kind: "structured", structured: importedDraft.structured_source }
    : { kind: importedDraft.source_filename ? "document" : "text", raw_text: importedDraft.raw_text, filename: importedDraft.source_filename ?? null }) : null;
  const [review, setReview] = useState<{ result: RecipeImportReview; packet: string } | null>(null);
  const [confirmed, setConfirmed] = useState(false);
  const [reviewBusy, setReviewBusy] = useState(false);
  const latestPacket = useRef("");
  const reviewGeneration = useRef(0);

  const [status, setStatus] = useState("");
  const [linkingOutput, setLinkingOutput] = useState(false);

  const itemsQuery = useItems();
  const unitVocab = useUnitVocabulary();
  const unitData = (unitVocab.data ?? FALLBACK_UNITS).map((u) => ({ value: u.value, label: u.label }));
  const unitValues = new Set(unitData.map((u) => u.value));
  // A Select can't display a value it has no option for, so add any unrecognised
  // unit (e.g. "pinch" from an import) as a one-off "(unsupported)" option — it
  // stays visible and flagged until the user picks a real one (ADR 0017 §3).
  const unitOptionsFor = (unit: string | undefined) =>
    unit && !unitValues.has(unit) ? [...unitData, { value: unit, label: `${unit} (unsupported)` }] : unitData;

  const {
    control,
    handleSubmit,
    reset,
    watch,
    setValue,
    formState: { errors, isValid },
  } = useForm<RecipeFormValues>({
    resolver: zodResolver(recipeFormSchema),
    mode: "onChange",
    defaultValues: {
      name: "",
      instructions: "",
      servings: 1,
      ingredients: [],
      output_item_id: null,
      output_quantity: null,
      output_unit: null,
    },
  });

  const { fields, append, remove } = useFieldArray({ control, name: "ingredients" });

  const recipeQuery = useQuery({
    queryKey: ["recipe", recipeId],
    queryFn: async () => {
      if (isNew) return null;
      const res = await api.get<RecipeWithIngredients>(`/api/recipes/${recipeId}`);
      return res.data;
    },
    enabled: !isNew,
  });

  // Catalog identity is resolved by the backend shared with structured imports.
  // Keep the source visible; saving is the first ingredient/catalog write.
  useEffect(() => {
    if (!importedDraft || !isNew) return;
    reset({
      name: importedDraft.title ?? "",
      instructions: importedDraft.instructions ?? "",
      servings: importedDraft.servings ?? 1,
      ingredients: (importedDraft.ingredients ?? []).map((row) => {
        const matched = row.item_id;
        return {
          item_id: matched ?? "",
          quantity_required: Number(row.quantity) || 1,
          unit: row.unit || "each",
          optional: row.optional ?? false,
          yield_basis: row.yield_basis ?? undefined,
          quantity_mode: row.quantity_mode ?? undefined,
          pending_name: matched ? undefined : row.name,
          raw_line: row.raw_line,
          source_line_id: row.source_line_id,
          resolution: row.resolution,
          suggestion: row.suggestion,
        };
      }),
      output_item_id: null,
      output_quantity: null,
      output_unit: null,
    });
  }, [importedDraft, isNew, reset]);

  useEffect(() => {
    if (recipeQuery.data) {
      reset({
        name: recipeQuery.data.recipe.name,
        instructions: recipeQuery.data.recipe.instructions ?? "",
        servings: recipeQuery.data.recipe.servings || 1,
        ingredients: recipeQuery.data.ingredients.map((ing) => ({
          item_id: ing.item_id,
          // The API serializes Decimal fields as strings (e.g. "0.250"), but
          // the form schema requires a real number — without this coercion
          // every ingredient row fails validation on load, and the Save
          // button locks out for every existing recipe, regardless of units.
          quantity_required: Number(ing.quantity_required) || 0,
          unit: ing.unit,
          optional: ing.optional ?? false,
          yield_basis: ing.yield_basis ?? undefined,
          quantity_mode: ing.quantity_mode ?? undefined,
        })),
        output_item_id: recipeQuery.data.recipe.output_item_id ?? null,
        output_quantity: recipeQuery.data.recipe.output_quantity ?? null,
        output_unit: recipeQuery.data.recipe.output_unit ?? null,
      });
    }
  }, [recipeQuery.data, reset]);

  const buildPayload = (values: RecipeFormValues) => {
      const body = {
        name: values.name,
        instructions: values.instructions ?? "",
        servings: values.servings || 1,
        output_item_id: values.output_item_id || null,
        output_quantity: values.output_quantity ?? null,
        output_unit: values.output_item_id ? values.output_unit || "each" : null,
      };
      // An unused Add Ingredient row has no identity. Keep named import rows,
      // including ones that still need catalog creation, but omit blank rows.
      const ingredients = values.ingredients
        .filter((ing) => ing.item_id || ing.pending_name?.trim())
        .map((ing) => ({
          ...(ing.item_id ? { item_id: ing.item_id } : { name: ing.pending_name?.trim() ?? "" }),
          quantity: ing.quantity_required,
          unit: ing.unit,
          optional: ing.optional ?? false,
          ...(ing.yield_basis !== undefined ? { yield_basis: ing.yield_basis } : {}),
          ...(ing.quantity_mode !== undefined ? { quantity_mode: ing.quantity_mode } : {}),
          ...(ing.raw_line ? { raw_line: ing.raw_line } : {}),
          ...(ing.source_line_id ? { source_line_id: ing.source_line_id } : {}),
        }));
      const payload = {
        ...body,
        ingredients,
        create_missing_items: true,
        ...(importedDraft ? {
          source: importedDraft.source ?? (importedDraft.source_filename ? "image" : "text"),
          source_filename: importedDraft.source_filename ?? null,
          source_text: importedDraft.raw_text,
          ...(importedDraft.structured_source ? {
            image_url: importedDraft.structured_source.image_url,
            external_source: importedDraft.structured_source.external_source,
          } : {}),
        } : {}),
      };
      return payload;
  };
  // Preview and submit must share the resolver's transformations (including
  // trimmed titles). Invalid intermediate form values never become a request.
  const watchedValues = watch();
  const normalizedValues = recipeFormSchema.safeParse(watchedValues);
  const currentPacket = normalizedValues.success
    ? JSON.stringify({ recipe: buildPayload(normalizedValues.data), source: importSource })
    : "";
  const rawFormState = JSON.stringify(watchedValues);
  latestPacket.current = currentPacket;
  useEffect(() => {
    setReview(null);
    setConfirmed(false);
    reviewGeneration.current += 1;
    setReviewBusy(false);
  }, [currentPacket, rawFormState]);
  const requestReview = async (classifier: boolean) => {
    const packet = latestPacket.current;
    if (!packet) {
      setStatus("Fix the recipe form before requesting a review.");
      return;
    }
    const generation = ++reviewGeneration.current;
    setConfirmed(false);
    setReviewBusy(true);
    try {
      const response = await api.post<RecipeImportReview>(`/api/recipes/import-${classifier ? "review" : "preview"}`, JSON.parse(packet));
      if (generation !== reviewGeneration.current || packet !== latestPacket.current) return;
      setReview({ result: response.data, packet });
      setStatus("");
    } catch (error) {
      if (generation !== reviewGeneration.current || packet !== latestPacket.current) return;
      setStatus(getApiErrorMessage(error, "Review unavailable. Use Check current recipe for manual review."));
    } finally {
      if (generation === reviewGeneration.current) setReviewBusy(false);
    }
  };
  const saveMutation = useMutation({
    mutationFn: async (values: RecipeFormValues) => {
      const payload = buildPayload(recipeFormSchema.parse(values));
      if (importSource && (!confirmed || !review || review.packet !== latestPacket.current)) {
        throw new Error("Check the current recipe and confirm its source before saving.");
      }
      const res = isNew
        ? await api.post<{ id: string }>(importSource ? "/api/recipes/import-reviewed" : "/api/recipes", importSource ? {
          recipe: payload, source: importSource, confirmed: true, reviewed_input_hash: review!.result.binding,
        } : payload)
        : await api.put<{ id: string }>(`/api/recipes/${recipeId}`, payload);
      const savedId = isNew ? res.data.id : (recipeId as string);
      return savedId;
    },
    onSuccess: async (_savedId) => {
      queryClient.invalidateQueries({ queryKey: ["recipes"] });
      queryClient.invalidateQueries({ queryKey: ["recipeSuggestions"] });
      queryClient.invalidateQueries({ queryKey: ["items"] });
      setStatus("Recipe saved!");
      // Back to the list. /recipes/:id is not a route — navigating there hit the
      // catch-all and bounced to the dashboard, which read as "it did not save".
      setTimeout(() => navigate(ROUTES.recipes), 800);
    },
    onError: (error) => {
      if (importSource) setConfirmed(false);
      setStatus(getApiErrorMessage(error, "Failed to save recipe"));
    },
  });

  const onSubmit = handleSubmit((values) => saveMutation.mutate(values));

  // "The recipe usually creates the recipe name" — cooking chili produces
  // chili. One click links (or, if it doesn't exist yet, creates) a catalog
  // item matching the recipe's own name, instead of requiring the output to
  // already be something on hand — the Output item Select can otherwise only
  // point at existing items.
  const useRecipeNameAsOutput = async () => {
    const name = watch("name")?.trim();
    if (!name) return;
    setLinkingOutput(true);
    try {
      const existing = itemsQuery.data?.find((i) => i.name.trim().toLowerCase() === name.toLowerCase());
      let itemId = existing?.id;
      if (!itemId) {
        const created = await api.post<{ id: string }>("/api/items", { name });
        itemId = created.data.id;
        await queryClient.invalidateQueries({ queryKey: ["items"] });
      }
      setValue("output_item_id", itemId, { shouldValidate: true });
      if (!watch("output_quantity")) setValue("output_quantity", 1);
      if (!watch("output_unit")) setValue("output_unit", "each");
    } catch (err) {
      setStatus(getApiErrorMessage(err, "Could not link the output item"));
    } finally {
      setLinkingOutput(false);
    }
  };

  // Block save while any ingredient carries a unit outside the vocabulary (e.g. an
  // unremapped "pinch" from an import, or "servings"/"large" from Spoonacular) —
  // the server would 422 it anyway (§3). Track *which* rows, not just whether any
  // do, so the block comes with a reason instead of a Save button that's
  // disabled for no visible reason.
  const itemNameById = new Map((itemsQuery.data ?? []).map((i) => [i.id, i.name]));
  const unknownUnitRows = (watch("ingredients") ?? [])
    .map((ing, idx) => ({ ing, idx }))
    .filter(({ ing }) => ing.unit && !unitValues.has(ing.unit));
  const hasUnknownUnit = unknownUnitRows.length > 0;

  return (
    <Stack component="form" onSubmit={onSubmit} gap="md">
      <Group justify="space-between">
        <Title>{isNew ? "New Recipe" : "Edit Recipe"}</Title>
        <Group>
          <Button type="button" variant="outline" onClick={() => navigate(-1)}>
            Cancel
          </Button>
          <Button type="submit" loading={saveMutation.isPending} disabled={!isValid || hasUnknownUnit || Boolean(importSource && (!confirmed || !review || review.packet !== currentPacket))}>
            Save Recipe
          </Button>
        </Group>
      </Group>

      {status && (
        <Paper p="sm" bg={status.includes("Failed") ? "red.1" : "green.1"}>
          <Text>{status}</Text>
        </Paper>
      )}

      {hasUnknownUnit && (
        <Alert color="orange" title="Can't save yet — fix these units first">
          <Stack gap={4}>
            {unknownUnitRows.map(({ ing, idx }) => (
              <Text size="sm" key={idx}>
                Row {idx + 1} ({itemNameById.get(ing.item_id) ?? ing.pending_name ?? "this ingredient"}): "
                {ing.unit}" isn't a recognized unit — pick one in the Unit column below.
              </Text>
            ))}
          </Stack>
        </Alert>
      )}

      {importedDraft && (
        <Alert title="Review imported recipe" color="blue">
          Check ingredient identities and quantities against the source before saving.
          {importedDraft.source_filename && <Text size="sm">Source file: {importedDraft.source_filename}</Text>}
          {importedDraft.raw_text && <Text size="sm" style={{ whiteSpace: "pre-wrap" }}>{importedDraft.raw_text}</Text>}
        </Alert>
      )}
      {recipeQuery.data && <RecipeImportReceipt recipe={recipeQuery.data.recipe} />}

      {importSource && <Stack gap="sm">
        <ClassifierVerificationNotice />
        <Text size="sm">Edit or remove incorrect ingredient rows, retain preparation details, and check quantities yourself. Rules and model hints cannot certify this recipe.</Text>
        <Group>
          <Button type="button" variant="outline" loading={reviewBusy} disabled={!isValid || !currentPacket} onClick={() => void requestReview(false)}>Check current recipe</Button>
          {isAdmin && <Button type="button" variant="subtle" disabled={reviewBusy || !isValid || !currentPacket} onClick={() => void requestReview(true)}>Ask classifier for optional hints</Button>}
        </Group>
        {review && <Alert color="yellow" title={<Text fw={600} c="var(--mantine-color-text)">Source-linked review</Text>}>
          <Text size="sm">{review.result.warning}</Text>
          {review.result.status !== "rules" && <Text size="sm">Classifier: {review.result.status.replaceAll("_", " ")}. Manual verification is still required.</Text>}
          {(review.result.lines ?? []).map((line) => {
            const hint = review.result.annotations?.find((a) => a.source_line_id === line.source_line_id);
            return <Paper key={line.source_line_id} p="xs" mt="xs" withBorder>
              <Text size="xs">{line.field_path ?? `Source line ${(line.line_index ?? 0) + 1}`} · {line.role.replaceAll("_", " ")}</Text>
              <Text size="sm" style={{ whiteSpace: "pre-wrap" }}>{line.raw_line}</Text>
              {line.warnings.map((warning, index) => <Text key={index} size="sm" c="var(--mantine-color-text)">{warning.replaceAll("_", " ")}</Text>)}
              {hint && <Text size="sm">Optional hint: {hint.role ?? "uncertain"}; {hint.score == null ? hint.status : `${Math.round(hint.score * 100)}% model score${hint.below_threshold ? " (below threshold)" : ""}`}. Verify this yourself.</Text>}
            </Paper>;
          })}
          <Checkbox mt="sm" label="I checked the source, ingredient identities, quantities and remaining warnings for this edited recipe." checked={confirmed} onChange={(event) => setConfirmed(event.currentTarget.checked)} />
        </Alert>}
      </Stack>}

      <Card>
        <Stack gap="sm">
          <Group align="flex-end" grow>
            <Controller
              name="name"
              control={control}
              render={({ field }) => (
                <TextInput
                  label="Recipe Name"
                  placeholder="e.g., Spaghetti Bolognese"
                  required
                  error={errors.name?.message}
                  {...field}
                />
              )}
            />
            <Controller
              name="servings"
              control={control}
              render={({ field }) => (
                <QuantityStepper
                  label="Servings"
                  ariaLabel="servings"
                  min={1}
                  value={field.value ?? 1}
                  onChange={(v) => field.onChange(Number(v) || 1)}
                />
              )}
            />
          </Group>
          <Controller
            name="instructions"
            control={control}
            render={({ field }) => (
              <Textarea
                label="Instructions"
                placeholder="Enter cooking instructions..."
                autosize
                minRows={14}
                maxRows={40}
                {...field}
                value={field.value ?? ""}
              />
            )}
          />
        </Stack>
      </Card>

      <Card>
        <Title order={4} mb="sm">
          Yields (optional)
        </Title>
        <Text size="sm" c="dimmed" mb="sm">
          Cooking consumes the ingredients and <b>produces</b> this item into inventory (e.g. a loaf of bread,
          or the finished dish itself as a leftover).
        </Text>
        <Group grow align="flex-end">
          <Controller
            name="output_item_id"
            control={control}
            render={({ field }) => (
              <Stack gap={4}>
                <Select
                  label="Output item"
                  placeholder="None — just consumes ingredients"
                  clearable
                  searchable
                  data={itemsQuery.data?.map((i) => ({ value: i.id, label: i.name })) || []}
                  value={field.value ?? null}
                  onChange={(v) => field.onChange(v)}
                />
                {watch("name")?.trim() && !importSource ? (
                  <Button
                    type="button"
                    size="compact-xs"
                    variant="subtle"
                    loading={linkingOutput}
                    onClick={useRecipeNameAsOutput}
                  >
                    Use "{watch("name").trim()}" as the output
                  </Button>
                ) : null}
              </Stack>
            )}
          />
          <Controller
            name="output_quantity"
            control={control}
            render={({ field }) => (
              <QuantityStepper
                label="Qty per serving"
                ariaLabel="qty per serving"
                min={0}
                step={0.25}
                decimalScale={3}
                value={field.value ?? ""}
                onChange={(v) => field.onChange(v === "" ? null : Number(v))}
              />
            )}
          />
          <Controller
            name="output_unit"
            control={control}
            render={({ field }) => (
              <Select
                label="Output unit"
                data={unitOptionsFor(field.value ?? undefined)}
                value={field.value ?? "each"}
                onChange={(v) => field.onChange(v)}
              />
            )}
          />
        </Group>
      </Card>

      <Card>
        <Group justify="space-between" mb="sm">
          <Title order={4}>Ingredients</Title>
          <Button
            type="button"
            size="sm"
            variant="outline"
            onClick={() => append({ item_id: "", quantity_required: 1, unit: "each", optional: false })}
          >
            + Add Ingredient
          </Button>
        </Group>

        {fields.length > 0 ? (
          <Table.ScrollContainer minWidth={560}>
          <Table>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Item</Table.Th>
                <Table.Th>Quantity</Table.Th>
                <Table.Th>Unit</Table.Th>
                <Table.Th>Optional</Table.Th>
                <Table.Th></Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {fields.map((row, idx) => (
                <Table.Tr key={row.id}>
                  <Table.Td>
                    <Controller
                      name={`ingredients.${idx}.item_id`}
                      control={control}
                      render={({ field }) => (
                        <Select
                          placeholder="Select item"
                          data={itemsQuery.data?.map((i) => ({ value: i.id, label: i.name })) || []}
                          searchable
                          clearable
                          style={{ width: 200 }}
                          {...field}
                          onChange={(val) => field.onChange(val ?? "")}
                        />
                      )}
                    />
                    {row.raw_line && <Text size="xs" c="dimmed">Source: {row.raw_line}</Text>}
                    {row.resolution && <Text size="xs" c="dimmed">Catalog: {row.resolution.replaceAll("_", " ")}</Text>}
                    {row.suggestion && <Text size="xs" c="orange">{row.suggestion}</Text>}
                    {!watch(`ingredients.${idx}.item_id`) && <Controller
                      name={`ingredients.${idx}.pending_name`}
                      control={control}
                      render={({ field }) => <TextInput mt={4} label={`Ingredient name ${idx + 1}`} placeholder="Enter an ingredient or remove this row" {...field} value={field.value ?? ""} />}
                    />}
                  </Table.Td>
                  <Table.Td>
                    <Controller
                      name={`ingredients.${idx}.quantity_required`}
                      control={control}
                      render={({ field }) => (
                        <QuantityStepper
                          size="sm"
                          ariaLabel="ingredient quantity"
                          min={0}
                          step={0.25}
                          decimalScale={3}
                          value={field.value ?? 0}
                          onChange={(val) => field.onChange(Number(val) || 0)}
                        />
                      )}
                    />
                  </Table.Td>
                  <Table.Td>
                    <Controller
                      name={`ingredients.${idx}.unit`}
                      control={control}
                      render={({ field }) => (
                        <Select
                          data={unitOptionsFor(field.value)}
                          style={{ width: 120 }}
                          error={field.value && !unitValues.has(field.value) ? "pick a unit" : undefined}
                          {...field}
                          onChange={(val) => field.onChange(val ?? "each")}
                        />
                      )}
                    />
                  </Table.Td>
                  <Table.Td>
                    <Controller
                      name={`ingredients.${idx}.optional`}
                      control={control}
                      render={({ field }) => (
                        <Checkbox
                          checked={field.value ?? false}
                          onChange={(e) => field.onChange(e.currentTarget.checked)}
                          aria-label="Optional / to taste"
                        />
                      )}
                    />
                  </Table.Td>
                  <Table.Td>
                    <Button type="button" size="xs" variant="subtle" color="red" onClick={() => remove(idx)}>
                      Remove
                    </Button>
                  </Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
          </Table.ScrollContainer>
        ) : (
          <Text c="dimmed">No ingredients added yet. Click "Add Ingredient" to start.</Text>
        )}
      </Card>
    </Stack>
  );
}
