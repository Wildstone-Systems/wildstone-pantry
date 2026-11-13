import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

// Testing Library only auto-unmounts when Vitest `globals` is on, and it is off
// here. Without this, every rendered tree stays mounted after its test, and
// React can still run scheduled work when the jsdom `window` is torn down —
// an intermittent "window is not defined" that fails CI with all tests green.
afterEach(() => {
  cleanup();
});

// Mantine depends on matchMedia for color scheme and media query hooks.
if (!window.matchMedia) {
  window.matchMedia = (query: string) =>
    ({
      matches: false,
      media: query,
      onchange: null,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => false,
    }) as any;
}

// Mantine components like Modal/ScrollArea depend on ResizeObserver.
if (!(window as any).ResizeObserver) {
  class ResizeObserver {
    observe() {}
    unobserve() {}
    disconnect() {}
  }
  (window as any).ResizeObserver = ResizeObserver;
}

// jsdom implements no layout, so it has no `scrollIntoView`. The scan pages call
// it from a timer after a result renders, which lands AFTER the test that
// triggered it has finished — surfacing as an unhandled error that fails the
// run even when every test passes. Stub it: we're asserting behaviour, not
// scroll position.
if (!Element.prototype.scrollIntoView) {
  Element.prototype.scrollIntoView = function scrollIntoView() {};
}

// jsdom has no FontFaceSet. Mantine's autosizing Textarea listens on
// `document.fonts` for font loads to re-measure; stub the event API.
if (!(document as any).fonts) {
  (document as any).fonts = {
    addEventListener: () => {},
    removeEventListener: () => {},
    ready: Promise.resolve(),
  };
}
