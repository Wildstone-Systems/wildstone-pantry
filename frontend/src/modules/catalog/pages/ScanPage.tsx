import { Alert, Badge, Button, Card, Group, Image, Select, Stack, Text, TextInput, Title, Tooltip } from "@mantine/core";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { BrowserMultiFormatReader } from "@zxing/browser";
import type { IScannerControls } from "@zxing/browser";

import { api } from "@/api/client";
import { getApiErrorMessage } from "@/api/errors";
import { QuantityStepper } from "@/components/QuantityStepper";
import { ROUTES } from "@/routes/paths";
import { useAppState } from "@/state/AppStateProvider";

import { useLocations } from "../hooks/catalog-hooks";

// ---------------------------------------------------------------------------
// Types matching the new scan API response
// ---------------------------------------------------------------------------

type ProductData = {
  name: string;
  brand: string | null;
  category: string | null;
  size_description: string | null;
  size_quantity: number | null;
  size_unit: string | null;
  image_url: string | null;
  nutrition: Record<string, string | number | null> | null;
  source: string;
  serving_size_g: number | null;
  ingredients_text: string | null;
  allergens: string[] | null;
  nutri_score: string | null;
  nova_group: number | null;
  eco_score: string | null;
};

type LocationCandidate = { location_id: string; location_name: string; quantity: string };

/** A best-by offset the backend learned from this item's own dated purchases.
 *  Absent whenever there isn't enough consistent history to stand behind one. */
type ShelfLifeHint = {
  days: number;
  observations: number;
  spread_days: number;
  source: string;
};

type ScanResponse = {
  barcode: string;
  found: boolean;
  created: boolean;
  item_id: string | null;
  item_name: string | null;
  source: string | null;
  item: Record<string, unknown> | null;
  product: ProductData | null;
  // Where this item should be checked in: one entry = "it already lives
  // there" (use it, no prompt); several = ask which one.
  location_candidates: LocationCandidate[];
  shelf_life_hint: ShelfLifeHint | null;
};

type InventoryLine = {
  inventory_id: string;
  item_id: string;
  item_name: string;
  location_id: string;
  quantity: string;
  unit: string;
  expiration_date: string | null;
};

type AddToInventoryResponse = {
  transaction_ids: string[];
  inventory: InventoryLine[];
  // Base-unit delta this add recorded (ADR 0016) — e.g. "3785.400" "ml" for
  // 2×½gal. Decimal fields come across the wire as JSON strings.
  added_quantity: string | null;
  added_unit: string | null;
};

// ---------------------------------------------------------------------------
// Unit options
// ---------------------------------------------------------------------------

const UNIT_OPTIONS = [
  { value: "each", label: "each" },
  { value: "lb", label: "lb" },
  { value: "oz", label: "oz" },
  { value: "g", label: "g" },
  { value: "kg", label: "kg" },
  { value: "ml", label: "ml" },
  { value: "l", label: "l" },
  { value: "floz", label: "fl oz" },
  { value: "gal", label: "gal" },
  { value: "pint", label: "pint" },
  { value: "qt", label: "qt" },
  { value: "cup", label: "cup" },
];

// The backend spells some units differently than the picker (parsed from OFF/
// USDA/Kroger). Fold those onto a picker value; anything unknown falls through.
// NOTE: fluid ounces are `floz` (volume) — never folded onto `oz` (weight), which
// would corrupt liquid package sizes into a mass (ADR 0017 §3).
const UNIT_ALIASES: Record<string, string> = { ea: "each", "fl oz": "floz" };

function normalizeUnit(unit: string | null | undefined): string | null {
  if (!unit) return null;
  const mapped = UNIT_ALIASES[unit] ?? unit;
  return UNIT_OPTIONS.some((o) => o.value === mapped) ? mapped : null;
}

// A count/package unit — the purchase is "N packages" and each package's size is
// given separately, so intake takes the container form (sealed_count × size).
const COUNT_UNITS = new Set(["each", "pack", "ct", "count", "unit", "pk"]);
const isCountUnit = (unit: string) => COUNT_UNITS.has(unit);

// today + n days as YYYY-MM-DD (native <input type="date"> value).
function isoInDays(days: number): string {
  const d = new Date();
  d.setDate(d.getDate() + days);
  return d.toISOString().slice(0, 10);
}

