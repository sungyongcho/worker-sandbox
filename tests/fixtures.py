"""Shared synthetic data and process boundary; never native acceptance evidence."""
import json
from pathlib import Path

from worker_sandbox import contracts as c
from worker_sandbox.artifacts import tree_manifest


def codex_auth(token='synthetic-access', account='synthetic-account'):
    return c.dumps({'auth_mode': 'chatgpt', 'OPENAI_API_KEY': None,
                    'tokens': {'id_token': 'synthetic-id', 'access_token': token,
                               'refresh_token': 'synthetic-refresh', 'account_id': account},
                    'last_refresh': '2026-09-19T00:00:00Z'})


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

    def verify_model(self, binary, binary_digest):
        return binary_digest

    def claim(self):
        pass

    def release(self):
        pass

    def confirm_stopped(self):
        return self.clean

    def collect_workspace(self):
        pass

    def start_run(self):
        self.service_active = True

    def stop_run(self):
        self.service_active = False
        return self.clean
