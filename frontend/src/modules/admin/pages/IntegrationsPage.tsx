import { Alert, Badge, Button, Card, Group, PasswordInput, Stack, Text, TextInput, Title } from "@mantine/core";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState, type ReactNode } from "react";

import { api } from "@/api/client";
import { FEATURE } from "@/api/entitlement";
import { getApiErrorMessage } from "@/api/errors";
import { FeatureGate } from "@/components/FeatureGate";

import { BrokeredKrogerCard } from "../components/BrokeredKrogerCard";
import { CloudAiCard } from "../components/CloudAiCard";
import { ClassifierCard } from "../components/ClassifierCard";
import { ClassifierEvaluationPanel } from "../components/ClassifierEvaluationPanel";
import { CollapsibleCard } from "../components/CollapsibleCard";
import { OidcCard } from "../components/OidcCard";
import { WildstoneCard } from "../components/WildstoneCard";

import {
  useClearConnectorConfig,
  useConnectorConfig,
  useSaveConnectorConfig,
  useTestConnectorConfig,
  useWebhookDeliveries,
} from "../hooks/integrations-hooks";

function ImapCard() {
  const config = useConnectorConfig("imap");
  const save = useSaveConnectorConfig("imap");
  const clear = useClearConnectorConfig("imap");
  const test = useTestConnectorConfig("imap");

  const [host, setHost] = useState("");
  const [port, setPort] = useState("993");
  const [username, setUsername] = useState("");
  const [mailbox, setMailbox] = useState("INBOX");
  const [password, setPassword] = useState("");
  const [msg, setMsg] = useState<string | null>(null);

  const view = config.data;
  const passwordSet = view?.secret_set?.password ?? false;

  // Seed the non-secret fields from the server view once loaded.
  useEffect(() => {
    if (!view) return;
    setHost(view.values.host ?? "");
    setPort(view.values.port ?? "993");
    setUsername(view.values.username ?? "");
    setMailbox(view.values.mailbox ?? "INBOX");
  }, [view]);

  const onSave = () => {
    setMsg(null);
    const updates: Record<string, string> = { host, port, username, mailbox };
    if (password) updates.password = password; // omit when blank → keep existing
    save.mutate(updates, {
      onSuccess: () => {
        setPassword("");
        setMsg("Saved.");
      },
      onError: (err) => setMsg(getApiErrorMessage(err, "Save failed")),
    });
  };

  const onTest = () => {
    setMsg(null);
    test.mutate(undefined, {
      onSuccess: (r) => setMsg(r.ok ? `✓ ${r.detail}` : `✗ ${r.detail}`),
      onError: (err) => setMsg(getApiErrorMessage(err, "Test failed")),
    });
  };

  const onClear = () => {
    setMsg(null);
    clear.mutate(undefined, {
      onSuccess: () => {
        setPassword("");
        setMsg("Disconnected — reverted to environment config.");
      },
      onError: (err) => setMsg(getApiErrorMessage(err, "Disconnect failed")),
    });
  };

  const configured = host.trim() && username.trim() && passwordSet;

  return (
    <CollapsibleCard
      id="imap"
      title="Email receipts (IMAP)"
      status={
        <Badge color={configured ? "green" : "gray"} variant={configured ? "filled" : "light"}>
          {configured ? "connected" : "not configured"}
        </Badge>
      }
    >
      <Text c="dimmed" size="sm">
        Pull order-confirmation emails from a mailbox to seed inventory. Use a dedicated{" "}
        <strong>app password</strong>, not your main account password.
      </Text>

      {view && !view.secrets_available ? (
        <Alert color="orange">
          Secret storage is disabled: set <code>CONFIG_SECRET_KEY</code> in the server environment to save
          credentials in-app.
        </Alert>
      ) : null}
      {view?.from_env_only && (view.values.host || view.secret_set.password) ? (
        <Alert color="blue">Currently using environment config. Saving here stores an in-app override.</Alert>
      ) : null}

      <Group grow>
        <TextInput label="IMAP host" placeholder="imap.gmail.com" value={host} onChange={(e) => setHost(e.currentTarget.value)} />
        <TextInput label="Port" value={port} onChange={(e) => setPort(e.currentTarget.value)} w={120} />
      </Group>
      <TextInput
        label="Username"
        placeholder="family@example.com"
        value={username}
        onChange={(e) => setUsername(e.currentTarget.value)}
      />
      <PasswordInput
        label="App password"
        placeholder={passwordSet ? "•••••••• (leave blank to keep)" : "Enter app password"}
        value={password}
        onChange={(e) => setPassword(e.currentTarget.value)}
      />
      <TextInput label="Mailbox" value={mailbox} onChange={(e) => setMailbox(e.currentTarget.value)} />

      {msg ? <Alert color="blue">{msg}</Alert> : null}

      <Group>
        <Button onClick={onSave} loading={save.isPending} disabled={!!view && !view.secrets_available}>
          Save
        </Button>
        <Button variant="light" onClick={onTest} loading={test.isPending} disabled={!configured}>
          Test connection
        </Button>
        <Button variant="subtle" color="red" onClick={onClear} loading={clear.isPending} disabled={!view?.has_override}>
          Disconnect
        </Button>
      </Group>
    </CollapsibleCard>
  );
}

