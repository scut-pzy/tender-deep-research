/**
 * app.js — Entry point: wire up events and orchestrate streaming
 * with structured event routing from stream-parser.
 */

import { streamChat, streamComplianceCheck } from './api.js';
import { StreamParser, TARGET } from './stream-parser.js';
import { addUserMessage, createAiMessage, addErrorMessage, createChecklistMessage, createComplianceMessage } from './chat.js';
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
    updateSendContext();
    // 切回合规 tab 时恢复已有报告（setMode 会重置 panel 为清单）
    if (mode === 'compliance' && lastComplianceItems) {
      createComplianceMessage(lastComplianceItems);
    }
  });
}

function updateSendContext() {
  const mode = getMode();
  userInput.placeholder = mode === 'compliance'
    ? '选择投标书后发送，开始合规核查...'
    : '输入额外字段（逗号分隔）或自由提问...';
}

// ── Send handler ─────────────────────────────────────────────────────────
btnSend.addEventListener('click', handleSend);
userInput.addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    handleSend();
  }
});

async function handleSend() {
  if (isStreaming) return;

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
    addErrorMessage('请选择或输入至少一个要提取的字段');
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

  isStreaming = true;
  btnSend.disabled = true;

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
    addErrorMessage(err.message);
  } finally {
    isStreaming = false;
    btnSend.disabled = false;
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

  isStreaming = true;
  btnSend.disabled = true;

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
    addErrorMessage(err.message);
  } finally {
    isStreaming = false;
    btnSend.disabled = false;
  }
}

/**
 * Route a single parsed event to the appropriate UI component.
 */
export function routeEvent(evt, aiMsg, inSummary) {
  const { target, text, event, data } = evt;

  // ── Structured events ──────────────────────────────────────────────
  if (event === 'field_start' && data) {
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
