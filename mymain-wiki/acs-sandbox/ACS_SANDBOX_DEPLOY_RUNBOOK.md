---
title: ACS Agent Sandbox 从 0 部署 Runbook（opencode + omo 完整落地手册）
description: 把 PoC（2026-09-22 冒烟 + 2026-09-23 T1-T16 全量实证）的整条经验库收敛为一份从零可复放的部署手册：全新机器 + 全新阿里云账号，端到端起出 opencode+omo 沙箱服务。覆盖账号拓扑（ACS 集群与 ClickHouse/liteLLM 跨账号）、集群搭建、adminApiKey/port-forward/SDK venv、共享预热池、最小镜像制作、ACR 拉取路径决策树、专用池与服务拉起、生命周期运维（pause/TTL/kill/成本）、32 项坑速查表（P01-P32 编号/症状签名/根因/修复/证据出处）与六份附录。触发词：ACS、Agent Sandbox、部署 runbook、从 0 部署、MicroVM、E2B、e2b SDK、SandboxSet、SandboxClaim、预热池、专用池、dedicated pool、kruise-agents、patch_e2b、envd、adminApiKey、port-forward、sandbox-manager、insufficient_scope、imagePullSecrets、ACR、跨账号、路由传播、route propagation、pause、resume、beta_pause、persistentContents、TTL、never-timeout、成本、计费、sleep infinity、opencode serve、omo、tier-a、405、403、AccountForbidden、ScalingLimited、FailedPostStartHook、sandbox state is dead、VmRSS、/config、XFF。
type: reference
status: active
created: 2026-09-23
updated: 2026-09-23
tags: [acs, agent-sandbox, deployment, runbook, opencode, omo, e2b, microvm]
related: [ACS_SANDBOX_SMOKE_2026-09-22.md, POC_IMAGE_opencode-sandbox-poc.md, ../INDEX.md, ../AGENTS.md]
---

# ACS Agent Sandbox 从 0 部署 Runbook — opencode + omo

> **定位**：本手册是「opencode + omo 部署在阿里云 ACS Agent Sandbox（MicroVM）」这条路径的**唯一部署正典**。它把 2026-09-22 基础设施冒烟（[ACS_SANDBOX_SMOKE_2026-09-22.md](ACS_SANDBOX_SMOKE_2026-09-22.md)，下称 *smoke 文档*）与 2026-09-23 Stage A PoC（T1-T16，证据在主仓 `.omo/evidence/task-*.json`，下称 *T&#8202;n*）的全部实测知识收敛为一次成型流程。**目标读者**：持全新机器 + 全新阿里云账号、要把它重新搭一遍的工程师。
>
> **诚实边界**：文中每一个延迟/RSS/成本数字都出自主仓 `.omo/evidence/` 的某份实测文件或 smoke 文档，不引入未测值；命令均为 PoC 实际跑过的形态（占位符化）。PoC 的历史叙事、行号级引用以两篇原文为准，本手册只维护「怎么做对 + 别再踩坑」。
>
> ⚠️ **安全红线（公开 fork，不可协商）**：所有基础设施标识一律占位符（`<ACS_CLUSTER_ID>`、`<API_SERVER_IP>`、`<ACR_EE_REGISTRY>`、`<ACR_EE_REGISTRY_VPC>`、`<ACR_NS_OLD>`、`<SANDBOX_DOMAIN>`、`<E2B_ADMIN_API_KEY>`、`<CH_HOST>`），凭证只存本机 `~/.acs-sandbox/`（600 权限），**永不**进入 commit / wiki / evidence / 聊天回显。

## 0. 阅读地图与总验收标准

| 你要做的事 | 直接去 |
|---|---|
| 第一次搭（新账号/新机器） | Phase 0 → 1 → 2 → 3（基础设施全绿）→ 4 → 5 → 6（服务点亮） |
| 已经跑起来了，做运维 | Phase 7（pause/TTL/kill/成本/清理纪律） |
| 报错了来查 | Phase 8 坑速查表（P01-P32），按**症状签名**列检索 |
| 抄命令 | 附录 A（全命令速查）+ 附录 C（两份完整 YAML）+ 附录 D（每 Phase 验证点汇总） |

**总验收（全过 = 部署成功）**：
1. `kubectl get nodes` 返回 3 个 `virtual-kubelet-*` Ready，server 版本 v1.36.1-aliyun.1（T1 rerun 基线）；
2. `curl http://localhost:8888/kruise/api/health` → 200；带 `X-API-Key` 的 `GET /kruise/api/v2/sandboxes` → 200，无 key → 401（鉴权真实生效）；
3. 共享池五检查点冒烟 PASS（smoke 附录 A 脚本）；
4. `opencode-sandbox-poc` 镜像 push 成功且 digest 与本地一致；
5. 专用池 `kubectl get sbs opencode-poc` → `AVAILABLE=2`；claim 后轮询 `GET /kruise/<SID>/4096/agent` ≤180s 内 200，且返回 **17 个 agent**（含 Sisyphus/oracle/librarian/explore 等 omo 签名，T8/T10 黄金标准）；
6. `sandbox-ctl` CLI 完成 create→state→route-wait→engine-get→stop→start→kill 全循环退出码全 0（T15 活体循环）。

---

## Phase 0 — 前提与账号拓扑

**目标**：搞清楚资源在哪个账号、凭证放哪里，再动手。

### 0.1 账号拓扑（关键架构事实，用户确认 2026-09-23，见主仓 notepad decisions.md）

| 资源 | 所在账号 | 说明 |
|---|---|---|
| ACS K8S 集群（sandbox 面） | 账号 A | 本手册 Phase 1-7 的全部对象 |
| ClickHouse / liteLLM | **账号 B（不同账号！）** | 与集群跨账号 |

**三条直接推论**：
1. **跨账号 ACR 免密拉取不成立**。T7c 在集群内 claim 换装私有镜像失败（`insufficient_scope`），追溯根因 = ACS 集群与镜像仓所属账号不同、平台免密集成本身不适用于该私有 EE 实例——同账号才谈免密。**修复路径见 Phase 5 决策树**（专用池 + `imagePullSecrets`）。
2. **沙箱 → ClickHouse/liteLLM 的数据面是跨账号网络问题**（CEN/对等连接/公网+鉴权，或维持 runner 形态由 ECS 侧桥接数据面）。PoC 范围内未解决，**列为后续迭代开放项**（附录 F）。
3. **PoC 的模型流量不受影响**：opencode 调 DashScope 走公网端点（T5 实测 138ms 可达），与账号拓扑正交。

### 0.2 凭证清单与本机布局

| 凭证 | 用途 | 获取方式 | 存放 |
|---|---|---|---|
| kubeconfig | 集群 API（kubectl） | 控制台下载，**必须走文件传**（P26） | `~/kubeconfig.yaml` |
| adminApiKey（= `E2B_API_KEY`） | sandbox-manager E2B API 鉴权 | 自定义生成：`echo "e2b_$(openssl rand -hex 16)"`，格式硬约束 `e2b_[0-9a-f]+`（P22） | `~/.acs-sandbox/admin-api-key`，600 |
| ACR 推送凭证（用户名+固定密码） | docker login / push | 容器镜像服务控制台 → 访问凭证 | `~/.acs-sandbox/acr-credentials`，600（两行：user / password） |
| `DASHSCOPE_API_KEY` | 模型端点鉴权 | DashScope 控制台 | `OpencodeAgent/.env`（PoC 时 len 116、双端点 200 验证，T7b） |
| `OPENCODE_SERVER_PASSWORD` | opencode serve basic auth | 自行生成：`openssl rand -hex 16` | `~/.acs-sandbox/poc-server-password`，600 |

```bash
umask 077 && mkdir -p ~/.acs-sandbox
# 三个文件逐一落盘（值不 echo 到终端）；admin key 生成示例：
echo "e2b_$(openssl rand -hex 16)" > ~/.acs-sandbox/admin-api-key
openssl rand -hex 16 > ~/.acs-sandbox/poc-server-password
```

**铁律**：任何真实值不进 commit/wiki/evidence/聊天回显；诊断输出先脱敏再落盘（PoC 全程 evidence 扫描零命中，T1/T8/T10/T12/T15 各自带 `secret_self_scan` 回执）。`sandbox-ctl` CLI 与未来 router 都按**文件路径**读凭证（`E2B_API_KEY_FILE`、`OPENCODE_SERVER_PASSWORD_FILE`，env 覆盖文件优先），argv/日志永不出现值。

**验证检查点 0**：`ls -l ~/.acs-sandbox/` 三文件齐全且权限 `-rw-------`。

**本 Phase 坑**：P22（admin key 格式）、P26（kubeconfig 粘贴损坏）。

---

## Phase 1 — 集群搭建（从 0）

**目标**：拿到一个带 Agent Sandbox 组件、API 可从本机访问的 ACS 集群。

### 1.1 创建 ACS 集群

