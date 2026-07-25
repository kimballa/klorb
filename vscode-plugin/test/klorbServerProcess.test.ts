// © Copyright 2026 Aaron Kimball
import { type ChildProcessWithoutNullStreams } from 'child_process';
import { PassThrough } from 'stream';
import { describe, expect, it } from 'vitest';

import { KlorbServerProcess } from '../src/klorbServerProcess';

function makeFakeChild(): ChildProcessWithoutNullStreams {
  const child = {
    stdin: new PassThrough(),
    stdout: new PassThrough(),
    killed: false,
    kill(): boolean {
      child.killed = true;
      return true;
    },
  } as unknown as ChildProcessWithoutNullStreams;
  return child;
}

describe('KlorbServerProcess', () => {
  it('returns the spawned child from start() and exposes it', () => {
    const child = makeFakeChild();
    const server = new KlorbServerProcess(() => child);

    expect(server.start({ command: 'klorb', env: {} })).toBe(child);
    expect(server.child).toBe(child);
    expect(server.isRunning).toBe(true);
  });

  it('kills the child on stop()', () => {
    const child = makeFakeChild();
    const server = new KlorbServerProcess(() => child);
    server.start({ command: 'klorb', env: {} });

    server.stop();

    expect(child.killed).toBe(true);
    expect(server.isRunning).toBe(false);
    expect(server.child).toBeUndefined();
  });

  it('stops the prior child when start() is called again', () => {
    const first = makeFakeChild();
    const second = makeFakeChild();
    const children = [first, second];
    const server = new KlorbServerProcess(() => {
      const next = children.shift();
      if (next === undefined) {
        throw new Error('spawned more children than expected');
      }
      return next;
    });

    server.start({ command: 'klorb', env: {} });
    server.start({ command: 'klorb', env: {} });

    expect(first.killed).toBe(true);
    expect(second.killed).toBe(false);
    expect(server.child).toBe(second);
  });

  it('spawns without --config when configPath is omitted', () => {
    let spawnArgs: string[] | undefined;
    const server = new KlorbServerProcess((_command, args) => {
      spawnArgs = args;
      return makeFakeChild();
    });
    server.start({ command: 'klorb', env: {} });

    expect(spawnArgs).toEqual(['server']);
  });

  it('spawns without --config when configPath is empty', () => {
    let spawnArgs: string[] | undefined;
    const server = new KlorbServerProcess((_command, args) => {
      spawnArgs = args;
      return makeFakeChild();
    });
    server.start({ command: 'klorb', env: {}, configPath: '' });

    expect(spawnArgs).toEqual(['server']);
  });

  it('passes --config when configPath is set', () => {
    let spawnArgs: string[] | undefined;
    const server = new KlorbServerProcess((_command, args) => {
      spawnArgs = args;
      return makeFakeChild();
    });
    server.start({ command: 'klorb', env: {}, configPath: '/tmp/klorb-config.json' });

    expect(spawnArgs).toEqual(['server', '--config', '/tmp/klorb-config.json']);
  });
});
