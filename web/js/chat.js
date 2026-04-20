/**
 * chat.js — Message rendering with live table + copy button + marked.js
 */

const messagesEl = document.getElementById('messages');

marked.setOptions({ breaks: true, gfm: true });

function removeWelcome() {
  const w = messagesEl.querySelector('.welcome-message');
  if (w) w.remove();
}

/**
 * Add a user message bubble.
 */
export function addUserMessage(filename, fields) {
  removeWelcome();
  const div = document.createElement('div');
  div.className = 'message user';

  let html = '';
  if (filename) {
    html += `<div class="msg-file">
      <svg class="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
        <path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/>
        <polyline points="14 2 14 8 20 8"/>
      </svg>
      ${escapeHtml(filename)}
    </div>`;
  }
  if (fields.length > 0) {
    html += '<div class="msg-fields">';
    for (const f of fields) {
      html += `<span class="msg-field-chip">${escapeHtml(f)}</span>`;
    }
    html += '</div>';
  }
  div.innerHTML = html;
  messagesEl.appendChild(div);
  scrollToBottom();
}

/**
 * Create an AI message with a live-updating table.
 */
export function createAiMessage() {
  removeWelcome();
  const div = document.createElement('div');
  div.className = 'message ai';
  div.innerHTML = '<div class="typing-indicator"><span></span><span></span><span></span></div>';
  messagesEl.appendChild(div);
  scrollToBottom();

  let typingRemoved = false;
  let statusArea = null;
  let progressBar = null;
  let tableWrapper = null;
  let tableBody = null;
  let summaryArea = null;
  let summaryAccum = '';
  const fieldRows = new Map(); // key → <tr> element

  function ensureTypingRemoved() {
    if (!typingRemoved) {
      div.innerHTML = '';
      typingRemoved = true;
    }
  }

  function getStatusArea() {
    if (!statusArea) {
      ensureTypingRemoved();
      statusArea = document.createElement('div');
      statusArea.className = 'ai-status-area';
      div.appendChild(statusArea);
    }
    return statusArea;
  }

  function getTable() {
    if (!tableWrapper) {
      ensureTypingRemoved();
      tableWrapper = document.createElement('div');
      tableWrapper.className = 'live-table-wrapper';
      tableWrapper.innerHTML = `
        <div class="live-table-header">
          <span class="live-table-title">提取结果</span>
          <button class="copy-table-btn" title="复制表格">
            <svg class="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <rect x="9" y="9" width="13" height="13" rx="2" ry="2"/>
              <path d="M5 15H4a2 2 0 01-2-2V4a2 2 0 012-2h9a2 2 0 012 2v1"/>
            </svg>
            复制
          </button>
        </div>
        <table class="live-table">
          <thead>
            <tr>
              <th>要素</th>
              <th>提取值</th>
              <th>来源页</th>
              <th>置信度</th>
              <th>核验</th>
            </tr>
          </thead>
          <tbody></tbody>
        </table>
      `;
      div.appendChild(tableWrapper);

      tableBody = tableWrapper.querySelector('tbody');

      // Copy button handler
      const copyBtn = tableWrapper.querySelector('.copy-table-btn');
      copyBtn.addEventListener('click', () => {
        const text = buildCopyText();
        navigator.clipboard.writeText(text).then(() => {
          copyBtn.innerHTML = `
            <svg class="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <polyline points="20 6 9 17 4 12"/>
            </svg>
            已复制`;
          setTimeout(() => {
            copyBtn.innerHTML = `
              <svg class="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <rect x="9" y="9" width="13" height="13" rx="2" ry="2"/>
                <path d="M5 15H4a2 2 0 01-2-2V4a2 2 0 012-2h9a2 2 0 012 2v1"/>
              </svg>
              复制`;
          }, 2000);
        });
      });
    }
    return tableBody;
  }

  function getSummaryArea() {
    if (!summaryArea) {
      ensureTypingRemoved();
      summaryArea = document.createElement('div');
      summaryArea.className = 'result-summary';
      div.appendChild(summaryArea);
    }
    return summaryArea;
  }

  function buildCopyText() {
    const lines = ['要素\t提取值\t来源页\t置信度\t核验'];
    for (const [key, tr] of fieldRows) {
      const cells = tr.querySelectorAll('td');
      const row = Array.from(cells).map(td => td.textContent.trim());
      lines.push(row.join('\t'));
    }
    return lines.join('\n');
  }

  return {
    element: div,

    /** Update or create the embedding progress bar */
    updateProgress(done, total, pct) {
      const area = getStatusArea();
      if (!progressBar) {
        progressBar = document.createElement('div');
        progressBar.className = 'embed-progress';
        progressBar.innerHTML = `
          <div class="embed-progress-label">
            <span class="embed-progress-text">Embedding</span>
            <span class="embed-progress-pct">0%</span>
          </div>
          <div class="embed-progress-track">
            <div class="embed-progress-fill"></div>
          </div>
          <div class="embed-progress-detail"></div>
        `;
        area.appendChild(progressBar);
      }
      progressBar.querySelector('.embed-progress-pct').textContent = `${pct}%`;
      progressBar.querySelector('.embed-progress-fill').style.width = `${pct}%`;
      progressBar.querySelector('.embed-progress-detail').textContent = `${done} / ${total} 文本块`;
      if (pct >= 100) {
        progressBar.classList.add('done');
      }
      scrollToBottom();
    },

    /** Remove the progress bar (called when index build is done) */
    removeProgress() {
      if (progressBar) {
        progressBar.remove();
        progressBar = null;
      }
    },

    /** Append a status line */
    appendStatus(text) {
      const area = getStatusArea();
      const line = document.createElement('div');
      line.className = 'ai-status-line';
      line.textContent = text.replace(/\n$/, '');
      area.appendChild(line);
      scrollToBottom();
    },

    /** Return final field values from the table (key → value string). */
    getFieldValues() {
      const result = {};
      for (const [key, tr] of fieldRows) {
        result[key] = tr.cells[1]?.textContent?.trim() || '';
      }
      return result;
    },

    /** Add or update a row in the live table */
    appendField(key, value, page, confidence) {
      const tbody = getTable();
      const confPct = Math.round(confidence * 100);
      const confClass = confPct >= 80 ? 'high' : confPct >= 50 ? 'medium' : 'low';
      const pageText = page ? `第${page}页` : '—';
      const valueText = value || '未找到';
      const valueClass = value ? '' : 'not-found';

      if (fieldRows.has(key)) {
        // Update existing row
        const tr = fieldRows.get(key);
        tr.cells[1].textContent = valueText;
        tr.cells[1].className = valueClass;
        tr.cells[2].textContent = pageText;
        tr.cells[3].innerHTML = `<span class="conf-badge ${confClass}">${confPct}%</span>`;
        return;
      }

      // Create new row
      const tr = document.createElement('tr');
      tr.className = 'animate-fade-in';
      tr.innerHTML = `
        <td class="field-key-cell">${escapeHtml(key)}</td>
        <td class="${valueClass}">${escapeHtml(valueText)}</td>
        <td>${pageText}</td>
        <td><span class="conf-badge ${confClass}">${confPct}%</span></td>
        <td class="verify-cell pending">
          <span class="verify-pending">⏳ 待核验</span>
        </td>
      `;
      tbody.appendChild(tr);
      fieldRows.set(key, tr);
      scrollToBottom();
    },

    /** Ensure a field row exists in the table (fallback for unparsed field_result) */
    ensureField(key, verified) {
      if (fieldRows.has(key)) return;
      const tbody = getTable();
      const tr = document.createElement('tr');
      tr.className = 'animate-fade-in';
      const verifyHtml = verified
        ? '<span class="verify-pass">✅ 通过</span>'
        : '<span class="verify-fail">❌ 未通过</span>';
      tr.innerHTML = `
        <td class="field-key-cell">${escapeHtml(key)}</td>
        <td class="not-found">未找到</td>
        <td>—</td>
        <td>—</td>
        <td class="verify-cell ${verified ? 'pass' : 'fail'}">${verifyHtml}</td>
      `;
      tbody.appendChild(tr);
      fieldRows.set(key, tr);
      scrollToBottom();
    },

    /** Update verification status for a row */
    updateVerification(key, verified) {
      const tr = fieldRows.get(key);
      if (!tr) return;
      const cell = tr.querySelector('.verify-cell');
      if (!cell) return;
      cell.className = `verify-cell ${verified ? 'pass' : 'fail'}`;
      cell.innerHTML = verified
        ? '<span class="verify-pass">✅ 通过</span>'
        : '<span class="verify-fail">❌ 未通过</span>';
    },

    /** Append final summary markdown */
    appendSummary(text) {
      summaryAccum += text;
      const area = getSummaryArea();
      area.innerHTML = marked.parse(summaryAccum);
      scrollToBottom();
    },

    getSummaryText() { return summaryAccum; },

    finish() {
      ensureTypingRemoved();
      if (!statusArea && !tableWrapper && !summaryArea) {
        div.innerHTML = '<span class="text-slate-500">(无内容)</span>';
      }
      // Make value cells editable
      if (tableBody) {
        for (const [key, tr] of fieldRows) {
          const valueCell = tr.cells[1];
          if (!valueCell) continue;
          valueCell.contentEditable = 'true';
          valueCell.classList.add('editable-cell');
          valueCell.title = '点击编辑';
          const original = valueCell.textContent;
          valueCell.dataset.original = original;
          valueCell.addEventListener('input', () => {
            const current = valueCell.textContent.trim();
            if (current !== original) {
              valueCell.classList.add('cell-modified');
            } else {
              valueCell.classList.remove('cell-modified');
            }
          });
          valueCell.addEventListener('blur', () => {
            if (!valueCell.textContent.trim()) {
              valueCell.textContent = original;
              valueCell.classList.remove('cell-modified');
            }
          });
        }
      }
      scrollToBottom();
    },
  };
}

