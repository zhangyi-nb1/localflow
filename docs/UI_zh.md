# LocalFlow 网页界面 · 中文使用指南

> 本文针对 LocalFlow Streamlit UI（v0.8.0+）撰写。从 v0.8.0 起界面
> **支持中英双语** —— 左侧栏顶端的 `Language / 语言` 单选可一键切
> 换。文中保留英文原文的标注（先给英文原文 + 中文解释）是因为
> 部分用户仍习惯英文界面，且这样可以同时帮助你定位屏幕上的按钮。

**v0.8.0 关键改进**：

- 🌍 **中英双语切换** —— 左侧栏置顶。session 级别（关掉浏览器后默认重置回英文）。
- 🧠 **Plan 页面自动识别 skill + planner** —— 用户只写 goal，
  LocalFlow 根据 goal 关键词 + 工作区文件类型自动选择 skill 和
  rule/llm。如需手动覆盖，展开「▶ Override (advanced)」即可。
- 📁 **自定义路径 UX 重构** —— 左侧栏 Source 单选按钮统一了
  「Sandbox 子目录」与「Custom path（自定义路径）」两种来源，
  替代之前折叠 expander 隐藏自定义路径输入框、被旁边的下拉
  抢走焦点导致输入被静默丢失的旧布局。
- 📌 **当前工作区徽章** —— 左侧栏顶端持续显示，不再需要靠下拉
  推断当前选了哪个目录。

---

## 1. 这是什么？

LocalFlow 是一个 **"给 LLM 装上安全护栏" 的执行框架**。当 AI 要在你的电脑上做事
（移动文件、改名、生成索引……），LocalFlow 会：

1. 让 AI **先出一份结构化计划**（不让它直接动手）
2. **预演** 给你看（这一步什么都不写入磁盘）
3. 等你 **确认** 之后才真的执行
4. 自动跑一次 **独立校验**
5. 如果你后悔了，**逐步回滚**（甚至能检测出"你后来手动改过的文件"，
   避免回滚误覆盖）

**网页 UI** 就是这套流程的可视化版本 —— 不用敲命令，点按钮就行。

> 同样的事情，CLI（命令行）和 MCP（外部 AI 客户端，如 Claude Code）
> 都能做。三者复用同一套核心代码，行为完全一致。

---

## 2. 5 分钟上手

### 安装一次（之后不用再装）

```powershell
cd c:\Users\13513\Desktop\XIANGMU\localflow
.\.venv\Scripts\Activate.ps1
pip install -e ".[ui]"
```

### 每次使用

```powershell
localflow ui-serve
```

终端会显示：

```
Starting LocalFlow UI on http://127.0.0.1:8501  (sandbox: ./sandbox/)
You can now view your Streamlit app in your browser.
  URL: http://127.0.0.1:8501
```

浏览器自动打开 `http://127.0.0.1:8501`。

**停止服务**：回到终端按 `Ctrl+C`。**关掉浏览器不会关 server**。

---

## 3. 总体布局

打开后界面分两块：

### 左侧栏 (Sidebar) · v0.8.0 新布局

```
┌─────────────────────────────────┐
│ Pages（页面切换）                │
│   • main / Plan / Execute /     │
│     Rollback / Memory           │
│                                 │
│ 🌍 Language / 语言               │
│   ⦿ English  ◯ 中文              │ ← 切换语言（v0.8.0 新增）
│ ───────────────────────────     │
│ Workspace（工作区）              │
│   Active workspace:             │ ← 顶端徽章，始终可见
│     ./sandbox/demo              │
│   Sandbox root: sandbox         │
│   Source（来源）：                │ ← 单选按钮（v0.8.0 新增）
│     ⦿ Sandbox subdir            │
│     ◯ Custom path               │ ← 仅 ?unsafe=1 时显示
│   Pick workspace [▼]            │
│   [🔄 Refresh]                  │
│                                 │
│ Memory（偏好快览）                │
│   显示当前 forbidden_paths /    │
│   naming_style                  │
└─────────────────────────────────┘
```

### 主区域

按当前所选页面渲染对应内容。

---

## 4. 工作区（Workspace）概念

**Workspace（工作区）= LocalFlow 唯一允许操作的文件夹。**

