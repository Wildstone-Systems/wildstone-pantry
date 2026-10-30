import {
  Alert,
  Badge,
  Box,
  Button,
  Card,
  Group,
  Loader,
  NumberInput,
  Progress,
  Stack,
  Text,
  TextInput,
  Title,
} from "@mantine/core";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useRef, useState } from "react";
import { Link } from "react-router-dom";

import { api } from "@/api/client";
import { getApiErrorMessage } from "@/api/errors";
import { ROUTES } from "@/routes/paths";
import { useAppState } from "@/state/AppStateProvider";

// ml per volume unit — mirrors the backend's table. Used only to turn "one tsp
// weighs N grams" (what a person can measure) into the density the item stores.
// Nobody should ever be asked to type g/ml.
const ML_PER: Record<string, number> = {
  tsp: 4.92892,
  tbsp: 14.7868,
  cup: 236.588,
};

type ConversionCandidate = {
  item_id: string;
  name: string;
  brand: string | null;
  base_unit: string | null;
  stock_mode: string | null;
  size_quantity: number | null;
  size_unit: string | null;
  recipe_units: string[];
};

type DensitySuggestion = {
  item_id: string;
  density_g_per_ml: string | number;
  source_description: string;
  fdc_id: number;
  verified: boolean | null;
  verified_why: string;
  grams_per_tsp: string | number | null;
  grams_per_tbsp: string | number | null;
  grams_per_cup: string | number | null;
};

/** null = looked and found nothing; undefined = not looked yet. */
type RowState = { loading?: boolean; suggestion?: DensitySuggestion | null; error?: string };

// A lookup is one USDA search plus up to four detail calls plus a model check —
// 2.5-11s each, against an API measured returning 400s under load. "Find all"
// therefore runs strictly one at a time with a gap between, never a fan-out.
const GAP_MS = 400;

