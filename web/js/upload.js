/**
 * upload.js — File upload + field selector with templates
 */

import { uploadFile, deleteFile } from './api.js';

// ── State ────────────────────────────────────────────────────────────────
let currentMode = 'extract'; // 'extract' or 'compliance'
let currentFileId = null;
let currentFileName = null;
let selectedFields = [];

// Compliance mode state
let tenderFileId = null;
let tenderFileName = null;
let bidFileId = null;
let bidFileName = null;
let checklistData = null; // 审查清单数据（LLM 提取结果）
let complianceStep = 1; // 1=上传招标书, 2=上传投标书+核查

// 信息提取结果缓存（key → value），切换合规核查时直接填入招标描述
let extractionCache = {};
// 从 localStorage 恢复（页面刷新后仍可用）
try {
  const _stored = localStorage.getItem('extractionCache');
  if (_stored) extractionCache = JSON.parse(_stored);
} catch (_) {}

// ── 分类字段模板（表3-1）────────────────────────────────────────────────
const FIELD_CATEGORIES = [
  { id: 'basic', label: '基础信息',
    fields: ['项目名称与编号', '招标人信息', '项目概况', '招标方式'] },
  { id: 'bid_req', label: '投标要求',
    fields: ['投标人资格条件', '联合体投标', '投标文件格式'] },
  { id: 'tech', label: '技术条款',
    fields: ['技术规范与标准', '工期/交付期', '售后服务'] },
  { id: 'commercial', label: '商务条款',
    fields: ['合同主要条款', '报价要求', '违约责任'] },
  { id: 'schedule', label: '时间安排',
    fields: ['关键日期', '答疑与澄清'] },
  { id: 'eval', label: '评标标准',
    fields: ['评标方法', '评分细则', '废标条款'] },
  { id: 'other', label: '其他重要信息',
    fields: ['附件与参考资料', '质疑与投诉渠道', '知识产权与保密'] },
  { id: 'notice', label: '注意事项',
    fields: ['完整性要求', '合规性要求', '明确性要求'] },
];

const ALL_FIELDS = FIELD_CATEGORIES.flatMap(c => c.fields);

// ── DOM refs ─────────────────────────────────────────────────────────────
const fileInput = document.getElementById('file-input');
const btnUpload = document.getElementById('btn-upload');
const attachmentsEl = document.getElementById('input-attachments');
const chipsEl = document.getElementById('field-chips');
const btnTemplate = document.getElementById('btn-template');
const dropdown = document.getElementById('template-dropdown');
const btnExisting = document.getElementById('btn-existing');
const fileDropdown = document.getElementById('file-dropdown');

// ── Existing file picker ─────────────────────────────────────────────────
btnExisting.addEventListener('click', async (e) => {
  e.stopPropagation();
  if (fileDropdown.classList.contains('open')) {
    fileDropdown.classList.remove('open');
    return;
  }
  fileDropdown.classList.add('open');
  if (currentMode === 'compliance') {
    await loadDualFileDropdown(fileDropdown);
  } else {
    await loadFileDropdown(fileDropdown, 'tender', (id, name) => {
      currentFileId = id;
      currentFileName = name;
      renderAttachment();
      fileDropdown.classList.remove('open');
    });
  }
});

// ── File upload ──────────────────────────────────────────────────────────
// 合规模式：上传前弹出类型选择浮层
let uploadTypeOverlay = null;

btnUpload.addEventListener('click', (e) => {
  e.stopPropagation();
  if (currentMode === 'compliance') {
    showUploadTypeOverlay();
  } else {
    fileInput.dataset.uploadType = 'tender';
    fileInput.click();
  }
});

function showUploadTypeOverlay() {
  // 已有浮层则关闭
  if (uploadTypeOverlay) { uploadTypeOverlay.remove(); uploadTypeOverlay = null; return; }
  uploadTypeOverlay = document.createElement('div');
  uploadTypeOverlay.className = 'upload-type-overlay';
  uploadTypeOverlay.innerHTML = `
    <button class="upload-type-btn" data-type="tender">📋 招标书</button>
    <button class="upload-type-btn" data-type="bid">📄 投标书</button>`;
  btnUpload.parentElement.style.position = 'relative';
  btnUpload.parentElement.appendChild(uploadTypeOverlay);

  uploadTypeOverlay.querySelectorAll('.upload-type-btn').forEach(btn => {
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      const type = btn.dataset.type;
      uploadTypeOverlay.remove(); uploadTypeOverlay = null;
      fileInput.dataset.uploadType = type;
      fileInput.click();
    });
  });
}

