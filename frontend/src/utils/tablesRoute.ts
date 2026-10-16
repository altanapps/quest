export type TablesRoute =
  | { kind: 'list'; projectId: string | null }
  | { kind: 'viewer'; tableId: string };

/** Only canonical UUIDs may become table-id path segments. */
const TABLE_ID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;

export function parseTablesRoute(pathname: string, search: string): TablesRoute | null {
  if (pathname === '/tables' || pathname === '/tables/') {
    return { kind: 'list', projectId: new URLSearchParams(search).get('project') || null };
  }
  if (!pathname.startsWith('/tables/')) return null;
  let rest = pathname.slice('/tables/'.length);
  if (rest.endsWith('/')) rest = rest.slice(0, -1);
  try {
    const tableId = decodeURIComponent(rest);
    return TABLE_ID_RE.test(tableId) ? { kind: 'viewer', tableId } : null;
  } catch {
    return null;
  }
}

export function tablesListPath(projectId?: string | null): string {
  return projectId ? `/tables?project=${encodeURIComponent(projectId)}` : '/tables';
}

export function tableViewerPath(tableId: string): string {
  return `/tables/${encodeURIComponent(tableId)}`;
}
