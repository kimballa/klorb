// © Copyright 2026 Aaron Kimball
// jsdom lacks ResizeObserver; provide a stub for components that use it.
globalThis.ResizeObserver = class {
  observe(): void {}
  unobserve(): void {}
  disconnect(): void {}
};
