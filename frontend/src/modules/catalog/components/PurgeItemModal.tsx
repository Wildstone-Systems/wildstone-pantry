import { Alert, Button, Group, List, Loader, Modal, Stack, Text, TextInput } from "@mantine/core";
import { useEffect, useState } from "react";

import { getApiErrorMessage } from "@/api/errors";
import { useAppState } from "@/state/AppStateProvider";

import type { Item } from "../hooks/catalog-hooks";
import { useItemUsage, usePurgeItem } from "../hooks/catalog-hooks";

type Props = {
  item: Item | null;
  onClose: () => void;
};

/** Confirmation for permanently deleting an item.
 *
 * Two things this does that a plain "Are you sure?" does not: it states what
 * will actually be destroyed, in counts rather than adjectives, and it makes
 * the operator type the item's name. The typing isn't ceremony — it's what
 * separates a deliberate purge from a mis-aimed click on the row below.
 */
export function PurgeItemModal({ item, onClose }: Props) {
  const { setStatus } = useAppState();
  const usage = useItemUsage(item?.id ?? null);
  const purge = usePurgeItem();
  const [typed, setTyped] = useState("");

  useEffect(() => {
    setTyped("");
  }, [item?.id]);

  if (!item) return null;

  const matches = typed.trim() === item.name;
  const counts = usage.data;
  const destroysHistory = Boolean(
    counts && (counts.ledger_events > 0 || counts.recipe_ingredients > 0),
  );

  const handlePurge = async () => {
    try {
      await purge.mutateAsync({ id: item.id, confirmName: typed.trim() });
      setStatus(`Permanently deleted ${item.name}`);
      onClose();
    } catch (err) {
      setStatus(getApiErrorMessage(err, "Could not delete that item"));
    }
  };

  return (
    <Modal opened onClose={onClose} title="Permanently delete this item?" centered>
      <Stack gap="sm">
        <Text size="sm">
          <b>{item.name}</b> and everything recorded about it will be removed. This cannot be
          undone — archiving is the reversible option.
        </Text>

        {usage.isLoading ? (
          <Group gap="xs">
            <Loader size="xs" />
            <Text size="sm" c="dimmed">
              Checking what depends on it…
            </Text>
          </Group>
        ) : counts ? (
          <Alert color={destroysHistory ? "red" : "gray"} title="This will also delete">
            <List size="sm" spacing={2}>
              <List.Item>
                {counts.ledger_events} inventory event{counts.ledger_events === 1 ? "" : "s"}
                {counts.ledger_events > 0 && " — purchases, meals and adjustments"}
              </List.Item>
              <List.Item>
                {counts.lots} stock lot{counts.lots === 1 ? "" : "s"}
                {counts.on_hand > 0 && ` (${counts.on_hand} still on hand)`}
              </List.Item>
              <List.Item>
                {counts.recipe_ingredients} recipe ingredient
                {counts.recipe_ingredients === 1 ? "" : "s"}
                {counts.recipe_ingredients > 0 && " — those recipes lose the line"}
              </List.Item>
            </List>
            {destroysHistory && (
              <Text size="xs" mt="xs">
                Past totals that included this item will change. Archiving keeps all of it.
              </Text>
            )}
          </Alert>
        ) : null}

        <TextInput
          label={`Type "${item.name}" to confirm`}
          value={typed}
          onChange={(e) => setTyped(e.currentTarget.value)}
          placeholder={item.name}
          autoComplete="off"
          data-autofocus
        />

        <Group justify="flex-end">
          <Button variant="default" onClick={onClose}>
            Cancel
          </Button>
          <Button
            color="red"
            disabled={!matches}
            loading={purge.isPending}
            onClick={handlePurge}
          >
            Delete permanently
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}
