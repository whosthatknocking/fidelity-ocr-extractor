const state = {
  files: [],
  rows: [],
  columns: [],
  overviewRows: [],
  selectedFile: null,
  sortColumn: null,
  sortDirection: 'asc',
  columnFilters: {},
  activeFilterColumn: null,
  filterSearchTerm: '',
  currentPage: 1,
  pageSize: 100,
  columnWidths: {},
  activeTab: 'table',
};

const VALID_TABS = new Set(['table', 'overview', 'reference']);

const elements = {
  fileSelect: document.getElementById('fileSelect'),
  rowCount: document.getElementById('rowCount'),
  pageSizeSelect: document.getElementById('pageSizeSelect'),
  freshnessSummary: document.getElementById('freshnessSummary'),
  datasetCards: document.getElementById('datasetCards'),
  tableHead: document.querySelector('#dataTable thead'),
  tableBody: document.querySelector('#dataTable tbody'),
  tableStatus: document.getElementById('tableStatus'),
  prevPageButton: document.getElementById('prevPageButton'),
  nextPageButton: document.getElementById('nextPageButton'),
  pageInfo: document.getElementById('pageInfo'),
  overviewStatus: document.getElementById('overviewStatus'),
  overviewGrid: document.getElementById('overviewGrid'),
  referenceContent: document.getElementById('referenceContent'),
  rowModal: document.getElementById('rowModal'),
  rowModalTitle: document.getElementById('rowModalTitle'),
  rowModalMeta: document.getElementById('rowModalMeta'),
  rowDetailGrid: document.getElementById('rowDetailGrid'),
  closeRowModalButton: document.getElementById('closeRowModalButton'),
  filterPopover: document.getElementById('filterPopover'),
  filterPopoverTitle: document.getElementById('filterPopoverTitle'),
  filterSearchWrap: document.getElementById('filterSearchWrap'),
  filterValueSearch: document.getElementById('filterValueSearch'),
  filterRangeWrap: document.getElementById('filterRangeWrap'),
  filterMinValue: document.getElementById('filterMinValue'),
  filterMaxValue: document.getElementById('filterMaxValue'),
  filterOptionList: document.getElementById('filterOptionList'),
  clearFilterButton: document.getElementById('clearFilterButton'),
  tabButtons: [...document.querySelectorAll('.tab-button')],
  tableTab: document.getElementById('tableTab'),
  overviewTab: document.getElementById('overviewTab'),
  referenceTab: document.getElementById('referenceTab'),
  themeToggle: document.getElementById('themeToggle'),
};

function escapeHtml(text) {
  return String(text)
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;');
}

async function fetchJson(url) {
  const response = await fetch(url);
  if (!response.ok) {
    throw new Error(`Request failed: ${response.status}`);
  }
  return response.json();
}

function normalizeNumber(value) {
  if (value === null || value === undefined) return null;
  let cleaned = String(value).trim();
  if (!cleaned) return null;
  let multiplier = 1;
  if (cleaned.endsWith('M')) {
    multiplier = 1_000_000;
    cleaned = cleaned.slice(0, -1);
  }
  cleaned = cleaned.replaceAll('$', '').replaceAll('%', '').replaceAll(',', '').replaceAll(' ', '');
  cleaned = cleaned.replace(/[^0-9.+-]/g, '');
  if (!cleaned || ['+', '-', '.', '+.', '-.'].includes(cleaned)) return null;
  const number = Number(cleaned);
  return Number.isFinite(number) ? number * multiplier : null;
}

function formatCell(value, columnName = null) {
  if (value === null || value === undefined || value === '') return '—';
  if (columnName === 'total_gl' || columnName === 'change') return String(value);
  return String(value);
}

function compareValues(left, right) {
  const leftNumber = normalizeNumber(left);
  const rightNumber = normalizeNumber(right);
  if (leftNumber !== null && rightNumber !== null) return leftNumber - rightNumber;
  return String(left ?? '').localeCompare(String(right ?? ''), undefined, { numeric: true, sensitivity: 'base' });
}

function ensureColumnGroup() {
  let colgroup = document.querySelector('#dataTable colgroup');
  if (!colgroup) {
    colgroup = document.createElement('colgroup');
    document.getElementById('dataTable').prepend(colgroup);
  }
  colgroup.innerHTML = '';
  state.columns.forEach((column) => {
    const col = document.createElement('col');
    const width = state.columnWidths[column.name];
    if (width) col.style.width = `${width}px`;
    colgroup.appendChild(col);
  });
}

