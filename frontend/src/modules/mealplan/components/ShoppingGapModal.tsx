import { Alert, Anchor, Badge, Button, Checkbox, Group, Loader, Modal, Stack, Text } from "@mantine/core";
import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";

import { getApiErrorMessage } from "@/api/errors";
import { ROUTES } from "@/routes/paths";

import {
  formatAmount,
  useExcludeItem,
  usePushShoppingGap,
  useShoppingGap,
  useUnexcludeItem,
  type ShoppingGapPushResult,
} from "../hooks/mealplan-hooks";

/**
 * "Add missing to list" for the week shown (#31). Today onward only; lines we
 * can measure start ticked, lines we can't ("check") start unticked. Nothing
 * goes on the shopping list until the button is pressed.
 */
export function ShoppingGapModal({
  opened,
  start,
  today,
  onClose,
}: {
  opened: boolean;
  start: string;
  today: string;
  onClose: () => void;
}) {
  const gap = useShoppingGap(start, today, opened);
  const push = usePushShoppingGap();
  const exclude = useExcludeItem();
  const unexclude = useUnexcludeItem();
  const [chosen, setChosen] = useState<Set<string>>(new Set());
  const [result, setResult] = useState<ShoppingGapPushResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  const lines = useMemo(() => gap.data?.lines ?? [], [gap.data]);

  // Re-tick the measurable lines whenever the gap is (re)loaded.
  useEffect(() => {
    setChosen(new Set(lines.filter((l) => l.status === "short").map((l) => l.key)));
  }, [lines]);

  useEffect(() => {
    if (opened) {
      setResult(null);
      setError(null);
    }
  }, [opened]);

  function toggle(key: string, on: boolean) {
    setChosen((prev) => {
      const next = new Set(prev);
      if (on) next.add(key);
      else next.delete(key);
      return next;
    });
  }

  async function run(action: () => Promise<unknown>) {
    setError(null);
    try {
      await action();
    } catch (err) {
      setError(getApiErrorMessage(err, "Something went wrong"));
    }
  }

  const selected = lines.filter((l) => chosen.has(l.key));

  return (
    <Modal opened={opened} onClose={onClose} title="Shopping for this week" size="lg">
      <Stack gap="sm">
        <Text size="sm" c="dimmed">
          Ingredients for planned meals from today on, minus what you already have.
        </Text>
        {error && (
          <Alert color="red" variant="light">
            {error}
          </Alert>
        )}
        {gap.isLoading && <Loader size="sm" />}
        {gap.isError && (
          <Alert color="red" variant="light">
            {getApiErrorMessage(gap.error, "Could not work out what to buy")}
          </Alert>
        )}
        {result && (
          <Alert color="green" variant="light">
            {result.added.length > 0 ? `Added ${result.added.join(", ")}.` : "Nothing new to add."}
            {result.already_on_list.length > 0 && ` Already on the list: ${result.already_on_list.join(", ")}.`}{" "}
            <Anchor component={Link} to={ROUTES.shoppingList} size="sm">
              Open shopping list
            </Anchor>
          </Alert>
        )}

        {gap.isSuccess && lines.length === 0 && <Text size="sm">You have everything for the meals planned.</Text>}

        {lines.map((line) => (
          <Group key={`${line.key}|${line.unit}`} justify="space-between" wrap="nowrap" align="flex-start">
            <Checkbox
              checked={chosen.has(line.key)}
              onChange={(e) => toggle(line.key, e.currentTarget.checked)}
              label={
                <Stack gap={0}>
                  <Group gap={6}>
                    <Text size="sm" fw={500}>
                      {line.label}
                    </Text>
                    {line.status === "short" ? (
                      <Text size="sm">{formatAmount(line.to_buy, line.unit)}</Text>
                    ) : (
                      <Badge size="xs" color="yellow" variant="light">
                        check amount
                      </Badge>
                    )}
                  </Group>
                  <Text size="xs" c="dimmed">
                    {line.status === "short"
                      ? `Need ${formatAmount(line.needed, line.unit)}, have ${formatAmount(line.on_hand, line.unit)}`
                      : `Need ${formatAmount(line.needed, line.unit)}; on-hand can't be compared`}
                    {" · "}
                    {line.meals.join(", ")}
                  </Text>
                </Stack>
              }
            />
            <Button
              size="compact-xs"
              variant="subtle"
              color="gray"
              disabled={exclude.isPending}
              onClick={() => run(() => exclude.mutateAsync({ key: line.key, label: line.label }))}
            >
              Always skip
            </Button>
          </Group>
        ))}

        {(gap.data?.excluded.length ?? 0) > 0 && (
          <Stack gap={4}>
            <Text size="xs" c="dimmed" tt="uppercase" fw={600}>
              Always skipped
            </Text>
            <Group gap={6}>
              {gap.data!.excluded.map((ex) => (
                <Badge
                  key={ex.id}
                  variant="outline"
                  color="gray"
                  rightSection={
                    <Anchor
                      component="button"
                      size="xs"
                      aria-label={`Stop skipping ${ex.label}`}
                      onClick={() => run(() => unexclude.mutateAsync(ex.id))}
                    >
                      undo
                    </Anchor>
                  }
                >
                  {ex.label}
                </Badge>
              ))}
            </Group>
          </Stack>
        )}

        <Group justify="flex-end" mt="xs">
          <Button variant="default" onClick={onClose}>
            Close
          </Button>
          <Button
            disabled={selected.length === 0}
            loading={push.isPending}
            onClick={() =>
              run(async () => setResult(await push.mutateAsync({ start, today, keys: selected.map((l) => l.key) })))
            }
          >
            Add {selected.length} to shopping list
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}
