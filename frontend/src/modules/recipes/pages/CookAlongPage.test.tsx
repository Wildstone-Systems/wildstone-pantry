import { cleanup, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { renderWithProviders } from "@/test-utils";

const apiGet = vi.fn();
const apiPost = vi.fn();
const apiPatch = vi.fn();
const apiDelete = vi.fn();
vi.mock("@/state/AuthProvider", () => ({ useAuth: () => ({ isAdmin: false }) }));

vi.mock("@/api/client", () => ({
  api: {
    get: (...a: unknown[]) => apiGet(...a),
    post: (...a: unknown[]) => apiPost(...a),
    patch: (...a: unknown[]) => apiPatch(...a),
    delete: (...a: unknown[]) => apiDelete(...a),
  },
}));

const navigate = vi.fn();
vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual<typeof import("react-router-dom")>("react-router-dom");
  return { ...actual, useParams: () => ({ recipeId: "r1" }), useNavigate: () => navigate };
});

const STEPS = ["Mix the dough.", "Bake until golden, about 20 minutes."];

const RECIPE = {
  recipe: {
    id: "r1",
    name: "Flatbread",
    instructions: STEPS.map((t, i) => `${i + 1}. ${t}`).join("\n"),
    servings: 4,
  },
  ingredients: [],
};

const RECIPE_WITH_OUTPUT = {
  ...RECIPE,
  recipe: { ...RECIPE.recipe, output_item_id: "out1", output_quantity: 60, output_unit: "oz" },
};

// --- a small stand-in for the cook-session API (ADR 0026 §6.1) --------------

type Timer = { id: string; label: string; step_number: number | null; duration_seconds: number; ends_at: string };
type Fake = {
  id: string;
  recipe_id: string;
  recipe_name: string;
  status: string;
  servings_made: string | null;
  current: number;
  done: number[];
  plan: unknown;
  ingredients?: unknown[];
};
let fake: Fake | null = null;
let timers: Timer[] = [];

function view(f: Fake) {
  return {
    id: f.id,
    recipe_id: f.recipe_id,
    recipe_name: f.recipe_name,
    status: f.status,
    base_servings: 4,
    servings_made: f.servings_made,
    step_number: f.current + 1,
    total_steps: STEPS.length,
    step_text: STEPS[f.current],
    is_first_step: f.current === 0,
    is_last_step: f.current === STEPS.length - 1,
    step_done: f.done.includes(f.current + 1),
    step_timers: f.current === 1 ? [{ seconds: 1200, label: "20 minutes" }] : [],
    steps: STEPS.map((text, i) => ({ number: i + 1, text, done: f.done.includes(i + 1) })),
    completed_steps: [...f.done].sort(),
    steps_left: STEPS.length - f.done.length,
    timers: timers.map((t) => ({
      ...t,
      session_id: f.id,
      started_at: new Date().toISOString(),
      remaining_seconds: Math.max(0, Math.round((new Date(t.ends_at).getTime() - Date.now()) / 1000)),
      state: new Date(t.ends_at).getTime() <= Date.now() ? "ringing" : "running",
    })),
    plan: f.plan,
    step_ingredients: [],
    ingredients: f.ingredients ?? [],
    cook_event_id: null,
  };
}

function startFake(over: Partial<Fake> = {}): Fake {
  fake = { id: "s1", recipe_id: "r1", recipe_name: "Flatbread", status: "active", servings_made: "4", current: 0, done: [], plan: null, ...over };
  return fake;
}

const SHORT_AVAILABILITY = {
  recipe_id: "r1",
  ingredients: [
    {
      ingredient_id: "ing1",
      item_id: "i1",
      item_name: "Flour",
      canonical_ingredient: "flour",
      quantity_required: "500",
      unit: "g",
      on_hand: "100",
      base_unit: "g",
      on_hand_recipe_unit: "100",
      status: "missing",
      available_via: null,
      suggestions: [],
      substitution_suggestions: [],
    },
  ],
};

const AVAILABILITY = (quantity: string) => ({
  recipe_id: "r1",
  ingredients: [
    {
      ingredient_id: "ing1",
      item_id: "i1",
      item_name: "Flour",
      canonical_ingredient: "flour",
      quantity_required: quantity,
      unit: "g",
      on_hand: "100",
      base_unit: "g",
      on_hand_recipe_unit: "100",
      status: "have",
      available_via: null,
      suggestions: [],
      substitution_suggestions: [],
    },
  ],
});

