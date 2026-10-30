import { Button, Card, Group, SimpleGrid, Stack, Text, Title } from "@mantine/core";
import { Link } from "react-router-dom";

import { AccessCard } from "@/components/AccessCard";
import { ROUTES } from "@/routes/paths";
import { useAuth } from "@/state/AuthProvider";

import { ConnectCard } from "../components/ConnectCard";
import { DevicesCard } from "../components/DevicesCard";
import { SystemCard } from "../components/SystemCard";

export function AdminHome() {
  const { account, logout } = useAuth();

  return (
    <Stack>
      <Group justify="space-between">
        <Title order={2}>Admin</Title>
        <Group>
          <Text size="sm" c="dimmed">
            {account?.email}
          </Text>
          <Button variant="light" onClick={logout}>
            Log out
          </Button>
        </Group>
      </Group>

      <Text c="dimmed">The protected side of PantryOS. Everyday kitchen use happens on the kiosk — no login needed.</Text>

      <SimpleGrid cols={{ base: 1, sm: 2 }}>
        <Card withBorder padding="lg" component={Link} to={ROUTES.adminHousehold}>
          <Title order={4}>Household</Title>
          <Text c="dimmed" size="sm">
            Manage the people in your home and their calorie targets.
          </Text>
        </Card>
        <Card withBorder padding="lg" component={Link} to={ROUTES.adminIntegrations}>
          <Title order={4}>Integrations</Title>
          <Text c="dimmed" size="sm">
            Connect vendor services (email receipts) to feed inventory.
          </Text>
        </Card>
        <Card withBorder padding="lg" component={Link} to={ROUTES.adminAgentKeys}>
          <Title order={4}>Agent Keys</Title>
          <Text c="dimmed" size="sm">
            Mint long-lived keys so an agent can act on PantryOS on your behalf.
          </Text>
        </Card>
        {/* Connected-services settings will live here (ADR 0018). */}
      </SimpleGrid>

      <AccessCard />
      <DevicesCard />
      <SystemCard />
      <ConnectCard />
    </Stack>
  );
}
