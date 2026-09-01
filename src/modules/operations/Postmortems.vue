<script setup lang="ts">
/**
 * 复盘报告
 * - 列出 hermes-trader 产出的 postmortem markdown 报告
 * - 选择后拉取 markdown 原文，增强渲染（标题/表格/列表/代码/分隔线/粗体）
 * - 报告本身已由后端生成中文内容，前端侧重排版美化
 */
import { onMounted, ref } from 'vue';
import { useRoute } from 'vue-router';
import http from '@/shared/api/client';
import { useToast } from '@/stores/toast';

const toast = useToast();
const route = useRoute();
const reports = ref<{ name: string; size: number; mtime: number }[]>([]);
const current = ref<string>('');
const content = ref<string>('');
const loading = ref(true);
const viewing = ref(false);

async function load() {
  loading.value = true;
  try {
    const { data } = await http.get('/api/portal/trader/postmortems');
    reports.value = data.reports || [];
  } catch (e: any) {
    toast.err(e?.response?.data?.detail || '加载复盘列表失败');
  } finally {
    loading.value = false;
  }
}

async function view(name: string) {
  viewing.value = true;
  try {
    const resp = await http.get(`/api/portal/trader/postmortems/${name}`, { responseType: 'text' });
    current.value = name;
    content.value = typeof resp.data === 'string' ? resp.data : JSON.stringify(resp.data);
  } catch (e: any) {
    toast.err(e?.response?.data?.detail || '加载报告失败');
  } finally {
    viewing.value = false;
  }
}

function fmtSize(b: number) {
  if (b < 1024) return `${b} B`;
  if (b < 1024 * 1024) return `${(b / 1024).toFixed(1)} KB`;
  return `${(b / 1024 / 1024).toFixed(2)} MB`;
}
function fmtDate(ts: number) {
  return new Date(ts * 1000).toLocaleString('zh-CN', { hour12: false });
}

