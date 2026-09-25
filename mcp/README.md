# Zygisk IL2CPP MCP Bridge

当前服务版本：**2.6.1** · 作者：**洋葱落日 && DUM**

2.6.1 增加自定义 SO 注入／任务状态工具，改进 IL2CPP 关系链短路径搜索、旧版 API 兼容和 AI 逻辑调用。手机模块、MCP 服务和 WebUI 包使用同一版本；更新文件后请重启 MCP 服务，手机原生功能还需更新模块并重启目标。

- `native_library_inject`／`native_library_status`：上传并加载 SO 到已连接目标，支持状态查询与显式 JNI 初始化。
- `il2cpp_relation_find`：默认 `strategy=shortest`，可选有界 `all_paths`，保留字段偏移与实例导航。
- `il2cpp_status`：返回旧版类枚举、数组布局及错误诊断，区分支持与已验证状态。
- `dobby_resolve_symbol`：仅查已加载模块的动态导出，不再扫描磁盘内部符号。
- `logic_program_*`：自定义函数、有界循环、集合运算与显式开启的带参托管调用。
- 原有资源、内存、断点、日志与项目工具继续保留；每项用法可通过 `debug_help` 查询。

新兼容路径和复杂加载场景仍需更多设备验证。自定义 SO 会在目标进程执行，不具备反编译工作进程的隔离能力。

这是一个仅依赖 Python 标准库的 stdio MCP Server。它通过 ADB 转发连接目标进程内的本地 Socket，并把 IL2CPP、普通 Native 内存、LuaJIT、Dobby、汇编和断点能力暴露为 MCP tools。

## Start

先在 WebUI 或 `/data/adb/zygisk_il2cpp_mcp/apps.txt` 中加入目标游戏包名，并在修改配置后重启目标游戏。默认命令端口是 `27184`。

```powershell
python mcp/mcp_server.py --port 27184
```

如果 MCP Server 直接运行在目标 Android 机器上，使用直连模式，无需 ADB 转发：

```sh
python mcp/mcp_server.py --port 27184 --direct
```

默认模式会先连接本机 `127.0.0.1:27184`，失败后再尝试 `adb forward`；`--direct` 会禁用自动转发。

仅在建立连接失败时自动转发并重试；连接后发生超时/断开不会自动重放命令。目标重启后需重新解析运行时地址。

2026-09-12 测试反馈修复：指针链批量结果/列表兼容原生数组，输入等待不再阻塞并行推送。此修复仅涉及 MCP Python，更新正在使用的 `mcp` 目录并重启 MCP 进程即可，无需为这几项重新编译或刷入模块。

方法查询诊断：方法查询可自动回传执行阶段，断开时 MCP 错误中包含 `last_native_stage`，无需手工抓 logcat。此项需要新版模块配合；旧模块仍按原查询协议工作。ImGui 注入开关、工作台布局及兼容边界。

MCP 还会启动独立的浏览器控制页面，默认地址是 `http://127.0.0.1:27185/`。所有功能开关第一次启动时全部开启，页面修改会立即影响 `tools/list` 并保存到 `mcp_features.json`。可用参数：

```text
--admin-host 127.0.0.1
--admin-port 27185
--admin-token <token>
--no-admin
--feature-config <json-path>
```

非回环管理地址必须设置令牌。功能读取、单项切换和全部切换只存在于浏览器管理 API，不注册为 MCP tools，因此 Agent 无法看到或调用管理接口。被禁用的工具不会返回给 Agent，`raw_hook_call` 也不能绕过开关；关闭全部功能后仍可通过浏览器页面恢复。

也可以从项目根目录使用整理好的启动脚本：

```powershell
powershell -ExecutionPolicy Bypass -File mcp/start_mcp.ps1 `
  -Python "D:\Program Files (x86)\python3.14.6\python.exe" `
  -Adb "D:\ASWJ\platform-tools\adb.exe" `
  -Port 27184