* 默认情况下 UI **只允许你选 `sandbox/` 目录下的子文件夹**作为 workspace
  （比如 `sandbox/demo`、`sandbox/messy_downloads`）。这是软隔离 ——
  防止你不小心选了 `C:\Users\...\Documents` 这种重要目录。
* 选好 workspace 后，**所有 Plan / Execute / Rollback 操作都只在这个
  目录下进行**。kernel（内核）会硬性拒绝任何越界路径。

### 怎么创建一个 workspace

PowerShell 里：

```powershell
mkdir sandbox\my_test_workspace
"Hello" | Out-File sandbox\my_test_workspace\note.txt
```

回 UI 点 **"🔄 Refresh"**（刷新）按钮，下拉里就出现 `sandbox\my_test_workspace` 了。

### 想用 sandbox 外的目录怎么办（v0.8.0 已重写流程）

浏览器地址栏改成 `http://127.0.0.1:8501/?unsafe=1`（**加上 `?unsafe=1`**），
回车。这时：

* 页面顶部会出现 **黄色警告条**
* 左侧栏的 **Source（来源）** 单选按钮会多出一个 **"Custom path
  (?unsafe=1 required)"** 选项 —— 选中它
* 下方出现一个 **"Workspace absolute path"**（工作区绝对路径）输入框
  + 实时校验：
  * ✅ 绿色：路径存在且是目录 → 自动作为当前工作区
  * ❌ 红色：错误信息直接显示在输入框下面（路径不存在、不是目录等）

（不加 `?unsafe=1` 时，Custom path 单选项**根本不显示**；左侧栏会
在 Source 下方给出一行 "🔒 Custom path locked — reload with
?unsafe=1 to enable." 的提示。）

⚠️ 但即便 UI 放行了，kernel 内置的安全检查仍然会拦截 —— UI 软隔离只是
第一道防线，不是唯一防线。

> 旧版本（v0.7.x）的 "Custom path" 输入框藏在一个折叠 expander 里，
> 上方的下拉框还会和它抢工作区选择 —— 实际效果是用户输入了路径
> 但 UI 静默回退到下拉里的选择。v0.8.0 用单选按钮把两种来源摆在
> 同一个控件里，从结构上消除了这种歧义。

---

## 5. 每个页面详解

### 5.1 主页（main）

打开 UI 默认看到的页面。功能：

* 显示当前选中的 workspace（如果还没选会提示 "Pick a workspace in the sidebar first."）
* 显示该 workspace 的 **Files**（文件数）和 **Total size**（总大小）
* 简短介绍三种 driver（CLI / MCP / UI）的关系

主页 **不执行任何操作**，纯展示。

---

### 5.2 📋 Plan（规划）页 · v0.8.0 已重设计

**用途**：让 LocalFlow 看一下你的 workspace，生成一份「我打算这么做」的
结构化计划。**这一步不动任何文件**。

#### v0.8.0 的新界面

```
┌──────────────────────────────────────────────────┐
│ What do you want to do? / 你想做什么？           │
│ ┌──────────────────────────────────────────────┐ │
│ │ e.g. organize by file type / 按文件类型整理 │ │
│ └──────────────────────────────────────────────┘ │
│                                                  │
│ ℹ️ Auto-detected · skill=folder_organizer ·      │
│    planner=rule                                  │
│ Reason — goal mentions organize/sort/categorize  │
│          · rule planner is enough                │
│                                                  │
│ ▶ Override (advanced)   ← 默认折叠               │
│                                                  │
│ [ 📋 Create plan ]                              │
└──────────────────────────────────────────────────┘
```

**只需要写一句 goal**，LocalFlow 会根据：

* **goal 里的关键词**（中英双语都识别）
  * `分析` / `analyze` / `groupby` → `data_analyzer`
  * `报告` / `统计` / `report` / `summary` → `data_reporter`
  * `论文` / `paper` / `pdf` / `index` → `pdf_indexer`
  * `整理` / `分类` / `organize` / `sort` → `folder_organizer`
* **工作区里的文件构成**（数据 skill 需要至少 1 个 csv/excel；
  pdf_indexer 需要至少 1 个 PDF）
* **是否需要大模型**
  * goal 包含 `按内容` / `语义` / `intelligent` / `by topic` 等
    语义意图词，并且 skill 支持 LLM → `planner=llm`
  * 否则 → `planner=rule`（快、确定、免费）

自动识别结果会显示在输入框下方，附带一行 **Reason**
（理由）说明它为什么这么选 —— 方便你判断对不对。

