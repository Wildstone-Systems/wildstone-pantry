import {
  Button,
  Checkbox,
  Card,
  Group,
  Stack,
  Table,
  Text,
  TextInput,
  Textarea,
  Title,
  NumberInput,
  Select,
} from "@mantine/core";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";
import { api } from "@/api/client";
import { getApiErrorMessage } from "@/api/errors";
import type {
  BarcodeLookupApiResponse,
  ReceiptCommitApiResponse,
  ReceiptPreviewApiResponse,
  ReceiptPreviewLine,
} from "@/api/hooks";
import { ClassifierVerificationNotice } from "@/components/ClassifierVerificationNotice";
import { LocationPill } from "@/components/LocationPill";
import { useItems } from "@/modules/catalog";
import { useAppState } from "@/state/AppStateProvider";

type Annotation = {
  line_id: string;
  status: string;
  role?: string | null;
  suggested_item_id?: string | null;
  score?: number | null;
  below_threshold?: boolean;
  threshold?: number;
  candidates: { id: string; name: string }[];
};
type ReviewLine = ReceiptPreviewLine & {
  create_new: boolean;
  line_id: string;
  role: string;
  excluded: boolean;
  confirmed: boolean;
  source?: string;
  confidence?: number | null;
  annotation?: Annotation | null;
};
type DraftPreview = Omit<ReceiptPreviewApiResponse, "lines"> & { draft_id: string; token: string; lines: ReviewLine[] };
type Review = {
  lines: ReviewLine[];
  locationId: string;
  rawText: string;
  filename: string | null;
  draftId: string;
  token: string;
};
const UNITS = ["each", "ea", "lb", "oz", "g", "kg", "ml", "l"];

