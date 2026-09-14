# Hunyuan 3D Skills

面向 Codex 的腾讯混元 3D 技能集合，使用 TokenHub API。技能目录均位于 `skills/`，安装时保持同级。

| 技能 | 当前状态 | 内容 |
|---|---|---|
| [hunyuan-3d-generator](skills/hunyuan-3d-generator/SKILL.md) | 已实现 | 专业版 3.1/3.0、Express 的文字/图片生成，多视图、查询与下载 |
| [hunyuan-3d-mesh](skills/hunyuan-3d-mesh/SKILL.md) | 已实现，网格 API 尚未实测 | 组件拆分、智能减面/重拓扑、格式转换、查询与下载 |
| [hunyuan-3d-material](skills/hunyuan-3d-material/SKILL.md) | 已实现，材质 API 尚未实测 | 文字/图片生成纹理，自动 UV 展开，PBR、保留 UV、多视图、查询与下载 |
| [hunyuan-3d-animation](skills/hunyuan-3d-animation/SKILL.md) | 已实现，动画 API 尚未实测 | 绑骨蒙皮、人形动作模板、文生动作、受限重定向对象透传、查询与下载 |

材质技能当前支持纹理生成和自动 UV 展开，烘焙待接入。动画技能支持绑骨蒙皮、动作模板和文生动作；重定向仅保留已确认原生对象的透传入口，不支持任意骨架。

## 安装

需要 Python 3.10+，运行客户端无需第三方 Python 依赖。

```powershell
git clone https://github.com/Bandukids/hunyuan-3d-skills.git
cd hunyuan-3d-skills
```

将 `skills/` 中需要的技能目录复制到 Codex 的技能目录（默认 `~/.codex/skills/`，自定义时使用 `$CODEX_HOME/skills/`）。更新已有技能前先保留本地修改。

`hunyuan-3d-mesh`、`hunyuan-3d-material` 和 `hunyuan-3d-animation` 必须与最新版 `hunyuan-3d-generator` 一同安装，保持以下结构；三者复用相邻生成技能的鉴权、提交、轮询和下载实现。材质客户端会检查共用客户端是否支持纹理图片脱敏。

```text
skills/
  hunyuan-3d-generator/
  hunyuan-3d-mesh/
  hunyuan-3d-material/
  hunyuan-3d-animation/
```

## 凭据与使用

客户端优先读取进程环境中的 `TOKENHUB_API_KEY`，兼容 `HUNYUAN_3D_API_KEY` 和 `TENCENT_HUNYUAN_API_KEY`。通过环境或安全凭据注入配置，勿将真实密钥写入仓库、命令行参数或日志。旧 AI3D 接口仅在显式指定 `--provider legacy` 时使用。

从仓库根目录执行离线预检：

```powershell
python skills/hunyuan-3d-generator/scripts/hunyuan_3d.py generate --prompt "一只原创卡通小猫" --dry-run
python skills/hunyuan-3d-mesh/scripts/hunyuan_mesh.py split --file-url "https://example.com/model.fbx" --dry-run
python skills/hunyuan-3d-mesh/scripts/hunyuan_mesh.py reduce --file-url "https://example.com/model.glb" --face-level medium --dry-run
python skills/hunyuan-3d-mesh/scripts/hunyuan_mesh.py convert --file-url "https://example.com/model.glb" --format fbx --dry-run
python skills/hunyuan-3d-material/scripts/hunyuan_material.py texture --file-url "https://example.com/model.glb" --prompt "青绿色釉面陶瓷" --enable-pbr --dry-run
python skills/hunyuan-3d-material/scripts/hunyuan_material.py uv --file-url "https://example.com/model.fbx" --dry-run
python skills/hunyuan-3d-animation/scripts/hunyuan_animation.py rig --file-url "https://example.com/character.glb" --dry-run
python skills/hunyuan-3d-animation/scripts/hunyuan_animation.py list-motions
python skills/hunyuan-3d-animation/scripts/hunyuan_animation.py motion --prompt "A person walks forward" --duration 5 --dry-run
```

示例链接仅用于演示。`--dry-run` 不联网、不读取密钥、不消耗额度；真实调用前按技能说明准备输入。恢复已有网格任务时保留原地域并正确指定 `--model component|retopology|format`，不要重新提交代替恢复。

材质技能的 `submit` 和 `query` 默认使用纹理模型，UV 任务须显式传 `--model uv`。UV 的输入尺寸/几何预检仅检查已知测量值，实际限制和单位说明见该技能的 `references/uv.md`。

动画技能的 `submit` 和 `query` 默认使用绑骨模型，文生动作须显式传 `--model motion`。文生动作的重定向来源要求及对象结构缺口见该技能的 `references/motion.md`。

## 验证

```powershell
python -B -m unittest discover -s skills/hunyuan-3d-generator/scripts -p "test_*.py" -v
python -B -m unittest discover -s skills/hunyuan-3d-mesh/scripts -p "test_*.py" -v
python -B -m unittest discover -s skills/hunyuan-3d-material/scripts -p "test_*.py" -v
python -B -m unittest discover -s skills/hunyuan-3d-animation/scripts -p "test_*.py" -v
```

当前共 200 项离线测试（生成客户端 57 项，网格客户端 56 项，材质客户端 49 项，动画客户端 38 项），模拟网络并阻止实际连接。测试通过不代表云端模型权限、额度或资产质量已验证。

原始 API 文档、参数限制和已知差异位于各技能的 `references/`。仓库仅包含技能源码、说明和测试，不包含生成素材、任务记录或凭据。
