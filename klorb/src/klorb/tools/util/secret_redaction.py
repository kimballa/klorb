# © Copyright 2026 Aaron Kimball
"""Secret-detection redaction filter shared by `ReadFileCore` and `EditFileCore` -- masks
likely credentials (AWS keys, private keys, vendor API tokens, etc, via `detect-secrets`) out
of file content before it reaches a model, and reverses the masking so `EditFileCore` can still
match/write the file's real bytes when a token is echoed back. See
docs/specs/secret-redaction.md.
"""

import hashlib
import re
import threading
from typing import TYPE_CHECKING, Any

from detect_secrets.core.potential_secret import PotentialSecret
from detect_secrets.core.scan import scan_line
from detect_secrets.settings import transient_settings

if TYPE_CHECKING:
    from klorb.session import Session

_TOOL_STATE_KEY = "SecretRedaction"
_TOKEN_MAP_KEY = "token_to_secret"

_TOKEN_PATTERN = re.compile(r"\[\[SECRET:[a-z0-9_]+:[0-9a-f]{12}\]\]")

_PLUGINS = (
    "AWSKeyDetector", "ArtifactoryDetector", "AzureStorageKeyDetector", "BasicAuthDetector",
    "CloudantDetector", "DiscordBotTokenDetector", "GitHubTokenDetector", "GitLabTokenDetector",
    "IbmCloudIamDetector", "IbmCosHmacDetector", "JwtTokenDetector", "KeywordDetector",
    "MailchimpDetector", "NpmDetector", "OpenAIDetector", "PrivateKeyDetector",
    "PypiTokenDetector", "SendGridDetector", "SlackDetector", "SoftlayerDetector",
    "SquareOAuthDetector", "StripeDetector", "TelegramBotTokenDetector", "TwilioKeyDetector",
)
"""`detect-secrets` plugin classnames this filter scans with -- every credential-shaped
vendor/format detector it ships, deliberately excluding `Base64HighEntropyString`/
`HexHighEntropyString` (trip constantly on ordinary hashes, UUIDs, and base64 blobs) and
`IPPublicDetector` (an IP address isn't a credential). See docs/specs/secret-redaction.md."""

_scan_lock = threading.Lock()
"""`detect-secrets` keeps its plugin/filter configuration in a process-wide
`functools.lru_cache` singleton (`detect_secrets.settings.get_settings()`); serializes
`SecretRedactor.redact()` calls so two sessions scanning concurrently in the same process
can't race on it."""


class SecretRedactor:
    """Detects likely credentials in file content and replaces each occurrence with a stable
    `[[SECRET:<type>:<hash>]]` token, reversible via `detokenize()` -- so a model never sees a
    plaintext secret via `ReadFile`, but `EditFile` can still match and write the file's real
    bytes when a token is echoed back in `start_text`/`end_text`/`old_text`/`context_before`/
    `context_after`/`new_text`. Holds no state of its own; the token<->plaintext map lives in
    `session.tool_state`, so a `Tool` can share one `SecretRedactor()` across its whole
    lifetime, the same shape as `klorb.tools.util.spill.SpillDir`. See
    docs/specs/secret-redaction.md.
    """

    def redact(self, session: "Session | None", text: str) -> str:
        """Scan `text` (typically one `ReadFile`/`EditFile` call's raw line content) for
        likely credentials and replace each one with its token, recording the token<->
        plaintext mapping in `session.tool_state` for a later `detokenize()` call to reverse.
        A `None` session (e.g. a `ToolSetupContext` built directly in a unit test) still
        redacts, just without a map that survives past this one call.
        """
        if not text:
            return text
        token_map = self._token_map(session)
        with _scan_lock, transient_settings(
            {"plugins_used": [{"name": name} for name in _PLUGINS]},
        ):
            lines = [self._redact_line(line, token_map) for line in text.split("\n")]
        return "\n".join(lines)

    def _redact_line(self, line: str, token_map: dict[str, str]) -> str:
        for secret in scan_line(line):
            if not secret.secret_value:
                continue
            line = line.replace(secret.secret_value, self._token_for(secret, token_map))
        return line

    @staticmethod
    def _token_for(secret: PotentialSecret, token_map: dict[str, str]) -> str:
        """Return `secret`'s token, deriving it from a hash of its plaintext so the same
        secret value always resolves to the same token -- a re-read of the same file (or the
        same secret appearing at a second location) doesn't mint a new one."""
        assert secret.secret_value is not None
        slug = re.sub(r"[^a-z0-9]+", "_", secret.type.lower()).strip("_")
        digest = hashlib.sha256(secret.secret_value.encode("utf-8")).hexdigest()[:12]
        token = f"[[SECRET:{slug}:{digest}]]"
        token_map[token] = secret.secret_value
        return token

    def detokenize(self, session: "Session | None", text: str) -> str:
        """Substitute every known token in `text` back to its real plaintext -- `EditFileCore`'s
        hook for matching/writing the file's real bytes when a model echoes a token from an
        earlier `ReadFile` back into `start_text`/`end_text`/`old_text`/`context_before`/
        `context_after`/`new_text`. A token with no known mapping (a different session, or one
        the model fabricated) is left as literal text rather than raising."""
        if "[[SECRET:" not in text:
            return text
        token_map = self._token_map(session)
        for token in _TOKEN_PATTERN.findall(text):
            secret = token_map.get(token)
            if secret is not None:
                text = text.replace(token, secret)
        return text

    @staticmethod
    def _token_map(session: "Session | None") -> dict[str, str]:
        if session is None:
            return {}
        state: dict[str, Any] = session.tool_state.setdefault(_TOOL_STATE_KEY, {})
        return state.setdefault(_TOKEN_MAP_KEY, {})  # type: ignore[no-any-return]
