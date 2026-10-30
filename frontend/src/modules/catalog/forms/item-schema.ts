import { z } from "zod";

/** An optional, non-negative number field that normalises "", null, and NaN to null. */
const optionalNonNegative = z
  .union([z.number().min(0), z.nan(), z.null()])
  .optional()
  .transform((v) => (v === undefined || v === null || Number.isNaN(v) ? null : v));

/** Optional *positive* number: "", null, NaN — and 0 — all normalise to null. Zero
 *  is meaningless for a divisor/serving size and the backend rejects it (gt=0), so
 *  treating 0 as "unset" here keeps the form from 422-ing on an empty-ish value. */
const optionalPositive = z
  .union([z.number(), z.nan(), z.null()])
  .optional()
  .transform((v) => (v === undefined || v === null || Number.isNaN(v) || v <= 0 ? null : v));

/** Validation schema for the Add Item / Edit Item forms. */
export const itemFormSchema = z.object({
  name: z.string().trim().min(1, "Item name is required").max(200, "Name is too long"),
  brand: z.string().trim().max(200, "Brand is too long").optional(),
  barcode: z.string().trim().max(64, "Barcode is too long").optional(),
  // Min-stock level (step 3): below this, the item auto-appears on the shopping
  // list. Optional — accepts null (after form reset), undefined, or a number.
  restock_threshold: optionalNonNegative,
  // Package size (populated from OpenFoodFacts on scan, editable here): the
  // amount the product is sold in — e.g. 1 gal of milk. Feeds unit-conversion.
  size_quantity: optionalNonNegative,
  size_unit: z.string().trim().max(24).nullable().optional(),
  size_description: z.string().trim().max(120).nullable().optional(),
  // How on-hand is tracked (ADR 0014): 'counted' whole units vs 'measured' by amount.
  stock_mode: z.enum(["counted", "measured"]).optional(),
  // Catch weight (ADR 0025 §1): every package weighs something different under
  // one barcode. The size above becomes a nominal, and check-in asks for the
  // weight of the package in hand instead of assuming it.
  variable_weight: z.boolean().optional(),
  // Calories per 1 unit (step 4): used to compute per-person intake when plated.
  calories_per_unit: optionalNonNegative,
  // Structured per-100g nutrition (step 5): populated from OpenFoodFacts on scan,
  // editable here. Drives recipe/plate macro rollups.
  energy_kcal_100g: optionalNonNegative,
  protein_g_100g: optionalNonNegative,
  fat_g_100g: optionalNonNegative,
  carbs_g_100g: optionalNonNegative,
  // These three are divisors/serving sizes the backend requires to be > 0.
  serving_size_g: optionalPositive,
  // Unit-conversion hints (step 5): grams per ml (volume) / per each (count).
  density_g_per_ml: optionalPositive,
  grams_per_each: optionalPositive,
});

export type ItemFormValues = z.infer<typeof itemFormSchema>;