document.addEventListener('click', () => {
  if (uploadTypeOverlay) { uploadTypeOverlay.remove(); uploadTypeOverlay = null; }
});

fileInput.addEventListener('change', async () => {
  const file = fileInput.files[0];
  if (!file) return;
  const type = fileInput.dataset.uploadType || 'tender';
  await doUpload(file, type);
  fileInput.value = '';
});

async function doUpload(file, fileType = 'tender') {
  // 创建带圆形进度的上传标签
  const tag = document.createElement('span');
  tag.className = 'attachment-tag uploading-tag';
  tag.innerHTML = `
    <svg class="upload-ring" width="20" height="20" viewBox="0 0 20 20">
      <circle class="upload-ring-bg" cx="10" cy="10" r="8" fill="none" stroke="currentColor" stroke-width="2" opacity="0.15"/>
      <circle class="upload-ring-fill" cx="10" cy="10" r="8" fill="none" stroke="currentColor" stroke-width="2.5"
              stroke-dasharray="50.27" stroke-dashoffset="50.27" stroke-linecap="round"
              transform="rotate(-90 10 10)"/>
    </svg>
    <span class="upload-label">上传中: ${escapeHtml(file.name)}</span>
    <span class="upload-pct">0%</span>
  `;
  attachmentsEl.appendChild(tag);

  const circle = tag.querySelector('.upload-ring-fill');
  const pctEl = tag.querySelector('.upload-pct');
  const circumference = 2 * Math.PI * 8; // ≈ 50.27

  function onProgress(pct) {
    const offset = circumference * (1 - pct / 100);
    circle.style.strokeDashoffset = offset;
    pctEl.textContent = `${pct}%`;
  }

  try {
    const result = await uploadFile(file, fileType, onProgress);
    tag.remove();
    if (currentMode === 'compliance') {
      if (fileType === 'tender') { tenderFileId = result.id; tenderFileName = result.filename; }
      else { bidFileId = result.id; bidFileName = result.filename; }
    } else {
      currentFileId = result.id;
      currentFileName = result.filename;
    }
    renderAttachment();
  } catch (err) {
    tag.remove();
    attachmentsEl.insertAdjacentHTML('beforeend',
      `<span class="attachment-tag" style="color:var(--danger)">❌ ${escapeHtml(err.message)}</span>`);
  }
}

/**
 * 加载文件下拉列表（带删除按钮）。
 * @param {HTMLElement} container - 下拉容器
 * @param {'tender'|'bid'} fileType - 过滤类型
 * @param {(id:string, name:string) => void} onSelect - 选择回调
 */
async function loadFileDropdown(container, fileType, onSelect) {
  container.innerHTML = '<div class="file-dropdown-empty">加载中...</div>';
  try {
    const resp = await fetch(`/v1/files/list?type=${fileType}`);
    const data = await resp.json();
    if (!data.files || data.files.length === 0) {
      const label = fileType === 'tender' ? '招标书' : '投标书';
      container.innerHTML = `<div class="file-dropdown-empty">暂无已上传${label}</div>`;
      return;
    }
    container.innerHTML = '';
    for (const f of data.files) {
      const item = document.createElement('div');
      item.className = 'file-option';
      item.dataset.id = f.id;
      item.dataset.name = f.filename;
      const badgeClass = f.has_vectors ? 'cached' : 'not-cached';
      const badgeText = f.has_vectors ? '已索引' : '未索引';
      item.innerHTML = `
        <span class="file-name">${escapeHtml(f.filename)}</span>
        <span class="file-badge ${badgeClass}">${badgeText}</span>
        <button class="file-delete-btn" title="删除文件" data-id="${f.id}">🗑</button>`;
      // 选择文件
      item.addEventListener('click', (e) => {
        if (e.target.closest('.file-delete-btn')) return;
        onSelect(f.id, f.filename);
      });
      // 删除文件
      item.querySelector('.file-delete-btn').addEventListener('click', async (e) => {
        e.stopPropagation();
        if (!confirm(`确认删除「${f.filename}」？`)) return;
        try {
          await deleteFile(f.id);
          // 如果删除的是当前选中文件则清空
          if (fileType === 'tender' && currentFileId === f.id) {
            currentFileId = null; currentFileName = null; renderAttachment();
          }
          await loadFileDropdown(container, fileType, onSelect);
        } catch (err) {
          alert(`删除失败: ${err.message}`);
        }
      });
      container.appendChild(item);
    }
  } catch {
    container.innerHTML = '<div class="file-dropdown-empty">加载失败</div>';
  }
}

