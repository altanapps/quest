import { useState } from 'react';
import { Link } from 'react-router-dom';
import { Globe, Search } from 'lucide-react';
import type { Project } from '../../api/types';
import type { QuestTable } from '../../api/tablesApi';
import { useProjects } from '../../contexts/ProjectsContext';
import { useTables } from '../../hooks/useTables';
import { tablesListPath, tableViewerPath } from '../../utils/tablesRoute';
import { formatRelativeTimestamp, parseUTCTimestamp } from '../../utils/formatters';
import './TablesListView.css';

export function tableMatchesQuery(table: QuestTable, query: string): boolean {
  const needle = query.trim().toLocaleLowerCase();
  return [table.title, table.description, table.owner?.name, table.owner?.email]
    .some((value) => value?.toLocaleLowerCase().includes(needle));
}

export function TablesListView({ projectId }: { projectId: string | null }) {
  const { projects, projectsLoaded } = useProjects();
  const [query, setQuery] = useState('');
  const project = projects.find((item) => item.id === projectId);
  const sortedProjects = [...projects].sort((a, b) =>
    a.name.localeCompare(b.name, undefined, { sensitivity: 'base' }) || a.id.localeCompare(b.id));

  return (
    <div className="tables-list">
      <header className="tables-list-header">
        <div>
          {projectId && <Link to={tablesListPath()}>All tables</Link>}
          <h2>{projectId ? `${project?.name ?? 'Project'} tables` : 'All Tables'}</h2>
          {project?.archived && <span className="tables-list-muted">Archived project</span>}
        </div>
        <label className="tables-list-search">
          <Search size={16} aria-hidden="true" />
          <input type="search" aria-label="Search tables" placeholder="Search tables"
            value={query} onChange={(event) => setQuery(event.target.value)} />
        </label>
      </header>
      <div className="tables-list-content">
        <p className="tables-list-muted">Search covers tables loaded so far.</p>
        {projectId ? (
          <TablesGroup key={projectId} label="Tables" projectId={projectId} query={query} />
        ) : (
          <>
            <TablesGroup label="Your tables" query={query} />
            <TablesGroup label="Shared with you" shared query={query} hideWhenEmpty />
            {!projectsLoaded && <p role="status">Loading project tables...</p>}
            {projectsLoaded && sortedProjects.map((item) => (
              <TablesGroup key={item.id} label={item.name} projectId={item.id}
                project={item} query={query} hideWhenEmpty />
            ))}
          </>
        )}
      </div>
    </div>
  );
}

/** Each group keeps its own server cursor, including project groups. */
function TablesGroup({
  label, projectId, shared = false, project, query, hideWhenEmpty = false,
}: {
  label: string;
  projectId?: string;
  shared?: boolean;
  project?: Project;
  query: string;
  hideWhenEmpty?: boolean;
}) {
  const list = useTables({ projectId, shared });
  const visible = list.tables.filter((table) => tableMatchesQuery(table, query));
  if (hideWhenEmpty && !list.loading && !list.error && list.tables.length === 0 && !list.hasMore) {
    return null;
  }
  return (
    <section className="tables-list-group" aria-label={`${label} table list`}>
      <h3>
        {project ? <Link to={tablesListPath(project.id)}>{label}</Link> : label}
        {project?.public && <span className="tables-mode"><Globe size={12} aria-hidden="true" />Public</span>}
        {project?.archived && <span className="tables-list-muted">Archived</span>}
      </h3>
      {list.loading ? <p role="status">Loading tables...</p> : (
        <>
          {list.error && (
            <div role="alert">
              <p>{list.error}</p>
              <button type="button" onClick={() => void list.refresh()}>Retry</button>
            </div>
          )}
          {visible.length > 0 ? (
            <table className="tables-index">
              <caption className="tables-caption">{label}</caption>
              <thead><tr>
                <th scope="col">Title</th><th scope="col">Mode</th><th scope="col">Scope</th>
                <th scope="col">Rows</th><th scope="col">Columns</th><th scope="col">Updated</th>
              </tr></thead>
              <tbody>{visible.map((table) => (
                <TableRow key={table.id} table={table} project={project} />
              ))}</tbody>
            </table>
          ) : !list.error && (
            <p className="tables-list-muted">
              {query.trim() ? 'No matching tables loaded.' : shared ? 'No shared tables yet.' : 'No tables yet. Ask Quest to create one.'}
            </p>
          )}
          {list.hasMore && (
            <button type="button" className="tables-load-more" disabled={list.loadingMore}
              onClick={list.loadMore} aria-label={`Load more: ${label}`}>
              {list.loadingMore ? 'Loading...' : 'Load more'}
            </button>
          )}
        </>
      )}
    </section>
  );
}

function TableRow({ table, project }: { table: QuestTable; project?: Project }) {
  const updated = parseUTCTimestamp(table.updated_at);
  const owner = table.owner?.name || table.owner?.email || 'another user';
  const scope = table.shared_with_me ? `Shared by ${owner}`
    : table.scope === 'project' ? project?.name ?? 'Project' : 'Your table';
  return (
    <tr>
      <td className="tables-title-cell">
        <Link to={tableViewerPath(table.id)}>{table.title}</Link>
        {table.description && <span className="tables-list-description">{table.description}</span>}
      </td>
      <td><span className="tables-mode">
        {table.mode === 'public' && <Globe size={12} aria-hidden="true" />}
        {table.mode === 'public' ? 'Public' : 'Private'}
      </span></td>
      <td title={table.shared_with_me ? `${scope} · ${table.permission === 'write' ? 'Can edit' : 'Can view'}` : undefined}>{scope}</td>
      <td className="tables-number">{table.row_count.toLocaleString()}</td>
      <td className="tables-number">{table.column_count.toLocaleString()}</td>
      <td title={Number.isNaN(updated.getTime()) ? undefined : updated.toLocaleString()}>
        {formatRelativeTimestamp(table.updated_at)}
      </td>
    </tr>
  );
}
