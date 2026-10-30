import {
  Alert,
  Button,
  Checkbox,
  Divider,
  Group,
  Modal,
  NumberInput,
  Select,
  Stack,
  Text,
  TextInput,
} from "@mantine/core";
import { zodResolver } from "@hookform/resolvers/zod";
import { Search, Sparkles } from "lucide-react";
import { useEffect, useState } from "react";
import { Controller, useForm } from "react-hook-form";

import { nutritionReferenceSourceLabel, nutritionReferenceStatusLabel } from "../nutrition-reference-labels";

import { getApiErrorMessage } from "@/api/errors";
import { useAppState } from "@/state/AppStateProvider";

import { itemFormSchema, type ItemFormValues } from "../forms/item-schema";
import { useRescanItem, useUpdateItem, type Item, type ReferenceReview } from "../hooks/catalog-hooks";
import { NutritionFields } from "./NutritionFields";
import { UsdaMatchModal } from "./UsdaMatchModal";

type Props = {
  item: Item | null;
  onClose: () => void;
  onSaved?: (item: Item) => void;
};

/**
 * Modal form to edit an existing catalog item: identity (name/brand/barcode),
 * stock threshold, and its full nutrition — per-100g macros (auto-filled from
 * OpenFoodFacts on scan) plus the unit-conversion hints recipes use.
 */
