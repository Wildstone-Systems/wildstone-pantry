import { z } from "zod";

export const profileSchema = z.object({
  display_name: z.string().trim().min(1, "Name is required").max(120),
  // Optional: accepts undefined (untouched), null (after form reset), or a number.
  daily_calorie_target: z
    .union([z.number().min(0), z.nan(), z.null()])
    .optional()
    .transform((v) => (v === undefined || v === null || Number.isNaN(v) ? null : v)),
});

export type ProfileValues = z.infer<typeof profileSchema>;
