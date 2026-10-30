import { useQuery } from "@tanstack/react-query";

import { api } from "./client";

/**
 * Deployment entitlement (ADR 0012). Free mode grants "core" only; premium,
 * infrastructure-backed features arrive in connected mode. Read up front to
 * decide which premium surfaces to show — see `<FeatureGate>`.
 */
export type Entitlement = {
  mode: "free" | "connected";
  tier: "free" | "premium";
  features: string[];
  all_features: string[];
  valid: boolean;
  source: string;
  reason: string;
  integrations_brokered: boolean;
};

export const FEATURE = {
  core: "core",
  storeIntegrations: "store_integrations",
  cloudAi: "cloud_ai",
  cloudBackup: "cloud_backup",
  remoteRelay: "remote_relay",
} as const;

export function useEntitlement() {
  return useQuery({
    queryKey: ["entitlement"],
    queryFn: async () => {
      const res = await api.get<Entitlement>("/api/entitlement");
      return res.data;
    },
    // Mode/tier changes rarely; don't refetch on every mount.
    staleTime: 60_000,
  });
}

/** True once the entitlement is known AND grants `feature`. False while loading. */
export function useFeature(feature: string): boolean {
  const { data } = useEntitlement();
  return data?.features.includes(feature) ?? false;
}
