import { Alert, Button, CopyButton, Group, Modal, Stack, Text, TextInput } from "@mantine/core";
import { useEffect, useState } from "react";

import { apiBaseUrl } from "@/api/client";
import { getApiErrorMessage } from "@/api/errors";

import { useCalendarFeedStatus, useCreateCalendarFeed, useTurnOffCalendarFeed } from "../hooks/mealplan-hooks";

/**
 * Admin-only: a read-only calendar (iCal) link for the meal plan (#33), for a
 * phone calendar or the home lab. The link is shown once, right after it is
 * made; making a new one turns the old one off.
 */
export function CalendarFeedModal({ opened, onClose }: { opened: boolean; onClose: () => void }) {
  const status = useCalendarFeedStatus(opened);
  const create = useCreateCalendarFeed();
  const turnOff = useTurnOffCalendarFeed();
  const [url, setUrl] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (opened) {
      setUrl(null);
      setError(null);
    }
  }, [opened]);

  async function run(action: () => Promise<void>) {
    setError(null);
    try {
      await action();
    } catch (err) {
      setError(getApiErrorMessage(err, "Something went wrong"));
    }
  }

  const enabled = status.data?.enabled ?? false;

  return (
    <Modal opened={opened} onClose={onClose} title="Calendar link">
      <Stack gap="sm">
        <Text size="sm" c="dimmed">
          Add the meal plan to a calendar app as a read-only subscription. Anyone with the link can see the plan, so
          share it only with your household.
        </Text>
        {error && (
          <Alert color="red" variant="light">
            {error}
          </Alert>
        )}
        {status.isError && (
          <Alert color="red" variant="light">
            {getApiErrorMessage(status.error, "Only an admin can manage the calendar link")}
          </Alert>
        )}
        {url && (
          <Stack gap={4}>
            <TextInput label="Your calendar link" value={url} readOnly onFocus={(e) => e.currentTarget.select()} />
            <Group gap="xs">
              <CopyButton value={url}>
                {({ copied, copy }) => (
                  <Button size="xs" variant="light" onClick={copy}>
                    {copied ? "Copied" : "Copy link"}
                  </Button>
                )}
              </CopyButton>
              <Text size="xs" c="dimmed">
                Shown once. Make a new link if you lose it.
              </Text>
            </Group>
          </Stack>
        )}
        {!url && status.isSuccess && (
          <Text size="sm">{enabled ? "A calendar link is on." : "No calendar link yet."}</Text>
        )}
        <Group justify="flex-end">
          {enabled && (
            <Button
              variant="subtle"
              color="red"
              loading={turnOff.isPending}
              onClick={() =>
                run(async () => {
                  await turnOff.mutateAsync();
                  setUrl(null);
                })
              }
            >
              Turn off
            </Button>
          )}
          <Button
            loading={create.isPending}
            disabled={status.isError}
            onClick={() =>
              run(async () => {
                const made = await create.mutateAsync();
                // The API may live on another address (VITE_API_BASE_URL): the
                // calendar app must call the API, not the web app.
                setUrl(`${apiBaseUrl()}${made.path}`);
              })
            }
          >
            {enabled ? "Make a new link" : "Make a link"}
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}
