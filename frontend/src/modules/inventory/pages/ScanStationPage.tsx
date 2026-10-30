import {
  Alert,
  Badge,
  Button,
  Card,
  Group,
  SegmentedControl,
  Select,
  Stack,
  Text,
  TextInput,
  Title,
} from "@mantine/core";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";

import { api } from "@/api/client";
import { getApiErrorMessage } from "@/api/errors";
import { QuantityStepper } from "@/components/QuantityStepper";
import { useItems, useItemSearch, useLocations } from "@/modules/catalog";
import { useAppState } from "@/state/AppStateProvider";

type Direction = "in" | "out";

/** One row of the append-only inventory ledger (ADR 0002). */
type LedgerEvent = {
  event_id: string;
  event_type: string;
  item_name: string;
  quantity: string | number;
  unit: string;
  location_name?: string | null;
  occurred_at: string;
  reason?: string | null;
};

// How each ledger fact reads to a human, and how loudly. Only the kinds that
// take something OFF the shelves get colour — an add is not news here.
const EVENT_LABEL: Record<string, { verb: string; color: string }> = {
  consumed: { verb: "used", color: "blue" },
  spoiled: { verb: "thrown out", color: "red" },
  purchased: { verb: "added", color: "gray" },
  produced: { verb: "cooked", color: "gray" },
  adjusted: { verb: "adjusted", color: "gray" },
  transferred: { verb: "moved", color: "gray" },
  opened: { verb: "opened", color: "gray" },
};