控制台路径：阿里云 → 容器服务 → **ACS 容器计算服务** → 创建集群（地域与后续镜像仓库同地域，PoC 实测 cn-hangzhou）。创建完成后集群自带 3 个 `virtual-kubelet-<region>-<zone>` 虚拟节点（ACS serverless，无普通节点）。组件版本要求（smoke §2 官方矩阵）：`acs-virtual-node ≥ v2.17.0`、`ack-agent-sandbox-controller ≥ v0.5.14-release.1`、`ack-sandbox-manager ≥ v0.6.0`（PoC 实测 manager `v0.6.12-release.2`）。详细创建步骤以官方文档为准（smoke 附录 C 链接）。

### 1.2 ⚠️ 商品开通（第一道 403 门）

**组件安装 ≠ 商品开通**——两者是分开的门。先在控制台开通商品码 `acs_sandbox_public_cn`。

- **症状签名**：沙箱 Pod 卡住，事件里 `code: 400/403, AccountForbidden.AuthorizationRequired`、`Your account commodity is not activated`（smoke §6.2）。
- **修复**：开通后**存量 Pod 不会自动重试**（virtual-kubelet 不重试该错误）——必须 `kubectl delete sbx <name>` 删 Sandbox CR 触发全新 provisioning。

### 1.3 ⚠️ API Server 公网白名单（最容易反复踩的门）

kubectl 走公网 6443 时，API Server 的 SLB **按出口 IP 白名单**放行。

- **症状签名**：`dial tcp <API_SERVER_IP>:6443: i/o timeout`（SYN 无响应）；`nc -z -G 10 <API_SERVER_IP> 6443` 不可达。
- **诊断链**（T1 实测流程，三步定死根因）：
  1. 通用外网对照：`curl -s -o /dev/null -m 8 -w '%{http_code}' https://api.github.com` 返回任意 HTTP 码 → 本机联网健康，排除断网；
  2. 双 kubeconfig 指纹比对：对两份文件的 server 字段取 sha256 一致 → 排除文件分歧/损坏；
  3. 结论 = **SLB 白名单未放行当前出口 IP**（P14）。
- **修复**：控制台 → 集群信息 → API server 公网白名单，加回当前出口 IPv4。家庭/移动网络出口 IP 会变，**此坑会复发**；每次连不上先跑三步诊断链，不要先怀疑别的。备选：同 VPC 跳板/ECS 上执行 kubectl。

### 1.4 kubeconfig 获取与验证

控制台下载 kubeconfig 存 `~/kubeconfig.yaml`。**传递只走文件**（IM/对话粘贴会损坏 base64 私钥，P26；诊断法 `openssl rsa -in <key> -check` 报 `d e not congruent to 1`）。

```bash
export KUBECONFIG=~/kubeconfig.yaml
kubectl version --client          # PoC 基线 v1.30.2
kubectl get nodes -o wide         # 期望 3 × virtual-kubelet Ready
```

### 1.5 组件核对

```bash
kubectl get pods -n sandbox-system     # sandbox-gateway ×3 Ready(1/1) + sandbox-manager ×3 Ready(2/2 容器)，0 重启
kubectl get svc  -n sandbox-system     # sandbox-manager 暴露 7788(沙箱流量)/8080/9002
kubectl get crd | grep kruise          # 12 个 CRD（sandboxes/sandboxsets/sandboxclaims/...）
```

**验证检查点 1**：3 虚拟节点 Ready + gateway 3/3 + manager 3/3 + 12 CRD + svc 端口齐全（T1 rerun ②④ 同款输出）。

**本 Phase 坑**：P21（商品开通 403 + 存量 Pod 不重试）、P14（白名单出口 IP 漂移）、P26（kubeconfig 损坏）、P23（别试 `kubectl delete pod`，webhook 拦）、P25（反复删建触发启动预算耗尽）。

---

## Phase 2 — 沙箱基础设施初始化

**目标**：本机拿到一条可用的 E2B 私有协议链路（admin key + port-forward + 钉版 SDK venv），跑通五检查点冒烟。

### 2.1 adminApiKey

生成（Phase 0.2）并写入组件配置：sandbox-manager 的 adminApiKey 在组件配置（控制台/`ack-sandbox-manager` 配置项）设置。格式违规的后果是 **SDK 客户端本地直接拒发请求**（E2B SDK ≥2.25 客户端校验 `e2b_[0-9a-f]+`，P22）。key 权限大（可管理其他 key），正式化时按官方多租户文档另发业务 key。

### 2.2 port-forward（免 sudo 高端口模式）

官方方式三示例用 `sudo ... 80:7788`；**读 `kruise_agents/patch_e2b.py` 源码确认私有协议 URL 是纯字符串拼接**（`http://{E2B_DOMAIN}/kruise/api`），`E2B_DOMAIN` 可带端口 → 高端口免 root（smoke §3/§6.6）：

```bash
nohup kubectl port-forward svc/sandbox-manager 8888:7788 -n sandbox-system > "$TMPDIR/acs-smoke/pf.log" 2>&1 &
disown
lsof -nP -i :8888        # 必须看到 kubectl 进程 TCP 127.0.0.1:8888 (LISTEN) 才算建立
curl -s http://localhost:8888/kruise/api/health   # → 200
```

- **pf 脆弱性（P24）**：进程随会话结束静默死亡；还有一种失败模式是 **kubectl 进程存活但不建 LISTEN socket**（卡在 API server 建连）。**检测永远用 `lsof`，不是进程存在性**（T1 learnings）。排障顺序：lsof → `pf.log` → API server 可达性（回 1.3 诊断链）。
- port-forward 建连成功后才会打印 `Forwarding from ...`，日志为空 = 还没连上，不是坏了。

### 2.3 SDK venv（版本硬锁）

```bash
mkdir -p "$TMPDIR/opencode/acs-smoke" && cd "$TMPDIR/opencode/acs-smoke"
python3 -m venv venv                    # 本机 python ≥3.9（PoC 实测 3.12.8）
./venv/bin/pip install "e2b>=2.35.0,<2.38.0" "e2b-code-interpreter>=2.9.0,<2.10.0"
./venv/bin/pip install "git+https://github.com/openkruise/agents.git@10db25ac3ecb5cd40380f0d2738d2aa51fb78369#subdirectory=sdk/customized_e2b"
```

- **P01 版本锁是硬的**：`e2b 2.51.0` 的 `Sandbox.create` 走 `POST /v2/sandboxes`，manager v0.6.x 未实现该路由 → **405 Method Not Allowed**；而 `GET /v2/sandboxes`（list）存在、完全正常——极具迷惑性。锁 `>=2.35,<2.38` 是 kruise-agents 0.1.0 元数据声明的测试范围，实测 2.37.1 通过（smoke §6.1）。
- **kruise-agents 钉 commit**：PoC 稳定环境钉 `10db25ac`（T15 依赖钉版），不要装未 pin 的 HEAD。
- 版本自查（**P31**：e2b 2.37 顶层无 `__version__` 属性，属正常非损坏）：

```bash
./venv/bin/python -c "from importlib.metadata import version; \
print('e2b=='+version('e2b'), '| ci=='+version('e2b-code-interpreter'), '| ka=='+version('kruise-agents'))"
```

### 2.4 patch_e2b 纪律（两次踩过的 import 坑）

任何访问沙箱的 Python 入口脚本头部必须是：

```python
from kruise_agents.patch_e2b import patch_e2b   # ① 导入「函数」，不是模块
patch_e2b(https=False)                          # ② 在任何 e2b/e2b_code_interpreter import 之前调用

from e2b_code_interpreter import Sandbox        # ③ patch 生效后才有 import
```

- **P02 模块陷阱**：`from kruise_agents import patch_e2b` 拿到的是**同名子模块**，`patch_e2b(...)` 调用直接 `TypeError`（或误当模块用则 patch 静默未生效）。唯一正确形态是 `from kruise_agents.patch_e2b import patch_e2b`（smoke 附录 A 脚本 / T15 用 AST+运行时 spy 双保险测试钉死）。
- **顺序要求**：patch 必须在第一个 e2b 模块加载前执行（T15 包内 `test_bootstrap_order.py` 把这条钉成回归门禁）。

### 2.5 五检查点冒烟

用 smoke 文档附录 A 的 `smoke.py`（create/run_code/files/commands/kill 五检查点）：

```bash
export E2B_DOMAIN=localhost:8888
export E2B_API_KEY="$(cat ~/.acs-sandbox/admin-api-key)"   # 不回显值
"$TMPDIR/opencode/acs-smoke/venv/bin/python" smoke.py
# 期望：SMOKE PASSED: 5/5 checkpoints (create latency 0.2s)   ← 池命中时，smoke §4 实测
```

**验证检查点 2**：health 200 + 带 key 200/无 key 401 对照 + 五检查点 5/5 PASS。

**本 Phase 坑**：P01、P02、P22、P24、P31。

---

## Phase 3 — 预热池（共享 code-interpreter）

**目标**：拉起 K8s 原生预热池，理解 claim/补足/规格约束，给镜像实验一个公共池底。

### 3.1 SandboxSet YAML

