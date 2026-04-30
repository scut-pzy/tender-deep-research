/**
 * stream-parser.js — Route streamed content to main chat vs sidebar panels
 * with structured events for precise UI updates.
 *
 * Returns { target, text, event?, data? }
 *
 * Events:
 *   'field_start'       — detected 🔑 开始处理「key」, data: { key, index, total }
 *   'field_done'        — detected ✅/⚠️「key」处理完成, data: { key, verified }
 *   'field_result'      — detected 「key」= **value** line, data: { key, value, page, confidence }
 *   'critic_result'     — detected ✅/❌「key」line, data: { key, verified, detail }
 *   'round_start'       — detected "第N轮", data: { roundNum }
 *   'rewrite_feedback'  — detected Critic→Policy feedback block
 *   'summary_start'     — detected ## 📋
 */

export const TARGET = {
  PREP: 'prep',
  POLICY: 'policy',
  CRITIC: 'critic',
  REWRITE: 'rewrite',
  MAIN: 'main',
};

// Emoji → stage mapping
const STAGE_MAP = [
  { emoji: '🔍', target: TARGET.MAIN, label: '开始分析' },
  { emoji: '📄', target: TARGET.PREP, label: '文档预处理' },
  { emoji: '🧮', target: TARGET.PREP, label: '索引构建' },
  { emoji: '🔎', target: TARGET.PREP, label: 'RAG 检索' },
  { emoji: '🔑', target: TARGET.MAIN, label: '字段处理' },
  { emoji: '📝', target: TARGET.POLICY, label: 'Policy 提取' },
  { emoji: '👁️', target: TARGET.CRITIC, label: 'Critic 核验' },
  { emoji: '🎉', target: TARGET.MAIN, label: '完成' },
  { emoji: '🔄', target: TARGET.REWRITE, label: '重新检索' },
  { emoji: '⚠️', target: TARGET.MAIN, label: '未通过' },
  { emoji: '✅', target: TARGET.MAIN, label: '通过' },
];

// Regex patterns for structured event detection
const RE_FIELD_START = /🔑\s*开始处理「([^」]+)」\((\d+)\/(\d+)\)/;
const RE_FIELD_DONE_PASS = /✅\s*「([^」]+)」(?:处理完成|投标书提取完成)/;
const RE_FIELD_DONE_WARN = /⚠️\s*「([^」]+)」(?:处理完成|投标书提取完成)/;
const RE_FIELD_DONE_FAIL = /❌\s*「([^」]+)」处理完成/;
const RE_ROUND = /Step\s*4\/5.*第(\d+)轮/;
const RE_FIELD = /「([^」]+)」=\s*\*\*([^*]+)\*\*.*?第(\d+)页.*?置信度[：:](\d+)%/;
const RE_CRITIC_PASS = /✅\s*「([^」]+)」[：:](?!处理完成)\s*(.*)/;
const RE_CRITIC_FAIL = /❌\s*「([^」]+)」[：:]\s*(.*)/;
const RE_REWRITE_HEADER = /Critic\s*→\s*Policy\s*反馈/;
const RE_EMBED_PROGRESS = /⏳\s*Embedding\s*进度[：:]\s*(\d+)\/(\d+)[（(](\d+)%[)）]/;
const RE_SUMMARY = /^##\s*📋/;
const RE_QUERY_REFINE = /→\s*重新生成\s*RAG\s*检索词/;
const RE_QUERY_TERMS = /→\s*新检索词[：:]\s*(.*)/;

export class StreamParser {
  constructor() {
    this.currentTarget = TARGET.MAIN;
    this.currentLabel = '';
    this.lineBuffer = '';
    this.inRewriteBlock = false;
  }

  /**
   * Parse a chunk of text (may contain partial lines).
   * Returns array of { target, text, event?, data? } objects.
   */
  parse(chunk) {
    this.lineBuffer += chunk;
    const results = [];

    const lines = this.lineBuffer.split('\n');
    this.lineBuffer = lines.pop(); // keep incomplete line

    for (const line of lines) {
      const routed = this._routeLine(line + '\n');
      if (routed) results.push(routed);
    }

    // Peek at buffer for routing changes
    if (this.lineBuffer.length > 0) {
      const stage = this._detectStage(this.lineBuffer);
      if (stage) {
        this.currentTarget = stage.target;
        this.currentLabel = stage.label;
      }
    }

    return results;
  }

  flush() {
    if (this.lineBuffer.length === 0) return [];
    const routed = this._routeLine(this.lineBuffer);
    this.lineBuffer = '';
    return routed ? [routed] : [];
  }

