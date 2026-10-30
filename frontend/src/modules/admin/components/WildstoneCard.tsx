import { Alert, Badge, Button, Code, CopyButton, Group, Stack, Table, Text, TextInput } from "@mantine/core";
import { useEffect, useState } from "react";

import { getApiErrorMessage } from "@/api/errors";

import { CollapsibleCard } from "./CollapsibleCard";
import {
  type MemberLink,
  type PairingCode,
  useCreatePairingCode,
  useDisconnectWildstone,
  useMemberLinks,
  useSetMemberLink,
  useTestWildstone,
  useWildstoneStatus,
  type WildstoneStatus,
} from "../hooks/wildstone-hooks";

/** Seconds left until `expiresAt`, ticking once a second. */
function useCountdown(expiresAt: number | null) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (expiresAt === null) return;
    const t = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(t);
  }, [expiresAt]);
  return expiresAt === null ? 0 : Math.max(0, Math.round((expiresAt - now) / 1000));
}

/** The web address pairing hands Wildstone for its dashboard's Pantry card. */
function WebAddress({ view }: { view: WildstoneStatus }) {
  if (view.web_url) {
    return (
      <Text size="sm" c="dimmed">
        Wildstone&apos;s dashboard will link to <Code>{view.web_url}</Code>
      </Text>
    );
  }
  if (view.web_url_problem) {
    return (
      <Alert color="orange" title="Wildstone won't be told PantryOS's web address">
        PANTRYOS_PUBLIC_URL is set but {view.web_url_problem}.
      </Alert>
    );
  }
  return (
    <Text size="xs" c="dimmed">
      Set PANTRYOS_PUBLIC_URL to the address people open PantryOS at, so Wildstone&apos;s dashboard can link here.
    </Text>
  );
}

function mmss(seconds: number) {
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
}

function MemberRow({ link }: { link: MemberLink }) {
  const save = useSetMemberLink();
  const [value, setValue] = useState(link.member ?? "");
  const [err, setErr] = useState<string | null>(null);
  const dirty = value.trim() !== (link.member ?? "");
  return (
    <Table.Tr>
      <Table.Td>{link.display_name}</Table.Td>
      <Table.Td>
        <TextInput
          aria-label={`Wildstone member for ${link.display_name}`}
          placeholder="Wildstone member name"
          value={value}
          error={err}
          onChange={(e) => setValue(e.currentTarget.value)}
          size="xs"
        />
      </Table.Td>
      <Table.Td>
        <Button
          size="xs"
          variant="light"
          disabled={!dirty}
          loading={save.isPending}
          onClick={() => {
            setErr(null);
            save.mutate(
              { profileId: link.profile_id, member: value },
              { onError: (e) => setErr(getApiErrorMessage(e, "Couldn't save")) },
            );
          }}
        >
          Save
        </Button>
      </Table.Td>
    </Table.Tr>
  );
}

/**
 * Wildstone Home Lab (ADR 0026 §7). Pairing is the whole setup: PantryOS
 * shows a one-time code, the admin types it into Wildstone, and the two
 * exchange credentials machine to machine — nobody copies a secret. Once
 * connected, the card shows whether events are reaching Wildstone and lets
 * the admin say which Wildstone member each household profile is.
 */