完整 YAML 见 smoke 附录 B（此处不重复；附录 C-1 是占位符化版本）。要点：
- 标签三件套是 MicroVM 算力的开关：`alibabacloud.com/acs: "true"` + `compute-class: agent-sandbox` + `compute-qos: default`；
- `runtimes: [agent-runtime]` 注入 envd（**不要**注入 `csi`——NAS/OSS 动态挂载需工单解除特权容器限制，冒烟不需要）；
- 镜像地域随集群（`registry-cn-<region>-vpc.ack.aliyuncs.com/acs/code-interpreter:v1.6`）；
- `replicas: 2` 起步控成本。

```bash
kubectl apply -f sandboxset.yaml
kubectl get sbs -n default        # 等 AVAILABLE=2（含镜像拉取+envd 初始化，商品开通后实测 ~90s）
kubectl get sbx -n default        # STATUS=Running, CLAIMED=false ×2
```

### 3.2 池行为（实测数字）

| 行为 | 数值 | 出处 |
|---|---|---|
| 池命中 claim | 0.2s | smoke §5 |
| 池无货冷建 | ~58-90s（58.0/58.2s 两独立样本；877MB 镜像首拉占 41-53s） | T2 trial2 / T4 E1 / smoke |
| kill/claim 后自动补足 | 28-40s（T8 p50=28.0 max=39.5；smoke ~40s；新池首建 39s） | T8 stats / smoke §5 |
| SandboxClaim（K8s 原生路径） | ~16s Completed，自动触发补池 | smoke §4 Phase4 |
| `create-on-no-stock` | 默认 true——无货即冷建，5 并发 claim 会看到冷建，属预期 | 源码侦察 extensions.go:67,163 |

### 3.3 规格与 claim-time 升配约束（T4 定案）

- **claim metadata 可以带规格**（`e2b.agents.kruise.io/cpu-request|cpu-limit|memory-request|memory-limit`）；
- **warm 原地升配被 ACS 平台拒绝（P09）**：池模板 Pod 缺 annotation `scaling.alibabacloud.com/enable-inplace-resource-resize: "true"` 时，命中存量热 Pod 的升配 claim 失败。症状签名 = pod 事件 `FailedUpdateInstance`（`Code: InvalidParameter ... operation of in-place resource resizing has been denied`，HTTP 400）→ claim 回滚删箱。**失败签名不是** kruise 的 UnsupportedResize 条件。
- **冷建直达目标规格可用（定案路线）**：`create-on-no-stock` 路径的实例按目标规格出生——实测 2C4G：nproc=2、MemTotal 4GiB、cgroup limit 4294967296、**Guaranteed QoS 不变**（requests==limits 保持）、1.2GiB 压力 10s 零 OOM、restartCount=0。
- **只升不降（P10）**：`memory-request=512Mi`（低于池 1Gi）在 API 校验层 0.2s 直接 400 `must not be lower than the current value`，不建箱不扣费。
- 改共享池模板（加 annotation）= **禁区**（多任务共享，D5⑥），要 annotation 就建自己的专用池（Phase 6）。

**验证检查点 3**：`AVAILABLE=2`；claim 后池自动回补到 2；`kubectl get events` 无 ScalingLimited。

**本 Phase 坑**：P09、P10、P21（若跳到本步才发现商品未开）、P23、P25、P19（池 hit 原地换镜像后 run_code 死，见 Phase 8）。

---

## Phase 4 — 镜像制作（opencode + omo 最小镜像）

**目标**：产出并推送 `opencode-sandbox-poc:v0.1`（1.39GB，零密钥）。完整血统叙事见 [POC_IMAGE_opencode-sandbox-poc.md](POC_IMAGE_opencode-sandbox-poc.md)；本节讲**为什么每层长这样**。构建上下文 = 仓库 `OpencodeAgent/`，Dockerfile 为 `OpencodeAgent/Dockerfile.sandbox-poc`（唯一权威源，不在此重复全文）。

### 4.1 逐层设计依据

| 层 | 内容 | 依据（不可改动点） |
|---|---|---|
| base | `node:20-bookworm-slim`（Debian 12，自带 `/bin/bash` + coreutils `cp/mv/mkdir/sleep`） | envd 注入的 postStart hook `bash /mnt/envd/envd-run.sh` 需要 bash+coreutils（T2）；未压缩 base ~200MB |
| opencode | `npm install -g opencode-ai@1.18.30`（钉版） | **只在构建期放文件、绝不执行 opencode 干实事**——历史教训：生产全栈 Dockerfile 在 arm64 宿主 `--platform=linux/amd64` 模拟下执行该二进制 SIGILL（P30）。本包 postinstall 只做硬链 + benign `--version` 探针，实测通过（T7） |
| omo 两级策略 | tier-a：构建期 `npm install -g oh-my-openagent@4.19.4` 成功则写 `/opt/omo-tier=tier-a-build-time`；失败降级 tier-b（运行时 opencode 首启自装） | **tier-a 可行因为 npm 只落文件不执行**（T7 实测成功）。tier-b 冷装有 1Gi OOM 与前文自删坑（P27/P28），能做就别靠它 |
| 烘焙配置 | `/opt/opencode-config/opencode.json`：model `alibaba-cn/qwen3.8-max` + provider `alibaba-cn` → `options.baseURL = https://dashscope.aliyuncs.com/compatible-mode/v1`（DashScope OpenAI 兼容**公网**端点）+ 模型声明（attachment/reasoning/tool_call + limit 1M context/128k output）+ `plugin: ["oh-my-openagent@4.19.4"]` | **零 apiKey 零密钥**——auth 全运行时注入（Phase 6 secretRef）。插件钉两种 tier 下都保留：它是 tier-b 机制、tier-a 下无害 |
| 工作目录 | `install -d -m 0777 /workspace` + `WORKDIR /workspace` | envd hook 创建的 uid-1000 用户（实测 commands 默认 uid 1001 `user`）要可写 |
| CMD | `ENTRYPOINT ["sleep","infinity"]`（回退 `["tail","-f","/dev/null"]`） | **长驻前台进程硬性要求（P05）**：ubuntu/bash 类秒退镜像 → 容器退出 → `FailedPostStartHook` + `Back-off` → sandbox 状态 dead → claim 失败（T2 trials 4/5 尸检）。`opencode serve` 不在这里起——Phase 6 由专用池模板 command 起 |

### 4.2 envd 注入架构（为什么镜像**不能**自带 envd）

agent-runtime 以 init sidecar（`acs/agent-runtime:v0.0.15`）把 envd 与 `envd-run.sh` 放进 emptyDir `/mnt/envd`，容器生命周期 `postStart` 执行该脚本：**在用户容器内**创建 uid-1000 用户（失败有直接改 `/etc/passwd` 的回退，非致命）后 `nohup $ENVD_DIR/envd -isnotfc &`。结论（T2 trial6 用 nginx 实证、T3 源码核对）：**自定义镜像不需要也不应该打包 envd**；镜像的硬性要求只有三条——bash+coreutils、PATH 无冲突、**长驻 CMD**。

### 4.3 体积裁剪手法（1.58GB → 1.39GB）

`opencode-ai` 的 optionalDependencies 落**两个 177MB x86_64 平台二进制**（现代 + baseline 变体），postinstall 把选定者**硬链**到 `bin/opencode.exe`（launcher `/usr/local/bin/opencode` 的唯一读取点，运行时不再读嵌套包）。因此安装后：

```dockerfile
RUN npm install -g opencode-ai@1.18.30 \
    && test -x /usr/local/lib/node_modules/opencode-ai/bin/opencode.exe \
    && rm -rf /usr/local/lib/node_modules/opencode-ai/node_modules \
    && /usr/local/bin/opencode --version        # 层内复验：硬链 inode 存活、二进制完整
```

不复验就不要删——层内 `--version` 通过是裁剪安全性的唯一证据（T7 build #2）。

### 4.4 构建与推送

```bash
colima start                          # ~22s 起 daemon（colima v0.8.1 / docker 27.4.0 实测）
docker build --platform=linux/amd64 \
  -f OpencodeAgent/Dockerfile.sandbox-poc \
  -t opencode-sandbox-poc:v0.1 OpencodeAgent/
# 镜像内验证（--platform 强制 amd64 跑，模拟有局限：--version 级验证，真 serve 验证在 Phase 6 沙箱原生 x86_64）
docker run --rm --platform=linux/amd64 --entrypoint sh opencode-sandbox-poc:v0.1 -c \
  'cat /opt/omo-tier; which opencode bash cp mv mkdir sleep; opencode --version'

read -r -s ACR_PW   # 不 echo
printf '%s' "$ACR_PW" | docker login <ACR_EE_REGISTRY> --username "$ACR_USER" --password-stdin
docker tag opencode-sandbox-poc:v0.1 <ACR_EE_REGISTRY>/test/opencode:v0.1
docker push <ACR_EE_REGISTRY>/test/opencode:v0.1     # 实测 118s / 1.39GB（T7c）
docker images --digests | grep opencode-sandbox-poc  # 与 push 回执 digest 比对
```

