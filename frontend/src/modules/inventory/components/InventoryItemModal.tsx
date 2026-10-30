import { Alert, Avatar, Badge, Button, Divider, Group, Modal, Select, Stack, Text, TextInput } from "@mantine/core";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { api } from "@/api/client";
import { getApiErrorMessage } from "@/api/errors";
import { EditItemModal, type Item, useLocations } from "@/modules/catalog";

import type { LotLine, WithLocation } from "../hooks/scoped-hooks";

function sizeText(item: Item | undefined): string {
  if (!item) return "no size set";
  if (item.size_description) return item.size_description;
  if (item.size_quantity && item.size_unit) return `${item.size_quantity} ${item.size_unit}`;
  return "no size set";
}

function fmtQty(value: string | number): string {
  const n = Number(value);
  return Number.isFinite(n) ? String(n) : String(value);
}

/** Row-level editor for a single lot: set THIS lot's best-by date (relabels just
 *  this lot, not the item's other lots), or jump to the full catalog item editor. */
export function InventoryItemModal({
  lot,
  item,
  onClose,
}: {
  lot: WithLocation<LotLine> | null;
  item: Item | undefined;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const { data: locations } = useLocations();
  const [bestBy, setBestBy] = useState("");
  const [locationId, setLocationId] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [editItem, setEditItem] = useState(false);
  // Two destructive actions with genuinely different meanings — kept separate
  // so neither can be triggered by a mis-tap meant for the other.
  const [confirmRemove, setConfirmRemove] = useState(false);
  const [confirmSpoil, setConfirmSpoil] = useState(false);

  useEffect(() => {
    if (!lot) return;
    setError(null);
    setBestBy(lot.expiration_date ?? "");
    setLocationId(lot.location_id);
    setConfirmRemove(false);
    setConfirmSpoil(false);
  }, [lot]);

  // Fire-and-forget: refetch only the views this edit touches (not every scoped
  // query for every location), and never block the modal close on it — the tables
  // update reactively when the refetches land.
  const invalidateAll = () => {
    queryClient.invalidateQueries({ queryKey: ["scoped", "lots"] });
    queryClient.invalidateQueries({ queryKey: ["scoped", "inventory"] });
    queryClient.invalidateQueries({ queryKey: ["scoped", "transactions"] });
    queryClient.invalidateQueries({ queryKey: ["expiringItems"] });
  };

  // "Threw it away" — the everyday case: food went bad or got binned. Recorded
  // as WASTE so it shows on the Waste tab and in spoilage trends. Previously
  // this lived only behind the Lots & batches tab's Spoil action, which meant
  // leaving the On hand list to do the most common thing you'd want here.
  const spoil = useMutation({
    mutationFn: async () => {
      if (!lot) return;
      await api.post(`/api/inventory/lots/${lot.lot_id}/spoil`);
    },
    onSuccess: () => {
      onClose();
      invalidateAll();
    },
    onError: (err) => setError(getApiErrorMessage(err, "Could not discard that")),
  });

  // "Used it up" — the common good outcome: this container is empty because it
  // was eaten, not wasted or mis-added. Mirrors the same call in the Lots &
  // batches tab and the Track worklist; no confirm step, matching those.
  const finish = useMutation({
    mutationFn: async () => {
      if (!lot) return;
      await api.post(`/api/inventory/lots/${lot.lot_id}/finish`);
    },
    onSuccess: () => {
      onClose();
      invalidateAll();
    },
    onError: (e) => setError(getApiErrorMessage(e, "Could not finish that")),
  });

  // "Added by mistake" — drops the lot off the shelf with no 'used' or 'wasted'
  // record (a correction), unlike consuming or spoiling it.
  const removeCorrection = useMutation({
    mutationFn: async () => {
      if (!lot) return;
      await api.post(`/api/inventory/lots/${lot.lot_id}/remove`);
    },
    onSuccess: () => {
      onClose();
      invalidateAll();
    },
    onError: (e) => setError(getApiErrorMessage(e, "Could not remove")),
  });

  const save = useMutation({
    mutationFn: async () => {
      if (!lot) return;
      if (bestBy && bestBy !== (lot.expiration_date ?? "")) {
        await api.post(`/api/inventory/lots/${lot.lot_id}/relabel`, { expiration_date: bestBy });
      }
      // Moved to the right place — transfer this lot's stock to the new location
      // (a `transferred` ledger fact, not a use/waste). Fixes a mis-located add.
      if (locationId && locationId !== lot.location_id) {
        await api.post("/api/inventory/move", {
          item_id: lot.item_id,
          source_location_id: lot.location_id,
          target_location_id: locationId,
          quantity: lot.quantity_remaining,
          unit: lot.unit,
        });
      }
    },
    onSuccess: () => {
      onClose();
      invalidateAll();
    },
    onError: (e) => setError(getApiErrorMessage(e, "Could not save")),
  });

  return (
    <>
      <Modal opened={lot !== null && !editItem} onClose={onClose} title={lot?.item_name ?? "Item"} size="md">
        {lot ? (
          <Stack gap="md">
            <Group gap="sm" wrap="nowrap">
              <Avatar src={item?.image_url ?? undefined} radius="sm" size={44}>
                {lot.item_name.slice(0, 1)}
              </Avatar>
              <div style={{ minWidth: 0 }}>
                <Text fw={600} lineClamp={1}>
                  {lot.item_name}
                </Text>
                <Text size="sm" c="dimmed">
                  {sizeText(item)} · in {lot.location_name}
                </Text>
              </div>
            </Group>

            <Group gap="xs">
              <Badge variant="light" color="gray" radius="xl">
                {fmtQty(lot.quantity_remaining)} {lot.unit} on hand
              </Badge>
              {lot.status && lot.status !== "sealed" ? (
                <Badge variant="light" color="terracotta" radius="xl">
                  {lot.status}
                </Badge>
              ) : null}
              {lot.frozen ? (
                <Badge variant="light" color="sage" radius="xl">
                  frozen
                </Badge>
              ) : null}
            </Group>

            {error ? <Alert color="red">{error}</Alert> : null}

            <TextInput
              label="Best-by date"
              type="date"
              value={bestBy}
              onChange={(e) => setBestBy(e.currentTarget.value)}
              description="Applies to this lot only"
            />

            <Select
              label="Location"
              description="Move this stock if it was logged to the wrong place"
              data={(locations ?? []).map((l) => ({ value: l.id, label: l.name }))}
              value={locationId}
              onChange={(v) => setLocationId(v ?? lot.location_id)}
              allowDeselect={false}
              comboboxProps={{ withinPortal: true }}
            />

            <Group justify="space-between" mt="sm">
              <Button variant="default" onClick={() => setEditItem(true)}>
                Edit item details…
              </Button>
              <Group gap="sm">
                <Button variant="subtle" onClick={onClose}>
                  Cancel
                </Button>
                <Button loading={save.isPending} onClick={() => save.mutate()}>
                  Save
                </Button>
              </Group>
            </Group>

            <Divider mt="xs" />
            {confirmSpoil ? (
              <Alert color="orange" variant="light">
                <Text size="sm" mb="xs">
                  Throw away all {fmtQty(lot.quantity_remaining)} {lot.unit}? It comes off the shelf and is
                  recorded as waste.
                </Text>
                <Group gap="sm">
                  <Button size="xs" color="orange" loading={spoil.isPending} onClick={() => spoil.mutate()}>
                    Yes, throw it away
                  </Button>
                  <Button size="xs" variant="subtle" onClick={() => setConfirmSpoil(false)}>
                    Keep it
                  </Button>
                </Group>
              </Alert>
            ) : confirmRemove ? (
              <Alert color="red" variant="light">
                <Text size="sm" mb="xs">
                  Remove all {fmtQty(lot.quantity_remaining)} {lot.unit}? This drops it off the shelf as a
                  correction — it won't count as used or wasted.
                </Text>
                <Group gap="sm">
                  <Button size="xs" color="red" loading={removeCorrection.isPending} onClick={() => removeCorrection.mutate()}>
                    Yes, remove it
                  </Button>
                  <Button size="xs" variant="subtle" onClick={() => setConfirmRemove(false)}>
                    Keep it
                  </Button>
                </Group>
              </Alert>
            ) : (
              <Group gap="sm">
                <Button
                  variant="light"
                  color="green"
                  size="compact-sm"
                  loading={finish.isPending}
                  onClick={() => finish.mutate()}
                  aria-label={`Mark ${item?.name ?? "this item"} used up`}
                >
                  ✓ Used it up
                </Button>
                <Button
                  variant="light"
                  color="orange"
                  size="compact-sm"
                  onClick={() => setConfirmSpoil(true)}
                  aria-label={`Throw away ${item?.name ?? "this item"}`}
                >
                  🗑 Threw it away
                </Button>
                <Button variant="subtle" color="red" size="compact-sm" onClick={() => setConfirmRemove(true)}>
                  Remove — added by mistake
                </Button>
              </Group>
            )}
          </Stack>
        ) : null}
      </Modal>

      {editItem ? (
        <EditItemModal
          item={item ?? null}
          onClose={() => setEditItem(false)}
          onSaved={() => {
            queryClient.invalidateQueries({ queryKey: ["items"] });
            setEditItem(false);
          }}
        />
      ) : null}
    </>
  );
}