#### 想要手动覆盖时

展开 **"▶ Override (advanced)"** 折叠面板，里面是经典的
**Skill 下拉** + **Planner 单选**（rule / llm）。这两个控件默认就
显示自动识别的结果；如果你不改，自动识别会被采纳；改了的话以
你的选择为准。

点 **"📋 Create plan"**（创建计划）按钮提交。

#### 提交后看什么

页面下方出现 **Plan**（计划）卡片：

* **Actions**（动作数）：将要执行的步骤数量
* **Files scanned**（扫描文件数）：workspace 里有多少个文件
* **Risk**（风险）：颜色徽章
  * 🟢 **LOW** = 低风险（只是创建索引等）
  * 🟡 **MEDIUM** = 中等风险（涉及移动文件）
  * 🔴 **HIGH** = 高风险（涉及覆盖等不可逆操作）
  * ⛔ **BLOCKED** = 被策略拦了（看下面的 warnings）
* **Outputs**（预期产物）：会生成哪些新文件
* 表格列出每一个 **Action**：类型 / 源路径→目标路径 / 原因 / 是否需要审批

底部会出现绿色提示「✅ Task `xxxxx` created.」+ 蓝色按钮
**"🔍 Continue to Execute →"**（去执行）。点这个按钮**自动跳到 Execute 页**。

---

### 5.3 🔍 Execute（执行）页

**用途**：把 Plan 真的跑起来。分三个阶段，每个阶段都有自己的安全闸门。

#### Stage 1 — Dry run（预演）

页面顶部下拉里选刚才创建的 task（应该已经自动选好）。

点 **"🔍 Render dry-run"**（渲染预演）按钮。

预期看到：
* 风险徽章（同 Plan 页）
* **"Actions to execute"**（要执行的写操作数）
* 折叠面板 **"📄 Dry-run preview (markdown)"**（预演预览）—— 展开后是
  详细 Markdown 表格，每个 action 一行

🔒 **这一步只写入 `dry_run.md` 文件到 `.localflow/runs/<task_id>/` 目录，
你的 workspace 完全不动**。

#### Stage 2 — Approval（审批）

下方出现复选框 **"✅ I've reviewed every action above and consent to commit them."**
（我已审阅上述每个动作并同意提交）。

**不勾选时**，下面的 **"Execute (locked)"** 按钮是**灰色不可点的**。
这是 LocalFlow 的关键安全特性：

> 没有明确审批，就不会执行。

勾选后，按钮变成蓝色的 **"🚀 Execute now"**（立即执行）。

#### Stage 3 — Execute + Verify（执行 + 校验）

点 **"🚀 Execute now"**。后台会：

1. 验证 **approval_token**（审批令牌，确保 dry-run 和 execute 之间没有篡改）
2. 真正写入你的 workspace
3. 自动跑独立 verifier（校验器）

结果显示 4 个 metric 卡片：

| 卡片 | 含义 |
|---|---|
| **Executed** | 成功执行了几个动作 |
| **Failed** | 失败了几个 |
| **Skipped** | 跳过了几个（断点续传场景） |
| **Verifier** | ✅ PASSED 或 ❌ FAILED —— **校验器独立判断成功与否，不问 LLM** |

如果 Verifier 显示 ✅ PASSED，下面会出现绿色提示 + **"↺ Continue to Rollback →"**
（去回滚）按钮，方便你紧接着体验回滚。

---

### 5.4 ↺ Rollback（回滚）页

**用途**：撤销之前 execute 过的任务，把 workspace 恢复到之前的状态。

#### 选 run + Preview

* **"Run to rollback"**（要回滚的 run）下拉里选你之前 execute 过的 task
* 点 **"🔍 Preview"**（预览）按钮

下方出现：
* **Entries**：要执行多少个反向操作
* **State**：
  * ✅ **CLEAN** 绿色 = 干净，所有文件都没被你手动改过 → 可以放心回滚
  * ❌ **CONFLICTS** 红色 + 黄色横幅 = 有冲突！下面解释