export function IngestPage() {
  const { selectedLocationId, setStatus } = useAppState();
  const queryClient = useQueryClient();
  const { data: items = [] } = useItems();
  const [barcode, setBarcode] = useState("");
  const [barcodeResult, setBarcodeResult] = useState<BarcodeLookupApiResponse | null>(null);
  const [rawText, setRawText] = useState("");
  const [review, setReview] = useState<Review | null>(null);
  const locationRef = useRef(selectedLocationId);
  locationRef.current = selectedLocationId;
  const previewVersion = useRef(0);
  const barcodeLookup = useMutation({
    mutationFn: async () => {
      if (!barcode.trim()) throw new Error("Enter a barcode to look up");
      return (await api.get<BarcodeLookupApiResponse>(`/api/barcodes/lookup/${encodeURIComponent(barcode.trim())}`))
        .data;
    },
    onSuccess: (data) => {
      setBarcodeResult(data);
      setStatus(data.found ? `Found item: ${data.item_name ?? data.barcode}` : `Unknown barcode: ${data.barcode}`);
    },
    onError: (error) => setStatus(getApiErrorMessage(error, "Barcode lookup failed")),
  });
  const previewReceipt = useMutation({
    mutationFn: async (file?: File) => {
      if (!selectedLocationId) throw new Error("Select a location first");
      const locationId = selectedLocationId;
      const version = ++previewVersion.current;
      let data: DraftPreview;
      if (file) {
        const form = new FormData();
        form.append("file", file);
        data = (
          await api.post<DraftPreview>(`/api/receipts/draft-image?location_id=${encodeURIComponent(locationId)}`, form)
        ).data;
      } else {
        data = (
          await api.post<DraftPreview>("/api/receipts/draft", {
            raw_text: rawText,
            location_id: locationId,
            source_filename: null,
          })
        ).data;
      }
      return {
        data,
        locationId,
        version,
        rawText: file ? data.lines.map((line) => line.raw_line).join("\n") : rawText,
        filename: file?.name ?? null,
      };
    },
    onSuccess: ({ data, locationId, version, rawText: sourceText, filename }) => {
      if (locationRef.current !== locationId || previewVersion.current !== version) {
        setStatus("Location changed. Preview the receipt again before committing.");
        return;
      }
      setReview({
        lines: data.lines.map((line) => ({ ...line, create_new: false, confirmed: false, excluded: false })),
        draftId: data.draft_id,
        token: data.token,
        locationId,
        rawText: sourceText,
        filename,
      });
      setStatus(`Preview: ${data.line_count} lines (review before committing)`);
    },
    onError: (error) => setStatus(getApiErrorMessage(error, "Preview failed")),
  });
  const lines = review?.lines ?? [];
  const validLines =
    lines.some((line) => !line.excluded) &&
    lines.every((line) => {
      if (line.excluded) return true;
      const quantity = Number(line.quantity);
      return (
        line.confirmed &&
        line.role === "stock" &&
        line.name.trim() &&
        Number.isFinite(quantity) &&
        quantity > 0 &&
        quantity < 1e9 &&
        /^\d+(?:\.\d{1,3})?$/.test(line.quantity) &&
        UNITS.includes(line.unit) &&
        Boolean(line.matched_item_id) !== line.create_new
      );
    });
  const staleReview = !!review && review.locationId !== selectedLocationId;
  const commitReceipt = useMutation({
    mutationFn: async () => {
      if (!review || !validLines || staleReview || locationRef.current !== review.locationId)
        throw new Error("Review every line and preview again if the location changed");
      return (
        await api.post<ReceiptCommitApiResponse>(`/api/receipts/drafts/${review.draftId}/commit`, {
          token: review.token,
          raw_text: review.rawText,
          location_id: review.locationId,
          source_filename: review.filename,
          lines: review.lines,
        })
      ).data;
    },
    onSuccess: (data) => {
      setReview(null);
      setRawText("");
      setStatus(`Committed ${data.line_count} items to inventory`);
      queryClient.invalidateQueries({ queryKey: ["inventory"] });
      queryClient.invalidateQueries({ queryKey: ["items"] });
    },
    onError: (error) => setStatus(getApiErrorMessage(error, "Commit failed")),
  });
  const classifierReview = useMutation({
    mutationFn: async () => {
      if (!review) throw new Error("Preview a receipt first");
      const captured = review;
      const version = previewVersion.current;
      const data = (
        await api.post<{ annotations: Annotation[]; provider_requests: number; status?: string }>(
          `/api/receipts/drafts/${review.draftId}/review`,
          { token: review.token, lines: review.lines }
        )
      ).data;
      return { data, captured, version };
    },
    onSuccess: ({ data, captured, version }) => {
      if (locationRef.current !== captured.locationId || version !== previewVersion.current) return;
      setReview((current) =>
        current?.draftId === captured.draftId
          ? {
              ...current,
              lines: current.lines.map((line) => ({
                ...line,
                annotation: data.annotations.find((a) => a.line_id === line.line_id) ?? null,
              })),
            }
          : current
      );
      setStatus(
        data.status === "stale_configuration" || data.status === "stale_source"
          ? "Classifier settings or source details changed. Suggestions discarded; review manually."
          : data.status === "unavailable"
            ? "Classifier settings are unavailable. You can finish this receipt manually."
            : `Classifier review returned (${data.provider_requests} requests). Suggestions require your confirmation.`
      );
    },
    onError: () => setStatus("Classifier unavailable or admin sign-in required. You can finish this receipt manually."),
  });
  const busy =
    classifierReview.isPending || barcodeLookup.isPending || previewReceipt.isPending || commitReceipt.isPending;
  const updateLine = (index: number, changes: Partial<ReviewLine>) =>
    setReview((previous) =>
      previous
        ? {
            ...previous,
            lines: previous.lines.map((line, i) =>
              i === index ? { ...line, ...changes, annotation: null, confirmed: changes.confirmed ?? false } : line
            ),
          }
        : null
    );
  const clearReview = () => {
    previewVersion.current++;
    setReview(null);
  };
  return (
    <Stack>
      <Group justify="space-between">
        <Title order={2}>Ingest</Title>
        <LocationPill />
      </Group>
      <Group align="flex-start" grow>
        <Card withBorder>
          <Title order={3}>Barcode Lookup</Title>
          <Stack gap="sm">
            <TextInput
              aria-label="Barcode"
              value={barcode}
              onChange={(e) => setBarcode(e.currentTarget.value)}
              placeholder="Scan or type UPC/EAN"
              inputMode="numeric"
              disabled={busy}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !busy) {
                  e.preventDefault();
                  barcodeLookup.mutate();
                }
              }}
            />
            <Text size="sm" c="dimmed">
              Click the field and scan with a Bluetooth barcode scanner, or enter a barcode.
            </Text>
            <Button onClick={() => barcodeLookup.mutate()} disabled={busy} loading={barcodeLookup.isPending}>
              Lookup Barcode
            </Button>
            {barcodeResult && (
              <Text>
                Barcode: {barcodeResult.barcode} · Item: {barcodeResult.item_name ?? "Unknown"}
              </Text>
            )}
          </Stack>
        </Card>
        <Card withBorder>
          <Title order={3}>Receipt Ingestion</Title>
          <Stack gap="sm">
            <Text size="sm" c="dimmed">
              Upload or capture an image, then review before adding to inventory.
            </Text>
            <input
              aria-label="Receipt image"
              type="file"
              accept="image/*"
              capture="environment"
              disabled={!selectedLocationId || busy}
              onChange={(e) => {
                const file = e.currentTarget.files?.[0];
                if (file) previewReceipt.mutate(file);
                e.currentTarget.value = "";
              }}
            />
            {!selectedLocationId && <Text>Choose a destination using the location selector above.</Text>}
            <Textarea
              aria-label="Receipt text"
              value={rawText}
              onChange={(e) => {
                setRawText(e.currentTarget.value);
                clearReview();
              }}
              minRows={8}
              placeholder={"milk 2 each\nbananas 6 each"}
              disabled={busy}
            />
            {staleReview && <Text c="red">Location changed. Preview again before committing.</Text>}
            {review && !validLines && (
              <Text c="dimmed">
                Choose an existing item or Create new for every line, with a positive quantity (up to three decimal
                places).
              </Text>
            )}
            <Group>
              {review && (
                <Button variant="light" disabled={busy || staleReview} onClick={() => classifierReview.mutate()}>
                  Ask classifier (admin)
                </Button>
              )}
              <Button
                onClick={() => previewReceipt.mutate(undefined)}
                disabled={busy || !selectedLocationId || !rawText.trim()}
                loading={previewReceipt.isPending}
              >
                Preview
              </Button>
              {review && (
                <>
                  <Button
                    onClick={() => commitReceipt.mutate()}
                    loading={commitReceipt.isPending}
                    disabled={busy || !validLines || staleReview}
                    color="green"
                  >
                    Commit to Inventory
                  </Button>
                  <Button onClick={clearReview} disabled={busy} variant="subtle" color="red">
                    Cancel review
                  </Button>
                </>
              )}
            </Group>
          </Stack>
        </Card>
      </Group>
      <Card withBorder>
        <Title order={3}>Review &amp; Edit Before Commit</Title>
        {review && <ClassifierVerificationNotice />}
        {review && (
          <Text size="sm">
            Optional classifier review examines up to 8 ambiguous lines per click. Candidate search uses at most 50
            recent catalog items; choose any item manually. No suggestion changes your choices.
          </Text>
        )}
        {review?.filename && <Text size="sm">Source: {review.filename}</Text>}
        <Table.ScrollContainer minWidth={800}>
          <Table striped highlightOnHover withTableBorder>
            <Table.Thead>
              <Table.Tr>
                {["Source and review", "Item Name", "Qty", "Unit", "Catalog item"].map((label) => (
                  <Table.Th key={label}>{label}</Table.Th>
                ))}
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {lines.map((line, idx) => (
                <Table.Tr key={idx}>
                  <Table.Td>
                    <Text>{line.raw_line}</Text>
                    <Text size="xs">
                      Extraction: {line.source ?? "rules"}
                      {line.confidence != null ? ` (${Math.round(line.confidence * 100)}% extraction score)` : ""}
                    </Text>
                    <Select
                      aria-label={`Role ${idx + 1}`}
                      value={line.role}
                      disabled={busy || line.excluded}
                      data={[
                        { value: "stock", label: "Stock purchase" },
                        { value: "financial_adjustment", label: "Financial adjustment" },
                        { value: "return", label: "Return / refund" },
                        { value: "unknown", label: "Unknown / insufficient evidence" },
                      ]}
                      onChange={(role) => updateLine(idx, { role: role ?? "unknown" })}
                    />
                    <Checkbox
                      label={`I verified line ${idx + 1}`}
                      checked={line.confirmed}
                      disabled={busy || line.excluded}
                      onChange={(e) => updateLine(idx, { confirmed: e.currentTarget.checked })}
                    />
                    {line.excluded && <Text>Excluded — will not add stock</Text>}
                    {line.annotation && (
                      <Stack gap={2}>
                        <Text size="sm">
                          Classifier: {line.annotation.status}
                          {line.annotation.role ? ` · suggested role: ${line.annotation.role}` : ""}
                        </Text>
                        {line.annotation.score != null && (
                          <Text c={line.annotation.below_threshold ? "orange" : undefined} size="sm">
                            Model score: {Math.round(line.annotation.score * 100)}%
                            {line.annotation.below_threshold
                              ? ` — below your ${Math.round((line.annotation.threshold ?? 0.9) * 100)}% threshold; verify carefully`
                              : " — human verification still required"}
                          </Text>
                        )}
                        {line.annotation.score == null && <Text size="sm">No usable score. Review manually.</Text>}
                        {line.annotation.candidates.map((c) => (
                          <Text size="xs" key={c.id}>
                            {c.id === line.annotation?.suggested_item_id ? "Suggested match: " : "Candidate: "}
                            {c.name}
                          </Text>
                        ))}
                      </Stack>
                    )}
                  </Table.Td>
                  <Table.Td>
                    <TextInput
                      aria-label={`Item name ${idx + 1}`}
                      disabled={busy || line.excluded}
                      value={line.name}
                      onChange={(e) => updateLine(idx, { name: e.currentTarget.value })}
                      size="xs"
                    />
                  </Table.Td>
                  <Table.Td>
                    <NumberInput
                      aria-label={`Quantity ${idx + 1}`}
                      disabled={busy || line.excluded}
                      value={line.quantity}
                      onChange={(value) => updateLine(idx, { quantity: String(value) })}
                      min={0}
                      step={0.5}
                      size="xs"
                      style={{ width: 95 }}
                    />
                  </Table.Td>
                  <Table.Td>
                    <Select
                      aria-label={`Unit ${idx + 1}`}
                      disabled={busy || line.excluded}
                      value={line.unit}
                      onChange={(value) => updateLine(idx, { unit: value ?? "each" })}
                      data={UNITS}
                      size="xs"
                      style={{ width: 80 }}
                    />
                  </Table.Td>
                  <Table.Td>
                    <Select
                      aria-label={`Catalog item ${idx + 1}`}
                      placeholder="Choose item or Create new"
                      disabled={busy || line.excluded}
                      searchable
                      value={line.create_new ? "__new__" : line.matched_item_id}
                      data={[
                        { value: "__new__", label: "Create new item" },
                        ...items.map((item) => ({
                          value: item.id,
                          label: item.brand ? `${item.name} (${item.brand})` : item.name,
                        })),
                        ...(line.matched_item_id && !items.some((item) => item.id === line.matched_item_id)
                          ? [{ value: line.matched_item_id, label: line.matched_item_name ?? line.name }]
                          : []),
                      ]}
                      onChange={(value) =>
                        updateLine(idx, {
                          matched_item_id: value === "__new__" ? null : value,
                          create_new: value === "__new__",
                        })
                      }
                    />
                    <Button
                      variant="subtle"
                      color="red"
                      disabled={busy}
                      onClick={() => updateLine(idx, { excluded: !line.excluded })}
                    >
                      {line.excluded ? `Restore line ${idx + 1}` : `Exclude line ${idx + 1}`}
                    </Button>
                  </Table.Td>
                </Table.Tr>
              ))}
              {lines.length === 0 && (
                <Table.Tr>
                  <Table.Td colSpan={5}>
                    <Text c="dimmed">No receipt lines to review. Enter text or upload a photo to preview.</Text>
                  </Table.Td>
                </Table.Tr>
              )}
            </Table.Tbody>
          </Table>
        </Table.ScrollContainer>
      </Card>
    </Stack>
  );
}
