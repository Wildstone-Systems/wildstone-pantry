import { Badge, Button, Card, Group, Stack, Switch, Table, Text, TextInput, Title } from "@mantine/core";
import { useMediaQuery } from "@mantine/hooks";
import { useMemo, useState } from "react";
import { Link } from "react-router-dom";

import { nutritionReferenceSourceLabel, nutritionReferenceStatusLabel } from "../nutrition-reference-labels";

import { getApiErrorMessage } from "@/api/errors";
import { useAppState } from "@/state/AppStateProvider";

import { EditItemModal } from "../components/EditItemModal";
import { EstimatedBadge } from "../components/EstimatedBadge";
import { EditLocationModal } from "../components/EditLocationModal";
import { PurgeItemModal } from "../components/PurgeItemModal";
import {
  rescanItemById,
  useItems,
  useLocations,
  useRescanItem,
  useSetItemActive,
  useSetLocationActive,
  type Item,
  type ItemRescanResult,
  type Location,
} from "../hooks/catalog-hooks";
import { ROUTES } from "../../../routes/paths";

/** Turn a rescan outcome into a short, human status line. */
function describeRescan(name: string, r: ItemRescanResult): string {
  if (
    r.reference_review &&
    !["verified", "human_selected", "label_exact", "manual"].includes(r.reference_review.status)
  ) {
    const review = r.reference_review;
    return `${name}: ${nutritionReferenceStatusLabel(review.status)} (${nutritionReferenceSourceLabel(review.source)}${review.description ? `: ${review.description}` : ""}) — ${review.why}. Review nutrition in Edit item.`;
  }
  if (r.status === "no_barcode") return `${name}: no barcode, and no USDA match by name`;
  if (r.status === "not_found") return `${name}: not found in Open Food Facts`;
  if (r.status === "rate_limited") return `${name}: a provider is throttling — try again shortly`;
  if (r.status === "no_change") return `${name}: already complete`;
  const via = r.estimated
    ? ` (estimated from USDA${r.estimated_source ? `: ${r.estimated_source}` : " by name"} — check it's right)`
    : "";
  return `${name}: added ${r.filled.join(", ")}${via}`;
}

// Open Food Facts rate-limits the product API (~100/min, and bursts hard), so
// the whole-catalog rescan runs one at a time, spaced out, and backs off when
// it does get throttled. Anything faster reliably trips 429s and the items
// come back empty.
const BULK_SPACING_MS = 700;
const BULK_BACKOFF_MS = 5000;

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