let recipeFixture: unknown = RECIPE;
let availabilityFixture: (url: string) => unknown = (url) =>
  // The server scales; mirror that so the page shows 4 g at 2 servings.
  AVAILABILITY(url.includes("servings_made=2") ? "4" : "8");

function installFakeServer() {
  apiGet.mockImplementation(async (url: string) => {
    if (url === "/api/items") return { data: { items: [] } };
    if (url === "/api/recipes/unit-vocab") return { data: { units: [] } };
    if (url === "/api/recipes/r1") return { status: 200, data: recipeFixture };
    if (url.startsWith("/api/recipes/r1/availability")) return { status: 200, data: availabilityFixture(url) };
    if (url === "/api/cook-sessions/active")
      return fake && fake.status === "active" ? { status: 200, data: view(fake) } : { status: 204, data: "" };
    if (fake && url === `/api/cook-sessions/${fake.id}`) return { status: 200, data: view(fake) };
    return { status: 200, data: {} };
  });
  apiPost.mockImplementation(async (url: string, body: Record<string, unknown>) => {
    if (url === "/api/cook-sessions") {
      const f = startFake({ servings_made: body.servings_made == null ? null : String(body.servings_made), plan: body.plan });
      return { data: view(f) };
    }
    if (fake && url === `/api/cook-sessions/${fake.id}/step`) {
      const last = STEPS.length - 1;
      if (body.action === "next") fake.current = Math.min(fake.current + 1, last);
      if (body.action === "previous") fake.current = Math.max(fake.current - 1, 0);
      if (body.action === "goto") fake.current = Number(body.step) - 1;
      if (body.action === "complete") {
        fake.done = [...new Set([...fake.done, fake.current + 1])];
        fake.current = Math.min(fake.current + 1, last);
      }
      return { data: view(fake) };
    }
    const tick = fake && url.match(/^\/api\/cook-sessions\/s1\/steps\/(\d+)$/);
    if (fake && tick) {
      const n = Number(tick[1]);
      fake.done = body.done ? [...new Set([...fake.done, n])] : fake.done.filter((d) => d !== n);
      return { data: view(fake) };
    }
    if (fake && url === `/api/cook-sessions/${fake.id}/finish`) {
      fake.status = "finished";
      return { data: { session: view(fake), cook: { cook_event_id: "c1" } } };
    }
    if (url === "/api/cook-timers") {
      const t = {
        id: `t${timers.length + 1}`,
        label: (body.label as string) ?? "Timer",
        step_number: (body.step_number as number) ?? null,
        duration_seconds: body.seconds as number,
        ends_at: new Date(Date.now() + (body.seconds as number) * 1000).toISOString(),
      };
      timers.push(t);
      return { data: t };
    }
    return { data: {} };
  });
  apiPatch.mockImplementation(async (_url: string, body: Record<string, unknown>) => {
    if (!fake) throw new Error("no session");
    fake.plan = body.plan;
    fake.servings_made = body.servings_made == null ? null : String(body.servings_made);
    return { data: view(fake) };
  });
  apiDelete.mockImplementation(async (url: string) => {
    if (fake && url === `/api/cook-sessions/${fake.id}`) {
      fake.status = "abandoned";
      return { data: view(fake) };
    }
    const id = url.split("/").pop();
    timers = timers.filter((t) => t.id !== id);
    return { data: { stopped: "cancelled" } };
  });
}

/** The body of the last POST to `url`. */
function posted(url: string): Record<string, unknown> | undefined {
  const call = [...apiPost.mock.calls].reverse().find((c) => c[0] === url);
  return call?.[1] as Record<string, unknown> | undefined;
}

async function openPage() {
  const { CookAlongPage } = await import("./CookAlongPage");
  renderWithProviders(<CookAlongPage />, { initialEntries: ["/recipes/r1/cook"] });
  await screen.findByText("Flatbread");
}

