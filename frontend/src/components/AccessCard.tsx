import { Alert, Card, Code, Group, Stack, Text, Title } from "@mantine/core";
import { QRCodeSVG } from "qrcode.react";

/**
 * "Reach PantryOS from other devices" card (ADR 0018 §5).
 *
 * The reliability floor for LAN discovery: show the address this instance is
 * being reached at, as text and as a QR any phone camera can scan. mDNS and
 * the desktop launcher build on top of this; the card never depends on them.
 */
export function AccessCard() {
  const origin = window.location.origin;
  const hostname = window.location.hostname;
  const isLoopback = hostname === "localhost" || hostname === "127.0.0.1" || hostname === "[::1]";

  return (
    <Card withBorder padding="lg">
      <Stack gap="sm">
        <Title order={4}>Access from other devices</Title>
        <Group align="flex-start" wrap="nowrap" gap="lg">
          <QRCodeSVG value={origin} size={128} marginSize={2} aria-label={`QR code for ${origin}`} />
          <Stack gap={6} style={{ minWidth: 0 }}>
            <Text size="sm">
              Any phone, tablet, or computer on your home network can use PantryOS at:
            </Text>
            <Code block>{origin}</Code>
            <Text size="sm" c="dimmed">
              On a phone: scan the QR code, then use your browser's <b>Add to Home Screen</b> for an
              app-like icon.
            </Text>
          </Stack>
        </Group>
        {isLoopback && (
          <Alert color="yellow" variant="light">
            You're viewing PantryOS via <b>{hostname}</b>, which only works on this machine. To share
            with other devices, open PantryOS at this machine's network address (its LAN IP or
            hostname) — the QR code will update to match.
          </Alert>
        )}
      </Stack>
    </Card>
  );
}
