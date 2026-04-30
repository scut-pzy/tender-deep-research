/**
 * settings.js — 设置面板逻辑
 *   - 显示设置（字体、密度、主题）：纯客户端，存 localStorage
 *   - 模型配置（API Key/Base/Model）：读写后端 /v1/config
 *   - 流程参数（RAG、迭代轮数）：写后端 /v1/config
 */

const DISPLAY_KEY = 'tdr_display_settings';

// ── 默认显示设置 ──────────────────────────────────────────────────────────────
const DISPLAY_DEFAULTS = {
  fontSize: 14,
  lineHeight: 1.65,
  fontFamily: 'system',
  density: 'comfortable',
  theme: 'dark',
};

// ── 字体映射 ──────────────────────────────────────────────────────────────────
const FONT_MAP = {
  system: 'system-ui, -apple-system, "Segoe UI", sans-serif',
  inter:  '"Inter", system-ui, sans-serif',
  noto:   '"Noto Sans SC", system-ui, sans-serif',
};

// ── 工具：Toast 通知 ─────────────────────────────────────────────────────────
function showToast(msg, type = 'success') {
  const el = document.createElement('div');
  el.className = `settings-toast ${type}`;
  el.textContent = (type === 'success' ? '✓ ' : '✕ ') + msg;
  document.body.appendChild(el);
  setTimeout(() => el.remove(), 3000);
}

// ── 应用显示设置到 CSS 变量 ───────────────────────────────────────────────────
export function applyDisplaySettings(s = {}) {
  const cfg = { ...DISPLAY_DEFAULTS, ...s };

  document.documentElement.style.setProperty('--font-size-base', cfg.fontSize + 'px');
  document.documentElement.style.setProperty('--font-family-base', FONT_MAP[cfg.fontFamily] || FONT_MAP.system);
  document.documentElement.style.setProperty('--line-height-base', cfg.lineHeight);
  document.body.dataset.density = cfg.density;

  // 主题
  if (cfg.theme === 'light') {
    document.documentElement.classList.remove('dark');
  } else {
    document.documentElement.classList.add('dark');
  }

  // highlight.js 主题切换
  const hljsLink = document.getElementById('hljs-theme');
  if (hljsLink) {
    hljsLink.href = cfg.theme === 'light'
      ? 'https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/styles/github.min.css'
      : 'https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/styles/github-dark.min.css';
  }
}

// ── 加载并应用已保存的显示设置 ───────────────────────────────────────────────
export function loadDisplaySettings() {
  try {
    const saved = JSON.parse(localStorage.getItem(DISPLAY_KEY) || '{}');
    applyDisplaySettings(saved);
    return { ...DISPLAY_DEFAULTS, ...saved };
  } catch {
    return { ...DISPLAY_DEFAULTS };
  }
}

// ── 将显示设置写入 localStorage ──────────────────────────────────────────────
function saveDisplaySettings(s) {
  localStorage.setItem(DISPLAY_KEY, JSON.stringify(s));
}

// ── 更新 Header 上的模型状态 Pill ─────────────────────────────────────────────
function updateStatusPills(cfg) {
  const pillPolicy = document.getElementById('pill-policy');
  const pillCritic = document.getElementById('pill-critic');
  if (pillPolicy) pillPolicy.textContent = cfg?.policy_llm?.model || '';
  if (pillCritic) pillCritic.textContent = cfg?.critic_vlm?.model || '';
}

// ── 从后端加载 config ──────────────────────────────────────────────────────
async function fetchConfig() {
  try {
    const resp = await fetch('/v1/config');
    if (!resp.ok) throw new Error('HTTP ' + resp.status);
    return await resp.json();
  } catch (e) {
    console.warn('fetchConfig failed:', e);
    return null;
  }
}

// ── 将 config 填入表单 ──────────────────────────────────────────────────────
function populateModelForm(cfg) {
  if (!cfg) return;

  const set = (id, val) => {
    const el = document.getElementById(id);
    if (el && val != null) el.value = val;
  };

  set('set-policy-base',  cfg.policy_llm?.api_base);
  set('set-policy-key',   cfg.policy_llm?.api_key);
  set('set-policy-model', cfg.policy_llm?.model);

  set('set-critic-base',  cfg.critic_vlm?.api_base);
  set('set-critic-key',   cfg.critic_vlm?.api_key);
  set('set-critic-model', cfg.critic_vlm?.model);

  set('set-embed-base',   cfg.embedding?.api_base);
  set('set-embed-key',    cfg.embedding?.api_key);
  set('set-embed-model',  cfg.embedding?.model);

  const statusBar = document.getElementById('model-load-status');
  if (statusBar) statusBar.textContent = '配置已加载';
}

