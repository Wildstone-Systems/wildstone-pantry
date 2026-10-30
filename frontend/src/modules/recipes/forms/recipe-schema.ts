import { z } from "zod";

export const recipeIngredientSchema = z.object({
  item_id: z.string(),
  quantity_required: z.number().min(0, "Must be >= 0"),
  unit: z.string().min(1),
  /** To-taste / garnish line — out of the can-make score, never auto-deducted. */
  optional: z.boolean().optional(),
  yield_basis: z.boolean().optional(),
  quantity_mode: z.enum(["amount", "package"]).optional(),
  /**
   * Name from an imported recipe that matched no catalog item yet. The item is
   * created on save, and only for rows the user chose to keep — so a mis-parsed
   * method step that gets deleted never pollutes the catalog.
   */
  pending_name: z.string().optional(),
  raw_line: z.string().nullable().optional(),
  source_line_id: z.string().nullable().optional(),
  resolution: z.string().nullable().optional(),
  suggestion: z.string().nullable().optional(),
});

/** Validation schema for the recipe create/edit form (with ingredient rows). */
export const recipeFormSchema = z.object({
  name: z.string().trim().min(1, "Recipe name is required").max(200, "Name is too long"),
  instructions: z.string().optional(),
  servings: z.number().min(1, "Must be at least 1").default(1),
  ingredients: z.array(recipeIngredientSchema),
  // Optional output/yield (step 4): cooking produces this item into inventory.
  output_item_id: z.string().nullable().optional(),
  output_quantity: z
    .union([z.number().min(0), z.nan(), z.null()])
    .optional()
    .transform((v) => (v === undefined || v === null || Number.isNaN(v) ? null : v)),
  output_unit: z.string().nullable().optional(),
});

export type RecipeFormValues = z.infer<typeof recipeFormSchema>;
