import { Badge, Button, Group, Image, Modal, Paper, Stack, Text, TextInput } from "@mantine/core";
import { useEffect, useState } from "react";

import { getApiErrorMessage } from "@/api/errors";
import type { Offer, ShoppingListEntry } from "@/api/types";

import { useAddFromOffer, useOfferSearch } from "../hooks/shopping-list-hooks";

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

/** Solidify a loose shopping-list entry (e.g. a hand-typed "Nutella") into a real
 *  vendor product: search offers matching the entry, pick one, and resolve the
 *  SAME entry in place (from-offer with entry_id) — no delete/re-add dance. */
export function MatchOfferModal({
  entry,
  onClose,
}: {
  entry: ShoppingListEntry | null;
  onClose: () => void;
}) {
  const [query, setQuery] = useState("");
  const [error, setError] = useState<string | null>(null);
  const offers = useOfferSearch(query);
  const addFromOffer = useAddFromOffer();

  // Seed the search with the entry's name each time the modal opens.
  useEffect(() => {
    if (!entry) return;
    setQuery(entry.name);
    setError(null);
  }, [entry]);

  const results = offers.data?.offers ?? [];

  const pick = async (offer: Offer) => {
    if (!entry) return;
    setError(null);
    try {
      await addFromOffer.mutateAsync({
        ...offer,
        quantity: Number(entry.quantity) || 1,
        unit: entry.unit,
        entry_id: entry.id,
        item_id: entry.item_id ?? undefined,
      });
      onClose();
    } catch (e) {
      setError(getApiErrorMessage(e, "Could not match this offer"));
    }
  };

  return (
    <Modal
      opened={entry !== null}
      onClose={onClose}
      title={entry ? `Match "${entry.name}" to a store item` : "Match to store item"}
      size="lg"
    >
      <Stack gap="sm">
        <Text size="sm" c="dimmed">
          Pick a real product to replace this loose entry. The list keeps its place; the item is saved to your
          catalog with the vendor's details.
        </Text>
        <TextInput
          placeholder="Search products, e.g. Nutella"
          value={query}
          onChange={(e) => setQuery(e.currentTarget.value)}
          aria-label="Search products to match"
        />

        {error ? (
          <Text size="sm" c="red">
            {error}
          </Text>
        ) : null}
        {offers.isFetching ? (
          <Text size="sm" c="dimmed">
            Searching…
          </Text>
        ) : null}
        {offers.data && results.length === 0 && !offers.isFetching && query.trim().length >= 2 ? (
          <Text size="sm" c="dimmed">
            No matches. (Non-food items need a retailer integration like Kroger.)
          </Text>
        ) : null}

        <Stack gap="xs">
          {results.map((o, i) => (
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
                  onClick={() => pick(o)}
                >
                  Use this
                </Button>
              </Group>
            </Paper>
          ))}
        </Stack>
      </Stack>
    </Modal>
  );
}
