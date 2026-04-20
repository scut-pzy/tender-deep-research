/**
 * history.js — History panel: list past analyses, click to view details
 */

import { createAiMessage, createComplianceMessage } from './chat.js';
import { StreamParser } from './stream-parser.js';
import { routeEvent } from './app.js';
import { resetSidebar } from './sidebar.js';
import { setMode } from './upload.js';

const panel = document.getElementById('history-panel');
const listEl = document.getElementById('history-list');
const toggleBtn = document.getElementById('history-toggle');
const closeBtn = document.getElementById('history-close');
const messagesEl = document.getElementById('messages');

export function setupHistory() {
  if (!toggleBtn || !panel) return;

  toggleBtn.addEventListener('click', () => {
    const isOpen = !panel.classList.contains('hidden');
    if (isOpen) {
      panel.classList.add('hidden');
    } else {
      panel.classList.remove('hidden');
      loadHistory();
    }
  });

  closeBtn?.addEventListener('click', () => {
    panel.classList.add('hidden');
  });
}

async function loadHistory() {
  listEl.innerHTML = '<div class="text-center text-slate-500 text-xs py-8">加载中...</div>';
  try {
    const resp = await fetch('/v1/history');
    const data = await resp.json();
    if (!data.history || data.history.length === 0) {
      listEl.innerHTML = '<div class="text-center text-slate-500 text-xs py-8">暂无历史记录</div>';
      return;
    }
    listEl.innerHTML = '';
    for (const item of data.history) {
      const el = document.createElement('div');
      el.className = 'history-item cursor-pointer rounded-lg p-3 transition ' +
        'dark:bg-white/[0.02] bg-black/[0.02] dark:border border dark:border-white/5 border-black/5 ' +
        'dark:hover:bg-white/[0.05] hover:bg-black/[0.04]';
      const dateStr = new Date(item.timestamp * 1000).toLocaleString('zh-CN', {
        month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit',
      });
      const isCompliance = item.type === 'compliance';
      const typeBadge = isCompliance
        ? '<span class="text-[10px] px-1.5 py-0.5 rounded bg-amber-500/10 text-amber-500 mr-1">合规核查</span>'
        : '<span class="text-[10px] px-1.5 py-0.5 rounded bg-blue-500/10 text-blue-500 mr-1">信息提取</span>';
      const countLabel = isCompliance ? `${item.field_count} 项` : `${item.field_count} 个要素`;
      el.innerHTML = `
        <div class="text-xs font-medium dark:text-slate-200 text-slate-700 truncate">${escapeHtml(item.filename)}</div>
        <div class="flex items-center justify-between mt-1">
          <span class="text-[10px] dark:text-slate-500 text-slate-400">${dateStr}</span>
          <div class="flex items-center gap-1">
            ${typeBadge}
            <span class="text-[10px] px-1.5 py-0.5 rounded dark:bg-accent/10 bg-accent/10 text-accent">${countLabel}</span>
          </div>
        </div>
      `;
      el.addEventListener('click', () => showHistoryDetail(item.id));
      listEl.appendChild(el);
    }
  } catch (e) {
    listEl.innerHTML = `<div class="text-center text-red-400 text-xs py-8">加载失败: ${e.message}</div>`;
  }
}

async function showHistoryDetail(id) {
  try {
    const resp = await fetch(`/v1/history/${id}`);
    const data = await resp.json();

    // Close panel
    panel.classList.add('hidden');

    // Remove welcome message if present
    const w = messagesEl.querySelector('.welcome-message');
    if (w) w.remove();

    const isCompliance = data.type === 'compliance';

    if (isCompliance) {
      // ── 合规核查历史 ──────────────────────────────────────────
      // 切到合规 tab
      const modeTabs = document.getElementById('mode-tabs');
      if (modeTabs) {
        modeTabs.querySelectorAll('.mode-tab').forEach(t => t.classList.remove('active'));
        const compTab = modeTabs.querySelector('[data-mode="compliance"]');
        if (compTab) compTab.classList.add('active');
        setMode('compliance');
      }

      // 渲染合规报告到 checklist-panel
      const result = data.result || {};
      const items = Array.isArray(result) ? result : (result.items || []);
      if (items.length > 0) {
        createComplianceMessage(items);
      }

      // 同时在聊天区回放 stream_log
      const aiMsg = createAiMessage();
      if (data.stream_log && data.stream_log.length > 0) {
        resetSidebar();
        const parser = new StreamParser();
        let inSummary = false;
        for (const chunk of data.stream_log) {
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
      }
      aiMsg.finish();
    } else {
      // ── 信息提取历史 ──────────────────────────────────────────
      const aiMsg = createAiMessage();

      // Replay stream_log to restore sidebar
      if (data.stream_log && data.stream_log.length > 0) {
        resetSidebar();
        const parser = new StreamParser();
        let inSummary = false;
        for (const chunk of data.stream_log) {
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
      } else {
        // Fallback for old history entries without stream_log
        const result = data.result || {};
        const keys = Object.keys(result);
        for (const key of keys) {
          const entry = result[key];
          if (entry) {
            aiMsg.appendField(key, entry.value, entry.source_page, entry.verified ? 1.0 : 0.5);
            aiMsg.updateVerification(key, entry.verified);
          } else {
            aiMsg.ensureField(key, false);
          }
        }
        const jsonStr = JSON.stringify(result, null, 2);
        aiMsg.appendSummary(`## 📋 历史结果 — ${escapeHtml(data.filename)}\n\`\`\`json\n${jsonStr}\n\`\`\`\n`);
      }

      aiMsg.finish();
    }
  } catch (e) {
    const errDiv = document.createElement('div');
    errDiv.className = 'message ai';
    errDiv.innerHTML = `<span class="text-red-400">加载历史详情失败: ${escapeHtml(e.message)}</span>`;
    messagesEl.appendChild(errDiv);
  }
}

function escapeHtml(str) {
  const d = document.createElement('div');
  d.textContent = str;
  return d.innerHTML;
}