function populatePipelineForm(cfg) {
  if (!cfg) return;

  const setSlider = (id, valId, val) => {
    const el = document.getElementById(id);
    const valEl = document.getElementById(valId);
    if (el && val != null) { el.value = val; }
    if (valEl && val != null) { valEl.textContent = val; }
  };

  const mode = cfg.rag?.mode || 'parent_child';
  const modeEl = document.getElementById('set-rag-mode');
  if (modeEl) modeEl.value = mode;

  setSlider('set-topk',      'set-topk-val',      cfg.rag?.top_k ?? 5);
  setSlider('set-maxiter',   'set-maxiter-val',    cfg.pipeline?.max_iterations ?? 3);
  setSlider('set-fieldtopk', 'set-fieldtopk-val', cfg.pipeline?.field_top_k ?? 5);
}

// ── 收集模型表单数据 ─────────────────────────────────────────────────────────
function collectModelForm() {
  const val = (id) => document.getElementById(id)?.value?.trim() || '';
  return {
    policy_llm: { api_base: val('set-policy-base'), api_key: val('set-policy-key'), model: val('set-policy-model') },
    critic_vlm: { api_base: val('set-critic-base'), api_key: val('set-critic-key'), model: val('set-critic-model') },
    embedding:  { api_base: val('set-embed-base'),  api_key: val('set-embed-key'),  model: val('set-embed-model') },
  };
}

function collectPipelineForm() {
  const num = (id) => parseFloat(document.getElementById(id)?.value);
  return {
    rag: {
      mode:  document.getElementById('set-rag-mode')?.value || 'parent_child',
      top_k: num('set-topk'),
    },
    pipeline: {
      max_iterations: num('set-maxiter'),
      field_top_k:    num('set-fieldtopk'),
    },
  };
}

// ── 保存到后端 ────────────────────────────────────────────────────────────────
async function patchConfig(updates) {
  const resp = await fetch('/v1/config', {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(updates),
  });
  if (!resp.ok) throw new Error('保存失败 HTTP ' + resp.status);
  return resp.json();
}

