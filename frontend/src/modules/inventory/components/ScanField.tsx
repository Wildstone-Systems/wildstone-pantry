import { Alert, Button, Group, Stack, Text, TextInput } from "@mantine/core";
import { BrowserMultiFormatReader } from "@zxing/browser";
import type { IScannerControls } from "@zxing/browser";
import { Camera, CameraOff } from "lucide-react";
import { forwardRef, useCallback, useEffect, useImperativeHandle, useRef, useState } from "react";

import { useItemSearch } from "@/modules/catalog";

export type ScanFieldHandle = { focus: () => void };

type Props = {
  value: string;
  onChange: (next: string) => void;
  /** Barcode typed or wedge-scanned and submitted with Enter. */
  onSubmit: (barcode: string) => void;
  /** An item picked by name instead — no barcode to hand. */
  onPickItem: (itemId: string) => void;
  /** "out" restricts name search to what's actually on hand. */
  direction: "in" | "out";
  busy?: boolean;
};

/**
 * The scan field: a hardware-wedge target, a name search for when no scanner is
 * handy, and an opt-in camera for phones.
 *
 * Focus discipline is the whole point and the reason this is one component
 * rather than three (ADR 0023 D12). A wedge scanner types into whatever holds
 * focus, so focus must return here after every action; the camera necessarily
 * takes it, so it hands focus back the moment it stops.
 */
export const ScanField = forwardRef<ScanFieldHandle, Props>(function ScanField(
  { value, onChange, onSubmit, onPickItem, direction, busy },
  ref,
) {
  const inputRef = useRef<HTMLInputElement>(null);
  const videoRef = useRef<HTMLVideoElement>(null);
  const controlsRef = useRef<IScannerControls | null>(null);
  const [cameraActive, setCameraActive] = useState(false);
  const [cameraError, setCameraError] = useState<string | null>(null);

  const focus = useCallback(() => {
    inputRef.current?.focus();
    inputRef.current?.select();
  }, []);

  useImperativeHandle(ref, () => ({ focus }), [focus]);

  // Barcodes are all digits and match nothing by name, so this stays an empty
  // list during a normal wedge scan rather than getting in the way.
  const search = useItemSearch(value, { inStockOnly: direction === "out" });
  const suggestions = search.data ?? [];
  const searching = value.trim().length >= 2;

  const stopCamera = useCallback(() => {
    controlsRef.current?.stop();
    controlsRef.current = null;
    setCameraActive(false);
    focus();
  }, [focus]);

  const startCamera = useCallback(async () => {
    setCameraError(null);
    try {
      const reader = new BrowserMultiFormatReader();
      const video = videoRef.current;
      if (!video) return;
      setCameraActive(true);
      const controls = await reader.decodeFromVideoDevice(undefined, video, (result, error) => {
        if (result) {
          controls.stop();
          controlsRef.current = null;
          setCameraActive(false);
          onSubmit(result.getText());
          focus();
        }
        // "no barcode in frame yet" is the normal case, not a failure.
        if (error && error.name !== "NotFoundException") console.warn("Scan error:", error);
      });
      controlsRef.current = controls;
    } catch (err) {
      setCameraError(err instanceof Error ? err.message : "Camera access failed");
      setCameraActive(false);
    }
  }, [focus, onSubmit]);

  useEffect(() => () => controlsRef.current?.stop(), []);

  return (
    <Stack gap="xs">
      <Group align="flex-end" gap="xs" wrap="nowrap">
        <TextInput
          ref={inputRef}
          label="Barcode or item name"
          placeholder="Scan, or type a name then Enter"
          value={value}
          autoFocus
          onChange={(e) => onChange(e.currentTarget.value)}
          onKeyDown={(e) => {
            if (e.key !== "Enter") return;
            const code = value.trim();
            if (code) onSubmit(code);
          }}
          style={{ flex: 1, minWidth: 160 }}
        />
        <Button
          variant="subtle"
          size="sm"
          onClick={cameraActive ? stopCamera : startCamera}
          leftSection={cameraActive ? <CameraOff size={16} /> : <Camera size={16} />}
        >
          {cameraActive ? "Stop" : "Camera"}
        </Button>
      </Group>

      {/* The element stays mounted so startCamera's ref is always valid; it
          collapses to nothing until the camera is actually running. */}
      <div
        style={{
          maxHeight: cameraActive ? 220 : 0,
          overflow: "hidden",
          borderRadius: 8,
          background: cameraActive ? "#111" : "transparent",
          transition: "max-height 150ms ease",
        }}
      >
        <video ref={videoRef} style={{ width: "100%", display: cameraActive ? "block" : "none" }} />
      </div>

      {cameraError ? (
        <Alert color="red" variant="light" title="Camera unavailable">
          {cameraError}
        </Alert>
      ) : null}

      {searching && (search.isFetching || suggestions.length > 0) ? (
        <Group gap="xs">
          {search.isFetching && suggestions.length === 0 ? (
            <Text size="xs" c="dimmed">
              Searching…
            </Text>
          ) : (
            suggestions.map((item) => (
              <Button key={item.id} size="xs" variant="light" loading={busy} onClick={() => onPickItem(item.id)}>
                {item.name}
                {item.brand ? ` — ${item.brand}` : ""}
              </Button>
            ))
          )}
        </Group>
      ) : null}

      {searching && !search.isFetching && suggestions.length === 0 ? (
        <Text size="xs" c="dimmed">
          {direction === "out" ? "Nothing on hand matches that name." : "No catalog items match that name."}
        </Text>
      ) : null}
    </Stack>
  );
});
