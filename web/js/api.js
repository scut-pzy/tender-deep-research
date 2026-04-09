/**
 * api.js — POST-based SSE stream + file upload + compliance APIs
 */

/**
 * Upload a PDF file and return { id, filename, size }.
 * @param {File} file
 * @param {'tender'|'bid'} fileType
 */
export async function uploadFile(file, fileType = 'tender') {
  const form = new FormData();
  form.append('file', file);
  form.append('file_type', fileType);
  const resp = await fetch('/v1/files', { method: 'POST', body: form });
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({ detail: resp.statusText }));
    throw new Error(err.detail || '上传失败');
  }
  return await resp.json();
}

/**
 * Delete a file by ID. Returns true on success.
 */
export async function deleteFile(fileId) {
  const resp = await fetch(`/v1/files/${fileId}`, { method: 'DELETE' });
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({ detail: resp.statusText }));
    throw new Error(err.detail || '删除失败');
  }
  return true;
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
  const message = `[file_id:${fileId}]\n请提取以下标书要素：\n${keys.map((k, i) => `${i + 1}. ${k}`).join('\n')}`;
  const resp = await fetch('/v1/chat/completions', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      model: 'tender-research',
      messages: [{ role: 'user', content: message }],
      stream: true,
      use_cache: useCache,
    }),
  });

  if (!resp.ok) {
    const err = await resp.json().catch(() => ({ detail: resp.statusText }));
    throw new Error(err.detail || '请求失败');
  }

  yield* readSSE(resp);
}

/**
 * Compliance: generate checklist from tender document.
 */
export async function* streamChecklist(fileId, useCache = true) {
  const resp = await fetch('/v1/compliance/checklist', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ file_id: fileId, use_cache: useCache }),
  });

  if (!resp.ok) {
    const err = await resp.json().catch(() => ({ detail: resp.statusText }));
    throw new Error(err.detail || '生成审查清单失败');
  }

  yield* readSSE(resp);
}

/**
 * Compliance: check bid document against checklist.
 */
export async function* streamComplianceCheck(fileId, checklist, useCache = true) {
  const resp = await fetch('/v1/compliance/check', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ file_id: fileId, checklist, use_cache: useCache }),
  });

  if (!resp.ok) {
    const err = await resp.json().catch(() => ({ detail: resp.statusText }));
    throw new Error(err.detail || '合规核查失败');
  }

  yield* readSSE(resp);
}
