import {
  Alert,
  Button,
  Card,
  Checkbox,
  Group,
  ScrollArea,
  Select,
  Stack,
  Table,
  Text,
  Textarea,
  TextInput,
  Title,
} from "@mantine/core";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "@/api/client";
import { getApiErrorMessage } from "@/api/errors";
import { ClassifierVerificationNotice } from "@/components/ClassifierVerificationNotice";
import { useItems } from "@/modules/catalog";
import { ROUTES } from "@/routes/paths";
import { useAppState } from "@/state/AppStateProvider";
import { useAuth } from "@/state/AuthProvider";
import type { PurchaseImportApiResponse } from "../hooks/shopping-hooks";

type Annotation = {
  line_id: string;
  status: string;
  score?: number | null;
  below_threshold?: boolean;
  role?: string | null;
  candidates: { id: string; name: string }[];
  suggested_item_id?: string | null;
};
type Line = {
  line_id: string;
  raw_line: string;
  name: string;
  quantity: string;
  unit: string;
  barcode: string | null;
  matched_item_id: string | null;
  matched_item_name: string | null;
  matched_item_brand?: string | null;
  create_new: boolean;
  role: string;
  excluded: boolean;
  confirmed: boolean;
  source_quantity: string;
  source_nonstock: boolean;
  resolution: string;
  warnings: string[];
  annotation?: Annotation | null;
};
type Draft = {
  draft_id: string;
  token: string;
  fingerprint: string;
  location_id: string;
  duplicate_of_purchase_id: string | null;
  lines: Line[];
};
const UNITS = ["each", "lb", "oz", "g", "kg", "ml", "l"];

