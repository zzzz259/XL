# 渠道登录研究归档

这里保存 Bilibili 和 TapTap 登录接入过程中形成的证据、失败分支及基线修正记录，不包含账号凭据、客户端接入密钥、签名文件或完整第三方源码。

日常使用与开发请先阅读[渠道登录说明](../gacha-channel-login.md)、[用户指南](../gacha-user-guide.md)和[构建与测试](../gacha-development.md)。Android 当前发布版本为 0.3.6，TapTap 真机授权修复已合入 `debug`；授权后仍需手动返回手记。Windows 最后版本为 0.3.4，已停止维护。0.4 用于后续信息组织与 UI 优化。

## 历史材料

- [Bilibili 渠道历史进度与架构](bilibili-channel-context.md)
- [Bilibili 渠道差异](bilibili-channel-diff.md)
- [Bilibili 接入可行性](bilibili-channel-feasibility.md)
- [Bilibili 授权验证过程](bilibili-channel-authorization.md)
- [TapTap App 研究与产品接入](taptap-app-login.md)
- [0.3.5 基线整合记录](../releases/gacha-v0.3.5.md)

这些材料按研究时间保留。早期的“未实现”“测试包”结论可能已由后续记录更新，不能直接据此判断当前版本能力。最终行为以当前源码、对应提交和验收证据为准。

实验脚本、研究包、提取物和临时结果保存在仓库外的 `E:\AI-Agent\OpenAI-Codex`。未接入产品的 `starfall_credential_probe`、运行生成的根目录 `Maps`、原始包体和 `local-research` 仅留本机，由根 `.gitignore` 排除；不作为产品代码发布。
