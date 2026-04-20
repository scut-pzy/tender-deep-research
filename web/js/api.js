/**
 * api.js — POST-based SSE stream + file upload + compliance APIs
 */

// ── Abort controller for stream cancellation ────────────────────────────
let _currentAbort = null;

export function abortCurrentStream() {
  if (_currentAbort) {
    _currentAbort.abort();
    _currentAbort = null;
  }
}

/**
 * Upload a PDF/Word file and return { id, filename, size }.
 * @param {File} file
 * @param {'tender'|'bid'} fileType
 * @param {(pct: number) => void} [onProgress] - 上传进度回调 0-100
 */
export function uploadFile(file, fileType = 'tender', onProgress) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    const form = new FormData();
    form.append('file', file);
    form.append('file_type', fileType);

    if (onProgress) {
      xhr.upload.addEventListener('progress', (e) => {
        if (e.lengthComputable) {
          onProgress(Math.round(e.loaded / e.total * 100));
        }
      });
    }

    xhr.addEventListener('load', () => {
      try {
        const data = JSON.parse(xhr.responseText);
        if (xhr.status >= 200 && xhr.status < 300) {
          resolve(data);
        } else {
          reject(new Error(data.detail || '上传失败'));
        }
      } catch {
        reject(new Error('上传失败'));
      }
    });

    xhr.addEventListener('error', () => reject(new Error('网络错误')));
    xhr.addEventListener('abort', () => reject(new Error('上传已取消')));

    xhr.open('POST', '/v1/files');
    xhr.send(form);
  });
}

/**
 * Delete a file by ID. Returns true on success.
 */
export async function deleteFile(fileId) {
  const resp = await fetch(`/v1/files/${fileId}`, { method: 'DELETE' });
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({ detail: resp.statusText }));
    throw new Error(formatDetail(err.detail, '删除失败'));
  }
  return true;
}

/**
 * Normalize FastAPI error detail into a readable string.
 * FastAPI validation errors return `detail` as an array of objects — raw
 * conversion produces "[object Object]", so we stringify defensively.
 */
function formatDetail(detail, fallback) {
  if (typeof detail === 'string' && detail) return detail;
  if (Array.isArray(detail)) {
    const msgs = detail.map(d => d?.msg || JSON.stringify(d)).filter(Boolean);
    if (msgs.length) return msgs.join('; ');
  }
  if (detail && typeof detail === 'object') {
    return detail.msg || JSON.stringify(detail);
  }
  return fallback;
}

/**
 * Generic SSE reader: yields content strings from a streaming response.
 */
async function* readSSE(resp) {
  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split('\n');
    buffer = lines.pop();
    for (const line of lines) {
      if (!line.startsWith('data: ')) continue;
      const data = line.slice(6);
      if (data === '[DONE]') return;
      try {
        const chunk = JSON.parse(data);
        const content = chunk.choices?.[0]?.delta?.content;
        if (content) yield content;
      } catch {
        // skip malformed JSON
      }
    }
  }
}

/**
 * Async generator: streams chat completion chunks as strings.
 */
export async function* streamChat(fileId, keys, useCache = true) {
  _currentAbort = new AbortController();
  const message = `[file_id:${fileId}]\n请提取以下标书要素：\n${keys.map((k, i) => `${i + 1}. ${k}`).join('\n')}`;
  try {
    const resp = await fetch('/v1/chat/completions', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        model: 'tender-research',
        messages: [{ role: 'user', content: message }],
        stream: true,
        use_cache: useCache,
      }),
      signal: _currentAbort.signal,
    });

    if (!resp.ok) {
      const err = await resp.json().catch(() => ({ detail: resp.statusText }));
      throw new Error(formatDetail(err.detail, '请求失败'));
    }

    yield* readSSE(resp);
  } finally {
    _currentAbort = null;
  }
}

/**
 * Compliance: generate checklist from tender document.
 */
export async function* streamChecklist(fileId, useCache = true) {
  _currentAbort = new AbortController();
  try {
    const resp = await fetch('/v1/compliance/checklist', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ file_id: fileId, use_cache: useCache }),
      signal: _currentAbort.signal,
    });

    if (!resp.ok) {
      const err = await resp.json().catch(() => ({ detail: resp.statusText }));
      throw new Error(formatDetail(err.detail, '生成审查清单失败'));
    }

    yield* readSSE(resp);
  } finally {
    _currentAbort = null;
  }
}

/**
 * Compliance: check bid document against checklist.
 */
export async function* streamComplianceCheck(fileId, checklist, useCache = true) {
  _currentAbort = new AbortController();
  try {
    const resp = await fetch('/v1/compliance/check', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ file_id: fileId, checklist, use_cache: useCache }),
      signal: _currentAbort.signal,
    });

    if (!resp.ok) {
      const err = await resp.json().catch(() => ({ detail: resp.statusText }));
      throw new Error(formatDetail(err.detail, '合规核查失败'));
    }

    yield* readSSE(resp);
  } finally {
    _currentAbort = null;
  }
}

/**
 * Compliance: reevaluate a single field with user-supplied additional context.
 * Streams reasoning text; the final ```json``` code block carries the updated verdict.
 */
export async function* streamComplianceReeval(payload, useCache = true) {
  _currentAbort = new AbortController();
  try {
    const resp = await fetch('/v1/compliance/reevaluate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ...payload, use_cache: useCache }),
      signal: _currentAbort.signal,
    });

    if (!resp.ok) {
      const err = await resp.json().catch(() => ({ detail: resp.statusText }));
      throw new Error(formatDetail(err.detail, '重新核查失败'));
    }

    yield* readSSE(resp);
  } finally {
    _currentAbort = null;
  }
}

/**
 * Chat QA: free-form question grounded on a document + optional context.
 */
export async function* streamChatQA(fileId, question, contextData = null, useCache = true) {
  _currentAbort = new AbortController();
  try {
    const resp = await fetch('/v1/chat/completions', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        model: 'tender-research',
        messages: [{ role: 'user', content: question }],
        stream: true,
        use_cache: useCache,
        mode: 'chat',
        file_id: fileId,
        context_data: contextData,
      }),
      signal: _currentAbort.signal,
    });

    if (!resp.ok) {
      const err = await resp.json().catch(() => ({ detail: resp.statusText }));
      throw new Error(formatDetail(err.detail, '对话请求失败'));
    }

    yield* readSSE(resp);
  } finally {
    _currentAbort = null;
  }
}
