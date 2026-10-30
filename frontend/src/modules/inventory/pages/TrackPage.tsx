import {
  Alert,
  Badge,
  Button,
  Card,
  Group,
  Modal,
  SegmentedControl,
  Stack,
  Text,
  Title,
} from "@mantine/core";
import { useDisclosure } from "@mantine/hooks";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, Clock, PackageOpen, Repeat } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { api } from "@/api/client";
import { getApiErrorMessage } from "@/api/errors";
import { LocationPill } from "@/components/LocationPill";
import { ProfilePicker } from "@/components/ProfilePicker";
import { type Item, useLocations } from "@/modules/catalog";
import { useAppState } from "@/state/AppStateProvider";

import { IntakeCard, type IntakeCardHandle, type ProductLookup } from "../components/IntakeCard";
import { ScanField, type ScanFieldHandle } from "../components/ScanField";
import { ScanPrompt, type ContainerCandidate, type LocationCandidate } from "../components/ScanPrompt";
import { useScopedWorklist, type WorklistRow } from "../hooks/scoped-hooks";

/**
 * Tracking worklist (ADR 0023 D8) — the surface for recording what a household
 * actually did with its stock.
 *
 * The list is the point, not a sidebar next to a scan field: tapping a row needs
 * no barcode, no typing and no knowing what an item is called in the catalog,
 * which makes it the only interaction here a distracted family member reliably
 * completes.
 *
 * Four verbs (D4). Three of them record a fact and are one tap, protected by
 * undo rather than by a prompt. "Ignore expiry" is the exception — it silences
 * future signal instead of recording anything, so it alone gets a confirmation.
 */

// "Still good" buys a short extension: long enough to stop the nagging, short
// enough that a genuinely forgotten item comes back (ADR 0023 D4).
const STILL_GOOD_DAYS = 3;

// Shared with the scan station so a household that has already picked a
// direction on this device keeps it when they move to this surface.
const DIRECTION_KEY = "pantry_scan_direction";

type Direction = "in" | "out";

type ActionKind = "finished" | "wasted" | "still_good" | "ignored";

type ScanOutcome = {
  status: "done" | "needs_location" | "needs_container";
  item_id: string;
  item_name: string;
  inventory: { quantity: string | number; unit: string }[];
  resolved_location_id: string | null;
  resolved_location_name: string | null;
  candidate_locations: LocationCandidate[];
  candidate_containers: ContainerCandidate[];
  transaction_id: string | null;
};

/**
 * A household mostly consumes, so the surface opens ready to take stock OUT.
 * The direction is remembered per device (the scan station's existing key), so
 * whoever is doing a big check-in sets it once.
 */
function initialDirection(): Direction {
  if (typeof window === "undefined") return "out";
  return window.localStorage.getItem(DIRECTION_KEY) === "in" ? "in" : "out";
}

type DoneAction = {
  label: string;
  // Present only for actions that wrote a reversible ledger entry. "Still good"
  // and "ignore" are edits, not consumption, and are corrected by acting again.
  transactionId: string | null;
};

function isoInDays(days: number): string {
  const d = new Date();
  d.setDate(d.getDate() + days);
  return d.toISOString().slice(0, 10);
}

function quantityLabel(row: WorklistRow): string {
  const qty = Number(row.quantity_remaining);
  if (!Number.isFinite(qty)) return "";
  const rounded = Math.round(qty * 100) / 100;
  return `${rounded}${row.unit ? ` ${row.unit}` : ""}`;
}

