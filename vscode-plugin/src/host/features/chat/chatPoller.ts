// © Copyright 2026 Aaron Kimball
import { errorMessage, type AcpConnection, type LogFn } from 'host/features/acp';
import { parseChatHistoryResult, type ChatHistoryUpdateMessage } from 'shared/webviewMessages';

/** How often to re-fetch the chat room while it's selected. */
const CHAT_POLL_INTERVAL_MS = 1000;

export type ChatHistoryListener = (update: Omit<ChatHistoryUpdateMessage, 'type'>) => void;

/** Polls `_klorb/chatHistory` on behalf of the webview's chat room view. */
export class ChatPoller {
  private readonly _connection: AcpConnection;
  private readonly _onHistory: ChatHistoryListener;
  private readonly _log: LogFn;
  private _timer: ReturnType<typeof setInterval> | undefined;
  private _selected = false;

  public constructor(connection: AcpConnection, onHistory: ChatHistoryListener, log: LogFn) {
    this._connection = connection;
    this._onHistory = onHistory;
    this._log = log;
  }

  /** The webview selected (or deselected) the "Chat Room" row. */
  public setSelected(selected: boolean): void {
    this._selected = selected;
    this._syncTimer();
  }

  /** Posts `text` to the chat room as the user. */
  public async postMessage(text: string): Promise<void> {
    await this._connection.extMethod('_klorb/chatPost', { text });
    if (this._selected) {
      // Avoids waiting up to CHAT_POLL_INTERVAL_MS for the sent message to appear.
      void this._pollHistory();
    }
  }

  /** Re-evaluates the timer against the connection's current `chatCapable` value. */
  public resync(): void {
    this._syncTimer();
  }

  /** Stops the timer. */
  public dispose(): void {
    this._selected = false;
    this._syncTimer();
  }

  private _syncTimer(): void {
    const shouldPoll = this._selected && this._connection.chatCapable;
    if (shouldPoll && this._timer === undefined) {
      void this._pollHistory();
      this._timer = setInterval(() => void this._pollHistory(), CHAT_POLL_INTERVAL_MS);
    } else if (!shouldPoll && this._timer !== undefined) {
      clearInterval(this._timer);
      this._timer = undefined;
    }
  }

  private async _pollHistory(): Promise<void> {
    try {
      const result = await this._connection.extMethod('_klorb/chatHistory', {});
      const parsed = parseChatHistoryResult(result);
      if (parsed === undefined) {
        this._log(`klorb: malformed _klorb/chatHistory result: ${JSON.stringify(result)}`);
        return;
      }
      this._onHistory(parsed);
    } catch (err) {
      this._log(`klorb: chat history poll failed: ${errorMessage(err)}`);
    }
  }
}
