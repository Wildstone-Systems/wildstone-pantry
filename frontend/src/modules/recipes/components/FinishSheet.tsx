import { Alert, Button, Card, Group, NumberInput, Select, Stack, Text } from "@mantine/core";

import type { Recipe } from "../hooks/recipes-hooks";

/** Weight units the sheet offers, in grams. */
const GRAMS_PER: Record<string, number> = { lb: 453.59237, oz: 28.349523125, g: 1, kg: 1000 };

type Props = {
  recipe: Recipe;
  servings: number;
  madeWeight: number | string;
  madeUnit: string;
  portions: number | string;
  portionSize: number | string;
  onMadeWeight: (value: number | string) => void;
  onMadeUnit: (value: string) => void;
  onPortions: (value: number | string) => void;
  onPortionSize: (value: number | string) => void;
  onBack: () => void;
  onLog: () => void;
  pending: boolean;
  error: string | null;
};

/** The last screen of a cook: what came out, and what shape it went into.
 *
 * Everything here is OPTIONAL and starts blank. An unweighed dish falls back to
 * the recipe's declared yield, and an unstated tub count lands the yield as one
 * lot — which is exactly what happened before this screen existed. Pre-filling
 * either would put numbers into the ledger that nobody actually looked at, and a
 * plausible wrong weight is worse than an admitted estimate.
 */
export function FinishSheet({
  recipe,
  servings,
  madeWeight,
  madeUnit,
  portions,
  portionSize,
  onMadeWeight,
  onMadeUnit,
  onPortions,
  onPortionSize,
  onBack,
  onLog,
  pending,
  error,
}: Props) {
  const tracksOutput = Boolean(recipe.output_item_id);
  const count = Number(portions) || 0;
  const size = Number(portionSize) || 0;
  const made = Number(madeWeight) || 0;

  // What stays out of the fridge: the difference between what was made and what
  // was packed. This is the number that makes the real evening expressible —
  // three tubs for the week, and the rest still on the table.
  const loose = made > 0 && count > 0 && size > 0 ? made - size * count : 0;
  const overPacked = made > 0 && count > 0 && size > 0 && size * count > made;

  const evenSplit = made > 0 && count > 0 && size <= 0 ? made / count : 0;

  return (
    <Stack>
      <Group wrap="nowrap" align="center" gap="sm">
        <Button variant="default" size="md" onClick={onBack}>
          Back
        </Button>
        <div style={{ flex: 1 }} />
        <Button
          size="md"
          color="green"
          loading={pending}
          disabled={overPacked}
          onClick={onLog}
        >
          Log it
        </Button>
      </Group>

      {tracksOutput ? (
        <>
          <Card withBorder padding="md">
            <Stack gap="xs">
              <Text fw={500}>How much did you make?</Text>
              <Text size="xs" c="dimmed">
                If you weighed it, this is what gets stocked — not the recipe's estimate. Leave it
                blank and the recipe's own yield stands.
              </Text>
              <Group gap="xs" align="flex-end" wrap="nowrap">
                <NumberInput
                  aria-label="Finished weight"
                  placeholder="e.g. 4.5"
                  value={madeWeight}
                  onChange={onMadeWeight}
                  min={0}
                  step={0.1}
                  decimalScale={3}
                  style={{ flex: 1 }}
                />
                <Select
                  aria-label="Weight unit"
                  data={Object.keys(GRAMS_PER)}
                  value={madeUnit}
                  onChange={(v) => onMadeUnit(v ?? "lb")}
                  w={90}
                  allowDeselect={false}
                />
              </Group>
            </Stack>
          </Card>

          <Card withBorder padding="md">
            <Stack gap="xs">
              <Text fw={500}>Packing any of it away?</Text>
              <Text size="xs" c="dimmed">
                Each container is tracked on its own, so you can finish or throw out one without
                touching the others. Skip this if it all went straight to the table.
              </Text>
              <Group gap="xs" align="flex-end" wrap="nowrap">
                <NumberInput
                  aria-label="Number of containers"
                  label="Containers"
                  placeholder="e.g. 3"
                  value={portions}
                  onChange={onPortions}
                  min={1}
                  step={1}
                  style={{ flex: 1 }}
                />
                <NumberInput
                  aria-label="Size of each container"
                  label={`Each (${madeUnit}, optional)`}
                  placeholder="split evenly"
                  value={portionSize}
                  onChange={onPortionSize}
                  min={0}
                  step={0.1}
                  decimalScale={3}
                  style={{ flex: 1 }}
                />
              </Group>

              {overPacked && (
                <Alert color="red" variant="light">
                  {count} × {size} {madeUnit} is more than the {made} {madeUnit} you made.
                </Alert>
              )}
              {!overPacked && loose > 0 && (
                <Text size="xs" c="dimmed">
                  {count} × {size} {madeUnit} packed away, and {Number(loose.toFixed(3))} {madeUnit}{" "}
                  left out.
                </Text>
              )}
              {evenSplit > 0 && (
                <Text size="xs" c="dimmed">
                  All of it, split into {count} × {Number(evenSplit.toFixed(3))} {madeUnit}.
                </Text>
              )}
              {count > 0 && made <= 0 && (
                <Text size="xs" c="dimmed">
                  Splitting the recipe's yield into {count}.
                </Text>
              )}
            </Stack>
          </Card>
        </>
      ) : (
        <Card withBorder padding="md">
          <Text size="sm" c="dimmed">
            This recipe doesn't track a finished item, so there's nothing to weigh or pack — cooking
            it just spends the ingredients. Add an output item to the recipe if you want leftovers
            tracked.
          </Text>
        </Card>
      )}

      {error && <Alert color="red">{error}</Alert>}

      <Text size="xs" c="dimmed" ta="center">
        Nothing leaves your pantry until you tap Log it — {servings} servings' worth of ingredients.
      </Text>
    </Stack>
  );
}
