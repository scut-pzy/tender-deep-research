/**
 * app.js — Entry point: wire up events and orchestrate streaming
 * with structured event routing from stream-parser.
 */

import { streamChat, streamComplianceCheck, streamChatQA, streamComplianceReeval, abortCurrentStream } from './api.js';
import { StreamParser, TARGET } from './stream-parser.js';
import { addUserMessage, createAiMessage, addErrorMessage, createChecklistMessage, createComplianceMessage, addChatUserMessage, createChatAiMessage, applyComplianceUpdate } from './chat.js';
import {
  resetSidebar,
  appendPrepStep,
  startRound,
  startField,
  updateFieldStatus,
  appendPolicy,
  appendCritic,
  appendRewriteFeedback,
} from './sidebar.js';
import {
  getFields, getFileId, getFileName, isReady, getMode,
  getBidFileId,
  setMode, getBidFileName,
  getComplianceChecklist,
  setExtractionResult, clearExtractionCache, renderChecklistPanel,
  getExtractionCache,
} from './upload.js';
import { initTheme, setupThemeToggle } from './theme.js';
import { setupHistory } from './history.js';

// Initialize theme before paint
initTheme();
setupThemeToggle();
setupHistory();

const userInput = document.getElementById('user-input');
const btnSend = document.getElementById('btn-send');

let isStreaming = false;
let lastComplianceItems = null; // 保存最近一次合规报告，切 tab 后可恢复
let focusedField = null; // { idx, item } — 对话重核时聚焦的合规字段
let inputIntent = 'action'; // 'action' | 'chat' — 输入框左侧分段控件状态

// ── Stop / Send button state ─────────────────────────────────────────────
const SEND_ICON = '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/></svg>';
const STOP_ICON = '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="currentColor" stroke="none"><rect x="4" y="4" width="16" height="16" rx="2"/></svg>';

function setStreamingUI(streaming) {
  isStreaming = streaming;
  if (streaming) {
    btnSend.disabled = false;
    btnSend.innerHTML = STOP_ICON;
    btnSend.classList.add('btn-stop');
    btnSend.title = '停止';
  } else {
    btnSend.innerHTML = SEND_ICON;
    btnSend.classList.remove('btn-stop');
    btnSend.title = '发送';
    btnSend.disabled = false;
  }
}

function handleStop() {
  abortCurrentStream();
}

// ── Compliance field focus (for "对话重核" flow) ─────────────────────────
const focusChipHost = document.createElement('div');
focusChipHost.id = 'cr-focus-chip-host';
// Insert above the input row (sibling of the flex row, not inside it)
const _inputRow = userInput.parentElement;
_inputRow?.parentElement?.insertBefore(focusChipHost, _inputRow);

function setFocusedField(payload) {
  focusedField = payload;
  renderFocusChip();
  updateSendContext();
  if (payload) {
    userInput.focus();
  }
}

function clearFocusedField() {
  setFocusedField(null);
}

function renderFocusChip() {
  if (!focusedField) {
    focusChipHost.innerHTML = '';
    return;
  }
  const key = focusedField.item?.key || '未知字段';
  focusChipHost.innerHTML = `
    <div class="cr-focus-chip">
      💬 对话重核：<strong>${escapeHtmlText(key)}</strong>
      <span class="cr-chip-close" title="取消">×</span>
    </div>`;
  focusChipHost.querySelector('.cr-chip-close').addEventListener('click', clearFocusedField);
}