export function ReviewedPurchaseCsvCard() {
  const { selectedLocationId, setStatus } = useAppState();
  const { account, isAdmin } = useAuth();
  const { data: items = [] } = useItems();
  const cache = useQueryClient();
  const [csv, setCsv] = useState("");
  const [merchant, setMerchant] = useState("");
  const [purchasedOn, setPurchasedOn] = useState("");
  const [createMissing, setCreateMissing] = useState(true);
  const [apply, setApply] = useState(true);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [result, setResult] = useState<PurchaseImportApiResponse | null>(null);
  const version = useRef(0);
  const location = useRef(selectedLocationId);
  location.current = selectedLocationId;
  function invalidate() {
    version.current++;
    setDraft(null);
    setResult(null);
  }
  useEffect(() => {
    invalidate();
  }, [selectedLocationId, account?.id]);
  useEffect(
    () => () => {
      version.current++;
    },
    []
  );
  const preview = useMutation({
    mutationFn: async () => {
      if (!selectedLocationId) throw new Error("Select a location first");
      const generation = ++version.current;
      const data = (
        await api.post<Draft>("/api/purchases/draft-csv", {
          location_id: selectedLocationId,
          csv_text: csv,
          merchant: merchant.trim() || null,
          purchased_on: purchasedOn || null,
          create_missing_items: createMissing,
          apply_to_inventory: apply,
          default_unit: "each",
        })
      ).data;
      return { data, generation };
    },
    onSuccess: ({ data, generation }) => {
      if (version.current !== generation || data.location_id !== location.current) return;
      setDraft(data);
      setResult(null);
      setStatus(`CSV preview: ${data.lines.length} rows. Verify or exclude each row.`);
    },
    onError: (error) => setStatus(getApiErrorMessage(error, "CSV preview failed")),
  });
  const classify = useMutation({
    mutationFn: async () => {
      if (!draft) throw new Error("Preview the CSV first");
      const generation = version.current;
      const data = (
        await api.post<{ annotations: Annotation[]; provider_requests: number; status?: string | null }>(
          `/api/purchases/drafts/${draft.draft_id}/review`,
          { token: draft.token, fingerprint: draft.fingerprint, lines: draft.lines }
        )
      ).data;
      return { data, generation };
    },
    onSuccess: ({ data, generation }) => {
      if (version.current !== generation) return;
      setDraft((current) =>
        current
          ? {
              ...current,
              lines: current.lines.map((line) => ({
                ...line,
                annotation: data.annotations.find((a) => a.line_id === line.line_id) ?? null,
              })),
            }
          : null
      );
      setStatus(
        data.status
          ? "Classifier information changed or is unavailable. Continue manually."
          : `Classifier hints returned (${data.provider_requests} requests); verify every choice.`
      );
    },
    onError: () => setStatus("Classifier unavailable. Your choices are unchanged; finish manually."),
  });
  const commit = useMutation({
    mutationFn: async () => {
      if (!draft || draft.location_id !== location.current)
        throw new Error("CSV source or location changed; preview again");
      const generation = version.current;
      const data = (
        await api.post<PurchaseImportApiResponse>(`/api/purchases/drafts/${draft.draft_id}/commit`, {
          token: draft.token,
          fingerprint: draft.fingerprint,
          lines: draft.lines,
        })
      ).data;
      return { data, generation };
    },
    onSuccess: ({ data, generation }) => {
      for (const queryKey of ["purchases", "purchaseDetail", "inventory", "items"])
        void cache.invalidateQueries({ queryKey: [queryKey] });
      if (version.current !== generation) {
        setStatus("Purchase committed to its original destination. Refresh history to inspect it.");
        return;
      }
      setDraft(null);
      setResult(data);
      setStatus(`Committed purchase (${data.line_count} verified rows)`);
    },
    onError: (error) =>
      setStatus(getApiErrorMessage(error, "Commit failed. Keep this draft and retry to recover the same import.")),
  });
  const busy = preview.isPending || classify.isPending || commit.isPending;
  function edit(index: number, changes: Partial<Line>) {
    version.current++;
    setDraft((current) =>
      current
        ? {
            ...current,
            lines: current.lines.map((line, i) =>
              i === index ? { ...line, ...changes, confirmed: changes.confirmed ?? false, annotation: null } : line
            ),
          }
        : null
    );
  }
  const valid = Boolean(
    draft &&
    !draft.duplicate_of_purchase_id &&
    draft.lines.some((line) => !line.excluded) &&
    draft.lines.every((line) => {
      if (line.excluded) return true;
      return (
        !line.source_nonstock &&
        line.confirmed &&
        line.role === "stock" &&
        line.name.trim() &&
        /^\d+(?:\.\d{1,3})?$/.test(line.quantity) &&
        Number(line.quantity) > 0 &&
        Number(line.quantity) < 1e9 &&
        UNITS.includes(line.unit) &&
        Boolean(line.matched_item_id) !== line.create_new
      );
    })
  );
  const itemLabel = (name: string, brand?: string | null, barcode?: string | null) =>
    `${name}${brand ? ` (${brand})` : ""}${barcode ? ` · ${barcode}` : ""}`;
  const knownChoices = new Map(
    items.map((item) => [item.id, { value: item.id, label: itemLabel(item.name, item.brand, item.barcode) }])
  );
  for (const line of draft?.lines ?? []) {
    if (line.matched_item_id && !knownChoices.has(line.matched_item_id))
      knownChoices.set(line.matched_item_id, {
        value: line.matched_item_id,
        label: `${itemLabel(line.matched_item_name || "Previously selected item", line.matched_item_brand, line.barcode)} — saved selection`,
      });
  }
  const choices = [
    ...knownChoices.values(),
    ...(createMissing ? [{ value: "__new__", label: "Create new item (reuse equivalent if available)" }] : []),
  ];
  return (
    <Card withBorder>
      <Stack>
        <Title order={3}>Purchase History Import (CSV)</Title>
        <Text>
          Preview the source, resolve item choices, then explicitly verify or exclude every row before committing.
        </Text>
        <Group grow>
          <TextInput
            label="Merchant (optional)"
            value={merchant}
            disabled={busy}
            onChange={(e) => {
              invalidate();
              setMerchant(e.currentTarget.value);
            }}
          />
          <TextInput
            label="Purchased on"
            type="date"
            value={purchasedOn}
            disabled={busy}
            onChange={(e) => {
              invalidate();
              setPurchasedOn(e.currentTarget.value);
            }}
          />
        </Group>
        <input
          aria-label="Purchase CSV file"
          type="file"
          accept=".csv,text/csv"
          disabled={busy}
          onChange={(e) => {
            const file = e.currentTarget.files?.[0];
            if (!file) return;
            invalidate();
            const generation = version.current;
            const reader = new FileReader();
            reader.onload = () => {
              if (version.current === generation) setCsv(String(reader.result ?? ""));
            };
            reader.readAsText(file);
            e.currentTarget.value = "";
          }}
        />
        <Textarea
          aria-label="Purchase CSV text"
          value={csv}
          minRows={5}
          disabled={busy}
          placeholder={"name,quantity,unit\nMilk,2,each"}
          onChange={(e) => {
            invalidate();
            setCsv(e.currentTarget.value);
          }}
        />
        <Group>
          <Checkbox
            label="Allow explicit creation of missing items"
            checked={createMissing}
            disabled={busy}
            onChange={(e) => {
              invalidate();
              setCreateMissing(e.currentTarget.checked);
            }}
          />
          <Checkbox
            label="Add verified rows to inventory"
            checked={apply}
            disabled={busy}
            onChange={(e) => {
              invalidate();
              setApply(e.currentTarget.checked);
            }}
          />
        </Group>
        <Group>
          <Button
            onClick={() => preview.mutate()}
            disabled={busy || !selectedLocationId || !csv.trim()}
            loading={preview.isPending}
          >
            Preview CSV
          </Button>
          {draft && (
            <>
              <Button
                variant="light"
                disabled={busy || !isAdmin}
                loading={classify.isPending}
                onClick={() => classify.mutate()}
              >
                Ask classifier (admin)
              </Button>
              <Button disabled={busy || !valid} loading={commit.isPending} onClick={() => commit.mutate()}>
                Commit reviewed CSV
              </Button>
              <Button variant="default" disabled={commit.isPending} onClick={invalidate}>
                Cancel CSV review
              </Button>
            </>
          )}
        </Group>
        {!selectedLocationId && <Text>Select a destination using the location selector above.</Text>}
        {draft && (
          <>
            <ClassifierVerificationNotice />
            <Text size="sm">
              Optional review uses at most8 requests and the50 newest catalog candidates, without retries. Your choices
              never change automatically. Edit any source/options to preview again.
            </Text>
            {!isAdmin && (
              <Text>
                Classifier hints require <Link to={ROUTES.admin}>administrator sign-in</Link>. Manual review remains
                available; preview again after signing in.
              </Text>
            )}
            {draft.duplicate_of_purchase_id && (
              <Alert color="orange">
                This source was already imported. Review purchase history; another import is blocked.
              </Alert>
            )}
            <ScrollArea>
              <Table withTableBorder striped>
                <Table.Thead>
                  <Table.Tr>
                    <Table.Th>Source and review</Table.Th>
                    <Table.Th>Item choice</Table.Th>
                    <Table.Th>Quantity / unit</Table.Th>
                    <Table.Th>Confirmation</Table.Th>
                  </Table.Tr>
                </Table.Thead>
                <Table.Tbody>
                  {draft.lines.map((line, index) => (
                    <Table.Tr key={line.line_id}>
                      <Table.Td>
                        <Text size="sm">{line.raw_line}</Text>
                        <Text size="xs">
                          Source quantity: {line.source_quantity || "Missing"} · Match:{" "}
                          {line.resolution.replaceAll("_", " ")}
                        </Text>
                        {line.barcode && <Text size="xs">Barcode: {line.barcode}</Text>}
                        {line.warnings.map((warning) => (
                          <Text c="orange" size="sm" key={warning}>
                            {warning}
                          </Text>
                        ))}
                        {line.annotation && (
                          <>
                            <Text size="sm">Classifier: {line.annotation.status}</Text>
                            <Text size="sm">
                              Suggested role:{" "}
                              {(
                                {
                                  stock: "Stock purchase",
                                  return: "Return / refund",
                                  financial_adjustment: "Financial adjustment",
                                  unknown: "Unknown / insufficient evidence",
                                } as Record<string, string>
                              )[line.annotation.role ?? "unknown"] ?? "Unknown / insufficient evidence"}
                            </Text>
                            <Text size="sm">
                              {line.annotation.suggested_item_id
                                ? `Suggested match: ${knownChoices.get(line.annotation.suggested_item_id)?.label ?? line.annotation.candidates.find((c) => c.id === line.annotation?.suggested_item_id)?.name ?? "Details unavailable; review catalog manually"}`
                                : "No supported catalog match suggested; identify the item manually."}
                            </Text>
                            <Text size="sm">
                              {line.annotation.score == null
                                ? "No usable score; review manually."
                                : `${Math.round(line.annotation.score * 100)}% model score — ${line.annotation.below_threshold ? "Below threshold; verify manually" : "Still requires your verification"}`}
                            </Text>
                            {line.annotation.candidates.map((candidate) => (
                              <Text size="xs" key={candidate.id}>
                                {candidate.id === line.annotation?.suggested_item_id
                                  ? "Suggested candidate"
                                  : "Alternative candidate"}
                                :{" "}
                                {knownChoices.get(candidate.id)?.label ??
                                  `${candidate.name} — current details not loaded`}
                              </Text>
                            ))}
                          </>
                        )}
                        {line.excluded && <Text>Excluded — will not import</Text>}
                      </Table.Td>
                      <Table.Td>
                        <TextInput
                          aria-label={`CSV item name ${index + 1}`}
                          value={line.name}
                          disabled={busy || line.excluded}
                          maxLength={200}
                          onChange={(e) => edit(index, { name: e.currentTarget.value })}
                        />
                        <Select
                          aria-label={`CSV catalog item ${index + 1}`}
                          searchable
                          clearable
                          data={choices}
                          value={line.create_new ? "__new__" : line.matched_item_id}
                          disabled={busy || line.excluded}
                          onChange={(value) =>
                            edit(index, {
                              matched_item_id: value === "__new__" ? null : value,
                              create_new: value === "__new__",
                            })
                          }
                        />
                      </Table.Td>
                      <Table.Td>
                        <TextInput
                          aria-label={`CSV quantity ${index + 1}`}
                          value={line.quantity}
                          disabled={busy || line.excluded || line.source_nonstock}
                          onChange={(e) => edit(index, { quantity: e.currentTarget.value })}
                        />
                        <Select
                          aria-label={`CSV unit ${index + 1}`}
                          data={UNITS}
                          value={line.unit}
                          disabled={busy || line.excluded}
                          onChange={(value) => edit(index, { unit: value ?? "" })}
                        />
                        <Select
                          aria-label={`CSV role ${index + 1}`}
                          data={[
                            { value: "stock", label: "Stock purchase" },
                            { value: "unknown", label: "Unknown" },
                            { value: "return", label: "Return" },
                            { value: "financial_adjustment", label: "Financial adjustment" },
                          ]}
                          value={line.role}
                          disabled={busy || line.excluded || line.source_nonstock}
                          onChange={(value) => edit(index, { role: value ?? "unknown" })}
                        />
                      </Table.Td>
                      <Table.Td>
                        <Checkbox
                          label={`I verified CSV row ${index + 1}`}
                          checked={line.confirmed}
                          disabled={busy || line.excluded || line.source_nonstock}
                          onChange={(e) => edit(index, { confirmed: e.currentTarget.checked })}
                        />
                        <Button
                          variant="subtle"
                          disabled={busy}
                          onClick={() => edit(index, { excluded: !line.excluded })}
                        >
                          {line.excluded ? "Restore" : "Exclude"} CSV row {index + 1}
                        </Button>
                      </Table.Td>
                    </Table.Tr>
                  ))}
                </Table.Tbody>
              </Table>
            </ScrollArea>
          </>
        )}
        {result && (
          <Alert color="green">
            Committed {result.line_count} verified rows. Purchase ID: {result.purchase_id}. Exact retries recover the
            same import.
          </Alert>
        )}
      </Stack>
    </Card>
  );
}
