import { create } from "zustand";
export type Artifact = {
  artifactId: string;
  component: string;
  title: string;
  datasetRef?: string;
  resultRef?: string;
  props?: { color?: string; showVolume?: boolean };
  identity?: unknown;
};
export const useCanvas = create<{
  artifacts: Artifact[];
  workspaceId: string | null;
  version: number;
  revision: number;
  historyTaskId: string | null;
  showHistory: (snapshot: any) => void;
  load: (w: any) => void;
  sync: (w: any) => void;
  apply: (event: any) => boolean;
}>((set, get) => ({
  artifacts: [],
  workspaceId: null,
  version: 0,
  revision: 0,
  historyTaskId: null,
  showHistory: (s) =>
    set({
      historyTaskId: s.task.id,
      workspaceId: s.workspace.id,
      revision: s.task.workspace_revision,
      version: s.workspace.version,
      artifacts: s.artifacts,
    }),
  load: (w) =>
    set({
      historyTaskId: null,
      workspaceId: w?.id ?? null,
      version: w?.version ?? 0,
      revision: w?.revision ?? 0,
      artifacts: w?.snapshot?.artifacts ?? [],
    }),
  sync: (w) => {
    const current = get();
    if (
      !w ||
      current.historyTaskId ||
      current.workspaceId !== w.id ||
      w.version < current.version
    )
      return;
    set({
      version: w.version,
      revision: w.revision,
      artifacts: w.snapshot?.artifacts ?? [],
    });
  },
  apply: (event) => {
    const data = event.data,
      current = get();
    if (
      current.historyTaskId ||
      data.archived ||
      data.workspaceId !== current.workspaceId
    )
      return true;
    if (!Number.isInteger(data.workspaceVersion)) return false;
    if (data.workspaceVersion <= current.version) return true;
    if (
      data.workspaceVersion !== current.version + 1 ||
      data.workspaceRevision !== current.revision
    )
      return false;
    const artifacts = [...current.artifacts],
      index = artifacts.findIndex((a) => a.artifactId === data.artifactId);
    if (event.event === "canvas.remove") {
      if (index < 0) return false;
      artifacts.splice(index, 1);
    } else {
      if (
        !["canvas.upsert", "canvas.patch"].includes(event.event) ||
        !data.component
      )
        return false;
      if (event.event === "canvas.patch" && index < 0) return false;
      const artifact: Artifact = {
        artifactId: data.artifactId,
        component: data.component,
        title: data.title,
        props: data.props,
        identity: data.identity,
        ...(data.datasetRef ? { datasetRef: data.datasetRef } : {}),
        ...(data.resultRef ? { resultRef: data.resultRef } : {}),
      };
      if (index < 0) artifacts.push(artifact);
      else artifacts[index] = artifact;
    }
    set({ artifacts, version: data.workspaceVersion });
    return true;
  },
}));
