// © Copyright 2026 Aaron Kimball
import { type ForwardedRef, type JSX, type ReactNode, forwardRef } from 'react';

export type IconButtonVariant = 'toolbar' | 'filled';

interface IconButtonProps {
  /** The icon shown inside the button: a `<vscode-icon name="..." />` for a codicon, or a
   * custom SVG icon component (see `webview/components/klorbIcons`) when no codicon fits. */
  children: ReactNode;
  /** 'toolbar' (the default) is borderless with a hover background, for an icon that sits
   * directly on the panel background (`PanelHeader`'s session-history/new-session buttons).
   * 'filled' is a solid `--vscode-button-background` square, for an icon that should read as a
   * primary action (`StatusMenu`'s chevron). */
  variant?: IconButtonVariant;
  /** Doubles as the accessible name (`aria-label`) and, via the native `title` attribute, the
   * host's hover tooltip. */
  title: string;
  onClick(): void;
}

/**
 * An 18x18 icon-only affordance button. Shared by every place in the panel chrome that wants
 * one instead of a full `vscode-button` -- see docs/specs/vscode-plugin-ui.md's "Buttons"
 * section for when this applies over `vscode-button` or a bare status chip.
 */
const IconButton = forwardRef(function IconButton(
  { children, variant = 'toolbar', title, onClick }: IconButtonProps,
  ref: ForwardedRef<HTMLButtonElement>
): JSX.Element {
  return (
    <button
      ref={ref}
      type="button"
      className={`icon-button icon-button-${variant}`}
      title={title}
      aria-label={title}
      onClick={onClick}>
      {children}
    </button>
  );
});

export default IconButton;
