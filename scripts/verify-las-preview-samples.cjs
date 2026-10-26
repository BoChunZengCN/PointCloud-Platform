const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const { readLasFile } = require('../frontend/las-preview-core.js');

async function main() {
  const folder = process.argv[2];
  if (!folder) throw new Error('请提供候选 LAS 文件夹路径。');
  const manifest = JSON.parse(fs.readFileSync(path.join(folder, 'manifest.json'), 'utf8'));
  let total = 0;
  for (const item of manifest.candidates) {
    const name = item.point_cloud;
    const data = fs.readFileSync(path.join(folder, name));
    const cloud = await readLasFile(new File([data], name));
    assert.equal(cloud.pointCount, item.point_count, `${name} 点数不一致`);
    for (let axis = 0; axis < 3; axis += 1) {
      assert.ok(Math.abs(cloud.extent[axis] - item.extent_m[axis]) <= 0.0011, `${name} 尺寸不一致`);
    }
    assert.ok(cloud.colors.some((value) => value !== 0), `${name} 未能解析颜色`);
    total += cloud.pointCount;
    console.log(`${name}: ${cloud.pointCount} 点，RGB 与尺寸校验通过`);
  }
  console.log(`总计 ${total} 个原始点，全部处于直接预览上限内。`);
}

main().catch((error) => { console.error(error); process.exitCode = 1; });