function renderAttachment() {
  if (currentMode === 'compliance') {
    let html = '';
    // 合规模式只显示投标书标签（招标书已隐含在审查清单中，无需展示）
    if (bidFileName) {
      html += `<span class="attachment-tag attachment-tag-bid">
        📄 ${escapeHtml(bidFileName)}
        <span class="remove" data-remove="bid">✕</span>
      </span>`;
    }
    attachmentsEl.innerHTML = html;
    attachmentsEl.querySelectorAll('[data-remove]').forEach(btn => {
      btn.addEventListener('click', () => {
        bidFileId = null; bidFileName = null;
        renderAttachment();
      });
    });
  } else {
    if (!currentFileName) { attachmentsEl.innerHTML = ''; return; }
    attachmentsEl.innerHTML = `
      <span class="attachment-tag">
        📎 ${escapeHtml(currentFileName)}
        <span class="remove" onclick="window.__removeFile()">✕</span>
      </span>`;
  }
}

window.__removeFile = () => {
  currentFileId = null; currentFileName = null; attachmentsEl.innerHTML = '';
};

// ── Drag & drop ──────────────────────────────────────────────────────────
let dragOverlay = null;

document.addEventListener('dragenter', (e) => {
  e.preventDefault();
  if (!dragOverlay) {
    dragOverlay = document.createElement('div');
    dragOverlay.className = 'drag-overlay';
    dragOverlay.innerHTML = '<span class="drag-overlay-text">拖放 PDF 文件到此处</span>';
    document.body.appendChild(dragOverlay);
  }
});

document.addEventListener('dragover', (e) => e.preventDefault());

document.addEventListener('dragleave', (e) => {
  if (e.relatedTarget === null && dragOverlay) {
    dragOverlay.remove();
    dragOverlay = null;
  }
});

document.addEventListener('drop', async (e) => {
  e.preventDefault();
  if (dragOverlay) { dragOverlay.remove(); dragOverlay = null; }
  const file = e.dataTransfer.files[0];
  if (file && /\.(pdf|doc|docx)$/i.test(file.name)) {
    // 合规模式：无招标书先传招标书，有招标书传投标书
    const type = (currentMode === 'compliance' && tenderFileId) ? 'bid' : 'tender';
    await doUpload(file, type);
  }
});

// ── Field template panel ─────────────────────────────────────────────────
buildTemplatePanel();

// 从 localStorage 恢复字段选择（页面刷新后保持选中状态）
try {
  const _sf = localStorage.getItem('selectedFields');
  if (_sf) {
    const restored = JSON.parse(_sf);
    restored.forEach(field => {
      const cb = dropdown.querySelector(`.tp-field-check[data-field="${field}"]`);
      if (cb) { cb.checked = true; updateCatCheckbox(cb.dataset.cat); }
    });
    syncFromCheckboxes();
  }
} catch (_) {}

btnTemplate.addEventListener('click', (e) => {
  e.stopPropagation();
  dropdown.classList.toggle('open');
});

document.addEventListener('click', () => {
  dropdown.classList.remove('open');
  fileDropdown.classList.remove('open');
  if (uploadTypeOverlay) { uploadTypeOverlay.remove(); uploadTypeOverlay = null; }
});

// Stop clicks inside panels from closing them
dropdown.addEventListener('click', (e) => e.stopPropagation());
fileDropdown.addEventListener('click', (e) => e.stopPropagation());