/**
 * Create an editable checklist table (for compliance Step 1 result).
 * Returns the items, calls onEdit callback when user edits.
 */
export function createChecklistMessage(items, onEdit) {
  removeWelcome();
  const div = document.createElement('div');
  div.className = 'message ai';

  let html = `
    <div class="live-table-wrapper">
      <div class="live-table-header">
        <span class="live-table-title">📋 审查清单（可编辑）</span>
        <span class="text-xs text-slate-500">${items.length} 条要求</span>
      </div>
      <table class="live-table checklist-table">
        <thead>
          <tr>
            <th style="width:30px">#</th>
            <th>要求名称</th>
            <th>具体要求</th>
            <th>类别</th>
            <th>来源页</th>
            <th style="width:40px"></th>
          </tr>
        </thead>
        <tbody>`;

  items.forEach((item, i) => {
    html += `
      <tr data-idx="${i}">
        <td class="text-slate-500">${i + 1}</td>
        <td contenteditable="true" class="editable-cell cl-key" data-field="key">${escapeHtml(item.key || '')}</td>
        <td contenteditable="true" class="editable-cell cl-req" data-field="requirement">${escapeHtml(item.requirement || '')}</td>
        <td class="text-slate-500 text-xs">${escapeHtml(item.category || '')}</td>
        <td class="text-slate-500 text-xs">${item.source_page ? '第' + item.source_page + '页' : '—'}</td>
        <td><span class="cl-remove" data-idx="${i}" title="删除">✕</span></td>
      </tr>`;
  });

  html += '</tbody></table></div>';
  div.innerHTML = html;
  messagesEl.appendChild(div);

  // Wire edit events
  div.querySelectorAll('.editable-cell').forEach(cell => {
    cell.addEventListener('blur', () => {
      const tr = cell.closest('tr');
      const idx = parseInt(tr.dataset.idx);
      const field = cell.dataset.field;
      items[idx][field] = cell.textContent.trim();
      if (onEdit) onEdit([...items]);
    });
  });

  // Wire remove buttons
  div.querySelectorAll('.cl-remove').forEach(btn => {
    btn.addEventListener('click', () => {
      const idx = parseInt(btn.dataset.idx);
      items.splice(idx, 1);
      if (onEdit) onEdit([...items]);
      // Re-render
      div.remove();
      createChecklistMessage(items, onEdit);
    });
  });

  scrollToBottom();
  return items;
}