基准 digest：`sha256:79bd36fc802ba2e991a472d742271326f301a1110db28a277b5efaf87480ed4a`（内容不变则跨仓一致，T7/T8/T7b 三处比对均以此校验）。构建完 `colima stop` 并双回执确认（`colima status` + `docker info` 都报错才算停）。

**验证检查点 4**：`/opt/omo-tier` = `tier-a-build-time`；未压缩 ≤1.5GB；push digest == 本地 digest；镜像内无任何密钥字段。

**本 Phase 坑**：P05、P30、P27（tier-b 才踩得到）、P28、P13（登录凭证被拒时先看 P13 判别法）。

---

## Phase 5 — ACR 与拉取路径（跨账号现实）

**目标**：选定集群侧拉镜像的可行路径。**决策树**（全部 T6/T7b/T7c/T8 实证）：

| # | 仓库形态 × 池形态 | 结果 | 依据 |
|---|---|---|---|
| 1 | 同账号 ACR **公共**仓库 × 任意池 | ✅ 匿名拉取可用 | T2 trial6 公网镜像；T6 池 ACS VPC 公共镜像匿名拉（877MB/53s） |
| 2 | **私有**仓库 × **专用池 + 模板 imagePullSecrets** | ✅ **选定路线** | T8 一次点亮（39s AVAILABLE=2） |
| 3 | 私有仓库 × 共享池 claim-time 换镜像 | ❌ 不可行：无凭证注入通道——claim metadata 只携带 image+resources（源码证明，T3 recon：env/secret 键不存在），修改共享 SandboxSet = 禁区 | T6 E3 `insufficient_scope` + 池 pod 零 imagePullSecrets/零 helper |
| 4 | **跨账号**免密（credentialless）拉取 | ❌ 不成立：`insufficient_scope` 授权拒绝（网络通、registry 有活回复；根因 = 集群与镜像仓不同账号） | T7c 决定性实验 |

- **PoC 期间还用过选项 1 的变体**（T6 裁决「仓库临时公开」，auth 全运行时注入所以镜像零秘密公开无害）；**生产路线 = 选项 2**。跨账号拓扑（Phase 0.1）意味着**选项 4 永远不可用**，别在免密上浪费时间。
- **P13 ACR 个人版鉴权怪癖**：带 scope 的 token 请求对**有效凭证与垃圾凭证一视同仁**都签发 `sub` 为空的匿名 token——**不能**用「带 scope 能拿到 token」判断凭证有效。**正确判别**：无 scope + Basic 头的 token 端点 ping，401 = 凭证无效、200 = 有效（匿名在该端点反而 200）。固定密码可在控制台「访问凭证→设置固定密码」重置（T7b 教训：不同实例的凭证不通用，ADB Spark customImage 那套凭证 ≠ docker registry 登录凭证）。
- **注册表可见性探测配方**（匿名 token-flow，无需 docker）：

```bash
# ① 取挑战 → ② 匿名 scoped token → ③ 带 Bearer GET manifest
curl -sI https://<ACR_EE_REGISTRY>/v2/test/opencode/manifests/v0.1        # 401 + WWW-Authenticate(realm/service)
TOKEN=*** -s "https://<realm>?service=<service>&scope=repository:test/opencode:pull" | jq -r .token)
curl -s -o /dev/null -w '%{http_code}' \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Accept: application/vnd.docker.distribution.manifest.v2+json' \
  https://<ACR_EE_REGISTRY>/v2/test/opencode/manifests/v0.1               # 200=公开可匿名拉 / 401=私有
```

- **集群内引用镜像必须用 VPC 端点 host**（`<ACR_EE_REGISTRY_VPC>`，同一份内容，仅 host 不同；VPC DNS 只在集群内存在）（T7b §6 交接注记）。

**验证检查点 5**：可见性探测返回预期码；docker login 成功（或 P13 判别确认凭证有效）。

---

## Phase 6 — 专用池与服务拉起（opencode serve as PID1）

**目标**：建 Secrets → 应用专用 SandboxSet → 池就绪 → claim → 路由传播 → 服务与 omo 全绿。这是 D10 裁决的主形态：**模板 command 起 serve（PID1），模板 env+secretRef 注凭证，模板 imagePullSecrets 拉私有镜像**——三个 blocker（F3 env 注入 / F4 拉取凭证 / F5 规格）在模板层一并消解（T8）。

### 6.1 Secrets（default ns）

**拉取凭证（dockerconfigjson，必须同时含公网与 VPC 两个 host 的 auths）**：

```bash
umask 077
ACR_USER=$(sed -n 1p ~/.acs-sandbox/acr-credentials); ACR_PW=$(sed -n 2p ~/.acs-sandbox/acr-credentials)
AUTH=$(printf '%s' "$ACR_USER:$ACR_PW" | base64)
cat > /tmp/opencode-poc-pull.json <<EOF
{"auths":{"<ACR_EE_REGISTRY":{"auth":"${AUTH}"},"<ACR_EE_REGISTRY_VPC>":{"auth":"${AUTH}"}}}
EOF
kubectl create secret generic opencode-poc-acr-pull \
  --type=kubernetes.io/dockerconfigjson \
  --from-file=.dockerconfigjson=/tmp/opencode-poc-pull.json \
  --dry-run=client -o yaml | kubectl apply -f -
rm -f /tmp/opencode-poc-pull.json          # 用完即删；值全程不经终端
```

**运行时环境（Opaque）**：`DASHSCOPE_API_KEY`（OpencodeAgent/.env）+ `OPENCODE_SERVER_PASSWORD`（`~/.acs-sandbox/poc-server-password`），同样用 stdin apply：

```bash
set +o histexpand 2>/dev/null || true      # 防值进 shell history（bash）
DK=$(grep -m1 '^DASHSCOPE_API_KEY=' OpencodeAgent/.env | cut -d= -f2-)
SP=$(cat ~/.acs-sandbox/poc-server-password)
cat <<EOF | kubectl apply -f -
apiVersion: v1
kind: Secret
metadata: {name: opencode-poc-env, namespace: default}
type: Opaque
stringData:
  DASHSCOPE_API_KEY: "${DK}"
  OPENCODE_SERVER_PASSWORD: "${SP}"
EOF
unset DK SP
```

### 6.2 专用 SandboxSet（工作 YAML，占位符化；全文在附录 C-2）

结构要点（与主仓 `.omo/evidence/task-8-dedicated-pool.json` 及现网 `kubectl get sandboxset opencode-poc -o yaml` 逐项一致）：
- `replicas: 2`；`runtimes: [agent-runtime]`；**`persistentContents: [filesystem]`**（pause 语义的前提，Phase 7）；
- 标签三件套同共享池（acs/compute-class=agent-sandbox/compute-qos=default）；
- `resources` requests==limits `cpu 2 / memory 4Gi`（Guaranteed）+ `requests.ephemeral-storage: 30Gi`；RSS 实测稳态 ~316-370MB，2C4G 余量巨大，门上裁决 T19 后可降 1C2Gi 省半（Phase 7.5）；
- `image: <ACR_EE_REGISTRY_VPC>/test/opencode:v0.1` + `imagePullSecrets: [opencode-poc-acr-pull]`；
- `env` 两枚 `secretKeyRef`（6.1）——**PID1 可见、envd 命令 shell 不可见**（P07 双面），serve 的 basic auth 用 `OPENCODE_SERVER_PASSWORD`（opencode serve 自动启用 HTTP basic auth，用户名字面 `opencode`）；
- **PRIMARY command（服务形态核心）**：

```yaml
command: ["/bin/bash","-c",
  "mkdir -p $HOME/.config/opencode && cp /opt/opencode-config/opencode.json $HOME/.config/opencode/opencode.json && exec opencode serve --hostname 0.0.0.0 --port 4096"]
```

  先把烘焙参考配置复制到 serve 生效位置，再 `exec` 让 serve 成为 PID1（T8 实证 `/proc/1 cmdline = opencode serve ...`，6 沙箱零重启）。

```bash
kubectl apply -f opencode-poc-sandboxset.yaml
kubectl get sbs opencode-poc -n default       # ~39s 后 AVAILABLE=2（镜像层 ECI 预热时）
```

### 6.3 claim 与路由传播（本手册最重要的一节运维事实）

claim 走 CLI（推荐，附录 A 有全例子）或裸 SDK：

```bash
~/.acs-sandbox/sandbox-ctl-venv/bin/e2b-sandbox-ctl create --template opencode-poc
# {"sandbox_id": "default--opencode-poc-xxxxx", "claim_s": 0.19}   热命中 claim p50=0.151s / p95=0.251s
```

