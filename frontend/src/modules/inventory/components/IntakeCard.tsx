import { Alert, Badge, Button, Card, Group, Select, Stack, Text, TextInput, Title } from "@mantine/core";
import { useMutation } from "@tanstack/react-query";
import { Sparkles } from "lucide-react";
import { forwardRef, useEffect, useImperativeHandle, useState } from "react";

import { api } from "@/api/client";
import { getApiErrorMessage } from "@/api/errors";
import { QuantityStepper } from "@/components/QuantityStepper";
import { type NutritionCandidate, type Item, UsdaSearchModal } from "@/modules/catalog";

export type ProductLookup = {
  barcode: string;
  found: boolean;
  item_id: string | null;
  item_name: string | null;
  item: Record<string, unknown> | null;
  product: {
    name: string;
    brand: string | null;
    size_quantity: number | null;
    size_unit: string | null;
  } | null;
  location_candidates: { location_id: string; location_name: string; quantity: string }[];
};

const UNIT_OPTIONS = [
  "each",
  "lb",
  "oz",
  "g",
  "kg",
  "ml",
  "l",
  "floz",
  "gal",
  "pint",
  "qt",
  "cup",
].map((u) => ({ value: u, label: u === "floz" ? "fl oz" : u }));

// The backend spells some units differently than the picker. Fluid ounces are
// `floz` (volume) and must never fold onto `oz` (weight) — that would turn a
// liquid package size into a mass (ADR 0017 §3).
const UNIT_ALIASES: Record<string, string> = { ea: "each", "fl oz": "floz" };

function normalizeUnit(unit: string | null | undefined): string | null {
  if (!unit) return null;
  const mapped = UNIT_ALIASES[unit] ?? unit;
  return UNIT_OPTIONS.some((o) => o.value === mapped) ? mapped : null;
}

function isoInDays(days: number): string {
  const d = new Date();
  d.setDate(d.getDate() + days);
  return d.toISOString().slice(0, 10);
}

type Props = {
  lookup: ProductLookup;
  locations: { id: string; name: string }[];
  defaultLocationId: string | null;
  onAdded: (message: string, transactionId: string | null) => void;
  onCancel: () => void;
  /** A brand-new item was created for a barcode nothing matched — swap the
   *  lookup for a "found" one against it, so intake continues without a rescan. */
  onItemCreated: (item: Item) => void;
  /** Return keyboard focus to the scan field. A wedge scanner types into
   *  whatever holds focus, so every control here hands it back after its own
   *  action (ADR 0023 D12, ported from the scan page) instead of leaving it
   *  sitting on a button or select where the next scan's keystrokes go nowhere. */
  focusScan: () => void;
};

export type IntakeCardHandle = {
  /** True once there's a saved item, a location, and nothing in flight — the
   *  same condition the "Add to inventory" button's own `disabled` uses.
   *  False for the not-in-the-catalog form, which always needs an explicit tap. */
  isReady: boolean;
  /** Commit the pending entry exactly as the button would. Rejects if the add
   *  fails, so a caller chaining "commit, then look up the next scan" naturally
   *  stops there instead of abandoning the failed item. */
  commit: () => Promise<void>;
};

/**
 * The barcode/name search came up empty (produce, deli counter, anything with
 * no UPC a provider knows about — e.g. bananas, a pork butt roast). Rather than
 * a dead end that sends the household to a different page, this creates the
 * item right here — optionally matched to USDA nutrition — and hands back to
 * the normal check-in flow above.
 */
