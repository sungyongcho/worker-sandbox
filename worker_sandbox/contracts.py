"""Typed wire contracts for the worker benchmark controller."""
from __future__ import annotations

import hashlib
from datetime import datetime
import json
import math
from typing import Annotated, Literal, TypeVar

import msgspec

FORMAT = "benchkit"
Text = Annotated[str, msgspec.Meta(min_length=1)]
Digest = Annotated[str, msgspec.Meta(pattern=r"^sha256:[0-9a-f]{64}$")]
Nonnegative = Annotated[int, msgspec.Meta(ge=0)]
Positive = Annotated[int, msgspec.Meta(gt=0)]
# One explicit native setting value; no placeholder may stand in for a frozen choice.
Setting = Annotated[str, msgspec.Meta(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")]
# Cells are defined by each frozen campaign; a run records the name of its cell.
CellName = Annotated[str, msgspec.Meta(pattern=r"^[A-Z][A-Z0-9_]{0,31}$")]
# How a run ends after the implementer's first native final: at once, through the Jev gate,
# or after one fixed generic re-check instruction.
Gate = Literal["off", "jev", "generic"]
Profile = Literal["default", "custom"]
_PLACEHOLDERS = {"unverified", "latest", "auto", "default"}
ReviewAction = Literal['repair', 'complete', 'external_blocker']
# A Noul answer: the probability, from 0 to 1, that the answer is yes.
Noul = Annotated[float, msgspec.Meta(ge=0, le=1)]
RunKind = Literal["pilot", "main"]
State = Literal["CREATED", "PREPARED", "RUNNING", "STOPPING", "STOP_FAILED", "STOPPED", "SEALED", "DISPOSED"]
Outcome = Literal["completed", "incomplete", "cancelled", "provider_error", "protocol_error", "harness_error", "interrupted"]
# Task outcomes form the primary analysis set; every other outcome is an infrastructure failure.
TASK_OUTCOMES = frozenset({"completed", "incomplete"})
TokenName = Literal["input_tokens", "cached_input_tokens", "output_tokens", "reasoning_tokens"]
Cleanup = Literal["pending", "confirmed", "failed"]


class ContractError(ValueError):
    """An input or persisted contract is invalid."""


class Halt(Exception):
    """A known run outcome propagated through nested native invocations."""

    def __init__(self, outcome: Outcome, detail: str):
        super().__init__(detail)
        self.outcome = outcome


def timestamp(value: str) -> datetime:
    try:
        result = datetime.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ContractError("expected an ISO timestamp with a timezone") from exc
    if result.tzinfo is None or result.utcoffset() is None:
        raise ContractError("timestamp must include a timezone")
    return result


class Model(msgspec.Struct, frozen=True, kw_only=True, forbid_unknown_fields=True):
    pass


class Document(Model):
    format: Literal["benchkit"]


class ArtifactRef(Model):
    path: Text
    digest: Digest
    size: Nonnegative
    source: Text = "controller"
    executable: bool = False
    kind: Literal['file', 'symlink'] = 'file'
    target: str | None = None

    def __post_init__(self):
        if self.path.startswith("/") or "\\" in self.path or any(x in {"", ".", ".."} for x in self.path.split("/")):
            raise ValueError("artifact path must be a normalized relative path")
        if (self.kind == 'symlink') != (self.target is not None):
            raise ValueError('Only symlink references carry link metadata')


class RuntimeSpec(Model):
    account: Literal["food-delivery"] = "food-delivery"
    worker_root: Literal["/var/lib/benchkit-worker"] = "/var/lib/benchkit-worker"
    control_root: Literal["/var/lib/benchkit-controller"] = "/var/lib/benchkit-controller"
    python: Literal["/usr/bin/python3"] = "/usr/bin/python3"
    workspace_storage: Literal["host_directory_no_hard_quota"] = "host_directory_no_hard_quota"



class NativeSpec(Model):
    """The implementer installation every cell shares: one binary, one version, one usage basis."""
    backend: Literal["codex"]
    route: Literal["openai"]
    binary: Text
    binary_digest: Digest
    version: Setting
    usage_mode: Literal["per_turn", "cumulative", "unverified"] = "unverified"

    def __post_init__(self):
        if not self.binary.startswith("/") or "\x00" in self.binary:
            raise ValueError("the native binary must be an absolute path")
        if self.version.lower() in _PLACEHOLDERS:
            raise ValueError("an explicit native version is required")


class ModelSpec(NativeSpec, kw_only=True):
    """The installation with one cell's explicit model and effort."""
    model: Setting
    effort: Setting

    def __post_init__(self):
        super().__post_init__()
        if any(x.lower() in _PLACEHOLDERS for x in (self.model, self.effort)):
            raise ValueError("explicit native model and effort are required")


class Cell(Model):
    """One experiment cell: how its runs end and which implementer model and effort they use."""
    gate: Gate
    model: Setting
    effort: Setting
    profile: Profile = "default"

    def __post_init__(self):
        if any(x.lower() in _PLACEHOLDERS for x in (self.model, self.effort)):
            raise ValueError("each cell needs an explicit implementer model and effort")


class Criterion(Model):
    id: Text
    text: Text


class Issue(Model):
    id: Text
    title: Text
    criteria: tuple[Criterion, ...]


class Catalog(Document, tag="catalog", tag_field="type"):
    name: Text
    issues: tuple[Issue, ...]
    integrations: tuple[Criterion, ...]

    def __post_init__(self):
        issue_ids = [i.id for i in self.issues]
        ids = [c.id for i in self.issues for c in i.criteria] + [i.id for i in self.integrations]
        if not self.issues or any(not i.criteria for i in self.issues) or len(set(issue_ids)) != len(issue_ids) or len(set(ids)) != len(ids):
            raise ValueError("catalog requires nonempty issues and globally unique criterion IDs")


class Excerpt(Model):
    file: Text
    text: Text


class Clause(Model):
    """One atomic public behavior, with its contract excerpts and the literals code retrieval uses."""
    id: Text
    text: Text
    excerpts: tuple[Excerpt, ...]
    endpoints: tuple[Text, ...]
    terms: tuple[Text, ...]


class ClauseCriterion(Model):
    criterion_id: Text
    criterion_text: Text
    clauses: tuple[Clause, ...]


class ClauseIssue(Model):
    issue_id: Text
    title: Text
    criteria: tuple[ClauseCriterion, ...]


class ClauseList(Model):
    """Every public criterion split into atomic clauses from the public brief alone; frozen per campaign."""
    format: Literal["benchkit-clauses"]
    version: Literal[1]
    source: Text
    issues: tuple[ClauseIssue, ...]

    def __post_init__(self):
        ids = [clause.id for issue in self.issues for item in issue.criteria for clause in item.clauses]
        if not ids or len(set(ids)) != len(ids) or any(not item.clauses for issue in self.issues for item in issue.criteria):
            raise ValueError('a clause list needs unique clause IDs and at least one clause per criterion')
        if any(not clause.excerpts for issue in self.issues for item in issue.criteria for clause in item.clauses):
            raise ValueError('every clause cites at least one public excerpt')


class Prompts(Model):
    common: Text
    # The one fixed continuation a `generic` cell sends after the first native final.
    generic: Text | None = None


class DraftConfig(Document, tag="draft", tag_field="type"):
    name: Text
    runtime: RuntimeSpec | None = None
    swe: NativeSpec | None = None
    cells: dict[CellName, Cell] | None = None
    jev_model: Text | None = None
    # Optional implementer profile directory copied at freeze; a relative path resolves from the campaign root.
    profile: Text | None = None


class Experiment(Model):
    name: Text
    runtime: RuntimeSpec
    swe: NativeSpec
    cells: dict[CellName, Cell]
    prompts: Prompts
    catalog: Catalog
    public_files: tuple[ArtifactRef, ...]
    seed_commit: Text
    runtime_inventory: dict[str, str]
    normalization: Literal['native-counters-interval-unions'] = 'native-counters-interval-unions'
    jev_model: Text | None = None
    # Implementer profile files relative to CODEX_HOME; seeded only for cells with the custom profile.
    profile: tuple[ArtifactRef, ...] = ()
    # The frozen clause list the Jev gate reviews; required when a cell uses the jev gate.
    gate: ArtifactRef | None = None

    def __post_init__(self):
        if self.jev_model is not None and self.jev_model != 'jev-1.13.0':
            raise ValueError('Jev must use the pinned jev-1.13.0 model')
        if any(ref.kind != 'file' for ref in self.profile) or len({ref.path for ref in self.profile}) != len(self.profile):
            raise ValueError('implementer profile must list unique regular files')
        if not self.cells:
            raise ValueError('an experiment defines at least one cell')
        # A custom-profile cell without a frozen profile is refused when a run or plan selects it.
        for name, spec in self.cells.items():
            if spec.gate == 'jev' and (self.jev_model is None or self.gate is None):
                raise ValueError(f'cell {name} is Jev-gated without a frozen Jev model and clause list')
            if spec.gate == 'generic' and self.prompts.generic is None:
                raise ValueError(f'cell {name} needs the frozen generic continuation prompt')


def cell(experiment: Experiment, name: str) -> Cell:
    if name not in experiment.cells:
        raise ContractError(f'unknown experiment cell: {name}')
    return experiment.cells[name]


def implementer(experiment: Experiment, name: str) -> ModelSpec:
    """The shared installation with one cell's model and effort."""
    selected = cell(experiment, name)
    return ModelSpec(**msgspec.structs.asdict(experiment.swe), model=selected.model, effort=selected.effort)


class Interval(Model):
    kind: Literal['active', 'native', 'jev', 'setup']
    clock_epoch: Text
    start: float
    end: float
    call_id: Text | None = None

    def __post_init__(self):
        if not math.isfinite(self.start) or not math.isfinite(self.end) or self.end < self.start:
            raise ValueError('Invalid monotonic interval')


class Manifest(Document, tag="manifest", tag_field="type"):
    fingerprint: Digest
    experiment: Experiment


class Snapshot(Document, tag="snapshot", tag_field="type"):
    run_id: Text
    digest: Digest
    files: tuple[ArtifactRef, ...]


class RunRecord(Document, tag="run", tag_field="type"):
    run_id: Text
    manifest_digest: Digest
    condition: CellName
    run_kind: RunKind
    controller_digest: Digest
    state: State = "CREATED"
    outcome: Outcome | None = None
    cleanup: Cleanup = "pending"
    started_utc: str | None = None
    finished_utc: str | None = None
    elapsed_sec: Annotated[float, msgspec.Meta(ge=0)] | None = None
    failure_detail: str | None = None
    collection_error: Text | None = None
    snapshot: Snapshot | None = None
    collection: Literal['pending', 'complete', 'incomplete'] = 'pending'
    validity: Literal['unverified', 'valid', 'invalid'] = 'unverified'
    missing_evidence: tuple[str, ...] = ()
    execution_space: Literal['present', 'removed', 'unknown'] = 'unknown'
    admission: Literal['blocked', 'allowed'] = 'blocked'
    cancellation_record: ArtifactRef | None = None
    archive: tuple[ArtifactRef, ...] = ()
    intervals: tuple[Interval, ...] = ()
    # Block index and 1-based plan position; set only for runs started from a batch plan.
    block: Positive | None = None
    position: Positive | None = None


class UsageMetric(Model):
    name: TokenName
    value: Annotated[float, msgspec.Meta(ge=0)] | None
    coverage: Literal["complete", "partial", "unknown"]
    reason: str | None = None

    def __post_init__(self):
        if self.value is not None and (not math.isfinite(self.value) or self.value != int(self.value)):
            raise ValueError("usage requires finite integral token counts")
        if self.value is None and (self.coverage != "unknown" or not self.reason):
            raise ValueError("missing usage requires unknown coverage and a reason")


class UsageObservation(Model):
    observation_id: Text
    provider: Text
    request_id: Text
    metrics: tuple[UsageMetric, ...]
    basis: Literal["per_turn", "cumulative", "unverified"] = "unverified"
    session_id: str | None = None
    source: ArtifactRef | None = None

    def __post_init__(self):
        if len({m.name for m in self.metrics}) != len(self.metrics):
            raise ValueError("duplicate usage metric")


class ToolObservation(Model):
    kind: Text
    success: bool | None
    output: str
    exit_code: int | None = None
    native_id: str | None = None
    command: str | None = None
    status: str | None = None
    sequence: Nonnegative | None = None
    raw_pointer: str | None = None
    native_input: dict | None = None


class SessionCursor(Model):
    session_id: Text
    usage: tuple[UsageMetric, ...] = ()


class ParsedTurn(Model):
    session_id: Text
    text: Text
    cursor: SessionCursor
    tools: tuple[ToolObservation, ...]
    usage: UsageObservation


class ModelObservation(Model):
    requested_model: Text
    requested_effort: Text
    observed_model: Text | None
    observed_effort: Text | None
    unavailable_reason: Text | None
    source: ArtifactRef | None
    configuration: dict = {}


class JevResult(Model):
    status: Literal["observed", "error"]
    # One Noul answer per requested question ID.
    nouls: dict[Text, Noul] = {}
    usage: UsageObservation | None = None
    error: str | None = None
    error_kind: Literal['authentication', 'transport', 'malformed_response', 'unsupported_input', 'storage'] | None = None
    duration_sec: float = 0.0

    def __post_init__(self):
        if not math.isfinite(self.duration_sec) or self.duration_sec < 0:
            raise ValueError("invalid Jev duration")
        if self.status == "observed":
            if not self.nouls:
                raise ValueError('Observed Jev result requires its answers')
        elif self.nouls:
            raise ValueError("unobserved Jev result cannot include answers")
        if self.status == "error" and (not self.error or not self.error_kind):
            raise ValueError('Jev failure requires a reason and independent error class')


class CallRecord(Document, tag="call", tag_field="type"):
    call_id: Text
    run_id: Text
    role: Literal["swe", "jev", "setup"]
    provider: Text
    unit_name: str | None
    status: Literal["registered", "running", "completed", "cancelled", "provider_error", "protocol_error", "harness_error", "interrupted"] = "registered"
    cleanup: Cleanup = "pending"
    session_id: str | None = None
    input: ArtifactRef | None = None
    stdout: ArtifactRef | None = None
    stderr: ArtifactRef | None = None
    usage: UsageObservation | None = None
    started_utc: str | None = None
    duration_sec: Annotated[float, msgspec.Meta(ge=0)] = 0.0
    exit_code: int | None = None
    error: str | None = None
    caused_by_call_id: Text | None = None
    checkpoint_id: Text | None = None
    # A completed Jev request's Noul answers by question ID.
    answers: dict[Text, Noul] | None = None
    execution_request: ArtifactRef | None = None
    model_observation: ModelObservation | None = None
    tools: tuple[ToolObservation, ...] = ()
    interval: Interval | None = None


class Checkpoint(Document, tag="checkpoint", tag_field="type"):
    """One gate review of an implementer return: the observed workspace, the gate plan, its Jev
    requests, every located clause's answer (asked now or reused) and the action code derived."""
    run_id: Text
    round_index: Positive
    source_digest: Digest
    checkpoint_id: Text
    call_id: Text
    evidence: ArtifactRef
    plan: ArtifactRef
    review_calls: tuple[Text, ...]
    nouls: dict[Text, Noul]
    blocker: Noul
    unlocated: tuple[Text, ...]
    flagged: tuple[Text, ...]
    withheld: tuple[Text, ...]
    unclear: tuple[Text, ...]
    action: ReviewAction

    def __post_init__(self):
        if not self.review_calls or len(set(self.review_calls)) != len(self.review_calls):
            raise ValueError('a checkpoint names its distinct Jev requests')
        if (self.action == 'repair') != bool(self.flagged):
            raise ValueError('repair is selected exactly when clauses are flagged')


class Check(Model):
    name: Literal["host", "native_identity", "auth"]
    evidence: ArtifactRef


class Preparation(Document, tag="preparation", tag_field="type"):
    run_id: Text
    manifest_digest: Digest
    checks: tuple[Check, ...]
    created_utc: Text
    # Controller code executing the run; recorded beside the creation digest, never a gate.
    controller_digest: Digest

    def __post_init__(self):
        if len({check.name for check in self.checks}) != len(self.checks):
            raise ValueError("preparation check names must be unique")


class EvaluationItem(Model):
    id: Text
    status: Literal["pass", "fail", "blocked", "not_run", "grader_error"]
    evidence: ArtifactRef | None

    def __post_init__(self):
        if self.status in {"pass", "fail"} and self.evidence is None:
            raise ValueError("pass/fail requires an evidence artifact")


class EvaluationReport(Document, tag="evaluation", tag_field="type"):
    run_id: Text
    manifest_digest: Digest
    snapshot_digest: Digest
    catalog_digest: Digest
    evaluator_revision: Text
    is_fixture: bool
    criteria: tuple[EvaluationItem, ...]
    integrations: tuple[EvaluationItem, ...]
    raw_report: ArtifactRef | None = None
    raw_evidence: tuple[ArtifactRef, ...] = ()


Record = RunRecord | CallRecord | Checkpoint | Preparation | EvaluationReport


class Event(Document, tag="event", tag_field="type"):
    sequence: Positive
    utc: Text
    kind: Text
    record: Record


class RunExport(Document, tag="run_export", tag_field="type"):
    manifest: Manifest
    run: RunRecord
    preparation: Preparation | None
    calls: tuple[CallRecord, ...]
    checkpoints: tuple[Checkpoint, ...]
    evaluations: tuple[EvaluationReport, ...]
    events: tuple[Event, ...]


class RuntimeRequest(Model):
    spec: RuntimeSpec
    name: Text
    workspace: Text
    home: Text
    argv: tuple[str, ...]
    log_dir: Text
    stdin: bytes | None = None


class RuntimeResult(Model):
    outcome: Outcome
    exit_code: int | None
    wall_sec: Annotated[float, msgspec.Meta(ge=0)]
    stdout_path: Text | None
    stderr_path: Text | None
    cleanup: Cleanup
    cleanup_error: str | None = None
    error: str | None = None
    interval: Interval | None = None


T = TypeVar("T")


def _finite(value):
    if isinstance(value, float) and not math.isfinite(value):
        raise ContractError("nonfinite number")
    if isinstance(value, dict):
        if any(not isinstance(k, str) for k in value):
            raise ContractError("JSON object keys must be strings")
        for item in value.values():
            _finite(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _finite(item)


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ContractError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def json_value(raw: bytes | str):
    """Strict JSON syntax, duplicate keys and finite values for every boundary."""
    try:
        value = json.loads(raw, object_pairs_hook=_pairs,
                           parse_constant=lambda text: (_ for _ in ()).throw(ContractError(f"invalid JSON number: {text}")))
        _finite(value)
        return value
    except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
        raise ContractError(str(exc)) from exc


def validate(value, cls: type[T]) -> T:
    try:
        data = msgspec.to_builtins(value)
        _finite(data)
        return msgspec.convert(data, type=cls, strict=True)
    except (ValueError, TypeError, RecursionError) as exc:
        raise ContractError(str(exc)) from exc


def loads(raw: bytes | str, cls: type[T]) -> T:
    return validate(json_value(raw), cls)


def make(cls: type[T], **fields) -> T:
    if issubclass(cls, Document):
        fields["format"] = FORMAT
        fields["type"] = cls.__struct_config__.tag
    return validate(fields, cls)


def dumps(value) -> bytes:
    normalized = validate(value, type(value)) if isinstance(value, Model) else value
    data = msgspec.to_builtins(normalized)
    _finite(data)
    return json.dumps(data, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")).encode()


def digest(value) -> str:
    return "sha256:" + hashlib.sha256(value if isinstance(value, bytes) else dumps(value)).hexdigest()


PUBLIC_TYPES = (DraftConfig, Catalog, Manifest, Snapshot, RunRecord, CallRecord, Checkpoint, Preparation, EvaluationReport, Event, RunExport)


def schemas(*additional_types) -> dict[str, dict]:
    return {cls.__struct_config__.tag: {"$schema": "https://json-schema.org/draft/2020-12/schema", **msgspec.json.schema(cls)} for cls in (*PUBLIC_TYPES, *additional_types)}


def examples() -> tuple[Document, ...]:
    """Valid synthetic contracts for schema consumers; never run evidence."""
    ref = make(ArtifactRef, path="evidence.txt", digest=digest(b"fixture"), size=7)
    catalog = make(Catalog, name="fixture", issues=(Issue(id="issue", title="Fixture issue",
                    criteria=(Criterion(id="criterion", text="Fixture criterion"),)),), integrations=())
    runtime = RuntimeSpec()
    swe = NativeSpec(binary_digest="sha256:" + "a" * 64, backend="codex", route="openai", binary="/usr/bin/codex", version="0.0.0-fixture")
    cells = {"FIXTURE_JEV": Cell(gate="jev", model="fixture-model", effort="high")}
    draft = make(DraftConfig, name="fixture", runtime=runtime, swe=swe, cells=cells, jev_model="jev-1.13.0")
    gate = make(ArtifactRef, path="gate/clauses.json", digest=digest(b"clauses"), size=7)
    experiment = make(Experiment, name="fixture", runtime=runtime, swe=swe, cells=cells, jev_model="jev-1.13.0", gate=gate,
                      prompts=Prompts(common="Fixture"),
                      catalog=catalog, public_files=(), seed_commit="a" * 40, runtime_inventory={"fixture": "synthetic"})
    manifest = make(Manifest, fingerprint=digest(experiment), experiment=experiment)
    run = make(RunRecord, run_id="fixture", manifest_digest=manifest.fingerprint, condition="FIXTURE_JEV", run_kind="pilot", block=1, position=1,
               controller_digest=digest(b"fixture"))
    sealed = make(Snapshot, run_id=run.run_id, digest=digest((ref,)), files=(ref,))
    call = make(CallRecord, call_id="fixture-call", run_id=run.run_id, role="swe", provider="codex", unit_name="bk-fixture")
    checkpoint = make(Checkpoint, run_id=run.run_id, round_index=1, source_digest=sealed.digest, checkpoint_id="fixture:1",
                      call_id=call.call_id, evidence=ref, plan=ref, review_calls=("fixture-review",), nouls={"clause#1": .9},
                      blocker=.01, unlocated=(), flagged=(), withheld=(), unclear=(), action="complete")
    preparation = make(Preparation, run_id=run.run_id, manifest_digest=manifest.fingerprint, checks=(Check(name="host", evidence=ref),), created_utc="2026-01-01T00:00:00+00:00",
                       controller_digest=digest(b"fixture"))
    evaluation = make(EvaluationReport, run_id=run.run_id, manifest_digest=manifest.fingerprint, snapshot_digest=sealed.digest,
                      catalog_digest=digest(catalog), evaluator_revision="fixture", is_fixture=True,
                      criteria=(EvaluationItem(id="criterion", status="pass", evidence=ref),), integrations=())
    event = make(Event, sequence=1, utc=preparation.created_utc, kind="run_created", record=run)
    exported = make(RunExport, manifest=manifest, run=run, preparation=preparation, calls=(call,),
                    checkpoints=(checkpoint,), evaluations=(evaluation,), events=(event,))
    return draft, catalog, manifest, run, sealed, call, checkpoint, preparation, evaluation, event, exported
