import { ActionIcon, Alert, Badge, Button, Card, Group, Loader, SimpleGrid, Stack, Text, Title, UnstyledButton } from "@mantine/core";
import { CalendarDays, ChevronLeft, ChevronRight, Plus, ShoppingCart } from "lucide-react";
import { useMemo, useState } from "react";

import { getApiErrorMessage } from "@/api/errors";
import { useHouseholdProfiles } from "@/api/hooks";
import { useAuth } from "@/state/AuthProvider";

import { CalendarFeedModal } from "../components/CalendarFeedModal";
import { EntryEditor, type EditorTarget } from "../components/EntryEditor";
import { ShoppingGapModal } from "../components/ShoppingGapModal";
import {
  MEAL_TYPES,
  addDays,
  forecastLabel,
  sundayOf,
  todayLocal,
  useMealPlanWeek,
  useNutritionForecast,
  weekDays,
  type ForecastDay,
  type ForecastPerson,
  type MealPlanEntry,
  type MealType,
} from "../hooks/mealplan-hooks";

function parseDay(day: string): Date {
  const [y, m, d] = day.split("-").map(Number);
  return new Date(y, m - 1, d);
}

function shortDate(day: string): string {
  return parseDay(day).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

function weekdayName(day: string): string {
  return parseDay(day).toLocaleDateString(undefined, { weekday: "long" });
}

const MEAL_LABEL: Record<MealType, string> = { breakfast: "Breakfast", lunch: "Lunch", dinner: "Dinner", snack: "Snack" };

function EntryRow({ entry, forName, onOpen }: { entry: MealPlanEntry; forName: string | null; onOpen: () => void }) {
  const muted = entry.status === "skipped";
  return (
    <UnstyledButton onClick={onOpen} aria-label={`Edit ${entry.meal}`} style={{ width: "100%" }}>
      <Group gap={6} wrap="nowrap" justify="space-between">
        <Text size="sm" td={muted ? "line-through" : undefined} c={muted ? "dimmed" : undefined} truncate>
          {entry.meal}
        </Text>
        <Group gap={4} wrap="nowrap">
          {forName && (
            <Badge size="xs" variant="light" color="grape">
              {forName}
            </Badge>
          )}
          {entry.status !== "planned" && (
            <Badge size="xs" variant="light" color={entry.status === "cooked" ? "green" : "gray"}>
              {entry.status}
            </Badge>
          )}
          <Text size="xs" c="dimmed">
            ×{entry.servings}
          </Text>
        </Group>
      </Group>
    </UnstyledButton>
  );
}

/** Planned calories per person for one day (#34). Hidden on a day nobody has
 *  anything planned. Red when over that person's daily target. */
function DayForecast({ day, people }: { day: string; people: ForecastPerson[] }) {
  const rows = people
    .map((p) => ({ person: p, day: p.days.find((d) => d.date === day) }))
    .filter((r): r is { person: ForecastPerson; day: ForecastDay } => r.day != null && r.day.meals > 0);
  if (rows.length === 0) return null;
  return (
    <Stack gap={0} mt={8} pt={6} style={{ borderTop: "1px solid var(--mantine-color-default-border)" }}>
      {rows.map(({ person, day: d }) => {
        const over = person.target != null && d.calories > person.target;
        return (
          <Group key={person.profile_id} justify="space-between" gap={4} wrap="nowrap">
            <Text size="xs" c="dimmed" truncate>
              {person.display_name}
            </Text>
            <Text size="xs" c={over ? "red" : "dimmed"} data-testid={`forecast-${day}-${person.display_name}`}>
              {forecastLabel(d, person.target)}
            </Text>
          </Group>
        );
      })}
    </Stack>
  );
}

export function MealPlanPage() {
  const today = todayLocal();
  const [start, setStart] = useState(() => sundayOf(today));
  const week = useMealPlanWeek(start);
  const forecast = useNutritionForecast(start);
  const profiles = useHouseholdProfiles();
  const [target, setTarget] = useState<EditorTarget | null>(null);
  const [shoppingOpen, setShoppingOpen] = useState(false);
  const [calendarOpen, setCalendarOpen] = useState(false);
  const { isAdmin } = useAuth();
  // A week that has fully passed has nothing left to shop for.
  const weekIsPast = addDays(start, 6) < today;

  const days = useMemo(() => weekDays(start), [start]);
  const dayOptions = useMemo(
    () => days.map((d) => ({ value: d, label: `${weekdayName(d)}, ${shortDate(d)}` })),
    [days],
  );
  const profileName = useMemo(
    () => new Map((profiles.data ?? []).map((p) => [p.id, p.display_name] as const)),
    [profiles.data],
  );
  const byCell = useMemo(() => {
    const map = new Map<string, MealPlanEntry[]>();
    for (const e of week.data?.entries ?? []) {
      const key = `${e.plan_date}|${e.meal_type}`;
      map.set(key, [...(map.get(key) ?? []), e]);
    }
    return map;
  }, [week.data]);

  return (
    <Stack gap="md">
      <Group justify="space-between" wrap="wrap">
        <Title order={2}>Meal Plan</Title>
        <Group gap="xs">
          <ActionIcon variant="default" aria-label="Previous week" onClick={() => setStart(addDays(start, -7))}>
            <ChevronLeft size={16} />
          </ActionIcon>
          <Text fw={500} miw={150} ta="center">
            {shortDate(days[0])} – {shortDate(days[6])}
          </Text>
          <ActionIcon variant="default" aria-label="Next week" onClick={() => setStart(addDays(start, 7))}>
            <ChevronRight size={16} />
          </ActionIcon>
          {start !== sundayOf(today) && (
            <Button variant="subtle" size="xs" onClick={() => setStart(sundayOf(today))}>
              This week
            </Button>
          )}
          <Button
            size="xs"
            variant="light"
            leftSection={<ShoppingCart size={14} />}
            disabled={weekIsPast}
            onClick={() => setShoppingOpen(true)}
          >
            Shopping
          </Button>
          {isAdmin && (
            <Button
              size="xs"
              variant="subtle"
              leftSection={<CalendarDays size={14} />}
              onClick={() => setCalendarOpen(true)}
            >
              Calendar link
            </Button>
          )}
        </Group>
      </Group>

      {week.isError && (
        <Alert color="red" variant="light">
          {getApiErrorMessage(week.error, "Could not load the meal plan")}
        </Alert>
      )}
      {week.isLoading && <Loader size="sm" />}

      <SimpleGrid cols={{ base: 1, sm: 2, lg: 4 }} spacing="sm">
        {days.map((day) => (
          <Card key={day} withBorder padding="sm" data-testid={`day-${day}`}>
            <Group justify="space-between" mb={6}>
              <Text fw={600}>{weekdayName(day)}</Text>
              <Group gap={4}>
                {day === today && (
                  <Badge size="xs" variant="filled">
                    Today
                  </Badge>
                )}
                <Text size="xs" c="dimmed">
                  {shortDate(day)}
                </Text>
              </Group>
            </Group>
            <Stack gap={8}>
              {MEAL_TYPES.map((mealType) => {
                const entries = byCell.get(`${day}|${mealType}`) ?? [];
                return (
                  <Stack key={mealType} gap={2}>
                    <Group justify="space-between" gap={4}>
                      <Text size="xs" c="dimmed" tt="uppercase" fw={600}>
                        {MEAL_LABEL[mealType]}
                      </Text>
                      <ActionIcon
                        size="xs"
                        variant="subtle"
                        aria-label={`Add ${mealType} on ${weekdayName(day)}`}
                        onClick={() => setTarget({ mode: "add", plan_date: day, meal_type: mealType })}
                      >
                        <Plus size={12} />
                      </ActionIcon>
                    </Group>
                    {entries.map((entry) => (
                      <EntryRow
                        key={entry.id}
                        entry={entry}
                        forName={entry.profile_id ? (profileName.get(entry.profile_id) ?? "Someone") : null}
                        onOpen={() => setTarget({ mode: "edit", entry })}
                      />
                    ))}
                  </Stack>
                );
              })}
            </Stack>
            <DayForecast day={day} people={forecast.data?.people ?? []} />
          </Card>
        ))}
      </SimpleGrid>

      <EntryEditor target={target} dayOptions={dayOptions} onClose={() => setTarget(null)} />
      <ShoppingGapModal opened={shoppingOpen} start={start} today={today} onClose={() => setShoppingOpen(false)} />
      <CalendarFeedModal opened={calendarOpen} onClose={() => setCalendarOpen(false)} />
    </Stack>
  );
}
