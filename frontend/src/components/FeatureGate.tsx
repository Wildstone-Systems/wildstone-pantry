import { Alert, Text } from "@mantine/core";
import type { ReactNode } from "react";

import { useEntitlement } from "@/api/entitlement";

type Props = {
  feature: string;
  children: ReactNode;
  /** Shown when the instance is not entitled. Omit to render nothing. */
  fallback?: ReactNode;
  /** Shown while entitlement is still loading. Defaults to nothing. */
  loading?: ReactNode;
};

/**
 * Renders `children` only when this instance is entitled to `feature` (ADR 0012).
 *
 * In free mode, premium features fall back to an "upgrade" hint (or nothing).
 * The everyday app is `core`, always granted, so wrapping ordinary UI in this is
 * a no-op — it exists for the infrastructure-backed premium surfaces.
 */
export function FeatureGate({ feature, children, fallback, loading }: Props) {
  const { data, isLoading } = useEntitlement();

  if (isLoading) return <>{loading ?? null}</>;
  if (data?.features.includes(feature)) return <>{children}</>;

  if (fallback !== undefined) return <>{fallback}</>;
  return (
    <Alert color="grape" variant="light" title="Premium feature">
      <Text size="sm">This feature requires PantryOS Connect. This instance is running in free mode.</Text>
    </Alert>
  );
}
