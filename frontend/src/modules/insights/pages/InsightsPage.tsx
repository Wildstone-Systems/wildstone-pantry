import { Button, Card, Group, Stack, Text, Title } from "@mantine/core";
import { Link } from "react-router-dom";

import { useConsumptionTrends, useExpiringItems, useRestockSuggestions } from "@/api/hooks";
import { ROUTES } from "@/routes/paths";
import { useAppState } from "@/state/AppStateProvider";

export function InsightsPage() {
  const { selectedLocationId } = useAppState();

  const trendsQuery = useConsumptionTrends(selectedLocationId, 30);
  const restockQuery = useRestockSuggestions(selectedLocationId);
  const expiringQuery = useExpiringItems(selectedLocationId, 7);

  const trends = trendsQuery.data ?? [];
  const restock = restockQuery.data ?? [];
  const expiring = expiringQuery.data ?? [];

  return (
    <Stack>
      <Group justify="space-between" align="baseline" wrap="wrap">
        <Title order={2}>Insights</Title>
        <Button component={Link} to={ROUTES.dashboard} variant="default">
          Go to Dashboard
        </Button>
      </Group>

      <Card withBorder>
        <Text size="sm" c="dimmed">
          The primary Insights view now lives on the <strong>Dashboard</strong> so you can act on trends and expiring items without changing pages.
        </Text>
      </Card>

      <Group align="flex-start" grow>
        <Card withBorder>
          <Title order={3} mb="xs">
            Consumption Trends
          </Title>
          <Stack gap="xs">
            {trends.map((trend) => (
              <div key={trend.item_id} style={{ padding: "6px 0", borderBottom: "1px solid var(--mantine-color-gray-3)" }}>
                <Text fw={700}>{trend.item_name}</Text>
                <Text size="sm">
                  Avg/day: {trend.avg_daily_usage.toFixed(3)} | Days to depletion:{" "}
                  {trend.days_to_depletion == null ? "-" : trend.days_to_depletion.toFixed(1)}
                </Text>
              </div>
            ))}
            {trends.length === 0 ? (
              <Text size="sm" c="dimmed">
                {selectedLocationId ? "No trend data available." : "Select a location first (top-right)."}
              </Text>
            ) : null}
          </Stack>
        </Card>

        <Card withBorder>
          <Title order={3} mb="xs">
            Restock Suggestions
          </Title>
          <Stack gap="xs">
            {restock.map((suggestion) => (
              <div
                key={suggestion.item_id}
                style={{ padding: "6px 0", borderBottom: "1px solid var(--mantine-color-gray-3)" }}
              >
                <Text fw={700}>
                  {suggestion.item_name} (priority {suggestion.priority})
                </Text>
                <Text size="sm">{suggestion.recommendation}</Text>
              </div>
            ))}
            {restock.length === 0 ? (
              <Text size="sm" c="dimmed">
                {selectedLocationId ? "No restock suggestions right now." : "Select a location first (top-right)."}
              </Text>
            ) : null}
          </Stack>
        </Card>

        <Card withBorder>
          <Title order={3} mb="xs">
            Expiring Soon
          </Title>
          <Stack gap="xs">
            {expiring.map((item) => (
              <div
                key={`${item.item_id}-${item.expiration_date}`}
                style={{ padding: "6px 0", borderBottom: "1px solid var(--mantine-color-gray-3)" }}
              >
                <Text fw={700}>{item.item_name}</Text>
                <Text size="sm">
                  Exp: {item.expiration_date} ({item.days_until_expiration} days)
                </Text>
              </div>
            ))}
            {expiring.length === 0 ? (
              <Text size="sm" c="dimmed">
                {selectedLocationId ? "No items expiring soon." : "Select a location first (top-right)."}
              </Text>
            ) : null}
          </Stack>
        </Card>
      </Group>
    </Stack>
  );
}
