import { Button, Card, Group, Stack, Text, Title } from "@mantine/core";
import { Link } from "react-router-dom";

import { useScopedExpiring, useScopedRestock, useScopedTrends } from "@/modules/inventory";
import { ROUTES } from "@/routes/paths";

/** Household-wide insights: every location pooled, no location selection needed. */
export function InsightsPage() {
  const trends = useScopedTrends(null, 30).data;
  const restock = useScopedRestock(null).data;
  const expiring = useScopedExpiring(null, 7).data;

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
                No trend data available.
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
                No restock suggestions right now.
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
                key={`${item.location_id}-${item.item_id}-${item.expiration_date}`}
                style={{ padding: "6px 0", borderBottom: "1px solid var(--mantine-color-gray-3)" }}
              >
                <Text fw={700}>{item.item_name}</Text>
                <Text size="sm">
                  Exp: {item.expiration_date} ({item.days_until_expiration} days)
                  {item.location_name ? ` · ${item.location_name}` : ""}
                </Text>
              </div>
            ))}
            {expiring.length === 0 ? (
              <Text size="sm" c="dimmed">
                No items expiring soon.
              </Text>
            ) : null}
          </Stack>
        </Card>
      </Group>
    </Stack>
  );
}
