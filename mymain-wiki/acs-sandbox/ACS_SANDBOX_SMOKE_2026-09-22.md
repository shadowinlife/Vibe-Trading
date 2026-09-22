---
title: ACS Agent Sandbox 冒烟验证记录（opencode 动态拉起后端·新部署路径起点）
description: 2026-09-22 阿里云 ACS Agent Sandbox（MicroVM）作为 opencode 动态拉起后端的全链路实测记录：材料清单、组件就绪核对、SandboxSet 预热池、port-forward + E2B 私有协议接入、五检查点冒烟、自动补足与 SandboxClaim 验证、性能指标（预热池命中 create 0.2s / MicroVM 冷启动 ~90s / 池补足 ~40s / claim 分配 ~16s）、七项踩坑（e2b SDK 2.51 与 manager v0.6 的 405 版本坑、商品未开通 403 AccountForbidden、webhook 拦截删 Pod、adminApiKey 格式、kubeconfig 粘贴损坏、免 sudo 高端口方案、SandboxSet 启动预算）、opencode 集成路径与资源清理指引。触发词：ACS、Agent Sandbox、MicroVM、E2B、e2b SDK、SandboxSet、SandboxClaim、预热池、kruise-agents、patch_e2b、私有协议、port-forward、sandbox-manager、code-interpreter、envd、冒烟、smoke、opencode 后端、部署路径、405、AccountForbidden、adminApiKey、商品开通、acs_sandbox_public_cn。
type: reference
status: active
created: 2026-09-22
updated: 2026-09-22
tags: [acs, agent-sandbox, microvm, e2b, deployment, smoke-test, opencode]
related: [../INDEX.md, ../engine-bridge/README.md, ../features/f7-opencode-agent.md, ../features/f8-engine-bridge.md]
---

# ACS Agent Sandbox 冒烟验证记录（2026-09-22）

> **定位**：验证「阿里云 ACS Agent Sandbox（MicroVM 安全沙箱）作为 opencode 动态拉起后端」这条**新部署路径**的可行性。本文是后续该路径全部测试的起点与 runbook 基线。与现有生产部署（ECS 裸部署，见 wiki 外权威 `OpencodeAgent/docs/DEPLOYMENT-PROD-ENGINE-BRIDGE.md`）的关系：候选替代/补充形态，尚未选型。
>
> ⚠️ 本仓库是公开 fork：文中基础设施标识一律占位符（`<ACS_CLUSTER_ID>`、`<E2B_ADMIN_API_KEY>` 等），真实值只存本机，严禁回填。

## 1. 结论

**冒烟通过（5/5 检查点 + 3 条获取路径全验证）**。E2B SDK → sandbox-manager → MicroVM 沙箱链路可用，预热池命中时 `Sandbox.create` 仅 **0.2s**，满足 opencode 交互式动态拉起后端的延迟要求。

| 验证项 | 结果 |
|---|---|
| E2B SDK 五检查点（create / run_code / files / commands / kill） | ✅ 全 PASS |
| 预热池自动补足（kill 后 AVAILABLE 回填） | ✅ ~40s 回填至 2 |
| SandboxClaim K8s 原生分配路径 | ✅ ~16s Completed，CLAIMED=true，自动触发补池 |
| API Key 鉴权（`X-API-Key`） | ✅ 200 |
| 私有协议 + port-forward（免域名/证书/ALB） | ✅ 全链路通 |

## 2. 材料清单（人工准备，复测时对照）

| 材料 | 占位符 | 说明 |
|---|---|---|
| 阿里云账号（已实名） | `<ACCOUNT_UID>` | 集群所属账号 |
| AK / SK | — | 控制台/CLI 用；本次实测本机 aliyun CLI 各 profile 均属其他账号，未用上 |
| **Agent Sandbox 商品开通** | 商品码 `acs_sandbox_public_cn` | ⚠️ 与组件安装是两回事，见踩坑 #2 |
| ACS 集群 | `<ACS_CLUSTER_ID>`，API server `<ACS_API_SERVER_IP>:6443` | 实测集群 cn-hangzhou，v1.36.1-aliyun.1，3 虚拟节点 |
| kubeconfig | 本机 `~/kubeconfig.yaml` | cluster-admin 客户端证书；⚠️ 传递必须走文件，见踩坑 #5 |
| adminApiKey（= E2B_API_KEY） | `<E2B_ADMIN_API_KEY>` | 自定义生成：`echo "e2b_$(openssl rand -hex 16)"`；格式硬约束见踩坑 #4 |
| 组件 domain 参数 | `<SANDBOX_DOMAIN>` | ack-sandbox-manager 配置项；port-forward 方案下客户端无需与其一致（见 §3） |
| 本地 Python ≥3.9 + kubectl | — | 实测 Python 3.12 venv、kubectl v1.30.2 |

