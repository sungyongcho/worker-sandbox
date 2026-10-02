"""Shared synthetic data and process boundary; never native acceptance evidence."""
import json
from pathlib import Path

import msgspec

from benchkit import batch, contracts as c
from benchkit.contracts import (ArtifactRef, Catalog, Cell, Clause, ClauseCriterion, ClauseIssue, ClauseList, Criterion,
                               Excerpt, Experiment, Issue, Manifest, ModelSpec, NativeSpec, Prompts, RunRecord,
                               RuntimeSpec, Snapshot, digest, make)
from benchkit.artifacts import atomic_write, tree_manifest
from benchkit.store import DATABASE, Store
from benchkit.worker_files import observe_tree


def codex_auth(token='synthetic-access', account='synthetic-account'):
    return c.dumps({'auth_mode': 'chatgpt', 'OPENAI_API_KEY': None,
                    'tokens': {'id_token': 'synthetic-id', 'access_token': token,
                               'refresh_token': 'synthetic-refresh', 'account_id': account},
                    'last_refresh': '2026-09-19T00:00:00Z'})


WORKER = ModelSpec(binary_digest="sha256:" + "a" * 64, backend="codex", route="openai", binary="/usr/bin/codex",
                   version="0.155.1", model="gpt-6-sol", effort="high")
# The synthetic 2x2 cells most tests use; campaigns define their own cells.
CELLS = ("DEFAULT_OFF", "DEFAULT_JEV", "CUSTOM_OFF", "CUSTOM_JEV")
GATES = {"DEFAULT_OFF": "off", "DEFAULT_JEV": "jev", "CUSTOM_OFF": "off", "CUSTOM_JEV": "jev", "DEFAULT_GENERIC": "generic"}
GENERIC_PROMPT = "Check the work against every public requirement."


def _clause(identifier, text, endpoint, *terms):
    return Clause(id=identifier, text=text, excerpts=(Excerpt(file="PRODUCT_CONTRACT.md", text=text),),
                  endpoints=(endpoint,), terms=terms)


# A synthetic clause list for the tiny catalog below; campaigns freeze the kit's real list.
CLAUSES = ClauseList(format="benchkit-clauses", version=1, source="synthetic fixture", issues=(
    ClauseIssue(issue_id="orders", title="Order handling", criteria=(
        ClauseCriterion(criterion_id="order-create", criterion_text="Create order", clauses=(
            _clause("order-create#1", "Creating an order returns 201.", "POST /orders", "create_order", "201"),)),
        ClauseCriterion(criterion_id="order-read", criterion_text="Read order", clauses=(
            _clause("order-read#1", "Only the owner can read an order.", "GET /orders/{id}", "read_order", "owner"),)))),
    ClauseIssue(issue_id="payments", title="Payment handling", criteria=(
        ClauseCriterion(criterion_id="payment-capture", criterion_text="Capture payment", clauses=(
            _clause("payment-capture#1", "Paying captures the payment once.", "POST /orders/{id}/pay", "capture"),)),
        ClauseCriterion(criterion_id="payment-refund", criterion_text="Refund payment", clauses=(
            _clause("payment-refund#1", "Cancelling a captured order refunds it once.", "POST /orders/{id}/cancel",
                    "refund"),)))),
))
CLAUSE_BYTES = c.dumps(CLAUSES)
GATE_REF = ArtifactRef(path="gate/clauses.json", digest=digest(CLAUSE_BYTES), size=len(CLAUSE_BYTES))
# Workspace code the synthetic clauses retrieve: one unit per clause.
PRODUCT_CODE = "\n".join((
    '@app.post("/orders")', 'def create_order():', '    return 201', '', '',
    '@app.get("/orders/{order_id}")', 'def read_order(order_id, owner):', '    return owner', '', '',
    '@app.post("/orders/{order_id}/pay")', 'def capture(order_id):', '    return "captured"', '', '',
    '@app.post("/orders/{order_id}/cancel")', 'def refund(order_id):', '    return "refunded"', ''))