* 表格列出每一条 entry：
  * **action_id**：原来 execute 时的动作 ID
  * **op**：反向操作类型（`move_back` 移回原位 / `delete_created_file` 删除新建文件 / `delete_created_dir` 删除新建目录 / `restore_from_backup` 从备份还原）
  * **target**：会动哪个文件
  * **status**：
    * **✅ clean** 绿色 = 文件未被手动改过，可以安全回滚
    * **⚠️ drift** 黄色 = 文件 execute 之后被你（或别人）改过了 ⭐ **这是 LocalFlow 的核心特色**
  * **reason**：如果 drift，这里写为什么（哈希不一致）

#### 干净回滚（无 drift）

如果 State = CLEAN，只有一个按钮 **"↺ Rollback now (clean)"**（立即干净回滚）。
点击 → 后台执行 → 显示结果。

#### 有冲突时的回滚（drift 处理）⭐

如果 State = CONFLICTS，会出现**两个按钮**：

##### 选项 A：**"↺ Safe rollback (skip conflicts)"**（安全回滚 · 跳过冲突）

* 把没 drift 的 entry 全部回滚
* drift 的那些**跳过不动**，**保护你的手动编辑**
* 结果会显示为 **PARTIAL**（部分完成）状态，附带蓝色说明：哪些目录因为
  装着你保留的文件而无法清空，**这是设计上的安全保证，不是 bug**

##### 选项 B：**"🔥 Force rollback (clobber edits)"**（强制回滚 · 覆盖编辑）

* 必须先勾选下方 **"⚠ I accept that forcing will overwrite my manual edits."**
  （我接受强制回滚将覆盖我的手动编辑）才能点
* 完全无视 drift，**把你的所有手动改动覆盖掉**
* 结果会是干净的 PASSED

#### 三种结果区域

完成回滚后，下方有 4 个 metric + 三个可能的折叠面板：

| 折叠面板 | 什么时候出现 |
|---|---|
| **"📂 Cascaded directory cleanups skipped"** | 蓝色信息，说明哪些目录因为装着 drift 文件而保留 |
| **"❌ Real failures"** | 红色错误 —— **真正的 bug** （比如文件被别的程序锁住），需要排查 |
| **"⚠️ Conflicts skipped"** | 黄色警告 —— 安全跳过的 drift 文件清单 |

⭐ **关键认知**：
* **Real failures** = 出 bug 了，要看
* **Conflicts skipped** = 你选了保留，正常
* **Cascaded directory cleanups skipped** = 上面那两个的衍生后果，也正常

---

### 5.5 ⚙ Memory（偏好记忆）页

**用途**：设置 LocalFlow 在所有 future 任务中要记住的偏好。**改一次永久生效**。

分三个 tab：

#### Tab 1 — 🚫 Forbidden paths（禁止访问的路径）

* **效果**：你列在这里的路径，**kernel 会硬性拒绝任何动作访问**。
  即便 Skill 想去碰，也会被驳回。
* **典型用例**：
  * `private/secrets` —— 不让任何 Skill 碰你的私密目录
  * `important.docx` —— 锁定某个具体文件
* **怎么加**：底下输入框填路径（**workspace 相对路径**，不能是绝对路径
  也不能用 `..`），点 **"➕ Forbid"**（禁止）
* **怎么删**：每行右边的 🗑 按钮

#### Tab 2 — 📝 Naming style（命名风格）

* **效果**：`folder_organizer` 在移动文件时会按这个风格重命名
* 4 种选择：

| 选项 | 例子（原：`Report (Final).pdf`） |
|---|---|
| **original**（不变） | `Report (Final).pdf` |
| **snake_case**（下划线小写） | `report_final.pdf` |
| **kebab-case**（连字符小写） | `report-final.pdf` |
| **lower**（仅小写） | `report (final).pdf` |

* 选完点 **"Save"** 按钮持久化
* 下方 **"Example transformations"** 折叠面板可以预览所有风格对几个样例的处理结果

#### Tab 3 — 📜 Audit log（审计日志）

* 记录你对 Memory 做过的**每一次修改**
* 时间戳 / event 事件类型 / 改了什么字段 / 修改前后值
* **永久持久化**到 `~/.localflow/memory/audit.jsonl`

---

## 6. 完整流程示例

> 假设你想体验：**整理一个乱糟糟的文件夹，然后撤销**。

### 准备数据（PowerShell 一次性命令）

```powershell
cd c:\Users\13513\Desktop\XIANGMU\localflow
mkdir sandbox\my_first | Out-Null
"" | Out-File "sandbox\my_first\report.pdf"
"text" | Out-File "sandbox\my_first\note.txt"
"a,b" | Out-File "sandbox\my_first\data.csv"
```