```

可直接复制并修改的客户端配置位于 `mcp/client-config.example.json`。MCP 只依赖 Python 标准库，不需要安装第三方包。

本机第一次连接失败时会自动执行：

```text
adb forward tcp:<port> tcp:<port>
```

连接多个 Android 设备时使用 `--serial <device>`。若目标地址可直接访问，可使用 `--no-adb-forward`。

## MCP client config

```json
{
  "mcpServers": {
    "zygisk-il2cpp": {
      "command": "python",
      "args": ["D:/AndroidStudioProjects/Zygisk-il2cpp-mcp/mcp/mcp_server.py", "--port", "27184"]
    }
  }
}
```

## IL2CPP tools

- `il2cpp_status`：初始化并附加 IL2CPP 线程，返回基址和 domain。
- `il2cpp_dump_file`：把完整 C# 元数据 Dump 直接写入目标应用私有目录，不通过 MCP 返回 Dump 内容；MCP 只收到 `success`。
- `il2cpp_list_images`：枚举已加载的程序集镜像。
- `il2cpp_list_classes`：按命名空间/类名过滤镜像内类型。
- `il2cpp_list_methods`：枚举方法、参数类型、返回类型、绝对地址和 RVA。
- `il2cpp_list_fields`：枚举字段类型、Offset、Flags、静态/常量状态，可选择父类字段。
- `il2cpp_search`：跨一个或全部 Image 模糊搜索 `class`、`method` 或 `field`，支持 Image/命名空间/类过滤、大小写、包含/前缀/精确匹配及分页。
- `il2cpp_find_method`：精确解析方法。
- `il2cpp_invoke` / `il2cpp_call`：通过 `il2cpp_runtime_invoke` 调用静态方法或指定实例地址的方法。
- `il2cpp_object_inspect`：按地址加载对象及字段值，可包含继承字段。
- `il2cpp_list_items`：分页读取一维数组或 `List<T>`。
- `il2cpp_dictionary_get`：按类型化 Key 调用 `Dictionary<TKey,TValue>.get_Item`。
- `il2cpp_hook`：解析方法后，将其 Dobby Hook 到自定义原生 replacement 地址。
- `il2cpp_hook_return`：解析方法后安装固定返回值 Hook。
- `il2cpp_unhook`：解析方法后通过 `DobbyDestroy` 恢复。

`il2cpp_invoke.arguments` 支持布尔值、数值、字符串、`null` 和枚举。引用对象参数可把对象地址作为字符串传入；实例方法必须提供 `instance_address`。

也支持显式参数类型，适合数值类型、对象地址或枚举容易产生歧义的调用：

```json
[
  {"type":"i32","value":42},
  {"type":"string","value":"test"},
  {"type":"object","value":"0x7abc123000"},
  {"type":"enum","value":"RoleSyncState.Walking"}
]
```

枚举参数会根据目标方法元数据自动读取真实底层整数类型，可使用以下任一写法：

```json
0
"Walking"
"RoleSyncState.Walking"
{"enum": "RoleSyncState.Walking"}
```

Flags 枚举成员可用 `|` 组合，例如 `{"enum":"Read|Write"}`。枚举返回值按其底层整数类型返回。

`il2cpp_dump_file` 可选 `image / namespace / class_name` 精确过滤（`namespace: ""` 选择全局空间）；不传参数导出全量。Dump 文件保存到目标应用的：

```text
files/zygisk_il2cpp_mcp/il2cpp_dump_<随机后缀>.cs
```

对应 Android 路径通常位于 `/data/user/0/<目标包名>/files/zygisk_il2cpp_mcp/`。每次生成独立文件，不覆盖旧 Dump；结果只含 `success/path/class_count`，不返回正文。

类型流程图、冻结/追踪管理、多层基址链、批量加载。流程图使用 `il2cpp_type_graph`；已有 Dump、对象检查器和 `memory_scan_base` 均直接扩展，无重复同义工具。

### 多类型关系链

- `il2cpp_relation_selection`：创建和维护命名选择集，可分批追加任意多个精确类/字段；修改使用 `revision` 防止覆盖并发修改。
- `il2cpp_relation_find`：按 `any`、`all` 或 `ordered` 搜索有界关系路径/网络；中间可经过未选择类型，达到预算会返回 `complete=false` 和 `stop_reasons`。
- `il2cpp_relation_results`：分页读取路径、节点、边和选择器映射。字段偏移是符号偏移，不是绝对地址。
- `il2cpp_relation_resolve`：从实时根对象或现有模块/指针链配方，在游戏帧中校验并加载一条可解引用路径；通过 `workspace_result` 轮询完成结果。

`ordered` 模式中，首个选择器带 `field` 时会约束第一条边，例如 `World.player`；后续选择器带字段时表示精确终点，例如 `Player.health`，标量字段也可以作为终点。因此可表达并验证 `World.player(+0x18) → Player.health(+0x704)`，实际偏移来自当前运行时元数据。返回节点中的类、字段、`storage_address`、引用对象、标量和渲染资格，可分别交给已有类型窗口、`workspace_navigate`、内存编辑/冻结与渲染工具处理；解析工具本身不写内存、不调用 getter、构造器或任意方法。

## Java 层对象渲染与 ImGui 控制

原有 38 个渲染/UI 工具继续保留，新增 33 个工作台与高级 UI 工具，共 71 个；接入默认开启的原有功能组，Lua 程序还依赖 `lua`。浏览器管理接口仍不暴露给 Agent。

新增 `overlay_upsert_window/upsert_node/apply_tree` 支持独立父窗口、子窗口、控件树、表格/分页及绑定；`overlay_program_*` 管理可编程 UI。`render_*_rule(s)` 管理字段规则、血条和自动包围盒。工作台新增帧队列检查器/精确调用、符号书签和目标端剪贴板/文件导出。完整参数以 `debug_help` 为准。

- UI：`overlay_status`、`overlay_set`、`overlay_set_window`、`overlay_reset`，管理中英语言、Classic/Dark/Light 主题、可见性、原生折叠、位置尺寸、缩放和透明度。
- 对象：`render_status`、`render_list_objects`、`render_add_object`、`render_update_object`、`render_remove_object`、`render_clear_objects`、`render_set_object_position`、`render_set_object_bones`。
- 样式/相机：`render_set_style`、`render_set_camera`、`render_list_cameras`、`render_set_camera_matrix`、`render_project`。
- Unity 采样：`render_bind_update`、`render_unbind_update`、`render_binding_status`、`render_find_objects`。
- 连续跟踪：`render_track_class`、`render_list_tracked_classes`、`render_untrack_class`、`render_refresh_class`。
- 相机刷新与异步投影：`render_refresh_cameras`、`render_projection_result`。
- 自定义 UI：`overlay_set_panel`、`overlay_set_widget`、`overlay_list_custom_ui`、`overlay_remove_custom_ui`、`overlay_ui_events`。
- 调用日志：`overlay_call_logs`、`overlay_clear_call_logs`，独立于 Toast。
- 图元：`render_set_primitive`、`render_list_primitives`、`render_remove_primitive`、`render_clear_primitives`。

显示走已有 Java SurfaceView，不 Hook EGL。Java 菜单启动后自动探测已加载 IL2CPP 的 MonoBehaviour 帧方法；检查 `render_binding_status` 的 automatic/auto_error/automatic_probes、thread_id 和 age_ms。探测失败保留普通内存/类型工具；先解除自动探针后仍可通过 `render_bind_update` 手动绑定。`render_find_objects` 返回受理状态，需要轮询结果。普通目标可提供手动坐标、骨骼和相机矩阵；对象操作只改变可视化，不销毁或移动游戏对象。

每条命令可通过 `debug_help` 查询。新工具需要同时更新设备模块与整个 MCP 目录（包括 `render_tools.py`），不能只替换 `mcp_server.py`。

矩阵被裁剪时使用 WorldToScreenPoint；此模式的 `render_project` 返回 pending/request_id，通过 `render_projection_result` 获取结果。所有 Unity 投影仍在游戏帧执行。

面板、控件和图元工具接收 descriptor 对象，按 ID 创建或局部更新。普通控件保存描述不执行动作，用户操作才触发绑定命令；Lua 程序需显式启用才能执行。图元新增 quad/polygon/bezier/mesh、前景层和裁剪区域，屏幕模式无需 Unity。手动菜单隐藏窗口树/控件/Lua 程序创建编辑器，MCP 接口及已创建控件仍可用；语言/主题/Toast 在“设置”页，调用历史在“MCP 调用日志”页。

对象位置和已绑定字段按游戏帧采样，旧 `sample_hz/refresh_ms` 参数保留兼容但不再控制采样周期。`workspace_export_result/text/job/logs` 在目标侧导出，返回状态/路径而非文件正文；文件位于目标 `files/zygisk_il2cpp_mcp/exports`，预设位于 `presets`。预设不会自动恢复游戏动作或运行脚本。

语言、主题、缩放、透明度、Toast 及原生窗口/表格布局自动保存到目标 `files/zygisk_il2cpp_mcp/settings/ui.json` 并加载，不恢复旧对象地址或执行游戏动作。手动浏览器改为场景/IL2CPP 分页表格，检查器、调用与分析各自独立窗口；分析默认自动读取范围，长度选项位于高级设置。

## Memory tools

- `memory_read`：从目标进程完整可读的映射区间读取原始字节，返回小写十六进制数据。
- `memory_write`：向目标进程完整可读写的映射区间写入十六进制字节，返回覆盖前的数据并校验写入结果。
- `memory_read_value`：按小端序读取 `bool`、整数、浮点数或指针值。
- `memory_write_value`：编码并写入类型化数值，同时返回覆盖前的类型化数值。
- `memory_list_modules`：列出当前进程的可执行模块、起止地址、load bias、映射段数量和同名实例序号。
- `memory_find_module`：通过精确模块名/完整路径和从 1 开始的 `occurrence` 定位指定模块实例，并返回全部映射段。
- `memory_address_info`：定位地址所在的映射区域、权限、文件偏移、所属模块和相对偏移。
- `memory_resolve_address`：解析模块 load bias/start 加有符号 Offset。
- `memory_resolve_pointer_chain`：解析最多 32 级的模块基址或绝对基址指针链，并返回每一级地址。
- `memory_read_pointer_chain` / `memory_write_pointer_chain`：解析指针链后读写类型化数值。
- `memory_scan_base`：多线程扫描指向模块基址、模块 Offset 或绝对地址的指针。
- `memory_search`：在模块实例或指定地址范围内搜索字节特征并创建过滤会话。
- `memory_search_value`：编码并搜索类型化数值。
- `memory_search_exact`：把同一个数值按多个勾选的类型分别执行精确搜索。
- `memory_search_fuzzy`：创建未知初值快照，再按变化、不变、增大或减小持续过滤。
- `memory_search_results`：分页读取地址和当前快照，避免一次返回过多数据。
- `memory_filter`：按新字节、变化状态或无符号大小关系过滤现有结果。
- `memory_filter_value`：按类型化数值执行相等/不等过滤。
- `memory_search_clear`：释放搜索会话和快照。

单次读写范围为 1 到 65536 字节。普通写入不会修改只读或仅可执行页面；修改原生代码请使用 `dobby_patch_code`。调用示例：

```json
{"address":"0x7abc123000","size":16}
{"address":"0x7abc123000","hex_bytes":"01000000"}
{"address":"0x7abc123000","value_type":"f32","value":1.5}
```

类型化调用支持 `bool`、`i8`、`u8`、`i16`、`u16`、`i32`、`u32`、`i64`、`u64`、`f32`、`f64`、`ptr32` 和 `ptr64`。指针类型需要按目标进程 ABI 选择；整数和指针写入值可使用 `0x...` 字符串。

指针链从 `module load_bias + base_offset` 或 `base_address + base_offset` 开始。每个 `offsets` 元素执行“读取当前指针，再加该有符号 Offset”；结果返回全部中间步骤。基址扫描的 `workers=0` 自动选择至少 2 个、最多 32 个线程，也可显式指定。默认 KittyMemory 可并行读取，选择驱动时读取在 Root 通道中串行转发；字节匹配使用 KittyScanner。

这些工具不依赖 IL2CPP 初始化，可用于普通 Native、Mono 或其他引擎进程。默认数据读写经 KittyMemory 的严格 `Normal` syscall 模式，也可通过 WebUI 选择外部驱动；只接受完整传输，不以不可读页的伪造数据参与搜索。扫描在有上限的本地快照上进行，保留 nibble 通配符、对齐和区域多选。

### 外部 Root 内核驱动

WebUI 恢复驱动和设备节点设置，保存后重启目标。驱动由外部 Root companion 打开并仅对固定目标 PID 读写；不在注入进程直接打开。`memory_backend_status` 报告后端、transport、state、reason；`unprobed` 表示尚未读写验证，`drivers_enabled:true` 只表示允许选择驱动。KMA 库存在时按 ARM64 条件链接；其他 ABI 使用默认 KittyMemory。失败不静默切换后端。

代码补丁也使用 KittyMemory，临时开放所需页的写权限、写入、恢复权限并刷新指令缓存。Dobby 仍负责安装/解除 Hook 和自己的 trampoline 内部操作；`dobby_patch_code` 保留旧名称以兼容客户端，但不再调用 DobbyCodePatch。

### 模块和重复名称

模块实例通过映射路径与 load bias 区分。同名模块按起始地址排序，`occurrence` 从 1 开始。例如：

```json
{"module_name":"libgame.so","occurrence":2}
```

返回值包含模块整体 `start`/`end`，以及每个 region 的 `start`、`end`、`permissions`、文件 `offset` 和 `path`。

### 搜索和过滤

新增 `memory_search_tabs`：管理独立搜索标签、精确/模糊/联合搜索、改善、结果分页、多选、保存项及文件/剪贴板导出。新增 `memory_batch_edit`：对 1–256 个已选结果或明确保存项进行批量写入/冻结，必须 `confirm:true`，非原子操作、失败即停止。冻结管理复用 `memory_freeze_*`，不增加重复工具。

`memory_search_exact` 新增 `100;200:512` 无序组、`100;200::512` 有序组、`10~20` 范围及混合类型后缀，支持 hex/UTF-8/UTF-16 搜索；高级搜索返回 `sessions` 和兼容的 `searches`。`memory_filter_value` 省略 `value_type` 时按原生结果类型改善，支持变化、大小比较和指定增减值；原普通数值等于/不等于调用保留。`memory_search_results` 支持至 100000 的 offset。请检查 `truncated/stop_reason`，导出的“全部”仅指全部缓存候选。

手动页面和 MCP 使用相同状态。使用 `debug_help` 查看完整参数。断点的 PC/整数寄存器快照和回溯已由 `breakpoint_hits` / `breakpoint_backtrace` 返回，属于采样而非暂停式调试。

按模块搜索机器码特征：

```json
{
  "module_name":"libgame.so",
  "occurrence":1,
  "pattern":"48 8B ?? A?",
  "max_results":1024
}
```

也可以使用 `start_address` 和 `end_address` 指定范围，或用 `memory_search_value` 搜索小端序数值。搜索返回 `session_id` 和地址列表；之后可调用：

```json
{"session_id":1,"mode":"changed"}
{"session_id":1,"mode":"equals","pattern":"01000000"}
```

过滤模式：

- `equals` / `not_equals`：按相同长度的新特征过滤，支持 `?` 通配半字节。
- `changed` / `unchanged`：与上次搜索或过滤时保存的快照比较。
- `increased` / `decreased`：把最多 8 字节的数据按小端无符号整数比较。

每次过滤后会更新保留结果的快照。最多同时保存 16 个搜索会话，超过后自动替换最旧会话；单次最多扫描 512 MiB、返回 10000 个地址，特征长度最多 256 字节。`memory_types` 可多选 `anonymous`、`heap`、`stack`、`app_code`、`system_code`、`app_data`、`ashmem`、`java` 和 `other`。

## 持久日志与调试工作流

- `journal_status`：查询 Root 持久日志、当前精确会话文件、异步队列和写入失败计数，不创建或清理日志。
- `journal_query`：列出会话文件或按字节游标分页读取单个精确会话；一次调用有扫描和返回大小上限，需要按 `next_cursor` 继续。
- `journal_export`：把一个会话复制到 Root 管理的目标隔离目录 `/data/adb/zygisk_il2cpp_mcp/journal/<target>/exports/`，只返回回执、路径和字节数。
- `diagnostic_export`：在同一 Root 导出目录生成有界诊断 JSON，包含日志尾部、模块、后端和系统摘要；不包含原始内存、全系统 logcat 或 tombstone。
- `debug_project`：维护按目标及版本隔离的持久调试项目，支持 list/create/get/update/archive/summary/export。项目只保存符号配置、任务、发现、书签、产物和笔记，不自动重放调用，也不把跨重启的实时地址视为仍有效。
- `debug_snapshot`：捕获对象、List、Dictionary 或一段已校验内存，支持 list/get/diff/export。IL2CPP 捕获在游戏帧中有界执行；它不是全进程一致性快照。跨会话/目标比较必须显式设置 `allow_cross_session=true`。
- `workspace_jobs`：统一查看手动 UI 与 MCP 的会话内后台任务，并可提交严格白名单中的只读命令、查询结果或协作取消。提交成功只表示受理；`cancel_requested` 也不等于已经取消。它不终止线程，不接受写入、Lua、调用、嵌套工作区命令或任意 PID。
- `change_history`：分页读取变更记录、导出或尝试撤销一条当前会话记录。撤销要求精确 session、`confirm=true`，并重新核对后端、映射、当前字节和冻结冲突。
- `debug_stop_all`：在 `confirm=true` 后尽力恢复调试器拥有的暂停线程并停止/暂停冻结、追踪、采样断点、Frida、原生逻辑和 UI 程序。它与撤销分开，不恢复内存、不撤销已调用方法，也不保证回滚任意 Hook 副作用。

命令日志和 change journal 都不是事务审计。change journal 明确是 best-effort：记录/持久化失败时原始内存写入或代码 Patch 仍可继续，因此缺少记录不能证明写入没有发生。只有成功记录且当前环境仍完全匹配的改动才可能由 `change_history(op=undo)` 撤销。

对象快照的 `next_offset/has_more` 用于跨嵌套对象继续捕获，跳页不会跳过引用发现；每页是一次新的有界捕获，不是同一瞬间的全图快照。List／Dictionary 回执保留实际类型。内存差异比较最多 65536 字节，按数值偏移排序，兼容旧快照格式，`diff` 的 `offset` 可到 65536。项目／快照 `export` 只返回成功状态、文件路径和字节数，不返回完整文档。

## LuaJIT tools

- `lua_status`：只查询状态，不创建 VM。
- `lua_execute`：首次调用时创建持久 LuaJIT VM，支持多行脚本和 FFI。
- `lua_logs`：读取脚本、`hookCPU` 回调和 `call` 闭包的持久日志。
- `lua_reset`：移除 Lua 所拥有的 Hook 和主线程泵，并退回未初始化的懒加载状态。

全局 Lua API 包含 `getBase`、`hookCPU`、`hookfunc`、`removehook`、`remove_all_hook`、`call`、`msleep`，以及 `readByte/readDword/readQword/readFloat/readDouble/readPtr/readBytes/readString` 和对应的写入函数。`call(function)` 优先通过 `eglSwapBuffers`，其次 `ALooper_pollOnce` 在主线程执行；若目标不具备这两个符号，300 ms 后由兜底线程执行。内置 `regs_t` FFI 定义可把 `hookCPU` 的 lightuserdata 转为寄存器上下文。

## 汇编与硬件断点 tools

- `assembly_status` / `assembly_assemble`：查询状态并把单条 AArch64 文本指令汇编为机器码。
- `assembly_disassemble`：使用 KittyMemory 读取和 Capstone 反汇编 ARM64 内存。
- `assembly_patch`：汇编后通过 KittyMemory 修改可执行地址并恢复权限。
- `breakpoint_status` / `breakpoint_set` / `breakpoint_list` / `breakpoint_hits` / `breakpoint_clear` / `breakpoint_clear_all`：管理不暂停进程的 ARM64 perf 硬件执行断点和数据监视点。
- `breakpoint_backtrace`：通过 `breakpoint_hits` 返回的 `hit_id` 读取命中时采样的用户栈回溯，并解析每帧所属映射/模块。

汇编、反汇编与硬件断点当前是 ARM64 能力。ARM32 或禁止 `perf_event_open` 的内核会返回明确的 unsupported/failed 原因，其他 MCP tools 仍正常使用。

### 外部暂停调试器

暂停调试器由目标进程外的 Root companion 执行，只允许启动时已绑定的目标 PID，与上面的 perf 采样断点互相独立：

- `debugger_status`：查询 ARM64 支持、拥有的暂停线程、租约和清理状态；状态可用不代表 ptrace 权限已经通过，权限只在实际暂停时确认。
- `debugger_threads`：分页枚举固定目标的线程，同时返回用于抵抗 TID 重用的 `thread_start_time`；当前请求线程不能由此通道暂停。
- `debugger_control`：pause/resume/resume_all/step/set_registers/renew/help。暂停必须提交 TID、匹配的启动时间和 `confirm=true`；租约范围 1–15 秒，过期、Broker 断线或所有者退出会触发清理。
- `debugger_registers`：只读取调试器已拥有并停止的线程，返回 X0–X30、SP、PC、PSTATE、`stop_id` 与剩余租约；读取不会续租。
- `debugger_backtrace`：读取最多 64 帧的 ARM64 帧指针回溯，并报告不完整原因；省略帧指针或 PAC 可能提前终止。

`step` 和寄存器写入要求当前 `stop_id`，写 PC/SP 还会检查执行/写映射及对齐。当前不提供按地址停止断点、step-over/step-out、FP/SIMD/SVE 寄存器、信号抑制或任意 PID 附加。暂停一个线程时其他线程仍运行，也可能等待它持有的锁；暂停期间不要发起依赖 Unity 游戏帧或目标锁的调用。传输错误后先查 `debugger_status`，不要盲目重复变更命令。

Root 通道允许传输失败后重新鉴权，但不重放已经发送的操作；变更响应丢失标为结果未知。重连保留暂停清理状态，且不会复用旧 perf 事件 ID 或 `stop_id`。仍持有暂停线程时，Root 日志／驱动操作会返回忙，避免慢磁盘阻塞租约清理；应先恢复线程，再查询日志或导出诊断。

## Ghidra 反编译

- `decompiler_status`：查询 ARM64 Ghidra Native 引擎、运行时内存读取和 IL2CPP 类型元数据状态。
- `decompile_function`：读取指定地址和范围，返回 Ghidra/Sleigh 生成的 C 风格伪代码。

反编译器使用独立的 `libghidra_decompiler.so`，不依赖 Java、RetDec 或外部服务。输入地址精确命中 IL2CPP 方法起始地址时，会自动注入方法名、返回类型、`this`、托管参数、隐藏的 `MethodInfo*` 参数，以及声明类和继承类的实例字段布局。未命中 IL2CPP 方法时仍可作为普通 ARM64 Native 反编译器使用。

引擎通过受限回调读取目标实时内存，并把 `/proc/self/maps` 中的只读区域传给 Ghidra，用于分析全局数据和已初始化的运行时字符串。函数分析严格限制在请求范围内；范围外直接分支会生成截断桩，不再导致整个反编译请求失败。

当前限制：范围外尾调用可能仍显示为 `halt_missing()`；调用目标尚未批量替换成 IL2CPP 方法名；未初始化的 IL2CPP 编码字符串槽不会自动展开为文本。反编译器缺失、ABI 不兼容或初始化失败只会停用这一功能，不影响内存、Hook、Dobby、Lua 或断点工具。

## 原生 AI 逻辑工具

`logic_program_schema/validate/set/list/get/control/remove` 提供独立于 Lua 的通用 JSON 逻辑运行时。AI 通过 MCP 查询语法并提交变量、条件、遍历、动态对象源、字段读取及绘制/UI 变量输出；目标游戏的字段、筛选规则和窗口布局由调用方下发，底层不预装业务程序。

原生界面的“AI 逻辑”页可编辑、校验、启停和查看诊断。设置 `auto_start=true` 后保存 `default` 工作区预设，可在下次启动重新解析对象源。当前不支持原生逻辑中的方法调用、字段写入或 Hook。

## Help 与兼容性

- `runtime_capabilities`：一次返回内存后端、LuaJIT、汇编、断点、Dobby 与 IL2CPP 的独立状态，不会提前初始化可选能力。
- `debug_help`：传 MCP 工具名时返回本地说明、Schema、来源和功能组，并遵从功能开关；不传时只列出当前对 Agent 可见的 MCP 工具。传未知于 MCP 目录但受支持的原生命令主题时，应用相同功能门控后返回原生 usage，不能用 HELP 绕过已禁用能力。

## JNI Toast tools

- `mcp_toast_status`：读取自动 Toast 开关，默认开启。
- `mcp_toast_set_enabled`：开启或关闭 MCP 调用内容 Toast。
- `mcp_toast_show`：主动显示自定义 Toast，不受自动开关影响。

自动 Toast 显示 MCP tool 名和 arguments；内容过长时由原生端截断，不影响实际调用。

## Dobby tools

- `dobby_resolve_symbol`：通过 `DobbySymbolResolver` 解析符号。
- `dobby_hook`：按 target/replacement 原生地址安装 Hook，并返回原函数 trampoline。
- `dobby_hook_return`：按地址安装固定返回值 Hook。
- `dobby_instrument` / `dobby_trace_get`：插桩并读取执行计数、最新线程和寄存器快照。
- `dobby_trace_backtrace`：读取 Dobby 插桩最近一次命中的 ARM64 帧指针回溯，并解析模块区域。
- `dobby_patch_code`：兼容名称；使用 KittyMemory 写入机器码，单次最多 4096 字节。回滚需要自行保留并写回原字节；`dobby_destroy` 不撤销独立字节补丁。
- `dobby_destroy`：卸载通过 Dobby Hook/Instrument 安装的拦截。
- `dobby_list_hooks`：列出由桥接层记录的 Hook 和插桩。
- `dobby_version`：读取内置 Dobby 版本标识。

桥接层还保留现有 Socket 功能：`ping`、剪贴板、Unity 输入框以及 `raw_hook_call`。因此新增原生命令后，无需先改 MCP Server 也能调用。

## 注意

Hook 和代码 Patch 直接修改目标进程。replacement 地址、ABI 返回类型或机器码错误都可能导致游戏崩溃。`dobby_patch_code` 是直接代码写入，不会被 `dobby_destroy` 自动撤销；需要调用方自行保存并恢复原始字节。