export function EditItemModal({ item, onClose, onSaved }: Props) {
  const updateItem = useUpdateItem();
  const rescan = useRescanItem();
  const { setStatus } = useAppState();
  const [error, setError] = useState<string | null>(null);
  // Local mirror so a rescan / USDA pick updates the "estimated" note live.
  const [current, setCurrent] = useState<Item | null>(item);
  const [referenceReview, setReferenceReview] = useState<ReferenceReview | null>(null);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [identityKey, setIdentityKey] = useState("");
  const [identityConfirmed, setIdentityConfirmed] = useState(false);
  const identityChanged = identityKey.trim() !== (item?.canonical_ingredient ?? "");

  const {
    control,
    handleSubmit,
    reset,
    setValue,
    formState: { errors, isValid },
  } = useForm<ItemFormValues>({
    resolver: zodResolver(itemFormSchema),
    mode: "onChange",
    defaultValues: { name: "", brand: "", barcode: "", restock_threshold: null, calories_per_unit: null },
  });

  useEffect(() => setCurrent(item), [item]);

  // Push server-filled nutrition (from a rescan or a USDA pick) into the open form
  // WITHOUT a full reset, so any other unsaved edits are preserved.
  const applyNutritionToForm = (it: Item) => {
    setValue("size_quantity", it.size_quantity ?? null);
    setValue("size_unit", it.size_unit ?? null);
    setValue("size_description", it.size_description ?? null);
    setValue("energy_kcal_100g", it.energy_kcal_100g ?? null);
    setValue("protein_g_100g", it.protein_g_100g ?? null);
    setValue("fat_g_100g", it.fat_g_100g ?? null);
    setValue("carbs_g_100g", it.carbs_g_100g ?? null);
    setValue("serving_size_g", it.serving_size_g ?? null);
    setValue("density_g_per_ml", it.density_g_per_ml ?? null);
    setValue("grams_per_each", it.grams_per_each ?? null);
    setCurrent(it);
  };

  const doRescan = async () => {
    if (!item) return;
    setError(null);
    try {
      const r = await rescan.mutateAsync(item.id);
      if (r.item) applyNutritionToForm(r.item);
      setReferenceReview(r.reference_review ?? null);
      if (r.status === "updated") {
        setStatus(`Filled ${r.filled.join(", ")}${r.estimated ? ` (estimated: ${r.estimated_source ?? "USDA"})` : ""}`);
      } else if (r.status === "no_barcode") {
        setStatus("No barcode and no USDA name match — try 'Match to a USDA food' and pick one.");
      } else {
        setStatus("Nothing new to fill.");
      }
    } catch (err) {
      setError(getApiErrorMessage(err, "Rescan failed"));
    }
  };

  // Re-seed the form whenever a different item is opened for editing.
  useEffect(() => {
    if (item) {
      setIdentityKey(item.canonical_ingredient ?? "");
      setIdentityConfirmed(false);
      setError(null);
      setReferenceReview(null);
      reset({
        name: item.name,
        brand: item.brand ?? "",
        barcode: item.barcode ?? "",
        size_quantity: item.size_quantity ?? null,
        size_unit: item.size_unit ?? null,
        size_description: item.size_description ?? null,
        stock_mode: item.stock_mode ?? "counted",
        restock_threshold: item.restock_threshold ?? null,
        calories_per_unit: item.calories_per_unit ?? null,
        energy_kcal_100g: item.energy_kcal_100g ?? null,
        protein_g_100g: item.protein_g_100g ?? null,
        fat_g_100g: item.fat_g_100g ?? null,
        carbs_g_100g: item.carbs_g_100g ?? null,
        serving_size_g: item.serving_size_g ?? null,
        density_g_per_ml: item.density_g_per_ml ?? null,
        grams_per_each: item.grams_per_each ?? null,
      });
    }
  }, [item, reset]);

  const onSubmit = handleSubmit(async (values) => {
    if (!item) return;
    if (identityChanged && (!identityConfirmed || !identityKey.trim())) return;
    setError(null);
    try {
      const saved = await updateItem.mutateAsync({
        id: item.id,
        ...(identityChanged
          ? {
              canonical_ingredient: identityKey.trim(),
              confirm_identity_change: true,
              expected_canonical_ingredient: item.canonical_ingredient ?? null,
              expected_canonical_revision: item.canonical_revision,
            }
          : {}),
        name: values.name,
        brand: values.brand?.trim() ? values.brand.trim() : null,
        barcode: values.barcode?.trim() ? values.barcode.trim() : null,
        size_quantity: values.size_quantity ?? null,
        size_unit: values.size_unit?.trim() ? values.size_unit.trim() : null,
        size_description: values.size_description?.trim() ? values.size_description.trim() : null,
        stock_mode: values.stock_mode ?? "counted",
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
      onSaved?.(saved);
      onClose();
    } catch (err) {
      setError(getApiErrorMessage(err, "Could not save item"));
    }
  });

  return (
    <Modal opened={item !== null} onClose={onClose} title={`Edit item — ${item?.name ?? ""}`} size="lg">
      <Stack component="form" onSubmit={onSubmit} gap="sm">
        {error ? (
          <Alert color="red" title="Could not save item">
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
        <Divider label="Ingredient identity" />
        <Text size="sm">
          Source: {item?.canonical_provenance ?? "legacy_unknown"}; version {item?.canonical_version ?? "unknown"}.
        </Text>
        <TextInput
          label="Ingredient identity key"
          description="Include meaningful differences such as gluten-free, salted, or raw. Renaming a product preserves confirmed and historical identities."
          value={identityKey}
          onChange={(event) => {
            setIdentityKey(event.currentTarget.value);
            setIdentityConfirmed(false);
          }}
        />
        {identityChanged && (
          <Alert color="yellow" title="Global recipe matching change">
            <Text size="sm">
              Changing {item?.canonical_ingredient ?? "unassigned"} to {identityKey.trim() || "unassigned"} affects
              every recipe using this product in your household.
            </Text>
            <Checkbox
              mt="xs"
              label="I confirm this product's ingredient identity for all recipes"
              checked={identityConfirmed}
              onChange={(event) => setIdentityConfirmed(event.currentTarget.checked)}
            />
          </Alert>
        )}
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
              description="Counted = whole units (cans, eggs). Measured = by amount (milk, oil)."
              data={[
                { value: "counted", label: "Counted (whole units)" },
                { value: "measured", label: "Measured (by amount)" },
              ]}
              value={field.value ?? "counted"}
              onChange={(v) => field.onChange(v ?? "counted")}
              allowDeselect={false}
            />
          )}
        />

        {current ? (
          <Text size="xs" c="dimmed">
            Units: <b>buy</b> by the package
            {current.size_quantity && current.size_unit
              ? ` (${Number(current.size_quantity)} ${current.size_unit})`
              : ""}
            ; <b>tracked &amp; used</b> in {current.base_unit ?? "each"}
            {current.stock_mode === "measured" &&
            (current.base_unit === "g" || current.base_unit === "ml") &&
            current.density_g_per_ml == null
              ? " — set a density below to use it by tsp/cup"
              : ""}
            .
          </Text>
        ) : null}

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

        <Divider label="Nutrition" labelPosition="left" mt="xs" />
        <Group gap="xs" wrap="wrap">
          <Button
            size="compact-sm"
            variant="light"
            leftSection={<Search size={14} />}
            loading={rescan.isPending}
            onClick={doRescan}
          >
            {(current?.barcode ?? "").trim() ? "Rescan barcode" : "Auto-fill from USDA"}
          </Button>
          <Button
            size="compact-sm"
            variant="light"
            color="grape"
            leftSection={<Sparkles size={14} />}
            onClick={() => setPickerOpen(true)}
          >
            Match to a USDA food…
          </Button>
          <Text size="xs" c="dimmed">
            Nutrition didn&apos;t populate? Rescan, or pick the right USDA food.
          </Text>
        </Group>

        {current ? (
          <Alert
            color={
              ["verified", "human_selected", "label_exact", "manual"].includes(
                current.nutrition_reference?.status ?? ""
              )
                ? "blue"
                : "yellow"
            }
            title="Nutrition reference"
          >
            <Text size="sm">
              {nutritionReferenceStatusLabel(current.nutrition_reference?.status)}; source:{" "}
              {nutritionReferenceSourceLabel(current.nutrition_reference?.source)}.
            </Text>
            {current.nutrition_reference?.description && (
              <Text size="sm">{current.nutrition_reference.description}</Text>
            )}
            {current.nutrition_reference?.fdc_id != null && (
              <Text size="sm">FDC ID: {current.nutrition_reference.fdc_id}</Text>
            )}
            <Text size="sm">
              {current.nutrition_reference?.why ||
                "Historical nutrition has no recorded reference approval. Review it before relying on the match."}
            </Text>
            {["unverified", "legacy_unknown"].includes(current.nutrition_reference?.status ?? "legacy_unknown") && (
              <Text size="sm">Reference match remains unresolved. You can select the correct USDA food below.</Text>
            )}
          </Alert>
        ) : null}
        {referenceReview &&
        !["verified", "human_selected", "label_exact", "manual"].includes(referenceReview.status) ? (
          <Alert color="yellow" title="Reference needs review">
            <Text size="sm">
              {nutritionReferenceStatusLabel(referenceReview.status)}; source:{" "}
              {nutritionReferenceSourceLabel(referenceReview.source)}.
            </Text>
            {referenceReview.description && <Text size="sm">{referenceReview.description}</Text>}
            <Text size="sm">{referenceReview.why}</Text>
            <Text size="sm">
              No automatic nutrition was applied from this reference. Select the correct USDA food to authorize it.
            </Text>
          </Alert>
        ) : null}

        {current?.nutrition_source ? (
          <Text size="sm">
            <Text span c="dimmed">
              Nutrition source:{" "}
            </Text>
            <b>{current.nutrition_source}</b>
          </Text>
        ) : null}

        {current?.nutrition_estimated ? (
          <Alert color="yellow" variant="light" title="Estimated nutrition">
            These macros are generic USDA values matched by name — approximate, not from this exact product. If
            it&apos;s the wrong food, use “Match to a USDA food…” above to pick the right one.
          </Alert>
        ) : null}

        <NutritionFields control={control} />

        <Group justify="flex-end" mt="sm">
          <Button variant="default" onClick={onClose}>
            Cancel
          </Button>
          <Button
            type="submit"
            loading={updateItem.isPending}
            disabled={!isValid || (identityChanged && (!identityConfirmed || !identityKey.trim()))}
          >
            Save changes
          </Button>
        </Group>
      </Stack>

      <UsdaMatchModal
        itemId={item?.id ?? null}
        itemName={current?.name ?? item?.name ?? ""}
        opened={pickerOpen}
        onClose={() => setPickerOpen(false)}
        onApplied={(updated) => {
          applyNutritionToForm(updated);
          setReferenceReview(null);
        }}
      />
    </Modal>
  );
}