/** Why this row is on the list, in the user's terms rather than the API's. */
function SourceTag({ row }: { row: WorklistRow }) {
  if (row.source === "expiring") {
    const days = row.days_until_expiration ?? 0;
    const overdue = days < 0;
    return (
      <Badge
        color={overdue ? "red" : days <= 2 ? "orange" : "yellow"}
        variant="light"
        leftSection={overdue ? <AlertTriangle size={12} /> : <Clock size={12} />}
      >
        {overdue ? `${Math.abs(days)}d past` : days === 0 ? "today" : `${days}d left`}
      </Badge>
    );
  }
  if (row.source === "open") {
    return (
      <Badge color="blue" variant="light" leftSection={<PackageOpen size={12} />}>
        open
      </Badge>
    );
  }
  return (
    <Badge color="gray" variant="light" leftSection={<Repeat size={12} />}>
      used often
    </Badge>
  );
}

export function TrackPage() {
  const { selectedLocationId, activeProfileId } = useAppState();
  const queryClient = useQueryClient();
  const worklist = useScopedWorklist(selectedLocationId);

  const { data: locations } = useLocations();

  const [done, setDone] = useState<DoneAction | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [ignoreTarget, setIgnoreTarget] = useState<WorklistRow | null>(null);
  const [confirmOpen, confirmModal] = useDisclosure(false);

  const [direction, setDirection] = useState<Direction>(initialDirection);
  const [code, setCode] = useState("");
  const [prompt, setPrompt] = useState<ScanOutcome | null>(null);
  const [lookup, setLookup] = useState<ProductLookup | null>(null);
  const scanFieldRef = useRef<ScanFieldHandle>(null);
  const intakeCardRef = useRef<IntakeCardHandle>(null);
  const focusScan = () => scanFieldRef.current?.focus();

  useEffect(() => {
    window.localStorage.setItem(DIRECTION_KEY, direction);
  }, [direction]);

  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ["scoped"] });
    queryClient.invalidateQueries({ queryKey: ["inventory"] });
    queryClient.invalidateQueries({ queryKey: ["expiringItems"] });
  };

  const announce = (label: string, transactionId: string | null) => {
    setError(null);
    setDone({ label, transactionId });
    refresh();
  };

  // ---- Out: consume ------------------------------------------------------
  // A container-tracked item resolves to a container, so this may come back
  // asking which one rather than moving anything (ADR 0023 D2/D3).
  const scanOut = useMutation({
    mutationFn: async (input: { barcode?: string; itemId?: string; locationId?: string; lotId?: string }) => {
      const res = await api.post<ScanOutcome>("/api/inventory/scan", {
        ...(input.itemId ? { item_id: input.itemId } : { barcode: input.barcode }),
        direction: "out",
        quantity: 1,
        ...(input.locationId ? { location_id: input.locationId } : {}),
        ...(input.lotId ? { lot_id: input.lotId } : {}),
      });
      return { data: res.data, input };
    },
    onSuccess: ({ data, input }) => {
      if (data.status !== "done") {
        // Keep what identified the item so answering the prompt can re-issue
        // the same scan with the choice attached.
        setPrompt({ ...data, ...(input.itemId ? {} : {}) });
        setPendingIdentity(input);
        return;
      }
      setPrompt(null);
      setPendingIdentity(null);
      const balance = data.inventory[0];
      const where = data.resolved_location_name ? ` in ${data.resolved_location_name}` : "";
      const now = balance ? ` (now ${Number(balance.quantity)} ${balance.unit})` : "";
      announce(`Used ${data.item_name}${where}${now}`, data.transaction_id);
      setCode("");
      scanFieldRef.current?.focus();
    },
    onError: (err) => {
      setPrompt(null);
      setDone(null);
      setError(getApiErrorMessage(err, "That scan didn't go through"));
      setCode("");
      scanFieldRef.current?.focus();
    },
  });

  const [pendingIdentity, setPendingIdentity] = useState<{
    barcode?: string;
    itemId?: string;
    locationId?: string;
  } | null>(null);

  // ---- In: identify, then check in --------------------------------------
  const lookupProduct = useMutation({
    mutationFn: async (barcode: string) => {
      const res = await api.post<ProductLookup>("/api/barcodes/scan", { barcode, auto_create: true });
      return res.data;
    },
    onSuccess: (data) => {
      setError(null);
      setLookup(data);
    },
    onError: (err) => {
      setLookup(null);
      setError(getApiErrorMessage(err, "Couldn't look that up"));
    },
  });

  // Picked by name rather than scanned: no barcode to look up, but the catalog
  // item already carries the package size and shelf life the intake card wants.
  const lookupItem = useMutation({
    mutationFn: async (itemId: string) => {
      const res = await api.get<Record<string, unknown>>(`/api/items/${itemId}`);
      const item = res.data;
      const candidates = await api.get<LocationCandidate[]>(
        `/api/inventory/item-locations?item_id=${itemId}&direction=in`,
      );
      return {
        barcode: "",
        found: true,
        item_id: itemId,
        item_name: String(item.name ?? "Item"),
        item,
        product: null,
        location_candidates: candidates.data ?? [],
      } as ProductLookup;
    },
    onSuccess: (data) => {
      setError(null);
      setLookup(data);
    },
    onError: (err) => {
      setLookup(null);
      setError(getApiErrorMessage(err, "Couldn't load that item"));
    },
  });

  // Commit a still-pending "in" check-in before moving to the next scan — the
  // scan page's own rapid-fire pattern: each new scan finalizes whatever the
  // operator was just looking at (at its currently-shown quantity/location),
  // so a whole cart can be scanned, glanced at, scanned, glanced at, with no
  // click for the common case. An item still waiting on a real decision
  // (which of several locations?) blocks instead of being silently dropped.
  const proceedIn = (next: () => void) => {
    if (lookup && intakeCardRef.current) {
      if (!intakeCardRef.current.isReady) {
        setError("Finish checking in the current item first — pick a location, then scan the next one.");
        focusScan();
        return;
      }
      intakeCardRef.current
        .commit()
        .then(next)
        .catch(() => {
          // The add failed — its own error is already shown on the card;
          // don't abandon it by moving on to the next scan.
        });
      return;
    }
    next();
  };

  const submitCode = (barcode: string) => {
    setDone(null);
    setError(null);
    if (direction === "in") {
      setCode("");
      proceedIn(() => lookupProduct.mutate(barcode));
      return;
    }
    setCode("");
    scanOut.mutate({ barcode });
  };

  const submitItem = (itemId: string) => {
    setDone(null);
    setError(null);
    setCode("");
    if (direction === "in") {
      proceedIn(() => lookupItem.mutate(itemId));
      return;
    }
    scanOut.mutate({ itemId });
  };

  const act = useMutation({
    mutationFn: async ({ row, kind }: { row: WorklistRow; kind: ActionKind }) => {
      // A "recent" row names an item rather than a container, so there is no lot
      // to target; those rows only offer the aggregate consume.
      if (kind === "finished" && !row.lot_id) {
        const res = await api.post<{ transaction_ids: string[] }>("/api/inventory/remove", {
          item_id: row.item_id,
          location_id: row.location_id,
          quantity: 1,
          unit: row.unit ?? "each",
          reason: "used",
        });
        // This one-tap path is as mistakeable as any other, so it gets the same
        // undo — the endpoint already returns a reversible transaction.
        return { label: `Used 1 ${row.item_name}`, transactionId: res.data.transaction_ids?.[0] ?? null };
      }
      if (kind === "finished") {
        const res = await api.post<{ transaction_id: string | null; already_resolved: boolean }>(
          `/api/inventory/lots/${row.lot_id}/finish`,
        );
        return {
          label: res.data.already_resolved
            ? `${row.item_name} was already finished`
            : `Finished ${row.item_name}`,
          transactionId: res.data.transaction_id,
        };
      }
      if (kind === "wasted") {
        const res = await api.post<{ transaction_id: string | null; already_resolved: boolean }>(
          `/api/inventory/lots/${row.lot_id}/spoil`,
        );
        return {
          label: res.data.already_resolved
            ? `${row.item_name} was already gone`
            : `Threw out ${row.item_name}`,
          transactionId: res.data.transaction_id,
        };
      }
      if (kind === "still_good") {
        await api.post(`/api/inventory/lots/${row.lot_id}/relabel`, {
          expiration_date: isoInDays(STILL_GOOD_DAYS),
        });
        return { label: `${row.item_name} kept for ${STILL_GOOD_DAYS} more days`, transactionId: null };
      }
      await api.put(`/api/items/${row.item_id}`, { ignore_expiry: true });
      return { label: `No longer tracking ${row.item_name}'s best-by`, transactionId: null };
    },
    onSuccess: (result) => {
      setError(null);
      setDone(result);
      refresh();
    },
    onError: (err) => {
      setDone(null);
      setError(getApiErrorMessage(err, "That didn't go through"));
    },
  });

  const undo = useMutation({
    mutationFn: async (transactionId: string) => {
      await api.post(`/api/inventory/transactions/${transactionId}/reverse`);
    },
    onSuccess: () => {
      setDone(null);
      setError(null);
      refresh();
    },
    onError: (err) => setError(getApiErrorMessage(err, "Couldn't undo that")),
  });

  const askToIgnore = (row: WorklistRow) => {
    setIgnoreTarget(row);
    confirmModal.open();
  };

  const confirmIgnore = () => {
    if (ignoreTarget) act.mutate({ row: ignoreTarget, kind: "ignored" });
    confirmModal.close();
    setIgnoreTarget(null);
  };

  const rows = worklist.data;

  return (
    <Stack>
      <Group justify="space-between" align="center" wrap="wrap">
        <Stack gap={2}>
          <Text size="xs" c="dimmed">
            What did you just use?
          </Text>
          <Title order={2}>Track</Title>
        </Stack>
        <Group gap="xs">
          <ProfilePicker />
          <LocationPill />
        </Group>
      </Group>

      {/* Attribution is the point of a shared surface: without a profile every
          tap lands on whoever was last selected (ADR 0023 D7). */}
      {activeProfileId ? null : (
        <Alert color="yellow" variant="light">
          No one is selected — pick a profile above so what you record counts for the right person.
        </Alert>
      )}

      {done ? (
        <Alert color="green" variant="light">
          <Group justify="space-between" wrap="nowrap">
            <Text size="sm">{done.label}</Text>
            {done.transactionId ? (
              <Button
                size="xs"
                variant="subtle"
                loading={undo.isPending}
                onClick={() => undo.mutate(done.transactionId as string)}
              >
                Undo
              </Button>
            ) : null}
          </Group>
        </Alert>
      ) : null}

      {error ? (
        <Alert color="red" variant="light">
          {error}
        </Alert>
      ) : null}

      <Card withBorder padding="md">
        <Stack gap="sm">
          <SegmentedControl
            fullWidth
            size="md"
            value={direction}
            color={direction === "in" ? "green" : "red"}
            onChange={(next) => {
              setDirection(next as Direction);
              setLookup(null);
              setPrompt(null);
              // The click steals focus from the field; a wedge scanner types
              // into whatever holds it, so claim it back on the next tick.
              window.setTimeout(() => scanFieldRef.current?.focus(), 0);
            }}
            data={[
              { label: "Out — used it", value: "out" },
              { label: "In — putting away", value: "in" },
            ]}
          />

          <ScanField
            ref={scanFieldRef}
            value={code}
            onChange={setCode}
            onSubmit={submitCode}
            onPickItem={submitItem}
            direction={direction}
            busy={scanOut.isPending || lookupProduct.isPending || lookupItem.isPending}
          />

          {prompt ? (
            <ScanPrompt
              itemName={prompt.item_name}
              containers={prompt.candidate_containers}
              locations={prompt.candidate_locations}
              busy={scanOut.isPending}
              onPickContainer={(lotId) =>
                scanOut.mutate({
                  ...pendingIdentity,
                  locationId: prompt.resolved_location_id ?? pendingIdentity?.locationId,
                  lotId,
                })
              }
              onPickLocation={(locationId) => scanOut.mutate({ ...pendingIdentity, locationId })}
              onCancel={() => {
                setPrompt(null);
                setPendingIdentity(null);
                scanFieldRef.current?.focus();
              }}
            />
          ) : null}

          {direction === "in" && lookup ? (
            <IntakeCard
              ref={intakeCardRef}
              lookup={lookup}
              locations={locations ?? []}
              defaultLocationId={selectedLocationId}
              focusScan={focusScan}
              onAdded={(label, transactionId) => {
                setLookup(null);
                announce(label, transactionId);
                scanFieldRef.current?.focus();
              }}
              onCancel={() => {
                setLookup(null);
                scanFieldRef.current?.focus();
              }}
              onItemCreated={(item: Item) => {
                queryClient.invalidateQueries({ queryKey: ["items"] });
                setLookup({
                  barcode: lookup.barcode,
                  found: true,
                  item_id: item.id,
                  item_name: item.name,
                  item,
                  product: null,
                  location_candidates: [],
                });
              }}
            />
          ) : null}
        </Stack>
      </Card>

      <Card withBorder padding="md">
        <Stack gap="sm">
          {worklist.isLoading ? <Text c="dimmed">Loading…</Text> : null}

          {!worklist.isLoading && rows.length === 0 ? (
            <Text c="dimmed" py="sm">
              Nothing needs attention right now.
            </Text>
          ) : null}

          {rows.map((row) => (
            <Card key={`${row.location_id}-${row.lot_id ?? row.item_id}`} withBorder padding="sm" radius="md">
              <Group justify="space-between" align="flex-start" wrap="wrap" gap="xs">
                <Stack gap={2}>
                  <Group gap="xs">
                    <Text fw={600}>{row.item_name}</Text>
                    <SourceTag row={row} />
                  </Group>
                  <Text size="xs" c="dimmed">
                    {quantityLabel(row)}
                    {row.sealed_count ? ` · ${row.sealed_count} sealed` : ""}
                    {row.location_name ? ` · ${row.location_name}` : ""}
                  </Text>
                </Stack>

                <Group gap="xs">
                  <Button
                    size="xs"
                    color="green"
                    loading={act.isPending}
                    onClick={() => act.mutate({ row, kind: "finished" })}
                  >
                    {row.lot_id ? "Finished it" : "Used one"}
                  </Button>
                  {row.lot_id ? (
                    <Button
                      size="xs"
                      color="orange"
                      variant="light"
                      loading={act.isPending}
                      onClick={() => act.mutate({ row, kind: "wasted" })}
                    >
                      Threw it out
                    </Button>
                  ) : null}
                  {row.source === "expiring" ? (
                    <>
                      <Button
                        size="xs"
                        variant="light"
                        loading={act.isPending}
                        onClick={() => act.mutate({ row, kind: "still_good" })}
                      >
                        Still good
                      </Button>
                      <Button size="xs" variant="subtle" color="gray" onClick={() => askToIgnore(row)}>
                        Ignore
                      </Button>
                    </>
                  ) : null}
                </Group>
              </Group>
            </Card>
          ))}
        </Stack>
      </Card>

      {/* The one action that silences future signal rather than recording a
          fact, so the one action that asks first (ADR 0023 D4). */}
      <Modal opened={confirmOpen} onClose={confirmModal.close} title="Stop tracking this item's best-by?">
        <Stack gap="sm">
          <Text size="sm">
            {ignoreTarget?.item_name} will never appear here for expiring again — for spices and staples that keep
            well past their printed date. You can turn it back on from the item in Catalog.
          </Text>
          <Group justify="flex-end">
            <Button variant="subtle" onClick={confirmModal.close}>
              Cancel
            </Button>
            <Button color="orange" onClick={confirmIgnore}>
              Stop tracking it
            </Button>
          </Group>
        </Stack>
      </Modal>
    </Stack>
  );
}
