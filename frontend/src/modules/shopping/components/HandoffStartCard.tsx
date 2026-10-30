import { Alert, Anchor, Button, Card, Group, Select, Stack, Text, Title } from "@mantine/core";
import { useState } from "react";
import { useNavigate } from "react-router-dom";

import { getApiErrorMessage } from "@/api/errors";

import { useActiveHandoffSessions, useRetailers, useStartHandoff } from "../hooks/handoff-hooks";

/** Entry point for the search-handoff loop (ADR 0019 D3): pick any retailer —
 * no API or account needed — and shop their site with PantryOS as the guide. */
export function HandoffStartCard({ pendingCount }: { pendingCount: number }) {
  const retailers = useRetailers();
  const sessions = useActiveHandoffSessions();
  const start = useStartHandoff();
  const navigate = useNavigate();
  const [retailer, setRetailer] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const options = (retailers.data ?? [])
    .filter((r) => r.available.includes("search_deep_link"))
    .map((r) => ({ value: r.key, label: r.label }));

  const begin = async () => {
    if (!retailer) return;
    setError(null);
    try {
      const session = await start.mutateAsync(retailer);
      navigate(`/shopping-list/handoff/${session.id}`);
    } catch (err) {
      setError(getApiErrorMessage(err, "Could not start the shopping session"));
    }
  };

  return (
    <Card withBorder padding="md">
      <Stack gap="sm">
        <div>
          <Title order={4}>Shop at a store</Title>
          <Text size="xs" c="dimmed">
            Works with any store — no account needed. Each item becomes a search link into the store's own
            site; mark what you add there, and confirm the trip when you're done.
          </Text>
        </div>
        {(sessions.data ?? []).map((s) => (
          <Alert key={s.id} color="blue" variant="light">
            You have an unfinished trip at {s.retailer_label}.{" "}
            <Anchor onClick={() => navigate(`/shopping-list/handoff/${s.id}`)}>Resume it →</Anchor>
          </Alert>
        ))}
        {error ? <Alert color="red">{error}</Alert> : null}
        <Group align="flex-end">
          <Select
            label="Store"
            placeholder={retailers.isLoading ? "Loading stores…" : "Pick a store"}
            searchable
            data={options}
            value={retailer}
            onChange={setRetailer}
            style={{ flex: 1, maxWidth: 320 }}
          />
          <Button onClick={begin} loading={start.isPending} disabled={!retailer || pendingCount === 0}>
            Start shopping
          </Button>
        </Group>
        {pendingCount === 0 ? (
          <Text size="xs" c="dimmed">
            Add items to the list first.
          </Text>
        ) : null}
      </Stack>
    </Card>
  );
}
