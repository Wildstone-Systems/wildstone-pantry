import { cleanup, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { renderWithProviders } from "@/test-utils";

import { QuantityStepper } from "./QuantityStepper";

describe("QuantityStepper", () => {
  afterEach(cleanup);

  it("nudges up and down by the step", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    renderWithProviders(<QuantityStepper label="How many" value={2} onChange={onChange} />);

    await user.click(screen.getByRole("button", { name: "Increase How many" }));
    expect(onChange).toHaveBeenCalledWith(3);

    await user.click(screen.getByRole("button", { name: "Decrease How many" }));
    expect(onChange).toHaveBeenCalledWith(1);
  });

  it("honours a fractional step without float drift", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    renderWithProviders(<QuantityStepper label="Size" value={0.1} step={0.2} onChange={onChange} />);

    // 0.1 + 0.2 is 0.30000000000000004 in raw binary floating point.
    await user.click(screen.getByRole("button", { name: "Increase Size" }));
    expect(onChange).toHaveBeenCalledWith(0.3);
  });

  it("clamps to min and disables decrement at the floor", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    renderWithProviders(<QuantityStepper label="Qty" value={0} min={0} onChange={onChange} />);

    expect(screen.getByRole("button", { name: "Decrease Qty" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "Increase Qty" }));
    expect(onChange).toHaveBeenCalledWith(1);
  });

  it("clamps to max and disables increment at the ceiling", async () => {
    const onChange = vi.fn();
    renderWithProviders(<QuantityStepper label="Qty" value={5} max={5} onChange={onChange} />);
    expect(screen.getByRole("button", { name: "Increase Qty" })).toBeDisabled();
  });

  it("fires onStep after a nudge so callers can restore focus", async () => {
    const user = userEvent.setup();
    const onStep = vi.fn();
    renderWithProviders(<QuantityStepper label="Qty" value={1} onChange={vi.fn()} onStep={onStep} />);

    await user.click(screen.getByRole("button", { name: "Increase Qty" }));
    expect(onStep).toHaveBeenCalledTimes(1);
  });

  it("clearing the field stays cleared so it can be retyped", async () => {
    // Snapping back to `min` on clear makes the field impossible to retype:
    // you clear it, it refills with the minimum, and the next keystroke appends
    // to that ("0.25" + "2" = 0.252).
    const user = userEvent.setup();
    const onChange = vi.fn();
    renderWithProviders(<QuantityStepper label="Qty" value={3} min={0.25} onChange={onChange} />);

    await user.clear(screen.getByLabelText("Qty"));
    expect(onChange).toHaveBeenLastCalledWith("");
  });

  it("names the input after the visible label, and the buttons after the short name", () => {
    // An accessible name that doesn't match the visible text is its own bug —
    // but "Decrease How many servings are you making?" is a terrible button name.
    renderWithProviders(
      <QuantityStepper
        label="How many servings are you making?"
        ariaLabel="servings"
        value={4}
        onChange={vi.fn()}
      />,
    );
    expect(screen.getByLabelText("How many servings are you making?")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Increase servings" })).toBeInTheDocument();
  });

  it("renders without a label when none is given", () => {
    renderWithProviders(<QuantityStepper value={1} onChange={vi.fn()} ariaLabel="Flour" />);
    expect(screen.getByRole("button", { name: "Increase Flour" })).toBeInTheDocument();
    expect(screen.getByLabelText("Flour")).toBeInTheDocument();
  });
});
