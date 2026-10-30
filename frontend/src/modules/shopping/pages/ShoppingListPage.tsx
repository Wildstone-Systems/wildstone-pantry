import {
  ActionIcon,
  Alert,
  Badge,
  Button,
  Card,
  Checkbox,
  Chip,
  Group,
  Image,
  Paper,
  Stack,
  Text,
  TextInput,
  Title,
} from "@mantine/core";
import { useMemo, useState } from "react";

import { getApiErrorMessage } from "@/api/errors";
import { QuantityStepper } from "@/components/QuantityStepper";
import type { ShoppingListEntry } from "@/api/types";

import {
  createInstacartLink,
  fetchExport,
  pushToKrogerCart,
  useAddFromOffer,
  useAddShoppingItem,
  useDeleteShoppingItem,
  useExportTargets,
  useOfferSearch,
  useShoppingList,
  useUpdateShoppingItem,
} from "../hooks/shopping-list-hooks";
import { HandoffStartCard } from "../components/HandoffStartCard";
import { MatchOfferModal } from "../components/MatchOfferModal";
import { PurchaseIntakeCard } from "./PurchaseIntakeCard";

const PROVIDER_LABEL: Record<string, string> = {
  kroger: "Kroger",
  food_db: "USDA",
  off: "Open Food Facts",
};
const PROVIDER_COLOR: Record<string, string> = {
  kroger: "indigo",
  food_db: "teal",
  off: "orange",
};

