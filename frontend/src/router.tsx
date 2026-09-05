/**
 * src/router.tsx
 *
 * Purpose:        Minimal hash router (no react-router dependency).
 * Responsibility: Parse `#/path`, expose `useRoute()` + `navigate()` + a `<Link>`
 *                 that renders an <a href="#/...">. Enough for CodeXray's handful
 *                 of screens.
 */
import { createContext, useContext, useEffect, useState, type ReactNode } from "react";

function currentPath(): string {
  const h = window.location.hash.replace(/^#/, "");
  return h || "/";
}

const RouteCtx = createContext<string>("/");

export function RouterProvider({ children }: { children: ReactNode }) {
  const [path, setPath] = useState(currentPath());
  useEffect(() => {
    const on = () => setPath(currentPath());
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  return <RouteCtx.Provider value={path}>{children}</RouteCtx.Provider>;
}

export function useRoute(): string {
  return useContext(RouteCtx);
}

export function navigate(to: string) {
  window.location.hash = to.startsWith("/") ? to : `/${to}`;
}

export function Link({ to, className, children }: { to: string; className?: string; children: ReactNode }) {
  return (
    <a
      className={className}
      href={`#${to}`}
      onClick={(e) => {
        e.preventDefault();
        navigate(to);
      }}
    >
      {children}
    </a>
  );
}

/** Split "/project/3/chat" -> ["project","3","chat"] */
export function segments(path: string): string[] {
  return path.split("/").filter(Boolean);
}
