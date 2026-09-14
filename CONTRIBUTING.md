# 参与贡献

欢迎通过 Issue 提交可复现问题、腾讯云官方文档更新或功能建议。每个 PR 聚焦一个问题，先说明触发场景与预期行为，再给出改动及验证结果。

## 本地开发

运行依赖为 Python 3.10+，无需安装第三方包。四个技能保持同级；网格、材质和动画客户端复用生成技能的鉴权、任务状态和下载逻辑。

```sh
python -B -X utf8 -m unittest discover -s skills/hunyuan-3d-generator/scripts -p "test_*.py" -v
python -B -X utf8 -m unittest discover -s skills/hunyuan-3d-mesh/scripts -p "test_*.py" -v
python -B -X utf8 -m unittest discover -s skills/hunyuan-3d-material/scripts -p "test_*.py" -v
python -B -X utf8 -m unittest discover -s skills/hunyuan-3d-animation/scripts -p "test_*.py" -v
```

修改单个客户端时运行对应测试；修改共享生成客户端时运行全部测试。测试使用模拟 HTTP 并阻止外部连接，不需要密钥；不要为 CI 添加真实 TokenHub 调用。

## 接口和说明

- 以腾讯云对应模型的官方文档为依据，在 `references/` 记录链接、核对日期和未明确的行为。
- 保持每个模型的字段、默认值和约束独立。文档缺少结构时说明缺口，不复制相似接口的字段填补。
- 校验应在付费提交前完成；测试真实边界、请求内容、查询恢复和下载结果，避免只匹配代码写法。
- 保留既有命令兼容性。收到任务 ID 后恢复同一任务；提交结果不确定时不自动重试生成。
- 同步更新 `SKILL.md`、相关参考和 `agents/openai.yaml`，准确区分已接入、离线验证和云端实测。

## 提交内容

请附脱敏后的最小复现命令、Python/系统版本和测试结果。不要提交真实密钥、授权头、签名下载地址、完整环境变量或私人模型；用 `example.com` 和模拟响应复现即可。

仓库的 Issue 和 PR 模板提供了简短填写结构。是否接受新的模型能力及接口变更，以可核对的文档和测试为依据。
