# Changelog

本文件记录 `dwImage` 的重要变更。

格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)。

## [Unreleased]

### Planned

- 优化 GitHub 展示素材与截图
- 完善发布包与版本发布流程

## [v0.1.0] - 2026-06-02

### Added

- 基于 `PySide6` 的 Windows 桌面客户端
- 文生图能力
- 参考图生成 / 编辑工作流
- 多提示词批量生成
- 多参考图批量生成
- 同提示词与同配置下按参考图逐张生成
- 窗口任意位置拖拽图片导入
- 粘贴剪贴板图片与图片文件
- 本地历史记录、收藏夹、请求日志
- 多 API Profile 管理
- Prompt Optimization 配置
- `Images API` 与 `Responses API` 兼容支持

### Changed

- 应用名称统一为 `dwImage`
- 本地数据目录切换为 `%LOCALAPPDATA%\\dwImage`
- 增加旧版本地配置自动迁移逻辑
- 生成状态与错误反馈改为更适合桌面端的展示方式

### Notes

- 当前版本仅面向 Windows 使用
- 首个公开版本重点是跑通本地工作流与批量生成体验
