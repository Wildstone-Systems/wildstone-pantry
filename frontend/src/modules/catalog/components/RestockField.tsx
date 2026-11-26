import { Group, NumberInput, Select } from "@mantine/core";
import { Controller, type Control, type FieldErrors } from "react-hook-form";

import type { ItemFormValues } from "../forms/item-schema";

/** "Restock at" amount plus what it's counted in: whole packages ("keep 2
 * jugs") or the item's stock unit (each / g / ml). Shared by Add and Edit. */
export function RestockField({
  control,
  errors,
}: {
  control: Control<ItemFormValues>;
  errors: FieldErrors<ItemFormValues>;
}) {
  return (
    <Group align="flex-start" gap="xs" grow wrap="nowrap">
      <Controller
        name="restock_threshold"
        control={control}
        render={({ field }) => (
          <NumberInput
            label="Restock at (min stock)"
            description="Below this, the item is added to the shopping list. Leave blank to skip."
            min={0}
            value={field.value ?? ""}
            onChange={(v) => field.onChange(v === "" ? null : Number(v))}
            error={errors.restock_threshold?.message}
          />
        )}
      />
      <Controller
        name="restock_unit"
        control={control}
        render={({ field }) => (
          <Select
            label="Counted in"
            description="Packages, or stock units"
            aria-label="Restock counted in"
            data={[
              { value: "package", label: "Packages" },
              { value: "base", label: "Stock units (each / g / ml)" },
            ]}
            value={field.value ?? "base"}
            onChange={(v) => field.onChange(v ?? "base")}
            allowDeselect={false}
          />
        )}
      />
    </Group>
  );
}
