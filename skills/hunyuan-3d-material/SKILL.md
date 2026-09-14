---
name: hunyuan-3d-material
description: Generate texture maps for existing OBJ/GLB geometry with Tencent TokenHub HY-3D-Texture. Use for text- or image-guided texturing, PBR, preserving existing UVs, multi-view texture references, task queries, and downloading model/material assets. UV unwrapping and baking are not implemented.
---

# 混元 3D 材质

当前接入 **HY-3D-Texture 纹理贴图生成**：已有单几何 OBJ/GLB 模型，配文字描述或参考图生成纹理。模型生成使用 `hunyuan-3d-generator`，拆分/减面/格式转换使用 `hunyuan-3d-mesh`；本技能尚未接入独立 UV 展开、烘焙或动画。

## 输入和选择

- 模型为可访问的 HTTPS OBJ/GLB 链接，请求字段为 `file_3d.url`。FBX、ZIP、本地路径不能直接提交；需要时先准备相应格式的副本，再使用用户指定或已授权的存储链接，不自动上传到第三方。
- 文字与主参考图必选其一，不能同时传。文字最多 200 字符，优先中文正向材质描述；本地参考图支持 JPEG/PNG，使用请求体 `image.base64`，远端图使用 `image.url`。不要发送专业版的顶层 `image_base64` / `image_url`。
- `--enable-pbr` 请求 PBR 材质；`--enable-keep-uv` 请求保留已有 UV。两者默认关闭。用户需要保留既有 UV 时先确认模型具备所需 UV；保留 UV 与 UV 展开是不同任务。
- `--texture-size` 接受 720～4096 的整数，表示正方形贴图边长，未指定使用服务端默认 4096；不额外限制为 2 的幂。
- 多视图使用 `--view VIEW=FILE_OR_HTTPS_URL`；每个视角只传一张。纹理接口的视图字段是 `view` / `image`，与生成接口不同。使用前阅读 [references/texture.md](references/texture.md)，其中记录了版本说明缺口及与主参考图不同的尺寸/容量限制。

## 执行与恢复

1. 使用 Python 3.10+。依赖相邻安装的最新版 `hunyuan-3d-generator`，共用客户端须支持纹理图片脱敏；运行时不需要第三方库。解析脚本、输入和输出为绝对路径。
2. 凭据优先读取进程环境变量 `TOKENHUB_API_KEY`，兼容 `HUNYUAN_3D_API_KEY` / `TENCENT_HUNYUAN_API_KEY`。真实密钥不进入脚本、命令行参数或日志；仅支持 TokenHub，不混用旧 AI3D 密钥或任务。
3. 检查模型来源及参考图，再用 `--dry-run` 校验。离线预检检查本地图片和参数，不联网，也不能验证远端模型是否单几何、UV 是否合适或远端图片的真实尺寸/总量。对未知项说明验证范围。
4. 用户已授权纹理生成且输入充分时提交一次；仅要求接入 API 或完善技能时不提交付费任务。`texture` 提交、等待并下载；`submit` 只提交。收到 ID 后报告任务 ID、模型和地域。
5. 默认并发 1，轮询至少间隔 5 秒。中断/超时/下载失败后用原 ID、模型 `texture` 和原地域查询；提交结果不确定或没有 ID 时先检查服务端记录，不自动重提，也不因多视图版本问题偷偷删除视图重做。
6. 任务 ID 有效期 24 小时，完成后及时下载 `data` 中全部模型、图片、纹理及 MTL 资源，保留原有资源关系。返回文件绝对路径，并区分云端完成、下载完成和材质检查完成。
7. 需要质量检查时在 Blender 导入副本，核对贴图绑定、UV 接缝和 PBR 通道；预览图不能当作完整材质组。未检查就不保证贴图完整、UV 完全一致或游戏就绪。

## 命令

以下从技能目录展示命令结构；实际运行使用已解析的绝对路径，`example.com` 是占位链接。

```powershell
python scripts/hunyuan_material.py texture --file-url "https://example.com/model.glb" --prompt "青绿色釉面陶瓷，细腻裂纹" --enable-pbr --texture-size 2048 --dry-run
python scripts/hunyuan_material.py texture --file-url "https://example.com/model.glb" --prompt "青绿色釉面陶瓷，细腻裂纹" --enable-pbr --texture-size 2048 --output-dir textures
python scripts/hunyuan_material.py texture --file-url "https://example.com/model.obj" --image reference.png --enable-keep-uv --output-dir textures
python scripts/hunyuan_material.py submit --file-url "https://example.com/model.glb" --image-url "https://example.com/reference.png"
python scripts/hunyuan_material.py query JOB_ID --wait --download --output-dir textures
python scripts/hunyuan_material.py check-auth
```

`--model texture` / `--model hy-3d-texture` 均可，默认已是纹理模型。`--base-url` 采用共用客户端的 TokenHub 官方地域白名单，默认广州；`check-auth` 只读取模型目录，不证明特定模型权限或额度充足。

## 维护

- 图片限制、视图 schema 和服务端差异：[references/texture.md](references/texture.md)。
- 从本技能目录运行 `python -B -m unittest discover -s scripts -p 'test_*.py' -v`，测试模拟网络并阻止真实连接。
- 修改共用客户端时同时运行生成与网格技能回归测试；用 skill-creator 的 `quick_validate.py` 检查结构。
