import { Alert, Badge, Button, Card, Group, Stack, Text, TextInput, Title } from "@mantine/core";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api } from "@/api/client";
import { useEntitlement } from "@/api/entitlement";
import { getApiErrorMessage } from "@/api/errors";

type ActivationStatus = {
  active: boolean;
  key_hint: string | null;
  tier: string | null;
  expires_at: string | null;
  last_refresh_at: string | null;
  refresh_failures: number | null;
};

/**
 * Connected Services (ADR 0018 §2) — one of the three allowed premium
 * surfaces. Enter an activation key, see entitlement state, deactivate to
 * free the seat. Invisible noise-wise for free users: no nags, just a card.
 */
export function ConnectCard() {
  const queryClient = useQueryClient();
  const [key, setKey] = useState("");

  const { data: entitlement } = useEntitlement();
  const connected = entitlement?.mode === "connected";

  const { data: status } = useQuery({
    queryKey: ["activationStatus"],
    enabled: connected,
    queryFn: async () => (await api.get<ActivationStatus>("/api/activation")).data,
  });

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ["activationStatus"] });
    queryClient.invalidateQueries({ queryKey: ["entitlement"] });
  };

  const activate = useMutation({
    mutationFn: async () => (await api.post("/api/activation", { key: key.trim() })).data,
    onSuccess: () => {
      setKey("");
      invalidate();
    },
  });

  const deactivate = useMutation({
    mutationFn: async () => (await api.delete("/api/activation")).data,
    onSuccess: invalidate,
  });

  if (!connected) {
    return (
      <Card withBorder padding="lg">
        <Stack gap={4}>
          <Title order={4}>Connected Services</Title>
          <Text size="sm" c="dimmed">
            This instance runs in free mode — everything local works forever, nothing phones home. To
            enable optional cloud services (brokered store connections, cloud AI), configure{" "}
            <b>LICENSE_SERVER_URL</b> and <b>LICENSE_PUBLIC_KEYS</b>, then enter an activation key here.
          </Text>
        </Stack>
      </Card>
    );
  }

  return (
    <Card withBorder padding="lg">
      <Stack gap="sm">
        <Group justify="space-between">
          <Title order={4}>Connected Services</Title>
          {status?.active ? (
            <Badge color="green" variant="light">
              Active — {status.tier} (…{status.key_hint})
            </Badge>
          ) : (
            <Badge color="gray" variant="light">
              Not activated
            </Badge>
          )}
        </Group>

        {status?.active ? (
          <Stack gap={6}>
            <Text size="sm" c="dimmed">
              Entitlement renews automatically; current token is valid until{" "}
              {status.expires_at ? new Date(status.expires_at).toLocaleDateString() : "—"}
              {status.refresh_failures ? ` (last ${status.refresh_failures} renewal attempts failed)` : ""}.
              If renewal keeps failing, premium features continue through a 14-day grace window, then
              this instance returns to free mode — local features are never affected.
            </Text>
            <Group>
              <Button
                variant="light"
                color="red"
                size="xs"
                loading={deactivate.isPending}
                onClick={() => deactivate.mutate()}
              >
                Deactivate this instance
              </Button>
            </Group>
          </Stack>
        ) : (
          <Stack gap={6}>
            <TextInput
              label="Activation key"
              placeholder="WSK-XXXX-XXXX-XXXX-XXXX"
              value={key}
              onChange={(e) => setKey(e.currentTarget.value.toUpperCase())}
              maxLength={64}
            />
            <Group>
              <Button loading={activate.isPending} disabled={key.trim().length < 8} onClick={() => activate.mutate()}>
                Activate
              </Button>
            </Group>
          </Stack>
        )}

        {activate.error != null && (
          <Alert color="red" variant="light">
            {getApiErrorMessage(activate.error, "Activation failed.")}
          </Alert>
        )}
      </Stack>
    </Card>
  );
}