function HomeAssistantCard() {
  const config = useConnectorConfig("home_assistant");
  const save = useSaveConnectorConfig("home_assistant");
  const clear = useClearConnectorConfig("home_assistant");
  const test = useTestConnectorConfig("home_assistant");
  const deliveries = useWebhookDeliveries("home_assistant", 1);

  const [webhookUrl, setWebhookUrl] = useState("");
  const [secret, setSecret] = useState("");
  const [msg, setMsg] = useState<string | null>(null);

  const view = config.data;
  const secretSet = view?.secret_set?.secret ?? false;

  useEffect(() => {
    if (!view) return;
    setWebhookUrl(view.values.webhook_url ?? "");
  }, [view]);

  const onSave = () => {
    setMsg(null);
    const updates: Record<string, string> = { webhook_url: webhookUrl };
    if (secret) updates.secret = secret; // omit when blank → keep existing
    save.mutate(updates, {
      onSuccess: () => {
        setSecret("");
        setMsg("Saved.");
      },
      onError: (err) => setMsg(getApiErrorMessage(err, "Save failed")),
    });
  };

  const onTest = () => {
    setMsg(null);
    test.mutate(undefined, {
      onSuccess: (r) => setMsg(r.ok ? `✓ ${r.detail}` : `✗ ${r.detail}`),
      onError: (err) => setMsg(getApiErrorMessage(err, "Test failed")),
    });
  };

  const onClear = () => {
    setMsg(null);
    clear.mutate(undefined, {
      onSuccess: () => {
        setSecret("");
        setMsg("Disconnected — reverted to environment config.");
      },
      onError: (err) => setMsg(getApiErrorMessage(err, "Disconnect failed")),
    });
  };

  const configured = Boolean(webhookUrl.trim());
  const lastDelivery = deliveries.data?.[0];

  return (
    <CollapsibleCard
      id="home_assistant"
      title="Home Assistant (webhook)"
      status={
        <Badge color={configured ? "green" : "gray"} variant={configured ? "filled" : "light"}>
          {configured ? "connected" : "not configured"}
        </Badge>
      }
    >
      <Text c="dimmed" size="sm">
        Push inventory events (added, removed, adjusted, moved) to a Home Assistant webhook trigger so
        automations can react — expiring-food notifications, kitchen dashboards, and more. Create a webhook
        trigger in HA and paste its URL here.
      </Text>

      {view && !view.secrets_available ? (
        <Alert color="orange">
          Secret storage is disabled: set <code>CONFIG_SECRET_KEY</code> in the server environment to save the
          signing secret in-app.
        </Alert>
      ) : null}
      {view?.from_env_only && (view.values.webhook_url || view.secret_set.secret) ? (
        <Alert color="blue">Currently using environment config. Saving here stores an in-app override.</Alert>
      ) : null}

      <TextInput
        label="Webhook URL"
        placeholder="http://homeassistant.local:8123/api/webhook/pantryos"
        value={webhookUrl}
        onChange={(e) => setWebhookUrl(e.currentTarget.value)}
      />
      <PasswordInput
        label="Signing secret (optional)"
        description="When set, events carry an HMAC-SHA256 signature (X-PantryOS-Signature) your automation can verify."
        placeholder={secretSet ? "•••••••• (leave blank to keep)" : "Enter a shared secret"}
        value={secret}
        onChange={(e) => setSecret(e.currentTarget.value)}
      />

      {msg ? <Alert color="blue">{msg}</Alert> : null}

      <Group>
        <Button onClick={onSave} loading={save.isPending} disabled={!!view && !view.secrets_available}>
          Save
        </Button>
        <Button variant="light" onClick={onTest} loading={test.isPending} disabled={!configured}>
          Send test event
        </Button>
        <Button variant="subtle" color="red" onClick={onClear} loading={clear.isPending} disabled={!view?.has_override}>
          Disconnect
        </Button>
      </Group>

      <Group gap="xs">
        {lastDelivery ? (
          <>
            <Badge color={lastDelivery.status === "delivered" ? "green" : "red"} variant="light">
              {lastDelivery.status}
            </Badge>
            <Text size="sm" c="dimmed">
              Last delivery: {lastDelivery.event_type} · {new Date(lastDelivery.created_at).toLocaleString()}
              {lastDelivery.attempts > 1 ? ` (${lastDelivery.attempts} attempts)` : ""}
            </Text>
          </>
        ) : (
          <Text size="sm" c="dimmed">
            No deliveries yet — save a URL and send a test event.
          </Text>
        )}
      </Group>
    </CollapsibleCard>
  );
}