// ── 初始化设置面板 ────────────────────────────────────────────────────────────
export function initSettings() {
  const panel   = document.getElementById('settings-panel');
  const overlay = document.getElementById('settings-overlay');
  const openBtn = document.getElementById('settings-toggle');
  const closeBtn = document.getElementById('settings-close');

  if (!panel || !openBtn) return;

  // 当前显示设置
  let displayCfg = loadDisplaySettings();
  let serverCfg  = null;

  // ── 开关面板 ──────────────────────────────────────────────────────────────
  function openPanel() {
    panel.classList.remove('hidden');
    overlay?.classList.remove('hidden');
    syncDisplayFormFromCfg(displayCfg);

    // 加载服务端配置（延迟，避免阻塞动画）
    const statusBar = document.getElementById('model-load-status');
    if (statusBar) statusBar.textContent = '正在加载配置...';

    fetchConfig().then(cfg => {
      serverCfg = cfg;
      populateModelForm(cfg);
      populatePipelineForm(cfg);
      updateStatusPills(cfg);
    });

    lucide?.createIcons?.();
  }

  function closePanel() {
    panel.classList.add('hidden');
    overlay?.classList.add('hidden');
  }

  openBtn.addEventListener('click', openPanel);
  closeBtn?.addEventListener('click', closePanel);
  overlay?.addEventListener('click', closePanel);
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && !panel.classList.contains('hidden')) closePanel();
  });

  // ── Tab 切换 ────────────────────────────────────────────────────────────
  panel.querySelectorAll('.settings-tab').forEach(tab => {
    tab.addEventListener('click', () => {
      panel.querySelectorAll('.settings-tab').forEach(t => t.classList.remove('active'));
      panel.querySelectorAll('.settings-pane').forEach(p => p.classList.remove('active'));
      tab.classList.add('active');
      const pane = panel.querySelector(`.settings-pane[data-pane="${tab.dataset.tab}"]`);
      if (pane) pane.classList.add('active');
    });
  });

  // ── Eye buttons (show/hide password) ─────────────────────────────────────
  panel.querySelectorAll('.key-eye-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      const input = document.getElementById(btn.dataset.target);
      if (!input) return;
      input.type = input.type === 'password' ? 'text' : 'password';
      btn.querySelector('i')?.setAttribute('data-lucide', input.type === 'password' ? 'eye' : 'eye-off');
      lucide?.createIcons?.();
    });
  });

  // ── Density buttons ──────────────────────────────────────────────────────
  const densityGroup = document.getElementById('set-density');
  densityGroup?.querySelectorAll('.density-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      densityGroup.querySelectorAll('.density-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      displayCfg.density = btn.dataset.val;
    });
  });

  // ── Theme buttons ─────────────────────────────────────────────────────────
  const themeGroup = document.getElementById('set-theme');
  themeGroup?.querySelectorAll('.theme-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      themeGroup.querySelectorAll('.theme-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      displayCfg.theme = btn.dataset.val;
    });
  });

  // ── Slider live update ───────────────────────────────────────────────────
  [
    ['set-font-size',   'set-font-size-val',   v => v + 'px',   'fontSize'],
    ['set-line-height', 'set-line-height-val', v => parseFloat(v).toFixed(2), 'lineHeight'],
    ['set-topk',        'set-topk-val',        v => v,          null],
    ['set-maxiter',     'set-maxiter-val',      v => v,          null],
    ['set-fieldtopk',   'set-fieldtopk-val',   v => v,          null],
  ].forEach(([sliderId, valId, fmt, cfgKey]) => {
    const el = document.getElementById(sliderId);
    const valEl = document.getElementById(valId);
    if (!el || !valEl) return;
    el.addEventListener('input', () => {
      valEl.textContent = fmt(el.value);
      if (cfgKey) displayCfg[cfgKey] = parseFloat(el.value);
    });
  });

  // ── Font family select ──────────────────────────────────────────────────
  document.getElementById('set-font-family')?.addEventListener('change', (e) => {
    displayCfg.fontFamily = e.target.value;
  });

  // ── Display: Apply ───────────────────────────────────────────────────────
  document.getElementById('set-display-save')?.addEventListener('click', () => {
    applyDisplaySettings(displayCfg);
    saveDisplaySettings(displayCfg);
    showToast('显示设置已应用');
  });

  // ── Display: Reset ───────────────────────────────────────────────────────
  document.getElementById('set-display-reset')?.addEventListener('click', () => {
    displayCfg = { ...DISPLAY_DEFAULTS };
    applyDisplaySettings(displayCfg);
    saveDisplaySettings(displayCfg);
    syncDisplayFormFromCfg(displayCfg);
    showToast('已恢复默认设置');
  });

  // ── Model: Save ──────────────────────────────────────────────────────────
  document.getElementById('set-model-save')?.addEventListener('click', async () => {
    const btn = document.getElementById('set-model-save');
    btn.textContent = '保存中...';
    btn.disabled = true;
    try {
      await patchConfig(collectModelForm());
      // 重新拉取以更新 pills（key 脱敏后重填）
      const cfg = await fetchConfig();
      if (cfg) { serverCfg = cfg; populateModelForm(cfg); updateStatusPills(cfg); }
      showToast('模型配置已保存');
    } catch (e) {
      showToast(e.message, 'error');
    } finally {
      btn.textContent = '保存';
      btn.disabled = false;
    }
  });

  // ── Pipeline: Save ───────────────────────────────────────────────────────
  document.getElementById('set-pipeline-save')?.addEventListener('click', async () => {
    const btn = document.getElementById('set-pipeline-save');
    btn.textContent = '保存中...';
    btn.disabled = true;
    try {
      await patchConfig(collectPipelineForm());
      showToast('流程参数已保存');
    } catch (e) {
      showToast(e.message, 'error');
    } finally {
      btn.textContent = '保存';
      btn.disabled = false;
    }
  });

  // 初始加载：更新 status pills
  fetchConfig().then(cfg => {
    if (cfg) updateStatusPills(cfg);
  });
}

// ── 将 displayCfg 对象反填到表单 ──────────────────────────────────────────────
function syncDisplayFormFromCfg(cfg) {
  const el = (id) => document.getElementById(id);

  const fs = el('set-font-size');
  if (fs) { fs.value = cfg.fontSize ?? 14; el('set-font-size-val').textContent = (cfg.fontSize ?? 14) + 'px'; }

  const lh = el('set-line-height');
  if (lh) { lh.value = cfg.lineHeight ?? 1.65; el('set-line-height-val').textContent = parseFloat(cfg.lineHeight ?? 1.65).toFixed(2); }

  const ff = el('set-font-family');
  if (ff) ff.value = cfg.fontFamily ?? 'system';

  // Density buttons
  document.querySelectorAll('#set-density .density-btn').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.val === (cfg.density ?? 'comfortable'));
  });

  // Theme buttons
  document.querySelectorAll('#set-theme .theme-btn').forEach(btn => {
    const isDark = document.documentElement.classList.contains('dark');
    const currentTheme = isDark ? 'dark' : 'light';
    btn.classList.toggle('active', btn.dataset.val === currentTheme);
  });
}
