import {
  Badge,
  Box,
  Button,
  Card,
  Divider,
  Group,
  Select,
  SimpleGrid,
  Stack,
  Text,
  Title,
} from "@mantine/core";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { BarChart2, Check, ChefHat, Clock, Minus, Plus, Utensils } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { api } from "@/api/client";
import { getApiErrorMessage } from "@/api/errors";
import { QuantityStepper } from "@/components/QuantityStepper";
import { ActionConsole } from "@/components/ActionConsole";
import { LocationPill } from "@/components/LocationPill";
import { useItems, useLocations } from "@/modules/catalog";
import { useShoppingList } from "@/modules/shopping";
import { useAppState } from "@/state/AppStateProvider";

import { DayTag, Panel, PanelRow } from "../components/kiosk-ui";
import {
  useScopedExpiring,
  useScopedInventory,
  useScopedRecipeSuggestions,
  useScopedRestock,
  useScopedTrends,
} from "../hooks/scoped-hooks";

type LogAction = "remove" | "add" | "adjust";

/** Trim trailing zeros: "1.000" → "1". */
function fmtQty(value: string | number): string {
  const n = Number(value);
  return Number.isFinite(n) ? String(n) : String(value);
}

const UNIT_OPTIONS = [
  { value: "each", label: "each" },
  { value: "slice", label: "slice" },
  { value: "oz", label: "oz" },
  { value: "lb", label: "lb" },
  { value: "g", label: "g" },
  { value: "kg", label: "kg" },
  { value: "ml", label: "mL" },
  { value: "l", label: "L" },
  { value: "cup", label: "cup" },
  { value: "tbsp", label: "tbsp" },
  { value: "tsp", label: "tsp" },
];

function greetingFor(date: Date): string {
  const weekday = date.toLocaleDateString(undefined, { weekday: "long" });
  const hour = date.getHours();
  const part = hour < 12 ? "morning" : hour < 17 ? "afternoon" : "evening";
  return `${weekday} ${part}`;
}

function LocationHint({ show, name }: { show: boolean; name: string }) {
  if (!show) return null;
  return (
    <Text component="span" size="xs" c="dimmed" ml={8}>
      · {name}
    </Text>
  );
}

