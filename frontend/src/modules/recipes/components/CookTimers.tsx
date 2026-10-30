import { ActionIcon, Badge, Button, Card, Group, NumberInput, Stack, Text, TextInput } from "@mantine/core";
import { useEffect, useRef, useState } from "react";

import type { CookTimer } from "../hooks/cook-session-hooks";

/** "4:05", "1:02:30" — what a kitchen timer shows. */
export function clock(seconds: number): string {
  const s = Math.max(0, Math.round(seconds));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = String(s % 60).padStart(2, "0");
  return h > 0 ? `${h}:${String(m).padStart(2, "0")}:${sec}` : `${m}:${sec}`;
}

/** Ticks once a second while anything is counting. */
function useNow(active: boolean): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!active) return;
    const t = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(t);
  }, [active]);
  return now;
}

/** A short two-note chime, repeated while any timer rings. Web Audio, so there
 * is no sound file to ship; a browser that blocks audio until a tap still
 * shows the ringing card. */
function useChime(ringing: boolean) {
  const ctx = useRef<AudioContext | null>(null);
  useEffect(() => {
    if (!ringing) return;
    const play = () => {
      try {
        const Ctor = window.AudioContext ?? (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
        if (!Ctor) return;
        ctx.current ??= new Ctor();
        const ac = ctx.current;
        [0, 0.25].forEach((offset, i) => {
          const osc = ac.createOscillator();
          const gain = ac.createGain();
          osc.frequency.value = i === 0 ? 880 : 1175;
          gain.gain.setValueAtTime(0.25, ac.currentTime + offset);
          gain.gain.exponentialRampToValueAtTime(0.001, ac.currentTime + offset + 0.35);
          osc.connect(gain).connect(ac.destination);
          osc.start(ac.currentTime + offset);
          osc.stop(ac.currentTime + offset + 0.4);
        });
        navigator.vibrate?.([200, 100, 200]);
      } catch {
        /* no audio here: the card still rings */
      }
    };
    play();
    const t = window.setInterval(play, 2500);
    return () => window.clearInterval(t);
  }, [ringing]);
}

type Props = {
  timers: CookTimer[];
  /** Timers the current step's words suggest ("simmer 20 minutes"). */
  suggestions: { seconds: number; label: string }[];
  stepNumber: number;
  onStart: (seconds: number, label: string | null) => void;
  onStop: (timerId: string) => void;
  pending?: boolean;
};

export function CookTimers({ timers, suggestions, stepNumber, onStart, onStop, pending }: Props) {
  const now = useNow(timers.length > 0);
  const left = (t: CookTimer) => (new Date(t.ends_at).getTime() - now) / 1000;
  const anyRinging = timers.some((t) => left(t) <= 0);
  useChime(anyRinging);

  const [adding, setAdding] = useState(false);
  const [minutes, setMinutes] = useState<number | string>(5);
  const [label, setLabel] = useState("");
  const running = new Set(timers.filter((t) => t.step_number === stepNumber).map((t) => t.duration_seconds));

  return (
    <Card withBorder padding="sm">
      <Group justify="space-between" mb={timers.length || suggestions.length || adding ? "xs" : 0}>
        <Text size="xs" c="dimmed">
          Timers
        </Text>
        {!adding && (
          <Button size="compact-xs" variant="subtle" onClick={() => setAdding(true)}>
            + Timer
          </Button>
        )}
      </Group>
      <Stack gap="xs">
        {timers.map((t) => {
          const rem = left(t);
          const ringing = rem <= 0;
          return (
            <Group
              key={t.id}
              justify="space-between"
              wrap="nowrap"
              p={6}
              style={{
                borderRadius: 8,
                background: ringing ? "var(--mantine-color-red-light)" : undefined,
              }}
              data-testid="cook-timer"
            >
              <Group gap="xs" wrap="nowrap" style={{ minWidth: 0 }}>
                <Text fw={700} ff="monospace" size="xl" c={ringing ? "red" : undefined} aria-live="polite">
                  {ringing ? "0:00" : clock(rem)}
                </Text>
                <Text size="sm" truncate>
                  {t.label}
                </Text>
                {t.step_number != null && (
                  <Badge size="xs" variant="light" color="gray">
                    step {t.step_number}
                  </Badge>
                )}
              </Group>
              <Button
                size="xs"
                color={ringing ? "red" : "gray"}
                variant={ringing ? "filled" : "subtle"}
                onClick={() => onStop(t.id)}
              >
                {ringing ? "Dismiss" : "Cancel"}
              </Button>
            </Group>
          );
        })}
        {suggestions.filter((s) => !running.has(s.seconds)).length > 0 && (
          <Group gap="xs">
            {suggestions
              .filter((s) => !running.has(s.seconds))
              .map((s) => (
                <Button
                  key={s.seconds}
                  size="xs"
                  variant="light"
                  loading={pending}
                  onClick={() => onStart(s.seconds, `Step ${stepNumber}: ${s.label}`)}
                >
                  Start {clock(s.seconds)} timer
                </Button>
              ))}
          </Group>
        )}
        {adding && (
          <Group gap="xs" align="flex-end" wrap="nowrap">
            <NumberInput
              label="Minutes"
              aria-label="Timer minutes"
              min={0.5}
              max={1440}
              step={1}
              decimalScale={1}
              value={minutes}
              onChange={setMinutes}
              w={96}
            />
            <TextInput
              label="Name"
              aria-label="Timer name"
              placeholder="e.g. Pasta"
              value={label}
              onChange={(e) => setLabel(e.currentTarget.value)}
              style={{ flex: 1 }}
            />
            <Button
              disabled={!Number(minutes) || Number(minutes) <= 0}
              loading={pending}
              onClick={() => {
                onStart(Math.round(Number(minutes) * 60), label.trim() || null);
                setAdding(false);
                setLabel("");
              }}
            >
              Start
            </Button>
            <ActionIcon variant="subtle" color="gray" aria-label="Close" onClick={() => setAdding(false)} mb={6}>
              ×
            </ActionIcon>
          </Group>
        )}
      </Stack>
    </Card>
  );
}
