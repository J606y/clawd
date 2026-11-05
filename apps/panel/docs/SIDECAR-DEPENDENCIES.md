# Sidecar 外部依赖规范：PyYAML

## 1. 约束与定位
- **模块设计原则**：Sidecar 绝大多数核心运维功能（主机状态、服务启停、技能解析、数据库文件夹、日志与备份恢复）坚持零第三方依赖与仅标准库设计，防止引入外部依赖冲突；严禁直接 import Hermes 内部代码。
- **YAML 脱敏例外**：为彻底杜绝 YAML 嵌套、流式语法（flow mapping/sequence）、转义字符、共享别名引用（anchors/aliases）和复杂语法造成的凭据泄漏与误判，用户已明确批准引入锁定版本的安全 YAML 解析依赖 `PyYAML`。
- **不可用安全降级**：若运行环境未安装 `yaml` 模块，Sidecar 模块加载与核心服务正常启动，但调用 `_redact_yaml` / `get_config` 时显式返回 HTTP 503 结构化错误 `yaml_parser_unavailable`（\"YAML 解析组件未安装，无法安全脱敏配置\"），绝不回退至脆弱的正则表达式，绝不返回未脱敏原文。

## 2. 锁定版本与哈希校验
依赖清单定义于 `sidecar/requirements.txt`，锁定版本为 **PyYAML 6.0.3**，配置了包含全部官方 PyPI 发布产物的 SHA256 哈希校验。

安装命令（使用 `--require-hashes` 强制哈希完整性校验）：
```bash
<python-bin> -m pip install --require-hashes -r sidecar/requirements.txt
```

## 3. 安全防护策略
Sidecar 内部使用 `yaml.SafeLoader`（禁止任何 `unsafe_load`），并在 AST（抽象语法树）层面执行深度与安全限制：
1. **多文档拒绝**：仅允许单文档 YAML 配置，多文档结构（`---`）保守拒绝（`config_parse_failed`），防范注入。
2. **标签白名单**：仅允许代码显式列出的 YAML 2002 标准标量及集合标签；命名空间前缀不视为白名单。自定义、未知、Python对象标签及未列入的merge标签均保守拒绝。
3. **递归与循环别名保护**：遍历过程中维护正在访问的节点栈，检测到循环引用即刻中断拒绝，防范 Billion Laughs / 别名炸弹。
4. **复杂度上限**：解析前按 UTF-8 字节限制为 1MiB；AST 最大嵌套深度限制为 30 层，节点总数上限为 5000，超出即报结构异常拒绝。AST限制在compose后校验，不宣称已限制解析器内部的峰值资源。
5. **复杂映射键与重复键防护**：禁止将 Sequence/Mapping 等复合对象作为键名；检测并拒绝同层级重复键名；脱敏重建后再次校验，拒绝隐藏键碰撞造成的重复键。
6. **别名与共享引用脱敏**：敏感节点在值或映射键位置出现的所有共享引用均处理；敏感键对应的节点以及指向敏感节点的所有别名（或先定义普通 anchor 再在敏感键 alias 引用该节点），均在重构 AST 时替换为 `<已隐藏>` 标量，杜绝共享引用泄漏。
7. **异常信息保护**：YAML 语法解析错误（ScannerError / ParserError）和校验异常在日志中仅打印类名或脱敏摘要，对外抛出的 `ApiError` 统一返回 `config_parse_failed`，绝不将可能带有文件敏感片段的原始异常信息吐给前端。

## 4. 生产部署指引（待用户明确授权）
- 本次改动仅在开发工作区 `hermes-panel-stream` 及隔离测试虚拟环境 `/root/hermes-workspace/yaml-test-venv` 中完成验证，**未安装到系统生产 Python，未重启生产 sidecar，未执行上线部署**。
- 后续如获部署授权：
  1. 必须在运行 `hermes-panel-sidecar.service` 的 Python 解释器环境下执行 `pip install --require-hashes -r sidecar/requirements.txt`；
  2. 确认服务配置指向具有 `yaml` 模块的 Python 解释器；
  3. 执行授权部署发布流程与健康检查验证。
