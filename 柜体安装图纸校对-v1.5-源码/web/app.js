(() => {
  'use strict';

  const state = {
    result: null,
    view: 'overview',
    hwQuery: '',
    hwDiffOnly: false,
    partQuery: '',
    partIssueOnly: false,
    stepQuery: '',
    stepIssueOnly: false,
    issueFilter: 'all',
    bomMap: {},
    partMap: {},
    stepMap: {},
    usageByCode: {},
    partRefs: {},
    issuesByStep: {},
    issuesByPage: {},
    viewer: { page: 1, boxes: [], title: '', sideHtml: '', zoomed: false, severity: 'info' },
    loadingTimer: null,
    toastTimer: null,
    knowledge: null,
  };

  const MAX_PDF_BYTES = 120 * 1024 * 1024;

  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));

  const esc = (value) =>
    String(value == null ? '' : value)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');

  const formatBytes = (bytes) => {
    if (!bytes) return '0 MB';
    const mb = bytes / 1024 / 1024;
    return mb >= 1 ? `${mb.toFixed(1)} MB` : `${Math.round(bytes / 1024)} KB`;
  };

  const ROLE_TEXT = {
    hardware: '五金',
    parts: '板件',
    steps: '步骤',
    cover: '封面',
    overview: '总览',
    intro: '说明',
    appendix: '附录',
    other: '其他',
  };
  const SEV_TEXT = { error: '错误', warn: '待确认', info: '提示' };
  const CHECK_TEXT = { pass: '通过', warn: '确认', fail: '异常' };

  const ICONS = {
    check: '<svg viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="m5 12.5 4.2 4.2L19 6.8" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>',
    alert: '<svg viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="M12 4 21 19H3L12 4Z" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"/><path d="M12 9.2v4" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/><circle cx="12" cy="16.2" r="1" fill="currentColor"/></svg>',
    x: '<svg viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="m7 7 10 10M17 7 7 17" stroke="currentColor" stroke-width="1.9" stroke-linecap="round"/></svg>',
    info: '<svg viewBox="0 0 24 24" fill="none" aria-hidden="true"><circle cx="12" cy="12" r="8.5" stroke="currentColor" stroke-width="1.6"/><path d="M12 11v5" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/><circle cx="12" cy="7.8" r="1" fill="currentColor"/></svg>',
    shield: '<svg viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="M12 2.8 20 6v6c0 5-3.4 8.2-8 9.2-4.6-1-8-4.2-8-9.2V6l8-3.2Z" stroke="currentColor" stroke-width="1.5"/><path d="m8.8 12 2.1 2.1 4.4-4.5" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>',
    search: '<svg viewBox="0 0 24 24" fill="none" aria-hidden="true"><circle cx="10.5" cy="10.5" r="6" stroke="currentColor" stroke-width="1.7"/><path d="m15 15 4.5 4.5" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/></svg>',
  };

  const content = $('#content');
  const fileInput = $('#fileInput');
  const fileCard = $('#fileCard');
  const nav = $('#nav');
  const btnOpen = $('#btnOpen');
  const btnExport = $('#btnExport');
  const btnHelp = $('#btnHelp');
  const btnKnowledge = $('#btnKnowledge');
  const btnWidget = $('#btnWidget');
  const loading = $('#loading');
  const loadingText = $('#loadingText');
  const toastEl = $('#toast');
  const viewer = $('#viewer');
  const viewerImg = $('#viewerImg');
  const pageStage = $('#pageStage');
  const viewerBoxes = $('#viewerBoxes');
  const viewerCanvas = $('#viewerCanvas');
  const viewerSide = $('#viewerSide');
  const viewerTitle = $('#viewerTitle');
  const viewerKicker = $('#viewerKicker');
  const viewerPages = $('#viewerPages');
  const crumb = $('#crumb');
  const topbarSub = $('#topbarSub');
  const dropOverlay = $('#dropOverlay');

  const LOADING_MESSAGES = [
    '读取 PDF 页面与文字层…',
    '识别五金清单与板件清单…',
    '汇总各步骤的五金用量表…',
    '交叉核对编号、数量与重复模块…',
    '生成图纸定位结果…',
  ];

  function init() {
    bindEvents();
    renderEmpty();
    loadKnowledgeStats();
  }

  function bindEvents() {
    btnOpen.addEventListener('click', () => fileInput.click());
    fileInput.addEventListener('change', () => {
      const file = fileInput.files && fileInput.files[0];
      fileInput.value = '';
      if (file) analyzeFile(file);
    });
    bindDropZone();
    btnExport.addEventListener('click', exportReport);
    btnHelp.addEventListener('click', showHelp);
    btnKnowledge.addEventListener('click', showKnowledge);
    btnWidget.addEventListener('click', showWidgetSettings);

    nav.addEventListener('click', (event) => {
      const button = event.target.closest('[data-view]');
      if (!button || !state.result) return;
      setView(button.dataset.view);
    });

    content.addEventListener('click', (event) => {
      const target = event.target.closest('[data-action]');
      if (!target) return;
      const action = target.dataset.action;
      if (action === 'sample') loadSample();
      else if (action === 'pick-file') fileInput.click();
      else if (action === 'open-hardware') openHardware(target.dataset.code);
      else if (action === 'open-part') openPart(Number(target.dataset.no));
      else if (action === 'open-step') openStep(Number(target.dataset.no));
      else if (action === 'open-issue') openIssue(target.dataset.id);
      else if (action === 'open-page') openPage(Number(target.dataset.page));
      else if (action === 'go-view') setView(target.dataset.view);
      else if (action === 'export') exportReport();
      else if (action === 'dismiss-issue') submitFeedback(target.dataset.id, 'dismiss');
      else if (action === 'confirm-issue') submitFeedback(target.dataset.id, 'confirm');
      else if (action === 'viewer-page') goPage(Number(target.dataset.page));
    });

    content.addEventListener('input', (event) => {
      const input = event.target;
      if (!input.dataset.filter) return;
      if (input.dataset.filter === 'hw') {
        state.hwQuery = input.value.trim();
        updateHardwareRows();
      } else if (input.dataset.filter === 'part') {
        state.partQuery = input.value.trim();
        updatePartRows();
      } else if (input.dataset.filter === 'step') {
        state.stepQuery = input.value.trim();
        updateStepsGrid();
      }
    });

    content.addEventListener('click', (event) => {
      const toggle = event.target.closest('[data-toggle]');
      if (!toggle) return;
      const kind = toggle.dataset.toggle;
      if (kind === 'hw-diff') {
        state.hwDiffOnly = !state.hwDiffOnly;
        toggle.classList.toggle('active', state.hwDiffOnly);
        updateHardwareRows();
      } else if (kind === 'part-issue') {
        state.partIssueOnly = !state.partIssueOnly;
        toggle.classList.toggle('active', state.partIssueOnly);
        updatePartRows();
      } else if (kind === 'step-issue') {
        state.stepIssueOnly = !state.stepIssueOnly;
        toggle.classList.toggle('active', state.stepIssueOnly);
        updateStepsGrid();
      }
    });

    content.addEventListener('click', (event) => {
      const chip = event.target.closest('[data-issue-filter]');
      if (!chip) return;
      state.issueFilter = chip.dataset.issueFilter;
      renderIssues();
    });

    $('#viewerClose').addEventListener('click', closeViewer);
    $('#viewerPrev').addEventListener('click', () => goPage(state.viewer.page - 1));
    $('#viewerNext').addEventListener('click', () => goPage(state.viewer.page + 1));
    $('#viewerZoom').addEventListener('click', toggleZoom);
    viewer.addEventListener('click', (event) => {
      if (event.target === viewer) closeViewer();
    });
    viewerImg.addEventListener('load', () => {
      fitStage();
      if (state.viewer.zoomed) scrollToActiveBox();
    });
    window.addEventListener('resize', () => {
      if (!viewer.hidden) fitStage();
    });
    document.addEventListener('keydown', (event) => {
      if (event.key === 'Escape') {
        if (!viewer.hidden) closeViewer();
        else {
          const help = $('.help-modal');
          if (help) help.remove();
        }
      }
      if (viewer.hidden) return;
      if (event.key === 'ArrowLeft') goPage(state.viewer.page - 1);
      if (event.key === 'ArrowRight') goPage(state.viewer.page + 1);
    });
  }

  function validatePdfFile(file) {
    if (!file) return false;
    if (!/\.pdf$/i.test(file.name)) {
      showToast('请选择 PDF 文件。', 'error');
      return false;
    }
    if (file.size > MAX_PDF_BYTES) {
      showToast(`文件 ${formatBytes(file.size)} 超过 120 MB，请拆分后再试。`, 'error');
      return false;
    }
    return true;
  }

  function bindDropZone() {
    let dragDepth = 0;

    const hasFiles = (event) => {
      const transfer = event.dataTransfer;
      return Boolean(transfer && Array.from(transfer.types || []).includes('Files'));
    };

    window.addEventListener('dragenter', (event) => {
      if (!hasFiles(event)) return;
      event.preventDefault();
      dragDepth += 1;
      dropOverlay.hidden = false;
    });

    window.addEventListener('dragover', (event) => {
      if (!hasFiles(event)) return;
      event.preventDefault();
      if (event.dataTransfer) event.dataTransfer.dropEffect = 'copy';
      dropOverlay.hidden = false;
    });

    window.addEventListener('dragleave', () => {
      if (!dragDepth) return;
      dragDepth = Math.max(0, dragDepth - 1);
      if (!dragDepth) dropOverlay.hidden = true;
    });

    window.addEventListener('drop', (event) => {
      const files = event.dataTransfer && event.dataTransfer.files;
      if (!files || !files.length) return;
      event.preventDefault();
      dragDepth = 0;
      dropOverlay.hidden = true;
      if (files.length > 1) showToast('一次只读取一份图纸，已选择第一个 PDF。');
      analyzeFile(files[0]);
    });

    window.addEventListener('blur', () => {
      dragDepth = 0;
      dropOverlay.hidden = true;
    });
  }

  async function analyzeFile(file) {
    if (!validatePdfFile(file)) return;
    showLoading();
    try {
      const response = await fetch('/api/analyze', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/pdf',
          'X-Filename': encodeURIComponent(file.name),
        },
        body: file,
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.message || '解析失败。');
      setResult(data);
      showToast(`已完成 ${data.summary.step_count} 个步骤的核对。`, 'success');
    } catch (error) {
      showToast(error.message || '解析失败。', 'error');
      if (!state.result) renderEmpty();
    } finally {
      hideLoading();
    }
  }

  async function loadSample(sampleId = null) {
    const picker = $('#sampleSelect');
    const id = sampleId || (picker ? picker.value : 'lingkewu');
    showLoading();
    try {
      const response = await fetch(`/api/analyze?sample=${encodeURIComponent(id)}`, { method: 'POST' });
      const data = await response.json();
      if (!response.ok) throw new Error(data.message || '示例加载失败。');
      setResult(data);
      const templateName = data.detected && data.detected.template_name ? ` · ${data.detected.template_name}` : '';
      showToast(`已打开示例图纸 ${data.file.name}${templateName}。`, 'success');
    } catch (error) {
      showToast(error.message || '示例加载失败。', 'error');
      if (!state.result) renderEmpty();
    } finally {
      hideLoading();
    }
  }

  function setResult(data, options = {}) {
    state.result = data;
    state.bomMap = {};
    state.partMap = {};
    state.stepMap = {};
    state.usageByCode = {};
    state.partRefs = {};
    state.issuesByStep = {};
    state.issuesByPage = {};
    for (const item of data.bom) state.bomMap[item.code] = item;
    for (const item of data.parts) state.partMap[item.no] = item;
    for (const step of data.steps) state.stepMap[step.no] = step;
    for (const step of data.steps) {
      for (const entry of step.hardware) {
        if (!state.usageByCode[entry.code]) state.usageByCode[entry.code] = [];
        state.usageByCode[entry.code].push({
          code: entry.code,
          qty: entry.qty,
          box: entry.box,
          step: step.no,
          page: step.page,
          repeat: step.repeat,
        });
      }
      for (const no of step.panel_nos) {
        if (!state.partRefs[no]) state.partRefs[no] = [];
        state.partRefs[no].push(step);
      }
    }
    for (const issue of data.issues) {
      if (issue.step) {
        if (!state.issuesByStep[issue.step]) state.issuesByStep[issue.step] = [];
        state.issuesByStep[issue.step].push(issue);
      }
      if (issue.page) {
        if (!state.issuesByPage[issue.page]) state.issuesByPage[issue.page] = [];
        state.issuesByPage[issue.page].push(issue);
      }
    }
    if (!options.preserveView) {
      state.hwQuery = '';
      state.hwDiffOnly = false;
      state.partQuery = '';
      state.partIssueOnly = false;
      state.stepQuery = '';
      state.stepIssueOnly = false;
      state.issueFilter = 'all';
    }
    btnExport.disabled = false;
    renderFileCard();
    renderNav();
    setView(options.preserveView ? state.view : 'overview');
    loadKnowledgeStats();
  }

  function renderFileCard() {
    const data = state.result;
    if (!data) {
      fileCard.className = 'file-card';
      fileCard.innerHTML = `<div class="file-card-empty">
        <div class="file-icon"><svg viewBox="0 0 24 24" fill="none"><path d="M6 2.8h7l5 5V21a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1V3.8a1 1 0 0 1 1-1Z" stroke="currentColor" stroke-width="1.4"/><path d="M13 2.8v5h5" stroke="currentColor" stroke-width="1.4"/></svg></div>
        <div><div class="file-title">未打开图纸</div><div class="file-meta">支持 领客屋 / 遨森 / 英驰 三类模板</div></div>
      </div>`;
      return;
    }
    fileCard.className = 'file-card has-file';
    fileCard.innerHTML = `<div class="file-card-empty">
      <div class="file-icon"><svg viewBox="0 0 24 24" fill="none"><path d="M6 2.8h7l5 5V21a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1V3.8a1 1 0 0 1 1-1Z" stroke="currentColor" stroke-width="1.4"/><path d="M13 2.8v5h5" stroke="currentColor" stroke-width="1.4"/></svg></div>
      <div style="min-width:0"><div class="file-title" title="${esc(data.file.name)}">${esc(data.file.name)}</div><div class="file-meta">${data.file.pages} 页 · ${formatBytes(data.file.size)} · ${esc((data.detected && data.detected.template_name) || '已识别模板')}</div></div>
    </div>`;
  }

  function renderNav() {
    const data = state.result;
    if (!data) {
      $('#navHwCount').textContent = '—';
      $('#navPartCount').textContent = '—';
      $('#navStepCount').textContent = '—';
      $('#navIssueCount').textContent = '—';
      return;
    }
    $('#navHwCount').textContent = data.bom.length;
    $('#navPartCount').textContent = data.parts.length;
    $('#navStepCount').textContent = data.steps.length;
    const issueCount = data.issues.length;
    $('#navIssueCount').textContent = issueCount;
    $('#navIssueCount').style.color = data.summary.errors ? '#ff9a9d' : '';
  }

  function setView(view) {
    if (!state.result) return;
    state.view = view;
    for (const button of $$('#nav [data-view]')) {
      button.classList.toggle('active', button.dataset.view === view);
    }
    const names = {
      overview: '校验总览',
      hardware: '五金核对',
      parts: '板件核对',
      steps: '步骤明细',
      issues: '问题清单',
    };
    crumb.textContent = names[view] || '校验总览';
    topbarSub.textContent = `${state.result.file.name} · ${(state.result.detected && state.result.detected.template_name) || '已识别模板'} · 第 ${state.result.file.pages} 页`;
    window.scrollTo({ top: 0, behavior: 'auto' });
    if (view === 'overview') renderOverview();
    else if (view === 'hardware') renderHardware();
    else if (view === 'parts') renderParts();
    else if (view === 'steps') renderSteps();
    else if (view === 'issues') renderIssues();
  }

  function renderEmpty() {
    content.innerHTML = `<section class="empty">
      <div>
        <div class="eyebrow">安装说明书自动校验</div>
        <h1>整套图纸，<em>一次核对</em>完成。</h1>
        <p>打开整套安装图纸，自动汇总每个步骤的五金用量，与清单逐项比对；同时检查板件编号是否错标、漏标，以及重复模块的用量是否矛盾。支持把 PDF 直接拖入窗口，所有结果都能定位回图纸页面。</p>
        <div class="empty-actions">
          <button class="btn btn-primary" data-action="pick-file">
            <svg viewBox="0 0 24 24" fill="none"><path d="M3.5 6.5h6l2 2h9v10a1.5 1.5 0 0 1-1.5 1.5h-14A1.5 1.5 0 0 1 3.5 18V6.5Z" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"/><path d="M3.5 9h17" stroke="currentColor" stroke-width="1.6"/></svg>
            打开 PDF 图纸
          </button>
          <div class="sample-picker">
            <select id="sampleSelect" class="sample-select" aria-label="选择示例模板">
              <option value="lingkewu">领客屋模板</option>
              <option value="aosom">遨森模板</option>
              <option value="yingchi">英驰模板</option>
            </select>
            <button class="btn btn-ghost" data-action="sample">用示例图纸体验</button>
          </div>
        </div>
        <div class="feature-row">
          <span class="feature">${ICONS.check} 五金数量差异</span>
          <span class="feature">${ICONS.check} 板件编号错标</span>
          <span class="feature">${ICONS.check} 步骤用量表核对</span>
          <span class="feature">${ICONS.check} 定位到图纸页</span>
        </div>
        <div class="empty-note">${ICONS.shield} 本地离线运行 · 可直接拖入 PDF · 支持 120 MB PDF</div>
      </div>
      <div class="empty-art">
        <div class="float-chip"><span>A</span><b>×66</b></div>
        <div class="float-chip green"><span>五金</span><b>410 / 410</b></div>
        <div class="float-chip"><span>步骤 12</span><b>13</b></div>
        <svg class="cabinet-illustration" viewBox="0 0 440 400" fill="none" aria-hidden="true">
          <defs>
            <linearGradient id="front" x1="80" y1="60" x2="330" y2="350" gradientUnits="userSpaceOnUse">
              <stop stop-color="#ffffff"/><stop offset="1" stop-color="#f1f6fd"/>
            </linearGradient>
          </defs>
          <ellipse cx="220" cy="362" rx="150" ry="14" fill="#dbe6f3" opacity=".7"/>
          <path d="M82 66 140 28l246 30-58 36H82Z" fill="#f9fbff" stroke="#c7d5e7" stroke-width="1.4"/>
          <path d="M82 66h246v280H82z" fill="url(#front)" stroke="#c7d5e7" stroke-width="1.4"/>
          <path d="M328 66l58-36v280l-58 36V66Z" fill="#f3f8fe" stroke="#c7d5e7" stroke-width="1.4"/>
          <g stroke="#d3dfed">
            <rect x="96" y="82" width="106" height="54" rx="2" fill="#fff"/>
            <rect x="234" y="82" width="106" height="54" rx="2" fill="#fff"/>
            <rect x="96" y="148" width="106" height="54" rx="2" fill="#fff"/>
            <rect x="234" y="148" width="106" height="54" rx="2" fill="#fff"/>
            <rect x="96" y="214" width="106" height="54" rx="2" fill="#fff"/>
            <rect x="234" y="214" width="106" height="54" rx="2" fill="#fff"/>
            <rect x="96" y="280" width="106" height="54" rx="2" fill="#fff"/>
            <rect x="234" y="280" width="106" height="54" rx="2" fill="#fff"/>
          </g>
          <g stroke="#b9c9dc" stroke-width="1.4" stroke-linecap="round">
            <path d="M122 88v42M176 88v42M260 88v42M314 88v42"/>
            <path d="M122 154v42M176 154v42M260 154v42M314 154v42"/>
            <path d="M122 220v42M176 220v42M260 220v42M314 220v42"/>
            <path d="M122 286v42M176 286v42M260 286v42M314 286v42"/>
          </g>
          <path d="M60 66v280M54 66h12M54 346h12" stroke="#aebfd4" stroke-width="1.2"/>
          <path d="M82 48h246M82 42v12M328 42v12" stroke="#aebfd4" stroke-width="1.2"/>
          <text x="44" y="210" fill="#8fa3ba" font-size="10" font-family="Arial" transform="rotate(-90 44 210)">800</text>
          <text x="196" y="40" fill="#8fa3ba" font-size="10" font-family="Arial">560</text>
          <circle cx="352" cy="332" r="30" fill="#0a9b6c"/>
          <path d="m338 332 9 9 18-19" stroke="#fff" stroke-width="4" stroke-linecap="round" stroke-linejoin="round"/>
        </svg>
      </div>
    </section>`;
  }

  function renderOverview() {
    const data = state.result;
    if (!data) return renderEmpty();
    const s = data.summary;
    const status = s.status;
    let title;
    let desc;
    let ringNum;
    let ringLabel;
    let ringClass;
    let meta;
    if (status === 'fail') {
      title = `发现 ${s.errors} 处硬性错误`;
      desc = `已完成 ${s.step_count} 个步骤、${s.hardware_kinds} 类五金与 ${s.part_kinds} 类板件的交叉核对。红色项目需要修正后重新导出。`;
      ringNum = s.errors;
      ringLabel = '硬性错误';
      ringClass = 'fail';
      meta = `另有 ${s.warnings} 待确认 · ${s.infos} 提示`;
    } else if (status === 'review') {
      title = `有 ${s.warnings} 处需要确认`;
      desc = `硬性规则全部通过，但有 ${s.warnings} 项需要人工复核。下方列出了具体步骤与图纸位置。`;
      ringNum = s.warnings;
      ringLabel = '待确认';
      ringClass = 'review';
      meta = `硬性错误 0 · 提示 ${s.infos}`;
    } else {
      title = '整套图纸校验通过';
      desc = `${s.hardware_kinds} 类五金用量 ${s.hardware_units} 件与清单完全一致；${s.part_kinds} 类板件编号均有有效引用。${s.infos ? `${s.infos} 条提示供复核。` : '未发现需要修正的问题。'}`;
      ringNum = 0;
      ringLabel = '硬性错误';
      ringClass = 'pass';
      meta = s.infos ? `${s.infos} 条提示` : '全部规则通过';
    }

    const checkHtml = data.checks.map((check) => `<div class="check-row">
      <div class="check-icon ${check.status}">${check.status === 'pass' ? ICONS.check : check.status === 'warn' ? ICONS.alert : ICONS.x}</div>
      <div style="min-width:0"><div class="check-name">${esc(check.name)}</div><div class="check-summary">${esc(check.summary)}</div></div>
      <span class="check-tag ${check.status}">${CHECK_TEXT[check.status] || check.status}</span>
    </div>`).join('');

    const maxQty = Math.max(...data.bom.map((item) => item.qty), 1);
    const tiles = data.bom.map((item) => {
      const used = usageTotal(item.code);
      const diff = used - item.qty;
      const pct = Math.max(0, Math.min(100, (used / Math.max(item.qty, 1)) * 100));
      return `<button class="hw-tile ${diff === 0 ? '' : 'bad'}" data-action="open-hardware" data-code="${esc(item.code)}" title="五金 ${esc(item.code)}：${used} / ${item.qty}">
        <div class="hw-tile-top"><span class="hw-code">${esc(item.code)}</span><span class="hw-qty"><b>${used}</b>/${item.qty}</span></div>
        <div class="hw-bar"><i style="width:${pct}%;background:${diff === 0 ? 'var(--pass)' : 'var(--error)'}"></i></div>
      </button>`;
    }).join('');

    const issuePreview = data.issues.length
      ? data.issues.slice(0, 3).map((issue) => `<div class="issue-mini" data-action="open-issue" data-id="${esc(issue.id)}">
          <span class="sev-dot ${issue.severity}"></span>
          <div style="min-width:0"><div class="issue-mini-title">${esc(issue.title)}</div><div class="issue-mini-meta">${issueLocation(issue)}</div></div>
        </div>`).join('')
      : `<div class="empty-inline"><div class="big-check">${ICONS.check}</div><b>没有发现问题</b><span>所有硬性规则与清单覆盖检查均通过。</span></div>`;

    const pageMap = data.pages.map((page) => {
      const label = page.steps.length ? `S${page.steps.join(',')}` : (ROLE_TEXT[page.role] || '其他');
      return `<button class="page-chip role-${page.role}" data-action="open-page" data-page="${page.index}" title="PDF 第 ${page.index} 页${page.label ? ` · 图册 ${page.label}` : ''}">
        <b>${page.index}</b><span>${esc(label)}</span>
      </button>`;
    }).join('');

    const kb = data.knowledge || {};
    const kbTiming = kb.enabled
      ? kb.cache_hit
        ? ` · 知识库缓存命中 ${kb.elapsed_ms} ms`
        : ` · 首次分析 ${kb.elapsed_ms} ms`
      : '';
    const kbDismissed = kb.dismissed ? ` · 已应用 ${kb.dismissed} 条误报记忆` : '';

    content.innerHTML = `<section class="view">
      <div class="hero ${ringClass}">
        <div>
          <div class="hero-eyebrow"><span class="status-dot"></span>校验完成 · ${esc(data.analyzed_at)}${kbTiming}${kbDismissed}</div>
          <h1>${esc(title)}</h1>
          <p>${esc(desc)}</p>
          <div class="hero-actions">
            <button class="btn btn-primary" data-action="export">导出 Excel 报告</button>
            <button class="btn btn-ghost" data-action="go-view" data-view="issues">查看${data.issues.length ? ` ${data.issues.length} 条结果` : '问题清单'}</button>
            <button class="btn btn-ghost" data-action="go-view" data-view="steps">浏览 ${data.steps.length} 个步骤</button>
          </div>
        </div>
        <div class="hero-score">
          <div class="score-ring ${ringClass}"><span class="score-num">${ringNum}</span><span class="score-label">${ringLabel}</span></div>
          <div class="score-meta">${esc(meta)}</div>
        </div>
      </div>

      <div class="stat-grid">
        ${statCard('五金种类', s.hardware_kinds, '类', `${s.hardware_units} 件安装用量 · ${s.spare_units} 件备用`)}
        ${statCard('板件种类', s.part_kinds, '类', `${s.part_units} 件总数量 · ${s.part_kinds === 0 ? '无' : '均有引用'}`)}
        ${statCard('安装步骤', s.step_count, '步', `${s.table_count} 张用量表 · ${s.repeat_steps} 个重复模块`)}
        ${statCard('硬性错误', s.errors, '处', s.errors ? `${s.warnings} 待确认 · ${s.infos} 提示` : `通过 · ${s.infos} 条提示`)}
      </div>

      <div class="overview-grid">
        <div class="card">
          <div class="card-head"><div><h2>核对结果</h2><div class="sub">五金、板件、步骤与识别质量的 ${data.checks.length} 项检查</div></div><button class="card-link" data-action="go-view" data-view="issues">问题清单 →</button></div>
          <div class="check-list">${checkHtml}</div>
        </div>
        <div class="card">
          <div class="card-head"><div><h2>五金一致性</h2><div class="sub">安装用量 / 清单需求 · 点击查看使用位置</div></div><button class="card-link" data-action="go-view" data-view="hardware">五金明细 →</button></div>
          <div class="hw-grid">${tiles}</div>
        </div>
      </div>

      <div class="overview-grid">
        <div class="card">
          <div class="card-head"><div><h2>需要关注</h2><div class="sub">按严重程度排序，点击定位到图纸</div></div><button class="card-link" data-action="go-view" data-view="issues">全部结果 →</button></div>
          ${issuePreview}
        </div>
        <div class="card">
          <div class="card-head"><div><h2>页面地图</h2><div class="sub">${data.file.pages} 页 · ${esc((data.detected && data.detected.template_name) || "已识别模板")} · 五金清单 P${data.detected.bom_page} · 板件清单 P${data.detected.parts_page}</div></div><button class="card-link" data-action="open-page" data-page="${data.detected.bom_page}">打开清单 →</button></div>
          <div class="page-map">${pageMap}</div>
        </div>
      </div>
    </section>`;
  }

  function statCard(label, value, unit, sub) {
    return `<div class="stat"><div class="stat-label">${esc(label)}</div><div class="stat-value">${value}<small>${esc(unit)}</small></div><div class="stat-sub">${esc(sub)}</div></div>`;
  }

  function usageTotal(code) {
    return (state.usageByCode[code] || []).reduce((sum, entry) => sum + entry.qty, 0);
  }

  function issueLocation(issue) {
    const page = state.result.pages.find((item) => item.index === issue.page);
    const label = page && page.label ? ` · 图册 ${page.label}` : '';
    if (issue.step) return `步骤 ${issue.step} · PDF 第 ${issue.page || '—'} 页${label}`;
    if (issue.page) return `PDF 第 ${issue.page} 页${label}`;
    return '全文档';
  }

  function renderHardware() {
    const data = state.result;
    const totalUsed = data.bom.reduce((sum, item) => sum + usageTotal(item.code), 0);
    const diffCount = data.bom.filter((item) => usageTotal(item.code) !== item.qty).length;
    content.innerHTML = `<section class="view">
      <div class="view-head">
        <div><h1>五金核对</h1><p>逐项比较“步骤用量表合计”与“五金清单需求”，备用数量单独展示。</p></div>
        <div class="view-head-actions"><button class="btn btn-ghost" data-action="export">导出 Excel</button></div>
      </div>
      <div class="summary-strip">
        <span class="summary-item"><b>${data.bom.length}</b> 类五金</span>
        <span class="summary-item ${diffCount === 0 ? 'good' : 'bad'}"><b>${totalUsed}</b> / ${data.summary.bom_units} 件用量</span>
        <span class="summary-item ${diffCount === 0 ? 'good' : 'bad'}"><b>${diffCount}</b> 类差异</span>
        <span class="summary-item"><b>${data.summary.spare_units}</b> 件备用</span>
        <span class="summary-item"><b>${data.summary.table_count}</b> 张用量表</span>
      </div>
      <div class="toolbar">
        <div class="search-box">${ICONS.search}<input data-filter="hw" id="hwSearch" placeholder="搜索编号，如 A / CR" value="${esc(state.hwQuery)}"></div>
        <button class="filter-toggle ${state.hwDiffOnly ? 'active' : ''}" data-toggle="hw-diff">仅看差异</button>
      </div>
      <div class="table-wrap">
        <table class="data">
          <thead><tr><th></th><th>编号</th><th>清单需求</th><th>备用</th><th>供应合计</th><th>步骤合计</th><th>差额</th><th>使用步骤</th><th>状态</th></tr></thead>
          <tbody id="hwBody"></tbody>
        </table>
      </div>
    </section>`;
    updateHardwareRows();
  }

  function updateHardwareRows() {
    const data = state.result;
    if (!data) return;
    const body = $('#hwBody');
    if (!body) return;
    const query = state.hwQuery.toUpperCase();
    const rows = data.bom.filter((item) => {
      if (query && !item.code.includes(query)) return false;
      if (state.hwDiffOnly && usageTotal(item.code) === item.qty) return false;
      return true;
    });
    if (!rows.length) {
      body.innerHTML = `<tr><td colspan="9" class="empty-row">没有符合条件的五金编号。</td></tr>`;
      return;
    }
    body.innerHTML = rows.map((item) => {
      const usage = (state.usageByCode[item.code] || []).slice().sort((a, b) => a.step - b.step);
      const used = usage.reduce((sum, entry) => sum + entry.qty, 0);
      const diff = used - item.qty;
      const diffClass = diff === 0 ? 'diff-zero' : diff < 0 ? 'diff-neg' : 'diff-pos';
      const statusClass = diff === 0 ? 'pass' : 'fail';
      const statusText = diff === 0 ? '一致' : `差 ${diff > 0 ? '+' : ''}${diff}`;
      const usageHtml = usage.length
        ? usage.map((entry) => `<span class="chip step" title="步骤 ${entry.step}${entry.repeat > 1 ? ` ×${entry.repeat}` : ''}：${entry.qty} 件">S${entry.step} · ${entry.qty}</span>`).join('')
        : '<span class="chip">未使用</span>';
      return `<tr data-action="open-hardware" data-code="${esc(item.code)}">
        <td><img class="thumb" src="${cropUrl(item.page, item.box, 120)}" loading="lazy" alt="五金 ${esc(item.code)}"></td>
        <td><span class="code-badge">${esc(item.code)}</span></td>
        <td class="num">${item.qty}</td>
        <td class="num">${item.spare || '—'}</td>
        <td class="num">${item.qty + item.spare}</td>
        <td class="num"><b>${used}</b></td>
        <td class="num ${diffClass}">${diff === 0 ? '0' : `${diff > 0 ? '+' : ''}${diff}`}</td>
        <td class="steps-cell">${usageHtml}</td>
        <td><span class="pill ${statusClass}">${statusText}</span></td>
      </tr>`;
    }).join('');
  }

  function partStatus(item) {
    const refs = state.partRefs[item.no] || [];
    if (!refs.length) return { status: 'warn', text: '未引用', note: '安装步骤中未出现' };
    let maxCount = 0;
    let maxStep = 0;
    for (const step of refs) {
      const count = step.panels.filter((panel) => Number(panel.no) === item.no).length;
      if (count > maxCount) {
        maxCount = count;
        maxStep = step.no;
      }
    }
    const repeat = Math.max(1, ...refs.map((step) => step.repeat));
    const perUnit = repeat > 1 ? item.qty / repeat : item.qty;
    if (maxCount > perUnit) {
      return { status: 'warn', text: '待确认', note: `步骤 ${maxStep} 同图 ${maxCount} 次` };
    }
    return { status: 'pass', text: '正常', note: '编号与用量相容' };
  }

  function renderParts() {
    const data = state.result;
    const referenced = Object.keys(state.partRefs).length;
    const unknown = new Set(data.issues.filter((issue) => issue.code === 'PART_UNKNOWN').map((issue) => issue.title.match(/\d+/g)?.slice(-1)[0])).size;
    content.innerHTML = `<section class="view">
      <div class="view-head">
        <div><h1>板件核对</h1><p>检查步骤与总览中的板件编号是否有效，重复步骤的每套用量是否与清单数量矛盾。</p></div>
        <div class="view-head-actions"><button class="btn btn-ghost" data-action="export">导出 Excel</button></div>
      </div>
      <div class="summary-strip">
        <span class="summary-item"><b>${data.parts.length}</b> 类板件</span>
        <span class="summary-item"><b>${data.summary.part_units}</b> 件总数量</span>
        <span class="summary-item ${referenced === data.parts.length ? 'good' : 'bad'}"><b>${referenced}</b> 类被步骤引用</span>
        <span class="summary-item ${unknown ? 'bad' : 'good'}"><b>${unknown}</b> 个未知编号</span>
        <span class="summary-item"><b>${data.summary.repeat_steps}</b> 个重复模块</span>
      </div>
      <div class="toolbar">
        <div class="search-box">${ICONS.search}<input data-filter="part" id="partSearch" placeholder="搜索板件编号，如 13" value="${esc(state.partQuery)}"></div>
        <button class="filter-toggle ${state.partIssueOnly ? 'active' : ''}" data-toggle="part-issue">仅看异常</button>
      </div>
      <div class="table-wrap">
        <table class="data">
          <thead><tr><th></th><th>编号</th><th>清单数量</th><th>引用步骤</th><th>首次出现</th><th>同图最多标注</th><th>总览</th><th>状态</th></tr></thead>
          <tbody id="partBody"></tbody>
        </table>
      </div>
    </section>`;
    updatePartRows();
  }

  function updatePartRows() {
    const data = state.result;
    if (!data) return;
    const body = $('#partBody');
    if (!body) return;
    const query = state.partQuery.trim();
    const rows = data.parts.filter((item) => {
      const status = partStatus(item);
      if (query && !String(item.no).includes(query)) return false;
      if (state.partIssueOnly && status.status === 'pass') return false;
      return true;
    });
    if (!rows.length) {
      body.innerHTML = `<tr><td colspan="8" class="empty-row">没有符合条件的板件编号。</td></tr>`;
      return;
    }
    body.innerHTML = rows.map((item) => {
      const refs = (state.partRefs[item.no] || []).slice().sort((a, b) => a.no - b.no);
      const status = partStatus(item);
      let maxCount = 0;
      let maxStep = 0;
      for (const step of refs) {
        const count = step.panels.filter((panel) => Number(panel.no) === item.no).length;
        if (count > maxCount) {
          maxCount = count;
          maxStep = step.no;
        }
      }
      const refsHtml = refs.length
        ? refs.map((step) => `<span class="chip step">S${step.no}${step.repeat > 1 ? `×${step.repeat}` : ''}</span>`).join('')
        : '<span class="chip">—</span>';
      const inOverview = data.overview.includes(item.no);
      return `<tr data-action="open-part" data-no="${item.no}">
        <td><img class="thumb wide" src="${cropUrl(item.page, item.box, 180)}" loading="lazy" alt="板件 ${item.no}"></td>
        <td><span class="code-badge part">${item.no}</span></td>
        <td class="num">${item.qty}</td>
        <td class="steps-cell">${refsHtml}</td>
        <td class="num">${refs.length ? `S${refs[0].no}` : '—'}</td>
        <td class="num">${maxCount ? `${maxCount}${maxStep ? ` · S${maxStep}` : ''}` : '—'}</td>
        <td>${inOverview ? '<span class="pill pass">有</span>' : '<span class="pill warn">无</span>'}</td>
        <td><span class="pill ${status.status}">${status.text}</span> <span style="color:var(--muted);font-size:9.5px">${esc(status.note)}</span></td>
      </tr>`;
    }).join('');
  }

  function renderSteps() {
    const data = state.result;
    const issueSteps = Object.keys(state.issuesByStep).length;
    content.innerHTML = `<section class="view">
      <div class="view-head">
        <div><h1>步骤明细</h1><p>每一步的板件编号与五金用量表，按重复倍数换算每套用量。点击卡片打开图纸定位。</p></div>
        <div class="view-head-actions"><button class="btn btn-ghost" data-action="export">导出 Excel</button></div>
      </div>
      <div class="summary-strip">
        <span class="summary-item"><b>${data.steps.length}</b> 个步骤</span>
        <span class="summary-item"><b>${data.summary.table_count}</b> 张五金用量表</span>
        <span class="summary-item"><b>${data.summary.repeat_steps}</b> 个重复模块</span>
        <span class="summary-item ${issueSteps ? 'bad' : 'good'}"><b>${issueSteps}</b> 个步骤有待确认项</span>
      </div>
      <div class="toolbar">
        <div class="search-box">${ICONS.search}<input data-filter="step" id="stepSearch" placeholder="搜索步骤 / 板件 / 五金" value="${esc(state.stepQuery)}"></div>
        <button class="filter-toggle ${state.stepIssueOnly ? 'active' : ''}" data-toggle="step-issue">仅看有问题</button>
      </div>
      <div class="steps-grid" id="stepsGrid"></div>
    </section>`;
    updateStepsGrid();
  }

  function updateStepsGrid() {
    const data = state.result;
    if (!data) return;
    const grid = $('#stepsGrid');
    if (!grid) return;
    const query = state.stepQuery.trim().toUpperCase();
    const steps = data.steps.filter((step) => {
      const issues = state.issuesByStep[step.no] || [];
      if (state.stepIssueOnly && !issues.length) return false;
      if (!query) return true;
      if (String(step.no).includes(query)) return true;
      if (step.panel_nos.some((no) => String(no).includes(query))) return true;
      if (step.hardware.some((entry) => entry.code.includes(query))) return true;
      return false;
    });
    if (!steps.length) {
      grid.innerHTML = `<div class="empty-row" style="grid-column:1/-1">没有符合条件的步骤。</div>`;
      return;
    }
    grid.innerHTML = steps.map((step) => {
      const issues = state.issuesByStep[step.no] || [];
      const hasError = issues.some((issue) => issue.severity === 'error');
      const panelChips = step.panel_nos.length
        ? step.panel_nos.map((no) => `<span class="chip panel">${no}</span>`).join('')
        : '<span class="chip">未标注</span>';
      const hardwareHtml = step.hardware.length
        ? step.hardware.map((entry) => {
            const per = step.repeat > 1 ? Number((entry.qty / step.repeat).toFixed(2)) : null;
            return `<span class="hw-amount">${esc(entry.code)}<b>×${entry.qty}</b>${per !== null ? `<em>每套 ${per}</em>` : ''}</span>`;
          }).join('')
        : '<span style="color:var(--muted);font-size:9.5px">纯拼装 / 检查步骤</span>';
      const footRight = issues.length
        ? `<span>${issues.some((issue) => issue.severity === 'error') ? '错误' : '待确认'} ${issues.length} 项</span>`
        : '<span style="color:var(--pass)">无异常</span>';
      return `<div class="step-card ${issues.length ? 'has-issue' : ''} ${hasError ? 'has-error' : ''}" data-action="open-step" data-no="${step.no}">
        <div class="step-head">
          <div class="step-no">${String(step.no).padStart(2, '0')}<small>STEP</small></div>
          <div class="step-page">P${step.page}${step.page_label ? ` · ${esc(step.page_label)}` : ''}</div>
          ${step.repeat > 1 ? `<span class="repeat-badge">×${step.repeat} 重复</span>` : ''}
        </div>
        <div class="step-body">
          <div class="step-line"><span class="step-line-label">板件</span><div class="step-line-value">${panelChips}</div></div>
          <div class="step-line"><span class="step-line-label">五金</span><div class="step-line-value hw-line">${hardwareHtml}</div></div>
        </div>
        <div class="step-foot"><span>${step.panel_nos.length} 种板件 · ${step.hardware.length} 种五金</span>${footRight}</div>
      </div>`;
    }).join('');
  }

  function renderIssues() {
    const data = state.result;
    const counts = {
      all: data.issues.length,
      error: data.issues.filter((issue) => issue.severity === 'error').length,
      warn: data.issues.filter((issue) => issue.severity === 'warn').length,
      info: data.issues.filter((issue) => issue.severity === 'info').length,
    };
    const filtered = state.issueFilter === 'all'
      ? data.issues
      : data.issues.filter((issue) => issue.severity === state.issueFilter);

    const filters = [
      ['all', '全部'],
      ['error', '错误'],
      ['warn', '待确认'],
      ['info', '提示'],
    ].map(([key, label]) => `<button class="filter-chip ${state.issueFilter === key ? 'active' : ''}" data-issue-filter="${key}">${label}<span class="n">${counts[key]}</span></button>`).join('');

    const cards = filtered.map((issue) => {
      const firstBox = issue.boxes && issue.boxes[0];
      const thumb = firstBox
        ? `<img class="issue-thumb" src="${cropUrl(firstBox.page, firstBox.box, 460)}" loading="lazy" alt="">`
        : `<div class="issue-thumb" style="display:grid;place-items:center;color:var(--muted);font-size:10px">无图形定位</div>`;
      const knowledge = issue.knowledge || {};
      const knowledgeChip = knowledge.decision === 'confirm'
        ? '<span class="kb-chip">已确认问题</span>'
        : knowledge.decision === 'dismiss'
          ? '<span class="kb-chip dismiss">已记为误报</span>'
          : '';
      return `<article class="issue-card ${issue.severity}">
        <div>
          <div class="issue-head"><span class="pill ${issue.severity}">${SEV_TEXT[issue.severity] || issue.severity}</span><h3 class="issue-title">${esc(issue.title)}</h3></div>
          <p class="issue-detail">${esc(issue.detail)}</p>
          <div class="issue-suggest"><b>建议：</b>${esc(issue.suggestion)}</div>
          <div class="issue-meta"><span>${esc(issueLocation(issue))}</span><span>·</span><span>规则 ${esc(issue.code)}</span>${knowledgeChip}</div>
          <div class="issue-actions">
            <button class="feedback-btn confirm" data-action="confirm-issue" data-id="${esc(issue.id)}">确认为问题</button>
            <button class="feedback-btn dismiss" data-action="dismiss-issue" data-id="${esc(issue.id)}">标记误报并记住</button>
          </div>
        </div>
        <div class="issue-thumb-wrap">
          ${thumb}
          <button class="btn btn-soft" data-action="open-issue" data-id="${esc(issue.id)}">定位图纸</button>
        </div>
      </article>`;
    }).join('');

    const empty = data.issues.length === 0
      ? `<div class="success-panel"><div class="big-check">${ICONS.check}</div><div><h3>没有发现需要修正的问题</h3><p>${data.summary.hardware_kinds} 类五金用量与清单一致，${data.summary.part_kinds} 类板件编号均有效。</p></div></div>`
      : `<div class="empty-row" style="border:1px dashed var(--line);border-radius:12px;margin-top:12px">当前筛选条件下没有结果。</div>`;

    content.innerHTML = `<section class="view">
      <div class="view-head">
        <div><h1>问题清单</h1><p>按严重程度排序。错误需要修正；待确认需要人工判断；提示用于提醒可能的漏标或多视图。</p></div>
        <div class="view-head-actions"><button class="btn btn-ghost" data-action="export">导出 Excel</button></div>
      </div>
      <div class="summary-strip">
        <span class="summary-item ${counts.error ? 'bad' : 'good'}"><b>${counts.error}</b> 错误</span>
        <span class="summary-item ${counts.warn ? 'bad' : 'good'}"><b>${counts.warn}</b> 待确认</span>
        <span class="summary-item"><b>${counts.info}</b> 提示</span>
        <span class="summary-item"><b>${counts.all}</b> 条结果</span>
      </div>
      <div class="filter-chips">${filters}</div>
      <div class="issue-list">${cards || empty}</div>
    </section>`;
  }

  function cropUrl(page, box, width) {
    const data = state.result;
    if (!data || !box) return '';
    const [x0, y0, x1, y1] = box;
    return `/api/crop?id=${encodeURIComponent(data.id)}&page=${page}&x0=${x0.toFixed(2)}&y0=${y0.toFixed(2)}&x1=${x1.toFixed(2)}&y1=${y1.toFixed(2)}&w=${width || 420}`;
  }

  function pageUrl(page) {
    const data = state.result;
    return `/api/page?id=${encodeURIComponent(data.id)}&page=${page}&dpi=160`;
  }

  function pageInfo(page) {
    return state.result.pages.find((item) => item.index === page);
  }

  function openViewer(options) {
    const page = Number(options.page) || 1;
    const info = pageInfo(page);
    if (!info) return;
    state.viewer = {
      page,
      boxes: options.boxes || [],
      title: options.title || '图纸定位',
      sideHtml: options.sideHtml || '',
      severity: options.severity || 'info',
      zoomed: false,
    };
    viewerTitle.textContent = state.viewer.title;
    viewerKicker.textContent = `图纸定位 · PDF 第 ${page} 页${info.label ? ` · 图册 ${info.label}` : ''}`;
    viewerSide.innerHTML = state.viewer.sideHtml;
    viewer.hidden = false;
    document.body.style.overflow = 'hidden';
    renderViewerPage(page);
  }

  function closeViewer() {
    viewer.hidden = true;
    state.viewer.zoomed = false;
    document.body.style.overflow = '';
  }

  function goPage(page) {
    const total = state.result ? state.result.file.pages : 0;
    const next = Math.max(1, Math.min(total, page));
    if (next === state.viewer.page) return;
    state.viewer.page = next;
    state.viewer.zoomed = false;
    pageStage.classList.remove('zoomed');
    renderViewerPage(next);
  }

  function renderViewerPage(page) {
    const info = pageInfo(page);
    if (!info) return;
    pageStage.classList.remove('zoomed');
    pageStage.style.width = '';
    pageStage.style.aspectRatio = `${info.width} / ${info.height}`;
    viewerImg.src = pageUrl(page);
    viewerBoxes.innerHTML = boxesForPage(page);
    viewerKicker.textContent = `图纸定位 · PDF 第 ${page} 页${info.label ? ` · 图册 ${info.label}` : ''}`;
    renderViewerPageStrip(page);
    requestAnimationFrame(() => {
      fitStage();
      if (state.viewer.zoomed) scrollToActiveBox();
    });
  }

  function boxesForPage(page) {
    const info = pageInfo(page);
    if (!info) return '';
    const boxes = state.viewer.boxes.filter((box) => Number(box.page) === page);
    return boxes.map((box) => {
      const index = state.viewer.boxes.indexOf(box);
      const [x0, y0, x1, y1] = box.box;
      const left = (x0 / info.width) * 100;
      const top = (y0 / info.height) * 100;
      const width = Math.max(0.3, ((x1 - x0) / info.width) * 100);
      const height = Math.max(0.3, ((y1 - y0) / info.height) * 100);
      const kind = box.kind || state.viewer.severity;
      const label = box.label ? `<span class="hl-label">${esc(box.label)}</span>` : '';
      return `<div class="hl ${kind}" data-box-index="${index}" style="left:${left}%;top:${top}%;width:${width}%;height:${height}%">${label}</div>`;
    }).join('');
  }

  function renderViewerPageStrip(active) {
    const data = state.result;
    viewerPages.innerHTML = data.pages.map((page) => {
      const roleClass = page.role ? `role-${page.role}` : '';
      return `<button class="${roleClass} ${page.index === active ? 'active' : ''}" data-action="viewer-page" data-page="${page.index}" title="PDF 第 ${page.index} 页${page.label ? ` · 图册 ${page.label}` : ''}">${page.index}</button>`;
    }).join('');
    const activeButton = viewerPages.querySelector('button.active');
    if (activeButton) activeButton.scrollIntoView({ block: 'nearest', inline: 'center' });
  }

  function fitStage() {
    const info = pageInfo(state.viewer.page);
    if (!info) return;
    if (state.viewer.zoomed) return;
    const availW = Math.max(240, viewerCanvas.clientWidth - 44);
    const availH = Math.max(240, viewerCanvas.clientHeight - 44);
    const ratio = info.width / info.height;
    const width = Math.min(availW, availH * ratio, 1080);
    pageStage.style.width = `${Math.max(260, Math.floor(width))}px`;
  }

  function toggleZoom() {
    state.viewer.zoomed = !state.viewer.zoomed;
    pageStage.classList.toggle('zoomed', state.viewer.zoomed);
    if (state.viewer.zoomed) {
      pageStage.style.width = '';
    } else {
      fitStage();
    }
    requestAnimationFrame(() => {
      if (state.viewer.zoomed) scrollToActiveBox();
    });
  }

  function scrollToActiveBox() {
    const target = viewerBoxes.querySelector('.hl[data-box-index]');
    if (!target) return;
    const canvasRect = viewerCanvas.getBoundingClientRect();
    const rect = target.getBoundingClientRect();
    const left = viewerCanvas.scrollLeft + (rect.left - canvasRect.left) - viewerCanvas.clientWidth / 2 + rect.width / 2;
    const top = viewerCanvas.scrollTop + (rect.top - canvasRect.top) - viewerCanvas.clientHeight / 2 + rect.height / 2;
    viewerCanvas.scrollTo({ left: Math.max(0, left), top: Math.max(0, top), behavior: 'smooth' });
  }

  function openHardware(code) {
    const item = state.bomMap[code];
    if (!item) return;
    const usage = (state.usageByCode[code] || []).slice().sort((a, b) => a.step - b.step);
    const used = usage.reduce((sum, entry) => sum + entry.qty, 0);
    const diff = used - item.qty;
    const statusClass = diff === 0 ? 'pass' : 'fail';
    const statusText = diff === 0 ? '与清单一致' : `相差 ${diff > 0 ? '+' : ''}${diff} 件`;
    const boxes = [{ page: item.page, box: item.box, label: `${code} 清单`, kind: diff === 0 ? 'pass' : 'error' }];
    for (const entry of usage) {
      boxes.push({ page: entry.page, box: entry.box, label: `S${entry.step} · ${entry.qty}`, kind: diff === 0 ? 'pass' : 'error' });
    }
    const usageButtons = usage.length
      ? usage.map((entry) => `<button class="usage-item" data-action="viewer-page" data-page="${entry.page}"><b>S${entry.step}</b><span>${entry.repeat > 1 ? `×${entry.repeat} 重复` : '单次'}</span><span class="qty">${entry.qty} 件</span></button>`).join('')
      : '<div class="side-empty">所有步骤用量表中均未出现该编号。</div>';
    const sideHtml = `<div class="pill ${statusClass}">五金 ${esc(code)}</div>
      <h3>五金 ${esc(code)} ${diff === 0 ? '用量一致' : '存在差异'}</h3>
      <div class="side-sub">清单需求 ${item.qty} 件${item.spare ? ` · 另附 ${item.spare} 件备用` : ''} · 供应合计 ${item.qty + item.spare} 件</div>
      <div class="side-section"><h4>用量对比</h4><div class="side-detail">步骤合计 <b>${used}</b> 件 · 差额 <b style="color:${diff === 0 ? 'var(--pass)' : 'var(--error)'}">${diff === 0 ? '0' : `${diff > 0 ? '+' : ''}${diff}`}</b> 件<br>${esc(statusText)}</div></div>
      <div class="side-section"><h4>使用位置</h4><div class="usage-list">${usageButtons}</div></div>`;
    openViewer({ page: item.page, boxes, title: `五金 ${code} · 使用位置`, sideHtml, severity: diff === 0 ? 'pass' : 'error' });
  }

  function openPart(no) {
    const item = state.partMap[no];
    if (!item) return;
    const refs = (state.partRefs[no] || []).slice().sort((a, b) => a.no - b.no);
    const status = partStatus(item);
    const boxes = [{ page: item.page, box: item.box, label: `板件 ${no} 清单`, kind: status.status === 'pass' ? 'pass' : 'warn' }];
    for (const step of refs) {
      for (const panel of step.panels) {
        if (Number(panel.no) === no) boxes.push({ page: step.page, box: panel.box, label: `S${step.no}`, kind: status.status === 'pass' ? 'pass' : 'warn' });
      }
    }
    for (const overview of state.result.overview_boxes) {
      if (overview.no === no) boxes.push({ page: overview.page, box: overview.box, label: '总览', kind: 'pass' });
    }
    const refButtons = refs.length
      ? refs.map((step) => `<button class="usage-item" data-action="viewer-page" data-page="${step.page}"><b>S${step.no}</b><span>${step.repeat > 1 ? `×${step.repeat} 重复` : '单次'}</span><span class="qty">${step.panels.filter((panel) => Number(panel.no) === no).length} 处标注</span></button>`).join('')
      : '<div class="side-empty">安装步骤中没有引用该板件。</div>';
    const sideHtml = `<div class="pill ${status.status === 'pass' ? 'pass' : 'warn'}">板件 ${no}</div>
      <h3>板件 ${esc(no)} · ${esc(status.text)}</h3>
      <div class="side-sub">清单数量 ${item.qty} 件 · ${refs.length} 个步骤引用 · ${state.result.overview.includes(no) ? '总览已标注' : '总览未标注'}</div>
      <div class="side-section"><h4>核对结果</h4><div class="side-detail">${esc(status.note)}<br>同一板件在一次步骤图中多次出现时，可能为爆炸图与装配图两个视图，需要人工确认。</div></div>
      <div class="side-section"><h4>出现位置</h4><div class="usage-list">${refButtons}</div></div>`;
    openViewer({ page: item.page, boxes, title: `板件 ${no} · 编号与用量`, sideHtml, severity: status.status === 'pass' ? 'pass' : 'warn' });
  }

  function openStep(no) {
    const step = state.stepMap[no];
    if (!step) return;
    const boxes = [];
    for (const panel of step.panels) boxes.push({ page: step.page, box: panel.box, label: `板件 ${panel.no}`, kind: 'pass' });
    for (const entry of step.hardware) boxes.push({ page: step.page, box: entry.box, label: `${entry.code} ×${entry.qty}`, kind: 'pass' });
    if (step.table_box) boxes.push({ page: step.page, box: step.table_box, label: '用量表', kind: 'info' });
    const issues = state.issuesByStep[no] || [];
    const panelChips = step.panel_nos.map((panelNo) => `<span class="chip panel">${panelNo}</span>`).join('') || '<span class="chip">未标注</span>';
    const hwRows = step.hardware.length
      ? step.hardware.map((entry) => `<div class="usage-item" style="cursor:default"><b>${esc(entry.code)}</b><span>×${entry.qty}${step.repeat > 1 ? ` · 每套 ${Number((entry.qty / step.repeat).toFixed(2))}` : ''}</span><span class="qty">${step.repeat > 1 ? '总用量' : ''}</span></div>`).join('')
      : '<div class="side-empty">该步骤没有五金用量表。</div>';
    const issueRows = issues.length
      ? issues.map((issue) => `<div class="usage-item" style="cursor:default"><b>${SEV_TEXT[issue.severity]}</b><span>${esc(issue.title)}</span></div>`).join('')
      : '<div class="side-empty">该步骤没有发现异常。</div>';
    const sideHtml = `<div class="pill info">步骤 ${step.no}</div>
      <h3>步骤 ${step.no}${step.repeat > 1 ? ` · ×${step.repeat} 重复` : ''}</h3>
      <div class="side-sub">PDF 第 ${step.page} 页${step.page_label ? ` · 图册 ${step.page_label}` : ''} · ${step.panel_nos.length} 种板件 · ${step.hardware.length} 种五金</div>
      <div class="side-section"><h4>板件编号</h4><div>${panelChips}</div></div>
      <div class="side-section"><h4>五金用量</h4><div class="usage-list">${hwRows}</div></div>
      <div class="side-section"><h4>核对提示</h4><div class="usage-list">${issueRows}</div></div>`;
    openViewer({ page: step.page, boxes, title: `步骤 ${step.no} · 图纸位置`, sideHtml, severity: issues.some((issue) => issue.severity === 'error') ? 'error' : 'info' });
  }

  function openIssue(id) {
    const issue = state.result.issues.find((item) => item.id === id);
    if (!issue) return;
    const boxes = (issue.boxes || []).map((box) => ({ ...box, kind: issue.severity }));
    const page = boxes[0] ? boxes[0].page : (issue.page || 1);
    const pages = Array.from(new Set(boxes.map((box) => box.page)));
    const pageButtons = pages.length
      ? pages.map((pageNo) => `<button class="usage-item" data-action="viewer-page" data-page="${pageNo}"><b>PDF 第 ${pageNo} 页</b><span>${state.result.pages.find((page) => page.index === pageNo)?.label || ''}</span><span class="qty">定位</span></button>`).join('')
      : '<div class="side-empty">该问题没有可定位的图形区域。</div>';
    const sideHtml = `<div class="pill ${issue.severity}">${SEV_TEXT[issue.severity] || issue.severity}</div>
      <h3>${esc(issue.title)}</h3>
      <div class="side-sub">${esc(issueLocation(issue))}</div>
      <div class="side-section"><h4>问题说明</h4><div class="side-detail">${esc(issue.detail)}</div><div class="side-suggest"><b>建议：</b>${esc(issue.suggestion)}</div></div>
      <div class="side-section"><h4>定位页</h4><div class="usage-list">${pageButtons}</div></div>`;
    openViewer({ page, boxes, title: issue.title, sideHtml, severity: issue.severity });
  }

  function openPage(page) {
    const info = pageInfo(page);
    if (!info) return;
    const issues = state.issuesByPage[page] || [];
    const boxes = [];
    for (const issue of issues) {
      for (const box of issue.boxes || []) {
        if (box.page === page) boxes.push({ ...box, kind: issue.severity });
      }
    }
    const stepText = info.steps.length ? info.steps.map((no) => `步骤 ${no}`).join('、') : (ROLE_TEXT[info.role] || '页面');
    const issueButtons = issues.length
      ? issues.map((issue) => `<button class="usage-item" data-action="open-issue" data-id="${esc(issue.id)}"><b>${SEV_TEXT[issue.severity]}</b><span>${esc(issue.title)}</span><span class="qty">定位</span></button>`).join('')
      : '<div class="side-empty">该页没有发现问题。</div>';
    const sideHtml = `<div class="pill info">${esc(ROLE_TEXT[info.role] || '页面')}</div>
      <h3>PDF 第 ${info.index} 页${info.label ? ` · 图册 ${info.label}` : ''}</h3>
      <div class="side-sub">${esc(stepText)}</div>
      <div class="side-section"><h4>本页问题</h4><div class="usage-list">${issueButtons}</div></div>
      <div class="side-section"><h4>页面说明</h4><div class="side-detail">点击底部页码可连续浏览；带颜色的页码表示五金清单、板件清单或安装步骤页。</div></div>`;
    openViewer({ page, boxes, title: `PDF 第 ${page} 页`, sideHtml, severity: 'info' });
  }

  function exportReport() {
    if (!state.result) return;
    const link = document.createElement('a');
    link.href = `/api/export?id=${encodeURIComponent(state.result.id)}`;
    link.download = '';
    document.body.appendChild(link);
    link.click();
    link.remove();
    showToast('Excel 报告已开始生成并下载。', 'success');
  }

  function showLoading() {
    loading.hidden = false;
    let index = 0;
    loadingText.textContent = LOADING_MESSAGES[0];
    clearInterval(state.loadingTimer);
    state.loadingTimer = setInterval(() => {
      index = (index + 1) % LOADING_MESSAGES.length;
      loadingText.textContent = LOADING_MESSAGES[index];
    }, 900);
  }

  function hideLoading() {
    clearInterval(state.loadingTimer);
    state.loadingTimer = null;
    loading.hidden = true;
  }

  function showToast(message, type) {
    toastEl.textContent = message;
    toastEl.className = `toast ${type || ''}`;
    toastEl.hidden = false;
    clearTimeout(state.toastTimer);
    state.toastTimer = setTimeout(() => {
      toastEl.hidden = true;
    }, 3400);
  }

  async function loadKnowledgeStats() {
    try {
      const response = await fetch('/api/knowledge');
      if (!response.ok) return;
      state.knowledge = await response.json();
      const badge = $('#kbCount');
      if (badge) badge.textContent = state.knowledge.corrections || 0;
      if (btnKnowledge) {
        btnKnowledge.title = state.knowledge.enabled
          ? `本地知识库：缓存 ${state.knowledge.documents} 份图纸，学习 ${state.knowledge.corrections} 条修正`
          : '本地知识库未启用';
      }
    } catch (_) {
      // Knowledge base is optional; analysis still works without it.
    }
  }

  async function submitFeedback(issueId, decision) {
    if (!state.result) return;
    try {
      const response = await fetch('/api/feedback', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          doc_id: state.result.id,
          issue_id: issueId,
          decision,
        }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.message || '反馈保存失败。');
      setResult(data, { preserveView: true });
      showToast(
        decision === 'dismiss'
          ? '已记住为误报，后续同模板会自动忽略这条结果。'
          : '已确认为问题，后续同模板会保留并标记。',
        'success'
      );
    } catch (error) {
      showToast(error.message || '反馈保存失败。', 'error');
    }
  }

  function knowledgeStat(value, label) {
    return `<div class="kb-stat"><b>${esc(value)}</b><span>${esc(label)}</span></div>`;
  }

  function knowledgeCorrectionRows(kb) {
    return (kb.correction_rows || []).map((row) => `<div class="kb-row">
      <div class="kb-row-main">
        <div class="kb-row-title">${esc(row.issue_title)}</div>
        <div class="kb-row-meta">${esc(row.template_name)} · 规则 ${esc(row.issue_code)} · 命中 ${row.hits || 0} 次 · ${esc(row.last_used || '')}</div>
      </div>
      <span class="kb-tag ${row.decision === 'dismiss' ? 'dismiss' : 'confirm'}">${row.decision === 'dismiss' ? '误报' : '确认'}</span>
      <button class="kb-delete" data-kb-delete="${esc(row.signature)}" title="删除这条记忆">删除</button>
    </div>`).join('');
  }

  function knowledgeDocumentRows(kb) {
    return (kb.document_rows || []).map((row) => `<div class="kb-row">
      <div class="kb-row-main">
        <div class="kb-row-title">${esc(row.file_name)}</div>
        <div class="kb-row-meta">${esc(row.template_name)} · ${row.page_count} 页 · 首次 ${row.first_elapsed_ms || 0} ms · 最近 ${row.last_elapsed_ms || 0} ms</div>
      </div>
      <span class="kb-tag">${row.hits || 0} 次命中</span>
      <span class="kb-row-time">${esc(row.last_used || '')}</span>
    </div>`).join('');
  }

  async function knowledgeAction(action, body = {}) {
    try {
      const response = await fetch(`/api/knowledge/${action}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.message || '知识库操作失败。');
      state.knowledge = data.knowledge || null;
      if (state.result) {
        const refreshed = await fetch(`/api/result?id=${encodeURIComponent(state.result.id)}`);
        if (refreshed.ok) setResult(await refreshed.json(), { preserveView: true });
      }
      const message = action === 'clear-cache'
        ? '文档缓存已清空，下次打开会重新解析。'
        : action === 'clear-corrections'
          ? '学习记录已清空，被忽略的问题会重新出现。'
          : '这条知识记忆已删除。';
      showToast(message, 'success');
    } catch (error) {
      showToast(error.message || '知识库操作失败。', 'error');
    }
  }

  async function showKnowledge() {
    const existing = $('.knowledge-modal');
    if (existing) existing.remove();
    await loadKnowledgeStats();
    const kb = state.knowledge || {};
    const correctionRows = knowledgeCorrectionRows(kb);
    const documentRows = knowledgeDocumentRows(kb);
    const modal = document.createElement('div');
    modal.className = 'help-modal knowledge-modal';
    modal.innerHTML = `<div class="help-card knowledge-card">
      <h2>本地知识库</h2>
      <p class="lead">每次校验都会记录 PDF 指纹和解析结果；再次打开同一图纸会直接走缓存。点击问题清单里的“标记误报并记住”或“确认为问题”，软件会按模板、规则和问题标题记住你的判断，并在后续图纸中自动应用。</p>
      <div class="kb-stat-grid">
        ${knowledgeStat(kb.documents || 0, '已缓存图纸')}
        ${knowledgeStat(kb.cache_hits || 0, '累计缓存命中')}
        ${knowledgeStat(kb.corrections || 0, '学习修正')}
        ${knowledgeStat(`${kb.speedup_pct || 0}%`, '平均提速')}
      </div>
      <div class="kb-speed">首次平均 ${kb.avg_first_ms || 0} ms · 缓存命中平均 ${kb.avg_cached_ms || 0} ms · 引擎 ${esc(kb.engine_version || '')}</div>
      <div class="kb-section-head"><b>学习到的修正</b><button class="kb-text-btn" data-kb-action="clear-corrections">清空学习记录</button></div>
      <div class="kb-list">${correctionRows || '<div class="kb-empty">还没有人工修正。打开问题清单，点击“标记误报并记住”即可开始学习。</div>'}</div>
      <div class="kb-section-head"><b>已缓存图纸</b><button class="kb-text-btn" data-kb-action="clear-cache">清空文档缓存</button></div>
      <div class="kb-list">${documentRows || '<div class="kb-empty">还没有缓存图纸。打开一次 PDF 后会自动建立缓存。</div>'}</div>
      <button class="btn btn-primary help-close" data-close-help>关闭</button>
    </div>`;
    modal.addEventListener('click', async (event) => {
      if (event.target === modal || event.target.closest('[data-close-help]')) {
        modal.remove();
        return;
      }
      const removeButton = event.target.closest('[data-kb-delete]');
      if (removeButton) {
        await knowledgeAction('delete', { signature: removeButton.dataset.kbDelete });
        modal.remove();
        showKnowledge();
        return;
      }
      const actionButton = event.target.closest('[data-kb-action]');
      if (actionButton) {
        await knowledgeAction(actionButton.dataset.kbAction);
        modal.remove();
        showKnowledge();
      }
    });
    document.body.appendChild(modal);
  }

  function callDesktopApi(method, ...args) {
    const api = window.pywebview && window.pywebview.api;
    if (!api || typeof api[method] !== 'function') {
      showToast('该功能需要在桌面程序中使用。', 'error');
      return null;
    }
    return api[method](...args);
  }

  function cacheBust(url) {
    const value = url || '/api/widget/avatar';
    return `${value}${value.includes('?') ? '&' : '?'}v=${Date.now()}`;
  }

  async function persistWidgetSettings(patch) {
    const api = window.pywebview && window.pywebview.api;
    if (api && typeof api.update_widget_settings === 'function') {
      try {
        return await api.update_widget_settings(patch);
      } catch (_) {
        // Fall through to the local HTTP route.
      }
    }
    try {
      const response = await fetch('/api/widget/settings', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(patch),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.message || '设置保存失败。');
      return data;
    } catch (error) {
      showToast(error.message || '设置保存失败。', 'error');
      return null;
    }
  }

  function showClosePrompt() {
    if ($('.close-modal')) return;
    const modal = document.createElement('div');
    modal.className = 'help-modal close-modal';
    modal.innerHTML = `<div class="close-card">
      <div class="close-icon">
        <svg viewBox="0 0 24 24" fill="none" aria-hidden="true"><circle cx="12" cy="12" r="8" stroke="currentColor" stroke-width="1.6"/><path d="M8.6 12h6.8M12 8.6v6.8" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg>
      </div>
      <span class="close-kicker">关闭柜图校对</span>
      <h2>要最小化到悬浮球吗？</h2>
      <p>主窗口会隐藏，桌面上保留一个圆形悬浮球。把 PDF 拖到悬浮球上，就能直接开始核对；左键单击悬浮球可恢复主界面。</p>
      <div class="close-actions">
        <button class="btn btn-ghost" data-close-choice="cancel">取消</button>
        <button class="btn btn-ghost danger-ghost" data-close-choice="exit">退出程序</button>
        <button class="btn btn-primary" data-close-choice="minimize">最小化到悬浮球</button>
      </div>
      <div class="close-hint">选择“最小化到悬浮球”后，下次关闭会直接最小化；可在悬浮球设置中恢复询问。右键悬浮球或小齿轮可更换图片。</div>
    </div>`;
    modal.addEventListener('click', async (event) => {
      if (event.target === modal) {
        modal.remove();
        return;
      }
      const button = event.target.closest('[data-close-choice]');
      if (!button) return;
      const choice = button.dataset.closeChoice;
      if (choice === 'cancel') {
        modal.remove();
        return;
      }
      if (choice === 'exit') {
        await persistWidgetSettings({ close_to_widget: false });
        const result = await callDesktopApi('exit_app');
        if (result) modal.remove();
        return;
      }
      if (choice === 'minimize') {
        button.disabled = true;
        await persistWidgetSettings({ close_to_widget: true });
        const result = await callDesktopApi('minimize_to_widget');
        if (result) modal.remove();
        else button.disabled = false;
      }
    });
    document.body.appendChild(modal);
  }

  async function showWidgetSettings() {
    const existing = $('.widget-modal');
    if (existing) existing.remove();
    let info = {
      custom: false,
      url: '/api/widget/avatar',
      size: 64,
      size_min: 44,
      size_max: 160,
      size_default: 64,
      close_to_widget: false,
    };
    try {
      const response = await fetch('/api/widget/info');
      if (response.ok) info = { ...info, ...(await response.json()) };
    } catch (_) {
      // The default image and size still work if the info route is unavailable.
    }
    const widgetSize = Math.max(44, Math.min(160, Number(info.size) || 64));
    const widgetSizeMin = Math.max(40, Number(info.size_min) || 44);
    const widgetSizeMax = Math.max(widgetSizeMin + 8, Number(info.size_max) || 160);
    const modal = document.createElement('div');
    modal.className = 'help-modal widget-modal';
    modal.innerHTML = `<div class="help-card widget-settings-card">
      <div class="widget-settings-head">
        <div class="widget-preview"><img id="widgetPreviewImg" src="${esc(cacheBust(info.url))}" alt="悬浮球预览"></div>
        <div>
          <span class="eyebrow">DESKTOP ORB</span>
          <h2>桌面悬浮球</h2>
          <p class="lead">圆形悬浮球默认 64px，接近桌面图标大小。点击窗口关闭按钮可选择最小化；桌面小窗口支持拖入 PDF，也支持换成你自己的图片。</p>
        </div>
      </div>
      <div class="widget-setting-note">
        <b id="widgetImageState">${info.custom ? '当前使用自定义图片' : '当前使用默认图片'}</b>
        <span>支持 PNG / JPG / WEBP / GIF / BMP，建议使用正方形图片，最大 12 MB。</span>
      </div>
      <div class="widget-size-block">
        <div class="widget-size-head">
          <b>悬浮球大小</b>
          <span id="widgetSizeValue">${widgetSize} × ${widgetSize}</span>
        </div>
        <input class="widget-size-range" id="widgetSizeRange" type="range" min="${widgetSizeMin}" max="${widgetSizeMax}" step="2" value="${widgetSize}">
        <div class="widget-size-presets">
          <button type="button" data-widget-size="48">小 48</button>
          <button type="button" data-widget-size="64">默认 64</button>
          <button type="button" data-widget-size="96">大 96</button>
          <button type="button" data-widget-size="120">特大 120</button>
        </div>
      </div>
      <div class="widget-close-block">
        <label><input id="widgetCloseToWidget" type="checkbox" ${info.close_to_widget ? 'checked' : ''}> 关闭窗口时直接最小化到悬浮球，不再询问</label>
        <span>取消勾选后，下次关闭窗口会重新弹出提示。</span>
      </div>
      <div class="widget-settings-actions">
        <button class="btn btn-primary" data-widget-setting="change">更换图片</button>
        <button class="btn btn-ghost" data-widget-setting="preview">打开悬浮球</button>
        <button class="btn btn-ghost" data-widget-setting="close">关闭</button>
      </div>
      <div class="widget-usage">
        <div><b>左键单击</b><span>恢复主界面</span></div>
        <div><b>右键 / 齿轮</b><span>打开悬浮球设置</span></div>
        <div><b>拖入 PDF</b><span>展开并自动核对</span></div>
      </div>
    </div>`;
    modal.addEventListener('click', async (event) => {
      if (event.target === modal) {
        modal.remove();
        return;
      }
      const button = event.target.closest('[data-widget-setting]');
      if (!button) return;
      const action = button.dataset.widgetSetting;
      if (action === 'close') {
        modal.remove();
        return;
      }
      if (action === 'preview') {
        modal.remove();
        await callDesktopApi('minimize_to_widget');
        return;
      }
      if (action === 'change') {
        button.disabled = true;
        const result = await callDesktopApi('choose_widget_image');
        button.disabled = false;
        if (result && result.ok) {
          const image = $('#widgetPreviewImg');
          if (image) image.src = cacheBust(result.url);
          const label = $('#widgetImageState');
          if (label) label.textContent = '当前使用自定义图片';
          showToast(result.message || '悬浮球图片已更新。', 'success');
        } else if (result && result.message && !result.canceled) {
          showToast(result.message, 'error');
        }
      }
    });
    const sizeRange = modal.querySelector('#widgetSizeRange');
    const sizeValue = modal.querySelector('#widgetSizeValue');
    const updateSizeLabel = (value) => {
      const size = Math.round(Number(value) || widgetSize);
      if (sizeValue) sizeValue.textContent = `${size} × ${size}`;
      return size;
    };
    const applySize = async (value) => {
      const size = updateSizeLabel(value);
      if (sizeRange) sizeRange.value = String(size);
      const result = await persistWidgetSettings({ size });
      if (result && result.ok) showToast(`悬浮球大小已设为 ${size}px。`, 'success');
      else if (result && result.message) showToast(result.message, 'error');
    };
    if (sizeRange) {
      sizeRange.addEventListener('input', () => updateSizeLabel(sizeRange.value));
      sizeRange.addEventListener('change', () => applySize(sizeRange.value));
    }
    modal.querySelectorAll('[data-widget-size]').forEach((button) => {
      button.addEventListener('click', () => applySize(button.dataset.widgetSize));
    });
    const closeToggle = modal.querySelector('#widgetCloseToWidget');
    if (closeToggle) {
      closeToggle.addEventListener('change', async () => {
        const result = await persistWidgetSettings({ close_to_widget: closeToggle.checked });
        if (result && result.ok) {
          showToast(closeToggle.checked ? '下次关闭将直接最小化。' : '下次关闭会重新弹出提示。', 'success');
        }
      });
    }
    document.body.appendChild(modal);
  }

  async function loadResultById(docId) {
    if (!docId) return;
    showLoading();
    try {
      const response = await fetch(`/api/result?id=${encodeURIComponent(docId)}`);
      const data = await response.json();
      if (!response.ok) throw new Error(data.message || '读取核对结果失败。');
      setResult(data);
      showToast('悬浮球已接收到图纸，核对完成。', 'success');
    } catch (error) {
      showToast(error.message || '读取核对结果失败。', 'error');
      if (!state.result) renderEmpty();
    } finally {
      hideLoading();
    }
  }

  function showHelp() {
    const existing = $('.help-modal');
    if (existing) existing.remove();
    const modal = document.createElement('div');
    modal.className = 'help-modal';
    modal.innerHTML = `<div class="help-card">
      <h2>检查说明与使用边界</h2>
      <p class="lead">这款工具把“画完的安装说明书”当作数据来核对：先识别清单与每个步骤的用量表，再按规则交叉检查。它不替代设计判断，但能把容易漏看、算错、标错的地方集中指出来。</p>
      <div class="help-row"><b>五金用量合计</b><span>逐项累加所有步骤用量表，与五金清单的“需求数量”比较。清单 x66+2 表示需求 66 件、另附 2 件备用；核对以 66 为准，备用单独展示。</span></div>
      <div class="help-row"><b>步骤用量表</b><span>检查同一编号是否在一张表里重复、数量是否为 0、数量能否被步骤的 ×N 重复倍数整除。</span></div>
      <div class="help-row"><b>五金编号合法性</b><span>步骤表中的字母必须存在于五金清单，防止字母错标或清单漏项。</span></div>
      <div class="help-row"><b>板件编号合法性</b><span>安装步骤与装配总览中的板件编号必须存在于板件清单；清单中的板件也应至少出现在一个步骤中。</span></div>
      <div class="help-row"><b>重复模块用量</b><span>按 ×N 重复的步骤，如果每套都使用某板件，清单数量必须不少于 N 且能被 N 整除，否则该编号很可能标错。</span></div>
      <div class="help-row"><b>同图多次标注</b><span>同一板件在一张步骤图里出现多次，可能是爆炸图与装配图两个视图，也可能确实标错。工具将其列为“提示”而非硬错误，需人工确认。</span></div>
      <div class="help-row"><b>识别边界</b><span>工具依赖 PDF 的文字层。文字已转曲或扫描件无法自动识别，会给出提示并要求人工复核。</span></div>
      <div class="help-row"><b>模板边界</b><span>当前版本内置领客屋、遨森、英驰三类安装图版式，会根据 PDF 页面内容自动识别。不同模板的清单位置、编号写法和步骤卡结构会分别处理；版式再变化时仍需补充识别规则。</span></div>
      <div class="help-row"><b>桌面悬浮球</b><span>点击关闭按钮可选择最小化到悬浮球。默认 64px 圆形悬浮球保持置顶，可在“悬浮球”设置中调整 44–160px；左键单击恢复主界面，右键或齿轮可更换图片；把 PDF 拖到悬浮球上会展开为椭圆并自动开始核对。首次选择“最小化到悬浮球”后，后续关闭会直接最小化；选择“退出程序”则下次启动重新询问。</span></div>
      <div class="help-row"><b>拖拽打开</b><span>可以直接把 PDF 从 Windows 资源管理器拖入程序窗口任意位置，出现蓝色投放提示后松开鼠标即可开始解析。一次读取一份图纸，文件不超过 120 MB。</span></div>
      <div class="help-row"><b>数据安全</b><span>全部解析在本机完成，不上传图纸，不调用云端 OCR 或大模型。</span></div>
      <button class="btn btn-primary help-close" data-close-help>知道了</button>
    </div>`;
    modal.addEventListener('click', (event) => {
      if (event.target === modal || event.target.closest('[data-close-help]')) modal.remove();
    });
    document.body.appendChild(modal);
  }

  viewerSide.addEventListener('click', (event) => {
    const target = event.target.closest('[data-action]');
    if (!target) return;
    if (target.dataset.action === 'viewer-page') goPage(Number(target.dataset.page));
    if (target.dataset.action === 'open-issue') {
      closeViewer();
      openIssue(target.dataset.id);
    }
  });

  window.cabinetShowClosePrompt = showClosePrompt;
  window.cabinetLoadResultById = loadResultById;

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
