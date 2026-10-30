import {
  Avatar,
  Badge,
  Box,
  Button,
  Card,
  Collapse,
  Group,
  Pagination,
  SegmentedControl,
  Select,
  Stack,
  Table,
  Text,
  TextInput,
  Title,
} from "@mantine/core";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ChevronDown, Plus, RefreshCw, ScanBarcode, Search } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";

import { api } from "@/api/client";
import { getApiErrorMessage } from "@/api/errors";
import { ActionConsole } from "@/components/ActionConsole";
import { LocationPill } from "@/components/LocationPill";
import { type Item, useItems, useLocations } from "@/modules/catalog";
import { ROUTES } from "@/routes/paths";
import { useAppState } from "@/state/AppStateProvider";

import { DayTag } from "../components/kiosk-ui";
import { SortableTh, applySort, toggleSort, type SortState } from "@/components/table-sort";

import { InventoryItemModal } from "../components/InventoryItemModal";
import { TransactionDetailModal } from "../components/TransactionDetailModal";
import type { Container, TransactionLine } from "../hooks/inventory-hooks";
import {
  type LotLine,
  useScopedContainers,
  useScopedInventory,
  useScopedLots,
  useScopedTransactions,
  useScopedWaste,
  type WasteLine,
  type WithLocation,
} from "../hooks/scoped-hooks";

type InventoryAction = "add" | "remove" | "adjust" | "move";
type InventoryTab = "onhand" | "lots" | "containers" | "waste" | "log";

const TAB_OPTIONS = [
  { value: "onhand", label: "On hand" },
  { value: "lots", label: "Lots & batches" },
  { value: "containers", label: "Containers" },
  { value: "waste", label: "Waste" },
  { value: "log", label: "Log" },
];

function parsePositiveNumber(value: string): number | null {
  const num = Number(value);
  if (!Number.isFinite(num) || num <= 0) return null;
  return num;
}

const PAGE_SIZE = 25;

const ONHAND_ACCESSORS: Record<string, (row: WithLocation<LotLine>) => string | number | null> = {
  item_name: (r) => r.item_name.toLowerCase(),
  location_name: (r) => r.location_name.toLowerCase(),
  quantity_remaining: (r) => Number(r.quantity_remaining),
  expiration_date: (r) => r.expiration_date,
};

const LOG_ACCESSORS: Record<string, (row: WithLocation<TransactionLine>) => string | number | null> = {
  created_at: (r) => new Date(r.created_at).getTime(),
  item_name: (r) => r.item_name.toLowerCase(),
  transaction_type: (r) => r.transaction_type,
  quantity_change: (r) => Number(r.quantity_change),
  reason: (r) => r.reason ?? "",
};

/** Trim trailing zeros: "1.000" → "1", "0.500" → "0.5". */
function fmtQty(value: string | number): string {
  const n = Number(value);
  return Number.isFinite(n) ? String(n) : String(value);
}

/** The package size to display for an item ("1 gal", "16 oz"), or "—". */
function itemSize(item: Item | undefined): string {
  if (!item) return "—";
  if (item.size_description) return item.size_description;
  if (item.size_quantity && item.size_unit) return `${item.size_quantity} ${item.size_unit}`;
  return "—";
}

function LocationBadge({ name }: { name: string }) {
  return (
    <Badge
      variant="light"
      color="gray"
      radius="xl"
      tt="none"
      fw={600}
      styles={{ root: { maxWidth: "none" }, label: { overflow: "visible" } }}
    >
      {name}
    </Badge>
  );
}

function StatusTag({ status, frozen }: { status: string; frozen: boolean }) {
  if (frozen) {
    return (
      <Badge color="sage" variant="light" radius="xl">
        frozen
      </Badge>
    );
  }
  if (status === "open") {
    return (
      <Badge color="terracotta" variant="light" radius="xl">
        open
      </Badge>
    );
  }
  return (
    <Badge color="gray" variant="light" radius="xl">
      {status || "sealed"}
    </Badge>
  );
}

