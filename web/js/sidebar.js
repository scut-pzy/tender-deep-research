/**
 * sidebar.js — Cumulative sidebar with collapsible rounds.
 * Rounds are NEVER cleared — old rounds auto-collapse, new ones expand.
 * Policy/Critic/Rewrite blocks have distinct labels, colors, and structured content.
 */

const sidebar = document.getElementById('sidebar');
const sidebarScroll = document.getElementById('sidebar-scroll');
const prepSection = document.getElementById('prep-section');
const prepContent = document.getElementById('prep-content');
const toggleBtn = document.getElementById('sidebar-toggle');
const btnToggleProcess = document.getElementById('btn-toggle-process');
const toggleProcessLabel = document.getElementById('toggle-process-label');

let sidebarVisible = true;
let processVisible = true;
let currentRoundNum = 0;
let currentRoundEl = null;
let currentFieldEl = null;

// Regex for parsing structured lines
const RE_FIELD_LINE = /「([^」]+)」=\s*\*\*([^*]+)\*\*.*?第(\d+)页.*?置信度[：:](\d+)%/;
const RE_STEP_TITLE = /^\s*(📝|👁️)\s*\*\*(.+)\*\*/;
const RE_REWRITE_FIELD = /「([^」]+)」[：:]\s*Critic\s*发现应为\s*\*\*([^*]+)\*\*/;

