import { zodResolver } from "@hookform/resolvers/zod";
import { Alert, Anchor, Button, Card, PasswordInput, Stack, Text, TextInput, Title } from "@mantine/core";
import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { useForm } from "react-hook-form";
import { Link } from "react-router-dom";

import { api } from "@/api/client";
import { getApiErrorMessage } from "@/api/errors";
import { setToken } from "@/auth";
import { ROUTES } from "@/routes/paths";
import { useAuth } from "@/state/AuthProvider";

import { loginSchema, type LoginValues } from "../forms/login-schema";

type OidcStatus = {
  enabled: boolean;
  button_label: string;
  local_login_hidden: boolean;
};

const OIDC_STATUS_FALLBACK: OidcStatus = {
  enabled: false,
  button_label: "Sign in with SSO",
  local_login_hidden: false,
};

export function LoginPage() {
  const { login, bootstrap, refreshSession } = useAuth();
  const [mode, setMode] = useState<"login" | "setup">("login");
  const [error, setError] = useState<string | null>(null);
  // The password form is always mounted (break-glass, ADR 0020 Decision 4) —
  // this only controls whether it starts collapsed behind a reveal link.
  const [showLocalForm, setShowLocalForm] = useState(true);

  // First run (no admin configured yet) → default to the setup form.
  useEffect(() => {
    let cancelled = false;
    api
      .get<{ needs_setup: boolean }>("/api/auth/setup-status")
      .then((res) => {
        if (!cancelled && res.data.needs_setup) setMode("setup");
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, []);

  const { data: oidcStatus } = useQuery({
    queryKey: ["oidc-status"],
    queryFn: async () => (await api.get<OidcStatus>("/api/auth/oidc/status")).data,
    // A probe, not a hard dependency — any failure (older backend, network
    // hiccup) just means SSO is treated as disabled.
    retry: false,
    // Only relevant in "login" mode; harmless to fetch during setup too.
    throwOnError: false,
  });

  const status = oidcStatus ?? OIDC_STATUS_FALLBACK;

  useEffect(() => {
    if (mode !== "login") return;
    setShowLocalForm(!status.local_login_hidden);
  }, [mode, status.local_login_hidden]);

  // Parse the SSO callback fragment (`#sso_token=` / `#sso_error=`) once, on
  // mount, in login mode only — the callback always lands on `/login`.
  useEffect(() => {
    if (mode !== "login") return;
    const hash = window.location.hash;
    if (!hash) return;

    const tokenMatch = hash.match(/#sso_token=([^&]+)/);
    const errorMatch = hash.match(/#sso_error=([^&]+)/);

    if (tokenMatch) {
      const token = decodeURIComponent(tokenMatch[1]);
      setToken(token);
      window.history.replaceState(null, "", window.location.pathname + window.location.search);
      void refreshSession();
    } else if (errorMatch) {
      setError(decodeURIComponent(errorMatch[1]));
      window.history.replaceState(null, "", window.location.pathname + window.location.search);
    }
    // Intentionally run once on mount — the hash is a one-shot callback signal.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mode]);

  const {
    register,
    handleSubmit,
    formState: { errors, isSubmitting },
  } = useForm<LoginValues>({ resolver: zodResolver(loginSchema), mode: "onChange" });

  const onSubmit = handleSubmit(async (values) => {
    setError(null);
    try {
      if (mode === "setup") {
        await bootstrap(values.email, values.password);
      } else {
        await login(values.email, values.password);
      }
    } catch (err: unknown) {
      const httpStatus = (err as { response?: { status?: number } })?.response?.status;
      if (mode === "setup" && httpStatus === 409) {
        setError("An admin already exists. Switch to Log in.");
        setMode("login");
      } else {
        setError(getApiErrorMessage(err, mode === "setup" ? "Could not create admin" : "Invalid email or password"));
      }
    }
  });

  const showSsoButton = mode === "login" && status.enabled;
  const collapseLocalForm = mode === "login" && status.local_login_hidden && !showLocalForm;

  return (
    <Stack align="center" mt="xl">
      <Card withBorder shadow="sm" padding="lg" maw={420} w="100%">
        <Stack>
          <Title order={3}>{mode === "setup" ? "Set up the admin account" : "Admin login"}</Title>
          <Text size="sm" c="dimmed">
            The admin area is protected. Everyday kitchen use doesn't need a login — this is only for
            managing people, integrations, and settings.
          </Text>

          {error ? <Alert color="red">{error}</Alert> : null}

          {showSsoButton ? (
            <Button
              size="md"
              onClick={() => {
                window.location.assign("/api/auth/oidc/login");
              }}
            >
              {status.button_label}
            </Button>
          ) : null}

          {collapseLocalForm ? (
            <Anchor component="button" type="button" size="sm" onClick={() => setShowLocalForm(true)}>
              Use local admin login
            </Anchor>
          ) : (
            <form onSubmit={onSubmit}>
              <Stack>
                <TextInput
                  label="Email"
                  type="email"
                  autoComplete="username"
                  error={errors.email?.message}
                  {...register("email")}
                />
                <PasswordInput
                  label="Password"
                  autoComplete={mode === "setup" ? "new-password" : "current-password"}
                  error={errors.password?.message}
                  {...register("password")}
                />

                <Button type="submit" loading={isSubmitting}>
                  {mode === "setup" ? "Create admin & continue" : "Log in"}
                </Button>

                <Anchor
                  component="button"
                  type="button"
                  size="sm"
                  onClick={() => {
                    setError(null);
                    setMode((m) => (m === "setup" ? "login" : "setup"));
                  }}
                >
                  {mode === "setup" ? "Have an account? Log in" : "First time? Set up the admin account"}
                </Anchor>
              </Stack>
            </form>
          )}

          <Anchor component={Link} to={ROUTES.dashboard} size="sm" c="dimmed">
            ← Back to the kitchen
          </Anchor>
        </Stack>
      </Card>
    </Stack>
  );
}
