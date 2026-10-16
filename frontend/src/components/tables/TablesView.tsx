import { Link } from 'react-router-dom';
import { Table2 } from 'lucide-react';
import type { TablesRoute } from '../../utils/tablesRoute';
import { tablesListPath } from '../../utils/tablesRoute';
import { TablesListView } from './TablesListView';
import './TablesListView.css';

/** Gating outside the data tree ensures closed accounts fetch no tables. */
export function TablesView({ route, enabled }: { route: TablesRoute | null; enabled: boolean }) {
  if (!enabled) return (
    <div className="tables-status" role="status">
      <Table2 size={32} aria-hidden="true" />
      <p>Quest Tables is turned off for your account.</p>
      <p className="tables-list-muted">Ask an admin to enable it in Settings &gt; Features.</p>
    </div>
  );
  if (route?.kind === 'list') {
    return <TablesListView key={route.projectId ?? 'all'} projectId={route.projectId} />;
  }
  return (
    <div className="tables-status" role="status">
      <Table2 size={32} aria-hidden="true" />
      <h2>{route ? 'Table viewer' : 'Invalid table link'}</h2>
      <p>{route ? 'The table viewer is not available yet. You can access table data through Quest’s table tools.' : 'This URL is not a valid table address.'}</p>
      <Link to={tablesListPath()}>Back to all tables</Link>
    </div>
  );
}
