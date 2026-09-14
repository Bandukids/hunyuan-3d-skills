# Hunyuan 3D Skills

[![CI](https://github.com/Bandukids/hunyuan-3d-skills/actions/workflows/ci.yml/badge.svg)](https://github.com/Bandukids/hunyuan-3d-skills/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/Python-3.10%2B-blue)

面向 Codex 的腾讯混元 3D 技能集合：从文字或图片生成模型，继续处理网格、UV、纹理、骨骼和动作。通过腾讯云 TokenHub API 调用，客户端只依赖 Python 标准库，也可以在终端独立运行。

**Codex skills and Python clients for Tencent Hunyuan 3D generation, mesh processing, materials, rigging, and text-to-motion.**

[快速上手](#快速上手) · [安装到 Codex](#安装到-codex) · [凭据与使用](#凭据与使用) · [已知边界](#已知边界) · [参与贡献](CONTRIBUTING.md)

## 能力

| 技能 | 当前状态 | 内容 |
|---|---|---|
| [hunyuan-3d-generator](skills/hunyuan-3d-generator/SKILL.md) | 已实现 | 专业版 3.1/3.0、Express 的文字/图片生成，多视图、查询与下载 |
| [hunyuan-3d-mesh](skills/hunyuan-3d-mesh/SKILL.md) | 已实现，网格 API 尚未实测 | 组件拆分、智能减面/重拓扑、格式转换、查询与下载 |
| [hunyuan-3d-material](skills/hunyuan-3d-material/SKILL.md) | 已实现，材质 API 尚未实测 | 文字/图片生成纹理，自动 UV 展开，PBR、保留 UV、多视图、查询与下载 |
| [hunyuan-3d-animation](skills/hunyuan-3d-animation/SKILL.md) | 已实现，动画 API 尚未实测 | 绑骨蒙皮、人形动作模板、文生动作、受限重定向对象透传、查询与下载 |

材质技能当前支持纹理生成和自动 UV 展开，烘焙待接入。动画技能支持绑骨蒙皮、动作模板和文生动作；重定向仅保留已确认原生对象的透传入口，不支持任意骨架。

## 快速上手

需要 Python **3.10+** 和 Git。先克隆仓库并进行一次不联网的参数预检，无需 API Key：

```powershell
git clone https://github.com/Bandukids/hunyuan-3d-skills.git
cd hunyuan-3d-skills
python skills/hunyuan-3d-generator/scripts/hunyuan_3d.py generate --prompt "一只原创卡通小猫" --dry-run
```

预检会输出请求的模型、地址及脱敏参数。真实生成时配置下文的凭据，去掉 `--dry-run`，并用 `--output-dir` 指定结果目录。真实调用会使用腾讯云账户的 API 额度。

## 安装到 Codex

将 `skills/` 下的四个技能目录复制到 Codex 的技能目录：默认 `~/.codex/skills/`，自定义时使用 `$CODEX_HOME/skills/`。也可以只安装生成技能；其他三个技能都依赖生成技能。

<details>
<summary>Windows PowerShell 首次安装命令</summary>

在仓库根目录执行。命令遇到已安装的同名技能会停止，避免覆盖本地修改。

```powershell
$skillsTarget = if ($env:CODEX_HOME) {
    Join-Path $env:CODEX_HOME 'skills'
} else {
    Join-Path $env:USERPROFILE '.codex/skills'
}
$skillNames = @('hunyuan-3d-generator', 'hunyuan-3d-mesh', 'hunyuan-3d-material', 'hunyuan-3d-animation')
foreach ($skillName in $skillNames) {
    if (Test-Path -LiteralPath (Join-Path $skillsTarget $skillName)) {
        throw "技能已存在：$skillName。请先备份，再手动更新对应目录。"
    }
}
New-Item -ItemType Directory -Path $skillsTarget -Force | Out-Null
foreach ($skillName in $skillNames) {
    Copy-Item -LiteralPath (Join-Path 'skills' $skillName) -Destination $skillsTarget -Recurse
}
```

</details>

更新时先备份已安装技能，在克隆目录执行 `git pull --ff-only`，再同步对应目录。客户端的独立运行方式不需要复制安装。

`hunyuan-3d-mesh`、`hunyuan-3d-material` 和 `hunyuan-3d-animation` 必须与最新版 `hunyuan-3d-generator` 一同安装，保持以下结构；三者复用相邻生成技能的鉴权、提交、轮询和下载实现。材质客户端会检查共用客户端是否支持纹理图片脱敏。

```text
skills/
  hunyuan-3d-generator/
  hunyuan-3d-mesh/
  hunyuan-3d-material/
  hunyuan-3d-animation/
```

## 凭据与使用

客户端优先读取进程环境中的 `TOKENHUB_API_KEY`，兼容 `HUNYUAN_3D_API_KEY` 和 `TENCENT_HUNYUAN_API_KEY`；不会自动读取 `.env` 文件。密钥须在运行客户端的进程环境中可用，勿写入仓库、命令行参数或日志。旧 AI3D 接口仅在显式指定 `--provider legacy` 时使用。

PowerShell 中可通过隐藏输入配置当前终端，再检查鉴权：

```powershell
$tokenInput = Read-Host 'TokenHub API Key' -AsSecureString
$env:TOKENHUB_API_KEY = [System.Net.NetworkCredential]::new('', $tokenInput).Password
Remove-Variable tokenInput
python skills/hunyuan-3d-generator/scripts/hunyuan_3d.py check-auth
```

`check-auth` 只读取模型目录，不提交生成任务；它不能证明某个模型的额度或权限充足。完整参数使用各客户端的 `--help` 或对应 [SKILL.md](skills/hunyuan-3d-generator/SKILL.md) 查看。

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

## 已知边界

- 网格、材质和动画接口已完成客户端接入与离线测试，尚未进行付费云端实测。测试通过不能代替实际资产质量检查。
- 模型处理接口通常要求可访问的 HTTPS 模型链接；输入格式各不相同。本地模型不能仅凭改扩展名或本地路径直接提交。
- [纹理多视图](skills/hunyuan-3d-material/references/texture.md)的版本选择、[动作重定向对象](skills/hunyuan-3d-animation/references/motion.md)的内部结构存在官方文档缺口，客户端不会猜测未定义字段。
- 减面档位不等于精确面数。UV、材质、蒙皮和动作效果应在 Blender 或目标引擎中检查。
- 每个操作是独立云端任务，按需组合。断线或超时后保留原任务 ID、模型和地域恢复，不重复提交代替查询。

## 测试与贡献

在仓库根目录运行全部离线测试：

```powershell
python -B -m unittest discover -s skills/hunyuan-3d-generator/scripts -p "test_*.py" -v
python -B -m unittest discover -s skills/hunyuan-3d-mesh/scripts -p "test_*.py" -v
python -B -m unittest discover -s skills/hunyuan-3d-material/scripts -p "test_*.py" -v
python -B -m unittest discover -s skills/hunyuan-3d-animation/scripts -p "test_*.py" -v
```

当前共 200 项离线测试（生成客户端 57 项，网格客户端 56 项，材质客户端 49 项，动画客户端 38 项），模拟网络并阻止实际连接。测试通过不代表云端模型权限、额度或资产质量已验证。

[GitHub Actions](https://github.com/Bandukids/hunyuan-3d-skills/actions/workflows/ci.yml) 在 Windows / Ubuntu、Python 3.10 / 3.13 上执行四套测试。CI 不配置 TokenHub 密钥，不运行付费生成任务。

遇到问题可提交 [Issue](https://github.com/Bandukids/hunyuan-3d-skills/issues/new/choose)；修改代码或补充接口请阅读 [贡献说明](CONTRIBUTING.md)。提交反馈前移除密钥、签名 URL 和私人素材链接。

## API 文档

| 功能 | 腾讯云调用指南 |
|---|---|
| 模型生成 | [专业版](https://cloud.tencent.com/document/product/1823/137181) · [Express](https://cloud.tencent.com/document/product/1823/137175) |
| 网格处理 | [组件拆分](https://cloud.tencent.com/document/product/1823/137176) · [减面](https://cloud.tencent.com/document/product/1823/137778) · [格式转换](https://cloud.tencent.com/document/product/1823/137182) |
| 材质 | [纹理](https://cloud.tencent.com/document/product/1823/137183) · [UV 展开](https://cloud.tencent.com/document/product/1823/137777) |
| 动画 | [绑骨蒙皮](https://cloud.tencent.com/document/product/1823/137779) · [文生动作](https://cloud.tencent.com/document/product/1823/137780) |

具体参数限制、文档差异和调用示例位于各技能的 `references/`。仓库包含技能源码、说明和测试，生成素材、任务记录和凭据留在使用者自己的工作区。
