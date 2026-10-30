import { Alert, Badge, Button, Group, Menu, Modal, Stack, Text, TextInput } from "@mantine/core";
import { useDisclosure } from "@mantine/hooks";
import { UserRound } from "lucide-react";
import { useMemo, useState } from "react";

import { api } from "@/api/client";
import { useHouseholdProfiles } from "@/api/hooks";
import type { HouseholdProfile } from "@/api/types";
import { useAppState } from "@/state/AppStateProvider";

/**
 * Kiosk identity (ADR 0004): "who's using this?" — selecting a household profile
 * stamps everyday actions with the right actor (via the X-Household-Profile
 * header the axios client injects). PIN-protected profiles ask for their PIN
 * before switching.
 */
export function ProfilePicker() {
  const { activeProfileId, setActiveProfileId } = useAppState();
  const { data: profiles } = useHouseholdProfiles();

  const [pinOpened, pinModal] = useDisclosure(false);
  const [pinTarget, setPinTarget] = useState<HouseholdProfile | null>(null);
  const [pinValue, setPinValue] = useState("");
  const [pinError, setPinError] = useState<string | null>(null);
  const [checking, setChecking] = useState(false);

  const active = useMemo(
    () => (profiles ?? []).find((p) => p.id === activeProfileId) ?? null,
    [profiles, activeProfileId],
  );
  const people = profiles ?? [];

  const select = (profile: HouseholdProfile) => {
    if (profile.has_pin) {
      setPinTarget(profile);
      setPinValue("");
      setPinError(null);
      pinModal.open();
      return;
    }
    setActiveProfileId(profile.id);
  };

  const confirmPin = async () => {
    if (!pinTarget) return;
    setChecking(true);
    setPinError(null);
    try {
      const res = await api.post<{ valid: boolean }>(`/api/household/profiles/${pinTarget.id}/verify-pin`, {
        pin: pinValue,
      });
      if (res.data.valid) {
        setActiveProfileId(pinTarget.id);
        pinModal.close();
      } else {
        setPinError("Incorrect PIN");
      }
    } catch {
      setPinError("Could not verify PIN");
    } finally {
      setChecking(false);
    }
  };

  return (
    <>
      <Menu shadow="md" width={240} position="bottom-end">
        <Menu.Target>
          <Button
            variant={active ? "default" : "light"}
            color="terracotta"
            radius="xl"
            size="sm"
            leftSection={<UserRound size={15} strokeWidth={2.4} />}
          >
            {active ? (
              <>
                <Text span size="sm" c="dimmed" mr={6}>
                  Acting as
                </Text>
                {active.display_name}
              </>
            ) : (
              "Who's using this?"
            )}
          </Button>
        </Menu.Target>

        <Menu.Dropdown>
          <Menu.Label>Household</Menu.Label>
          {people.length === 0 ? (
            <Menu.Item disabled>No people yet — add them in Admin → Household</Menu.Item>
          ) : (
            people.map((p) => (
              <Menu.Item
                key={p.id}
                onClick={() => select(p)}
                rightSection={
                  p.id === activeProfileId ? (
                    <Badge size="xs">current</Badge>
                  ) : p.has_pin ? (
                    <Text span size="xs" c="dimmed">
                      🔒
                    </Text>
                  ) : null
                }
              >
                {p.display_name}
              </Menu.Item>
            ))
          )}
          {active ? (
            <>
              <Menu.Divider />
              <Menu.Item color="gray" onClick={() => setActiveProfileId(null)}>
                Clear selection
              </Menu.Item>
            </>
          ) : null}
        </Menu.Dropdown>
      </Menu>

      <Modal opened={pinOpened} onClose={pinModal.close} title={`Enter PIN — ${pinTarget?.display_name ?? ""}`}>
        <Stack>
          {pinError ? <Alert color="red">{pinError}</Alert> : null}
          <TextInput
            label="PIN"
            inputMode="numeric"
            autoFocus
            value={pinValue}
            onChange={(e) => setPinValue(e.currentTarget.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") void confirmPin();
            }}
          />
          <Group justify="flex-end">
            <Button variant="default" onClick={pinModal.close}>
              Cancel
            </Button>
            <Button onClick={() => void confirmPin()} loading={checking}>
              Unlock
            </Button>
          </Group>
        </Stack>
      </Modal>
    </>
  );
}
