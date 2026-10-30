import { Alert, Button, Card, Group, NumberInput, Select, Stack, Switch, Text, TextInput, Title } from "@mantine/core";
import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Sparkles } from "lucide-react";
import { useState } from "react";
import { Controller, useForm } from "react-hook-form";
import { Link, useSearchParams } from "react-router-dom";

import { api } from "@/api/client";
import { getApiErrorMessage } from "@/api/errors";
import { ROUTES } from "@/routes/paths";
import { useAppState } from "@/state/AppStateProvider";

import { NutritionFields } from "../components/NutritionFields";
import { UsdaSearchModal } from "../components/UsdaSearchModal";
import { itemFormSchema, type ItemFormValues } from "../forms/item-schema";
import { useItems, type Item, type NutritionCandidate } from "../hooks/catalog-hooks";

// Sentinel for "let the server infer counted/measured from the size unit".
const AUTO_STOCK_MODE = "__auto__";

const EMPTY_ITEM_FORM: ItemFormValues = {
  name: "",
  brand: "",
  barcode: "",
  stock_mode: undefined,
  variable_weight: false,
  size_quantity: null,
  size_unit: null,
  size_description: null,
  restock_threshold: null,
  calories_per_unit: null,
  energy_kcal_100g: null,
  protein_g_100g: null,
  fat_g_100g: null,
  carbs_g_100g: null,
  serving_size_g: null,
  density_g_per_ml: null,
  grams_per_each: null,
};