组件版本要求（官方矩阵）：`acs-virtual-node` ≥ v2.17.0；`ack-agent-sandbox-controller` ≥ v0.5.14-release.1；`ack-sandbox-manager` ≥ v0.6.0；Kube Scheduler 按集群版本对照官方文档。

## 3. 接入拓扑（本次实测采用的最短路径）

官方三种接入方式中选了**方式三（端口转发 + 私有协议）**做冒烟：跳过域名、DNS、TLS 证书、ALB Ingress 全部依赖。

```
本地 Python (e2b SDK + kruise-agents patch_e2b)
  │  http://localhost:8888/kruise/api        ← E2B_DOMAIN=localhost:8888（带端口可行，见踩坑 #6）
  ▼
kubectl port-forward 8888:7788 -n sandbox-system   ← 高端口免 sudo
  ▼
svc/sandbox-manager :7788（sandbox 流量端口）
  ▼
sandbox-manager（E2B API + 鉴权） ──► SandboxSet 预热池（MicroVM Pod，compute-class=agent-sandbox）
```

关键源码依据（`kruise_agents/patch_e2b.py`，60 行）：私有协议 URL 是纯字符串拼接 `http://{E2B_DOMAIN}/kruise/api` 与 `{domain}/kruise/{sandbox_id}/{port}`，因此：
- `E2B_DOMAIN` 可带端口（`localhost:8888`），无需绑定 80 → **免 sudo**；
- 客户端 domain 无需与组件 `domain` 参数一致（请求直达 svc，不经 Ingress 的 host 路由）——官方文档"必须一致"的要求仅适用于走 Ingress 的方式一/二。

## 4. 验证步骤回放（runbook）

工作目录：本机临时目录 `$TMPDIR/opencode/acs-smoke/`（**仓库外**，含 venv、smoke.py、YAML；kubeconfig 在 `~/kubeconfig.yaml`）。

### Phase 0 — 就绪核对

```bash
export KUBECONFIG=~/kubeconfig.yaml
kubectl get nodes -o wide                     # 3 虚拟节点 Ready；region 标签确定镜像地域
kubectl get pods -n sandbox-system            # sandbox-gateway 3/3、sandbox-manager 3/3 Running
kubectl get svc -n sandbox-system             # sandbox-manager: 7788(流量)/8080/9002
kubectl get crd | grep kruise                 # 12 个 CRD（sandboxes/sandboxsets/sandboxclaims/...）
```

### Phase 1 — 预热池

应用附录 B 的 SandboxSet（要点：镜像地域改 `cn-hangzhou`；`replicas: 2` 控成本；runtimes 只注入 `agent-runtime`，**不注入 `csi`**——csi 需工单解除特权容器限制，冒烟不需要）。

```bash
kubectl apply -f sandboxset.yaml
kubectl get sbs -n default        # 等 AVAILABLE=2
kubectl get sbx -n default        # STATUS=Running, CLAIMED=false
```

实测：商品开通后从删旧重建到 `AVAILABLE=2` 约 **90s**（含镜像拉取 + envd 初始化；Pod 先 `PodInitializing` 1/2 → 2/2 Running）。

### Phase 2 — 客户端链路

