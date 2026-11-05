# HK clawd 迁移替换调度

用户已明确停止测试、授权替换HK现网Hermes，叫停后又明确恢复迁移。当前只做迁移必要工作，不扩审、不跑测试套件、不清理磁盘、不重复询问替换目标。

父实测：hermes-gateway.service active PID2948333；hermes-panel-sidecar.service active PID2212646；clawd.service/clawd-gateway.service尚不存在；/root/.clawd不存在；磁盘free1982980096 bytes。现网面板current指向/var/www/hermes-panel/releases/20261103-221623-c28f2f8。这是派发前快照，不是迁移完成。

唯一执行owner：deleg_d2a06286 / sa-0-d1f757d5。可写迁移目录/root/hermes-workspace/clawd-production-migration/、本次唯一/opt/clawd/releases发布与/root/.clawd数据副本。系统unit/nginx仅只读核对，并在迁移工作目录生成候选文件及完整apply/rollback入口，不在当前网关会话中切换。

必须实际落地产物与环境、default当前数据一致副本、外部切换/回滚脚本、STATUS.md及真实检查日志。SQLite必须backup API，不直接拷活db/WAL；快照时当前会话仍写入，外部切换前必须停写最终同步。凭据不得进入stdout或报告，备份0700/敏感文件0600。不得修改其它profile。

边界：不读改/usr/local/lib/hermes-agent；不改原面板源码；不删原数据/旧发布/缓存；不伪造acceptance_green、不拆旧切换工具安全门；不使用子代理、systemd-run、cron、MOSS绕过当前会话不能停止自身网关的限制。实际切换需外部SSH或外部服务管理触发，最终给一个准确执行入口。切换前只能报告准备完成，不能称已上线。

待owner返回后父回读产物和运行证据；源代码实现、部署准备、实际现网切换分别记录。旧验收停止，不将未跑或中断测试勾通过。