/**
 * Render compliance report as cards inside #checklist-panel (replaces checklist).
 */
export function createComplianceMessage(items) {
  const panel = document.getElementById('checklist-panel');
  if (!panel) return;

  const passCount = items.filter(it => it.verdict === 'pass').length;
  const failCount = items.filter(it => it.verdict === 'fail').length;
  const warnCount = items.filter(it => it.verdict === 'warn').length;

  const verdictConfig = {
    pass: { icon: '✅', label: '合规', cls: 'cr-pass' },
    fail: { icon: '❌', label: '不合规', cls: 'cr-fail' },
    warn: { icon: '⚠️', label: '需确认', cls: 'cr-warn' },
  };

  let cardsHtml = items.map((item, idx) => {
    const cfg = verdictConfig[item.verdict] || verdictConfig.warn;
    const page = item.source_page ? `第${item.source_page}页` : '—';
    const editedBadge = item.humanEdited
      ? `<span class="cr-edited-badge" title="已人工审核">✍️ 已人工审核</span>`
      : '';
    return `
      <div class="cr-card ${cfg.cls}" data-idx="${idx}">
        <div class="cr-card-header">
          <span class="cr-key">${escapeHtml(item.key || '')}</span>
          <div class="cr-head-right">
            ${editedBadge}
            <span class="cr-badge ${cfg.cls}">${cfg.icon} ${cfg.label}</span>
          </div>
        </div>
        <div class="cr-row">
          <span class="cr-label">招标要求</span>
          <span class="cr-value cr-req">${escapeHtml(item.requirement || '—')}</span>
        </div>
        <div class="cr-row">
          <span class="cr-label">投标响应</span>
          <span class="cr-value cr-response">${escapeHtml(item.response || '未找到')}</span>
        </div>
        <div class="cr-row">
          <span class="cr-label">判定依据</span>
          <span class="cr-value cr-reason-text ${cfg.cls}">${escapeHtml(item.reason || '—')}</span>
        </div>
        <div class="cr-footer">
          <span class="cr-page">来源：${page}</span>
          ${item.source_text ? `<span class="cr-source">${escapeHtml(item.source_text.slice(0, 60))}${item.source_text.length > 60 ? '…' : ''}</span>` : ''}
          <div class="cr-card-actions">
            <button class="cr-card-btn cr-edit-btn" data-idx="${idx}" title="人工编辑">✏️ 编辑</button>
            <button class="cr-card-btn cr-reeval-btn" data-idx="${idx}" title="对话补充重核">💬 对话重核</button>
          </div>
        </div>
      </div>`;
  }).join('');

  panel.innerHTML = `
    <div class="cl-header">
      <span class="cl-title">合规核查报告</span>
      <span class="cl-count">${items.length} 项</span>
      <div class="cr-summary">
        <span class="cr-sum-pass">✅ ${passCount}</span>
        <span class="cr-sum-fail">❌ ${failCount}</span>
        <span class="cr-sum-warn">⚠️ ${warnCount}</span>
      </div>
      <div class="cr-actions">
        <button class="cr-btn" id="cr-copy-btn" title="复制报告">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 01-2-2V4a2 2 0 012-2h9a2 2 0 012 2v1"/>
          </svg>复制
        </button>
        <button class="cr-btn" id="cr-pdf-btn" title="保存为 PDF">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/><polyline points="14 2 14 8 20 8"/>
            <line x1="12" y1="18" x2="12" y2="12"/><polyline points="9 15 12 18 15 15"/>
          </svg>PDF
        </button>
      </div>
    </div>
    <div class="cr-cards">${cardsHtml}</div>`;

  // ── Copy button ────────────────────────────────────────────────────
  panel.querySelector('#cr-copy-btn').addEventListener('click', (e) => {
    const btn = e.currentTarget;
    const verdictLabel = { pass: '✅ 合规', fail: '❌ 不合规', warn: '⚠️ 需确认' };
    const lines = [
      `合规核查报告（${items.length}项：✅${passCount} ❌${failCount} ⚠️${warnCount}）`,
      '='.repeat(60),
    ];
    items.forEach((item, i) => {
      lines.push(`\n[${i + 1}] ${item.key}  ${verdictLabel[item.verdict] || item.verdict}`);
      lines.push(`招标要求：${item.requirement || '—'}`);
      lines.push(`投标响应：${item.response || '未找到'}`);
      lines.push(`判定依据：${item.reason || '—'}`);
      lines.push(`来源页码：${item.source_page ? '第' + item.source_page + '页' : '—'}`);
      if (item.source_text) lines.push(`原文片段：${item.source_text}`);
    });
    navigator.clipboard.writeText(lines.join('\n')).then(() => {
      btn.innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="20 6 9 17 4 12"/></svg>已复制`;
      setTimeout(() => {
        btn.innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 01-2-2V4a2 2 0 012-2h9a2 2 0 012 2v1"/></svg>复制`;
      }, 2000);
    });
  });

  // ── PDF button ─────────────────────────────────────────────────────
  panel.querySelector('#cr-pdf-btn').addEventListener('click', () => {
    const verdictLabel = { pass: '✅ 合规', fail: '❌ 不合规', warn: '⚠️ 需确认' };
    const verdictColor = { pass: '#16a34a', fail: '#dc2626', warn: '#d97706' };
    const rowsBg = { pass: '#f0fdf4', fail: '#fef2f2', warn: '#fffbeb' };

    const rows = items.map((item, i) => {
      const v = item.verdict || 'warn';
      const color = verdictColor[v] || verdictColor.warn;
      const bg = rowsBg[v] || rowsBg.warn;
      return `
        <tr style="background:${bg}">
          <td style="padding:10px 12px;font-weight:600;color:#1e293b;white-space:nowrap;vertical-align:top">${i+1}. ${escapeHtml(item.key || '')}</td>
          <td style="padding:10px 12px;color:#475569;font-size:12px;vertical-align:top">${escapeHtml(item.requirement || '—')}</td>
          <td style="padding:10px 12px;color:#475569;font-size:12px;vertical-align:top">${escapeHtml(item.response || '未找到')}</td>
          <td style="padding:10px 12px;font-weight:700;color:${color};white-space:nowrap;text-align:center;vertical-align:top">${verdictLabel[v] || v}</td>
          <td style="padding:10px 12px;color:#475569;font-size:12px;vertical-align:top">${escapeHtml(item.reason || '—')}</td>
          <td style="padding:10px 12px;color:#94a3b8;font-size:11px;white-space:nowrap;vertical-align:top">${item.source_page ? '第'+item.source_page+'页' : '—'}</td>
        </tr>`;
    }).join('');

    const now = new Date().toLocaleString('zh-CN');
    const html = `<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8">
<title>合规核查报告</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: "PingFang SC","Microsoft YaHei",sans-serif; font-size:13px; color:#1e293b; padding:32px; }
  h1 { font-size:20px; font-weight:700; margin-bottom:4px; color:#0f172a; }
  .meta { font-size:12px; color:#64748b; margin-bottom:20px; }
  .summary { display:flex; gap:16px; margin-bottom:20px; }
  .sum-chip { padding:4px 14px; border-radius:20px; font-size:12px; font-weight:600; }
  .sum-pass { background:#dcfce7; color:#16a34a; }
  .sum-fail { background:#fee2e2; color:#dc2626; }
  .sum-warn { background:#fef9c3; color:#ca8a04; }
  table { width:100%; border-collapse:collapse; font-size:12px; }
  thead { background:#f1f5f9; }
  th { padding:9px 12px; text-align:left; font-weight:600; color:#475569; border-bottom:2px solid #e2e8f0; font-size:11px; }
  td { border-bottom:1px solid #e2e8f0; }
  tr:last-child td { border-bottom:none; }
  @media print { body { padding:16px; } }
</style></head><body>
<h1>合规核查报告</h1>
<div class="meta">生成时间：${now} &nbsp;|&nbsp; 共 ${items.length} 项</div>
<div class="summary">
  <span class="sum-chip sum-pass">✅ 合规 ${passCount}</span>
  <span class="sum-chip sum-fail">❌ 不合规 ${failCount}</span>
  <span class="sum-chip sum-warn">⚠️ 需确认 ${warnCount}</span>
</div>
<table>
  <thead><tr>
    <th style="width:12%">要求名称</th>
    <th style="width:22%">招标要求</th>
    <th style="width:22%">投标响应</th>
    <th style="width:8%">判定</th>
    <th style="width:28%">判定依据</th>
    <th style="width:8%">来源页</th>
  </tr></thead>
  <tbody>${rows}</tbody>
</table>
</body></html>`;

    const win = window.open('', '_blank', 'width=1000,height=700');
    win.document.write(html);
    win.document.close();
    win.focus();
    setTimeout(() => win.print(), 400);
  });

  // ── Per-card edit buttons ──────────────────────────────────────────
  panel.querySelectorAll('.cr-edit-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      const idx = parseInt(btn.dataset.idx);
      _enterComplianceCardEditMode(panel, items, idx);
    });
  });

  // ── Per-card reeval buttons (dispatch custom event to app.js) ──────
  panel.querySelectorAll('.cr-reeval-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      const idx = parseInt(btn.dataset.idx);
      const item = items[idx];
      if (!item) return;
      document.dispatchEvent(new CustomEvent('compliance:focus-field', {
        detail: { idx, item },
      }));
    });
  });
}

