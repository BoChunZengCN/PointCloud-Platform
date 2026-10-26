const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../../frontend/las-preview.js'), 'utf8');

function previewHarness({ createBufferFails = false, bufferError = false, drawError = false } = {}) {
  const elements = new Map();
  function element() {
    return {
      events: new Map(), dataset: {}, textContent: '', value: '', disabled: false,
      clientWidth: 400, clientHeight: 300, width: 0, height: 0,
      addEventListener(name, listener) { this.events.set(name, listener); },
      replaceChildren() { this.children = []; },
      append(...children) { this.children = (this.children || []).concat(children); },
      classList: { toggle() {} },
    };
  }
  for (const id of ['las-canvas', 'preview-status', 'point-counter', 'cloud-facts',
    'las-files', 'las-file-list', 'view-name', 'point-size', 'point-size-value']) {
    elements.set(id, element());
  }
  const gl = {
    VERTEX_SHADER: 1, FRAGMENT_SHADER: 2, COMPILE_STATUS: 3, LINK_STATUS: 4,
    ARRAY_BUFFER: 5, STATIC_DRAW: 6, COLOR_BUFFER_BIT: 7, DEPTH_BUFFER_BIT: 8,
    DEPTH_TEST: 9, FLOAT: 10, UNSIGNED_BYTE: 11, POINTS: 12, NO_ERROR: 0,
    createShader: () => ({}), shaderSource() {}, compileShader() {},
    getShaderParameter: () => true, deleteShader() {},
    createProgram: () => ({}), attachShader() {}, linkProgram() {},
    deleteProgram() {}, getProgramParameter: () => true, useProgram() {},
    clearColor() {}, enable() {}, getAttribLocation: () => 0,
    getUniformLocation: () => ({}),
    createBuffer: () => createBufferFails ? null : ({}),
    bindBuffer() {}, bufferData() { if (bufferError) this.pendingError = 1285; }, deleteBuffer() {},
    viewport() {}, clear() {}, uniform1f() {}, uniform2f() {},
    enableVertexAttribArray() {}, vertexAttribPointer() {},
    drawArrays() { if (drawError) this.pendingError = 1282; },
    getError() { const code = this.pendingError || 0; this.pendingError = 0; return code; },
    isContextLost: () => false,
  };
  const canvas = elements.get('las-canvas');
  canvas.getContext = () => gl;
  const cloud = {
    pointCount: 1, positions: new Float32Array([0, 0, 0]),
    colors: new Uint8Array([255, 0, 0]), extent: [1, 1, 1],
  };
  const document = {
    getElementById: (id) => elements.get(id),
    createElement: () => element(),
    querySelectorAll: () => [],
  };
  const window = {
    LasPreviewCore: { readLasFile: async () => cloud },
    devicePixelRatio: 1, addEventListener() {},
  };
  vm.runInNewContext(source, { document, window, ResizeObserver: undefined });
  return { elements, canvas, gl };
}

async function chooseOneFile(harness) {
  const input = harness.elements.get('las-files');
  input.files = [{ name: 'sample.las' }];
  input.events.get('change')();
  await new Promise((resolve) => setImmediate(resolve));
}

test('WebGL 无法创建 GPU 缓冲时，不宣称点云已显示', async () => {
  const preview = previewHarness({ createBufferFails: true });
  await chooseOneFile(preview);
  assert.equal(preview.elements.get('preview-status').dataset.kind, 'error');
  assert.equal(preview.elements.get('point-counter').textContent, '0 点');
  assert.equal(preview.elements.get('las-files').disabled, true);
});

test('WebGL 上下文丢失后清除成功状态并要求刷新', async () => {
  const preview = previewHarness();
  await chooseOneFile(preview);
  assert.equal(preview.elements.get('preview-status').textContent, '全部原始点已显示');
  let prevented = false;
  preview.canvas.events.get('webglcontextlost')({ preventDefault() { prevented = true; } });
  assert.equal(prevented, true);
  assert.equal(preview.elements.get('preview-status').dataset.kind, 'error');
  assert.equal(preview.elements.get('point-counter').textContent, '0 点');
});

test('WebGL 上传或绘制报错时，不宣称点云已显示', async () => {
  for (const option of [{ bufferError: true }, { drawError: true }]) {
    const preview = previewHarness(option);
    await chooseOneFile(preview);
    assert.equal(preview.elements.get('preview-status').dataset.kind, 'error');
    assert.equal(preview.elements.get('point-counter').textContent, '0 点');
    assert.equal(preview.elements.get('las-files').disabled, true);
  }
});
