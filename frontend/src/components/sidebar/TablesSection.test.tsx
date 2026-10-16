import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { tableFixture } from '../../test/tableFixtures';
import { TablesSection } from './TablesSection';

describe('TablesSection', () => {
  afterEach(cleanup);
  it('shows the five most recent rows without mutating the input and navigates', () => {
    const tables = Array.from({ length: 7 }, (_, i) => tableFixture({
      id: `id-${i}`, title: `Table ${i}`, updated_at: `2026-10-0${i + 1}T00:00:00`,
    }));
    const open = vi.fn();
    const all = vi.fn();
    render(<TablesSection tables={tables} loading={false} activeTableId="id-6"
      onOpenTable={open} onOpenAll={all} headerActive />);
    expect(screen.getAllByRole('button')).toHaveLength(6);
    expect(screen.queryByText('Table 0')).toBeNull();
    expect(tables[0].id).toBe('id-0');
    const active = screen.getByRole('button', { name: /Table 6/ });
    expect(active.getAttribute('aria-current')).toBe('page');
    fireEvent.click(active);
    expect(open).toHaveBeenCalledWith('id-6');
    fireEvent.click(screen.getByRole('button', { name: 'Tables' }));
    expect(all).toHaveBeenCalledOnce();
  });
  it('distinguishes loading, empty, and failed states', () => {
    const props = { tables: [], activeTableId: null, onOpenTable: vi.fn(), onOpenAll: vi.fn() };
    const { rerender } = render(<TablesSection {...props} loading />);
    expect(screen.queryByText(/create a table/)).toBeNull();
    rerender(<TablesSection {...props} loading={false} />);
    expect(screen.getByText('Ask Quest to create a table')).toBeTruthy();
    rerender(<TablesSection {...props} loading={false} error="Offline" />);
    expect(screen.getByText("Couldn't load tables")).toBeTruthy();
    expect(screen.queryByText(/create a table/)).toBeNull();
  });
});