export function ShoppingListPage() {
  const list = useShoppingList();
  const addItem = useAddShoppingItem();
  const updateItem = useUpdateShoppingItem();
  const deleteItem = useDeleteShoppingItem();

  const exportTargets = useExportTargets();
  const addFromOffer = useAddFromOffer();
  const [name, setName] = useState("");
  const [qty, setQty] = useState<number | string>(1);
  const [exportMsg, setExportMsg] = useState<string | null>(null);
  const [cartMsg, setCartMsg] = useState<{ color: string; text: string } | null>(null);
  const [cartBusy, setCartBusy] = useState(false);
  const [search, setSearch] = useState("");
  const [providerFilter, setProviderFilter] = useState("all");
  const [matchEntry, setMatchEntry] = useState<ShoppingListEntry | null>(null);
  const offers = useOfferSearch(search);

  const allOffers = offers.data?.offers ?? [];
  const providerCounts = useMemo(() => {
    const counts: Record<string, number> = {};
    for (const o of allOffers) counts[o.provider] = (counts[o.provider] ?? 0) + 1;
    return counts;
  }, [allOffers]);
  const providers = Object.keys(providerCounts);
  const visibleOffers =
    providerFilter === "all" ? allOffers : allOffers.filter((o) => o.provider === providerFilter);

  const runExport = async (target: string) => {
    setExportMsg(null);
    try {
      const result = await fetchExport(target);
      if (result.format === "csv") {
        const blob = new Blob([result.body], { type: result.content_type });
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = result.filename ?? "shopping-list.csv";
        a.click();
        URL.revokeObjectURL(url);
        setExportMsg("Downloaded CSV.");
      } else {
        await navigator.clipboard?.writeText(result.body);
        setExportMsg("Copied the list to the clipboard.");
      }
    } catch (err) {
      setExportMsg(getApiErrorMessage(err, "Export failed"));
    }
  };

  const sendToInstacart = async () => {
    setCartMsg(null);
    setCartBusy(true);
    try {
      const result = await createInstacartLink();
      window.open(result.url, "_blank", "noopener");
      setCartMsg({ color: "green", text: `Opened an Instacart list with ${result.count} items — pick a store there.` });
    } catch (err) {
      const msg = getApiErrorMessage(err, "Could not create the Instacart link");
      // 409 = dormant/not configured; keep the message friendly.
      setCartMsg({ color: "yellow", text: msg });
    } finally {
      setCartBusy(false);
    }
  };

  const sendToKrogerCart = async () => {
    setCartMsg(null);
    setCartBusy(true);
    try {
      const result = await pushToKrogerCart();
      const addedN = result.added.length;
      const skippedN = result.skipped.length;
      const failedN = result.failed?.length ?? 0;
      const skipNote = skippedN
        ? ` ${skippedN} skipped (no store match): ${result.skipped.map((s) => s.name).join(", ")}.`
        : "";
      const failNote = failedN
        ? ` ${failedN} couldn't be added (Kroger rejected): ${result.failed.map((s) => s.name).join(", ")}.`
        : "";
      setCartMsg({
        color: addedN ? "green" : "yellow",
        text: `${addedN} item${addedN === 1 ? "" : "s"} added to your Kroger cart.${skipNote}${failNote}`,
      });
    } catch (err) {
      setCartMsg({ color: "red", text: getApiErrorMessage(err, "Could not send to Kroger cart") });
    } finally {
      setCartBusy(false);
    }
  };

  const entries = list.data ?? [];
  const pending = entries.filter((e) => e.status === "pending");
  const purchased = entries.filter((e) => e.status === "purchased");

  const add = () => {
    const trimmed = name.trim();
    if (!trimmed) return;
    addItem.mutate({ name: trimmed, quantity: Number(qty) || 1 });
    setName("");
    setQty(1);
  };

  const row = (e: ShoppingListEntry) => (
    <Group
      key={e.id}
      justify="space-between"
      wrap="nowrap"
      py="xs"
      style={{ borderBottom: "1px solid var(--mantine-color-default-border)" }}
    >
      <Group gap="sm" wrap="nowrap" style={{ minWidth: 0 }}>
        <Checkbox
          checked={e.status === "purchased"}
          onChange={(ev) =>
            updateItem.mutate({ id: e.id, status: ev.currentTarget.checked ? "purchased" : "pending" })
          }
          aria-label={`Mark ${e.name} purchased`}
        />
        <div style={{ minWidth: 0 }}>
          <Text td={e.status === "purchased" ? "line-through" : undefined}>
            {e.name} · {String(e.quantity)} {e.unit}
            {e.chosen_offer?.size ? (
              <Text span c="dimmed">
                {" "}
                ({e.chosen_offer.size})
              </Text>
            ) : null}
          </Text>
          <Group gap={6}>
            <Badge size="xs" color={e.source === "auto" ? "blue" : "gray"} variant="light">
              {e.source}
            </Badge>
            {e.chosen_offer ? (
              <Badge size="xs" color="grape" variant="light">
                {e.chosen_offer.vendor ?? e.chosen_offer.brand ?? e.chosen_offer.provider}
                {e.chosen_offer.price != null ? ` · $${e.chosen_offer.price.toFixed(2)}` : ""}
              </Badge>
            ) : null}
            {e.on_hand != null ? (
              <Text span size="xs" c="dimmed">
                on hand: {String(e.on_hand)}
              </Text>
            ) : null}
          </Group>
        </div>
      </Group>
      <Group gap={4} wrap="nowrap" style={{ flexShrink: 0 }}>
        {e.status !== "purchased" ? (
          <Button
            size="compact-xs"
            variant="subtle"
            color="grape"
            onClick={() => setMatchEntry(e)}
          >
            {e.chosen_offer ? "Change store item" : "Match to store"}
          </Button>
        ) : null}
        <ActionIcon
          variant="subtle"
          color="red"
          onClick={() => deleteItem.mutate(e.id)}
          aria-label={`Remove ${e.name}`}
        >
          ✕
        </ActionIcon>
      </Group>
    </Group>
  );

  return (
    <Stack>
      <Title order={2}>Shopping List</Title>
      <Text c="dimmed">
        Auto entries appear when stock drops below an item's restock level; add anything else by hand.
      </Text>

      <Card withBorder padding="md">
        <Group align="flex-end">
          <TextInput
            label="Add an item"
            placeholder="e.g. Paper towels"
            value={name}
            onChange={(e) => setName(e.currentTarget.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") add();
            }}
            style={{ flex: 1 }}
          />
          <QuantityStepper label="Qty" ariaLabel="quantity" min={1} value={qty} onChange={setQty} />
          <Button onClick={add} loading={addItem.isPending} disabled={!name.trim()}>
            Add
          </Button>
        </Group>
      </Card>

      <Card withBorder padding="md">
        <Stack gap="sm">
          <div>
            <Title order={4}>Search &amp; add from a store</Title>
            <Text size="xs" c="dimmed">
              Pick a real product — it's added to the list and saved to your catalog. Food results come from Open
              Food Facts / USDA; retailer results arrive with vendor integrations.
            </Text>
          </div>
          <TextInput
            placeholder="Search products, e.g. marinara sauce"
            value={search}
            onChange={(e) => setSearch(e.currentTarget.value)}
            aria-label="Search products to add"
          />
          {offers.isFetching ? (
            <Text size="sm" c="dimmed">
              Searching…
            </Text>
          ) : null}
          {offers.data?.throttled?.length ? (
            <Text size="xs" c="dimmed">
              Some sources were rate-limited — try again shortly.
            </Text>
          ) : null}
          {offers.data && allOffers.length === 0 && !offers.isFetching && search.trim().length >= 2 ? (
            <Text size="sm" c="dimmed">
              No matches. (Non-food items need a retailer integration like Kroger.)
            </Text>
          ) : null}

          {providers.length > 1 ? (
            <Chip.Group multiple={false} value={providerFilter} onChange={(v) => setProviderFilter(v as string)}>
              <Group gap="xs">
                <Chip value="all" size="xs" variant="outline">
                  All ({allOffers.length})
                </Chip>
                {providers.map((p) => (
                  <Chip key={p} value={p} size="xs" variant="outline">
                    {PROVIDER_LABEL[p] ?? p} ({providerCounts[p]})
                  </Chip>
                ))}
              </Group>
            </Chip.Group>
          ) : null}

          <Stack gap="xs">
            {visibleOffers.map((o, i) => (
              <Paper key={`${o.provider}-${o.vendor_sku ?? o.upc ?? o.name}-${i}`} withBorder p="xs" radius="md">
                <Group justify="space-between" wrap="nowrap" gap="sm">
                  <Group gap="sm" wrap="nowrap" style={{ flex: 1, minWidth: 0 }}>
                    {o.image_url ? (
                      <Image
                        src={o.image_url}
                        w={52}
                        h={52}
                        fit="contain"
                        radius="sm"
                        alt={o.name}
                        style={{ flexShrink: 0, background: "var(--mantine-color-default-hover)" }}
                      />
                    ) : (
                      <Paper w={52} h={52} radius="sm" bg="var(--mantine-color-default-hover)" style={{ flexShrink: 0 }}>
                        <Group h="100%" justify="center">
                          <Text size="lg" c="dimmed">
                            🛒
                          </Text>
                        </Group>
                      </Paper>
                    )}
                    <div style={{ minWidth: 0 }}>
                      <Text size="sm" fw={500} lineClamp={2}>
                        {o.name}
                      </Text>
                      <Group gap={6} mt={2}>
                        {o.brand ? (
                          <Text span size="xs" c="dimmed">
                            {o.brand}
                          </Text>
                        ) : null}
                        {o.size ? (
                          <Badge size="xs" variant="light" color="blue">
                            {o.size}
                          </Badge>
                        ) : null}
                        {o.price != null ? (
                          <Badge size="xs" variant="light" color="green">
                            ${o.price.toFixed(2)}
                          </Badge>
                        ) : null}
                        <Badge size="xs" variant="light" color={PROVIDER_COLOR[o.provider] ?? "gray"}>
                          {PROVIDER_LABEL[o.provider] ?? o.provider}
                        </Badge>
                      </Group>
                    </div>
                  </Group>
                  <Button
                    size="compact-sm"
                    variant="light"
                    style={{ flexShrink: 0 }}
                    loading={addFromOffer.isPending}
                    onClick={async () => {
                      await addFromOffer.mutateAsync({ ...o });
                      setSearch("");
                      setProviderFilter("all");
                    }}
                  >
                    Add
                  </Button>
                </Group>
              </Paper>
            ))}
          </Stack>
        </Stack>
      </Card>

      <Card withBorder padding="md">
        <Stack>
          <Title order={4}>To buy ({pending.length})</Title>
          {pending.length === 0 ? (
            <Text c="dimmed">Nothing to buy right now.</Text>
          ) : (
            <Stack gap={0}>{pending.map(row)}</Stack>
          )}
        </Stack>
      </Card>

      <HandoffStartCard pendingCount={pending.length} />

      <Card withBorder padding="md">
        <Title order={4} mb="sm">
          Export
        </Title>
        {exportMsg ? (
          <Alert color="blue" mb="sm">
            {exportMsg}
          </Alert>
        ) : null}
        <Group>
          {(exportTargets.data ?? [])
            .filter((t) => t.key !== "kroger") // Kroger has its own live cart action below
            .map((t) => (
              <Button
                key={t.key}
                variant={t.configured ? "light" : "default"}
                disabled={!t.configured || pending.length === 0}
                onClick={() => runExport(t.key)}
                title={t.configured ? undefined : "Not configured yet"}
              >
                {t.label}
                {t.configured ? "" : " (soon)"}
              </Button>
            ))}
        </Group>

        <Title order={5} mt="md">
          Order from Kroger
        </Title>
        <Text size="xs" c="dimmed" mb="xs">
          Sends items that have a Kroger UPC into your Kroger cart. Connect a Kroger account under Integrations
          first; items without a UPC are skipped.
        </Text>
        {cartMsg ? (
          <Alert color={cartMsg.color} mb="sm">
            {cartMsg.text}{" "}
            {cartMsg.color === "green" ? (
              <a href="https://www.kroger.com/cart" target="_blank" rel="noreferrer">
                Open Kroger cart →
              </a>
            ) : null}
          </Alert>
        ) : null}
        <Group>
          <Button color="indigo" onClick={sendToKrogerCart} loading={cartBusy} disabled={pending.length === 0}>
            Send to Kroger cart
          </Button>
          <Button
            variant="light"
            color="green"
            onClick={sendToInstacart}
            loading={cartBusy}
            disabled={pending.length === 0}
            title="Multi-retailer — pick a store on Instacart. Dormant until an Instacart key is configured."
          >
            Build Instacart list
          </Button>
        </Group>
      </Card>

      <PurchaseIntakeCard />

      {purchased.length > 0 ? (
        <Card withBorder padding="md">
          <Group justify="space-between">
            <Title order={4}>Purchased ({purchased.length})</Title>
            <Button
              size="xs"
              variant="subtle"
              onClick={() => purchased.forEach((e) => deleteItem.mutate(e.id))}
            >
              Clear purchased
            </Button>
          </Group>
          <Stack gap={0} mt="sm">{purchased.map(row)}</Stack>
        </Card>
      ) : null}

      <MatchOfferModal entry={matchEntry} onClose={() => setMatchEntry(null)} />
    </Stack>
  );
}