function buildTemplatePanel() {
  const totalFields = ALL_FIELDS.length;
  let html = `
    <div class="tp-header">
      <label class="tp-check-label">
        <input type="checkbox" id="tp-select-all" class="tp-checkbox">
        <span id="tp-count">全选 (0/${totalFields})</span>
      </label>
      <button class="tp-clear-btn" id="tp-clear">清空</button>
    </div>
    <div class="tp-body">`;

  for (const cat of FIELD_CATEGORIES) {
    html += `
      <div class="tp-category" data-cat="${cat.id}">
        <div class="tp-cat-header">
          <label class="tp-check-label">
            <input type="checkbox" class="tp-checkbox tp-cat-check" data-cat="${cat.id}">
            <span class="tp-cat-label">${cat.label}</span>
          </label>
          <span class="tp-cat-count">${cat.fields.length}</span>
          <span class="tp-chevron">›</span>
        </div>
        <div class="tp-cat-fields">
          ${cat.fields.map(f => `
            <label class="tp-field-label">
              <input type="checkbox" class="tp-checkbox tp-field-check" data-cat="${cat.id}" data-field="${f}">
              <span>${f}</span>
            </label>
          `).join('')}
        </div>
      </div>`;
  }

  html += '</div>';
  dropdown.innerHTML = html;

  // Wire events
  const selectAll = dropdown.querySelector('#tp-select-all');
  const clearBtn = dropdown.querySelector('#tp-clear');

  selectAll.addEventListener('change', () => {
    const checked = selectAll.checked;
    dropdown.querySelectorAll('.tp-field-check, .tp-cat-check').forEach(cb => { cb.checked = checked; });
    syncFromCheckboxes();
  });

  clearBtn.addEventListener('click', () => {
    dropdown.querySelectorAll('.tp-field-check, .tp-cat-check').forEach(cb => { cb.checked = false; });
    selectAll.checked = false;
    selectAll.indeterminate = false;
    syncFromCheckboxes();
  });

  // Category header click → toggle expand
  dropdown.querySelectorAll('.tp-cat-header').forEach(header => {
    header.addEventListener('click', (e) => {
      if (e.target.closest('.tp-check-label')) return; // don't toggle when clicking checkbox
      const cat = header.closest('.tp-category');
      cat.classList.toggle('expanded');
    });
  });

  // Category checkbox → toggle all fields in category
  dropdown.querySelectorAll('.tp-cat-check').forEach(cb => {
    cb.addEventListener('change', () => {
      const catId = cb.dataset.cat;
      const checked = cb.checked;
      dropdown.querySelectorAll(`.tp-field-check[data-cat="${catId}"]`).forEach(f => { f.checked = checked; });
      syncFromCheckboxes();
    });
  });

  // Individual field checkbox
  dropdown.querySelectorAll('.tp-field-check').forEach(cb => {
    cb.addEventListener('change', () => {
      updateCatCheckbox(cb.dataset.cat);
      syncFromCheckboxes();
    });
  });
}

function updateCatCheckbox(catId) {
  const fields = dropdown.querySelectorAll(`.tp-field-check[data-cat="${catId}"]`);
  const catCb = dropdown.querySelector(`.tp-cat-check[data-cat="${catId}"]`);
  const total = fields.length;
  const checked = [...fields].filter(f => f.checked).length;
  catCb.checked = checked === total;
  catCb.indeterminate = checked > 0 && checked < total;
}

function syncFromCheckboxes() {
  const checked = [...dropdown.querySelectorAll('.tp-field-check:checked')];
  selectedFields = checked.map(cb => cb.dataset.field);

  // Update select-all state
  const selectAll = dropdown.querySelector('#tp-select-all');
  const total = ALL_FIELDS.length;
  const count = selectedFields.length;
  selectAll.checked = count === total;
  selectAll.indeterminate = count > 0 && count < total;
  dropdown.querySelector('#tp-count').textContent = `全选 (${count}/${total})`;

  // 持久化到 localStorage（页面刷新后可恢复）
  try { localStorage.setItem('selectedFields', JSON.stringify(selectedFields)); } catch (_) {}

  renderChips();
}

function renderChips() {
  chipsEl.innerHTML = selectedFields
    .map((f, i) => `<span class="field-chip">${escapeHtml(f)} <span class="remove-chip" data-idx="${i}">✕</span></span>`)
    .join('');

  chipsEl.querySelectorAll('.remove-chip').forEach((btn) => {
    btn.addEventListener('click', () => {
      const field = selectedFields[parseInt(btn.dataset.idx)];
      selectedFields.splice(parseInt(btn.dataset.idx), 1);
      // Uncheck the corresponding checkbox in the panel
      const cb = dropdown.querySelector(`.tp-field-check[data-field="${field}"]`);
      if (cb) {
        cb.checked = false;
        updateCatCheckbox(cb.dataset.cat);
        const selectAll = dropdown.querySelector('#tp-select-all');
        const total = ALL_FIELDS.length;
        const count = selectedFields.length;
        selectAll.checked = count === total;
        selectAll.indeterminate = count > 0 && count < total;
        dropdown.querySelector('#tp-count').textContent = `全选 (${count}/${total})`;
      }
      renderChips();
    });
  });
}

