# © Copyright 2026 Aaron Kimball
"""Tests for klorb.server.jsonl_server."""

import io
import json

from klorb.server.jsonl_server import JsonlServer


def _run(input_text: str) -> tuple[int, list[dict]]:
    stdin = io.StringIO(input_text)
    stdout = io.StringIO()
    exit_code = JsonlServer(stdin=stdin, stdout=stdout).run()
    replies = [json.loads(line) for line in stdout.getvalue().splitlines() if line]
    return exit_code, replies


def test_run_returns_zero_at_eof() -> None:
    exit_code, replies = _run("")
    assert exit_code == 0
    assert replies == []


def test_greet_replies_with_hello_message() -> None:
    exit_code, replies = _run('{"greet": "Ada"}\n')
    assert exit_code == 0
    assert replies == [{"message": "hello, Ada!"}]


def test_greet_leading_and_trailing_whitespace_is_stripped() -> None:
    exit_code, replies = _run('  {"greet": "Ada"}  \n')
    assert exit_code == 0
    assert replies == [{"message": "hello, Ada!"}]


def test_multiple_greet_commands_each_get_a_reply() -> None:
    exit_code, replies = _run('{"greet": "Ada"}\n{"greet": "Grace"}\n')
    assert exit_code == 0
    assert replies == [{"message": "hello, Ada!"}, {"message": "hello, Grace!"}]


def test_blank_lines_are_skipped() -> None:
    exit_code, replies = _run('\n   \n{"greet": "Ada"}\n\n')
    assert exit_code == 0
    assert replies == [{"message": "hello, Ada!"}]


def test_shutdown_stops_the_loop_before_later_lines() -> None:
    exit_code, replies = _run('{"action": "shutdown"}\n{"greet": "Ada"}\n')
    assert exit_code == 0
    assert replies == []


def test_shutdown_after_other_commands_stops_the_loop() -> None:
    exit_code, replies = _run('{"greet": "Ada"}\n{"action": "shutdown"}\n{"greet": "Grace"}\n')
    assert exit_code == 0
    assert replies == [{"message": "hello, Ada!"}]


def test_malformed_json_returns_error_and_continues() -> None:
    exit_code, replies = _run('not json\n{"greet": "Ada"}\n')
    assert exit_code == 0
    assert len(replies) == 2
    assert "error" in replies[0]
    assert replies[1] == {"message": "hello, Ada!"}


def test_non_object_json_returns_error() -> None:
    exit_code, replies = _run('[1, 2, 3]\n')
    assert exit_code == 0
    assert replies == [{"error": "record must be a JSON object"}]


def test_unrecognized_command_returns_error() -> None:
    exit_code, replies = _run('{"foo": "bar"}\n')
    assert exit_code == 0
    assert replies == [{"error": "unrecognized command"}]


def test_greet_with_non_string_value_returns_error() -> None:
    exit_code, replies = _run('{"greet": 5}\n')
    assert exit_code == 0
    assert replies == [{"error": "'greet' must be a string"}]


def test_reply_is_written_as_a_single_terminated_line() -> None:
    stdin = io.StringIO('{"greet": "Ada"}\n')
    stdout = io.StringIO()
    JsonlServer(stdin=stdin, stdout=stdout).run()
    assert stdout.getvalue() == '{"message": "hello, Ada!"}\n'