export function WildstoneCard() {
  const [pairing, setPairing] = useState<(PairingCode & { expiresAt: number; pairedBefore: string | null }) | null>(null);
  const secondsLeft = useCountdown(pairing?.expiresAt ?? null);
  const waiting = pairing !== null && secondsLeft > 0;

  // While a code is on screen, watch for Wildstone completing the pairing.
  const status = useWildstoneStatus(waiting ? 3000 : 0);
  const createCode = useCreatePairingCode();
  const test = useTestWildstone();
  const disconnect = useDisconnectWildstone();
  const [confirmDisconnect, setConfirmDisconnect] = useState(false);
  const [msg, setMsg] = useState<{ color: string; text: string } | null>(null);

  const view = status.data;
  const connected = view?.connected ?? false;
  const members = useMemberLinks(connected);

  // The pairing landed: `paired_at` moved past what it was when the code
  // was made. Put the code away and say so.
  useEffect(() => {
    if (!pairing || !view?.paired_at || view.paired_at === pairing.pairedBefore) return;
    setPairing(null);
    setMsg({ color: "green", text: `Paired with Wildstone at ${view.keystone_url}.` });
  }, [pairing, view]);

  const newCode = () => {
    setMsg(null);
    const pairedBefore = view?.paired_at ?? null;
    createCode.mutate(undefined, {
      onSuccess: (c) => setPairing({ ...c, expiresAt: Date.now() + c.expires_in * 1000, pairedBefore }),
      onError: (e) => setMsg({ color: "red", text: getApiErrorMessage(e, "Couldn't create a pairing code") }),
    });
  };

  const badge = status.isLoading ? null : connected ? (
    <Badge color="green" variant="light">
      Connected
    </Badge>
  ) : (
    <Badge color="gray" variant="light">
      Not connected
    </Badge>
  );

  return (
    <CollapsibleCard id="wildstone" title="Wildstone Home Lab" status={badge}>
      <Text size="sm" c="dimmed">
        Lets the household's Wildstone agents check stock, keep the grocery list and cook along with you, and lets
        PantryOS tell Wildstone when meals are logged, groceries come in or something runs low.
      </Text>

      {status.isError && (
        <Alert color="red">{getApiErrorMessage(status.error, "Couldn't load the Wildstone connection")}</Alert>
      )}

      {waiting && pairing ? (
        <Stack gap="xs">
          <Text size="sm">In Wildstone, pair PantryOS with this code and this PantryOS's address:</Text>
          <Group gap="xs" align="center">
            <Code fz="xl" fw={700} style={{ letterSpacing: "0.15em" }} data-testid="pairing-code">
              {pairing.code}
            </Code>
            <CopyButton value={pairing.code}>
              {({ copied, copy }) => (
                <Button size="xs" variant="subtle" onClick={copy}>
                  {copied ? "Copied" : "Copy"}
                </Button>
              )}
            </CopyButton>
          </Group>
          <Group gap="xs" align="center">
            <Code>{window.location.origin}</Code>
            <CopyButton value={window.location.origin}>
              {({ copied, copy }) => (
                <Button size="xs" variant="subtle" onClick={copy}>
                  {copied ? "Copied" : "Copy address"}
                </Button>
              )}
            </CopyButton>
          </Group>
          <Text size="xs" c="dimmed">
            Works once. Expires in {mmss(secondsLeft)}. This screen updates when the pairing completes.
          </Text>
          <Group>
            <Button size="xs" variant="subtle" onClick={() => setPairing(null)}>
              Cancel
            </Button>
          </Group>
        </Stack>
      ) : (
        <>
          {pairing && secondsLeft === 0 && (
            <Alert color="orange">That code expired before it was used. Ask for a new one.</Alert>
          )}
          {connected && view ? (
            <Stack gap={4}>
              <Text size="sm">
                Sending events to <Code>{view.keystone_url}</Code>
              </Text>
              <Text size="sm" c="dimmed">
                {view.relay.delivered} sent · {view.relay.pending} waiting · {view.relay.rejected} refused by Wildstone
                {view.relay.last_delivered_at ? ` · last sent ${new Date(view.relay.last_delivered_at).toLocaleString()}` : ""}
              </Text>
              {view.relay.last_error && (
                <Alert color="orange" title="Wildstone isn't taking events right now">
                  {view.relay.last_error}
                </Alert>
              )}
            </Stack>
          ) : (
            <Text size="sm">Not connected. Pair once and both sides are set up.</Text>
          )}
          {view && <WebAddress view={view} />}
          <Group>
            <Button onClick={newCode} loading={createCode.isPending}>
              {connected ? "Pair again" : "Pair with Wildstone"}
            </Button>
            {connected && (
              <Button
                variant="light"
                loading={test.isPending}
                onClick={() => {
                  setMsg(null);
                  test.mutate(undefined, {
                    onSuccess: () => setMsg({ color: "green", text: "Wildstone received a test event." }),
                    onError: (e) => setMsg({ color: "red", text: getApiErrorMessage(e, "The test event failed") }),
                  });
                }}
              >
                Send test event
              </Button>
            )}
            {connected &&
              (confirmDisconnect ? (
                <Button
                  color="red"
                  loading={disconnect.isPending}
                  onClick={() =>
                    disconnect.mutate(undefined, {
                      onSuccess: () => {
                        setConfirmDisconnect(false);
                        setMsg({ color: "blue", text: "Disconnected. Events not yet sent were dropped." });
                      },
                      onError: (e) => setMsg({ color: "red", text: getApiErrorMessage(e, "Couldn't disconnect") }),
                    })
                  }
                >
                  Yes, disconnect
                </Button>
              ) : (
                <Button variant="subtle" color="red" onClick={() => setConfirmDisconnect(true)}>
                  Disconnect
                </Button>
              ))}
          </Group>
        </>
      )}

      {msg && <Alert color={msg.color}>{msg.text}</Alert>}

      {connected && (
        <Stack gap="xs">
          <Text fw={600} size="sm">
            Who is who
          </Text>
          <Text size="xs" c="dimmed">
            Link a household profile to its Wildstone member so a logged meal reaches the right person. Leave it empty
            and the meal still reaches Wildstone, without a name on it.
          </Text>
          {members.isError && <Alert color="red">{getApiErrorMessage(members.error, "Couldn't load profiles")}</Alert>}
          {members.data && members.data.length === 0 && (
            <Text size="sm" c="dimmed">
              No household profiles yet.
            </Text>
          )}
          {members.data && members.data.length > 0 && (
            <Table>
              <Table.Tbody>
                {members.data.map((link) => (
                  <MemberRow key={link.profile_id} link={link} />
                ))}
              </Table.Tbody>
            </Table>
          )}
        </Stack>
      )}
    </CollapsibleCard>
  );
}