export function CatalogPage() {
  const { setStatus } = useAppState();

  const [showArchivedItems, setShowArchivedItems] = useState(false);
  // Purge is only offered for items already archived — archiving first is the
  // safety ladder, so a permanent delete is never one click from the main list.
  const [purgingItem, setPurgingItem] = useState<Item | null>(null);
  const [showArchivedLocations, setShowArchivedLocations] = useState(false);

  const itemsQuery = useItems(showArchivedItems);
  const locationsQuery = useLocations(showArchivedLocations);
  const setItemActive = useSetItemActive();
  const setLocationActive = useSetLocationActive();
  const rescanItem = useRescanItem();

  const items = itemsQuery.data ?? [];
  const locations = locationsQuery.data ?? [];

  const [itemQuery, setItemQuery] = useState("");
  const [locationQuery, setLocationQuery] = useState("");

  const [editingItem, setEditingItem] = useState<Item | null>(null);
  const [editingLocation, setEditingLocation] = useState<Location | null>(null);

  const [bulkProgress, setBulkProgress] = useState<{ done: number; total: number } | null>(null);

  const rescanOne = async (item: Item) => {
    try {
      const result = await rescanItem.mutateAsync(item.id);
      setStatus(describeRescan(item.name, result));
    } catch (err) {
      setStatus(getApiErrorMessage(err, `Could not rescan ${item.name}`));
    }
  };

  const rescanCatalog = async () => {
    const withBarcode = items.filter((it) => (it.barcode ?? "").trim());
    if (withBarcode.length === 0) {
      setStatus("No items with a barcode to rescan.");
      return;
    }
    setBulkProgress({ done: 0, total: withBarcode.length });
    let updated = 0;
    let rateLimited = 0;
    let needsReview = 0;

    // Serial + throttled: OFF will 429 a burst and return the items empty.
    for (let i = 0; i < withBarcode.length; i += 1) {
      try {
        const r = await rescanItemById(withBarcode[i].id);
        if (r.status === "updated") updated += 1;
        if (
          r.reference_review &&
          !["verified", "human_selected", "label_exact", "manual"].includes(r.reference_review.status)
        )
          needsReview += 1;
        if (r.status === "rate_limited") {
          rateLimited += 1;
          await sleep(BULK_BACKOFF_MS); // throttled — ease off before continuing
        }
      } catch {
        // Skip a failed item; the summary still reflects what succeeded.
      }
      setBulkProgress({ done: i + 1, total: withBarcode.length });
      if (i < withBarcode.length - 1) await sleep(BULK_SPACING_MS);
    }

    setBulkProgress(null);
    await itemsQuery.refetch();
    setStatus(
      `Rescanned ${withBarcode.length} item(s); updated ${updated}` +
        (needsReview ? `, ${needsReview} nutrition reference(s) need review in Edit item` : "") +
        (rateLimited ? `, ${rateLimited} throttled — run again later to finish those.` : ".")
    );
  };

  const filteredItems = useMemo(() => {
    const q = itemQuery.trim().toLowerCase();
    if (!q) return items;
    return items.filter((item) => `${item.name} ${item.brand ?? ""} ${item.barcode ?? ""}`.toLowerCase().includes(q));
  }, [items, itemQuery]);

  const filteredLocations = useMemo(() => {
    const q = locationQuery.trim().toLowerCase();
    if (!q) return locations;
    return locations.filter((loc) => `${loc.name} ${loc.description ?? ""}`.toLowerCase().includes(q));
  }, [locations, locationQuery]);

  const toggleItemActive = async (item: Item) => {
    try {
      await setItemActive.mutateAsync({ id: item.id, is_active: !item.is_active });
      setStatus(item.is_active ? `Archived item ${item.name}` : `Reactivated item ${item.name}`);
    } catch (err) {
      setStatus(getApiErrorMessage(err, "Could not update item"));
    }
  };

  const toggleLocationActive = async (loc: Location) => {
    try {
      await setLocationActive.mutateAsync({ id: loc.id, is_active: !loc.is_active });
      setStatus(loc.is_active ? `Archived location ${loc.name}` : `Reactivated location ${loc.name}`);
    } catch (err) {
      setStatus(getApiErrorMessage(err, "Could not update location"));
    }
  };

  // Below this width the five-column table can't fit a phone, so we stack each
  // row into its own card instead of forcing a horizontal scroll (the reported
  // issue). matchMedia is falsy under test, so the table layout is what tests see.
  const isMobile = useMediaQuery("(max-width: 48em)");

  const statusBadge = (active: boolean) => (
    <Badge color={active ? "green" : "gray"} variant="light">
      {active ? "Active" : "Archived"}
    </Badge>
  );

  const itemActions = (item: Item) => (
    <Group gap="xs" wrap="nowrap">
      <Button size="compact-sm" variant="light" onClick={() => setEditingItem(item)} aria-label={`Edit ${item.name}`}>
        Edit
      </Button>
      <Button
        size="compact-sm"
        variant="subtle"
        loading={rescanItem.isPending && rescanItem.variables === item.id}
        disabled={bulkProgress !== null}
        onClick={() => rescanOne(item)}
        aria-label={`Rescan ${item.name}`}
        title={
          (item.barcode ?? "").trim()
            ? "Refresh from Open Food Facts"
            : "No barcode — try to estimate nutrition from USDA by name"
        }
      >
        Rescan
      </Button>
      <Button
        size="compact-sm"
        variant="light"
        color={item.is_active ? "red" : "green"}
        loading={setItemActive.isPending}
        onClick={() => toggleItemActive(item)}
        aria-label={`${item.is_active ? "Archive" : "Reactivate"} ${item.name}`}
      >
        {item.is_active ? "Archive" : "Reactivate"}
      </Button>
      {!item.is_active && (
        <Button
          size="compact-sm"
          variant="subtle"
          color="red"
          onClick={() => setPurgingItem(item)}
          aria-label={`Permanently delete ${item.name}`}
        >
          Delete forever
        </Button>
      )}
    </Group>
  );

  const locationActions = (loc: Location) => (
    <Group gap="xs" wrap="nowrap">
      <Button size="compact-sm" variant="light" onClick={() => setEditingLocation(loc)} aria-label={`Edit ${loc.name}`}>
        Edit
      </Button>
      <Button
        size="compact-sm"
        variant="light"
        color={loc.is_active ? "red" : "green"}
        loading={setLocationActive.isPending}
        onClick={() => toggleLocationActive(loc)}
        aria-label={`${loc.is_active ? "Archive" : "Reactivate"} ${loc.name}`}
      >
        {loc.is_active ? "Archive" : "Reactivate"}
      </Button>
    </Group>
  );

  return (
    <Stack>
      <Group justify="space-between" align="flex-start" wrap="wrap">
        <Stack gap={2}>
          <Title order={2}>Catalog</Title>
          <Text size="sm" c="dimmed">
            Add and manage items and locations. Archiving hides an entry without deleting it, so recipes and history
            keep working — reactivate it any time.
          </Text>
        </Stack>
        <Group wrap="wrap">
          <Button
            variant="light"
            loading={bulkProgress !== null}
            onClick={rescanCatalog}
            aria-label="Rescan whole catalog against Open Food Facts"
          >
            {bulkProgress ? `Rescanning ${bulkProgress.done}/${bulkProgress.total}…` : "Rescan catalog"}
          </Button>
          <Button component={Link} to={ROUTES.catalogAddItem} variant="default">
            Add Item
          </Button>
          <Button component={Link} to={ROUTES.catalogAddLocation} variant="default">
            Add Location
          </Button>
        </Group>
      </Group>

      <Card withBorder>
        <Group justify="space-between" align="flex-end" wrap="wrap" mb="xs">
          <Title order={3}>
            Items ({filteredItems.length}
            {itemQuery.trim() ? ` of ${items.length}` : ""})
          </Title>
          <Group wrap="wrap">
            <Switch
              label="Show archived"
              checked={showArchivedItems}
              onChange={(e) => setShowArchivedItems(e.currentTarget.checked)}
            />
            <TextInput
              value={itemQuery}
              onChange={(e) => setItemQuery(e.currentTarget.value)}
              placeholder="Search items"
              aria-label="Search items"
              w={isMobile ? "100%" : 240}
            />
            <Button variant="default" onClick={() => setItemQuery("")} disabled={!itemQuery.trim()}>
              Clear
            </Button>
            <Button variant="default" onClick={() => itemsQuery.refetch()} loading={itemsQuery.isFetching}>
              Refresh
            </Button>
          </Group>
        </Group>

        {isMobile ? (
          <Stack gap="xs">
            {filteredItems.map((item) => (
              <Card key={item.id} withBorder padding="sm" opacity={item.is_active ? undefined : 0.6}>
                <Group justify="space-between" wrap="nowrap" align="flex-start">
                  <div style={{ minWidth: 0 }}>
                    <Group gap={6} wrap="nowrap" align="baseline">
                      <Text fw={500} lineClamp={2}>
                        {item.name}
                      </Text>
                      <EstimatedBadge estimated={item.nutrition_estimated} source={item.nutrition_source} />
                    </Group>
                    <Text size="xs" c="dimmed">
                      {item.brand ?? "no brand"}
                      {(item.barcode ?? "").trim() ? ` · ${item.barcode}` : ""}
                    </Text>
                  </div>
                  {statusBadge(item.is_active)}
                </Group>
                <Group mt="sm">{itemActions(item)}</Group>
              </Card>
            ))}
            {filteredItems.length === 0 ? (
              <Text size="sm" c="dimmed">
                {items.length === 0 ? "No items yet." : "No matching items."}
              </Text>
            ) : null}
          </Stack>
        ) : (
          <Table.ScrollContainer minWidth={760}>
            {/* tableLayout:fixed so the sized columns below hold and Name takes
                the remainder — without it the browser widens Name to fit the
                longest product title and squeezes Actions into wrapping. */}
            <Table striped highlightOnHover withTableBorder style={{ tableLayout: "fixed" }}>
              <Table.Thead>
                <Table.Tr>
                  <Table.Th>Name</Table.Th>
                  <Table.Th w={150}>Brand</Table.Th>
                  <Table.Th w={130}>Barcode</Table.Th>
                  <Table.Th w={90}>Status</Table.Th>
                  <Table.Th w={230}>Actions</Table.Th>
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {filteredItems.map((item) => (
                  <Table.Tr key={item.id} opacity={item.is_active ? undefined : 0.6}>
                    {/* title= so the full value is still reachable on hover
                        once it's clipped. */}
                    <Table.Td>
                      {/* An estimated value must be distinguishable from a
                          measured one wherever it is read, not only inside the
                          edit modal — otherwise nobody ever questions it. */}
                      <Group gap={6} wrap="nowrap">
                        <Text size="sm" truncate title={item.name}>
                          {item.name}
                        </Text>
                        <EstimatedBadge estimated={item.nutrition_estimated} source={item.nutrition_source} />
                      </Group>
                    </Table.Td>
                    <Table.Td>
                      <Text size="sm" truncate title={item.brand ?? undefined}>
                        {item.brand ?? "-"}
                      </Text>
                    </Table.Td>
                    <Table.Td>
                      <Text size="sm" truncate title={item.barcode ?? undefined}>
                        {item.barcode ?? "-"}
                      </Text>
                    </Table.Td>
                    <Table.Td>{statusBadge(item.is_active)}</Table.Td>
                    <Table.Td>{itemActions(item)}</Table.Td>
                  </Table.Tr>
                ))}
                {filteredItems.length === 0 ? (
                  <Table.Tr>
                    <Table.Td colSpan={5}>
                      <Text size="sm" c="dimmed">
                        {items.length === 0 ? "No items yet." : "No matching items."}
                      </Text>
                    </Table.Td>
                  </Table.Tr>
                ) : null}
              </Table.Tbody>
            </Table>
          </Table.ScrollContainer>
        )}
      </Card>

      <Card withBorder>
        <Group justify="space-between" align="flex-end" wrap="wrap" mb="xs">
          <Title order={3}>
            Locations ({filteredLocations.length}
            {locationQuery.trim() ? ` of ${locations.length}` : ""})
          </Title>
          <Group wrap="wrap">
            <Switch
              label="Show archived"
              checked={showArchivedLocations}
              onChange={(e) => setShowArchivedLocations(e.currentTarget.checked)}
            />
            <TextInput
              value={locationQuery}
              onChange={(e) => setLocationQuery(e.currentTarget.value)}
              placeholder="Search locations"
              aria-label="Search locations"
              w={isMobile ? "100%" : 240}
            />
            <Button variant="default" onClick={() => setLocationQuery("")} disabled={!locationQuery.trim()}>
              Clear
            </Button>
            <Button variant="default" onClick={() => locationsQuery.refetch()} loading={locationsQuery.isFetching}>
              Refresh
            </Button>
          </Group>
        </Group>

        {isMobile ? (
          <Stack gap="xs">
            {filteredLocations.map((loc) => (
              <Card key={loc.id} withBorder padding="sm" opacity={loc.is_active ? undefined : 0.6}>
                <Group justify="space-between" wrap="nowrap" align="flex-start">
                  <div style={{ minWidth: 0 }}>
                    <Text fw={500} lineClamp={2}>
                      {loc.name}
                    </Text>
                    <Text size="xs" c="dimmed">
                      {loc.description ?? "no description"}
                    </Text>
                  </div>
                  {statusBadge(loc.is_active)}
                </Group>
                <Group mt="sm">{locationActions(loc)}</Group>
              </Card>
            ))}
            {filteredLocations.length === 0 ? (
              <Text size="sm" c="dimmed">
                {locations.length === 0 ? "No locations yet." : "No matching locations."}
              </Text>
            ) : null}
          </Stack>
        ) : (
          <Table.ScrollContainer minWidth={620}>
            <Table striped highlightOnHover withTableBorder style={{ tableLayout: "fixed" }}>
              <Table.Thead>
                <Table.Tr>
                  <Table.Th>Name</Table.Th>
                  <Table.Th>Description</Table.Th>
                  <Table.Th w={90}>Status</Table.Th>
                  <Table.Th w={190}>Actions</Table.Th>
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {filteredLocations.map((loc) => (
                  <Table.Tr key={loc.id} opacity={loc.is_active ? undefined : 0.6}>
                    <Table.Td>
                      <Text size="sm" truncate title={loc.name}>
                        {loc.name}
                      </Text>
                    </Table.Td>
                    <Table.Td>
                      <Text size="sm" truncate title={loc.description ?? undefined}>
                        {loc.description ?? "-"}
                      </Text>
                    </Table.Td>
                    <Table.Td>{statusBadge(loc.is_active)}</Table.Td>
                    <Table.Td>{locationActions(loc)}</Table.Td>
                  </Table.Tr>
                ))}
                {filteredLocations.length === 0 ? (
                  <Table.Tr>
                    <Table.Td colSpan={4}>
                      <Text size="sm" c="dimmed">
                        {locations.length === 0 ? "No locations yet." : "No matching locations."}
                      </Text>
                    </Table.Td>
                  </Table.Tr>
                ) : null}
              </Table.Tbody>
            </Table>
          </Table.ScrollContainer>
        )}
      </Card>

      <EditItemModal
        item={editingItem}
        onClose={() => setEditingItem(null)}
        onSaved={(saved) => setStatus(`Updated item ${saved.name}`)}
      />
      <EditLocationModal
        location={editingLocation}
        onClose={() => setEditingLocation(null)}
        onSaved={(saved) => setStatus(`Updated location ${saved.name}`)}
      />
      <PurgeItemModal item={purgingItem} onClose={() => setPurgingItem(null)} />
    </Stack>
  );
}