**⚠️ 路由传播地板（P15）**：claim 成功 ≠ HTTP 可用。`/kruise/<SID>/4096` 的路由要**概率性等待 65-98s 典型带**才出首个 200（全样本分布 {0.2, 65.4, 65.8, 67.0, 69.3, 71.4, 71.6, 72.4, 73.0, 74.2, 83.6, 97.5}，另有 T15 一次 120.3s、T16 一次 177.4s——**上界不止 98s，180s 预算是底线不是保险箱**）。serve 从容器出生就在跑，这是纯网关路由面延迟。
- **纪律：poll，别 sleep**。每 8-10s 探一次、预算 ≥180s，容忍窗口期 404/503/连接失败（附录 A route-wait / 附录 D）。
- **通道不对称**：SDK `commands.run`（envd 通道）在池命中 claim 后立即可用（T2/T3/T8-fallback 多个 0.2-0.3s 即用样本）；但 T16 观察到同一 claim 后 envd 请求也可能路由未齐而失败（engine-get 提前调用 exit 5）——**防御姿势 = 对任何首调用都套轮询**。
- **resume 路径不受地板影响**：pause→connect 后路由 0.13-0.17s 即 200、无重传播（同 pod IP，见 Phase 7）。**Stage B 用 pause/resume 做唤醒可完全绕开地板**（D11 裁决的生命周期依据）。

### 6.4 就绪 + omo 生效验证配方

```bash
CTL=~/.acs-sandbox/sandbox-ctl-venv/bin/e2b-sandbox-ctl
SID=$(...)                                # 6.3 得到的 sandbox_id
$CTL route-wait "$SID" --budget 180       # {"ready": true, "route_ready_s": ..., "attempts": ...}

# 凭据经 600 文件传给 curl，绝不进 argv（密码曾进 traceback 的事故教训，T8）：
umask 077; RC=/tmp/sbx-curlrc; printf 'header = "Authorization: Basic %s"\n' \
  "$(printf '%s:%s' opencode "$(cat ~/.acs-sandbox/poc-server-password)" | base64)" > $RC
curl -s --config $RC "http://localhost:8888/kruise/${SID}/4096/agent"        # 200
curl -s --config $RC "http://localhost:8888/kruise/${SID}/4096/session"      # 200（桥兼容端点）
curl -s --config $RC "http://localhost:8888/kruise/${SID}/4096/experimental/tool/ids"
rm -f $RC
```

- **黄金标准（T8/T10）**：`GET /agent` 返回 **17 个 agent**——omo 10 个（Sisyphus-ultraworker / Hephaestus / Prometheus / Atlas / Metis / Momus / Sisyphus-Junior / librarian / multimodal-looker / oracle）+ 核心 7 个（build/compaction/explore/general/plan/summary/title），对照本地 `opencode serve --pure` 基线 7 个，**严格超集判定**；tool-ids 含 `call_omo_agent` 等 omo 专属工具。
- **P16**：`GET /config` 会**回显解析后的 apiKey 明文**——该端点禁止出现在任何证据采集/日志管道里。
- **auth 语义经网关全透传**：无凭据 401 / 正确 200 / 错误密码 401，POST 写路径 200/204（T9 矩阵）。

**验证检查点 6**：AVAILABLE=2 → claim <0.3s → route-wait ≤180s 内 ready → `/agent` 17 roster → 一次桥式轮次（POST /session 200 → prompt_async 204 → GET /event 首帧 <0.5s → message 200）。

**本 Phase 坑**：P05/P07/P12/P15/P16/P17（容器内没 curl——curl 在你本机跑；箱内探测用 bash `/dev/tcp` + `node -e` + `base64`，池镜像无 python3/curl，T10 三件套）、P04（裸 SDK create 记得 `request_timeout`）、P29（pf/API 瞬断时先重试收敛再下结论）。

---

## Phase 7 — 生命周期运维

### 7.1 pause / resume（原地计算检查点——实测推翻文档）

**配方**（D11 主形态）：`beta_pause(keep_memory=False)` + SandboxSet 声明 `persistentContents: [filesystem]`。

- **实测语义 = 原地计算检查点（P-T11 头条）**：pod 对象不变（AGE 连续、RESTARTS 0）、microVM boot epoch 不变、**PID1 serve 进程存活**、pod IP 保留 → **resume 无需路由重传播**：connect 后 0.13-0.17s 即 200。总唤醒 **0.75-0.86s**（pause API 1.30-2.44s 在冻结侧，connect 0.49-0.56s、envd 0.05-0.13s、route 0.13-0.17s 在唤醒侧）。
- **官方文档说 pause=休眠/内存状态保留/唤醒 1-10s、有「pod 删除重建」表述——以实测为准**（3 循环一致 + CR conditions `SandboxResumed/ResumePod` 控制面佐证）。注意保留条件：该结论成立于 agent-runtime 池形态；若池形态变成真重建 pod，会重新落入 0.2-100s 网关带（T11 caveat）。
- **轮次进行中 pause 可存活**：prompt+1.8s 时暂停、30s 停留、resume 后该轮**干净完成**（finish=stop、文本 PONG、无 error/abort，completed 时间戳落在唤醒瞬间）——engine-bridge f8 分支 1 的挂死担忧在此形态未出现。
- **休眠计费**：paused 态不收 CPU/内存费，仅临时存储计费且**无免费额度**（官方计费说明；单价文档未明示，PoC 用 0.0021 CNY/GiB·h 代理估算并标风险——见 7.5 与 T13 开放项）。paused 保留时长默认 forever（`reserve-paused-sandbox-duration`）。
- **CLI 映射**：`stop` = beta_pause（幂等：已暂停返回 `already_paused:true` exit 0）；`start` = connect+route 轮询，**单步**（relaunch 分支观测与假设两种形态下均不可达，防御性保留 no-op，T11/T15）。

### 7.2 TTL 语义

- `create(timeout=…)`：到点**平台自动释放**（T10 实证 `timeout=1800` 到期释放：`get_info → SandboxNotFound`、`kill → False`）。**但 T8-fallback 曾在某构建上观测到 900s TTL 超时后仍存活 ~40min（lesson L6）——不要依赖到期即释放，主动 `set_timeout` 管理**。
- `set_timeout(N)`：**仅当延长才生效**（only-if-longer）；`connect()` 会顺带续期。PoC 交接标准动作 = `set_timeout 3600`。
- **清理铁律：任何沙箱禁止配 `never-timeout`**（metadata 键存在但 PoC 全程零使用并有审计回执，T13）；实验结束 `kill` 自己的箱并验证 `kubectl get sbx` 中 `claimed=true` 残留 = 0。

### 7.3 kill 语义（丢失面，T12 实证）

| 数据类 | kill 后 | 证据 |
|---|---|---|
| 引擎 session store（`/root/.local/share/opencode`，serve PID1 私有） | **全丢**——新 claim `GET /session` = `200 []`、旧会话双 404 NotFoundError | API 面权威证明 |
| `/workspace/.omo/run-continuation/ses_*.json` 续跑文件 | 丢 | manifest diff |
| 镜像烘焙层（omo tier-a `/usr/local/lib/node_modules` + `/opt/opencode-config`，17549 文件） | **hash 级存活**（BCKD_HASH 前后一致） | manifest diff |
| secretRef 凭证 | 随模板自动重放（新 pod 认证照常 200） | claim2 复验 |
| 池 | 自动补位（p50 28s；CR 硬删 5.6s；对比 TTL 到期路径 CR 在 shutdown 态滞留 ~9min） | T12/T10 |
| 租户隔离彩蛋 | envd 命令用户 uid 1001，`/root` 700 不可读——沙箱内代理代码读不到引擎 store（正面安全属性） | T11/T12 |

### 7.4 auto-pause 策略（门已批准的建议形态，D11 ⓑ）

**空闲 → pause；按需唤醒**：活跃会话 keep-alive 用 `set_timeout` 阶梯续期；空闲超阈值 `stop`（pause）；下一条消息 `start`（~0.8s 唤醒，绕开路由地板）。kill 仅限退役/重建（接受引擎 store 丢失由桥 ECS 侧转录 + f8 分支 3 兜底）。

### 7.5 成本表（T13，单价 = 官方文档 2026-09-23 查询）

**费率**：agent-sandbox（default QoS）中国内地 ¥0.078/vCPU·h + ¥0.039/GiB·h，按秒计量、失败创建不计费；运行态临时存储 30GiB 内免费。

| 情景 | 月成本（720h） |
|---|---|
| (i) 现形态 2×2C4G 常驻 | ¥449.28 |
| (ii) 2×1C2Gi 常驻（RSS 335-386MiB 支撑，门上裁决可降） | ¥224.64 |
| (iii) 1×2C4G + pause 主导（10% 活跃） | ~¥45.5（pause 存储底线 ¥45.36 为**估算**，待首张账单） |
| (iii′) 同上前 50% 活跃 | ~¥135 |
| (iv) 对照：现生产 ECS 4C8G 全栈目录价 | ¥216-391 |
| (ref) 共享池 2×1C1Gi | ¥168.48 |
| （参考）ACR EE 基础版目录价 | ¥780/月（用户实例，沉没） |

### 7.6 彻底清理