/**
 * Replace a card's row content with editable inputs (verdict dropdown + textareas).
 */
function _enterComplianceCardEditMode(panel, items, idx) {
  const card = panel.querySelector(`.cr-card[data-idx="${idx}"]`);
  if (!card) return;
  const item = items[idx];
  card.innerHTML = `
    <div class="cr-card-header">
      <span class="cr-key">${escapeHtml(item.key || '')}</span>
      <select class="cr-edit-verdict">
        <option value="pass" ${item.verdict === 'pass' ? 'selected' : ''}>✅ 合规</option>
        <option value="fail" ${item.verdict === 'fail' ? 'selected' : ''}>❌ 不合规</option>
        <option value="warn" ${item.verdict === 'warn' ? 'selected' : ''}>⚠️ 需确认</option>
      </select>
    </div>
    <div class="cr-row">
      <span class="cr-label">招标要求</span>
      <span class="cr-value cr-req">${escapeHtml(item.requirement || '—')}</span>
    </div>
    <div class="cr-row cr-edit-row">
      <span class="cr-label">投标响应</span>
      <textarea class="cr-edit-response" rows="2">${escapeHtml(item.response || '')}</textarea>
    </div>
    <div class="cr-row cr-edit-row">
      <span class="cr-label">判定依据</span>
      <textarea class="cr-edit-reason" rows="3">${escapeHtml(item.reason || '')}</textarea>
    </div>
    <div class="cr-edit-actions">
      <button class="cr-card-btn cr-save-btn">💾 保存</button>
      <button class="cr-card-btn cr-cancel-btn">取消</button>
    </div>
  `;
  card.querySelector('.cr-save-btn').addEventListener('click', () => {
    items[idx] = {
      ...item,
      verdict: card.querySelector('.cr-edit-verdict').value,
      response: card.querySelector('.cr-edit-response').value.trim(),
      reason: card.querySelector('.cr-edit-reason').value.trim(),
      humanEdited: true,
    };
    createComplianceMessage(items); // re-render full panel
  });
  card.querySelector('.cr-cancel-btn').addEventListener('click', () => {
    createComplianceMessage(items); // re-render to revert
  });
}

