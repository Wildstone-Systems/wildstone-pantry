import { Affix, Button, Group, Modal, Select, Stack, Text, TextInput, Textarea } from "@mantine/core";
import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { Controller, useForm } from "react-hook-form";
import { Link, useLocation } from "react-router-dom";

import { api } from "../api/client";
import { getApiErrorMessage } from "../api/errors";
import { feedbackFormSchema, type FeedbackFormValues } from "../modules/feedback";
import { ROUTES } from "../routes/paths";
import { useAppState } from "../state/AppStateProvider";

const DEFAULTS: FeedbackFormValues = {
  type: "bug",
  area: "ui",
  summary: "",
  steps: "",
  expected: "",
  actual: "",
  notes: "",
};

export function FeedbackButton() {
  const location = useLocation();
  const { setStatus } = useAppState();

  const [opened, setOpened] = useState(false);

  const { control, handleSubmit, reset } = useForm<FeedbackFormValues>({
    resolver: zodResolver(feedbackFormSchema),
    mode: "onChange",
    defaultValues: DEFAULTS,
  });

  const route = useMemo(() => location.pathname + location.search, [location.pathname, location.search]);

  const mutation = useMutation({
    mutationFn: async (values: FeedbackFormValues) => {
      const meta = {
        route,
        userAgent: typeof navigator !== "undefined" ? navigator.userAgent : "unknown",
        language: typeof navigator !== "undefined" ? navigator.language : "unknown",
        platform: typeof navigator !== "undefined" ? (navigator as any).platform ?? "unknown" : "unknown",
        viewport:
          typeof window !== "undefined"
            ? { width: window.innerWidth, height: window.innerHeight, devicePixelRatio: window.devicePixelRatio }
            : null,
        theme: typeof document !== "undefined" ? document.documentElement.getAttribute("data-mantine-color-scheme") ?? "unknown" : "unknown",
        time: new Date().toISOString(),
      };

      const parts: string[] = [];
      parts.push(`Summary:\n${values.summary.trim()}`);
      if (values.steps?.trim()) parts.push(`Steps to reproduce:\n${values.steps.trim()}`);
      if (values.expected?.trim()) parts.push(`Expected:\n${values.expected.trim()}`);
      if (values.actual?.trim()) parts.push(`Actual:\n${values.actual.trim()}`);
      if (values.notes?.trim()) parts.push(`Notes:\n${values.notes.trim()}`);
      parts.push(`---\nMeta: ${JSON.stringify(meta)}`);

      await api.post("/api/feedback", {
        category: `${values.type}-${values.area}`,
        route: meta.route,
        message: parts.join("\n\n"),
      });
    },
    onSuccess: () => {
      reset(DEFAULTS);
      setOpened(false);
      setStatus("Feedback submitted");
    },
    onError: (error) => {
      setStatus(getApiErrorMessage(error, error instanceof Error ? error.message : "Submit feedback failed"));
    },
  });

  const busy = mutation.isPending;
  const onSubmit = handleSubmit((values) => mutation.mutate(values));

  return (
    <>
      <Affix position={{ bottom: 20, right: 20 }}>
        <Button onClick={() => setOpened(true)} aria-label="Open feedback form">
          Feedback
        </Button>
      </Affix>

      <Modal opened={opened} onClose={() => (busy ? null : setOpened(false))} title="Send Feedback" size="lg">
        <Stack component="form" onSubmit={onSubmit} gap="sm">
          <Text size="sm" c="dimmed">
            Route: <code>{route}</code>
          </Text>

          <Group grow>
            <Controller
              name="type"
              control={control}
              render={({ field }) => (
                <Select
                  label="Type"
                  data={[
                    { value: "bug", label: "Bug" },
                    { value: "feature", label: "Feature" },
                  ]}
                  disabled={busy}
                  allowDeselect={false}
                  {...field}
                  onChange={(value) => field.onChange(value || "bug")}
                />
              )}
            />

            <Controller
              name="area"
              control={control}
              render={({ field }) => (
                <Select
                  label="Area"
                  data={[
                    { value: "ui", label: "UI" },
                    { value: "workflow", label: "Workflow" },
                    { value: "data", label: "Data" },
                    { value: "ocr", label: "OCR" },
                    { value: "performance", label: "Performance" },
                    { value: "licensing", label: "Licensing" },
                    { value: "other", label: "Other" },
                  ]}
                  disabled={busy}
                  allowDeselect={false}
                  {...field}
                  onChange={(value) => field.onChange(value || "ui")}
                />
              )}
            />
          </Group>

          <Controller
            name="summary"
            control={control}
            render={({ field, fieldState }) => (
              <TextInput
                label="Summary"
                placeholder="Short description of the problem or request"
                disabled={busy}
                withAsterisk
                autoFocus
                error={fieldState.error?.message}
                {...field}
              />
            )}
          />

          <Controller
            name="steps"
            control={control}
            render={({ field }) => (
              <Textarea
                label="Steps to Reproduce (optional)"
                minRows={4}
                disabled={busy}
                placeholder={"1.\n2.\n3."}
                {...field}
                value={field.value ?? ""}
              />
            )}
          />

          <Controller
            name="expected"
            control={control}
            render={({ field }) => (
              <Textarea label="Expected (optional)" minRows={3} disabled={busy} {...field} value={field.value ?? ""} />
            )}
          />

          <Controller
            name="actual"
            control={control}
            render={({ field }) => (
              <Textarea label="Actual (optional)" minRows={3} disabled={busy} {...field} value={field.value ?? ""} />
            )}
          />

          <Controller
            name="notes"
            control={control}
            render={({ field }) => (
              <Textarea label="Notes (optional)" minRows={3} disabled={busy} {...field} value={field.value ?? ""} />
            )}
          />

          <Group justify="space-between">
            <Button component={Link} variant="default" to={ROUTES.feedback} onClick={() => setOpened(false)}>
              Feedback Inbox
            </Button>

            <Button type="submit" disabled={busy}>
              {busy ? "Submitting..." : "Submit"}
            </Button>
          </Group>
        </Stack>
      </Modal>
    </>
  );
}
