import { ActionIcon, Card, Collapse, Group, Stack, Title } from "@mantine/core";
import { ChevronDown, ChevronUp } from "lucide-react";
import { useState, type ReactNode } from "react";

const STORAGE_PREFIX = "pantry_integration_card_";

function getInitialOpen(id: string, defaultOpen: boolean): boolean {
  if (typeof window === "undefined") return defaultOpen;
  const stored = window.localStorage.getItem(STORAGE_PREFIX + id);
  return stored === null ? defaultOpen : stored === "1";
}

/** A card whose body collapses, with its status badge staying visible in the
 * header either way — the Integrations page has enough of these that seeing
 * them all expanded made it slow to scan. Open/closed state persists per-card
 * across visits (same idea as the sidebar's own collapse memory), keyed by
 * `id` — use a stable, unique id per card (e.g. the connector key). */
export function CollapsibleCard({
  id,
  title,
  status,
  defaultOpen = false,
  children,
}: {
  id: string;
  title: ReactNode;
  status?: ReactNode;
  defaultOpen?: boolean;
  children: ReactNode;
}) {
  const [opened, setOpened] = useState(() => getInitialOpen(id, defaultOpen));

  const toggle = () => {
    setOpened((prev) => {
      const next = !prev;
      if (typeof window !== "undefined") {
        window.localStorage.setItem(STORAGE_PREFIX + id, next ? "1" : "0");
      }
      return next;
    });
  };

  return (
    <Card withBorder padding="lg">
      <Group
        justify="space-between"
        wrap="nowrap"
        onClick={toggle}
        style={{ cursor: "pointer" }}
        role="button"
        tabIndex={0}
        aria-expanded={opened}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            toggle();
          }
        }}
      >
        <Title order={4}>{title}</Title>
        <Group gap="xs" wrap="nowrap">
          {status}
          <ActionIcon
            variant="subtle"
            aria-label={opened ? "Collapse" : "Expand"}
            onClick={(e) => {
              e.stopPropagation();
              toggle();
            }}
          >
            {opened ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
          </ActionIcon>
        </Group>
      </Group>
      <Collapse in={opened}>
        <Stack gap="sm" mt="md">
          {children}
        </Stack>
      </Collapse>
    </Card>
  );
}
