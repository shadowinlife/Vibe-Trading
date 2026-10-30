---
title: 冷启动耗时：项目作用域的插件安装
description: 箱子从启动到 VT MCP 接入模型工具面要 70-120 秒，根因是项目作用域配置里的 plugin 列表触发一次 63 MB 的现场安装；本文记录度量方法、证据、修复与复核命令。触发词：冷启动、启动慢、领取慢、node_modules、插件安装、project scope、entrypoint、MCP gate。
type: reference
status: active
created: 2026-10-30
updated: 2026-10-30
tags: [feature, boot, opencode, plugin]
---

# 冷启动耗时：项目作用域的插件安装

## 结论

`OpencodeAgent/entrypoint.sh` 把渲染好的 `opencode.json` 复制到项目作用域 `/workspace/.opencode/opencode.json` 时，**必须去掉其中的 `plugin` 列表**。opencode 会为项目作用域单独安装配置里声明的插件（`oh-my-openagent` 及其依赖，约 63 MB），冷启动实测耗时 70-82 秒；插件本身已由全局作用域从镜像里预烘焙的缓存加载，去掉项目侧的声明不影响插件生效。

修复后同一台机器上：冷启动到 `/mcp?directory=/workspace` 显示 `vibe-trading: connected` 从 73-82 秒降到 **8.75 秒**；策略恢复入口 `/health` 就绪从 77-122 秒降到 **40 秒上下**。

## 症状与影响

- 箱内日志顺序固定：`opencode.json rendered` → `VT MCP server OK — 99 tools registered` → `opencode server listening` → `warmup complete` → **长时间静默** → `VT MCP connected` → `starting strategy-run entry`。
- 中间那段静默期间 `/workspace/.opencode/node_modules` 被创建（63 MB、3826 个文件）。
- 控制面（RM）按需领取箱子：每个用户每次领取都要等待这段安装；带策略快照的用户还要求入口就绪后才能发布，于是 `replicas: 0` 下的领取体验是两分钟左右。

## 度量方法（可复现）

1. **看容器日志的时间戳**，不只看顺序：
   `kubectl logs <pod> -c sandbox --timestamps | grep -E "entrypoint\]|strategy-run entry"`
   关注 `warmup complete` 与 `VT MCP connected` 之间的间隔。
2. **对 mtime**：`stat -c "%y %n" /workspace/.opencode/node_modules`——若它的写入时间落在启动窗口内，说明这次启动在装依赖。
3. **直接计时一次冷目录**（不动正在用的目录）：
   ```bash
   mkdir -p /tmp/probe/.opencode && cp /workspace/.opencode/opencode.json /tmp/probe/.opencode/
   time curl -s -o /dev/null -u "opencode:$OPENCODE_SERVER_PASSWORD" \
     "http://127.0.0.1:4096/mcp?directory=/tmp/probe"
   ```
   未预置依赖：73-82 秒；预置 `node_modules`：6.1 秒；去掉 `plugin`：8.75 秒。
4. **对照组**：同一份配置放进新目录、并把 `/workspace/.opencode/node_modules` 复制过去，6.09 秒返回 200——证明差别只在安装，不在 MCP 服务本身（直接 stdio 握手 5.5 秒，与 cwd 无关）。

## 根因

- 全局作用域 `/home/opencode/.opencode/opencode.json` 的插件走镜像预烘焙缓存 `~/.cache/opencode/packages/<spec>/node_modules/<name>`，实例引导约 2 秒。
- 项目作用域 `/workspace/.opencode/opencode.json` 是入口脚本启动时复制过去的一份**完整配置**，同样带 `plugin` 列表。opencode 对每个项目按自己的 `package.json` 安装插件，于是产生独立的一次 npm 安装；`/workspace/.opencode/package.json` 里会写下 `oh-my-openagent` 这一依赖。
- 失败模式是静默的：安装期间不报错，状态接口只是长期显示 MCP 未连接。

## 修复

`finalize_config()` 复制配置到项目作用域后，调用一次 JSON 处理删除 `plugin` 键（保留 `mcp` 块，它才是项目作用域副本存在的理由）。插件仍由全局配置加载——日志里 `[oh-my-openagent] ENTRY - plugin loading {"directory":"/workspace"}` 依旧出现，工具面与代理配置不变。

回归测试：`OpencodeAgent/tests/test_entrypoint_supervision.py::test_project_scope_copy_drops_the_plugin_list`。

## 验收

- 冷启动（新箱、无缓存目录）：首次 `/mcp?directory=/workspace` 到 `vibe-trading: connected` **≤ 15 秒**。
- 策略恢复入口 `/health` 200 **≤ 45 秒**（旧镜像 v3.14.0 也只有 44 秒，修复后应有明显余量）。
- 启动窗口内 `/workspace/.opencode/node_modules` 的 mtime 不变化；断网时同样通过。
- `VT MCP server OK — 99 tools registered` 与 `VT MCP connected` 两条日志保留。

## 边界与回退

- 若将来项目作用域确实需要**与全局不同版本**的插件，不能再简单删键：需要预置该项目的 `node_modules` 或改用镜像层烘焙，否则会回到 70 秒级冷启动。
- 回退方式：删除 `finalize_config()` 里的删键片段，冷启动恢复旧行为；控制面侧把 `ENTRY_READY_BUDGET_S`（RM 仓 `src/resource_manager/strategy_restore.py`）保持在 180 秒即可兜住。
- 相关：RM 的领取等待预算从 45 秒提到 180 秒，是同一问题的控制面兜底，不解决安装本身。
