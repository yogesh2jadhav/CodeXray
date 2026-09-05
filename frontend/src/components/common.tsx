/**
 * src/components/common.tsx
 *
 * Purpose:        Small shared presentational bits.
 * Responsibility: Spinner, ErrorBox, StatusBadge, Loading wrapper — used across pages.
 */
import type { ReactNode } from "react";

export function Spinner({ label }: { label?: string }) {
  return (
    <span className="row small muted">
      <span className="spinner" /> {label || "loading…"}
    </span>
  );
}

export function ErrorBox({ message }: { message: string }) {
  return <div className="card err">⚠ {message}</div>;
}

export function StatusBadge({ status }: { status: string }) {
  return <span className={`status-${status}`}>{status}</span>;
}

export function Loading<T>({
  loading,
  error,
  data,
  children,
}: {
  loading: boolean;
  error: string | null;
  data: T | null;
  children: (d: T) => ReactNode;
}) {
  if (loading) return <Spinner />;
  if (error) return <ErrorBox message={error} />;
  if (data == null) return <div className="muted small">no data</div>;
  return <>{children(data)}</>;
}
