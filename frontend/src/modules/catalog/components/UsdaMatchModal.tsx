import { Badge, Button, Card, Group, Loader, Modal, ScrollArea, Stack, Text, TextInput } from "@mantine/core";
import { useDebouncedValue } from "@mantine/hooks";
import { useEffect, useState } from "react";

import { getApiErrorMessage } from "@/api/errors";
import { useAppState } from "@/state/AppStateProvider";

import { type Item, useApplyUsdaNutrition, useNutritionCandidates } from "../hooks/catalog-hooks";

const num = (v: number | null) => (v === null || v === undefined ? "—" : Math.round(v));

/** Search USDA and pick the right food for an item's nutrition. Seeds the search
 *  with the item's name; the user can refine ("chicken egg" → "egg whole raw")
 *  when the right food is buried under egg salad / egg foo young. Applying a food
 *  imports its per-100g macros. */
export function UsdaMatchModal({
  itemId,
  itemName,
  opened,
  onClose,
  onApplied,
}: {
  itemId: string | null;
  itemName: string;
  opened: boolean;
  onClose: () => void;
  onApplied: (item: Item) => void;
}) {
  const { setStatus } = useAppState();
  const [query, setQuery] = useState(itemName);
  const [debounced] = useDebouncedValue(query, 400);
  const candidates = useNutritionCandidates(itemId, opened, debounced);
  const apply = useApplyUsdaNutrition();
  const rows = candidates.data?.candidates ?? [];
  const throttled = candidates.data?.throttled ?? false;
  // Show the spinner from the first keystroke (while the debounce is pending) all
  // the way through the request, so the user always knows a search is coming.
  const searching = candidates.isFetching || query.trim() !== debounced.trim();

  // Re-seed the search box with the item's name each time the picker opens.
  useEffect(() => {
    if (opened) setQuery(itemName);
  }, [opened, itemName]);

  const choose = async (fdcId: number | null) => {
    if (!itemId || fdcId == null) return;
    try {
      // Pass the search the user picked from so the server reproduces the list.
      const updated = await apply.mutateAsync({ itemId, fdcId, query: debounced });
      setStatus(`Nutrition set from USDA — ${updated.nutrition_source ?? "matched food"}`);
      onApplied(updated);
      onClose();
    } catch (err) {
      setStatus(getApiErrorMessage(err, "Could not apply that food"));
    }
  };

  return (
    <Modal opened={opened} onClose={onClose} title={`Match nutrition — ${itemName}`} size="lg">
      <Stack gap="sm">
        <Text size="sm">
          Search results are unverified candidates. Choosing “Use this” confirms your reference selection; nutrition
          values come from USDA.
        </Text>
        <TextInput
          label="Search USDA foods"
          description="Values are per 100 g. Refine the search if the right food isn't listed — e.g. 'egg whole raw'."
          placeholder="e.g. egg whole raw"
          value={query}
          onChange={(e) => setQuery(e.currentTarget.value)}
          aria-label="Search USDA foods"
          autoFocus
        />

        {searching ? (
          <Group gap="xs">
            <Loader size="xs" />
            <Text size="sm" c="dimmed">
              Searching USDA…
            </Text>
          </Group>
        ) : throttled ? (
          <Text size="sm" c="orange">
            USDA is rate-limiting the search — wait a few seconds and it&apos;ll retry.
          </Text>
        ) : candidates.data && rows.length === 0 && debounced.trim().length > 0 ? (
          <Text size="sm" c="dimmed">
            No USDA matches. Try different words (needs a USDA key configured).
          </Text>
        ) : null}

        <ScrollArea.Autosize mah={380} type="auto">
          <Stack gap="xs" pr="xs">
            {rows.map((c) => (
              <Card key={c.fdc_id ?? c.description} withBorder padding="sm">
                <Group justify="space-between" wrap="nowrap" align="flex-start">
                  <div style={{ minWidth: 0 }}>
                    <Text fw={500} lineClamp={2}>
                      {c.description ?? "Unnamed food"}
                    </Text>
                    <Text size="xs">
                      Source: USDA FoodData Central · Unverified · FDC ID: {c.fdc_id ?? "unavailable"}
                    </Text>
                    <Group gap={6} mt={4}>
                      {c.data_type ? (
                        <Badge size="xs" variant="light" color="gray">
                          {c.data_type}
                        </Badge>
                      ) : null}
                      <Text size="xs" c="dimmed">
                        {num(c.calories)} kcal · P {num(c.protein_g)} · F {num(c.fat_g)} · C {num(c.carbs_g)}
                      </Text>
                    </Group>
                  </div>
                  <Button
                    size="compact-sm"
                    variant="light"
                    loading={apply.isPending && apply.variables?.fdcId === c.fdc_id}
                    disabled={c.fdc_id == null}
                    onClick={() => choose(c.fdc_id)}
                    style={{ flexShrink: 0 }}
                  >
                    Use this
                  </Button>
                </Group>
              </Card>
            ))}
          </Stack>
        </ScrollArea.Autosize>
      </Stack>
    </Modal>
  );
}
