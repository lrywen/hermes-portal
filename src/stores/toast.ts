import { defineStore } from 'pinia';
import { ref } from 'vue';
import type { ToastMessage } from '@/shared/types';

// 全局 Toast 通知 store（替代 alert）
export const useToastStore = defineStore('toast', () => {
  const messages = ref<ToastMessage[]>([]);
  let seq = 0;

  function push(text: string, kind: ToastMessage['kind'] = 'info', ttl = 3500) {
    const id = ++seq;
    messages.value.push({ id, kind, text });
    setTimeout(() => dismiss(id), ttl);
  }
  function dismiss(id: number) {
    messages.value = messages.value.filter((m) => m.id !== id);
  }
  const ok = (t: string) => push(t, 'ok');
  const err = (t: string) => push(t, 'err');
  const info = (t: string) => push(t, 'info');

  return { messages, push, dismiss, ok, err, info };
});

// 兼容别名：业务模块中统一使用 useToast
export const useToast = useToastStore;