```bash
kubectl delete sandboxclaim <name> -n default        # 若用过 K8s 原生 claim
kubectl delete sbx <claimed-sbx> -n default          # 只能删 sbx CR（P23）
kubectl delete sandboxset code-interpreter opencode-poc -n default    # 停池停止计费
kubectl delete secret opencode-poc-acr-pull opencode-poc-env -n default
# 本地：kill port-forward；凭证轮换义务——PoC 结束轮换 adminApiKey；poc-server-password 若曾入
# traceback 必须在正式化前轮换（轮换需重建池 env，T8 注记）。
```

**验证检查点 7**：一轮 pause→resume 唤醒 <1s 且 `/agent` 仍 17；kill 后 `AVAILABLE` 回 2；`kubectl get sbx -n default` 无 `claimed=true` 残留。

---

## Phase 8 — 坑大全（编号速查表，P01-P32）

> 按症状签名检索（共 32 项，超过任务最低 20 项要求）。每行：编号 / 症状签名 / 根因 / 修复 / 首次踩坑证据。「Phase X 坑」小节内联引用此处。

| # | 症状签名 | 根因 | 修复 | 首次证据 |
|---|---|---|---|---|
| P01 | `POST /v2/sandboxes` → **405**（GET 同路径正常） | e2b SDK ≥2.5x 换了 create 路由，manager v0.6 未实现 | 硬锁 `e2b>=2.35,<2.38` + `e2b-code-interpreter>=2.9,<2.10`（实测 2.37.1+2.9.0） | smoke §6.1 |
| P02 | patch 未生效/`TypeError: 'module' object is not callable` | `from kruise_agents import patch_e2b` 导入的是同名**模块** | 只写 `from kruise_agents.patch_e2b import patch_e2b`，且先于一切 e2b import | smoke 附录 A；T15 测试钉死 |
| P03 | create 直接 `400 Template or Checkpoint not found` | SDK 默认 `template="base"`，ACS 池无此模板 | 永远显式 `template="code-interpreter"` / `"opencode-poc"` | T6 E3 |
| P04 | `httpx.ReadTimeout` / 500 `context canceled`，但服务端 claim 实际成功 | SDK 默认 HTTP read timeout **60s < 冷建/换镜像等待**（三次独立踩中） | `request_timeout > wait-ready-timeout + 30s`；客户端超时后先 `Sandbox.connect(sid)` 补救再判断 | T2 trial2 / T3 E3 / T4 E1 |
| P05 | claim 永挂 → `sandbox state is dead`；pod 事件 `FailedPostStartHook` + `Back-off`（镜像明明拉得动、`already present on machine`） | CMD 非长驻（ubuntu/bash 秒退）→ 容器在 postStart hook 完成前退出 | 镜像 ENTRYPOINT 长驻前台（`sleep infinity`）+ 带 bash/coreutils | T2 trials 4/5 尸检 |
| P06 | 前台命令超时/取消后，它 nohup/setsid 起的守护进程**全被杀**（重定向也救不了） | 客户端流超时 → envd 杀整棵命令 cgroup | 长驻进程一律 `commands.run(cmd, background=True)` | T8-fallback L1 |
| P07 | claim `envVars` 里设了 key，PID1 服务读不到 | envd 在命令 shell exec **后**做 shell 级 export：对 `commands.run` 及子进程可见、**对 PID1 永不可见**（root 实测 count=0）；反向：模板 secretRef env 对 PID1 可见、对 envd 命令 shell 不可见 | fail-closed 的 PID1 入口 = 专用池 template env/envFrom；claim envVars 只配 envd 启动的服务 | T3 E1/E2；T12/T15 |
| P08 | crashloop 期想 `connect` 进去手动救 → `SandboxNotFoundException`；commands/files 全不可达 | claim 在 Ready 前失败 → manager 拒绝路由并删箱；且外来镜像容器内根本没有 envd 面（面随 agent-runtime 注入到 Ready 的容器） | 放弃 crashloop 救援路线；P05 从源头保证不 crashloop | T3 E3 |
| P09 | warm 命中 + 升配 metadata 的 claim：pod 事件 `FailedUpdateInstance`（`InvalidParameter ... enable-inplace-resource-resize`）→ claim 回滚删箱 | 共享池模板 Pod 出生时缺 ACS 原地升配 annotation | ① 冷建直达目标规格（create-on-no-stock 路径实证可用）② 专用池模板自带 annotation。失败签名不是 kruise UnsupportedResize 条件 | T4 E2 |
| P10 | claim `400: target memory ... must not be lower than the current value` | 原地 resize 只升不降（QoS class 也不得变） | 规划时一次到位；降规格 = 建新池 | T4 E4 |
| P11 | 集群拉私有镜像 `insufficient_scope: authorization failed`（网络通、registry 有活回复、ErrImagePull→ImagePullBackOff） | **跨账号**私有仓 + 无任何凭证注入到池 pod（免密不成立） | Phase 5 决策树 → 专用池 + imagePullSecrets（或同账号公共仓） | T7c（T6 同型于同账号私有仓） |
| P12 | 想给共享池加 pull secret 无处下手：claim metadata 只有 image+resources 两族键；`automountServiceAccountToken:false` 跟 imagePullSecrets 无关 | manager 无凭证注入通道（源码 grep 证明） | 别改共享池；建专用池 | T6 E1/E4 + T3 recon |
| P13 | `docker login` 被拒但「token 端点能拿到 token」；带 scope 一切看似正常 | ACR 个人版 auth：带 scope 请求对垃圾凭证也签发空 sub 匿名 token；**无 scope + Basic** 才校验身份（401=无效/200=有效） | 用无-scope Basic ping 判别；固定密码在控制台重置；凭证按实例隔离不复用 | T7b 对照矩阵 |
| P14 | `dial tcp <API_SERVER_IP>:6443: i/o timeout`（SYN 无响应），昨日还能连 | API server 公网 SLB 白名单按出口 IP；家庭/移动网络 IP 会漂 | 三步诊断链（外网对照+双 kubeconfig 指纹）→ 控制台加白当前出口 IP；复发即此坑 | T1 |
| P15 | fresh claim 后 `/kruise/<SID>/<port>/` 持续 404/503/连接失败，serve 明明在跑 | kruise 网关路由注册概率性延迟：典型 65-98s、观测全域 0.2-177.4s、与 pod 年龄无关 | **poll 不 sleep**，预算 ≥180s；唤醒走 pause/resume（0.13-0.17s 无重传播）；envd 通道多数即时但 T16 见其同受路由门制——首调用一律带轮询 | T8/T9/T10/T13；T15 120.3s；T16 177.4s |
| P16 | 诊断时顺手 GET `/config`，响应体含明文 apiKey | serve 的 /config 回显**解析后的** provider apiKey | 证据采集/日志管道**禁调该端点**；已捕获的立即脱敏 | T8-fallback L5 |
| P17 | 箱内 `ps/free/curl/python3` 全部 `command not found`（exit 127） | 最小镜像不含 procps/curl/python3（CI 池镜像有 python3、opencode-poc 无） | RSS 用 `grep VmRSS /proc/<pid>/status`；HTTP 探测用 `node -e`；TCP 用 bash `/dev/tcp`；编码 `base64` | T4 E1（free 缺）/ T8 / T10 / T12 |
| P18 | ① `timeout=900` 的箱超时 40min 仍活着 ② 到 1800s 又被平台释放 ③ bash 工具调用被后台子进程持有 stdout 拖到超时 | TTL 释放在不同构建上表现不一致；SSE 类 `Popen` 继承 stdout 管道即挂住工具 | 把 TTL 当「不保证到点即释放」用，显式 `set_timeout`/kill 管理；后台进程 stdio 全 `DEVNULL`/重定向到文件；**禁止 never-timeout** | T8-fallback L6；T10 claim1；T13 审计 |
| P19 | pool-hit 原地换镜像后 `run_code` → 503 `delayed connect error: Connection refused`，换回同镜像也不恢复 | swap 重启容器杀掉了原镜像内置 jupyter 服务，无人重拉 | 只在冷建 CI 镜像上用 run_code；PoC 面（commands/files）无影响 | T2 trials 1/3 |
| P20 | 失败的 claim 留下一堆 dead 沙箱？——其实不用你管 | manager 对 wait-ready 失败自动删箱（日志 `the failed sandbox will be deleted` → `sandbox deleted`） | 别 panic 清理；正常收尾仍要 kill 自己的成功 claim | T2 trials 4/5 / T6 / T7c |
| P21 | Pod 事件 `AccountForbidden.AuthorizationRequired / commodity is not activated`（403） | Agent Sandbox **商品**未开通（与组件安装是两回事） | 开通 `acs_sandbox_public_cn`；**存量 Pod 不自动重试** → `kubectl delete sbx <name>` 重建 | smoke §6.2 |
| P22 | SDK 报 key 格式错，请求根本没发出 | E2B SDK ≥2.25 客户端强校验 `e2b_[0-9a-f]+` | `echo "e2b_$(openssl rand -hex 16)"` 生成；组件配置同步为该值 | smoke §6.4 |
| P23 | `kubectl delete pod` 被拒：admission webhook `v-pod-delete.kb.io` denies | 池管理的 Pod 由 Sandbox CR 拥有 | 删对象走 CR：`kubectl delete sbx <name>`，SandboxSet 自动重建 | smoke §6.3 |
| P24 | port-forward「进程在但连不上」/ 会话后静默死亡 | pf 随会话结束；或 kubectl 卡在 API 建连从未 listen | 检测用 `lsof -nP -i :8888` 找 LISTEN，而不是 `ps` 看进程；失败回 P14 诊断链 | T1 |
| P25 | 反复删建后创建被限速，SandboxSet status 条件 `ScalingLimited: startup budget is exhausted` | 启动预算机制 | 避免无意义删建循环；排障先 `kubectl describe pod` / `get events --sort-by=.lastTimestamp` | smoke §6.7 |
| P26 | `tls: failed to parse private key`（openssl 校验 `d e not congruent to 1`） | kubeconfig 经 IM/对话粘贴时 base64 被改坏 | 落盘走文件传；`openssl rsa -check` 定位 | smoke §6.5 |
| P27 | 在 `~/.cache/opencode/packages/` 里 npm install omo 中途目录消失（`ENOENT package-lock.json`） | oh-my-openagent postinstall **自删** `oh-my-*` 缓存目录（invalidate 插件缓存），装进缓存区=自毁 | 构建到临时目录（如 `/opt/omo-build`，16s 热/91s 冷）再 `mv` 进缓存布局 | T8-fallback |
| P28 | 1Gi 沙箱里首装 opencode+omo：serve 静默死亡、留下半坏缓存被 opencode 误判已装 | bootstrap 峰值 >1Gi memcg，kernel OOM kill（客户端只见 connection-refused）；半包 dist 无 package.json | 预置插件缓存或 tier-a 烘焙镜像；瞬时需求 ≥1.5Gi；1Gi 池只配稳态 ~370MB | T8-fallback L4 |
| P29 | 调用 CLI 得到 BackendUnavailable（exit 3），服务端动作其实已完成 | 本机→API server/pf 瞬断（`bad file descriptor`、隧道掉 60s~1h 均有实录）；CLI 超时被杀报 UNKNOWN 而非假成功 | **重试收敛**：幂等再查 state（如 stop 幂等 `already_paused` exit 0）；kubectl 类命令对瞬断重试 ≤3 | T16 FINDING-2 |
| P30 | 构建期执行 opencode 二进制 SIGILL（arm64 宿主跨平台模拟） | QEMU/Rosetta 对该 Go 二进制的模拟缺陷（生产全栈 Dockerfile 历史注记） | 构建期 npm 只放文件；唯一允许的「执行」是 benign `--version` 探针；真验证放到原生 x86_64 沙箱里 | T7 §1 设计输入 |
| P31 | `import e2b; e2b.__version__` → AttributeError，疑似 venv 损坏 | e2b 2.37.x 顶层不导出 `__version__`（正常现象） | 版本自查用 `importlib.metadata.version("e2b")` | T1 |
| P32 | 网关后面的应用以为客户端可信：收到伪造的 `x-forwarded-for` / 任意 `Host` | kruise 网关**不清洗**入站 x-forwarded-*、Host 原样透传、peer 恒为网关 pod IP、还注入 `x-envoy-original-dst-host` | XFF 覆盖/剥离必须发生在**最外层自己的 nginx 边缘**；serve 层 peer-IP 白名单在此路径无意义 | T9 header 探针 |

