import "@testing-library/jest-dom/vitest";

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