function escapeHtml(s: string) {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

function inlineFormat(text: string): string {
  return escapeHtml(text)
    .replace(/\*\*(.+?)\*\*/g, '<strong class="text-white font-semibold">$1</strong>')
    .replace(/`([^`]+)`/g, '<code class="px-1 py-0.5 rounded bg-white/10 text-cyan-300 text-[0.85em] font-mono">$1</code>')
    .replace(/_(.+?)_/g, '<em class="text-slate-400">$1</em>');
}

/** 增强 markdown → HTML：支持标题/表格/有序无序列表/引用块/代码块/分隔线/粗体 */
function renderMd(md: string): string {
  const lines = md.split('\n');
  const html: string[] = [];
  let listType: 'ul' | 'ol' | null = null;
  let inCode = false;
  let quoteBuf: string[] = [];
  let tableRows: string[][] = [];
  let tableAlign: ('left' | 'center' | 'right')[] = [];

  function closeList() {
    if (listType) {
      html.push(`</${listType}>`);
      listType = null;
    }
  }
  function openList(kind: 'ul' | 'ol') {
    if (listType === kind) return;
    closeList();
    if (kind === 'ol') {
      html.push('<ol class="list-decimal pl-5 space-y-1 my-2 text-sm leading-relaxed text-slate-300 marker:text-cyan-400">');
    } else {
      html.push('<ul class="list-disc pl-5 space-y-1 my-2 text-sm leading-relaxed text-slate-300 marker:text-cyan-400">');
    }
    listType = kind;
  }
  function flushQuote() {
    if (quoteBuf.length === 0) return;
    const inner = quoteBuf
      .map((q) => inlineFormat(q.trim()))
      .join('<br>');
    html.push(
      `<blockquote class="my-3 pl-4 py-2 border-l-4 border-cyan-400/60 bg-cyan-400/5 rounded-r text-sm text-slate-300 italic leading-relaxed">${inner}</blockquote>`,
    );
    quoteBuf = [];
  }
  function flushTable() {
    if (tableRows.length === 0) return;
    html.push('<div class="overflow-x-auto my-4 rounded-lg border border-[var(--border)]"><table class="w-full text-xs border-collapse">');
    tableRows.forEach((row, ri) => {
      const tag = ri === 0 ? 'th' : 'td';
      html.push(
        ri === 0
          ? '<tr class="bg-white/5">'
          : '<tr class="hover:bg-white/5 transition-colors">',
      );
      row.forEach((cell, ci) => {
        const align = tableAlign[ci] || 'left';
        const cls = ri === 0
          ? 'px-3 py-2 text-left font-semibold text-white border-b border-[var(--border)] whitespace-nowrap'
          : 'px-3 py-1.5 border-b border-[var(--border)]/50 font-mono text-slate-300';
        html.push(`<${tag} class="${cls}" style="text-align:${align}">${inlineFormat(cell.trim())}</${tag}>`);
      });
      html.push('</tr>');
    });
    html.push('</table></div>');
    tableRows = [];
    tableAlign = [];
  }

  for (let i = 0; i < lines.length; i++) {
    const raw = lines[i];
    const line = raw;

    // 代码块围栏
    if (/^```/.test(line.trim())) {
      closeList(); flushQuote(); flushTable();
      if (inCode) { html.push('</code></pre>'); inCode = false; }
      else { html.push('<pre class="bg-black/50 rounded-lg p-3 my-3 overflow-x-auto text-xs border border-[var(--border)]"><code class="text-slate-300 font-mono">'); inCode = true; }
      continue;
    }
    if (inCode) {
      html.push(escapeHtml(line) + '\n');
      continue;
    }

    // 引用块（> 开头，可连续多行）
    if (/^>\s?/.test(line.trimStart())) {
      closeList(); flushTable();
      quoteBuf.push(line.trimStart().replace(/^>\s?/, ''));
      continue;
    } else {
      flushQuote();
    }

    // 表格分隔线 |---|---|
    if (/^\s*\|?[\s:|-]+\|[\s:|-]+$/.test(line) && line.includes('-')) {
      const cells = line.split('|').map((c) => c.trim()).filter((c, idx, arr) => {
        if (idx === 0 && c === '') return false;
        if (idx === arr.length - 1 && c === '') return false;
        return true;
      });
      tableAlign = cells.map((c) => {
        if (c.startsWith(':') && c.endsWith(':')) return 'center';
        if (c.endsWith(':')) return 'right';
        return 'left';
      });
      continue;
    }

    // 表格行
    if (/^\s*\|.*\|\s*$/.test(line) && line.includes('|')) {
      closeList();
      const cells = line.split('|').map((c) => c.trim()).filter((c, idx, arr) => {
        if (idx === 0 && c === '') return false;
        if (idx === arr.length - 1 && c === '') return false;
        return true;
      });
      if (cells.length > 0) tableRows.push(cells);
      continue;
    } else {
      flushTable();
    }

    // 分隔线
    if (/^---+\s*$/.test(line) || /^\*\*\*+\s*$/.test(line)) {
      closeList();
      html.push('<hr class="my-5 border-[var(--border)]">');
      continue;
    }

    // 标题
    if (/^####\s+/.test(line)) {
      closeList();
      html.push(`<h4 class="text-sm font-semibold mt-4 mb-2 text-slate-200 flex items-center gap-2 before:content-['▸'] before:text-cyan-400">${inlineFormat(line.replace(/^####\s+/, ''))}</h4>`);
    } else if (/^###\s+/.test(line)) {
      closeList();
      html.push(`<h3 class="text-base font-semibold mt-6 mb-2 text-white flex items-center gap-2 before:content-['▸'] before:text-cyan-400">${inlineFormat(line.replace(/^###\s+/, ''))}</h3>`);
    } else if (/^##\s+/.test(line)) {
      closeList();
      html.push(`<h2 class="text-lg font-bold mt-7 mb-3 text-white pb-2 border-b border-[var(--border)]">${inlineFormat(line.replace(/^##\s+/, ''))}</h2>`);
    } else if (/^#\s+/.test(line)) {
      closeList();
      html.push(`<h1 class="text-2xl font-bold mt-2 mb-5 text-white tracking-tight">${inlineFormat(line.replace(/^#\s+/, ''))}</h1>`);
    } else if (/^\d+\.\s+/.test(line)) {
      openList('ol');
      html.push(`<li>${inlineFormat(line.replace(/^\d+\.\s+/, ''))}</li>`);
    } else if (/^[-*]\s+/.test(line)) {
      openList('ul');
      html.push(`<li>${inlineFormat(line.replace(/^[-*]\s+/, ''))}</li>`);
    } else if (line.trim() === '') {
      closeList();
    } else {
      closeList();
      html.push(`<p class="my-2 leading-7 text-sm text-slate-300">${inlineFormat(line)}</p>`);
    }
  }
  closeList();
  flushQuote();
  flushTable();
  if (inCode) html.push('</code></pre>');
  return html.join('\n');
}