type ProviderField = { name: string; label: string; secret?: boolean; placeholder?: string };

type KrogerStore = { location_id: string; name: string; address: string };

/** Find a Kroger store by ZIP (the location id isn't shown on their website). */
function KrogerStoreFinder({ onPick }: { onPick: (locationId: string) => void }) {
  const [zip, setZip] = useState("");
  const [stores, setStores] = useState<KrogerStore[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const find = async () => {
    setErr(null);
    setBusy(true);
    try {
      const res = await api.get<{ stores: KrogerStore[] }>("/api/admin/integrations/kroger/locations", {
        params: { zip: zip.trim() },
      });
      setStores(res.data.stores);
    } catch (e) {
      setErr(getApiErrorMessage(e, "Store lookup failed — save your client id/secret first."));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card withBorder padding="sm" bg="var(--mantine-color-default-hover)">
      <Stack gap="xs">
        <Text size="sm" fw={500}>
          Find your store
        </Text>
        <Group align="flex-end" gap="xs">
          <TextInput
            label="ZIP code"
            placeholder="e.g. 45202"
            value={zip}
            onChange={(e) => {
              const v = e.currentTarget.value;
              setZip(v);
            }}
            w={140}
          />
          <Button variant="light" onClick={find} loading={busy} disabled={zip.trim().length < 3}>
            Find stores
          </Button>
        </Group>
        {err ? <Text size="xs" c="red">{err}</Text> : null}
        {stores?.length === 0 ? <Text size="xs" c="dimmed">No stores found near that ZIP.</Text> : null}
        {(stores ?? []).map((s) => (
          <Group key={s.location_id} justify="space-between" wrap="nowrap">
            <div>
              <Text size="sm">{s.name}</Text>
              <Text size="xs" c="dimmed">
                {s.address} · id {s.location_id}
              </Text>
            </div>
            <Button size="compact-xs" variant="subtle" onClick={() => onPick(s.location_id)}>
              Use
            </Button>
          </Group>
        ))}
      </Stack>
    </Card>
  );
}

/** Generic key card for a barcode/product-data provider (ADR 0013). */
function ProviderKeyCard({
  connector,
  title,
  description,
  fields,
  footer,
  extra,
}: {
  connector: string;
  title: string;
  description: ReactNode;
  fields: ProviderField[];
  footer?: ReactNode;
  extra?: (setValue: (name: string, value: string) => void) => ReactNode;
}) {
  const config = useConnectorConfig(connector);
  const save = useSaveConnectorConfig(connector);
  const clear = useClearConnectorConfig(connector);
  const view = config.data;

  const [values, setValues] = useState<Record<string, string>>({});
  const [msg, setMsg] = useState<string | null>(null);

  useEffect(() => {
    if (!view) return;
    const seed: Record<string, string> = {};
    for (const f of fields) if (!f.secret) seed[f.name] = view.values?.[f.name] ?? "";
    setValues(seed);
  }, [view]); // eslint-disable-line react-hooks/exhaustive-deps

  const onSave = () => {
    setMsg(null);
    const updates: Record<string, string> = {};
    for (const f of fields) {
      const v = values[f.name] ?? "";
      if (f.secret && !v) continue; // blank secret = keep existing
      updates[f.name] = v;
    }
    save.mutate(updates, {
      onSuccess: () => {
        // Clear secret inputs after save so they're not held in the DOM.
        setValues((cur) => {
          const next = { ...cur };
          for (const f of fields) if (f.secret) delete next[f.name];
          return next;
        });
        setMsg("Saved.");
      },
      onError: (err) => setMsg(getApiErrorMessage(err, "Save failed")),
    });
  };

  return (
    <CollapsibleCard
      id={connector}
      title={title}
      status={
        view ? (
          <Badge variant="light" color={view.from_env_only ? "gray" : "green"}>
            {view.from_env_only ? "from env / unset" : "configured in-app"}
          </Badge>
        ) : null
      }
    >
      <Text size="sm" c="dimmed">
        {description}
      </Text>
      {view && !view.secrets_available ? (
        <Alert color="orange">Set CONFIG_SECRET_KEY on the server to store keys in-app.</Alert>
      ) : null}
      {fields.map((f) =>
        f.secret ? (
          <PasswordInput
            key={f.name}
            label={f.label}
            placeholder={view?.secret_set?.[f.name] ? "•••••••• (set — leave blank to keep)" : f.placeholder}
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
      {extra ? extra((name, value) => setValues((c) => ({ ...c, [name]: value }))) : null}
      {footer}
      {msg ? <Text size="sm">{msg}</Text> : null}
      <Group>
        <Button onClick={onSave} loading={save.isPending}>
          Save
        </Button>
        {view?.has_override ? (
          <Button variant="default" onClick={() => clear.mutate(undefined, { onSuccess: () => setMsg("Reverted to env.") })}>
            Clear (use env)
          </Button>
        ) : null}
      </Group>
    </CollapsibleCard>
  );
}

type KrogerOAuthStatus = {
  connected: boolean;
  expires_at: number | null;
  scope: string | null;
  secrets_available: boolean;
  redirect_uri: string;
  has_credentials: boolean;
};

/** Kroger Cart authorization (Phase 4): link a Kroger account so the shopping
 *  list can be pushed into its cart. Three-legged OAuth handled server-side. */
function KrogerCartCard() {
  const qc = useQueryClient();
  const status = useQuery<KrogerOAuthStatus>({
    queryKey: ["kroger-oauth-status"],
    queryFn: async () => (await api.get<KrogerOAuthStatus>("/api/integrations/kroger/oauth/status")).data,
  });
  const [msg, setMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // Reflect the callback's ?kroger=connected|error result, then clean the URL.
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const outcome = params.get("kroger");
    if (!outcome) return;
    if (outcome === "connected") setMsg("✓ Kroger account connected.");
    else if (outcome === "error")
      setMsg("✗ Kroger connection failed — check the client ID/secret and redirect URI, then retry.");
    qc.invalidateQueries({ queryKey: ["kroger-oauth-status"] });
    window.history.replaceState({}, "", window.location.pathname);
  }, [qc]);

  const view = status.data;
  const connected = view?.connected ?? false;
  const hasCreds = view?.has_credentials ?? false;
  const redirectUri = view?.redirect_uri ?? "";
  const expectedRedirect = `${window.location.origin}/api/integrations/kroger/oauth/callback`;

  const connect = async () => {
    setMsg(null);
    setBusy(true);
    try {
      const res = await api.post<{ authorize_url: string }>("/api/integrations/kroger/oauth/start");
      window.location.href = res.data.authorize_url; // off to Kroger's consent page
    } catch (e) {
      setMsg(getApiErrorMessage(e, "Could not start Kroger authorization"));
      setBusy(false);
    }
  };

  const disconnect = async () => {
    setMsg(null);
    setBusy(true);
    try {
      await api.post("/api/integrations/kroger/oauth/disconnect");
      await qc.invalidateQueries({ queryKey: ["kroger-oauth-status"] });
      setMsg("Disconnected.");
    } catch (e) {
      setMsg(getApiErrorMessage(e, "Disconnect failed"));
    } finally {
      setBusy(false);
    }
  };

  const secretsOff = view ? !view.secrets_available : false;

  return (
    <CollapsibleCard
      id="kroger_cart"
      title="Kroger cart (ordering)"
      status={
        <Badge color={connected ? "green" : "gray"} variant={connected ? "filled" : "light"}>
          {connected ? "connected" : "not connected"}
        </Badge>
      }
    >
      <Text c="dimmed" size="sm">
        Authorize once to send your shopping list into your Kroger cart for checkout. Uses the client ID/secret
        above plus the redirect URI, and needs the <b>Cart</b> scope added to your Kroger app.
      </Text>

      {secretsOff ? (
        <Alert color="orange">
          Set <code>CONFIG_SECRET_KEY</code> on the server to store the Kroger authorization.
        </Alert>
      ) : null}
      {!hasCreds ? <Alert color="yellow">Save your Kroger client ID and secret above first.</Alert> : null}
      {hasCreds && !redirectUri ? (
        <Alert color="yellow">
          Set the Kroger <b>redirect URI</b> above to <b>{expectedRedirect}</b> and register that exact URL in your
          Kroger app.
        </Alert>
      ) : null}

      {msg ? <Alert color="blue">{msg}</Alert> : null}

      <Group>
        {connected ? (
          <Button color="red" variant="light" onClick={disconnect} loading={busy}>
            Disconnect Kroger
          </Button>
        ) : (
          <Button onClick={connect} loading={busy} disabled={!hasCreds || !redirectUri || secretsOff}>
            Connect Kroger account
          </Button>
        )}
      </Group>
    </CollapsibleCard>
  );
}

export function IntegrationsPage() {
  return (
    <Stack>
      <Title order={2}>Integrations</Title>
      <Text c="dimmed">
        Connect PantryOS to your household's services. Saved credentials are encrypted and are not returned to the browser.
      </Text>

      <Title order={3} mt="md">
        Single sign-on
      </Title>
      <OidcCard />

      <Title order={3} mt="md">
        Product data sources
      </Title>
      <Text size="sm" c="dimmed">
        Barcode lookup and shopping search. Put your own keys here in free mode; the hosted version supplies them
        later.
      </Text>

      <CollapsibleCard
        id="off"
        title="Open Food Facts"
        status={
          <Badge variant="light" color="green">
            keyless
          </Badge>
        }
      >
        <Text size="sm" c="dimmed">
          Free and open — no key required. Always on as the first barcode lookup source.
        </Text>
      </CollapsibleCard>

      <CollapsibleCard
        id="themealdb"
        title="TheMealDB"
        status={
          <Badge variant="light" color="green">
            keyless
          </Badge>
        }
      >
        <Text size="sm" c="dimmed">
          Free and open — no key required. Always on for Recipes → Find a Recipe Online. A small, curated
          catalog; add Spoonacular below for a much bigger one.
        </Text>
      </CollapsibleCard>

      <ProviderKeyCard
        connector="usda"
        title="USDA FoodData Central"
        description="US branded-grocery coverage OFF often misses (public-domain). Free key at api.data.gov/signup."
        fields={[{ name: "api_key", label: "API key", secret: true, placeholder: "your data.gov API key" }]}
      />

      <ProviderKeyCard
        connector="spoonacular"
        title="Spoonacular"
        description="Bigger recipe search catalog for Recipes → Find a Recipe Online, alongside TheMealDB above. Free tier (50 requests/day) at spoonacular.com/food-api."
        fields={[{ name: "api_key", label: "API key", secret: true, placeholder: "your Spoonacular API key" }]}
      />

      <ProviderKeyCard
        connector="kroger"
        title="Kroger"
        description="Product search (household + food, with prices). Register an app at developer.kroger.com."
        fields={[
          { name: "client_id", label: "Client ID", placeholder: "kroger client id" },
          { name: "client_secret", label: "Client secret", secret: true },
          { name: "location_id", label: "Store location ID (optional)", placeholder: "for prices, e.g. 01400943" },
          {
            name: "redirect_uri",
            label: "Redirect URI (for cart ordering)",
            placeholder: "https://<host>/api/integrations/kroger/oauth/callback",
          },
        ]}
        extra={(setValue) => <KrogerStoreFinder onPick={(id) => setValue("location_id", id)} />}
        footer={
          <Alert color="blue" variant="light">
            Product search uses client credentials — no redirect URI needed. Cart <b>ordering</b> (below) does: set
            the redirect URI to <b>{`${window.location.origin}/api/integrations/kroger/oauth/callback`}</b> and
            register that exact URL (with the Cart scope) in your Kroger app. Set a store <b>location ID</b> to get
            prices in results.
          </Alert>
        }
      />

      <Title order={3} mt="md">
        Ordering
      </Title>
      <Text size="sm" c="dimmed">
        Send your shopping list straight to a vendor cart for checkout. Kroger orders directly; Instacart builds a
        multi-retailer link.
      </Text>
      <KrogerCartCard />

      <ProviderKeyCard
        connector="instacart"
        title="Instacart (multi-retailer)"
        description="Turns the shopping list into an Instacart link where you pick any Instacart retailer and check out. Dormant until a Developer Platform API key is set — Instacart isn't accepting new developer applications yet."
        fields={[
          { name: "api_key", label: "API key", secret: true, placeholder: "Instacart Developer Platform key" },
          {
            name: "base_url",
            label: "Base URL (optional)",
            placeholder: "https://connect.dev.instacart.tools for the dev server",
          },
        ]}
      />

      <Title order={3} mt="md">
        Other services
      </Title>
      <ImapCard />
      <HomeAssistantCard />
      <WildstoneCard />

      <Title order={3} mt="md">
        AI (optional)
      </Title>
      <Text size="sm" c="dimmed">
        Smarter receipt reading. Local providers keep everything on your hardware and always win over
        cloud AI (ADR 0018).
      </Text>
      <ProviderKeyCard
        connector="ollama"
        title="Ollama (local)"
        description="A local model server on your network. Use a vision-capable model (e.g. llava, qwen-vl) for receipt photos."
        fields={[
          { name: "base_url", label: "Base URL", placeholder: "http://localhost:11434" },
          { name: "model", label: "Model", placeholder: "llava" },
        ]}
      />
      <ProviderKeyCard
        connector="openai_compat"
        title="OpenAI-compatible API"
        description="Any OpenAI-style endpoint — OpenAI, OpenRouter, LM Studio, llama.cpp server — with your own key."
        fields={[
          { name: "base_url", label: "Base URL", placeholder: "https://api.openai.com" },
          { name: "api_key", label: "API key", secret: true },
          { name: "model", label: "Model", placeholder: "gpt-4o-mini" },
        ]}
      />
      <CloudAiCard />

      <Title order={3} mt="md">
        Classifier (optional)
      </Title>
      <ClassifierCard />
      <ClassifierEvaluationPanel />

      <Title order={3} mt="md">
        Connected services
      </Title>
      <Text size="sm" c="dimmed">
        Optional hosted extras (ADR 0018): store connections without registering your own developer keys, and
        cloud AI. Everything above works without them.
      </Text>
      <FeatureGate
        feature={FEATURE.storeIntegrations}
        fallback={
          <CollapsibleCard
            id="brokered_store_connections"
            title="Brokered store connections"
            status={
              <Badge variant="light" color="grape">
                PantryOS Connect
              </Badge>
            }
          >
            <Text size="sm" c="dimmed">
              With an activation key, connect Kroger (and future retailers) with one click — no developer
              account, no keys to paste. This instance is running in free mode; bring-your-own keys above stay
              free forever.
            </Text>
          </CollapsibleCard>
        }
      >
        <BrokeredKrogerCard />
      </FeatureGate>
    </Stack>
  );
}
