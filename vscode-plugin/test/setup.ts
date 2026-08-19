// © Copyright 2026 Aaron Kimball
// jsdom lacks ResizeObserver; provide a stub for components that use it.
globalThis.ResizeObserver = class {
  observe(): void {}
  unobserve(): void {}
  disconnect(): void {}
};

// jsdom's ElementInternals omits the form-association API that vscode-elements' form-associated
// components call unconditionally from their constructors, so stub it before any such element
// is constructed.
if (typeof ElementInternals !== 'undefined') {
  ElementInternals.prototype.setFormValue = function (): void {};
  ElementInternals.prototype.setValidity = function (): void {};
  ElementInternals.prototype.checkValidity = function (): boolean {
    return true;
  };
  ElementInternals.prototype.reportValidity = function (): boolean {
    return true;
  };
  Object.defineProperty(ElementInternals.prototype, 'form', {
    get: () => null,
    configurable: true,
  });
  Object.defineProperty(ElementInternals.prototype, 'validity', {
    get: () => ({ valid: true }),
    configurable: true,
  });
  Object.defineProperty(ElementInternals.prototype, 'validationMessage', {
    get: () => '',
    configurable: true,
  });
  Object.defineProperty(ElementInternals.prototype, 'willValidate', {
    get: () => true,
    configurable: true,
  });
}

// Registers the real @vscode-elements/elements custom elements so jsdom-environment tests
// exercise the same <vscode-textarea>/<vscode-button>/etc. behavior as production instead of
// inert, unregistered elements. Guarded on `customElements` because this setup file also runs
// for host-side tests under plain Node, which has no DOM.
if (typeof customElements !== 'undefined') {
  await import('webview/registerCustomElements');

  // <vscode-icon> reads this link's href for the codicons stylesheet. Without one, jsdom leaves
  // the hrefless <link rel="stylesheet"> it renders into its shadow DOM with a broken
  // CSSStyleSheet that crashes Node's own error formatter if anything inspects it.
  const codiconStylesheetLink = document.createElement('link');
  codiconStylesheetLink.id = 'vscode-codicon-stylesheet';
  codiconStylesheetLink.rel = 'stylesheet';
  codiconStylesheetLink.href = 'about:blank';
  document.head.appendChild(codiconStylesheetLink);
}
