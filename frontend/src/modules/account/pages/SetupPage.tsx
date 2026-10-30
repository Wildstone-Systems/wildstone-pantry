import {
  Alert,
  Button,
  Card,
  Checkbox,
  Container,
  Group,
  PasswordInput,
  SimpleGrid,
  Stack,
  Stepper,
  Text,
  TextInput,
  Title,
} from "@mantine/core";
import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Navigate, useNavigate } from "react-router-dom";

import { api } from "@/api/client";
import { getApiErrorMessage } from "@/api/errors";
import { useSetupStatus } from "@/api/hooks";
import { AccessCard } from "@/components/AccessCard";
import { ROUTES } from "@/routes/paths";

/**
 * First-run wizard (ADR 0018 §7). Every step is skippable and the whole thing
 * can be dismissed — it configures, it never gates. Backend endpoints seal
 * themselves (403) once setup completes.
 */
export function SetupPage() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { data: status } = useSetupStatus();

  const [step, setStep] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const [householdName, setHouseholdName] = useState("");
  const [lanAck, setLanAck] = useState(false);
  const [mode, setMode] = useState<"open" | "accounts" | null>(null);
  const [adminEmail, setAdminEmail] = useState("");
  const [adminPassword, setAdminPassword] = useState("");
  const [seeded, setSeeded] = useState<string | null>(null);

  // A lived-in instance never sees the wizard, even by URL.
  if (status?.completed) {
    return <Navigate to={ROUTES.dashboard} replace />;
  }

  const run = async (action: () => Promise<void>) => {
    setError(null);
    setBusy(true);
    try {
      await action();
    } catch (err) {
      setError(getApiErrorMessage(err, "Something went wrong — you can skip this step."));
      return false;
    } finally {
      setBusy(false);
    }
    return true;
  };

  const next = () => setStep((s) => Math.min(s + 1, 3));

  const submitHousehold = async () => {
    if (!householdName.trim()) {
      next();
      return;
    }
    if (await run(() => api.post("/api/setup/household", { name: householdName.trim() }).then(() => undefined))) {
      next();
    }
  };

  const submitMode = async () => {
    if (mode === "accounts") {
      if (
        await run(async () => {
          await api.post("/api/auth/bootstrap-admin", { email: adminEmail, password: adminPassword });
          await api.post("/api/setup/mode", { mode: "accounts" });
        })
      ) {
        next();
      }
      return;
    }
    if (mode === "open") {
      if (await run(() => api.post("/api/setup/mode", { mode: "open", lan_ack: lanAck }).then(() => undefined))) {
        next();
      }
      return;
    }
    next(); // no choice made — skip
  };

  const submitSeed = async (preset: "minimal" | "typical" | null) => {
    if (!preset) {
      next();
      return;
    }
    if (
      await run(async () => {
        await api.post("/api/setup/seed-defaults", { preset });
        setSeeded(preset);
      })
    ) {
      next();
    }
  };

  const finish = async () => {
    await run(async () => {
      await api.post("/api/setup/complete");
      await queryClient.invalidateQueries({ queryKey: ["setupStatus"] });
    });
    navigate(ROUTES.dashboard, { replace: true });
  };

  return (
    <Container size="sm" py="xl">
      <Stack>
        <Group justify="space-between" align="baseline">
          <Title order={2}>Welcome to PantryOS</Title>
          <Button variant="subtle" size="xs" onClick={finish} disabled={busy}>
            Skip setup
          </Button>
        </Group>

        {error && (
          <Alert color="red" variant="light" withCloseButton onClose={() => setError(null)}>
            {error}
          </Alert>
        )}

        <Stepper active={step} size="sm">
          <Stepper.Step label="Household">
            <Stack mt="md">
              <Text>What should we call your household? (Optional — used for display only.)</Text>
              <TextInput
                label="Household name"
                placeholder="e.g. The Halls"
                value={householdName}
                onChange={(e) => setHouseholdName(e.currentTarget.value)}
                maxLength={120}
              />
              <Group justify="flex-end">
                <Button onClick={submitHousehold} loading={busy}>
                  Continue
                </Button>
              </Group>
            </Stack>
          </Stepper.Step>

          <Stepper.Step label="Who uses this?">
            <Stack mt="md">
              <SimpleGrid cols={{ base: 1, sm: 2 }}>
                <Card
                  withBorder
                  padding="lg"
                  onClick={() => setMode("open")}
                  style={{ cursor: "pointer", borderColor: mode === "open" ? "var(--mantine-color-blue-5)" : undefined }}
                >
                  <Title order={5}>Open household (recommended)</Title>
                  <Text size="sm" c="dimmed" mt={4}>
                    No logins for everyday use — walk up and go, on any device in the house. The admin
                    area still requires a password.
                  </Text>
                </Card>
                <Card
                  withBorder
                  padding="lg"
                  onClick={() => setMode("accounts")}
                  style={{
                    cursor: "pointer",
                    borderColor: mode === "accounts" ? "var(--mantine-color-blue-5)" : undefined,
                  }}
                >
                  <Title order={5}>Household accounts</Title>
                  <Text size="sm" c="dimmed" mt={4}>
                    Protected surfaces require a sign-in. Create the first admin account now; add
                    family members later.
                  </Text>
                </Card>
              </SimpleGrid>

              {mode === "open" && (
                <Alert color="yellow" variant="light">
                  <Stack gap="xs">
                    <Text size="sm">
                      Open mode means <b>anyone on your WiFi has full everyday access</b> — family,
                      guests, and any device on the network. The admin area stays password-protected.
                    </Text>
                    <Checkbox
                      label="I understand — keep everyday use login-free"
                      checked={lanAck}
                      onChange={(e) => setLanAck(e.currentTarget.checked)}
                    />
                  </Stack>
                </Alert>
              )}

              {mode === "accounts" && (
                <Stack gap="xs">
                  <TextInput
                    label="Admin email"
                    value={adminEmail}
                    onChange={(e) => setAdminEmail(e.currentTarget.value)}
                    placeholder="you@example.com"
                  />
                  <PasswordInput
                    label="Admin password"
                    value={adminPassword}
                    onChange={(e) => setAdminPassword(e.currentTarget.value)}
                  />
                </Stack>
              )}

              <Group justify="space-between">
                <Button variant="subtle" onClick={next} disabled={busy}>
                  Decide later
                </Button>
                <Button
                  onClick={submitMode}
                  loading={busy}
                  disabled={
                    (mode === "open" && !lanAck) ||
                    (mode === "accounts" && (!adminEmail.trim() || !adminPassword)) ||
                    mode === null
                  }
                >
                  Continue
                </Button>
              </Group>
            </Stack>
          </Stepper.Step>

          <Stepper.Step label="Starter setup">
            <Stack mt="md">
              <Text>Start with some common storage locations{seeded ? " — done!" : "?"}</Text>
              <SimpleGrid cols={{ base: 1, sm: 2 }}>
                <Card withBorder padding="lg">
                  <Title order={5}>Minimal</Title>
                  <Text size="sm" c="dimmed" mt={4}>
                    Pantry, Fridge, Freezer.
                  </Text>
                  <Button mt="sm" variant="light" loading={busy} onClick={() => submitSeed("minimal")}>
                    Use minimal
                  </Button>
                </Card>
                <Card withBorder padding="lg">
                  <Title order={5}>Typical kitchen</Title>
                  <Text size="sm" c="dimmed" mt={4}>
                    Pantry, Fridge, Freezer, Garage + starter categories.
                  </Text>
                  <Button mt="sm" variant="light" loading={busy} onClick={() => submitSeed("typical")}>
                    Use typical
                  </Button>
                </Card>
              </SimpleGrid>
              <Group justify="flex-end">
                <Button variant="subtle" onClick={() => submitSeed(null)} disabled={busy}>
                  Skip — I'll add my own
                </Button>
              </Group>
            </Stack>
          </Stepper.Step>

          <Stepper.Step label="Other devices">
            <Stack mt="md">
              <AccessCard />
              <Group justify="flex-end">
                <Button onClick={finish} loading={busy}>
                  Finish — open PantryOS
                </Button>
              </Group>
            </Stack>
          </Stepper.Step>
        </Stepper>
      </Stack>
    </Container>
  );
}
