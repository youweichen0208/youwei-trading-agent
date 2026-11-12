import { test } from "node:test";
import assert from "node:assert/strict";
import { useCanvas } from "../../apps/web/lib/canvas.js";

test("Canvas reducer ignores duplicate/archived events and requests recovery on gaps", () => {
  const canvas = useCanvas.getState();
  canvas.load({
    id: "workspace",
    version: 2,
    revision: 1,
    snapshot: { artifacts: [] },
  });
  const event = {
    event: "canvas.upsert",
    data: {
      workspaceId: "workspace",
      workspaceVersion: 3,
      workspaceRevision: 1,
      artifactId: "a",
      component: "stock_chart",
      title: "NVDA",
      datasetRef: "ref",
    },
  };
  assert.equal(canvas.apply(event), true);
  assert.equal(useCanvas.getState().artifacts.length, 1);
  assert.equal(canvas.apply(event), true);
  assert.equal(useCanvas.getState().artifacts.length, 1);
  assert.equal(
    canvas.apply({ ...event, data: { ...event.data, workspaceVersion: 5 } }),
    false,
  );
  assert.equal(useCanvas.getState().version, 3);
  assert.equal(
    canvas.apply({
      ...event,
      data: { ...event.data, workspaceVersion: 4, archived: true },
    }),
    true,
  );
  assert.equal(useCanvas.getState().version, 3);
  assert.equal(
    canvas.apply({
      event: "canvas.remove",
      data: { ...event.data, workspaceVersion: 4 },
    }),
    true,
  );
  assert.equal(useCanvas.getState().artifacts.length, 0);
});

test("reconnect snapshots preserve an explicit historical view and reject stale or foreign workspaces", () => {
  const canvas = useCanvas.getState();
  const current = {
    id: "workspace",
    version: 8,
    revision: 3,
    snapshot: { artifacts: [] },
  };
  canvas.load(current);
  canvas.sync({
    ...current,
    version: 7,
    snapshot: { artifacts: [{ artifactId: "stale" }] },
  });
  canvas.sync({ ...current, id: "other", version: 9 });
  assert.equal(useCanvas.getState().version, 8);
  canvas.showHistory({
    task: { id: "old-task", workspace_revision: 1 },
    workspace: current,
    artifacts: [{ artifactId: "history", title: "Saved historical result" }],
  });
  canvas.sync({ ...current, version: 9, revision: 4 });
  assert.equal(useCanvas.getState().historyTaskId, "old-task");
  assert.equal(useCanvas.getState().artifacts[0]?.artifactId, "history");
  canvas.load(current);
  canvas.sync({ ...current, version: 9, revision: 4 });
  assert.equal(useCanvas.getState().version, 9);
  assert.equal(useCanvas.getState().revision, 4);
});
