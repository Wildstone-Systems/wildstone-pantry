import { cleanup, fireEvent, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { renderWithProviders } from "@/test-utils";
import type { ClassifierConfigView, ClassifierEvaluation } from "../hooks/classifier-hooks";
import { ClassifierCard } from "./ClassifierCard";

const apiGet = vi.fn();
const apiPut = vi.fn();
const apiPost = vi.fn();
const apiDelete = vi.fn();
vi.mock("@/api/client", () => ({
  api: {
    get: (...args: unknown[]) => apiGet(...args),
    put: (...args: unknown[]) => apiPut(...args),
    post: (...args: unknown[]) => apiPost(...args),
    delete: (...args: unknown[]) => apiDelete(...args),
  },
}));

const initial: ClassifierConfigView = {
  config: {
    enabled: false,
    provider: "ollama",
    model: "clef",
    execution_location: "cloud_direct",
    endpoint: "https://ollama.com",
    cloud_opt_in: false,
    runtime_review_enabled: false,
    review_confidence: 0.9,
  },
  api_key_set: false,
  secrets_available: true,
  configured: false,
  valid: true,
  detail: "Disabled",
};
const success: ClassifierEvaluation = {
  id: "result-1",
  status: "ok",
  reason_code: "classified",
  answers: { q_1: { type: "choice", choice: "c_1", probabilities: { c_1: 0.9, c_2: 0.1 }, confidence: 0.5 } },
  provider: "ollama",
  model: "clef",
  execution_location: "cloud_direct",
  contract_version: "classifier.v1",
  policy_version: "shadow.v1",
  source_version: "manual.v1",
  provider_contract_version: "clef.v1",
  created_at: "2026-10-25T00:00:00Z",
  expires_at: "2026-10-26T00:00:00Z",
};
let view: ClassifierConfigView;

async function openCard() {
  renderWithProviders(<ClassifierCard />);
  const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: "Expand" }));
  await waitFor(() => expect(screen.getByRole("button", { name: "Disconnect classifier" })).toBeEnabled());
  return user;
}

function connected(enabled = false) {
  view = {
    ...initial,
    config: { ...initial.config, cloud_opt_in: true, enabled },
    api_key_set: true,
    configured: true,
  };
}

