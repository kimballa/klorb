// © Copyright 2026 Aaron Kimball
import { type ChildProcessWithoutNullStreams, spawn } from 'child_process';
import * as readline from 'readline';

/** Spawns a child process, given the same arguments as Node's own `child_process.spawn`,
 * split out so tests can inject a fake process without spawning anything real. */
export type SpawnFn = (
  command: string,
  args: string[],
  env: NodeJS.ProcessEnv,
) => ChildProcessWithoutNullStreams;

/** Where to find `klorb` and what environment to launch it with. */
export interface KlorbServerOptions {
  command: string;
  env: NodeJS.ProcessEnv;
  /** Path to an additional klorb-config.json file, passed to `klorb server` as `--config`
   * when non-empty (see docs/specs/klorb-server.md). */
  configPath?: string;
}

const defaultSpawnFn: SpawnFn = (command, args, env) => spawn(command, args, { env });

/**
 * Parses one line of the server's JSONL stdout (docs/specs/klorb-server.md) into a reply
 * object, falling back to an `{error}` shape if the line isn't a JSON object — mirroring how
 * the server itself replies to malformed input rather than crashing.
 */
export function parseReplyLine(line: string): Record<string, unknown> {
  let parsed: unknown;
  try {
    parsed = JSON.parse(line);
  } catch {
    return { error: 'klorb server reply was not valid JSON' };
  }
  if (typeof parsed !== 'object' || parsed === null) {
    return { error: 'klorb server reply was not a JSON object' };
  }
  return parsed as Record<string, unknown>;
}

/**
 * Owns one `klorb server` child process and the JSONL request/response exchange over its
 * stdin/stdout (docs/specs/klorb-server.md). The protocol has no request id, and replies
 * arrive in the same order requests were written, so pending `greet()` calls are tracked as a
 * FIFO queue of resolvers rather than correlated explicitly. `spawnFn` is injected (defaulting
 * to real `child_process.spawn`) so tests can drive this class against a fake process instead
 * of a real `klorb` binary.
 */
export class KlorbServerProcess {
  private readonly _spawnFn: SpawnFn;
  private _child: ChildProcessWithoutNullStreams | undefined;
  private _rl: readline.Interface | undefined;
  private _pending: Array<(reply: Record<string, unknown>) => void> = [];

  public constructor(spawnFn: SpawnFn = defaultSpawnFn) {
    this._spawnFn = spawnFn;
  }

  public get isRunning(): boolean {
    return this._child !== undefined;
  }

  /** Stops any running server, then spawns a fresh `klorb server` with the given options. */
  public start(options: KlorbServerOptions): void {
    this.stop();
    const args = ['server'];
    if (options.configPath !== undefined && options.configPath.length > 0) {
      args.push('--config', options.configPath);
    }
    const child = this._spawnFn(options.command, args, options.env);
    this._child = child;
    this._rl = readline.createInterface({ input: child.stdout });
    this._rl.on('line', (line: string) => this._handleLine(line));
  }

  /** Kills the running server (if any) and fails any requests still awaiting a reply. */
  public stop(): void {
    this._rl?.close();
    this._rl = undefined;
    const child = this._child;
    this._child = undefined;
    if (child !== undefined && !child.killed) {
      child.kill();
    }
    const pending = this._pending;
    this._pending = [];
    pending.forEach((resolve) => resolve({ error: 'klorb server restarted' }));
  }

  /** Sends `{"greet": name}` and resolves with the server's next reply (docs/specs/klorb-server.md). */
  public greet(name: string): Promise<Record<string, unknown>> {
    return new Promise((resolve) => {
      if (this._child === undefined) {
        resolve({ error: 'klorb server is not running' });
        return;
      }
      this._pending.push(resolve);
      this._child.stdin.write(`${JSON.stringify({ greet: name })}\n`);
    });
  }

  private _handleLine(line: string): void {
    const resolve = this._pending.shift();
    if (resolve === undefined) {
      return;
    }
    resolve(parseReplyLine(line));
  }
}
