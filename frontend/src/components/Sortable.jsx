import { useMemo, useState } from 'react';

const compare = (a, b) => {
  if (typeof a === 'string' || typeof b === 'string') return String(a).localeCompare(String(b));
  return a - b;
};

// Sorts `rows` by the column the user last clicked. `columns` maps a column key to a
// function returning that column's sortable value for a row. Rows with no value
// (null/undefined/NaN) always go last, whichever way the column is sorted.
export function useSort(rows, columns, initial = { key: null, dir: 'asc' }) {
  const [sort, setSort] = useState(initial);

  const sorted = useMemo(() => {
    const get = columns[sort.key];
    if (!get) return rows;
    const sign = sort.dir === 'asc' ? 1 : -1;
    const empty = (v) => v == null || v === '' || Number.isNaN(v);
    return rows
      .map((row, i) => ({ row, i, v: get(row) }))
      .sort((a, b) => {
        if (empty(a.v) || empty(b.v)) return empty(a.v) - empty(b.v) || a.i - b.i;
        return sign * compare(a.v, b.v) || a.i - b.i;
      })
      .map((x) => x.row);
  }, [rows, columns, sort]);

  // First click on a text column sorts A→Z; on a number column, high→low.
  const toggle = (key) =>
    setSort((s) => {
      if (s.key === key) return { key, dir: s.dir === 'asc' ? 'desc' : 'asc' };
      const sample = rows.map(columns[key]).find((v) => v != null);
      return { key, dir: typeof sample === 'string' ? 'asc' : 'desc' };
    });

  return { sorted, sort, toggle };
}

export function SortTh({ col, sort, onSort, title, children }) {
  const active = sort.key === col;
  return (
    <th className={active ? 'sorted' : ''} title={title} aria-sort={active ? (sort.dir === 'asc' ? 'ascending' : 'descending') : 'none'}>
      <button type="button" className="sort-btn" onClick={() => onSort(col)}>
        {children}
        <span className="sort-arrow">{active ? (sort.dir === 'asc' ? '▲' : '▼') : '↕'}</span>
      </button>
    </th>
  );
}