function relativeTime(iso: string): string {
  const mins = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.round(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  return "yesterday";
}
const DIRECTION_KEY = "pantry_scan_direction";
const AUTO = "__auto__";

type ScanLocationOption = { location_id: string; location_name: string; quantity: string };
type ScanResult = {
  status: "done" | "needs_location";
  direction: Direction;
  item_name: string;
  resolved_location_name: string | null;
  inventory: { quantity: string | number; unit: string }[];
  candidate_locations: ScanLocationOption[];
};
// Exactly one of barcode/itemId identifies what's being scanned — a typed/wedge
// barcode, or an item tapped from the name-search suggestions below the field.
type ScanInput = {
  barcode?: string;
  itemId?: string;
  locationId: string | null;
  direction: Direction;
  quantity: number;
  expiration?: string;
};
type LogEntry = { ok: boolean; text: string };
type Pending = { input: ScanInput; itemName: string; candidates: ScanLocationOption[] };

function initialDirection(): Direction {
  if (typeof window === "undefined") return "in";
  return window.localStorage.getItem(DIRECTION_KEY) === "out" ? "out" : "in";
}

export function ScanStationPage() {
  const { activeProfileId } = useAppState();
  const { data: locations } = useLocations();
  useItems(); // warm the catalog cache so freshly-added items resolve

  const [direction, setDirection] = useState<Direction>(initialDirection);
  const [stationLocation, setStationLocation] = useState<string>(AUTO);
  const [barcode, setBarcode] = useState("");
  const [qty, setQty] = useState<number | string>(1);
  const [bestBy, setBestBy] = useState("");
  const [log, setLog] = useState<LogEntry[]>([]);
  const [pending, setPending] = useState<Pending | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const queryClient = useQueryClient();

  // Recent activity comes from the append-only ledger, not component state.
  // It used to be a `useState` array: it vanished on reload, so there was no way
  // to check "did I already scan that out?" — which is exactly the question you
  // ask the morning after. Bounded by a 24h window AND a row cap so it stays a
  // glanceable list rather than an ever-growing log.
  const historyQuery = useQuery({
    queryKey: ["inventory-events", "recent"],
    queryFn: async () =>
      (
        await api.get<{ events: LedgerEvent[] }>("/api/inventory/events", {
          params: { since_hours: 24, limit: 25 },
        })
      ).data.events,
    refetchOnWindowFocus: true,
  });

  useEffect(() => {
    window.localStorage.setItem(DIRECTION_KEY, direction);
  }, [direction]);

  const locationOptions = [
    { value: AUTO, label: "Auto — find location" },
    ...(locations ?? []).map((l) => ({ value: l.id, label: l.name })),
  ];

  // No-scanner-handy path: type a few letters of the item's name instead of
  // its barcode. "Out" only offers items actually on hand (nothing to consume
  // otherwise); "in" searches the whole catalog (restocking something bought
  // before). Barcodes are all-digits and won't match anything by name, so this
  // stays harmless (just an empty list) during a normal scanner-wedge submit.
  const nameSearch = useItemSearch(barcode, { inStockOnly: direction === "out" });
  const suggestions = nameSearch.data ?? [];

  const scan = useMutation({
    mutationFn: async (input: ScanInput) => {
      const res = await api.post<ScanResult>("/api/inventory/scan", {
        ...(input.itemId ? { item_id: input.itemId } : { barcode: input.barcode }),
        location_id: input.locationId,
        direction: input.direction,
        quantity: input.quantity,
        ...(input.expiration ? { expiration_date: input.expiration } : {}),
      });
      return { data: res.data, input };
    },
    onSuccess: ({ data, input }) => {
      if (data.status === "needs_location") {
        // In more than one place — ask instead of guessing.
        setPending({ input, itemName: data.item_name, candidates: data.candidate_locations });
        return;
      }
      setPending(null);
      const bal = data.inventory[0];
      const verb = data.direction === "in" ? "added" : "consumed";
      const where = data.resolved_location_name ? ` in ${data.resolved_location_name}` : "";
      setLog((l) =>
        [
          {
            ok: true,
            text: `${verb} ${input.quantity} — ${data.item_name}${where} (now ${String(bal?.quantity ?? "?")} ${bal?.unit ?? ""})`,
          },
          ...l,
        ].slice(0, 12),
      );
      queryClient.invalidateQueries({ queryKey: ["inventory-events", "recent"] });
    },
    onError: (err) => {
      setPending(null);
      setLog((l) => [{ ok: false, text: getApiErrorMessage(err, "Scan failed") }, ...l].slice(0, 12));
    },
  });

  const locationIdForScan = () => (stationLocation === AUTO ? null : stationLocation);

  const submit = () => {
    const code = barcode.trim();
    if (!code) return;
    scan.mutate({
      barcode: code,
      locationId: locationIdForScan(),
      direction,
      quantity: Number(qty) || 1,
      expiration: direction === "in" && bestBy ? bestBy : undefined,
    });
    setBarcode("");
    inputRef.current?.focus();
  };

  const submitItem = (itemId: string) => {
    scan.mutate({
      itemId,
      locationId: locationIdForScan(),
      direction,
      quantity: Number(qty) || 1,
      expiration: direction === "in" && bestBy ? bestBy : undefined,
    });
    setBarcode("");
    inputRef.current?.focus();
  };

  const chooseLocation = (locationId: string) => {
    if (!pending) return;
    scan.mutate({ ...pending.input, locationId });
  };

  return (
    <Stack>
      <Group justify="space-between" align="center">
        <Title order={2}>Scan Station</Title>
        <Select
          data={locationOptions}
          value={stationLocation}
          onChange={(v) => setStationLocation(v ?? AUTO)}
          allowDeselect={false}
          aria-label="Scan location"
          w={220}
        />
      </Group>

      <SegmentedControl
        fullWidth
        size="lg"
        value={direction}
        onChange={(v) => {
          setDirection(v as Direction);
          // The click itself steals focus from the barcode field — claim it
          // back on the next tick so "pick a mode, keep scanning" needs no
          // extra tap in between.
          window.setTimeout(() => inputRef.current?.focus(), 0);
        }}
        data={[
          { label: "In (add)", value: "in" },
          { label: "Out (consume)", value: "out" },
        ]}
        color={direction === "in" ? "green" : "red"}
      />

      <Text c="dimmed">
        Scan or type a barcode and press Enter — or type a few letters of the item's name to pick it from the list
        below. On <b>Auto</b>, a consume is taken from wherever the item is — you're only asked when it's in more
        than one place. Direction is remembered on this device.
      </Text>

      {pending ? (
        <Alert color="blue" title={`Where is ${pending.itemName}?`}>
          <Text size="sm" mb="xs">
            It's in more than one place — pick where to {pending.input.direction === "in" ? "add" : "take"} it.
          </Text>
          <Group gap="xs">
            {pending.candidates.map((c) => (
              <Button
                key={c.location_id}
                size="sm"
                variant="light"
                loading={scan.isPending}
                onClick={() => chooseLocation(c.location_id)}
              >
                {c.location_name} ({Number(c.quantity)})
              </Button>
            ))}
            <Button size="sm" variant="subtle" color="gray" onClick={() => setPending(null)}>
              Cancel
            </Button>
          </Group>
        </Alert>
      ) : null}

      {activeProfileId ? null : (
        <Text size="sm" c="dimmed">
          No profile selected — scans won't be attributed to a person.
        </Text>
      )}

      <Card withBorder padding="md">
        <Stack gap="sm">
          <Group align="flex-end">
            <TextInput
              ref={inputRef}
              label="Barcode or item name"
              placeholder="Scan, or type a barcode/name then Enter"
              value={barcode}
              autoFocus
              onChange={(e) => setBarcode(e.currentTarget.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") submit();
              }}
              style={{ flex: 1, minWidth: 180 }}
            />
            <QuantityStepper label="Qty" ariaLabel="quantity" min={1} value={qty} onChange={setQty} />
            {direction === "in" ? (
              <TextInput
                label="Best-by (optional)"
                type="date"
                value={bestBy}
                onChange={(e) => setBestBy(e.currentTarget.value)}
                w={170}
              />
            ) : null}
            <Button
              size="lg"
              color={direction === "in" ? "green" : "red"}
              onClick={submit}
              loading={scan.isPending && !pending}
              disabled={!barcode.trim()}
            >
              {direction === "in" ? "Add" : "Consume"}
            </Button>
          </Group>

          {/* Name-search suggestions — only meaningful once there's enough to
              search on; a real barcode just won't match anything here. */}
          {barcode.trim().length >= 2 && (nameSearch.isFetching || suggestions.length > 0) && (
            <Group gap="xs">
              {nameSearch.isFetching && suggestions.length === 0 ? (
                <Text size="xs" c="dimmed">
                  Searching…
                </Text>
              ) : (
                suggestions.map((item) => (
                  <Button
                    key={item.id}
                    size="xs"
                    variant="light"
                    loading={scan.isPending}
                    onClick={() => submitItem(item.id)}
                  >
                    {item.name}
                    {item.brand ? ` — ${item.brand}` : ""}
                  </Button>
                ))
              )}
            </Group>
          )}
          {barcode.trim().length >= 2 && !nameSearch.isFetching && suggestions.length === 0 && (
            <Text size="xs" c="dimmed">
              {direction === "out" ? "Nothing on hand matches that name." : "No catalog items match that name."}
            </Text>
          )}
        </Stack>
      </Card>

      <Card withBorder padding="md">
        <Group justify="space-between" align="baseline">
          <Title order={4}>Recent</Title>
          <Text size="xs" c="dimmed">
            last 24 hours
          </Text>
        </Group>
        <Stack gap={4} mt="sm">
          {/* Failed scans first: they never became ledger events, so this is the
              only place they exist. They're session-local by nature. */}
          {log
            .filter((entry) => !entry.ok)
            .map((entry, i) => (
              <Group key={`err-${i}`} gap="xs" wrap="nowrap" align="flex-start">
                <Badge color="red" variant="light" size="sm">
                  error
                </Badge>
                <Text size="sm">{entry.text}</Text>
              </Group>
            ))}

          {historyQuery.isLoading ? (
            <Text c="dimmed" size="sm">
              Loading…
            </Text>
          ) : (historyQuery.data ?? []).length === 0 ? (
            <Text c="dimmed" size="sm">
              Nothing has moved in the last 24 hours.
            </Text>
          ) : (
            (historyQuery.data ?? []).map((event) => {
              const label = EVENT_LABEL[event.event_type] ?? {
                verb: event.event_type,
                color: "gray",
              };
              return (
                <Group key={event.event_id} gap="xs" wrap="nowrap" align="baseline">
                  <Badge color={label.color} variant="light" size="sm">
                    {label.verb}
                  </Badge>
                  <Text size="sm" style={{ flex: 1, minWidth: 0 }} truncate>
                    {Number(event.quantity)} {event.unit} — {event.item_name}
                    {event.location_name ? ` (${event.location_name})` : ""}
                  </Text>
                  <Text size="xs" c="dimmed" style={{ flexShrink: 0 }}>
                    {relativeTime(event.occurred_at)}
                  </Text>
                </Group>
              );
            })
          )}
        </Stack>
      </Card>
    </Stack>
  );
}