export function AddItemPage() {
  const queryClient = useQueryClient();
  const { setStatus } = useAppState();
  // Carried from a failed barcode scan (ScanPage's "Add Item Manually"), so the
  // barcode just scanned doesn't have to be retyped by hand.
  const [searchParams] = useSearchParams();
  const prefillBarcode = searchParams.get("barcode") ?? "";

  const itemsQuery = useItems();
  const items = itemsQuery.data ?? [];

  const [error, setError] = useState<string | null>(null);
  const [usdaPickerOpen, setUsdaPickerOpen] = useState(false);
  // The USDA food picked for this not-yet-created item. Applied locally as a
  // live preview, then re-applied through the real match endpoint right after
  // creation so nutrition_source/estimated end up set exactly as they would be
  // from the Edit screen's "Match to a USDA food…".
  const [usdaMatch, setUsdaMatch] = useState<{ fdcId: number; query: string; description: string | null } | null>(
    null,
  );

  const {
    control,
    handleSubmit,
    reset,
    setValue,
    watch,
    formState: { errors, isValid },
  } = useForm<ItemFormValues>({
    resolver: zodResolver(itemFormSchema),
    mode: "onChange",
    defaultValues: { ...EMPTY_ITEM_FORM, barcode: prefillBarcode },
  });

  const itemName = watch("name");

  const onUsdaPick = (candidate: NutritionCandidate, query: string) => {
    if (candidate.fdc_id == null) return;
    setUsdaMatch({ fdcId: candidate.fdc_id, query, description: candidate.description });
    setValue("energy_kcal_100g", candidate.calories ?? null);
    setValue("protein_g_100g", candidate.protein_g ?? null);
    setValue("fat_g_100g", candidate.fat_g ?? null);
    setValue("carbs_g_100g", candidate.carbs_g ?? null);
    setUsdaPickerOpen(false);
  };

  const mutation = useMutation({
    mutationFn: async (values: ItemFormValues) => {
      const trimmedBarcode = values.barcode?.trim() ?? "";
      if (trimmedBarcode) {
        const existing = items.find((item) => (item.barcode ?? "").trim() === trimmedBarcode);
        if (existing) {
          throw new Error(
            `Barcode already exists: ${trimmedBarcode}. Use a different UPC/EAN or leave the barcode blank.`,
          );
        }
      }

      const res = await api.post<Item>("/api/items", {
        name: values.name,
        brand: values.brand?.trim() ? values.brand.trim() : null,
        barcode: trimmedBarcode ? trimmedBarcode : null,
        // Omit when "auto" so the server infers counted/measured from the size unit.
        ...(values.stock_mode ? { stock_mode: values.stock_mode } : {}),
        variable_weight: values.variable_weight ?? false,
        size_quantity: values.size_quantity ?? null,
        size_unit: values.size_unit?.trim() ? values.size_unit.trim() : null,
        size_description: values.size_description?.trim() ? values.size_description.trim() : null,
        restock_threshold: values.restock_threshold ?? null,
        calories_per_unit: values.calories_per_unit ?? null,
        energy_kcal_100g: values.energy_kcal_100g ?? null,
        protein_g_100g: values.protein_g_100g ?? null,
        fat_g_100g: values.fat_g_100g ?? null,
        carbs_g_100g: values.carbs_g_100g ?? null,
        serving_size_g: values.serving_size_g ?? null,
        density_g_per_ml: values.density_g_per_ml ?? null,
        grams_per_each: values.grams_per_each ?? null,
      });
      let created = res.data;
      let usdaApplied = false;
      if (usdaMatch) {
        try {
          const applied = await api.post<Item>(`/api/items/${created.id}/nutrition-from-usda`, {
            fdc_id: usdaMatch.fdcId,
            ...(usdaMatch.query.trim() ? { q: usdaMatch.query.trim() } : {}),
          });
          created = applied.data;
          usdaApplied = true;
        } catch {
          // The item is already created — don't lose it over a nutrition
          // re-tagging failure. The previewed macros already saved with it.
        }
      }
      return { created, hadMatch: Boolean(usdaMatch), usdaApplied };
    },
    onSuccess: async ({ created, hadMatch, usdaApplied }) => {
      setError(null);
      reset(EMPTY_ITEM_FORM);
      setUsdaMatch(null);
      setStatus(
        hadMatch && !usdaApplied
          ? `Created item ${created.name} — USDA match didn't apply; try "Match to a USDA food" from Edit`
          : `Created item ${created.name}`,
      );
      await queryClient.invalidateQueries({ queryKey: ["items"] });
    },
    onError: (err) => {
      const message = err instanceof Error ? err.message : getApiErrorMessage(err, "Create item failed");
      setError(message);
      setStatus(message);
    },
  });

  const onSubmit = handleSubmit((values) => {
    setError(null);
    mutation.mutate(values);
  });

  return (
    <Stack>
      <Group justify="space-between" align="baseline">
        <Title order={2}>Add Item</Title>
        <Button component={Link} to={ROUTES.catalog} variant="default">
          Back to Catalog
        </Button>
      </Group>

      <Card withBorder>
        <Stack component="form" onSubmit={onSubmit} gap="sm">
          {error ? (
            <Alert color="red" title="Could not create item">
              {error}
            </Alert>
          ) : null}

          <Controller
            name="name"
            control={control}
            render={({ field }) => <TextInput label="Item name" required error={errors.name?.message} {...field} />}
          />
          <Controller
            name="brand"
            control={control}
            render={({ field }) => (
              <TextInput label="Brand" error={errors.brand?.message} {...field} value={field.value ?? ""} />
            )}
          />
          <Controller
            name="barcode"
            control={control}
            render={({ field }) => (
              <TextInput
                label="Barcode (UPC/EAN)"
                description="Barcodes are unique. Leave blank if unknown."
                error={errors.barcode?.message}
                {...field}
                value={field.value ?? ""}
              />
            )}
          />

          <Controller
            name="stock_mode"
            control={control}
            render={({ field }) => (
              <Select
                label="Tracking"
                description="How on-hand is counted. Auto picks measured for weight/volume sizes (milk, oil), else counted."
                data={[
                  { value: AUTO_STOCK_MODE, label: "Auto (infer from size)" },
                  { value: "counted", label: "Counted (whole units — cans, eggs)" },
                  { value: "measured", label: "Measured (by amount — milk, oil)" },
                ]}
                value={field.value ?? AUTO_STOCK_MODE}
                onChange={(v) => field.onChange(v && v !== AUTO_STOCK_MODE ? v : undefined)}
                allowDeselect={false}
              />
            )}
          />

          <Controller
            name="variable_weight"
            control={control}
            render={({ field }) => (
              <Switch
                label="Weight varies by package"
                description="For meat, cheese and produce sold by weight: every package scans the same barcode but weighs something different. The size above becomes a rough guide, and check-in asks what this one weighs."
                checked={field.value ?? false}
                onChange={(event) => field.onChange(event.currentTarget.checked)}
              />
            )}
          />

          <Controller
            name="restock_threshold"
            control={control}
            render={({ field }) => (
              <NumberInput
                label="Restock at (min stock)"
                description="When on-hand drops below this, the item is added to the shopping list. Leave blank to skip."
                min={0}
                value={field.value ?? ""}
                onChange={(v) => field.onChange(v === "" ? null : Number(v))}
                error={errors.restock_threshold?.message}
              />
            )}
          />

          <Group gap="xs" wrap="wrap" mt="xs">
            <Button
              size="compact-sm"
              variant="light"
              color="grape"
              leftSection={<Sparkles size={14} />}
              onClick={() => setUsdaPickerOpen(true)}
            >
              Match to a USDA food…
            </Button>
            <Text size="xs" c="dimmed">
              No barcode? Search USDA by name (e.g. "banana raw", "pork butt roast raw") to fill in nutrition.
            </Text>
          </Group>
          {usdaMatch ? (
            <Text size="sm">
              <Text span c="dimmed">
                USDA match:{" "}
              </Text>
              <b>{usdaMatch.description ?? "selected food"}</b>
            </Text>
          ) : null}

          <NutritionFields control={control} />

          <Group>
            <Button type="submit" loading={mutation.isPending} disabled={!isValid}>
              Create Item
            </Button>
            <Text size="sm" c="dimmed">
              Tip: create item first, then add it to inventory.
            </Text>
          </Group>
        </Stack>
      </Card>

      <UsdaSearchModal
        seedQuery={itemName}
        opened={usdaPickerOpen}
        onClose={() => setUsdaPickerOpen(false)}
        onPick={onUsdaPick}
      />
    </Stack>
  );
}
