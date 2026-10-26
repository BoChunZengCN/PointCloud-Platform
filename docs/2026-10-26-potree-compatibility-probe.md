# Potree 分层预览兼容性探针（2026-10-26）

## 范围与结论

本次只验证完整 LAS 预览链路所需的转换器产物结构；浏览器中的真实显示另按阶段验收。已从官方发布地址取得并固定以下资源，放在 Git 忽略的 `workspace/`，不提交第三方包或点云：

| 资源 | 官方来源 | 下载包 SHA-256 |
| --- | --- | --- |
| PotreeConverter 2.1.1 Windows x64 | [官方发布](https://github.com/potree/PotreeConverter/releases/tag/2.1.1) | `8B4A70194FA85CEAFA51E017B58E455CF3539EDC9AB212B2C92B4A1F2B15E887` |
| Potree 查看器 1.8.2 | [官方发布](https://github.com/potree/potree/releases/tag/1.8.2) | `C140A8BCE0CE129DE762FBC0FD3BA7C21120FFD36D423DD2755ABB39F32AEBE5` |

转换器可执行文件 SHA-256 为 `117DAB69B06C99A01367F178019CE8F094A9FAA2ECFFCBACA7B6C554313C0A1E`；查看器 `build/potree/potree.js` SHA-256 为 `E503483823E30FF5398382840343FC46BBC3DF604D7D589EE4508A014C38CA4C`。这些是本机下载校验记录，并非上游公开签名或供应链认证；任何重新下载都要重新核对来源和指纹。

## 真实小样本结果

- 输入：`workspace/validation-samples/machine-zoom-preview-s50-20261023-v2.las`，24,908,123 字节，957,996 点，SHA-256 `145BD2ABD50E7E7F3C33BDB4447D7F1E4FCD021DCA184FBF4A8F173211EACC2A`。
- 命令边界：`PotreeConverter.exe -i <输入> -o <新运行目录>`；转换器帮助确认支持 `-i` 和 `-o`。本机实际运行约 2 秒。
- 输出：`metadata.json` 3,464 字节、`hierarchy.bin` 15,004 字节、`octree.bin` 25,865,892 字节。元数据 `version=2.0`、`encoding=DEFAULT`、`points=957996`、`rgb.size=6`、`firstChunkSize=8668`、`depth=4`。
- 新门禁核对来源转换前后指纹、点数、RGB、边界、层级记录和二进制偏移，真实输出通过。模拟故障聚焦测试与只读来源测试共 18 项通过。

## 尚未通过的门禁

- 尚未在浏览器加载查看器资源、检查颜色与镜头操作，不能宣称查看器组合已完成端到端兼容性验证。
- 尚未转换 47,899,752 点完整原始 LAS；完整转换须在版本化发布、Range 服务与页面就绪后执行。
- 当前包没有可信的上游签名/校验和证明，保留下载地址、版本和本机 SHA-256 用于追溯。

Potree 1.8.2 的[官方加载器源码](https://github.com/potree/potree/blob/1.8.2/src/modules/loader/2.0/OctreeLoader.js)确实针对 `hierarchy.bin` 和 `octree.bin` 发起单段 HTTP Range 请求；这支持服务设计，但不代替浏览器验收。