function startColumnResize(event, columnName) {
  event.preventDefault();
  event.stopPropagation();
  const th = event.target.closest('th');
  const startX = event.clientX;
  const startWidth = th.getBoundingClientRect().width;

  const onMove = (moveEvent) => {
    const nextWidth = Math.max(96, startWidth + (moveEvent.clientX - startX));
    state.columnWidths[columnName] = nextWidth;
    ensureColumnGroup();
  };

  const onUp = () => {
    window.removeEventListener('pointermove', onMove);
    window.removeEventListener('pointerup', onUp);
    document.body.classList.remove('is-resizing');
  };

  document.body.classList.add('is-resizing');
  window.addEventListener('pointermove', onMove);
  window.addEventListener('pointerup', onUp);
}

function hasActiveColumnFilter(columnName) {
  return Boolean(state.columnFilters[columnName]);
}

function isNumericColumn(columnName) {
  const column = state.columns.find((entry) => entry.name === columnName);
  return Boolean(column?.is_numeric);
}

function isRangeFilter(columnName) {
  return hasActiveColumnFilter(columnName) && state.columnFilters[columnName].type === 'range';
}

function getFilteredRows() {
  let rows = state.rows.slice();

  Object.entries(state.columnFilters).forEach(([columnName, filter]) => {
    if (filter.type === 'range') {
      rows = rows.filter((row) => {
        const numeric = normalizeNumber(row[columnName]);
        if (numeric === null) return false;
        if (filter.min !== null && numeric < filter.min) return false;
        if (filter.max !== null && numeric > filter.max) return false;
        return true;
      });
      return;
    }

    rows = rows.filter((row) => filter.values.has(String(row[columnName] ?? '')));
  });

  if (!state.sortColumn) return rows;
  return rows.sort((left, right) => {
    const comparison = compareValues(left[state.sortColumn], right[state.sortColumn]);
    return state.sortDirection === 'asc' ? comparison : -comparison;
  });
}

function getPagedRows(filteredRows) {
  if (state.pageSize === 'all') {
    return { rows: filteredRows, totalPages: 1 };
  }
  const totalPages = Math.max(1, Math.ceil(filteredRows.length / state.pageSize));
  state.currentPage = Math.min(state.currentPage, totalPages);
  const start = (state.currentPage - 1) * state.pageSize;
  return {
    rows: filteredRows.slice(start, start + state.pageSize),
    totalPages,
  };
}

function renderFreshnessSummary(summary) {
  if (!summary) {
    elements.freshnessSummary.innerHTML = '';
    return;
  }
  elements.freshnessSummary.innerHTML = `
    <article class="freshness-card">
      <span class="freshness-label">CSV Saved</span>
      <strong>${escapeHtml(summary.file_modified_at || '—')}</strong>
      <span class="freshness-detail">Filesystem write time</span>
    </article>
    <article class="freshness-card">
      <span class="freshness-label">Screenshot Time</span>
      <strong>${escapeHtml(summary.source_created_at || '—')}</strong>
      <span class="freshness-detail">Derived from PNG creation time</span>
    </article>
  `;
}

function renderDatasetCards(cards) {
  elements.datasetCards.innerHTML = (cards || []).map((card) => `
    <article class="freshness-card">
      <span class="freshness-label">${escapeHtml(card.name)}</span>
      <strong title="${escapeHtml(card.description || '')}">${escapeHtml(card.value || '—')}</strong>
    </article>
  `).join('');
}