export function InventoryPage() {
  const queryClient = useQueryClient();
  const { selectedLocationId, setStatus } = useAppState();
  const isAll = selectedLocationId === null;

  const { data: items } = useItems();
  const { data: locations } = useLocations();
  const locationList = useMemo(() => locations ?? [], [locations]);

  const [activeTab, setActiveTab] = useState<InventoryTab>("onhand");
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [editLot, setEditLot] = useState<WithLocation<LotLine> | null>(null);
  const [selectedTransaction, setSelectedTransaction] = useState<WithLocation<TransactionLine> | null>(null);
  const [onHandSort, setOnHandSort] = useState<SortState>(null);
  const [logSort, setLogSort] = useState<SortState>(null);

  // Write target: in All-locations mode the console/containers need an explicit
  // location; otherwise writes go to the selected location.
  // The location writes go to. Defaults to the location you're viewing (so it
  // follows the filter), but is ALWAYS shown and editable in the console so an
  // add can never silently land in the wrong place.
  const [writeLoc, setWriteLoc] = useState<string | null>(null);
  useEffect(() => {
    if (selectedLocationId) {
      setWriteLoc(selectedLocationId);
      return;
    }
    setWriteLoc((cur) => (cur && locationList.some((l) => l.id === cur) ? cur : (locationList[0]?.id ?? null)));
  }, [selectedLocationId, locationList]);
  const writeLocationId = writeLoc;

  const [selectedItemId, setSelectedItemId] = useState<string | null>(null);
  const [targetLocationId, setTargetLocationId] = useState<string | null>(null);
  const [quantity, setQuantity] = useState("1");
  const [unit, setUnit] = useState("each");
  const [expirationDate, setExpirationDate] = useState("");
  const [containerId, setContainerId] = useState<string | null>(null);
  const [newContainerName, setNewContainerName] = useState("");
  const [newContainerLoc, setNewContainerLoc] = useState<string | null>(null);

  useEffect(() => {
    if (selectedItemId) return;
    if (!items || items.length === 0) return;
    setSelectedItemId(items[0].id);
  }, [items, selectedItemId]);

  useEffect(() => {
    if (targetLocationId) return;
    if (locationList.length === 0) return;
    setTargetLocationId(locationList.length > 1 ? locationList[1].id : locationList[0].id);
  }, [locationList, targetLocationId]);

  // Container choices reset when the write location changes.
  useEffect(() => {
    setContainerId(null);
  }, [writeLocationId]);

  useEffect(() => {
    if (newContainerLoc && locationList.some((l) => l.id === newContainerLoc)) return;
    if (locationList.length > 0) setNewContainerLoc(writeLocationId ?? locationList[0].id);
  }, [locationList, newContainerLoc, writeLocationId]);

  const inventoryQuery = useScopedInventory(selectedLocationId);
  const transactionsQuery = useScopedTransactions(selectedLocationId, 50);
  const lotsQuery = useScopedLots(selectedLocationId);
  const wasteQuery = useScopedWaste(selectedLocationId);
  const containersQuery = useScopedContainers(selectedLocationId);
  // Containers for the write location power the console's container picker.
  const writeContainersQuery = useScopedContainers(writeLocationId);

  const inventory = inventoryQuery.data;
  const transactions = transactionsQuery.data;
  const lots = lotsQuery.data;
  const waste = wasteQuery.data;
  const containers = containersQuery.data;

  const locationTitle = isAll
    ? "All locations"
    : (locationList.find((loc) => loc.id === selectedLocationId)?.name ?? "Location");

  // Stat strip (aggregated across whatever is in scope).
  const itemsOnHand = inventory.length;
  const expiringWithin7 = useMemo(
    () => lots.filter((l) => l.days_until_expiration !== null && l.days_until_expiration <= 7).length,
    [lots],
  );
  const totalWasteEvents = useMemo(() => waste.reduce((acc, row) => acc + (row.events ?? 0), 0), [waste]);

  const term = search.trim().toLowerCase();
  const byName = (name: string) => term === "" || name.toLowerCase().includes(term);

  const containerOptions = useMemo(
    () => writeContainersQuery.data.map((c) => ({ value: c.id, label: c.name })),
    [writeContainersQuery.data],
  );
  const itemOptions = useMemo(() => (items ?? []).map((item) => ({ value: item.id, label: item.name })), [items]);
  const locationOptions = useMemo(() => locationList.map((loc) => ({ value: loc.id, label: loc.name })), [locationList]);
  const itemById = useMemo(() => new Map((items ?? []).map((i) => [i.id, i])), [items]);

  // Reset to the first page whenever the view, search, or location scope changes,
  // so search/filters always apply across the whole set (then paginate the result).
  useEffect(() => setPage(1), [search, activeTab, selectedLocationId]);

  // Load a row into the console (and pick a sensible unit) — makes the actions
  // panel "quick": click the milk row, adjust, hit Remove.
  const pickItem = (itemId: string | null, preferredUnit?: string) => {
    setSelectedItemId(itemId);
    if (!itemId) return;
    const it = itemById.get(itemId);
    setUnit(preferredUnit || it?.size_unit || "each");
  };

  const invalidateScoped = () => queryClient.invalidateQueries({ queryKey: ["scoped"] });

  const containerMutation = useMutation({
    mutationFn: async (action: { type: "create"; name: string; locationId: string } | { type: "delete"; id: string }) => {
      if (action.type === "create") {
        await api.post("/api/inventory/containers", { location_id: action.locationId, name: action.name });
      } else {
        await api.delete(`/api/inventory/containers/${action.id}`);
      }
    },
    onSuccess: () => {
      setNewContainerName("");
      void Promise.all([
        queryClient.invalidateQueries({ queryKey: ["containers"] }),
        queryClient.invalidateQueries({ queryKey: ["lots"] }),
        invalidateScoped(),
      ]);
    },
    onError: (error) => setStatus(getApiErrorMessage(error, "Container action failed")),
  });

  const lotAction = useMutation({
    mutationFn: async (args: { path: string; body?: Record<string, unknown> }) => {
      await api.post(`/api/inventory/${args.path}`, args.body ?? {});
    },
    onSuccess: () => {
      setStatus("Lot updated");
      void Promise.all([
        queryClient.invalidateQueries({ queryKey: ["lots"] }),
        queryClient.invalidateQueries({ queryKey: ["inventory"] }),
        queryClient.invalidateQueries({ queryKey: ["waste"] }),
        queryClient.invalidateQueries({ queryKey: ["transactions"] }),
        queryClient.invalidateQueries({ queryKey: ["expiringItems"] }),
        invalidateScoped(),
      ]);
    },
    onError: (error) => setStatus(getApiErrorMessage(error, "Lot action failed")),
  });

  const mutation = useMutation({
    mutationFn: async (action: InventoryAction) => {
      if (!selectedItemId) throw new Error("Select an item first");
      if (!writeLocationId) throw new Error("Select a location to write to");

      const quantityNum = action === "adjust" ? Number(quantity) : parsePositiveNumber(quantity);
      if (!Number.isFinite(quantityNum as number) || (action !== "adjust" && (quantityNum as number) <= 0)) {
        throw new Error(action === "adjust" ? "Quantity must be a number" : "Quantity must be > 0");
      }

      const payload: Record<string, unknown> = {
        item_id: selectedItemId,
        unit: unit.trim() || "each",
        reason: "manual",
        note: null,
      };

      if (action === "move") {
        if (!targetLocationId) throw new Error("Select a target location to move inventory");
        payload.source_location_id = writeLocationId;
        payload.target_location_id = targetLocationId;
        payload.quantity = quantityNum;
      } else {
        payload.location_id = writeLocationId;
        payload.quantity = quantityNum;
      }

      if (action === "add" || action === "adjust") {
        payload.expiration_date = expirationDate || null;
      }
      if (action === "add" && containerId) {
        payload.container_id = containerId;
      }

      await api.post(`/api/inventory/${action}`, payload);
    },
    onSuccess: () => {
      setStatus("Inventory updated");
      void Promise.all([
        queryClient.invalidateQueries({ queryKey: ["inventory"] }),
        queryClient.invalidateQueries({ queryKey: ["lots"] }),
        queryClient.invalidateQueries({ queryKey: ["transactions"] }),
        queryClient.invalidateQueries({ queryKey: ["consumptionTrends"] }),
        queryClient.invalidateQueries({ queryKey: ["restockSuggestions"] }),
        queryClient.invalidateQueries({ queryKey: ["expiringItems"] }),
        queryClient.invalidateQueries({ queryKey: ["recipeSuggestions"] }),
        invalidateScoped(),
      ]);
    },
    onError: (error) => {
      setStatus(getApiErrorMessage(error, "Inventory update failed"));
    },
  });

  const refreshAll = () => {
    inventoryQuery.refetch();
    transactionsQuery.refetch();
    lotsQuery.refetch();
    wasteQuery.refetch();
    containersQuery.refetch();
  };

  // Filter across the WHOLE set (so search/filters are complete), then paginate.
  const filterByName = <T extends { item_name: string }>(list: T[]): T[] => {
    const t = search.trim().toLowerCase();
    return t === "" ? list : list.filter((r) => r.item_name.toLowerCase().includes(t));
  };
  // On-hand and Lots are both per-lot now (On-hand = the friendly columns, Lots =
  // the technical/container view) — so different-dated lots of an item show
  // separately and each is editable.
  const lotsFiltered = useMemo(() => filterByName(lots), [lots, search]);
  const wasteFiltered = useMemo(() => filterByName(waste), [waste, search]);
  const logFiltered = useMemo(() => filterByName(transactions), [transactions, search]);

  // Sorting is client-side over the already-fetched/filtered rows and only
  // reorders — it never changes which rows are in scope, so counts/paging
  // below are unaffected. Default (no header clicked) leaves the fetched
  // order untouched, which for the log is already newest-first.
  const onHandRows = useMemo(() => applySort(lotsFiltered, onHandSort, ONHAND_ACCESSORS), [lotsFiltered, onHandSort]);
  const logRows = useMemo(() => applySort(logFiltered, logSort, LOG_ACCESSORS), [logFiltered, logSort]);

  const filteredCount =
    activeTab === "onhand" || activeTab === "lots"
      ? lotsFiltered.length
      : activeTab === "waste"
        ? wasteFiltered.length
        : activeTab === "log"
          ? logFiltered.length
          : 0;
  const pageCount = Math.max(1, Math.ceil(filteredCount / PAGE_SIZE));
  const start = (page - 1) * PAGE_SIZE;
  const pageOf = <T,>(list: T[]): T[] => list.slice(start, start + PAGE_SIZE);
  useEffect(() => setPage((p) => Math.min(p, pageCount)), [pageCount]);

  const consoleNode = (
    <ActionConsole>
      <Stack gap="md" p="lg">
        <Group gap={8} wrap="nowrap">
          <Plus size={20} color="var(--pantry-accent)" strokeWidth={2.6} />
          <Text style={{ fontFamily: "var(--mantine-heading-font-family)", fontSize: 19 }}>Inventory actions</Text>
        </Group>

        {/* Always visible so an Add can never silently go to the wrong location. */}
        <Select
          label="Adding to"
          description="Where Add / Adjust / Move-from apply"
          data={locationOptions}
          value={writeLocationId}
          onChange={setWriteLoc}
          allowDeselect={false}
          comboboxProps={{ radius: "lg" }}
          styles={{ input: { fontWeight: 600 } }}
        />

        <Button
          component={Link}
          to={ROUTES.scan}
          variant="default"
          leftSection={<ScanBarcode size={18} color="var(--pantry-accent)" strokeWidth={2.6} />}
          style={{ minHeight: 48 }}
        >
          Scan a barcode
        </Button>

        <Select
          label="Item"
          description="Tip: click a row to edit it or set a best-by date"
          data={itemOptions}
          value={selectedItemId}
          onChange={(v) => pickItem(v)}
          searchable
          nothingFoundMessage="No items"
          allowDeselect={false}
          comboboxProps={{ radius: "lg" }}
        />
        <Group grow>
          <TextInput label="Quantity" value={quantity} onChange={(e) => setQuantity(e.currentTarget.value)} type="number" min={0} step="0.001" />
          <TextInput label="Unit" value={unit} onChange={(e) => setUnit(e.currentTarget.value)} />
        </Group>

        <Button
          variant="subtle"
          size="compact-sm"
          justify="flex-start"
          px={4}
          onClick={() => setShowAdvanced((v) => !v)}
          rightSection={<ChevronDown size={15} style={{ transform: showAdvanced ? "rotate(180deg)" : "none", transition: "transform 120ms" }} />}
        >
          {showAdvanced ? "Fewer options" : "More options — container, expiry, move"}
        </Button>
        <Collapse in={showAdvanced}>
          <Stack gap="md">
            <Group grow>
              <Select
                label="Container"
                placeholder="Loose in location"
                data={containerOptions}
                value={containerId}
                onChange={setContainerId}
                clearable
                searchable
                comboboxProps={{ radius: "lg" }}
              />
              <TextInput label="Expiration" value={expirationDate} onChange={(e) => setExpirationDate(e.currentTarget.value)} type="date" />
            </Group>
            <Select
              label="Move to"
              data={locationOptions}
              value={targetLocationId}
              onChange={setTargetLocationId}
              allowDeselect={false}
              comboboxProps={{ radius: "lg" }}
            />
          </Stack>
        </Collapse>

        <Box style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 }}>
          <Button style={{ gridColumn: "1 / 3", minHeight: 50 }} loading={mutation.isPending} onClick={() => mutation.mutate("add")}>
            Add
          </Button>
          <Button variant="default" style={{ minHeight: 46 }} loading={mutation.isPending} onClick={() => mutation.mutate("remove")}>
            Remove
          </Button>
          <Button variant="default" style={{ minHeight: 46 }} loading={mutation.isPending} onClick={() => mutation.mutate("adjust")}>
            Adjust
          </Button>
          <Button variant="default" style={{ gridColumn: "1 / 3", minHeight: 46 }} loading={mutation.isPending} onClick={() => mutation.mutate("move")}>
            Move
          </Button>
        </Box>
      </Stack>
    </ActionConsole>
  );

  return (
    <Box>
      <Group justify="space-between" align="flex-end" mb="md" wrap="wrap">
        <Stack gap={2}>
          <Text size="xs" c="dimmed">
            Inventory
          </Text>
          <Title order={2}>{locationTitle}</Title>
        </Stack>
        <LocationPill />
      </Group>

      <Group gap="sm" mb="md" grow>
        <Card padding="md">
          <Text style={{ fontFamily: "var(--mantine-heading-font-family)", fontSize: 26, lineHeight: 1 }}>{itemsOnHand}</Text>
          <Text size="xs" c="dimmed" mt={3}>
            items on hand
          </Text>
        </Card>
        <Card padding="md" style={{ background: "var(--pantry-accent-100)" }}>
          <Text style={{ fontFamily: "var(--mantine-heading-font-family)", fontSize: 26, lineHeight: 1, color: "var(--pantry-accent-800)" }}>
            {expiringWithin7}
          </Text>
          <Text size="xs" mt={3} style={{ color: "var(--pantry-accent-700)" }}>
            expiring ≤ 7 days
          </Text>
        </Card>
        <Card padding="md">
          <Text style={{ fontFamily: "var(--mantine-heading-font-family)", fontSize: 26, lineHeight: 1 }}>{totalWasteEvents}</Text>
          <Text size="xs" c="dimmed" mt={3}>
            waste events · all time
          </Text>
        </Card>
      </Group>

      <Group justify="space-between" mb="sm" wrap="wrap" gap="sm">
        <SegmentedControl
          value={activeTab}
          onChange={(value) => setActiveTab(value as InventoryTab)}
          data={TAB_OPTIONS}
          style={{ background: "var(--pantry-surface)" }}
        />
        <Group gap="sm">
          <TextInput
            placeholder="Search items"
            value={search}
            onChange={(e) => setSearch(e.currentTarget.value)}
            leftSection={<Search size={15} strokeWidth={2.4} />}
            w={220}
          />
          <Button
            variant="default"
            leftSection={<RefreshCw size={16} strokeWidth={2.6} />}
            onClick={refreshAll}
            loading={inventoryQuery.isFetching || lotsQuery.isFetching || transactionsQuery.isFetching || wasteQuery.isFetching}
          >
            Refresh
          </Button>
        </Group>
      </Group>

      <Card padding="md">
        {activeTab === "containers" ? (
          <ContainersView
            containers={containers.filter((c) => byName(c.name))}
            showLocation={isAll}
            newContainerName={newContainerName}
            setNewContainerName={setNewContainerName}
            locationOptions={locationOptions}
            newContainerLoc={newContainerLoc}
            setNewContainerLoc={setNewContainerLoc}
            pickLocation={isAll}
            pending={containerMutation.isPending}
            onCreate={() =>
              containerMutation.mutate({
                type: "create",
                name: newContainerName.trim(),
                locationId: (isAll ? newContainerLoc : selectedLocationId) ?? "",
              })
            }
            onDelete={(id) => containerMutation.mutate({ type: "delete", id })}
          />
        ) : (
          <>
            <Table.ScrollContainer minWidth={isAll ? 960 : 860}>
              {activeTab === "onhand" ? (
                <OnHandTable
                  rows={pageOf(onHandRows)}
                  showLocation={isAll}
                  itemById={itemById}
                  onOpen={(lot) => setEditLot(lot)}
                  sort={onHandSort}
                  onSort={(key) => setOnHandSort((s) => toggleSort(s, key))}
                />
              ) : null}
              {activeTab === "lots" ? (
                <LotsTable
                  rows={pageOf(lotsFiltered)}
                  showLocation={isAll}
                  itemById={itemById}
                  lotAction={lotAction}
                  onOpen={(lot) => setEditLot(lot)}
                />
              ) : null}
              {activeTab === "waste" ? <WasteTable rows={pageOf(wasteFiltered)} showLocation={isAll} /> : null}
              {activeTab === "log" ? (
                <LogTable
                  rows={pageOf(logRows)}
                  showLocation={isAll}
                  sort={logSort}
                  onSort={(key) => setLogSort((s) => toggleSort(s, key))}
                  onOpen={(line) => setSelectedTransaction(line)}
                />
              ) : null}
            </Table.ScrollContainer>
            {filteredCount > 0 ? (
              <Group justify="space-between" mt="sm">
                <Text size="xs" c="dimmed">
                  Showing {start + 1}–{Math.min(start + PAGE_SIZE, filteredCount)} of {filteredCount}
                </Text>
                {pageCount > 1 ? <Pagination value={page} onChange={setPage} total={pageCount} size="sm" /> : null}
              </Group>
            ) : null}
          </>
        )}
      </Card>

      {consoleNode}

      <InventoryItemModal
        lot={editLot}
        item={editLot ? itemById.get(editLot.item_id) : undefined}
        onClose={() => setEditLot(null)}
      />

      <TransactionDetailModal transaction={selectedTransaction} onClose={() => setSelectedTransaction(null)} />
    </Box>
  );
}

