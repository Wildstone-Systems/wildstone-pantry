import {
  Button,
  Card,
  Checkbox,
  Group,
  ScrollArea,
  Select,
  Stack,
  Table,
  Text,
  TextInput,
  Textarea,
  Title,
} from "@mantine/core";
import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";

import { ROUTES } from "@/routes/paths";
import { LocationPill } from "@/components/LocationPill";
import { useAppState } from "@/state/AppStateProvider";

import type { PurchaseDetailResponse, PurchaseRead } from "../hooks/shopping-hooks";
import { usePurchases, usePurchaseDetail, useIngestReceiptImage } from "../hooks/shopping-hooks";

import { ReviewedPurchaseCsvCard } from "../components/ReviewedPurchaseCsvCard";

export function PurchasesPage() {
  const queryClient = useQueryClient();
  const { selectedLocationId, setStatus } = useAppState();

  const purchasesQuery = usePurchases(25);
  const purchases = purchasesQuery.data ?? [];

  const [selectedPurchaseId, setSelectedPurchaseId] = useState<string | null>(null);

  useEffect(() => {
    if (selectedPurchaseId) return;
    if (purchases.length === 0) return;
    setSelectedPurchaseId(purchases[0].id);
  }, [purchases, selectedPurchaseId]);

  const purchaseDetailQuery = usePurchaseDetail(selectedPurchaseId);
  const purchaseDetail: PurchaseDetailResponse | null = purchaseDetailQuery.data ?? null;

  const selectedPurchase: PurchaseRead | null = useMemo(() => {
    if (!selectedPurchaseId) return null;
    return purchases.find((row) => row.id === selectedPurchaseId) ?? null;
  }, [purchases, selectedPurchaseId]);

  const receiptImageMutation = useIngestReceiptImage();

  const busy = receiptImageMutation.isPending;

  return (
    <Stack>
      <Group justify="space-between">
        <Title order={2}>Purchases</Title>
        <LocationPill />
      </Group>

      <ReviewedPurchaseCsvCard />

      <Card withBorder>
        <Title order={3} mb="xs">
          Receipt Image Upload
        </Title>
        <Text size="sm" c="dimmed" mb="sm">
          Upload a receipt photo to automatically add items to inventory (uses the selected location).
        </Text>
        <Stack gap="sm">
          <input
            type="file"
            accept="image/*"
            capture="environment"
            onChange={(e) => {
              const file = e.currentTarget.files?.[0] ?? null;
              if (!file) return;
              receiptImageMutation.mutate({ locationId: selectedLocationId!, file });
              e.currentTarget.value = "";
            }}
            disabled={!selectedLocationId || busy}
          />
          {!selectedLocationId ? (
            <Text size="sm" c="dimmed">
              Select a location first (top-right).
            </Text>
          ) : null}
          <Group>
            <Text size="sm" c="dimmed">
              Need text import instead?
            </Text>
            <Button component={Link} to={ROUTES.ingest} variant="default" size="xs">
              Ingest
            </Button>
          </Group>
        </Stack>
      </Card>

      <Card withBorder>
        <Group justify="space-between" align="flex-end" wrap="wrap" mb="xs">
          <Title order={3}>Purchase Imports</Title>
          <Group>
            <Button variant="default" onClick={() => purchasesQuery.refetch()} loading={purchasesQuery.isFetching}>
              Refresh
            </Button>
            <Select
              aria-label="Select purchase import"
              data={purchases.map((purchase) => ({
                value: purchase.id,
                label: `${purchase.purchased_on ? `${purchase.purchased_on} ` : ""}${purchase.merchant ? `${purchase.merchant} ` : ""}(${purchase.line_count} lines)`,
              }))}
              value={selectedPurchaseId}
              onChange={(value) => setSelectedPurchaseId(value)}
              disabled={purchases.length === 0}
              allowDeselect={false}
              w={320}
            />
          </Group>
        </Group>

        <Text size="sm" c="dimmed" mb="sm">
          {selectedPurchase
            ? `Selected: ${selectedPurchase.id} | status: ${selectedPurchase.status} | created: ${new Date(selectedPurchase.created_at).toLocaleString()}`
            : "No import selected."}
        </Text>

        <ScrollArea>
          <Table striped highlightOnHover withTableBorder>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Raw</Table.Th>
                <Table.Th>Parsed</Table.Th>
                <Table.Th>Qty</Table.Th>
                <Table.Th>Barcode</Table.Th>
                <Table.Th>Match</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {(purchaseDetail?.lines || []).map((line) => (
                <Table.Tr key={line.line_item_id}>
                  <Table.Td>{line.raw_line}</Table.Td>
                  <Table.Td>{line.parsed_name ?? "-"}</Table.Td>
                  <Table.Td>
                    {String(line.parsed_quantity)} {line.parsed_unit}
                  </Table.Td>
                  <Table.Td>{line.parsed_barcode ?? "-"}</Table.Td>
                  <Table.Td>{line.matched_item_name ?? "-"}</Table.Td>
                </Table.Tr>
              ))}
              {(purchaseDetail?.lines || []).length === 0 ? (
                <Table.Tr>
                  <Table.Td colSpan={5}>
                    <Text size="sm" c="dimmed">
                      No purchase lines to display for the selected import.
                    </Text>
                  </Table.Td>
                </Table.Tr>
              ) : null}
            </Table.Tbody>
          </Table>
        </ScrollArea>
      </Card>
    </Stack>
  );
}