function renderTable() {
  const filteredRows = getFilteredRows();
  const { rows, totalPages } = getPagedRows(filteredRows);
  elements.rowCount.textContent = filteredRows.length.toLocaleString();
  ensureColumnGroup();

  elements.tableHead.innerHTML = '';
  const headerRow = document.createElement('tr');
  state.columns.forEach((column) => {
    const th = document.createElement('th');
    const headerInner = document.createElement('div');
    headerInner.className = 'header-cell';
    const button = document.createElement('button');
    const sortMark = state.sortColumn === column.name ? (state.sortDirection === 'asc' ? ' ↑' : ' ↓') : '';
    button.innerHTML = `<span class="tooltip-label" title="${escapeHtml(column.description)}">${escapeHtml(column.name)}</span>${sortMark}`;
    button.addEventListener('click', () => {
      if (state.sortColumn === column.name) {
        state.sortDirection = state.sortDirection === 'asc' ? 'desc' : 'asc';
      } else {
        state.sortColumn = column.name;
        state.sortDirection = 'asc';
      }
      renderTable();
    });

    const filterButton = document.createElement('button');
    filterButton.type = 'button';
    filterButton.className = `header-filter-button ${hasActiveColumnFilter(column.name) ? 'active' : ''}`;
    filterButton.innerHTML = `<span class="header-filter-icon" aria-hidden="true"><svg viewBox="0 0 16 16"><path d="M2.5 3.5h11l-4.25 4.75v3.1l-2.5 1.45V8.25z"></path></svg></span>`;
    if (hasActiveColumnFilter(column.name)) {
      const filterCount = document.createElement('span');
      filterCount.className = 'header-filter-count';
      filterCount.textContent = isRangeFilter(column.name) ? 'R' : String(state.columnFilters[column.name].values.size);
      filterButton.appendChild(filterCount);
    }
    filterButton.addEventListener('click', (event) => {
      event.preventDefault();
      event.stopPropagation();
      if (state.activeFilterColumn === column.name && elements.filterPopover.classList.contains('open')) {
        closeFilterPopover();
      } else {
        openFilterPopover(column.name, filterButton);
      }
    });

    const resizer = document.createElement('span');
    resizer.className = 'column-resizer';
    resizer.addEventListener('pointerdown', (event) => startColumnResize(event, column.name));
    headerInner.appendChild(button);
    headerInner.appendChild(filterButton);
    headerInner.appendChild(resizer);
    th.appendChild(headerInner);
    headerRow.appendChild(th);
  });
  elements.tableHead.appendChild(headerRow);

  elements.tableBody.innerHTML = '';
  rows.forEach((row) => {
    const tr = document.createElement('tr');
    tr.className = 'data-row';
    tr.tabIndex = 0;
    tr.addEventListener('click', () => openRowModal(row));
    tr.addEventListener('keydown', (event) => {
      if (event.key === 'Enter' || event.key === ' ') {
        event.preventDefault();
        openRowModal(row);
      }
    });

    state.columns.forEach((column) => {
      const td = document.createElement('td');
      const rawValue = row[column.name];
      const numeric = normalizeNumber(rawValue);
      td.className = numeric !== null && numeric < 0 ? 'negative' : numeric !== null && numeric > 0 && /gl|change/.test(column.name) ? 'positive' : '';
      td.textContent = formatCell(rawValue, column.name);
      tr.appendChild(td);
    });
    elements.tableBody.appendChild(tr);
  });

  const pageStart = filteredRows.length === 0 ? 0 : (state.pageSize === 'all' ? 1 : ((state.currentPage - 1) * state.pageSize) + 1);
  const pageEnd = state.pageSize === 'all' ? filteredRows.length : Math.min(state.currentPage * state.pageSize, filteredRows.length);
  elements.tableStatus.textContent = `${state.selectedFile} · showing ${pageStart.toLocaleString()}-${pageEnd.toLocaleString()} of ${filteredRows.length.toLocaleString()} filtered rows`;
  elements.pageInfo.textContent = state.pageSize === 'all' ? 'All rows' : `Page ${state.currentPage} of ${totalPages}`;
  elements.prevPageButton.disabled = state.pageSize === 'all' || state.currentPage <= 1;
  elements.nextPageButton.disabled = state.pageSize === 'all' || state.currentPage >= totalPages;
}

function openRowModal(row) {
  const title = [row.symbol, row.instrument_type, row.expiration].filter(Boolean).join(' · ') || 'Record';
  elements.rowModalTitle.textContent = title;
  elements.rowModalMeta.textContent = state.selectedFile ? `Source ${state.selectedFile}` : '';
  elements.rowDetailGrid.innerHTML = '';

  state.columns.forEach((column) => {
    const item = document.createElement('article');
    item.className = 'row-detail-item';
    item.innerHTML = `
      <div class="row-detail-label">${escapeHtml(column.name)}</div>
      <div class="row-detail-value">${escapeHtml(formatCell(row[column.name], column.name))}</div>
      <div class="row-detail-description">${escapeHtml(column.description || '')}</div>
    `;
    elements.rowDetailGrid.appendChild(item);
  });

  elements.rowModal.classList.add('open');
  elements.rowModal.setAttribute('aria-hidden', 'false');
  document.body.classList.add('modal-open');
}