function EmptyRow({ colSpan, text }: { colSpan: number; text: string }) {
  return (
    <Table.Tr>
      <Table.Td colSpan={colSpan}>
        <Text size="sm" c="dimmed">
          {text}
        </Text>
      </Table.Td>
    </Table.Tr>
  );
}

function ItemCell({ name, item }: { name: string | undefined; item: Item | undefined }) {
  const display = name || item?.name || "—";
  return (
    <Group gap="sm" wrap="nowrap">
      <Avatar src={item?.image_url ?? undefined} radius="sm" size={34}>
        {display.slice(0, 1)}
      </Avatar>
      <div style={{ minWidth: 0 }}>
        <Text fw={600} lineClamp={1}>
          {display}
        </Text>
        {item?.brand ? (
          <Text size="xs" c="dimmed" lineClamp={1}>
            {item.brand}
          </Text>
        ) : null}
      </div>
    </Group>
  );
}

function OnHandTable({
  rows,
  showLocation,
  itemById,
  onOpen,
  sort,
  onSort,
}: {
  rows: WithLocation<LotLine>[];
  showLocation: boolean;
  itemById: Map<string, Item>;
  onOpen: (lot: WithLocation<LotLine>) => void;
  sort: SortState;
  onSort: (key: string) => void;
}) {
  const cols = showLocation ? 5 : 4;
  return (
    <Table className="pantry-table" highlightOnHover verticalSpacing="sm">
      <Table.Thead>
        <Table.Tr>
          <SortableTh label="Item" sortKey="item_name" sort={sort} onSort={onSort} />
          {showLocation ? <SortableTh label="Location" sortKey="location_name" sort={sort} onSort={onSort} /> : null}
          <Table.Th>Size</Table.Th>
          <SortableTh label="On hand" sortKey="quantity_remaining" sort={sort} onSort={onSort} />
          <SortableTh label="Best by" sortKey="expiration_date" sort={sort} onSort={onSort} />
        </Table.Tr>
      </Table.Thead>
      <Table.Tbody>
        {rows.map((lot) => {
          const item = itemById.get(lot.item_id);
          return (
            <Table.Tr key={lot.lot_id} style={{ cursor: "pointer" }} onClick={() => onOpen(lot)}>
              <Table.Td>
                <ItemCell name={lot.item_name} item={item} />
              </Table.Td>
              {showLocation ? (
                <Table.Td>
                  <LocationBadge name={lot.location_name} />
                </Table.Td>
              ) : null}
              <Table.Td>{itemSize(item)}</Table.Td>
              <Table.Td fw={600}>
                {fmtQty(lot.quantity_remaining)} {lot.unit}
                {lot.status === "open" ? (
                  <Text component="span" size="xs" c="dimmed" ml={6}>
                    open
                  </Text>
                ) : null}
              </Table.Td>
              <Table.Td>
                {lot.expiration_date ? (
                  <DayTag days={lot.days_until_expiration} />
                ) : (
                  <Badge size="xs" variant="light" color="gray">
                    set a date
                  </Badge>
                )}
              </Table.Td>
            </Table.Tr>
          );
        })}
        {rows.length === 0 ? <EmptyRow colSpan={cols} text="Nothing on hand." /> : null}
      </Table.Tbody>
    </Table>
  );
}

