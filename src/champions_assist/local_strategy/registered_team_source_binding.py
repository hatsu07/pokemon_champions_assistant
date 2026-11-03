"""#141②⑤: off-match, read-only registered-team/source identity cross-check.

Checks independently recomputed local registered-team CSV, repository index,
and source-hashed LIVE-asset manifest. It is NOT real-video authentication,
Champions simulator compatibility, or proof that a submitted match used the
declared team. Never invoke from timed battle/Qt callback.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
import re
from typing import Any

from ..app_config import ConfigurationError
from ..team_management import TeamDocument, TeamRepository, regulation_scope
from ..team_prep_readiness import team_readiness
from ..team_live_assets import GENERATED_VERSION

_ID = re.compile(r"[a-f0-9]{32}\Z")
_SHA = re.compile(r"[a-f0-9]{64}\Z")
# team_readiness sets matrix/lookup/team_match=True BEFORE checking the
# candidate's counts and AI cell invariants. Only its *final* successful
# integrity states can establish current local source identity.
_CURRENT_ASSET_INTEGRITY_STATUSES = frozenset({
    "評価検証済み", "条件付き準備", "対面データ未評価",
})


@dataclass(frozen=True)
class RegisteredSourceContext:
    data_root: Path
    master_dir: Path
    environment_snapshot: Path | None = None


@dataclass(frozen=True)
class RegisteredTeamSourceBinding:
    status: str
    reason: str
    source_identity_cross_bound: bool = False
    registered_team_sha256: str = ""
    team_id: str = ""
    fingerprint: str = ""
    # File and ledger equality are not independent evidence of game/video.
    game_capture_independently_authenticated: bool = False
    champions_mechanics_verified: bool = False
    phase2_accepted: bool = False


def _no(reason: str) -> RegisteredTeamSourceBinding:
    return RegisteredTeamSourceBinding("REGISTERED_SOURCE_BINDING_REFUSED", reason)


def bind_registered_team_sources(
    context: RegisteredSourceContext, *,
    team_id: str, team_fingerprint: str,
    frozen_team_sha256: str, regulation: str,
) -> RegisteredTeamSourceBinding:
    """Verify the THREE names are tied to the same real local registered CSV.

    This function never accepts a caller-supplied 'trusted binding' or an
    asserted 'verified' flag. It reads the team index and rebuilt/fresh
    team-scoped LIVE-asset outputs via existing source checks.
    """
    if (not isinstance(context, RegisteredSourceContext)
            or not isinstance(team_id, str) or _ID.fullmatch(team_id) is None
            or not isinstance(team_fingerprint, str)
            or _SHA.fullmatch(team_fingerprint) is None
            or not isinstance(frozen_team_sha256, str)
            or _SHA.fullmatch(frozen_team_sha256) is None
            or not isinstance(regulation, str) or not regulation):
        return _no("INVALID_REGISTERED_ID_OR_CONTEXT")
    try:
        root = Path(context.data_root).resolve()
        master = Path(context.master_dir).resolve()
        index_dir = (root / "teams").resolve()
        csv_path = index_dir / f"{team_id}.csv"
        if not csv_path.is_file() or not (index_dir / "index.json").is_file():
            return _no("REGISTERED_TEAM_INDEX_OR_CSV_MISSING")
        record = TeamRepository(index_dir, catalog=None).get(team_id)
        # A tampered index must not point to an arbitrary CSV or old team
        # under a different ID. Do not rely on its stored SHA alone.
        if Path(record.csv_path).resolve() != csv_path.resolve():
            return _no("REGISTERED_TEAM_CSV_PATH_MISMATCH")
        raw = csv_path.read_bytes()
        if not raw or sha256(raw).hexdigest() != frozen_team_sha256:
            return _no("REGISTERED_TEAM_RAW_CSV_SHA_MISMATCH")
        doc = TeamDocument.load(csv_path)
        if (len(doc.members) != 6
                or len({m.get("name") for m in doc.members}) != 6
                or record.sha256 != doc.sha256()):
            return _no("REGISTERED_TEAM_CANONICAL_DOCUMENT_MISMATCH")
        active_reg, member_regs = regulation_scope(doc)
        if (active_reg != regulation
                or len(member_regs) != 6
                or any(x != regulation for x in member_regs)):
            return _no("REGISTERED_TEAM_REGULATION_MISMATCH")
        ready = team_readiness(
            root, team_id, csv_path, master_dir=master,
            environment_snapshot=context.environment_snapshot)
        if (ready.get("fingerprint") != team_fingerprint
                or not ready.get("team_match")
                or not ready.get("matrix")
                or not ready.get("lookup")
                or ready.get("stale_reasons")
                or ready.get("status") not in
                    _CURRENT_ASSET_INTEGRITY_STATUSES):
            return _no("SOURCE_ASSET_MANIFEST_NOT_CURRENT_OR_MATCHED")
        path = root / "live-team-assets" / team_id / team_fingerprint / "manifest.json"
        if not path.is_file() or not 1 <= path.stat().st_size <= 2_000_000:
            return _no("LIVE_ASSET_MANIFEST_MISSING")
        import json
        manifest: Any = json.loads(path.read_text(encoding="utf-8"))
        if (not isinstance(manifest, dict)
                or manifest.get("schema") != GENERATED_VERSION
                or manifest.get("team_id") != team_id
                or manifest.get("fingerprint") != team_fingerprint
                or manifest.get("team_file") != csv_path.name
                or manifest.get("team_regulation") != regulation):
            return _no("REGISTERED_TEAM_MANIFEST_IDENTITY_MISMATCH")
        if (sha256(csv_path.read_bytes()).hexdigest() != frozen_team_sha256
                or TeamRepository(index_dir, catalog=None).get(team_id).sha256
                   != record.sha256):
            return _no("REGISTERED_TEAM_CHANGED_DURING_AUDIT")
        return RegisteredTeamSourceBinding(
            status="LOCAL_REGISTERED_SOURCES_CROSS_BOUND",
            reason="LOCAL_FILES_ONLY_NOT_GAME_CAPTURE_PROOF",
            source_identity_cross_bound=True,
            registered_team_sha256=frozen_team_sha256,
            team_id=team_id,
            fingerprint=team_fingerprint,
        )
    except (ConfigurationError, FileNotFoundError, PermissionError,
            IsADirectoryError, OSError, ValueError, TypeError, KeyError):
        return _no("REGISTERED_TEAM_OR_MANIFEST_UNREADABLE")
