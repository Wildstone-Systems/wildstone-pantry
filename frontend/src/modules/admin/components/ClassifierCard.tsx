import {
  Alert,
  Badge,
  Button,
  Checkbox,
  Group,
  NativeSelect,
  PasswordInput,
  Text,
  Textarea,
  TextInput,
} from "@mantine/core";
import { useEffect, useState } from "react";

import {
  type ClassifierConfig,
  type ClassifierEvaluation,
  useClassifierConfig,
  useClearClassifierConfig,
  useEvaluateClassifier,
  useSaveClassifierConfig,
  useTestClassifier,
} from "../hooks/classifier-hooks";
import { CollapsibleCard } from "./CollapsibleCard";

const defaults: ClassifierConfig = {
  enabled: false,
  provider: "ollama",
  model: "clef",
  execution_location: "cloud_direct",
  endpoint: "https://ollama.com",
  cloud_opt_in: false,
  runtime_review_enabled: false,
  review_confidence: 0.9,
};

function EvaluationResult({ result }: { result: ClassifierEvaluation }) {
  const answer = result.answers.q_1;
  const labels: Record<string, string> = { c_1: "Food product", c_2: "Other" };
  return (
    <Alert color={result.status === "ok" ? "blue" : "orange"} title="Shadow result">
      <Text>
        Status:{" "}
        {result.status === "ok"
          ? "Completed"
          : result.status === "abstain"
            ? "No decision"
            : result.status === "invalid"
              ? "Invalid response"
              : "Unavailable"}
      </Text>
      {result.status === "ok" && answer ? (
        <>
          <Text>Decision: {labels[answer.choice] ?? "Unrecognized choice"}</Text>
          {Object.entries(answer.probabilities).map(([choice, probability]) => (
            <Text key={choice}>
              {labels[choice] ?? "Other choice"}: {(probability * 100).toFixed(1)}%
            </Text>
          ))}
          <Text>Distribution confidence: {(answer.confidence * 100).toFixed(1)}%</Text>
        </>
      ) : (
        <Text>The classifier did not return a usable decision. Check the saved configuration and try again.</Text>
      )}
      <Text size="sm">
        {result.provider} · {result.model} · This result never changes your pantry. Distribution confidence describes
        the answer spread; it is not measured accuracy.
      </Text>
      {result.resolved_model ? <Text size="sm">Resolved model: {result.resolved_model}</Text> : null}
      <Text size="sm">
        Input tokens: {result.usage?.input_tokens ?? "Unknown"} · Output tokens:{" "}
        {result.usage?.output_tokens ?? "Unknown"} · Cost (USD): {result.usage?.cost_usd ?? "Unknown"}
      </Text>
    </Alert>
  );
}