function closeRowModal() {
  elements.rowModal.classList.remove('open');
  elements.rowModal.setAttribute('aria-hidden', 'true');
  document.body.classList.remove('modal-open');
}

function closeFilterPopover() {
  state.activeFilterColumn = null;
  elements.filterPopover.classList.remove('open');
  elements.filterPopover.setAttribute('aria-hidden', 'true');
}

function getUniqueColumnValues(columnName) {
  return [...new Set(state.rows.map((row) => String(row[columnName] ?? '')))].sort(compareValues);
}

function openFilterPopover(columnName, anchor) {
  state.activeFilterColumn = columnName;
  elements.filterPopoverTitle.textContent = columnName;
  const isNumeric = isNumericColumn(columnName);
  elements.filterSearchWrap.hidden = isNumeric;
  elements.filterRangeWrap.hidden = !isNumeric;
  elements.filterOptionList.innerHTML = '';
  elements.filterValueSearch.value = '';
  elements.filterMinValue.value = '';
  elements.filterMaxValue.value = '';

  if (isNumeric) {
    const filter = state.columnFilters[columnName];
    if (filter?.type === 'range') {
      elements.filterMinValue.value = filter.min ?? '';
      elements.filterMaxValue.value = filter.max ?? '';
    }
    renderRangeFilter(columnName);
  } else {
    renderValueFilter(columnName);
  }

  const rect = anchor.getBoundingClientRect();
  elements.filterPopover.style.top = `${window.scrollY + rect.bottom + 6}px`;
  elements.filterPopover.style.left = `${Math.max(10, window.scrollX + rect.left - 220)}px`;
  elements.filterPopover.classList.add('open');
  elements.filterPopover.setAttribute('aria-hidden', 'false');
}

function renderRangeFilter(columnName) {
  elements.filterOptionList.innerHTML = '<div class="filter-option-empty">Set a min and/or max value for this numeric column.</div>';
  const applyRange = () => {
    const min = elements.filterMinValue.value === '' ? null : Number(elements.filterMinValue.value);
    const max = elements.filterMaxValue.value === '' ? null : Number(elements.filterMaxValue.value);
    if (min === null && max === null) {
      delete state.columnFilters[columnName];
    } else {
      state.columnFilters[columnName] = { type: 'range', min, max };
    }
    state.currentPage = 1;
    renderTable();
  };
  elements.filterMinValue.oninput = applyRange;
  elements.filterMaxValue.oninput = applyRange;
}

function renderValueFilter(columnName) {
  const values = getUniqueColumnValues(columnName).filter((value) => value.toLowerCase().includes(elements.filterValueSearch.value.trim().toLowerCase()));
  const active = state.columnFilters[columnName]?.values;
  if (values.length === 0) {
    elements.filterOptionList.innerHTML = '<div class="filter-option-empty">No values match the current search.</div>';
    return;
  }
  elements.filterOptionList.innerHTML = '';
  values.forEach((value) => {
    const label = document.createElement('label');
    label.className = 'filter-option';
    const checkbox = document.createElement('input');
    checkbox.type = 'checkbox';
    checkbox.checked = active ? active.has(value) : true;
    checkbox.addEventListener('change', () => {
      const nextValues = active ? new Set(active) : new Set(getUniqueColumnValues(columnName));
      if (checkbox.checked) {
        nextValues.add(value);
      } else {
        nextValues.delete(value);
      }
      if (nextValues.size === getUniqueColumnValues(columnName).length) {
        delete state.columnFilters[columnName];
      } else {
        state.columnFilters[columnName] = { type: 'value', values: nextValues };
      }
      state.currentPage = 1;
      renderTable();
      renderValueFilter(columnName);
    });
    const span = document.createElement('span');
    span.textContent = value || '—';
    label.appendChild(checkbox);
    label.appendChild(span);
    elements.filterOptionList.appendChild(label);
  });
}