onMounted(async () => {
  await load();
  // 支持飞书卡片深链：/portal/postmortems?file=xxx.md，登录后自动打开对应报告
  const file = route.query.file;
  if (typeof file === 'string' && file) {
    const exists = reports.value.some((r) => r.name === file);
    if (exists) {
      await view(file);
    } else {
      toast.err(`未找到报告：${file}`);
    }
  }
});
</script>

<template>
  <div class="space-y-5">
    <header class="flex items-center justify-between flex-wrap gap-3">
      <div>
        <h2 class="text-xl font-semibold">复盘报告</h2>
        <p class="text-sm text-[var(--text-muted)] mt-1">暴涨行情/极端事件的事后复盘归档（只读）</p>
      </div>
      <div class="flex gap-2">
        <button v-if="current" class="btn" @click="current = ''">← 返回列表</button>
        <button class="btn" @click="load">🔄 刷新</button>
      </div>
    </header>

    <div v-if="loading" class="card text-center py-10 text-[var(--text-muted)]">加载中...</div>

    <!-- 列表 -->
    <div v-else-if="!current" class="card p-0 overflow-hidden">
      <div v-if="reports.length === 0" class="py-16 text-center text-[var(--text-muted)]">
        <div class="text-4xl mb-3 opacity-40">📭</div>
        <div>暂无复盘报告</div>
        <div class="text-xs mt-1 opacity-60">暴涨行情触发后将自动生成报告</div>
      </div>
      <ul v-else class="divide-y divide-[var(--border)]">
        <li v-for="r in reports" :key="r.name" class="group">
          <button
            class="w-full flex items-center justify-between py-3 px-4 hover:bg-[var(--surface-hover)] transition-colors text-left"
            @click="view(r.name)"
          >
            <div class="flex items-center gap-3">
              <span class="text-lg">📄</span>
              <div>
                <div class="font-mono text-sm text-slate-200 group-hover:text-cyan-300 transition-colors">{{ r.name }}</div>
                <div class="text-xs text-[var(--text-muted)] mt-0.5">🕐 {{ fmtDate(r.mtime) }}</div>
              </div>
            </div>
            <div class="text-xs text-[var(--text-muted)]">{{ fmtSize(r.size) }}</div>
          </button>
        </li>
      </ul>
    </div>

    <!-- 详情 -->
    <div v-else class="card">
      <div v-if="viewing" class="py-16 text-center text-[var(--text-muted)]">
        <div class="animate-pulse">加载报告中...</div>
      </div>
      <article
        v-else
        class="max-w-none pm-article"
        v-html="renderMd(content)"
      ></article>
    </div>
  </div>
</template>

<style scoped>
/* 中文排版优化（穿透 v-html 注入的内容） */
.pm-article :deep(p) {
  text-align: justify;
  word-break: break-word;
}
.pm-article :deep(li) {
  text-align: justify;
}
.pm-article :deep(p + p) {
  margin-top: 0.6rem;
}
/* 中文字符之间使用宽松字距，数字/英文保留正常间距 */
.pm-article :deep(h1),
.pm-article :deep(h2) {
  letter-spacing: 0.02em;
}
</style>
