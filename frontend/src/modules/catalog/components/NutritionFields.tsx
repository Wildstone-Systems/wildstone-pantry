import { Divider, Group, NumberInput, Select, Text, TextInput } from "@mantine/core";
import { Controller, type Control } from "react-hook-form";

import type { ItemFormValues } from "../forms/item-schema";

/** A NumberInput bound to an optional numeric form field (null when blank). */
function NumField({
  control,
  name,
  label,
  description,
  suffix,
}: {
  control: Control<ItemFormValues>;
  name: keyof ItemFormValues;
  label: string;
  description?: string;
  suffix?: string;
}) {
  return (
    <Controller
      name={name}
      control={control}
      render={({ field }) => (
        <NumberInput
          label={label}
          description={description}
          suffix={suffix}
          min={0}
          // Coerce rather than gate on `typeof === "number"`: the API serialises
          // these DB Numeric columns as JSON strings ("2.11"), and a strict
          // number check silently blanked every scanned value.
          value={field.value === null || field.value === undefined || field.value === "" ? "" : Number(field.value)}
          onChange={(v) => field.onChange(v === "" ? null : Number(v))}
        />
      )}
    />
  );
}

/**
 * Shared nutrition + unit-conversion inputs used by both the Add Item and Edit
 * Item forms: per-100g macros (auto-filled from OpenFoodFacts on scan) plus the
 * conversion hints recipes use to turn amounts into grams. Kept in one place so
 * the two forms never drift apart.
 */
const SIZE_UNITS = ["ea", "g", "kg", "oz", "lb", "ml", "l", "cup", "pint", "qt", "gal"];

export function NutritionFields({ control }: { control: Control<ItemFormValues> }) {
  return (
    <>
      <Divider label="Package size" labelPosition="left" mt="xs" />
      <Text size="xs" c="dimmed">
        How the product is sold — e.g. 1 gal of milk. Auto-filled from OpenFoodFacts on scan; edit any value.
        Used to convert a recipe or removal amount (like "1 cup") against a whole package.
      </Text>
      <Group grow align="flex-start">
        <NumField control={control} name="size_quantity" label="Size" />
        <Controller
          name="size_unit"
          control={control}
          render={({ field }) => (
            <Select
              label="Unit"
              data={SIZE_UNITS}
              searchable
              clearable
              {...field}
              value={field.value ?? null}
              onChange={(v) => field.onChange(v ?? null)}
            />
          )}
        />
      </Group>
      <Controller
        name="size_description"
        control={control}
        render={({ field }) => (
          <TextInput
            label="Size label"
            description="Free text as printed on the pack (e.g. '1 gallon (3.78 L)')."
            {...field}
            value={field.value ?? ""}
            onChange={(e) => field.onChange(e.currentTarget.value || null)}
          />
        )}
      />

      <Divider label="Nutrition (per 100 g)" labelPosition="left" mt="xs" />
      <Text size="xs" c="dimmed">
        Auto-filled from OpenFoodFacts when scanned; edit any value. These drive per-serving recipe macros
        and per-person calorie/macro tracking.
      </Text>
      <Group grow>
        <NumField control={control} name="energy_kcal_100g" label="Calories" suffix=" kcal" />
        <NumField control={control} name="protein_g_100g" label="Protein" suffix=" g" />
      </Group>
      <Group grow>
        <NumField control={control} name="fat_g_100g" label="Fat" suffix=" g" />
        <NumField control={control} name="carbs_g_100g" label="Carbohydrates" suffix=" g" />
      </Group>

      <Divider label="Amounts & conversions (optional)" labelPosition="left" mt="xs" />
      <Text size="xs" c="dimmed">
        Help the app convert recipe amounts to grams. Set density for liquids/volumes and weight-per-item for
        things counted individually (e.g. one egg ≈ 50 g).
      </Text>
      <Group grow>
        <NumField
          control={control}
          name="calories_per_unit"
          label="Calories per unit"
          suffix=" kcal"
          description="Legacy per-unit figure."
        />
        <NumField control={control} name="serving_size_g" label="Serving size" suffix=" g" />
      </Group>
      <Group grow>
        <NumField control={control} name="density_g_per_ml" label="Density" suffix=" g/ml" description="For cups/ml/tbsp." />
        <NumField control={control} name="grams_per_each" label="Weight per each" suffix=" g" description="For counted items." />
      </Group>
    </>
  );
}
