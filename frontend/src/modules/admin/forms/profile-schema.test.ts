import { describe, expect, it } from "vitest";

import { profileSchema } from "./profile-schema";

describe("profileSchema", () => {
  it("accepts a null calorie target (form reset sets null, not undefined)", () => {
    // Regression: `.optional()` alone rejected null, leaving 'Add person'
    // permanently disabled after the first create reset the field to null.
    const parsed = profileSchema.parse({ display_name: "Alex", daily_calorie_target: null });
    expect(parsed.daily_calorie_target).toBeNull();
  });

  it("keeps a numeric calorie target and requires a name", () => {
    expect(profileSchema.parse({ display_name: "Sam", daily_calorie_target: 2000 }).daily_calorie_target).toBe(2000);
    expect(() => profileSchema.parse({ display_name: "" })).toThrow();
  });
});