export function DashboardPage() {
  const queryClient = useQueryClient();
  const { selectedLocationId, setStatus } = useAppState();
  const isAll = selectedLocationId === null;

  const itemsQuery = useItems();
  const items = itemsQuery.data ?? [];
  const { data: locations } = useLocations();
  const locationList = useMemo(() => locations ?? [], [locations]);

  const expiringQuery = useScopedExpiring(selectedLocationId, 7);
  const restockQuery = useScopedRestock(selectedLocationId);
  const recipesQuery = useScopedRecipeSuggestions(selectedLocationId);
  const trendsQuery = useScopedTrends(selectedLocationId, 30);
  const inventoryQuery = useScopedInventory(selectedLocationId);
  const inventory = inventoryQuery.data;

  // Expiring: soonest first.
  const expiring = useMemo(
    () => [...expiringQuery.data].sort((a, b) => a.days_until_expiration - b.days_until_expiration).slice(0, 8),
    [expiringQuery.data],
  );
  // Running low: keep highest-priority first (backend assigns priority 3 to
  // out-of-stock, 1 to least urgent, and sorts descending — match that).
  const restock = useMemo(() => [...restockQuery.data].sort((a, b) => b.priority - a.priority).slice(0, 8), [restockQuery.data]);
  // Cook tonight: dedupe recipes across locations, keeping the best availability.
  const recipes = useMemo(() => {
    const best = new Map<string, (typeof recipesQuery.data)[number]>();
    for (const r of recipesQuery.data) {
      const prev = best.get(r.recipe_id);
      if (!prev || r.available_score > prev.available_score) best.set(r.recipe_id, r);
    }
    return [...best.values()].sort((a, b) => b.available_score - a.available_score).slice(0, 3);
  }, [recipesQuery.data]);
  // Quick Log chips: most-consumed items across scope, deduped by item.
  const consumptionChips = useMemo(() => {
    const best = new Map<string, { id: string; name: string; usage: number }>();
    for (const t of trendsQuery.data) {
      const prev = best.get(t.item_id);
      if (!prev || t.avg_daily_usage > prev.usage) best.set(t.item_id, { id: t.item_id, name: t.item_name, usage: t.avg_daily_usage });
    }
    return [...best.values()].sort((a, b) => b.usage - a.usage).slice(0, 6);
  }, [trendsQuery.data]);

  const [selectedItemId, setSelectedItemId] = useState<string | null>(null);
  const [amount, setAmount] = useState<number | string>(1);
  const [unit, setUnit] = useState<string>("each");
  const [manualWriteLoc, setManualWriteLoc] = useState<string | null>(null);

  useEffect(() => {
    if (manualWriteLoc && locationList.some((l) => l.id === manualWriteLoc)) return;
    if (locationList.length > 0) setManualWriteLoc(locationList[0].id);
  }, [locationList, manualWriteLoc]);

  const writeLocationId = isAll ? manualWriteLoc : selectedLocationId;
  const itemsById = useMemo(() => new Map(items.map((i) => [i.id, i])), [items]);

  // What's actually on hand at the target location (item_id → total + its stocked
  // unit). Drives on-hand-first picking and the correct consumption unit so a
  // "log" never fails an each→gal conversion.
  const onHand = useMemo(() => {
    const map = new Map<string, { qty: number; unit: string }>();
    for (const row of inventory) {
      if (isAll && writeLocationId && row.location_id !== writeLocationId) continue;
      const qty = Number(row.quantity);
      const prev = map.get(row.item_id);
      if (prev) prev.qty += qty;
      else map.set(row.item_id, { qty, unit: row.unit });
    }
    return map;
  }, [inventory, isAll, writeLocationId]);

  // Load an item into the picker and default a sensible, convertible unit.
  const pickItem = (id: string | null) => {
    setSelectedItemId(id);
    if (!id) return;
    setUnit(onHand.get(id)?.unit || itemsById.get(id)?.size_unit || "each");
    setAmount(1);
  };

  // On-hand items first (with their amount), then the rest — you log what you have.
  const itemOptions = useMemo(() => {
    const withStock: { value: string; label: string }[] = [];
    const without: { value: string; label: string }[] = [];
    for (const it of items) {
      const oh = onHand.get(it.id);
      const entry = { value: it.id, label: oh ? `${it.name} — ${fmtQty(oh.qty)} ${oh.unit}` : it.name };
      (oh ? withStock : without).push(entry);
    }
    const byLabel = (a: { label: string }, b: { label: string }) => a.label.localeCompare(b.label);
    return [...withStock.sort(byLabel), ...without.sort(byLabel)];
  }, [items, onHand]);
  const locationOptions = useMemo(() => locationList.map((loc) => ({ value: loc.id, label: loc.name })), [locationList]);

  // Quick chips: recently-used items you still have on hand; if none, on-hand staples.
  const quickChips = useMemo(() => {
    const onHandIds = new Set(onHand.keys());
    const recent = consumptionChips.filter((c) => onHandIds.has(c.id));
    if (recent.length > 0) return recent.slice(0, 6);
    return [...onHand.keys()].slice(0, 6).map((id) => ({ id, name: itemsById.get(id)?.name ?? "item", usage: 0 }));
  }, [consumptionChips, onHand, itemsById]);

  // Default to an on-hand item with its stocked unit (so the first log just works).
  useEffect(() => {
    if (selectedItemId) return;
    const def = items.find((i) => onHand.has(i.id)) ?? items[0];
    if (!def) return;
    setSelectedItemId(def.id);
    setUnit(onHand.get(def.id)?.unit || def.size_unit || "each");
  }, [items, selectedItemId, onHand]);

  // Tapping a chip: counted items log −1 instantly; measured items (milk) load
  // into the picker so you set the amount — never a wrong blind "−1 gal".
  const onChip = (chip: { id: string; name: string }) => {
    const it = itemsById.get(chip.id);
    const unit = onHand.get(chip.id)?.unit || it?.size_unit || "each";
    if (it?.stock_mode === "counted") {
      logMutation.mutate({ action: "remove", itemId: chip.id, quantity: 1, unit, itemName: chip.name });
    } else {
      pickItem(chip.id);
    }
  };

  const greeting = useMemo(() => greetingFor(new Date()), []);

  const logMutation = useMutation({
    mutationFn: async (input: { action: LogAction; itemId: string | null; quantity: number | string; unit: string; itemName?: string }) => {
      if (!writeLocationId) throw new Error("Choose a location to log to");
      if (!input.itemId) throw new Error("Select an item first");
      const rawQty = typeof input.quantity === "string" ? input.quantity.trim() : input.quantity;
      const qtyNum = Number(rawQty);
      if (rawQty === "" || !Number.isFinite(qtyNum)) throw new Error("Amount must be a number");
      if (input.action !== "adjust" && qtyNum <= 0) throw new Error("Amount must be > 0");
      if (input.action === "adjust" && qtyNum < 0) throw new Error("Adjust amount must be >= 0");

      const payload: Record<string, unknown> = {
        item_id: input.itemId,
        location_id: writeLocationId,
        quantity: qtyNum,
        unit: input.unit.trim() || "each",
        reason: input.action === "remove" ? "consumption" : "dashboard_quick_action",
        note: "quick_log",
      };
      await api.post(`/api/inventory/${input.action}`, payload);
      return { action: input.action, quantity: qtyNum, itemName: input.itemName };
    },
    onSuccess: (result) => {
      if (result.action === "remove" && result.itemName) {
        setStatus(`Logged ${result.quantity} ${result.itemName} used`);
      } else {
        setStatus("Inventory updated");
      }
      setAmount(1); // ready for the next log
      void Promise.all([
        queryClient.invalidateQueries({ queryKey: ["inventory"] }),
        queryClient.invalidateQueries({ queryKey: ["transactions"] }),
        queryClient.invalidateQueries({ queryKey: ["consumptionTrends"] }),
        queryClient.invalidateQueries({ queryKey: ["restockSuggestions"] }),
        queryClient.invalidateQueries({ queryKey: ["expiringItems"] }),
        queryClient.invalidateQueries({ queryKey: ["recipeSuggestions"] }),
        queryClient.invalidateQueries({ queryKey: ["scoped"] }),
      ]);
    },
    onError: (error) => {
      setStatus(getApiErrorMessage(error, "Quick log failed"));
    },
  });

  // Add a Running-Low item straight to the shopping list (idempotent server-side).
  // Persistent truth: which items are already on the shopping list. Derived from
  // the list itself so the "on list" state survives leaving and returning to the
  // page (a transient local Set would reset on every remount). The local
  // optimistic Set just flips the pill instantly before the refetch lands.
  const shoppingList = useShoppingList();
  const onListIds = useMemo(() => {
    const ids = new Set<string>();
    for (const e of shoppingList.data ?? []) {
      if (e.status === "pending" && e.item_id) ids.add(e.item_id);
    }
    return ids;
  }, [shoppingList.data]);
  const [addedToList, setAddedToList] = useState<Set<string>>(() => new Set());
  const isOnList = (itemId: string) => onListIds.has(itemId) || addedToList.has(itemId);
  const addToList = useMutation({
    mutationFn: async (s: { item_id: string; item_name: string }) => {
      await api.post("/api/shopping-list", { name: s.item_name, item_id: s.item_id, quantity: 1 });
      return s.item_id;
    },
    onSuccess: (itemId) => {
      setAddedToList((prev) => new Set(prev).add(itemId));
      setStatus("Added to shopping list");
      queryClient.invalidateQueries({ queryKey: ["shopping-list"] });
    },
    onError: (error) => setStatus(getApiErrorMessage(error, "Could not add to shopping list")),
  });

  const consoleNode = (
    <ActionConsole>
      <Stack gap="md" p="lg">
        <Group gap={8} wrap="nowrap">
          <Utensils size={20} color="var(--pantry-accent)" strokeWidth={2.6} />
          <Text style={{ fontFamily: "var(--mantine-heading-font-family)", fontSize: 19 }}>Quick Log</Text>
          <Badge color="gray" variant="light" radius="xl" ml="auto">
            pinned
          </Badge>
        </Group>
        <Text size="xs" c="dimmed" mt={-12}>
          Ate an apple? Used some yogurt? Tap to log it.
        </Text>

        <Select
          label="Log to"
          data={locationOptions}
          value={writeLocationId}
          onChange={setManualWriteLoc}
          disabled={!isAll}
          allowDeselect={false}
          comboboxProps={{ radius: "lg" }}
        />

        <Box>
          <Text size="xs" c="dimmed">
            Just consumed — tap to log one
          </Text>
          <Group gap={8} mt={7}>
            {quickChips.map((chip) => {
              const counted = itemsById.get(chip.id)?.stock_mode === "counted";
              return (
                <Button
                  key={chip.id}
                  variant="default"
                  size="sm"
                  loading={logMutation.isPending}
                  onClick={() => onChip(chip)}
                  style={{ minHeight: 44 }}
                >
                  {chip.name}
                  <Text component="span" c="var(--pantry-accent)" fw={700} ml={8}>
                    {counted ? "−1" : "log…"}
                  </Text>
                </Button>
              );
            })}
            {quickChips.length === 0 ? (
              <Text size="xs" c="dimmed">
                Nothing on hand to log here.
              </Text>
            ) : null}
          </Group>
        </Box>

        <Divider color="var(--pantry-divider)" />

        <Select
          label="Or pick an item"
          description="On-hand items first, with how much you have"
          data={itemOptions}
          value={selectedItemId}
          onChange={pickItem}
          searchable
          allowDeselect={false}
          disabled={items.length === 0}
          comboboxProps={{ radius: "lg" }}
        />
        <Group grow>
          <QuantityStepper label="Amount used" ariaLabel="amount used" value={amount} onChange={setAmount} min={0} step={1} decimalScale={3} />
          <Select label="Unit" data={UNIT_OPTIONS} value={unit} onChange={(value) => setUnit(value || "each")} comboboxProps={{ radius: "lg" }} />
        </Group>

        <Button
          size="lg"
          leftSection={<Minus size={19} strokeWidth={2.8} />}
          loading={logMutation.isPending}
          onClick={() => {
            const itemName = items.find((i) => i.id === selectedItemId)?.name;
            logMutation.mutate({ action: "remove", itemId: selectedItemId, quantity: amount, unit, itemName });
          }}
          style={{ minHeight: 54 }}
        >
          Log as used
        </Button>
        <Group grow>
          <Button variant="default" loading={logMutation.isPending} onClick={() => logMutation.mutate({ action: "add", itemId: selectedItemId, quantity: amount, unit })} style={{ minHeight: 46 }}>
            Add stock
          </Button>
          <Button variant="default" loading={logMutation.isPending} onClick={() => logMutation.mutate({ action: "adjust", itemId: selectedItemId, quantity: amount, unit })} style={{ minHeight: 46 }}>
            Adjust
          </Button>
        </Group>
      </Stack>
    </ActionConsole>
  );

  return (
    <Box>
      <Group justify="space-between" align="flex-end" mb="lg" wrap="wrap">
        <Stack gap={2}>
          <Text size="xs" c="dimmed">
            {greeting}
          </Text>
          <Title order={2}>The Kitchen</Title>
        </Stack>
        <LocationPill />
      </Group>

      <SimpleGrid cols={{ base: 1, md: 2 }} spacing="md">
        <Panel title="Expiring soon" icon={Clock}>
          {expiring.map((item) => (
            <PanelRow
              key={`${item.location_id}-${item.item_id}-${item.expiration_date}`}
              left={
                <>
                  {item.item_name}
                  <LocationHint show={isAll} name={item.location_name} />
                </>
              }
              right={<DayTag days={item.days_until_expiration} />}
            />
          ))}
          {expiring.length === 0 ? (
            <Text size="sm" c="dimmed" py="xs">
              Nothing expiring soon.
            </Text>
          ) : null}
        </Panel>

        <Panel title="Running low" icon={BarChart2} iconColor="var(--pantry-sage-600)">
          {restock.map((suggestion) => (
            <PanelRow
              key={`${suggestion.location_id}-${suggestion.item_id}`}
              left={
                <>
                  {suggestion.item_name}
                  <LocationHint show={isAll} name={suggestion.location_name} />
                </>
              }
              right={
                isOnList(suggestion.item_id) ? (
                  <Badge color="sage" variant="filled" radius="xl" leftSection={<Check size={12} strokeWidth={3} />}>
                    on list
                  </Badge>
                ) : (
                  <Button
                    size="compact-xs"
                    variant="light"
                    color="sage"
                    radius="xl"
                    leftSection={<Plus size={13} strokeWidth={2.8} />}
                    loading={addToList.isPending && addToList.variables?.item_id === suggestion.item_id}
                    onClick={() => addToList.mutate({ item_id: suggestion.item_id, item_name: suggestion.item_name })}
                  >
                    restock
                  </Button>
                )
              }
            />
          ))}
          {restock.length === 0 ? (
            <Text size="sm" c="dimmed" py="xs">
              Nothing running low.
            </Text>
          ) : null}
        </Panel>
      </SimpleGrid>

      <Panel title="Cook tonight — from what you have" icon={ChefHat} style={{ marginTop: 16 }}>
        <SimpleGrid cols={{ base: 1, sm: 2, md: 3 }} spacing="md" mt={6}>
          {recipes.map((recipe) => {
            const missing = recipe.missing_items.length;
            return (
              <Card key={recipe.recipe_id} padding={0} style={{ background: "var(--pantry-surface)", overflow: "hidden" }}>
                <Box className="pantry-photo" style={{ height: 90, fontSize: 11 }}>
                  {recipe.recipe_name}
                </Box>
                <Box p="sm">
                  <Text fw={700} size="sm">
                    {recipe.recipe_name}
                  </Text>
                  <Text size="xs" c="dimmed">
                    {Math.round(recipe.available_score * 100)}% on hand
                    {missing > 0 ? ` · missing ${missing}` : ""}
                  </Text>
                </Box>
              </Card>
            );
          })}
          {recipes.length === 0 ? (
            <Text size="sm" c="dimmed" py="xs">
              No recipe suggestions yet.
            </Text>
          ) : null}
        </SimpleGrid>
      </Panel>

      {consoleNode}
    </Box>
  );
}