function LotsTable({
  rows,
  showLocation,
  itemById,
  lotAction,
  onOpen,
}: {
  rows: WithLocation<LotLine>[];
  showLocation: boolean;
  itemById: Map<string, Item>;
  lotAction: ReturnType<typeof useMutation<void, Error, { path: string; body?: Record<string, unknown> }>>;
  onOpen: (lot: WithLocation<LotLine>) => void;
}) {
  const cols = showLocation ? 8 : 7;
  return (
    <Table className="pantry-table" highlightOnHover verticalSpacing="sm">
      <Table.Thead>
        <Table.Tr>
          <Table.Th>Item</Table.Th>
          {showLocation ? <Table.Th>Location</Table.Th> : null}
          <Table.Th>Container</Table.Th>
          <Table.Th>Remaining</Table.Th>
          <Table.Th>Expiration</Table.Th>
          <Table.Th>Expires in</Table.Th>
          <Table.Th>Status</Table.Th>
          <Table.Th style={{ textAlign: "right" }}>Actions</Table.Th>
        </Table.Tr>
      </Table.Thead>
      <Table.Tbody>
        {rows.map((lot) => (
          <Table.Tr key={lot.lot_id} style={{ cursor: "pointer" }} onClick={() => onOpen(lot)}>
            <Table.Td>
              <ItemCell name={lot.item_name} item={itemById.get(lot.item_id)} />
            </Table.Td>
            {showLocation ? (
              <Table.Td>
                <LocationBadge name={lot.location_name} />
              </Table.Td>
            ) : null}
            <Table.Td>{lot.container_name ?? "—"}</Table.Td>
            <Table.Td>
              {fmtQty(lot.quantity_remaining)} {lot.unit}
            </Table.Td>
            <Table.Td>{lot.expiration_date ?? "—"}</Table.Td>
            <Table.Td>
              {lot.days_until_expiration === null ? (
                <Text size="sm" c="dimmed">
                  —
                </Text>
              ) : (
                <DayTag days={lot.days_until_expiration} />
              )}
            </Table.Td>
            <Table.Td>
              <StatusTag status={lot.status} frozen={lot.frozen} />
            </Table.Td>
            <Table.Td onClick={(e) => e.stopPropagation()}>
              <Group gap={6} justify="flex-end" wrap="nowrap">
                <Button
                  size="compact-sm"
                  variant="default"
                  disabled={lot.status === "open" || lotAction.isPending}
                  onClick={() => lotAction.mutate({ path: `lots/${lot.lot_id}/open` })}
                >
                  Open
                </Button>
                <Button
                  size="compact-sm"
                  variant="default"
                  disabled={lotAction.isPending}
                  onClick={() => lotAction.mutate({ path: `lots/${lot.lot_id}/${lot.frozen ? "thaw" : "freeze"}` })}
                >
                  {lot.frozen ? "Thaw" : "Freeze"}
                </Button>
                {/* The third outcome, and the common one: you finished it.
                    Without this the only ways off the shelf were Spoil (counts
                    it as waste) and Remove (claims it was never there) — both
                    put something false in the ledger for food that was eaten. */}
                <Button
                  size="compact-sm"
                  variant="default"
                  disabled={lotAction.isPending}
                  onClick={() => lotAction.mutate({ path: `lots/${lot.lot_id}/finish` })}
                  aria-label={`Mark this container used up`}
                >
                  Used it up
                </Button>
                <Button
                  size="compact-sm"
                  variant="outline"
                  color="terracotta"
                  disabled={lotAction.isPending}
                  onClick={() => lotAction.mutate({ path: `lots/${lot.lot_id}/spoil` })}
                  styles={{ root: { color: "var(--pantry-accent-700)", borderColor: "var(--pantry-accent-300)" } }}
                >
                  Spoil
                </Button>
              </Group>
            </Table.Td>
          </Table.Tr>
        ))}
        {rows.length === 0 ? <EmptyRow colSpan={cols} text="No lots on hand." /> : null}
      </Table.Tbody>
    </Table>
  );
}