// SVG icons (inline, no Lucide dependency for dynamic elements)
const ICON_POLICY = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/></svg>`;
const ICON_CRITIC = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>`;
const ICON_REWRITE = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 9v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>`;
const ICON_ROUND = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>`;
const ICON_CHEVRON = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="6 9 12 15 18 9"/></svg>`;

// ── Sidebar toggle ─────────────────────────────────────────────────────
toggleBtn.addEventListener('click', () => {
  sidebarVisible = !sidebarVisible;
  sidebar.classList.toggle('collapsed', !sidebarVisible);
});

// ── Process visibility toggle ──────────────────────────────────────────
btnToggleProcess.addEventListener('click', () => {
  processVisible = !processVisible;
  sidebarScroll.classList.toggle('process-hidden', !processVisible);
  toggleProcessLabel.textContent = processVisible ? '隐藏' : '显示';
  const icon = btnToggleProcess.querySelector('[data-lucide]');
  if (icon) {
    icon.setAttribute('data-lucide', processVisible ? 'eye' : 'eye-off');
    if (window.lucide) lucide.createIcons({ nodes: [btnToggleProcess] });
  }
});

// ── Public API ─────────────────────────────────────────────────────────

/**
 * Reset sidebar for a new analysis (full clear).
 */
export function resetSidebar() {
  sidebarScroll.querySelectorAll('.round-section').forEach(el => el.remove());
  sidebarScroll.querySelectorAll('.field-section').forEach(el => el.remove());
  sidebarScroll.querySelectorAll('.sidebar-placeholder').forEach(el => el.remove());
  prepContent.innerHTML = '';
  prepSection.classList.add('hidden');
  currentRoundNum = 0;
  currentRoundEl = null;
  currentFieldEl = null;
}

/**
 * Append content to the prep section (Steps 1-3).
 */
export function appendPrepStep(text) {
  prepSection.classList.remove('hidden');
  appendDetailLine(prepContent, text);
  scrollSidebar();
}

/**
 * Start a new round. Old rounds auto-collapse.
 */
export function startRound(n) {
  // Collapse previous round
  if (currentRoundEl) {
    const body = currentRoundEl.querySelector('.round-body');
    const chevron = currentRoundEl.querySelector('.round-chevron');
    if (body) body.classList.add('collapsed');
    if (chevron) chevron.classList.add('collapsed');
    // Update previous round badge
    updateRoundBadge(currentRoundEl);
  }

  currentRoundNum = n;

  const section = document.createElement('div');
  section.className = 'round-section';
  section.dataset.round = n;

  section.innerHTML = `
    <div class="round-header">
      <span class="round-title">
        <span class="w-4 h-4 inline-block">${ICON_ROUND}</span>
        第 ${n} 轮
        <span class="round-badge in-progress">进行中</span>
      </span>
      <span class="round-chevron w-4 h-4 inline-block">${ICON_CHEVRON}</span>
    </div>
    <div class="round-body">
      <div class="policy-block">
        <div class="block-label policy-label">
          <span>${ICON_POLICY}</span> Policy 提取
        </div>
      </div>
      <div class="critic-block">
        <div class="block-label critic-label">
          <span>${ICON_CRITIC}</span> Critic 核验
        </div>
      </div>
    </div>
  `;

  // Toggle collapse on header click
  const header = section.querySelector('.round-header');
  const body = section.querySelector('.round-body');
  const chevron = section.querySelector('.round-chevron');
  header.addEventListener('click', () => {
    body.classList.toggle('collapsed');
    chevron.classList.toggle('collapsed');
  });

  sidebarScroll.appendChild(section);
  currentRoundEl = section;
  scrollSidebar();
}

/**
 * Start a new field section (per-field iteration mode).
 */
export function startField(key, index, total) {
  // Collapse previous field
  if (currentFieldEl) {
    const body = currentFieldEl.querySelector('.round-body');
    const chevron = currentFieldEl.querySelector('.round-chevron');
    if (body) body.classList.add('collapsed');
    if (chevron) chevron.classList.add('collapsed');
  }

  const section = document.createElement('div');
  section.className = 'field-section round-section';
  section.dataset.fieldKey = key;

  section.innerHTML = `
    <div class="round-header">
      <span class="round-title">
        <span class="w-4 h-4 inline-block">${ICON_ROUND}</span>
        「${escapeHtml(key)}」(${index}/${total})
        <span class="round-badge in-progress">进行中</span>
      </span>
      <span class="round-chevron w-4 h-4 inline-block">${ICON_CHEVRON}</span>
    </div>
    <div class="round-body">
      <div class="policy-block">
        <div class="block-label policy-label">
          <span>${ICON_POLICY}</span> Policy 提取
        </div>
      </div>
      <div class="critic-block">
        <div class="block-label critic-label">
          <span>${ICON_CRITIC}</span> Critic 核验
        </div>
      </div>
    </div>
  `;

  // Toggle collapse on header click
  const header = section.querySelector('.round-header');
  const body = section.querySelector('.round-body');
  const chevron = section.querySelector('.round-chevron');
  header.addEventListener('click', () => {
    body.classList.toggle('collapsed');
    chevron.classList.toggle('collapsed');
  });

  sidebarScroll.appendChild(section);
  currentFieldEl = section;
  // Also set currentRoundEl so appendPolicy/appendCritic work
  currentRoundEl = section;
  scrollSidebar();
}

/**
 * Update field section badge status when done.
 */
export function updateFieldStatus(key, verified) {
  // Find the field section
  const sections = sidebarScroll.querySelectorAll('.field-section');
  for (const section of sections) {
    if (section.dataset.fieldKey === key) {
      const badge = section.querySelector('.round-badge');
      if (badge) {
        badge.className = `round-badge ${verified ? 'all-pass' : 'has-fail'}`;
        badge.textContent = verified ? '已通过' : '未通过';
      }
      break;
    }
  }
}

/**
 * Append text to current round's Policy block.
 * Parses field lines into structured cards; step titles get special rendering.
 */
export function appendPolicy(text) {
  if (!currentRoundEl) return;
  const block = currentRoundEl.querySelector('.policy-block');
  if (!block) return;

  const trimmed = text.trim();
  if (!trimmed) return;

  // Try to parse as a field result line
  const fieldMatch = trimmed.match(RE_FIELD_LINE);
  if (fieldMatch) {
    const [, key, value, page, conf] = fieldMatch;
    const confNum = parseInt(conf);
    const confClass = confNum >= 80 ? 'high' : confNum >= 50 ? 'medium' : 'low';

    const item = document.createElement('div');
    item.className = 'policy-field-item';
    item.innerHTML = `
      <span class="field-name">${escapeHtml(key)}</span>
      <span class="field-value" title="${escapeHtml(value)}">${escapeHtml(value)}</span>
      <span class="field-meta">
        <span class="field-page">p.${page}</span>
        <span class="field-conf ${confClass}">${conf}%</span>
      </span>
    `;
    block.appendChild(item);
    scrollSidebar();
    return;
  }

  // Step title line
  const stepMatch = trimmed.match(RE_STEP_TITLE);
  if (stepMatch) {
    const title = document.createElement('div');
    title.className = 'step-title';
    title.textContent = stepMatch[2].replace(/\*\*/g, '');
    block.appendChild(title);
    scrollSidebar();
    return;
  }

  // Plain text fallback
  appendDetailLine(block, text);
  scrollSidebar();
}

/**
 * Append text to current round's Critic block.
 * ✅/❌ lines become colored badge rows.
 */
export function appendCritic(text) {
  if (!currentRoundEl) return;
  const block = currentRoundEl.querySelector('.critic-block');
  if (!block) return;

  const trimmed = text.trim();
  if (!trimmed) return;

  // ✅ or ❌ verification result
  if (trimmed.startsWith('✅') || trimmed.startsWith('❌')) {
    const isPass = trimmed.startsWith('✅');
    const rest = trimmed.slice(2).trim();

    // Try to extract key and detail
    const keyMatch = rest.match(/「([^」]+)」[：:]\s*(.*)/);

    const item = document.createElement('div');
    item.className = `critic-item ${isPass ? 'pass-row' : 'fail-row'}`;

    if (keyMatch) {
      item.innerHTML = `
        <span class="critic-icon ${isPass ? 'pass' : 'fail'}">${isPass ? '✅' : '❌'}</span>
        <span class="critic-key">${escapeHtml(keyMatch[1])}</span>
        <span class="critic-detail">${escapeHtml(keyMatch[2])}</span>
      `;
    } else {
      item.innerHTML = `
        <span class="critic-icon ${isPass ? 'pass' : 'fail'}">${isPass ? '✅' : '❌'}</span>
        <span class="critic-detail">${escapeHtml(rest)}</span>
      `;
    }
    block.appendChild(item);
    scrollSidebar();
    return;
  }

  // Step title line
  const stepMatch = trimmed.match(RE_STEP_TITLE);
  if (stepMatch) {
    const title = document.createElement('div');
    title.className = 'step-title';
    title.textContent = stepMatch[2].replace(/\*\*/g, '');
    block.appendChild(title);
    scrollSidebar();
    return;
  }

  // Plain text fallback
  appendDetailLine(block, text);
  scrollSidebar();
}

/**
 * Append Critic→Policy rewrite feedback (amber card).
 * Parses structured feedback lines into highlight items.
 */
export function appendRewriteFeedback(text) {
  if (!currentRoundEl) return;
  const body = currentRoundEl.querySelector('.round-body');
  if (!body) return;

  let rewriteBlock = body.querySelector('.rewrite-block');
  if (!rewriteBlock) {
    rewriteBlock = document.createElement('div');
    rewriteBlock.className = 'rewrite-block';
    rewriteBlock.innerHTML = `
      <div class="rewrite-header">
        <span>${ICON_REWRITE}</span>
        Critic → Policy 反馈
      </div>
    `;
    body.appendChild(rewriteBlock);
  }

  const trimmed = text.trim();
  if (!trimmed) return;

  // Try to parse structured rewrite field
  const rwMatch = trimmed.match(RE_REWRITE_FIELD);
  if (rwMatch) {
    const item = document.createElement('div');
    item.className = 'rewrite-field-item';
    item.innerHTML = `<span class="rw-key">${escapeHtml(rwMatch[1])}</span>: 应为 <span class="rw-value">${escapeHtml(rwMatch[2])}</span>`;
    rewriteBlock.appendChild(item);
    scrollSidebar();
    return;
  }

  // Plain text fallback
  appendDetailLine(rewriteBlock, text);
  scrollSidebar();
}

/**
 * Update round badge status based on critic results.
 */
export function updateRoundStatus(allPass) {
  if (!currentRoundEl) return;
  const badge = currentRoundEl.querySelector('.round-badge');
  if (!badge) return;
  badge.className = `round-badge ${allPass ? 'all-pass' : 'has-fail'}`;
  badge.textContent = allPass ? '全部通过' : '有未通过';
}

/**
 * Toggle process visibility.
 */
export function toggleProcessVisible() {
  btnToggleProcess.click();
}

// ── Helpers ────────────────────────────────────────────────────────────

function updateRoundBadge(roundEl) {
  const badge = roundEl.querySelector('.round-badge');
  if (!badge) return;
  const hasFailRows = roundEl.querySelectorAll('.critic-item.fail-row').length > 0;
  const hasCriticItems = roundEl.querySelectorAll('.critic-item').length > 0;
  if (hasCriticItems) {
    badge.className = `round-badge ${hasFailRows ? 'has-fail' : 'all-pass'}`;
    badge.textContent = hasFailRows ? '有未通过' : '全部通过';
  }
}

function appendDetailLine(container, text) {
  const line = document.createElement('div');
  line.className = 'detail-line';
  line.textContent = text.replace(/\n$/, '');
  container.appendChild(line);
}

function scrollSidebar() {
  sidebarScroll.scrollTo({ top: sidebarScroll.scrollHeight, behavior: 'smooth' });
}

function escapeHtml(str) {
  const d = document.createElement('div');
  d.textContent = str;
  return d.innerHTML;
}
