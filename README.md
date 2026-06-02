# dwImage

`dwImage` 是一个基于 `PySide6` 构建的 Windows 桌面图像生成客户端。

它面向本地高频使用场景，围绕文生图、参考图编辑、批量生成、提示词优化、历史记录与本地管理做了桌面化封装。

当前仓库版本为 **Windows 专用**。

## 功能特性

- 文生图
- 参考图编辑 / 重绘
- 兼容 `Images API`
- 兼容 `Responses API`
- 支持多 API 配置
- 支持提示词优化配置
- 支持多提示词批量生成
- 支持多参考图批量生成
- 支持外部图片拖入窗口任意位置
- 支持粘贴外部图片或已复制的图片文件
- 支持本地历史记录、收藏夹、请求日志

## 批量生成逻辑

`dwImage` 当前支持两类批量能力：

### 1. 多提示词批量

- 批量提示词模式下，**每行一个提示词**
- 所有提示词共用当前配置和参考图

### 2. 多参考图批量

- `合并参考图一起生成`
  - 多张参考图会作为同一次请求的附件
- `每张参考图单独生成`
  - 每张参考图都会拆分成独立任务

## 运行环境

- Windows 10 / 11
- Python 3.11 及以上版本更推荐

## 快速开始

### 1. 克隆仓库

```bash
git clone https://github.com/<your-name>/dwImage.git
cd dwImage
```

### 2. 安装依赖

```bash
pip install -r requirements.txt
```

### 3. 启动程序

```bash
python app.py
```

## 本地数据目录

程序运行数据默认保存在：

```text
%LOCALAPPDATA%\dwImage
```

常见内容包括：

- `settings.json`
- `mint_image.db`
- `request_logs.jsonl`
- `generated_images/`

## 项目结构

```text
dwImage/
├─ app.py
├─ requirements.txt
├─ dwimage/
│  ├─ api.py
│  ├─ image_store.py
│  ├─ main.py
│  ├─ models.py
│  ├─ prompt_opt.py
│  ├─ services.py
│  ├─ storage.py
│  └─ ui/
│     └─ main_window.py
```

## 截图

你可以在发布到 GitHub 前，把截图放到：

- `docs/screenshots/generate.png`
- `docs/screenshots/history.png`
- `docs/screenshots/settings.png`

然后在这里补上：

```md
![生成页](docs/screenshots/generate.png)
```

## 当前状态

这个项目已经可以用于本地 Windows 工作流，但仍在持续迭代中。

后续可能继续完善：

- 更好的窗口自适应布局
- 更成熟的多 API 配置管理
- 失败任务重试队列
- 更完整的打包与安装流程
- 更适合桌面端的历史 / 画廊视图

## 贡献

欢迎提交 Issue 和 Pull Request。

如果你准备参与贡献，建议先阅读：

- [CONTRIBUTING.md](CONTRIBUTING.md)
- [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)

## 安全问题

如果你发现安全问题，请先阅读：

- [SECURITY.md](SECURITY.md)

## 更新记录

- [CHANGELOG.md](CHANGELOG.md)

## 许可证

本项目基于 [MIT License](LICENSE) 开源。

## 致谢

- [PySide6](https://doc.qt.io/qtforpython-6/)
- [Requests](https://requests.readthedocs.io/)
- [Pillow](https://python-pillow.org/)
