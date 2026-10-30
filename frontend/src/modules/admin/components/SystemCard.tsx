import { Alert, Anchor, Badge, Button, Card, Group, Stack, Table, Text, Title } from "@mantine/core";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/api/client";
import { getApiErrorMessage } from "@/api/errors";

type VersionInfo = {
  current: string;
  available: { version: string; urgency: string; notes_url: string | null } | null;
};

type BackupEntry = {
  name: string;
  created_at: string;
  app_version: string;
  db: string;
  size_bytes: number;
};

type BackupList = { enabled: boolean; backups: BackupEntry[] };

function formatSize(bytes: number): string {
  if (bytes >= 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  if (bytes >= 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${bytes} B`;
}

/** Version + backups management (ADR 0018 §6). Admin-only surface. */
export function SystemCard() {
  const queryClient = useQueryClient();

  const { data: version } = useQuery({
    queryKey: ["systemVersion"],
    queryFn: async () => (await api.get<VersionInfo>("/api/system/version")).data,
    staleTime: 60_000,
  });

  const { data: backups, error: backupsError } = useQuery({
    queryKey: ["systemBackups"],
    queryFn: async () => (await api.get<BackupList>("/api/system/backups")).data,
  });

  const runBackup = useMutation({
    mutationFn: async () => (await api.post("/api/system/backups")).data,
    onSettled: () => queryClient.invalidateQueries({ queryKey: ["systemBackups"] }),
  });

  const download = async (name: string) => {
    // The download needs the bearer header, so fetch as a blob rather than
    // using a plain <a href> (which would carry no Authorization).
    const res = await api.get(`/api/system/backups/${name}/download`, { responseType: "blob" });
    const url = URL.createObjectURL(res.data as Blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${name}.dump`;
    a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <Card withBorder padding="lg">
      <Stack gap="sm">
        <Group justify="space-between">
          <Title order={4}>System</Title>
          {version && <Badge variant="light">v{version.current}</Badge>}
        </Group>

        {version?.available && (
          <Alert color="blue" variant="light" title={`Update available: v${version.available.version}`}>
            <Text size="sm">
              {version.available.urgency === "critical" ? "Critical update — apply promptly. " : ""}
              Installs running the update supervisor apply it automatically after the defer window.{" "}
              {version.available.notes_url && (
                <Anchor href={version.available.notes_url} target="_blank" rel="noreferrer">
                  Release notes
                </Anchor>
              )}
            </Text>
          </Alert>
        )}

        <Group justify="space-between" align="baseline">
          <Title order={5}>Backups</Title>
          <Button
            size="xs"
            variant="light"
            loading={runBackup.isPending}
            disabled={backups?.enabled === false}
            onClick={() => runBackup.mutate()}
          >
            Back up now
          </Button>
        </Group>

        {backups?.enabled === false && (
          <Text size="sm" c="dimmed">
            Backups are disabled — set <b>BACKUP_DIR</b>. The product stack enables nightly backups
            automatically (kept: 7 daily + 4 weekly).
          </Text>
        )}
        {Boolean(backupsError) && (
          <Text size="sm" c="dimmed">
            {getApiErrorMessage(backupsError, "Couldn't load backups.")}
          </Text>
        )}
        {runBackup.error != null && (
          <Alert color="red" variant="light">
            {getApiErrorMessage(runBackup.error, "Backup failed.")}
          </Alert>
        )}

        {backups && backups.backups.length > 0 && (
          <Table>
            <Table.Tbody>
              {backups.backups.slice(0, 5).map((b) => (
                <Table.Tr key={b.name}>
                  <Table.Td>{new Date(b.created_at).toLocaleString()}</Table.Td>
                  <Table.Td>v{b.app_version}</Table.Td>
                  <Table.Td>{formatSize(b.size_bytes)}</Table.Td>
                  <Table.Td>
                    <Anchor component="button" type="button" onClick={() => download(b.name)}>
                      Download
                    </Anchor>
                  </Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        )}
        {backups?.enabled && backups.backups.length === 0 && (
          <Text size="sm" c="dimmed">
            No backups yet — the nightly scheduler takes the first one within the hour, or run one now.
          </Text>
        )}
      </Stack>
    </Card>
  );
}