export function ClassifierCard() {
  const query = useClassifierConfig();
  const save = useSaveClassifierConfig();
  const clear = useClearClassifierConfig();
  const test = useTestClassifier();
  const evaluate = useEvaluateClassifier();
  const [draft, setDraft] = useState<ClassifierConfig>(defaults);
  const [apiKey, setApiKey] = useState("");
  const [message, setMessage] = useState<string | null>(null);
  const [result, setResult] = useState<ClassifierEvaluation | null>(null);
  const [state, setState] = useState("A sealed bag of brown rice.");
  const [instructions, setInstructions] = useState("Classify the described item as a food product or other.");
  const view = query.data;

  useEffect(() => {
    if (view) setDraft({ ...defaults, ...view.config });
  }, [view]);

  const dirty =
    !view ||
    (Object.keys(defaults) as Array<keyof ClassifierConfig>).some(
      (key) => draft[key] !== (view.config[key] ?? defaults[key])
    ) ||
    apiKey.length > 0;
  const cloud = draft.execution_location !== "local";
  const openRouter = draft.provider === "openrouter";
  const cloudProvider = openRouter ? "OpenRouter" : "Ollama Cloud";
  const savedIdentity = Boolean(
    view &&
    (["provider", "endpoint", "model", "execution_location"] as const).every((key) => draft[key] === view.config[key])
  );
  const busy = save.isPending || clear.isPending || test.isPending || evaluate.isPending;
  const canCall = Boolean(view?.valid && view.configured && !dirty && (!cloud || draft.cloud_opt_in));
  const update = (changes: Partial<ClassifierConfig>) => {
    setDraft((current) => ({ ...current, ...changes }));
    setResult(null);
    setMessage(null);
  };
  const changeLocation = (location: ClassifierConfig["execution_location"]) => {
    update({
      execution_location: location,
      endpoint:
        location === "cloud_direct"
          ? "https://ollama.com"
          : draft.execution_location !== "cloud_direct"
            ? draft.endpoint
            : "http://localhost:11434",
      model: location === "cloud_via_local" ? "clef:cloud" : "clef",
      cloud_opt_in: false,
    });
    setApiKey("");
  };
  const changeProvider = (provider: ClassifierConfig["provider"]) => {
    update({
      provider,
      execution_location: "cloud_direct",
      endpoint: provider === "openrouter" ? "https://openrouter.ai" : "https://ollama.com",
      model: provider === "openrouter" ? "typesafe/jev-1.14" : "clef",
      cloud_opt_in: false,
    });
    setApiKey("");
  };
  const receive = (next: ClassifierEvaluation) => {
    setResult(next);
    setMessage(null);
  };

  return (
    <CollapsibleCard
      id="classifier"
      title="Classifier"
      status={
        <Badge color={view?.config.enabled ? "blue" : "gray"}>
          {view?.config.runtime_review_enabled ? "human review" : view?.config.enabled ? "shadow only" : "disabled"}
        </Badge>
      }
    >
      <Text size="sm">
        Optional item classification, configured separately from receipt vision and other AI providers. Shadow results
        never change pantry items, identities, recipes, or nutrition.
      </Text>
      {query.isError ? <Alert color="red">Classifier settings could not be loaded. Reload to try again.</Alert> : null}
      {view && !view.valid ? (
        <Alert color="orange">
          Saved classifier settings are invalid or unreadable. Disconnect the classifier, then configure it again.
        </Alert>
      ) : null}
      {view && !view.secrets_available ? (
        <Alert color="orange">
          Secure settings storage is unavailable. Ask your administrator to enable it before saving classifier settings,
          including local settings. You can still disconnect the classifier to recover.
        </Alert>
      ) : null}
      <Checkbox
        label="Enable classifier shadow evaluations"
        checked={draft.enabled}
        onChange={(event) => update({ enabled: event.currentTarget.checked })}
      />
      <Checkbox
        label="Allow classifier assistance in recipe match reviews"
        checked={draft.runtime_review_enabled ?? false}
        disabled={busy}
        onChange={(event) => update({ runtime_review_enabled: event.currentTarget.checked })}
      />
      <TextInput
        label="Recipe review confidence threshold (%)"
        type="number"
        min={0}
        max={100}
        step={1}
        value={(draft.review_confidence ?? 0.9) * 100}
        disabled={busy}
        onChange={(event) => update({ review_confidence: Number(event.currentTarget.value) / 100 })}
      />
      <Text size="sm">
        Default 90%. Lower-score possibilities remain available for manual review when identity is known. Every recipe
        choice needs human confirmation. Saving changes invalidates pending reviews; prior human-confirmed choices
        remain saved.
      </Text>
      <NativeSelect
        label="Classifier provider"
        value={draft.provider}
        data={[
          { value: "ollama", label: "Ollama / Clef" },
          { value: "openrouter", label: "OpenRouter / Jev" },
        ]}
        onChange={(event) => changeProvider(event.currentTarget.value as ClassifierConfig["provider"])}
        disabled={busy}
      />
      <NativeSelect
        label="Execution location"
        value={draft.execution_location}
        onChange={(event) => changeLocation(event.currentTarget.value as ClassifierConfig["execution_location"])}
        disabled={openRouter || busy}
        data={
          openRouter
            ? [{ value: "cloud_direct", label: "OpenRouter Cloud (direct)" }]
            : [
                { value: "cloud_direct", label: "Ollama Cloud (direct)" },
                { value: "cloud_via_local", label: "Ollama Cloud through local or LAN Ollama" },
                { value: "local", label: "Local or LAN Ollama (local models)" },
              ]
        }
      />
      <TextInput
        label="Classifier endpoint"
        placeholder={draft.execution_location === "cloud_direct" ? undefined : "http://ollama.example.test:11434"}
        value={draft.endpoint}
        onChange={(event) => update({ endpoint: event.currentTarget.value })}
      />
      <TextInput
        label="Classifier model"
        value={draft.model}
        onChange={(event) => update({ model: event.currentTarget.value })}
      />
      {openRouter ? (
        <Text size="sm">
          Jev connects directly through OpenRouter using its Decisions API. Use https://openrouter.ai and the pinned
          model typesafe/jev-1.14. Content is sent to OpenRouter and its model provider; there is no automatic provider
          fallback.
        </Text>
      ) : draft.execution_location === "cloud_direct" ? (
        <Text size="sm">
          Direct Ollama Cloud uses https://ollama.com. To use your own Ollama server, choose a local or LAN execution
          option above.
        </Text>
      ) : (
        <Text size="sm">
          Enter the Ollama server address reachable from PantryOS: a LAN IP, hostname or Docker service name. HTTP/HTTPS
          and custom ports are supported, for example http://ollama.example.test:11434. Localhost means the PantryOS
          server or container. Cloud through local or LAN Ollama still sends content to Ollama Cloud; a local or LAN
          endpoint does not make cloud inference local.
        </Text>
      )}
      {draft.execution_location === "cloud_direct" ? (
        <PasswordInput
          label="Classifier API key"
          autoComplete="new-password"
          value={apiKey}
          onChange={(event) => setApiKey(event.currentTarget.value)}
          placeholder={
            view?.api_key_set && savedIdentity ? "Key saved — leave blank to keep" : `Enter an ${cloudProvider} key`
          }
        />
      ) : null}
      <Text size="sm">
        A blank key keeps the saved key only when provider, endpoint, model and execution location are unchanged.
        Changing any of these clears the previous key unless you supply a new one.
      </Text>
      {cloud ? (
        <Checkbox
          label={`Allow classifier content to be sent to ${cloudProvider}`}
          checked={draft.cloud_opt_in}
          disabled={busy || draft.provider !== view?.config.provider}
          onChange={(event) => update({ cloud_opt_in: event.currentTarget.checked })}
        />
      ) : null}
      {draft.provider !== view?.config.provider ? (
        <Text size="sm">
          Save the provider change first, then allow cloud requests and save again. Provider changes clear prior consent
          and credentials.
        </Text>
      ) : null}
      <Text size="sm">
        Saving does not contact the provider. Tests and evaluations send content and may consume paid credits. The
        synthetic test sends a fixed example, even while the classifier is disabled. A successful connection does not
        establish classification quality; quality remains unmeasured.
      </Text>
      {message ? <Alert>{message}</Alert> : null}
      <Group>
        <Button
          disabled={
            !view ||
            query.isError ||
            busy ||
            !view.secrets_available ||
            !Number.isFinite(draft.review_confidence) ||
            (draft.review_confidence ?? -1) < 0 ||
            (draft.review_confidence ?? 2) > 1
          }
          onClick={() => {
            setMessage(null);
            setResult(null);
            save.mutate(
              { ...draft, ...(apiKey ? { api_key: apiKey } : {}) },
              {
                onSuccess: () => {
                  setApiKey("");
                  setMessage("Classifier settings saved.");
                },
                onError: () => {
                  setApiKey("");
                  setMessage(
                    "Classifier settings could not be saved. Check the endpoint, model, consent and credential storage, then try again."
                  );
                },
              }
            );
          }}
        >
          Save classifier settings
        </Button>
        <Button
          variant="light"
          disabled={!canCall || busy}
          onClick={() => {
            setResult(null);
            setMessage(null);
            test.mutate(undefined, {
              onSuccess: receive,
              onError: () =>
                setMessage("The synthetic test could not complete. Check the saved configuration and try again."),
            });
          }}
        >
          Run synthetic test
        </Button>
        <Button
          variant="subtle"
          color="red"
          disabled={!view || busy}
          onClick={() => {
            setApiKey("");
            setResult(null);
            setMessage(null);
            clear.mutate(undefined, {
              onSuccess: () => setMessage("Classifier disconnected and disabled."),
              onError: () => setMessage("Classifier could not be disconnected. Try again."),
            });
          }}
        >
          Disconnect classifier
        </Button>
      </Group>
      {dirty ? <Text size="sm">Save your settings before testing or evaluating.</Text> : null}
      <Textarea
        label="Shadow example"
        maxLength={8000}
        value={state}
        onChange={(event) => {
          setState(event.currentTarget.value);
          setResult(null);
        }}
      />
      <Textarea
        label="Classification instructions"
        maxLength={1000}
        value={instructions}
        onChange={(event) => {
          setInstructions(event.currentTarget.value);
          setResult(null);
        }}
      />
      <Button
        variant="light"
        disabled={!canCall || !draft.enabled || busy || !state.trim() || !instructions.trim()}
        onClick={() => {
          setResult(null);
          setMessage(null);
          evaluate.mutate(
            {
              state,
              questions: { q_1: { type: "choice", instructions, criteria: { c_1: "Food product", c_2: "Other" } } },
              contract_version: "classifier.v1",
              policy_version: "shadow.v1",
              source_version: "manual.v1",
            },
            {
              onSuccess: receive,
              onError: () =>
                setMessage("The shadow evaluation could not complete. Check the saved configuration and try again."),
            }
          );
        }}
      >
        Evaluate shadow example
      </Button>
      {result ? <EvaluationResult result={result} /> : null}
    </CollapsibleCard>
  );
}