// ── Public API ───────────────────────────────────────────────────────────

/**
 * Get selected fields, merging chip selections with comma-separated user input.
 */
export function getFields(extraText) {
  const extra = extraText
    .split(/[,，\n]/)
    .map((s) => s.trim())
    .filter(Boolean);
  // Deduplicate
  const set = new Set([...selectedFields, ...extra]);
  return [...set];
}

export function getFileId() {
  return currentFileId;
}

export function getFileName() {
  return currentFileName;
}

export function isReady() {
  if (currentMode === 'compliance') {
    return !!bidFileId;
  }
  return !!currentFileId;
}

/** 在信息提取流式输出中记录每个字段的提取值，并直接更新清单表格单元格。 */
export function setExtractionResult(key, value) {
  if (!key) return;
  extractionCache[key] = value || '';
  // 持久化到 localStorage（页面刷新后可恢复）
  try {
    const stored = JSON.parse(localStorage.getItem('extractionCache') || '{}');
    stored[key] = value || '';
    localStorage.setItem('extractionCache', JSON.stringify(stored));
  } catch (_) {}
  // 直接更新清单面板中对应行的描述单元格（如果面板已渲染）
  const tbody = document.getElementById('cl-tbody');
  if (!tbody) return;
  try {
    const row = tbody.querySelector(`tr[data-field="${CSS.escape(key)}"]`);
    if (row) {
      const cell = row.querySelector('.cl-desc');
      if (cell) cell.textContent = value || '';
    }
  } catch (_) {}
}

/** 开始新一轮信息提取前清空缓存。 */
export function clearExtractionCache() {
  extractionCache = {};
  try { localStorage.removeItem('extractionCache'); } catch (_) {}
}

export function getExtractionCache() { return { ...extractionCache }; }
export function getMode() { return currentMode; }
export function getTenderFileId() { return tenderFileId; }
export function getBidFileId() { return bidFileId; }
export function getComplianceStep() { return complianceStep; }
export function getChecklist() { return checklistData; }
export function setChecklist(data) { checklistData = data; complianceStep = 2; }
export function getTenderFileName() { return tenderFileName; }
export function getBidFileName() { return bidFileName; }

export function setMode(mode) {
  currentMode = mode;
  complianceStep = 1;
  checklistData = null;
  bidFileId = null; bidFileName = null;

  if (mode === 'compliance') {
    // 切换到合规模式时，将当前已选文件自动设为招标书
    tenderFileId = currentFileId || tenderFileId;
    tenderFileName = currentFileName || tenderFileName;
    currentFileId = null; currentFileName = null;
  } else {
    tenderFileId = null; tenderFileName = null;
    currentFileId = null; currentFileName = null;
  }

  renderAttachment();

  const panel = document.getElementById('checklist-panel');
  if (panel) {
    if (mode === 'compliance') {
      panel.classList.remove('hidden');
      renderChecklistPanel();
    } else {
      panel.classList.add('hidden');
    }
  }
}

// ── Compliance Checklist Panel ───────────────────────────────────────────

function getFieldCategory(fieldName) {
  for (const cat of FIELD_CATEGORIES) {
    if (cat.fields.includes(fieldName)) return cat.label;
  }
  return '自定义';
}

export function renderChecklistPanel() {
  const panel = document.getElementById('checklist-panel');
  if (!panel) return;

  if (selectedFields.length === 0) {
    panel.innerHTML = '<div class="cl-empty">请先在「信息提取」中选择要核查的字段</div>';
    return;
  }

  const hasCached = selectedFields.some(f => extractionCache[f]);
  const rows = selectedFields.map((field, i) => {
    const cat = getFieldCategory(field);
    const desc = extractionCache[field] || '';
    return `<tr data-field="${escapeAttr(field)}">
      <td class="cl-idx">${i + 1}</td>
      <td class="cl-type">${escapeHtml(cat)}</td>
      <td class="cl-key">${escapeHtml(field)}</td>
      <td class="cl-desc" contenteditable="true">${escapeHtml(desc)}</td>
    </tr>`;
  }).join('');

  panel.innerHTML = `
    <div class="cl-header">
      <span class="cl-title">审查清单</span>
      <span class="cl-count">${selectedFields.length} 项</span>
      <span class="cl-hint" id="cl-hint">${hasCached ? '已从信息提取结果填入，可手动编辑' : '可手动编辑，或选择招标书后自动填入'}</span>
    </div>
    <div class="cl-table-wrap">
      <table class="cl-table">
        <thead>
          <tr>
            <th class="cl-th-idx">序号</th>
            <th class="cl-th-type">信息类型</th>
            <th class="cl-th-key">招标字段</th>
            <th class="cl-th-desc">招标描述</th>
          </tr>
        </thead>
        <tbody id="cl-tbody">${rows}</tbody>
      </table>
    </div>`;
}