def jev_answer(request, values=None, *, default=.95, blocker=.02) -> bytes:
    """A Jev response with one Noul per requested question; `values` overrides chosen questions."""
    questions = json.loads(request.data)["questions"]
    answers = {name: {"type": "noul", "noul": blocker if name == "blocker" else (values or {}).get(name, default)}
               for name in questions}
    return c.dumps({"model": "jev-1.13.0", "answers": answers, "usage": {"input_tokens": 17, "output_tokens": 0}})


def native(worker: ModelSpec) -> NativeSpec:
    return NativeSpec(**{name: value for name, value in msgspec.structs.asdict(worker).items() if name not in ("model", "effort")})


def cells_fixture(worker: ModelSpec = WORKER, names=CELLS) -> dict[str, Cell]:
    return {name: Cell(gate=GATES[name], model=worker.model, effort=worker.effort,
                       profile="custom" if name.startswith("CUSTOM") else "default") for name in names}


def manifest_fixture(*, worker=None, cells=None) -> Manifest:
    catalog = make(Catalog, name="tiny-food-test", issues=(
        Issue(id="orders", title="Order handling", criteria=(Criterion(id="order-create", text="Create order"),
                                                                  Criterion(id="order-read", text="Read order"))),
        Issue(id="payments", title="Payment handling", criteria=(Criterion(id="payment-capture", text="Capture payment"),
                                                                      Criterion(id="payment-refund", text="Refund payment"))),
    ), integrations=(Criterion(id="journey", text="Complete customer journey"),))
    worker = worker or WORKER
    experiment = Experiment(name="store-test", runtime=RuntimeSpec(), swe=native(worker),
                            cells=cells_fixture(worker, cells or CELLS),
                            prompts=Prompts(common="Build the app.", generic=GENERIC_PROMPT), gate=GATE_REF,
                            catalog=catalog, public_files=(), seed_commit="a" * 40, runtime_inventory={"fixture": "synthetic"},
                            jev_model="jev-1.13.0")
    return make(Manifest, fingerprint=digest(experiment), experiment=experiment)


def store_fixture(root: Path, run_id="run-1", *, condition="DEFAULT_OFF", run_kind="main", block=None, position=None, worker=None,
                  cells=None) -> Store:
    manifest = manifest_fixture(worker=worker, cells=cells or (CELLS if condition in CELLS else (*CELLS, condition)))
    record = make(RunRecord, run_id=run_id, manifest_digest=manifest.fingerprint, condition=condition,
                  run_kind=run_kind, block=block, position=position, controller_digest="sha256:" + "b" * 64)
    store = Store(root / "runs" / run_id / DATABASE)
    store.initialize(record, manifest)
    (store.path.parent / "gate").mkdir()
    atomic_write(store.path.parent / "gate" / "clauses.json", CLAUSE_BYTES)
    (store.path.parent / 'inputs').mkdir()
    return store


def plan_fixture(root: Path, output: Path, *, cells=("DEFAULT_OFF",), attempts=1, revision="evaluator-1") -> batch.Plan:
    """Write a batch plan over the campaign `root` whose runs are made with slot_fixture."""
    manifest = manifest_fixture()
    plan = make(batch.Plan, campaign=str(root), manifest_digest=manifest.fingerprint, cells=cells,
                attempts=attempts, order_seed="order", slots=batch.schedule(cells, attempts, "order"),
                controller_digest="sha256:" + "b" * 64, evaluator_revision=revision, acceptance_rule="full_project_success")
    if not (root / "manifest.json").exists():
        root.mkdir(parents=True, exist_ok=True)
        atomic_write(root / "manifest.json", c.dumps(manifest))
    output.mkdir(parents=True)
    atomic_write(output / batch.PLAN, c.dumps(plan))
    return plan


def slot_fixture(output: Path, plan: batch.Plan, position: int, run_id=None) -> Store:
    """Create the run a started plan slot names, as batch records it."""
    slot = plan.slots[position - 1]
    store = store_fixture(Path(plan.campaign), run_id or f"run-{position}", condition=slot.cell,
                          block=slot.block, position=slot.position)
    folder = output / f"{slot.position:03d}-{slot.cell}"
    folder.mkdir()
    atomic_write(folder / "created.json", c.dumps({"run": str(store.path.parent), "record": store.run()}))
    return store


