# Zygisk IL2CPP MCP

把 Android Unity IL2CPP 游戏进程中的运行时查询、方法调用和 Dobby Hook 暴露给 MCP 客户端的 Zygisk 模块。我要Star⭐ QAQ

## 已实现

- 通过持久目录中的 `apps.txt` 配置多个目标包名，同时匹配应用主进程和 `包名:子进程`。
- 通过持久目录中的 `port.txt` 自定义 MCP/命令 Socket 端口，默认 `27184`。
- 由 Zygisk Root companion 读取配置并通过 IPC 传给目标进程，兼容应用进程无法访问 `/data/adb` 的环境。
- 全新 KernelSU/Magisk WebUI：添加/移除多个包名、修改端口、连接检测、复制 MCP 配置、一键导出 `MCP.zip`。
- 游戏内 ImGui 悬浮菜单：Java SurfaceView 显示，使用 `zh_Font.h` 字体、中英双语、原生标题栏折叠/展开与尺寸调整；默认 Classic 紫色主题，可切换 Dark/Light。保留 Toast、Dump、运行状态和内存/ARM64 分析入口。
- 对象可视化：自动游戏帧连接、类筛选、多类自动跟踪、对象多选、相机选择、射线、2D/角框/3D 轴对齐方框、名称/距离/计数、人形骨骼；遮挡检测已移除。矩阵接口被裁剪时支持 WorldToScreenPoint 降级，Unity 调用仍只在游戏帧回调执行。
- 渲染/UI/工作台 MCP 工具：对象与规则、独立父/子窗口、控件树、双向绑定、Lua UI 程序、网格/曲线、检查器和导出。手动操作与 AI 配置共享状态，帮助通过 `debug_help` 查询。
- 原生 AI 逻辑区：7 个 `logic_program_*` MCP 工具，支持变量、条件、循环、动态对象源、字段读取、绘制覆盖和 UI 变量输出，不依赖 Lua。底层不预装游戏规则，AI 通过 MCP 下发程序和业务界面；。
- IL2CPP：跨 Image 模糊搜索类/方法/字段，字段偏移与类型、完整方法签名、对象字段、数组/List/Dictionary 加载、带参静态/实例方法调用，以及方法 Hook。
- IL2CPP Dump：支持全量、指定类或命名空间，写入目标应用私有目录 `files/zygisk_il2cpp_mcp/il2cpp_dump_<随机后缀>.cs`；MCP 只返回状态、路径和类数量，不返回正文。
- 非 IL2CPP 内存工具：安全读写映射、模块起止地址/重复实例定位、地址反查、多级指针链、并行基址扫描、字节/类型化搜索及多轮过滤。
- KittyMemory 统一内存读写：KittyScanner 扫描受控快照，保留精确/模糊搜索、过滤和多线程基址扫描；代码补丁、Lua 内存入口、反汇编/反编译读取也经过 KittyMemory。内核驱动全部停用，不再读取旧驱动配置。
- 搜索：精确多类型搜索、未知值模糊搜索、变化/不变/增大/减小过滤、结果分页，以及内存区域类型多选。
- Dobby：符号解析、原生地址 Hook、固定返回、Instrument 计数、代码 Patch、Destroy 与 Hook 列表。
- 动态调试：内置 LuaJIT+FFI、ARM64 AsmJit 汇编、Capstone 反汇编/指令修改、Ghidra C 风格伪代码还原、perf 硬件断点与命中栈回溯、Dobby 追踪回溯。Ghidra 直接读取目标的实时内存以解析只读字符串和全局数据；输入地址精确命中 IL2CPP 方法时自动注入返回值、参数、声明类及实例字段 Offset 类型。
- MCP 功能控制：20 组开关全部默认开启（新增 `rendering`、`overlay_ui`），仅通过默认 `127.0.0.1:27185` 浏览器管理页面动态关闭。管理接口不会暴露给 Agent；禁用工具会从 `tools/list` 消失，原始命令也无法绕过对应开关。
- 除反编译器外的可选能力按需懒加载；ARM64 Ghidra 反编译器在功能默认开启时随注入主体一同初始化。目标 ABI、内核或运行时不支持时只停用对应工具，Socket、内存、Dobby 和其他能力继续工作。
- JNI Toast：显示当前 MCP tool 与参数，可通过 MCP 开关或主动显示自定义内容。
- 注入目标启动提示：目标进程初始化时会通过 Toast 显示 `TG: @il2cppmcp`；如果 Android 应用上下文尚未就绪，模块会在启动后短暂重试，不影响目标进程运行。
- 无第三方 Python 依赖的 stdio MCP Server，默认自动执行 `adb forward`。

WebUI 和 MCP 配置中不包含陀螺仪功能。

## 配置

模块安装后可直接通过 WebUI 保存配置，也可手动编辑：

```text
/data/adb/zygisk_il2cpp_mcp/apps.txt
/data/adb/zygisk_il2cpp_mcp/port.txt
/data/adb/zygisk_il2cpp_mcp/overlay_enabled.txt
```

`apps.txt` 每行一个包名，例如：

```text
com.example.game
com.example.anothergame
```