/**
 * Apply an updated item (e.g. from reevaluate) into the compliance list in place.
 */
export function applyComplianceUpdate(items, idx, patch) {
  if (idx < 0 || idx >= items.length) return;
  items[idx] = { ...items[idx], ...patch, humanEdited: true };
  createComplianceMessage(items);
}

/**
 * Show an error message.
 */
export function addErrorMessage(text) {
  removeWelcome();
  let safe;
  if (typeof text === 'string') {
    safe = text;
  } else if (text && typeof text === 'object') {
    safe = text.message || text.detail || text.error || JSON.stringify(text);
  } else {
    safe = String(text ?? '未知错误');
  }
  const div = document.createElement('div');
  div.className = 'message ai';
  div.innerHTML = `<span class="text-red-400 flex items-center gap-2">
    <svg class="w-4 h-4 flex-shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
      <circle cx="12" cy="12" r="10"/><line x1="15" y1="9" x2="9" y2="15"/>
      <line x1="9" y1="9" x2="15" y2="15"/>
    </svg>
    ${escapeHtml(safe)}
  </span>`;
  messagesEl.appendChild(div);
  scrollToBottom();
}

/**
 * Add a plain text user message bubble (for chat mode).
 */
export function addChatUserMessage(text) {
  removeWelcome();
  const div = document.createElement('div');
  div.className = 'message user';
  div.innerHTML = `<div class="msg-text">${escapeHtml(text)}</div>`;
  messagesEl.appendChild(div);
  scrollToBottom();
}