/**
 * 从 LLM 结果批量填入招标描述（不覆盖已有内容）。
 */
export function fillChecklistDescriptions(items) {
  const tbody = document.getElementById('cl-tbody');
  if (!tbody) return;
  items.forEach(item => {
    const key = item.key || '';
    const row = tbody.querySelector(`tr[data-field="${CSS.escape(key)}"]`);
    if (row) {
      const cell = row.querySelector('.cl-desc');
      if (cell && !cell.textContent.trim()) {
        cell.textContent = item.requirement || '';
      }
    }
  });
}

/**
 * 读取当前清单表格数据，返回合规核查所需格式。
 */
export function getComplianceChecklist() {
  const tbody = document.getElementById('cl-tbody');
  if (!tbody) return [];
  return [...tbody.querySelectorAll('tr')].map((row, i) => ({
    key: row.dataset.field || `字段${i + 1}`,
    requirement: row.querySelector('.cl-desc')?.textContent?.trim() || '',
    category: row.querySelector('.cl-type')?.textContent?.trim() || '',
  }));
}

/**
 * 合规模式：两段式下拉（招标书 + 投标书）。
 */
async function loadDualFileDropdown(container) {
  container.innerHTML = '<div class="file-dropdown-empty">加载中...</div>';
  try {
    const [tRes, bRes] = await Promise.all([
      fetch('/v1/files/list?type=tender').then(r => r.json()),
      fetch('/v1/files/list?type=bid').then(r => r.json()),
    ]);
    container.innerHTML = '';

    const renderSection = (label, colorClass, files, onSelect, onDelete) => {
      const header = document.createElement('div');
      header.className = `fd-section-header ${colorClass}`;
      header.textContent = label;
      container.appendChild(header);

      if (!files || files.length === 0) {
        const empty = document.createElement('div');
        empty.className = 'file-dropdown-empty fd-section-empty';
        empty.textContent = `暂无已上传${label}`;
        container.appendChild(empty);
        return;
      }
      for (const f of files) {
        const item = document.createElement('div');
        item.className = 'file-option';
        const badgeClass = f.has_vectors ? 'cached' : 'not-cached';
        const badgeText = f.has_vectors ? '已索引' : '未索引';
        item.innerHTML = `
          <span class="file-name">${escapeHtml(f.filename)}</span>
          <span class="file-badge ${badgeClass}">${badgeText}</span>
          <button class="file-delete-btn" title="删除文件" data-id="${f.id}">🗑</button>`;
        item.addEventListener('click', (e) => {
          if (e.target.closest('.file-delete-btn')) return;
          onSelect(f.id, f.filename);
          container.classList.remove('open');
        });
        item.querySelector('.file-delete-btn').addEventListener('click', async (e) => {
          e.stopPropagation();
          if (!confirm(`确认删除「${f.filename}」？`)) return;
          try {
            await deleteFile(f.id);
            onDelete(f.id);
            await loadDualFileDropdown(container);
          } catch (err) { alert(`删除失败: ${err.message}`); }
        });
        container.appendChild(item);
      }
    };

    renderSection('📋 招标书', 'fds-tender', tRes.files,
      (id, name) => { tenderFileId = id; tenderFileName = name; renderAttachment(); },
      (id) => { if (tenderFileId === id) { tenderFileId = null; tenderFileName = null; renderAttachment(); } }
    );
    renderSection('📄 投标书', 'fds-bid', bRes.files,
      (id, name) => { bidFileId = id; bidFileName = name; renderAttachment(); },
      (id) => { if (bidFileId === id) { bidFileId = null; bidFileName = null; renderAttachment(); } }
    );
  } catch {
    container.innerHTML = '<div class="file-dropdown-empty">加载失败</div>';
  }
}

function escapeHtml(str) {
  const d = document.createElement('div');
  d.textContent = str;
  return d.innerHTML;
}

function escapeAttr(str) {
  return str.replace(/&/g, '&amp;').replace(/"/g, '&quot;');
}
