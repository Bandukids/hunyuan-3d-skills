# HY-3D-Format 格式转换

来源：[腾讯云 HY-3D-Format 调用指南](https://cloud.tencent.com/document/product/1823/137182)。核对：2026-09-13；页面更新：2026-09-08。本实现通过离线模拟验证，尚未提交真实转换任务。

## 请求

使用 Bearer 鉴权，提交 `POST /v1/api/3d/submit`。输入为 OBJ / GLB / FBX 链接，输出支持 STL / USDZ / FBX / MP4 / GIF / OBJ / GLB。`model`、`file`、`format` 均必填：

```json
{
  "model": "hy-3d-format",
  "file": {"url": "https://example.com/model.glb"},
  "format": "FBX"
}
```

目标 `format` 按文档大写发送。不要使用减面的 `file_3d`、生成的 `result_format`，或添加未定义的输入 `type`。输入不支持 STL、USDZ、MP4、GIF、GLTF、ZIP；不要从输出枚举推断输入支持范围。

## 输入大小

文档原文为“模型文件大小≤60m”，没有定义十进制或二进制字节数。客户端在提供 `--input-size-bytes` 时采用保守上限 **60,000,000 字节**，这是本地校验约定，不是官方精确字节定义。

从对应本地文件或获授权的远端元数据核实大小后，才能使用该参数。它不会读取/上传本地文件，不会联网测量 URL，也不会把大小加入请求体。不传时大小由服务端最终校验，不能宣称离线预检已确认大小；不得把此限制套用到拆分或减面接口。

## 命令与恢复

```powershell
python scripts/hunyuan_mesh.py convert --file-url "https://example.com/model.glb" --format fbx --input-size-bytes 24000000 --dry-run
python scripts/hunyuan_mesh.py convert --file-url "https://example.com/model.glb" --format fbx --output-dir converted
python scripts/hunyuan_mesh.py submit --model format --file-url "https://example.com/model.obj" --format stl
python scripts/hunyuan_mesh.py query JOB_ID --model format --wait --download --output-dir converted
```

`convert` 默认模型为 `format`；`submit` / `query` 默认仍为组件模型，需要显式指定 `--model format`（也接受 `hy-3d-format`）。命令目标与模型冲突会在提交前失败，不自动改做其他操作。

查询 `POST /v1/api/3d/query`：

```json
{"model":"hy-3d-format","id":"JOB_ID"}
```

默认并发 1，任务 ID 有效期 24 小时。恢复时保持模型与地域；超时或下载失败后查询同一 ID，不能重新提交以代替恢复。

## 结果兼容与用途

文档查询示例的 `data` 使用 `type`，数据结构表使用 `format`。下载器兼容二者；URL 缺少后缀时可按已知格式补齐，已有文件名后缀则保留。所有下载都不携带 API 鉴权头。

MP4/GIF 是媒体文件，不等于可编辑骨骼动画；STL 主要用于几何交付，不能据此保证材质保留。格式接口未提供帧率、时长、相机、纹理烘焙或骨骼参数，不添加此类选项。用户需要保留层级、贴图、蒙皮时应检查转换结果，不承诺无损。
