import {
  ActionIcon,
  Alert,
  Badge,
  Button,
  Card,
  Group,
  Loader,
  NumberInput,
  Select,
  Stack,
  Table,
  Text,
  Title,
} from "@mantine/core";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";

import { api } from "@/api/client";
import { getApiErrorMessage } from "@/api/errors";
import { useAppState } from "@/state/AppStateProvider";
import { ROUTES } from "@/routes/paths";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

type RetailerInfo = {
  retailer: string;
  senders: string[];
  estimated_count: number;
  latest_date: string | null;
};

type DiscoverResponse = {
  retailers: RetailerInfo[];
  error: string | null;
};

type EmailSummary = {
  message_id: string;
  retailer: string | null;
  sender: string;
  subject: string;
  date: string | null;
  snippet: string;
};

type ListResponse = {
  emails: EmailSummary[];
  total_available: number;
  error: string | null;
};

type ParsedLineItem = {
  description: string;
  product_name: string | null;
  quantity: string;
  unit: string;
  price: string | null;
  category: string | null;
};

type ReceiptPreview = {
  message_id: string;
  retailer: string | null;
  store_name: string | null;
  store_number: string | null;
  purchase_date: string | null;
  subtotal: string | null;
  tax: string | null;
  total: string | null;
  items: ParsedLineItem[];
  item_count: number;
  parse_method: string;
};

type PreviewBatchResponse = {
  previews: ReceiptPreview[];
  errors: string[];
};

type ImportResponse = {
  receipt_id: string | null;
  status: string;
  item_count: number;
  inventory_transactions: string[];
  error: string | null;
};

const RETAILER_LABELS: Record<string, string> = {
  kroger: "Kroger",
  walmart: "Walmart",
  target: "Target",
  costco: "Costco",
  amazon: "Amazon",
  instacart: "Instacart",
  sams_club: "Sam's Club",
  whole_foods: "Whole Foods",
  trader_joes: "Trader Joe's",
  aldi: "Aldi",
};