程序会在目标进程启动时读取配置。修改后请彻底结束并重新启动目标游戏。
WebUI 的“注入 ImGui 窗口”默认开启；关闭并保存后，下次启动不创建 Java 悬浮窗/SurfaceView/ImGui 绘制线程，屏幕对象绘制也停用，MCP 和原生调试仍可使用。配置文件为 `1`（开启）或 `0`（关闭），缺失时默认开启；它不同于 `overlay_set(visible=false)` 临时隐藏窗口。
旧版本的 `memory_backend.txt`、`driver_node.txt` 不再加载；无需配置或刷入内核驱动。WebUI 使用实色扁平化面板，保留深浅色适配，不使用渐变、毛玻璃或发光阴影。

## 游戏内悬浮菜单

目标进程注入后，菜单在前台 Activity 就绪时显示，标题为 `il2cpp mcp tg@il2cppmcp`。保留 ImGui 原生标题栏移动、三角折叠/展开与边框缩放；横屏浏览与详情分窗，竖屏使用接近屏宽的可切换窗口。

- **场景**：表格形式的场景/对象树，箭头展开、名称打开独立检查器；支持分页、重试和缺失场景接口的降级查询。
- **IL2CPP**：程序集/类列表与可展开方法面板；单击类打开独立类型标签页，双击或“独立类型详情”打开新窗口。支持上下排列/左右分栏、方法调用/固定返回/追踪/断点/分析入口、独立对象选择窗，并自动带入选中实例。字段值、字段类型路径导航、类/命名空间 Dump 与关系流程图保留。
- **工具**：模块、程序集、Hook、断点列表；运行状态与 Dump 放在折叠区域。内存/汇编/伪代码在单独的分析窗口。
- **分析窗口**：按类型/映射自动决定读取长度；每页数量可设 1–256（默认 32），内存每行 16 字节。伪代码通过 Ghidra 地址映射双向同步汇编，可从指令启动计数、追踪和断点。未知函数范围仍有 16 KiB 上限与估算提示。
- **渲染**：对象、类跟踪、相机、样式和规则各自分页。对象和类支持搜索、多选、表格编辑与检查器跳转，不依赖 AI 才能操作。
- **扩展控件**：只有 AI/MCP 创建了面板才出现，用于操作已有控件。隐藏面板、窗口树和 Lua UI 程序的手动创建编辑器；对应 MCP 工具及 AI 创建的窗口保持可用。
- **AI 逻辑**：通用 JSON 程序编辑、校验、保存、启停、重启和删除；查看对象源、运行成本及错误，不内置特定游戏的控制面板。
- **MCP 调用日志**：时间、来源、状态、命令、耗时列表与独立详情区；默认只看远程请求，可筛选、暂停、复制和导出。关闭 Toast 不会关闭日志。
- **设置**：中文/英文、Classic 紫色/Dark/Light、缩放、透明度、每页数量、三类屏幕日志开关和 MCP Toast；修改后自动保存加载，窗口/表格布局也自动保存。可选预设和书签放在设置的独立分页。

## MCP 启动

```powershell
python mcp/mcp_server.py --port 27184
```

启动后浏览器功能控制页面默认位于：

```text
http://127.0.0.1:27185/
```

开关保存到 `mcp/mcp_features.json`。使用 `--admin-port` 修改端口，使用 `--no-admin` 关闭页面；管理端口监听非本机地址时必须同时配置 `--admin-token`。

如果 MCP Server 就运行在目标 Android 设备上，使用直连模式，不需要 ADB 端口转发：

```sh
python mcp/mcp_server.py --port 27184 --direct
```

默认模式会先尝试直连 `127.0.0.1:27184`，连接失败后才自动执行 `adb forward`；`--direct` 会关闭这一回退行为。

客户端配置示例：

```json
{
  "mcpServers": {
    "zygisk-il2cpp": {
      "command": "python",
      "args": ["D:/path/Zygisk-il2cpp-mcp/mcp/mcp_server.py", "--port", "27184"]
    }
  }
}
```

完整工具说明见 [mcp/README.md](mcp/README.md)。

ARM64 伪代码能力集成 Apache-2.0 许可的 [ghidra-native](https://github.com/radareorg/ghidra-native)，使用 Ghidra Decompiler 与 Sleigh 语义恢复 C 风格代码，无需 Java、RetDec 或外部反编译服务。反编译器使用独立 `libghidra_decompiler.so`：功能默认开启并随注入主体加载，通过受限回调读取目标当前的代码、只读数据、字符串与全局变量。对 `libil2cpp.so` 中的精确方法起始地址，模块会反查运行时元数据并锁定 Ghidra 函数原型和类字段布局；未匹配或 IL2CPP API 不可用时自动退回普通 Native 反编译。缺失、ABI 不兼容或初始化失败只会停用反编译，不影响 Hook、内存、Lua、Dobby 与断点功能。

## 风险提示

此项目面向你有权调试的应用。错误的实例地址、replacement 地址、返回 ABI 或机器码 Patch 会直接导致目标进程崩溃。代码 Patch 不会由 `DobbyDestroy` 自动恢复。