function escapeHtmlText(s) {
  return String(s).replace(/[&<>"']/g, c => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  })[c]);
}

// 合规卡片触发的 focus 事件
document.addEventListener('compliance:focus-field', (e) => {
  if (isStreaming) return;
  setFocusedField(e.detail);
});

// Set initial placeholder to match current mode
updateSendContext();

// ── Mode tabs ────────────────────────────────────────────────────────────
const modeTabs = document.getElementById('mode-tabs');
if (modeTabs) {
  modeTabs.addEventListener('click', (e) => {
    const tab = e.target.closest('.mode-tab');
    if (!tab || isStreaming) return;
    const mode = tab.dataset.mode;
    modeTabs.querySelectorAll('.mode-tab').forEach(t => t.classList.remove('active'));
    tab.classList.add('active');
    setMode(mode);
    clearFocusedField(); // 切 tab 清掉对话重核聚焦
    updateSendContext();
    // 切回合规 tab 时恢复已有报告（setMode 会重置 panel 为清单）
    if (mode === 'compliance' && lastComplianceItems) {
      createComplianceMessage(lastComplianceItems);
    }
  });
}

// ── Input intent (action / chat) ──────────────────────────────────────────
const intentBar = document.getElementById('input-intent');
const chipsContainer = document.getElementById('field-chips');

function applyIntentUI() {
  if (intentBar) {
    intentBar.querySelectorAll('.intent-btn').forEach(b => {
      b.classList.toggle('active', b.dataset.intent === inputIntent);
    });
  }
  if (chipsContainer) {
    chipsContainer.classList.toggle('chips-dimmed', inputIntent === 'chat');
  }
}

if (intentBar) {
  intentBar.addEventListener('click', (e) => {
    const btn = e.target.closest('.intent-btn');
    if (!btn || isStreaming) return;
    const next = btn.dataset.intent;
    if (next === inputIntent) return;
    inputIntent = next;
    applyIntentUI();
    updateSendContext();
  });
}
applyIntentUI();

function updateSendContext() {
  const mode = getMode();
  if (focusedField) {
    userInput.placeholder = `输入补充信息以重新核查「${focusedField.item?.key || ''}」...`;
    return;
  }
  if (inputIntent === 'chat') {
    userInput.placeholder = mode === 'compliance'
      ? '提问或让 AI 直接修改合规报告（例："招标方式其实是邀请招标，请改成合规"）...'
      : '自由提问，AI 基于文档回答...';
    return;
  }
  userInput.placeholder = mode === 'compliance'
    ? '选择投标书后发送，开始合规核查...'
    : '输入额外字段（逗号分隔）后发送，开始字段提取...';
}

// ── Send handler ─────────────────────────────────────────────────────────
btnSend.addEventListener('click', () => {
  if (isStreaming) {
    handleStop();
  } else {
    handleSend();
  }
});
userInput.addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    if (!isStreaming) handleSend();
  }
});

async function handleSend() {
  if (isStreaming) return;

  // ── 聚焦字段：优先走对话重核流程 ──
  if (focusedField && userInput.value.trim()) {
    await handleReevalSend();
    return;
  }

  // ── 对话模式：只要有输入就走 QA（chip 被灰掉，getFields 不参与）──
  if (inputIntent === 'chat') {
    if (!userInput.value.trim()) {
      addErrorMessage('请输入问题');
      return;
    }
    await handleChatSend();
    return;
  }

  // ── 动作模式 ──
  const mode = getMode();
  if (mode === 'compliance') {
    await handleComplianceSend();
    return;
  }

  if (!isReady()) {
    addErrorMessage('请先上传 PDF 文件');
    return;
  }

  const fields = getFields(userInput.value);

  if (fields.length === 0) {
    addErrorMessage('请选择或输入至少一个要提取的字段（或切到「对话」模式自由提问）');
    return;
  }

  const fileId = getFileId();
  const fileName = getFileName();

  // Show user message
  addUserMessage(fileName, fields);
  userInput.value = '';
  clearExtractionCache();

  // Prepare AI message and sidebar
  const aiMsg = createAiMessage();
  resetSidebar();

  setStreamingUI(true);

  const parser = new StreamParser();
  let inSummary = false;

  try {
    for await (const chunk of streamChat(fileId, fields)) {
      const events = parser.parse(chunk);
      for (const evt of events) {
        routeEvent(evt, aiMsg, inSummary);
        if (evt.event === 'summary_start') {
          inSummary = true;
        }
      }
    }

    const remaining = parser.flush();
    for (const evt of remaining) {
      routeEvent(evt, aiMsg, inSummary);
      if (evt.event === 'summary_start') {
        inSummary = true;
      }
    }

    aiMsg.finish();
    // Push final rendered table values into cache (overwrite mid-stream partials)
    const finalValues = aiMsg.getFieldValues();
    for (const [key, value] of Object.entries(finalValues)) {
      setExtractionResult(key, value);
    }
    // If compliance panel is already visible, refresh descriptions immediately
    const panel = document.getElementById('checklist-panel');
    if (panel && !panel.classList.contains('hidden')) {
      renderChecklistPanel();
    }
  } catch (err) {
    aiMsg.finish();
    if (err.name === 'AbortError') {
      aiMsg.appendStatus('⏹ 已停止');
    } else {
      addErrorMessage(err.message);
    }
  } finally {
    setStreamingUI(false);
  }
}

