import { Alert, Anchor, Badge, Button, Card, Code, Group, Stack, Table, Text, Title } from "@mantine/core";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { QRCodeSVG } from "qrcode.react";
import { useState } from "react";

import { api, apiBaseUrl } from "@/api/client";
import { getApiErrorMessage } from "@/api/errors";

type Device = {
  id: string;
  name: string;
  created_at: string;
  last_used_at: string | null;
  revoked: boolean;
};

type PairingCode = { code: string; expires_in: number };

/**
 * Device pairing + management (ADR 0018 §8). The QR encodes
 * pantryos://pair?base=<url>&code=<one-time-code> for the future mobile app;
 * the manual code works today for anything that can call /api/devices/pair.
 */
export function DevicesCard() {
  const queryClient = useQueryClient();
  const [pairing, setPairing] = useState<PairingCode | null>(null);

  const { data } = useQuery({
    queryKey: ["devices"],
    queryFn: async () => (await api.get<{ devices: Device[] }>("/api/devices")).data.devices,
  });

  const mint = useMutation({
    mutationFn: async () => (await api.post<PairingCode>("/api/devices/pairing-codes")).data,
    onSuccess: setPairing,
  });

  const revoke = useMutation({
    mutationFn: async (id: string) => (await api.delete(`/api/devices/${id}`)).data,
    onSettled: () => queryClient.invalidateQueries({ queryKey: ["devices"] }),
  });

  const active = (data ?? []).filter((d) => !d.revoked);
  const pairUrl = pairing
    ? `pantryos://pair?base=${encodeURIComponent(apiBaseUrl())}&code=${encodeURIComponent(pairing.code)}`
    : "";

  return (
    <Card withBorder padding="lg">
      <Stack gap="sm">
        <Group justify="space-between">
          <Title order={4}>Devices</Title>
          <Button size="xs" variant="light" loading={mint.isPending} onClick={() => mint.mutate()}>
            Pair a new device
          </Button>
        </Group>
        <Text size="sm" c="dimmed">
          Paired phones and tablets get their own long-lived key — no daily logins. Revoke a lost
          device here and its access ends immediately.
        </Text>

        {mint.error != null && (
          <Alert color="red" variant="light">
            {getApiErrorMessage(mint.error, "Could not create a pairing code.")}
          </Alert>
        )}

        {pairing && (
          <Alert color="blue" variant="light" withCloseButton onClose={() => setPairing(null)}>
            <Group align="flex-start" wrap="nowrap" gap="lg">
              <QRCodeSVG value={pairUrl} size={112} marginSize={2} />
              <Stack gap={4}>
                <Text size="sm">
                  Scan with the PantryOS app, or enter this one-time code (valid ~
                  {Math.round(pairing.expires_in / 60)} min):
                </Text>
                <Code block>{pairing.code}</Code>
              </Stack>
            </Group>
          </Alert>
        )}

        {active.length > 0 && (
          <Table>
            <Table.Tbody>
              {active.map((d) => (
                <Table.Tr key={d.id}>
                  <Table.Td>{d.name}</Table.Td>
                  <Table.Td>
                    <Badge variant="light" color="green">
                      active
                    </Badge>
                  </Table.Td>
                  <Table.Td>
                    {d.last_used_at ? `last used ${new Date(d.last_used_at).toLocaleDateString()}` : "never used"}
                  </Table.Td>
                  <Table.Td>
                    <Anchor component="button" type="button" c="red" onClick={() => revoke.mutate(d.id)}>
                      Revoke
                    </Anchor>
                  </Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        )}
        {active.length === 0 && (
          <Text size="sm" c="dimmed">
            No paired devices yet.
          </Text>
        )}
      </Stack>
    </Card>
  );
}
