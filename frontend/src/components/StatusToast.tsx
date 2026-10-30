import { Paper, Text } from "@mantine/core";
import { useEffect, useRef, useState } from "react";

import { useAppState } from "../state/AppStateProvider";

/**
 * Lightweight replacement for the old status footer: surfaces `status` updates
 * (mutation results, validation errors) as a transient pill in the corner.
 * Kept dependency-free — no notifications package is installed.
 */
export function StatusToast() {
  const { status } = useAppState();
  const [visible, setVisible] = useState(false);
  const firstRender = useRef(true);

  useEffect(() => {
    // Skip the first render and the idle "Ready" sentinel; only toast real
    // updates (mutation results, validation/errors).
    if (firstRender.current) {
      firstRender.current = false;
      return;
    }
    if (status === "Ready") {
      setVisible(false);
      return;
    }
    setVisible(true);
    const timer = window.setTimeout(() => setVisible(false), 3200);
    return () => window.clearTimeout(timer);
  }, [status]);

  if (!visible) return null;

  return (
    <Paper
      role="status"
      shadow="md"
      px="md"
      py="xs"
      style={{
        position: "fixed",
        left: "50%",
        transform: "translateX(-50%)",
        bottom: 24,
        zIndex: 300,
        background: "var(--pantry-surface)",
        border: "1px solid var(--pantry-divider)",
        maxWidth: 420,
      }}
    >
      <Text size="sm" lineClamp={2}>
        {status}
      </Text>
    </Paper>
  );
}