def running_fixture(store: Store) -> None:
    store.transition({"CREATED"}, "PREPARED")
    store.transition({"PREPARED"}, "RUNNING")


def sealed_fixture(store: Store, *, outcome="completed") -> None:
    """Seal an ungated run; a completed one records its single implementer return."""
    running_fixture(store)
    if outcome == "completed":
        run_id, prompt = store.run().run_id, store.manifest().experiment.prompts.common.encode()
        (store.path.parent / "prompt.txt").write_bytes(prompt)
        call = make(c.CallRecord, call_id=run_id + "-swe", run_id=run_id, role="swe", provider="codex", unit_name=None,
                    input=ArtifactRef(path="prompt.txt", digest=digest(prompt), size=len(prompt)))
        store.register_call(call)
        store.finish_call(msgspec.structs.replace(call, status="completed", cleanup="confirmed"))
    store.transition({"RUNNING"}, "STOPPING", outcome=outcome)
    store.transition({"STOPPING"}, "STOPPED", cleanup="confirmed", elapsed_sec=10.0)
    folder = store.path.parent / "snapshot"
    folder.mkdir()
    content = b"print('food app')\n"
    (folder / "app.py").write_bytes(content)
    files = (ArtifactRef(path="app.py", digest=digest(content), size=len(content), source="candidate"),)
    snapshot = make(Snapshot, run_id=store.run().run_id, files=files, digest=digest(files))
    store.transition({"STOPPED"}, "SEALED", snapshot=snapshot)


class FakeRuntime:
    def __init__(self, *, outcome="completed", cleanup=True):
        self.outcome, self.clean = outcome, cleanup
        self.invocations = []
        self.on_invoke = None
        self.service_active = False

    def inspect(self):
        return {"host": "fixture", "hard_disk_quota": "not_guaranteed"}

    def cleanup(self, name):
        return self.clean

    def invoke(self, request, cancel=None):
        self.invocations.append(request)
        if self.on_invoke:
            self.on_invoke()
        raw = "1.2.3\n"
        outcome = "completed"
        if "exec" in request.argv:
            outcome = self.outcome
            events = [{"type":"thread.started","thread_id":"session"}, {"type":"turn.started"},
                      {"type":"item.completed","item":{"type":"agent_message","text":"Native final response"}},
                      {"type":"turn.completed","usage":{"input_tokens":2,"output_tokens":1}}]
            raw = "\n".join(json.dumps(event) for event in events)
        folder = Path(request.log_dir)
        folder.mkdir(parents=True)
        (folder / "stdout").write_text(raw)
        (folder / "stderr").write_text("")
        return c.make(c.RuntimeResult, outcome=outcome, exit_code=0, wall_sec=0.01,
                      stdout_path=str(folder / "stdout"), stderr_path=str(folder / "stderr"),
                      cleanup=("pending" if self.service_active else "confirmed") if self.clean else "failed")

    def verify_seed(self, expected):
        if tree_manifest(self.root / "workspace", exclude_generated=False) != expected:
            raise c.ContractError("worker seed changed")

    def verify_model(self, model):
        return model.binary_digest

    def claim(self):
        pass

    def release(self):
        pass

    def sync_credentials(self):
        return 'unchanged'

    def read_credentials(self):
        return codex_auth()

    def confirm_stopped(self):
        return self.clean

    def collect_workspace(self):
        pass

    def start_run(self):
        self.service_active = True

    def stop_run(self):
        self.service_active = False
        return self.clean

    def collect_native_evidence(self):
        return ()

    def observe_workspace(self):
        return observe_tree(self.root / 'workspace')

    def model_evidence(self, session_id, destination):
        raw = b'\n'.join(c.dumps(item) for item in (
            {'type': 'session_meta', 'payload': {'id': session_id}},
            {'type': 'turn_context', 'payload': {'model': 'gpt-6-sol', 'effort': 'high'}}))
        ref = atomic_write(destination, raw)
        return c.make(c.ArtifactRef, path=destination.relative_to(self.root).as_posix(), digest=ref.digest, size=ref.size)

    def execution_request(self, unit_name):
        return c.dumps({'fixture': True, 'unit_name': unit_name})
