import { zodResolver } from "@hookform/resolvers/zod";
import {
  Alert,
  Badge,
  Button,
  Card,
  Group,
  Modal,
  NumberInput,
  Stack,
  Switch,
  Table,
  Text,
  TextInput,
  Title,
} from "@mantine/core";
import { useDisclosure } from "@mantine/hooks";
import { Controller, useForm } from "react-hook-form";
import { useState } from "react";

import { getApiErrorMessage } from "@/api/errors";
import type { HouseholdProfile } from "@/api/types";
import { useAuth } from "@/state/AuthProvider";

import { profileSchema, type ProfileValues } from "../forms/profile-schema";
import { useCreateProfile, useHouseholdProfiles, useSetProfilePin, useUpdateProfile } from "../hooks/admin-hooks";

export function HouseholdPage() {
  const { account, logout } = useAuth();
  const [includeInactive, setIncludeInactive] = useState(false);
  const profilesQuery = useHouseholdProfiles(includeInactive);
  const createProfile = useCreateProfile();
  const updateProfile = useUpdateProfile();
  const setProfilePin = useSetProfilePin();

  const [pinModalOpened, pinModal] = useDisclosure(false);
  const [pinTarget, setPinTarget] = useState<HouseholdProfile | null>(null);
  const [pinValue, setPinValue] = useState("");
  const [pinError, setPinError] = useState<string | null>(null);

  const openPinModal = (profile: HouseholdProfile) => {
    setPinTarget(profile);
    setPinValue("");
    setPinError(null);
    pinModal.open();
  };

  const savePin = async () => {
    if (!pinTarget) return;
    try {
      await setProfilePin.mutateAsync({ id: pinTarget.id, pin: pinValue });
      pinModal.close();
    } catch (err) {
      setPinError(getApiErrorMessage(err, "Could not set PIN"));
    }
  };

  const {
    register,
    handleSubmit,
    control,
    reset,
    formState: { errors, isValid },
  } = useForm<ProfileValues>({ resolver: zodResolver(profileSchema), mode: "onChange" });

  const onCreate = handleSubmit(async (values) => {
    await createProfile.mutateAsync({
      display_name: values.display_name,
      daily_calorie_target: values.daily_calorie_target,
    });
    reset({ display_name: "", daily_calorie_target: null });
  });

  const profiles = profilesQuery.data ?? [];

  return (
    <Stack>
      <Group justify="space-between">
        <Title order={2}>Household</Title>
        <Group>
          <Text size="sm" c="dimmed">
            {account?.email}
          </Text>
          <Button variant="light" onClick={logout}>
            Log out
          </Button>
        </Group>
      </Group>

      <Text c="dimmed">
        People in your household. Profiles have no login — they're who inventory changes and calories are
        attributed to on the kiosk. Kids can be added here without giving them admin access.
      </Text>

      <Card withBorder padding="lg">
        <form onSubmit={onCreate}>
          <Stack>
            <Title order={4}>Add a person</Title>
            {createProfile.isError ? (
              <Alert color="red">{getApiErrorMessage(createProfile.error, "Could not add person")}</Alert>
            ) : null}
            <Group align="flex-end" grow>
              <TextInput label="Name" error={errors.display_name?.message} {...register("display_name")} />
              <Controller
                control={control}
                name="daily_calorie_target"
                render={({ field }) => (
                  <NumberInput
                    label="Daily calorie target (optional)"
                    min={0}
                    value={field.value ?? ""}
                    onChange={(v) => field.onChange(v === "" ? null : Number(v))}
                  />
                )}
              />
            </Group>
            <Group>
              <Button type="submit" disabled={!isValid} loading={createProfile.isPending}>
                Add person
              </Button>
            </Group>
          </Stack>
        </form>
      </Card>

      <Group justify="space-between">
        <Title order={4}>People</Title>
        <Switch
          label="Show inactive"
          checked={includeInactive}
          onChange={(e) => setIncludeInactive(e.currentTarget.checked)}
        />
      </Group>

      <Card withBorder padding={0}>
        <Table.ScrollContainer minWidth={620}>
        <Table striped>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>Name</Table.Th>
              <Table.Th>Calorie target</Table.Th>
              <Table.Th>PIN</Table.Th>
              <Table.Th>Status</Table.Th>
              <Table.Th />
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {profiles.length === 0 ? (
              <Table.Tr>
                <Table.Td colSpan={5}>
                  <Text c="dimmed">No people yet — add your family above.</Text>
                </Table.Td>
              </Table.Tr>
            ) : (
              profiles.map((p) => (
                <Table.Tr key={p.id}>
                  <Table.Td>{p.display_name}</Table.Td>
                  <Table.Td>{p.daily_calorie_target ?? "—"}</Table.Td>
                  <Table.Td>
                    <Group gap="xs">
                      <Badge color={p.has_pin ? "blue" : "gray"} variant="light">
                        {p.has_pin ? "Set" : "None"}
                      </Badge>
                      <Button size="compact-xs" variant="subtle" onClick={() => openPinModal(p)}>
                        {p.has_pin ? "Change" : "Set"}
                      </Button>
                      {p.has_pin ? (
                        <Button
                          size="compact-xs"
                          variant="subtle"
                          color="red"
                          onClick={() => setProfilePin.mutate({ id: p.id, pin: null })}
                        >
                          Clear
                        </Button>
                      ) : null}
                    </Group>
                  </Table.Td>
                  <Table.Td>
                    <Badge color={p.is_active ? "green" : "gray"}>{p.is_active ? "Active" : "Inactive"}</Badge>
                  </Table.Td>
                  <Table.Td>
                    <Button
                      size="xs"
                      variant="light"
                      color={p.is_active ? "red" : "green"}
                      loading={updateProfile.isPending}
                      onClick={() => updateProfile.mutate({ id: p.id, is_active: !p.is_active })}
                    >
                      {p.is_active ? "Deactivate" : "Reactivate"}
                    </Button>
                  </Table.Td>
                </Table.Tr>
              ))
            )}
          </Table.Tbody>
        </Table>
        </Table.ScrollContainer>
      </Card>

      <Modal opened={pinModalOpened} onClose={pinModal.close} title={`Set PIN — ${pinTarget?.display_name ?? ""}`}>
        <Stack>
          <Text size="sm" c="dimmed">
            A PIN is asked for when this person is selected on the kiosk. 4–12 digits. Leave blank and save to clear.
          </Text>
          {pinError ? <Alert color="red">{pinError}</Alert> : null}
          <TextInput
            label="PIN"
            inputMode="numeric"
            value={pinValue}
            onChange={(e) => setPinValue(e.currentTarget.value)}
          />
          <Group justify="flex-end">
            <Button variant="default" onClick={pinModal.close}>
              Cancel
            </Button>
            <Button onClick={savePin} loading={setProfilePin.isPending}>
              Save
            </Button>
          </Group>
        </Stack>
      </Modal>
    </Stack>
  );
}
