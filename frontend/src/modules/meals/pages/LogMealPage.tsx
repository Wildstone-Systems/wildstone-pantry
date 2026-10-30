import { Alert, Badge, Button, Card, Group, Loader, Stack, Text, TextInput, Title } from "@mantine/core";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { BrowserMultiFormatReader } from "@zxing/browser";
import type { IScannerControls } from "@zxing/browser";

import { api } from "@/api/client";
import { getApiErrorMessage } from "@/api/errors";
import { useItems } from "@/modules/catalog";
import { useHouseholdProfiles } from "@/api/hooks";
import { useAppState } from "@/state/AppStateProvider";

import { CartLineRow, snapQuantity, type CartLine } from "../components/CartLineRow";
import { usePlateBatch } from "../hooks/meals-hooks";

type BarcodeScanResponse = {
  found: boolean;
  item_id: string | null;
  item_name: string | null;
};

type FoodCandidate = {
  fdc_id: number | null;
  description: string | null;
  calories: number | null;
};

/** One row in the unified result list — a catalog item, or a food-database
 *  entry that becomes a catalog item when picked. */
type SearchHit =
  | { source: "catalog"; id: string; name: string; calories: number | null }
  | { source: "usda"; fdcId: number; name: string; calories: number | null }
  | { source: "barcode"; code: string; name: string; calories: null };

/** A USB "wedge" scanner types digits into whatever field has focus then hits
 *  Enter. Folding barcode lookup into the one search box keeps that working
 *  without a second field to aim at. */
const BARCODE_RE = /^\d{8,14}$/;

const CART_KEY = "pantry_meal_cart";

