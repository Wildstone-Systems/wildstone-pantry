import { z } from "zod";

export const FEEDBACK_TYPES = ["bug", "feature"] as const;
export const FEEDBACK_AREAS = ["ui", "workflow", "data", "ocr", "performance", "licensing", "other"] as const;

/** Validation schema for the in-app feedback form (modal). */
export const feedbackFormSchema = z.object({
  type: z.enum(FEEDBACK_TYPES),
  area: z.enum(FEEDBACK_AREAS),
  summary: z.string().trim().min(1, "Feedback summary is required"),
  steps: z.string().optional(),
  expected: z.string().optional(),
  actual: z.string().optional(),
  notes: z.string().optional(),
});

export type FeedbackFormValues = z.infer<typeof feedbackFormSchema>;
