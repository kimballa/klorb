# © Copyright 2026 Aaron Kimball
"""Secret-detection redaction filter shared by `ReadFileCore` and `EditFileCore` -- masks
likely credentials (AWS keys, private keys, vendor API tokens, etc, via `detect-secrets`) out
of file content before it reaches a model, and reverses the masking so `EditFileCore` can still
match/write the file's real bytes when a token is echoed back. See
docs/specs/secret-redaction.md.
"""

import hashlib
import logging
import re
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any

from detect_secrets.core.baseline import UnableToReadBaselineError, load_from_file
from detect_secrets.core.potential_secret import PotentialSecret
from detect_secrets.core.scan import scan_line
from detect_secrets.settings import transient_settings

if TYPE_CHECKING:
    from klorb.session import Session

logger = logging.getLogger(__name__)

_TOOL_STATE_KEY = "SecretRedaction"
_TOKEN_MAP_KEY = "token_to_secret"

_TOKEN_PATTERN = re.compile(r"\[\[SECRET:[a-z0-9_]+:[0-9a-f]{12}\]\]")

_SECRETS_BASELINE_FILENAME = "secrets-baseline.json"

SECRET_DETECTION_PLUGINS: tuple[str, ...] = (
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

SECRET_DETECTION_SCAN_LOCK = threading.Lock()
"""`detect-secrets` keeps its plugin/filter configuration in a process-wide
`functools.lru_cache` singleton (`detect_secrets.settings.get_settings()`); serializes
`SecretRedactor.redact()` calls so two sessions scanning concurrently in the same process
can't race on it."""


def load_secrets_baseline(workspace_path: Path, *, trusted: bool) -> frozenset[str]:
    """Load `.klorb/secrets-baseline.json` under `workspace_path` and return every
    `hashed_secret` value it contains as a frozenset, or an empty set if the file doesn't
    exist, is malformed, or the workspace is not trusted.

    The file is expected to be the literal output of `detect-secrets scan
    > .klorb/secrets-baseline.json` (the standard `detect-secrets` baseline format).
    `detect_secrets.core.baseline.load_from_file` handles all parsing and format
    upgrades.

    The returned hashes are SHA-1 digests (the same algorithm `detect-secrets` uses
    internally), so a secret's hash can be compared against them without holding the
    original plaintext.
    """
    from klorb.permissions.directory_access import KLORB_PROJECT_DIR_NAME

    if not trusted:
        return frozenset()

    path = workspace_path / KLORB_PROJECT_DIR_NAME / _SECRETS_BASELINE_FILENAME
    if not path.is_file():
        logger.debug("No secrets baseline at %s; all detected secrets will be redacted.", path)
        return frozenset()

    try:
        raw = load_from_file(str(path))
    except (UnableToReadBaselineError, KeyError) as exc:
        logger.warning("Failed to read secrets baseline %s: %s", path, exc)
        return frozenset()

    results = raw.get("results")
    if not isinstance(results, dict):
        logger.warning("Secrets baseline %s has no 'results' object; ignoring.", path)
        return frozenset()

    hashes: set[str] = set()
    for entries in results.values():
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if isinstance(entry, dict) and entry.get("hashed_secret"):
                hashes.add(str(entry["hashed_secret"]))

    logger.debug("Loaded %d baseline hashes from %s.", len(hashes), path)
    return frozenset(hashes)


class SecretRedactor:
    """Detects likely credentials in file content and replaces each occurrence with a stable
    `[[SECRET:<type>:<hash>]]` token, reversible via `detokenize()` -- so a model never sees a
    plaintext secret via `ReadFile`, but `EditFile` can still match and write the file's real
    bytes when a token is echoed back in `start_text`/`end_text`/`old_text`/`context_before`/
    `context_after`/`new_text`. Holds no state of its own; the token<->plaintext map lives in
    `session.tool_state`, so a `Tool` can share one `SecretRedactor()` across its whole
    lifetime, the same shape as `klorb.tools.util.spill.SpillDir`. See
    docs/specs/secret-redaction.md.

    When `baseline_hashes` is given (SHA-1 digests from a `detect-secrets` baseline file),
    secrets whose hash appears in the set are left un-redacted -- they're known false
    positives the workspace owner has allowlisted. See `load_secrets_baseline()`.
    """

    def __init__(self, baseline_hashes: frozenset[str] = frozenset()) -> None:
        self._baseline_hashes = baseline_hashes

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
        with SECRET_DETECTION_SCAN_LOCK, transient_settings(
            {"plugins_used": [{"name": name} for name in SECRET_DETECTION_PLUGINS]},
        ):
            lines = [self._redact_line(line, token_map) for line in text.split("\n")]
        return "\n".join(lines)

    def _redact_line(self, line: str, token_map: dict[str, str]) -> str:
        for secret in scan_line(line):
            if not secret.secret_value:
                continue
            if self._baseline_hashes and self._is_in_baseline(secret):
                continue
            line = line.replace(secret.secret_value, self._token_for(secret, token_map))
        return line

    def _is_in_baseline(self, secret: PotentialSecret) -> bool:
        """Return True if `secret`'s SHA-1 hash matches a baseline entry."""
        return PotentialSecret.hash_secret(secret.secret_value or "") in self._baseline_hashes

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