function ContainersView({
  containers,
  showLocation,
  newContainerName,
  setNewContainerName,
  locationOptions,
  newContainerLoc,
  setNewContainerLoc,
  pickLocation,
  pending,
  onCreate,
  onDelete,
}: {
  containers: WithLocation<Container>[];
  showLocation: boolean;
  newContainerName: string;
  setNewContainerName: (value: string) => void;
  locationOptions: { value: string; label: string }[];
  newContainerLoc: string | null;
  setNewContainerLoc: (value: string | null) => void;
  pickLocation: boolean;
  pending: boolean;
  onCreate: () => void;
  onDelete: (id: string) => void;
}) {
  const canCreate = newContainerName.trim().length > 0 && Boolean(pickLocation ? newContainerLoc : true);
  return (
    <Stack gap="md">
      <Group align="flex-end">
        <TextInput
          label="New container"
          placeholder="e.g. Shelf A, Top drawer, Bin 3"
          value={newContainerName}
          onChange={(e) => setNewContainerName(e.currentTarget.value)}
          style={{ flex: 1 }}
        />
        {pickLocation ? (
          <Select
            label="Location"
            data={locationOptions}
            value={newContainerLoc}
            onChange={setNewContainerLoc}
            allowDeselect={false}
            comboboxProps={{ radius: "lg" }}
            w={180}
          />
        ) : null}
        <Button disabled={!canCreate} loading={pending} onClick={onCreate}>
          Add container
        </Button>
      </Group>
      <Group gap="xs">
        {containers.map((c) => (
          <Button key={c.id} size="compact-sm" variant="light" rightSection="✕" onClick={() => onDelete(c.id)}>
            {c.name}
            {showLocation ? <Text component="span" size="xs" c="dimmed" ml={6}>· {c.location_name}</Text> : null}
          </Button>
        ))}
        {containers.length === 0 ? (
          <Text size="sm" c="dimmed">
            No containers.
          </Text>
        ) : null}
      </Group>
    </Stack>
  );
}