function renderOverview(rows) {
  elements.overviewStatus.textContent = `${state.selectedFile} · ${rows.length} symbol groups`;
  elements.overviewGrid.innerHTML = rows.map((row) => `
    <article class="overview-card">
      <div>
        <p class="eyebrow">Symbol</p>
        <h2>${escapeHtml(row.symbol)}</h2>
      </div>
      <div class="overview-metrics">
        <div class="overview-metric"><span>Rows</span><strong>${escapeHtml(String(row.row_count))}</strong></div>
        <div class="overview-metric"><span>Options</span><strong>${escapeHtml(String(row.option_rows))}</strong></div>
        <div class="overview-metric"><span>Equities</span><strong>${escapeHtml(String(row.equity_rows))}</strong></div>
        <div class="overview-metric"><span>Expirations</span><strong>${escapeHtml(String(row.expiration_count))}</strong></div>
        <div class="overview-metric"><span>Last</span><strong>${escapeHtml(String(row.latest_last || '—'))}</strong></div>
        <div class="overview-metric"><span>Total G/L</span><strong class="${Number(row.total_gl) < 0 ? 'negative' : 'positive'}">${escapeHtml(String(row.total_gl ?? '—'))}</strong></div>
      </div>
      <p>${escapeHtml(row.descriptions || 'No description available.')}</p>
    </article>
  `).join('');
}

function parseTableCells(line) {
  const trimmed = line.trim();
  if (!trimmed.includes('|')) return [];
  return trimmed.replace(/^\|/, '').replace(/\|$/, '').split('|').map((cell) => cell.trim());
}

function isMarkdownTableSeparator(line) {
  const cells = parseTableCells(line);
  return cells.length > 0 && cells.every((cell) => /^:?-{3,}:?$/.test(cell));
}

function renderMarkdownTable(lines) {
  const [headerLine, , ...bodyLines] = lines;
  const headers = parseTableCells(headerLine);
  const bodyHtml = bodyLines
    .map((line) => parseTableCells(line))
    .filter((cells) => cells.length > 0)
    .map((cells) => `<tr>${headers.map((_, index) => `<td>${inlineMarkdown(cells[index] || '')}</td>`).join('')}</tr>`)
    .join('');
  return `<table><thead><tr>${headers.map((cell) => `<th>${inlineMarkdown(cell)}</th>`).join('')}</tr></thead><tbody>${bodyHtml}</tbody></table>`;
}

function inlineMarkdown(text) {
  return escapeHtml(text).replace(/`([^`]+)`/g, '<code>$1</code>');
}

function renderMarkdown(markdown) {
  const lines = markdown.split('\n');
  let html = '';
  let inList = false;
  let inCode = false;

  const closeList = () => {
    if (inList) {
      html += '</ul>';
      inList = false;
    }
  };

  for (let index = 0; index < lines.length; index += 1) {
    const line = lines[index];
    if (line.startsWith('```')) {
      closeList();
      html += inCode ? '</code></pre>' : '<pre><code>';
      inCode = !inCode;
      continue;
    }
    if (inCode) {
      html += `${escapeHtml(line)}\n`;
      continue;
    }
    if (!line.trim()) {
      closeList();
      continue;
    }
    const nextLine = lines[index + 1];
    if (nextLine && line.includes('|') && isMarkdownTableSeparator(nextLine)) {
      closeList();
      const tableLines = [line, nextLine];
      index += 2;
      while (index < lines.length && lines[index].includes('|')) {
        tableLines.push(lines[index]);
        index += 1;
      }
      index -= 1;
      html += renderMarkdownTable(tableLines);
      continue;
    }
    if (line.startsWith('# ')) {
      closeList();
      html += `<h1>${inlineMarkdown(line.slice(2))}</h1>`;
      continue;
    }
    if (line.startsWith('## ')) {
      closeList();
      html += `<h2>${inlineMarkdown(line.slice(3))}</h2>`;
      continue;
    }
    if (line.startsWith('### ')) {
      closeList();
      html += `<h3>${inlineMarkdown(line.slice(4))}</h3>`;
      continue;
    }
    if (line.startsWith('- ')) {
      if (!inList) {
        html += '<ul>';
        inList = true;
      }
      html += `<li>${inlineMarkdown(line.slice(2))}</li>`;
      continue;
    }
    closeList();
    html += `<p>${inlineMarkdown(line)}</p>`;
  }

  closeList();
  if (inCode) html += '</code></pre>';
  return html;
}

