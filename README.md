# 安全查重清理助手（safe-dedupe）

一个用于 WorkBuddy 的 Skill：**fclones 原生极速扫描** + **加固型可恢复删除**。先分组报告、人工确认，再把重复文件按"保留修改时间最新"移入同盘隔离区，逐批校验、失败即停。

> 适用平台：Windows（主要）。针对杀毒软件拦截、回收站配额退化、通配符文件名、沙箱保护路径、系统盘被快照撑满等实战陷阱做了专门防护。

## 功能

- **极速查重**：调用 fclones 原生引擎（按大小预分组 + 并行哈希），10 万+ 文件 / TB 级目录远快于逐文件 Python 哈希；支持查找副本不足的文件（`--rf-under`）。
- **保留最新**：每组重复文件保留修改时间最新的一份，并列时取路径字典序最大者。
- **先报告后删除**：`fclones group` 只读扫描 → 引号感知解析 CSV → `dedupe_report.json` + `dedupe_decisions.csv`，用户确认后才动手。
- **加固删除**：默认同盘隔离区移动（瞬时、不占额外空间、可恢复）；.NET `File.Move` 绕过杀软拦截，两步重命名兜底，通配符名走 `-LiteralPath`；每批可配置，逐文件校验，任一失败立即停止。
- **回收站预检**：如需回收站模式，先用 `verify_recycle_bin.ps1` 检测配额——`SendToRecycleBin` 在无配额时会"假装成功"地永久删除。
- **可选纯 Python 扫描器**：无 fclones 或需要断点续扫时，用三阶段扫描（大小 → 首尾指纹 → 完整/采样 SHA-256）。

## 安装

```bash
# 克隆仓库
git clone https://github.com/evilhumen/safe-dedupe.git

# 复制到 WorkBuddy 用户级技能目录
# Windows (Git Bash)
mkdir -p ~/.workbuddy/skills/safe-dedupe
cp -r safe-dedupe/* ~/.workbuddy/skills/safe-dedupe/
```

重启 WorkBuddy 后即可用自然语言触发，例如：

> 对 F:\003.工作 用 fclones 查重，保留修改日期最新的，报告先给我看。

### 安装 fclones 引擎（必需）

本仓库出于许可证原因**不包含** `fclones.exe`（fclones 为 MIT 许可的独立开源项目）。请自行下载并放到 `bin/fclones.exe`：

1. 打开 fclones 官方发布页：<https://github.com/pkolaczk/fclones/releases>
2. 下载 Windows x86_64 版本（如 `fclones-x86_64-pc-windows-msvc.zip`）。
3. 解压，把 `fclones.exe` 放到 `<技能目录>/bin/fclones.exe`。

验证：`& .\bin\fclones.exe --version`（本技能在 v0.35.0 上验证）。

> 如果只想用纯 Python 扫描器（不依赖 fclones），可跳过此步，直接调用 `scripts/scan_duplicates.py`。

## 目录结构

```
safe-dedupe/
├── SKILL.md                          # 技能主文档与六步工作流
├── bin/
│   └── fclones.exe                   # 需自行下载（见上），v0.35.0 验证
├── scripts/
│   ├── fclones_csv_to_report.py      # 引号感知 CSV → 报告/决定清单
│   ├── scan_duplicates.py            # 可选纯 Python 三阶段扫描器
│   ├── delete_duplicates.ps1         # 分批隔离移动 + .NET/两步重命名兜底
│   └── verify_recycle_bin.ps1        # 回收站配额/载荷预检与后检
└── references/
    └── windows-battlefield.md        # 生产实战踩坑记录
```

## 典型用法

```powershell
# 1) 只读扫描（报告写到目标盘，别写 C:，大任务会撑爆系统盘快照）
& .\bin\fclones.exe group "F:\target" --format csv --output "F:\report\fclones.csv"

# 2) 转成可核对的报告
python scripts/fclones_csv_to_report.py "F:\report\fclones.csv" --output "F:\report"

# 3) 先看清单，确认后 DryRun
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/delete_duplicates.ps1 -Report "F:\report\dedupe_report.json" -DryRun

# 4) 正式移入同盘隔离区
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/delete_duplicates.ps1 -Report "F:\report\dedupe_report.json" -QuarantineRoot "F:\_dedupe_quarantine" -BatchSize 50 -Log "F:\report\deletion_log.txt"

# 5) 复扫确认 0 组重复后，再永久删除隔离区释放空间
```

参数细节与完整工作流见 `SKILL.md`。

## 安全设计

1. 一次只处理一个顶层文件夹；扫描前确认目标、判定标准、日期依据。
2. 删除前必须产出报告 + 决定清单，展示数量与可释放空间，并以加粗警告要求确认。
3. 默认可恢复删除（同盘隔离区），永久删除需明确授权。
4. 每批不超过可配置上限（保守默认 10），逐文件校验，失败即停。
5. 删除后校验每个 delete 路径已消失、每个 keep 路径仍存在，如实汇报。
6. 所有 Windows 特殊路径（`[ ] * ?`、含逗号路径）用 `-LiteralPath` / 正规 CSV 解析处理。

详见 `references/windows-battlefield.md`。

## License

MIT（fclones.exe 为独立的 MIT 开源项目，不随本仓库分发）。
