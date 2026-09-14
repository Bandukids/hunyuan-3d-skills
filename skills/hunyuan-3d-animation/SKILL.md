---
name: hunyuan-3d-animation
description: Rig and skin FBX/GLB characters with Tencent TokenHub HY-3D-Rigging. Use for humanoid or animal rigging, optional humanoid motion templates, task queries, and downloading rigged models. Text-to-motion and custom retargeting are not implemented.
---

# 混元 3D 动画

当前接入 **HY-3D-Rigging 绑骨蒙皮**，支持人物或动物模型，另可显式选择人形动作模板。文生动作和自定义重定向尚未接入；模型生成、网格处理、UV/纹理分别使用同系列 generator、mesh、material 技能。

## 输入和动作选择

- 输入是可访问的 HTTPS FBX/GLB 链接；OBJ、本地路径不能直接提交。需要时先准备格式副本，使用用户指定或已授权的存储，不自动上传到第三方。
- 人形输入按文档使用 A Pose 或 T Pose，避免附带坐骑、翅膀和复杂松散部件。动物等非人形采用单一生命体、简洁姿态；形态和姿态需检查模型，不能仅凭扩展名判断。详细来源和范围见 [references/rigging.md](references/rigging.md)。
- 默认只请求绑骨蒙皮，不发送 `motion_type`。用户明确需要模板动作时，用 `list-motions` 查编号，再传 `--motion-type 1..48`；不将模板视为自由文本动作生成。
- 非人形不支持动作模板。`--character-type humanoid|non-humanoid` 只记录已知分类用于本地校验，非人形与模板组合会被拒绝；未填不代表脚本已确认人形。选择模板前依据模型或已知上下文核对分类。
- `--input-size-bytes` 可检查已核验的文件大小，按文档 60mb 保守采用 60,000,000 字节上限。它和角色分类都不发送给 API，不填猜测值；预检不下载远端模型，也不检查姿态、权重或真实文件大小。

## 执行与恢复

1. 使用 Python 3.10+，与 `hunyuan-3d-generator` 同级安装，共用客户端 API 版本 1；无需第三方库。执行时将脚本、输入输出解析为绝对路径。
2. 凭据优先读进程环境 `TOKENHUB_API_KEY`，兼容 `HUNYUAN_3D_API_KEY` / `TENCENT_HUNYUAN_API_KEY`。不要把真实密钥写入脚本、命令行或日志；本技能只支持 TokenHub。
3. 准备输入后用 `--dry-run` 预检；用户已授权绑骨且输入充分时提交一次。仅要求完善技能时不调用付费接口。`rig`（别名 `rigging`）提交、等待并下载；`submit` 只提交。
4. 收到 ID 后保留 ID、模型 `rigging` 和地域。默认并发 1，轮询至少间隔 5 秒；任务 ID 有效期 24 小时。中断、超时或下载失败后查询原任务；提交结果不确定或缺少 ID 时先检查服务端记录，不自动重提。
5. 下载完成响应中全部资源。区分云端任务完成、文件下载和绑骨质量检查；需要检查时在 Blender 导入副本，核对骨架、蒙皮权重、变形，选过模板时再检查动作。未检查不能保证引擎骨架兼容或蒙皮质量。

## 命令

以下展示命令结构；执行时使用绝对路径。`example.com` 为占位链接。

```powershell
python scripts/hunyuan_animation.py rig --file-url "https://example.com/character.glb" --dry-run
python scripts/hunyuan_animation.py rig --file-url "https://example.com/animal.fbx" --character-type non-humanoid --output-dir rigged
python scripts/hunyuan_animation.py list-motions
python scripts/hunyuan_animation.py rig --file-url "https://example.com/character.glb" --character-type humanoid --motion-type 23 --output-dir walking
python scripts/hunyuan_animation.py submit --file-url "https://example.com/character.fbx"
python scripts/hunyuan_animation.py query JOB_ID --model rigging --wait --download --output-dir rigged
python scripts/hunyuan_animation.py check-auth
```

`--model rigging` / `--model hy-3d-rigging` 均可，默认已是绑骨模型。`--base-url` 使用共用客户端的官方 TokenHub 地域白名单，默认广州，恢复时保留原地域。`check-auth` 只读取模型目录，不证明特定模型权限或额度充足。

## 维护

- 请求字段、输入要求和模板来源见 [references/rigging.md](references/rigging.md)。
- 在本技能目录运行 `python -B -m unittest discover -s scripts -p 'test_*.py' -v`，模拟 HTTP 并阻止真实连接；用 skill-creator 的 `quick_validate.py` 检查结构。
- 若修改共享生成客户端，同时运行同系列其他技能的回归测试。