function NewItemForm({
  barcode,
  onCreated,
  onCancel,
  focusScan,
}: {
  barcode: string;
  onCreated: (item: Item) => void;
  onCancel: () => void;
  focusScan: () => void;
}) {
  const [name, setName] = useState("");
  const [usdaPickerOpen, setUsdaPickerOpen] = useState(false);
  const [usdaMatch, setUsdaMatch] = useState<{ fdcId: number; query: string; description: string | null } | null>(
    null,
  );
  const [error, setError] = useState<string | null>(null);

  const onUsdaPick = (candidate: NutritionCandidate, query: string) => {
    if (candidate.fdc_id == null) return;
    setUsdaMatch({ fdcId: candidate.fdc_id, query, description: candidate.description });
    setUsdaPickerOpen(false);
    focusScan();
  };

  const create = useMutation({
    mutationFn: async () => {
      const res = await api.post<Item>("/api/items", {
        name: name.trim(),
        barcode: barcode ? barcode : null,
      });
      let created = res.data;
      if (usdaMatch) {
        try {
          const applied = await api.post<Item>(`/api/items/${created.id}/nutrition-from-usda`, {
            fdc_id: usdaMatch.fdcId,
            ...(usdaMatch.query.trim() ? { q: usdaMatch.query.trim() } : {}),
          });
          created = applied.data;
        } catch {
          // Item already exists — proceed to check-in even if the USDA
          // re-tag failed; it can be matched later from Catalog's Edit.
        }
      }
      return created;
    },
    onSuccess: (created) => {
      onCreated(created);
      focusScan();
    },
    onError: (err) => {
      setError(getApiErrorMessage(err, "Couldn't create that item"));
      focusScan();
    },
  });

  return (
    <Alert color="yellow" variant="light" title="Not in the catalog yet">
      <Stack gap="xs">
        <Text size="sm">
          Nothing matches{barcode ? ` barcode ${barcode}` : " that"}. Add it now — optionally match it to a USDA
          food for nutrition — and it'll go straight into check-in below.
        </Text>

        <TextInput
          label="Item name"
          placeholder="e.g. Bananas"
          value={name}
          onChange={(e) => setName(e.currentTarget.value)}
          onBlur={focusScan}
          autoFocus
        />

        <Group gap="xs" wrap="wrap">
          <Button
            size="compact-sm"
            variant="light"
            color="grape"
            leftSection={<Sparkles size={14} />}
            onClick={() => setUsdaPickerOpen(true)}
            disabled={!name.trim()}
          >
            Match to a USDA food…
          </Button>
          {usdaMatch ? (
            <Text size="xs" c="dimmed">
              Matched: <b>{usdaMatch.description ?? "selected food"}</b>
            </Text>
          ) : null}
        </Group>

        {error ? (
          <Alert color="red" variant="light">
            {error}
          </Alert>
        ) : null}

        <Group gap="sm">
          <Button size="xs" color="green" loading={create.isPending} disabled={!name.trim()} onClick={() => create.mutate()}>
            Create item
          </Button>
          <Button size="xs" variant="subtle" onClick={onCancel}>
            Dismiss
          </Button>
        </Group>
      </Stack>

      <UsdaSearchModal
        seedQuery={name}
        opened={usdaPickerOpen}
        onClose={() => {
          setUsdaPickerOpen(false);
          focusScan();
        }}
        onPick={onUsdaPick}
      />
    </Alert>
  );
}

/**
 * The "In" branch (ADR 0023 Phase 2) — check in what a scan just identified.
 *
 * A check-in is three separable things (ADR 0016): how many packages, how big
 * each package is, and its best-by. Keeping the package size explicit is what
 * lets intake record CONTAINERS rather than a bare quantity, which is the
 * precondition for ever finishing one (D9) — stock checked in without it can
 * never be consumed by container.
 */