export function ConversionReviewPage() {
  const { setStatus } = useAppState();
  const queryClient = useQueryClient();
  const [rows, setRows] = useState<Record<string, RowState>>({});
  const [manual, setManual] = useState<Record<string, number | string>>({});
  const [terms, setTerms] = useState<Record<string, string>>({});
  const [scanning, setScanning] = useState(false);
  const [progress, setProgress] = useState(0);
  const cancelled = useRef(false);

  const queue = useQuery({
    queryKey: ["conversion-review"],
    queryFn: async () =>
      (await api.get<{ items: ConversionCandidate[]; judge_available: boolean }>("/api/items/conversion-review")).data,
  });

  const lookup = useCallback(async (item: ConversionCandidate, term?: string) => {
    setRows((r) => ({ ...r, [item.item_id]: { loading: true } }));
    try {
      const res = await api.get<DensitySuggestion | null>(
        `/api/items/${item.item_id}/density-suggestion`,
        term ? { params: { q: term } } : undefined
      );
      setRows((r) => ({ ...r, [item.item_id]: { suggestion: res.data ?? null } }));
    } catch (err) {
      setRows((r) => ({
        ...r,
        [item.item_id]: { error: getApiErrorMessage(err, "Lookup failed") },
      }));
    }
  }, []);

  /** Walk the whole queue one row at a time. Cancellable; one failure never
   *  stops the run, it just leaves that row retryable. */
  const findAll = useCallback(async () => {
    const items = queue.data?.items ?? [];
    cancelled.current = false;
    setScanning(true);
    setProgress(0);
    for (let i = 0; i < items.length; i += 1) {
      if (cancelled.current) break;
      // Skip anything already answered — re-running is pure cost.
      if (rows[items[i].item_id]?.suggestion === undefined) {
        await lookup(items[i]);
        await new Promise((resolve) => window.setTimeout(resolve, GAP_MS));
      }
      setProgress(i + 1);
    }
    setScanning(false);
  }, [queue.data, rows, lookup]);

  const applyDensity = useMutation({
    mutationFn: async ({ itemId, density }: { itemId: string; density: number }) => {
      await api.post(`/api/items/${itemId}/density`, { density_g_per_ml: density });
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["conversion-review"] });
      queryClient.invalidateQueries({ queryKey: ["items"] });
    },
  });

  const accept = async (item: ConversionCandidate, density: number) => {
    try {
      await applyDensity.mutateAsync({ itemId: item.item_id, density });
      setStatus(`${item.name} can now be converted`);
    } catch (err) {
      setStatus(getApiErrorMessage(err, "Could not save that conversion"));
    }
  };

  /** The unit this item's recipes actually ask for — what the manual prompt
   *  should name, so nobody has to translate. */
  const askUnit = (item: ConversionCandidate) => item.recipe_units.find((u) => ML_PER[u]) ?? "tsp";

  const items = queue.data?.items ?? [];

  return (
    <Stack>
      <Group justify="space-between" align="flex-start" wrap="wrap">
        <div>
          <Title order={2}>Data to review</Title>
          <Text c="dimmed" size="sm" mt={4}>
            {items.length} item{items.length === 1 ? "" : "s"} recipes measure by volume that can&apos;t be converted
            yet. Fixing one clears it from every recipe, permanently.
          </Text>
        </div>
        <Group>
          <Button variant="default" component={Link} to={ROUTES.settings}>
            Back
          </Button>
          {scanning ? (
            <Button color="red" variant="light" onClick={() => (cancelled.current = true)}>
              Cancel
            </Button>
          ) : (
            <Button onClick={findAll} disabled={items.length === 0}>
              Find all conversions
            </Button>
          )}
        </Group>
      </Group>

      {scanning && (
        <Card withBorder padding="sm">
          <Group justify="space-between" mb={6}>
            <Text size="sm">
              Looking up {progress} of {items.length}…
            </Text>
            <Text size="xs" c="dimmed">
              one at a time — the food database rate-limits
            </Text>
          </Group>
          <Progress value={items.length ? (progress / items.length) * 100 : 0} />
        </Card>
      )}

      {queue.data && !queue.data.judge_available && (
        <Alert color="yellow" title="No local model configured">
          Suggestions will come back unchecked, so you are the only thing confirming the reference food is the same as
          yours. Configure Ollama under Admin → Integrations to have matches vetted first.
        </Alert>
      )}

      {queue.isLoading && <Loader />}

      {!queue.isLoading && items.length === 0 && (
        <Card withBorder padding="lg">
          <Text>Nothing to review — every ingredient a recipe measures by volume can be converted.</Text>
        </Card>
      )}

      <Stack gap="sm">
        {items.map((item) => {
          const state = rows[item.item_id] ?? {};
          const unit = askUnit(item);
          const suggestion = state.suggestion;
          const gramsKey = `grams_per_${unit}` as keyof DensitySuggestion;
          const grams = suggestion ? Number(suggestion[gramsKey] ?? 0) : 0;

          return (
            <Card key={item.item_id} withBorder padding="md">
              <Group justify="space-between" align="flex-start" wrap="nowrap">
                <div style={{ minWidth: 0 }}>
                  <Text fw={600}>{item.name}</Text>
                  <Text size="xs" c="dimmed">
                    stocked as {item.base_unit ?? "—"}
                    {item.size_quantity ? ` · ${item.size_quantity} ${item.size_unit ?? ""}` : ""}
                  </Text>
                </div>
                <Badge variant="light" style={{ flexShrink: 0 }}>
                  recipes ask in {item.recipe_units.join(", ")}
                </Badge>
              </Group>

              <Box mt="sm">
                {state.loading ? (
                  <Group gap="xs">
                    <Loader size="xs" />
                    <Text size="sm" c="dimmed">
                      Searching the food database…
                    </Text>
                  </Group>
                ) : suggestion ? (
                  <Alert color={suggestion.verified ? "green" : "yellow"} p="sm">
                    <Text size="sm" fw={600}>
                      1 {unit} = {grams.toFixed(2)} g
                    </Text>
                    <Text size="xs" c="dimmed">
                      from “{suggestion.source_description}” (USDA, FDC ID: {suggestion.fdc_id})
                    </Text>
                    <Text size="xs" mt={4}>
                      {suggestion.verified === false
                        ? `rejected — ${suggestion.verified_why || "reference does not match; review before accepting"}`
                        : suggestion.verified
                          ? `checked — same food as yours${suggestion.verified_why ? `: ${suggestion.verified_why}` : ""}`
                          : "not checked — confirm this is the same food as yours"}
                    </Text>
                    <Group mt="xs" gap="xs">
                      <Button
                        size="xs"
                        color="green"
                        loading={applyDensity.isPending}
                        onClick={() => accept(item, Number(suggestion.density_g_per_ml))}
                      >
                        Use this
                      </Button>
                      <Button
                        size="xs"
                        variant="default"
                        onClick={() => setRows((r) => ({ ...r, [item.item_id]: { suggestion: null } }))}
                      >
                        Not right
                      </Button>
                    </Group>
                  </Alert>
                ) : state.error ? (
                  <Group gap="xs">
                    <Text size="sm" c="red">
                      {state.error}
                    </Text>
                    <Button size="xs" variant="subtle" onClick={() => lookup(item)}>
                      Try again
                    </Button>
                  </Group>
                ) : suggestion === null ? (
                  // Looked, found nothing usable. NOT a dead end: the gate is
                  // deliberately strict and rejects some valid matches, so the
                  // two manual paths below are the point of this page.
                  <Stack gap="xs">
                    <Text size="sm" c="dimmed">
                      No confident match. Search for it yourself, or enter the weight.
                    </Text>
                    <Group gap="xs" align="flex-end" wrap="wrap">
                      <TextInput
                        label="Search the food database for"
                        placeholder={item.name}
                        value={terms[item.item_id] ?? ""}
                        onChange={(e) => {
                          // Read the value BEFORE the state updater. React nulls
                          // `currentTarget` once the handler returns, and the
                          // updater form runs later — during the reducer — so
                          // touching the event inside it throws and takes the
                          // whole page down with it.
                          const value = e.currentTarget.value;
                          setTerms((t) => ({ ...t, [item.item_id]: value }));
                        }}
                        size="xs"
                        style={{ width: 220 }}
                      />
                      <Button
                        size="xs"
                        variant="light"
                        disabled={!(terms[item.item_id] ?? "").trim()}
                        onClick={() => lookup(item, terms[item.item_id])}
                        aria-label={`Search the food database for ${item.name}`}
                      >
                        Search
                      </Button>
                    </Group>
                    <Group gap="xs" align="flex-end" wrap="wrap">
                      <NumberInput
                        // Asked in the unit the recipe uses, never in g/ml —
                        // a teaspoon can be weighed, a density cannot.
                        label={`1 ${unit} of ${item.name} weighs`}
                        suffix=" g"
                        value={manual[item.item_id] ?? ""}
                        onChange={(v) => setManual((m) => ({ ...m, [item.item_id]: v }))}
                        min={0}
                        step={0.1}
                        decimalScale={2}
                        size="xs"
                        style={{ width: 240 }}
                      />
                      <Button
                        size="xs"
                        loading={applyDensity.isPending}
                        disabled={!Number(manual[item.item_id])}
                        onClick={() => accept(item, Number(manual[item.item_id]) / ML_PER[unit])}
                        aria-label={`Save conversion for ${item.name}`}
                      >
                        Save
                      </Button>
                    </Group>
                  </Stack>
                ) : (
                  <Button size="xs" variant="light" onClick={() => lookup(item)}>
                    Find conversion
                  </Button>
                )}
              </Box>
            </Card>
          );
        })}
      </Stack>
    </Stack>
  );
}
