import { Alert, Badge, Button, Card, Group, Select, Stack, Table, Text, Title } from "@mantine/core";
import { useState } from "react";

import { getApiErrorMessage } from "@/api/errors";
import { useLocations } from "@/modules/catalog";

import {
  previewConnector,
  useImportConnector,
  usePurchaseConnectors,
  type NormalizedOrder,
  type PurchaseConnector,
} from "../hooks/shopping-list-hooks";

function ConnectorRow({ connector }: { connector: PurchaseConnector }) {
  const locations = useLocations();
  const importConnector = useImportConnector();
  const [locationId, setLocationId] = useState<string | null>(null);
  const [orders, setOrders] = useState<NormalizedOrder[] | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [previewing, setPreviewing] = useState(false);

  const lineCount = (orders ?? []).reduce((n, o) => n + o.lines.length, 0);

  const preview = async () => {
    setMsg(null);
    setPreviewing(true);
    try {
      setOrders(await previewConnector(connector.key));
    } catch (err) {
      setMsg(getApiErrorMessage(err, "Preview failed"));
    } finally {
      setPreviewing(false);
    }
  };

  const runImport = () => {
    if (!locationId) return;
    setMsg(null);
    importConnector.mutate(
      { key: connector.key, locationId },
      {
        onSuccess: (r) => {
          setMsg(
            `Imported ${r.imported_lines} item(s) from ${r.imported_orders} order(s)` +
              (r.skipped_orders ? `; skipped ${r.skipped_orders} already-imported` : "") +
              ".",
          );
          setOrders(null);
        },
        onError: (err) => setMsg(getApiErrorMessage(err, "Import failed")),
      },
    );
  };

  return (
    <Card withBorder padding="sm" radius="sm">
      <Group justify="space-between" wrap="nowrap">
        <Text fw={500}>{connector.label}</Text>
        <Badge variant={connector.configured ? "filled" : "light"} color={connector.configured ? "green" : "gray"}>
          {connector.configured ? "connected" : "not configured"}
        </Badge>
      </Group>

      {!connector.configured ? (
        <Text c="dimmed" size="sm" mt={4}>
          {connector.configurable
            ? "Set it up under Admin → Integrations to turn it on."
            : "Set the connector's credentials in the environment to turn it on."}
        </Text>
      ) : (
        <Stack gap="xs" mt="sm">
          <Group align="flex-end">
            <Select
              label="Import to"
              placeholder="Pick a location"
              data={(locations.data ?? []).map((l) => ({ value: l.id, label: l.name }))}
              value={locationId}
              onChange={setLocationId}
              style={{ flex: 1 }}
            />
            <Button variant="light" onClick={preview} loading={previewing}>
              Preview
            </Button>
            <Button onClick={runImport} disabled={!locationId} loading={importConnector.isPending}>
              Import
            </Button>
          </Group>

          {orders != null ? (
            orders.length === 0 ? (
              <Text c="dimmed" size="sm">
                No new orders found.
              </Text>
            ) : (
              <>
                <Text size="sm" c="dimmed">
                  {orders.length} order(s), {lineCount} line(s):
                </Text>
                <Table.ScrollContainer minWidth={440}>
                <Table>
                  <Table.Thead>
                    <Table.Tr>
                      <Table.Th>Item</Table.Th>
                      <Table.Th>Qty</Table.Th>
                      <Table.Th>Vendor</Table.Th>
                    </Table.Tr>
                  </Table.Thead>
                  <Table.Tbody>
                    {orders.flatMap((o) =>
                      o.lines.map((l, i) => (
                        <Table.Tr key={`${o.external_id ?? o.vendor}-${i}`}>
                          <Table.Td>{l.name}</Table.Td>
                          <Table.Td>
                            {l.quantity} {l.unit}
                          </Table.Td>
                          <Table.Td>{o.vendor}</Table.Td>
                        </Table.Tr>
                      )),
                    )}
                  </Table.Tbody>
                </Table>
                </Table.ScrollContainer>
              </>
            )
          ) : null}
        </Stack>
      )}

      {msg ? (
        <Alert color="blue" mt="sm">
          {msg}
        </Alert>
      ) : null}
    </Card>
  );
}

export function PurchaseIntakeCard() {
  const connectors = usePurchaseConnectors();
  const list = connectors.data ?? [];

  return (
    <Card withBorder padding="md">
      <Title order={4} mb={4}>
        Purchase intake
      </Title>
      <Text c="dimmed" size="sm" mb="sm">
        Connectors pull what you bought back into inventory. Preview an order, then import it to a location.
      </Text>
      {list.length === 0 ? (
        <Text c="dimmed" size="sm">
          No intake connectors available.
        </Text>
      ) : (
        <Stack gap="sm">
          {list.map((c) => (
            <ConnectorRow key={c.key} connector={c} />
          ))}
        </Stack>
      )}
    </Card>
  );
}
