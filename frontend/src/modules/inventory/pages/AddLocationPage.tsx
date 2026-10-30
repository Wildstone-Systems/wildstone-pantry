import { Alert, Button, Card, Group, Stack, Text, TextInput, Textarea, Title } from "@mantine/core";
import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Controller, useForm } from "react-hook-form";
import { Link } from "react-router-dom";

import { api } from "@/api/client";
import { getApiErrorMessage } from "@/api/errors";
import { useLocations, type Location } from "@/modules/catalog";
import { ROUTES } from "@/routes/paths";
import { useAppState } from "@/state/AppStateProvider";

import { locationFormSchema, type LocationFormValues } from "../forms/location-schema";

export function AddLocationPage() {
  const queryClient = useQueryClient();
  const { setStatus } = useAppState();

  // Ensure locations are loaded so the user sees new locations immediately after create.
  useLocations();

  const [error, setError] = useState<string | null>(null);

  const {
    control,
    handleSubmit,
    reset,
    formState: { errors, isValid },
  } = useForm<LocationFormValues>({
    resolver: zodResolver(locationFormSchema),
    mode: "onChange",
    defaultValues: { name: "", description: "" },
  });

  const mutation = useMutation({
    mutationFn: async (values: LocationFormValues) => {
      const res = await api.post<Location>("/api/locations", {
        name: values.name,
        description: values.description?.trim() ? values.description.trim() : null,
      });
      return res.data;
    },
    onSuccess: async (created) => {
      setError(null);
      reset({ name: "", description: "" });
      setStatus(`Created location ${created.name}`);
      await queryClient.invalidateQueries({ queryKey: ["locations"] });
    },
    onError: (err) => {
      const message = getApiErrorMessage(err, "Create location failed");
      setError(message);
      setStatus(message);
    },
  });

  const onSubmit = handleSubmit((values) => {
    setError(null);
    mutation.mutate(values);
  });

  return (
    <Stack>
      <Group justify="space-between" align="baseline">
        <Title order={2}>Add Location</Title>
        <Button component={Link} to={ROUTES.catalog} variant="default">
          Back to Catalog
        </Button>
      </Group>

      <Card withBorder>
        <Stack component="form" onSubmit={onSubmit} gap="sm">
          {error ? (
            <Alert color="red" title="Could not create location">
              {error}
            </Alert>
          ) : null}

          <Controller
            name="name"
            control={control}
            render={({ field }) => (
              <TextInput label="Location name" required error={errors.name?.message} {...field} />
            )}
          />
          <Controller
            name="description"
            control={control}
            render={({ field }) => (
              <Textarea
                label="Description (optional)"
                minRows={3}
                error={errors.description?.message}
                {...field}
                value={field.value ?? ""}
              />
            )}
          />

          <Group>
            <Button type="submit" loading={mutation.isPending} disabled={!isValid}>
              Create Location
            </Button>
            <Text size="sm" c="dimmed">
              Use locations to group inventory (pantry, freezer, garage, etc).
            </Text>
          </Group>
        </Stack>
      </Card>
    </Stack>
  );
}
