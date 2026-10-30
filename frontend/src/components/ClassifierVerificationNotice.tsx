import { Alert, Text } from "@mantine/core";

export function ClassifierVerificationNotice() {
  return (
    <Alert
      color="yellow"
      title={<Text fw={600} c="var(--mantine-color-text)">Classifier suggestion — human verification required</Text>}
    >
      Suggestions can be wrong or based on incomplete information. A confidence score is not validated accuracy,
      even above your threshold. Check the source details and the proposed choice before confirming.
    </Alert>
  );
}
