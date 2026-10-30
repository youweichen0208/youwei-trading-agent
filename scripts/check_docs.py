"""Check task graph and milestone consistency without installing dependencies."""

import re
from pathlib import Path

root = Path(__file__).resolve().parents[1]
source = next((root / "docs").glob("*Architecture_and_Task_Plan.md")).read_text()
pattern = r"^#### TASK-(\d+) · ([^\n]+)\n(.*?)(?=^#### TASK-|^### 13\.|^## 14\.)"
tasks = {}
for match in re.finditer(pattern, source, re.M | re.S):
    task_id = int(match[1])
    body = match[3]
    assert task_id not in tasks, f"Duplicate task {task_id}"
    priority = int(re.search(r"：P(\d) / [SML] /", body)[1])
    phase = int(re.search(r"所属里程碑\*\*：M(\d)", body)[1])
    deps = [
        int(d) for d in re.findall(r"TASK-(\d+)", re.search(r"前置依赖\*\*：([^\n]+)", body)[1])
    ]
    tasks[task_id] = (priority, phase, deps)
assert set(tasks) == set(range(1, 121)), "Task ID coverage differs from expected 120 tasks"
visited, active = set(), set()


def visit(task_id):
    assert task_id not in active, f"Dependency cycle at {task_id}"
    if task_id in visited:
        return
    active.add(task_id)
    priority, phase, dependencies = tasks[task_id]
    for dependency in dependencies:
        assert dependency in tasks, f"Unknown dependency {dependency}"
        other_priority, other_phase, _ = tasks[dependency]
        assert other_priority <= priority, f"Priority inversion: {task_id} -> {dependency}"
        assert other_phase <= phase, f"Milestone inversion: {task_id} -> {dependency}"
        visit(dependency)
    active.remove(task_id)
    visited.add(task_id)


for task in tasks:
    visit(task)
listed = []
for match in re.finditer(r"^\| M(\d) [^|]+\| ([0-9、]+) \|", source, re.M):
    phase = int(match[1])
    ids = [int(n) for n in match[2].split("、")]
    for task in ids:
        assert tasks[task][1] == phase, f"Milestone table mismatch: {task}"
    listed.extend(ids)
assert sorted(listed) == list(range(1, 121)), "Milestone table must cover each task exactly once"
print("120 tasks: unique IDs, valid DAG, priority closure and milestone coverage passed")
