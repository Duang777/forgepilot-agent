# ForgePilot Website (GitHub Pages)

这是用于 GitHub Pages 的静态官网下载页。

## 文件结构

- `index.html`: 页面结构
- `styles.css`: 视觉样式（参考 Figma DESIGN.md 语义）
- `script.js`: 交互脚本（复制校验码）

## 你需要改的地方

在 `index.html` 中把以下占位值替换成真实发布信息：

1. 如果要做“一键直链下载”，把按钮链接从 `releases/latest` 改成你的资产文件名下载链接
2. `SHA256-WIN-PLACEHOLDER`
3. `SHA256-MAC-PLACEHOLDER`
4. `SHA256-LINUX-PLACEHOLDER`
5. 页脚仓库地址（如后续仓库迁移需同步修改）

## 发布到 github.io

仓库里已经提供 `.github/workflows/pages-website.yml`：

- 推送 `website/**` 到 `main/master` 后会自动部署。
- 需要在仓库 `Settings -> Pages` 中选择 `GitHub Actions` 作为 Source。
