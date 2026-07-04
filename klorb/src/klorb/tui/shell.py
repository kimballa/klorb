# Shell integration for klorb's TUI
# This module provides a small wrapper around launching a user-specified shell command
# in /bin/bash and capturing its stdout/stderr for display in the UI.

import subprocess
from typing import Tuple


class UserShellCommand:
    """Run a single shell command directly entered by the user using /bin/bash.

    The command is executed with -c so that it is interpreted by bash as a simple command line.
    We run this command in a login shell.
    The output is captured and returned as (stdout, stderr, returncode).

    This class trusts the command directly because it came from the user. It must
    not be used to execute a command provided by the model.
    """

    def __init__(self, command: str) -> None:
        self._command = command

    def run(self) -> Tuple[str, str, int]:
        # Execute via /bin/bash -c <command>
        try:
            result = subprocess.run(["/bin/bash", "--login", "-c", self._command],
                                    stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE,
                                    text=True)
            return (result.stdout, result.stderr, result.returncode)
        except FileNotFoundError:
            # /bin/bash not found; return a lightweight error message
            return ("", "/bin/bash not found; cannot execute command", 127)
