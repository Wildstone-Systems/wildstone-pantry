import { Card, Group, Text } from "@mantine/core";
import type { LucideIcon } from "lucide-react";
import type { CSSProperties, ReactNode } from "react";

/**
 * Shared Organic building blocks for the kiosk Dashboard + Inventory screens.
 */

export function dayTagStyle(daysUntil: number | null): CSSProperties {
  if (daysUntil === null) {
    return { background: "var(--pantry-neutral-200)", color: "var(--pantry-neutral-700)" };
  }
  if (daysUntil <= 1) {
    return { background: "var(--pantry-accent-200)", color: "var(--pantry-accent-800)" };
  }
  if (daysUntil <= 3) {
    return { background: "var(--pantry-accent-100)", color: "var(--pantry-accent-700)" };
  }
  return { background: "var(--pantry-neutral-200)", color: "var(--pantry-neutral-700)" };
}

export function dayTagLabel(daysUntil: number | null): string {
  if (daysUntil === null) return "—";
  if (daysUntil < 0) return `expired ${-daysUntil}d ago`;
  if (daysUntil === 0) return "Today";
  if (daysUntil === 1) return "Tomorrow";
  return `${daysUntil} days`;
}

export function DayTag({ days, label }: { days: number | null; label?: string }) {
  return (
    <span className="pantry-daytag" style={dayTagStyle(days)}>
      {label ?? dayTagLabel(days)}
    </span>
  );
}

/** Surface panel with an icon + title header (the "mk-panel" pattern). */
export function Panel({
  title,
  icon: Icon,
  iconColor = "var(--pantry-accent)",
  children,
  style,
}: {
  title: string;
  icon: LucideIcon;
  iconColor?: string;
  children: ReactNode;
  style?: CSSProperties;
}) {
  return (
    <Card padding="lg" style={style}>
      <Group gap={9} mb={6} wrap="nowrap">
        <Icon size={19} color={iconColor} strokeWidth={2.6} />
        <Text style={{ fontFamily: "var(--mantine-heading-font-family)", fontSize: 17 }}>{title}</Text>
      </Group>
      {children}
    </Card>
  );
}

/** A single label/value row with a bottom divider (the "mk-row" pattern). */
export function PanelRow({ left, right }: { left: ReactNode; right?: ReactNode }) {
  return (
    <Group
      justify="space-between"
      wrap="nowrap"
      style={{ padding: "9px 0", borderBottom: "1px solid var(--pantry-divider)" }}
    >
      <Text size="sm">{left}</Text>
      {right}
    </Group>
  );
}
