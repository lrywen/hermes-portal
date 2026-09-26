<script setup lang="ts">
import { reactive, watch } from 'vue';

// 嵌套对象块的结构化编辑器：把一个 object 配置按字段规格渲染为带标签的表单，
// 取代裸 JSON textarea。值直接写回 model[key]，与父级 config 双向共享。

interface FieldSpec {
  label: string;
  type: 'bool' | 'int' | 'float';
  unit?: string;
  step?: number;
  hint?: string;
}

const props = defineProps<{
  model: Record<string, any>;
  fields: Record<string, FieldSpec>;
}>();

// 本地草稿（数值/布尔直接代理到 model；编辑即生效，随外层 save 一起提交）
const draft = reactive<Record<string, any>>({});
watch(
  () => props.model,
  (m) => {
    Object.keys(props.fields).forEach((k) => {
      draft[k] = m?.[k];
    });
  },
  { deep: true, immediate: true },
);

function set(key: string, value: any) {
  draft[key] = value;
  props.model[key] = value;
}
</script>

<template>
  <div class="space-y-3">
    <div v-for="(spec, key) in fields" :key="key">
      <label class="flex items-center justify-between gap-2 text-sm">
        <span class="flex items-center gap-1.5">
          <span>{{ spec.label }}</span>
          <span class="text-[10px] text-[var(--text-muted)] font-mono">{{ key }}</span>
        </span>
        <input
          v-if="spec.type === 'bool'"
          type="checkbox"
          class="w-4 h-4 accent-blue-500"
          :checked="!!draft[key]"
          @change="(e) => set(key, (e.target as HTMLInputElement).checked)"
        />
      </label>

      <div v-if="spec.type !== 'bool'" class="flex items-center gap-2 mt-1">
        <input
          type="number"
          :step="spec.step ?? (spec.type === 'float' ? 0.05 : 1)"
          class="input flex-1"
          :value="draft[key]"
          @input="(e) => set(key, spec.type === 'float'
            ? parseFloat((e.target as HTMLInputElement).value)
            : parseInt((e.target as HTMLInputElement).value, 10))"
        />
        <span v-if="spec.unit" class="text-xs text-[var(--text-muted)] whitespace-nowrap">{{ spec.unit }}</span>
      </div>
      <p v-if="spec.hint" class="text-[11px] text-[var(--text-muted)] mt-1 leading-relaxed">{{ spec.hint }}</p>
    </div>
  </div>
</template>
