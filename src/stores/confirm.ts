import { defineStore } from 'pinia';
import { ref } from 'vue';

// Promise 式确认框：调用 confirm() 返回 Promise<boolean>，由 ConfirmDialog 组件承接交互
export const useConfirmStore = defineStore('confirm', () => {
  const open = ref(false);
  const title = ref('');
  const message = ref('');
  const confirmText = ref('确定');
  const cancelText = ref('取消');
  const danger = ref(false);
  let resolver: ((v: boolean) => void) | null = null;

  function confirm(opts: {
    title?: string;
    message: string;
    confirmText?: string;
    cancelText?: string;
    danger?: boolean;
  }): Promise<boolean> {
    title.value = opts.title || '请确认';
    message.value = opts.message;
    confirmText.value = opts.confirmText || '确定';
    cancelText.value = opts.cancelText || '取消';
    danger.value = !!opts.danger;
    open.value = true;
    return new Promise<boolean>((resolve) => {
      resolver = resolve;
    });
  }

  function settle(v: boolean) {
    open.value = false;
    if (resolver) {
      resolver(v);
      resolver = null;
    }
  }

  return { open, title, message, confirmText, cancelText, danger, confirm, settle };
});