```bash
python3 -m venv venv
./venv/bin/pip install "e2b>=2.35.0,<2.38.0" "e2b-code-interpreter>=2.9.0,<2.10.0"   # ⚠️ 版本必须锁，见踩坑 #1
./venv/bin/pip install "git+https://github.com/openkruise/agents.git#subdirectory=sdk/customized_e2b"

# 后台端口转发（免 sudo 高端口）
kubectl port-forward svc/sandbox-manager 8888:7788 -n sandbox-system &

# 探活 + 鉴权验证
curl -s http://localhost:8888/kruise/api/health                                  # → 200
curl -s -H "X-API-Key: $E2B_API_KEY" http://localhost:8888/kruise/api/v2/sandboxes  # → 200 []
```

### Phase 3 — 五检查点冒烟

```bash
export E2B_DOMAIN=localhost:8888
export E2B_API_KEY=<E2B_ADMIN_API_KEY>
./venv/bin/python smoke.py     # 源码见附录 A
```

实测输出（2026-09-22）：

```
[1-create]   PASS: sandbox_id=default--code-interpreter-cp2ms, 耗时 0.2s
[2-run_code] PASS: stdout='hello, world\n'
[3-files]    PASS: read back='acs agent sandbox'
[4-commands] PASS: stdout='acs agent sandboxx86_64\n/usr/bin/bash\n' exit_code=0
[5-kill]     PASS: kill returned True
SMOKE PASSED: 5/5 checkpoints (create latency 0.2s)
```

### Phase 4 — 补足与 Claim 路径

```bash
# kill 后观察回填：AVAILABLE 1→2 约 40s，新沙箱自动创建
kubectl get sbs code-interpreter -n default -w

# SandboxClaim（附录 B）：~16s PHASE=Completed，目标沙箱 CLAIMED=true，同时自动触发补池
kubectl apply -f sandboxclaim.yaml
kubectl get sandboxclaim -n default
kubectl get sbx -n default -l agents.kruise.io/claim-name=smoke-claim
```

### Phase 5 — 清理测试产物（保留预热池供后续测试）

```bash
kubectl delete sandboxclaim smoke-claim -n default
kubectl delete sbx <被claim的沙箱名> -n default    # ⚠️ 只能删 sbx CR，不能删 Pod，见踩坑 #3
```

## 5. 性能指标（opencode 后端选型依据）

| 指标 | 实测值 | 备注 |
|---|---|---|
| **预热池命中 create** | **0.2s** | opencode 动态拉起的核心指标 |
| MicroVM 冷启动（含镜像拉取） | ~90s | 首次无缓存；镜像缓存（Image Cache）可加速，见官方 annotation `ops.alibabacloud.com/update-with-image-cache` |
| kill/claim 后池补足 | ~40s | 期间池内仍有其余实例可分配 |
| SandboxClaim 分配 | ~16s | K8s 原生路径，适合非 SDK 集成 |
| 沙箱内环境 | x86_64，bash 可用 | code-interpreter:v1.6 镜像 |

## 6. 踩坑记录（七项，复测前必读）

1. **e2b SDK 版本坑（本次最大坑）**：`e2b 2.51.0` 的 `Sandbox.create` 走 `POST /v2/sandboxes`，sandbox-manager v0.6.x 未实现该路由 → **405 Method Not Allowed**（而 `GET /v2/sandboxes` 存在，list 正常，极具迷惑性）。**修复**：锁 kruise-agents 0.1.0 元数据声明的测试范围 `e2b>=2.35,<2.38` + `e2b-code-interpreter>=2.9,<2.10`（实测 2.37.1 + 2.9.0 通过）。
2. **组件装好 ≠ 商品开通**：沙箱 Pod 卡 `AccountForbidden.AuthorizationRequired`，事件明示 `code: 403, Your account commodity is not activated`。开通 `acs_sandbox_public_cn` 后**存量 Pod 不会自动重试**（virtual-kubelet 不重试该错误），必须删 Sandbox CR 触发全新 provisioning。
3. **删 Pod 被 webhook 拦截**：`v-pod-delete.kb.io` 拒绝 `kubectl delete pod`（"corresponding sandbox exists"）。正确姿势：`kubectl delete sbx <name>`，SandboxSet 控制器自动重建。
4. **adminApiKey 格式硬约束**：E2B SDK ≥2.25 客户端侧校验 `e2b_[0-9a-f]+`，不合规的 key（如任意单词）请求根本发不出去。生成：`echo "e2b_$(openssl rand -hex 16)"`。admin key 权限大（可管理其他 key），业务侧应按官方多租户文档另发普通 key。
5. **kubeconfig 走 IM/对话粘贴会损坏私钥**：base64 中途被改 → `tls: failed to parse private key`（openssl 校验 `d e not congruent to 1`）。诊断法：`openssl rsa -in <key> -check`。传递必须落盘走文件路径。
6. **免 sudo 接入方案**：官方文档方式三用 `sudo kubectl port-forward 80:7788`；实测读 `patch_e2b.py` 源码确认 URL 为字符串拼接，`E2B_DOMAIN=localhost:8888` 带端口可用 → 高端口转发免 root、免改 /etc/hosts。
7. **SandboxSet 启动预算**：反复失败触发 `ScalingLimited: startup budget is exhausted`，之后创建被限速。避免无意义删建循环；排障先 `kubectl describe pod` / `kubectl get events --sort-by=.lastTimestamp`。

