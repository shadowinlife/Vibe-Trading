import { afterEach, beforeEach, describe, expect, expectTypeOf, it, vi } from "vitest";
import { ApiError, api, type ScheduledRun, type StrategySchedule } from "../api";

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });

const scheduleRow: StrategySchedule = {
  job_id: "job_strategy_1",
  strategy_id: "stg_1",
  strategy_version: "v1-abcd1234",
  schedule: "0 9 * * 1-5",
  timezone: "Asia/Shanghai",
  status: "pending",
  next_run_at: 1_790_000_000_000,
  last_runs: [],
};

let fetchMock: ReturnType<typeof vi.fn>;

function lastCall(): { url: string; init: RequestInit } {
  const calls = fetchMock.mock.calls;
  const call = calls[calls.length - 1];
  if (!call) throw new Error("fetch was not called");
  return { url: call[0] as string, init: call[1] as RequestInit };
}

beforeEach(() => {
  fetchMock = vi.fn();
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("strategy library api", () => {
  it("GETs the library KPI summary", async () => {
    fetchMock.mockResolvedValue(json({
      active_count: 3,
      runs_today: 5,
      failed_today: 1,
      next_run_at: 1_790_000_000_000,
      success_rate_7d: 0.8333,
    }));

    const summary = await api.getStrategySummary();

    expect(lastCall().url).toBe("/strategies/summary");
    expect(lastCall().init.method).toBeUndefined();
    expect(summary.success_rate_7d).toBe(0.8333);
  });

  it("serializes list filters as query params", async () => {
    fetchMock.mockResolvedValue(json({ items: [], total: 0, limit: 25, offset: 50 }));

    await api.listStrategies({ kind: "code", readiness: "ready", limit: 25, offset: 50 });

    expect(lastCall().url).toBe("/strategies?kind=code&readiness=ready&limit=25&offset=50");
  });

  it("POSTs a schedule attach body and returns the job branch for a code strategy", async () => {
    fetchMock.mockResolvedValue(json({
      proposal_id: null,
      confirm_token: null,
      job_id: "job_strategy_1",
      strategy_version: "v1-abcd1234",
      requires_confirmation: false,
    }));

    const result = await api.createStrategySchedule("stg_1", {
      schedule: "0 9 * * 1-5",
      timezone: "Asia/Shanghai",
    });

    expect(lastCall().url).toBe("/strategies/stg_1/schedule");
    expect(lastCall().init.method).toBe("POST");
    expect(JSON.parse(String(lastCall().init.body))).toEqual({
      schedule: "0 9 * * 1-5",
      timezone: "Asia/Shanghai",
    });
    if (result.requires_confirmation) throw new Error("expected a code job");
    expect(result.job_id).toBe("job_strategy_1");
  });

  it("narrows a workflow attach to the proposal branch", async () => {
    fetchMock.mockResolvedValue(json({
      proposal_id: "ssp_1",
      confirm_token: "scp_once",
      job_id: null,
      strategy_version: "v1-abcd1234",
      requires_confirmation: true,
    }));

    const result = await api.createStrategySchedule("stg_wf", {
      schedule: "0 9 * * 1-5",
      timezone: "Asia/Shanghai",
      run_timeout_s: 300,
    });

    if (!result.requires_confirmation) throw new Error("expected a proposal");
    expect(result.proposal_id).toBe("ssp_1");
    expect(result.confirm_token).toBe("scp_once");
    expect(JSON.parse(String(lastCall().init.body))).toEqual({
      schedule: "0 9 * * 1-5",
      timezone: "Asia/Shanghai",
      run_timeout_s: 300,
    });
  });

  it("commits a workflow schedule with the token in the body and never in storage", async () => {
    const token = "scp_7f3c9a1b2d4e6f80";
    fetchMock.mockResolvedValue(json(scheduleRow));
    const localSet = vi.spyOn(Storage.prototype, "setItem");
    const sessionSet = vi.spyOn(window.sessionStorage, "setItem");
    const localGet = vi.spyOn(Storage.prototype, "getItem");

    const committed = await api.commitStrategySchedule("ssp_1", token);

    expect(lastCall().url).toBe("/strategy-schedule-proposals/ssp_1/commit");
    expect(lastCall().init.method).toBe("POST");
    expect(JSON.parse(String(lastCall().init.body))).toEqual({ confirm_token: token });
    expect(committed.job_id).toBe("job_strategy_1");

    // The auth header reader may read localStorage, but nothing may be written
    // and no storage call may ever see the one-time token.
    expect(localSet).not.toHaveBeenCalled();
    expect(sessionSet).not.toHaveBeenCalled();
    const storageCalls = [...localSet.mock.calls, ...localGet.mock.calls].flat().map(String);
    expect(storageCalls.some((value) => value.includes(token))).toBe(false);
  });

  it("GETs a strategy's merged run history with a bounded limit", async () => {
    fetchMock.mockResolvedValue(json([{
      run_id: "run_1",
      job_id: "job_strategy_1",
      strategy_id: "stg_1",
      strategy_version: "v1-abcd1234",
      status: "completed",
      started_at: 1_790_000_000_000,
      finished_at: 1_790_000_060_000,
      session_id: null,
      run_dir: "runs/run_1",
      summary: "2 trades",
      error: "",
      error_kind: "",
    }]));

    const runs = await api.getStrategyRuns("stg_1", 5);

    expect(lastCall().url).toBe("/strategies/stg_1/runs?limit=5");
    expect(runs[0].status).toBe("completed");
  });

  it("POSTs run-now and surfaces a 409 with the backend code", async () => {
    fetchMock.mockResolvedValue(json(scheduleRow));

    const row = await api.runStrategyScheduleNow("job_strategy_1");

    expect(lastCall().url).toBe("/strategy-schedules/job_strategy_1/run-now");
    expect(lastCall().init.method).toBe("POST");
    expect(row.job_id).toBe("job_strategy_1");

    fetchMock.mockResolvedValue(json(
      { detail: { code: "run_in_flight", message: "a run is already in flight" } },
      409,
    ));
    const failure = await api.runStrategyScheduleNow("job_strategy_1").catch((error) => error);

    expect(failure).toBeInstanceOf(ApiError);
    expect(failure).toMatchObject({ status: 409, code: "run_in_flight" });
  });
});

describe("strategy type contract on ScheduledRun", () => {
  it("widens source_type and keeps the execution fields optional", () => {
    expectTypeOf<ScheduledRun["source_type"]>().toEqualTypeOf<
      "prompt" | "playbook" | "system" | "strategy"
    >();
    expectTypeOf<ScheduledRun["source_type"]>().not.toEqualTypeOf<
      "prompt" | "playbook" | "system"
    >();
    expectTypeOf<ScheduledRun["executor_kind"]>().toEqualTypeOf<
      "prompt" | "internal" | "strategy" | undefined
    >();
    expectTypeOf<ScheduledRun["strategy_id"]>().toEqualTypeOf<string | undefined>();
    expectTypeOf<ScheduledRun["strategy_version"]>().toEqualTypeOf<string | undefined>();

    const strategyOnly: Pick<ScheduledRun, "source_type"> = { source_type: "strategy" };
    const noExecutionFields: Pick<
      ScheduledRun,
      "executor_kind" | "strategy_id" | "strategy_version"
    > = {};
    expect(strategyOnly.source_type).toBe("strategy");
    expect(noExecutionFields).toEqual({});
  });
});