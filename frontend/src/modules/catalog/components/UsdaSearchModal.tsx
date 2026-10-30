import { Badge, Button, Card, Group, Loader, Modal, ScrollArea, Stack, Text, TextInput } from "@mantine/core";
import { useDebouncedValue } from "@mantine/hooks";
import { useEffect, useState } from "react";

import { type NutritionCandidate, useSearchNutrition } from "../hooks/catalog-hooks";

const num = (v: number | null) => (v === null || v === undefined ? "—" : Math.round(v));

/** Search USDA for a food to seed a NEW item's nutrition, before it has an id.
 *  Item-agnostic sibling of `UsdaMatchModal`: same search-and-pick UI, but
 *  picking just hands the candidate (and the query it came from) back to the
 *  caller instead of POSTing to an item that doesn't exist yet — the create
 *  form applies the macros locally, then re-applies via the real USDA-match
 *  endpoint right after the item is created, so nutrition_source/estimated
 *  end up set exactly as if it had been matched from the Edit screen. */
export function UsdaSearchModal({
  seedQuery,
  opened,
  onClose,
  onPick,
}: {
  seedQuery: string;
  opened: boolean;
  onClose: () => void;
  onPick: (candidate: NutritionCandidate, query: string) => void;
}) {
  const [query, setQuery] = useState(seedQuery);
  const [debounced] = useDebouncedValue(query, 400);
  const candidates = useSearchNutrition(debounced, opened);
  const rows = candidates.data?.candidates ?? [];
  const throttled = candidates.data?.throttled ?? false;
  const searching = candidates.isFetching || query.trim() !== debounced.trim();

  // Re-seed the search box with the item name typed so far each time it opens.
  useEffect(() => {
    if (opened) setQuery(seedQuery);
  }, [opened, seedQuery]);

  return (
    <Modal opened={opened} onClose={onClose} title="Match nutrition — USDA food" size="lg">
      <Stack gap="sm">
        <Text size="sm">
          Search results are unverified candidates. Choosing “Use this” confirms your reference selection; nutrition
          values come from USDA.
        </Text>
        <TextInput
          label="Search USDA foods"
          description="Values are per 100 g. E.g. 'banana raw' or 'pork butt roast raw'."
          placeholder="e.g. banana raw"
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
                    disabled={c.fdc_id == null}
                    onClick={() => onPick(c, debounced)}
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