## 7. 当前环境状态（2026-09-22 收口时）

- **仍在运行（持续计费）**：预热池 `code-interpreter`（default ns，2 × 1C1Gi MicroVM + 30Gi 临时存储，均 Running 未分配）。
- 本地 port-forward 进程随会话结束失效，复测时重新拉起（Phase 2 命令）。
- 测试产物（smoke-claim、被 claim 沙箱）已清理。
- **凭证轮换待办**：本次验证中 kubeconfig 曾粘贴进对话（私钥已损坏版）、`<E2B_ADMIN_API_KEY>` 曾出现在对话——正式启用前应：① 控制台重置 kubeconfig；② 组件配置换新 adminApiKey 并为业务发普通 key。

**彻底清理（停止计费）**：

```bash
kubectl delete sandboxset code-interpreter -n default
```

## 8. 下一步：opencode 集成测试路径

1. **opencode 运行镜像注入**：预热池用轻量占位镜像，分配时原地替换（不重建 Pod）：
   ```python
   sbx = Sandbox.create(template="code-interpreter", timeout=300, metadata={
       "e2b.agents.kruise.io/image": "<OPENCODE_IMAGE>",
       "e2b.agents.kruise.io/wait-ready-timeout-seconds": "120",
   })
   ```
   自定义镜像硬性要求：含 `cp`/`mv`/`mkdir`；bash 位于 `/bin/bash`。代价：`run_code` 不可用（依赖镜像内置 code-interpreter 服务），但 opencode 只需 `commands.run` / `files.*`（本次已验证可用）。大镜像场景配 annotation `ops.alibabacloud.com/update-with-image-cache: "true"`。
2. **会话生命周期**：`timeout` 默认 300s 自动清理；长会话 `set_timeout` 续期或 metadata `e2b.agents.kruise.io/never-timeout: "true"`；会话恢复测 `beta_pause` + Sandbox CR `persistentContents: [filesystem]`（休眠保留文件系统）。
3. **规格弹性**：分配时 VPA 原地升配 CPU（metadata `e2b.agents.kruise.io/cpu-request|cpu-limit`；集群 <1.32 需开 `InPlacePodVerticalScaling` 特性门控，本次集群 v1.36 无需）。
4. **持久化**：NAS/OSS 动态挂载需 `csi` runtime 注入 + 工单解除特权容器限制（本次未测）。
5. **生产化接入**：port-forward 仅调试；生产走 ALB Ingress + 域名证书（组件 domain 已配 `<SANDBOX_DOMAIN>`，本次观察 Ingress ADDRESS 尚为 pending，需检查 ALB 实例状态），或集群内直连 `sandbox-manager.sandbox-system.svc.cluster.local`。
6. **与 engine-bridge 的关系**：本路径若选型，opencode 实例从「ECS 常驻」变为「按需 MicroVM」，会话恢复/降级语义见 [../features/f8-engine-bridge.md](../features/f8-engine-bridge.md)；harness 操作规范见 [../features/f7-opencode-agent.md](../features/f7-opencode-agent.md)。

## 附录 A：smoke.py（五检查点）

