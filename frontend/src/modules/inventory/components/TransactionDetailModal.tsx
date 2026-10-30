import { Alert, Badge, Button, Group, Modal, Stack, Text } from "@mantine/core";
import { useEffect, useState } from "react";

import { getApiErrorMessage } from "@/api/errors";
import { useAppState } from "@/state/AppStateProvider";

import type { TransactionLine } from "../hooks/inventory-hooks";
import { useReverseTransaction, type WithLocation } from "../hooks/scoped-hooks";

function fmtQty(value: string | number): string {
  const n = Number(value);
  return Number.isFinite(n) ? String(n) : String(value);
}

function DetailRow({ label, value }: { label: string; value: string }) {
  return (
    <Group gap={6} wrap="nowrap">
      <Text size="sm" c="dimmed" style={{ minWidth: 90 }}>
        {label}
      </Text>
      <Text size="sm">{value}</Text>
    </Group>
  );
}

/** Log-row detail: shows the transaction's fields and, for anything that isn't
 * itself a reversal, a "Reverse this entry" button. Reversal is additive — it
 * posts a compensating entry rather than editing or deleting history. */
export function TransactionDetailModal({
  transaction,
  onClose,
}: {
  transaction: WithLocation<TransactionLine> | null;
  onClose: () => void;
}) {
  const { setStatus } = useAppState();
  const [error, setError] = useState<string | null>(null);
  const reverse = useReverseTransaction();

  useEffect(() => {
    setError(null);
  }, [transaction]);

  const isReversal = transaction?.reason === "reversal";

  const handleReverse = () => {
    if (!transaction) return;
    setError(null);
    reverse.mutate(transaction.transaction_id, {
      onSuccess: () => {
        setStatus("Transaction reversed");
        onClose();
      },
      onError: (e) => setError(getApiErrorMessage(e, "Could not reverse this entry")),
    });
  };

  return (
    <Modal opened={transaction !== null} onClose={onClose} title="Transaction detail" size="md">
      {transaction ? (
        <Stack gap="md">
          <Group justify="space-between" wrap="nowrap">
            <Text fw={600} lineClamp={1}>
              {transaction.item_name}
            </Text>
            <Badge variant="light" color="gray" radius="xl">
              {transaction.transaction_type}
            </Badge>
          </Group>

          <Stack gap={4}>
            <DetailRow label="Time" value={new Date(transaction.created_at).toLocaleString()} />
            <DetailRow label="Location" value={transaction.location_name} />
            <DetailRow label="Quantity" value={`${fmtQty(transaction.quantity_change)} ${transaction.unit}`} />
            <DetailRow label="Reason" value={transaction.reason ?? "—"} />
            <DetailRow label="Note" value={transaction.note ?? "—"} />
          </Stack>

          {error ? <Alert color="red">{error}</Alert> : null}

          {isReversal ? (
            <Text size="xs" c="dimmed">
              This is a reversal entry.
            </Text>
          ) : (
            <Text size="xs" c="dimmed">
              Adds a compensating entry — history is never deleted.
            </Text>
          )}

          <Group justify="flex-end">
            <Button variant="subtle" onClick={onClose}>
              Close
            </Button>
            <Button
              variant="outline"
              color="terracotta"
              disabled={isReversal}
              loading={reverse.isPending}
              onClick={handleReverse}
            >
              Reverse this entry
            </Button>
          </Group>
        </Stack>
      ) : null}
    </Modal>
  );
}
