# Bootstrap 模块指南

`bootstrap` 目录存放了系统启动和初始化（引导阶段）相关的核心组件。在应用或系统启动时，这些模块负责准备必要的外部进程、配置全局工具链（如日志系统）等工作。

## 模块功能解析

### 1. `camera_node.py`
**功能说明**：后台相机节点启动器（`CameraNodeLauncher`）。
- 负责自动启动和停止外部 ROS 相机节点（依赖底层 bash 命令及 `start_node` 环境）。
- 基于配置文件中 `vision` 相关的设置（例如 `auto_start_node`、`launch_command` 和 `startup_delay_sec`）来控制是否启动以及启动延迟。
- 通过管理 `subprocess` 和其进程组（PID bindings），确保系统停止时能干净地杀掉底层的 ROS camera 子进程树。

### 2. `logging.py`
**功能说明**：全局结构化日志配置模块。
- 基于 `structlog` 库，为全应用提供统一步伐的日志输出方式。
- `setup_logging()`：在应用启动阶段被调用，负责设定日志管道与过滤器。它能够根据部署环境（`settings.env`）自动切换输出格式——开发环境应用彩色终端（`ConsoleRenderer`），生产环境应用易于搜集的 JSON 格式输出（`JSONRenderer`）。
- `get_logger(name)`：通用的获取日志实例方法。其他业务模块均使用 `from src.robot_agent.bootstrap.logging import get_logger` 来产生日志。

## 如何修改以及二次开发 (二开指南)

如果你希望改造现有能力或者将新的启动层组件加入本模块，可遵循以下规范和建议：

### 对 `camera_node.py` 进行二开
1. **添加跨平台支持**：
   目前的 `os.setsid` 和 `bash` 调用均为 `posix`（Linux\macOS） 特占。如果需要迁移至 Windows 或者其它系统，需改写 `Popen` 的 `creationflags` 和进程终止（如 `taskkill`）的逻辑。
2. **引入健康检查机制**：
   当前节点启动仅依赖 `startup_delay_sec` 的静态死等（`time.sleep`）。你可以将其改造为基于 ROS Node API 或 ping 的健康监测器。例如监测某 topic 是否正式开始 push 数据后，再通知应用主流程。

### 对 `logging.py` 进行二开
1. **添加文件或集中化输出**：
   你可以增加额外的全局处理器。例如引入 `logging.FileHandler` 生成本地持久化日志，或是结合 Filebeat/Logstash 直接将数据抛送到集中式日志平台中。
2. **扩充共享上下文**：
   在 `shared_processors` 当中按需增加 `structlog` 处理器，如自动绑定线程ID（Thread ID），异步Task ID，甚至请求Trace ID，让每一条日志都携带上下文信息。

### 增加新的 Bootstrap 模块
如需新增组件，请首先明确 **“什么代码应该放在 `bootstrap` 目录？”**

**`bootstrap` 模块的设计职责（What belongs here）：**
存放所有在应用**核心业务循环开始前**必须就绪的“一次性”环境准备与依赖组装代码。具体包括：
1. **基础工具初始化**：日志配置（现有的 `logging.py`）、系统追踪（Tracing/APM）预埋、全局异常处理器注册。
2. **外部依赖启动/挂载**：外部设备节点（现有的 `camera_node.py`）、数据库连接池启动、Redis/MQ 连接就绪确认。
3. **前置环境与健康检查**：目录读写权限校验、环境变量强校验、音频/CUDA 设备自检代码、端口占用检测。
4. **依赖注入与组装配置**：加载系统级配置文件验证其合法性（Schema 校验），或者绑定全局 IoC（控制反转）容器。
5. **系统级信号接管**：如捕捉 `SIGINT`/`SIGTERM` 实现应用整体的平滑关闭（Graceful Shutdown）钩子。

**什么代码 __不应该__ 放在 `bootstrap` 目录（反模式）：**
- ❌ **具体业务逻辑**：如 Agent 思考流程、对话管理、意图识别模型。
- ❌ **长时间运行的核心任务**：Bootstrap 的各任务应都是“启动 -> 返回”的轻量化动作，不能在其中执行死循环阻塞主线程（阻塞型任务应由 bootstrap 拉起子进程/后台线程，并马上把控制权交还）。
- ❌ **供其他业务调用的工具函数**：如果是给 Agent 流程反复使用的时间格式化、字符串处理函数，应放去 `utils` 或 `helpers` 模块，只有**仅在启动阶段**调用的代码才放这里。
