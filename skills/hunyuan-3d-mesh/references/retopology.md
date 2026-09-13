# HY-3D-Retopology 智能减面

来源：[腾讯云 HY-3D-Retopology 调用指南](https://cloud.tencent.com/document/product/1823/137778)。核对日期：2026-09-13；文档更新日期：2026-09-09。官方介绍采用 Polygon 1.5，将高模转为布线规整、面数较低的模型。本实现已做离线模拟验证，尚未调用真实减面任务。

## API 参数

Bearer 鉴权、地域选择、任务轮询与下载沿用共用客户端。提交为 `POST /v1/api/3d/submit`，查询为 `POST /v1/api/3d/query`。模型名固定为 `hy-3d-retopology`，不要误写成 `hy-3d-component` 或 `polygon-1.5`。

| 参数 | 类型 | 说明 |
|---|---|---|
| `model` | string，必填 | `hy-3d-retopology` |
| `file_3d` | object，必填 | 输入模型对象，不能使用组件接口的 `file` 字段 |
| `file_3d.url` | string，必填 | 可访问的 OBJ / GLB / FBX 链接 |
| `file_3d.type` | string，可选 | `obj` / `glb` / `fbx`，CLI 用 `--file-type` 显式传入 |
| `polygon_type` | string，可选 | `triangle` / `quadrilateral`；服务端默认 `triangle` |
| `face_level` | string，可选 | 输出面数档位 `high` / `medium` / `low`；没有公布具体数值或默认档位 |

最简请求按官方示例省略所有可选参数：

```json
{
  "model": "hy-3d-retopology",
  "file_3d": {"url": "https://example.com/high-poly.glb"}
}
```

指定输入类型、四边面和低面数档位：

```json
{
  "model": "hy-3d-retopology",
  "file_3d": {"url": "https://example.com/high-poly.fbx", "type": "fbx"},
  "polygon_type": "quadrilateral",
  "face_level": "low"
}
```

`face_level` 描述的是减面后面数档位，不是保留比例。不能把 `high` 当成“最大减面强度”，也不能编造 `low=5000` 之类映射。需要精确面数或三角面预算时应在实际结果上统计，必要时另行选择可控制面数的处理方式。

## CLI 与恢复

```powershell
python scripts/hunyuan_mesh.py reduce --file-url "https://example.com/high-poly.glb" --face-level medium --polygon-type triangle --dry-run
python scripts/hunyuan_mesh.py reduce --file-url "https://example.com/high-poly.glb" --face-level medium --polygon-type triangle --output-dir reduced
python scripts/hunyuan_mesh.py submit --model retopology --file-url "https://example.com/high-poly.fbx" --face-level low
python scripts/hunyuan_mesh.py query JOB_ID --model retopology --wait --download --output-dir reduced
```

`retopology` 是 `reduce` 的别名；两者默认使用减面模型，显式指定其他模型会报错。`--model` 接受 `retopology` / `hy-3d-retopology`。`submit` 默认仍是组件模型，提交减面任务时必须指定模型；`query` 也必须携带正确模型。

无后缀的签名下载端点可用 `--file-type glb` 等明确输入类型。脚本保留原签名查询参数供服务端读取，显示时脱敏；离线校验不能证明远端内容或链接有效期。URL 明确后缀与 `--file-type` 不一致时会在提交前失败。

减面不支持组件分阶段/后处理参数；`--face-count`、`--ratio`、`--result-format` 未在文档中定义，不发送。未指定 `--face-level` 时直接省略，不能将示例值 `high` 当作默认值。

## 状态与结果

提交返回 `id`、`request_id`、`status` 等字段。查询体仅为：

```json
{"model":"hy-3d-retopology","id":"JOB_ID"}
```

状态为 `queued` / `in_progress` / `completed` / `failed`。默认并发 1，任务 ID 有效期 24 小时。超时或下载失败后恢复原任务；提交结果不确定时不自动重提。

完成后的 `data` 数组各项包含 `type` 和 `url`，官方示例包括 OBJ、GLB 和 PNG 图片。下载全部实际返回资源，保留名称与格式；不要假设必定返回这三种资源，也不要把附属图片直接认定为完整 PBR 材质组。

## 质量与流程边界

- 保留原高模及资源，使用独立输出目录。四边面数和导出后的三角面数是不同指标，针对游戏预算需要统计三角化后的数量。
- 检查轮廓、薄片、孔洞和关节处拓扑，以及 UV/贴图是否仍匹配；文档未承诺纹理、蒙皮、动画或组件层级完整保留。
- 重拓扑改变面索引，不能沿用原模型的 `part_segmentation_info`。如果用户要求拆分后减面，应明确对象顺序并让每一步使用对应的模型与数据，不能自动追加任务。
- 文档未给出本地上传/Base64 接口、输入大小或面数上限。OBJ 材质关联与外部贴图的打包规则也未明确，不以 ZIP 代替 OBJ 输入。
