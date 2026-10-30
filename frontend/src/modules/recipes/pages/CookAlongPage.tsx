import {
  Alert,
  Anchor,
  Badge,
  Button,
  Card,
  Checkbox,
  Group,
  Loader,
  Progress,
  Stack,
  Text,
  Title,
} from "@mantine/core";
import { useQuery } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";

import { api } from "@/api/client";
import { getApiErrorMessage } from "@/api/errors";
import { QuantityStepper } from "@/components/QuantityStepper";
import { useAppState } from "@/state/AppStateProvider";

import { CookTimers } from "../components/CookTimers";
import { FinishSheet } from "../components/FinishSheet";
import { ScopedSubstitutionReview } from "../components/ScopedSubstitutionReview";
import {
  type CookPlan,
  type CookSubstitution,
  type CookSession,
  useAbandonCookSession,
  useActiveCookSession,
  useCookSession,
  useFinishCookSession,
  useMoveCookStep,
  useSetCookStepDone,
  useStartCookSession,
  useStartTimer,
  useStopTimer,
  useUpdateCookSession,
} from "../hooks/cook-session-hooks";
import type { RecipeIngredientAvailability, RecipeWithIngredients } from "../hooks/recipes-hooks";
import { useRecipeAvailability } from "../hooks/recipes-hooks";

type Phase = "setup" | "cook" | "finish";

/** Weight units the finish sheet offers, in grams. The server converts too, but
 * it takes the measured yield in grams, so the conversion happens here. */
const GRAMS_PER: Record<string, number> = { lb: 453.59237, oz: 28.349523125, g: 1, kg: 1000 };

// ``unit`` is kept from a plan an agent made (an amount in another unit);
// this screen's own amounts are in the recipe line's unit, so editing one
// here clears it.
type Override = { quantity: number | null; omit: boolean; unit?: string | null };

function n(v: string | number | null | undefined): number {
  const x = Number(v ?? 0);
  return Number.isFinite(x) ? x : 0;
}

/** Trim trailing zeros so 0.50 reads as 0.5 and 4.000 as 4. */
function amount(value: number): string {
  return String(Number(value.toFixed(3)));
}

/** A nudge sized to the amount: quarter-cups for fractions, whole units for a
 * handful, tens for gram weights. Stepping 170 g by 1 would take all day;
 * stepping 0.33 cup by 1 overshoots on the first tap. */
function stepFor(value: number): number {
  if (value < 1) return 0.25;
  if (value < 20) return 1;
  return 10;
}

/** How often the page re-reads the session while cooking, so a step, tick or
 * timer changed by voice shows here — and so does an agent finishing it. */
const POLL_MS = 3000;

function n2(v: string | null | undefined): number | null {
  if (v == null) return null;
  const x = Number(v);
  return Number.isFinite(x) ? x : null;
}

