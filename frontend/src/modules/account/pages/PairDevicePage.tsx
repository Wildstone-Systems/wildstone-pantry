import { Alert, Anchor, Button, Card, Container, Stack, Text, TextInput, Title } from "@mantine/core";
import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import { api } from "@/api/client";
import { getApiErrorMessage } from "@/api/errors";
import { setToken } from "@/auth";
import { ROUTES } from "@/routes/paths";
import { useAuth } from "@/state/AuthProvider";

type PairResponse = { device_key: string };

/**
 * Pair this browser (e.g. a wall-mounted kitchen tablet) with a one-time code
 * from Admin → Devices. The returned device key is stored like a login token,
 * so the tablet stays signed in until an admin revokes it.
 */
export function PairDevicePage() {
  const navigate = useNavigate();
  const { refreshSession } = useAuth();
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
      setToken(res.data.device_key);
      await refreshSession();
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
