import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/api/client";

export type ConnectorConfigView = {
  key: string;
  values: Record<string, string>;
  secret_set: Record<string, boolean>;
  has_override: boolean;
  from_env_only: boolean;
  secrets_available: boolean;
};

export type ConnectorTestResult = { ok: boolean; detail: string };

const key = (connector: string) => ["admin", "integrations", connector] as const;

export function useConnectorConfig(connector: string) {
  return useQuery({
    queryKey: key(connector),
    queryFn: async () => {
      const res = await api.get<ConnectorConfigView>(`/api/admin/integrations/${connector}`);
      return res.data;
    },
  });
}

export function useSaveConnectorConfig(connector: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (updates: Record<string, string>) => {
      const res = await api.put<ConnectorConfigView>(`/api/admin/integrations/${connector}`, { updates });
      return res.data;
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: key(connector) });
      qc.invalidateQueries({ queryKey: ["purchase-connectors"] });
    },
  });
}

export function useClearConnectorConfig(connector: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async () => {
      const res = await api.delete<ConnectorConfigView>(`/api/admin/integrations/${connector}`);
      return res.data;
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: key(connector) });
      qc.invalidateQueries({ queryKey: ["purchase-connectors"] });
    },
  });
}

export function useTestConnectorConfig(connector: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async () => {
      const res = await api.post<ConnectorTestResult>(`/api/admin/integrations/${connector}/test`);
      return res.data;
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: [...key(connector), "deliveries"] });
    },
  });
}

export type WebhookDelivery = {
  id: string;
  event_type: string;
  status: string; // delivered | failed
  attempts: number;
  last_attempt_at: string | null;
  delivered_at: string | null;
  created_at: string;
};

export function useWebhookDeliveries(connector: string, limit = 5) {
  return useQuery({
    queryKey: [...key(connector), "deliveries", limit],
    queryFn: async () => {
      const res = await api.get<WebhookDelivery[]>(
        `/api/admin/integrations/${connector}/deliveries`,
        { params: { limit } },
      );
      return res.data;
    },
  });
}
