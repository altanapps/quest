import { Table2 } from 'lucide-react';
import { SIDEBAR_TABLE_LIMIT, type QuestTable } from '../../api/tablesApi';
import { formatSidebarDocTime, formatSidebarDocTimeTitle } from '../../utils/sidebarDocs';
import { ChevronRightIcon } from './icons';

export interface TablesSectionProps {
  tables: QuestTable[];
  loading: boolean;
  error?: string | null;
  activeTableId: string | null;
  onOpenTable: (id: string) => void;
  onOpenAll: () => void;
  headerActive?: boolean;
}

export function TablesSection({
  tables, loading, error, activeTableId, onOpenTable, onOpenAll, headerActive = false,
}: TablesSectionProps) {
  const items = [...tables].sort((a, b) =>
    b.updated_at.localeCompare(a.updated_at) || b.id.localeCompare(a.id)).slice(0, SIDEBAR_TABLE_LIMIT);
  return (
    <div className="tables-section">
      <div className="section-header docs-section-header">
        <button type="button"
          className={`docs-section-header-button${headerActive ? ' active' : ''}`}
          onClick={onOpenAll} title="All tables" aria-current={headerActive ? 'page' : undefined}>
          <span className="section-label">Tables</span>
          <ChevronRightIcon className="docs-section-chevron" />
        </button>
      </div>
      {items.map((table) => (
        <button key={table.id} type="button"
          className={`conversation-item doc-row${activeTableId === table.id ? ' active' : ''}`}
          onClick={() => onOpenTable(table.id)} title={table.title}
          aria-current={activeTableId === table.id ? 'page' : undefined}>
          <Table2 size={14} className="doc-row-icon" aria-hidden="true" />
          <span className="doc-row-title">{table.title}</span>
          {table.mode === 'public' && <span className="tables-mode">Public</span>}
          <span className="doc-row-time" title={formatSidebarDocTimeTitle(table.updated_at)}>
            {formatSidebarDocTime(table.updated_at)}
          </span>
        </button>
      ))}
      {!loading && tables.length === 0 && (
        <div className="sidebar-empty">{error ? "Couldn't load tables" : 'Ask Quest to create a table'}</div>
      )}
    </div>
  );
}
