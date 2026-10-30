import { Alert, Button, Group, Modal, Stack, TextInput, Textarea } from "@mantine/core";
import { zodResolver } from "@hookform/resolvers/zod";
import { useEffect, useState } from "react";
import { Controller, useForm } from "react-hook-form";

import { getApiErrorMessage } from "@/api/errors";

import { locationFormSchema, type LocationFormValues } from "../forms/location-schema";
import { useUpdateLocation, type Location } from "../hooks/catalog-hooks";

type Props = {
  location: Location | null;
  onClose: () => void;
  onSaved?: (location: Location) => void;
};

/** Modal form to edit an existing inventory location (name, description). */
export function EditLocationModal({ location, onClose, onSaved }: Props) {
  const updateLocation = useUpdateLocation();
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

  useEffect(() => {
    if (location) {
      setError(null);
      reset({ name: location.name, description: location.description ?? "" });
    }
  }, [location, reset]);

  const onSubmit = handleSubmit(async (values) => {
    if (!location) return;
    setError(null);
    try {
      const saved = await updateLocation.mutateAsync({
        id: location.id,
        name: values.name,
        description: values.description?.trim() ? values.description.trim() : null,
      });
      onSaved?.(saved);
      onClose();
    } catch (err) {
      setError(getApiErrorMessage(err, "Could not save location"));
    }
  });

  return (
    <Modal opened={location !== null} onClose={onClose} title={`Edit location — ${location?.name ?? ""}`}>
      <Stack component="form" onSubmit={onSubmit} gap="sm">
        {error ? (
          <Alert color="red" title="Could not save location">
            {error}
          </Alert>
        ) : null}

        <Controller
          name="name"
          control={control}
          render={({ field }) => <TextInput label="Location name" required error={errors.name?.message} {...field} />}
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

        <Group justify="flex-end">
          <Button variant="default" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" loading={updateLocation.isPending} disabled={!isValid}>
            Save changes
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}
