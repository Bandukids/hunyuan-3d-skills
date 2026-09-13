# HY-3D-Express 极速版

依据 [TokenHub HY-3D-Express 官方文档](https://cloud.tencent.com/document/product/1823/137175)，核对日期 2026-09-13。新增功能已进行离线验证，未提交 Express 付费任务，不声称已验证实际生成质量或速度。

## 接入

- 模型参数：`hy-3d-express`，CLI 可简写为 `--model express`。
- 提供商：`tokenhub`；此客户端不支持 Express 的旧版 AI3D 接入。
- 提交：`POST /v1/api/3d/submit`。
- 查询：`POST /v1/api/3d/query`，请求体必须包含 `model` 和 `id`。
- 默认域名：`https://tokenhub.tencentmaas.com`，鉴权为 `Authorization: Bearer` 加 API Key。
- 地域与环境变量沿用[共用 API 参考](api.md#provider-routing)，密钥不应写进命令或文件。
- 默认并发为 **1**；前一个 Express 任务结束后再提交下一个。此 CLI 每次处理一个任务，不提供跨进程队列。

## 来源与参数

文字、本地图片、公开 HTTPS 图片 URL 三选一，不能同时提供提示词和图片。提示词最多 1024 个字符。图片允许 JPEG/PNG/WebP，单边 128..5000 像素（含边界），本地文件按 6 MiB 上限校验；远程图片文档上限为 8 MB，客户端不下载远程输入做预验证。

| CLI 参数 | 请求字段 | 说明 |
| --- | --- | --- |
| --prompt | prompt | 中文正向描述 |
| --image | image_base64 | 原始 Base64 字符串，不加 data-URL 前缀 |
| --image-url | image_url | 公开 HTTPS 地址 |
| --enable-pbr | enable_pbr=true | 启用 PBR 材质 |
| --enable-geometry | enable_geometry=true | 白模，不生成纹理 |
| --result-format | result_format | obj、glb、stl、usdz、fbx、mp4 |

普通模式默认返回 OBJ；白模默认返回 GLB，白模不能指定 OBJ。本客户端也拒绝白模与 PBR 同时开启，避免要求生成无纹理模型却又启用材质。

需要 Blender 或可编辑模型时选 GLB；MP4 是视频格式，不能代替可编辑网格。文档的普通模式默认值为 OBJ，虽然查询示例同时展示了 OBJ/GLB 两项，也不能假定每次默认返回两种格式；应按实际 data 数组下载。

专业版的多视图、面数、拓扑类型、LowPoly、Sketch 不在本接口参数中，客户端会在提交前拒绝这些选项。默认 Normal 不发送 generate_type。使用 `--generate-type Geometry` 时映射为 `enable_geometry=true`，不会把专业版的 generate_type 字段发到 Express。

最小文字请求：

```json
{"model":"hy-3d-express","prompt":"一只小猫"}
```

图片白模请求结构：

```json
{"model":"hy-3d-express","image_url":"https://example.com/reference.png","enable_geometry":true,"result_format":"glb"}
```

## 命令

执行前解析脚本、参考图和输出目录为绝对路径。以下仅展示参数结构。

```powershell
# 先离线检查请求，不读取密钥、不联网
python scripts/hunyuan_3d.py generate --model express --prompt "一只小猫" --result-format glb --enable-pbr --dry-run

# 明确授权后：单图生成带 PBR 的 GLB
python scripts/hunyuan_3d.py image-to-3d --model hy-3d-express --image reference.png --enable-pbr --result-format glb --output-dir output

# 生成白模，省略格式则默认 GLB
python scripts/hunyuan_3d.py image-to-3d --model express --image reference.png --enable-geometry --output-dir output

# 恢复同一任务，模型名不可误写为 3.1
python scripts/hunyuan_3d.py query JOB_ID --model express --wait --download --output-dir output
```

## 查询与结果

查询体为 `{"model":"hy-3d-express","id":"TASK_ID"}`。任务状态为 queued、in_progress、completed、failed；CLI 统一显示 WAIT、RUN、DONE、FAIL。结果位于 data 数组，每项包含 type、url，可附 preview_image_url。任务 ID 有效期为 24 小时，成功后及时下载。

沿用专业版的失败恢复原则：一次明确授权的任务只提交一次；有 ID 后只查询、下载；提交结果不确定时先查控制台任务记录，不自动重试、换模型或降级。共享的下载逻辑会保留已经完成的文件，并处理过期链接、部分下载和重复预览。