function WasteTable({ rows, showLocation }: { rows: WithLocation<WasteLine>[]; showLocation: boolean }) {
  const cols = showLocation ? 5 : 4;
  return (
    <Table className="pantry-table" highlightOnHover verticalSpacing="sm">
      <Table.Thead>
        <Table.Tr>
          <Table.Th>Item</Table.Th>
          {showLocation ? <Table.Th>Location</Table.Th> : null}
          <Table.Th>Wasted</Table.Th>
          <Table.Th>Unit</Table.Th>
          <Table.Th>Events</Table.Th>
        </Table.Tr>
      </Table.Thead>
      <Table.Tbody>
        {rows.map((row) => (
          <Table.Tr key={`${row.location_id}-${row.item_id}`}>
            <Table.Td fw={600}>{row.item_name}</Table.Td>
            {showLocation ? (
              <Table.Td>
                <LocationBadge name={row.location_name} />
              </Table.Td>
            ) : null}
            <Table.Td>{String(row.quantity_wasted)}</Table.Td>
            <Table.Td>{row.unit}</Table.Td>
            <Table.Td>{row.events}</Table.Td>
          </Table.Tr>
        ))}
        {rows.length === 0 ? <EmptyRow colSpan={cols} text="No waste recorded." /> : null}
      </Table.Tbody>
    </Table>
  );
}