function activateTab(tabName) {
  const nextTab = VALID_TABS.has(tabName) ? tabName : 'table';
  state.activeTab = nextTab;
  elements.tabButtons.forEach((button) => {
    button.classList.toggle('active', button.dataset.tab === nextTab);
  });
  elements.tableTab.classList.toggle('active', nextTab === 'table');
  elements.overviewTab.classList.toggle('active', nextTab === 'overview');
  elements.referenceTab.classList.toggle('active', nextTab === 'reference');
}

function updateThemeToggleLabel(theme) {
  elements.themeToggle.textContent = theme === 'dark' ? 'Light' : 'Dark';
}

function setTheme(theme) {
  document.documentElement.dataset.theme = theme;
  document.body.dataset.theme = theme;
  localStorage.setItem('fidelity-extractor-theme', theme);
  updateThemeToggleLabel(theme);
}

function initializeTheme() {
  setTheme(localStorage.getItem('fidelity-extractor-theme') || 'light');
}

async function loadFiles() {
  const payload = await fetchJson('/api/files');
  state.files = payload.files || [];
  elements.fileSelect.innerHTML = '';
  state.files.forEach((file) => {
    const option = document.createElement('option');
    option.value = file.name;
    option.textContent = file.label;
    option.title = file.name;
    elements.fileSelect.appendChild(option);
  });
}

async function loadData(fileName) {
  const [dataPayload, overviewPayload] = await Promise.all([
    fetchJson(`/api/data?file=${encodeURIComponent(fileName)}`),
    fetchJson(`/api/overview?file=${encodeURIComponent(fileName)}`),
  ]);
  state.selectedFile = dataPayload.selected_file;
  state.rows = dataPayload.rows;
  state.columns = dataPayload.columns;
  state.overviewRows = overviewPayload.rows;
  state.columnFilters = {};
  state.currentPage = 1;
  state.columnWidths = {};
  elements.fileSelect.value = state.selectedFile;
  renderFreshnessSummary(dataPayload.freshness_summary);
  renderDatasetCards(dataPayload.dataset_cards);
  renderOverview(state.overviewRows);
  renderTable();
}

async function loadReference() {
  const payload = await fetchJson('/api/reference');
  elements.referenceContent.innerHTML = renderMarkdown(payload.markdown);
}

async function initialize() {
  initializeTheme();
  await Promise.all([loadFiles(), loadReference()]);

  if (state.files.length > 0) {
    await loadData(state.files[0].name);
  } else {
    elements.tableStatus.textContent = 'No CSV files found in output/.';
    elements.overviewStatus.textContent = 'No CSV files found in output/.';
  }

  elements.fileSelect.addEventListener('change', async (event) => {
    await loadData(event.target.value);
  });
  elements.pageSizeSelect.addEventListener('change', (event) => {
    state.pageSize = event.target.value === 'all' ? 'all' : Number(event.target.value);
    state.currentPage = 1;
    renderTable();
  });
  elements.prevPageButton.addEventListener('click', () => {
    state.currentPage = Math.max(1, state.currentPage - 1);
    renderTable();
  });
  elements.nextPageButton.addEventListener('click', () => {
    state.currentPage += 1;
    renderTable();
  });
  elements.tabButtons.forEach((button) => {
    button.addEventListener('click', () => activateTab(button.dataset.tab));
  });
  elements.themeToggle.addEventListener('click', () => {
    const nextTheme = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
    setTheme(nextTheme);
  });
  elements.closeRowModalButton.addEventListener('click', closeRowModal);
  elements.rowModal.addEventListener('click', (event) => {
    if (event.target.dataset.closeModal === 'true') closeRowModal();
  });
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') {
      closeRowModal();
      closeFilterPopover();
    }
  });
  document.addEventListener('click', (event) => {
    if (!elements.filterPopover.contains(event.target) && !event.target.closest('.header-filter-button')) {
      closeFilterPopover();
    }
  });
  elements.filterValueSearch.addEventListener('input', () => {
    if (state.activeFilterColumn) renderValueFilter(state.activeFilterColumn);
  });
  elements.clearFilterButton.addEventListener('click', () => {
    if (!state.activeFilterColumn) return;
    delete state.columnFilters[state.activeFilterColumn];
    state.currentPage = 1;
    closeFilterPopover();
    renderTable();
  });
}

initialize().catch((error) => {
  elements.tableStatus.textContent = error.message;
});
