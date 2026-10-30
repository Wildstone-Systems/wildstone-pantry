import { Alert, Badge, Button, Card, Group, Loader, Stack, Table, Text, Title } from "@mantine/core";
import { useState } from "react";
import { Link } from "react-router-dom";

import { useAppState } from "@/state/AppStateProvider";
import { ROUTES } from "@/routes/paths";

import { EditItemModal } from "../components/EditItemModal";
import { EstimatedBadge } from "../components/EstimatedBadge";
import { useDataQuality, useItems, type Item } from "../hooks/catalog-hooks";

/** Stored nutrition values that disagree with themselves.
 *
 * The counterpart to the conversion queue: that lists what is *missing*, this
 * lists what is *present and doubtful*. Both are needed. Twenty bad nutrition
 * estimates once sat in this catalogue fully populated and completely
 * invisible, because nothing ever looked at a value again once it was written.
 *
 * Every check behind this page is arithmetic — no provider, no model — so it
 * cannot itself be miscalibrated, and it costs nothing to run.
 */
export function DataQualityPage() {
  const { setStatus } = useAppState();
  const quality = useDataQuality();
  const rows = quality.data?.items ?? [];
  // Fixing a value is the whole point of the page, so the editor opens HERE.
  // Sending someone to the catalogue to search for the row they were just
  // looking at loses the finding and the place in the list.
  const allItems = useItems(true);
  const [editing, setEditing] = useState<Item | null>(null);

  return (
    <Stack>
      <Group justify="space-between" align="flex-start" wrap="wrap">
        <div>
          <Title order={2}>Nutrition data quality</Title>
          <Text c="dimmed" size="sm" mt={4}>
            Items whose stored numbers don&apos;t add up. Checked with arithmetic alone — nothing
            here was asked of a model or a provider.
          </Text>
        </div>
        <Group>
          <Button variant="default" component={Link} to={ROUTES.settings}>
            Back
          </Button>
          <Button variant="light" component={Link} to={ROUTES.reviewConversions}>
            Conversions
          </Button>
        </Group>
      </Group>

      {quality.isLoading && <Loader />}

      {quality.data && rows.length === 0 && (
        <Card withBorder padding="lg">
          <Text>Nothing looks wrong — every stored nutrition value is internally consistent.</Text>
        </Card>
      )}

      {quality.data && rows.length > 0 && (
        <Group gap="xs">
          {quality.data.impossible > 0 && (
            <Badge color="red" variant="light">
              {quality.data.impossible} impossible
            </Badge>
          )}
          <Badge color="yellow" variant="light">
            {quality.data.suspect} worth a look
          </Badge>
          <Text size="xs" c="dimmed">
            New writes with impossible values are now refused outright; these predate that.
          </Text>
        </Group>
      )}

      {rows.length > 0 && (
        <Table.ScrollContainer minWidth={720}>
          <Table striped highlightOnHover withTableBorder>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Item</Table.Th>
                <Table.Th w={190} style={{ whiteSpace: "nowrap" }}>
                  Stored
                </Table.Th>
                <Table.Th>What&apos;s wrong</Table.Th>
                <Table.Th w={110} style={{ whiteSpace: "nowrap" }}>
                  Fix
                </Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {rows.map((row) => {
                const worst = row.issues.some((i) => i.severity === "impossible")
                  ? "impossible"
                  : "suspect";
                return (
                  <Table.Tr key={row.item_id}>
                    <Table.Td>
                      <Group gap={6} wrap="nowrap">
                        <Text size="sm" truncate title={row.name}>
                          {row.name}
                        </Text>
                        {/* Provenance sits next to the finding: "this looks
                            wrong" is only actionable beside where it came from. */}
                        <EstimatedBadge estimated={row.estimated} source={row.source} />
                      </Group>
                      {row.brand && (
                        <Text size="xs" c="dimmed">
                          {row.brand}
                        </Text>
                      )}
                    </Table.Td>
                    <Table.Td>
                      <Text size="xs" c="dimmed">
                        {row.energy_kcal_100g ?? "—"} kcal · P {row.protein_g_100g ?? "—"} · F{" "}
                        {row.fat_g_100g ?? "—"} · C {row.carbs_g_100g ?? "—"}
                        <br />
                        per 100 g
                      </Text>
                    </Table.Td>
                    <Table.Td>
                      <Stack gap={2}>
                        {row.issues.map((issue, i) => (
                          <Group key={i} gap={6} wrap="nowrap" align="baseline">
                            <Badge
                              size="xs"
                              variant="light"
                              color={issue.severity === "impossible" ? "red" : "yellow"}
                              style={{ flexShrink: 0 }}
                            >
                              {issue.field}
                            </Badge>
                            <Text size="xs">{issue.message}</Text>
                          </Group>
                        ))}
                      </Stack>
                    </Table.Td>
                    <Table.Td>
                      <Button
                        size="compact-sm"
                        variant="light"
                        color={worst === "impossible" ? "red" : undefined}
                        aria-label={`Edit ${row.name}`}
                        onClick={() => {
                          const found = (allItems.data ?? []).find((i) => i.id === row.item_id);
                          if (found) setEditing(found);
                          else setStatus(`Couldn't open ${row.name}`);
                        }}
                      >
                        Fix
                      </Button>
                    </Table.Td>
                  </Table.Tr>
                );
              })}
            </Table.Tbody>
          </Table>
        </Table.ScrollContainer>
      )}

      {quality.isError && <Alert color="red">Couldn&apos;t load the data-quality check.</Alert>}

      <EditItemModal
        item={editing}
        onClose={() => setEditing(null)}
        onSaved={(saved) => {
          setStatus(`Updated ${saved.name}`);
          // Re-run the audit: a corrected value should drop off the list
          // immediately, so the queue visibly shortens as you work.
          quality.refetch();
        }}
      />
    </Stack>
  );
}
