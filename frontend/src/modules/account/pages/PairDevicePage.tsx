import { Alert, Anchor, Button, Card, Container, Stack, Text, TextInput, Title } from "@mantine/core";
import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import { api } from "@/api/client";
import { getApiErrorMessage } from "@/api/errors";
import { setDeviceKey } from "@/auth";
import { ROUTES } from "@/routes/paths";

type PairResponse = { device_key: string };

/**
 * Pair this browser (e.g. a wall-mounted kitchen tablet) with a one-time code
 * from Admin → Devices. The device key is kept separately from any admin login,
 * so the tablet stays paired until an admin revokes it.
 */
export function PairDevicePage() {
  const navigate = useNavigate();
  const [code, setCode] = useState("");
  const [deviceName, setDeviceName] = useState("Kitchen tablet");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setError(null);
    setBusy(true);
    try {
      const res = await api.post<PairResponse>("/api/devices/pair", {
        code: code.trim(),
        device_name: deviceName.trim() || "Browser",
      });
      // Stored as the device's credential, not as an admin session: the
      // admin area still asks a person to sign in.
      setDeviceKey(res.data.device_key);
      navigate(ROUTES.dashboard, { replace: true });
    } catch (err) {
      setError(getApiErrorMessage(err, "That code did not work. Ask an admin for a new one."));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Container size="xs" py="xl">
      <Card withBorder padding="lg">
        <form onSubmit={submit}>
          <Stack>
            <Title order={3}>Pair this device</Title>
            <Text size="sm" c="dimmed">
              PantryOS requires a sign-in. On an admin device, open Admin → Devices → “Pair a new
              device” and enter the one-time code here.
            </Text>
            {error && (
              <Alert color="red" variant="light">
                {error}
              </Alert>
            )}
            <TextInput
              label="Pairing code"
              value={code}
              onChange={(e) => setCode(e.currentTarget.value)}
              autoComplete="one-time-code"
              required
            />
            <TextInput
              label="Device name"
              value={deviceName}
              onChange={(e) => setDeviceName(e.currentTarget.value)}
            />
            <Button type="submit" loading={busy} disabled={!code.trim()}>
              Pair device
            </Button>
            <Anchor component={Link} to={ROUTES.admin} size="sm">
              Sign in with an account instead
            </Anchor>
          </Stack>
        </form>
      </Card>
    </Container>
  );
}
