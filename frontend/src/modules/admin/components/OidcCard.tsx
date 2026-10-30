import {
  ActionIcon,
  Alert,
  Badge,
  Button,
  Code,
  CopyButton,
  Group,
  PasswordInput,
  Stack,
  Switch,
  Text,
  TextInput,
  Tooltip,
} from "@mantine/core";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { api } from "@/api/client";
import { getApiErrorMessage } from "@/api/errors";

import { CollapsibleCard } from "./CollapsibleCard";
import { useConnectorConfig, useSaveConnectorConfig } from "../hooks/integrations-hooks";

function CopyIcon() {
  return (
    <svg
      width="16"
      height="16"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <rect x="9" y="9" width="13" height="13" rx="2" />
      <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" />
    </svg>
  );
}

const FIELDS: { name: string; label: string; secret?: boolean; placeholder?: string }[] = [
  { name: "issuer", label: "Issuer", placeholder: "https://auth.example.lan/application/o/pantryos/" },
  { name: "client_id", label: "Client ID" },
  { name: "client_secret", label: "Client secret", secret: true },
  { name: "scopes", label: "Scopes", placeholder: "openid profile email groups" },
  { name: "groups_claim", label: "Groups claim", placeholder: "groups" },
  { name: "users_group", label: "Users group", placeholder: "pantryos_users" },
  { name: "admin_group", label: "Admin group", placeholder: "pantryos_admin" },
  { name: "button_label", label: "Button label", placeholder: "Sign in with SSO" },
];

/**
 * Native OIDC single sign-on (ADR 0020). LLDAP (or any directory) fronted by
 * an OIDC provider — Authelia, authentik, Keycloak, Pocket ID — covers
 * homelab identity without a second PantryOS password. Config lives in the
 * same connector-config store as the other integration cards; the "hide
 * local login" toggle is a separate app-settings switch (UI-only — the
 * password endpoint is never actually disabled, break-glass by design).
 */
export function OidcCard() {
  const config = useConnectorConfig("oidc");
  const save = useSaveConnectorConfig("oidc");
  const queryClient = useQueryClient();

  const status = useQuery({
    queryKey: ["oidc-status"],
    queryFn: async () =>
      (await api.get<{ enabled: boolean; button_label: string; local_login_hidden: boolean }>("/api/auth/oidc/status"))
        .data,
    retry: false,
  });

  const [values, setValues] = useState<Record<string, string>>({});
  const [msg, setMsg] = useState<string | null>(null);
  const [hideLocalLogin, setHideLocalLogin] = useState(false);

  const view = config.data;

  useEffect(() => {
    if (status.data) setHideLocalLogin(status.data.local_login_hidden);
  }, [status.data]);

  useEffect(() => {
    if (!view) return;
    const seed: Record<string, string> = {};
    for (const f of FIELDS) if (!f.secret) seed[f.name] = view.values?.[f.name] ?? "";
    setValues(seed);
  }, [view]); // eslint-disable-line react-hooks/exhaustive-deps

  const configured = Boolean(values.issuer?.trim() && values.client_id?.trim() && view?.secret_set?.client_secret);

  const toggleHidden = useMutation({
    mutationFn: async (hidden: boolean) => (await api.put("/api/auth/local-login-visibility", { hidden })).data as { hidden: boolean },
    onSuccess: (data) => {
      setHideLocalLogin(data.hidden);
      queryClient.invalidateQueries({ queryKey: ["oidc-status"] });
    },
    onError: (err) => setMsg(getApiErrorMessage(err, "Could not update the local-login setting")),
  });

  const onSave = () => {
    setMsg(null);
    const updates: Record<string, string> = {};
    for (const f of FIELDS) {
      const v = values[f.name] ?? "";
      if (f.secret && !v) continue; // blank secret = keep existing
      updates[f.name] = v;
    }
    save.mutate(updates, {
      onSuccess: () => {
        setValues((cur) => {
          const next = { ...cur };
          for (const f of FIELDS) if (f.secret) delete next[f.name];
          return next;
        });
        setMsg("Saved.");
      },
      onError: (err) => setMsg(getApiErrorMessage(err, "Save failed")),
    });
  };

  const redirectUri = `${window.location.origin}/api/auth/oidc/callback`;

  return (
    <CollapsibleCard
      id="oidc"
      title="Single sign-on (OIDC)"
      status={
        <Badge variant="light" color={configured ? "green" : "gray"}>
          {configured ? "configured" : "not configured"}
        </Badge>
      }
    >
      <Text size="sm" c="dimmed">
        Let your household log in with the identity provider you already run — LLDAP as the directory,
        fronted by Authelia, authentik, Keycloak, or Pocket ID. Local admin login always stays available
        as a break-glass path.
      </Text>

      {view && !view.secrets_available ? (
        <Alert color="orange">Set CONFIG_SECRET_KEY on the server to store the client secret in-app.</Alert>
      ) : null}

      <Stack gap={4}>
        <Text size="sm" fw={500}>
          Redirect URI
        </Text>
        <Group gap="xs" wrap="nowrap">
          <Code style={{ flex: 1, wordBreak: "break-all" }}>{redirectUri}</Code>
          <CopyButton value={redirectUri}>
            {({ copied, copy }) => (
              <Tooltip label={copied ? "Copied" : "Copy"} withArrow>
                <ActionIcon variant="subtle" color={copied ? "teal" : "gray"} onClick={copy} aria-label="Copy redirect URI">
                  <CopyIcon />
                </ActionIcon>
              </Tooltip>
            )}
          </CopyButton>
        </Group>
        <Text size="xs" c="dimmed">
          Register this exact URL in your identity provider.
        </Text>
      </Stack>

      {FIELDS.map((f) =>
        f.secret ? (
          <PasswordInput
            key={f.name}
            label={f.label}
            placeholder={view?.secret_set?.[f.name] ? "unchanged" : f.placeholder}
            value={values[f.name] ?? ""}
            onChange={(e) => {
              const v = e.currentTarget.value;
              setValues((c) => ({ ...c, [f.name]: v }));
            }}
          />
        ) : (
          <TextInput
            key={f.name}
            label={f.label}
            placeholder={f.placeholder}
            value={values[f.name] ?? ""}
            onChange={(e) => {
              const v = e.currentTarget.value;
              setValues((c) => ({ ...c, [f.name]: v }));
            }}
          />
        ),
      )}

      {msg ? <Text size="sm">{msg}</Text> : null}

      <Group>
        <Button onClick={onSave} loading={save.isPending} disabled={!!view && !view.secrets_available}>
          Save
        </Button>
      </Group>

      <Tooltip
        label={configured ? "" : "Configure and save OIDC above first — the toggle stays locked until there's another way in."}
        disabled={configured}
      >
        <Switch
          label="Hide local password login"
          description="Cosmetic only — the local login endpoint always keeps working as a break-glass path."
          checked={hideLocalLogin}
          disabled={!configured || toggleHidden.isPending}
          onChange={(e) => toggleHidden.mutate(e.currentTarget.checked)}
        />
      </Tooltip>
    </CollapsibleCard>
  );
}
