(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.LasPreviewCore = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  const MAX_BYTES = 128 * 1024 * 1024;
  const MAX_POINTS = 750000;
  const MIN_HEADER_BYTES = 227;
  const RECORD_BYTES = { 2: 26, 3: 34 };

  function parseLasHeader(buffer, fileSize) {
    if (!(buffer instanceof ArrayBuffer) || buffer.byteLength < MIN_HEADER_BYTES) {
      throw new Error("LAS 文件头截断。");
    }
    if (!Number.isSafeInteger(fileSize) || fileSize < MIN_HEADER_BYTES) {
      throw new Error("LAS 文件长度无效。");
    }
    if (fileSize > MAX_BYTES) {
      throw new Error("文件超过 128 MiB 浏览器直接预览上限，请使用分层发布。");
    }
    const view = new DataView(buffer);
    if ([76, 65, 83, 70].some((value, index) => view.getUint8(index) !== value)) {
      throw new Error("LAS 签名无效。");
    }
    const versionMajor = view.getUint8(24);
    const versionMinor = view.getUint8(25);
    if (versionMajor !== 1 || (versionMinor !== 2 && versionMinor !== 3)) {
      throw new Error("当前直接预览仅支持 LAS 1.2/1.3。");
    }
    const headerSize = view.getUint16(94, true);
    const pointDataOffset = view.getUint32(96, true);
    if (headerSize < MIN_HEADER_BYTES || pointDataOffset < headerSize || pointDataOffset > fileSize) {
      throw new Error("LAS 文件头或点数据偏移无效。");
    }
    const pointFormat = view.getUint8(104);
    if (pointFormat & 0xc0) throw new Error("压缩 LAS/LAZ 暂不支持浏览器直接预览。");
    if (!Object.hasOwn(RECORD_BYTES, pointFormat)) {
      throw new Error("当前直接预览仅支持带 RGB 的 LAS 点格式 2/3。");
    }
    const recordLength = view.getUint16(105, true);
    const pointCount = view.getUint32(107, true);
    if (recordLength < RECORD_BYTES[pointFormat] || recordLength > 512) {
      throw new Error("LAS 点记录长度无效。");
    }
    if (pointCount === 0) throw new Error("LAS 文件没有点记录。");
    if (pointCount > MAX_POINTS) {
      throw new Error("点数超过 750,000 浏览器直接预览上限，请使用分层发布。");
    }
    if (pointDataOffset + pointCount * recordLength > fileSize) {
      throw new Error("LAS 点记录截断或文件长度不匹配。");
    }
    const scales = [131, 139, 147].map((offset) => view.getFloat64(offset, true));
    const offsets = [155, 163, 171].map((offset) => view.getFloat64(offset, true));
    if (scales.some((value) => !Number.isFinite(value) || value <= 0)) {
      throw new Error("LAS 坐标比例尺无效。");
    }
    if (offsets.some((value) => !Number.isFinite(value))) {
      throw new Error("LAS 坐标偏移无效。");
    }
    return { pointCount, pointFormat, pointDataOffset, recordLength, scales, offsets };
  }

  async function readLasFile(file) {
    if (!file || !Number.isSafeInteger(file.size)) throw new Error("请选择有效 LAS 文件。");
    if (file.name && !/\.las$/i.test(file.name)) throw new Error("请选择未压缩的 .las 文件；LAZ 暂不支持。");
    if (file.size > MAX_BYTES) {
      throw new Error("文件超过 128 MiB 浏览器直接预览上限，请使用分层发布。");
    }
    if (file.size < MIN_HEADER_BYTES || typeof file.arrayBuffer !== "function") {
      throw new Error("LAS 文件头截断或文件不可读取。");
    }
    const buffer = await file.arrayBuffer();
    if (buffer.byteLength !== file.size) throw new Error("LAS 文件读取长度不匹配。");
    const header = parseLasHeader(buffer, file.size);
    const view = new DataView(buffer);
    const absolute = new Float64Array(header.pointCount * 3);
    const rawColors = new Uint16Array(header.pointCount * 3);
    const minimum = [Infinity, Infinity, Infinity];
    const maximum = [-Infinity, -Infinity, -Infinity];
    const rawMinimum = [Infinity, Infinity, Infinity];
    const rawMaximum = [-Infinity, -Infinity, -Infinity];
    let maximumColor = 0;
    for (let index = 0; index < header.pointCount; index += 1) {
      const base = header.pointDataOffset + index * header.recordLength;
      const colorBase = base + (header.pointFormat === 2 ? 20 : 28);
      for (let axis = 0; axis < 3; axis += 1) {
        const raw = view.getInt32(base + axis * 4, true);
        const value = raw * header.scales[axis] + header.offsets[axis];
        if (!Number.isFinite(value)) throw new Error("LAS 点坐标超出有限数值范围。");
        absolute[index * 3 + axis] = value;
        minimum[axis] = Math.min(minimum[axis], value);
        maximum[axis] = Math.max(maximum[axis], value);
        rawMinimum[axis] = Math.min(rawMinimum[axis], raw);
        rawMaximum[axis] = Math.max(rawMaximum[axis], raw);
        const color = view.getUint16(colorBase + axis * 2, true);
        rawColors[index * 3 + axis] = color;
        maximumColor = Math.max(maximumColor, color);
      }
    }
    const center = minimum.map((value, axis) => value / 2 + maximum[axis] / 2);
    const extent = minimum.map((value, axis) => maximum[axis] - value);
    if (center.some((value) => !Number.isFinite(value)) || extent.some((value) => !Number.isFinite(value))) {
      throw new Error("LAS 坐标范围超出浏览器显示能力。");
    }
    if (extent.some((value, axis) => value === 0 && rawMinimum[axis] !== rawMaximum[axis])) {
      throw new Error("LAS 坐标偏移导致几何精度丢失，无法可靠预览。");
    }
    const positions = new Float32Array(absolute.length);
    const colors = new Uint8Array(rawColors.length);
    const displayedMinimum = [Infinity, Infinity, Infinity];
    const displayedMaximum = [-Infinity, -Infinity, -Infinity];
    const divisor = maximumColor <= 255 ? 1 : 257;
    for (let index = 0; index < absolute.length; index += 1) {
      const axis = index % 3;
      positions[index] = absolute[index] - center[axis];
      if (!Number.isFinite(positions[index])) {
        throw new Error("LAS 坐标范围超出浏览器显示能力。");
      }
      displayedMinimum[axis] = Math.min(displayedMinimum[axis], positions[index]);
      displayedMaximum[axis] = Math.max(displayedMaximum[axis], positions[index]);
      colors[index] = Math.round(rawColors[index] / divisor);
    }
    if (extent.some((value, axis) => value > 0 && displayedMinimum[axis] === displayedMaximum[axis])) {
      throw new Error("LAS 坐标范围低于浏览器显示精度，无法可靠预览。");
    }
    return { pointCount: header.pointCount, pointFormat: header.pointFormat, positions, colors, minimum, maximum, center, extent };
  }

  return { MAX_BYTES, MAX_POINTS, parseLasHeader, readLasFile };
});