/**
 * Create a simple AI chat message that accumulates markdown.
 */
export function createChatAiMessage() {
  removeWelcome();
  const div = document.createElement('div');
  div.className = 'message ai';
  div.innerHTML = '<div class="typing-indicator"><span></span><span></span><span></span></div>';
  messagesEl.appendChild(div);
  scrollToBottom();

  let typingRemoved = false;
  let contentArea = null;
  let accum = '';

  function ensureTypingRemoved() {
    if (!typingRemoved) {
      div.innerHTML = '';
      typingRemoved = true;
    }
  }

  function getContentArea() {
    if (!contentArea) {
      ensureTypingRemoved();
      contentArea = document.createElement('div');
      contentArea.className = 'chat-content';
      div.appendChild(contentArea);
    }
    return contentArea;
  }

  return {
    element: div,

    append(text) {
      accum += text;
      const area = getContentArea();
      area.innerHTML = marked.parse(accum);
      scrollToBottom();
    },

    appendStatus(text) {
      const area = getContentArea();
      const line = document.createElement('div');
      line.className = 'ai-status-line';
      line.textContent = text;
      area.appendChild(line);
      scrollToBottom();
    },

    finish() {
      ensureTypingRemoved();
      if (!contentArea) {
        div.innerHTML = '<span class="text-slate-500">(无内容)</span>';
      }
      scrollToBottom();
    },
  };
}

function scrollToBottom() {
  messagesEl.scrollTo({ top: messagesEl.scrollHeight, behavior: 'smooth' });
}

function escapeHtml(str) {
  const d = document.createElement('div');
  d.textContent = str;
  return d.innerHTML;
}