export const IntakeCard = forwardRef<IntakeCardHandle, Props>(function IntakeCard(
  { lookup, locations, defaultLocationId, onAdded, onCancel, onItemCreated, focusScan },
  ref,
) {
  const item = (lookup.item ?? {}) as Record<string, unknown>;
  const candidates = lookup.location_candidates ?? [];
  // Catch weight (ADR 0025 §1): this product's packages all scan the same
  // barcode and all weigh something different, so the size prefilled below is a
  // nominal to correct, not a fact to accept. The server refuses a by-the-each
  // check-in for these, which is what stops a 7 lb roast booking as 4.87 lb.
  const variableWeight = Boolean(item.variable_weight);

  const [packages, setPackages] = useState<number | string>(1);
  const [pkgAmount, setPkgAmount] = useState<number | string>("");
  const [pkgUnit, setPkgUnit] = useState<string | null>(null);
  const [expiry, setExpiry] = useState("");
  const [locationId, setLocationId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Prefill from the scanned product: one package, its known size shown
  // separately so "2 × 0.5 gal" reads the way a human says it, and a best-by
  // seeded from the item's default shelf life.
  useEffect(() => {
    const sizeQty = lookup.product?.size_quantity ?? (item.size_quantity as number | null) ?? null;
    const unit = normalizeUnit(lookup.product?.size_unit ?? (item.size_unit as string | null | undefined));
    setPackages(1);
    setPkgAmount(sizeQty != null && sizeQty > 0 ? sizeQty : "");
    setPkgUnit(unit);
    const defDays = item.default_expiration_days as number | null | undefined;
    setExpiry(defDays != null && defDays > 0 ? isoInDays(defDays) : "");
    // One clear match against the item's own history means "use it silently";
    // several means the operator picks (mirrors the scan station's prompt).
    setLocationId(candidates.length === 1 ? candidates[0].location_id : defaultLocationId);
    setError(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [lookup]);

  const add = useMutation({
    mutationFn: async () => {
      if (!lookup.item_id) throw new Error("Nothing to add");
      if (!locationId) throw new Error("Pick a location first");
      const count = variableWeight ? 1 : Number(packages) || 1;
      const size = Number(pkgAmount);
      const body: Record<string, unknown> = {
        item_id: lookup.item_id,
        location_id: locationId,
        ...(expiry ? { expiration_date: expiry } : {}),
      };
      if (size > 0 && pkgUnit) {
        // Container batch (ADR 0014 × 0016): N sealed packages of this size.
        body.sealed_count = count;
        body.container_size = size;
        body.container_unit = pkgUnit;
        body.quantity = count; // ignored server-side for a container batch
      } else {
        body.quantity = count;
        body.unit = "each";
      }
      const res = await api.post<{ transaction_ids: string[] }>("/api/inventory/add", body);
      const label =
        size > 0 && pkgUnit
          ? `Added ${count} × ${size} ${pkgUnit} of ${lookup.item_name ?? "item"}`
          : `Added ${count} × ${lookup.item_name ?? "item"}`;
      return { label, transactionId: res.data.transaction_ids?.[0] ?? null };
    },
    onSuccess: (result) => onAdded(result.label, result.transactionId),
    onError: (err) => {
      setError(getApiErrorMessage(err, "Couldn't add that"));
      focusScan();
    },
  });

  useImperativeHandle(ref, () => ({
    isReady: lookup.found && Boolean(lookup.item_id) && Boolean(locationId) && !add.isPending,
    commit: () => add.mutateAsync().then(() => undefined),
  }));

  if (!lookup.found || !lookup.item_id) {
    return <NewItemForm barcode={lookup.barcode} onCreated={onItemCreated} onCancel={onCancel} focusScan={focusScan} />;
  }

  const noSize = !(Number(pkgAmount) > 0 && pkgUnit);

  return (
    <Card withBorder padding="md" radius="md">
      <Stack gap="sm">
        <Group justify="space-between" align="center">
          <Group gap="xs">
            <Title order={5}>{lookup.item_name}</Title>
            {lookup.product?.brand ? (
              <Badge variant="light" color="gray">
                {lookup.product.brand}
              </Badge>
            ) : null}
          </Group>
          <Button size="xs" variant="subtle" color="gray" onClick={onCancel}>
            Cancel
          </Button>
        </Group>

        <Group align="flex-end" gap="sm" wrap="wrap">
          <QuantityStepper
            label="Packages"
            ariaLabel="packages"
            min={1}
            // A catch-weight entry describes ONE package, because the weight
            // below is that package's. Letting the count rise would record N
            // roasts as all weighing the same — the exact fiction the
            // per-package model exists to remove (ADR 0025 §1). Scan the next
            // one separately; the lane is built for that.
            max={variableWeight ? 1 : undefined}
            disabled={variableWeight}
            value={variableWeight ? 1 : packages}
            onChange={setPackages}
            onStep={focusScan}
            inputProps={{
              onBlur: focusScan,
              onKeyDown: (e) => {
                if (e.key === "Enter" && locationId && !add.isPending) {
                  e.preventDefault();
                  add.mutate();
                }
              },
            }}
          />
          {/* Same +/- stepper as Packages, nudging by half a unit — 0.5 gal,
              12 ea, 2 oz are all common package sizes worth a half-step. */}
          <QuantityStepper
            label={variableWeight ? "This one weighs" : "Each contains"}
            ariaLabel="package size"
            value={pkgAmount}
            onChange={setPkgAmount}
            onStep={focusScan}
            min={0}
            step={0.5}
            inputWidth={72}
            inputProps={{ onBlur: focusScan }}
          />
          <Select
            label="Unit"
            data={UNIT_OPTIONS}
            value={pkgUnit}
            onChange={(v) => {
              setPkgUnit(v);
              focusScan();
            }}
            onBlur={focusScan}
            w={110}
            searchable
            aria-label="package unit"
          />
        </Group>

        {variableWeight && (
          <Text size="xs" c="orange.7">
            Weight varies by package — check the label and correct the amount above if it isn't
            {pkgAmount ? ` ${pkgAmount}` : ""} {pkgUnit ?? ""}. One at a time, since that weight is
            this package's; scan the next one after.
          </Text>
        )}

        <Group align="flex-end" gap="sm">
          <TextInput
            label="Best-by"
            type="date"
            value={expiry}
            onChange={(e) => setExpiry(e.currentTarget.value)}
            onBlur={focusScan}
            w={160}
          />
          <Button.Group>
            <Button
              variant="default"
              onClick={() => {
                setExpiry(isoInDays(7));
                focusScan();
              }}
            >
              +1wk
            </Button>
            <Button
              variant="default"
              onClick={() => {
                setExpiry(isoInDays(14));
                focusScan();
              }}
            >
              +2wk
            </Button>
            <Button
              variant="default"
              onClick={() => {
                setExpiry(isoInDays(30));
                focusScan();
              }}
            >
              +1mo
            </Button>
            <Button
              variant="default"
              disabled={!expiry}
              onClick={() => {
                setExpiry("");
                focusScan();
              }}
            >
              Clear
            </Button>
          </Button.Group>
        </Group>

        {/* Not a validation error — plenty of things have no meaningful package
            size. It is worth saying plainly, because it is the difference
            between stock you can finish by container and stock you can't. */}
        {noSize ? (
          <Text size="xs" c="dimmed">
            No package size, so this is tracked as a plain count — "Finished it" won't be available for it.
          </Text>
        ) : null}

        {candidates.length > 1 ? (
          <Select
            label="Where is it going?"
            data={candidates.map((c) => ({
              value: c.location_id,
              label: `${c.location_name} (${Number(c.quantity)})`,
            }))}
            value={locationId}
            onChange={(v) => {
              setLocationId(v);
              focusScan();
            }}
            onBlur={focusScan}
            aria-label="intake location"
          />
        ) : (
          <Select
            label="Location"
            data={locations.map((l) => ({ value: l.id, label: l.name }))}
            value={locationId}
            onChange={(v) => {
              setLocationId(v);
              focusScan();
            }}
            onBlur={focusScan}
            aria-label="intake location"
          />
        )}

        {error ? (
          <Alert color="red" variant="light">
            {error}
          </Alert>
        ) : null}

        <Group>
          <Button color="green" loading={add.isPending} disabled={!locationId} onClick={() => add.mutate()}>
            Add to inventory
          </Button>
        </Group>
      </Stack>
    </Card>
  );
});