  _routeLine(line) {
    const trimmed = line.trimStart();

    // Detect stage change
    const stage = this._detectStage(line);
    if (stage) {
      this.currentTarget = stage.target;
      this.currentLabel = stage.label;
      // Exit rewrite block when new stage begins
      if (stage.target !== TARGET.REWRITE) {
        this.inRewriteBlock = false;
      }
    }

    // Field start: 🔑 开始处理「key」(N/total)
    const fieldStartMatch = trimmed.match(RE_FIELD_START);
    if (fieldStartMatch) {
      return {
        target: TARGET.MAIN,
        text: line,
        event: 'field_start',
        data: { key: fieldStartMatch[1], index: parseInt(fieldStartMatch[2]), total: parseInt(fieldStartMatch[3]) },
      };
    }

    // Field done: ✅「key」处理完成 or ⚠️「key」处理完成
    const fieldDonePass = trimmed.match(RE_FIELD_DONE_PASS);
    if (fieldDonePass) {
      return {
        target: TARGET.MAIN,
        text: line,
        event: 'field_done',
        data: { key: fieldDonePass[1], verified: true },
      };
    }
    const fieldDoneWarn = trimmed.match(RE_FIELD_DONE_WARN);
    if (fieldDoneWarn) {
      return {
        target: TARGET.MAIN,
        text: line,
        event: 'field_done',
        data: { key: fieldDoneWarn[1], verified: false },
      };
    }
    const fieldDoneFail = trimmed.match(RE_FIELD_DONE_FAIL);
    if (fieldDoneFail) {
      return {
        target: TARGET.MAIN,
        text: line,
        event: 'field_done',
        data: { key: fieldDoneFail[1], verified: false },
      };
    }

    // Embedding progress: ⏳ Embedding 进度：done/total（pct%）
    const embedMatch = trimmed.match(RE_EMBED_PROGRESS);
    if (embedMatch) {
      return {
        target: TARGET.PREP,
        text: line,
        event: 'embed_progress',
        data: { done: parseInt(embedMatch[1]), total: parseInt(embedMatch[2]), pct: parseInt(embedMatch[3]) },
      };
    }

    // Check for rewrite feedback header
    if (RE_REWRITE_HEADER.test(trimmed)) {
      this.inRewriteBlock = true;
      this.currentTarget = TARGET.REWRITE;
      return { target: TARGET.REWRITE, text: line, event: 'rewrite_feedback' };
    }

    // Round 3: query refinement lines
    if (RE_QUERY_REFINE.test(trimmed)) {
      this.inRewriteBlock = true;
      this.currentTarget = TARGET.REWRITE;
      return { target: TARGET.REWRITE, text: line, event: 'query_refine_start' };
    }

    const queryTermsMatch = trimmed.match(RE_QUERY_TERMS);
    if (queryTermsMatch) {
      return { target: TARGET.REWRITE, text: line, event: 'query_refine_terms', data: { terms: queryTermsMatch[1] } };
    }

    // Field result detection — must precede inRewriteBlock check so that Round 3
    // re-extraction results still update the live table even while in rewrite context.
    const fieldMatch = trimmed.match(RE_FIELD);
    if (fieldMatch) {
      return {
        target: TARGET.POLICY,
        text: line,
        event: 'field_result',
        data: {
          key: fieldMatch[1],
          value: fieldMatch[2],
          page: parseInt(fieldMatch[3]),
          confidence: parseInt(fieldMatch[4]) / 100,
        },
      };
    }

    // If in rewrite block, keep routing there until new stage
    if (this.inRewriteBlock && this.currentTarget === TARGET.REWRITE) {
      return { target: TARGET.REWRITE, text: line };
    }

    // Summary table → always main
    if (RE_SUMMARY.test(trimmed)) {
      this.currentTarget = TARGET.MAIN;
      return { target: TARGET.MAIN, text: line, event: 'summary_start' };
    }

    // Table rows go to main (summary context)
    if (trimmed.startsWith('|') || trimmed.startsWith('>')) {
      return { target: TARGET.MAIN, text: line };
    }

    // Round start detection
    const roundMatch = trimmed.match(RE_ROUND);
    if (roundMatch) {
      const roundNum = parseInt(roundMatch[1]);
      return {
        target: this.currentTarget,
        text: line,
        event: 'round_start',
        data: { roundNum },
      };
    }

    // Critic pass
    const passMatch = trimmed.match(RE_CRITIC_PASS);
    if (passMatch) {
      return {
        target: TARGET.CRITIC,
        text: line,
        event: 'critic_result',
        data: { key: passMatch[1], verified: true, detail: passMatch[2] },
      };
    }

    // Critic fail
    const failMatch = trimmed.match(RE_CRITIC_FAIL);
    if (failMatch) {
      const detail = failMatch[2];
      const isVlmError = detail.includes('VLM调用失败') || detail.includes('第3轮异常') || detail.includes('异常 (');
      return {
        target: TARGET.CRITIC,
        text: line,
        event: 'critic_result',
        data: { key: failMatch[1], verified: false, detail, isVlmError },
      };
    }

    return { target: this.currentTarget, text: line };
  }

  _detectStage(text) {
    const trimmed = text.trimStart();
    for (const stage of STAGE_MAP) {
      if (trimmed.startsWith(stage.emoji)) {
        return stage;
      }
    }
    return null;
  }
}
