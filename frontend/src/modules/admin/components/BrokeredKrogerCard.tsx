import { Alert, Badge, Button, Group, Text } from "@mantine/core";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/api/client";
import { getApiErrorMessage } from "@/api/errors";

import { CollapsibleCard } from "./CollapsibleCard";

type BrokerStatus = {
  broker_available: boolean;
  connected: boolean;
  last_error: string | null;
};

/**
 * Brokered Kroger connection (ADR 0018 §3) — the entitled view inside the
 * Connected-services FeatureGate. One click: no developer account, no keys,
 * no redirect-URI registration. The broker keeps the refresh token; this
 * instance only ever holds short-lived access tokens.
 */
export function BrokeredKrogerCard() {
  const queryClient = useQueryClient();

  const { data: status } = useQuery({
    queryKey: ["krogerBrokerStatus"],
    queryFn: async () => (await api.get<BrokerStatus>("/api/integrations/kroger/broker/status")).data,
  });

  const start = useMutation({
    mutationFn: async () => (await api.post<{ authorize_url: string }>("/api/integrations/kroger/broker/start")).data,
    onSuccess: (data) => {
      window.location.href = data.authorize_url;
    },
  });

  const disconnect = useMutation({
    mutationFn: async () => (await api.post("/api/integrations/kroger/broker/disconnect")).data,
    onSettled: () => queryClient.invalidateQueries({ queryKey: ["krogerBrokerStatus"] }),
  });

  return (
    <CollapsibleCard
      id="kroger_broker"
      title="Kroger (via PantryOS Connect)"
      status={
        status?.connected ? (
          <Badge color="green" variant="light">
            Connected
          </Badge>
        ) : (
          <Badge color="gray" variant="light">
            Not connected
          </Badge>
        )
      }
    >
      <Text size="sm" c="dimmed">
        Connect your Kroger account with one click — no developer keys needed. Your sign-in stays
        with Kroger; PantryOS receives only short-lived tokens to add items to your cart.
      </Text>
      {status?.last_error && (
        <Alert color="yellow" variant="light">
          Last connection issue: {status.last_error}
        </Alert>
      )}
      {start.error != null && (
        <Alert color="red" variant="light">
          {getApiErrorMessage(start.error, "Could not start the Kroger connection.")}
        </Alert>
      )}
      <Group>
        {status?.connected ? (
          <Button variant="light" color="red" size="xs" loading={disconnect.isPending} onClick={() => disconnect.mutate()}>
            Disconnect
          </Button>
        ) : (
          <Button size="xs" loading={start.isPending} onClick={() => start.mutate()}>
            Connect Kroger account
          </Button>
        )}
      </Group>
    </CollapsibleCard>
  );
}