---

## 附录 A — 完整命令速查

```bash
# ===== 基础设施一次性准备 =====
export KUBECONFIG=~/kubeconfig.yaml
umask 077 && mkdir -p ~/.acs-sandbox
echo "e2b_$(openssl rand -hex 16)" > ~/.acs-sandbox/admin-api-key
openssl rand -hex 16 > ~/.acs-sandbox/poc-server-password
# acr-credentials: 两行（username / password），值不进终端

# ===== venv（SDK 直连用）=====
mkdir -p "$TMPDIR/opencode/acs-smoke" && cd "$TMPDIR/opencode/acs-smoke"
python3 -m venv venv
./venv/bin/pip install "e2b>=2.35.0,<2.38.0" "e2b-code-interpreter>=2.9.0,<2.10.0"
./venv/bin/pip install "git+https://github.com/openkruise/agents.git@10db25ac3ecb5cd40380f0d2738d2aa51fb78369#subdirectory=sdk/customized_e2b"

# ===== sandbox-ctl 稳定 venv（CLI，别用 /tmp 易失 venv）=====
python3 -m venv ~/.acs-sandbox/sandbox-ctl-venv
~/.acs-sandbox/sandbox-ctl-venv/bin/pip install -e <repo>/OpencodeAgent/deploy/sandbox-ctl
# 验证： ~/.acs-sandbox/sandbox-ctl-venv/bin/e2b-sandbox-ctl --help  → exit 0

# ===== port-forward 起 / 停 / 检 =====
nohup kubectl port-forward svc/sandbox-manager 8888:7788 -n sandbox-system > "$TMPDIR/pf.log" 2>&1 & disown
lsof -nP -i :8888                                  # 必须有 LISTEN
curl -s http://localhost:8888/kruise/api/health    # 200
# 停： kill <pid>（仅当你要停；共享基础设施上他人 pf 禁杀）

# ===== 池管理 =====
kubectl apply -f sandboxset.yaml                   # 附录 C
kubectl get sbs -n default;  kubectl get sbx -n default
kubectl get events -n default --sort-by=.lastTimestamp | tail -20

# ===== sandbox-ctl 全子命令例子（stdout 恒为单个严格 JSON；退出码 0/2/3/4/5/6）=====
CTL=~/.acs-sandbox/sandbox-ctl-venv/bin/e2b-sandbox-ctl
$CTL create --template opencode-poc --timeout 1800 --request-timeout 320   # {"sandbox_id":...,"claim_s":...}
SID=default--opencode-poc-xxxxx
$CTL state "$SID"                                    # {"state":"running|paused|absent",...}
$CTL route-wait "$SID" --budget 180                  # {"ready":true,"route_ready_s":...,"attempts":...}
$CTL engine-get "$SID" /session                      # {"status":200,"body":"[]","password_path":...}
$CTL stop "$SID"                                     # beta_pause；幂等
$CTL start "$SID" --ttl 1800                         # connect+poll 单步唤醒（~1.3s）
$CTL kill "$SID"                                     # {"killed":true}
# 退出码契约： 0 ok / 2 usage / 3 BackendUnavailable / 4 sandbox absent / 5 op failed / 6 auth

# ===== 裸 SDK claim 骨架（P02 顺序 + P03 模板 + P04 超时，三坑一次防住）=====
python - <<'PY'
from kruise_agents.patch_e2b import patch_e2b
patch_e2b(https=False)
from e2b_code_interpreter import Sandbox            # noqa: E402
import os
sbx = Sandbox.create(template="opencode-poc", timeout=1800, request_timeout=320)
print(sbx.sandbox_id)
PY

# ===== curl basic auth（密码只经 600 rc 文件）=====
umask 077; RC=/tmp/sbx-curlrc
printf 'header = "Authorization: Basic %s"\n' \
  "$(printf '%s:%s' opencode "$(cat ~/.acs-sandbox/poc-server-password)" | base64)" > "$RC"
curl -s --config "$RC" "http://localhost:8888/kruise/${SID}/4096/agent"
rm -f "$RC"
```

## 附录 B — 凭证文件布局与权限

```text
~/.acs-sandbox/                     # 目录与文件全部 700/600，仓库外
├── admin-api-key                   # e2b_<32hex>；env 可覆盖（E2B_API_KEY）
├── acr-credentials                 # 2 行：username / password（个人版/企业版凭证不通用，P13）
├── poc-server-password             # opencode serve basic-auth 密码（用户名恒为 opencode）
└── sandbox-ctl-venv/               # CLI 专用 venv（稳定路径，勿放 /tmp）
~/kubeconfig.yaml                   # 文件传递（P26）
<repo>/OpencodeAgent/.env           # DASHSCOPE_API_KEY 等应用侧密钥（.gitignore 管辖）
```

规则：值永不 echo/进 argv/进日志/进 evidence；诊断命令用 `--config`/`--from-file`/stdin 管道；`curlrc` 类临时文件用后即删（PoC 全部有删除回执，`/tmp/t8_curlrc` 例外挂账待 T23 清除）。

## 附录 C — SandboxSet 完整 YAML（两份，占位符化）

### C-1 共享预热池（smoke 附录 B 原样）

```yaml
apiVersion: agents.kruise.io/v1alpha1
kind: SandboxSet
metadata:
  name: code-interpreter
  namespace: default
spec:
  runtimes:
  - name: agent-runtime              # 注入 envd；csi 需工单，冒烟/ PoC 都不加
  replicas: 2
  template:
    metadata:
      labels:
        alibabacloud.com/acs: "true"
        alibabacloud.com/compute-class: agent-sandbox
        alibabacloud.com/compute-qos: default
    spec:
      automountServiceAccountToken: false
      containers:
      - image: registry-cn-hangzhou-vpc.ack.aliyuncs.com/acs/code-interpreter:v1.6
        imagePullPolicy: IfNotPresent
        name: sandbox
        resources:
          limits: {cpu: "1", memory: 1Gi}
          requests: {cpu: "1", memory: 1Gi, ephemeral-storage: 30Gi}
      terminationGracePeriodSeconds: 30
```