### UI 操作（按顺序点）

1. **左侧栏** → 点 **"🔄 Refresh"** → 下拉选 `sandbox\my_first`
2. **左侧栏** → 点 **"Plan"**
3. **表单** → Skill=`folder_organizer`、Goal=`organize by file type` → 点 **"📋 Create plan"**
4. 看到 Plan 表格 → 点底部 **"🔍 Continue to Execute →"**
5. **Execute 页** → 点 **"🔍 Render dry-run"** → 浏览预演内容
6. 勾选 **"✅ I've reviewed every action above..."**
7. 点 **"🚀 Execute now"** → 看到 ✅ PASSED
8. 点 **"↺ Continue to Rollback →"**
9. **Rollback 页** → 下拉选刚才的 task → 点 **"🔍 Preview"** → 看到全 ✅ clean
10. 点 **"↺ Rollback now (clean)"** → 看到全 7 个 undone

### 验证（PowerShell）

```powershell
Get-ChildItem -Recurse sandbox\my_first -File
```

应该看到三个文件**回到了根目录**（execute 之后曾经被分到 `papers/` `notes/` `data/`，
rollback 把它们移回来了）。

### 进阶：体验 drift 检测 ⭐

走完上面 1-7 步（execute 但**别 rollback**），然后：

```powershell
"USER EDIT" | Out-File -Append "sandbox\my_first\notes\note.txt"
```

回 UI Rollback 页 → 重新 Preview → 你会看到：
* State = ❌ CONFLICTS（红色）
* `note.txt` 那行 status = ⚠️ drift 黄色
* 两个按钮：Safe 和 Force

试 **Safe rollback** → 你的 USER EDIT 被保留下来；其他文件回到根目录。

---

## 7. 常见问题

| 现象 | 原因 / 解决 |
|---|---|
| 浏览器显示 `ERR_CONNECTION_REFUSED` | 服务端没起来 / 端口冲突。检查终端有没有"URL: http://127.0.0.1:8501"输出。换端口：`localflow ui-serve --port 8520` |
| 第一次启动卡在 "Email:" 提示 | 你的版本 < v0.7.1。`git pull` 升级后重试 |
| 左侧栏 workspace 下拉是空的 | 你 `sandbox/` 下没有任何子目录。PowerShell `mkdir sandbox\demo` 后点 **"🔄 Refresh"** |
| Plan 页报错 "workspace outside the soft sandbox" | 你选了 sandbox 外的路径。要么移到 sandbox 下，要么 URL 加 `?unsafe=1` |
| Execute 页 "Execute (locked)" 按钮一直点不亮 | 没勾选审批 checkbox |
| 看到 `⚠ LocalFlow loaded 1 external skill(s)...` 警告 | **正常**，不是错误。是 Phase 7.1 安全提醒，告诉你加载了外部 skill |
| Rollback Force 按钮一直灰色 | 必须先勾选 "⚠ I accept that forcing will overwrite my manual edits." |
| Rollback 显示 PARTIAL 状态 + 蓝色提示 | **不是 bug**。你选了 Safe 保留某些文件 → 它们所在的目录无法清空 → 这是正常副作用 |
| Memory 页加 forbidden_path 报 "absolute paths not allowed" | 用 workspace 相对路径，不要写 `C:\...` 这种绝对路径 |
| Plan 页 LLM planner 选项灰色 | 当前 skill 不支持 LLM 规划。换成 rule planner，或选 `folder_organizer` / `data_analyzer`（支持 LLM 的 skill） |
| Plan 页 auto-detect 选错了 skill | 展开 **"▶ Override (advanced)"** 手动选择 |
| 切换语言后部分文字仍是英文 | 该字符串可能还没翻译，或显示为 `!!key!!` —— 这是开发态的标记。请去 GitHub 提 issue。UI 不会因此崩溃。 |
| 关掉浏览器后语言又变回 English | session 级别设置，**这是设计如此**。每个浏览器 tab 新建都要重新选一次中文。 |
| 关掉浏览器后再打开，session 都丢了 | Streamlit session 跟浏览器 tab 绑定。这是 Streamlit 特性，重新选 workspace 即可。task 数据**没丢**（在磁盘上） |

---

## 8. 安全机制速览

LocalFlow 在网页 UI 这一层叠加了**多重安全保证**，从外到内：

