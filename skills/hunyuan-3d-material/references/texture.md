# HY-3D-Texture 接入参考

来源：[腾讯云纹理生成调用指南](https://cloud.tencent.com/document/product/1823/137183)。核对：2026-09-14；文档更新：2026-09-09。已做离线模拟验证，尚未提交真实纹理生成任务。

## 请求与图片限制

提交 `POST /v1/api/3d/submit`，模型为 `hy-3d-texture`。最简参考图请求：

```json
{
  "model": "hy-3d-texture",
  "file_3d": {"url": "https://example.com/model.obj"},
  "image": {"url": "https://example.com/reference.png"}
}
```

文字使用 `prompt`（最多 200 字符），与 `image` 互斥。本地参考图发送 `image.base64` 原始 Base64；文档同时提到顶层 `image_url`，但参数表和示例使用 `image` 对象，客户端遵循后者。模型只支持 OBJ/GLB；不添加未定义的 `file_3d.type`。

| 图片 | 单边尺寸 | 格式 | 容量 |
|---|---|---|---|
| 主参考图 | 129～4095 像素 | JPEG / PNG | Base64 严格小于 10mb |
| 附加视图 | 129～4999 像素 | JPEG / PNG | 全部图片合计不超过 6M 原始 / 8M 编码后 |

文档未明确 M/mb 的字节进制。客户端保守采用十进制：主图编码后 `<10,000,000`；多视图模式下主图和附加视图的已知本地数据合计 `≤6,000,000` 原始字节、`≤8,000,000` Base64 字节。远端图片内容不在离线计数中，不能据此宣称远端或混合来源已通过总量检查。

可选 `enable_pbr`、`enable_keep_uv` 默认 false；`texture_size` 是正方形贴图边长，整数 720～4096，默认 4096。未指定的选项不发送。

## 多视图

```powershell
python scripts/hunyuan_material.py texture --file-url "https://example.com/model.glb" --image front.png --view "left=left.png" --view "back=https://example.com/back.png" --enable-pbr --dry-run
```

纹理视图对象为 `{"view":"left","image":"URL_OR_RAW_BASE64"}`；不要套用专业生成接口的 `view_type` / `view_image_base64`。可选视角：left、right、back、top、bottom、left_front、right_front，各限一张；主图单独通过 `image` 传入。仅有附加视图不能代替必需的文字或主图。

文档注明多视图仅 3.1 支持，却没有提供版本选择字段。客户端保持 `model=hy-3d-texture`，不编造 `version` 或模型后缀，也不保证账户后端一定支持多视图。文字与附加视图的组合未被文档明确禁止，客户端只执行明确的主来源互斥校验；组合效果需实测。遭遇版本/参数拒绝时报告服务端信息，不自动换模型或再次付费提交。

## 查询与资源

查询 `POST /v1/api/3d/query`，请求为 `{"model":"hy-3d-texture","id":"JOB_ID"}`。恢复命令默认使用纹理模型，保持原地域。

完成后下载 `data` 的所有 `type` / `url` 项。官方示例包括 OBJ、GLB、图片、纹理图片和 MTL；实际结果可能不同。OBJ/MTL/贴图应一起保存，MTL 的纹理路径须在导入时核对。开启 PBR 不代表固定返回特定文件名或完整通道集合。

该能力为已有几何生成纹理，不等于单独 UV 展开或烘焙。`enable_keep_uv` 用于请求保留已有 UV，实际结果仍应比对；不要把参数描述误解为生成后颜色与贴图内容不会改变。
