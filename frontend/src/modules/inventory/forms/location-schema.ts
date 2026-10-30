import { z } from "zod";

/** Validation schema for the Add Location form (RHF + Zod exemplar). */
export const locationFormSchema = z.object({
  name: z.string().trim().min(1, "Location name is required").max(200, "Name is too long"),
  description: z.string().trim().max(1000, "Description is too long").optional(),
});

export type LocationFormValues = z.infer<typeof locationFormSchema>;
