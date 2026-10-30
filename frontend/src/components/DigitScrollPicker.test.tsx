import { cleanup, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { renderWithProviders } from "@/test-utils";

import { DigitScrollPicker, type FractionMode } from "./DigitScrollPicker";

/** The picker is controlled; anything spanning two columns (typing a whole
 *  number) needs the value to actually move between keystrokes. */
function Controlled({ start = 0, max, fraction }: { start?: number; max?: number; fraction?: FractionMode }) {
  const [value, setValue] = useState(start);
  return (
    <>
      <DigitScrollPicker ariaLabel="grams" value={value} onChange={setValue} max={max} fraction={fraction} />
      <output data-testid="value">{value}</output>
    </>
  );
}

const column = (place: string) => screen.getByRole("spinbutton", { name: `${place} of grams` });

describe("DigitScrollPicker", () => {
  afterEach(cleanup);

  it("splits the value into one wheel per place", () => {
    renderWithProviders(<DigitScrollPicker ariaLabel="grams" value={157} onChange={vi.fn()} />);

    expect(column("hundreds")).toHaveAttribute("aria-valuenow", "1");
    expect(column("tens")).toHaveAttribute("aria-valuenow", "5");
    expect(column("ones")).toHaveAttribute("aria-valuenow", "7");
  });

  it("moves by that column's place value, so 0 to 100 costs the same as 0 to 1", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    renderWithProviders(<DigitScrollPicker ariaLabel="grams" value={0} onChange={onChange} />);

    await user.click(column("ones"));
    await user.keyboard("{ArrowUp}");
    expect(onChange).toHaveBeenLastCalledWith(1);

    await user.click(column("hundreds"));
    await user.keyboard("{ArrowUp}");
    expect(onChange).toHaveBeenLastCalledWith(100);
  });

  it("holds at the ends of a wheel instead of carrying into the next", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    renderWithProviders(<DigitScrollPicker ariaLabel="grams" value={9} onChange={onChange} />);

    // 9 + 1 in the ones column would be a carry — the wheels don't do that;
    // the tens wheel is right there.
    await user.click(column("ones"));
    await user.keyboard("{ArrowUp}");
    expect(onChange).not.toHaveBeenCalled();

    await user.keyboard("{ArrowDown}");
    expect(onChange).toHaveBeenLastCalledWith(8);
  });

  it("jumps a wheel to a digit that is on screen but not centred", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    renderWithProviders(<DigitScrollPicker ariaLabel="grams" value={5} onChange={onChange} />);

    await user.click(within(column("tens")).getByText("4"));
    expect(onChange).toHaveBeenLastCalledWith(45);
  });

  it("Home and End reach the ends of a wheel in one go", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    renderWithProviders(<DigitScrollPicker ariaLabel="grams" value={55} onChange={onChange} />);

    await user.click(column("tens"));
    await user.keyboard("{End}");
    expect(onChange).toHaveBeenLastCalledWith(95);

    await user.keyboard("{Home}");
    expect(onChange).toHaveBeenLastCalledWith(5);
  });

  it("typing a number fills the wheels left to right", async () => {
    const user = userEvent.setup();
    renderWithProviders(<Controlled />);

    await user.click(column("hundreds"));
    await user.keyboard("157");

    expect(screen.getByTestId("value")).toHaveTextContent(/^157$/);
    expect(column("ones")).toHaveFocus();
  });

  it("clamps to max rather than showing a number it would not log", async () => {
    const user = userEvent.setup();
    renderWithProviders(<Controlled start={0} max={250} />);

    await user.click(column("hundreds"));
    await user.keyboard("{End}");

    expect(screen.getByTestId("value")).toHaveTextContent(/^250$/);
    expect(column("hundreds")).toHaveAttribute("aria-valuenow", "2");
  });

  it("rounds a fractional starting value to whole units", () => {
    renderWithProviders(<DigitScrollPicker ariaLabel="grams" value={2.4} onChange={vi.fn()} />);
    expect(column("ones")).toHaveAttribute("aria-valuenow", "2");
  });

  it("names the wheels after the label when there is no short name", () => {
    renderWithProviders(<DigitScrollPicker label="How much" value={0} onChange={vi.fn()} />);
    expect(screen.getByRole("group", { name: "How much" })).toBeInTheDocument();
    expect(screen.getByRole("spinbutton", { name: "tens of How much" })).toBeInTheDocument();
  });

  it("has no fraction wheel unless one is asked for", () => {
    renderWithProviders(<DigitScrollPicker ariaLabel="grams" value={3} onChange={vi.fn()} />);
    expect(screen.getAllByRole("spinbutton")).toHaveLength(3);
    expect(screen.queryByRole("spinbutton", { name: "fraction of grams" })).not.toBeInTheDocument();
  });

  it("adds a quarter at a time on the fraction wheel", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    renderWithProviders(<DigitScrollPicker ariaLabel="grams" value={1} onChange={onChange} fraction="quarters" />);

    await user.click(column("fraction"));
    await user.keyboard("{ArrowUp}");
    expect(onChange).toHaveBeenLastCalledWith(1.25);

    await user.keyboard("{End}");
    expect(onChange).toHaveBeenLastCalledWith(1.75);
  });

  it("keeps the fraction while the whole-number wheels move", async () => {
    const user = userEvent.setup();
    renderWithProviders(<Controlled start={0.5} fraction="quarters" />);

    await user.click(column("hundreds"));
    await user.keyboard("2");
    expect(screen.getByTestId("value")).toHaveTextContent(/^200\.5$/);
    expect(column("fraction")).toHaveAttribute("aria-valuetext", "0.50");
  });

  it("holds at three quarters instead of carrying into the next whole", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    renderWithProviders(<DigitScrollPicker ariaLabel="grams" value={2.75} onChange={onChange} fraction="quarters" />);

    await user.click(column("fraction"));
    await user.keyboard("{ArrowUp}");
    expect(onChange).not.toHaveBeenCalled();
  });

  it("counts in exact quarters — no floating-point crumbs", async () => {
    const user = userEvent.setup();
    renderWithProviders(<Controlled start={0} fraction="quarters" />);

    // 0.1 + 0.2 territory: three quarter-steps must land on 0.75, not
    // 0.7500000000000001, because this value is what gets logged.
    await user.click(column("fraction"));
    await user.keyboard("{ArrowUp}{ArrowUp}{ArrowUp}");
    expect(screen.getByTestId("value")).toHaveTextContent(/^0\.75$/);
  });

  it("respects max with a fraction wheel in play", async () => {
    const user = userEvent.setup();
    renderWithProviders(<Controlled start={2} max={2} fraction="quarters" />);

    await user.click(column("fraction"));
    await user.keyboard("{ArrowUp}");
    expect(screen.getByTestId("value")).toHaveTextContent(/^2$/);
  });

  it("offers halves or tenths when quarters are the wrong grain", async () => {
    const user = userEvent.setup();
    renderWithProviders(<Controlled start={0} fraction="tenths" />);

    await user.click(column("fraction"));
    await user.keyboard("3");
    expect(screen.getByTestId("value")).toHaveTextContent(/^0\.3$/);
  });

  it("is inert when disabled", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    renderWithProviders(<DigitScrollPicker ariaLabel="grams" value={5} onChange={onChange} disabled />);

    await user.click(within(column("ones")).getByText("8"));
    expect(onChange).not.toHaveBeenCalled();
    expect(column("ones")).toHaveAttribute("tabindex", "-1");
  });
});
