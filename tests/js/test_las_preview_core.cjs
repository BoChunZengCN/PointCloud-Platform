const test = require('node:test');
const assert = require('node:assert/strict');
const { parseLasHeader, readLasFile } = require('../../frontend/las-preview-core.js');

function lasFixture({ format = 2, count = 2 } = {}) {
  const recordLength = format === 3 ? 34 : 26;
  const buffer = new ArrayBuffer(227 + recordLength * count);
  const view = new DataView(buffer);
  for (const [index, char] of [...'LASF'].entries()) view.setUint8(index, char.charCodeAt(0));
  view.setUint8(24, 1);
  view.setUint8(25, 2);
  view.setUint16(94, 227, true);
  view.setUint32(96, 227, true);
  view.setUint8(104, format);
  view.setUint16(105, recordLength, true);
  view.setUint32(107, count, true);
  for (const offset of [131, 139, 147]) view.setFloat64(offset, 0.01, true);
  view.setFloat64(155, 1000, true);
  view.setFloat64(163, 2000, true);
  view.setFloat64(171, 3000, true);
  const raw = [[0, 0, 0], [200, 100, 50]];
  for (let index = 0; index < count; index += 1) {
    const base = 227 + recordLength * index;
    for (let axis = 0; axis < 3; axis += 1) view.setInt32(base + axis * 4, raw[index % 2][axis], true);
    const color = format === 3 ? base + 28 : base + 20;
    view.setUint16(color, index % 2 ? 0 : 65535, true);
    view.setUint16(color + 2, index % 2 ? 65535 : 0, true);
    view.setUint16(color + 4, 0, true);
  }
  return buffer;
}

test('LAS 1.2 格式 2：解码全部原始 XYZ 与 RGB，不抽样', async () => {
  const buffer = lasFixture();
  const header = parseLasHeader(buffer, buffer.byteLength);
  assert.equal(header.pointCount, 2);
  assert.equal(header.pointFormat, 2);
  const cloud = await readLasFile(new Blob([buffer]));
  assert.equal(cloud.pointCount, 2);
  assert.deepEqual([...cloud.positions], [-1, -0.5, -0.25, 1, 0.5, 0.25]);
  assert.deepEqual([...cloud.colors], [255, 0, 0, 0, 255, 0]);
  assert.deepEqual(cloud.extent, [2, 1, 0.5]);
});

test('LAS 1.2 格式 3：使用格式 3 的 RGB 偏移', async () => {
  const cloud = await readLasFile(new Blob([lasFixture({ format: 3 })]));
  assert.deepEqual([...cloud.colors], [255, 0, 0, 0, 255, 0]);
});

test('拒绝错误签名、压缩点格式及无效比例尺', () => {
  const signature = lasFixture();
  new DataView(signature).setUint8(0, 0);
  assert.throws(() => parseLasHeader(signature, signature.byteLength), /LAS 签名/);
  const compressed = lasFixture();
  new DataView(compressed).setUint8(104, 0x82);
  assert.throws(() => parseLasHeader(compressed, compressed.byteLength), /LAZ|压缩/);
  const scale = lasFixture();
  new DataView(scale).setFloat64(131, 0, true);
  assert.throws(() => parseLasHeader(scale, scale.byteLength), /比例尺/);
});

test('拒绝截断点记录及超过浏览器预算的文件', async () => {
  const truncated = lasFixture();
  assert.throws(() => parseLasHeader(truncated, truncated.byteLength - 1), /截断|长度/);
  const oversized = { size: 129 * 1024 * 1024, slice() { throw Error('不应读取'); } };
  await assert.rejects(readLasFile(oversized), /128 MiB/);
  const tooMany = lasFixture();
  new DataView(tooMany).setUint32(107, 750001, true);
  assert.throws(() => parseLasHeader(tooMany, 227 + 750001 * 26), /750,000/);
});

test('拒绝在坐标中心化或 Float32 显示转换时溢出的 LAS', async () => {
  const wide = lasFixture();
  const wideView = new DataView(wide);
  wideView.setFloat64(131, 1e40, true);
  wideView.setFloat64(155, 0, true);
  wideView.setInt32(227, -1, true);
  wideView.setInt32(253, 1, true);
  await assert.rejects(readLasFile(new Blob([wide])), /坐标|范围|精度/);

  const largeOffset = lasFixture();
  const offsetView = new DataView(largeOffset);
  offsetView.setFloat64(155, 1e308, true);
  await assert.rejects(readLasFile(new Blob([largeOffset])), /坐标|范围|精度/);

  const tinyScale = lasFixture();
  const tinyView = new DataView(tinyScale);
  tinyView.setFloat64(131, 1e-50, true);
  tinyView.setFloat64(155, 0, true);
  await assert.rejects(readLasFile(new Blob([tinyScale])), /坐标|范围|精度/);
});
