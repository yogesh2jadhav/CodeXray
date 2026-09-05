/**
 * src/components/CodeViewer.tsx
 *
 * Purpose:        Show a source file with line numbers, scrolled to and
 *                 highlighting a target line (build plan §50 — click evidence to
 *                 open source).
 * Responsibility: Resolve `file path -> file id` via the files list, fetch the
 *                 source, render a <pre> with a highlighted line. No syntax
 *                 colouring (keeps the bundle tiny).
 */
import { useEffect, useMemo, useRef } from "react";
import { api } from "../api/client";
import { useAsync } from "../hooks/useApi";
import { Loading } from "./common";

export function CodeViewer({
  projectId,
  file,
  line,
  onClose,
}: {
  projectId: number;
  file: string;
  line: number | null;
  onClose: () => void;
}) {
  const files = useAsync(() => api.files(projectId), [projectId]);
  const fileId = useMemo(() => {
    const f = files.data?.find((x) => x.path === file || x.path.endsWith(file));
    return f?.id ?? null;
  }, [files.data, file]);

  const src = useAsync(
    () => (fileId != null ? api.fileSource(projectId, fileId) : Promise.reject(new Error("file not indexed"))),
    [projectId, fileId],
  );

  const preRef = useRef<HTMLPreElement>(null);
  useEffect(() => {
    if (src.data && line && preRef.current) {
      const el = preRef.current.querySelector(`[data-ln="${line}"]`);
      el?.scrollIntoView({ block: "center" });
    }
  }, [src.data, line]);

  return (
    <div className="card">
      <div className="row" style={{ justifyContent: "space-between" }}>
        <h2 style={{ margin: 0 }} className="mono">
          {file}
          {line ? `:${line}` : ""}
        </h2>
        <button className="secondary" onClick={onClose}>
          close
        </button>
      </div>
      <Loading loading={files.loading || src.loading} error={files.error || src.error} data={src.data}>
        {(data) => (
          <pre className="code" ref={preRef}>
            {data.content.split("\n").map((row, i) => {
              const n = i + 1;
              return (
                <span key={n} data-ln={n} className={n === line ? "hl" : undefined}>
                  <span className="ln">{String(n).padStart(4, " ")} </span>
                  {row}
                  {"\n"}
                </span>
              );
            })}
          </pre>
        )}
      </Loading>
    </div>
  );
}