```
┌─────────────────────────────────────────────────────────────┐
│ 1. 网络层：默认 bind 127.0.0.1（不开放局域网）              │
│    要 LAN 暴露需 `localflow ui-serve --host 0.0.0.0` 显式指定│
│                                                              │
│  2. UI 软沙箱：workspace 下拉只列 ./sandbox/ 子目录          │
│     ?unsafe=1 才能解锁，且有黄色警告                          │
│                                                              │
│   3. Approval Token（审批令牌）：execute 必须先 dry-run     │
│      Token 绑定 plan_hash + dry_run_hash + workspace        │
│      过期 10 分钟、一次性、不可重放                          │
│                                                              │
│    4. Kernel 硬墙：policy_guard 拒绝越界路径 / 禁止动作      │
│       forbidden_paths kernel 强制（Memory 设置）              │
│       即便 Skill 想绕过也会被驳                              │
│                                                              │
│     5. Verifier 独立校验：用规则判断成功失败                 │
│        从不问 LLM "你完成了吗"                                │
│                                                              │
│      6. Rollback hash 守卫：检测你 execute 后手动改的文件    │
│         默认 Safe 跳过，需 Force 才覆盖（你被警告过）         │
└─────────────────────────────────────────────────────────────┘
```

任何一层失守，下一层都会接住。

---

## 9. 不同版本演进

UI 自 v0.7.0 推出，至 v0.7.4 经历的关键改进：

| 版本 | 改进 |
|---|---|
| v0.7.0 | 网页 UI 首次发布，5 个页面齐备 |
| v0.7.1 | 修复首次启动卡 Email prompt 的问题（默认禁用 Streamlit 第一次运行向导） |
| v0.7.2 | 加 **"🔍 Continue to Execute →"** / **"↺ Continue to Rollback →"** 自动跳转按钮，不用每次手动切页 |
| v0.7.3 | 修复 Rollback 页 AttributeError 崩溃 |
| v0.7.4 | Rollback 结果区智能区分"真失败"和"因 drift 而连带跳过的目录清理"，状态从 FAILED → PARTIAL |
| v0.8.0 | **三大 UX 修复**：① 中英双语切换（左侧栏顶端单选按钮）② Plan 页面自动识别 skill + planner，写 goal 即可，手动选项收进折叠面板 ③ 左侧栏 Source 单选按钮取代旧的下拉 + 折叠 expander，自定义路径输入框现在显式且可见 |
| v0.8.1 | 修复「点 Plan 后自定义路径被静默切回 sandbox」—— Streamlit 多页切换会丢掉 URL 的 `?unsafe=1`。现在 unsafe 模式一旦启用就锁定到 session_state，跨页切换不会再丢失。 |
| v0.8.2 | **三大升级**：① 新增 `workspace_visualizer` skill —— 真画 PNG 柱状图（不再是 markdown 假图） ② 复合 goal 自动升级 LLM（含「然后/再/最后」等连接词或 3+ 不同动词时） ③ Memory 新增「Prefer LLM by default」开关 + Plan 页面增加「能力缺口」黄色警告，提示当一个 skill 不能完整覆盖 goal 时该怎么补齐。 |
| v0.9.0 | **架构性升级 · agent meta-skill**：UI 上不再让用户在 5 个 skill 之间选 —— 永远走新增的 `agent` skill。它的 LLM 一次性出包含 mkdir + move + 写 markdown + 画 PNG 的**单一 ActionPlan**，复合 goal 一个 task 跑完。Override 折叠面板移除，能力缺口警告移除（agent 没有缺口）。Specialist skill 仍在 CLI/MCP 可用；harness 不变，仍由 dry-run / approval / executor / verifier / rollback 五道闸保证安全。 |

---

## 10. 下一步

* 想看 CLI 版怎么用：[docs/UI.md](UI.md) 英文版（含 troubleshooting 完整表）+ [README.md](../README.md) Quickstart
* 想看完整生命周期文本截图：[docs/demo_walkthrough.md](demo_walkthrough.md)
* 想了解架构：[docs/ARCHITECTURE.md](ARCHITECTURE.md)
* 想了解安全模型：[docs/SECURITY.md](SECURITY.md)
* 想让 Claude Code 等 AI 客户端通过 MCP 调用 LocalFlow：[docs/MCP.md](MCP.md)