export function CookAlongPage() {
  const { recipeId } = useParams();
  const navigate = useNavigate();
  const { setStatus, activeProfileId } = useAppState();

  const recipeQuery = useQuery({
    queryKey: ["recipe", recipeId],
    queryFn: async () => (await api.get<RecipeWithIngredients>(`/api/recipes/${recipeId}`)).data,
    enabled: Boolean(recipeId),
  });

  const recipe = recipeQuery.data?.recipe;
  const baseServings = recipe?.servings ?? 1;

  const [phase, setPhase] = useState<Phase>("setup");
  const [servingsMade, setServingsMade] = useState<number | string>("");
  const [overrides, setOverrides] = useState<Record<string, Override>>({});
  // The cook-along session this page drives (ADR 0026 §6.1). Nothing is
  // deducted until Finish (ADR 0022 §4); the session holds the progress, the
  // ticks, the timers and the plan, so closing the tablet — or an agent
  // taking over by voice — loses nothing.
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [finishError, setFinishError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [endedElsewhere, setEndedElsewhere] = useState<string | null>(null);
  // What the cook OBSERVED, as opposed to what the recipe predicts (ADR 0025).
  // All blank by default: an unmeasured dish falls back to the recipe's yield,
  // and an unstated tub count means the yield lands as one lot. Pre-filling
  // either would put numbers in the ledger that nobody actually looked at.
  const [madeWeight, setMadeWeight] = useState<number | string>("");
  const [madeUnit, setMadeUnit] = useState<string>("lb");
  const [portions, setPortions] = useState<number | string>("");
  const [portionSize, setPortionSize] = useState<number | string>("");
  // "Use all the pork I have" (ADR 0025 §5): the pantry sizes the batch and
  // every other line follows. Null = the servings stepper decides, as before.
  const [scaleTo, setScaleTo] = useState<string | null>(null);
  // Permission, not an instruction: when a line runs short, take what's there
  // rather than failing the cook (ADR 0025 §7). Off by default — the
  // all-or-nothing guarantee is the safer thing to be wrong about.
  const [allowShort, setAllowShort] = useState(false);
  // Swaps an agent put in the plan. This screen does not edit them, but
  // every plan it saves carries them, so Finish still uses the substitutes.
  const [substitutions, setSubstitutions] = useState<CookSubstitution[]>([]);
  const [substitutionApprovalIds, setSubstitutionApprovalIds] = useState<string[]>([]);

  // Default the target to the recipe's own yield — ONCE, when it loads. Keying
  // this off an empty value would snap the field back to the default the moment
  // you cleared it to type a new number.
  const seededServings = useRef(false);
  useEffect(() => {
    if (recipe && !seededServings.current) {
      seededServings.current = true;
      setServingsMade(recipe.servings ?? 1);
    }
  }, [recipe]);

  // A session already under way — started here earlier, or by an agent — is
  // offered, never silently replaced.
  const active = useActiveCookSession(phase === "setup" ? POLL_MS : 0);
  const activeHere = active.data && active.data.recipe_id === recipeId ? active.data : null;
  const activeElsewhere = active.data && active.data.recipe_id !== recipeId ? active.data : null;
  const current = useCookSession(sessionId, phase === "setup" ? 0 : POLL_MS);
  const session: CookSession | null = current.data ?? null;
  const serverApprovalIds = JSON.stringify(session?.plan?.substitution_approval_ids ?? []);
  useEffect(() => {
    if (session) setSubstitutionApprovalIds(session.plan?.substitution_approval_ids ?? []);
  }, [session?.id, serverApprovalIds]);

  // Pick up a cook of THIS recipe that is already going: the tablet resumes
  // where the cook (or the agent) left off. Once per page load, so going back
  // to the setup screen on purpose does not bounce you into the steps again.
  const resumed = useRef(false);
  useEffect(() => {
    if (resumed.current || !activeHere) return;
    resumed.current = true;
    adopt(activeHere);
    setPhase("cook");
  }, [activeHere]);

  /** Take a session's servings and plan as this page's own. */
  const adopt = (s: CookSession) => {
    setSessionId(s.id);
    const made = n2(s.servings_made);
    if (made != null) {
      seededServings.current = true;
      setServingsMade(made);
    }
    const plan = s.plan ?? {};
    const restored: Record<string, Override> = {};
    for (const o of plan.ingredient_overrides ?? []) {
      restored[o.ingredient_id] = {
        quantity: o.quantity == null ? null : Number(o.quantity),
        omit: Boolean(o.omit),
        unit: o.unit ?? null,
      };
    }
    setOverrides(restored);
    setScaleTo(plan.scale_to_ingredient_id ?? null);
    setAllowShort(Boolean(plan.allow_short_ingredients));
    setSubstitutions(plan.substitutions ?? []);
    setSubstitutionApprovalIds(plan.substitution_approval_ids ?? []);
  };

  // The session ended somewhere else — an agent finished it, or someone
  // stopped it — while this page was on it.
  useEffect(() => {
    if (!session || phase === "setup") return;
    if (session.status === "finished" && phase !== "finish") {
      setEndedElsewhere(`${session.recipe_name} was finished and logged.`);
    } else if (session.status === "abandoned") {
      setEndedElsewhere("This cook was stopped. Nothing was taken from the pantry.");
    }
  }, [session, phase]);

  const target = Number(servingsMade) || baseServings;
  const availability = useRecipeAvailability(recipeId ?? null, null, target);
  const startSession = useStartCookSession();
  const updateSession = useUpdateCookSession();
  const moveStep = useMoveCookStep();
  const setStepDone = useSetCookStepDone();
  const abandon = useAbandonCookSession();
  const finish = useFinishCookSession();
  const startTimer = useStartTimer(sessionId, activeProfileId);
  const stopTimer = useStopTimer(sessionId);

  const hasInstructions = Boolean(recipe?.instructions?.trim());

  const lines: RecipeIngredientAvailability[] = availability.data ?? [];

  /** The amount actually going in: an override wins, else the scaled figure the
   * server already computed for this serving count. */
  const effectiveAmount = (line: RecipeIngredientAvailability): number => {
    const override = overrides[line.ingredient_id];
    if (override?.quantity != null) return override.quantity;
    return n(line.quantity_required);
  };

  const setOverride = (ingredientId: string, patch: Partial<Override>) => {
    setOverrides((prev) => {
      const current: Override = prev[ingredientId] ?? { quantity: null, omit: false };
      return { ...prev, [ingredientId]: { ...current, ...patch } };
    });
  };

  const short = lines.filter(
    (l) => !overrides[l.ingredient_id]?.omit && (l.status === "missing" || l.status === "cant_convert"),
  );

  /** The dish's weight in grams, or null when it wasn't weighed. */
  const madeGrams = (): number | null => {
    const value = Number(madeWeight);
    if (!madeWeight || !Number.isFinite(value) || value <= 0) return null;
    return value * (GRAMS_PER[madeUnit] ?? 1);
  };

  /** The plan as the server keeps it: finishing — here or by an agent — cooks
   * exactly this. */
  const plan = (): CookPlan => ({
    ingredient_overrides: Object.entries(overrides)
      .filter(([, o]) => o.omit || o.quantity != null)
      .map(([ingredient_id, o]) => ({
        ingredient_id,
        quantity: o.omit ? null : o.quantity,
        omit: o.omit,
        ...(o.unit && !o.omit ? { unit: o.unit } : {}),
      })),
    scale_to_ingredient_id: scaleTo,
    allow_short_ingredients: allowShort,
    substitutions,
    ...(substitutionApprovalIds.length ? { substitution_approval_ids: substitutionApprovalIds } : {}),
  });

  const act = async (fn: () => Promise<unknown>, fallback: string) => {
    setActionError(null);
    try {
      await fn();
    } catch (err) {
      setActionError(getApiErrorMessage(err, fallback));
    }
  };

  const handleStart = () =>
    act(async () => {
      if (!recipeId) return;
      // Sizing from an ingredient IS the batch size, so an absolute serving
      // count alongside it would be two answers to one question.
      const servings = scaleTo ? null : target;
      const s = sessionId
        ? await updateSession.mutateAsync({ id: sessionId, servingsMade: servings, plan: plan() })
        : await startSession.mutateAsync({ recipeId, servingsMade: servings, plan: plan() });
      setSessionId(s.id);
      setPhase("cook");
    }, "Could not start cooking");

  const handleFinish = async () => {
    if (!sessionId) return;
    setFinishError(null);
    try {
      const result = await finish.mutateAsync({
        id: sessionId,
        outputWeightGrams: madeGrams(),
        outputPortions: portions === "" ? null : Number(portions),
        // A tub size is expressed in the same unit the cook weighed in — asking
        // for the total in pounds and the tubs in grams is a unit switch nobody
        // wants mid-flow.
        outputPortionSize: portionSize === "" ? null : Number(portionSize),
        outputPortionUnit: portionSize === "" ? null : madeUnit,
      });
      // Name what was trimmed. A dish made with 60% of its rub is a different
      // dish, and the moment to say so is now, not when someone tastes it.
      const trimmed = result.cook?.capped_ingredients ?? [];
      const made = scaleTo
        ? `${amount(Number(result.cook?.scale_used ?? 1))}× the recipe`
        : `${amount(target)} servings`;
      setStatus(
        trimmed.length > 0
          ? `Cooked ${recipe?.name ?? "recipe"} — ${made}, short on ${trimmed
              .map((c) => c.item_name)
              .join(", ")} (used what was there)`
          : `Cooked ${recipe?.name ?? "recipe"} — ${made}`,
      );
      navigate(`/recipes/${recipeId}`);
    } catch (err) {
      setFinishError(getApiErrorMessage(err, "Could not finish that cook"));
    }
  };

  const handleQuit = () => navigate(`/recipes/${recipeId}`);

  const handleStop = () =>
    act(async () => {
      if (sessionId) await abandon.mutateAsync(sessionId);
      navigate(`/recipes/${recipeId}`);
    }, "Could not stop cooking");

  if (recipeQuery.isLoading) {
    return (
      <Group justify="center" py="xl">
        <Loader />
      </Group>
    );
  }

  if (!recipe) {
    return <Alert color="red">Recipe not found.</Alert>;
  }

  return (
    <Stack>
      <Group justify="space-between" align="flex-start">
        <div>
          <Title order={2}>{recipe.name}</Title>
          <Text c="dimmed" size="sm">
            {phase === "setup"
              ? "Set up your cook"
              : phase === "finish"
                ? "How did it turn out?"
                : session
                  ? `Step ${session.step_number} of ${session.total_steps}`
                  : "Starting…"}
          </Text>
        </div>
        {phase === "cook" ? (
          <Button variant="subtle" color="gray" onClick={handleStop} loading={abandon.isPending}>
            Stop cooking
          </Button>
        ) : (
          <Button variant="subtle" color="gray" onClick={handleQuit}>
            Quit
          </Button>
        )}
      </Group>

      {endedElsewhere && (
        <Alert color="blue" title="This cook has ended">
          <Stack gap="xs">
            <Text size="sm">{endedElsewhere}</Text>
            <Group>
              <Button size="xs" onClick={() => navigate(`/recipes/${recipeId}`)}>
                Back to the recipe
              </Button>
            </Group>
          </Stack>
        </Alert>
      )}

      {phase === "setup" && activeElsewhere && (
        <Alert color="orange" title={`You're cooking ${activeElsewhere.recipe_name}`}>
          <Stack gap="xs">
            <Text size="sm">
              It's on step {activeElsewhere.step_number} of {activeElsewhere.total_steps}. Starting this recipe stops
              that one — nothing is taken from the pantry for it.
            </Text>
            <Group>
              <Button size="xs" variant="light" onClick={() => navigate(`/recipes/${activeElsewhere.recipe_id}/cook`)}>
                Go back to {activeElsewhere.recipe_name}
              </Button>
            </Group>
          </Stack>
        </Alert>
      )}

      {phase === "finish" ? (
        <FinishSheet
          recipe={recipe}
          madeWeight={madeWeight}
          madeUnit={madeUnit}
          portions={portions}
          portionSize={portionSize}
          onMadeWeight={setMadeWeight}
          onMadeUnit={setMadeUnit}
          onPortions={setPortions}
          onPortionSize={setPortionSize}
          onBack={() => setPhase("cook")}
          onLog={handleFinish}
          pending={finish.isPending}
          error={finishError}
          servings={target}
        />
      ) : phase === "setup" ? (
        <>
          <Card withBorder padding="md">
            <Stack gap="sm">
              <Group align="flex-end" justify="space-between" gap="md" wrap="wrap">
                <Group align="flex-end" gap="md" wrap="wrap">
                  <QuantityStepper
                    label="How many servings are you making?"
                    ariaLabel="servings"
                    min={0.25}
                    step={1}
                    decimalScale={2}
                    value={servingsMade}
                    onChange={setServingsMade}
                  />
                  <Text size="sm" c="dimmed" pb={12}>
                    Recipe as written makes <b>{baseServings}</b>.
                  </Text>
                </Group>
                <Button
                  onClick={handleStart}
                  disabled={!hasInstructions}
                  loading={startSession.isPending || updateSession.isPending}
                  size="md"
                  mb={4}
                >
                  {sessionId ? "Back to cooking" : "Start cooking"}
                </Button>
              </Group>
              {!hasInstructions && (
                <Text size="xs" c="dimmed">
                  This recipe has no instructions — add some to walk through it.
                </Text>
              )}
              {scaleTo && (
                <Text size="xs" c="orange.7">
                  Sized to use all your{" "}
                  {lines.find((l) => l.ingredient_id === scaleTo)?.item_name ?? "ingredient"} — every
                  other amount scales to match, and the servings figure above is ignored.
                </Text>
              )}
              {!scaleTo && target !== baseServings && (
                <Text size="xs" c="dimmed">
                  Amounts below are scaled to {amount(target)} serving{target === 1 ? "" : "s"} (
                  {amount(target / baseServings)}× the recipe).
                </Text>
              )}
              <Text size="xs" c="dimmed">
                If the "makes {baseServings}" figure is wrong, fix it on the recipe first — every amount
                here is scaled from it.
              </Text>
            </Stack>
          </Card>

          <Card withBorder padding="md">
            <Title order={4} mb="sm">
              Ingredients
            </Title>
            {availability.isFetching && lines.length === 0 ? (
              <Group justify="center" py="md">
                <Loader size="sm" />
              </Group>
            ) : (
              <Stack gap="xs">
                {lines.map((line) => {
                  const override = overrides[line.ingredient_id];
                  const omitted = Boolean(override?.omit);
                  return (
                    <Group key={line.ingredient_id} justify="space-between" wrap="nowrap" align="center">
                      <Group gap="xs" wrap="nowrap" style={{ minWidth: 0, flex: 1 }}>
                        <Checkbox
                          checked={!omitted}
                          aria-label={`Use ${line.item_name}`}
                          onChange={(e) => setOverride(line.ingredient_id, { omit: !e.currentTarget.checked })}
                        />
                        <Text
                          size="sm"
                          style={{ textDecoration: omitted ? "line-through" : undefined }}
                          c={omitted ? "dimmed" : undefined}
                          truncate
                        >
                          {line.item_name}
                        </Text>
                        {!omitted && line.quantity_mode !== "package" && (
                          <Anchor
                            component="button"
                            type="button"
                            size="xs"
                            c={scaleTo === line.ingredient_id ? "orange.7" : "dimmed"}
                            onClick={() =>
                              setScaleTo((current) =>
                                current === line.ingredient_id ? null : line.ingredient_id,
                              )
                            }
                          >
                            {scaleTo === line.ingredient_id ? "using it all" : "use it all"}
                          </Anchor>
                        )}
                        {!omitted && line.status === "missing" && (
                          <Badge size="xs" color="orange" variant="light">
                            short
                          </Badge>
                        )}
                        {!omitted && line.status === "have" && (
                          <Badge size="xs" color="green" variant="light">
                            have
                          </Badge>
                        )}
                        {line.status === "optional" && (
                          <Badge size="xs" color="gray" variant="light">
                            to taste
                          </Badge>
                        )}
                      </Group>
                      <Group gap={4} wrap="nowrap">
                        <QuantityStepper
                          size="sm"
                          min={0}
                          step={stepFor(effectiveAmount(line))}
                          decimalScale={3}
                          disabled={omitted}
                          ariaLabel={`Amount of ${line.item_name}`}
                          value={omitted ? "" : effectiveAmount(line)}
                          onChange={(v) =>
                            setOverride(line.ingredient_id, { quantity: v === "" ? null : Number(v), unit: null })
                          }
                        />
                        <Text size="xs" c="dimmed" w={44}>
                          {line.unit}
                        </Text>
                      </Group>
                    </Group>
                  );
                })}
              </Stack>
            )}
            {short.length > 0 && (
              <Alert color="orange" variant="light" mt="sm">
                <Stack gap="xs">
                  <Text size="sm">
                    Short on {short.map((l) => l.item_name).join(", ")}. You can still cook — uncheck
                    what you're leaving out, or adjust the amounts.
                  </Text>
                  <Checkbox
                    checked={allowShort}
                    onChange={(e) => setAllowShort(e.currentTarget.checked)}
                    label="Use what I have"
                    description="Take whatever is on the shelf for a short ingredient instead of stopping. The proportions won't be exact, and you'll be told which ones were trimmed."
                  />
                </Stack>
              </Alert>
            )}
          </Card>

          {actionError && <Alert color="red">{actionError}</Alert>}
        </>
      ) : (
        <>
          {/* Controls ride WITH the progress bar at the top: the step text can
              be long, and burying Next/Back under it means scrolling past the
              instruction you're reading to advance. */}
          {!session ? (
            <Group justify="center" py="xl">
              <Loader />
            </Group>
          ) : (
            <>
              <Group wrap="nowrap" align="center" gap="sm">
                <Button
                  variant="default"
                  size="md"
                  onClick={() =>
                    session.is_first_step
                      ? setPhase("setup")
                      : act(() => moveStep.mutateAsync({ id: session.id, action: "previous" }), "Could not go back")
                  }
                >
                  Back
                </Button>
                <Progress
                  value={session.total_steps ? (session.completed_steps.length / session.total_steps) * 100 : 0}
                  size="sm"
                  style={{ flex: 1 }}
                  aria-label="Steps done"
                />
                {session.is_last_step ? (
                  <Button size="md" color="green" onClick={() => setPhase("finish")}>
                    Finish
                  </Button>
                ) : (
                  <Button
                    size="md"
                    loading={moveStep.isPending}
                    onClick={() => act(() => moveStep.mutateAsync({ id: session.id, action: "complete" }), "Could not move on")}
                  >
                    Done, next
                  </Button>
                )}
              </Group>

              <Card withBorder padding="xl">
                <Stack gap="md">
                  <Text size="xl" style={{ lineHeight: 1.5 }}>
                    {session.step_text}
                  </Text>
                  <Checkbox
                    size="md"
                    label={session.step_done ? "Done" : "Mark this step done"}
                    checked={session.step_done}
                    onChange={(e) =>
                      act(
                        () =>
                          setStepDone.mutateAsync({
                            id: session.id,
                            step: session.step_number,
                            done: e.currentTarget.checked,
                          }),
                        "Could not tick that step",
                      )
                    }
                  />
                </Stack>
              </Card>

              <CookTimers
                timers={session.timers}
                suggestions={session.step_timers}
                stepNumber={session.step_number}
                pending={startTimer.isPending}
                onStart={(seconds, label) =>
                  act(
                    () => startTimer.mutateAsync({ seconds, label, stepNumber: session.step_number }),
                    "Could not start the timer",
                  )
                }
                onStop={(id) => act(() => stopTimer.mutateAsync(id), "Could not stop the timer")}
              />

              <Card withBorder padding="sm">
                <Text size="xs" c="dimmed" mb={6}>
                  Steps · {session.steps_left === 0 ? "all done" : `${session.steps_left} left`}
                </Text>
                <Stack gap={4}>
                  {session.steps.map((st) => (
                    <Group key={st.number} gap="xs" wrap="nowrap" align="flex-start">
                      <Checkbox
                        mt={2}
                        checked={st.done}
                        aria-label={`Step ${st.number} done`}
                        onChange={(e) =>
                          act(
                            () =>
                              setStepDone.mutateAsync({ id: session.id, step: st.number, done: e.currentTarget.checked }),
                            "Could not tick that step",
                          )
                        }
                      />
                      <Anchor
                        component="button"
                        type="button"
                        size="sm"
                        ta="left"
                        c={st.number === session.step_number ? undefined : "dimmed"}
                        fw={st.number === session.step_number ? 700 : undefined}
                        td={st.done ? "line-through" : undefined}
                        onClick={() =>
                          act(
                            () => moveStep.mutateAsync({ id: session.id, action: "goto", step: st.number }),
                            "Could not go to that step",
                          )
                        }
                      >
                        {st.number}. {st.text}
                      </Anchor>
                    </Group>
                  ))}
                </Stack>
              </Card>

              <Card withBorder padding="sm">
                <Text size="xs" c="dimmed" mb={6}>
                  Using
                </Text>
                {/* The session's own list: what Finish will take out, with the
                    plan's overrides, omissions and substitutions applied. */}
                <Group gap="xs">
                  {session.ingredients.map((l) => (
                    <Badge key={l.ingredient_id} variant="light" size="sm">
                      {l.quantity} {l.unit} {l.item}
                      {l.replaces ? ` (for ${l.replaces})` : ""}
                    </Badge>
                  ))}
                </Group>
              </Card>
              {session.status === "active" && <ScopedSubstitutionReview
                session={session} ingredients={recipeQuery.data?.ingredients ?? []}
                onChanged={async () => { const result = await current.refetch(); if (result.data) adopt(result.data); }}
              />}
            </>
          )}

          {actionError && <Alert color="red">{actionError}</Alert>}
          {finishError && <Alert color="red">{finishError}</Alert>}

          <Text size="xs" c="dimmed" ta="center">
            Nothing leaves your pantry until you tap Finish. Your household's Wildstone agent can follow along, move
            the steps and set timers too.
          </Text>
        </>
      )}
    </Stack>
  );
}