describe("Classifier settings", () => {
  beforeEach(() => {
    window.localStorage.clear();
    vi.clearAllMocks();
    view = structuredClone(initial);
    apiGet.mockImplementation(async () => ({ data: view }));
    apiPut.mockImplementation(async (_url, update) => {
      const sameIdentity = ["provider", "endpoint", "model", "execution_location"].every(
        (key) => update[key] === view.config[key as keyof typeof view.config]
      );
      const keySet = Boolean(update.api_key) || (sameIdentity && view.api_key_set);
      view = {
        ...view,
        config: { ...view.config, ...Object.fromEntries(Object.entries(update).filter(([key]) => key !== "api_key")) },
        api_key_set: keySet,
        configured: update.execution_location !== "cloud_direct" || keySet,
      };
      return { data: view };
    });
    apiDelete.mockResolvedValue({ data: initial });
    apiPost.mockResolvedValue({ data: success });
  });
  afterEach(cleanup);

  it("loads only dedicated settings, defaults disabled, and never invokes inference automatically", async () => {
    await openCard();
    expect(apiGet).toHaveBeenCalledWith("/api/ai/classifier/config");
    expect(apiGet.mock.calls.every(([url]) => url === "/api/ai/classifier/config")).toBe(true);
    expect(screen.getByLabelText("Enable classifier shadow evaluations")).not.toBeChecked();
    expect(screen.getByLabelText("Classifier provider")).toHaveValue("ollama");
    expect(screen.getByRole("button", { name: "Run synthetic test" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Evaluate shadow example" })).toBeDisabled();
    expect(apiPost).not.toHaveBeenCalled();
  });

  it("saves a new credential once and clears it from the input", async () => {
    const user = await openCard();
    await user.type(screen.getByLabelText("Classifier API key"), "private-test-key");
    await user.click(screen.getByRole("button", { name: "Save classifier settings" }));
    await screen.findByText("Classifier settings saved.");
    expect(apiPut).toHaveBeenCalledWith("/api/ai/classifier/config", {
      ...initial.config,
      api_key: "private-test-key",
    });
    expect(screen.getByLabelText("Classifier API key")).toHaveValue("");
    expect(apiPost).not.toHaveBeenCalled();
  });

  it("shows saved-key status without hydrating a key and omits blank keys on save", async () => {
    connected();
    const user = await openCard();
    expect(screen.getByLabelText("Classifier API key")).toHaveValue("");
    expect(screen.getByPlaceholderText(/Key saved/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Save classifier settings" }));
    await screen.findByText("Classifier settings saved.");
    expect(apiPut.mock.calls[0][1]).not.toHaveProperty("api_key");
  });

  it("allows an explicit synthetic test while disabled but forbids ordinary evaluations", async () => {
    connected(false);
    const user = await openCard();
    expect(screen.getByRole("button", { name: "Evaluate shadow example" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "Run synthetic test" }));
    expect(apiPost).toHaveBeenCalledWith("/api/ai/classifier/test", {});
    expect(await screen.findByText("Decision: Food product")).toBeInTheDocument();
    expect(screen.getByText("Food product: 90.0%")).toBeInTheDocument();
    expect(screen.getByText(/not measured accuracy/)).toBeInTheDocument();
    expect(screen.getByText(/may consume paid credits/)).toBeInTheDocument();
  });

  it("requires saved consent and saved settings before making any cloud request", async () => {
    connected(true);
    const user = await openCard();
    await user.click(screen.getByLabelText("Allow classifier content to be sent to Ollama Cloud"));
    expect(screen.getByRole("button", { name: "Run synthetic test" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "Save classifier settings" }));
    await screen.findByText("Classifier settings saved.");
    expect(screen.getByRole("button", { name: "Evaluate shadow example" })).toBeDisabled();
    expect(apiPost).not.toHaveBeenCalled();
  });

  it("resets cloud consent and supplies exact local-cloud defaults when switching location", async () => {
    connected();
    const user = await openCard();
    await user.selectOptions(screen.getByLabelText("Execution location"), "cloud_via_local");
    expect(screen.getByLabelText("Classifier model")).toHaveValue("clef:cloud");
    expect(screen.getByLabelText("Classifier endpoint")).toHaveValue("http://localhost:11434");
    expect(screen.getByLabelText("Allow classifier content to be sent to Ollama Cloud")).not.toBeChecked();
    expect(screen.queryByLabelText("Classifier API key")).not.toBeInTheDocument();
    expect(screen.getByText(/a local or LAN endpoint does not make cloud inference local/)).toBeInTheDocument();
    expect(screen.getByText(/clears the previous key/)).toBeInTheDocument();
    await user.selectOptions(screen.getByLabelText("Execution location"), "local");
    expect(screen.getByLabelText("Classifier model")).toHaveValue("clef");
    expect(screen.queryByLabelText("Allow classifier content to be sent to Ollama Cloud")).not.toBeInTheDocument();
    await user.selectOptions(screen.getByLabelText("Execution location"), "cloud_direct");
    expect(screen.getByLabelText("Classifier endpoint")).toHaveValue("https://ollama.com");
  });

  it("uses enabled local configuration without cloud consent", async () => {
    view = {
      ...initial,
      config: { ...initial.config, enabled: true, execution_location: "local", endpoint: "http://localhost:11434" },
      configured: true,
    };
    await openCard();
    expect(screen.getByRole("button", { name: "Evaluate shadow example" })).toBeEnabled();
    expect(apiPost).not.toHaveBeenCalled();
  });

  it("saves an editable LAN server endpoint without a cloud key or automatic inference", async () => {
    connected(true);
    const user = await openCard();
    await user.selectOptions(screen.getByLabelText("Execution location"), "local");
    expect(screen.getByRole("option", { name: "Local or LAN Ollama (local models)" })).toBeInTheDocument();
    await user.clear(screen.getByLabelText("Classifier endpoint"));
    await user.type(screen.getByLabelText("Classifier endpoint"), "http://ollama.example.test:18114");
    await user.click(screen.getByRole("button", { name: "Save classifier settings" }));
    await screen.findByText("Classifier settings saved.");
    expect(apiPut).toHaveBeenLastCalledWith(
      "/api/ai/classifier/config",
      expect.objectContaining({
        execution_location: "local",
        endpoint: "http://ollama.example.test:18114",
        model: "clef",
        cloud_opt_in: false,
      })
    );
    expect(apiPut.mock.calls[0][1]).not.toHaveProperty("api_key");
    expect(screen.getByLabelText("Classifier endpoint")).toHaveValue("http://ollama.example.test:18114");
    expect(screen.getByRole("button", { name: "Evaluate shadow example" })).toBeEnabled();
    expect(apiPost).not.toHaveBeenCalled();
  });

  it("preserves a LAN hostname when switching server modes and still requires cloud consent", async () => {
    connected(true);
    const user = await openCard();
    await user.selectOptions(screen.getByLabelText("Execution location"), "local");
    await user.clear(screen.getByLabelText("Classifier endpoint"));
    await user.type(screen.getByLabelText("Classifier endpoint"), "https://ollama.example.test:8443");
    await user.selectOptions(screen.getByLabelText("Execution location"), "cloud_via_local");
    expect(screen.getByLabelText("Classifier endpoint")).toHaveValue("https://ollama.example.test:8443");
    expect(screen.getByLabelText("Classifier model")).toHaveValue("clef:cloud");
    expect(screen.getByLabelText("Allow classifier content to be sent to Ollama Cloud")).not.toBeChecked();
    await user.click(screen.getByRole("button", { name: "Save classifier settings" }));
    await screen.findByText("Classifier settings saved.");
    expect(screen.getByRole("button", { name: "Run synthetic test" })).toBeDisabled();
    expect(apiPost).not.toHaveBeenCalled();
  });

  it("sends only the deliberate shadow example with versioned choice criteria", async () => {
    connected(true);
    const user = await openCard();
    await user.clear(screen.getByLabelText("Shadow example"));
    await user.type(screen.getByLabelText("Shadow example"), "A bag of pasta");
    await user.click(screen.getByRole("button", { name: "Evaluate shadow example" }));
    await screen.findByText("Decision: Food product");
    expect(apiPost).toHaveBeenCalledWith("/api/ai/classifier/evaluations", {
      state: "A bag of pasta",
      questions: {
        q_1: {
          type: "choice",
          instructions: "Classify the described item as a food product or other.",
          criteria: { c_1: "Food product", c_2: "Other" },
        },
      },
      contract_version: "classifier.v1",
      policy_version: "shadow.v1",
      source_version: "manual.v1",
    });
  });

  it("switches provider without reusing credentials or consent, then saves and exercises Jev shadow calls", async () => {
    connected(true);
    apiPost.mockResolvedValue({
      data: {
        ...success,
        provider: "openrouter",
        model: "typesafe/jev-1.14",
        resolved_model: "typesafe/jev-1.14-20260909",
        usage: { input_tokens: 24, output_tokens: 3, cost_usd: 0.001 },
      },
    });
    const user = await openCard();
    await user.click(screen.getByRole("button", { name: "Run synthetic test" }));
    await screen.findByText("Decision: Food product");
    await user.type(screen.getByLabelText("Classifier API key"), "unsaved-ollama-key");
    await user.selectOptions(screen.getByLabelText("Classifier provider"), "openrouter");
    expect(screen.queryByText("Decision: Food product")).not.toBeInTheDocument();
    expect(screen.getByLabelText("Classifier API key")).toHaveValue("");
    expect(screen.getByPlaceholderText("Enter an OpenRouter key")).toBeInTheDocument();
    expect(screen.getByLabelText("Classifier endpoint")).toHaveValue("https://openrouter.ai");
    expect(screen.getByLabelText("Classifier model")).toHaveValue("typesafe/jev-1.14");
    expect(screen.getByLabelText("Execution location")).toBeDisabled();
    expect(screen.getByLabelText("Execution location").querySelectorAll("option")).toHaveLength(1);
    expect(screen.getByLabelText("Allow classifier content to be sent to OpenRouter")).not.toBeChecked();
    expect(screen.getByLabelText("Allow classifier content to be sent to OpenRouter")).toBeDisabled();
    expect(screen.getByText(/Content is sent to OpenRouter and its model provider/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Run synthetic test" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "Save classifier settings" }));
    await screen.findByText("Classifier settings saved.");
    expect(apiPut.mock.calls[0][1]).not.toHaveProperty("api_key");
    expect(view.api_key_set).toBe(false);
    expect(screen.getByLabelText("Allow classifier content to be sent to OpenRouter")).toBeEnabled();
    expect(screen.getByRole("button", { name: "Run synthetic test" })).toBeDisabled();
    await user.type(screen.getByLabelText("Classifier API key"), "jev-fixture-key");
    await user.click(screen.getByLabelText("Allow classifier content to be sent to OpenRouter"));
    await user.click(screen.getByRole("button", { name: "Save classifier settings" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Run synthetic test" })).toBeEnabled());
    expect(apiPut.mock.calls[1][1]).toMatchObject({
      provider: "openrouter",
      api_key: "jev-fixture-key",
      cloud_opt_in: true,
    });
    expect(screen.getByLabelText("Classifier API key")).toHaveValue("");
    await user.click(screen.getByRole("button", { name: "Run synthetic test" }));
    await screen.findByText("Resolved model: typesafe/jev-1.14-20260909");
    expect(screen.getByText(/Input tokens: 24 · Output tokens: 3 · Cost \(USD\): 0.001/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Evaluate shadow example" }));
    await waitFor(() => expect(apiPost).toHaveBeenCalledWith("/api/ai/classifier/evaluations", expect.any(Object)));
    await screen.findByText("Decision: Food product");
    await user.type(screen.getByLabelText("Classifier API key"), "unsaved-jev-key");
    await user.selectOptions(screen.getByLabelText("Classifier provider"), "ollama");
    expect(screen.queryByText("Decision: Food product")).not.toBeInTheDocument();
    expect(screen.getByLabelText("Classifier API key")).toHaveValue("");
    expect(screen.getByPlaceholderText("Enter an Ollama Cloud key")).toBeInTheDocument();
    expect(screen.getByLabelText("Classifier endpoint")).toHaveValue("https://ollama.com");
    expect(screen.getByLabelText("Classifier model")).toHaveValue("clef");
    expect(screen.getByLabelText("Execution location")).toBeEnabled();
    expect(screen.getByLabelText("Allow classifier content to be sent to Ollama Cloud")).not.toBeChecked();
    await user.click(screen.getByRole("button", { name: "Save classifier settings" }));
    await screen.findByText("Classifier settings saved.");
    expect(apiPut.mock.calls[2][1]).not.toHaveProperty("api_key");
    expect(view.api_key_set).toBe(false);
    expect(screen.getByRole("button", { name: "Run synthetic test" })).toBeDisabled();
    expect(apiPost.mock.calls).toHaveLength(3);
  });

  it("shows absent provider usage as unknown, preserving measured zero values", async () => {
    connected();
    apiPost.mockResolvedValue({ data: { ...success, usage: { input_tokens: 0, cost_usd: 0 } } });
    const user = await openCard();
    await user.click(screen.getByRole("button", { name: "Run synthetic test" }));
    expect(await screen.findByText(/Input tokens: 0 · Output tokens: Unknown · Cost \(USD\): 0/)).toBeInTheDocument();
  });

  it.each(["abstain", "unavailable", "invalid"] as const)(
    "renders %s without presenting a decision",
    async (status) => {
      connected();
      apiPost.mockResolvedValue({ data: { ...success, status, answers: {} } });
      const user = await openCard();
      await user.click(screen.getByRole("button", { name: "Run synthetic test" }));
      expect(await screen.findByText(/did not return a usable decision/)).toBeInTheDocument();
      expect(screen.queryByText("Decision: Food product")).not.toBeInTheDocument();
    }
  );

  it("never echoes server errors or submitted credentials into failure messages", async () => {
    connected();
    const failure = { response: { data: { detail: "private-test-key raw provider content" } } };
    apiPut.mockRejectedValue(failure);
    apiPost.mockRejectedValue(failure);
    apiDelete.mockRejectedValue(failure);
    const user = await openCard();
    await user.type(screen.getByLabelText("Classifier API key"), "private-test-key");
    await user.click(screen.getByRole("button", { name: "Save classifier settings" }));
    await screen.findByText(/settings could not be saved/);
    expect(screen.getByLabelText("Classifier API key")).toHaveValue("");
    await user.click(screen.getByRole("button", { name: "Run synthetic test" }));
    await screen.findByText(/synthetic test could not complete/);
    await user.click(screen.getByRole("button", { name: "Disconnect classifier" }));
    await screen.findByText(/could not be disconnected/);
    expect(screen.queryByText(/private-test-key/)).not.toBeInTheDocument();
  });

  it("disconnects only the classifier and resets its disabled configuration", async () => {
    connected(true);
    const user = await openCard();
    await user.click(screen.getByRole("button", { name: "Disconnect classifier" }));
    await screen.findByText("Classifier disconnected and disabled.");
    expect(apiDelete).toHaveBeenCalledWith("/api/ai/classifier/config");
    expect(screen.getByLabelText("Enable classifier shadow evaluations")).not.toBeChecked();
    expect(screen.getByRole("button", { name: "Run synthetic test" })).toBeDisabled();
  });

  it("blocks all settings saves without secure storage while keeping disconnect available", async () => {
    view = { ...initial, secrets_available: false };
    const user = await openCard();
    expect(screen.getByText(/Ask your administrator to enable it/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save classifier settings" })).toBeDisabled();
    await user.type(screen.getByLabelText("Classifier API key"), "private-test-key");
    expect(screen.getByRole("button", { name: "Save classifier settings" })).toBeDisabled();
    await user.selectOptions(screen.getByLabelText("Execution location"), "local");
    expect(screen.getByRole("button", { name: "Save classifier settings" })).toBeDisabled();
    expect(apiPut).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "Disconnect classifier" }));
    await screen.findByText("Classifier disconnected and disabled.");
    expect(apiDelete).toHaveBeenCalledWith("/api/ai/classifier/config");
  });

  it("gives an actionable recovery for unreadable settings without exposing server details", async () => {
    view = { ...initial, valid: false, detail: "untrusted private server contents" };
    await openCard();
    expect(screen.getByText(/Disconnect the classifier, then configure it again/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Run synthetic test" })).toBeDisabled();
    expect(screen.queryByText(/untrusted private server contents/)).not.toBeInTheDocument();
  });
  it("defaults recipe assistance off at90%, saves a stricter policy without any inference", async () => {
    const user = await openCard();
    expect(screen.getByLabelText("Allow classifier assistance in recipe match reviews")).not.toBeChecked();
    expect(screen.getByLabelText("Recipe review confidence threshold (%)")).toHaveValue(90);
    await user.click(screen.getByLabelText("Allow classifier assistance in recipe match reviews"));
    fireEvent.change(screen.getByLabelText("Recipe review confidence threshold (%)"), { target: { value: "97" } });
    await user.click(screen.getByRole("button", { name: "Save classifier settings" }));
    await waitFor(() =>
      expect(apiPut).toHaveBeenCalledWith(
        "/api/ai/classifier/config",
        expect.objectContaining({ runtime_review_enabled: true, review_confidence: 0.97 })
      )
    );
    expect(apiPost).not.toHaveBeenCalled();
  });
  it("rejects out-of-range review thresholds before saving", async () => {
    await openCard();
    fireEvent.change(screen.getByLabelText("Recipe review confidence threshold (%)"), { target: { value: "101" } });
    expect(screen.getByRole("button", { name: "Save classifier settings" })).toBeDisabled();
    expect(apiPut).not.toHaveBeenCalled();
  });
});