export function LogMealPage() {
  const { activeProfileId } = useAppState();
  const { data: items } = useItems();
  const { data: profiles } = useHouseholdProfiles();
  const plateBatch = usePlateBatch();

  const [query, setQuery] = useState("");
  const [cart, setCart] = useState<CartLine[]>([]);
  const [result, setResult] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busyHit, setBusyHit] = useState<string | null>(null);

  const [usda, setUsda] = useState<FoodCandidate[]>([]);
  const [usdaLoading, setUsdaLoading] = useState(false);
  const [broadenedTo, setBroadenedTo] = useState<string | null>(null);

  // The cart is unsaved work — nothing is written until you log it — so keep it
  // across a reload or an accidental navigation mid-meal.
  useEffect(() => {
    const raw = window.localStorage.getItem(CART_KEY);
    if (!raw) return;
    try {
      const saved = JSON.parse(raw) as CartLine[];
      // Amounts move in wheel stops now — snap a cart saved by the old stepper
      // so what's shown and what gets logged agree.
      setCart(saved.map((l) => ({ ...l, quantity: snapQuantity(l.quantity, l.unit) })));
    } catch {
      window.localStorage.removeItem(CART_KEY);
    }
  }, []);
  useEffect(() => {
    window.localStorage.setItem(CART_KEY, JSON.stringify(cart));
  }, [cart]);

  const trimmed = query.trim();

  useEffect(() => {
    if (trimmed.length < 3) {
      setUsda([]);
      setBroadenedTo(null);
      return;
    }
    let cancelled = false;
    setUsdaLoading(true);
    const timer = window.setTimeout(async () => {
      try {
        const res = await api.get<{ candidates: FoodCandidate[]; searched_term?: string }>(
          "/api/items/search-nutrition",
          { params: { q: trimmed, limit: 8 } },
        );
        if (cancelled) return;
        setUsda(res.data.candidates ?? []);
        // The server broadens an over-specific query; say so rather than
        // silently returning results for words the user didn't type.
        const searched = res.data.searched_term;
        setBroadenedTo(searched && searched.toLowerCase() !== trimmed.toLowerCase() ? searched : null);
      } catch {
        if (!cancelled) {
          setUsda([]);
          setBroadenedTo(null);
        }
      } finally {
        if (!cancelled) setUsdaLoading(false);
      }
    }, 400);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [trimmed]);

  // Local first, always: something already in your pantry beats a generic
  // database row for the same words, and it resolves instantly.
  const hits: SearchHit[] = useMemo(() => {
    if (!trimmed) return [];
    if (BARCODE_RE.test(trimmed)) {
      return [{ source: "barcode", code: trimmed, name: `Look up barcode ${trimmed}`, calories: null }];
    }
    const needle = trimmed.toLowerCase();
    const local: SearchHit[] = (items ?? [])
      .filter((i) => i.name.toLowerCase().includes(needle))
      .slice(0, 8)
      .map((i) => ({ source: "catalog", id: i.id, name: i.name, calories: i.energy_kcal_100g ?? null }));
    const localNames = new Set(local.map((l) => l.name.toLowerCase()));
    const remote: SearchHit[] = usda
      .filter((c) => c.fdc_id != null && !localNames.has((c.description ?? "").toLowerCase()))
      .map((c) => ({
        source: "usda",
        fdcId: c.fdc_id as number,
        name: c.description ?? "Unknown food",
        calories: c.calories,
      }));
    return [...local, ...remote];
  }, [items, usda, trimmed]);

  const addLine = useCallback((itemId: string, itemName: string) => {
    setCart((prev) => [
      ...prev,
      { key: `${itemId}:${Date.now()}`, itemId, itemName, quantity: 1, unit: "each", unitSeeded: false },
    ]);
    setQuery("");
    setUsda([]);
    setBroadenedTo(null);
    setResult(null);
  }, []);

  const pickHit = async (hit: SearchHit) => {
    setError(null);
    if (hit.source === "catalog") {
      addLine(hit.id, hit.name);
      return;
    }
    if (hit.source === "barcode") {
      setBusyHit(`barcode:${hit.code}`);
      try {
        await lookupBarcode(hit.code);
      } finally {
        setBusyHit(null);
      }
      return;
    }
    // A database row isn't a catalog item yet — materialise it (idempotent),
    // then add. Repeat picks of the same food reuse the same item.
    setBusyHit(`usda:${hit.fdcId}`);
    try {
      const res = await api.post<{ id: string; name: string }>("/api/items/from-food-search", {
        fdc_id: hit.fdcId,
        q: broadenedTo ?? trimmed,
      });
      addLine(res.data.id, res.data.name);
    } catch (err) {
      setError(getApiErrorMessage(err, "Could not use that food"));
    } finally {
      setBusyHit(null);
    }
  };

  // ---- Barcode / camera: a scan can't be typed, so it keeps its own control —
  // but it drops straight into the same cart.
  const videoRef = useRef<HTMLVideoElement>(null);
  const controlsRef = useRef<IScannerControls | null>(null);
  const [cameraActive, setCameraActive] = useState(false);
  const [cameraError, setCameraError] = useState<string | null>(null);
  const lastScanRef = useRef<string>("");

  const lookupBarcode = useCallback(
    async (code: string) => {
      const normalized = code.trim();
      if (!normalized) return;
      setError(null);
      try {
        const res = await api.post<BarcodeScanResponse>("/api/barcodes/scan", {
          barcode: normalized,
          auto_create: true,
        });
        if (res.data.found && res.data.item_id) {
          addLine(res.data.item_id, res.data.item_name ?? normalized);
        } else {
          setError(`Barcode not found: ${normalized}`);
        }
      } catch (err) {
        setError(getApiErrorMessage(err, "Barcode lookup failed"));
      }
    },
    [addLine],
  );

  const startCamera = useCallback(async () => {
    setCameraError(null);
    try {
      const reader = new BrowserMultiFormatReader();
      if (!videoRef.current) return;
      setCameraActive(true);
      const controls = await reader.decodeFromVideoDevice(undefined, videoRef.current, (res, err) => {
        if (res) {
          // Stays on for the next item — a meal is usually several barcodes in
          // a row. Guard against the decoder firing repeatedly on one label.
          const code = res.getText();
          if (code !== lastScanRef.current) {
            lastScanRef.current = code;
            window.setTimeout(() => (lastScanRef.current = ""), 2500);
            lookupBarcode(code);
          }
        }
        if (err && err.name !== "NotFoundException") console.warn("Scan error:", err);
      });
      controlsRef.current = controls;
    } catch (err) {
      setCameraError(err instanceof Error ? err.message : "Camera access failed");
      setCameraActive(false);
    }
  }, [lookupBarcode]);

  const stopCamera = useCallback(() => {
    controlsRef.current?.stop();
    controlsRef.current = null;
    setCameraActive(false);
  }, []);

  useEffect(() => () => controlsRef.current?.stop(), []);

  const activeProfile = (profiles ?? []).find((p) => p.id === activeProfileId);

  const submit = async () => {
    if (!cart.length) return;
    setError(null);
    setResult(null);
    try {
      const res = await plateBatch.mutateAsync({
        profile_id: activeProfileId,
        entries: cart.map((l) => ({ item_id: l.itemId, quantity: l.quantity, unit: l.unit })),
      });
      const kcal = Math.round(Number(res.total_calories) || 0);
      setCart([]);
      window.localStorage.removeItem(CART_KEY);
      setResult(
        `Logged ${res.logged.length} item${res.logged.length === 1 ? "" : "s"} — ${kcal} kcal` +
          (res.complete ? "" : " (some items had no nutrition data)"),
      );
    } catch (err) {
      setError(getApiErrorMessage(err, "Could not log that meal"));
    }
  };

  return (
    <Stack>
      <Title order={2}>Log meal</Title>
      <Text c="dimmed">Search or scan to build up everything you ate, then log it in one go.</Text>

      {!activeProfileId ? (
        <Alert color="yellow">Pick who's eating with the profile selector (top-right) before logging.</Alert>
      ) : (
        <Text size="sm">
          Eating: <Badge variant="light">{activeProfile?.display_name ?? "?"}</Badge>
        </Text>
      )}

      <Card withBorder padding="md">
        <Stack gap="sm">
          <TextInput
            label="What did you eat?"
            placeholder="Search your pantry and the food database…"
            value={query}
            onChange={(e) => setQuery(e.currentTarget.value)}
            onKeyDown={(e) => {
              // A wedge scanner ends its digit burst with Enter — take the top
              // hit so scanning into this one field just works.
              if (e.key === "Enter" && hits.length > 0) {
                e.preventDefault();
                void pickHit(hits[0]);
              }
            }}
            rightSection={usdaLoading ? <Loader size="xs" /> : null}
          />

          {broadenedTo && (
            <Text size="xs" c="dimmed">
              No exact matches — showing results for "{broadenedTo}".
            </Text>
          )}

          {trimmed.length >= 3 && hits.length === 0 && !usdaLoading && (
            <Text size="xs" c="dimmed">
              Nothing found for "{trimmed}". Try fewer words.
            </Text>
          )}

          {hits.length > 0 && (
            <Stack gap={4}>
              {hits.map((hit) => {
                const key =
                  hit.source === "catalog"
                    ? `c:${hit.id}`
                    : hit.source === "barcode"
                      ? `barcode:${hit.code}`
                      : `usda:${hit.fdcId}`;
                return (
                  <Button
                    key={key}
                    variant="default"
                    size="xs"
                    fullWidth
                    loading={busyHit === key}
                    onClick={() => pickHit(hit)}
                    styles={{
                      inner: { justifyContent: "flex-start" },
                      label: { whiteSpace: "normal", textAlign: "left" },
                    }}
                  >
                    <Group gap="xs" wrap="wrap">
                      <Text size="xs">{hit.name}</Text>
                      <Badge
                        size="xs"
                        variant="light"
                        color={hit.source === "catalog" ? "teal" : hit.source === "barcode" ? "blue" : "gray"}
                      >
                        {hit.source === "catalog"
                          ? "in your pantry"
                          : hit.source === "barcode"
                            ? "barcode"
                            : "food database"}
                      </Badge>
                      {hit.calories != null && (
                        <Text size="xs" c="dimmed">
                          {Math.round(hit.calories)} kcal/100g
                        </Text>
                      )}
                    </Group>
                  </Button>
                );
              })}
            </Stack>
          )}

          <Group>
            {!cameraActive ? (
              <Button onClick={startCamera} leftSection={<span>📷</span>} variant="subtle" size="xs">
                Scan barcodes
              </Button>
            ) : (
              <Button onClick={stopCamera} color="red" variant="outline" size="xs">
                Stop scanning
              </Button>
            )}
            {cameraActive && (
              <Text size="xs" c="dimmed">
                Scanning stays on — scan one item after another.
              </Text>
            )}
          </Group>
          <div
            style={{
              position: "relative",
              width: "100%",
              minHeight: cameraActive ? 180 : 0,
              background: cameraActive ? "#111" : "transparent",
              borderRadius: 8,
              overflow: "hidden",
            }}
          >
            <video
              ref={videoRef}
              style={{ width: "100%", display: cameraActive ? "block" : "none" }}
              playsInline
              muted
            />
          </div>
          {cameraError && (
            <Alert color="red" title="Camera Error">
              {cameraError}
            </Alert>
          )}
        </Stack>
      </Card>

      <Card withBorder padding="md">
        <Group justify="space-between" mb="sm">
          <Title order={4}>This meal</Title>
          {cart.length > 0 && (
            <Button variant="subtle" size="xs" color="gray" onClick={() => setCart([])}>
              Clear
            </Button>
          )}
        </Group>

        {cart.length === 0 ? (
          <Text c="dimmed" size="sm">
            Nothing added yet. Search or scan above — nothing is saved until you log it.
          </Text>
        ) : (
          <Stack gap="sm">
            {cart.map((line, i) => (
              <CartLineRow
                key={line.key}
                line={line}
                onChange={(next) => setCart((prev) => prev.map((l, j) => (j === i ? next : l)))}
                onRemove={() => setCart((prev) => prev.filter((_, j) => j !== i))}
              />
            ))}
          </Stack>
        )}

        {error && (
          <Alert color="red" mt="sm">
            {error}
          </Alert>
        )}
        {result && (
          <Alert color="green" mt="sm">
            {result}
          </Alert>
        )}

        <Group mt="md">
          <Button onClick={submit} loading={plateBatch.isPending} disabled={!cart.length || !activeProfileId} size="md">
            Log {cart.length > 0 ? `${cart.length} item${cart.length === 1 ? "" : "s"}` : "meal"}
          </Button>
        </Group>
      </Card>
    </Stack>
  );
}
