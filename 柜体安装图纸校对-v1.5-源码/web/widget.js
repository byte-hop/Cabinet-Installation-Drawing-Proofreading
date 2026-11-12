(() => {
  'use strict';

  const MAX_PDF_BYTES = 120 * 1024 * 1024;
  const state = { mode: 'idle', busy: false, dragDepth: 0, down: null, errorTimer: null, size: 64 };

  const orb = document.querySelector('#orb');
  const gear = document.querySelector('#gear');
  const avatar = document.querySelector('#avatar');
  const dropAvatar = document.querySelector('#dropAvatar');
  const dropKicker = document.querySelector('#dropKicker');
  const dropTitle = document.querySelector('#dropTitle');
  const dropSub = document.querySelector('#dropSub');
  const menuSizeRange = document.querySelector('#menuSizeRange');
  const menuSizeValue = document.querySelector('#menuSizeValue');

  function bridge(method, ...args) {
    const api = window.pywebview && window.pywebview.api;
    if (!api || typeof api[method] !== 'function') return Promise.resolve(null);
    return Promise.resolve(api[method](...args));
  }

  function setAvatar() {
    const url = `/api/widget/avatar?v=${Date.now()}`;
    avatar.src = url;
    dropAvatar.src = url;
  }

  function setCopy(kicker, title, sub) {
    dropKicker.textContent = kicker;
    dropTitle.textContent = title;
    dropSub.textContent = sub;
  }

  function resetCopy() {
    setCopy('PDF 图纸', '将图纸拖拽进来，开始核对！', '支持 领客屋 / 遨森 / 英驰');
  }

  function setMode(mode, resize = true) {
    state.mode = mode;
    document.body.dataset.mode = mode;
    if (mode !== 'error') {
      clearTimeout(state.errorTimer);
      state.errorTimer = null;
    }
    if (mode === 'drop') resetCopy();
    if (resize) bridge('widget_mode', mode);
  }

  function setSizeLabel(value) {
    const size = Number(value) || state.size;
    state.size = size;
    if (menuSizeRange) menuSizeRange.value = String(size);
    if (menuSizeValue) menuSizeValue.textContent = String(size);
  }

  async function loadSettings() {
    try {
      const response = await fetch('/api/widget/info', { cache: 'no-store' });
      if (!response.ok) return;
      const info = await response.json();
      if (menuSizeRange) {
        if (Number.isFinite(Number(info.size_min))) menuSizeRange.min = String(info.size_min);
        if (Number.isFinite(Number(info.size_max))) menuSizeRange.max = String(info.size_max);
      }
      setSizeLabel(Number(info.size) || 64);
    } catch (_) {
      // Keep the last known size if the local service is still starting.
    }
  }

  async function persistSettings(patch) {
    const api = window.pywebview && window.pywebview.api;
    if (api && typeof api.update_widget_settings === 'function') {
      try {
        return await api.update_widget_settings(patch);
      } catch (_) {
        return null;
      }
    }
    try {
      const response = await fetch('/api/widget/settings', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(patch),
      });
      return await response.json();
    } catch (_) {
      return null;
    }
  }

  function resetWidget() {
    state.busy = false;
    state.dragDepth = 0;
    setAvatar();
    resetCopy();
    loadSettings();
    setMode('idle', false);
  }

  function flashError(title, sub) {
    clearTimeout(state.errorTimer);
    setCopy('未识别', title, sub);
    setMode('error');
    state.errorTimer = setTimeout(() => {
      if (state.mode === 'error' && !state.busy) setMode('idle');
    }, 3200);
  }

  function hasFiles(event) {
    const transfer = event.dataTransfer;
    return Boolean(transfer && Array.from(transfer.types || []).includes('Files'));
  }

  function showDropMode() {
    if (state.busy) return;
    setMode('drop');
  }

  function enterFile(file) {
    if (!file) return;
    const name = file.name || '图纸.pdf';
    if (!/\.pdf$/i.test(name)) {
      flashError('请拖入 PDF 图纸', '只支持 .pdf 文件');
      return;
    }
    if (file.size > MAX_PDF_BYTES) {
      flashError('图纸文件太大', '单个文件不能超过 120 MB');
      return;
    }
    analyzeFile(file);
  }

  async function analyzeFile(file) {
    if (state.busy) return;
    state.busy = true;
    clearTimeout(state.errorTimer);
    setCopy('正在读取图纸', '正在核对清单与安装步骤…', file.name);
    setMode('busy');
    try {
      const response = await fetch('/api/analyze', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/pdf',
          'X-Filename': encodeURIComponent(file.name),
        },
        body: file,
      });
      let data;
      try {
        data = await response.json();
      } catch (_) {
        throw new Error('服务器返回的数据无法读取');
      }
      if (!response.ok) throw new Error(data.message || '未识别为有效图纸');
      const detected = data.detected || {};
      if (!detected.template || !data.summary || !data.summary.step_count) {
        throw new Error('未识别到安装步骤');
      }
      setCopy('核对完成', '正在打开主界面…', `${data.file.name} · ${data.summary.step_count} 个步骤`);
      setMode('success');
      await new Promise((resolve) => setTimeout(resolve, 700));
      state.busy = false;
      const shown = await bridge('show_result', data.id);
      if (shown == null) window.location.href = '/';
    } catch (error) {
      state.busy = false;
      flashError('这不是可识别的图纸', '请确认是领客屋、遨森或英驰安装图');
    }
  }

  async function restoreMain() {
    const result = await bridge('restore_main');
    if (result == null) window.location.href = '/';
  }

  async function openMenu() {
    if (state.busy) return;
    setMode('menu');
  }

  async function changeImage() {
    const result = await bridge('choose_widget_image');
    if (!result) {
      setMode('idle');
      return;
    }
    if (result.ok) {
      setAvatar();
      setMode('idle');
      return;
    }
    if (result.canceled) {
      setMode('idle');
      return;
    }
    flashError('图片更新失败', result.message || '请重试');
  }

  orb.addEventListener('pointerdown', (event) => {
    if (event.button !== 0) return;
    state.down = { x: event.screenX, y: event.screenY };
  });

  window.addEventListener('pointerup', (event) => {
    if (!state.down) return;
    const distance = Math.hypot(event.screenX - state.down.x, event.screenY - state.down.y);
    state.down = null;
    if (distance < 6 && state.mode === 'idle') restoreMain();
  });

  gear.addEventListener('click', (event) => {
    event.stopPropagation();
    openMenu();
  });

  if (menuSizeRange) {
    const stopWindowDrag = (event) => event.stopPropagation();
    menuSizeRange.addEventListener('mousedown', stopWindowDrag);
    menuSizeRange.addEventListener('pointerdown', stopWindowDrag);
    menuSizeRange.addEventListener('input', () => setSizeLabel(Number(menuSizeRange.value)));
    menuSizeRange.addEventListener('change', async () => {
      const value = Number(menuSizeRange.value);
      setSizeLabel(value);
      const result = await persistSettings({ size: value });
      if (result && result.ok) {
        setMode('idle');
        return;
      }
      flashError('大小设置失败', (result && result.message) || '请重试');
    });
  }

  window.addEventListener('contextmenu', (event) => {
    event.preventDefault();
    if (state.mode === 'idle') openMenu();
  });

  document.querySelector('#widgetMenu').addEventListener('click', (event) => {
    const button = event.target.closest('[data-widget-action]');
    if (!button) return;
    const action = button.dataset.widgetAction;
    if (action === 'cancel') setMode('idle');
    else if (action === 'restore') restoreMain();
    else if (action === 'change') changeImage();
    else if (action === 'exit') bridge('exit_app');
  });

  window.addEventListener('dragenter', (event) => {
    if (!hasFiles(event) || state.busy) return;
    event.preventDefault();
    state.dragDepth += 1;
    showDropMode();
  });

  window.addEventListener('dragover', (event) => {
    if (!hasFiles(event) || state.busy) return;
    event.preventDefault();
    if (event.dataTransfer) event.dataTransfer.dropEffect = 'copy';
    if (state.mode !== 'drop' && state.mode !== 'busy') showDropMode();
  });

  window.addEventListener('dragleave', () => {
    if (!state.dragDepth) return;
    state.dragDepth = Math.max(0, state.dragDepth - 1);
    if (!state.dragDepth && state.mode === 'drop') setMode('idle');
  });

  window.addEventListener('drop', (event) => {
    const files = event.dataTransfer && event.dataTransfer.files;
    if (!files || !files.length) return;
    event.preventDefault();
    state.dragDepth = 0;
    enterFile(files[0]);
  });

  window.addEventListener('blur', () => {
    if (state.mode === 'drop' && !state.busy) setMode('idle');
  });

  window.cabinetResetWidget = resetWidget;
  window.cabinetSetAvatar = setAvatar;
  window.cabinetEnterDropMode = showDropMode;
  window.cabinetAnalyzeFile = analyzeFile;

  function init() {
    setAvatar();
    resetWidget();
    loadSettings();
    const previewMode = new URLSearchParams(window.location.search).get('mode');
    if (previewMode) setTimeout(() => setMode(previewMode), 120);
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
