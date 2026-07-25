// © Copyright 2026 Aaron Kimball
import { type ChildProcessWithoutNullStreams, spawn } from 'child_process';

/** Spawns a child process, given the same arguments as Node's own `child_process.spawn`,
 * split out so tests can inject a fake process without spawning anything real. */
export type SpawnFn = (
  command: string,
  args: string[],
  env: NodeJS.ProcessEnv,
  cwd: string
) => ChildProcessWithoutNullStreams;

/** Where to find `klorb` and what environment to launch it with. */
export interface KlorbServerOptions {
  command: string;
  env: NodeJS.ProcessEnv;
  /** Path to an additional klorb-config.json file, passed to `klorb server` as `--config`
   * when non-empty (see docs/specs/klorb-server.md). */
  configPath?: string;
}

const defaultSpawnFn: SpawnFn = (command, args, env, cwd) => spawn(command, args, { env, cwd });

/**
 * Owns the one `klorb server` child process: spawning it with the right arguments and
 * environment, killing it, and restarting it. The ACP protocol spoken over the child's
 * stdin/stdout is owned by `AcpConnection` (src/host/features/acp/acpConnection.ts), which
 * binds to the `ChildProcessWithoutNullStreams` this class returns from `start()`. `spawnFn` is
 * injected (defaulting to real `child_process.spawn`) so tests can drive this class against a
 * fake process instead of a real `klorb` binary.
 */
export class KlorbServerProcess {
  private readonly _spawnFn: SpawnFn;
  private _child: ChildProcessWithoutNullStreams | undefined;

  public constructor(spawnFn: SpawnFn = defaultSpawnFn) {
    this._spawnFn = spawnFn;
  }

  public get isRunning(): boolean {
    return this._child !== undefined;
  }

  /** The running child process, if any. */
  public get child(): ChildProcessWithoutNullStreams | undefined {
    return this._child;
  }

  /**
   * Stops any running server, then spawns a fresh `klorb server` with the given options,
   * returning the new child process so the caller can bind a protocol connection to its
   * stdio streams. `cwd` is the workspace folder the session will be opened against — it
   * becomes the server process's own working directory, which is what `ProcessConfig`
   * resolution (`klorb.process_config.load_process_config`) uses to find and trust the
   * project's `.klorb/klorb-config.json` (see docs/specs/klorb-server.md and
   * docs/specs/projects-and-trust.md). Without it, the server inherits the extension host's
   * own cwd instead of the open workspace, and every workspace-scoped permission grant
   * (e.g. `readDirs`) is resolved against the wrong directory.
   */
  public start(options: KlorbServerOptions, cwd: string): ChildProcessWithoutNullStreams {
    this.stop();
    const args = ['server'];
    if (options.configPath !== undefined && options.configPath.length > 0) {
      args.push('--config', options.configPath);
    }
    const child = this._spawnFn(options.command, args, options.env, cwd);
    this._child = child;
    return child;
  }

  /** Kills the running server, if any. */
  public stop(): void {
    const child = this._child;
    this._child = undefined;
    if (child !== undefined && !child.killed) {
      child.kill();
    }
  }
}
