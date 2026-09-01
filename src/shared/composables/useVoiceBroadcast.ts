import { ref } from 'vue';
import http from '@/shared/api/client';

/**
 * 语音播报 composable。
 * - voiceId 为 null 时使用浏览器内置 SpeechSynthesis（系统默认语音）
 * - voiceId 非空时播放后端 /api/portal/alerts/voices/{id}/audio 自定义音频
 *
 * 所有关键节点均输出 console.log，便于排查浏览器自动播放拦截、
 * 音频加载失败、CORS/鉴权等问题。日志统一加 [VoiceBroadcast] 前缀。
 */
const LOG = '[VoiceBroadcast]';

export function useVoiceBroadcast() {
  const speaking = ref(false);
  let currentAudio: HTMLAudioElement | null = null;

  function stop() {
    console.log(LOG, 'stop() called — speaking =', speaking.value);
    if (currentAudio) {
      currentAudio.pause();
      currentAudio.onended = null;
      currentAudio.onerror = null;
      currentAudio = null;
    }
    window.speechSynthesis?.cancel();
    speaking.value = false;
  }

  function speakWithTTS(text: string, volume = 0.8) {
    console.log(LOG, 'speakWithTTS() — text:', JSON.stringify(text).slice(0, 80), 'volume:', volume);
    if (!('speechSynthesis' in window)) {
      console.warn(LOG, '当前浏览器不支持 SpeechSynthesis，TTS 不可用');
      return;
    }
    window.speechSynthesis.cancel();
    const utter = new SpeechSynthesisUtterance(text);
    utter.lang = 'zh-CN';
    utter.volume = volume;
    utter.rate = 1.0;
    const voices = window.speechSynthesis.getVoices();
    const zhVoice = voices.find((v) => v.lang.startsWith('zh'));
    if (zhVoice) utter.voice = zhVoice;
    console.log(LOG, 'TTS 可用语音数:', voices.length, '选中中文语音:', zhVoice?.name ?? '无（使用默认）');
    utter.onstart = () => {
      speaking.value = true;
      console.log(LOG, 'TTS onstart — 开始播报');
    };
    utter.onend = () => {
      speaking.value = false;
      console.log(LOG, 'TTS onend — 播报结束');
    };
    utter.onerror = (e) => {
      speaking.value = false;
      console.error(LOG, 'TTS onerror —', e.error);
    };
    window.speechSynthesis.speak(utter);
  }

  async function speakCustom(voiceId: string, volume = 0.8) {
    const url = `/api/portal/alerts/voices/${voiceId}/audio`;
    console.log(LOG, 'speakCustom() — voiceId:', voiceId, 'volume:', volume, 'url:', url);
    try {
      const resp = await http.get(url, { responseType: 'blob' });
      const blob = resp.data as Blob;
      console.log(LOG, '音频加载完成 — type:', blob.type, 'size:', blob.size, 'bytes');
      const objectUrl = URL.createObjectURL(blob);
      console.log(LOG, 'createObjectURL:', objectUrl);

      currentAudio = new Audio(objectUrl);
      currentAudio.volume = volume;
      currentAudio.preload = 'auto';

      currentAudio.addEventListener('loadstart', () => console.log(LOG, 'audio loadstart'));
      currentAudio.addEventListener('loadeddata', () => console.log(LOG, 'audio loadeddata (首帧数据就绪)'));
      currentAudio.addEventListener('canplay', () => console.log(LOG, 'audio canplay'));
      currentAudio.addEventListener('canplaythrough', () => console.log(LOG, 'audio canplaythrough (可完整播放)'));

      currentAudio.onended = () => {
        speaking.value = false;
        console.log(LOG, 'audio onended — 播放结束，revokeObjectURL');
        URL.revokeObjectURL(objectUrl);
      };
      currentAudio.onerror = (e) => {
        speaking.value = false;
        const err = currentAudio?.error;
        console.error(LOG, 'audio onerror — code:', err?.code, 'message:', err?.message ?? '(无)', '事件:', e);
        URL.revokeObjectURL(objectUrl);
      };
      currentAudio.onpause = () => console.log(LOG, 'audio onpause');

      speaking.value = true;
      console.log(LOG, '调用 audio.play() ...');
      const playPromise = currentAudio.play();
      if (playPromise !== undefined) {
        await playPromise;
        console.log(LOG, 'audio.play() resolve — 播放已开始');
      } else {
        console.log(LOG, 'audio.play() 返回 undefined（旧浏览器）');
      }
    } catch (err: any) {
      speaking.value = false;
      // 浏览器自动播放策略拦截：DOMException name === 'NotAllowedError'
      console.error(LOG, 'speakCustom 失败 — name:', err?.name, 'message:', err?.message, err);
      if (err?.name === 'NotAllowedError') {
        console.warn(
          LOG,
          '⚠️ 浏览器阻止了自动播放。请确保在用户点击/交互后触发，或在浏览器设置中允许此站点的声音权限。'
        );
      }
    }
  }

  async function broadcast(text: string, opts: { voiceId: string | null; volume: number; enabled: boolean }) {
    const hasGesture =
      typeof navigator !== 'undefined' && (navigator as any).userActivation
        ? (navigator as any).userActivation.hasBeenActive
        : '(不支持 userActivation API)';
    const isVisible = typeof document !== 'undefined' ? !document.hidden : true;
    console.log(
      LOG,
      'broadcast() — enabled:',
      opts.enabled,
      'voiceId:',
      opts.voiceId,
      'volume:',
      opts.volume,
      '| 用户已交互过页面:',
      hasGesture,
      '| 页面可见:',
      isVisible,
    );
    if (!opts.enabled) {
      console.log(LOG, 'broadcast 跳过：语音播报未启用');
      return;
    }
    if (hasGesture === false) {
      console.warn(
        LOG,
        '⚠️ 用户尚未与页面发生过任何交互（点击/按键），浏览器很可能阻止自动播放。请先在页面上点击一次。'
      );
    }
    if (!isVisible) {
      console.warn(LOG, '⚠️ 页面当前处于后台/隐藏状态，部分浏览器会暂停后台标签页的音频播放。');
    }
    stop();
    if (opts.voiceId) {
      await speakCustom(opts.voiceId, opts.volume);
    } else {
      speakWithTTS(text, opts.volume);
    }
  }

  return { speaking, broadcast, stop };
}
