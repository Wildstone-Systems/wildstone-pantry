import { Select } from "@mantine/core";
import { MapPin } from "lucide-react";

import { useLocations } from "../api/hooks";
import { useAppState } from "../state/AppStateProvider";

// A null selectedLocationId means "All locations". The Select needs a concrete
// string value, so we map null <-> this sentinel option.
export const ALL_LOCATIONS = "__all__";

/**
 * Location selector styled as an Organic pill. On the kiosk screens the top
 * header is gone, so location selection lives in the page header / console
 * instead. Reads and writes the shared `selectedLocationId`; the first option
 * is "All locations" (aggregates every location).
 */
export function LocationPill({ width = 210 }: { width?: number }) {
  const { data: locations } = useLocations();
  const { selectedLocationId, setSelectedLocationId } = useAppState();

  const data = [
    { value: ALL_LOCATIONS, label: "All locations" },
    ...(locations ?? []).map((loc) => ({ value: loc.id, label: loc.name })),
  ];

  return (
    <Select
      aria-label="Selected location"
      leftSection={<MapPin size={15} color="var(--pantry-accent)" strokeWidth={2.6} />}
      data={data}
      value={selectedLocationId ?? ALL_LOCATIONS}
      onChange={(value) => setSelectedLocationId(value && value !== ALL_LOCATIONS ? value : null)}
      allowDeselect={false}
      w={width}
      comboboxProps={{ radius: "lg" }}
      styles={{ input: { background: "var(--pantry-surface)" } }}
    />
  );
}
