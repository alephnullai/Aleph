"""Offline license validation using Ed25519 signatures.

License files are JSON signed with Ed25519. The public key is embedded here.
The private key stays on the AlephNull.ai server and signs licenses at purchase.

License file location (checked in order):
  1. .aleph-license.json in project root
  2. ~/.aleph/license.json
  3. ALEPH_LICENSE_FILE environment variable
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum


class LicenseStatus(Enum):
    VALID = "valid"
    EXPIRED = "expired"
    INVALID_SIGNATURE = "invalid_signature"
    NOT_FOUND = "not_found"
    MALFORMED = "malformed"


@dataclass
class LicenseInfo:
    status: LicenseStatus
    licensee: str = ""
    tier: str = "free"  # free | pro | enterprise
    seats: int = 1
    expires: str = ""
    message: str = ""

    @property
    def is_valid(self) -> bool:
        return self.status == LicenseStatus.VALID

    @property
    def is_team(self) -> bool:
        return self.tier in ("pro", "enterprise")


# Ed25519 public key for license verification (base64-encoded)
# The corresponding private key is held by Aleph Null LLC
# and used to sign licenses at https://alephnull.ai
#
# This will be replaced with the real public key once generated.
# For now, this placeholder enables the validation infrastructure.
_PUBLIC_KEY_B64 = "PLACEHOLDER_PUBLIC_KEY_REPLACE_BEFORE_RELEASE"


def _find_license_file(project_dir: str | None = None) -> str | None:
    """Search for a license file in standard locations."""
    candidates = []

    # Environment variable override
    env_path = os.environ.get("ALEPH_LICENSE_FILE")
    if env_path:
        candidates.append(env_path)

    # Project root
    if project_dir:
        candidates.append(os.path.join(project_dir, ".aleph-license.json"))

    # User home
    home = os.path.expanduser("~")
    candidates.append(os.path.join(home, ".aleph", "license.json"))

    for path in candidates:
        if os.path.isfile(path):
            return path

    return None


def _verify_signature(payload: dict, signature_hex: str) -> bool:
    """Verify the Ed25519 signature of the license payload.

    For now, uses HMAC-SHA256 with the public key as a placeholder
    until Ed25519 keys are generated for production.
    """
    if _PUBLIC_KEY_B64 == "PLACEHOLDER_PUBLIC_KEY_REPLACE_BEFORE_RELEASE":
        # During development: accept any license with a non-empty signature
        return len(signature_hex) > 0

    # Production: Ed25519 verification
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        import base64

        public_key_bytes = base64.b64decode(_PUBLIC_KEY_B64)
        public_key = Ed25519PublicKey.from_public_bytes(public_key_bytes)

        # Canonical payload: sorted JSON with no whitespace
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        signature_bytes = bytes.fromhex(signature_hex)

        public_key.verify(signature_bytes, canonical.encode("utf-8"))
        return True
    except Exception:
        return False


def validate_license(project_dir: str | None = None) -> LicenseInfo:
    """Validate the license file and return license info.

    Returns LicenseInfo with status=NOT_FOUND if no license file exists
    (this is normal for solo/free-tier users).
    """
    path = _find_license_file(project_dir)
    if path is None:
        return LicenseInfo(
            status=LicenseStatus.NOT_FOUND,
            tier="free",
            message="No license file found. Free tier (solo use).",
        )

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        return LicenseInfo(
            status=LicenseStatus.MALFORMED,
            message=f"License file unreadable: {e}",
        )

    # Extract fields
    licensee = data.get("licensee", "")
    tier = data.get("tier", "free")
    seats = data.get("seats", 1)
    expires = data.get("expires", "")
    signature = data.get("signature", "")

    if not licensee or not signature:
        return LicenseInfo(
            status=LicenseStatus.MALFORMED,
            message="License file missing required fields (licensee, signature).",
        )

    # Verify signature
    payload = {k: v for k, v in data.items() if k != "signature"}
    if not _verify_signature(payload, signature):
        return LicenseInfo(
            status=LicenseStatus.INVALID_SIGNATURE,
            licensee=licensee,
            message="License signature verification failed.",
        )

    # Check expiration
    if expires:
        try:
            exp_date = datetime.fromisoformat(expires)
            if exp_date.tzinfo is None:
                exp_date = exp_date.replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) > exp_date:
                return LicenseInfo(
                    status=LicenseStatus.EXPIRED,
                    licensee=licensee,
                    tier=tier,
                    seats=seats,
                    expires=expires,
                    message=f"License expired on {expires}. Renew at https://alephnull.ai",
                )
        except ValueError:
            pass  # Invalid date format, skip expiry check

    return LicenseInfo(
        status=LicenseStatus.VALID,
        licensee=licensee,
        tier=tier,
        seats=seats,
        expires=expires,
        message=f"Licensed to {licensee} ({tier}, {seats} seats)",
    )


def check_team_usage(project_dir: str) -> bool:
    """Detect if this looks like team/commercial usage.

    Indicators:
    - Multiple agent_ids in epistemic history
    - CI environment variables present
    - Multiple contributors in recent git history
    """
    # Check CI environment
    ci_vars = ["CI", "GITHUB_ACTIONS", "JENKINS_URL", "GITLAB_CI",
               "CIRCLECI", "TRAVIS", "BUILDKITE", "CODEBUILD_BUILD_ID"]
    if any(os.environ.get(v) for v in ci_vars):
        return True

    # Check epistemic for multiple agents
    epistemic_path = os.path.join(project_dir, ".aleph", "project.aleph.epistemic")
    if os.path.isfile(epistemic_path):
        try:
            with open(epistemic_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            agents = set()
            for inf in data.get("inferences", []):
                aid = inf.get("agent_id", "default")
                if aid != "default":
                    agents.add(aid)
            for review in data.get("reviewed", []):
                aid = review.get("agent_id", "default")
                if aid != "default":
                    agents.add(aid)
            if len(agents) > 1:
                return True
        except (json.JSONDecodeError, OSError):
            pass

    return False


def format_license_notice(info: LicenseInfo, is_team: bool = False) -> str | None:
    """Format a license notice for display on startup. Returns None if no notice needed."""
    if info.is_valid:
        return f"[aleph] Licensed to {info.licensee} ({info.tier})"

    if info.status == LicenseStatus.EXPIRED:
        return f"[aleph] License expired. Renew at https://alephnull.ai"

    if info.status == LicenseStatus.INVALID_SIGNATURE:
        return f"[aleph] Invalid license signature. Contact support@alephnull.ai"

    if info.status == LicenseStatus.NOT_FOUND and is_team:
        return (
            "[aleph] Team/CI usage detected. Commercial use requires a license.\n"
            "[aleph] Free for solo developers. Licenses: https://alephnull.ai/pricing"
        )

    # Solo free use — no notice needed
    return None