### C-2 专用池（现网 `opencode-poc` 抓取并脱敏；registry host → `<ACR_EE_REGISTRY_VPC>`）

```yaml
apiVersion: agents.kruise.io/v1alpha1
kind: SandboxSet
metadata:
  name: opencode-poc
  namespace: default
spec:
  persistentContents:
  - filesystem                        # pause 检查点前提（Phase 7.1）
  replicas: 2
  runtimes:
  - name: agent-runtime
  template:
    metadata:
      labels:
        alibabacloud.com/acs: "true"
        alibabacloud.com/compute-class: agent-sandbox
        alibabacloud.com/compute-qos: default
    spec:
      automountServiceAccountToken: false
      containers:
      - name: sandbox
        image: <ACR_EE_REGISTRY_VPC>/test/opencode:v0.1
        imagePullPolicy: IfNotPresent
        command:
        - /bin/bash
        - -c
        - mkdir -p $HOME/.config/opencode && cp /opt/opencode-config/opencode.json
          $HOME/.config/opencode/opencode.json && exec opencode serve --hostname 0.0.0.0
          --port 4096
        env:
        - name: DASHSCOPE_API_KEY
          valueFrom:
            secretKeyRef: {name: opencode-poc-env, key: DASHSCOPE_API_KEY}
        - name: OPENCODE_SERVER_PASSWORD
          valueFrom:
            secretKeyRef: {name: opencode-poc-env, key: OPENCODE_SERVER_PASSWORD}
        resources:
          limits: {cpu: "2", memory: 4Gi}
          requests: {cpu: "2", memory: 4Gi, ephemeral-storage: 30Gi}
      imagePullSecrets:
      - name: opencode-poc-acr-pull
      terminationGracePeriodSeconds: 30
  updateStrategy:
    maxUnavailable: 20%
```

> 抓取自 AVAILABLE=2 的健康态现网对象（2026-09-23），`status`/`resourceVersion`/`uid` 等运行时字段已剔除；K8s 原生 claim 路径的 SandboxClaim YAML 见 smoke 附录 B。

## 附录 D — 验证命令清单（每 Phase checkpoint 汇总）

| Phase | 命令 → 期望 |
|---|---|
| 0 | `ls -l ~/.acs-sandbox/` → 三文件（admin-api-key / acr-credentials / poc-server-password）600 |
| 1 | `kubectl get nodes` → 3×virtual-kubelet Ready；`get pods -n sandbox-system` → gw 3/3 + mgr 3/3；`get crd \| grep -c kruise` → 12 |
| 2 | `lsof -nP -i :8888` → LISTEN；`curl .../kruise/api/health` → 200；带 key `GET /v2/sandboxes` → 200，无 key → 401；`smoke.py` → `SMOKE PASSED: 5/5` |
| 3 | `kubectl get sbs` → AVAILABLE=2；claim→kill→观察 28-40s 回补 |
| 4 | 容器内 `cat /opt/omo-tier` → tier-a；`opencode --version` → 1.18.30；`docker push` 回执 digest == 本地；密钥 grep → 0 |
| 5 | 匿名 manifest GET → 200（公共）或 401（私有，走 C-2 pull secret）；`docker login` exit 0 |
| 6 | sbs AVAILABLE=2（~39s）；claim <0.3s；`route-wait` ≤180s ready；`/agent` 200 且 17 agents；tool-ids 含 `call_omo_agent`；桥式一轮 PONG 全 200/204 |
| 7 | `stop`→`state` paused→`start` <2s running→`engine-get` 200；`kill`→`state` absent + 池 28-40s 回补；收尾 `get sbx` claimed=true 计数 0 |
| CLI | `$CTL --help` exit 0；unit `pytest -q` 69 passed；活体全循环退出码全 0 |

## 附录 E — 实测数字总表（全部引自 smoke / T2-T16 evidence，2026-09-22/23 两日）

| 类别 | 指标 | 值 | 出处 |
|---|---|---|---|
| 延迟 | claim（热池命中） | p50 **0.151s** / p95 0.251s / range 0.056-0.263（n=6） | T8 |
| 延迟 | claim（同镜像 swap / 异镜像 nginx / 冷建） | 0.2-0.3s / 35.7s / 58.0-58.2s | T2/T4 |
| 延迟 | 池补足 | p50 28.0s / max 39.5s；首池 39s；smoke ~40s | T8/smoke |
| 延迟 | SandboxClaim 分配 | ~16s | smoke |
| 延迟 | fresh-claim 路由地板 | 典型 65-98s（11+ 样本）；极值 0.2s(×1)/120.3s/177.4s → 预算 180s | T8/T9/T10/T11/T12/T15/T16 |
| 延迟 | resume 路由 | 0.13-0.17s（无重传播，n=3+） | T11 |
| 延迟 | pause→wake 总链 | **0.75-0.86s**（segment: pause 1.30-2.44 / connect 0.49-0.56 / envd 0.05-0.13 / route 0.13-0.17） | T11 |
| 延迟 | 首消息（prompt→首个 SSE 事件） | p50 **0.183s** / p95 0.191s / max 0.192（冷轮 0.025s） | T10 |
| 延迟 | 完整轮次 | p50 2.278s / p95 5.736s / cold max 6.84s | T10 |
| 延迟 | SSE 心跳 | cadence 10.0s、最坏间隙 10.11s、零缓冲（TTFB 0.127s，1470s 窗口 148 帧） | T10 |
| 延迟 | serve→/session 200（热缓存） | 14s；omo 安装 16s 热/91s 冷；npm 装 opencode 10s | T8-fb |
| 对照 | 本地 docker 全栈 wake | 19.7-40.9s（Rosetta 注记） | T10 |
| 资源 | RSS 稳态 | 专用池 316MiB(serve)+19MiB(envd)；1Gi 匹配栈 370MB（avail 490MB）；1Gi 冷装峰值 >1Gi → OOM | T8/T8-fb |
| 资源 | 冷建 2C4G 校验 | nproc 2 / MemTotal 4Gi / cgroup 4294967296 / Guaranteed / 1.2Gi 压测 0 OOM | T4 |
| egress | DashScope 404 138ms / 腾讯行情 200 145ms / aliyun 200 92.5ms / npm registry 200 | T5/T8-fb |
| 镜像 | 1.39GB（1.58→裁剪）；digest 79bd36fc…；push 118s | T7/T7c |
| 计费 | ¥0.078/vCPU·h + ¥0.039/GiB·h；休眠免 CPU/内存、存储无免费额度；月成本情景 ¥45.5-449.28；ECS 对照 ¥216-391；ACR EE ¥780 | T13 |
| 基线 | 集群 v1.36.1-aliyun.1 ×3 vnode；manager v0.6.12-release.2；agent-runtime v0.0.15；CRD×12；svc 7788/8080/9002 | smoke/T1/T6 |

## 附录 F — 与生产集成的关系

- **Stage B 形态 = (a) runner（D11 门裁决，2026-09-23）**：gateway + 桥存储留在 ECS，`opencode serve` 进沙箱，桥经 `OPENCODE_BASE_URL → 沙箱稳定代理 URL`（sandbox ID 跨 pause 不变支撑 1:1 绑定）访问；生命周期 **pause-only**（0.8s 唤醒绕开路由地板，kill 仅限退役且由 f8 分支 3 兜底）；空闲 auto-pause；生产代码侧零依赖入 router——SDK 全数住在 `sandbox-ctl` shell-out 载体（D7），`E2bSandboxBackend` 已落地（T16，退出码→BackendUnavailable 契约见其包 README）。
- **开放项（后续迭代讨论）**：① 跨账号数据面——沙箱（账号 A）访问 ClickHouse/liteLLM（账号 B）需要 CEN/对等/公网+鉴权或维持 ECS 桥接形态（Phase 0.1）；② 路由地板的 VPC-direct 化能否整体绕开 `/kruise` HTTP 面（T10 开放问题）；③ paused 存储单价待首张账单核实（T13 风险标注）；④ 凭证轮换义务收口（adminApiKey、poc-server-password）与 T23 清理清单（smoke §7、T13 residue register、STATE.md 挂账）。
- 本手册与生产部署正典（wiki 外 `OpencodeAgent/docs/DEPLOYMENT-PROD-ENGINE-BRIDGE.md`）的关系：沙箱路径是候选替代/补充形态，ECS 生产路径不变；两侧互不引用密钥。

---

**维护约定**：本文件是 acs-sandbox 知识域的**部署正典**（新机器照此执行）；smoke 文档与 PoC 镜像卡降级为历史叙事与血统证据，内容不改。PoC/Stage B 每次新坑合入 Phase 8（编号只增不改），数字变化同步附录 E 并保留出处。