// ── Compliance mode handler ─────────────────────────────────────────────
async function handleComplianceSend() {
  const bidFileId = getBidFileId();
  if (!bidFileId) {
    addErrorMessage('请先选择投标书');
    return;
  }

  const checklist = getComplianceChecklist();
  if (!checklist || checklist.length === 0) {
    addErrorMessage('审查清单为空，请先在信息提取中选择字段');
    return;
  }

  const hasDesc = checklist.some(item => item.requirement.trim());
  if (!hasDesc) {
    addErrorMessage('审查清单缺少招标描述，请先完成招标书的信息提取，或手动填写招标描述');
    return;
  }

  // Run compliance check
  lastComplianceItems = null; // 清空旧报告，新结果进来后再写入
  addUserMessage(getBidFileName(), ['合规核查']);
  const aiMsg = createAiMessage();
  resetSidebar();

  setStreamingUI(true);

  const parser = new StreamParser();
  let inSummary = false;
  let fullText = '';

  try {
    for await (const chunk of streamComplianceCheck(bidFileId, checklist)) {
      fullText += chunk;
      const events = parser.parse(chunk);
      for (const evt of events) {
        routeEvent(evt, aiMsg, inSummary);
        if (evt.event === 'summary_start') inSummary = true;
      }
    }

    const remaining = parser.flush();
    for (const evt of remaining) {
      routeEvent(evt, aiMsg, inSummary);
      if (evt.event === 'summary_start') inSummary = true;
    }

    // Parse compliance report from JSON code block
    const jsonMatch = fullText.match(/```json\s*\n([\s\S]*?)\n```/);
    if (jsonMatch) {
      const parsed = JSON.parse(jsonMatch[1]);
      const items = Array.isArray(parsed) ? parsed : (parsed.items || []);
      lastComplianceItems = items; // 保存供 tab 切换恢复
      createComplianceMessage(items);
    }
    aiMsg.finish();
  } catch (err) {
    aiMsg.finish();
    if (err.name === 'AbortError') {
      aiMsg.appendStatus('⏹ 已停止');
    } else {
      addErrorMessage(err.message);
    }
  } finally {
    setStreamingUI(false);
  }
}

// ── Chat mode handler ────────────────────────────────────────────────────
async function handleChatSend() {
  const question = userInput.value.trim();
  if (!question) return;

  const mode = getMode();
  let fileId, contextData;
  const isComplianceChat = mode === 'compliance';

  if (isComplianceChat) {
    fileId = getBidFileId();
    contextData = lastComplianceItems;
    if (!fileId) {
      addErrorMessage('请先选择投标书');
      return;
    }
  } else {
    fileId = getFileId();
    contextData = getExtractionCache();
    if (!fileId) {
      addErrorMessage('请先上传 PDF 文件');
      return;
    }
  }

  addChatUserMessage(question);
  userInput.value = '';

  const aiMsg = createChatAiMessage();
  setStreamingUI(true);

  let fullText = '';
  try {
    for await (const chunk of streamChatQA(fileId, question, contextData)) {
      fullText += chunk;
      aiMsg.append(chunk);
    }
    aiMsg.finish();

    // 合规对话：解析尾部 ```json``` 块并应用 patch
    if (isComplianceChat && Array.isArray(lastComplianceItems)) {
      const m = fullText.match(/```json\s*\n([\s\S]*?)\n```/);
      if (m) {
        try {
          const parsed = JSON.parse(m[1]);
          const updates = Array.isArray(parsed?.updates) ? parsed.updates : [];
          let applied = 0;
          for (const upd of updates) {
            if (!upd || !upd.key) continue;
            const idx = lastComplianceItems.findIndex(it => it.key === upd.key);
            if (idx < 0) continue;
            const patch = {};
            if (upd.verdict) patch.verdict = upd.verdict;
            if (upd.reason) patch.reason = upd.reason;
            if (upd.response) patch.response = upd.response;
            if (Object.keys(patch).length > 0) {
              applyComplianceUpdate(lastComplianceItems, idx, patch);
              applied++;
            }
          }
          if (applied > 0) {
            aiMsg.appendStatus(`✅ 已自动更新 ${applied} 条合规条目（查看右侧报告）`);
          }
        } catch (e) {
          console.warn('解析合规对话 updates JSON 失败', e);
        }
      }
    }
  } catch (err) {
    aiMsg.finish();
    if (err.name === 'AbortError') {
      aiMsg.appendStatus('⏹ 已停止');
    } else {
      addErrorMessage(err?.message ?? String(err));
    }
  } finally {
    setStreamingUI(false);
  }
}