function LogTable({
  rows,
  showLocation,
  sort,
  onSort,
  onOpen,
}: {
  rows: WithLocation<TransactionLine>[];
  showLocation: boolean;
  sort: SortState;
  onSort: (key: string) => void;
  onOpen: (line: WithLocation<TransactionLine>) => void;
}) {
  const cols = showLocation ? 6 : 5;
  return (
    <Table className="pantry-table" highlightOnHover verticalSpacing="sm">
      <Table.Thead>
        <Table.Tr>
          <SortableTh label="Time" sortKey="created_at" sort={sort} onSort={onSort} />
          <SortableTh label="Item" sortKey="item_name" sort={sort} onSort={onSort} />
          {showLocation ? <Table.Th>Location</Table.Th> : null}
          <SortableTh label="Type" sortKey="transaction_type" sort={sort} onSort={onSort} />
          <SortableTh label="Change" sortKey="quantity_change" sort={sort} onSort={onSort} />
          <SortableTh label="Reason" sortKey="reason" sort={sort} onSort={onSort} />
        </Table.Tr>
      </Table.Thead>
      <Table.Tbody>
        {rows.map((line) => (
          <Table.Tr key={line.transaction_id} style={{ cursor: "pointer" }} onClick={() => onOpen(line)}>
            <Table.Td>{new Date(line.created_at).toLocaleString()}</Table.Td>
            <Table.Td fw={600}>{line.item_name}</Table.Td>
            {showLocation ? (
              <Table.Td>
                <LocationBadge name={line.location_name} />
              </Table.Td>
            ) : null}
            <Table.Td>{line.transaction_type}</Table.Td>
            <Table.Td>
              {String(line.quantity_change)} {line.unit}
            </Table.Td>
            <Table.Td>{line.reason ?? "—"}</Table.Td>
          </Table.Tr>
        ))}
        {rows.length === 0 ? <EmptyRow colSpan={cols} text="No transactions yet." /> : null}
      </Table.Tbody>
    </Table>
  );
}
