// © Copyright 2026 Aaron Kimball
import { type ChildProcessWithoutNullStreams } from 'child_process';
import { PassThrough } from 'stream';
import { describe, expect, it } from 'vitest';

import { KlorbServerProcess, parseReplyLine } from '../src/klorbServerProcess';

describe('parseReplyLine', () => {
  it('parses a JSON object reply', () => {
    expect(parseReplyLine('{"message":"hello, Ada!"}')).toEqual({ message: 'hello, Ada!' });
  });

  it('reports invalid JSON as an error object', () => {
    expect(parseReplyLine('not json')).toEqual({ error: 'klorb server reply was not valid JSON' });
  });

  it('reports a non-object JSON value as an error object', () => {
    expect(parseReplyLine('42')).toEqual({ error: 'klorb server reply was not a JSON object' });
  });
});

function makeFakeChild(): { child: ChildProcessWithoutNullStreams; stdout: PassThrough; written: string[] } {
  const stdin = new PassThrough();
  const stdout = new PassThrough();
  const written: string[] = [];
  stdin.on('data', (chunk: Buffer) => written.push(chunk.toString()));
  const child = {
    stdin,
    stdout,
    killed: false,
    kill(): boolean {
      child.killed = true;
      return true;
    },
  } as unknown as ChildProcessWithoutNullStreams;
  return { child, stdout, written };
}

describe('KlorbServerProcess', () => {
  it('resolves greet() with the reply on the matching stdout line', async () => {
    const { child, stdout, written } = makeFakeChild();
    const server = new KlorbServerProcess(() => child);
    server.start({ command: 'klorb', env: {} });

    const reply = server.greet('Ada');
    stdout.write('{"message":"hello, Ada!"}\n');

    await expect(reply).resolves.toEqual({ message: 'hello, Ada!' });
    expect(written).toEqual([`${JSON.stringify({ greet: 'Ada' })}\n`]);
  });

  it('matches replies to requests in FIFO order', async () => {
    const { child, stdout } = makeFakeChild();
    const server = new KlorbServerProcess(() => child);
    server.start({ command: 'klorb', env: {} });

    const first = server.greet('Ada');
    const second = server.greet('Grace');
    stdout.write('{"message":"hello, Ada!"}\n{"message":"hello, Grace!"}\n');

    await expect(first).resolves.toEqual({ message: 'hello, Ada!' });
    await expect(second).resolves.toEqual({ message: 'hello, Grace!' });
  });

  it('fails pending requests when the server is stopped', async () => {
    const { child } = makeFakeChild();
    const server = new KlorbServerProcess(() => child);
    server.start({ command: 'klorb', env: {} });

    const reply = server.greet('Ada');
    server.stop();

    await expect(reply).resolves.toEqual({ error: 'klorb server restarted' });
    expect(child.killed).toBe(true);
  });

  it('resolves greet() immediately when no server is running', async () => {
    const server = new KlorbServerProcess();
    await expect(server.greet('Ada')).resolves.toEqual({ error: 'klorb server is not running' });
  });

  it('spawns without --config when configPath is omitted', () => {
    const { child } = makeFakeChild();
    let spawnArgs: string[] | undefined;
    const server = new KlorbServerProcess((_command, args) => {
      spawnArgs = args;
      return child;
    });
    server.start({ command: 'klorb', env: {} });

    expect(spawnArgs).toEqual(['server']);
  });

  it('spawns without --config when configPath is empty', () => {
    const { child } = makeFakeChild();
    let spawnArgs: string[] | undefined;
    const server = new KlorbServerProcess((_command, args) => {
      spawnArgs = args;
      return child;
    });
    server.start({ command: 'klorb', env: {}, configPath: '' });

    expect(spawnArgs).toEqual(['server']);
  });

  it('passes --config when configPath is set', () => {
    const { child } = makeFakeChild();
    let spawnArgs: string[] | undefined;
    const server = new KlorbServerProcess((_command, args) => {
      spawnArgs = args;
      return child;
    });
    server.start({ command: 'klorb', env: {}, configPath: '/tmp/klorb-config.json' });

    expect(spawnArgs).toEqual(['server', '--config', '/tmp/klorb-config.json']);
  });
});
