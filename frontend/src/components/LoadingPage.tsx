import { Center, Loader, Stack, Text, Title } from "@mantine/core";

export function LoadingPage({ title, message }: { title: string; message: string }) {
  return (
    <Center style={{ minHeight: "100vh" }} p="md">
      <Stack gap="xs" align="center">
        <Title order={2}>{title}</Title>
        <GroupLoader message={message} />
      </Stack>
    </Center>
  );
}

function GroupLoader({ message }: { message: string }) {
  return (
    <Stack gap={6} align="center">
      <Loader size="sm" />
      <Text size="sm" c="dimmed">
        {message}
      </Text>
    </Stack>
  );
}
