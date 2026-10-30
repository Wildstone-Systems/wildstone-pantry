import { Alert, Button, Checkbox, Group, Modal, Stack, Text } from "@mantine/core";
import { useEffect, useState } from "react";

export type IdentityChoice = {
  ingredientId: string;
  itemId: string;
  itemName: string;
  currentKey: string | null;
  targetKey: string | null;
  provenance?: string;
  version?: number | null;
  revision?: number;
};

export function IdentityConfirmationModal({
  choice,
  busy,
  onClose,
  onConfirm,
}: {
  choice: IdentityChoice | null;
  busy: boolean;
  onClose: () => void;
  onConfirm: (choice: IdentityChoice, replaceConfirmed: boolean) => void;
}) {
  const [replace, setReplace] = useState(false);
  useEffect(() => setReplace(false), [choice]);
  const conflict = choice?.provenance === "confirmed" && choice.currentKey !== choice.targetKey;
  return (
    <Modal
      opened={choice !== null}
      onClose={() => {
        if (!busy) onClose();
      }}
      title="Confirm ingredient identity"
    >
      {choice && (
        <Stack>
          <Alert color="yellow">
            This changes this product&apos;s identity for every recipe in your household. Confirm only if these are the
            same ingredient, including dietary and preparation differences.
          </Alert>
          <Text>
            {choice.itemName}: {choice.currentKey ?? "unassigned"} → {choice.targetKey ?? "unassigned"}
          </Text>
          <Text size="sm">
            Identity source: {choice.provenance ?? "legacy_unknown"}; version {choice.version ?? "unknown"}.
          </Text>
          {conflict && (
            <Checkbox
              label="Replace the previously confirmed identity"
              checked={replace}
              onChange={(event) => setReplace(event.currentTarget.checked)}
            />
          )}
          <Group justify="flex-end">
            <Button variant="default" disabled={busy} onClick={onClose}>
              Cancel identity change
            </Button>
            <Button
              loading={busy}
              disabled={!choice.targetKey || (conflict && !replace)}
              onClick={() => onConfirm(choice, replace)}
            >
              Confirm global identity
            </Button>
          </Group>
        </Stack>
      )}
    </Modal>
  );
}
