# HY-3D-Component 接入参考

来源：[腾讯云组件生成调用指南](https://cloud.tencent.com/document/product/1823/137176)。核对日期：2026-09-13；文档更新日期：2026-09-08。实现已通过离线模拟验证，尚未对组件 API 进行真实付费调用。

## 请求与结果

TokenHub 使用 `Authorization: Bearer ...`。提交和查询都使用 POST：

- `/v1/api/3d/submit`
- `/v1/api/3d/query`

提交参数：

| 参数 | 类型 | 用法 |
|---|---|---|
| `model` | string，必填 | 固定 `hy-3d-component` |
| `file` | object，必填 | 按官方示例发送 `{"url":"https://example.com/model.fbx"}`；输入仅支持 FBX |
| `part_segmentation_info` | string，可选 | JSON 文本形式的分割数据；不是嵌套 JSON 对象 |
| `enable_staged_generation` | boolean，可选 | 分步骤生成，默认关闭 |
| `enable_post_process` | boolean，可选 | 后处理，默认关闭；官方说明只输出一个模型链接，额外增加 20 积分 |

不发送未指定的选项；不从其他 3D 模型复制 `result_format`、`face_count`、`enable_pbr` 或图片参数。

```json
{
  "model": "hy-3d-component",
  "file": {"url": "https://example.com/model.fbx"}
}
```

提交响应包含 `id`、`request_id`、`status`；任务 ID 有效期 24 小时。查询只需要：

```json
{"model":"hy-3d-component","id":"JOB_ID"}
```

状态：`queued` / `in_progress` / `completed` / `failed`。完成后 `data` 是数组，各项有 `type` 和 `url`；官方普通模式示例返回多个 GLB 链接。下载所有项，保留服务端实际后缀和组件名称；重名文件使用序号保存。不要承诺固定组件数或名称，也不要把相同 URL 的重复记录解释成不同几何组件。

## 分阶段生成

第一阶段显式开启开关：

```powershell
python scripts/hunyuan_mesh.py split --file-url "https://example.com/original.fbx" --enable-staged-generation --output-dir stage-one
```

保存服务端返回的模型、分割数据和任务 ID。检查实际返回内容后再编辑分割数据；与数据对应的模型要保留原有面索引。不得在两阶段之间减面、重拓扑、合并或三角化后仍沿用旧索引，也不能把其他模型的面编号套过来。

将编辑后的分割数据保存在 UTF-8 JSON 文件中，例如字段形状为：

```json
{"part_0":{"face_ids":[0,1,2]}}
```

这只是格式示意，编号必须来自对应服务端结果，不能把示例直接用于真实模型。客户端只检查 JSON 是非空对象、键不重复、数值有效，不擅自重命名组件或修改面编号；请求中的 `part_segmentation_info` 保持为字符串。

```powershell
python scripts/hunyuan_mesh.py split --file-url "https://example.com/model-with-segmentation.fbx" --part-segmentation-info edited-parts.json --dry-run
```

文档说明可将带分割信息的模型与编辑后的数据再次传入，但未完整定义分割数据 schema、首阶段各返回文件类型以及第二阶段开关组合。上面只演示数据参数传递，不代表已验证完整两阶段流程；首次实际操作应查看返回结果和最新说明，再按用户要求提交下一阶段。脚本独立传递开关和数据，不自动推断阶段或追加付费调用。

## 当前边界

- 官方页面没有列出 Base64、本地上传接口、输入文件大小或面数限制。本技能仅使用文档明确提供的 URL 形式。
- 离线校验保留签名 URL 的查询参数供请求使用，显示和日志中脱敏；不能仅靠 URL 证明 FBX 内容完整或云端可访问。
- 普通模式与后处理结果按服务端 `data` 下载。后处理“一个模型链接”不等于承诺对象合并方式、部件层级或拓扑质量。
- 凭据、官方域名校验、任务 ID 校验、单次提交、轮询、无鉴权资源下载及错误脱敏复用相邻 `hunyuan-3d-generator/scripts/hunyuan_3d.py`。保持共用客户端 API v1，不单独复制一套实现。
