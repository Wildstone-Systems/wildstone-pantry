import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/api/client";

// The cook-along lives on the server (ADR 0026 §6.1): the tablet and an agent
// read and move the same session, so "next" said to the agent moves the
// tablet, and a tap here is what the agent reads back.

export type CookStep = { number: number; text: string; done: boolean };

export type CookTimer = {
  id: string;
  label: string;
  session_id: string | null;
  step_number: number | null;
  duration_seconds: number;
  started_at: string;
  ends_at: string;
  remaining_seconds: number;
  state: "running" | "ringing";
};

export type CookIngredient = {
  ingredient_id: string;
  item: string;
  quantity: string;
  unit: string;
  optional: boolean;
  // Set on a substituted line: the recipe ingredient it stands in for.
  replaces?: string | null;
};

export type CookSubstitution = {
  original_item_id: string;
  substitute_item_id: string;
  ratio?: number | null;
  unit?: string | null;
};

export type CookPlan = {
  ingredient_overrides: { ingredient_id: string; quantity: number | null; omit: boolean; unit?: string | null }[];
  scale_to_ingredient_id: string | null;
  allow_short_ingredients: boolean;
  // Chosen by an agent (this screen does not edit them); kept so a plan
  // saved from here never drops them.
  substitutions?: CookSubstitution[];
  substitution_approval_ids?: string[];
};

export type CookSession = {
  id: string;
  recipe_id: string;
  recipe_name: string;
  status: "active" | "finishing" | "finished" | "abandoned";
  base_servings: number | null;
  servings_made: string | null;
  step_number: number;
  total_steps: number;
  step_text: string;
  is_first_step: boolean;
  is_last_step: boolean;
  step_done: boolean;
  step_timers: { seconds: number; label: string }[];
  steps: CookStep[];
  completed_steps: number[];
  steps_left: number;
  timers: CookTimer[];
  plan: Partial<CookPlan> | null;
  step_ingredients: CookIngredient[];
  ingredients: CookIngredient[];
  cook_event_id: string | null;
  substitution_review_warning?: string | null;
  scoped_substitutions?: Record<string, unknown>[];
};

export type CookFinished = { session: CookSession; cook: Record<string, unknown> & { capped_ingredients?: { item_name: string }[]; scale_used?: number } };

const BASE = "/api/cook-sessions";
export const activeCookKey = ["cook-session", "active"] as const;
const sessionKey = (id: string) => ["cook-session", id] as const;

/** Whatever is being cooked right now, or null. Polled, so a session an agent
 * starts elsewhere is offered here. */
export function useActiveCookSession(pollMs = 0) {
  return useQuery({
    queryKey: activeCookKey,
    queryFn: async () => {
      const resp = await api.get<CookSession | "">(`${BASE}/active`);
      // 204 = nothing is being cooked.
      return resp.status === 204 || !resp.data ? null : (resp.data as CookSession);
    },
    refetchInterval: pollMs > 0 ? pollMs : false,
  });
}

/** One session, polled while cooking: a step, tick or timer changed by voice
 * shows here within `pollMs`, and so does the agent finishing or stopping it. */
export function useCookSession(id: string | null, pollMs = 0) {
  return useQuery({
    queryKey: sessionKey(id ?? "none"),
    queryFn: async () => (await api.get<CookSession>(`${BASE}/${id}`)).data,
    enabled: Boolean(id),
    refetchInterval: id && pollMs > 0 ? pollMs : false,
  });
}

function useSessionWrite<P>(fn: (p: P) => Promise<CookSession>) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: fn,
    onSuccess: (s) => {
      qc.setQueryData(sessionKey(s.id), s);
      qc.setQueryData(activeCookKey, s.status === "active" ? s : null);
    },
  });
}

export function useStartCookSession() {
  return useSessionWrite(
    async (p: { recipeId: string; servingsMade: number | null; plan: CookPlan }) =>
      (await api.post<CookSession>(BASE, { recipe_id: p.recipeId, servings_made: p.servingsMade, plan: p.plan })).data,
  );
}

export function useUpdateCookSession() {
  return useSessionWrite(
    async (p: { id: string; servingsMade: number | null; plan: CookPlan }) =>
      (await api.patch<CookSession>(`${BASE}/${p.id}`, { servings_made: p.servingsMade, plan: p.plan })).data,
  );
}

export function useMoveCookStep() {
  return useSessionWrite(
    async (p: { id: string; action: "next" | "previous" | "goto" | "complete"; step?: number }) =>
      (await api.post<CookSession>(`${BASE}/${p.id}/step`, { action: p.action, step: p.step ?? null })).data,
  );
}

export function useSetCookStepDone() {
  return useSessionWrite(
    async (p: { id: string; step: number; done: boolean }) =>
      (await api.post<CookSession>(`${BASE}/${p.id}/steps/${p.step}`, { done: p.done })).data,
  );
}

export function useAbandonCookSession() {
  return useSessionWrite(async (id: string) => (await api.delete<CookSession>(`${BASE}/${id}`)).data);
}

export function useFinishCookSession() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (p: {
      id: string;
      outputWeightGrams: number | null;
      outputPortions: number | null;
      outputPortionSize: number | null;
      outputPortionUnit: string | null;
    }) => {
      // Blank means "not measured": such keys are left out, not sent as null.
      const body: Record<string, unknown> = { location_id: null };
      if (p.outputWeightGrams != null) body.output_weight_grams = p.outputWeightGrams;
      if (p.outputPortions != null) body.output_portions = p.outputPortions;
      if (p.outputPortionSize != null) body.output_portion_size = p.outputPortionSize;
      if (p.outputPortionUnit != null) body.output_portion_unit = p.outputPortionUnit;
      return (await api.post<CookFinished>(`${BASE}/${p.id}/finish`, body)).data;
    },
    onSuccess: (r) => {
      qc.setQueryData(sessionKey(r.session.id), r.session);
      qc.setQueryData(activeCookKey, null);
      qc.invalidateQueries({ queryKey: ["recipe"] });
    },
  });
}

// --- kitchen timers ---

/** `profileId` is the household profile picked on the tablet ("Who's using
 * this?"), so the timer's "done" can reach that person through Wildstone. */
export function useStartTimer(sessionId: string | null, profileId: string | null = null) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (p: { seconds: number; label?: string | null; stepNumber?: number | null }) =>
      (
        await api.post<CookTimer>("/api/cook-timers", {
          seconds: p.seconds,
          label: p.label ?? null,
          session_id: sessionId,
          step_number: p.stepNumber ?? null,
          profile_id: profileId,
        })
      ).data,
    onSuccess: () => sessionId && qc.invalidateQueries({ queryKey: sessionKey(sessionId) }),
  });
}

export function useStopTimer(sessionId: string | null) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (timerId: string) => (await api.delete(`/api/cook-timers/${timerId}`)).data,
    onSuccess: () => sessionId && qc.invalidateQueries({ queryKey: sessionKey(sessionId) }),
  });
}
