import { Box, Button, Card, Group, SegmentedControl, Stack, Text, Title, UnstyledButton } from "@mantine/core";
import { ChevronRight, LogOut, MessageSquare,
  Ruler, Moon, Settings2, Sun, Tags } from "lucide-react";
import { Link } from "react-router-dom";

import { ROUTES } from "@/routes/paths";
import { useAuth } from "@/state/AuthProvider";
import { useThemeMode } from "@/theme/ThemeModeProvider";

type LinkRow = { label: string; description: string; to: string; icon: typeof Tags };

const MANAGE_LINKS: LinkRow[] = [
  { label: "Catalog", description: "Items, brands and barcodes", to: ROUTES.catalog, icon: Tags },
  { label: "Admin", description: "Household, integrations and access", to: ROUTES.admin, icon: Settings2 },
  {
    label: "Data to review",
    description: "Unit conversions the system couldn't work out on its own",
    to: ROUTES.reviewConversions,
    icon: Ruler,
  },
  {
    label: "Nutrition data quality",
    description: "Stored values that don't add up",
    to: ROUTES.reviewDataQuality,
    icon: Ruler,
  },
  { label: "Feedback", description: "Send feedback and view the inbox", to: ROUTES.feedback, icon: MessageSquare },
];

export function SettingsPage() {
  const { mode, setMode } = useThemeMode();
  const { isAdmin, logout } = useAuth();

  return (
    <Box style={{ maxWidth: 760 }}>
      <Stack gap={4} mb="lg">
        <Text size="xs" c="dimmed">
          Settings
        </Text>
        <Title order={2}>Settings</Title>
      </Stack>

      <Stack gap="md">
        <Card padding="lg">
          <Title order={3} mb="xs">
            Appearance
          </Title>
          <Group justify="space-between" wrap="nowrap">
            <Text size="sm" c="dimmed">
              Switch between the warm light theme and a low-light dark theme.
            </Text>
            <SegmentedControl
              value={mode}
              onChange={(value) => setMode(value === "dark" ? "dark" : "light")}
              data={[
                {
                  value: "light",
                  label: (
                    <Group gap={6} wrap="nowrap">
                      <Sun size={15} /> Light
                    </Group>
                  ),
                },
                {
                  value: "dark",
                  label: (
                    <Group gap={6} wrap="nowrap">
                      <Moon size={15} /> Dark
                    </Group>
                  ),
                },
              ]}
            />
          </Group>
        </Card>

        <Card padding="lg">
          <Title order={3} mb="sm">
            Manage
          </Title>
          <Stack gap={0}>
            {MANAGE_LINKS.map((row) => {
              const Icon = row.icon;
              return (
                <UnstyledButton
                  key={row.to}
                  component={Link}
                  to={row.to}
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: 14,
                    padding: "12px 6px",
                    borderBottom: "1px solid var(--pantry-divider)",
                  }}
                >
                  <Box
                    style={{
                      width: 40,
                      height: 40,
                      borderRadius: 12,
                      background: "var(--pantry-accent-100)",
                      display: "grid",
                      placeItems: "center",
                      flex: "none",
                    }}
                  >
                    <Icon size={20} color="var(--pantry-accent)" strokeWidth={2.4} />
                  </Box>
                  <Box style={{ flex: 1 }}>
                    <Text fw={600}>{row.label}</Text>
                    <Text size="xs" c="dimmed">
                      {row.description}
                    </Text>
                  </Box>
                  <ChevronRight size={18} color="var(--pantry-text-muted)" />
                </UnstyledButton>
              );
            })}
          </Stack>
        </Card>

        {/* Everyday kitchen use needs no login (ADR 0004); this only appears once
            an admin has signed in via the Admin area. */}
        {isAdmin ? (
          <Card padding="lg">
            <Title order={3} mb="xs">
              Admin session
            </Title>
            <Group justify="space-between" wrap="nowrap">
              <Text size="sm" c="dimmed">
                You're signed in as an admin on this device.
              </Text>
              <Button variant="default" leftSection={<LogOut size={16} />} onClick={logout}>
                Sign out
              </Button>
            </Group>
          </Card>
        ) : null}
      </Stack>
    </Box>
  );
}
