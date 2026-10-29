"""Configuración Git negociada, versionada y aprobada junto al bootstrap."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from typing import Any, Literal, Mapping, Sequence
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError as PydanticValidationError

from .domain import (
    GitConfiguration,
    GitConfigurationStatus,
    GitInitializationStrategy,
    GitIntegrationPolicy,
    GitPublicationPolicy,
    TechnicalCommitPolicy,
)
from .errors import ConflictError, NotFoundError, ValidationError
from .git import GitAdapter
from .github_service import GitHubRegistryService
from .ports import Repository


BLOCK_PATTERN = re.compile(
    r"```vibecoder-git-config\s*\n(?P<body>.*?)\n```", re.DOTALL | re.IGNORECASE
)
AUTHORIZED_OPERATIONS = {
    "CREATE_TECHNICAL_BASE",
    "PUBLISH_BOOTSTRAP_BRANCH",
    "CREATE_PULL_REQUEST",
}


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")


class GitConfigPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    repository_id: str = Field(min_length=1)
    main_branch: str = Field(min_length=1, max_length=128)
    base_branch_exists: bool
    initialization_strategy: GitInitializationStrategy
    technical_commit_policy: TechnicalCommitPolicy
    publication_policy: GitPublicationPolicy = GitPublicationPolicy.PULL_REQUEST
    integration_policy: GitIntegrationPolicy = GitIntegrationPolicy.HUMAN_REVIEW_MERGE
    bootstrap_branch: str = Field(min_length=1, max_length=128)
    authorized_operations: list[str]


class GitConfigBlock(GitConfigPayload):
    confirmation: Literal["proposed", "confirmed"]


class GitConfigurationService:
    def __init__(
        self,
        repository: Repository,
        github: GitHubRegistryService,
        *,
        authorized_users: Sequence[str],
    ):
        self.repository = repository
        self.github = github
        self.authorized_users = {item.casefold() for item in authorized_users}

    def ensure_default_proposal(self, workflow_id: str) -> GitConfiguration:
        existing = self.latest(workflow_id, GitConfigurationStatus.PROPOSED)
        if existing:
            return existing
        workflow = self.repository.get("bootstrap_workflows", workflow_id)
        if str(workflow["status"]) == "APPROVED":
            raise ConflictError("un bootstrap histórico aprobado requiere reconciliación explícita")
        observation = self.github.observe_repository_state(str(workflow["repository_id"]))
        empty = bool(observation["is_empty"])
        main = "main" if empty else str(observation["default_branch"])
        payload = GitConfigPayload(
            repository_id=str(workflow["repository_id"]),
            main_branch=main,
            base_branch_exists=not empty,
            initialization_strategy=(
                GitInitializationStrategy.TECHNICAL_BASE_COMMIT
                if empty else GitInitializationStrategy.EXISTING_BASE
            ),
            technical_commit_policy=(
                TechnicalCommitPolicy.CREATE_MINIMAL if empty else TechnicalCommitPolicy.NONE
            ),
            bootstrap_branch=f"vibecoder/bootstrap-{workflow_id[:8]}",
            authorized_operations=(
                sorted(AUTHORIZED_OPERATIONS)
                if empty else ["CREATE_PULL_REQUEST", "PUBLISH_BOOTSTRAP_BRANCH"]
            ),
        )
        return self.create_revision(
            workflow_id, payload, GitConfigurationStatus.PROPOSED, observation=observation
        )

    def create_revision(
        self,
        workflow_id: str,
        payload: GitConfigPayload | Mapping[str, Any],
        status: GitConfigurationStatus,
        *,
        source_comment_id: int | None = None,
        observation: Mapping[str, Any] | None = None,
    ) -> GitConfiguration:
        if status not in {GitConfigurationStatus.PROPOSED, GitConfigurationStatus.CONFIRMED}:
            raise ValidationError("solo se crean revisiones propuestas o confirmadas")
        workflow = self.repository.get("bootstrap_workflows", workflow_id)
        if str(workflow["status"]) == "APPROVED":
            raise ConflictError("la configuración aprobada está congelada")
        validated = payload if isinstance(payload, GitConfigPayload) else self.validate(payload)
        if validated.repository_id != str(workflow["repository_id"]):
            raise ValidationError("la configuración pertenece a otro repositorio")
        self._validate_consistency(validated)
        observation = observation or self.github.observe_repository_state(validated.repository_id)
        digest = self.hash_payload(validated)
        existing = self.repository.find_one(
            "git_configurations",
            workflow_id=workflow_id,
            configuration_hash=digest,
            status=status.value,
        )
        if existing:
            return self._from_row(existing)
        revisions = self.repository.list("git_configurations", workflow_id=workflow_id)
        now = _now()
        config_id = str(uuid4())
        self.repository.insert(
            "git_configurations",
            {
                "id": config_id,
                "workflow_id": workflow_id,
                "repository_id": validated.repository_id,
                "revision": max((int(item["revision"]) for item in revisions), default=0) + 1,
                "status": status.value,
                "schema_version": validated.schema_version,
                "config_json": self.canonical_json(validated),
                "configuration_hash": digest,
                "source_comment_id": source_comment_id,
                "observed_github_id": int(observation["github_id"]),
                "observed_is_empty": int(bool(observation["is_empty"])),
                "observed_default_branch": observation.get("default_branch"),
                "observed_head_sha": observation.get("head_sha"),
                "specification_hash": None,
                "approved_by": None,
                "approved_event_id": None,
                "applied_at": None,
                "created_at": now,
                "updated_at": now,
            },
        )
        return self.get(config_id)

    def confirm_block(
        self, workflow_id: str, body: str, *, author: str, comment_id: int
    ) -> GitConfiguration | None:
        blocks = self.parse_blocks(body)
        if not blocks:
            return None
        if len(blocks) != 1:
            raise ValidationError("el comentario contiene más de un bloque Git y es ambiguo")
        if author.casefold() not in self.authorized_users:
            raise ValidationError("solo un usuario autorizado puede confirmar configuración Git")
        block = blocks[0]
        if block.confirmation != "confirmed":
            raise ValidationError("el bloque humano debe declarar confirmation=confirmed")
        payload = GitConfigPayload.model_validate(block.model_dump(exclude={"confirmation"}))
        return self.create_revision(
            workflow_id,
            payload,
            GitConfigurationStatus.CONFIRMED,
            source_comment_id=comment_id,
        )

    def record_proposal_block(self, workflow_id: str, body: str) -> GitConfiguration | None:
        blocks = self.parse_blocks(body)
        if not blocks:
            return None
        if len(blocks) != 1 or blocks[0].confirmation != "proposed":
            raise ValidationError("la respuesta del agente no contiene una propuesta Git inequívoca")
        payload = GitConfigPayload.model_validate(blocks[0].model_dump(exclude={"confirmation"}))
        return self.create_revision(workflow_id, payload, GitConfigurationStatus.PROPOSED)

    def approve(
        self,
        workflow_id: str,
        *,
        specification_hash: str,
        approved_by: str,
        approved_event_id: int,
    ) -> GitConfiguration:
        approved = self.latest(workflow_id, GitConfigurationStatus.APPROVED)
        if approved:
            if (
                approved.specification_hash != specification_hash
                or approved.approved_by != approved_by
                or approved.approved_event_id != approved_event_id
            ):
                raise ConflictError("la configuración ya fue aprobada con otra autorización")
            return approved
        confirmed = self.latest(workflow_id, GitConfigurationStatus.CONFIRMED)
        if confirmed is None:
            raise ConflictError("falta una configuración Git confirmada")
        self.repository.update(
            "git_configurations",
            confirmed.id,
            {
                "status": GitConfigurationStatus.APPROVED.value,
                "specification_hash": specification_hash,
                "approved_by": approved_by,
                "approved_event_id": approved_event_id,
                "updated_at": _now(),
            },
        )
        return self.get(confirmed.id)

    def mark_applied(self, configuration_id: str) -> GitConfiguration:
        config = self.get(configuration_id)
        if config.status is GitConfigurationStatus.APPLIED:
            return config
        if config.status is not GitConfigurationStatus.APPROVED:
            raise ConflictError("solo una configuración aprobada puede marcarse aplicada")
        now = _now()
        self.repository.update(
            "git_configurations", config.id,
            {"status": GitConfigurationStatus.APPLIED.value, "applied_at": now, "updated_at": now},
        )
        return self.get(config.id)

    def check_remote(self, configuration_id: str) -> Mapping[str, Any]:
        config = self.get(configuration_id)
        current = self.github.observe_repository_state(config.repository_id)
        mismatches: list[str] = []
        if int(current["github_id"]) != config.observed_github_id:
            mismatches.append("identidad del repositorio")
        if bool(current["is_empty"]) != config.observed_is_empty:
            mismatches.append("estado vacío/con commits")
        if current.get("default_branch") != config.observed_default_branch:
            mismatches.append("rama por defecto")
        if current.get("head_sha") != config.observed_head_sha:
            mismatches.append("HEAD remoto")
        return {"compatible": not mismatches, "mismatches": mismatches, "current": current}

    def get(self, configuration_id: str) -> GitConfiguration:
        return self._from_row(self.repository.get("git_configurations", configuration_id))

    def latest(
        self, workflow_id: str, status: GitConfigurationStatus | None = None
    ) -> GitConfiguration | None:
        criteria: dict[str, object] = {"workflow_id": workflow_id}
        if status:
            criteria["status"] = status.value
        rows = self.repository.list("git_configurations", **criteria)
        if not rows:
            return None
        return self._from_row(max(rows, key=lambda item: int(item["revision"])))

    def approved(self, workflow_id: str) -> GitConfiguration | None:
        return self.latest(workflow_id, GitConfigurationStatus.APPLIED) or self.latest(
            workflow_id, GitConfigurationStatus.APPROVED
        )

    @staticmethod
    def contains_block(body: str) -> bool:
        return "```vibecoder-git-config" in body.casefold()

    @staticmethod
    def parse_blocks(body: str) -> list[GitConfigBlock]:
        blocks: list[GitConfigBlock] = []
        for match in BLOCK_PATTERN.finditer(body):
            try:
                raw = json.loads(match.group("body"))
                blocks.append(GitConfigBlock.model_validate(raw))
            except (json.JSONDecodeError, PydanticValidationError) as exc:
                raise ValidationError(f"bloque de configuración Git inválido: {exc}") from exc
        if GitConfigurationService.contains_block(body) and not blocks:
            raise ValidationError("bloque de configuración Git incompleto")
        return blocks

    @staticmethod
    def validate(payload: Mapping[str, Any]) -> GitConfigPayload:
        try:
            validated = GitConfigPayload.model_validate(payload)
        except PydanticValidationError as exc:
            raise ValidationError(f"configuración Git inválida: {exc}") from exc
        GitConfigurationService._validate_consistency(validated)
        return validated

    @staticmethod
    def _validate_consistency(payload: GitConfigPayload) -> None:
        GitAdapter.validate_branch(payload.main_branch)
        GitAdapter.validate_branch(payload.bootstrap_branch)
        operations = set(payload.authorized_operations)
        if len(operations) != len(payload.authorized_operations) or not operations <= AUTHORIZED_OPERATIONS:
            raise ValidationError("operaciones Git autorizadas inválidas o duplicadas")
        if payload.base_branch_exists:
            if (
                payload.initialization_strategy is not GitInitializationStrategy.EXISTING_BASE
                or payload.technical_commit_policy is not TechnicalCommitPolicy.NONE
                or "CREATE_TECHNICAL_BASE" in operations
            ):
                raise ValidationError("configuración incompatible con una rama base existente")
        else:
            required = AUTHORIZED_OPERATIONS
            if (
                payload.initialization_strategy is not GitInitializationStrategy.TECHNICAL_BASE_COMMIT
                or payload.technical_commit_policy is not TechnicalCommitPolicy.CREATE_MINIMAL
                or operations != required
            ):
                raise ValidationError("un repositorio vacío requiere la estrategia técnica completa")
        if payload.bootstrap_branch == payload.main_branch:
            raise ValidationError("la rama bootstrap debe ser distinta de la principal")

    @staticmethod
    def canonical_json(payload: GitConfigPayload) -> str:
        data = payload.model_dump(mode="json")
        data["authorized_operations"] = sorted(data["authorized_operations"])
        return json.dumps(
            data, sort_keys=True, separators=(",", ":"),
            ensure_ascii=False,
        )

    @staticmethod
    def hash_payload(payload: GitConfigPayload) -> str:
        return hashlib.sha256(GitConfigurationService.canonical_json(payload).encode()).hexdigest()

    @staticmethod
    def render(config: GitConfiguration, *, confirmation: str | None = None) -> str:
        payload = GitConfigurationService._payload(config).model_dump(mode="json")
        payload["authorized_operations"] = sorted(payload["authorized_operations"])
        payload["confirmation"] = confirmation or (
            "confirmed" if config.status is GitConfigurationStatus.CONFIRMED else "proposed"
        )
        return "```vibecoder-git-config\n" + json.dumps(
            payload, indent=2, sort_keys=True, ensure_ascii=False
        ) + "\n```"

    @staticmethod
    def _payload(config: GitConfiguration) -> GitConfigPayload:
        return GitConfigPayload(
            schema_version=config.schema_version,
            repository_id=config.repository_id,
            main_branch=config.main_branch,
            base_branch_exists=config.base_branch_exists,
            initialization_strategy=config.initialization_strategy,
            technical_commit_policy=config.technical_commit_policy,
            publication_policy=config.publication_policy,
            integration_policy=config.integration_policy,
            bootstrap_branch=config.bootstrap_branch,
            authorized_operations=list(config.authorized_operations),
        )

    @staticmethod
    def _from_row(row: Mapping[str, Any]) -> GitConfiguration:
        payload = GitConfigPayload.model_validate_json(str(row["config_json"]))
        return GitConfiguration(
            id=str(row["id"]), workflow_id=str(row["workflow_id"]),
            repository_id=str(row["repository_id"]), revision=int(row["revision"]),
            status=GitConfigurationStatus(str(row["status"])),
            schema_version=payload.schema_version, main_branch=payload.main_branch,
            base_branch_exists=payload.base_branch_exists,
            initialization_strategy=payload.initialization_strategy,
            technical_commit_policy=payload.technical_commit_policy,
            publication_policy=payload.publication_policy,
            integration_policy=payload.integration_policy,
            bootstrap_branch=payload.bootstrap_branch,
            authorized_operations=tuple(payload.authorized_operations),
            configuration_hash=str(row["configuration_hash"]),
            source_comment_id=int(row["source_comment_id"]) if row["source_comment_id"] else None,
            observed_github_id=int(row["observed_github_id"]),
            observed_is_empty=bool(row["observed_is_empty"]),
            observed_default_branch=(
                str(row["observed_default_branch"]) if row["observed_default_branch"] else None
            ),
            observed_head_sha=str(row["observed_head_sha"]) if row["observed_head_sha"] else None,
            specification_hash=str(row["specification_hash"]) if row["specification_hash"] else None,
            approved_by=str(row["approved_by"]) if row["approved_by"] else None,
            approved_event_id=int(row["approved_event_id"]) if row["approved_event_id"] else None,
            applied_at=str(row["applied_at"]) if row["applied_at"] else None,
            created_at=str(row["created_at"]), updated_at=str(row["updated_at"]),
        )