describe("CookAlongPage", () => {
  beforeEach(() => {
    for (const f of [apiGet, apiPost, apiPatch, apiDelete, navigate]) f.mockReset();
    fake = null;
    timers = [];
    recipeFixture = RECIPE;
    availabilityFixture = (url) => AVAILABILITY(url.includes("servings_made=2") ? "4" : "8");
    installFakeServer();
  });
  afterEach(() => {
    cleanup();
  });

  it("defaults to the recipe's own yield and walks the server's steps", async () => {
    const user = userEvent.setup();
    await openPage();
    expect(await screen.findByLabelText("How many servings are you making?")).toHaveValue("4");

    await user.click(screen.getByRole("button", { name: "Start cooking" }));
    expect(posted("/api/cook-sessions")).toMatchObject({ recipe_id: "r1", servings_made: 4 });

    expect(await screen.findByText("Step 1 of 2")).toBeInTheDocument();
    expect(screen.getAllByText("Mix the dough.").length).toBeGreaterThan(0);

    // "Done, next" ticks the step off and moves on — the same action an
    // agent's "done, what's next?" takes.
    await user.click(screen.getByRole("button", { name: "Done, next" }));
    expect(posted("/api/cook-sessions/s1/step")).toEqual({ action: "complete", step: null });
    expect(await screen.findByText("Step 2 of 2")).toBeInTheDocument();
    expect(screen.getByLabelText("Step 1 done")).toBeChecked();
  });

  it("sends absolute servings_made, not a batch multiplier", async () => {
    const user = userEvent.setup();
    await openPage();

    const servings = await screen.findByLabelText("How many servings are you making?");
    await user.clear(servings);
    await user.type(servings, "2");
    await screen.findByDisplayValue("4");

    await user.click(screen.getByRole("button", { name: "Start cooking" }));
    await waitFor(() => expect(posted("/api/cook-sessions")).toMatchObject({ servings_made: 2 }));
  });

  it("omitting an ingredient goes into the session's plan", async () => {
    const user = userEvent.setup();
    await openPage();
    await user.click(await screen.findByLabelText("Use Flour"));

    await user.click(screen.getByRole("button", { name: "Start cooking" }));
    await waitFor(() =>
      expect(posted("/api/cook-sessions")?.plan).toEqual({
        ingredient_overrides: [{ ingredient_id: "ing1", quantity: null, omit: true }],
        scale_to_ingredient_id: null,
        allow_short_ingredients: false,
        substitutions: [],
      }),
    );
  });

  it("does not touch the pantry until Finish", async () => {
    const user = userEvent.setup();
    await openPage();
    await user.click(screen.getByRole("button", { name: "Start cooking" }));
    await user.click(await screen.findByRole("button", { name: "Done, next" }));
    await user.click(await screen.findByRole("button", { name: "Finish" }));
    await screen.findByText("How did it turn out?");

    // Walked to the finish sheet: no cook, no finish.
    expect(apiPost.mock.calls.map((c) => c[0])).not.toContain("/api/cook-sessions/s1/finish");
    expect(apiPost.mock.calls.map((c) => c[0])).not.toContain("/api/recipes/r1/cook");
  });

  it("picks up a cook of this recipe already under way — started by voice", async () => {
    startFake({ current: 1, done: [1] });
    await openPage();
    expect(await screen.findByText("Step 2 of 2")).toBeInTheDocument();
    expect(screen.getByLabelText("Step 1 done")).toBeChecked();
    // No second session was started.
    expect(posted("/api/cook-sessions")).toBeUndefined();
  });

  it("lists what the session will use — the substitute, not the recipe's line", async () => {
    startFake({
      ingredients: [{ ingredient_id: "i1", item: "Rye", quantity: "12", unit: "g", optional: false, replaces: "Flour" }],
    });
    await openPage();
    expect(await screen.findByText("12 g Rye (for Flour)")).toBeInTheDocument();
  });

  it("going back to the setup and on again keeps the agent's substitutions", async () => {
    const user = userEvent.setup();
    const swap = { original_item_id: "flour", substitute_item_id: "rye", ratio: 1.5, unit: "g" };
    startFake({ plan: { ingredient_overrides: [], scale_to_ingredient_id: null, allow_short_ingredients: false, substitutions: [swap] } });
    await openPage();
    await screen.findByText("Step 1 of 2");
    await user.click(screen.getByRole("button", { name: "Back" }));
    await user.click(await screen.findByRole("button", { name: "Back to cooking" }));
    await waitFor(() => expect(apiPatch).toHaveBeenCalled());
    const body = apiPatch.mock.calls.at(-1)?.[1] as { plan: { substitutions: unknown[] } };
    expect(body.plan.substitutions).toEqual([swap]);
  });

  it("preserves approved row choices separately from legacy swaps when returning to setup", async () => {
    const user = userEvent.setup();
    const swap = { original_item_id: "flour", substitute_item_id: "rye", ratio: 1.5, unit: "g" };
    startFake({ plan: { substitutions: [swap], substitution_approval_ids: ["approved-row-choice"] } });
    await openPage();
    await screen.findByText("Step 1 of 2");
    await user.click(screen.getByRole("button", { name: "Back" }));
    await user.click(await screen.findByRole("button", { name: "Back to cooking" }));
    await waitFor(() => expect(apiPatch).toHaveBeenCalled());
    expect(apiPatch.mock.calls.at(-1)?.[1]).toMatchObject({ plan: { substitutions: [swap], substitution_approval_ids: ["approved-row-choice"] } });
  });

  it("opens substitution review only for an explicitly started cook and never starts or finishes from that action", async () => {
    const user = userEvent.setup();
    await openPage();
    expect(screen.queryByRole("button", { name: "Review a substitution" })).not.toBeInTheDocument();
    expect(apiPost).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "Start cooking" }));
    await user.click(await screen.findByRole("button", { name: "Review a substitution" }));
    expect(await screen.findByText("Review a directional substitution")).toBeInTheDocument();
    expect(apiPost).toHaveBeenCalledTimes(1);
    expect(apiDelete).not.toHaveBeenCalled();
  });

  it("going back to the setup and on again keeps an agent's amount in its own unit", async () => {
    const user = userEvent.setup();
    const override = { ingredient_id: "ing1", quantity: 100, omit: false, unit: "g" };
    startFake({ plan: { ingredient_overrides: [override], scale_to_ingredient_id: null, allow_short_ingredients: false } });
    await openPage();
    await screen.findByText("Step 1 of 2");
    await user.click(screen.getByRole("button", { name: "Back" }));
    await user.click(await screen.findByRole("button", { name: "Back to cooking" }));
    await waitFor(() => expect(apiPatch).toHaveBeenCalled());
    const body = apiPatch.mock.calls.at(-1)?.[1] as { plan: { ingredient_overrides: unknown[] } };
    expect(body.plan.ingredient_overrides).toEqual([override]);
  });

  it("follows a step the agent moves", async () => {
    startFake();
    await openPage();
    await screen.findByText("Step 1 of 2");
    fake!.current = 1;
    expect(await screen.findByText("Step 2 of 2", undefined, { timeout: 5000 })).toBeInTheDocument();
  }, 10000);

  it("says so when the agent finishes the cook", async () => {
    startFake({ current: 1 });
    await openPage();
    await screen.findByText("Step 2 of 2");
    fake!.status = "finished";
    expect(await screen.findByText(/was finished and logged/, undefined, { timeout: 5000 })).toBeInTheDocument();
  }, 10000);

  it("offers the other recipe being cooked instead of silently replacing it", async () => {
    startFake({ recipe_id: "r2", recipe_name: "Soup" });
    await openPage();
    expect(await screen.findByText("You're cooking Soup")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Go back to Soup" })).toBeInTheDocument();
  });

  it("offers the timer the step's words name, and stops it", async () => {
    const user = userEvent.setup();
    startFake({ current: 1 });
    await openPage();
    await user.click(await screen.findByRole("button", { name: "Start 20:00 timer" }));
    expect(posted("/api/cook-timers")).toEqual({
      seconds: 1200,
      label: "Step 2: 20 minutes",
      session_id: "s1",
      step_number: 2,
      profile_id: null,
    });
    expect(await screen.findByText("Step 2: 20 minutes")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(apiDelete).toHaveBeenCalledWith("/api/cook-timers/t1"));
  });

  it("a ringing timer asks to be dismissed", async () => {
    timers = [{ id: "t9", label: "Pasta", step_number: null, duration_seconds: 60, ends_at: new Date(Date.now() - 1000).toISOString() }];
    startFake();
    await openPage();
    expect(await screen.findByText("Pasta")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Dismiss" })).toBeInTheDocument();
    expect(screen.getByText("0:00")).toBeInTheDocument();
  });

  it("stopping the cook abandons the session", async () => {
    const user = userEvent.setup();
    startFake();
    await openPage();
    await user.click(await screen.findByRole("button", { name: "Stop cooking" }));
    await waitFor(() => expect(apiDelete).toHaveBeenCalledWith("/api/cook-sessions/s1"));
    expect(navigate).toHaveBeenCalledWith("/recipes/r1");
  });

  describe("the finish sheet", () => {
    beforeEach(() => {
      recipeFixture = RECIPE_WITH_OUTPUT;
      availabilityFixture = () => AVAILABILITY("8");
    });

    const walkToFinish = async (user: ReturnType<typeof userEvent.setup>) => {
      await openPage();
      await user.click(screen.getByRole("button", { name: "Start cooking" }));
      await user.click(await screen.findByRole("button", { name: "Done, next" }));
      await user.click(await screen.findByRole("button", { name: "Finish" }));
      await screen.findByText("How did it turn out?");
    };

    it("finishes the session with the weighed yield in grams", async () => {
      const user = userEvent.setup();
      await walkToFinish(user);

      await user.type(screen.getByLabelText("Finished weight"), "4.5");
      await user.click(screen.getByRole("button", { name: "Log it" }));

      // 4.5 lb — the sheet converts, because the server takes grams.
      await waitFor(() =>
        expect(posted("/api/cook-sessions/s1/finish")).toMatchObject({ output_weight_grams: 4.5 * 453.59237 }),
      );
      expect(navigate).toHaveBeenCalledWith("/recipes/r1");
    });

    it("sends the containers it was packed into", async () => {
      const user = userEvent.setup();
      await walkToFinish(user);

      await user.type(screen.getByLabelText("Finished weight"), "4.5");
      await user.type(screen.getByLabelText("Number of containers"), "3");
      await user.type(screen.getByLabelText("Size of each container"), "1");
      await user.click(screen.getByRole("button", { name: "Log it" }));

      await waitFor(() =>
        expect(posted("/api/cook-sessions/s1/finish")).toMatchObject({
          output_portions: 3,
          output_portion_size: 1,
          output_portion_unit: "lb",
        }),
      );
    });

    it("sends nothing about the yield when nothing was measured", async () => {
      // Blank means "I didn't look" — the recipe's own yield stands, and no
      // portioning key is sent at all.
      const user = userEvent.setup();
      await walkToFinish(user);

      await user.click(screen.getByRole("button", { name: "Log it" }));

      await waitFor(() => expect(posted("/api/cook-sessions/s1/finish")).toBeDefined());
      const body = posted("/api/cook-sessions/s1/finish")!;
      expect(body).not.toHaveProperty("output_weight_grams");
      expect(body).not.toHaveProperty("output_portions");
      expect(body).not.toHaveProperty("output_portion_size");
    });

    it("refuses to log more packed than was made", async () => {
      const user = userEvent.setup();
      await walkToFinish(user);

      await user.type(screen.getByLabelText("Finished weight"), "2");
      await user.type(screen.getByLabelText("Number of containers"), "3");
      await user.type(screen.getByLabelText("Size of each container"), "1");

      expect(await screen.findByText(/is more than the 2 lb you made/)).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Log it" })).toBeDisabled();
    });

    it("says how much is left out when only some was packed", async () => {
      const user = userEvent.setup();
      await walkToFinish(user);

      await user.type(screen.getByLabelText("Finished weight"), "4.5");
      await user.type(screen.getByLabelText("Number of containers"), "3");
      await user.type(screen.getByLabelText("Size of each container"), "1");

      expect(await screen.findByText(/1.5 lb left out/)).toBeInTheDocument();
    });
  });

  it("'use it all' sizes the batch from the pantry instead of the servings box", async () => {
    // The evening this is for: you don't pick a batch size and then buy meat,
    // you get handed a roast and size the batch around it. Sending servings_made
    // alongside would be two answers to one question.
    const user = userEvent.setup();
    await openPage();
    await user.click(await screen.findByRole("button", { name: "use it all" }));
    expect(await screen.findByText(/Sized to use all your Flour/)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Start cooking" }));
    await waitFor(() => expect(posted("/api/cook-sessions")).toBeDefined());
    const body = posted("/api/cook-sessions")!;
    expect((body.plan as { scale_to_ingredient_id: string }).scale_to_ingredient_id).toBe("ing1");
    expect(body.servings_made).toBeNull();
  });

  describe("when something is short", () => {
    beforeEach(() => {
      availabilityFixture = () => SHORT_AVAILABILITY;
    });

    it("offers to use what's there, and doesn't by default", async () => {
      const user = userEvent.setup();
      await openPage();
      // The all-or-nothing guarantee is the safer thing to be wrong about, so
      // the offer starts off.
      const toggle = await screen.findByLabelText("Use what I have");
      expect(toggle).not.toBeChecked();

      await user.click(screen.getByRole("button", { name: "Start cooking" }));
      await waitFor(() =>
        expect((posted("/api/cook-sessions")?.plan as { allow_short_ingredients: boolean }).allow_short_ingredients).toBe(false),
      );
    });

    it("puts the flag in the plan once you accept the trade", async () => {
      const user = userEvent.setup();
      await openPage();
      await user.click(await screen.findByLabelText("Use what I have"));

      await user.click(screen.getByRole("button", { name: "Start cooking" }));
      await waitFor(() =>
        expect((posted("/api/cook-sessions")?.plan as { allow_short_ingredients: boolean }).allow_short_ingredients).toBe(true),
      );
    });
  });
});
