import {
  Button,
  Card,
  Group,
  ScrollArea,
  Select,
  Stack,
  Table,
  Text,
  Title,
} from "@mantine/core";
import { useEffect, useMemo, useState } from "react";

import { LocationPill } from "@/components/LocationPill";

import type { PurchaseDetailResponse, PurchaseRead } from "../hooks/shopping-hooks";
import { usePurchases, usePurchaseDetail } from "../hooks/shopping-hooks";

export function PurchasesPage() {
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

  return (
    <Stack>
      <Group justify="space-between">
        <Title order={2}>Purchases</Title>
        <LocationPill />
      </Group>

      <Card withBorder>
        <Group justify="space-between" align="flex-end" wrap="wrap" mb="xs">
          <Title order={3}>Purchase history</Title>
          <Group>
            <Button variant="default" onClick={() => purchasesQuery.refetch()} loading={purchasesQuery.isFetching}>
              Refresh
            </Button>
            <Select
              aria-label="Select purchase"
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
            : "No purchase selected."}
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
                      No lines recorded for this purchase. Purchases are recorded when you complete a store hand-off.
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
