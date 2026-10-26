(function () {
  "use strict";

  const canvas = document.getElementById("las-canvas");
  const status = document.getElementById("preview-status");
  const counter = document.getElementById("point-counter");
  const facts = document.getElementById("cloud-facts");
  const fileInput = document.getElementById("las-files");
  const fileList = document.getElementById("las-file-list");
  const viewName = document.getElementById("view-name");
  const pointSize = document.getElementById("point-size");
  const pointSizeValue = document.getElementById("point-size-value");
  const core = window.LasPreviewCore;
  let files = [];
  let requestId = 0;
  let cloud = null;
  let dragging = null;
  let rendererFailed = false;
  const camera = { yaw: -0.65, pitch: -0.65, zoom: 1.55, panX: 0, panY: 0 };

  function setStatus(message, kind = "normal") {
    status.textContent = message;
    status.dataset.kind = kind;
  }

  function setFacts(rows) {
    facts.replaceChildren();
    for (const [label, value] of rows) {
      const term = document.createElement("dt");
      const detail = document.createElement("dd");
      term.textContent = label;
      detail.textContent = value;
      facts.append(term, detail);
    }
  }

  function shader(gl, type, source) {
    const value = gl.createShader(type);
    gl.shaderSource(value, source);
    gl.compileShader(value);
    if (!gl.getShaderParameter(value, gl.COMPILE_STATUS)) {
      const message = gl.getShaderInfoLog(value) || "未知错误";
      gl.deleteShader(value);
      throw new Error(`WebGL 着色器编译失败：${message}`);
    }
    return value;
  }

  function createRenderer() {
    const gl = canvas.getContext("webgl", { antialias: false });
    if (!gl) throw new Error("当前浏览器或设备不支持 WebGL，无法进行三维点云预览。");
    const vertex = shader(gl, gl.VERTEX_SHADER, `
      attribute vec3 aPosition;
      attribute vec3 aColor;
      uniform float uYaw, uPitch, uZoom, uRadius, uAspect, uPointSize;
      uniform vec2 uPan;
      varying vec3 vColor;
      void main() {
        vec3 p = aPosition / uRadius;
        float cy = cos(uYaw), sy = sin(uYaw);
        float cp = cos(uPitch), sp = sin(uPitch);
        vec3 y = vec3(cy * p.x - sy * p.y, sy * p.x + cy * p.y, p.z);
        vec3 r = vec3(y.x, cp * y.y - sp * y.z, sp * y.y + cp * y.z);
        gl_Position = vec4((r.x + uPan.x) * uZoom / uAspect,
                           (r.y + uPan.y) * uZoom, -r.z, 1.0);
        gl_PointSize = uPointSize;
        vColor = aColor;
      }
    `);
    const fragment = shader(gl, gl.FRAGMENT_SHADER, `
      precision mediump float;
      varying vec3 vColor;
      void main() {
        vec2 d = gl_PointCoord - vec2(0.5);
        if (dot(d, d) > 0.25) discard;
        gl_FragColor = vec4(vColor, 1.0);
      }
    `);
    const program = gl.createProgram();
    gl.attachShader(program, vertex);
    gl.attachShader(program, fragment);
    gl.linkProgram(program);
    gl.deleteShader(vertex);
    gl.deleteShader(fragment);
    if (!gl.getProgramParameter(program, gl.LINK_STATUS)) {
      throw new Error(`WebGL 程序链接失败：${gl.getProgramInfoLog(program) || "未知错误"}`);
    }
    gl.useProgram(program);
    gl.clearColor(0.027, 0.063, 0.094, 1);
    gl.enable(gl.DEPTH_TEST);
    return {
      gl,
      program,
      positions: gl.getAttribLocation(program, "aPosition"),
      colors: gl.getAttribLocation(program, "aColor"),
      uniforms: Object.fromEntries(["Yaw", "Pitch", "Zoom", "Radius", "Aspect", "PointSize", "Pan"]
        .map((name) => [name, gl.getUniformLocation(program, `u${name}`)])),
      positionBuffer: null,
      colorBuffer: null,
    };
  }

  let renderer;
  try {
    if (!core) throw new Error("LAS 解析器未加载。");
    renderer = createRenderer();
  } catch (error) {
    setStatus(error.message, "error");
    fileInput.disabled = true;
    fileList.disabled = true;
    return;
  }

  function failRenderer(error) {
    if (rendererFailed) return;
    rendererFailed = true;
    requestId += 1;
    const gl = renderer.gl;
    if (!gl.isContextLost()) {
      if (renderer.positionBuffer) gl.deleteBuffer(renderer.positionBuffer);
      if (renderer.colorBuffer) gl.deleteBuffer(renderer.colorBuffer);
    }
    renderer.positionBuffer = null;
    renderer.colorBuffer = null;
    cloud = null;
    dragging = null;
    counter.textContent = "0 点";
    fileInput.disabled = true;
    fileList.disabled = true;
    pointSize.disabled = true;
    setFacts([["状态", "渲染失败"]]);
    setStatus(error.message || "WebGL 渲染失败，请刷新页面重试。", "error");
  }

  canvas.addEventListener("webglcontextlost", (event) => {
    event.preventDefault();
    failRenderer(new Error("WebGL 上下文丢失，请刷新页面重试。"));
  });

  function clearCloud() {
    if (rendererFailed) return;
    const gl = renderer.gl;
    if (renderer.positionBuffer) gl.deleteBuffer(renderer.positionBuffer);
    if (renderer.colorBuffer) gl.deleteBuffer(renderer.colorBuffer);
    renderer.positionBuffer = null;
    renderer.colorBuffer = null;
    cloud = null;
    counter.textContent = "0 点";
    draw();
  }

  function uploadCloud(value) {
    if (rendererFailed) return false;
    const gl = renderer.gl;
    let positionBuffer = null;
    let colorBuffer = null;
    try {
      if (gl.isContextLost()) throw new Error("WebGL 上下文丢失，请刷新页面重试。");
      positionBuffer = gl.createBuffer();
      if (!positionBuffer) throw new Error("WebGL 无法分配点坐标缓冲，请刷新页面重试。");
      gl.bindBuffer(gl.ARRAY_BUFFER, positionBuffer);
      gl.bufferData(gl.ARRAY_BUFFER, value.positions, gl.STATIC_DRAW);
      if (gl.getError() !== gl.NO_ERROR) throw new Error("WebGL 点坐标上传失败，请刷新页面重试。");
      colorBuffer = gl.createBuffer();
      if (!colorBuffer) throw new Error("WebGL 无法分配颜色缓冲，请刷新页面重试。");
      gl.bindBuffer(gl.ARRAY_BUFFER, colorBuffer);
      gl.bufferData(gl.ARRAY_BUFFER, value.colors, gl.STATIC_DRAW);
      if (gl.getError() !== gl.NO_ERROR) throw new Error("WebGL 点颜色上传失败，请刷新页面重试。");
      renderer.positionBuffer = positionBuffer;
      renderer.colorBuffer = colorBuffer;
      cloud = value;
      return draw();
    } catch (error) {
      if (positionBuffer && positionBuffer !== renderer.positionBuffer && !gl.isContextLost()) gl.deleteBuffer(positionBuffer);
      if (colorBuffer && colorBuffer !== renderer.colorBuffer && !gl.isContextLost()) gl.deleteBuffer(colorBuffer);
      failRenderer(error);
      return false;
    }
  }

  function draw() {
    if (rendererFailed) return false;
    const { gl, program, uniforms } = renderer;
    try {
      if (gl.isContextLost()) throw new Error("WebGL 上下文丢失，请刷新页面重试。");
      const pixelRatio = Math.min(window.devicePixelRatio || 1, 2);
      const width = Math.max(1, Math.round(canvas.clientWidth * pixelRatio));
      const height = Math.max(1, Math.round(canvas.clientHeight * pixelRatio));
      if (canvas.width !== width || canvas.height !== height) {
        canvas.width = width;
        canvas.height = height;
      }
      gl.viewport(0, 0, width, height);
      gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
      if (cloud) {
        gl.useProgram(program);
        gl.uniform1f(uniforms.Yaw, camera.yaw);
        gl.uniform1f(uniforms.Pitch, camera.pitch);
        gl.uniform1f(uniforms.Zoom, camera.zoom);
        gl.uniform1f(uniforms.Radius, Math.max(...cloud.extent, 0.001));
        gl.uniform1f(uniforms.Aspect, width / height);
        gl.uniform1f(uniforms.PointSize, Number(pointSize.value) * pixelRatio);
        gl.uniform2f(uniforms.Pan, camera.panX, camera.panY);
        gl.bindBuffer(gl.ARRAY_BUFFER, renderer.positionBuffer);
        gl.enableVertexAttribArray(renderer.positions);
        gl.vertexAttribPointer(renderer.positions, 3, gl.FLOAT, false, 0, 0);
        gl.bindBuffer(gl.ARRAY_BUFFER, renderer.colorBuffer);
        gl.enableVertexAttribArray(renderer.colors);
        gl.vertexAttribPointer(renderer.colors, 3, gl.UNSIGNED_BYTE, true, 0, 0);
        gl.drawArrays(gl.POINTS, 0, cloud.pointCount);
      }
      const code = gl.getError();
      if (code !== gl.NO_ERROR) throw new Error(`WebGL 绘制失败（错误码 ${code}），请刷新页面重试。`);
      return true;
    } catch (error) {
      failRenderer(error);
      return false;
    }
  }

  function setView(view) {
    const angles = {
      free: [-0.65, -0.65, "自由视角"],
      top: [0, 0, "顶视 XY"],
      front: [0, -Math.PI / 2, "正视 XZ"],
      side: [-Math.PI / 2, -Math.PI / 2, "侧视 YZ"],
    }[view];
    if (!angles) return;
    [camera.yaw, camera.pitch] = angles;
    camera.zoom = 1.55;
    camera.panX = camera.panY = 0;
    viewName.textContent = angles[2];
    document.querySelectorAll("[data-view]").forEach((button) => button.classList.toggle("active", button.dataset.view === view));
    draw();
  }

  async function loadSelected() {
    if (rendererFailed) return;
    const thisRequest = ++requestId;
    clearCloud();
    if (rendererFailed) return;
    const file = files[Number(fileList.value)];
    if (!file) {
      setStatus("请选择 LAS 文件");
      setFacts([["状态", "等待文件"]]);
      return;
    }
    setStatus(`正在读取 ${file.name}…`);
    setFacts([["文件", file.name], ["状态", "读取中"]]);
    try {
      const next = await core.readLasFile(file);
      if (thisRequest !== requestId) return;
      if (!uploadCloud(next)) return;
      setView("free");
      if (rendererFailed) return;
      const extentText = next.extent.map((value) => value.toFixed(3)).join(" × ");
      setFacts([
        ["文件", file.name],
        ["点数", next.pointCount.toLocaleString("zh-CN")],
        ["尺寸", `${extentText} m`],
        ["颜色", "原始 RGB"],
        ["状态", "全部原始点已显示"],
      ]);
      counter.textContent = `${next.pointCount.toLocaleString("zh-CN")} 点`;
      setStatus("全部原始点已显示");
    } catch (error) {
      if (thisRequest !== requestId || rendererFailed) return;
      clearCloud();
      setFacts([["文件", file.name], ["状态", "预览失败"]]);
      setStatus(error.message || "无法读取 LAS 文件。", "error");
    }
  }

  fileInput.addEventListener("change", () => {
    files = Array.from(fileInput.files || []);
    fileList.replaceChildren();
    for (const [index, file] of files.entries()) {
      const option = document.createElement("option");
      option.value = String(index);
      option.textContent = file.name;
      fileList.append(option);
    }
    fileList.disabled = files.length < 2;
    if (files.length) fileList.value = "0";
    void loadSelected();
  });
  fileList.addEventListener("change", () => void loadSelected());
  pointSize.addEventListener("input", () => { pointSizeValue.textContent = pointSize.value; draw(); });
  document.querySelectorAll("[data-view]").forEach((button) => {
    button.addEventListener("click", () => setView(button.dataset.view));
  });
  canvas.addEventListener("pointerdown", (event) => {
    if (!cloud) return;
    dragging = { x: event.clientX, y: event.clientY, pan: event.shiftKey };
    canvas.setPointerCapture(event.pointerId);
  });
  canvas.addEventListener("pointermove", (event) => {
    if (!dragging) return;
    const dx = event.clientX - dragging.x;
    const dy = event.clientY - dragging.y;
    dragging.x = event.clientX;
    dragging.y = event.clientY;
    if (dragging.pan) {
      camera.panX += 2 * dx / canvas.clientHeight / camera.zoom;
      camera.panY -= 2 * dy / canvas.clientHeight / camera.zoom;
    } else {
      camera.yaw += dx * 0.008;
      camera.pitch = Math.max(-Math.PI / 2, Math.min(Math.PI / 2, camera.pitch + dy * 0.008));
      viewName.textContent = "自由视角";
      document.querySelectorAll("[data-view]").forEach((button) => button.classList.toggle("active", button.dataset.view === "free"));
    }
    draw();
  });
  canvas.addEventListener("pointerup", () => { dragging = null; });
  canvas.addEventListener("pointercancel", () => { dragging = null; });
  canvas.addEventListener("wheel", (event) => {
    if (!cloud) return;
    event.preventDefault();
    camera.zoom = Math.max(0.3, Math.min(20, camera.zoom * Math.exp(-event.deltaY * 0.001)));
    draw();
  }, { passive: false });
  window.addEventListener("resize", draw);
  if (typeof ResizeObserver !== "undefined") new ResizeObserver(draw).observe(canvas);
  setView("free");
})();