```python
"""ACS Agent Sandbox E2B 冒烟测试脚本（私有协议 + port-forward 方式）."""
import time
from kruise_agents.patch_e2b import patch_e2b

patch_e2b(https=False)  # 必须在 import e2b 之前调用

from e2b_code_interpreter import Sandbox  # noqa: E402

failures = []

def check(idx, ok, detail):
    print(f"[{idx}] {'PASS' if ok else 'FAIL'}: {detail}")
    if not ok:
        failures.append(idx)

t0 = time.time()
sbx = Sandbox.create(template="code-interpreter", timeout=300)
elapsed = time.time() - t0
check("1-create", bool(sbx.sandbox_id), f"sandbox_id={sbx.sandbox_id}, 耗时 {elapsed:.1f}s")

result = sbx.run_code("print('hello, world')")
stdout = "".join(result.logs.stdout)
check("2-run_code", "hello, world" in stdout, f"stdout={stdout!r}")

sbx.files.write("text.txt", "acs agent sandbox")
content = sbx.files.read("text.txt")
check("3-files", content == "acs agent sandbox", f"read back={content!r}")

cmd = sbx.commands.run("cat text.txt && uname -m && which bash")
check("4-commands", cmd.exit_code == 0 and "acs agent sandbox" in cmd.stdout,
      f"stdout={cmd.stdout!r} exit_code={cmd.exit_code}")

killed = sbx.kill()
check("5-kill", killed is True, f"kill returned {killed}")

print()
if failures:
    print(f"SMOKE FAILED: {failures}")
    raise SystemExit(1)
print(f"SMOKE PASSED: 5/5 checkpoints (create latency {elapsed:.1f}s)")
```

## 附录 B：SandboxSet / SandboxClaim YAML

```yaml
# sandboxset.yaml — 预热池（冒烟最小集：agent-runtime only, replicas=2）
apiVersion: agents.kruise.io/v1alpha1
kind: SandboxSet
metadata:
  name: code-interpreter
  namespace: default
spec:
  runtimes:
  - name: agent-runtime        # 注入 envd；csi 需工单开特权，冒烟不加
  replicas: 2
  template:
    metadata:
      labels:
        alibabacloud.com/acs: "true"
        alibabacloud.com/compute-class: agent-sandbox   # MicroVM 沙箱算力，关键标签
        alibabacloud.com/compute-qos: default
    spec:
      automountServiceAccountToken: false
      containers:
      - image: registry-cn-hangzhou-vpc.ack.aliyuncs.com/acs/code-interpreter:v1.6  # 地域随集群
        imagePullPolicy: IfNotPresent
        name: sandbox
        resources:
          limits: {cpu: "1", memory: 1Gi}
          requests: {cpu: "1", memory: 1Gi, ephemeral-storage: 30Gi}
      terminationGracePeriodSeconds: 30
---
# sandboxclaim.yaml — K8s 原生分配路径
apiVersion: agents.kruise.io/v1alpha1
kind: SandboxClaim
metadata:
  name: smoke-claim
  namespace: default
spec:
  templateName: code-interpreter
  replicas: 1
  claimTimeout: 5m
  ttlAfterCompleted: 15m
```

## 附录 C：官方文档

- [创建 Agent Sandbox](https://help.aliyun.com/zh/cs/user-guide/create-an-agent-sandbox)（准备工作/组件安装/预热池/域名证书/FAQ）
- [配置 Sidecar 容器自动注入](https://help.aliyun.com/zh/cs/user-guide/configure-automatic-sidecar-injection-for-agent-sandbox)（runtimes: csi / agent-runtime）
- [使用 E2B SDK 接入](https://help.aliyun.com/zh/cs/user-guide/connect-to-agent-sandbox-using-the-e2b-sdk)（三种接入方式/API 兼容性对照表）
- [Sandbox CRD 字段说明](https://help.aliyun.com/zh/cs/user-guide/sandbox-crd-field-descriptions)（phase/conditions/labels/pause/shutdown）
- OpenKruise Agents（上游开源）：[openkruise.io/kruiseagents](https://openkruise.io/zh/kruiseagents/)；kruise-agents SDK 源码 `sdk/customized_e2b`
