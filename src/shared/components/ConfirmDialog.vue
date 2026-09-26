<script setup lang="ts">
import { onMounted, onUnmounted } from 'vue';
import { useConfirmStore } from '@/stores/confirm';

const confirmStore = useConfirmStore();

function onKey(e: KeyboardEvent) {
  if (!confirmStore.open) return;
  if (e.key === 'Escape') confirmStore.settle(false);
  else if (e.key === 'Enter') confirmStore.settle(true);
}
onMounted(() => window.addEventListener('keydown', onKey));
onUnmounted(() => window.removeEventListener('keydown', onKey));
</script>

<template>
  <Teleport to="body">
    <div v-if="confirmStore.open" class="confirm-overlay" @click.self="confirmStore.settle(false)">
      <div
        class="confirm-dialog"
        role="dialog"
        aria-modal="true"
        :aria-label="confirmStore.title"
      >
        <h3 class="text-base font-semibold mb-2">{{ confirmStore.title }}</h3>
        <p class="text-sm text-[var(--muted)] whitespace-pre-line mb-5">{{ confirmStore.message }}</p>
        <div class="flex justify-end gap-2">
          <button class="btn text-sm" @click="confirmStore.settle(false)">{{ confirmStore.cancelText }}</button>
          <button
            class="btn text-sm"
            :class="confirmStore.danger ? 'bg-rose-600 border-rose-600 text-white hover:bg-rose-500' : ''"
            @click="confirmStore.settle(true)"
          >{{ confirmStore.confirmText }}</button>
        </div>
      </div>
    </div>
  </Teleport>
</template>

<style scoped>
.confirm-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.55);
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 16px;
  z-index: 9500;
}
.confirm-dialog {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  box-shadow: var(--shadow);
  padding: 22px;
  width: 100%;
  max-width: 400px;
}
</style>