// ── Reevaluate single compliance field with user's extra context ──────
async function handleReevalSend() {
  const additionalContext = userInput.value.trim();
  if (!additionalContext || !focusedField) return;

  const bidFileId = getBidFileId();
  if (!bidFileId) {
    addErrorMessage('请先选择投标书');
    return;
  }
  if (!lastComplianceItems) {
    addErrorMessage('没有可重核的合规报告');
    return;
  }

  const { idx, item } = focusedField;
  addChatUserMessage(`🔁 重核「${item.key}」：${additionalContext}`);
  userInput.value = '';

  const aiMsg = createChatAiMessage();
  setStreamingUI(true);

  let fullText = '';
  try {
    const payload = {
      file_id: bidFileId,
      field_key: item.key,
      requirement: item.requirement || '',
      current_response: item.response || '',
      current_verdict: item.verdict || 'warn',
      current_reason: item.reason || '',
      additional_context: additionalContext,
    };
    for await (const chunk of streamComplianceReeval(payload)) {
      fullText += chunk;
      aiMsg.append(chunk);
    }
    aiMsg.finish();

    // Parse final JSON code block → apply patch
    const jsonMatch = fullText.match(/```json\s*\n([\s\S]*?)\n```/);
    if (jsonMatch) {
      try {
        const parsed = JSON.parse(jsonMatch[1]);
        const patch = {
          verdict: parsed.verdict || item.verdict,
          reason: parsed.reason || item.reason,
        };
        if (parsed.response) patch.response = parsed.response;
        applyComplianceUpdate(lastComplianceItems, idx, patch);
      } catch (e) {
        console.warn('解析重核 JSON 失败', e);
      }
    }
    clearFocusedField();
  } catch (err) {
    aiMsg.finish();
    if (err.name === 'AbortError') {
      aiMsg.appendStatus('⏹ 已停止');
    } else {
      addErrorMessage(err.message);
    }
  } finally {
    setStreamingUI(false);
  }
}

/**
 * Route a single parsed event to the appropriate UI component.
 */
export function routeEvent(evt, aiMsg, inSummary) {
  const { target, text, event, data } = evt;

  // ── Structured events ──────────────────────────────────────────────
  if (event === 'embed_progress' && data) {
    aiMsg.updateProgress(data.done, data.total, data.pct);
    appendPrepStep(text);
    return;
  }

  if (event === 'field_start' && data) {
    aiMsg.removeProgress();
    startField(data.key, data.index, data.total);
    aiMsg.appendStatus(text);
    return;
  }

  if (event === 'field_done' && data) {
    aiMsg.ensureField(data.key, data.verified);
    updateFieldStatus(data.key, data.verified);
    aiMsg.appendStatus(text);
    return;
  }

  if (event === 'round_start' && data) {
    startRound(data.roundNum);
    aiMsg.appendStatus(text);
    return;
  }

  if (event === 'field_result' && data) {
    aiMsg.appendField(data.key, data.value, data.page, data.confidence);
    setExtractionResult(data.key, data.value);
    appendPolicy(text);
    return;
  }

  if (event === 'critic_result' && data) {
    aiMsg.updateVerification(data.key, data.verified);
    appendCritic(text);
    return;
  }

  if (event === 'rewrite_feedback') {
    appendRewriteFeedback(text);
    return;
  }

  if (event === 'summary_start') {
    aiMsg.appendSummary(text);
    return;
  }

  // ── Summary mode: everything goes to summary ──────────────────────
  if (inSummary) {
    aiMsg.appendSummary(text);
    return;
  }

  // ── Route by target ───────────────────────────────────────────────
  switch (target) {
    case TARGET.PREP:
      appendPrepStep(text);
      break;
    case TARGET.POLICY:
      appendPolicy(text);
      break;
    case TARGET.CRITIC:
      appendCritic(text);
      break;
    case TARGET.REWRITE:
      appendRewriteFeedback(text);
      break;
    case TARGET.MAIN:
      aiMsg.appendStatus(text);
      break;
  }
}