const RETAILER_OPTIONS = [
  { value: "", label: "All retailers" },
  ...Object.entries(RETAILER_LABELS).map(([value, label]) => ({ value, label })),
];

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function EmailReceiptsPage() {
  const { selectedLocationId, setStatus } = useAppState();
  const queryClient = useQueryClient();

  const [selectedRetailer, setSelectedRetailer] = useState("");
  const [maxAgeDays, setMaxAgeDays] = useState(90);
  const [selectedPreview, setSelectedPreview] = useState<ReceiptPreview | null>(null);
  const [importedIds, setImportedIds] = useState<Set<string>>(new Set());

  // ---- Discover ----
  const discoverQuery = useQuery({
    queryKey: ["emailReceipts", "discover", maxAgeDays],
    queryFn: async () => {
      const res = await api.get<DiscoverResponse>("/api/receipts/email/discover", {
        params: { max_age_days: maxAgeDays },
      });
      return res.data;
    },
  });

  // ---- List ----
  const listMutation = useMutation({
    mutationFn: async ({ retailer, maxAgeDays: days }: { retailer: string; maxAgeDays: number }) => {
      const res = await api.post<ListResponse>("/api/receipts/email/list", {
        retailer: retailer || null,
        max_age_days: days,
        max_results: 20,
      });
      return res.data;
    },
  });

  // ---- Batch Preview ----
  const previewMutation = useMutation({
    mutationFn: async ({ retailer, maxAgeDays: days }: { retailer: string; maxAgeDays: number }) => {
      const res = await api.post<PreviewBatchResponse>("/api/receipts/email/preview", {
        retailer: retailer || null,
        max_age_days: days,
        max_results: 10,
      });
      return res.data;
    },
  });

  // ---- Single Preview ----
  const singlePreviewMutation = useMutation({
    mutationFn: async (messageId: string) => {
      const res = await api.get<ReceiptPreview>(`/api/receipts/email/preview/${messageId}`);
      return res.data;
    },
  });

  // ---- Import ----
  const importMutation = useMutation({
    mutationFn: async ({ messageId, locationId }: { messageId: string; locationId: string }) => {
      const res = await api.post<ImportResponse>("/api/receipts/email/import", {
        message_id: messageId,
        location_id: locationId,
      });
      return res.data;
    },
    onSuccess: (data) => {
      setStatus(`Imported ${data.item_count} items into inventory`);
      queryClient.invalidateQueries({ queryKey: ["inventory"] });
      queryClient.invalidateQueries({ queryKey: ["items"] });
      if (data.receipt_id) {
        setImportedIds((prev) => new Set(prev).add(data.receipt_id!));
      }
    },
    onError: (error) => {
      setStatus(getApiErrorMessage(error, "Import failed"));
    },
  });

  const busy =
    listMutation.isPending ||
    previewMutation.isPending ||
    singlePreviewMutation.isPending ||
    importMutation.isPending;

  const previews = previewMutation.data?.previews ?? [];

  return (
    <Stack>
      <Group justify="space-between" align="baseline">
        <Title order={2}>Email Receipts</Title>
        <Button component={Link} to={ROUTES.ingest} variant="default" size="xs">
          Text Ingest
        </Button>
      </Group>

      {/* ── Discover retailers ── */}
      <Card withBorder>
        <Group justify="space-between" align="baseline">
          <Title order={3}>Connected Retailers</Title>
          <ActionIcon
            variant="subtle"
            onClick={() => discoverQuery.refetch()}
            loading={discoverQuery.isFetching}
          >
            🔄
          </ActionIcon>
        </Group>

        {discoverQuery.isPending && <Loader size="sm" />}

        {discoverQuery.error && (
          <Alert color="red" title="Connection Error">
            {getApiErrorMessage(discoverQuery.error, "Failed to connect to Gmail")}
          </Alert>
        )}

        {discoverQuery.data?.error && (
          <Alert color="yellow" title="Gmail Not Connected">
            {discoverQuery.data.error}
          </Alert>
        )}

        {discoverQuery.data?.retailers && discoverQuery.data.retailers.length === 0 && (
          <Text c="dimmed" size="sm">
            No receipt emails found in the last {maxAgeDays} days. Try increasing the date range or
            check that your Gmail account has purchase receipt emails.
          </Text>
        )}

        {discoverQuery.data?.retailers && discoverQuery.data.retailers.length > 0 && (
          <Group gap="xs" mt="xs">
            {discoverQuery.data.retailers.map((r) => (
              <Badge
                key={r.retailer}
                variant="light"
                size="lg"
                style={{ cursor: "pointer" }}
                onClick={() => setSelectedRetailer(r.retailer)}
              >
                {RETAILER_LABELS[r.retailer] || r.retailer} ({r.estimated_count})
              </Badge>
            ))}
          </Group>
        )}
      </Card>

      {/* ── Search controls ── */}
      <Card withBorder>
        <Title order={3} mb="xs">
          Find Receipts
        </Title>
        <Group align="flex-end">
          <Select
            value={selectedRetailer}
            onChange={(v) => setSelectedRetailer(v || "")}
            data={RETAILER_OPTIONS}
            label="Retailer"
            size="sm"
            style={{ width: 200 }}
          />
          <NumberInput
            value={maxAgeDays}
            onChange={(v) => setMaxAgeDays(typeof v === "number" ? v : 90)}
            label="Max age (days)"
            min={1}
            max={365}
            size="sm"
            style={{ width: 140 }}
          />
          <Button
            onClick={() => previewMutation.mutate({ retailer: selectedRetailer, maxAgeDays })}
            loading={previewMutation.isPending}
            size="sm"
          >
            Scan & Preview
          </Button>
        </Group>
      </Card>

      {/* ── Preview results ── */}
      {previewMutation.error && (
        <Alert color="red" title="Fetch Error">
          {getApiErrorMessage(previewMutation.error, "Failed to fetch receipt emails")}
        </Alert>
      )}

      {previewMutation.data?.errors && previewMutation.data.errors.length > 0 && (
        <Alert color="yellow" title="Some errors occurred">
          {previewMutation.data.errors.map((e, i) => (
            <Text key={i} size="sm">
              {e}
            </Text>
          ))}
        </Alert>
      )}

      {previews.length > 0 && (
        <Stack>
          <Title order={3}>
            Found {previews.length} Receipt{previews.length !== 1 ? "s" : ""}
          </Title>

          {previews.map((preview) => (
            <Card
              key={preview.message_id}
              withBorder
              style={{
                cursor: "pointer",
                borderColor:
                  selectedPreview?.message_id === preview.message_id ? "var(--mantine-color-blue-6)" : undefined,
              }}
              onClick={() => setSelectedPreview(preview)}
            >
              <Group justify="space-between">
                <div>
                  <Group gap="xs">
                    <Text fw={600}>
                      {preview.store_name || RETAILER_LABELS[preview.retailer || ""] || preview.retailer || "Unknown"}
                    </Text>
                    {preview.retailer && (
                      <Badge size="sm" variant="outline">
                        {RETAILER_LABELS[preview.retailer] || preview.retailer}
                      </Badge>
                    )}
                    <Badge size="sm" color="violet">
                      {preview.parse_method}
                    </Badge>
                  </Group>
                  <Text size="sm" c="dimmed">
                    {preview.purchase_date || "No date"} · {preview.item_count} items
                    {preview.total && ` · $${preview.total}`}
                  </Text>
                </div>
                <Group gap="xs">
                  {preview.subtotal && <Badge size="sm" variant="light">Sub: ${preview.subtotal}</Badge>}
                  {preview.tax && <Badge size="sm" variant="light" color="orange">Tax: ${preview.tax}</Badge>}
                </Group>
              </Group>
            </Card>
          ))}
        </Stack>
      )}

      {/* ── Selected receipt detail ── */}
      {selectedPreview && (
        <Card withBorder>
          <Group justify="space-between" align="baseline">
            <Title order={3}>Receipt Details</Title>
            <Group gap="xs">
              {!selectedLocationId ? (
                <Text size="sm" c="dimmed">
                  Select a location to import
                </Text>
              ) : (
                <Button
                  onClick={() =>
                    importMutation.mutate({
                      messageId: selectedPreview.message_id,
                      locationId: selectedLocationId,
                    })
                  }
                  loading={importMutation.isPending}
                  color="green"
                  size="sm"
                >
                  Import to Inventory
                </Button>
              )}
              <Button variant="subtle" size="xs" onClick={() => setSelectedPreview(null)}>
                Close
              </Button>
            </Group>
          </Group>

          <Group gap="xs" mb="sm">
            <Text size="sm">
              <strong>Store:</strong>{" "}
              {selectedPreview.store_name || RETAILER_LABELS[selectedPreview.retailer || ""] || "Unknown"}
            </Text>
            {selectedPreview.store_number && (
              <Text size="sm">
                <strong>Store #:</strong> {selectedPreview.store_number}
              </Text>
            )}
            <Text size="sm">
              <strong>Date:</strong> {selectedPreview.purchase_date || "Unknown"}
            </Text>
            <Text size="sm">
              <strong>Items:</strong> {selectedPreview.item_count}
            </Text>
          </Group>

          {(selectedPreview.subtotal || selectedPreview.tax || selectedPreview.total) && (
            <Group gap="xs" mb="sm">
              {selectedPreview.subtotal && (
                <Badge size="sm" variant="light">Subtotal: ${selectedPreview.subtotal}</Badge>
              )}
              {selectedPreview.tax && (
                <Badge size="sm" variant="light" color="orange">Tax: ${selectedPreview.tax}</Badge>
              )}
              {selectedPreview.total && (
                <Badge size="sm" variant="filled" color="green">Total: ${selectedPreview.total}</Badge>
              )}
            </Group>
          )}

          {selectedPreview.items.length > 0 ? (
            <Table.ScrollContainer minWidth={520}>
            <Table striped highlightOnHover>
              <Table.Thead>
                <Table.Tr>
                  <Table.Th>Item</Table.Th>
                  <Table.Th>Qty</Table.Th>
                  <Table.Th>Unit</Table.Th>
                  <Table.Th>Price</Table.Th>
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {selectedPreview.items.map((item, i) => (
                  <Table.Tr key={i}>
                    <Table.Td>
                      <Text size="sm" fw={item.product_name ? 500 : 400}>
                        {item.product_name || item.description}
                      </Text>
                      {item.product_name && item.description !== item.product_name && (
                        <Text size="xs" c="dimmed">{item.description}</Text>
                      )}
                    </Table.Td>
                    <Table.Td>
                      <Text size="sm">{item.quantity}</Text>
                    </Table.Td>
                    <Table.Td>
                      <Text size="sm">{item.unit}</Text>
                    </Table.Td>
                    <Table.Td>
                      <Text size="sm">{item.price ? `$${item.price}` : "—"}</Text>
                    </Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
            </Table.ScrollContainer>
          ) : (
            <Text c="dimmed" size="sm">
              No line items could be parsed from this receipt.
            </Text>
          )}
        </Card>
      )}

      {/* ── Import result ── */}
      {importMutation.data && (
        <Alert
          color={importMutation.data.error ? "red" : "green"}
          title={importMutation.data.error ? "Import Failed" : "Import Successful"}
        >
          {importMutation.data.error ? (
            importMutation.data.error
          ) : (
            <Stack gap="xs">
              <Text size="sm">
                Imported {importMutation.data.item_count} items into inventory.
              </Text>
              <Text size="xs" c="dimmed">
                Receipt ID: {importMutation.data.receipt_id}
              </Text>
              {importMutation.data.inventory_transactions.length > 0 && (
                <Text size="xs" c="dimmed">
                  {importMutation.data.inventory_transactions.length} inventory transactions created.
                </Text>
              )}
            </Stack>
          )}
        </Alert>
      )}
    </Stack>
  );
}