// Render a base-unit amount the human-readable way: ml→L past a litre, g→kg past
// a kilo, else the base unit as-is. Used only for the friendly confirmation toast.
// The backend serializes Decimal fields as JSON strings (e.g. "1.000"), not
// numbers — coerce first, or `.toFixed()` throws on the raw string and (since
// this runs inside addMutation's onSuccess) React Query reports the mutation
// as failed even though the add already committed.
function humanizeBase(qty: number | string | null, unit: string | null): string | null {
  if (qty == null || !unit) return null;
  const n0 = Number(qty);
  if (unit === "ml") return n0 >= 1000 ? `${(n0 / 1000).toFixed(2)} L` : `${Math.round(n0)} ml`;
  if (unit === "g") return n0 >= 1000 ? `${(n0 / 1000).toFixed(2)} kg` : `${Math.round(n0)} g`;
  const n = Number.isInteger(n0) ? n0 : Number(n0.toFixed(2));
  return `${n} ${unit}`;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function ScanPage() {
  const { selectedLocationId, setSelectedLocationId, setStatus } = useAppState();
  const locationsQuery = useLocations();
  const queryClient = useQueryClient();

  // Camera state
  const videoRef = useRef<HTMLVideoElement>(null);
  const controlsRef = useRef<IScannerControls | null>(null);
  const [cameraActive, setCameraActive] = useState(false);
  const [cameraError, setCameraError] = useState<string | null>(null);

  // Manual / scanner-wedge input state. This field is the workflow's home base:
  // focus returns here after every add so the next barcode can be typed/scanned
  // straight away, no clicking.
  const [manualBarcode, setManualBarcode] = useState("");
  const scanInputRef = useRef<HTMLInputElement>(null);
  const qtyRef = useRef<HTMLInputElement>(null);

  // Scan result state
  const [scanResult, setScanResult] = useState<ScanResponse | null>(null);
  const [scanError, setScanError] = useState<string | null>(null);

  // Add-to-inventory state. A check-in is three separable things (ADR 0016):
  // how many packages you bought, how big each package is, and its best-by.
  const [purchaseQty, setPurchaseQty] = useState(1);
  const [purchaseUnit, setPurchaseUnit] = useState("each");
  const [pkgAmount, setPkgAmount] = useState<number | null>(null);
  const [pkgUnit, setPkgUnit] = useState<string | null>(null);
  const [expiry, setExpiry] = useState<string>(""); // YYYY-MM-DD or ""
  // Where the prefilled date came from, so the field can show its working
  // instead of presenting a guess as fact. Cleared the moment the operator
  // edits it — from then on it's their date, not ours.
  const [expirySource, setExpirySource] = useState<"learned" | "default" | null>(null);
  const [shelfLifeHint, setShelfLifeHint] = useState<ShelfLifeHint | null>(null);

  // Where THIS scan will be added — resolved from the item's own history, not
  // the app-wide "Adding scans to location" default. `suggestedLocationId` is
  // either the single place this item already lives (auto-picked, silent) or
  // whatever the operator chose from `locationCandidates` (shown as a prompt
  // when the item lives in more than one place, or has never been stocked
  // anywhere — mirrors the scan station's own location resolution). Both reset
  // to a fresh guess on every new scan.
  const [suggestedLocationId, setSuggestedLocationId] = useState<string | null>(null);
  const [locationCandidates, setLocationCandidates] = useState<LocationCandidate[]>([]);

  const addCardRef = useRef<HTMLDivElement>(null);

  const focusScanField = useCallback(() => {
    scanInputRef.current?.focus();
    scanInputRef.current?.select();
  }, []);

  // Reset form when new scan comes in
  const resetForNewScan = useCallback(() => {
    setScanResult(null);
    setScanError(null);
    setPurchaseQty(1);
    setPurchaseUnit("each");
    setPkgAmount(null);
    setPkgUnit(null);
    setExpiry("");
    setExpirySource(null);
    setShelfLifeHint(null);
    setSuggestedLocationId(null);
    setLocationCandidates([]);
  }, []);

  // Pre-fill the check-in from the scanned product: one package to start, the
  // package's size (0.5 gal, 12 ea, 2 oz…) shown separately so "2 each 0.5 gal
  // reads naturally, and a best-by seeded from the item's default shelf life.
  const applySizePrefill = useCallback((data: ScanResponse) => {
    const product = data.product;
    const item = (data.item ?? {}) as Record<string, unknown>;
    const sizeQty = product?.size_quantity ?? ((item.size_quantity as number | null) ?? null);
    const unit = normalizeUnit(product?.size_unit ?? (item.size_unit as string | null | undefined));
    setPurchaseQty(1);
    setPurchaseUnit("each");
    setPkgAmount(sizeQty != null && sizeQty > 0 ? sizeQty : null);
    setPkgUnit(unit);
    // Best-by precedence: what this item has actually been dated at beats the
    // item's configured default, which is usually a coarse category guess from
    // the receipt importer (7d produce / 180d frozen / 30d otherwise) rather
    // than anything anyone chose. Both lose to whatever the operator types.
    const hint = data.shelf_life_hint ?? null;
    const defDays = item.default_expiration_days as number | null | undefined;
    setExpirySource(hint ? "learned" : defDays != null && defDays > 0 ? "default" : null);
    setShelfLifeHint(hint);
    if (hint) {
      setExpiry(isoInDays(hint.days));
    } else {
      setExpiry(defDays != null && defDays > 0 ? isoInDays(defDays) : "");
    }
  }, []);

  // One clear match against the item's own history (or none configured yet)
  // means "use it, silently"; several means the operator picks, same as the
  // scan station's own location prompt.
  const applyLocationSuggestion = useCallback(
    (data: ScanResponse) => {
      const candidates = data.location_candidates ?? [];
      if (candidates.length === 1) {
        setSuggestedLocationId(candidates[0].location_id);
        setLocationCandidates([]);
        return;
      }
      // Two very different situations arrive here as "several candidates":
      //
      //  * the item IS in stock in more than one place — genuinely ambiguous,
      //    so it must be asked (a silent guess could decrement the wrong shelf);
      //  * the item is nowhere yet, so the backend offers EVERY location as an
      //    add target. Nothing is ambiguous — it just has no history.
      //
      // The second case is the whole "scanning a box of new spices" workflow,
      // and forcing a tap per item made it unusable. Zero on-hand across every
      // candidate is what tells them apart, so a brand-new item defaults to the
      // location set at the top of the page while still showing the picker.
      const isNewItemWithNoHistory = candidates.every((c) => Number(c.quantity) === 0);
      const defaultable =
        isNewItemWithNoHistory && selectedLocationId && candidates.some((c) => c.location_id === selectedLocationId);
      setSuggestedLocationId(defaultable ? selectedLocationId : null);
      setLocationCandidates(candidates);
    },
    [selectedLocationId],
  );

  const chooseAddLocation = useCallback(
    (locationId: string) => {
      setSuggestedLocationId(locationId);
      setLocationCandidates([]);
      // Every control in the Add card hands focus back to the barcode field —
      // a hardware scanner types into whatever currently has it, so leaving
      // focus stranded here would swallow the next physical scan.
      focusScanField();
    },
    [focusScanField],
  );

  // ---- Barcode scan mutation ----
  const scanMutation = useMutation({
    mutationFn: async (barcode: string) => {
      const normalized = barcode.trim();
      if (!normalized) throw new Error("Enter a barcode");
      const res = await api.post<ScanResponse>("/api/barcodes/scan", {
        barcode: normalized,
        auto_create: true,
      });
      return res.data;
    },
    onSuccess: (data) => {
      setScanResult(data);
      setScanError(null);
      setManualBarcode("");
      if (data.found) {
        const verb = data.created ? "Created" : "Found";
        setStatus(`${verb}: ${data.item_name ?? data.barcode} (via ${data.source})`);
        applySizePrefill(data);
        applyLocationSuggestion(data);
      } else {
        setStatus(`Barcode not found: ${data.barcode}`);
      }
    },
    onError: (error) => {
      const msg = getApiErrorMessage(error, "Scan failed");
      setScanError(msg);
      setStatus(msg);
    },
  });

  // Whether this check-in goes in as a container batch (N sealed packages of a
  // known size) vs a plain measured amount. Buying "2 each, 0.5 gal" is a batch;
  // buying "2 lb" of loose produce is a direct amount.
  const useContainerForm = isCountUnit(purchaseUnit) && pkgAmount != null && pkgAmount > 0 && Boolean(pkgUnit);

  // The location this specific item will be added to: the auto-detected/chosen
  // suggestion, falling back to the app-wide default only once any ambiguity
  // has been resolved — while candidates are still waiting on a pick, there's
  // no silent fallback (that would defeat the point of asking).
  // A pending pick still blocks the fallback (asking and then silently guessing
  // would defeat the point) — but a pre-applied default counts as resolved, so
  // a new item can be added straight away without a tap.
  const effectiveLocationId =
    suggestedLocationId ?? (locationCandidates.length > 0 ? null : selectedLocationId);

  // ---- Add to inventory mutation ----
  const addMutation = useMutation({
    mutationFn: async () => {
      if (!effectiveLocationId) throw new Error("Select a location first");
      if (!scanResult?.item_id) throw new Error("No item to add");
      const body: Record<string, unknown> = {
        item_id: scanResult.item_id,
        location_id: effectiveLocationId,
        ...(expiry ? { expiration_date: expiry } : {}),
      };
      if (useContainerForm) {
        // Container batch (ADR 0014 × 0016): N sealed packages, each pkgAmount
        // pkgUnit; the server stores it in base units and tracks the containers.
        body.sealed_count = purchaseQty;
        body.container_size = pkgAmount;
        body.container_unit = pkgUnit;
        body.quantity = purchaseQty; // ignored server-side, kept valid (gt=0)
      } else {
        body.quantity = purchaseQty;
        body.unit = purchaseUnit;
      }
      const res = await api.post<AddToInventoryResponse>("/api/inventory/add", body);
      return res.data;
    },
    onSuccess: (data) => {
      const name = data.inventory[0]?.item_name ?? scanResult?.item_name ?? "item";
      // Friendly confirmation in the human's terms — "2 × 0.5 gal (3.79 L)" — not
      // the raw base number (ADR 0016 B).
      const pkg = useContainerForm ? `${purchaseQty} × ${pkgAmount} ${pkgUnit}` : `${purchaseQty} ${purchaseUnit}`;
      const base = humanizeBase(data.added_quantity, data.added_unit);
      const baseNote = base && useContainerForm ? ` (${base})` : "";
      setStatus(`Added ${pkg}${baseNote} of ${name} — scan the next item`);
      queryClient.invalidateQueries({ queryKey: ["inventory"] });
      queryClient.invalidateQueries({ queryKey: ["items"] });
      // Close the loop: clear the result and send focus back to the scan field.
      resetForNewScan();
      setManualBarcode("");
    },
    onError: (error) => {
      setStatus(getApiErrorMessage(error, "Add to inventory failed"));
      // A real failure (bad request, network drop) still needs the scan field
      // reclaimed — the scanned item stays on screen so the user can fix
      // whatever's wrong (e.g. pick a location) and retry the add.
      focusScanField();
    },
  });

  const canAdd = Boolean(scanResult?.found && scanResult?.item_id && effectiveLocationId);

  // ---- Focus choreography ----
  // Focus always lives in the barcode field — that's the workflow's home base
  // (see the field's own comment). Scanning the next barcode while a result is
  // still showing auto-commits it at its default quantity (see
  // handleManualSubmit), so the operator never has to leave the scan field for
  // the common case; the qty field and its +/- steppers are there only for the
  // times the default needs a correction. The scanner and the result stack
  // vertically at every width, so scroll the Add card into view when a result
  // appears — `"nearest"` moves the minimum distance needed (unlike `"center"`,
  // which was scrolling far enough to push the barcode field itself
  // off-screen).
  useEffect(() => {
    const addable = scanResult?.found && scanResult?.item_id;
    const t = window.setTimeout(() => {
      if (addable) {
        addCardRef.current?.scrollIntoView({ behavior: "smooth", block: "nearest" });
      }
      scanInputRef.current?.focus();
    }, 30);
    return () => window.clearTimeout(t);
  }, [scanResult]);

  // ---- Camera scanning ----
  const startCamera = useCallback(async () => {
    setCameraError(null);
    try {
      const codeReader = new BrowserMultiFormatReader();
      const videoInput = videoRef.current;
      if (!videoInput) return;

      setCameraActive(true);
      const controls = await codeReader.decodeFromVideoDevice(
        undefined,
        videoInput,
        (result, error) => {
          if (result) {
            const barcode = result.getText();
            // Stop scanning after first successful read
            controls.stop();
            setCameraActive(false);
            resetForNewScan();
            scanMutation.mutate(barcode);
          }
          // NotFoundException-ish errors are normal (no barcode in frame yet)
          if (error && error.name !== "NotFoundException") {
            console.warn("Scan error:", error);
          }
        },
      );
      controlsRef.current = controls;
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Camera access failed";
      setCameraError(msg);
      setCameraActive(false);
    }
  }, [resetForNewScan, scanMutation]);

  const stopCamera = useCallback(() => {
    controlsRef.current?.stop();
    controlsRef.current = null;
    setCameraActive(false);
    focusScanField();
  }, [focusScanField]);

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      controlsRef.current?.stop();
    };
  }, []);

  // ---- Handle manual barcode submit ----
  // The core rapid-scan loop: if a previous scan is still sitting there ready
  // to add, scanning the NEXT barcode commits it first (at whatever quantity
  // is currently shown — the prefilled default, unless the operator tapped a
  // stepper to correct it) and only then looks up the new code. That's what
  // lets the whole workflow be "scan, scan, scan" with no taps in between.
  const handleManualSubmit = () => {
    const barcode = manualBarcode.trim();
    if (!barcode) return;
    setManualBarcode("");
    if (canAdd) {
      addMutation.mutate(undefined, { onSuccess: () => scanMutation.mutate(barcode) });
    } else {
      resetForNewScan();
      scanMutation.mutate(barcode);
    }
  };

  const busy = scanMutation.isPending || addMutation.isPending;
  const locationOptions = (locationsQuery.data ?? []).map((loc) => ({ value: loc.id, label: loc.name }));
  const locationName = (id: string | null) =>
    locationOptions.find((o) => o.value === id)?.label ?? "this location";
  const noLocations = locationsQuery.data && locationsQuery.data.length === 0;

  return (
    <Stack gap="sm">
      {/* Header + location picker share one compact row instead of two full-width
          ones — on a tablet that row of dead space at the top is exactly what
          pushes the scan result below the fold. */}
      <Group justify="space-between" align="flex-end" wrap="wrap" gap="sm">
        <Group gap="sm" align="baseline">
          <Title order={3}>Barcode Scanner</Title>
          <Button component={Link} to={ROUTES.catalog} variant="subtle" size="xs">
            Back to Catalog
          </Button>
        </Group>
        {/* Location is chosen once and stays put across scans — keep it out of
            the per-scan add form so the loop stays fast. */}
        <Select
          label="Adding scans to location"
          placeholder="Choose a location"
          searchable
          data={locationOptions}
          value={selectedLocationId}
          onChange={(val) => setSelectedLocationId(val)}
          error={noLocations ? "No locations yet — create one in Catalog first." : undefined}
          size="sm"
          w={240}
        />
      </Group>

      {/* One column, not two. Side-by-side split the width, which forced the
          add form's rows to wrap and pushed the Add button off-screen. Stacked,
          the scanner stays a thin strip at the top and the item card gets the
          full width — so the qty/unit/package row fits on one line. */}
      <Stack gap="sm">
        {/* ── Scanner section — collapses to a single input row when the camera
            is off (the usual case: a wedge scanner types straight into it) and
            expands only while the camera is actually running. ── */}
        <Card withBorder padding="sm">
          <Stack gap="xs">
            {/* Manual / wedge-scanner input is the primary control and is always
                re-focused, so a USB/Bluetooth scanner types straight into it. */}
            <TextInput
              ref={scanInputRef}
              label="Barcode"
              value={manualBarcode}
              onChange={(e) => setManualBarcode(e.currentTarget.value)}
              placeholder="Scan or type a UPC/EAN, then Enter"
              inputMode="numeric"
              size="md"
              autoFocus
              rightSectionWidth={96}
              rightSection={
                <Button
                  onClick={handleManualSubmit}
                  loading={scanMutation.isPending}
                  disabled={!manualBarcode.trim()}
                  size="xs"
                  mr="xs"
                >
                  Look Up
                </Button>
              }
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.preventDefault();
                  handleManualSubmit();
                }
              }}
            />
            {/* Camera is opt-in and collapsed to nothing until started — the
                preview box was dead space on every load for a workflow that's
                almost always the wedge scanner above. The <video> stays mounted
                (just hidden) so startCamera's videoRef is always valid;
                `display: none` gives it zero footprint while inactive.
                Running, it takes the height it needs to actually aim with —
                capped in viewport units so the item card below stays in frame
                on a tablet. */}
            <div
              style={{
                position: "relative",
                width: "100%",
                height: cameraActive ? "min(42vh, 340px)" : 0,
                background: cameraActive ? "#111" : "transparent",
                borderRadius: 8,
                overflow: "hidden",
              }}
            >
              <video
                ref={videoRef}
                style={{
                  width: "100%",
                  height: "100%",
                  objectFit: "cover",
                  display: cameraActive ? "block" : "none",
                }}
                playsInline
                muted
              />
            </div>

            {cameraError && (
              <Alert color="red" title="Camera Error">
                {cameraError}
              </Alert>
            )}

            {/* Hint and camera toggle share one line — two stacked lines of
                chrome above the result is exactly what pushes Add below the
                fold. The hint drops out once you're mid-scan; by then you know. */}
            <Group justify="space-between" gap="sm" wrap="nowrap">
              <Text size="xs" c="dimmed" style={{ minWidth: 0 }}>
                {scanResult
                  ? ""
                  : "A USB/Bluetooth scanner acts as a keyboard — just scan; it submits on the trailing Enter."}
              </Text>
              {!cameraActive ? (
                <Button
                  onClick={startCamera}
                  loading={busy}
                  leftSection={<span>📷</span>}
                  variant="subtle"
                  size="xs"
                  style={{ flexShrink: 0 }}
                >
                  Or use the camera instead
                </Button>
              ) : (
                <Button onClick={stopCamera} color="red" variant="outline" size="xs" style={{ flexShrink: 0 }}>
                  Stop Camera
                </Button>
              )}
            </Group>
          </Stack>
        </Card>

        {/* ── Result section ── */}
        <Card withBorder padding="sm">
          {!scanResult && !scanError && (
            <>
              <Title order={4} mb="xs">
                Scan Result
              </Title>
              <Text c="dimmed" size="sm">
                Scan a barcode to see product details
              </Text>
            </>
          )}

          {scanError && (
            <Alert color="red" title="Scan Failed">
              {scanError}
            </Alert>
          )}

          {scanResult && (
            <Stack gap="xs">
              {/* Status badges — also carries the identity, so no separate
                  "Scan Result" heading here; that's dead space once real
                  content is showing. */}
              <Group gap="xs">
                <Badge color={scanResult.found ? "green" : "red"}>
                  {scanResult.found ? "Found" : "Not Found"}
                </Badge>
                {scanResult.created && <Badge color="blue">Auto-Created</Badge>}
                {scanResult.source && (
                  <Badge variant="outline">
                    {scanResult.source === "item.barcode"
                      ? "Local DB"
                      : scanResult.source === "barcode_alias"
                        ? "Alias"
                        : scanResult.source.startsWith("external:")
                          ? `External (${scanResult.source.split(":")[1]})`
                          : scanResult.source}
                  </Badge>
                )}
                <Text size="xs" c="dimmed">
                  {scanResult.barcode}
                </Text>
              </Group>

              {/* Identity row: image + name/brand/size */}
              {scanResult.found && (
                <Group align="flex-start" gap="sm" wrap="nowrap">
                  {scanResult.product?.image_url && (
                    <Image
                      src={scanResult.product.image_url}
                      alt={scanResult.item_name ?? "product"}
                      h={48}
                      w={48}
                      fit="contain"
                      radius="md"
                    />
                  )}
                  <Stack gap={2} style={{ flex: 1, minWidth: 0 }}>
                    <Text fw={600}>{scanResult.product?.name ?? scanResult.item_name}</Text>
                    {scanResult.product?.brand && (
                      <Text size="sm" c="dimmed">
                        {scanResult.product.brand}
                      </Text>
                    )}
                    {(scanResult.product?.size_description ||
                      (scanResult.item?.size_description as string | undefined)) && (
                      <Text size="sm">
                        Sold as:{" "}
                        {scanResult.product?.size_description ??
                          (scanResult.item?.size_description as string | undefined)}
                      </Text>
                    )}
                  </Stack>
                </Group>
              )}

              {/* Add to inventory — the primary action, right up top */}
              {scanResult.found && scanResult.item_id && (
                <Card ref={addCardRef} withBorder p="sm" radius="md" bg="var(--mantine-color-green-light)">
                  <Text size="sm" fw={700} mb={6}>
                    Add to Inventory
                  </Text>
                  <Stack gap="sm">
                    {/* Where this item goes: auto-picked from its own history
                        when that's unambiguous (silent — just shows up already
                        selected below); asked for otherwise, same as the scan
                        station's own location prompt. */}
                    {locationCandidates.length > 0 ? (
                      <Alert color={suggestedLocationId ? "gray" : "blue"} title="Which location?" p="sm">
                        <Text size="xs" mb="xs">
                          {suggestedLocationId ? (
                            <>
                              New item — going to <b>{locationName(suggestedLocationId)}</b> (the location set above).
                              Tap another to change it.
                            </>
                          ) : (
                            <>
                              {scanResult.item_name ?? "This item"} — pick where to add it (it'll be remembered for
                              next time).
                            </>
                          )}
                        </Text>
                        {/* The highlight ring is drawn OUTSIDE the button box
                            (outline + offset), so the row needs its own inset
                            or the ring collides with the alert's edge and the
                            neighbouring buttons. 4px clears a 2px ring at a
                            2px offset. */}
                        <Group gap="sm" style={{ padding: 4 }}>
                          {locationCandidates.map((c) => {
                            const isDefault = c.location_id === suggestedLocationId;
                            return (
                              <Button
                                key={c.location_id}
                                size="xs"
                                // The chosen one is filled and ringed so the
                                // destination is obvious at a glance mid-scan.
                                variant={isDefault ? "filled" : "light"}
                                style={
                                  isDefault
                                    ? { outline: "2px solid var(--mantine-color-blue-4)", outlineOffset: 2 }
                                    : undefined
                                }
                                onClick={() => chooseAddLocation(c.location_id)}
                              >
                                {c.location_name}
                                {Number(c.quantity) > 0 ? ` (${Number(c.quantity)})` : ""}
                              </Button>
                            );
                          })}
                        </Group>
                      </Alert>
                    ) : (
                      <Select
                        label="Location"
                        description={suggestedLocationId ? "Auto-picked from where it's stocked" : undefined}
                        data={locationOptions}
                        value={effectiveLocationId}
                        onChange={(val) => {
                          if (val) chooseAddLocation(val);
                        }}
                        size="sm"
                        w={220}
                      />
                    )}

                    <Group align="flex-end" gap="sm">
                      {/* How many packages you're checking in — a big +/- stepper
                          either side of the number, sized for a tablet tap rather
                          than the number input's own tiny native spinner. No
                          label/description on the input itself — those would add
                          height only on one side and throw off the vertical
                          centering against the +/- buttons. */}
                      <QuantityStepper
                        ref={qtyRef}
                        label="How many"
                        ariaLabel="quantity"
                        value={purchaseQty}
                        onChange={(val) => setPurchaseQty(Number(val) || 0)}
                        onStep={focusScanField}
                        min={0}
                        step={1}
                        inputProps={{
                          onBlur: focusScanField,
                          onKeyDown: (e) => {
                            if (e.key === "Enter" && canAdd && !busy) {
                              e.preventDefault();
                              addMutation.mutate();
                            }
                          },
                        }}
                      />
                      <Select
                        value={purchaseUnit}
                        onChange={(val) => {
                          setPurchaseUnit(val || "each");
                          focusScanField();
                        }}
                        data={UNIT_OPTIONS}
                        label="Unit"
                        size="sm"
                        style={{ width: 100 }}
                      />
                      <Text size="sm" c="dimmed" pb={14}>
                        of
                      </Text>
                      {/* The size of each package — 0.5 gal, 12 ea, 2 oz — same
                          +/- stepper treatment, nudging by the 0.5 step. */}
                      <QuantityStepper
                        label="Package size"
                        ariaLabel="package size"
                        value={pkgAmount ?? ""}
                        onChange={(val) => setPkgAmount(val === "" ? null : Number(val))}
                        onStep={focusScanField}
                        min={0}
                        step={0.5}
                        inputWidth={72}
                        inputProps={{ onBlur: focusScanField }}
                      />
                      <Select
                        value={pkgUnit}
                        onChange={(val) => {
                          setPkgUnit(val || null);
                          focusScanField();
                        }}
                        data={UNIT_OPTIONS}
                        label="Unit"
                        placeholder="unit"
                        size="sm"
                        style={{ width: 100 }}
                        clearable
                      />
                    </Group>

                    {/* Best-by, with quick nudges. When it arrives prefilled the
                        field says so and offers the reasoning behind it — an
                        estimate that can't be told apart from a date someone
                        read off the package is worse than an empty field,
                        because nobody thinks to check it. */}
                    <Group align="flex-end" gap="sm">
                      <TextInput
                        type="date"
                        value={expiry}
                        onChange={(e) => {
                          setExpiry(e.currentTarget.value);
                          // Their date now — stop claiming we derived it.
                          setExpirySource(null);
                        }}
                        onBlur={focusScanField}
                        label={
                          <Group gap={6} align="center" wrap="nowrap">
                            <span>Best-by (optional)</span>
                            {expirySource && (
                              <Tooltip
                                multiline
                                w={280}
                                withArrow
                                events={{ hover: true, focus: true, touch: true }}
                                label={
                                  expirySource === "learned" && shelfLifeHint
                                    ? `Estimated, not read off the package. Your last ${shelfLifeHint.observations} best-by dates for this item averaged ${shelfLifeHint.days} days from check-in${
                                        shelfLifeHint.spread_days > 0
                                          ? ` (they varied by ${shelfLifeHint.spread_days} day${shelfLifeHint.spread_days === 1 ? "" : "s"})`
                                          : ""
                                      }. It's a guess from your own history — check the package and correct it if it differs.`
                                    : "Estimated from this item's configured default shelf life, which is usually a rough category guess — not the actual package date. Check the package and correct it if it differs."
                                }
                              >
                                <Badge
                                  size="xs"
                                  variant="light"
                                  color={expirySource === "learned" ? "blue" : "gray"}
                                  style={{ cursor: "help" }}
                                  tabIndex={0}
                                  aria-label="How this best-by date was estimated"
                                >
                                  {expirySource === "learned" ? "estimated ⓘ" : "default ⓘ"}
                                </Badge>
                              </Tooltip>
                            )}
                          </Group>
                        }
                        size="sm"
                        style={{ width: 170 }}
                      />
                      <Button.Group>
                        <Button
                          variant="default"
                          size="md"
                          h={48}
                          fz="md"
                          onClick={() => {
                            setExpiry(isoInDays(7));
                            setExpirySource(null);
                            focusScanField();
                          }}
                        >
                          +1wk
                        </Button>
                        <Button
                          variant="default"
                          size="md"
                          h={48}
                          fz="md"
                          onClick={() => {
                            setExpiry(isoInDays(14));
                            setExpirySource(null);
                            focusScanField();
                          }}
                        >
                          +2wk
                        </Button>
                        <Button
                          variant="default"
                          size="md"
                          h={48}
                          fz="md"
                          onClick={() => {
                            setExpiry(isoInDays(30));
                            setExpirySource(null);
                            focusScanField();
                          }}
                        >
                          +1mo
                        </Button>
                        <Button
                          variant="default"
                          size="md"
                          h={48}
                          fz="md"
                          onClick={() => {
                            setExpiry("");
                            setExpirySource(null);
                            focusScanField();
                          }}
                          disabled={!expiry}
                        >
                          Clear
                        </Button>
                      </Button.Group>
                    </Group>

                    <Button
                      onClick={() => addMutation.mutate()}
                      loading={addMutation.isPending}
                      disabled={!canAdd}
                      color="green"
                      size="xl"
                      fullWidth
                      style={{ minHeight: 72, fontSize: "1.35rem" }}
                    >
                      Add & next
                    </Button>
                    <Text size="xs" c="dimmed">
                      {useContainerForm
                        ? `Checking in ${purchaseQty} × ${pkgAmount} ${pkgUnit ?? ""} — tracked as sealed packages. Enter adds and returns to the scan field.`
                        : "Enter in How many adds and returns to the scan field."}
                    </Text>
                  </Stack>
                </Card>
              )}

              {/* Not found — create manually */}
              {!scanResult.found && (
                <Alert color="yellow" title="Barcode not found">
                  <Stack gap="xs">
                    <Text size="sm">
                      No product data found for barcode {scanResult.barcode}. You can add this item manually.
                    </Text>
                    <Button
                      component={Link}
                      to={`${ROUTES.catalogAddItem}?barcode=${encodeURIComponent(scanResult.barcode)}`}
                      variant="outline"
                      size="xs"
                    >
                      Add Item Manually
                    </Button>
                  </Stack>
                </Alert>
              )}

              {/* Product detail / nutrition reference — below the action */}
              {scanResult.product && (
                <Stack gap="xs">
                  {scanResult.product.category && (
                    <Text size="sm">
                      <strong>Category:</strong> {scanResult.product.category}
                    </Text>
                  )}
                  {scanResult.product.serving_size_g != null && (
                    <Text size="sm">
                      <strong>Serving size:</strong> {scanResult.product.serving_size_g}g
                    </Text>
                  )}

                  {/* Nutrition summary */}
                  {scanResult.product.nutrition && (
                    <Card withBorder p="xs" radius="sm">
                      <Text size="xs" fw={700} mb={4}>
                        Nutrition (per 100g)
                      </Text>
                      <Group gap="xs">
                        {scanResult.product.nutrition["energy-kcal_100g"] != null && (
                          <Badge size="sm" variant="light">
                            {Math.round(Number(scanResult.product.nutrition["energy-kcal_100g"]))} kcal
                          </Badge>
                        )}
                        {scanResult.product.nutrition.proteins_100g != null && (
                          <Badge size="sm" variant="light" color="teal">
                            {Number(scanResult.product.nutrition.proteins_100g).toFixed(1)}g protein
                          </Badge>
                        )}
                        {scanResult.product.nutrition.carbohydrates_100g != null && (
                          <Badge size="sm" variant="light" color="yellow">
                            {Number(scanResult.product.nutrition.carbohydrates_100g).toFixed(1)}g carbs
                          </Badge>
                        )}
                        {scanResult.product.nutrition.fat_100g != null && (
                          <Badge size="sm" variant="light" color="orange">
                            {Number(scanResult.product.nutrition.fat_100g).toFixed(1)}g fat
                          </Badge>
                        )}
                        {scanResult.product.nutrition.fiber_100g != null && (
                          <Badge size="sm" variant="light" color="grape">
                            {Number(scanResult.product.nutrition.fiber_100g).toFixed(1)}g fiber
                          </Badge>
                        )}
                        {scanResult.product.nutrition.sugars_100g != null && (
                          <Badge size="sm" variant="light" color="pink">
                            {Number(scanResult.product.nutrition.sugars_100g).toFixed(1)}g sugar
                          </Badge>
                        )}
                        {scanResult.product.nutrition["saturated-fat_100g"] != null && (
                          <Badge size="sm" variant="light" color="orange">
                            {Number(scanResult.product.nutrition["saturated-fat_100g"]).toFixed(1)}g sat. fat
                          </Badge>
                        )}
                        {scanResult.product.nutrition.sodium_100g != null && (
                          <Badge size="sm" variant="light" color="cyan">
                            {Number(scanResult.product.nutrition.sodium_100g).toFixed(2)}g sodium
                          </Badge>
                        )}
                        {scanResult.product.nutri_score && (
                          <Badge
                            size="sm"
                            color={
                              scanResult.product.nutri_score === "a"
                                ? "green"
                                : scanResult.product.nutri_score === "b"
                                  ? "lime"
                                  : scanResult.product.nutri_score === "c"
                                    ? "yellow"
                                    : scanResult.product.nutri_score === "d"
                                      ? "orange"
                                      : "red"
                            }
                          >
                            Nutri-Score: {scanResult.product.nutri_score.toUpperCase()}
                          </Badge>
                        )}
                        {scanResult.product.nova_group != null && (
                          <Badge size="sm" variant="outline">
                            NOVA {scanResult.product.nova_group}
                          </Badge>
                        )}
                        {scanResult.product.eco_score && (
                          <Badge size="sm" variant="outline" color="green">
                            Eco-Score: {scanResult.product.eco_score.toUpperCase()}
                          </Badge>
                        )}
                      </Group>
                    </Card>
                  )}

                  {scanResult.product.allergens && scanResult.product.allergens.length > 0 && (
                    <Text size="sm">
                      <strong>Allergens:</strong> {scanResult.product.allergens.join(", ")}
                    </Text>
                  )}

                  {scanResult.product.ingredients_text && (
                    <Text size="xs" c="dimmed">
                      <strong>Ingredients:</strong> {scanResult.product.ingredients_text}
                    </Text>
                  )}
                </Stack>
              )}

              {/* Scan another (also just refocuses the field) */}
              <Button
                variant="subtle"
                size="xs"
                onClick={() => {
                  resetForNewScan();
                  setManualBarcode("");
                  focusScanField();
                }}
              >
                Clear & scan another
              </Button>
            </Stack>
          )}
        </Card>
      </Stack>
    </Stack>
  );
}
