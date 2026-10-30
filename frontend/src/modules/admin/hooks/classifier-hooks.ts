import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/api/client";

export type ClassifierConfig = {
  enabled: boolean;
  provider: "ollama" | "openrouter";
  model: string;
  execution_location: "cloud_direct" | "cloud_via_local" | "local";
  endpoint: string;
  cloud_opt_in: boolean;
  runtime_review_enabled?: boolean;
  review_confidence?: number;
};

export type ClassifierConfigView = {
  config: ClassifierConfig;
  api_key_set: boolean;
  secrets_available: boolean;
  configured: boolean;
  valid: boolean;
  detail: string;
};

export type ClassifierEvaluation = {
  id: string;
  status: "ok" | "abstain" | "unavailable" | "invalid";
  reason_code: string;
  answers: Record<
    string,
    {
      type: "choice";
      choice: string;
      probabilities: Record<string, number>;
      confidence: number;
    }
  >;
  provider: string;
  model: string;
  resolved_model?: string | null;
  usage?: { input_tokens?: number | null; output_tokens?: number | null; cost_usd?: number | null } | null;
  execution_location: string;
  contract_version: string;
  policy_version: string;
  source_version: string;
  provider_contract_version: string;
  created_at: string;
  expires_at: string;
};

export type ClassifierEvaluationInput = {
  state: string;
  questions: Record<
    string,
    {
      type: "choice";
      instructions: string;
      criteria: Record<string, string>;
    }
  >;
  contract_version: "classifier.v1";
  policy_version: "shadow.v1";
  source_version: "manual.v1";
};

const configPath = "/api/ai/classifier/config";
const configKey = ["admin", "classifier", "config"];

export function useClassifierConfig() {
  return useQuery({
    queryKey: configKey,
    queryFn: async () => (await api.get<ClassifierConfigView>(configPath)).data,
  });
}

export function useSaveClassifierConfig() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: async (update: Partial<ClassifierConfig> & { api_key?: string }) =>
      (await api.put<ClassifierConfigView>(configPath, update)).data,
    onSuccess: (view) => client.setQueryData(configKey, view),
  });
}

export function useClearClassifierConfig() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: async () => (await api.delete<ClassifierConfigView>(configPath)).data,
    onSuccess: (view) => client.setQueryData(configKey, view),
  });
}

export function useTestClassifier() {
  return useMutation({
    mutationFn: async () => (await api.post<ClassifierEvaluation>("/api/ai/classifier/test", {})).data,
  });
}

export function useEvaluateClassifier() {
  return useMutation({
    mutationFn: async (input: ClassifierEvaluationInput) =>
      (await api.post<ClassifierEvaluation>("/api/ai/classifier/evaluations", input)).data,
  });
}
